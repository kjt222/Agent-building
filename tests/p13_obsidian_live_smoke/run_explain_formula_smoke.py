"""Live end-to-end: ask agent to insert formula derivation on an Obsidian note.

Optional CLI args:
  --profile <name>   Override active_profile (e.g. ``gpt-5.5``); default uses
                     whatever ``config/app.yaml`` has set.


We give the agent a deliberately under-specified prompt — no vault path, no
disk path, no instructions on how to read the PDF or render LaTeX. The
prompt only states the goal. We monitor every tool call, every diff
preview, and every approval gate so we can see the agent's full reasoning
trajectory, not just the final outcome.

Pre-flight (NOT given to the agent):
  - Locate vault from %APPDATA%/obsidian/obsidian.json (verification only)
  - Hash all candidate target files (so we can diff after)
  - Confirm a fresh backup already exists in <vault>/.agent_bak_*

Post-flight:
  - Re-hash all monitored files
  - For any changed file: print a structural verdict (JSON parses? required
    fields present? new element appears?)
  - Print full tool timeline and elapsed time
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from contextlib import closing
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parents[2]
RESULTS = ROOT / "tests" / "results" / "p13_obsidian_live_smoke"

PROMPT = (
    "我有一个 Obsidian 笔记，标题是「A Comparative Evaluation of Different "
    "Test Structures for the Extraction of Ultralow Specific Contact "
    "Resistivity A Review」。请你帮我在跟这篇论文相关的 Excalidraw 画板上"
    "插入公式 (6) 和公式 (7) 的完整推导过程 —— 每一步推导都要能在画板里"
    "看到渲染好的公式（不是只有 LaTeX 源码占位框，要真的能看到公式图像）。"
)


def _ts() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


def _free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _wait(base_url: str, timeout_s: float = 30.0) -> None:
    deadline = time.time() + timeout_s
    last = ""
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{base_url}/api/agent_runtime", timeout=3) as r:
                if 200 <= r.status < 300:
                    return
        except Exception as exc:
            last = str(exc)
        time.sleep(0.5)
    raise RuntimeError(f"server did not start: {last}")


def _find_vault() -> Path | None:
    cfg = Path(os.environ.get("APPDATA", "")) / "obsidian" / "obsidian.json"
    if not cfg.exists():
        return None
    try:
        data = json.loads(cfg.read_text(encoding="utf-8"))
    except Exception:
        return None
    for entry in (data.get("vaults") or {}).values():
        p = Path(entry.get("path") or "")
        if p.exists():
            return p
    return None


def _hash_all(root: Path, patterns: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for pattern in patterns:
        for f in root.rglob(pattern):
            if not f.is_file():
                continue
            if ".agent_bak_" in str(f):
                continue
            try:
                out[str(f)] = hashlib.md5(f.read_bytes()).hexdigest()
            except Exception:
                pass
    return out


def _parse_sse_line_events(blob: str) -> list[dict]:
    out: list[dict] = []
    event = ""
    data_lines: list[str] = []

    def flush() -> None:
        nonlocal event, data_lines
        if not data_lines:
            event = ""
            return
        payload = "\n".join(data_lines)
        try:
            obj = json.loads(payload)
        except Exception:
            obj = {"raw": payload}
        out.append({"event": event or "message", "data": obj})
        event = ""
        data_lines = []

    for line in blob.splitlines():
        if line == "":
            flush()
            continue
        if line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data_lines.append(line[5:].strip())
    flush()
    return out


_FENCE_RE = re.compile(r"```compressed-json\s*\n(.*?)\n```", re.DOTALL)


def _decode_excalidraw(text: str) -> tuple[dict | None, str | None, str]:
    """Returns (data, error, kind). kind ∈ {"compressed-json","plain-json","none"}."""
    m = _FENCE_RE.search(text)
    if m is not None:
        try:
            import lzstring  # type: ignore
        except Exception as exc:
            return None, f"lzstring import failed: {exc}", "compressed-json"
        body = re.sub(r"\s+", "", m.group(1))
        try:
            decoded = lzstring.LZString().decompressFromBase64(body)
            if not decoded:
                return None, "lz-string returned empty", "compressed-json"
            return json.loads(decoded), None, "compressed-json"
        except Exception as exc:
            return None, f"compressed-json decode: {exc}", "compressed-json"
    m_open = text.find("%%")
    m_close = text.find("%%", m_open + 2) if m_open >= 0 else -1
    if 0 <= m_open < m_close:
        block = text[m_open + 2:m_close].strip()
        try:
            return json.loads(block), None, "plain-json"
        except Exception as exc:
            return None, f"plain-json parse: {exc}", "plain-json"
    return None, "no fence and no %% block", "none"


def _summarise_file_change(path: Path) -> dict[str, Any]:
    info: dict[str, Any] = {"path": str(path), "exists": path.exists()}
    if not path.exists():
        return info
    info["size_bytes"] = path.stat().st_size
    text = path.read_text(encoding="utf-8", errors="replace")
    info["len_chars"] = len(text)

    data, err, kind = _decode_excalidraw(text)
    info["block_kind"] = kind
    info["has_excalidraw_block"] = kind != "none"
    if data is None:
        info["json_parses"] = False
        if err:
            info["json_error"] = err
        return info

    info["json_parses"] = True
    elements = data.get("elements") or []
    files = data.get("files") or {}
    info["element_count"] = len(elements)
    info["file_count"] = len(files)

    latex_imgs: list[dict[str, Any]] = []
    for el in elements:
        ls = (el.get("customData") or {}).get("latex_source")
        if not isinstance(ls, str):
            continue
        fid = el.get("fileId")
        f = files.get(fid) if isinstance(fid, str) else None
        url = (f or {}).get("dataURL") or ""
        latex_imgs.append({
            "id": el.get("id"),
            "fileId": fid,
            "latex_preview": ls[:120],
            "has_dataurl": bool(url),
            "dataurl_len": len(url),
            "dataurl_is_svg_b64": url.startswith("data:image/svg+xml;base64,"),
        })
    info["latex_image_elements"] = latex_imgs
    info["latex_image_count"] = len(latex_imgs)
    info["latex_with_svg_count"] = sum(
        1 for x in latex_imgs if x["dataurl_is_svg_b64"] and x["dataurl_len"] > 500
    )
    info["latex_missing_svg_count"] = sum(
        1 for x in latex_imgs if not (x["dataurl_is_svg_b64"] and x["dataurl_len"] > 500)
    )
    return info


def _scrub_lookalike_dirs(project_root: Path) -> list[str]:
    """Delete vault-lookalike scratch dirs left in the project root.

    Past smoke runs sometimes ended with the agent creating fallback
    directories like ``demo_vault/`` when it could not find the real vault.
    On the next run a fresh agent ``dir``-scans CWD, latches onto the
    leftover, and "completes" the task on the wrong file. The real vault
    is always external (``%APPDATA%/obsidian/obsidian.json`` etc.) — any
    vault-shaped dir directly under the project root is contamination.
    """
    import shutil
    removed: list[str] = []
    for name in ("demo_vault", "vault", "obsidian_vault", "fake_vault"):
        target = project_root / name
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
            removed.append(str(target))
    return removed


def _parse_argv(argv: list[str]) -> dict[str, str | None]:
    """Tiny ad-hoc parser: --profile <name> | -p <name>; everything else ignored."""
    out: dict[str, str | None] = {"profile": None}
    it = iter(argv)
    for tok in it:
        if tok in ("--profile", "-p"):
            out["profile"] = next(it, None)
    return out


def main() -> int:
    args = _parse_argv(sys.argv[1:])
    profile_override = args.get("profile")
    scrubbed = _scrub_lookalike_dirs(ROOT)
    if scrubbed:
        print(f"[pre-flight] scrubbed stale lookalike dirs: {scrubbed}")
    vault = _find_vault()
    if vault is None:
        print("ERR: could not locate Obsidian vault from %APPDATA%/obsidian/obsidian.json")
        return 2
    print(f"[pre-flight] vault = {vault}")

    out_dir = RESULTS / _ts()
    out_dir.mkdir(parents=True, exist_ok=True)

    monitored_patterns = ["**/*.excalidraw.md", "**/*.excalidraw"]
    before = _hash_all(vault, monitored_patterns)
    print(f"[pre-flight] hashed {len(before)} excalidraw files for change detection")
    (out_dir / "before_hashes.json").write_text(
        json.dumps(before, ensure_ascii=False, indent=2), encoding="utf-8")

    target_md = (
        vault / "文献阅读" / "SD接触" / "接触电阻测试" /
        "A Comparative Evaluation of Different Test Structures "
        "for the Extraction of Ultralow Specific Contact "
        "Resistivity A Review.md"
    )
    print(f"[pre-flight] expected target note exists: {target_md.exists()}")

    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    print(f"[server] starting uvicorn at {base_url} …")
    proc = subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn", "--factory",
            "--host", "127.0.0.1", "--port", str(port),
            "--app-dir", str(ROOT), "agent.ui.server:create_app",
            "--log-level", "warning",
        ],
        env=dict(os.environ), cwd=str(ROOT),
        stdout=(out_dir / "server_stdout.txt").open("w", encoding="utf-8", errors="replace"),
        stderr=(out_dir / "server_stderr.txt").open("w", encoding="utf-8", errors="replace"),
        text=True,
    )

    summary: dict[str, Any] = {
        "vault": str(vault),
        "target_md": str(target_md),
        "target_md_exists_before": target_md.exists(),
        "prompt": PROMPT,
    }
    try:
        _wait(base_url)
        res = requests.post(f"{base_url}/api/conversations",
                            json={"title": "P13.1 obsidian smoke"}, timeout=15)
        res.raise_for_status()
        conv_id = res.json().get("conversation_id") or res.json().get("id")
        summary["conversation_id"] = conv_id

        payload = {
            "message": PROMPT,
            "mode": "restricted",
            "plan_mode": False,
            "conversation_id": conv_id,
            "history": [],
            "max_iterations": 35,
            # No human / UI is connected — skip the 300 s human-approval wait
            # and let the model pivot immediately on denied tools.
            "unattended": True,
        }
        if profile_override:
            payload["profile"] = profile_override
            print(f"[run] profile override: {profile_override}")
        summary["profile_override"] = profile_override

        # Auto-approve diff previews and plan approvals so the agent isn't
        # stuck waiting for a UI we're not running. We still log every gate.
        approved_diffs: set[str] = set()
        approved_plans: set[str] = set()
        diff_re = re.compile(r'"preview_id"\s*:\s*"([a-zA-Z0-9_-]+)"')
        plan_re = re.compile(r'"plan_id"\s*:\s*"([a-zA-Z0-9_-]+)"')

        def _auto_approve(chunk: str) -> None:
            for m in diff_re.finditer(chunk):
                pid = m.group(1)
                if pid in approved_diffs: continue
                approved_diffs.add(pid)
                print(f"  [auto-approve diff_preview {pid[:8]}]")
                threading.Thread(target=lambda: requests.post(
                    f"{base_url}/api/diff_previews/{pid}",
                    json={"approved": True, "note": "auto-approve (P13.1 smoke)"},
                    timeout=20), daemon=True).start()
            for m in plan_re.finditer(chunk):
                pid = m.group(1)
                if pid in approved_plans: continue
                approved_plans.add(pid)
                print(f"  [auto-approve plan_preview {pid[:8]}]")
                threading.Thread(target=lambda: requests.post(
                    f"{base_url}/api/plan_approvals/{pid}",
                    json={"approved": True}, timeout=20), daemon=True).start()

        print("\n[run] streaming SSE (every tool call printed live):")
        print("-" * 76)
        raw_chunks: list[str] = []
        tool_calls: list[dict] = []
        tool_results: list[dict] = []
        last_tool_call_ts: float = 0.0
        start = time.time()
        with requests.post(f"{base_url}/api/agent_chat_v2",
                           json=payload, stream=True, timeout=900) as resp:
            resp.raise_for_status()
            buf = ""
            for chunk in resp.iter_content(chunk_size=None, decode_unicode=True):
                if not chunk:
                    continue
                if isinstance(chunk, bytes):
                    chunk = chunk.decode("utf-8", errors="replace")
                raw_chunks.append(chunk)
                _auto_approve(chunk)
                buf += chunk
                # Live print interesting activity events (don't reparse old).
                while "\n\n" in buf:
                    event_blob, _, buf = buf.partition("\n\n")
                    events = _parse_sse_line_events(event_blob + "\n\n")
                    for ev in events:
                        if ev["event"] != "activity":
                            continue
                        d = ev["data"]
                        atype = d.get("type")
                        meta = d.get("meta") or {}
                        elapsed = time.time() - start
                        if atype == "tool_call":
                            name = meta.get("name") or "?"
                            ap = meta.get("args_preview") or ""
                            print(f"  [{elapsed:6.1f}s] CALL  {name:<22} {str(ap)[:90]}")
                            tool_calls.append({"name": name, "ts": elapsed,
                                              "args_preview": ap})
                            last_tool_call_ts = elapsed
                        elif atype == "tool_result":
                            name = meta.get("name") or "?"
                            err = meta.get("is_error")
                            mark = "ERR " if err else "ok  "
                            rs = meta.get("result_preview") or ""
                            print(f"  [{elapsed:6.1f}s] {mark} {name:<22} {str(rs)[:90]}")
                            tool_results.append({"name": name, "ts": elapsed,
                                                "is_error": err,
                                                "result_preview": rs})
                        elif atype == "diff_preview":
                            print(f"  [{elapsed:6.1f}s] GATE  diff_preview tool={meta.get('tool')} op_count={meta.get('op_count')}")
                        elif atype == "plan_preview":
                            print(f"  [{elapsed:6.1f}s] GATE  plan_preview")

        elapsed_total = round(time.time() - start, 2)
        print("-" * 76)
        print(f"[run] finished in {elapsed_total}s")

        blob = "".join(raw_chunks)
        (out_dir / "raw_sse.txt").write_text(blob, encoding="utf-8")
        events = _parse_sse_line_events(blob)
        (out_dir / "events.jsonl").write_text(
            "\n".join(json.dumps(e, ensure_ascii=False) for e in events),
            encoding="utf-8")

        tokens = [e["data"].get("text", "") for e in events if e["event"] == "token"]
        assistant = "".join(tokens)
        (out_dir / "assistant.txt").write_text(assistant, encoding="utf-8")
        done = next((e["data"] for e in events if e["event"] == "done"), {})
        summary.update(
            elapsed_seconds=elapsed_total,
            tool_call_count=len(tool_calls),
            tool_calls=tool_calls,
            tool_results=tool_results,
            assistant_text_length=len(assistant),
            done=done,
            diff_previews_auto_approved=sorted(approved_diffs),
            plan_previews_auto_approved=sorted(approved_plans),
        )
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()

    print("\n[post-flight] checking file changes …")
    after = _hash_all(vault, monitored_patterns)
    (out_dir / "after_hashes.json").write_text(
        json.dumps(after, ensure_ascii=False, indent=2), encoding="utf-8")

    changed: list[str] = []
    for path, h_before in before.items():
        h_after = after.get(path)
        if h_after is None:
            changed.append(f"DELETED: {path}")
        elif h_after != h_before:
            changed.append(f"MODIFIED: {path}")
    for path, h_after in after.items():
        if path not in before:
            changed.append(f"NEW: {path}")

    print(f"[post-flight] {len(changed)} file(s) changed:")
    for c in changed:
        print(f"  {c}")

    file_verdicts = []
    for c in changed:
        kind, _, path = c.partition(": ")
        if kind == "DELETED":
            file_verdicts.append({"kind": kind, "path": path})
            continue
        verdict = _summarise_file_change(Path(path))
        verdict["change_kind"] = kind
        file_verdicts.append(verdict)
        print(f"  → {Path(path).name}: "
              f"json_parses={verdict.get('json_parses')} "
              f"elements={verdict.get('element_count')} "
              f"latex_sources={len(verdict.get('latex_sources_in_canvas') or [])}")

    target_md_verdict = _summarise_file_change(target_md)
    print(f"\n[post-flight] target note ({target_md.name[:60]}…):")
    print(f"  exists={target_md_verdict.get('exists')} "
          f"size={target_md_verdict.get('size_bytes')} "
          f"len_chars={target_md_verdict.get('len_chars')}")
    print(f"  block_kind={target_md_verdict.get('block_kind')} "
          f"json_parses={target_md_verdict.get('json_parses')} "
          f"elements={target_md_verdict.get('element_count')} "
          f"files={target_md_verdict.get('file_count')}")
    print(f"  latex_image_count={target_md_verdict.get('latex_image_count')} "
          f"with_svg={target_md_verdict.get('latex_with_svg_count')} "
          f"missing_svg={target_md_verdict.get('latex_missing_svg_count')}")

    summary["changed_files"] = changed
    summary["file_verdicts"] = file_verdicts
    summary["target_md_verdict"] = target_md_verdict

    # ---- P14.1.3 multi-axis acceptance verdict ----
    # L1 = structural (runner check)
    # L2 = excalidraw oracle (P14.2)
    # L3 = sonnet vision judge over PIL-rendered layout (P14.3)
    sys.path.insert(0, str(ROOT / "tests"))
    try:
        from _acceptance import build_verdict
    finally:
        sys.path.pop(0)
    sys.path.insert(0, str(ROOT))

    l1_pass = bool(
        target_md_verdict.get("exists")
        and target_md_verdict.get("json_parses")
        and (
            (target_md_verdict.get("latex_with_svg_count") or 0) > 0
            or (target_md_verdict.get("latex_image_count") or 0) > 0
        )
    )

    # L2: Excalidraw oracle
    l2_verdict_str: str | None = None
    oracle_report_dict: dict | None = None
    try:
        from agent.acceptance.oracles.excalidraw import ExcalidrawOracle
        oracle = ExcalidrawOracle()
        report = oracle.check([target_md])
        oracle_report_dict = report.to_dict()
        l2_verdict_str = report.verdict
        print(f"\n[L2 oracle] excalidraw verdict={report.verdict} "
              f"findings={len(report.findings)}")
        for f in report.findings[:10]:
            print(f"  - {f}")
    except Exception as exc:
        print(f"[L2 oracle] failed to run: {exc}")
        oracle_report_dict = {"error": str(exc)}

    # L3: render → vision_judge (soft-fail if no API key)
    l3_verdict_str: str | None = None
    judge_report_dict: dict | None = None
    render_meta: dict | None = None
    try:
        from agent.acceptance.renderers.excalidraw_renderer import render_excalidraw_file
        from agent.acceptance.vision_judge import judge as vision_judge
        png_path = out_dir / "rendered_excalidraw.png"
        render_meta = render_excalidraw_file(target_md, png_path)
        print(f"\n[L3 render] {render_meta}")
        if render_meta.get("rendered"):
            task_spec = {
                "user_prompt": PROMPT,
                "expected_outcome": (
                    "公式 (6) 和 (7) 的完整推导以渲染好的公式图（不是 LaTeX 源码）"
                    "出现在画板上，靠近原文公式位置；多步推导成组排列、可一起移动；"
                    "公式之间不堆叠、不越界。"
                ),
            }
            report = vision_judge(png_path, task_spec)
            judge_report_dict = report.to_dict()
            l3_verdict_str = report.verdict
            print(f"[L3 judge] verdict={report.verdict} "
                  f"confidence={report.confidence} error={report.error}")
            for f in report.findings[:10]:
                print(f"  - {f}")
            for u in report.unmet_requirements[:10]:
                print(f"  ! UNMET: {u}")
    except Exception as exc:
        print(f"[L3] failed to run: {exc}")
        judge_report_dict = {"error": str(exc)}

    verdict_obj = build_verdict(
        structural_pass=l1_pass,
        semantic_pass=l2_verdict_str,
        user_view_pass=l3_verdict_str,
        # build_verdict will parse <self_confidence> from `assistant` text
        # when model_self_confidence is left at "unknown".
        assistant_final_text=assistant,
        tool_calls=tool_calls,
        notes=[
            f"missing_svg={target_md_verdict.get('latex_missing_svg_count')}",
            f"changed_files={len(changed)}",
        ],
    )
    summary["verdict"] = verdict_obj.to_dict()
    summary["l2_oracle"] = oracle_report_dict
    summary["l3_render"] = render_meta
    summary["l3_judge"] = judge_report_dict
    v = summary["verdict"]
    print(f"\n[verdict] L1={v['L1_structural']} L2={v['L2_semantic']} "
          f"L3={v['L3_user_view']} disclosure={v['disclosure']} "
          f"ask_user_count={v['user_questions_asked']} → OVERALL={v['overall']}")

    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[done] run dir: {out_dir}")
    print(f"[done] events.jsonl + raw_sse.txt + summary.json saved")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
