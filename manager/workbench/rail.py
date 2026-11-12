"""The rail: which sections a subject shows, how they are grouped, and the table of sections."""

from __future__ import annotations

from functools import partial
from typing import Any

from nicegui import ui

from common.i18n import t
from manager.backups import backups as backups_page
from manager.devices import devices as devices_page
from manager.extensions import ext_groups, ext_mark
from manager.frontend import players as players_page
from manager.frontend import themes as themes_page
from manager.kit import busy, deeplink, game_tables, panel
from manager.library import row_drag
from manager.workbench.all_settings import (
    _all_settings,
    _all_settings_shown,
    _launcher_config_sections,
)
from manager.workbench.collection import (
    _all_games_details,
    _collection_details,
    _collection_games,
    _games_label,
)
from manager.workbench.core import (
    _GRIP,
    DEFAULT_SECTION,
    Section,
    _docked,
    _section_row,
    dock_height,
    dock_width,
)
from manager.workbench.device import (
    _device_capabilities,
    _device_details,
    _device_logs,
    _device_software,
)
from manager.workbench.file import (
    _asset_file_block,
    _asset_has_stage,
    _media_file_block,
    _media_has_stage,
)
from manager.workbench.game_assets import _assets_block, _assets_label
from manager.workbench.game_guides import _guides_block, _guides_label
from manager.workbench.game_media import _media_block
from manager.workbench.game_play import _play_block
from manager.workbench.game_table import (
    _collections_block,
    _collections_label,
    _game_block,
    _game_selection_marks,
    _media_label,
    _selection_marks,
    _selection_settings,
    _table_block,
    _table_settings,
)
from manager.workbench.launcher import _app_keeps_settings, _config_backups, _launcher_details
from manager.workbench.subject_verbs import SUBJECT_VERBS
from manager.workbench.tag import _tag_details, _tag_games

# The subjects whose panel is a place a picture can be dropped, and the field naming whose.
PICTURE_OWNERS = {"collection": "name", "player": "id"}

# The panels whose subject is the file itself: narrow, they open on the file as it looks
# and the details come after. The game's panel keeps the map first.
VIEWER_FIRST = frozenset({"media_file", "asset_file"})


def sections_for(subject: str) -> tuple[Section, ...]:
    """The rail for this subject, in order."""
    return tuple(item for item in SECTIONS if subject in item.subjects)


def chosen_section(state: dict[str, Any], subject: str = "game",
                   rail: tuple[Section, ...] | None = None) -> str:
    """The section this client is on, seeded once and kept across games.

    "" when every section is closed, which is a place like any other.

    Held across games so stepping down a list does not re-pick a section at every stop,
    which is what looking at one kind across a library needs. Held across subjects too
    where it can be - but a section the new subject has no answer for is not one of
    those, and then the rail's own default is where you land rather than wherever the
    list happens to start.
    """
    rail = sections_for(subject) if rail is None else rail
    known = {item.key for item in rail}
    if state.get("section") == deeplink.COLLAPSED:
        # Asked for by the address, so it is kept - including across a change of subject.
        # Seeding a default here would reopen a panel the address said to leave closed.
        return deeplink.COLLAPSED
    if subject == "all_games":
        return rail[0].key
    if state.get("section") not in known:
        wanted = DEFAULT_SECTION.get(subject, "")
        state["section"] = wanted if wanted in known else rail[0].key
    return state["section"]


