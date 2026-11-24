"""Downloading the DOF config: when a check is owed, what a run reports, and each way it fails.

The downloader is a script run in a child process, so these run a stand-in script of the
same shape: it takes `-A`, `-T` and `--force`, writes into the target and prints the lines
the real one prints. Nothing here reaches the DOF Config Tool.
"""

from __future__ import annotations

import configparser
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from typing import cast
from unittest import mock

import httpapi
from common import service_errors, timestamps
from common.config.config_store import ConfigStore
from common.host import dof_service
from common.i18n import t
from common.online import cache_stamps, dof_sync
from common.runtime import jobs
from tests.support.stamps import OwnStamps

try:
    from starlette.testclient import TestClient
except ImportError:  # pragma: no cover
    TestClient = None  # type: ignore[assignment,misc]

STAND_IN = '''\
import json, os, sys
from pathlib import Path

args = sys.argv[1:]
target = Path(args[args.index("-T") + 1])
Path(os.environ["STAND_IN_ARGS"]).write_text(json.dumps(args))
mode = os.environ.get("STAND_IN_MODE", "")
if mode:
    print(mode.replace("KEY", args[args.index("-A") + 1]))
    sys.exit(1)
target.mkdir(parents=True, exist_ok=True)
(target / "tables.ini").write_text(os.environ.get("STAND_IN_BODY", "a"))
print("Done: config updated to v1.")
'''


def _config(download: str = "weekly", key: str = "secret-key") -> ConfigStore:
    parser = configparser.ConfigParser()
    parser.read_dict({"dof": {"download_config": download, "config_tool_api_key": key}})
    return cast(ConfigStore, parser)


def _ago(seconds: float) -> str:
    now = timestamps.iso_to_epoch(timestamps.utc_now_iso()) or 0
    return timestamps.epoch_to_iso(now - seconds)


class DueTests(OwnStamps):
    def test_never_checked_is_due(self) -> None:
        self.assertTrue(dof_sync.due(_config()))

    def test_never_is_a_decision_not_to_ask(self) -> None:
        self.assertFalse(dof_sync.due(_config("never")))

    def test_without_a_key_nothing_is_due(self) -> None:
        self.assertFalse(dof_sync.due(_config(key="")))
        self.assertFalse(dof_sync.due(_config(key="   ")))

    def test_inside_the_interval_is_not_due_and_past_it_is(self) -> None:
        cache_stamps.put(dof_sync.STAMPS, checked=_ago(3600))
        self.assertFalse(dof_sync.due(_config("daily")))
        cache_stamps.put(dof_sync.STAMPS, checked=_ago(90000))
        self.assertTrue(dof_sync.due(_config("daily")))
        self.assertFalse(dof_sync.due(_config("weekly")))

    def test_a_schedule_this_build_does_not_know_asks_nothing(self) -> None:
        self.assertFalse(dof_sync.due(_config("hourly")))

    def test_weekly_is_what_a_config_with_no_choice_gets(self) -> None:
        parser = configparser.ConfigParser()
        parser.read_dict({"dof": {"config_tool_api_key": "k"}})
        self.assertEqual(dof_sync.schedule(cast(ConfigStore, parser)), "weekly")


