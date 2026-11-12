"""The sections a game and its tables share: marks, settings, collections, entries and details."""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable, Sequence
from functools import partial
from pathlib import Path
from typing import Any, NamedTuple

from nicegui import run, ui

from common.devices import install_identity
from common.failures import why
from common.games import ties
from common.i18n import t
from common.online import sites, vps_updates
from manager.backups import backup_rows
from manager.extensions import ext_groups, ext_lists
from manager.kit import (
    confirm,
    deeplink,
    dropdown,
    game_tables,
    offload,
    panel,
    stars,
    tag_chips,
    undo,
    verbs,
    when,
)
from manager.kit.data import Library, tag_source
from manager.kit.on_page import on_page
from manager.library import (
    collection_adds,
    discovery_marks,
    file_acts,
    game_states,
    getting_file,
    mediamap,
    mediasource,
    vps_records,
)
from manager.library.match_group import (
    DETAIL_WORDS,
    change_match,
    match_rows,
    pick_a_match,
    unmatched,
)
from manager.settings import dof
from manager.workbench import game_default
from manager.workbench.core import (
    _KEEP_SCROLL,
    FULL,
    HEADING,
    _asset_name,
    _choose,
    _outside,
    _rows,
    _said_file,
    _said_name,
    _save,
    _save_game_overrides,
    _switch,
    _table_address,
    _write,
)
from manager.workbench.game_match import (
    _based_on,
    _pick_a_release,
    _read_releases,
    _release_shown,
)
from manager.workbench.launcher import _feature_chips, _launcher_report, _play_action

logger = logging.getLogger("vpinfe.manager.workbench.game_table")


def _media_label(context: dict[str, Any]) -> str:
    present, borrowed, total = mediamap.summary(
        context["library"].media.get(context["game_id"], {}))
    label = (t("manager.workbench.media_borrowed", present=(present), total=(total),
               borrowed=(borrowed)) if borrowed
             else t("manager.workbench.media", present=(present), total=(total)))
    return label + _media_scope(context)


def _media_scope(context: dict[str, Any]) -> str:
    """The lens used to be repeated here. The header states it now, once."""
    return ""


async def _game_block(context: dict[str, Any]) -> None:
    """What the game is - the machine, not the file.

    Shown whichever subject is selected. A table belongs to a game, and the VPS match,
    the manufacturer and the theme are as true while you are looking at one of its files
    as they are otherwise. Substituting one for the other throws away half of what
    somebody is looking at.
    """
    game = context["game"]
    library = context["library"]
    vps_id = str(game.get("vps_id") or "")
    found: dict[str, Any] = await offload.io(library.vps_entry, vps_id) if vps_id else {}
    differs = (await offload.io(library.vps_details, context["game_id"])
               if found.get("name") else [])
    held = bool(found) or not vps_id or await offload.io(library.vps_catalog_held)
    links = await offload.io(library.outside_links, context["game_id"])
    copies = await backup_rows.listed(library, "game", context["game_id"])
    # Under Tables the table's own section leads with them, and saying each twice on
    # one panel is what teaches a reader to skip the block.
    findings = [] if context["lens"] else await _findings(context, context["tables"])
    # Test lights the cabinet, so it is offered where this device has a frontend.
    lights = install_identity.FRONTEND in (library.discovery().get("features") or ())
    dof_state = await dof.event_state(library) if lights else None
    with ui.column().classes("gap-0 manager-form"):
        _rows(ui, findings + _game_entries(context, found, differs, held, links, copies,
                                           dof_state))
        # The facts above sit inside `.manager-facts`' side padding; this block is drawn
        # outside that grid and needs the same edge.
        with ui.column().classes("gap-0 w-full px-3"):
            # A region: hiding a table changes which one plays, and the list says so.
            context["tables_list"] = panel.region(partial(_tables_block, context))
            context["tables_list"]()
        ext_groups.fill(context, "details", "game")


async def _table_block(context: dict[str, Any]) -> None:
    """What this table is - the file somebody built, and what it needs to run."""
    chosen = next((t for t in context["tables"] if t.get("id") == context["lens"]),
                  None)
    match = await _release_match(context, chosen) if chosen is not None else []
    links = (await offload.io(context["library"].outside_links, context["game_id"],
                              str(chosen.get("id") or ""))
             if chosen is not None and chosen.get("id") else [])
    findings = await _findings(context, [chosen]) if chosen is not None else []
    with ui.column().classes("gap-0 manager-form"):
        if chosen is None:
            ui.label(t("manager.workbench.none_selected")).classes("manager-help")
            return
        _rows(ui, _table_entries(chosen, context, match, links, findings))
        ext_groups.fill(context, "details", "table")
    ui.run_javascript(_KEEP_SCROLL % f"table:{chosen.get('id') or ''}".replace("'", "\\'"))


async def _table_settings(context: dict[str, Any]) -> None:
    from manager.workbench import app_settings

    await app_settings.section(context)


async def _selection_marks(context: dict[str, Any]) -> None:
    from manager.workbench import selection_marks

    await selection_marks.section(context, selection_marks.TABLES)


async def _game_selection_marks(context: dict[str, Any]) -> None:
    from manager.workbench import selection_marks

    await selection_marks.section(context, selection_marks.GAMES)


async def _selection_settings(context: dict[str, Any]) -> None:
    from manager.workbench import app_settings

    await app_settings.selection_section(context)


async def _release_match(context: dict[str, Any],
                         table: dict[str, Any]) -> list[tuple[Any, Any]]:
    """Which build of the machine this file is, as the table's VPS Release group.

    No group at all where the game has no VPS entry.
    """
    entry = str(context["game"].get("vps_id") or "")
    if not entry:
        return []
    source = table.get("source") or {}
    bound = str(source.get("vps_file_id") or "")
    rows: list[tuple[Any, Any]] = [(HEADING, t("manager.workbench.vps_release"))]
    # A table whose file is gone has nothing to match.
    can_match = bool(table.get("available"))

    async def pick() -> None:
        await _pick_a_release(context, table)

    def acts(*, matched: bool) -> list[tuple[Any, Any]]:
        return [(FULL, change_match(pick, matched=matched, release=True))] if can_match else []

    if not bound:
        none = game_states.release().state(ties.release_state(
            {"game_vps_id": entry, "source": source}))
        return rows + ([(FULL, panel.state(none.word, "off", hint=none.meaning))]
                       if none else []) + acts(matched=False)
    releases, unread = await _read_releases(context, entry)
    release = next((one for one in releases
                    if str(one.get("vps_file_id") or "") == bound), None)
    # Found across the whole catalog, though no longer under this entry.
    named = game_tables.release_version_and_authors(table)
    if release is None and named is not None:
        release = {"version": named[0], "authors": source.get("authors")}
    if release is not None:
        rows.append((FULL, partial(_release_shown, release)))
        if source.get("mod_of"):
            rows.append((FULL, partial(_based_on, source["mod_of"])))
        how = game_states.release().state(ties.release_state(
            {"game_vps_id": entry, "source": source}))
        if how is not None:
            rows.append((t("manager.workbench.match_how"),
                         panel.state(how.word, "off", hint=how.meaning)))
    else:
        try:
            held = not unread and await offload.io(context["library"].vps_catalog_held)
        except Exception as exc:
            logger.warning("manager: could not read VPS for %s", entry, exc_info=True)
            held, unread = False, why(exc)
        rows += unmatched(release_match_gap(entry, bound, held, read=not unread),
                          hint=unread)
    return rows + acts(matched=True)


