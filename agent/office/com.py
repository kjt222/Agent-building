from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional


class OfficeComError(RuntimeError):
    pass


def _load_win32():
    if not sys.platform.startswith("win"):
        raise OfficeComError("COM automation is only available on Windows")
    try:
        import win32com.client  # type: ignore
    except ImportError as exc:
        raise OfficeComError("pywin32 not installed. Install `pywin32` on Windows.") from exc
    return win32com.client


def _dispatch(progids: Iterable[str]):
    win32 = _load_win32()
    last_exc: Optional[Exception] = None
    for progid in progids:
        for method in ("DispatchEx", "Dispatch"):
            dispatch = getattr(win32, method, None)
            if dispatch is None:
                continue
            try:
                return dispatch(progid), progid
            except Exception as exc:  # pragma: no cover - depends on COM setup
                last_exc = exc
    raise OfficeComError(f"Failed to dispatch COM object. Last error: {last_exc}")


def _set_app_flags(app, visible: bool, display_alerts: bool) -> None:
    if hasattr(app, "Visible"):
        app.Visible = bool(visible)
    if hasattr(app, "DisplayAlerts"):
        app.DisplayAlerts = bool(display_alerts)
    if hasattr(app, "ScreenUpdating"):
        app.ScreenUpdating = False


def _resolve_progids(config: dict, app_name: str) -> list[str]:
    prefer = str(config.get("prefer", "wps")).lower()
    apps = config.get("apps", {})
    app_cfg = apps.get(app_name, {})
    wps = list(app_cfg.get("wps_progids", []) or [])
    office = list(app_cfg.get("office_progids", []) or [])
    if prefer == "office":
        return office + wps
    return wps + office


@dataclass
class WordComResult:
    replacements: int = 0
    appended: int = 0
    headings: int = 0


@dataclass
class ExcelComResult:
    set_cells: int = 0
    formula_cells: int = 0
    inserted_columns: int = 0
    deleted_columns: int = 0
    inserted_rows: int = 0
    deleted_rows: int = 0
    filters_set: int = 0
    sorted_ranges: int = 0


class WordComEditor:
    def __init__(self, path: Path, config: dict) -> None:
        progids = _resolve_progids(config, "word")
        self.app, _ = _dispatch(progids)
        self.config = config
        _set_app_flags(
            self.app,
            visible=bool(config.get("visible", False)),
            display_alerts=bool(config.get("display_alerts", False)),
        )
        self.path = path
        self.doc = self.app.Documents.Open(str(path))

    def close(self, save: bool = True) -> None:
        save_flag = -1 if save else 0
        if self.doc is not None:
            self.doc.Close(SaveChanges=save_flag)
        if self.app is not None:
            self.app.Quit()

    def _replace_text(self, old: str, new: str, count: Optional[int]) -> int:
        finder = self.doc.Content.Find
        finder.ClearFormatting()
        finder.Replacement.ClearFormatting()
        finder.Text = old
        finder.Replacement.Text = new
        finder.Forward = True
        finder.Wrap = 0  # stop at end
        replaced = 0
        limit = count if count is not None and count >= 0 else None
        while True:
            if limit is not None and replaced >= limit:
                break
            found = finder.Execute(Replace=1)
            if not found:
                break
            replaced += 1
        return replaced

    def _append_paragraph(self, text: str) -> None:
        paragraph = self.doc.Content.Paragraphs.Add()
        paragraph.Range.Text = text
        paragraph.Range.InsertParagraphAfter()

    def _add_heading(self, text: str, level: int) -> None:
        paragraph = self.doc.Content.Paragraphs.Add()
        paragraph.Range.Text = text
        style_templates = (
            self.config.get("apps", {}).get("word", {}).get("heading_styles") or ["Heading {level}"]
        )
        for template in style_templates:
            try:
                paragraph.Range.Style = str(template).format(level=level)
                break
            except Exception:
                continue
        paragraph.Range.InsertParagraphAfter()

    def apply_ops(self, ops: list[dict]) -> WordComResult:
        result = WordComResult()
        for op in ops:
            op_type = op.get("op")
            if op_type == "replace_text":
                old = str(op.get("old", ""))
                new = str(op.get("new", ""))
                count = op.get("count")
                if count is not None:
                    count = int(count)
                result.replacements += self._replace_text(old, new, count)
            elif op_type == "append_paragraph":
                self._append_paragraph(str(op.get("text", "")))
                result.appended += 1
            elif op_type == "add_heading":
                level = int(op.get("level", 1))
                self._add_heading(str(op.get("text", "")), level)
                result.headings += 1
            else:
                raise ValueError(f"Unsupported Word op: {op_type}")
        return result


