"""A choice between a few named states is one control with an edge, wherever it is drawn.

`panel.segmented` carries the look, so a call site never writes the props. The phone
surface keeps its own full-width toggle.
"""

from __future__ import annotations

import ast
import pathlib
import unittest

from tests.support import trees
from tests.support.stylesheet import declarations

MANAGER = pathlib.Path(__file__).resolve().parent.parent.parent / "manager"
OWN_LOOK = {"panel.py", "remote_record.py"}


def _lines(tree: ast.AST, owner: str, attribute: str) -> list[int]:
    return [node.lineno for node in ast.walk(tree)
            if isinstance(node, ast.Attribute) and node.attr == attribute
            and isinstance(node.value, ast.Name) and node.value.id == owner]


class EverySegmentedChoiceIsOne(unittest.TestCase):
    def test_no_module_draws_a_raw_ui_toggle(self) -> None:
        raw = []
        for path in sorted(MANAGER.rglob("*.py")):
            if path.name not in OWN_LOOK:
                raw += [f"manager/{path.name}:{line}"
                        for line in _lines(trees.tree_for(path), "ui", "toggle")]
        self.assertEqual([], raw, "use panel.segmented")

    def test_it_found_the_toggles(self) -> None:
        """An empty sweep passes and measures nothing, which reads the same as clean."""
        uses = sum(len(_lines(trees.tree_for(path), "panel", "segmented"))
                   for path in MANAGER.rglob("*.py"))
        self.assertGreaterEqual(uses, 7)

    def test_the_edge_is_the_control_boundary(self) -> None:
        self.assertEqual("1px solid var(--line-strong)",
                         declarations(".manager-segmented").get("border"))


if __name__ == "__main__":
    unittest.main()
