import subprocess
import tempfile
import unittest
from pathlib import Path

from tests import affected, stamp


class TreeHashTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.git("init", "-q")
        (self.repo / ".gitignore").write_text("skipped.txt\n", encoding="utf-8")
        (self.repo / "a.py").write_text("a = 1\n", encoding="utf-8")
        self.git("add", "a.py")

    def git(self, *args: str) -> str:
        run = subprocess.run(["git", "-C", str(self.repo), *args], check=True,
                             capture_output=True, text=True, env=affected.git_env())
        return run.stdout.strip()

    def test_it_matches_the_tree_that_staging_everything_would_commit(self) -> None:
        before = stamp.tree_hash(self.repo)
        self.git("add", "-A")
        self.assertEqual(before, self.git("write-tree"))

    def test_it_leaves_the_real_index_alone(self) -> None:
        stamp.tree_hash(self.repo)
        self.assertEqual(self.git("ls-files"), "a.py")

    def test_an_edit_and_an_untracked_file_each_change_it(self) -> None:
        clean = stamp.tree_hash(self.repo)
        (self.repo / "a.py").write_text("a = 2\n", encoding="utf-8")
        edited = stamp.tree_hash(self.repo)
        (self.repo / "b.py").write_text("b = 1\n", encoding="utf-8")
        self.assertEqual(len({clean, edited, stamp.tree_hash(self.repo)}), 3)

    def test_an_ignored_file_does_not(self) -> None:
        clean = stamp.tree_hash(self.repo)
        (self.repo / "skipped.txt").write_text("x\n", encoding="utf-8")
        self.assertEqual(stamp.tree_hash(self.repo), clean)

    def test_a_recorded_hash_is_one_line_in_the_git_directory(self) -> None:
        stamp.record(self.repo, "abc123")
        self.assertEqual((self.repo / ".git" / stamp.STAMP).read_text(encoding="utf-8"),
                         "abc123\n")
