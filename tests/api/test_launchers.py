"""Launchers over the wire.

The id is the caller's to send, which is the part worth pinning: it is also how a launcher
copied to a cabinet lands without being renumbered, and renumbering would break every
mapping that travelled with it.
"""

import asyncio
import configparser
import os
import pathlib
import subprocess
import sys
import threading
import time
import unittest
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from nicegui import run, ui

import httpapi
from common import service_errors
from common.games import launcher_ops, launchers
from common.runtime import jobs
from manager.kit import data, panel
from manager.kit.api import ApiClient
from manager.settings import settings as settings_page
from manager.workbench import all_settings, app_settings
from manager.workbench import file as wb_file
from tests.support.launchers_api import delete_launcher, put_launcher
from tests.support.library import own_launchers
from tests.support.workbench_patch import patch_workbench


def _client() -> TestClient:
    return TestClient(httpapi.create_api_app(), raise_server_exceptions=False)


class LauncherApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = own_launchers(self, self.tmp.name)
        patcher = patch.object(launchers, "get_launcher_store",
                               return_value=self.store)
        patcher.start()
        self.addCleanup(patcher.stop)
        # Marked as already seeded: building the app runs the startup pass, and a
        # shipped launcher appearing under these would make every count here wrong.
        # A real install is in this state from its second start onwards.
        self.store.save([], {})
        self.client = _client()

    def _put(self, launcher_id: str, **body):
        return put_launcher(self.client, launcher_id, json={"app": "vpx", **body})

    def test_an_install_with_none_answers_an_empty_list(self) -> None:
        body = self.client.get("/launchers").json()

        self.assertEqual(body["launchers"], [])
        self.assertEqual(body["mappings"], {})
        self.assertIsNone(body["defaults"]["vpx"])

    def test_a_launcher_carries_the_shape_of_its_own_settings(self) -> None:
        """So a client can draw an editor without knowing what a Visual Pinball launcher
        happens to hold - which is the point of the app declaring its fields."""
        self._put("one", display_name="VPX")

        found = self.client.get("/launchers").json()["launchers"][0]

        self.assertEqual(found["app_name"], "Visual Pinball X")
        keys = [field["key"] for field in found["fields"]]
        self.assertIn("bin_path", keys)
        self.assertEqual(sorted(found["settings"]), sorted(keys))

    def test_an_empty_settings_file_says_which_file_it_stands_for(self) -> None:
        own = pathlib.Path(self.tmp.name, "VPinballX.ini")
        own.write_text("[Player]\n", encoding="utf-8")
        self._put("one", display_name="VPX")

        with patch("apps.vpx.config.own_file", return_value=own):
            found = self.client.get("/launchers").json()["launchers"][0]

        fields = {one["key"]: one for one in found["fields"]}
        self.assertEqual((fields["ini_path"]["default_path"], fields["ini_path"]["default_exists"],
                          fields["ini_path"]["blank"]), (str(own), True, ""))
        self.assertNotIn("default_path", fields["bin_path"])
        self.assertEqual((fields["bin_path"]["blank"], fields["bin_path"]["left_empty"]),
                         ("", ""))

    def test_a_settings_file_not_made_yet_still_names_where_it_will_be(self) -> None:
        made = pathlib.Path(self.tmp.name, "10.8", "VPinballX.ini")
        self._put("one", display_name="VPX")

        with patch("apps.vpx.config.own_file", return_value=None), \
                patch("apps.vpx.config.preferences_file", return_value=made):
            found = self.client.get("/launchers").json()["launchers"][0]

        ini = {one["key"]: one for one in found["fields"]}["ini_path"]
        self.assertEqual((ini["default_path"], ini["default_exists"]), (str(made), False))

    def test_a_settings_file_with_no_place_to_be_reads_not_found_and_why(self) -> None:
        self._put("one", display_name="VPX")

        with patch("apps.vpx.config.own_file", return_value=None), \
                patch("apps.vpx.config.preferences_file", return_value=None):
            found = self.client.get("/launchers").json()["launchers"][0]

        ini = {one["key"]: one for one in found["fields"]}["ini_path"]
        self.assertEqual((ini["blank"], ini["left_empty"], "default_path" in ini),
                         ("Not found", "Visual Pinball makes it the first time it runs", False))

    def test_standing_answers_for_an_app_before_any_launcher_holds_it(self) -> None:
        own = pathlib.Path(self.tmp.name, "VPinballX.ini")

        with patch("apps.vpx.config.own_file", return_value=own) as found:
            body = self.client.post("/launchers/standing", json={
                "app": "vpx", "settings": {"bin_path": "/x/VPinballX_BGFX"}}).json()

        found.assert_called_with("/x/VPinballX_BGFX")
        self.assertEqual(body["ini_path"]["default_path"], str(own))
        self.assertNotIn("bin_path", body)
        self.assertEqual(self.client.get("/launchers").json()["launchers"], [])

    def test_standing_for_an_app_with_none_to_say_is_empty(self) -> None:
        for app in ("generic", "nothing"):
            self.assertEqual(self.client.post("/launchers/standing",
                                              json={"app": app, "settings": {}}).json(), {})

    def test_a_launcher_says_whether_its_app_keeps_settings_of_its_own(self) -> None:
        self._put("vpx", display_name="VPX")
        self._put("gen", app="generic", display_name="Generic")

        held = {one["launcher_id"]: one["has_config"]
                for one in self.client.get("/launchers").json()["launchers"]}

        self.assertEqual(held, {"vpx": True, "gen": False})

    def test_an_app_lists_the_paths_a_new_launcher_of_it_is_asked_for(self) -> None:
        """What Add asks, before there is a launcher to read its fields from."""
        vpx = next(one for one in self.client.get("/launchers").json()["apps"]
                   if one["id"] == "vpx")

        paths = [(one["key"], one["label"], one["blank"], one["path"], one["suffixes"])
                 for one in vpx["fields"] if one["path"]]

        self.assertEqual(paths, [("bin_path", "Program", "", "exe", []),
                                 ("ini_path", "Settings File", "", "file", [".ini"])])

    def test_an_app_says_whether_it_has_settings_of_its_own(self) -> None:
        """What a table played by another program is told it gives up."""
        held = {one["id"]: one["has_config"]
                for one in self.client.get("/launchers").json()["apps"]}

        self.assertEqual((held["vpx"], held["generic"]), (True, False))

    def test_making_one_the_default_moves_it_to_the_front(self) -> None:
        self._put("first", display_name="VPX")
        self._put("gen", app="generic", display_name="Generic")
        self._put("wide", display_name="VPX (4K)", settings={"bin_path": "/opt/vpx"})

        got = self.client.post("/launchers/wide/default")

        self.assertEqual(got.status_code, 200, got.text)
        body = self.client.get("/launchers").json()
        self.assertEqual(body["defaults"]["vpx"], "wide")
        self.assertEqual([one["launcher_id"] for one in body["launchers"]],
                         ["wide", "first", "gen"])

    def test_a_switched_off_one_cannot_be_the_default(self) -> None:
        self._put("first", display_name="VPX")
        self._put("off", display_name="VPX (4K)", enabled=False,
                  settings={"bin_path": "/opt/vpx"})

        got = self.client.post("/launchers/off/default")

        self.assertEqual(got.status_code, 400, got.text)
        self.assertIn("switched off", got.json()["error"]["message"])
        self.assertEqual(self.client.get("/launchers").json()["defaults"]["vpx"], "first")

    def test_one_with_no_program_cannot_be_the_default(self) -> None:
        """Tables that name no launcher would go to one that cannot start them."""
        self._put("first", display_name="VPX", settings={"bin_path": "/opt/vpx"})
        self._put("bare", display_name="VPX (4K)")

        got = self.client.post("/launchers/bare/default")

        self.assertEqual(got.status_code, 400, got.text)
        self.assertEqual(got.json()["error"]["message"],
                         "VPX (4K) has no program. Set one to make it the default.")
        self.assertEqual(self.client.get("/launchers").json()["defaults"]["vpx"], "first")

    def test_a_table_says_when_it_falls_back(self) -> None:
        """Set here and in effect, set here and switched off, and following the default
        are three different things to a reader."""
        from common.games import table_lens

        self._put("first", display_name="VPX", settings={"bin_path": "/opt/vpx"})
        self._put("wide", display_name="VPX (4K)", settings={"bin_path": "/opt/vpx"})
        self._put("off", display_name="VPX (old)", enabled=False)
        self.store.save(self.store.launchers(), {"t-wide": "wide", "t-off": "off"})

        said = {table: (found["launcher"], found["launcher_set_here"],
                        found["launcher_falls_back"])
                for table in ("t-wide", "t-off", "t-follows")
                for found in [table_lens.launcher_of("vpx", table)]}

        self.assertEqual(said, {"t-wide": ("wide", True, False),
                                "t-off": ("first", True, True),
                                "t-follows": ("first", False, False)})

    def test_the_caller_names_the_id(self) -> None:
        """A launcher copied from another machine is that launcher. Minting a new id here
        would break the mappings that came with it."""
        self._put("kept-id", display_name="VPX")

        self.assertEqual(self.client.get("/launchers").json()["launchers"][0]
                         ["launcher_id"], "kept-id")

    def test_putting_it_again_replaces_it(self) -> None:
        self._put("one", display_name="First")
        self._put("one", display_name="Second")

        held = self.client.get("/launchers").json()["launchers"]

        self.assertEqual([one["display_name"] for one in held], ["Second"])

    def test_a_name_another_launcher_has_is_refused(self) -> None:
        self._put("one", display_name="VPX")

        refused = self._put("two", display_name=" vpx ")

        self.assertEqual(refused.status_code, 400)
        self.assertEqual(refused.json()["error"]["message"],
                         "Another launcher is already called vpx")
        self.assertEqual(len(self.client.get("/launchers").json()["launchers"]), 1)

    def test_whatever_app_it_runs(self) -> None:
        self._put("one", display_name="Mine")

        self.assertEqual(self._put("two", app="generic", display_name="MINE").status_code,
                         400)

    def test_a_launcher_keeps_its_own_name(self) -> None:
        self._put("one", display_name="VPX")

        self.assertEqual(self._put("one", display_name="VPX", enabled=True).status_code, 200)

    def test_a_blank_name_is_its_app_s_and_that_is_checked_too(self) -> None:
        self._put("one", display_name="Visual Pinball X")

        self.assertEqual(self._put("two").status_code, 400)

    def test_the_default_is_named_rather_than_left_to_be_worked_out(self) -> None:
        """A client re-deriving "first enabled for this app" is a second place for the
        rule to be wrong."""
        self._put("off", display_name="Off", enabled=False)
        self._put("on", display_name="On")

        self.assertEqual(self.client.get("/launchers").json()["defaults"]["vpx"], "on")

    def test_an_app_this_build_does_not_know_is_refused(self) -> None:
        """A typo would make a launcher nothing can ever run, listed as though it could."""
        response = put_launcher(self.client, "one", json={"app": "atari-pinball"})

        self.assertEqual(response.status_code, 400)
        self.assertIn("atari-pinball", response.json()["error"]["message"])

    def _checks(self) -> dict:
        """The states `/config/paths` gives for launcher one, by field."""
        found = self.client.get("/config/paths").json()["checks"]
        return {one["field"]: one for one in found
                if one.get("list") == "launchers" and one["id"] == "one"}

    def test_a_launcher_says_what_the_disk_makes_of_its_paths(self) -> None:
        """Without it, one pointing at a program that has been uninstalled looks exactly
        like one that works, and the list cannot say which of two can run a table."""
        self._put("one", settings={"bin_path": "/nope/VPinballX"})

        checks = self._checks()

        self.assertEqual(checks["bin_path"]["state"], "missing")
        self.assertTrue(checks["bin_path"]["reason"])

    def test_a_path_that_is_there_is_reported_ok(self) -> None:
        import os
        program = os.path.join(self.tmp.name, "VPinballX")
        with open(program, "w", encoding="utf-8"):
            pass
        os.chmod(program, 0o755)
        self._put("one", settings={"bin_path": program})

        checks = self._checks()

        self.assertEqual(checks["bin_path"]["state"], "ok")
        self.assertEqual(checks["bin_path"]["reason"], "")

    def test_only_the_fields_that_name_a_path_are_checked(self) -> None:
        """A state on every field would be a mark on every row, which says nothing."""
        self._put("one", settings={"bin_path": "/nope/VPinballX"})

        checks = self._checks()

        self.assertNotIn("launch_env", checks)
        self.assertNotIn("log_delete_on_start", checks)

    def test_removing_one_takes_its_mappings(self) -> None:
        self._put("one", display_name="VPX")
        self.client.put("/launchers/mappings/t1", json={"launcher_id": "one"})

        delete_launcher(self.client, "one")

        body = self.client.get("/launchers").json()
        self.assertEqual(body["launchers"], [])
        self.assertEqual(body["mappings"], {})

    def test_removing_one_that_is_not_there_is_a_404(self) -> None:
        self.assertEqual(delete_launcher(self.client, "ghost").status_code, 404)

    def test_a_table_can_be_pointed_at_one_and_cleared(self) -> None:
        self._put("one", display_name="VPX")

        self.client.put("/launchers/mappings/t1", json={"launcher_id": "one"})
        self.assertEqual(self.client.get("/launchers").json()["mappings"],
                         {"t1": "one"})

        self.client.put("/launchers/mappings/t1", json={"launcher_id": ""})
        self.assertEqual(self.client.get("/launchers").json()["mappings"], {},
                         "cleared drops the row, because absent already means default")

    def test_pointing_a_table_at_a_launcher_that_is_not_there_is_refused(self) -> None:
        """Storing it would be a table reporting an override it does not have."""
        response = self.client.put("/launchers/mappings/t1",
                                   json={"launcher_id": "ghost"})

        self.assertEqual(response.status_code, 404)


