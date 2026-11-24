"""The dialog where a pairing token is pasted ignores a click outside it, and Escape still
cancels it."""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import Mock, patch

from nicegui import ui

from manager.devices import pairing
from manager.kit import dialog as frame
from tests.support.clicks import press, said

# Made at import, outside any task, where NiceGUI still hands out the script's own page.
_PAGE = ui.element()


class PastingAToken(unittest.TestCase):
    def test_a_click_outside_does_nothing_and_escape_cancels(self) -> None:
        async def run() -> tuple[str, bool]:
            known = {one.id for one in _PAGE.client.elements.values()}
            waiting = asyncio.create_task(press(pairing.paste, Mock(), "Cab"))
            await asyncio.sleep(0)
            box = next(one for one in _PAGE.client.elements.values()
                       if isinstance(one, frame.Dialog) and one.id not in known)
            props = str(box.props)
            said(box, "escapeKey")
            said(box, "update:modelValue", False)
            return props, await waiting

        with patch.object(ui, "run_javascript"):
            props, paired = asyncio.run(run())

        self.assertIn("no-backdrop-dismiss", props)
        self.assertNotIn("persistent", props)
        self.assertFalse(paired)


if __name__ == "__main__":
    unittest.main()
