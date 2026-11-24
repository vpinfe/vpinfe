"""The copies kept of a game's scripts, settings and notes, beside the media copies.

What is worth pinning: the two areas never read each other's files, an asset copy is listed,
put back and deleted like a media copy, and a move of the backups folder carries both.
"""

from __future__ import annotations

import pathlib
import time
from unittest.mock import patch

from common.backups import copies, places, relocate
from common.backups import folder as backup_folder
from common.backups import media as backup_media
from common.games import game_repository, media_ops
from common.games.game_identity import game_id as game_id_of
from common.runtime import jobs
from tests.api.test_backups import BackupsCase
from tests.support.library import write_game

GAME = "id-Taxi"
NAME = "Taxi"
SCRIPT = "Taxi.vbs"


class AssetCase(BackupsCase):
    def setUp(self) -> None:
        super().setUp()
        self.folder = write_game(
            self.library, NAME, info={"Info": {"Title": NAME},
                                      "vpinfe": {"schema": 2, "game_id": GAME}},
            files={SCRIPT: b"Option Explicit\r\n", "Taxi.ini": b"[Player]\r\n"})
        for patcher in (
                patch.object(game_repository, "catalog",
                             side_effect=lambda: {game_id_of(game): game
                                                  for game in self.library_games()}),
                patch.object(media_ops, "reread")):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.root = backup_folder.current()

    def keep(self, relative: str = SCRIPT, reason: str = copies.REASON_EDIT,
             area: str = backup_media.ASSETS) -> pathlib.Path:
        return backup_media.keep(self.root, GAME, self.folder / relative, relative, reason,
                                 area=area)

    def assets(self) -> list[dict]:
        return [one for one in self.listing() if one["kind"] == copies.KIND_ASSET]


class ListingTests(AssetCase):
    def test_a_copy_sits_under_the_assets_area_and_names_the_edit(self) -> None:
        kept = self.keep()

        self.assertEqual(kept.parent, self.root / "assets" / GAME)
        self.assertRegex(kept.name, r"^Taxi\.vbs\.vpinfe-\d{8}T\d{6}Z-edit$")
        self.assertEqual(kept.read_bytes(), b"Option Explicit\r\n")

    def test_it_is_listed_as_an_asset_copy_of_the_game(self) -> None:
        self.keep()

        (found,) = self.assets()

        self.assertEqual((found["subject_kind"], found["subject_id"], found["subject_name"]),
                         ("game", GAME, NAME))
        self.assertEqual((found["file"], found["reason"], found["gone"]),
                         (SCRIPT, "edit", False))

    def test_it_is_listed_with_the_game_it_is_a_copy_of(self) -> None:
        self.keep()

        listed = self.client.get(f"/backups?subject_kind=game&subject_id={GAME}").json()

        self.assertEqual([one["kind"] for one in listed["copies"]], ["asset"])

    def test_two_edits_in_the_same_second_keep_two_copies(self) -> None:
        self.keep()
        self.keep()

        self.assertEqual(len(self.assets()), 2)

    def test_a_media_copy_is_not_an_asset_and_an_asset_is_not_media(self) -> None:
        asset = self.keep()
        media = self.keep("Taxi.ini", copies.REASON_TURN, backup_media.MEDIA)

        kinds = {one["path"]: one["kind"] for one in self.listing()}

        self.assertEqual(kinds, {str(asset): "asset", str(media): "media"})

    def test_an_id_that_names_a_file_in_the_other_area_reaches_nothing(self) -> None:
        asset = self.keep()

        forged = copies.make_id(copies.PLACE_MEDIA, asset)

        self.assertIsNone(places.find(forged))
        self.assertEqual(self.client.post(f"/backups/{forged}/restore").status_code, 404)

    def test_an_asset_id_for_a_media_copy_reaches_nothing(self) -> None:
        media = self.keep("Taxi.ini", copies.REASON_TURN, backup_media.MEDIA)

        forged = copies.make_id(copies.PLACE_ASSET, media)

        self.assertIsNone(places.find(forged))
        self.assertEqual(self.client.delete(f"/backups/{forged}").status_code, 404)
        self.assertTrue(media.is_file())

    def test_a_game_that_is_gone_says_so(self) -> None:
        self.keep()
        for path in self.folder.iterdir():
            path.unlink()
        self.folder.rmdir()

        (found,) = self.assets()

        self.assertTrue(found["gone"])


