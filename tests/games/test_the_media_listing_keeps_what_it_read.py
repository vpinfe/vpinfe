"""The library's media listing reads a game again only once something about it changed."""

from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from common.games import media_lens
from tests.support.library import TempTree, fake_game, game_info, write_game

GAME_ID = "Held0000001"
FOLDER = "Medieval Madness (Williams 1997)"
TABLES = {"tbl0000001": {"id": "tbl0000001", "filename": f"{FOLDER}.vpx"}}


class HeldListingTests(TempTree):
    def setUp(self) -> None:
        super().setUp()
        self.info = game_info("Medieval Madness", vps_id="vps-1", game_id=GAME_ID,
                              tables=TABLES)
        self.folder = write_game(self.root, FOLDER, info=self.info,
                                 medias={"table.png": b"\x89PNGtable"})
        self.catalog = {GAME_ID: fake_game(self.folder, FOLDER, meta=self.info)}
        patcher = patch("common.games.game_repository.catalog", return_value=self.catalog)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.reads = patch("common.games.media_lens._game_rows",
                           wraps=media_lens._game_rows)

    def _row(self, kind: str) -> dict:
        return next(row for row in media_lens.listing()["media"]
                    if row["kind"] == kind and not row["table"])

    def test_an_unchanged_game_is_not_read_again(self) -> None:
        first = media_lens.listing()
        with self.reads as reads:
            second = media_lens.listing()
        self.assertEqual(reads.call_count, 0)
        self.assertEqual(second, first)

    def test_a_file_added_to_the_folder_shows_on_the_next_listing(self) -> None:
        self.assertFalse(self._row("wheel")["present"])
        (self.folder / "medias" / "wheel.png").write_bytes(b"\x89PNGwheel")
        self.assertTrue(self._row("wheel")["present"])

    def test_a_record_written_without_a_refresh_is_read_again(self) -> None:
        """What persist_game_meta does: the same game object, its record rebound."""
        self.assertEqual(self._row("wheel")["vps_id"], "vps-1")
        info = game_info("Medieval Madness", vps_id="vps-22", game_id=GAME_ID,
                         tables=TABLES)
        (self.folder / f"{FOLDER}.info").write_text(json.dumps(info), encoding="utf-8")
        self.catalog[GAME_ID].meta_config = info
        self.assertEqual(self._row("wheel")["vps_id"], "vps-22")

    def test_a_game_the_catalog_read_again_is_read_again(self) -> None:
        self.assertEqual(self._row("wheel")["vps_id"], "vps-1")
        info = game_info("Medieval Madness", vps_id="vps-22", game_id=GAME_ID,
                         tables=TABLES)
        self.catalog[GAME_ID] = fake_game(self.folder, FOLDER, meta=info)
        self.assertEqual(self._row("wheel")["vps_id"], "vps-22")

    def test_one_game_is_read_from_disk_every_time(self) -> None:
        media_lens.listing()
        with self.reads as reads:
            media_lens.listing(game=GAME_ID)
            media_lens.listing(game=GAME_ID)
        self.assertEqual(reads.call_count, 2)


if __name__ == "__main__":
    unittest.main()
