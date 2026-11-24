"""An operation that writes a game's record re-reads the game afterwards.

`reread_game` goes through `refresh_game`, which announces the change. A write that only
reloads the record in place leaves the cabinet showing the list it built before.

A writer is found by what it calls, not by its name: a `MetaConfig` method that reaches
`write_config`, `persist_game_meta`, or a `game_metadata` function that reaches either.
Writes routed through `game_service` are left out, because those re-read the folder
themselves.
"""

from __future__ import annotations

import ast
import pathlib
import unittest

from tests.support import trees

REPO = pathlib.Path(__file__).resolve().parent.parent.parent
OPS = ("common/games/table_ops.py", "common/games/game_ops.py")
METADATA = REPO / "common/games/game_metadata.py"
INFO_FILE = REPO / "common/games/info_file.py"
REREAD = "reread_game"


def _calls(node: ast.AST) -> list[ast.Call]:
    return [call for call in ast.walk(node) if isinstance(call, ast.Call)]


def _called(node: ast.AST) -> set[str]:
    """Bare names this code calls: `f(...)`, not `x.f(...)`."""
    return {call.func.id for call in _calls(node) if isinstance(call.func, ast.Name)}


def _methods_called(node: ast.AST, on: str | None = None) -> set[str]:
    """Methods this code calls, `x.f(...)`, on anything or on the name `on`."""
    return {call.func.attr for call in _calls(node)
            if isinstance(call.func, ast.Attribute)
            and (on is None or (isinstance(call.func.value, ast.Name)
                                and call.func.value.id == on))}


def _functions(body: list[ast.stmt]) -> dict[str, ast.FunctionDef]:
    return {node.name: node for node in body if isinstance(node, ast.FunctionDef)}


def _module(path: pathlib.Path) -> list[ast.stmt]:
    return trees.tree_for(path).body


def _closure(functions: dict[str, ast.FunctionDef], seed: set[str], reaches) -> set[str]:
    found = set(seed)
    while more := {name for name, node in functions.items()
                   if name not in found and reaches(node, found)}:
        found |= more
    return found


def _record_writes() -> set[str]:
    """MetaConfig's methods that write the file, directly or through another."""
    cls = next(node for node in _module(INFO_FILE)
               if isinstance(node, ast.ClassDef) and node.name == "MetaConfig")
    return _closure(_functions(cls.body), {"write_config"},
                    lambda node, found: _methods_called(node, "self") & found)


def _writes(node: ast.AST, methods: set[str], functions: set[str]) -> bool:
    return bool(_methods_called(node) & methods or _called(node) & functions)


def _persisting(methods: set[str]) -> set[str]:
    """Every game_metadata function that writes a record, directly or through another."""
    return _closure(_functions(_module(METADATA)), {"persist_game_meta"},
                    lambda node, found: _writes(node, methods, found))


class TestAWrittenRecordIsReread(unittest.TestCase):
    def setUp(self) -> None:
        methods = _record_writes()
        functions = _persisting(methods)
        self.found = {f"{path}:{name}": node
                      for path in OPS
                      for name, node in _functions(_module(REPO / path)).items()
                      if _writes(node, methods, functions)}

    def test_the_writers_are_found(self) -> None:
        for expected in ("common/games/table_ops.py:set_hidden",
                         "common/games/table_ops.py:set_default",
                         "common/games/game_ops.py:set_rating"):
            self.assertIn(expected, self.found, "the walk no longer sees the writes")

    def test_every_writer_rereads_the_game(self) -> None:
        for where, node in self.found.items():
            with self.subTest(where):
                self.assertIn(REREAD, _called(node))


if __name__ == "__main__":
    unittest.main()
