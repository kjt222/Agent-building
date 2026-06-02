"""Slice 1: CLI list + run --dry-run."""

from __future__ import annotations

import json
import sys
from io import StringIO

from agent.eval.cli import main


def _capture(argv: list[str]) -> tuple[int, str]:
    old = sys.stdout
    buf = StringIO()
    sys.stdout = buf
    try:
        rc = main(argv)
    finally:
        sys.stdout = old
    return rc, buf.getvalue()


def test_list_text_format_lists_tier_a():
    rc, out = _capture(["list", "--suite", "tier_a"])
    assert rc == 0
    assert "p4_word_thesis_all_in_one" in out


def test_list_json_format_is_valid_json():
    rc, out = _capture(["list", "--suite", "tier_a", "--format", "json"])
    assert rc == 0
    items = json.loads(out)
    assert isinstance(items, list)
    assert any(it["id"] == "p4_word_thesis_all_in_one" for it in items)


def test_run_dry_run_plans_commands(tmp_path):
    results_root = tmp_path / "eval_out"
    rc, out = _capture([
        "run", "--suite", "tier_a", "--models", "doubao-code",
        "--dry-run", "--results-root", str(results_root),
        "--filter", "p4_word",
    ])
    summary = json.loads((results_root / "summary.json").read_text(encoding="utf-8"))
    assert summary["dry_run"] is True
    assert summary["totals"]["n"] == 2  # both word scenarios
    for c in summary["cases"]:
        assert c["case_id"].startswith("p4_word_")
        assert c["model"] == "doubao-code"
        assert "python" in c["invocation_cmd"][0]
    # dry-run returns 0 when planning succeeded for every case.
    assert rc == 0


def test_run_without_models_errors():
    rc, _ = _capture(["run", "--suite", "tier_a", "--models", ""])
    assert rc == 1


def test_run_unknown_filter_errors(tmp_path):
    rc, _ = _capture([
        "run", "--suite", "tier_a", "--models", "doubao-code",
        "--filter", "nonexistent_case_xxx",
        "--results-root", str(tmp_path / "out"),
    ])
    assert rc == 1
