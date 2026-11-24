"""Which settings are read only while VPinFE starts, from the source alone.

A read runs once when it sits at module level outside a loop, or in a function whose every
call sits in code that runs once. Anything else is live: a function that is decorated or
passed as a value (a callback, a thread target, an event handler), a lambda, a nested
function, a loop body, or anything called from live code. Calls are matched by name, so a
method called `value` is called by every `.value()` in the tree.

What it cannot see: a function reached through a string or `getattr` reads as run-once, and
a read through a variable section or key, or a wrapper such as `config_access`, is not a
read here at all.
"""

from __future__ import annotations

import ast
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from tests.support import trees
from tests.support.project import project_files

# The readers take a source, a section and a key; the store's own accessors take a
# section and a key. Writes are not reads of the value.
READERS = {"cfg_get", "cfg_bool", "cfg_int", "cfg_list", "cfg_set"}
READ_ONLY = READERS - {"cfg_set"}
STORE_ACCESSORS = {"value", "set_value"}
STORES = {"store", "config_store", "ini_config"}

# A decorator that only changes how a method is called leaves it called from the same
# places. Any other decorator may hand the function to a router or a registry.
PLAIN_DECORATORS = {"classmethod", "staticmethod"}

ONCE = "once"
LIVE = "live"

Scope = str | tuple[str, str]


@dataclass(frozen=True)
class Read:
    file: str
    line: int
    section: str
    key: str
    scope: Scope


@dataclass
class _Def:
    ident: tuple[str, str]
    name: str
    cls: str | None
    live: bool


@dataclass
class _Facts:
    defs: dict[tuple[str, str], _Def] = field(default_factory=dict)
    bases: dict[str, set[str]] = field(default_factory=dict)
    calls: list[tuple[Scope, str | None, str]] = field(default_factory=list)
    referenced: set[str] = field(default_factory=set)
    reads: list[Read] = field(default_factory=list)

    def lineage(self, cls: str) -> set[str]:
        """The class and every class it inherits from."""
        found, pending = set(), [cls]
        while pending:
            name = pending.pop()
            if name not in found:
                found.add(name)
                pending.extend(self.bases.get(name, ()))
        return found


class _Walk(ast.NodeVisitor):
    def __init__(self, rel: str, facts: _Facts) -> None:
        self.rel = rel
        self.facts = facts
        self.classes: list[str] = []
        self.functions: list[tuple[str, str]] = []
        self.aliases: dict[str, str] = {}
        self.loops = 0

    def _scope(self) -> Scope:
        if self.loops:
            return LIVE
        if not self.functions:
            return ONCE
        inner = self.functions[-1]
        return inner if len(self.functions) == 1 and inner[1] != "<lambda>" else LIVE

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.facts.bases.setdefault(node.name, set()).update(
            b.id if isinstance(b, ast.Name) else b.attr
            for b in node.bases if isinstance(b, (ast.Name, ast.Attribute)))
        for outer in node.decorator_list:
            self.visit(outer)
        self.classes.append(node.name)
        for statement in node.body:
            self.visit(statement)
        self.classes.pop()

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for imported in node.names:
            if imported.asname:
                self.aliases[imported.asname] = imported.name

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._function(node)

    def _function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        for outer in (*node.decorator_list, *node.args.defaults,
                      *(d for d in node.args.kw_defaults if d is not None)):
            self.visit(outer)
        cls = self.classes[-1] if self.classes and not self.functions else None
        ident = (self.rel, f"{cls}.{node.name}" if cls else node.name)
        registered = any(not (isinstance(d, ast.Name) and d.id in PLAIN_DECORATORS)
                         for d in node.decorator_list)
        # A name defined twice cannot be told apart by name, so neither is trusted.
        self.facts.defs[ident] = _Def(ident, node.name, cls, registered or ident in self.facts.defs)
        outside, self.loops = self.loops, 0
        self.functions.append(ident)
        for statement in node.body:
            self.visit(statement)
        self.functions.pop()
        self.loops = outside

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self.functions.append((self.rel, "<lambda>"))
        self.visit(node.body)
        self.functions.pop()

    def _repeated(self, *parts: ast.AST | list[ast.stmt]) -> None:
        self.loops += 1
        for part in parts:
            for node in part if isinstance(part, list) else [part]:
                self.visit(node)
        self.loops -= 1

    def visit_For(self, node: ast.For) -> None:
        self.visit(node.iter)
        self._repeated(node.target, node.body, node.orelse)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self.visit(node.iter)
        self._repeated(node.target, node.body, node.orelse)

    def visit_While(self, node: ast.While) -> None:
        self._repeated(node.test, node.body, node.orelse)

    def _comprehension(self, node: ast.expr) -> None:
        self._repeated(*ast.iter_child_nodes(node))

    def visit_ListComp(self, node: ast.ListComp) -> None:
        self._comprehension(node)

    def visit_SetComp(self, node: ast.SetComp) -> None:
        self._comprehension(node)

    def visit_DictComp(self, node: ast.DictComp) -> None:
        self._comprehension(node)

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        self._comprehension(node)

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Load):
            self.facts.referenced.add(self.aliases.get(node.id, node.id))

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if isinstance(node.ctx, ast.Load):
            self.facts.referenced.add(node.attr)
        self.visit(node.value)

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        scope = self._scope()
        if isinstance(func, ast.Name):
            name = self.aliases.get(func.id, func.id)
            if name == "cls" and self.classes:
                name = self.classes[-1]
            self.facts.calls.append((scope, None, name))
        elif isinstance(func, ast.Attribute):
            receiver = func.value.id if isinstance(func.value, ast.Name) else None
            self.facts.calls.append((scope, receiver, func.attr))
            if receiver is None:
                self.visit(func.value)
        else:
            self.visit(func)
        self._read(node, scope)
        for argument in (*node.args, *(k.value for k in node.keywords)):
            self.visit(argument)

    def _read(self, node: ast.Call, scope: Scope) -> None:
        func = node.func
        name = getattr(func, "id", None) or getattr(func, "attr", None)
        if name in READ_ONLY and len(node.args) >= 3:
            section, key = node.args[1], node.args[2]
        elif (name == "value" and isinstance(func, ast.Attribute) and len(node.args) >= 2
              and (getattr(func.value, "attr", None) or getattr(func.value, "id", "")) in STORES):
            section, key = node.args[0], node.args[1]
        else:
            return
        if (isinstance(section, ast.Constant) and isinstance(section.value, str)
                and isinstance(key, ast.Constant) and isinstance(key.value, str)):
            self.facts.reads.append(Read(self.rel, node.lineno, section.value, key.value, scope))


