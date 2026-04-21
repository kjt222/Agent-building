import tempfile
import unittest
from pathlib import Path

from agent.init_setup import init_app


class TestInitSetup(unittest.TestCase):
    def test_init_app_creates_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir) / "config"
            created = init_app(str(config_dir), force=True, active_profile="research")
            app_yaml = config_dir / "app.yaml"
            self.assertTrue(app_yaml.exists())
            self.assertTrue(created)
