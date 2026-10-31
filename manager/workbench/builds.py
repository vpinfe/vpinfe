"""The build_* entry points, each drawing one kind of subject into the panel."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Sequence
from typing import Any

from nicegui import run, ui

from common.failures import why
from common.games.collection_store import BUILTIN_ALL
from common.i18n import t
from manager.backups import backups as backups_page
from manager.devices import devices as devices_page
from manager.extensions import ext_row
from manager.frontend import players as players_page
from manager.frontend import themes as themes_page
from manager.kit import game_tables, offload, panel, verbs
from manager.kit.data import Library
from manager.kit.on_page import on_page
from manager.library import collection_adds, collection_rules, discovery_get, discovery_kinds
from manager.library import discovery as discovery_page
from manager.workbench import discovery_rail, leftovers
from manager.workbench.all_settings import _playing
from manager.workbench.collection import _note_unsaved, unsaved
from manager.workbench.core import Section, _blank, _pane_build, _rebuilds, _table_line, _title
from manager.workbench.file import (
    _asset_detail_of,
    _file_settings,
    _file_title,
    _same_file,
    _served,
)
from manager.workbench.game_table import _collections_held
from manager.workbench.launcher import _launchers_for_panel
from manager.workbench.rail import _rail, sections_for

logger = logging.getLogger("vpinfe.manager.workbench.builds")


async def build(container: ui.column, title: ui.column, library: Library,
                game_id: str | None, state: dict[str, Any] | None = None,
                table_id: str = "") -> None:
    """Fill the panel, and the name that lives up in the panel's header row.

    `table_id` is the subject when the Tables lens is what selected it. Empty means the
    game itself, which is what the Games lens selects.
    """
    state = state if state is not None else {}
    await _pane_build(container, state,
                      lambda: _draw(container, title, library, game_id, state, table_id))


async def _draw(container: ui.column, title: ui.column, library: Library,
                game_id: str | None, state: dict[str, Any], table_id: str = "") -> None:
    if game_id is None:
        _blank(container, title, t("manager.page.game"),
               t("manager.page.select_game"))
        return
    game = next((entry for entry in library.games if entry["id"] == game_id), None)
    if game is None:
        _blank(container, title, t("manager.page.game"),
               t("manager.page.not_library"))
        return
    # Off the loop, always. This is an HTTP call to our own process: made on the
    # event loop it blocks the server from answering it, the request times out
    # after 15s, and the browser reports the socket as lost rather than slow.
    #
    # And read *before* clearing. Clearing first leaves the panel empty for the whole
    # round trip, which after a write reads as the panel flashing black - the write,
    # the reread and the redraw are one act to the person who asked for it.
    tables = await offload.io(library.tables_for, game_id)
    # Off the loop and once, beside the tables: every table row offers the same list, so
    # asking per row would be one blocking HTTP call per table - and the Manager consumes
    # its own process, so a blocking call here does not just cost time, it deadlocks.
    launcher_listing = await offload.io(_launchers_for_panel, library)
    await offload.io(library.media_for, game_id, None)
    # The header menu's collections, read here for the same reason as the tables.
    collections = await collection_adds.read(library, str(state.get("collection") or ""))
    in_collections = await _collections_held(library, game_id)

    container.clear()
    title.clear()
    with container:
        # Named under the game once a file is the subject, so the header says which of
        # the four you are looking at without a control to read.
        chosen = next((t for t in tables if t.get("id") == table_id), None)

        def retitle() -> None:
            """The header again, from the game as it is now: a name override changes it."""
            title.clear()
            current = context["game"]
            _title(title, current.get("name") or "", game_tables.made(current))
            if chosen is not None:
                with title:
                    ui.label(_table_line(chosen, tables)).classes("manager-workbench-table") \
                        .tooltip(str(chosen.get("filename") or ""))

        # `lens` is a table id or "" for the folder's shared files, and it resets per
        # game because a table id means nothing to the next one. `redraws` is how the
        # lens reaches the sections that follow it without rebuilding the panel.
        # The picked slot lives with the client, not this build of the panel: a mode
        # change rebuilds and would otherwise forget it. Kept across games too, which
        # is what sweeping one kind down a list needs.
        state.setdefault("slot", {"kind": None})
        # The lens is the grid's selection, not a control in here. Under Games that is
        # the game and the panel answers for shared files; under Tables it is one file
        # and the panel answers for that.
        context = {"library": library, "game": game, "game_id": game_id,
                   "tables": tables, "launchers": launcher_listing, "state": state,
                   "collections": collections, "in_collections": in_collections,
                   "lens": table_id, "redraws": [], "slot": state["slot"]}

        # The subject has to survive a rebuild. Left off, every rail click fell
        # back to the game and the table's own sections vanished under the cursor.
        context["retitle"] = retitle
        retitle()
        context["rebuild"] = _rebuilds(
            context, f"{game_id}:{table_id}",
            lambda: build(container, title, library, game_id, state, table_id))
        # The panel showing now, for a message that outlives the one that raised it.
        state["panel_rebuild"] = context["rebuild"]
        await _rail(context, "table" if table_id else "game", state)


async def build_collection(container: ui.column, title: ui.column, library: Library,
                           name: str | None, state: dict[str, Any] | None = None) -> None:
    """The panel, for a collection rather than a game.

    Its own entry point rather than a branch inside `build`: a collection has no game,
    no tables and no media lens, so everything that build assembles for those would be
    a chain of empty values threaded through to sections that never read them.
    """
    state = state if state is not None else {}
    await _pane_build(container, state,
                      lambda: _draw_collection(container, title, library, name, state))


async def build_all_games(container: ui.column, title: ui.column, library: Library,
                          state: dict[str, Any] | None = None) -> None:
    """The panel for the whole library: not a collection, so it carries the one setting
    that is about it and nothing a collection edits."""
    state = state if state is not None else {}
    await _pane_build(container, state,
                      lambda: _draw_all_games(container, title, library, state))


async def build_selection(container: ui.column, title: ui.column, library: Library,
                          table_ids: Sequence[str], state: dict[str, Any] | None = None,
                          ) -> None:
    """The panel, for the tables ticked in the Tables grid: one subject that answers for
    all of them."""
    state = state if state is not None else {}
    await _pane_build(container, state,
                      lambda: _draw_selection(container, title, library, table_ids, state))


async def _draw_selection(container: ui.column, title: ui.column, library: Library,
                          table_ids: Sequence[str], state: dict[str, Any]) -> None:
    wanted = set(table_ids)
    rows = await offload.io(library.load_tables)
    tables = [row for row in rows if row.get("id") in wanted]
    if not tables:
        _blank(container, title, t("manager.page.table"), t("manager.page.select_table"))
        return
    launcher_listing = await offload.io(_launchers_for_panel, library)
    container.clear()
    title.clear()
    with container:
        _title(title, t("manager.games.tables", count=len(tables)),
               t("manager.page.selection"))
        context: dict[str, Any] = {
            "library": library, "tables": tables, "launchers": launcher_listing,
            "state": state, "redraws": [], "dock": None,
            "game_ids": sorted({str(one.get("game_id") or "") for one in tables})}
        context["rebuild"] = _rebuilds(
            context, "selection",
            lambda: build_selection(container, title, library, table_ids, state),
            refresh_with="refresh_tables", refresh_of="game_ids", grid_first=True)
        # The open section is not a place to come back to: the table that is shown next
        # lands where the last one did.
        kept = state.get("section")
        await _rail(context, "selection", state)
        if kept is None:
            state.pop("section", None)
        else:
            state["section"] = kept


async def build_game_selection(container: ui.column, title: ui.column, library: Library,
                               game_ids: Sequence[str],
                               state: dict[str, Any] | None = None) -> None:
    """The panel, for the games ticked in the Games grid."""
    state = state if state is not None else {}
    await _pane_build(container, state,
                      lambda: _draw_game_selection(container, title, library, game_ids,
                                                   state))


async def _draw_game_selection(container: ui.column, title: ui.column, library: Library,
                               game_ids: Sequence[str], state: dict[str, Any]) -> None:
    wanted = set(game_ids)
    games = [game for game in library.games if game.get("id") in wanted]
    if not games:
        _blank(container, title, t("manager.page.game"), t("manager.page.select_game"))
        return
    container.clear()
    title.clear()
    with container:
        _title(title, t("manager.games.games", count=len(games)),
               t("manager.page.selection"))
        context: dict[str, Any] = {
            "library": library, "games": games, "state": state, "redraws": [],
            "dock": None, "game_ids": sorted(wanted)}
        context["rebuild"] = _rebuilds(
            context, "game_selection",
            lambda: build_game_selection(container, title, library, game_ids, state),
            refresh_with="refresh_games", refresh_of="game_ids", grid_first=True)
        kept = state.get("section")
        await _rail(context, "game_selection", state)
        if kept is None:
            state.pop("section", None)
        else:
            state["section"] = kept


async def build_tag(container: ui.column, title: ui.column, library: Library,
                    name: str | None, state: dict[str, Any] | None = None) -> None:
    """The panel, for one tag."""
    state = state if state is not None else {}
    await _pane_build(container, state,
                      lambda: _draw_tag(container, title, library, name, state))


async def _draw_tag(container: ui.column, title: ui.column, library: Library,
                    name: str | None, state: dict[str, Any]) -> None:
    if not name:
        _blank(container, title, t("manager.page.tag"), t("manager.page.select_tag"))
        return
    look = library.tag_looks().get(name)
    if look is None:
        _blank(container, title, t("manager.page.tag"), t("manager.page.gone"))
        return
    container.clear()
    title.clear()
    with container:
        count, tables = int(look.get("games") or 0), int(look.get("tables") or 0)
        _title(title, name, t("manager.tags.on_games", count=count) if count
               else t("manager.tags.on_tables", count=tables) if tables
               else t("manager.tags.on_no_games"))
        context: dict[str, Any] = {"library": library, "name": name, "tag": look,
                                   "state": state, "redraws": [], "dock": None}
        context["rebuild"] = _rebuilds(
            context, f"tag:{name}",
            lambda: build_tag(container, title, library, state.get("tag"), state))
        await _rail(context, "tag", state)


async def build_file(container: ui.column, title: ui.column, library: Library,
                     row: dict[str, Any] | None, state: dict[str, Any] | None = None,
                     family: str = "media") -> None:
    """The panel, for one media or asset file. `family` is `media` or `assets`."""
    state = state if state is not None else {}
    await _pane_build(container, state,
                      lambda: _draw_file(container, title, library, row, state, family))


async def _draw_file(container: ui.column, title: ui.column, library: Library,
                     row: dict[str, Any] | None, state: dict[str, Any],
                     family: str) -> None:
    media = family == "media"
    heading = t("manager.page.media" if media else "manager.page.assets")
    if not row:
        _blank(container, title, heading, t("manager.page.select_media" if media
                                             else "manager.page.select_asset"))
        return
    game_id = str(row.get("game_id") or "")
    game = next((one for one in library.games if one["id"] == game_id), None)
    if game is None:
        _blank(container, title, heading, t("manager.page.not_library"))
        return
    files = await offload.io(library.files_of, "media" if media else "asset", game_id)
    found = _same_file(files, row)
    if found is None:
        _blank(container, title, heading, t("manager.page.gone"))
        return
    tables = await offload.io(library.tables_for, game_id)
    table_id = str(found.get("table") or "")
    detail = {} if media else await _asset_detail_of(library, game_id, found)
    settings = None if media else await _file_settings(library, found, files, tables, detail)
    container.clear()
    title.clear()
    with container:
        _file_title(title, found, game, next((one for one in tables
                                              if one.get("id") == table_id), None))
        context: dict[str, Any] = {"library": library, "game": game, "game_id": game_id,
                                   "tables": tables, "state": state, "lens": table_id,
                                   "redraws": [], "dock": None, "row": found,
                                   "served": _served(found, files, tables),
                                   "kind": str(found.get("kind") or ""), "detail": detail,
                                   "slot": {"kind": found.get("kind")}}
        context["rebuild"] = _rebuilds(
            context, f"file:{found.get('id')}",
            lambda: build_file(container, title, library, found, state, family),
            refresh_with="refresh_files")
        if settings is not None:
            context["file_settings"] = {**settings, "state": state,
                                        "rebuild": context["rebuild"],
                                        "refresh_grid": context["saved"], "fits": {}}
        await _rail(context, "media_file" if media else "asset_file", state)


async def build_backup(container: ui.column, title: ui.column, library: Library,
                       backup_id: str | None,
                       state: dict[str, Any] | None = None) -> None:
    """The panel, for a copy VPinFE keeps rather than a game."""
    state = state if state is not None else {}
    await _pane_build(container, state,
                      lambda: _draw_backup(container, title, library, backup_id, state))


async def _draw_backup(container: ui.column, title: ui.column, library: Library,
                       backup_id: str | None, state: dict[str, Any]) -> None:
    if not backup_id:
        _blank(container, title, t("manager.page.backup"), t("manager.page.select_backup"))
        return
    # Read fresh: a part's `gone` and a note are answers about this moment.
    found = await offload.io(library.backups)
    held = backups_page.rows(list(found.get("copies") or []), parts=True)
    row = next((one for one in held if one["id"] == backup_id and not one["within_id"]), None)
    if row is None:
        _blank(container, title, t("manager.page.backup"), t("manager.page.not_found"))
        return

    container.clear()
    title.clear()
    with container:
        _title(title, backups_page.name_of(row), row["taken_text"])
        context: dict[str, Any] = {
            "library": library, "backup": row, "state": state, "redraws": [], "dock": None,
            "parts": [one for one in held if one["within_id"] == backup_id]}
        context["rebuild"] = _rebuilds(context, f"backup:{backup_id}",
                                       lambda: build_backup(container, title, library,
                                                            backup_id, state))
        await _rail(context, "backup", state)


async def build_extension_row(container: ui.column, title: ui.column, library: Library,
                              extension: dict[str, Any], declared: dict[str, Any],
                              ids: Sequence[str], state: dict[str, Any],
                              again: Callable[[], Any] | None = None) -> None:
    """The panel, for the row an extension's list has open, or the rows it has ticked. The
    extension draws nothing: it answers what the row holds and core draws that."""
    await _pane_build(container, state, lambda: _draw_extension_row(
        container, title, library, extension, declared, list(ids), state, again))


async def _draw_extension_row(container: ui.column, title: ui.column, library: Library,
                              extension: dict[str, Any], declared: dict[str, Any],
                              ids: list[str], state: dict[str, Any],
                              again: Callable[[], Any] | None,
                              found: dict[str, Any] | None = None,
                              built: int | None = None) -> None:
    # A redraw (after a save, a verb, a rail click) carries the build it belongs to and is
    # dropped once another pane has been built since: it would draw this row over the next.
    if built is not None and state.get("build_seq") != built:
        return
    built = state.get("build_seq", 0)
    if not ids:
        _blank(container, title, ext_row.heading(declared), ext_row.prompt())
        return
    if len(ids) > 1:
        _blank(container, title, t("manager.community.rows", count=len(ids)),
               t("manager.page.selection"))
        return
    row_id = ids[0]
    if found is None:
        try:
            found = await ext_row.read(extension, declared, row_id)
        except Exception as exc:  # noqa: BLE001 - the extension's words, or the API's
            _blank(container, title, ext_row.heading(declared), why(exc))
            return
    row = await offload.io(ext_row.row_of, extension, declared, row_id)
    container.clear()
    title.clear()
    with container:
        _title(title, str(found["title"]), str(found["under"]))
        if found.get("picture"):
            ui.image(found["picture"]).classes("manager-pane-picture")
        context: dict[str, Any] = {
            "library": library, "extension": extension, "declared": declared,
            "row": row, "state": state,
            "route": ext_row.route(extension, declared, row_id), "redraws": [], "dock": None}

        async def draw(answer: dict[str, Any] | None) -> None:
            await _draw_extension_row(container, title, library, extension, declared, ids,
                                      state, again, answer, built)

        async def verbed() -> None:
            if again is not None:
                await again()
            await draw(None)

        context["redraw"] = draw
        context["verbed"] = verbed
        context["again"] = again
        context["rail"] = ext_row.sections(context, found)
        context["rebuild"] = _rebuilds(context, f"extension:{row_id}", lambda: draw(None))
        if context["rail"]:
            await _rail(context, ext_row.SUBJECT, state)
        elif state.get("subject_menu") is not None:
            panel.subject_menu(state["subject_menu"], ext_row.verbs(context))


async def build_discovery(container: ui.column, title: ui.column, library: Library,
                          vps_id: str | None,
                          state: dict[str, Any] | None = None) -> None:
    """The panel, for a catalog entry rather than a game of the library's."""
    state = state if state is not None else {}
    await _pane_build(container, state,
                      lambda: _draw_discovery(container, title, library, vps_id, state))


