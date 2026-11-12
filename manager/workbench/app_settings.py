"""A table's Settings section: which launcher plays it, then what the program that
launcher runs does differently for this table.

The program's rows are the table's differences and nothing else. Every setting, at this
table, is one step further: Show Every Setting.
"""

from __future__ import annotations

import asyncio
import contextlib
import html
import json
import os
from collections.abc import Awaitable, Callable, Collection, Sequence
from dataclasses import dataclass, replace
from functools import partial
from types import SimpleNamespace
from typing import Any

from nicegui import ui

from common.failures import why
from common.i18n import t
from manager.kit import confirm, deeplink, offload, panel, undo, verbs
from manager.kit import dialog as frame
from manager.kit.data import config_groups
from manager.kit.on_page import on_page
from manager.workbench import all_settings, core
from manager.workbench import launcher as wb_launcher

SCOPE_ENTRY = "entry"
SCOPE_LAUNCHER = "launcher"
ADDED = "added_settings"
# Past this many launchers a selection's "uses the launchers' settings" line counts them.
MOST_LAUNCHERS_NAMED = 3
# How long a read of a selection runs before the section says it is reading.
READING_NOTICE_S = 1.0
# How often a write into several tables is asked how far it has got.
JOB_POLL_S = 0.4

_SHOW_ADDED = """
(() => {
  let tries = 0;
  const show = () => {
    const label = [...document.querySelectorAll('.manager-workbench-body .manager-fact-label')]
      .find(el => el.textContent.trim() === %s);
    const value = label && label.nextElementSibling;
    if (!value) { if (++tries < 40) setTimeout(show, 25); return; }
    value.scrollIntoView({block: 'center'});
    (value.querySelector('input:not(.hidden), textarea')
      || value.querySelector('[tabindex="0"]'))?.focus();
  };
  show();
})()
"""


def title_for(game_name: str, table_name: str, launcher_name: str,
              folder_tables: int) -> str:
    """Named for the table being edited. The game names it, and the table is added where
    the game has more than one."""
    subject = (t("manager.app_settings.game_and_table", game=game_name, table=table_name)
               if folder_tables > 1 and table_name else game_name)
    return t("manager.app_settings.settings", table=subject, launcher_name=launcher_name)


def shared_note(found: dict, folder_tables: int) -> tuple[Any, Any] | None:
    """Where this table's file is also its game's, what a value set for it reaches."""
    if folder_tables <= 1 or not found.get("shared_with_game"):
        return None
    return panel.intro(t("manager.app_settings.shared_with_game", count=folder_tables - 1))


async def section(context: dict[str, Any]) -> None:
    table = next((one for one in context["tables"] if one.get("id") == context["lens"]),
                 None)
    with ui.column().classes("gap-0 manager-form"):
        if table is None:
            ui.label(t("manager.workbench.none_selected")).classes("manager-help")
            return

        async def drawn() -> None:
            panel.facts(ui, [(panel.HEADING, t("manager.workbench.launcher")),
                             *wb_launcher.launcher_rows(context, table),
                             *await _program_entries(context, table)])

        context["settings_region"] = panel.region(drawn)
        context["settings_region"]()
    await context["settings_region"].refilled()
    ui.run_javascript(core._KEEP_SCROLL
                      % f"settings:{table.get('id') or ''}".replace("'", "\\'"))


def _runs(context: dict[str, Any], table: dict[str, Any]) -> dict[str, Any] | None:
    held = (context.get("launchers") or {}).get("launchers") or []
    return next((one for one in held if one["launcher_id"] == table.get("launcher")), None)


async def selection_section(context: dict[str, Any]) -> None:
    """The settings of every selected table, as one table's would read."""
    tables = list(context["tables"])
    with ui.column().classes("gap-0 manager-form"):
        reading = ui.label(t("manager.app_settings.reading")).classes("manager-help")
        reading.set_visibility(False)
        late = ui.timer(READING_NOTICE_S, lambda: reading.set_visibility(True), once=True)

        async def drawn() -> None:
            panel.facts(ui, await _selection_entries(context, tables))

        context["settings_region"] = panel.region(drawn)
        context["settings_region"]()
    try:
        await context["settings_region"].refilled()
    finally:
        late.cancel()
    reading.delete()
    ui.run_javascript(core._KEEP_SCROLL % "settings:selection")


def _programs(context: dict[str, Any],
              tables: Sequence[dict[str, Any]]) -> list[tuple[dict[str, Any] | None, list]]:
    """The selected tables by the program their launcher runs, the most tables first."""
    found: dict[str, tuple[dict[str, Any] | None, list]] = {}
    for table in tables:
        launcher = _runs(context, table)
        found.setdefault(str((launcher or {}).get("app") or ""), (launcher, []))[1] \
            .append(table)
    return sorted(found.values(), key=lambda held: -len(held[1]))


def _program_name(launcher: dict[str, Any] | None) -> str:
    return str((launcher or {}).get("app_name") or "") or t("manager.app_settings.no_program")


