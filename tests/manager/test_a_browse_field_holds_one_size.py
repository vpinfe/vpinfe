"""The marks at the end of a field with Browse are one size.

A field that draws Browse wears the class the sizing rule is keyed on, or its error mark,
tick and folder glyph stop matching. The same class hides the tick while Quasar's error
mark is up and centers the tick holder on the Browse button.
"""

from __future__ import annotations

import ast
import pathlib
import unittest

from tests.support import trees
from tests.support.stylesheet import declarations

CLASS = "manager-adorned"
MANAGER = pathlib.Path(__file__).resolve().parent.parent.parent / "manager"


def _says_the_class(function: ast.AST) -> bool:
    """Whether the class reaches a `.classes(...)` call, not merely appears in a string."""
    return any(isinstance(call, ast.Call) and getattr(call.func, "attr", "") == "classes"
               and any(isinstance(one, ast.Constant) and isinstance(one.value, str)
                       and CLASS in one.value for one in ast.walk(call))
               for call in ast.walk(function))


BROWSE_DRAWERS = ("browse_button", "path_buttons")


def _draws_browse(function: ast.AST) -> bool:
    return any(isinstance(node, ast.Call)
               and getattr(node.func, "id", getattr(node.func, "attr", "")) in BROWSE_DRAWERS
               for node in ast.walk(function))


def _browse_fields() -> list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef, bool]]:
    """Each top-level function that draws a Browse button, and whether it names the class.

    Top-level only: `panel.field` draws Browse inside a nested `draw`, and the class is
    set in that same function, so the outer one answers for both.
    """
    found = []
    for path in sorted(MANAGER.rglob("*.py")):
        for node in trees.tree_for(path).body:
            if (isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
                    and node.name not in BROWSE_DRAWERS
                    and _draws_browse(node)):
                found.append((f"manager/{path.name}:{node.lineno} {node.name}", node,
                              _says_the_class(node)))
    return found


class ABrowseFieldHoldsOneSize(unittest.TestCase):
    def test_the_class_sizes_the_icons_in_the_append_slot(self) -> None:
        rule = declarations(f".{CLASS} .q-field__append .q-icon")
        self.assertEqual("var(--icon-mark)", rule.get("font-size"))

    def test_a_refused_field_draws_no_tick(self) -> None:
        rule = declarations(f".{CLASS}.q-field--error .manager-value-state")
        self.assertEqual("none", rule.get("display"))

    def test_the_tick_centers_with_the_browse_button(self) -> None:
        rule = declarations(f".{CLASS} div.manager-value-state")
        self.assertEqual({"display": "flex", "align-items": "center"},
                         {key: rule.get(key) for key in ("display", "align-items")})

    def test_every_field_that_draws_browse_wears_the_class(self) -> None:
        without = [where for where, _, wears in _browse_fields() if not wears]
        self.assertEqual([], without, f"Browse drawn in a field without .{CLASS}")

    def test_it_found_the_fields(self) -> None:
        """An empty sweep passes and measures nothing, which reads the same as clean."""
        self.assertGreaterEqual(len(_browse_fields()), 3)


if __name__ == "__main__":
    unittest.main()
