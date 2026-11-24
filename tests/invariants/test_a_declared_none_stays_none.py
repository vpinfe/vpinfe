"""A machine declared to be in no catalog is never resolved back into a match.

`alt_vpsid` null says so, and `str(value or "")` turns it into the empty that means no
opinion. A reader doing that falls back to `Info.VPSId`, at that reader alone.
"""

from __future__ import annotations

import ast
import pathlib
import unittest

from tests.support import trees

REPO = pathlib.Path(__file__).resolve().parent.parent.parent
KEY = "alt_vpsid"

# Every site that reads the value, and what it does about null. A new one fails this
# until it is read and added, because the flattening is invisible at the call site.
REVIEWED = {
    # Knows: the predicate, and the resolver that answers "" rather than falling through.
    "common/games/game_metadata.py",
    # Knows: the row every lens reads keeps None rather than flattening it.
    "common/games/game_repository.py",
    # Knows: the effective id on the wire.
    "common/games/game_lens.py",
    # Knows: a 2.x migration leaves a declared none alone.
    "common/games/game_service.py",
    # Flattens, correctly: an id to match membership against, guarded by truthiness, and
    # a declared none has no id to match by.
    "common/games/collection_store.py",
    # Flattens, correctly: the theme contract carries strings and a theme reads the
    # effective id, which is already "" for a declared none.
    "common/games/entry_lens.py",
    "frontend/game_state.py",
}


def _reads(path: pathlib.Path) -> list[int]:
    """Lines calling `.get("alt_vpsid", ...)` or subscripting it. A rename map or a
    constant naming the key reads nothing and is not one of these."""
    out = []
    for node in ast.walk(trees.tree_for(path)):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
           and node.func.attr == "get" and node.args \
           and isinstance(node.args[0], ast.Constant) and node.args[0].value == KEY:
            out.append(node.lineno)
        if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant) \
           and node.slice.value == KEY:
            out.append(node.lineno)
    return out


class TestADeclaredNoneStaysNone(unittest.TestCase):
    def test_the_predicate_answers(self) -> None:
        from common.games.game_metadata import declared_no_match
        self.assertTrue(declared_no_match({"vpinfe": {"alt_vpsid": None}}))
        self.assertFalse(declared_no_match({"vpinfe": {"alt_vpsid": ""}}))
        self.assertFalse(declared_no_match({"vpinfe": {"alt_vpsid": "abc"}}))
        self.assertFalse(declared_no_match({"vpinfe": {}}))
        self.assertFalse(declared_no_match({}))

    def test_the_resolver_does_not_fall_through(self) -> None:
        from common.games.game_metadata import game_vps_id

        class Game:
            meta_config = {"vpinfe": {"alt_vpsid": None},
                           "Info": {"VPSId": "SCANNED1"}}

        self.assertEqual(game_vps_id(Game()), "",  # type: ignore[arg-type]
                         "a declared none must not resolve to the scanned id")

    def test_the_migration_leaves_it_alone(self) -> None:
        from common.games.game_service import route_legacy_match
        data = {"vpinfe": {"alt_vpsid": None}}
        self.assertEqual(route_legacy_match(data), "kept")
        self.assertIsNone(data["vpinfe"]["alt_vpsid"])

    def test_every_reader_has_been_read(self) -> None:
        offenders = []
        for folder in ("common", "httpapi", "manager", "frontend"):
            for path in sorted((REPO / folder).rglob("*.py")):
                if "__pycache__" in path.parts:
                    continue
                name = path.relative_to(REPO).as_posix()
                if name in REVIEWED:
                    continue
                for line in _reads(path):
                    offenders.append(f"{name}:{line}")
        self.assertEqual(sorted(offenders), [],
                         f"reads {KEY} without having been read for null; "
                         "check it, then add it to REVIEWED with what it does")


if __name__ == "__main__":
    unittest.main()
