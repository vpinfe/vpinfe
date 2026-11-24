"""The controller window reports its buttons and keys to a Manager: what the
window may send, and what the frontend broadcasts to make it start and stop."""

from __future__ import annotations

import configparser
import unittest
from types import SimpleNamespace

from common import events
from frontend import input_capture
from frontend.api import API, API_INTERNAL_METHODS


class _Bridge:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    def send_event_all_with_iframe(self, message) -> None:
        self.sent.append(message)


def _window() -> API:
    parser = configparser.ConfigParser()
    parser.add_section("general")
    ini = SimpleNamespace(config=parser, save=lambda: None)
    return API(ini, window_name="table")  # type: ignore[arg-type]


class _Presses:
    def __init__(self) -> None:
        self.seen: list[dict] = []
        events.subscribe(events.INPUT_CAPTURE_PRESS, self._note)

    def _note(self, **payload) -> None:
        self.seen.append(payload)


class WhatAWindowMaySend(unittest.TestCase):
    def setUp(self) -> None:
        events.clear()
        self.addCleanup(events.clear)
        self.presses = _Presses()
        self.window = _window()

    def test_a_button_going_down_or_up_reaches_the_bus(self) -> None:
        self.assertEqual(self.window.input_capture("pad:standard/button:4", "down"),
                         {"relayed": True})
        self.window.input_capture("pad:standard/button:4", "up")

        self.assertEqual(self.presses.seen, [
            {"token": "pad:standard/button:4", "phase": "down"},
            {"token": "pad:standard/button:4", "phase": "up"}])

    def test_a_key_going_down_or_up_reaches_the_bus(self) -> None:
        for code in ("KeyN", "ArrowUp", "Digit1", "ShiftLeft", "Numpad0"):
            self.assertEqual(self.window.input_capture(f"key:{code}", "down"),
                             {"relayed": True})

        self.assertEqual([one["token"] for one in self.presses.seen],
                         ["key:KeyN", "key:ArrowUp", "key:Digit1", "key:ShiftLeft",
                          "key:Numpad0"])

    def test_something_that_is_not_a_button_or_key_is_refused(self) -> None:
        for token in ("", None, "pad", 7, "pad:x", "pad:standard/button:",
                      "pad:standard/button:4 ", "pad:1234/button:0", "key:", "key:Key N",
                      "key:" + "A" * 33, "key:KeyN ", "keys:KeyN"):
            self.assertEqual(self.window.input_capture(token, "down"), {"relayed": False})

    def test_a_phase_other_than_down_or_up_is_refused(self) -> None:
        for phase in ("hold", "", None):
            self.assertEqual(self.window.input_capture("pad:2/button:0", phase),
                             {"relayed": False})

        self.assertEqual(self.presses.seen, [])

    def test_a_theme_cannot_call_it(self) -> None:
        self.assertIn("input_capture", API_INTERNAL_METHODS)


class WhatTheFrontendBroadcasts(unittest.TestCase):
    def setUp(self) -> None:
        events.clear()
        input_capture.reset_for_tests()
        self.addCleanup(events.clear)
        self.addCleanup(input_capture.reset_for_tests)
        self.bridge = _Bridge()
        input_capture.register(self.bridge)  # type: ignore[arg-type]

    def test_asking_goes_to_every_window(self) -> None:
        events.emit(events.INPUT_CAPTURE_ARMED, armed=True, ttl_ms=6000)

        self.assertEqual(self.bridge.sent, [{"type": "CaptureArm", "armed": True,
                                             "ttl_ms": 6000, "browser": "", "device": ""}])

    def test_the_window_is_told_who_holds_the_buttons(self) -> None:
        events.emit(events.INPUT_CAPTURE_ARMED, armed=True, ttl_ms=6000,
                    browser="Safari on Mac", device="office-laptop")

        self.assertEqual((self.bridge.sent[0]["browser"], self.bridge.sent[0]["device"]),
                         ("Safari on Mac", "office-laptop"))

    def test_giving_it_back_goes_to_every_window(self) -> None:
        events.emit(events.INPUT_CAPTURE_ARMED, armed=False)

        self.assertEqual(self.bridge.sent, [{"type": "CaptureArm", "armed": False,
                                             "ttl_ms": 0, "browser": "", "device": ""}])

    def test_a_take_back_from_the_window_is_announced_to_the_pages(self) -> None:
        released: list[bool] = []
        events.subscribe(events.INPUT_CAPTURE_RELEASED, lambda **_: released.append(True))

        result = _window().input_capture_release()

        self.assertEqual(result, {"released": True})
        self.assertEqual(released, [True])

    def test_a_theme_cannot_take_them_back(self) -> None:
        self.assertIn("input_capture_release", API_INTERNAL_METHODS)

    def test_registering_twice_sends_once(self) -> None:
        input_capture.register(self.bridge)  # type: ignore[arg-type]

        events.emit(events.INPUT_CAPTURE_ARMED, armed=True, ttl_ms=6000)

        self.assertEqual(len(self.bridge.sent), 1)
