"""The page host's own drawing: the same step row, body and footer the dialog draws,
on the page rather than in a dialog, and the current step kept in the address - all
proven with a fake flow, since nothing in the tree hosts a real one yet."""

from __future__ import annotations

import asyncio
import unittest
from collections.abc import Callable
from typing import Any
from unittest import mock

from nicegui import core, ui

from manager.kit import wizard
from tests.support.clicks import said

# Made at import, outside any task, where NiceGUI still hands out the script's own page.
_PAGE = ui.element()


def _calls(*, first: dict[str, Any],
          on_check: Callable[[dict[str, Any], str], dict[str, Any]] | None = None,
          on_act: Callable[[str, dict[str, Any], str], dict[str, Any]] | None = None,
          on_goto: Callable[[str], dict[str, Any]] | None = None,
          on_run: Callable[[dict[str, Any], str], dict[str, Any]] | None = None,
          seen_first: list[str | None] | None = None) -> wizard.Calls:
    async def first_call(step: str | None = None) -> dict:
        if seen_first is not None:
            seen_first.append(step)
        if step is None:
            return first
        if on_goto is None:
            raise AssertionError("this flow answers no step by key")
        return on_goto(step)

    async def check(values: dict[str, Any], step: str) -> dict:
        if on_check is None:
            raise AssertionError("this flow answers no check")
        return on_check(values, step)

    async def act(key: str, values: dict[str, Any], step: str) -> dict:
        if on_act is None:
            raise AssertionError("this flow answers no act")
        return on_act(key, values, step)

    async def run(values: dict[str, Any], step: str) -> dict:
        return on_run(values, step) if on_run is not None else {"ok": True}

    async def job(job_id: str) -> dict:
        raise AssertionError("no test here drives a job")

    return wizard.Calls(first_call, check, act, run, job)


async def _settled() -> None:
    for _ in range(8):
        await asyncio.sleep(0)


async def _opened(calls: wizard.Calls, *, step: str | None = None) -> ui.element:
    """The page, drawn past its first render. Unlike the dialog host, `open_page`
    returns once it has drawn rather than staying open, so there is no task to hold."""
    was = set(_PAGE.client.layout.descendants())
    with _PAGE:
        await wizard.open_page(label="Test", calls=calls, under="test.action", step=step)
    await _settled()
    return next(one for one in set(_PAGE.client.layout.descendants()) - was
               if "manager-wizard-page" in one.classes)


def _forward(page: ui.element) -> ui.button:
    return next(one for one in page.descendants()
               if isinstance(one, ui.button) and wizard._FORWARD in one.classes)


def _labelled(page: ui.element, text: str) -> ui.label:
    return next(one for one in page.descendants()
               if isinstance(one, ui.label) and one.text == text)


def _segments(root: ui.element) -> list[tuple[str, str, bool]]:
    """Each step of the row as (name, state, reopens): `here`, `done` or '' for one still
    ahead, and whether its name answers a click."""
    found = []
    for one in root.descendants():
        if "manager-wizard-step" not in one.classes:
            continue
        name = next(inner for inner in one.descendants() if isinstance(inner, ui.label))
        state = next((c.removeprefix("manager-wizard-step--") for c in one.classes
                      if c.startswith("manager-wizard-step--")), "")
        found.append((name.text, state, "manager-link" in name.classes))
    return found


def _ran(run: Callable[[], Any]) -> Any:
    """`run`, with NiceGUI's loop set so a handler's own task is started at all."""
    async def inside() -> Any:
        was = core.loop
        core.loop = asyncio.get_running_loop()
        try:
            return await run()
        finally:
            core.loop = was

    return asyncio.run(inside())


class NotReadyTests(unittest.TestCase):
    def test_a_plain_step_held_not_ready_disables_next_and_says_why(self) -> None:
        first = {"step": "one", "fields": [], "ready": False, "reason": "Pick something first"}

        async def run() -> tuple[bool, str]:
            page = await _opened(_calls(first=first))
            disabled = _forward(page)._props.get("disable", False)
            reason = _labelled(page, "Pick something first").text
            return disabled, reason

        disabled, reason = _ran(run)
        self.assertEqual((True, "Pick something first"), (disabled, reason))


