"""What Python names, Python spells: `snake_case`, everywhere we define a name.

`docs/conventions.md` settles it - functions, methods, variables and arguments are
`snake_case`, classes are `CapWords`, constants are `UPPER_SNAKE_CASE`. PEP 8 allows
camelCase "only in contexts where that's already the prevailing style", which was the
argument for the 2.x names, and they are gone.

The wire is a separate question and has its own gate. A published theme reads
`tableDirName` off a contract 1 row and always will, and `gameDirName` off a contract 2
row; `game_dir_name` is the attribute behind both. This checks what we *define*, never
what we serve, so the two can differ without either one drifting.

`ruff`'s `N` rules cover most of this and block in CI. They do not cover a PascalCase
dataclass field - `BGImagePath` sat on `Game` for two years without a single finding -
so the rule is stated here in full rather than assumed.
"""

from __future__ import annotations

import ast
import pathlib
import re
import unittest

from tests.support import trees

REPO = pathlib.Path(__file__).resolve().parent.parent.parent
PACKAGES = ("apps", "common", "manager", "extensions", "frontend", "httpapi")
# The two modules at the root are swept too: cli.py forwards metadata_service's
# arguments, so leaving it out would have let a name go back to being half renamed.
MODULES = ("cli.py", "main.py")

SNAKE = re.compile(r"^_{0,2}[a-z][a-z0-9_]*_{0,2}$")
UPPER = re.compile(r"^_{0,2}[A-Z][A-Z0-9_]*$")
CAPWORDS = re.compile(r"^_?[A-Z][A-Za-z0-9]*$")

# Names somebody else chose, each with whose name it is. Adding to this list is a
# decision that the name is not ours, not a way to quiet the check.
BORROWED = {
    # Python's own throwaway.
    "_",
    # http.server dispatches to `do_` plus the request's method, so the name is its own.
    "do_GET",
    "do_POST",
}


def _ok(name: str, what: str) -> bool:
    if name in BORROWED or (name.startswith("__") and name.endswith("__")):
        return True
    if what == "class":
        return bool(CAPWORDS.match(name))
    if what == "module variable":
        # A type alias and a namedtuple factory are CapWords by convention - they name a
        # type. Only at module scope: a CapWords field on a class is the thing this check
        # exists to catch.
        return bool(SNAKE.match(name) or UPPER.match(name) or CAPWORDS.match(name))
    return bool(SNAKE.match(name) or UPPER.match(name))


FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)
SCOPE_NAMES = {"module": "module variable", "class": "class attribute"}


def _bound(node: ast.AST, scope: str, out: list) -> None:
    """One assignment target, at the scope it is bound in."""
    if isinstance(node, ast.Name):
        out.append((node.lineno, SCOPE_NAMES.get(scope, "variable"), node.id))
    elif (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
            and node.value.id == "self"):
        out.append((node.lineno, "attribute", node.attr))
    elif isinstance(node, (ast.Tuple, ast.List)):
        for item in node.elts:
            _bound(item, scope, out)
    elif isinstance(node, ast.Starred):
        _bound(node.value, scope, out)


def _names(node: ast.AST, scope: str, out: list) -> None:
    """Every name this subtree binds, recursing with the scope it recurses into."""
    for child in ast.iter_child_nodes(node):
        inner = scope
        if isinstance(child, FUNCTIONS):
            out.append((child.lineno, "function", child.name))
            args = child.args
            optional = [arg for arg in (args.vararg, args.kwarg) if arg is not None]
            for arg in (*args.posonlyargs, *args.args, *args.kwonlyargs, *optional):
                out.append((arg.lineno, "argument", arg.arg))
            inner = "function"
        elif isinstance(child, ast.ClassDef):
            out.append((child.lineno, "class", child.name))
            inner = "class"
        elif isinstance(child, ast.Assign):
            for target in child.targets:
                _bound(target, scope, out)
        elif isinstance(child, ast.AnnAssign):
            _bound(child.target, scope, out)
        _names(child, inner, out)


def _defined(tree: ast.Module) -> list[tuple[int, str, str]]:
    """The names a module binds that this convention does not allow."""
    found: list = []
    _names(tree, "module", found)
    return [item for item in found if not _ok(item[2], item[1])]


def _sources():
    for package in PACKAGES:
        for path in sorted((REPO / package).rglob("*.py")):
            if "__pycache__" not in path.parts:
                yield path
    for name in MODULES:
        yield REPO / name


def _offenders() -> list[str]:
    out = []
    for path in _sources():
        where = path.relative_to(REPO).as_posix()
        for lineno, what, name in _defined(trees.tree_for(path)):
            out.append(f"{where}:{lineno}: {what} {name}")
    return out


class PythonIsSnakeCaseTests(unittest.TestCase):
    def test_nothing_in_these_packages_defines_a_camel_case_name(self) -> None:
        """A name somebody else really chose is a `BORROWED` entry saying whose it is."""
        self.assertEqual(_offenders(), [])

    def test_every_borrowed_name_is_still_used(self) -> None:
        """An entry nothing uses is an exemption sitting there waiting to cover
        something else."""
        seen: set[str] = set()
        for path in _sources():
            for node in ast.walk(trees.tree_for(path)):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    seen.add(node.name)
                elif isinstance(node, ast.Name):
                    seen.add(node.id)
                elif isinstance(node, ast.arg):
                    seen.add(node.arg)
        self.assertEqual(sorted(BORROWED - seen), [])

    def test_the_checker_can_actually_fail(self) -> None:
        """Every rule it enforces, and the one exemption that is about scope."""
        cases = {
            "def loadGames(self): pass": "function loadGames",
            "def f(iniConfig): pass": "argument iniConfig",
            "class game_parser: pass": "class game_parser",
            "def f():\n    vpxData = 1": "variable vpxData",
            "class C:\n    gameDirName = None": "class attribute gameDirName",
            "class C:\n    BGImagePath: str = ''": "class attribute BGImagePath",
            "class C:\n    def f(self):\n        self.jsGameDict = 1": "attribute jsGameDict",
        }
        for source, expected in cases.items():
            with self.subTest(source=source):
                found = _defined(trees.parse_snippet(source))
                self.assertEqual([f"{what} {name}" for _line, what, name in found],
                                 [expected])

        # And the things that are correct stay quiet.
        for source in ("def load_games(self): pass",
                       "class GameParser: pass",
                       "VPX_PATHS = {}",
                       "NavItem = tuple[str, str]",
                       "def f():\n    _ = 1"):
            with self.subTest(source=source):
                self.assertEqual(_defined(trees.parse_snippet(source)), [])

        # A type alias is CapWords at module scope only. The same name on a class is
        # the dataclass field this check was written for.
        self.assertEqual(_defined(trees.parse_snippet("Progress = int")), [])
        self.assertEqual(
            [what for _line, what, _name
             in _defined(trees.parse_snippet("class C:\n    Progress = 1"))],
            ["class attribute"])


if __name__ == "__main__":
    unittest.main()
