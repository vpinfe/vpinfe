"""Every dropdown in the Manager is one class, so every one is drawn the same way.

`dropdown.Select` carries the look, so a call site names which of two it wants and never
writes the props. The browser half, that the text of each is at the middle of its box, is
in `tests/theming/test_manager_pages_drive.py`.
"""

from __future__ import annotations

import ast
import pathlib
import unittest

from manager.kit import dropdown
from tests.support import trees
from tests.support.stylesheet import declarations

MANAGER = pathlib.Path(__file__).resolve().parent.parent.parent / "manager"


def _names(tree: ast.AST, owner: str, attribute: str) -> list[int]:
    """The lines where `owner.attribute` is written, as a call, a base class or a type."""
    return [node.lineno for node in ast.walk(tree)
            if isinstance(node, ast.Attribute) and node.attr == attribute
            and isinstance(node.value, ast.Name) and node.value.id == owner]


class EveryDropdownIsOne(unittest.TestCase):
    def test_no_module_draws_a_raw_ui_select(self) -> None:
        raw = []
        for path in sorted(MANAGER.rglob("*.py")):
            if path.name == "dropdown.py":
                continue
            raw += [f"manager/{path.name}:{line}"
                    for line in _names(trees.tree_for(path), "ui", "select")]
        self.assertEqual([], raw, "use manager.kit.dropdown.Select, or a panel helper over it")

    def test_it_found_the_selects(self) -> None:
        """An empty sweep passes and measures nothing, which reads the same as clean."""
        uses = sum(len(_names(trees.tree_for(path), "dropdown", "Select"))
                   for path in MANAGER.rglob("*.py"))
        self.assertGreaterEqual(uses, 15)

    def test_a_call_site_names_a_look_and_there_are_two(self) -> None:
        self.assertEqual({dropdown.FIELD, dropdown.OUTLINED}, set(dropdown._PROPS))
        self.assertEqual({dropdown.FIELD, dropdown.OUTLINED}, set(dropdown._CLASSES))

    def test_the_value_is_centered_inside_the_border(self) -> None:
        """The container inherits the control's bordered height, so without this it is two
        pixels too tall and the text sits a pixel low."""
        rule = declarations(".manager-edit-select .q-field__control-container")
        self.assertEqual("100%", rule.get("height"))
        self.assertEqual("0", rule.get("padding-top"))


if __name__ == "__main__":
    unittest.main()
