"""Pressing something from outside the browser.

Two halves that have to agree: what reaches the bus, and what a caller is allowed to
say. The hold is the part worth testing hardest - a press that is never released is a
wheel that spins until somebody notices, and the whole reason a press expires is that a
release can be lost.
"""

from __future__ import annotations

import unittest

import httpapi
from common import events
from common.host import input_actions
from frontend import input_events

try:
    from starlette.testclient import TestClient
except ImportError:  # pragma: no cover
    TestClient = None  # type: ignore[assignment,misc]


class _Bridge:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    def send_event_all_with_iframe(self, message) -> None:
        self.sent.append(message)


class _Heard:
    """Every input event, in order, as the frontend bridge would see them."""

    def __init__(self) -> None:
        self.seen: list[dict] = []
        events.subscribe(events.INPUT_ACTION, self._note)

    def _note(self, **payload) -> None:
        self.seen.append(payload)

    @property
    def phases(self) -> list[str]:
        return [one["phase"] for one in self.seen]


class HoldTests(unittest.TestCase):
    def setUp(self) -> None:
        events.clear()
        input_actions.release_all()
        self.heard = _Heard()
        self.addCleanup(events.clear)
        self.addCleanup(input_actions.release_all)

    def test_a_renewal_is_not_a_second_press(self) -> None:
        """One gesture, one press. A client renewing while a thumb is down is saying
        nothing has changed, and announcing each renewal would make one hold N presses."""
        input_actions.press("next", source="test")
        input_actions.press("next", source="test")
        input_actions.press("next", source="test")

        self.assertEqual(self.heard.phases, ["press"])
        self.assertEqual(input_actions.holding(), frozenset({"next"}))

    def test_a_press_that_is_never_released_lets_go_on_its_own(self) -> None:
        """The failure this exists for: a phone locks its screen mid-hold and the
        release never arrives."""
        input_actions.press("next", source="test", ttl_ms=input_actions.MIN_TTL_MS)

        released = _wait_for(lambda: "release" in self.heard.phases)

        self.assertTrue(released, "the hold never expired")
        self.assertEqual(input_actions.holding(), frozenset())
        self.assertTrue(self.heard.seen[-1]["expired"])

    def test_a_release_cancels_the_expiry(self) -> None:
        """Otherwise a released action releases itself a second time, and a window that
        had since been pressed again would be let go of by a timer for the old press."""
        input_actions.press("next", source="test", ttl_ms=input_actions.MIN_TTL_MS)
        input_actions.release("next", source="test")

        self.assertFalse(_wait_for(lambda: self.heard.phases.count("release") > 1))

    def test_a_release_nobody_pressed_is_still_announced(self) -> None:
        """A client that reconnects mid-gesture and lets go is doing the right thing,
        and the windows need to hear it whether or not this process saw the press."""
        input_actions.release("previous", source="test")

        self.assertEqual(self.heard.phases, ["release"])

    def test_a_tap_is_a_press_and_a_release(self) -> None:
        """Not a phase of its own, so the frontend has one thing to learn rather than
        two ways to say it."""
        input_actions.tap("select", source="test")

        self.assertEqual(self.heard.phases, ["press", "release"])
        self.assertEqual(input_actions.holding(), frozenset())

    def test_a_ttl_is_clamped_rather_than_refused(self) -> None:
        """A caller that sent seconds where it meant milliseconds gets a hold that ends,
        not a 400 in the middle of a gesture."""
        self.assertEqual(input_actions.clamp_ttl(0), input_actions.DEFAULT_TTL_MS)
        self.assertEqual(input_actions.clamp_ttl(1), input_actions.MIN_TTL_MS)
        self.assertEqual(input_actions.clamp_ttl(10 ** 9), input_actions.MAX_TTL_MS)


@unittest.skipIf(TestClient is None, "starlette test client unavailable")
class InputRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        events.clear()
        input_actions.release_all()
        self.heard = _Heard()
        self.client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)
        self.addCleanup(events.clear)
        self.addCleanup(input_actions.release_all)

    def _post(self, **body):
        return self.client.post("/input/actions", json=body)

    def test_a_tap_is_the_default_phase(self) -> None:
        """The common case is a button on a phone, which has nothing to hold."""
        answer = self._post(action="next")

        self.assertEqual(answer.status_code, 200)
        self.assertEqual(self.heard.phases, ["press", "release"])
        self.assertEqual(answer.json()["holding"], [])

    def test_an_action_nobody_declared_is_refused_by_name(self) -> None:
        """The vocabulary is closed and small, so the answer can list it rather than
        leaving a client to guess which of ten words it got wrong."""
        answer = self._post(action="flipper_left")

        self.assertEqual(answer.status_code, 400)
        self.assertIn("previous", answer.json()["error"]["message"])
        self.assertEqual(self.heard.seen, [])

    def test_a_phase_nobody_declared_is_refused(self) -> None:
        answer = self._post(action="next", phase="wiggle")

        self.assertEqual(answer.status_code, 400)
        self.assertEqual(self.heard.seen, [])

    def test_a_hold_reports_what_is_still_held(self) -> None:
        """So a client that reconnected can see what it is on the hook for renewing."""
        held = self._post(action="next", phase="press").json()

        self.assertEqual(held["holding"], ["next"])
        self.assertEqual(self.heard.phases, ["press"])

    def test_the_source_carries_where_the_caller_came_from(self) -> None:
        """B11 was a "where did this press come from" defect that took an executed test
        to find. The next one will be too, so the log has to be able to answer it."""
        self._post(action="next", source="remote")

        self.assertTrue(self.heard.seen[0]["source"].startswith("remote/"))

    def test_a_caller_that_names_nothing_still_names_something(self) -> None:
        self._post(action="next")

        self.assertTrue(self.heard.seen[0]["source"].startswith("api/"))

    def test_the_device_says_what_it_can_do_with_keys_during_play(self) -> None:
        from pathlib import Path
        from unittest import mock

        from httpapi import input as route

        # Through the route's own references: a test that imports the simulator afresh
        # leaves the package holding a different module object.
        key_reader, key_simulator = route.key_reader, route.key_simulator
        refused = {"key": key_reader.NO_INPUT_GROUP, "params": {}, "fix": "user",
                   "remedy": {"key": key_reader.JOIN_INPUT_GROUP, "params": {}}}
        with mock.patch.object(key_simulator, "sender", return_value=key_simulator.Sender(
                "wtype", Path("/usr/bin/wtype"))), \
                mock.patch.object(key_reader, "hearing",
                                  return_value=key_reader.Hearing("", refused)):
            answer = self.client.get("/input/play")

        self.assertEqual(answer.status_code, 200)
        self.assertEqual(answer.json()["press"],
                         {"available": True, "via": "wtype", "reason": None})
        self.assertEqual(answer.json()["hear"]["reason"]["remedy"]["key"],
                         key_reader.JOIN_INPUT_GROUP)


