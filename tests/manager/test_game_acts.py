"""A game's and a table's verbs: one list, drawn in the grid's row menu, the panel's header
menu and the grid's selection menu, so the three cannot offer different things."""

from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from nicegui import ui

from manager.kit import panel
from manager.library import game_acts

_PAGE = ui.element()


def _table(table_id: str, game_id: str = "afm", **fields: Any) -> dict[str, Any]:
    return {"id": table_id, "game_id": game_id, "available": True, "filename": f"{table_id}.vpx",
            **fields}


async def _after(_games: list[str], _gone: bool) -> None:
    return None


async def _refresh(_ids: list[str]) -> None:
    return None


def _game(game_id: str = "afm", **fields: Any) -> dict[str, Any]:
    return {"id": game_id, "name": "Attack from Mars", **fields}


def _library(*games: dict[str, Any]) -> MagicMock:
    library = MagicMock()
    library.games = list(games)
    return library


def _labels(verbs: list[panel.Verb]) -> list[str]:
    return [one.label for one in verbs]


def _acts(table: dict[str, Any], *tables: dict[str, Any], collections: Any = None,
          library: Any = None) -> list[panel.Verb]:
    return game_acts.table_acts(
        library or MagicMock(),
        [game_acts.Target(table["game_id"], "Attack from Mars", table, list(tables) or [table])],
        _after, {}, collections)


def _several(*tables: dict[str, Any], library: Any = None) -> list[panel.Verb]:
    return game_acts.table_acts(
        library or MagicMock(),
        [game_acts.Target(one["game_id"], "Attack from Mars", one, list(tables))
         for one in tables], _after, {}, None)


class ForAGame(unittest.TestCase):
    def test_it_can_be_played_and_filed(self) -> None:
        verbs = game_acts.game_acts(_library(), [_game()], {}, _refresh,
                                    collections=lambda _: None)

        self.assertEqual(["Launch", "Mark Private", "Match to VPS…", "Auto-Match",
                          "Download Missing Media…", "Send to Device…", "Back Up Now…",
                          "Add to Collection", "Delete from Disk"], _labels(verbs))

    def test_it_is_filed_only_once_the_collections_are_read(self) -> None:
        verbs = game_acts.game_acts(_library(), [_game()], {}, _refresh)

        self.assertNotIn("Add to Collection", _labels(verbs))

    def test_launching_is_for_one_game(self) -> None:
        verbs = game_acts.game_acts(_library(), [_game(), _game("medusa")], {}, _refresh)

        self.assertTrue(next(one for one in verbs if one.label == "Launch").one)

    def test_rating_is_offered_where_the_caller_can_write_one(self) -> None:
        async def rate(_ids: list[str], _value: int) -> bool:
            return True

        rated = game_acts.game_acts(_library(), [_game()], {}, _refresh, rate=rate)
        unrated = game_acts.game_acts(_library(), [_game()], {}, _refresh)

        self.assertEqual("Rate…", _labels(rated)[1])
        self.assertNotIn("Rate…", _labels(unrated))

    def test_private_is_offered_for_the_games_it_applies_to(self) -> None:
        plain = game_acts.game_acts(_library(), [_game()], {}, _refresh)
        marked = game_acts.game_acts(_library(), [_game(private=True)], {}, _refresh)
        mixed = game_acts.game_acts(_library(), [_game(private=True), _game("medusa")], {},
                                    _refresh)

        self.assertIn("Mark Private", _labels(plain))
        self.assertNotIn("Mark Not Private", _labels(plain))
        self.assertIn("Mark Not Private", _labels(marked))
        self.assertNotIn("Mark Private", _labels(marked))
        self.assertIn("Mark Private", _labels(mixed))
        self.assertIn("Mark Not Private", _labels(mixed))

    def test_sending_asks_where_for_these_games_with_the_pages_state(self) -> None:
        state: dict[str, Any] = {"watch_jobs": lambda: None}
        games = [_game()]
        verbs = game_acts.game_acts(_library(), games, state, _refresh)
        send = next(one for one in verbs if one.label == "Send to Device…")

        with patch.object(game_acts.send_to_device, "ask_where") as ask:
            assert send.run is not None
            send.run()

        ask.assert_called_once_with(games, state)

    def test_the_play_record_can_be_reset_only_where_there_is_one(self) -> None:
        played = _library({"id": "afm", "user": {"play_count": 3}})
        unplayed = _library({"id": "afm", "user": {"play_count": 0, "last_played": None}})

        offered = game_acts.game_acts(played, [_game()], {}, _refresh)
        quiet = game_acts.game_acts(unplayed, [_game()], {}, _refresh)

        self.assertTrue(next(one for one in offered
                             if one.label == "Reset Game Play Record").danger)
        self.assertNotIn("Reset Game Play Record", _labels(quiet))

    def test_deleting_from_disk_is_a_danger_offered_for_any_games(self) -> None:
        for chosen in ([_game()], [_game(), _game("medusa")]):
            with self.subTest(count=len(chosen)):
                verbs = game_acts.game_acts(_library(), chosen, {}, _refresh)

                self.assertTrue(next(one for one in verbs
                                     if one.label == "Delete from Disk").danger)

    def test_a_single_game_is_named_in_the_match_walk_and_several_are_not(self) -> None:
        self.assertEqual("Attack from Mars", game_acts._only_name([_game()]))
        self.assertEqual("", game_acts._only_name([_game(), _game("medusa")]))