class ActTests(unittest.TestCase):
    def test_pressing_an_act_asks_for_it_by_key_and_redraws_the_same_step(self) -> None:
        first = {"step": "one", "fields": [], "acts": [{"key": "connect", "label": "Connect"}],
                 "ready": False, "reason": "Not connected"}
        redrawn = {"step": "one", "fields": [], "notes": ["Connected"], "ready": True}
        acted: list[tuple[str, str]] = []

        def acting(key: str, values: dict[str, Any], step: str) -> dict[str, Any]:
            acted.append((key, step))
            return redrawn

        async def run() -> tuple[list[tuple[str, str]], str, bool]:
            page = await _opened(_calls(first=first, on_act=acting))
            said(next(one for one in page.descendants()
                     if isinstance(one, ui.button) and one.text == "Connect"), "click")
            await _settled()
            note = _labelled(page, "Connected").text
            disabled = _forward(page)._props.get("disable", False)
            return acted, note, disabled

        found, note, disabled = _ran(run)
        self.assertEqual([("connect", "one")], found)
        self.assertEqual("Connected", note)
        self.assertFalse(disabled)


class StepRowTests(unittest.TestCase):
    def test_no_steps_key_draws_no_row(self) -> None:
        first = {"step": "one", "fields": []}

        async def run() -> bool:
            page = await _opened(_calls(first=first))
            return any("manager-wizard-steps" in one.classes for one in page.descendants())

        self.assertFalse(_ran(run))

    def test_the_row_has_one_segment_per_step_and_only_a_done_one_reopens(self) -> None:
        first = {"step": "two", "fields": [],
                 "steps": [{"key": "one", "label": "One", "done": True},
                          {"key": "two", "label": "Two", "done": False},
                          {"key": "three", "label": "Three", "done": False}]}

        async def run() -> list[tuple[str, str, bool]]:
            return _segments(await _opened(_calls(first=first)))

        self.assertEqual([("One", "done", True), ("Two", "here", False),
                          ("Three", "", False)], _ran(run))

    def test_the_current_segment_alone_says_it_is_the_current_step(self) -> None:
        first = {"step": "two", "fields": [],
                 "steps": [{"key": "one", "label": "One", "done": True},
                          {"key": "two", "label": "Two", "done": False}]}

        async def run() -> list[tuple[str, str | None]]:
            page = await _opened(_calls(first=first))
            return [(next(inner for inner in one.descendants()
                          if isinstance(inner, ui.label)).text, one.props.get("aria-current"))
                    for one in page.descendants() if "manager-wizard-step" in one.classes]

        self.assertEqual([("One", None), ("Two", "step")], _ran(run))

    def test_a_welcome_centers_the_step_and_not_the_row_above_it(self) -> None:
        first = {"step": "hello", "welcome": True, "title": "Hello", "fields": [],
                 "steps": [{"key": "hello", "label": "Start", "done": False}]}

        async def run() -> tuple[bool, list[bool]]:
            page = await _opened(_calls(first=first))
            centered = [one for one in page.descendants()
                        if "manager-wizard--welcome" in one.classes]
            row = next(one for one in page.descendants() if "manager-wizard-steps" in one.classes)
            title = _labelled(page, "Hello")
            inside = [{id(inner) for inner in one.descendants()} for one in centered]
            return ("manager-wizard--welcome" in page.classes,
                    [id(title) in held and id(row) not in held for held in inside])

        on_page, scoped = _ran(run)
        self.assertFalse(on_page)
        self.assertEqual([True], scoped)

    def test_clicking_a_done_step_reopens_it_by_key(self) -> None:
        first = {"step": "two", "fields": [],
                 "steps": [{"key": "one", "label": "One", "done": True},
                          {"key": "two", "label": "Two", "done": False}]}
        reopened = {"step": "one", "fields": [], "notes": ["Back on one"],
                   "steps": first["steps"]}
        asked: list[str] = []

        def going(key: str) -> dict[str, Any]:
            asked.append(key)
            return reopened

        async def run() -> tuple[list[str], str]:
            page = await _opened(_calls(first=first, on_goto=going))
            said(_labelled(page, "One"), "click")
            await _settled()
            note = _labelled(page, "Back on one").text
            return asked, note

        asked, note = _ran(run)
        self.assertEqual(["one"], asked)
        self.assertEqual("Back on one", note)


class RowOnRunTests(unittest.TestCase):
    """The row says where a person is in the steps, and a run is not one of them."""

    _last = {"step": "review", "fields": [], "summary": [["Folder", "/tables"]],
             "steps": [{"key": "one", "label": "One", "done": True},
                      {"key": "review", "label": "Review", "done": False}]}

    def _pressed(self, calls: wizard.Calls) -> list[tuple[str, str, bool]]:
        async def run() -> list[tuple[str, str, bool]]:
            page = await _opened(calls)
            said(_forward(page), "click")
            await _settled()
            return _segments(page)

        return _ran(run)

    def test_a_run_that_finishes_at_once_takes_the_row_away(self) -> None:
        self.assertEqual([], self._pressed(_calls(
            first=self._last, on_run=lambda values, step: {"ok": True, "message": "All set"})))

    def test_a_run_that_becomes_a_job_takes_the_row_away(self) -> None:
        async def done(_job_id: str) -> dict:
            return {"state": "done", "pct": 100}

        calls = _calls(first=self._last, on_run=lambda values, step: {"job_id": "j1"})
        calls.job = done

        self.assertEqual([], self._pressed(calls))

    def test_a_run_that_is_refused_leaves_the_row_for_the_step_it_is_still_on(self) -> None:
        with mock.patch.object(ui, "notify"):
            segments = self._pressed(_calls(
                first=self._last, on_run=lambda values, step: {"ok": False, "reason": "Not yet"}))

        self.assertEqual([("One", "done", True), ("Review", "here", False)], segments)


