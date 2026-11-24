"""What the Enabled switch does before it switches a launcher off."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, Mock, patch

from nicegui import ui

from common.i18n import t
from manager.kit import confirm, offload
from manager.workbench import all_settings
from tests.support.clicks import press

LAUNCHER = {"launcher_id": "wide", "display_name": "VPX (4K)", "app_name": "Visual Pinball X"}


def _fallback(tables: int = 0, fallbacks: tuple = (), refused: str = "") -> dict:
    return {"tables": tables, "refused": refused,
            "fallbacks": [{"launcher_id": key, "display_name": name, "tables": count,
                           "has_program": True} for key, name, count in fallbacks]}


class SwitchingOff(unittest.IsolatedAsyncioTestCase):
    async def _switch_off(self, found: dict, answer: bool = True):
        ask = AsyncMock(return_value=answer)
        notify = Mock()
        with patch.object(offload, "io", new=AsyncMock(return_value=found)), \
                patch.object(confirm, "ask", new=ask), \
                patch.object(ui, "notify", new=notify):
            agreed = await press(all_settings._agreed_to_switch_off, Mock(), LAUNCHER)
        return agreed, ask, notify

    async def test_one_no_table_uses_goes_off_without_asking(self) -> None:
        agreed, ask, notify = await self._switch_off(_fallback())

        self.assertTrue(agreed)
        ask.assert_not_awaited()
        notify.assert_not_called()

    async def test_a_refusal_is_shown_and_nothing_is_asked(self) -> None:
        refused = t("error.launchers.fallback_unrunnable", name="Visual Pinball X")
        agreed, ask, notify = await self._switch_off(
            _fallback(4, (("plain", "Visual Pinball X", 4),), refused))

        self.assertFalse(agreed)
        ask.assert_not_awaited()
        self.assertEqual(notify.call_args.args[0], refused)

    async def test_the_confirm_counts_the_tables_and_names_where_they_go(self) -> None:
        agreed, ask, _notify = await self._switch_off(
            _fallback(4, (("plain", "Visual Pinball X", 4),)))

        self.assertTrue(agreed)
        self.assertEqual(ask.call_args.args[0],
                         t("manager.workbench.off_question", name="VPX (4K)"))
        self.assertEqual(ask.call_args.kwargs["detail"],
                         t("manager.workbench.off_detail", count=4,
                           fallback="Visual Pinball X"))
        self.assertFalse(ask.call_args.kwargs["danger"])

    async def test_cancelling_leaves_it_on(self) -> None:
        agreed, _ask, _notify = await self._switch_off(
            _fallback(1, (("plain", "Visual Pinball X", 1),)), answer=False)

        self.assertFalse(agreed)

    async def test_tables_going_to_more_than_one_launcher_get_a_line_each(self) -> None:
        _agreed, ask, _notify = await self._switch_off(
            _fallback(4, (("plain", "Visual Pinball X", 3), ("fp", "Future Pinball", 1))))

        self.assertEqual(list(ask.call_args.kwargs["lines"]), [
            t("manager.workbench.launch_count", count=3, name="Visual Pinball X"),
            t("manager.workbench.launch_count", count=1, name="Future Pinball"),
        ])


if __name__ == "__main__":
    unittest.main()
