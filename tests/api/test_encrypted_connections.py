"""Trusted Proxies, Trusted Networks and the certificate VPinFE serves HTTPS with.

A listed range or a proxy's HTTPS claim spares an Owner the Confirm and nothing else: a
token, a Remote pass and a stranger hold what they held, and no token edits either list.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from starlette.datastructures import Headers
from starlette.testclient import TestClient

import httpapi
from common.devices import serving
from common.host import addresses, this_machine
from common.security import api_tokens, encrypted_connections, sign_in
from httpapi import auth, scopes
from httpapi.encrypted import _held
from tests.support.skips import needs_posix_permissions

Refused = encrypted_connections.EntryError

CONFIG = """[req]
distinguished_name = dn
x509_extensions = ext
prompt = no
[dn]
CN = cab.example.test
[ext]
subjectAltName = DNS:cab.example.test,DNS:*.wild.example.test,IP:100.101.102.103
"""


def make_certificate(folder: Path) -> tuple[str, str]:
    (folder / "cert.cnf").write_text(CONFIG)
    cert, key = folder / "cert.pem", folder / "key.pem"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2",
                    "-keyout", str(key), "-out", str(cert), "-config", str(folder / "cert.cnf")],
                   check=True, capture_output=True)
    return str(cert), str(key)


def _request(peer, cookies=None, method="GET", scheme="http", **headers):
    client = SimpleNamespace(host=peer) if peer is not None else None
    return SimpleNamespace(client=client, headers=headers, cookies=cookies or {}, method=method,
                           url=SimpleNamespace(scheme=scheme))


class Clock:
    def __init__(self) -> None:
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class ConnectionsCase(unittest.TestCase):
    def setUp(self) -> None:
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.root = Path(holder.name)
        self.clock = Clock()
        self.signer = sign_in.SignIn(self.root / "sessions.json", self.clock)
        self.addCleanup(self.signer.wait_for_saves)
        self.tokens = api_tokens.Tokens(self.root / "tokens.json", self.clock)
        self.connections = encrypted_connections.Connections(self.root / "connections.json")
        for module, previous in ((sign_in, sign_in.instance()),
                                 (api_tokens, api_tokens.instance()),
                                 (encrypted_connections, encrypted_connections.instance())):
            self.addCleanup(module.use, previous)
        sign_in.use(self.signer)
        api_tokens.use(self.tokens)
        encrypted_connections.use(self.connections)
        serving.forget()
        encrypted_connections.forget_listening()
        self.addCleanup(serving.forget)
        self.addCleanup(encrypted_connections.forget_listening)
        self.policy = auth.SignInPolicy()

    def owner_cookie(self, address="192.168.1.20"):
        token, session = self.signer.open(kind=sign_in.OWNER, address=address)
        self.clock.advance(sign_in.CONFIRM_SECONDS + 1)
        return {auth.COOKIE: token}, session

    def identify(self, peer="192.168.1.20", cookies=None, scheme="http", **headers):
        return self.policy.identify(_request(peer, cookies, scheme=scheme, **headers))


class TheListsTests(ConnectionsCase):
    def test_an_address_is_kept_in_one_spelling(self) -> None:
        self.assertEqual(self.connections.add_proxy("::ffff:10.0.0.5"), "10.0.0.5")
        self.connections.add_proxy("10.0.0.5")

        self.assertEqual(self.connections.proxies(), ["10.0.0.5"])
        self.assertTrue(self.connections.is_proxy("::ffff:10.0.0.5"))

    def test_what_is_not_an_address_is_refused_with_a_reason(self) -> None:
        for text in ("proxy.example", "0.0.0.0", "224.0.0.1", "10.0.0.0/24", ""):
            with self.subTest(text=text), self.assertRaises(Refused) as raised:
                self.connections.add_proxy(text)
            self.assertEqual(raised.exception.reason, encrypted_connections.BAD_ADDRESS)

    def test_a_range_that_is_every_address_is_too_wide(self) -> None:
        for text in ("0.0.0.0/0", "::/0"):
            with self.subTest(text=text), self.assertRaises(Refused) as raised:
                self.connections.add_network(text)
            self.assertEqual(raised.exception.reason, encrypted_connections.TOO_WIDE)

    def test_a_mapped_range_that_is_every_address_is_too_wide(self) -> None:
        for text in ("::ffff:0:0/96", "::ffff:0.0.0.0/96"):
            with self.subTest(text=text), self.assertRaises(Refused) as raised:
                self.connections.add_network(text)
            self.assertEqual(raised.exception.reason, encrypted_connections.TOO_WIDE)
        self.assertEqual(self.connections.networks(), [])

    def test_a_mapped_range_is_kept_as_the_address_range_it_means(self) -> None:
        self.assertEqual(self.connections.add_network("::ffff:10.0.0.0/120"), "10.0.0.0/24")

    def test_a_typed_range_wider_than_a_subnet_is_refused(self) -> None:
        for text in ("10.0.0.0/23", "10.0.0.0/8", "0.0.0.0/1", "2001:db8::/48", "fd00::/8",
                     "::ffff:10.0.0.0/104"):
            with self.subTest(text=text), self.assertRaises(Refused) as raised:
                self.connections.add_network(text)
            self.assertEqual(raised.exception.reason, encrypted_connections.TOO_WIDE)
        self.assertEqual(self.connections.networks(), [])

    def test_a_subnet_and_anything_narrower_is_kept(self) -> None:
        for text in ("10.0.0.0/24", "10.0.1.0/25", "192.168.7.5", "2001:db8::/64",
                     "2001:db8:0:1::/96"):
            with self.subTest(text=text):
                self.connections.add_network(text)

        self.assertEqual(self.connections.networks(),
                         ["10.0.0.0/24", "10.0.1.0/25", "192.168.7.5/32", "2001:db8::/64",
                          "2001:db8:0:1::/96"])

    def test_a_range_that_is_not_one_is_refused(self) -> None:
        with self.assertRaises(encrypted_connections.EntryError) as raised:
            self.connections.add_network("not a range")

        self.assertEqual(raised.exception.reason, encrypted_connections.BAD_RANGE)

    def test_tailscale_is_one_click_and_covers_its_whole_range(self) -> None:
        self.connections.add_tailscale()
        self.connections.add_tailscale()

        self.assertEqual(self.connections.networks(), [encrypted_connections.TAILSCALE])
        self.assertTrue(self.connections.in_encrypted_network("100.101.102.103"))
        self.assertTrue(self.connections.in_encrypted_network("::ffff:100.64.0.1"))
        self.assertFalse(self.connections.in_encrypted_network("100.128.0.1"))
        self.assertFalse(self.connections.in_encrypted_network("192.168.1.20"))
        self.assertFalse(self.connections.in_encrypted_network(None))

    def test_tailscale_is_the_one_range_wider_than_a_subnet(self) -> None:
        with self.assertRaises(Refused) as raised:
            self.connections.add_network(encrypted_connections.TAILSCALE)

        self.assertEqual(raised.exception.reason, encrypted_connections.TOO_WIDE)
        self.assertEqual(self.connections.networks(), [])

    def test_tailscale_is_removed_like_any_other_network(self) -> None:
        self.connections.add_tailscale()
        self.connections.add_network("10.8.0.0/24")

        self.assertTrue(self.connections.remove_network(encrypted_connections.TAILSCALE))
        self.assertEqual(self.connections.networks(), ["10.8.0.0/24"])
        self.assertFalse(self.connections.remove_network("10.0.0.0/8"))

    def test_the_lists_survive_a_restart_and_come_out_in_the_same_form(self) -> None:
        self.connections.add_proxy("10.0.0.5")
        self.connections.add_network("192.168.7.9/24")

        again = encrypted_connections.Connections(self.root / "connections.json")

        self.assertEqual(again.proxies(), ["10.0.0.5"])
        self.assertEqual(again.networks(), ["192.168.7.0/24"])

    def test_tailscale_survives_a_restart_and_the_next_save(self) -> None:
        self.connections.add_tailscale()

        again = encrypted_connections.Connections(self.root / "connections.json")
        again.add_proxy("10.0.0.5")
        later = encrypted_connections.Connections(self.root / "connections.json")

        self.assertEqual(again.networks(), [encrypted_connections.TAILSCALE])
        self.assertEqual(later.networks(), [encrypted_connections.TAILSCALE])
        self.assertTrue(later.in_encrypted_network("100.100.1.1"))

    def test_an_entry_can_be_removed_in_any_spelling(self) -> None:
        self.connections.add_proxy("10.0.0.5")
        self.connections.add_network("192.168.7.0/24")

        self.assertTrue(self.connections.remove_proxy("::ffff:10.0.0.5"))
        self.assertTrue(self.connections.remove_network("192.168.7.9/24"))
        self.assertFalse(self.connections.remove_proxy("10.0.0.5"))
        self.assertEqual((self.connections.proxies(), self.connections.networks()), ([], []))

    @needs_posix_permissions
    def test_the_file_is_private_to_the_owner_of_this_device(self) -> None:
        self.connections.add_proxy("10.0.0.5")

        mode = (self.root / "connections.json").stat().st_mode & 0o777

        self.assertEqual(mode, 0o600)


class WhatCountsAsEncryptedTests(ConnectionsCase):
    def test_a_plain_connection_from_anywhere_is_not(self) -> None:
        self.assertFalse(encrypted_connections.is_encrypted("192.168.1.20", {}))

    def test_this_servers_own_tls_is(self) -> None:
        self.assertTrue(encrypted_connections.is_encrypted("192.168.1.20", {}, "https"))

    def test_a_listed_proxy_that_says_https_is(self) -> None:
        self.connections.add_proxy("10.0.0.5")

        self.assertTrue(encrypted_connections.is_encrypted(
            "10.0.0.5", {"x-forwarded-proto": "https"}))
        self.assertFalse(encrypted_connections.is_encrypted(
            "10.0.0.5", {"x-forwarded-proto": "http"}))

    def test_a_header_from_anyone_else_counts_for_nothing(self) -> None:
        self.connections.add_proxy("10.0.0.5")

        self.assertFalse(encrypted_connections.is_encrypted(
            "192.168.1.20", {"x-forwarded-proto": "https"}))

    def test_an_address_inside_a_listed_network_is_whatever_it_says(self) -> None:
        self.connections.add_tailscale()

        self.assertTrue(encrypted_connections.is_encrypted("100.100.1.2", {}))
        self.assertTrue(encrypted_connections.is_encrypted(
            "100.100.1.2", {"x-forwarded-proto": "http"}))

    def test_the_client_behind_a_proxy_is_read_from_the_right(self) -> None:
        self.connections.add_proxy("10.0.0.5")
        self.connections.add_proxy("10.0.0.6")
        spoofed = {"x-forwarded-for": "6.6.6.6, 192.168.1.40, 10.0.0.6"}

        self.assertEqual(encrypted_connections.forwarded_for("10.0.0.5", spoofed), "192.168.1.40")

    def test_a_listed_proxy_inside_a_listed_network_says_https_or_is_not_encrypted(self) -> None:
        self.connections.add_proxy("100.100.1.2")
        self.connections.add_tailscale()

        self.assertFalse(encrypted_connections.is_encrypted("100.100.1.2", {}))
        self.assertTrue(encrypted_connections.is_encrypted(
            "100.100.1.2", {"x-forwarded-proto": "https"}))

    def test_repeated_headers_are_read_as_one_list_from_the_right(self) -> None:
        self.connections.add_proxy("10.0.0.5")
        sent = Headers(raw=[(b"x-forwarded-for", b"6.6.6.6"),
                            (b"x-forwarded-for", b"192.168.1.40"),
                            (b"x-forwarded-proto", b"https"),
                            (b"x-forwarded-proto", b"http")])

        self.assertEqual(encrypted_connections.forwarded_for("10.0.0.5", sent), "192.168.1.40")
        self.assertFalse(encrypted_connections.said_https("10.0.0.5", sent))

    def test_a_proxy_adding_its_own_https_line_after_a_forged_one_is_believed(self) -> None:
        self.connections.add_proxy("10.0.0.5")
        sent = Headers(raw=[(b"x-forwarded-proto", b"http"), (b"x-forwarded-proto", b"https")])

        self.assertTrue(encrypted_connections.said_https("10.0.0.5", sent))

    def test_a_header_from_a_peer_that_is_not_a_proxy_is_not_read(self) -> None:
        self.assertEqual(encrypted_connections.forwarded_for(
            "192.168.1.20", {"x-forwarded-for": "6.6.6.6"}), "192.168.1.20")


class WhatAnEncryptedConnectionSparesTests(ConnectionsCase):
    def test_an_owner_inside_a_listed_network_needs_no_confirm(self) -> None:
        self.connections.add_tailscale()
        cookies, _ = self.owner_cookie("100.100.1.2")

        identity = self.identify("100.100.1.2", cookies)

        self.assertTrue(identity.can(scopes.PROTECTED))
        self.assertTrue(identity.can(scopes.SYSTEM_ADMIN))

    def test_the_same_owner_outside_it_is_asked_to_confirm(self) -> None:
        self.connections.add_tailscale()
        cookies, _ = self.owner_cookie()

        identity = self.identify("192.168.1.20", cookies)

        self.assertTrue(identity.can(scopes.CONFIG_WRITE))
        self.assertFalse(identity.can(scopes.PROTECTED))

    def test_an_owner_behind_a_proxy_that_says_https_holds_it_too(self) -> None:
        self.connections.add_proxy("10.0.0.5")
        cookies, _ = self.owner_cookie()

        said_https = self.identify("10.0.0.5", cookies, **{"x-forwarded-proto": "https"})
        said_http = self.identify("10.0.0.5", cookies, **{"x-forwarded-proto": "http"})

        self.assertTrue(said_https.can(scopes.PROTECTED))
        self.assertFalse(said_http.can(scopes.PROTECTED))

    def test_an_owner_over_this_servers_own_tls_holds_it(self) -> None:
        cookies, _ = self.owner_cookie()

        identity = self.identify("192.168.1.20", cookies, scheme="https")

        self.assertTrue(identity.can(scopes.PROTECTED))

    def test_a_remote_pass_inside_a_listed_network_holds_play_and_nothing_more(self) -> None:
        self.connections.add_tailscale()
        token, _ = self.signer.open(kind=sign_in.REMOTE, address="100.100.1.3")

        identity = self.identify("100.100.1.3", {auth.COOKIE: token})

        self.assertEqual(identity.scopes, scopes.PLAY)

    def test_a_token_inside_a_listed_network_never_reaches_the_protected_tier(self) -> None:
        self.connections.add_tailscale()
        secret, _ = self.tokens.make("home", api_tokens.FULL, can_power=True)

        identity = self.identify("100.100.1.2", authorization="Bearer " + secret)

        self.assertTrue(identity.can(scopes.CONFIG_WRITE))
        self.assertFalse(identity.can(scopes.PROTECTED))

    def test_a_stranger_inside_a_listed_network_is_still_a_stranger(self) -> None:
        self.connections.add_tailscale()

        identity = self.identify("100.100.1.2")

        self.assertEqual(identity.scopes, frozenset({scopes.INSTANCE_READ}))

    def test_a_manager_call_carries_whether_the_browser_it_acts_for_was_encrypted(self) -> None:
        _, session = self.owner_cookie()

        plain = self.identify("127.0.0.1", **{auth.ACTING_HEADER: auth.sign_acting(session.id)})
        encrypted = self.identify("127.0.0.1", **{
            auth.ACTING_HEADER: auth.sign_acting(session.id, encrypted=True)})

        self.assertFalse(plain.can(scopes.PROTECTED))
        self.assertTrue(encrypted.can(scopes.PROTECTED))

    def test_the_flag_cannot_be_flipped_without_the_secret(self) -> None:
        _, session = self.owner_cookie()
        header = auth.sign_acting(session.id)
        session_id, expires, _flag, mac = header.split(".")

        identity = self.identify("127.0.0.1", **{
            auth.ACTING_HEADER: f"{session_id}.{expires}.1.{mac}"})

        self.assertFalse(identity.can(scopes.PROTECTED))


class AProxyOnThisDeviceTests(ConnectionsCase):
    """With HTTPS on, this device's own windows have a port of their own, so a request
    from a listed proxy on this device to any other port is not one of them."""

    def test_a_listed_proxy_on_this_device_is_not_this_device(self) -> None:
        serving.bound("loopback_port", 54321)
        self.connections.add_proxy("127.0.0.1")

        self.assertFalse(this_machine.request_is_this_machine("127.0.0.1", {}, 8001))

    def test_the_devices_own_windows_still_are(self) -> None:
        serving.bound("loopback_port", 54321)
        self.connections.add_proxy("127.0.0.1")

        self.assertTrue(this_machine.request_is_this_machine("127.0.0.1", {}, 54321))

    def test_an_unlisted_loopback_peer_on_the_network_port_is_this_device(self) -> None:
        serving.bound("loopback_port", 54321)

        self.assertTrue(this_machine.request_is_this_machine("127.0.0.1", {}, 8001))

    def test_without_https_the_list_changes_nothing_here(self) -> None:
        self.connections.add_proxy("127.0.0.1")

        self.assertTrue(this_machine.request_is_this_machine("127.0.0.1", {}, 8001))

    def test_a_forwarding_header_still_means_it_is_not(self) -> None:
        self.assertFalse(this_machine.request_is_this_machine(
            "127.0.0.1", {"X-Forwarded-For": "203.0.113.9"}, 8001))


class TheEditsAreProtectedTests(ConnectionsCase):
    def call(self, method, path, peer, *, cookies=None, json=None, route="/auth/encrypted",
             **headers):
        client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False,
                            client=(peer, 50000), base_url="http://" + peer)
        for name, value in (cookies or {}).items():
            client.cookies.set(name, value)
        return client.request(method, route + path, json=json, headers=headers)

    def test_this_device_adds_and_removes(self) -> None:
        added = self.call("POST", "/proxies", "127.0.0.1", json={"value": "10.0.0.5"})
        tailscale = self.call("POST", "/networks/tailscale", "127.0.0.1")
        removed = self.call("DELETE", "/proxies?address=10.0.0.5", "127.0.0.1")

        self.assertEqual(added.status_code, 200)
        self.assertEqual(tailscale.json()["networks"], [encrypted_connections.TAILSCALE])
        self.assertEqual(removed.json()["proxies"], [])

    def test_a_bad_entry_is_refused_in_words(self) -> None:
        response = self.call("POST", "/networks", "127.0.0.1", json={"value": "0.0.0.0/0"})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.connections.networks(), [])

    def test_a_range_wider_than_a_subnet_is_refused_in_words(self) -> None:
        response = self.call("POST", "/networks", "127.0.0.1", json={"value": "10.0.0.0/8"})

        self.assertEqual(response.status_code, 400)
        self.assertIn("/24", response.json()["error"]["message"])
        self.assertEqual(self.connections.networks(), [])

    def test_no_token_edits_either_list_or_the_certificate(self) -> None:
        secret, _ = self.tokens.make("home", api_tokens.FULL, can_power=True)
        attempts = (("POST", "/proxies", {"value": "10.0.0.5"}),
                    ("POST", "/networks", {"value": "192.168.7.0/24"}),
                    ("POST", "/networks/tailscale", None),
                    ("DELETE", "/proxies?address=10.0.0.5", None),
                    ("PUT", "/certificate", {"certfile": "a", "keyfile": "b"}),
                    ("DELETE", "/certificate", None))
        for method, path, body in attempts:
            with self.subTest(method=method, path=path):
                response = self.call(method, path, "192.168.1.50", json=body,
                                     authorization="Bearer " + secret)

                self.assertEqual(response.status_code, 403)
        self.assertEqual((self.connections.proxies(), self.connections.networks()), ([], []))

    def test_an_unconfirmed_owner_is_asked_to_confirm_first(self) -> None:
        cookies, _ = self.owner_cookie()

        response = self.call("POST", "/networks/tailscale", "192.168.1.50", cookies=cookies)

        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.connections.networks(), [])

    def test_the_lists_are_read_by_this_device_and_never_by_a_token(self) -> None:
        self.connections.add_proxy("10.0.0.5")
        secret, _ = self.tokens.make("home", api_tokens.FULL)

        by_token = self.call("GET", "", "192.168.1.50", authorization="Bearer " + secret)
        here = self.call("GET", "", "127.0.0.1")

        self.assertEqual(by_token.status_code, 403)
        self.assertEqual(here.json()["proxies"], ["10.0.0.5"])

    def test_me_says_confirmed_to_an_owner_on_a_listed_network_and_to_nobody_else(self) -> None:
        self.connections.add_tailscale()
        owner, _ = self.owner_cookie("100.100.1.2")
        remote, _ = self.signer.open(kind=sign_in.REMOTE, address="100.100.1.3")

        as_owner = self.call("GET", "", "100.100.1.2", cookies=owner, route="/auth/me")
        as_remote = self.call("GET", "", "100.100.1.3", cookies={auth.COOKIE: remote},
                              route="/auth/me")
        outside = self.call("GET", "", "192.168.1.20", cookies=owner, route="/auth/me")

        self.assertTrue(as_owner.json()["confirmed"])
        self.assertFalse(as_remote.json()["confirmed"])
        self.assertFalse(outside.json()["confirmed"])

    def test_the_answer_says_whether_this_request_was_encrypted(self) -> None:
        self.connections.add_tailscale()

        request = SimpleNamespace(client=SimpleNamespace(host="100.100.1.2"), headers={},
                                  url=SimpleNamespace(scheme="http"))
        answer = _held(request)  # type: ignore[arg-type]

        self.assertTrue(answer.encrypted)


class TheCertificateTests(ConnectionsCase):
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

    def test_a_pair_that_loads_is_kept(self) -> None:
        self.connections.set_certificate(self.cert, self.key)

        self.assertEqual(self.connections.certificate(), (self.cert, self.key))
        again = encrypted_connections.Connections(self.root / "connections.json")
        self.assertEqual(again.certificate(), (self.cert, self.key))

    def test_a_file_that_is_not_there_is_refused_and_nothing_changes(self) -> None:
        with self.assertRaises(encrypted_connections.EntryError) as raised:
            self.connections.set_certificate(str(self.root / "gone.pem"), self.key)

        self.assertEqual(raised.exception.reason, encrypted_connections.NO_FILE)
        self.assertEqual(self.connections.certificate(), ("", ""))

    def test_a_key_that_does_not_belong_to_the_certificate_is_unreadable(self) -> None:
        other = Path(tempfile.mkdtemp(dir=self.root))
        _, other_key = make_certificate(other)

        with self.assertRaises(encrypted_connections.EntryError) as raised:
            self.connections.set_certificate(self.cert, other_key)

        self.assertEqual(raised.exception.reason, encrypted_connections.UNREADABLE)

    def test_a_key_that_asks_for_a_passphrase_is_unreadable_and_never_prompts(self) -> None:
        protected = Path(tempfile.mkdtemp(dir=self.root))
        (protected / "cert.cnf").write_text(CONFIG)
        cert, key = protected / "cert.pem", protected / "key.pem"
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-passout", "pass:secret",
                        "-days", "2", "-keyout", str(key), "-out", str(cert),
                        "-config", str(protected / "cert.cnf")],
                       check=True, capture_output=True)

        with self.assertRaises(encrypted_connections.EntryError) as raised:
            self.connections.set_certificate(str(cert), str(key))

        self.assertEqual(raised.exception.reason, encrypted_connections.UNREADABLE)

    def test_clearing_goes_back_to_plain_http(self) -> None:
        self.connections.set_certificate(self.cert, self.key)
        self.connections.clear_certificate()

        self.assertEqual(self.connections.certificate(), ("", ""))

    def test_the_names_it_vouches_for_leave_out_wildcards(self) -> None:
        self.assertEqual(encrypted_connections.certificate_names(self.cert),
                         ["cab.example.test", "100.101.102.103"])

    def test_an_unreadable_file_vouches_for_nothing(self) -> None:
        self.assertEqual(encrypted_connections.certificate_names(str(self.root / "gone.pem")), [])

    def test_the_addresses_handed_out_lead_with_the_certificates_names_over_https(self) -> None:
        self.connections.set_certificate(self.cert, self.key)
        encrypted_connections.https_started()

        found = addresses.urls(8001, "/remote")

        self.assertEqual(found[:2], ["https://cab.example.test:8001/remote",
                                     "https://100.101.102.103:8001/remote"])
        self.assertTrue(all(one.startswith("https://") for one in found))

    def test_without_a_certificate_they_stay_plain_http(self) -> None:
        self.assertTrue(all(one.startswith("http://") for one in addresses.urls(8001)))

    def test_a_run_that_has_not_started_yet_is_announced_as_the_certificate_chosen_says(
            self) -> None:
        self.assertFalse(encrypted_connections.serves_https())

        self.connections.set_certificate(self.cert, self.key)
        self.assertTrue(encrypted_connections.serves_https())

    def test_once_the_run_has_decided_that_is_what_is_announced(self) -> None:
        self.connections.set_certificate(self.cert, self.key)
        encrypted_connections.https_failed(encrypted_connections.UNREADABLE)

        self.assertFalse(encrypted_connections.serves_https())

    def test_a_certificate_that_cannot_be_served_leaves_the_run_on_http(self) -> None:
        encrypted_connections.https_failed(encrypted_connections.UNREADABLE)

        self.assertFalse(encrypted_connections.listening().https)
        self.assertEqual(encrypted_connections.listening().problem,
                         encrypted_connections.UNREADABLE)
        self.assertTrue(all(one.startswith("http://") for one in addresses.urls(8001)))


class TheLoopbackPortTests(ConnectionsCase):
    def test_the_devices_own_windows_use_the_loopback_port_once_there_is_one(self) -> None:
        self.assertEqual(serving.loopback_or(8001), 8001)

        serving.bound("loopback_port", 54321)

        self.assertEqual(serving.loopback_or(8001), 54321)
        self.assertIn(54321, serving.bound_ports())


if __name__ == "__main__":
    unittest.main()
