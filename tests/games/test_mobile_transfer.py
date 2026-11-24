"""Sending a game to a device that runs VPX but not VPinFE.

Driven against a stand-in that speaks the device's own protocol, because the protocol
*is* the contract here: there is no library on the far side to check against, and a
transfer that puts the right bytes at the wrong offsets looks exactly like one that
worked until somebody tries to play the table.
"""

from __future__ import annotations

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock
from urllib.parse import parse_qs, urlparse

from common.games import mobile_transfer
from common.i18n import t


class _Device(BaseHTTPRequestHandler):
    """Enough of VPX Mobile's web server to be wrong against."""

    folders: dict[str, dict[str, bytearray]] = {}
    refreshed = 0
    refuse = ""

    def log_message(self, *_args) -> None:
        pass

    def _query(self) -> dict:
        return {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}

    def _done(self, status: int = 200, body: bytes = b"") -> None:
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - the base class names it
        if urlparse(self.path).path != "/files":
            return self._done(404)
        listing = [{"name": name, "isDir": True} for name in _Device.folders]
        listing.append({"name": "notes.txt", "isDir": False})
        self._done(body=json.dumps(listing).encode())

    def do_POST(self) -> None:  # noqa: N802
        where = urlparse(self.path).path
        query = self._query()
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        if _Device.refuse and _Device.refuse in where:
            return self._done(500)
        if where == "/folder":
            _Device.folders.setdefault(query.get("q", ""), {})
            return self._done()
        if where == "/upload":
            folder = _Device.folders.setdefault(query.get("q", ""), {})
            held = folder.setdefault(query.get("file", ""), bytearray())
            at = int(query.get("offset") or 0)
            if len(held) < at:
                held.extend(b"\0" * (at - len(held)))
            held[at:at + len(body)] = body
            return self._done()
        if where == "/delete":
            _Device.folders.pop(query.get("q", ""), None)
            return self._done()
        if where == "/command":
            _Device.refreshed += 1
            return self._done()
        self._done(404)


