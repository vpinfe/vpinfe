"""The update check, kept a day and worked out against the running version on each read.

The transport is stubbed: nothing here reaches GitHub.
"""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

import requests

from common.online import app_updater
from common.security import http_client

MANIFEST_URL = "https://github.com/vpinfe/vpinfe/releases/download/v3.1.0/manifest.json"
RELEASE = {
    "tag_name": "v3.1.0",
    "body": "Notes nobody keeps",
    "assets": [
        {"name": "manifest.json", "browser_download_url": MANIFEST_URL, "size": 1},
        {"name": "vpinfe-linux-x64.zip", "browser_download_url": "https://x/linux.zip"},
    ],
}
MANIFEST = {"version": "v3.1.0",
            "assets": {"linux-x64": {"file": "vpinfe-linux-x64.zip", "sha256": "0"}}}
INSTALL = {"supported": True, "reason": None, "triplet": "linux-x64",
           "current_version": "v3.0.0"}
SOURCE = {**INSTALL, "supported": False, "reason": "source_build",
          "current_version": "3.0.0-dev"}
HOUR = 60 * 60
REAL_CONTEXT = app_updater.get_install_context


def _release(tag: str, **extra: Any) -> dict:
    url = f"https://github.com/vpinfe/vpinfe/releases/download/{tag}/manifest.json"
    return {"tag_name": tag, "assets": [
        {"name": "manifest.json", "browser_download_url": url},
        {"name": "vpinfe-linux-x64.zip", "browser_download_url": "https://x/linux.zip"}],
        **extra}


