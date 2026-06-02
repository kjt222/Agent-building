"""Tests for the KLayout and Sentaurus L2 oracle skeletons (P14.2.4/.5)."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.acceptance.oracle import get_oracle
from agent.acceptance.oracles.klayout import KLayoutOracle, _try_gdspy, _try_klayout_db
from agent.acceptance.oracles.sentaurus import SentaurusOracle


# ---- registration ----


def test_oracles_registered():
    from agent.acceptance import oracles  # noqa: F401
    assert get_oracle("klayout") is not None
    assert get_oracle("sentaurus") is not None


# ---- KLayout ----


def test_klayout_with_no_gds_paths_returns_unknown():
    rep = KLayoutOracle().check([Path("foo.txt")])
    assert rep.verdict == "unknown"
    assert any(".gds" in f for f in rep.findings)


def test_klayout_with_no_backend_returns_unknown(monkeypatch, tmp_path):
    # Force both backends unavailable
    import agent.acceptance.oracles.klayout as kmod
    monkeypatch.setattr(kmod, "_try_klayout_db", lambda: None)
    monkeypatch.setattr(kmod, "_try_gdspy", lambda: None)
    p = tmp_path / "x.gds"
    p.write_bytes(b"")  # not a real gds, but oracle short-circuits earlier
    rep = KLayoutOracle().check([p])
    assert rep.verdict == "unknown"
    assert any("klayout" in f.lower() or "gdspy" in f.lower() for f in rep.findings)


@pytest.mark.skipif(
    _try_klayout_db() is None and _try_gdspy() is None,
    reason="neither klayout.db nor gdspy installed",
)
def test_klayout_missing_file_fails(tmp_path):
    rep = KLayoutOracle().check([tmp_path / "ghost.gds"])
    assert rep.verdict == "fail"


# ---- Sentaurus ----


def test_sentaurus_with_no_logs_returns_unknown(tmp_path):
    # Only a binary-ish file present, no .log/.out/.txt
    p = tmp_path / "out.tdr"
    p.write_bytes(b"binary garbage")
    rep = SentaurusOracle().check([p])
    assert rep.verdict == "unknown"
    assert any("log" in f.lower() for f in rep.findings)


def test_sentaurus_log_with_convergence_passes(tmp_path):
    p = tmp_path / "run.log"
    p.write_text("Iteration 5\nFinal solution found\nSimulation completed\nconverged\n")
    rep = SentaurusOracle().check([p])
    assert rep.verdict == "pass", rep.findings


def test_sentaurus_log_with_fatal_fails(tmp_path):
    p = tmp_path / "run.log"
    p.write_text("starting...\nFATAL ERROR: Newton iteration did not converge\n")
    rep = SentaurusOracle().check([p])
    assert rep.verdict == "fail"
    assert any("fatal" in f.lower() for f in rep.findings)


def test_sentaurus_log_with_no_convergence_marker_fails(tmp_path):
    p = tmp_path / "run.log"
    p.write_text("just some noise but no completion marker\n")
    rep = SentaurusOracle().check([p])
    assert rep.verdict == "fail"
    assert any("must-contain" in f.lower() for f in rep.findings)


def test_sentaurus_runtime_budget(tmp_path):
    p = tmp_path / "run.log"
    p.write_text(
        "Simulation completed\nconverged\n"
        "elapsed time: 7200 s\n"
    )
    rep = SentaurusOracle().check(
        [p],
        task_spec={"acceptance": {"sentaurus": {"max_runtime_s": 3600}}},
    )
    assert rep.verdict == "fail"
    assert any("runtime" in f.lower() for f in rep.findings)


def test_sentaurus_custom_keywords(tmp_path):
    p = tmp_path / "run.log"
    p.write_text("everything OK\n")
    rep = SentaurusOracle().check(
        [p],
        task_spec={"acceptance": {"sentaurus": {
            "must_contain_keywords": ["OK"],
            "must_not_contain_keywords": ["BAD"],
        }}},
    )
    assert rep.verdict == "pass"
