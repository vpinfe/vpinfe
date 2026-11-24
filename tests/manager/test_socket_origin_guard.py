"""A page of another site cannot open the Manager's live connection.

The guard sits in front of NiceGUI, which accepts any Origin. The browser names the page
that opened the socket and a page cannot change it.
"""

from __future__ import annotations

import unittest

from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route, WebSocketRoute
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from manager.security.origin_guard import SocketOriginGuard


async def _page(request):
    return PlainTextResponse("page")


async def _socket(websocket):
    await websocket.accept()
    await websocket.send_text("hello")
    await websocket.close()


def _app() -> Starlette:
    app = Starlette(routes=[Route("/_nicegui_ws/socket.io/", _page),
                            Route("/page", _page),
                            WebSocketRoute("/_nicegui_ws/ws", _socket)])
    app.add_middleware(SocketOriginGuard)
    return app


class SocketOriginGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(_app(), base_url="http://localhost:8001")

    def test_the_managers_own_page_gets_through(self) -> None:
        response = self.client.get("/_nicegui_ws/socket.io/",
                                   headers={"origin": "http://localhost:8001"})
        self.assertEqual(response.status_code, 200)

    def test_a_client_that_sends_no_origin_gets_through(self) -> None:
        self.assertEqual(self.client.get("/_nicegui_ws/socket.io/").status_code, 200)

    def test_a_page_of_another_site_is_refused_on_polling(self) -> None:
        response = self.client.get("/_nicegui_ws/socket.io/",
                                   headers={"origin": "https://evil.example.com"})
        self.assertEqual(response.status_code, 403)

    def test_a_page_of_another_site_is_refused_on_the_socket(self) -> None:
        with self.assertRaises(WebSocketDisconnect) as caught:
            with self.client.websocket_connect(
                    "/_nicegui_ws/ws", headers={"origin": "https://evil.example.com"}):
                pass
        self.assertEqual(caught.exception.code, 1008)

    def test_the_managers_own_page_gets_a_socket(self) -> None:
        with self.client.websocket_connect(
                "/_nicegui_ws/ws",
                headers={"host": "localhost:8001", "origin": "http://localhost:8001"}) as socket:
            self.assertEqual(socket.receive_text(), "hello")

    def test_a_route_that_is_not_the_socket_is_left_alone(self) -> None:
        response = self.client.get("/page", headers={"origin": "https://evil.example.com"})
        self.assertEqual(response.status_code, 200)


if __name__ == "__main__":
    unittest.main()