class ForATable(unittest.TestCase):
    def test_the_games_own_record_is_cleared_apart_from_the_tables(self) -> None:
        played = _library({"id": "afm", "user": {"play_count": 3}})

        verb = game_acts.reset_games(played, ["afm"], _refresh)

        self.assertEqual("Reset Game Play Record", verb.label if verb else "")
        self.assertIsNone(game_acts.reset_games(_library({"id": "afm"}), ["afm"], _refresh))

    def test_a_table_that_is_not_the_default_can_be_made_it(self) -> None:
        verbs = _acts(_table("a"), _table("a"), _table("b", default=True))

        self.assertEqual(["Make Default", "Hide", "Match to VPS…", "Extract Script"],
                         _labels(verbs))

    def test_the_default_of_several_can_be_locked_and_not_made_the_default(self) -> None:
        one = _table("a", default=True)
        verbs = _acts(one, one, _table("b"))

        self.assertNotIn("Make Default", _labels(verbs))
        self.assertEqual(["Hide", "Match to VPS…", "Extract Script"], _labels(verbs)[1:])
        self.assertEqual(4, len(verbs))

    def test_a_hidden_table_offers_to_unhide_and_cannot_be_made_the_default(self) -> None:
        verbs = _acts(_table("a", hidden=True))

        self.assertEqual(["Unhide", "Match to VPS…", "Extract Script"], _labels(verbs))

    def test_a_missing_table_can_be_forgotten_as_a_danger(self) -> None:
        verbs = _acts(_table("a", available=False, absent_since="2026-10-01"),
                      collections=lambda _: None)

        self.assertEqual("Forget", verbs[-1].label)
        self.assertTrue(verbs[-1].danger)
        self.assertEqual("Add to Collection", verbs[-2].label)

    def test_the_play_record_can_be_reset_only_where_there_is_one(self) -> None:
        played = _acts(_table("a", user={"last_played": "2026-10-01T20:00:00Z"}))
        unplayed = _acts(_table("a", user={"play_count": 0}))

        self.assertIn("Reset Table Play Record", _labels(played))
        self.assertTrue(next(one for one in played
                             if one.label == "Reset Table Play Record").danger)
        self.assertNotIn("Reset Table Play Record", _labels(unplayed))

    def test_the_games_record_is_reset_beside_the_tables(self) -> None:
        verbs = _acts(_table("a"), library=_library({"id": "afm", "user": {"play_count": 2}}))

        self.assertEqual("Reset Game Play Record", verbs[-1].label)
        self.assertTrue(verbs[-1].danger)

    def test_a_table_on_disk_cannot_be_forgotten(self) -> None:
        self.assertFalse([one for one in _acts(_table("a")) if one.danger])

    def test_a_table_running_its_own_script_can_delete_it(self) -> None:
        own = _table("a", assets={"script": {"file": "a.vbs", "resolution": "sidecar"}})
        verbs = _acts(own)

        self.assertIn("Delete Script", _labels(verbs))
        self.assertNotIn("Extract Script", _labels(verbs))
        self.assertTrue(next(one for one in verbs if one.label == "Delete Script").danger)


