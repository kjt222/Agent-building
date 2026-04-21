import tempfile
import unittest
from pathlib import Path

import yaml

from agent.config_loader import load_app_config, load_office_config, load_yaml, save_yaml


class TestConfigLoader(unittest.TestCase):
    def test_load_yaml(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "sample.yaml"
            payload = {"alpha": 1, "beta": {"enabled": True}}
            with path.open("w", encoding="utf-8") as handle:
                yaml.safe_dump(payload, handle)

            loaded = load_yaml(path)
            self.assertEqual(loaded, payload)

    def test_load_app_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            app_path = config_dir / "app.yaml"
            payload = {"active_profile": "research", "profiles": {"research": {}}}
            with app_path.open("w", encoding="utf-8") as handle:
                yaml.safe_dump(payload, handle)

            loaded = load_app_config(str(config_dir))
            self.assertEqual(loaded["active_profile"], "research")

    def test_load_office_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            office_path = config_dir / "office.yaml"
            payload = {"backend": "com", "apps": {"word": {}}}
            with office_path.open("w", encoding="utf-8") as handle:
                yaml.safe_dump(payload, handle)

            loaded = load_office_config(str(config_dir))
            self.assertEqual(loaded["backend"], "com")

    def test_save_yaml(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "out.yaml"
            payload = {"alpha": 1, "beta": {"flag": True}}
            save_yaml(path, payload)
            loaded = load_yaml(path)
            self.assertEqual(loaded, payload)
