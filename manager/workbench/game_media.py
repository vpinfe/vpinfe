"""The media section of a game and its tables."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Sequence
from functools import partial
from pathlib import Path
from typing import Any

from nicegui import run, ui

from common.failures import why
from common.games import file_move as moved_files
from common.i18n import t
from common.media.media_specs import PAGED_KINDS, media_family, media_label_map
from manager.devices import plays_on
from manager.kit import art, confirm, deeplink, media_ownership, offload, panel, verbs
from manager.kit.on_page import on_page
from manager.library import (
    file_acts,
    file_facts,
    file_move,
    media_fill,
    mediamap,
    mediapages,
    mediasource,
    mediaview,
)
from manager.library import record as recorder
from manager.workbench.core import _outside_lines, _provenance, _table_address, _table_line
from manager.workbench.game_match import _match_button, _match_line

logger = logging.getLogger("vpinfe.manager.workbench.game_media")


def _on_disk(context: dict[str, Any], path: str) -> str:
    """Where a file the game's folder holds is on this machine, from the path it is
    addressed by there. Empty when the folder or the path is not known."""
    folder = str((context.get("game") or {}).get("folder") or "")
    return str(Path(folder) / path) if folder and path else ""


def _file_line(context: dict[str, Any], kind: str, name: str, view: str,
               path: str = "") -> None:
    """A file's name, which opens the file's own panel in `view` where a dock draws it,
    with the whole path on hover and Copy path after it. `view` is "" in the file's own
    panel, where the name is already that panel's."""
    classes = "manager-slot-file grow min-w-0"
    where = _on_disk(context, path)
    if not view:
        named = ui.label(name).classes(classes)
        if where:
            named.tooltip(where)
    else:
        table = str((context.get("row") or {}).get("table") or context["lens"] or "")
        to = "/manager?" + deeplink.query({"view": view, "game": context["game_id"],
                                           "table": table, "slot": {"kind": kind}})
        panel.link(name, to=to, classes=classes, hint=where)()
    if where:
        panel.copy_text(where)


