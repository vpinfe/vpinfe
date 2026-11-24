"""A picture replaced while it is being sent still arrives as one whole file."""

from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from typing import Any

from starlette.requests import Request

from common.media.sized_media import Served
from httpapi import responses

LONG = b"L" * 82
SHORT = b"S" * 80


def _request(*headers: tuple[str, str]) -> Request:
    return Request({"type": "http", "method": "GET", "path": "/", "query_string": b"",
                    "headers": [(k.encode(), v.encode()) for k, v in headers]})


def _sent(response: Any, *headers: tuple[str, str]) -> tuple[int, dict[str, str], bytes]:
    """Run `response` as a server would and return its status, headers and body."""
    sent: list[dict] = []

    async def receive() -> dict:
        # A server's receive waits for the client to go; one that answers at once keeps a
        # FileResponse listening for the disconnect forever, because it never yields.
        await asyncio.Event().wait()
        return {"type": "http.disconnect"}

    async def send(message: dict) -> None:
        sent.append(message)

    asyncio.run(response({"type": "http", "method": "GET",
                          "headers": [(k.encode(), v.encode()) for k, v in headers]},
                         receive, send))
    start = next(m for m in sent if m["type"] == "http.response.start")
    seen = {k.decode(): v.decode() for k, v in start["headers"]}
    return start["status"], seen, b"".join(m.get("body", b"") for m in sent)


class ArtIsSentWhole(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / "picture.webp"
        self.path.write_bytes(LONG)

    def replace_with(self, content: bytes) -> None:
        staged = self.path.with_name("staged")
        staged.write_bytes(content)
        os.replace(staged, self.path)

    def test_a_replacement_between_the_headers_and_the_body_is_not_a_short_body(self) -> None:
        response = responses.media_file(Served(self.path, "now"), _request(), "")
        self.replace_with(SHORT)

        _, headers, body = _sent(response)

        self.assertEqual(int(headers["content-length"]), len(body))

    def test_a_version_kept_for_good_is_sent_whole_too(self) -> None:
        response = responses.media_file(Served(self.path, "now"), _request(), "now")
        self.replace_with(SHORT)

        _, headers, body = _sent(response)

        self.assertEqual(int(headers["content-length"]), len(body))
        self.assertEqual(headers["cache-control"], responses.FOREVER)

    def test_the_etag_names_the_bytes_that_were_sent(self) -> None:
        first = responses.media_file(Served(self.path, "now"), _request(), "")
        self.replace_with(SHORT)
        _, headers, body = _sent(first)
        again = responses.media_file(Served(self.path, "now"), _request(), "")

        _, newer, newer_body = _sent(again)

        self.assertEqual((headers["etag"] == newer["etag"]), (body == newer_body))

    def test_the_etag_it_gave_is_answered_with_a_304(self) -> None:
        _, headers, _ = _sent(responses.media_file(Served(self.path, "now"), _request(), ""))

        status, _, body = _sent(responses.media_file(
            Served(self.path, "now"), _request(("if-none-match", headers["etag"])), ""))

        self.assertEqual((status, body), (304, b""))

    def test_a_range_is_still_answered_as_one(self) -> None:
        request = _request(("range", "bytes=0-9"))

        status, _, body = _sent(responses.media_file(Served(self.path, "now"), request, ""),
                                ("range", "bytes=0-9"))

        self.assertEqual((status, body), (206, LONG[:10]))


if __name__ == "__main__":
    unittest.main()
