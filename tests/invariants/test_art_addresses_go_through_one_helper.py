"""A Manager picture's address is built by `manager/kit/art.py` and nowhere else.

The helper is what asks for the copy sized for where the picture is drawn, and for the
version that lets the browser keep it. An address written out at a call site asks for
the original on every draw.
"""

from __future__ import annotations

import ast
import pathlib
import unittest

from tests.support import trees

REPO = pathlib.Path(__file__).resolve().parent.parent.parent
MANAGER = REPO / "manager"
HELPER = MANAGER / "kit" / "art.py"

# The art routes live under these. `manager/kit/api.py` calls the same routes relative to its
# own base, as a client rather than as something a page draws, so it never spells these.
ART_ROOTS = ("/api/v1/games", "/api/v1/collections")


def _fragments(path: pathlib.Path) -> list[tuple[int, str]]:
    """Every string literal, and every literal piece of an f-string, with its line."""
    found = []
    for node in ast.walk(trees.tree_for(path)):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            found.append((node.lineno, node.value))
    return found


def _addresses(path: pathlib.Path) -> list[str]:
    if path.suffix == ".py":
        pieces = _fragments(path)
    else:
        pieces = list(enumerate(path.read_text(encoding="utf-8").splitlines(), 1))
    return [f"{path.relative_to(REPO).as_posix()}:{line}" for line, text in pieces
            if any(root in text for root in ART_ROOTS)]


class ArtAddressesGoThroughOneHelperTests(unittest.TestCase):
    def test_no_manager_module_writes_an_art_address(self) -> None:
        offenders = []
        for path in sorted([*MANAGER.rglob("*.py"), *MANAGER.rglob("*.js")]):
            if path != HELPER:
                offenders += _addresses(path)
        self.assertEqual(offenders, [], "build it with manager/kit/art.py")

    def test_the_scan_finds_the_helper_s_own_addresses(self) -> None:
        """Or a scan that matches nothing would pass the test above as well."""
        self.assertGreaterEqual(len(_addresses(HELPER)), 2)


if __name__ == "__main__":
    unittest.main()
