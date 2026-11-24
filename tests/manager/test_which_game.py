"""A drop on the library that brought no table asks which game it is for."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from common.games import game_names
from manager.library import which_game
from tests.manager.test_drop_arrival import ADrop
from tests.support.arrivals import GAME, OTHER

CRAZY = {"id": "g-3", "name": "Crazy Taxi (Bally 1999)", "folder": "/games/Crazy Taxi"}
MEDIA = {"has_game": False, "assets": [{"kind": "media"}],
         "files": ["Other Game backglass.png"]}


class RankedTests(unittest.TestCase):
    def test_games_the_files_name_come_first_and_the_rest_keep_the_libraries_order(self) -> None:
        shown = which_game.ranked([GAME, OTHER, CRAZY], ["Crazy Taxi - Wheel.png"])

        self.assertEqual([one["id"] for one in shown], ["g-3", "g-1", "g-2"])

    def test_the_game_more_of_the_files_name_comes_before_one_fewer_name(self) -> None:
        files = ["Other Game wheel.png", "Other Game flyer.png", "Some Game wheel.png"]

        shown = which_game.ranked([GAME, OTHER], files)

        self.assertEqual([one["id"] for one in shown], ["g-2", "g-1"])

    def test_the_makers_note_after_a_name_is_not_part_of_what_a_file_says(self) -> None:
        shown = which_game.ranked([GAME, CRAZY], ["Crazy Taxi.png"])

        self.assertEqual(shown[0]["id"], "g-3")

    def test_no_files_leave_the_libraries_order_alone(self) -> None:
        self.assertEqual(which_game.ranked([OTHER, GAME], []), [OTHER, GAME])

    def test_the_games_are_not_changed(self) -> None:
        held = [GAME, OTHER]

        which_game.ranked(held, ["Other Game.png"])

        self.assertEqual(held, [GAME, OTHER])


class TheOneItNamesTests(unittest.TestCase):
    def test_a_single_game_the_files_name_is_the_one_offered(self) -> None:
        self.assertEqual(which_game.named([GAME, OTHER], ["Other Game wheel.png"]), "g-2")

    def test_two_games_the_files_name_offer_none(self) -> None:
        files = ["Other Game wheel.png", "Some Game wheel.png"]

        self.assertEqual(which_game.named([GAME, OTHER], files), "")

    def test_a_name_inside_a_longer_one_the_files_also_name_is_the_longer_ones(self) -> None:
        taxi = {"id": "g-4", "name": "Taxi", "folder": "/games/Taxi"}

        self.assertEqual(which_game.named([taxi, CRAZY], ["Crazy Taxi wheel.png"]), "g-3")

    def test_files_that_name_no_game_offer_none(self) -> None:
        self.assertEqual(which_game.named([GAME, OTHER], ["wheel.png"]), "")


class GameNamesTests(unittest.TestCase):
    def test_the_makers_note_and_the_punctuation_do_not_count(self) -> None:
        self.assertTrue(game_names.names_game("Crazy-Taxi_wheel", "Crazy Taxi (Bally 1999)"))

    def test_a_game_with_no_name_is_named_by_nothing(self) -> None:
        self.assertFalse(game_names.names_game("anything", "(Bally 1999)"))

    def test_another_game_is_not_named(self) -> None:
        self.assertFalse(game_names.names_game("Other Game wheel", "Some Game"))


class ALibraryDropWithNoTable(ADrop):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.library.games = [GAME, OTHER]
        self.analyzed.return_value = MEDIA
        self.asked = AsyncMock(return_value=OTHER)
        patched = patch.object(which_game, "ask", new=self.asked)
        patched.start()
        self.addCleanup(patched.stop)

    async def test_it_asks_which_game_and_runs_the_import_on_the_one_chosen(self) -> None:
        await self._dropped("overview")

        self.asked.assert_awaited_once()
        sent = self._sent()
        self.assertEqual((sent["game_id"], sent["game_dir"], sent["allow_new_game"]),
                         ("g-2", OTHER["folder"], False))
        self.aborted.assert_not_called()

    async def test_the_question_is_put_the_files_the_drop_holds(self) -> None:
        await self._dropped("games")

        asked = self.asked.await_args
        assert asked is not None
        self.assertEqual(asked.args[1], MEDIA)

    async def test_cancelling_lets_go_of_the_upload_and_imports_nothing(self) -> None:
        self.asked.return_value = None

        await self._dropped("overview", upload_id="u-9")

        self.aborted.assert_called_once_with("u-9")
        self.confirmed.assert_not_awaited()
        self.said.assert_not_called()

    async def test_a_game_gone_before_the_answer_says_so_and_lets_go(self) -> None:
        self.asked.return_value = {"id": "g-gone"}

        await self._dropped("overview", upload_id="u-9")

        self._turned_away_with("manager.page.row_unknown")
        self.aborted.assert_called_once_with("u-9")

    async def test_a_question_that_fails_says_so_and_lets_go(self) -> None:
        self.asked.side_effect = RuntimeError("no picker")

        await self._dropped("overview", upload_id="u-9")

        self._turned_away_with("manager.uploads.not_work")
        self.aborted.assert_called_once_with("u-9")

    async def test_an_empty_library_has_no_game_to_ask_about(self) -> None:
        self.library.games = []

        await self._dropped("overview", upload_id="u-9")

        self.asked.assert_not_awaited()
        self._turned_away_with("manager.page.drop_hint")
        self.aborted.assert_called_once_with("u-9")

    async def test_a_drop_that_holds_nothing_for_a_game_is_still_turned_away(self) -> None:
        self.analyzed.return_value = {"has_game": False, "assets": [], "files": ["notes.xyz"]}

        await self._dropped("overview")

        self.asked.assert_not_awaited()
        self._turned_away_with("manager.page.drop_hint")

    async def test_a_drop_with_a_table_makes_a_new_game_and_asks_nothing(self) -> None:
        self.analyzed.return_value = {"has_game": True, "assets": [{"kind": "table"}]}
        patched = patch("manager.library.import_dialog.ask_where", new=AsyncMock(return_value=""))
        patched.start()
        self.addCleanup(patched.stop)

        await self._dropped("overview")

        self.asked.assert_not_awaited()
        self.assertTrue(self._sent()["allow_new_game"])

    async def test_a_drop_on_a_game_asks_nothing(self) -> None:
        await self._dropped("games", row_id="g-1", col_id="name")

        self.asked.assert_not_awaited()
        self.assertEqual(self._sent()["game_id"], "g-1")


if __name__ == "__main__":
    unittest.main()
