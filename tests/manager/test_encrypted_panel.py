"""What Security shows for encrypted connections, read off the entries without a browser."""

from __future__ import annotations

import asyncio
import inspect
import unittest
from typing import Any
from unittest import mock

from nicegui import ui

from common.i18n import t
from manager.kit import panel
from manager.kit.api import ApiError
from manager.security import encrypted_panel
from tests.support.clicks import press

# Made at import, outside any task, where NiceGUI still hands out the script's own page.
_PAGE = ui.element()


def _noop(*_args: Any) -> None:
    return None


def _entries(**held: Any) -> list[tuple[Any, ...]]:
    return encrypted_panel.entries(
        held, "Game Room", add_proxy=_noop, remove_proxy=_noop, add_network=_noop,
        add_tailscale=_noop, remove_network=_noop, choose=_noop, clear=_noop)


def _headings(rows: list[tuple[Any, ...]]) -> list[str]:
    return [row[1] for row in rows if row[0] == panel.HEADING]


def _page(drawn: list[str]) -> mock.MagicMock:
    def label(text: str = "", **_: Any) -> mock.MagicMock:
        drawn.append(text)
        return mock.MagicMock()

    page = mock.MagicMock()
    page.label.side_effect = label
    return page


def _text(rows: list[tuple[Any, ...]]) -> list[str]:
    """The labels and values, and what a `note`, `intro`, `lede` or entry would draw."""
    said = [str(cell) for row in rows for cell in row if isinstance(cell, str)]
    drawn: list[str] = []
    page = _page(drawn)
    with (mock.patch.object(panel, "line", lambda text, **_: drawn.append(text)),
          mock.patch.object(panel, "icon_action", lambda *_args, **_kw: _noop),
          mock.patch.object(panel, "ui", page),
          mock.patch.object(encrypted_panel, "ui", page)):
        for row in rows:
            for cell in row:
                if getattr(cell, "__qualname__", "").split(".")[0] in (
                        "note", "intro", "lede", "_entry"):
                    cell()
    return said + drawn


def _under(rows: list[tuple[Any, ...]], heading: str) -> list[str]:
    """The help lines drawn directly under a heading, before its first entry or button."""
    at = next(i for i, row in enumerate(rows) if row[0] == panel.HEADING and row[1] == heading)
    drawn: list[str] = []
    with mock.patch.object(panel, "ui", _page(drawn)):
        for row in rows[at + 1:]:
            if row[0] is not panel.LEDE:
                break
            row[1]()
    return drawn


