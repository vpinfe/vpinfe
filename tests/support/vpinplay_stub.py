"""A stand-in for VPinPlay's own service: an id is available until claimed, and a claim's
empty send always takes. Used by any drive that has to claim a real account without
reaching the real service.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


class VPinPlayStub(BaseHTTPRequestHandler):
    """`taken` names ids nothing may claim; `unreachable` names ids answered as though
    the service could not be reached at all; every claim seen is kept in `claimed`, and
    an id already in it answers `/available` the same as one in `taken` - a real send
    against this stub is as good as a marker, the way it is against the real service."""

    taken: set[str] = set()
    unreachable: set[str] = set()
    claimed: list[dict] = []

    def _answer(self, status: int, body: dict) -> None:
        said = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(said)))
        self.end_headers()
        self.wfile.write(said)

    def do_GET(self) -> None:
        if "/available" in self.path:
            user_id = self.path.rsplit("/users/", 1)[-1].split("/", 1)[0]
            if user_id in type(self).unreachable:
                self._answer(503, {"detail": "down"})
                return
            registered = {str((one.get("client") or {}).get("userId") or "")
                         for one in type(self).claimed}
            self._answer(200, {"available": user_id not in type(self).taken
                               and user_id not in registered})
            return
        self._answer(404, {"detail": "none"})

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        if self.path.endswith("/sync"):
            type(self).claimed.append(body)
            self._answer(200, {"status": "ok"})
            return
        self._answer(404, {"detail": "none"})

    def log_message(self, *_args: Any) -> None:
        return


def start() -> ThreadingHTTPServer:
    """A running stub, on its own thread. `server_address[1]` is its port; `stop()` it
    when the drive is done."""
    VPinPlayStub.taken = set()
    VPinPlayStub.unreachable = set()
    VPinPlayStub.claimed = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), VPinPlayStub)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def stop(server: ThreadingHTTPServer) -> None:
    server.shutdown()
    server.server_close()


def endpoint(server: ThreadingHTTPServer) -> str:
    return f"http://127.0.0.1:{server.server_address[1]}"
