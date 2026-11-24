"""Everything the app reads at runtime that is not Python is in the build.

PyInstaller follows imports and nothing else, so a data file reaches a release only if
`packaging/vpinfe.spec` names a root above it. The failure is a missing image or a key on
screen in a release artifact, found by a person looking at a cabinet and never by a red
suite.

The spec lists roots and PyInstaller takes each one whole, so a new file under an existing
root needs nothing. What needs saying is a new *owner*.
"""

from __future__ import annotations

import ast
import pathlib
import subprocess
import unittest

from tests.support import trees
from tests.support.project import project_files

REPO = pathlib.Path(__file__).resolve().parent.parent.parent
SPEC = REPO / "packaging" / "vpinfe.spec"

# What the build runs or is described by, never what it ships.
NOT_SHIPPED = ("tests/", "docs/", "packaging/", "scripts/", ".github/")
SUBMODULE = "160000"


def _declared_roots() -> set[str]:
    """The DATA_ROOTS list out of the spec, read rather than imported.

    The spec is a PyInstaller script and running it needs PyInstaller's own globals, so
    this parses it instead of importing it.
    """
    tree = trees.tree_for(SPEC)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        names = {t.id for t in node.targets if isinstance(t, ast.Name)}
        if "DATA_ROOTS" not in names:
            continue
        assert isinstance(node.value, ast.List)
        return {
            element.value for element in node.value.elts
            if isinstance(element, ast.Constant) and isinstance(element.value, str)
        }
    raise AssertionError("packaging/vpinfe.spec no longer declares DATA_ROOTS")


def _tracked_data() -> list[str]:
    """Every tracked file that is not Python and not one of the build's own."""
    listed = subprocess.run(["git", "ls-files", "-s", "-z"], cwd=REPO, check=True,
                            capture_output=True, text=True).stdout
    found = []
    for line in filter(None, listed.split("\0")):
        mode, _, _, path = line.split(maxsplit=3)
        if mode == SUBMODULE or "/" not in path or path.startswith(NOT_SHIPPED):
            continue
        if not path.endswith(".py"):
            found.append(path)
    return found


def _unshipped(paths: list[str], roots: set[str]) -> list[str]:
    return sorted(path for path in paths
                  if not any(path.startswith(f"{root}/") for root in roots))


def _static_dirs_in_tree() -> set[str]:
    """Every directory named `static` that holds a file we ship."""
    found: set[str] = set()
    for path in project_files("*"):
        parts = path.relative_to(REPO).parts
        found.update("/".join(parts[:depth]) for depth, part in enumerate(parts[:-1], 1)
                     if part == "static")
    return {relative for relative in found
            if not relative.startswith((".", "third-party/", "third_party/", "chromium/",
                                        "web/"))
            and "node_modules" not in relative}


class BuildDataTests(unittest.TestCase):
    def test_every_static_directory_is_one_the_build_ships(self) -> None:
        """A static directory the spec does not name is simply absent from a release,
        and nothing else reports that."""
        declared = _declared_roots()
        missing = sorted(
            directory for directory in _static_dirs_in_tree()
            if not any(directory == root or directory.startswith(f"{root}/")
                       for root in declared)
        )
        self.assertEqual(missing, [])

    @unittest.skipIf(not (REPO / ".git").exists(), "not a git checkout")
    def test_every_tracked_data_file_is_under_a_root_the_build_ships(self) -> None:
        self.assertEqual(_unshipped(_tracked_data(), _declared_roots()), [])

    def test_the_spec_names_nothing_that_has_gone(self) -> None:
        """A root that no longer exists means the build copies nothing and says nothing.

        `third_party/` is not in a checkout: it is gitignored and the fetch scripts fill it
        before PyInstaller runs, so it is absent here and present in a build.
        """
        gone = sorted(
            root for root in _declared_roots()
            if not root.startswith("third_party/") and not (REPO / root).exists()
        )
        self.assertEqual(gone, [])

    def test_the_checker_can_actually_fail(self) -> None:
        """It reads the spec rather than trusting a constant, so prove it reads one."""
        declared = _declared_roots()
        self.assertIn("frontend/static", declared)
        self.assertNotIn("nothing/static", declared)

    @unittest.skipIf(not (REPO / ".git").exists(), "not a git checkout")
    def test_the_file_check_can_actually_fail(self) -> None:
        unshipped = _unshipped(_tracked_data(), {"frontend/static"})
        self.assertIn("common/i18n/catalogs/en.json", unshipped)
        self.assertNotIn("frontend/static/common/vpinfe-core.js", unshipped)