class ConfigFilesTests(unittest.TestCase):
    """What a copy of a launcher's settings takes, and the routes that went."""

    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = own_launchers(self, self.tmp.name)
        patcher = patch.object(launchers, "get_launcher_store", return_value=self.store)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.store.save([], {})
        self.client = _client()
        self.ini = pathlib.Path(self.tmp.name, "VPinballX.ini")
        self.ini.write_text("[Player]\nFXAA = 1\n")
        put_launcher(self.client, "l1", json={"app": "vpx", "settings": {
            "bin_path": "/opt/vpx", "ini_path": str(self.ini)}})

    def test_the_config_names_the_file_a_copy_takes(self) -> None:
        got = self.client.get("/launchers/l1/config")

        self.assertEqual(got.status_code, 200, got.text)
        self.assertEqual(got.json()["files"], {"application": str(self.ini)})

    def test_the_routes_that_listed_and_took_copies_answer_404(self) -> None:
        self.assertEqual(self.client.get("/launchers/l1/config/backups").status_code, 404)
        self.assertEqual(self.client.post("/launchers/l1/config/backups",
                                          json={}).status_code, 404)


class MakeSettingsFileTests(unittest.TestCase):
    """The program asked once for the file it has not written yet."""

    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = own_launchers(self, self.tmp.name)
        patcher = patch.object(launchers, "get_launcher_store", return_value=self.store)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(launcher_ops._wrote_nothing.clear)
        self.store.save([], {})
        self.client = _client()
        self.ini = pathlib.Path(self.tmp.name, "VPinballX.ini")
        self.program = pathlib.Path(self.tmp.name, "vpx")
        self.program.write_text("#!/bin/sh\n")
        self.program.chmod(0o755)
        put_launcher(self.client, "l1", json={"app": "vpx", "settings": {
            "bin_path": str(self.program), "ini_path": str(self.ini)}})

    def _run(self, writes: bool = True) -> Any:
        def made(command: list[str], **_: Any) -> None:
            if writes:
                self.ini.write_text("[Player]\nFXAA = 1\n")
        return patch("common.games.launcher_ops.subprocess.run", side_effect=made)

    def _launcher(self) -> dict[str, Any]:
        return next(one for one in self.client.get("/launchers").json()["launchers"]
                    if one["launcher_id"] == "l1")

    def test_the_listing_says_the_file_is_missing_and_can_be_made(self) -> None:
        found = self._launcher()

        self.assertIs(found["has_settings_file"], False)
        self.assertIs(found["can_make_settings_file"], True)

    def test_the_program_runs_with_the_ini_and_the_flag_and_the_file_is_answered(self) -> None:
        with self._run() as run_program:
            got = self.client.post("/launchers/l1/config/file")

        self.assertEqual(got.status_code, 200, got.text)
        self.assertEqual(got.json()["files"], {"application": str(self.ini)})
        command = run_program.call_args.args[0]
        self.assertEqual(command[1:], ["-ini", str(self.ini), "-v"])
        self.assertIs(self._launcher()["has_settings_file"], True)

    def test_a_program_that_leaves_no_file_is_refused(self) -> None:
        with self._run(writes=False):
            got = self.client.post("/launchers/l1/config/file")

        self.assertEqual(got.status_code, 400, got.text)
        self.assertFalse(self.ini.exists())

    def test_a_program_that_left_no_file_is_not_offered_again(self) -> None:
        with self._run(writes=False):
            self.client.post("/launchers/l1/config/file")

        found = self._launcher()
        self.assertIs(found["settings_file_not_made"], True)
        self.assertIs(found["can_make_settings_file"], False)

    def test_a_different_program_is_offered_again(self) -> None:
        with self._run(writes=False):
            self.client.post("/launchers/l1/config/file")
        other = pathlib.Path(self.tmp.name, "vpx2")
        other.write_text("#!/bin/sh\n", encoding="utf-8")
        other.chmod(0o755)
        put_launcher(self.client, "l1", json={"app": "vpx", "settings": {
            "bin_path": str(other), "ini_path": str(self.ini)}})

        found = self._launcher()
        self.assertIs(found["settings_file_not_made"], False)
        self.assertIs(found["can_make_settings_file"], True)

    def test_a_program_stopped_for_running_too_long_is_offered_again(self) -> None:
        with patch("common.games.launcher_ops.subprocess.run",
                   side_effect=subprocess.TimeoutExpired("vpx", 30)):
            self.client.post("/launchers/l1/config/file")

        self.assertIs(self._launcher()["can_make_settings_file"], True)

    def test_a_file_made_since_a_run_that_made_none_ends_the_hiding(self) -> None:
        with self._run(writes=False):
            self.client.post("/launchers/l1/config/file")
        self.ini.write_text("[Player]\n")
        self.assertIs(self._launcher()["settings_file_not_made"], False)
        self.ini.unlink()

        self.assertIs(self._launcher()["can_make_settings_file"], True)

    def test_a_program_that_cannot_start_is_refused_and_the_reason_is_logged(self) -> None:
        with patch("common.games.launcher_ops.subprocess.run",
                   side_effect=PermissionError("not allowed")), \
                self.assertLogs("vpinfe.common.games.launcher_ops", "WARNING") as logged:
            got = self.client.post("/launchers/l1/config/file")

        self.assertEqual(got.status_code, 400, got.text)
        self.assertIn("not allowed", logged.output[0])

    @unittest.skipIf(sys.platform == "win32", "Windows has no modes")
    def test_a_linux_account_with_nowhere_to_save_is_refused_before_anything_runs(self) -> None:
        shut = pathlib.Path(self.tmp.name, "shut")
        shut.mkdir()
        shut.chmod(0o500)
        self.addCleanup(shut.chmod, 0o700)
        with patch("common.games.launcher_ops.sys.platform", "linux"), \
                patch.dict(os.environ, {"HOME": str(shut / "home"), "XDG_DATA_HOME": ""}), \
                self._run() as run_program:
            got = self.client.post("/launchers/l1/config/file")

        self.assertEqual(got.status_code, 400, got.text)
        run_program.assert_not_called()

    def test_a_linux_data_home_not_made_yet_under_a_writable_folder_is_enough(self) -> None:
        later = pathlib.Path(self.tmp.name, "not", "yet")
        with patch("common.games.launcher_ops.sys.platform", "linux"), \
                patch.dict(os.environ, {"HOME": "", "XDG_DATA_HOME": str(later)}), \
                self._run():
            got = self.client.post("/launchers/l1/config/file")

        self.assertEqual(got.status_code, 200, got.text)

    def test_a_program_that_is_not_there_cannot_be_asked(self) -> None:
        self.program.unlink()

        self.assertIs(self._launcher()["can_make_settings_file"], False)

    def test_nothing_runs_while_a_table_is_playing(self) -> None:
        with patch("common.games.launcher_ops.launch_state.current",
                   return_value=SimpleNamespace(launching=True)), \
                self._run() as run_program:
            got = self.client.post("/launchers/l1/config/file")

        self.assertEqual(got.status_code, 409, got.text)
        run_program.assert_not_called()

    def test_a_second_request_while_the_program_runs_is_refused(self) -> None:
        with launcher_ops._making_file, self._run() as run_program:
            got = self.client.post("/launchers/l1/config/file")

        self.assertEqual(got.status_code, 409, got.text)
        run_program.assert_not_called()
        self.assertFalse(launcher_ops._making_file.locked())

    def test_a_program_that_stays_open_is_stopped_and_the_file_it_wrote_is_answered(self) -> None:
        def hangs(command: list[str], **_: Any) -> None:
            self.ini.write_text("[Player]\nFXAA = 1\n")
            raise subprocess.TimeoutExpired(command, 30)

        with patch("common.games.launcher_ops.subprocess.run", side_effect=hangs), \
                self.assertLogs("vpinfe.common.games.launcher_ops", "WARNING"):
            got = self.client.post("/launchers/l1/config/file")

        self.assertEqual(got.status_code, 200, got.text)

    def test_a_program_that_stays_open_and_wrote_nothing_is_refused(self) -> None:
        def hangs(command: list[str], **_: Any) -> None:
            raise subprocess.TimeoutExpired(command, 30)

        with patch("common.games.launcher_ops.subprocess.run", side_effect=hangs), \
                self.assertLogs("vpinfe.common.games.launcher_ops", "WARNING"):
            got = self.client.post("/launchers/l1/config/file")

        self.assertEqual(got.status_code, 400, got.text)

    def test_a_linux_data_home_that_takes_a_write_is_enough_without_a_home(self) -> None:
        with patch("common.games.launcher_ops.sys.platform", "linux"), \
                patch.dict(os.environ, {"HOME": "", "XDG_DATA_HOME": self.tmp.name}), \
                self._run():
            got = self.client.post("/launchers/l1/config/file")

        self.assertEqual(got.status_code, 200, got.text)

    def test_a_file_already_there_is_not_run_over(self) -> None:
        self.ini.write_text("[Player]\n")

        with self._run() as run_program:
            got = self.client.post("/launchers/l1/config/file")

        self.assertEqual(got.status_code, 400, got.text)
        run_program.assert_not_called()

    def test_a_launcher_with_no_program_is_refused(self) -> None:
        made = put_launcher(self.client, "l2",
                            json={"app": "vpx", "display_name": "Other", "settings": {}})
        self.assertEqual(made.status_code, 200, made.text)

        with self._run() as run_program:
            got = self.client.post("/launchers/l2/config/file")

        self.assertEqual(got.status_code, 400, got.text)
        run_program.assert_not_called()


