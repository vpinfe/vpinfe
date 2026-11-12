"""Launchers: which ways this install runs a table, and one of them open beside it.

A grid with a workbench, the shape Devices uses, and for the reason Devices uses it: one
launcher is deep. Its own seven fields are the least of it - an app declares its whole
settings surface, which for Visual Pinball is around twelve hundred keys in named groups,
and that is more inside one object than anything else in the Manager holds. A section rail
is how the Manager shows what is inside one thing, and without one the seven fields
somebody actually came to change sit at the top of a thousand-row scroll.

The grid answers how many tables each launcher plays, and says a state only on the ones
that cannot play them. It knows because each row carries what the disk made of the
program it names.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Any

from nicegui import run, ui

from common.backups import copies as kept_copies
from common.config import path_state
from common.devices import install_identity
from common.failures import why
from common.i18n import t
from manager.backups import backup_rows
from manager.frontend import settings_file_choice as file_choice
from manager.kit import (
    busy,
    confirm,
    folder_picker,
    grid,
    offload,
    panel,
    states,
    undo,
    verbs,
    views,
    walks,
)
from manager.kit import dialog as frame
from manager.kit.data import Library
from manager.kit.failed_together import Failures
from manager.kit.on_page import on_page

logger = logging.getLogger("vpinfe.manager.frontend.launchers")

SCOPE = "manager.launchers.columns"

STATE_READY, STATE_OFF, STATE_MISSING, STATE_NO_PROGRAM = "ready", "off", "missing", "no_program"

# Switched Off is a choice; the two below it are launchers that cannot run their tables.
STATES = states.Table(
    covers=t("manager.launchers.state.help"),
    states=(
        states.State(STATE_READY, t("manager.launchers.ready"),
                     t("manager.launchers.state.ready"), glyph=states.TICK),
        states.State(STATE_OFF, t("manager.launchers.switched_off"),
                     t("manager.launchers.state.off"), tone=states.QUIET),
        states.State(STATE_NO_PROGRAM, t("manager.launchers.no_program"),
                     t("manager.launchers.state.no_program"), tone=states.BAD),
        states.State(STATE_MISSING, t("manager.launchers.program_missing"),
                     t("manager.launchers.state.missing"), tone=states.BAD)))

_NUMERIC: dict[str, Any] = {"type": "numericColumn", "filter": "agNumberColumnFilter"}

COLUMNS: list[dict[str, Any]] = [
    grid.identifier("name", t("word.name"), 240, pinned="left", subtitle="app",
                    help=t("manager.launchers.name.help")),
    grid.column("tables", t("manager.launchers.tables"),
                help=t("manager.launchers.tables.help"), **_NUMERIC),
    STATES.column("state", t("word.state"), 150),
    grid.column("default", t("word.default"), 110,
                help=t("manager.launchers.default.help")),
    grid.column("app", t("word.runs"), 180, help=t("manager.launchers.app.help")),
    grid.column("program", t("word.program"), 420,
                help=t("manager.launchers.program.help")),
]

LAUNCHER_VIEWS: dict[str, list[str] | views.Preset] = {
    "manager.view.everything": views.Preset(
        columns=("name", "tables", "state", "default"),
        help=t("manager.view.launchers.help")),
}


def _program_check(one: dict) -> str:
    """What the disk made of the program it names, or "" where its app names none."""
    checks = one.get("checks") or {}
    return next((str((checks.get(field["key"]) or {}).get("state") or "")
                 for field in one.get("fields") or [] if field.get("path") == "exe"), "")


def state_of(one: dict) -> str:
    """The worse fact wins. Switched off is a choice somebody made; a launcher with no
    program to run is the one to say when a row is both."""
    program = _program_check(one)
    if program == path_state.UNSET:
        return STATE_NO_PROGRAM
    if program not in ("", path_state.OK):
        return STATE_MISSING
    return STATE_READY if one.get("enabled") else STATE_OFF


def rows(held: list[dict], defaults: dict,
         plays: dict[str, int] | None = None) -> list[dict[str, Any]]:
    return [{
        "id": one["launcher_id"],
        "name": one["display_name"],
        "tables": int((plays or {}).get(one["launcher_id"]) or 0),
        "app": one["app_name"],
        "state": state_of(one),
        # Blank on every other row rather than "No": a column that says the same thing
        # everywhere but once is a column about the exception.
        "default": t("word.default") if defaults.get(one["app"]) ==
                one["launcher_id"] else "",
        "program": str((one.get("settings") or {}).get("bin_path") or ""),
    } for one in held]


def marked(found: dict[str, Any]) -> list[dict[str, Any]]:
    """The launchers with `is_default` set on the one each app plays with unless a table
    names another."""
    defaults = set((found.get("defaults") or {}).values())
    return [{**one, "is_default": one["launcher_id"] in defaults}
            for one in found.get("launchers") or []]


def launcher_offer(held: list[dict], app: str, named: str = "") -> list[dict[str, Any]]:
    """What a table of `app` may be pointed at, in order: that app's launchers, then
    every other program's. A switched-off one is left out unless the table names it,
    since picking it would change nothing that happens."""
    shown = [one for one in held if one.get("enabled") or one["launcher_id"] == named]
    ordered = ([one for one in shown if one.get("app") == app]
               + [one for one in shown if one.get("app") != app])
    return [{"id": one["launcher_id"], "name": one["display_name"],
             "other": one.get("app") != app,
             "mark": (STATES.word(STATE_OFF) if not one.get("enabled")
                      else t("word.default") if one.get("app") == app and one.get("is_default")
                      else "")}
            for one in ordered]


def build(library: Library, state: dict[str, Any],
          on_select: Callable[[dict | None], Any],
          redraw: Callable[[], None]) -> None:
    """The grid. Read on every draw, because this page edits it and what the disk says
    can change without anybody editing anything."""
    body = ui.column().classes("w-full grow min-h-0 gap-0")
    # Later, because reading goes over HTTP and the draw it is part of runs on the event
    # loop, where the client refuses a call.
    busy.fill(body, lambda: _fill(library, state, on_select, redraw, body))


async def _fill(library: Library, state: dict[str, Any], on_select: Callable[[dict | None], Any],
                redraw: Callable[[], None], body: Any) -> None:
    state["show_launcher"] = on_select
    try:
        found = await offload.io(library.launchers)
    except Exception as exc:  # noqa: BLE001 - this page says why, never 500s
        with body:
            panel.facts(ui, [panel.intro(t("manager.launchers.launchers_failed"),
                                         hint=why(exc))])
        return

    # Imported here: `workbench.launcher` imports this module, and `games` imports
    # `workbench.all_settings`, so reaching for it at the top would close the loop.
    from manager.library.games import view_control
    from manager.workbench import all_settings

    held = list(found.get("launchers") or [])
    apps_known = list(found.get("apps") or [])
    playing = {"now": await offload.io(all_settings._playing, library)}
    built = rows(held, dict(found.get("defaults") or {}), dict(found.get("tables") or {}))
    fields = [definition["field"] for definition in COLUMNS]

    async def playing_now() -> None:
        playing["now"] = await offload.io(all_settings._playing, library)

    with body:
        with ui.row().classes("w-full items-center gap-2 px-3 py-2 mb-2 shrink-0 "
                              "manager-panel manager-grid-bar"):
            bar = panel.grid_bar()
            wire_views, _picker, showing, describe = view_control(
                library, SCOPE, LAUNCHER_VIEWS, fields, COLUMNS, bar=bar)
            describe()
            with bar.top, panel.bar_end():
                search = panel.search(t("manager.launchers.search_launchers"))
            with bar.bottom, panel.bar_end():
                count = ui.label(t("manager.launchers.launcher", count=len(built))) \
                    .classes("text-xs manager-label")
                show_selection = panel.selection_menu(
                    t("manager.launchers.launchers_menu"), lambda: grid.selection(table),
                    lambda chosen: acts_on(chosen),
                    lambda: table.run_grid_method("deselectAll"), ready=playing_now)
                panel.add_action(
                    [(t("manager.launchers.add", app=one["name"]),
                      (lambda a=one: _add(library, state, redraw, a)))
                     for one in apps_known],
                    empty=not built, heading=t("manager.launchers.new_launcher"))

        if not built:
            panel.facts(ui, [panel.intro(
                t("manager.launchers.empty"))])
            return

        by_id = {row["id"]: row for row in built}
        by_launcher = {one["launcher_id"]: one for one in marked(found)}
        grid.on_row_focus(SCOPE,
                          lambda event: on_select(by_id.get(grid.focused_row(event))))

        async def copied() -> None:
            current = by_id.get(str(state.get("launcher") or ""))
            if current is None:
                return
            answer = on_select(current)
            if inspect.isawaitable(answer):
                await answer

        def acts_on(rows_on: list[dict[str, Any]]) -> list[panel.Verb]:
            chosen = [by_launcher[str(one["id"])] for one in rows_on
                      if str(one["id"]) in by_launcher]
            if not chosen:
                return []
            return acts(library, state, chosen, len(by_launcher), redraw, after=copied,
                        playing=playing["now"])

        picked: list[dict[str, Any]] = []
        shown = {"rows": len(built)}

        def said() -> str:
            if picked:
                return grid.selection_said(table, len(picked), t(
                    "manager.launchers.selected", picked=len(picked), shown=shown["rows"]))
            return t("manager.launchers.launcher", count=len(built))

        def on_selected(rows_selected: list[dict[str, Any]]) -> None:
            picked[:] = rows_selected
            show_selection(bool(rows_selected))
            count.text = said()

        async def counted() -> None:
            seen = await table.run_grid_method("getDisplayedRowCount")
            shown["rows"] = int(seen if isinstance(seen, int) else len(built))
            count.text = said()

        async def fill(row: dict | None) -> None:
            if row is None or str(row.get("id") or "") not in by_launcher:
                menu.clear()
                return
            await playing_now()
            on = grid.menu_rows(table, row)
            panel.verb_menu(
                menu, str(by_launcher[str(row["id"])]["display_name"]) if len(on) == 1
                else t("manager.launchers.launcher", count=len(on)),
                panel.for_selection(acts_on(on), len(on)))

        async def on_header_context(col_id: str | None) -> None:
            await grid.header_menu(menu, table, COLUMNS, col_id)

        with ui.element("div").classes("w-full grow min-h-0 flex flex-col"):
            table = grid.build(COLUMNS, built, SCOPE, on_selected, fill, on_header_context,
                               view_of=showing)
            menu = ui.context_menu()
        table.on("modelUpdated", counted)
        search.on_value_change(
            lambda: table.run_grid_method("setGridOption", "quickFilterText",
                                          search.value or ""))
        # After the grid exists: the widgets sit above it and the behavior needs it.
        wire_views(table)

        async def refresh_launchers() -> None:
            again = await offload.io(library.launchers)
            fresh_held = list(again.get("launchers") or [])
            by_launcher.clear()
            by_launcher.update({one["launcher_id"]: one for one in marked(again)})
            grid.replace_rows(table, built, by_id,
                              rows(fresh_held, dict(again.get("defaults") or {}),
                                   dict(again.get("tables") or {})),
                              lambda _row: True)
            count.text = said()

        state["refresh_launchers"] = refresh_launchers


def standing_option(field: dict[str, Any], app_name: str) -> dict[str, Any]:
    """A launcher field as the control reads it: a path left empty stands for the file
    the program uses, and the hover says whose it is."""
    if not field.get("default_path"):
        return field
    return {**field, "default_whose": t("manager.workbench.app_default", app=app_name)}


@on_page
async def _add(library: Library, state: dict[str, Any], redraw: Callable[[], None],
               app: dict) -> None:
    """A new launcher for an app: its name, the paths the app declares and where its
    settings come from."""
    from common.games import launcher_settings
    from common.games import launchers as model

    held = await offload.io(library.launchers)
    names = _names_of(held)
    asked = [one for one in app.get("fields") or []
             if one.get("path") and one["key"] != launcher_settings.KEY]
    made = model.mint_launcher_id()
    fallback = model.free_name(app["name"], names)
    places = await offload.io(library.launcher_settings_file_places, app["id"], {})

    def choosing(name: Callable[[], str]) -> file_choice.Choice:
        return file_choice.Choice(library, app=app, held=held, places=places,
                                  mode=file_choice.NEW, name=name, fallback=fallback)

    @on_page
    async def write(name: str, settings: dict[str, str],
                    choice: file_choice.Choice | None) -> bool:
        assert choice is not None
        shown = name or fallback
        answer = await choice.apply(made, create={
            "app": app["id"], "enabled": True, "settings": settings, "display_name": shown})
        if answer is None:
            return False
        file_choice.announce(library, answer, choice.how, file_choice.NEW,
                             after=_forgotten(state, redraw))
        return True

    async def standing(settings: dict[str, str]) -> dict[str, dict[str, Any]]:
        found = await offload.io(library.launcher_standing, app["id"], settings)
        return {key: standing_option(one, app["name"]) for key, one in found.items()}

    if await ask_name(t("manager.launchers.new_title", app=app["name"]), t("word.add"),
                      names, write, asked=asked, looking=library.folders, standing=standing,
                      placeholder=t("manager.launchers.name_example", app=app["name"]),
                      choosing=choosing):
        await _open(state, redraw, made)


def _forgotten(state: dict[str, Any], redraw: Callable[[], None]) -> Callable[[], Awaitable[Any]]:
    """What runs after an Undo of an add: the launcher is gone, so the grid is drawn again
    without it and the workbench is left empty."""
    async def run() -> None:
        state["launcher"] = ""
        redraw()
    return run


def _place(index: int, total: int) -> str:
    return t("manager.walk.position", index=index, total=total) if total > 1 else ""


@on_page
async def duplicate(library: Library, state: dict[str, Any], redraw: Callable[[], None],
                    launchers: list[dict]) -> None:
    """A copy of each, which is the case this feature exists for: change one thing -
    usually the Settings File - and you have a second way of running the same program.
    Over several it asks a name for each in turn.
    """
    from common.games import launchers as model

    held = await offload.io(library.launchers)
    names = _names_of(held)
    made: list[str] = []
    for index, launcher in enumerate(launchers, 1):
        new_id = model.mint_launcher_id()
        offered = model.free_name(
            t("manager.launchers.copy_of", name=launcher["display_name"]), names)
        app = {"id": launcher["app"], "name": launcher["app_name"]}
        places = await offload.io(library.launcher_settings_file_places, launcher["app"],
                                  dict(launcher.get("settings") or {}))

        def choosing(name: Callable[[], str], launcher: dict = launcher, app: dict = app,
                     places: list[dict] = places, offered: str = offered) -> file_choice.Choice:
            return file_choice.Choice(library, app=app, held=held, places=places,
                                      mode=file_choice.DUPLICATE, name=name,
                                      fallback=offered, launcher=launcher)

        @on_page
        async def write(name: str, _settings: dict[str, str],
                        choice: file_choice.Choice | None, original: dict = launcher,
                        new_id: str = new_id, offered: str = offered) -> bool:
            assert choice is not None
            shown = name or offered
            answer = await choice.apply(new_id, create={
                "app": original["app"], "enabled": bool(original.get("enabled", True)),
                "settings": dict(original.get("settings") or {}), "display_name": shown})
            if answer is None:
                return False
            names.append(shown)
            file_choice.announce(library, answer, choice.how, file_choice.DUPLICATE,
                                 after=_forgotten(state, redraw))
            return True

        answer = await ask_name(
            t("manager.launchers.duplicate_title", name=launcher["display_name"]),
            t("manager.workbench.duplicate"), names, write, named=offered,
            icon=verbs.DUPLICATE, place=_place(index, len(launchers)),
            walking=len(launchers) > 1, choosing=choosing)
        if answer == walks.STOPPED:
            break
        if answer:
            made.append(new_id)
    if len(made) == 1:
        await _open(state, redraw, made[0])
    elif made:
        redraw()


@on_page
async def change_file(library: Library, launcher: dict, changed: Callable[[], Awaitable[Any]],
                      ) -> None:
    """Give a launcher another settings file: the same choices as a new launcher, and Save To
    for where a new one goes. `changed` redraws whatever shows the file afterwards."""
    held = await offload.io(library.launchers)
    places = await offload.io(library.launcher_settings_file_places, launcher["app"],
                              dict(launcher.get("settings") or {}))
    name = str(launcher["display_name"])
    choice = file_choice.Choice(
        library, app={"id": launcher["app"], "name": launcher["app_name"]}, held=held,
        places=places, mode=file_choice.CHANGE, name=lambda: name, fallback=name,
        launcher=launcher)

    @on_page
    async def go() -> None:
        if choice.refuse():
            return
        try:
            answer = await choice.apply(launcher["launcher_id"])
        except Exception as exc:  # noqa: BLE001 - said where the person is looking
            ui.notify(t("manager.launchers.sf.change_failed"), caption=why(exc),
                      type="negative")
            return
        if answer is None:
            return
        box.submit(True)
        if answer.get("unchanged"):
            ui.notify(t("manager.launchers.sf.unchanged", name=name))
            return
        file_choice.announce(library, answer, choice.how, file_choice.CHANGE, after=changed)
        await changed()

    with frame.opened(t("manager.launchers.sf.change_title", name=name),
                      ignore_outside_click=True) as box:
        panel.facts(ui, choice.rows())
        with frame.footer():
            frame.leave(box, False)
            ok = frame.answer(t("word.change"), go, icon="swap_horiz")
    frame.enter_presses(ok)
    await box


async def open_launcher(state: dict[str, Any], launcher_id: str) -> None:
    """That launcher selected in the grid and open in the workbench."""
    state["launcher"] = launcher_id
    grid.land_on(SCOPE, {"id": launcher_id})
    await _show(state, launcher_id)


async def _show(state: dict[str, Any], launcher_id: str) -> None:
    state["launcher"] = launcher_id
    show = state.get("show_launcher")
    if callable(show):
        await show({"id": launcher_id})
    if callable(opened := state.get("open_pane")):
        opened()


async def _open(state: dict[str, Any], redraw: Callable[[], None], made: str) -> None:
    """The grid drawn again with it, and it in the workbench."""
    state["launcher"] = made
    grid.land_on(SCOPE, {"id": made})
    redraw()
    await _show(state, made)


async def ask_name(title: str, answer: str, names: list[str],
                   write: Callable[..., Awaitable[bool | None]], *,
                   named: str = "", asked: list[dict] | None = None,
                   placeholder: str = "", icon: str = verbs.CREATE,
                   looking: Callable[..., dict] | None = None,
                   standing: Callable[[dict[str, str]], Awaitable[dict[str, dict]]] | None = None,
                   place: str = "", walking: bool = False,
                   choosing: Callable[[Callable[[], str]], file_choice.Choice] | None = None,
                   ) -> bool | str:
    """Name a launcher, and fill the path fields `asked` lists, before it exists.

    `looking` lists a folder for the picker; without it the path fields are typed only.
    `standing` answers, for the paths as typed, what each field the app can name a file
    for would stand for left empty; a field it names shows that, and is asked again when
    another path changes.
    `place` says where a walk has got to, and `walking` splits Cancel into Skip and Stop.
    `choosing` is given the name as typed and answers the Settings File rows; `write` gets it
    as its third argument, None where there are none. `write` answers False where the
    person declined a question of its own, and the dialog stays open.

    True once `write` took it, False when skipped, `walks.STOPPED` when a walk is ended.
    A name already in use, or any refusal from `write`, is said on the Name field and the
    dialog stays open.
    """
    from common.games import launchers as model

    fields: dict[str, Any] = {}
    holders: dict[str, Any] = {}
    stands: dict[str, dict] = {}
    asking = [one["key"] for one in asked or ()]
    chosen = choosing(lambda: " ".join(str(fields["name"].value or "").split())) \
        if choosing else None

    def typed(key: str) -> str:
        text = str(fields[key].value or "").strip()
        return "" if text == (stands.get(key) or {}).get("default_path") else text

    def browsing(one: dict) -> Callable[[str], Awaitable[str | None]] | None:
        if not (kind := str(one.get("path") or "")) or looking is None:
            return None

        async def browsed(current: str) -> str | None:
            return await folder_picker.pick_path(
                looking, current, kind=kind, suffixes=tuple(one.get("suffixes") or ()))

        return browsed

    def draw(key: str, value: str = "", hint: str = "",
             browse: Callable[[str], Awaitable[str | None]] | None = None) -> Callable[[], None]:
        def drawn() -> None:
            fields[key] = frame.field(value, placeholder=hint, browse=browse)
        return drawn

    def paint(one: dict, value: str) -> None:
        key = one["key"]
        stood = stands.get(key) or {}
        extra: dict[str, Any] = {
            "default": str(stood.get("default_path") or ""),
            "whose": str(stood.get("default_whose") or ""),
            "left_empty": str(stood.get("left_empty") or "")} if stood else {}
        fields[key] = frame.field(
            value, placeholder=str(stood.get("blank") or one.get("blank") or ""),
            browse=browsing(one), **extra)

    def asked_field(one: dict) -> Callable[[], None]:
        def drawn() -> None:
            with ui.element("div").classes("contents") as holders[one["key"]]:
                paint(one, "")
            if stands and one["key"] not in stands:
                fields[one["key"]].on_value_change(refresh)
        return drawn

    latest = [0]

    async def refresh() -> None:
        """Ask again what the fields stand for once the paths stop changing."""
        latest[0] += 1
        mine = latest[0]
        await asyncio.sleep(0.3)
        if mine != latest[0] or standing is None or holders[asking[0]].is_deleted:
            return
        try:
            found = await standing({key: typed(key) for key in asking})
        except Exception:  # noqa: BLE001 - the fields keep what they showed
            logger.exception("what a launcher field stands for could not be read")
            return
        if mine != latest[0]:
            return
        for one in asked or ():
            key = one["key"]
            if found.get(key) != stands.get(key):
                now = typed(key)
                stands[key] = found.get(key) or {}
                holders[key].clear()
                with holders[key]:
                    paint(one, now)

    def refused(why: str) -> None:
        fields["name"].props["error"] = bool(why)
        fields["name"].props["error-message"] = why

    async def keep() -> None:
        name = " ".join(str(fields["name"].value or "").split())
        if any(model.same_name(name, one) for one in names):
            refused(t("error.launchers.name_taken", name=name))
            return
        if chosen is not None and chosen.refuse():
            return
        try:
            wrote = await write(name, {key: typed(key) for key in asking}, chosen)
        except Exception as exc:  # noqa: BLE001 - said where it can be put right
            refused(why(exc))
            return
        if wrote is False:
            return
        box.submit(True)

    if standing is not None and asked:
        try:
            stands.update(await standing({key: "" for key in asking}))
        except Exception:  # noqa: BLE001 - the fields draw as the app declares them
            logger.exception("what a launcher field stands for could not be read")

    with frame.opened(title, ignore_outside_click=True) as box:
        if place:
            ui.label(place).classes("manager-help px-3")
        panel.facts(ui, [(t("word.name"), draw("name", named, placeholder))]
                    + [(one["label"], asked_field(one)) for one in asked or ()]
                    + (chosen.rows() if chosen is not None else []))
        with frame.footer():
            frame.leave(box, walking)
            go = frame.answer(answer, keep, icon=icon)
    fields["name"].on_value_change(lambda: refused(""))
    if chosen is not None:
        fields["name"].on_value_change(chosen.follow_name)
    frame.focus(box, fields["name"], select=bool(named))
    frame.enter_presses(go)
    answered = await box
    return answered if answered == walks.STOPPED else bool(answered)


def _names_of(held: dict) -> list[str]:
    return [str(one.get("display_name") or "") for one in held.get("launchers") or []]


def copy_targets(known: list[dict], install_id: str) -> list[dict]:
    return [one for one in known
            if str(one.get("kind") or "vpinfe") == "vpinfe"
            and install_identity.FRONTEND in (one.get("features") or ())
            and str(one.get("device_id") or "") != install_id]


@on_page
async def copy_dialog(library: Library, state: dict[str, Any],
                      launchers: list[dict]) -> None:
    """Pick the devices, see what it will do, then do it.

    A copy with no ongoing link, which the dialog says rather than leaving somebody to
    find out: edit a cabinet's launcher afterwards and the two diverge.
    """
    try:
        known = await offload.io(library.devices)
    except Exception as exc:  # noqa: BLE001
        ui.notify(t("manager.launchers.devices_failed"), caption=why(exc),
                  type="negative")
        return
    reachable = copy_targets(known, str(state.get("install_id") or ""))
    if not reachable:
        ui.notify(t("manager.launchers.no_peers"), type="warning")
        return

    picked: set[str] = set()
    with frame.opened(
            t("manager.launchers.copy_title", name=launchers[0]["display_name"])
            if len(launchers) == 1
            else t("manager.launchers.copy_title_many", count=len(launchers))) as box:
        ui.label(t("manager.launchers.copy_note")).classes("manager-help px-3")
        with ui.column().classes("gap-1 px-3"):
            for one in reachable:
                name = str(one.get("display_name") or one.get("device_id"))
                ui.checkbox(name, on_change=lambda e, d=one: (
                    picked.add(str(d.get("device_id"))) if e.value
                    else picked.discard(str(d.get("device_id"))))) \
                    .props("dense")
            also = ui.checkbox(t("manager.launchers.copy_usage")).props("dense")
        ui.label(t("manager.launchers.copy_diverge")).classes("manager-help px-3")
        with frame.footer():
            frame.cancel(lambda: box.submit(None))
            frame.answer(t("word.copy"), lambda: box.submit(True), icon=verbs.COPY)

    if not await box:
        return
    if not picked:
        ui.notify(t("manager.launchers.none_picked"), type="warning")
        return
    await _do_copy(library, launchers, [one for one in reachable
                                       if str(one.get("device_id")) in picked],
                   bool(also.value))


@on_page
async def _do_copy(library: Library, launchers: list[dict], devices: list[dict],
                   with_mappings: bool) -> None:
    from common.games import launcher_copy

    mappings = {}
    if with_mappings:
        try:
            found = await offload.io(library.launchers)
            ids = {str(one["launcher_id"]) for one in launchers}
            mappings = {table: to for table, to in (found.get("mappings") or {}).items()
                        if to in ids}
        except Exception as exc:  # noqa: BLE001
            ui.notify(t("manager.launchers.assignments_failed"), caption=why(exc),
                      type="negative")
            return

    def client_for(device: Any) -> Any:
        from common.devices import device_client

        return device_client.for_device(device)

    outcomes = await offload.io(launcher_copy.copy_to, devices, launchers,
                                  mappings, client_for=client_for)
    ui.notify(launcher_copy.said(outcomes), caption=launcher_copy.trouble(outcomes),
              type="positive" if all(one.ok for one in outcomes) else "warning")


def keeps_settings(launcher: dict) -> bool:
    """Whether there is a settings file here to copy and put back: the app has one and
    its program is on this machine."""
    checks = launcher.get("checks") or {}
    return bool(launcher.get("has_config")) \
        and (checks.get("bin_path") or {}).get("state") == path_state.OK


def copy_verbs(library: Library, launchers: list[dict],
               after: Callable[[], Awaitable[Any]], *,
               playing: bool = False) -> list[panel.Verb]:
    """Back up now and Restore..., for the settings file each launcher's program writes.
    Both ask about each launcher in turn.

    `playing` is what the caller knew when it drew the menu; a menu outlives it, so the
    click asks again."""
    from manager.workbench import all_settings

    def guarded(act: Callable[[dict, str, bool], Awaitable[Any]]) -> Callable[[], Any]:
        @on_page
        async def run_it() -> None:
            if await offload.io(all_settings._playing, library):
                ui.notify(t(all_settings.PLAYING_NOTE), caption=t(all_settings.PLAYING_WHY),
                          type="warning")
                return
            for index, launcher in enumerate(launchers, 1):
                if await act(launcher, _place(index, len(launchers)),
                             len(launchers) > 1) == walks.STOPPED:
                    break
        return run_it

    async def take(launcher: dict, place: str, walking: bool) -> Any:
        launcher_id = str(launcher["launcher_id"])
        return await backup_rows.back_up(
            library, kept_copies.SUBJECT_LAUNCHER, launcher_id,
            str(launcher.get("display_name") or launcher_id), after, place=place,
            walking=walking)

    async def choose(launcher: dict, place: str, walking: bool) -> Any:
        launcher_id = str(launcher["launcher_id"])
        return await backup_rows.open_for(
            library, kept_copies.SUBJECT_LAUNCHER, launcher_id,
            str(launcher.get("display_name") or launcher_id), after, place=place,
            walking=walking)

    hint = t(all_settings.PLAYING_NOTE) if playing else ""
    return [panel.Verb(t("manager.backups.take_now"),
                       None if playing else guarded(take), hint=hint),
            panel.Verb(t("manager.backups.restore_dialog"),
                       None if playing else guarded(choose), hint=hint)]


def acts(library: Library, state: dict[str, Any], launchers: list[dict], count: int,
         redraw: Callable[[], None], *, after: Callable[[], Awaitable[Any]] | None = None,
         playing: bool = False) -> list[panel.Verb]:
    """What can be done to these launchers, of the `count` there are. With `after`, also
    what can be done to their programs' settings files, for those that have one."""
    several = len(launchers) > 1
    offered: list[panel.Verb] = []
    if several or not launchers[0].get("is_default"):
        offered.append(_default_verb(library, state, launchers, after))
    offered.append(panel.Verb(t("manager.launchers.duplicate_menu"),
                              lambda: duplicate(library, state, redraw, launchers),
                              group=_ABOUT_IT))
    if state.get("can_manage_devices"):
        offered.append(panel.Verb(t("manager.workbench.copy_devices"),
                                  lambda: copy_dialog(library, state, launchers),
                                  group=_ABOUT_IT))
    keeping = [one for one in launchers if keeps_settings(one)]
    if after is not None and keeping:
        offered += [replace(one, group=_ABOUT_IT)
                    for one in copy_verbs(library, keeping, after, playing=playing)]
    offered.append(panel.Verb(
        t("word.remove"),
        None if count <= len(launchers) else (lambda: remove(library, state, redraw, launchers)),
        danger=True,
        hint=t("manager.launchers.keep_one" if several else "manager.workbench.only_launcher")))
    return offered


