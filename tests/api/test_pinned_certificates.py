"""A paired install is met only by the certificate it showed when it was paired.

These run against a real TLS server on this machine, because what is pinned is what a TLS
handshake shows, and a stand-in for the handshake would test the stand-in.
"""

from __future__ import annotations

import http.server
import shutil
import ssl
import tempfile
import threading
import unittest
import warnings
from pathlib import Path
from unittest import mock

import requests

from common import service_errors
from common.config import paths
from common.security import http_client, machine_secrets, pinned_certificates
from tests.api.test_encrypted_connections import make_certificate

HOST = "127.0.0.1"


class _Counting(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.server.asked += 1  # type: ignore[attr-defined]
        self.server.callers.append(self.client_address[1])  # type: ignore[attr-defined]
        body = b'{"ok": true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        pass


class _Quiet(http.server.HTTPServer):
    """A client that walks away mid-handshake is the case under test, not an error."""

    def handle_error(self, request: object, client_address: object) -> None:
        pass


class _Server:
    def __init__(self, cert: str, key: str, port: int = 0) -> None:
        self.httpd = _Quiet((HOST, port), _Counting)
        self.httpd.asked = 0  # type: ignore[attr-defined]
        self.httpd.callers = []  # type: ignore[attr-defined]
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)
        self.httpd.socket = context.wrap_socket(self.httpd.socket, server_side=True)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    @property
    def asked(self) -> int:
        return self.httpd.asked  # type: ignore[attr-defined]

    @property
    def callers(self) -> list[int]:
        return self.httpd.callers  # type: ignore[attr-defined]

    @property
    def url(self) -> str:
        return f"https://{HOST}:{self.port}/api/v1/ping"

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join()


