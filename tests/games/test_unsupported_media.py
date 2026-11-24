"""A file named for a kind in an extension the kind cannot read is listed as Unsupported."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from common.games import media_lens
from tests.support.library import TempTree, fake_game, game_info, write_game

GAME_ID = "Unsup000001"
FOLDER = "Medieval Madness (Williams 1997)"
TABLES = {"tbl0000001": {"id": "tbl0000001", "filename": f"{FOLDER}.vpx"}}


class UnsupportedRowTests(TempTree):
    def listing(self, medias: dict[str, bytes], files: dict[str, bytes] | None = None
                ) -> list[dict]:
        info = game_info("Medieval Madness", vps_id="vps-1", game_id=GAME_ID, tables=TABLES)
        folder = write_game(self.root, FOLDER, info=info, medias=medias, files=files)
        catalog = {GAME_ID: fake_game(folder, FOLDER, meta=info)}
        with patch("common.games.game_repository.catalog", return_value=catalog):
            return [row for row in media_lens.listing()["media"]
                    if row.get("via") == media_lens.UNSUPPORTED]

    def test_a_fixed_name_in_a_convertible_extension_is_one(self) -> None:
        found = self.listing({"wheel.tga": b"tga"})

        self.assertEqual([(row["kind"], row["path"], row["format"]) for row in found],
                         [("wheel", "medias/wheel.tga", "TGA")])
        self.assertTrue(found[0]["present"])
        self.assertEqual(found[0]["serves"], 0)

    def test_a_token_name_is_one_in_a_family_of_the_kinds_class(self) -> None:
        found = self.listing({"(Wheel) Elsewhere.tga": b"tga", "(Audio) Elsewhere.flac": b"fl",
                              "realdmd.avi": b"avi"})

        self.assertEqual({(row["kind"], row["file"]) for row in found},
                         {("wheel", "(Wheel) Elsewhere.tga"),
                          ("audio", "(Audio) Elsewhere.flac"),
                          ("real_dmd_video", "realdmd.avi")})

    def test_a_name_that_is_not_ours_is_not_one(self) -> None:
        self.assertEqual(self.listing({"notes.tga": b"x", "holiday.avi": b"x"}), [])

    def test_an_extension_the_kind_reads_is_not_one(self) -> None:
        self.assertEqual(self.listing({"wheel.png": b"\x89PNGwheel"}), [])

    def test_an_extension_that_does_not_convert_is_not_one(self) -> None:
        self.assertEqual(self.listing({"wheel.xyz": b"x"}), [])

    def test_a_picture_extension_on_a_sound_kind_is_not_one(self) -> None:
        self.assertEqual(self.listing({"(Audio) Elsewhere.tga": b"x"}), [])

    def test_a_file_inside_a_set_is_not_considered(self) -> None:
        self.assertEqual(self.listing({"sets/one/wheel.tga": b"x"}), [])

    def test_one_in_the_folder_root_is_found_too(self) -> None:
        found = self.listing({}, files={"wheel.tga": b"tga"})

        self.assertEqual([row["path"] for row in found], ["wheel.tga"])


class VideoCodecTests(TempTree):
    """A video a browser plays carries the codec its header says; the lens does not read the
    header of one VPinFE plays itself."""

    def codecs(self, medias: dict[str, bytes]) -> dict[str, str | None]:
        info = game_info("Medieval Madness", vps_id="vps-1", game_id=GAME_ID, tables=TABLES)
        folder = write_game(self.root, FOLDER, info=info, medias=medias)
        catalog = {GAME_ID: fake_game(folder, FOLDER, meta=info)}
        with patch("common.games.game_repository.catalog", return_value=catalog), \
                patch.object(media_lens.media_probe, "probe",
                             return_value={"video_codec": "vp9"}):
            return {row["file"]: row.get("video_codec") for row in media_lens.listing()["media"]
                    if row.get("present") and row["file"].endswith((".webm", ".mp4"))}

    def test_a_backglass_video_carries_its_codec(self) -> None:
        self.assertEqual(self.codecs({"bg.webm": b"x"}), {"bg.webm": "vp9"})

    def test_a_real_dmd_video_is_not_read_for_one(self) -> None:
        self.assertEqual(self.codecs({"realdmd.mp4": b"x"}), {"realdmd.mp4": None})


if __name__ == "__main__":
    unittest.main()
