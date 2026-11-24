"""Writing one table's own settings by its id, as an extension asks for it."""

from __future__ import annotations

import pathlib
import unittest
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

import httpapi
from common import service_errors
from common.games import launcher_ops, launchers
from tests.support.launchers_api import put_launcher
from tests.support.library import own_launchers

GRILL = "Plugin.B2SLegacy.B2SHideGrill"


class TableSettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = pathlib.Path(self.tmp.name)
        self.data = root / "data"
        self.folder = root / "Taxi"
        self.folder.mkdir()
        (self.folder / "Taxi.vpx").write_bytes(b"x")
        self.store = own_launchers(self, self.tmp.name)
        game = SimpleNamespace(full_path_game=str(self.folder))
        for patcher in (patch.object(launchers, "get_launcher_store", return_value=self.store),
                        patch("apps.vpx.config._machine_folders",
                              return_value=(self.data / "VPinballX", None)),
                        patch.object(launcher_ops, "find_table_by_id",
                                     side_effect=lambda _games, wanted:
                                     (game, "Taxi.vpx") if wanted == "t1" else None)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.store.save([], {})
        client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)
        put_launcher(client, "l1", json={"app": "vpx", "settings": {"bin_path": "/opt/vpx"}})
        made = client.put("/launchers/l1/config", json={
            "scope": "launcher", "create": True, "values": {GRILL: "0"}})
        self.assertEqual(made.status_code, 200, made.text)

    def test_a_value_is_the_tables_own_and_the_launchers_is_unchanged(self) -> None:
        launcher_ops.write_table_settings("t1", {GRILL: "1"})

        self.assertEqual(launcher_ops.effective_values("l1", "t1")[GRILL], "1")
        self.assertEqual(launcher_ops.effective_values("l1")[GRILL], "0")
        self.assertTrue((self.folder / "Taxi.ini").is_file())

    def test_the_program_is_reported_by_launcher_and_by_table(self) -> None:
        with patch("apps.vpx.config.plugins.present", return_value=frozenset({"Nixie", "B2S"})):
            by_launcher = launcher_ops.program_of("l1")
            by_table = launcher_ops.table_program("t1")
            in_config = launcher_ops.app_config("l1")["program"]

        self.assertEqual(by_launcher, by_table)
        self.assertEqual(by_launcher, in_config)
        assert by_launcher is not None
        self.assertEqual(by_launcher["plugins"], ["B2S", "Nixie"])
        self.assertEqual(by_launcher["defaults"],
                         {"Plugin.B2S.Enable": "1", "Plugin.B2SLegacy.Enable": "0"})
        self.assertGreaterEqual(by_launcher["build"], 6293)

    def test_a_program_whose_plugins_cannot_be_read_says_so(self) -> None:
        found = launcher_ops.program_of("l1")

        assert found is not None
        self.assertIsNone(found["plugins"])

    def test_the_program_of_a_table_nobody_made_is_refused(self) -> None:
        with self.assertRaises(service_errors.NotFoundError):
            launcher_ops.table_program("nope")

    def test_a_table_nobody_made_is_refused(self) -> None:
        with self.assertRaises(service_errors.NotFoundError):
            launcher_ops.write_table_settings("nope", {GRILL: "1"})

    def test_a_value_the_cabinet_already_has_makes_no_file(self) -> None:
        launcher_ops.write_table_settings("t1", {GRILL: "0"})

        self.assertFalse((self.folder / "Taxi.ini").exists())


if __name__ == "__main__":
    unittest.main()
