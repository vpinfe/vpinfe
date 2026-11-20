"""Every module we import is a module that exists.

Renaming a module and missing one of its importers is not a test failure - the suite
never imports `main.py`, so the entry point can be broken while 992 tests pass. That is
exactly what happened when `clioptions.py` became `cli.py`: the app could not
start at all, and the first thing to notice was the cabinet.

This reads the import statements rather than executing them, so it covers `main.py` and
everything else the suite cannot import for its side effects.
"""

from __future__ import annotations

import ast
import importlib.util
import unittest
from pathlib import Path

from tests.support import trees
from tests.support.project import project_files

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# Our own packages, plus the vendored drop we do not lint or own.
SKIP_DIRS = {"build", "third_party", "chromium", "web", "tests"}
VENDORED: set[str] = set()

# Modules that only exist on one platform, imported inside the branch that needs them.
# `find_spec` answers for the machine running the suite, so without this the check says
# macOS-only imports "name nothing" on every Linux and Windows runner - which it did, and
# the point of this test is renames, not portability.
PLATFORM_ONLY = {
    "AppKit",     # PyObjC, macOS window geometry
    "Quartz",     # PyObjC, macOS key codes
    "objc",
    "Xlib",       # python-xlib, which pynput brings on Linux: X11 window hand-over
    "win32api", "win32con", "win32gui", "winreg",
}


def _source_files():
    for path in project_files("*.py"):
        rel = path.relative_to(REPO_ROOT)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        # Posix: VENDORED is written with forward slashes, and str() on a Windows path
        # gives backslashes - so the vendored drop was scanned there and nowhere else.
        if any(rel.as_posix().startswith(v) for v in VENDORED):
            continue
        yield path, rel


def _is_ours(name: str) -> bool:
    return (REPO_ROOT / f"{name}.py").exists() or (REPO_ROOT / name).is_dir()


class ImportsResolveTests(unittest.TestCase):
    def test_every_imported_module_exists(self) -> None:
        missing = []
        for path, rel in _source_files():
            try:
                tree = trees.tree_for(path)
            except SyntaxError as exc:
                self.fail(f"{rel} does not parse: {exc}")
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [alias.name.split(".")[0] for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    names = [node.module.split(".")[0]]
                else:
                    continue
                for name in names:
                    if name in PLATFORM_ONLY or _is_ours(name):
                        continue
                    if importlib.util.find_spec(name) is not None:
                        continue
                    missing.append(f"{rel}:{node.lineno} imports '{name}'")

        self.assertEqual(missing, [], "imports that name nothing:\n" + "\n".join(missing))

    def test_the_platform_allowlist_earns_its_place(self) -> None:
        """An entry nothing imports is one nobody will remember to remove."""
        imported: set[str] = set()
        for path, _ in _source_files():
            tree = trees.tree_for(path)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.update(alias.name.split(".")[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    imported.add(node.module.split(".")[0])

        # win32* and winreg are listed ahead of the Windows work rather than in use.
        unused = sorted(PLATFORM_ONLY - imported - {"win32api", "win32con", "win32gui",
                                                    "winreg", "objc"})
        self.assertEqual(unused, [], "drop it, or import it")


class SelfSignallingTests(unittest.TestCase):
    """A test that signals its own process says so on the platforms that cannot.

    Windows defines SIGTERM but does not deliver it - `os.kill` falls through to
    TerminateProcess, so the run dies with no failure, no summary and exit 1 halfway
    through. Two test files did this and Tests was red on Windows for both, one after
    the other, each found only by bisecting dots in a CI log.
    """

    def test_every_self_signalling_test_is_guarded(self) -> None:
        unguarded = []
        for path in sorted((REPO_ROOT / "tests").rglob("test_*.py")):
            text = path.read_text(encoding="utf-8", errors="ignore")
            if "os.kill(os.getpid()" not in text:
                continue
            if 'sys.platform.startswith("win")' not in text:
                unguarded.append(path.relative_to(REPO_ROOT).as_posix())
        self.assertEqual(unguarded, [],
                         "signalling your own process kills the run on Windows - guard "
                         "the class with skipIf(sys.platform.startswith('win'), ...)")


if __name__ == "__main__":
    unittest.main()
