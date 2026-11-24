"""A game's Pictures over the API: listed newest first, served, removed and put back."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from io import BytesIO
from unittest.mock import patch

from PIL import Image
from starlette.testclient import TestClient

import httpapi
from common.games import pictures
from common.media import sized_media
from tests.support.library import TempTree, fake_game, write_game

GAME_ID = "Pictures0001"
FOLDER = "Example (Maker 1990)"


def _png(width: int = 40, height: int = 80) -> Image.Image:
    return Image.new("RGB", (width, height), (200, 40, 40))


class PictureRouteTests(TempTree):
    def setUp(self) -> None:
        super().setUp()
        info = {"Info": {"Title": "Example"},
                "vpinfe": {"schema": 2, "id": GAME_ID, "default_table": "t1"},
                "tables": {"t1": {"id": "t1", "filename": "t1.vpx"},
                           "t2": {"id": "t2", "filename": "t2.vpx"}}}
        self.folder = write_game(self.root, FOLDER, info=info, vpx=False,
                                 files={"t1.vpx": b"vpx", "t2.vpx": b"vpx"})
        game = fake_game(self.folder, FOLDER, meta=info)
        for patcher in (patch("common.games.game_repository.catalog",
                              return_value={GAME_ID: game}),
                        patch.object(sized_media, "ROOT", self.root / "sized")):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)

    def _keep(self, when: str, table_id: str = "t1") -> str:
        return pictures.keep(_png(), self.folder, table_id=table_id,
                             taken=datetime.fromisoformat(when)).name

    def _listed(self, query: str = "") -> list[dict]:
        response = self.client.get(f"/games/{GAME_ID}/pictures{query}")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["pictures"]

    def test_a_game_with_no_pictures_lists_none(self) -> None:
        self.assertEqual(self._listed(), [])

    def test_listed_newest_first_with_the_time_and_table_the_picture_says(self) -> None:
        older = self._keep("2026-09-27T20:00:00", "t2")
        newer = self._keep("2026-09-28T21:04:05")

        rows = self._listed()

        self.assertEqual([row["name"] for row in rows], [newer, older])
        self.assertEqual(rows[0]["table_id"], "t1")
        # Kept in this device's local time; listed in UTC.
        self.assertEqual(rows[0]["taken"], datetime.fromisoformat("2026-09-28T21:04:05")
                         .astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"))
        self.assertEqual((rows[0]["width"], rows[0]["height"]), (40, 80))
        self.assertGreater(rows[0]["size_bytes"], 0)
        self.assertTrue(rows[0]["version"])

    def test_a_picture_that_says_no_time_takes_the_files(self) -> None:
        folder = pictures.folder_of(self.folder)
        folder.mkdir()
        _png().save(folder / "dropped in.png")
        os.utime(folder / "dropped in.png", (1_700_000_000, 1_700_000_000))

        (row,) = self._listed()

        self.assertEqual(row["taken"], "2023-11-14T22:13:20Z")
        self.assertEqual(row["table_id"], "")

    def test_a_table_keeps_its_own(self) -> None:
        self._keep("2026-09-27T20:00:00", "t2")
        mine = self._keep("2026-09-28T21:04:05", "t1")

        self.assertEqual([row["name"] for row in self._listed("?table=t1")], [mine])

    def test_only_pngs_directly_in_the_folder_are_pictures(self) -> None:
        kept = self._keep("2026-09-28T21:04:05")
        folder = pictures.folder_of(self.folder)
        (folder / "notes.txt").write_text("not a picture")
        (folder / ".hidden.png").write_bytes(b"")
        (folder / "inner").mkdir()
        _png().save(folder / "inner" / "deeper.png")

        self.assertEqual([row["name"] for row in self._listed()], [kept])

    def test_one_is_served_whole_and_at_a_listed_size(self) -> None:
        name = self._keep("2026-09-28T21:04:05")

        whole = self.client.get(f"/games/{GAME_ID}/pictures/{name}")
        small = self.client.get(f"/games/{GAME_ID}/pictures/{name}?size=256")

        self.assertEqual(whole.status_code, 200)
        self.assertEqual(whole.content, (pictures.folder_of(self.folder) / name).read_bytes())
        self.assertEqual(small.status_code, 200)
        self.assertEqual(small.headers["content-type"], "image/webp")
        self.assertEqual(self.client.get(f"/games/{GAME_ID}/pictures/{name}?size=300")
                         .status_code, 400)

    def test_a_name_that_is_not_a_picture_is_refused_and_a_missing_one_not_found(self) -> None:
        write_game(self.root, FOLDER, files={"secret.png": b"elsewhere"}, vpx=False)

        for name in ("..%2Fsecret.png", "notes.txt", ".hidden.png"):
            with self.subTest(name=name):
                answer = self.client.get(f"/games/{GAME_ID}/pictures/{name}")
                self.assertIn(answer.status_code, (400, 404), answer.text)
                self.assertNotEqual(answer.content, b"elsewhere")
        self.assertEqual(self.client.get(f"/games/{GAME_ID}/pictures/notes.txt")
                         .status_code, 400)
        self.assertEqual(self.client.get(f"/games/{GAME_ID}/pictures/gone.png")
                         .status_code, 404)

    def test_removed_and_put_back_under_its_own_name_as_it_was(self) -> None:
        name = self._keep("2026-09-28T21:04:05")
        path = pictures.folder_of(self.folder) / name
        held = path.read_bytes()

        removed = self.client.delete(f"/games/{GAME_ID}/pictures/{name}")
        self.assertEqual(removed.status_code, 200, removed.text)
        self.assertEqual(removed.json(), {"removed": name})
        self.assertFalse(path.exists())

        back = self.client.put(f"/games/{GAME_ID}/pictures/{name}",
                               files={"file": (name, held)})
        self.assertEqual(back.status_code, 200, back.text)
        self.assertEqual(path.read_bytes(), held)
        self.assertEqual(back.json()["table_id"], "t1")
        self.assertEqual([row["name"] for row in self._listed()], [name])

    def test_a_name_already_taken_is_a_conflict_and_the_file_is_left(self) -> None:
        name = self._keep("2026-09-28T21:04:05")
        path = pictures.folder_of(self.folder) / name
        before = path.read_bytes()
        other = BytesIO()
        _png(10, 10).save(other, "PNG")

        answer = self.client.put(f"/games/{GAME_ID}/pictures/{name}",
                                 files={"file": (name, other.getvalue())})

        self.assertEqual(answer.status_code, 409, answer.text)
        self.assertEqual(path.read_bytes(), before)

    def test_only_a_png_is_put(self) -> None:
        jpeg = BytesIO()
        _png().save(jpeg, "JPEG")

        for data in (jpeg.getvalue(), b"not an image"):
            with self.subTest(data=data[:4]):
                answer = self.client.put(f"/games/{GAME_ID}/pictures/new.png",
                                         files={"file": ("new.png", data)})
                self.assertEqual(answer.status_code, 400, answer.text)
        self.assertFalse((pictures.folder_of(self.folder) / "new.png").exists())
