"""What the import sheet shows when a drop has more than one answer: variants to choose among,
files with no place yet, extras, and what was left out."""

from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import Mock, patch

from manager.kit import candidates
from manager.library import drop_sheet, import_dialog


def _item(index: int = 0, **more: Any) -> dict:
    return {"index": index, "kind": "media", "label": "Media", "media_kind": "wheel",
            "name": "wheel.png", "member": "wheel.png", "size": 40_000,
            "action": "replace_media", "destination": "/games/MM/medias/wheel.png",
            "replaces": "", "default_enabled": True, "extra": False, **more}


def _candidate(member: str, width: int = 512, height: int = 512, size: int = 86_000) -> dict:
    return {"member": member, "name": member.rsplit("/", 1)[-1], "size": size,
            "basis": "named", "width": width, "height": height}


def _group(key: str = "media:wheel", label: str = "Wheel", **more: Any) -> dict:
    return {"key": key, "kind": "media", "media_kind": "wheel", "label": label,
            "candidates": [_candidate("pack/(Wheel) A.png"),
                           _candidate("pack/(Wheel) B.png"),
                           _candidate("pack/(Wheel) C.png", 600, 300, 40_000)],
            "preselected": "", "extra": False, "table_id": "", **more}


def _unclaimed(member: str = "IMG_0002.png") -> dict:
    return {"member": member, "name": member, "size": 9_000,
            "choices": [{"kind": "media", "media_kind": "backglass", "label": "Backglass",
                         "table_id": "", "suggested": False},
                        {"kind": "media", "media_kind": "topper", "label": "Topper",
                         "table_id": "", "suggested": False}]}


def _plan(**more: Any) -> dict:
    return {"game_dir": "/games/MM", "new_game_dir_name": "", "items": [], "blocked": [],
            "groups": [], "unclaimed": [], "left_out": [], "tables": [], "scope": "",
            "choice": None, **more}


def _drawn(draw: Any, *args: Any, **kwargs: Any) -> Mock:
    with patch.object(drop_sheet, "ui") as ui, patch.object(candidates, "thumb"):
        draw(*args, **kwargs)
    return ui


def _said(ui: Mock) -> list[str]:
    return [one.args[0] for one in ui.label.call_args_list]


class SingleFile(unittest.TestCase):
    def test_a_single_declared_file_is_a_sheet_with_nothing_to_choose(self) -> None:
        sheet = drop_sheet.Sheet(_plan(items=[_item()]))

        self.assertTrue(sheet.single())
        self.assertEqual([], sheet.sections())
        self.assertEqual("", sheet.reason())

    def test_a_second_thing_to_say_makes_it_more_than_a_single_file(self) -> None:
        for more in ({"groups": [_group()]}, {"unclaimed": [_unclaimed()]},
                     {"left_out": [{"member": "a.txt", "name": "a.txt", "reason_key": "k",
                                    "reason": "No place"}]},
                     {"blocked": [{"kind": "rom", "reason": "No ROM name"}]}):
            with self.subTest(more=list(more)):
                self.assertFalse(drop_sheet.Sheet(_plan(items=[_item()], **more)).single())


