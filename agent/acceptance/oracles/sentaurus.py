"""Sentaurus sProcess L2 oracle skeleton (P14.2.5).

`.tdr` is a proprietary binary format read by Tecplot / SVisual / Inspect —
no free Python parser. We instead inspect the text-mode siblings Sentaurus
writes alongside `.tdr`:

  - `.cmd`       — command script the run executed
  - `.log` / `.out` — run log (look for `convergence failed`, fatal errors,
    final converged time-step)
  - `.plt` / `.csv` — exported curves (optional, parseable with pandas)

Returns:
  - `pass` if a log file is present and contains a convergence marker and
    no fatal error markers
  - `fail` if any fatal marker is found
  - `unknown` if no .log/.out file is present at all (oracle can't
    interrogate a `.tdr` directly)

Optional `task_spec["acceptance"]["sentaurus"]`:
    {
      "must_contain_keywords": ["converged"],
      "must_not_contain_keywords": ["FATAL", "convergence failed"],
      "max_runtime_s": 86400,
    }
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable

from ..oracle import OracleReport, register_oracle


# Conservative defaults — covers the common Sentaurus log dialects.
_DEFAULT_MUST_NOT_CONTAIN = [
    "FATAL ERROR",
    "convergence failed",
    "could not converge",
    "Newton iteration did not converge",
    "*** Error ***",
]
_DEFAULT_MUST_CONTAIN = [
    "converged",
    "Final solution",
    "Simulation completed",
]

_RUNTIME_RE = re.compile(
    r"(?:elapsed|total)\s+(?:time|cpu)\s*[:=]?\s*([0-9.]+)\s*(s|sec|seconds|min|hours?)",
    re.IGNORECASE,
)


def _scan_log(path: Path, must_contain: list[str], must_not_contain: list[str],
              max_runtime_s: float | None) -> tuple[bool, list[str], dict]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return False, [f"could not read log: {exc}"], {}
    findings: list[str] = []
    evidence: dict[str, Any] = {"path": str(path), "size_bytes": len(text)}

    hits_negative = [k for k in must_not_contain if k.lower() in text.lower()]
    if hits_negative:
        findings.append(f"fatal markers present: {hits_negative}")
    evidence["negative_hits"] = hits_negative

    hits_positive = [k for k in must_contain if k.lower() in text.lower()]
    if not hits_positive:
        findings.append(
            f"none of must-contain markers {must_contain} found in log"
        )
    evidence["positive_hits"] = hits_positive

    if max_runtime_s is not None:
        m = _RUNTIME_RE.search(text)
        if m:
            v = float(m.group(1))
            unit = m.group(2).lower()
            seconds = v * {"s": 1, "sec": 1, "seconds": 1,
                           "min": 60, "hour": 3600, "hours": 3600}[unit]
            evidence["runtime_seconds"] = seconds
            if seconds > max_runtime_s:
                findings.append(
                    f"runtime {seconds:.0f}s exceeds budget {max_runtime_s:.0f}s"
                )

    ok = (not hits_negative) and bool(hits_positive) and not any(
        "runtime" in f for f in findings
    )
    return ok, findings, evidence


class SentaurusOracle:
    name = "sentaurus"

    def check(
        self,
        artifact_paths: Iterable[Path],
        task_spec: dict[str, Any] | None = None,
    ) -> OracleReport:
        paths = [Path(p) for p in artifact_paths]
        spec = (task_spec or {}).get("acceptance", {}).get("sentaurus") or {}
        must_contain = spec.get("must_contain_keywords") or _DEFAULT_MUST_CONTAIN
        must_not_contain = (
            spec.get("must_not_contain_keywords") or _DEFAULT_MUST_NOT_CONTAIN
        )
        max_runtime_s = spec.get("max_runtime_s")

        log_paths = [
            p for p in paths
            if p.exists() and p.suffix.lower() in (".log", ".out", ".txt")
        ]
        if not log_paths:
            return OracleReport(
                oracle=self.name,
                verdict="unknown",
                findings=[
                    "no .log/.out/.txt log paths in input — oracle cannot "
                    "interrogate .tdr binaries directly. Pass a Sentaurus "
                    "log file alongside the .tdr to enable convergence "
                    "checks.",
                ],
                evidence={"input_paths": [str(p) for p in paths]},
            )

        per_file: dict[str, dict] = {}
        findings: list[str] = []
        any_fail = False
        for p in log_paths:
            ok, file_findings, ev = _scan_log(
                p, must_contain, must_not_contain, max_runtime_s
            )
            per_file[str(p)] = ev
            if not ok:
                any_fail = True
            if file_findings:
                findings.append(f"[{p.name}]")
                findings.extend(file_findings)

        verdict = "fail" if any_fail else "pass"
        return OracleReport(
            oracle=self.name, verdict=verdict, findings=findings,
            evidence={"files": per_file},
        )


register_oracle("sentaurus", SentaurusOracle())
