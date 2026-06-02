"""Run T1 sequentially across multiple model profiles, summarize.

The two model runs cannot overlap — both target the same Obsidian
canvas. Sequential execution: profile A runs to completion, the canvas
is left in its post-A state, then profile B runs (will see A's
changes). Each run is a separate output dir + summary.json.

For a fair comparison the user should optionally reset the canvas
between runs (see --restore-baseline flag).

Run:
    .venv/Scripts/python.exe tests/p14_6_meta_tier/run_t1_multimodel.py \
        [--profiles doubao-code,gpt-5.5] [--max-iterations 3] [--restore-baseline]
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

RESULTS_ROOT = _REPO_ROOT / "tests" / "results" / "p14_6_meta_tier_t1_multimodel"


def _restore_canvas_baseline() -> bool:
    """Restore the .excalidraw.md target from its baseline backup (made
    earlier during P14.6 probing). No-op if the backup is missing."""
    vault = Path(r"D:\D\scientific research vault")
    target = next(
        (p for p in vault.rglob("A Comparative Evaluation*.md")
         if ".agent_bak_" not in str(p) and ".bak" not in p.name
         and ".backup" not in p.name),
        None,
    )
    if target is None:
        return False
    bak = target.with_suffix(target.suffix + ".bak_p14_6_probe_baseline")
    if not bak.exists():
        return False
    shutil.copy2(bak, target)
    print(f"[restore] {target.name} ← {bak.name} ({target.stat().st_size} bytes)")
    return True


def _trigger_obsidian_refresh() -> None:
    """Best-effort: ask Obsidian to re-read the target file so the
    canvas window shows the restored state before the next profile
    starts. Failures (REST API not up, key missing, …) are silent."""
    try:
        from urllib.parse import quote
        import ssl
        import urllib.request
        import keyring

        from agent.credentials import SERVICE_NAME
        from agent.tools_capability.obsidian.rest_client import (
            keyring_ref_for_vault,
        )

        vault = Path(r"D:\D\scientific research vault")
        ref = keyring_ref_for_vault(str(vault))
        key = keyring.get_password(SERVICE_NAME, ref)
        if not key:
            return
        target = next(
            p for p in vault.rglob("A Comparative Evaluation*.md")
            if ".agent_bak_" not in str(p) and ".bak" not in p.name
            and ".backup" not in p.name
        )
        rel = target.relative_to(vault).as_posix()
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        def post(path):
            req = urllib.request.Request(
                f"https://127.0.0.1:27124{path}", method="POST",
                headers={"Authorization": "Bearer " + key, "Content-Length": "0"},
            )
            urllib.request.urlopen(req, timeout=10, context=ctx)

        post(f"/open/{quote(rel)}")
        time.sleep(0.5)
        post("/commands/workspace:close/")
        time.sleep(0.8)
        post(f"/open/{quote(rel)}")
        time.sleep(2.0)
        print("[restore] Obsidian re-loaded restored canvas")
    except Exception as exc:
        print(f"[restore] Obsidian refresh skipped: {exc}")


def main(argv: list[str] | None = None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    profiles = ["doubao-code", "gpt-5.5"]
    max_iter = 3
    restore = False
    it = iter(argv)
    for tok in it:
        if tok == "--profiles":
            v = next(it, "")
            profiles = [p.strip() for p in (v or "").split(",") if p.strip()]
        elif tok in ("--max-iterations", "-n"):
            max_iter = int(next(it, "3") or "3")
        elif tok == "--restore-baseline":
            restore = True

    stamp = time.strftime("%Y%m%d_%H%M%S")
    out = RESULTS_ROOT / stamp
    out.mkdir(parents=True, exist_ok=True)
    print(f"[multi] profiles: {profiles}  max_iter: {max_iter}  "
          f"restore: {restore}")
    print(f"[multi] output: {out}")

    results: list[dict] = []
    runner = _REPO_ROOT / "tests" / "p14_6_meta_tier" / "run_t1_formula_smoke.py"

    for profile in profiles:
        if restore:
            print(f"\n========== restoring canvas before {profile} ==========")
            if _restore_canvas_baseline():
                _trigger_obsidian_refresh()
        print(f"\n========== profile: {profile} ==========")
        log_path = out / f"{profile}.stdout.log"
        start = time.time()
        rc = subprocess.run(
            [sys.executable, str(runner),
             "--profile", profile, "--max-iterations", str(max_iter)],
            stdout=open(log_path, "w", encoding="utf-8", errors="replace"),
            stderr=subprocess.STDOUT,
            cwd=str(_REPO_ROOT),
        ).returncode
        elapsed = round(time.time() - start, 1)
        # The per-profile runner writes its own per-run summary.json under
        # tests/results/p14_6_meta_tier_t1/<ts>/. Find the latest dir
        # newer than `start` and pull its summary.
        runner_results = _REPO_ROOT / "tests" / "results" / "p14_6_meta_tier_t1"
        latest_summary = None
        if runner_results.exists():
            dirs = sorted(
                [d for d in runner_results.iterdir() if d.is_dir()],
                key=lambda p: p.stat().st_mtime, reverse=True,
            )
            for d in dirs:
                if d.stat().st_mtime >= start:
                    sj = d / "summary.json"
                    if sj.exists():
                        try:
                            latest_summary = json.loads(sj.read_text(encoding="utf-8"))
                            latest_summary["run_dir"] = str(d)
                        except Exception:
                            pass
                    break
        results.append({
            "profile": profile,
            "exit_code": rc,
            "elapsed_seconds": elapsed,
            "log": str(log_path),
            "summary": latest_summary,
        })
        print(f"[{profile}] exit={rc}  wall={elapsed}s  log={log_path.name}")

    # ---- compact comparison table ----
    print(f"\n{'=' * 76}\n  P14.6 T1 multi-model results\n{'=' * 76}")
    print(f"\n{'PROFILE':<18}{'EXIT':<6}{'WALL(s)':<10}{'OVERALL':<14}"
          f"{'L1':<8}{'L2':<8}{'L3':<8}{'ITERS':<7}{'SELF':<10}")
    print("-" * 76)
    for r in results:
        s = r.get("summary") or {}
        v = (s.get("final_verdict") or {})
        iters = len(s.get("iterations") or [])
        print(f"{r['profile']:<18}{r['exit_code']:<6}{r['elapsed_seconds']:<10}"
              f"{(v.get('overall') or '?'):<14}"
              f"{(v.get('L1_structural') or '?'):<8}"
              f"{(v.get('L2_semantic') or '?'):<8}"
              f"{(v.get('L3_user_view') or '?'):<8}"
              f"{iters:<7}"
              f"{(v.get('model_self_confidence') or '?'):<10}")

    (out / "results.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[done] {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
