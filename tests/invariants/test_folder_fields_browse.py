"""A path field always offers Browse: `path_field`, whatever `wants` is, and every
`control_for` call, which is the one place a schema or launcher field's `path` reaches a
control."""

from __future__ import annotations

import ast
import pathlib
import unittest

from tests.support import trees

REPO = pathlib.Path(__file__).resolve().parent.parent.parent
MANAGER = REPO / "manager"


def _names_a_call(func: ast.expr, name: str) -> bool:
    """Whichever way it was reached - `panel.path_field(...)` or, from inside the module
    that defines it, the bare `control_for(...)`."""
    return (isinstance(func, ast.Name) and func.id == name) or \
        (isinstance(func, ast.Attribute) and func.attr == name)


def _calls_without_browse(tree: ast.Module, name: str) -> list[int]:
    found = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and _names_a_call(node.func, name)):
            continue
        if not any(kw.arg == "browse" for kw in node.keywords):
            found.append(node.lineno)
    return found


class TestFolderFieldsOfferBrowse(unittest.TestCase):
    def test_every_path_field_call_carries_browse(self) -> None:
        offenders = []
        for path in sorted(MANAGER.rglob("*.py")):
            for line in _calls_without_browse(trees.tree_for(path), "path_field"):
                offenders.append(f"{path.relative_to(REPO).as_posix()}:{line}")
        self.assertEqual(offenders, [], "give it browse=, so typing is not the only way in")

    def test_every_control_for_call_carries_browse(self) -> None:
        offenders = []
        for path in sorted(MANAGER.rglob("*.py")):
            for line in _calls_without_browse(trees.tree_for(path), "control_for"):
                offenders.append(f"{path.relative_to(REPO).as_posix()}:{line}")
        self.assertEqual(offenders, [], "give it browse=, so a path option can offer it")

    def test_the_check_flags_one_with_no_browse_and_passes_one_with_it(self) -> None:
        for name in ("path_field", "control_for"):
            flagged = trees.parse_snippet(f'panel.{name}(wants="dir")\n')
            cleared = trees.parse_snippet(
                f'panel.{name}(wants="dir", browse=library.folders)\n')

            self.assertEqual(_calls_without_browse(flagged, name), [1])
            self.assertEqual(_calls_without_browse(cleared, name), [])


if __name__ == "__main__":
    unittest.main()
