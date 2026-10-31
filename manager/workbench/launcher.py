"""The launcher subject."""

from __future__ import annotations

import logging
from collections.abc import Callable
from functools import partial
from typing import Any

from nicegui import run, ui

from common import tokens
from common.failures import why
from common.i18n import t
from manager.backups import backup_rows
from manager.frontend import launchers as launchers_page
from manager.kit import deeplink, game_tables, offload, panel, table_features
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
)
from manager.workbench.core import HEADING, _choose, _rows, _save_overrides, _switch

logger = logging.getLogger("vpinfe.manager.workbench.launcher")


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
    defaults = set((found.get("defaults") or {}).values())
    return {**found, "launchers": [{**one, "is_default": one["launcher_id"] in defaults}
                                   for one in (found.get("launchers") or [])]}


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
        return ""

    @on_page
    async def write(**changes: Any) -> bool:
        refused = await put(changes)
        if refused:
            ui.notify(t("manager.workbench.save_failed"), caption=refused, type="negative")
        return not refused

    async def rename(text: str) -> str:
        wanted = text.strip() or launcher["app_name"]
        if wanted == launcher["display_name"]:
            return ""
        refused = await put({"display_name": wanted})
        retitle = context.get("retitle")
        if not refused and callable(retitle):
            retitle(wanted)
        return refused

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
            try:
                await run.io_bound(library.make_launcher_default, launcher["launcher_id"])
            except Exception as exc:  # noqa: BLE001
                ui.notify(t("manager.workbench.save_failed"), caption=why(exc), type="negative")
            again = context["state"].get("refresh_launchers")
            if callable(again):
                await again()
        await rebuild()

    def save_field(key: str) -> Callable[[Any], Any]:
        async def save(value: Any) -> bool:
            return await write(settings={**launcher["settings"], key: value})
        return save

    entries: list[tuple[Any, Any]] = [
        (HEADING, t("manager.workbench.this_launcher")),
        (t("word.name"), panel.field(launcher["display_name"], rename,
                             placeholder=launcher["app_name"], refuses=True)),
        panel.note(t("manager.workbench.name_note")),
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
    for field in launcher.get("fields") or []:
        if field["key"] == COMMANDS_FROM:
            entries.append((HEADING, t("manager.workbench.commands")))
        entries.append((field["label"],
                        settings_page.control_for(
                            launchers_page.standing_option(field, launcher["app_name"]),
                            launcher["settings"].get(field["key"]),
                            save_field(field["key"]),
                            check=(launcher.get("checks") or {}).get(field["key"]),
                            browse=library.folders)))
        if field.get("description"):
            entries.append(panel.note(field["description"]))
        # Once, under the pair, rather than beside each of them.
        if field["key"] == "on_table_exit":
            commands_help.add_to(entries, tokens.TABLE)
    with ui.column().classes("gap-0 manager-form"):
        _rows(ui, entries)


def _app_keeps_settings(context: dict[str, Any]) -> bool:
    """Whether there is a settings file to copy at all. `generic` keeps none."""
    return bool((context.get("launcher") or {}).get("has_config")) \
        and (bool(context.get("config_groups")) or _program_is_there(context))


async def _config_backups(context: dict[str, Any]) -> None:
    """The settings file this launcher writes, and the copies kept of it."""
    library, launcher = context["library"], context["launcher"]
    launcher_id = launcher["launcher_id"]
    try:
        found = await offload.io(library.launcher_config, launcher_id)
    except Exception:  # noqa: BLE001 - the copies are listed apart from the settings
        found = {}
    named = list((found.get("files") or {}).values())
    held = await backup_rows.listed(library, "launcher", launcher_id)
    playing = bool(context.get("playing"))
    entries: list[tuple[Any, ...]] = []
    if named:
        entries.append((t("word.file"), panel.path_value(named[0])))
    entries.append(backup_rows.entry(
        library, "launcher", launcher_id, str(launcher.get("display_name") or launcher_id),
        held, context["rebuild"], hint=t(PLAYING_NOTE) if playing else ""))
    _rows(ui, entries)