async def _selection_entries(context: dict[str, Any],
                             tables: list[dict[str, Any]]) -> list[tuple[Any, Any]]:
    programs = _programs(context, tables)
    heading = (panel.HEADING, t("manager.section.settings"))
    if len(programs) > 1:
        return [heading, panel.intro(t("manager.app_settings.many_programs",
                                       count=len(programs))),
                *[(panel.FULL, _keep_link(context, launcher, held))
                  for launcher, held in programs]]
    launcher = programs[0][0]
    if launcher is None or not launcher.get("has_config"):
        return [heading, panel.intro(t("manager.app_settings.no_config"))]
    entries: list[tuple[Any, Any]] = [(panel.HEADING, t(
        "manager.app_settings.tables_title", app=launcher.get("app_name") or ""))]
    if note := all_settings._program_note(launcher):
        return [*entries, panel.intro(note)]
    library = context["library"]
    shared = [{"table": table, "launcher_id": str((_runs(context, table) or {})
                                                     .get("launcher_id") or "")}
              for table in tables]
    try:
        found = await offload.io(library.launcher_config, shared[0]["launcher_id"],
                                 str(tables[0].get("id") or ""), SCOPE_ENTRY)
    except Exception as exc:  # noqa: BLE001 - this says why, never 500s
        return [*entries, panel.intro(t("said.settings_unreadable"), hint=why(exc))]
    groups = config_groups(found)
    inner: dict[str, Any] = {
        "library": library, "launcher": launcher, "config_groups": groups,
        "config_scope": SCOPE_ENTRY, "config_table": str(tables[0].get("id") or ""),
        "playing": await offload.io(all_settings._playing, library),
        "state": context["state"], "rebuild": context["rebuild"], "tables": tables,
        "shared": shared, "by_value": True,
        "config_read": partial(as_one, library, shared, groups),
        "config_write": partial(write_selection, library, context["state"], shared, groups),
    }
    inner["config_done"] = partial(_wrote, inner)
    inner["config_more"] = MoveToAll(inner, _for_every_table(groups, SCOPE_LAUNCHER),
                                     inner["rebuild"])
    try:
        values = await offload.io(as_one, library, shared, groups)
    except Exception as exc:  # noqa: BLE001 - this says why, never 500s
        return [*entries, panel.intro(t("said.settings_unreadable"), hint=why(exc))]
    inner["config_values"] = values
    entries.append(panel.intro(t("manager.app_settings.selection_writes", count=len(tables))))
    added = _added(context["state"], "selection:" + ",".join(
        sorted(str(one.get("id") or "") for one in tables)))
    blocks = differences(groups, values, added)
    view = point_of_view(groups, values, added)
    options = table_options(groups, values)
    if not blocks and view is None and options is None:
        entries.append(_uses_launcher(context, tables))
    if view is not None:
        blocks.append((view.label, view.rows))
    entries += await all_settings._setting_entries(
        inner, [(label, "", fields) for label, fields in blocks], curated=True, sub=True,
        pairs=named_pairs(groups))
    remove = partial(_remove, inner)
    if view is not None:
        entries += _camera_entries(view, remove, inner["playing"])
    if options is not None:
        entries += _option_entries(options, values, remove, inner["playing"])
    if offered := addable(groups, values, added):
        entries.append((panel.FULL, partial(_add_picker, context, added, offered)))
    entries.append((panel.FULL, panel.action(
        t("manager.app_settings.show_all"),
        partial(_every, context, inner, t("manager.app_settings.selection_all",
                                          count=len(tables),
                                          launcher_name=launcher.get("display_name") or "")),
        icon=verbs.DRILL, hint=t("manager.app_settings.show_all.help"))))
    return entries


def _uses_launcher(context: dict[str, Any],
                   tables: Sequence[dict[str, Any]]) -> tuple[Any, Callable[[], None]]:
    """The line for tables that set nothing of their own, a way to the settings they use.
    Tables on several launchers have no one page, so each launcher is named and linked."""
    found = {str(one["launcher_id"]): one for table in tables
             if (one := _runs(context, table)) is not None}

    def way(launcher_id: str, label: str) -> Callable[[], None]:
        to = "/manager?" + deeplink.query({"view": "launchers", "launcher": launcher_id})
        return panel.link(label, to=to)

    if len(found) == 1:
        ((launcher_id, _),) = found.items()
        return (panel.FULL, way(launcher_id, t("manager.app_settings.no_override")))
    if len(found) > MOST_LAUNCHERS_NAMED:
        return panel.intro(t("manager.app_settings.no_override_many", count=len(found)))

    def draw() -> None:
        with ui.element("div").classes("manager-help manager-intro"):
            ui.html(html.escape(t("manager.app_settings.no_override_some")) + " ",
                    tag="span", sanitize=False)
            for index, (launcher_id, one) in enumerate(found.items()):
                if index:
                    joiner = (f" {t('manager.launchers.sf.and')} " if index == len(found) - 1
                              else t("manager.launchers.sf.list_join"))
                    ui.html(html.escape(joiner), tag="span", sanitize=False)
                way(launcher_id, str(one.get("display_name") or ""))()

    return (panel.FULL, draw)


def _keep_link(context: dict[str, Any], launcher: dict[str, Any] | None,
               held: list[dict[str, Any]]) -> Callable[[], None]:
    """One program's tables, as a link that leaves only them ticked."""
    ids = [str(one.get("id") or "") for one in held]

    def draw() -> None:
        ui.label(t("manager.app_settings.keep_program", app=_program_name(launcher),
                   count=len(held))).classes("manager-link cursor-pointer") \
            .on("click", lambda: context["state"]["keep_selected"](ids)) \
            .tooltip(t("manager.app_settings.keep_program.help"))

    return draw


def _wrote(inner: dict[str, Any], wrote: dict[str, Any]) -> None:
    """Says what a write to the selection did, with Undo putting each table back as it
    was: its own value where it held one, the program's shared reading where it did not."""
    _said_failed(wrote.get("failed"))
    changes = wrote.get("undo") or []
    if not changes:
        return
    said = (t("manager.app_settings.cleared") if wrote.get("blank")
            else t("manager.app_settings.selection_set", count=len(changes)))
    undo.offer(said, partial(_put_back, inner, changes))


def _said_failed(failed: Sequence[dict[str, Any]] | None) -> None:
    if failed:
        ui.notify(t("manager.app_settings.selection_failed", count=len(failed)),
                  caption="; ".join(dict.fromkeys(str(one.get("reason") or "")
                                                  for one in failed)),
                  type="warning")


async def _put_back(inner: dict[str, Any], changes: Sequence[tuple[str, str, dict]]) -> None:
    """Each table's own values again, the tables that held the same ones together."""
    together: dict[tuple[str, tuple], list[str]] = {}
    for table_id, launcher_id, held in changes:
        together.setdefault((launcher_id, tuple(sorted(held.items()))), []).append(table_id)
    failed: list[dict[str, Any]] = []
    try:
        for (launcher_id, pairs), ids in together.items():
            failed += await _write_tables(inner["library"], inner["state"], launcher_id,
                                          dict(pairs), ids)
    finally:
        await inner["rebuild"]()
    if failed:
        raise RuntimeError("; ".join(dict.fromkeys(str(one.get("reason") or "")
                                                   for one in failed if one.get("reason"))))


