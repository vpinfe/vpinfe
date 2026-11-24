"""A line whose hover says more is drawn by `panel.line`, which marks it as having more.

What this can be certain of: a tooltip whose words are a failure's - `why(...)`, or a
record's `error` or `detail` - and a tooltip on a caption, a `manager-help` line, whether
the tooltip is chained on it, set on a name bound to it, or opened inside `with` it. What
it does not follow: a detail carried in a variable onto a line that is not a caption.
"""

from __future__ import annotations

import ast
import unittest
from collections.abc import Callable, Iterator
from pathlib import Path

from tests.support import trees

ROOT = Path(__file__).resolve().parents[2]
MANAGER = ROOT / "manager"
HELPER = ("manager/kit/panel.py", "line")

CAPTION = "manager-help"
REASON_KEYS = {"error", "detail"}

Lookup = Callable[[str], list[ast.expr]]


def _named(node: ast.expr) -> str:
    return getattr(node, "id", None) or getattr(node, "attr", None) or ""


def _chain(node: ast.expr) -> Iterator[ast.Call]:
    """Each call in `a(...).b(...).c(...)`, outermost first."""
    while isinstance(node, ast.Call):
        yield node
        node = node.func.value if isinstance(node.func, ast.Attribute) else node.func


def _words(node: ast.expr) -> str:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return " ".join(_words(part) for part in node.values)
    return ""


def _is_caption(node: ast.expr, lookup: Lookup, depth: int = 0) -> bool:
    if isinstance(node, ast.Name) and depth < 3:
        return any(_is_caption(bound, lookup, depth + 1) for bound in lookup(node.id))
    return any(_named(call.func) == "classes" and call.args
               and CAPTION in _words(call.args[0]).split()
               for call in _chain(node))


def _is_reason(node: ast.AST) -> bool:
    if isinstance(node, ast.Call) and _named(node.func) == "why":
        return True
    if isinstance(node, ast.Subscript):
        return _words(node.slice) in REASON_KEYS
    return (isinstance(node, ast.Call) and _named(node.func) == "get" and bool(node.args)
            and _words(node.args[0]) in REASON_KEYS)


def _carries_reason(node: ast.expr, lookup: Lookup, depth: int = 0) -> bool:
    for one in ast.walk(node):
        if _is_reason(one):
            return True
        if isinstance(one, ast.Name) and depth < 3 and any(
                _carries_reason(bound, lookup, depth + 1) for bound in lookup(one.id)):
            return True
    return False


def _is_ui_tooltip(node: ast.expr) -> bool:
    return any(isinstance(call.func, ast.Attribute) and call.func.attr == "tooltip"
               and isinstance(call.func.value, ast.Name) and call.func.value.id == "ui"
               for call in _chain(node))


def _bindings(body: list[ast.stmt]) -> dict[str, list[ast.expr]]:
    """What each name in one scope is bound to, not looking into the scopes inside it."""
    bound: dict[str, list[ast.expr]] = {}
    pending: list[ast.AST] = list(body)
    while pending:
        node = pending.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda,
                             ast.ClassDef)):
            continue
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    bound.setdefault(target.id, []).append(node.value)
        if isinstance(node, ast.withitem) and isinstance(node.optional_vars, ast.Name):
            bound.setdefault(node.optional_vars.id, []).append(node.context_expr)
        pending.extend(ast.iter_child_nodes(node))
    return bound


