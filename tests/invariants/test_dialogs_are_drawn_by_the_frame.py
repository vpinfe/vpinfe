"""Protects: (e) behaves the same on every screen. Every Manager dialog is made by
`manager/kit/dialog.py` and sets no side margin, width or card style of its own, so they
all share one design."""

from __future__ import annotations

import ast
import pathlib
import re
import unittest

from tests.support import trees

MANAGER = pathlib.Path(__file__).resolve().parents[2] / "manager"
FRAME = "dialog.py"
OPENS_ONE = "ui.dialog("


def _at(name: str) -> pathlib.Path:
    """The one file of that name, wherever its area keeps it."""
    (found,) = MANAGER.rglob(name)
    return found


# Not questions, so they draw their own card: nothing in them is answered or committed.
NOT_ASKING = {
    "mediaview.py": "an enlarged picture or text, with nothing to answer",
    "remote.py": "the phone remote's own sheets, drawn for a touch screen",
}


# `persistent` blocks Escape as well as a click outside, so it is for a run that cannot be
# left half-way or an answer that cannot be left unsaid. Anything typed into takes
# `ignore_outside_click` and Escape still cancels. Each file below holds the number of
# places it says `persistent`, so a further one is a decision made here: a run puts it on
# and takes it off again, and a request still waiting is drawn with it.
PERSISTENT = {
    "import_dialog.py": 1,
    "mediasource.py": 2,
    "sign_in_notice.py": 1,
    "wizard.py": 2,
}


def _persistent(path: pathlib.Path) -> list[ast.AST]:
    """Where `path` asks for a `persistent` dialog, as a keyword or among the props."""
    return [one for one in ast.walk(trees.tree_for(path))
            if (isinstance(one, ast.keyword) and one.arg == "persistent")
            or (isinstance(one, ast.Constant) and isinstance(one.value, str)
                and "persistent" in one.value.split())]


def _called(path: pathlib.Path) -> set[str]:
    """What `path` calls on `manager.kit.dialog`, by name."""
    tree = trees.tree_for(path)
    names = {bound for one in ast.walk(tree) if isinstance(one, ast.ImportFrom)
             for bound, module, name in trees.manager_imports(one)
             if module == "dialog" and name is None}
    return {one.func.attr for one in ast.walk(tree)
            if isinstance(one, ast.Call) and isinstance(one.func, ast.Attribute)
            and isinstance(one.func.value, ast.Name) and one.func.value.id in names}


TYPED_INTO = {"field", "input", "textarea"}


def _can_be_clicked_away(call: ast.expr) -> bool:
    """Whether `call` is `dialog.opened(...)` with neither way to keep a click outside out."""
    return (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
            and call.func.attr == "opened"
            and not any(key.arg in {"ignore_outside_click", "persistent"}
                        and not (isinstance(key.value, ast.Constant) and key.value.value is False)
                        for key in call.keywords))


def _calls(nodes: list[ast.stmt] | ast.AST, names: set[str]) -> bool:
    """Whether anything under `nodes` calls an attribute named in `names`."""
    roots = nodes if isinstance(nodes, list) else [nodes]
    return any(isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
               and call.func.attr in names
               for root in roots for call in ast.walk(root))


def _typed_into(body: list[ast.stmt]) -> bool:
    """Whether `body` draws a field that can be typed into, a read-only box being shown to copy."""
    return any(_calls([statement], TYPED_INTO)
               and not any(isinstance(one, ast.Constant) and isinstance(one.value, str)
                           and "readonly" in one.value.split() for one in ast.walk(statement))
               for root in body for statement in ast.walk(root)
               if isinstance(statement, (ast.Expr, ast.Assign)))


def _typed_dialogs_that_close_on_a_click(path: pathlib.Path) -> list[int]:
    """Lines where `path` opens a dialog that is typed into and has no way to ignore the click
    outside. Typed into means the dialog draws a field, directly or in a helper of the same
    file it names, or the function that opens it puts the cursor in one with `frame.focus`."""
    tree = trees.tree_for(path)
    helpers: dict[str, list[ast.FunctionDef | ast.AsyncFunctionDef]] = {}
    for function in ast.walk(tree):
        if isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            helpers.setdefault(function.name, []).append(function)
    lines = set()
    for function in ast.walk(tree):
        if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        focuses = _calls(function, {"focus"})
        for one in ast.walk(function):
            if not isinstance(one, ast.With):
                continue
            named = {name.id for statement in one.body for name in ast.walk(statement)
                     if isinstance(name, ast.Name)}
            drawn = [one.body] + [helper.body for name in named for helper in helpers.get(name, [])]
            if (any(_can_be_clicked_away(item.context_expr) for item in one.items)
                    and (focuses or any(_typed_into(body) for body in drawn))):
                lines.add(one.lineno)
    return sorted(lines)


