import tempfile
import unittest
from pathlib import Path

from agent.tools import create_snapshot, list_snapshots, restore_snapshot


class TestSnapshots(unittest.TestCase):
    def test_create_list_restore(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "note.txt"
            target.write_text("hello", encoding="utf-8")

            snapshot = create_snapshot(target, note="unit-test")
            self.assertTrue(snapshot.file_path.exists())

            target.write_text("changed", encoding="utf-8")
            restore_snapshot(target, snapshot.snapshot_id)
            self.assertEqual(target.read_text(encoding="utf-8"), "hello")

            snapshots = list_snapshots(target)
            self.assertEqual(len(snapshots), 1)
            self.assertEqual(snapshots[0].snapshot_id, snapshot.snapshot_id)