class NeverRunTests(unittest.TestCase):
    """A Visual Pinball that has not run yet: no preferences folder, so no file."""

    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = pathlib.Path(self.tmp.name, "data")
        self.store = own_launchers(self, self.tmp.name)
        for patcher in (patch.object(launchers, "get_launcher_store", return_value=self.store),
                        patch("apps.vpx.config._machine_folders",
                              return_value=(self.data / "VPinballX", None))):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.store.save([], {})
        self.client = _client()
        put_launcher(self.client, "l1", json={"app": "vpx", "settings": {"bin_path": "/opt/vpx"}})
        self.file = self.data / "VPinballX" / "10.8" / "VPinballX.ini"

    def _put(self, **body):
        return self.client.put("/launchers/l1/config",
                               json={"scope": "launcher", "values": {"Player.BGSet": "1"},
                                     **body})

    def test_a_write_on_its_own_is_refused_and_makes_nothing(self) -> None:
        got = self._put()

        self.assertEqual(got.status_code, 400, got.text)
        self.assertFalse(self.file.exists())

    def test_a_write_that_asks_makes_the_newest_releases_file_holding_only_what_was_sent(
            self) -> None:
        got = self._put(create=True)

        self.assertEqual(got.status_code, 200, got.text)
        self.assertEqual(got.json()["written"], ["Player.BGSet"])
        self.assertEqual(self.file.read_text(), "[Player]\nBGSet = 1\n")

    def test_the_next_write_needs_no_asking(self) -> None:
        self._put(create=True)

        got = self.client.put("/launchers/l1/config", json={
            "scope": "launcher", "values": {"Player.ScreenInclination": "6"}})

        self.assertEqual(got.status_code, 200, got.text)
        self.assertIn("BGSet = 1", self.file.read_text())
        self.assertIn("ScreenInclination = 6", self.file.read_text())


class SetupMakesTheFileTests(unittest.TestCase):
    """Protects: (d) Setup on a launcher with no settings file leaves one holding what was
    sent, whether or not the program ran and wrote its own first."""

    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = pathlib.Path(self.tmp.name, "data")
        self.store = own_launchers(self, self.tmp.name)
        for patcher in (patch.object(launchers, "get_launcher_store", return_value=self.store),
                        patch("apps.vpx.config._machine_folders",
                              return_value=(self.data / "VPinballX", None))):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.store.save([], {})
        self.client = _client()
        self.file = self.data / "VPinballX" / "10.8" / "VPinballX.ini"
        self.program = pathlib.Path(self.tmp.name, "vpx")
        self.program.write_text("#!/bin/sh\n")
        self.program.chmod(0o755)
        put_launcher(self.client, "l1", json={"app": "vpx", "settings": {
            "bin_path": str(self.program)}})
        self.addCleanup(launcher_ops._wrote_nothing.clear)

    def _put(self, **body):
        return self.client.put("/launchers/l1/config",
                               json={"scope": "launcher", "values": {"Player.BGSet": "1"},
                                     **body})

    def _run(self, writes: bool) -> Any:
        def made(command: list[str], **_: Any) -> None:
            if writes:
                self.file.parent.mkdir(parents=True)
                self.file.write_text("; the program's own comment\n[Player]\nFXAA = 1\n")
        return patch("common.games.launcher_ops.subprocess.run", side_effect=made)

    def test_the_programs_own_file_is_the_one_written_into(self) -> None:
        with self._run(writes=True):
            got = self._put(create=True)

        self.assertEqual(got.status_code, 200, got.text)
        text = self.file.read_text()
        self.assertIn("; the program's own comment", text)
        self.assertIn("FXAA = 1", text)
        self.assertIn("BGSet = 1", text)

    def test_a_program_that_writes_nothing_leaves_the_bare_file(self) -> None:
        with self._run(writes=False):
            got = self._put(create=True)

        self.assertEqual(got.status_code, 200, got.text)
        self.assertEqual(self.file.read_text(), "[Player]\nBGSet = 1\n")

    def test_a_program_that_cannot_run_leaves_the_bare_file(self) -> None:
        with patch("common.games.launcher_ops.subprocess.run", side_effect=OSError("no")):
            got = self._put(create=True)

        self.assertEqual(got.status_code, 200, got.text)
        self.assertEqual(self.file.read_text(), "[Player]\nBGSet = 1\n")


