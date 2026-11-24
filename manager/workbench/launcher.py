"""The launcher subject."""

from __future__ import annotations

import html
import logging
from collections.abc import Callable
from functools import partial
from typing import Any

from nicegui import run, ui

from common import apps, tokens
from common.config import path_state
from common.failures import why
from common.i18n import t
from manager.backups import backup_rows
from manager.frontend import launchers as launchers_page
from manager.kit import deeplink, game_tables, offload, panel, table_features, verbs
from manager.kit.data import Library
from manager.kit.on_page import on_page
from manager.settings import commands as commands_help
from manager.settings import settings as settings_page
from manager.workbench.all_settings import (
    CAME_FROM,
    COMMANDS_FROM,
    PLAYING_NOTE,
    _agreed_to_switch_off,
    _program_is_there,
    _program_note,
    _switched_off_goes_to,
    _why_not_the_default,
    awaiting_file,
)
from manager.workbench.core import HEADING, _choose, _rows, _save_overrides, _switch

logger = logging.getLogger("vpinfe.manager.workbench.launcher")

SETTINGS_FILE = "ini_path"


def _play_action(context: dict[str, Any], table: dict[str, Any]) -> Callable[[], None]:
    """Play this one, not the game's default - the panel is about this entry."""
    filename = game_tables.native_key(table)

    @on_page
    async def go() -> None:
        try:
            await run.io_bound(context["library"].launch, context["game_id"], filename)
        except Exception as exc:
            ui.notify(t("manager.workbench.launch_failed"), caption=why(exc), type="negative")

    def draw() -> None:
        with ui.row().classes("items-center gap-2"):
            button = ui.button(t("manager.workbench.play_table"), icon="play_arrow",
                    on_click=go) \
                .props("flat dense no-caps size=sm").classes("manager-action")
            if not table.get("available"):
                button.disable()
                button.tooltip(t("manager.workbench.vpx_absent"))

    return draw


def launcher_rows(context: dict[str, Any], table: dict[str, Any]) -> list[tuple[Any, Any]]:
    """Which launcher plays this table and what it does after. Neither has a discovered
    value - each falls back to what applies everywhere - so empty is the revert."""
    overrides = table.get("overrides") or {}

    async def toggle(event: Any) -> None:
        await _save_overrides(context, {"delete_nvram_on_close": bool(event.value)},
                              table=True)

    def nvram() -> None:
        _switch(bool(overrides.get("delete_nvram_on_close")), toggle,
                hint=t("manager.workbench.nvram_note"))

    return [
        (t("manager.workbench.launcher"), _launcher_pick(context, table)),
        *_launcher_notes(context, table),
        (t("manager.workbench.nvram_clear"), nvram),
    ]


def _launcher_report(context: dict[str, Any], table: dict[str, Any]) -> tuple[Any, Any]:
    """The launcher in one line, and a way to its settings."""
    name = str(table.get("launcher_name") or "")
    own = int(table.get("launcher_settings_here") or 0)
    from_game = int(table.get("launcher_settings_from_folder") or 0)
    configurable = bool(table.get("launcher_app_configurable"))
    said = (t("manager.workbench.launcher_own", name=name, count=own)
            if name and configurable and own
            else t("manager.workbench.launcher_from_game", name=name, count=from_game,
                   scope=t(CAME_FROM["folder"]))
            if name and configurable and from_game
            else name or "-")
    to = "/manager?" + deeplink.query({**context["state"], "section": "table_settings"})
    return (t("manager.workbench.launcher"),
            panel.link(said, to=to, on_click=partial(_choose, context, "table_settings")))


def _launcher_notes(context: dict[str, Any], table: dict[str, Any]) -> list[tuple[Any, Any]]:
    """What the picker cannot say itself: that there is nothing to pick, or that the
    program playing this table does without what the table's own app knows about it."""
    listing = context.get("launchers") or {}
    held = listing.get("launchers") or []
    if not held:
        return [panel.note(t("manager.workbench.launcher_hint"))]
    runs = next((one for one in held if one["launcher_id"] == table.get("launcher")), None)
    own: dict[str, Any] = next((one for one in listing.get("apps") or []
                if one.get("id") == table.get("app")), {})
    if runs is None or runs.get("app") == table.get("app") or not own.get("has_config"):
        return []
    return [panel.note(t("manager.workbench.runs_with_other",
                         app=runs.get("app_name") or "", own=own.get("name") or ""))]


