"""A value change on the Manager never draws the page again.

The handler of a value control - a switch, a select, a field, a number - saves what the
user chose. Whatever else on the page reads that value is refilled where it stands
(`panel.region(...).refill()`) or left alone; the page is not rebuilt, because a rebuild
dims the view, throws the scroll position and focus away and replaces the control the
pointer is on. A rebuild is for structure: a row added or removed, a subject changed.

The scan reads the calls that make a value control - `panel.switch`-shaped constructors,
the `Select` classes, and `.on_value_change(handler)` - and follows what the handler can
reach by name, in its file and in the manager modules that file imports from: a local
function, the function a name was assigned from, a function it calls. It looks for a verb
that draws the page again. It does not follow a method on an object, nor a handler passed
into another function as an argument; the drive test covers those. What it finds is either
fixed or listed in PENDING with what converts it, and the list only shrinks.
"""

from __future__ import annotations

import ast
import functools
import unittest
from pathlib import Path

from tests.support import trees

ROOT = Path(__file__).resolve().parents[2]
MANAGER = ROOT / "manager"

# A name that draws the whole view or panel again, or takes the page again.
REBUILD_VERBS = frozenset({
    "redraw", "rerender", "rebuild", "read_then_render", "clear_workbench",
})

# The constructors of a value control: the user changes a value and the handler saves it.
VALUE_CONTROLS = frozenset({
    "switch", "select", "multi_select", "choice_buttons", "segmented", "combo", "number",
    "named_number", "field", "swatch", "path_field", "choice", "toggle", "checkbox",
    "radio", "input", "textarea",
})
# Helpers that draw a value control and take its handler.
CONTROL_HELPERS = frozenset({"_switch", "_override"})
# The modules whose attribute of that name is the control: `panel.switch`, `ui.switch`,
# and a `*Select` class from `panel` or `dropdown`. A control wired after it is made
# through `.on_value_change(handler)` is found by that call.
CONTROL_MODULES = frozenset({"panel", "ui", "dropdown"})
# Keyword arguments of a control that open something else and never handle the value:
# `browse` opens a folder picker, which has its own dialog to draw.
NOT_HANDLERS = frozenset({"browse"})
# The modules whose calls draw an element and return it.
ELEMENT_MODULES = CONTROL_MODULES | {"grid", "frame"}

WORKBENCH = "Workbench panels: save, then refill the mark or block that reads the value."
LAUNCHERS = "Launcher panels: save, then refill what reads the value."
UNREAD = "Not classified yet: convert it, or say here why it stays."

# (file under manager/, function enclosing the control) -> what converts it. Each is a
# handler that draws its page again today. The scan follows names inside one file and the
# modules it imports from, so a handler handed through another function's argument is
# the drive test's to catch.
PENDING: dict[tuple[str, str], str] = {
    ("workbench/launcher.py", "_launcher_details"): LAUNCHERS,
    ("workbench/launcher.py", "launcher_rows.nvram"): LAUNCHERS,
    ("frontend/launchers.py", "ask_name.asked_field.drawn"): UNREAD,
    ("kit/folder_picker.py", "draw_browser"): UNREAD,
}

# Handlers that draw the page again because the user went somewhere, or because the write
# changed what the page lists, so rows come and go. Each stays, with the reason.
NAVIGATION: dict[tuple[str, str], str] = {
    ("workbench/tag.py", "_tag_details"):
        "A renamed tag is another tag: its games, color and address all follow the name, "
        "so the panel follows it. Description and color are saved in place.",
    ("workbench/collection.py", "_text_control.draw"):
        "A renamed collection is another address and another row, and the panel follows "
        "it. Description and the other fields are saved in place.",
    ("devices/devices.py", "_where_rows"):
        "A mobile device's address and port change the endpoint the page reads its "
        "status from, and the probe that follows redraws the page anyway.",
    ("devices/devices.py", "_send_picker.draw"):
        "Sending changes the games the device carries, and that list is read from the "
        "device.",
    ("workbench/all_settings.py", "_all_settings"):
        "A write that changes which settings are listed, or whose value is in force, "
        "draws the panel again. Every other write marks the rows in place.",
    ("workbench/all_settings.py", "_area_search"):
        "What is typed carries on in All Settings, which opens.",
}

# Neither number goes up. Raising one takes a line in a commit saying why. The sites are
# counted as well as the functions, since a function already listed can gain a control.
CEILING = 4
SITES_CEILING = 16


def _is_rebuild(node: ast.AST) -> bool:
    if isinstance(node, ast.Name):
        return node.id in REBUILD_VERBS
    if isinstance(node, ast.Attribute):
        if node.attr in REBUILD_VERBS:
            return True
        # ui.navigate.reload
        return (node.attr == "reload" and isinstance(node.value, ast.Attribute)
                and node.value.attr == "navigate")
    if isinstance(node, ast.Subscript):
        # context["rebuild"]
        key = node.slice
        return isinstance(key, ast.Constant) and key.value in REBUILD_VERBS
    return False


