"""Writing where a backglass file places the DMD, over the API.

The file is shared by every table that reads it, so what is pinned is that a change splices
only `<Displays>`, leaves nothing beside the file, is refused while a table plays, and can be
put back once while the file is as the change left it.
"""

from __future__ import annotations

import os
import sys
import threading
import unittest
from unittest.mock import patch

from starlette.testclient import TestClient

import httpapi
from common.games import backglass_displays
from common.games.formats import directb2s_displays, directb2s_parser
from common.host import launch_state
from common.media import sized_media
from tests.api.test_backglass_picture import FOLDER, GAME_ID, _directb2s
from tests.support.library import TempTree, fake_game, write_game

PUT = f"/games/{GAME_ID}/assets/backglass/displays"
UNDO = f"{PUT}/undo"
FILE = "t1.directb2s"
BOX = {"target": "Backglass", "x": 4, "y": 5, "w": 20, "h": 6}


class DisplaysRoutes(TempTree):
    def setUp(self) -> None:
        super().setUp()
        info = {"Info": {"Title": "Example"},
                "vpinfe": {"schema": 2, "id": GAME_ID, "default_table": "t1"},
                "tables": {"t1": {"id": "t1", "filename": "t1.vpx"}}}
        self.folder = write_game(
            self.root, FOLDER, info=info, vpx=False,
            files={"t1.vpx": b"vpx", FILE: _directb2s(scoreview=(24, 8)),
                   "Broken.directb2s": b"not a backglass at all", "notes.txt": b"hello"})
        game = fake_game(self.folder, FOLDER, meta=info)
        self.running = launch_state.LaunchState()
        for patcher in (patch("common.games.game_repository.catalog",
                              return_value={GAME_ID: game}),
                        patch.object(sized_media, "ROOT", self.root / "sized"),
                        patch.object(directb2s_parser, "ROOT", self.root / "glass"),
                        patch.object(launch_state, "current",
                                     side_effect=lambda: self.running)):
            patcher.start()
            self.addCleanup(patcher.stop)
        directb2s_parser._facts.clear()
        self.addCleanup(directb2s_parser._facts.clear)
        self.client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)
        self.path = self.folder / FILE
        self.original = self.path.read_bytes()

    def _detail(self, path: str) -> dict:
        response = self.client.get(f"/games/{GAME_ID}/assets/detail", params={"path": path})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def put(self, body: dict | None = None, path: str = FILE):
        return self.client.put(PUT, params={"path": path}, json=body or BOX)

    def written(self) -> dict:
        response = self.put()
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()


