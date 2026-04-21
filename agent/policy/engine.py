from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatch
from typing import Iterable, Optional


@dataclass(frozen=True)
class PolicyDecision:
    status: str
    rule: Optional[str] = None

    @property
    def allowed(self) -> bool:
        return self.status == "allow"

    @property
    def needs_confirmation(self) -> bool:
        return self.status == "confirm"


class PolicyEngine:
    def __init__(self, policy: dict) -> None:
        self.default = policy.get("default", "deny")
        self.allow = policy.get("allow", [])
        self.confirm = policy.get("confirm", [])
        self.deny = policy.get("deny", [])

    def _match(self, action: str, rules: Iterable[str]) -> Optional[str]:
        for rule in rules:
            if fnmatch(action, rule):
                return rule
        return None

    def check(self, action: str) -> PolicyDecision:
        matched = self._match(action, self.deny)
        if matched:
            return PolicyDecision(status="deny", rule=matched)
        matched = self._match(action, self.confirm)
        if matched:
            return PolicyDecision(status="confirm", rule=matched)
        matched = self._match(action, self.allow)
        if matched:
            return PolicyDecision(status="allow", rule=matched)
        return PolicyDecision(status=self.default, rule=None)
