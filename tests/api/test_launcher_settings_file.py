"""Protects: (a) a launcher's settings are never overwritten, or left behind, without a copy
kept and a way back.

Giving a launcher another settings file by copying, or replacing a file others play with.
"""

import pathlib
import unittest
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi.testclient import TestClient

import httpapi
from common.games import launcher_settings, launchers, settings_file_name
from tests.support.launchers_api import put_launcher
from tests.support.library import own_launchers
from tests.support.swaps import library_of


class LauncherSettingsFileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.store = own_launchers(self, self.tmp.name)
        patcher = patch.object(launchers, "get_launcher_store", return_value=self.store)
        patcher.start()
        self.addCleanup(patcher.stop)
        folder = patch.object(launcher_settings, "LAUNCHER_SETTINGS_DIR", self.root / "kept")
        folder.start()
        self.addCleanup(folder.stop)
        library_of(self)
        self.store.save([], {})
        self.client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)
        self.desktop = self._launcher("desktop", "Desktop", "desktop.ini", "desktop settings")
        self.cab = self._launcher("cab", "Cab", "cab.ini", "cab settings")

    def _launcher(self, launcher_id: str, name: str, file: str, text: str) -> pathlib.Path:
        path = self.root / file
        path.write_text(text)
        put_launcher(self.client, launcher_id, json={
            "app": "vpx", "display_name": name, "owns_ini": True,
            "settings": {"bin_path": str(self.root / "vpx"), "ini_path": str(path)}})
        return path

    def _apply(self, launcher_id: str, **body):
        return self.client.post(f"/launchers/{launcher_id}/settings-file", json=body)

    def _held(self, launcher_id: str) -> str:
        return str(self.store.get(launcher_id).settings["ini_path"])

    def test_a_copy_for_a_new_launcher_lands_in_vpinfes_folder_under_its_name(self) -> None:
        got = self._apply("new", choice="copy", copy_from="desktop", create={
            "app": "vpx", "display_name": "Fourk",
            "settings": {"bin_path": str(self.root / "vpx")}})

        self.assertEqual(got.status_code, 200, got.text)
        target = self.root / "kept" / "Fourk.ini"
        self.assertEqual(target.read_text(), "desktop settings")
        self.assertEqual(self._held("new"), str(target))
        self.assertTrue(self.store.get("new").owns_ini)
        self.assertEqual(got.json()["undo"], "")

    def test_settings_others_play_with_are_not_written_over_until_asked(self) -> None:
        got = self._apply("cab", choice="copy", copy_from="cab", file_name="desktop.ini",
                          save_dir=str(self.root))

        self.assertEqual(got.json()["needs_replace"]["users"], ["Desktop"])
        self.assertEqual(self.desktop.read_text(), "desktop settings")
        self.assertEqual(self._held("cab"), str(self.root / "cab.ini"))

    def test_a_replace_keeps_the_old_settings_and_can_be_undone(self) -> None:
        got = self._apply("cab", choice="copy", copy_from="cab", file_name="desktop.ini",
                          save_dir=str(self.root), replace=True)

        self.assertEqual(got.status_code, 200, got.text)
        self.assertTrue(got.json()["backed_up"])
        self.assertEqual(self._held("cab"), str(self.desktop))
        self.assertEqual(self.desktop.read_text(), "cab settings")
        kept = [one for one in self.root.iterdir() if ".vpinfe-" in one.name]
        self.assertEqual([one.read_text() for one in kept], ["desktop settings"])

        self.client.post("/launchers/settings-file/undo", json={"undo": got.json()["undo"]})

        self.assertEqual(self._held("cab"), str(self.root / "cab.ini"))
        self.assertEqual(self.desktop.read_text(), "desktop settings")

    def test_a_launchers_own_file_is_backed_up_when_it_is_left_for_another(self) -> None:
        got = self._apply("cab", choice="pick", existing=str(self.desktop))

        self.assertTrue(got.json()["backed_up"])
        self.assertEqual(self._held("cab"), str(self.desktop))
        self.assertEqual((self.root / "cab.ini").read_text(), "cab settings")
        self.assertEqual(self.client.post("/launchers/settings-file/undo",
                                          json={"undo": got.json()["undo"]}).status_code, 200)
        self.assertEqual(self._held("cab"), str(self.root / "cab.ini"))

    def test_a_name_no_system_can_save_is_refused_before_anything_is_written(self) -> None:
        for name in ("CON.ini", "a:b.ini", ".ini"):
            got = self._apply("cab", choice="copy", copy_from="desktop", file_name=name,
                              save_dir=str(self.root / "elsewhere"))
            self.assertEqual(got.status_code, 400, name)
        self.assertFalse((self.root / "elsewhere").exists())

    def test_a_renamed_launcher_takes_its_own_file_along(self) -> None:
        self._apply("cab", choice="copy", copy_from="desktop")
        first = pathlib.Path(self._held("cab"))
        self.assertEqual(first, self.root / "kept" / "Cab.ini")

        put_launcher(self.client, "cab", json={
            "app": "vpx", "display_name": "Cab 2/4", "owns_ini": True,
            "settings": dict(self.store.get("cab").settings)})
        self.client.post("/launchers/cab/settings-file/renamed", json={"was": "Cab"})

        moved = self.root / "kept" / "Cab 2-4.ini"
        self.assertEqual(self._held("cab"), str(moved))
        self.assertEqual(moved.read_text(), "desktop settings")
        self.assertFalse(first.exists())

    def test_a_launcher_called_after_a_windows_device_gets_a_name_that_can_be_saved(self) -> None:
        for display, expected in (("Aux", "Aux-.ini"), ("nul.old", "nul-.old.ini"),
                                  ("Cab 2/4", "Cab 2-4.ini")):
            name = settings_file_name.from_display_name(display)
            self.assertEqual(name, expected)
            self.assertEqual(settings_file_name.problem(name), "")

    def _receive(self, launcher_id: str, data: bytes):
        import base64

        return self.client.post(f"/launchers/{launcher_id}/settings-file/received",
                                json={"data": base64.b64encode(data).decode("ascii")})

    def test_the_file_goes_into_vpinfes_folder_under_the_launchers_name(self) -> None:
        put_launcher(self.client, "arrived", json={
            "app": "vpx", "display_name": "Cab Two",
            "settings": {"bin_path": str(self.root / "vpx")}})

        got = self._receive("arrived", b"sent settings")

        self.assertEqual(got.status_code, 200, got.text)
        target = self.root / "kept" / "Cab Two.ini"
        self.assertEqual(target.read_bytes(), b"sent settings")
        self.assertEqual(self._held("arrived"), str(target))
        self.assertTrue(self.store.get("arrived").owns_ini)

    def test_a_file_already_there_is_backed_up_before_it_is_replaced(self) -> None:
        put_launcher(self.client, "arrived", json={
            "app": "vpx", "display_name": "Cab Two",
            "settings": {"bin_path": str(self.root / "vpx")}})
        self._receive("arrived", b"first")

        got = self._receive("arrived", b"second")

        self.assertEqual(got.status_code, 200, got.text)
        self.assertEqual((self.root / "kept" / "Cab Two.ini").read_bytes(), b"second")
        self.assertEqual((got.json()["backed_up"], got.json()["replaced"]), (True, True))

    def test_a_launcher_that_is_not_here_is_refused(self) -> None:
        got = self._receive("nobody", b"x")

        self.assertEqual(got.status_code, 404)
        self.assertFalse((self.root / "kept").exists())

    def test_a_file_that_is_too_big_or_not_a_file_is_refused_and_writes_nothing(self) -> None:
        self.assertEqual(
            self._receive("cab", b"x" * (launcher_settings.MAX_RECEIVED + 1)).status_code, 400)
        garbled = self.client.post("/launchers/cab/settings-file/received",
                                   json={"data": "not base64!"})
        self.assertEqual(garbled.status_code, 400)
        self.assertEqual(self._held("cab"), str(self.root / "cab.ini"))
        self.assertEqual((self.root / "cab.ini").read_text(), "cab settings")

    def test_a_name_another_launcher_plays_with_is_not_written_over(self) -> None:
        kept = self.root / "kept"
        kept.mkdir()
        (kept / "Cab Two.ini").write_text("theirs")
        put_launcher(self.client, "other", json={
            "app": "vpx", "display_name": "Other", "owns_ini": True,
            "settings": {"bin_path": str(self.root / "vpx"),
                         "ini_path": str(kept / "Cab Two.ini")}})
        put_launcher(self.client, "arrived", json={
            "app": "vpx", "display_name": "Cab Two",
            "settings": {"bin_path": str(self.root / "vpx")}})

        got = self._receive("arrived", b"sent settings")

        self.assertEqual(got.status_code, 200, got.text)
        self.assertEqual((kept / "Cab Two.ini").read_text(), "theirs")
        self.assertEqual(self._held("other"), str(kept / "Cab Two.ini"))
        self.assertEqual(self._held("arrived"), str(kept / "Cab Two 2.ini"))
        self.assertEqual((kept / "Cab Two 2.ini").read_bytes(), b"sent settings")
