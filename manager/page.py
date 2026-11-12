"""The Manager shell: nav, content and the workbench that follows the selection."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from functools import partial
from typing import Any
from urllib.parse import parse_qsl

import requests
from nicegui import background_tasks, run, ui

from common import icons
from common.devices import device_client, install_identity, install_setup
from common.failures import why
from common.games.collection_store import BUILTIN_ALL
from common.i18n import t
from common.release import whats_new
from common.runtime.jobs import (
    DONE,
    KIND_BACKGLASS_ORDER,
    KIND_LIBRARY_SCAN,
    KIND_MEDIA_CAPTURE,
    KIND_VPS_ROLLUP,
)
from manager import power, rail_marks
from manager import update_count as update_count_module
from manager.backups import backups as backups_page
from manager.devices import devices as devices_page
from manager.extensions import ext_links, ext_mark, ext_pages
from manager.extensions import ext_lists as community_page
from manager.frontend import launchers as launchers_page
from manager.frontend import players as players_page
from manager.frontend import theme_sources
from manager.frontend import themes as themes_page
from manager.kit import (
    appearance,
    busy,
    confirm,
    deeplink,
    grid,
    media_ownership,
    offload,
    order_notice,
    remembered,
    report_groups,
    undo,
    verbs,
    views,
    wizard,
)
from manager.kit import panel as panel_parts
from manager.kit.api import ApiClient
from manager.kit.data import Library
from manager.kit.on_page import on_page
from manager.kit.together import together
from manager.library import assets as assets_page
from manager.library import collections as collections_page
from manager.library import discovery as discovery_page
from manager.library import (
    discovery_kinds,
    drop_target,
    file_acts,
    games,
    row_drag,
    tageditor,
    text_editor,
    uploads,
    wizard_settle,  # noqa: F401 - registers the settle field with the wizard
)
from manager.library import discovery_releases as discovery_releases_page
from manager.library import media as media_page
from manager.library import record as recorder
from manager.overview import needs
from manager.remote import remote
from manager.security import security, sign_in_notice
from manager.settings import folders, sections, shelves, waiting
from manager.settings import settings as settings_page
from manager.settings.library_folders import KIND_IMPORT, hold_library
from manager.setup import setup as setup_page
from manager.system import about as about_page
from manager.system import job_report, jobs_list
from manager.system import jobs as jobs_page
from manager.system import logs as logs_page
from manager.system import metrics as metrics_page
from manager.system import whats_new as whats_new_page
from manager.workbench import builds, core, game_guides, game_table, rail
from manager.workbench import collection as wb_collection

logger = logging.getLogger("vpinfe.manager.page")

# Every pane's header band. `.manager-nav-header` and `.manager-workbench` carry the
# same two numbers in the stylesheet.
HEADER_H_PX = 48
HEADER_TOP_PX = 8

# Wide enough for the longest label at the nested indent, with the scrollbar the
# rail now needs: "Collections" indented is the widest thing in here.
NAV_WIDE_PX = 252
# Past the width where the panel stops being a rail beside its work and becomes an
# accordion. Opening into the accordion made the stacked reading the one everybody sees
# first, when it is the fallback for a pane squeezed down on purpose. 640 rather than
# the breakpoint itself: at the breakpoint the labels wrap and a path field shows about
# a dozen characters, which is a rail beside a column too thin to work in.
WORKBENCH_WIDE_PX = 640
# What the panel needs to hold its three regions, not the smallest a pane can be.
# Below this the honest move is the rail, which is one click away.
WORKBENCH_MIN_PX = 320
# Past the work width, so the divider reaches every mode on its own and Full is the far
# end of one continuum rather than the only way in. Short of squeezing the list out -
# that is Full's job, and Full has a control that brings it back.
WORKBENCH_MAX_PX = 1000
# Below a desk-sized window the nav gives up its labels rather than its fifth of the
# screen. The rail already existed as a manual toggle and simply never fired on size;
# width drives that same state rather than adding a second way to collapse, so a toggle
# by hand still wins afterwards. Phone is not addressed - that needs a different shell,
# and the phone-shaped job has its own surface.
NAV_NARROW_PX = 1100
# Quasar's own width for turning a drawer into an overlay, which ignores `mini`: the rail
# then covers the page at full width with its labels already hidden. Zero keeps the drawer
# in the layout at every width, where the rail is the narrow form.
NAV_OVERLAY_PX = 0
# One rail width for both panels - they collapse to the same thing.
RAIL_PX = 57

# Every entry is a place, not a thing. Gallery is gone - the media map in the details
# pane answers "what is missing here" and the Media section answers "what is missing
# anywhere", which is what it was reaching for.
# Grouped, because four of these are what the library holds and the rest are not.
# A subject is a place, not transient state - so Tables and Tags are
# rail entries rather than a mode a dropdown puts the Games page into.
# Library is a rail entry like the rest, with the four it holds nested under it - not a
# bare heading, which would be the one thing in this rail that is not a place. It is a
# disclosure rather than a destination: a row that both navigated and collapsed would
# make one click mean two things, and the children are the destinations.
NAV_PARENT = ("library", "manager.section.library", "inventory_2")

# What the `frontend` feature owns, gathered the way Library gathers what `library` owns.
# The rule the two of them make: a feature with more than one subject gets a container.
#
# Launchers belongs here rather than under System - a launcher is neither configuration
# nor a record. It is also gated on a feature rather than on `core`, which is the tell,
# and the same tell keeps Devices out of System's company on the gate alone.
NAV_FRONTEND = ("frontend", "manager.section.frontend", "smart_display")

# This install itself: how it is set up, what it has recorded doing, and what it is.
# Records are places; configuration is a setting - which is why the log *viewer* is here
# and the logger's own settings are a page inside Settings.
NAV_SYSTEM = ("system", "manager.section.system", "settings")

NAV_COMMUNITY = ("community", "manager.section.community", "groups")

# Which feature each destination answers for, `core` being the one every install has, or
# several, any one of which shows it. An install without a feature does not show its
# section at all - not greyed and not empty, absent - because a section for something
# this machine is not for is a place with nothing in it.
NavItem = tuple[str, str, str, str | tuple[str, ...]]

NAV_GROUPS: tuple[tuple[tuple[str, str, str] | None, tuple[NavItem, ...]], ...] = (
    (None, (("overview", "manager.section.overview", "space_dashboard",
        install_identity.OVERVIEW),)),
    # Media sits with the grains of the library it is one of, ahead of the two that
    # organize it rather than being part of it.
    (NAV_PARENT, (("games", "manager.section.games", icons.GAMES, install_identity.LIBRARY),
                  ("tables", "manager.section.tables", icons.TABLES, install_identity.LIBRARY),
                  ("media", "manager.section.media", "perm_media", install_identity.LIBRARY),
                  ("assets", "manager.section.assets", "widgets", install_identity.LIBRARY),
                  ("collections", "manager.section.collections", "collections_bookmark",
                   install_identity.LIBRARY),
                  ("tags", "manager.section.tags", "sell", install_identity.LIBRARY),
                  ("discovery", "manager.section.discovery", "travel_explore",
                   install_identity.LIBRARY))),
    # Subjects, not settings: both are collections of objects with per-row actions,
    # which is not what a page of (label, value) pairs does. The frontend's *settings*
    # stay in Settings, the way media's do - one holds things, the other holds values.
    (NAV_FRONTEND, (("players", "manager.section.players", "person",
                     install_identity.FRONTEND),
                    ("launchers", "manager.section.launchers", "rocket_launch",
                     install_identity.FRONTEND),
                    ("themes", "manager.section.themes", "palette", install_identity.FRONTEND))),
    (NAV_COMMUNITY, ()),
    # Last, and always here: every other section exists because a feature is enabled,
    # and this is where features are switched on. Ordered configuration, then what this
    # install knows about, then its records, then what it is.
    (NAV_SYSTEM, (("settings", "manager.section.settings", "tune", install_identity.CORE),
                  ("devices", "manager.section.devices", "devices", install_identity.DEVICES),
                  ("metrics", "manager.section.metrics", "monitor_heart", install_identity.CORE),
                  ("logs", "manager.section.logs", "description", install_identity.CORE),
                  ("jobs", "manager.section.jobs", "work_history", install_identity.CORE),
                  ("backups", "manager.section.backups", "history", install_identity.CORE),
                  ("about", "manager.section.about", "info", install_identity.CORE))),
)


class _Title(ui.column):
    """The panel header's name for its subject, and the slot beside it holding the
    subject's menu. Cleared together: every drawing path clears the title, and a menu
    left behind would offer to act on a subject that has gone."""

    menu: ui.row | None = None

    def clear(self) -> Any:
        if self.menu is not None:
            self.menu.clear()
        return super().clear()


def features_of(item: NavItem) -> tuple[str, ...]:
    return item[3] if isinstance(item[3], tuple) else (item[3],)


def nav_for(features: list[str] | tuple[str, ...] | None,
            added: Mapping[str, tuple[NavItem, ...]] | None = None
            ) -> list[tuple[tuple[str, str, str] | None, tuple[NavItem, ...]]]:
    """The rail this install has. A group whose entries have all gone goes with them -
    a disclosure with nothing under it is a control that does nothing.

    `features` is an install's own answer, which already has `core` in it and is taken at
    its word: an empty list is an install that is for nothing, as `install_identity.features`
    reads an empty setting, and gets System alone. `None` is an install that has not
    answered, and gets the ordinary rail. `core` is held either way, which is what keeps
    System in a rail an install has switched everything else out of.

    `added` is what extensions put in each group, by the area's name. It follows the
    group's own entries, except that About stays last in System, and is held to the same
    features.
    """
    said = install_identity.DEFAULT_FEATURES if features is None else features
    held = {str(name).strip().lower() for name in said} | {install_identity.CORE}
    out = []
    for parent, items in NAV_GROUPS:
        theirs = (added or {}).get(parent[0], ()) if parent else ()
        ordered = (*items[:-1], *theirs, items[-1]) if parent is NAV_SYSTEM \
            else (*items, *theirs)
        kept = tuple(item for item in ordered if not held.isdisjoint(features_of(item)))
        if kept:
            out.append((parent, kept))
    return out

def landing_for(views: list[str], last: str = "") -> str:
    """Where a bare `/manager` opens: where you left off, else the first place in the
    rail. `last` is dropped unless the rail still holds it."""
    if last and last in views:
        return last
    return next(iter(views), "settings")


# What the header calls each destination. A section owns a subject too, but that is a
# fact about the data behind the page, not a caption for it - printing "one row is one
# collection" over a page of cards described something that was not on the screen.
SECTIONS = {
    "overview": "manager.section.overview",
    "games": "manager.section.games",
    "tables": "manager.section.tables",
    "tags": "manager.section.tags",
    "discovery": "manager.section.discovery",
    "collections": "manager.section.collections",
    "media": "manager.section.media",
    "assets": "manager.section.assets",
    "devices": "manager.section.devices",
    "settings": "manager.section.settings",
    "launchers": "manager.section.launchers",
    "themes": "manager.section.themes",
    "players": "manager.section.players",
    "metrics": "manager.section.metrics",
    "logs": "manager.section.logs",
    "jobs": "manager.section.jobs",
    "backups": "manager.section.backups",
    "about": "manager.section.about",
}


def _version(said: Any) -> str:
    """A version as a person writes one.

    The tag already carries its own "v", and a build that is not a release carries a
    word instead - so prefixing unconditionally produced "vv2.6.1" from one and
    "vdev-local" from the other.
    """
    text = str(said or "").strip() or "?"
    return text if not text[:1].isdigit() else f"v{text}"


JOBS_RUNNING_S = 2.0
JOBS_IDLE_S = 5.0
# Per browser: when Jobs was last opened, which the rail's count of jobs to look at runs from.
JOBS_SEEN = "jobs_seen"


def failed_since(jobs: list[dict[str, Any]], opened: float,
                 dismissed: set[str]) -> dict[str, Any] | None:
    """The job that ended last, when it failed after `opened` and is not dismissed.

    `jobs` is the install's list, running ones first and then the latest to end.
    """
    ended = next((job for job in jobs if job.get("state") != "running"), None)
    if (ended is None or ended.get("state") != "failed"
            or float(ended.get("finished_at") or 0) < opened
            or str(ended.get("id") or "") in dismissed):
        return None
    return ended


def reads_ended_since(jobs: list[dict[str, Any]], opened: float,
                      told: set[str]) -> list[str]:
    """The ids of the library reads that finished after `opened`, not yet told."""
    return [str(job.get("id") or "") for job in jobs
            if job.get("kind") == KIND_LIBRARY_SCAN and job.get("state") == DONE
            and float(job.get("finished_at") or 0) >= opened
            and str(job.get("id") or "") not in told]


def comparisons_ended_since(jobs: list[dict[str, Any]], opened: float,
                            told: set[str]) -> list[str]:
    """The ids of the comparisons with VPS that finished after `opened`, not yet told: the
    sync of the spreadsheet ends in one, and a later version may have come with it."""
    return [str(job.get("id") or "") for job in jobs
            if job.get("kind") == KIND_VPS_ROLLUP and job.get("state") == DONE
            and float(job.get("finished_at") or 0) >= opened
            and str(job.get("id") or "") not in told]


def orders_unset_since(jobs: list[dict[str, Any]], opened: float,
                       told: set[str]) -> list[str]:
    """The ids of the passes that set tables' DMD order and finished after `opened` with a
    table they could not write, not yet told."""
    return [str(job.get("id") or "") for job in jobs
            if job.get("kind") == KIND_BACKGLASS_ORDER and job.get("state") == DONE
            and int(job.get("problems") or 0)
            and float(job.get("finished_at") or 0) >= opened
            and str(job.get("id") or "") not in told]


def list_is_idle(state: dict[str, Any]) -> bool:
    """A games or tables list is on screen with nothing open, ticked or typed in the
    search box, so it can be redrawn without closing a panel or dropping any of them."""
    return state.get("view") in LIST_VIEWS and not state.get("game") \
        and not state.get("ticked") and not state.get("searching")


def imports_ended_since(jobs: list[dict[str, Any]], opened: float,
                        told: set[str]) -> list[dict[str, Any]]:
    """The imports that finished after `opened`, not yet told.

    A failed one is the rail's failure line, not a finish."""
    return [job for job in jobs
            if job.get("kind") == KIND_IMPORT and job.get("state") == DONE
            and float(job.get("finished_at") or 0) >= opened
            and str(job.get("id") or "") not in told]


