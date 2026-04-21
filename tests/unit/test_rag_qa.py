import unittest

from agent.rag.qa import NO_CONTEXT_MESSAGE, answer_question, build_context, build_prompt
from agent.rag.store import SearchResult


class DummyLLM:
    def __init__(self) -> None:
        self.last_prompt = None

    def chat(self, prompt: str) -> str:
        self.last_prompt = prompt
        return "ok"


class TestRagQa(unittest.TestCase):
    def test_build_context_truncates(self) -> None:
        results = [
            SearchResult(
                doc_id="d1",
                text="alpha beta gamma",
                metadata={"source_path": "C:\\a.txt"},
                score=0.9,
            ),
            SearchResult(
                doc_id="d2",
                text="delta epsilon zeta",
                metadata={"source_path": "C:\\b.txt"},
                score=0.8,
            ),
        ]
        context = build_context(results, max_context_chars=50)
        self.assertIn("[1] source: C:\\a.txt", context)

    def test_answer_question_empty(self) -> None:
        llm = DummyLLM()
        answer = answer_question(
            llm=llm,
            question="test",
            results=[],
            max_context_chars=1000,
            allow_empty=False,
        )
        self.assertEqual(answer, NO_CONTEXT_MESSAGE)

    def test_answer_question_with_context(self) -> None:
        llm = DummyLLM()
        results = [
            SearchResult(
                doc_id="d1",
                text="alpha",
                metadata={"source_path": "C:\\a.txt"},
                score=0.9,
            )
        ]
        answer = answer_question(
            llm=llm,
            question="what is alpha?",
            results=results,
            max_context_chars=1000,
        )
        self.assertEqual(answer, "ok")
        self.assertIsNotNone(llm.last_prompt)
        self.assertIn("what is alpha?", llm.last_prompt)

    def test_build_prompt_includes_context(self) -> None:
        prompt = build_prompt("question?", "context block")
        self.assertIn("context block", prompt)
        self.assertIn("question?", prompt)
