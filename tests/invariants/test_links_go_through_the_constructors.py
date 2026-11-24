"""A link is built by `panel.link` or `panel.link_out`, never by reaching for `ui.link`.

The treatment is the constructor's to decide. A call site that builds its own anchor
picks its own colour, its own underline and its own mark, and that is what this fails on.
"""

from __future__ import annotations

import ast
import pathlib
import unittest

from tests.support import trees

REPO = pathlib.Path(__file__).resolve().parent.parent.parent
MANAGER = REPO / "manager"

# `panel.py` owns the treatment, so it is where `ui.link` lives.
#
# `page.py` builds the nav rail's rows, which are anchors for the same reason a link is
# one - a destination has an address - but they are not links: they carry no underline
# and no accent, because `manager-nav-active` already says which one you are on. They are
# a rail control and they stay where the rail is built.
ALLOWED = {"panel.py", "page.py"}


def _calls(tree: ast.AST) -> list[ast.Call]:
    return [node for node in ast.walk(tree) if isinstance(node, ast.Call)]


def _is_ui_link(node: ast.Call) -> bool:
    func = node.func
    return (isinstance(func, ast.Attribute) and func.attr == "link"
            and isinstance(func.value, ast.Name) and func.value.id == "ui")


class TestLinksGoThroughTheConstructors(unittest.TestCase):
    def test_no_call_site_builds_its_own_anchor(self) -> None:
        offenders = []
        for path in sorted(MANAGER.rglob("*.py")):
            if path.name in ALLOWED:
                continue
            tree = trees.tree_for(path)
            offenders += [f"{path.relative_to(REPO).as_posix()}:{node.lineno}"
                          for node in _calls(tree) if _is_ui_link(node)]
        self.assertEqual(offenders, [],
                         "use panel.link or panel.link_out")

    def test_the_constructors_are_there_to_be_used(self) -> None:
        """The allowlist is only honest while the thing it defers to exists."""
        tree = trees.tree_for(MANAGER / "kit" / "panel.py")
        built = {node.name for node in ast.walk(tree)
                 if isinstance(node, ast.FunctionDef)}
        self.assertIn("link", built)
        self.assertIn("link_out", built)

    def test_only_a_link_that_leaves_the_app_is_marked(self) -> None:
        """`open_in_new` is the mark for leaving, so the internal constructor must not
        carry one - a mark on every link is a mark that says nothing."""
        tree = trees.tree_for(MANAGER / "kit" / "panel.py")
        inside = next(node for node in ast.walk(tree)
                      if isinstance(node, ast.FunctionDef) and node.name == "link")
        self.assertNotIn("open_in_new", ast.unparse(inside))
        outside = next(node for node in ast.walk(tree)
                       if isinstance(node, ast.FunctionDef) and node.name == "link_out")
        self.assertIn("open_in_new", ast.unparse(outside))


if __name__ == "__main__":
    unittest.main()