async def _draw_discovery(container: ui.column, title: ui.column, library: Library,
                          vps_id: str | None, state: dict[str, Any]) -> None:
    if not vps_id:
        _blank(container, title, t("manager.page.entry"), t("manager.page.select_entry"))
        return
    if not library.has_catalog():
        await offload.io(library.load_catalog)
    row = discovery_page.row_of(library, vps_id)
    if row is None:
        _blank(container, title, t("manager.page.entry"), t("manager.page.not_found"))
        return

    container.clear()
    title.clear()
    with container:
        _title(title, row["name"], game_tables.made(row))
        context: dict[str, Any] = {
            "library": library, "discovery": row, "state": state, "redraws": [], "dock": None}
        context["rebuild"] = _rebuilds(context, f"discovery:{vps_id}",
                                       lambda: build_discovery(container, title, library,
                                                               vps_id, state))
        scene = await discovery_kinds.read(context)
        context["game_id"] = scene.game_id
        context["rail"] = discovery_rail.sections(scene)

        async def settle(row: dict[str, Any]) -> bool:
            """The entry read again and its marks and open section drawn again where they
            stand. False where the game or the rail changed, or no kind section is open,
            which only a full draw puts right."""
            before = context["scene"]
            context["discovery"] = row
            now = await discovery_kinds.read(context)
            kinds = [one.key for one in (*now.files, *now.media)]
            if (kinds != [one.key for one in (*before.files, *before.media)]
                    or (now.game_id, now.in_library, now.counted)
                    != (before.game_id, before.in_library, before.counted)
                    or not now.tray.painted(discovery_get.BODY)):
                return False
            now.tray.repaint(discovery_get.RAIL, discovery_get.BODY)
            return True

        state["settle_pane_discovery"] = settle
        await _rail(context, "discovery", state)