class WhatSecurityShows(unittest.TestCase):
    def test_the_lead_says_what_a_listing_would_skip_and_on_whose_screen(self) -> None:
        said = _text(_entries())

        self.assertIn(t("manager.encrypted.lead", device="Game Room"), said)
        self.assertNotIn("{device}", " ".join(said))

    def test_the_two_lists_and_the_certificate_each_have_a_heading(self) -> None:
        self.assertEqual(_headings(_entries()), [
            t("manager.encrypted.heading"), t("manager.encrypted.proxies"),
            t("manager.encrypted.networks"), t("manager.encrypted.https")])

    def test_each_heading_says_what_its_group_does_before_any_entry_or_button(self) -> None:
        rows = _entries()

        self.assertEqual(_under(rows, t("manager.encrypted.heading")), [
            t("manager.encrypted.lead", device="Game Room"), t("manager.encrypted.lead_skip")])
        self.assertEqual(_under(rows, t("manager.encrypted.proxies")), [
            t("manager.encrypted.proxies_what"), t("manager.encrypted.proxies_line")])
        self.assertEqual(_under(rows, t("manager.encrypted.networks")), [
            t("manager.encrypted.networks_what"), t("manager.encrypted.networks_line")])
        self.assertEqual(_under(rows, t("manager.encrypted.https")), [
            t("manager.encrypted.https_line")])

    def test_an_entry_is_listed_by_its_own_address(self) -> None:
        said = _text(_entries(proxies=["192.168.1.5"], networks=["100.64.0.0/10"]))

        self.assertIn("192.168.1.5", said)
        self.assertIn("100.64.0.0/10", said)

    def test_the_lead_stays_once_something_is_set_up(self) -> None:
        said = _text(_entries(networks=["100.64.0.0/10"]))

        self.assertIn(t("manager.encrypted.lead", device="Game Room"), said)

    def test_each_empty_list_says_so_and_a_filled_one_does_not(self) -> None:
        empty = _text(_entries())
        filled = _text(_entries(proxies=["192.168.1.5"], networks=["100.64.0.0/10"]))

        for key in ("manager.encrypted.proxies_none", "manager.encrypted.networks_none"):
            self.assertIn(t(key), empty)
            self.assertNotIn(t(key), filled)

    def test_one_list_empty_leaves_the_other_alone(self) -> None:
        said = _text(_entries(networks=["100.64.0.0/10"]))

        self.assertIn(t("manager.encrypted.proxies_none"), said)
        self.assertNotIn(t("manager.encrypted.networks_none"), said)

    def test_each_remove_control_names_the_address_it_takes_off(self) -> None:
        named: list[str] = []
        page = mock.MagicMock()
        rows = _entries(proxies=["192.168.1.5"], networks=["100.64.0.0/10"])

        def record(label: str, *_args: Any, **_kw: Any) -> Any:
            named.append(label)
            return _noop

        with (mock.patch.object(panel, "icon_action", record),
              mock.patch.object(encrypted_panel, "ui", page)):
            for row in rows:
                for cell in row:
                    if getattr(cell, "__qualname__", "").split(".")[0] == "_entry":
                        cell()

        self.assertEqual(named, [t("manager.encrypted.remove_entry", address="192.168.1.5"),
                                 t("manager.encrypted.remove_entry", address="100.64.0.0/10")])

    def test_a_proxy_on_this_device_says_it_waits_for_https(self) -> None:
        for proxies in (["127.0.0.1"], ["10.0.0.5", "::1"]):
            with self.subTest(proxies=proxies):
                said = _text(_entries(proxies=proxies, https=False))

                self.assertIn(t("manager.encrypted.proxy_here"), said)

    def test_that_line_goes_once_https_is_served(self) -> None:
        said = _text(_entries(proxies=["127.0.0.1"], https=True))

        self.assertNotIn(t("manager.encrypted.proxy_here"), said)

    def test_a_proxy_on_another_device_has_no_such_line(self) -> None:
        with mock.patch.object(encrypted_panel.this_machine, "peer_is_this_machine",
                               return_value=False):
            said = _text(_entries(proxies=["10.0.0.5"], https=False))

        self.assertNotIn(t("manager.encrypted.proxy_here"), said)

    def test_without_a_certificate_it_says_it_serves_plain_http(self) -> None:
        said = _text(_entries())

        self.assertIn(t("manager.encrypted.https_none"), said)

    def test_a_certificate_shows_both_files_and_the_start_it_waits_for(self) -> None:
        said = _text(_entries(certfile="/certs/a.pem", keyfile="/certs/a.key", https=False))

        self.assertIn(t("manager.encrypted.next_start"), said)
        self.assertNotIn(t("manager.encrypted.https_none"), said)

    def test_a_certificate_being_served_has_no_start_to_wait_for(self) -> None:
        said = _text(_entries(certfile="/certs/a.pem", keyfile="/certs/a.key", https=True))

        self.assertNotIn(t("manager.encrypted.next_start"), said)

    def test_a_certificate_that_would_not_load_says_why_it_did_not(self) -> None:
        said = _text(_entries(certfile="/certs/a.pem", keyfile="/certs/a.key", https=False,
                              problem="unreadable"))

        self.assertIn(t("manager.encrypted.problem"), said)
        self.assertNotIn(t("manager.encrypted.next_start"), said)


