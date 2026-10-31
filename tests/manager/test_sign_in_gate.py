"""Another device sees the sign-in page and nothing else until a code has signed it in."""

from __future__ import annotations

import base64
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import httpapi
from common.config import paths
from common.security import accepted, authenticator, sign_in
from httpapi import auth
from manager.security import sign_in_gate
from tests.support.accepted import isolated

OTHER = "192.168.1.31"
HTML = {"accept": "text/html,application/xhtml+xml"}
_OWNER_CARD = sign_in_gate._owner_card


async def _page(request):
    return PlainTextResponse("manager")


async def _socket(websocket):
    await websocket.accept()
    await websocket.send_text("hello")
    await websocket.close()


def _app() -> Starlette:
    app = Starlette(routes=[
        Route("/", _page), Route("/manager", _page), Route("/remote", _page),
        Route("/favicon.ico", _page), Route("/_nicegui/x.js", _page),
        Route("/manager-static/x.css", _page), Route("/api/v1/health", _page),
        Route("/api/v1", _page), Route(sign_in_gate.PAGE, sign_in_gate.sign_in_page),
        Route("/media_games/x.png", _page, methods=["GET", "POST"]),
        WebSocketRoute("/_nicegui_ws/ws", _socket),
        Mount(sign_in_gate.API_PREFIX, httpapi.create_api_app())])
    app.add_middleware(sign_in_gate.SignInGate)
    return app


class Gate(unittest.TestCase):
    def setUp(self) -> None:
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.signer = sign_in.SignIn(Path(holder.name) / "sessions.json")
        self.addCleanup(sign_in.use, sign_in.instance())
        sign_in.use(self.signer)
        auth.set_policy(auth.SignInPolicy())
        self.addCleanup(auth.set_policy, auth.SignInPolicy())
        patcher = mock.patch.object(sign_in_gate, "_owner_card", return_value=("", ""))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.app = _app()
        self.machine = TestClient(self.app)
        self.computer = TestClient(self.app, client=(OTHER, 50000), follow_redirects=False)

    def remote(self) -> TestClient:
        token, _ = self.signer.open(kind=sign_in.REMOTE, address=OTHER)
        client = TestClient(self.app, client=(OTHER, 50000), follow_redirects=False)
        client.cookies.set(auth.COOKIE, token)
        return client

    def signed_in(self) -> TestClient:
        request = self.signer.reveal_now(OTHER)
        token, _ = self.signer.verify(request.id, request.code, address=OTHER)
        client = TestClient(self.app, client=(OTHER, 50000), follow_redirects=False)
        client.cookies.set(auth.COOKIE, token)
        return client


class WhoIsLetThrough(Gate):
    def test_this_machine_is_never_asked(self) -> None:
        self.assertEqual(self.machine.get("/manager").text, "manager")

    def test_a_signed_in_browser_gets_the_page(self) -> None:
        self.assertEqual(self.signed_in().get("/manager").text, "manager")

    def test_the_sign_in_page_is_open_to_anyone(self) -> None:
        self.assertEqual(self.computer.get(sign_in_gate.PAGE).status_code, 200)

    def test_the_api_answers_for_itself(self) -> None:
        self.assertEqual(self.computer.get("/api/v1/health").status_code, 200)
        self.assertEqual(self.computer.get("/api/v1").status_code, 200)

    def test_a_name_that_only_starts_like_the_api_is_not_the_api(self) -> None:
        self.assertEqual(self.computer.get("/api/v1x", headers=HTML).status_code, 303)