def _override(effective: str, found: str | None, source: str,
              save: Callable[[str], Any], *, shown: str | None = None,
              hint: str = "", beside: Callable[[str], str] | None = None,
              placeholder: str = "",
              after: Callable[[ui.input], Any] | None = None) -> Callable[[], None]:
    """One field the user may override, drawn into a fact row.

    A field the whole time. The read state and the edit state are one element, so there
    is nothing for the alignment to drift against and nothing moves on the first click.

    `found` is what the field would say with no override, or None where nothing but the
    user ever supplies one. Where there is a `found`, an amber revert appears exactly
    when the value differs from it, and typing that value back clears the override -
    so the mark's presence always means "this differs from the default".

    `save` answers False where the write was refused. `beside` maps the value to the
    address of a link drawn after the field, or "" for none; it follows the value as it
    is saved. `placeholder` is what an empty field stands for. `after` is handed the
    field once it is drawn and draws what sits inline after it.
    """
    current = effective if shown is None else shown

    def draw() -> None:
        held = {"value": current}

        def address() -> str:
            return beside(held["value"]) if beside is not None else ""

        def link() -> None:
            if address():
                panel.link_out("", to=address())()

        with ui.element("div").classes("manager-fact-edit"):
            field = ui.input(value=current, placeholder=placeholder) \
                .props("dense borderless").classes("manager-edit-field")
            if hint:
                field.tooltip(hint)
            beside_link = panel.region(link)
            if beside is not None:
                beside_link()
            if after is not None:
                after(field)
            icon = ui.icon(verbs.REVERT).classes("manager-revert")
            if found is not None:
                target = f'"{found}"' if found else "empty"
                icon.tooltip(t("manager.workbench.revert", target=(target),
                        source=(source)))

            def mark() -> None:
                """The amber revert, present exactly when the value differs from the
                default."""
                differs = found is not None and held["value"] != found
                icon.visible = differs
                if differs:
                    field.classes("manager-edit-field--differs")
                else:
                    field.classes(remove="manager-edit-field--differs")

            def settle(sent: str) -> None:
                """What the field says once `sent` is saved: clearing an override brings
                the discovered value back."""
                held["value"] = found if found is not None and not sent else sent
                if str(field.value or "") != held["value"]:
                    field.value = held["value"]
                mark()
                beside_link.refill()

            mark()

            async def commit() -> None:
                value = str(field.value or "").strip()
                if value == held["value"]:
                    return
                saved, held["value"] = held["value"], value
                # Sending the discovered value is how an override is cleared, so the
                # two cases are one request rather than a delete and a write.
                sent = "" if found is not None and value == found else value
                if await save(sent) is False:
                    held["value"] = saved
                    return
                settle(sent)

            def cancel() -> None:
                field.value = held["value"]
                field.run_method("blur")

            async def revert() -> None:
                if await save("") is not False:
                    settle("")

            field.on("blur", commit)
            field.on("keydown.enter", lambda: field.run_method("blur"))
            field.on("keydown.escape", cancel)
            icon.on("click", revert)

    return draw



def release_match_gap(entry: str, bound: str, held: bool,
                      read: bool = True) -> tuple[str, str, str]:
    """The same for a table's release, which has none to draw. `read` is whether the
    entry's releases could be read."""
    if not entry:
        return ("manager.workbench.not_matched", "off",
                "manager.workbench.releases_unmatched")
    if not bound:
        return ("manager.workbench.not_matched", "off", "")
    if not read:
        return ("word.unknown", "unknown", "manager.workbench.vps_failed")
    if not held:
        return ("word.unknown", "unknown", "manager.workbench.no_vps_data")
    return ("manager.workbench.not_listed", "warn",
            "manager.workbench.release_unlisted")



def _game_entries(context: dict[str, Any], entry: dict[str, Any],
                  differs: list[dict[str, Any]], held: bool,
                  links: Sequence[dict[str, Any]] = (),
                  copies: list[dict[str, Any]] | None = None,
                  dof_state: dict[str, Any] | None = None) -> list[tuple[Any, Any]]:
    game = context["game"]
    folder = str(game.get("folder") or "")
    found = game.get("discovered") or {}

    # The block that says where the details have come apart from the entry's. It reads
    # the overrides, so a save reads it again.
    reading = {"differs": differs}

    def draw_differs() -> None:
        if reading["differs"]:
            _details_differ(context, reading["differs"])()

    differing = panel.region(draw_differs)

    async def reread_differs() -> None:
        if entry.get("name"):
            try:
                reading["differs"] = await offload.io(context["library"].vps_details,
                                                       context["game_id"])
            except Exception:  # noqa: BLE001 - the block keeps what it said; the save stands
                logger.warning("manager: could not read VPS for %s", context["game_id"],
                               exc_info=True)
        differing.refill()

    def save(key: str) -> Callable[[str], Awaitable[bool]]:
        async def write(value: str) -> bool:
            if not await _save_game_overrides(context, {key: value}):
                return False
            await reread_differs()
            return True
        return write

    def field(label: str, key: str, alt: str) -> tuple[Any, Any]:
        return (label, _override(str(game.get(key) or ""), str(found.get(key) or ""),
                                 "VPS", save(alt)))

    vps_id = str(game.get("vps_id") or "")
    declared = (game.get("overrides") or {}).get("alt_vps_id", "") is None
    entries: list[tuple[Any, ...]] = [(HEADING, t("manager.workbench.vps_game"))]
    entries += match_rows(entry, vps_id, declared, held, game_tables.how_matched(game))
    entries.append((FULL, change_match(lambda: pick_a_match(context),
                                       matched=bool(vps_id) or declared)))
    entries.append((FULL, differing))

    ipdb = str(game.get("ipdb_id") or "")
    entries += [
        (HEADING, t("manager.workbench.details")),
        (t("word.name"), _override(game.get("name") or "",
                found.get("name") or "",
                           "VPS", save("alt_title"))),
        field(t("word.manufacturer"), "manufacturer", "alt_manufacturer"),
        field(t("word.year"), "year", "alt_year"),
        field(t("manager.workbench.type"), "type", "alt_type"),
        (t("manager.workbench.themes"), ", ".join(game.get("themes") or []) or "-"),
        (t("manager.workbench.ipdb"),
         _override(ipdb, str(found.get("ipdb_id") or ""), "VPS", save("alt_ipdb_id"),
                   beside=lambda value: IPDB_URL.format(id=value) if value else "")),
        *_outside(links),
        (t("word.folder"), panel.path_value(folder) if folder else "-"),
    ]
    if copies is not None:
        entries.append(backup_rows.entry(
            context["library"], "game", context["game_id"], str(game.get("name") or folder),
            copies, context["rebuild"]))
    return entries + _game_marks(context) + _game_frontend(context, dof_state)


