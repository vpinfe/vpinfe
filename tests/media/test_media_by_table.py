"""Per-table media resolution: two builds in one folder can answer differently.

The chain always keyed tier 1 off a table stem, but the scan only ever ran it once, for
the table that launches - so /media/<table id>/<kind> was addressed by table and answered
by game. These cover the resolution the scan now records per .vpx, and the lookup that
reads it.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from common.games.media_lookup import media_path, table_filename
from common.media.media_specs import resolve_media_by_table

FOLDER = "Cactus Canyon (Bally 1998)"
DESKTOP = f"{FOLDER} - VPW 1.2.vpx"
VR = f"{FOLDER} - VR 2.0.vpx"


def _by_table(medias, tables=(DESKTOP, VR), root=()):
    return resolve_media_by_table(f"/games/{FOLDER}", set(root), set(medias), tables)


class ResolveByTableTests(unittest.TestCase):
    def test_each_table_gets_its_own_entry(self) -> None:
        by_table = _by_table(["wheel.png"])

        self.assertEqual(set(by_table), {DESKTOP.lower(), VR.lower()})

    def test_a_table_named_file_reaches_only_that_table(self) -> None:
        by_table = _by_table([f"(Playfield) {Path(VR).stem}.png", "table.png"])

        self.assertTrue(by_table[VR.lower()]["playfield"]
                        .endswith(f"(Playfield) {Path(VR).stem}.png"))
        self.assertTrue(by_table[DESKTOP.lower()]["playfield"].endswith("table.png"))

    def test_a_folder_named_file_reaches_every_table(self) -> None:
        by_table = _by_table([f"(Wheel) {FOLDER}.png"])

        for name in (DESKTOP, VR):
            self.assertTrue(by_table[name.lower()]["wheel"]
                            .endswith(f"(Wheel) {FOLDER}.png"))

    def test_kinds_that_resolved_to_nothing_are_not_recorded(self) -> None:
        by_table = _by_table(["wheel.png"])

        self.assertEqual(set(by_table[VR.lower()]), {"wheel"})

    def test_a_folder_with_no_tables_records_nothing(self) -> None:
        self.assertEqual(_by_table(["wheel.png"], tables=()), {})


class _Game:
    """Enough of a game for the lookup: the ledger of tables, and the scan's answers."""

    def __init__(self, media_by_table, **attrs):
        self.media_by_table = media_by_table
        self.meta_config = {"tables": {
            "tbl0000001": {"filename": DESKTOP},
            "tbl0000002": {"filename": VR},
        }}
        self.playfield_image_path = attrs.get("playfield")
        self.wheel_image_path = attrs.get("wheel")


class LookupTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = Path(__file__).parent / "_by_table_tmp"
        self._tmp.mkdir(exist_ok=True)
        self.mine = self._tmp / "vr.png"
        self.shared = self._tmp / "shared.png"
        for path in (self.mine, self.shared):
            path.write_bytes(b"x")

    def tearDown(self) -> None:
        for path in self._tmp.iterdir():
            path.unlink()
        self._tmp.rmdir()

    def _game(self):
        return _Game(
            {DESKTOP.lower(): {"playfield": str(self.shared)},
             VR.lower(): {"playfield": str(self.mine)}},
            playfield=str(self.shared))

    def test_the_id_in_the_url_decides_which_file_is_served(self) -> None:
        games = [self._game()]

        self.assertEqual(media_path(games, "tbl0000002", "playfield"), self.mine)
        self.assertEqual(media_path(games, "tbl0000001", "playfield"), self.shared)

    def test_a_kind_this_table_lacks_does_not_borrow_the_default_table_s(self) -> None:
        # The regression this whole change is about: the game attribute answers for the
        # table that launches, so falling back to it serves one table's art as another's.
        game = _Game({DESKTOP.lower(): {"wheel": str(self.shared)},
                      VR.lower(): {}},
                     wheel=str(self.shared))

        self.assertIsNone(media_path([game], "tbl0000002", "wheel"))
        self.assertEqual(media_path([game], "tbl0000001", "wheel"), self.shared)

    def test_a_game_the_scan_never_touched_still_answers(self) -> None:
        game = _Game(None, playfield=str(self.shared))

        self.assertEqual(media_path([game], "tbl0000002", "playfield"), self.shared)

    def test_an_unknown_table_id_is_not_a_table_of_this_game(self) -> None:
        self.assertEqual(table_filename(self._game(), "tbl0000009"), "")


if __name__ == "__main__":
    unittest.main()