class BridgeTests(unittest.TestCase):
    """What actually reaches the windows."""

    def setUp(self) -> None:
        events.clear()
        input_events.reset_for_tests()
        input_actions.release_all()
        self.bridge = _Bridge()
        input_events.register(self.bridge)  # type: ignore[arg-type]
        self.addCleanup(events.clear)
        self.addCleanup(input_events.reset_for_tests)
        self.addCleanup(input_actions.release_all)

    def test_a_press_reaches_every_window(self) -> None:
        """Broadcast, not aimed: Python does not know which window is the controller,
        and aiming from here would make one press three on a three-screen cabinet."""
        input_actions.press("next", source="remote")

        self.assertEqual(self.bridge.sent,
                         [{"type": "InputAction", "action": "next", "phase": "press"}])

    def test_where_the_press_came_from_does_not_travel_to_the_browser(self) -> None:
        """A theme that behaved differently for a phone than for a flipper would be a
        bug surface, and the log is where the question actually gets asked."""
        input_actions.press("next", source="remote/network", ttl_ms=400)

        self.assertNotIn("source", self.bridge.sent[0])
        self.assertNotIn("ttl_ms", self.bridge.sent[0])

    def test_registering_twice_does_not_send_twice(self) -> None:
        """Every API instance sends through the same bridge, so a per-window
        registration would put each press on the wire once per screen."""
        input_events.register(self.bridge)  # type: ignore[arg-type]

        input_actions.tap("select", source="remote")

        self.assertEqual([one["phase"] for one in self.bridge.sent],
                         ["press", "release"])

    def test_a_press_the_page_already_answered_does_not_go_back_to_it(self) -> None:
        """The page announces what it handled so extensions hear it, and sending it
        back to the windows would have it answer its own press a second time."""
        input_actions.heard("take_picture")
        input_actions.settle()

        self.assertEqual(self.bridge.sent, [])


class PageHeardTests(unittest.TestCase):
    """Protects: (b) an extension hears a press the page answered, once, as a named action."""

    def setUp(self) -> None:
        events.clear()
        input_actions.release_all()
        self.heard = _Heard()
        self.addCleanup(events.clear)
        self.addCleanup(input_actions.release_all)

    def test_a_named_action_is_announced_as_a_press_from_the_page(self) -> None:
        self.assertTrue(input_actions.heard("back"))
        input_actions.settle()

        self.assertEqual([(one["action"], one["phase"], one["source"])
                          for one in self.heard.seen],
                         [("back", "press", input_actions.PAGE)])

    def test_a_name_that_is_not_an_action_is_not_announced(self) -> None:
        self.assertFalse(input_actions.heard("KeyQ"))
        self.assertFalse(input_actions.heard(""))
        input_actions.settle()

        self.assertEqual(self.heard.seen, [])

    def test_nothing_is_held_afterwards(self) -> None:
        input_actions.heard("next")
        input_actions.settle()
        input_actions.release_all()

        self.assertEqual(self.heard.phases, ["press"], "no hold was armed, so none is let go")

    def test_a_slow_handler_does_not_hold_up_the_page_and_order_is_kept(self) -> None:
        import threading

        gate = threading.Event()
        order: list[str] = []

        def slow(**payload) -> None:
            gate.wait(5)
            order.append(payload["action"])

        events.subscribe(events.INPUT_ACTION, slow)

        input_actions.heard("next")
        input_actions.heard("back")
        self.assertEqual(order, [], "the caller returned while the handler still waits")
        gate.set()
        input_actions.settle()

        self.assertEqual(order, ["next", "back"])

    def test_the_page_reports_through_the_api(self) -> None:
        from frontend.api import API

        reported = API.report_input_action(API.__new__(API), "select")

        input_actions.settle()

        self.assertEqual(reported, {"announced": True})
        self.assertEqual([one["action"] for one in self.heard.seen], ["select"])


def _wait_for(said, seconds: float = 2.0) -> bool:
    """Poll rather than sleep the whole budget: a timer test that always costs its
    timeout is a test people stop running."""
    import time

    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if said():
            return True
        time.sleep(0.01)
    return False
