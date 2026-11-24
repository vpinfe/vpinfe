"""An async Manager function that reads the page after a wait runs under the page, with
`manager.kit.on_page`, unless it draws into the container it is awaited in.

Read means anything that goes through the current slot: `ui.*`, and any Manager function
that does, followed through calls into other modules and into local closures.
"""

from __future__ import annotations

import ast
import unittest
from collections.abc import Callable, Iterator
from pathlib import Path

from tests.support import trees

ROOT = Path(__file__).resolve().parents[2]

SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)
FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)
WAITS = (ast.Await, ast.AsyncFor, ast.AsyncWith)
LOOPS = (ast.For, ast.AsyncFor, ast.While)
# Shown the same wherever they are made.
UNPLACED = frozenset({"notify", "notification", "run_javascript", "download", "navigate",
                      "context", "dialog", "timer", "clipboard", "on", "keyboard",
                      "add_css", "add_head_html", "add_body_html", "query", "page_title"})


def _own(node: ast.AST) -> Iterator[ast.AST]:
    """What runs as part of `node`; a function defined inside it, but not its body."""
    waiting = list(ast.iter_child_nodes(node))
    while waiting:
        one = waiting.pop()
        yield one
        if not isinstance(one, SCOPES):
            waiting.extend(ast.iter_child_nodes(one))


def _ui_path(expr: ast.AST) -> list[str] | None:
    """`["notify"]` for `ui.notify`, `["column", "classes"]` for `ui.column().classes`."""
    names = []
    while isinstance(expr, (ast.Attribute, ast.Call)):
        if isinstance(expr, ast.Call):
            expr = expr.func
        else:
            names.append(expr.attr)
            expr = expr.value
    if isinstance(expr, ast.Name) and expr.id == "ui" and names:
        return names[::-1]
    return None


def _decorated(fn: ast.AST, name: str) -> bool:
    for one in getattr(fn, "decorator_list", []):
        target = one.func if isinstance(one, ast.Call) else one
        if (isinstance(target, ast.Name) and target.id == name) \
                or (isinstance(target, ast.Attribute) and target.attr == name):
            return True
    return False


def _is_page(fn: ast.AST) -> bool:
    return any(_ui_path(one.func if isinstance(one, ast.Call) else one) == ["page"]
               for one in getattr(fn, "decorator_list", []))


class _Module:
    def __init__(self, name: str, tree: ast.Module) -> None:
        self.name = name
        self.tree = tree
        self.top = {one.name: one for one in tree.body if isinstance(one, FUNCTIONS)}
        self.modules: dict[str, str] = {}
        self.names: dict[str, tuple[str, str]] = {}
        for one in ast.walk(tree):
            if not isinstance(one, ast.ImportFrom):
                continue
            for bound, module_name, imported in trees.manager_imports(one):
                if imported is None:
                    self.modules[bound] = module_name
                else:
                    self.names[bound] = (module_name, imported)
        self.scope_of: dict[ast.AST, ast.AST] = {}
        self.defined: dict[ast.AST, dict[str, ast.AST]] = {}
        for scope in ast.walk(tree):
            if isinstance(scope, SCOPES):
                self.defined[scope] = {}
                for one in _own(scope):
                    self.scope_of[one] = scope
                    if isinstance(one, FUNCTIONS):
                        self.defined[scope][one.name] = one