def _game_frontend(context: dict[str, Any],
                   dof_state: dict[str, Any] | None = None) -> list[tuple[Any, Any]]:
    """The game's DOF event, and Test beside it where this device has a frontend to light
    it for. `dof_state` is `dof.event_state`'s answer there and None elsewhere."""
    overrides = context["game"].get("overrides") or {}

    async def save_dof(value: str) -> bool:
        return await _save_game_overrides(context, {"frontend_dof_event": value})

    def test(field: ui.input) -> None:
        tester = dof.Tester(context["library"], dof_state or {},
                            lambda: str(field.value or ""))
        tester()
        field.on_value_change(tester.refresh)

    # Empty is the revert: nothing but the user supplies this, and clearing it asks for
    # a different event on every selection.
    entry = _override(overrides.get("frontend_dof_event") or "", None, "", save_dof,
                      placeholder=t("manager.workbench.dof_random"),
                      after=test if dof_state is not None else None)

    def draw() -> None:
        with ui.column().classes("gap-1 min-w-0"):
            entry()
            ui.label(t("manager.workbench.dof_event_hint")).classes("manager-help")

    return [
        (HEADING, game_tables.FRONTEND),
        (t("manager.workbench.dof_event"), draw),
    ]


def _game_marks(context: dict[str, Any]) -> list[tuple[Any, Any]]:
    game = context["game"]
    record = game.get("user") or {}

    async def rate(value: int) -> None:
        await _write(context, context["library"].set_game_rating,
                     context["game_id"], value)

    async def favorite(event: Any) -> None:
        if await _save(context, context["library"].set_game_favorite,
                       context["game_id"], bool(event.value)):
            await _recounted(context)

    async def private(event: Any) -> None:
        if await _save(context, context["library"].set_game_private,
                       context["game_id"], bool(event.value)):
            await _recounted(context)

    async def retag(chosen: list[str]) -> None:
        await _write(context, context["library"].set_game_tags,
                     context["game_id"], chosen)

    return [
        (HEADING, t("manager.workbench.yours")),
        (t("manager.workbench.game_rating"), stars.draw(int(game.get("rating") or 0), rate)),
        (t("word.favorite"),
         lambda: _switch(bool(record.get("favorite")), favorite,
                         hint=t("manager.workbench.filter_hint"))),
        (game_tables.PRIVATE_WORDS[0], lambda: _switch(bool(game.get("private")), private)),
        panel.note(game_tables.PRIVATE_HELP),
        (t("manager.workbench.tags"),
         _tag_picker(list(record.get("tags") or []), context["library"], retag,
                     list(game.get("derived_tags") or []))),
    ]


def _table_marks(context: dict[str, Any], table: dict[str, Any]) -> list[tuple[Any, Any]]:
    record = table.get("user") or {}
    table_id = str(table.get("id") or "")

    async def rate(value: int) -> None:
        await _write(context, context["library"].set_table_rating,
                     context["game_id"], table_id, value)

    async def retag(chosen: list[str]) -> None:
        await _write(context, context["library"].set_table_tags,
                     context["game_id"], table_id, chosen)

    return [
        (HEADING, t("manager.workbench.yours")),
        (t("manager.workbench.table_rating"),
         stars.draw(int(table.get("rating") or 0), rate)),
        (t("manager.workbench.tags"),
         _tag_picker(list(record.get("tags") or []), context["library"], retag,
                     list(table.get("derived_tags") or []))),
    ]


HELD_HOW = {"added": "manager.workbench.held_manually_added",
            "matched": "manager.workbench.held_rule_matched",
            "taken_out": "manager.workbench.held_manually_removed"}


async def _collections_block(context: dict[str, Any]) -> None:
    """Every collection holding this game, the ones keeping it out, and the way to add
    it to another."""
    library, game_id = context["library"], context["game_id"]
    try:
        held = await offload.io(library.game_collections, game_id)
        every = await offload.io(library.load_collections)
        members = await offload.io(library.held_members,
                                   [str(one.get("name") or "") for one in every])
    except Exception as exc:  # noqa: BLE001 - the panel says why, never raises
        panel.line(t("manager.workbench.collections_failed"), hint=why(exc),
                   classes="manager-help px-3")
        return
    with ui.column().classes("gap-0 manager-form w-full min-w-0"):
        if not held["collections"] and not held["taken_out"]:
            ui.label(t("manager.workbench.no_collections")).classes("manager-help px-3")
        for one in held["collections"]:
            _collection_row(context, one)
        for one in held["taken_out"]:
            _collection_row(context, {**one, "how": "taken_out"})
        with ui.element("div").classes("manager-slot-actions px-3"):
            _add_to_collection(context, every, members)
        ext_groups.fill(context, "collections")


def _collection_row(context: dict[str, Any], one: dict[str, Any]) -> None:
    library, game_id = context["library"], context["game_id"]
    name = str(one.get("name") or "")
    how = str(one.get("how") or "")

    @on_page
    async def act() -> None:
        if how != "taken_out":
            await collection_adds.remove(library, name, [game_id],
                                         then=partial(_game_redrawn, context))
            return
        try:
            await run.io_bound(library.unexclude_from_collection, name, game_id, None)
        except Exception as exc:  # noqa: BLE001
            ui.notify(t("manager.workbench.save_failed"), caption=why(exc), type="negative")
            return
        ui.notify(t("manager.workbench.restore_into", name=name), type="positive")
        await context["rebuild"]()

    with ui.row().classes("items-center gap-2 w-full no-wrap manager-member-row"):
        with ui.row().classes("items-center gap-2 grow min-w-0 no-wrap"):
            panel.link(name, to="/manager?" + deeplink.query(
                {"view": "collections", "collection": name}))()
            ui.label(t(HELD_HOW.get(str(one.get("how")),
                                    "manager.workbench.held_manually_added"))) \
                .classes("manager-member-chip manager-tier manager-tier--off")
        with ui.element("div").classes("manager-row-action"):
            if how == "taken_out":
                ui.button(icon=verbs.REVERT, on_click=act) \
                    .props("flat dense round size=sm") \
                    .tooltip(t("manager.workbench.put_back_2"))
            else:
                ui.button(icon=verbs.REMOVE if how == "added" else verbs.EXCLUDE,
                          on_click=act) \
                    .props("flat dense round size=sm") \
                    .tooltip(t("manager.workbench.remove_collection"))


def _add_to_collection(context: dict[str, Any], every: list[dict[str, Any]],
                       members: dict[str, list[dict[str, Any]]]) -> None:
    library, game_id, table_id = context["library"], context["game_id"], context["lens"]
    offer = collection_adds.Offer(
        library, [collection_adds.Row(game_id, table_id)],
        collection_adds.TABLES if table_id else collection_adds.GAMES,
        "", then=partial(_game_redrawn, context))
    button = ui.button(t("manager.workbench.to_collection"), icon=verbs.ADD_TO_LIST) \
        .props("flat dense no-caps size=sm").classes("manager-action")
    with button, ui.menu() as menu:
        collection_adds.draw_every(offer, every, members, menu.close)


async def _game_redrawn(context: dict[str, Any]) -> None:
    """The panel redrawn if it still shows this game, and the grid's row either way. Run
    again by an Undo, when the panel may have moved on."""
    state, game_id = context["state"], context["game_id"]
    if state.get("view") not in ("games", "tables"):
        return
    if state.get("game") == game_id:
        await context["rebuild"]()
        return
    refresh = state.get("refresh_game")
    if callable(refresh):
        await refresh(game_id)


def _rule_sheet(context: dict[str, Any]) -> dict[str, Any]:
    slot = (context["library"].media.get(context["game_id"]) or {}).get("rule_sheet") or {}
    return slot if slot.get("present") else {}


async def _collections_held(library: Library, game_id: str) -> int | None:
    """How many collections hold this game; None where the read failed."""
    try:
        held = await offload.io(library.game_collections, game_id)
    except Exception:  # noqa: BLE001 - a count in a label is not worth the panel
        logger.warning("manager: could not count the collections of a game", exc_info=True)
        return None
    return len(held["collections"])