async def build_launcher(container: ui.column, title: ui.column, library: Library,
                         launcher_id: str | None,
                         state: dict[str, Any] | None = None) -> None:
    """The panel, for a launcher rather than a game."""
    state = state if state is not None else {}
    await _pane_build(container, state,
                      lambda: _draw_launcher(container, title, library, launcher_id, state))


async def _draw_launcher(container: ui.column, title: ui.column, library: Library,
                         launcher_id: str | None, state: dict[str, Any]) -> None:
    if not launcher_id:
        _blank(container, title, t("manager.page.launcher"),
               t("manager.page.select_launcher"))
        return
    # Read fresh rather than from the grid's copy: every control in here writes, and a
    # rebuild that redrew from a stale row would show the edit undoing itself.
    found = await offload.io(library.launchers)
    held = list(found.get("launchers") or [])
    row = next((one for one in held if one.get("launcher_id") == launcher_id), None)
    if row is None:
        _blank(container, title, t("manager.page.launcher"),
               t("manager.page.not_found"))
        return

    groups = await offload.io(library.launcher_config_groups, launcher_id)
    playing = await offload.io(_playing, library)

    container.clear()
    title.clear()
    with container:
        _title(title, row.get("display_name") or "", row.get("app_name") or "")
        context: dict[str, Any] = {
            "library": library, "launcher": row, "launchers": held,
            "config_groups": list(groups or []), "state": state, "playing": playing,
            "defaults": dict(found.get("defaults") or {}),
            "redraws": [], "dock": None,
        }

        def retitle(name: str) -> None:
            title.clear()
            _title(title, name, row.get("app_name") or "")

        context["retitle"] = retitle
        context["rebuild"] = _rebuilds(
            context, f"launcher:{launcher_id}",
            lambda: build_launcher(container, title, library, launcher_id, state))
        await leftovers.notice(context)
        await _rail(context, "launcher", state)


