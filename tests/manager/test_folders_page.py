"""The Folders page: what each row says and offers, from the rows the API reports.

Drawn from `GET /locations` alone, so the cases are rows in and words out. The scenarios
are the ones that decide where new games go: one folder that can take them, several with
asking on, several with it off, the chosen one away, the chosen one removed, none able to.
"""

from __future__ import annotations

import asyncio
import unittest
from typing import Any
from unittest.mock import AsyncMock, MagicMock, Mock, patch

from nicegui import ui

from common.config import path_state
from common.devices import install_identity
from common.games import library_folders as model
from manager.kit import offload, panel
from manager.settings import folders
from manager.settings import settings as settings_page
from tests.support.clicks import press

# Made at import, outside any task, where NiceGUI still hands out the script's own page.
_PAGE = ui.element()


def row(location_id: str, *, state: str = path_state.OK, kind: str = model.KIND_ROOT,
        games: int = 3, shadowed: int = 0, write_to: bool = False, server: str = "",
        reason: str = "", path: str = "") -> dict[str, Any]:
    ready = state in (path_state.OK, path_state.READ_ONLY)
    return {"location_id": location_id, "path": path or f"/games/{location_id}",
            "kind": kind, "state": state, "reason": reason, "reachable": ready,
            "writable": state == path_state.OK, "games": games, "shadowed": shadowed,
            "write_to": write_to, "origin": {"server": server} if server else {}}


def found(*rows: dict[str, Any], ask: bool = True) -> dict[str, Any]:
    return {"locations": list(rows), "ask_where_new_games_go": ask}


def labels(block: folders.Folders, folder_id: str) -> list[str]:
    at = block.order.index(folder_id)
    folder = next(one for one in block.plan.folders if one.id == folder_id)
    return [one.label for one in folders.acts(block, folder, at, len(block.order))]


def make(*rows: dict[str, Any], ask: bool = True) -> folders.Folders:
    return folders.Folders(Mock(), found(*rows, ask=ask), MagicMock())


