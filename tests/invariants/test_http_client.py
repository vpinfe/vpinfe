"""A host that says wait is not asked again until the time it gave."""

from __future__ import annotations

import logging
import os
import unittest
from datetime import datetime
from email.utils import formatdate
from typing import Any
from unittest import mock

import requests

from common import i18n
from common.security import http_client

NOW = 1_800_000_000.0
URL = "https://api.github.com/repos/o/r/releases/latest"


def _answer(status: int, headers: dict[str, str] | None = None) -> requests.Response:
    response = requests.Response()
    response.status_code = status
    response.headers.update(headers or {})
    response._content = b"{}"
    response._content_consumed = True
    response.url = URL
    return response


class GateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.now = NOW
        self.asked: list[str] = []
        patches: list[Any] = [
            mock.patch.dict(os.environ, {http_client.OFFLINE: ""}),
            mock.patch.dict(http_client._quiet, clear=True),
            mock.patch.object(http_client.time, "time", side_effect=lambda: self.now),
        ]
        for one in patches:
            one.start()
            self.addCleanup(one.stop)

    def _transport(self, *answers: requests.Response) -> Any:
        queue = list(answers)

        def get(url: str, **_: object) -> requests.Response:
            self.asked.append(url)
            return queue.pop(0)
        return mock.patch.object(http_client.requests, "get", side_effect=get)

    def _closed_until(self, answer: requests.Response) -> float:
        with self._transport(answer), self.assertLogs(http_client.logger, "WARNING") as said:
            with self.assertRaises(http_client.HostQuietError) as raised:
                http_client.get_json(URL)
        self.assertEqual(len(said.records), 1)
        self.assertIn("api.github.com", said.output[0])
        self.assertIs(raised.exception.response, answer)
        return raised.exception.until

    def test_a_spent_limit_waits_for_its_reset(self) -> None:
        until = self._closed_until(_answer(403, {"x-ratelimit-remaining": "0",
                                                 "x-ratelimit-reset": str(int(NOW) + 900)}))
        self.assertEqual(until, NOW + 900)

    def test_retry_after_in_seconds_waits_that_long(self) -> None:
        self.assertEqual(self._closed_until(_answer(429, {"retry-after": "120"})), NOW + 120)

    def test_retry_after_as_a_date_waits_until_it(self) -> None:
        stamp = formatdate(NOW + 300, usegmt=True)
        self.assertEqual(self._closed_until(_answer(403, {"retry-after": stamp})), NOW + 300)

    def test_retry_after_wins_over_the_reset(self) -> None:
        until = self._closed_until(_answer(429, {"retry-after": "30",
                                                 "x-ratelimit-remaining": "0",
                                                 "x-ratelimit-reset": str(int(NOW) + 900)}))
        self.assertEqual(until, NOW + 30)

    def test_a_429_carrying_neither_waits_a_minute(self) -> None:
        self.assertEqual(self._closed_until(_answer(429)), NOW + 60)

    def test_a_rate_limited_403_carrying_neither_time_waits_a_minute(self) -> None:
        until = self._closed_until(_answer(403, {"x-ratelimit-remaining": "12"}))
        self.assertEqual(until, NOW + 60)

    def test_a_reset_already_past_waits_a_minute(self) -> None:
        until = self._closed_until(_answer(403, {"x-ratelimit-remaining": "0",
                                                 "x-ratelimit-reset": str(int(NOW) - 5)}))
        self.assertEqual(until, NOW + 60)

    def test_a_bare_403_is_a_refusal_not_a_wait(self) -> None:
        with self._transport(_answer(403), _answer(200)):
            with self.assertRaises(requests.HTTPError) as raised:
                http_client.get_json(URL)
            self.assertNotIsInstance(raised.exception, http_client.HostQuietError)
            self.assertEqual(http_client.get_json(URL), {})
        self.assertEqual(len(self.asked), 2)

    def test_quiet_then_open(self) -> None:
        self._closed_until(_answer(429, {"retry-after": "120"}))
        with self._transport(_answer(200)):
            self.now = NOW + 119
            with self.assertNoLogs(http_client.logger, "WARNING"):
                with self.assertRaises(http_client.HostQuietError):
                    http_client.get_json(URL)
                with self.assertRaises(http_client.HostQuietError):
                    http_client.download_file(URL, mock.MagicMock())
            self.assertEqual(len(self.asked), 1)
            self.now = NOW + 120
            self.assertEqual(http_client.get_json(URL), {})
        self.assertEqual(len(self.asked), 2)

    def test_another_host_is_still_asked(self) -> None:
        self._closed_until(_answer(429))
        with self._transport(_answer(200)):
            self.assertEqual(http_client.get_json("https://raw.githubusercontent.com/x"), {})

    def test_the_wait_is_said_in_words(self) -> None:
        self.addCleanup(i18n.set_language, i18n.language())
        i18n.set_language("en")
        until = datetime(2026, 3, 1, 14, 5).timestamp()

        self.assertEqual(str(http_client.HostQuietError("api.github.com", until)),
                         "api.github.com asked VPinFE to wait until 14:05")


