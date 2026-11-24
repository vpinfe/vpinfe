"""What the frontend's own page asks core about recording: Exit's stop, the main menu's
Record item, and a recording shown in place for a decision, from the page and the API."""

from __future__ import annotations

import json
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from starlette.testclient import TestClient

import httpapi
from common import events, service_errors
from common.backups import folder as backup_folder
from common.capture import proposals, run, session, slots
from common.games import asset_origin, game_repository
from common.host import frontend_state, launch_state
from frontend import preview
from frontend.api import API
from tests.capture.test_run import FOLDER, GAME_ID, INFO, _Started
from tests.capture.test_runs import THREE, _Held

PLAN_NOTHING_MISSING = {"kinds": [{"kind": "playfield", "reason": None, "missing": 0,
                                   "have": 1}]}


class StopRecordingTests(_Held):
    def test_exit_s_stop_ends_the_run_throwing_the_game_in_hand_away(self) -> None:
        job = run.start(THREE)
        self.inside.wait(10)

        API.__new__(API).stop_recording()
        result = self.ran(job)

        self.assertEqual((result["run"]["state"], result["tables"]), ("stopped", []))
        self.assertIsNone(run.current())

    def test_a_run_already_gone_is_not_a_failure(self) -> None:
        self.assertEqual(API.__new__(API).stop_recording(), {"run": None})


class _Channel:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    def send_event_all_with_iframe(self, message: dict) -> None:
        self.sent.append(message)

    def shown(self) -> list[Any]:
        return [message["preview"] for message in self.sent
                if message["type"] == preview.MESSAGE]


class _Frontend(_Started):
    """The fake library with the frontend's windows up, a page on the game, and
    proposals kept in a folder of their own."""

    def setUp(self) -> None:
        super().setUp()
        held = tempfile.TemporaryDirectory()
        self.addCleanup(held.cleanup)
        patcher = patch("common.capture.proposals.ROOT", Path(held.name) / "proposals")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.recording = Path(held.name) / "recording.mp4"
        self.recording.write_bytes(b"recorded")

        frontend_state.reset_for_tests()
        self.addCleanup(frontend_state.reset_for_tests)
        preview.reset_for_tests()
        self.addCleanup(preview.reset_for_tests)
        self.channel = _Channel()
        preview.register(self.channel)  # type: ignore[arg-type]
        frontend_state.started("")

        game = game_repository.game_by_id(GAME_ID)
        self.page = API.__new__(API)
        for held_by in (patch.object(API, "entry_at",
                                     lambda _self, index: SimpleNamespace(game=game)
                                     if index == 0 else None),
                        patch("common.games.game_identity.game_id", return_value=GAME_ID)):
            held_by.start()
            self.addCleanup(held_by.stop)
        self.client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)

    def keep(self, kind: str = "playfield_video") -> str:
        return str(proposals.keep(GAME_ID, "", kind, self.recording)["id"])

    def waiting(self) -> list[str]:
        return [row["id"] for row in proposals.listing()["proposals"]]

    def on_show(self) -> frontend_state.Preview | None:
        return frontend_state.current().preview


class RecordOfferTests(_Frontend):
    def test_a_game_lacking_a_kind_is_offered_missing_only_then_replace(self) -> None:
        offer = self.page.recording_offer(0)

        self.assertEqual((offer["game_id"], offer["label"]), (GAME_ID, "Record Missing Media"))
        self.assertEqual([one["existing"] for one in offer["choices"]], ["fill", "choose"])
        self.assertEqual([one["label"] for one in offer["choices"]], ["Missing Only", "Replace"])

    def test_a_game_with_every_kind_is_offered_replace_alone(self) -> None:
        with patch("common.capture.run.plan", return_value=PLAN_NOTHING_MISSING):
            offer = self.page.recording_offer(0)

        self.assertEqual(offer["label"], "Record Media")
        self.assertEqual([one["existing"] for one in offer["choices"]], ["choose"])

    def test_a_kind_this_device_cannot_record_is_not_counted(self) -> None:
        blocked = {"kinds": [{"kind": "topper", "reason": {"key": "capture.screen.none"},
                              "missing": 1, "have": 0},
                             {"kind": "playfield", "reason": None, "missing": 0, "have": 1}]}
        with patch("common.capture.run.plan", return_value=blocked):
            offer = self.page.recording_offer(0)

        self.assertEqual(offer["label"], "Record Media")

    def test_a_device_that_records_nothing_offers_nothing(self) -> None:
        with patch("common.capture.preflight.report", return_value=self.blocked):
            self.assertEqual(self.page.recording_offer(0), {})

    def test_nothing_on_the_wheel_offers_nothing(self) -> None:
        self.assertEqual(self.page.recording_offer(3), {})

    def test_missing_only_records_the_game_with_the_device_s_settings(self) -> None:
        answer = self.page.record_media(0, "fill")

        job = self.finished(run.jobs.get(answer["job_id"]))
        assert isinstance(job.result, dict)
        self.assertEqual(job.result["tables"][0]["game_id"], GAME_ID)
        self.assertEqual(self.sessions[0].target.game_id, GAME_ID)

    def test_only_the_menu_s_two_choices_are_taken(self) -> None:
        self.assertEqual(self.page.record_media(0, "replace_all"), {})
        self.assertEqual(self.sessions, [])


