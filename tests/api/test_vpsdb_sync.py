"""Keeping the local catalog current, and knowing when a check is owed.

Everything VPS-shaped reads a file: matching, release lists, obtainability, what a kind
is offered from.
"""

from __future__ import annotations

import configparser
import tempfile
import unittest
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock, patch

from common import timestamps
from common.config.config_store import ConfigStore
from common.online import cache_stamps, vpsdb_sync
from common.runtime import jobs
from tests.support.stamps import OwnStamps


def _config(download: str = "", **stamps: str) -> ConfigStore:
    """`download` is the schedule a person sets; `checked` and `last` (the version held)
    are what the sync keeps beside the catalog."""
    parser = configparser.ConfigParser()
    parser.read_dict({"updates": {"download_spreadsheet": download} if download else {}})
    cache_stamps.put("vpsdb", **{{"last": "version"}.get(key, key): value
                                 for key, value in stamps.items()})
    return cast(ConfigStore, parser)


def _ago(seconds: float) -> str:
    now = timestamps.iso_to_epoch(timestamps.utc_now_iso()) or 0
    return timestamps.epoch_to_iso(now - seconds)


class DueTests(OwnStamps):
    def test_never_checked_is_due(self) -> None:
        """A fresh install holding no catalog should not wait a day for one."""
        self.assertTrue(vpsdb_sync.due(_config(download="daily")))

    def test_never_is_not_an_interval(self) -> None:
        """It is a decision not to ask, not a very long wait - so even never having
        asked does not make it due."""
        self.assertFalse(vpsdb_sync.due(_config(download="never")))

    def test_inside_the_interval_is_not_due(self) -> None:
        self.assertFalse(vpsdb_sync.due(_config(download="daily", checked=_ago(3600))))

    def test_past_the_interval_is_due(self) -> None:
        self.assertTrue(vpsdb_sync.due(_config(download="daily", checked=_ago(90000))))

    def test_a_longer_schedule_holds_longer(self) -> None:
        day_old = _ago(90000)
        self.assertTrue(vpsdb_sync.due(_config(download="daily", checked=day_old)))
        self.assertFalse(vpsdb_sync.due(_config(download="weekly", checked=day_old)))

    def test_a_schedule_this_build_does_not_know_asks_nothing(self) -> None:
        """A config from a newer build must not be read as "every time"."""
        self.assertFalse(vpsdb_sync.due(_config(download="hourly")))


class SyncTests(OwnStamps):
    def test_not_due_does_not_download(self) -> None:
        config = _config(download="daily", checked=_ago(60))
        with patch("common.games.game_service.ensure_vpsdb_downloaded") as fetch:
            result = vpsdb_sync.sync(config)

        fetch.assert_not_called()
        self.assertFalse(result["checked"])

    def test_forced_ignores_the_schedule(self) -> None:
        """Asked for by a person. Answering "not due" would report a rule back to
        whoever is overriding it."""
        config = _config(download="never", checked=_ago(60))
        with patch("common.games.game_service.ensure_vpsdb_downloaded",
                   return_value=True) as fetch:
            result = vpsdb_sync.sync(config, force=True)

        fetch.assert_called_once()
        self.assertTrue(result["checked"])

    def test_a_check_that_found_nothing_new_says_so(self) -> None:
        config = _config(download="daily", last="1788355140571")
        with patch("common.games.game_service.ensure_vpsdb_downloaded",
                   return_value=True):
            result = vpsdb_sync.sync(config, force=True)

        self.assertTrue(result["ok"])
        self.assertFalse(result["changed"])

    def test_a_failed_check_still_stamps(self) -> None:
        """Or an unreachable catalog is re-asked on every draw."""
        config = _config(download="daily")
        with patch("common.games.game_service.ensure_vpsdb_downloaded",
                   return_value=False):
            vpsdb_sync.sync(config, force=True)

        self.assertTrue(vpsdb_sync.checked_at())
        self.assertFalse(vpsdb_sync.due(config))


class EnsureCatalogTests(OwnStamps):
    """A read with new games to match gets a catalog when there is none on disk."""

    def setUp(self) -> None:
        super().setUp()
        held = tempfile.TemporaryDirectory()
        self.addCleanup(held.cleanup)
        self.catalog = Path(held.name) / "vpsdb.json"
        path = patch("common.games.game_service.VPSDB_JSON_PATH", self.catalog)
        path.start()
        self.addCleanup(path.stop)
        self.said: list[str] = []
        jobs.reset_for_tests()
        self.addCleanup(jobs.reset_for_tests)

    def ensure(self, config: ConfigStore) -> tuple[bool, MagicMock]:
        def fetch() -> bool:
            self.catalog.write_text("[]", encoding="utf-8")
            return True

        with patch("common.games.game_service.ensure_vpsdb_downloaded",
                   side_effect=fetch) as fetched:
            held = vpsdb_sync.ensure_catalog(config, lambda: self.said.append("downloading"))
        return held, fetched

    def test_a_catalog_on_disk_is_not_downloaded_again(self) -> None:
        self.catalog.write_text("[]", encoding="utf-8")

        held, fetched = self.ensure(_config(download="daily"))

        self.assertTrue(held)
        fetched.assert_not_called()
        self.assertEqual(self.said, [])

    def test_no_catalog_is_downloaded_whenever_it_was_last_asked(self) -> None:
        """The stamp says it was asked an hour ago; the file says nothing came of it."""
        config = _config(download="daily", checked=_ago(3600))

        held, fetched = self.ensure(config)

        self.assertTrue(held)
        fetched.assert_called_once()
        self.assertEqual(self.said, ["downloading"])

    def test_the_download_is_a_job_the_rail_can_show(self) -> None:
        seen: list[str] = []

        def fetch() -> bool:
            seen.extend(job.kind for job in jobs.active())
            self.catalog.write_text("[]", encoding="utf-8")
            return True

        with patch("common.games.game_service.ensure_vpsdb_downloaded", side_effect=fetch):
            vpsdb_sync.ensure_catalog(_config(download="daily"))

        self.assertEqual(seen, [jobs.KIND_SPREADSHEET_DOWNLOAD])
        self.assertEqual(jobs.recent()[0].state, jobs.DONE)

    def test_a_download_that_fails_is_the_jobs_failure(self) -> None:
        with patch("common.games.game_service.ensure_vpsdb_downloaded", return_value=False):
            held = vpsdb_sync.ensure_catalog(_config(download="daily"))

        self.assertFalse(held)
        job = jobs.recent()[0]
        self.assertEqual((job.kind, job.state), (jobs.KIND_SPREADSHEET_DOWNLOAD, jobs.FAILED))

    def test_a_refresh_by_the_watch_is_not_a_job(self) -> None:
        self.catalog.write_text("[]", encoding="utf-8")

        with patch("common.games.game_service.ensure_vpsdb_downloaded", return_value=True):
            vpsdb_sync.sync(_config(download="daily"), force=True)

        self.assertEqual(jobs.recent(), [])

    def test_never_downloads_nothing(self) -> None:
        held, fetched = self.ensure(_config(download="never"))

        self.assertFalse(held)
        fetched.assert_not_called()
        self.assertEqual(self.said, [])


if __name__ == "__main__":
    unittest.main()