async def _media_block(context: dict[str, Any]) -> None:
    """The map, and whatever tile is picked out of it."""
    library, game_id = context["library"], context["game_id"]
    holder = ui.column().classes("w-full gap-0")

    async def draw() -> None:
        table_id = context["lens"]
        entries = await offload.io(library.media_for, game_id, table_id or None)
        # Only from the game's lens. Looking at one table, the badge on a slot already
        # says whose file it is, and marking the others would be noise about tables
        # that are not the subject.
        overrides = ({} if table_id else
                     await run.io_bound(library.media_overrides, game_id))
        offered = await offload.io(_offered_media, context)
        kept = _kept_kinds(context, "media")
        context["devices"] = await offload.io(library.known_devices)
        wont_play = await _wont_play(context)
        holder.clear()
        with holder:
            mediamap.build(entries, game_id, table_id or "",
                           on_pick=lambda kind: _pick_slot(context, kind, draw),
                           selected=context["slot"]["kind"],
                           overrides=overrides, offered=offered, kept=kept,
                           wont_play=wont_play)
            # A slot arrived at by link can be anywhere in a map of twenty tiles,
            # including below the fold - and a selection you cannot see is not one.
            # `nearest` moves the least that makes it visible, so a tile already on
            # screen does not jolt the map, and clicking one never scrolls at all.
            #
            # Inside `with holder`, and that is not tidiness. `run_javascript` reaches
            # for the client through the current slot, and a redraw scheduled from a
            # click runs on a bare task with no slot at all - so out here it raises,
            # takes the rest of this function with it, and the dock below is never
            # cleared. The map updates, the editor keeps whatever it had, and nothing
            # says why.
            if context["slot"]["kind"]:
                ui.run_javascript("""
                requestAnimationFrame(() => {
                  const tile = document.querySelector('.manager-mediatile--on');
                  if (tile) tile.scrollIntoView({block: 'nearest', inline: 'nearest'});
                });
                """)
        dock = context.get("dock")
        if dock is not None:
            dock.clear()
            # Kept for the sources dialog, which says whether a file it is offering
            # is already serving one of this game's slots.
            context["media"] = entries
            kind = context["slot"]["kind"]
            detail = None
            matched = ""
            if kind and kind in entries:
                try:
                    detail = await offload.io(library.media_detail, game_id,
                                                table_id or None, kind)
                except Exception:
                    logger.debug("No detail for %s", kind, exc_info=True)
                matched = await _match_line(context, kind,
                                            (detail or {}).get("matched_to")
                                            or entries[kind].get("matched_to"))
            waiting = (await recorder.waiting_on(library, game_id, table_id or "", kind)
                       if kind and kind in entries else [])
            with dock:
                if kind and kind in entries:
                    _slot(context, kind, entries[kind], detail, written,
                          (overrides or {}).get(kind) or [], matched=matched,
                          waiting=waiting)
                else:
                    # The region is reserved either way, so it says what it is for
                    # rather than sitting there as an empty box. Named for what the
                    # user is looking at - the user sees - rather than for the slot
                    # they fill, which is our word and not theirs. No "above" or
                    # "beside" either: this region moves depending on the width.
                    with ui.column().classes("manager-dock-empty items-center gap-1"):
                        ui.label(t("manager.workbench.no_media")).classes("manager-empty-title")
                        ui.label(t("manager.workbench.media_hint")) \
                            .classes("manager-help")

    async def written() -> None:
        await draw()
        saved = context.get("saved")
        if callable(saved):
            await saved()

    async def placed() -> None:
        library.forget_media(game_id)
        await offload.io(library.media_for, game_id, None)
        if not holder.is_deleted:
            for redraw in context["redraws"]:
                await redraw()

    context["redraws"].append(written)
    await draw()
    recording = _record_media(context, placed)
    if context["lens"] and recording is None:
        return
    with ui.element("div").classes("manager-slot-actions px-3"):
        if not context["lens"]:
            panel.action(t("manager.media_fill.get_missing"),
                         lambda: media_fill.ask([game_id], context["state"], placed,
                                              name=str(context["game"].get("name") or "")),
                         icon=verbs.FETCH,
                         hint=t("manager.media_fill.get_missing.help"))()
        if recording is not None:
            recording()


def _record_media(context: dict[str, Any],
                  placed: Callable[[], Any]) -> Callable[[], None] | None:
    """Record Media for the game or the table in view, dimmed with why where this install
    records nothing; None where recording is not something it does."""
    able = recorder.capability(context["library"])
    if able is None:
        return None
    table = next((one for one in context["tables"] if one.get("id") == context["lens"]),
                 None)
    name = str(context["game"].get("name") or "")
    title = t("manager.record.title_table", name=name,
              table=_table_line(table, context["tables"])) if table is not None \
        else t("manager.record.title", name=name)
    return panel.action(t("manager.record.record_media"),
                        lambda: recorder.ask(context["library"],
                                             [(context["game_id"], context["lens"] or "")],
                                             title, context["state"], placed),
                        icon=verbs.RECORD, enabled=bool(able.get("available")),
                        hint=str(able.get("reason") or ""))


def _local_id(context: dict[str, Any]) -> str:
    return str(context["library"].discovery().get("install_id") or "")


async def _wont_play(context: dict[str, Any]) -> dict[str, list[str]]:
    """Each video in the lens that some device won't play, and those devices in words."""
    devices = context["devices"]
    local = _local_id(context)
    if not plays_on.asks_codecs(devices, local):
        return {}
    try:
        codecs = await offload.io(context["library"].video_codecs, context["game_id"],
                                  context["lens"] or None)
    except Exception:  # noqa: BLE001 - a codec not read is no warning
        logger.debug("No codecs for %s", context["game_id"], exc_info=True)
        return {}
    found = {kind: [one.label for one in plays_on.refusals(codec, devices, local)]
             for kind, codec in codecs.items()}
    return {kind: labels for kind, labels in found.items() if labels}