@unittest.skipUnless(shutil.which("openssl"), "needs openssl to make a certificate")
class PinnedCertificateTests(unittest.TestCase):
    def setUp(self) -> None:
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.folder = Path(holder.name)
        patched = mock.patch.object(paths, "SECRETS_PATH", self.folder / "secrets.json")
        patched.start()
        self.addCleanup(patched.stop)
        self.servers: list[_Server] = []
        self.addCleanup(self._stop_servers)

    def _stop_servers(self) -> None:
        for server in self.servers:
            try:
                server.stop()
            except Exception:
                pass

    def serve(self, name: str, port: int = 0) -> _Server:
        (self.folder / name).mkdir(exist_ok=True)
        cert, key = make_certificate(self.folder / name)
        server = _Server(cert, key, port)
        self.servers.append(server)
        return server

    def test_a_self_signed_certificate_is_refused_until_one_is_pinned(self) -> None:
        server = self.serve("one")

        with self.assertRaises(requests.exceptions.SSLError):
            http_client.get_json(server.url, timeout=5)

        self.assertEqual(server.asked, 0)

    def test_pairing_pins_what_the_machine_shows_and_later_calls_meet_it(self) -> None:
        server = self.serve("one")
        shown = pinned_certificates.trust_now(server.url)

        pinned_certificates.adopt(server.url, shown)

        self.assertEqual(pinned_certificates.pinned(HOST, server.port), shown)
        self.assertEqual(http_client.get_json(server.url, timeout=5), {"ok": True})

    def test_each_call_opens_its_own_connection(self) -> None:
        server = self.serve("one")
        pinned_certificates.adopt(server.url, pinned_certificates.trust_now(server.url))

        http_client.get_json(server.url, timeout=5)
        http_client.get_json(server.url, timeout=5)

        self.assertEqual(len(set(server.callers)), 2)

    def test_a_different_certificate_at_the_same_address_is_refused_before_anything_is_sent(
            self) -> None:
        first = self.serve("one")
        pinned_certificates.adopt(first.url, pinned_certificates.trust_now(first.url))
        port = first.port
        first.stop()
        second = self.serve("two", port)

        with self.assertRaises(service_errors.CertificateChangedError) as raised:
            http_client.get_json(second.url, timeout=5)

        self.assertIn(HOST, str(raised.exception))
        self.assertEqual(second.asked, 0)

    def test_pairing_again_trusts_the_new_certificate(self) -> None:
        first = self.serve("one")
        pinned_certificates.adopt(first.url, pinned_certificates.trust_now(first.url))
        port = first.port
        first.stop()
        second = self.serve("two", port)

        pinned_certificates.adopt(second.url, pinned_certificates.trust_now(second.url))

        self.assertEqual(http_client.get_json(second.url, timeout=5), {"ok": True})

    def test_a_pin_belongs_to_one_address_and_port(self) -> None:
        pinned_certificates.keep(HOST, 8001, "ab" * 32)

        self.assertEqual(pinned_certificates.pinned(HOST, 8001), "ab" * 32)
        self.assertEqual(pinned_certificates.pinned(HOST, 8002), "")
        pinned_certificates.forget(HOST, 8001)
        self.assertEqual(pinned_certificates.pinned(HOST, 8001), "")

    def test_a_call_that_carries_no_secret_may_look_without_pinning(self) -> None:
        server = self.serve("one")

        with warnings.catch_warnings():
            warnings.simplefilter("error")
            answered = http_client.get_json(
                server.url, timeout=5, trust=pinned_certificates.UNVERIFIED)

        self.assertEqual(answered, {"ok": True})
        self.assertEqual(pinned_certificates.pinned(HOST, server.port), "")

    def test_a_call_with_no_trust_given_meets_the_pin_that_is_kept(self) -> None:
        server = self.serve("one")
        pinned_certificates.keep(HOST, server.port, "00" * 32)

        with self.assertRaises(service_errors.CertificateChangedError):
            http_client.get_json(server.url, timeout=5)

    def test_a_certificate_an_authority_vouches_for_is_not_pinned_and_forgets_an_old_pin(
            self) -> None:
        server = self.serve("one")
        pinned_certificates.keep(HOST, server.port, "00" * 32)

        with mock.patch.object(pinned_certificates, "vouched_for", return_value=True):
            trust = pinned_certificates.trust_now(server.url)

        self.assertEqual(trust, pinned_certificates.VERIFIED)
        pinned_certificates.adopt(server.url, trust)
        self.assertEqual(pinned_certificates.pinned(HOST, server.port), "")

    def test_an_install_outside_this_machine_is_not_met_while_offline(self) -> None:
        with mock.patch.object(pinned_certificates.socket, "create_connection") as opened, \
                self.assertRaises(http_client.OfflineError):
            pinned_certificates.trust_now("https://192.168.1.40:8001")

        opened.assert_not_called()

    def test_a_proxy_or_ca_bundle_in_the_environment_does_not_reach_a_pinned_session(
            self) -> None:
        session = pinned_certificates._session("ab" * 32)

        self.assertFalse(session.trust_env)
        self.assertFalse(session.verify)

    def test_two_pins_kept_together_both_survive(self) -> None:
        pinned_certificates.keep(HOST, 8001, "ab" * 32)
        pinned_certificates.keep(HOST, 8002, "cd" * 32)
        pinned_certificates.forget(HOST, 8001)

        self.assertEqual(pinned_certificates.pinned(HOST, 8001), "")
        self.assertEqual(pinned_certificates.pinned(HOST, 8002), "cd" * 32)

    def test_plain_http_has_nothing_to_pin(self) -> None:
        self.assertEqual(pinned_certificates.trust_now("http://192.168.1.50:8001"), "")
        pinned_certificates.adopt("http://192.168.1.50:8001", "ab" * 32)
        self.assertEqual(machine_secrets.read().get(pinned_certificates.PINNED), None)


class FingerprintShapeTests(unittest.TestCase):
    def test_an_address_that_answers_no_tls_is_an_oserror_not_a_pin(self) -> None:
        with mock.patch.object(pinned_certificates.socket, "create_connection",
                               side_effect=ConnectionRefusedError()):
            with self.assertRaises(OSError):
                pinned_certificates.shown_by(HOST, 1)

    def test_a_host_name_is_sent_in_the_handshake_and_an_address_is_not(self) -> None:
        self.assertEqual(pinned_certificates._name_to_send("cab.local"), "cab.local")
        self.assertIsNone(pinned_certificates._name_to_send("192.168.1.50"))
        self.assertIsNone(pinned_certificates._name_to_send("fe80::1"))

    def test_split_reads_the_host_and_default_port_of_an_https_url_only(self) -> None:
        self.assertEqual(pinned_certificates.split("https://cab.local/x"), ("cab.local", 443))
        self.assertEqual(pinned_certificates.split("https://10.0.0.5:8001/x"),
                         ("10.0.0.5", 8001))
        self.assertIsNone(pinned_certificates.split("http://10.0.0.5:8001/x"))


if __name__ == "__main__":
    unittest.main()
