"""The bar above a grid keeps its two rows in order."""

from __future__ import annotations

import ast
import pathlib
import unittest

from tests.support import trees

MANAGER = pathlib.Path(__file__).resolve().parent.parent.parent / "manager"
KEY = "manager-tier-key"


def _ends(tree: ast.AST) -> list[ast.With]:
    """Every `with ... panel.bar_end():` block - the right-aligned end of a row."""
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.With):
            continue
        for item in node.items:
            call = item.context_expr
            if isinstance(call, ast.Call) and getattr(call.func, "attr", "") == "bar_end":
                found.append(node)
                break
    return found


def _classes_named(node: ast.AST, wanted: str) -> bool:
    return any(isinstance(inner, ast.Constant) and isinstance(inner.value, str)
               and wanted in inner.value
               for inner in ast.walk(node))


BAR_CONTAINER = "manager-grid-bar"


def _bars_without_the_container() -> list[str]:
    """A `view_control` whose bar is not drawn inside a `manager-grid-bar`."""
    loose = []
    for path in sorted(MANAGER.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        tree = trees.tree_for(path)
        calls = [node for node in ast.walk(tree)
                 if isinstance(node, ast.Call)
                 and getattr(node.func, "id", getattr(node.func, "attr", "")) ==
                 "view_control"
                 and not isinstance(node.func, ast.Attribute)]
        if not calls:
            continue
        if BAR_CONTAINER not in source:
            loose += [f"manager/{path.name}:{node.lineno}: a view control whose bar is "
                      f"not drawn in a {BAR_CONTAINER}, so its two rows sit side by side"
                      for node in calls]
    return loose


class EveryBarIsStacked(unittest.TestCase):

    def test_none_is_loose(self):
        loose = _bars_without_the_container()
        self.assertEqual(loose, [], "\n" + "\n".join(loose))

    def test_it_found_the_bars(self):
        """An empty sweep passes and measures nothing, which reads the same as clean."""
        seen = sum("view_control(" in p.read_text(encoding="utf-8")
                   for p in MANAGER.rglob("*.py"))
        self.assertGreater(seen, 5)


class TheKeyIsNotAnAction(unittest.TestCase):
    """A key to a view's marks is drawn on the description line, not in the end."""

    def test_no_key_in_a_row_end(self):
        wrong = []
        for path in sorted(MANAGER.rglob("*.py")):
            tree = trees.tree_for(path)
            for end in _ends(tree):
                if _classes_named(end, KEY):
                    wrong.append(f"manager/{path.name}:{end.lineno}: a mark key drawn "
                                 "in a bar's right-aligned end")
        self.assertEqual(wrong, [], "\n" + "\n".join(wrong))

    def test_it_found_the_keys_and_the_ends(self):
        """An empty sweep passes and measures nothing, which reads the same as clean."""
        source = "\n".join(p.read_text(encoding="utf-8")
                           for p in MANAGER.rglob("*.py"))
        self.assertGreater(source.count(KEY), 1)
        self.assertGreater(sum(len(_ends(trees.tree_for(p)))
                               for p in MANAGER.rglob("*.py")), 5)


if __name__ == "__main__":
    unittest.main()