class ScenarioTests(unittest.TestCase):
    """Where new games go, as the chip, the switch and the menu say it."""

    def test_one_folder_that_can_take_a_game_has_the_chip_and_no_choice(self) -> None:
        plan = folders.plan(found(row("a", write_to=True)))

        self.assertEqual(plan.chip, "a")
        self.assertFalse(plan.switch)
        self.assertFalse(plan.nowhere)

    def test_the_only_candidate_is_not_offered_what_it_already_has(self) -> None:
        block = make(row("a", write_to=True))

        self.assertNotIn(folders.t("manager.folders.put_here"), labels(block, "a"))

    def test_asking_on_with_several_has_no_chip_and_the_switch(self) -> None:
        plan = folders.plan(found(row("a", write_to=True), row("b"), ask=True))

        self.assertEqual(plan.chip, "")
        self.assertTrue(plan.switch)
        self.assertTrue(plan.asks)

    def test_asking_on_offers_the_verb_on_every_folder(self) -> None:
        block = make(row("a"), row("b"), ask=True)

        for one in ("a", "b"):
            with self.subTest(folder=one):
                self.assertIn(folders.t("manager.folders.put_here"), labels(block, one))

    def test_asking_off_puts_the_chip_on_the_chosen_folder(self) -> None:
        block = make(row("a"), row("b", write_to=True), row("c", state=path_state.NOT_ANSWERING),
                     ask=False)

        self.assertEqual(block.plan.chip, "b")
        self.assertTrue(block.plan.switch)
        self.assertNotIn(folders.t("manager.folders.put_here"), labels(block, "b"))
        self.assertIn(folders.t("manager.folders.put_here"), labels(block, "a"))

    def test_a_chosen_folder_that_is_away_keeps_the_chip(self) -> None:
        plan = folders.plan(found(row("a"), row("b", state=path_state.NOT_ANSWERING,
                                                write_to=True), ask=False))

        self.assertEqual(plan.chip, "b")
        self.assertFalse(plan.nowhere)

    def test_a_chosen_folder_that_is_read_only_keeps_the_chip_and_says_what_follows(
            self) -> None:
        plan = folders.plan(found(row("a"), row("b", state=path_state.READ_ONLY, write_to=True),
                                  ask=False))
        folder = plan.folders[1]

        self.assertEqual(plan.chip, "b")
        self.assertIn(folders.t("manager.folders.chip_read_only"), folders.chip_tip(folder))

    def test_the_chip_of_an_away_folder_says_adding_asks_until_it_is_back(self) -> None:
        away = folders.Folder.read(row("b", state=path_state.NOT_ANSWERING, write_to=True))

        self.assertIn(folders.t("manager.folders.chip_away"), folders.chip_tip(away))

    def test_a_removed_choice_moves_the_chip_to_the_first_that_can_take_one(self) -> None:
        """The API reports the first writable folder once nothing is chosen."""
        plan = folders.plan(found(row("a", state=path_state.MISSING), row("b", write_to=True),
                                  row("c"), ask=False))

        self.assertEqual(plan.chip, "b")

    def test_no_folder_able_to_take_a_game_has_no_chip_and_says_so(self) -> None:
        plan = folders.plan(found(row("a", state=path_state.NOT_ANSWERING),
                                  row("b", state=path_state.READ_ONLY),
                                  row("c", kind=model.KIND_GAME), ask=False))

        self.assertEqual(plan.chip, "")
        self.assertTrue(plan.nowhere)
        self.assertFalse(plan.switch)

    def test_a_second_folder_added_while_asking_is_on_gives_way_to_the_switch(self) -> None:
        before = folders.plan(found(row("a", write_to=True), ask=True))
        after = folders.plan(found(row("a", write_to=True), row("b"), ask=True))

        self.assertEqual((before.chip, before.switch), ("a", False))
        self.assertEqual((after.chip, after.switch), ("", True))

    def test_a_single_game_folder_is_never_offered_new_games(self) -> None:
        block = make(row("a", write_to=True), row("g", kind=model.KIND_GAME))

        self.assertNotIn(folders.t("manager.folders.put_here"), labels(block, "g"))

    def test_a_folder_that_cannot_take_one_now_has_the_verb_dimmed_with_why(self) -> None:
        block = make(row("a", write_to=True),
                     row("b", state=path_state.NOT_ANSWERING,
                         reason="No answer in time. Try later."),
                     ask=False)
        folder = block.plan.folders[1]

        verb = next(one for one in folders.acts(block, folder, 1, 2)
                    if one.label == folders.t("manager.folders.put_here"))

        self.assertIsNone(verb.run)
        self.assertEqual(verb.hint, "No answer in time")

    def test_a_read_only_folder_is_refused_in_the_catalog_s_words(self) -> None:
        block = make(row("a", write_to=True), row("b", state=path_state.READ_ONLY), ask=False)
        verb = next(one for one in folders.acts(block, block.plan.folders[1], 1, 2)
                    if one.label == folders.t("manager.folders.put_here"))

        self.assertIsNone(verb.run)
        self.assertEqual(verb.hint, folders.t("error.path.unwritable"))


class MenuTests(unittest.TestCase):
    def test_remove_is_last_and_the_only_dangerous_one(self) -> None:
        block = make(row("a"), row("b"), row("c"))

        for one in ("a", "b", "c"):
            with self.subTest(folder=one):
                folder = next(f for f in block.plan.folders if f.id == one)
                verbs = folders.acts(block, folder, block.order.index(one), 3)
                self.assertEqual([v.label for v in verbs if v.danger], [verbs[-1].label])
                self.assertEqual(verbs[-1].label, folders.t("word.remove"))

    def test_the_first_row_cannot_move_up_and_the_last_cannot_move_down(self) -> None:
        block = make(row("a"), row("b"))
        up, down = (folders.t("manager.theme_sources.move_up"),
                    folders.t("manager.theme_sources.move_down"))

        self.assertNotIn(up, labels(block, "a"))
        self.assertIn(down, labels(block, "a"))
        self.assertIn(up, labels(block, "b"))
        self.assertNotIn(down, labels(block, "b"))

    def test_every_row_can_be_changed(self) -> None:
        block = make(row("a"), row("g", kind=model.KIND_GAME))

        for one in ("a", "g"):
            self.assertIn(folders.t("manager.folders.change"), labels(block, one))

    def test_a_lone_folder_has_nothing_to_move(self) -> None:
        block = make(row("a", write_to=True))

        self.assertEqual(labels(block, "a"), [folders.t("manager.folders.change"),
                                              folders.t("word.remove")])


