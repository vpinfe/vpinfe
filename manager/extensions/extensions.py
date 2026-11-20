"""The extensions this install has and the ones the VPinFE Extension List offers, in one grid.

An entry is the pair of what is installed (`record`, from the registry) and what the list
says (`listed`); either may be missing. Nothing here decides what an extension may do - that
is `common.extensions` - this page shows it and runs the acts the list allows.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from nicegui import background_tasks, run, ui

from common.extensions import host
from common.failures import why
from common.i18n import t
from common.security import accepted
from manager.extensions import ext_mark, ext_page
from manager.kit import (
    busy,
    confirm,
    deeplink,
    grid,
    offload,
    panel,
    states,
    verbs,
    views,
    when,
)
from manager.kit.data import Library
from manager.kit.failed_together import Failures
from manager.kit.on_page import on_page
from manager.settings import settings

logger = logging.getLogger("vpinfe.manager.extensions.extensions")

SCOPE = "manager.extensions.columns"

ON, UPDATE, OFF, FAILED, CHANGED, NOT_TRUSTED, NOT_INSTALLED = (
    "on", "update", "off", "failed", "changed", "not_trusted", "not_installed")

STATUS = states.Table(
    covers=t("manager.extensions.status.help"),
    states=(
        states.State(ON, t("word.on"), t("manager.extensions.status.on"), tone=states.ACCENT),
        states.State(UPDATE, t("manager.extensions.update_available"),
                     t("manager.extensions.status.update"), tone=states.WARN),
        states.State(OFF, t("word.off"), t("manager.extensions.status.off")),
        states.State(FAILED, t("manager.ext_page.failed"),
                     t("manager.extensions.status.failed"), tone=states.BAD),
        states.State(CHANGED, t("manager.extensions.changed"),
                     t("manager.extensions.status.changed"), tone=states.BAD),
        states.State(NOT_TRUSTED, t("manager.ext_page.not_trusted_chip"),
                     t("manager.extensions.status.not_trusted"), tone=states.BAD),
        states.State(NOT_INSTALLED, t("manager.extensions.not_installed"),
                     t("manager.extensions.status.not_installed"), tone=states.QUIET)))

# An extension that does not come with VPinFE or from its list. A copy is one that stands in
# for a name VPinFE ships or lists, which is the case that needs the plainer word.
UNOFFICIAL, COPY = "unofficial", "copy"
TRUST = states.Table(
    covers=t("manager.extensions.trust.help"),
    states=(
        states.State(UNOFFICIAL, t("manager.ext_page.unofficial_chip"),
                     t("manager.extensions.trust.unofficial"), tone=states.WARN),
        states.State(COPY, t("manager.extensions.unofficial_copy"),
                     t("manager.extensions.trust.copy"), tone=states.WARN)),
    blank=states.State("", t("manager.extensions.official"),
                       t("manager.extensions.trust.official")))

_PANEL_TIER = {ON: "on", UPDATE: "warn", OFF: "off", FAILED: "bad", CHANGED: "bad",
               NOT_TRUSTED: "bad", NOT_INSTALLED: "off", UNOFFICIAL: "warn", COPY: "warn"}

COLUMNS: list[dict[str, Any]] = [
    grid.identifier("name", t("word.name"), 320, subtitle="said", help=grid.NO_HELP,
                    picture="picture", glyph=ext_mark.PICTURE_GLYPH),
    STATUS.column("status", t("word.status"), 170),
    TRUST.column("trust", t("manager.extensions.trust"), 150),
    grid.column("version", t("word.version"), 110, help=grid.NO_HELP),
    grid.column("updated", t("manager.extensions.updated"), 150,
                help=t("manager.extensions.updated.help"), **when.cell("updated")),
    grid.column("repository", t("manager.extensions.repository"), 280, help=grid.NO_HELP),
]
_ALL = [one["field"] for one in COLUMNS]
_SHOWN = tuple(one for one in _ALL if one != "repository")

VIEWS: dict[str, list[str] | views.Preset] = {
    "manager.view.everything": views.Preset(
        columns=_SHOWN, help=t("manager.view.extensions_everything.help")),
    "manager.view.extensions_installed": views.Preset(
        columns=_SHOWN,
        filters={"status": {"values": [ON, UPDATE, OFF, FAILED, CHANGED, NOT_TRUSTED]}}),
    "manager.view.updates": views.Preset(
        columns=_SHOWN, filters={"status": {"values": [UPDATE]}},
        help=t("manager.view.extensions_updates.help")),
    "manager.view.extensions_available": views.Preset(
        columns=_SHOWN, filters={"status": {"values": [NOT_INSTALLED]}}),
}


# -- what an entry is -----------------------------------------------------------


def entries_of(records: list[dict[str, Any]], listing: dict[str, Any]) -> list[dict[str, Any]]:
    """Every installed extension and every listed one, once, with what each says."""
    listed = {str(one["name"]): one for one in listing.get("entries") or []}
    where, checked = str(listing.get("list") or ""), bool(listing.get("checked"))
    found = [{"name": str(one["name"]), "record": one, "listed": listed.get(str(one["name"])),
              "list": where, "checked": checked} for one in records]
    held = {one["name"] for one in found}
    found += [{"name": name, "record": None, "listed": one, "list": where, "checked": checked}
              for name, one in listed.items() if name not in held]
    return sorted(found, key=lambda one: shown(one).lower())


@on_page
async def load(library: Library, *, check: bool = False
               ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """The entries and the list as it stands. With `check` the list is fetched again; when
    that fails the one already kept is read instead and the failure is said."""
    records = await offload.io(library.extensions)
    try:
        listing = await offload.io(library.extension_list, check)
    except Exception as exc:  # noqa: BLE001 - the page shows what it has, never 500s
        if check:
            ui.notify(t("manager.extensions.check_failed"), caption=why(exc), type="negative")
        else:
            logger.warning("manager: the extension list could not be read: %s", why(exc))
        listing = {}
        if check:
            try:
                listing = await offload.io(library.extension_list, False)
            except Exception:  # noqa: BLE001
                logger.warning("manager: the kept extension list could not be read",
                               exc_info=True)
    return entries_of(records, listing), listing


def shown(entry: dict[str, Any]) -> str:
    record, listed = entry["record"] or {}, entry["listed"] or {}
    return str(record.get("display_name") or listed.get("display_name") or entry["name"])


def _official(record: dict[str, Any]) -> bool:
    """VPinFE stands behind it: it ships with VPinFE or came from the list and is still on
    it."""
    return accepted.is_official(str(record.get("origin"))) and not record.get("left_list")


def status(entry: dict[str, Any]) -> str:
    record, listed = entry["record"], entry["listed"] or {}
    if record is None:
        return NOT_INSTALLED
    if not _official(record) and not (record.get("trust") or {}).get("accepted"):
        return CHANGED if record.get("origin") == accepted.CHANGED else NOT_TRUSTED
    if listed.get("update"):
        return UPDATE
    state = record.get("state")
    if state == host.LOADED:
        return ON
    if state in (host.FAILED, host.DISABLED):
        return FAILED
    return OFF


def trust_state(entry: dict[str, Any]) -> str:
    record = entry["record"]
    if record is None or _official(record):
        return ""
    return COPY if record.get("origin") == accepted.REPLACES else UNOFFICIAL


def stopped(entry: dict[str, Any]) -> bool:
    return status(entry) in (CHANGED, NOT_TRUSTED)


def version_of(entry: dict[str, Any]) -> str:
    record, listed = entry["record"] or {}, entry["listed"] or {}
    return str(record.get("version") or listed.get("version") or "")


def repository_of(entry: dict[str, Any]) -> str:
    """The repository address: the list's for a listed extension, the manifest's otherwise."""
    record, listed = entry["record"] or {}, entry["listed"] or {}
    return str(listed.get("url") or record.get("source") or "")


def help_of(entry: dict[str, Any]) -> str:
    """Where its help is: the manifest's `home`, else the README in its repository."""
    return str((entry["record"] or {}).get("home") or "") or panel.readme_page(
        repository_of(entry))


