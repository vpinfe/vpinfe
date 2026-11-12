"""Backups: every copy VPinFE keeps, in one list.

A copy is a file of its own, or a backup that holds several. Both are one row: the part
of a backup is not a row, because restoring or deleting one part is not something the
server does. What is restored or deleted is the id the listing gave, and nothing here
builds a path.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from nicegui import run, ui

from common import i18n
from common.backups import copies as kept_copies
from common.failures import why
from common.i18n import t
from manager.backups import backup_cleanup, backup_go_back
from manager.kit import (
    busy,
    clipboard,
    confirm,
    deeplink,
    offload,
    panel,
    states,
    undo,
    verbs,
    views,
    walks,
    when,
)
from manager.kit import dialog as frame
from manager.kit import grid as grid_module
from manager.kit.data import Library
from manager.kit.on_page import on_page
from manager.settings import waiting

logger = logging.getLogger("vpinfe.manager.backups.backups")

SCOPE = "backups"
SETTING_ADDRESS = "/manager?" + deeplink.query({"view": "settings",
                                                "settings_page": "vpinfe.backups"})

# Several rows are named in a question up to here and counted after it.
NAMED_AT_MOST = 8

KIND_WORDS = {
    kept_copies.KIND_GAME: "manager.backups.kind.game_metadata",
    kept_copies.KIND_SETTINGS: "manager.backups.kind.settings",
    kept_copies.KIND_COLLECTIONS: "manager.backups.kind.collections",
    kept_copies.KIND_DEVICES: "manager.backups.kind.devices",
    kept_copies.KIND_PLAYERS: "manager.backups.kind.players",
    kept_copies.KIND_RECORDS: "manager.backups.kind.records",
    kept_copies.KIND_LAUNCHER: "manager.backups.kind.launcher_settings",
    kept_copies.KIND_THEME: "manager.backups.kind.theme",
    kept_copies.KIND_ARCHIVE: "manager.backups.kind.backup",
    kept_copies.KIND_MEDIA: "manager.backups.kind.media",
    kept_copies.KIND_ASSET: "manager.backups.kind.asset",
    kept_copies.KIND_TABLE: "manager.backups.kind.table",
}
REASON_WORDS = {
    kept_copies.REASON_UPGRADE: "manager.backups.reason.upgrade",
    kept_copies.REASON_RESTORE: "manager.backups.reason.restore",
    kept_copies.REASON_IMPORT: "manager.backups.reason.import",
    kept_copies.REASON_RESET: "manager.backups.reason.reset",
    kept_copies.REASON_FIRST_CHANGE: "manager.backups.reason.first_change",
    kept_copies.REASON_UPDATE: "manager.backups.reason.update",
    kept_copies.REASON_YOURS: "manager.backups.reason.yours",
    kept_copies.REASON_TURN: "manager.backups.reason.rotate",
    kept_copies.REASON_CONVERT: "manager.backups.reason.convert",
    kept_copies.REASON_EDIT: "manager.backups.reason.edit",
    kept_copies.REASON_MOVE: "manager.backups.reason.move",
    kept_copies.REASON_REPLACE: "manager.backups.reason.replace",
    kept_copies.REASON_DELETE: "manager.backups.reason.delete",
}
BY_WORDS = {kept_copies.BY_2X: "manager.backups.by.2x", kept_copies.BY_3X: "manager.backups.by.3x"}

# What a Back up now press can cover from here. Some games is chosen where the games are.
SCOPE_ALL_GAMES = "all_games"
SCOPE_RECORDS = "records"
SCOPE_LAUNCHER = "launcher"
SCOPE_EVERYTHING = "everything"
SCOPE_WORDS = {
    SCOPE_ALL_GAMES: "manager.backups.scope.all_games",
    SCOPE_RECORDS: "manager.backups.scope.records",
    SCOPE_LAUNCHER: "manager.backups.scope.launcher",
    SCOPE_EVERYTHING: "manager.backups.scope.everything",
}

# The filter field carries a time of day; the comparison ignores it, and the copy's.
_BEFORE_DAY = """(filterAt, taken) => {
  if (!taken) return -1;
  const day = at => new Date(at.getFullYear(), at.getMonth(), at.getDate()).getTime();
  const [copy, chosen] = [day(new Date(taken)), day(filterAt)];
  return copy < chosen ? -1 : copy > chosen ? 1 : 0;
}"""


_SHOWN = """(() => {
  const ids = [];
  getElement(%d).api.forEachNodeAfterFilterAndSort(node => ids.push(node.data.id));
  return ids;
})()"""

TAKEN_FILTER: dict[str, Any] = {
    ":valueFormatter": "params => (params.data || {}).taken_text || ''",
    "filter": "agDateColumnFilter",
    "filterParams": {":comparator": _BEFORE_DAY, "filterOptions": ["lessThan"],
                     "maxNumConditions": 1, "buttons": ["reset"]},
}


def _choices(words: dict[str, str]) -> list[dict[str, Any]]:
    return [{"value": value, "label": t(word)} for value, word in words.items()]


ORIGINAL_PRESENT, ORIGINAL_MISSING = "present", "missing"

ORIGINAL = states.Table(
    covers=t("manager.backups.original.help"),
    states=(states.State(ORIGINAL_PRESENT, t("manager.backups.present"),
                         t("manager.backups.present.meaning"), glyph=states.TICK),
            states.State(ORIGINAL_MISSING, t("manager.backups.gone"),
                         t("manager.backups.gone.meaning"), tone=states.WARN)))

COLUMNS = [
    grid_module.identifier("name", t("manager.backups.name"), 220, pinned="left",
                           help=t("manager.backups.name.help"),
                           **{":getQuickFilterText":
                              "params => [(params.data || {}).name, (params.data || {}).holds]"
                              ".filter(Boolean).join(' ')"}),
    grid_module.column("table", t("word.table"), 200,
                       help=t("manager.backups.table.help")),
    grid_module.column("kind", t("word.kind"), 150,
                       help=t("manager.backups.kind_column.help"),
                       **grid_module.choice_filter(_choices(KIND_WORDS))),
    grid_module.column("taken", t("manager.backups.taken"), 190,
                       tooltipField="taken_utc", help=t("manager.backups.taken.help"),
                       **TAKEN_FILTER),
    grid_module.column("reason", t("manager.backups.reason"), 160,
                       help=t("manager.backups.reason.help"),
                       **grid_module.choice_filter(_choices(REASON_WORDS))),
    grid_module.column("size", t("word.size"), 100, type="numericColumn",
                       help=t("manager.backups.size.help"),
                       **{":valueFormatter": "params => (params.data || {}).size_text || ''"}),
    ORIGINAL.column("original", t("manager.backups.original"), 130),
    grid_module.column("note", t("manager.backups.note"), 220,
                       help=t("manager.backups.note.help")),
    grid_module.column("written_by", t("manager.backups.written_by"), 160,
                       help=t("manager.backups.written_by.help"),
                       **grid_module.choice_filter(_choices(BY_WORDS))),
    grid_module.column("file", t("word.file"), 280, tooltipField="path",
                       help=t("manager.backups.file.help")),
]


_ALL = [definition["field"] for definition in COLUMNS]
_NEWEST_FIRST = ({"colId": "taken", "sort": "desc", "sortIndex": 0},)
# Whose records VPinFE keeps in its own config folder, which is what the VPinFE view holds.
_OWN_RECORDS = [kept_copies.KIND_SETTINGS, kept_copies.KIND_COLLECTIONS,
                kept_copies.KIND_DEVICES, kept_copies.KIND_PLAYERS, kept_copies.KIND_RECORDS]


def _by_kind(kinds: list[str], columns: tuple[str, ...], *, help: str) -> views.Preset:
    """The order of the columns is the grid's, so `columns` is a subsequence of `_ALL`."""
    return views.Preset(columns=columns, sort=_NEWEST_FIRST,
                        filters={"kind": {"values": kinds}}, help=help)