class Groups(unittest.TestCase):
    def test_a_group_draws_a_radio_for_each_candidate_and_one_for_none(self) -> None:
        sheet = drop_sheet.Sheet(_plan(groups=[_group()]))

        ui = _drawn(drop_sheet.draw_group, sheet, sheet.plan["groups"][0],
                    upload_id="u1", changed=lambda: None)

        self.assertEqual(4, ui.radio.call_count)
        said = _said(ui)
        self.assertIn("512 x 512", said)
        self.assertIn("600 x 300", said)
        self.assertIn("None of These", said)

    def test_a_candidate_leads_with_its_picture_from_inside_the_archive(self) -> None:
        sheet = drop_sheet.Sheet(_plan(groups=[_group()]))

        with patch.object(drop_sheet, "ui"), patch.object(candidates, "thumb") as thumb:
            drop_sheet.draw_group(sheet, sheet.plan["groups"][0], upload_id="u1",
                                  changed=lambda: None)

        self.assertEqual(
            "/api/v1/uploads/u1/picture?name=pack%2F%28Wheel%29+A.png&size=256",
            thumb.call_args_list[0].args[0])

    def test_import_says_what_to_choose_until_the_group_has_an_answer(self) -> None:
        sheet = drop_sheet.Sheet(_plan(groups=[_group()]))

        self.assertEqual("Choose One Wheel", sheet.reason())
        sheet.pick("media:wheel", "pack/(Wheel) B.png")
        self.assertEqual("", sheet.reason())

    def test_none_of_these_is_an_answer(self) -> None:
        sheet = drop_sheet.Sheet(_plan(groups=[_group()]))

        sheet.pick("media:wheel", "")

        self.assertEqual("", sheet.reason())
        self.assertEqual({"media:wheel": ""}, sheet.asked()["picks"])

    def test_a_preselected_candidate_starts_picked_and_asks_for_a_plan_with_it(self) -> None:
        sheet = drop_sheet.Sheet(_plan(groups=[_group(preselected="pack/(Wheel) A.png")]))

        self.assertEqual("", sheet.reason())
        self.assertEqual({"media:wheel": "pack/(Wheel) A.png"}, sheet.asked()["picks"])
        self.assertTrue(sheet.seeded)

    def test_a_pick_that_a_fresh_plan_no_longer_offers_is_dropped(self) -> None:
        sheet = drop_sheet.Sheet(_plan(groups=[_group()]))
        sheet.pick("media:wheel", "pack/(Wheel) B.png")

        sheet.take(_plan(groups=[]))

        self.assertNotIn("picks", sheet.asked())

    def test_the_picked_file_is_drawn_by_its_radio_and_not_again_as_a_row(self) -> None:
        picked = _item(member="pack/(Wheel) B.png", name="(Wheel) B.png",
                       replaces="replaces wheel.png")
        sheet = drop_sheet.Sheet(_plan(items=[picked], groups=[_group()]))
        sheet.pick("media:wheel", "pack/(Wheel) B.png")

        (section,) = sheet.sections()

        self.assertEqual([], section.items)
        self.assertEqual("replaces wheel.png", sheet.replaces(sheet.plan["groups"][0]))

    def test_choosing_a_candidate_asks_for_a_new_plan(self) -> None:
        sheet = drop_sheet.Sheet(_plan(groups=[_group()]))
        changed = Mock()
        ui = _drawn(drop_sheet.draw_group, sheet, sheet.plan["groups"][0],
                    upload_id="u1", changed=changed)

        radios = ui.radio.return_value.props.return_value.on_value_change
        radios.call_args_list[1].args[0](Mock(value="on"))

        self.assertEqual("pack/(Wheel) B.png", sheet.picks["media:wheel"])
        changed.assert_called_once_with()


