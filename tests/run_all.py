"""统一测试入口

使用方式：
    python -m tests.run_all              # unit 测试
    python -m tests.run_all --integration  # + integration 测试
    python -m tests.run_all --perf         # + 性能基准
    python -m tests.run_all --all          # 全部
"""

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
RESULTS_DIR = BASE_DIR / "results"


def run_pytest(dirs: list[str], extra_args: list[str] | None = None) -> bool:
    cmd = [sys.executable, "-m", "pytest", "-v"] + dirs
    if extra_args:
        cmd.extend(extra_args)
    result = subprocess.run(cmd, cwd=BASE_DIR.parent)
    return result.returncode == 0


def main() -> None:
    parser = argparse.ArgumentParser(description="统一测试入口")
    parser.add_argument("--integration", action="store_true", help="Include integration tests")
    parser.add_argument("--perf", action="store_true", help="Include performance tests")
    parser.add_argument("--all", action="store_true", help="Run everything")
    args = parser.parse_args()

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    status = {
        "timestamp": datetime.now().isoformat(),
        "unit": False,
        "integration": False,
        "perf": False,
    }

    # Unit tests（总是运行）
    status["unit"] = run_pytest(["tests/unit/"])

    # Integration tests
    if args.integration or args.all:
        status["integration"] = run_pytest(
            ["tests/integration/"], extra_args=["--run-integration"]
        )

    # Performance tests
    if args.perf or args.all:
        status["perf"] = run_pytest(
            ["tests/perf/"], extra_args=["--run-slow"]
        )

    out_path = RESULTS_DIR / f"run_all_{datetime.now().strftime('%Y-%m-%d')}.json"
    out_path.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")

    ok = status["unit"] and (not args.integration or status["integration"])
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