class MobileTransferTests(unittest.TestCase):
    def setUp(self) -> None:
        _Device.folders = {}
        _Device.refreshed = 0
        _Device.refuse = ""
        self.server = HTTPServer(("127.0.0.1", 0), _Device)
        host, port = self.server.server_address[:2]
        self.host, self.port = str(host), int(port)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.server.shutdown)
        self.addCleanup(self.server.server_close)

        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.game = Path(self.tmp.name) / "Alpha (Bally 1991)"
        (self.game / "medias").mkdir(parents=True)
        (self.game / "Alpha.vpx").write_bytes(b"table bytes")
        (self.game / "medias" / "wheel.png").write_bytes(b"art")
        (self.game / "Alpha (Bally 1991).info").write_text(
            json.dumps({"Info": {"Name": "Alpha"}, "VPinFE": {}, "User": {}}))

    def _send(self, **rest) -> int:
        return mobile_transfer.send(self.game, self.host, self.port, **rest)

    def test_the_files_arrive_whole(self) -> None:
        """The point of the offsets. A chunked upload that reassembles wrongly looks
        exactly like one that worked until somebody opens the table."""
        self._send()

        held = _Device.folders["Alpha (Bally 1991)"]
        self.assertEqual(bytes(held["Alpha.vpx"]), b"table bytes")

    def test_a_file_larger_than_a_chunk_still_arrives_whole(self) -> None:
        """Every byte of a table crosses in pieces, and the last one is short."""
        whole = bytes(range(256)) * 40
        (self.game / "Alpha.vpx").write_bytes(whole)

        self._send(chunk_bytes=100)

        self.assertEqual(bytes(_Device.folders["Alpha (Bally 1991)"]["Alpha.vpx"]),
                         whole)

    def test_what_is_sent_is_what_an_export_holds(self) -> None:
        """Not decided here. A game sent to a device and the same game exported as a
        file carry the same things, because two answers to "what belongs in an export"
        is the whole reason this reuses that one.

        Which means the default carries the table and its metadata and no art: `medias`
        rides along only when the whole folder is asked for.
        """
        self._send()
        lean = set(_Device.folders["Alpha (Bally 1991)"])

        _Device.folders = {}
        self._send(everything=True)
        full = set(_Device.folders["Alpha (Bally 1991)"])

        self.assertEqual(lean, {"Alpha.vpx", "Alpha (Bally 1991).info"})
        self.assertEqual(full - lean, {"medias/wheel.png"})

    def test_the_folder_is_made_before_anything_lands_in_it(self) -> None:
        """A device reads a folder as soon as it appears; files loose at the top level
        are not a game."""
        self._send()

        self.assertIn("Alpha (Bally 1991)", _Device.folders)
        self.assertNotIn("Alpha.vpx", _Device.folders)

    def test_the_table_lands_before_the_media(self) -> None:
        """So it shows up named rather than as an entry with nothing in it."""
        sent = mobile_transfer._ordered(
            [(Path("x"), "medias/wheel.png"), (Path("y"), "Alpha.vpx")])

        self.assertEqual([one for _, one in sent], ["Alpha.vpx", "medias/wheel.png"])

    def test_the_device_is_told_to_look_again(self) -> None:
        self._send()

        self.assertEqual(_Device.refreshed, 1)

    def test_it_says_what_it_is_carrying_and_ignores_loose_files(self) -> None:
        """A file at the top level is not a game, and offering one would offer a delete
        that does nothing."""
        self._send()

        self.assertEqual(mobile_transfer.carried(self.host, self.port),
                         ["Alpha (Bally 1991)"])

    def test_removing_takes_it_off(self) -> None:
        self._send()

        mobile_transfer.remove("Alpha (Bally 1991)", self.host, self.port)

        self.assertEqual(mobile_transfer.carried(self.host, self.port), [])

    def test_a_device_that_refuses_says_so_rather_than_reporting_success(self) -> None:
        _Device.refuse = "/upload"

        with self.assertLogs("vpinfe.common.games.mobile_transfer", "WARNING") as logged, \
                self.assertRaises(mobile_transfer.DeviceUnreachableError) as raised:
            self._send()

        self.assertEqual(str(raised.exception), t("device.reason.rejected"))
        self.assertIn("/upload: 500", logged.output[0])

    def test_a_device_that_is_not_there_says_so(self) -> None:
        with mock.patch("common.games.mobile_transfer.time.sleep") as slept, \
                self.assertRaises(mobile_transfer.DeviceUnreachableError) as raised:
            # Windows retries a closed loopback port for about two seconds before refusing.
            mobile_transfer.carried("127.0.0.1", 9, timeout=10)

        self.assertEqual(str(raised.exception), t("device.reason.refused"))
        slept.assert_not_called()

    def test_a_device_on_the_network_is_not_asked_while_offline(self) -> None:
        with mock.patch("common.games.mobile_transfer.time.sleep") as slept, \
                mock.patch("common.games.mobile_transfer.http.client.HTTPConnection") as opened, \
                self.assertRaises(mobile_transfer.DeviceUnreachableError):
            mobile_transfer.remove("Alpha", "192.168.1.40", 2112)

        opened.assert_not_called()
        slept.assert_not_called()

    def test_everything_but_the_listing_retries_a_dropped_connection(self) -> None:
        with mock.patch("common.games.mobile_transfer.time.sleep") as slept, \
                self.assertRaises(mobile_transfer.DeviceUnreachableError):
            mobile_transfer.remove("Alpha", "127.0.0.1", 9)

        self.assertEqual([call.args[0] for call in slept.call_args_list], [2, 4])

    def test_the_settings_file_stays_home_unless_it_is_asked_for(self) -> None:
        (self.game / "Alpha.ini").write_text("[Player]", encoding="utf-8")

        self._send()
        left = set(_Device.folders["Alpha (Bally 1991)"])
        _Device.folders = {}
        self._send(settings=True)
        asked = set(_Device.folders["Alpha (Bally 1991)"])

        self.assertNotIn("Alpha.ini", left)
        self.assertIn("Alpha.ini", asked)

    def test_a_mobile_settings_file_arrives_as_the_settings_file(self) -> None:
        (self.game / "Alpha.ini").write_text("own", encoding="utf-8")
        (self.game / "Alpha.mobile.ini").write_text("for mobile", encoding="utf-8")

        self._send(mobile_word="mobile")

        held = _Device.folders["Alpha (Bally 1991)"]
        self.assertEqual(bytes(held["Alpha.ini"]), b"for mobile")
        self.assertNotIn("Alpha.mobile.ini", held)

    def test_a_game_with_nothing_to_send_is_refused_before_anything_is_made(self) -> None:
        """Otherwise an empty folder appears on the device and reads as a broken game."""
        empty = Path(self.tmp.name) / "Empty"
        empty.mkdir()

        with self.assertRaises(ValueError) as raised:
            mobile_transfer.send(empty, self.host, self.port)

        self.assertEqual(str(raised.exception),
                         t("error.devices.nothing_sendable", game="Empty"))

        self.assertEqual(_Device.folders, {})
