"""Tests for OBSIDIAN_MIRROR_ROOT path guard (P14.6.15)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import pytest

from agent.tools_capability.obsidian._mirror_guard import (
    guard_canvas_path,
    mirror_root,
    rest_disabled_reason,
)


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    monkeypatch.delenv("OBSIDIAN_MIRROR_ROOT", raising=False)
    yield


def test_no_env_means_no_mirror():
    assert mirror_root() is None
    assert guard_canvas_path(Path("/etc/passwd")) is None
    assert rest_disabled_reason() is None


def test_path_inside_mirror_allowed(tmp_path, monkeypatch):
    monkeypatch.setenv("OBSIDIAN_MIRROR_ROOT", str(tmp_path))
    f = tmp_path / "vault" / "note.md"
    f.parent.mkdir(parents=True)
    f.write_text("x", encoding="utf-8")
    assert guard_canvas_path(f) is None


def test_path_outside_mirror_rejected(tmp_path, monkeypatch):
    mirror = tmp_path / "mirror"
    mirror.mkdir()
    outside = tmp_path / "real_vault" / "note.md"
    outside.parent.mkdir()
    outside.write_text("x", encoding="utf-8")
    monkeypatch.setenv("OBSIDIAN_MIRROR_ROOT", str(mirror))
    msg = guard_canvas_path(outside)
    assert msg is not None
    assert "outside the mirror" in msg
    assert str(mirror) in msg


def test_rest_disabled_in_mirror_mode(tmp_path, monkeypatch):
    monkeypatch.setenv("OBSIDIAN_MIRROR_ROOT", str(tmp_path))
    reason = rest_disabled_reason()
    assert reason is not None
    assert "mirror mode" in reason
