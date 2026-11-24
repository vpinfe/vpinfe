"""The apps under `apps/` may import the contract and the standard library.

Python will not stop `apps/vpx/` importing `game_service` and quietly re-fusing what the
contract just separated. Nothing in this project can make that impossible at runtime, so
the boundary is a test - the same way every route is made to declare a scope.
"""

from __future__ import annotations

import ast
import pathlib
import sys
import unittest

from tests.support import trees

APPS_DIR = pathlib.Path(__file__).resolve().parents[2] / "apps"

# The one module of ours an app may import. It is the boundary.
CONTRACT = "common.apps.contract"

# Ours, whatever else is on the path.
FIRST_PARTY = frozenset({"apps", "common", "manager", "frontend", "httpapi",
                         "tests"})

# Third-party an app may use, one line per library. It stays a list rather than a blanket
# allowance so that adding a dependency to an app is a decision somebody made rather than
# one nobody saw.
ALLOWED_THIRD_PARTY: frozenset[str] = frozenset({
    "olefile",  # reads the flags a .vpx table carries (apps/vpx/tablefile.py)
})

STDLIB = frozenset(sys.stdlib_module_names)


def _imports(tree: ast.AST) -> list[str]:
    """Every module an import statement names, dotted and absolute.

    A relative import inside `apps/` resolves within the app itself, which is always
    allowed and carries no module name to check.
    """
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.append(node.module)
    return found


def _app_sources() -> list[pathlib.Path]:
    return sorted(APPS_DIR.rglob("*.py"))


class ImportBoundaryTests(unittest.TestCase):
    def test_there_are_apps_to_check(self) -> None:
        """A boundary test over an empty directory passes and means nothing."""
        self.assertTrue(_app_sources(), f"No app sources under {APPS_DIR}")

    def test_an_app_imports_the_contract_and_nothing_else_of_ours(self) -> None:
        for source in _app_sources():
            tree = trees.tree_for(source)
            for module in _imports(tree):
                root = module.split(".")[0]
                if root not in FIRST_PARTY or root == "apps":
                    continue
                with self.subTest(source=source.name, module=module):
                    self.assertEqual(
                        module, CONTRACT,
                        f"{source} imports {module}. An app may import {CONTRACT} and "
                        f"nothing else of ours.")

    def test_an_app_takes_no_dependency_nobody_agreed_to(self) -> None:
        for source in _app_sources():
            tree = trees.tree_for(source)
            for module in _imports(tree):
                root = module.split(".")[0]
                if root in FIRST_PARTY or root in STDLIB:
                    continue
                with self.subTest(source=source.name, module=module):
                    self.assertIn(
                        root, ALLOWED_THIRD_PARTY,
                        f"{source} imports {module}, which is neither the standard "
                        f"library nor in ALLOWED_THIRD_PARTY.")


class RegistryTests(unittest.TestCase):
    """The built-in source is linked rather than loaded, so it cannot fail to be there."""

    def test_the_registry_holds_both_built_in_apps(self) -> None:
        from common import apps

        self.assertEqual([app.id for app in apps.all_apps()], ["vpx", "generic"])

    def test_generic_is_always_present(self) -> None:
        """An install whose everything else is broken still has to be configurable, and
        a frontend that boots to a black screen is the worst outcome there is."""
        from common import apps

        self.assertIsNotNone(apps.get("generic"))

    def test_generic_claims_no_extension(self) -> None:
        """It must never take a table away from the app that understands it."""
        from common import apps

        app = apps.app_for("Medieval Madness.vpx")
        assert app is not None
        self.assertEqual(app.id, "vpx")

    def test_every_app_answers_the_groups_it_declares(self) -> None:
        """A group is either implemented or None. A half-answer is what the two
        implementations exist to catch."""
        from common import apps
        from common.apps import contract

        for app in apps.all_apps():
            with self.subTest(app=app.id):
                self.assertIsInstance(app.claim, contract.Claim)
                self.assertIsInstance(app.kinds, contract.Kinds)
                for group, protocol in (("format", contract.Format),
                                        ("launch", contract.Launch),
                                        ("config", contract.Config),
                                        ("capability", contract.Capability)):
                    held = getattr(app, group)
                    if held is not None:
                        self.assertIsInstance(held, protocol)


if __name__ == "__main__":
    unittest.main()
