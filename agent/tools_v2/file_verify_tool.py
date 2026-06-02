"""FileVerify: structural assertions against a file's content.

The point of this tool is to give the model a *machine-readable* answer
to "did my edit land correctly?". Browser-level Verify already exists
(see ``verify_tool.py``); ``FileVerify`` covers the bigger category of
"any text file with structure" — JSON, YAML, markdown with embedded
JSON blocks (Obsidian Excalidraw, Jupyter notebooks), etc.

Assertion types are intentionally narrow + composable. The model
should chain several small assertions rather than reach for arbitrary
code. ``python_predicate`` is available for the rare case the
predefined set is too narrow; it runs in-process and requires the
``Bash(sandbox=true)`` discipline to be applied by the caller for any
predicate they don't trust.

Design constraints:
  - No new third-party deps (no jsonpath-ng, no jmespath). Dotted path
    + integer indices + ``*`` array spread is enough for our cases.
  - Every assertion returns ``expected`` + ``actual`` so the model can
    diff and retry without re-running the tool.
  - File reads are size-bounded (32 MiB default) so a misdirected
    target can't OOM the agent.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from agent.core.loop import LoopContext, PermissionLevel, ToolResultBlock
from agent.tools_v2.primitives import _ToolBase, _resolve_guarded_path


_MAX_FILE_BYTES = 32 * 1024 * 1024


class FileVerifyTool(_ToolBase):
    name = "FileVerify"
    description = (
        "Run structural assertions against a file's content. Returns a "
        "structured pass/fail per assertion with expected vs actual. Use "
        "this immediately after Write/Edit so the model can verify its "
        "own edit landed correctly. Assertion types: file_exists, "
        "size_bytes, regex_match, regex_not_match, contains_text, "
        "extracted_block_parses, json_path_equals, json_path_exists, "
        "json_path_count_min, python_predicate."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "target": {"type": "string", "description": "File path to verify."},
            "encoding": {"type": "string", "default": "utf-8"},
            "assertions": {
                "type": "array",
                "description": "List of assertions; all must pass for ok=true.",
                "items": {
                    "type": "object",
                    "properties": {"type": {"type": "string"}},
                    "required": ["type"],
                },
            },
        },
        "required": ["target", "assertions"],
    }
    permission_level = PermissionLevel.SAFE
    parallel_safe = True

    async def run(self, input: dict, ctx: LoopContext) -> ToolResultBlock:
        try:
            target_raw = str(input["target"])
            assertions = input.get("assertions") or []
            if not isinstance(assertions, list):
                return self._err("assertions must be a list")

            path = _resolve_guarded_path(target_raw, ctx)
            encoding = str(input.get("encoding") or "utf-8")

            file_stat: dict[str, Any] = {"exists": path.exists()}
            text: str | None = None
            read_error: str | None = None

            if path.exists() and path.is_file():
                stat = path.stat()
                file_stat.update(
                    size_bytes=stat.st_size,
                    mtime=stat.st_mtime,
                )
                if stat.st_size > _MAX_FILE_BYTES:
                    read_error = (
                        f"file is {stat.st_size} bytes, exceeds "
                        f"{_MAX_FILE_BYTES}-byte read limit"
                    )
                else:
                    try:
                        text = path.read_text(encoding=encoding)
                    except UnicodeDecodeError as exc:
                        read_error = f"decode failed: {exc}"

            results: list[dict[str, Any]] = []
            for assertion in assertions:
                if not isinstance(assertion, dict):
                    results.append({
                        "type": "<invalid>", "ok": False,
                        "error": "assertion must be a dict",
                    })
                    continue
                results.append(
                    _run_assertion(assertion, path=path, text=text,
                                   file_stat=file_stat, read_error=read_error)
                )

            ok = all(r.get("ok") for r in results) and not read_error
            summary = {
                "total": len(results),
                "passed": sum(1 for r in results if r.get("ok")),
                "failed": sum(1 for r in results if not r.get("ok")),
                "failing": [r.get("type") for r in results if not r.get("ok")],
            }
            payload = {
                "ok": ok,
                "target": str(path),
                "file_stat": file_stat,
                "read_error": read_error,
                "assertions": results,
                "summary": summary,
            }
            return self._ok(json.dumps(payload, ensure_ascii=False, indent=2))
        except PermissionError as exc:
            return self._err(f"path not allowed: {exc}")
        except Exception as exc:
            return self._err(f"{type(exc).__name__}: {exc}")


def _run_assertion(
    assertion: dict,
    *,
    path: Path,
    text: str | None,
    file_stat: dict[str, Any],
    read_error: str | None,
) -> dict[str, Any]:
    kind = str(assertion.get("type") or "").lower()
    result: dict[str, Any] = {"type": kind, "ok": False}

    if kind == "file_exists":
        actual = bool(file_stat.get("exists"))
        result.update(ok=actual, actual=actual)
        return result

    if kind == "size_bytes":
        size = file_stat.get("size_bytes")
        if size is None:
            result.update(ok=False, error="file does not exist")
            return result
        lo = assertion.get("min")
        hi = assertion.get("max")
        ok = True
        if lo is not None and size < int(lo):
            ok = False
        if hi is not None and size > int(hi):
            ok = False
        result.update(ok=ok, actual=size, min=lo, max=hi)
        return result

    if read_error:
        result.update(ok=False, error=f"file not readable: {read_error}")
        return result
    if text is None:
        result.update(ok=False, error="file content unavailable")
        return result

    if kind == "regex_match":
        pattern = str(assertion["pattern"])
        flags = _parse_regex_flags(assertion.get("flags"))
        match = re.search(pattern, text, flags)
        result.update(
            ok=match is not None,
            pattern=pattern,
            actual_first_match=(match.group(0)[:200] if match else None),
        )
        return result

    if kind == "regex_not_match":
        pattern = str(assertion["pattern"])
        flags = _parse_regex_flags(assertion.get("flags"))
        match = re.search(pattern, text, flags)
        result.update(
            ok=match is None,
            pattern=pattern,
            actual_first_match=(match.group(0)[:200] if match else None),
        )
        return result

    if kind == "contains_text":
        needle = str(assertion["text"])
        ok = needle in text
        result.update(ok=ok, expected=needle[:200])
        return result

    if kind == "extracted_block_parses":
        block = _extract_block(text, assertion)
        if block is None:
            result.update(ok=False, error="block markers not found",
                          between=assertion.get("between"))
            return result
        parser = str(assertion.get("as") or "json").lower()
        try:
            _parse_block(block, parser)
            result.update(ok=True, parser=parser, length=len(block))
        except Exception as exc:
            result.update(ok=False, parser=parser,
                          error=f"{type(exc).__name__}: {exc}",
                          block_excerpt=block[:200])
        return result

    if kind in ("json_path_equals", "json_path_exists", "json_path_count_min"):
        data, perr = _load_json_for_path(text, assertion)
        if perr is not None:
            result.update(ok=False, error=perr)
            return result
        path_expr = str(assertion.get("path") or "")
        try:
            values = _resolve_path(data, path_expr)
        except Exception as exc:
            result.update(ok=False, error=f"path resolve failed: {exc}",
                          path=path_expr)
            return result

        if kind == "json_path_exists":
            ok = len(values) > 0
            result.update(ok=ok, path=path_expr,
                          actual_count=len(values),
                          actual_excerpt=_truncate_values(values))
            return result

        if kind == "json_path_count_min":
            min_count = int(assertion.get("min", 1))
            ok = len(values) >= min_count
            result.update(ok=ok, path=path_expr, min=min_count,
                          actual_count=len(values))
            return result

        # json_path_equals
        expected = assertion.get("value")
        if not values:
            result.update(ok=False, path=path_expr,
                          expected=expected, actual=None,
                          error="path matched no values")
            return result
        actual = values[0]
        result.update(ok=(actual == expected), path=path_expr,
                      expected=expected, actual=actual)
        return result

    if kind == "python_predicate":
        code = str(assertion.get("code") or "")
        if not code.strip():
            result.update(ok=False, error="empty predicate code")
            return result
        try:
            data, _perr = _load_json_for_path(text, assertion)
            ns: dict[str, Any] = {
                "text": text,
                "data": data,
                "path": str(path),
                "size_bytes": file_stat.get("size_bytes"),
            }
            predicate = eval(code, {"__builtins__": _safe_builtins()}, ns)
            value = predicate(ns["data"]) if callable(predicate) else predicate
            result.update(ok=bool(value), actual=bool(value))
        except Exception as exc:
            result.update(ok=False, error=f"{type(exc).__name__}: {exc}")
        return result

    result.update(ok=False, error=f"unknown assertion type: {kind!r}")
    return result


def _parse_regex_flags(spec: Any) -> int:
    if not spec:
        return 0
    s = str(spec)
    out = 0
    if "i" in s.lower(): out |= re.IGNORECASE
    if "m" in s.lower(): out |= re.MULTILINE
    if "s" in s.lower(): out |= re.DOTALL
    return out


def _extract_block(text: str, assertion: dict) -> str | None:
    between = assertion.get("between")
    if not isinstance(between, (list, tuple)) or len(between) != 2:
        return None
    open_marker, close_marker = str(between[0]), str(between[1])
    start = text.find(open_marker)
    if start < 0:
        return None
    inner = start + len(open_marker)
    end = text.find(close_marker, inner)
    if end < 0:
        return None
    return text[inner:end].strip()


def _parse_block(block: str, parser: str) -> Any:
    if parser == "json":
        return json.loads(block)
    if parser == "yaml":
        try:
            import yaml
        except ImportError as exc:
            raise RuntimeError("PyYAML not installed") from exc
        return yaml.safe_load(block)
    raise ValueError(f"unknown parser: {parser}")


def _load_json_for_path(text: str, assertion: dict) -> tuple[Any, str | None]:
    """Resolve the JSON document for json_path_* assertions.

    If ``between`` is provided, extract the block first; otherwise parse
    the whole file as JSON.
    """
    if "between" in assertion:
        block = _extract_block(text, assertion)
        if block is None:
            return None, "block markers not found"
        try:
            return json.loads(block), None
        except Exception as exc:
            return None, f"block JSON parse failed: {exc}"
    try:
        return json.loads(text), None
    except Exception as exc:
        return None, f"file JSON parse failed: {exc}"


def _resolve_path(data: Any, path_expr: str) -> list[Any]:
    """Tiny dotted-path resolver.

    Supports: ``a.b.c``, ``a.0.b`` (integer index), ``a.*.b`` (array
    spread), ``a.[*].b`` (alt array spread). Returns the list of
    matched values (empty list when no match).
    """
    if not path_expr:
        return [data]
    parts = [p for p in re.split(r"\.|\[|\]", path_expr) if p != ""]
    cursor: list[Any] = [data]
    for part in parts:
        next_cursor: list[Any] = []
        for node in cursor:
            if node is None:
                continue
            if part == "*":
                if isinstance(node, list):
                    next_cursor.extend(node)
                elif isinstance(node, dict):
                    next_cursor.extend(node.values())
            elif part.lstrip("-").isdigit() and isinstance(node, list):
                idx = int(part)
                if -len(node) <= idx < len(node):
                    next_cursor.append(node[idx])
            elif isinstance(node, dict):
                if part in node:
                    next_cursor.append(node[part])
        cursor = next_cursor
        if not cursor:
            return []
    return cursor


def _truncate_values(values: list[Any], limit: int = 3) -> list[Any]:
    out = []
    for v in values[:limit]:
        if isinstance(v, (dict, list)):
            out.append(type(v).__name__)
        else:
            s = str(v)
            out.append(s[:80] + ("…" if len(s) > 80 else ""))
    if len(values) > limit:
        out.append(f"…(+{len(values) - limit} more)")
    return out


def _safe_builtins() -> dict:
    return {
        "len": len, "range": range, "min": min, "max": max,
        "sum": sum, "any": any, "all": all,
        "isinstance": isinstance, "int": int, "float": float,
        "str": str, "list": list, "dict": dict, "tuple": tuple, "bool": bool,
    }
