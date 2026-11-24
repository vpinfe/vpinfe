"""A section heading or a view name is a word, never the key it was looked up under.

`t()` returns the key it was given when the catalog has no entry, so a heading with no
entry draws itself on screen instead of failing anywhere a test would see.
"""

from __future__ import annotations

import ast
import json
import pathlib
import unittest

from tests.support import trees

REPO = pathlib.Path(__file__).resolve().parent.parent.parent
CATALOG = REPO / "common/i18n/catalogs/en.json"
MANAGER = REPO / "manager"

# Where a heading is declared: the keyword a column carries, and the two dicts of view
# presets whose keys are what the view picker prints.
GROUP_KEYWORD = "group"
PRESET_NAMES = ("GAME_VIEWS", "TABLE_VIEWS")


def _entries() -> dict[str, object]:
    return json.loads(CATALOG.read_text(encoding="utf-8"))


def _string_constants(tree: ast.Module) -> dict[str, str]:
    found = {}
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)):
            found[node.targets[0].id] = node.value.value
    return found


def _looked_up_key(node: ast.AST, consts: dict[str, str]) -> str | None:
    """The catalog key a `t(...)` call asks for, or None if this is not one."""
    if not isinstance(node, ast.Call):
        return None
    name = getattr(node.func, "id", getattr(node.func, "attr", ""))
    if name != "t" or not node.args:
        return None
    first = node.args[0]
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return first.value
    if isinstance(first, ast.Name):
        return consts.get(first.id)
    return None


def _headings() -> list[tuple[str, int, str, str]]:
    out = []
    for path in sorted(MANAGER.rglob("*.py")):
        tree = trees.tree_for(path)
        consts = _string_constants(tree)
        where = path.relative_to(REPO).as_posix()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                for keyword in node.keywords:
                    if keyword.arg != GROUP_KEYWORD:
                        continue
                    key = _looked_up_key(keyword.value, consts)
                    if key:
                        out.append((where, node.lineno, "column group", key))
                    elif isinstance(keyword.value, ast.Name):
                        # The bare constant, which is the key rather than the word.
                        out.append((where, node.lineno, "column group",
                                    consts.get(keyword.value.id, "")))
            if isinstance(node, ast.Assign) and any(
                    isinstance(t, ast.Name) and t.id in PRESET_NAMES
                    for t in node.targets):
                if isinstance(node.value, ast.Dict):
                    for entry in node.value.keys:
                        key = _looked_up_key(entry, consts) if entry else None
                        if key:
                            out.append((where, node.lineno, "view name", key))
    return out


class HeadingsResolve(unittest.TestCase):

    def test_every_heading_is_in_the_catalog(self):
        catalog = _entries()
        missing = [f"{where}:{line}: {what} {key!r} has no catalog entry, "
                   f"so it draws as {key!r}"
                   for where, line, what, key in _headings()
                   if key and key not in catalog]
        self.assertEqual(missing, [], "\n" + "\n".join(missing))

    def test_it_found_something_to_check(self):
        """An empty sweep passes and measures nothing, which looks the same as clean."""
        self.assertGreater(len(_headings()), 5)

    def test_it_can_actually_fail(self):
        catalog = _entries()
        self.assertNotIn("manager.games.assets", catalog)
        planted = [("manager/library/games.py", 44, "column group", "manager.games.assets")]
        missing = [key for _, _, _, key in planted if key not in catalog]
        self.assertEqual(["manager.games.assets"], missing)


if __name__ == "__main__":
    unittest.main()
