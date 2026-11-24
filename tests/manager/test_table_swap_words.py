"""What the import confirmation holds of a replacement: the answers so far, what they ask the
plan and the import for, and what the dialog and the notice call the tables."""

from __future__ import annotations

import unittest
from unittest import mock

from nicegui import ui

from common.i18n import t
from manager.kit import game_tables
from manager.kit.data import Library
from manager.library import import_dialog, import_swap

_PAGE = ui.element()

TABLES = [{"id": "t1", "label": "1.1.0 · Rascal", "filename": "Cactus 1.1.vpx",
           "default": True},
          {"id": "t2", "label": "Cactus Other.vpx", "filename": "Cactus Other.vpx",
           "default": False}]
CHOICE = {"tables": TABLES, "replace": True, "table": "t1"}
PLAN = {"items": [
    {"kind": "table", "name": "Cactus 1.2.vpx", "swap": {
        "label": "1.1.0 · Rascal", "filename": "Cactus 1.1.vpx",
        "files": [{"kind": "ini", "carry": True}, {"kind": "script", "carry": False}]}}]}


class SwappingTests(unittest.TestCase):
    def test_a_game_with_no_table_to_replace_asks_nothing(self) -> None:
        self.assertIsNone(import_swap.Swapping.of(None))
        self.assertIsNone(import_swap.Swapping.of({"tables": [], "replace": False, "table": ""}))

    def test_it_starts_where_the_plan_does(self) -> None:
        swapping = import_swap.Swapping.of(CHOICE)

        assert swapping is not None
        self.assertEqual((swapping.replace, swapping.table, swapping.carry), (True, "t1", None))

    def test_replacing_asks_for_the_table_and_what_is_ticked(self) -> None:
        swapping = import_swap.Swapping.of(CHOICE, instead=True)
        assert swapping is not None
        swapping.settle(PLAN)

        self.assertEqual(swapping.asked(), {"add_table": False, "replace_table": "t1",
                                            "carry": ["ini"], "instead": True})

    def test_keeping_both_asks_for_an_added_table_and_nothing_to_carry(self) -> None:
        swapping = import_swap.Swapping.of(CHOICE, instead=True)
        assert swapping is not None
        swapping.replace = False

        self.assertEqual(swapping.asked(), {"add_table": True, "replace_table": "",
                                            "carry": None, "instead": False})

    def test_the_plan_s_ticks_are_taken_once_and_a_person_s_are_kept(self) -> None:
        swapping = import_swap.Swapping.of(CHOICE)
        assert swapping is not None
        swapping.settle(PLAN)
        swapping.carry = ["ini", "script"]

        swapping.settle(PLAN)

        self.assertEqual(swapping.carry, ["ini", "script"])

    def test_an_unticked_table_row_carries_nothing_and_keeps_the_choice(self) -> None:
        swapping = import_swap.Swapping.of(CHOICE)
        assert swapping is not None
        swapping.settle(PLAN)

        self.assertEqual(swapping.asked(table_on=False)["carry"], [])
        self.assertEqual(swapping.asked(table_on=False)["replace_table"], "t1")
        self.assertEqual(swapping.asked(table_on=True)["carry"], ["ini"])

    def test_the_ticks_are_not_taken_from_a_plan_whose_table_row_is_off(self) -> None:
        swapping = import_swap.Swapping.of(CHOICE)
        assert swapping is not None

        swapping.settle({"items": [{**PLAN["items"][0], "index": 0}]}, table_on=False)

        self.assertIsNone(swapping.carry)

    def test_the_plan_s_ticks_are_not_taken_while_keeping_both(self) -> None:
        swapping = import_swap.Swapping.of({**CHOICE, "replace": False})
        assert swapping is not None

        swapping.settle(PLAN)

        self.assertIsNone(swapping.carry)

    def test_a_table_is_named_by_its_label(self) -> None:
        swapping = import_swap.Swapping.of(CHOICE)
        assert swapping is not None

        self.assertEqual(swapping.label_of("t2"), "Cactus Other.vpx")
        self.assertEqual(swapping.label_of("nothing"), "")


