"""Settings > Output, the DOF foot: Last checked, and Check Now only while there is a key."""

from __future__ import annotations

import unittest
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

from common.i18n import t
from manager.kit import offload
from manager.library import record
from manager.settings import dof, settings


class Library:
    """A Library that answers the two DOF calls and records that the second was made."""

    def __init__(self, state: dict[str, Any] | Exception) -> None:
        self.state = state
        self.pressed = 0

    def dof_config_state(self) -> dict[str, Any]:
        if isinstance(self.state, Exception):
            raise self.state
        return self.state

    def download_dof_config(self) -> dict[str, Any]:
        self.pressed += 1
        return {"id": "job-1"}


class FootTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.refill = MagicMock()
        self.drawn: dict[str, Any] = {}
        self.enterContext(patch.object(
            offload, "io", new=AsyncMock(side_effect=lambda call, *args: call(*args))))
        self.enterContext(patch("manager.kit.on_page.ui"))
        self.ui = self.enterContext(patch.object(dof, "ui"))

        def last_checked(checked: str, now: Any, *, blocked: str = "") -> tuple[str, Any]:
            self.drawn.update(checked=checked, now=now, blocked=blocked)
            return ("Last checked", lambda: None)

        self.enterContext(patch.object(settings, "last_checked", last_checked))

    async def _foot(self, state: dict[str, Any] | Exception) -> None:
        self.library = Library(state)
        self.rows = await dof.foot(self.library, self.refill)

    async def test_with_a_key_check_now_is_open_and_shows_when_it_last_ran(self) -> None:
        await self._foot({"checked": "2026-10-09T10:00:00Z", "has_key": True})

        self.assertEqual(self.drawn["checked"], "2026-10-09T10:00:00Z")
        self.assertEqual(self.drawn["blocked"], "")
        self.assertEqual(len(self.rows), 2)

    async def test_without_a_key_check_now_says_to_enter_one(self) -> None:
        await self._foot({"checked": "", "has_key": False})

        self.assertEqual(self.drawn["blocked"], t("error.dof_config.no_key"))

    async def test_a_state_that_cannot_be_read_says_so_and_offers_no_act(self) -> None:
        await self._foot(RuntimeError("down"))

        self.assertNotIn("now", self.drawn)
        self.assertEqual(len(self.rows), 2)

    async def _press(self, ended: dict[str, Any]) -> None:
        await self._foot({"checked": "", "has_key": True})
        with patch.object(record, "ended_job", new=AsyncMock(return_value=ended)):
            await self.drawn["now"]()

    async def test_a_press_that_changed_a_file_says_the_config_updated(self) -> None:
        await self._press({"changed": True})

        self.assertEqual(self.library.pressed, 1)
        self.ui.notify.assert_called_once_with(t("said.dof_config.updated"), type="positive")
        self.refill.assert_called_once()

    async def test_a_press_that_changed_nothing_says_it_is_up_to_date(self) -> None:
        await self._press({"changed": False})

        self.ui.notify.assert_called_once_with(t("manager.settings.already_date"), type="info")

    async def test_a_failed_download_leads_with_words_and_carries_the_reason(self) -> None:
        await self._press({"error": "The DOF Config Tool did not accept your API key"})

        self.ui.notify.assert_called_once_with(
            t("manager.settings.check_failed"),
            caption="The DOF Config Tool did not accept your API key", type="negative")

    async def test_a_refusal_before_any_job_exists_is_said_on_the_page(self) -> None:
        await self._foot({"checked": "", "has_key": True})
        cast(Any, self.library).download_dof_config = MagicMock(side_effect=RuntimeError("no key"))

        await self.drawn["now"]()

        self.assertEqual(self.ui.notify.call_args.args[0], t("manager.settings.check_failed"))
        self.assertEqual(self.ui.notify.call_args.kwargs["type"], "negative")


class LastCheckedTests(unittest.TestCase):
    def _action(self, blocked: str) -> dict[str, Any]:
        with patch.object(settings, "ui"), patch.object(settings, "panel") as panel:
            _, draw = settings.last_checked("", lambda: None, blocked=blocked)
            draw()
        self.no_hint = panel.NO_HINT
        return panel.action.call_args.kwargs

    def test_a_reason_dims_the_act_and_is_its_tooltip(self) -> None:
        shown = self._action("Enter your API key")

        self.assertFalse(shown["enabled"])
        self.assertEqual(shown["hint"], "Enter your API key")

    def test_no_reason_leaves_the_act_open(self) -> None:
        shown = self._action("")

        self.assertTrue(shown["enabled"])
        self.assertEqual(shown["hint"], self.no_hint)


class KeySaveTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_saved_key_redraws_the_page_so_check_now_opens(self) -> None:
        refill = MagicMock()
        save = AsyncMock(return_value=True)

        self.assertTrue(await settings._redrawn_after(save, refill)("abc"))

        save.assert_awaited_once_with("abc")
        refill.assert_called_once()

    async def test_a_key_that_was_not_saved_leaves_the_page_alone(self) -> None:
        refill = MagicMock()

        done = await settings._redrawn_after(AsyncMock(return_value=False), refill)("abc")

        self.assertFalse(done)
        refill.assert_not_called()


if __name__ == "__main__":
    unittest.main()