# What the panel says when nothing is selected, per page. Named for what *that* page
# selects: three of them select something and the rest do not, and one asking for a game
# on a page with no games is the panel describing a different screen.
EMPTY_PANE = {
    "games": ("manager.page.game", "manager.page.select_game"),
    "tables": ("manager.page.table", "manager.page.select_table"),
    "collections": ("manager.page.collection", "manager.page.select_collection"),
    "tags": ("manager.page.tag", "manager.page.select_tag"),
    "discovery": ("manager.page.entry", "manager.page.select_entry"),
    "media": ("manager.page.media", "manager.page.select_media"),
    "assets": ("manager.page.assets", "manager.page.select_asset"),
    "devices": ("manager.page.device", "manager.page.select_device"),
    "launchers": ("manager.page.launcher", "manager.page.select_launcher"),
    "themes": ("manager.page.theme", "manager.page.select_theme"),
    "players": ("manager.page.player", "manager.page.select_player"),
    "backups": ("manager.page.backup", "manager.page.select_backup"),
}

# The pages the pane has a role on. Media is one of them: a row is one game's slot, so
# the panel opens on it the way it does for a game. Everywhere else it is hidden -
# `render` adds `manager-no-pane` - and the user's own open/closed and width are left alone,
# so coming back to a page that selects something restores what they arranged.
WORKBENCH_VIEWS = frozenset(EMPTY_PANE)
LIST_VIEWS = frozenset({"games", "tables"})


def _read_hub() -> dict[str, Any]:
    """Every blocking call the page needs, made once off the event loop.

    The Manager consumes its own process over HTTP, so a synchronous page handler asking
    the API for the library deadlocks: uvicorn cannot answer a request it is blocked
    inside. Keeping the loop free is the cost of the boundary, and it is worth paying -
    the alternative is importing the services, which is what the boundary exists to
    prevent.
    """
    from common.config.paths import get_ini_config

    client = ApiClient()
    library = Library(client)
    _, devices, extensions, trouble, shell = together(
        library.load, client.devices, client.extensions, settings_page.local_trouble,
        partial(_read_shell, client))
    return {
        "library": library,
        "devices": devices,
        "extensions": extensions,
        "pending": install_setup.pending(get_ini_config()),
        "whats_new_due": whats_new.due(get_ini_config()),
        # This machine's own configuration, asked of this machine: a path is only
        # answerable by the install holding it, and the Manager is served by that one.
        "trouble": trouble,
        **shell,
    }


def _read_shell(client: ApiClient | None = None) -> dict[str, Any]:
    """What the shell draws its rail and its device rows from: this install's own answer,
    which a feature switch changes. Blocking, like `_read_hub`; a client of its own, so the
    answer is read rather than remembered."""
    client = client or ApiClient()
    capabilities = client.capabilities()
    return {
        "discovery": client.discovery(),
        # What a device can be asked about, which is what the frontend feature turns on.
        # Infrastructure carries no feature and is not a fact about a particular machine,
        # so it is not offered as a per-device row.
        "device_capabilities": [entry["name"] for entry in capabilities
                                if entry.get("feature") == install_identity.FRONTEND],
        "local_capabilities": set(device_client.served(capabilities)),
    }


def setup_line(*, window: bool) -> None:
    """The line over every page while Setup is unfinished, and the way back into it."""
    with ui.row().classes("items-center gap-2"):
        ui.label(t("manager.setup.not_finished")).classes("manager-help")
        panel_parts.action(t("word.continue"), lambda: setup_page.open_setup(window=window),
                           icon=verbs.GO, inline=True,
                           hint=t("manager.setup.open.help"))()


def arrives_in_setup(pending: bool, view: str) -> bool:
    """A bare address while Setup is unfinished opens it; a link naming a place shows
    that place, with the line above it."""
    return pending and not view.strip()


def arrives_in_whats_new(due: bool, view: str, *, window: bool) -> bool:
    """A bare address after an update opens what changed. A link naming a place shows
    that place, and the frontend's own window never does: nobody at the cabinet is
    reading release notes."""
    return due and not window and not view.strip()


def why_not_loaded(exc: Exception) -> str:
    """Why the first read failed, in this install's words.

    The client words a request nothing answered for a remote device, "It may be off or
    asleep", and the install serving this page is neither.
    """
    if isinstance(exc.__cause__, requests.Timeout):
        return t("manager.page.timeout")
    return why(exc)


@on_page
async def _group_of_report(library: Library, job_id: str,
                           group: str) -> report_groups.Group | None:
    """The games of one group of a kept report, or None, said on the page, where the report
    is gone or has no games in that group."""
    try:
        job = await offload.io(library.job, job_id)
    except Exception as exc:  # noqa: BLE001 - the reason belongs on screen
        ui.notify(t("manager.jobs.report_unavailable"), caption=why(exc), type="negative")
        return None
    found = report_groups.groups(job).get(group)
    if found is None:
        ui.notify(t("manager.report.no_group"), type="warning")
    return found


def _not_loaded(loading: ui.element, exc: Exception) -> None:
    """The loading line, replaced by what went wrong and a way to ask again."""
    loading.delete()
    with ui.column().classes("w-full h-full items-center justify-center gap-3"):
        ui.label(t("manager.page.load_failed")).classes("manager-empty-title")
        ui.label(why_not_loaded(exc)).classes("text-sm opacity-60")
        panel_parts.action(t("manager.page.try_again"), ui.navigate.reload,
                           icon=verbs.REFRESH)()


# The title is per page, not from ui.run: an app-wide title would put one surface's name
# in another's tab (this page, the remote, the editors).
# Both, because the bare host is what somebody types and /manager is what a link
# carries. One function serves them so there is no redirect to lose a query string.
#
# Five minutes to reconnect, not the default three seconds: a suspended background tab
# goes quiet for longer than that, and a page deleted under one comes back as a reload.
# On each route, because the timeout is the page's and these are two pages.
@on_page
async def _took_a_drop(
        library: Library, state: dict, redraw: Callable[[], None], drop: Any, *,
        added: bool = False) -> None:
    """What a drop meant, and the conversation that follows it.

    Where somebody let go is the whole of the targeting: `drop_target.resolve` reads the
    row, the cell and any declaration there against the view showing, and a drop that
    lands on nothing names the library, which is what makes it a new game rather than a
    guess about an existing one. `added` is a file brought in by the page's Add, which
    names the library the same way and is turned away in words about adding, not dropping.
    """
    from manager.library import import_dialog, uploads

    try:
        target = await offload.io(drop_target.resolve, library,
                                  str(state.get("view") or ""), drop.where)
    except Exception:
        logger.exception("manager: a drop's target could not be resolved")
        await _turned_away(library, drop, t("manager.uploads.not_work"))
        return
    if isinstance(target, drop_target.Refusal):
        await _turned_away(library, drop, target.text())
        return
    if not drop.upload_id:
        await _turned_away(library, drop, t("manager.drop.not_here"))
        return
    if target.picture:
        await _took_a_picture(library, state, drop, target)
        return
    if target.covered:
        ui.notify(t("manager.drop.covered"), type="info")
    game_id, game_dir, media_kind = target.game, target.folder, target.slot

    # A slot drop is not analyzed at all, and that is the whole point of one: the cell
    # said which game and which slot, so any image belongs on an image slot and is
    # written under that slot's own name. Asking the analyzer first rejected exactly
    # the file this is for - one whose name says nothing.
    analysis: dict[str, Any] = {}
    if not media_kind:
        read = await uploads.analyzed(library, drop.upload_id,
                                      game_named=bool(game_dir) and not target.asset)
        if read is None:
            return
        analysis = read

    # A drop with no game named is a new game if it brought a table. Without one it is for
    # a game that is already there, which only the person who dropped it can say.
    new_game = not game_dir and not media_kind
    if new_game and not analysis.get("has_game"):
        picked = await _which_game(library, drop, analysis, added=added)
        if picked is None:
            return
        game_id, game_dir, new_game = picked.game, picked.folder, False

    # Where a new game would go is settled before anything is planned, because the
    # answer can be "nowhere" and that is not something to discover halfway through.
    where = ""
    if new_game:
        # None is the cancel, which is not the same answer as the default location.
        chosen = await import_dialog.ask_where(library)
        if chosen is None:
            await run.io_bound(library.abort_upload, drop.upload_id)
            return
        where = chosen

    on_a_game = bool(game_dir) and not media_kind and not target.asset
    done = await game_table.after_a_row_drop(library, state, game_id, redraw) if on_a_game \
        else _put_right(state, redraw, game_id)
    await uploads.arrived(
        library, drop.upload_id, analysis=analysis, source=drop.name, file_count=drop.count,
        on_done=done,
        game_id=game_id, game_dir=game_dir, allow_new_game=new_game,
        media_kind=media_kind, location_id=where, asset_kind=target.asset,
        add_table=on_a_game and not target.table,
        replace_table=target.table if on_a_game else "",
        table_id=target.table, page=target.page)


@on_page
async def _which_game(library: Library, drop: Any, analysis: dict[str, Any], *,
                      added: bool = False) -> drop_target.Target | None:
    """The game a drop with no table is for, as a drop on that game's row would name it.
    None, with the upload let go of, where there is nothing to ask about or the answer was
    to cancel."""
    from manager.library import which_game

    if added and not library.games:
        await _turned_away(library, drop, t("manager.add.no_games"))
        return None
    if not library.games or not analysis.get("assets"):
        await _turned_away(library, drop, t(
            "manager.uploads.nothing_import" if added else "manager.page.drop_hint"))
        return None
    try:
        chosen = await which_game.ask(library, analysis)
        target = await offload.io(drop_target.resolve, library, "games",
                                  drop_target.Where(row_id=str(chosen["id"]))) \
            if chosen else None
    except Exception:
        logger.exception("manager: the game a drop is for could not be asked or resolved")
        await _turned_away(library, drop, t("manager.uploads.not_work"))
        return None
    if isinstance(target, drop_target.Refusal):
        await _turned_away(library, drop, target.text())
    elif target is None:
        await run.io_bound(library.abort_upload, drop.upload_id)
    return target if isinstance(target, drop_target.Target) else None


# The hook each grid registers for putting one game's rows back, by the view it serves.
_ROW_REFRESH = {"games": "refresh_game", "tables": "refresh_game",
                "media": "refresh_files", "assets": "refresh_files"}


def _put_right(state: dict, redraw: Callable[[], None],
               game_id: str) -> Callable[[], Awaitable[None]]:
    """What follows a file dropped on one of a game's slots: the rows it changed, and the
    panel where it is open on that game, put back in place. A page rebuild sends the grid
    back to the top, so one is kept for a drop that names no game and for a view with no
    row to refresh."""
    async def put_right() -> None:
        view = str(state.get("view") or "")
        shown = state.get("panel_rebuild")
        if game_id and view in _ROW_REFRESH and state.get("game") == game_id \
                and callable(shown):
            await shown()
            return
        refresh = state.get(_ROW_REFRESH.get(view, ""))
        if game_id and callable(refresh):
            await refresh(game_id)
            return
        redraw()
    return put_right


@on_page
async def _took_a_picture(library: Library, state: dict, drop: Any,
                          target: drop_target.Target) -> None:
    """The one image a drop brought, set as the picture it landed on. Nothing else is
    read from the drop, and the session is let go whichever way it ends."""
    try:
        name, data = await offload.io(library.upload_image, drop.upload_id)
    except Exception as exc:  # noqa: BLE001 - the reason belongs on screen
        await _turned_away(library, drop, why(exc))
        return
    await run.io_bound(library.abort_upload, drop.upload_id)
    redraw: Callable[[], Awaitable[None]] | None = None
    try:
        if target.picture_of == drop_target.PLAYER:
            redraw = await players_page.dropped_picture(library, state, target.picture,
                                                        name, data)
        else:
            await offload.io(library.set_collection_image, target.picture, name, data)
    except Exception as exc:  # noqa: BLE001 - the reason belongs on screen
        ui.notify(t("said.save_failed"), caption=why(exc), type="negative")
        return
    if redraw is not None:
        await redraw()
        return
    ui.notify(t("manager.mediasource.label_saved", label=t("manager.workbench.wheel")),
              type="positive")
    if callable(reread := state.get("refresh_collections")):
        await reread(target.picture if state.get("collection") == target.picture else "")


