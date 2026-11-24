"""Refuses a page of another site that reaches for the Manager's live connection.

A browser lets any page open a socket to any address, and sends the page's own Origin with
it. NiceGUI accepts every Origin, so a page on the open web could drive a Manager it can
reach. The page that is allowed to is the one the Manager served, which names itself the
same way the Manager was asked for. A client that sends no Origin is not a browser page.
"""

from __future__ import annotations

from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from common.host import this_machine

SOCKET_PREFIX = "/_nicegui_ws"


class SocketOriginGuard:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket") and scope["path"].startswith(SOCKET_PREFIX):
            headers = {key.decode("latin-1").lower(): value.decode("latin-1")
                       for key, value in scope["headers"]}
            origin = headers.get("origin")
            if origin is not None and not this_machine.origin_is_ours(
                    origin, headers.get("host", "")):
                if scope["type"] == "websocket":
                    await receive()
                    await send({"type": "websocket.close", "code": 1008})
                else:
                    await PlainTextResponse("Forbidden", status_code=403)(scope, receive, send)
                return
        await self.app(scope, receive, send)
