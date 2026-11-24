"""What the frontend is showing, read and followed from outside, and switched from either end.

The windows here are simulated: a message the API sends them is applied the way core's
handler applies it, and the controller reports the wheel the way core reports it.
"""

from __future__ import annotations

import configparser
from types import SimpleNamespace
from unittest.mock import patch

from starlette.testclient import TestClient

import httpapi
from common import events
from common.games.collection_store import CollectionStore
from common.host import frontend_state
from frontend import showing
from frontend.api import API
from frontend.library_resolver import LibraryResolver
from httpapi import events as event_stream
from tests.support.library import TempTree
from tests.support.library_loader import start_library_of


def _game(gid: str, title: str) -> SimpleNamespace:
    return SimpleNamespace(
        game_dir_name=title, full_path_game=f"/games/{title}",
        full_path_vpx_file=f"/games/{title}/{title}.vpx", creation_time=0,
        pup_pack_exists=False, alt_color_exists=False, alt_sound_exists=False,
        meta_config={"Info": {"Title": title}, "User": {},
                     "vpinfe": {"game_id": gid},
                     "tables": {f"{gid}t": {"id": f"{gid}t", "filename": f"{title}.vpx"}}})


def _ini() -> SimpleNamespace:
    parser = configparser.ConfigParser()
    parser.add_section("general")
    return SimpleNamespace(config=parser, save=lambda: None)


class _Channel:
    """The device channel, as far as the frontend's side of this reaches into it."""

    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.windows_changed = lambda _count: None

    def on_windows_changed(self, callback) -> None:
        self.windows_changed = callback

    def send_event_all_with_iframe(self, message: dict) -> None:
        self.sent.append(message)


