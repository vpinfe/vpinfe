"""Every button carries an icon, and the drawing comes from `manager/kit/verbs.py`."""

from __future__ import annotations

import ast
import pathlib
import unittest

from tests.support import trees

REPO = pathlib.Path(__file__).resolve().parent.parent.parent
MANAGER = REPO / "manager"

NAME_PICKERS = {("send_to_device.py", "name_of")}


def _sources() -> list[tuple[pathlib.Path, ast.Module]]:
    return [(p, trees.tree_for(p)) for p in sorted(MANAGER.rglob("*.py"))]


def _button_calls(tree: ast.Module) -> list[ast.Call]:
    got = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        owner = node.func.value
        if not isinstance(owner, ast.Name):
            continue
        if (owner.id, node.func.attr) in (("ui", "button"), ("panel", "action")):
            got.append(node)
    return got


def _labelled(node: ast.Call) -> bool:
    """A button with a first positional argument is one that shows words."""
    return bool(node.args)


def _excused(path: pathlib.Path, node: ast.Call) -> bool:
    first = node.args[0] if node.args else None
    name = first.func.id if isinstance(first, ast.Call) and isinstance(
        first.func, ast.Name) else ""
    return (path.name, name) in NAME_PICKERS


class TestEveryButtonCarriesAnIcon(unittest.TestCase):
    def test_no_button_is_text_alone(self) -> None:
        offenders = []
        for path, tree in _sources():
            for node in _button_calls(tree):
                if not _labelled(node) or _excused(path, node):
                    continue
                if not any(kw.arg == "icon" for kw in node.keywords):
                    offenders.append(f"{path.relative_to(REPO).as_posix()}:{node.lineno}")
        self.assertEqual(offenders, [], "give it an icon from manager/kit/verbs.py")

    def test_every_drawing_is_declared(self) -> None:
        from manager.kit import verbs
        allowed = verbs.declared()
        offenders = []
        for path, tree in _sources():
            if path.name == "verbs.py":
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.keyword) or node.arg != "icon":
                    continue
                if isinstance(node.value, ast.Constant) and isinstance(
                        node.value.value, str):
                    if node.value.value not in allowed:
                        offenders.append(
                            f"{path.relative_to(REPO).as_posix()}:{node.lineno} "
                            f"{node.value.value!r}")
        self.assertEqual(offenders, [],
                         "name it in manager/kit/verbs.py rather than here")

    def test_no_button_wears_the_smart_mark(self) -> None:
        from manager.kit import verbs
        buttons, worn = 0, []
        for path, tree in _sources():
            for node in _button_calls(tree):
                buttons += 1
                for keyword in node.keywords:
                    value = keyword.value
                    if keyword.arg == "icon" and (
                            (isinstance(value, ast.Attribute) and value.attr == "SMART")
                            or (isinstance(value, ast.Constant)
                                and value.value == verbs.SMART)):
                        worn.append(f"{path.relative_to(REPO).as_posix()}:{node.lineno}")
        self.assertGreater(buttons, 50)
        self.assertEqual(worn, [], "the Smart mark says what a collection is; it is not a verb")


if __name__ == "__main__":
    unittest.main()