class WriteTests(DisplaysRoutes):
    def test_a_file_without_a_dmd_reports_none(self) -> None:
        self.assertEqual(self._detail(FILE)["backglass"]["displays"], [])

    def test_the_dmd_is_added_and_the_detail_reports_it(self) -> None:
        written = self.written()

        self.assertEqual(written["displays"], [BOX])
        self.assertEqual(self._detail(FILE)["backglass"]["displays"], [BOX])

    def test_only_the_displays_element_is_added(self) -> None:
        self.written()

        after = self.path.read_bytes()

        self.assertTrue(after.startswith(self.original[:-len(b"</DirectB2SData>\n")]))
        self.assertTrue(after.endswith(b"</DirectB2SData>\n"))
        self.assertIn(b'<DMD Target="Backglass" X="4" Y="5" W="20" H="6" />', after)
        self.assertEqual(after.replace(
            b'  <Displays>\n    <DMD Target="Backglass" X="4" Y="5" W="20" H="6" />\n'
            b"  </Displays>\n", b""), self.original)

    def test_the_version_changes_with_the_file(self) -> None:
        before = self._detail(FILE)["backglass"]["version"]

        written = self.written()

        self.assertNotEqual(written["version"], before)
        self.assertEqual(self._detail(FILE)["backglass"]["version"], written["version"])

    def test_nothing_is_left_beside_the_file(self) -> None:
        before = sorted(one.name for one in self.folder.iterdir())

        self.written()

        self.assertEqual(sorted(one.name for one in self.folder.iterdir()), before)

    @unittest.skipIf(sys.platform == "win32", "Windows has no modes")
    def test_the_file_keeps_its_mode(self) -> None:
        self.path.chmod(0o640)

        self.written()

        self.assertEqual(self.path.stat().st_mode & 0o777, 0o640)

    def test_a_second_write_moves_the_dmd_it_added(self) -> None:
        self.written()

        moved = self.put({**BOX, "x": 9, "y": 9}).json()

        self.assertEqual(moved["displays"], [{**BOX, "x": 9, "y": 9}])
        self.assertEqual(self.path.read_bytes().count(b"<DMD "), 1)

    def test_no_numbers_takes_the_dmd_out(self) -> None:
        self.written()

        removed = self.put({"target": "Backglass"})

        self.assertEqual(removed.status_code, 200, removed.text)
        self.assertEqual(removed.json()["displays"], [])
        self.assertNotIn(b"<DMD ", self.path.read_bytes())

    def test_taking_out_what_is_not_there_writes_nothing(self) -> None:
        before = self.path.stat().st_mtime_ns

        removed = self.put({"target": "Backglass"}).json()

        self.assertEqual((removed["displays"], removed["undo"]), ([], ""))
        self.assertEqual(self.path.stat().st_mtime_ns, before)

    def test_a_dmd_of_the_score_view_is_its_own(self) -> None:
        self.written()

        both = self.put({**BOX, "target": "ScoreView", "x": 1}).json()

        self.assertEqual({one["target"] for one in both["displays"]},
                         {"Backglass", "ScoreView"})

    def test_part_of_a_box_is_refused(self) -> None:
        response = self.put({"target": "Backglass", "x": 1, "y": 2})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_a_box_without_a_size_is_refused(self) -> None:
        response = self.put({**BOX, "w": 0})

        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_a_window_that_does_not_exist_is_refused(self) -> None:
        self.assertEqual(self.put({**BOX, "target": "Marquee"}).status_code, 400)
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_only_a_backglass_file_is_written(self) -> None:
        self.assertEqual(self.put(path="notes.txt").status_code, 400)
        self.assertEqual(self.put(path="../escape.directb2s").status_code, 400)
        self.assertEqual(self.put(path="nothing.directb2s").status_code, 404)

    def test_a_file_that_is_not_a_b2s_file_is_refused_and_left_alone(self) -> None:
        broken = self.folder / "Broken.directb2s"

        response = self.put(path="Broken.directb2s")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(broken.read_bytes(), b"not a backglass at all")


class PlayingTests(DisplaysRoutes):
    def test_nothing_is_written_while_a_table_of_the_game_plays(self) -> None:
        self.running = launch_state.LaunchState(launching=True, game_id=GAME_ID)

        response = self.put()

        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_another_game_playing_does_not_stop_it(self) -> None:
        self.running = launch_state.LaunchState(launching=True, game_id="Other")

        self.assertEqual(self.put().status_code, 200)

    def test_nothing_is_put_back_while_a_table_plays_and_the_undo_stays(self) -> None:
        token = self.written()["undo"]
        self.running = launch_state.LaunchState(launching=True, game_id=GAME_ID)

        self.assertEqual(self.client.post(UNDO, json={"undo": token}).status_code, 409)

        self.running = launch_state.LaunchState()
        self.assertEqual(self.client.post(UNDO, json={"undo": token}).status_code, 200)