async def _recounted(context: dict[str, Any]) -> None:
    """The rail's Collections count after a write that can move the game in or out of a
    smart collection without rebuilding the panel."""
    context["in_collections"] = await _collections_held(context["library"],
                                                        context["game_id"])
    for relabel in context.get("relabels", []):
        await relabel()


def _collections_label(context: dict[str, Any]) -> str:
    held = context["in_collections"]
    return t("manager.workbench.collections") if held is None \
        else t("manager.workbench.collections_counted", count=held)


def _table_entries(table: dict[str, Any],
                   context: dict[str, Any] | None = None,
                   match: Sequence[tuple[Any, Any]] = (),
                   links: Sequence[dict[str, Any]] = (),
                   findings: Sequence[tuple[Any, Any]] = ()) -> list[tuple[Any, Any]]:
    """One table's own facts.

    The rom is the one it resolves to with any alias followed, which is the one that
    has to exist - the declared name is shown beside it only when they differ, because
    that is the case somebody has to reason about.
    """
    pinmame = (table.get("dependencies") or {}).get("pinmame") or {}
    declared = str(pinmame.get("declared") or "")
    effective = str(pinmame.get("effective") or "")
    rom = effective or declared or "-"
    if declared and effective and declared != effective:
        rom = t("manager.workbench.effective_declared",
                effective=(effective), declared=(declared))

    # Grouped by what a fact is about, one vocabulary shared with the views and the
    # grid. Each group carries the actions that work on it.
    features = table.get("features") or {}

    entries: list[tuple[Any, Any]] = [*findings]
    entries += match

    # What this file is. "Filename" rather than the group's own word, which named the
    # filename here and the on-disk state under Status - one word, two facts, both on
    # screen at once.
    reference = table.get("reference") or {}
    if game_tables.is_keyed(table):
        # Nothing read a file, so there is no version, author or hash to show and a
        # row of dashes would say we looked. What it has is the ID and whose it is.
        entries += [
            (HEADING, game_tables.KNOWN_AS),
            (t("word.id"), table.get("key") or "-"),
            (t("word.program"), table.get("app_name") or "-"),
            panel.note(t("manager.workbench.keyed_hint")),
        ]
    elif reference:
        # Where it is comes first, because that is the fact that makes this entry
        # different from every other one on the page.
        where = str(reference.get("resolved") or reference.get("path") or "")
        entries += [
            (HEADING, game_tables.ELSEWHERE),
            (t("word.path"), panel.path_value(where) if where else "-"),
            (t("word.now"), panel.state(
                game_tables.word_for(game_tables.REACH_WORDS,
                                     not reference.get("reachable")),
                "on" if reference.get("reachable") else "warn")),
        ]
        if not reference.get("reachable"):
            # The distinction that matters: nothing here has been lost, so the thing
            # to do is make the location reachable, not forget the entry.
            entries.append(panel.note(
                t("manager.workbench.file_offline")))
        entries += _file_facts(table)
    else:
        entries += [
            (HEADING, game_tables.FILE),
            (t("manager.workbench.filename"), table.get("filename") or "-"),
            *_file_facts(table),
            (t("manager.workbench.hash"), table.get("file_hash") or "-"),
            *_made_from(table, context),
        ]
    entries += _outside(links)

    # Its own group. These say what the table implements, which is not the same
    # question as what plays it - the group they shared could not be named honestly.
    if features:
        # The chips span the row: the heading already says Features, and a row
        # labelled for its own group is the File/Filename collision again.
        entries += [(HEADING, game_tables.FEATURES),
                    (FULL, lambda: _feature_chips(features))]
    if context is not None:
        entries += _table_marks(context, table)

    # Can it run, and how. The dependencies are shown as evidence, never managed
    # here: a finding jumps to where it is fixed.
    entries += [(HEADING, game_tables.LAUNCH)]
    present = bool(table.get("available"))
    entries += [(t("manager.workbench.run"), _launch_state(table.get("launchable")))]
    # Only where there is a file of this game's to be on disk or not. Where it is is
    # already answered above for a reference, in words that fit it - and "Missing" is
    # what a deleted file says, which is not what an unmounted share is. An entry with
    # no file at all has nothing this row could report.
    if not game_tables.is_referenced(table) and not game_tables.is_keyed(table):
        entries.append(
            (game_tables.FILE,
             _state(game_tables.word_for(game_tables.FILE_WORDS, not present),
                    "on" if present else "bad")))
    if not game_tables.is_keyed(table):
        entries.append((t("manager.workbench.rom"), _rom_state(pinmame, rom, context=context)))
    if context is not None:
        entries.append(_hidden_row(context, table))
        entries.append(_launcher_report(context, table))
        entries += [(FULL, _play_action(context, table))]
    return entries


def _file_facts(table: dict[str, Any]) -> list[tuple[Any, Any]]:
    """What the file itself says. One quiet line where the table is named by the release
    it is matched to and the file reads differently, else its version and authors."""
    if says := game_tables.file_says(table):
        return [(t("manager.workbench.file_says"), says)]
    return [(t("word.version"), table.get("version") or "-"),
            (t("word.author"), ", ".join(table.get("authors") or []) or "-")]


def _made_from(table: dict[str, Any],
               context: dict[str, Any] | None) -> list[tuple[Any, Any]]:
    """The file a patch made this table from: the way to that table while it is on disk,
    Replaced where another file has its name, and Missing where nothing does."""
    base = (table.get("source") or {}).get("base") or {}
    if not base.get("file"):
        return []
    tables = (context or {}).get("tables") or []
    held = next((one for one in tables if base.get("available")
                 and str(one.get("id") or "") == str(base.get("table_id") or "")), None)
    if context is not None and held is not None:
        return [(t("manager.workbench.made_from"), panel.link(
            game_tables.name_among(held, tables),
            to=_table_address(context, str(held["id"]))))]
    if base.get("available"):
        return [(t("manager.workbench.made_from"), str(base["file"]))]
    named = any(one.get("available") and one.get("filename") == base["file"]
                and str(one.get("id") or "") != str(base.get("table_id") or "")
                for one in tables if one is not table)
    word, why = ((t("manager.workbench.replaced"), t("manager.workbench.name_reused"))
                 if named else (game_tables.word_for(game_tables.FILE_WORDS, True), ""))

    def gone() -> None:
        with ui.row().classes("items-center gap-2 no-wrap min-w-0"):
            ui.label(str(base["file"])).classes("truncate min-w-0")
            chip = ui.label(word) \
                .classes("manager-member-chip manager-tier manager-tier--warn")
            if why:
                chip.tooltip(why)

    return [(t("manager.workbench.made_from"), gone)]


_state = panel.state


def _launch_state(launchable: bool | None) -> Any:
    """The rollup, above the dependencies it is built from.

    Its inputs stay on screen underneath: a bare "Blocked" with no reason is a state
    somebody has to go hunting behind, and the row that explains it is the next one.
    """
    if launchable is None:
        return _state(game_tables.LAUNCH_UNKNOWN, "unknown")
    return _state(game_tables.word_for(game_tables.LAUNCH_WORDS, not launchable),
                  "on" if launchable else "bad")