async def _turned_away(library: Library, drop: Any, said: str) -> None:
    """Say why a drop goes nowhere, and let go of whatever it had already uploaded."""
    ui.notify(said, type="warning")
    if drop.upload_id:
        await run.io_bound(library.abort_upload, drop.upload_id)


def reads_before_drawing(view: str, library: Library) -> Callable[[], Any] | None:
    """What `view` reads off the loop before it draws, or None where it holds all of it.
    render() runs on the loop, and the client refuses an HTTP call there."""
    reads = {"games": (library.has_games_grid, library.load_games_grid),
             "overview": (library.has_overview, library.load_overview),
             "tables": (library.has_tables_view, library.load_tables_view),
             "collections": (library.has_collections, library.load_collections),
             "media": (library.has_media_rows, library.load_media_rows),
             "assets": (library.has_asset_rows, library.load_asset_rows)}
    if view not in reads:
        return None
    has, load = reads[view]
    return None if has() else load


@ui.page("/", title=t("manager.page.vpinfe_manager"), reconnect_timeout=300)
@ui.page("/manager", title=t("manager.page.vpinfe_manager"), reconnect_timeout=300)
async def manager_page(view: str = "", game: str = "", table: str = "", section: str = "",
                   slot: str = "", page: str = "", mode: str = "",
                   collection: str = "", launcher: str = "", sets: str = "",
                   player: str = "", setup: str = "", log: str = "", find: str = "",
                   backup: str = "", report: str = "", group: str = "",
                   entry: str = "", row: str = "", author: str = "", maker: str = "",
                   theme: str = "", tab: str = "", pane: str = "",
                   file: str = "") -> None:
    """The Manager. Query parameters say where in it, so a place can be linked to."""
    # 2.x's own page keys, still sitting in bookmarks and manager-ui-state.json.
    if page.strip().lower() in ("vpinplay_account", "vpinplay_player"):
        ui.navigate.to("/remote?screen=join")
        return
    # The palette and Quasar's dark mode are two separate switches. The toggle button
    # that used to own the second one is gone, so it is set here - without it the shell
    # renders light while the tokens stay dark.
    # `?mode=` looks at one without changing what the install is set to, which is how
    # two modes get compared side by side. It is never written into the address.
    chosen = appearance.mode_or_default(mode) if mode else appearance.configured_mode()
    appearance.apply_mode(chosen)
    grid.install_filters()
    panel_parts.install_fact_tooltips()
    # The shell takes the viewport once, here, and every height below it is flex. The
    # old layout gave each pane its own calc(100vh - N) against chrome that later
    # changed, so a pane collapsed the moment the header it was subtracting went away.
    ui.query(".nicegui-content").classes("p-0 gap-0 h-screen")

    ui.on(text_editor.DARK_EVENT, text_editor.wire())
    ui.on(text_editor.RETURN_EVENT, lambda: text_editor.shown_again(state))
    ui.on("hub_window_px", lambda event: on_window_width(int(event.args or 0)))
    ui.on("hub_member_moved", lambda event: wb_collection.member_moved(state, event.args))
    ui.on(row_drag.DRAGGED, lambda: row_drag.dragging(library, state))
    ui.on(row_drag.DROPPED,
          lambda event: row_drag.dropped(library, state, event.args))
    ui.on("hub_guide_moved", lambda event: game_guides.guide_moved(state, event.args))
    for source_group, moved in theme_sources.MOVED.items():
        ui.on(moved, lambda event, source_group=source_group: theme_sources.moved(
            state, source_group, event))
    ui.on(folders.MOVED, lambda event: folders.moved(state, event))
    ui.on(shelves.MOVED, lambda event: shelves.moved(state, event))
    ui.on("hub_dock_px", lambda event: core.dock_dragged(state, event.args))
    ui.on("hub_dock_w", lambda event: core.dock_widened(event.args))
    ui.on("hub_row_focus", grid.row_focused)
    ui.on("hub_row_pick", lambda: state["pick_row"]())
    ui.on("hub_stacked", lambda event: state["stacked_changed"](event.args))
    ui.on("hub_arrive", lambda event: state["arrive"](event.args))
    ui.on("hub_media_zoom", lambda event: games.media_zoomed(state, event))
    ui.on("hub_rate", lambda event: games.rated(state, event))
    ui.on(jobs_list.REPORT_ASKED, jobs_page.report_asked)
    ui.on("manager_dnd", uploads.listener(lambda drop: _took_a_drop(library, state, redraw,
                                                                     drop)))

    # The shell first, then wait for the browser to have it, and only then read
    # anything. Reading first meant a page function that took two seconds to return, and
    # nicegui abandons a response that is not ready in three - which surfaced as a
    # reload loop and a page whose handlers were never wired, not as a slow page.
    with ui.column().classes("w-full h-full items-center justify-center gap-3") as loading:
        ui.label(t("manager.page.loading_manager")).classes("text-sm opacity-60")
    busy.until_gone(loading)

    await ui.context.client.connected()
    # Asked here, while the loading screen is up: the width event further down is sent
    # with the shell in one message, so the nav would draw wide first on a narrow window.
    try:
        first_width = int(await ui.run_javascript("window.innerWidth", timeout=3.0) or 0)
    except (TimeoutError, ValueError):
        first_width = 0
    try:
        loaded = await offload.io(_read_hub)
    except Exception as exc:  # noqa: BLE001 - said on the page, the traceback logged
        logger.warning("The Manager's first read failed", exc_info=True)
        if not ui.context.client.is_deleted:
            _not_loaded(loading, exc)
        return
    if ui.context.client.is_deleted:
        # Reading the library takes long enough that somebody can close the tab or
        # reload inside it, and there is then nothing to draw on and nothing to clear.
        # Building anyway raises out of the page function and logs a stack trace for
        # somebody having changed their mind.
        return

    library: Library = loaded["library"]
    discovery = loaded["discovery"]
    devices = loaded["devices"]
    installed_extensions = loaded["extensions"]
    device_capabilities = loaded["device_capabilities"]
    local_capabilities = loaded["local_capabilities"]
    pending = bool(loaded["pending"])
    whats_new_due = bool(loaded["whats_new_due"])
    waiting_bar = waiting.Bar(library, lambda: redraw())

    def rail_for(extensions: list[dict[str, Any]]) -> Any:
        return nav_for(discovery.get("features"), community_page.nav_items(extensions))

    # The rail this install has: it decides what is drawn and which addresses resolve, and
    # those two disagreeing is a link that lands on a blank page. Drawn again by
    # `draw_rail` when an extension is switched.
    nav_groups = rail_for(installed_extensions)
    nav_items = [item for _parent, items in nav_groups for item in items
                 if not ext_links.is_link(item[0])]

    landing_view = landing_for([key for key, *_rest in nav_items],
                               str(remembered.get("section", "") or ""))
    # Set by the frontend's own window, the one place that cannot say where else Setup is.
    in_the_frontends_window = setup.strip().lower() == "window"

    state: dict[str, Any] = {"view": landing_view, "device": None, "mini": False,
                             "host": ext_links.opened_at(),
                             "workbench": True, "settings_page": "",
                             "addressed": not mode and not in_the_frontends_window,
                             "collection": None, "trouble": loaded["trouble"],
                             "devices": devices, "extensions": installed_extensions,
                             "ranked_orders": community_page.ranked_orders(
                                 installed_extensions)}
    # Before anything is built, so the first render is the place asked for rather than
    # the front door followed by a jump.
    deeplink.apply(state, {"view": view, "game": game, "table": table,
                           "section": section, "slot": slot, "page": page,
                           "collection": collection, "launcher": launcher, "sets": sets,
                           "player": player, "log": log, "find": find, "backup": backup,
                           "report": report, "group": group, "entry": entry,
                           "row": row, "author": author, "maker": maker, "theme": theme,
                           "tab": tab, "pane": pane, "file": file},
                   views=[key for key, _label, _icon, _feature in nav_items],
                   sections=[*(item.key for item in rail.SECTIONS), *discovery_kinds.vocabulary()])

    # The saved views, read before anything draws: a grid asks for them while it is
    # being built, which is on the loop, and the client refuses an HTTP call there.
    # An address that names a section has to read what that section needs, because the
    # first draw goes straight to render() and only redraw() reads on the way in. Both
    # are asked at once: neither needs the other's answer.
    if state["view"] == "media" and state.get("group") == report_groups.UNSUPPORTED:
        await run.io_bound(media_page.arrive_on, library, media_page.UNSUPPORTED_VIEW)
    reads = reads_before_drawing(state["view"], library)
    await asyncio.gather(
        run.io_bound(library.warm, games.SCOPE + views.VIEWS_SUFFIX,
                     f"{games.SCOPE}.tables" + views.VIEWS_SUFFIX),
        *([run.io_bound(reads)] if reads is not None else []))
    # So a report's links, from a tab of its own, can land here.
    ui.run_javascript(f"window.name = {json.dumps(report_groups.MANAGER_WINDOW)}")
    reported_job, reported_group = state.pop("report", ""), state.pop("group", "")
    if reported_job and state["view"] == "tables":
        state["reported"] = await _group_of_report(library, reported_job, reported_group)
    if ui.context.client.is_deleted:
        return

    # Anything that hides with the rail, which is a label on most rows and a caret,
    # an image or a spinner's line on the rest.
    labels: list[ui.element] = []
    destinations: dict[str, ui.row] = {}
    # Deliberately not in `labels`: a badge has to survive the collapse to the icon
    # rail, because once the label has gone it is the only thing left that can say an
    # entry wants attention.
    badges: dict[str, ui.label] = {}

    def set_mini(mini: bool) -> None:
        """Collapse the nav to an icon rail rather than hiding it.

        Hiding it entirely costs you the map; mini keeps every destination reachable
        and one click away, which is the point of a rail.
        """
        state["mini"] = mini
        left.props(add="mini") if mini else left.props(remove="mini")
        for label in labels:
            label.set_visibility(not mini)
        nav_icon.props(f'name={"menu" if mini else "menu_open"}')
        # Centring the rail's icons is the stylesheet's job, under .q-drawer--mini:
        # the padding that offsets them is !important, which no class here outranks.

    def on_window_width(width: int) -> None:
        if state.get("nav_collapsed_by_full"):
            return
        wants_rail = width < NAV_NARROW_PX
        if wants_rail != state["mini"] and wants_rail != state.get("nav_by_hand"):
            set_mini(wants_rail)

    def toggle_mini() -> None:
        """The nav's own control. Setting it by hand takes it out of Full's care: an
        explicit choice outranks the one Full made on your behalf, so leaving Full
        will not undo it."""
        state.pop("nav_collapsed_by_full", None)
        # Remembered, so a width change does not undo what was just asked for by hand.
        state["nav_by_hand"] = not state["mini"]
        set_mini(not state["mini"])


    power_menu = power.Power(library, waiting_bar, state, devices,
                             str(discovery.get("install_id") or ""),
                             signed_in=bool(security.this_session_id()))
    with ui.left_drawer(value=True).props(
            f"width={NAV_WIDE_PX} breakpoint={NAV_OVERLAY_PX} bordered") as left:
        # A nav header row carrying the collapse control (`manager-nav-header`).
        # It is the panel's own chrome, so it costs nothing that
        # was not already the panel, and nothing floats over the grid.
        nav_header = ui.row() \
            .classes("items-center gap-3 w-full manager-nav-header")
        with nav_header:
            # The icon is the control, not the bar it sits in. A whole clickable header
            # collapses the panel when someone meant to click the name in it, and hangs
            # the tooltip off the middle of a wide row where it points at nothing.
            nav_icon = ui.icon("menu_open", size="24px") \
                .classes("opacity-70 shrink-0 cursor-pointer manager-panel-toggle") \
                .on("click", lambda: toggle_mini())
            nav_icon.tooltip(t("manager.page.nav_toggle"))
            # Larger and heavier than a nav item, like HA's own title. Row height may
            # differ from the items below - that is fine and expected; what has to stay
            # aligned is the icon column, which does not depend on the label's size.
            labels.append(ui.label(t("manager.page.vpinfe_manager"))
                          .classes("whitespace-nowrap manager-nav-title"))
        # The destinations scroll; the header and the foot do not. Without this the
        # drawer is one scroll box, so the title and the version scrolled away with the
        # rows - which is the two pieces of chrome that should always be reachable.
        nav_body = ui.column().classes("w-full gap-0 manager-nav-body")
        rail_labels: list[ui.element] = []
        def draw_rail(groups: list) -> None:
            nav_body.clear()
            destinations.clear()
            badges.clear()
            for old in rail_labels:
                labels.remove(old)
            rail_labels.clear()
            for parent, items in groups:
                held: list[ui.row] = []
                with nav_body:
                    if parent is not None:
                        _nav_parent(parent, state, rail_labels, held, badges)
                    for key, label, icon, _feature in items:
                        if ext_links.is_link(key):
                            _nav_link(key, label, icon, state, rail_labels,
                                      nested=parent is not None, held=held)
                            continue
                        # redraw, not render: a destination whose rows are read on
                        # demand has to read them before it draws, and arriving is when
                        # that is first true.
                        _nav_item(key, label, icon, state, lambda: redraw(), rail_labels,
                                  destinations, badges,
                                  nested=parent is not None, held=held,
                                  owner=_owner_of(key, state["extensions"]))
                        if key == "collections":
                            row_drag.rail(state)
                    if parent is not None:
                        # After the children exist: the caret leads `held`, and a group
                        # left closed last time has to draw closed rather than open and
                        # blink.
                        _show_group(state[f"{parent[0]}_open"], held[0], held)
            labels.extend(rail_labels)
            for label in rail_labels:
                label.set_visibility(not state["mini"])

        draw_rail(nav_groups)
        foot = ui.column().classes("items-center gap-1 px-3 py-3 w-full") \
            .style("border-top:1px solid rgba(255,255,255,0.10)")
        with foot:
            # Large enough to actually read the mark. It joins `labels` so it hides with
            # the rail - an 820px square badge has nothing legible left at 57px, so
            # scaling it down there would be worse than dropping it.
            labels.append(ui.image(appearance.LOGO).classes("w-28 h-28 mx-auto"))
        with foot:
            # Where somebody finds the phone surface, which is the one thing in this
            # drawer that is about leaving it.
            remote.invite(labels)
        with foot:
            sign_in_notice.watch()
        with foot:
            # What the install is doing, when it is doing anything. Silent otherwise:
            # a line that reads "No active jobs" spends a permanent slot to report
            # nothing, and the one it replaced said that even while a scan ran.
            job_line = ui.column().classes("items-center gap-1 w-full manager-job")
            with job_line:
                # Only this row opens the jobs: Stop sits beside it, and a click on Stop
                # is not a request to go anywhere.
                with ui.row().classes("items-center justify-center gap-2 w-full no-wrap "
                                      "cursor-pointer").on("click", lambda: _show_jobs()):
                    ui.spinner(size="16px").classes("shrink-0")
                    job_text = ui.label("").classes("text-xs min-w-0 truncate")
                    # Its own label, because the first job's line truncates and would
                    # take this with it.
                    job_more = ui.label("").classes("text-xs shrink-0 opacity-70")
                    with job_more:
                        job_others = ui.tooltip("").classes("whitespace-pre-line")
                job_stop = ui.row().classes("items-center justify-center no-wrap")
            job_line.set_visibility(False)
            labels.append(job_text)
            labels.append(job_more)
            labels.append(job_stop)
            run_line = ui.column().classes("items-center gap-1 w-full manager-job")
            with run_line:
                with ui.row().classes("items-center justify-center gap-2 w-full no-wrap"):
                    ui.icon("pause_circle", size="16px").classes("shrink-0")
                    run_text = ui.element("div").classes("flex min-w-0")
                run_acts = ui.row().classes("items-center justify-center gap-1 no-wrap")
            run_line.set_visibility(False)
            labels.append(run_text)
            labels.append(run_acts)
            failed_line = ui.row().classes("items-center justify-center gap-2 w-full "
                                           "no-wrap manager-job cursor-pointer")
            with failed_line:
                ui.icon("error", size="16px").classes("shrink-0 text-negative")
                failed_text = ui.element("div").classes("flex min-w-0")
            failed_line.set_visibility(False)
            failed_line.on("click", lambda: _show_jobs())
            labels.append(failed_text)
            with ui.row().classes("gap-2 items-center justify-center w-full no-wrap"):
                # Which build this is. Identity rather than state, so it is always
                # true and always shown. Whether a newer one exists is a device's
                # business and is said on the Devices entry, which is one place for it
                # rather than two that can disagree.
                labels.append(ui.label(_version(discovery.get("vpinfe_version")))
                              .classes("text-xs opacity-70"))
                power_menu.place()

    opened = time.time()
    # Entry key to the warm mark it draws, beside the red ones the feature checks make.
    warm: dict[str, rail_marks.Mark] = {}
    # A watch started by `watch_jobs` runs as a task of its own, outside the page.
    here = ui.context.client
    hold_library(library)
    told: set[str] = set()
    shown_failed: dict[str, Any] = {"id": "", "said": None}
    dismissed: set[str] = set()

    def _dismiss_failed() -> None:
        dismissed.add(shown_failed["id"])
        failed_line.set_visibility(False)

    @on_page
    async def _show_jobs() -> None:
        if not await may_leave(state, "jobs"):
            return
        go("jobs")

    def _jobs_opened() -> None:
        """Opening Jobs by any route reads what the badge and the failure line were about."""
        remembered.put(JOBS_SEEN, time.time())
        _dismiss_failed()
        _warm_jobs([])

    offered: dict[str, Any] = {"stop": "", "run": None}

    def records() -> bool:
        return any(one.get("name") == "capture"
                   for one in discovery.get("capabilities") or [])

    def _offer_stop(job: dict[str, Any]) -> None:
        """Stop beside the job that is running, where it says it can be stopped."""
        link = str((job.get("links") or {}).get("stop") or "") if job.get("stoppable") \
            else ""
        if link == offered["stop"]:
            return
        offered["stop"] = link
        job_stop.clear()
        if link:
            with job_stop:
                panel_parts.action(t("manager.page.stop"),
                                   partial(_stop, link, str(job.get("kind") or "")),
                                   icon=verbs.STOP, inline=True)()

    @on_page
    async def _stop(link: str, kind: str) -> None:
        asks = {KIND_MEDIA_CAPTURE: ("manager.page.stop_recording",
                                     "manager.page.stop_recording_detail"),
                KIND_IMPORT: ("manager.page.stop_import",
                              "manager.page.stop_import_detail")}.get(kind)
        if asks and not await confirm.ask(
                t(asks[0]), detail=t(asks[1]),
                confirm=t("manager.page.stop"), icon=verbs.STOP):
            return
        try:
            await offload.io(ApiClient().stop_job, link)
        except Exception as exc:  # noqa: BLE001 - the reason belongs on screen
            ui.notify(t("manager.page.could_not_stop"), caption=why(exc), type="negative")
            return
        watch_jobs()

    async def _watch_run() -> None:
        """A recording run left waiting, with Resume and Discard."""
        found: dict[str, Any] = {}
        if records():
            try:
                found = await offload.io(ApiClient().capture_run)
            except Exception:  # noqa: BLE001 - the line says nothing it cannot read
                found = {}
        said = recorder.waiting(found)
        run_line.set_visibility(bool(said[0]))
        if said == offered["run"]:
            return
        offered["run"] = said
        run_text.clear()
        run_acts.clear()
        if not said[0]:
            return
        with run_text:
            panel_parts.line(said[0], hint=said[1], classes="text-xs min-w-0 truncate")
        with run_acts:
            panel_parts.action(t("manager.page.resume"), _resume, icon=verbs.RUN,
                               inline=True)()
            panel_parts.action(t("manager.record.discard"), _discard, icon=verbs.DISCARD,
                               inline=True)()

    @on_page
    async def _resume() -> None:
        try:
            job = await offload.io(ApiClient().resume_capture)
        except Exception as exc:  # noqa: BLE001 - the reason belongs on screen
            ui.notify(t("manager.page.could_not_resume"), caption=why(exc), type="warning")
            return
        watch_jobs()
        await recorder.follow_run(library, str(job.get("id") or ""), state, lambda: None)

    @on_page
    async def _discard() -> None:
        try:
            await offload.io(ApiClient().discard_capture)
        except Exception as exc:  # noqa: BLE001 - the reason belongs on screen
            ui.notify(t("manager.page.could_not_discard"), caption=why(exc),
                      type="negative")
            return
        watch_jobs()

    @on_page
    async def _watch_jobs() -> None:
        """Say what is running, or what failed since the page opened, wherever it was
        started from."""
        try:
            found = await offload.io(ApiClient().jobs)
        except Exception:
            job_line.set_visibility(False)
            return
        running = [job for job in found if job.get("state") == "running"]
        ended = reads_ended_since(found, opened, told)
        told.update(ended)
        for job_id in ended:
            background_tasks.create(_tell_read(job_id), name="manager-tell-read")
        for job in imports_ended_since(found, opened, told):
            told.add(str(job.get("id") or ""))
            await run.io_bound(library.refresh_after_import)
            await update_count.recount()
            if not wizard.watching(str(job.get("id") or "")):
                _redraw_if_idle()
                _tell_import(job)
        for job_id in orders_unset_since(found, opened, told):
            told.add(job_id)
            background_tasks.create(_tell_order(job_id), name="manager-tell-order")
        if compared := comparisons_ended_since(found, opened, told):
            told.update(compared)
            await update_count.recount()
        failed = None if running else failed_since(found, opened, dismissed)
        if failed is not None and state.get("view") == "jobs":
            dismissed.add(str(failed.get("id") or ""))
            failed = None
        seen = _jobs_seen()
        if state.get("view") == "jobs" and jobs_list.unseen(found, seen):
            seen = time.time()
            remembered.put(JOBS_SEEN, seen)
        _warm_jobs(jobs_list.unseen(found, seen))
        job_timer.interval = JOBS_RUNNING_S if running else JOBS_IDLE_S
        job_line.set_visibility(bool(running))
        failed_line.set_visibility(failed is not None)
        if failed is not None:
            failure = ((str(failed.get("message") or "").strip() or t("manager.page.failed")),
                       str(failed.get("error") or ""))
            if failure != shown_failed["said"]:
                shown_failed["said"] = failure
                failed_text.clear()
                with failed_text:
                    panel_parts.line(failure[0], hint=failure[1],
                                     classes="text-xs min-w-0 truncate")
            shown_failed["id"] = str(failed.get("id") or "")
        if not running:
            _offer_stop({})
            await _watch_run()
            return
        run_line.set_visibility(False)
        job, *others = running
        _offer_stop(job)
        said = _job_said(job)
        pct = int(job.get("pct") or 0)
        job_text.text = t("manager.page.job_progress", message=said, percent=pct) \
            if pct else said
        job_more.text = t("manager.page.more_running", count=len(others)) if others else ""
        job_more.set_visibility(bool(others) and not state.get("mini"))
        job_others.text = "\n".join(_job_said(one) for one in others)

    def _jobs_seen() -> float:
        """When this browser last opened Jobs. A browser with nothing stored stores the
        time this page opened, so a first visit does not count the whole history."""
        seen = remembered.get(JOBS_SEEN)
        if isinstance(seen, (int, float)):
            return float(seen)
        remembered.put(JOBS_SEEN, opened)
        return opened

    def _warm_jobs(news: list[dict[str, Any]]) -> None:
        mark = rail_marks.Mark(len(news), jobs_list.unseen_said(news)) if news else None
        if warm.get("jobs") == mark:
            return
        if mark is None:
            warm.pop("jobs")
        else:
            warm["jobs"] = mark
        mark_findings()

    def _job_said(job: dict[str, Any]) -> str:
        return str(job.get("message") or "").strip() or t("manager.page.working")

    def _tell_import(job: dict[str, Any]) -> None:
        """An import this page is not watching has ended. It says how it went and opens the
        report, which is what an import that stopped, or skipped games, is read from."""
        problems = int(job.get("problems") or 0)
        if job.get("stopped"):
            said = t("manager.page.import_stopped")
        elif problems:
            said = t("manager.page.import_problems", count=problems)
        else:
            said = t("manager.page.import_done")
        warn = bool(problems or job.get("stopped"))
        if not job.get("report"):
            ui.notify(said, type="warning" if warn else "positive")
            return
        undo.act(said, t("manager.page.view_report"),
                 partial(job_report.open_for, str(job.get("id") or "")), warn=warn)

    def _redraw_if_idle() -> None:
        """A games or tables list already on screen shows what the library now holds, but
        only while nothing is open, ticked or searched: a redraw closes the panel and
        drops ticks and the search."""
        if list_is_idle(state):
            redraw()

    async def _tell_order(job_id: str) -> None:
        """The tables a pass over the library could not write, named."""
        try:
            found = await offload.io(ApiClient().job, job_id)
        except Exception:
            return
        with here:
            order_notice.tell((found.get("result") or {}).get("failed"))

    async def _tell_read(job_id: str) -> None:
        """A library read this page did not start, said when it found new games."""
        try:
            found = await offload.io(ApiClient().job, job_id)
        except Exception:
            return
        result = found.get("result") or {}
        if not (int(result.get("new_games") or 0) or int(result.get("waiting_matched") or 0)):
            return
        await run.io_bound(library.refresh_after_import)
        with here:
            _redraw_if_idle()
            _say_read(result)

    job_timer = ui.timer(JOBS_RUNNING_S, _watch_jobs)
    ui.timer(waiting.POLL_S, waiting_bar.refresh)
    ui.timer(0.1, waiting_bar.refresh, once=True)
    ui.timer(0.1, power_menu.load, once=True)
    ui.timer(waiting.POLL_S, power_menu.reload)

    def watch_jobs() -> None:
        """For a job this page has just started."""
        background_tasks.create(_watch_jobs(), name="manager-watch-jobs")

    state["watch_jobs"] = watch_jobs

    def _probe_one(device_id: str) -> dict:
        return devices_page.remember_answer(device_id, ApiClient().probe_device(device_id))

    def _probe_on_load(device_id: str) -> dict:
        return devices_page.recent_silence(device_id) or _probe_one(device_id)

    async def _reached(device_id: str, found: dict) -> None:
        """One device's answer, onto its row and, when it is the one open, the panel."""
        state.setdefault("device_reach", {})[device_id] = found
        landed = state.get("device_landed")
        if state.get("view") != "devices" or landed is None:
            return
        landed(device_id)
        if state.get("device_id") == device_id:
            # A probe's task has no slot of its own, so the redraw is entered on the page.
            with here:
                await show_device(None)

    async def _arrived(device_id: str, found: dict) -> None:
        """What that device says it can be asked to do, for its menus, then `_reached`.

        Actions first: `_reached` redraws the open panel, which reads them."""
        entry = next((one for one in devices
                      if str(one.get("device_id") or "") == device_id), None)
        if entry is not None:
            state.setdefault("device_actions", {})[device_id] = \
                await devices_page.load_actions(library, entry, discovery.get("install_id"),
                                                found)
        await _reached(device_id, found)
        if entry is not None and device_id == power_menu.local_id:
            power_menu.refill()

    async def _look_for_update() -> None:
        """Mark each device with whether it answered, and Devices with how many of them
        have an update, once per page load.

        Off the loop and never fatal: an install with no internet is not broken, a
        device that is asleep is not a broken device, and a rail that cannot say whether
        a build is current says nothing rather than claiming it is.
        """
        local_id = discovery.get("install_id")
        entries = {str(entry.get("device_id") or ""): entry for entry in devices}
        waiting: list[str] = []

        async def reached(device_id: str, found: dict) -> None:
            await _arrived(device_id, found)
            entry = entries.get(device_id) or {}
            client = device_client.for_device(entry, local_id)
            if found.get("state") != device_client.ANSWERING or client is None:
                return
            ask = devices_page.update_checker(device_id == local_id, client)
            if ask is None:
                return
            try:
                update = await offload.io(ask)
            except Exception as exc:  # noqa: BLE001
                # Debug, because a device being off is not news. This runs on every
                # draw, so at any louder level a cabinet somebody switched off in
                # August fills the log for the rest of the year - and the Manager
                # already says which devices are unreachable, on the page about them.
                logger.debug("Could not ask %s what it is running: %s",
                             devices_page.device_label(entry), exc)
                return
            if device_id == local_id:
                # Kept so the device's own page does not ask a second time on arrival.
                state["update"] = update
            if update.get("update_available") and not update.get("error"):
                waiting.append(devices_page.device_label(entry))

        for exc in await devices_page.ask_each(devices, _probe_on_load, reached):
            logger.debug("Could not probe a device: %s", exc)

        if not waiting:
            return
        # A count, not a version: the entry stands for every device, and which build each
        # one is going to is on its own page. The tooltip names them while it still can,
        # because "3" on its own does not say which three.
        warm["devices"] = rail_marks.Mark(len(waiting), (
            t("manager.page.update_waiting", devices=", ".join(waiting))
            if len(waiting) <= 3
            else t("manager.page.updates_count", count=len(waiting))))
        mark_findings()

    ui.timer(0.1, _look_for_update, once=True)

    update_count = update_count_module.Count(warm, lambda: mark_findings())
    state["update_count"] = update_count
    state["recount_updates"] = update_count.recount
    ui.timer(0.2, update_count.recount, once=True)

    loop = asyncio.get_running_loop()

    def _tie_written() -> None:
        """A tie was written, from whichever thread wrote it."""
        loop.call_soon_threadsafe(
            lambda: background_tasks.create(update_count.recount(), name="manager-recount-ties"))

    library.after_tie = _tie_written

    @on_page
    async def _rescan() -> None:
        """Scan, and say what came of it however long it took.

        The footer line reports a scan while it runs, which on a library worth scanning
        is most of the time - but a small one finishes in a tenth of a second and no
        poll will ever catch it. So the outcome is reported here, from the job the
        refresh handed back, and the line is for watching a long one rather than for
        knowing it happened.
        """
        job = await _read_the_library()
        if job is None:
            return
        job_id = str(job.get("id") or "")
        told.add(job_id)
        watch_jobs()
        client = ApiClient()
        for _ in range(150):
            await asyncio.sleep(0.2)
            try:
                found = await offload.io(client.job, job_id)
            except Exception:
                return
            if found.get("state") == "running":
                continue
            if found.get("state") == "failed":
                lead, caption = read_failure(found)
                ui.notify(lead, caption=caption, type="negative")
            else:
                await run.io_bound(library.refresh_after_import)
                _say_read(found.get("result") or {})
                redraw()
            return
        # Still going: the rail tells it when it ends, as it would anyone else's.
        told.discard(job_id)

    def _say_read(result: dict[str, Any]) -> None:
        said, waiting, also = read_outcome(result)
        if not waiting:
            ui.notify(said, type="positive", caption=also or None, multi_line=bool(also))
        elif len(waiting) == 1:
            undo.act(said, t("manager.page.show"), partial(_open_to_match, waiting[0]),
                     caption=also)
        else:
            undo.act(said, t("manager.page.show_unmatched"), _show_unmatched, caption=also)

    @on_page
    async def _open_to_match(game_id: str) -> None:
        """The game's Game Details, whose first row is its match."""
        if not await may_leave(state, "games"):
            return
        leave_for(state, "games")
        state["section"] = "game_details"
        if not library.has_games_grid():
            await run.io_bound(library.load_games_grid)
        render()
        deeplink.sync(state, push=True)
        await show_game({"id": game_id})
        state["open_pane"]()
        deeplink.sync(state)

    async def _show_unmatched() -> None:
        if not await may_leave(state, "games"):
            return
        state["arriving"] = games.UNMATCHED
        go("games")

    async def _show_recorded() -> None:
        """Media on where each file came from, filtered to the recorded ones."""
        if not await may_leave(state, "media"):
            return
        await offload.io(media_page.arrive_on_sources, library)
        state["arriving"] = media_page.recorded()
        go("media")

    state["show_recorded"] = _show_recorded

    @on_page
    async def _open_on(opens: needs.Opens) -> None:
        """Where a row of Needs You lands: a view, with the filter its grid arrives on."""
        if opens.then == needs.RETRY:
            library.forget_watched()
            go("overview")
            return
        if opens.then == needs.REVIEW:
            try:
                waiting_on = {str(row.get("game_id") or "") for row in
                              (await offload.io(library.capture_proposals))
                              .get("proposals") or []}
            except Exception:  # noqa: BLE001 - review says it could not read them
                waiting_on = set()

            async def used() -> None:
                await run.io_bound(library.reread_media, waiting_on)

            await recorder.review(library, used)
            library.forget_watched()
            go("overview")
            return
        if opens.then == needs.FOLDERS:
            await sections.metadata_folders(
                library, state, str(opens.keys.get("folders") or ""),
                lambda: go("overview") if state.get("view") == "overview" else None)
            return
        if opens.action:
            standing = await offload.io(community_page.as_it_stands,
                                        {"name": opens.extension})
            await community_page.open_over(standing, opens.action, [])
            library.forget_watched()
            go("overview")
            return
        if not await may_leave(state, opens.view):
            return
        keys = dict(opens.keys)
        if view_key := keys.pop(games.ARRIVE_VIEW, None):
            arrive = media_page.arrive_on if opens.view == "media" else games.arrive_on
            await run.io_bound(arrive, library, str(view_key))
        state.update(keys)
        if opens.arriving is not None:
            state["arriving"] = opens.arriving
        go(opens.view)

    state["open_on"] = _open_on


    splitter = ui.splitter(reverse=True, limits=(WORKBENCH_MIN_PX, WORKBENCH_MAX_PX),
                           value=WORKBENCH_WIDE_PX) \
        .props("unit=px").classes("w-full h-full manager-pane-box")
    with splitter.after, ui.column().classes("w-full h-full gap-0 manager-workbench") as workbench:
        def declare_drop(**ids: Any) -> None:
            uploads.declare(workbench, **ids)
            workbench.update()

        state["declare_drop"] = declare_drop
        # The selected game's name shares the row with the toggle rather than sitting
        # under it - same class list, same height and same gutter as the nav's header,
        # so neither the icon's inset from the edge nor its height can drift.
        workbench_header = ui.row() \
            .classes("items-center gap-2 px-3 w-full justify-between "
                     "no-wrap manager-panel-header") \
            .style(f"min-height:{HEADER_H_PX}px")
        with workbench_header:
            # Only drawn where the panel has the window to itself, which is the way back
            # to the list.
            panel_parts.icon_only(verbs.BACK, lambda: back_from_pane(), hint=t("word.back")) \
                .props("size=sm").classes("manager-pane-back shrink-0")
            # min-w-0 lets the column shrink below its content so the title truncates
            # instead of pushing the toggle onto a second line; shrink-0 keeps the
            # toggle at its own size while that happens.
            workbench_title = _Title().classes("gap-0 min-w-0 overflow-hidden grow")
            with ui.row().classes("items-center gap-1 shrink-0 no-wrap"):
                # Their own row so they can go with the panel: at the rail there is
                # nothing to step through, and they do not fit beside a 57px header.
                workbench_actions = ui.row() \
                    .classes("items-center gap-1 shrink-0 no-wrap")
                with workbench_actions:
                    workbench_title.menu = state["subject_menu"] = ui.row() \
                        .classes("items-center shrink-0 no-wrap")
                    # Stepping the list from in here, so a sweep does not need the grid
                    # on screen - which is the point of Full.
                    ui.button(icon=verbs.COLLAPSE, on_click=lambda: _step(-1)) \
                        .props("flat dense round size=sm").tooltip(t("manager.page.previous_game"))
                    ui.button(icon=verbs.EXPAND, on_click=lambda: _step(1)) \
                        .props("flat dense round size=sm").classes("manager-pane-next") \
                        .tooltip(t("manager.page.next_game"))
                    full_icon = ui.button(icon=verbs.ENLARGE,
                                          on_click=lambda: toggle_full()) \
                        .props("flat dense round size=sm").classes("manager-pane-full") \
                        .tooltip(t("manager.page.workbench_max"))
                # Outside that row: it is the only way back once the rest have gone.
                workbench_icon = ui.icon("menu_open", size="24px") \
                    .classes("opacity-70 shrink-0 cursor-pointer manager-panel-toggle") \
                    .on("click", lambda: show_workbench(not state["workbench"]))
                workbench_icon.tooltip(t("manager.page.workbench_toggle"))
        # The scrolling belongs to the workbench's body column now, so the outline
        # beside it can stay put while that scrolls.
        panel = ui.column().classes("w-full gap-0 grow min-h-0 overflow-hidden")


    def show_workbench(shown: bool) -> None:
        """Collapse to a rail, never away.

        Hiding it outright leaves no control to bring it back. A rail keeps its own
        toggle on screen, so both panels collapse the same way and nothing overlays the
        grid.
        """
        state["workbench"] = shown
        workbench_icon.props(f'name={"menu_open" if shown else "menu"}')
        # The floor moves with the state. Held at WORKBENCH_MIN_PX the pane cannot be
        # dragged down to a useless sliver while it is open, and the rail is below that
        # floor - so collapsing has to lower it first or Quasar clamps the value back.
        splitter._props["limits"] = [RAIL_PX if not shown else WORKBENCH_MIN_PX,
                                     WORKBENCH_MAX_PX]
        splitter.update()
        # The width you chose, not the width it opens at: the constant would snap the
        # pane back to 320 every time it reopened.
        splitter.set_value(state.get("workbench_px", WORKBENCH_WIDE_PX) if shown
                           else RAIL_PX)
        panel.set_visibility(shown)
        workbench_title.set_visibility(shown)
        workbench_actions.set_visibility(shown)
        # A rail is panel the whole way down, with no window in it - the stylesheet
        # stops cutting the band to transparent, or the page grid shows through 57px.
        splitter.classes(remove="manager-rail") if shown \
            else splitter.classes(add="manager-rail")
        # justify-end, not justify-center: the header keeps its 18px right padding, and
        # centring inside a padded box put the icon 27px in. Held to the right, the
        # padding alone places it exactly where it sits when the panel is open.
        workbench_header.classes(add="justify-end", remove="justify-between") \
            if not shown else \
            workbench_header.classes(add="justify-between", remove="justify-end")


    with splitter.before:
        # 2.x's own 24px at the sides and bottom, which separates the content from the
        # two panels either side and lets the backdrop read as a backdrop. The top is
        # the header offset the other two panes use, not this one.
        content = ui.column().classes("w-full h-full gap-0 px-6 pb-6") \
            .style(f"padding-top:{HEADER_TOP_PX}px")

    def toggle_full() -> None:
        """The list never goes away - it steps back to the rail every panel here
        collapses to, and this control is what brings it forward again.

        The nav goes to its rail with it. Full means "give this the screen", and
        leaving the 220px gutter behind makes that two gestures instead of one - on a
        1100px window it is the difference between reaching the work width and not.
        Only the nav Full collapsed comes back, so a nav you had already put away
        stays away.
        """
        state["full"] = not state.get("full")
        splitter.classes(add="manager-full") if state["full"] \
            else splitter.classes(remove="manager-full")
        full_icon.props(f'icon={"close_fullscreen" if state["full"] else "open_in_full"}')
        if state["full"]:
            if not state["mini"]:
                state["nav_collapsed_by_full"] = True
                set_mini(True)
            if not state["workbench"]:
                show_workbench(True)
        elif state.pop("nav_collapsed_by_full", None):
            set_mini(False)

    state["leave_full"] = lambda: toggle_full() if state.get("full") else None

    def open_pane() -> None:
        """Show the panel for the row picked. Where the list and the panel do not fit side
        by side the stylesheet makes it the page; wide, it only opens a collapsed panel."""
        if not has_pane():
            return
        if not state["workbench"]:
            show_workbench(True)
        state["opened"] = True
        splitter.classes(add="manager-opened")

    def has_pane() -> bool:
        """Whether this page has a subject for the panel to be about."""
        listed = community_page.find(state["view"], state["extensions"])
        return state["view"] in WORKBENCH_VIEWS or bool(
            listed is not None and listed[1].get("detail"))

    def close_pane() -> None:
        """Back to the list."""
        state["opened"] = False
        state["pane_pushed"] = False
        splitter.classes(remove="manager-opened")

    def pick_row() -> None:
        """A row picked: the panel opens, and where it is the page that opening is a place
        of its own, so Back returns to the list."""
        if state.get("opened") or not state.get("stacked") or not state["addressed"] \
                or not has_pane():
            open_pane()
            return
        deeplink.sync(state)
        open_pane()
        state["pane_pushed"] = True
        deeplink.sync(state, push=True, over=True)

    def back_from_pane() -> None:
        """The arrow in the panel's header. An entry the opening added is walked back out
        of, so the stack is what it was before and Forward reopens the panel; a panel the
        page started on has no entry before it to go to."""
        if state.get("pane_pushed"):
            ui.run_javascript("history.back()")
            return
        close_pane()
        deeplink.sync(state)

    def stacked_changed(narrow: Any) -> None:
        """The list and the panel now fit side by side, or no longer do. The address says
        which half shows only where they are not both."""
        was = deeplink.showing(state)
        state["stacked"] = bool(narrow)
        if deeplink.showing(state) != was and state["addressed"]:
            deeplink.sync(state)

    state["open_pane"] = open_pane
    state["close_pane"] = close_pane
    state["pick_row"] = pick_row
    state["stacked_changed"] = stacked_changed

    def _step(delta: int) -> None:
        """Move the focused row, which is what the workbench follows. Sent as the key
        the grid already handles rather than reaching into its API, so the buttons and
        the keyboard cannot drift apart. With the list hidden behind the panel the grid
        has no focused cell to send it to, so the row the panel is about is stepped
        from by index."""
        key = "ArrowDown" if delta > 0 else "ArrowUp"
        ui.run_javascript(
            "(() => { const c = document.querySelector('.ag-body-viewport .ag-cell-focus');"
            " if (c) { c.focus(); c.dispatchEvent(new KeyboardEvent('keydown', "
            "{key: '" + key + "', bubbles: true})); return; }"
            " const el = document.querySelector('.nicegui-aggrid'); if (!el) return;"
            " const api = getElement(Number(el.id.slice(1))).api;"
            " const ctx = api.getGridOption('context') || {};"
            " const at = ctx.focusId ? api.getRowNode(ctx.focusId) : null;"
            " if (!at || at.rowIndex == null) return;"
            f" const next = at.rowIndex + {delta};"
            " if (next < 0 || next >= api.getDisplayedRowCount()) return;"
            " api.ensureIndexVisible(next);"
            " api.setFocusedCell(next, ctx.focusCol); })()")

    def remember_width(event: Any) -> None:
        """Keep the width a drag settled on. Quasar only reports it on release, which
        is exactly when it is worth keeping."""
        try:
            value = float(event.value or 0)
        except (TypeError, ValueError):
            return
        if value > RAIL_PX:
            state["workbench_px"] = value

    splitter.on_value_change(remember_width)

    @on_page
    async def show_game(row: dict | None) -> None:
        """What the grid has selected is what the workbench is about.

        Both grids land here, and which one you are on decides what a row means: under
        Games a row is a folder, under Tables it is one file inside one. There is no
        control to keep in step - the place you are in is the answer, which is what
        making a subject a rail entry rather than a dropdown bought.
        """
        by_table = state["view"] == "tables"
        state["game"] = (row or {}).get("game_id" if by_table else "id")
        state["table"] = (row or {}).get("id") if by_table else ""
        await builds.build(panel, workbench_title, library, state["game"], state,
                           state["table"])
        deeplink.sync(state)

    @on_page
    async def show_selection(table_ids: list[str]) -> None:
        """The tables ticked in the grid. The pane is not opened for them: a panel
        somebody shut stays shut."""
        await builds.build_selection(panel, workbench_title, library, table_ids, state)

    @on_page
    async def show_game_selection(game_ids: list[str]) -> None:
        await builds.build_game_selection(panel, workbench_title, library, game_ids, state)

    @on_page
    async def show_slot(row: dict | None) -> None:
        """The address keeps the game, the table and the kind, which is all a link to a
        file carries; the panel finds the row again from those."""
        state["game"] = (row or {}).get("game_id") or ""
        state["table"] = (row or {}).get("table") or ""
        if row:
            picked = state.setdefault("slot", {"kind": None})
            picked["kind"] = row.get("kind")
            picked["file"] = (row.get("path") if file_acts.is_loose(row)
                              or state["view"] == "assets" else None) or None
        await builds.build_file(panel, workbench_title, library, row, state,
                                state["view"])
        deeplink.sync(state)

    @on_page
    async def show_device(row: dict | None) -> None:
        """What the grid has selected is what the workbench is about - the same rule
        every other subject follows, so the panel needs no control of its own.

        A row of nothing is the grid settling, not a deselection: it fires a focus event
        with no row while it restores its own state, and taking that literally empties
        the panel a moment after the page opened one. There is no way to select no
        device, so the last one stands.
        """
        wanted = (row or {}).get("id") or state.get("device_id")
        state["device_id"] = wanted
        chosen = next((d for d in devices
                       if str(d.get("device_id")) == wanted), None)
        await builds.build_device(panel, workbench_title, library, chosen, state,
                                  discovery.get("install_id"), device_capabilities,
                                  local_capabilities)
        deeplink.sync(state)

    async def show_theme(row: dict | None) -> None:
        """The panel follows the focused row, as it does for every subject. A row of
        nothing is the grid settling, and the last theme stands."""
        state["theme_key"] = (row or {}).get("id") or state.get("theme_key")
        await builds.build_theme(panel, workbench_title, library, state["theme_key"],
                                 state)

    @on_page
    async def _ask(entries: list[dict]) -> None:
        failed = await devices_page.ask_each(entries, _probe_one, _arrived)
        if failed:
            ui.notify(t("manager.page.devices_failed"), caption=why(failed[0]),
                      type="negative")

    @on_page
    async def _probe_devices() -> None:
        """Ask every device again, on demand. The page's own pass runs once on load;
        this is for when you have just gone and switched one on."""
        await _ask(devices)

    @on_page
    async def _probe_device(device_id: str) -> None:
        """Ask one device again, for the rail that has nothing to show until it answers."""
        await _ask([one for one in devices if str(one.get("device_id") or "") == device_id])

    state["ask_device"] = _probe_device

    @on_page
    async def show_collection(row: dict | None) -> None:
        """What the grid has selected is what the workbench is about - the same rule
        Games follows, so the panel never needs a control of its own."""
        chosen = (row or {}).get("id")
        # The whole library is no collection: the Games page and the address must not
        # be told to filter by it.
        state["collection"] = None if chosen == BUILTIN_ALL else chosen
        if chosen == BUILTIN_ALL:
            await builds.build_all_games(panel, workbench_title, library, state)
        else:
            await builds.build_collection(panel, workbench_title, library,
                                          state["collection"], state)
        deeplink.sync(state)

    async def show_tag(row: dict | None) -> None:
        state["tag"] = (row or {}).get("id") or None
        await builds.build_tag(panel, workbench_title, library, state["tag"], state)

    @on_page
    async def show_launcher(row: dict | None) -> None:
        """What the grid has selected is what the workbench is about, the same rule
        every other subject follows."""
        state["launcher"] = (row or {}).get("id")
        await builds.build_launcher(panel, workbench_title, library,
                                    state["launcher"], state)
        deeplink.sync(state)

    @on_page
    async def show_player(row: dict | None) -> None:
        """What the grid has selected is what the workbench is about, the same rule
        every other subject follows."""
        state["player"] = (row or {}).get("id")
        await builds.build_player(panel, workbench_title, library, state["player"],
                                  state)
        deeplink.sync(state)

    @on_page
    async def show_discovery(row: dict | None) -> None:
        """What the grid has selected is what the workbench is about, the same rule
        every other subject follows."""
        state["entry"] = (row or {}).get("id")
        await builds.build_discovery(panel, workbench_title, library, state["entry"], state)
        deeplink.sync(state)

    @on_page
    async def show_backup(row: dict | None) -> None:
        """What the grid has selected is what the workbench is about, the same rule
        every other subject follows."""
        state["backup"] = (row or {}).get("id")
        await builds.build_backup(panel, workbench_title, library, state["backup"], state)
        deeplink.sync(state)

    @on_page
    async def show_extension_row(extension: dict, declared: dict, rows: list[dict],
                                 again: Any) -> None:
        """What an extension's list has open is what the workbench is about, the same rule
        every other subject follows."""
        state["ext_row"] = str(rows[0]["id"]) if len(rows) == 1 else ""
        await builds.build_extension_row(panel, workbench_title, library, extension,
                                         declared, [str(one["id"]) for one in rows],
                                         state, again)
        deeplink.sync(state)

    def clear_workbench() -> None:
        """Empty the pane. Sync, so render() can call it without awaiting a rebuild."""
        workbench_title.clear()
        panel.clear()
        state["panel_rebuild"] = None
        if (declare_drop := state.get("declare_drop")) is not None:
            declare_drop()
        # The same two-line shape a selected game gets, so the header does not change
        # height or alignment as the selection comes and goes.
        listed = community_page.find(state["view"], state["extensions"])
        if listed is not None and listed[1].get("detail"):
            heading, prompt = str(listed[1].get("title") or ""), t("manager.community.pane_select")
        else:
            heading, prompt = EMPTY_PANE.get(
                state["view"],
                ("manager.page.game", "manager.page.select_game"))
            heading, prompt = t(heading), t(prompt)
        with workbench_title:
            ui.label(heading) \
                .classes("text-base manager-workbench-title leading-tight truncate")
            ui.label(prompt).classes("text-xs manager-workbench-label leading-none truncate")

    def place_name() -> str:
        """What the page calls the place it is on: its heading, and the browser's name for
        the history entry."""
        view = state["view"]
        listed = community_page.find(view, state["extensions"])
        if listed is not None:
            return str(listed[1].get("title") or "")
        paged = ext_pages.find(view, state["extensions"])
        if paged is not None:
            return str(paged[1].get("title") or "")
        return t(SECTIONS.get(view, view.title()))

    state["place_title"] = lambda: t("manager.page.place_title", place=place_name())

    def page_header() -> ui.row:
        """The page's name, and one line saying what it is for. Answers the slot at the
        end of the name's row, where a page whose subject is the page draws its menu.

        Two controls belong to neither the page nor a row: a library rescan, which is about
        the library and so shows in the library's own toolbar, and a jobs readout, which
        is about the whole install and lives in the rail's foot. On Settings and Devices
        neither would mean anything.

        Here rather than in an app header because each pane already owns its chrome.
        """
        title = place_name()
        purpose = t(f"manager.purpose.{state['view']}")
        listed = community_page.find(state["view"], state["extensions"])
        if listed is not None:
            purpose = t("manager.purpose.community.held" if listed[1].get("relation")
                        else "manager.purpose.community",
                        name=str(listed[0].get("display_name") or listed[0].get("name")))
        paged = ext_pages.find(state["view"], state["extensions"])
        if paged is not None:
            purpose = t("manager.purpose.page",
                        name=str(paged[0].get("display_name") or paged[0].get("name")))
        # The band the other two panes' headers use, so the page name sits in a fixed
        # rhythm rather than at whatever height its text makes. Not aligned *across*
        # panes - the nav's band is taller than its minimum and starts inside its own
        # padding, and matching that would be a magic number against an accident.
        # The title carries the buttons' 32px line height so the band centers one
        # height: centring boxes of different heights aligns boxes, not baselines.
        with ui.column().classes("w-full gap-0 min-w-0") \
                .style(f"min-height:{HEADER_H_PX}px"):
            waiting_bar.place()
            if pending:
                setup_line(window=in_the_frontends_window)
            with ui.row().classes("items-center gap-2 w-full no-wrap"):
                ui.label(title).classes("grow min-w-0 truncate manager-page-title")
                menu = ui.row().classes("items-center shrink-0 no-wrap")
            # A key with no entry draws as itself, so a page added without one says
            # nothing rather than printing `manager.purpose.<view>`.
            if purpose and not purpose.startswith("manager.purpose."):
                ui.label(purpose).classes("manager-page-purpose truncate")
        return menu

    def light_nav() -> None:
        """The page you are on stays lit while you are on it."""
        for key, row in destinations.items():
            row.classes(add="manager-nav-active") if key == state["view"] \
                else row.classes(remove="manager-nav-active")

    def render() -> None:
        # A game shown beside a different destination is stale by definition.
        clear_workbench()
        # The pane is not closed with it. It has an empty state, and `clear_workbench`
        # names it for whichever page you have arrived at. Its width and whether it is
        # open are how the workspace is arranged, and rearranging it on every move
        # between sections is the shell taking a decision back.
        #
        # On a page with no subject it goes entirely, rail included. Nothing there can
        # fill it, so the strip would be a control that reopens an empty panel.
        splitter.classes(remove="manager-no-pane") if has_pane() \
            else splitter.classes(add="manager-no-pane")
        light_nav()
        uploads.drops_on(state["view"], content.client)
        if state["view"] == "jobs":
            _jobs_opened()
        content.clear()
        with content:
            menu = page_header()
            view = state["view"]
            if view == "overview":
                sections.overview(library, devices, discovery, go, state)
            elif view == "games":
                games.build(library.game_rows(), library.kinds_present(), library,
                            show_game, state, redraw, rescan=_rescan,
                            on_selection=show_game_selection)
            elif view == "tables":
                games.build_tables(library.table_rows(), library, show_game, state,
                                   redraw, rescan=_rescan, on_selection=show_selection)
            elif view == "tags":
                tageditor.build(library.tag_rows(), library, show_tag, state, redraw)
            elif view == "collections":
                collections_page.build(library.collections(), library, show_collection,
                                       state, redraw)
            elif view.startswith(community_page.PREFIX):
                listed = community_page.find(view, state["extensions"])
                if listed is not None:
                    community_page.build(*listed, library, subject=menu,
                                         on_open=show_extension_row)
            elif ext_pages.is_page(view):
                paged = ext_pages.find(view, state["extensions"])
                if paged is not None:
                    ext_pages.build(*paged, library, subject=menu, host=state["host"])
            elif view == "media":
                media_page.build(library.media_rows(), library, show_slot, state,
                                 rescan=_rescan)
            elif view == "assets":
                assets_page.build(library.asset_rows(), library, show_slot, state,
                                  rescan=_rescan)
            elif view == "devices":
                devices_page.build(devices, library, state, show_device,
                                   probe=_probe_devices,
                                   local_device_id=discovery.get("install_id"),
                                   rerender=redraw)
            elif view == "settings":
                settings_page.build_system(library, state, redraw, discovery)
            elif view == "launchers":
                # Copying to other machines is offered only where this install manages
                # them, and it needs to know which row is itself.
                state["can_manage_devices"] = (
                    install_identity.DEVICES in (discovery.get("features") or []))
                state["install_id"] = discovery.get("install_id") or ""
                state["rerender"] = redraw
                launchers_page.build(library, state, show_launcher, redraw)
            elif view == "themes":
                themes_page.build(library, state, show_theme, redraw)
            elif view == "players":
                state["rerender"] = redraw
                players_page.build(library, state, show_player, redraw)
            elif view == "metrics":
                metrics_page.build(library, state, redraw)
            elif view == "logs":
                logs_page.build(library, state, redraw)
            elif view == "jobs":
                jobs_page.build(library, state, redraw)
            elif view == "backups":
                state["rerender"] = redraw
                backups_page.build(library, state, show_backup, redraw)
            elif view == "discovery":
                discovery_page.build(library, state, show_discovery, redraw)
            elif view == "about":
                about_page.build(library, state, redraw)

    def mark_findings() -> None:  # timers made earlier call it: no page-level await above
        """What the enabled features are missing, and what is waiting, on the entry whose
        page deals with it and on the group holding that entry, because a mark only on
        the child is a mark nobody sees on a rail they have collapsed."""
        items = state.get("trouble") or []
        for parent, entries in nav_groups:
            marks = []
            for key, *_rest in entries:
                mark = rail_marks.entry_mark([one for one in items if one.where == key],
                                             warm.get(key))
                _draw_mark(badges.get(key), mark)
                marks.append(mark)
            if parent is not None:
                _draw_mark(badges.get(parent[0]), rail_marks.group_mark(marks))

    async def recheck_trouble() -> None:
        """For a write outside Settings that can mend or break a feature."""
        state["trouble"] = await offload.io(settings_page.local_trouble)
        mark_findings()

    state["recheck_trouble"] = recheck_trouble

    def _draw_mark(badge: Any, mark: rail_marks.Mark | None) -> None:
        if badge is None:
            return
        badge.set_visibility(mark is not None)
        badge.classes(add="manager-nav-badge--error") if mark and mark.trouble \
            else badge.classes(remove="manager-nav-badge--error")
        if mark is None:
            return
        badge.text = str(mark.count)
        # Inside the badge: this also runs from a background read, which has no page
        # context to create the tooltip in.
        with badge:
            badge.clear()
            badge.tooltip(mark.tooltip)

    def links_now() -> list[list[dict[str, str]]]:
        return [ext_links.drawn(one, state["host"]) for one in state["extensions"]]

    nav_links = links_now()

    def refresh_rail() -> None:
        """The rail and the collection orders, from the extensions as they now stand."""
        nonlocal nav_groups, nav_links
        state["ranked_orders"] = community_page.ranked_orders(state["extensions"])
        groups = rail_for(state["extensions"])
        links = links_now()
        if groups != nav_groups or links != nav_links:
            draw_rail(groups)
            nav_groups, nav_links = groups, links
            light_nav()
            mark_findings()

    async def refresh_shell() -> None:
        """For a write that decides what this install is: the rail, the marks and the device
        rows follow it where they stand. The objects are updated, not replaced, because the
        pages built from them hold these ones."""
        turn = state["shell_turn"] = state.get("shell_turn", 0) + 1
        try:
            found = await offload.io(_read_shell)
        except Exception as exc:  # noqa: BLE001 - the write was saved; the shell reads again on the next load
            logger.warning("Could not read the install again after a change: %s", why(exc))
            return
        if turn != state["shell_turn"]:
            # A later switch has asked again, and its answer includes this one.
            return
        discovery.clear()
        discovery.update(found["discovery"])
        device_capabilities[:] = found["device_capabilities"]
        local_capabilities.clear()
        local_capabilities.update(found["local_capabilities"])
        refresh_rail()
        await recheck_trouble()

    state["refresh_shell"] = refresh_shell

    async def read_extensions() -> None:
        """The installed extensions as they now stand, with the rail and the marks that
        follow them, for a page that redraws its own part and not the whole view."""
        state["extensions"] = await offload.io(ApiClient().extensions)
        refresh_rail()
        await recheck_trouble()

    state["read_extensions"] = read_extensions

    def redraw() -> None:
        read_then_render(state, library, content, render, mark_findings, light_nav,
                         refresh_rail)

    state["redraw"] = redraw
    state["took_a_drop"] = lambda drop: _took_a_drop(library, state, redraw, drop, added=True)

    uploads.install()
    row_drag.install()

    ui.run_javascript(panel_parts.NAV_CLICK)
    ui.run_javascript(text_editor.ASK_BEFORE_UNLOAD)
    ui.run_javascript(text_editor.LISTEN_FOR_RETURN)

    # Reported once on load and on every settle after a resize. Debounced, because a
    # drag fires this continuously and each one is a round trip.
    ui.run_javascript("""
    (() => {
      let t = null;
      const say = () => emitEvent('hub_window_px', window.innerWidth);
      window.addEventListener('resize', () => {
        clearTimeout(t);
        t = setTimeout(say, 150);
      });
      say();
    })()
    """)

    # Whether the list and the panel fit side by side, said once and on every change. The
    # width is the one the stylesheet's container query reads.
    ui.run_javascript("""
    (() => {
      const box = document.querySelector('.manager-pane-box');
      if (!box) return;
      let last = null;
      const say = () => {
        const now = box.getBoundingClientRect().width <= 703;
        if (now !== last) { last = now; emitEvent('hub_stacked', now); }
      };
      new ResizeObserver(say).observe(box);
      say();
    })()
    """)

    # A tooltip on the control that opened a menu sits on top of it. Watched once here
    # rather than wired per menu: menus portal into the body, so one observer sees them
    # all, and the alternative is remembering this on every menu ever added.
    ui.run_javascript("""
    (() => {
      const open = () => document.querySelector('.q-menu') !== null;
      const say = () => document.body.classList.toggle('manager-menu-open', open());
      new MutationObserver(say).observe(document.body, {childList: true});
      say();
    })()
    """)

    if state["addressed"]:
        deeplink.dialogs_follow_back()

    client = ui.context.client

    def go(view: str) -> None:
        leave_for(state, view)
        # redraw, not render: a destination whose data is read on demand has to read it
        # before it draws, and arriving is exactly when that is first true.
        redraw()
        with client:
            deeplink.sync(state, push=True)

    state["go"] = go

    @on_page
    async def land_subject() -> None:
        """Open the pane the address names, not just the section. Restored after the
        shell exists, because that is what show_game builds into - and through show_game
        rather than around it, so a link, a reload and Back all land on exactly the state
        a click would have produced."""
        # The four that select a game. Collections has a pane too and is not one of
        # them: what its address names is a collection, which lands by its own route
        # below.
        landing = (state.get("game")
                   if state["view"] in ("games", "tables", "media", "assets") else None)
        arrival = None
        if landing:
            # Shaped as the lens in play expects it, so the one handler reads it the same
            # way whether it came from a click or from the address bar. The file lenses
            # take the row shape they hand out, which carries the table rather than
            # standing in for it - a shared file has none, and the address says so too.
            if state["view"] in ("media", "assets"):
                arrival = show_slot({"game_id": landing, "table": state.get("table") or "",
                                     "kind": (state.get("slot") or {}).get("kind") or "",
                                     "path": (state.get("slot") or {}).get("file") or "",
                                     "via": (media_ownership.UNUSED
                                             if (state.get("slot") or {}).get("file") else "")})
            else:
                arrival = show_game({"game_id": landing, "id": state.get("table") or landing}
                                    if state["view"] == "tables" else {"id": landing})
        elif state["view"] == "collections" and state.get("collection"):
            arrival = show_collection({"id": state["collection"]})
        elif state["view"] == "launchers" and state.get("launcher"):
            arrival = show_launcher({"id": state["launcher"]})
        elif state["view"] == "players" and state.get("player"):
            arrival = show_player({"id": state["player"]})
        elif state["view"] == "backups" and state.get("backup"):
            arrival = show_backup({"id": state["backup"]})
        elif state["view"] == "discovery" and state.get("entry"):
            arrival = show_discovery({"id": state["entry"]})
        elif state.get("ext_row") and community_page.find(state["view"], state["extensions"]):
            listed = community_page.find(state["view"], state["extensions"])
            if listed is not None and listed[1].get("detail"):
                arrival = show_extension_row(*listed, [{"id": state["ext_row"]}], None)
        elif state["view"] == "devices":
            # Arriving at Devices lands on this device with its rail open, so reaching a
            # setting is the two clicks it was when Settings was a place of its own. It is
            # first in every view for the same reason.
            arrival = show_device({"id": state.get("device_id")
                                   or discovery.get("install_id")})
        if arrival is not None:
            await arrival
            # An address that names a subject lands on its panel. Devices is not one: it
            # arrives on this device so its rail is open, and the list is the page.
            if state["view"] != "devices" and state.pop("pane", "") != "list":
                open_pane()
                if state["addressed"]:
                    deeplink.sync(state)

    async def land_on_page() -> None:
        """`land_subject` as a task of its own, which starts with no page to draw on."""
        with client:
            await land_subject()

    arriving = asyncio.Lock()

    @on_page
    async def arrive(args: Any) -> None:
        """Follow the browser to another entry, which is Back or Forward: the page this
        address names, built the way a first load builds it. One at a time: a second
        entry reached while the first is still being built, or waiting on an answer,
        waits its turn rather than building over it."""
        async with arriving:
            counted = True

            def settle() -> None:
                nonlocal counted
                if counted:
                    counted = False
                    deeplink.settled()

            try:
                params = dict(parse_qsl(str((args or {}).get("search") or "").lstrip("?")))
                over = bool((args or {}).get("over"))
                target: dict[str, Any] = {"view": landing_view}
                deeplink.apply(
                    target, params,
                    views=[key for _parent, items in nav_groups for key, *_rest in items
                           if not ext_links.is_link(key)],
                    sections=[*(item.key for item in rail.SECTIONS),
                              *discovery_kinds.vocabulary()])
                if state.get("stacked") and deeplink.same_place(state, target):
                    if target.get("pane") == "open":
                        open_pane()
                        state["pane_pushed"] = over
                    else:
                        close_pane()
                    settle()
                    deeplink.sync(state)
                    return
                if not await may_leave(state, target["view"]):
                    # The browser has already moved: refusing puts the place the page is
                    # still on back as a new entry, and Forward is lost.
                    settle()
                    deeplink.sync(state, push=True)
                    return
                deeplink.forget(state)
                leave_for(state, target["view"])
                state.update(target)
                state["pane_pushed"] = target.get("pane") == "open" and over
                settle()
                reported_job, reported_group = state.pop("report", ""), state.pop("group", "")
                if reported_job and state["view"] == "tables":
                    state["reported"] = await _group_of_report(
                        library, reported_job, reported_group)
                await builds.build(panel, workbench_title, library, None, state)
                _land(state)
                read_then_render(state, library, content, render, mark_findings, light_nav,
                                 refresh_rail, after=land_on_page)
                deeplink.name_place(state)
            finally:
                settle()

    state["arrive"] = arrive
    ui.run_javascript(
        "window.addEventListener('popstate', () => {"
        " window.vpinArrivals = (window.vpinArrivals || 0) + 1;"
        " emitEvent('hub_arrive', {search: location.search,"
        " over: !!(history.state && history.state.vpinOver)}); })")
    if first_width:
        on_window_width(first_width)
    await builds.build(panel, workbench_title, library, None, state)
    _land(state)
    render()
    # Once the nav exists to carry it. Read on the way in with everything else, so an
    # install that is misconfigured says so on the first screen rather than on the
    # first visit to System.
    mark_findings()
    # Nothing since the reads above yields - a pane with no subject draws without
    # waiting - so the shell and this go out in one message. A wait added in between
    # shows the shell under the loading screen.
    loading.delete()
    await land_subject()
    # The place named in full, once, so Back to this entry lands where the page started
    # and not wherever a bare address resolves to by then. The frontend's own window and a
    # `mode` comparison are left as they were opened.
    if not view and not mode and not in_the_frontends_window:
        deeplink.sync(state)
    elif not in_the_frontends_window:
        deeplink.name_place(state)
    if arrives_in_setup(pending, view):
        background_tasks.create(
            setup_page.open_setup(window=in_the_frontends_window), name="manager-setup")
    elif arrives_in_whats_new(whats_new_due, view, window=in_the_frontends_window):
        background_tasks.create(whats_new_page.open_whats_new(library, state),
                                name="manager-whats-new")


