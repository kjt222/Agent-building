import tempfile
import unittest
from pathlib import Path

import yaml

from agent.profile import resolve_profile, update_active_profile


class TestProfileConfig(unittest.TestCase):
    def test_resolve_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            app_path = config_dir / "app.yaml"
            db_path = Path(temp_dir) / "rag.sqlite"
            logs_dir = Path(temp_dir) / "logs"
            lexicon = Path(temp_dir) / "lexicons" / "global.yaml"
            lexicon.parent.mkdir(parents=True, exist_ok=True)
            lexicon.write_text("sensitive: []\nwhitelist: []\n", encoding="utf-8")

            payload = {
                "active_profile": "research",
                "profiles": {
                    "research": {
                        "rag_db_path": str(db_path),
                        "logs_dir": str(logs_dir),
                        "lexicon_files": [str(lexicon)],
                        "cloud_send": "raw",
                        "allow_raw_on_confirm": True,
                        "conflict_confirm": False,
                        "vector_store_content": "raw",
                    }
                },
            }
            with app_path.open("w", encoding="utf-8") as handle:
                yaml.safe_dump(payload, handle)

            profile = resolve_profile(str(config_dir))
            self.assertEqual(profile.name, "research")
            self.assertEqual(profile.rag_db_path, db_path)
            self.assertEqual(profile.logs_dir, logs_dir)
            self.assertEqual(profile.cloud_send, "raw")

    def test_update_active_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            app_path = config_dir / "app.yaml"
            payload = {
                "active_profile": "research",
                "profiles": {"research": {}, "sensitive": {}},
            }
            with app_path.open("w", encoding="utf-8") as handle:
                yaml.safe_dump(payload, handle)

            update_active_profile(str(config_dir), "sensitive")
            updated = yaml.safe_load(app_path.read_text(encoding="utf-8"))
            self.assertEqual(updated["active_profile"], "sensitive")
