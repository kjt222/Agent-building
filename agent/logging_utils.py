from __future__ import annotations

import json
from pathlib import Path
from time import strftime


def log_event(log_dir: Path, event: dict) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    payload = dict(event)
    payload.setdefault("timestamp", strftime("%Y-%m-%dT%H:%M:%S"))
    log_path = log_dir / "agent.log.jsonl"
    with log_path.open("a", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False)
        handle.write("\n")
