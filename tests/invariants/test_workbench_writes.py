"""Every workbench surface builds its rebuild with `_rebuilds`, which also puts the
grid right. One that builds its own gets neither half."""

from __future__ import annotations

import ast
import pathlib
import unittest

from tests.support import trees

REPO = pathlib.Path(__file__).resolve().parent.parent.parent
WORKBENCH = sorted((REPO / "manager" / "workbench").glob("*.py"))
WRAPPER = "_rebuilds"


def _assignments() -> list[tuple[str, int, str]]:
    """Every `context["rebuild"] = ...`, with the file, the line and what it was given."""
    found = []
    for path, node in ((one, node) for one in WORKBENCH
                       for node in ast.walk(trees.tree_for(one))):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not (isinstance(target, ast.Subscript)
                and getattr(target.value, "id", "") == "context"
                and getattr(getattr(target, "slice", None), "value", "") == "rebuild"):
            continue
        call = node.value
        name = (getattr(call.func, "id", getattr(call.func, "attr", ""))
                if isinstance(call, ast.Call) else "")
        found.append((path.relative_to(REPO).as_posix(), node.lineno, name))
    return found


class WorkbenchWrites(unittest.TestCase):

    def test_every_surface_goes_through_the_wrapper(self):
        bare = [f"{path}:{line}: builds its own rebuild"
                for path, line, name in _assignments() if name != WRAPPER]
        self.assertEqual(bare, [], "\n" + "\n".join(bare))

    def test_it_found_the_surfaces(self):
        """An empty sweep passes and measures nothing, which reads the same as clean."""
        self.assertGreaterEqual(len(_assignments()), 5)

    def test_a_write_can_save_without_rebuilding(self):
        """`saved` is the half a value write uses; without it there is one path only."""
        source = "".join(path.read_text(encoding="utf-8") for path in WORKBENCH)
        self.assertIn('context["saved"] =', source)
        self.assertIn("shape=False", source)


if __name__ == "__main__":
    unittest.main()