class RowTests(unittest.TestCase):
    def folder(self, **kwargs: Any) -> folders.Folder:
        return folders.Folder.read(row("a", **kwargs))

    def test_the_caption_counts_games_and_names_the_server_and_duplicates(self) -> None:
        said = folders.caption(self.folder(games=214, server="nas", shadowed=2))

        self.assertEqual(said, [("214 games", False), ("on nas", False),
                                ("2 duplicates", True)])

    def test_one_game_and_no_games_have_their_own_words(self) -> None:
        self.assertEqual(folders.caption(self.folder(games=1)), [("1 game", False)])
        self.assertEqual(folders.caption(self.folder(games=0)), [("No games", False)])

    def test_a_single_game_folder_says_one_game_whatever_was_counted(self) -> None:
        self.assertEqual(folders.caption(self.folder(kind=model.KIND_GAME, games=0)),
                         [("One game", False)])

    def test_a_folder_that_cannot_be_read_gives_no_count(self) -> None:
        """Its chip says why they are missing, and a zero would say they are gone."""
        self.assertEqual(folders.caption(self.folder(state=path_state.NOT_ANSWERING, games=0)), [])

    def test_a_ready_folder_has_no_chip(self) -> None:
        self.assertIsNone(folders.state_chip(self.folder()))

    def test_a_folder_that_is_not_ready_is_named_by_its_state(self) -> None:
        word, level, tip = folders.state_chip(self.folder(
            state=path_state.MISSING, reason="Nothing is there."))  # type: ignore[misc]

        self.assertEqual(word, folders.t("word.not_found"))
        self.assertEqual((level, tip), ("bad", "Nothing is there."))

    def test_read_only_costs_scores_and_settings_and_is_a_warning(self) -> None:
        word, level, tip = folders.state_chip(self.folder(  # type: ignore[misc]
            state=path_state.READ_ONLY, reason="Mounted read-only."))

        self.assertEqual(word, folders.t("word.read_only"))
        self.assertEqual(level, "warn")
        self.assertEqual(tip, folders.t("manager.folders.read_only_cost"))

    def test_the_first_sentence_is_the_reason_without_its_stop(self) -> None:
        self.assertEqual(folders.first_sentence("No answer in time. Check the share."),
                         "No answer in time")
        self.assertEqual(folders.first_sentence("Nothing is there."), "Nothing is there")


class RemovedWordsTests(unittest.TestCase):
    def said(self, **kwargs: Any) -> str:
        return folders.removed_said(folders.Folder.read(row("a", path="/games/a", **kwargs)))

    def test_a_folder_with_games_says_how_many_left(self) -> None:
        self.assertEqual(self.said(games=214), "Removed /games/a. Its 214 games left the "
                                               "library, and nothing on disk was touched.")

    def test_a_folder_with_one_game_says_its_game(self) -> None:
        self.assertEqual(self.said(games=1), "Removed /games/a. Its game left the library, "
                                             "and nothing on disk was touched.")

    def test_a_single_game_folder_is_its_game(self) -> None:
        self.assertEqual(self.said(kind=model.KIND_GAME, games=0),
                         "Removed /games/a. Its game left the library, and nothing on "
                         "disk was touched.")

    def test_a_folder_that_cannot_be_read_has_no_count_to_give(self) -> None:
        self.assertEqual(self.said(state=path_state.NOT_ANSWERING, games=0),
                         "Removed /games/a. Nothing on disk was touched.")