class HidingSeveralTables(unittest.IsolatedAsyncioTestCase):
    async def test_hiding_offers_undo_that_unhides_each_table_it_hid(self) -> None:
        library = MagicMock()
        offers: list[Any] = []

        async def inline(call, *args):
            return call(*args)

        shown = _table("a")
        already = _table("b", hidden=True)
        with patch.object(game_acts.run, "io_bound", inline), \
                patch.object(game_acts.undo, "offer", lambda *args, **_kw: offers.append(args)), \
                patch.object(game_acts.ui, "notify"):
            verbs = _several(shown, already, library=library)
            hide = next(one for one in verbs if one.label == "Hide")
            with _PAGE:
                assert hide.run is not None
                await hide.run()
                library.set_table_hidden.assert_called_once_with("afm", "a", True)
                await offers[0][1]()

        self.assertEqual([("afm", "a", True), ("afm", "a", False)],
                         [call.args for call in library.set_table_hidden.call_args_list])


class MatchingReleases(unittest.IsolatedAsyncioTestCase):
    def _library(self) -> MagicMock:
        library = _library(_game("afm", vps_id="v1"), _game("medusa"), _game("pz", vps_id="v2"))
        library.tables_for.side_effect = lambda game_id: [
            _table("a", game_id, source={"vps_file_id": "bound"}), _table("b", game_id),
            _table("p", game_id)]
        return library

    async def walk(self, library: Any, tables: list[dict[str, Any]],
                   answers: list[Any]) -> list[tuple[str, str]]:
        """Run the verb with the picker answering from `answers`; what it was shown, as
        (table id, position)."""
        shown: list[tuple[str, str]] = []
        after = AsyncMock()

        async def inline(call, *args):
            return call(*args)

        async def picker(_context, table, place=""):
            shown.append((str(table["id"]), place))
            return answers.pop(0)

        with patch.object(game_acts.run, "io_bound", inline), \
                patch("manager.workbench.game_match.ask_a_release", picker), \
                patch.object(game_acts.ui, "notify"):
            verbs = game_acts.table_acts(
                library, [game_acts.Target(one["game_id"], "G", one, tables) for one in tables],
                after, {}, None)
            match = next(one for one in verbs if one.label == "Match to VPS…")
            with _PAGE:
                assert match.run is not None
                await match.run()
        self.after = after
        return shown

    def test_it_is_dimmed_with_a_reason_where_no_selected_game_is_matched(self) -> None:
        verbs = _several(_table("m", "medusa"), library=self._library())
        match = next(one for one in verbs if one.label == "Match to VPS…")

        self.assertIsNone(match.run)
        self.assertTrue(match.hint)

    async def test_tables_of_unmatched_games_are_left_out_and_the_rest_are_numbered(self) -> None:
        tables = [_table("a"), _table("b", "medusa"), _table("p", "pz")]

        shown = await self.walk(self._library(), tables, [None, None])

        self.assertEqual([("a", "1 of 2"), ("p", "2 of 2")], shown)

    async def test_a_matched_table_is_shown_with_the_match_it_holds(self) -> None:
        library = self._library()
        seen: list[Any] = []

        async def inline(call, *args):
            return call(*args)

        async def picker(_context, table, place=""):
            seen.append(table["source"])
            return None

        with patch.object(game_acts.run, "io_bound", inline), \
                patch("manager.workbench.game_match.ask_a_release", picker):
            verbs = game_acts.table_acts(
                library, [game_acts.Target("afm", "G", _table("a"), [_table("a")])],
                _after, {}, None)
            match = next(one for one in verbs if one.label == "Match to VPS…")
            with _PAGE:
                assert match.run is not None
                await match.run()

        self.assertEqual([{"vps_file_id": "bound"}], seen)

    async def test_skip_writes_nothing_and_an_answer_or_a_clear_is_written(self) -> None:
        library = self._library()
        tables = [_table("a"), _table("b"), _table("p", "pz")]

        await self.walk(library, tables, [None, "new", ""])

        self.assertEqual([("afm", "b", "new"), ("pz", "p", "")],
                         [call.args for call in library.set_table_source.call_args_list])
        self.after.assert_awaited_once_with(["afm", "pz"], False)

    async def test_stop_ends_the_walk_and_leaves_the_rest(self) -> None:
        library = self._library()
        tables = [_table("a"), _table("b"), _table("p", "pz")]

        shown = await self.walk(library, tables, ["new", game_acts.walks.STOPPED])

        self.assertEqual(["a", "b"], [one for one, _place in shown])
        self.assertEqual([("afm", "a", "new")],
                         [call.args for call in library.set_table_source.call_args_list])

    async def test_one_matched_table_is_shown_without_a_position(self) -> None:
        shown = await self.walk(self._library(), [_table("a"), _table("b", "medusa")], [None])

        self.assertEqual([("a", "")], shown)

    async def test_a_game_whose_tables_cannot_be_read_is_skipped_and_the_rest_are_put_right(
            self) -> None:
        library = self._library()
        listed = library.tables_for.side_effect

        def tables_for(game_id: str) -> Any:
            if game_id == "pz":
                raise OSError("gone")
            return listed(game_id)

        library.tables_for.side_effect = tables_for
        tables = [_table("a"), _table("p", "pz")]

        shown = await self.walk(library, tables, ["new"])

        self.assertEqual([("a", "1 of 2")], shown)
        self.assertEqual([("afm", "a", "new")],
                         [call.args for call in library.set_table_source.call_args_list])
        self.after.assert_awaited_once_with(["afm"], False)

    async def test_a_walk_with_nothing_written_puts_nothing_right(self) -> None:
        await self.walk(self._library(), [_table("a")], [None])

        self.after.assert_not_awaited()


