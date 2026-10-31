
"""What this install and this machine are.

The facts a person is asked for when they report something: version, build, OS, browser,
and where this install keeps its files. Metrics answers what the machine is *doing*;
this answers what it *is*, and the two are on separate pages because one changes every
two seconds and the other does not change at all.

**The page exists for its copy button.** Reading fourteen fields off a screen and typing
them into an issue is where a report goes wrong, so the whole set travels as one block
of text, assembled by the install rather than by whoever is looking at it.

The copy happens in the browser at the moment of the click. A browser only allows one
while it still believes a person just asked, and this Manager is server-rendered - a
click that goes to the server and comes back has spent that permission on the way.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

from nicegui import ui

from common.failures import why
from common.i18n import t
from manager.kit import busy, clipboard, offload, panel, verbs
from manager.kit.data import Library
from manager.system import support as support_dialog
from manager.system import whats_new as whats_new_page

logger = logging.getLogger("vpinfe.manager.system.about")


def build(library: Library, state: dict[str, Any], redraw: Callable[[], None]) -> None:
    """Read once when the page opens. None of it changes while VPinFE runs."""
    held: dict[str, Any] = {"text": ""}

    with ui.column().classes("w-full gap-3") as body:
        pass

    async def load(refresh: bool = False) -> None:
        try:
            found = await offload.io(library.about, refresh)
        except Exception as exc:  # noqa: BLE001 - this page says why, never 500s
            body.clear()
            with body:
                panel.facts(ui, [panel.intro(t("manager.about.read_failed"),
                                             hint=why(exc))])
            return
        held["text"] = str(found.get("text") or "")
        _draw(body, found.get("groups") or [], held, load, library, state)

    busy.fill(body, load)


def _draw(body: Any, groups: list[dict[str, Any]], held: dict[str, Any],
          reload: Callable[..., Any], library: Library, state: dict[str, Any]) -> None:
    body.clear()
    with body:
        with ui.row().classes("items-center gap-2 w-full no-wrap"):
            ui.label(t("manager.about.copy_note")) \
                .classes("manager-help grow min-w-0")
            panel.action(t("word.copy"), lambda event: clipboard.copied(bool(event.args),
                    held["text"]),
                         icon="content_copy",
                         hint=t("manager.about.copy_hint"),
                         js=clipboard.COPY_JS % json.dumps(held["text"]))()
            panel.action(t("manager.about.support_package"),
                         support_dialog.open_support_package,
                         icon=verbs.GET_HELP, hint=t("manager.about.support_package_hint"))()
            panel.action(t("manager.about.whats_new"),
                         lambda: whats_new_page.open_whats_new(library, state, again=True),
                         icon=verbs.REVIEW, hint=t("manager.about.whats_new_hint"))()
            panel.action(t("manager.about.refresh"), lambda: reload(True), icon=verbs.REFRESH,
                         hint=t("word.reread"))()
        # One list for all of them, headings inside it, the way Settings next door
        # draws its sections: a list per group sizes a label column per group, and the
        # values then start at four different places down one page.
        entries: list[tuple[Any, Any]] = []
        for group in groups:
            entries.append((panel.HEADING, str(group.get("heading") or "")))
            entries += [_row(fact) for fact in group.get("facts") or []]
        panel.facts(ui, entries)


def _row(fact: dict[str, Any]) -> tuple[Any, Any]:
    """A line with no label is a sentence the group says about itself, and a `sub` fact
    belongs to the one above it."""
    label, value = str(fact.get("label") or ""), str(fact.get("value") or "")
    if not label:
        return (panel.FULL, lambda: ui.label(value).classes("manager-help"))
    shown: Any = panel.path_value(value) if fact.get("path") and value else value
    return (panel.SUB, (label, shown)) if fact.get("sub") else (label, shown)