class _TableCase(unittest.TestCase):
    """A launcher, and one table whose folder has a settings file of its own."""

    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = own_launchers(self, self.tmp.name)
        store_patch = patch.object(launchers, "get_launcher_store",
                                   return_value=self.store)
        store_patch.start()
        self.addCleanup(store_patch.stop)
        self.store.save([], {})
        self.client = _client()
        put_launcher(self.client, "l1", json={"app": "vpx", "settings": {"bin_path": "/opt/vpx"}})
        folder = os.path.join(self.tmp.name, "Attack from Mars")
        os.makedirs(folder)
        self.table = os.path.join(folder, "afm.vpx")
        pathlib.Path(self.table).touch()
        pathlib.Path(folder, "Attack from Mars.ini").write_text(
            "[Player]\nBallTrail = 1\nFXAA = 3\n", encoding="utf-8")
        self.beside = os.path.join(folder, "afm.ini")
        patcher = patch("common.games.launcher_ops._game_file", return_value=self.table)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _write(self, **body):
        return self.client.put("/launchers/l1/config",
                               json={"scope": "entry", "table": "t1", **body})


class GameFileTests(_TableCase):
    def setUp(self) -> None:
        super().setUp()
        self.game_file = pathlib.Path(os.path.dirname(self.table), "Attack from Mars.ini")

    def test_a_table_s_first_value_of_its_own_is_all_its_file_holds(self) -> None:
        self._write(values={"Backglass.BackglassWndX": "137"})

        self.assertEqual(pathlib.Path(self.beside).read_text(encoding="utf-8"),
                         "[Backglass]\nBackglassWndX = 137\n")

    def test_a_write_to_the_game_s_file_is_refused(self) -> None:
        before = self.game_file.read_text()

        got = self._write(scope="folder", values={"Player.FXAA": "0"})

        self.assertEqual(got.status_code, 400, got.text)
        self.assertEqual(self.game_file.read_text(), before)

    def test_and_where_the_game_has_none_none_is_made(self) -> None:
        self.game_file.unlink()

        got = self._write(scope="folder", values={"Player.FXAA": "0"})

        self.assertEqual(got.status_code, 400, got.text)
        self.assertFalse(self.game_file.exists())

    def test_the_game_s_file_is_still_read_where_it_reaches_a_table(self) -> None:
        got = self.client.get("/launchers/l1/config?table=t1&scope=entry")

        held = got.json()["values"]["Player.FXAA"]
        self.assertEqual((held["value"], held["scope"]), ("3", "folder"))

    def test_what_it_sets_that_a_table_with_its_own_file_does_not_read(self) -> None:
        app_ini = pathlib.Path(self.tmp.name, "VPinballX.ini")
        app_ini.write_text("[Player]\nFXAA = 1\n", encoding="utf-8")
        put_launcher(self.client, "l1", json={"app": "vpx", "settings": {
            "bin_path": "/opt/vpx", "ini_path": str(app_ini)}})
        pathlib.Path(self.beside).write_text("[Player]\nFXAA = 2\n", encoding="utf-8")

        at_table = self.client.get("/launchers/l1/config?table=t1&scope=entry").json()
        at_launcher = self.client.get("/launchers/l1/config?table=t1&scope=launcher").json()

        self.assertEqual(at_table["from_game"], {"Player.BallTrail": "1"})
        self.assertEqual(at_launcher["from_game"], {})

    def test_what_it_sets_for_a_table_with_no_file_of_its_own(self) -> None:
        app_ini = pathlib.Path(self.tmp.name, "VPinballX.ini")
        app_ini.write_text("[Player]\nFXAA = 1\n", encoding="utf-8")
        put_launcher(self.client, "l1", json={"app": "vpx", "settings": {
            "bin_path": "/opt/vpx", "ini_path": str(app_ini)}})

        got = self.client.get("/launchers/l1/config?table=t1&scope=entry").json()

        self.assertEqual(got["follows_game"], {"Player.BallTrail": "1", "Player.FXAA": "3"})
        self.assertEqual(got["from_game"], {})

    def test_and_nothing_to_follow_once_the_table_has_a_file(self) -> None:
        pathlib.Path(self.beside).write_text("[Player]\nFXAA = 2\n", encoding="utf-8")

        got = self.client.get("/launchers/l1/config?table=t1&scope=entry").json()

        self.assertEqual(got["follows_game"], {})


class ClearingTests(_TableCase):
    def test_a_table_s_own_value_says_what_clearing_it_leaves(self) -> None:
        """The game's value, where clearing takes the table file's last key and the file
        with it."""
        app_ini = pathlib.Path(self.tmp.name, "VPinballX.ini")
        app_ini.write_text("[Player]\nFXAA = 1\n", encoding="utf-8")
        put_launcher(self.client, "l1", json={"app": "vpx", "settings": {
            "bin_path": "/opt/vpx", "ini_path": str(app_ini)}})
        self._write(values={"Player.FXAA": "2"})

        got = self.client.get("/launchers/l1/config?table=t1&scope=entry")

        held = got.json()["values"]["Player.FXAA"]
        self.assertEqual((held["value"], held["fallback"], held["fallback_scope"]),
                         ("2", "3", "folder"))


class SeveralTablesTests(_TableCase):
    """`tables` in place of `table` writes the same values into each table's own file."""

    def setUp(self) -> None:
        super().setUp()
        jobs.reset_for_tests()
        self.addCleanup(jobs.reset_for_tests)
        self.files = {}
        for name in ("one", "two", "three"):
            folder = os.path.join(self.tmp.name, name)
            os.makedirs(folder)
            self.files[name] = os.path.join(folder, f"{name}.vpx")
            pathlib.Path(self.files[name]).touch()
        found = patch("common.games.launcher_ops._game_file",
                      side_effect=lambda table: self.files.get(table, self.table))
        found.start()
        self.addCleanup(found.stop)

    def _write_many(self, tables, **body):
        return self.client.put("/launchers/l1/config",
                               json={"scope": "entry", "tables": tables, **body})

    def _finished(self, got) -> jobs.Job:
        job = jobs.get(got.json()["id"])
        assert job is not None
        deadline = time.monotonic() + 5
        while job.state == jobs.RUNNING and time.monotonic() < deadline:
            time.sleep(0.01)
        return job

    def test_more_than_one_is_a_job_that_writes_each(self) -> None:
        got = self._write_many(["one", "two"], values={"Player.FXAA": "2"})

        self.assertEqual(got.status_code, 202, got.text)
        self.assertEqual(got.headers["Location"], f"/api/v1/jobs/{got.json()['id']}")
        job = self._finished(got)
        self.assertEqual(job.kind, jobs.KIND_TABLE_SETTINGS)
        self.assertEqual(job.result, {"tables": 2, "written": ["one", "two"], "failed": []})
        for name in ("one", "two"):
            beside = pathlib.Path(self.files[name]).with_suffix(".ini")
            self.assertEqual(beside.read_text(), "[Player]\nFXAA = 2\n")
        self.assertFalse(pathlib.Path(self.files["three"]).with_suffix(".ini").exists())

    def test_a_table_that_cannot_be_written_is_named_and_the_rest_go_on(self) -> None:
        with patch("common.games.launcher_ops._writable_before_writing",
                   side_effect=[ValueError("locked"), None]):
            got = self._write_many(["one", "two"], values={"Player.FXAA": "0"})
            job = self._finished(got)

        self.assertEqual(job.state, jobs.DONE)
        result: Any = job.result
        self.assertEqual(result["written"], ["two"])
        self.assertEqual([one["table"] for one in result["failed"]], ["one"])
        self.assertTrue(result["failed"][0]["reason"])

    def test_the_job_is_exclusive(self) -> None:
        release = threading.Event()
        blocker = jobs.submit(jobs.KIND_TABLE_SETTINGS, lambda job: release.wait(5))
        self.addCleanup(release.set)

        got = self._write_many(["one", "two"], values={"Player.FXAA": "2"})

        self.assertEqual(got.status_code, 409, got.text)
        release.set()
        self.assertIsNotNone(jobs.get(blocker.id))

    def test_a_list_is_refused_at_the_launcher_scope(self) -> None:
        got = self._write_many(["one", "two"], scope="launcher", values={"Player.FXAA": "0"})

        self.assertEqual(got.status_code, 400, got.text)
        self.assertEqual(jobs.active(jobs.KIND_TABLE_SETTINGS), [])

    def test_one_in_the_list_is_written_in_the_answer(self) -> None:
        got = self._write_many(["one"], values={"Player.FXAA": "2"})

        self.assertEqual(got.status_code, 200, got.text)
        beside = pathlib.Path(self.files["one"]).with_suffix(".ini")
        self.assertEqual(beside.read_text(), "[Player]\nFXAA = 2\n")

    def test_a_table_that_is_gone_is_named_and_the_rest_are_written(self) -> None:
        missing = service_errors.NotFoundError("no such table")
        stand_in = {"two": self.files["two"]}

        def find(table: str) -> str:
            if table and table not in stand_in:
                raise missing
            return stand_in.get(table, "")

        with patch("common.games.launcher_ops._game_file", side_effect=find):
            job = self._finished(self._write_many(["gone", "two"],
                                                  values={"Player.FXAA": "0"}))

        result: Any = job.result
        self.assertEqual(result["written"], ["two"])
        self.assertEqual([one["table"] for one in result["failed"]], ["gone"])

    def test_tables_that_are_not_a_list_are_refused(self) -> None:
        got = self._write_many("one", values={"Player.FXAA": "0"})

        self.assertEqual(got.status_code, 400, got.text)
        self.assertEqual(jobs.active(jobs.KIND_TABLE_SETTINGS), [])