class WordsTests(unittest.TestCase):
    def test_the_new_table_is_its_release_where_one_is_named_else_its_file(self) -> None:
        self.assertEqual(import_swap.new_name(PLAN, "1.4.0 · Ahr1man"), "1.4.0 · Ahr1man")
        self.assertEqual(import_swap.new_name(PLAN), "Cactus 1.2.vpx")

    def test_the_title_names_both_tables_while_replacing(self) -> None:
        swapping = import_swap.Swapping.of(CHOICE)

        said = import_swap.title(PLAN, swapping, "1.4.0 · Ahr1man")

        self.assertIn("1.1.0 · Rascal", said)
        self.assertIn("1.4.0 · Ahr1man", said)

    def test_there_is_no_title_while_keeping_both_or_with_no_choice(self) -> None:
        swapping = import_swap.Swapping.of({**CHOICE, "replace": False})

        self.assertEqual(import_swap.title(PLAN, swapping), "")
        self.assertEqual(import_swap.title(PLAN, None), "")

    def test_the_one_button_says_what_the_choice_does(self) -> None:
        replacing = import_swap.Swapping.of(CHOICE)
        keeping = import_swap.Swapping.of({**CHOICE, "replace": False})

        self.assertEqual(import_swap.confirm_label(replacing), t("word.replace"))
        self.assertEqual(import_swap.confirm_label(keeping), t("manager.import_swap.keep_both"))
        self.assertEqual(import_swap.confirm_label(None), t("manager.import_dialog.import"))


class TableRowTests(unittest.TestCase):
    ITEMS = {"items": [{"index": 0, "kind": "table", "swap": {"label": "x"}},
                       {"index": 1, "kind": "ini"}]}

    def test_the_table_row_is_on_unless_it_was_unticked(self) -> None:
        self.assertTrue(import_swap.table_on(self.ITEMS, {0: True, 1: False}))
        self.assertFalse(import_swap.table_on(self.ITEMS, {0: False, 1: True}))

    def test_a_plan_with_nothing_to_swap_is_always_on(self) -> None:
        self.assertTrue(import_swap.table_on({"items": [{"index": 0, "kind": "table"}]},
                                             {0: False}))

    def test_an_unticked_row_is_a_plain_import_with_no_replace_title(self) -> None:
        swapping = import_swap.Swapping.of(CHOICE)

        self.assertEqual(import_swap.title(PLAN, swapping, on=False), "")
        self.assertEqual(import_swap.confirm_label(swapping, False),
                         t("manager.import_dialog.import"))
        self.assertEqual(import_swap.confirm_label(swapping, True), t("word.replace"))


class SaidTests(unittest.TestCase):
    def swapped(self, kept: list[dict]) -> dict:
        return {"game": "g1", "table": {"id": "t1"}, "kept": kept}

    def test_a_swap_with_copies_offers_undo_that_swaps_back_to_them(self) -> None:
        library = mock.Mock()
        with mock.patch.object(import_swap.undo, "offer") as offer:
            import_swap.said(library, self.swapped([{"id": "c1"}, {"id": "c2"}]),
                             old="1.1.0", new="1.2.0")

        offer.assert_called_once()
        self.assertIn("1.1.0", offer.call_args.args[0])

    def test_a_swap_whose_import_then_failed_says_so_and_still_offers_undo(self) -> None:
        with mock.patch.object(import_swap.undo, "offer") as offer:
            import_swap.said(mock.Mock(), self.swapped([{"id": "c1"}]),
                             old="1.1.0", new="1.2.0", partly=True)

        self.assertEqual(offer.call_args.args[0],
                         t("manager.import_swap.replaced_partly", old="1.1.0", new="1.2.0"))

    def test_a_swap_with_nothing_kept_only_says_so(self) -> None:
        with mock.patch.object(import_swap.undo, "offer") as offer, \
                mock.patch.object(import_swap.ui, "notify") as notify:
            import_swap.said(mock.Mock(), self.swapped([]), old="1.1.0", new="1.2.0")

        offer.assert_not_called()
        notify.assert_called_once()


class SwapKeptTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_failure_after_the_swap_reads_the_page_again_and_offers_undo(self) -> None:
        done = mock.Mock()
        kept = [{"game": "g1", "table": {"id": "t1"}, "kept": [{"id": "c1"}]}]
        with mock.patch.object(import_dialog.import_swap, "said") as said, _PAGE:
            await import_dialog._swap_kept(mock.Mock(), kept, None, PLAN, None, done)

        done.assert_called_once()
        self.assertTrue(done.call_args.args[0]["failed"])
        self.assertTrue(said.call_args.kwargs["partly"])


class RestoredBackupsTests(unittest.TestCase):
    def test_a_restore_forgets_each_games_table_list(self) -> None:
        library = Library(mock.Mock())
        library.tables["g1"] = [{"id": "t1"}]

        library.restored_backups()

        self.assertEqual(library.tables, {})


class CanSwapTests(unittest.TestCase):
    def test_a_table_kept_in_the_games_folder_and_there_can_be(self) -> None:
        self.assertTrue(game_tables.can_swap({"form": "contained", "available": True}))

    def test_one_outside_it_or_gone_cannot(self) -> None:
        self.assertFalse(game_tables.can_swap({"form": "referenced", "available": True}))
        self.assertFalse(game_tables.can_swap({"form": "contained", "available": False}))
        self.assertFalse(game_tables.can_swap(
            {"form": "contained", "available": True, "absent_since": "2026-10-01"}))
        self.assertFalse(game_tables.can_swap(None))
