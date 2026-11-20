"""DOF's config is read from one folder: the one Visual Pinball's DOF plugin reads."""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from common.host import dof_config, dof_service
from tests import REAL_MACHINE_FOLDERS


class WhereTheFolderIsTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.enterContext(mock.patch("apps.vpx.config._machine_folders", REAL_MACHINE_FOLDERS))

    def _on(self, platform: str, **env: str) -> None:
        home = str(self.root)
        environ = {"HOME": home, "USERPROFILE": home, "APPDATA": "", "XDG_DATA_HOME": "",
                   **env}
        self.enterContext(mock.patch.dict(os.environ, environ))
        self.enterContext(mock.patch("sys.platform", platform))

    def test_per_platform_with_no_version_folder_it_is_the_newest_releases(self) -> None:
        for platform, data, env in (
                ("linux", "xdg/VPinballX", {"XDG_DATA_HOME": "xdg"}),
                ("linux", ".local/share/VPinballX", {}),
                ("darwin", "Library/Application Support/VPinballX", {}),
                ("win32", "Roaming/VPinballX", {"APPDATA": "Roaming"})):
            with self.subTest(platform=platform, data=data):
                self._on(platform, **{k: str(self.root / v) for k, v in env.items()})

                self.assertEqual(dof_config.base_path(), self.root / data / "10.8")
                self.assertEqual(dof_config.config_folder(),
                                 self.root / data / "10.8" / "directoutputconfig")

    def test_the_newest_version_folder_there_is_wins(self) -> None:
        data = self.root / "Library" / "Application Support" / "VPinballX"
        for version in ("10.8", "10.10", "9.2"):
            (data / version).mkdir(parents=True)
        self._on("darwin")

        self.assertEqual(dof_config.config_folder(), data / "10.10" / "directoutputconfig")

    def test_it_is_the_folder_of_the_version_the_default_launcher_runs(self) -> None:
        data = self.root / "Library" / "Application Support" / "VPinballX"
        for version in ("10.8", "10.9"):
            (data / version).mkdir(parents=True)
        self._on("darwin")

        with mock.patch("common.games.launchers.default_value", return_value="vpx"), \
                mock.patch("apps.vpx.program.folder_version", return_value="10.8"):
            self.assertEqual(dof_config.config_folder(), data / "10.8" / "directoutputconfig")

    def test_a_file_named_like_a_version_is_not_one(self) -> None:
        data = self.root / "Library" / "Application Support" / "VPinballX"
        (data / "10.8").mkdir(parents=True)
        (data / "11.0").write_text("", encoding="utf-8")
        self._on("darwin")

        self.assertEqual(dof_config.base_path(), data / "10.8")

    def test_a_platform_with_no_preferences_folder_names_none(self) -> None:
        self._on("win32")

        self.assertIsNone(dof_config.base_path())
        self.assertIsNone(dof_config.config_folder())


class WhetherThereIsConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.folder = Path(tmp.name) / "directoutputconfig"
        self.enterContext(mock.patch.object(dof_config, "config_folder",
                                            lambda: self.folder))

    def test_a_folder_that_is_not_there_has_none(self) -> None:
        self.assertFalse(dof_config.present())

    def test_an_empty_folder_has_none(self) -> None:
        self.folder.mkdir()

        self.assertFalse(dof_config.present())

    def test_a_folder_holding_only_folders_has_none(self) -> None:
        (self.folder / "sub").mkdir(parents=True)

        self.assertFalse(dof_config.present())

    def test_a_folder_with_a_file_has_some(self) -> None:
        self.folder.mkdir()
        (self.folder / "directoutputconfig30.ini").write_text("", encoding="utf-8")

        self.assertTrue(dof_config.present())

    def test_what_a_file_manager_leaves_behind_is_not_config(self) -> None:
        self.folder.mkdir()
        for name in (".DS_Store", "Thumbs.db", "desktop.ini"):
            (self.folder / name).write_text("", encoding="utf-8")

        self.assertFalse(dof_config.present())

    def test_a_platform_that_names_no_folder_is_not_judged(self) -> None:
        with mock.patch.object(dof_config, "config_folder", lambda: None):
            self.assertTrue(dof_config.present())


class TheHelperIsGivenItTests(unittest.TestCase):
    def test_a_start_gives_the_helper_the_folder(self) -> None:
        helper = mock.Mock()
        helper.request.return_value = {"ok": True, "started": True, "detail": "x"}
        with (mock.patch.object(dof_service, "_is_enabled", return_value=True),
              mock.patch.object(dof_service, "_get_helper", return_value=helper),
              mock.patch.object(dof_service, "_HELPER", None),
              mock.patch.object(dof_config, "base_path", return_value=Path("/prefs/10.8"))):
            self.assertTrue(dof_service.start_dof_service_if_enabled(mock.Mock()))

        helper.request.assert_called_once_with("start", timeout=10.0,
                                              base_path=str(Path("/prefs/10.8")))

    def test_a_platform_with_none_gives_the_runner_its_own_default(self) -> None:
        helper = mock.Mock()
        helper.request.return_value = {"ok": True, "started": True, "detail": "x"}
        with (mock.patch.object(dof_service, "_is_enabled", return_value=True),
              mock.patch.object(dof_service, "_get_helper", return_value=helper),
              mock.patch.object(dof_service, "_HELPER", None),
              mock.patch.object(dof_config, "base_path", return_value=None)):
            dof_service.start_dof_service_if_enabled(mock.Mock())

        helper.request.assert_called_once_with("start", timeout=10.0, base_path="")


if __name__ == "__main__":
    unittest.main()
