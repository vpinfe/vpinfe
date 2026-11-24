"""A dialog title that names something puts the name in quotes."""

from __future__ import annotations

import ast
import json
import re
import unittest
from collections.abc import Iterator
from pathlib import Path

from tests.support import trees

ROOT = Path(__file__).resolve().parents[2]
CATALOG = json.loads((ROOT / "common/i18n/catalogs/en.json").read_text(encoding="utf-8"))

NOT_A_NAME = {"len", "count", "the_count", "lower", "latest", "kind", "who", "label"}
SLOT = re.compile(r"(.?)\{(\w+)\}(.?)")


def _keys(node: ast.AST) -> Iterator[str]:
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call) and getattr(sub.func, "id", None) == "t" \
                and sub.args and isinstance(sub.args[0], ast.Constant):
            yield str(sub.args[0].value)


def _titles() -> list[tuple[str, str]]:
    """(where, key) for every catalog entry a Manager dialog uses as its title."""
    found = []
    for path in sorted((ROOT / "manager").rglob("*.py")):
        for node in ast.walk(trees.tree_for(path)):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            where = f"{path.name}:{node.lineno}"
            if node.func.attr in ("ask", "opened") \
                    and getattr(node.func.value, "id", "") in ("confirm", "dialog", "frame") \
                    and node.args:
                found += [(where, key) for key in _keys(node.args[0])]
            if node.func.attr == "classes" and node.args \
                    and "manager-confirm-title" in str(getattr(node.args[0], "value", "")) \
                    and isinstance(node.func.value, ast.Call) and node.func.value.args:
                found += [(where, key) for key in _keys(node.func.value.args[0])]
    return found


class ADialogTitle(unittest.TestCase):
    def test_the_titles_are_found(self) -> None:
        keys = {key for _, key in _titles()}
        self.assertGreater(len(keys), 30)
        self.assertIn("manager.vps_match.match_named", keys)

    def test_a_name_in_a_title_is_quoted(self) -> None:
        unquoted = []
        for where, key in _titles():
            said = CATALOG.get(key, "")
            said = " ".join(said.values()) if isinstance(said, dict) else str(said)
            for before, slot, after in SLOT.findall(said):
                # `setting{value}` is a plural ending, not a name.
                if slot in NOT_A_NAME or before.isalpha():
                    continue
                if (before, after) != ("“", "”"):
                    unquoted.append(f"{where} {key}: {said}")
        self.assertEqual([], unquoted)


if __name__ == "__main__":
    unittest.main()