def _picture(entry: dict[str, Any]) -> str:
    record = entry["record"] or {}
    if not record.get("picture_shown"):
        return ""
    return f"/api/v1/extensions/{entry['name']}/picture?v={version_of(entry)}"


def rows(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [when.said(_row(one), "updated") for one in entries]


def author_of(entry: dict[str, Any]) -> str:
    """The line under the name, in the grid and over the panel alike."""
    record, listed = entry["record"] or {}, entry["listed"] or {}
    return str(record.get("author") or listed.get("author") or "")


def _row(entry: dict[str, Any]) -> dict[str, Any]:
    listed = entry["listed"] or {}
    return {"id": entry["name"], "name": shown(entry), "picture": _picture(entry),
            "said": author_of(entry),
            "status": status(entry), "trust": trust_state(entry),
            "version": version_of(entry), "updated": str(listed.get("updated") or ""),
            "repository": (ext_mark.source_shown(repository_of(entry))
                           if repository_of(entry) else "")}


# -- the page ---------------------------------------------------------------------


def build(library: Library, state: dict[str, Any],
          on_select: Callable[[dict | None], Any], redraw: Callable[[], None]) -> None:
    body = ui.column().classes("w-full grow min-h-0 gap-0")
    busy.fill(body, lambda: _fill(library, state, on_select, redraw, body, check=False))


def _checked_said(listing: dict[str, Any]) -> str:
    stamp = str(listing.get("checked") or "")
    return t("manager.extensions.checked", when=when.ago(stamp)) if stamp \
        else t("manager.extensions.never_checked")


def _extensions_said(shown_count: int, total: int) -> str:
    if shown_count == total:
        return t("manager.extensions.extensions", count=total)
    return t("manager.extensions.extensions_of", shown=shown_count, count=total)


async def _fill(library: Library, state: dict[str, Any],
                on_select: Callable[[dict | None], Any], redraw: Callable[[], None],
                body: Any, check: bool) -> None:
    # Deferred: `games` imports `workbench.all_settings`, and `workbench.rail` imports this
    # module for its sections.
    from manager.library.games import view_control

    try:
        found, listing = await load(library, check=check)
    except Exception as exc:  # noqa: BLE001 - this page says why, never 500s
        body.clear()
        with body:
            panel.facts(ui, [panel.intro(t("manager.extensions.read_failed"), hint=why(exc))])
        return
    recount = state.get("recount_extensions")
    if check and callable(recount):
        await recount()

    built = rows(found)
    by_id = {row["id"]: row for row in built}
    by_name = {one["name"]: one for one in found}
    body.clear()
    with body:
        with ui.row().classes("w-full items-center gap-2 px-3 py-2 mb-2 shrink-0 "
                              "manager-panel manager-grid-bar"):
            bar = panel.grid_bar()
            wire_views, _picker, showing, describe = view_control(
                library, SCOPE, VIEWS, _ALL, COLUMNS, bar=bar)
            describe()
            with bar.top, panel.bar_end():
                search = panel.search(t("manager.extensions.search"))
            with bar.bottom, panel.bar_end():
                count = ui.label(_extensions_said(len(built), len(built))) \
                    .classes("text-xs manager-label")
                checked = ui.label(_checked_said(listing)).classes("text-xs manager-label")
                show_selection = panel.selection_menu(
                    t("manager.extensions.menu"), lambda: grid.selection(table),
                    lambda chosen: acts_on(chosen),
                    lambda: table.run_grid_method("deselectAll"))
                panel.refresh(lambda: _fill(library, state, on_select, redraw, body, check=True),
                              t("manager.extensions.check_updates"))

        async def on_header_context(col_id: str | None) -> None:
            await grid.header_menu(menu, table, COLUMNS, col_id)

        async def redrawn() -> None:
            redraw()

        def acts_on(rows_on: list[dict[str, Any]]) -> list[panel.Verb]:
            chosen = [by_name[str(one["id"])] for one in rows_on if str(one["id"]) in by_name]
            return acts(library, chosen, redrawn) if chosen else []

        picked: list[dict[str, Any]] = []
        showing_rows = {"rows": len(built)}

        def said() -> str:
            if picked:
                return grid.selection_said(table, len(picked), t(
                    "manager.extensions.selected", picked=len(picked),
                    shown=showing_rows["rows"]))
            return _extensions_said(showing_rows["rows"], len(by_id))

        def on_selected(rows_selected: list[dict[str, Any]]) -> None:
            picked[:] = rows_selected
            show_selection(bool(rows_selected))
            count.text = said()

        def fill(row: dict | None) -> None:
            if row is None or str(row.get("id") or "") not in by_name:
                menu.clear()
                return
            on = grid.menu_rows(table, row)
            panel.verb_menu(
                menu, shown(by_name[str(row["id"])]) if len(on) == 1
                else t("manager.extensions.extensions", count=len(on)),
                panel.for_selection(acts_on(on), len(on)))

        with ui.element("div").classes("w-full grow min-h-0 flex flex-col"):
            table = grid.build(COLUMNS, built, SCOPE, on_selected, fill, on_header_context,
                               view_of=showing)
            menu = ui.context_menu()
        grid.on_row_focus(SCOPE, lambda event: on_select(by_id.get(grid.focused_row(event))))

        async def counted() -> None:
            seen = await table.run_grid_method("getDisplayedRowCount")
            showing_rows["rows"] = seen if isinstance(seen, int) else len(by_id)
            count.text = said()

        table.on("modelUpdated", counted)

        async def refresh_rows() -> None:
            now, again = await load(library)
            by_name.clear()
            by_name.update({one["name"]: one for one in now})
            grid.replace_rows(table, built, by_id, rows(now), lambda _row: True)
            count.text = said()
            checked.text = _checked_said(again)

        state["refresh_extensions"] = refresh_rows
        wire_views(table)
        search.on_value_change(
            lambda: table.run_grid_method("setGridOption", "quickFilterText",
                                          search.value or ""))


@on_page
async def changed(context: dict[str, Any]) -> None:
    """The grid's row, this panel and the rail, after an act changed the extension. The rail
    is read again because what is running decides which pages an extension adds to it."""
    state = context["state"]
    try:
        refresh = state.get("refresh_extensions")
        if callable(refresh):
            await refresh()
        for step in ("read_extensions", "refresh_shell"):
            again = state.get(step)
            if callable(again):
                await again()
        await context["rebuild"]()
    except Exception as exc:  # noqa: BLE001 - VPinFE may be restarting; the page says so
        ui.notify(t("manager.extensions.read_failed"), caption=why(exc), type="negative")


def refiller(context: dict[str, Any]) -> Callable[[], Any]:
    """What the pieces in `ext_page` call to draw the panel again, from code that is not
    async."""
    return lambda: background_tasks.create(on_page(changed)(context),
                                           name="manager-extension-refill")


# -- the panel ----------------------------------------------------------------------


def _open_device() -> None:
    ui.navigate.to("/manager?" + deeplink.query(
        {"view": "settings", "settings_page": settings.IDENTITY}))


def _findings(entry: dict[str, Any], library: Library, again: Callable[[], Any],
              refill: Callable[[], None]) -> list[Callable[[], None]]:
    record = entry["record"]
    if record is None:
        return []
    if status(entry) == CHANGED and entry["listed"]:
        count = len((record.get("trust") or {}).get("changed_files") or [])
        fix = panel.action(t("manager.extensions.reinstall"),
                           lambda: _each(entry_run(library, "install_extension"), [entry],
                                         "manager.extensions.reinstalled",
                                         "manager.extensions.reinstall_failed", again),
                           icon=verbs.FETCH, hint=t("manager.extensions.reinstall.help"))
        return [ext_page.finding(t("manager.extensions.changed_line", count=count),
                                 t("manager.extensions.changed_detail"), fix)]
    found = ext_page.findings(record, _open_device, refill)
    if record.get("left_list"):
        found.append(ext_page.finding(t("manager.extensions.left_line"),
                                      t("manager.extensions.left_detail")))
    return found


def entry_run(library: Library, method: str) -> Callable[[str], Any]:
    return getattr(library, method)


async def details(context: dict[str, Any]) -> None:
    entry = context["extension"]
    library = context["library"]
    record = entry["record"]
    listed = entry["listed"] or {}
    refill = refiller(context)

    async def again() -> None:
        await changed(context)

    @on_page
    async def check() -> None:
        try:
            await offload.io(library.extension_list, True)
        except Exception as exc:  # noqa: BLE001 - the kept list stays what the panel reads
            ui.notify(t("manager.extensions.check_failed"), caption=why(exc), type="negative")
            return
        recount = context["state"].get("recount_extensions")
        if callable(recount):
            await recount()
        await again()

    with ui.column().classes("gap-0 manager-form w-full min-w-0"):
        entries: list[tuple[Any, ...]] = []
        description = str((record or {}).get("description") or listed.get("description") or "")
        if description:
            entries.append(panel.intro(description))
        entries += [(panel.FULL, one) for one in _findings(entry, library, again, refill)]
        if record is not None:
            entries.append(ext_page.enabled_row(record, refill))
        entries.append((t("manager.extensions.updates"),
                        _updates_value(library, entry, again, check)))
        entries.append((t("manager.extensions.trust"), _trust_value(entry)))
        if version_of(entry):
            entries.append((t("word.version"), version_of(entry)))
        author = str((record or {}).get("author") or listed.get("author") or "")
        if author:
            entries.append((t("word.author"), author))
        if repository_of(entry):
            entries.append((t("word.links"), panel.links(
                (t("word.help"), help_of(entry)),
                (t("word.source_code"), repository_of(entry)))))
        if listed.get("updated"):
            entries.append((t("manager.extensions.updated"), when.ago(listed["updated"])))
        if listed.get("needs"):
            entries.append((t("manager.extensions.made_for"),
                            t("manager.extensions.made_for_value", version=listed["needs"])))
        if trust_state(entry) and (record or {}).get("path"):
            entries.append((t("manager.ext_page.folder_path"),
                            panel.path_value(str(record["path"]))))
        if record is not None:
            reached = ext_page.reported_rows(
                record, t("manager.ext_page.reach_help") if trust_state(entry)
                else t("manager.extensions.reach_listed")
                if not record.get("comes_with_vpinfe") else "")
            if reached:
                entries.append((panel.HEADING, t("manager.extensions.reach")))
                entries += reached
        foot = _foot(entry)
        if foot:
            entries.append(panel.note(foot))
        panel.facts(ui, entries)


def _foot(entry: dict[str, Any]) -> str:
    record = entry["record"]
    if record is None:
        return ""
    if record.get("comes_with_vpinfe"):
        return t("manager.extensions.shipped_foot")
    if not record.get("from_list"):
        return t("manager.extensions.folder_foot")
    return ""


async def settings_section(context: dict[str, Any]) -> None:
    record = context["extension"]["record"] or {}
    refill = refiller(context)
    found = await ext_page.read(record)
    with ui.column().classes("gap-0 manager-form w-full"):
        panel.facts(ui, ext_page.settings_entries(record, found, refill))


def has_settings(context: dict[str, Any]) -> bool:
    return bool((context["extension"]["record"] or {}).get("has_settings"))


async def actions_section(context: dict[str, Any]) -> None:
    record = context["extension"]["record"] or {}
    with ui.column().classes("gap-0 manager-form w-full"):
        panel.facts(ui, ext_page.action_entries(record, refiller(context)))


def has_actions(context: dict[str, Any]) -> bool:
    return bool(ext_page.unplaced(context["extension"]["record"] or {}))


def _updates_value(library: Library, entry: dict[str, Any], again: Callable[[], Any],
                   check: Callable[[], Any]) -> Callable[[], Any] | str:
    record, listed = entry["record"], entry["listed"] or {}
    if record is None:
        def install() -> None:
            panel.action(t("manager.extensions.install"),
                         lambda: _each(entry_run(library, "install_extension"), [entry],
                                       "manager.extensions.installed",
                                       "manager.extensions.install_failed", again),
                         icon=verbs.FETCH, inline=True,
                         hint=t("manager.extensions.install.help"))()
        return install
    if listed.get("update"):
        def newer() -> None:
            with ui.row().classes("items-center gap-2 no-wrap"):
                panel.state(t("manager.extensions.update_available"), "warn")()
                panel.action(t("manager.extensions.update_to", version=listed.get("version")),
                             lambda: _each(entry_run(library, "install_extension"), [entry],
                                           "manager.extensions.updated_to",
                                           "manager.extensions.update_failed", again),
                             icon=verbs.UPDATE, inline=True, hint=panel.NO_HINT)()
        return newer
    if listed:
        return t("manager.extensions.current")
    if entry["checked"]:
        return t("manager.extensions.not_listed")

    def ask() -> None:
        with ui.row().classes("items-center gap-2 no-wrap"):
            panel.action(t("manager.extensions.check_updates"), check, icon=verbs.REFRESH,
                         inline=True, hint=panel.NO_HINT)()
    return ask


def _trust_value(entry: dict[str, Any]) -> Callable[[], Any] | str:
    record = entry["record"] or {}
    kind = trust_state(entry)
    if not kind:
        return t("manager.extensions.trust_shipped") if record.get("comes_with_vpinfe") \
            else t("manager.extensions.trust_listed")
    if record.get("left_list"):
        where = t("manager.extensions.trust_left")
    elif record.get("origin") == accepted.CHANGED:
        where = t("manager.extensions.trust_changed")
    elif kind == COPY:
        where = t("manager.extensions.trust_copy")
    else:
        where = t("manager.extensions.trust_folder")
    return panel.state(TRUST.word(kind), _PANEL_TIER[kind], beside=where,
                       hint=t("manager.ext_page.unofficial_tip"), glyph=ext_mark.GLYPH)


# -- acts ----------------------------------------------------------------------------


def acts(library: Library, entries: list[dict[str, Any]],
         after: Callable[[], Any]) -> list[panel.Verb]:
    """What can be done to these extensions: what the list can give, and what can be taken
    away. An extension nothing offered it for is turned off with its switch, not here."""
    absent = [one for one in entries if one["record"] is None and one["listed"]]
    newer = [one for one in entries if one["record"] and (one["listed"] or {}).get("update")
             and _official(one["record"])]
    broken = [one for one in entries if status(one) == CHANGED and one["listed"]]
    installed = [one for one in entries if one["record"] and one["record"].get("from_list")]
    reverting = [one for one in installed if one["record"].get("comes_with_vpinfe")]
    removable = [one for one in installed if one not in reverting]
    install = entry_run(library, "install_extension")
    offered: list[panel.Verb] = []
    if absent:
        offered.append(panel.Verb(
            t("manager.extensions.install"),
            lambda: _each(install, absent, "manager.extensions.installed",
                          "manager.extensions.install_failed", after), icon=verbs.FETCH))
    if newer:
        offered.append(panel.Verb(
            t("manager.extensions.update"),
            lambda: _each(install, newer, "manager.extensions.updated_to",
                          "manager.extensions.update_failed", after), icon=verbs.UPDATE))
    if broken:
        offered.append(panel.Verb(
            t("manager.extensions.reinstall"),
            lambda: _each(install, broken, "manager.extensions.reinstalled",
                          "manager.extensions.reinstall_failed", after), icon=verbs.FETCH))
    if reverting:
        offered.append(panel.Verb(t("manager.extensions.use_shipped"),
                                  lambda: use_shipped(library, reverting, after),
                                  icon=verbs.UNINSTALL, danger=True))
    if removable:
        offered.append(panel.Verb(t("word.uninstall"),
                                  lambda: uninstall(library, removable, after), danger=True))
    return offered


@on_page
async def _each(call: Callable[[str], Any], entries: list[dict[str, Any]], done_key: str,
                failed_key: str, again: Callable[[], Any]) -> None:
    """Each in turn; one that fails says so and the rest go on. What was changed takes
    effect the next time VPinFE starts."""
    done: list[str] = []
    failed = Failures()
    for entry in entries:
        name = shown(entry)
        working = ui.notification(t("manager.extensions.working", name=name), spinner=True,
                                  timeout=None)
        try:
            await run.io_bound(call, entry["name"])
        except Exception as exc:  # noqa: BLE001
            failed.add(name, why(exc))
            continue
        finally:
            working.dismiss()
        done.append(name)
    failed.say(failed_key)
    if done:
        ui.notify(t(done_key, count=len(done), name=done[0]), type="positive")
        await again()


@on_page
async def uninstall(library: Library, entries: list[dict[str, Any]],
                    again: Callable[[], Any]) -> None:
    if not await confirm.ask(
            t("manager.extensions.uninstall", name=shown(entries[0])) if len(entries) == 1
            else t("manager.extensions.uninstall_many", count=len(entries)),
            detail=t("manager.extensions.uninstall_detail", count=len(entries)),
            confirm=t("word.uninstall"), icon=verbs.UNINSTALL):
        return
    gone: list[str] = []
    failed = Failures()
    for entry in entries:
        try:
            await run.io_bound(library.remove_extension, entry["name"])
        except Exception as exc:  # noqa: BLE001
            failed.add(shown(entry), why(exc))
            continue
        gone.append(shown(entry))
    failed.say("manager.extensions.uninstall_failed")
    if gone:
        ui.notify(t("manager.extensions.uninstalled", count=len(gone), name=gone[0]),
                  type="positive")
        await again()


@on_page
async def use_shipped(library: Library, entries: list[dict[str, Any]],
                      again: Callable[[], Any]) -> None:
    """Drop the update installed from the list, so the copy that came with VPinFE runs."""
    if not await confirm.ask(
            t("manager.extensions.use_shipped_question", count=len(entries),
              name=shown(entries[0])),
            detail=t("manager.extensions.use_shipped_detail", count=len(entries)),
            confirm=t("manager.extensions.use_shipped_confirm"), icon=verbs.UNINSTALL):
        return
    gone: list[str] = []
    failed = Failures()
    for entry in entries:
        try:
            await run.io_bound(library.remove_extension, entry["name"])
        except Exception as exc:  # noqa: BLE001
            failed.add(shown(entry), why(exc))
            continue
        gone.append(shown(entry))
    failed.say("manager.extensions.use_shipped_failed")
    if gone:
        ui.notify(t("manager.extensions.use_shipped_done", count=len(gone), name=gone[0]),
                  type="positive")
        await again()


@on_page
async def update_all(library: Library, again: Callable[[], Any]) -> None:
    """Every update the list has, for Needs You: the same act as Update on each row."""
    working = ui.notification(t("manager.extensions.working_many"), spinner=True, timeout=None)
    try:
        await run.io_bound(library.update_extensions)
    except Exception as exc:  # noqa: BLE001
        ui.notify(t("manager.extensions.update_all_failed"), caption=why(exc), type="negative")
        return
    finally:
        working.dismiss()
    ui.notify(t("manager.extensions.updated_all"), type="positive")
    await again()


UPDATE_ALL, REINSTALL, UNINSTALL = "update_all", "reinstall", "uninstall"


@on_page
async def run_fix(library: Library, action: str, name: str, again: Callable[[], Any]) -> None:
    """A Needs You row's button: the same act as the page's, on the extension it names."""
    if action == UPDATE_ALL:
        await update_all(library, again)
        return
    try:
        entries, _listing = await load(library)
    except Exception as exc:  # noqa: BLE001 - VPinFE may be restarting; say why
        ui.notify(t("manager.extensions.read_failed"), caption=why(exc), type="negative")
        return
    entry = next((one for one in entries if one["name"] == name), None)
    if entry is None:
        return
    if action == REINSTALL:
        await _each(entry_run(library, "install_extension"), [entry],
                    "manager.extensions.reinstalled", "manager.extensions.reinstall_failed",
                    again)
    elif action == UNINSTALL:
        await uninstall(library, [entry], again)