_DEFAULT, _ABOUT_IT = "default", "about-it"


def _default_verb(library: Library, state: dict[str, Any], launchers: list[dict],
                  after: Callable[[], Awaitable[Any]] | None) -> panel.Verb:
    """Make Default, alone at the top of the menu. Dimmed with the reason behind an info
    mark where the launcher cannot be one; the selection's own dimming drops the mark."""
    from manager.workbench import all_settings

    label = t("word.make_default")
    first = launchers[0]
    refused = all_settings._why_not_the_default(first)
    if refused:
        return panel.Verb(label, hint=refused, one=True, group=_DEFAULT)
    return panel.Verb(label, lambda: make_default(library, state, first, after), one=True,
                      group=_DEFAULT)




@on_page
async def make_default(library: Library, state: dict[str, Any], launcher: dict,
                       after: Callable[[], Awaitable[Any]] | None = None) -> None:
    """The one act behind the menu's Make Default and the workbench's Default switch."""
    try:
        done = await offload.io(library.make_launcher_default, launcher["launcher_id"])
    except Exception as exc:  # noqa: BLE001
        ui.notify(t("manager.workbench.save_failed"), caption=why(exc), type="negative")
        return

    async def settle() -> None:
        refresh = state.get("refresh_launchers")
        if callable(refresh):
            await refresh()
        if after is not None:
            await after()

    async def reverse() -> None:
        await offload.io(library.restore_launcher_order, list(done.get("was") or []))
        await settle()

    undo.offer(t("manager.launchers.made_default", name=launcher["display_name"],
                 app=launcher.get("app_name") or ""), reverse)
    await settle()