async def build_player(container: ui.column, title: ui.column, library: Library,
                       player_id: str | None, state: dict[str, Any]) -> None:
    """The panel, for a player."""
    await _pane_build(container, state,
                      lambda: _draw_player(container, title, library, player_id, state))


async def _draw_player(container: ui.column, title: ui.column, library: Library,
                       player_id: str | None, state: dict[str, Any]) -> None:
    if not player_id:
        _blank(container, title, t("manager.page.player"), t("manager.page.select_player"))
        return
    # Read fresh rather than from the grid's copy: who is up moves from a phone and from
    # the frontend as well as from here.
    roster = await offload.io(library.players)
    player = next((one for one in roster if one.get("id") == player_id), None)
    if player is None:
        _blank(container, title, t("manager.page.player"), t("manager.page.not_found"))
        return
    try:
        held = await offload.io(library.player_accounts, player_id)
    except Exception:  # noqa: BLE001 - the player is still worth drawing without them
        logger.warning("Could not read a player's accounts", exc_info=True)
        held = []

    container.clear()
    title.clear()
    with container:
        _title(title, players_page.shown_name(player), players_page.kind_word(player))
        fixed = sections_for("player")
        accounts = tuple(Section(key, label, build, subjects=frozenset({"player"}))
                         for key, label, build in map(players_page.account_section, held))
        context: dict[str, Any] = {
            "library": library, "player": player, "roster": roster, "state": state,
            "accounts": held,
            "rail": (*fixed[:1], *accounts, *fixed[1:]), "redraws": [], "dock": None,
        }

        def retitle(name: str) -> None:
            title.clear()
            _title(title, name, players_page.kind_word(player))

        context["retitle"] = retitle

        async def again() -> None:
            """The grid first, so its row menus hold the accounts as they are now."""
            await players_page.changed(context)
            await build_player(container, title, library, player_id, state)

        context["rebuild"] = _rebuilds(context, f"player:{player_id}", again)
        await _rail(context, "player", state)


