"""An extension may import the contract, the standard library, and what it declares.

Python will not stop one under `extensions/` importing `game_service` and reaching past
the context it was handed, which is the whole guarantee. Nothing at runtime can make that
impossible, so the boundary is a test - the same way every route is made to declare a
scope, and the same way `tests/apps/test_app_boundary.py` holds the app contract.

It matters most for the ones we write. A first-party extension with a private hook would
advertise a surface that does not work for anybody else.
"""

from __future__ import annotations

import ast
import pathlib
import sys
import unittest

from tests.support import trees

REPO = pathlib.Path(__file__).resolve().parents[2]
EXTENSIONS_DIR = REPO / "extensions"
STAND_IN_DIR = REPO / "tests" / "fixtures" / "extensions"

# The one module of ours an extension may import: the manifest types and the ABI. Its
# reach into everything else is the context, which is handed to it and imported from
# nowhere.
CONTRACT = "common.extensions.contract"

FIRST_PARTY = frozenset({"apps", "common", "manager", "extensions", "frontend",
                         "httpapi", "tests"})

# Third-party an extension may use, one line per library, because adding a dependency to
# an extension should be a decision somebody made rather than one nobody saw.
ALLOWED_THIRD_PARTY = frozenset({
    "fastapi",      # ctx.add_router takes an APIRouter
})

STDLIB = frozenset(sys.stdlib_module_names)


def _imports(tree: ast.AST) -> list[str]:
    """Every module an import names, dotted and absolute. A relative import resolves
    inside the extension itself and carries no module name to check."""
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.append(node.module)
    return found


def _sources() -> list[pathlib.Path]:
    """Every extension source: the stand-ins, and each extension in `extensions/`, which
    is a link to a clone, so the walk follows links. An extension's own tests import the
    test kit and are not extension code."""
    return sorted([*trees.source_files(STAND_IN_DIR), *trees.source_files(EXTENSIONS_DIR)])


class ImportBoundaryTests(unittest.TestCase):
    def test_the_included_extensions_are_in_the_extensions_folder(self) -> None:
        """Their code and their tests are repositories of their own, linked or cloned in."""
        from scripts.fetch_extensions import INCLUDED
        absent = [name for name in INCLUDED
                  if not (EXTENSIONS_DIR / name / "extension.json").is_file()]
        self.assertEqual([], absent,
                         "Not in extensions/: clone each from its repository into that folder, "
                         "or link a clone you work in")

    def test_an_extension_imports_the_contract_and_nothing_else_of_ours(self) -> None:
        for source in _sources():
            tree = trees.tree_for(source)
            for module in _imports(tree):
                root = module.split(".")[0]
                if root in STDLIB or root in ALLOWED_THIRD_PARTY:
                    continue
                if module == CONTRACT or module.startswith(f"{CONTRACT}."):
                    continue
                shown = f"{source.parent.name}/{source.name}"
                with self.subTest(source=shown, module=module):
                    self.assertNotIn(root, FIRST_PARTY,
                                     f"{shown} imports "
                                     f"{module}; an extension reaches core through the "
                                     f"context it is given, or through {CONTRACT}")


if __name__ == "__main__":
    unittest.main()