class DeletingGamesFromDisk(unittest.IsolatedAsyncioTestCase):
    async def run_it(self, answer: bool, library: Any, games: list[dict[str, Any]],
                     refreshed: list[list[str]]) -> list[Any]:
        asked: list[Any] = []

        async def inline(call, *args):
            return call(*args)

        async def said(*args, **kwargs):
            asked.append(kwargs)
            return answer

        async def refresh(ids: list[str]) -> None:
            refreshed.append(ids)

        with patch.object(game_acts.run, "io_bound", inline), \
                patch.object(game_acts.confirm, "ask", said), \
                patch.object(game_acts.ui, "notify"):
            verb = game_acts.delete_games(library, games, refresh)
            with _PAGE:
                assert verb.run is not None
                await verb.run()
        return asked

    async def test_a_no_deletes_nothing(self) -> None:
        library = MagicMock()
        refreshed: list[list[str]] = []

        await self.run_it(False, library, [_game()], refreshed)

        library.delete_game_folder.assert_not_called()
        self.assertEqual([], refreshed)

    async def test_the_question_names_each_folder(self) -> None:
        games = [_game(folder="/tables/Attack"), _game("medusa", folder="/tables/Medusa")]

        asked = await self.run_it(False, MagicMock(), games, [])

        self.assertEqual(["/tables/Attack", "/tables/Medusa"], list(asked[0]["lines"]))

    async def test_a_yes_deletes_each_and_puts_the_page_right(self) -> None:
        library = MagicMock()
        refreshed: list[list[str]] = []

        await self.run_it(True, library, [_game(), _game("medusa")], refreshed)

        self.assertEqual([("afm",), ("medusa",)],
                         [call.args for call in library.delete_game_folder.call_args_list])
        self.assertEqual([["afm", "medusa"]], refreshed)

    async def test_a_failure_part_way_puts_right_what_was_deleted(self) -> None:
        library = MagicMock()
        refreshed: list[list[str]] = []
        library.delete_game_folder.side_effect = [None, RuntimeError("busy")]

        await self.run_it(True, library, [_game(), _game("medusa")], refreshed)

        self.assertEqual([["afm"]], refreshed)


