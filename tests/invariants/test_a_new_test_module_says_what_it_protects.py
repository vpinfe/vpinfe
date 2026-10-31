"""Protects: (c) a test module added after the clean-up names what it protects, so the suite
stops growing by habit.

A module not listed in `tests/protects_baseline.txt` opens its docstring with
`Protects: (x)`, x being one or more of the letters below. Modules already in the list are
left as they are, and the list only shrinks: a module removed or given the line comes off it.

    (a) a person's data or files would be lost or damaged
    (b) a promise to a theme, an extension or an API consumer would break
    (c) a bug that already happened would come back
    (d) first run, launch or play would break
    (e) it would behave differently on another system

A test for anything else is not written: how a page looks and what it says are checked by
review of the page and by the wording lint.
"""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

from tests.support import trees

ROOT = Path(__file__).resolve().parents[2]
TESTS = ROOT / "tests"
BASELINE = TESTS / "protects_baseline.txt"
CEILING = 970
PROTECTS = re.compile(r"Protects: \([a-e](?:, [a-e])*\)")


def says(tree: ast.Module) -> bool:
    doc = ast.get_docstring(tree, clean=True) or ""
    return PROTECTS.match(doc.splitlines()[0] if doc else "") is not None


def _modules() -> dict[str, ast.Module]:
    return {path.relative_to(ROOT).as_posix(): trees.tree_for(path)
            for path in sorted(TESTS.rglob("test_*.py"))}


def _listed() -> list[str]:
    return [line for line in BASELINE.read_text().splitlines() if line.strip()]


class ANewTestModuleSaysWhatItProtects(unittest.TestCase):
    def test_a_module_not_in_the_list_says_which_of_a_to_e_it_protects(self) -> None:
        listed = set(_listed())
        silent = [name for name, tree in _modules().items()
                  if name not in listed and not says(tree)]

        self.assertEqual([], silent,
                         "Open the module docstring with 'Protects: (x)', x being one or "
                         "more of a-e, which this module's own docstring spells out. "
                         "If none fits, the test is not written.")

    def test_a_module_in_the_list_is_still_there(self) -> None:
        gone = [name for name in _listed() if not (ROOT / name).is_file()]

        self.assertEqual([], gone, "Remove the line from tests/protects_baseline.txt.")

    def test_a_module_that_says_what_it_protects_comes_off_the_list(self) -> None:
        modules = _modules()
        done = [name for name in _listed() if name in modules and says(modules[name])]

        self.assertEqual([], done, "Remove the line from tests/protects_baseline.txt.")

    def test_the_list_only_shrinks(self) -> None:
        self.assertLessEqual(len(_listed()), CEILING,
                             "A module is not added to tests/protects_baseline.txt. "
                             "Lower CEILING when the list gets shorter.")


if __name__ == "__main__":
    unittest.main()
