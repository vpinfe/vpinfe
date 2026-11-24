"""Settings > Output, Test Event: Test becomes Stop, and is dimmed with the reason under it."""

from __future__ import annotations

import unittest
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

from common.i18n import t
from manager.kit import offload
from manager.settings import dof
from manager.settings.settings import address_for


class Library:
    """A Library that turns events on and off and records what it was asked."""

    def __init__(self, state: dict[str, Any], refusal: Exception | None = None) -> None:
        self.state = state
        self.refusal = refusal
        self.fired: list[str] = []
        self.stopped = 0
        self.no_boards = False

    def dof_event_state(self) -> dict[str, Any]:
        return self.state

    def test_dof_event(self, event: str) -> dict[str, Any]:
        if self.refusal:
            raise self.refusal
        self.fired.append(event)
        self.state = {"event": event, "blocked": "", "no_boards": self.no_boards}
        return {"event": event, "no_boards": self.no_boards}

    def stop_dof_event(self) -> None:
        self.stopped += 1
        self.state = {"event": None, "blocked": ""}


class Buttons:
    """Stands in for `panel.action`, keeping what each button was drawn with."""

    def __init__(self) -> None:
        self.drawn: list[dict[str, Any]] = []

    def action(self, label: str, on_click: Any, **how: Any) -> Any:
        self.drawn.append({"label": label, "press": on_click, **how})
        return lambda: None


class TesterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.buttons = Buttons()
        self.enterContext(patch.object(
            offload, "io", new=AsyncMock(side_effect=lambda call, *args: call(*args))))
        self.enterContext(patch("manager.kit.on_page.ui"))
        self.enterContext(patch.object(
            dof.run, "io_bound", new=AsyncMock(side_effect=lambda call, *args: call(*args))))
        self.ui = self.enterContext(patch.object(dof, "ui"))
        self.column = self.ui.column.return_value.classes.return_value
        self.column.is_deleted = False
        self.enterContext(patch.object(dof.panel, "action", self.buttons.action))
        self.said: list[str] = []
        self.ui.label.side_effect = (
            lambda text: self.said.append(text) or MagicMock())  # type: ignore[func-returns-value]

    async def _draw(self, library: Library, event: str = "E900") -> dof.Tester:
        state = await dof.event_state(library)
        self.field = {"event": event}
        tester = dof.Tester(library, state, lambda: self.field["event"])
        tester()
        return tester

    def _last(self) -> dict[str, Any]:
        return self.buttons.drawn[-1]

    async def test_an_open_device_offers_test(self) -> None:
        await self._draw(Library({"event": None, "blocked": ""}))

        self.assertEqual(self._last()["label"], t("manager.dof.test"))
        self.assertTrue(self._last()["enabled"])
        self.assertEqual(self.said, [])

    async def test_a_blocked_device_dims_test_and_says_why_under_it(self) -> None:
        for reason in (t("dof.event.off"), t("dof.event.playing")):
            self.buttons.drawn.clear()
            self.said.clear()
            with self.subTest(reason=reason):
                await self._draw(Library({"event": None, "blocked": reason}))

                self.assertFalse(self._last()["enabled"])
                self.assertEqual(self.said, [reason])

    async def test_an_empty_field_dims_test_without_a_reason_line(self) -> None:
        await self._draw(Library({"event": None, "blocked": ""}), event=" ")

        self.assertFalse(self._last()["enabled"])
        self.assertEqual(self.said, [])

    async def test_pressing_test_fires_the_event_and_the_button_reads_stop(self) -> None:
        library = Library({"event": None, "blocked": ""})
        await self._draw(library, event="S27")

        await self._last()["press"]()

        self.assertEqual(library.fired, ["S27"])
        self.assertEqual(self._last()["label"], t("word.stop"))

    async def test_an_event_with_no_board_says_so_and_links_to_setup(self) -> None:
        library = Library({"event": None, "blocked": ""})
        library.no_boards = True
        await self._draw(library, event="S27")
        link = MagicMock()
        links: list[dict[str, Any]] = []
        self.enterContext(patch.object(
            dof.panel, "link",
            lambda label, **how: links.append({"label": label, **how}) or link))  # type: ignore[func-returns-value]

        await self._last()["press"]()

        self.assertIn(t("manager.dof.no_boards"), self.said)
        self.assertEqual([one["label"] for one in links], [t("manager.dof.setup")])
        self.assertEqual(links[0]["to"], address_for(dof.SECTION))

    async def test_the_output_page_says_no_boards_without_linking_to_itself(self) -> None:
        library = Library({"event": "E900", "blocked": "", "no_boards": True})
        state = await dof.event_state(library)
        links: list[str] = []
        self.enterContext(patch.object(
            dof.panel, "link",
            lambda label, **_: links.append(label) or (lambda: None)))  # type: ignore[func-returns-value]
        dof.Tester(library, state, lambda: "E900", on_dof_page=True)()

        self.assertEqual(self.said, [t("manager.dof.no_boards")])
        self.assertEqual(links, [])

    async def test_pressing_stop_turns_it_off_and_the_button_reads_test_again(self) -> None:
        library = Library({"event": "E900", "blocked": ""})
        await self._draw(library)
        self.assertEqual(self._last()["label"], t("word.stop"))

        await self._last()["press"]()

        self.assertEqual(library.stopped, 1)
        self.assertEqual(self._last()["label"], t("manager.dof.test"))

    async def test_a_refusal_is_said_and_the_button_dims_with_what_is_true_now(self) -> None:
        library = Library({"event": None, "blocked": ""},
                          refusal=RuntimeError(t("dof.event.playing")))
        await self._draw(library)
        library.state = {"event": None, "blocked": t("dof.event.playing")}

        await self._last()["press"]()

        self.ui.notify.assert_called_once_with(
            t("manager.dof.test_failed"), caption=t("dof.event.playing"), type="negative")
        self.assertFalse(self._last()["enabled"])
        self.assertEqual(self.said, [t("dof.event.playing")])

    async def test_a_state_that_cannot_be_read_leaves_test_open_to_answer_for_itself(
            self) -> None:
        library = Library({})
        cast(Any, library).dof_event_state = MagicMock(side_effect=RuntimeError("down"))

        await self._draw(library)

        self.assertTrue(self._last()["enabled"])


    async def test_an_event_other_than_the_one_in_the_field_leaves_test_to_replace_it(
            self) -> None:
        await self._draw(Library({"event": "E900", "blocked": ""}), event="e901")

        self.assertEqual(self._last()["label"], t("manager.dof.test"))
        self.assertTrue(self._last()["enabled"])

    async def test_the_field_changing_draws_the_button_again(self) -> None:
        tester = await self._draw(Library({"event": None, "blocked": ""}), event="")
        self.assertFalse(self._last()["enabled"])

        self.field["event"] = "E905"
        tester.refresh()

        self.assertTrue(self._last()["enabled"])

    async def test_a_page_that_has_gone_is_not_drawn_on(self) -> None:
        tester = await self._draw(Library({"event": None, "blocked": ""}))
        self.buttons.drawn.clear()
        self.column.is_deleted = True

        tester.refresh()

        self.assertEqual(self.buttons.drawn, [])

    async def test_a_stop_that_did_not_take_says_so(self) -> None:
        library = Library({"event": "E900", "blocked": ""})
        cast(Any, library).stop_dof_event = lambda: None
        await self._draw(library)

        await self._last()["press"]()

        self.assertEqual(self._last()["label"], t("word.stop"))
        self.ui.notify.assert_called_once_with(t("manager.dof.stop_failed"), type="negative")

    async def test_every_button_carries_what_pressing_it_does(self) -> None:
        await self._draw(Library({"event": None, "blocked": ""}))
        self.assertEqual(self._last()["hint"], dof.panel.NO_HINT)

        await self._draw(Library({"event": "E900", "blocked": ""}))
        self.assertEqual(self._last()["hint"], t("manager.dof.stop_hint"))


