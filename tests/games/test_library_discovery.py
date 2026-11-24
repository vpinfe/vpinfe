"""Reconciling a folder's .vpx files against what its .info describes.

Metadata build describes one table per folder, so every other build in a folder was
invisible to anything addressing tables by id. Discovery closes that without parsing
anything, and records - never acts on - a file that has gone.
"""

from __future__ import annotations

import unittest

from common.games.library_discovery import absent_since, discover
from common.games.tables import ABSENT_SINCE_KEY, entry_filename, table_entries
from tests.support.library import TempTree, fake_game, write_game
from tests.support.saved import saved_record

FOLDER = "Cactus Canyon (Bally 1998)"
DESKTOP = f"{FOLDER}.vpx"
VR = f"{FOLDER} - VR.vpx"


def _info(tables: dict) -> dict:
    return {"Info": {"Name": "Cactus Canyon"},
            "vpinfe": {"game_id": "cc00000001", "schema": 2},
            "tables": tables}


class DiscoveryTests(TempTree):
    def _game(self, described: dict, on_disk=(DESKTOP, VR)):
        folder = write_game(self.root, FOLDER, info=_info(described), vpx=False,
                            files={name: b"vpx" for name in on_disk})
        self.folder = folder
        return fake_game(folder, FOLDER, meta=_info(described),
                         table_files=list(on_disk))

    def _stored(self) -> dict:
        return table_entries(saved_record(self.folder / f"{FOLDER}.info"))

    def _by_name(self) -> dict:
        return {entry_filename(e).lower(): e for e in self._stored().values()}

    def test_a_build_nothing_described_gets_an_entry(self) -> None:
        game = self._game({"tbl0000001": {"id": "tbl0000001", "filename": DESKTOP}})

        totals = discover([game])

        self.assertEqual(totals["found"], 1)
        self.assertIn(VR.lower(), self._by_name())

    def test_the_new_entry_claims_nothing_but_its_name(self) -> None:
        game = self._game({"tbl0000001": {"id": "tbl0000001", "filename": DESKTOP}})
        discover([game])

        self.assertEqual(self._by_name()[VR.lower()], {"filename": VR})

    def test_a_described_file_that_is_gone_is_stamped(self) -> None:
        game = self._game({"tbl0000001": {"id": "tbl0000001", "filename": DESKTOP},
                           "tbl0000002": {"id": "tbl0000002", "filename": VR}},
                          on_disk=(DESKTOP,))

        totals = discover([game])

        self.assertEqual(totals["absent"], 1)
        self.assertTrue(absent_since(self._by_name()[VR.lower()]))

    def test_a_gone_file_keeps_its_id_and_its_history(self) -> None:
        """The entry is what carries `hidden` and the play stats, so it stays put."""
        game = self._game({"tbl0000002": {"id": "tbl0000002", "filename": VR,
                                          "hidden": True, "plays": 12}},
                          on_disk=(DESKTOP,))

        discover([game])
        entry = self._by_name()[VR.lower()]

        self.assertEqual(entry["id"], "tbl0000002")
        self.assertTrue(entry["hidden"])
        self.assertEqual(entry["plays"], 12)

    def test_a_file_that_comes_back_loses_the_stamp(self) -> None:
        game = self._game({"tbl0000002": {"id": "tbl0000002", "filename": VR,
                                          ABSENT_SINCE_KEY: "2026-08-01T00:00:00Z"}})

        totals = discover([game])

        self.assertEqual(totals["returned"], 1)
        self.assertEqual(absent_since(self._by_name()[VR.lower()]), "")

    def test_a_stamp_is_not_moved_while_the_file_stays_gone(self) -> None:
        stamp = "2026-08-01T00:00:00Z"
        game = self._game({"tbl0000002": {"id": "tbl0000002", "filename": VR,
                                          ABSENT_SINCE_KEY: stamp}},
                          on_disk=(DESKTOP,))

        discover([game])

        self.assertEqual(absent_since(self._by_name()[VR.lower()]), stamp)

    def test_a_settled_library_is_not_rewritten(self) -> None:
        game = self._game({"tbl0000001": {"id": "tbl0000001", "filename": DESKTOP},
                           "tbl0000002": {"id": "tbl0000002", "filename": VR}})

        self.assertEqual(discover([game])["games"], 0)

    def test_a_game_with_no_listing_is_left_entirely_alone(self) -> None:
        """The hazard this refuses: a share that has not mounted reads as no files at
        all, and marking the whole library gone would be one write away from losing
        every hidden flag and play count in it."""
        game = self._game({"tbl0000001": {"id": "tbl0000001", "filename": DESKTOP}})
        game.table_files = None

        self.assertFalse(any(discover([game]).values()))
        self.assertEqual(len(self._stored()), 1)

    def test_an_empty_listing_is_refused_the_same_way(self) -> None:
        game = self._game({"tbl0000001": {"id": "tbl0000001", "filename": DESKTOP}})
        game.table_files = []

        discover([game])

        self.assertEqual(absent_since(self._by_name()[DESKTOP.lower()]), "")


if __name__ == "__main__":
    unittest.main()
