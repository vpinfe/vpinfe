from __future__ import annotations

import configparser
import unittest

from common.devices import device_registry, serving
from frontend import config_api
from manager.kit import api as manager_api
from tests.support.library import TempTree


def _settings(**ports: int) -> configparser.ConfigParser:
    parser = configparser.ConfigParser()
    parser["network"] = {key: str(value) for key, value in ports.items()}
    return parser


class ServingPortsTests(unittest.TestCase):
    def setUp(self) -> None:
        serving.forget()
        self.addCleanup(serving.forget)

    def test_a_port_nothing_here_started_is_the_setting(self) -> None:
        network = serving.network(_settings(http_port=8101, ws_port=8102))

        self.assertEqual((network.http_port, network.ws_port), (8101, 8102))

    def test_a_started_port_outlives_a_new_setting(self) -> None:
        serving.bound("http_port", 8001)

        network = serving.network(_settings(http_port=9001, ws_port=9002))

        self.assertEqual((network.http_port, network.ws_port), (8001, 9002))

    def test_the_manager_and_the_windows_follow_the_started_port(self) -> None:
        """Saving a new port in Settings must not point the open Manager, a theme or a
        device at a port nothing is listening on."""
        serving.bound("http_port", 8765)
        moved = _settings(http_port=9001)

        self.assertEqual(manager_api.local_base_url(), "http://127.0.0.1:8765")
        self.assertEqual(config_api.get_http_port(moved), 8765)
        self.assertEqual(config_api.get_theme_assets_port(moved), 8765)

    def test_the_windows_are_sent_to_the_loopback_listener_while_https_is_on(self) -> None:
        serving.bound("http_port", 8765)
        serving.bound("loopback_port", 8766)

        self.assertEqual(config_api.get_theme_assets_port(_settings()), 8766)


class PublishedPortTests(TempTree):
    def setUp(self) -> None:
        super().setUp()
        serving.forget()
        self.addCleanup(serving.forget)

    def test_the_port_a_run_bound_is_what_a_shell_finds(self) -> None:
        serving.bound("http_port", 8765)

        serving.publish()

        self.assertEqual(serving.published(), 8765)

    def test_it_is_a_field_of_the_install_row_and_no_file_of_its_own(self) -> None:
        serving.bound("http_port", 8765)

        serving.publish()

        self.assertEqual(device_registry.get_device_registry().self_get("port"), 8765)
        self.assertEqual([p.name for p in self.root.iterdir()], [])

    def test_nothing_is_published_before_a_server_has_bound(self) -> None:
        serving.publish()

        self.assertIsNone(serving.published())

    def test_a_port_the_row_does_not_hold_as_a_port_is_no_port(self) -> None:
        device_registry.get_device_registry().set_self(port="not a port")

        self.assertIsNone(serving.published())


if __name__ == "__main__":
    unittest.main()
