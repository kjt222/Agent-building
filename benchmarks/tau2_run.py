"""Run tau2-bench retail with our scaffold agent.

Registers `OurScaffoldAgent` and invokes `tau2.runner.run_domain` directly —
we cannot use the tau2 CLI because it validates `--agent` against a fixed
choices list at arg-parse time (before any plugin hooks fire).

Usage:
    # Smoke test: 2 tasks
    python -m benchmarks.tau2_run --domain retail --num-tasks 2 \
        --llm gpt-5.4 --save-to our_scaffold_smoke

    # Full 20-task retail run matching the baseline
    python -m benchmarks.tau2_run --domain retail --num-tasks 20 \
        --llm gpt-5.4 --max-concurrency 4 --save-to our_scaffold_retail_20
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from tau2.data_model.simulation import TextRunConfig
from tau2.registry import registry
from tau2.runner import run_domain

from benchmarks.tau2_adapter import create_our_agent


AGENT_NAME = "our_scaffold"


def _register_once() -> None:
    # Idempotent: tau2's registry raises on duplicate registration.
    if AGENT_NAME not in registry._agent_factories:  # noqa: SLF001
        registry.register_agent_factory(create_our_agent, AGENT_NAME)


def _build_config(args: argparse.Namespace) -> TextRunConfig:
    agent_args: dict = {}
    if args.temperature is not None:
        agent_args["temperature"] = args.temperature
    if args.reasoning_effort is not None:
        agent_args["reasoning_effort"] = args.reasoning_effort
    if args.provider != "openai":
        agent_args["provider"] = args.provider

    user_args: dict = {}
    if args.temperature is not None:
        user_args["temperature"] = args.temperature

    return TextRunConfig(
        domain=args.domain,
        agent=AGENT_NAME,
        llm_agent=args.llm,
        llm_args_agent=agent_args,
        user="user_simulator",
        llm_user=args.llm_user or args.llm,
        llm_args_user=user_args,
        num_tasks=args.num_tasks,
        num_trials=args.num_trials,
        max_steps=args.max_steps,
        max_concurrency=args.max_concurrency,
        save_to=args.save_to,
        seed=args.seed,
        log_level=args.log_level,
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Run tau2-bench with OurScaffoldAgent.")
    p.add_argument("--domain", default="retail")
    p.add_argument("--llm", default="gpt-5.4", help="Model for the agent")
    p.add_argument("--llm-user", default=None, help="Model for user simulator (defaults to --llm)")
    p.add_argument("--temperature", type=float, default=None)
    p.add_argument(
        "--reasoning-effort",
        choices=["minimal", "low", "medium", "high", "none"],
        default=None,
        help="gpt-5 series reasoning_effort (passed via llm_args_agent)",
    )
    p.add_argument("--num-tasks", type=int, default=20)
    p.add_argument("--num-trials", type=int, default=1)
    p.add_argument("--max-steps", type=int, default=30)
    p.add_argument("--max-concurrency", type=int, default=4)
    p.add_argument("--save-to", default="our_scaffold_retail_20")
    p.add_argument(
        "--provider",
        choices=["openai", "qwen"],
        default="openai",
        help="Which model provider our scaffold agent uses (user simulator stays on OpenAI).",
    )
    p.add_argument("--seed", type=int, default=300)
    p.add_argument("--log-level", default="INFO")
    args = p.parse_args(argv)

    _register_once()
    config = _build_config(args)
    results = run_domain(config)

    # Pass^1 summary to stdout (tau2 also prints its own metrics panel)
    n = len(results.simulations)
    passes = sum(
        1 for s in results.simulations if s.reward_info and s.reward_info.reward == 1.0
    )
    print(f"\n[our_scaffold] {passes}/{n} passed = {passes / max(n, 1):.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