async def _program_entries(context: dict[str, Any],
                           table: dict[str, Any]) -> list[tuple[Any, Any]]:
    launcher = _runs(context, table)
    if launcher is None or not launcher.get("has_config"):
        return []
    library = context["library"]
    entries: list[tuple[Any, Any]] = [(panel.HEADING, t(
        "manager.app_settings.table_title", app=launcher.get("app_name") or ""))]
    if note := all_settings._program_note(launcher):
        return [*entries, panel.intro(note)]
    table_id = str(table.get("id") or "")
    try:
        found = await offload.io(library.launcher_config, launcher["launcher_id"],
                                 table_id, SCOPE_ENTRY)
    except Exception as exc:  # noqa: BLE001 - this says why, never 500s
        return [*entries, panel.intro(t("said.settings_unreadable"), hint=why(exc))]

    groups = config_groups(found)
    values = dict(found.get("values") or {})
    inner: dict[str, Any] = {
        "library": library, "launcher": launcher, "config_groups": groups,
        "config_values": values, "config_scope": SCOPE_ENTRY, "config_table": table_id,
        "playing": await offload.io(all_settings._playing, library),
        "state": context["state"], "rebuild": context["rebuild"],
        "saved": context.get("saved"),
    }
    if shared := shared_note(found, len(context.get("tables") or []) or 1):
        entries.append(shared)
    if reach := dict(found.get("from_game") or {}):
        entries += _from_game_entries(inner, reach)
    elif follows := dict(found.get("follows_game") or {}):
        entries += _follows_game_entries(inner, follows)
    going = partial(going_back, groups, dict(found.get("from_game") or {}))
    back = partial(going, inner)
    inner["clear_note"] = going
    if others := _others(context, table, launcher):
        inner["config_more"] = ForAll(inner, others, bool(found.get("shared_with_game")),
                                      list(context.get("tables") or []),
                                      _for_every_table(groups), inner["rebuild"])
    added = _added(context["state"], table_id)
    blocks = differences(groups, values, added)
    view = point_of_view(groups, values, added)
    options = table_options(groups, values)
    if not blocks and view is None and options is None:
        entries.append(_uses_launcher(context, [table]))
    if view is not None:
        blocks.append((view.label, view.rows))
    entries += await all_settings._setting_entries(
        inner, [(label, "", fields) for label, fields in blocks], curated=True, sub=True,
        pairs=named_pairs(groups))
    remove = partial(_remove, inner)
    if view is not None:
        entries += _camera_entries(view, remove, inner["playing"], back)
    if options is not None:
        entries += _option_entries(options, values, remove, inner["playing"], back)
    if offered := addable(groups, values, added):
        entries.append((panel.FULL, partial(_add_picker, context, added, offered)))
    entries.append((panel.FULL, panel.action(
        t("manager.app_settings.show_all"),
        partial(_every_setting, context, table, inner), icon=verbs.DRILL,
        hint=t("manager.app_settings.show_all.help"))))
    return entries


def differences(groups: Sequence[Any], values: dict[str, Any],
                added: Collection[str] = ()) -> list[tuple[str, list[Any]]]:
    """What this table's file sets, what reaches it from its game's and what was just
    added, by area: the rows an area curates first, in its order, then the rest in the
    program's. A plugin's rows lead with the plugin's name, which is all that tells five
    Enables apart, and a window's rows lead with the window's."""
    names = all_settings._plugin_names(groups)
    found = []
    for group in groups:
        if group.summarized or getattr(group, "read_only", False):
            continue
        order = {key: at for at, key in
                 enumerate(key for heading in group.curated for key in heading.keys)}
        shown = _whole(group, {field.key for field in group.settings
                               if _differs(values.get(field.key) or {}) or field.key in added})
        paired = {key for pair in _pairs_of(group) for key in pair.keys}
        fields = sorted((field for field in group.settings if field.key in shown),
                        key=lambda field: order.get(field.key, len(order)))
        if fields:
            found.append((group.label, [field if field.key in paired
                                        else _named(field, group, names) for field in fields]))
    return found


def _pairs_of(group: Any) -> list[Any]:
    return [pair for heading in group.curated for pair in getattr(heading, "pairs", ())]


def _whole(group: Any, keys: set[str]) -> set[str]:
    """`keys`, and the rest of each pair one of them is in."""
    return keys | {key for pair in _pairs_of(group) if not keys.isdisjoint(pair.keys)
                   for key in pair.keys}


def named_pairs(groups: Sequence[Any]) -> list[Any]:
    """Each pair, named as it is away from its heading: by its plugin, or by its window
    where the heading has a name."""
    names = all_settings._plugin_names(groups)
    found = []
    for group in groups:
        for heading in group.curated:
            for pair in getattr(heading, "pairs", ()):
                found.append(SimpleNamespace(**{**vars(pair),
                                                "label": _pair_name(heading, pair, names)}))
    return found


def _pair_name(heading: Any, pair: Any, names: dict[str, str]) -> str:
    section = all_settings._section_of(pair.keys[0])
    if section.startswith(all_settings.PLUGIN_SECTION):
        return all_settings.plugin_row(section, pair.label, names)
    if heading.label:
        return t("manager.app_settings.window_row", window=heading.label, label=pair.label)
    return pair.label


def _differs(held: dict[str, Any]) -> bool:
    return bool(held.get("set_here")) or held.get("scope") == "folder"


def _shown(group: Any, values: dict[str, Any], added: Collection[str]) -> bool:
    return any(_differs(values.get(field.key) or {}) or field.key in added
               for field in group.settings)


def _added(state: dict[str, Any], table_id: str) -> list[str]:
    """The settings Add a Setting drew at this table, forgotten when another is open."""
    held = state.get(ADDED) or {}
    if held.get("table") != table_id:
        held = state[ADDED] = {"table": table_id, "keys": []}
    return list(held["keys"])


