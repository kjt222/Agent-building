"""Slice 2/3: runner helpers (output path resolution, profile swap)."""

from __future__ import annotations

import time
from pathlib import Path

from agent.eval.runner import (
    resolve_output_path,
    temporary_active_profile,
    APP_YAML,
)


def test_resolve_output_path_literal(tmp_path):
    f = tmp_path / "summary.json"
    f.write_text("{}", encoding="utf-8")
    assert resolve_output_path("{artifact_root}/summary.json", tmp_path) == f


def test_resolve_output_path_returns_none_when_missing(tmp_path):
    assert resolve_output_path("{artifact_root}/summary.json", tmp_path) is None


def test_resolve_output_path_glob_picks_newest(tmp_path):
    a = tmp_path / "20260101_000000" / "summary.json"
    a.parent.mkdir()
    a.write_text("{}", encoding="utf-8")
    time.sleep(0.05)
    b = tmp_path / "20260102_000000" / "summary.json"
    b.parent.mkdir()
    b.write_text("{}", encoding="utf-8")
    resolved = resolve_output_path("{artifact_root}/*/summary.json", tmp_path)
    assert resolved == b


def test_resolve_output_path_min_mtime_filters_stale(tmp_path):
    """A glob template must not pick a pre-run file when min_mtime is set."""
    old = tmp_path / "20260101_000000" / "summary.json"
    old.parent.mkdir()
    old.write_text("{}", encoding="utf-8")
    # Cut-off is in the future relative to `old` -> old must be filtered.
    cutoff = time.time() + 10.0
    assert resolve_output_path(
        "{artifact_root}/*/summary.json", tmp_path, min_mtime=cutoff
    ) is None
    # Without min_mtime, the same template returns the stale file.
    assert resolve_output_path("{artifact_root}/*/summary.json", tmp_path) == old


def test_temporary_active_profile_restores_on_exit():
    original = ""
    for line in APP_YAML.read_text(encoding="utf-8").splitlines():
        if line.startswith("active_profile:"):
            original = line.split(":", 1)[1].strip()
            break
    assert original, "config/app.yaml has no active_profile"
    with temporary_active_profile("doubao-code") as old:
        assert old == original
        assert "active_profile: doubao-code" in APP_YAML.read_text(encoding="utf-8")
    final = APP_YAML.read_text(encoding="utf-8")
    assert f"active_profile: {original}" in final