class UndoTests(DisplaysRoutes):
    def test_undo_puts_the_file_back_byte_for_byte(self) -> None:
        token = self.written()["undo"]

        undone = self.client.post(UNDO, json={"undo": token})

        self.assertEqual(undone.status_code, 200, undone.text)
        self.assertEqual(undone.json()["displays"], [])
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_undo_of_a_move_puts_the_first_place_back(self) -> None:
        self.written()
        moved = self.put({**BOX, "x": 9}).json()
        after_first = self.path.read_bytes()

        self.client.post(UNDO, json={"undo": moved["undo"]})

        self.assertEqual(self._detail(FILE)["backglass"]["displays"], [BOX])
        self.assertNotEqual(self.path.read_bytes(), after_first)

    def test_undo_of_a_removal_brings_the_dmd_back(self) -> None:
        self.written()
        removed = self.put({"target": "Backglass"}).json()

        self.client.post(UNDO, json={"undo": removed["undo"]})

        self.assertEqual(self._detail(FILE)["backglass"]["displays"], [BOX])

    def test_a_change_is_undone_once(self) -> None:
        token = self.written()["undo"]
        self.client.post(UNDO, json={"undo": token})

        self.assertEqual(self.client.post(UNDO, json={"undo": token}).status_code, 404)

    def test_a_token_nobody_gave_is_not_found(self) -> None:
        self.assertEqual(self.client.post(UNDO, json={"undo": "nope"}).status_code, 404)

    def test_another_game_cannot_use_the_token(self) -> None:
        token = self.written()["undo"]

        response = self.client.post("/games/Other/assets/backglass/displays/undo",
                                    json={"undo": token})

        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.client.post(UNDO, json={"undo": token}).status_code, 200)

    def test_a_file_changed_since_is_left_as_it_is(self) -> None:
        token = self.written()["undo"]
        self.path.write_bytes(self.path.read_bytes() + b"<!-- edited -->")
        edited = self.path.read_bytes()

        response = self.client.post(UNDO, json={"undo": token})

        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.path.read_bytes(), edited)


SINGLE_LINE = (b'<DirectB2SData Version="1.2"><Name Value="Taxi" />'
               b"<Images><BackglassImage Value=\"QUJD\" /></Images></DirectB2SData>")


class BoundsTests(DisplaysRoutes):
    def test_a_position_or_size_outside_the_limits_is_refused(self) -> None:
        for change in ({"x": -1}, {"y": -1}, {"x": 100001}, {"y": 100001},
                       {"w": 100001}, {"h": 100001}, {"w": 0}, {"h": 0}):
            with self.subTest(change=change):
                response = self.put({**BOX, **change})

                self.assertEqual(response.status_code, 422, response.text)
                self.assertEqual(self.path.read_bytes(), self.original)

    def test_the_limits_themselves_are_written(self) -> None:
        response = self.put({"target": "Backglass", "x": 0, "y": 100000, "w": 100000, "h": 1})

        self.assertEqual(response.status_code, 200, response.text)

    def test_the_service_refuses_a_box_too_when_it_is_called_without_the_route(self) -> None:
        with self.assertRaises(Exception) as caught:
            backglass_displays.place(GAME_ID, FILE, "Backglass", (0, 0, 100001, 1))

        self.assertEqual(type(caught.exception).__name__, "RefusedError")


class UnknownKeyTests(DisplaysRoutes):
    def test_a_key_the_body_does_not_name_is_refused_and_takes_nothing_out(self) -> None:
        self.written()
        kept = self.path.read_bytes()

        for body in ({"target": "Backglass", "width": 5},
                     {"target": "Backglass", "X": 1, "Y": 2, "W": 3, "H": 4},
                     {"target": "Backglass", "extra": None}):
            with self.subTest(body=body):
                response = self.put(body)

                self.assertEqual(response.status_code, 422, response.text)
                self.assertEqual(self.path.read_bytes(), kept)

    def test_a_body_of_the_target_alone_still_takes_the_dmd_out(self) -> None:
        self.written()

        self.assertEqual(self.put({"target": "Backglass"}).status_code, 200)
        self.assertNotIn(b"<DMD ", self.path.read_bytes())