def addable(groups: Sequence[Any], values: dict[str, Any],
            added: Collection[str]) -> list[tuple[Any, str]]:
    """What this table could be given a value of its own for and is not showing yet, each
    with its area: the settings commonly set per table first, then the rest. Of the point
    of view, only the view modes it would draw."""
    names = all_settings._plugin_names(groups)
    pairs = {pair.keys[0]: pair for pair in named_pairs(groups)}
    first: list[tuple[Any, str]] = []
    rest: list[tuple[Any, str]] = []
    for group in groups:
        if getattr(group, "read_only", False) or (
                group.summarized and _shown(group, values, added)):
            continue
        shown = _whole(group, {field.key for field in group.settings
                               if field.key in added or _differs(values.get(field.key) or {})})
        later = {key for pair in _pairs_of(group) for key in pair.keys[1:]}
        for field in group.settings:
            if (field.key in shown or field.key in later
                    or SCOPE_ENTRY not in (field.scopes or (SCOPE_ENTRY,))
                    or (group.summarized and field.key not in group.rows)):
                continue
            pair = pairs.get(field.key)
            (first if field.per_table else rest).append(
                (SimpleNamespace(**{**vars(field), "label": pair.label}) if pair is not None
                 else _named(field, group, names), str(group.label)))
    return first + rest


def _add_picker(context: dict[str, Any], added: list[str],
                offered: list[tuple[Any, str]]) -> None:
    """Picking one draws its row, with the value the table uses now, and focuses it."""
    headings = {}
    lead = offered[0][0]
    rest = next((field.key for field, _area in offered if not field.per_table), None)
    if lead.per_table:
        headings[lead.key] = t("manager.app_settings.common_heading")
        if rest is not None:
            headings[rest] = t("manager.app_settings.everything_else")
    labels = {field.key: str(field.label) for field, _area in offered}
    picker = panel.SettingPicker(
        labels, areas={field.key: area for field, area in offered}, headings=headings,
        label=t("manager.app_settings.add")) \
        .props('input-debounce=0 hide-selected fill-input '
               'popup-content-class="manager-picker-popup"') \
        .classes("w-full mt-2")

    @on_page
    async def pick() -> None:
        key = str(picker.value or "")
        if key not in labels:
            return
        context["state"][ADDED]["keys"] = [*added, key]
        await context["settings_region"].refilled()
        ui.run_javascript(_SHOW_ADDED % json.dumps(labels[key]))

    picker.on_value_change(pick)
    ui.run_javascript(core._ADD_BOX % (picker.id, "false"))


def _named(field: Any, group: Any, names: dict[str, str]) -> Any:
    section = all_settings._section_of(field.key)
    if section.startswith(all_settings.PLUGIN_SECTION):
        label = all_settings.plugin_row(section, field.label, names)
    elif window := _window_of(field.key, group):
        label = t("manager.app_settings.window_row", window=window, label=field.label)
    else:
        return field
    return SimpleNamespace(**{**vars(field), "label": label})


def _window_of(key: str, group: Any) -> str:
    """The window or view a setting belongs to: the curated heading it is drawn under, or
    the one whose keys its own continues, where that heading's keys share one name.
    `BackglassFSWidth` goes with the heading of `BackglassOutput` and `BackglassDisplay`."""
    held = next((heading for heading in group.curated if key in heading.keys), None)
    for heading in (held,) if held is not None else group.curated:
        names = [one.rsplit(".", 1)[-1] for one in heading.keys]
        stem = os.path.commonprefix(names)
        if (len(names) > 1 and stem and key.rsplit(".", 1)[-1].startswith(stem)
                and {all_settings._section_of(one) for one in heading.keys}
                == {all_settings._section_of(key)}):
            return str(heading.label)
    return ""


def point_of_view(groups: Sequence[Any], values: dict[str, Any],
                  added: Collection[str] = ()) -> SimpleNamespace | None:
    """A summarized group, where anything in it differs at this table or was just added:
    the rows it draws, each named by its view, and the headings the rest is saved
    under."""
    group = next((one for one in groups if one.summarized), None)
    if group is None or not _shown(group, values, added):
        return None
    fields = {field.key: field for field in group.settings}
    drawn = [key for key in getattr(group, "rows", ()) if key in fields]
    camera = {key: values.get(key) or {} for key in fields if key not in drawn}
    return SimpleNamespace(
        label=group.label, rows=[_named(fields[key], group, {}) for key in drawn],
        views=[str(heading.label) for heading in group.curated
               if any(_differs(camera.get(key) or {}) for key in heading.keys)],
        own=[key for key, held in camera.items() if held.get("set_here")],
        reaching=any(_differs(held) for held in camera.values()))


def _camera_entries(view: SimpleNamespace, remove: Callable[[list[str]], Any],
                    playing: bool, back: Callable[[Sequence[str]], str] = lambda keys: ""
                    ) -> list[tuple[Any, Any]]:
    """The rest of the summarized group as one row, with Reset where the table holds it."""
    entries: list[tuple[Any, Any]] = [
        (t("manager.app_settings.camera"), _dotted(camera_said(view), bool(view.own)))]
    if view.own:
        entries.append((panel.ASIDE, _reset(
            t("word.reset"), partial(remove, view.own),
            _hint_with(t("manager.app_settings.camera_reset.help"), back(view.own)),
            playing)))
    entries.append(panel.note(t("manager.app_settings.camera.help")))
    return entries


def camera_said(view: SimpleNamespace) -> str:
    said = t("manager.workbench.saved_table" if view.own
             else "manager.app_settings.saved_game" if view.reaching
             else "manager.app_settings.the_tables_own")
    if view.views and view.reaching:
        return t("manager.app_settings.saved_in_views", saved=said,
                 views=", ".join(view.views))
    return said


def table_options(groups: Sequence[Any], values: dict[str, Any]) -> tuple[str, list[Any]] | None:
    """A read-only group's settings that differ at this table, under its label."""
    for group in groups:
        fields = [field for field in group.settings
                  if _differs(values.get(field.key) or {})]
        if getattr(group, "read_only", False) and fields:
            return group.label, fields
    return None