def _tag_picker(held: list[str], library: Library,
                on_change: Callable[[list[str]], Any],
                derived: Sequence[str] = ()) -> Callable[[], None]:
    """The tags on this game, and the ones the library already knows.

    The same control a multi-valued filter axis uses - chips for what is set, and the
    text input once the list outgrows a glance, because a list longer than a screen is
    typed into rather than scrolled. Typing filters the known
    ones, so a tag somebody already used is the easy thing to pick; a new one is added
    on Enter, and `add-unique` is what makes a repeat impossible.

    Case is not folded. Two spellings are two tags until somebody merges them, and
    folding here would hide the duplicate instead of letting it be found.
    """
    def draw() -> None:
        with ui.column().classes("gap-1 w-full min-w-0"):
            if derived:
                _derived_chips(derived, library.tag_looks())
            control = tag_chips.Picker(library.tags(), value=held,
                                       looks=library.tag_looks(), adds=True,
                                       look=dropdown.OUTLINED) \
                .props('hide-dropdown-icon popup-content-class="manager-picker-popup"') \
                .classes("w-full min-w-0")
        control.on_value_change(lambda: on_change(list(control.value or [])))

    return draw


def _derived_chips(tags: Sequence[str], looks: dict[str, dict[str, Any]]) -> None:
    with ui.element("span").classes(tag_chips.BOX):
        for tag in tags:
            sources = (looks.get(tag) or {}).get("sources") or [{}]
            panel.tag_link(
                tag, tag_chips.color_of(tag, looks), mark=ext_lists.ICON,
                to=ext_lists.address(str(sources[0].get("extension") or ""),
                                     str(sources[0].get("list") or "")),
                hint=t("manager.tags.from_source",
                       source=", ".join(tag_source(one) for one in sources if one)))()


IPDB_URL = "https://www.ipdb.org/machine.cgi?id={id}"



def _details_differ(context: dict[str, Any],
                    differs: list[dict[str, Any]]) -> Callable[[], None]:
    """The game's details against the entry's, where they have come apart.

    Adopting is one act over all of them rather than a choice per field. They are one
    machine's facts, and taking this one's year beside that one's maker would describe
    no machine at all.
    """
    async def adopt() -> None:
        await _write(context, context["library"].adopt_vps_details,
                     context["game_id"])

    def draw() -> None:
        # The block lays its children out in a row, so the stack goes in a column of
        # its own - the same shape the fault list beside an icon uses.
        with ui.element("div").classes("manager-attention w-full"), \
                ui.column().classes("gap-1 min-w-0 grow"):
            ui.label(t("manager.workbench.spreadsheet_differs")) \
                .classes("manager-attention-line")
            for item in differs:
                said = str(item.get("field") or "")
                with ui.row().classes("items-baseline gap-2 w-full no-wrap"):
                    ui.label(DETAIL_WORDS.get(said, said)).classes("manager-diff-field")
                    ui.label(str(item.get("ours") or "-")).classes("manager-diff-was")
                    ui.icon("arrow_forward").classes("manager-diff-arrow")
                    ui.label(str(item.get("theirs") or "-")).classes("manager-help truncate")
            ui.button(t("manager.workbench.use_spreadsheet"),
                icon=verbs.ACCEPT, on_click=adopt) \
                .props("flat dense no-caps size=sm").classes("manager-action")

    return draw



def _rom_state(pinmame: dict[str, Any], rom: str,
               context: dict[str, Any] | None = None) -> Any:
    """The rom, and whether it is actually there.

    The chain resolves aliases and audits the install, so the name alone is half an
    answer: what anybody wants of a rom is whether it will run.

    A missing one carries the way *to* the fix rather than the fix: Launch reports and
    Assets manages, so a finding jumps to where the act lives.
    """
    installed = pinmame.get("installed")
    if not pinmame.get("effective"):
        return rom
    if installed is None:
        unknown = next(one for one in game_states.rom().states
                       if one.token == game_states.UNKNOWN)
        return _state(unknown.word, "unknown", beside=rom, hint=unknown.line())
    chip = _state(t("word.installed") if installed
            else t("manager.workbench.not_installed"),
                  "on" if installed else "warn", beside=rom)
    if installed or context is None:
        return chip

    def draw() -> None:
        chip()
        ui.button(t("manager.workbench.assets"), icon=verbs.DRILL,
                  on_click=lambda: _choose(context, "assets")) \
            .props("flat dense no-caps size=sm") \
            .classes("manager-action manager-action--inline") \
            .tooltip(t("manager.workbench.rom_tip"))

    return draw


def _rom_row(context: dict[str, Any], pinmame: dict[str, Any]) -> Any:
    rom = str(pinmame.get("effective") or pinmame.get("declared") or "-")
    chip = _rom_state(pinmame, rom)
    if pinmame.get("installed") is not False:
        return chip
    return _with_add(context, "rom", chip)


def _with_add(context: dict[str, Any], kind: str, chip: Callable[[], Any]) -> Any:
    def draw() -> None:
        with ui.element("div").classes("manager-fact-edit"):
            chip()
            _add_action(context, kind)

    return draw


def _add_action(context: dict[str, Any], kind: str) -> None:
    opens = _add_opener(context, kind)
    if opens is not None:
        panel.action(t("word.add"), opens, icon=verbs.ADD, inline=True,
                     hint=t("manager.workbench.add_missing.help"))()


def _add_opener(context: dict[str, Any], kind: str) -> Callable[[], Any] | None:
    label = _asset_name(kind)
    done = context["rebuild"]
    if kind == "readme":
        return partial(mediasource.open_notes_sources, context, label, done)
    if kind in file_acts.WHOLE_FOLDER or kind == "rom":
        return partial(mediasource.open_folder_sources, context, kind, label, done)
    if kind in file_acts.PLACEABLE:
        return partial(mediasource.open_asset_sources, context, kind, label, done)
    return None


async def _findings(context: dict[str, Any],
                    tables: Sequence[dict[str, Any]]) -> list[tuple[Any, Any]]:
    """What is wrong with these tables, before anything that is merely true about them:
    one alert per finding, ending in the act that fixes it where the panel has one.

    Only real faults. Hidden and not-default are choices somebody made, not problems,
    and listing them here would teach the block to be ignored.
    """
    releases: dict[str, dict[str, Any]] = {}
    entry_url = unread = ""
    updates = newer = any(table.get("update_available") for table in tables)
    if newer:
        try:
            updates = await offload.io(context["library"].updates_shown)
        except Exception:  # noqa: BLE001 - a notice shown beats the real faults lost
            logger.warning("manager: could not read Show Updates", exc_info=True)
    aside: dict[str, dict[str, Any]] = {}
    if newer and updates:
        try:
            await offload.io(context["library"].load_updates, str(context["game_id"]))
        except Exception:  # noqa: BLE001 - the strip shows where the marks cannot be read
            logger.warning("manager: could not read the skipped updates", exc_info=True)
        else:
            aside = {str(move["release_id"]): move
                     for move in context["library"].set_aside_moves(str(context["game_id"]))}
        vps_id = str(context["game"].get("vps_id") or "")
        listed, unread = await _read_releases(context, vps_id)
        releases = {str(one.get("vps_file_id") or ""): one for one in listed}
        if vps_id and not unread:
            try:
                entry = await offload.io(context["library"].vps_entry, vps_id)
            except Exception as exc:
                logger.warning("manager: could not read VPS for %s", vps_id, exc_info=True)
                unread = why(exc)
            else:
                entry_url = str((entry or {}).get("url") or "")
    everyone = context["tables"]
    causes: dict[tuple[str, ...], list[tuple[dict[str, Any], _Fault]]] = {}
    quiet: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for table in tables:
        move = aside.get(str((table.get("source") or {}).get("vps_file_id") or ""))
        if move and table.get("update_available"):
            quiet.append((table, move))
        for fault in _faults(context, table, releases, entry_url, unread, updates and not move):
            causes.setdefault(fault.cause, []).append((table, fault))
    entries: list[tuple[Any, Any]] = []
    for found in causes.values():
        members = [table for table, _ in found]
        first = found[0][1]
        line = t(first.words, count=len(members), **first.params)
        entries.append((FULL, _alert(line, first.act, *_said(members, everyone))))
    entries += [(FULL, _set_aside_line(context, table, move, everyone)) for table, move in quiet]
    return entries


