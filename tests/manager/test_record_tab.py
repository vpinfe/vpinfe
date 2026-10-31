"""A media slot's Record tab: which slots have it, what stops it, what Record asks the
device for, and what the tab shows when the recording ends."""

from __future__ import annotations

import contextlib
import unittest
from types import SimpleNamespace
from typing import Any
from unittest import mock

from common.capture import adapters, placing
from manager.library import mediasource, record

CAPTURE = {"name": "capture", "available": True, "reason": None}
NOT_SHOWN = {"key": placing.NOT_SHOWN, "fix": "none", "remedy": None,
             "params": {"app": "Visual Pinball X", "window": "scoreview"}}


def _library(capabilities: list[dict[str, Any]]) -> Any:
    return SimpleNamespace(discovery=lambda: {"capabilities": capabilities},
                           placements=None, displaced_by=None, place_media=None,
                           offers_online=lambda _kind: False)


class WhichSlotsRecordTests(unittest.TestCase):
    def test_a_recordable_kind_on_an_install_that_serves_capture(self) -> None:
        library = _library([{"name": "launch", "available": True}, CAPTURE])

        self.assertEqual(mediasource.records(library, "playfield_video"), CAPTURE)
        self.assertEqual(mediasource.records(library, "audio"), CAPTURE)
        self.assertIsNone(mediasource.records(library, "wheel"))
        self.assertIsNone(mediasource.records(_library([]), "playfield_video"))

    def test_record_opens_the_source_dialog_on_its_record_tab(self) -> None:
        context = {"library": _library([CAPTURE]), "game_id": "g1", "game": {},
                   "lens": ""}
        with mock.patch.object(mediasource._Slot, "open", autospec=True) as opened:
            mediasource.open_sources(context, "backglass_video", "Backglass Video",
                                     lambda: None, recording_it=True)
            mediasource.open_sources(context, "wheel", "Wheel", lambda: None)

        recorded, wheel = (call.args[0] for call in opened.call_args_list)
        self.assertEqual((recorded.records, recorded.first), (True, "record"))
        self.assertEqual((wheel.records, wheel.first), (False, ""))

    def test_the_file_name_follows_the_recording_on_its_tab(self) -> None:
        context = {"library": _library([CAPTURE]), "game_id": "g1", "game": {},
                   "lens": ""}
        slot = mediasource._Slot(context, "playfield_video", "Playfield Video",
                                 lambda: None, mediasource._media(context["library"],
                                                                  "playfield_video"),
                                 records=True)

        slot.picked("record")
        self.assertEqual(slot.chosen_extension, ".mp4")
        slot.picked("upload")
        self.assertEqual(slot.chosen_extension, "")

    def test_a_backglass_file_opens_an_empty_picture_slot_on_its_tab(self) -> None:
        context = {"library": _library([]), "game_id": "g1", "game": {}, "lens": ""}
        offer = {"path": "a.directb2s", "file": "a.directb2s", "grill": True,
                 "version": "v1"}
        with mock.patch.object(mediasource._Slot, "open", autospec=True) as opened:
            mediasource.open_sources(context, "backglass", "Backglass", lambda: None,
                                     backglass_file=offer)
            mediasource.open_sources(context, "backglass", "Backglass", lambda: None,
                                     "medias/bg.png", backglass_file=offer)
            mediasource.open_sources(context, "backglass", "Backglass", lambda: None)

        empty, filled, none = (call.args[0] for call in opened.call_args_list)
        self.assertEqual((empty.backglass, empty.first), (True, "backglass"))
        self.assertEqual((filled.backglass, filled.first), (True, ""))
        self.assertEqual((none.backglass, none.first), (False, ""))

    def test_the_file_name_follows_the_backglass_file_on_its_tab(self) -> None:
        context = {"library": _library([]), "game_id": "g1", "game": {}, "lens": ""}
        slot = mediasource._Slot(context, "backglass", "Backglass", lambda: None,
                                 mediasource._media(context["library"], "backglass"),
                                 backglass_file={"path": "a.directb2s"})

        slot.picked("backglass")
        self.assertEqual(slot.chosen_extension, ".png")
        slot.picked("upload")
        self.assertEqual(slot.chosen_extension, "")