class Manager:
    """The Manager's modules, with what each of their functions does to the page."""

    def __init__(self, modules: dict[str, ast.Module]) -> None:
        self.modules = {name: _Module(name, tree) for name, tree in modules.items()}
        self._reads: dict[ast.AST, bool] = {}
        self._placed: dict[ast.AST, bool] = {}

    @classmethod
    def read(cls, root: Path) -> Manager:
        folder = root / "manager"
        return cls({path.stem: trees.tree_for(path) for path in sorted(folder.rglob("*.py"))})

    def called(self, module: _Module, call: ast.Call) -> tuple[_Module, ast.AST] | None:
        func = call.func
        if isinstance(func, ast.Name):
            scope = module.scope_of.get(call)
            while scope is not None:
                if func.id in module.defined[scope]:
                    return module, module.defined[scope][func.id]
                scope = module.scope_of.get(scope)
            if func.id in module.top:
                return module, module.top[func.id]
            if func.id in module.names:
                other_name, name = module.names[func.id]
                other = self.modules.get(other_name)
                if other is not None and name in other.top:
                    return other, other.top[name]
        elif isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) \
                and func.value.id in module.modules:
            other = self.modules.get(module.modules[func.value.id])
            if other is not None and func.attr in other.top:
                return other, other.top[func.attr]
        return None

    def _reads_page(self, module: _Module, node: ast.AST) -> bool:
        if isinstance(node, ast.Attribute):
            return _ui_path(node) == ["context"]
        if not isinstance(node, ast.Call):
            return False
        if _ui_path(node.func) is not None:
            return True
        found = self.called(module, node)
        return found is not None and self.reads(*found)

    def _draws_in_place(self, module: _Module, node: ast.AST) -> bool:
        if not isinstance(node, ast.Call):
            return False
        path = _ui_path(node.func)
        if path is not None:
            return path[0] not in UNPLACED
        found = self.called(module, node)
        return found is not None and self.placed(*found)

    def _holds(self, module: _Module, expr: ast.AST) -> bool:
        """Whether `with expr:` names a container for what runs inside it."""
        if isinstance(expr, ast.Call):
            return _ui_path(expr.func) is not None or self.called(module, expr) is not None
        return isinstance(expr, (ast.Name, ast.Attribute, ast.Subscript))

    def unheld(self, module: _Module, fn: ast.AST,
               hit: Callable[[_Module, ast.AST], bool]) -> list[ast.expr]:
        """Each node in `fn`'s own body that `hit` picks out, where no container is named."""
        found: list[ast.expr] = []

        def visit(node: ast.AST, held: bool) -> None:
            if not held and isinstance(node, ast.expr) and hit(module, node):
                found.append(node)
            if isinstance(node, SCOPES):
                return
            if isinstance(node, ast.With):
                inner = held
                for item in node.items:
                    visit(item.context_expr, inner)
                    inner = inner or self._holds(module, item.context_expr)
                for statement in node.body:
                    visit(statement, inner)
                return
            for child in ast.iter_child_nodes(node):
                visit(child, held)

        for child in ast.iter_child_nodes(fn):
            if child not in getattr(fn, "decorator_list", []):
                visit(child, False)
        return found

    def reads(self, module: _Module, fn: ast.AST) -> bool:
        if fn not in self._reads:
            self._reads[fn] = False
            self._reads[fn] = _decorated(fn, "on_page") \
                or bool(self.unheld(module, fn, self._reads_page))
        return self._reads[fn]

    def placed(self, module: _Module, fn: ast.AST) -> bool:
        """Whether `fn` draws into the container it is called in."""
        if fn not in self._placed:
            self._placed[fn] = False
            self._placed[fn] = not _decorated(fn, "on_page") \
                and bool(self.unheld(module, fn, self._draws_in_place))
        return self._placed[fn]

    def late(self, module: _Module, fn: ast.AST) -> list[ast.expr]:
        """Where `fn` reads the page once it has waited."""
        mine = [one for one in _own(fn) if module.scope_of.get(one) is fn]
        starts = []
        for one in mine:
            if isinstance(one, ast.Await):
                starts.append((one.end_lineno, one.end_col_offset))
            elif isinstance(one, (ast.AsyncFor, ast.AsyncWith)):
                starts.append((one.body[0].lineno, one.body[0].col_offset - 1))
        if not starts:
            return []
        waited = min(starts)
        again = {id(inside) for loop in mine if isinstance(loop, LOOPS)
                 if any(isinstance(one, WAITS) for one in _own(loop))
                 for inside in _own(loop)}
        return [one for one in self.unheld(module, fn, self._reads_page)
                if (one.lineno, one.col_offset) > waited or id(one) in again]

    def functions(self) -> Iterator[tuple[str, ast.AsyncFunctionDef, bool, bool, bool]]:
        """Each async function: where, and whether it is a builder, reads the page late,
        and runs under the page."""
        for name, module in self.modules.items():
            for fn in ast.walk(module.tree):
                if isinstance(fn, ast.AsyncFunctionDef) and not _is_page(fn):
                    yield (f"manager/{name.replace('.', '/')}.py:{fn.lineno} {fn.name}", fn,
                           self.placed(module, fn), bool(self.late(module, fn)),
                           _decorated(fn, "on_page"))


def _judged(source: str) -> dict[str, tuple[bool, bool]]:
    """Each async function in `source`: (a builder, reads the page after a wait)."""
    return {fn.name: (builder, late)
            for _where, fn, builder, late, _on
            in Manager({"sample": trees.parse_snippet(source)}).functions()}


