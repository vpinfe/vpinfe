"""Protects: (a) a press on the Edit row's Rotate must not touch the file; only Save does.

A person turns a picture as many times as they like to see how it will look. The file is
written once, by Save, turned by whatever the presses add up to.
"""

from __future__ import annotations

import asyncio
import unittest
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from common.games import media_rotate
from common.i18n import t
from manager.library import edit_tools

RIGHT = t("manager.media_rotate.right")
SAVE = t("word.save")


@contextmanager
def _drawn() -> Iterator[tuple[dict[str, Any], AsyncMock]]:
    """The row drawn against stand-ins, with the handler each button was given by its label
    and the rotation job's `run`, which stays patched until the block ends."""
    handlers: dict[str, Any] = {}
    run = AsyncMock()

    def button(label: str, on_click: Any, **_kw: Any) -> MagicMock:
        handlers[label] = on_click
        return MagicMock()

    with patch.object(edit_tools, "ui"), \
            patch.object(edit_tools.panel, "icon_action", button), \
            patch.object(edit_tools.panel, "draft_button", button), \
            patch.object(edit_tools.rotate_job, "run", run):
        edit_tools.edit_row(edit_tools.tools_for("image"), element_id="art", game_id="g1",
                            path="medias/bg.png", state={}, then=AsyncMock())
        yield handlers, run


class RotatingInTheEditRow(unittest.TestCase):
    def test_presses_write_nothing(self) -> None:
        with _drawn() as (handlers, run):
            handlers[RIGHT]()
            handlers[RIGHT]()

        run.assert_not_called()

    def test_save_writes_once_for_everything_the_presses_added_up_to(self) -> None:
        with _drawn() as (handlers, run):
            handlers[RIGHT]()
            handlers[RIGHT]()
            asyncio.run(handlers[SAVE]())

            run.assert_awaited_once()
            assert run.await_args is not None
            start = run.await_args.args[0]
            with patch.object(edit_tools, "ApiClient") as api:
                start()

        api.return_value.rotate_media.assert_called_once_with(
            [("g1", "medias/bg.png")], media_rotate.AROUND, True)


if __name__ == "__main__":
    unittest.main()
