"""A test that drives a browser waits on what the app says, never on the clock.

In a test file that imports `tests.support.browser_session` or
`tests.support.live_instance`, a `sleep` of a literal 0.25s or more fails, and so does a
`setTimeout` in the page's script with a literal delay of 250ms or more. Wait on the
change the action causes instead: `manager_walk`'s `act`, `BrowserSession.wait_for`, the
app's own signal. A check that nothing happened waits out a named window, whose name says
what it is sized from.
"""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

from tests.support import trees

ROOT = Path(__file__).resolve().parents[2]
TESTS = ROOT / "tests"
DRIVERS = {"tests.support.browser_session", "tests.support.live_instance"}
LONGEST_S = 0.25

_SET_TIMEOUT = re.compile(r"\bsetTimeout\s*\(")
_NUMBER = re.compile(r"\d+(?:\.\d+)?|\.\d+")
_OPENS, _CLOSES = "([{", ")]}"


def drives_a_browser(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(alias.name in DRIVERS for alias in node.names):
                return True
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.module in DRIVERS or any(
                    f"{node.module}.{alias.name}" in DRIVERS for alias in node.names):
                return True
    return False


def _literal_s(node: ast.expr | None) -> float | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, int | float) \
            and not isinstance(node.value, bool):
        return float(node.value)
    return None


def long_sleeps(tree: ast.AST) -> list[tuple[int, float]]:
    """(line, seconds) of each `sleep(<literal>)` of `LONGEST_S` or more."""
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = (node.func.attr if isinstance(node.func, ast.Attribute)
                else node.func.id if isinstance(node.func, ast.Name) else "")
        if name != "sleep":
            continue
        given = node.args[0] if node.args else next(
            (one.value for one in node.keywords if one.arg in ("delay", "secs")), None)
        seconds = _literal_s(given)
        if seconds is not None and seconds >= LONGEST_S:
            found.append((node.lineno, seconds))
    return sorted(found)


def _delay(script: str, start: int) -> str | None:
    """The last argument of the call whose `(` is at `start`, or None when the call does
    not close inside `script`."""
    depth, quote, escaped, args, piece = 0, "", False, [], []
    for char in script[start + 1:]:
        if quote:
            piece.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
            continue
        if char in "'\"`":
            quote = char
        elif char in _OPENS:
            depth += 1
        elif char in _CLOSES:
            if depth == 0:
                args.append("".join(piece))
                return args[-1].strip()
            depth -= 1
        elif char == "," and depth == 0:
            args.append("".join(piece))
            piece = []
            continue
        piece.append(char)
    return None


def long_timeouts(script: str) -> list[float]:
    """Each literal `setTimeout` delay in `script` of `LONGEST_S` or more, in ms."""
    found = []
    for call in _SET_TIMEOUT.finditer(script):
        delay = _delay(script, call.end() - 1)
        if delay is not None and _NUMBER.fullmatch(delay) \
                and float(delay) >= LONGEST_S * 1000:
            found.append(float(delay))
    return found


def page_scripts(tree: ast.AST) -> list[tuple[int, str]]:
    """(line, text) of every string in the module, an f-string's literal parts joined
    around a placeholder."""
    found, inside = [], set()
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            parts = []
            for part in node.values:
                inside.add(id(part))
                parts.append(part.value if isinstance(part, ast.Constant)
                             and isinstance(part.value, str) else "{}")
            found.append((node.lineno, "".join(parts)))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                and id(node) not in inside:
            found.append((node.lineno, node.value))
    return found


def browser_tests() -> dict[str, ast.Module]:
    return {path.relative_to(ROOT).as_posix(): tree
            for path in sorted(TESTS.rglob("test_*.py"))
            if drives_a_browser(tree := trees.tree_for(path))}


class ABrowserTestWaitsOnTheApp(unittest.TestCase):
    def test_no_browser_test_sleeps_a_literal_quarter_second_or_more(self) -> None:
        found = [f"{name}:{line} sleeps {seconds:g}s"
                 for name, tree in browser_tests().items()
                 for line, seconds in long_sleeps(tree)]

        self.assertEqual([], found,
                         "Wait on the change the action causes - manager_walk's act, "
                         "BrowserSession.wait_for, the app's own signal. A check that "
                         "nothing happened waits a named window sized from what could "
                         "still cause it.")

    def test_no_page_script_pauses_a_literal_quarter_second_or_more(self) -> None:
        found = [f"{name}:{line} setTimeout {delay:g}ms"
                 for name, tree in browser_tests().items()
                 for line, script in page_scripts(tree)
                 for delay in long_timeouts(script)]

        self.assertEqual([], found,
                         "Poll from the test with BrowserSession.wait_for, which has a "
                         "limit, rather than pausing inside the page.")

    def test_the_drives_are_the_files_read(self) -> None:
        read = browser_tests()

        for name in ("tests/theming/test_render_smoke.py",
                     "tests/theming/test_manager_pages_drive.py",
                     "tests/games/test_revert_3x.py"):
            self.assertIn(name, read)
        self.assertNotIn("tests/invariants/test_a_browser_test_waits_on_the_app.py", read)

    def test_each_import_form_counts(self) -> None:
        for source in ("from tests.support.browser_session import BrowserSession\n",
                       "from tests.support import live_instance\n",
                       "import tests.support.browser_session\n",
                       "def boot():\n    from tests.support.live_instance import LiveInstance\n"):
            with self.subTest(source=source):
                self.assertTrue(drives_a_browser(trees.parse_snippet(source)))
        self.assertFalse(drives_a_browser(trees.parse_snippet(
            "from tests.support.library import write_game\n"
            "text = 'from tests.support.live_instance import LiveInstance'\n")))

    def test_only_a_literal_sleep_of_a_quarter_second_or_more_counts(self) -> None:
        tree = trees.parse_snippet(
            "time.sleep(2.0)\n"
            "await asyncio.sleep(0.25)\n"
            "sleep(1)\n"
            "await asyncio.sleep(delay=3)\n"
            "await asyncio.sleep(0.2)\n"
            "time.sleep(WINDOW_S)\n"
            "await asyncio.sleep(window * 2)\n")

        self.assertEqual([(1, 2.0), (2, 0.25), (3, 1.0), (4, 3.0)], long_sleeps(tree))

    def test_only_a_literal_page_pause_of_a_quarter_second_or_more_counts(self) -> None:
        self.assertEqual([300.0, 1200.0], long_timeouts(
            "await new Promise(r => setTimeout(r, 300));"
            " setTimeout(() => { throw new Error('a, b'); }, 1200)"))
        self.assertEqual([], long_timeouts(
            "setTimeout(() => { throw new Error('boom'); }, 0);"
            " await new Promise(done => setTimeout(done, 100));"
            " setTimeout(fn, delay); setTimeout(fn)"))

    def test_an_f_string_pause_is_read_around_its_placeholders(self) -> None:
        tree = trees.parse_snippet(
            "script = f\"for (x of {items}) {{ await new Promise(r => setTimeout(r, 500)); }}\"\n"
            "other = f\"setTimeout(r, {delay})\"\n")

        self.assertEqual([500.0], [delay for _line, script in page_scripts(tree)
                                   for delay in long_timeouts(script)])


if __name__ == "__main__":
    unittest.main()