class FrontendStateTests(TempTree):
    def setUp(self) -> None:
        super().setUp()
        events.clear()
        self.addCleanup(events.clear)
        frontend_state.reset_for_tests()
        self.addCleanup(frontend_state.reset_for_tests)
        event_stream.reset()
        self.addCleanup(event_stream.reset)

        self.collections = CollectionStore(str(self.root / "collections.json"))
        held = patch("frontend.library_resolver.get_collections_manager",
                     lambda: self.collections)
        held.start()
        self.addCleanup(held.stop)
        self.collections.add_collection("Friday Night", ["mm", "afm"])
        self.collections.add_collection("Empty", [])

        games = [_game("afm", "Attack from Mars"), _game("mm", "Medieval Madness"),
                 _game("taf", "The Addams Family")]
        start_library_of(self, games)
        self.library = LibraryResolver(_ini(), games=games)
        self.window = API(_ini(), window_name="table", library=self.library)  # type: ignore[arg-type]
        self.channel = _Channel()
        showing.register(self.channel, self.library)  # type: ignore[arg-type]

        self.published: list[dict] = []
        events.subscribe(events.FRONTEND_STATE_CHANGED, self._heard)
        self.client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)

    def _heard(self, **payload) -> None:
        self.published.append(
            event_stream.STREAMED_EVENTS[events.FRONTEND_STATE_CHANGED](**payload))

    def _read(self) -> dict:
        response = self.client.get("/frontend/state")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def _windows_apply(self) -> None:
        """Every window does what core's handler does with each message sent."""
        for message in self.channel.sent:
            if message["type"] == "TableDataChange":
                if message["collection"] == "None":
                    self.window.get_tables(reset=True)
                else:
                    self.window.set_tables_by_collection(message["collection"])
                    self.window.get_tables()
            self.window.notify_table_selected(message["index"])
        self.channel.sent.clear()

    def _assert_agree(self) -> dict:
        read = self._read()
        self.assertEqual({"state": read}, self.published[-1])
        return read

    def test_a_switch_at_the_screen_is_read_and_heard_alike(self) -> None:
        self.channel.windows_changed(1)
        self.window.set_tables_by_collection("Friday Night")
        self.window.notify_table_selected(1)

        read = self._assert_agree()
        self.assertEqual(read["collection"], "Friday Night")
        self.assertEqual(read["game"]["id"], "mm")
        self.assertEqual(read["game"]["links"], {"self": "/api/v1/games/mm"})

    def test_a_switch_from_outside_is_read_and_heard_alike(self) -> None:
        self.channel.windows_changed(1)
        self.window.notify_table_selected(2)

        response = self.client.put("/frontend/collection", json={"name": "Friday Night"})

        self.assertEqual(response.status_code, 202, response.text)
        self.assertEqual(self.channel.sent, [{"type": "TableDataChange", "index": 0,
                                              "collection": "Friday Night"}])
        self._windows_apply()
        read = self._assert_agree()
        self.assertEqual(read["collection"], "Friday Night")
        self.assertEqual(read["game"]["id"], "afm")

    def test_the_whole_library_goes_out_as_the_name_core_already_sends(self) -> None:
        self.channel.windows_changed(1)
        self.window.set_tables_by_collection("Friday Night")

        self.client.put("/frontend/collection", json={"name": ""})

        self.assertEqual(self.channel.sent[0]["collection"], "None")
        self._windows_apply()
        self.assertEqual(self._assert_agree()["collection"], "")

    def test_moving_the_wheel_sends_where_the_game_sits_on_screen(self) -> None:
        self.channel.windows_changed(1)
        self.window.set_tables_by_collection("Friday Night")
        self.window.notify_table_selected(0)

        response = self.client.put("/frontend/game", json={"id": "mm"})

        self.assertEqual(response.status_code, 202, response.text)
        self.assertEqual(self.channel.sent,
                         [{"type": "TableIndexUpdate", "index": 1, "id": "mmt"}])
        self._windows_apply()
        self.assertEqual(self._assert_agree()["game"]["id"], "mm")

    def test_a_game_the_collection_on_screen_does_not_hold_is_not_found(self) -> None:
        self.channel.windows_changed(1)
        self.window.set_tables_by_collection("Friday Night")

        response = self.client.put("/frontend/game", json={"id": "taf"})

        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(self.channel.sent, [])

    def test_a_collection_that_does_not_exist_is_not_found(self) -> None:
        self.channel.windows_changed(1)

        response = self.client.put("/frontend/collection", json={"name": "Nope"})

        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(self.channel.sent, [])

    def test_an_empty_collection_leaves_nothing_on_the_wheel(self) -> None:
        self.channel.windows_changed(1)
        self.window.notify_table_selected(0)

        self.client.put("/frontend/collection", json={"name": "Empty"})
        self.window.set_tables_by_collection("Empty")
        self.window.get_tables()

        read = self._assert_agree()
        self.assertEqual(read["collection"], "Empty")
        self.assertIsNone(read["game"])

    def test_with_no_window_up_there_is_nothing_to_switch(self) -> None:
        self.channel.windows_changed(1)
        self.window.notify_table_selected(0)
        self.channel.windows_changed(0)

        self.assertEqual(self._assert_agree(),
                         {"running": False, "collection": "", "game": None,
                          "preview": None})
        for path, body in (("/frontend/collection", {"name": ""}),
                           ("/frontend/game", {"id": "afm"})):
            with self.subTest(path=path):
                self.assertEqual(self.client.put(path, json=body).status_code, 409)
        self.assertEqual(self.channel.sent, [])

    def test_a_second_window_does_not_forget_the_game(self) -> None:
        self.channel.windows_changed(1)
        self.window.notify_table_selected(2)
        self.channel.windows_changed(2)

        self.assertEqual(self._read()["game"]["id"], "mm")

    def test_a_new_subscriber_is_given_the_state(self) -> None:
        self.channel.windows_changed(1)
        self.window.notify_table_selected(0)

        snapshot = event_stream._snapshots[events.FRONTEND_STATE_CHANGED]()

        self.assertEqual(snapshot, {"state": self._read()})