def read_then_render(state: dict[str, Any], library: Library, content: ui.element,
                     render: Callable[[], None], mark_findings: Callable[[], None],
                     light: Callable[[], None],
                     refresh_rail: Callable[[], None] = lambda: None,
                     after: Callable[[], Awaitable[None]] | None = None) -> None:
    """Light the view's rail row now, then render it, first reading anything it needs,
    with `content` busy from now until it is drawn: what it shows stays up, dimmed under
    the spinner, while the read runs. `after` runs once it is drawn, under the same mark:
    render() empties the pane, so whatever opens the pane has to come after it.

    The by-file lens is a second walk of every folder, so it is read when somebody asks
    for it rather than at startup - and off the loop, because render() runs on it and the
    client refuses an HTTP call there.
    """
    light()
    if state["view"] == "settings":
        # Asked again on every draw, which is when a path may just have been fixed - and
        # off the loop, because it stats the disk and a share that has gone away is
        # exactly the case this reports.
        async def read_trouble_then_draw() -> None:
            state["trouble"] = await offload.io(settings_page.local_trouble)
            # A switch on an extension's page changes what is running, and the rail
            # follows it.
            try:
                state["extensions"] = await offload.io(ApiClient().extensions)
                refresh_rail()
            except Exception:  # noqa: BLE001
                logger.exception("Could not read the installed extensions")
            render()
            mark_findings()
            if after is not None:
                await after()
        busy.start(content, read_trouble_then_draw)
        return
    if state["view"] == "devices":
        async def read_devices_then_draw() -> None:
            state["devices"][:] = await offload.io(library.devices)
            render()
            if after is not None:
                await after()
        busy.start(content, read_devices_then_draw)
        return
    reads = reads_before_drawing(state["view"], library)
    # The Won't Play judgement is made against the devices, which change under an open page.
    judged_by_devices = state["view"] == "media"
    if reads is not None or judged_by_devices:
        async def read_then_draw() -> None:
            if reads is not None:
                await run.io_bound(reads)
            if judged_by_devices:
                state["devices"][:] = await offload.io(library.devices)
            render()
            if after is not None:
                await after()
        busy.start(content, read_then_draw)
        return
    render()
    if after is not None:
        busy.start(content, after)


