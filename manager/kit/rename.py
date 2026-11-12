"""Asking for a new name, for one row or for each of several in a walk."""

from __future__ import annotations

from typing import Any

from nicegui import ui

from common.i18n import t
from manager.kit import dialog as frame
from manager.kit import panel, verbs, walks


async def ask(current: str, *, title: str, label: str, hint: str = "", place: str = "",
              walking: bool = False) -> str | None:
    """The name wanted, `walks.SKIPPED` for no change, or `walks.STOPPED` to end a walk.

    `place` says where a walk has got to, and is empty outside one. `walking` splits Cancel
    into Skip and Stop, which are different intents in a run.
    """
    held: dict[str, Any] = {}

    def answer() -> str | None:
        said = " ".join(str(held["field"].value or "").split())
        return said if said and said != current else walks.SKIPPED

    with frame.opened(title, ignore_outside_click=True) as box:
        if place:
            ui.label(place).classes("manager-help")
        if hint:
            ui.label(hint).classes("manager-help")
        panel.facts(ui, [(label, lambda: held.update(field=frame.field(current)))])
        with frame.footer():
            frame.leave(box, walking)
            go = frame.answer(t("word.rename"), lambda: box.submit(answer()),
                              icon=verbs.RENAME)
    frame.focus(box, held["field"], select=True)
    frame.enter_presses(go)
    return await box
