"""Shared types and helpers for harness_bench tasks.

Task module contract (each `task_NN_<slug>.py`):
    PROMPT: str               # message sent to /api/agent_chat_v2;
                              # empty string = skip agent (verifier-sanity only)
    MODE: str = "read-only"   # "read-only" | "full-access"
    PROFILE_OVERRIDE: str = "" # if non-empty, force this model profile
                              # (e.g. vision-capable model for screenshot tasks)
    TIMEOUT_S: float = 240.0  # wall-clock cap on the whole turn
    MAX_ITERATIONS: int = 0   # 0 = unlimited (Claude Code mode). Override per
                              # task ONLY when you need a low cap on purpose
                              # (e.g. P18-C task 12 doom-loop detection).
    NEEDS_AGENT: bool = True  # set False for verifier-sanity tasks (P18-D).
                              # When False, runner skips server start AND skips
                              # the /api/agent_chat_v2 call — verify() runs on
                              # the empty default RunOutcome.
    def setup() -> dict        # prepare fixtures; return state dict for verify
    def verify(outcome, state) -> tuple[bool, str]   # (passed, reason)
    def teardown(state) -> None  # optional cleanup
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any


@dataclass
class RunOutcome:
    """What the agent runner observed for one prompt."""

    tool_calls: list[str] = field(default_factory=list)
    tool_trace: list[dict[str, Any]] = field(default_factory=list)
    assistant_text: str = ""
    manifest_tools: list[str] = field(default_factory=list)
    capability_scope: Any = None
    done: dict[str, Any] = field(default_factory=dict)
    elapsed_s: float = 0.0
    error: str = ""
    # P18.1.4: silent-handoff StopPolicy flags. Each str is one of:
    #   "unexecuted_helper_script" | "handoff_phrase" | "shell_pattern_stuck"
    # Multiple flags can co-occur (model can both abandon a script AND ask
    # user to run it AND be stuck in Windows shell). gate_retry_count is the
    # number of follow-up nudges the StopPolicy actually injected before the
    # loop ended.
    silent_handoff_flags: list[str] = field(default_factory=list)
    gate_retry_count: int = 0

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TaskResult:
    task_id: str
    profile: str                       # the --profile flag (bench-wide)
    effective_profile: str             # what was actually used (== PROFILE_OVERRIDE or profile)
    passed: bool
    reason: str
    elapsed_s: float
    category: str = "ok"               # "ok" | "run_error" | "setup_error" | "verify_error" | "task_error"
    outcome: dict[str, Any] = field(default_factory=dict)
    setup_state: dict[str, Any] = field(default_factory=dict)


def tool_was_called(outcome: RunOutcome, name: str) -> bool:
    return name in outcome.tool_calls


def tool_results_for(outcome: RunOutcome, name: str) -> list[dict[str, Any]]:
    return [t for t in outcome.tool_trace if t.get("name") == name]


# ---------------------------------------------------------------------------
# Post-hoc silent-handoff classifier (P18.1.4).
#
# The same detection logic runs inside the server as a StopPolicy (see
# agent/core/hooks.py::make_silent_handoff_policy). That policy decides
# whether to inject a nudge during the loop. THIS function runs over the
# already-recorded outcome in the bench process for analytics: it labels
# each finished run with the same flag taxonomy so summary.json can break
# out fails by root cause.
#
# Detection logic is duplicated here on purpose for now — server policy
# walks typed Message blocks, bench post-hoc walks dict tool_trace. If
# they diverge in practice we'll factor into a shared module. Regex
# constants are the load-bearing part; if you change one, change both.
# ---------------------------------------------------------------------------

_SCRIPT_EXTS = (".py", ".pyw", ".ps1", ".sh", ".bash", ".bat", ".cmd")

_CODE_FENCE_RE = re.compile(
    r"```(?:python|py|bash|sh|shell|ps1|powershell|cmd|bat)\b",
    re.IGNORECASE,
)

_BARE_FENCE_WITH_CMD_RE = re.compile(
    r"```\s*\n\s*"
    r"(?:python|py|python3|pwsh|powershell|bash|sh|cmd|"
    r"\.?[\\/]?[\w.-]+\.(?:py|sh|ps1|bat|cmd|exe))\b",
    re.IGNORECASE,
)

_HANDOFF_PHRASE_RE = re.compile(
    r"\brun (?:this|that|the|it)\b|"
    r"\bexecute (?:this|that|the|it|the prepared)\b|"
    r"\bplease run\b|"
    r"\byou (?:should|can|need to|just need to|just have to) run\b|"
    r"\bfrom a shell where you can\b|"
    r"请你(?:跑|运行|执行)|"
    r"麻烦(?:你)?(?:跑|运行|执行)|"
    r"手动(?:运行|执行)|"
    r"你(?:可以|需要|只需)(?:跑|运行|执行)",
    re.IGNORECASE,
)

_SHELL_STUCK_PATTERNS = (
    "<< 'eof'",
    "<<eof",
    "syntax error near unexpected token",
    "unexpected end of file",
    "cannot find the path specified",
    "the system cannot find the file specified",
    "is not recognized as an internal or external command",
    "heredoc",
    "此时不应有",
    "系统找不到指定的",
    "系统找不到文件",
    "系统找不到路径",
    "不是内部或外部命令",
    "无法识别",
)

# P18.1.6: incomplete-plan markers. Mirror of hooks.py constants.
_PLAN_NUMBERED_RE = re.compile(r"(?m)^\s*\d+\.\s+\S.+$")
_PLAN_CHECKBOX_RE = re.compile(r"(?m)^\s*[-*+]\s*\[\s\]\s+\S")
_COMPLETION_PHRASE_RE = re.compile(
    r"\b(?:all\s+)?done\b|"
    r"\bfinished\b|"
    r"\bcompleted\b|"
    r"\bsuccessfully\s+(?:applied|completed|renamed|updated)\b|"
    r"任务(?:已)?完成|"
    r"(?:已经?|全部)?(?:完成|完毕|搞定|做完|改完|处理完)|"
    r"修改(?:已)?完成",
    re.IGNORECASE,
)
_MUTATION_BASH_RE = re.compile(
    r"(?:^|[\s;&|`(])"
    r"(?:mv|move|rename|ren|cp|copy|rm|del|mkdir|touch|"
    r"git\s+(?:mv|rm|add)|"
    r"powershell[^|;]*?(?:Move-Item|Copy-Item|Remove-Item|New-Item|Rename-Item))"
    r"\b",
    re.IGNORECASE,
)

FLAG_UNEXECUTED = "unexecuted_helper_script"
FLAG_HANDOFF = "handoff_phrase"
FLAG_SHELL_STUCK = "shell_pattern_stuck"
FLAG_INCOMPLETE_PLAN = "incomplete_plan"


def _ext_of(path: str) -> str:
    if not path:
        return ""
    p = path.lower().rsplit(".", 1)
    return ("." + p[-1]) if len(p) == 2 else ""


def classify_silent_handoff(outcome: RunOutcome) -> list[str]:
    """Return a sorted list of silent-handoff flags for a recorded outcome.

    Empty list = no silent-handoff pattern detected. Multiple flags can
    co-occur (e.g. unexecuted script + handoff phrasing about it).
    """
    flags: set[str] = set()

    # A. unexecuted helper script:  Write/Edit a script-ext path with no
    # subsequent Bash invocation referencing its filename.
    script_writes: list[tuple[str, int]] = []
    for i, t in enumerate(outcome.tool_trace):
        name = t.get("name") or ""
        if name not in ("Write", "Edit"):
            continue
        if t.get("is_error"):
            continue
        inp = t.get("input") or {}
        raw = inp.get("path") or inp.get("file_path") or ""
        if _ext_of(str(raw)) not in _SCRIPT_EXTS:
            continue
        try:
            filename = Path(str(raw)).name
        except Exception:
            filename = str(raw)
        if filename:
            script_writes.append((filename, i))

    if script_writes:
        bash_cmds = [
            (i, str((t.get("input") or {}).get("command") or ""))
            for i, t in enumerate(outcome.tool_trace)
            if (t.get("name") or "") == "Bash"
        ]
        for filename, write_idx in script_writes:
            if any(idx > write_idx and filename in cmd for idx, cmd in bash_cmds):
                continue
            stem = filename.rsplit(".", 1)[0]
            if stem and any(idx > write_idx and stem in cmd for idx, cmd in bash_cmds):
                continue
            flags.add(FLAG_UNEXECUTED)
            break

    # B. handoff phrasing in final assistant_text.
    text = outcome.assistant_text or ""
    has_fence = bool(_CODE_FENCE_RE.search(text)) or bool(_BARE_FENCE_WITH_CMD_RE.search(text))
    if has_fence and _HANDOFF_PHRASE_RE.search(text):
        flags.add(FLAG_HANDOFF)

    # C. shell stuck: last 3 Bash results contain >=2 cmd.exe / heredoc errors.
    bash_results = [
        t for t in outcome.tool_trace
        if (t.get("name") or "") == "Bash"
    ][-3:]
    hits = 0
    for t in bash_results:
        detail = str(t.get("detail") or "").lower()
        if any(p in detail for p in _SHELL_STUCK_PATTERNS):
            hits += 1
    if hits >= 2:
        flags.add(FLAG_SHELL_STUCK)

    # D. incomplete plan: model wrote a >=3-step numbered/checkbox plan and a
    # completion phrase, but the mutation tool count is below 60% of plan size.
    numbered = _PLAN_NUMBERED_RE.findall(text)
    checkboxes = _PLAN_CHECKBOX_RE.findall(text)
    planned = max(len(numbered), len(checkboxes))
    if planned >= 3 and _COMPLETION_PHRASE_RE.search(text):
        mutations = 0
        for t in outcome.tool_trace:
            n = t.get("name") or ""
            if n in ("Write", "Edit"):
                mutations += 1
            elif n == "Bash":
                cmd = str((t.get("input") or {}).get("command") or "")
                if _MUTATION_BASH_RE.search(cmd):
                    mutations += 1
        if mutations < planned * 0.6:
            flags.add(FLAG_INCOMPLETE_PLAN)

    return sorted(flags)
