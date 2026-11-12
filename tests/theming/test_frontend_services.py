from __future__ import annotations

import configparser
import json
import os
import threading
import types
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from common.devices import device_registry
from common.games import game_play_service, game_report_service
from common.games.game_metadata import game_frontend_dof_event, set_game_rating
from common.games.tables import entry_for_filename
from common.host import real_dmd, system_actions
from common.i18n import t
from common.runtime import jobs
from frontend import config_api, theme_api
from tests.support.accepted import owner_accepts_theme
from tests.support.library import own_registry, write_game
from tests.support.saved import saved_record


class FrontendServiceTests(unittest.TestCase):
    def test_system_actions_restart_reexecs_when_flag_exists(self):
        with TemporaryDirectory() as temp_dir:
            own_registry(self, Path(temp_dir) / "devices.json")
            system_actions.request_app_restart()
            logger = types.SimpleNamespace(info=mock.Mock())
            calls = []

            with (mock.patch("common.host.system_actions.os.execvp",
                             side_effect=lambda *args: calls.append(args)),
                  mock.patch.object(system_actions.sys, "argv",
                                    ["main.py", "--headless", "--configdir", "/cfg"])):
                system_actions.restart_if_requested(
                    logger,
                    main_script=Path("/app/main.py"),
                    sleep_func=lambda _seconds: None,
                )

            self.assertFalse(device_registry.get_device_registry().self_get("restart_requested"))
            # The code passes main_script through os.path.abspath, which on Windows
            # prefixes the current drive. Expect what it actually builds.
            main_script = os.path.abspath(Path("/app/main.py"))
            self.assertEqual(
                calls,
                [(system_actions.sys.executable,
                  [system_actions.sys.executable, main_script,
                   "--headless", "--configdir", "/cfg"])],
            )

    def test_a_frozen_build_restarts_with_its_flags(self):
        with TemporaryDirectory() as temp_dir:
            own_registry(self, Path(temp_dir) / "devices.json")
            system_actions.request_app_restart()
            calls = []

            with (mock.patch("common.host.system_actions.os.execvp",
                             side_effect=lambda *args: calls.append(args)),
                  mock.patch.object(system_actions.sys, "frozen", True, create=True),
                  mock.patch.object(system_actions.sys, "argv", ["VPinFE", "--headless"])):
                system_actions.restart_if_requested(
                    types.SimpleNamespace(info=mock.Mock()),
                    sleep_func=lambda _seconds: None)

            self.assertEqual(calls, [(system_actions.sys.executable,
                                      [system_actions.sys.executable, "--headless"])])

    def test_theme_config_missing_file_is_optional(self):
        parser = configparser.ConfigParser()
        parser["general"] = {"theme": "Example"}
        with (TemporaryDirectory() as temp_dir,
              mock.patch("frontend.theme_api.THEMES_DIR", Path(temp_dir))):
            (Path(temp_dir) / "Example").mkdir()
            self.assertIsNone(theme_api.get_theme_config(parser))

    def _theme_config(self, **files):
        """get_theme_config for a theme dir holding the given <name>.json files."""
        parser = configparser.ConfigParser()
        parser["general"] = {"theme": "Example"}
        owner_accepts_theme(self, "Example")
        with (
            TemporaryDirectory() as temp_dir,
            mock.patch("frontend.theme_api.THEMES_DIR", Path(temp_dir)),
        ):
            theme_dir = Path(temp_dir) / "Example"
            theme_dir.mkdir()
            for name, body in files.items():
                (theme_dir / f"{name}.json").write_text(json.dumps(body), encoding="utf-8")
            return theme_api.get_theme_config(parser)

    def test_theme_config_merges_author_config_under_user_options(self):
        """Revolution's shape: one user option must not drop the author's config.json."""
        result = self._theme_config(
            config={"use_core_audio": True, "audio": {"enabled": True}},
            theme={"options": [{"key": "startCollectionSelection", "value": False}]},
        )

        self.assertEqual(
            result,
            {
                "use_core_audio": True,
                "audio": {"enabled": True},
                "startCollectionSelection": False,
            },
        )

    def test_theme_config_user_option_wins_but_keeps_the_rest_of_the_section(self):
        result = self._theme_config(
            config={"audio": {"enabled": True, "maxVolume": 0.4}},
            theme={"options": [{"key": "audio.maxVolume", "value": 0.9}]},
        )

        self.assertEqual(result, {"audio": {"enabled": True, "maxVolume": 0.9}})

    def test_theme_config_reads_author_config_when_there_are_no_options(self):
        result = self._theme_config(config={"use_core_audio": True})

        self.assertEqual(result, {"use_core_audio": True})

    def test_theme_config_flattens_values_from_theme_json(self):
        parser = configparser.ConfigParser()
        parser["general"] = {"theme": "Example"}
        owner_accepts_theme(self, "Example")
        with (TemporaryDirectory() as temp_dir,
              mock.patch("frontend.theme_api.THEMES_DIR", Path(temp_dir))):
            theme_dir = Path(temp_dir) / "Example"
            theme_dir.mkdir()
            (theme_dir / "theme.json").write_text(
                json.dumps(
                    {
                        "title": "Example Options",
                        "options": [
                            {"key": "showClock", "type": "boolean", "value": True},
                            {"key": "wheel.scale", "type": "number", "value": 1.25},
                            {"key": "audio.maxVolume", "type": "number", "default": 0.8},
                        ],
                    }
                ),
                encoding="utf-8",
            )

            result = theme_api.get_theme_config(parser)

        self.assertEqual(
            result,
            {
                "showClock": True,
                "wheel": {"scale": 1.25},
                "audio": {"maxVolume": 0.8},
            },
        )

    def test_config_api_set_audio_mute_saves_and_broadcasts(self):
        parser = configparser.ConfigParser()
        parser["general"] = {"muteaudio": "false"}

        class DummyIni:
            def __init__(self):
                self.config = parser
                self.saved = False

            def save(self):
                self.saved = True

        events = []
        api = types.SimpleNamespace(
            _ini_config=DummyIni(),
            send_event_all_windows_incself=lambda event: events.append(event),
        )

        self.assertTrue(config_api.set_audio_muted(api, "true"))
        self.assertEqual(parser["behavior"]["mute_audio"], "true")
        self.assertTrue(api._ini_config.saved)
        self.assertEqual(events, [{"type": "AudioMuteChanged", "muted": True}])

    def test_mainmenu_config_reports_the_setting_rather_than_raising(self):
        """The menu asks once per open, and a raise here is invisible: mainmenu.js catches
        it and shows the quit item, so hide_quit_button silently did nothing.

        The config file has to exist and hold JSON for this to bite - configparser skips a
        path that is not there, which is why a missing file does not reproduce it.
        """
        parser = configparser.ConfigParser()
        parser["behavior"] = {"hide_quit_button": "true"}
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "vpinfe.json"
            path.write_text(json.dumps({"behavior": {"hide_quit_button": True}}))
            ini = types.SimpleNamespace(config=parser, configfilepath=str(path))

            self.assertEqual(config_api.get_mainmenu_config(ini), {"hideQuitButton": True})

    def _attract(self, **behavior):
        parser = configparser.ConfigParser()
        parser["behavior"] = {key: str(value) for key, value in behavior.items()}
        return config_api.get_attract_config(parser)

    def test_attract_config_answers_in_milliseconds_with_the_defaults_when_unset(self):
        self.assertEqual(self._attract(), {
            "enabled": True, "idle_ms": 60000, "step_ms": 8000, "order": "random",
            "on_return": "restore", "audio": False})

    def test_attract_config_reads_what_the_person_set(self):
        self.assertEqual(
            self._attract(attract_enabled="false", attract_idle=30, attract_step_time=5,
                          attract_order="in_order", attract_on_return="stay", attract_audio="true"),
            {"enabled": False, "idle_ms": 30000, "step_ms": 5000, "order": "in_order",
             "on_return": "stay", "audio": True})

    def test_attract_config_never_answers_a_timer_that_fires_every_frame(self):
        found = self._attract(attract_idle=0, attract_step_time=-3)

        self.assertEqual((found["idle_ms"], found["step_ms"]), (1000, 1000))

    def test_an_attract_order_that_is_a_typo_does_not_stop_the_wheel(self):
        self.assertEqual(self._attract(attract_order="sideways")["order"], "random")

    def test_attract_on_return_other_than_stay_restores(self):
        self.assertEqual(self._attract(attract_on_return="whatever")["on_return"], "restore")

    def test_realdmd_helpers_and_updater_process_pending(self):
        game = types.SimpleNamespace(
            game_dir_name="Example",
            real_dmd_image_path="/tmp/real_dmd.png",
            real_dmd_color_image_path="/tmp/realdmd-color.png",
            meta_config={"vpinfe": {"frontend_dof_event": "E901"}},
        )
        color_config = configparser.ConfigParser()
        color_config.read_dict({"Media": {"realdmdmediapriority": "color"}})
        standard_config = configparser.ConfigParser()
        standard_config.read_dict({"Media": {"realdmdmediapriority": "standard"}})

        # get_realdmd_image_for_game returns path.resolve(); on macOS /tmp is a
        # symlink to /private/tmp, so compare against the resolved expectation.
        color_expected = Path("/tmp/realdmd-color.png").resolve()
        standard_expected = Path("/tmp/real_dmd.png").resolve()
        self.assertEqual(game_frontend_dof_event(game), "E901")
        self.assertEqual(real_dmd.get_realdmd_image_for_game(game), color_expected)
        self.assertEqual(real_dmd.get_realdmd_image_for_game(game, color_config), color_expected)
        self.assertEqual(real_dmd.get_realdmd_image_for_game(game, standard_config),
                         standard_expected)

        game.real_dmd_color_image_path = ""
        self.assertEqual(real_dmd.get_realdmd_image_for_game(game, color_config), standard_expected)

        calls = []
        updater = real_dmd.RealDmdUpdater(
            "ini", "table", lambda ini, image: calls.append((ini, image)) or True)
        updater._game_name = "Example"
        updater._image_path = Path("/tmp/real_dmd.png")
        updater._process_pending()
        self.assertEqual(calls, [("ini", Path("/tmp/real_dmd.png"))])

    def test_game_report_service_logs_unknown_game(self):
        parser_instance = mock.Mock()
        game = types.SimpleNamespace(game_dir_name="Unknown")
        parser_instance.get_all_games.return_value = [game]
        vps_instance = mock.Mock()
        vps_instance.__len__ = mock.Mock(return_value=0)
        vps_instance.parse_game_name_from_dir.return_value = {
            "name": "Unknown", "manufacturer": "", "year": ""}
        vps_instance.lookup_name.return_value = None
        logs = []
        ini = types.SimpleNamespace(config={})

        def log(msg, *args):
            logs.append(msg % args if args else msg)

        with (
            mock.patch("common.games.game_repository.all_games",
                       return_value=parser_instance.get_all_games.return_value),
            mock.patch("common.games.game_report_service.VPSdb", return_value=vps_instance),
        ):
            game_report_service.list_unknown_games(iniconfig=ini, log=log)

        self.assertTrue(any("Unknown table 1: Unknown" in line for line in logs))

    def test_frontend_rating_write_preserves_newer_on_disk_stats(self):
        with TemporaryDirectory() as temp_dir:
            game_dir = write_game(temp_dir, "Example", vpx=False)
            info_path = game_dir / "Example.info"
            info_path.write_text(
                json.dumps(
                    {
                        "Info": {"Title": "Example", "VPSId": "vps-1"},
                        "User": {"Rating": 0, "StartCount": 0, "RunTime": 0},
                        "VPXFile": {},
                        "vpinfe": {},
                    }
                ),
                encoding="utf-8",
            )
            game = types.SimpleNamespace(
                full_path_game=str(game_dir),
                game_dir_name="Example",
                meta_config=json.loads(info_path.read_text(encoding="utf-8")),
            )

            info_path.write_text(
                json.dumps(
                    {
                        "Info": {"Title": "Example", "VPSId": "vps-1"},
                        "User": {"Rating": 0, "StartCount": 7, "RunTime": 15},
                        "VPXFile": {},
                        "vpinfe": {},
                    }
                ),
                encoding="utf-8",
            )

            self.assertEqual(set_game_rating(game, 5), 5)
            saved = saved_record(info_path)
            self.assertEqual(saved["User"]["Rating"], 5)
            self.assertEqual(saved["User"]["StartCount"], 7)
            self.assertEqual(saved["User"]["RunTime"], 15)

    def test_play_tracking_preserves_newer_on_disk_rating(self):
        with TemporaryDirectory() as temp_dir:
            game_dir = write_game(temp_dir, "Example", vpx=False)
            info_path = game_dir / "Example.info"
            info_path.write_text(
                json.dumps(
                    {
                        "Info": {"Title": "Example", "VPSId": "vps-1"},
                        "User": {"Rating": 0, "StartCount": 0, "RunTime": 0},
                        "VPXFile": {},
                        "vpinfe": {},
                    }
                ),
                encoding="utf-8",
            )
            game = types.SimpleNamespace(
                full_path_game=str(game_dir),
                game_dir_name="Example",
                meta_config=json.loads(info_path.read_text(encoding="utf-8")),
            )

            info_path.write_text(
                json.dumps(
                    {
                        "Info": {"Title": "Example", "VPSId": "vps-1"},
                        "User": {"Rating": 4, "StartCount": 0, "RunTime": 0},
                        "VPXFile": {},
                        "vpinfe": {},
                    }
                ),
                encoding="utf-8",
            )

            game_play_service.increment_start_count(game)

            saved = saved_record(info_path)
            self.assertEqual(saved["User"]["Rating"], 4)
            self.assertEqual(saved["User"]["StartCount"], 1)

    def test_parse_score_from_nvram_reads_the_rom_it_is_given(self) -> None:
        with TemporaryDirectory() as tmp:
            game_dir = write_game(tmp, "Example", vpx=False)
            game = types.SimpleNamespace(
                full_path_game=str(game_dir),
                game_dir_name="Example",
                meta_config={},
            )

            with (
                mock.patch("common.games.score_parser.read_rom_with_source",
                           return_value=(123, "/scores/vpx_rom.nv")) as read_rom,
                mock.patch("common.games.score_parser.result_to_jsonable",
                           return_value={"rom": "vpx_rom"}) as to_json,
            ):
                score_data, score_path = game_play_service.parse_score_from_nvram(
                    game, "vpx_rom")  # type: ignore[arg-type]

            read_rom.assert_called_once_with("vpx_rom", str(game_dir))
            to_json.assert_called_once_with("vpx_rom", 123, "/scores/vpx_rom.nv", None)
            self.assertEqual(score_data, {"rom": "vpx_rom"})
            self.assertEqual(score_path, "/scores/vpx_rom.nv")

    def test_high_scores_are_kept_under_their_rom_beside_the_play_record(self) -> None:
        with TemporaryDirectory() as tmp:
            game_dir = write_game(tmp, "Example", vpx=False)
            info_path = game_dir / "Example.info"
            info_path.write_text(json.dumps({"User": {"Rating": 4, "StartCount": 2},
                                             "vpinfe": {"schema": 2}}), encoding="utf-8")
            game = types.SimpleNamespace(full_path_game=str(game_dir),
                                         game_dir_name="Example", meta_config={})
            reading = {"rom": "vpx_rom", "score_kind": "Leaderboard", "entries": [
                {"section": "HIGH SCORES", "rank": 1, "initials": "", "score": 10}]}

            game_play_service.keep_high_scores(
                game, "vpx_rom", reading, None, "/nv")  # type: ignore[arg-type]

            saved = saved_record(info_path)["User"]
            self.assertEqual((saved["Rating"], saved["StartCount"]), (4, 2))
            self.assertEqual(saved["HighScores"]["vpx_rom"]["entries"], reading["entries"])
            self.assertNotIn("Score", saved)

    def _nvram(self, tmp: str, *, folder: bool, table: bool | None) -> tuple:
        """A folder with two ROMs' NVRAM, Clear NVRAM on Exit set for the folder and,
        where `table` is not None, for the table on `vpx_rom` itself."""
        game_dir = Path(tmp) / "Example"
        nvram_dir = game_dir / "pinmame" / "nvram"
        nvram_dir.mkdir(parents=True)
        played, other = nvram_dir / "vpx_rom.nv", nvram_dir / "info_rom.nv"
        played.write_bytes(b"vpx")
        other.write_bytes(b"info")
        entry = {"id": "t2", "filename": "Mod.vpx", "rom": "vpx_rom",
                 **({"vpinfe": {"delete_nvram_on_close": table}} if table is not None else {})}
        game = types.SimpleNamespace(
            full_path_game=str(game_dir), game_dir_name="Example",
            meta_config={"tables": {"t1": {"id": "t1", "filename": "Example.vpx",
                                           "rom": "info_rom"}, "t2": entry},
                         "vpinfe": {"delete_nvram_on_close": folder}})
        return game, entry, played, other

    def test_delete_nvram_if_configured_deletes_the_played_tables_rom(self) -> None:
        with TemporaryDirectory() as tmp:
            game, entry, played, other = self._nvram(tmp, folder=True, table=None)

            game_play_service.delete_nvram_if_configured(game, entry)

            self.assertFalse(played.exists())
            self.assertTrue(other.exists())

    def test_a_tables_own_clear_nvram_setting_comes_before_the_folders(self) -> None:
        """What the Manager's switch under a table writes."""
        for folder, table, cleared in ((False, True, True), (True, False, False)):
            with self.subTest(folder=folder, table=table), TemporaryDirectory() as tmp:
                game, entry, played, _other = self._nvram(tmp, folder=folder, table=table)

                game_play_service.delete_nvram_if_configured(game, entry)

                self.assertEqual(not played.exists(), cleared)


