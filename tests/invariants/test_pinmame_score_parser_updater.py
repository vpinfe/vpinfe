from __future__ import annotations

import importlib
import json
import tempfile
import time
from pathlib import Path
from typing import Any
from unittest import mock

from common.online import cache_stamps
from tests.support.stamps import OwnStamps

updater = importlib.import_module("common.online.pinmame_score_parser_updater")


class TestPinmameScoreParserUpdater(OwnStamps):
    def test_ensure_latest_roms_json_downloads_and_tracks_release_digest(self) -> None:
        roms_bytes = json.dumps({"foo": {"scoretype": "HIGH SCORE"}}).encode("utf-8")
        release_payload = {
            "tag_name": "v1.2.3",
            "assets": [
                {
                    "name": "roms.json",
                    "browser_download_url": "https://example.invalid/roms.json",
                    "digest": f"sha256:{updater.hashlib.sha256(roms_bytes).hexdigest()}",
                }
            ],
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            with mock.patch.object(updater, "ROMS_JSON_PATH", temp_path / "roms.json"), \
                mock.patch.object(updater, "_request_json", return_value=release_payload), \
                mock.patch.object(updater.http_client, "download_file",
                                  side_effect=lambda url, dest, **_: dest.write_bytes(roms_bytes)):
                result = updater.ensure_latest_roms_json()

            self.assertEqual(result["status"], "downloaded")
            self.assertEqual((temp_path / "roms.json").read_bytes(), roms_bytes)
            self.assertEqual(cache_stamps.get("roms", "release"),
                             updater.hashlib.sha256(roms_bytes).hexdigest())

    def test_ensure_latest_roms_json_skips_download_when_digest_matches(self) -> None:
        roms_bytes = json.dumps({"foo": {"scoretype": "HIGH SCORE"}}).encode("utf-8")
        roms_sha = updater.hashlib.sha256(roms_bytes).hexdigest()
        release_payload = {
            "assets": [
                {
                    "name": "roms.json",
                    "browser_download_url": "https://example.invalid/roms.json",
                    "digest": f"sha256:{roms_sha}",
                }
            ],
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            (temp_path / "roms.json").write_bytes(roms_bytes)
            cache_stamps.put("roms", release=roms_sha)

            with mock.patch.object(updater, "ROMS_JSON_PATH", temp_path / "roms.json"), \
                mock.patch.object(updater, "_request_json", return_value=release_payload), \
                mock.patch.object(updater.http_client, "download_file") as download:
                result = updater.ensure_latest_roms_json()

            self.assertEqual(result["status"], "up_to_date")
            download.assert_not_called()
            self.assertTrue(cache_stamps.get("roms", "checked"))


class TestAskedOnceADay(OwnStamps):
    """A start does not ask GitHub for the release it asked about in the last day."""

    RELEASE = {"assets": [{"name": "roms.json",
                           "browser_download_url": "https://example.invalid/roms.json",
                           "digest": "sha256:abc"}]}

    def setUp(self) -> None:
        super().setUp()
        held = tempfile.TemporaryDirectory()
        self.addCleanup(held.cleanup)
        self.root = Path(held.name)
        cache_stamps.put("roms", release="abc")
        one = mock.patch.object(updater, "ROMS_JSON_PATH", self.root / "roms.json")
        one.start()
        self.addCleanup(one.stop)

    def _checked(self, hours_ago: float) -> None:
        cache_stamps.put("roms", checked=updater.timestamps.epoch_to_iso(
            time.time() - hours_ago * 3600))

    def _run(self, **lookup: Any) -> tuple[dict | None, mock.MagicMock]:
        with mock.patch.object(updater, "_request_json", **lookup) as asked:
            try:
                return updater.ensure_latest_roms_json(), asked
            except OSError:
                return None, asked

    def test_checked_today_is_not_asked(self) -> None:
        (self.root / "roms.json").write_text("{}")
        self._checked(hours_ago=23)
        result, asked = self._run(return_value=self.RELEASE)
        asked.assert_not_called()
        self.assertEqual((result or {}).get("status"), "checked_recently")

    def test_checked_yesterday_is_asked_and_stamped(self) -> None:
        (self.root / "roms.json").write_text("{}")
        self._checked(hours_ago=25)
        before = cache_stamps.get("roms", "checked")
        result, asked = self._run(return_value=self.RELEASE)
        asked.assert_called_once()
        self.assertEqual((result or {}).get("status"), "up_to_date")
        self.assertNotEqual(cache_stamps.get("roms", "checked"),
                            before)

    def test_a_missing_file_is_asked_for_however_recent_the_check(self) -> None:
        self._checked(hours_ago=1)
        _, asked = self._run(side_effect=OSError("offline"))
        asked.assert_called_once()

    def test_a_failed_lookup_leaves_the_stamp_alone(self) -> None:
        (self.root / "roms.json").write_text("{}")
        self._checked(hours_ago=30)
        before = cache_stamps.get("roms", "checked")
        result, asked = self._run(side_effect=OSError("offline"))
        asked.assert_called_once()
        self.assertIsNone(result)
        self.assertEqual(cache_stamps.get("roms", "checked"), before)
