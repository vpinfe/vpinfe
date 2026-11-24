"""No Manager coroutine reaches a blocking library read through a plain call.

A library read can end in a request to the Manager's own server, which cannot answer
while the loop is waiting on it. A read handed to `offload.io` is an attribute
reference, not a call, so it is not what this looks for.

A plain call is followed into a function of any manager module, a def in scope and a
method of the same class. A lambda, or a def that is only handed on, is left alone: it
runs whenever something calls it, not where it is written. A coroutine is checked on
its own rather than through the one that awaits it.
"""

from __future__ import annotations

import ast
import pathlib
import unittest
from collections.abc import Iterator

from tests.support import trees

REPO = pathlib.Path(__file__).resolve().parent.parent.parent
MANAGER = REPO / "manager"

Function = ast.FunctionDef | ast.AsyncFunctionDef
FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)
Place = tuple[str, str, str]

# (module, function making the call, read): why that read may stay on the loop.
ALLOWED: dict[Place, str] = {}


def _methods(path: str, name: str) -> dict[str, ast.FunctionDef]:
    tree = trees.tree_for(REPO / path)
    cls = next(node for node in tree.body
               if isinstance(node, ast.ClassDef) and node.name == name)
    return {node.name: node for node in cls.body if isinstance(node, ast.FunctionDef)}


def _self_calls(method: ast.FunctionDef) -> set[str]:
    return {node.func.attr for node in ast.walk(method)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name) and node.func.value.id == "self"}


def _closed(methods: dict[str, ast.FunctionDef], seed: set[str]) -> set[str]:
    """`seed`, and every method that reaches one of them through `self`."""
    reached = set(seed)
    calls = {name: _self_calls(method) for name, method in methods.items()}
    grew = True
    while grew:
        grew = False
        for name, callees in calls.items():
            if name not in reached and callees & reached:
                reached.add(name)
                grew = True
    return reached


def _sends() -> set[str]:
    """ApiClient methods that make a request, a cached one included: a cache is cold on
    a fresh page."""
    client = _methods("manager/kit/api.py", "ApiClient")
    return _closed(client, {name for name, method in client.items() if any(
        (isinstance(node, ast.Attribute) and node.attr == "_session")
        or (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "_refuse_the_event_loop")
        for node in ast.walk(method))})


def _blocking(sends: set[str]) -> set[str]:
    """Library methods that can end in one of `sends`."""
    library = _methods("manager/kit/data.py", "Library")
    asks = {name for name, method in library.items() if any(
        isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Attribute)
        and node.func.value.attr == "_client" and node.func.attr in sends
        for node in ast.walk(method))}
    return _closed(library, asks)


def _is_library(node: ast.AST) -> bool:
    if isinstance(node, ast.Name):
        return node.id == "library"
    if isinstance(node, ast.Attribute):
        return (node.attr == "library" and isinstance(node.value, ast.Name)
                and node.value.id == "self")
    return (isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant)
            and node.slice.value == "library")


def _is_client(node: ast.AST, clients: set[str]) -> bool:
    if isinstance(node, ast.Name):
        return node.id in clients
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "ApiClient")


def _own(function: Function) -> Iterator[ast.AST]:
    """The function's own nodes, not those of a lambda or a def inside it."""
    stack: list[ast.AST] = list(function.body)
    while stack:
        node = stack.pop()
        if isinstance(node, (*FUNCTIONS, ast.Lambda)):
            continue
        yield node
        stack.extend(ast.iter_child_nodes(node))


