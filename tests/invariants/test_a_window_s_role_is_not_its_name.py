"""Nothing of ours decides what a window does by comparing its name to a window name.

A comparison is seen when one side is a window name of either contract and the other is
named for one (`window_name`, `win_name`, `windowName`). A name held under no such
variable, such as a tuple's first item, is not seen. `canonical(...)` compared to a name
is how a role is asked for, so it passes.
"""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

from frontend import theme_windows
from tests.support import trees
from tests.support.project import project_files

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

NAMES = frozenset(name for names in theme_windows.DEFAULT_WINDOWS.values() for name in names)
NAMED_FOR_A_WINDOW = re.compile(r"win(?:dow)?_?name", re.IGNORECASE)
_NAME = "|".join(sorted(NAMES))
SCRIPT_COMPARISON = re.compile(
    rf"""([\w.$]+)\s*[!=]==?\s*["'](?:{_NAME})["']"""
    rf"""|["'](?:{_NAME})["']\s*[!=]==?\s*([\w.$]+)""")

SKIP_PARTS = {"build", "third_party", "chromium", "node_modules", "tests"}


def _files(*patterns: str):
    for pattern in patterns:
        for path in project_files(pattern):
            rel = path.relative_to(REPO_ROOT).as_posix()
            if not any(part in SKIP_PARTS for part in rel.split("/")):
                yield path, rel


def _names(node: ast.expr) -> set[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return {node.value}
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return {one.value for one in node.elts
                if isinstance(one, ast.Constant) and isinstance(one.value, str)}
    return set()


def _asks_for_a_role(node: ast.expr) -> bool:
    return (isinstance(node, ast.Call)
            and (getattr(node.func, "id", "") or getattr(node.func, "attr", "")) == "canonical")


def python_offenders(tree: ast.Module, source: str, rel: str) -> list[str]:
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        sides = [node.left, *node.comparators]
        for left, right in zip(sides, sides[1:], strict=False):
            for named, literal in ((left, right), (right, left)):
                if not _names(literal) & NAMES or _asks_for_a_role(named):
                    continue
                if NAMED_FOR_A_WINDOW.search(ast.get_source_segment(source, named) or ""):
                    found.append(f"{rel}:{node.lineno}: {ast.get_source_segment(source, node)}")
    return found


def script_offenders(source: str, rel: str) -> list[str]:
    found = []
    for line_no, line in enumerate(source.splitlines(), 1):
        for match in SCRIPT_COMPARISON.finditer(line):
            if NAMED_FOR_A_WINDOW.search(match.group(1) or match.group(2) or ""):
                found.append(f"{rel}:{line_no}: {line.strip()[:96]}")
    return found


class WindowRoleTests(unittest.TestCase):
    def test_no_window_s_role_comes_from_comparing_its_name(self) -> None:
        offenders = []
        for path, rel in _files("*.py"):
            source = path.read_text(encoding="utf-8")
            offenders += python_offenders(trees.tree_for(path), source, rel)
        for path, rel in _files("*.js", "*.html"):
            offenders += script_offenders(path.read_text(encoding="utf-8", errors="ignore"), rel)

        self.assertEqual(offenders, [], "\n" + "\n".join(offenders))

    def test_the_checks_can_fail(self) -> None:
        def offenders(source: str) -> list[str]:
            return python_offenders(trees.parse_snippet(source), source, "sample.py")

        self.assertTrue(offenders('if window_name == "table": pass'))
        self.assertTrue(offenders('x = win_name in ("bg", "dmd")'))
        self.assertTrue(script_offenders('const x = windowName === "playfield";', "a.js"))
        self.assertFalse(offenders('if kind == "table": pass'))
        self.assertFalse(offenders('if canonical(window_name) == "playfield": pass'))


if __name__ == "__main__":
    unittest.main()