class PreviewTests(_Frontend):
    def test_the_windows_are_told_what_to_draw_and_the_state_says_it(self) -> None:
        kept = self.keep()

        response = self.client.put("/frontend/preview", json={"proposal": kept})

        self.assertEqual(response.status_code, 202, response.text)
        shown = self.channel.shown()[-1]
        self.assertEqual((shown["proposal"], shown["showing"], shown["kind"], shown["label"],
                          shown["game_id"], shown["before"]),
                         (kept, "after", "playfield_video", "Playfield Video", GAME_ID, True))
        state = self.client.get("/frontend/state").json()["preview"]
        self.assertEqual((state["proposal"], state["showing"], state["game"]["id"]),
                         (kept, "after", GAME_ID))

    def test_left_and_right_show_the_file_there_now_and_the_recording(self) -> None:
        kept = self.keep()
        preview.show(kept)

        self.page.switch_preview("before")

        self.assertEqual(self.channel.shown()[-1]["showing"], "before")
        self.assertEqual(self.client.get("/frontend/state").json()["preview"]["showing"],
                         "before")

    def test_select_keeps_it_and_the_next_one_waiting_follows(self) -> None:
        first, second = self.keep("playfield_video"), self.keep("backglass")
        preview.show(first, series=[second])

        self.page.decide_preview(True)

        self.assertEqual(self.waiting(), [second])
        self.assertEqual(self.on_show().proposal, second)  # type: ignore[union-attr]
        self.assertEqual(slots.source(slots.serving(GAME_ID, "", "playfield_video")),
                         asset_origin.RECORDED)

    def test_a_choice_refused_for_want_of_a_backups_folder_says_so_and_keeps_the_proposal(
            self) -> None:
        info = {**INFO, "vpinfe": {"schema": 2, "game_id": GAME_ID}}
        (self.root / FOLDER / f"{FOLDER}.info").write_text(json.dumps(info), encoding="utf-8")
        first, second = self.keep("playfield_video"), self.keep("backglass")
        preview.show(first, series=[second])
        refusal = service_errors.BlockedError("the backups folder cannot be written to")

        with patch.object(backup_folder, "for_writing", side_effect=refusal):
            self.assertEqual(self.page.decide_preview(True), {})

        self.assertEqual(sorted(self.waiting()), sorted([first, second]))
        self.assertEqual(self.on_show().proposal, first)  # type: ignore[union-attr]
        self.assertEqual(self.channel.shown()[-1]["proposal"], first)
        self.assertEqual(self.channel.shown()[-1]["refused"],
                         "the backups folder cannot be written to")
        self.assertTrue((self.root / FOLDER / "medias" / "table.mp4").exists())
        self.page.decide_preview(False)
        self.assertEqual(self.on_show().proposal, second)  # type: ignore[union-attr]
        self.assertEqual(self.channel.shown()[-1]["refused"], "")

    def test_back_throws_it_away_and_the_last_one_ends_the_showing(self) -> None:
        kept = self.keep()
        preview.show(kept)

        self.page.decide_preview(False)

        self.assertEqual(self.waiting(), [])
        self.assertIsNone(self.on_show())
        self.assertIsNone(self.channel.shown()[-1])

    def test_exit_ends_the_showing_and_leaves_the_rest_waiting(self) -> None:
        first, second = self.keep(), self.keep("backglass")
        preview.show(first, series=[second])

        self.page.end_preview()

        self.assertIsNone(self.on_show())
        self.assertEqual(sorted(self.waiting()), sorted([first, second]))

    def test_a_decision_through_the_api_moves_the_windows_past_it(self) -> None:
        kept = self.keep()
        self.client.put("/frontend/preview", json={"proposal": kept, "showing": "before"})

        response = self.client.post(f"/capture/proposals/{kept}", json={"use": False})

        self.assertEqual(response.status_code, 200, response.text)
        self.assertIsNone(self.client.get("/frontend/state").json()["preview"])

    def test_delete_ends_it_and_is_never_a_failure(self) -> None:
        self.assertEqual(self.client.delete("/frontend/preview").status_code, 202)
        preview.show(self.keep())

        self.assertEqual(self.client.delete("/frontend/preview").status_code, 202)
        self.assertIsNone(self.on_show())

    def test_a_proposal_that_is_gone_is_not_found(self) -> None:
        response = self.client.put("/frontend/preview", json={"proposal": "a1b2c3d4e5f6"})

        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(self.channel.sent, [])

    def test_not_while_a_table_runs_nor_with_no_window_up(self) -> None:
        kept = self.keep()
        launch_state.set_launching("Cactus Canyon", source=launch_state.SOURCE_FRONTEND)
        self.assertEqual(self.client.put("/frontend/preview",
                                         json={"proposal": kept}).status_code, 409)
        launch_state.clear()
        frontend_state.stopped()
        self.assertEqual(self.client.put("/frontend/preview",
                                         json={"proposal": kept}).status_code, 409)
        self.assertEqual(self.channel.sent, [])

    def test_a_table_launched_ends_the_showing_and_leaves_it_waiting(self) -> None:
        kept = self.keep()
        preview.show(kept)

        events.emit(events.TABLE_LAUNCHING, source=launch_state.SOURCE_FRONTEND)

        self.assertIsNone(self.on_show())
        self.assertEqual(self.waiting(), [kept])

    def test_the_menu_s_run_shows_what_it_left_waiting_once_it_ends(self) -> None:
        def proposing(this: Any) -> session.Result:
            kept = self.keep(this.target.kinds[0])
            return session.Result(session.RECORDED, proposed=[
                {"kind": this.target.kinds[0], "id": kept}])

        heard = threading.Event()

        def changed(**payload: Any) -> None:
            if payload["state"]["preview"]:
                heard.set()

        events.subscribe(events.FRONTEND_STATE_CHANGED, changed)
        self.addCleanup(events.unsubscribe, events.FRONTEND_STATE_CHANGED, changed)
        with patch("common.capture.session.Session.run", new=proposing):
            self.page.record_media(0, "choose")
            self.assertTrue(heard.wait(10), "nothing was shown when the run ended")

        self.assertEqual(self.on_show().proposal, self.waiting()[0])  # type: ignore[union-attr]
