"""Route handlers call a service and shape a response, and do not hold logic themselves.

`docs/conventions.md` says logic belongs in a service where the other callers can reach it.
It had not been checked, and `httpapi/` had become the place the logic lived: a hundred and
eleven handlers carried more than a few lines each, `games.py` was the de facto service layer
with ten sibling modules importing its private helpers, and the tests, the extension
operations and one route calling another all reached into handlers because that was the only
way to reach the code inside them.

The line count is a smell test rather than a rule - a handler is thin because it delegates,
not because it is short. It works here because the shape is unambiguous: of a hundred and
sixty-one handlers, four in five are one or two lines, and the ones listed below are the
whole tail. A new handler that lands above the line is not necessarily wrong, but it has to
say why here first.
"""

from __future__ import annotations

import ast
import pathlib
import unittest
from typing import cast

from tests.support import trees

REPO = pathlib.Path(__file__).resolve().parent.parent.parent
ROUTES = REPO / "httpapi"

# Code lines, not counting the docstring. Chosen from the shape of the tree rather than
# picked: 154 of the 161 handlers sit at or under it.
LIMIT = 8

# What a route legitimately does that a service cannot, each with the reason. These are all
# wire concerns - the response type, the status code, the order work happens in relative to
# the reply - and moving any of them into `common/` would drag HTTP down there with it.
ALLOWED = {
    # A query parameter gates a second permission, which the route's dependencies cannot
    # express: they are evaluated before the parameter is known.
    "games.py:get_game_archive",
    # Unpacks one request model into the service's keyword arguments.
    "collections.py:patch_collection",
    # Maps two service refusals onto the same 400, each with its own message.
    "config.py:put_values",
    # Chooses between answering now and answering after a background task, which decides
    # what the response says rather than what the work is.
    "actions.py:perform_action",
    # Builds a StreamingResponse and the SSE headers that keep proxies from buffering it.
    "events.py:subscribe",
    # Schedules the process to quit after the response is sent; doing it inline would take
    # the process down before the caller was told anything.
    "instance.py:perform_update",
    # Reads the request body chunk by chunk as it arrives, which only the wire layer can do;
    # the writing and the check for room are in `upload_ops`.
    "uploads.py:put_upload_file",
}


def _is_route(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Decorated with `@<something>router.<method>(...)`."""
    dumped = ast.dump(ast.Module(body=cast("list[ast.stmt]", node.decorator_list), type_ignores=[]))
    return "router" in dumped


def _code_lines(node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    """How many lines the body spans once its docstring is set aside."""
    body = node.body
    first = body[0] if body else None
    if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)):
        body = body[1:]
    if not body:
        return 0
    return (body[-1].end_lineno or body[-1].lineno) - body[0].lineno + 1


def _fat_handlers(root: pathlib.Path) -> list[str]:
    out = []
    for path in sorted(root.glob("*.py")):
        tree = trees.tree_for(path)
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            if not _is_route(node):
                continue
            size = _code_lines(node)
            if size > LIMIT and f"{path.name}:{node.name}" not in ALLOWED:
                out.append(f"{path.name}:{node.name} is {size} lines")
    return out


class RouteHandlerTests(unittest.TestCase):
    def test_no_handler_holds_more_than_it_should(self) -> None:
        """A handler over the line is either logic that belongs in a service, or a wire
        concern that belongs in the list above with its reason."""
        self.assertEqual(_fat_handlers(ROUTES), [])

    def test_the_allowlist_only_names_handlers_that_exist(self) -> None:
        """A stale entry silently widens the check."""
        found = set()
        for path in sorted(ROUTES.glob("*.py")):
            tree = trees.tree_for(path)
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and _is_route(node):
                    found.add(f"{path.name}:{node.name}")
        self.assertEqual(sorted(ALLOWED - found), [])

    def test_the_checker_can_actually_fail(self) -> None:
        """A checker nobody has seen fail is one nobody knows the shape of."""
        with self.subTest("a handler over the line is reported"):
            tree = trees.parse_snippet(
                "@router.get('/x')\n"
                "def wide():\n" + "".join(f"    a{i} = {i}\n" for i in range(LIMIT + 1))
            )
            node = tree.body[0]
            assert isinstance(node, ast.FunctionDef)
            self.assertTrue(_is_route(node))
            self.assertGreater(_code_lines(node), LIMIT)
        with self.subTest("a docstring does not count against it"):
            tree = trees.parse_snippet(
                "@router.get('/x')\n"
                "def narrow():\n"
                '    """' + "\n".join(str(i) for i in range(20)) + '"""\n'
                "    return 1\n"
            )
            narrow = tree.body[0]
            assert isinstance(narrow, ast.FunctionDef)
            self.assertEqual(_code_lines(narrow), 1)
        with self.subTest("a plain function is not a handler"):
            helper = trees.parse_snippet("def helper():\n    return 1\n").body[0]
            assert isinstance(helper, ast.FunctionDef)
            self.assertFalse(_is_route(helper))
