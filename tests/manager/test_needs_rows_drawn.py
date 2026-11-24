"""The Needs You rows the library's metadata raises, as the Overview draws them."""

from __future__ import annotations

import unittest
from typing import Any
from unittest import mock
from unittest.mock import Mock

from nicegui import ui

from manager.overview import needs
from manager.settings import sections
from tests.manager.test_needs import METADATA, FakeLibrary


class _Overview(FakeLibrary):
    def dismissed(self) -> dict[str, list[str]]:
        return {}


def _rows() -> dict[str, needs.Need]:
    return {one.key: one for one in needs.collect(FakeLibrary(metadata=METADATA), {})}


def _labels(within: ui.element) -> list[str]:
    return [one.text for one in within.descendants() if isinstance(one, ui.label)]


class TheRowsAreDrawn(unittest.TestCase):
    def _drawn(self, key: str) -> ui.element:
        with ui.column() as body:
            sections._need_row(_rows()[key], {"open_on": Mock()}, Mock(), Mock())
        return body

    def _menus(self, body: ui.element) -> list[ui.element]:
        return [one for one in body.descendants() if isinstance(one, (ui.menu, ui.context_menu))]

    def test_a_row_that_cannot_be_set_aside_draws_no_menu(self) -> None:
        for key in ("unwritten", "newer_build"):
            body = self._drawn(key)

            self.assertEqual([], self._menus(body), key)
            self.assertEqual([], [one for one in body.descendants()
                                  if isinstance(one, ui.button)], key)

    def test_older_format_draws_a_menu_that_offers_the_upgrade_first(self) -> None:
        body = self._drawn("older_format")

        menus = self._menus(body)
        self.assertEqual(2, len(menus))
        for menu in menus:
            self.assertEqual("Upgrade", _labels(menu)[0])

    def test_newer_build_says_how_many_games_and_names_none(self) -> None:
        body = self._drawn("newer_build")

        self.assertEqual(["Written by a Newer VPinFE", "2 games"], _labels(body))
        self.assertEqual((), _rows()["newer_build"].ids)

    def test_each_folder_row_says_its_name_and_count(self) -> None:
        self.assertEqual(["Could Not Save", "2 games"], _labels(self._drawn("unwritten")))
        self.assertEqual(["Older Format", "2 games"], _labels(self._drawn("older_format"))[:2])


class TheUpgradeFix(unittest.TestCase):
    def test_the_overview_runs_the_upgrade_for_the_older_format_row(self) -> None:
        library: Any = _Overview(metadata=METADATA)
        state: dict[str, Any] = {"view": "overview", "open_on": Mock()}
        drawn: dict[str, Any] = {}

        def needs_you(_arranged: Any, _state: Any, fix: Any, _acts: Any) -> None:
            drawn["fix"] = fix

        with mock.patch.object(sections, "_needs_you", needs_you), \
                mock.patch.object(sections, "_prune"), \
                mock.patch.object(sections, "_library"), \
                mock.patch.object(sections, "_recent_jobs"), \
                mock.patch.object(sections.front, "top"), \
                mock.patch.object(sections.front, "strip"), \
                mock.patch.object(ui, "timer"), \
                mock.patch.object(sections, "_metadata_action") as action, \
                ui.column():
            sections.overview(library, [], {}, Mock(), state)
            drawn["fix"](_rows()["older_format"])

        action.assert_called_once()
        self.assertIs(library, action.call_args.args[0])
        self.assertIs(state, action.call_args.args[1])
        action.return_value.assert_called_once_with("upgrade")


if __name__ == "__main__":
    unittest.main()
