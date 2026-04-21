import os
import unittest

from agent.credentials import describe_key, resolve_api_key


class TestCredentials(unittest.TestCase):
    def test_resolve_prefers_env(self) -> None:
        os.environ["TEST_KEY_ENV"] = "secret1234"
        value = resolve_api_key(api_key_env="TEST_KEY_ENV", api_key_ref=None, prefer_env=True)
        self.assertEqual(value, "secret1234")

    def test_describe_env_key(self) -> None:
        os.environ["TEST_KEY_ENV2"] = "abcd9876"
        status = describe_key(api_key_env="TEST_KEY_ENV2", api_key_ref=None)
        self.assertTrue(status.present)
        self.assertIn("****", status.masked)