def _land(state: dict[str, Any]) -> None:
    """Open the grid an address shows on the row it names, the one its panel is about."""
    view = state["view"]
    game, table = str(state.get("game") or ""), str(state.get("table") or "")
    kind = str((state.get("slot") or {}).get("kind") or "")
    held = str((state.get("slot") or {}).get("file") or "")
    if view == "games" and game:
        grid.land_on(games.SCOPE, {"id": game})
    elif view == "tables" and game:
        grid.land_on(f"{games.SCOPE}.tables", {"id": table} if table else {"game_id": game})
    elif view in ("media", "assets") and game and kind:
        grid.land_on(media_page.SCOPE if view == "media" else assets_page.SCOPE,
                     *([{"game_id": game, "kind": kind, "path": held, "table": table}]
                       if held else []),
                     {"game_id": game, "kind": kind, "table": table},
                     {"game_id": game, "kind": kind, "table": ""})
    elif view == "launchers" and state.get("launcher"):
        grid.land_on(launchers_page.SCOPE, {"id": str(state["launcher"])})
    elif view == "players" and state.get("player"):
        grid.land_on(players_page.SCOPE, {"id": str(state["player"])})
    elif view == "backups" and state.get("backup"):
        grid.land_on(backups_page.SCOPE, {"id": str(state["backup"])})
    elif view == "discovery" and state.get("entry"):
        grid.land_on(discovery_page.SCOPE, {"id": str(state["entry"])})
        grid.land_on(discovery_releases_page.SCOPE, {"game_id": str(state["entry"])})
    elif view == "collections" and state.get("collection"):
        grid.land_on(collections_page.SCOPE, {"id": str(state["collection"])})
    elif state.get("ext_row") and community_page.find(view, state["extensions"]):
        listed = community_page.find(view, state["extensions"])
        if listed is not None:
            grid.land_on(community_page.scope_of(*listed), {"id": str(state["ext_row"])})


