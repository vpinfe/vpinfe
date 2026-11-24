"""The Example Scores site, small enough to read, for the guide's extensions to talk to.

Standard library only. Run it with `python practice_site.py` and it answers on
127.0.0.1:8790. It accepts the key `practice-key` in an `X-API-Key` header and keeps its
scores in memory, so stopping it forgets what was posted.

    GET  /status   open to anyone: {"open": true}
    GET  /check    200 with the right key, 401 without
    GET  /top      the scores, best first, with the right key
    POST /scores   {"player", "table", "score"}, and a "vps_id" if it has one, with the right
                   key; 201
    GET  /scores/<id>        open to anyone: that score, as a page a link can go to
    POST /scores/<id>/flag   with the right key: marks the score flagged
    DELETE /scores/<id>      with the right key: removes the score

Every score has an `id`, a `vps_id` (empty when the poster named only the table), a
`posted` time, a `status` of `verified` or `pending`, and a `flagged` flag.  A score posted
here is `pending`.

A test starts it with `make_server(0)` and reads the port the system chose.
"""

from __future__ import annotations

import itertools
import json
import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

HOST = "127.0.0.1"
PORT = 8790
KEY = "practice-key"

SCORES: list[dict[str, Any]] = [
    {"id": "s1", "player": "AAA", "table": "Medieval Madness", "vps_id": "practice-medieval",
     "score": 1_240_000_000, "posted": "2026-10-01T19:30:00Z", "status": "verified",
     "flagged": False},
    {"id": "s2", "player": "BEE", "table": "Attack from Mars", "vps_id": "9Paf7-CL",
     "score": 980_500_000, "posted": "2026-10-03T21:05:00Z", "status": "verified",
     "flagged": False},
    {"id": "s3", "player": "CJ", "table": "The Addams Family", "vps_id": "aT_GONvw",
     "score": 310_000_000, "posted": "2026-10-08T18:40:00Z", "status": "pending",
     "flagged": False},
]


def _handler(scores: list[dict[str, Any]]) -> type[BaseHTTPRequestHandler]:
    counter = itertools.count(len(scores) + 1)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args: Any) -> None:
            pass

        def _send(self, status: int, body: Any) -> None:
            data = json.dumps(body).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _signed_in(self) -> bool:
            if self.headers.get("X-API-Key") == KEY:
                return True
            self._send(401, {"error": "The key was not accepted"})
            return False

        def do_GET(self) -> None:  # noqa: N802 - the name http.server looks for
            if self.path == "/status":
                self._send(200, {"open": True})
            elif self.path == "/check" and self._signed_in():
                self._send(200, {"ok": True})
            elif self.path == "/top" and self._signed_in():
                self._send(200, sorted(scores, key=lambda one: -one["score"]))
            elif self.path.startswith("/scores/") and self._one(self.path[8:]):
                self._send(200, self._one(self.path[8:]))
            elif self.path not in ("/check", "/top"):
                self._send(404, {"error": "No such page"})

        def _one(self, score_id: str) -> dict[str, Any] | None:
            return next((one for one in scores if one["id"] == score_id), None)

        def do_DELETE(self) -> None:  # noqa: N802
            found = self._one(self.path[8:]) if self.path.startswith("/scores/") else None
            if found is None:
                self._send(404, {"error": "No such score"})
            elif self._signed_in():
                scores.remove(found)
                self._send(200, {"ok": True})

        def do_POST(self) -> None:  # noqa: N802
            flagging = self.path.startswith("/scores/") and self.path.endswith("/flag")
            found = self._one(self.path[8:-5]) if flagging else None
            if flagging and found is None:
                self._send(404, {"error": "No such score"})
            elif flagging and found is not None and self._signed_in():
                found["flagged"] = True
                self._send(200, {"ok": True})
            elif flagging:
                pass
            elif self.path != "/scores":
                self._send(404, {"error": "No such page"})
            elif self._signed_in():
                size = int(self.headers.get("Content-Length") or 0)
                try:
                    posted = json.loads(self.rfile.read(size) or b"{}")
                    scores.append({"id": f"s{next(counter)}", "player": str(posted["player"]),
                                   "table": str(posted["table"]),
                                   "vps_id": str(posted.get("vps_id") or ""),
                                   "score": int(posted["score"]),
                                   "posted": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                                   "status": "pending", "flagged": False})
                except (ValueError, KeyError):
                    self._send(400, {"error": "A score needs player, table and score"})
                else:
                    self._send(201, {"ok": True})

    return Handler


def make_server(port: int = PORT) -> ThreadingHTTPServer:
    """A server on `port` (0 for any free one) with its own copy of the sample scores."""
    return ThreadingHTTPServer((HOST, port), _handler([dict(one) for one in SCORES]))


def serve_in_background(port: int = 0) -> ThreadingHTTPServer:
    """`make_server`, answering on a thread of its own. `shutdown()` and `server_close()`
    end it."""
    server = make_server(port)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


if __name__ == "__main__":
    print(f"Example Scores on http://{HOST}:{PORT}  (key: {KEY})")
    make_server().serve_forever()
