"""Every path a user types is asked for through `panel.path_field`."""

from __future__ import annotations

import ast
import json
import pathlib
import re
import unittest

from tests.support import trees

REPO = pathlib.Path(__file__).resolve().parent.parent.parent
MANAGER = REPO / "manager"
ABOUT_A_PATH = re.compile(r"\b(path|folder|directory)\b", re.I)


def _catalog() -> dict[str, str]:
    return json.loads(
        (REPO / "common/i18n/catalogs/en.json").read_text(encoding="utf-8"))


def _placeholder(node: ast.AST, catalog: dict[str, str]) -> str:
    """A placeholder's words, whether written at the call or named as a catalog key."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if (isinstance(node, ast.Call) and getattr(node.func, "id", "") == "t"
            and node.args and isinstance(node.args[0], ast.Constant)):
        key = str(node.args[0].value)
        return str(catalog.get(key, key))
    return ""


def _bare_path_inputs() -> list[str]:
    catalog = _catalog()
    found = []
    for path in sorted(MANAGER.rglob("*.py")):
        tree = trees.tree_for(path)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call)
                    and getattr(node.func, "attr", "") == "input"):
                continue
            for keyword in node.keywords:
                if keyword.arg != "placeholder":
                    continue
                said = _placeholder(keyword.value, catalog)
                if said and ABOUT_A_PATH.search(said):
                    found.append(f"manager/{path.name}:{node.lineno}: asks for a path "
                                 f"({said!r}) on a plain input, so nothing says whether "
                                 "it is there - panel.path_field does")
    return found


class ATypedPathIsChecked(unittest.TestCase):

    def test_none_is_asked_for_on_a_plain_input(self):
        bare = _bare_path_inputs()
        self.assertEqual(bare, [], "\n" + "\n".join(bare))

    def test_the_detector_reads_both_spellings(self):
        """A clean tree and a broken detector look identical, so exercise it on both
        ways a placeholder is written."""
        catalog = _catalog()
        written = trees.parse_snippet('ui.input(placeholder="/path/to/your/games")')
        named = trees.parse_snippet(
            'ui.input(placeholder=t("manager.setup.tables.folder_placeholder"))')
        for tree in (written, named):
            call = next(n for n in ast.walk(tree) if isinstance(n, ast.Call)
                        and getattr(n.func, "attr", "") == "input")
            said = _placeholder(call.keywords[0].value, catalog)
            self.assertTrue(ABOUT_A_PATH.search(said), said)

    def test_it_found_the_inputs(self):
        """An empty sweep passes and measures nothing, which reads the same as clean."""
        seen = sum(1
                   for path in MANAGER.rglob("*.py")
                   for node in ast.walk(trees.tree_for(path))
                   if isinstance(node, ast.Call)
                   and getattr(node.func, "attr", "") == "input")
        self.assertGreater(seen, 5)


if __name__ == "__main__":
    unittest.main()
