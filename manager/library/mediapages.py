"""The pages of a flyer or an instruction card, drawn as a strip in the slot's panel.

The kind is one slot with several pictures in order. Each page can be looked at, replaced,
taken out or moved, and one more goes on after the last the same ways a first does.
"""

from __future__ import annotations

import logging
from functools import partial
from typing import Any

from nicegui import ui

from common.failures import why
from common.i18n import t
from common.media.media_specs import PAGED_KINDS, media_label_map
from manager.kit import art, confirm, media_ownership, offload, panel, verbs
from manager.kit.on_page import on_page
from manager.library import mediasource, mediaview

logger = logging.getLogger(__name__)

# One page is the slot as it always was; the strip is for a slot that has more than one.
STRIP_FROM = 2


def count(kind: str, entry: dict[str, Any]) -> int:
    """How many pages the slot shows: 0 for nothing, and for a kind that does not page."""
    return int(entry.get("pages") or 0) if kind in PAGED_KINDS else 0


def addresses(game_id: str, kind: str, table_id: str, entry: dict[str, Any]) -> list[str]:
    """Every page's picture at full size, in order."""
    version = entry.get("version")
    return [art.media(game_id, kind, table_id, version=version, page=number)
            for number in range(1, count(kind, entry) + 1)]


def named(kind: str, page: int) -> str:
    return t("media.page_named", label=media_label_map().get(kind, kind), page=page)


def owner_of(entry: dict[str, Any], table_id: str) -> str:
    """The table a page is taken out at: the lens's own where the table holds page 1,
    otherwise the folder's, which is the empty name."""
    return table_id if str(entry.get("via") or "") == "table" else ""


def removable(entry: dict[str, Any]) -> bool:
    """Only a page at the table's or the folder's own tier goes from here, and the default
    counts as the folder's."""
    return media_ownership.key_of(str(entry.get("via") or "")) in (
        media_ownership.TABLE, media_ownership.GAME)


async def confirm_remove(kind: str, page: int, pages: int, owner: str) -> bool:
    """Ask before one page goes. Pages after it move down a number, and a page of the
    folder's is every table's."""
    later = [t("manager.mediapages.remove_renumbers")] if page < pages else []
    shared = [t("manager.workbench.remove_asset.game")] if not owner else []
    return await confirm.ask(t("manager.mediapages.remove", name=named(kind, page)),
                             detail=later + shared, confirm=t("word.remove"),
                             icon=verbs.REMOVE)


def add(context: dict[str, Any], kind: str, entry: dict[str, Any], draw: Any) -> Any:
    """The dialog that takes a file as the page after the last."""
    after = max(count(kind, entry), 1) + 1
    return mediasource.open_sources(context, kind, named(kind, after), draw, "", page=after)


def add_button(context: dict[str, Any], kind: str, entry: dict[str, Any], draw: Any) -> None:
    """For a slot of one page, where the strip is not drawn."""
    panel.action(t("manager.mediapages.add"), partial(add, context, kind, entry, draw),
                 icon=verbs.ADD_ART, hint=t("manager.mediapages.add_hint"))()


def strip(context: dict[str, Any], kind: str, entry: dict[str, Any], draw: Any, *,
          borrowed: bool = False) -> None:
    """The slot's pages, numbered, each with its own acts, and Add page after the last.
    Pages the view does not own can be enlarged and nothing else."""
    library, game_id = context["library"], context["game_id"]
    table_id = context["lens"] or ""
    pages = count(kind, entry)
    full = addresses(game_id, kind, table_id, entry)
    owner = owner_of(entry, table_id)

    @on_page
    async def replace(number: int) -> None:
        try:
            detail = await offload.io(library.media_detail, game_id, table_id or None,
                                      kind, number)
        except Exception:  # noqa: BLE001 - the dialog opens without what it would add
            logger.debug("No detail for %s page %s", kind, number, exc_info=True)
            detail = {}
        mediasource.open_sources(context, kind, named(kind, number), draw,
                                 str(detail.get("path") or ""), page=number)

    @on_page
    async def remove(number: int) -> None:
        if not removable(entry):
            ui.notify(t("manager.workbench.nothing_removable"), type="info")
            return
        if not await confirm_remove(kind, number, pages, owner):
            return
        try:
            await offload.io(library.remove_media, game_id, owner, kind, number)
        except Exception as exc:
            ui.notify(t("said.remove_failed"), caption=why(exc), type="negative")
            return
        await draw()

    @on_page
    async def move(number: int, to: int) -> None:
        try:
            await offload.io(library.move_media_page, game_id, table_id, kind, number, to)
        except Exception as exc:
            ui.notify(t("manager.workbench.move_failed"), caption=why(exc), type="negative")
            return
        await draw()

    with ui.row().classes("manager-pagestrip w-full"):
        for number in range(1, pages + 1):
            src = art.media(game_id, kind, table_id, version=entry.get("version"),
                            size=art.CELL, page=number)
            with ui.column().classes("manager-pagecard gap-1"):
                with ui.element("div").classes("manager-pagecard-art"):
                    ui.html(f'<img src="{src}" loading="lazy">') \
                        .classes("manager-pagecard-media")
                ui.label(t("manager.mediapages.page", page=number)).classes("manager-help")
                with ui.row().classes("manager-pagecard-acts gap-0 no-wrap"):
                    panel.icon_action(
                        t("word.enlarge"),
                        partial(mediaview.open_pages, full,
                                media_label_map().get(kind, kind), number),
                        icon=verbs.ENLARGE, hint=t("word.enlarge"))()
                    if borrowed:
                        continue
                    panel.icon_action(
                        t("word.replace"), partial(replace, number),
                        icon=verbs.REPLACE, hint=t("word.replace"))()
                    panel.icon_action(
                        t("word.remove"), partial(remove, number),
                        icon=verbs.REMOVE, hint=t("word.remove"))()
                    panel.icon_action(
                        t("manager.mediapages.earlier"), partial(move, number, number - 1),
                        icon=verbs.EARLIER, hint=t("manager.mediapages.earlier"),
                        enabled=number > 1)()
                    panel.icon_action(
                        t("manager.mediapages.later"), partial(move, number, number + 1),
                        icon=verbs.LATER, hint=t("manager.mediapages.later"),
                        enabled=number < pages)()
        if not borrowed:
            with ui.element("div").classes("manager-pagecard-add"):
                panel.action(t("manager.mediapages.add"),
                             partial(add, context, kind, entry, draw),
                             icon=verbs.ADD_ART, hint=t("manager.mediapages.add_hint"))()
