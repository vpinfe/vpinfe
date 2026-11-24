"""A test class whose tests each boot their own VPinFE says why.

A `LiveInstance(` anywhere in a test class but its `setUpClass` boots one per test, which
costs seconds a test. The class says why in `BOOTS_PER_TEST`, a sentence that is read,
not checked. One booted in `setUpClass` or `setUpModule` is shared and needs nothing.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from tests.support import trees

ROOT = Path(__file__).resolve().parents[2]
TESTS = ROOT / "tests"
DECLARED = "BOOTS_PER_TEST"
SHARED = {"setUpClass", "setUpModule"}


def _boots(node: ast.AST) -> bool:
    return any(isinstance(one, ast.Call) and (
        isinstance(one.func, ast.Name) and one.func.id == "LiveInstance"
        or isinstance(one.func, ast.Attribute) and one.func.attr == "LiveInstance")
        for one in ast.walk(node))


def _reason(cls: ast.ClassDef) -> str | None:
    for node in cls.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == DECLARED
                for target in node.targets):
            value = node.value
            return value.value if isinstance(value, ast.Constant) \
                and isinstance(value.value, str) else ""
    return None


def boots(tree: ast.Module) -> tuple[dict[str, str | None], list[str]]:
    """({class booting per test: its reason, None if undeclared}, [module-level
    functions other than setUpModule that boot])."""
    classes: dict[str, str | None] = {}
    loose: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            if any(isinstance(one, (ast.FunctionDef, ast.AsyncFunctionDef))
                   and one.name not in SHARED and _boots(one) for one in node.body):
                classes[node.name] = _reason(node)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name not in SHARED and _boots(node):
            loose.append(node.name)
    return classes, loose


def declared(tree: ast.Module) -> list[str]:
    return [node.name for node in tree.body
            if isinstance(node, ast.ClassDef) and _reason(node) is not None]


def _tests() -> dict[str, ast.Module]:
    return {path.relative_to(ROOT).as_posix(): trees.tree_for(path)
            for path in sorted(TESTS.rglob("test_*.py"))}


class ATestThatBootsItsOwnInstanceSaysWhy(unittest.TestCase):
    def test_a_class_that_boots_per_test_says_why(self) -> None:
        silent = [f"{name} {cls}" for name, tree in _tests().items()
                  for cls, reason in boots(tree)[0].items() if not (reason or "").strip()]

        self.assertEqual([], silent,
                         f"Boot once in setUpClass, or say in {DECLARED} why each test "
                         "needs an instance of its own.")

    def test_only_a_class_boots_per_test(self) -> None:
        loose = [f"{name} {function}" for name, tree in _tests().items()
                 for function in boots(tree)[1]]

        self.assertEqual([], loose,
                         "Boot in a class's setUpClass, in setUpModule, or in a class "
                         f"that says why in {DECLARED}.")

    def test_a_declared_class_does_boot_per_test(self) -> None:
        stale = [f"{name} {cls}" for name, tree in _tests().items()
                 for cls in declared(tree) if cls not in boots(tree)[0]]

        self.assertEqual([], stale, f"{DECLARED} on a class that boots once.")

    def test_the_classes_that_boot_per_test_are_found(self) -> None:
        found = {cls for tree in _tests().values() for cls in boots(tree)[0]}

        self.assertLessEqual({"PlayerListSmokeTests", "SeparationTests"}, found)

    def test_a_boot_anywhere_but_setupclass_is_per_test(self) -> None:
        tree = trees.parse_snippet(
            "class Shared:\n"
            "    @classmethod\n"
            "    def setUpClass(cls):\n"
            "        cls.instance = LiveInstance(root).__enter__()\n"
            "class Helper:\n"
            "    def _drive(self):\n"
            "        async def run():\n"
            "            with live_instance.LiveInstance(root) as instance:\n"
            "                pass\n"
            "class Said:\n"
            "    BOOTS_PER_TEST = 'each test leaves the library changed'\n"
            "    def test_one(self):\n"
            "        with LiveInstance(root):\n"
            "            pass\n"
            "class Blank:\n"
            "    BOOTS_PER_TEST = '  '\n"
            "    def setUp(self):\n"
            "        self.instance = LiveInstance(root)\n"
            "def setUpModule():\n"
            "    LiveInstance(root)\n"
            "def boot():\n"
            "    return LiveInstance(root)\n")

        self.assertEqual(({"Helper": None, "Said": "each test leaves the library changed",
                           "Blank": "  "}, ["boot"]), boots(tree))
        self.assertEqual(["Said", "Blank"], declared(tree))


if __name__ == "__main__":
    unittest.main()
