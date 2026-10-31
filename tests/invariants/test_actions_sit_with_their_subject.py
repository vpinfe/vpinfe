"""An action sits with what it acts on, never under a heading that says "Actions".

The placement rule is `docs/conventions.md`, "An action sits with what it acts on". What
this reads is its residue: a section or heading named for the mechanism, a subject whose
panel menu and grid menu are drawn from different lists, and buttons built by hand
instead of through `panel.py`, which is where a verb's treatment is decided.
"""

from __future__ import annotations

import ast
import json
import pathlib
import unittest

from tests.support import trees

REPO = pathlib.Path(__file__).resolve().parent.parent.parent
MANAGER = REPO / "manager"
CATALOG = REPO / "common" / "i18n" / "catalogs" / "en.json"


def _at(name: str) -> pathlib.Path:
    """The one file of that name, wherever its area keeps it."""
    (found,) = MANAGER.rglob(name)
    return found


# `panel.py` is the ⋮ that opens a subject's verbs. `ext_page.py` heads the buttons an
# extension declares, which have no subject menu to sit in.
ACTIONS_WORD_AT = {"panel.py", "ext_page.py"}

# Every subject with a panel ⋮: the module and function that write its list of verbs, and
# the module whose grid row menu draws it.
GRID_OF = {
    "tag": ("tageditor.py", "acts", "tageditor.py"),
    "collection": ("collections.py", "acts", "collections.py"),
    "launcher": ("launchers.py", "acts", "launchers.py"),
    "theme": ("themes.py", "acts", "themes.py"),
    "device": ("devices.py", "acts", "devices.py"),
    "player": ("players.py", "acts", "players.py"),
    "backup": ("backups.py", "acts", "backups.py"),
    "discovery": ("discovery.py", "acts", "discovery.py"),
    "game": ("game_acts.py", "game_acts", "games.py"),
    "table": ("game_acts.py", "table_acts", "games.py"),
    "selection": ("game_acts.py", "table_acts", "games.py"),
    "game_selection": ("game_acts.py", "game_acts", "games.py"),
    "media_file": ("file_acts.py", "media_acts", "media.py"),
    "asset_file": ("file_acts.py", "asset_acts", "assets.py"),
}

# Direct `ui.button(` calls outside `panel.py`. A count that only comes down: lower it
# when one moves to a constructor, never raise it.
BUTTON_CEILING = 52


# Grids that have not yet reached the selection rules in `docs/conventions.md`. A page
# leaves a list when it conforms, and the test fails while one still listed already does.
SELECTION_BY_HAND: set[str] = set()
ROW_MENU_WITHOUT_HEADER: set[str] = set()
HEADER_MENU_WITHOUT_ROW: set[str] = set()
# A chooser in a dialog reads its ticks in the footer button, which is its selection menu;
# Community's rows come from an extension and have no subject to act on.
NO_SELECTION_MENU = {"backup_rows.py", "signed_in_rows.py"}
HEADER_ONLY = {"ext_lists.py"}
# `grid.build`'s positional order after `columns, rows, scope`.
BUILD_PARAMS = ("on_select_rows", "on_context", "on_header_context")


def _tree(path: pathlib.Path) -> ast.Module:
    return trees.tree_for(path)


def _is_ui_call(node: ast.AST, attr: str) -> bool:
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == attr and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "ui")


# What a section's builder draws, read from `ui.` and `panel.` calls. A button is a verb; a
# layout call holds either; anything else is something to read or set.
_BUTTONS = {"action", "icon_action", "remote_action", "remote_key", "browse_button", "link",
            "link_out", "out", "menu_button", "verb_menu", "subject_menu", "button"}
_LAYOUT = {"row", "column", "element", "space", "separator", "tooltip", "menu", "item",
           "card", "timer", "notify", "navigate", "run_javascript", "add_head_html",
           "add_css", "expansion", "tabs", "tab", "tab_panels", "tab_panel", "dialog"}