class ActTests(unittest.IsolatedAsyncioTestCase):
    """What a choice from the menu writes."""

    async def asyncSetUp(self) -> None:
        self.library = Mock()
        self.redraw = MagicMock()
        self.enterContext(patch.object(
            offload, "io", new=AsyncMock(side_effect=lambda call, *args: call(*args))))
        self.enterContext(patch("nicegui.run.io_bound",
                                new=AsyncMock(side_effect=lambda call, *a: call(*a))))
        self.enterContext(patch("manager.kit.on_page.ui"))
        self.notified = self.enterContext(patch.object(folders.ui, "notify"))
        self.offered = self.enterContext(patch.object(folders.undo, "offer"))

    def block(self, *rows: dict[str, Any], ask: bool = True) -> folders.Folders:
        return folders.Folders(self.library, found(*rows, ask=ask), self.redraw)

    def here(self, *ids: str) -> None:
        """The folders the server holds when an Undo asks, the removed one back last."""
        self.library.locations.return_value = {
            "locations": [{"location_id": one} for one in ids]}

    async def test_choosing_a_folder_for_new_games_turns_asking_off(self) -> None:
        block = self.block(row("a"), row("b"), ask=True)

        await block.put_here("b")

        self.library.set_location_write_to.assert_called_once_with("b")
        self.library.put_config.assert_called_once()
        self.redraw.assert_called_once()

    async def test_choosing_one_while_not_asking_leaves_the_switch_alone(self) -> None:
        block = self.block(row("a"), row("b"), ask=False)

        await block.put_here("b")

        self.library.put_config.assert_not_called()

    async def test_a_step_writes_the_new_order(self) -> None:
        block = self.block(row("a"), row("b"), row("c"))

        await block.step("a", 1)

        self.library.set_location_order.assert_called_once_with(["b", "a", "c"])
        self.redraw.assert_called_once()

    async def test_a_drop_naming_no_row_writes_nothing(self) -> None:
        block = self.block(row("a"), row("b"))

        for event in ({}, {"from": "x", "to": 1}, {"from": 5, "to": 0}, {"from": 1, "to": 1}):
            await block.moved(event)

        self.library.set_location_order.assert_not_called()

    async def test_remove_acts_at_once_and_offers_it_back(self) -> None:
        block = self.block(row("a"), row("b", games=214, write_to=True), row("c"), ask=False)
        self.here("a", "c")
        gone = block.plan.folders[1]

        await press(block.remove, gone)

        self.library.delete_location.assert_called_once_with("b")
        said, undo = self.offered.call_args.args
        self.assertEqual(said, folders.removed_said(gone))

        await undo()

        self.library.put_location.assert_called_once_with(
            "b", {"path": "/games/b", "kind": model.KIND_ROOT})
        self.library.set_location_order.assert_called_once_with(["a", "b", "c"])
        self.library.set_location_write_to.assert_called_once_with("b")

    async def test_undoing_a_remove_does_not_choose_what_was_not_chosen(self) -> None:
        block = self.block(row("a"), row("b"), ask=False)
        self.here("a")

        await press(block.remove, block.plan.folders[1])
        await self.offered.call_args.args[1]()

        self.library.set_location_write_to.assert_not_called()

    async def test_undoing_a_remove_does_not_make_the_default_a_choice(self) -> None:
        """With asking on the chip sits on the first folder that can take a game, and
        nobody chose it."""
        block = self.block(row("a", write_to=True), row("b"), ask=True)
        self.here("b")

        await press(block.remove, block.plan.folders[0])
        await self.offered.call_args.args[1]()

        self.library.set_location_write_to.assert_not_called()
        self.library.set_location_order.assert_called_once_with(["a", "b"])

    async def test_undoing_two_removals_in_either_order_restores_the_list(self) -> None:
        first = self.block(row("a"), row("b"), row("c"))
        await press(first.remove, first.plan.folders[0])
        undo_a = self.offered.call_args.args[1]
        second = self.block(row("b"), row("c"))
        await press(second.remove, second.plan.folders[0])
        undo_b = self.offered.call_args.args[1]

        self.here("c", "a")
        await undo_a()
        self.library.set_location_order.assert_called_with(["a", "c"])
        self.here("a", "c", "b")
        await undo_b()

        self.library.set_location_order.assert_called_with(["a", "b", "c"])

    async def test_a_second_press_on_a_row_already_removed_does_nothing(self) -> None:
        block = self.block(row("a"), row("b"))
        gone = block.plan.folders[0]

        await press(block.remove, gone)
        await press(block.remove, gone)

        self.library.delete_location.assert_called_once_with("a")

    async def test_a_refused_order_draws_the_list_as_it_was_saved(self) -> None:
        self.library.set_location_order.side_effect = OSError("busy")
        block = self.block(row("a"), row("b"))

        await block.moved({"from": 0, "to": 1})

        self.notified.assert_called_once()
        self.redraw.assert_called_once()

    async def test_a_refused_switch_draws_it_as_it_was_saved(self) -> None:
        self.library.put_config.side_effect = OSError("busy")
        block = self.block(row("a"), row("b"), ask=True)

        await block.ask_changed(Mock(value=False))

        self.notified.assert_called_once()
        self.redraw.assert_called_once()

    async def test_a_remove_that_fails_says_so_and_offers_no_undo(self) -> None:
        self.library.delete_location.side_effect = OSError("busy")
        block = self.block(row("a"))

        await press(block.remove, block.plan.folders[0])

        self.notified.assert_called_once()
        self.offered.assert_not_called()

    async def test_a_copy_is_given_its_own_id_only_once_confirmed(self) -> None:
        copy = {"path": "/games/b/Attack", "used_path": "/games/a/Attack"}
        with patch.object(folders.confirm, "ask", new=AsyncMock(return_value=True)):
            self.assertTrue(await folders.adopt_copy(self.library, "b", copy))

        self.library.adopt_shadowed.assert_called_once_with("b", "/games/b/Attack")

    async def test_a_declined_confirm_writes_nothing_to_the_game_folder(self) -> None:
        with patch.object(folders.confirm, "ask", new=AsyncMock(return_value=False)):
            self.assertFalse(await folders.adopt_copy(self.library, "b", {"path": "/x"}))

        self.library.adopt_shadowed.assert_not_called()

    async def test_an_adopt_that_fails_says_so_and_reports_nothing_done(self) -> None:
        self.library.adopt_shadowed.side_effect = OSError("busy")
        with patch.object(folders.confirm, "ask", new=AsyncMock(return_value=True)):
            self.assertFalse(await folders.adopt_copy(self.library, "b", {"path": "/x"}))

        self.notified.assert_called_once()


