"""Skill registry: progressive tool/prompt disclosure driven by SKILL.md files.

Each skill is a directory ``skills/<name>/SKILL.md`` with simple YAML-ish
frontmatter and a markdown body. The frontmatter lists trigger regexes,
optional history-context regexes, the tool subset that should be exposed when
the skill is active, a stable ``scope`` id, and a numeric ``priority``. The
body is appended to the system prompt only when the skill matches.

The loader is intentionally tiny: no PyYAML dependency, no Jinja, no schema
validation beyond what is needed by the loop. Skills are sorted by priority
descending so the most specific one wins.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    scope: str
    priority: int
    triggers: tuple[re.Pattern, ...]
    history_triggers: tuple[re.Pattern, ...]
    tools_base: tuple[str, ...]
    tools: tuple[str, ...]
    prompt_body: str
    source_path: Path | None = None

    def matches(self, message: str, *, history_text: str = "") -> bool:
        if not self.triggers:
            return False
        if not any(p.search(message or "") for p in self.triggers):
            return False
        if self.history_triggers:
            if not any(p.search(history_text or "") for p in self.history_triggers):
                return False
        return True

    def all_tools(self) -> tuple[str, ...]:
        seen: dict[str, None] = {}
        for name in (*self.tools_base, *self.tools):
            if name not in seen:
                seen[name] = None
        return tuple(seen)


_DOUBLE_QUOTE_ESCAPES = {
    '"': '"',
    "\\": "\\",
    "n": "\n",
    "t": "\t",
    "r": "\r",
    "/": "/",
    "0": "\0",
}


def _unescape_double_quoted(text: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "\\" and i + 1 < len(text):
            nxt = text[i + 1]
            out.append(_DOUBLE_QUOTE_ESCAPES.get(nxt, nxt))
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _strip_quotes(value: str) -> str:
    if len(value) < 2:
        return value
    first, last = value[0], value[-1]
    if first == last and first == '"':
        return _unescape_double_quoted(value[1:-1])
    if first == last and first == "'":
        return value[1:-1]
    return value


def _parse_simple_yaml(block: str) -> dict:
    """Parse a tiny subset of YAML used in SKILL.md frontmatter.

    Supports:
    - ``key: value`` scalar pairs
    - ``key:`` followed by indented ``- item`` list entries
    - inline comments after ``#``
    - quoted strings (single or double)
    """
    out: dict = {}
    current_list_key: str | None = None
    for raw in block.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if line.startswith((" ", "\t")):
            content = stripped
            if current_list_key and content.startswith("- "):
                value = _strip_quotes(content[2:].strip())
                out.setdefault(current_list_key, []).append(value)
            continue
        if ":" not in stripped:
            continue
        key, _, rest = stripped.partition(":")
        key = key.strip()
        rest = rest.strip()
        if not rest:
            current_list_key = key
            out.setdefault(key, [])
            continue
        current_list_key = None
        out[key] = _strip_quotes(rest)
    return out


def _coerce_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value if v is not None]
    if isinstance(value, str):
        return [value]
    return [str(value)]


def _compile_triggers(values: Iterable[str]) -> tuple[re.Pattern, ...]:
    compiled: list[re.Pattern] = []
    for raw in values:
        if not raw:
            continue
        try:
            compiled.append(re.compile(raw, re.IGNORECASE))
        except re.error:
            continue
    return tuple(compiled)


def parse_skill_file(path: Path) -> Skill | None:
    text = path.read_text(encoding="utf-8", errors="replace")
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return None
    front, body = match.group(1), match.group(2)
    meta = _parse_simple_yaml(front)
    name = str(meta.get("name") or path.parent.name).strip()
    if not name:
        return None
    try:
        priority = int(str(meta.get("priority") or 0))
    except ValueError:
        priority = 0
    return Skill(
        name=name,
        description=str(meta.get("description") or "").strip(),
        scope=str(meta.get("scope") or name).strip(),
        priority=priority,
        triggers=_compile_triggers(_coerce_list(meta.get("triggers"))),
        history_triggers=_compile_triggers(_coerce_list(meta.get("history_triggers"))),
        tools_base=tuple(_coerce_list(meta.get("tools_base"))),
        tools=tuple(_coerce_list(meta.get("tools"))),
        prompt_body=body.strip(),
        source_path=path,
    )


def load_skills(skills_dir: Path) -> list[Skill]:
    if not skills_dir.exists():
        return []
    skills: list[Skill] = []
    for path in sorted(skills_dir.glob("*/SKILL.md")):
        skill = parse_skill_file(path)
        if skill is not None:
            skills.append(skill)
    skills.sort(key=lambda item: (-item.priority, item.name))
    return skills


def select_skill(
    message: str,
    *,
    skills: list[Skill],
    history_text: str = "",
) -> Skill | None:
    for skill in skills:
        if skill.matches(message, history_text=history_text):
            return skill
    return None


def build_history_text(history: list | None, limit: int = 8) -> str:
    if not history:
        return ""
    recent = history[-limit:]
    parts: list[str] = []
    for item in recent:
        if not isinstance(item, dict):
            continue
        content = item.get("content")
        if content is None:
            continue
        parts.append(str(content))
    return "\n".join(parts)
