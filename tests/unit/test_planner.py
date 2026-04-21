import unittest

from agent.planner import parse_plan_text, validate_docx_plan, validate_xlsx_plan


class TestPlanner(unittest.TestCase):
    def test_parse_docx_plan(self) -> None:
        text = """```yaml
file: sample.docx
ops:
  - op: append_paragraph
    text: hello
```"""
        plan = parse_plan_text(text)
        validate_docx_plan(plan)
        self.assertEqual(plan["file"], "sample.docx")

    def test_parse_xlsx_plan(self) -> None:
        text = """```yaml
file: sample.xlsx
sheet: Sheet1
ops:
  - op: set_cell
    cell: A1
    value: 1
```"""
        plan = parse_plan_text(text)
        validate_xlsx_plan(plan)
        self.assertEqual(plan["file"], "sample.xlsx")
