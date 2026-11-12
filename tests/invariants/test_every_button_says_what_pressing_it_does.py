"""Every button says what pressing it does: `panel.action`, `panel.remote_action` and
`panel.remote_key` carry a `hint=` drawn as its tooltip, or `panel.NO_HINT` where the
button's own words already say it. A button with no words takes a hint, never the
marker. A button built by hand with an icon and no label carries a tooltip too, since the
icon is all a person has."""

from __future__ import annotations

import ast
import pathlib
import unittest

from tests.support import trees

REPO = pathlib.Path(__file__).resolve().parent.parent.parent
MANAGER = REPO / "manager"


def _sources() -> list[tuple[pathlib.Path, ast.Module]]:
    return [(p, trees.tree_for(p)) for p in sorted(MANAGER.rglob("*.py"))]


def _action_calls(tree: ast.Module) -> list[ast.Call]:
    got = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        owner = node.func.value
        if not isinstance(owner, ast.Name) or owner.id != "panel":
            continue
        if node.func.attr in ("action", "remote_action", "remote_key"):
            got.append(node)
    return got


def _has_hint(node: ast.Call) -> bool:
    hint_kw = next((kw for kw in node.keywords if kw.arg == "hint"), None)
    if hint_kw is None:
        return False
    return not (isinstance(hint_kw.value, ast.Constant) and hint_kw.value.value == "")


def _ui_buttons(tree: ast.Module) -> list[ast.Call]:
    return [node for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "button" and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "ui"]


def _chained(node: ast.Call, parents: dict[ast.AST, ast.AST]) -> set[str]:
    """The methods chained on this call's result: `ui.button(...).props(...).tooltip(...)`."""
    names: set[str] = set()
    at: ast.AST = node
    while True:
        attribute = parents.get(at)
        call = parents.get(attribute) if attribute is not None else None
        if not (isinstance(attribute, ast.Attribute) and attribute.value is at
                and isinstance(call, ast.Call) and call.func is attribute):
            return names
        names.add(attribute.attr)
        at = call


def _bound_name(node: ast.Call, parents: dict[ast.AST, ast.AST]) -> str:
    """The name a button is kept under: `x = ui.button(...)` or `with ui.button(...) as x`."""
    at: ast.AST = node
    while isinstance(parents.get(at), (ast.Attribute, ast.Call)):
        at = parents[at]
    holder = parents.get(at)
    if isinstance(holder, ast.Assign) and isinstance(holder.targets[0], ast.Name):
        return holder.targets[0].id
    if isinstance(holder, ast.withitem) and isinstance(holder.optional_vars, ast.Name):
        return holder.optional_vars.id
    return ""


def _tooltipped_names(scope: ast.AST) -> set[str]:
    """Names with a tooltip put on them, directly or at the end of a chain, or with a
    `ui.tooltip(...)` drawn straight inside a `with <name>:` block."""
    names: set[str] = set()
    for node in ast.walk(scope):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "tooltip":
            root: ast.AST = node.func.value
            while isinstance(root, (ast.Attribute, ast.Call)):
                root = root.value if isinstance(root, ast.Attribute) else root.func
            if isinstance(root, ast.Name):
                names.add(root.id)
        if isinstance(node, ast.With) and any(
                isinstance(line, (ast.Expr, ast.Assign)) and isinstance(line.value, ast.Call)
                and isinstance(line.value.func, ast.Attribute)
                and line.value.func.attr == "tooltip"
                and isinstance(line.value.func.value, ast.Name)
                and line.value.func.value.id == "ui" for line in node.body):
            names.update(item.context_expr.id for item in node.items
                         if isinstance(item.context_expr, ast.Name))
    return names


def _enclosing(node: ast.AST, parents: dict[ast.AST, ast.AST], tree: ast.Module) -> ast.AST:
    at = node
    while at in parents:
        at = parents[at]
        if isinstance(at, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return at
    return tree


class TestEveryButtonSaysWhatPressingItDoes(unittest.TestCase):
    def test_every_action_carries_a_hint(self) -> None:
        offenders = []
        total = 0
        for path, tree in _sources():
            for node in _action_calls(tree):
                total += 1
                if not _has_hint(node):
                    offenders.append(f"{path.relative_to(REPO).as_posix()}:{node.lineno}")
        self.assertGreater(total, 70)
        self.assertEqual(offenders, [], "give it a hint= that says what pressing it does, "
                                        "or hint=panel.NO_HINT where its words already do")

    def test_no_hint_is_only_for_a_button_with_words(self) -> None:
        wordless = []
        marked = 0
        for path, tree in _sources():
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                for keyword in node.keywords:
                    if keyword.arg == "hint" and isinstance(keyword.value, ast.Attribute) \
                            and keyword.value.attr == "NO_HINT":
                        marked += 1
                        label = node.args[0] if node.args else None
                        named = (isinstance(node.func, ast.Attribute) and node.func.attr in (
                            "action", "link_action", "remote_action", "remote_key")) \
                            or (isinstance(node.func, ast.Name) and node.func.id == "_tap_button")
                        if not named or label is None or (
                                isinstance(label, ast.Constant) and not label.value):
                            wordless.append(f"{path.relative_to(REPO).as_posix()}:{node.lineno}")
        self.assertGreater(marked, 0, "the scan stopped finding the marker")
        self.assertEqual(wordless, [], "a button with no words needs a hint saying what it does")

    def test_a_button_with_no_label_has_a_tooltip(self) -> None:
        offenders = []
        total = 0
        for path, tree in _sources():
            parents = {child: node for node in ast.walk(tree)
                       for child in ast.iter_child_nodes(node)}
            for node in _ui_buttons(tree):
                if node.args:
                    continue
                total += 1
                kept = _tooltipped_names(_enclosing(node, parents, tree))
                if "tooltip" in _chained(node, parents) or _bound_name(node, parents) in kept:
                    continue
                offenders.append(f"{path.relative_to(REPO).as_posix()}:{node.lineno}")
        self.assertGreater(total, 8)
        self.assertEqual(offenders, [], "an icon alone needs `.tooltip(...)` saying what it does")


if __name__ == "__main__":
    unittest.main()
