"""A launcher's settings write asks the disk about the file and its folder first, and a
refusal comes as a blocked write in the shared words rather than an errno."""

from __future__ import annotations

import pathlib
import unittest
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi.testclient import TestClient

import httpapi
from common import service_errors
from common.config import path_state
from common.games import launcher_ops, launchers
from common.i18n import t
from tests.support.hung_folder import never_answering
from tests.support.launchers_api import put_launcher
from tests.support.library import own_launchers
from tests.support.skips import needs_posix_permissions


class LauncherFileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = pathlib.Path(self.tmp.name, "data")
        self.store = own_launchers(self, self.tmp.name)
        for patcher in (patch.object(launchers, "get_launcher_store", return_value=self.store),
                        patch("apps.vpx.config._machine_folders",
                              return_value=(self.data / "VPinballX", None)),
                        patch.object(path_state, "PROBE_SECONDS", 0.2)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.store.save([], {})
        self.client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)
        put_launcher(self.client, "l1", json={"app": "vpx", "settings": {"bin_path": "/opt/vpx"}})
        self.file = self.data / "VPinballX" / "10.8" / "VPinballX.ini"
        self.made = self.put(create=True)
        self.before = self.file.read_bytes()

    def put(self, **body):
        return self.client.put("/launchers/l1/config",
                               json={"scope": "launcher", "values": {"Player.BGSet": "2"},
                                     **body})

    def test_the_file_was_made(self) -> None:
        self.assertEqual(self.made.status_code, 200, self.made.text)

    @needs_posix_permissions
    def test_a_read_only_file_is_refused_and_left_as_it_was(self) -> None:
        self.file.chmod(0o444)
        self.addCleanup(self.file.chmod, 0o644)
        got = self.put()
        self.assertEqual(got.status_code, 409, got.text)
        self.assertIn(t("error.path.read_only", name=self.file.name), got.text)
        self.assertEqual(self.file.read_bytes(), self.before)

    def test_a_file_that_does_not_answer_is_refused_without_waiting_on_it(self) -> None:
        with never_answering(str(self.file)):
            got = self.put()
        self.assertEqual(got.status_code, 409, got.text)
        self.assertEqual(self.file.read_bytes(), self.before)


class TableFolderTests(unittest.TestCase):
    def test_a_table_folder_that_does_not_answer_is_refused(self) -> None:
        with TemporaryDirectory() as tmp, \
                patch.object(path_state, "PROBE_SECONDS", 0.2), \
                never_answering(tmp), \
                self.assertRaises(service_errors.BlockedError):
            launcher_ops._writable_before_writing(None, "entry",  # type: ignore[arg-type]
                                                  str(pathlib.Path(tmp) / "t.vpx"))

    def test_no_table_named_asks_about_nothing(self) -> None:
        with patch.object(path_state, "look") as looked:
            launcher_ops._writable_before_writing(None, "entry", "")  # type: ignore[arg-type]
        looked.assert_not_called()


if __name__ == "__main__":
    unittest.main()