class _Module:
    def __init__(self, name: str, tree: ast.Module, known: set[str]) -> None:
        self.name = name
        self.tree = tree
        self.top = {node.name: node for node in self.tree.body
                    if isinstance(node, FUNCTIONS)}
        self.classes = {node.name: node for node in self.tree.body
                        if isinstance(node, ast.ClassDef)}
        self.parent = {child: node for node in ast.walk(self.tree)
                       for child in ast.iter_child_nodes(node)}
        self.inner: dict[ast.AST, dict[str, Function]] = {}
        for node in ast.walk(self.tree):
            if isinstance(node, FUNCTIONS):
                self.inner.setdefault(self._scope(node), {})[node.name] = node
        self.modules: dict[str, str] = {}
        self.imported: dict[str, tuple[str, str]] = {}
        for node in ast.walk(self.tree):
            if isinstance(node, ast.ImportFrom):
                self._imports(node, known)

    def _imports(self, node: ast.ImportFrom, known: set[str]) -> None:
        if node.level:
            dotted = f"manager.{node.module}" if node.module else "manager"
            node = ast.ImportFrom(module=dotted, names=node.names, level=0)
        for bound, module, name in trees.manager_imports(node):
            if module not in known:
                continue
            if name is None:
                self.modules[bound] = module
            else:
                self.imported[bound] = (module, name)

    def _scope(self, node: ast.AST) -> ast.AST:
        while node in self.parent:
            node = self.parent[node]
            if isinstance(node, (*FUNCTIONS, ast.Lambda, ast.ClassDef)):
                return node
        return self.tree

    def _around(self, function: Function) -> list[ast.AST]:
        """`function` and what encloses it, innermost first."""
        chain: list[ast.AST] = [function]
        while chain[-1] is not self.tree:
            chain.append(self._scope(chain[-1]))
        return chain

    def clients(self, function: Function) -> set[str]:
        """Names bound to a client built in `function` or in one around it."""
        return {target.id for scope in self._around(function) if isinstance(scope, FUNCTIONS)
                for node in _own(scope) if isinstance(node, ast.Assign)
                and _is_client(node.value, set())
                for target in node.targets if isinstance(target, ast.Name)}

    def _method(self, cls: ast.ClassDef, name: str) -> Function | None:
        for node in cls.body:
            if isinstance(node, FUNCTIONS) and node.name == name:
                return node
        for base in cls.bases:
            if isinstance(base, ast.Name) and base.id in self.classes:
                found = self._method(self.classes[base.id], name)
                if found is not None:
                    return found
        return None

    def callee(self, call: ast.Call, function: Function,
               modules: dict[str, _Module]) -> tuple[_Module, Function] | None:
        func = call.func
        around = self._around(function)
        if isinstance(func, ast.Name):
            for scope in around:
                if isinstance(scope, (*FUNCTIONS, ast.Module)) \
                        and func.id in self.inner.get(scope, {}):
                    return self, self.inner[scope][func.id]
            if func.id in self.imported:
                module, name = self.imported[func.id]
                if name in modules[module].top:
                    return modules[module], modules[module].top[name]
            return None
        if not isinstance(func, ast.Attribute) or not isinstance(func.value, ast.Name):
            return None
        if func.value.id == "self":
            cls = next((scope for scope in around if isinstance(scope, ast.ClassDef)), None)
            found = self._method(cls, func.attr) if cls is not None else None
            return (self, found) if found is not None else None
        other = modules.get(self.modules.get(func.value.id, ""))
        if other is not None and func.attr in other.top:
            return other, other.top[func.attr]
        return None


def _offenders(modules: dict[str, _Module], library: set[str],
               client: set[str]) -> dict[Place, list[str]]:
    """Each place a blocking read is made, with the trails from a coroutine to it."""
    found: dict[Place, list[str]] = {}
    for module in modules.values():
        for coroutine in ast.walk(module.tree):
            if not isinstance(coroutine, ast.AsyncFunctionDef):
                continue
            seen: set[int] = set()
            stack: list[tuple[_Module, Function, tuple[str, ...]]] = [
                (module, coroutine, (f"{module.name}.{coroutine.name}",))]
            while stack:
                here, function, trail = stack.pop()
                clients = here.clients(function)
                for node in _own(function):
                    if not isinstance(node, ast.Call):
                        continue
                    func = node.func
                    if isinstance(func, ast.Attribute) and (
                            (func.attr in library and _is_library(func.value))
                            or (func.attr in client and _is_client(func.value, clients))):
                        found.setdefault((here.name, function.name, func.attr), []).append(
                            f"{' > '.join(trail)} > {func.attr} (line {node.lineno})")
                        continue
                    target = here.callee(node, function, modules)
                    if target is None or isinstance(target[1], ast.AsyncFunctionDef) \
                            or id(target[1]) in seen:
                        continue
                    seen.add(id(target[1]))
                    stack.append((target[0], target[1], trail + (target[1].name,)))
    return found


