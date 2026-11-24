"""A Full Control token asks first, the Owner approves, and the request is repeated once.

`POST /extensions/{name}/accept` stands in for a Protected change: with a name nothing
answers to it ends in a 404 when it gets past the scope check and in a 403 when it does not.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from starlette.testclient import TestClient

import httpapi
from common.security import api_tokens, approvals, sign_in
from httpapi import auth

PEER = "192.168.1.50"
PROTECTED_PATH = "/extensions/nothing/accept"


class Clock:
    def __init__(self) -> None:
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class AskCase(unittest.TestCase):
    def setUp(self) -> None:
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        root = Path(holder.name)
        self.clock = Clock()
        self.signer = sign_in.SignIn(root / "sessions.json", self.clock)
        self.addCleanup(self.signer.wait_for_saves)
        self.tokens = api_tokens.Tokens(root / "tokens.json", self.clock)
        self.asks = approvals.Approvals(self.clock)
        self.addCleanup(sign_in.use, sign_in.instance())
        self.addCleanup(api_tokens.use, api_tokens.instance())
        self.addCleanup(approvals.use, approvals.instance())
        sign_in.use(self.signer)
        api_tokens.use(self.tokens)
        approvals.use(self.asks)
        self.full, _ = self.tokens.make("home", api_tokens.FULL)
        self.play, _ = self.tokens.make("kiosk", api_tokens.PLAY)
        self.app = httpapi.create_api_app()

    def client(self, peer: str = PEER) -> TestClient:
        return TestClient(self.app, raise_server_exceptions=False, client=(peer, 50000),
                          base_url="http://" + peer)

    def as_token(self, secret: str, **extra: str) -> dict[str, str]:
        return {"authorization": "Bearer " + secret, **extra}

    def ask(self, secret=None, method="POST", path=PROTECTED_PATH, body=None):
        return self.client().post("/auth/asks", headers=self.as_token(secret or self.full),
                                  json={"method": method, "path": path, "body": body})

    def owner(self, *, confirmed=True) -> dict[str, str]:
        token, session = self.signer.open(kind=sign_in.OWNER, address=PEER)
        if confirmed:
            self.signer.confirm_grant(session.id)
        else:
            self.clock.advance(sign_in.CONFIRM_SECONDS + 1)
        return {auth.COOKIE: token}

    def owner_call(self, method, path, cookies, **kwargs):
        client = self.client()
        for name, value in cookies.items():
            client.cookies.set(name, value)
        return client.request(method, path, **kwargs)

    def approve(self, ask_id: str) -> None:
        response = self.owner_call("POST", f"/auth/asks/{ask_id}/approve", self.owner())
        self.assertEqual(response.status_code, 200, response.text)

    def repeat(self, ask_id: str, secret=None, method="POST", path=PROTECTED_PATH,
               content_type=None, **kwargs):
        headers = self.as_token(secret or self.full, **{auth.APPROVAL_HEADER: ask_id})
        if content_type:
            headers["content-type"] = content_type
        return self.client().request(method, path, headers=headers, **kwargs)


class WhoMayAskTests(AskCase):
    def test_a_full_control_token_asks_and_waits(self) -> None:
        response = self.ask()

        self.assertEqual(response.status_code, 200, response.text)
        asked = response.json()
        self.assertEqual((asked["state"], asked["method"], asked["path"], asked["token"]),
                         ("waiting", "POST", PROTECTED_PATH, "home"))
        self.assertGreater(asked["expires_in"], 0)

    def test_a_play_token_cannot_ask(self) -> None:
        response = self.ask(self.play)

        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.asks.waiting(), [])

    def test_an_owner_does_not_ask_they_confirm(self) -> None:
        response = self.owner_call("POST", "/auth/asks", self.owner(),
                                   json={"method": "POST", "path": PROTECTED_PATH})

        self.assertEqual(response.status_code, 403)

    def test_a_stranger_cannot_ask(self) -> None:
        response = self.client().post("/auth/asks",
                                      json={"method": "POST", "path": PROTECTED_PATH})

        self.assertEqual(response.status_code, 403)

    def test_a_token_reads_only_its_own_ask(self) -> None:
        ask_id = self.ask().json()["id"]
        other, _ = self.tokens.make("other", api_tokens.FULL)

        mine = self.client().get(f"/auth/asks/{ask_id}", headers=self.as_token(self.full))
        theirs = self.client().get(f"/auth/asks/{ask_id}", headers=self.as_token(other))

        self.assertEqual(mine.json()["state"], "waiting")
        self.assertEqual(theirs.status_code, 404)

    def test_a_token_cannot_approve_even_its_own_ask(self) -> None:
        ask_id = self.ask().json()["id"]

        response = self.client().post(f"/auth/asks/{ask_id}/approve",
                                      headers=self.as_token(self.full))

        self.assertEqual(response.status_code, 403)
        ask = self.asks.get(ask_id)
        assert ask is not None
        self.assertEqual(ask.state, approvals.WAITING)

    def test_a_token_can_take_its_ask_back(self) -> None:
        ask_id = self.ask().json()["id"]

        response = self.client().delete(f"/auth/asks/{ask_id}", headers=self.as_token(self.full))

        self.assertEqual(response.json()["state"], "declined")


class WhatCannotBeAskedForTests(AskCase):
    def test_nothing_under_auth_can_be_asked_for(self) -> None:
        for method, path in (("POST", "/auth/tokens"),
                             ("PUT", "/auth/tokens/abc"),
                             ("POST", "/auth/encrypted/proxies"),
                             ("POST", "/auth/encrypted/networks"),
                             ("DELETE", "/auth/encrypted/networks"),
                             ("POST", "/auth/authenticator"),
                             ("POST", "/auth/asks"),
                             ("POST", "/auth/asks/abc/approve"),
                             ("POST", "/auth"),
                             ("POST", "/auth/tokens?x=1")):
            with self.subTest(method=method, path=path):
                response = self.ask(method=method, path=path)

                self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(self.asks.waiting(), [])

    def test_a_read_cannot_be_asked_for(self) -> None:
        self.assertEqual(self.ask(method="GET", path="/games").status_code, 400)

    def test_a_percent_encoded_path_cannot_be_asked_for(self) -> None:
        self.assertEqual(self.ask(path="/extensions/a%20b/accept").status_code, 400)

    def test_a_request_that_does_not_exist_cannot_be_asked_for(self) -> None:
        self.assertEqual(self.ask(path="/no/such/route").status_code, 400)

    def test_a_body_too_large_to_read_is_refused(self) -> None:
        response = self.ask(body={"text": "x" * approvals.MAX_BODY_CHARS})

        self.assertEqual(response.status_code, 400)

    def test_too_many_waiting_is_a_429(self) -> None:
        for number in range(approvals.PER_TOKEN):
            self.assertEqual(self.ask(body={"n": number}).status_code, 200)

        self.assertEqual(self.ask(body={"n": "more"}).status_code, 429)


class RepeatingTheRequestTests(AskCase):
    def test_without_approval_the_protected_change_is_refused(self) -> None:
        response = self.client().post(PROTECTED_PATH, headers=self.as_token(self.full))

        self.assertEqual(response.status_code, 403)

    def test_an_approved_request_gets_past_the_scope_check_once(self) -> None:
        ask_id = self.ask().json()["id"]
        self.approve(ask_id)

        first = self.repeat(ask_id)
        second = self.repeat(ask_id)

        self.assertEqual(first.status_code, 404, first.text)
        self.assertEqual(second.status_code, 403)
        self.assertEqual(second.json()["error"]["details"]["reason"], auth.APPROVAL_INVALID)

    def test_an_ask_not_yet_approved_does_not_let_it_through(self) -> None:
        ask_id = self.ask().json()["id"]

        response = self.repeat(ask_id)

        self.assertEqual(response.status_code, 403)
        ask = self.asks.get(ask_id)
        assert ask is not None
        self.assertEqual(ask.state, approvals.WAITING)

    def test_a_declined_ask_does_not_let_it_through(self) -> None:
        ask_id = self.ask().json()["id"]
        declined = self.owner_call("POST", f"/auth/asks/{ask_id}/decline", self.owner())
        self.assertEqual(declined.json()["state"], "declined")

        self.assertEqual(self.repeat(ask_id).status_code, 403)

    def test_an_approval_not_yet_used_can_be_taken_back(self) -> None:
        ask_id = self.ask().json()["id"]
        self.owner_call("POST", f"/auth/asks/{ask_id}/approve", self.owner())
        taken = self.owner_call("POST", f"/auth/asks/{ask_id}/decline", self.owner())

        self.assertEqual(taken.json()["state"], "declined")
        self.assertEqual(self.repeat(ask_id).status_code, 403)

    def test_a_body_sent_in_chunks_is_refused_and_spends_nothing(self) -> None:
        ask_id = self.ask(body={"name": "a"}).json()["id"]
        self.approve(ask_id)

        chunked = self.repeat(ask_id, content=(part for part in (b'{"name":', b'"a"}')),
                              content_type="application/json")
        sized = self.repeat(ask_id, json={"name": "a"})

        self.assertEqual(chunked.status_code, 403)
        self.assertEqual(chunked.json()["error"]["details"]["reason"], auth.APPROVAL_INVALID)
        self.assertEqual(sized.status_code, 404, sized.text)

    def test_revoking_a_token_drops_what_it_asked(self) -> None:
        kept = self.tokens.make("other", api_tokens.FULL)[0]
        self.ask()
        self.ask(secret=kept, path="/extensions/other/accept")
        token_id = self.tokens.tokens()[0].id

        revoked = self.owner_call("DELETE", f"/auth/tokens/{token_id}", self.owner())

        self.assertEqual(revoked.status_code, 200, revoked.text)
        self.assertEqual([one.token_name for one in self.asks.waiting()], ["other"])

    def test_another_path_or_method_spends_nothing(self) -> None:
        ask_id = self.ask().json()["id"]
        self.approve(ask_id)

        wrong_path = self.repeat(ask_id, path="/extensions/other/accept")
        wrong_method = self.repeat(ask_id, method="DELETE")

        self.assertEqual(wrong_path.status_code, 403)
        self.assertEqual(wrong_method.status_code, 403)
        ask = self.asks.get(ask_id)
        assert ask is not None
        self.assertEqual(ask.state, approvals.APPROVED)
        self.assertEqual(self.repeat(ask_id).status_code, 404)

    def test_the_body_must_be_the_one_that_was_asked_for(self) -> None:
        ask_id = self.ask(body={"a": 1, "b": 2}).json()["id"]
        self.approve(ask_id)

        changed = self.repeat(ask_id, json={"a": 1, "b": 3})
        not_json = self.repeat(ask_id, content=b"a=1&b=2")
        same = self.repeat(ask_id, content=b'{"b": 2,  "a": 1}',
                           content_type="application/json")

        self.assertEqual(changed.status_code, 403)
        self.assertEqual(not_json.status_code, 403)
        self.assertNotEqual(same.status_code, 403, same.text)

    def test_the_query_must_be_the_one_that_was_asked_for(self) -> None:
        ask_id = self.ask(path=PROTECTED_PATH + "?force=false").json()["id"]
        self.approve(ask_id)

        self.assertEqual(self.repeat(ask_id, path=PROTECTED_PATH + "?force=true").status_code,
                         403)
        self.assertEqual(self.repeat(ask_id, path=PROTECTED_PATH + "?force=false").status_code,
                         404)

    def test_another_token_cannot_spend_it(self) -> None:
        ask_id = self.ask().json()["id"]
        self.approve(ask_id)
        other, _ = self.tokens.make("other", api_tokens.FULL)

        self.assertEqual(self.repeat(ask_id, secret=other).status_code, 403)
        self.assertEqual(self.repeat(ask_id).status_code, 404)

    def test_an_approval_lapses_after_an_hour(self) -> None:
        ask_id = self.ask().json()["id"]
        self.approve(ask_id)

        self.clock.advance(approvals.LAPSE_SECONDS + 1)

        self.assertEqual(self.repeat(ask_id).status_code, 403)

    def test_an_approval_adds_the_protected_tier_to_that_request_only(self) -> None:
        ask_id = self.ask().json()["id"]
        self.approve(ask_id)
        self.repeat(ask_id)

        after = self.client().post(PROTECTED_PATH, headers=self.as_token(self.full))

        self.assertEqual(after.status_code, 403)
        self.assertEqual(after.json()["error"]["details"]["reason"], auth.PROTECTED)

    def test_a_play_token_gets_nothing_from_an_approval_it_borrowed(self) -> None:
        ask_id = self.ask().json()["id"]
        self.approve(ask_id)

        self.assertEqual(self.repeat(ask_id, secret=self.play).status_code, 403)

    def test_the_header_means_nothing_to_a_caller_who_is_not_a_token(self) -> None:
        ask_id = self.ask().json()["id"]
        self.approve(ask_id)

        response = self.client().post(PROTECTED_PATH,
                                      headers={auth.APPROVAL_HEADER: ask_id})

        self.assertEqual(response.status_code, 403)
        ask = self.asks.get(ask_id)
        assert ask is not None
        self.assertEqual(ask.state, approvals.APPROVED)


class TheOwnersSideTests(AskCase):
    def test_the_owner_sees_what_is_waiting_and_a_token_does_not(self) -> None:
        self.ask(body={"a": 1})

        seen = self.owner_call("GET", "/auth/asks", self.owner())
        refused = self.client().get("/auth/asks", headers=self.as_token(self.full))

        self.assertEqual([(one["path"], one["body"]) for one in seen.json()["asks"]],
                         [(PROTECTED_PATH, {"a": 1})])
        self.assertEqual(refused.status_code, 403)

    def test_approving_needs_a_confirm(self) -> None:
        ask_id = self.ask().json()["id"]

        response = self.owner_call("POST", f"/auth/asks/{ask_id}/approve",
                                   self.owner(confirmed=False))

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["details"]["reason"], auth.CONFIRM_NEEDED)
        ask = self.asks.get(ask_id)
        assert ask is not None
        self.assertEqual(ask.state, approvals.WAITING)

    def test_this_device_approves_without_a_confirm(self) -> None:
        ask_id = self.ask().json()["id"]

        response = self.client("127.0.0.1").post(f"/auth/asks/{ask_id}/approve")

        self.assertEqual(response.json()["state"], "approved")

    def test_approving_what_has_lapsed_is_a_404(self) -> None:
        ask_id = self.ask().json()["id"]
        self.clock.advance(approvals.LAPSE_SECONDS + 1)

        response = self.client("127.0.0.1").post(f"/auth/asks/{ask_id}/approve")

        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
