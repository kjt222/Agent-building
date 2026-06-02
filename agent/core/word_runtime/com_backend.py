"""Win32 COM backend for the Word runtime.

Late-binding only: Office 2007's typelib breaks ``win32com.client.gencache``,
so we never use ``EnsureDispatch`` and never rely on ``win32com.client.constants``.
All Word constants are hard-coded.
"""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path
from typing import Any

from agent.core.word_runtime.types import (
    AnchorMode,
    BackendUnavailable,
    FileLockedByOther,
    HeadingInfo,
    NotConnected,
    OpKind,
    UnknownAnchor,
    WordRuntimeError,
    WordRuntimeOp,
    WordRuntimeRequest,
    WordRuntimeResult,
    WordStructure,
)


# Word enum constants (verified against Office 2007 / Word 12.0 typelib).
WD_FORMAT_XML_DOCUMENT = 12          # WdSaveFormat.wdFormatXMLDocument (.docx)
WD_FORMAT_DOCUMENT_DEFAULT = 16      # WdSaveFormat.wdFormatDocumentDefault
WD_OUTLINE_LEVEL_BODY_TEXT = 10      # WdOutlineLevel.wdOutlineLevelBodyText
WD_DO_NOT_SAVE_CHANGES = 0           # WdSaveOptions.wdDoNotSaveChanges
WD_FIELD_TOC = 13                    # WdFieldType.wdFieldTOC


def _import_win32():
    try:
        import pythoncom  # noqa: F401
        import win32com.client as win32_client
        import win32com.client.dynamic as win32_dynamic
        import pywintypes
    except ImportError as exc:  # pragma: no cover - covered by skip in tests
        raise BackendUnavailable(f"pywin32 not available: {exc}") from exc
    return win32_client, win32_dynamic, pywintypes


def _short_path(path: Path) -> str:
    """Return a short 8.3 path on Windows when the path contains non-ASCII.

    Some win32com installs choke on CJK paths via Documents.Open. Short paths
    sidestep that without dropping precision (the file content is unchanged).
    """
    raw = str(path)
    if os.name != "nt":
        return raw
    try:
        ascii_only = raw.encode("ascii")
        del ascii_only
        return raw
    except UnicodeEncodeError:
        try:
            from ctypes import create_unicode_buffer, windll
            buf = create_unicode_buffer(260)
            n = windll.kernel32.GetShortPathNameW(raw, buf, 260)
            if n and n < 260:
                return buf.value
        except Exception:
            pass
        return raw


def _check_file_lock(path: Path) -> None:
    """Best-effort check that ``path`` is not locked by another writer."""
    if not path.exists():
        return
    sibling = path.with_name(f".{path.name}.lockprobe")
    try:
        os.rename(str(path), str(sibling))
        os.rename(str(sibling), str(path))
    except PermissionError as exc:
        raise FileLockedByOther(
            f"file is locked by another process (probably open in Word): {path}"
        ) from exc
    except OSError:
        return


