"""A test that reads the whole tree takes its files from `project_files`, never from a glob
of the repo root."""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

from tests.support import trees
from tests.support.project import REPO, project_files

ROOTWARD = re.compile(r"(?:pathlib\.)?Path\(__file__\)\.resolve\(\)"
                      r"(?P<parents>(?:\.parent)+|\.parents\[(?P<index>\d+)\])")


def _root_names(tree: ast.Module, path: Path) -> set[str]:
    """The module-level names bound to the repo root, reckoned from `path`."""
    names = set()
    for node in tree.body:
        if not (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)):
            continue
        spelled = ROOTWARD.fullmatch(ast.unparse(node.value))
        if spelled is None:
            continue
        up = (int(spelled["index"]) if spelled["index"] is not None
              else spelled["parents"].count(".parent") - 1)
        if up < len(path.parents) and path.parents[up] == REPO:
            names.add(node.targets[0].id)
    return names


def root_globs(tree: ast.Module, path: Path) -> list[int]:
    """The line of every `glob` or `rglob` called on the repo root."""
    roots = _root_names(tree, path)
    return [node.lineno for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"glob", "rglob"}
            and isinstance(node.func.value, ast.Name) and node.func.value.id in roots]


class WholeTreeChecks(unittest.TestCase):
    def test_no_test_globs_the_repo_root(self) -> None:
        offenders = []
        for path in project_files("*.py"):
            if path.relative_to(REPO).parts[0] != "tests":
                continue
            offenders += [f"{path.relative_to(REPO).as_posix()}:{line}"
                          for line in root_globs(trees.tree_for(path), path)]

        self.assertEqual(offenders, [], "take the files from project_files instead")

    def test_it_knows_the_root_when_it_sees_one(self) -> None:
        """An empty sweep passes and measures nothing, which reads the same as clean."""
        roots = [path for path in project_files("test_*.py")
                 if _root_names(trees.tree_for(path), path)]

        self.assertGreaterEqual(len(roots), 10)

    def test_each_spelling_of_the_root_is_caught_and_a_package_is_not(self) -> None:
        here = REPO / "tests" / "invariants" / "test_x.py"
        tree = trees.parse_snippet(
            "REPO = Path(__file__).resolve().parent.parent.parent\n"
            "ROOT = pathlib.Path(__file__).resolve().parents[2]\n"
            "TESTS = Path(__file__).resolve().parents[1]\n"
            "REPO.rglob('*.py')\n"
            "ROOT.glob('*/**/*.py')\n"
            "TESTS.rglob('test_*.py')\n"
            "(REPO / 'manager').rglob('*.py')\n")

        self.assertEqual(root_globs(tree, here), [4, 5])


if __name__ == "__main__":
    unittest.main()