class BlankWordsTests(_TableCase):
    def test_a_window_size_left_blank_reads_auto_from_the_screen(self) -> None:
        app_ini = pathlib.Path(self.tmp.name, "VPinballX.ini")
        app_ini.write_text("[Player]\n; Width: Width of the window [Default: 16384]\n"
                           "PlayfieldWidth =\nFXAA = 1\n", encoding="utf-8")
        put_launcher(self.client, "l1", json={"app": "vpx", "settings": {
            "bin_path": "/opt/vpx", "ini_path": str(app_ini)}})

        got = self.client.get("/launchers/l1/config")

        offered = {f["key"]: f for g in got.json()["groups"] for f in g["settings"]}
        size = offered["Player.PlayfieldWidth"]
        self.assertEqual((size["default"], size["blank"], size["left_empty"]),
                         ("", "Auto", "From the screen"))
        self.assertEqual((offered["Player.FXAA"]["blank"], offered["Player.FXAA"]["left_empty"]),
                         ("", ""))


class NamedValuesTests(_TableCase):
    def test_frame_limit_s_minus_one_and_zero_are_named_in_the_app_s_words(self) -> None:
        app_ini = pathlib.Path(self.tmp.name, "VPinballX.ini")
        app_ini.write_text("[Player]\n; Limit Framerate:  [Default: -1.0 in -1.0 .. 1000.0]:\n"
                           "MaxFramerate =\nFXAA = 1\n", encoding="utf-8")
        put_launcher(self.client, "l1", json={"app": "vpx", "settings": {
            "bin_path": "/opt/vpx", "ini_path": str(app_ini)}})

        got = self.client.get("/launchers/l1/config")

        offered = {f["key"]: f for g in got.json()["groups"] for f in g["settings"]}
        self.assertEqual(offered["Player.MaxFramerate"]["named"],
                         [["-1", "Match the Display"], ["0", "No Limit"]])
        self.assertEqual(offered["Player.FXAA"]["named"], [])


class ReportedValuesTests(_TableCase):
    def test_a_window_s_display_carries_the_names_vpx_used(self) -> None:
        app_ini = pathlib.Path(self.tmp.name, "VPinballX.ini")
        app_ini.write_text("[Player]\n; Display: Where [Default: '']\nPlayfieldDisplay =\n"
                           "FXAA = 1\n", encoding="utf-8")
        log = pathlib.Path(self.tmp.name, "vpinball.log")
        log.write_text('INFO [VPX::Window::Window@86] No display configured. '
                       'Using display "Built-in Display".\n', encoding="utf-8")
        put_launcher(self.client, "l1", json={"app": "vpx", "settings": {
            "bin_path": "/opt/vpx", "ini_path": str(app_ini)}})

        with patch("apps.vpx.config.own_log", return_value=log):
            got = self.client.get("/launchers/l1/config")

        offered = {f["key"]: f for g in got.json()["groups"] for f in g["settings"]}
        self.assertEqual(offered["Player.PlayfieldDisplay"]["reported"], ["Built-in Display"])
        self.assertEqual(offered["Player.FXAA"]["reported"], [])


class PointOfViewTests(_TableCase):
    def setUp(self) -> None:
        super().setUp()
        app_ini = pathlib.Path(self.tmp.name, "VPinballX.ini")
        app_ini.write_text(
            "[Player]\n; View Mode: Which setup [Default: 0, 0='Desktop', 1='Cabinet']\n"
            "BGSet = 1\n[TableOverride]\n"
            "; View mode: How [Default: 2, 0='Legacy', 1='Camera', 2='Window']\n"
            "ViewCabMode =\n; Field of view: How wide [Default: 55]\nViewCabFOV =\n",
            encoding="utf-8")
        put_launcher(self.client, "l1", json={"app": "vpx", "settings": {
            "bin_path": "/opt/vpx", "ini_path": str(app_ini)}})
        pathlib.Path(self.beside).write_text("[TableOverride]\nViewCabFOV = 50\n"
                                            "[TableOption]\nBall_Speed = 2\n", encoding="utf-8")

    def _groups(self) -> dict:
        got = self.client.get("/launchers/l1/config?table=t1&scope=entry")
        return {g["key"]: g for g in got.json()["groups"]}

    def test_a_table_s_view_mode_is_a_row_of_its_own(self) -> None:
        view = self._groups()["point_of_view"]

        self.assertEqual(view["rows"], ["TableOverride.ViewCabMode"])
        mode = next(f for f in view["settings"] if f["key"] == "TableOverride.ViewCabMode")
        self.assertEqual(mode["blank"], "The table's own")
        self.assertEqual(sorted(mode["choice_help"]), ["0", "1", "2"])

    def test_its_table_options_are_listed_as_its_file_holds_them(self) -> None:
        options = self._groups()["table_options"]

        self.assertIs(options["read_only"], True)
        self.assertEqual([f["label"] for f in options["settings"]], ["Ball Speed"])

    def test_all_tables_have_no_table_options(self) -> None:
        got = self.client.get("/launchers/l1/config")

        self.assertNotIn("table_options", [g["key"] for g in got.json()["groups"]])


class TwoVersionRowTests(_TableCase):
    KEY = "Plugin.B2SLegacy.B2SDualMode"

    def setUp(self) -> None:
        super().setUp()
        app_ini = pathlib.Path(self.tmp.name, "VPinballX.ini")
        app_ini.write_text("[Plugin.B2SLegacy]\nEnable = 1\nB2SDualMode =\n", encoding="utf-8")
        put_launcher(self.client, "l1", json={"app": "vpx", "settings": {
            "bin_path": "/opt/vpx", "ini_path": str(app_ini)}})

    def _config(self) -> list[dict]:
        got = self.client.get("/launchers/l1/config")
        return [g for g in got.json()["groups"]
                if any(h["key"] == "B2SLegacy" for h in g["curated"])]

    def test_the_heading_names_the_row_a_file_of_two_versions_adds(self) -> None:
        (group,) = self._config()
        heading = next(h for h in group["curated"] if h["key"] == "B2SLegacy")

        self.assertEqual(heading["versions"], [self.KEY])
        self.assertNotIn(self.KEY, heading["keys"])

    def test_the_row_offers_the_two_versions_by_name(self) -> None:
        (group,) = self._config()
        row = next(f for f in group["settings"] if f["key"] == self.KEY)

        self.assertEqual((row["type"], row["choices"]),
                         ("choice", [["1", "Authentic"], ["2", "Fantasy"]]))


class BackglassPluginAtATableTests(_TableCase):
    VALUES = {"Plugin.B2S.ShowGrill": "1", "Plugin.B2S.BackglassDMDX": "120",
              "Plugin.B2S.BackglassDMDY": "40", "Plugin.B2S.BackglassDMDW": "512",
              "Plugin.B2S.BackglassDMDH": "128", "Plugin.B2S.BackglassDMDAutoPos": "1",
              "Plugin.B2S.ScoreViewDMDAutoPos": "1", "Plugin.B2S.ScoreViewDMDX": "60"}

    def setUp(self) -> None:
        super().setUp()
        app_ini = pathlib.Path(self.tmp.name, "VPinballX.ini")
        app_ini.write_text("[Plugin.B2S]\nEnable = 1\n"
                           + "".join(f"{key.rsplit('.', 1)[-1]} = \n" for key in self.VALUES),
                           encoding="utf-8")
        put_launcher(self.client, "l1", json={"app": "vpx", "settings": {
            "bin_path": "/opt/vpx", "ini_path": str(app_ini)}})

    def test_each_is_written_to_the_table_s_file_and_read_back_from_it(self) -> None:
        for key, value in self.VALUES.items():
            with self.subTest(key=key):
                written = self._write(values={key: value})
                self.assertEqual(written.status_code, 200, written.text)

                got = self.client.get("/launchers/l1/config?table=t1&scope=entry")
                held = got.json()["values"][key]
                self.assertEqual((held["value"], held["set_here"]), (value, True))

                file = configparser.ConfigParser(interpolation=None)
                file.optionxform = str  # type: ignore[assignment,method-assign]
                file.read(self.beside)
                self.assertEqual(file["Plugin.B2S"][key.rsplit(".", 1)[-1]], value)

    def test_its_heading_is_about_the_backglass_and_pairs_each_dmd_box(self) -> None:
        got = self.client.get("/launchers/l1/config?table=t1&scope=entry")
        plugins = {g["key"]: g for g in got.json()["groups"]}["plugins"]
        b2s, = plugins["curated"]
        backglass, scoreview = "Plugin.B2S.BackglassDMD", "Plugin.B2S.ScoreViewDMD"

        self.assertEqual(b2s["kinds"], ["backglass"])
        self.assertEqual([(p["label"], p["joiner"], p["keys"]) for p in b2s["pairs"]], [
            ("Backglass DMD Position", ",", [f"{backglass}X", f"{backglass}Y"]),
            ("Backglass DMD Size", "×", [f"{backglass}W", f"{backglass}H"]),
            ("Score View DMD Position", ",", [f"{scoreview}X", f"{scoreview}Y"]),
            ("Score View DMD Size", "×", [f"{scoreview}W", f"{scoreview}H"])])
        self.assertTrue(set(self.VALUES) <= set(b2s["keys"]))

    def test_the_overlay_s_rows_are_switched_by_the_overlay(self) -> None:
        got = self.client.get("/launchers/l1/config?table=t1&scope=entry")
        b2s, = {g["key"]: g for g in got.json()["groups"]}["plugins"]["curated"]

        self.assertEqual(b2s["switched"], [one for art in ("Backglass", "ScoreView") for one in (
            {"enabled_by": f"Plugin.B2S.{art}DMDOverlay",
             "keys": [f"Plugin.B2S.{art}DMD{part}" for part in
                      ("AutoPos", "X", "Y", "W", "H")], "on": True},
            {"enabled_by": f"Plugin.B2S.{art}DMDAutoPos",
             "keys": [f"Plugin.B2S.{art}DMD{part}" for part in "XYWH"], "on": False})])


