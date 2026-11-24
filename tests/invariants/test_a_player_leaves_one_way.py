"""A player leaves through `remove_player` alone: no other function in the tree takes one
off the roster, forgets an account or forgets a record."""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from tests.support import trees

REPO = Path(__file__).resolve().parents[2]
SOURCE_ROOTS = ("apps", "common", "manager", "extensions", "frontend", "httpapi")

THE_ONE = ("common/games/player_records.py", "remove_player")


def _receiver(call: ast.Call) -> str:
    func = call.func
    return ast.unparse(func.value) if isinstance(func, ast.Attribute) else ""


def _leaves(call: ast.Call) -> bool:
    """A roster removal, an account forget or a record forget."""
    func = call.func
    if not isinstance(func, ast.Attribute):
        return False
    receiver = _receiver(call)
    if func.attr == "remove":
        return "roster" in receiver.lower()
    if func.attr == "forget":
        return receiver == "accounts" or "records" in receiver.lower()
    return False


def _callers(path: Path, tree: ast.AST) -> set[tuple[str, str]]:
    relative = path.relative_to(REPO).as_posix() if path.is_absolute() else str(path)
    found: set[tuple[str, str]] = set()

    def visit(node: ast.AST, function: str) -> None:
        for child in ast.iter_child_nodes(node):
            inside = (child.name if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef)
                      else function)
            if isinstance(child, ast.Call) and _leaves(child):
                found.add((relative, function or "<module>"))
            visit(child, inside)

    visit(tree, "")
    return found


class APlayerLeavesOneWay(unittest.TestCase):
    def test_only_remove_player_takes_a_player_off_or_forgets_what_they_held(self) -> None:
        found: set[tuple[str, str]] = set()
        for root in SOURCE_ROOTS:
            for path in sorted(trees.source_files(REPO / root)):
                if "__pycache__" not in path.parts:
                    found |= _callers(path, trees.tree_for(path))

        self.assertEqual(found - {THE_ONE}, set(),
                         "Remove a player or sign a guest out with "
                         "player_records.remove_player, which forgets their accounts and "
                         "record too")
        self.assertIn(THE_ONE, found, "remove_player no longer does what this looks for")

    def test_the_check_sees_each_of_the_three(self) -> None:
        tree = trees.parse_snippet(
            "def a():\n    get_roster().remove(player_id)\n"
            "def b():\n    accounts.forget(player_id)\n"
            "def c():\n    get_records().forget(player_id)\n"
            "def d():\n    items.remove(one)\n    store.forget(name)\n")

        self.assertEqual(sorted(name for _, name in _callers(Path("x.py"), tree)),
                         ["a", "b", "c"])


if __name__ == "__main__":
    unittest.main()
