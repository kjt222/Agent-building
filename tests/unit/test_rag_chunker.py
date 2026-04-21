import unittest

from agent.rag.chunker import split_text


class TestChunker(unittest.TestCase):
    def test_split_text_overlap(self) -> None:
        text = "abcdefg"
        chunks = split_text(text, chunk_size=4, chunk_overlap=1)
        self.assertEqual(chunks, ["abcd", "defg"])

    def test_split_text_invalid(self) -> None:
        with self.assertRaises(ValueError):
            split_text("abc", chunk_size=0, chunk_overlap=0)
        with self.assertRaises(ValueError):
            split_text("abc", chunk_size=3, chunk_overlap=3)