def _the(kind: type, scope: ui.element) -> list[Any]:
    return [one for one in scope.descendants() if isinstance(one, kind)]


class TheAddDialog(unittest.TestCase):
    """What the Add Proxy and Add Network dialogs show on opening, and what Add does empty."""

    def setUp(self) -> None:
        patcher = mock.patch.object(ui, "run_javascript")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.keep: Any = None
        answer = encrypted_panel.frame.answer

        def answering(label: str, on_click: Any, **kwargs: Any) -> ui.button:
            self.keep = on_click
            return answer(label, on_click, **kwargs)

        answering_patch = mock.patch.object(encrypted_panel.frame, "answer", answering)
        answering_patch.start()
        self.addCleanup(answering_patch.stop)

    def _opened(self, act: Any, add: Any = None) -> Any:
        async def accepted(text: str) -> None:
            return None

        async def run() -> Any:
            async def ask() -> Any:
                with _PAGE:
                    return await encrypted_panel._ask_entry(
                        "Add", "Address", "such as 10.0.0.1", add or accepted)

            was = set(_the(ui.dialog, _PAGE.client.layout))
            asking = asyncio.create_task(ask())
            await asyncio.sleep(0)
            box = next(one for one in _the(ui.dialog, _PAGE.client.layout)
                       if one not in was and one.value)
            done = act(box)
            if inspect.isawaitable(done):
                await done
            return await asking

        return asyncio.run(run())

    def test_the_field_is_empty_and_the_example_is_a_line_under_it(self) -> None:
        seen: dict[str, Any] = {}

        def act(box: ui.dialog) -> None:
            field = _the(ui.input, box)[0]
            drawn = list(box.descendants())
            below = [one.text for one in drawn[drawn.index(field) + 1:]
                     if isinstance(one, ui.label)]
            seen.update(value=field.value, placeholder=field.props.get("placeholder"),
                        below=below)
            box.submit(None)

        self._opened(act)

        self.assertEqual(seen["value"], "")
        self.assertFalse(seen["placeholder"])
        self.assertIn("such as 10.0.0.1", seen["below"])

    def test_adding_nothing_answers_nothing_and_says_what_is_needed(self) -> None:
        seen: dict[str, Any] = {}

        async def act(box: ui.dialog) -> None:
            await press(self.keep)
            seen["open"] = box.value
            seen["error"] = _the(ui.input, box)[0].props.get("error-message")
            box.submit(None)

        self.assertFalse(self._opened(act))
        self.assertTrue(seen["open"])
        self.assertEqual(seen["error"], t("manager.encrypted.needs_address"))

    def test_a_refusal_stays_under_the_field_and_the_dialog_stays_open(self) -> None:
        asked: list[str] = []
        seen: dict[str, Any] = {}

        async def refuse(text: str) -> None:
            asked.append(text)
            raise ApiError("That range is wider than a /24")

        async def act(box: ui.dialog) -> None:
            field = _the(ui.input, box)[0]
            field.value = "10.0.0.0/8"
            await press(self.keep)
            seen["open"] = box.value
            seen["error"] = field.props.get("error-message")
            box.submit(None)

        self.assertFalse(self._opened(act, refuse))
        self.assertEqual(asked, ["10.0.0.0/8"])
        self.assertTrue(seen["open"])
        self.assertEqual(seen["error"], "That range is wider than a /24")

    def test_an_entry_that_is_taken_closes_the_dialog_with_a_yes(self) -> None:
        taken: list[str] = []

        async def take(text: str) -> None:
            taken.append(text)

        async def act(box: ui.dialog) -> None:
            _the(ui.input, box)[0].value = "  10.8.0.0/24  "
            await press(self.keep)

        self.assertTrue(self._opened(act, take))
        self.assertEqual(taken, ["10.8.0.0/24"])


if __name__ == "__main__":
    unittest.main()
