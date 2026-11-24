"""A step that takes a while is waited for, and says how far it has got."""

from __future__ import annotations

import asyncio
import unittest
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any
from unittest import mock

from manager.extensions import ext_action
from manager.kit import api, offload, wizard


def _ran(work: Any) -> Any:
    return asyncio.run(work)


async def _direct(callback: Callable[..., Any], *args: Any) -> Any:
    return callback(*args)


class StepCallsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sent: list[tuple[str, dict, int | None]] = []
        sent = self.sent

        class Client:
            def ext_get(self, path: str) -> dict:
                sent.append((path, {}, None))
                return {"stage": "reading"}

            def ext_post(self, path: str, body: dict, timeout: int | None = None) -> dict:
                sent.append((path, body, timeout))
                return {}

        patch = mock.patch.object(ext_action, "ApiClient", Client)
        patch.start()
        self.addCleanup(patch.stop)
        patch_io = mock.patch.object(offload, "io", _direct)
        patch_io.start()
        self.addCleanup(patch_io.stop)
        self.calls = ext_action.calls("demo", {"base": "/wizard"})

    def test_a_step_is_given_longer_than_a_page_load_to_answer(self) -> None:
        _ran(self.calls.check({}, "source"))
        _ran(self.calls.act("browse", {}, "source"))
        _ran(self.calls.run({}, "summary"))

        self.assertEqual([one[2] for one in self.sent], [api.STEP_TIMEOUT] * 3)
        self.assertGreater(api.STEP_TIMEOUT, api._TIMEOUT)

    def test_the_first_call_carries_the_step_and_the_rows_in_its_body(self) -> None:
        calls = ext_action.calls("demo", {"base": "/wizard"}, ["a b", "c&d"])

        _ran(calls.first("source"))

        self.assertEqual(self.sent[-1][:2], ("/ext/demo/wizard/ask",
                                             {"step": "source", "rows": ["a b", "c&d"]}))

    def test_the_first_call_carries_the_game_the_dialog_was_opened_for(self) -> None:
        who = {"game_id": "", "vps_id": "abcdefghij", "name": "Taxi"}
        calls = ext_action.calls("demo", {"base": "/wizard"}, ["pick-1"], who)

        _ran(calls.first(""))

        self.assertEqual(self.sent[-1][:2], ("/ext/demo/wizard/ask",
                                             {"step": "", "rows": ["pick-1"], "who": who}))

    def test_a_later_call_carries_the_game_beside_its_values(self) -> None:
        who = {"game_id": "", "vps_id": "abcdefghij", "name": "Taxi"}
        calls = ext_action.calls("demo", {"base": "/wizard"}, ["pick-1"], who)

        _ran(calls.check({"a": "1"}, "source"))
        _ran(calls.act("browse", {"a": "1"}, "source"))
        _ran(calls.run({"a": "1"}, "summary"))

        self.assertEqual([one[1]["who"] for one in self.sent[-3:]], [who] * 3)
        self.assertEqual({"a": "1"}, self.sent[-1][1]["values"])

    def test_a_later_call_for_no_game_names_none(self) -> None:
        _ran(self.calls.check({"a": "1"}, "source"))
        _ran(self.calls.run({"a": "1"}, "summary"))

        self.assertEqual([("who" in one[1]) for one in self.sent[-2:]], [False, False])

    def test_progress_is_asked_of_the_action(self) -> None:
        assert self.calls.progress is not None

        self.assertEqual(_ran(self.calls.progress()), {"stage": "reading"})
        self.assertEqual(self.sent[-1][0], "/ext/demo/wizard/progress")

    def test_an_action_with_no_progress_stops_being_asked_after_a_few_answers(self) -> None:
        asked = []

        class Client:
            def ext_get(self, path: str) -> dict:
                asked.append(path)
                raise OSError("no such route")

        with mock.patch.object(ext_action, "ApiClient", Client):
            calls = ext_action.calls("demo", {"base": "/wizard"})
            assert calls.progress is not None
            for _ in range(ext_action.GIVE_UP_AFTER + 2):
                self.assertEqual(_ran(calls.progress()), {})

        self.assertEqual(len(asked), ext_action.GIVE_UP_AFTER)

    def test_one_slow_answer_does_not_stop_the_asking(self) -> None:
        answers: list[Any] = [OSError("busy"), {"stage": "reading"}, OSError("busy"),
                              {"stage": "x"}]

        class Client:
            def ext_get(self, path: str) -> dict:
                said = answers.pop(0)
                if isinstance(said, Exception):
                    raise said
                return said

        with mock.patch.object(ext_action, "ApiClient", Client):
            calls = ext_action.calls("demo", {"base": "/wizard"})
            assert calls.progress is not None
            seen = [_ran(calls.progress()) for _ in range(4)]

        self.assertEqual(seen, [{}, {"stage": "reading"}, {}, {"stage": "x"}])


class ShownTests(unittest.TestCase):
    def _shown(self) -> SimpleNamespace:
        props: list[str] = []
        bar = SimpleNamespace(
            value=0.0,
            props=lambda *args, **kwargs: props.append(
                kwargs.get("remove", "") and f"-{kwargs['remove']}" or str(args[0])))
        return SimpleNamespace(bar=bar, said=SimpleNamespace(text=""), props=props)

    def test_a_count_gives_a_bar_that_fills(self) -> None:
        shown = self._shown()

        wizard._show({"bar": shown.bar, "said": shown.said},
                     {"message": "Measuring the files", "done": 20, "total": 80})

        self.assertAlmostEqual(shown.bar.value, 0.25)
        self.assertIn("Measuring the files", shown.said.text)
        self.assertEqual(shown.props, ["-indeterminate"])

    def test_no_count_keeps_the_bar_moving_without_a_value(self) -> None:
        shown = self._shown()

        wizard._show({"bar": shown.bar, "said": shown.said}, {"message": "Reading it"})

        self.assertEqual(shown.said.text, "Reading it")
        self.assertEqual(shown.props, ["indeterminate"])

    def test_nothing_said_leaves_what_is_shown(self) -> None:
        shown = self._shown()
        shown.said.text = "Working"

        wizard._show({"bar": shown.bar, "said": shown.said}, {"message": ""})

        self.assertEqual(shown.said.text, "Working")
        self.assertEqual(shown.props, [])


class HeldButtonsTests(unittest.TestCase):
    def _button(self, classes: str, enabled: bool = True) -> SimpleNamespace:
        button = SimpleNamespace(classes=classes.split(), enabled=enabled)
        button.set_enabled = lambda on: setattr(button, "enabled", on)
        return button

    def test_only_the_forward_button_is_held_and_only_what_was_enabled_is_given_back(
            self) -> None:
        forward = self._button(f"x {wizard._FORWARD}")
        gated = self._button(f"x {wizard._FORWARD}", enabled=False)
        back = self._button("x")
        footer = SimpleNamespace(default_slot=SimpleNamespace(
            children=[back, gated, forward]))

        held = wizard._hold(footer)

        self.assertEqual(held, [forward])
        self.assertFalse(forward.enabled)
        self.assertTrue(back.enabled)
        wizard._release(held)
        self.assertTrue(forward.enabled)
        self.assertFalse(gated.enabled)


if __name__ == "__main__":
    unittest.main()
