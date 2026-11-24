"""A question is a yes or a no, and may offer a second way to say yes."""

from __future__ import annotations

import asyncio
import unittest
from typing import Any
from unittest.mock import patch

from nicegui import ui

from manager.kit import confirm
from tests.support.clicks import said

# Made at import, outside any task, where NiceGUI still hands out the script's own page.
_PAGE = ui.element()


def _dialogs() -> list[ui.dialog]:
    return [one for one in _PAGE.client.layout.descendants()
            if isinstance(one, ui.dialog) and not one.is_deleted]


async def _answered(button: str, **kwargs: Any) -> tuple[Any, list[str], list[str]]:
    """The answer once `button` is pressed, the buttons offered, and the labels of the
    buttons Enter was wired to.

    The page is shared with every other module in the process, so only the dialog this
    question opened is looked in.
    """
    wired: list[str] = []
    others = {one.id for one in _dialogs()}

    async def run() -> Any:
        with _PAGE:
            return await confirm.ask("Move “Esc” to “Select”?", **kwargs)

    with patch.object(confirm.frame, "enter_presses",
                      lambda one: wired.append(one.props.get("label", ""))):
        waiting = asyncio.create_task(run())
        await asyncio.sleep(0)
    [box] = [one for one in _dialogs() if one.id not in others]
    offered = [one for one in box.descendants()
               if isinstance(one, ui.button) and not one.is_deleted]
    names = [one.props.get("label", "") for one in offered]
    said(offered[names.index(button)], "click")
    answer = await waiting
    box.delete()
    return answer, names, wired


async def _asked(button: str, **kwargs: Any) -> tuple[Any, list[str]]:
    """The answer to a question once `button` is pressed, and the buttons it offered."""
    answer, names, _ = await _answered(button, **kwargs)
    return answer, names


class AQuestionWithOneWayToSayYes(unittest.IsolatedAsyncioTestCase):
    async def test_the_confirm_is_true(self) -> None:
        answer, _ = await _asked("Delete")

        self.assertIs(answer, True)

    async def test_cancel_is_false(self) -> None:
        answer, _ = await _asked("Cancel")

        self.assertIs(answer, False)


class AQuestionWithTwo(unittest.IsolatedAsyncioTestCase):
    SECOND = {"second": ("Use for both", "swap_horiz"), "confirm": "Move",
              "icon": "arrow_forward", "danger": False}

    async def test_the_confirm_is_the_confirm_answer(self) -> None:
        answer, _ = await _asked("Move", **self.SECOND)

        self.assertIs(answer, confirm.Answer.CONFIRM)

    async def test_the_second_is_the_second_answer(self) -> None:
        answer, _ = await _asked("Use for both", **self.SECOND)

        self.assertIs(answer, confirm.Answer.SECOND)

    async def test_cancel_is_the_cancel_answer(self) -> None:
        answer, _ = await _asked("Cancel", **self.SECOND)

        self.assertIs(answer, confirm.Answer.CANCEL)

    async def test_the_buttons_run_cancel_then_the_second_then_the_confirm(self) -> None:
        _, names = await _asked("Move", **self.SECOND)

        self.assertEqual(names[-3:], ["Cancel", "Use for both", "Move"])


class EnterAnswersAQuestionThatIsNotDangerous(unittest.IsolatedAsyncioTestCase):
    async def test_enter_presses_the_confirm(self) -> None:
        _, _, wired = await _answered("Move", confirm="Move", icon="arrow_forward",
                                      danger=False)

        self.assertEqual(wired, ["Move"])

    async def test_enter_is_wired_to_the_confirm_beside_a_second(self) -> None:
        _, _, wired = await _answered("Use for both", **AQuestionWithTwo.SECOND)

        self.assertEqual(wired, ["Move"])

    async def test_a_dangerous_verb_is_not_one_enter_reaches(self) -> None:
        _, _, wired = await _answered("Delete")

        self.assertEqual(wired, [])


if __name__ == "__main__":
    unittest.main()