def _launcher_pick(context: dict[str, Any], table: dict[str, Any]) -> Callable[[], None]:
    """Which launcher plays this table, chosen from the ones this install has.

    A picker, not a path. A free-text field asks somebody to type a binary that nothing
    validates, and every other table would have to be told about it separately.

    It shows the launcher that plays the table. One the table chose carries a dot and a
    Clear, and clearing is how it goes back to the default.
    """
    table_id = str(table.get("id") or "")
    listing = context.get("launchers") or {}
    held = listing.get("launchers") or []
    known = {one["launcher_id"]: one for one in held}
    named = str((listing.get("mappings") or {}).get(table_id) or "")
    named = named if named in known else ""
    offer = launchers_page.launcher_offer(held, str(table.get("app") or ""), named)
    current = named or str(table.get("launcher") or "")

    def draw() -> None:
        field = panel.MarkedSelect(
            {one["id"]: one["name"] for one in offer},
            value=current if any(one["id"] == current for one in offer) else None,
            marks={one["id"]: one["mark"] for one in offer},
            heading=t("manager.workbench.other_programs"),
            heading_at=next((one["id"] for one in offer if one["other"]), None)) \
            .classes("w-full min-w-0")
        if named:
            field.props("clearable")
            off = not known[named].get("enabled")
            with field.add_slot("prepend"):
                ui.element("span").classes(
                    "manager-mark manager-mark--full manager-named-mark"
                    + (" manager-named-mark--off" if off else "")).tooltip(
                    t("manager.workbench.named_off", name=known[named]["display_name"],
                      fallback=str(table.get("launcher_name") or "")) if off
                    else t("manager.workbench.chosen_here"))
        if not held:
            field.disable()
            field.tooltip(t("manager.workbench.no_launcher"))

        @on_page
        async def changed() -> None:
            try:
                await run.io_bound(context["library"].assign_launcher,
                                   table_id, str(field.value or ""))
            except Exception as exc:
                ui.notify(t("manager.workbench.launcher_failed"),
                          caption=why(exc), type="negative")
                return
            await (context.get("rebuild") or context["saved"])()

        field.on_value_change(changed)

    return draw


def _launchers_for_panel(library: Library) -> dict[str, Any]:
    """This install's launchers with the default marked, beside what each table names
    and what each app has. Runs off the event loop.

    Empty where the read fails: the panel is about a table, and a launcher list that
    would not load is a disabled picker with a reason rather than a panel that does not
    draw.
    """
    try:
        found = library.launchers()
    except Exception:
        logger.exception("Could not read the launchers for the table panel")
        return {}
    return {**found, "launchers": launchers_page.marked(found)}


# Named for the thing, not for the .info key. PinMAME is left out: it is not a script
# flourish like the rest, and the ROM row above already answers for it in detail.
def _feature_chips(features: dict[str, Any]) -> None:
    """What the table's script was seen to use, named where there is room for names -
    the grid draws the same three states as marks."""
    with ui.element("div").classes("manager-chips"):
        for key, label in table_features.LABELS.items():
            state = table_features.state_of(features.get(key))
            ui.label(label).classes(f"manager-tier {state.chip}").tooltip(state.noun)


