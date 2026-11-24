"""The catalog sweep: when it is owed, and when it must not run."""

from __future__ import annotations

import configparser
import unittest

from common.config.config_store import ConfigStore
from common.devices import device_registry
from common.online import cache_stamps, vpsdb_sync
from tests.support.library import own_registry
from tests.support.stamps import OwnStamps


class _Config(ConfigStore):
    """A ConfigStore's surface, as much of it as this reads."""

    def __init__(self, schedules: dict[str, str]) -> None:
        self.config = configparser.ConfigParser()
        self.config.read_dict({"updates": schedules})
        self.saved = 0

    def save(self) -> None:
        self.saved += 1


def _config(download: str = "", update_game_details: str = "", **vpsdb: str) -> _Config:
    """The two settings a person makes: when the spreadsheet downloads, and whether a newer
    one rewrites matched games. `last`, the catalog version held, goes where the sync keeps
    it, and `games_updated_to` on this install's own row."""
    wanted = {"download_spreadsheet": download, "update_game_details": update_game_details}
    cache_stamps.put("vpsdb", version=vpsdb.pop("last", ""))
    device_registry.get_device_registry().set_self(
        vps_games_updated_to=vpsdb.pop("games_updated_to", "") or None)
    return _Config({key: value for key, value in wanted.items() if value})


class _OwnRow(OwnStamps):
    def setUp(self) -> None:
        super().setUp()
        own_registry(self, self.stamps_path.parent / "devices.json")


class WhenASweepIsOwed(_OwnRow):
    def test_off_by_default(self) -> None:
        """Read off the schema, which is where the default actually lives. A config
        object that has never had the key set falls back to whatever the caller passed,
        so asking one of those proves nothing."""
        from common.config.config_schema import options

        declared = next(one for one in options()
                        if one.section == "updates" and one.key == "update_game_details")
        self.assertEqual("bool", declared.type)
        self.assertEqual("false", declared.default)

    def test_a_config_with_nothing_set_is_owed_nothing(self) -> None:
        self.assertFalse(vpsdb_sync.adopt_due(_config(last="2026-09-22")))

    def test_off_is_off_even_with_a_new_catalog(self) -> None:
        self.assertFalse(vpsdb_sync.adopt_due(
            _config(update_game_details="false", last="2026-09-22",
                    games_updated_to="2026-01-01")))

    def test_a_catalog_that_moved_is_owed_one(self) -> None:
        self.assertTrue(vpsdb_sync.adopt_due(
            _config(update_game_details="true", last="2026-09-22",
                    games_updated_to="2026-01-01")))

    def test_a_catalog_that_has_not_moved_is_not(self) -> None:
        self.assertFalse(vpsdb_sync.adopt_due(
            _config(update_game_details="true", last="2026-09-22",
                    games_updated_to="2026-09-22")))

    def test_never_swept_and_a_catalog_in_hand_is_owed_one(self) -> None:
        self.assertTrue(vpsdb_sync.adopt_due(
            _config(update_game_details="true", last="2026-09-22")))

    def test_no_catalog_yet_is_not(self) -> None:
        self.assertFalse(vpsdb_sync.adopt_due(_config(update_game_details="true", last="")))

    def test_a_value_that_is_not_a_boolean_reads_as_off(self) -> None:
        self.assertFalse(vpsdb_sync.adopt_due(
            _config(update_game_details="daily", last="2026-09-22",
                    games_updated_to="2026-01-01")))


class TheTwoSchedulesAreSeparate(_OwnRow):
    def test_fetching_daily_does_not_sweep(self) -> None:
        held = _config(download="daily", last="2026-09-22", games_updated_to="2026-01-01")
        self.assertFalse(vpsdb_sync.adopt_due(held))

    def test_sweeping_does_not_require_fetching(self) -> None:
        held = _config(download="never", update_game_details="true",
                       last="2026-09-22", games_updated_to="2026-01-01")
        self.assertTrue(vpsdb_sync.adopt_due(held))


if __name__ == "__main__":
    unittest.main()