class _Wire:
    """The Manager's HTTP session, answered by the API in this process."""

    def __init__(self, client: TestClient, base: str) -> None:
        self._client, self._base = client, base

    def _send(self, method: str, url: str, **kwargs: Any) -> Any:
        kwargs.pop("timeout", None)
        response = self._client.request(method, url.removeprefix(self._base), **kwargs)
        response.ok = response.is_success
        return response

    def get(self, url: str, **kwargs: Any) -> Any:
        return self._send("GET", url, **kwargs)

    def put(self, url: str, **kwargs: Any) -> Any:
        return self._send("PUT", url, **kwargs)


async def _off_the_loop(callback: Any, *args: Any, **kwargs: Any) -> Any:
    return await asyncio.to_thread(callback, *args, **kwargs)


class _BackglassPanel(_TableCase, unittest.IsolatedAsyncioTestCase):
    """A backglass file's Settings, over a launcher whose program is there, read and
    written through the Manager's own client."""

    BACKGLASS = {"kind": "backglass", "binding": "table", "table": "t1", "present": True}
    TABLES = [{"id": "t1", "launcher": "l1", "launcher_app_configurable": True,
               "filename": "afm.vpx"}]
    X = "Plugin.B2S.BackglassDMDX"

    def setUp(self) -> None:
        super().setUp()
        program = pathlib.Path(self.tmp.name, "vpx", "VPinballX_GL")
        program.parent.mkdir()
        program.touch(mode=0o755)
        app_ini = pathlib.Path(self.tmp.name, "VPinballX.ini")
        app_ini.write_text(
            "[Plugin.B2S]\nEnable = 1\nBackglassDMDOverlay = 1\nScoreViewDMDOverlay = 1\n"
            + "".join(f"{key.rsplit('.', 1)[-1]} = \n"
                      for key in BackglassPluginAtATableTests.VALUES), encoding="utf-8")
        put_launcher(self.client, "l1", json={"app": "vpx", "settings": {
            "bin_path": str(program), "ini_path": str(app_ini)}})
        api = ApiClient("http://testserver")
        api._session = _Wire(self.client, "http://testserver/api/v1")  # type: ignore[assignment]
        self.library = data.Library(api)
        self.enterContext(patch.object(run, "io_bound", new=_off_the_loop))
        self.enterContext(patch.object(ui, "context"))
        self.enterContext(patch_workbench("_keeping_place", new=AsyncMock()))

    async def _panel(self) -> tuple[Any, Any]:
        context = await wb_file._file_settings(self.library, self.BACKGLASS,
                                                 [self.BACKGLASS], self.TABLES, {})
        assert context is not None
        context["rebuild"] = AsyncMock()
        with patch_workbench("ui"), patch.object(panel, "facts"), \
                patch.object(settings_page, "control_for") as control_for, \
                patch_workbench("_marked") as marked:
            await wb_file._file_settings_entries({"file_settings": context})
        return control_for, marked


class BackglassPanelTests(_BackglassPanel):
    """The backglass file's Settings and the table's Settings are one value each: what
    either writes, the other shows as the table's."""

    async def _at_the_table(self) -> dict[str, Any]:
        return await asyncio.to_thread(self.library.launcher_config, "l1", "t1", "entry")

    async def test_each_is_written_from_the_panel_and_read_back_by_the_table(self) -> None:
        control_for, _ = await self._panel()
        saves = {call.args[0]["key"]: call.args[2] for call in control_for.call_args_list}

        for key, value in BackglassPluginAtATableTests.VALUES.items():
            with self.subTest(key=key):
                self.assertTrue(await saves[key](value))

                held = (await self._at_the_table())["values"][key]
                self.assertEqual((held["value"], held["set_here"]), (value, True))
                file = configparser.ConfigParser(interpolation=None)
                file.optionxform = str  # type: ignore[assignment,method-assign]
                file.read(self.beside)
                self.assertEqual(file["Plugin.B2S"][key.rsplit(".", 1)[-1]], value)

    async def test_a_value_set_here_is_listed_in_the_table_s_settings(self) -> None:
        control_for, _ = await self._panel()
        save = next(call.args[2] for call in control_for.call_args_list
                    if call.args[0]["key"] == self.X)

        await save(120)

        found = await self._at_the_table()
        listed = app_settings.differences(data.config_groups(found), found["values"])
        self.assertIn(self.X, [field.key for _, fields in listed for field in fields])

    async def test_one_the_table_s_settings_wrote_is_the_table_s_here(self) -> None:
        await asyncio.to_thread(self.library.write_launcher_config, "l1", {self.X: "120"},
                                table="t1", scope="entry")

        _, marked = await self._panel()

        held = next(held for call in marked.call_args_list
                    for _, held, field in call.args[0] if field.key == self.X)
        self.assertEqual((held["value"], held["set_here"]), ("120", True))

    async def test_clear_takes_it_back_to_all_tables(self) -> None:
        await asyncio.to_thread(self.library.write_launcher_config, "l1", {self.X: "120"},
                                table="t1", scope="entry")
        _, marked = await self._panel()
        clear = next(call.kwargs["clear"] for call in marked.call_args_list
                     if self.X in [field.key for _, _, field in call.args[0]])

        await clear()

        self.assertFalse((await self._at_the_table())["values"][self.X]["set_here"])
        self.assertFalse(os.path.exists(self.beside))