_SCOPES = (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def _is_region(node: ast.AST) -> bool:
    """`region(build)` or `panel.region(build)`: the part of a page a handler refills."""
    return isinstance(node, ast.Call) and (
        (isinstance(node.func, ast.Name) and node.func.id == "region")
        or (isinstance(node.func, ast.Attribute) and node.func.attr == "region"))


def _makes_an_element(node: ast.AST) -> bool:
    """`ui.row()`, `panel.search(...)`, `grid.build(...)`: a call that returns what was
    drawn, so the name it is assigned to is an element, not a handler. The handlers handed
    to it are found where the call is."""
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in ELEMENT_MODULES)


def _bound_in(scope: ast.AST) -> dict[str, list[ast.AST]]:
    """What each name directly in `scope` stands for: a function it defines, or the
    function-valued expression it was assigned. Nested scopes are their own. A name
    assigned a region stands for nothing: a handler that refills it runs `refill`, and the
    region's build is drawn by the region, as a part of the page. A parameter, and a name
    assigned an element, stand for nothing either, and hide a function of the same name
    outside."""
    bound: dict[str, list[ast.AST]] = {}
    if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
        args = scope.args
        for one in (*args.posonlyargs, *args.args, *args.kwonlyargs,
                    *filter(None, (args.vararg, args.kwarg))):
            bound[one.arg] = []

    def visit(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                bound.setdefault(child.name, []).append(child)
            elif isinstance(child, ast.ClassDef):
                continue
            else:
                if isinstance(child, ast.Assign):
                    stands_for = isinstance(child.value, (ast.Lambda, ast.Call, ast.Name)) \
                        and not _is_region(child.value) \
                        and not _makes_an_element(child.value)
                    for target in child.targets:
                        if isinstance(target, ast.Name):
                            bound.setdefault(target.id, [])
                            if stands_for:
                                bound[target.id].append(child.value)
                visit(child)

    visit(scope)
    return bound


class _File:
    """A file's names, each looked up the way Python looks it up: in the function that
    uses it, then the ones around it, then the module, then what the module imports."""

    def __init__(self, tree: ast.Module, files: dict[str, _File]) -> None:
        self.files = files
        self.up: dict[int, ast.AST] = {}
        self.scope: dict[int, dict[str, list[ast.AST]]] = {}
        self.names: dict[str, tuple[str, str]] = {}
        self.modules: dict[str, str] = {}
        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                self.up[id(child)] = node
            if isinstance(node, _SCOPES):
                self.scope[id(node)] = _bound_in(node)
            if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                for one in node.names:
                    self.names[one.asname or one.name] = (node.module, one.name)
                    self.modules[one.asname or one.name] = f"{node.module}.{one.name}"
        self.top = tree

    def named(self, node: ast.AST) -> list[tuple[_File, ast.AST]]:
        """The definitions `node` stands for, as (the file they are in, the definition)."""
        if isinstance(node, ast.Name):
            at: ast.AST | None = self.up.get(id(node))
            while at is not None:
                here = self.scope.get(id(at), {})
                if node.id in here:
                    return [(self, one) for one in here[node.id]]
                at = self.up.get(id(at))
            if node.id in self.names:
                return self._there(*self.names[node.id])
            return []
        if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                and node.value.id in self.modules):
            return self._there(self.modules[node.value.id], node.attr)
        return []

    def _there(self, module: str, name: str) -> list[tuple[_File, ast.AST]]:
        other = self.files.get(module)
        if other is None:
            return []
        return [(other, one) for one in other.scope[id(other.top)].get(name, ())]

    def reaches_rebuild(self, root: ast.AST, seen: set[int] | None = None) -> bool:
        """True if `root`, or a function it names, draws the page again."""
        seen = set() if seen is None else seen
        if id(root) in seen:
            return False
        seen.add(id(root))
        for node in ast.walk(root):
            # A name the file defines stands for what it does, not for what it is called.
            defined = self.named(node)
            if not defined and _is_rebuild(node):
                return True
            if isinstance(node, (ast.Attribute, ast.Subscript)) and _is_rebuild(node):
                return True
            for owner, target in defined:
                if owner.reaches_rebuild(target, seen):
                    return True
        return False


def _index() -> dict[str, _File]:
    files: dict[str, _File] = {}
    for path in sorted(MANAGER.rglob("*.py")):
        module = ".".join(path.relative_to(ROOT).with_suffix("").parts)
        files[module] = _File(trees.tree_for(path), files)
    return files


def _control_call(node: ast.AST) -> str | None:
    """The control's name if `node` is `panel.switch(...)`-shaped, else None."""
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        return node.func.id if node.func.id in CONTROL_HELPERS else None
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
        return None
    attr = node.func.attr
    if attr == "on_value_change":
        return attr
    owner = node.func.value
    if isinstance(owner, ast.Name) and owner.id in CONTROL_MODULES and (
            attr in VALUE_CONTROLS or attr.endswith("Select")):
        return attr
    return None


