"""A file VPX finds by name, placed in a game's folder and taken out again, over HTTP."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from starlette.datastructures import UploadFile
from starlette.testclient import TestClient

import httpapi
from common.games import asset_origin
from common.i18n import t
from tests.support.library import TempTree, fake_game, write_game
from tests.support.skips import needs_posix_permissions

GAME_ID = "Assets0002"
FOLDER = "Attack from Mars (Bally 1995)"


class AssetWrites(TempTree):
    def setUp(self) -> None:
        super().setUp()
        info = {"Info": {"Name": "Attack from Mars"}, "VPinFE": {"game_id": GAME_ID},
                "tables": {"a1": {"id": "a1", "filename": f"{FOLDER}.vpx"},
                           "vr": {"id": "vr", "filename": "AFM VR.vpx"}}}
        self.folder = write_game(self.root, FOLDER, info=info,
                                 files={"AFM VR.vpx": b"vpx",
                                        "AFM VR.directb2s": b"old backglass",
                                        "pupvideos/Backglass/1.mp4": b"mp4",
                                        "extras/notes.txt": b"notes"})
        game = fake_game(self.folder, FOLDER, meta=info)
        patcher = patch("common.games.game_repository.catalog",
                        return_value={GAME_ID: game})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)

    def _put(self, kind: str, name: str, data: bytes, table: str = ""):
        where = f"/tables/{table}" if table else ""
        return self.client.put(f"/games/{GAME_ID}{where}/assets/{kind}",
                               files={"file": (name, data)})

    def test_a_kind_with_a_shared_name_offers_it_and_each_table(self) -> None:
        body = self.client.get(f"/games/{GAME_ID}/assets/backglass/placements").json()

        self.assertEqual([("", FOLDER, []), ("vr", "AFM VR", ["AFM VR.directb2s"])],
                         [(one["table"], one["base"], one["displaces"])
                          for one in body["placements"]])
        self.assertEqual([".directb2s"], body["extensions"])

    def test_a_table_named_as_its_folder_in_another_case_is_not_offered_either(
            self) -> None:
        lower = f"{FOLDER.lower()}.vpx"
        info = {"Info": {"Name": "Attack from Mars"}, "VPinFE": {"game_id": GAME_ID},
                "tables": {"a3": {"id": "a3", "filename": lower}}}
        folder = write_game(self.root / "other", FOLDER, info=info, vpx=False,
                            files={lower: b"vpx"})
        with patch("common.games.game_repository.catalog",
                   return_value={GAME_ID: fake_game(folder, FOLDER, meta=info)}):
            body = self.client.get(f"/games/{GAME_ID}/assets/backglass/placements").json()

        self.assertEqual([""], [one["table"] for one in body["placements"]])

    def test_a_file_named_as_the_folder_is_one_row_serving_every_table(self) -> None:
        (self.folder / f"{FOLDER}.ini").write_bytes(b"[Player]")

        rows = self.client.get("/assets", params={"game": GAME_ID, "kind": "ini"}).json()

        self.assertEqual([("", f"{FOLDER}.ini", "game", 2)],
                         [(row["table"], row["file"], row["binding"], row["serves"])
                          for row in rows["assets"]])

    def test_a_shared_file_serves_only_the_tables_without_one_of_their_own(self) -> None:
        (self.folder / f"{FOLDER}.directb2s").write_bytes(b"shared backglass")

        rows = self.client.get("/assets", params={"game": GAME_ID,
                                                  "kind": "backglass"}).json()

        self.assertEqual([("", "game", 1), ("vr", "table", 1)],
                         [(row["table"], row["binding"], row["serves"])
                          for row in rows["assets"]])

    def test_a_shared_file_every_table_has_its_own_beside_serves_none(self) -> None:
        info = {"Info": {"Name": "Attack from Mars"}, "VPinFE": {"game_id": GAME_ID},
                "tables": {"a2": {"id": "a2", "filename": "AFM Night.vpx"},
                           "vr": {"id": "vr", "filename": "AFM VR.vpx"}}}
        folder = write_game(self.root / "covered", FOLDER, info=info, vpx=False,
                            files={"AFM Night.vpx": b"vpx", "AFM VR.vpx": b"vpx",
                                   "AFM Night.directb2s": b"night",
                                   "AFM VR.directb2s": b"vr",
                                   f"{FOLDER}.directb2s": b"shared"})
        with patch("common.games.game_repository.catalog",
                   return_value={GAME_ID: fake_game(folder, FOLDER, meta=info)}):
            rows = self.client.get("/assets", params={"game": GAME_ID,
                                                      "kind": "backglass"}).json()

        self.assertEqual([("", "game", 0), ("a2", "table", 1), ("vr", "table", 1)],
                         [(row["table"], row["binding"], row["serves"])
                          for row in rows["assets"]])

    def test_a_table_with_no_id_is_not_counted_as_having_its_own_file(self) -> None:
        info = {"Info": {"Name": "Attack from Mars"}, "VPinFE": {"game_id": GAME_ID},
                "tables": {"a2": {"id": "a2", "filename": "AFM Night.vpx"},
                           "no": {"id": "", "filename": "AFM VR.vpx"}}}
        folder = write_game(self.root / "unnamed", FOLDER, info=info, vpx=False,
                            files={"AFM Night.vpx": b"vpx", "AFM VR.vpx": b"vpx",
                                   "AFM VR.directb2s": b"vr",
                                   f"{FOLDER}.directb2s": b"shared"})
        with patch("common.games.game_repository.catalog",
                   return_value={GAME_ID: fake_game(folder, FOLDER, meta=info)}):
            rows = self.client.get("/assets", params={"game": GAME_ID,
                                                      "kind": "backglass"}).json()

        self.assertEqual([1], [row["serves"] for row in rows["assets"]
                               if row["binding"] == "game"])

    def test_a_point_of_view_is_offered_only_for_a_table(self) -> None:
        body = self.client.get(f"/games/{GAME_ID}/assets/pov/placements").json()

        self.assertEqual({"a1", "vr"}, {one["table"] for one in body["placements"]})

    def test_a_table_s_file_takes_the_table_s_name_and_says_what_it_replaced(self) -> None:
        said = self._put("backglass", "anything.directb2s", b"new backglass", "vr").json()

        self.assertEqual(("AFM VR.directb2s", ["AFM VR.directb2s"]),
                         (said["written"], said["displaced"]))
        self.assertEqual(b"new backglass", (self.folder / "AFM VR.directb2s").read_bytes())

    def test_a_shared_file_takes_the_folder_s_name(self) -> None:
        said = self._put("ini", "settings.ini", b"[Player]").json()

        self.assertEqual((f"{FOLDER}.ini", []), (said["written"], said["displaced"]))

    def test_a_placed_file_is_recorded_as_the_user_s(self) -> None:
        self._put("ini", "settings.ini", b"[Player]", "vr")

        self.assertEqual("user", asset_origin.origin_of(
            asset_origin.ledger(self.folder), self.folder, self.folder / "AFM VR.ini"))

    def test_a_shared_point_of_view_is_refused(self) -> None:
        self.assertEqual(400, self._put("pov", "view.pov", b"pov").status_code)

    def test_a_file_of_another_kind_is_refused(self) -> None:
        self.assertEqual(400, self._put("backglass", "art.png", b"png", "vr").status_code)
        self.assertFalse((self.folder / "AFM VR.png").exists())

    @needs_posix_permissions
    def test_a_folder_it_may_not_write_is_refused_with_why(self) -> None:
        self.folder.chmod(0o555)
        self.addCleanup(self.folder.chmod, 0o755)

        answer = self._put("backglass", "anything.directb2s", b"new backglass")

        self.assertEqual(
            (409, "conflict", t("error.path.read_only", name=self.folder.name)),
            (answer.status_code, answer.json()["error"]["code"],
             answer.json()["error"]["message"]))
        self.assertEqual(b"old backglass", (self.folder / "AFM VR.directb2s").read_bytes())

    def test_what_a_write_would_replace_is_asked_first(self) -> None:
        body = self.client.get(f"/games/{GAME_ID}/assets/backglass/displaced",
                               params={"filename": "b.directb2s", "table": "vr"}).json()

        self.assertEqual(["AFM VR.directb2s"], body["displaced"])

    def test_a_file_on_this_machine_is_copied_in(self) -> None:
        elsewhere = self.root / "downloads" / "AFM.pov"
        elsewhere.parent.mkdir()
        elsewhere.write_bytes(b"pov")
        with patch("common.games.media_browse.within_roots", return_value=elsewhere):
            said = self.client.post(f"/games/{GAME_ID}/assets/pov/import",
                                    json={"path": str(elsewhere), "table": "vr"}).json()

        self.assertEqual("AFM VR.pov", said["written"])
        self.assertTrue(elsewhere.exists(), "copied, not moved")

    def test_the_bytes_of_a_file_go_in_as_the_path_of_it_does(self) -> None:
        """A picker reads the file here and sends it; the result is what importing the
        path gave, so choosing between the two changes nothing the person sees."""
        elsewhere = self.root / "downloads" / "AFM.pov"
        elsewhere.parent.mkdir()
        elsewhere.write_bytes(b"pov")
        landed = self.folder / "AFM VR.pov"
        outcomes = []
        for how in ("import", "bytes"):
            with patch("common.games.media_browse.within_roots", return_value=elsewhere):
                if how == "import":
                    said = self.client.post(f"/games/{GAME_ID}/assets/pov/import",
                                            json={"path": str(elsewhere), "table": "vr"})
                else:
                    said = self._put("pov", elsewhere.name, elsewhere.read_bytes(), "vr")
            outcomes.append((said.status_code, said.json(), landed.read_bytes(),
                             asset_origin.origin_of(asset_origin.ledger(self.folder),
                                                    self.folder, landed)))
            landed.unlink()

        self.assertEqual(outcomes[0], outcomes[1])

    def test_a_large_upload_is_written_in_pieces_rather_than_read_whole(self) -> None:
        size = 3 * 1024 * 1024
        reads: list[int] = []
        real = UploadFile.read

        async def counted(upload, size: int = -1):
            reads.append(size)
            return await real(upload, size)

        with patch.object(UploadFile, "read", counted):
            said = self._put("ini", "settings.ini", b"x" * size, "vr")

        self.assertEqual(200, said.status_code)
        self.assertEqual(size, (self.folder / "AFM VR.ini").stat().st_size)
        self.assertEqual([], reads, "the whole body was read into memory")

    def test_removing_takes_the_one_file(self) -> None:
        response = self.client.delete(f"/games/{GAME_ID}/assets",
                                      params={"path": "AFM VR.directb2s"})

        self.assertEqual(["AFM VR.directb2s"], response.json()["removed"])
        self.assertFalse((self.folder / "AFM VR.directb2s").exists())
        self.assertTrue((self.folder / "AFM VR.vpx").exists())

    def test_a_backglass_placed_or_removed_settles_the_game_s_order(self) -> None:
        with patch("common.games.backglass_order.settle") as settle:
            self._put("backglass", "anything.directb2s", b"new backglass", "vr")
            self.client.delete(f"/games/{GAME_ID}/assets", params={"path": "AFM VR.directb2s"})

        self.assertEqual([((GAME_ID,),), ((GAME_ID,),)], settle.call_args_list)

    def test_a_file_that_is_not_a_backglass_settles_nothing(self) -> None:
        with patch("common.games.backglass_order.settle") as settle:
            self._put("ini", "settings.ini", b"[Player]")
            self.client.delete(f"/games/{GAME_ID}/assets", params={"path": "extras/notes.txt"})

        settle.assert_not_called()

    def test_removing_a_table_or_a_path_out_of_the_folder_is_refused(self) -> None:
        for path in ("AFM VR.vpx", "../outside.ini"):
            with self.subTest(path=path):
                self.assertEqual(400, self.client.delete(
                    f"/games/{GAME_ID}/assets", params={"path": path}).status_code)
        self.assertTrue(Path(self.folder / "AFM VR.vpx").exists())

    def test_a_kind_s_folder_goes_whole(self) -> None:
        response = self.client.delete(f"/games/{GAME_ID}/assets", params={"path": "pupvideos"})

        self.assertEqual(["pupvideos"], response.json()["removed"])
        self.assertFalse((self.folder / "pupvideos").exists())

    def test_any_other_folder_is_refused(self) -> None:
        for path in ("extras", "."):
            with self.subTest(path=path):
                self.assertEqual(400, self.client.delete(
                    f"/games/{GAME_ID}/assets", params={"path": path}).status_code)
        self.assertTrue((self.folder / "extras" / "notes.txt").exists())
