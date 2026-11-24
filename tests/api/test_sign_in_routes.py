"""Signing in over HTTP: ask, type the code the machine showed, hold the cookie, end it.

The machine is the test client's default peer. Another device is a client whose peer is a
LAN address, which is how the API sees one.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

import httpapi
from common.security import sign_in
from httpapi import auth, scopes

OTHER = "192.168.1.31"
PHONE = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
         "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")


class Clock:
    def __init__(self) -> None:
        self.now = 2_000_000.0

    def __call__(self) -> float:
        return self.now


class SignInOverHttp(unittest.TestCase):
    def setUp(self) -> None:
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.clock = Clock()
        self.signer = sign_in.SignIn(Path(holder.name) / "sessions.json", self.clock)
        self.addCleanup(sign_in.use, sign_in.instance())
        sign_in.use(self.signer)
        auth.set_policy(auth.SignInPolicy())
        self.addCleanup(auth.set_policy, auth.SignInPolicy())
        self.api = httpapi.create_api_app()
        self.machine = TestClient(self.api, raise_server_exceptions=False)

    def computer(self, address: str = OTHER) -> TestClient:
        return TestClient(self.api, raise_server_exceptions=False, client=(address, 50000),
                          headers={"user-agent": PHONE})

    def shown(self, client: TestClient) -> tuple[str, str]:
        asked = client.post("/auth/code")
        self.assertEqual(asked.status_code, 200, asked.text)
        request_id = asked.json()["request_id"]
        revealed = self.signer.reveal(request_id)
        assert revealed is not None
        return request_id, revealed.code

    def sign_in(self, client: TestClient) -> None:
        request_id, code = self.shown(client)
        done = client.post("/auth/session", json={"request_id": request_id, "code": code})
        self.assertEqual(done.status_code, 200, done.text)


class TheCodeFlow(SignInOverHttp):
    def test_asking_answers_with_no_digits(self) -> None:
        body = self.computer().post("/auth/code").json()

        self.assertEqual(set(body), {"request_id", "expires_in", "can_ask_again_in"})
        self.assertEqual(body["expires_in"], sign_in.CODE_SECONDS)

    def test_the_right_code_sets_a_cookie_that_opens_the_rest(self) -> None:
        client = self.computer()
        self.assertEqual(client.get("/play/state").status_code, 403)

        self.sign_in(client)

        self.assertEqual(client.get("/play/state").status_code, 200)
        cookie = client.cookies.get(auth.COOKIE)
        self.assertTrue(cookie)

    def test_the_cookie_cannot_be_read_by_a_script(self) -> None:
        client = self.computer()
        request_id, code = self.shown(client)

        done = client.post("/auth/session", json={"request_id": request_id, "code": code})

        header = done.headers["set-cookie"].lower()
        self.assertIn("httponly", header)
        self.assertIn("samesite=lax", header)
        self.assertNotIn("secure", header)

    def test_the_session_says_what_browser_and_where(self) -> None:
        client = self.computer()
        self.sign_in(client)

        row = self.machine.get("/auth/sessions").json()["sessions"][0]

        self.assertEqual(row["agent"], "Safari on iPhone")
        self.assertEqual(row["address"], OTHER)
        self.assertEqual(row["kind"], sign_in.OWNER)

    def test_a_wrong_code_is_refused_and_leaves_the_browser_out(self) -> None:
        client = self.computer()
        request_id, _ = self.shown(client)

        refused = client.post("/auth/session", json={"request_id": request_id, "code": "000000"})

        self.assertEqual(refused.status_code, 403)
        self.assertEqual(refused.json()["error"]["details"]["reason"], sign_in.WRONG)
        self.assertEqual(client.get("/play/state").status_code, 403)

    def test_a_code_nobody_showed_is_refused(self) -> None:
        client = self.computer()
        request_id = client.post("/auth/code").json()["request_id"]

        refused = client.post("/auth/session", json={"request_id": request_id, "code": "123456"})

        self.assertEqual(refused.status_code, 403)
        self.assertEqual(refused.json()["error"]["details"]["reason"], sign_in.NO_CODE)

    def test_a_second_browser_at_the_same_address_cannot_take_the_request(self) -> None:
        first = self.computer()
        request_id, code = self.shown(first)
        second = self.computer()

        asked = second.post("/auth/code")
        stolen = second.post("/auth/session", json={"request_id": request_id, "code": code})

        self.assertEqual(asked.status_code, 429)
        self.assertEqual(stolen.status_code, 403)
        self.assertEqual(stolen.json()["error"]["details"]["reason"], sign_in.NO_CODE)
        done = first.post("/auth/session", json={"request_id": request_id, "code": code})
        self.assertEqual(done.status_code, 200, done.text)

    def test_the_same_browser_asking_again_gets_its_request_back(self) -> None:
        client = self.computer()

        first = client.post("/auth/code").json()["request_id"]

        self.assertEqual(client.post("/auth/code").json()["request_id"], first)

    def test_another_address_asking_too_soon_is_told_to_wait(self) -> None:
        self.computer().post("/auth/code")

        soon = self.computer("192.168.1.32").post("/auth/code")

        self.assertEqual(soon.status_code, 429)
        error = soon.json()["error"]
        self.assertEqual(error["code"], "too_many_requests")
        self.assertGreater(error["details"]["wait"], 0)

    def test_an_address_the_machine_ignored_cannot_ask(self) -> None:
        client = self.computer()
        request_id = client.post("/auth/code").json()["request_id"]
        self.signer.ignore(request_id)

        refused = client.post("/auth/code")

        self.assertEqual(refused.status_code, 403)
        self.assertEqual(refused.json()["error"]["details"]["reason"], sign_in.IGNORED)

    def test_a_page_of_another_site_cannot_ask_for_a_code(self) -> None:
        client = self.computer()

        refused = client.post("/auth/code", headers={"sec-fetch-site": "cross-site"})

        self.assertEqual(refused.status_code, 403)
        self.assertIsNone(self.signer.pending())

    def test_a_page_of_another_site_cannot_type_a_code(self) -> None:
        client = self.computer()
        request_id, code = self.shown(client)

        refused = client.post("/auth/session", json={"request_id": request_id, "code": code},
                              headers={"sec-fetch-site": "cross-site"})

        self.assertEqual(refused.status_code, 403)
        self.assertEqual(self.signer.sessions(), [])


class WhoIAm(SignInOverHttp):
    def test_the_machine_is_local_and_has_no_session(self) -> None:
        body = self.machine.get("/auth/me").json()

        self.assertTrue(body["local"])
        self.assertFalse(body["signed_in"])

    def test_a_device_not_signed_in_is_told_so(self) -> None:
        body = self.computer().get("/auth/me").json()

        self.assertFalse(body["local"])
        self.assertFalse(body["signed_in"])

    def test_a_signed_in_browser_is_told_its_kind(self) -> None:
        client = self.computer()
        self.sign_in(client)

        body = client.get("/auth/me").json()

        self.assertTrue(body["signed_in"])
        self.assertEqual(body["kind"], sign_in.OWNER)
        self.assertTrue(body["session_id"])


class EndingASession(SignInOverHttp):
    def test_signing_out_closes_the_door_behind_the_browser(self) -> None:
        client = self.computer()
        self.sign_in(client)

        done = client.post("/auth/sign_out")

        self.assertEqual(done.status_code, 200)
        self.assertEqual(self.signer.sessions(), [])
        self.assertEqual(client.get("/play/state").status_code, 403)

    def test_signing_out_when_not_signed_in_is_still_signed_out(self) -> None:
        self.assertEqual(self.computer().post("/auth/sign_out").status_code, 200)

    def test_the_machine_ends_one_browser_and_leaves_the_rest(self) -> None:
        first, second = self.computer(), self.computer("192.168.1.32")
        self.sign_in(first)
        self.clock.now += 60
        self.sign_in(second)
        ended = [one for one in self.machine.get("/auth/sessions").json()["sessions"]
                 if one["address"] == OTHER][0]

        gone = self.machine.delete(f"/auth/sessions/{ended['id']}")

        self.assertEqual(gone.json(), {"ended": 1})
        self.assertEqual(first.get("/play/state").status_code, 403)
        self.assertEqual(second.get("/play/state").status_code, 200)

    def test_the_machine_signs_everyone_out(self) -> None:
        client = self.computer()
        self.sign_in(client)

        self.assertEqual(self.machine.delete("/auth/sessions").json(), {"ended": 1})
        self.assertEqual(client.get("/play/state").status_code, 403)

    def test_the_list_marks_the_browser_asking(self) -> None:
        client = self.computer()
        self.sign_in(client)

        row = client.get("/auth/sessions").json()["sessions"][0]

        self.assertTrue(row["current"])
        self.assertFalse(self.machine.get("/auth/sessions").json()["sessions"][0]["current"])


class TheScopes(SignInOverHttp):
    def test_the_routes_that_manage_sessions_need_the_access_scopes(self) -> None:
        wanted = {("GET", "/auth/sessions"): scopes.ACCESS_READ,
                  ("DELETE", "/auth/sessions"): scopes.ACCESS_WRITE,
                  ("DELETE", "/auth/sessions/{session_id}"): scopes.ACCESS_WRITE}
        found = {(method, path): auth.route_scope(route)
                 for path, route in auth.iter_api_routes(self.api)
                 for method in route.methods or () if (method, path) in wanted}

        self.assertEqual(found, wanted)

    def test_a_device_not_signed_in_cannot_see_or_end_sessions(self) -> None:
        client = self.computer()

        self.assertEqual(client.get("/auth/sessions").status_code, 403)
        self.assertEqual(client.delete("/auth/sessions").status_code, 403)

    def test_a_device_not_signed_in_can_still_ask_what_this_is(self) -> None:
        self.assertEqual(self.computer().get("/").status_code, 200)

    def test_the_spec_and_the_explorer_wait_for_a_sign_in(self) -> None:
        client = self.computer()

        for path in ("/openapi.json", "/docs", "/docs/oauth2-redirect"):
            self.assertEqual(client.get(path).status_code, 403, path)

        self.sign_in(client)

        for path in ("/openapi.json", "/docs"):
            self.assertEqual(client.get(path).status_code, 200, path)
        self.assertEqual(self.machine.get("/openapi.json").status_code, 200)

    def test_the_gate_is_the_same_when_the_api_is_mounted_under_its_prefix(self) -> None:
        parent = FastAPI()
        httpapi.register(parent)
        client = TestClient(parent, raise_server_exceptions=False, client=(OTHER, 50000))
        machine = TestClient(parent, raise_server_exceptions=False)

        self.assertEqual(client.get(f"{httpapi.API_PREFIX}/openapi.json").status_code, 403)
        self.assertEqual(client.get(f"{httpapi.API_PREFIX}/docs").status_code, 403)
        self.assertEqual(machine.get(f"{httpapi.API_PREFIX}/openapi.json").status_code, 200)
