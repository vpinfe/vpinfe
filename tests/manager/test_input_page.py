"""Hardware > Input: the binding table mounted over the input settings."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, Mock, patch

from fastapi.testclient import TestClient
from nicegui import ui

import httpapi
from common.config import config_schema, paths, setting_groups
from common.config.config_store import ConfigStore
from manager.kit import panel
from manager.kit.data import Library
from manager.settings import input_page, settings
from tests.support.catalogs import served as catalog
from tests.support.regions import Immediate

INPUT_PAGE = next(page for _group, pages in settings.DEVICE_INDEX for page in pages
                  if page[0] == input_page.KEY)


HEADINGS = {setting_groups.NAVIGATION: "Navigation", setting_groups.MENUS: "Menus",
            setting_groups.DURING_PLAY: "During Play"}


def _option(key: str, label: str, group: str, default: str = "", **more: Any) -> dict:
    return {"key": key, "label": label, "group": group, "group_label": HEADINGS[group],
            "default": default, **more}


OPTIONS = [
    _option("previous", "Previous", setting_groups.NAVIGATION, "key:ArrowLeft,key:ShiftLeft"),
    _option("menu", "Menu", setting_groups.MENUS, "key:KeyM"),
    _option("exit", "Quit", setting_groups.NAVIGATION, "key:Escape@hold:1500"),
    _option("take_picture", "Take Picture", setting_groups.DURING_PLAY,
            description="Pauses the game."),
]


class RowsTests(unittest.TestCase):
    def test_a_row_is_made_of_each_setting(self) -> None:
        rows = input_page.table_rows(OPTIONS)

        self.assertEqual([row.name for row in rows],
                         ["previous", "exit", "menu", "take_picture"])
        self.assertEqual([row.label for row in rows],
                         ["Previous", "Quit", "Menu", "Take Picture"])

    def test_a_heading_gathers_its_rows_though_the_schema_spreads_them(self) -> None:
        """Quit is declared after the menus, and belongs with navigation."""
        rows = input_page.table_rows(OPTIONS)

        self.assertEqual([row.group for row in rows],
                         ["Navigation", "Navigation", "Menus", "During Play"])

    def test_the_defaults_are_the_schema_s_string_split_into_bindings(self) -> None:
        [previous, *_] = input_page.table_rows(OPTIONS)

        self.assertEqual(previous.defaults, ("key:ArrowLeft", "key:ShiftLeft"))

    def test_an_input_heard_during_play_cannot_be_held(self) -> None:
        held = {row.name: row.holds for row in input_page.table_rows(OPTIONS)}

        self.assertEqual(held, {"previous": True, "exit": True, "menu": True,
                                "take_picture": False})

    def test_the_description_is_the_note_under_the_row(self) -> None:
        notes = {row.name: row.note for row in input_page.table_rows(OPTIONS)}

        self.assertEqual(notes["take_picture"], "Pauses the game.")
        self.assertEqual(notes["previous"], "")

    def test_a_finding_goes_on_the_row_it_was_found_for(self) -> None:
        def finding() -> None:
            return None

        rows = {row.name: row for row in input_page.table_rows(
            OPTIONS, {"take_picture": [finding]})}

        self.assertEqual(rows["take_picture"].findings, (finding,))
        self.assertEqual(rows["previous"].findings, ())


class MountedTests(unittest.TestCase):
    def _block(self, **more: Any) -> list[dict]:
        return [{"name": "input", "writable": True, "options": OPTIONS, **more},
                {"name": "network", "options": [{"key": "port"}]}]

    def test_the_table_is_one_row_across_the_page(self) -> None:
        [(where, _draw)] = input_page.rows(self._block(), {}, AsyncMock())

        self.assertIs(where, panel.FULL)

    def test_a_device_where_it_cannot_be_written_says_so_above_the_table(self) -> None:
        shown = input_page.rows(self._block(writable=False), {}, AsyncMock())

        self.assertEqual(len(shown), 2)
        self.assertIs(shown[1][0], panel.FULL)

    def test_no_input_settings_draws_nothing(self) -> None:
        self.assertEqual(input_page.rows([{"name": "network", "options": []}], {},
                                         AsyncMock()), [])

    def test_stored_values_are_what_the_rows_are_mapped_to(self) -> None:
        [(_, draw)] = input_page.rows(
            self._block(), {"input": {"menu": ["key:KeyN"]}}, AsyncMock())

        with patch.object(ui, "run_javascript"), ui.column() as body:
            draw()

        names = [one.text for one in body.descendants() if isinstance(one, ui.label)]
        self.assertIn("N", names)
        self.assertNotIn("M", names)

    def test_controller_chips_wait_for_a_controller_where_the_caller_asks(self) -> None:
        options = [_option("previous", "Previous", setting_groups.NAVIGATION,
                           "key:ShiftLeft,pad:standard/button:14")]
        block = [{"name": "input", "writable": True, "options": options}]
        for waits, expected in ((True, ["Left Shift"]), (False, ["Left Shift", "D-pad Left"])):
            with self.subTest(waits=waits):
                [(_, draw)] = input_page.rows(block, {}, AsyncMock(), pads_when_heard=waits)

                with patch.object(ui, "run_javascript"), ui.column() as body:
                    draw()

                names = [one.text for one in body.descendants() if isinstance(one, ui.label)]
                self.assertEqual(expected, [one for one in names
                                            if one in ("Left Shift", "D-pad Left")])

    def test_a_setting_never_stored_is_mapped_to_its_default(self) -> None:
        [(_, draw)] = input_page.rows(self._block(), {}, AsyncMock())

        with patch.object(ui, "run_javascript"), ui.column() as body:
            draw()

        names = [one.text for one in body.descendants() if isinstance(one, ui.label)]
        self.assertIn("M", names)


class IntroTests(unittest.TestCase):
    """How Mapping Works is open until an action is mapped otherwise than the default."""

    def _opened(self, values: dict, **more: Any) -> Any:
        [(_, draw)] = input_page.rows([{"name": "input", "writable": True, "options": OPTIONS}],
                                      values, AsyncMock(), **more)
        with patch.object(input_page, "Table") as built:
            draw()
        return built.call_args.kwargs["how_open"]

    def test_it_is_open_while_every_action_is_as_vpinfe_maps_it(self) -> None:
        self.assertTrue(self._opened({}))

    def test_it_is_open_where_a_stored_value_is_the_default_spelt_another_way(self) -> None:
        self.assertTrue(self._opened({"input": {"menu": ["key:KeyM"]}}))

    def test_it_folds_to_its_title_once_an_action_is_mapped_otherwise(self) -> None:
        self.assertFalse(self._opened({"input": {"menu": ["key:KeyN"]}}))

    def test_an_action_with_nothing_by_default_counts_once_it_is_mapped(self) -> None:
        self.assertFalse(self._opened({"input": {"take_picture": ["key:KeyP"]}}))

    def test_it_stays_open_where_the_caller_asks(self) -> None:
        self.assertTrue(self._opened({"input": {"menu": ["key:KeyN"]}}, keep_intro_open=True))


class WrittenTests(unittest.IsolatedAsyncioTestCase):
    async def test_the_rows_that_change_are_written_to_the_input_section(self) -> None:
        write = AsyncMock(return_value=True)
        [(_, draw)] = input_page.rows([{"name": "input", "writable": True, "options": OPTIONS}],
                                      {}, write)

        with patch.object(input_page, "Table") as built:
            draw()
        answered = await built.call_args.kwargs["write"]({"menu": ["key:KeyN"]})

        write.assert_awaited_once_with({"input": {"menu": ["key:KeyN"]}})
        self.assertTrue(answered)

    async def test_what_the_write_answers_is_what_the_table_follows(self) -> None:
        [(_, draw)] = input_page.rows([{"name": "input", "writable": True, "options": OPTIONS}],
                                      {}, AsyncMock(return_value=False))

        with patch.object(input_page, "Table") as built:
            draw()

        self.assertFalse(await built.call_args.kwargs["write"]({"menu": []}))


class WiredIntoSettingsTests(unittest.TestCase):
    def test_the_page_has_no_note_under_its_heading_for_the_table_explains_itself(self) -> None:
        self.assertNotIn(input_page.KEY, settings.PAGE_NOTES)

    def test_the_binding_editor_is_no_longer_one_the_manager_routes_a_setting_to(self) -> None:
        self.assertNotIn(config_schema.EDITOR_BINDING, settings.EDITORS)

    def test_the_input_detector_is_gone_from_the_page_s_foot(self) -> None:
        self.assertNotIn("input", settings.FOOTERS)

    def test_a_finding_is_drawn_for_the_row_it_names(self) -> None:
        schema = [{"name": "input", "options": [
            {"key": "back"}, {"key": "take_picture", "findings": [("It cannot", "Do this")]}]}]

        drawn = settings._input_findings(schema)

        self.assertEqual(list(drawn), ["back", "take_picture"])
        self.assertEqual(drawn["back"], ())
        self.assertEqual(len(drawn["take_picture"]), 1)

    def test_a_finding_on_another_section_is_not_the_input_page_s(self) -> None:
        schema = [{"name": "tools", "options": [{"key": "take_picture",
                                                  "findings": [("x", "y")]}]}]

        self.assertEqual(settings._input_findings(schema), {})


class DrawnOnTheInputPageTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.ini = root / "vpinfe.ini"
        self.enterContext(patch.object(paths, "VPINFE_INI_PATH", self.ini))
        store = ConfigStore(self.ini)
        store.set_value("input", "menu", "key:KeyN")
        store.save()
        client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)
        self.library = Library(_Served(client))  # type: ignore[arg-type]
        self.enterContext(patch.object(
            settings.run, "io_bound",
            new=AsyncMock(side_effect=lambda call, *args, **kwargs: call(*args, **kwargs))))
        self.enterContext(patch.object(settings, "page_head"))
        self.enterContext(patch.object(panel, "region", new=Immediate))
        self.facts = self.enterContext(patch.object(panel, "facts"))
        self.drawn_by_the_device_page: list[Any] = []
        self.enterContext(patch.object(settings, "build_device_page", new=AsyncMock(
            side_effect=self._device_page)))

    async def _device_page(self, *args: Any, **kwargs: Any) -> None:
        self.drawn_by_the_device_page.append((args, kwargs))

    async def _drawn(self) -> tuple[tuple[Any, ...], dict[str, Any]]:
        await settings._draw_system_page(self.library, Mock(), MagicMock(), INPUT_PAGE, {}, [])
        return self.drawn_by_the_device_page[-1]

    async def test_the_input_section_is_not_drawn_as_a_list_of_settings(self) -> None:
        args, _ = await self._drawn()

        sections = args[4]
        self.assertNotIn("input", sections)

    async def test_the_table_is_placed_as_a_block_at_the_foot(self) -> None:
        _, kwargs = await self._drawn()

        [(above, entries)] = kwargs["blocks"]
        self.assertEqual(above, "")
        self.assertIs(entries[-1][0], panel.FULL)

    async def test_the_page_still_asks_what_keys_can_do_during_play(self) -> None:
        with patch.object(settings, "_play_input", new=AsyncMock(return_value={})) as asked:
            await self._drawn()

        assert asked.await_args is not None
        self.assertIn("input", asked.await_args.args[1])

    async def test_the_values_the_table_is_over_are_the_stored_ones(self) -> None:
        args, _ = await self._drawn()

        self.assertEqual(args[3]["input"]["menu"], ["key:KeyN"])

    async def test_another_page_is_still_drawn_by_the_device_page(self) -> None:
        other = next(page for _group, pages in settings.DEVICE_INDEX for page in pages
                     if page[0] == "vpinfe.logger")

        await settings._draw_system_page(self.library, Mock(), MagicMock(), other, {}, [])

        _, kwargs = self.drawn_by_the_device_page[-1]
        self.assertEqual(kwargs["blocks"], [])

    async def test_the_words_of_the_intro_are_the_catalogs(self) -> None:
        for key in ("how_title", "how_body", "how_relay", "how_step_1", "how_step_2",
                    "how_step_3", "where_here", "where_browser", "where_device"):
            with self.subTest(key=key):
                self.assertIn(f"manager.binding_table.{key}", catalog())


class _Served:
    """The Manager's client, answered by this install's API in-process."""

    def __init__(self, client: TestClient) -> None:
        self._client = client

    @staticmethod
    def _answered(response: Any) -> Any:
        if response.is_error:
            raise RuntimeError(response.text)
        return response.json()

    def config_schema(self) -> list[dict]:
        return list(self._answered(self._client.get("/config/schema"))["sections"])

    def config_values(self) -> dict:
        return dict(self._answered(self._client.get("/config/settings")))

    def config_path_checks(self) -> list[dict]:
        return list(self._answered(self._client.get("/config/paths"))["checks"])

    def put_config(self, changes: dict) -> dict:
        sent = self._client.put("/config", json={"settings": changes})
        return dict(self._answered(sent)["settings"])


if __name__ == "__main__":
    unittest.main()