def _set_aside_line(context: dict[str, Any], table: dict[str, Any], move: dict[str, Any],
                    everyone: Sequence[dict[str, Any]]) -> Callable[[], None]:
    """The quiet line a skipped or ignored update leaves where the strip would be, with the
    link that takes the mark back. It names the table where the game has several."""
    stop = discovery_marks.take_back(
        context, str(context["game"].get("name") or ""), t("word.table"), move,
        discovery_marks.recounting(context["state"], partial(_table_list_changed, context)))

    def draw() -> None:
        with ui.row().classes("items-center gap-x-1 no-wrap w-full min-w-0 manager-help"):
            if len(everyone) > 1:
                ui.label(game_tables.name_among(table, list(everyone))).classes("truncate min-w-0")
                ui.label("·").classes("flex-none")
            ui.label(t("manager.discovery.update.ignored")
                     if move["set_aside"] == vps_updates.IGNORED
                     else t("manager.discovery.update.skipped", version=move["now"])
                     ).classes("flex-none")
            ui.label("·").classes("flex-none")
            ui.label(t("manager.discovery.mark.stop")).classes("manager-link flex-none") \
                .props("role=button tabindex=0").on("click", stop).on("keydown.enter", stop)

    return draw


_NAMED_AT_MOST = 3


def _said(members: list[dict[str, Any]],
          everyone: list[dict[str, Any]]) -> tuple[list[str], tuple[str, str] | None]:
    """The lines under a notice: which of the game's tables it is about.

    Nothing when the game has the one table, the table's name when the notice is about
    one of several, and when it is about several of them their names, the first three
    and the rest behind the mark, or *All 3 tables* when it is about every one.
    """
    if len(everyone) < 2:
        return [], None
    if len(members) == 1:
        return [game_tables.name_among(members[0], everyone)], None
    if len(members) == len(everyone):
        return [t("manager.workbench.cause_both") if len(members) == 2
                else t("manager.workbench.cause_all", count=len(members))], None
    names = [game_tables.name_among(one, everyone) for one in members]
    rest = names[_NAMED_AT_MOST:]
    return names[:_NAMED_AT_MOST], (
        (t("said.and_more", count=len(rest)), "\n".join(rest)) if rest else None)


class _Fault(NamedTuple):
    """One thing wrong with one table.

    `cause` is what two tables share when their notice would say the same words and be
    fixed by the same press: the words' key and what fills them, or the table itself where
    the press acts on that table's file alone. `words` is asked for with the count of
    tables the notice ends up covering, so a shared one reads in the plural.
    """
    cause: tuple[str, ...]
    words: str
    params: dict[str, str]
    act: Callable[[], None] | None


def _faults(context: dict[str, Any], table: dict[str, Any],
            releases: dict[str, dict[str, Any]], entry_url: str,
            unread: str = "", updates: bool = True) -> list[_Fault]:
    pinmame = (table.get("dependencies") or {}).get("pinmame") or {}
    own = str(table.get("id") or "")
    faults: list[_Fault] = []
    if not table.get("available"):
        # Three ways a thing can be unavailable and they are not the same fault. A file
        # that is gone is a deletion; a file somewhere else is a location that is away,
        # and nothing here is lost; something with no file at all has nothing to play
        # it. The same sentence for all three would send somebody to the wrong fix.
        if game_tables.is_referenced(table):
            words = "manager.workbench.place_unreachable"
            faults.append(_Fault((words,), words, {}, None))
        elif game_tables.is_keyed(table):
            words = "manager.workbench.unplayable"
            faults.append(_Fault((words,), words, {}, None))
        else:
            # Forget decides about this file and no other, so each file is its own cause.
            words = "manager.workbench.file_absent"
            faults.append(_Fault(
                (words, own), words, {},
                panel.action(t("word.forget"), partial(_forget_table, context, table),
                             icon=verbs.FORGET,
                             hint=t("manager.workbench.forget_table.help"))))
    base = (table.get("source") or {}).get("base") or {}
    if table.get("available") and base.get("file") and not base.get("available"):
        words = "manager.workbench.source_gone"
        faults.append(_Fault((words, str(base["file"])), words, {}, None))
    if pinmame.get("effective") and pinmame.get("installed") is False:
        # The audit reads the game's folder, so every table naming this set gets the one
        # answer, and the game's one Add fixes them all.
        opens = _add_opener(context, "rom")
        words = "manager.workbench.rom_missing"
        rom = str(pinmame["effective"])
        faults.append(_Fault(
            (words, rom), words, {"rom": rom},
            panel.action(t("word.add"), opens, icon=verbs.ADD,
                         hint=t("manager.workbench.add_missing.help")) if opens else None))
    if updates and table.get("update_available"):
        source = table.get("source") or {}
        theirs = str(source.get("version") or "")
        release = releases.get(str(source.get("vps_file_id") or "")) or {}
        to = str(release.get("url") or "") or entry_url
        if to and game_tables.can_swap(table):
            act: Callable[[], None] | None = panel.action(
                t("manager.workbench.update"),
                partial(_update_table, context, table, to, theirs, release),
                icon=verbs.REPLACE, hint=t("manager.workbench.update.help", version=theirs))
        else:
            act = (panel.link_out(t("manager.workbench.get_version", version=theirs), to=to)
                   if to else _state(t("manager.workbench.vps_failed"), "unknown",
                                     hint=unread) if unread else None)
        words = "manager.workbench.newer_on_vps"
        faults.append(_Fault((words, own), words, {
            "theirs": theirs, "ours": str(source.get("vps_version") or "")}, act))
    return faults


def _update_table(context: dict[str, Any], table: dict[str, Any], page: str, version: str,
                  release: dict[str, Any]) -> None:
    """Open the newer version's page and take the file for it, to replace this table."""
    vps_file_id = str((table.get("source") or {}).get("vps_file_id") or "")
    told = vps_records.told(release) or version
    getting_file.open_for(
        context["library"], game_id=context["game_id"],
        game_dir=str(context["game"].get("folder") or ""), table_id=str(table.get("id") or ""),
        page=page, site=sites.site_name(page),
        title=t("manager.getting_file.update_to", version=version),
        release=getting_file.release_of(vps_file_id, told),
        on_done=partial(_table_list_changed, context))


