import json
import re
import unittest
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent.config_loader import get_config_dir, load_app_config, save_yaml
from agent.credentials import resolve_api_key
from agent.ui.server import create_app


FIXTURES_PATH = Path(__file__).parent.parent / "fixtures" / "queries.json"


CITE_RE = re.compile(r"\[(\d+)\]")


@pytest.mark.integration
class TestSourcesMatch(unittest.TestCase):
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

        cls.client = TestClient(create_app(cls.config_dir))

        with FIXTURES_PATH.open("r", encoding="utf-8") as handle:
            cls.queries = json.load(handle)

    @classmethod
    def tearDownClass(cls) -> None:
        if cls.app_path and cls.original_app:
            cls.app_path.write_text(cls.original_app, encoding="utf-8")

    def test_sources_match_citations(self) -> None:
        for question in self.queries.get("should_use_kb", []):
            resp = self.client.post("/api/chat", json={"message": question, "kb_mode": "auto"})
            self.assertEqual(resp.status_code, 200, msg=question)
            data = resp.json()
            reply = data.get("reply", "")
            sources = data.get("sources") or []
            cited = {int(m) for m in CITE_RE.findall(reply)}
            if cited:
                self.assertEqual(
                    len(sources),
                    len(cited),
                    msg=f"Cited indices should match sources count: {question}",
                )
            else:
                self.assertEqual(len(sources), 0, msg=f"No citations expected: {question}")

