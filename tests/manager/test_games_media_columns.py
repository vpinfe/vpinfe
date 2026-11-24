"""The Games grid's media columns: one for every kind the library collects."""

from __future__ import annotations

import unittest
from unittest.mock import Mock

from common.media.media_specs import media_label_map
from manager.kit import data, media_ownership, views
from manager.library import games


def _library(hidden: frozenset[str] = frozenset()) -> Mock:
    library = Mock()
    library.asset_keys.return_value = []
    library.thumbnail_look.return_value = None
    library.kept_kinds.return_value = {"media": set(media_label_map()) - hidden,
                                       "asset": set()}
    return library


def _columns(kinds: list[str], library: Mock) -> dict[str, dict]:
    return {column["field"]: column for column in games.grid_columns([], kinds, library)}


def _media_view(kinds: list[str], library: Mock) -> tuple[str, ...]:
    preset = games.grid_presets(kinds, library)[games._MEDIA]
    assert isinstance(preset, views.Preset)
    return preset.columns


class TheGamesGridMediaColumns(unittest.TestCase):
    def test_a_kind_no_game_has_yet_is_a_column_to_drop_on(self) -> None:
        self.assertIn("media_rule_sheet", set(_columns(["wheel"], _library())))

    def test_the_media_view_shows_the_kinds_the_library_has(self) -> None:
        self.assertEqual(("name", "media_wheel"), _media_view(["wheel"], _library()))

    def test_a_kind_the_library_does_not_collect_is_not_a_column(self) -> None:
        library = _library(hidden=frozenset({"topper"}))

        self.assertNotIn("media_topper", set(_columns(["wheel", "topper"], library)))
        self.assertEqual(("name", "media_wheel"), _media_view(["wheel", "topper"], library))

    def test_the_kinds_the_library_has_set_the_width(self) -> None:
        columns = _columns(["wheel", "backglass"], _library())
        width = columns["media_backglass"]["width"]

        self.assertEqual(width, columns["media_wheel"]["width"])
        self.assertEqual(width, columns["media_rule_sheet"]["width"])

    def test_a_kind_with_a_wider_name_is_as_wide_as_its_name(self) -> None:
        columns = _columns(["wheel", "backglass"], _library())

        self.assertGreater(columns["media_instruction_card"]["width"],
                           columns["media_backglass"]["width"])


class TheWordForACell(unittest.TestCase):
    def test_a_present_file_of_a_tier_nobody_named_reads_as_a_stand_in(self) -> None:
        stand_in = media_ownership.tier_for(media_ownership.STAND_IN).noun
        missing = media_ownership.tier_for(media_ownership.MISSING).noun
        for via in ("", None, "somewhere_new"):
            with self.subTest(via=via):
                word = data._glyph({"present": True, "via": via})
                self.assertEqual(stand_in, word)
                self.assertNotEqual(missing, word)

    def test_an_absent_file_is_blank_and_a_named_tier_keeps_its_word(self) -> None:
        self.assertEqual("", data._glyph({"present": False, "via": "table"}))
        self.assertEqual(media_ownership.tier_for(media_ownership.TABLE).noun,
                         data._glyph({"present": True, "via": "table"}))


if __name__ == "__main__":
    unittest.main()
