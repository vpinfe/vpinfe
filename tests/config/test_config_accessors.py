"""The typed views over `vpinfe.ini`, and the normalizing they do on the way through."""

from __future__ import annotations

import configparser
import unittest

from common.config.config_access import DisplayConfig, MediaConfig, NetworkConfig, SettingsConfig


class RealDmdPriorityTests(unittest.TestCase):
    """The setting asks one question, colour or standard, and what it offers is what the
    reader takes."""

    def _read(self, value: str) -> str:
        parser = configparser.ConfigParser()
        parser.read_dict({"Media": {"realdmdmediapriority": value}})
        return MediaConfig.from_config(parser).realdmd_media_priority

    def test_it_offers_color_and_standard(self) -> None:
        from common.config import config_schema

        option = config_schema.option("presentation", "real_dmd_media_priority")

        assert option is not None
        self.assertEqual(option.choices, ("color", "standard"))

    def test_each_choice_reads_back_as_itself(self) -> None:
        from common.config import config_schema

        option = config_schema.option("presentation", "real_dmd_media_priority")
        assert option is not None
        for choice in option.choices:
            with self.subTest(choice=choice):
                self.assertEqual(self._read(choice), choice)


class TypedConfigTests(unittest.TestCase):
    def test_typed_config_accessors_normalize_common_sections(self) -> None:
        parser = configparser.ConfigParser()
        parser.read_dict({
            "Settings": {
                "vpxinipath": "/home/player/.vpinball/VPinballX.ini",
                "vpxlogdeleteonstart": "yes",
                "theme": "",
                "cabmode": "true",
            },
            "Media": {
                "playfieldvariant": "FSS",
                "playfieldresolution": "4K",
                "playfieldvideoresolution": "1080p",
                "playfieldmediapriority": "image",
                "bgmediapriority": "mp4",
                "dmdmediapriority": "invalid",
                "realdmdmediapriority": "realdmd.png",
            },
            "Network": {
                "wsport": "9002",
                "themeassetsport": "bad",
            },
            "Displays": {
                "playfieldscreenid": "2",
                "playfieldrotation": "270",
            },
            "vpinplay": {
                "apiendpoint": " http://example.test ",
                "synconexit": "1",
            },
        })

        self.assertEqual(SettingsConfig.from_config(parser).theme, "Revolution")
        self.assertTrue(SettingsConfig.from_config(parser).default_chrome_options)
        media_config = MediaConfig.from_config(parser)
        self.assertEqual(media_config.playfield_variant, "fss")
        self.assertEqual(media_config.playfield_media_priority, "image")
        self.assertEqual(media_config.bg_media_priority, "video")
        self.assertEqual(media_config.dmd_media_priority, "video")
        self.assertEqual(media_config.realdmd_media_priority, "standard")
        self.assertEqual(
            media_config.priority_payload(),
            # Canonical keys, because core looks them up with a normalized kind name.
            # `bg` and `dmd` stay so a contract 1 theme reading them directly still works.
            {"playfield": "image", "backglass": "video", "scoreview": "video",
             "real_dmd": "standard", "bg": "video", "dmd": "video"},
        )
        self.assertEqual(NetworkConfig.from_config(parser).ws_port, 9002)
        self.assertEqual(DisplayConfig.from_config(parser).playfield_screen_id, 2)
        self.assertEqual(
            DisplayConfig.from_config(parser).window_screen_id("playfieldscreenid"), "2")
        self.assertTrue(DisplayConfig.from_config(parser).cab_mode)

    def test_display_config_preserves_empty_game_screen_for_window_discovery(self) -> None:
        parser = configparser.ConfigParser()
        parser.read_dict({"Displays": {"playfieldscreenid": ""}})

        display = DisplayConfig.from_config(parser)

        self.assertEqual(display.playfield_screen_id, 0)
        self.assertEqual(display.window_screen_id("playfieldscreenid"), "")

    def test_settings_config_defaults_splashscreen_off(self) -> None:
        parser = configparser.ConfigParser()

        self.assertFalse(SettingsConfig.from_config(parser).splashscreen)

    def test_settings_config_reads_default_chrome_options(self) -> None:
        parser = configparser.ConfigParser()
        parser.read_dict({"chromium": {"defaults": "no"}})

        self.assertFalse(SettingsConfig.from_config(parser).default_chrome_options)


class DisplayGeometryNormalizationTests(unittest.TestCase):
    """`[Displays]` is free text, and every theme compared it exactly.

    `[Media] playfieldvariant` has always lowercased on read; `[Displays]` never did, so a
    capitalized `Portrait` silently meant landscape, and a rotation of 45 reached themes
    that only test for 90 or 270.
    """

    def _displays(self, orientation, rotation):
        parser = configparser.ConfigParser()
        parser.read_dict({"Displays": {
            "playfieldorientation": str(orientation),
            "playfieldrotation": str(rotation),
        }})
        return DisplayConfig.from_config(parser)

    def test_orientation_is_matched_without_regard_to_case_or_padding(self) -> None:
        for written in ("Portrait", "PORTRAIT", "  portrait  "):
            with self.subTest(written=written):
                self.assertEqual(self._displays(written, 0).playfield_orientation, "portrait")

    def test_an_unrecognized_orientation_falls_back_to_landscape(self) -> None:
        self.assertEqual(self._displays("portait", 0).playfield_orientation, "landscape")

    def test_rotation_is_one_of_four_turns(self) -> None:
        for written, expected in ((0, 0), (90, 90), (180, 180), (270, 270),
                                  (-90, 270), (450, 90), (720, 0)):
            with self.subTest(written=written):
                self.assertEqual(self._displays("portrait", written).playfield_rotation, expected)

    def test_a_rotation_that_is_not_a_quarter_turn_falls_back_to_none(self) -> None:
        self.assertEqual(self._displays("portrait", 45).playfield_rotation, 0)


if __name__ == "__main__":
    unittest.main()
