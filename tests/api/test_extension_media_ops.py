"""The two things an import asks core for beside the library: art, and the wheel set."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from common.games import media_ops
from common.runtime import jobs
from httpapi import extension_ops


class WheelSetTests(unittest.TestCase):
    def test_logos_are_set_as_the_wheel_through_the_settings_store(self) -> None:
        with patch("common.settings.config_service.apply") as applied:
            made = media_ops.use_logos_for_wheels()

        applied.assert_called_once_with({"settings": {"media": {"wheelset": "logo"}}})
        self.assertEqual(made, {"wheelset": "logo"})

    def test_it_is_offered_under_the_settings_scope_and_not_the_games_one(self) -> None:
        self.assertIn("use_logos_for_wheels", extension_ops.CONFIG_WRITES)
        self.assertNotIn("use_logos_for_wheels", extension_ops.WRITES)


class MissingArtTests(unittest.TestCase):
    def test_a_fill_starts_for_the_games_named(self) -> None:
        with patch("common.games.media_fill.start") as started:
            made = media_ops.get_missing_media(["g1", "g2"])

        started.assert_called_once_with(["g1", "g2"])
        self.assertEqual(made, {"started": True})

    def test_a_fill_already_running_is_said_and_not_waited_for(self) -> None:
        with patch("common.games.media_fill.start", side_effect=jobs.JobBusyError("busy")):
            made = media_ops.get_missing_media(["g1"])

        self.assertEqual(made, {"started": False})

    def test_it_is_offered_under_the_games_write_scope(self) -> None:
        self.assertIn("get_missing_media", extension_ops.WRITES)


if __name__ == "__main__":
    unittest.main()
