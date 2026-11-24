"""Every menu item in the Manager takes the menu treatment.

One menu is one language: a row's context menu, a column's, the view's and the actions on
a selection all read alike, or the odd one out reads as a different kind of thing.
"""

from __future__ import annotations

import ast
import pathlib
import unittest

from tests.support import trees

MANAGER = pathlib.Path(__file__).resolve().parent.parent.parent / "manager"
DRAWN = ("menu_item", "item_label")
TREATMENT = "manager-menu"


def _dressed_calls(tree: ast.AST) -> set[int]:
    """The menu calls that are the receiver of a `.classes(...)` naming the treatment.

    Walked from the `.classes` call down its own chain, rather than by reading nearby
    lines: a bare item sitting beside a dressed one passes any window wide enough to
    hold both.
    """
    dressed: set[int] = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call)
                and getattr(node.func, "attr", "") == "classes"):
            continue
        # A class list built above the call is still a class list; only a literal that
        # names something else is a miss.
        literal = [a.value for a in node.args
                   if isinstance(a, ast.Constant) and isinstance(a.value, str)]
        if literal and not any(TREATMENT in a for a in literal):
            continue
        assert isinstance(node.func, ast.Attribute)
        inner = node.func.value
        while isinstance(inner, ast.Call):
            if getattr(inner.func, "attr", "") in DRAWN:
                dressed.add(id(inner))
                break
            if not isinstance(inner.func, ast.Attribute):
                break
            inner = inner.func.value
    return dressed


def _bare() -> list[str]:
    out = []
    for path in sorted(MANAGER.rglob("*.py")):
        tree = trees.tree_for(path)
        dressed = _dressed_calls(tree)
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and getattr(node.func, "attr", "") in DRAWN
                    and id(node) not in dressed):
                out.append(f"manager/{path.name}:{node.lineno}: a menu item with no "
                           "menu treatment")
    return out


class MenuItemsAreDressed(unittest.TestCase):

    def test_a_call_chained_straight_off_a_constructor_ends_the_walk(self):
        tree = trees.parse_snippet("Row(1).classes('manager-menu')\n"
                                   "ui.menu_item('x').classes('manager-menu')")
        self.assertEqual(1, len(_dressed_calls(tree)))

    def test_none_is_bare(self):
        bare = _bare()
        self.assertEqual(bare, [], "\n" + "\n".join(bare))

    def test_it_found_the_menus(self):
        """An empty sweep passes and measures nothing, which reads the same as clean."""
        seen = sum(
            1
            for path in MANAGER.rglob("*.py")
            for node in ast.walk(trees.tree_for(path))
            if isinstance(node, ast.Call) and getattr(node.func, "attr", "") in DRAWN)
        self.assertGreater(seen, 25)


if __name__ == "__main__":
    unittest.main()