class SharedBackglassPanelTests(_BackglassPanel):
    """The game's backglass, used by two tables: one row a setting, written to both."""

    BACKGLASS = {"kind": "backglass", "binding": "game", "table": "", "present": True}
    TABLES = [*_BackglassPanel.TABLES,
              {"id": "t2", "launcher": "l1", "launcher_app_configurable": True,
               "filename": "afm2.vpx"}]
    Y = "Plugin.B2S.BackglassDMDY"

    def setUp(self) -> None:
        super().setUp()
        second = pathlib.Path(self.table).with_name("afm2.vpx")
        second.touch()
        self.own = {"t1": pathlib.Path(self.beside), "t2": second.with_suffix(".ini")}
        self.enterContext(patch("common.games.launcher_ops._game_file",
                                side_effect={"t1": self.table, "t2": str(second)}.get))
        self.notify = self.enterContext(patch.object(ui, "notify"))

    async def _set_at(self, table: str, **values: str) -> None:
        await asyncio.to_thread(self.library.write_launcher_config, "l1",
                                {getattr(self, key): value for key, value in values.items()},
                                table=table, scope="entry")

    def _in_file(self, table: str) -> str:
        file = configparser.ConfigParser(interpolation=None)
        file.optionxform = str  # type: ignore[assignment,method-assign]
        file.read(self.own[table])
        return file["Plugin.B2S"].get("BackglassDMDX", "") if file.has_section(
            "Plugin.B2S") else ""

    @staticmethod
    def _row(control_for: Any, marked: Any, key: str) -> tuple[Any, Any]:
        control = next(call for call in control_for.call_args_list
                       if call.args[0]["key"] == key)
        parts = next(call.args[0] for call in marked.call_args_list
                     if key in [field.key for _, _, field in call.args[0]])
        return control, parts

    async def test_where_they_differ_the_row_reads_varies(self) -> None:
        await self._set_at("t1", X="120", Y="40")
        await self._set_at("t2", X="60", Y="40")

        control_for, marked = await self._panel()

        control, parts = self._row(control_for, marked, self.X)
        self.assertIs(control.kwargs["varies"], True)
        self.assertIs(self._row(control_for, marked, self.Y)[0].kwargs["varies"], False)
        with patch.object(panel, "state") as state:
            all_settings._varies(parts, self.TABLES, "VPX", ",")
        self.assertEqual(state.call_args.args[0], "Varies")
        self.assertEqual(state.call_args.kwargs["hint"].splitlines(),
                         ["afm: 120,40", "afm2: 60,40"])

    async def test_where_they_agree_it_holds_their_value(self) -> None:
        await self._set_at("t1", X="120")
        await self._set_at("t2", X="120")

        control_for, marked = await self._panel()

        control, parts = self._row(control_for, marked, self.X)
        self.assertEqual((control.args[1], control.kwargs["varies"]), (120, False))
        self.assertIsNone(all_settings._varies(parts, self.TABLES, "VPX", ","))

    async def test_a_value_set_here_is_written_to_both_tables(self) -> None:
        await self._set_at("t1", X="120")
        await self._set_at("t2", X="60")
        control_for, marked = await self._panel()

        self.assertTrue(await self._row(control_for, marked, self.X)[0].args[2](200))

        self.assertEqual([self._in_file(one) for one in ("t1", "t2")], ["200", "200"])

    async def test_clear_clears_it_at_both(self) -> None:
        await self._set_at("t1", X="120")
        await self._set_at("t2", X="60")
        _, marked = await self._panel()
        clear = next(call.kwargs["clear"] for call in marked.call_args_list
                     if self.X in [field.key for _, _, field in call.args[0]])

        await clear()

        self.assertEqual([self._in_file(one) for one in ("t1", "t2")], ["", ""])

    async def test_clear_gives_a_table_that_did_not_set_it_no_file(self) -> None:
        await self._set_at("t1", X="120")
        _, marked = await self._panel()
        clear = next(call.kwargs["clear"] for call in marked.call_args_list
                     if self.X in [field.key for _, _, field in call.args[0]])

        await clear()

        self.assertEqual(self._in_file("t1"), "")
        self.assertFalse(self.own["t2"].exists())

    async def test_the_tables_that_stop_reading_the_game_s_file_are_named(self) -> None:
        control_for, marked = await self._panel()

        await self._row(control_for, marked, self.X)[0].args[2](200)

        self.assertEqual([self._in_file(one) for one in ("t1", "t2")], ["200", "200"])
        said = [call for call in self.notify.call_args_list
                if call.kwargs.get("type") == "warning"]
        self.assertEqual([call.args[0] for call in said],
                         ["2 tables no longer read this game's own settings:\nafm\nafm2"])


class SharedWithGameTests(_TableCase):
    def test_the_table_named_after_its_folder_says_its_file_is_the_game_s(self) -> None:
        named = os.path.join(os.path.dirname(self.table), "Attack from Mars.vpx")
        pathlib.Path(named).touch()

        with patch("common.games.launcher_ops._game_file", return_value=named):
            got = self.client.get("/launchers/l1/config?table=t1&scope=entry")

        self.assertIs(got.json()["shared_with_game"], True)

    def test_another_table_s_file_is_its_own(self) -> None:
        got = self.client.get("/launchers/l1/config?table=t1&scope=entry")

        self.assertIs(got.json()["shared_with_game"], False)


class AllTablesOnlyTests(_TableCase):
    def setUp(self) -> None:
        super().setUp()
        app_ini = pathlib.Path(self.tmp.name, "VPinballX.ini")
        app_ini.write_text("[Input]\nNudgeSensorCount = 2\n\n"
                           "[Player]\nShowFPS = 0\nFXAA = 1\n", encoding="utf-8")
        put_launcher(self.client, "l1", json={"app": "vpx", "settings": {
            "bin_path": "/opt/vpx", "ini_path": str(app_ini)}})

    def _offered(self, scope: str) -> dict:
        got = self.client.get(f"/launchers/l1/config?table=t1&scope={scope}")
        self.assertEqual(got.status_code, 200, got.text)
        return {f["key"]: f for g in got.json()["groups"] for f in g["settings"]}

    def test_the_launcher_offers_it(self) -> None:
        offered = self._offered("launcher")

        self.assertEqual(offered["Player.ShowFPS"]["scopes"], ["launcher"])
        self.assertEqual(offered["Player.FXAA"]["scopes"], ["launcher", "folder", "entry"])

    def test_a_table_and_a_folder_do_not(self) -> None:
        for scope in ("entry", "folder"):
            with self.subTest(scope=scope):
                offered = self._offered(scope)
                self.assertNotIn("Player.ShowFPS", offered)
                self.assertNotIn("Input.NudgeSensorCount", offered)
                self.assertIn("Player.FXAA", offered)

    def test_one_a_table_s_file_already_holds_is_still_listed(self) -> None:
        pathlib.Path(self.beside).write_text("[Player]\nShowFPS = 1\n", encoding="utf-8")

        offered = self._offered("entry")

        self.assertEqual(offered["Player.ShowFPS"]["scopes"], ["launcher"])

    def test_writing_one_at_a_table_is_refused(self) -> None:
        got = self._write(values={"Player.ShowFPS": "1"})

        self.assertEqual(got.status_code, 400, got.text)
        self.assertFalse(os.path.exists(self.beside))

    def test_clearing_one_at_a_table_is_not(self) -> None:
        pathlib.Path(self.beside).write_text("[Player]\nShowFPS = 1\nFXAA = 3\n", encoding="utf-8")

        got = self._write(values={"Player.ShowFPS": ""})

        self.assertEqual(got.status_code, 200, got.text)
        self.assertNotIn("ShowFPS", pathlib.Path(self.beside).read_text(encoding="utf-8"))


class TableOnlyTests(_TableCase):
    def setUp(self) -> None:
        super().setUp()
        self.app_ini = pathlib.Path(self.tmp.name, "VPinballX.ini")
        self.app_ini.write_text("[Player]\nFXAA = 1\n\n"
                                "[TableOverride]\nDifficulty =\nViewCabFOV =\n")
        put_launcher(self.client, "l1", json={"app": "vpx", "settings": {
            "bin_path": "/opt/vpx", "ini_path": str(self.app_ini)}})

    def _groups(self, scope: str) -> dict:
        got = self.client.get(f"/launchers/l1/config?table=t1&scope={scope}")
        self.assertEqual(got.status_code, 200, got.text)
        return {g["key"]: g for g in got.json()["groups"]}

    def test_a_table_offers_it_and_the_launcher_does_not(self) -> None:
        at_launcher = {f["key"] for g in self._groups("launcher").values()
                       for f in g["settings"]}
        at_table = {f["key"] for g in self._groups("entry").values() for f in g["settings"]}

        self.assertNotIn("TableOverride.Difficulty", at_launcher)
        self.assertIn("TableOverride.Difficulty", at_table)

    def test_writing_one_at_the_launcher_is_refused_in_its_own_words(self) -> None:
        got = self.client.put("/launchers/l1/config", json={
            "scope": "launcher", "values": {"TableOverride.Difficulty": "2"}})

        self.assertEqual(got.status_code, 400, got.text)
        self.assertIn("one table at a time", got.text)
        self.assertNotIn("Difficulty = 2", self.app_ini.read_text())

    def test_the_point_of_view_is_one_summarized_group_at_a_table(self) -> None:
        groups = self._groups("entry")

        self.assertTrue(groups["point_of_view"]["summarized"])
        self.assertEqual([f["key"] for f in groups["point_of_view"]["settings"]],
                         ["TableOverride.ViewCabFOV"])
        self.assertNotIn("point_of_view", self._groups("launcher"))


