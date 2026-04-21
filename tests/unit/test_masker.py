import tempfile
import unittest
from pathlib import Path

import yaml

from agent.privacy import load_lexicons, mask_text


class TestMasker(unittest.TestCase):
    def test_mask_text(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            lexicon_path = Path(temp_dir) / "lex.yaml"
            payload = {
                "sensitive": ["Alice", "Bob"],
                "whitelist": ["Bob"],
                "patterns": [{"name": "email", "regex": r"[A-Za-z0-9._%+-]+@example\.com"}],
            }
            with lexicon_path.open("w", encoding="utf-8") as handle:
                yaml.safe_dump(payload, handle)

            lexicon = load_lexicons([lexicon_path])
            text = "Alice sent mail to test@example.com and Bob."
            masked = mask_text(text, lexicon)
            self.assertIn("[MASKED]", masked)
            self.assertIn("[MASKED:email]", masked)
            self.assertIn("Bob", masked)
