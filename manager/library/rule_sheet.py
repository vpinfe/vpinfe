"""A reading document in the card, a rule sheet or a readme: read where the browser can
draw it, and edited where it is text.

A PDF goes to the browser's own viewer. A Markdown or text file is drawn as a page with an
Edit button that swaps the page for the text editor in place, and Cancel or Save swap it
back. A web page is drawn in a frame with every permission off, so a page that arrives
with a table cannot run anything in the Manager's window.

Library layer: a caller in the workbench hands over what the slot knows and what to do once
a save has changed the file.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from pathlib import PurePosixPath
from typing import Any

import markdown2
from nicegui import ui

from common.i18n import t
from manager.kit import busy, offload, panel, verbs
from manager.library import mediaview, text_editor

logger = logging.getLogger("vpinfe.manager.library.rule_sheet")

PDF, MARKDOWN, TEXT, FIXED, WEB = "pdf", "markdown", "text", "fixed", "web"
_STYLES = {".pdf": PDF, ".md": MARKDOWN, ".txt": TEXT, ".nfo": FIXED, ".html": WEB}


def style_of(name: str) -> str:
    """How the file is drawn, or blank for a file this does not draw - a picture, say, which
    the slot shows as it always has."""
    return _STYLES.get(PurePosixPath(name).suffix.lower(), "")


def _page_of(text: str) -> str:
    # Raw HTML in the file is escaped rather than passed on: the sanitizer after it is a
    # second line, not the first.
    return markdown2.markdown(text, safe_mode="escape",
                              extras=["tables", "fenced-code-blocks", "break-on-newline"])


def _body(style: str, shown: str, src: str, label: str) -> None:
    """`shown` is the page for Markdown, already made, and the file's text for a text one."""
    if style == WEB:
        mediaview.web_page(src, label)
    elif style == MARKDOWN:
        ui.html(shown).classes("manager-rulesheet-page manager-rulesheet-page--markdown")
    elif style == FIXED:
        ui.label(shown).classes("manager-rulesheet-page manager-rulesheet-page--fixed")
    else:
        ui.label(shown).classes("manager-rulesheet-page manager-rulesheet-page--text")


def _enlarge(act: Callable[[], Any]) -> None:
    panel.icon_only(verbs.ENLARGE, act, hint=t("word.enlarge")) \
        .props("size=sm").classes("manager-slot-zoom")


def draw(context: dict[str, Any], kind: str, label: str, src: str, path: str, *,
         then: Callable[[], Awaitable[Any]] | None = None) -> bool:
    """Draw the rule sheet or readme at `path` where the slot's art goes, and say whether
    this file is one it draws. `then` runs after a save has changed the file, and without it
    there is no Edit button."""
    style = style_of(path)
    if not style:
        return False
    if style == PDF:
        mediaview.pdf(src, label)
        _enlarge(lambda: mediaview.open_document(
            label, lambda: mediaview.pdf(src, label, large=True)))
        return True

    library, game_id, state = context["library"], context["game_id"], context["state"]
    holder = ui.element("div").classes("manager-rulesheet")

    async def read() -> dict[str, Any] | None:
        try:
            return await offload.io(library.asset_text, game_id, path)
        except Exception:  # noqa: BLE001 - the file is still there to open
            logger.debug("Could not read %s", path, exc_info=True)
            return None

    async def after_save() -> None:
        if then is not None:
            await then()
        if not holder.is_deleted:
            await view()

    async def view() -> None:
        found = await read()
        shown = ""
        if found is not None:
            shown = str(found["text"])
            if style == MARKDOWN:
                shown = await offload.io(_page_of, shown)
        holder.clear()
        with holder:
            if found is None:
                panel.link_out(t("manager.workbench.open", kind=label), to=src)()
                return
            editable = bool(found["editable"])
            with ui.row().classes("items-center gap-1 no-wrap manager-rulesheet-strip"):
                if not editable:
                    panel.line(str(found["why"]), classes="manager-help truncate")
                ui.space()
                panel.icon_only(verbs.ENLARGE, lambda: mediaview.open_document(
                    label, lambda: _body(style, shown, src, label)),
                    hint=t("word.enlarge"))
                if then is not None and editable:
                    panel.action(t("word.edit"), edit, icon=verbs.EDIT, inline=True,
                                 hint=t("manager.rule_sheet.edit.help"))()
            _body(style, shown, src, label)

    async def edit() -> None:
        found = await read()
        if found is None:
            await view()
            return
        holder.clear()
        inside = {**context, "kind": kind, "after_save": after_save, "after_cancel": view}
        with holder:
            text_editor.editor(inside, path, label, found,
                               "" if found["editable"] else str(found["why"]))

    async def fill() -> None:
        # A draft outlives the view that started it, so coming back to the slot puts the
        # typed text in front of the person rather than the page it would replace.
        if text_editor.draft_key(game_id, path) in text_editor.drafts_of(state):
            await edit()
        else:
            await view()

    busy.fill(holder, fill)
    return True