class RunTests(OwnStamps):
    def setUp(self) -> None:
        super().setUp()
        held = tempfile.TemporaryDirectory()
        self.addCleanup(held.cleanup)
        root = Path(held.name)
        self.script = root / "ledcontrol_pull.py"
        self.script.write_text(STAND_IN)
        self.target = root / "directoutputconfig"
        self.args = root / "args.json"
        env = mock.patch.dict(os.environ, {"STAND_IN_ARGS": str(self.args)})
        env.start()
        self.addCleanup(env.stop)
        self.cache = root / "cache"
        for patch in (
                mock.patch.object(dof_sync, "CACHE_DIR", self.cache),
                mock.patch.object(dof_service, "find_dof_file", lambda *_names: self.script),
                mock.patch.object(dof_sync.dof_config, "config_folder", lambda: self.target)):
            self.enterContext(patch)

    def _mode(self, **values: str) -> None:
        env = mock.patch.dict(os.environ, values)
        env.start()
        self.addCleanup(env.stop)

    def _asked(self) -> list[str]:
        return json.loads(self.args.read_text())

    def test_a_new_file_reads_as_changed_and_the_check_is_stamped(self) -> None:
        result = dof_sync.run(_config(), force=True)

        self.assertTrue(result["changed"])
        self.assertEqual(result["at"], dof_sync.checked_at())
        self.assertTrue(dof_sync.checked_at())
        self.assertEqual((self.target / "tables.ini").read_text(), "a")

    def test_the_same_files_again_read_as_not_changed(self) -> None:
        dof_sync.run(_config(), force=True)

        self.assertFalse(dof_sync.run(_config(), force=True)["changed"])

    def test_a_different_file_reads_as_changed(self) -> None:
        dof_sync.run(_config(), force=True)
        self._mode(STAND_IN_BODY="b")

        self.assertTrue(dof_sync.run(_config(), force=True)["changed"])

    def test_a_press_forces_and_the_schedule_does_not(self) -> None:
        dof_sync.run(_config(), force=True)
        forced = self._asked()
        dof_sync.run(_config(), force=False)
        scheduled = self._asked()

        self.assertIn("--force", forced)
        self.assertNotIn("--force", scheduled)
        self.assertEqual(forced[forced.index("-A") + 1], "secret-key")
        self.assertEqual(forced[forced.index("-T") + 1], str(self.target))

    def test_the_zip_goes_to_the_cache_not_beside_the_script(self) -> None:
        dof_sync.run(_config(), force=True)

        asked = self._asked()
        self.assertEqual(asked[asked.index("-F") + 1], str(self.cache / "dof_config.zip"))
        self.assertTrue(self.cache.is_dir())

    def test_each_failure_leads_with_words_and_carries_the_scripts_line(self) -> None:
        for said, words in (
                ("** Failed download (HTTP 401)", "said.dof_config.refused"),
                ("** response is not a valid ZIP archive", "said.dof_config.refused"),
                ("** Failed to retrieve online version", "said.dof_config.unreached"),
                ("** Error: Could not create destination path", "said.dof_config.folder"),
                ("** something nobody planned for", "said.dof_config.failed")):
            self._mode(STAND_IN_MODE=said)
            with self.subTest(said=said), self.assertRaises(dof_sync.DofConfigError) as caught:
                dof_sync.run(_config(), force=True)

            self.assertTrue(str(caught.exception).startswith(t(words)), str(caught.exception))
            self.assertIn(said.lstrip("* "), str(caught.exception))

    def test_a_failure_is_stamped_so_it_is_not_asked_again_at_once(self) -> None:
        self._mode(STAND_IN_MODE="** Failed download (HTTP 500)")
        with self.assertRaises(dof_sync.DofConfigError):
            dof_sync.run(_config(), force=False)

        self.assertFalse(dof_sync.due(_config()))

    def test_a_firewall_refusal_does_not_blame_the_key(self) -> None:
        self._mode(STAND_IN_MODE="** Failed download (HTTP 403)")
        with self.assertRaises(dof_sync.DofConfigError) as caught:
            dof_sync.run(_config(), force=True)

        self.assertTrue(str(caught.exception).startswith(t("said.dof_config.unreached")))

    def test_only_the_scripts_failure_lines_decide_the_words(self) -> None:
        self._mode(STAND_IN_MODE="Reading from /tmp/timeout-read-only\n** something nobody planned")
        with self.assertRaises(dof_sync.DofConfigError) as caught:
            dof_sync.run(_config(), force=True)

        self.assertTrue(str(caught.exception).startswith(t("said.dof_config.failed")))

    def test_the_key_is_never_in_what_a_person_reads(self) -> None:
        self._mode(STAND_IN_MODE="** Failed download (HTTP 401) for ?key=KEY")
        with self.assertRaises(dof_sync.DofConfigError) as caught:
            dof_sync.run(_config(), force=True)

        self.assertNotIn("secret-key", str(caught.exception))
        self.assertIn("***", str(caught.exception))

    def test_a_new_key_is_due_at_once_after_a_failed_one(self) -> None:
        self._mode(STAND_IN_MODE="** Failed download (HTTP 401)")
        with self.assertRaises(dof_sync.DofConfigError):
            dof_sync.run(_config(key="typo"), force=False)

        self.assertFalse(dof_sync.due(_config(key="typo")))
        self.assertTrue(dof_sync.due(_config(key="corrected")))

    def test_a_script_that_cannot_start_says_so(self) -> None:
        with mock.patch("subprocess.run", side_effect=OSError("no such interpreter")):
            with self.assertRaises(dof_sync.DofConfigError) as caught:
                dof_sync.run(_config(), force=True)

        self.assertEqual(str(caught.exception), t("said.dof_config.failed"))

    def test_no_key_is_refused_before_anything_runs(self) -> None:
        with self.assertRaises(service_errors.RefusedError) as caught:
            dof_sync.start(_config(key=""))

        self.assertEqual(str(caught.exception), t("error.dof_config.no_key"))
        self.assertFalse(self.args.exists())

    def test_no_script_or_no_folder_is_unavailable(self) -> None:
        with mock.patch.object(dof_service, "find_dof_file", lambda *_names: None):
            with self.assertRaises(service_errors.UnavailableError) as caught:
                dof_sync.start(_config())
        self.assertEqual(str(caught.exception), t("error.dof_config.not_installed"))

        with mock.patch.object(dof_sync.dof_config, "config_folder", lambda: None):
            with self.assertRaises(service_errors.UnavailableError) as caught:
                dof_sync.start(_config())
        self.assertEqual(str(caught.exception), t("error.dof_config.no_folder"))

    def test_a_press_is_a_job_that_says_what_it_did(self) -> None:
        jobs.reset_for_tests()
        job = dof_sync.start(_config())
        for _ in range(600):
            if job.state != jobs.RUNNING:
                break
            threading.Event().wait(0.05)

        self.assertEqual(job.state, jobs.DONE, job.error)
        self.assertEqual(job.detail, t("said.dof_config.updated"))
        self.assertIn("--force", self._asked())

    def test_a_press_that_changed_a_file_starts_the_helper_over_and_one_that_did_not_does_not(
            self) -> None:
        for body, restarts in (("b", 1), ("b", 0)):
            with self.subTest(restarts=restarts), \
                    mock.patch.object(dof_service, "reload_config") as reload:
                self._mode(STAND_IN_BODY=body)
                jobs.reset_for_tests()
                job = dof_sync.start(_config())
                for _ in range(600):
                    if job.state != jobs.RUNNING:
                        break
                    threading.Event().wait(0.05)

                self.assertEqual(job.state, jobs.DONE, job.error)
                self.assertEqual(reload.call_count, restarts)

    def test_a_helper_that_will_not_restart_does_not_fail_a_download_that_worked(self) -> None:
        with mock.patch.object(dof_service, "reload_config", side_effect=OSError("busy")):
            self._mode(STAND_IN_BODY="b")
            jobs.reset_for_tests()
            job = dof_sync.start(_config())
            for _ in range(600):
                if job.state != jobs.RUNNING:
                    break
                threading.Event().wait(0.05)

        self.assertEqual(job.state, jobs.DONE, job.error)

    def test_a_scheduled_run_never_starts_the_helper_over(self) -> None:
        with mock.patch.object(dof_service, "reload_config") as reload:
            dof_sync.run(_config(), force=False)

        reload.assert_not_called()


