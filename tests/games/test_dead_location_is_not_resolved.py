"""A library folder that does not answer is never resolved or listed: `realpath` on a dead
mount blocks the way `stat` does, and nothing cancels it."""

from __future__ import annotations

from unittest import mock

from common.backups import places
from common.config import path_state
from common.games import library_folders
from tests.support.hung_folder import never_answering
from tests.support.library import TempTree, write_game

LIVE, DEAD = "live", "dead"


class DeadLocationTests(TempTree):
    def setUp(self) -> None:
        super().setUp()
        self.live, self.dead = self.root / LIVE, self.root / DEAD
        self.game = write_game(self.live, "Taxi", info={"Info": {"Title": "Taxi"}})
        self.dead.mkdir()
        self.places = [library_folders.Location("l1", str(self.live)),
                       library_folders.Location("l2", str(self.dead))]
        for patcher in (
                mock.patch.object(library_folders, "configured", return_value=self.places),
                mock.patch.object(path_state, "PROBE_SECONDS", 0.5)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def _refuse_to_resolve_the_dead_one(self):
        real = path_state.canonical

        def canonical(path: str) -> str:
            self.assertNotEqual(path, str(self.dead), "resolved a folder that did not answer")
            return real(path)

        return mock.patch.object(path_state, "canonical", side_effect=canonical)

    def test_the_locations_that_answered_are_the_ones_taken(self) -> None:
        with never_answering(str(self.dead)):
            self.assertEqual([one.location_id for one in library_folders.answering()], ["l1"])

    def test_a_game_in_a_live_folder_is_recognized_while_another_folder_is_dead(self) -> None:
        with never_answering(str(self.dead)), self._refuse_to_resolve_the_dead_one():
            self.assertTrue(places._is_game_folder(self.game))