def _nav_parent(parent: tuple[str, str, str], state: dict[str, Any],
                labels: list, held: list, badges: dict) -> None:
    """The row a group of entries sits under, which opens and closes them.

    A disclosure, not a place: it has no page of its own, so a click that navigated
    would have to pick one of its children and the caret would then mean something
    different from the row it sits on. It carries a badge all the same - closed, it is
    the only row left that can say something under it wants attention.
    """
    key, label, icon = parent
    state.setdefault(f"{key}_open", bool(remembered.get(f"open.{key}", True)))

    def toggle() -> None:
        state[f"{key}_open"] = not state[f"{key}_open"]
        remembered.put(f"open.{key}", state[f"{key}_open"])
        _show_group(state[f"{key}_open"], caret, held)

    # A row rather than a link: this one opens and closes the entries under it and has
    # no page of its own, so there is no address for a browser to be offered.
    row = ui.row().classes("items-center gap-3 cursor-pointer w-full no-wrap "
                           "manager-nav-row").on("click", toggle)
    with row:
        with ui.element("div").classes("manager-nav-mark"):
            ui.icon(icon, size="24px").classes("opacity-70 shrink-0")
            badge = ui.label("").classes("manager-nav-badge")
            badge.set_visibility(False)
            badges[key] = badge
        labels.append(ui.label(t(label)).classes("manager-nav-item whitespace-nowrap"))
        ui.space()
        caret = ui.icon("expand_more", size="20px").classes("opacity-60 shrink-0")
        labels.append(caret)
    # Applied after the children exist, from the shell that owns the loop.
    held.append(caret)