OPTIONS = {"size", "tall", "persistent", "ignore_outside_click", "classes"}
SIZES = {"question", "form", "wide", "large", "full"}

# A class a dialog may carry beside the frame's own is a hook a rule reaches it by, never a
# look. A need the parts do not meet becomes a new part in `dialog.py`.
HOOKS = {"manager-wizard-card", "manager-dialog--series", "manager-sources-card",
         "manager-import-card"}

# A side margin on a dialog's content, which is the frame's to set.
SIDE_MARGIN = re.compile(r"(?<![\w-])(?:px|mx)-\d")
WIDTH_STYLE = re.compile(r"(?<![\w-])(?:min-|max-)?width\s*:")


def _words(node: ast.expr | None) -> set[str] | None:
    """The strings `node` can be: a constant, or the two of a conditional. None where it is
    computed, which a reader cannot check."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return set(node.value.split())
    if isinstance(node, ast.IfExp):
        one, other = _words(node.body), _words(node.orelse)
        return None if one is None or other is None else one | other
    return None


def _opens(item: ast.withitem) -> ast.Call | None:
    call = item.context_expr
    if (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
            and call.func.attr == "opened"):
        return call
    return None


def _opened(path: pathlib.Path) -> list[tuple[ast.Call, ast.With]]:
    """Each `with <x>.opened(...)` in `path`, as its call and the block it opens."""
    return [(call, block) for block in ast.walk(trees.tree_for(path))
            if isinstance(block, ast.With)
            for item in block.items if (call := _opens(item)) is not None]


def _strings(nodes: list[ast.stmt]) -> list[tuple[int, str]]:
    return [(one.lineno, one.value) for root in nodes for one in ast.walk(root)
            if isinstance(one, ast.Constant) and isinstance(one.value, str)]


class DialogsAreDrawnByTheFrame(unittest.TestCase):
    def test_a_dialog_asks_the_frame_for_a_size_and_nothing_else(self) -> None:
        wrong: list[str] = []
        for path in sorted(MANAGER.rglob("*.py")):
            for call, _block in _opened(path):
                where = f"{path.name}:{call.lineno}"
                for key in call.keywords:
                    if key.arg not in OPTIONS:
                        wrong.append(f"{where} says {key.arg}=")
                    elif key.arg == "size" and not (_words(key.value) or {"?"}) <= SIZES:
                        wrong.append(f"{where} asks for a size the frame does not have")
                    elif key.arg == "classes" and not (_words(key.value) or {"?"}) <= HOOKS:
                        wrong.append(f"{where} gives the card a class that is not a hook")
        self.assertEqual([], wrong)

    def test_a_dialog_sets_no_side_margin_or_width_of_its_own(self) -> None:
        wrong = [f"{path.name}:{line} {text!r}" for path in sorted(MANAGER.rglob("*.py"))
                 for _call, block in _opened(path)
                 for line, text in _strings(block.body)
                 if SIDE_MARGIN.search(text) or WIDTH_STYLE.search(text)]
        self.assertEqual([], wrong)

    def test_no_dialog_is_drawn_by_hand(self) -> None:
        by_hand = sorted(path.name for path in MANAGER.rglob("*.py")
                         if path.name != FRAME
                         and OPENS_ONE in path.read_text(encoding="utf-8"))
        self.assertEqual([], by_hand)

    def test_only_an_exception_draws_its_own_card(self) -> None:
        own = sorted(path.name for path in MANAGER.rglob("*.py") if "made" in _called(path))
        self.assertEqual(sorted(NOT_ASKING), own)

    def test_an_exception_asks_nothing(self) -> None:
        for name in NOT_ASKING:
            with self.subTest(name=name):
                self.assertNotIn("opened", _called(_at(name)))

    def test_only_a_run_or_a_required_answer_blocks_escape(self) -> None:
        blocking = {path.name: len(_persistent(path)) for path in sorted(MANAGER.rglob("*.py"))
                    if path.name != FRAME and _persistent(path)}
        self.assertEqual(PERSISTENT, blocking)

    def test_nothing_typed_is_lost_to_a_click_outside(self) -> None:
        loses = {path.name: lines for path in sorted(MANAGER.rglob("*.py"))
                 if path.name != FRAME and (lines := _typed_dialogs_that_close_on_a_click(path))}
        self.assertEqual({}, loses)

    def test_no_dialog_is_persistent_whatever_happens(self) -> None:
        always = sorted(path.name for path in MANAGER.rglob("*.py")
                        for one in _persistent(path)
                        if isinstance(one, ast.keyword) and isinstance(one.value, ast.Constant)
                        and one.value.value is True)
        self.assertEqual([], always)


if __name__ == "__main__":
    unittest.main()
