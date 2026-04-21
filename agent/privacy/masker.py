from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import yaml


@dataclass(frozen=True)
class PatternSpec:
    name: str
    regex: str


@dataclass(frozen=True)
class Lexicon:
    sensitive: list[str]
    whitelist: list[str]
    patterns: list[PatternSpec]


DEFAULT_PATTERNS = [
    PatternSpec(name="id_card", regex=r"\b\d{17}[\dXx]\b"),
    PatternSpec(name="phone", regex=r"\b1\d{10}\b"),
    PatternSpec(name="email", regex=r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    PatternSpec(name="bank_card", regex=r"\b\d{12,19}\b"),
    PatternSpec(name="tax_id", regex=r"\b[0-9A-Z]{15,20}\b"),
    PatternSpec(name="invoice_no", regex=r"\b\d{8,20}\b"),
]


def load_lexicons(files: Iterable[Path]) -> Lexicon:
    sensitive: list[str] = []
    whitelist: list[str] = []
    patterns: list[PatternSpec] = []
    for path in files:
        if not path.exists():
            continue
        if path.suffix.lower() in {".yaml", ".yml"}:
            data = _load_yaml(path)
            sensitive.extend(_as_list(data.get("sensitive")))
            whitelist.extend(_as_list(data.get("whitelist")))
            patterns.extend(_load_patterns(data.get("patterns")))
        elif path.suffix.lower() == ".txt":
            lines = _load_txt(path)
            if "whitelist" in path.name.lower():
                whitelist.extend(lines)
            else:
                sensitive.extend(lines)
    return Lexicon(
        sensitive=_unique(sensitive),
        whitelist=_unique(whitelist),
        patterns=_unique_patterns(patterns or DEFAULT_PATTERNS),
    )


def mask_text(text: str, lexicon: Lexicon) -> str:
    masked = text
    for pattern in lexicon.patterns:
        masked = re.sub(pattern.regex, f"[MASKED:{pattern.name}]", masked)
    for term in sorted(lexicon.sensitive, key=len, reverse=True):
        if term and term not in lexicon.whitelist:
            masked = masked.replace(term, "[MASKED]")
    return masked


def _load_yaml(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Invalid yaml lexicon format: {path}")
    return data


def _load_txt(path: Path) -> list[str]:
    lines = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        lines.append(line)
    return lines


def _as_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    return [str(value)]


def _load_patterns(value: object) -> list[PatternSpec]:
    if not value:
        return []
    if not isinstance(value, list):
        raise ValueError("patterns must be a list")
    patterns: list[PatternSpec] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "pattern"))
        regex = str(item.get("regex", "")).strip()
        if regex:
            patterns.append(PatternSpec(name=name, regex=regex))
    return patterns


def _unique(values: Iterable[str]) -> list[str]:
    seen = set()
    output = []
    for value in values:
        if value not in seen:
            seen.add(value)
            output.append(value)
    return output


def _unique_patterns(patterns: Iterable[PatternSpec]) -> list[PatternSpec]:
    seen = set()
    output = []
    for pattern in patterns:
        key = (pattern.name, pattern.regex)
        if key not in seen:
            seen.add(key)
            output.append(pattern)
    return output