def _show_group(open_now: bool, caret: Any, held: list) -> None:
    """Rows in, caret with them. The caret joins `labels` so it goes when the rail
    collapses to icons - there is nothing to disclose in a column of glyphs."""
    caret.props(f'name={"expand_more" if open_now else "chevron_right"}')
    for row in held:
        if row is not caret:
            row.set_visibility(open_now)


# Sections of another subject's panel: they name a file or catalog entry of the page just left.
OWN_SUBJECT_SECTIONS = ("media_file", "asset_file", *discovery_kinds.vocabulary())


def leave_for(state: dict[str, Any], view: str) -> None:
    """Point the shell at another destination, dropping what was selected on the last.

    Both ways of arriving somewhere else come through here, or one of them keeps the
    selection.
    """
    if view != state["view"]:
        state["game"] = ""
        state["table"] = ""
        state["collection"] = None
        state["launcher"] = None
        state["player"] = None
        state["backup"] = None
        state["entry"] = None
        state["ext_row"] = ""
        state["sets"] = None
        state.pop("discovery_tab", None)
        state.pop("opening", None)
        state["subject"] = None
        state.pop("slot", None)
        if state.get("section") in OWN_SUBJECT_SECTIONS:
            state.pop("section", None)
    state["view"] = view
    remembered.put("section", view)
    # The list comes first on a window that shows one of the two.
    if close_pane := state.get("close_pane"):
        close_pane()


