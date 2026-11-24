"""Every dialog field that can be given a message keeps the row for it."""

from __future__ import annotations

import ast
import pathlib
import unittest

from tests.support import trees

MANAGER = pathlib.Path(__file__).resolve().parent.parent.parent / "manager"
GROWS = "error-message"
KEEPS_THE_ROW = "bottom-slots"
# Controls that keep the row themselves, so a caller setting an error on one is fine.
BUILT_KEEPING_IT = ("path_field",)


def _props_by_name(scope: ast.AST) -> dict[str, list[str]]:
    """Every props string applied to each name in this function.

    Keyed by the name the control is held under and gathered per function, so two
    dialogs in one module that both call their field `name` are not read as one.
    """
    props: dict[str, list[str]] = {}
    for node in ast.walk(scope):
        if not (isinstance(node, ast.Call)
                and getattr(node.func, "attr", "") == "props"):
            continue
        said = [a.value for a in node.args
                if isinstance(a, ast.Constant) and isinstance(a.value, str)]
        # The receiver, through however many chained calls sit between.
        assert isinstance(node.func, ast.Attribute)
        inner: ast.AST = node.func.value
        while isinstance(inner, ast.Call | ast.Attribute):
            inner = inner.func if isinstance(inner, ast.Call) else inner.value
        if isinstance(inner, ast.Name):
            props.setdefault(inner.id, []).extend(said)
    # A control named by its assignment: `field = ui.input(...).props("...")`, or one
    # built by a helper that has already asked for the row.
    for node in ast.walk(scope):
        if not (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and isinstance(node.value, ast.Call)):
            continue
        held = node.targets[0].id
        for inner in ast.walk(node.value):
            if not isinstance(inner, ast.Call):
                continue
            if getattr(inner.func, "attr", "") in BUILT_KEEPING_IT:
                props.setdefault(held, []).append(KEEPS_THE_ROW)
            if getattr(inner.func, "attr", "") == "props":
                props.setdefault(held, []).extend(
                    a.value for a in inner.args
                    if isinstance(a, ast.Constant) and isinstance(a.value, str))
    return props


def _outermost(tree: ast.AST) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    """The functions nothing else contains.

    A dialog builds its field and sets the error from a nested handler, so a nested
    scope holds the error and none of the props the field was made with - read on its
    own it reports every dialog in the tree.
    """
    kinds = (ast.FunctionDef, ast.AsyncFunctionDef)
    nested = {id(inner)
              for node in ast.walk(tree) if isinstance(node, kinds)
              for inner in ast.walk(node) if isinstance(inner, kinds) and inner is not node}
    return [node for node in ast.walk(tree)
            if isinstance(node, kinds) and id(node) not in nested]


def _growing() -> list[str]:
    found = []
    for path in sorted(MANAGER.rglob("*.py")):
        tree = trees.tree_for(path)
        for scope in _outermost(tree):
            props = _props_by_name(scope)
            for held, said in props.items():
                if not any(GROWS in one for one in said):
                    continue
                if any(KEEPS_THE_ROW in one for one in said):
                    continue
                found.append(f"manager/{path.name}:{scope.lineno}: {held} can be given "
                             "an error message and does not keep the row for it, so the "
                             "dialog grows when it is")
    return found


class ADialogIsTheSizeItOpenedAt(unittest.TestCase):

    def test_no_field_grows_its_dialog(self):
        growing = _growing()
        self.assertEqual(growing, [], "\n" + "\n".join(growing))

    def test_it_found_the_fields(self):
        """An empty sweep passes and measures nothing, which reads the same as clean."""
        seen = sum(one.count(GROWS)
                   for path in MANAGER.rglob("*.py")
                   for one in [path.read_text(encoding="utf-8")])
        self.assertGreater(seen, 3)


if __name__ == "__main__":
    unittest.main()
