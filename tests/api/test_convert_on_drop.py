"""A picture, sound or video the slot cannot read is claimed for it, offered a choice of what
to make it, and converted before anything is written.

What is worth pinning: the default is the recommendation, the choice is the sender's, Don't
Convert keeps the file untouched, a file nothing has a place for is offered unticked and kept
as it is, and a stopped or failed import adds nothing.
"""

from __future__ import annotations

import io
import time
import unittest
from unittest.mock import patch

from PIL import Image

from common.games import media_convert
from common.runtime import jobs
from common.uploads import drop_convert, upload_session_service
from tests.api.test_uploads_arrival import _Api


def _tga() -> bytes:
    held = io.BytesIO()
    Image.new("RGB", (64, 48), (30, 120, 200)).save(held, "TGA")
    return held.getvalue()


def _medias(case: _Api) -> set[str]:
    return {name.split("/", 1)[1] for name in case._files() if name.startswith("medias/")}


def _row(plan: dict, member: str) -> dict:
    return next(one for one in plan["items"] if one["member"] == member)


class ConvertOnDropTests(_Api):
    def test_a_tga_on_a_declared_wheel_is_claimed_with_its_choice_row(self) -> None:
        upload_id = self._session("wheel.tga", _tga())

        plan = self._plan(upload_id, media_kind="wheel").json()

        row = plan["items"][0]
        self.assertEqual("wheel.tga", row["name"])
        self.assertTrue(row["destination"].endswith(".png"))
        self.assertTrue(row["default_enabled"])
        view = row["convert"]
        self.assertEqual("picture", view["kind"])
        self.assertEqual("TGA", view["from"])
        self.assertEqual("png", view["to"])
        recommended = [one["value"] for one in view["options"] if one["recommended"]]
        self.assertEqual(["png"], recommended)
        self.assertEqual("asis", view["options"][-1]["value"])
        self.assertFalse(plan["convert"]["reencodes"])

    def test_the_recommendation_is_what_is_written(self) -> None:
        upload_id = self._session("wheel.tga", _tga())

        done = self._import(upload_id, media_kind="wheel")

        self.assertEqual(200, done.status_code, done.text)
        written = _medias(self)
        self.assertEqual(1, len(written))
        name = next(iter(written))
        self.assertTrue(name.endswith(".png"), name)
        with Image.open(self.folder / "medias" / name) as made:
            self.assertEqual("PNG", made.format)
            self.assertEqual((64, 48), made.size)

    def test_the_senders_choice_is_the_format_written(self) -> None:
        upload_id = self._session("wheel.tga", _tga())

        done = self._import(upload_id, media_kind="wheel",
                            convert={"rows": {"wheel.tga": {"to": "webp"}}})

        self.assertEqual(200, done.status_code, done.text)
        self.assertTrue(next(iter(_medias(self))).endswith(".webp"))

    def test_a_preferred_format_is_what_the_row_starts_at_and_what_is_written(self) -> None:
        upload_id = self._session("wheel.tga", _tga())
        held = media_convert.convert_preferences.Preferences(picture="webp")

        with patch.object(media_convert.convert_preferences, "read", return_value=held):
            view = self._plan(upload_id, media_kind="wheel").json()["items"][0]["convert"]
            done = self._import(upload_id, media_kind="wheel")

        self.assertEqual("webp", view["to"])
        self.assertEqual({"webp"}, {one["value"] for one in view["options"] if one["preferred"]})
        self.assertEqual(["png"], [one["value"] for one in view["options"] if one["recommended"]])
        self.assertEqual(200, done.status_code, done.text)
        self.assertTrue(next(iter(_medias(self))).endswith(".webp"))

    def test_without_a_preference_no_option_is_preferred(self) -> None:
        upload_id = self._session("wheel.tga", _tga())

        view = self._plan(upload_id, media_kind="wheel").json()["items"][0]["convert"]

        self.assertFalse([one for one in view["options"] if one["preferred"]])

    def test_dont_convert_keeps_the_file_untouched_under_the_slots_name(self) -> None:
        upload_id = self._session("wheel.tga", _tga())
        convert = {"rows": {"wheel.tga": {"to": "asis"}}}

        plan = self._plan(upload_id, media_kind="wheel", convert=convert).json()
        done = self._import(upload_id, media_kind="wheel", convert=convert)

        self.assertTrue(plan["items"][0]["destination"].endswith(".tga"))
        self.assertEqual(200, done.status_code, done.text)
        name = next(iter(_medias(self)))
        self.assertTrue(name.endswith(".tga"), name)
        self.assertEqual(_tga(), (self.folder / "medias" / name).read_bytes())

    def test_leave_out_leaves_the_file_out(self) -> None:
        upload_id = self._session("wheel.tga", _tga())

        done = self._import(upload_id, media_kind="wheel", selected=[])

        self.assertEqual(422, done.status_code)
        self.assertEqual(set(), _medias(self))

    def test_a_file_named_for_a_slot_is_claimed_where_it_lands_without_a_slot(self) -> None:
        upload_id = self._drop({"(Wheel) Multi.tga": _tga(), "table.vpx": b"x"})

        plan = self._plan(upload_id).json()

        row = _row(plan, "(Wheel) Multi.tga")
        self.assertEqual("wheel", row["media_kind"])
        self.assertEqual("named", row["basis"])
        self.assertEqual("png", row["convert"]["to"])

    def test_a_file_the_slot_cannot_read_is_a_suggestion_not_a_guess(self) -> None:
        upload_id = self._drop({"art.tga": _tga(), "table.vpx": b"x"})

        plan = self._plan(upload_id).json()

        self.assertEqual(["art.tga"], [one["member"] for one in plan["unclaimed"]])
        slots = [c["media_kind"] for c in plan["unclaimed"][0]["choices"]]
        self.assertIn("wheel", slots)

    def test_a_file_with_no_place_is_an_unticked_row_kept_as_it_is(self) -> None:
        upload_id = self._drop({"(Wheel) Multi.png": _png(), "Manual.docx": b"words"})

        plan = self._plan(upload_id).json()

        row = _row(plan, "Manual.docx")
        self.assertFalse(row["default_enabled"])
        self.assertEqual("other", row["basis"])
        self.assertEqual([], plan["left_out"])
        ticked = [one["index"] for one in plan["items"]]

        done = self._import(upload_id, selected=ticked)

        self.assertEqual(200, done.status_code, done.text)
        self.assertEqual(b"words", (self.folder / "Manual.docx").read_bytes())

    def test_an_unticked_file_with_no_place_writes_nothing(self) -> None:
        upload_id = self._drop({"(Wheel) Multi.png": _png(), "Manual.docx": b"words"})

        done = self._import(upload_id)

        self.assertEqual(200, done.status_code, done.text)
        self.assertNotIn("Manual.docx", self._files())

    def test_no_ffmpeg_keeps_a_sound_as_it_is_and_unticked(self) -> None:
        upload_id = self._drop({"(Audio) Multi.wav": b"RIFF", "table.vpx": b"x"})

        class _None(media_convert.Ffmpeg):
            @property
            def usable(self) -> bool:
                return False

        with patch.object(media_convert, "Ffmpeg", _None):
            plan = self._plan(upload_id).json()

        row = _row(plan, "(Audio) Multi.wav")
        self.assertFalse(row["default_enabled"])
        self.assertEqual("asis", row["convert"]["to"])
        self.assertTrue(row["convert"]["problem"])
        self.assertFalse(plan["convert"]["usable"])

    def test_a_job_converts_every_file_and_says_where_each_is(self) -> None:
        upload_id = self._drop({"(Wheel) Multi.tga": _tga(), "(Backglass) Multi.tga": _tga()})

        started = self.client.post(f"/uploads/{upload_id}/import/start",
                                   json={"game_dir": str(self.folder)})

        self.assertEqual(202, started.status_code, started.text)
        job = started.json()
        self.assertEqual("upload.import", job["kind"])
        finished = self._finished(job["id"])
        self.assertEqual("done", finished["state"], finished)
        self.assertEqual({"done"}, {one["state"] for one in finished["parts"]})
        self.assertEqual({"(Wheel) Multi.tga", "(Backglass) Multi.tga"},
                         {one["member"] for one in finished["parts"]})
        self.assertEqual(2, len(_medias(self)))

    def test_a_file_that_cannot_be_converted_fails_the_job_and_adds_nothing(self) -> None:
        upload_id = self._drop(
            {"(Wheel) Multi.png": _png(), "(Backglass) Multi.tga": b"not a picture"})

        with self.assertLogs("vpinfe.common.runtime.jobs", "ERROR"):
            started = self.client.post(f"/uploads/{upload_id}/import/start",
                                       json={"game_dir": str(self.folder)})
            finished = self._finished(started.json()["id"])
        self.assertEqual("failed", finished["state"])
        self.assertIn("(Backglass) Multi.tga", finished["error"])
        self.assertEqual(set(), _medias(self))

    def test_a_stop_after_the_last_conversion_still_adds_nothing(self) -> None:
        upload_id = self._drop({"(Wheel) Multi.tga": _tga()})
        stop = self.client.post

        def convert_then_stop(drop, assets, said, halted):
            stop("/uploads/import/stop")
            return {}, lambda: None

        with patch.object(drop_convert.Drop, "convert_all", convert_then_stop):
            started = self.client.post(f"/uploads/{upload_id}/import/start",
                                       json={"game_dir": str(self.folder)})
            finished = self._finished(started.json()["id"])

        self.assertEqual("done", finished["state"])
        self.assertTrue(finished["stopped"])
        self.assertEqual(set(), _medias(self))

    def test_a_drop_of_pictures_does_not_ask_for_ffmpeg(self) -> None:
        upload_id = self._drop({"(Wheel) Multi.tga": _tga()})

        class _None(media_convert.Ffmpeg):
            @property
            def usable(self) -> bool:
                return False

        with patch.object(media_convert, "Ffmpeg", _None):
            plan = self._plan(upload_id).json()

        self.assertTrue(plan["convert"]["usable"])

    def test_a_drop_with_nothing_to_add_is_refused_before_a_job_starts(self) -> None:
        upload_id = self._drop({"(Wheel) Multi.tga": _tga()})

        started = self.client.post(
            f"/uploads/{upload_id}/import/start",
            json={"game_dir": str(self.folder), "selected": []})

        self.assertGreaterEqual(started.status_code, 400)
        self.assertNotEqual(202, started.status_code)

    def test_a_session_an_import_is_reading_is_neither_aborted_nor_swept(self) -> None:
        upload_id = self.client.post("/uploads").json()["id"]
        self.addCleanup(self.client.delete, f"/uploads/{upload_id}")
        self.addCleanup(upload_session_service.release, upload_id)
        upload_session_service.hold(upload_id)

        self.client.delete(f"/uploads/{upload_id}")
        later = time.time() + 10 * upload_session_service.SESSION_TTL_SECONDS
        with patch.object(upload_session_service.time, "time", return_value=later):
            upload_session_service._sweep()

        self.assertTrue(upload_session_service.is_held(upload_id))
        self.assertTrue(upload_session_service.get_session_dir(upload_id).is_dir())

    def test_a_conversion_that_fails_to_plan_is_blocked_not_raised(self) -> None:
        upload_id = self._drop({"(Wheel) Multi.tga": _tga()})

        with patch.object(media_convert, "plan_staged", side_effect=OSError("unreadable")):
            plan = self._plan(upload_id)

        self.assertEqual(200, plan.status_code)
        self.assertTrue(_row(plan.json(), "(Wheel) Multi.tga")["convert"]["problem"])

    def test_stopping_with_nothing_running_says_so(self) -> None:
        stopped = self.client.post("/uploads/import/stop")

        self.assertEqual(200, stopped.status_code)
        self.assertFalse(stopped.json()["ok"])

    def test_a_tga_has_a_picture_to_draw(self) -> None:
        upload_id = self._session("wheel.tga", _tga())

        shown = self.client.get(f"/uploads/{upload_id}/picture", params={"name": "wheel.tga"})

        self.assertEqual(200, shown.status_code)
        with Image.open(io.BytesIO(shown.content)) as drawn:
            self.assertGreater(drawn.size[0], 0)

    def _finished(self, job_id: str) -> dict:
        for _ in range(100):
            found = self.client.get(f"/jobs/{job_id}").json()
            if found["state"] != jobs.RUNNING:
                return found
            time.sleep(0.1)
        self.fail("the import did not finish")


def _png() -> bytes:
    held = io.BytesIO()
    Image.new("RGB", (32, 32)).save(held, "PNG")
    return held.getvalue()


if __name__ == "__main__":
    unittest.main()
