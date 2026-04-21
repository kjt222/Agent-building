import os
import unittest

from agent.models.base import ModelAdapter, ModelCapabilities
from agent.models.registry import ModelRegistry


class DummyAdapter(ModelAdapter):
    capabilities = ModelCapabilities(chat=True, embeddings=True)

    def chat(self, prompt: str, **kwargs: object) -> str:
        return "ok"

    def embed(self, text: str, **kwargs: object) -> list[float]:
        return [0.0]


class TestModelRegistry(unittest.TestCase):
    def test_create_from_config(self) -> None:
        os.environ["TEST_KEY"] = "abc"
        config = {
            "profiles": {
                "research": {
                    "llm": {
                        "active": "dummy",
                        "providers": {"dummy": {"model": "m1", "api_key_env": "TEST_KEY"}},
                    },
                    "embedding": {
                        "active": "dummy",
                        "providers": {"dummy": {"model": "e1", "api_key_env": "TEST_KEY"}},
                    },
                }
            }
        }
        registry = ModelRegistry()
        registry.register("dummy", DummyAdapter)

        llm = registry.create_from_config(config, "llm", profile="research")
        emb = registry.create_from_config(config, "embedding", profile="research")

        self.assertEqual(llm.model, "m1")
        self.assertEqual(emb.model, "e1")
        self.assertEqual(llm.api_key, "abc")
        self.assertEqual(emb.api_key, "abc")

    def test_unknown_provider(self) -> None:
        config = {"profiles": {"research": {"llm": {"active": "missing", "providers": {}}}}}
        registry = ModelRegistry()
        with self.assertRaises(KeyError):
            registry.create_from_config(config, "llm", profile="research")