class FailedWriteTests(DisplaysRoutes):
    def _names(self) -> list[str]:
        return sorted(one.name for one in self.folder.iterdir())

    def test_a_write_that_fails_leaves_the_file_and_no_temp_file(self) -> None:
        before = self._names()

        with patch("common.games.backglass_displays.os.fsync", side_effect=OSError("disk")):
            response = self.put()

        self.assertNotEqual(response.status_code, 200)
        self.assertEqual(self._names(), before)
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_a_replace_that_fails_leaves_no_temp_file(self) -> None:
        before = self._names()

        with patch("common.atomic_write.os.replace", side_effect=OSError("locked")):
            response = self.put()

        self.assertNotEqual(response.status_code, 200)
        self.assertEqual(self._names(), before)

    @unittest.skipIf(sys.platform == "win32", "Windows has no modes")
    def test_the_mode_is_applied_to_the_file_after_it_is_replaced_not_to_the_staged_one(
            self) -> None:
        self.path.chmod(0o640)
        seen: list[bool] = []
        real = os.replace

        def spy(source: object, target: object) -> None:
            seen.append(os.access(str(source), os.W_OK))
            real(source, target)  # type: ignore[arg-type]

        with patch("common.atomic_write.os.replace", spy):
            self.written()

        self.assertEqual((seen, self.path.stat().st_mode & 0o777), ([True], 0o640))


class ReadOnlyTests(DisplaysRoutes):
    def setUp(self) -> None:
        super().setUp()
        self.path.chmod(0o444)
        self.addCleanup(self.path.chmod, 0o644)

    def test_a_read_only_file_is_refused_and_left_alone(self) -> None:
        before = sorted(one.name for one in self.folder.iterdir())

        response = self.put()

        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(self.path.read_bytes(), self.original)
        self.assertEqual(sorted(one.name for one in self.folder.iterdir()), before)

    def test_nor_is_it_undone_into(self) -> None:
        self.path.chmod(0o644)
        token = self.written()["undo"]
        self.path.chmod(0o444)
        written = self.path.read_bytes()

        response = self.client.post(UNDO, json={"undo": token})

        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(self.path.read_bytes(), written)


class ConcurrentTests(DisplaysRoutes):
    def test_two_windows_written_at_once_both_stay(self) -> None:
        real = directb2s_displays.place
        meeting = threading.Barrier(2, timeout=0.4)

        def slow(data: bytes, window: str, where: object) -> bytes:
            """Both writers read the file before either writes, unless one waits for the other."""
            out = real(data, window, where)  # type: ignore[arg-type]
            try:
                meeting.wait()
            except threading.BrokenBarrierError:
                pass
            return out

        failures: list[BaseException] = []

        def write(window: str, x: int) -> None:
            try:
                backglass_displays.place(GAME_ID, FILE, window, (x, 1, 10, 10))
            except BaseException as exc:  # noqa: BLE001
                failures.append(exc)

        with patch.object(directb2s_displays, "place", slow):
            threads = [threading.Thread(target=write, args=(window, x))
                       for window, x in (("Backglass", 3), ("ScoreView", 4))]
            for one in threads:
                one.start()
            for one in threads:
                one.join()

        self.assertEqual(failures, [])
        held = {one["target"]: one["x"] for one in self._detail(FILE)["backglass"]["displays"]}
        self.assertEqual(held, {"Backglass": 3, "ScoreView": 4})

    def test_the_playing_check_is_made_with_the_files_lock_held(self) -> None:
        real = backglass_displays.file_move.playing
        locked: list[bool] = []

        def playing(game: object, game_id: str) -> bool:
            lock = backglass_displays._file_lock(self.path)
            locked.append(lock.locked())
            return real(game, game_id)  # type: ignore[arg-type]

        with patch.object(backglass_displays.file_move, "playing", playing):
            self.written()

        self.assertEqual(locked, [True])