async def build_device(container: ui.column, title: ui.column, library: Library,
                       device: dict[str, Any] | None, state: dict[str, Any],
                       local_device_id: str | None = None,
                       device_capabilities: list[str] | None = None,
                       local_capabilities: set[str] | None = None) -> None:
    """The panel, for a device rather than a game.

    Its own entry point for the same reason a collection has one: a device has no game,
    no tables and no media lens, so everything `build` assembles would be a chain of
    empty values threaded through to sections that never read them.
    """
    await _pane_build(container, state, lambda: _draw_device(
        container, title, library, device, state, local_device_id,
        device_capabilities or [], local_capabilities or set()))


async def _draw_device(container: ui.column, title: ui.column, library: Library,
                       device: dict[str, Any] | None, state: dict[str, Any],
                       local_device_id: str | None,
                       device_capabilities: list[str],
                       local_capabilities: set[str]) -> None:
    if not device:
        _blank(container, title, t("manager.page.device"),
               t("manager.page.select_device"))
        return

    container.clear()

    def retitle() -> None:
        """The header again, from the device as it is now: a rename changes it."""
        title.clear()
        _title(title, devices_page.device_label(device),
               devices_page.KIND_LABELS.get(str(device.get("kind") or "vpinfe"),
                                            "VPinFE"))

    retitle()
    with container:
        reach = (state.get("device_reach") or {}).get(str(device.get("device_id") or ""))
        context: dict[str, Any] = {
            "library": library, "device": device, "state": state,
            "local_device_id": local_device_id,
            "device_capabilities": device_capabilities,
            "local_capabilities": local_capabilities,
            "reach": reach, "redraws": [], "dock": None, "retitle": retitle,
            "actions": (state.get("device_actions") or {}).get(
                str(device.get("device_id") or "")),
        }
        if context["actions"] is None:
            context["actions"] = await devices_page.load_actions(
                library, device, local_device_id, reach)
            state.setdefault("device_actions", {})[
                str(device.get("device_id") or "")] = context["actions"]

        context["rebuild"] = _rebuilds(
            context, f"device:{local_device_id}",
            lambda: build_device(container, title, library, device, state,
                                 local_device_id, device_capabilities,
                                 local_capabilities))
        await _rail(context, "device", state)