class PageIndexTests(unittest.TestCase):
    def test_a_page_may_name_several_features_and_is_held_if_any_is(self) -> None:
        for features in ([install_identity.LIBRARY], [install_identity.FRONTEND]):
            with self.subTest(features=features):
                held = [one[0] for _group, one in settings_page.pages_for_features(features)]
                self.assertIn(folders.KEY, held)

        held = [one[0] for _group, one in
                settings_page.pages_for_features([install_identity.DEVICES])]
        self.assertNotIn(folders.KEY, held)

    def test_an_install_that_has_not_said_has_every_page_and_one_for_nothing_has_core(self) -> None:
        def held(features):
            return [one[0] for _group, one in settings_page.pages_for_features(features)]

        self.assertIn(folders.KEY, held(None))
        self.assertNotIn(folders.KEY, held([]))

    def test_the_page_is_drawn_by_the_folders_module_with_the_settings_it_is_home_to(self) -> None:
        draw = AsyncMock(return_value=lambda: None)

        with patch.object(folders, "page", new=draw):
            asyncio.run(settings_page.PAGES[folders.KEY](Mock(), MagicMock(), {}))

        draw.assert_awaited_once()
        self.assertTrue(callable(draw.await_args_list[0].args[3]))

    def test_the_page_has_its_note(self) -> None:
        self.assertIn(folders.KEY, settings_page.PAGE_NOTES)


