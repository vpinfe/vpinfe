"""What a row of the import dialog says: the file's picture where it has one, what it is for,
and what it does to what is there."""

from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import patch

from manager.kit import candidates
from manager.library import convert_row, drop_sheet, import_dialog


def _media(name: str = "(RuleSheet) 300.png", **more: object) -> dict:
    return {"index": 0, "kind": "media", "label": "Media", "media_kind": "rule_sheet",
            "name": name, "size": 412_000, "action": "replace_media",
            "destination": "/games/300/medias/rulesheet.png", "replaces": "slot is empty",
            **more}


def _drawn(item: dict, upload_id: str = "") -> tuple[list[str], list[Any]]:
    with patch.object(import_dialog, "ui") as ui, \
            patch.object(candidates, "thumb") as thumb:
        plan = {"game_dir": "/games/300", "items": [item]}
        import_dialog._draw_row(drop_sheet.Sheet(plan), item, plan, {0: True}, True,
                                lambda: None, upload_id, progress=convert_row.Progress(),
                                replan=lambda: None, redraw=lambda: None)
    return [one.args[0] for one in ui.label.call_args_list], thumb.call_args_list


class SlotWords(unittest.TestCase):
    def test_a_media_file_says_the_slot_it_fills_not_the_word_media(self) -> None:
        said, _thumbs = _drawn(_media())

        self.assertIn("Rule Sheet · slot is empty · 402.3 KB", said)
        self.assertNotIn("Media", said)

    def test_the_path_of_a_media_file_is_not_a_line_of_the_row(self) -> None:
        said, _thumbs = _drawn(_media())

        self.assertFalse([one for one in said if "medias/" in one])

    def test_a_file_that_is_not_media_says_what_it_is_and_where_it_goes(self) -> None:
        item = {"index": 0, "kind": "table", "label": "Table", "media_kind": "",
                "name": "300.vpx", "size": 9, "action": "replace_vpx",
                "destination": "/games/300/300.vpx", "replaces": "replaces 300 old.vpx"}
        said, _thumbs = _drawn(item)

        self.assertIn("Table · the game folder · replaces 300 old.vpx · 9 B", said)


class Pictures(unittest.TestCase):
    def test_a_loose_image_leads_with_its_picture_from_the_upload(self) -> None:
        _said, thumbs = _drawn(_media(), "u1")

        self.assertEqual(
            "/api/v1/uploads/u1/picture?name=%28RuleSheet%29+300.png&size=256",
            thumbs[0].args[0])

    def test_a_media_file_with_no_picture_leads_with_the_glyph(self) -> None:
        _said, thumbs = _drawn(_media("(RuleSheet) 300.pdf"))

        self.assertEqual("", thumbs[0].args[0])

    def test_a_row_that_is_not_media_has_no_picture_frame(self) -> None:
        item = {"index": 0, "kind": "table", "label": "Table", "media_kind": "",
                "name": "300.vpx", "size": 9, "action": "copy",
                "destination": "/games/300/300.vpx", "replaces": ""}
        _said, thumbs = _drawn(item)

        self.assertEqual([], thumbs)

    def test_an_image_is_pictured_whether_it_came_loose_or_in_an_archive(self) -> None:
        self.assertTrue(import_dialog._pictured(_media()))

    def test_a_document_is_not_pictured(self) -> None:
        self.assertFalse(import_dialog._pictured(_media("rules.pdf")))

    def test_a_long_bundle_is_pictured_only_as_far_as_the_cap(self) -> None:
        self.assertFalse(import_dialog._pictured(_media(index=import_dialog.PICTURES_UP_TO)))


if __name__ == "__main__":
    unittest.main()