async def build_theme(container: ui.column, title: ui.column, library: Library,
                      key: str | None, state: dict[str, Any]) -> None:
    """The panel, for a frontend theme."""
    await _pane_build(container, state,
                      lambda: _draw_theme(container, title, library, key, state))


async def _draw_theme(container: ui.column, title: ui.column, library: Library,
                      key: str | None, state: dict[str, Any]) -> None:
    if not key:
        _blank(container, title, t("manager.page.theme"), t("manager.page.select_theme"))
        return
    found = await offload.io(library.themes, False)
    theme = next((one for one in found.get("themes") or [] if one.get("key") == key), None)
    if theme is None:
        _blank(container, title, t("manager.page.theme"),
               t("manager.page.gone"))
        return
    container.clear()
    title.clear()
    with container:
        _title(title, str(theme.get("name") or key),
               themes_page.STATUS.word(themes_page.status(theme)))
        context: dict[str, Any] = {"library": library, "theme": theme, "state": state,
                                   "redraws": [], "dock": None}
        context["rebuild"] = _rebuilds(
            context, f"theme:{key}",
            lambda: build_theme(container, title, library, key, state))
        await _rail(context, "theme", state)


async def _read_collection(library: Library, name: str,
                           state: dict[str, Any]) -> dict[str, Any] | None:
    """What the panel of the collection `name` is drawn from, or None where it is gone.

    Read fresh rather than from the grid's copy: every control in the panel writes, and a
    panel redrawn from a stale row would show the edit undoing itself.
    """
    rows = await offload.io(library.load_collections)
    row = next((entry for entry in rows if entry.get("name") == name), None)
    if row is None:
        return None
    # Independent of each other, so one wait rather than four.
    membership, axes, settings, _ = await asyncio.gather(
        run.io_bound(library.collection_members, name),
        run.io_bound(library.filter_axes),
        run.io_bound(library.config_values),
        run.io_bound(library.load_thumbnails))
    membership = library.with_entry_art(membership)
    known = collection_rules.fields(axes or [])
    # The rules being edited, which are not always the rules that are stored. Held on
    # the client rather than in this build of the panel, so a section change or a
    # redraw does not discard an edit in progress.
    drafts = state.setdefault("collection_drafts", {})
    draft = drafts.setdefault(name, {})
    if draft.get("rules") == collection_rules.rows_from(row.get("filters"), known):
        draft.pop("rules")
    if "rules" not in draft:
        draft.pop("order", None)
    waiting = unsaved(drafts, rows, known)
    preview: dict[str, Any] | None = None
    if name in waiting:
        try:
            preview = library.with_entry_art(await offload.io(
                library.preview_collection, name,
                collection_rules.filters_from(draft["rules"], known)))
        except Exception as exc:  # noqa: BLE001 - the list says why in its place
            preview = {"error": why(exc)}
    return {"collection": row, "membership": membership, "fields": known,
            "settings": settings or {}, "preview": preview, "unsaved": name in waiting,
            "draft": draft, "waiting": waiting}


