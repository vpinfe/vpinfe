"""Whether a caller reached us from this machine or over the network, and what each may do.

VPinFE binds every interface by default, so "on this machine" and "able to reach this
machine" are different questions. The policy answers them differently: the machine is
trusted, another device is trusted once a code shown on the machine has signed its
browser in, and anyone else may only ask what this instance is.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from common.security import sign_in
from httpapi import auth, scopes


def _request(peer, cookies=None, method="GET", **headers):
    """A request as Starlette hands one over: a peer address, headers and cookies."""
    client = SimpleNamespace(host=peer) if peer is not None else None
    return SimpleNamespace(client=client, headers=headers, cookies=cookies or {}, method=method)


class CallerOriginTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = auth.SignInPolicy()
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.addCleanup(sign_in.use, sign_in.instance())
        sign_in.use(sign_in.SignIn(Path(holder.name) / "sessions.json"))

    def sign_in_from(self, address: str) -> str:
        signer = sign_in.instance()
        request = signer.reveal_now(address)
        token, _ = signer.verify(request.id, request.code, address=address)
        return token

    def test_a_request_from_this_machine_is_local(self) -> None:
        for host in ("127.0.0.1", "::1", "localhost"):
            with self.subTest(host=host):
                self.assertTrue(auth.caller_is_local(_request(host)))

    def test_a_request_from_anywhere_else_is_not(self) -> None:
        for host in ("192.168.110.42", "10.0.0.5", "203.0.113.9"):
            with self.subTest(host=host):
                self.assertFalse(auth.caller_is_local(_request(host)))

    def test_an_in_process_call_is_local(self) -> None:
        """No socket means no remote party to be cautious about."""
        self.assertTrue(auth.caller_is_local(_request(None)))

    def test_a_caller_cannot_declare_itself_local(self) -> None:
        """X-Forwarded-For is written by whoever sent the request. Reading it here would
        let any caller claim to be on the machine, which is the whole distinction."""
        spoofed = _request("10.0.0.5", **{"X-Forwarded-For": "127.0.0.1"})

        self.assertFalse(auth.caller_is_local(spoofed))
        self.assertEqual(self.policy.identify(spoofed).origin, auth.NETWORK)

    def test_a_loopback_request_that_was_forwarded_is_not_local(self) -> None:
        """A proxy on this machine connects from loopback for everyone behind it."""
        forwarded = _request("127.0.0.1", **{"X-Forwarded-For": "203.0.113.9"})

        self.assertFalse(auth.caller_is_local(forwarded))

    def test_a_loopback_request_for_a_name_that_is_not_ours_is_not_local(self) -> None:
        """DNS rebinding: a page's own name resolves to loopback."""
        rebound = _request("127.0.0.1", **{"host": "rebind.example.com:8001"})

        self.assertFalse(auth.caller_is_local(rebound))

    def test_a_loopback_request_from_a_page_of_another_site_is_not_local(self) -> None:
        foreign = _request("127.0.0.1", **{"host": "localhost:8001",
                                           "origin": "https://evil.example.com"})

        self.assertFalse(auth.caller_is_local(foreign))

    def test_the_machines_own_address_is_the_machine(self) -> None:
        from common.host import this_machine

        own = sorted(name for name in this_machine.own_names()
                     if name.count(".") == 3 and not name.startswith("127."))
        if not own:
            self.skipTest("this machine has no other IPv4 address")

        self.assertTrue(auth.caller_is_local(_request(own[0])))

    def test_the_identity_records_where_the_caller_came_from(self) -> None:
        self.assertEqual(self.policy.identify(_request("127.0.0.1")).origin, auth.LOCAL)
        self.assertEqual(self.policy.identify(_request("10.0.0.5")).origin, auth.NETWORK)

    def test_this_machine_may_do_everything(self) -> None:
        self.assertEqual(self.policy.identify(_request("127.0.0.1")).scopes, scopes.CORE)

    def test_a_device_that_has_not_signed_in_may_only_ask_what_this_is(self) -> None:
        for host in ("10.0.0.5", "192.168.110.42"):
            with self.subTest(host=host):
                self.assertEqual(self.policy.identify(_request(host)).scopes,
                                 frozenset({scopes.INSTANCE_READ}))

    def test_a_browser_signed_in_from_another_device_may_do_everything(self) -> None:
        token = self.sign_in_from("10.0.0.5")

        identity = self.policy.identify(_request("10.0.0.5", {auth.COOKIE: token}))

        self.assertEqual(identity.scopes, scopes.CORE)
        self.assertEqual(identity.origin, auth.NETWORK)
        self.assertEqual(identity.kind, sign_in.OWNER)

    def test_a_sign_in_that_ended_opens_nothing(self) -> None:
        token = self.sign_in_from("10.0.0.5")
        sign_in.instance().sign_out_everywhere()

        identity = self.policy.identify(_request("10.0.0.5", {auth.COOKIE: token}))

        self.assertEqual(identity.scopes, frozenset({scopes.INSTANCE_READ}))

    def test_a_write_another_site_began_cannot_use_the_cookie(self) -> None:
        token = self.sign_in_from("10.0.0.5")

        identity = self.policy.identify(_request(
            "10.0.0.5", {auth.COOKIE: token}, "POST", **{"sec-fetch-site": "cross-site"}))

        self.assertEqual(identity.scopes, frozenset({scopes.INSTANCE_READ}))

    def test_a_read_another_site_began_still_uses_the_cookie(self) -> None:
        token = self.sign_in_from("10.0.0.5")

        identity = self.policy.identify(_request(
            "10.0.0.5", {auth.COOKIE: token}, "GET", **{"sec-fetch-site": "cross-site"}))

        self.assertEqual(identity.scopes, scopes.CORE)

    def test_an_identity_built_without_an_origin_reads_as_network(self) -> None:
        """The cautious default: a policy or a test that forgets to say must not have
        its identity silently claim to be on the machine."""
        anonymous = auth.Identity(name="whoever", scopes=frozenset())

        self.assertEqual(anonymous.origin, auth.NETWORK)
        self.assertFalse(anonymous.is_local)


if __name__ == "__main__":
    unittest.main()
