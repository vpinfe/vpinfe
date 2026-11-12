"""The wizard's fields for long lists and for the person's own pictures, each driven with
a stub flow: what they draw, and what reaches the next call as the answer."""

from __future__ import annotations

import unittest
from collections.abc import Callable
from typing import Any
from unittest import mock

from nicegui import ui

from common.i18n import t
from manager.kit import wizard
from manager.library import vps_match, wizard_settle
from tests.manager.test_wizard_dialog import (
    _calls,
    _finished,
    _forward,
    _opened,
    _ran,
    _settled,
)
from tests.support.clicks import said


def _walked(first: dict[str, Any], seen: list[dict[str, Any]]) -> wizard.Calls:
    def on_check(values: dict[str, Any], _step: str) -> dict[str, Any]:
        seen.append({key: (list(one) if isinstance(one, list) else dict(one)
                           if isinstance(one, dict) else one) for key, one in values.items()})
        return {"step": "done"}

    return _calls(first=first, on_check=on_check)


def _row_names(dialog: ui.dialog, *, shown: bool = True) -> list[str]:
    return [next(inner.text for inner in one.descendants() if isinstance(inner, ui.label))
            for one in dialog.descendants()
            if "manager-wizard-listrow" in one.classes and (one.visible or not shown)]


def _buttons(dialog: ui.dialog, text: str) -> list[ui.button]:
    return [one for one in dialog.descendants()
            if isinstance(one, ui.button) and one.text == text]


_TABLES = {
    "step": "tables",
    "fields": [{
        "key": "tables", "type": "checklist", "label": "Tables",
        "value": ["a"],
        "rows": [
            {"key": "a", "name": "Attack from Mars", "note": "New", "groups": ["new"]},
            {"key": "b", "name": "Black Knight", "note": "In VPinFE", "groups": ["have"]},
            {"key": "c", "name": "Cirqus Voltaire", "note": "New", "groups": ["new"]},
        ],
        "chips": [["new", "New"], ["have", "In VPinFE"], ["hidden", "Hidden"]],
    }],
}


_TOKEN = {"step": "key", "fields": [
    {"key": "token", "type": "secret", "label": "Token", "set": True},
    {"key": "name", "type": "string", "label": "Name"}]}


class SecretTests(unittest.TestCase):
    def _inputs(self, dialog: ui.dialog) -> list[ui.input]:
        return [one for one in dialog.descendants() if isinstance(one, ui.input)]

    def test_a_secret_is_drawn_empty_masked_and_says_it_is_set(self) -> None:
        async def run() -> tuple[Any, Any, Any]:
            task, dialog = await _opened(_calls(first=_TOKEN))
            box = self._inputs(dialog)[0]
            found = (box.value, box.props.get("type"), box.props.get("placeholder"))
            await _finished(task)
            return found

        value, kind, placeholder = _ran(run)
        self.assertEqual((value, kind), ("", "password"))
        self.assertEqual(placeholder, t("manager.ext_page.secret_set"))

    def test_nothing_typed_sends_no_key_and_what_is_typed_is_sent(self) -> None:
        seen: list[dict[str, Any]] = []

        async def run() -> None:
            task, dialog = await _opened(_walked(_TOKEN, seen))
            said(_forward(dialog), "click")
            await _settled()
            await _finished(task)

        _ran(run)
        self.assertEqual(seen, [{"name": ""}])

        seen.clear()

        async def typed() -> None:
            task, dialog = await _opened(_walked(_TOKEN, seen))
            self._inputs(dialog)[0].set_value("abc")
            said(_forward(dialog), "click")
            await _settled()
            await _finished(task)

        _ran(typed)
        self.assertEqual(seen, [{"token": "abc", "name": ""}])


class DrawersTests(unittest.TestCase):
    def test_a_type_that_registers_its_drawer_has_one(self) -> None:
        """`FIELD_TYPES` names the type and `FIELD_DRAWERS` is what draws it."""
        self.assertIn("settle", wizard.FIELD_TYPES)
        self.assertIn("settle", wizard.FIELD_DRAWERS)


