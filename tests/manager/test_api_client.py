"""What the Manager's API client raises when nothing answers it."""

from __future__ import annotations

import socket
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import requests

from common.i18n import t
from manager.kit import acting
from manager.kit.api import ApiClient, ApiError


def _closed_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class NothingAnsweredTests(unittest.TestCase):
    def test_nothing_on_the_port_is_said_in_words(self) -> None:
        client = ApiClient(f"http://127.0.0.1:{_closed_port()}")

        with self.assertRaises(ApiError) as caught:
            client.discovery()

        self.assertEqual(str(caught.exception), t("device.reason.refused"))
        self.assertIsInstance(caught.exception.__cause__, requests.ConnectionError)

    def test_an_answer_that_never_came_says_it_may_be_asleep(self) -> None:
        with patch.object(requests.adapters.HTTPAdapter, "send",
                          side_effect=requests.ReadTimeout()), \
                self.assertRaises(ApiError) as caught:
            ApiClient("http://127.0.0.1:1").set_favorite("g1", True)

        self.assertEqual(str(caught.exception), t("device.reason.timed_out"))


class HowLongEachReadWaitsTests(unittest.TestCase):
    @staticmethod
    def _waited(call) -> float:
        seen: dict = {}

        def send(_adapter, _request, **kwargs):
            seen.update(kwargs)
            response = requests.Response()
            response.status_code = 200
            response._content = b"{}"
            return response

        with patch.object(requests.adapters.HTTPAdapter, "send", send):
            call(ApiClient("http://127.0.0.1:1"))
        return seen["timeout"]

    def test_reading_the_whole_library_waits_for_a_cold_share(self) -> None:
        for read in (lambda c: c.games(), lambda c: c.all_tables(),
                     lambda c: c.all_media(), lambda c: c.all_assets(),
                     lambda c: c.filter_axes(), lambda c: c.library_entries(),
                     lambda c: c.tags(), lambda c: c.locations(),
                     lambda c: c.library_game_collections(),
                     lambda c: c.collection_games("c"), lambda c: c.collection_entries("c"),
                     lambda c: c.info_maintenance()):
            self.assertGreater(self._waited(read), 60)

    def test_a_question_about_one_thing_still_gives_up_quickly(self) -> None:
        self.assertEqual(self._waited(lambda c: c.game("g1")), 15)

    def test_sending_a_file_waits_as_long_as_importing_one(self) -> None:
        """The write can finish after a short wait ends, and the person is then told a
        placed file failed."""
        for send in (lambda c: c.place_media("g1", "", "wheel", "a.png", b"x"),
                     lambda c: c.place_asset("g1", "", "pov", "a.pov", b"x")):
            self.assertGreater(self._waited(send), 60)


class GameOffersTests(unittest.TestCase):
    @staticmethod
    def _requested(call) -> str:
        seen: list[str] = []

        def send(_adapter, request, **_kwargs):
            seen.append(request.url)
            response = requests.Response()
            response.status_code = 200
            response._content = b"{}"
            return response

        with patch.object(requests.adapters.HTTPAdapter, "send", send):
            call(ApiClient("http://127.0.0.1:1"))
        return seen[0]

    def test_a_games_own_entry_is_asked_with_the_kinds_alone(self) -> None:
        url = self._requested(lambda c: c.game_offers("g 1", "", "", ["wheel", "topper"]))

        self.assertEqual("http://127.0.0.1:1/api/v1/games/g%201/offers?kind=wheel&kind=topper", url)

    def test_another_entry_is_named_with_its_name(self) -> None:
        url = self._requested(lambda c: c.game_offers("g1", "v&1", "Attack & Co", ["wheel"]))

        self.assertEqual("http://127.0.0.1:1/api/v1/games/g1/offers"
                         "?kind=wheel&vps_id=v%261&name=Attack+%26+Co", url)


class _Slow(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:
        time.sleep(0.1)
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *args) -> None:
        pass


class _Listening(ThreadingHTTPServer):
    request_queue_size = 128


class EveryWorkerAtOnceTests(unittest.TestCase):
    def test_every_worker_calling_at_once_keeps_its_connection(self) -> None:
        server = _Listening(("127.0.0.1", 0), _Slow)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base = f"http://127.0.0.1:{server.server_address[1]}"
        client = ApiClient(base)
        pool = acting.ActingPool()
        self.addCleanup(pool.shutdown)

        with self.assertNoLogs("urllib3.connectionpool", "WARNING"):
            calls = [pool.submit(client._session.get, f"{base}/x")
                     for _ in range(acting.WORKERS * 3)]
            for call in calls:
                call.result()

    def test_the_workers_are_the_number_the_client_has_connections_for(self) -> None:
        self.assertEqual(acting.ActingPool()._max_workers, acting.WORKERS)


if __name__ == "__main__":
    unittest.main()
