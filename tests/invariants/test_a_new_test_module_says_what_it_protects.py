"""Protects: (c) a test module added after the clean-up names what it protects, so the suite
stops growing by habit.

A module not listed in `tests/protects_baseline.txt` opens its docstring with
`Protects: (x)`, x being one or more of the letters below. Modules already in the list are
left as they are, and the list only shrinks: a module removed or given the line comes off it.
The same holds for the tests an extension carries in `extensions/<name>/tests`, each with
a `protects_baseline.txt` of its own.

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
CEILING = 930
PROTECTS = re.compile(r"Protects: \([a-e](?:, [a-e])*\)")


def says(tree: ast.Module) -> bool:
    doc = ast.get_docstring(tree, clean=True) or ""
    return PROTECTS.match(doc.splitlines()[0] if doc else "") is not None


def _roots() -> list[tuple[Path, Path]]:
    """Each folder of tests with its baseline: ours, then the one of every extension that
    carries tests, which `extensions/` links in."""
    found = [(ROOT, TESTS)]
    for folder in sorted((ROOT / "extensions").glob("*/tests")):
        if (folder / "__init__.py").is_file():
            found.append((folder.parent, folder))
    return found


def _baseline(tests: Path) -> Path:
    return tests / "protects_baseline.txt"


def _modules(base: Path, tests: Path) -> dict[str, ast.Module]:
    return {path.relative_to(base).as_posix(): trees.tree_for(path)
            for path in sorted(tests.rglob("test_*.py"))
            if "fixtures" not in path.relative_to(tests).parts}


def _listed(tests: Path) -> list[str]:
    path = _baseline(tests)
    if not path.is_file():
        return []
    return [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


class ANewTestModuleSaysWhatItProtects(unittest.TestCase):
    def test_a_module_not_in_the_list_says_which_of_a_to_e_it_protects(self) -> None:
        silent = []
        for base, tests in _roots():
            listed = set(_listed(tests))
            silent += [f"{base.name}/{name}" for name, tree in _modules(base, tests).items()
                       if name not in listed and not says(tree)]

        self.assertEqual([], silent,
                         "Open the module docstring with 'Protects: (x)', x being one or "
                         "more of a-e, which this module's own docstring spells out. "
                         "If none fits, the test is not written.")

    def test_a_module_in_the_list_is_still_there(self) -> None:
        gone = [f"{base.name}/{name}" for base, tests in _roots()
                for name in _listed(tests) if not (base / name).is_file()]

        self.assertEqual([], gone, "Remove the line from the protects_baseline.txt.")

    def test_a_module_that_says_what_it_protects_comes_off_the_list(self) -> None:
        done = []
        for base, tests in _roots():
            modules = _modules(base, tests)
            done += [f"{base.name}/{name}" for name in _listed(tests)
                     if name in modules and says(modules[name])]

        self.assertEqual([], done, "Remove the line from the protects_baseline.txt.")

    def test_the_list_only_shrinks(self) -> None:
        self.assertLessEqual(len(_listed(TESTS)), CEILING,
                             "A module is not added to tests/protects_baseline.txt. "
                             "Lower CEILING when the list gets shorter.")


if __name__ == "__main__":
    unittest.main()