def removal_words(found: dict[str, Any]) -> tuple[str, list[str]]:
    """The confirm's detail and lines, from what switching the launcher off would do,
    which is what removing it does to its tables."""
    kept = t("manager.launchers.remove_keeps_files")
    count = int(found.get("tables") or 0)
    goes = list(found.get("fallbacks") or [])
    if not count:
        return kept, []
    if len(goes) == 1:
        name = goes[0].get("display_name")
        said = (t("manager.launchers.remove_moves_to", count=count, fallback=name) if name
                else t("manager.launchers.remove_strands", count=count))
        return t("manager.launchers.remove_detail", tables=said, kept=kept), []
    return (t("manager.launchers.remove_split_detail", kept=kept,
              tables=t("manager.launchers.remove_split", count=count)),
            [t("manager.workbench.launch_count", count=int(one["tables"]),
               name=one["display_name"]) if one.get("display_name")
             else t("manager.launchers.unassigned", count=int(one["tables"]))
             for one in goes])


def removal_words_many(found: list[dict[str, Any]]) -> tuple[str, list[str]]:
    """The confirm's detail for several launchers at once: what they keep, and how many
    tables lose theirs."""
    kept = t("manager.launchers.remove_keeps_files")
    count = sum(int(one.get("tables") or 0) for one in found)
    if not count:
        return kept, []
    return t("manager.launchers.remove_detail", kept=kept,
             tables=t("manager.launchers.remove_many_tables", count=count)), []


