"""A control that takes several values draws them as chips, and a chosen one is not offered again.

Picking an option a second time would remove it, so the open list holds only what is not
chosen and the x on a chip is the one way out. `dropdown.Select` is where that lives, so
a control cannot be a chip field without it.
"""

from __future__ import annotations

import ast
import pathlib
import unittest
from typing import Any

from nicegui import ui

from manager.kit import dropdown, panel, tag_chips
from tests.support import trees

MANAGER = pathlib.Path(__file__).resolve().parent.parent.parent / "manager"
OPTIONS = ["night", "shortlist", "classic"]


def _offered(control: Any) -> list[str]:
    return [str(one["label"]) for one in control._props["options"]]


def _chosen(control: Any) -> list[str]:
    return [str(one["label"]) for one in control._props["model-value"]]


def _plain(value: list[str]) -> Any:
    return dropdown.Select(OPTIONS, value=value, multiple=True)


def _tags(value: list[str]) -> Any:
    return tag_chips.Picker(OPTIONS, value=value, looks={}, adds=True)


def _counted(value: list[str]) -> Any:
    return panel.CountedSelect(OPTIONS, {}, value=value, typed=True)


def _panel_row(value: list[str]) -> Any:
    held: list[Any] = []
    panel.multi_select(OPTIONS, value, lambda _e: None, typed=True,
                       on_control=held.append)()
    return held[0]


MAKERS = {"dropdown.Select": _plain, "tag_chips.Picker": _tags,
          "panel.CountedSelect": _counted, "panel.multi_select": _panel_row}


class AChosenChipLeavesTheList(unittest.TestCase):
    def test_what_is_chosen_is_not_offered(self) -> None:
        for name, make in MAKERS.items():
            with self.subTest(control=name), ui.card():
                control = make(["shortlist"])
                self.assertEqual(["night", "classic"], _offered(control))
                self.assertEqual(["shortlist"], _chosen(control))

    def test_taking_a_chip_off_offers_it_again(self) -> None:
        for name, make in MAKERS.items():
            with self.subTest(control=name), ui.card():
                control = make(["night", "shortlist"])
                control.set_value(["night"])
                self.assertEqual(["shortlist", "classic"], _offered(control))

    def test_choosing_one_takes_it_out_of_the_list(self) -> None:
        for name, make in MAKERS.items():
            with self.subTest(control=name), ui.card():
                control = make([])
                self.assertEqual(OPTIONS, _offered(control))
                control.set_value(["classic"])
                self.assertEqual(["night", "shortlist"], _offered(control))

    def test_a_chosen_value_keeps_the_index_the_server_reads_it_by(self) -> None:
        """The client sends an index into the full list, not into what it was shown."""
        with ui.card():
            control = _plain(["night"])
            self.assertEqual([1, 2], [one["value"] for one in control._props["options"]])

    def test_a_dropdown_that_takes_one_is_left_whole(self) -> None:
        with ui.card():
            control = dropdown.Select(OPTIONS, value="night")
            self.assertEqual(OPTIONS, _offered(control))


def _calls(tree: ast.AST) -> list[ast.Call]:
    return [node for node in ast.walk(tree) if isinstance(node, ast.Call)]


def _given_multiple(tree: ast.AST) -> list[ast.Call]:
    return [call for call in _calls(tree) if any(word.arg == "multiple" for word in call.keywords)]


def _is_a_dropdown_select(call: ast.Call) -> bool:
    """`dropdown.Select(...)`, or `super().__init__(...)` inside a subclass of it."""
    func = call.func
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        return (func.value.id, func.attr) == ("dropdown", "Select")
    return isinstance(func, ast.Attribute) and func.attr == "__init__" \
        and isinstance(func.value, ast.Call) and getattr(func.value.func, "id", "") == "super" \
        and _inside_a_subclass(call)


def _inside_a_subclass(call: ast.Call) -> bool:
    for path in MANAGER.rglob("*.py"):
        for cls in (n for n in ast.walk(trees.tree_for(path)) if isinstance(n, ast.ClassDef)):
            if call in list(ast.walk(cls)):
                return any(isinstance(base, ast.Attribute) and base.attr == "Select"
                           and getattr(base.value, "id", "") == "dropdown" for base in cls.bases)
    return False


class EveryChipFieldIsADropdownSelect(unittest.TestCase):
    def test_no_module_draws_chips_itself(self) -> None:
        """`use-chips` is what makes a select a chip field, and `ui.input_chips` is a
        second one that offers nothing to leave out."""
        found = []
        for path in sorted(MANAGER.rglob("*.py")):
            tree = trees.tree_for(path)
            for node in ast.walk(tree):
                if path.name != "dropdown.py" and isinstance(node, ast.Constant) \
                        and isinstance(node.value, str) and "use-chips" in node.value:
                    found.append(f"manager/{path.name}:{node.lineno} use-chips")
                if isinstance(node, ast.Attribute) and node.attr == "input_chips" \
                        and isinstance(node.value, ast.Name) and node.value.id == "ui":
                    found.append(f"manager/{path.name}:{node.lineno} ui.input_chips")
        self.assertEqual([], found, "a chip field is a dropdown.Select with multiple=True")

    def test_only_a_dropdown_select_is_given_multiple(self) -> None:
        """A call names `dropdown.Select`, or is a subclass of it handing `multiple` up."""
        found = []
        for path in sorted(MANAGER.rglob("*.py")):
            if path.name != "dropdown.py":
                found += [f"manager/{path.name}:{call.lineno}"
                          for call in _given_multiple(trees.tree_for(path))
                          if not _is_a_dropdown_select(call)]
        self.assertEqual([], found, "a chip field is a dropdown.Select with multiple=True")

    def test_it_found_the_chip_fields(self) -> None:
        """An empty sweep passes and measures nothing, which reads the same as clean."""
        uses = sum(len(_given_multiple(trees.tree_for(path))) for path in MANAGER.rglob("*.py"))
        self.assertGreaterEqual(uses, 3)


if __name__ == "__main__":
    unittest.main()