class Unclaimed(unittest.TestCase):
    def test_an_unclaimed_file_starts_unticked_and_cannot_be_ticked_before_it_has_a_place(
            self) -> None:
        sheet = drop_sheet.Sheet(_plan(items=[_item(), _item(1, name="b.png", member="b.png")],
                                       unclaimed=[_unclaimed()]))

        ui = _drawn(drop_sheet.draw_unclaimed, sheet, sheet.plan["unclaimed"][0],
                    upload_id="u1", changed=lambda: None)

        box = ui.checkbox.call_args
        self.assertFalse(box.kwargs["value"])
        self.assertIn("disable", ui.checkbox.return_value.props.call_args.args[0])

    def test_it_is_offered_where_it_could_go_and_leave_out(self) -> None:
        sheet = drop_sheet.Sheet(_plan(unclaimed=[_unclaimed()]))

        options = sheet.options("IMG_0002.png")

        self.assertEqual(
            ["Select File Type", "Backglass", "Topper", "Skip"],
            list(options.values()))

    def test_a_lone_script_reads_the_same_as_a_picture_with_no_place(self) -> None:
        script = {"member": "table.vbs", "name": "table.vbs", "size": 90,
                  "choices": [{"kind": "script", "media_kind": "", "label": "Script for Table",
                               "table_id": "", "suggested": False}]}
        sheet = drop_sheet.Sheet(_plan(unclaimed=[script]))

        self.assertEqual("Select File Type", sheet.options("table.vbs")[drop_sheet.CHOOSE])
        self.assertEqual("Select File Type", drop_sheet.go_label(sheet, "Import", 1))

    def test_the_button_says_select_file_type_while_a_picture_has_no_place(self) -> None:
        sheet = drop_sheet.Sheet(_plan(unclaimed=[_unclaimed()]))

        self.assertEqual("Select File Type", drop_sheet.go_label(sheet, "Import", 1))

    def test_choosing_a_place_asks_for_a_plan_that_claims_it_on_the_declared_kind(self) -> None:
        sheet = drop_sheet.Sheet(_plan(unclaimed=[_unclaimed()]))
        key = next(one for one, label in sheet.options("IMG_0002.png").items()
                   if label == "Topper")

        sheet.place("IMG_0002.png", key)

        self.assertEqual([{"member": "IMG_0002.png", "kind": "media", "media_kind": "topper"}],
                         sheet.asked()["assignments"])

    def test_the_other_ticks_stay_where_they_were_when_a_place_is_chosen(self) -> None:
        old = _plan(items=[_item(0), _item(1, name="b.png", member="b.png")])
        fresh = _plan(items=[_item(0), _item(1, name="b.png", member="b.png"),
                             _item(2, name="IMG_0002.png", member="IMG_0002.png",
                                   media_kind="topper")])

        kept = import_dialog.kept_ticks({0: True, 1: False}, old, fresh)

        self.assertEqual({0: True, 1: False, 2: True}, kept)

    def test_a_place_once_offered_stays_offered_after_the_file_is_claimed(self) -> None:
        sheet = drop_sheet.Sheet(_plan(unclaimed=[_unclaimed()]))
        sheet.place("IMG_0002.png", list(sheet.options("IMG_0002.png"))[2])

        sheet.take(_plan(items=[_item(member="IMG_0002.png", name="IMG_0002.png")]))

        self.assertIn("Topper", sheet.options("IMG_0002.png").values())
        self.assertEqual(sheet.placed("IMG_0002.png"), sheet.shown("IMG_0002.png"))

    def test_leave_out_is_the_same_as_unticking(self) -> None:
        sheet = drop_sheet.Sheet(_plan(unclaimed=[_unclaimed()]))
        topper = list(sheet.options("IMG_0002.png"))[2]
        sheet.place("IMG_0002.png", topper)

        sheet.leave("IMG_0002.png")

        self.assertNotIn("assignments", sheet.asked())
        self.assertEqual(drop_sheet.LEAVE, sheet.shown("IMG_0002.png"))

    def test_a_row_the_person_placed_draws_its_choice_on_the_item(self) -> None:
        sheet = drop_sheet.Sheet(_plan(unclaimed=[_unclaimed()]))
        sheet.place("IMG_0002.png", list(sheet.options("IMG_0002.png"))[1])
        item = _item(member="IMG_0002.png", name="IMG_0002.png", media_kind="backglass")

        self.assertTrue(sheet.has_place(item))
        self.assertFalse(sheet.has_place(_item(member="other.png")))


def _suggesting(member: str = "IMG_0001.png", slot: str = "backglass") -> dict:
    """An unclaimed file the engine read the shape of: `slot` is the one it suggests."""
    found = _unclaimed(member)
    for choice in found["choices"]:
        choice["suggested"] = choice["media_kind"] == slot
    return found


