"""Tests for the ExcelRuntimeEdit structured diff builder (P12.2.2)."""

from __future__ import annotations

from agent.core.hooks import build_excel_runtime_diff
from agent.core.loop import LoopConfig, LoopContext, ToolUseBlock


def _use(ops):
    return ToolUseBlock(
        id="t1",
        name="ExcelRuntimeEdit",
        input={"path": "book.xlsx", "ops": ops},
    )


def _ctx():
    return LoopContext(config=LoopConfig())


def test_set_cell_row_carries_sheet_cell_value():
    use = _use([{"op": "set_cell", "sheet": "Q1", "cell": "B2", "value": 12345}])
    payload = build_excel_runtime_diff(use, _ctx())
    assert payload is not None
    assert payload["tool"] == "ExcelRuntimeEdit"
    assert payload["path"] == "book.xlsx"
    row = payload["op_summary"][0]
    assert row["kind"] == "set_cell"
    assert row["sheet"] == "Q1"
    assert row["cell"] == "B2"
    assert row["value"] == "12345"
    assert "Q1!B2" in row["summary"]


def test_set_cell_truncates_long_value():
    long_value = "x" * 500
    use = _use([{"op": "set_cell", "sheet": "S", "cell": "A1", "value": long_value}])
    payload = build_excel_runtime_diff(use, _ctx())
    row = payload["op_summary"][0]
    assert len(row["value"]) <= 121  # 120 + ellipsis
    assert row["value"].endswith("…")


def test_set_formula_row_emits_formula_and_coord():
    use = _use([{"op": "set_formula", "sheet": "Sum", "cell": "C3", "formula": "=A1+B1"}])
    payload = build_excel_runtime_diff(use, _ctx())
    row = payload["op_summary"][0]
    assert row["kind"] == "set_formula"
    assert row["formula"] == "=A1+B1"
    assert row["sheet"] == "Sum"
    assert row["cell"] == "C3"


def test_set_range_values_computes_rows_cols_and_sample():
    values = [
        [1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
        [11, 12, 13, 14, 15, 16, 17, 18, 19, 20],
        [21, 22, 23, 24, 25, 26, 27, 28, 29, 30],
    ]
    use = _use([
        {
            "op": "set_range_values",
            "sheet": "Data",
            "range": "A1:J3",
            "values": values,
        }
    ])
    payload = build_excel_runtime_diff(use, _ctx())
    row = payload["op_summary"][0]
    assert row["kind"] == "set_range_values"
    assert row["rows"] == 3
    assert row["cols"] == 10
    assert len(row["sample_row"]) == 8  # capped
    assert row["sample_row"][0] == "1"
    assert "3×10" in row["summary"]


def test_create_named_range_with_refers_to():
    use = _use([
        {"op": "create_named_range", "name": "Sales", "refers_to": "=Sheet1!$A$1:$C$5"}
    ])
    payload = build_excel_runtime_diff(use, _ctx())
    row = payload["op_summary"][0]
    assert row["kind"] == "create_named_range"
    assert row["name"] == "Sales"
    assert row["target"] == "=Sheet1!$A$1:$C$5"


def test_create_named_range_falls_back_to_sheet_range():
    use = _use([
        {"op": "create_named_range", "name": "Sales", "sheet": "S1", "range": "A1:B2"}
    ])
    payload = build_excel_runtime_diff(use, _ctx())
    row = payload["op_summary"][0]
    assert row["target"] == "S1!A1:B2"


def test_refresh_calculation_is_side_effect():
    use = _use([{"op": "refresh_calculation"}])
    payload = build_excel_runtime_diff(use, _ctx())
    assert payload is not None
    row = payload["op_summary"][0]
    assert row["kind"] == "side_effect"
    assert "Recalculate" in row["summary"]


def test_get_structure_only_returns_none():
    use = _use([{"op": "get_structure"}])
    payload = build_excel_runtime_diff(use, _ctx())
    assert payload is None


def test_mixed_read_only_and_mutating_drops_read_only_rows():
    use = _use([
        {"op": "get_structure"},
        {"op": "set_cell", "sheet": "S", "cell": "A1", "value": 1},
    ])
    payload = build_excel_runtime_diff(use, _ctx())
    assert payload is not None
    assert payload["op_count"] == 1
    assert payload["op_summary"][0]["kind"] == "set_cell"


def test_empty_ops_returns_none():
    use = _use([])
    payload = build_excel_runtime_diff(use, _ctx())
    assert payload is None
