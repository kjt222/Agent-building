"""Unit tests for ExcelArtifactManifest derivation (P11.3.2)."""

from __future__ import annotations

from agent.core.artifact_context import (
    ArtifactKind,
    ExcelArtifactManifest,
    get_registry,
    register_excel_artifact_from_read,
    register_excel_artifact_from_runtime,
    reset_all_registries,
    serialize_for_context,
)


# Matches the shape returned by ExcelRuntimeEdit's structure_after (smoke output).
_RUNTIME_STRUCTURE_HEALTHY = {
    "sheets": ["Sales", "Quarterly"],
    "active_sheet": "Sales",
    "used_ranges": {"Sales": "$A$1:$B$4", "Quarterly": "$A$1:$C$5"},
    "formulas": [
        {"sheet": "Sales", "cell": "B4", "formula": "=SUM(B2:B3)", "value": 40.0},
    ],
    "formula_errors": [],
    "names": [
        {"name": "SalesData", "refers_to": "=Sales!$A$1:$B$4", "valid": True},
    ],
    "chart_counts": {"Sales": 1, "Quarterly": 2},
    "calculation_mode": -4105,  # xlCalculationAutomatic
}

_RUNTIME_STRUCTURE_BROKEN = {
    "sheets": ["Data"],
    "active_sheet": "Data",
    "used_ranges": {"Data": "$A$1:$D$10"},
    "formulas": [
        {"sheet": "Data", "cell": "D5", "formula": "=A5/B5", "value": "#DIV/0!"},
    ],
    "formula_errors": [{"cell": "D5", "error": "#DIV/0!"}],
    "names": [
        {"name": "Stale", "refers_to": "=#REF!", "valid": False},
        {"name": "Good", "refers_to": "=Data!$A$1", "valid": True},
    ],
    "chart_counts": {"Data": 0},
    "calculation_mode": -4135,  # xlCalculationManual
}

# Matches what ExcelRead returns (compressed shape).
_READ_RESULT = {
    "type": "excel_read",
    "path": "C:\\book.xlsx",
    "sheets": ["Sales"],
    "active_sheet": "Sales",
    "inspected_sheets": [
        {
            "name": "Sales",
            "used_range": "A1:B4",
            "inspected_range": "A1:B4",
            "max_row": 4,
            "cells": [
                {"address": "A1", "value": "Quarter"},
                {"address": "B1", "value": "Sales"},
                {"address": "B4", "value": "=SUM(B2:B3)", "formula": "=SUM(B2:B3)"},
            ],
        }
    ],
}


def setup_function(_func):
    reset_all_registries()


def test_manifest_from_runtime_structure_healthy():
    m = ExcelArtifactManifest.from_runtime_structure("C:\\b.xlsx", _RUNTIME_STRUCTURE_HEALTHY)
    assert m.kind == ArtifactKind.EXCEL
    assert m.path == "C:\\b.xlsx"
    assert m.sheets == ["Sales", "Quarterly"]
    assert m.active_sheet == "Sales"
    assert m.used_ranges == {"Sales": "$A$1:$B$4", "Quarterly": "$A$1:$C$5"}
    assert m.formula_count == 1
    assert m.formula_error_count == 0
    assert len(m.named_ranges) == 1 and m.named_ranges[0]["valid"] is True
    assert m.chart_counts == {"Sales": 1, "Quarterly": 2}
    assert m.calculation_mode == "automatic"


def test_manifest_from_runtime_structure_broken():
    m = ExcelArtifactManifest.from_runtime_structure("b.xlsx", _RUNTIME_STRUCTURE_BROKEN)
    assert m.formula_error_count == 1
    assert m.formula_errors[0]["cell"] == "D5"
    invalid = [n for n in m.named_ranges if not n["valid"]]
    assert len(invalid) == 1 and invalid[0]["name"] == "Stale"
    assert m.calculation_mode == "manual"


def test_manifest_compact_text_healthy():
    m = ExcelArtifactManifest.from_runtime_structure("b.xlsx", _RUNTIME_STRUCTURE_HEALTHY)
    text = m.to_compact_text()
    assert text.startswith('<artifact kind="excel" path="b.xlsx">')
    assert "sheets (2): Sales, Quarterly" in text
    assert "active=Sales" in text
    assert "formulas: 1" in text
    assert "ERROR" not in text
    assert "named_ranges: 1 valid" in text
    assert "charts: 3" in text  # 1 + 2
    # automatic is the default; the compact form omits it.
    assert "calc_mode" not in text


def test_manifest_compact_text_broken_surfaces_problems():
    m = ExcelArtifactManifest.from_runtime_structure("b.xlsx", _RUNTIME_STRUCTURE_BROKEN)
    text = m.to_compact_text()
    assert "1 ERROR" in text
    assert "formula_error: D5" in text
    assert "INVALID" in text and "Stale" in text
    assert "calc_mode: manual" in text


def test_manifest_from_read_result_is_sparse_but_present():
    m = ExcelArtifactManifest.from_read_result("C:\\book.xlsx", _READ_RESULT)
    assert m.sheets == ["Sales"]
    assert m.active_sheet == "Sales"
    assert m.used_ranges == {"Sales": "A1:B4"}
    assert m.formula_count == 1
    assert m.named_ranges == []  # not derivable from ExcelRead
    assert m.chart_counts == {}
    assert m.calculation_mode is None


def test_registry_round_trip_runtime():
    register_excel_artifact_from_runtime(
        "conv-x", "C:\\book.xlsx", _RUNTIME_STRUCTURE_HEALTHY
    )
    rec = get_registry("conv-x").get("C:\\book.xlsx")
    assert rec is not None and rec.kind == ArtifactKind.EXCEL
    text = serialize_for_context("conv-x")
    assert "C:\\book.xlsx" in text
    assert "charts: 3" in text


def test_registry_runtime_overrides_read_result():
    # An ExcelRead happens first → sparse manifest.
    register_excel_artifact_from_read(
        "conv-y", "C:\\book.xlsx", _READ_RESULT
    )
    rec_initial = get_registry("conv-y").get("C:\\book.xlsx")
    assert rec_initial.manifest.chart_counts == {}
    # Then ExcelRuntimeEdit fires → richer manifest replaces it.
    register_excel_artifact_from_runtime(
        "conv-y", "C:\\book.xlsx", _RUNTIME_STRUCTURE_HEALTHY
    )
    rec_after = get_registry("conv-y").get("C:\\book.xlsx")
    assert rec_after.manifest.chart_counts == {"Sales": 1, "Quarterly": 2}


def test_calc_mode_label_unknown_value_marked():
    m = ExcelArtifactManifest.from_runtime_structure(
        "b.xlsx", {**_RUNTIME_STRUCTURE_HEALTHY, "calculation_mode": 99}
    )
    assert m.calculation_mode and m.calculation_mode.startswith("unknown")


def test_word_and_excel_coexist_in_one_serialization():
    from agent.core.artifact_context import register_word_artifact

    register_word_artifact("conv-mix", "a.docx", {
        "headings": [{"text": "H1", "level": 1}],
        "has_toc_field": False,
        "page_count": 1,
        "paragraph_count": 1,
    })
    register_excel_artifact_from_runtime(
        "conv-mix", "b.xlsx", _RUNTIME_STRUCTURE_HEALTHY
    )
    text = serialize_for_context("conv-mix")
    assert 'kind="word"' in text and 'kind="excel"' in text