def _manager() -> dict[Place, list[str]]:
    sends = _sends()
    paths = sorted(MANAGER.rglob("*.py"))
    known = {path.stem for path in paths}
    modules = {path.stem: _Module(path.stem, trees.tree_for(path), known) for path in paths}
    return _offenders(modules, _blocking(sends), sends)


def _trails(found: dict[Place, list[str]]) -> list[str]:
    return sorted(trail for trails in found.values() for trail in trails)


class ManagerReadsOffTheLoop(unittest.TestCase):
    def _found(self, **sources: str) -> list[str]:
        sends = _sends()
        known = set(sources)
        modules = {name: _Module(name, trees.parse_snippet(source), known)
                  for name, source in sources.items()}
        return _trails(_offenders(modules, _blocking(sends), sends))

    def test_the_check_sees_a_read_three_calls_down(self) -> None:
        self.assertIn("vps_releases", _blocking(_sends()))
        chain = (
            "async def draw(context):\n"
            "    _slot(context)\n"
            "def _slot(context):\n"
            "    _match_line(context)\n"
            "def _match_line(context):\n"
            "    context['library'].vps_releases('x', 'wheelArtFiles')\n")
        self.assertEqual(self._found(workbench=chain),
                         ["workbench.draw > _slot > _match_line > vps_releases (line 6)"])

    def test_the_check_passes_a_read_handed_to_a_worker(self) -> None:
        handed = (
            "async def draw(context):\n"
            "    library = context['library']\n"
            "    await offload.io(library.vps_releases, 'x', 'wheelArtFiles')\n"
            "    ui.button(on_click=lambda: library.vps_releases('x', 'y'))\n"
            "    def later():\n"
            "        library.vps_releases('x', 'y')\n"
            "    ui.button(on_click=later)\n")
        self.assertEqual(self._found(workbench=handed), [])

    def test_the_check_follows_a_call_into_another_module(self) -> None:
        page = (
            "from manager.settings import sections as parts\n"
            "from .media import build\n"
            "async def show(library):\n"
            "    parts.overview(library)\n"
            "    build(library)\n")
        sections = "def overview(library):\n    library.library_policy()\n"
        media = "def build(library):\n    library.load_media_rows()\n"
        self.assertEqual(self._found(page=page, sections=sections, media=media),
                         ["page.show > build > load_media_rows (line 2)",
                          "page.show > overview > library_policy (line 2)"])

    def test_the_check_follows_a_def_in_scope_and_a_method(self) -> None:
        page = (
            "async def show(library):\n"
            "    def render():\n"
            "        library.library_policy()\n"
            "    render()\n"
            "class Sources:\n"
            "    def _host(self):\n"
            "        return self.library.devices()\n"
            "    async def open(self):\n"
            "        self._host()\n")
        self.assertEqual(self._found(page=page),
                         ["page.open > _host > devices (line 7)",
                          "page.show > render > library_policy (line 3)"])

    def test_the_check_sees_a_request_through_a_client_made_in_place(self) -> None:
        page = (
            "async def show():\n"
            "    ApiClient().jobs()\n"
            "    client = ApiClient()\n"
            "    client.devices()\n"
            "    await offload.io(client.devices)\n")
        self.assertEqual(self._found(page=page),
                         ["page.show > devices (line 4)", "page.show > jobs (line 2)"])

    def test_no_manager_coroutine_reaches_one(self) -> None:
        found = _manager()
        held = {place: trails for place, trails in found.items() if place not in ALLOWED}
        self.assertEqual(_trails(held), [], "a library read from a coroutine goes "
                                            "through `offload.io` or `run.io_bound`")

    def test_every_allowed_read_is_still_found(self) -> None:
        self.assertEqual(sorted(set(ALLOWED) - set(_manager())), [])


if __name__ == "__main__":
    unittest.main()
