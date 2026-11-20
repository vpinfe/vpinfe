"""An extension may import the contract, the standard library, the dependencies the contract
names, and what its manifest lists.

Python will not stop one under `extensions/` importing `game_service` and reaching past
the context it was handed, which is the whole guarantee. Nothing at runtime can make that
impossible, so the boundary is a test - the same way every route is made to declare a
scope, and the same way `tests/apps/test_app_boundary.py` holds the app contract.

It matters most for the ones we write. A first-party extension with a private hook would
advertise a surface that does not work for anybody else, and one importing a dependency nobody
listed would fail on a device that does not have it.
"""

from __future__ import annotations

import json
import pathlib
import unittest

from common.extensions import imports

REPO = pathlib.Path(__file__).resolve().parents[2]
EXTENSIONS_DIR = REPO / "extensions"
STAND_IN_DIR = REPO / "tests" / "fixtures" / "extensions"


def _folders() -> list[pathlib.Path]:
    """Every extension: the stand-ins, and each one in `extensions/`, which is a link to a
    clone, so the walk follows links."""
    return sorted(one for top in (STAND_IN_DIR, EXTENSIONS_DIR) if top.is_dir()
                  for one in top.iterdir() if (one / "extension.json").is_file())


def _listed(folder: pathlib.Path) -> list[str]:
    """The names of the dependencies an extension's manifest lists, as written there."""
    manifest = json.loads((folder / "extension.json").read_text(encoding="utf-8"))
    return [str(one).split("==")[0] for one in manifest.get("dependencies") or ()]


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
        for folder in _folders():
            for refusal in imports.folder_refusals(folder, _listed(folder)):
                if refusal.kind != imports.FIRST_PARTY_IMPORT:
                    continue
                shown = f"{folder.name}/{refusal.file}"
                with self.subTest(source=shown, module=refusal.module):
                    self.fail(f"{shown} imports {refusal.module}; an extension reaches core "
                              f"through the context it is given, or through {imports.CONTRACT}")

    def test_an_extension_imports_no_dependency_nobody_listed(self) -> None:
        """The contract names `fastapi` and `requests`; anything else is in the manifest."""
        for folder in _folders():
            for refusal in imports.folder_refusals(folder, _listed(folder)):
                if refusal.kind != imports.UNLISTED_IMPORT:
                    continue
                shown = f"{folder.name}/{refusal.file}"
                with self.subTest(source=shown, module=refusal.module):
                    self.fail(f"{shown} imports {refusal.module}, which is not the standard "
                              "dependency, the contract, or a dependency its manifest lists")

    def test_the_dependencies_the_contract_names_say_why(self) -> None:
        for name, reason in imports.DEPENDENCIES_OF_THE_CONTRACT.items():
            with self.subTest(dependency=name):
                self.assertTrue(reason.strip())

    def test_an_extension_carries_no_compiled_file(self) -> None:
        for folder in _folders():
            with self.subTest(extension=folder.name):
                self.assertEqual([], imports.compiled_files(folder))


if __name__ == "__main__":
    unittest.main()
