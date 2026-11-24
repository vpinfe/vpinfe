"""Reading the library once, checked two ways.

Constructing a `GameParser` loads every game, so a reload on the next line reads the
whole library a second time. It is invisible at runtime and cheap to reintroduce, which
is why one test watches the parser and the other watches the source for the pattern.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from common.games.game_parser import GameParser
from tests.support import library_loader, trees
from tests.support.project import project_files

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _tableparser_target(node) -> str | None:
    """The variable name, if this statement is `x = GameParser(...)`."""
    if not isinstance(node, ast.Assign) or len(node.targets) != 1:
        return None
    if not isinstance(node.value, ast.Call):
        return None
    func = node.value.func
    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
    if name != "GameParser":
        return None
    target = node.targets[0]
    return target.id if isinstance(target, ast.Name) else None


def _is_reload_of(node, name: str) -> bool:
    """Whether this statement is `name.load_games(...)`."""
    if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Call):
        return False
    func = node.value.func
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "load_games"
        and isinstance(func.value, ast.Name)
        and func.value.id == name
    )


class LibraryIsReadOnceTests(unittest.TestCase):
    def test_constructing_a_gameparser_reads_each_game_once(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("A Table (Bally 1990)", "B Table (Bally 1991)"):
                folder = root / name
                folder.mkdir()
                (folder / f"{name}.vpx").write_text("")

            real_build = GameParser._build_game
            calls = []

            def counting_build(self, game_dir, **kwargs):
                calls.append(game_dir)
                return real_build(self, game_dir, **kwargs)

            with mock.patch.object(GameParser, "_build_game", counting_build):
                parser = GameParser(root)
                games = parser.get_all_games()

            self.assertEqual(len(games), 2)
            self.assertEqual(len(calls), 2, "the library was read more than once")

    def test_no_caller_reloads_a_freshly_constructed_tableparser(self) -> None:
        """Constructing loads, so a reload on the next line reads the whole library twice.

        Cheap to reintroduce and invisible at runtime, so it is checked in the source
        rather than left to review.
        """
        offenders = []
        for path in project_files("*.py"):
            if any(part in {"build", "third_party"}
                   for part in path.relative_to(REPO_ROOT).parts):
                continue
            tree = trees.tree_for(path)
            for node in ast.walk(tree):
                body = getattr(node, "body", None)
                if not isinstance(body, list):
                    continue
                for first, second in zip(body, body[1:], strict=False):
                    name = _tableparser_target(first)
                    if name and _is_reload_of(second, name):
                        offenders.append(f"{path.relative_to(REPO_ROOT).as_posix()}:{second.lineno}")

        self.assertEqual(offenders, [], "TableParser is constructed then reloaded at: "
                                        + ", ".join(offenders))


class LoaderPatchSitesTests(unittest.TestCase):
    """A test standing a library in front of the app has to reach every module.

    `all_games` is bound by `from ... import`, so patching one module leaves the
    others holding the real function. That is not hypothetical: patching only
    `frontend.api` and then reading `API.entries` - which resolves through
    `frontend.library_resolver` - ran the real loader against whatever root the preceding
    tests had configured, and failed only in a full run.
    """

    def test_the_sites_are_every_module_that_imported_it(self) -> None:
        """So a sixth importer fails here rather than as an intermittent somewhere else."""
        importers = set()
        for path in project_files("*.py"):
            rel = path.relative_to(REPO_ROOT)
            if rel.parts[0] not in {"common", "frontend", "httpapi"}:
                continue
            tree = trees.tree_for(path)
            # Module level only. A function-local import resolves through the defining
            # module when it runs, so patching that module already reaches it - and it
            # is not an attribute here to patch.
            for node in tree.body:
                if isinstance(node, ast.ImportFrom) and any(
                        alias.name == "all_games" for alias in node.names):
                    importers.add(".".join(rel.with_suffix("").parts))

        covered = set(library_loader.LOADER_SITES) - {"common.games.game_repository"}
        self.assertEqual(importers, covered,
                         "tests/support/library_loader.py names the modules a test has to "
                         "patch; this is the list of modules that actually import it")


if __name__ == "__main__":
    unittest.main()
