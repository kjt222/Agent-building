import unittest

from agent.policy import PolicyEngine


class TestPolicyEngine(unittest.TestCase):
    def test_allow_confirm_deny(self) -> None:
        policy = {
            "default": "deny",
            "allow": ["tool.docx_*", "tool.xlsx_*"],
            "confirm": ["tool.web_*"],
            "deny": ["tool.docx_delete"],
        }
        engine = PolicyEngine(policy)

        self.assertEqual(engine.check("tool.docx_edit").status, "allow")
        self.assertEqual(engine.check("tool.web_search").status, "confirm")
        self.assertEqual(engine.check("tool.docx_delete").status, "deny")
        self.assertEqual(engine.check("tool.unknown").status, "deny")
