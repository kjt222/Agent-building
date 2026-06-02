"""Per-conversation token-usage tracker (P12.6).

A tiny in-memory rollup. After each ``/api/agent_chat_v2`` turn the v2
endpoint adds the run's final ``ctx.usage`` to the entry for that
conversation. The UI reads the cumulative value via the SSE
``usage_update`` activity event (and a diagnostic GET endpoint).

Pricing is a coarse, optional add-on. Unknown models map to ``None`` and
the UI shows the token count without a currency estimate.
"""

from __future__ import annotations

import threading
from typing import Dict, Optional


# USD per 1M tokens. Update conservatively — overestimating is safer than
# under, since the meter is meant to keep the user honest about spend.
# Set to 0.0 for models the user is on a flat-rate / free plan.
_PRICING_USD_PER_M: dict[str, dict[str, float]] = {
    # Anthropic
    "claude-opus-4-7": {"in": 15.0, "out": 75.0},
    "claude-sonnet-4-6": {"in": 3.0, "out": 15.0},
    "claude-haiku-4-5": {"in": 0.8, "out": 4.0},
    # OpenAI
    "gpt-5.4": {"in": 1.25, "out": 10.0},
    "gpt-5.4-mini": {"in": 0.25, "out": 2.0},
    "gpt-5-mini": {"in": 0.25, "out": 2.0},
    # DeepSeek
    "deepseek-chat": {"in": 0.14, "out": 0.28},
    "deepseek-reasoner": {"in": 0.55, "out": 2.19},
    # Doubao / volcengine — flat-rate plan, mark as 0.
    "doubao-seed-2.0-code": {"in": 0.0, "out": 0.0},
}


def estimate_cost_usd(model: str, input_tokens: int, output_tokens: int) -> Optional[float]:
    """Return a USD estimate, or ``None`` if the model is not priced."""
    rates = _PRICING_USD_PER_M.get(str(model or "").strip())
    if rates is None:
        return None
    return round(
        (input_tokens / 1_000_000.0) * rates["in"]
        + (output_tokens / 1_000_000.0) * rates["out"],
        6,
    )


_USAGE: Dict[str, Dict[str, int]] = {}
_LOCK = threading.Lock()


def _key(conversation_id: str | None) -> str:
    cid = (conversation_id or "").strip()
    return cid or "default"


def add_run(
    conversation_id: str | None,
    *,
    usage: dict,
    model: str | None = None,
) -> dict:
    """Add this run's final ``ctx.usage`` to the conversation rollup.

    Returns a dict with ``run`` (this run's totals + cost), ``cumulative``
    (conversation-cumulative totals + cost), and ``model``. Pass the
    return value straight to the SSE producer.
    """
    keys = ("input_tokens", "output_tokens", "reasoning_tokens", "total_tokens")
    run = {k: int(usage.get(k) or 0) for k in keys}
    cid = _key(conversation_id)
    with _LOCK:
        entry = _USAGE.setdefault(cid, {k: 0 for k in keys})
        for k in keys:
            entry[k] += run[k]
        cumulative = dict(entry)
    run_cost = estimate_cost_usd(model or "", run["input_tokens"], run["output_tokens"])
    cum_cost = estimate_cost_usd(
        model or "", cumulative["input_tokens"], cumulative["output_tokens"]
    )
    return {
        "model": model or "",
        "run": {**run, "cost_usd": run_cost},
        "cumulative": {**cumulative, "cost_usd": cum_cost},
    }


def get_cumulative(conversation_id: str | None) -> dict:
    cid = _key(conversation_id)
    with _LOCK:
        entry = _USAGE.get(cid)
        return dict(entry) if entry else {
            "input_tokens": 0,
            "output_tokens": 0,
            "reasoning_tokens": 0,
            "total_tokens": 0,
        }


def reset(conversation_id: str | None) -> None:
    cid = _key(conversation_id)
    with _LOCK:
        _USAGE.pop(cid, None)


def reset_all() -> None:
    with _LOCK:
        _USAGE.clear()