class WhoIsKeptOut(Gate):
    def test_a_page_request_is_sent_to_sign_in_and_remembers_where_it_was_going(self) -> None:
        answer = self.computer.get("/manager?tab=security", headers=HTML)

        self.assertEqual(answer.status_code, 303)
        self.assertEqual(answer.headers["location"],
                         "/signin?next=%2Fmanager%3Ftab%3Dsecurity")

    def test_anything_that_is_not_a_page_is_refused_plainly(self) -> None:
        for call in (lambda: self.computer.get("/media_games/x.png"),
                     lambda: self.computer.post("/media_games/x.png", headers=HTML)):
            with self.subTest():
                answer = call()
                self.assertEqual(answer.status_code, 401)
                self.assertNotIn("manager", answer.text)

    def test_the_live_connection_is_closed(self) -> None:
        with self.assertRaises(WebSocketDisconnect) as caught:
            with TestClient(self.app, client=(OTHER, 50000)).websocket_connect(
                    "/_nicegui_ws/ws"):
                pass
        self.assertEqual(caught.exception.code, 1008)

    def test_a_browser_whose_session_ended_is_kept_out_again(self) -> None:
        client = self.signed_in()
        self.signer.sign_out_everywhere()

        self.assertEqual(client.get("/manager", headers=HTML).status_code, 303)

    def test_the_live_connection_opens_for_this_machine(self) -> None:
        with self.machine.websocket_connect("/_nicegui_ws/ws") as socket:
            self.assertEqual(socket.receive_text(), "hello")


class ARemoteSignIn(Gate):
    """A browser that opened the Remote's QR holds a Play sign-in, and the Manager is not
    behind it: asking for a page past the Remote is asking for the code."""

    def test_a_page_past_the_remote_is_sent_to_the_code_and_remembers_where(self) -> None:
        client = self.remote()

        for path, next_ in (("/", "%2F"), ("/manager", "%2Fmanager")):
            with self.subTest(path=path):
                answer = client.get(path, headers=HTML)

                self.assertEqual(answer.status_code, 303)
                self.assertEqual(answer.headers["location"], f"/signin?next={next_}")

    def test_the_sign_in_page_offers_the_code_and_a_way_back(self) -> None:
        answer = self.remote().get("/signin?next=%2Fmanager")

        self.assertEqual(answer.status_code, 200)
        self.assertIn('id="ask"', answer.text)
        self.assertIn('id="code"', answer.text)
        self.assertIn("Signed in for the Remote only.", answer.text)
        self.assertIn('href="/remote"', answer.text)

    def test_the_way_back_is_for_a_remote_sign_in_alone(self) -> None:
        self.assertNotIn("Signed in for the Remote only.", self.computer.get("/signin").text)

    def test_an_owner_is_still_sent_on_from_the_sign_in_page(self) -> None:
        answer = self.signed_in().get("/signin?next=%2Fmanager")

        self.assertEqual(answer.status_code, 303)
        self.assertEqual(answer.headers["location"], "/manager")

    def test_what_is_not_a_page_stays_refused(self) -> None:
        client = self.remote()

        self.assertEqual(client.get("/media_games/x.png").status_code, 403)
        self.assertEqual(client.post("/media_games/x.png", headers=HTML).status_code, 403)
        with self.assertRaises(WebSocketDisconnect) as caught:
            with client.websocket_connect("/manager/ws"):
                pass
        self.assertEqual(caught.exception.code, 1008)

    def test_the_remote_and_what_it_loads_are_unchanged(self) -> None:
        client = self.remote()

        for path in ("/remote", "/favicon.ico", "/_nicegui/x.js", "/manager-static/x.css"):
            with self.subTest(path=path):
                self.assertEqual(client.get(path, headers=HTML).status_code, 200)

    def test_the_api_still_answers_for_itself(self) -> None:
        self.assertEqual(self.remote().get("/api/v1/health").status_code, 200)

    def test_a_hostile_destination_still_goes_home_from_a_remote_sign_in(self) -> None:
        answer = self.remote().get("/signin?next=//evil.example")

        self.assertIn("const hasApp", answer.text)
        self.assertIn('after = "/"', answer.text)

    def test_a_wrong_code_leaves_the_remote_sign_in_as_it_was(self) -> None:
        client = self.remote()
        asked = client.post("/api/v1/auth/code").json()

        done = client.post("/api/v1/auth/session",
                           json={"request_id": asked["request_id"], "code": "000000"})

        self.assertNotEqual(done.status_code, 200)
        self.assertEqual([s.kind for s in self.signer.sessions()], [sign_in.REMOTE])
        self.assertEqual(client.get("/remote", headers=HTML).status_code, 200)

    def test_an_owner_who_signs_in_again_keeps_the_row_it_had(self) -> None:
        token, _ = self.signer.open(kind=sign_in.OWNER, address=OTHER)
        client = TestClient(self.app, client=(OTHER, 50000), follow_redirects=False)
        client.cookies.set(auth.COOKIE, token)
        request = self.signer.reveal_now(OTHER)

        done = client.post("/api/v1/auth/session",
                           json={"request_id": request.id, "code": request.code})

        self.assertEqual(done.status_code, 200, done.text)
        self.assertEqual([s.kind for s in self.signer.sessions()], [sign_in.OWNER] * 2)

    def test_a_code_from_a_remote_browser_signs_it_in_as_owner_and_ends_the_remote(self) -> None:
        client = self.remote()
        remote_id = self.signer.sessions()[0].id
        self.assertEqual(client.get("/manager", headers=HTML).status_code, 303)

        asked = client.post("/api/v1/auth/code")
        self.assertEqual(asked.status_code, 200, asked.text)
        request = self.signer.reveal(asked.json()["request_id"])
        assert request is not None
        done = client.post("/api/v1/auth/session",
                           json={"request_id": request.id, "code": request.code})
        self.assertEqual(done.status_code, 200, done.text)

        sessions = self.signer.sessions()
        self.assertEqual([s.kind for s in sessions], [sign_in.OWNER])
        self.assertNotEqual(sessions[0].id, remote_id)
        self.assertEqual(client.get("/", headers=HTML).text, "manager")