def _option_entries(options: tuple[str, list[Any]], values: dict[str, Any],
                    remove: Callable[[list[str]], Any], playing: bool,
                    back: Callable[[Sequence[str]], str] = lambda keys: ""
                    ) -> list[tuple[Any, Any]]:
    """Each as the file holds it, with Reset where the table holds it, and Reset All."""
    label, fields = options
    entries: list[tuple[Any, Any]] = [(panel.FULL, partial(all_settings._subheading, label))]
    own = []
    for field in fields:
        held = values.get(field.key) or {}
        entries.append((field.label, _dotted(str(held.get("value") or ""),
                                             bool(held.get("set_here")))))
        if held.get("set_here"):
            own.append(field.key)
            entries.append((panel.ASIDE, _reset(
                t("word.reset"), partial(remove, [field.key]),
                _hint_with(t("manager.app_settings.option_reset.help"), back([field.key])),
                playing)))
        elif mark := all_settings._config_mark(held, SCOPE_ENTRY, field):
            entries.append((panel.ASIDE, mark))
    if len(own) > 1:
        entries.append((panel.FULL, _reset(
            t("manager.app_settings.reset_all"), partial(remove, own),
            _hint_with(t("manager.app_settings.option_reset.help"), back(own)),
            playing, inline=False)))
    return entries


def going_back(groups: Sequence[Any], reach: dict[str, str], inner: dict[str, Any],
               cleared: Sequence[str]) -> str:
    """Where clearing `cleared` takes the last key of the table's own file, so the
    program reads the game's file instead: that, and the other rows the game's file
    changes. Empty where the file keeps a key, or the game's file changes nothing else.
    Reads the values as they stand now, since a clear redraws a row, not the panel."""
    own = {key for key, held in (inner.get("config_values") or {}).items()
           if held.get("set_here")}
    if not own or not own <= set(cleared):
        return ""
    labels = {field.key: field.label for group in groups for field in group.settings}
    rows = [labels.get(key, key) for key in reach if key not in cleared]
    if not rows:
        return ""
    return t("manager.app_settings.back_to_game", rows="\n".join(rows))


def _hint_with(hint: str, note: str) -> str:
    return "\n".join((hint, note)) if note else hint


def _dotted(text: str, own: bool) -> Callable[[], None]:
    """A value that is not edited here, with the dot where this table sets it."""
    def draw() -> None:
        with ui.row().classes("items-center gap-1 no-wrap manager-field-row"):
            ui.element("span").classes(
                "manager-mark manager-mark--full manager-named-mark").set_visibility(own)
            ui.label(text).classes("manager-fact-value truncate min-w-0")
    return draw


def _reset(label: str, on_click: Callable[[], Any], hint: str, playing: bool, *,
           inline: bool = True) -> Callable[[], None]:
    return panel.action(label, on_click, icon=verbs.RESET, inline=inline,
                        enabled=not playing,
                        hint=t(all_settings.PLAYING_NOTE) if playing else hint)


@on_page
async def _remove(inner: dict[str, Any], keys: list[str]) -> None:
    """Takes these off the table's own settings, and draws the section again."""
    blank = dict.fromkeys(keys, "")
    try:
        if (write := inner.get("config_write")) is not None:
            wrote = await all_settings.config_applied(write, blank)
        else:
            wrote = await offload.io(inner["library"].write_launcher_config,
                                     inner["launcher"]["launcher_id"], blank,
                                     table=inner["config_table"], scope=SCOPE_ENTRY)
    except Exception as exc:  # noqa: BLE001 - said, never raised into the page
        ui.notify(t("said.clear_failed"), caption=why(exc), type="negative")
        return
    if (done := inner.get("config_done")) is not None:
        done(wrote or {})
    await inner["rebuild"]()


async def _every_setting(context: dict[str, Any], table: dict[str, Any],
                         inner: dict[str, Any]) -> None:
    """All Settings, at this table, over the panel. The panel is drawn again on the way
    out, since anything here may have changed what it lists."""
    count = len(context.get("tables") or []) or 1
    title = title_for(str((context.get("game") or {}).get("name") or ""),
                      core._table_line(table),
                      str(inner["launcher"].get("display_name") or ""), count)
    await _every(context, inner, title)


async def _every(context: dict[str, Any], inner: dict[str, Any], title: str) -> None:
    every: dict[str, Any] = {**inner, "state": {"all_settings": {}}}
    every.pop("config_values", None)

    with frame.opened(title, full=True) as dialog:
        body = ui.column().classes("w-full grow min-h-0 gap-0 overflow-auto")

        async def redraw() -> None:
            every.pop("config_values", None)
            body.clear()
            with body:
                await all_settings._all_settings(every)

        every["rebuild"] = redraw
        if (more := inner.get("config_more")) is not None:
            every["config_more"] = replace(more, rebuild=redraw)
        await redraw()
        with frame.footer():
            frame.answer(t("word.done"), dialog.close, icon=verbs.DONE)

    dialog.on("hide", lambda: context["rebuild"]())
    dialog.open()


def _from_game_entries(inner: dict[str, Any], reach: dict[str, str]) -> list[tuple[Any, Any]]:
    """How many of the game's own settings this table's file keeps from reaching it, and
    Copy the Game's Settings Here."""
    playing = bool(inner["playing"])
    return [panel.intro(t("manager.app_settings.from_game", count=len(reach))),
            (panel.FULL, panel.action(
                t("manager.app_settings.copy_from_game"),
                partial(_copy_from_game, inner, reach), icon=verbs.COPY,
                enabled=not playing,
                hint=t(all_settings.PLAYING_NOTE) if playing
                else t("manager.app_settings.copy_from_game.help")))]