class ByteExactUndoTests(DisplaysRoutes):
    def _file(self, name: str, data: bytes):
        (self.folder / name).write_bytes(data)
        return name

    def test_files_of_every_shape_come_back_to_the_byte(self) -> None:
        shapes = {
            "one.directb2s": SINGLE_LINE,
            "tail.directb2s": b"<DirectB2SData><Images></Images></DirectB2SData>",
            "bom.directb2s": b"\xef\xbb\xbf" + self.original,
            "bom_one.directb2s": b"\xef\xbb\xbf" + SINGLE_LINE,
            "crlf.directb2s": self.original.replace(b"\n", b"\r\n"),
            "none.directb2s": SINGLE_LINE.rstrip(),
        }
        for name, data in shapes.items():
            with self.subTest(name):
                self._file(name, data)

                first = self.put(BOX, path=name)
                moved = self.put({**BOX, "x": 99}, path=name)
                gone = self.put({"target": "Backglass"}, path=name)

                self.assertEqual(first.status_code, 200, first.text)
                for token in (gone.json()["undo"], moved.json()["undo"], first.json()["undo"]):
                    self.assertEqual(self.client.post(UNDO, json={"undo": token}).status_code,
                                     200)
                self.assertEqual((self.folder / name).read_bytes(), data)

    def test_an_older_change_is_refused_until_the_newer_is_undone_and_says_so(self) -> None:
        older = self.written()
        newer = self.put({**BOX, "x": 9}).json()

        refused = self.client.post(UNDO, json={"undo": older["undo"]})

        self.assertEqual(refused.status_code, 409)
        self.assertEqual(refused.json()["error"]["details"]["reason"], "superseded")
        self.assertEqual(self.client.post(UNDO, json={"undo": newer["undo"]}).status_code, 200)
        self.assertEqual(self.client.post(UNDO, json={"undo": older["undo"]}).status_code, 200)
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_a_file_edited_since_says_changed_not_superseded(self) -> None:
        token = self.written()["undo"]
        self.path.write_bytes(self.path.read_bytes() + b"<!-- edited -->")

        refused = self.client.post(UNDO, json={"undo": token})

        self.assertEqual(refused.json()["error"]["details"]["reason"], "changed")

    def test_the_token_stays_after_a_refusal(self) -> None:
        older = self.written()
        newer = self.put({**BOX, "x": 9}).json()
        self.client.post(UNDO, json={"undo": older["undo"]})

        self.assertEqual(self.client.post(UNDO, json={"undo": newer["undo"]}).status_code, 200)


class JournalTests(DisplaysRoutes):
    def setUp(self) -> None:
        super().setUp()
        backglass_displays._done.clear()
        self.addCleanup(backglass_displays._done.clear)

    def test_the_journal_drops_the_oldest_when_the_stored_bytes_pass_the_limit(self) -> None:
        with patch.object(backglass_displays, "_MAX_BYTES", 120):
            first = self.written()
            removed = self.put({"target": "Backglass"}).json()

        sizes = [one.size for one in backglass_displays._done.values()]
        self.assertLessEqual(sum(sizes), 120)
        self.assertEqual(self.client.post(UNDO, json={"undo": first["undo"]}).status_code, 404)
        self.assertEqual(self.client.post(UNDO, json={"undo": removed["undo"]}).status_code,
                         200)

    def test_a_change_over_the_limit_is_written_and_says_it_cannot_be_undone(self) -> None:
        with patch.object(backglass_displays, "_MAX_BYTES", 10):
            written = self.written()

        self.assertEqual((written["undo"], written["undo_refused"]), ("", "too_large"))
        self.assertEqual(written["displays"], [BOX])
        self.assertEqual(backglass_displays._done, {})

    def test_a_kept_change_says_nothing_of_it(self) -> None:
        self.assertEqual(self.written()["undo_refused"], "")

    def test_a_move_keeps_only_what_it_changed(self) -> None:
        self.written()
        self.put({**BOX, "w": 21})

        (_, latest) = list(backglass_displays._done.items())[-1]

        self.assertLess(latest.size, 10)
