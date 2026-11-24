"""The name of a kept copy is built by `common/kept_copies.py`, and read there.

`backup_path` makes one, `is_copy`, `backup_stamp` and `backup_reason` take one apart. A
second place that spells the marker writes names the first cannot read back, and the
listing in `common/backups/` would not show them.

`apps/vpx/config.py` is the one other spelling, because an app may import only the contract.
Its marker has to stay equal to the real one.
"""

from __future__ import annotations

import ast
import pathlib
import unittest

from apps.vpx import config as vpx_config
from common.kept_copies import BACKUP_MARKER
from tests.support import trees

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
SCANNED = ("common", "apps", "httpapi", "manager", "frontend", "extensions")
SPELLS_IT = frozenset({"common/kept_copies.py", "apps/vpx/config.py"})


def _docstrings(tree: ast.Module) -> set[int]:
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                found.add(id(first.value))
    return found


def _spellings(path: pathlib.Path) -> list[int]:
    tree = trees.tree_for(path)
    skipped = _docstrings(tree)
    return [node.lineno for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
            and BACKUP_MARKER in node.value and id(node) not in skipped]


class AKeptCopyIsNamedInOnePlace(unittest.TestCase):
    def test_no_other_source_spells_the_marker(self) -> None:
        found = []
        for top in SCANNED:
            for path in sorted(trees.source_files(ROOT / top)):
                relative = path.relative_to(ROOT).as_posix()
                if relative in SPELLS_IT or "__pycache__" in path.parts:
                    continue
                found += [f"{relative}:{line}" for line in _spellings(path)]

        self.assertEqual(found, [], "build and read a copy's name with "
                                    "common/kept_copies.py")

    def test_the_one_other_spelling_matches(self) -> None:
        self.assertEqual(vpx_config.ASIDE_MARKER, BACKUP_MARKER)


if __name__ == "__main__":
    unittest.main()