class _Reader(ast.NodeVisitor):
    """Every tooltip drawn on a line with a detail, outside the helper."""

    def __init__(self, path: str) -> None:
        self.path = path
        self.scopes: list[dict[str, list[ast.expr]]] = []
        self.within: list[list[ast.expr]] = []
        self.functions: list[str] = []
        self.found: list[tuple[int, str]] = []

    def lookup(self, name: str) -> list[ast.expr]:
        for scope in reversed(self.scopes):
            if name in scope:
                return scope[name]
        return []

    def visit_Module(self, node: ast.Module) -> None:
        self.scopes.append(_bindings(node.body))
        self.within.append([])
        self.generic_visit(node)

    def _scope(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        if (self.path, node.name) == HELPER and not self.functions:
            return
        self.scopes.append(_bindings(node.body))
        self.within.append([])
        self.functions.append(node.name)
        self.generic_visit(node)
        self.functions.pop()
        self.within.pop()
        self.scopes.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._scope(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._scope(node)

    def _with(self, node: ast.With | ast.AsyncWith) -> None:
        for item in node.items:
            self.visit(item)
        self.within[-1].extend(item.context_expr for item in node.items)
        for line in node.body:
            self.visit(line)
        del self.within[-1][-len(node.items):]

    def visit_With(self, node: ast.With) -> None:
        self._with(node)

    def visit_AsyncWith(self, node: ast.AsyncWith) -> None:
        self._with(node)

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "tooltip":
            said = node.args[0] if node.args else None
            if isinstance(func.value, ast.Name) and func.value.id == "ui":
                on_caption = any(_is_caption(one, self.lookup) for one in self.within[-1])
            else:
                on_caption = _is_caption(func.value, self.lookup)
            if on_caption or (said is not None and _carries_reason(said, self.lookup)):
                self.found.append((node.lineno, ast.unparse(node)[:90]))
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            if (isinstance(target, ast.Attribute) and target.attr == "text"
                    and isinstance(target.value, ast.Name)
                    and any(_is_ui_tooltip(bound) for bound in self.lookup(target.value.id))
                    and _carries_reason(node.value, self.lookup)):
                self.found.append((node.lineno, ast.unparse(node)[:90]))
        self.generic_visit(node)


def offenders(tree: ast.Module, path: str = "") -> list[tuple[int, str]]:
    reader = _Reader(path)
    reader.visit(tree)
    return sorted(reader.found)


class ALineWithMoreToSayShowsIt(unittest.TestCase):

    def test_every_line_with_a_detail_is_drawn_by_the_helper(self) -> None:
        found = []
        for path in sorted(MANAGER.rglob("*.py")):
            name = path.relative_to(ROOT).as_posix()
            found += [f"{name}:{line} {said}"
                      for line, said in offenders(trees.tree_for(path), name)]
        self.assertEqual(found, [], "draw it with panel.line(text, hint=...)")

    def test_each_way_is_read(self) -> None:
        source = ("def draw(library, said, lens):\n"
                  "    try:\n"
                  "        library.read()\n"
                  "    except OSError as exc:\n"
                  "        ui.label(t('x.could_not')).tooltip(why(exc))\n"
                  "        ui.label(t('x.could_not')).classes('text-xs').tooltip(ctx.why(exc))\n"
                  "    age = ui.label(read_state(said)).classes('manager-help')\n"
                  "    age.tooltip(t('x.more'))\n"
                  "    ui.label(t('x.a')).classes('manager-help px-3').tooltip(hover)\n"
                  "    chip = ui.label(t('x.b')).classes('manager-member-chip')\n"
                  "    chip.tooltip(str(said['error']))\n"
                  "    ui.label(t('x.c')).tooltip(said.get('detail') or '')\n"
                  "    detail = str(lens['error'])\n"
                  "    ui.label(t('x.d')).tooltip(detail)\n"
                  "    note = ui.label('').classes(f'manager-help {extra}')\n"
                  "    def show(detail):\n"
                  "        with note:\n"
                  "            ui.tooltip(detail)\n"
                  "    whose = ui.tooltip('')\n"
                  "    def watch(failed):\n"
                  "        whose.text = str(failed.get('error') or '')\n"
                  "    ui.button(icon='x').tooltip(t('x.hint'))\n"
                  "    chip.tooltip(why)\n"
                  "    ui.label(name).classes('truncate').tooltip(name)\n"
                  "    panel.line(t('x.e'), hint=why(exc))\n"
                  "    whose.text = t('x.value')\n")

        said = [line for line, _ in offenders(trees.parse_snippet(source))]

        self.assertEqual(said, [5, 6, 8, 9, 11, 12, 14, 18, 21])

    def test_the_helper_is_left_to_draw_them(self) -> None:
        source = ("def line(text, *, hint=''):\n"
                  "    shown = ui.label(text).classes('manager-help')\n"
                  "    shown.tooltip(hint)\n"
                  "def elsewhere(hint):\n"
                  "    ui.label('').classes('manager-help').tooltip(hint)\n")

        self.assertEqual(
            [line for line, _ in offenders(trees.parse_snippet(source), HELPER[0])], [5])

    def test_the_helper_is_there(self) -> None:
        """The exemption is only honest while the thing it defers to exists."""
        tree = trees.tree_for(ROOT / HELPER[0])
        self.assertIn(HELPER[1], {node.name for node in tree.body
                                  if isinstance(node, ast.FunctionDef)})


if __name__ == "__main__":
    unittest.main()
