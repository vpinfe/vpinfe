"""A relative path leaves this codebase forward-slashed, whatever host built it.

`str()` on a `Path` and `os.path.relpath` both answer in the *host's* separator. The
values they build are read somewhere else - an API payload, a zip entry, an `.info` key -
so the same library described itself as `medias/bg.png` on Linux and `medias\\bg.png` on
Windows, and only Windows CI ever said so.

That is the trap this file exists for: **the mistake is invisible on the machine that
makes it.** Four sites had it at once and three others next to them were already correct,
so the rule was known and simply not applied. A test that runs everywhere catches it at
the point it is written rather than an hour later in someone else's CI.

The check is per function: if a function builds a relative path, it has to normalize
somewhere in the same function. That allows the shape `rel = os.path.relpath(...)` used a
line later inside an f-string that ends `.replace(os.sep, "/")`, which is how several
correct call sites are written.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from tests.support import trees

REPO_ROOT = Path(__file__).resolve().parents[2]

# Where a path can reach another machine, and `tests/`, where the same text is compared
# with a forward-slashed literal. Not `frontend/` - its Python serves the browser and its
# own tests cover the URLs.
SOURCES = ("common", "httpapi", "manager", "tests")

# Either of these turns a path into text in the host's separator.
BUILDERS = ("relpath", "relative_to")
# Any of these makes it portable again.
NORMALIZERS = ("as_posix", "sep")


def _builds_a_path(node: ast.AST) -> bool:
    """`os.path.relpath(...)`, or `str()` or an f-string around a `.relative_to(...)`."""
    if isinstance(node, ast.FormattedValue):
        return _is_relative_to(node.value)
    if not isinstance(node, ast.Call):
        return False
    if isinstance(node.func, ast.Attribute) and node.func.attr == "relpath":
        return True
    if isinstance(node.func, ast.Name) and node.func.id == "str" and node.args:
        return _is_relative_to(node.args[0])
    return False


def _is_relative_to(node: ast.AST) -> bool:
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "relative_to")


def _normalizes(fn: ast.AST) -> bool:
    for node in ast.walk(fn):
        if isinstance(node, ast.Attribute) and node.attr in NORMALIZERS:
            return True
    return False


class WirePathTests(unittest.TestCase):
    def test_a_relative_path_is_made_portable_where_it_is_built(self) -> None:
        offenders = []
        for source in SOURCES:
            for path in sorted((REPO_ROOT / source).rglob("*.py")):
                rel = path.relative_to(REPO_ROOT).as_posix()
                if "__pycache__" in rel:
                    continue
                try:
                    tree = trees.tree_for(path)
                except SyntaxError:
                    continue
                for fn in ast.walk(tree):
                    if not isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef):
                        continue
                    if _normalizes(fn):
                        continue
                    for node in ast.walk(fn):
                        if _builds_a_path(node):
                            assert isinstance(node, ast.expr)
                            offenders.append(
                                f"{rel}:{node.lineno} {fn.name}() builds a relative "
                                f"path in the host's separator and never makes it "
                                f"forward-slashed")
                            break

        self.assertEqual(
            offenders, [],
            "a relative path that leaves this process must be forward-slashed - use "
            "`.as_posix()`, or `.replace(os.sep, '/')` on a string:\n  "
            + "\n  ".join(offenders))

    def test_the_check_would_catch_the_bug_it_exists_for(self) -> None:
        """The guard is only worth having if it fails on the real thing.

        This is the shape that shipped: a payload built with `str()` on a relative path
        and handed to a caller as-is.
        """
        broken = trees.parse_snippet(
            "def placements(game_dir, going):\n"
            "    return [str(p.relative_to(game_dir)) for p in going]\n")
        fn = next(n for n in ast.walk(broken) if isinstance(n, ast.FunctionDef))
        self.assertFalse(_normalizes(fn))
        self.assertTrue(any(_builds_a_path(n) for n in ast.walk(fn)))

        lettered = trees.parse_snippet(
            "def named(path, root):\n"
            "    return f'{path.relative_to(root)}:1'\n")
        self.assertTrue(any(_builds_a_path(n) for n in ast.walk(lettered)))

        fixed = trees.parse_snippet(
            "def placements(game_dir, going):\n"
            "    return [p.relative_to(game_dir).as_posix() for p in going]\n")
        fixed_fn = next(n for n in ast.walk(fixed) if isinstance(n, ast.FunctionDef))
        self.assertTrue(_normalizes(fixed_fn))