@on_page
async def may_leave(state: dict[str, Any], view: str) -> bool:
    """Whether to go to `view`: asked first where that would drop rules or text nobody
    saved, and both dropped on a yes."""
    waiting = sorted(set(state.get("unsaved_rules") or ()) | set(text_editor.waiting(state)))
    if not waiting or view == state.get("view"):
        return True
    if not await confirm.ask(t("manager.page.leave_unsaved"),
                             detail=t("manager.page.unsaved_lost"), lines=waiting,
                             confirm=t("manager.page.leave"), icon=verbs.GO):
        return False
    state["collection_drafts"] = {}
    state["unsaved_rules"] = set()
    text_editor.drop_all(state)
    return True


def _owner_of(view: str, extensions: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The extension a rail entry came from, or None for one VPinFE draws itself."""
    found = community_page.find(view, extensions) or ext_pages.find(view, extensions)
    return found[0] if found else None


def _nav_item(key: str, label: str, icon: str, state: dict[str, Any], render: Callable[..., Any],
              labels: list, destinations: dict, badges: dict, nested: bool = False,
              held: list | None = None, owner: dict[str, Any] | None = None) -> None:
    @on_page
    async def choose() -> None:
        if not await may_leave(state, key):
            return
        leave_for(state, key)
        render()
        deeplink.sync(state, push=True)

    # An anchor, not a div. A destination has an address - it is in the bar the moment
    # you arrive at one - and a row that only listens for a click hides that from the
    # browser, so "open in a new tab", middle-click and copy-link-address are all dead
    # on a list of places. The click handler still does the navigating; `panel.NAV_CLICK`
    # stops the browser following the href on a plain click and leaves every modified
    # one alone.
    row = ui.link(target=f"/manager?view={key}") \
        .classes("items-center gap-3 cursor-pointer w-full no-wrap flex "
                 "manager-nav-row" + (" manager-nav-row--nested" if nested else "")) \
        .on("click", choose)
    with row:
        # The badge is positioned against the icon rather than the row, so it sits on the
        # same corner whether the rail is open or collapsed - the icon is the one part of
        # an entry that is in both states, and the label is not.
        with ui.element("div").classes("manager-nav-mark"):
            ui.icon(icon, size="24px").classes("opacity-70 shrink-0")
            badge = ui.label("").classes("manager-nav-badge")
            badge.set_visibility(False)
            badges[key] = badge
            # At the icon's corner, shown only where the label that carries it is hidden.
            ext_mark.draw(owner, classes="manager-ext-mark--corner")
        # `no-wrap` on the row and nowrap on the label. A rail entry is one line by
        # definition, and the rail scrolls once there are enough of them - which takes a
        # scrollbar's width off every row and was enough to break "Collections" in two.
        labels.append(ui.label(t(label))
                      .classes("manager-nav-item whitespace-nowrap"))
        after = ext_mark.draw(owner, classes="manager-ext-mark--after")
        if after is not None:
            labels.append(after)
    destinations[key] = row
    if held is not None and nested:
        held.append(row)


def _nav_link(key: str, label: str, icon: str, state: dict[str, Any], labels: list,
              nested: bool = False, held: list | None = None) -> None:
    """A rail entry that opens an extension's own UI in a new tab. Not a destination: it
    is never lit, never a view and never the page you land on."""
    found = ext_links.find(state["extensions"], key, state["host"])
    if found is None:
        return
    row = ui.link(target=found["url"], new_tab=True) \
        .classes("items-center gap-3 cursor-pointer w-full no-wrap flex manager-nav-row "
                 "manager-nav-row--out" + (" manager-nav-row--nested" if nested else ""))
    row.props["rel"] = ext_links.REL
    row.props["aria-label"] = t("manager.page.new_tab", label=label)
    with row:
        with ui.element("div").classes("manager-nav-mark"):
            ui.icon(icon, size="24px").classes("opacity-70 shrink-0")
        labels.append(ui.label(label).classes("manager-nav-item whitespace-nowrap"))
        labels.append(ui.icon(verbs.OPEN_OUT).classes("manager-nav-out-mark opacity-50 ml-auto"))
    if found["tip"]:
        row.tooltip(found["tip"])
    if held is not None and nested:
        held.append(row)


def read_outcome(result: dict[str, Any]) -> tuple[str, list[str], str]:
    """What a finished read of the library says, the new games waiting for a match, and a
    line under it for the games seen before that it matched."""
    waiting = [str(one) for one in result.get("new_unmatched_ids") or () if one]
    total = int(result.get("new_games") or 0)
    matched = int(result.get("new_matched") or 0)
    again = int(result.get("waiting_matched") or 0)
    also = t("manager.page.matched_waiting_games", count=again) if again else ""
    if waiting and matched:
        return t("manager.page.matched_some", count=len(waiting),
                 matched=matched, total=total), waiting, also
    if waiting:
        return t("manager.page.needs_match", count=len(waiting)), waiting, also
    if matched:
        return t("manager.page.matched_all", count=matched), [], also
    if also:
        return also, [], ""
    return t("manager.page.library_date"), [], ""


def read_failure(job: dict[str, Any]) -> tuple[str, str]:
    """What a read of the library that failed says, and the reason under it."""
    return t("manager.page.scan_failed"), str(job.get("error") or "")


@on_page
async def _read_the_library() -> dict | None:
    """Start a re-read of the library and hand back the job to watch.

    Not "look for new tables": the scan re-reads every grain of the library - the
    tables, the media beside them and the assets they launch with - and naming it for
    one of the four was 2.x's framing from when the library was only tables.
    """
    try:
        job = await offload.io(ApiClient().refresh_library)
    except Exception as exc:
        # Already running is the ordinary case here, not a failure worth a trace.
        ui.notify(t("manager.page.start_failed"), caption=why(exc), type="warning")
        return None
    ui.notify(t("manager.page.rescanning"), type="positive")
    return job