def _alert(line: str, act: Callable[[], None] | None, said: Sequence[str] = (),
           more: tuple[str, str] | None = None) -> Callable[[], None]:
    """One finding, and the tables it is about where the panel holds more than one.

    `more` is the line for the names that did not fit, with them behind its mark.
    """
    def draw() -> None:
        with ui.element("div").classes("manager-attention"):
            ui.icon("error_outline").classes("manager-attention-icon")
            with ui.column().classes("gap-0 min-w-0 grow"):
                ui.label(line).classes("manager-attention-line")
                for name in said:
                    ui.label(name).classes("manager-member-table truncate max-w-full") \
                        .tooltip(name)
                if more is not None:
                    panel.line(more[0], hint=more[1], classes="manager-member-table")
            if act is not None:
                act()

    return draw


def _hidden_row(context: dict[str, Any], table: dict[str, Any]) -> tuple[Any, Any]:
    """Whether the frontend offers this table."""
    table_id = str(table.get("id") or "")

    tip: list[ui.tooltip] = []

    def hold(control: ui.switch) -> None:
        with control:
            tip.append(ui.tooltip(game_tables.hidden_hint(table, context["tables"])))

    @on_page
    async def hide(event: Any) -> None:
        hidden = bool(event.value)
        try:
            answer = await run.io_bound(context["library"].set_table_hidden,
                                        context["game_id"], table_id, hidden)
        except Exception as exc:
            ui.notify(t("manager.workbench.save_failed"), caption=why(exc), type="negative")
            return
        ui.notify(game_tables.hidden_said(str(context["game"].get("name") or ""), table,
                                          answer, hidden=hidden), type="positive")
        await _reread_tables(context)
        await context["saved"]()
        for one in tip:
            one.set_text(game_tables.hidden_hint(table, context["tables"]))
        if "tables_list" in context:
            context["tables_list"].refill()

    # On is hidden, the direction the grid column and its filter read the same flag.
    return (t("word.hidden"),
            panel.switch(bool(table.get("hidden")), hide, on_control=hold))


async def _reread_tables(context: dict[str, Any]) -> None:
    """The game's tables as they are now, written into the rows the panel holds, so a
    section that reads one of them sees the change without a rebuild."""
    fresh = {str(one.get("id") or ""): one
             for one in await offload.io(context["library"].tables_for, context["game_id"])}
    for held in context["tables"]:
        new = fresh.get(str(held.get("id") or ""))
        if new is not None and new is not held:
            held.clear()
            held.update(new)


@on_page
async def _script_act(context: dict[str, Any], call: Any, table_id: str,
                      done: str, failed: str) -> None:
    """Run one script act and redraw. The panel is showing which script runs, so it is
    exactly what the act changes."""
    try:
        await run.io_bound(call, context["game_id"], table_id)
    except Exception as exc:
        ui.notify(failed, caption=why(exc), type="negative")
        return
    ui.notify(done, type="positive")
    await context["rebuild"]()


async def _extract_script(context: dict[str, Any], table: dict[str, Any]) -> None:
    """Not confirmed: it writes a new file and takes nothing away, and the way back is
    the Delete beside it."""
    await _script_act(context, context["library"].extract_script,
                      table.get("id") or "", t("manager.workbench.extract_done", count=1),
                      t("manager.workbench.extract_failed"))


@on_page
async def _drop_script(context: dict[str, Any], table: dict[str, Any]) -> None:
    """Confirmed: whatever the sidecar held goes with it, and a patched table quietly
    becomes an unpatched one."""
    every = game_tables.shares_its_script(table)
    if not await confirm.ask(
            t("manager.workbench.delete_question", count=1),
            detail=t("manager.game_tables.script_detail" if every
                     else "manager.workbench.delete_detail"),
            lines=[f"{Path(table.get('filename') or '').stem}.vbs"]):
        return
    await _script_act(context, context["library"].delete_script,
                      table.get("id") or "",
                      t("manager.game_tables.script_deleted" if every
                        else "manager.workbench.delete_done", count=1),
                      t("manager.workbench.delete_failed"))


@on_page
async def _forget_table(context: dict[str, Any], table: dict[str, Any]) -> None:
    """Drop a gone table's record, once the user says it is not coming back.

    Confirmed because it is the only destructive thing on this surface, and the dialog
    names the file rather than the id - the id is ours, the filename is what the user
    recognizes. The API refuses the request outright if the .vpx is back, so a stale
    panel cannot delete a table that returned while it was open.
    """
    detail = ("manager.workbench.forget_keyed" if game_tables.is_keyed(table)
              else "manager.workbench.forget_reference" if game_tables.is_referenced(table)
              else "manager.workbench.forget_missing")
    made = game_tables.made_from_it_line(
        [game_tables.name_among(one, context["tables"])
         for one in game_tables.made_from_it(table, context["tables"])])
    if not await confirm.ask(
            t("manager.workbench.forget_table", count=1), detail=t(detail),
            lines=[game_tables.name_among(table, context["tables"]), *([made] if made else [])],
            confirm=t("word.forget"), icon=verbs.FORGET):
        return
    try:
        await run.io_bound(context["library"].forget_table,
                           context["game_id"], table.get("id") or "")
    except Exception as exc:
        ui.notify(t("manager.workbench.forget_failed"), caption=why(exc), type="negative")
        return
    ui.notify(t("manager.workbench.table_forgotten", count=1), type="positive")
    await _table_gone(context, table)


async def _table_gone(context: dict[str, Any], table: dict[str, Any]) -> None:
    """Put the panel and the grid right once `table` is no longer the game's."""
    if table.get("id") and table.get("id") == context.get("lens"):
        left = await offload.io(context["library"].tables_for, context["game_id"])
        after = next((str(one.get("id") or "") for one in left if one.get("default")), "")
        if await _open_in_tables(context, after):
            return
    await _table_list_changed(context)


# How long a table that has just been added is marked as new in its game's block.
_ARRIVED_S = 5.0


def _add_table(context: dict[str, Any]) -> None:
    mediasource.open_table_sources(context, _after_an_add(context))


def _after_an_add(context: dict[str, Any]) -> Callable[[], Awaitable[None]]:
    """What follows a table joining this game, remembering what it held before so what
    arrived can be told from what was there."""
    held = {str(one.get("id") or "") for one in context["tables"]}
    was = next((one for one in context["tables"] if one.get("default")), None)
    return partial(_table_added, context, held, was)


async def after_a_row_drop(library: Library, state: dict[str, Any], game_id: str,
                           redraw: Callable[[], None]) -> Callable[[], Awaitable[None]]:
    """What follows a table dropped on a game's row: what follows one dropped on its
    panel, with the page put right in place of the panel where it shows another game."""
    game = next((one for one in library.games if str(one.get("id")) == game_id), {})
    tables = await offload.io(library.tables_for, game_id)

    async def put_right() -> None:
        shown = state.get("panel_rebuild")
        if state.get("game") == game_id and callable(shown):
            await shown()
            return
        refresh = state.get("refresh_game")
        if state.get("view") in ("games", "tables") and callable(refresh):
            await refresh(game_id)
            return
        redraw()

    context = {"library": library, "game": game, "game_id": game_id, "tables": tables,
               "state": state, "rebuild": put_right}
    return _after_an_add(context)


@on_page
async def _table_added(context: dict[str, Any], held: set[str],
                       was: dict[str, Any] | None) -> None:
    try:
        tables = await offload.io(context["library"].tables_for, context["game_id"])
    except Exception:  # noqa: BLE001 - the redraw below reads it again and says why
        tables = []
    new = next((one for one in tables if str(one.get("id") or "") not in held), None)
    if new is None:
        await _table_list_changed(context)
        return
    context["state"]["arrived_table"] = (str(new.get("id") or ""), time.monotonic())
    _say_added(context, new, was, tables)
    if not await _open_in_tables(context, str(new.get("id") or "")):
        await _table_list_changed(context)