class SaidOnThePage(unittest.TestCase):
    found: list[tuple[str, ast.AsyncFunctionDef, bool, bool, bool]]

    @classmethod
    def setUpClass(cls) -> None:
        cls.found = list(Manager.read(ROOT).functions())

    def test_a_handler_that_says_after_a_wait_runs_under_the_page(self) -> None:
        stray = [where for where, _fn, builder, late, on in self.found
                 if late and not builder and not on]

        self.assertEqual(stray, [], "decorate it with manager.kit.on_page.on_page")

    def test_a_builder_draws_where_it_is_awaited(self) -> None:
        moved = [where for where, _fn, builder, _late, on in self.found if builder and on]

        self.assertEqual(moved, [], "under the page, what it draws goes to the page's end; "
                                    "take on_page off")

    def test_it_found_both(self) -> None:
        """An empty sweep passes and measures nothing, which reads the same as clean."""
        self.assertGreaterEqual(sum(on for *_, on in self.found), 150)
        self.assertGreaterEqual(sum(builder for _w, _f, builder, _l, _o in self.found), 10)


class Judging(unittest.TestCase):
    def test_a_notice_after_a_wait_is_late_and_one_before_it_is_not(self) -> None:
        self.assertEqual(_judged("async def after():\n"
                                 "    await go()\n"
                                 "    ui.notify('x')\n"
                                 "async def before():\n"
                                 "    ui.notify('x')\n"
                                 "    await go()\n"),
                         {"after": (False, True), "before": (False, False)})

    def test_one_under_a_container_it_names_is_not(self) -> None:
        self.assertEqual(_judged("async def held():\n"
                                 "    page = ui.context.client\n"
                                 "    await go()\n"
                                 "    with page:\n"
                                 "        ui.notify('x')\n"
                                 "async def taken_late():\n"
                                 "    await go()\n"
                                 "    with ui.context.client:\n"
                                 "        ui.notify('x')\n"),
                         {"held": (False, False), "taken_late": (False, True)})

    def test_one_earlier_in_a_loop_that_waits_is_late(self) -> None:
        self.assertEqual(_judged("async def each(rows):\n"
                                 "    for row in rows:\n"
                                 "        ui.notify(row)\n"
                                 "        await go(row)\n"),
                         {"each": (False, True)})

    def test_what_is_awaited_first_runs_before_the_wait(self) -> None:
        self.assertEqual(_judged("async def ask():\n"
                                 "    await confirm()\n"
                                 "async def confirm():\n"
                                 "    ui.notify('x')\n"),
                         {"ask": (False, False), "confirm": (False, False)})

    def test_a_helper_or_a_closure_that_says_is_followed(self) -> None:
        self.assertEqual(_judged("def say():\n"
                                 "    ui.notify('x')\n"
                                 "async def through_a_helper():\n"
                                 "    await go()\n"
                                 "    say()\n"
                                 "async def through_a_closure():\n"
                                 "    def told():\n"
                                 "        ui.run_javascript('x')\n"
                                 "    await go()\n"
                                 "    told()\n"),
                         {"through_a_helper": (False, True),
                          "through_a_closure": (False, True)})

    def test_a_decorated_handler_reads_the_page_when_it_is_called(self) -> None:
        self.assertEqual(_judged("@on_page\n"
                                 "async def handler():\n"
                                 "    await go()\n"
                                 "    ui.notify('x')\n"
                                 "async def caller():\n"
                                 "    await go()\n"
                                 "    await handler()\n"),
                         {"handler": (False, True), "caller": (False, True)})

    def test_a_dialog_is_not_a_builder_and_a_column_is(self) -> None:
        self.assertEqual(_judged("async def asks():\n"
                                 "    with ui.dialog() as box, ui.card():\n"
                                 "        ui.label('x')\n"
                                 "    await box\n"
                                 "async def builds(held):\n"
                                 "    await go()\n"
                                 "    with held, ui.column():\n"
                                 "        ui.label('x')\n"
                                 "async def draws():\n"
                                 "    await go()\n"
                                 "    with ui.column():\n"
                                 "        ui.label('x')\n"),
                         {"asks": (False, False), "builds": (False, False),
                          "draws": (True, True)})


if __name__ == "__main__":
    unittest.main()