class ExcelComEditor:
    def __init__(self, path: Path, config: dict) -> None:
        progids = _resolve_progids(config, "excel")
        self.app, _ = _dispatch(progids)
        _set_app_flags(
            self.app,
            visible=bool(config.get("visible", False)),
            display_alerts=bool(config.get("display_alerts", False)),
        )
        self.path = path
        self.workbook = self.app.Workbooks.Open(str(path))

    def close(self, save: bool = True) -> None:
        save_flag = -1 if save else 0
        if self.workbook is not None:
            self.workbook.Close(SaveChanges=save_flag)
        if self.app is not None:
            self.app.Quit()

    def _sheet(self, sheet_name: Optional[str]):
        if sheet_name:
            return self.workbook.Sheets(sheet_name)
        return self.workbook.ActiveSheet

    def apply_ops(self, ops: list[dict], default_sheet: Optional[str] = None) -> ExcelComResult:
        result = ExcelComResult()
        for op in ops:
            op_type = op.get("op")
            sheet = self._sheet(op.get("sheet") or default_sheet)
            if op_type == "set_cell":
                cell_ref = op.get("cell")
                if not cell_ref:
                    raise ValueError("set_cell requires cell")
                if "formula" in op:
                    sheet.Range(cell_ref).Formula = op.get("formula")
                    result.formula_cells += 1
                else:
                    sheet.Range(cell_ref).Value = op.get("value")
                    result.set_cells += 1
            elif op_type == "fill_formula":
                ref = op.get("range")
                template = op.get("formula")
                if not ref or template is None:
                    raise ValueError("fill_formula requires range/formula")
                cells = sheet.Range(ref)
                for row in range(1, cells.Rows.Count + 1):
                    for col in range(1, cells.Columns.Count + 1):
                        target = cells.Cells(row, col)
                        address = target.Address(False, False)
                        row_num = target.Row
                        col_letter = address.rstrip("0123456789")
                        formula = str(template).format(row=row_num, col=col_letter)
                        target.Formula = formula if str(formula).startswith("=") else f"={formula}"
                        result.formula_cells += 1
            elif op_type == "insert_columns":
                index = int(op.get("index", 1))
                amount = int(op.get("amount", 1))
                for _ in range(amount):
                    sheet.Columns(index).Insert()
                    result.inserted_columns += 1
            elif op_type == "delete_columns":
                index = int(op.get("index", 1))
                amount = int(op.get("amount", 1))
                for _ in range(amount):
                    sheet.Columns(index).Delete()
                    result.deleted_columns += 1
            elif op_type == "insert_rows":
                index = int(op.get("index", 1))
                amount = int(op.get("amount", 1))
                for _ in range(amount):
                    sheet.Rows(index).Insert()
                    result.inserted_rows += 1
            elif op_type == "delete_rows":
                index = int(op.get("index", 1))
                amount = int(op.get("amount", 1))
                for _ in range(amount):
                    sheet.Rows(index).Delete()
                    result.deleted_rows += 1
            elif op_type == "set_auto_filter":
                ref = op.get("range")
                if not ref:
                    raise ValueError("set_auto_filter requires range")
                sheet.Range(ref).AutoFilter()
                result.filters_set += 1
            elif op_type == "sort_range":
                ref = op.get("range")
                if not ref:
                    raise ValueError("sort_range requires range")
                key = op.get("key")
                header = bool(op.get("header", False))
                ascending = bool(op.get("ascending", True))
                rng = sheet.Range(ref)
                if key is None:
                    key_ref = rng.Columns(1)
                else:
                    if isinstance(key, str):
                        start_row = rng.Row
                        key_ref = sheet.Range(f"{key}{start_row}")
                    else:
                        key_ref = rng.Columns(int(key))
                rng.Sort(
                    Key1=key_ref,
                    Order1=1 if ascending else 2,
                    Header=1 if header else 2,
                )
                result.sorted_ranges += 1
            elif op_type == "pivot":
                raise NotImplementedError("pivot is not supported via COM")
            else:
                raise ValueError(f"Unsupported Excel op: {op_type}")
        return result
