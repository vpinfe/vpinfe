"""Protects: (b) an extension installed after the build can import any standard library module
it could import from source.
"""

from __future__ import annotations

import ast
import importlib.util
import pathlib
import sys
import unittest

from scripts import stdlib_modules
from tests.support import trees

SPEC = pathlib.Path(__file__).resolve().parents[2] / "packaging" / "vpinfe.spec"


class StandardLibraryTests(unittest.TestCase):
    def test_the_spec_names_the_standard_library_as_hidden_imports(self) -> None:
        """The spec is a PyInstaller script, so it is read rather than run."""
        calls = [node for node in ast.walk(trees.tree_for(SPEC))
                 if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                 and node.func.attr == "names"
                 and isinstance(node.func.value, ast.Name)
                 and node.func.value.id == "stdlib_modules"]
        self.assertTrue(calls, "packaging/vpinfe.spec no longer adds stdlib_modules.names()")

    def test_every_standard_library_module_but_the_ones_left_out_is_named(self) -> None:
        named = set(stdlib_modules.names())
        missing = sorted(name for name in sys.stdlib_module_names
                         if name not in named and name not in stdlib_modules.LEFT_OUT
                         and not name.startswith("_test") and _importable(name))
        self.assertEqual([], missing)

    def test_what_extensions_use_is_in(self) -> None:
        named = set(stdlib_modules.names())
        for module in ("json", "urllib.request", "xml.etree.ElementTree", "sqlite3", "ssl",
                       "zoneinfo", "concurrent.futures", "email.mime.text", "http.server",
                       "importlib.metadata", "tomllib", "zipfile", "asyncio"):
            with self.subTest(module=module):
                self.assertIn(module, named)

    def test_what_a_cabinet_cannot_use_is_out(self) -> None:
        named = set(stdlib_modules.names())
        for module in ("tkinter", "tkinter.ttk", "turtle", "idlelib", "test", "ensurepip",
                       "unittest.test", "sqlite3.__main__"):
            with self.subTest(module=module):
                self.assertNotIn(module, named)


def _importable(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


if __name__ == "__main__":
    unittest.main()