class WatchTests(OwnStamps):
    def _watched(self, config: ConfigStore, real: bool = False,
                 **busy: object) -> list[bool]:
        stop = threading.Event()
        forced: list[bool] = []

        def ran(_config: object, *, force: bool) -> dict:
            forced.append(force)
            return {"changed": False, "at": ""}

        with mock.patch.object(dof_sync, "run", dof_sync.run if real else ran), \
                mock.patch.object(jobs, "active", lambda _kind=None: busy.get("jobs", [])), \
                mock.patch.object(dof_sync, "_WAKE_SECONDS", 0.01):
            thread = dof_sync.start_watch(config, stop)
            threading.Event().wait(0.2)
            stop.set()
            thread.join(5)
        return forced

    def test_a_due_check_runs_without_force(self) -> None:
        self.assertEqual(self._watched(_config())[:1], [False])

    def test_a_device_with_no_downloader_is_skipped_quietly(self) -> None:
        with mock.patch.object(dof_service, "find_dof_file", lambda *_names: None), \
                self.assertNoLogs(dof_sync.logger, "WARNING"):
            self.assertEqual(self._watched(_config(), real=True), [])

    def test_nothing_runs_when_none_is_due_or_a_press_is_running(self) -> None:
        self.assertEqual(self._watched(_config("never")), [])
        self.assertEqual(self._watched(_config(), jobs=[object()]), [])


