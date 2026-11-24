"""A dialog an extension supplies can be opened for a game the library may not hold yet,
over a dialog that is already open, and a step's note can link out to where a file is
hosted."""

from __future__ import annotations

import unittest
from collections.abc import Awaitable, Callable
from typing import Any
from unittest import mock

from nicegui import ui

from manager.extensions import ext_action
from manager.kit import offload, wizard
from tests.manager.test_wizard_welcome import _PAGE, _classed, _drawn, _finished, _ran

WHO = {"game_id": "", "vps_id": "abcdefghij", "name": "Taxi"}
ACTION = {"key": "take", "label": "Get a file", "base": "/actions/take"}
STEP = {"step": "pick", "title": "Pick a file", "fields": [], "confirm": "Get it?"}


async def _direct(callback: Callable[..., Any], *args: Any) -> Any:
    return callback(*args)


async def _on_the_page(opening: Callable[[], Awaitable[Any]]) -> Any:
    with _PAGE:
        return await opening()


class Posts:
    """The extension's end of the wire, answering every call with `STEP`."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, dict]] = []

    def client(self) -> type:
        sent = self.sent

        class Client:
            def ext_post(self, path: str, body: dict, _timeout: int | None = None) -> dict:
                sent.append((path, body))
                return STEP

        return Client

    async def opened(self, **kwargs: Any) -> tuple[Any, ui.dialog]:
        with mock.patch.object(ext_action, "ApiClient", self.client()), \
                mock.patch.object(offload, "io", _direct):
            return await _drawn(lambda: ext_action.open_action("demo", ACTION, **kwargs))


class OpenedForAGame(unittest.TestCase):
    def test_the_first_ask_names_the_game_and_the_rows(self) -> None:
        posts = Posts()

        async def run() -> None:
            task, _dialog = await posts.opened(rows=["pick-1"], who=WHO)
            await _finished(task)

        _ran(run)

        self.assertEqual(("/ext/demo/actions/take/ask",
                          {"step": "", "rows": ["pick-1"], "who": WHO}), posts.sent[0])

    def test_a_dialog_opened_for_no_game_sends_no_who(self) -> None:
        posts = Posts()

        async def run() -> None:
            task, _dialog = await posts.opened(rows=["pick-1"])
            await _finished(task)

        _ran(run)

        self.assertEqual({"step": "", "rows": ["pick-1"]}, posts.sent[0][1])

    def test_the_answers_it_starts_with_hold_the_rows_and_not_the_game(self) -> None:
        opened = mock.AsyncMock(return_value=True)

        with mock.patch.object(ext_action.wizard, "open_dialog", opened):
            _ran(lambda: _on_the_page(lambda: ext_action.open_action(
                "demo", ACTION, values={"kept": "1"}, rows=["pick-1"], who=WHO)))

        self.assertEqual({"kept": "1", "rows": ["pick-1"]}, opened.call_args.kwargs["values"])

    def test_every_later_call_names_the_game_beside_its_values(self) -> None:
        posts = Posts()

        with mock.patch.object(ext_action, "ApiClient", posts.client()), \
                mock.patch.object(offload, "io", _direct):
            calls = ext_action.calls("demo", ACTION, ["pick-1"], WHO)
            _ran(lambda: calls.check({"kept": "1"}, "pick"))
            _ran(lambda: calls.act("more", {"kept": "1"}, "pick"))
            _ran(lambda: calls.run({"kept": "1"}, "pick"))

        bodies = [body for _path, body in posts.sent[-3:]]
        self.assertEqual([{"values": {"kept": "1"}, "step": "pick", "who": WHO},
                          {"act": "more", "values": {"kept": "1"}, "step": "pick", "who": WHO},
                          {"values": {"kept": "1"}, "step": "pick", "who": WHO}], bodies)

    def test_a_dialog_opened_for_neither_starts_with_what_it_was_given(self) -> None:
        opened = mock.AsyncMock(return_value=True)

        with mock.patch.object(ext_action.wizard, "open_dialog", opened):
            _ran(lambda: _on_the_page(lambda: ext_action.open_action("demo", ACTION)))

        self.assertEqual({}, opened.call_args.kwargs["values"])


class OverAnotherDialog(unittest.TestCase):
    def test_a_dialog_opened_while_another_is_open_leaves_that_one_open(self) -> None:
        posts = Posts()

        async def run() -> tuple[bool, bool, bool]:
            with _PAGE:
                under = ui.dialog()
                with under, ui.card():
                    ui.label("Add or replace")
            under.open()
            task, over = await posts.opened(rows=[], who=WHO)
            found = (under.value, over.value, over is not under)
            await _finished(task)
            return found

        still_open, open_over, distinct = _ran(run)

        self.assertTrue(still_open)
        self.assertTrue(open_over)
        self.assertTrue(distinct)


class NotesThatLinkOut(unittest.TestCase):
    def _drawn(self, lines: list[Any]) -> ui.element:
        with _PAGE:
            holder = ui.column()
            with holder:
                wizard._lines(lines, "")
        return holder

    def _links(self, holder: ui.element) -> list[ui.element]:
        return [one for one in holder.descendants() if isinstance(one, ui.link)]

    def test_a_note_with_a_web_address_is_drawn_as_a_link_out_in_a_new_tab(self) -> None:
        holder = self._drawn([{"text": "Where it is hosted", "to": "https://host.test/a b"}])

        (link,) = self._links(holder)

        self.assertEqual("https://host.test/a b", link.props["href"])
        self.assertEqual("_blank", link.props["target"])
        self.assertEqual("noopener noreferrer", link.props["rel"])
        self.assertEqual(["Where it is hosted", ],
                         [one.text for one in link.descendants() if isinstance(one, ui.label)])
        self.assertEqual(["open_in_new"],
                         [one._props["name"] for one in link.descendants()
                          if isinstance(one, ui.icon)])

    def test_a_note_with_a_detail_and_an_address_keeps_the_detail_as_the_tooltip(self) -> None:
        holder = self._drawn([{"text": "Hosted here", "detail": "The page it came from",
                               "to": "https://host.test/"}])

        (link,) = self._links(holder)

        self.assertEqual([("The page it came from", f"#c{link.id}")],
                         [(one.text, one.props["target"]) for one in holder.descendants()
                          if isinstance(one, ui.tooltip)])

    def test_a_note_without_an_address_stays_a_plain_line(self) -> None:
        holder = self._drawn(["Nothing to open", {"text": "Also plain", "detail": "More"}])

        self.assertEqual([], self._links(holder))
        self.assertEqual(2, len(_classed(holder, "manager-help")))

    def test_an_address_that_is_not_web_is_not_drawn_as_a_link(self) -> None:
        holder = self._drawn([{"text": "Open it", "to": "javascript:alert(1)"}])

        self.assertEqual([], self._links(holder))
        self.assertEqual(["Open it"],
                         [one.text for one in holder.descendants()
                          if isinstance(one, ui.label)])


if __name__ == "__main__":
    unittest.main()