class StoppedTests(unittest.TestCase):
    def test_what_stops_record_is_said_in_order(self) -> None:
        unavailable = {"available": False,
                       "reason": {"key": adapters.NO_WAY, "params": {}}}
        able = {"available": True}

        self.assertEqual(record.stopped(unavailable, None, running=True),
                         "This desktop gives VPinFE no way to record its screens")
        self.assertEqual(record.stopped(able, {"reason": NOT_SHOWN}, running=True),
                         "Visual Pinball X doesn't show the DMD on a screen of its own")
        self.assertEqual(record.stopped(able, {"reason": None}, running=True),
                         "A table is running on this device")
        self.assertEqual(record.stopped(able, {"reason": None}, running=False), "")


class Device:
    def __init__(self, ran: dict[str, Any]) -> None:
        self.ran = ran
        self.started: list[dict[str, Any]] = []
        self.planned: list[dict[str, Any]] = []

    def discovery(self) -> dict[str, Any]:
        return {"capabilities": [CAPTURE]}

    def capture_report(self) -> dict[str, Any]:
        return {"available": True}

    def config_schema(self) -> list[dict[str, Any]]:
        return [{"name": "capture", "options": []}]

    def config_values(self) -> dict[str, Any]:
        return {"capture": {}}

    def play_state(self) -> dict[str, Any]:
        return {"launching": False}

    def plan_capture(self, body: dict[str, Any]) -> dict[str, Any]:
        self.planned.append(body)
        return {"kinds": [{"kind": body["kinds"][0], "does": "propose", "reason": None}],
                "recording": body["kinds"], "estimate_seconds": 59}

    def start_capture(self, body: dict[str, Any]) -> dict[str, Any]:
        self.started.append(body)
        return {"id": "j1"}

    def capture_proposals(self) -> dict[str, Any]:
        return {"proposals": [{"id": "a1", "kind": "playfield_video"}]}

    placements = displaced_by = place_media = None


def _io(call: Any, *args: Any, **kwargs: Any) -> Any:
    return call(*args, **kwargs)


class TabTests(unittest.IsolatedAsyncioTestCase):
    """Record in the tab: one kind, kept for review, at the destination's tier."""

    def setUp(self) -> None:
        self.enterContext(mock.patch("manager.kit.on_page.ui"))
        self.enterContext(mock.patch.object(mediasource, "ui"))
        self.enterContext(mock.patch.object(mediasource.offload, "io",
                                            mock.AsyncMock(side_effect=_io)))
        self.enterContext(mock.patch.object(mediasource.busy, "held",
                                            lambda *_a, **_k: contextlib.nullcontext()))
        self.enterContext(mock.patch.object(record, "setting_rows", return_value=[]))
        self.enterContext(mock.patch.object(mediasource.panel, "facts"))
        self.enterContext(mock.patch.object(mediasource.panel, "line"))
        self.action = self.enterContext(mock.patch.object(mediasource.panel, "action"))
        self.attention = self.enterContext(mock.patch.object(mediasource, "_attention"))
        self.shown = self.enterContext(mock.patch.object(record, "proposal"))

    async def _record(self, ran: dict[str, Any], lens: str = "") -> Device:
        device = Device(ran)
        context = {"library": device, "game_id": "g1", "game": {"name": "Game"},
                   "lens": lens, "state": {}}
        slot = mediasource._Slot(context, "playfield_video", "Playfield Video",
                                 mock.AsyncMock(), mediasource._media(device,
                                                                      "playfield_video"),
                                 records=True)
        slot.dialog = mock.Mock(is_deleted=False, value=True)
        slot.placed_at = {"table": lens}
        with mock.patch.object(record, "ended", mock.AsyncMock(return_value=ran)):
            await slot.record_tab(mock.MagicMock())
            go = next(call.args[1] for call in self.action.call_args_list
                      if call.args[0] == "Record")
            await go()
        return device

    async def test_record_asks_for_the_one_kind_kept_for_review(self) -> None:
        device = await self._record({"state": "recorded", "proposed": [
            {"kind": "playfield_video", "id": "a1"}]}, lens="t1")

        self.assertEqual(device.started, [{
            "tables": [{"game": "g1", "table": "t1"}], "existing": "fill",
            "kinds": ["playfield_video"], "settings": {}, "review": True}])
        self.assertEqual(self.shown.call_args.args[1], {"id": "a1", "kind": "playfield_video"})

    async def test_a_kind_that_failed_says_why_without_its_name(self) -> None:
        await self._record({"state": "failed", "proposed": [], "reason": None,
                            "failed": [{"kind": "playfield_video", "reason": NOT_SHOWN}]})

        self.assertEqual(self.attention.call_args.args[0],
                         "Visual Pinball X doesn't show the DMD on a screen of its own")
        self.shown.assert_not_called()


if __name__ == "__main__":
    unittest.main()