class StateTests(OwnStamps):
    def test_it_says_when_it_was_asked_and_whether_a_key_is_stored_and_never_the_key(self) -> None:
        cache_stamps.put(dof_sync.STAMPS, checked=_ago(60))

        said = dof_sync.state(_config("daily"))

        self.assertEqual(set(said), {"schedule", "checked", "due", "has_key"})
        self.assertEqual((said["schedule"], said["has_key"], said["due"]), ("daily", True, False))
        self.assertNotIn("secret-key", json.dumps(said))

    def test_no_key_reads_as_none_stored_and_nothing_due(self) -> None:
        said = dof_sync.state(_config(key=""))

        self.assertEqual((said["has_key"], said["due"], said["checked"]), (False, False, ""))


@unittest.skipIf(TestClient is None, "starlette TestClient unavailable")
class DofConfigRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)

    def test_a_press_is_accepted_as_a_job_with_a_location(self) -> None:
        job = jobs.Job(id="abc", kind=jobs.KIND_DOF_CONFIG)
        with mock.patch.object(dof_sync, "start", return_value=job):
            answer = self.client.post("/dof/config")

        self.assertEqual(answer.status_code, 202)
        self.assertEqual(answer.headers["Location"], "/api/v1/jobs/abc")
        self.assertEqual(answer.json()["kind"], jobs.KIND_DOF_CONFIG)

    def test_the_state_is_read_under_config_read_and_carries_no_key(self) -> None:
        with mock.patch.object(dof_sync, "state", return_value={
                "schedule": "weekly", "checked": "2026-10-09T10:00:00Z", "due": False,
                "has_key": True}):
            answer = self.client.get("/dof/config")

        self.assertEqual(answer.status_code, 200)
        self.assertEqual(answer.json(), {"schedule": "weekly", "checked": "2026-10-09T10:00:00Z",
                                         "due": False, "has_key": True})

    def test_each_refusal_has_its_own_status_and_words(self) -> None:
        refusals = (
            (service_errors.RefusedError(t("error.dof_config.no_key")), 400),
            (service_errors.UnavailableError(t("error.dof_config.no_folder")), 501),
            (jobs.JobBusyError(t("error.jobs.getting_dof_config")), 409))
        for error, status in refusals:
            with self.subTest(status=status), mock.patch.object(dof_sync, "start",
                                                                side_effect=error):
                answer = self.client.post("/dof/config")

                self.assertEqual(answer.status_code, status)
                self.assertEqual(answer.json()["error"]["message"], str(error))


if __name__ == "__main__":
    unittest.main()