class ComWordBackend:
    """Single Word.Application instance, reused across ops in one session."""

    name = "com_word"

    def __init__(self) -> None:
        self._win32_client = None
        self._win32_dynamic = None
        self._pywintypes = None
        self._app: Any = None
        self._pythoncom_initialized = False

    # ------------------------------------------------------------------ lifecycle

    def connect(self) -> None:
        if self._app is not None:
            return
        self._win32_client, self._win32_dynamic, self._pywintypes = _import_win32()
        import pythoncom

        try:
            pythoncom.CoInitialize()
            self._pythoncom_initialized = True
        except Exception:
            self._pythoncom_initialized = False
        # DispatchEx forces CoCreateInstance → a fresh isolated process. Plain
        # Dispatch would attach to a running Word/WPS the user already has
        # open, and shutdown() would close their document along with ours.
        # Office 2007's typelib breaks gencache, so prefer the dynamic
        # late-binding variant; never fall back to plain Dispatch.
        try:
            try:
                self._app = self._win32_client.DispatchEx("Word.Application")
            except Exception:
                self._app = self._win32_dynamic.DispatchEx("Word.Application")
        except Exception as exc:
            raise BackendUnavailable(f"Word.Application not available: {exc}") from exc
        try:
            self._app.Visible = False
            self._app.DisplayAlerts = False
            self._app.ScreenUpdating = False
        except Exception:
            pass

    def shutdown(self) -> None:
        app = self._app
        self._app = None
        if app is not None:
            try:
                app.Quit(SaveChanges=WD_DO_NOT_SAVE_CHANGES)
            except Exception:
                pass
        # Release the COM proxy *before* CoUninitialize. If the proxy is still
        # alive when the COM apartment is torn down, its Release() call lands
        # in a dead thread and WINWORD.EXE never receives the last decref,
        # leaking the process. Forcing app=None here triggers the decref now.
        app = None
        if self._pythoncom_initialized:
            try:
                import pythoncom
                pythoncom.CoUninitialize()
            except Exception:
                pass
            self._pythoncom_initialized = False

    def is_alive(self) -> bool:
        if self._app is None:
            return False
        try:
            _ = self._app.Version
            return True
        except Exception:
            self._app = None
            return False

    # ------------------------------------------------------------------ public

    def get_structure(self, path: Path) -> WordStructure:
        if self._app is None:
            raise NotConnected("ComWordBackend.get_structure called before connect()")
        doc = self._open(path, read_only=True)
        try:
            return self._snapshot_structure(doc)
        finally:
            self._close(doc, save=False)

    def apply(self, request: WordRuntimeRequest) -> WordRuntimeResult:
        if self._app is None:
            raise NotConnected("ComWordBackend.apply called before connect()")

        path = Path(request.path).expanduser().resolve()
        if not path.exists():
            raise WordRuntimeError(f"file not found: {path}")
        _check_file_lock(path)

        backup_path: Path | None = None
        if request.save:
            backup_path = self._make_backup(path, request.backup_dir, request.keep_backups)

        doc = self._open(path, read_only=False)
        try:
            structure_before = self._snapshot_structure(doc).to_dict()
            notes: list[str] = []
            applied = 0
            for op in request.ops:
                op.validate_for_anchor()
                self._dispatch_op(doc, op, notes)
                applied += 1
            if request.refresh_fields_on_save:
                self._refresh_all_fields(doc)
            structure_after = self._snapshot_structure(doc).to_dict()
            if request.save:
                self._save(doc, path)
        finally:
            self._close(doc, save=False)

        return WordRuntimeResult(
            path=str(path),
            backup_path=str(backup_path) if backup_path else None,
            ops_applied=applied,
            structure_before=structure_before,
            structure_after=structure_after,
            notes=notes,
        )

    # ------------------------------------------------------------------ helpers

    def _open(self, path: Path, *, read_only: bool):
        if self._app is None:
            raise NotConnected("ComWordBackend not connected")
        opener = self._app.Documents
        try:
            return opener.Open(_short_path(path), ReadOnly=read_only, AddToRecentFiles=False)
        except self._pywintypes.com_error as exc:
            text = str(exc).lower()
            if "in use" in text or "locked" in text or "another user" in text:
                raise FileLockedByOther(f"document is locked: {path}") from exc
            raise WordRuntimeError(f"failed to open {path}: {exc}") from exc

    def _close(self, doc, *, save: bool) -> None:
        if doc is None:
            return
        try:
            doc.Close(SaveChanges=WD_DO_NOT_SAVE_CHANGES if not save else -1)
        except Exception:
            pass

    def _save(self, doc, path: Path) -> None:
        target = _short_path(path)
        suffix = path.suffix.lower()
        fmt = WD_FORMAT_XML_DOCUMENT if suffix == ".docx" else WD_FORMAT_DOCUMENT_DEFAULT
        try:
            doc.SaveAs(FileName=target, FileFormat=fmt, AddToRecentFiles=False)
        except self._pywintypes.com_error as exc:
            raise WordRuntimeError(f"failed to save {path}: {exc}") from exc

    def _make_backup(
        self,
        path: Path,
        backup_dir: Path | None,
        keep: int,
    ) -> Path | None:
        target_dir = (backup_dir or (path.parent / ".bak")).expanduser()
        target_dir.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%d%H%M%S")
        backup = target_dir / f"{path.stem}-{ts}{path.suffix}"
        shutil.copy2(path, backup)
        self._gc_backups(target_dir, path.stem, path.suffix, keep)
        return backup

    @staticmethod
    def _gc_backups(target_dir: Path, stem: str, suffix: str, keep: int) -> None:
        keep = max(1, int(keep))
        candidates = sorted(
            (p for p in target_dir.glob(f"{stem}-*{suffix}") if p.is_file()),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for stale in candidates[keep:]:
            try:
                stale.unlink()
            except OSError:
                pass

    # ------------------------------------------------------------------ ops

    def _dispatch_op(self, doc, op: WordRuntimeOp, notes: list[str]) -> None:
        if op.op == OpKind.REPLACE_IN_HEADING:
            self._op_replace_in_heading(doc, op)
        elif op.op == OpKind.INSERT_PARAGRAPH_AFTER_HEADING:
            self._op_insert_paragraph_after_heading(doc, op)
        elif op.op == OpKind.SET_HEADING_TEXT:
            self._op_set_heading_text(doc, op)
        elif op.op == OpKind.REFRESH_FIELDS:
            self._refresh_all_fields(doc)
            notes.append("refresh_fields applied")
        elif op.op == OpKind.ADD_TOC:
            self._op_add_toc(doc, op)
        elif op.op == OpKind.SAVE_NORMALIZED:
            notes.append("save_normalized handled at request level")
        elif op.op == OpKind.GET_STRUCTURE:
            notes.append("get_structure is implicit in result.structure_after")
        else:
            raise WordRuntimeError(f"unknown op: {op.op}")

    def _find_heading_paragraph(self, doc, anchor_text: str):
        """Return the first paragraph whose text matches anchor_text and is a heading."""
        target = anchor_text.strip()
        for paragraph in doc.Paragraphs:
            text = (paragraph.Range.Text or "").rstrip("\r\x07")
            if text.strip() != target:
                continue
            try:
                if 1 <= paragraph.OutlineLevel <= 9:
                    return paragraph
            except Exception:
                continue
        raise UnknownAnchor(f"no heading paragraph matches: {anchor_text!r}")

    def _heading_body_range(self, doc, heading):
        """Return (start, end) char positions for the heading body
        (paragraphs after heading until the next heading at same or lower level)."""
        anchor_level = int(heading.OutlineLevel)
        body_start = heading.Range.End
        body_end = body_start
        next_after = False
        for paragraph in doc.Paragraphs:
            if not next_after:
                if paragraph.Range.Start == heading.Range.Start:
                    next_after = True
                continue
            try:
                lvl = int(paragraph.OutlineLevel)
            except Exception:
                lvl = WD_OUTLINE_LEVEL_BODY_TEXT
            if 1 <= lvl <= anchor_level:
                break
            body_end = paragraph.Range.End
        return body_start, body_end

    def _op_replace_in_heading(self, doc, op: WordRuntimeOp) -> None:
        heading = self._find_heading_paragraph(doc, op.anchor or "")
        body_start, body_end = self._heading_body_range(doc, heading)
        if body_end <= body_start:
            insertion = doc.Range(body_start, body_start)
            insertion.InsertParagraphAfter()
            insertion = doc.Range(body_start, body_start + 1)
            insertion.Text = (op.new_text or "")
            try:
                insertion.Style = doc.Styles("Normal")
            except Exception:
                pass
            return
        body = doc.Range(body_start, body_end)
        body.Text = (op.new_text or "") + "\r"
        try:
            body.Style = doc.Styles(op.style or "Normal")
        except Exception:
            pass

    def _op_insert_paragraph_after_heading(self, doc, op: WordRuntimeOp) -> None:
        heading = self._find_heading_paragraph(doc, op.anchor or "")
        rng = doc.Range(heading.Range.End, heading.Range.End)
        rng.InsertParagraphBefore()
        rng = doc.Range(heading.Range.End, heading.Range.End)
        rng.Text = (op.new_text or "")
        try:
            rng.Style = doc.Styles(op.style or "Normal")
        except Exception:
            pass

    def _op_set_heading_text(self, doc, op: WordRuntimeOp) -> None:
        heading = self._find_heading_paragraph(doc, op.anchor or "")
        rng = heading.Range
        end_excluding_pilcrow = rng.End - 1
        rng.SetRange(rng.Start, end_excluding_pilcrow)
        rng.Text = (op.new_text or "")
        if op.level and 1 <= int(op.level) <= 9:
            heading.OutlineLevel = int(op.level)

    def _op_add_toc(self, doc, op: WordRuntimeOp) -> None:
        upper, lower = self._parse_toc_levels(op.levels)
        anchor_text = op.anchor or ""
        if anchor_text:
            heading = self._find_heading_paragraph(doc, anchor_text)
            insertion = doc.Range(heading.Range.End, heading.Range.End)
        else:
            insertion = doc.Range(0, 0)
        if op.title:
            insertion.InsertParagraphBefore()
            insertion = doc.Range(insertion.Start, insertion.Start)
            insertion.Text = op.title + "\r"
            try:
                insertion.Style = doc.Styles("Heading 1")
            except Exception:
                pass
            insertion = doc.Range(insertion.End, insertion.End)
        doc.TablesOfContents.Add(
            insertion,
            UseHeadingStyles=True,
            UpperHeadingLevel=upper,
            LowerHeadingLevel=lower,
        )

    @staticmethod
    def _parse_toc_levels(levels: str | None) -> tuple[int, int]:
        if not levels:
            return 1, 3
        parts = str(levels).replace(" ", "").split("-", 1)
        try:
            if len(parts) == 1:
                upper = lower = max(1, min(9, int(parts[0])))
                return upper, lower
            upper = max(1, min(9, int(parts[0])))
            lower = max(upper, min(9, int(parts[1])))
            return upper, lower
        except ValueError:
            return 1, 3

    def _refresh_all_fields(self, doc) -> None:
        try:
            for toc in doc.TablesOfContents:
                toc.Update()
        except Exception:
            pass
        try:
            doc.Fields.Update()
        except Exception:
            pass
        try:
            doc.Repaginate()
        except Exception:
            pass

    # ------------------------------------------------------------------ snapshot

    def _snapshot_structure(self, doc) -> WordStructure:
        headings: list[HeadingInfo] = []
        for idx, paragraph in enumerate(doc.Paragraphs):
            try:
                level = int(paragraph.OutlineLevel)
            except Exception:
                continue
            if not (1 <= level <= 9):
                continue
            text = (paragraph.Range.Text or "").rstrip("\r\x07").strip()
            headings.append(HeadingInfo(text=text, level=level, paragraph_index=idx))
        toc_entries: list[dict[str, Any]] = []
        has_toc_field = False
        try:
            for toc in doc.TablesOfContents:
                has_toc_field = True
                rng_text = (toc.Range.Text or "").splitlines()
                for line in rng_text:
                    line = line.rstrip().lstrip()
                    if not line:
                        continue
                    toc_entries.append({"line": line})
        except Exception:
            pass
        field_codes: list[str] = []
        try:
            for fld in doc.Fields:
                code = (fld.Code.Text or "").strip()
                if code:
                    field_codes.append(code)
        except Exception:
            pass
        try:
            track = bool(doc.TrackRevisions)
        except Exception:
            track = False
        try:
            rev_count = int(doc.Revisions.Count)
        except Exception:
            rev_count = 0
        try:
            page_count = int(doc.ComputeStatistics(2))  # wdStatisticPages = 2
        except Exception:
            page_count = None
        try:
            paragraph_count = int(doc.Paragraphs.Count)
        except Exception:
            paragraph_count = 0
        return WordStructure(
            headings=headings,
            toc_entries=toc_entries,
            has_toc_field=has_toc_field,
            field_codes=field_codes,
            has_track_changes=track,
            revision_count=rev_count,
            page_count=page_count,
            paragraph_count=paragraph_count,
        )


def make_default_com_backend_factory():
    """Return a zero-arg factory; used by SessionManager.configure_factory."""

    def _factory():
        return ComWordBackend()

    return _factory