def _say_added(context: dict[str, Any], new: dict[str, Any], was: dict[str, Any] | None,
               tables: list[dict[str, Any]]) -> None:
    """Name what arrived, and where the automatic default moved to it, offer the one it
    moved from back, locked."""
    name = game_tables.name_among(new, tables)
    game = str(context["game"].get("name") or "")
    plays = next((one for one in tables if one.get("default")), None)
    if was is None or plays is None or plays.get("id") != new.get("id"):
        ui.notify(t("manager.game_tables.added", table=name, game=game), type="positive")
        return
    state, game_id, library = context["state"], context["game_id"], context["library"]
    view = state.get("view")
    kept = game_tables.name_among(was, tables)

    async def keep() -> None:
        await offload.io(library.set_default_table, game_id, str(was.get("id") or ""))
        redraw = state.get("panel_rebuild")
        if state.get("game") == game_id and state.get("view") == view and callable(redraw):
            await redraw()
            return
        refresh = state.get("refresh_game")
        if callable(refresh):
            await refresh(game_id)

    undo.offer(t("manager.game_tables.result_added", table=name, game=game), keep,
               label=t("manager.game_tables.keep", table=kept),
               done=t("manager.game_tables.locked_to", table=kept))


async def _open_in_tables(context: dict[str, Any], table_id: str) -> bool:
    """Under Tables, put the grid right and open `table_id` there, or nothing where it
    is "". False under any other view, which a rebuild answers instead."""
    state = context["state"]
    opens = state.get("open_table")
    if state.get("view") != "tables" or not callable(opens):
        return False
    refresh = state.get("refresh_game")
    if callable(refresh):
        await refresh(context["game_id"])
    await opens(table_id)
    return True


async def _table_list_changed(context: dict[str, Any]) -> None:
    """Redraw the panel, and put the grid behind it right too.

    A name at the call site; `rebuild` does both.
    """
    await context["rebuild"]()


@on_page
async def _contain_table(context: dict[str, Any], table: dict[str, Any]) -> None:
    """Copy a referenced file into the game folder, so the entry stops depending on
    somewhere else being there.

    Confirmed because it writes a file and because what it costs - the disk - is the
    thing somebody is choosing to spend. Named for what it is *for*, not for the
    mechanism: nobody wants "convert to contained", they want the table to keep working
    when the share is away.
    """
    reference = table.get("reference") or {}
    if not await confirm.ask(
            t("manager.workbench.copy_question"),
            detail=t("manager.workbench.copy_detail"),
            lines=[reference.get("resolved") or reference.get("path") or ""],
            confirm=t("manager.workbench.copy"), icon=verbs.COPY):
        return
    try:
        await run.io_bound(context["library"].contain_table,
                           context["game_id"], table.get("id") or "")
    except Exception as exc:  # noqa: BLE001
        ui.notify(t("manager.workbench.copy_failed"), caption=why(exc), type="negative")
        return
    ui.notify(t("manager.workbench.copied"), type="positive")
    await _table_list_changed(context)


def _tables_block(context: dict[str, Any]) -> None:
    """This game's tables, and which one it plays.

    A block inside Game Details rather than a rail entry of its own: most games hold one
    table, so "Tables (1)" would be a place you went to read a single row, and the rail
    holds places. A row is the table's name, the file under it, Missing or Hidden where
    that is so, and the way to Table Details, where the match and every act on the table
    are. The radios and Automatic show only where there is a choice.
    """
    tables = context["tables"]
    showing = str(context.get("lens") or "")
    several = len(tables) > 1
    game = str(context["game"].get("name") or "")
    said = t("manager.workbench.tables_2", count=(len(tables))) if several \
        else t("manager.workbench.table_3")
    with ui.row().classes("items-center gap-2 w-full no-wrap manager-heading-row"):
        ui.label(said if tables else t("manager.workbench.tables")) \
            .classes("manager-card-title manager-fact-heading grow")
    if not tables:
        ui.label(t("manager.workbench.no_tables")).classes("manager-help")
        ui.label(t("manager.workbench.no_tables_drop")).classes("manager-help manager-drop-only")
    if several:
        ui.label(t("manager.workbench.tables_default_help")).classes("manager-help")
        game_default.fell_note(tables)
        game_default.automatic_choice(context, tables)
    arrived, at = context["state"].get("arrived_table") or ("", 0.0)
    if time.monotonic() - at > _ARRIVED_S:
        arrived = ""
    for table in tables:
        since = str(table.get("absent_since") or "")
        here = str(table.get("id") or "") == showing
        with ui.row().classes("items-start gap-2 w-full no-wrap manager-member-row") as row:
            if arrived and table.get("id") == arrived:
                row.classes(add="manager-member-row--new")
            if several:
                game_default.table_choice(context, table)
            with ui.column().classes("gap-0 grow min-w-0"):
                with ui.row().classes("items-center gap-2 w-full no-wrap"):
                    name = _said_name(table, game_tables.table_name(table)) \
                        .classes("manager-member-name min-w-0")
                    if here:
                        name.classes(add="manager-member-name--here")
                    if since:
                        name.classes(add="opacity-60")
                    if several and table.get("default"):
                        ui.label(t("manager.workbench.default_word")) \
                            .classes("manager-cell-quiet") \
                            .tooltip(t("manager.workbench.default_word.help"))
                if line := game_tables.file_line(table, game):
                    _said_file(line, str(table.get("filename") or ""))
            # Only what explains what the frontend does with the table. How it was
            # matched and the launcher are in Table Details.
            if since:
                ui.label(game_tables.word_for(game_tables.FILE_WORDS, True)) \
                    .classes("manager-member-chip manager-tier manager-tier--warn") \
                    .tooltip(t("manager.workbench.absent_since", value=when.local(since)))
            if table.get("hidden"):
                ui.label(game_tables.HIDDEN_WORDS[0]) \
                    .classes("manager-member-chip manager-chip-warn") \
                    .tooltip(game_tables.hidden_hint(table, tables))
            with ui.element("div").classes("manager-row-action"):
                if game_tables.is_referenced(table):
                    ui.button(icon=verbs.CONTAIN,
                              on_click=lambda _, t=table: _contain_table(context, t)) \
                        .props("flat dense round size=sm") \
                        .tooltip(t("manager.workbench.copy_game"))
                # An entry the folder does not hold can always be forgotten: nothing
                # on disk here will mint it again, which is the opposite of a table
                # that is there - dropping that record only means the next scan
                # re-adds it.
                if game_tables.can_forget(table):
                    ui.button(icon=verbs.FORGET,
                              on_click=lambda _, t=table: _forget_table(context, t)) \
                        .props("flat dense round size=sm color=warning") \
                        .tooltip(t("word.forget"))
            with panel.link_tile(to=_table_address(context, str(table.get("id") or "")),
                                 classes="manager-member-go") as go:
                ui.icon(verbs.DRILL)
            go.tooltip(t("manager.workbench.table_details"))
    # Drawn even where the list is empty: a folder with nothing in it yet is exactly
    # where one of these is added.
    with ui.row().classes("items-center gap-2 w-full manager-slot-actions"):
        panel.action(t("manager.workbench.add_table"), lambda: _add_table(context),
                     icon=verbs.ADD, hint=t("manager.workbench.add_table.help"))()