class PuttingBackTests(AssetCase):
    def test_putting_it_back_restores_the_file_and_keeps_what_it_replaced(self) -> None:
        kept = self.keep()
        (self.folder / SCRIPT).write_bytes(b"Option Explicit\r\n' edited\r\n")

        response = self.client.post(f"/backups/{self.id_of(kept)}/restore")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual((self.folder / SCRIPT).read_bytes(), b"Option Explicit\r\n")
        replaced = [one for one in self.assets() if one["reason"] == "restore"]
        self.assertEqual(len(replaced), 1)
        self.assertEqual(pathlib.Path(replaced[0]["path"]).read_bytes(),
                         b"Option Explicit\r\n' edited\r\n")
        self.assertEqual([one["kind"] for one in response.json()["kept"]], ["asset"])

    def test_a_file_that_is_no_longer_there_is_put_back_without_a_second_copy(self) -> None:
        kept = self.keep()
        (self.folder / SCRIPT).unlink()

        response = self.client.post(f"/backups/{self.id_of(kept)}/restore")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual((self.folder / SCRIPT).read_bytes(), b"Option Explicit\r\n")
        self.assertEqual(response.json()["kept"], [])

    def test_a_backglass_file_put_back_settles_the_order_of_its_game(self) -> None:
        (self.folder / "Taxi.directb2s").write_bytes(b"first")
        kept = self.keep("Taxi.directb2s")
        (self.folder / "Taxi.directb2s").write_bytes(b"second")

        with patch("common.games.backglass_order.settle") as settle:
            self.client.post(f"/backups/{self.id_of(kept)}/restore")

        settle.assert_called_once_with(GAME)

    def test_a_file_that_is_not_a_backglass_put_back_settles_nothing(self) -> None:
        kept = self.keep()

        with patch("common.games.backglass_order.settle") as settle:
            self.client.post(f"/backups/{self.id_of(kept)}/restore")

        settle.assert_not_called()

    def test_a_copy_in_a_folder_inside_the_game_goes_back_to_that_folder(self) -> None:
        (self.folder / "docs").mkdir()
        (self.folder / "docs" / "notes.txt").write_bytes(b"first")
        kept = self.keep("docs/notes.txt")
        (self.folder / "docs" / "notes.txt").write_bytes(b"second")

        self.client.post(f"/backups/{self.id_of(kept)}/restore")

        self.assertEqual((self.folder / "docs" / "notes.txt").read_bytes(), b"first")


class DeletingTests(AssetCase):
    def test_deleting_the_last_copy_removes_the_folders_it_leaves_empty(self) -> None:
        kept = self.keep()

        response = self.client.delete(f"/backups/{self.id_of(kept)}")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.listing(), [])
        self.assertFalse((self.root / "assets" / GAME).exists())

    def test_deleting_an_asset_copy_leaves_the_media_area_alone(self) -> None:
        asset = self.keep()
        media = self.keep("Taxi.ini", copies.REASON_TURN, backup_media.MEDIA)

        self.client.delete(f"/backups/{self.id_of(asset)}")

        self.assertTrue(media.is_file())
        self.assertFalse((self.root / "assets" / GAME).exists())


class MovingTests(AssetCase):
    def test_a_move_of_the_backups_folder_carries_the_asset_copies(self) -> None:
        kept = self.keep()
        self.keep("Taxi.ini", copies.REASON_TURN, backup_media.MEDIA)
        bigger = self.library.parent / "bigger"
        bigger.mkdir()

        found = relocate.plan(str(bigger))
        job = relocate.begin(str(bigger), lambda: None)
        while job.state == jobs.RUNNING:
            time.sleep(0.01)

        self.assertEqual(found.moving, 2)
        self.assertEqual(job.state, jobs.DONE, job.error)
        assert isinstance(job.result, dict)
        self.assertEqual(job.result["moved"], 2)
        self.assertTrue((bigger / kept.relative_to(self.root)).is_file())
        self.assertFalse((self.root / "assets").exists())
        self.assertFalse((self.root / "media").exists())
