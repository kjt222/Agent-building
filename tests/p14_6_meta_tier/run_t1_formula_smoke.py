"""T1 smoke: insert formula (6)(7) derivation into Obsidian Excalidraw canvas.

Key design points (per user directives 2026-05-21):

- Prompt is UNDER-specified. No flow hints. The model figures out
  format / placement / rendering strategy from tool descriptions and
  oracle feedback, NOT from prompt scaffolding.

- Capture Obsidian's actual window (PrintWindow) — what the L3 vision
  judge scores is what the user would see.

- Verdict-feedback iteration loop. After each agent end_turn:
    * compute L2 (excalidraw oracle) + L3 (vision judge on real
      Obsidian screenshot) verdicts
    * if overall == 'fail' AND attempts remain, compose an OBJECTIVE
      feedback message ("L2 reports X; L3 says Y; the canvas state
      visible to the user does/doesn't contain Z") and feed it back as
      a follow-up user turn
    * iterate until overall != 'fail' or max_iterations exhausted

  The feedback message describes WHAT was observed, not WHAT to do
  next. No tool names, no flow guidance. Per user 2026-05-21:
  "prompt 里不要提醒模型这样做，他自己失败了就自己尝试" — the model
  decides whether to switch tools, change strategy, or give up.

Run:
    .venv/Scripts/python.exe tests/p14_6_meta_tier/run_t1_formula_smoke.py [--profile <name>] [--max-iterations N]
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

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tests.p14_6_meta_tier.obsidian_window import (
    capture_window,
    find_obsidian_window,
)

ROOT = _REPO_ROOT
RESULTS = ROOT / "tests" / "results" / "p14_6_meta_tier_t1"

PROMPT = (
    "我有一个 Obsidian 笔记，标题是「A Comparative Evaluation of "
    "Different Test Structures for the Extraction of Ultralow Specific "
    "Contact Resistivity A Review」。请在跟这篇论文相关的 Excalidraw "
    "画板上加上公式 (6) 和公式 (7) 的完整推导。我会打开 Obsidian 看结果，"
    "你不能要求我关掉 Obsidian 或者手动操作。"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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
            with urllib.request.urlopen(
                f"{base_url}/api/agent_runtime", timeout=3
            ) as r:
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
            if ".agent_bak_" in str(f) or ".bak" in f.name:
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


def _parse_argv(argv: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "profile": None,
        "max_iterations": 3,
        "mirror": False,
    }
    it = iter(argv)
    for tok in it:
        if tok in ("--profile", "-p"):
            out["profile"] = next(it, None)
        elif tok in ("--max-iterations", "-n"):
            try:
                out["max_iterations"] = int(next(it, "3") or "3")
            except ValueError:
                out["max_iterations"] = 3
        elif tok == "--mirror":
            out["mirror"] = True
    return out


# ---------------------------------------------------------------------------
# Vault mirror — isolation for destructive tests
# ---------------------------------------------------------------------------


def _materialize_vault_mirror(real_vault: Path, target_md: Path) -> tuple[Path, Path]:
    """Copy enough of the real vault into tests/_vault_mirror so the
    model can read/write without touching real user data.

    Strategy: keep the target .md at the same RELATIVE path, copy any
    .pdf referenced by the canvas's element_links, and write a minimal
    .obsidian/ skeleton so the model's vault-discovery heuristics see
    this as a real Obsidian vault. Returns (mirror_root, mirror_md).

    The real vault stays untouched. Mirror is rebuilt every call so
    each run starts from a clean snapshot of the real target.
    """
    import shutil
    mirror_root = (
        Path(__file__).resolve().parents[1]
        / "_vault_mirror"
        / "sd_contact_research"
    )
    if mirror_root.exists():
        shutil.rmtree(mirror_root)
    mirror_root.mkdir(parents=True)

    # 1. .obsidian skeleton so vault-discovery finds it
    (mirror_root / ".obsidian").mkdir()
    (mirror_root / ".obsidian" / "app.json").write_text("{}", encoding="utf-8")

    # 2. Mirror the target .md at the same RELATIVE path
    rel_md = target_md.relative_to(real_vault)
    mirror_md = mirror_root / rel_md
    mirror_md.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(target_md, mirror_md)

    # 3. Copy any PDF the canvas references (element_links → .pdf filenames)
    try:
        from agent.tools_capability.obsidian.canvas_tools import read_canvas
        summary = read_canvas(mirror_md)
        pdfs_wanted: set[str] = set()
        for link in summary.element_links.values():
            tag = link.split("#", 1)[0]
            if tag.lower().endswith(".pdf"):
                pdfs_wanted.add(tag)
        for pdf_name in pdfs_wanted:
            # PDFs in Obsidian are bare filenames; search whole vault
            for src in real_vault.rglob("*.pdf"):
                # Try both the encoded (underscored) and decoded variants
                candidates = {pdf_name, pdf_name.replace("_", " ")}
                if src.name in candidates:
                    dst = mirror_root / src.relative_to(real_vault)
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dst)
                    break
    except Exception as exc:
        print(f"[mirror] warning: could not mirror PDFs: {exc}")

    return mirror_root, mirror_md


# ---------------------------------------------------------------------------
# One agent turn (stream SSE, capture everything)
# ---------------------------------------------------------------------------


def _slim_tool_input(raw: Any, *, depth: int = 0) -> Any:
    """Return a memory-bounded copy of a tool input.

    The runner stores tool_call inputs across all iterations into
    summary.json; raw inputs can carry tens of KB of SVG dataURLs per
    image element. This shrinks those long string fields while keeping
    structural info (element ids, types, coords, groupIds/frameId).
    """
    if depth > 4:
        return "<depth-limited>"
    if isinstance(raw, str):
        return raw if len(raw) <= 200 else raw[:200] + f"...<+{len(raw)-200}c>"
    if isinstance(raw, list):
        return [_slim_tool_input(x, depth=depth + 1) for x in raw[:60]]
    if isinstance(raw, dict):
        out: dict[str, Any] = {}
        for k, v in raw.items():
            if k in ("dataURL", "content") and isinstance(v, str) and len(v) > 200:
                out[k] = f"<{len(v)} chars elided>"
            else:
                out[k] = _slim_tool_input(v, depth=depth + 1)
        return out
    return raw


def _run_one_turn(
    base_url: str, conv_id: str, message: str, *,
    profile: str | None, label: str, out_dir: Path,
) -> dict[str, Any]:
    payload = {
        "message": message,
        "mode": "full-access",
        "plan_mode": False,
        "conversation_id": conv_id,
        "history": [],
        "max_iterations": 35,
        "unattended": True,
    }
    if profile:
        payload["profile"] = profile

    approved_diffs: set[str] = set()
    approved_plans: set[str] = set()
    diff_re = re.compile(r'"preview_id"\s*:\s*"([a-zA-Z0-9_-]+)"')
    plan_re = re.compile(r'"plan_id"\s*:\s*"([a-zA-Z0-9_-]+)"')

    def _auto_approve(chunk: str) -> None:
        for m in diff_re.finditer(chunk):
            pid = m.group(1)
            if pid in approved_diffs:
                continue
            approved_diffs.add(pid)
            threading.Thread(target=lambda: requests.post(
                f"{base_url}/api/diff_previews/{pid}",
                json={"approved": True, "note": "auto"},
                timeout=20), daemon=True).start()
        for m in plan_re.finditer(chunk):
            pid = m.group(1)
            if pid in approved_plans:
                continue
            approved_plans.add(pid)
            threading.Thread(target=lambda: requests.post(
                f"{base_url}/api/plan_approvals/{pid}",
                json={"approved": True}, timeout=20), daemon=True).start()

    raw_chunks: list[str] = []
    tool_calls: list[dict] = []
    tool_results: list[dict] = []
    print(f"\n[turn:{label}] streaming SSE")
    print("-" * 76)
    start = time.time()
    with requests.post(
        f"{base_url}/api/agent_chat_v2",
        json=payload, stream=True, timeout=1500,
    ) as resp:
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
            while "\n\n" in buf:
                event_blob, _, buf = buf.partition("\n\n")
                for ev in _parse_sse_line_events(event_blob + "\n\n"):
                    if ev["event"] != "activity":
                        continue
                    d = ev["data"]
                    meta = d.get("meta") or {}
                    elapsed = time.time() - start
                    atype = d.get("type")
                    if atype == "tool_call":
                        name = meta.get("name") or "?"
                        ap = str(meta.get("args_preview") or "")[:80]
                        print(f"  [{elapsed:6.1f}s] CALL  {name:<32} {ap}")
                        # Keep the full input so _compose_feedback can
                        # read element ids, write counts, etc. Strip any
                        # huge base64 dataURLs to keep memory bounded.
                        raw_input = meta.get("input") or {}
                        slim_input = _slim_tool_input(raw_input)
                        tool_calls.append({"name": name, "ts": elapsed,
                                          "args_preview": ap,
                                          "input": slim_input})
                    elif atype == "tool_result":
                        name = meta.get("name") or "?"
                        err = meta.get("is_error")
                        mark = "ERR " if err else "ok  "
                        rs = str(meta.get("result_preview") or "")[:80]
                        print(f"  [{elapsed:6.1f}s] {mark} {name:<32} {rs}")
                        tool_results.append({"name": name, "ts": elapsed,
                                            "is_error": err,
                                            "result_preview": rs})

    elapsed_total = round(time.time() - start, 2)
    print("-" * 76)
    print(f"[turn:{label}] finished in {elapsed_total}s")

    blob = "".join(raw_chunks)
    (out_dir / f"raw_sse_{label}.txt").write_text(blob, encoding="utf-8")
    events = _parse_sse_line_events(blob)
    (out_dir / f"events_{label}.jsonl").write_text(
        "\n".join(json.dumps(e, ensure_ascii=False) for e in events),
        encoding="utf-8")

    tokens = [e["data"].get("text", "") for e in events if e["event"] == "token"]
    assistant = "".join(tokens)
    (out_dir / f"assistant_{label}.txt").write_text(assistant, encoding="utf-8")

    return {
        "elapsed_seconds": elapsed_total,
        "tool_calls": tool_calls,
        "tool_results": tool_results,
        "assistant_text": assistant,
        "diff_previews_auto_approved": sorted(approved_diffs),
        "plan_previews_auto_approved": sorted(approved_plans),
    }


# ---------------------------------------------------------------------------
# Verdict computation (single iteration)
# ---------------------------------------------------------------------------


def _snapshot_baseline(target_md: Path | None) -> dict:
    """Capture pre-run state we'll diff against: element ids + L2 finding
    strings. This lets _compute_verdict report "what the model ADDED /
    BROKE this run" rather than penalizing the model for pollution that
    was already in the canvas (left over from prior test runs)."""
    if target_md is None or not target_md.exists():
        return {"element_ids": set(), "l2_findings": set()}
    try:
        from agent.tools_capability.obsidian.canvas_tools import read_canvas
        summary_obj = read_canvas(target_md, include_elements=True)
        ids = {str(e.get("id")) for e in summary_obj.elements if e.get("id")}
    except Exception:
        ids = set()
    findings: set[str] = set()
    try:
        from agent.acceptance.oracles.excalidraw import ExcalidrawOracle
        r = ExcalidrawOracle().check([target_md])
        findings = {str(f) for f in r.findings}
    except Exception:
        pass
    return {"element_ids": ids, "l2_findings": findings}


def _evaluate_task_completion(target_md: Path | None,
                              baseline: dict) -> dict[str, Any]:
    """Inspect the post-run canvas vs baseline. Returns:
      - new_element_ids: ids that didn't exist at baseline
      - new_latex_elements: of those, the ones with customData.latex_source
      - touches_6_or_7: do any new latex elements mention '(6)' or '(7)'?
      - new_elements_grouped: do all new latex elements share a groupId
        or frameId? (single-element OK)
    """
    out: dict[str, Any] = {
        "new_element_ids": [],
        "new_latex_elements": 0,
        "touches_6": False,
        "touches_7": False,
        "new_elements_grouped": False,
    }
    if target_md is None or not target_md.exists():
        return out
    try:
        from agent.tools_capability.obsidian.canvas_tools import read_canvas
        s = read_canvas(target_md, include_elements=True)
    except Exception:
        return out
    base_ids = baseline.get("element_ids") or set()
    new_els = [e for e in s.elements
               if str(e.get("id")) not in base_ids and not e.get("isDeleted")]
    out["new_element_ids"] = [str(e.get("id")) for e in new_els]
    latex_new = [
        e for e in new_els
        if isinstance((e.get("customData") or {}).get("latex_source"), str)
        and (e.get("customData") or {}).get("latex_source").strip()
    ]
    out["new_latex_elements"] = len(latex_new)
    for e in latex_new:
        src = (e.get("customData") or {}).get("latex_source") or ""
        if "(6)" in src:
            out["touches_6"] = True
        if "(7)" in src:
            out["touches_7"] = True
    # Grouping: all new latex elements share at least one groupId, OR
    # share the same frameId. Single element trivially "grouped".
    if len(latex_new) <= 1:
        out["new_elements_grouped"] = True
    else:
        common: set[str] | None = None
        for e in latex_new:
            gids = set(e.get("groupIds") or [])
            common = gids if common is None else (common & gids)
        if common:
            out["new_elements_grouped"] = True
        else:
            frames = {e.get("frameId") for e in latex_new}
            out["new_elements_grouped"] = len(frames) == 1 and None not in frames
    return out


def _compute_verdict(
    *, target_md: Path | None, hwnd: int | None, out_dir: Path,
    label: str, prompt: str, assistant: str, tool_calls: list[dict],
    changed_files: list[str], baseline: dict | None = None,
) -> dict[str, Any]:
    """Compute L1/L2/L3 + overall verdict. Returns a dict mirroring
    AcceptanceVerdict.to_dict() plus l2_report / l3_report blobs.

    P14.6.16-F: when ``baseline`` is provided, L2 is judged on NEW
    findings only (introduced this run) and a task-completion check
    confirms the model added (6)/(7) latex elements properly grouped.
    """
    baseline = baseline or {"element_ids": set(), "l2_findings": set()}
    # ---- L1 ----
    target_changed = target_md is not None and str(target_md) in changed_files
    l1_pass = False
    canvas_summary = None
    if target_md and target_md.exists():
        try:
            from agent.tools_capability.obsidian.canvas_tools import read_canvas
            summary_obj = read_canvas(target_md, include_elements=False)
            canvas_summary = summary_obj.to_dict()
            l1_pass = target_changed and summary_obj.element_count > 0
            print(f"[{label}:L1] target_changed={target_changed} "
                  f"elements={summary_obj.element_count} "
                  f"→ L1={'pass' if l1_pass else 'fail'}")
        except Exception as exc:
            print(f"[{label}:L1] read failed: {exc}")

    # ---- L2 ---- raw + baseline-relative
    l2_verdict: str | None = None
    l2_report: dict | None = None
    l2_new_findings: list[str] = []
    try:
        from agent.acceptance.oracles.excalidraw import ExcalidrawOracle
        if target_md:
            r = ExcalidrawOracle().check([target_md])
            l2_report = r.to_dict()
            base_findings = baseline.get("l2_findings") or set()
            base_ids = baseline.get("element_ids") or set()
            import re as _re
            def _is_pure_baseline_finding(f: str) -> bool:
                # Catch the orphan-grouping-style finding that mentions
                # specific element ids — if EVERY id mentioned in the
                # finding existed before the run, it's pre-existing
                # pollution (often surfaced because the oracle wording
                # changed between runs), not a regression the model
                # introduced this turn.
                ids_in = _re.findall(r"'(img_[A-Za-z0-9_]+|n_[A-Za-z0-9_]+|frame_[A-Za-z0-9_]+|lf_[A-Za-z0-9_]+|[A-Za-z0-9]{6,32})'", f)
                if not ids_in:
                    return False
                return all(i in base_ids for i in ids_in)
            l2_new_findings = [
                f for f in r.findings
                if f not in base_findings and not _is_pure_baseline_finding(f)
            ]
            # Verdict: pass if NO new fail-grade findings introduced.
            # (raw oracle may still say fail because of baseline pollution.)
            if r.verdict == "pass":
                l2_verdict = "pass"
            elif not l2_new_findings:
                l2_verdict = "pass"
            else:
                l2_verdict = r.verdict
            print(f"[{label}:L2] raw={r.verdict} findings={len(r.findings)} "
                  f"new={len(l2_new_findings)} → L2={l2_verdict}")
    except Exception as exc:
        print(f"[{label}:L2] failed: {exc}")

    # ---- Task completion check ----
    tc = _evaluate_task_completion(target_md, baseline)
    print(f"[{label}:task] new_elements={len(tc['new_element_ids'])} "
          f"new_latex={tc['new_latex_elements']} "
          f"touches=(6:{tc['touches_6']},7:{tc['touches_7']}) "
          f"grouped={tc['new_elements_grouped']}")

    # ---- Capture Obsidian + L3 ----
    captured = False
    if hwnd is not None:
        captured = capture_window(hwnd, out_dir / f"obsidian_after_{label}.png")
        print(f"[{label}] obsidian-after captured: {captured}")

    l3_verdict: str | None = None
    l3_report: dict | None = None
    if captured:
        try:
            from agent.acceptance.vision_judge import judge as vision_judge
            task_spec = {
                "user_prompt": prompt,
                "expected_outcome": (
                    "公式 (6) 和 (7) 的完整推导出现在 Obsidian Excalidraw "
                    "画板里，能看到渲染好的公式，靠近原文公式所在 PDF 页面附近，"
                    "多步推导相邻成组。"
                ),
            }
            r = vision_judge(out_dir / f"obsidian_after_{label}.png", task_spec)
            l3_verdict = r.verdict
            l3_report = r.to_dict()
            print(f"[{label}:L3] verdict={l3_verdict} confidence={r.confidence}")
            for f in (r.findings or [])[:6]:
                print(f"  - {f}")
        except Exception as exc:
            print(f"[{label}:L3] failed: {exc}")

    # ---- 5-axis composite ----
    sys.path.insert(0, str(ROOT / "tests"))
    try:
        from _acceptance import build_verdict
    finally:
        sys.path.pop(0)
    v = build_verdict(
        structural_pass=l1_pass,
        semantic_pass=l2_verdict,
        user_view_pass=l3_verdict,
        assistant_final_text=assistant,
        tool_calls=tool_calls,
    )
    v_dict = v.to_dict()
    v_dict["l2_report"] = l2_report
    v_dict["l2_new_findings"] = l2_new_findings
    v_dict["l3_report"] = l3_report
    v_dict["canvas_summary"] = canvas_summary
    v_dict["captured_screenshot"] = captured
    v_dict["task_completion"] = tc
    # Task-completion gate: L3 is unknown in mirror runs (no Obsidian),
    # so without this the overall verdict can never reach pass. Promote
    # to pass when:
    #   L1 pass + L2 no new fails + new latex elements + grouped, AND
    #   EITHER  (a) literal "(6)" and "(7)" labels appear in latex_source,
    #   OR      (b) >=5 new latex elements (substantive derivation that
    #              dropped the literal label — empirically common).
    has_labels = tc["touches_6"] and tc["touches_7"]
    enough_derivation = tc["new_latex_elements"] >= 5
    task_ok = (
        l1_pass
        and l2_verdict == "pass"
        and tc["new_latex_elements"] >= 1
        and (has_labels or enough_derivation)
        and tc["new_elements_grouped"]
    )
    if task_ok and v.overall != "pass":
        v_dict["overall"] = "pass"
        v_dict["overall_reason"] = (
            "task-completion gate: model added (6)+(7) latex, grouped, "
            "no new L2 regressions; L3 unknown in mirror mode is OK."
        )
    print(f"[{label}:verdict] L1={v.L1_structural} L2={v.L2_semantic} "
          f"L3={v.L3_user_view} disclosure={v.disclosure} "
          f"ask_user={v.user_questions_asked} self={v.model_self_confidence} "
          f"task_ok={task_ok} → OVERALL={v_dict['overall']}")
    return v_dict


# ---------------------------------------------------------------------------
# Feedback composer (OBJECTIVE only — no flow guidance)
# ---------------------------------------------------------------------------


def _summarize_write_calls(tool_calls: list[dict]) -> dict:
    """Extract write-side facts from this iteration's tool calls.

    P14.6.16: there are no obsidian_* capability tools — every canvas
    mutation comes from meta tools (Write / Edit / Bash). We just count
    those so the feedback can report "you made N mutations" without
    naming tools that don't exist.
    """
    write_calls = 0
    bash_calls = 0
    for tc in tool_calls or []:
        name = (tc.get("name") or "").split()[0]
        if name in ("Write", "Edit"):
            write_calls += 1
        elif name == "Bash":
            bash_calls += 1
    return {
        "write_or_edit": write_calls,
        "bash": bash_calls,
    }


def _compose_feedback(
    verdict: dict,
    *,
    tool_calls: list[dict] | None = None,
    canvas_path: Path | None = None,
) -> str:
    """Build a follow-up user message describing what happened, NOT what
    to do next. The model decides strategy from facts.

    Three independent fact-categories are reported separately so the
    model does not conflate them (P14.6.11 root-cause analysis):
      - WRITE FACTS: what the model's tool calls actually changed on disk
      - VIEWPORT FACTS: where the canvas will open by default
      - VIEW FACTS: what the screenshot+vision_judge saw vs not
    """
    parts: list[str] = []
    parts.append("我打开了 Obsidian 看你的修改，目前还没达到预期。客观结果如下：")

    # --- WRITE FACTS ---
    write = _summarize_write_calls(tool_calls or [])
    parts.append(
        f"\n[写入事实] 本轮通过元能力对画板做了 "
        f"{write['write_or_edit']} 次 Write/Edit + {write['bash']} 次 Bash。"
        " 没有 obsidian 专用工具——所有 lz-string round-trip、schema 校验、"
        "viewport focus 都得你的脚本自己处理。"
    )

    # --- CANVAS STATE (post-write, on-disk) ---
    cs = verdict.get("canvas_summary") or {}
    if cs:
        parts.append(
            f"\n[画布当前状态] elements={cs.get('element_count')}, "
            f"types={cs.get('type_breakdown')}, "
            f"bbox={cs.get('bbox')}"
        )
        # Read appState viewport so model knows where Obsidian will land.
        if canvas_path:
            try:
                from agent.tools_capability.obsidian.excalidraw_io import (
                    read_canvas_file,
                )
                data, _ = read_canvas_file(
                    canvas_path.read_text(encoding="utf-8")
                )
                app = data.get("appState") or {}
                zoom = app.get("zoom")
                zv = zoom.get("value") if isinstance(zoom, dict) else zoom
                parts.append(
                    f"  appState viewport: scrollX={app.get('scrollX')}, "
                    f"scrollY={app.get('scrollY')}, zoom={zv}"
                )
            except Exception:
                pass

    # --- L2 STRUCTURAL ---
    l2 = verdict.get("l2_report") or {}
    l2_findings = l2.get("findings") or []
    l2_new = verdict.get("l2_new_findings") or []
    if l2_findings:
        baseline_count = max(0, len(l2_findings) - len(l2_new))
        parts.append(
            f"\n[L2 结构性 oracle ({verdict.get('L2_semantic')})] "
            f"共 {len(l2_findings)} 项 finding（其中 {baseline_count} 项是 "
            f"baseline 残留，{len(l2_new)} 项是本轮新引入的）。"
        )
        if l2_new:
            parts.append("  本轮新引入的 finding（这些要修；baseline 的不用管）：")
            for f in l2_new[:5]:
                parts.append(f"  - {f}")
        else:
            parts.append("  本轮没引入新 finding（只剩 baseline 残留）。")

    # --- 任务完成度 ---
    tc = verdict.get("task_completion") or {}
    if tc:
        parts.append(
            f"\n[任务完成度] 本轮新增 {len(tc.get('new_element_ids') or [])} "
            f"个 element，其中带 latex_source 的 {tc.get('new_latex_elements', 0)} 个。"
            f" 公式 (6) 命中={tc.get('touches_6')}，(7) 命中={tc.get('touches_7')}，"
            f"新增 latex 是否成组={tc.get('new_elements_grouped')}。"
        )
        if not (tc.get('touches_6') and tc.get('touches_7')):
            parts.append(
                "  ⚠️ 任务核心要求是『加入公式 (6) 和 (7) 的完整推导』。"
                "新增 latex_source 字符串里要包含 '(6)' 和 '(7)' 这两个标号，"
                "verdict 才会判 pass。"
            )

    # --- L3 VIEW FACTS ---
    l3 = verdict.get("l3_report") or {}
    if l3:
        parts.append(
            f"\n[L3 视觉验收 ({verdict.get('L3_user_view')}, "
            f"confidence={l3.get('confidence')})]"
        )
        for f in (l3.get("findings") or [])[:5]:
            parts.append(f"  - {f}")
        for u in (l3.get("unmet_requirements") or [])[:5]:
            parts.append(f"  ! 未满足: {u}")
        parts.append(
            "  提醒：L3 报告反映的是「截图能看到什么」。如果"
            "[写入事实] 显示文件已成功改动，但 L3 说看不到，"
            "那是 viewport 没对准（典型情况：你写了但 appState 没指向"
            "新元素 bbox），不代表写入失败、也不要再写一份重复内容。"
        )

    # Note: previous "重复风险 / 原位修改上一轮新建的元素" warning was
    # removed (P14.6.16-J). In mirror mode the canvas is RE-MATERIALIZED
    # each run, so "上一轮新建" doesn't refer to this run's writes —
    # mostly to other test runs' debris. The hint pushed the model to
    # replace baseline orphans in place instead of adding the requested
    # (6)/(7) latex. The [任务完成度] block above already tells the
    # model exactly what's missing.

    parts.append(
        "\n以上是观察事实。原始任务不变。"
    )
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    args = _parse_argv(sys.argv[1:])
    profile_override = args.get("profile")
    max_iter = int(args.get("max_iterations") or 3)
    use_mirror = bool(args.get("mirror"))

    real_vault = _find_vault()
    if real_vault is None:
        print("ERR: could not locate Obsidian vault")
        return 2
    print(f"[pre-flight] real vault = {real_vault}")

    out_dir = RESULTS / _ts()
    out_dir.mkdir(parents=True, exist_ok=True)

    # Strict canonical-name match: the legitimate canvas filename ends
    # with "A Review.md" with nothing between. Past test runs left
    # *.final_fix_backup.*.md, *.bak_p14_6_*.md, *.restorebackup.*.md
    # alongside it; "in p.name" filters were too loose (backup substrings
    # without leading dot slipped through, e.g. "fix_backup").
    real_canvases = [
        p for p in real_vault.rglob("A Comparative Evaluation*A Review.md")
        if (
            p.name.endswith("A Review.md")
            and ".agent_bak_" not in str(p)
            and ".bak_" not in p.name
            and ".backup" not in p.name
            and ".restorebackup" not in p.name
            and ".final_fix_backup" not in p.name
        )
    ]
    if len(real_canvases) > 1:
        # Multiple candidates → prefer the shortest path (canonical lives
        # at the canonical folder; backups under .agent_bak_/ subdirs are
        # always deeper).
        real_canvases.sort(key=lambda p: len(str(p)))
    real_target = real_canvases[0] if real_canvases else None
    if real_target:
        print(f"[pre-flight] picked canonical canvas: {real_target}")
    if len(real_canvases) > 1:
        print(f"[pre-flight] (also seen but skipped: {[str(p) for p in real_canvases[1:5]]})")
    if real_target is None:
        print("ERR: could not locate target canvas in real vault")
        return 2

    if use_mirror:
        vault, target_md = _materialize_vault_mirror(real_vault, real_target)
        print(f"[mirror] vault = {vault}")
        print(f"[mirror] target = {target_md} ({target_md.stat().st_size} bytes)")
        print("[mirror] real vault will NOT be touched by this run (mirror layer)")
    else:
        vault, target_md = real_vault, real_target
    print(f"[pre-flight] target canvas: {target_md}")

    # Safety net: snapshot the REAL target canvas so we can detect &
    # restore any out-of-band mutation. Mirror mode is supposed to
    # divert all writes; in practice, models discover the real vault
    # via obsidian.json + write directly through `python -c` Bash. This
    # snapshot/rollback runs regardless of --mirror so the real canvas
    # is restored to pre-run state no matter what the model did.
    import hashlib
    snapshot_path = real_target.with_suffix(
        real_target.suffix + f".bak_runner_snapshot_{int(time.time())}"
    )
    snapshot_bytes = real_target.read_bytes()
    snapshot_path.write_bytes(snapshot_bytes)
    snapshot_hash = hashlib.md5(snapshot_bytes).hexdigest()
    print(f"[safety] real canvas snapshot → {snapshot_path.name} "
          f"({len(snapshot_bytes)} bytes, md5={snapshot_hash[:12]}...)")

    monitored = ["**/*.excalidraw.md", "**/A Comparative Evaluation*.md"]
    before_hashes = _hash_all(vault, monitored)

    # P14.6.16-F: pre-run snapshot of element ids + L2 findings, so the
    # verdict can ignore baseline pollution and only judge what the model
    # changed this run.
    pre_baseline = _snapshot_baseline(target_md)
    print(f"[baseline] elements={len(pre_baseline['element_ids'])} "
          f"l2_findings={len(pre_baseline['l2_findings'])}")

    title_hint = "A Comparative Evaluation" if target_md else "Obsidian"
    # In mirror mode Obsidian is not running on the mirror canvas, so
    # don't try to screenshot — L3 will report unknown (which is honest).
    hwnd = None if use_mirror else find_obsidian_window(title_substring=title_hint)
    print(f"[pre-flight] Obsidian hwnd: {hwnd} (mirror_mode={use_mirror})")
    if hwnd is not None:
        capture_window(hwnd, out_dir / "obsidian_baseline.png")

    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    print(f"[server] starting uvicorn at {base_url}")
    # Pass OBSIDIAN_MIRROR_ROOT into the uvicorn subprocess: obsidian_*
    # tools' path guard reads this to reject any canvas_path that
    # escapes the mirror. Without it, models that ignore the prompt's
    # "stay in mirror" instruction can still write through to real vault.
    server_env = dict(os.environ)
    if use_mirror:
        server_env["OBSIDIAN_MIRROR_ROOT"] = str(vault)
    else:
        server_env.pop("OBSIDIAN_MIRROR_ROOT", None)
    # Smoke runs have no human approver; tell the UI server to
    # auto-decline AskUserQuestion immediately instead of waiting 600s
    # of dead air per iteration (P13.1.2 fix).
    server_env["SMOKE_NO_APPROVER"] = "1"
    proc = subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn", "--factory",
            "--host", "127.0.0.1", "--port", str(port),
            "--app-dir", str(ROOT), "agent.ui.server:create_app",
            "--log-level", "warning",
        ],
        env=server_env, cwd=str(ROOT),
        stdout=(out_dir / "server_stdout.txt").open(
            "w", encoding="utf-8", errors="replace"),
        stderr=(out_dir / "server_stderr.txt").open(
            "w", encoding="utf-8", errors="replace"),
        text=True,
    )

    summary: dict[str, Any] = {
        "vault": str(vault),
        "target_md": str(target_md) if target_md else None,
        "prompt": PROMPT,
        "profile": profile_override,
        "max_iterations": max_iter,
        "iterations": [],
    }

    try:
        _wait(base_url)
        res = requests.post(
            f"{base_url}/api/conversations",
            json={"title": "P14.6 T1 formula smoke"}, timeout=15,
        )
        res.raise_for_status()
        conv_id = res.json().get("conversation_id") or res.json().get("id")
        summary["conversation_id"] = conv_id

        message = PROMPT
        if use_mirror:
            # Mirror mode hard constraint (P14.6.15). Past V4/GLM runs
            # ignored softly-worded mirror hints, globbed `D:\` to find
            # the real vault, then wrote through. The path guard on
            # obsidian_* tools now rejects out-of-mirror paths, but a
            # blunt prompt up-front saves the model wasted turns.
            message = (
                PROMPT
                + "\n\n=== 测试沙箱硬约束（不是建议）==="
                f"\n你的 vault 根目录 = {vault}"
                f"\n目标 .md 文件 = {target_md}"
                "\n下列动作会被拒绝/失败，请不要尝试："
                f"\n  • 任何不在 {vault} 之下的文件路径"
                "\n  • glob/walk `D:\\` `C:\\` 或其他磁盘根去找『真』vault"
                "\n  • 读 obsidian.json 或类似配置反查真 vault 位置"
                "\n  • 调用 Obsidian Local REST API（127.0.0.1:27124 / 27123）"
                "\n  • 镜像里没有运行 Obsidian，任何 refresh / live-reload 路径都跳过"
                "\n你看到的 vault 就是 mirror。没有『另一个真 vault』。所有"
                "需要的 PDF/embedded 文件镜像里都有副本，直接通过 element_links"
                "解出的相对路径访问即可。"
                "\n\n=== 工具能力提示 ==="
                "\n本次会话**没有** obsidian_* 专用工具（read_canvas / write_elements /"
                " refresh_note / find_pdf_text_anchor 都不存在）。所有 Excalidraw 操作"
                "**靠元能力**：Read / Write / Bash / Glob / Grep。具体配方请先调"
                " `show_relevant_tools` 查到 `__skill__obsidian-excalidraw` 指针，"
                "然后 Read `skills/obsidian-excalidraw/SKILL.md` —— 里面有完整的"
                "lz-string 解码/编码 + LaTeX→SVG + pdfplumber 锚点 + viewport focus"
                "+ container strategy 的可跑代码。"
                "\n================================="
            )
        prev_hashes = before_hashes
        final_verdict: dict[str, Any] | None = None

        for iteration in range(max_iter):
            label = f"iter{iteration + 1}"
            print(f"\n========== Iteration {iteration + 1}/{max_iter} ==========")
            turn_result = _run_one_turn(
                base_url, conv_id, message,
                profile=profile_override, label=label, out_dir=out_dir,
            )

            after_hashes = _hash_all(vault, monitored)
            changed = [
                p for p, h in prev_hashes.items() if after_hashes.get(p) != h
            ] + [p for p in after_hashes if p not in prev_hashes]

            verdict = _compute_verdict(
                target_md=target_md, hwnd=hwnd, out_dir=out_dir,
                label=label, prompt=PROMPT,
                assistant=turn_result["assistant_text"],
                tool_calls=turn_result["tool_calls"],
                changed_files=changed,
                baseline=pre_baseline,
            )
            summary["iterations"].append({
                "label": label,
                "elapsed_seconds": turn_result["elapsed_seconds"],
                "tool_calls": turn_result["tool_calls"],
                "tool_results": turn_result["tool_results"],
                "changed_files": changed,
                "verdict": verdict,
                "feedback_message_sent": message if iteration == 0 else "(feedback)",
            })

            prev_hashes = after_hashes
            final_verdict = verdict

            overall = verdict.get("overall")
            print(f"\n[iteration {iteration + 1}] overall = {overall}")
            if overall == "pass":
                print(f"[iteration {iteration + 1}] PASS — stop iterating")
                break
            if overall in ("partial", "needs_review"):
                print(f"[iteration {iteration + 1}] {overall} — stop iterating")
                break
            if iteration + 1 >= max_iter:
                print(f"[iteration {iteration + 1}] fail — max iterations reached")
                break

            message = _compose_feedback(
                verdict,
                tool_calls=turn_result["tool_calls"],
                canvas_path=target_md,
            )
            (out_dir / f"feedback_{label}.txt").write_text(message, encoding="utf-8")
            print(f"\n[iteration {iteration + 1}] composing feedback "
                  f"({len(message)} chars) for next turn …")

        summary["final_verdict"] = final_verdict
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        # Safety-net rollback: if the real canvas was mutated despite
        # mirror mode, restore it from the snapshot. The snapshot file
        # stays on disk for forensics.
        try:
            current_bytes = real_target.read_bytes()
            current_hash = hashlib.md5(current_bytes).hexdigest()
            if current_hash != snapshot_hash:
                real_target.write_bytes(snapshot_bytes)
                print(
                    f"[safety] real canvas was modified during run "
                    f"({len(current_bytes)} bytes, md5={current_hash[:12]}...) "
                    f"→ restored from snapshot ({len(snapshot_bytes)} bytes). "
                    f"Mutated copy preserved at {snapshot_path.name} for forensics."
                )
                # Save the mutated post-run state under a separate name
                # so it isn't lost when we restore.
                forensic_path = real_target.with_suffix(
                    real_target.suffix + f".bak_runner_postrun_{int(time.time())}"
                )
                forensic_path.write_bytes(current_bytes)
                print(f"[safety] mutated post-run copy → {forensic_path.name}")
            else:
                print(f"[safety] real canvas unchanged (md5={snapshot_hash[:12]}...) — no rollback needed")
                # Snapshot served its purpose; remove to keep dir tidy
                try: snapshot_path.unlink()
                except Exception: pass
        except Exception as exc:
            print(f"[safety] WARNING: rollback check failed: {exc}")

    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[done] {out_dir}")
    if final_verdict:
        print(f"[done] final overall = {final_verdict.get('overall')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
