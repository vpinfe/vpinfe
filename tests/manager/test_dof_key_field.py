"""The DOF Config Tool API key is drawn as a secret: the API answers whether one is held and
never the key, so the box starts empty and says Set or Not set instead of printing True."""

from __future__ import annotations

import asyncio
import unittest
from typing import Any
from unittest import mock

from common.i18n import t
from common.settings import config_service
from manager.extensions import ext_page
from manager.kit import panel
from manager.settings import settings

KEY = "config_tool_api_key"


def _options() -> list[dict]:
    return next(block for block in config_service.schema()["sections"]
                if block["name"] == "dof")["options"]


class TheDofKeyFieldTests(unittest.TestCase):
    def _drawn(self, held: Any, *, saved: bool = True, asked: bool = True,
               writable: bool = True) -> dict[str, Any]:
        seen: dict[str, Any] = {"actions": [], "writes": []}

        def field(value, on_save, **kwargs):
            seen.update(value=value, on_save=on_save, **kwargs)
            return lambda: None

        def action(label, on_click, **kwargs):
            seen["actions"].append((label, on_click))
            seen["enabled"] = kwargs.get("enabled", True)
            return lambda: None

        async def ask(*args, **kwargs):
            seen["asked"] = kwargs
            return asked

        async def write(value):
            seen["writes"].append(value)
            return saved

        for target, name, replacement in (
                (panel, "field", field), (panel, "action", action),
                (ext_page, "on_page", lambda handler: handler),
                (ext_page.confirm, "ask", ask), (ext_page.ui, "row", mock.MagicMock())):
            patcher = mock.patch.object(target, name, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)
        saver = mock.patch.object(settings, "saver", return_value=write)
        saver.start()
        self.addCleanup(saver.stop)

        rows = settings.section_rows(mock.Mock(), "dof", _options(), {"dof": {KEY: held}},
                                     writable, mock.Mock())
        label = t(f"config.dof.{KEY}.label")
        next(draw for text, draw in rows if text == label)()
        return seen

    def test_a_key_that_is_held_is_not_drawn_as_true(self) -> None:
        seen = self._drawn(True)

        self.assertEqual((seen["value"], seen["placeholder"]),
                         ("", t("manager.ext_page.secret_set")))
        self.assertTrue(seen["secret"])

    def test_a_key_that_is_not_held_says_so_and_has_no_clear(self) -> None:
        seen = self._drawn(False)

        self.assertEqual(seen["placeholder"], t("manager.ext_page.secret_not_set"))
        self.assertEqual(seen["actions"], [])

    def test_a_typed_key_is_saved_as_typed(self) -> None:
        seen = self._drawn(False)

        said = asyncio.run(seen["on_save"]("abc-key"))

        self.assertEqual((seen["writes"], said), (["abc-key"], ""))

    def test_a_pasted_key_is_saved_without_the_space_or_newline_around_it(self) -> None:
        seen = self._drawn(False)

        said = asyncio.run(seen["on_save"]("  abc-key \n"))

        self.assertEqual((seen["writes"], said), (["abc-key"], ""))

    def test_a_blank_box_saves_nothing(self) -> None:
        seen = self._drawn(True)

        said = asyncio.run(seen["on_save"]("  \n"))

        self.assertEqual((seen["writes"], said), ([], ""))

    def test_a_section_that_cannot_be_written_disables_the_box_and_clear(self) -> None:
        seen = self._drawn(True, writable=False)

        self.assertTrue(seen["disabled"])
        self.assertFalse(seen["enabled"])

    def test_a_writable_section_leaves_both_enabled(self) -> None:
        seen = self._drawn(True)

        self.assertFalse(seen["disabled"])
        self.assertTrue(seen["enabled"])

    def test_a_key_that_could_not_be_saved_is_refused_in_words(self) -> None:
        seen = self._drawn(False, saved=False)

        said = asyncio.run(seen["on_save"]("abc-key"))

        self.assertEqual(said, t("manager.settings.save_failed"))

    def test_clear_asks_first_and_then_empties_the_key(self) -> None:
        seen = self._drawn(True)
        (label, clear), = seen["actions"]

        asyncio.run(clear())

        self.assertEqual((label, seen["writes"]), (t("manager.ext_page.clear"), [""]))
        self.assertEqual(seen["asked"]["detail"], t("manager.settings.secret_clear_detail"))

    def test_clear_declined_keeps_the_key(self) -> None:
        seen = self._drawn(True, asked=False)
        (_label, clear), = seen["actions"]

        asyncio.run(clear())

        self.assertEqual(seen["writes"], [])


if __name__ == "__main__":
    unittest.main()
