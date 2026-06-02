"""L2 oracle contract (P14.2).

A `DomainOracle` answers: "given these artifact paths and a task_spec, is the
output semantically meaningful for this domain?" Oracles are pure (no LLM, no
network) — they parse files and check domain invariants. They feed
`OracleReport.findings` back to the agent as actionable repair hints so a
long-running loop can self-correct without human intervention.

Registry pattern (no auto-discovery, no plugin scanning): import the oracle
module and call `register_oracle(name, instance)` at module load. Callers
look up via `get_oracle("excalidraw")`. Returning `None` rather than raising
keeps smoke runners robust against missing oracles in older test trees.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Literal, Protocol, runtime_checkable

Verdict = Literal["pass", "fail", "warn", "unknown"]


@dataclass
class OracleReport:
    """Structured oracle output.

    `verdict` is the L2 summary; `findings` is the human-readable / agent-
    actionable list of problems (e.g. "element ele42 has latex_source but no
    groupId"); `evidence` is opaque structured data for summary.json that
    later analysis can reduce over without re-parsing the artifact.
    """

    oracle: str
    verdict: Verdict = "unknown"
    findings: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "oracle": self.oracle,
            "verdict": self.verdict,
            "findings": list(self.findings),
            "evidence": dict(self.evidence),
        }


@runtime_checkable
class DomainOracle(Protocol):
    name: str

    def check(
        self,
        artifact_paths: Iterable[Path],
        task_spec: dict[str, Any] | None = None,
    ) -> OracleReport:
        ...


_REGISTRY: dict[str, DomainOracle] = {}


def register_oracle(name: str, oracle: DomainOracle) -> None:
    _REGISTRY[name] = oracle


def get_oracle(name: str) -> DomainOracle | None:
    return _REGISTRY.get(name)


def list_oracles() -> list[str]:
    return sorted(_REGISTRY)
