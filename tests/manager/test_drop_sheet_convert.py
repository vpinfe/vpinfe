"""What the drop sheet keeps and asks for the rows that are converted on the way in."""

from __future__ import annotations

import unittest
from typing import Any

from common.i18n import t
from manager.library import convert_row, drop_sheet


def _options(*, recommended: str = "png", ffmpeg: bool = True) -> list[dict[str, Any]]:
    return [
        {"value": "png", "label": "PNG", "recommended": recommended == "png",
         "why": "Plays everywhere", "available": True},
        {"value": "webp", "label": "WebP", "recommended": False, "why": "Smaller",
         "available": ffmpeg},
        {"value": "asis", "label": "Don't Convert", "recommended": False, "why": "",
         "available": True}]


def _item(member: str = "wheel.tga", **convert: Any) -> dict[str, Any]:
    view = {"kind": "picture", "from": "TGA", "to": "png", "options": _options(),
            "settings": [], "line": "", "note": "", "problem": "", "reencodes": False,
            **convert}
    return {"index": 0, "kind": "media", "media_kind": "wheel", "name": member,
            "member": member, "size": 40_000, "default_enabled": True, "extra": False,
            "convert": view}


def _plan(*items: dict[str, Any], **more: Any) -> dict[str, Any]:
    return {"game_dir": "/games/MM", "new_game_dir_name": "", "items": list(items),
            "blocked": [], "groups": [], "unclaimed": [], "left_out": [], "tables": [],
            "scope": "", "choice": None, **more}


class WhatIsAsked(unittest.TestCase):
    def test_nothing_is_asked_until_a_choice_is_made(self) -> None:
        sheet = drop_sheet.Sheet(_plan(_item()))

        self.assertNotIn("convert", sheet.asked())

    def test_a_choice_goes_back_keyed_by_the_file(self) -> None:
        sheet = drop_sheet.Sheet(_plan(_item()))

        sheet.decide("wheel.tga", "to", "asis")

        self.assertEqual({"rows": {"wheel.tga": {"to": "asis"}}, "wait": True},
                         sheet.asked()["convert"])

    def test_settings_of_one_file_accumulate_on_it(self) -> None:
        sheet = drop_sheet.Sheet(_plan(_item()))

        sheet.decide("wheel.tga", "quality", "high")
        sheet.decide("wheel.tga", "video", "vp9")

        self.assertEqual({"quality": "high", "video": "vp9"},
                         sheet.asked()["convert"]["rows"]["wheel.tga"])

    def test_not_waiting_is_asked_even_with_no_choice_made(self) -> None:
        sheet = drop_sheet.Sheet(_plan(_item()))
        sheet.wait = False

        self.assertEqual({"rows": {}, "wait": False}, sheet.asked()["convert"])

    def test_the_view_is_the_one_the_plan_gave_the_file(self) -> None:
        sheet = drop_sheet.Sheet(_plan(_item()))

        self.assertEqual("PNG", (sheet.view("wheel.tga") or {})["options"][0]["label"])
        self.assertIsNone(sheet.view("other.png"))


class FfmpegAndReencoding(unittest.TestCase):
    def test_a_plan_with_no_convert_state_has_nothing_missing(self) -> None:
        sheet = drop_sheet.Sheet(_plan(_item()))

        self.assertFalse(sheet.ffmpeg_missing())
        self.assertFalse(sheet.reencodes())

    def test_ffmpeg_is_missing_when_the_plan_says_it_is_not_usable(self) -> None:
        sheet = drop_sheet.Sheet(_plan(_item(), convert={"ffmpeg": "", "usable": False,
                                                          "reencodes": False, "wait": True}))

        self.assertTrue(sheet.ffmpeg_missing())

    def test_a_usable_ffmpeg_is_not_missing(self) -> None:
        sheet = drop_sheet.Sheet(_plan(_item(), convert={"ffmpeg": "/x", "usable": True,
                                                          "reencodes": True, "wait": True}))

        self.assertFalse(sheet.ffmpeg_missing())
        self.assertTrue(sheet.reencodes())


class RowWords(unittest.TestCase):
    def test_the_recommended_option_says_so_in_its_label(self) -> None:
        labels, _says, _unavailable = convert_row._lists(_options())

        self.assertEqual(t("manager.drop_sheet.recommended", name="PNG"), labels["png"])
        self.assertEqual("WebP", labels["webp"])

    def test_a_preferred_option_says_so_instead_of_recommended(self) -> None:
        options = _options()
        options[0]["preferred"] = True
        options[1]["preferred"] = False

        labels, _says, _unavailable = convert_row._lists(options)

        self.assertEqual(t("manager.drop_sheet.your_preference", name="PNG"), labels["png"])
        self.assertEqual("WebP", labels["webp"])

    def test_the_answer_that_converts_nothing_has_its_own_label(self) -> None:
        labels, _says, _unavailable = convert_row._lists(_options())

        self.assertEqual(t("manager.drop_sheet.dont_convert"), labels["asis"])

    def test_an_option_that_cannot_be_picked_is_listed_with_its_sentence(self) -> None:
        _labels, says, unavailable = convert_row._lists(_options(ffmpeg=False))

        self.assertEqual({"webp": "Smaller"}, unavailable)
        self.assertEqual("Plays everywhere", says["png"])

    def test_an_unavailable_option_with_no_reason_is_still_unavailable(self) -> None:
        options = _options(ffmpeg=False)
        options[1]["why"] = ""

        _labels, _says, unavailable = convert_row._lists(options)

        self.assertEqual({"webp": ""}, unavailable)

    def test_the_note_is_what_converting_does_and_nothing_when_nothing_is_converted(self) -> None:
        converting = _item(note="Will be re-encoded.")["convert"]
        untouched = _item(to="asis", note="Will be re-encoded.")["convert"]

        self.assertEqual("Will be re-encoded.", convert_row.note(converting))
        self.assertEqual("", convert_row.note(untouched))
        self.assertEqual("", convert_row.note(None))

    def test_the_format_is_named_in_the_words_the_choice_lists_use(self) -> None:
        view = _item(to="webp")["convert"]

        self.assertEqual("WebP", convert_row.format_name(view))
        self.assertEqual("gif", convert_row.format_name({"to": "gif", "options": []}))


if __name__ == "__main__":
    unittest.main()