class ReadBelowTests(unittest.TestCase):
    def test_every_setting_the_foot_reads_is_one_the_page_has(self) -> None:
        from common.config import config_schema

        keys = {one.key for one in config_schema.options() if one.section == dof.SECTION}

        self.assertLessEqual(dof.READ_BELOW, keys)


class EventRowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.enterContext(patch.dict(dof._typed, {"event": "E900"}))
        self.captured: dict[str, Any] = {}

        def field(value: str, on_save: Any, **_: Any) -> Any:
            self.captured.update(value=value, on_save=on_save)
            return lambda: None

        self.enterContext(patch.object(dof.panel, "field", field))

    async def test_the_field_takes_a_letter_and_a_number_and_says_so_on_anything_else(
            self) -> None:
        label, _ = dof.event_row(object(), {})

        self.assertEqual(label, t("manager.dof.test_event"))
        self.assertEqual(self.captured["value"], "E900")
        self.assertEqual(await self.captured["on_save"]("s27"), "")
        self.assertEqual(dof._typed["event"], "S27")
        self.assertEqual(await self.captured["on_save"]("905"), t("dof.event.malformed"))
        self.assertEqual(dof._typed["event"], "")

    async def test_what_was_typed_is_there_when_the_page_is_drawn_again(self) -> None:
        dof.event_row(object(), {})
        await self.captured["on_save"]("S27")

        dof.event_row(object(), {})

        self.assertEqual(self.captured["value"], "S27")


if __name__ == "__main__":
    unittest.main()
