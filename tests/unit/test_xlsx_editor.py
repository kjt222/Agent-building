import tempfile
import unittest
from pathlib import Path

import openpyxl

from agent.tools import XlsxEditor


class TestXlsxEditor(unittest.TestCase):
    def test_classify_and_apply(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "sample.xlsx"
            workbook = openpyxl.Workbook()
            sheet = workbook.active
            sheet.title = "Sheet1"
            sheet["A1"] = 1
            workbook.save(path)

            editor = XlsxEditor(path)
            ops = [
                {"op": "set_cell", "cell": "A1", "value": 2},
                {"op": "set_cell", "cell": "B1", "formula": "=A1+1"},
                {"op": "fill_formula", "range": "C1:C2", "formula": "=A{row}+1"},
                {"op": "set_auto_filter", "range": "A1:C2"},
                {"op": "sort_range", "range": "A1:C2", "key": "A", "header": False},
            ]
            actions = editor.classify_ops(ops, default_sheet="Sheet1")
            self.assertEqual(actions[0].action, "tool.xlsx_set_value")
            self.assertEqual(actions[1].action, "tool.xlsx_add_formula")
            self.assertEqual(actions[2].action, "tool.xlsx_add_formula")
            self.assertEqual(actions[3].action, "tool.xlsx_filter")
            self.assertEqual(actions[4].action, "tool.xlsx_sort")

            result = editor.apply_ops(ops, default_sheet="Sheet1")
            editor.save()
            self.assertEqual(result.set_cells, 1)
            self.assertEqual(result.formula_cells, 3)

            updated = openpyxl.load_workbook(path)
            sheet = updated["Sheet1"]
            self.assertEqual(sheet["A1"].value, 2)
            self.assertEqual(sheet["B1"].value, "=A1+1")
            self.assertEqual(sheet["C1"].value, "=A1+1")
            self.assertEqual(sheet.auto_filter.ref, "A1:C2")