async def _launcher_details(context: dict[str, Any]) -> None:
    """What this launcher is, and what it runs. Its own fields, which are few - the
    program's settings are the sections after this one."""
    library = context["library"]
    launcher = context["launcher"]
    rebuild = context["rebuild"]
    only_one = len(context.get("launchers") or []) <= 1
    is_default = context.get("defaults", {}).get(launcher["app"]) == launcher["launcher_id"]
    refused_default = _why_not_the_default(launcher)

    @ui.refreshable
    def command_line() -> None:
        _command_line(launcher.get("command_line"))

    async def put(changes: dict[str, Any]) -> str:
        """Empty when it saved, else why it did not."""
        try:
            await run.io_bound(library.put_launcher, launcher["launcher_id"],
                               {**launcher, **changes})
        except Exception as exc:  # noqa: BLE001
            return why(exc)
        launcher.update(changes)
        for after in ("recheck_trouble", "refresh_launchers"):
            again = context["state"].get(after)
            if callable(again):
                await again()
        if "settings" in changes:
            await read_back_command_line()
        return ""

    async def read_back_command_line() -> None:
        """Built where the launcher runs, so it is read back rather than worked out here."""
        try:
            fresh = await offload.io(library.launchers)
        except Exception:  # noqa: BLE001 - it saved; the old Command Line would be wrong
            logger.exception("Could not read back what %s runs", launcher["display_name"])
            fresh = {}
        launcher["command_line"] = next(
            (one.get("command_line") for one in fresh.get("launchers") or []
             if one.get("launcher_id") == launcher["launcher_id"]), None)
        command_line.refresh()

    @on_page
    async def write(**changes: Any) -> bool:
        refused = await put(changes)
        if refused:
            ui.notify(t("manager.workbench.save_failed"), caption=refused, type="negative")
        return not refused

    async def rename(text: str) -> str:
        wanted = text.strip() or launcher["app_name"]
        was = launcher["display_name"]
        if wanted == was:
            return ""
        refused = await put({"display_name": wanted})
        retitle = context.get("retitle")
        if not refused and callable(retitle):
            retitle(wanted)
        if not refused:
            await follow_rename(was)
        return refused

    async def follow_rename(was: str) -> None:
        """A file VPinFE keeps for this launcher takes the launcher's new name."""
        try:
            followed = await offload.io(library.launcher_was_renamed, launcher["launcher_id"],
                                        was)
        except Exception:  # noqa: BLE001 - the launcher is renamed; its file keeps its name
            logger.exception("Could not rename the settings file of %s", was)
            return
        if followed.get("renamed"):
            launcher["settings"] = {**launcher["settings"], SETTINGS_FILE: followed["file"]}
            for shown in file_regions:
                shown.refill()
            await read_back_command_line()

    @on_page
    async def flip(on: bool) -> None:
        if not on and not await _agreed_to_switch_off(library, launcher):
            await rebuild()
            return
        if await write(enabled=on):
            await rebuild()

    @on_page
    async def make_default(on: bool) -> None:
        if on and not is_default:
            await launchers_page.make_default(library, context["state"], launcher, rebuild)
        else:
            await rebuild()

    async def choose_file(_current: str) -> None:
        """The folder button on Settings File: its own dialog, never a path to type."""
        await launchers_page.change_file(library, launcher, rebuild)

    async def refresh_grid() -> None:
        refresh = context["state"].get("refresh_launchers")
        if callable(refresh):
            await refresh()

    @on_page
    async def make_file() -> None:
        try:
            await run.io_bound(library.make_launcher_settings_file, launcher["launcher_id"])
        except Exception as exc:  # noqa: BLE001
            ui.notify(t("manager.workbench.make_file_failed"), caption=why(exc), type="negative")
        else:
            ui.notify(t("manager.workbench.make_file_made"), type="positive")
        refresh = context["state"].get("refresh_launchers")
        if callable(refresh):
            await refresh()
        await rebuild()

    def save_field(key: str) -> Callable[[Any], Any]:
        async def save(value: Any) -> bool:
            return await write(settings={**launcher["settings"], key: value})
        return save

    async def save_args(text: str) -> str:
        return await put({"settings": {**launcher["settings"], "args": text}})

    file_regions: list[panel.Region] = []

    def settings_file(field: dict[str, Any]) -> panel.Region:
        """Read-only, with the folder button opening the dialog that changes it. Nothing
        marks it as changed, because which file a launcher plays is not a departure from
        anything."""
        def build() -> None:
            settings_page.control_for(
                {**launchers_page.standing_option(field, launcher["app_name"]),
                 "unmarked": True, "readonly": True, "chooser": choose_file,
                 "chooser_tip": t("manager.launchers.sf.change_tip")},
                launcher["settings"].get(SETTINGS_FILE), save_field(SETTINGS_FILE),
                check=_file_check(launcher, field), browse=library.folders)()
        shown = panel.region(build)
        file_regions.append(shown)
        return shown

    entries: list[tuple[Any, ...]] = [
        (HEADING, t("manager.workbench.this_launcher")),
        (t("word.name"), panel.field(launcher["display_name"], rename,
                             placeholder=launcher["app_name"], refuses=True)),
        (t("word.runs"), launcher["app_name"]),
        (t("word.default"), panel.switch(
            is_default, lambda e: make_default(bool(e.value)),
            disabled=is_default or bool(refused_default),
            hint=t("manager.workbench.default_another") if is_default else refused_default)),
        panel.note(t("manager.workbench.default_help", app=launcher["app_name"])),
    ]
    entries.append((t("manager.workbench.enabled"), panel.switch(
        launcher["enabled"], lambda e: flip(bool(e.value)), disabled=only_one,
        hint=t("manager.workbench.only_launcher") if only_one else "")))
    if goes_to := _switched_off_goes_to(launcher, context.get("launchers") or []):
        entries.append(panel.note(t("manager.workbench.off_note", name=goes_to)))

    entries.append((HEADING, t("word.program")))
    if note := _program_note(launcher):
        entries.append(panel.note(note))
    playing = bool(context.get("playing"))
    for field in launcher.get("fields") or []:
        if field["key"] == COMMANDS_FROM:
            entries.append((HEADING, t("manager.workbench.commands")))
        if field["key"] == "args":
            # Refused where it is typed rather than in a toast, and on opening for one a
            # launch would refuse.
            typed = str(launcher["settings"].get("args") or "")
            refused = apps.arguments_problem(launcher["app"], typed)
            control = panel.field(typed, save_args, refuses=True,
                                  on_control=partial(_refusing, refused) if refused else None)
        elif field["key"] == SETTINGS_FILE:
            control = settings_file(field)
        else:
            control = settings_page.control_for(
                launchers_page.standing_option(field, launcher["app_name"]),
                launcher["settings"].get(field["key"]),
                save_field(field["key"]),
                check=(launcher.get("checks") or {}).get(field["key"]),
                browse=library.folders)
        entries.append((field["label"], control))
        if field["key"] == SETTINGS_FILE and awaiting_file(launcher):
            entries.append((panel.ASIDE, _file_state(launcher, field, make_file, playing)))
        if field.get("description"):
            entries.append(panel.note(field["description"]))
        if field["key"] == SETTINGS_FILE and _app_keeps_settings(context):
            held = await backup_rows.listed(library, "launcher", launcher["launcher_id"])
            entries.append(backup_rows.entry(
                library, "launcher", launcher["launcher_id"],
                str(launcher.get("display_name") or launcher["launcher_id"]), held,
                context["rebuild"], hint=t(PLAYING_NOTE) if playing else ""))
        if field["key"] == "args":
            commands_help.add_to(entries, tokens.TABLE, after=False,
                                 note=t("launcher.args.names_note"))
            entries.append((t("launcher.command_line"), command_line))
            if _reads_its_own_file(context, launcher):
                entries.append(panel.note(
                    t("launcher.command_line.own_file", app=launcher["app_name"])))
        # Once, under the pair, rather than beside each of them.
        if field["key"] == "on_table_exit":
            commands_help.add_to(entries, tokens.TABLE)
    with ui.column().classes("gap-0 manager-form"):
        _rows(ui, entries)