class ChecklistTests(unittest.TestCase):
    def test_every_row_is_drawn_with_its_note_and_the_ticked_one_is_ticked(self) -> None:
        async def run() -> tuple[list[str], list[bool]]:
            task, dialog = await _opened(_calls(first=_TABLES))
            found = (_row_names(dialog),
                     [one.value for one in dialog.descendants()
                      if isinstance(one, ui.checkbox)])
            await _finished(task)
            return found

        names, ticks = _ran(run)
        self.assertEqual(["Attack from Mars", "Black Knight", "Cirqus Voltaire"], names)
        self.assertEqual([True, False, False], ticks)

    def test_a_chip_is_drawn_only_for_a_group_that_has_rows(self) -> None:
        async def run() -> list[str]:
            task, dialog = await _opened(_calls(first=_TABLES))
            toggle = next(one for one in dialog.descendants() if isinstance(one, ui.toggle))
            options = list(toggle.options)
            await _finished(task)
            return options

        self.assertEqual(["", "new", "have"], _ran(run))

    def test_a_chip_and_the_name_filter_narrow_the_rows(self) -> None:
        async def run() -> tuple[list[str], list[str], list[str]]:
            task, dialog = await _opened(_calls(first=_TABLES))
            toggle = next(one for one in dialog.descendants() if isinstance(one, ui.toggle))
            toggle.set_value("new")
            narrowed = _row_names(dialog)
            find = next(one for one in dialog.descendants()
                        if isinstance(one, ui.input) and one.props.get("placeholder"))
            find.set_value("cirq")
            both = _row_names(dialog)
            find.set_value("nothing like it")
            none = _row_names(dialog)
            await _finished(task)
            return narrowed, both, none

        narrowed, both, none = _ran(run)
        self.assertEqual(["Attack from Mars", "Cirqus Voltaire"], narrowed)
        self.assertEqual(["Cirqus Voltaire"], both)
        self.assertEqual([], none)

    def test_select_all_and_none_act_only_on_the_rows_shown(self) -> None:
        seen: list[dict[str, Any]] = []

        async def run() -> None:
            task, dialog = await _opened(_walked(_TABLES, seen))
            toggle = next(one for one in dialog.descendants() if isinstance(one, ui.toggle))
            toggle.set_value("have")
            said(_buttons(dialog, "Select All")[0], "click")
            toggle.set_value("")
            said(_forward(dialog), "click")
            await _settled()
            await _finished(task)

        _ran(run)
        self.assertEqual([{"tables": ["a", "b"]}], seen)

    def test_select_none_clears_what_the_filter_shows(self) -> None:
        seen: list[dict[str, Any]] = []

        async def run() -> None:
            task, dialog = await _opened(_walked(_TABLES, seen))
            said(_buttons(dialog, "Select None")[0], "click")
            said(_forward(dialog), "click")
            await _settled()
            await _finished(task)

        _ran(run)
        self.assertEqual([{"tables": []}], seen)

    def test_enter_in_the_filter_stops_before_the_wizard_hears_it(self) -> None:
        async def run() -> list[str]:
            task, dialog = await _opened(_calls(first=_TABLES))
            find = next(one for one in dialog.descendants()
                        if isinstance(one, ui.input) and one.props.get("placeholder"))
            types = [one.type for one in find._event_listeners.values()]
            await _finished(task)
            return types

        self.assertIn("keyup.enter.stop", _ran(run))

    def test_the_count_follows_the_ticks(self) -> None:
        async def run() -> tuple[str, str]:
            task, dialog = await _opened(_calls(first=_TABLES))
            was = next(one.text for one in dialog.descendants()
                       if isinstance(one, ui.label) and "selected" in one.text)
            next(one for one in dialog.descendants()
                 if isinstance(one, ui.checkbox)).set_value(False)
            now = next(one.text for one in dialog.descendants()
                       if isinstance(one, ui.label) and "selected" in one.text)
            await _finished(task)
            return was, now

        self.assertEqual(("1 of 3 selected", "0 of 3 selected"), _ran(run))


_MATCHES = {
    "step": "matches",
    "fields": [{
        "key": "matches", "type": "settle", "label": "Matches",
        "rows": [
            {"key": "x", "name": "Xenon", "entry": {}, "reason": "Two entries"},
            {"key": "y", "name": "Yukon", "entry": {
                "name": "Yukon", "manufacturer": "Gottlieb", "year": 1971},
             "vps_id": "vps-y", "settled": True},
            {"key": "z", "name": "Zira", "entry": {}, "reason": ""},
        ],
    }],
}


class _Held:
    def vps_entry(self, vps_id: str) -> dict[str, Any]:
        return {"name": f"Entry {vps_id}"}


async def _io(function: Callable[..., Any], *args: Any) -> Any:
    return function(*args)


def _asked(answer: Any) -> Any:
    async def ask(_library: Any, game: dict[str, Any], *_rest: Any, **_more: Any) -> Any:
        ask.games.append(game)  # type: ignore[attr-defined]
        return answer
    ask.games = []  # type: ignore[attr-defined]
    return ask