class WhereToGoNext(unittest.TestCase):
    def test_a_path_on_this_server_is_kept(self) -> None:
        self.assertEqual(sign_in_gate.next_page("/manager?tab=players"), "/manager?tab=players")

    def test_anything_that_names_another_site_goes_home(self) -> None:
        for raw in ("//evil.example", "https://evil.example/", "/\\evil.example", "evil",
                    "/a\nb", "/a\rb", "/\t/evil.example", "/a\x00b", ""):
            with self.subTest(raw=raw):
                self.assertEqual(sign_in_gate.next_page(raw), "/")


class TheThemeFolders(unittest.TestCase):
    def setUp(self) -> None:
        isolated(self)
        accepted.accept_theme("Mine", "")

    def test_only_a_theme_that_may_run_is_served(self) -> None:
        self.assertTrue(sign_in_gate.theme_served("/themes/Mine/index.html"))
        self.assertFalse(sign_in_gate.theme_served("/themes/Dropped/index.html"))
        self.assertFalse(sign_in_gate.theme_served("/themes/Mine/../Dropped/index.html"))

    def test_nothing_else_is_asked_about(self) -> None:
        self.assertTrue(sign_in_gate.theme_served("/manager"))
        self.assertTrue(sign_in_gate.theme_served("/core/vpinfe-core.js"))