class CuratedTests(_TableCase):
    def setUp(self) -> None:
        super().setUp()
        app_ini = pathlib.Path(self.tmp.name, "VPinballX.ini")
        app_ini.write_text("[Player]\nPlaySound = 1\nSound3D = 0\nShowFPS = 0\n\n"
                           "[Plugin.PinMAME]\nEnable = 1\nPinMAMEPath =\n", encoding="utf-8")
        put_launcher(self.client, "l1", json={"app": "vpx", "settings": {
            "bin_path": "/opt/vpx", "ini_path": str(app_ini)}})

    def _groups(self, scope: str) -> dict:
        got = self.client.get(f"/launchers/l1/config?table=t1&scope={scope}")
        self.assertEqual(got.status_code, 200, got.text)
        return {g["key"]: g for g in got.json()["groups"]}

    def test_a_heading_carries_its_words_and_rows(self) -> None:
        sound = self._groups("launcher")["sound"]

        self.assertEqual(sound["curated"], [{
            "key": "playfield", "label": "Playfield",
            "note": "Mechanical sounds - flippers, solenoids, the ball", "description": "",
            "keys": ["Player.PlaySound", "Player.Sound3D"], "enabled_by": "",
            "rivals": [], "pairs": [], "switched": [], "kinds": [], "versions": []}])
        self.assertFalse(sound["summarized"])

    def test_a_heading_carries_its_pairs_with_their_words(self) -> None:
        pathlib.Path(self.tmp.name, "VPinballX.ini").write_text(
            "[Player]\nPlayfieldWndX = 0\nPlayfieldWndY = 0\nPlayfieldWidth = 0\n",
            encoding="utf-8")

        playfield, = self._groups("launcher")["displays"]["curated"]

        self.assertEqual(playfield["pairs"], [{
            "key": "position", "label": "Position", "note": "On the display it opens on",
            "joiner": ",", "keys": ["Player.PlayfieldWndX", "Player.PlayfieldWndY"]}])

    def test_a_plugin_heading_names_the_rival_switches_the_file_has(self) -> None:
        app_ini = pathlib.Path(self.tmp.name, "VPinballX.ini")
        app_ini.write_text("[Plugin.B2S]\nEnable = 1\n\n[Plugin.B2SLegacy]\nEnable = 0\n",
                           encoding="utf-8")
        both = {h["key"]: h["rivals"] for h in self._groups("launcher")["plugins"]["curated"]}
        app_ini.write_text("[Plugin.B2S]\nEnable = 1\n", encoding="utf-8")
        alone = {h["key"]: h["rivals"] for h in self._groups("launcher")["plugins"]["curated"]}

        self.assertEqual(both, {"B2S": ["Plugin.B2SLegacy.Enable"],
                                "B2SLegacy": ["Plugin.B2S.Enable"]})
        self.assertEqual(alone, {"B2S": []})

    def test_a_rival_the_program_does_not_have_is_not_named(self) -> None:
        bundle = pathlib.Path(self.tmp.name, "VPinballX_BGFX.app", "Contents")
        for one in ("B2S", "Nixie"):
            (bundle / "PlugIns" / one.lower()).mkdir(parents=True)
            (bundle / "PlugIns" / one.lower() / "plugin.cfg").write_text(
                f'[configuration]\nid = "{one}"\nname = "{one}"\n')
        pathlib.Path(self.tmp.name, "VPinballX.ini").write_text(
            "[Plugin.B2S]\nEnable = 1\n\n[Plugin.B2SLegacy]\nEnable = 0\n\n"
            "[Plugin.Nixie]\nEnable = 1\n", encoding="utf-8")
        put_launcher(self.client, "l1", json={"app": "vpx", "settings": {
            "bin_path": str(bundle / "MacOS" / "VPinballX_BGFX"),
            "ini_path": str(pathlib.Path(self.tmp.name, "VPinballX.ini"))}})

        rivals = {h["key"]: h["rivals"]
                  for h in self._groups("launcher")["plugins"]["curated"]}

        self.assertEqual(rivals["B2S"], [])
        self.assertNotIn("B2SLegacy", rivals)

    def test_a_plugin_heading_is_switched_by_its_enable(self) -> None:
        plugins = self._groups("launcher")["plugins"]

        self.assertEqual(plugins["curated"][0]["keys"],
                         ["Plugin.PinMAME.Enable", "Plugin.PinMAME.Sound",
                          "Plugin.PinMAME.PinMAMEPath"])
        self.assertEqual(plugins["curated"][0]["enabled_by"], "Plugin.PinMAME.Enable")

    def test_a_plugin_heading_has_the_program_s_words_before_the_catalog_s(self) -> None:
        program = pathlib.Path(self.tmp.name, "vpx", "VPinballX_GL")
        manifest = program.parent / "plugins" / "pinmame" / "plugin.cfg"
        manifest.parent.mkdir(parents=True)
        manifest.write_text('[configuration]\nid = "PinMAME"\nname = "Pinball MAME"\n'
                            'description = "Plays ROMs"\n')
        put_launcher(self.client, "l1", json={"app": "vpx", "settings": {
            "bin_path": str(program),
            "ini_path": str(pathlib.Path(self.tmp.name, "VPinballX.ini"))}})

        heading = self._groups("launcher")["plugins"]["curated"][0]

        self.assertEqual((heading["label"], heading["description"]),
                         ("Pinball MAME", "Plays ROMs"))
        self.assertEqual(heading["note"], "Runs the original game's ROM")

    def test_a_heading_holds_only_rows_the_scope_offers(self) -> None:
        plugins = self._groups("entry")["plugins"]

        self.assertEqual(plugins["curated"][0]["keys"],
                         ["Plugin.PinMAME.Enable", "Plugin.PinMAME.Sound"])

    def test_a_setting_carries_a_help_line_and_whether_a_table_sets_it(self) -> None:
        settings = {f["key"]: f for g in self._groups("launcher").values()
                    for f in g["settings"]}

        self.assertEqual(settings["Player.Sound3D"]["help"],
                         "How playfield sound is spread across speakers")
        self.assertTrue(settings["Player.Sound3D"]["per_table"])
        self.assertFalse(settings["Player.ShowFPS"]["per_table"])
        self.assertEqual(settings["Plugin.PinMAME.PinMAMEPath"]["label"], "PinMAME Path")


class SwitchingOffTests(unittest.TestCase):
    """Three tables, all played by the first of two VPX launchers."""

    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = own_launchers(self, self.tmp.name)
        store_patch = patch.object(launchers, "get_launcher_store",
                                   return_value=self.store)
        store_patch.start()
        self.addCleanup(store_patch.stop)
        self.store.save([], {})
        library = [SimpleNamespace(meta_config={"tables": {
            one: {"id": one, "filename": f"{one}.vpx"} for one in ("t1", "t2", "t3")}})]
        games_patch = patch("common.games.game_repository.all_games",
                            return_value=library)
        games_patch.start()
        self.addCleanup(games_patch.stop)
        self.client = _client()
        self._put("wide", display_name="VPX Wide", bin_path="/opt/wide")
        self._put("plain", display_name="VPX Plain", bin_path="/opt/plain")

    def _put(self, launcher_id: str, *, app: str = "vpx", enabled: bool = True,
             display_name: str = "", bin_path: str = ""):
        return put_launcher(self.client, launcher_id, json={
            "app": app, "display_name": display_name, "enabled": enabled,
            "settings": {"bin_path": bin_path}})

    def _fallback(self, launcher_id: str) -> dict:
        got = self.client.get(f"/launchers/{launcher_id}/fallback")
        self.assertEqual(got.status_code, 200, got.text)
        return got.json()

    def _enabled(self, launcher_id: str) -> bool:
        return next(one["enabled"] for one in self.client.get("/launchers").json()
                    ["launchers"] if one["launcher_id"] == launcher_id)

    def test_it_counts_the_tables_it_plays_and_names_where_they_go(self) -> None:
        said = self._fallback("wide")

        self.assertEqual(said["tables"], 3)
        self.assertEqual(said["fallbacks"], [{"launcher_id": "plain",
                                              "display_name": "VPX Plain",
                                              "tables": 3, "has_program": True}])
        self.assertEqual(said["refused"], "")

    def test_a_table_pointed_elsewhere_is_not_counted(self) -> None:
        self.store.assign("t1", "plain")

        self.assertEqual(self._fallback("wide")["tables"], 2)

    def test_the_list_counts_what_each_plays(self) -> None:
        self.store.assign("t1", "plain")

        self.assertEqual(self.client.get("/launchers").json()["tables"],
                         {"wide": 2, "plain": 1})

    def test_a_switched_off_one_plays_none(self) -> None:
        self.store.assign("t1", "plain")
        self._put("plain", display_name="VPX Plain", bin_path="/opt/plain", enabled=False)

        self.assertEqual(self.client.get("/launchers").json()["tables"],
                         {"wide": 3, "plain": 0})

    def test_one_pointed_at_a_launcher_that_is_not_the_default_is(self) -> None:
        self.store.assign("t1", "plain")

        said = self._fallback("plain")

        self.assertEqual(said["tables"], 1)
        self.assertEqual(said["fallbacks"][0]["launcher_id"], "wide")

    def test_switching_off_onto_a_launcher_with_no_program_is_refused(self) -> None:
        self._put("plain", display_name="VPX Plain")

        got = self._put("wide", display_name="VPX Wide", bin_path="/opt/wide",
                        enabled=False)

        self.assertEqual(got.status_code, 400, got.text)
        self.assertIn("VPX Plain", got.json()["error"]["message"])
        self.assertTrue(self._enabled("wide"))

    def test_and_the_fallback_says_so_before_anything_is_tried(self) -> None:
        self._put("plain", display_name="VPX Plain")

        said = self._fallback("wide")

        self.assertFalse(said["fallbacks"][0]["has_program"])
        self.assertIn("VPX Plain", said["refused"])

    def test_switching_off_the_last_launcher_for_its_tables_is_refused(self) -> None:
        delete_launcher(self.client, "plain")
        self._put("gen", app="generic", display_name="Generic", bin_path="/opt/gen")

        got = self._put("wide", display_name="VPX Wide", bin_path="/opt/wide",
                        enabled=False)

        self.assertEqual(got.status_code, 400, got.text)
        self.assertEqual(self._fallback("wide")["fallbacks"][0]["launcher_id"], "")

    def test_one_no_table_uses_switches_off_whatever_the_fallback(self) -> None:
        self._put("spare", display_name="Spare")

        got = self._put("plain", display_name="VPX Plain", bin_path="/opt/plain",
                        enabled=False)

        self.assertEqual(got.status_code, 200, got.text)

    def test_a_working_fallback_lets_it_go(self) -> None:
        got = self._put("wide", display_name="VPX Wide", bin_path="/opt/wide",
                        enabled=False)

        self.assertEqual(got.status_code, 200, got.text)
        self.assertFalse(self._enabled("wide"))


if __name__ == "__main__":
    unittest.main()
