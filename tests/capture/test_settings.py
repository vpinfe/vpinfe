"""The Recording settings a recording reads: this device's, with a run's own in place."""

from __future__ import annotations

import configparser
import unittest

from common import service_errors
from common.capture import settings
from common.config import config_schema


def _config(**values: str) -> configparser.ConfigParser:
    held = configparser.ConfigParser()
    held["capture"] = values
    return held


class SettingsTests(unittest.TestCase):
    def test_an_unset_setting_reads_as_the_schemas_default(self) -> None:
        read = settings.read(_config())

        for option in config_schema.CONFIG_OPTIONS:
            if option.section != settings.SECTION:
                continue
            with self.subTest(option.key):
                self.assertEqual(str(getattr(read, option.key)).lower(), option.default)

    def test_a_stored_value_the_setting_does_not_take_reads_as_its_default(self) -> None:
        read = settings.read(_config(quality="ultra", length="soon"))

        self.assertEqual(read.quality, settings.read(_config()).quality)
        self.assertEqual(read.length, settings.read(_config()).length)

    def test_a_runs_own_values_replace_the_devices(self) -> None:
        read = settings.read(_config(length="30"),
                             {"length": 12, "sound": True, "video_codec": "vp9"})

        self.assertEqual((read.length, read.sound, read.video_codec), (12, True, "vp9"))

    def test_a_run_naming_anything_else_is_refused(self) -> None:
        for overrides in ({"lenght": 12}, {"quality": "ultra"}, {"length": -1},
                          {"sound": "maybe"}, {"picture_at": 20, "length": 20}):
            with self.subTest(overrides), self.assertRaises(service_errors.RefusedError):
                settings.read(_config(), overrides)

    def test_a_picture_past_the_end_of_a_stored_length_is_taken_inside_it(self) -> None:
        read = settings.read(_config(length="4", picture_at="9"))

        self.assertEqual((read.length, read.picture_at), (4, 3))


if __name__ == "__main__":
    unittest.main()
