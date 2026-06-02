"""Tests for the L3 vision_judge (P14.3.1).

We don't hit the real API in unit tests; the contract we verify is:
  - soft-fails (returns unknown, no exception) when image missing
  - soft-fails when ANTHROPIC_API_KEY missing
  - parses well-formed JSON response
  - parses JSON wrapped in chatter / fences
  - degrades to unknown when response unparseable
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.acceptance.vision_judge import JudgeReport, _parse_response, judge


def test_judge_unknown_when_image_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")
    rep = judge(tmp_path / "nope.png", {"user_prompt": "x"})
    assert rep.verdict == "unknown"
    assert rep.error and "not found" in rep.error


def test_judge_unknown_when_no_backend_key(tmp_path, monkeypatch):
    """When no vision backend key is available anywhere, soft-fail with
    a multi-key error message."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    # Mock keyring to return None for every ref
    import agent.acceptance.vision_judge as vj
    monkeypatch.setattr(
        vj, "_resolve_backend", lambda prefer=None: None
    )
    p = tmp_path / "x.png"
    from PIL import Image
    Image.new("RGB", (32, 32)).save(p)
    rep = judge(p, {"user_prompt": "x"})
    assert rep.verdict == "unknown"
    assert rep.error and "no vision backend" in rep.error


def test_parse_response_well_formed():
    text = '{"verdict": "pass", "confidence": "high", "findings": ["ok"], "unmet_requirements": []}'
    v, c, f, u = _parse_response(text)
    assert v == "pass"
    assert c == "high"
    assert f == ["ok"]
    assert u == []


def test_parse_response_wrapped_in_chatter():
    text = (
        "Sure! Here is my evaluation:\n"
        '```json\n{"verdict": "partial", "confidence": "med", '
        '"findings": ["公式靠下"], "unmet_requirements": ["未分组"]}\n```\n'
        "Hope that helps!"
    )
    v, c, f, u = _parse_response(text)
    assert v == "partial"
    assert c == "med"
    assert "公式靠下" in f
    assert "未分组" in u


def test_parse_response_invalid_json_unknown():
    v, c, f, u = _parse_response("the answer is yes pass high")
    assert v == "unknown"
    assert c == "unknown"
    assert f == []


def test_parse_response_invalid_verdict_value_falls_back():
    text = '{"verdict": "amazing", "confidence": "great", "findings": [1, 2]}'
    v, c, f, u = _parse_response(text)
    assert v == "unknown"
    assert c == "unknown"
    # numeric findings cast to strings
    assert f == ["1", "2"]


def test_report_to_dict_serializable():
    rep = JudgeReport(verdict="pass", confidence="high",
                      findings=["a"], unmet_requirements=["b"],
                      raw_response="x" * 5000, model="claude-sonnet-4-6")
    d = rep.to_dict()
    assert d["verdict"] == "pass"
    # raw_response truncated to 2000
    assert len(d["raw_response"]) == 2000
    import json
    json.dumps(d)
