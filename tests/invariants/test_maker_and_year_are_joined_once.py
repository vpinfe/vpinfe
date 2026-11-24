"""Who made a game and when is written one way, by `game_tables.made`. A join of the two
anywhere else is a second answer to how that line reads."""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from tests.support import trees
from tests.support.project import project_files

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
OWNER = "manager/kit/game_tables.py"
SKIP_PARTS = {"build", "third_party", "chromium", "tests"}
PAIR = {"manufacturer", "year"}


def _joins(tree: ast.AST) -> list[int]:
    found = []
    for node in ast.walk(tree):
        building = (isinstance(node, ast.JoinedStr)
                    or isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add)
                    or isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "join")
        if not building:
            continue
        words = {one.value for one in ast.walk(node)
                 if isinstance(one, ast.Constant) and isinstance(one.value, str)}
        if PAIR <= words and not {word for word in words - PAIR if word.strip(" ?")}:
            found.append(getattr(node, "lineno", 0))
    return found


class MakerAndYearAreJoinedOnce(unittest.TestCase):
    def test_nothing_but_the_formatter_joins_them(self) -> None:
        offenders = []
        for path in project_files("*.py"):
            rel = path.relative_to(REPO_ROOT).as_posix()
            if rel == OWNER or any(part in SKIP_PARTS for part in rel.split("/")):
                continue
            tree = trees.tree_for(path)
            offenders += [f"{rel}:{line}" for line in sorted(set(_joins(tree)))]

        self.assertEqual(offenders, [], "maker and year joined outside game_tables.made:\n  "
                                        + "\n  ".join(offenders))

    def test_the_checker_can_actually_fail(self) -> None:
        """Each shape the copies took, and the search text that is not one."""
        for source in ('" ".join(str(g.get(k) or "") for k in ("manufacturer", "year"))',
                       "f\"{g.get('manufacturer') or '?'} {g.get('year') or ''}\"",
                       'g.get("manufacturer", "") + " " + g.get("year", "")'):
            with self.subTest(source=source):
                self.assertTrue(_joins(trees.parse_snippet(source)))
        self.assertEqual(_joins(trees.parse_snippet(
            '" ".join(str(g.get(k) or "") for k in ("name", "manufacturer", "year"))')), [])


if __name__ == "__main__":
    unittest.main()
