"""A page-level listener is registered by the page function itself, before it waits for
the browser to connect, never from a handler or a helper."""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from tests.support import trees
from tests.support.project import project_files

ROOT = Path(__file__).resolve().parents[2]
NOT_SOURCE = {"tests", "node_modules"}

LATE_TODAY: frozenset[tuple[str, str]] = frozenset()


def _registers(call: ast.Call) -> bool:
    """`ui.on(...)`, or `.on(...)` on a page's layout: both add to the page's own set."""
    func = call.func
    if not isinstance(func, ast.Attribute) or func.attr != "on":
        return False
    owner = func.value
    return (isinstance(owner, ast.Name) and owner.id == "ui") \
        or (isinstance(owner, ast.Attribute) and owner.attr == "layout")


def _is_page(node: ast.AST) -> bool:
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return False
    for decorator in node.decorator_list:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        if isinstance(target, ast.Attribute) and target.attr == "page" \
           and isinstance(target.value, ast.Name) and target.value.id == "ui":
            return True
    return False


def _sent_at(page: ast.AST) -> int | None:
    """The line of the page's own `await ....connected()`, which sends the page."""
    lines = []
    waiting = list(ast.iter_child_nodes(page))
    while waiting:
        node = waiting.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        if isinstance(node, ast.Await) and isinstance(node.value, ast.Call) \
           and isinstance(node.value.func, ast.Attribute) \
           and node.value.func.attr == "connected":
            lines.append(node.lineno)
        waiting.extend(ast.iter_child_nodes(node))
    return min(lines, default=None)


class _Registrations(ast.NodeVisitor):
    """Each registration, and whether it is its page's own: directly in the page
    function, and ahead of the page being sent."""

    def __init__(self) -> None:
        self.enclosing: list[ast.AST] = []
        self.sent: dict[ast.AST, int | None] = {}
        self.found: list[tuple[int, str, bool]] = []

    def _inside(self, node: ast.AST) -> None:
        if _is_page(node):
            self.sent[node] = _sent_at(node)
        self.enclosing.append(node)
        self.generic_visit(node)
        self.enclosing.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._inside(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._inside(node)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self._inside(node)

    def visit_Call(self, node: ast.Call) -> None:
        if _registers(node):
            inner = self.enclosing[-1] if self.enclosing else None
            name = "<module>" if inner is None else getattr(inner, "name", "<lambda>")
            sent = self.sent.get(inner) if inner is not None else None
            own = inner in self.sent and (sent is None or node.lineno < sent)
            self.found.append((node.lineno, name, own))
        self.generic_visit(node)


def _registrations(tree: ast.AST) -> list[tuple[int, str, bool]]:
    seen = _Registrations()
    seen.visit(tree)
    return seen.found


def _tree() -> list[tuple[str, int, str, bool]]:
    found = []
    for path in project_files("*.py"):
        where = path.relative_to(ROOT)
        if where.parts[0] in NOT_SOURCE:
            continue
        tree = trees.tree_for(path)
        found += [(where.as_posix(), *one) for one in _registrations(tree)]
    return found


class PageListeners(unittest.TestCase):
    def setUp(self) -> None:
        self.found = _tree()

    def test_every_one_is_registered_directly_by_its_page(self) -> None:
        late = [f"{where}:{line} in {name}" for where, line, name, by_page in self.found
                if not by_page and (where, name) not in LATE_TODAY]

        self.assertEqual(late, [], "register it in the page function before it awaits "
                                   "connected(), and reach it through state")

    def test_the_named_ones_are_still_there(self) -> None:
        still = {(where, name) for where, _line, name, by_page in self.found if not by_page}

        self.assertEqual(sorted(LATE_TODAY - still), [], "take these off LATE_TODAY")

    def test_it_found_the_pages_own(self) -> None:
        """An empty sweep passes and measures nothing, which reads the same as clean."""
        self.assertGreaterEqual(sum(by_page for *_, by_page in self.found), 10)

    def test_a_handler_a_lambda_and_a_helper_are_each_refused(self) -> None:
        tree = trees.parse_snippet('@ui.page("/x")\n'
                                   'async def page():\n'
                                   '    ui.on("a", go)\n'
                                   '    ui.context.client.layout.on("b", go)\n'
                                   '    def later():\n'
                                   '        ui.on("c", go)\n'
                                   '    ui.on("d", lambda e: ui.on("e", go))\n'
                                   '    button.on("click", go)\n'
                                   'def helper():\n'
                                   '    ui.on("f", go)\n')

        self.assertEqual([(name, by_page) for _line, name, by_page in _registrations(tree)],
                         [("page", True), ("page", True), ("later", False),
                          ("page", True), ("<lambda>", False), ("helper", False)])

    def test_one_after_the_page_is_sent_is_refused(self) -> None:
        tree = trees.parse_snippet('@ui.page("/x")\n'
                                   'async def page():\n'
                                   '    async def elsewhere():\n'
                                   '        await ui.context.client.connected()\n'
                                   '    ui.on("a", go)\n'
                                   '    await ui.context.client.connected()\n'
                                   '    ui.on("b", go)\n')

        self.assertEqual([(name, by_page) for _line, name, by_page in _registrations(tree)],
                         [("page", True), ("page", False)])


if __name__ == "__main__":
    unittest.main()