VIEWS: dict[str, list[str] | views.Preset] = {
    "manager.view.everything": views.Preset(
        columns=("name", "kind", "taken", "reason", "size", "original"), sort=_NEWEST_FIRST,
        help=t("manager.backups.everything_view.help")),
    "manager.view.backups_deleted": views.Preset(
        columns=("name", "kind", "taken", "reason", "size"), sort=_NEWEST_FIRST,
        filters={"original": {"values": [ORIGINAL_MISSING]}},
        help=t("manager.backups.deleted_view.help")),
    "manager.view.backups_yours": _by_kind(
        [kept_copies.KIND_ARCHIVE], ("name", "taken", "size", "original", "note"),
        help=t("manager.backups.yours_view.help")),
    "manager.view.backups_games": _by_kind(
        [kept_copies.KIND_GAME], ("name", "taken", "reason", "size", "original", "written_by"),
        help=t("manager.backups.games_view.help")),
    "manager.view.backups_media": _by_kind(
        [kept_copies.KIND_MEDIA], ("name", "taken", "reason", "size", "original", "file"),
        help=t("manager.backups.media_view.help")),
    "manager.view.backups_assets": _by_kind(
        [kept_copies.KIND_ASSET], ("name", "taken", "reason", "size", "original", "file"),
        help=t("manager.backups.assets_view.help")),
    "manager.view.backups_tables": _by_kind(
        [kept_copies.KIND_TABLE],
        ("name", "table", "taken", "reason", "size", "original", "file"),
        help=t("manager.backups.tables_view.help")),
    "manager.view.backups_launchers": _by_kind(
        [kept_copies.KIND_LAUNCHER], ("name", "taken", "reason", "size", "original", "note"),
        help=t("manager.backups.launchers_view.help")),
    "manager.view.backups_themes": _by_kind(
        [kept_copies.KIND_THEME], ("name", "taken", "size", "original"),
        help=t("manager.backups.themes_view.help")),
    "manager.view.backups_vpinfe": _by_kind(
        _OWN_RECORDS, ("kind", "taken", "reason", "size", "written_by"),
        help=t("manager.backups.vpinfe_view.help")),
}