class ThePage(Gate):
    def test_the_machine_is_sent_on_without_a_page(self) -> None:
        answer = TestClient(self.app, follow_redirects=False).get("/signin?next=%2Fmanager")

        self.assertEqual(answer.status_code, 303)
        self.assertEqual(answer.headers["location"], "/manager")

    def test_a_signed_in_browser_is_sent_on_too(self) -> None:
        answer = self.signed_in().get("/signin")

        self.assertEqual(answer.status_code, 303)
        self.assertEqual(answer.headers["location"], "/")

    def test_a_redirect_cannot_leave_the_server(self) -> None:
        answer = self.signed_in().get("/signin?next=//evil.example")

        self.assertEqual(answer.headers["location"], "/")

    def test_nothing_on_the_page_can_run_unless_the_server_wrote_it(self) -> None:
        answer = self.computer.get("/signin")
        policy = answer.headers["content-security-policy"]
        nonce = policy.split("script-src 'nonce-")[1].split("'")[0]

        self.assertIn("default-src 'none'", policy)
        self.assertIn(f'<script nonce="{nonce}">', answer.text)
        self.assertEqual(answer.headers["x-frame-options"], "DENY")
        self.assertEqual(answer.headers["cache-control"], "no-store")
        self.assertNotIn(" onclick=", answer.text)

    def test_the_page_names_the_device_and_offers_a_code(self) -> None:
        answer = self.computer.get("/signin")

        self.assertIn('id="ask"', answer.text)
        self.assertIn('id="code"', answer.text)

    def test_the_owners_name_is_escaped(self) -> None:
        with mock.patch.object(sign_in_gate, "_owner_card",
                               return_value=("<script>alert(1)</script>", "")):
            answer = self.computer.get("/signin")

        self.assertNotIn("<script>alert(1)</script>", answer.text)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", answer.text)

    def test_the_destination_cannot_end_the_script_early(self) -> None:
        answer = self.computer.get("/signin?next=/a%3C/script%3E%3Cb")

        self.assertNotIn("</script><b", answer.text)

    def test_the_owner_card_survives_a_roster_that_cannot_be_read(self) -> None:
        with mock.patch.object(sign_in_gate.players, "get_roster", side_effect=OSError):
            self.assertEqual(_OWNER_CARD(), ("", ""))

    def test_the_owners_picture_comes_with_the_page_as_data(self) -> None:
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        picture = Path(holder.name) / "face.png"
        picture.write_bytes(b"\x89PNG")
        owner = SimpleNamespace(name="Pat", initials="P", player_id="p1")
        roster = SimpleNamespace(owner=lambda: owner,
                                 picture=lambda player_id, size: SimpleNamespace(path=picture))
        with mock.patch.object(sign_in_gate.players, "get_roster", return_value=roster):
            name, data = _OWNER_CARD()

        self.assertEqual(name, "Pat")
        self.assertTrue(data.startswith("data:image/png;base64,"))

    def test_an_owner_with_no_picture_still_has_a_name(self) -> None:
        owner = SimpleNamespace(name="Pat", initials="P", player_id="p1")
        roster = SimpleNamespace(owner=lambda: owner, picture=mock.Mock(side_effect=OSError))
        with mock.patch.object(sign_in_gate.players, "get_roster", return_value=roster):
            self.assertEqual(_OWNER_CARD(), ("Pat", ""))


class TheAuthenticatorWay(Gate):
    def setUp(self) -> None:
        super().setUp()
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        root = Path(holder.name)
        for name, value in (("CONFIG_DIR", root), ("SECRETS_PATH", root / "secrets.json")):
            patched = mock.patch.object(paths, name, value)
            patched.start()
            self.addCleanup(patched.stop)
        self.addCleanup(authenticator.use, authenticator.instance())
        self.held = authenticator.Authenticator()
        authenticator.use(self.held)

    def page(self) -> str:
        return self.computer.get(sign_in_gate.PAGE).text

    def test_with_none_set_up_the_page_offers_the_screen_code_alone(self) -> None:
        self.assertIn("const hasApp = false, screen = true;", self.page())

    def test_with_one_set_up_the_page_offers_both_ways(self) -> None:
        self.held.activate(self._first_code())

        text = self.page()

        self.assertIn("const hasApp = true, screen = true;", text)
        self.assertIn('id="appcode"', text)
        self.assertIn("Use Authenticator App", text)

    def test_with_the_screen_code_off_the_page_opens_on_the_app_alone(self) -> None:
        self.held.activate(self._first_code())
        self.held.set_screen_codes(False)

        self.assertIn("const hasApp = true, screen = false;", self.page())

    def test_the_app_signs_a_remote_browser_in_as_owner_and_ends_the_remote(self) -> None:
        self.held.activate(self._first_code())
        client = self.remote()
        remote_id = self.signer.sessions()[0].id
        self.held.set_screen_codes(False)

        done = client.post("/api/v1/auth/authenticator/session",
                           json={"code": self._next_code()})

        self.assertEqual(done.status_code, 200, done.text)
        sessions = self.signer.sessions()
        self.assertEqual([s.kind for s in sessions], [sign_in.OWNER])
        self.assertNotEqual(sessions[0].id, remote_id)

    def _next_code(self) -> str:
        return authenticator._code(base64.b32decode(self.secret), int(time.time() // 30) + 1)

    def _first_code(self) -> str:
        made = self.held.begin("Game Room")
        self.secret = made.secret
        return authenticator._code(base64.b32decode(made.secret), int(time.time() // 30))
