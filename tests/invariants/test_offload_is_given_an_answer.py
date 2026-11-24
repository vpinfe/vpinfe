"""`offload.io` is only given a call that answers something: it raises on `None`."""

from __future__ import annotations

import ast
import pathlib
import unittest

from tests.support import trees

REPO = pathlib.Path(__file__).resolve().parent.parent.parent

# The classes whose methods the Manager hands to `offload.io` by attribute.
DECLARED = {"manager/kit/data.py": "Library", "manager/kit/api.py": "ApiClient"}


def _answers_nothing() -> set[str]:
    """Method names declared `-> None` in every one of these classes that has them."""
    returns: dict[str, set[str]] = {}
    for path, name in DECLARED.items():
        tree = trees.tree_for(REPO / path)
        cls = next(node for node in tree.body
                   if isinstance(node, ast.ClassDef) and node.name == name)
        for method in cls.body:
            if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
                returns.setdefault(method.name, set()).add(
                    ast.unparse(method.returns) if method.returns else "")
    return {name for name, said in returns.items() if said == {"None"}}


def _offenders(tree: ast.AST, void: set[str]) -> list[tuple[int, str]]:
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
           and node.func.attr == "io" and isinstance(node.func.value, ast.Name) \
           and node.func.value.id == "offload" and node.args \
           and isinstance(node.args[0], ast.Attribute) and node.args[0].attr in void:
            out.append((node.lineno, node.args[0].attr))
    return out


class OffloadIsGivenAnAnswer(unittest.TestCase):
    def test_the_check_sees_a_void_call(self) -> None:
        void = _answers_nothing()
        self.assertIn("delete_location", void)
        self.assertEqual(
            _offenders(trees.parse_snippet("await offload.io(library.delete_location, 'x')"),
                       void),
            [(1, "delete_location")])
        self.assertEqual(
            _offenders(trees.parse_snippet("await run.io_bound(library.delete_location, 'x')"),
                       void),
            [])

    def test_no_manager_call_hands_it_one(self) -> None:
        void = _answers_nothing()
        found = [f"{path.relative_to(REPO).as_posix()}:{line} {name}"
                 for path in sorted((REPO / "manager").rglob("*.py"))
                 for line, name in _offenders(trees.tree_for(path), void)]
        self.assertEqual(found, [], "a call that answers nothing goes through "
                                    "`run.io_bound`, not `offload.io`")


if __name__ == "__main__":
    unittest.main()
