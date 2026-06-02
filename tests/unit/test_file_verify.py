"""P13.0.1 FileVerify — per-assertion coverage + composite passing/failing runs."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from agent.tools_v2.file_verify_tool import FileVerifyTool, _resolve_path


class _FakeConfig:
    workspace_root: Path | None = None


class _FakeCtx:
    def __init__(self, workspace: Path):
        self.config = _FakeConfig()
        self.config.workspace_root = workspace
        self.scratch: dict = {}


def _run(tool: FileVerifyTool, ctx, *, target: str, assertions: list[dict]) -> dict:
    block = asyncio.run(
        tool.run({"target": target, "assertions": assertions}, ctx)
    )
    return json.loads(block.content)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def ctx(workspace: Path) -> _FakeCtx:
    return _FakeCtx(workspace)


@pytest.fixture
def tool() -> FileVerifyTool:
    return FileVerifyTool()


def test_file_exists_passes(tool, ctx, workspace):
    f = workspace / "a.txt"
    f.write_text("hello", encoding="utf-8")
    r = _run(tool, ctx, target=str(f), assertions=[{"type": "file_exists"}])
    assert r["ok"] is True
    assert r["assertions"][0]["ok"] is True


def test_file_exists_fails(tool, ctx, workspace):
    r = _run(tool, ctx, target=str(workspace / "missing.txt"),
             assertions=[{"type": "file_exists"}])
    assert r["ok"] is False
    assert r["assertions"][0]["ok"] is False


def test_size_bytes_min_max(tool, ctx, workspace):
    f = workspace / "b.txt"
    f.write_text("a" * 100, encoding="utf-8")
    r = _run(tool, ctx, target=str(f), assertions=[
        {"type": "size_bytes", "min": 50, "max": 200},
    ])
    assert r["ok"] is True
    r2 = _run(tool, ctx, target=str(f), assertions=[
        {"type": "size_bytes", "max": 50},
    ])
    assert r2["ok"] is False
    assert r2["assertions"][0]["actual"] == 100


def test_regex_match_and_not_match(tool, ctx, workspace):
    f = workspace / "c.md"
    f.write_text("excalidraw-plugin: parsed\n", encoding="utf-8")
    r = _run(tool, ctx, target=str(f), assertions=[
        {"type": "regex_match", "pattern": r"excalidraw-plugin:\s*parsed"},
        {"type": "regex_not_match", "pattern": "FORBIDDEN"},
    ])
    assert r["ok"] is True
    r2 = _run(tool, ctx, target=str(f), assertions=[
        {"type": "regex_not_match", "pattern": "parsed"},
    ])
    assert r2["ok"] is False


def test_contains_text(tool, ctx, workspace):
    f = workspace / "d.txt"
    f.write_text("the quick brown fox", encoding="utf-8")
    r = _run(tool, ctx, target=str(f), assertions=[
        {"type": "contains_text", "text": "quick brown"},
    ])
    assert r["ok"] is True


def test_extracted_block_parses_json(tool, ctx, workspace):
    f = workspace / "e.md"
    f.write_text("preamble\n%%\n{\"a\": 1, \"b\": [1, 2]}\n%%\nepilogue",
                 encoding="utf-8")
    r = _run(tool, ctx, target=str(f), assertions=[
        {"type": "extracted_block_parses", "between": ["%%", "%%"], "as": "json"},
    ])
    assert r["ok"] is True
    assert r["assertions"][0]["parser"] == "json"


def test_extracted_block_fail_when_markers_missing(tool, ctx, workspace):
    f = workspace / "f.md"
    f.write_text("plain text no markers", encoding="utf-8")
    r = _run(tool, ctx, target=str(f), assertions=[
        {"type": "extracted_block_parses", "between": ["%%", "%%"]},
    ])
    assert r["ok"] is False
    assert "block markers not found" in r["assertions"][0]["error"]


def test_extracted_block_fail_when_inner_invalid(tool, ctx, workspace):
    f = workspace / "g.md"
    f.write_text("%%\n{not valid json}\n%%", encoding="utf-8")
    r = _run(tool, ctx, target=str(f), assertions=[
        {"type": "extracted_block_parses", "between": ["%%", "%%"]},
    ])
    assert r["ok"] is False
    assert "JSONDecodeError" in r["assertions"][0]["error"] or "JSON" in r["assertions"][0]["error"]


def test_json_path_equals_and_exists_and_count(tool, ctx, workspace):
    payload = {
        "type": "excalidraw",
        "elements": [
            {"id": "e1", "type": "image"},
            {"id": "e2", "type": "text", "customData": {"latex_source": "x^2"}},
        ],
    }
    f = workspace / "h.json"
    f.write_text(json.dumps(payload), encoding="utf-8")
    r = _run(tool, ctx, target=str(f), assertions=[
        {"type": "json_path_equals", "path": "type", "value": "excalidraw"},
        {"type": "json_path_exists", "path": "elements.1.customData.latex_source"},
        {"type": "json_path_count_min", "path": "elements.*", "min": 2},
        {"type": "json_path_equals", "path": "elements.0.type", "value": "image"},
    ])
    assert r["ok"] is True, r["assertions"]


def test_json_path_in_extracted_block(tool, ctx, workspace):
    f = workspace / "i.md"
    block = json.dumps({"elements": [{"type": "image", "customData": {"latex_source": "E=mc^2"}}]})
    f.write_text(f"hdr\n%%\n{block}\n%%\n", encoding="utf-8")
    r = _run(tool, ctx, target=str(f), assertions=[
        {"type": "json_path_exists", "between": ["%%", "%%"],
         "path": "elements.0.customData.latex_source"},
    ])
    assert r["ok"] is True
    assert r["assertions"][0]["actual_count"] == 1


def test_json_path_count_min_fails(tool, ctx, workspace):
    f = workspace / "j.json"
    f.write_text(json.dumps({"elements": [{}]}), encoding="utf-8")
    r = _run(tool, ctx, target=str(f), assertions=[
        {"type": "json_path_count_min", "path": "elements.*", "min": 3},
    ])
    assert r["ok"] is False
    assert r["assertions"][0]["actual_count"] == 1


def test_python_predicate_with_data_arg(tool, ctx, workspace):
    f = workspace / "k.json"
    f.write_text(json.dumps({"elements": [{"type": "image"}, {"type": "text"}]}),
                 encoding="utf-8")
    r = _run(tool, ctx, target=str(f), assertions=[
        {"type": "python_predicate",
         "code": "lambda d: any(e['type']=='image' for e in d['elements'])"},
    ])
    assert r["ok"] is True


def test_python_predicate_error_is_captured(tool, ctx, workspace):
    f = workspace / "l.json"
    f.write_text("{}", encoding="utf-8")
    r = _run(tool, ctx, target=str(f), assertions=[
        {"type": "python_predicate", "code": "lambda d: 1/0"},
    ])
    assert r["ok"] is False
    assert "ZeroDivisionError" in r["assertions"][0]["error"]


def test_resolve_path_handles_arrays_and_wildcards():
    data = {"a": [{"b": 1}, {"b": 2}, {"b": 3}]}
    assert _resolve_path(data, "a.0.b") == [1]
    assert _resolve_path(data, "a.*.b") == [1, 2, 3]
    assert _resolve_path(data, "a.[*].b") == [1, 2, 3]
    assert _resolve_path(data, "missing") == []
    assert _resolve_path(data, "") == [data]


def test_unknown_assertion_type_fails_clearly(tool, ctx, workspace):
    f = workspace / "m.txt"
    f.write_text("x", encoding="utf-8")
    r = _run(tool, ctx, target=str(f), assertions=[
        {"type": "made_up_thing"},
    ])
    assert r["ok"] is False
    assert "unknown assertion type" in r["assertions"][0]["error"]


def test_composite_failing_run_summary(tool, ctx, workspace):
    f = workspace / "n.json"
    f.write_text(json.dumps({"a": 1}), encoding="utf-8")
    r = _run(tool, ctx, target=str(f), assertions=[
        {"type": "file_exists"},
        {"type": "json_path_equals", "path": "a", "value": 1},
        {"type": "json_path_equals", "path": "missing", "value": "x"},
        {"type": "regex_match", "pattern": "NOPE"},
    ])
    assert r["ok"] is False
    assert r["summary"]["total"] == 4
    assert r["summary"]["passed"] == 2
    assert r["summary"]["failed"] == 2
    assert "json_path_equals" in r["summary"]["failing"]
    assert "regex_match" in r["summary"]["failing"]