class PerTablePlayStatsTests(unittest.TestCase):
    """A folder holds several tables and the API can launch any of them, so a play
    is credited to the one that ran as well as to the table."""

    def _launch(self, config, table, seconds=90, played_at=1000):
        game_play_service.apply_start_count_update(config, played_at, table)
        game_play_service.apply_runtime_update(config, seconds, table)
        return config

    def test_a_launch_counts_against_the_game_and_the_table(self):
        config = self._launch({}, "Example (VR).vpx")

        self.assertEqual(config["User"]["StartCount"], 1)
        self.assertEqual(config["User"]["LastRun"], 1000)
        played = entry_for_filename(config["tables"], "Example (VR).vpx")[1]["user"]
        self.assertEqual(played, {"last_run": "1970-01-01T00:16:40Z",
                                  "start_count": 1, "run_time_seconds": 90})

    def test_each_table_keeps_its_own_count(self):
        config = self._launch({}, "Example.vpx")
        self._launch(config, "Example.vpx")
        self._launch(config, "Example (VR).vpx")

        entries = config["tables"]
        self.assertEqual(
            entry_for_filename(entries, "Example.vpx")[1]["user"]["start_count"], 2)
        self.assertEqual(
            entry_for_filename(entries, "Example (VR).vpx")[1]["user"]["start_count"], 1)
        self.assertEqual(config["User"]["StartCount"], 3, "the table saw all three")

    def test_the_game_total_is_not_a_rollup(self):
        """Deleting a table must not un-play hours that were played, which is what
        a total summed from the entries would do."""
        config = self._launch({}, "Example.vpx")
        self._launch(config, "Gone.vpx")
        del config["tables"][entry_for_filename(config["tables"], "Gone.vpx")[0]]

        self.assertEqual(config["User"]["StartCount"], 2)
        self.assertEqual(config["vpinfe"]["run_time_seconds"], 180)
        self.assertEqual(config["User"]["RunTime"], 3, "minutes, as the spec key always was")

    def test_a_short_session_is_not_charged_a_whole_minute(self):
        """Start a table and quit straight back out and the play count goes up, which is
        right. The play time used to go up by a minute too, every time."""
        config = self._launch({}, "Example.vpx", seconds=3)

        self.assertEqual(config["vpinfe"]["run_time_seconds"], 3)
        self.assertEqual(config["User"]["RunTime"], 0)

    def test_short_sessions_add_up_instead_of_being_lost(self):
        """The other way of getting the minutes right is to floor each session, which
        throws away everything under a minute. Twenty of these is a real ten minutes."""
        config = {}
        for _ in range(20):
            self._launch(config, "Example.vpx", seconds=30)

        self.assertEqual(config["vpinfe"]["run_time_seconds"], 600)
        self.assertEqual(config["User"]["RunTime"], 10)

    def test_a_library_played_before_this_carries_its_history_forward(self):
        """The seconds counter is new. A game with only the old minutes must not read
        as never played."""
        config = {"User": {"RunTime": 7}}

        self._launch(config, "Example.vpx", seconds=30)

        self.assertEqual(config["vpinfe"]["run_time_seconds"], 7 * 60 + 30)
        self.assertEqual(config["User"]["RunTime"], 7)

    def test_the_minutes_are_a_view_and_never_drift(self):
        config = {}
        for seconds in (45, 20, 130, 5):
            self._launch(config, "Example.vpx", seconds=seconds)

        total = config["vpinfe"]["run_time_seconds"]
        self.assertEqual(total, 200)
        self.assertEqual(config["User"]["RunTime"], total // 60)

    def test_a_hidden_table_still_accrues_and_stays_hidden(self):
        """Hiding is presentation: the API can still launch it, and it is still played."""
        config = {"tables": {"Base.vpx": {"hidden": True, "rom": "afm_113b"}}}
        self._launch(config, "Base.vpx")

        entry = entry_for_filename(config["tables"], "Base.vpx")[1]
        self.assertEqual(entry["user"]["start_count"], 1)
        self.assertTrue(entry["hidden"])
        self.assertEqual(entry["rom"], "afm_113b", "the parse must survive a play")

    def test_a_launch_with_no_table_named_still_counts_for_the_game(self):
        """Nothing outside the launch path knows which file ran."""
        config = self._launch({}, "")

        self.assertEqual(config["User"]["StartCount"], 1)
        self.assertNotIn("tables", config)


class BuildMetadataJobTests(unittest.TestCase):
    """The main menu's Build Metadata, run as the library job."""

    def setUp(self) -> None:
        jobs.reset_for_tests()
        self.addCleanup(jobs.reset_for_tests)
        self.api = mock.Mock()
        self.ended = threading.Event()
        self.rebuilt = threading.Event()
        self.sent: list[dict] = []

        def send(event: dict) -> None:
            self.sent.append(event)
            if event["type"] in ("buildmeta_complete", "buildmeta_error"):
                self.ended.set()

        self.api.send_event_all_windows_incself.side_effect = send
        for patcher in (mock.patch("frontend.api.all_games", return_value=[]),
                        mock.patch("frontend.game_state.rebuild_view",
                                   side_effect=lambda api: self.rebuilt.set())):
            patcher.start()
            self.addCleanup(patcher.stop)

    def build(self, scan: mock.Mock) -> dict:
        from frontend.api import API

        with mock.patch("common.games.metadata_service.build_metadata", scan):
            API.build_metadata(self.api, download_media=False)
            self.assertTrue(self.ended.wait(5))
        return self.sent[-1]

    def test_it_runs_as_the_library_job(self) -> None:
        running: list[int] = []

        def scan(**_kwargs: object) -> dict:
            running.append(len(jobs.active(jobs.KIND_LIBRARY_SCAN)))
            return {"found": 1, "not_found": 0}

        self.build(mock.Mock(side_effect=scan))

        self.assertTrue(self.rebuilt.wait(5))
        self.assertEqual(running, [1])
        self.assertEqual(self.sent[-1], {"type": "buildmeta_complete",
                                         "result": {"found": 1, "not_found": 0}})

    def test_a_busy_library_is_said_in_words(self) -> None:
        release = threading.Event()
        self.addCleanup(release.set)
        jobs.submit(jobs.KIND_LIBRARY_SCAN, lambda job: release.wait(5))
        scan = mock.Mock()

        event = self.build(scan)

        scan.assert_not_called()
        self.assertEqual(event, {"type": "buildmeta_error",
                                 "error": t("frontend.buildmeta.library_busy")})

    def test_a_scan_that_breaks_says_so_in_words(self) -> None:
        scan = mock.Mock(side_effect=PermissionError(13, "Permission denied", "/tables/x"))

        with self.assertLogs("vpinfe.frontend.metadata_build_service", "ERROR"):
            event = self.build(scan)

        self.assertEqual(event, {"type": "buildmeta_error",
                                 "error": t("frontend.buildmeta.stopped")})


if __name__ == "__main__":
    unittest.main()