class ForSeveralTables(unittest.TestCase):
    def test_nothing_chosen_offers_nothing(self) -> None:
        self.assertEqual([], game_acts.table_acts(MagicMock(), [], _after, {}, None))

    def test_a_verb_shows_when_it_applies_to_any_of_them(self) -> None:
        verbs = _several(_table("a", hidden=True), _table("b", "medusa"))

        self.assertIn("Hide", _labels(verbs))
        self.assertIn("Unhide", _labels(verbs))

    def test_making_a_default_is_for_one_table_and_is_dimmed_for_several(self) -> None:
        verbs = panel.for_selection(_several(_table("a"), _table("b", "medusa")), 2)

        make = next(one for one in verbs if one.label == "Make Default")
        self.assertIsNone(make.run)
        self.assertEqual("", make.hint)

    def test_forgetting_is_one_verb_for_one_table_or_many(self) -> None:
        gone: dict[str, Any] = {"available": False, "absent_since": "2026-10-01"}
        one = _acts(_table("a", **gone))
        many = _several(_table("a", **gone), _table("b", "medusa", **gone))

        self.assertEqual(["Forget"], [v.label for v in one if v.danger])
        self.assertEqual(["Forget"], [v.label for v in many if v.danger])


class DrawnIntoAMenu(unittest.TestCase):
    def test_an_entry_that_draws_its_own_is_given_what_closes_the_menu(self) -> None:
        given: list[Any] = []

        def draw(close: Any) -> None:
            given.append(close)

        def shut() -> None:
            return None

        with _PAGE, ui.menu():
            panel.verb_entries([panel.Verb("Rate", lambda: None),
                                panel.Verb("Collections", draw=draw)], shut)

        self.assertEqual([shut], given)


async def _inline(call, *args, **kwargs):
    return call(*args, **kwargs)


class DeletingATableFile(unittest.IsolatedAsyncioTestCase):
    """Delete from Disk keeps a copy of each file, and the notice offers them back."""

    async def deleted(self, kept: list[dict[str, Any]]) -> tuple[MagicMock, MagicMock]:
        self.notified = MagicMock()
        library = MagicMock()
        library.table_swap_plan.return_value = {"label": "1.1 · Rascal", "filename": "a.vpx",
                                                "files": [], "patched": []}
        library.delete_table_file.return_value = {"kept": kept}
        offered = MagicMock()
        table = _table("a", form="contained")
        verbs = game_acts.table_acts(
            library, [game_acts.Target("afm", "Attack from Mars", table, [table, _table("b")])],
            _after, {}, None)
        run = next(one for one in verbs if one.label == "Delete from Disk").run
        assert run is not None
        with patch.object(game_acts.offload, "io", _inline), \
                patch.object(game_acts.run, "io_bound", _inline), \
                patch.object(game_acts.confirm, "ask", AsyncMock(return_value=True)), \
                patch.object(game_acts.undo, "offer", offered), \
                patch.object(game_acts.ui, "notify", self.notified):
            with _PAGE:
                await run()
            if offered.called:
                await offered.call_args.args[1]()
        return library, offered

    async def test_the_notice_offers_to_put_back_every_copy_that_was_kept(self) -> None:
        library, offered = await self.deleted([{"id": "c1"}, {"id": "c2"}])

        offered.assert_called_once()
        self.assertIn("1.1 · Rascal", offered.call_args.args[0])
        self.assertEqual(["c1", "c2"], [call.args[0] for call in
                                         library.restore_backup.call_args_list])
        library.restored_backups.assert_called_once()

    async def test_a_delete_that_kept_nothing_only_says_so(self) -> None:
        _library, offered = await self.deleted([])

        offered.assert_not_called()
        self.notified.assert_called_once()
        self.assertIn("1.1 · Rascal", self.notified.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
