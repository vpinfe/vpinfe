"""A ROM name names a folder inside the game, so it is one plain name and nothing else."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from starlette.testclient import TestClient

import httpapi
from common import service_errors
from common.uploads import asset_import_service, upload_ops
from common.uploads.asset_analyzer_service import analyze_path
from common.uploads.asset_import_service import build_import_plan, folder_for
from tests.support.library import TempTree
from tests.support.uploads import make_zip

NOT_PLAIN = ("..", ".", "../out", "a/b", "a\\b", "/abs", "..\\out", "ok/..", "a\0b")


class FolderForTests(unittest.TestCase):
    def test_a_name_that_is_not_plain_is_refused(self) -> None:
        for kind in ("altsound", "altcolor_serum", "altcolor_vni"):
            for name in NOT_PLAIN:
                with self.subTest(kind=kind, name=name), self.assertRaises(ValueError):
                    folder_for(kind, "/g", name)

    def test_a_plain_name_still_names_its_folder(self) -> None:
        self.assertEqual(Path("/g/altsound/cc_13"), folder_for("altsound", "/g", "cc_13"))
        self.assertEqual(Path("/g/altsound/cc_13"), folder_for("altsound", "/g", " cc_13 "))

    def test_a_kind_with_no_rom_in_its_folder_does_not_look_at_the_name(self) -> None:
        self.assertEqual(Path("/g/user"), folder_for("user", "/g", "../x"))
        self.assertEqual(Path("/g/pupvideos"), folder_for("pup_pack", "/g", "../x"))

    def test_no_name_is_still_none_for_a_kind_that_needs_one(self) -> None:
        self.assertIsNone(folder_for("altsound", "/g", ""))


class PlanTests(TempTree):
    def setUp(self) -> None:
        super().setUp()
        self.game = self.root / "Foo (Bar 1999)"
        self.game.mkdir()
        (self.game / "Foo.vpx").write_bytes(b"x")
        self.zip = self.root / "drop.zip"
        make_zip(self.zip, ["x.crz"])

    def test_a_plan_with_a_name_that_is_not_plain_is_refused(self) -> None:
        for name in NOT_PLAIN:
            with self.subTest(name=name), self.assertRaises(ValueError):
                build_import_plan(analyze_path(self.zip), game_dir=self.game, rom_name=name)

    def test_a_plain_name_is_planned_under_its_folder(self) -> None:
        plan = build_import_plan(analyze_path(self.zip), game_dir=self.game, rom_name="mm_105")

        self.assertEqual([self.game / "serum" / "mm_105" / "mm_105.cRZ"],
                         [Path(item.destination) for item in plan.items])


class _Here(TempTree):
    def setUp(self) -> None:
        super().setUp()
        self.game = self.root / "games" / "Foo (Bar 1999)"
        self.game.mkdir(parents=True)
        (self.game / "Foo.vpx").write_bytes(b"x")
        self.incoming = self.root / "incoming"
        (self.incoming / "altsound" / "1").mkdir(parents=True)
        (self.incoming / "altsound" / "altsound.csv").write_bytes(b"x" * 16)
        (self.incoming / "altsound" / "1" / "x.ogg").write_bytes(b"x" * 16)
        roots = [{"path": str(self.root.resolve()), "name": "root", "source": "library"}]
        patcher = mock.patch("common.games.media_browse.roots", return_value=roots)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _begun(self) -> str:
        upload = upload_ops.begin_from(str(self.incoming))["id"]
        self.addCleanup(upload_ops.abort, upload)
        return upload

    def _ask(self, rom_name: str) -> dict:
        return {"game_dir": str(self.game), "rom_name": rom_name}

    def _outside(self) -> list[Path]:
        return sorted(p for p in self.root.rglob("altsound.csv")
                      if self.game not in p.parents and self.incoming not in p.parents)


class OperationTests(_Here):
    def test_planning_with_a_name_that_is_not_plain_is_refused(self) -> None:
        upload = self._begun()
        for name in NOT_PLAIN:
            with self.subTest(name=name), self.assertRaises(service_errors.RefusedError):
                upload_ops.plan_for(upload, self._ask(name))

    def test_importing_with_a_name_that_is_not_plain_writes_nothing(self) -> None:
        upload = self._begun()
        with mock.patch.object(asset_import_service, "refresh_game"):
            for name in NOT_PLAIN:
                with self.subTest(name=name), self.assertRaises(service_errors.RefusedError):
                    upload_ops.execute(upload, self._ask(name))

        self.assertEqual([], self._outside())
        self.assertEqual([], list(self.game.rglob("altsound.csv")))

    def test_a_plain_name_is_planned_inside_the_game(self) -> None:
        plan = upload_ops.plan_for(self._begun(), self._ask("cc_13"))

        self.assertEqual(["altsound"], [item["kind"] for item in plan["items"]])
        self.assertEqual(str(self.game / "altsound" / "cc_13"), plan["items"][0]["destination"])

    def test_a_plain_name_is_imported_inside_the_game(self) -> None:
        with mock.patch.object(asset_import_service, "refresh_game"):
            upload_ops.execute(self._begun(), self._ask("cc_13"))

        self.assertTrue((self.game / "altsound" / "cc_13" / "altsound.csv").is_file())


class RouteTests(_Here):
    def test_the_plan_route_refuses_a_name_that_is_not_plain(self) -> None:
        upload = self._begun()
        client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)

        for name in NOT_PLAIN:
            with self.subTest(name=name):
                response = client.post(f"/uploads/{upload}/plan", json=self._ask(name))
                self.assertEqual(400, response.status_code, response.text)

        self.assertEqual([], self._outside())


if __name__ == "__main__":
    unittest.main()
