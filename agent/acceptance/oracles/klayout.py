"""KLayout L2 oracle skeleton (P14.2.4).

Loads a .gds (or .oas) via `klayout.db` if available, reports cell count,
layer set, instance count, and bbox. DRC is a stub — the policy engine
expects an oracle name but real DRC needs a script + the klayout binary.

When neither `klayout.db` nor `gdspy` is importable, the oracle returns
`unknown` with an explicit install hint. Soft-fail by design — a missing
binary should not crash the long-running loop; it should escalate to user
with a clear "install klayout-package or gdspy" finding.

Optional `task_spec["acceptance"]["klayout"]`:
    {
      "expected_layers": [1, 2, 5],     # required layer numbers
      "min_cells": 1,
      "max_cells": 10_000,
      "min_bbox_um": [1.0, 1.0],
      "max_bbox_um": [10_000.0, 10_000.0],
    }
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from ..oracle import OracleReport, register_oracle


def _try_klayout_db():
    try:
        import klayout.db as kdb  # type: ignore
        return kdb
    except Exception:
        return None


def _try_gdspy():
    try:
        import gdspy  # type: ignore
        return gdspy
    except Exception:
        return None


def _probe_with_kdb(p: Path) -> tuple[bool, str, dict]:
    kdb = _try_klayout_db()
    if kdb is None:
        return False, "klayout.db not importable", {}
    try:
        layout = kdb.Layout()
        layout.read(str(p))
    except Exception as exc:
        return False, f"klayout parse failed: {exc}", {}
    cells = list(layout.each_cell())
    layers = sorted({layout.get_info(li).layer for li in layout.layer_indexes()})
    top = layout.top_cells()
    bbox = top[0].bbox() if top else None
    dbu = layout.dbu  # microns per dbu
    return True, "", {
        "backend": "klayout.db",
        "cell_count": len(cells),
        "layer_numbers": layers,
        "top_cell": top[0].name if top else None,
        "bbox_um": (
            [bbox.width() * dbu, bbox.height() * dbu] if bbox else None
        ),
        "dbu": dbu,
    }


def _probe_with_gdspy(p: Path) -> tuple[bool, str, dict]:
    gdspy = _try_gdspy()
    if gdspy is None:
        return False, "gdspy not importable", {}
    try:
        lib = gdspy.GdsLibrary(infile=str(p))
    except Exception as exc:
        return False, f"gdspy parse failed: {exc}", {}
    cells = list(lib.cells.values())
    layers = sorted({
        layer for c in cells for layer in {p.layer for p in c.polygons}
    })
    return True, "", {
        "backend": "gdspy",
        "cell_count": len(cells),
        "layer_numbers": layers,
        "top_cell": next(iter(lib.cells), None),
    }


def _validate_against_spec(info: dict, spec: dict | None, findings: list[str]) -> bool:
    if not spec:
        return True
    ok = True
    exp_layers = spec.get("expected_layers")
    if exp_layers is not None:
        actual = set(info.get("layer_numbers") or [])
        missing = sorted(set(exp_layers) - actual)
        if missing:
            findings.append(f"missing expected layers: {missing}")
            ok = False
    min_cells = spec.get("min_cells")
    if isinstance(min_cells, int) and info.get("cell_count", 0) < min_cells:
        findings.append(
            f"cell_count {info.get('cell_count')} < min_cells {min_cells}"
        )
        ok = False
    max_cells = spec.get("max_cells")
    if isinstance(max_cells, int) and info.get("cell_count", 0) > max_cells:
        findings.append(
            f"cell_count {info.get('cell_count')} > max_cells {max_cells}"
        )
        ok = False
    return ok


class KLayoutOracle:
    name = "klayout"

    def check(
        self,
        artifact_paths: Iterable[Path],
        task_spec: dict[str, Any] | None = None,
    ) -> OracleReport:
        paths = [Path(p) for p in artifact_paths if Path(p).suffix.lower()
                 in (".gds", ".gds2", ".oas")]
        if not paths:
            return OracleReport(
                oracle=self.name,
                verdict="unknown",
                findings=["no .gds/.oas artifacts in input"],
            )

        if _try_klayout_db() is None and _try_gdspy() is None:
            return OracleReport(
                oracle=self.name,
                verdict="unknown",
                findings=[
                    "neither klayout.db nor gdspy importable",
                    "install one: `.venv/Scripts/pip install klayout` (full) "
                    "or `gdspy`. KLayout DRC needs the standalone binary.",
                ],
            )

        per_file: dict[str, dict] = {}
        findings: list[str] = []
        any_fail = False
        spec_check = (task_spec or {}).get("acceptance", {}).get("klayout")

        for p in paths:
            if not p.exists():
                per_file[str(p)] = {"missing": True}
                findings.append(f"[{p.name}] missing")
                any_fail = True
                continue
            ok, err, info = _probe_with_kdb(p)
            if not ok:
                ok, err, info = _probe_with_gdspy(p)
            per_file[str(p)] = info
            if not ok:
                any_fail = True
                findings.append(f"[{p.name}] {err}")
                continue
            if not _validate_against_spec(info, spec_check, findings):
                any_fail = True

        verdict = "fail" if any_fail else "pass"
        return OracleReport(
            oracle=self.name, verdict=verdict, findings=findings,
            evidence={"files": per_file},
        )


register_oracle("klayout", KLayoutOracle())
