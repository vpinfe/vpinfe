"""A port setting is checked when it is saved: it is in range, nothing else is on it, and
no other port setting holds the same number. A refusal writes nothing."""

from __future__ import annotations

import socket
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

import httpapi
from common.config import paths
from common.config.config_store import ConfigStore
from common.devices import serving
from common.i18n import t
from common.settings import config_service, port_checks

_real_listening = port_checks.listening
HTTP = "network.http_port"
WS = "network.ws_port"


class PortCheckTests(unittest.TestCase):
    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.ini = Path(folder.name) / "vpinfe.ini"
        self.enterContext(mock.patch.object(paths, "VPINFE_INI_PATH", self.ini))
        serving.forget()
        self.addCleanup(serving.forget)
        # Not this machine's: a running VPinFE is on 8001 while the suite runs.
        self.taken: set[int] = set()
        self.enterContext(mock.patch.object(
            port_checks, "listening", side_effect=lambda port: port in self.taken))

    def _listening(self) -> int:
        port = 40000
        self.taken.add(port)
        return port

    def _store(self) -> ConfigStore:
        return ConfigStore(str(self.ini))

    def test_a_port_outside_the_range_is_refused(self) -> None:
        for said in (0, 65536, -1, "", "eighty"):
            with self.subTest(said=said):
                self.assertEqual(
                    port_checks.refused({"network": {"http_port": said}}),
                    {HTTP: t("error.config.port_range", low=1, high=65535)})

    def test_a_port_something_is_listening_on_is_refused(self) -> None:
        taken = self._listening()

        self.assertEqual(port_checks.refused({"network": {"http_port": taken}}),
                         {HTTP: t("error.config.port_taken", port=taken)})

    def test_a_real_listener_is_found(self) -> None:
        held = socket.socket()
        self.addCleanup(held.close)
        held.bind(("127.0.0.1", 0))
        held.listen()
        port = int(held.getsockname()[1])
        self.enterContext(mock.patch.object(port_checks, "listening",
                                            new=_real_listening))

        self.assertTrue(port_checks.listening(port))

    def test_a_port_this_run_serves_is_not_in_use(self) -> None:
        mine = self._listening()
        serving.bound("ws_port", mine)

        self.assertEqual(port_checks.refused({"network": {"http_port": mine}}), {})

    def test_two_port_settings_cannot_hold_one_number(self) -> None:
        self.assertEqual(
            port_checks.refused({"network": {"ws_port": 8001}}),
            {WS: t("error.config.port_clash", port=8001,
                   setting=t("config.network.http_port.label"))})

    def test_a_patch_that_moves_both_is_checked_as_it_will_stand(self) -> None:
        self.assertEqual(
            port_checks.refused({"network": {"http_port": 9001, "ws_port": 8001}}), {})

    def test_a_value_that_is_not_changing_is_not_checked(self) -> None:
        taken = self._listening()
        store = self._store()
        store.set_value("network", "http_port", taken)
        store.save()

        self.assertEqual(port_checks.refused({"network": {"http_port": taken}}), {})

    def test_anything_that_is_not_a_port_is_left_alone(self) -> None:
        self.assertEqual(port_checks.refused({"behavior": {"paging_size": 0}}), {})

    def test_a_refused_write_changes_nothing(self) -> None:
        with self.assertRaises(config_service.RefusedSettingsError) as caught:
            config_service.set_values({"network": {"http_port": 70000, "ws_port": 9100}})

        self.assertEqual(list(caught.exception.reasons), [HTTP])
        self.assertEqual(self._store().value("network", "ws_port"), 8002)

    def test_the_api_says_which_setting_it_refused(self) -> None:
        client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)

        answer = client.put("/config", json={"settings": {"network": {"http_port": 70000}}})

        self.assertEqual(answer.status_code, 400)
        error = answer.json()["error"]
        self.assertEqual(error["details"], {"settings": {
            HTTP: t("error.config.port_range", low=1, high=65535)}})
        self.assertEqual(error["message"], t("error.config.port_range", low=1, high=65535))


if __name__ == "__main__":
    unittest.main()
