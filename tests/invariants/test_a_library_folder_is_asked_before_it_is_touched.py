"""A function that walks the library folders asks `path_state` about them before it touches
one. A stat on a share that has gone never returns and cannot be cancelled, so the question
has to be one that carries its own time limit. A function that asks about one path and
touches another passes: this reads which calls a function makes, not what they are given."""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from tests.support import trees

REPO = Path(__file__).resolve().parents[2]
SCANNED = ("common", "manager", "httpapi", "frontend", "extensions", "apps")

# Lists the library folders and also touches a path that is on this machine for certain.
EXEMPT = frozenset({("httpapi/metrics.py", "_watched")})

# What gives a person the library folders.
LISTS = frozenset({"configured", "locations"})
# What reads a path off the disk.
TOUCHES = frozenset({"is_dir", "is_file", "exists", "iterdir", "stat", "lstat", "listdir",
                     "scandir", "walk", "glob", "rglob", "disk_usage", "isdir", "isfile",
                     "realpath", "resolve", "canonical", "access", "samefile", "open",
                     "read_text", "read_bytes", "rmtree", "copytree"})
# What asks with a limit.
ASKS = frozenset({"look", "look_all", "states_of", "answering"})


def _called(function: ast.AST) -> set[str]:
    return {node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id
            for node in ast.walk(function)
            if isinstance(node, ast.Call)
            and isinstance(node.func, (ast.Attribute, ast.Name))}


def _offenders() -> tuple[int, list[str]]:
    walked = 0
    found = []
    for folder in SCANNED:
        for path in sorted(trees.source_files(REPO / folder)):
            rel = path.relative_to(REPO).as_posix()
            for node in ast.walk(trees.tree_for(path)):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                called = _called(node)
                if not called & LISTS:
                    continue
                walked += 1
                if called & TOUCHES and not called & ASKS \
                        and (rel, node.name) not in EXEMPT:
                    found.append(f"{rel}:{node.lineno} {node.name}")
    return walked, found


class TestALibraryFolderIsAskedBeforeItIsTouched(unittest.TestCase):
    def test_a_function_that_lists_the_folders_and_touches_the_disk_asks_first(self) -> None:
        walked, offenders = _offenders()
        self.assertGreater(walked, 8)
        self.assertEqual(offenders, [],
                         "ask `path_state.look`, `look_all` or `library_folders.states_of` "
                         "before touching a library folder")


if __name__ == "__main__":
    unittest.main()
