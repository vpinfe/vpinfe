"""A collection's Open On: what the frontend opens on, and the act that moves it."""

from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

from nicegui import run, ui

from common.games.collection_store import LAST_COLLECTION
from common.i18n import t
from manager.kit import panel
from manager.workbench import collection, rail
from tests.support.clicks import press
from tests.support.workbench_patch import patch_workbench

# Made at import, outside any task, where NiceGUI still hands out the script's own page.
_PAGE = ui.element()


async def _now(callback: Any, *args: Any, **kwargs: Any) -> Any:
    return callback(*args, **kwargs)


class OpenOn(unittest.IsolatedAsyncioTestCase):
    def _drawn(self, name: str, opens_on: str, all_games: bool = False,
               ) -> tuple[str, str | None, Any, dict[str, Any]]:
        """The value shown, the label of the one act, that act, and the context."""
        context = {"library": Mock(), "state": {},
                   "settings": {"behavior": {"startup_collection": opens_on}}}
        shown: list[str] = []
        acts: list[Any] = []

        def action(label: str, on_click: Any, **_: Any) -> Any:
            acts.append((label, on_click))
            return lambda: None

        class Label:
            def __init__(self, text: str) -> None:
                shown.append(text)

            def classes(self, *_: Any) -> Label:
                return self

        class Row:
            def __enter__(self) -> Row:
                return self

            def __exit__(self, *_: Any) -> None:
                return None

            def classes(self, *_: Any) -> Row:
                return self

        with patch.object(panel, "action", new=action), \
                patch.object(ui, "row", new=lambda: Row()), \
                patch.object(ui, "label", new=Label), _PAGE:
            collection._open_on(context, {"name": name}, all_games=all_games)()
        label, act = acts[0] if acts else (None, None)
        return shown[0], label, act, context

    async def _used(self, name: str, opens_on: str) -> dict[str, Any]:
        _, _, use, context = self._drawn(name, opens_on)
        with patch.object(run, "io_bound", new=_now), \
                patch_workbench("_settled", new=AsyncMock()) as settled:
            await press(use)
        context["settled"] = settled
        return context

    def test_it_names_the_collection_the_frontend_opens_on_and_offers_all_games(self) -> None:
        shown, label, _, _ = self._drawn("Friday Night", "Friday Night")

        self.assertEqual(("Friday Night", t("manager.workbench.open_on.use_all")),
                         (shown, label))

    def test_another_collection_is_named_and_this_one_is_offered(self) -> None:
        shown, label, _, _ = self._drawn("90s Bally", "Friday Night")

        self.assertEqual(("Friday Night", t("manager.workbench.open_on.use_this")),
                         (shown, label))

    def test_nothing_set_says_all_games_and_offers_this_one(self) -> None:
        shown, label, _, _ = self._drawn("90s Bally", "")

        self.assertEqual((t("manager.workbench.open_on.all_games"),
                          t("manager.workbench.open_on.use_this")), (shown, label))

    async def test_using_this_writes_the_setting_settings_writes(self) -> None:
        context = await self._used("90s Bally", "Friday Night")

        context["library"].put_config.assert_called_once_with(
            {"behavior": {"startup_collection": "90s Bally"}})
        context["settled"].assert_awaited_once()

    async def test_using_all_games_empties_it(self) -> None:
        context = await self._used("Friday Night", "Friday Night")

        context["library"].put_config.assert_called_once_with(
            {"behavior": {"startup_collection": ""}})

    def _all_games_select(self, opens_on: str) -> tuple[dict[str, str], str, Any, dict[str, Any]]:
        """The options, the value and the change handler the All Games row hands the select."""
        context = {"library": Mock(), "state": {},
                   "settings": {"behavior": {"startup_collection": opens_on}}}
        drawn: list[Any] = []

        def select(options: dict[str, str], value: str, on_change: Any, **_: Any) -> Any:
            drawn.append((options, value, on_change))
            return lambda: None

        with patch.object(panel, "select", new=select):
            collection._open_on(context, {"name": ""}, all_games=True)()
        options, value, on_change = drawn[0]
        return options, value, on_change, context

    def test_all_games_row_offers_the_two_special_choices(self) -> None:
        options, value, _, _ = self._all_games_select("")

        self.assertEqual(
            ({LAST_COLLECTION: t("manager.workbench.open_on.last_collection"),
              "": t("manager.workbench.open_on.all_games")}, ""), (options, value))

    def test_all_games_row_keeps_a_set_collection_on_the_list(self) -> None:
        options, value, _, _ = self._all_games_select("Friday Night")

        self.assertEqual(("Friday Night", "Friday Night"), (value, options["Friday Night"]))

    def test_all_games_row_shows_last_collection_when_that_is_set(self) -> None:
        options, value, _, _ = self._all_games_select(LAST_COLLECTION)

        self.assertEqual((LAST_COLLECTION, 2), (value, len(options)))

    async def _chosen(self, opens_on: str, picked: str) -> dict[str, Any]:
        _, _, on_change, context = self._all_games_select(opens_on)
        with patch.object(run, "io_bound", new=_now), \
                patch_workbench("_written", new=AsyncMock()):
            await press(on_change, Mock(value=picked))
        return context

    async def test_choosing_last_collection_writes_those_words(self) -> None:
        context = await self._chosen("", LAST_COLLECTION)

        context["library"].put_config.assert_called_once_with(
            {"behavior": {"startup_collection": LAST_COLLECTION}})

    async def test_choosing_all_games_empties_the_setting(self) -> None:
        context = await self._chosen("Friday Night", "")

        context["library"].put_config.assert_called_once_with(
            {"behavior": {"startup_collection": ""}})

    async def test_choosing_what_is_already_set_writes_nothing(self) -> None:
        context = await self._chosen(LAST_COLLECTION, LAST_COLLECTION)

        context["library"].put_config.assert_not_called()

    def test_a_collection_says_last_collection_when_that_is_set(self) -> None:
        shown, label, _, _ = self._drawn("Friday Night", LAST_COLLECTION)

        self.assertEqual((t("manager.workbench.open_on.last_collection"),
                          t("manager.workbench.open_on.use_this")), (shown, label))


class TheRememberedSection(unittest.TestCase):
    def test_selecting_all_games_leaves_the_section_a_collection_was_on(self) -> None:
        state = {"section": "collection_rules"}

        rail.chosen_section(state, "all_games")

        self.assertEqual("collection_rules", state["section"])


if __name__ == "__main__":
    unittest.main()
