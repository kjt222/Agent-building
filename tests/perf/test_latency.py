"""Performance tests for RAG latency (manual run)."""

import json
import statistics
import time
from datetime import datetime
from pathlib import Path

from fastapi.testclient import TestClient

from agent.config_loader import get_config_dir, load_app_config, save_yaml
from agent.credentials import resolve_api_key
from agent.ui.server import create_app


BASE_DIR = Path(__file__).resolve().parent.parent
FIXTURES_PATH = BASE_DIR / "fixtures" / "queries.json"
RESULTS_DIR = BASE_DIR / "results"


def _run_chat(client: TestClient, question: str, use_kb: bool) -> float:
    start = time.perf_counter()
    resp = client.post("/api/chat", json={"message": question, "use_kb": use_kb, "kb_mode": "auto"})
    resp.raise_for_status()
    return (time.perf_counter() - start) * 1000.0


def main() -> None:
    key = resolve_api_key(api_key_ref="22.llm.zhipu", api_key_env=None, prefer_env=True)
    if not key:
        print("Missing API key for profile 22 (zhipu).")
        return

    config_dir = None
    app_path = get_config_dir(config_dir) / "app.yaml"
    original_app = app_path.read_text(encoding="utf-8")

    try:
        app_cfg = load_app_config(config_dir)
        app_cfg["active_profile"] = "22"
        active_kbs = app_cfg.get("active_kbs") or []
        if "22" not in active_kbs:
            active_kbs.append("22")
        app_cfg["active_kbs"] = active_kbs
        app_cfg["active_kb"] = "22"
        save_yaml(app_path, app_cfg)

        client = TestClient(create_app(config_dir))
        fixtures = json.loads(FIXTURES_PATH.read_text(encoding="utf-8-sig"))
        queries = fixtures.get("should_skip_kb", []) + fixtures.get("should_use_kb", [])

        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        out_path = RESULTS_DIR / f"latency_{datetime.now().strftime('%Y-%m-%d')}.jsonl"

        runs = 3
        with out_path.open("a", encoding="utf-8") as handle:
            for question in queries:
                timings = {}
                for use_kb in (False, True):
                    samples = [_run_chat(client, question, use_kb) for _ in range(runs)]
                    timings[str(use_kb)] = {
                        "samples_ms": samples,
                        "median_ms": statistics.median(samples),
                    }
                record = {
                    "question": question,
                    "runs": runs,
                    "timings": timings,
                }
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                print(record)
    finally:
        app_path.write_text(original_app, encoding="utf-8")


if __name__ == "__main__":
    main()