@on_page
async def remove(library: Library, state: dict[str, Any], redraw: Callable[[], None],
                 launchers: list[dict]) -> None:
    """Asked about first, because it is the destructive one and it takes assignments
    with it - a table pointing here goes back to the default."""
    try:
        found = [await offload.io(library.launcher_fallback, one["launcher_id"])
                 for one in launchers]
    except Exception as exc:  # noqa: BLE001
        ui.notify(t("manager.workbench.lookup_failed"), caption=why(exc), type="negative")
        return
    if len(launchers) == 1:
        detail, lines = removal_words(found[0])
        title = t("manager.launchers.remove", name=launchers[0]["display_name"])
    else:
        detail, lines = removal_words_many(found)
        title = t("manager.launchers.remove_many", count=len(launchers))
    own_files = [one["launcher_id"] for one in launchers if one.get("offer_delete")]
    offered = confirm.Option(t("manager.launchers.sf.remove_file", count=len(own_files))) \
        if own_files else None
    if not await confirm.ask(title, detail=detail, lines=lines, confirm=t("word.remove"),
                             icon=verbs.REMOVE, option=offered):
        return
    failed = Failures()
    for launcher in launchers:
        try:
            if offered is not None and offered.on and launcher["launcher_id"] in own_files:
                await run.io_bound(library.delete_launcher_own_file, launcher["launcher_id"])
            await run.io_bound(library.delete_launcher, launcher["launcher_id"])
        except Exception as exc:  # noqa: BLE001
            failed.add(launcher["display_name"], why(exc))
    failed.say("manager.launchers.remove_failed")
    state["launcher"] = ""
    redraw()
