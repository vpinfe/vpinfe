"""The web server reaches the socket the way it was told to, and says so in one line.

The bind setting is only worth having if it arrives: `NetworkConfig` reading it correctly
and `ui.run` still being handed a hardcoded host would look right everywhere except on the
socket, and the default is the value that hides that. NiceGUI prints its own welcome
message unless told not to, and the two together would put two lines on the terminal, one
of them naming a library the person never chose.
"""

from __future__ import annotations

import contextlib
import io
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from common.devices import serving
from common.security import encrypted_connections
from manager import host, loopback
from tests.api.test_encrypted_connections import make_certificate


def _printed(urls: set[str]) -> str:
    out = io.StringIO()
    with mock.patch.object(host, "app", SimpleNamespace(urls=urls)), \
         contextlib.redirect_stdout(out):
        host._say_ready()
    return out.getvalue()


class TheReadyLineTests(unittest.TestCase):
    def test_it_lists_localhost_first_and_then_the_other_addresses(self) -> None:
        line = _printed({"http://192.168.1.50:8001", "http://localhost:8001",
                         "http://10.0.0.7:8001"})

        self.assertEqual(
            line,
            "VPinFE ready to go on http://localhost:8001, http://10.0.0.7:8001, "
            "and http://192.168.1.50:8001\n")

    def test_two_addresses_read_as_one_and_another(self) -> None:
        line = _printed({"http://localhost:8001", "http://192.168.1.50:8001"})

        self.assertEqual(
            line,
            "VPinFE ready to go on http://localhost:8001, and http://192.168.1.50:8001\n")

    def test_an_install_that_answers_one_address_lists_only_that(self) -> None:
        self.assertEqual(_printed({"http://127.0.0.1:8001"}),
                         "VPinFE ready to go on http://127.0.0.1:8001\n")

    def test_https_addresses_keep_their_scheme(self) -> None:
        self.assertEqual(_printed({"https://localhost:8001"}),
                         "VPinFE ready to go on https://localhost:8001\n")


class StartingTheServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.addCleanup(setattr, host, "_ui_bind", host._ui_bind)
        self.addCleanup(setattr, host, "_ui_port", host._ui_port)

    def _ran_with(self, **kwargs) -> tuple[mock.Mock, mock.Mock]:
        """What `_run_ui` would hand NiceGUI, without starting a server."""
        host._ui_bind = kwargs.get("bind", "0.0.0.0")
        host._ui_port = kwargs.get("port", 8001)
        with mock.patch.object(host, "ui") as nicegui, \
             mock.patch.object(host, "app") as app, \
             mock.patch.object(host.machine_secrets, "session_secret", return_value="s"), \
             mock.patch.object(host, "_server_urls", return_value=[]):
            host._run_ui()
        return nicegui, app

    def test_it_answers_every_interface_by_default(self) -> None:
        """What it has always done: a cabinet is administered from another machine."""
        nicegui, _ = self._ran_with()

        self.assertEqual(nicegui.run.call_args.kwargs["host"], "0.0.0.0")

    def test_an_install_can_pull_it_back_to_this_machine(self) -> None:
        nicegui, _ = self._ran_with(bind="127.0.0.1")

        self.assertEqual(nicegui.run.call_args.kwargs["host"], "127.0.0.1")

    def test_the_port_still_arrives_with_it(self) -> None:
        nicegui, _ = self._ran_with(bind="127.0.0.1", port=9001)

        ran = nicegui.run.call_args.kwargs
        self.assertEqual((ran["host"], ran["port"]), ("127.0.0.1", 9001))

    def test_a_forwarding_header_is_never_rewritten_into_the_peer(self) -> None:
        """The listed-proxy check reads the real socket, so uvicorn must leave it alone."""
        nicegui, _ = self._ran_with()

        self.assertIs(nicegui.run.call_args.kwargs["proxy_headers"], False)

    def test_nicegui_is_told_to_keep_its_own_message_to_itself(self) -> None:
        nicegui, _ = self._ran_with()

        self.assertIs(nicegui.run.call_args.kwargs["show_welcome_message"], False)

    def test_the_ready_line_runs_when_the_server_starts(self) -> None:
        _, app = self._ran_with()

        app.on_startup.assert_any_call(host._say_ready)


class TheServerAndHttpsTests(unittest.TestCase):
    holder: tempfile.TemporaryDirectory[str]
    cert: str
    key: str

    @classmethod
    def setUpClass(cls) -> None:
        if shutil.which("openssl") is None:
            raise unittest.SkipTest("openssl is needed to make a certificate")
        cls.holder = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.holder.cleanup)
        cls.cert, cls.key = make_certificate(Path(cls.holder.name))

    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.connections = encrypted_connections.Connections(Path(folder.name) / "c.json")
        self.addCleanup(encrypted_connections.use, encrypted_connections.instance())
        encrypted_connections.use(self.connections)
        serving.forget()
        encrypted_connections.forget_listening()
        self.addCleanup(serving.forget)
        self.addCleanup(encrypted_connections.forget_listening)
        self.addCleanup(setattr, host, "_tls", host._tls)

    def test_no_certificate_means_plain_http_and_no_second_listener(self) -> None:
        self.assertEqual(host._certificate_to_serve(), {})
        self.assertFalse(encrypted_connections.listening().https)
        self.assertEqual(encrypted_connections.listening().problem, "")

    def test_a_certificate_that_loads_is_served(self) -> None:
        self.connections.set_certificate(self.cert, self.key)

        self.assertEqual(host._certificate_to_serve(),
                         {"ssl_certfile": self.cert, "ssl_keyfile": self.key})
        self.assertTrue(encrypted_connections.listening().https)

    def test_a_certificate_gone_since_it_was_chosen_starts_plain_http_and_says_why(self) -> None:
        self.connections.set_certificate(self.cert, self.key)
        with mock.patch.object(encrypted_connections, "unreadable_reason",
                               return_value=encrypted_connections.NO_FILE):
            self.assertEqual(host._certificate_to_serve(), {})

        self.assertFalse(encrypted_connections.listening().https)
        self.assertEqual(encrypted_connections.listening().problem, encrypted_connections.NO_FILE)

    def test_https_gives_this_devices_own_windows_a_port_of_their_own(self) -> None:
        self.connections.set_certificate(self.cert, self.key)
        with mock.patch.object(host, "_ui_thread", None), \
             mock.patch.object(host.threading, "Thread"), \
             mock.patch.object(loopback, "serve_on_startup") as serve:
            host.start_server(port=8765)
        sock = serve.call_args.args[0]
        self.addCleanup(sock.close)

        own = serving.bound_port("loopback_port")
        self.assertEqual(sock.getsockname(), ("127.0.0.1", own))
        self.assertNotEqual(own, 8765)
        self.assertEqual(serving.bound_port("http_port"), 8765)
        self.assertEqual(serving.loopback_or(8765), own)

    def test_plain_http_has_no_loopback_port(self) -> None:
        with mock.patch.object(host, "_ui_thread", None), \
             mock.patch.object(host.threading, "Thread"), \
             mock.patch.object(loopback, "serve_on_startup") as serve:
            host.start_server(port=8765)

        serve.assert_not_called()
        self.assertIsNone(serving.bound_port("loopback_port"))


if __name__ == "__main__":
    unittest.main()
