"""What a person says about several tables or games at once, written as one job.

Against a real library: each mark reaches the row it was meant for and nothing else, a
row that cannot take it is named while the rest are written, and an edit that names a
field its row cannot hold is refused before anything is.
"""

from __future__ import annotations

import configparser
import socket
import time
import unittest
from pathlib import Path
from typing import cast
from unittest import mock

from starlette.testclient import TestClient

import httpapi
from common import events
from common.extensions.games import withdraw_all
from common.games import game_repository, library_refresh, unreadable, unwritten
from common.games.formats.vpx_parser import VPXParser
from common.games.game_parser import GameParser
from common.games.library_folders import KIND_ROOT, Location
from common.runtime import jobs
from tests.support.library import TempTree, game_info, write_game

FIRST, SECOND = "Mark00000001", "Mark00000002"
FOLDERS = {FIRST: "Medieval Madness (Williams 1997)", SECOND: "Twilight Zone (Bally 1993)"}
EXTRA = "Medieval Madness Remix.vpx"

_LOOPBACK = ("127.0.0.1", "::1", "localhost")
_CONNECT = socket.socket.connect


def _offline(sock: socket.socket, address: object) -> None:
    """Refuse every connection but the loopback one: Windows builds an event loop's wake-up
    pair with a connect to itself, and the test client makes a loop for each request."""
    if isinstance(address, tuple) and address[0] in _LOOPBACK:
        return _CONNECT(sock, address)
    raise OSError("offline")


class MarksTests(TempTree):
    def setUp(self) -> None:
        super().setUp()
        for game_id, folder in FOLDERS.items():
            write_game(self.root, folder, info=game_info(
                folder.split(" (")[0], game_id=game_id,
                tables={f"{folder}.vpx": {"filename": f"{folder}.vpx"}}))
        (self.root / FOLDERS[FIRST] / EXTRA).write_bytes(b"not really a vpx")
        config = configparser.ConfigParser()
        config.read_dict({"Settings": {"gamerootdir": str(self.root)}, "Media": {}})

        held = mock.patch.object(
            game_repository.library_folders, "configured",
            return_value=[Location(location_id="test", path=str(self.root))])
        held.start()
        self.addCleanup(held.stop)
        previous = dict(game_repository._PARSERS)
        game_repository._PARSERS.clear()
        game_repository._PARSERS[(str(self.root), KIND_ROOT)] = GameParser(
            str(self.root), config)  # type: ignore[arg-type]
        self.addCleanup(game_repository._PARSERS.update, previous)
        self.addCleanup(game_repository._PARSERS.clear)

        for patcher in (
                mock.patch.object(VPXParser, "single_file_extract", self._read),
                mock.patch("socket.socket.connect", _offline),
                mock.patch("common.games.media_fill.request"),
                mock.patch.dict(unwritten._HELD, clear=True),
                mock.patch.dict(unreadable._FAILED, clear=True)):
            patcher.start()
            self.addCleanup(patcher.stop)
        library_refresh.refresh()

        events.clear()
        self.addCleanup(events.clear)
        withdraw_all()
        self.addCleanup(withdraw_all)
        self.client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)

    @staticmethod
    def _read(_parser: object, path: str) -> dict[str, str]:
        return {"filename": Path(path).name, "filehash": "f00d", "rom": "sample"}

    def _tables(self) -> dict[str, dict]:
        return {row["id"]: row for row in self.client.get("/tables").json()["tables"]}

    def _first_game_tables(self) -> list[str]:
        return [one for one, row in self._tables().items() if row["game_id"] == FIRST]

    def _game(self, game_id: str) -> dict:
        return self.client.get(f"/games/{game_id}").json()

    def _put(self, edits: list[dict]):
        return self.client.put("/library/marks", json={"edits": edits})

    def _result(self, response) -> dict:
        self.assertEqual(202, response.status_code, response.text)
        job_id = response.json()["id"]
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            job = jobs.get(job_id)
            if job is not None and job.state != jobs.RUNNING:
                self.assertIsNone(job.error, job.error)
                return cast(dict, job.result)
            time.sleep(0.02)
        self.fail("the marks job did not finish")

    def test_a_game_takes_its_four_marks(self) -> None:
        result = self._result(self._put([
            {"game_id": SECOND, "rating": 4, "favorite": True, "private": True,
             "tags": ["Classic"]}]))

        self.assertEqual([{"game": SECOND, "table": ""}], result["written"])
        game = self._game(SECOND)
        self.assertEqual(4, game["rating"])
        self.assertIs(True, game["private"])
        self.assertEqual(["Classic"], game["user"]["tags"])
        self.assertIs(True, game["user"]["favorite"])
        self.assertEqual(0, self._game(FIRST)["rating"])

    def test_a_table_takes_its_marks_and_its_sibling_does_not(self) -> None:
        mine, other = sorted(self._first_game_tables())[:2]

        result = self._result(self._put([
            {"game_id": FIRST, "table_id": mine, "rating": 3, "hidden": True,
             "delete_nvram_on_close": True, "tags": ["Keeper"]}]))

        self.assertEqual([{"game": FIRST, "table": mine}], result["written"])
        tables = self._tables()
        self.assertEqual(3, tables[mine]["rating"])
        self.assertIs(True, tables[mine]["hidden"])
        self.assertIs(True, tables[mine]["delete_nvram_on_close"])
        self.assertEqual(["Keeper"], tables[mine]["user"]["tags"])
        self.assertEqual(0, tables[other]["rating"])
        self.assertIs(False, tables[other]["hidden"])
        self.assertIs(False, tables[other]["delete_nvram_on_close"])

    def test_a_mark_put_back_reads_as_it_did(self) -> None:
        (mine, *_rest) = self._first_game_tables()
        self._result(self._put([{"game_id": FIRST, "table_id": mine, "hidden": True}]))

        self._result(self._put([{"game_id": FIRST, "table_id": mine, "hidden": False}]))

        self.assertIs(False, self._tables()[mine]["hidden"])

    def test_a_row_that_cannot_be_written_is_named_and_the_rest_go_on(self) -> None:
        (mine, *_rest) = self._first_game_tables()

        result = self._result(self._put([
            {"game_id": FIRST, "table_id": "not-a-table", "rating": 2},
            {"game_id": FIRST, "table_id": mine, "rating": 2},
            {"game_id": SECOND, "rating": 5}]))

        self.assertEqual({(FIRST, mine), (SECOND, "")},
                         {(one["game"], one["table"]) for one in result["written"]})
        (missed,) = result["failed"]
        self.assertEqual(("not-a-table", True), (missed["table"], bool(missed["reason"])))
        self.assertEqual(2, self._tables()[mine]["rating"])
        self.assertEqual(5, self._game(SECOND)["rating"])

    def test_a_field_the_row_cannot_hold_is_refused_before_anything_is_written(self) -> None:
        (mine, *_rest) = self._first_game_tables()

        for edit in ({"game_id": SECOND, "hidden": True},
                     {"game_id": FIRST, "table_id": mine, "favorite": True}):
            response = self._put([{"game_id": SECOND, "rating": 5}, edit])

            self.assertEqual(400, response.status_code)
            self.assertEqual(0, self._game(SECOND)["rating"])

    def test_nothing_named_is_refused(self) -> None:
        self.assertEqual(400, self._put([]).status_code)

    def test_a_second_selection_while_one_is_written_is_refused(self) -> None:
        with jobs.track(jobs.KIND_MARKS):
            response = self._put([{"game_id": SECOND, "rating": 1}])

        self.assertEqual(409, response.status_code)


if __name__ == "__main__":
    unittest.main()