async def _rail(context: dict[str, Any], subject: str,
                state: dict[str, Any]) -> None:
    """The rail and the open section, for whatever subject the panel is about.

    Three regions, in reading order: which mode you are in, what that mode gives you to
    browse, and what you are working on. The outline must not scroll with what it
    points at, so this row is the fixed frame and only the body scrolls.

    Wide, the rail is a column down the left and the work fills the space beside it, and
    the rows are wrapped so they scroll without dragging the work along - a rail longer
    than the panel would otherwise take the work with it when it scrolled.

    Narrow, the same rows are a strict accordion with the work under the row that opened
    it. The wrapper would break that, so the stylesheet drops it there and `order` puts
    the work back in its place among the rows. Three buckets is all it takes: rows down
    to the open one, the work, then the rest. The markup is the same at either width, so
    nothing is rebuilt on a drag.
    """
    header = state.get("subject_menu")
    verbs_of = SUBJECT_VERBS.get(subject)
    if header is not None and verbs_of is not None:
        panel.subject_menu(header, verbs_of(context))
    if subject in ("game", "table") and (declare_drop := state.get("declare_drop")):
        declare_drop(game=context["game_id"], table=context.get("lens") or "")
    elif subject in PICTURE_OWNERS and (declare_drop := state.get("declare_drop")):
        key = PICTURE_OWNERS[subject]
        declare_drop(picture=str(context[subject].get(key) or ""), of=subject)
    declared = context.get("rail") or (
        (*sections_for(subject), *_group_sections(context)) if subject in ("game", "table")
        else sections_for(subject))
    rows = tuple(item for item in declared if item.shown is None or item.shown(context))
    section = chosen_section(state, subject, rows)
    # A section this subject has no answer for is not a place to land, so an address or a
    # remembered choice that names one opens the rail's own default instead.
    if section != deeplink.COLLAPSED and section not in {item.key for item in rows}:
        section = rows[0].key if rows else deeplink.COLLAPSED
    if section == deeplink.COLLAPSED and len(rows) == 1:
        section = rows[0].key
    body = None
    open_item = next((item for item in rows if item.key == section), None)
    docked = open_item is not None and _docked(open_item, context)
    with ui.element("div").classes("w-full grow min-h-0 manager-sections") as sections:
        if subject == "collection":
            sections._props[row_drag.TARGET] = context["collection"]["name"]
        # The rows are their own region so they can scroll without taking the work with
        # them. A rail longer than the panel is the ordinary case for a device, and one
        # that moves what you are reading is the wrong half to move.
        with ui.element("div").classes("min-h-0 manager-section-rail"):
            heading = ""
            after = 0
            for item in rows:
                if item.group and item.group != heading:
                    ui.label(item.group).classes("manager-group manager-rail-group") \
                        .style(f"order: {after}")
                heading = item.group
                _section_row(context, item, item.key == section, order=after)
                if item.key == section:
                    after = panel.AFTER_WORK_ORDER
        if open_item is not None:
            work = ui.element("div").classes("min-w-0 manager-section-work") \
                .style(f"order: {panel.WORK_ORDER}")
            if docked:
                work.classes(add="manager-has-dock")
                work.style(f"--dock-h: {dock_height(state, section)}px")
                if dock_width(section):
                    work.style(f"--dock-w: {dock_width(section)}px")
                work._props["data-dock-section"] = section
                if subject in VIEWER_FIRST:
                    work._props["data-viewer-first"] = "1"
            with work:
                body = ui.column().classes("min-w-0 overflow-auto gap-0 "
                                           "manager-workbench-body")
                if docked:
                    ui.element("div").classes("manager-dock-grip") \
                        .tooltip(t("manager.workbench.drag_resize"))
                    context["dock"] = ui.column().classes("min-w-0 gap-0 manager-dock")
                else:
                    context["dock"] = None
    if body is not None:
        with body, busy.held(body):
            await _one_section(context, section, rows)
        if context.get("dock") is not None:
            ui.run_javascript(_GRIP)


def _group_sections(context: dict[str, Any]) -> tuple[Section, ...]:
    """The groups extensions made a section of their own, after core's."""
    return tuple(_group_row(key, extension, group)
                 for key, extension, group in ext_groups.own(context))


def _group_row(key: str, extension: dict[str, Any], group: dict[str, Any]) -> Section:
    label = ext_groups.title(extension, group)
    return Section(key, lambda _: label, partial(_group_section, extension, group),
                   icon=group.get("icon") or "extension", mark=ext_mark.drawer(extension))


async def _group_section(extension: dict[str, Any], group: dict[str, Any],
                         context: dict[str, Any]) -> None:
    with ui.column().classes("gap-0 manager-form w-full"):
        ext_groups.show_in_place(context, extension, group)


async def _one_section(context: dict[str, Any], key: str,
                       rows: tuple[Section, ...] = ()) -> None:
    """The chosen section's content, under its own name where it draws no heading."""
    item = next(item for item in rows or SECTIONS if item.key == key)
    if item.titled:
        with ui.column().classes("w-full gap-0 manager-form"):
            panel.facts(ui, [(panel.HEADING, item.label(context))])
    await item.build(context)


