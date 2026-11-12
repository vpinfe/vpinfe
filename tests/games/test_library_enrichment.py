"""Reading the tables discovery found.

Discovery leaves an entry holding a filename and an id. This is what turns that into a
table with a rom and a version, and it is the expensive half - a parse hashes the whole
file - so what it *skips* matters as much as what it reads.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from common.games.library_enrichment import enrich, pending
from common.games.tables import entry_filename, table_entries
from tests.support.library import TempTree, fake_game, write_game
from tests.support.saved import saved_record

FOLDER = "Cactus Canyon (Bally 1998)"
DESKTOP = f"{FOLDER}.vpx"
VR = f"{FOLDER} - VR.vpx"

PARSE = {"filename": VR, "file_hash": "abc123", "rom": "cc_13", "version": "2.0",
         "author_name": "Sixtoe, Flux", "detect_ssf": True}


def _info(tables: dict) -> dict:
    return {"Info": {"Name": "Cactus Canyon"},
            "vpinfe": {"game_id": "cc00000001", "schema": 2},
            "tables": tables}


class EnrichmentTests(TempTree):
    def _game(self, described: dict):
        folder = write_game(self.root, FOLDER, info=_info(described), vpx=False,
                            files={DESKTOP: b"vpx", VR: b"vpx"})
        self.folder = folder
        return fake_game(folder, FOLDER, meta=_info(described),
                         table_files=[DESKTOP, VR])

    def _stored(self) -> dict:
        data = saved_record(self.folder / f"{FOLDER}.info")
        return {entry_filename(e).lower(): e
                for e in table_entries(data).values()}

    def test_an_unread_entry_is_offered_for_reading(self) -> None:
        game = self._game({"t1": {"id": "t1", "filename": DESKTOP, "rom": "cc_13"},
                           "t2": {"id": "t2", "filename": VR}})

        todo = pending([game])

        self.assertEqual([(key, name) for _, key, name in todo], [("t2", VR)])

    def test_an_entry_already_read_is_not_read_again(self) -> None:
        """The whole file is hashed to read one, so re-reading a settled library would
        be the most expensive way to learn nothing."""
        game = self._game({"t1": {"id": "t1", "filename": DESKTOP, "rom": "cc_13"}})

        self.assertEqual(pending([game]), [])

    def test_an_entry_whose_file_is_gone_is_not_offered(self) -> None:
        game = self._game({"t9": {"id": "t9", "filename": "vanished.vpx"}})

        self.assertEqual(pending([game]), [])

    def test_reading_fills_the_entry_in(self) -> None:
        game = self._game({"t2": {"id": "t2", "filename": VR}})

        with patch("common.games.formats.vpx_parser.VPXParser.single_file_extract",
                   return_value=PARSE):
            totals = enrich([game])

        entry = self._stored()[VR.lower()]
        self.assertEqual(totals["read"], 1)
        self.assertEqual(entry["rom"], "cc_13")
        self.assertEqual(entry["authors"], ["Sixtoe", "Flux"])
        self.assertTrue(entry["detect_ssf"])

    def test_reading_never_overwrites_what_a_parse_does_not_know(self) -> None:
        game = self._game({"t2": {"id": "t2", "filename": VR,
                                  "hidden": True, "plays": 7}})

        with patch("common.games.formats.vpx_parser.VPXParser.single_file_extract",
                   return_value=PARSE):
            enrich([game])

        entry = self._stored()[VR.lower()]
        self.assertEqual(entry["id"], "t2")
        self.assertTrue(entry["hidden"])
        self.assertEqual(entry["plays"], 7)

    def test_a_file_that_will_not_read_is_counted_and_left_alone(self) -> None:
        game = self._game({"t2": {"id": "t2", "filename": VR}})

        with patch("common.games.formats.vpx_parser.VPXParser.single_file_extract",
                   return_value=None):
            totals = enrich([game])

        self.assertEqual(totals, {"read": 0, "failed": 1, "games": 0})
        self.assertEqual(self._stored()[VR.lower()], {"id": "t2", "filename": VR})

    def test_a_file_that_will_not_read_is_offered_again_only_once_it_changes(self) -> None:
        game = self._game({"t2": {"id": "t2", "filename": VR}})
        with patch("common.games.formats.vpx_parser.VPXParser.single_file_extract",
                   return_value=None):
            enrich([game])

        before = pending([game])
        (self.folder / VR).write_bytes(b"a different vpx")

        self.assertEqual(before, [])
        self.assertEqual([name for _, _, name in pending([game])], [VR])

    def test_progress_is_reported_against_a_known_total(self) -> None:
        game = self._game({"t1": {"id": "t1", "filename": DESKTOP},
                           "t2": {"id": "t2", "filename": VR}})
        seen = []

        class _Reporter:
            def progress(self, current, total, message):
                seen.append((current, total))

            def log(self, message):
                pass

        with patch("common.games.formats.vpx_parser.VPXParser.single_file_extract",
                   return_value=PARSE):
            enrich([game], _Reporter())  # type: ignore[arg-type]

        self.assertEqual(seen, [(1, 2), (2, 2)])


if __name__ == "__main__":
    unittest.main()