class UpdateCheckTest(unittest.TestCase):
    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / "cache" / "update.json"
        self.asked: list[str] = []
        self.failure: Exception | None = None
        self.install: dict[str, Any] = dict(INSTALL)
        self.followed = app_updater.STABLE
        self.releases: list[dict] = []
        self.latest = RELEASE
        self.now = time.time()
        clock = mock.Mock()
        clock.time = lambda: self.now
        for patch in (mock.patch.object(app_updater, "UPDATE_CHECK_PATH", self.path),
                      mock.patch.object(app_updater, "channel", lambda: self.followed),
                      mock.patch.object(app_updater, "get_json", self._get_json),
                      mock.patch.object(app_updater, "get_install_context",
                                        lambda: dict(self.install)),
                      mock.patch.object(app_updater, "time", clock)):
            patch.start()
            self.addCleanup(patch.stop)

    def _get_json(self, url: str, **_: Any) -> Any:
        self.asked.append(url)
        if self.failure is not None:
            raise self.failure
        answers: dict[str, Any] = {app_updater.LATEST_RELEASE_URL: self.latest,
                                   app_updater.RELEASES_URL: self.releases}
        return MANIFEST if url.endswith("/manifest.json") else answers[url]

    def _kept(self) -> dict:
        return json.loads(self.path.read_text(encoding="utf-8"))

    def test_a_first_check_asks_and_keeps_what_github_said(self) -> None:
        answer = app_updater.check_now()

        self.assertEqual(self.asked, [app_updater.LATEST_RELEASE_URL, MANIFEST_URL])
        self.assertTrue(answer["update_available"] and answer["update_supported"])
        self.assertEqual(answer["asset_name"], "vpinfe-linux-x64.zip")
        self.assertIsNone(answer["error"])
        kept = self._kept()
        self.assertEqual(kept["release"]["tag_name"], "v3.1.0")
        self.assertNotIn("body", kept["release"])
        self.assertEqual(kept["manifest"], MANIFEST)
        self.assertEqual(answer["checked_at"], kept["checked_at"])

    def test_within_a_day_nothing_is_asked(self) -> None:
        app_updater.check_now()
        self.asked.clear()
        self.now += 23 * HOUR

        self.assertTrue(app_updater.check_now()["update_supported"])
        self.assertEqual(self.asked, [])

    def test_after_a_day_it_asks_again(self) -> None:
        app_updater.check_now()
        self.asked.clear()
        self.now += 25 * HOUR

        app_updater.check_now()
        self.assertEqual(self.asked[0], app_updater.LATEST_RELEASE_URL)

    def test_refresh_asks_now(self) -> None:
        app_updater.check_now()
        self.asked.clear()

        app_updater.check_now(refresh=True)
        self.assertEqual(self.asked[0], app_updater.LATEST_RELEASE_URL)

    def test_an_install_that_just_updated_reads_itself_as_current(self) -> None:
        app_updater.check_now()
        self.asked.clear()
        self.install["current_version"] = "v3.1.0"

        answer = app_updater.check_now()
        self.assertFalse(answer["update_available"])
        self.assertEqual(answer["current_version"], "v3.1.0")
        self.assertEqual(self.asked, [])

    def test_a_kept_release_missing_the_manifest_it_now_needs_is_asked_again(self) -> None:
        self.install = {**SOURCE, "current_version": "v3.2.0"}
        app_updater.check_now()
        self.assertIsNone(self._kept()["manifest"])
        self.asked.clear()
        self.install = dict(INSTALL)

        self.assertTrue(app_updater.check_now()["update_supported"])
        self.assertEqual(self.asked, [app_updater.LATEST_RELEASE_URL, MANIFEST_URL])

    def _failing(self, level: str = "WARNING") -> Any:
        return self.assertLogs("vpinfe.common.online.app_updater", level)

    def test_a_failed_attempt_says_so_beside_the_last_success(self) -> None:
        checked = app_updater.check_now()["checked_at"]
        self.now += 25 * HOUR
        self.failure = requests.ConnectionError("no route")

        with self._failing():
            answer = app_updater.check_now()
        self.assertEqual(answer["error"], "remote_check_failed")
        self.assertEqual(answer["checked_at"], checked)
        self.assertEqual(answer["latest_version"], "v3.1.0")

    def test_a_failed_attempt_waits_an_hour_unless_refreshed(self) -> None:
        self.failure = requests.ConnectionError("no route")
        with self._failing():
            self.assertIsNone(app_updater.check_now()["checked_at"])
        self.asked.clear()

        self.now += HOUR - 60
        self.assertEqual(app_updater.check_now()["error"], "remote_check_failed")
        self.assertEqual(self.asked, [])

        with self._failing():
            app_updater.check_now(refresh=True)
        self.assertEqual(len(self.asked), 1)

        self.now += HOUR + 60
        self.failure = None
        answer = app_updater.check_now()
        self.assertIsNone(answer["error"])
        self.assertIsNotNone(answer["checked_at"])

    def test_a_network_failure_is_one_line(self) -> None:
        self.failure = requests.HTTPError("503 Server Error")
        with self._failing() as logs:
            app_updater.check_now()
        self.assertEqual([(r.levelname, r.exc_info) for r in logs.records
                          if r.levelname != "INFO"], [("WARNING", None)])
        self.assertIn("503 Server Error", logs.output[0])

    def test_a_host_that_said_wait_is_not_warned_about_again(self) -> None:
        self.failure = http_client.HostQuietError("api.github.com", self.now + 60)
        with self._failing("DEBUG") as logs:
            self.assertEqual(app_updater.check_now()["error"], "remote_check_failed")
        self.assertEqual([r.levelname for r in logs.records if r.levelname == "WARNING"], [])

    def test_anything_else_keeps_its_traceback(self) -> None:
        self.failure = KeyError("tag_name")
        with self._failing("ERROR") as logs:
            self.assertEqual(app_updater.check_now()["error"], "remote_check_failed")
        self.assertIsNotNone(logs.records[0].exc_info)

    def test_a_source_build_is_not_an_error(self) -> None:
        self.install = dict(SOURCE)

        answer = app_updater.check_now()
        self.assertIsNone(answer["error"])
        self.assertEqual(answer["support_reason"], "source_build")
        self.assertEqual(self.asked, [app_updater.LATEST_RELEASE_URL])

    def test_a_build_with_no_version_to_weigh_is_not_told_the_release_is_newer(self) -> None:
        self.install = {**SOURCE, "current_version": "dev-local"}

        answer = app_updater.check_now()
        self.assertIsNone(answer["update_available"])
        self.assertEqual(answer["latest_version"], "v3.1.0")

    def _on_beta(self, *releases: dict) -> None:
        self.followed = app_updater.BETA
        self.releases = list(releases)

    def test_the_stable_channel_never_reads_the_list(self) -> None:
        self.releases = [_release("v9.0.0")]

        self.assertEqual(app_updater.check_now()["latest_version"], "v3.1.0")
        self.assertNotIn(app_updater.RELEASES_URL, self.asked)

    def test_the_beta_channel_takes_the_highest_version_not_the_first_listed(self) -> None:
        self._on_beta(_release("v3.0.5"), _release("v3.2.0-beta.2"),
                      _release("v3.2.0-beta.10"), _release("v3.2.0-beta.9"))

        answer = app_updater.check_now()
        self.assertEqual(answer["latest_version"], "v3.2.0-beta.10")
        self.assertTrue(answer["update_available"] and answer["update_supported"])
        self.assertEqual(self.asked[0], app_updater.RELEASES_URL)

    def test_a_beta_is_not_offered_over_its_own_final(self) -> None:
        self._on_beta(_release("v3.2.0-beta.4"), _release("v3.2.0"))
        self.assertEqual(app_updater.check_now()["latest_version"], "v3.2.0")

    def test_a_build_on_a_beta_is_offered_the_next_beta_and_then_the_final(self) -> None:
        self.install["current_version"] = "v3.2.0-beta.1"
        self._on_beta(_release("v3.2.0-beta.2"))
        self.assertTrue(app_updater.check_now()["update_available"])

        self.install["current_version"] = "v3.2.0-beta.2"
        self.assertFalse(app_updater.check_now()["update_available"])

        self.releases = [_release("v3.2.0-beta.2"), _release("v3.2.0")]
        self.assertTrue(app_updater.check_now(refresh=True)["update_available"])

    def test_a_beta_build_on_stable_is_offered_the_final_once_it_is_out(self) -> None:
        self.install["current_version"] = "v3.1.0-beta.2"

        self.assertTrue(app_updater.check_now()["update_available"])

    def test_the_update_itself_comes_from_the_channel_being_followed(self) -> None:
        taken: list[str] = []

        def stop_at_the_release(payload: dict) -> dict:
            taken.append(payload["tag_name"])
            raise app_updater.UpdateError("stop")

        self.releases = [_release("v3.2.0-beta.1"), _release("v3.0.5")]
        with mock.patch.object(app_updater, "_get_release_manifest", stop_at_the_release):
            for followed in (app_updater.STABLE, app_updater.BETA):
                self.followed = followed
                with self.assertRaises(app_updater.UpdateError):
                    app_updater.prepare_update()

        self.assertEqual(taken, ["v3.1.0", "v3.2.0-beta.1"])

    def test_a_beta_build_on_stable_is_not_offered_the_older_final(self) -> None:
        self.install["current_version"] = "v3.2.0-beta.2"

        self.assertFalse(app_updater.check_now()["update_available"])

    def test_stable_never_takes_a_prerelease_even_one_github_did_not_flag(self) -> None:
        for latest in (_release("v3.2.0-beta.1", prerelease=True), _release("v3.2.0-beta.1")):
            with self.subTest(latest=latest):
                self.latest = latest
                with self._failing("DEBUG"):
                    answer = app_updater.check_now(refresh=True)
                self.assertEqual(answer["error"], "remote_check_failed")
                self.assertIsNone(answer["latest_version"])

    def test_a_kept_answer_with_no_channel_is_a_stable_one(self) -> None:
        app_updater.check_now()
        kept = self._kept()
        del kept["channel"]
        self.path.write_text(json.dumps(kept), encoding="utf-8")
        self.asked.clear()

        app_updater.check_now()
        self.assertEqual(self.asked, [])
        self.followed = app_updater.BETA
        self.releases = [_release("v3.2.0-beta.2")]
        app_updater.check_now()
        self.assertEqual(self.asked[0], app_updater.RELEASES_URL)

    def test_drafts_and_tags_that_are_not_versions_are_passed_over(self) -> None:
        self._on_beta(_release("v3.3.0", draft=True), _release("nightly"),
                      _release("v3.1.5"))
        self.assertEqual(app_updater.check_now()["latest_version"], "v3.1.5")

    def test_a_list_with_no_release_in_it_is_a_failed_check(self) -> None:
        self._on_beta(_release("nightly"))

        self.assertEqual(app_updater.check_now()["error"], "remote_check_failed")

    def test_switching_channel_asks_again_inside_the_day(self) -> None:
        app_updater.check_now()
        self.asked.clear()
        self._on_beta(_release("v3.2.0-beta.2"))

        answer = app_updater.check_now()
        self.assertEqual(answer["latest_version"], "v3.2.0-beta.2")
        self.assertEqual(self.asked[0], app_updater.RELEASES_URL)

    def test_a_failed_ask_on_the_other_channel_does_not_show_the_old_channels_release(
            self) -> None:
        app_updater.check_now()
        self.now += 25 * HOUR
        self._on_beta(_release("v3.2.0-beta.2"))
        self.failure = requests.ConnectionError("no route")

        with self._failing():
            answer = app_updater.check_now()
        self.assertIsNone(answer["latest_version"])
        self.assertEqual(answer["error"], "remote_check_failed")

    def _context_of(self, version: str) -> dict:
        with mock.patch.object(app_updater, "get_version", lambda: version), \
                mock.patch.object(app_updater.sys, "frozen", True, create=True), \
                mock.patch.object(app_updater.platform, "system", lambda: "Linux"), \
                mock.patch.object(app_updater.platform, "machine", lambda: "x86_64"):
            return REAL_CONTEXT()

    def test_a_beta_build_can_update_itself_and_a_dev_build_cannot(self) -> None:
        self.assertTrue(self._context_of("v3.0.0-beta.1")["supported"])
        self.assertEqual(self._context_of("dev-local")["reason"], "non_release_build")

    def test_a_kept_answer_that_cannot_be_read_is_asked_again(self) -> None:
        self.path.parent.mkdir(parents=True)
        self.path.write_text("{not json", encoding="utf-8")

        with self.assertLogs("vpinfe.common.online.app_updater", "WARNING"):
            app_updater.check_now()
        self.assertEqual(self.asked[0], app_updater.LATEST_RELEASE_URL)


if __name__ == "__main__":
    unittest.main()