class ShapeSuggestions(unittest.TestCase):
    def test_a_suggested_place_is_taken_up_and_asks_for_a_plan_that_carries_it(self) -> None:
        sheet = drop_sheet.Sheet(_plan(unclaimed=[_suggesting()]))

        self.assertTrue(sheet.seeded)
        self.assertEqual(drop_sheet.choice_key({"kind": "media", "media_kind": "backglass"}),
                         sheet.placed("IMG_0001.png"))
        self.assertEqual([{"member": "IMG_0001.png", "kind": "media", "media_kind": "backglass"}],
                         sheet.asked()["assignments"])

    def test_the_chip_stands_while_the_suggestion_does(self) -> None:
        sheet = drop_sheet.Sheet(_plan(unclaimed=[_suggesting()]))

        self.assertTrue(sheet.suggests("IMG_0001.png"))

    def test_a_place_the_person_chose_takes_the_chip_away(self) -> None:
        sheet = drop_sheet.Sheet(_plan(unclaimed=[_suggesting()]))
        topper = next(key for key, label in sheet.options("IMG_0001.png").items()
                      if label == "Topper")

        sheet.place("IMG_0001.png", topper)

        self.assertFalse(sheet.suggests("IMG_0001.png"))
        self.assertEqual([{"member": "IMG_0001.png", "kind": "media", "media_kind": "topper"}],
                         sheet.asked()["assignments"])

    def test_the_chip_stays_after_the_plan_that_carries_the_suggestion(self) -> None:
        sheet = drop_sheet.Sheet(_plan(unclaimed=[_suggesting()]))

        sheet.take(_plan(items=[_item(member="IMG_0001.png", name="IMG_0001.png",
                                      media_kind="backglass")]))

        self.assertTrue(sheet.suggests("IMG_0001.png"))
        self.assertFalse(sheet.seeded)

    def test_a_suggestion_the_person_gave_up_is_not_made_again(self) -> None:
        sheet = drop_sheet.Sheet(_plan(unclaimed=[_suggesting()]))
        sheet.place("IMG_0001.png", drop_sheet.CHOOSE)

        sheet.take(_plan(unclaimed=[_suggesting()]))

        self.assertEqual("", sheet.placed("IMG_0001.png"))
        self.assertNotIn("assignments", sheet.asked())
        self.assertFalse(sheet.seeded)

    def test_a_file_left_out_is_not_given_its_suggestion_back(self) -> None:
        sheet = drop_sheet.Sheet(_plan(unclaimed=[_suggesting()]))
        sheet.leave("IMG_0001.png")

        sheet.take(_plan(unclaimed=[_suggesting()]))

        self.assertEqual(drop_sheet.LEAVE, sheet.shown("IMG_0001.png"))
        self.assertFalse(sheet.suggests("IMG_0001.png"))

    def test_a_file_with_no_suggestion_gets_no_place_and_no_chip(self) -> None:
        sheet = drop_sheet.Sheet(_plan(unclaimed=[_unclaimed("IMG_0002.png")]))

        self.assertFalse(sheet.seeded)
        self.assertEqual("", sheet.placed("IMG_0002.png"))
        self.assertFalse(sheet.suggests("IMG_0002.png"))

    def test_a_lone_picture_with_a_place_keeps_the_choice_of_place(self) -> None:
        sheet = drop_sheet.Sheet(_plan(unclaimed=[_suggesting()]))

        sheet.take(_plan(items=[_item(member="IMG_0001.png", name="IMG_0001.png",
                                      media_kind="backglass")]))

        self.assertFalse(sheet.single())

    def test_only_a_suggested_file_draws_the_chip(self) -> None:
        sheet = drop_sheet.Sheet(_plan(unclaimed=[_suggesting(), _unclaimed("IMG_0002.png")]))

        self.assertEqual(["Suggested"], _said(_drawn(drop_sheet.draw_suggested, sheet,
                                                     "IMG_0001.png")))
        self.assertEqual([], _said(_drawn(drop_sheet.draw_suggested, sheet, "IMG_0002.png")))


class Extras(unittest.TestCase):
    def test_an_extra_sits_in_its_own_section_and_arrives_unticked(self) -> None:
        extra = _item(1, name="Manual.docx", member="Manual.docx", kind="other",
                      media_kind="", default_enabled=False, extra=True)
        sheet = drop_sheet.Sheet(_plan(items=[_item(), extra]))

        media, with_ = sheet.sections()

        self.assertEqual(("media", "with"), (media.key, with_.key))
        self.assertEqual([_item()], media.items)
        self.assertEqual([extra], with_.items)
        self.assertEqual({0: True, 1: False}, import_dialog.initial_ticks(sheet.plan))

    def test_an_extra_group_is_answered_by_none_of_these_until_one_is_picked(self) -> None:
        sheet = drop_sheet.Sheet(_plan(items=[_item()], groups=[
            _group("media:backglass#extra", "Backglass", extra=True)]))

        self.assertEqual("", sheet.reason())
        (media,) = sheet.sections()
        self.assertEqual(1, len(media.groups))


class Refused(unittest.TestCase):
    BLOCKED = {"kind": "rom", "reason": "This game has no ROM name"}
    LEFT_OUT = {"member": "x/notes.txt", "name": "notes.txt", "reason_key": "k",
                "reason": "A text file that isn't a README"}
    NO_FIT = {"member": "big.png", "name": "big.png",
              "reason_key": "arrival.left_out.doesnt_fit", "reason": "Too large for a wheel"}

    def test_what_the_plan_refuses_sits_in_the_section_it_would_have_gone_to(self) -> None:
        sheet = drop_sheet.Sheet(_plan(items=[_item()], blocked=[self.BLOCKED],
                                       left_out=[self.LEFT_OUT, self.NO_FIT]))

        media, with_ = sheet.sections()

        self.assertEqual(["Too large for a wheel"], [one["reason"] for one in media.refused])
        self.assertEqual(["ROM", "notes.txt"], [one["name"] for one in with_.refused])

    def test_the_refusals_are_a_line_each_with_the_name_and_the_reason(self) -> None:
        sheet = drop_sheet.Sheet(_plan(items=[_item()], blocked=[self.BLOCKED],
                                       left_out=[self.LEFT_OUT]))

        lines = sheet.refusals()

        self.assertEqual(2, len(lines))
        self.assertIn("ROM - This game has no ROM name", lines)
        self.assertIn("notes.txt - A text file that isn't a README", lines)


