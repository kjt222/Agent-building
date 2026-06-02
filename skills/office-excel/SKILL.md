---
name: office-excel
description: Edit .xlsx Excel workbooks with scoped Read/Edit + render verification.
scope: office_excel
priority: 100
triggers:
  - "(?i)\\bexcel\\b|\\bxlsx\\b|\\bxlsm\\b|spreadsheet|workbook|worksheet|\\bsheet\\b|\\boffice\\b"
  - "表格|工作簿|工作表|电子表格|修改表格|表格格式"
tools_base:
  - Read
  - Glob
tools:
  - ExcelRead
  - ExcelEdit
  - RenderDocument
---

For Excel workbook edits, inspect the workbook with ExcelRead before ExcelEdit;
use explicit sheet/cell/range scopes and avoid global changes unless the user
explicitly requested them. After writing, use RenderDocument to confirm the
layout visually.