def _imports(tree: ast.Module) -> dict[str, tuple[str, str | None]]:
    """What each imported manager name is: (module, name) for a function, (module, None)
    for a module."""
    found: dict[str, tuple[str, str | None]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for bound, module, name in trees.manager_imports(node):
                found[bound] = (module, name)
    return found


def draws(trees_by_module: dict[str, ast.Module], module: str, name: str) -> set[str]:
    """`button` and `content` for what `module.name` draws, through the manager functions
    it calls. A call that resolves to nothing here is a library call, and is not read."""
    defs = {mod: {node.name: node for node in tree.body
                  if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
            for mod, tree in trees_by_module.items()}
    imports = {mod: _imports(tree) for mod, tree in trees_by_module.items()}
    seen: set[tuple[str, str]] = set()
    found: set[str] = set()
    stack = [(module, name)]
    while stack:
        mod, fn = stack.pop()
        node = defs.get(mod, {}).get(fn)
        if node is None or (mod, fn) in seen:
            continue
        seen.add((mod, fn))
        for call in ast.walk(node):
            if not isinstance(call, ast.Call):
                continue
            func = call.func
            if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                owner, attr = func.value.id, func.attr
                if owner in ("ui", "panel"):
                    found.add("button" if attr in _BUTTONS else
                              "layout" if attr in _LAYOUT and owner == "ui" else "content")
                elif owner in imports[mod] and imports[mod][owner][1] is None:
                    stack.append((imports[mod][owner][0], attr))
            elif isinstance(func, ast.Name):
                if func.id in defs.get(mod, {}):
                    stack.append((mod, func.id))
                elif func.id in imports[mod] and imports[mod][func.id][1] is not None:
                    stack.append((imports[mod][func.id][0], imports[mod][func.id][1] or ""))
    return found - {"layout"}


def _workbench_modules() -> list[str]:
    return sorted(path.stem for path in (MANAGER / "workbench").glob("*.py")
                  if path.stem != "__init__")


def _builders(trees_by_module: dict[str, ast.Module]) -> dict[str, list[tuple[str, str]]]:
    """Each rail section's key, with the functions that build it. The workbench's `SECTIONS`
    names them; an account section is built by `players._account`, outside that list."""
    built: dict[str, list[tuple[str, str]]] = {}
    for module in _workbench_modules():
        tree = trees_by_module[module]
        imports = _imports(tree)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and getattr(node.func, "id", "") == "Section"
                    and len(node.args) > 2):
                continue
            refs: list[tuple[str, str]] = []
            for ref in ast.walk(node.args[2]):
                if isinstance(ref, ast.Name):
                    found = imports.get(ref.id)
                    refs.append((found[0], found[1] or ref.id) if found and found[1]
                                else (module, ref.id))
                elif (isinstance(ref, ast.Attribute) and isinstance(ref.value, ast.Name)
                      and ref.value.id in imports and imports[ref.value.id][1] is None):
                    refs.append((imports[ref.value.id][0], ref.attr))
            built[ast.unparse(node.args[0])] = refs
    built["player_account"] = [("players", "_account")]
    return built


def _manager_trees() -> dict[str, ast.Module]:
    return {path.stem: _tree(path) for path in MANAGER.rglob("*.py")}


def _subject_verbs() -> set[str]:
    for node in _tree(MANAGER / "workbench" / "subject_verbs.py").body:
        target = getattr(node, "target", None)
        value = getattr(node, "value", None)
        if isinstance(target, ast.Name) and target.id == "SUBJECT_VERBS" \
                and isinstance(value, ast.Dict):
            return {key.value for key in value.keys
                    if isinstance(key, ast.Constant) and isinstance(key.value, str)}
    return set()


def _is_grid_build(node: ast.AST) -> bool:
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "build" and isinstance(node.func.value, ast.Name)
            and node.func.value.id in ("grid", "grid_module"))


def _given(call: ast.Call) -> set[str]:
    """Which of the optional handlers a `grid.build` call passes, by keyword or position."""
    named = {keyword.arg for keyword in call.keywords if keyword.arg}
    positional = set(BUILD_PARAMS[:max(0, len(call.args) - 3)])
    return {name for name in BUILD_PARAMS if name in named | positional}


def _grids() -> dict[str, set[str]]:
    """Each manager module that builds a grid, with the handlers its grids are given."""
    found: dict[str, set[str]] = {}
    for path in sorted(MANAGER.rglob("*.py")):
        calls = [node for node in ast.walk(_tree(path))
                 if isinstance(node, ast.Call) and _is_grid_build(node)]
        if calls:
            found[path.name] = set().union(*(_given(call) for call in calls))
    return found


def _calls_panel(path: pathlib.Path, attr: str) -> bool:
    return any(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
               and node.func.attr == attr and isinstance(node.func.value, ast.Name)
               and node.func.value.id == "panel"
               for node in ast.walk(_tree(path)))