def _kept_kinds(context: dict[str, Any], family: str) -> set[str] | None:
    """The kinds this library collects, or None to filter nothing: an empty set would
    blank the surface."""
    return set(context["library"].kept_kinds()[family]) or None


def _offered_media(context: dict[str, Any]) -> dict[str, int]:
    """What the catalog lists for this game's empty slots.

    Swallowed on failure rather than taken as zero being reported: the map is about the
    library, and a catalog that cannot be reached should mark nothing rather than say
    the catalog has nothing.
    """
    try:
        return context["library"].offered_media(context["game_id"])
    except Exception:
        return {}


def _pick_slot(context: dict[str, Any], kind: str, draw: Any) -> None:
    """Pick a slot to see the detail of. Clicking the picked one again does nothing.

    Putting the panel away would make one control mean two things and cost the selection
    to a mis-click. The detail sits beside the map rather than over it, so there is
    nothing to dismiss - a slot stays picked until another is.
    """
    slot = context["slot"]
    if slot["kind"] == kind:
        return
    slot["kind"] = kind
    deeplink.sync(context["state"])
    asyncio.create_task(draw())


# An empty slot still shows what shape of thing belongs in it. Not the map's glyph
# rule, which returns nothing for a kind that normally has a frame to show - here
# there is no frame, which is the point.
_BLANK_ICON = {"image": "image", "video": "movie",
               "audio": "graphic_eq", "doc": "description"}


def _used_as(candidate: dict[str, Any]) -> str:
    """Who a file in a slot's detail answers for: its own tier while some table is shown
    it, otherwise Unused."""
    return str(candidate.get("tier") or "") if candidate.get("serves") \
        else media_ownership.UNUSED


def _slot_art(context: dict[str, Any], kind: str, entry: dict[str, Any],
              page: int = 1) -> None:
    """The slot's picture, player or blank outline. `page` is the page of a paged kind
    to show; Enlarge then steps through all of them, starting there."""
    game_id, table_id = context["game_id"], context["lens"]
    label = media_label_map().get(kind, kind)
    with ui.element("div").classes("manager-slot-art"):
        if entry.get("present"):
            version = entry.get("version")
            mediaview.preview(art.media(game_id, kind, table_id or "", version=version,
                               size=art.PANEL, page=page), kind, label)
            # On the picture, where the map puts it. Images only: a video keeps its
            # native controls here, and those carry a full-screen button already.
            if media_family(kind) == "image":
                if mediapages.count(kind, entry) >= mediapages.STRIP_FROM:
                    enlarge = partial(
                        mediaview.open_pages,
                        mediapages.addresses(game_id, kind, table_id or "", entry),
                        label, page)
                else:
                    enlarge = partial(
                        mediaview.open_viewer,
                        art.media(game_id, kind, table_id or "", version=version),
                        kind, label)
                ui.button(icon=verbs.ENLARGE, on_click=lambda: enlarge()) \
                    .props("flat dense round size=sm") \
                    .classes("manager-slot-zoom").tooltip(t("word.enlarge"))
        else:
            with ui.column().classes("manager-slot-blank items-center gap-1"):
                ui.icon(_BLANK_ICON.get(media_family(kind), "help_outline")) \
                    .classes("manager-slot-blank-icon")


