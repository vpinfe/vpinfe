"""The Preferred Formats as Convert reads them: only a value somebody set is a preference."""

from __future__ import annotations

import configparser
import unittest

from common.games import convert_preferences


def _config(**sections: dict[str, str]) -> configparser.ConfigParser:
    parser = configparser.ConfigParser()
    parser.read_dict(sections)
    return parser


class ReadTests(unittest.TestCase):
    def test_an_empty_file_holds_no_preference(self) -> None:
        self.assertEqual(convert_preferences.read(_config()), convert_preferences.Preferences())

    def test_automatic_and_a_quality_left_at_standard_hold_none(self) -> None:
        held = convert_preferences.read(_config(
            media={"preferred_picture": "auto", "preferred_sound": "auto",
                   "keep_originals": "auto"},
            capture={"video_codec": "auto", "quality": "standard"}))

        self.assertEqual(held, convert_preferences.Preferences())

    def test_every_value_that_was_set_is_a_preference(self) -> None:
        held = convert_preferences.read(_config(
            media={"preferred_picture": "webp", "preferred_sound": "ogg",
                   "keep_originals": "discard"},
            capture={"video_codec": "vp9", "quality": "high"}))

        self.assertEqual(held, convert_preferences.Preferences(
            picture="webp", sound="ogg", video="vp9", quality="high", keep=False))

    def test_keeping_the_originals_is_a_preference_too(self) -> None:
        held = convert_preferences.read(_config(media={"keep_originals": "keep"}))

        self.assertIs(held.keep, True)

    def test_a_value_the_setting_does_not_take_holds_none(self) -> None:
        held = convert_preferences.read(_config(
            media={"preferred_picture": "gif", "keep_originals": "sometimes"},
            capture={"video_codec": "av1"}))

        self.assertEqual(held, convert_preferences.Preferences())


if __name__ == "__main__":
    unittest.main()
