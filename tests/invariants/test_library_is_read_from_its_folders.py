"""Nothing reads the 2.x library root but the seed and the fallback before it has run."""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from tests.support import trees

REPO = Path(__file__).resolve().parents[2]

SCANNED = ("common", "manager", "httpapi", "frontend", "extensions", "apps")
TOP_LEVEL = ("cli.py", "main.py")
NAMES = frozenset({"game_root_dir", "gamerootdir", "tablerootdir", "get_games_path"})

# Where the setting is declared, renamed and defined.
ALLOWED_FILES = frozenset({"common/config/paths.py", "common/config/config_schema.py",
                           "common/config/config_store.py", "common/deprecations.py"})
# And the two reads that remain: the seed, and the fallback before it has run.
ALLOWED_FUNCTIONS = frozenset({("common/games/library_folders.py", "seed")})


def _mentions(node: ast.AST) -> bool:
    if isinstance(node, ast.Name):
        return node.id in NAMES
    if isinstance(node, ast.Attribute):
        return node.attr in NAMES
    if isinstance(node, ast.alias):
        return node.name in NAMES
    return isinstance(node, ast.Constant) and isinstance(node.value, str) \
        and node.value in NAMES


def _offenders(rel: str, tree: ast.AST) -> list[str]:
    allowed = [(node.lineno, node.end_lineno or node.lineno) for node in ast.walk(tree)
               if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
               and (rel, node.name) in ALLOWED_FUNCTIONS]
    found = []
    for node in ast.walk(tree):
        line = getattr(node, "lineno", 0)
        if _mentions(node) and not any(start <= line <= end for start, end in allowed):
            found.append(f"{rel}:{line}")
    return found


def _sources():
    for folder in SCANNED:
        yield from sorted(trees.source_files(REPO / folder))
    for name in TOP_LEVEL:
        yield REPO / name


class LibraryIsReadFromItsFoldersTests(unittest.TestCase):
    def test_nothing_else_reads_the_2x_root(self) -> None:
        offenders = []
        for path in _sources():
            rel = path.relative_to(REPO).as_posix()
            if rel not in ALLOWED_FILES:
                offenders += _offenders(rel, trees.tree_for(path))
        self.assertEqual(offenders, [], "read the library folders instead: "
                         "common.games.library_folders.configured()")

    def test_it_would_see_a_reader(self) -> None:
        """A check that finds nothing looks the same as one that cannot find anything."""
        planted = trees.parse_snippet(
            "from common.config.paths import get_games_path\n"
            "root = settings.game_root_dir\n"
            "cfg_get(config, 'Settings', 'tablerootdir')\n")
        self.assertEqual(len(_offenders("common/elsewhere.py", planted)), 3)


if __name__ == "__main__":
    unittest.main()