def _slot(context: dict[str, Any], kind: str, entry: dict[str, Any],
          detail: dict[str, Any] | None, draw: Any,
          differing: list[dict[str, Any]] | None = None, *,
          titled: bool = True, in_place: bool = True, scope_chip: bool = True,
          links: Sequence[dict[str, Any]] = (), matched: str = "", draw_art: bool = True,
          waiting: Sequence[dict[str, Any]] = (), page: int = 1) -> None:
    """One slot: the art at the size of the room, and what there is to know about it.
    `draw_art=False` leaves the picture to the caller, which draws it somewhere else.
    `waiting` are the recordings kept for a decision on this slot. `page` is the page of a
    paged kind that `detail` describes, where the panel is one page's file.

    The picture is the subject. Everything else is one line each underneath, because
    the questions this panel answers - what is this, why this one, what else is here -
    are each a sentence, and a table of fields makes the reader do the joining.
    """
    library, game_id = context["library"], context["game_id"]
    table_id = context["lens"]
    label = media_label_map().get(kind, kind)
    # The map already knows the slot; detail only adds to it. A failed read should
    # cost the spec line, never the panel.
    detail = detail or {}
    present = bool(entry.get("present"))
    file_name = detail.get("file") or entry.get("file") or ""
    also_here = list(detail.get("tiers") or [])
    pages = mediapages.count(kind, entry)
    many = pages >= mediapages.STRIP_FROM
    # The strip carries the acts on each page, so the slot's own act is to take them all.
    in_strip = many and draw_art
    # A file panel for one page of several acts on that page and nothing else.
    one_page = many and not draw_art
    slot_label = mediapages.named(kind, page) if many or page > 1 else label

    @on_page
    async def remove() -> None:
        if one_page:
            owner = mediapages.owner_of(entry, table_id)
            if not mediapages.removable(entry):
                ui.notify(t("manager.workbench.nothing_removable"), type="info")
                return
            if not await mediapages.confirm_remove(kind, page, pages, owner):
                return
            taken = None
        else:
            taken = file_acts.removal(context["tables"], context["game"], table_id,
                                      detail.get("tiers"), entry,
                                      detail.get("after_remove"))
        if taken is not None and not await confirm.ask(
                t("manager.mediapages.remove_all", name=label, count=pages)
                if in_strip else t("manager.workbench.remove_asset", name=taken[0]),
                detail=taken[1], confirm=t("word.remove"), icon=verbs.REMOVE):
            return
        try:
            result = await offload.io(
                library.remove_media, game_id,
                mediapages.owner_of(entry, table_id) if one_page else table_id, kind,
                page if one_page else None)
        except Exception as exc:
            ui.notify(t("said.remove_failed"), caption=why(exc), type="negative")
            return
        gone = len(result.get("removed") or [])
        ui.notify(t("manager.workbench.removed_files", count=gone) if gone
                  else t("manager.workbench.nothing_removable"),
                  type="positive" if gone else "info")
        await draw()

    # A file the view does not own is not its to remove: it says where it comes from and
    # the slot offers Add, which makes one of its own.
    borrowed = present and file_acts.removal(
        context["tables"], context["game"], table_id, detail.get("tiers"), entry,
        detail.get("after_remove")) is None

    async def redrawn(_games: list[str]) -> None:
        for redraw in context["redraws"]:
            await redraw()

    with ui.column().classes("w-full gap-1 manager-slot p-2"):
        if titled:
            ui.label(label).classes("manager-card-title")

        if draw_art:
            if in_strip:
                mediapages.strip(context, kind, entry, draw, borrowed=borrowed)
            else:
                _slot_art(context, kind, entry)

        with ui.column().classes("w-full gap-0 manager-slot-facts"):
            if present:
                with ui.row().classes("items-start gap-2 w-full no-wrap"):
                    _file_line(context, kind, str(file_name), "media" if in_place else "",
                               str(detail.get("path") or entry.get("path") or ""))
                    via = detail.get("via") or entry.get("via")
                    if scope_chip or media_ownership.key_of(via) not in media_ownership.SCOPES:
                        media_ownership.badge(via)
                refused = plays_on.refusals(detail.get("video_codec"),
                                            context.get("devices") or [], _local_id(context))
                if refused:
                    with ui.row().classes("items-center gap-1 w-full"):
                        for one in refused:
                            ui.label(one.label) \
                                .classes("manager-tier manager-tier--warn").tooltip(one.why)
                spec = file_facts.media_spec(detail)
                if spec:
                    ui.label(spec).classes("manager-help")
                _provenance(context, detail.get("via") or entry.get("via"), borrowed)
                origin = str(detail.get("origin") or entry.get("origin") or "")
                # "unknown" is the ledger's honest answer and a useless line to read:
                # most files predate the ledger, so it would be on nearly every slot.
                if origin and origin not in ("unknown", "user"):
                    ui.label(t("manager.workbench.source",
                            source_name=(media_ownership.source_name(origin)))) \
                        .classes("manager-help")
                # Only when somebody has said - "not matched" is true of nearly every
                # file, and the button below is where the unanswered case belongs.
                if matched:
                    ui.label(matched).classes("manager-help")
                _outside_lines(links)
            else:
                ui.label(t("manager.workbench.missing_table", kind=label)
                         if table_id
                         else t("manager.workbench.missing_game", kind=label)) \
                    .classes("manager-help")

        # Only when there is more than one, because with one the sentence above has
        # already said where it is. Two is the case worth a list: the second file is
        # why an edit appeared to do nothing.
        if len(also_here) > 1:
            with ui.column().classes("w-full gap-0 manager-slot-others"):
                ui.label(t("manager.workbench.not_used")).classes("manager-slot-others-title")
                for item in also_here:
                    if item.get("wins"):
                        continue
                    with ui.row().classes("items-center gap-2 w-full no-wrap"):
                        ui.label(item.get("file") or "").classes("manager-slot-other-file")
                        media_ownership.badge(_used_as(item))
                ui.label(t("manager.workbench.shadowed_hint")) \
                    .classes("manager-help")

        # Only from the game's lens, and only when somebody differs. This is the whole
        # of Model B in the panel: the shared file above, and who is not using it.
        for other in (differing or []):
            with ui.row().classes("items-center gap-2 w-full no-wrap manager-slot-differs"):
                media_ownership.badge("table")
                said = _table_line(other) or other.get("filename") or ""
                panel.link(said, to=_table_address(context, str(other.get("table") or "")),
                           hint=other.get("file") or "")()

        if waiting:
            recorder.waiting_line(library, waiting, draw)

        with ui.row().classes("items-center gap-2 w-full manager-slot-actions") \
                .style("flex-wrap:wrap"):
            if not in_strip:
                ui.button(t("word.replace") if present and not borrowed else t("word.add"),
                          icon=verbs.ADD_ART,
                          on_click=lambda: mediasource.open_sources(
                              context, kind, slot_label, draw,
                              str(detail.get("path") or entry.get("path") or ""),
                              backglass_file=detail.get("backglass_file"), page=page)) \
                    .props("flat dense no-caps size=sm").classes("manager-action")
            if present and in_place and kind in PAGED_KINDS and not many and not borrowed:
                mediapages.add_button(context, kind, entry, draw)
            able = mediasource.records(library, kind) if page == 1 else None
            if able is not None:
                panel.action(t("manager.record.record"),
                             lambda: mediasource.open_sources(
                                 context, kind, label, draw,
                                 str(detail.get("path") or entry.get("path") or ""),
                                 recording_it=True,
                                 backglass_file=detail.get("backglass_file")),
                             icon=verbs.RECORD, enabled=bool(able.get("available")),
                             hint=str(able.get("reason") or ""))()
            if present:
                # Beside the acts on the bytes: a slot holding nothing has no identity.
                # Of the slot, which page 1 stands for.
                if page == 1:
                    _match_button(context, kind, label,
                                  detail if detail.get("path") else entry, draw)
                if not borrowed:
                    ui.button(t("manager.mediapages.clear") if in_strip
                              else t("word.remove"), icon=verbs.REMOVE, on_click=remove) \
                        .props("flat dense no-caps size=sm") \
                        .classes("manager-action manager-action--danger")
            if present and page == 1:
                file_move.card_buttons(
                    library, {"kind": kind, "game_id": game_id, "table": table_id,
                              "present": present, "via": entry.get("via"),
                              "path": entry.get("path")},
                    context["game"], moved_files.MEDIA, redrawn)
