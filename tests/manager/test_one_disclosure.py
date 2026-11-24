"""A disclosure is drawn one way, whatever it sits in.

Every expansion in the Manager is a disclosure, built by `panel.disclosure`, and a
stylesheet rule reaches Quasar's expansion only through `manager-disclosure`. A rule
scoped to a container instead restyles the disclosures that happen to sit there.
"""

from __future__ import annotations

import ast
import pathlib
import re
import unittest

from nicegui import ui

from common import i18n
from manager.kit import panel
from tests.support import trees

REPO = pathlib.Path(__file__).resolve().parent.parent.parent
MANAGER = REPO / "manager"
EXPANSION_PARTS = (".q-expansion-item", ".nicegui-expansion")


def _call_of(node: ast.AST, owner: str, name: str) -> bool:
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == name and isinstance(node.func.value, ast.Name)
            and node.func.value.id == owner)


def _is_ui_expansion(node: ast.AST) -> bool:
    return _call_of(node, "ui", "expansion") or _call_of(node, "panel", "disclosure")


def _built_outside_the_panel() -> list[str]:
    found = []
    for path in sorted(MANAGER.rglob("*.py")):
        tree = trees.tree_for(path)
        allowed: set[int] = set()
        if path.name == "panel.py":
            for fn in ast.walk(tree):
                if isinstance(fn, ast.FunctionDef) and fn.name == "disclosure":
                    allowed |= {id(node) for node in ast.walk(fn)}
        found += [f"{path.relative_to(REPO).as_posix()}:{node.lineno}" for node in ast.walk(tree)
                  if isinstance(node, ast.Call) and _call_of(node, "ui", "expansion")
                  and id(node) not in allowed]
    return found


def _base(call: ast.Call) -> ast.AST:
    """The call a chain of `.props(...)`, `.classes(...)` and the like hangs from."""
    node: ast.AST = call
    while (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
           and isinstance(node.func.value, ast.Call)):
        node = node.func.value
    return node


def _expansions() -> list[tuple[str, int, list[str]]]:
    """Each disclosure built with the words its chained `.classes` calls pass."""
    found = []
    for path in sorted(MANAGER.rglob("*.py")):
        tree = trees.tree_for(path)
        classes: dict[int, list[str]] = {}
        for node in ast.walk(tree):
            if _is_ui_expansion(node):
                classes.setdefault(id(node), [])
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "classes"):
                continue
            base = _base(node)
            if id(base) in classes:
                classes[id(base)] += [word for arg in node.args
                                      if isinstance(arg, ast.Constant)
                                      and isinstance(arg.value, str)
                                      for word in arg.value.split()]
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _is_ui_expansion(node):
                found.append((path.relative_to(REPO).as_posix(), node.lineno, classes[id(node)]))
    return found


def _selectors() -> list[tuple[str, str]]:
    found = []
    for sheet in sorted((MANAGER / "static").rglob("*.css")):
        text = re.sub(r"/\*.*?\*/", "", sheet.read_text(encoding="utf-8"), flags=re.S)
        for group in re.findall(r"([^{}]+)\{[^{}]*\}", text):
            found += [(sheet.name, one.strip()) for one in group.split(",")]
    return found


class TestOneDisclosure(unittest.TestCase):
    def test_the_manager_has_disclosures_to_check(self) -> None:
        self.assertTrue(_expansions(), "no disclosure found; the walk is broken")

    def test_every_expansion_is_a_disclosure(self) -> None:
        self.assertEqual(_built_outside_the_panel(), [],
                         "an expansion in the Manager is built by panel.disclosure")

    def test_a_disclosure_is_named_by_its_words_alone(self) -> None:
        self.addCleanup(i18n.set_language, i18n.language())
        i18n.set_language("qps")
        label = i18n.t("manager.workbench.hidden_guides", count=2)
        with ui.card():
            built = panel.disclosure(label)

        self.assertIn("manager-disclosure", built.classes)
        self.assertEqual(built.props["toggle-aria-label"], label)

    def test_no_disclosure_spans_the_pane(self) -> None:
        for where, line, words in _expansions():
            with self.subTest(f"{where}:{line}"):
                self.assertNotIn("w-full", words)

    def test_the_expansion_is_styled_only_as_a_disclosure(self) -> None:
        for sheet, selector in _selectors():
            if not any(part in selector for part in EXPANSION_PARTS):
                continue
            with self.subTest(f"{sheet}: {selector}"):
                self.assertRegex(selector, r"^\.manager-disclosure[\s.]",
                                 "a rule on the expansion goes through .manager-disclosure")


if __name__ == "__main__":
    unittest.main()
