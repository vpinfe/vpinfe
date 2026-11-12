"""A help line, a tooltip, a state or a setting's description that is one sentence takes
no full stop, and a line of two or more sentences ends every one of them with one."""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path
from typing import Any

from tests.support import trees
from tests.support.catalogs import served

ROOT = Path(__file__).resolve().parents[2]
CATALOG = served()

# One sentence ending and the next beginning. A number before the stop is a step's
# number ("1. Download it"), which starts a line and ends no sentence.
_BOUNDARY = re.compile(r"(?<!\b\d)[.!?][”\"')]?\s+(?=[A-Z0-9“\"(])")
_ENDS_WITH_A_STOP = re.compile(r"[.!?…:][”\"')]?$")

# The Manager's helpers that draw their first argument as a help line or a state.
_PANEL_HELP = frozenset({"note", "intro", "lede", "state"})


def _lone_stops(value: Any) -> list[str]:
    forms = value.values() if isinstance(value, dict) else [value]
    lines = [line.strip() for form in forms for line in str(form).split("\n")]
    return [line for line in lines
            if line.endswith(".") and not line.endswith("...")
            and not _BOUNDARY.search(line[:-1])]


def _unfinished(value: Any) -> list[str]:
    forms = value.values() if isinstance(value, dict) else [value]
    lines = [line.strip() for form in forms for line in str(form).split("\n")]
    return [line for line in lines
            if _BOUNDARY.search(line) and not _ENDS_WITH_A_STOP.search(line)]


def _keys(node: ast.AST) -> list[str]:
    if isinstance(node, ast.IfExp):
        return _keys(node.body) + _keys(node.orelse)
    if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "t" \
       and node.args and isinstance(node.args[0], ast.Constant):
        return [str(node.args[0].value)]
    return []


def _styled_as_help(label: ast.Call, parents: dict[ast.AST, ast.AST]) -> bool:
    chained = parents.get(label)
    if not (isinstance(chained, ast.Attribute) and chained.attr == "classes"):
        return False
    call = parents.get(chained)
    return isinstance(call, ast.Call) and bool(call.args) \
        and isinstance(call.args[0], ast.Constant) \
        and "manager-help" in str(call.args[0].value)


def _help_and_state(tree: ast.AST) -> list[tuple[int, str]]:
    """Every catalog key the source draws as a help line, a tooltip or a state."""
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
        owner = getattr(func.value, "id", None) if isinstance(func, ast.Attribute) else None
        spots = [kw.value for kw in node.keywords if kw.arg == "hint"]
        if node.args and (name == "tooltip" or (name in _PANEL_HELP and owner == "panel")):
            spots.append(node.args[0])
        if name == "hint" and owner == "panel" and len(node.args) > 1:
            spots.append(node.args[1])
        if name == "label" and node.args and _styled_as_help(node, parents):
            spots.append(node.args[0])
        found += [(spot.lineno, key) for spot in spots for key in _keys(spot)]
    return found


class TheCatalog(unittest.TestCase):
    def test_no_help_label_summary_or_description_is_a_lone_sentence_with_a_stop(self) -> None:
        found = {key: lines for key, value in CATALOG.items()
                 if key.rsplit(".", 1)[-1] in ("help", "label", "summary", "description")
                 and (lines := _lone_stops(value))}

        self.assertEqual(found, {})

    def test_no_error_is_a_lone_sentence_with_a_stop(self) -> None:
        found = {key: lines for key, value in CATALOG.items()
                 if (key.split(".", 2)[-1] if key.startswith(("app.", "ext.")) else key)
                 .startswith("error.") and (lines := _lone_stops(value))}

        self.assertEqual(found, {})


class EverySentenceOfALongerLine(unittest.TestCase):
    def test_ends_with_a_stop(self) -> None:
        found = {key: lines for key, value in CATALOG.items()
                 if (lines := _unfinished(value))}

        self.assertEqual(found, {}, "end the last sentence with a full stop")

    def test_a_numbered_step_is_not_two_sentences(self) -> None:
        self.assertEqual(_unfinished("1. Download it from the site"), [])
        self.assertEqual(_unfinished("Removed it. Nothing else changed"),
                         ["Removed it. Nothing else changed"])


class WhereTheManagerDrawsThem(unittest.TestCase):
    def setUp(self) -> None:
        self.drawn: list[tuple[str, str]] = []
        for path in sorted((ROOT / "manager").rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            tree = trees.tree_for(path)
            self.drawn += [(f"{path.relative_to(ROOT).as_posix()}:{line}", key)
                           for line, key in _help_and_state(tree)]

    def test_none_ends_a_lone_line_with_a_stop(self) -> None:
        found = sorted(f"{at} {key}" for at, key in self.drawn
                       if _lone_stops(CATALOG.get(key, "")))

        self.assertEqual(found, [], "drop the full stop")

    def test_every_position_is_read(self) -> None:
        tree = trees.parse_snippet('x.tooltip(t("a"))\n'
                                   'panel.note(t("b"))\n'
                                   'ui.label(t("c")).classes("text-xs manager-help")\n'
                                   'panel.action("x", go, hint=t("d"))\n'
                                   'panel.hint(field, t("e"))\n'
                                   'panel.state(t("f") if on else t("g"), "on")\n'
                                   'ui.label(t("h")).classes("manager-card-title")\n')

        self.assertEqual(sorted(key for _line, key in _help_and_state(tree)),
                         ["a", "b", "c", "d", "e", "f", "g"])

    def test_it_found_where_they_are_drawn(self) -> None:
        """An empty sweep passes and measures nothing, which reads the same as clean."""
        self.assertGreater(len(self.drawn), 150)


if __name__ == "__main__":
    unittest.main()
