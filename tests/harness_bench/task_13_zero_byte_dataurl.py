"""Tier D — verifier-sanity test 13: 0-byte dataURL must red-light.

No agent is invoked (PROMPT == ""). setup() fabricates an Excalidraw-like
artifact where one element references a fileId whose dataURL is empty string —
the classic silent failure mode after a failed LaTeX-to-SVG render. The
strict verifier (which Tier B task 5 / 6 will also use) must return False.

Task passes if the strict verifier correctly red-lights the bad artifact.
If the verifier returns True ("looks fine to me"), Tier B's verdict on real
agent runs is unreliable.
"""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
from typing import Any

from .verifiers import strict_check_fileids


NEEDS_AGENT = False               # verifier-sanity only; no agent invocation
PROMPT = ""
MODE = "read-only"
TIMEOUT_S = 5.0


def setup() -> dict[str, Any]:
    workdir = Path(tempfile.mkdtemp(prefix="harness_bench_t13_"))
    canvas = {
        "elements": [
            {"id": "rect-1", "type": "rectangle", "x": 0, "y": 0,
             "width": 100, "height": 50},
            # Silent-failure element: image references a fileId whose dataURL
            # was never populated (e.g. katex rendering threw, agent ignored it).
            {"id": "img-bad", "type": "image", "x": 100, "y": 0,
             "width": 50, "height": 50, "fileId": "sha-empty"},
        ],
        "files": {
            "sha-empty": {"id": "sha-empty", "mimeType": "image/svg+xml",
                          "dataURL": ""},
        },
    }
    artifact = workdir / "fake_canvas.json"
    artifact.write_text(json.dumps(canvas, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    # Simulate an agent that lied about success.
    fake_claim = "All done! The image element is fixed and renders correctly."
    return {
        "workdir": str(workdir),
        "artifact": str(artifact),
        "fake_assistant_text": fake_claim,
    }


def verify(outcome, state) -> tuple[bool, str]:
    canvas = json.loads(Path(state["artifact"]).read_text(encoding="utf-8"))
    ok, reason = strict_check_fileids(canvas["elements"], canvas["files"])
    if ok:
        # Verifier blessed a known-bad artifact — Tier B verdicts cannot be trusted.
        return False, f"VERIFIER LIED: said OK on artifact with empty dataURL ({reason})"
    return True, f"verifier correctly red-lighted: {reason}"


def teardown(state) -> None:
    import shutil
    wd = state.get("workdir")
    if wd:
        shutil.rmtree(wd, ignore_errors=True)
