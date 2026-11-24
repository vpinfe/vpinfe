"""What `why()` puts under a failure's message."""

from __future__ import annotations

import errno
import socket
import sqlite3
import unittest
from contextlib import closing
from email.message import Message
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

import requests

from common import i18n
from common.failures import upstream, why
from common.security.http_client import HostQuietError
from tests.support.library import TempTree
from tests.support.skips import needs_posix_permissions


def _answered(status: int) -> requests.HTTPError:
    response = requests.Response()
    response.status_code = status
    response.reason = "Refused"
    response.url = "https://catalog.example/data.json"
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        return exc
    raise AssertionError(status)


class WhyTests(TempTree):
    def setUp(self) -> None:
        super().setUp()
        self.addCleanup(i18n.set_language, i18n.language())
        i18n.set_language("en")

    @needs_posix_permissions
    def test_a_folder_it_may_not_write_names_the_path(self) -> None:
        locked = self.root / "locked"
        locked.mkdir(mode=0o500)
        self.addCleanup(locked.chmod, 0o700)

        with self.assertRaises(PermissionError) as raised:
            (locked / "one.txt").write_text("x", encoding="utf-8")

        self.assertEqual(why(raised.exception),
                         f"VPinFE does not have permission for {locked / 'one.txt'}")

    def test_a_file_failure_with_no_path_says_it_plainly(self) -> None:
        self.assertEqual(why(OSError(errno.ENOSPC, "No space left on device")),
                         "The disk is full")

    def test_nothing_at_a_path(self) -> None:
        with self.assertRaises(FileNotFoundError) as raised:
            (self.root / "gone.vpx").read_bytes()

        self.assertEqual(why(raised.exception), f"Nothing is at {self.root / 'gone.vpx'}")

    def test_a_port_nothing_listens_on(self) -> None:
        with socket.socket() as free:
            free.bind(("127.0.0.1", 0))
            port = free.getsockname()[1]

        with self.assertRaises(requests.ConnectionError) as raised:
            requests.get(f"http://127.0.0.1:{port}/", timeout=5)

        self.assertEqual(why(raised.exception), "Nothing answers at 127.0.0.1")

    def test_a_host_that_did_not_answer_in_time(self) -> None:
        request = requests.Request("GET", "https://catalog.example/data.json").prepare()

        self.assertEqual(why(requests.ReadTimeout(request=request)),
                         "catalog.example did not answer in time")

    def test_what_a_host_answered(self) -> None:
        self.assertEqual([why(_answered(status)) for status in (404, 403, 503, 400)],
                         ["catalog.example does not have it", "catalog.example refused it",
                          "catalog.example is having trouble", "catalog.example refused it"])

    def test_a_link_that_leads_back_to_itself(self) -> None:
        loop = self.root / "Loop.vpx"
        try:
            loop.symlink_to(loop)
        except (OSError, NotImplementedError):
            self.skipTest("this platform will not make a symlink")

        with self.assertRaises(OSError) as raised:
            loop.open("wb")

        self.assertEqual([why(raised.exception),
                          why(OSError(errno.ELOOP, "Too many levels of symbolic links"))],
                         [f"The link at {loop} leads back to itself",
                          "That link leads back to itself"])

    def test_urllib_is_read_by_what_it_wraps(self) -> None:
        with socket.socket() as free:
            free.bind(("127.0.0.1", 0))
            port = free.getsockname()[1]

        with self.assertRaises(URLError) as raised:
            urlopen(f"http://127.0.0.1:{port}/", timeout=5)

        self.assertEqual([why(raised.exception), why(URLError(TimeoutError("timed out"))),
                          why(URLError("unknown url type: gopher"))],
                         ["Nothing answers there", "It did not answer in time",
                          "unknown url type: gopher"])

    def test_what_a_host_answered_over_urllib(self) -> None:
        url = "https://catalog.example/data.json"
        self.assertEqual([why(HTTPError(url, status, "Refused", Message(), None))
                          for status in (404, 403, 503, 400, 418)],
                         ["catalog.example does not have it", "catalog.example refused it",
                          "catalog.example is having trouble", "catalog.example refused it",
                          "HTTP Error 418: Refused"])

    def test_urllib_is_worded_by_where_the_caller_was_reaching(self) -> None:
        with socket.socket() as free:
            free.bind(("127.0.0.1", 0))
            port = free.getsockname()[1]
        url = f"http://127.0.0.1:{port}/api/v1/tables"

        with self.assertRaises(URLError) as raised:
            urlopen(url, timeout=5)

        self.assertEqual([why(raised.exception, at=url),
                          why(URLError(TimeoutError("timed out")), at="https://api.example/x"),
                          why(URLError(socket.gaierror(8, "nodename")),
                              at="https://api.example/x")],
                         ["Nothing answers at 127.0.0.1", "api.example did not answer in time",
                          "api.example could not be reached"])

    def test_a_file_failure_is_worded_by_the_path_the_caller_gives(self) -> None:
        self.assertEqual([why(OSError(errno.EACCES, "Permission denied"), at=self.root / "a"),
                          why(OSError(errno.ENOENT, "No such file"), at="C:\\Tables\\a.vpx")],
                         [f"VPinFE does not have permission for {self.root / 'a'}",
                          "Nothing is at C:\\Tables\\a.vpx"])

    def test_where_the_exception_says_it_was_wins(self) -> None:
        request = requests.Request("GET", "https://catalog.example/data.json").prepare()

        self.assertEqual([why(requests.ReadTimeout(request=request), at="https://api.example/"),
                          why(FileNotFoundError(errno.ENOENT, "gone", "/own/one.vpx"),
                              at="/elsewhere.vpx")],
                         ["catalog.example did not answer in time", "Nothing is at /own/one.vpx"])

    def test_a_url_is_not_a_path_and_a_path_is_not_a_host(self) -> None:
        self.assertEqual([why(OSError(errno.ENOENT, "No such file"), at="https://api.example/x"),
                          why(ConnectionRefusedError(), at="/tables/a.vpx"),
                          why(ConnectionRefusedError(), at="http://[::1")],
                         ["Nothing is there", "Nothing answers there", "Nothing answers there"])

    def test_a_host_that_asked_to_wait(self) -> None:
        said = why(HostQuietError("api.example", 0.0))

        self.assertTrue(said.startswith("api.example asked VPinFE to wait until "), said)

    def test_a_database_that_would_not_open_is_said_by_its_code(self) -> None:
        elsewhere = self.root / "no" / "such.db"
        notes = self.root / "notes.db"
        notes.write_bytes(b"not a database at all, just some text" * 4)
        said: list[str] = []

        def ask() -> None:
            with closing(sqlite3.connect(notes)) as database:
                database.execute("select 1 from Games")

        for attempt in (lambda: sqlite3.connect(elsewhere), ask):
            with self.assertRaises(sqlite3.Error) as raised:
                attempt()
            said.append(why(raised.exception))

        self.assertEqual(said, ["The database could not be opened", "That file is not a database"])

    def test_a_database_error_with_no_code_is_still_a_sentence(self) -> None:
        self.assertEqual([why(sqlite3.Error("odd")),
                          why(sqlite3.OperationalError("database is locked"))],
                         ["The database could not be used", "The database could not be used"])

    def test_which_side_failed(self) -> None:
        with self.assertRaises(URLError) as raised:
            urlopen((self.root / "gone.json").as_uri(), timeout=5)
        request = requests.Request("GET", "https://catalog.example/data.json").prepare()

        self.assertEqual(
            [upstream(one) for one in (
                OSError(errno.EACCES, "Permission denied"), OSError(errno.ENOSPC, "Full"),
                OSError(errno.EIO, "Input/output error"), raised.exception,
                requests.ReadTimeout(request=request), _answered(503),
                URLError(ConnectionRefusedError()), URLError("unknown url type: gopher"),
                socket.gaierror(8, "nodename"), OSError(errno.EHOSTUNREACH, "No route"))],
            [False, False, False, False, True, True, True, True, True, True])
        self.assertEqual(why(raised.exception), f"Nothing is at {self.root / 'gone.json'}")

    def test_anything_else_is_the_exception_s_own_text(self) -> None:
        self.assertEqual([why(ValueError("Unexpected end of archive")), why(KeyError()),
                          why(OSError(errno.EIO, "Input/output error")), why(_answered(418))],
                         ["Unexpected end of archive", "KeyError",
                          "[Errno 5] Input/output error",
                          "418 Client Error: Refused for url: https://catalog.example/data.json"])


if __name__ == "__main__":
    unittest.main()
