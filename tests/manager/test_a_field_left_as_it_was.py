"""A one-line field writes when it is left with new text, and not when it is left as it
was - Browse blurs its field on the way to the picker."""

from __future__ import annotations

import asyncio
import unittest
from collections.abc import Awaitable, Callable

from nicegui import core, ui

from manager.kit import panel, verbs
from tests.support.clicks import said

# Made at import, outside any task, where NiceGUI still hands out the script's own page.
_PAGE = ui.element()
PICKED = "/Volumes/tables/Picked.vpx"


def _drawn(value: str, saved: list[str], **extra: object) -> ui.element:
    with _PAGE:
        holder = ui.element()
        with holder:
            panel.field(value, saved.append, **extra)()  # type: ignore[arg-type]
    return holder


def _input(holder: ui.element) -> ui.input:
    return next(one for one in holder.descendants() if isinstance(one, ui.input))


async def _settled() -> None:
    for _ in range(5):
        await asyncio.sleep(0)


def _ran(run: Callable[[], Awaitable[None]]) -> None:
    """`run`, with NiceGUI's loop set so a handler's task is started at all."""
    async def inside() -> None:
        was = core.loop
        core.loop = asyncio.get_running_loop()
        try:
            await run()
        finally:
            core.loop = was

    asyncio.run(inside())


class AFieldLeftAsItWas(unittest.TestCase):
    def test_leaving_it_unchanged_writes_nothing(self) -> None:
        saved: list[str] = []

        async def run() -> None:
            said(_input(_drawn("/Volumes/tables", saved)), "blur")
            await _settled()

        _ran(run)
        self.assertEqual([], saved)

    def test_leaving_it_changed_writes_once(self) -> None:
        saved: list[str] = []

        async def run() -> None:
            control = _input(_drawn("/Volumes/tables", saved))
            said(control, "update:value", "/Volumes/media")
            said(control, "blur")
            await _settled()
            said(control, "blur")
            await _settled()

        _ran(run)
        self.assertEqual(["/Volumes/media"], saved)

    def test_a_wrapping_field_grows_with_its_text_and_still_writes_on_leaving(self) -> None:
        saved: list[str] = []

        async def run() -> None:
            control = _input(_drawn("a long command", saved, wrap=True))
            self.assertIsInstance(control, ui.textarea)
            self.assertTrue(control.props.get("autogrow"))
            said(control, "update:value", "a longer command")
            said(control, "blur")
            await _settled()

        _ran(run)
        self.assertEqual(["a longer command"], saved)

    def test_a_browsed_path_is_written_after_the_field_is_redrawn_under_it(self) -> None:
        saved: list[str] = []
        drawn: dict[str, ui.element] = {}

        async def browse(_current: str) -> str:
            drawn["holder"].delete()
            return PICKED

        async def run() -> None:
            drawn["holder"] = _drawn("", saved, browse=browse)
            button = next(one for one in drawn["holder"].descendants()
                          if isinstance(one, ui.button) and one.props.get("icon") == verbs.BROWSE)
            said(button, "click")
            await _settled()

        _ran(run)
        self.assertEqual([PICKED], saved)


if __name__ == "__main__":
    unittest.main()
