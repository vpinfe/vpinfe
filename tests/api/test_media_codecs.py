"""Which codec a video slot's file holds, as the API says it."""

from __future__ import annotations

import struct
import unittest
from unittest.mock import patch

from starlette.testclient import TestClient

import httpapi
from tests.support.library import TempTree, fake_game, write_game

GAME_ID = "Codecs0001"
FOLDER = "Space Station (Williams 1987)"
TABLE = f"{FOLDER}.vpx"

INFO = {
    "Info": {"Name": "Space Station"},
    "VPinFE": {"game_id": GAME_ID},
    "tables": {"tbl0000001": {"id": "tbl0000001", "filename": TABLE}},
}


def _box(kind: bytes, body: bytes) -> bytes:
    return struct.pack(">I4s", 8 + len(body), kind) + body


def _video(entry: bytes) -> bytes:
    """A `.mp4` whose one track is described by a sample entry of type `entry`."""
    stsd = _box(b"stsd", bytes(4) + struct.pack(">I", 1) + _box(entry, bytes(78)))
    trak = _box(b"trak", _box(b"mdia", _box(b"minf", _box(b"stbl", stsd))))
    return _box(b"ftyp", b"isom" + bytes(4)) + _box(b"moov", trak)


class MediaCodecTests(TempTree):
    def setUp(self) -> None:
        super().setUp()
        folder = write_game(self.root, FOLDER, info=INFO, vpx=False,
                            files={TABLE: b"vpx"},
                            medias={"table.mp4": _video(b"avc1"), "bg.mp4": _video(b"vp09"),
                                    "wheel.png": b"\x89PNG"})
        game = fake_game(folder, FOLDER, meta=INFO)
        self.enterContext(patch("common.games.game_repository.catalog",
                                return_value={GAME_ID: game}))
        self.client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)

    def _get(self, path: str) -> dict:
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_asked_for_them_the_list_names_each_video_s_codec(self) -> None:
        media = self._get(f"/games/{GAME_ID}/media?codecs=true")["media"]

        self.assertEqual((media["playfield_video"]["video_codec"],
                          media["backglass_video"]["video_codec"]),
                         ("h264", "vp9"))
        self.assertIsNone(media["wheel"]["video_codec"])

    def test_not_asked_the_list_reads_no_header(self) -> None:
        with patch("common.games.media_probe.probe") as probe:
            media = self._get(f"/games/{GAME_ID}/media")["media"]

        probe.assert_not_called()
        self.assertIsNone(media["playfield_video"]["video_codec"])

    def test_a_table_s_list_is_asked_the_same_way(self) -> None:
        media = self._get(f"/games/{GAME_ID}/tables/tbl0000001/media?codecs=true")["media"]

        self.assertEqual(media["playfield_video"]["video_codec"], "h264")

    def test_a_slot_s_detail_names_it(self) -> None:
        detail = self._get(f"/games/{GAME_ID}/media/backglass_video/detail")

        self.assertEqual(detail["video_codec"], "vp9")


if __name__ == "__main__":
    unittest.main()
