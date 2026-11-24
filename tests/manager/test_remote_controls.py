"""The Remote's Table Controls: what is asked of the target, and what a press does."""

from __future__ import annotations

import unittest
from typing import Any
from unittest import mock

from common.i18n import t
from manager.remote import remote


class _Target:
    def __init__(self, answer: dict | None = None, *, raises: Exception | None = None,
                 pressed: bool = True) -> None:
        self.answer, self.raises, self.pressed = answer, raises, pressed
        self.asked: list[str] = []

    def table_controls(self) -> dict | None:
        if self.raises:
            raise self.raises
        return self.answer

    def press_control(self, control_id: str) -> dict:
        self.asked.append(control_id)
        if self.raises:
            raise self.raises
        return {"control_id": control_id, "pressed": self.pressed}


def _target(*args: Any, **kwargs: Any) -> Any:
    return _Target(*args, **kwargs)


async def _awaited(fn: Any, *args: Any) -> Any:
    return fn(*args)


class ReadTests(unittest.TestCase):
    PLAYING = {"launching": True, "source": "frontend"}
    ANSWER = {"table": {"game_id": "g", "game_name": "Example", "app": "vpx"},
              "press": {"available": True, "via": "keys", "reason": ""},
              "controls": [{"id": "pause", "group": "game", "label": "Pause", "hold_ms": 0,
                            "available": True, "reason": ""}]}

    def test_read_from_the_target_while_a_table_is_up(self) -> None:
        self.assertEqual(remote._controls_of(_target(self.ANSWER), self.PLAYING),
                         self.ANSWER)

    def test_not_asked_while_nothing_plays_or_a_recording_holds_the_table(self) -> None:
        target = _target(raises=AssertionError("asked"))
        self.assertEqual(remote._controls_of(target, {"launching": False}), {})
        self.assertEqual(remote._controls_of(target, self.PLAYING | {"source": "capture"}),
                         {})

    def test_a_target_too_old_to_know_the_route_or_not_answering_offers_none(self) -> None:
        self.assertEqual(remote._controls_of(_target(None), self.PLAYING), {})
        self.assertEqual(remote._controls_of(_target(raises=OSError("down")), self.PLAYING),
                         {})


class BlockedTests(unittest.TestCase):
    CONTROL = {"id": "extra_ball", "available": False, "reason": "Mapped to a button"}

    def test_a_control_is_blocked_by_its_own_reason(self) -> None:
        self.assertEqual(remote._blocked({"press": {"available": True}}, self.CONTROL),
                         "Mapped to a button")

    def test_an_available_control_is_not_blocked(self) -> None:
        self.assertEqual(remote._blocked({}, self.CONTROL | {"available": True}), "")

    def test_an_install_that_cannot_send_keys_blocks_every_control(self) -> None:
        press = {"available": False, "reason": {"key": "keys.send.no_session", "params": {}}}
        self.assertEqual(
            remote._blocked({"press": press}, self.CONTROL | {"available": True}),
            t("keys.send.no_session"))


class PressTests(unittest.IsolatedAsyncioTestCase):
    def _on_a_thread(self) -> Any:
        self.enterContext(mock.patch.object(remote.ui, "context", mock.MagicMock()))
        return mock.patch.object(remote.run, "io_bound", new=_awaited)

    async def test_the_press_is_the_controls_id_and_nothing_else(self) -> None:
        target = _target()
        with self._on_a_thread():
            await remote._press_control(lambda: target, "volume_up")
        self.assertEqual(target.asked, ["volume_up"])

    async def test_a_failure_is_said_once_unless_the_control_is_held(self) -> None:
        target = _target(raises=OSError("down"))
        with self._on_a_thread(), mock.patch.object(remote.ui, "notify") as notify:
            await remote._press_control(lambda: target, "start")
            await remote._press_control(lambda: target, "volume_up", quiet=True)
        self.assertEqual(notify.call_count, 1)

    async def test_a_key_that_was_not_sent_is_said(self) -> None:
        target = _target(pressed=False)
        with self._on_a_thread(), mock.patch.object(remote.ui, "notify") as notify:
            await remote._press_control(lambda: target, "start")
        notify.assert_called_once()


if __name__ == "__main__":
    unittest.main()
