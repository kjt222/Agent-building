"""P14.2/14.3 acceptance: domain oracles (L2) + vision judge (L3).

L1 structural checks live in the runners themselves and in `agent/tools_v2/
verify_tool.py`. L2 oracles answer "is this output semantically meaningful for
this domain?" by parsing the artifact (Excalidraw JSON, docx, .gds, .tdr ...)
and probing domain invariants. L3 vision_judge renders the artifact to an
image and asks a multimodal model whether the result satisfies the task.

Both layers downgrade rather than fail-hard when prerequisites are missing
(no API key, no rendered image, no task_spec) — the verdict layer treats
`warn` / `unknown` as not-pass, but smoke runs still complete and emit
structured output for the next iteration.
"""

from .oracle import DomainOracle, OracleReport, get_oracle, register_oracle

__all__ = ["DomainOracle", "OracleReport", "get_oracle", "register_oracle"]