def offenders(tree: ast.Module, files: dict[str, _File] | None = None
              ) -> list[tuple[int, str, str]]:
    """(line, enclosing function as `outer.inner`, control) of every value control whose
    handler reaches a verb that draws the page again."""
    files = {} if files is None else files
    file = _File(tree, files)
    found: list[tuple[int, str, str]] = []

    def visit(node: ast.AST, where: tuple[str, ...]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                visit(child, (*where, child.name))
                continue
            control = _control_call(child)
            if control is not None and isinstance(child, ast.Call):
                handlers = [*child.args, *(one.value for one in child.keywords
                                           if one.arg not in NOT_HANDLERS)]
                if any(file.reaches_rebuild(handler) for handler in handlers):
                    found.append((child.lineno, ".".join(where), control))
            visit(child, where)

    visit(tree, ())
    return found


@functools.cache
def _manager() -> dict[str, list[tuple[int, str, str]]]:
    files = _index()
    out: dict[str, list[tuple[int, str, str]]] = {}
    for path in sorted(MANAGER.rglob("*.py")):
        found = offenders(trees.tree_for(path), files)
        if found:
            out[path.relative_to(MANAGER).as_posix()] = found
    return out


class AValueHandlerDoesNotRebuildThePage(unittest.TestCase):
    def test_every_handler_that_rebuilds_is_listed_with_what_converts_it(self) -> None:
        stray = [f"manager/{name}:{line} in {where or 'the module'} ({control})"
                 for name, found in _manager().items()
                 for line, where, control in found
                 if (name, where) not in PENDING and (name, where) not in NAVIGATION]

        self.assertEqual([], stray,
                         "A value handler saves and refills what reads the value; it does "
                         "not redraw the page. Refill the region (`panel.region`), or list "
                         "the handler in PENDING with what converts it.")

    def test_every_listed_handler_still_rebuilds(self) -> None:
        present = {(name, where) for name, found in _manager().items()
                   for _line, where, _control in found}

        self.assertEqual([], sorted((set(PENDING) | set(NAVIGATION)) - present),
                         "Converted: remove it from the list.")

    def test_the_list_only_comes_down(self) -> None:
        self.assertLessEqual(len(PENDING), CEILING)
        self.assertLessEqual(sum(len(found) for found in _manager().values()), SITES_CEILING)

    def test_the_scan_finds_a_handler_that_rebuilds(self) -> None:
        tree = trees.parse_snippet(
            "def page(rerender):\n"
            "    async def save(value):\n"
            "        rerender()\n"
            "    panel.field('x', save)\n"
            "    panel.switch(True, lambda e: rebuild())\n"
            "    panel.select(['a'], 'a', _after(save))\n"
            "    panel.number(1, lambda v: store(v))\n"
            "def _after(save):\n"
            "    async def saved(v):\n"
            "        redraw()\n"
            "    return saved\n")

        self.assertEqual([(4, "page", "field"), (5, "page", "switch"), (6, "page", "select")],
                         offenders(tree))

    def test_a_handler_that_only_saves_is_not_found(self) -> None:
        tree = trees.parse_snippet(
            "def page(refill, region):\n"
            "    def save(value):\n"
            "        region.refill()\n"
            "    panel.field('x', save)\n"
            "    panel.switch(True, lambda e: write(e.value))\n")

        self.assertEqual([], offenders(tree))

    def test_a_region_is_read_by_its_refill_not_by_what_it_draws(self) -> None:
        tree = trees.parse_snippet(
            "def page(context):\n"
            "    def draws():\n"
            "        ui.button('x', on_click=lambda: rebuild())\n"
            "    part = panel.region(draws)\n"
            "    def save(value):\n"
            "        part.refill()\n"
            "    panel.field('x', save)\n")

        self.assertEqual([], offenders(tree))

    def test_a_button_is_not_a_value_control(self) -> None:
        tree = trees.parse_snippet(
            "def page(redraw):\n"
            "    ui.button('x', on_click=redraw)\n")

        self.assertEqual([], offenders(tree))

    def test_on_value_change_is_a_value_control(self) -> None:
        tree = trees.parse_snippet(
            "def page(redraw):\n"
            "    ui.tabs().on_value_change(lambda e: redraw())\n")

        self.assertEqual([(2, "page", "on_value_change")], offenders(tree))

    def test_refilling_a_region_does_not_read_what_the_region_draws(self) -> None:
        tree = trees.parse_snippet(
            "def page():\n"
            "    def build():\n"
            "        redraw()\n"
            "    part = panel.region(build)\n"
            "    panel.field('x', lambda e: part.refill())\n")

        self.assertEqual([], offenders(tree))

    def test_a_picker_a_field_opens_is_not_its_handler(self) -> None:
        tree = trees.parse_snippet(
            "def page(save, redraw):\n"
            "    def pick():\n"
            "        redraw()\n"
            "    panel.field('x', save, browse=pick)\n")

        self.assertEqual([], offenders(tree))

    def test_a_local_function_named_for_a_rebuild_is_read_by_its_body(self) -> None:
        tree = trees.parse_snippet(
            "def page(region):\n"
            "    def redraw():\n"
            "        region.refill()\n"
            "    panel.switch(True, lambda e: redraw())\n")

        self.assertEqual([], offenders(tree))


if __name__ == "__main__":
    unittest.main()