def _follows_game_entries(inner: dict[str, Any],
                          reach: dict[str, str]) -> list[tuple[Any, Any]]:
    """Says that a table with no file of its own reads its game's, and offers it a copy
    of what the game's file sets."""
    playing = bool(inner["playing"])
    return [panel.intro(t("manager.app_settings.follows_game")),
            (panel.FULL, panel.action(
                t("manager.app_settings.copy_from_game"),
                partial(_copy_from_game, inner, reach), icon=verbs.COPY,
                enabled=not playing,
                hint=t(all_settings.PLAYING_NOTE) if playing
                else t("manager.app_settings.copy_from_game.follows_help")))]


@on_page
async def _copy_from_game(inner: dict[str, Any], reach: dict[str, str]) -> None:
    try:
        await offload.io(inner["library"].write_launcher_config,
                         inner["launcher"]["launcher_id"], reach,
                         table=inner["config_table"], scope=SCOPE_ENTRY)
    except Exception as exc:  # noqa: BLE001 - said, never raised into the page
        ui.notify(t("said.save_failed"), caption=why(exc), type="negative")
        return
    ui.notify(t("manager.app_settings.copied_from_game", count=len(reach)), type="positive")
    await inner["rebuild"]()


def _others(context: dict[str, Any], table: dict[str, Any],
            launcher: dict[str, Any]) -> list[dict[str, Any]]:
    """The game's other tables that run on the same program, each with its launcher."""
    found = []
    for other in context.get("tables") or []:
        runs = _runs(context, other)
        if (other.get("id") != table.get("id") and runs is not None
                and runs.get("has_config") and runs.get("app") == launcher.get("app")):
            found.append({"table": other, "launcher_id": runs["launcher_id"]})
    return found