class TestActionsSitWithTheirSubject(unittest.TestCase):
    def test_no_heading_or_section_is_called_actions(self) -> None:
        catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
        spelled = sorted(key for key, value in catalog.items() if value == "Actions")
        self.assertEqual(spelled, ["word.actions"],
                         "name the section for what it holds, or put the verbs in a strip")
        offenders = sorted(path.name for path in MANAGER.rglob("*.py")
                           if '"word.actions"' in path.read_text(encoding="utf-8")
                           and path.name not in ACTIONS_WORD_AT)
        self.assertEqual(offenders, [])

    def test_no_section_key_is_an_actions_bucket(self) -> None:
        keys = [node.args[0].value
                for module in _workbench_modules()
                for node in ast.walk(_tree(MANAGER / "workbench" / f"{module}.py"))
                if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "Section"
                and node.args and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)]
        self.assertTrue(keys)
        self.assertEqual([key for key in keys if key.endswith("_actions")], [])

    def test_a_subject_menu_and_its_row_menu_share_one_list(self) -> None:
        self.assertEqual(_subject_verbs(), set(GRID_OF))
        for subject, (written, function, drawn) in GRID_OF.items():
            defined = {node.name for node in _tree(_at(written)).body
                       if isinstance(node, ast.FunctionDef)}
            tree = _tree(_at(drawn))
            called = {node.func.attr for node in ast.walk(tree)
                      if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                      and isinstance(node.func.value, ast.Name)
                      and node.func.value.id == "panel"}
            with self.subTest(subject=subject):
                self.assertIn(function, defined)
                self.assertIn("verb_menu", called)

    def test_no_section_is_only_buttons(self) -> None:
        read = _manager_trees()
        sections = _builders(read)
        self.assertGreater(len(sections), 25)
        drawn = {key: set().union(*(draws(read, *ref) for ref in refs))
                 for key, refs in sections.items()}
        self.assertEqual([key for key, found in drawn.items() if not found], [],
                         "a section's builder was not followed to anything it draws")
        only_buttons = sorted(key for key, found in drawn.items() if found == {"button"})
        self.assertEqual(only_buttons, [],
                         "a verb sits in the menu of what it acts on; a section holds "
                         "something to read or set")

    def test_a_builder_of_only_buttons_is_found_and_a_mixed_one_is_not(self) -> None:
        source = trees.parse_snippet(
            "def bare(): panel.action('Go', go)()\n"
            "def mixed(): panel.action('Go', go)(); panel.facts(ui, [])\n"
            "def through(): bare()\n")
        found = {"snippet": source}
        self.assertEqual(draws(found, "snippet", "bare"), {"button"})
        self.assertEqual(draws(found, "snippet", "mixed"), {"button", "content"})
        self.assertEqual(draws(found, "snippet", "through"), {"button"})

    def test_hand_built_buttons_only_come_down(self) -> None:
        count = sum(1 for path in MANAGER.rglob("*.py") if path.name != "panel.py"
                    for node in ast.walk(_tree(path)) if _is_ui_call(node, "button"))
        self.assertLessEqual(count, BUTTON_CEILING,
                             "build it with panel.action, panel.verb_menu or dialog.footer")
        if count < BUTTON_CEILING:
            self.fail(f"{count} now: lower BUTTON_CEILING to match")

    def test_a_selection_menu_is_drawn_from_the_subjects_list(self) -> None:
        """Every selection `more_vert` is built by `panel.selection_menu`, from the list
        the row menu draws."""
        grids = _grids()
        self.assertIn("backups.py", grids)
        selectable = {name for name, given in grids.items() if "on_select_rows" in given}
        drawn = {name for name in selectable if _calls_panel(_at(name), "selection_menu")}
        by_hand = selectable - drawn - NO_SELECTION_MENU
        self.assertEqual(sorted(SELECTION_BY_HAND), sorted(by_hand),
                         "a selection menu is built with panel.selection_menu; a page "
                         "that does is out of SELECTION_BY_HAND")
        for path in sorted(MANAGER.rglob("*.py")):
            if path.name != "panel.py" and _calls_panel(path, "selection_actions"):
                self.assertIn(path.name, SELECTION_BY_HAND, "build it with selection_menu")

    def test_checkboxes_are_drawn_only_for_a_grid_with_a_selection_handler(self) -> None:
        build = next(node for node in _tree(MANAGER / "kit" / "grid.py").body
                     if isinstance(node, ast.FunctionDef) and node.name == "build")
        gated = [node for node in ast.walk(build) if isinstance(node, ast.IfExp)
                 and ast.unparse(node.test) == "on_select_rows is not None"
                 and any(isinstance(inner, ast.Constant) and inner.value == "rowSelection"
                         for inner in ast.walk(node.body))]
        self.assertTrue(gated, "rowSelection must be drawn only when on_select_rows is given")

    def test_a_header_menu_is_where_a_row_menu_is(self) -> None:
        grids = _grids()
        row_only = {name for name, given in grids.items()
                    if "on_context" in given and "on_header_context" not in given}
        header_only = {name for name, given in grids.items()
                       if "on_header_context" in given and "on_context" not in given}
        self.assertEqual(sorted(ROW_MENU_WITHOUT_HEADER), sorted(row_only),
                         "give the grid the Pin and Hide menu every other grid has")
        self.assertEqual(sorted(HEADER_MENU_WITHOUT_ROW), sorted(header_only - HEADER_ONLY),
                         "give the grid a row menu from its subject's list")

    def test_a_grid_is_read_by_position_and_by_name(self) -> None:
        statement = trees.parse_snippet("grid.build(a, b, c, pick, on_header_context=h)").body[0]
        assert isinstance(statement, ast.Expr)
        call = statement.value
        assert isinstance(call, ast.Call)
        self.assertEqual({"on_select_rows", "on_header_context"}, _given(call))
        bare_statement = trees.parse_snippet("grid.build(a, b, c)").body[0]
        assert isinstance(bare_statement, ast.Expr)
        bare = bare_statement.value
        assert isinstance(bare, ast.Call)
        self.assertEqual(set(), _given(bare))


if __name__ == "__main__":
    unittest.main()
