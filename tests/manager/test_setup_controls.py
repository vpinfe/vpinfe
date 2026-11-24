"""Setup's Controls step: the Settings > Input table, less what is only heard during play."""

from __future__ import annotations

import asyncio
import unittest
from typing import Any
from unittest import mock

from common.config import setting_groups
from common.settings import config_service
from manager.setup import setup, setup_controls


def _ran(coro: Any) -> Any:
    return asyncio.run(coro)


def _library(schema: list[dict], values: dict) -> mock.Mock:
    library = mock.Mock()
    library.config_schema.return_value = schema
    library.config_values.return_value = values
    return library


def _input_options(schema: list[dict]) -> list[dict]:
    return [one for block in schema if block["name"] == "input" for one in block["options"]]


class AskedHereTests(unittest.TestCase):
    def test_what_is_heard_during_play_is_left_out(self) -> None:
        schema = config_service.schema()["sections"]
        during_play = {one["key"] for one in _input_options(schema)
                       if one.get("group") == setting_groups.DURING_PLAY}
        every = {one["key"] for one in _input_options(schema)}

        left = {one["key"] for one in _input_options(setup_controls._asked_here(schema))}

        self.assertTrue(during_play)
        self.assertEqual(every - during_play, left)

    def test_another_section_is_left_as_it_was(self) -> None:
        other = {"name": "network",
                 "options": [{"key": "port", "group": setting_groups.DURING_PLAY}]}

        self.assertEqual([other], setup_controls._asked_here([other]))


class AskTests(unittest.TestCase):
    def test_the_step_draws_the_input_table_with_controllers_waiting_to_be_heard(self) -> None:
        schema = config_service.schema()["sections"]
        with mock.patch.object(setup_controls, "Library", return_value=_library(schema, {})), \
             mock.patch.object(setup_controls.input_page, "rows",
                               return_value=[("row", mock.Mock())]) as rows:
            asked = _ran(setup_controls.ask())

        self.assertTrue(rows.call_args.kwargs["pads_when_heard"])
        self.assertTrue(rows.call_args.kwargs["keep_intro_open"])
        self.assertIs(setup_controls.write, rows.call_args.args[2])
        self.assertTrue(callable(asked["draw"]))
        self.assertNotIn("help", asked)

    def test_the_rows_it_asks_for_have_no_during_play_setting(self) -> None:
        schema = config_service.schema()["sections"]
        with mock.patch.object(setup_controls, "Library", return_value=_library(schema, {})), \
             mock.patch.object(setup_controls.input_page, "rows",
                               return_value=[("row", mock.Mock())]) as rows:
            _ran(setup_controls.ask())

        kept = {one.get("group") for one in _input_options(rows.call_args.args[0])}
        self.assertNotIn(setting_groups.DURING_PLAY, kept)

    def test_settings_that_cannot_be_read_are_said_not_drawn(self) -> None:
        library = mock.Mock()
        library.config_schema.side_effect = OSError("down")
        with mock.patch.object(setup_controls, "Library", return_value=library):
            asked = _ran(setup_controls.ask())

        self.assertNotIn("draw", asked)
        self.assertTrue(asked["notes"])


class StepTests(unittest.TestCase):
    def test_the_step_is_in_the_row_between_the_screens_and_visual_pinball(self) -> None:
        keys = [one.key for one in setup.STEPS]

        self.assertLess(keys.index(setup.SCREENS), keys.index(setup.CONTROLS))
        self.assertLess(keys.index(setup.CONTROLS), keys.index(setup.VPX))

    def test_answering_it_writes_nothing_because_each_press_was_saved(self) -> None:
        self.assertIsNone(_ran(setup_controls.answer({})))


if __name__ == "__main__":
    unittest.main()
