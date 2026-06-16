"""Structural conformance for harness_bench task modules + verifier logic for
the reconstructed P18-C tasks (09-12).

These don't drive a live model — they check every task_NN module satisfies the
base.py contract, and that the new P18-C verifiers accept a correctly-completed
fixture and reject an untouched one (so they can't silently pass).
"""

from __future__ import annotations

import importlib
import pkgutil
import shutil
from pathlib import Path

import pytest

import tests.harness_bench as hb
from tests.harness_bench.base import RunOutcome


def _task_modules():
    mods = []
    for info in pkgutil.iter_modules(hb.__path__):
        if info.name.startswith("task_"):
            mods.append(importlib.import_module(f"tests.harness_bench.{info.name}"))
    return mods


def test_at_least_the_expected_task_ids_present():
    ids = set()
    for info in pkgutil.iter_modules(hb.__path__):
        if info.name.startswith("task_"):
            ids.add(int(info.name.split("_")[1]))
    # Tiers A(1-3) B(4-8) C(9-12) D(13-14).
    assert {9, 10, 11, 12}.issubset(ids), sorted(ids)


@pytest.mark.parametrize("mod", _task_modules(), ids=lambda m: m.__name__.split(".")[-1])
def test_task_conforms_to_contract(mod):
    assert isinstance(getattr(mod, "PROMPT"), str)
    assert getattr(mod, "MODE", "read-only") in ("read-only", "full-access")
    assert callable(getattr(mod, "setup"))
    assert callable(getattr(mod, "verify"))


def _run_lifecycle(mod, mutate):
    state = mod.setup()
    try:
        mutate(state)
        return mod.verify(RunOutcome(), state)
    finally:
        td = getattr(mod, "teardown", None)
        if callable(td):
            td(state)


def test_task_09_accepts_completed_and_rejects_untouched():
    mod = importlib.import_module("tests.harness_bench.task_09_organize_by_extension")

    def complete(state):
        wd = Path(state["workdir"])
        for name, ext in [
            ("notes.txt", "txt"), ("second.txt", "txt"),
            ("data.csv", "csv"), ("report.md", "md"),
        ]:
            (wd / ext).mkdir(exist_ok=True)
            shutil.move(str(wd / name), str(wd / ext / name))
        (wd / "scratch.tmp").unlink()

    assert _run_lifecycle(mod, complete)[0] is True
    assert _run_lifecycle(mod, lambda s: None)[0] is False


def test_task_10_accepts_completed_and_rejects_untouched():
    mod = importlib.import_module("tests.harness_bench.task_10_dedupe_lines")

    def complete(state):
        Path(state["path"]).write_text("alpha\nbeta\ngamma\ndelta\n", encoding="utf-8")

    assert _run_lifecycle(mod, complete)[0] is True
    assert _run_lifecycle(mod, lambda s: None)[0] is False


def test_task_11_accepts_completed_and_rejects_untouched():
    mod = importlib.import_module("tests.harness_bench.task_11_count_todos")

    def complete(state):
        Path(state["out"]).write_text("3", encoding="utf-8")

    assert _run_lifecycle(mod, complete)[0] is True
    assert _run_lifecycle(mod, lambda s: None)[0] is False


def test_task_12_accepts_completed_and_rejects_untouched():
    mod = importlib.import_module("tests.harness_bench.task_12_bounded_single_edit")

    def complete(state):
        p = Path(state["path"])
        p.write_text(p.read_text(encoding="utf-8").replace("__VERSION__", "1.0"), encoding="utf-8")

    assert _run_lifecycle(mod, complete)[0] is True
    assert _run_lifecycle(mod, lambda s: None)[0] is False