class SettleTests(unittest.TestCase):
    def test_a_settled_row_waits_behind_show_all(self) -> None:
        async def run() -> tuple[list[str], list[str], list[str]]:
            task, dialog = await _opened(_calls(first=_MATCHES))
            before = _row_names(dialog)
            say = [one.text for one in dialog.descendants()
                   if isinstance(one, ui.label) and one.text in ("Not found - Two entries",
                                                                 "Not found")]
            said(_buttons(dialog, "Show All 3")[0], "click")
            after = _row_names(dialog)
            await _finished(task)
            return before, say, after

        before, say, after = _ran(run)
        self.assertEqual(["Xenon", "Zira"], before)
        self.assertEqual(["Not found - Two entries", "Not found"], say)
        self.assertEqual(["Xenon", "Yukon", "Zira"], after)

    def test_change_writes_the_pick_into_the_answer_and_names_it(self) -> None:
        seen: list[dict[str, Any]] = []
        ask = _asked("vps-9")

        async def run() -> list[str]:
            with mock.patch.object(vps_match, "ask", ask), \
                    mock.patch.object(wizard_settle, "Library", lambda _client: _Held()), \
                    mock.patch.object(wizard_settle.offload, "io", _io):
                task, dialog = await _opened(_walked(_MATCHES, seen))
                said(_buttons(dialog, "Change…")[0], "click")
                await _settled()
                lines = [one.text for one in dialog.descendants()
                         if isinstance(one, ui.label) and one.text.startswith("Entry")]
                said(_forward(dialog), "click")
                await _settled()
                await _finished(task)
            return lines

        lines = _ran(run)
        self.assertEqual(["Entry vps-9"], lines)
        self.assertEqual([{"matches": {"x": "vps-9"}}], seen)
        self.assertEqual("Xenon", ask.games[0]["name"])

    def test_clearing_a_match_answers_an_empty_id(self) -> None:
        seen: list[dict[str, Any]] = []

        async def run() -> None:
            with mock.patch.object(vps_match, "ask", _asked(vps_match.CLEARED)), \
                    mock.patch.object(wizard_settle, "Library", lambda _client: _Held()), \
                    mock.patch.object(wizard_settle.offload, "io", _io):
                task, dialog = await _opened(_walked(_MATCHES, seen))
                said(_buttons(dialog, "Change…")[0], "click")
                await _settled()
                said(_forward(dialog), "click")
                await _settled()
                await _finished(task)

        _ran(run)
        self.assertEqual([{"matches": {"x": ""}}], seen)

    def test_cancelling_the_picker_leaves_the_answer_alone(self) -> None:
        seen: list[dict[str, Any]] = []

        async def run() -> None:
            with mock.patch.object(vps_match, "ask", _asked(vps_match.CANCELLED)), \
                    mock.patch.object(wizard_settle, "Library", lambda _client: _Held()):
                task, dialog = await _opened(_walked(_MATCHES, seen))
                said(_buttons(dialog, "Change…")[0], "click")
                await _settled()
                said(_forward(dialog), "click")
                await _settled()
                await _finished(task)

        _ran(run)
        self.assertEqual([{"matches": {}}], seen)

    def test_a_failed_name_lookup_still_answers_and_reads_picked(self) -> None:
        seen: list[dict[str, Any]] = []

        class Down(_Held):
            def vps_entry(self, vps_id: str) -> dict[str, Any]:
                raise OSError("down")

        async def run() -> list[str]:
            with mock.patch.object(vps_match, "ask", _asked("vps-9")), \
                    mock.patch.object(wizard_settle, "Library", lambda _client: Down()), \
                    mock.patch.object(wizard_settle.offload, "io", _io):
                task, dialog = await _opened(_walked(_MATCHES, seen))
                said(_buttons(dialog, "Change…")[0], "click")
                await _settled()
                lines = [one.text for one in dialog.descendants()
                         if isinstance(one, ui.label) and one.text == "Picked"]
                said(_forward(dialog), "click")
                await _settled()
                await _finished(task)
            return lines

        self.assertEqual(["Picked"], _ran(run))
        self.assertEqual([{"matches": {"x": "vps-9"}}], seen)

    def test_a_pick_made_before_is_named_when_the_step_is_drawn_again(self) -> None:
        first = {"step": "matches", "fields": [{
            "key": "matches", "type": "settle", "label": "Matches", "value": {"x": "vps-4"},
            "rows": [{"key": "x", "name": "Xenon", "entry": {}}]}]}

        async def run() -> list[str]:
            with mock.patch.object(wizard_settle, "Library", lambda _client: _Held()), \
                    mock.patch.object(wizard_settle.offload, "io", _io):
                task, dialog = await _opened(_calls(first=first))
                await _settled()
                lines = [one.text for one in dialog.descendants()
                         if isinstance(one, ui.label) and one.text.startswith("Entry")]
                await _finished(task)
            return lines

        self.assertEqual(["Entry vps-4"], _ran(run))

    def test_change_after_a_cleared_match_starts_from_nothing(self) -> None:
        first = {"step": "matches", "fields": [{
            "key": "matches", "type": "settle", "label": "Matches", "value": {"x": ""},
            "rows": [{"key": "x", "name": "Xenon", "candidate": "", "vps_id": "old"}]}]}
        ask = _asked(vps_match.CANCELLED)

        async def run() -> None:
            with mock.patch.object(vps_match, "ask", ask), \
                    mock.patch.object(wizard_settle, "Library", lambda _client: _Held()):
                task, dialog = await _opened(_calls(first=first))
                said(_buttons(dialog, "Change…")[0], "click")
                await _settled()
                await _finished(task)

        _ran(run)
        self.assertEqual("", ask.games[0]["vps_id"])

    def test_a_list_with_no_rows_says_nothing_about_counts(self) -> None:
        first = {"step": "m", "fields": [
            {"key": "a", "type": "settle", "label": "A", "rows": []},
            {"key": "b", "type": "checklist", "label": "B", "rows": []}]}

        async def run() -> list[str]:
            task, dialog = await _opened(_calls(first=first))
            found = [one.text for one in dialog.descendants()
                     if isinstance(one, ui.label)
                     and ("settled" in one.text or "selected" in one.text)]
            await _finished(task)
            return found

        self.assertEqual([], _ran(run))

    def test_nothing_to_settle_says_so_and_shows_the_rows_on_request(self) -> None:
        first = {"step": "m", "fields": [{
            "key": "matches", "type": "settle", "label": "Matches",
            "rows": [{"key": "y", "name": "Yukon", "candidate": "Yukon", "settled": True}]}]}

        async def run() -> tuple[list[str], bool]:
            task, dialog = await _opened(_calls(first=first))
            found = ([one.text for one in dialog.descendants()
                      if isinstance(one, ui.label) and "settled" in one.text],
                     bool(_row_names(dialog)))
            await _finished(task)
            return found

        words, shown = _ran(run)
        self.assertEqual(["All 1 are settled"], words)
        self.assertFalse(shown)