def _gather(trees: Mapping[str, ast.Module]) -> _Facts:
    facts = _Facts()
    for rel, tree in trees.items():
        _Walk(rel, facts).visit(tree)
    return facts


def _targets(facts: _Facts, named: Mapping[str, list[_Def]], receiver: str | None,
             name: str) -> Iterable[_Def]:
    """The definitions a call by this name can reach."""
    if receiver in facts.bases:
        lineage = facts.lineage(receiver)
        return [d for d in named.get(name, ()) if d.cls in lineage]
    if receiver is None and name in facts.bases:
        lineage = facts.lineage(name)
        return [d for d in named.get("__init__", ()) if d.cls in lineage]
    return named.get(name, ())


def _handed_over(facts: _Facts, definition: _Def) -> bool:
    """Whether the function, or for a constructor its class, is used as a value."""
    if definition.name != "__init__":
        return definition.name in facts.referenced
    return any(definition.cls in facts.lineage(cls) for cls in facts.referenced
               if cls in facts.bases)


def run_once(trees: Mapping[str, ast.Module]) -> tuple[list[Read], Callable[[Read], bool]]:
    """Every literal read of a setting, and whether a read runs once per process."""
    facts = _gather(trees)
    named: dict[str, list[_Def]] = {}
    for definition in facts.defs.values():
        named.setdefault(definition.name, []).append(definition)
    called_from: dict[tuple[str, str], set[Scope]] = {}
    for scope, receiver, name in facts.calls:
        for target in _targets(facts, named, receiver, name):
            called_from.setdefault(target.ident, set()).add(scope)
    once: set[tuple[str, str]] = set()
    changed = True
    while changed:
        changed = False
        for ident, scopes in called_from.items():
            definition = facts.defs[ident]
            if ident in once or definition.live or _handed_over(facts, definition):
                continue
            if all(s == ONCE or s in once for s in scopes):
                once.add(ident)
                changed = True

    def at_startup(read: Read) -> bool:
        return read.scope == ONCE or read.scope in once

    return facts.reads, at_startup


def source_trees() -> dict[str, ast.Module]:
    """The parsed source the check reads: every tracked module but the tests and vendored code."""
    root = Path(__file__).resolve().parent.parent.parent
    found = {}
    for path in project_files("*.py"):
        rel = path.relative_to(root).as_posix()
        if rel.startswith(("tests/", "third_party/")):
            continue
        try:
            found[rel] = trees.tree_for(path)
        except SyntaxError:
            continue
    return found