class TableLine(unittest.TestCase):
    TABLES = [{"id": "t1", "label": "Alpha", "default": True},
              {"id": "t2", "label": "Bravo", "default": False}]

    def test_there_is_no_line_where_the_plan_lists_no_tables(self) -> None:
        sheet = drop_sheet.Sheet(_plan(items=[_item()]))

        ui = _drawn(drop_sheet.draw_table_line, sheet, {}, lambda: None)

        ui.row.assert_not_called()

    def test_media_dropped_on_a_table_asks_what_it_is_named_for(self) -> None:
        sheet = drop_sheet.Sheet(_plan(items=[_item()], tables=self.TABLES, scope="table"))
        asked = {"table_id": "t2"}
        changed = Mock()

        with patch.object(drop_sheet.panel, "select") as select, \
                patch.object(drop_sheet, "ui"):
            drop_sheet.draw_table_line(sheet, asked, changed)

        options, value, handler = select.call_args.args
        self.assertEqual({"table": "Bravo", "game": "The Whole Game"}, options)
        self.assertEqual("table", value)
        handler(Mock(value="game"))
        self.assertEqual("game", asked["scope"])
        changed.assert_called_once_with()

    def test_a_per_table_file_named_for_no_table_asks_which_table(self) -> None:
        sheet = drop_sheet.Sheet(_plan(items=[_item(kind="pov", media_kind="")],
                                       tables=self.TABLES))
        asked: dict[str, Any] = {}
        changed = Mock()

        with patch.object(drop_sheet.panel, "select") as select, \
                patch.object(drop_sheet, "ui"):
            drop_sheet.draw_table_line(sheet, asked, changed)

        options, value, handler = select.call_args.args
        self.assertEqual({"t1": "Alpha", "t2": "Bravo"}, options)
        self.assertEqual("t1", value)
        handler(Mock(value="t2"))
        self.assertEqual("t2", asked["table_id"])
        changed.assert_called_once_with()

    def test_the_table_choice_stays_once_the_table_is_chosen(self) -> None:
        sheet = drop_sheet.Sheet(_plan(items=[_item(kind="pov", media_kind="")],
                                       tables=self.TABLES))

        sheet.take(_plan(items=[_item(kind="pov", media_kind="")]))

        self.assertEqual(self.TABLES, sheet.tables)


class Titles(unittest.TestCase):
    def test_a_slot_target_names_the_slot_and_the_game(self) -> None:
        self.assertEqual("Wheel for Medieval Madness", drop_sheet.title(
            game="Medieval Madness", asked={"media_kind": "wheel"}))

    def test_an_asset_target_names_the_kind_and_the_game(self) -> None:
        self.assertEqual("Backglass File for Medieval Madness", drop_sheet.title(
            game="Medieval Madness", asked={"asset_kind": "backglass"}))

    def test_a_drop_on_a_game_adds_to_it(self) -> None:
        self.assertEqual("Add to Medieval Madness",
                         drop_sheet.title(game="Medieval Madness", asked={}))

    def test_a_new_game_is_named_for_what_it_came_in(self) -> None:
        self.assertEqual("New Game from pack.zip", drop_sheet.title(
            game="", asked={}, new_game=True, source="pack.zip"))
        self.assertEqual("New Game from This Drop", drop_sheet.title(
            game="", asked={}, new_game=True))


class TheButton(unittest.TestCase):
    def test_it_says_how_many_files_when_there_are_several(self) -> None:
        sheet = drop_sheet.Sheet(_plan(items=[_item(0), _item(1, member="b", name="b")]))

        self.assertEqual("Import 2 Files", drop_sheet.go_label(sheet, "Import", 2))
        self.assertEqual("Import", drop_sheet.go_label(sheet, "Import", 0))

    def test_a_swap_keeps_its_own_words(self) -> None:
        sheet = drop_sheet.Sheet(_plan(items=[_item(0), _item(1, member="b", name="b")]))

        self.assertEqual("Replace", drop_sheet.go_label(sheet, "Replace", 2))

    def test_an_open_group_replaces_the_words_with_what_to_choose(self) -> None:
        sheet = drop_sheet.Sheet(_plan(groups=[_group()]))

        self.assertEqual("Choose One Wheel", drop_sheet.go_label(sheet, "Import", 0))


if __name__ == "__main__":
    unittest.main()