def _read_all(library: Any, others: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for one in others:
        _read_settings(library, one)
    return others


def _read_settings(library: Any, one: dict[str, Any]) -> None:
    """Another table's settings as they stand, whether its file is also the game's, and
    whether the game's file is the one it reads."""
    found = library.launcher_config(one["launcher_id"], str(one["table"].get("id") or ""),
                                    SCOPE_ENTRY)
    one["values"] = dict(found.get("values") or {})
    one["shares"] = bool(found.get("shared_with_game"))
    one["reads_game"] = any((held or {}).get("scope") == "folder"
                            for held in one["values"].values())


def _for_every_table(groups: Sequence[Any], scope: str = SCOPE_ENTRY) -> frozenset[str]:
    """The keys Set for All offers: every setting a table holds at `scope`, except the
    camera and the table options. Move to All Tables asks for the launcher's scope."""
    found: set[str] = set()
    for group in groups:
        if getattr(group, "read_only", False):
            continue
        drawn = set(getattr(group, "rows", ())) if group.summarized else None
        found.update(field.key for field in group.settings
                     if (drawn is None or field.key in drawn)
                     and scope in (field.scopes or (scope,)))
    return frozenset(found)


def already_uses(other: dict[str, Any], field: Any, value: str, shares_here: bool) -> bool:
    """Whether another of the game's tables uses this value: it reads this table's file,
    which is also the game's, or has the value already."""
    if shares_here and other.get("reads_game"):
        return True
    held = (other.get("values") or {}).get(field.key) or {}
    return all_settings._same_value(field, str(held.get("value") or ""), value)


@dataclass
class ForAll:
    """Set for All N Tables, beside a value this table sets itself that another of its
    game's tables does not use. A pair's row hands in its other rows as `paired`, and
    each of them set here is written with it. `rebuild` draws again whatever offered it.

    Nothing is offered until `ready` has read the other tables."""

    inner: dict[str, Any]
    others: list[dict[str, Any]]
    shares_here: bool
    tables: list[dict[str, Any]]
    offered: frozenset[str]
    rebuild: Callable[[], Awaitable[Any]]
    read: bool = False

    def _own(self, key: str, held: dict) -> bool:
        return key in self.offered and bool(held.get("set_here")) \
            and bool(held.get("in_effect", True))

    async def ready(self, values: dict[str, Any]) -> None:
        """Reads the other tables, once, when one of `values` is this table's own."""
        if self.read or not any(self._own(key, held or {}) for key, held in values.items()):
            return
        self.read = True
        try:
            await offload.io(_read_all, self.inner["library"], self.others)
        except Exception:  # noqa: BLE001 - without them the page offers no Set for All
            self.others = []

    def __call__(self, held: dict, field: Any,
                 *paired: tuple[dict, Any]) -> Callable[[], None] | None:
        own = [(one, str(said.get("value") or "")) for said, one in ((held, field), *paired)
               if self._own(one.key, said)]
        if not self.read or all(already_uses(other, one, value, self.shares_here)
                                for other in self.others for one, value in own):
            return None
        playing = bool(self.inner.get("playing"))
        return panel.action(
            t("manager.app_settings.apply_all", count=len(self.others) + 1),
            partial(_set_for_all, self.inner, self.others, own, self.shares_here,
                    self.tables, self.rebuild),
            icon=verbs.SHARE, inline=True, enabled=not playing,
            hint=t(all_settings.PLAYING_NOTE) if playing
            else t("manager.app_settings.apply_all.help"))


def write_for_all(library: Any, others: list[dict[str, Any]], field: Any, value: str,
                  shares_here: bool) -> list[dict[str, Any]]:
    """Writes the value into each other table's own file, the one whose file is also the
    game's first, and none that uses it by then. Returns the tables written that read the
    game's file before, and no longer do."""
    cut = []
    for one in sorted(others, key=lambda one: not one.get("shares")):
        _read_settings(library, one)
        if already_uses(one, field, value, shares_here):
            continue
        library.write_launcher_config(one["launcher_id"], {field.key: value},
                                      table=str(one["table"].get("id") or ""),
                                      scope=SCOPE_ENTRY)
        if one["reads_game"]:
            cut.append(one["table"])
        _read_settings(library, one)
    return cut


def as_one(library: Any, targets: list[dict[str, Any]],
           groups: Sequence[Any]) -> dict[str, dict[str, Any]]:
    """Each setting as several tables hold it, in one `held`: set where any of them sets
    it, in effect where each that sets it has it in effect, with `each` table's value in
    use and `varies` where those differ, holding no value of its own then."""
    for one in targets:
        _read_settings(library, one)
    fields = {field.key: field for group in groups for field in group.settings}
    found = {}
    for key in dict.fromkeys(key for one in targets for key in one["values"]):
        field = fields.get(key)
        default = str(getattr(field, "default", "") or "")
        held = [(one["table"], (one["values"].get(key) or {})) for one in targets]
        setting = [one for _, one in held if one.get("set_here")]
        merged = dict(setting[0] if setting else held[0][1])
        each = [(table, str(one.get("value") or "") or default) for table, one in held]
        merged.update(set_here=bool(setting),
                      in_effect=all(one.get("in_effect", True) for one in setting),
                      each=each)
        if any(not all_settings._same_value(field, each[0][1], value) for _, value in each):
            merged.update(varies=True, value="")
        found[key] = merged
    return found


def write_shared(library: Any, targets: list[dict[str, Any]], groups: Sequence[Any],
                 values: dict[str, str]) -> dict[str, Any]:
    """Values written at each of several tables: a value to each not using it yet, as Set
    for All does, and a blank to each that sets one. Returns under `cut` the tables
    written that read the game's file before, and no longer do."""
    fields = {field.key: field for group in groups for field in group.settings}
    cut = [table for key, value in values.items() if value != ""
           for table in write_for_all(library, targets, fields[key], value, False)]
    blank = [key for key, value in values.items() if value == ""]
    for one in targets if blank else []:
        _read_settings(library, one)
        held = {key: "" for key in blank
                if (one["values"].get(key) or {}).get("set_here")}
        if held:
            library.write_launcher_config(one["launcher_id"], held,
                                          table=str(one["table"].get("id") or ""),
                                          scope=SCOPE_ENTRY)
    return {"cut": cut}


def _plan_selection(library: Any, targets: list[dict[str, Any]], groups: Sequence[Any],
                    values: dict[str, str]) -> tuple[dict[str, tuple[str, dict[str, str]]],
                                                     list[dict[str, Any]]]:
    """What writing `values` to each target changes: under each table that would change, its
    launcher and what it held before - its own value, or a blank where it held none - and the
    tables written that read the game's file before, and no longer do."""
    fields = {field.key: field for group in groups for field in group.settings}
    for one in targets:
        _read_settings(library, one)
    before: dict[str, tuple[str, dict[str, str]]] = {}
    set_to: set[str] = set()
    for key, value in values.items():
        for one in targets:
            held = one["values"].get(key) or {}
            if (bool(held.get("set_here")) if value == ""
                    else not already_uses(one, fields[key], value, False)):
                table_id = str(one["table"].get("id") or "")
                before.setdefault(table_id, (one["launcher_id"], {}))[1][key] = \
                    str(held.get("value") or "") if held.get("set_here") else ""
                if value != "":
                    set_to.add(table_id)
    cut = [one["table"] for one in targets
           if str(one["table"].get("id") or "") in set_to and one["reads_game"]]
    return before, cut


async def write_selection(library: Any, state: dict[str, Any], targets: list[dict[str, Any]],
                          groups: Sequence[Any], values: dict[str, str]) -> dict[str, Any]:
    """A value to each target not using it yet, and a blank to each that sets one. Returns
    under `undo` what each table it changed held before, `blank` where the write only
    cleared, `cut` as `write_shared` does, and `failed` for the tables that could not be
    written, each with its `reason`."""
    before, cut = await offload.io(_plan_selection, library, targets, groups, values)
    together: dict[tuple[str, tuple[str, ...]], list[str]] = {}
    for table_id, (launcher_id, held) in before.items():
        together.setdefault((launcher_id, tuple(sorted(held))), []).append(table_id)
    failed: list[dict[str, Any]] = []
    for (launcher_id, keys), ids in together.items():
        try:
            failed += await _write_tables(library, state, launcher_id,
                                          {key: values[key] for key in keys}, ids)
        except Exception as exc:  # noqa: BLE001 - these tables are named, the rest go on
            failed += [{"table": one, "reason": why(exc)} for one in ids]
    lost = {str(one.get("table")) for one in failed}
    return {"cut": [one for one in cut if str(one.get("id")) not in lost],
            "blank": all(value == "" for value in values.values()), "failed": failed,
            "undo": [(table_id, launcher_id, held)
                     for table_id, (launcher_id, held) in before.items()
                     if table_id not in lost]}


async def _write_tables(library: Any, state: dict[str, Any], launcher_id: str,
                        values: dict[str, str], ids: list[str]) -> list[dict[str, Any]]:
    """The same values into each of `ids`, waiting out the job that several make. Returns
    the tables that could not be written."""
    done = await offload.io(library.write_launcher_config_tables, launcher_id, values, ids)
    job_id = str(done.get("id") or "")
    if not job_id:
        return []
    if callable(watch := state.get("watch_jobs")):
        watch()
    while True:
        await asyncio.sleep(JOB_POLL_S)
        found = await offload.io(library.job, job_id)
        if found.get("state") != "running":
            break
    library.settings_written()
    if found.get("state") == "failed":
        raise RuntimeError(str(found.get("error") or ""))
    result = found.get("result") or {}
    failed = list(result.get("failed") or ())
    reached = set(result.get("written") or ()) | {str(one.get("table")) for one in failed}
    return failed + [{"table": one, "reason": ""} for one in ids if one not in reached]


@on_page
async def _set_for_all(inner: dict[str, Any], others: list[dict[str, Any]],
                       own: Sequence[tuple[Any, str]], shares_here: bool,
                       tables: list[dict[str, Any]],
                       rebuild: Callable[[], Awaitable[Any]]) -> None:
    try:
        cut = await offload.io(lambda: [
            table for field, value in own
            for table in write_for_all(inner["library"], others, field, value, shares_here)])
    except Exception as exc:  # noqa: BLE001 - said, never raised into the page
        ui.notify(t("said.save_failed"), caption=why(exc), type="negative")
    else:
        ui.notify(t("manager.app_settings.apply_done", count=len(others) + 1),
                  type="positive")
        if cut:
            all_settings.no_longer_reads_game(cut, tables)
    await all_settings._keeping_place(ui.context.client, rebuild, own[0][0].key)


@dataclass
class MoveToAll:
    """Move to All Tables, beside a value the selection holds as one that at least one of
    its tables sets itself. A pair's row hands in its other rows as `paired`, and is
    offered only when each of them set here can be moved too. `rebuild` draws again
    whatever offered it.

    Dimmed while a table plays and where the selection uses more than one launcher, which
    have an All Tables each."""

    inner: dict[str, Any]
    offered: frozenset[str]
    rebuild: Callable[[], Awaitable[Any]]

    async def ready(self, values: dict[str, Any]) -> None:
        """Nothing to read: what follows All Tables is counted when the move is asked for."""

    def _own(self, key: str, held: dict) -> bool:
        return key in self.offered and bool(held.get("set_here")) \
            and bool(held.get("in_effect", True)) and not held.get("varies") \
            and bool(held.get("value"))

    def __call__(self, held: dict, field: Any,
                 *paired: tuple[dict, Any]) -> Callable[[], None] | None:
        rows = ((held, field), *paired)
        own = [(one, str(said.get("value") or "")) for said, one in rows
               if self._own(one.key, said)]
        if not own or any(said.get("set_here") and not self._own(one.key, said)
                          for said, one in rows):
            return None
        launchers = sorted({str(one["launcher_id"]) for one in self.inner["shared"]})
        playing = bool(self.inner.get("playing"))
        if playing:
            hint = t(all_settings.PLAYING_NOTE)
        elif len(launchers) > 1:
            hint = t("manager.app_settings.move_all.launchers", count=len(launchers))
        else:
            hint = t("manager.app_settings.move_all.help")
        return panel.action(
            t("manager.app_settings.move_all"),
            partial(_move_to_all, self.inner, own, launchers[0], self.rebuild),
            icon=verbs.MOVE, inline=True, enabled=not playing and len(launchers) == 1,
            hint=hint)


@on_page
async def _move_to_all(inner: dict[str, Any], own: Sequence[tuple[Any, str]],
                       launcher_id: str, rebuild: Callable[[], Awaitable[Any]]) -> None:
    """Asks, then writes the values as the launcher's own and takes them off each selected
    table that sets them. The launcher write can be refused for a file nobody can write,
    so nothing is cleared before it has gone, and a clearing that fails puts it back."""
    library = inner["library"]
    keys = [field.key for field, _ in own]
    try:
        rows = await offload.io(library.load_tables)
        found = await offload.io(library.launcher_config, launcher_id, "", SCOPE_LAUNCHER)
    except Exception as exc:  # noqa: BLE001 - said, never raised into the page
        ui.notify(t("said.settings_unreadable"), caption=why(exc), type="negative")
        return
    selected = {str(one.get("id") or "") for one in inner["tables"]}
    wanted = set(keys)
    setting = sum(1 for one in inner["shared"]
                  if any(((one.get("values") or {}).get(key) or {}).get("set_here")
                         for key in keys))
    following = sum(1 for row in rows
                    if row.get("launcher") == launcher_id
                    and str(row.get("id") or "") not in selected
                    and not wanted <= set(row.get("launcher_settings_keys") or ()))
    held = found.get("values") or {}
    if all((held.get(field.key) or {}).get("set_here")
           and all_settings._same_value(field, str(held[field.key].get("value") or ""), value)
           for field, value in own):
        following = 0
    detail = [t("manager.app_settings.move_all.stop" if setting == len(inner["shared"])
                else "manager.app_settings.move_all.some", count=setting)]
    if following:
        detail.append(t("manager.app_settings.move_all.follow", count=following))
    label = _move_label(inner["config_groups"], own)
    if not await confirm.ask(t("manager.app_settings.move_all.ask", setting=label),
                             detail=detail, confirm=t("word.move"), icon=verbs.MOVE,
                             danger=False):
        return
    try:
        held = (await offload.io(library.launcher_config, launcher_id, "",
                                 SCOPE_LAUNCHER)).get("values") or {}
        before = {key: str((held.get(key) or {}).get("value") or "")
                  if (held.get(key) or {}).get("set_here") else "" for key in keys}
        await offload.io(library.write_launcher_config, launcher_id,
                         {field.key: value for field, value in own})
    except Exception as exc:  # noqa: BLE001 - said, never raised into the page
        ui.notify(t("said.save_failed"), caption=why(exc), type="negative")
        await all_settings._keeping_place(ui.context.client, rebuild, keys[0])
        return
    try:
        wrote = await write_selection(library, inner["state"], inner["shared"],
                                      inner["config_groups"], dict.fromkeys(keys, ""))
    except Exception as exc:  # noqa: BLE001 - said, never raised into the page
        wrote = {"failed": [], "undo": []}
        ui.notify(t("said.save_failed"), caption=why(exc), type="negative")
    else:
        _said_failed(wrote.get("failed"))
    if wrote.get("undo"):
        undo.offer(t("manager.app_settings.move_done", setting=label),
                   partial(_move_back, inner, launcher_id, before, wrote["undo"]))
    else:
        with contextlib.suppress(Exception):
            await offload.io(library.write_launcher_config, launcher_id, before)
    await all_settings._keeping_place(ui.context.client, rebuild, keys[0])


def _move_label(groups: Sequence[Any], own: Sequence[tuple[Any, str]]) -> str:
    """The row the user sees: a pair by its own name, a lone setting by its label."""
    keys = {field.key for field, _ in own}
    for pair in named_pairs(groups):
        if keys & set(pair.keys):
            return str(pair.label)
    return " / ".join(field.label for field, _ in own)


async def _move_back(inner: dict[str, Any], launcher_id: str, before: dict[str, str],
                     changes: Sequence[tuple[str, str, dict]]) -> None:
    """The launcher's own values as they were, then each table's. The tables stay as they
    are when the launcher refuses, so All Tables and they never disagree."""
    await offload.io(inner["library"].write_launcher_config, launcher_id, before)
    await _put_back(inner, changes)