class DrawTests(unittest.TestCase):
    """The rows and lines the page puts on screen, read back from what was drawn."""

    def drawn(self, *rows: dict[str, Any], ask: bool = True,
              importing: dict[str, Any] | None = None,
              below: list[tuple[Any, ...]] | None = None,
              newest_import: tuple[str, str] | None = None) -> list[str]:
        block = make(*rows, ask=ask)
        with _PAGE, patch.object(ui, "run_javascript"):
            holder = ui.element()
            with holder:
                folders.draw(block, importing, below, newest_import)
        said = []
        for one in holder.descendants():
            if isinstance(one, (ui.label, ui.button)) and one.text:
                said.append(one.text.replace("\u200e", ""))
            elif isinstance(one, ui.switch):
                said.append(f"switch:{one.value}")
        return said

    def test_the_newest_import_is_a_line_beside_import_a_library(self) -> None:
        newest = ("PinballY, 12 to review", "/report/job-1")

        said = self.drawn(row("a"), importing={}, newest_import=newest)

        self.assertIn(newest[0], said)

    def test_the_empty_library_says_it_too(self) -> None:
        newest = ("PinballY, imported 8 Oct", "/report/job-1")

        said = self.drawn(importing={}, newest_import=newest)

        self.assertIn(newest[0], said)

    def test_no_import_kept_draws_no_line(self) -> None:
        said = self.drawn(row("a"), importing={})

        self.assertFalse([one for one in said if "to review" in one or "imported" in one])

    def test_one_folder_draws_no_chip_and_no_switch(self) -> None:
        said = self.drawn(row("a", write_to=True))

        self.assertIn("/games/a", said)
        self.assertIn("3 games", said)
        self.assertNotIn(folders.t("manager.folders.new_games"), said)
        self.assertFalse([one for one in said if one.startswith("switch:")])
        self.assertNotIn(folders.t("manager.folders.priority"), said)

    def test_two_folders_while_asking_draw_the_switch_on_and_the_priority_line(self) -> None:
        said = self.drawn(row("a"), row("b"), ask=True)

        self.assertIn("switch:True", said)
        self.assertIn(folders.t("manager.folders.priority"), said)
        self.assertNotIn(folders.t("manager.folders.new_games"), said)

    def test_two_folders_not_asking_draw_the_chip_on_one_and_the_switch_off(self) -> None:
        said = self.drawn(row("a"), row("b", write_to=True), ask=False)

        self.assertIn("switch:False", said)
        self.assertEqual(said.count(folders.t("manager.folders.new_games")), 1)
        chip = said.index(folders.t("manager.folders.new_games"))
        self.assertEqual(said[chip - 1], "3 games")

    def test_a_folder_that_is_away_says_why_in_a_chip_and_gives_no_count(self) -> None:
        said = self.drawn(row("a", write_to=True),
                          row("b", state=path_state.NOT_ANSWERING, games=0), ask=False)

        self.assertIn(folders.t("word.not_answering"), said)
        self.assertEqual(said.count("3 games"), 1)
        self.assertNotIn("No games", said)

    def test_nothing_able_to_take_a_game_says_so_under_the_priority_line(self) -> None:
        said = self.drawn(row("a", state=path_state.MISSING), row("b", state=path_state.READ_ONLY),
                          ask=False)

        self.assertIn(folders.t("manager.folders.nowhere"), said)
        self.assertNotIn(folders.t("manager.folders.new_games"), said)

    def test_a_fresh_library_leads_with_the_fix(self) -> None:
        said = self.drawn()

        self.assertIn(folders.t("manager.folders.none_yet"), said)
        self.assertIn(folders.t("manager.folders.add_folder"), said)
        self.assertNotIn(folders.t("manager.folders.import"), said)

    def test_the_import_button_is_offered_only_where_the_importer_runs(self) -> None:
        self.assertIn(folders.t("manager.folders.import"),
                      self.drawn(row("a"), importing={"key": "import"}))
        self.assertNotIn(folders.t("manager.folders.import"), self.drawn(row("a")))

    def test_the_duplicates_are_a_link_in_the_caption(self) -> None:
        said = self.drawn(row("a"), row("b", shadowed=2))

        self.assertIn("2 duplicates", said)

    def test_the_library_group_is_named_whether_or_not_other_groups_follow_it(self) -> None:
        later = [(panel.HEADING, "Later"), panel.lede("A line")]
        heading = folders.t("manager.folders.heading_library")

        for found in ((row("a"),), ()):
            with self.subTest(folders=len(found)):
                self.assertIn(heading, self.drawn(*found, below=later))
                self.assertIn(heading, self.drawn(*found))


class DialogTests(unittest.TestCase):
    """The one dialog Add and Change share."""

    def opened(self, **kwargs: Any) -> list[str]:
        before = {one for one in _PAGE.client.layout.descendants()
                  if isinstance(one, ui.dialog)}
        with _PAGE, patch.object(ui, "run_javascript"):
            folders.folder_dialog(Mock(), AsyncMock(), **kwargs)
        box = next(one for one in _PAGE.client.layout.descendants()
                   if isinstance(one, ui.dialog) and one not in before)
        self.addCleanup(box.delete)
        said = []
        for one in box.descendants():
            if isinstance(one, (ui.label, ui.button)) and one.text:
                said.append(one.text)
            elif isinstance(one, ui.radio):
                said.extend(one.options.values() if isinstance(one.options, dict)
                            else one.options)
        return said

    def test_add_asks_for_the_folder_and_nothing_about_what_it_holds(self) -> None:
        said = self.opened()

        self.assertIn(folders.t("manager.folders.add_folder"), said)
        self.assertIn(folders.t("word.add"), said)
        self.assertNotIn(folders.t("manager.folders.many_games"), said)

    def test_change_adds_what_the_folder_contains_and_saves(self) -> None:
        said = self.opened(current=folders.Folder.read(row("a")))

        self.assertIn(folders.t("manager.folders.change_title"), said)
        self.assertIn(folders.t("word.save"), said)
        self.assertIn(folders.t("manager.folders.many_games"), said)
        self.assertIn(folders.t("manager.folders.one_game_folder"), said)
        self.assertNotIn(folders.t("word.add"), said)


if __name__ == "__main__":
    unittest.main()