class TitleFocusTests(unittest.TestCase):
    def test_a_step_transition_focuses_the_title_not_a_field(self) -> None:
        first = {"step": "one", "fields": [{"key": "name", "type": "string", "label": "Name"}]}
        second = {"step": "two", "fields": []}

        async def run() -> list[str]:
            with mock.patch.object(ui, "run_javascript") as js:
                page = await _opened(_calls(first=first, on_check=lambda v, s: second))
                heading = next(one for one in page.descendants()
                              if "manager-dialog-title" in one.classes)
                js.reset_mock()
                said(_forward(page), "click")
                await _settled()
                calls_seen = [call.args[0] for call in js.call_args_list]
            return [text for text in calls_seen if f"c{heading.id}" in text]

        self.assertTrue(_ran(run))

    def test_an_act_redraw_focuses_the_field_not_the_title(self) -> None:
        first = {"step": "one", "fields": [{"key": "name", "type": "string", "label": "Name"}],
                 "acts": [{"key": "go", "label": "Go"}]}
        redrawn = {"step": "one", "fields": [{"key": "name", "type": "string", "label": "Name"}]}

        async def run() -> bool:
            with mock.patch.object(ui, "run_javascript") as js:
                page = await _opened(_calls(first=first, on_act=lambda k, v, s: redrawn))
                heading = next(one for one in page.descendants()
                              if "manager-dialog-title" in one.classes)
                js.reset_mock()
                said(next(one for one in page.descendants()
                         if isinstance(one, ui.button) and one.text == "Go"), "click")
                await _settled()
                calls_seen = [call.args[0] for call in js.call_args_list]
            return any(f"c{heading.id}" in text for text in calls_seen)

        self.assertFalse(_ran(run))


class AddressTests(unittest.TestCase):
    def test_opening_writes_the_first_step_into_the_address(self) -> None:
        first = {"step": "one", "fields": []}

        async def run() -> list[str]:
            with mock.patch.object(ui, "run_javascript") as js:
                await _opened(_calls(first=first))
                return [call.args[0] for call in js.call_args_list]

        calls_seen = _ran(run)
        self.assertTrue(any("replaceState" in text and '"one"' in text
                            for text in calls_seen))

    def test_pressing_next_writes_the_new_step_into_the_address(self) -> None:
        first = {"step": "one", "fields": []}
        second = {"step": "two", "fields": []}

        async def run() -> list[str]:
            with mock.patch.object(ui, "run_javascript") as js:
                page = await _opened(_calls(first=first, on_check=lambda v, s: second))
                js.reset_mock()
                said(_forward(page), "click")
                await _settled()
                return [call.args[0] for call in js.call_args_list]

        calls_seen = _ran(run)
        self.assertTrue(any("replaceState" in text and '"two"' in text
                            for text in calls_seen))

    def test_a_step_given_at_open_is_asked_for_by_key_not_started_fresh(self) -> None:
        resumed = {"step": "two", "fields": [], "notes": ["Resumed"]}
        seen: list[str | None] = []

        async def run() -> tuple[list[str | None], str]:
            page = await _opened(_calls(first={"step": "one", "fields": []},
                                        on_goto=lambda key: resumed, seen_first=seen),
                                 step="two")
            note = _labelled(page, "Resumed").text
            return seen, note

        seen, note = _ran(run)
        self.assertEqual(["two"], seen)
        self.assertEqual("Resumed", note)


class RunTests(unittest.TestCase):
    def test_finishing_without_a_job_draws_the_message_and_no_close_button(self) -> None:
        first = {"step": "one", "fields": [], "summary": [["Ready"]]}

        async def run() -> tuple[str, bool]:
            page = await _opened(_calls(first=first,
                                        on_run=lambda values, step: {"message": "All set"}))
            said(_forward(page), "click")
            await _settled()
            message = _labelled(page, "All set").text
            has_close = any(isinstance(one, ui.button) and one.text == "Close"
                            for one in page.descendants())
            return message, has_close

        message, has_close = _ran(run)
        self.assertEqual("All set", message)
        self.assertFalse(has_close)


if __name__ == "__main__":
    unittest.main()