class OfflineTest(unittest.TestCase):
    """VPINFE_OFFLINE refuses every host but this machine, before any request."""

    def _asked(self, url: str) -> bool:
        with mock.patch.dict(os.environ, {http_client.OFFLINE: "1"}), \
                mock.patch.object(http_client.requests, "get",
                                  return_value=_answer(200)) as get:
            try:
                http_client.get_json(url)
            except http_client.OfflineError:
                pass
        return get.called

    def test_the_suite_runs_with_it_set(self) -> None:
        self.assertTrue(os.environ.get(http_client.OFFLINE))

    def test_a_host_outside_is_refused_as_a_connection_error(self) -> None:
        self.assertTrue(issubclass(http_client.OfflineError, requests.ConnectionError))
        for url in (URL, "http://192.168.1.20:8001/api/v1/update", "http://cab.lan/"):
            self.assertFalse(self._asked(url), url)

    def test_a_call_that_reads_the_status_is_refused_too(self) -> None:
        with mock.patch.dict(os.environ, {http_client.OFFLINE: "1"}), \
                mock.patch.object(http_client.requests, "get") as get, \
                mock.patch.object(http_client.requests, "post") as post:
            with self.assertRaises(http_client.OfflineError):
                http_client.get_response(URL)
            with self.assertRaises(http_client.OfflineError):
                http_client.post_response(URL, {})

        self.assertFalse(get.called or post.called)

    def test_a_transport_that_cannot_be_wrapped_asks_first(self) -> None:
        with mock.patch.dict(os.environ, {http_client.OFFLINE: "1"}):
            http_client.require_reachable("http://127.0.0.1:2112/files")
            with self.assertRaises(http_client.OfflineError):
                http_client.require_reachable("http://192.168.1.40:2112/files")

    def test_this_machine_is_asked(self) -> None:
        for url in ("http://127.0.0.1:8001/api/v1", "http://localhost:8001/",
                    "http://[::1]:8001/", "http://127.0.0.2/"):
            self.assertTrue(self._asked(url), url)


class UnreachableTest(unittest.TestCase):
    def _said(self, exc: requests.RequestException) -> list[Any]:
        log = mock.Mock()
        http_client.unreachable(log, "Could not ask", exc)
        return [call.args for call in log.log.call_args_list]

    def test_a_network_failure_is_one_warning_with_the_reason(self) -> None:
        down = requests.ConnectionError("no route to host")

        self.assertEqual(self._said(down),
                         [(logging.WARNING, "%s: %s", "Could not ask", down)])

    def test_a_refusal_is_debug(self) -> None:
        for exc in (http_client.HostQuietError("api.github.com", NOW),
                    http_client.OfflineError("api.github.com")):
            self.assertEqual(self._said(exc)[0][0], logging.DEBUG)


if __name__ == "__main__":
    unittest.main()
