
"""Ask before something cannot be undone.

One dialog: two had already been written to the same shape with different spellings, and
a third would have been the one that taught users the buttons are not in a fixed place.
Awaited rather than callback, so the caller keeps its own flow.
"""

from __future__ import annotations

import enum
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any, overload

from nicegui import ui

from common.i18n import t
from manager.kit import dialog as frame
from manager.kit import verbs


class Answer(enum.StrEnum):
    """How a question with two ways to say yes was answered."""

    CANCEL = "cancel"
    CONFIRM = "confirm"
    SECOND = "second"


@dataclass
class Option:
    """A tick offered with the question, unticked unless `on` says otherwise. The caller
    reads `on` once the question is answered."""

    label: str
    on: bool = False


@overload
async def ask(question: str, *, detail: str | Sequence[str] = ...,
              lines: Iterable[str] = ..., confirm: str = ..., icon: str = ...,
              danger: bool = ...,
              also: tuple[str, Callable[[], Any], str] | None = ...,
              second: None = ..., option: Option | None = ...) -> bool: ...


@overload
async def ask(question: str, *, detail: str | Sequence[str] = ...,
              lines: Iterable[str] = ..., confirm: str = ..., icon: str = ...,
              danger: bool = ...,
              also: tuple[str, Callable[[], Any], str] | None = ...,
              second: tuple[str, str], option: Option | None = ...) -> Answer: ...


async def ask(question: str, *, detail: str | Sequence[str] = "",
              lines: Iterable[str] = (),
              confirm: str = t("word.delete"), icon: str = verbs.DELETE,
              danger: bool = True,
              also: tuple[str, Callable[[], Any], str] | None = None,
              second: tuple[str, str] | None = None,
              option: Option | None = None) -> bool | Answer:
    """Put the question, and wait for an answer.

    `question` is the whole ask - "Delete the extracted script?", never "Are you sure?",
    which asks nothing. `detail` is the consequence, a line to each sentence where
    there are two. `lines` names files where a count would hide which ones. The
    `confirm` button is the verb that does the thing, so it reads without the question,
    and `icon` is that verb's drawing - pass both or neither. `also` is one act to offer
    before answering, as its label, what it does and its drawing; it leaves the question
    open.

    `second` is another way to say yes, as its label and its drawing, quiet beside the
    confirm. Given one, the answer is an `Answer`; without, a bool as it always was.

    `option` is a tick under the lines, whose state is left in the `Option` it came in.

    Enter presses the confirm unless the question is `danger`.
    """
    with frame.opened(question, size="question") as box:
        for said in [detail] if isinstance(detail, str) else detail:
            if said:
                ui.label(said).classes("manager-help")
        for line in lines:
            ui.label(line).classes("manager-confirm-line")
        if option is not None:
            ui.checkbox(option.label).props("dense").bind_value(option, "on")
        with frame.footer():
            if also is not None:
                label, act, drawing = also
                frame.aside(label, act, icon=drawing)
            # Cancel first and quiet: the destructive verb is the one to be aimed at.
            frame.cancel(lambda: box.submit(Answer.CANCEL))
            if second is not None:
                frame.quiet(second[0], lambda: box.submit(Answer.SECOND), icon=second[1])
            yes = frame.answer(confirm, lambda: box.submit(Answer.CONFIRM), icon=icon,
                               danger=danger)
            # A dangerous verb is never one Enter can reach by habit.
            if not danger:
                frame.enter_presses(yes)
    answered = await box
    if second is None:
        return answered == Answer.CONFIRM
    return Answer(answered) if answered else Answer.CANCEL


async def replace(label: str, going: list[str]) -> bool:
    """Name what a write would replace in a slot, and wait for a yes.

    The files are listed rather than counted because the surprising case is the one a
    count hides: a whole family goes at this tier, so a .mp4 arriving over a .png takes
    the .png with it and the user never named that file.
    """
    return await ask(t("manager.mediasource.replace", kind=label),
                     detail=t("manager.mediasource.replace_detail"),
                     lines=going, confirm=t("word.replace"), icon=verbs.REPLACE)