def _file_check(launcher: dict[str, Any], field: dict[str, Any]) -> dict[str, Any] | None:
    """The mark in the Settings File field. A tick where the file is there, whether it is
    the program's own or one named; no cross for a file not made yet, which the chip under
    the field says."""
    if launcher.get("has_settings_file"):
        return {"state": path_state.OK}
    found = (launcher.get("checks") or {}).get(field["key"])
    if awaiting_file(launcher) and (found or {}).get("state") == path_state.MISSING:
        return None
    return found


def _file_state(launcher: dict[str, Any], field: dict[str, Any],
                make: Callable[[], Any], playing: bool) -> Callable[[], None]:
    """Under the Settings File field while the file is not there: the chip, and beside it
    the way to make it, or why that is not on offer."""
    app_name = launcher["app_name"]
    wrote_nothing = bool(launcher.get("settings_file_not_made"))

    def draw() -> None:
        with ui.element("div").classes("manager-fact-edit flex-wrap"):
            ui.label(t("manager.workbench.not_created")).classes(
                "manager-tier manager-tier--warn")
            if wrote_nothing:
                if said := _never_wrote(launcher["app"], field):
                    ui.label(said).classes("manager-help min-w-0")
            elif launcher.get("can_make_settings_file"):
                panel.action(
                    t("manager.workbench.make_file"), make, icon=verbs.CREATE, inline=True,
                    enabled=not playing,
                    hint=t("error.launchers.make_file_playing") if playing
                    else t("manager.workbench.make_file.help", app=app_name))()

    return draw


def _never_wrote(app_id: str, field: dict[str, Any]) -> str:
    """When the program will write its file, in the app's catalog's words."""
    found = next((one for one in apps.launcher_fields(app_id) or ()
                  if one.key == field["key"]), None)
    return apps.field_nowhere(app_id, found) if found is not None else ""


def _refusing(said: str, control: Any) -> None:
    control.props["error"] = True
    control.props["error-message"] = said


def _command_line(pieces: list[dict[str, Any]] | None) -> None:
    """What the launcher runs, with the launcher's own Arguments marked. Nothing while a
    launch would refuse them."""
    if pieces is None:
        ui.element("div")  # the grid's cell: without it every row after this one shifts
        return
    joined = " ".join(
        f'<span class="manager-option--yours">{html.escape(one["text"])}</span>'
        if one.get("yours") else html.escape(one["text"]) for one in pieces)
    with ui.column().classes("manager-options manager-options--field w-full min-w-0 gap-0"):
        ui.html(joined, sanitize=False, tag="div").classes("manager-option")


def _reads_its_own_file(context: dict[str, Any], launcher: dict[str, Any]) -> bool:
    """A command line with no `-ini` for a program that has a settings file: the program
    reads its own, and the line says so rather than leave the gap to be read as a mistake."""
    pieces = launcher.get("command_line")
    return pieces is not None and _app_keeps_settings(context) \
        and not any(str(one.get("text")) == "-ini" for one in pieces)


def _app_keeps_settings(context: dict[str, Any]) -> bool:
    """Whether there is a settings file to copy at all. `generic` keeps none."""
    return bool((context.get("launcher") or {}).get("has_config")) \
        and (bool(context.get("config_groups")) or _program_is_there(context))
