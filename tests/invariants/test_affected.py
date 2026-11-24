"""`python -m tests --affected` picks the modules a change reaches, and falls back to all."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from fnmatch import fnmatch
from pathlib import Path

from tests import affected

REPO = Path(affected.__file__).resolve().parents[1]
FILES = {
    "common/low.py": "",
    "common/mid.py": "import common.low\n",
    "manager/page.py": "from common import mid\n",
    "common/alone.py": "",
    "tests/test_low.py": "import common.low\n",
    "tests/test_page.py": "import manager.page\n",
    "tests/test_unrelated.py": "import unittest\n",
    "tests/support/helper.py": "",
    "tests/test_uses_helper.py": "from tests.support import helper\n",
    "pkg/__init__.py": "",
    "pkg/a.py": "",
    "pkg/b.py": "from . import a\n",
    "pkg/sub/__init__.py": "",
    "pkg/sub/c.py": "from ..a import thing\n",
    "tests/test_b.py": "import pkg.b\n",
    "tests/test_c.py": "from pkg.sub import c\n",
    "tests/test_package.py": "import pkg.a\n",
}
AREAS = [{"paths": ["data/*.json"], "tests": ["tests/test_page.py"]},
         {"paths": ["docs/*", "*.md"], "tests": []}]


class SelectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = Path(self.enterContext(tempfile.TemporaryDirectory()))
        for name, source in FILES.items():
            (self.repo / name).parent.mkdir(parents=True, exist_ok=True)
            (self.repo / name).write_text(source, encoding="utf-8")

    def pick(self, *changed: str) -> affected.Selection:
        return affected.select(self.repo, list(changed), list(FILES), AREAS)

    def test_the_invariants_always_run(self) -> None:
        self.assertEqual(self.pick("docs/page").paths, [affected.INVARIANTS])

    def test_a_changed_module_selects_who_imports_it_and_who_imports_its_importers(self) -> None:
        picked = self.pick("common/low.py")
        self.assertFalse(picked.whole_suite)
        self.assertEqual(picked.paths, [affected.INVARIANTS, "tests/test_low.py"])
        self.assertEqual(self.pick("common/mid.py").paths,
                         [affected.INVARIANTS, "tests/test_page.py"])

    def test_the_hop_goes_through_a_module_that_has_no_test_of_its_own(self) -> None:
        files = {**FILES, "tests/test_mid_user.py": "import common.mid\n"}
        for name, source in files.items():
            (self.repo / name).write_text(source, encoding="utf-8")
        picked = affected.select(self.repo, ["common/low.py"], list(files), AREAS)
        self.assertEqual(picked.paths, [affected.INVARIANTS, "tests/test_low.py",
                                        "tests/test_mid_user.py"])

    def test_a_changed_test_selects_itself(self) -> None:
        self.assertEqual(self.pick("tests/test_unrelated.py").paths,
                         [affected.INVARIANTS, "tests/test_unrelated.py"])

    def test_a_changed_helper_selects_the_tests_that_import_it(self) -> None:
        self.assertEqual(self.pick("tests/support/helper.py").paths,
                         [affected.INVARIANTS, "tests/test_uses_helper.py"])

    def test_a_data_file_selects_what_the_map_names(self) -> None:
        self.assertEqual(self.pick("data/a.json").paths,
                         [affected.INVARIANTS, "tests/test_page.py"])

    def test_prose_selects_nothing_beyond_the_invariants(self) -> None:
        picked = self.pick("README.md", "docs/a/page", "releases/3.0")
        self.assertEqual((picked.paths, picked.whole_suite), ([affected.INVARIANTS], False))

    def test_the_tests_an_extension_carries_are_picked_like_any_others(self) -> None:
        files = {**FILES, "extensions/alpha/__init__.py": "",
                 "extensions/alpha/tool.py": "import common.mid\n",
                 "extensions/alpha/tests/__init__.py": "",
                 "extensions/alpha/tests/test_tool.py": "from extensions.alpha import tool\n"}
        for name, source in files.items():
            (self.repo / name).parent.mkdir(parents=True, exist_ok=True)
            (self.repo / name).write_text(source, encoding="utf-8")
        mine = "extensions/alpha/tests/test_tool.py"

        for changed in ("extensions/alpha/tests/test_tool.py", "extensions/alpha/tool.py",
                        "common/mid.py"):
            with self.subTest(changed=changed):
                picked = affected.select(self.repo, [changed], list(files), AREAS)
                self.assertFalse(picked.whole_suite)
                self.assertIn(mine, picked.paths)
        self.assertNotIn(mine, affected.select(self.repo, ["common/alone.py"], list(files),
                                               AREAS).paths)

    def test_a_module_no_test_reaches_selects_the_whole_suite_and_says_which(self) -> None:
        picked = self.pick("common/alone.py")
        self.assertTrue(picked.whole_suite)
        self.assertEqual(picked.notes, ["common/alone.py: no test is mapped to it"])

    def test_a_floor_area_adds_its_tests_without_accounting_for_the_file(self) -> None:
        floor = [{"paths": ["common/*"], "tests": ["tests/test_unrelated.py"], "floor": True}]
        reached = affected.select(self.repo, ["common/low.py"], list(FILES), floor)
        self.assertEqual(reached.paths, [affected.INVARIANTS, "tests/test_low.py",
                                         "tests/test_unrelated.py"])
        self.assertFalse(reached.whole_suite)
        alone = affected.select(self.repo, ["common/alone.py"], list(FILES), floor)
        self.assertTrue(alone.whole_suite)
        self.assertIn("tests/test_unrelated.py", alone.paths)

    def test_a_file_the_map_does_not_know_selects_the_whole_suite(self) -> None:
        self.assertTrue(self.pick("static/app.css").whole_suite)

    def test_a_relative_import_is_an_import(self) -> None:
        self.assertEqual(self.pick("pkg/a.py").paths,
                         [affected.INVARIANTS, "tests/test_b.py", "tests/test_c.py",
                          "tests/test_package.py"])

    def test_a_package_s_init_is_reached_by_whoever_imports_a_module_in_it(self) -> None:
        picked = self.pick("pkg/__init__.py")
        self.assertFalse(picked.whole_suite)
        self.assertEqual(picked.paths, [affected.INVARIANTS, "tests/test_b.py",
                                        "tests/test_c.py", "tests/test_package.py"])

    def test_a_test_that_is_gone_has_nothing_to_run(self) -> None:
        picked = affected.select(self.repo, ["tests/test_gone.py"], list(FILES), AREAS)
        self.assertEqual((picked.paths, picked.whole_suite), ([affected.INVARIANTS], False))

    def test_an_area_test_that_is_not_on_disk_is_dropped(self) -> None:
        missing = [{"paths": ["data/*.json"], "tests": ["tests/test_missing.py"]}]
        picked = affected.select(self.repo, ["data/a.json"], list(FILES), missing)
        self.assertEqual(picked.paths, [affected.INVARIANTS])


class ChangedFileTests(unittest.TestCase):
    def test_it_lists_the_working_tree_and_untracked_files_since_a_revision(self) -> None:
        repo = Path(self.enterContext(tempfile.TemporaryDirectory()))

        def git(*args: str) -> None:
            subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c",
                            "user.email=t@t", "-c", "commit.gpgsign=false", "-c",
                            "core.hooksPath=/dev/null", *args], check=True,
                           capture_output=True, env=affected.git_env())
        git("init", "-q")
        (repo / "kept.py").write_text("a = 1\n", encoding="utf-8")
        (repo / "edited.py").write_text("a = 1\n", encoding="utf-8")
        git("add", ".")
        git("commit", "-q", "-m", "first")
        (repo / "edited.py").write_text("a = 2\n", encoding="utf-8")
        (repo / "new.py").write_text("b = 1\n", encoding="utf-8")
        self.assertEqual(affected.changed_files(repo, "HEAD"), ["edited.py", "new.py"])
        git("commit", "-q", "-a", "-m", "second")
        self.assertEqual(affected.changed_files(repo, "HEAD"), ["new.py"])
        self.assertEqual(affected.changed_files(repo, "HEAD~1"), ["edited.py", "new.py"])

    def test_an_extension_that_is_its_own_repository_adds_its_own_changes(self) -> None:
        repo = Path(self.enterContext(tempfile.TemporaryDirectory()))
        clone = repo / "extensions" / "alpha"
        clone.mkdir(parents=True)

        def git(where: Path, *args: str) -> None:
            subprocess.run(["git", "-C", str(where), "-c", "user.name=t", "-c",
                            "user.email=t@t", "-c", "commit.gpgsign=false", "-c",
                            "core.hooksPath=/dev/null", *args], check=True,
                           capture_output=True, env=affected.git_env())
        git(repo, "init", "-q")
        (repo / ".gitignore").write_text("extensions/\n", encoding="utf-8")
        (repo / "kept.py").write_text("a = 1\n", encoding="utf-8")
        git(repo, "add", ".")
        git(repo, "commit", "-q", "-m", "first")
        git(clone, "init", "-q")
        (clone / "tool.py").write_text("a = 1\n", encoding="utf-8")
        git(clone, "add", ".")
        git(clone, "commit", "-q", "-m", "first")
        (clone / "tool.py").write_text("a = 2\n", encoding="utf-8")
        (clone / "new.py").write_text("b = 1\n", encoding="utf-8")

        self.assertEqual(affected.changed_files(repo, "HEAD"),
                         ["extensions/alpha/new.py", "extensions/alpha/tool.py"])
        self.assertEqual(affected.python_files(repo),
                         ["kept.py", "extensions/alpha/new.py", "extensions/alpha/tool.py"])

    def test_a_revision_that_names_nothing_is_an_error_with_git_s_words(self) -> None:
        with self.assertRaises(ValueError) as caught:
            affected.changed_files(REPO, "no-such-revision")
        self.assertIn("no-such-revision", str(caught.exception))


class MapTests(unittest.TestCase):
    def test_every_area_names_files_that_exist(self) -> None:
        for area in affected.read_map():
            for path in area["tests"]:
                if path.startswith("extensions/") and not (
                        REPO / path.split("/tests/")[0]).is_dir():
                    continue
                self.assertTrue((REPO / path).is_file(), path)
            self.assertTrue(area["paths"], area)

    def test_every_area_pattern_matches_a_file_in_the_tree(self) -> None:
        tree = affected._git(REPO, "ls-files", "--cached", "--others", "--exclude-standard")
        for area in affected.read_map():
            for pattern in area["paths"]:
                if pattern.startswith("extensions/"):
                    continue
                self.assertTrue(any(fnmatch(name, pattern) for name in tree), pattern)

    def test_what_a_test_reads_without_importing_it_selects_that_test(self) -> None:
        def reaches(changed: str) -> list[str]:
            return affected.select(REPO, [changed], [], affected.read_map()).paths
        self.assertIn("tests/js/test_frontend_suite.py", reaches("docs/theme.md"))
        self.assertIn("tests/js/test_frontend_suite.py", reaches("tests/js/words.test.js"))
        self.assertIn("tests/js/test_frontend_suite.py",
                      reaches("frontend/static/common/vpinfe-core.js"))
        self.assertIn("tests/extensions/test_guide.py", reaches("docs/extensions/tutorial.md"))
        self.assertEqual(reaches("docs/conventions.md"), [affected.INVARIANTS])

    def test_every_module_that_opens_a_browser_is_in_some_area(self) -> None:
        mapped = {path for area in affected.read_map() for path in area["tests"]}
        found = [*(REPO / "tests").rglob("test_*.py"),
                 *(REPO / "extensions").glob("*/tests/test_*.py")]
        drives = {path.relative_to(REPO).as_posix() for path in found
                  if "BrowserSession(" in path.read_text(encoding="utf-8")
                  and "invariants" not in path.parts}
        self.assertEqual(sorted(drives - mapped), [])


if __name__ == "__main__":
    unittest.main()