SECTIONS: tuple[Section, ...] = (
    # The game first, then the file: a table belongs to a game, and reading down is
    # reading from the thing that contains to the thing contained.
    Section("game_details", lambda _: t("manager.workbench.game_section"), _game_block),
    Section("table_details", lambda _: t("manager.workbench.table_section"), _table_block,
            subjects=frozenset({"table"})),
    Section("table_settings", lambda _: t("manager.workbench.table_settings"),
            _table_settings, subjects=frozenset({"table"})),
    Section("selection_marks", lambda _: t("manager.workbench.details"),
            _selection_marks, subjects=frozenset({"selection"})),
    Section("game_selection_marks", lambda _: t("manager.workbench.details"),
            _game_selection_marks, subjects=frozenset({"game_selection"})),
    Section("selection_settings", lambda _: t("manager.workbench.table_settings"),
            _selection_settings, subjects=frozenset({"selection"})),
    Section("play", lambda _: game_tables.PLAY, _play_block),
    Section("collections", _collections_label, _collections_block, titled=True),
    Section("media", _media_label, _media_block, dock=True, titled=True),
    # Beside Media, not under Details: both answer "what does this game hold", one
    # for what a screen shows and one for what a launch needs.
    Section("assets", _assets_label, _assets_block, dock=True),
    Section("guides", _guides_label, _guides_block, titled=True),
    # A launcher, in reading order: what it is and what it runs, then the program's own
    # settings grouped as the app declares them, then the copies kept of its file.
    Section("launcher_details", lambda _: t("manager.workbench.details"), _launcher_details,
            subjects=frozenset({"launcher"})),
    *_launcher_config_sections(),
    Section("launcher_all", lambda _: t("manager.workbench.all_settings"), _all_settings,
            subjects=frozenset({"launcher"}), shown=_all_settings_shown),
    Section("launcher_backups", lambda _: t("manager.workbench.settings_file"),
            _config_backups, subjects=frozenset({"launcher"}), shown=_app_keeps_settings,
            titled=True),
    Section("collection_details", lambda _: t("manager.workbench.details"),
            _collection_details,
            subjects=frozenset({"collection"})),
    Section("collection_games", _games_label, _collection_games,
            subjects=frozenset({"collection"})),
    Section("all_games_details", lambda _: t("manager.workbench.details"),
            _all_games_details, subjects=frozenset({"all_games"})),
    Section("media_file", lambda _: t("word.file"), _media_file_block,
            subjects=frozenset({"media_file"}), dock=_media_has_stage),
    Section("tag_details", lambda _: t("manager.workbench.details"), _tag_details,
            subjects=frozenset({"tag"}), titled=True),
    Section("tag_games", lambda context: t("manager.tags.games_counted",
                                           count=int(context["tag"].get("games") or 0)),
            _tag_games, subjects=frozenset({"tag"}), titled=True),
    Section("asset_file", lambda _: t("word.file"), _asset_file_block,
            subjects=frozenset({"asset_file"}), dock=_asset_has_stage),
    # A device, in reading order: what it is, what it is running, what it can be asked
    # for, what it has written down, and what it can be told to do. Its settings are not
    # here at all - they are a door in Details into that install's own Manager, because a
    # build's settings belong to that build and rendering them from here meant fetching
    # its schema over HTTP.
    Section("device_details", lambda _: t("manager.workbench.details"), _device_details,
            subjects=frozenset({"device"})),
    Section("device_software", lambda _: t("word.software"), _device_software,
            subjects=frozenset({"device"}),
            shown=lambda context: not devices_page.is_phone(context), titled=True),
    Section("device_capabilities", lambda _: t("manager.workbench.capabilities"),
            _device_capabilities,
            subjects=frozenset({"device"}), titled=True),
    Section("device_logs", lambda _: t("manager.workbench.logs"), _device_logs,
            subjects=frozenset({"device"}),
            shown=lambda context: devices_page.serves(context, "logs"), titled=True),
    Section("theme_details", lambda _: t("manager.themes.theme"), themes_page.details,
            subjects=frozenset({"theme"}), titled=True),
    Section("theme_settings", lambda _: t("manager.themes.settings"),
            themes_page.settings_section, subjects=frozenset({"theme"}),
            shown=lambda context: bool(context["theme"].get("configurable")), titled=True),
    # A player: who they are, then an account per running extension offering one, which
    # the panel puts between these two, then what they have played.
    Section("player_details", lambda _: t("manager.workbench.details"),
            players_page.details, subjects=frozenset({"player"}), titled=True),
    Section("player_plays", lambda _: t("manager.players.plays"), players_page.plays,
            subjects=frozenset({"player"}),
            shown=lambda context: players_page.has_plays(context["player"]), titled=True),
    # A copy, then what a backup holds.
    Section("backup_details", lambda _: t("manager.workbench.details"), backups_page.details,
            subjects=frozenset({"backup"}), titled=True),
    Section("backup_contents", lambda _: t("manager.backups.contents"), backups_page.contents,
            subjects=frozenset({"backup"}), shown=backups_page.has_contents, titled=True),
)
