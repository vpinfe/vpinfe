import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import common.config.paths

ENV_VAR = "VPINFE_CONFIG_DIR"


def _as(cls, attr: str):
    return lambda *a, **k: getattr(cls(*a, **k), attr)


class TestStateDir(unittest.TestCase):
    """common.config.paths.STATE_DIR on each platform, with platformdirs' answers stubbed."""

    def setUp(self) -> None:
        self._saved = os.environ.get(ENV_VAR)

    def tearDown(self) -> None:
        if self._saved is None:
            os.environ.pop(ENV_VAR, None)
        else:
            os.environ[ENV_VAR] = self._saved
        importlib.reload(common.config.paths)

    def _resolve(self, config: str, state: str, platform: str, logs: str = "/srv/logs"):
        os.environ.pop(ENV_VAR, None)
        with (patch("platformdirs.user_config_dir", lambda *a, **k: config),
              patch("platformdirs.user_state_dir", lambda *a, **k: state),
              patch("platformdirs.user_log_dir", lambda *a, **k: logs),
              patch.object(sys, "platform", platform)):
            return importlib.reload(common.config.paths)

    def test_linux_keeps_state_in_the_xdg_state_folder(self) -> None:
        paths = self._resolve("/srv/vpinfe/config", "/srv/vpinfe/state",
                              "linux")

        self.assertEqual(paths.STATE_DIR, Path("/srv/vpinfe/state"))

    def test_macos_keeps_state_in_a_folder_of_the_config_folder(self) -> None:
        both = "/Library/Application Support/vpinfe"

        paths = self._resolve(both, both, "darwin")

        self.assertEqual(paths.STATE_DIR, Path(both) / "state")

    def test_windows_keeps_state_beside_its_cache_and_logs(self) -> None:
        both = "/ProgramData/vpinfe/vpinfe"

        paths = self._resolve(both, both, "win32")

        self.assertEqual(paths.STATE_DIR, Path(both) / "State")

    def test_an_override_keeps_state_beside_it(self) -> None:
        override = Path(tempfile.gettempdir()) / "vpinfe-override"
        os.environ[ENV_VAR] = str(override)
        paths = importlib.reload(common.config.paths)

        self.assertEqual(paths.STATE_DIR, override.absolute() / "state")


class TestLogAndStateFiles(unittest.TestCase):
    """What lives in the log, state and cache folders rather than at the config folder's top."""

    setUp = TestStateDir.setUp
    tearDown = TestStateDir.tearDown
    _resolve = TestStateDir._resolve

    def test_the_log_folder_is_what_each_platform_calls_its_own(self) -> None:
        from platformdirs.macos import MacOS
        from platformdirs.unix import Unix
        from platformdirs.windows import Windows

        home = Path.home()
        for cls, platform, expected in (
                (Unix, "linux", home / ".local/state/vpinfe/log"),
                (MacOS, "darwin", home / "Library/Logs/vpinfe"),
                (Windows, "win32", None)):
            with self.subTest(platform=platform), \
                    patch("platformdirs.windows.get_win_folder", lambda name: "/appdata"), \
                    patch.dict(os.environ, {}, clear=False):
                os.environ.pop("XDG_STATE_HOME", None)
                os.environ.pop(ENV_VAR, None)
                with (patch("platformdirs.user_config_dir",
                            _as(cls, "user_config_dir")),
                      patch("platformdirs.user_state_dir",
                            _as(cls, "user_state_dir")),
                      patch("platformdirs.user_log_dir",
                            _as(cls, "user_log_dir")),
                      patch.object(sys, "platform", platform)):
                    paths = importlib.reload(common.config.paths)

                self.assertEqual(paths.LOG_DIR, expected or paths.CONFIG_DIR / "Logs")

    def test_an_override_keeps_the_logs_beside_it(self) -> None:
        override = Path(tempfile.gettempdir()) / "vpinfe-override"
        os.environ[ENV_VAR] = str(override)
        paths = importlib.reload(common.config.paths)

        self.assertEqual(paths.LOG_DIR, override.absolute() / "logs")

    def test_each_file_is_in_the_folder_for_its_kind(self) -> None:
        with tempfile.TemporaryDirectory() as override:
            os.environ[ENV_VAR] = override
            paths = importlib.reload(common.config.paths)

            self.assertEqual(paths.CAPTURE_DIR, paths.STATE_DIR / "capture")
            self.assertEqual(paths.UPDATES_DIR, paths.STATE_DIR / "updates")
            self.assertEqual(paths.DERIVED_TAGS_PATH, paths.CACHE_DIR / "derived_tags.json")
            with patch.dict(os.environ):
                self.assertEqual(Path(paths.configure_nicegui_storage()),
                                 paths.STATE_DIR / ".nicegui")

    def test_the_modules_that_write_them_follow_the_folders(self) -> None:
        from common.capture import proposals, run, trial, unshown
        from common.capture.adapters import portal
        from common.games import dismissals
        from common.host import launch
        from common.online import app_updater

        self.assertEqual(dismissals.PATH.parent, common.config.paths.STATE_DIR)
        self.assertEqual(app_updater.LAST_UPDATE_LOG.parent, common.config.paths.LOG_DIR)
        for held in (proposals.ROOT, run.WORK, run.RUN_FILE, trial.WORK, unshown.FILE,
                     portal.KEPT, launch.CAPTURE_LAUNCH_DIR):
            with self.subTest(held=held):
                self.assertTrue(held.is_relative_to(common.config.paths.CAPTURE_DIR), held)


if __name__ == "__main__":
    unittest.main()
