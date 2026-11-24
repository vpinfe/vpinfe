"""A media write keeps a copy of the file it replaces, so Backups can put it back.

The write is `media_placement.place`, which a drop, Import a File, a picture chosen from a
source and a download all reach. What is worth pinning: the copy is there before the file
goes, every file the write takes is kept (a different extension removes the old one), a write
that adds to an empty slot keeps nothing, and a backups folder that cannot be written to
refuses the write with the file untouched.
"""

from __future__ import annotations

import pathlib
import unittest
from unittest.mock import patch

from common import service_errors
from common.backups import copies
from common.backups import folder as backup_folder
from common.games import game_repository, media_ops, media_placement
from common.games.game_identity import game_id as game_id_of
from tests.api.test_backups import BackupsCase
from tests.support.library import write_game
from tests.support.pictures import png

GAME = "id-Taxi"
NAME = "Taxi"
OLD = b"old picture"


class ReplaceCopyCase(BackupsCase):
    def setUp(self) -> None:
        super().setUp()
        self.folder = write_game(self.library, NAME, vpx=True,
                                 info={"Info": {"Title": NAME}, "vpinfe": {"schema": 2,
                                                                          "game_id": GAME}},
                                 medias={"(Wheel) Taxi.png": OLD})
        self.incoming = self.library.parent / "incoming"
        self.incoming.mkdir()
        catalog = patch.object(
            game_repository, "catalog",
            side_effect=lambda: {game_id_of(game): game for game in self.library_games()})
        catalog.start()
        self.addCleanup(catalog.stop)
        for patcher in (patch.object(media_ops, "reread"),
                        patch.object(media_placement, "record_origin")):
            patcher.start()
            self.addCleanup(patcher.stop)

    def source(self, name: str = "new.png", data: bytes | None = None) -> pathlib.Path:
        path = self.incoming / name
        path.write_bytes(png(8, 8) if data is None else data)
        return path

    def media_copies(self) -> list[dict]:
        return [one for one in self.listing() if one["kind"] == "media"]


class KeepsWhatItReplacesTests(ReplaceCopyCase):
    def test_a_file_replaced_in_its_slot_is_kept_with_the_reason_replace(self) -> None:
        media_placement.place(self.folder, "wheel", NAME, self.source())

        kept = self.media_copies()
        self.assertEqual([(one["reason"], one["file"]) for one in kept],
                         [(copies.REASON_REPLACE, "medias/(Wheel) Taxi.png")])
        self.assertEqual(pathlib.Path(kept[0]["path"]).read_bytes(), OLD)

    def test_a_drop_through_the_shared_write_keeps_the_file_it_replaced(self) -> None:
        media_ops.place_file(GAME, "wheel", "", self.source())

        self.assertEqual([one["reason"] for one in self.media_copies()],
                         [copies.REASON_REPLACE])

    def test_a_different_extension_keeps_the_file_it_removes(self) -> None:
        media_placement.place(self.folder, "wheel", NAME, self.source("new.jpg"))

        self.assertFalse((self.folder / "medias" / "(Wheel) Taxi.png").exists())
        kept = self.media_copies()
        self.assertEqual([one["file"] for one in kept], ["medias/(Wheel) Taxi.png"])
        self.assertEqual(pathlib.Path(kept[0]["path"]).read_bytes(), OLD)

    def test_two_files_in_one_slot_are_both_kept(self) -> None:
        (self.folder / "medias" / "(Wheel) Taxi.jpg").write_bytes(b"older jpg")

        media_placement.place(self.folder, "wheel", NAME, self.source("new.webp"))

        self.assertEqual(sorted(one["file"] for one in self.media_copies()),
                         ["medias/(Wheel) Taxi.jpg", "medias/(Wheel) Taxi.png"])

    def test_a_folder_with_no_game_id_is_written_and_keeps_nothing(self) -> None:
        folder = write_game(self.library, "Anon", vpx=True, info={"Info": {"Title": "Anon"}},
                            medias={"(Wheel) Anon.png": OLD})

        media_placement.place(folder, "wheel", "Anon", self.source())

        self.assertEqual(self.media_copies(), [])
        self.assertNotEqual((folder / "medias" / "(Wheel) Anon.png").read_bytes(), OLD)

    def test_a_slot_with_nothing_in_it_keeps_nothing(self) -> None:
        media_placement.place(self.folder, "flyer", NAME, self.source())

        self.assertEqual(self.media_copies(), [])

    def test_the_copy_is_put_back_by_restoring_it(self) -> None:
        media_placement.place(self.folder, "wheel", NAME, self.source(data=b"new picture"))
        copy_id = self.media_copies()[0]["id"]

        self.assertEqual(self.client.post(f"/backups/{copy_id}/restore").status_code, 200)

        self.assertEqual((self.folder / "medias" / "(Wheel) Taxi.png").read_bytes(), OLD)


class WhenTheCopyCannotBeKeptTests(ReplaceCopyCase):
    def test_the_write_is_refused_and_the_file_is_left_alone(self) -> None:
        refusal = service_errors.BlockedError("the backups folder cannot be written to")
        with patch.object(backup_folder, "for_writing", side_effect=refusal):
            with self.assertRaises(service_errors.BlockedError):
                media_placement.place(self.folder, "wheel", NAME, self.source("new.jpg"))

        self.assertEqual((self.folder / "medias" / "(Wheel) Taxi.png").read_bytes(), OLD)
        self.assertEqual(self.media_copies(), [])

    def test_an_empty_slot_is_written_without_asking_for_the_folder(self) -> None:
        with patch.object(backup_folder, "for_writing") as asked:
            media_placement.place(self.folder, "flyer", NAME, self.source())

        asked.assert_not_called()


if __name__ == "__main__":
    unittest.main()
