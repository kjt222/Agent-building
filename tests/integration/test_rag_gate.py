import json
import os
import unittest
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent.config_loader import get_config_dir, load_app_config, save_yaml
from agent.credentials import resolve_api_key
from agent.profile import resolve_profile
from agent.ui.server import create_app


FIXTURES_PATH = Path(__file__).parent.parent / "fixtures" / "queries.json"


def _read_new_events(log_path: Path, offset: int) -> tuple[list[dict], int]:
    if not log_path.exists():
        return [], offset
    with log_path.open("rb") as handle:
        handle.seek(offset)
        data = handle.read()
        new_offset = handle.tell()
    if not data:
        return [], new_offset
    lines = data.decode("utf-8").splitlines()
    events = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events, new_offset


@pytest.mark.integration
class TestRagGate(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        key = resolve_api_key(api_key_ref="22.llm.zhipu", api_key_env=None, prefer_env=True)
        if not key:
            raise unittest.SkipTest("Missing API key for profile 22 (zhipu).")

        cls.config_dir = None
        cls.paths_base = get_config_dir(cls.config_dir)
        cls.app_path = cls.paths_base / "app.yaml"
        cls.original_app = cls.app_path.read_text(encoding="utf-8")

        app_cfg = load_app_config(cls.config_dir)
        app_cfg["active_profile"] = "22"
        active_kbs = app_cfg.get("active_kbs") or []
        if "22" not in active_kbs:
            active_kbs.append("22")
        app_cfg["active_kbs"] = active_kbs
        app_cfg["active_kb"] = "22"
        save_yaml(cls.app_path, app_cfg)

        cls.profile = resolve_profile(cls.config_dir, "22")
        cls.log_path = cls.profile.logs_dir / "agent.log.jsonl"
        cls.client = TestClient(create_app(cls.config_dir))

        with FIXTURES_PATH.open("r", encoding="utf-8-sig") as handle:
            cls.queries = json.load(handle)

    @classmethod
    def tearDownClass(cls) -> None:
        if cls.app_path and cls.original_app:
            cls.app_path.write_text(cls.original_app, encoding="utf-8")

    def _last_chat_event(self, offset: int) -> tuple[dict, int]:
        events, new_offset = _read_new_events(self.log_path, offset)
        for event in reversed(events):
            if event.get("action") == "chat":
                return event, new_offset
        return {}, new_offset

    def test_should_skip_kb(self) -> None:
        for question in self.queries.get("should_skip_kb", []):
            offset = self.log_path.stat().st_size if self.log_path.exists() else 0
            resp = self.client.post("/api/chat", json={"message": question, "kb_mode": "auto"})
            self.assertEqual(resp.status_code, 200, msg=question)
            event, _ = self._last_chat_event(offset)
            self.assertTrue(event.get("skip_kb"), msg=f"Expected skip for: {question}")
            self.assertEqual(event.get("sources_count"), 0, msg=f"Expected no sources: {question}")

    def test_should_use_kb(self) -> None:
        for question in self.queries.get("should_use_kb", []):
            offset = self.log_path.stat().st_size if self.log_path.exists() else 0
            resp = self.client.post("/api/chat", json={"message": question, "kb_mode": "auto"})
            self.assertEqual(resp.status_code, 200, msg=question)
            event, _ = self._last_chat_event(offset)
            self.assertFalse(event.get("skip_kb"), msg=f"Expected use KB for: {question}")
            self.assertGreaterEqual(event.get("sources_count", 0), 1, msg=f"Expected sources: {question}")

