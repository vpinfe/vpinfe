"""Going back to 2.x: what it would put back, the quit that asks for it, and the exit that
carries it out."""

from __future__ import annotations

import json
import pathlib
import unittest
from tempfile import TemporaryDirectory
from unittest.mock import patch

from common.backups import going_back
from common.devices import device_registry
from common.host import system_actions
from common.kept_copies import backup_path
from common.runtime import lifecycle
from tests.api.test_backups import NEWER, OLD, BackupsCase
from tests.support.library import own_registry

ASKED = lifecycle.VPINFE, lifecycle.STOP
SCHEMA_2 = {"Info": {"Title": "now"}, "vpinfe": {"schema": 2, "game_id": "id-Taxi"}}
OLD_COLLECTIONS = "[Favorites]\nvpsids = a\n"


class GoingBackCase(BackupsCase):
    def setUp(self) -> None:
        super().setUp()
        kept = TemporaryDirectory()
        self.addCleanup(kept.cleanup)
        own_registry(self, pathlib.Path(kept.name) / "devices.json")
        patcher = patch.object(going_back, "CONFIG_DIR", self.config)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(lifecycle.reset_for_tests)
        self.quit: list[lifecycle.Request] = []
        lifecycle.register_performer(*ASKED, self.quit.append)

    def requested(self) -> bool:
        return (self.config / going_back.REQUEST_NAME).exists()

    def old_game(self, name: str = "Taxi") -> pathlib.Path:
        folder = self.game(name)
        self.kept(folder, OLD)
        return folder

    def old_collections(self) -> None:
        path = pathlib.Path(backup_path(self.config / "collections.ini", OLD))
        path.write_text(OLD_COLLECTIONS, encoding="utf-8")


class Plan(GoingBackCase):
    def test_it_counts_the_games_that_hold_a_2x_copy(self) -> None:
        self.old_game("Taxi")
        self.old_game("Eight Ball")
        self.game("Cue Ball")

        found = self.client.get("/backups/going-back").json()

        self.assertEqual({"games": 2, "collections": False}, found)

    def test_a_copy_a_newer_build_wrote_is_not_one_to_go_back_to(self) -> None:
        folder = self.game()
        self.kept(folder, NEWER, info=SCHEMA_2)

        self.assertEqual(0, self.client.get("/backups/going-back").json()["games"])

    def test_it_says_when_the_collections_have_a_2x_copy(self) -> None:
        self.old_collections()

        self.assertTrue(self.client.get("/backups/going-back").json()["collections"])


class Request(GoingBackCase):
    def test_it_asks_for_it_and_quits(self) -> None:
        self.old_game()

        answer = self.client.post("/backups/going-back")

        self.assertEqual(200, answer.status_code)
        self.assertEqual({"games": 1, "collections": False}, answer.json())
        self.assertTrue(self.requested())
        self.assertEqual(1, len(self.quit))

    def test_with_no_2x_copy_it_is_refused_and_nothing_quits(self) -> None:
        self.game()

        answer = self.client.post("/backups/going-back")

        self.assertEqual(400, answer.status_code)
        self.assertFalse(self.requested())
        self.assertEqual([], self.quit)

    def test_where_quitting_is_not_wired_up_nothing_is_asked_for(self) -> None:
        self.old_game()
        lifecycle.reset_for_tests()

        answer = self.client.post("/backups/going-back")

        self.assertEqual(501, answer.status_code)
        self.assertFalse(self.requested())

    def test_a_quit_that_did_not_happen_leaves_no_request_behind(self) -> None:
        self.old_game()

        with patch("common.host.action_ops.perform", return_value=False):
            self.client.post("/backups/going-back")

        self.assertFalse(self.requested())

    def test_a_quit_that_fails_leaves_no_request_behind(self) -> None:
        self.old_game()

        with patch("common.host.action_ops.perform", side_effect=RuntimeError("no")):
            self.client.post("/backups/going-back")

        self.assertFalse(self.requested())


class CarryOut(GoingBackCase):
    def test_nothing_is_put_back_unless_it_was_asked_for(self) -> None:
        folder = self.old_game()
        now = (folder / "Taxi.info").read_text(encoding="utf-8")

        self.assertFalse(going_back.carry_out(self.config))

        self.assertEqual(now, (folder / "Taxi.info").read_text(encoding="utf-8"))

    def test_each_games_2x_copy_goes_back_with_its_id_and_the_request_is_spent(self) -> None:
        folder = self.old_game()
        going_back.request(self.config)

        self.assertTrue(going_back.carry_out(self.config))

        info = json.loads((folder / "Taxi.info").read_text(encoding="utf-8"))
        self.assertEqual("was", info["Info"]["Title"])
        self.assertEqual("id-Taxi", info["vpinfe"]["game_id"])
        self.assertFalse(self.requested())

    def test_what_it_replaces_is_kept(self) -> None:
        self.old_game()
        going_back.request(self.config)

        going_back.carry_out(self.config)

        self.assertEqual(["2.x", "3.0"], sorted(one["written_by"] for one in self.listing()
                                                if one["subject_name"] == "Taxi"))

    def test_the_collections_go_back_with_the_games(self) -> None:
        self.old_game()
        self.old_collections()
        going_back.request(self.config)

        going_back.carry_out(self.config)

        self.assertEqual(OLD_COLLECTIONS, (self.config / "collections.ini")
                         .read_text(encoding="utf-8"))

    def test_a_restart_asked_for_as_well_is_dropped(self) -> None:
        self.old_game()
        going_back.request(self.config)
        system_actions.request_app_restart()

        going_back.carry_out(self.config)

        self.assertFalse(device_registry.get_device_registry().self_get("restart_requested"))


if __name__ == "__main__":
    unittest.main()