def parts_of(found: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The copies that are listed: a backup's own parts are inside its row."""
    return [one for one in found if not one.get("within")]


def rows(found: list[dict[str, Any]], *, parts: bool = False) -> list[dict[str, Any]]:
    """One row per listed copy, or per part of a backup as well where `parts` is set. The
    time is kept as the stamp and drawn from the text."""
    with_records = {str(one["within"]) for one in found
                    if one.get("within") and one.get("subject_kind") == kept_copies.SUBJECT_VPINFE}
    holds: dict[str, list[str]] = {}
    for one in found:
        if one.get("within") and one.get("subject_name"):
            holds.setdefault(str(one["within"]), []).append(str(one["subject_name"]))
    return [{
        "id": one["id"],
        "holds": " ".join(holds.get(str(one["id"]), [])),
        "name": one.get("subject_name") or "",
        "subject_id": one.get("subject_id") or "",
        "subject_kind": one.get("subject_kind") or "",
        "kind": one.get("kind") or "",
        "file": one.get("file") or "",
        "table": one.get("label") or "",
        "path": one.get("path") or "",
        "taken": one.get("taken") or "",
        "taken_text": when.local_seconds(one.get("taken")),
        "taken_utc": str(one.get("taken") or ""),
        "written_by": one.get("written_by") or "",
        "reason": one.get("reason") or "",
        "reason_text": "" if one.get("reason") in (kept_copies.REASON_YOURS, None)
        else t(REASON_WORDS.get(str(one["reason"]), "manager.backups.reason.yours")),
        "within": bool(one.get("within")),
        "within_id": str(one.get("within") or ""),
        "version": one.get("version") or "",
        "note": one.get("note") or "",
        "size": int(one.get("size") or 0),
        "size_text": i18n.size(int(one.get("size") or 0)),
        "gone": bool(one.get("gone")),
        "original": ORIGINAL_MISSING if one.get("gone") else ORIGINAL_PRESENT,
        "holds_records": one["id"] in with_records,
    } for one in (found if parts else parts_of(found))]


def name_of(row: dict[str, Any]) -> str:
    """What a copy is of, or its file where the server names no owner."""
    return str(row.get("name") or row.get("file") or "")


def summary(shown: list[dict[str, Any]]) -> str:
    return t("manager.backups.summary", count=len(shown),
             size=i18n.size(sum(int(one.get("size") or 0) for one in shown)))


def build(library: Library, state: dict[str, Any], on_select: Callable[[Any], Any],
          rerender: Callable[[], None]) -> None:
    body = ui.column().classes("w-full grow min-h-0 gap-0")
    busy.fill(body, lambda: _fill(library, body, state, on_select, rerender,
                                  str(state.pop("find", "") or "")))


def _open_on_everything(library: Library) -> None:
    """A search that was linked to is read over every copy, not over the view last used."""
    kept, _active = views.stored(library, SCOPE)
    views.remember(library, SCOPE, kept, views.builtin_id("manager.view.everything"))


def _folder_line(folder: dict[str, Any]) -> None:
    """Where a backup taken now goes, and what is wrong with it. Read-only: the setting is
    where it changes, so the way there is offered only when there is something to fix."""
    if not folder.get("path"):
        return
    with ui.row().classes("w-full items-center gap-2 px-3 pb-2 shrink-0"):
        ui.label(t("manager.backups.kept_in", folder=folder["path"])).classes(
            "text-xs manager-label")
        if folder.get("problem"):
            ui.label(folder["problem"]).classes("text-xs text-negative")
            panel.link(t("word.change"), to=SETTING_ADDRESS)()


async def _fill(library: Library, body: Any, state: dict[str, Any],
                on_select: Callable[[Any], Any], rerender: Callable[[], None],
                find: str = "") -> None:
    # Imported here: `games` imports `backup_rows`, which imports this module.
    from manager.library.games import view_control

    if find:
        await run.io_bound(_open_on_everything, library)
    try:
        found = await offload.io(library.backups)
    except Exception as exc:  # noqa: BLE001 - this page says why, never 500s
        with body:
            panel.facts(ui, [panel.intro(t("manager.backups.read_failed"), hint=why(exc))])
        return
    built = rows(list(found.get("copies") or []))
    columns = list(COLUMNS)
    by_id = {row["id"]: row for row in built}
    on_screen: dict[str, Any] = {"ids": [row["id"] for row in built], "selected": 0}

    with body:
        with ui.row().classes(
                "w-full items-center gap-2 px-3 py-2 mb-2 shrink-0 manager-panel manager-grid-bar"):
            bar = panel.grid_bar()
            if built:
                wire_views, _picker, showing, describe = view_control(
                    library, SCOPE, VIEWS, _ALL, columns, bar=bar)
                describe()
            with bar.top, panel.bar_end():
                search = panel.search(t("manager.backups.search"))
                search.value = find
            with bar.bottom, panel.bar_end():
                count = ui.label(summary(built)).classes("text-xs manager-label")
                strip = [(t("manager.backups.take_now"), lambda: _ask_now(library, refresh))]
                if backup_go_back.available(built):
                    strip.append((t("manager.backups.go_back"),
                                  lambda: backup_go_back.ask(library)))
                panel.add_action(strip, empty=not built)
                show_selection = panel.selection_menu(
                    t("manager.backups.actions_selected"), lambda: picked(),
                    lambda chosen: acts(library, chosen, refresh,
                                        check=bool(grid_module.hidden_count(table))),
                    lambda: table.run_grid_method("deselectAll"))
                if built:
                    panel.icon_only(verbs.CLEAN,
                                    lambda: backup_cleanup.ask(library, built, refresh),
                                    hint=t("manager.backups.clean_up"))

        _folder_line(found.get("folder") or {})
        if not built:
            panel.facts(ui, [panel.intro(t("manager.backups.empty"))])
            return

        def counted_text() -> str:
            if on_screen["selected"]:
                return grid_module.selection_said(
                    table, on_screen["selected"],
                    t("manager.backups.selected", picked=on_screen["selected"],
                      shown=len(on_screen["ids"])))
            return summary([by_id[one] for one in on_screen["ids"] if one in by_id])

        def on_selected(chosen_rows: list[dict[str, Any]]) -> None:
            on_screen["selected"] = len(chosen_rows)
            show_selection(bool(chosen_rows))
            count.text = counted_text()

        def fill_menu(row: dict | None) -> None:
            one = by_id.get(str((row or {}).get("id") or ""))
            if one is None:
                menu.clear()
                return
            on = grid_module.menu_rows(table, one)
            panel.verb_menu(menu, name_of(one) if len(on) == 1
                            else t("manager.backups.selected_n", count=len(on)),
                            panel.for_selection(
                                acts(library, on, refresh,
                                     check=len(on) > 1
                                     and bool(grid_module.hidden_count(table))), len(on)))

        async def on_header_context(col_id: str | None) -> None:
            await grid_module.header_menu(menu, table, columns, col_id)

        with ui.element("div").classes("w-full grow min-h-0 flex flex-col"):
            table: ui.aggrid = grid_module.build(columns, built, SCOPE,
                                                 on_select_rows=on_selected,
                                                 on_context=fill_menu,
                                                 on_header_context=on_header_context,
                                                 view_of=showing)
            menu: ui.context_menu = ui.context_menu()
        grid_module.on_row_focus(
            SCOPE, lambda event: on_select(by_id.get(grid_module.focused_row(event))))
        search.on_value_change(
            lambda: table.run_grid_method("setGridOption", "quickFilterText",
                                          search.value or ""))
        if find:
            table.run_grid_method("setGridOption", "quickFilterText", find)

        async def counted() -> None:
            drawn = await ui.run_javascript(_SHOWN % table.id) or []
            on_screen["ids"] = [str(one) for one in drawn]
            count.text = counted_text()

        table.on("modelUpdated", counted)
        wire_views(table)

    def picked() -> list[dict[str, Any]]:
        return [by_id[str(r["id"])] for r in grid_module.selection(table)
                if str(r.get("id")) in by_id]

    @on_page
    async def reread() -> None:
        """Read the copies again into the grid in place, so a filter and a search stay."""
        try:
            fresh = rows(list((await offload.io(library.backups)).get("copies") or []))
        except Exception as exc:  # noqa: BLE001 - the reason belongs on the page
            ui.notify(t("manager.backups.read_failed"), caption=why(exc), type="negative")
            return
        if not fresh:
            built.clear()
            by_id.clear()
            rerender()
            return
        built[:] = fresh
        by_id.clear()
        by_id.update({row["id"]: row for row in fresh})
        table.run_grid_method("setGridOption", "rowData", fresh)

    @on_page
    async def refresh() -> None:
        """The grid read again, and the panel drawn again on the copy it is about, or
        emptied where that copy is gone."""
        await reread()
        chosen = state.get("backup")
        if chosen and state.get("workbench"):
            await on_select(by_id.get(chosen))

    state["reread_backups"] = reread
    state["refresh_backups"] = refresh


def acts(library: Library, rows_on: list[dict[str, Any]],
         refresh: Callable[[], Awaitable[None]], *,
         check: bool = False) -> list[panel.Verb]:
    """What can be done to the copies a menu is open on. `check` is where the selection
    reaches rows the screen leaves out, so a restore asks first."""
    present = [one for one in rows_on if not one["gone"]]
    entries = [panel.Verb(
        t("word.restore"),
        (lambda: restore(library, rows_on, refresh, check=check)) if present else None,
        hint="" if present else t("manager.backups.owner_gone"))]
    entries.append(panel.Verb(t("word.copy_path"),
                              draw=_copy_path(rows_on[0]["path"]), one=True))
    if not any(one["within"] for one in rows_on):
        entries.append(panel.Verb(t("word.delete"), lambda: delete(library, rows_on, refresh),
                                  danger=True))
    return entries


def _copy_path(path: str) -> Callable[[Callable[[], Any]], None]:
    def draw(_close: Callable[[], Any]) -> None:
        # In the browser at the click, which is the only moment it will copy.
        ui.menu_item(t("word.copy_path")).classes("manager-menu-item") \
            .on("click", lambda event: clipboard.copied(bool(event.args), path),
                js_handler=clipboard.COPY_JS % json.dumps(path))
    return draw


def _named(rows_on: list[dict[str, Any]]) -> list[str]:
    names = ["  ".join(part for part in (one["name"], one["table"] or one["file"]) if part)
             or one["id"]
             for one in rows_on]
    if len(names) <= NAMED_AT_MOST:
        return names
    return names[:NAMED_AT_MOST] + [t("said.and_more", count=len(names) - NAMED_AT_MOST)]


@on_page
async def delete(library: Library, rows_on: list[dict[str, Any]],
                  refresh: Callable[[], Awaitable[None]]) -> None:
    if not rows_on:
        return
    one = len(rows_on) == 1
    if not await confirm.ask(
            t("manager.backups.delete_one") if one
            else t("manager.backups.delete_many", count=len(rows_on)),
            detail=t("manager.backups.delete_detail"), lines=_named(rows_on),
            confirm=t("word.delete"), icon=verbs.DELETE):
        return
    for row in rows_on:
        try:
            await run.io_bound(library.delete_backup, row["id"])
        except Exception as exc:  # noqa: BLE001 - the reason belongs on the page
            ui.notify(t("said.delete_failed"), caption=why(exc), type="negative")
            await refresh()
            return
    ui.notify(t("manager.backups.deleted", count=len(rows_on)), type="positive")
    await refresh()


def _waits_for_a_restart(row: dict[str, Any]) -> bool:
    """Records are put back as VPinFE closes, and a backup holds them or does not."""
    return row["subject_kind"] == kept_copies.SUBJECT_VPINFE or row["holds_records"]


@on_page
async def restore(library: Library, rows_on: list[dict[str, Any]],
                   refresh: Callable[[], Awaitable[None]], *, check: bool = False) -> None:
    rows_on = [one for one in rows_on if not one["gone"]]
    if not rows_on:
        return
    restarting = any(_waits_for_a_restart(one) for one in rows_on)
    if restarting or check:
        question = t("manager.backups.restore_one", name=name_of(rows_on[0])) \
            if len(rows_on) == 1 else t("manager.backups.restore_many", count=len(rows_on))
        if not await confirm.ask(
                question, detail=t("manager.backups.restart_detail") if restarting else "",
                lines=_named(rows_on), confirm=t("word.restore"), icon=verbs.RESTORE,
                danger=False):
            return
    kept: list[str] = []
    skipped: list[dict[str, str]] = []
    waits = False
    went = False
    for row in rows_on:
        try:
            done = await offload.io(library.restore_backup, row["id"], reread=False)
        except Exception as exc:  # noqa: BLE001 - the reason belongs on the page
            ui.notify(t("manager.backups.restore_failed"), caption=why(exc), type="negative")
            break
        went = True
        kept += [str(one["id"]) for one in done.get("kept") or []]
        skipped += list(done.get("skipped") or [])
        waits = waits or bool(done.get("pending"))
    if went:
        await run.io_bound(library.restored_backups)
    if waits:
        if skipped:
            ui.notify(t("manager.backups.restore_skipped"), caption=_skipped_said(skipped),
                      type="warning")
        await _restart(library)
        return
    if went:
        await _said_restored(library, rows_on, kept, skipped, refresh)
        return
    await refresh()


def _skipped_said(skipped: list[dict[str, str]]) -> str:
    return "\n".join(": ".join((one.get("name", ""), one.get("reason", ""))) for one in skipped)


async def _said_restored(library: Library, rows_on: list[dict[str, Any]], kept: list[str],
                         skipped: list[dict[str, str]],
                         refresh: Callable[[], Awaitable[None]]) -> None:
    said = t("manager.backups.restored_one", name=name_of(rows_on[0])) if len(rows_on) == 1 \
        else t("manager.backups.restored_many", count=len(rows_on))
    caption = _skipped_said(skipped)

    async def reverse() -> None:
        for copy_id in kept:
            await offload.io(library.restore_backup, copy_id, reread=False)
        await run.io_bound(library.restored_backups)
        await refresh()

    if kept:
        undo.offer(said, reverse, icon=verbs.RESTORE, warn=bool(skipped), caption=caption)
    else:
        ui.notify(said, caption=caption or None, type="warning" if skipped else "positive")
    await refresh()


@on_page
async def _restart(library: Library) -> None:
    """Records are put back as VPinFE exits, so this is the restart that does it, and the
    page follows the Manager to its address afterwards."""
    label = t("action.vpinfe.restart")
    try:
        answer = await offload.io(library.config_waiting)
        port = int(answer.get("http_port") or 0)
        done = await offload.io(library.perform_action, "vpinfe", "restart")
    except Exception as exc:  # noqa: BLE001 - the reason belongs on the page
        ui.notify(t("said.action_failed"), caption=why(exc), type="negative")
        return
    if not done.get("performed"):
        ui.notify(t("manager.devices.not_happen", label=label), type="warning")
        return
    ui.notify(t("manager.backups.restarting"), type="positive")
    if port:
        ui.run_javascript(waiting.follow_script(port))


def _name_input(wanted: dict[str, Any], held: dict[str, Any]) -> None:
    held["name"] = ui.input(value=wanted["name"]).props("dense outlined clearable") \
        .classes("w-full")
    held["name"].on_value_change(lambda e: wanted.update(name=str(e.value or "")))


def _note_input(wanted: dict[str, Any], held: dict[str, Any]) -> None:
    held["note"] = ui.input(placeholder=t("manager.backups.note_placeholder")) \
        .props("dense outlined clearable").classes("w-full")
    held["note"].on_value_change(lambda e: wanted.update(note=str(e.value or "")))


async def ask_details(suggested: str, take: Callable[[str, str], Awaitable[None]], *,
                      place: str = "", walking: bool = False) -> Any:
    """Back up now for something already chosen: a name to confirm or change, and a note.

    `take` is given both, trimmed, and runs once the dialog has closed. Answers True once
    it has run, `walks.SKIPPED` or, in a walk, `walks.STOPPED`; `place` says where the walk
    has got to.
    """
    wanted: dict[str, Any] = {"name": suggested, "note": ""}
    held: dict[str, Any] = {}

    def confirm() -> None:
        if held.get("sent"):
            return
        held["sent"] = True
        box.submit((wanted["name"].strip(), wanted["note"].strip()))

    with frame.opened(t("manager.backups.back_up_title"), ignore_outside_click=True) as box:
        if place:
            ui.label(place).classes("manager-help px-3")
        panel.facts(ui, [(t("manager.backups.name"), lambda: _name_input(wanted, held)),
                         (t("manager.backups.note"), lambda: _note_input(wanted, held))])
        with frame.footer():
            frame.leave(box, walking)
            go = frame.answer(t("manager.backups.back_up"), confirm, icon=verbs.ADD)
    frame.enter_presses(go)
    frame.focus(box, held["name"], select=True)
    answer = await box
    if not isinstance(answer, tuple):
        return answer or walks.SKIPPED
    await take(*answer)
    return True


@on_page
async def _ask_now(library: Library, refresh: Callable[[], Awaitable[None]]) -> None:
    try:
        found = await offload.io(library.launchers)
    except Exception:  # noqa: BLE001 - a list that will not load only takes that choice away
        logger.warning("Could not read the launchers for Back up now", exc_info=True)
        found = {}
    programs = {str(one["launcher_id"]): str(one.get("name") or one["launcher_id"])
                for one in found.get("launchers") or []}
    wanted = {"scope": SCOPE_ALL_GAMES, "launcher": next(iter(programs), ""), "name": "",
              "note": ""}
    held: dict[str, Any] = {}

    def suggested() -> str:
        if wanted["scope"] == SCOPE_LAUNCHER:
            return programs.get(wanted["launcher"], "")
        return t(SCOPE_WORDS[wanted["scope"]])

    wanted["name"] = suggested()

    def rename(before: str) -> None:
        """The suggestion follows the choice until the name has been written over."""
        if wanted["name"] == before:
            held["name"].set_value(suggested())

    def draw_what() -> None:
        with ui.column().classes("w-full gap-1"):
            panel.select({key: t(word) for key, word in SCOPE_WORDS.items()
                          if key != SCOPE_LAUNCHER or programs},
                         wanted["scope"], lambda e: _chose(e.value))()
            with ui.element("div").classes("w-full") as which:
                panel.select(programs or {"": ""}, wanted["launcher"],
                             lambda e: _picked(str(e.value or "")))()
            which.set_visibility(False)
            held["program"] = which

    def _chose(scope: str) -> None:
        before = suggested()
        wanted["scope"] = scope
        held["program"].set_visibility(scope == SCOPE_LAUNCHER)
        rename(before)

    def _picked(launcher: str) -> None:
        before = suggested()
        wanted["launcher"] = launcher
        rename(before)

    @on_page
    async def take() -> None:
        box.close()
        scope = wanted["scope"]
        try:
            await offload.io(library.take_backup, scope,
                             launcher=wanted["launcher"] if scope == SCOPE_LAUNCHER else "",
                             name=wanted["name"].strip(), note=wanted["note"].strip())
        except Exception as exc:  # noqa: BLE001 - the reason belongs on the page
            ui.notify(t("manager.backups.back_up_failed"), caption=why(exc), type="negative")
            return
        ui.notify(t("manager.backups.backed_up"), type="positive")
        await refresh()

    with frame.opened(t("manager.backups.back_up_title"), ignore_outside_click=True) as box:
        panel.facts(ui, [(t("manager.backups.what"), draw_what),
                         (t("manager.backups.name"), lambda: _name_input(wanted, held)),
                         (t("manager.backups.note"), lambda: _note_input(wanted, held))])
        with frame.footer():
            frame.cancel(box.close)
            go = frame.answer(t("manager.backups.back_up"), take, icon=verbs.ADD)
    frame.enter_presses(go)
    box.open()


# --- the panel ------------------------------------------------------------------

REASON_LINES = {
    kept_copies.REASON_UPGRADE: "manager.backups.reason_line.upgrade",
    kept_copies.REASON_RESTORE: "manager.backups.reason_line.restore",
    kept_copies.REASON_IMPORT: "manager.backups.reason_line.import",
    kept_copies.REASON_RESET: "manager.backups.reason_line.reset",
    kept_copies.REASON_FIRST_CHANGE: "manager.backups.reason_line.first_change",
    kept_copies.REASON_UPDATE: "manager.backups.reason_line.update",
    kept_copies.REASON_TURN: "manager.backups.reason_line.rotate",
    kept_copies.REASON_CONVERT: "manager.backups.reason_line.convert",
    kept_copies.REASON_EDIT: "manager.backups.reason_line.edit",
    kept_copies.REASON_MOVE: "manager.backups.reason_line.move",
    kept_copies.REASON_REPLACE: "manager.backups.reason_line.replace",
    kept_copies.REASON_DELETE: "manager.backups.reason_line.delete",
}
# Where the name of what a copy is of opens, per kind of owner. A theme has a page and no
# address of its own.
_OPENS = {
    kept_copies.SUBJECT_GAME: lambda row: {"view": "games", "game": row["subject_id"]},
    kept_copies.SUBJECT_LAUNCHER: lambda row: {"view": "launchers", "launcher": row["subject_id"]},
    kept_copies.SUBJECT_THEME: lambda row: {"view": "themes"},
}
# The parts of a backup drawn at once; the rest are reached by searching.
PARTS_SHOWN = 100


def kept_said(row: dict[str, Any]) -> str:
    """How long VPinFE keeps this copy, from what the listing says about it."""
    if row["subject_kind"] == kept_copies.SUBJECT_ARCHIVE:
        return t("manager.backups.kept.until_deleted")
    if row["kind"] == kept_copies.KIND_THEME:
        return t("manager.backups.kept.theme_update")
    if row["file"].endswith(".bak"):
        return t("manager.backups.kept.until_deleted")
    if row["written_by"] == kept_copies.BY_3X:
        return t("manager.backups.kept.next_copy")
    if row["written_by"] == kept_copies.BY_2X:
        return t("manager.backups.kept.upgraded_again")
    return t("manager.backups.kept.until_deleted")


def written_by_said(row: dict[str, Any]) -> str:
    """The whole version a backup was written by, where the column says the series."""
    if row["version"]:
        return t("manager.backups.by.version", version=row["version"])
    return t(BY_WORDS[row["written_by"]]) if row["written_by"] in BY_WORDS else ""


def _taken(row: dict[str, Any]) -> Callable[[], None]:
    def draw() -> None:
        ui.label(row["taken_text"]).classes("manager-fact-value truncate min-w-0") \
            .tooltip(row["taken_utc"])
    return draw


def _file(row: dict[str, Any]) -> Callable[[], None]:
    icon = "folder" if row["kind"] == kept_copies.KIND_THEME else "description"
    return panel.path_value(row["path"], icon=icon)


def _name_of_owner(row: dict[str, Any]) -> None:
    """The name of what a copy is of, opening that thing where it still is."""
    opens = _OPENS.get(row["subject_kind"])
    if opens and row["subject_id"] and not row["gone"]:
        with ui.element("span").classes("truncate min-w-0"):
            panel.link(name_of(row), to="/manager?" + deeplink.query(opens(row)))()
    else:
        ui.label(name_of(row)).classes("manager-fact-value manager-member-name truncate min-w-0")
    if row["gone"]:
        ui.label(t("manager.backups.gone")).tooltip(t("manager.backups.gone.help")) \
            .classes("manager-member-chip manager-chip-warn")


def _subject(row: dict[str, Any]) -> Callable[[], None]:
    def draw() -> None:
        with ui.row().classes("items-center gap-2 no-wrap min-w-0"):
            _name_of_owner(row)
    return draw


def _note(context: dict[str, Any]) -> Callable[[], None]:
    row, library, state = context["backup"], context["library"], context["state"]

    @on_page
    async def save(text: str) -> str:
        """Saved, and the grid's row put right behind it. The reason it was refused, or
        empty."""
        try:
            await run.io_bound(library.set_backup_note, row["id"], text.strip())
        except Exception as exc:  # noqa: BLE001
            return why(exc)
        reread = state.get("reread_backups")
        if callable(reread):
            await reread()
        return ""

    return panel.field(row["note"], save, refuses=True,
                       placeholder=t("manager.backups.note_placeholder"))


async def details(context: dict[str, Any]) -> None:
    row = context["backup"]
    entries: list[tuple[Any, Any]] = []
    if row["subject_kind"] == kept_copies.SUBJECT_ARCHIVE:
        entries.append((t("manager.backups.note"), _note(context)))
    else:
        if row["name"]:
            entries.append((t("manager.backups.name"), _subject(row)))
        entries.append((t("word.kind"),
                        t(KIND_WORDS.get(row["kind"], "manager.backups.kind.records"))))
        if row["reason"] in REASON_LINES:
            entries += [(t("manager.backups.reason"), t(REASON_WORDS[row["reason"]])),
                        panel.note(t(REASON_LINES[row["reason"]]))]
    if row["note"] and row["subject_kind"] != kept_copies.SUBJECT_ARCHIVE:
        entries.append((t("manager.backups.note"), row["note"]))
    entries.append((t("manager.backups.taken"), _taken(row)))
    if said := written_by_said(row):
        entries.append((t("manager.backups.written_by"), said))
    entries += [(t("word.size"), i18n.size(row["size"])),
                (t("word.file"), _file(row)),
                (t("manager.backups.kept"), kept_said(row))]
    with ui.column().classes("gap-0 manager-form w-full min-w-0"):
        panel.facts(ui, entries)


def has_contents(context: dict[str, Any]) -> bool:
    archive = context["backup"]["subject_kind"] == kept_copies.SUBJECT_ARCHIVE
    return archive and bool(context["parts"])


async def contents(context: dict[str, Any]) -> None:
    """What a backup holds, a line per part, each restorable on its own."""
    row, state = context["backup"], context["state"]
    finds = state.setdefault("backup_find", {})

    async def refresh() -> None:
        again = state.get("refresh_backups")
        if callable(again):
            await again()

    def lines() -> None:
        shown.clear()
        needle = str(finds.get(row["id"], "")).strip().lower()
        found = [one for one in context["parts"]
                 if not needle
                 or needle in " ".join((one["name"], one["table"], one["file"])).lower()]
        with shown:
            for part in found[:PARTS_SHOWN]:
                _part_line(context["library"], part, refresh)
            if len(found) > PARTS_SHOWN:
                panel.line(t("manager.backups.parts_more", shown=PARTS_SHOWN,
                             total=len(found)), classes="manager-help")
            if not found:
                panel.line(t("manager.backups.parts_none"), classes="manager-help")

    with ui.column().classes("gap-0 w-full min-w-0"):
        with ui.row().classes("items-center gap-x-4 manager-section-bar"):
            box = panel.search(t("manager.backups.search_parts")).props("debounce=250")
            box.value = str(finds.get(row["id"], ""))
        shown = ui.column().classes("gap-0 w-full")
        lines()

        def looked() -> None:
            finds[row["id"]] = box.value or ""
            lines()

        box.on_value_change(looked)


def _part_line(library: Library, part: dict[str, Any],
               refresh: Callable[[], Awaitable[None]]) -> None:
    on = [part]
    menu = ui.context_menu()
    panel.verb_menu(menu, name_of(part), acts(library, on, refresh))
    with ui.row().classes("items-center gap-2 w-full no-wrap manager-member-row"):
        with ui.column().classes("gap-0 grow min-w-0"):
            with ui.row().classes("items-center gap-2 w-full no-wrap"):
                _name_of_owner(part)
            ui.label(t(KIND_WORDS.get(part["kind"], "manager.backups.kind.records"))) \
                .classes("manager-help")
        ui.label(i18n.size(part["size"])).classes("manager-help whitespace-nowrap")
        panel.icon_action(t("word.restore"), lambda: restore(library, on, refresh),
                          icon=verbs.RESTORE, enabled=not part["gone"],
                          hint=t("manager.backups.owner_gone") if part["gone"]
                          else t("word.restore"))()