async def _draw_collection(container: ui.column, title: ui.column, library: Library,
                           name: str | None, state: dict[str, Any]) -> None:
    if not name:
        _blank(container, title, t("manager.page.collection"),
               t("manager.page.select_collection"))
        with container:
            _note_unsaved(state, set(state.get("unsaved_rules") or ())
                          & set(state.get("collection_drafts") or {}))
        return
    # All of it before the container is cleared. Clearing first holds the panel empty
    # across three round trips, which is a black flash after changing a member's table:
    # the old panel stays up until the new one is ready to replace it.
    read = await _read_collection(library, name, state)
    if read is None:
        _blank(container, title, t("manager.page.collection"),
               t("manager.page.gone"))
        return
    waiting = read.pop("waiting")

    container.clear()
    title.clear()
    with container:
        context: dict[str, Any] = {**read, "library": library, "state": state,
                                   "redraws": [], "dock": None,
                                   "games_by_id": {str(one.get("id") or ""): one
                                                   for one in library.games}}

        def retitle() -> None:
            """The header again, from the collection as it is now."""
            row = context["collection"]
            size = int(row.get("count") or 0)
            smart = (row.get("type") or "") == "filter"
            title.clear()
            with title:
                ui.label(row.get("name") or "") \
                    .classes("text-base manager-workbench-title leading-tight truncate") \
                    .tooltip(row.get("name") or "")
                with ui.row().classes("items-center gap-1 no-wrap"):
                    if smart:
                        ui.icon(verbs.SMART).classes("manager-heading-mark") \
                            .tooltip(t("manager.collections.smart.help"))
                    ui.label(t("manager.workbench.smart_games", count=size) if smart
                             else t("manager.workbench.hand_picked", count=size)) \
                        .classes("text-xs manager-workbench-label leading-none truncate")

        @on_page
        async def reload() -> None:
            """The panel's copy of the collection read again where it stands. The row is
            updated in place: what was drawn from it holds the same dict."""
            fresh = await _read_collection(library, name, state)
            if fresh is None:
                return
            waiting_now = fresh.pop("waiting")
            row = dict(fresh.pop("collection"))
            context["collection"].clear()
            context["collection"].update(row)
            context.update(fresh)
            _note_unsaved(state, waiting_now)
            retitle()

        retitle()
        context["reload"] = reload
        state["collection_panel"] = context
        _note_unsaved(state, waiting)

        context["rebuild"] = _rebuilds(
            context, f"collection:{name}",
            lambda: build_collection(container, title, library, state.get("collection"),
                                     state))
        await _rail(context, "collection", state)


async def _draw_all_games(container: ui.column, title: ui.column, library: Library,
                          state: dict[str, Any]) -> None:
    settings = await run.io_bound(library.config_values)
    container.clear()
    title.clear()
    with container:
        _title(title, t("manager.collections.all_games"),
               t("manager.workbench.all_games.count", count=len(library.games)))
        _note_unsaved(state, set())
        context: dict[str, Any] = {"library": library, "collection": {"name": BUILTIN_ALL},
                                   "settings": settings or {}, "state": state,
                                   "redraws": [], "dock": None}
        context["rebuild"] = _rebuilds(
            context, "collection:all",
            lambda: build_all_games(container, title, library, state))
        await _rail(context, "all_games", state)
