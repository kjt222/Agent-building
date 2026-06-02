"""Excel-specific artifact manifest.

Two data sources:

- ``ExcelRuntimeEdit`` returns a rich ``structure_after`` produced by the
  COM backend (sheets, used_ranges, formulas, formula_errors, named
  ranges with validity, chart counts, AutoCalc mode). This is the
  preferred manifest source — post-mutation, fully populated.
- ``ExcelRead`` returns ``{sheets, active_sheet, inspected_sheets}`` where
  ``inspected_sheets[i]`` carries ``used_range`` and cell payloads with
  optional ``formula`` strings. This source is sparser; chart counts and
  named-range validity are unknown here.

Either source feeds the same dataclass; missing fields stay ``None``/
empty and ``to_compact_text`` renders only what is known.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent.core.artifact_context.types import ArtifactKind


# Excel AutoCalc mode constants (verified against xlCalculation* in the
# Excel typelib; hard-coded since we use late-binding everywhere).
_XL_CALCULATION_AUTOMATIC = -4105
_XL_CALCULATION_MANUAL = -4135
_XL_CALCULATION_SEMIAUTOMATIC = 2


def _calculation_mode_label(value: object) -> str | None:
    if value is None:
        return None
    try:
        ivalue = int(value)
    except (TypeError, ValueError):
        return None
    if ivalue == _XL_CALCULATION_AUTOMATIC:
        return "automatic"
    if ivalue == _XL_CALCULATION_MANUAL:
        return "manual"
    if ivalue == _XL_CALCULATION_SEMIAUTOMATIC:
        return "semiautomatic"
    return f"unknown({ivalue})"


@dataclass
class ExcelArtifactManifest:
    kind: ArtifactKind = ArtifactKind.EXCEL
    path: str = ""
    sheets: list[str] = field(default_factory=list)
    active_sheet: str | None = None
    used_ranges: dict[str, str] = field(default_factory=dict)
    formula_count: int | None = None
    formula_error_count: int = 0
    formula_errors: list[dict] = field(default_factory=list)
    named_ranges: list[dict] = field(default_factory=list)
    chart_counts: dict[str, int] = field(default_factory=dict)
    calculation_mode: str | None = None

    @classmethod
    def from_runtime_structure(
        cls, path: str | Path, structure: dict[str, Any]
    ) -> "ExcelArtifactManifest":
        formulas = structure.get("formulas") or []
        errors = structure.get("formula_errors") or []
        names_raw = structure.get("names") or []
        named_ranges: list[dict] = []
        for n in names_raw:
            if isinstance(n, dict) and n.get("name"):
                named_ranges.append(
                    {"name": str(n.get("name")), "valid": bool(n.get("valid"))}
                )
        chart_counts_raw = structure.get("chart_counts") or {}
        chart_counts = (
            {str(k): int(v) for k, v in chart_counts_raw.items()}
            if isinstance(chart_counts_raw, dict)
            else {}
        )
        used_ranges_raw = structure.get("used_ranges") or {}
        used_ranges = (
            {str(k): str(v) for k, v in used_ranges_raw.items()}
            if isinstance(used_ranges_raw, dict)
            else {}
        )
        return cls(
            path=str(path),
            sheets=[str(s) for s in (structure.get("sheets") or [])],
            active_sheet=(
                str(structure.get("active_sheet"))
                if structure.get("active_sheet")
                else None
            ),
            used_ranges=used_ranges,
            formula_count=len(formulas) if formulas else 0,
            formula_error_count=len(errors),
            formula_errors=[
                e for e in errors if isinstance(e, dict)
            ][:10],
            named_ranges=named_ranges,
            chart_counts=chart_counts,
            calculation_mode=_calculation_mode_label(structure.get("calculation_mode")),
        )

    @classmethod
    def from_read_result(
        cls, path: str | Path, read_result: dict[str, Any]
    ) -> "ExcelArtifactManifest":
        sheets = [str(s) for s in (read_result.get("sheets") or [])]
        active = read_result.get("active_sheet")
        inspected = read_result.get("inspected_sheets") or []

        used_ranges: dict[str, str] = {}
        formula_count = 0
        for sheet_payload in inspected:
            if not isinstance(sheet_payload, dict):
                continue
            name = str(sheet_payload.get("name") or "")
            if not name:
                continue
            used = sheet_payload.get("used_range")
            if used:
                used_ranges[name] = str(used)
            for cell in sheet_payload.get("cells") or []:
                if isinstance(cell, dict) and cell.get("formula"):
                    formula_count += 1

        return cls(
            path=str(path),
            sheets=sheets,
            active_sheet=str(active) if active else None,
            used_ranges=used_ranges,
            # ExcelRead does not inspect every cell, so this is a lower bound;
            # the compact text marks it accordingly.
            formula_count=formula_count if formula_count else None,
            named_ranges=[],
            chart_counts={},
            calculation_mode=None,
        )

    def to_dict(self) -> dict:
        return {
            "kind": self.kind.value,
            "path": self.path,
            "sheets": list(self.sheets),
            "active_sheet": self.active_sheet,
            "used_ranges": dict(self.used_ranges),
            "formula_count": self.formula_count,
            "formula_error_count": self.formula_error_count,
            "formula_errors": list(self.formula_errors),
            "named_ranges": list(self.named_ranges),
            "chart_counts": dict(self.chart_counts),
            "calculation_mode": self.calculation_mode,
        }

    def to_compact_text(self) -> str:
        lines: list[str] = []
        lines.append(f'<artifact kind="excel" path="{self.path}">')

        if self.sheets:
            preview = ", ".join(self.sheets[:6])
            more = "" if len(self.sheets) <= 6 else f" (+{len(self.sheets)-6} more)"
            active = f" (active={self.active_sheet})" if self.active_sheet else ""
            lines.append(f"  sheets ({len(self.sheets)}): {preview}{more}{active}")
        else:
            lines.append("  sheets: unknown")

        if self.used_ranges:
            range_preview = ", ".join(
                f"{name}={rng}" for name, rng in list(self.used_ranges.items())[:6]
            )
            lines.append(f"  used_ranges: {range_preview}")

        if self.formula_count is not None:
            err = (
                f", {self.formula_error_count} ERROR"
                if self.formula_error_count
                else ""
            )
            lines.append(f"  formulas: {self.formula_count}{err}")
        if self.formula_errors:
            for e in self.formula_errors[:3]:
                cell = e.get("cell") or "?"
                kind = e.get("error") or e.get("value") or "?"
                lines.append(f"    formula_error: {cell} -> {kind}")

        if self.named_ranges:
            invalid = [n["name"] for n in self.named_ranges if not n.get("valid")]
            valid_count = len(self.named_ranges) - len(invalid)
            if invalid:
                preview = ", ".join(invalid[:5])
                lines.append(
                    f"  named_ranges: {valid_count} valid, "
                    f"{len(invalid)} INVALID ({preview})"
                )
            else:
                lines.append(f"  named_ranges: {valid_count} valid")

        if self.chart_counts:
            total = sum(self.chart_counts.values())
            if total:
                detail = ", ".join(
                    f"{sheet}={count}" for sheet, count in self.chart_counts.items() if count
                )
                lines.append(f"  charts: {total} ({detail})")

        if self.calculation_mode and self.calculation_mode != "automatic":
            lines.append(f"  calc_mode: {self.calculation_mode}")

        lines.append("</artifact>")
        return "\n".join(lines)