class PictureFromFilesTests(unittest.TestCase):
    def test_a_choice_names_files_and_each_is_fetched_through_the_file_route(self) -> None:
        first = {"step": "media", "fields": [{
            "key": "kind", "type": "choice", "label": "Wheels",
            "choices": [["logos", "Logos", "Wide and clear",
                         ["/lib/wheels/a b.png", "/lib/wheels/c.png"]],
                        ["wheels", "Wheels", "Round", "image"]]}]}

        async def run() -> list[str]:
            task, dialog = await _opened(_calls(first=first))
            sources = [str(one.props.get("src")) for one in dialog.descendants()
                       if one.tag == "img"]
            await _finished(task)
            return sources

        self.assertEqual(
            ["/api/v1/filesystem/file?path=%2Flib%2Fwheels%2Fa+b.png",
             "/api/v1/filesystem/file?path=%2Flib%2Fwheels%2Fc.png"], _ran(run))

    def test_the_same_files_on_every_option_are_drawn_once_above_them(self) -> None:
        shown = ["/lib/wheels/a.png", "/lib/wheels/b.png"]
        first = {"step": "media", "fields": [{
            "key": "kind", "type": "choice", "label": "Wheels",
            "choices": [["logos", "Logos", "Wide and clear", shown],
                        ["wheels", "Wheels", "Round", shown]]}]}

        async def run() -> tuple[list[str], int]:
            task, dialog = await _opened(_calls(first=first))
            sources = [str(one.props.get("src")) for one in dialog.descendants()
                       if one.tag == "img"]
            rows = sum(1 for one in dialog.descendants()
                       if "manager-wizard-pick" in one.classes)
            await _finished(task)
            return sources, rows

        sources, rows = _ran(run)

        self.assertEqual(2, len(sources))
        self.assertEqual(2, rows)

    def test_a_picture_that_is_a_name_stays_a_name(self) -> None:
        self.assertEqual("desktop", wizard._picture_named("desktop"))
        self.assertEqual("", wizard._picture_named(None))
        self.assertEqual(["/a.png"], wizard._picture_named(["/a.png", ""]))


if __name__ == "__main__":
    unittest.main()
