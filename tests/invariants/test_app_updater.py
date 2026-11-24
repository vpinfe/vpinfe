import tempfile
import unittest
from pathlib import Path
from unittest import mock

from common.atomic_write import STAGED_PREFIX, STAGED_SUFFIX
from common.online import app_updater
from common.online.app_updater import _build_posix_update_script, _clear_abandoned_downloads
from common.online.update_scripts import _build_macos_update_script


class TestAppUpdaterScripts(unittest.TestCase):
    def test_build_posix_update_script_keeps_shell_env_expansion_literals(self) -> None:
        prepared = {
            "zip_path": "/tmp/vpinfe.zip",
            "stage_dir": "/tmp/stage",
            "install_root": "/tmp/install",
            "launch_target": "/tmp/install/vpinfe",
            "last_update_log": "/tmp/last_update.log",
        }

        script = _build_posix_update_script(prepared, current_pid=1234,
                                           log_path=Path("/tmp/apply_update.log"))

        self.assertIn("DISPLAY=${DISPLAY:-<unset>}", script)
        self.assertIn("WAYLAND_DISPLAY=${WAYLAND_DISPLAY:-<unset>}", script)
        self.assertIn("XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR:-<unset>}", script)

    def test_build_posix_update_script_repairs_linux_chromium_permissions(self) -> None:
        prepared = {
            "zip_path": "/tmp/vpinfe.zip",
            "stage_dir": "/tmp/stage",
            "install_root": "/tmp/install",
            "launch_target": "/tmp/install/vpinfe",
            "last_update_log": "/tmp/last_update.log",
        }

        script = _build_posix_update_script(prepared, current_pid=1234,
                                           log_path=Path("/tmp/apply_update.log"))

        self.assertIn('CHROMIUM_ROOT="$INSTALL_ROOT/_internal/chromium/linux/chrome"', script)
        self.assertIn('find "$CHROMIUM_ROOT" -type d -exec chmod a+rx {} +', script)
        self.assertIn("chrome_crashpad_handler chrome-sandbox", script)


class TestMacInstallContext(unittest.TestCase):
    def _context(self, bundle: Path, *, writable: bool = True) -> dict:
        exe = bundle / "Contents" / "MacOS" / "VPinFE"
        exe.parent.mkdir(parents=True)
        exe.touch()
        with (mock.patch.object(app_updater.platform, "system", return_value="Darwin"),
              mock.patch.object(app_updater.platform, "machine", return_value="arm64"),
              mock.patch.object(app_updater.sys, "frozen", True, create=True),
              mock.patch.object(app_updater.sys, "executable", str(exe)),
              mock.patch.object(app_updater, "get_version", return_value="v3.0.0"),
              mock.patch.object(app_updater.release_notes, "is_release", return_value=True),
              mock.patch.object(app_updater, "_bundled_chromium_exists", return_value=False),
              mock.patch.object(app_updater.os, "access", return_value=writable)):
            return app_updater.get_install_context()

    def test_an_app_in_a_folder_the_person_can_write_updates_itself(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp).resolve() / "VPinFE.app"
            context = self._context(bundle)
        self.assertTrue(context["supported"])
        self.assertEqual(context["triplet"], "macos-arm64-slim")
        self.assertEqual(context["install_root"], bundle)
        self.assertEqual(context["launch_target"], bundle)

    def test_an_app_that_was_never_moved_is_not_the_installed_one(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp).resolve() / "AppTranslocation" / "ABC" / "d" / "VPinFE.app"
            context = self._context(bundle)
        self.assertFalse(context["supported"])
        self.assertEqual(context["reason"], "macos_not_installed")

    def test_a_folder_the_person_cannot_write_says_so(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = self._context(Path(tmp).resolve() / "VPinFE.app", writable=False)
        self.assertFalse(context["supported"])
        self.assertEqual(context["reason"], "macos_folder_not_writable")


class TestMacUpdateScript(unittest.TestCase):
    def test_the_old_app_is_touched_only_after_the_new_one_verifies(self) -> None:
        prepared = {
            "zip_path": "/tmp/vpinfe.zip",
            "stage_dir": "/tmp/stage",
            "install_root": "/Applications/VPinFE.app",
            "last_update_log": "/tmp/last_update.log",
        }

        script = _build_macos_update_script(prepared, current_pid=1234,
                                            log_path=Path("/tmp/apply_update.log"))

        self.assertLess(script.index("codesign --verify"), script.index('mv "$INSTALL_ROOT"'))
        self.assertIn("hdiutil attach", script)
        self.assertIn('open "$INSTALL_ROOT"', script)
        self.assertIn('mv "$BACKUP_ROOT" "$INSTALL_ROOT"', script)
        self.assertIn("trap finish EXIT", script)
        self.assertIn("-name '*.app'", script)


class TestAbandonedDownloads(unittest.TestCase):
    def test_a_killed_download_leaves_nothing_behind_the_next_one(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stage = Path(tmp)
            killed = stage / f"{STAGED_PREFIX}0123456789ab{STAGED_SUFFIX}"
            killed.write_bytes(b"half a zip")
            kept = stage / "vpinfe-3.0.zip"
            kept.write_bytes(b"a whole one")

            _clear_abandoned_downloads(stage)

            self.assertFalse(killed.exists())
            self.assertTrue(kept.exists())


if __name__ == "__main__":
    unittest.main()
