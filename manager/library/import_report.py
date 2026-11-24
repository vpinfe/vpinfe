"""The import's report, in the order a person works through it: what needs a look, what is
good to know, what was left behind, and what came in.

A row is read from the result the importer kept (`items`, `rows`, `already_here`,
`left_behind`) and, when the report is shown, checked against the library again - a fixed
row keeps a tick instead of disappearing, so working down a list shows it getting shorter
without losing the place.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import datetime
from typing import Any

from nicegui import ui

from common.i18n import number, t
from manager.kit import deeplink, media_ownership, offload, panel, report_groups, when, wizard
from manager.kit.api import ApiClient
from manager.kit.on_page import on_page
from manager.settings import library_folders
from manager.system import jobs_list

# How many rows of a group are named before the rest is behind a link.
SHOWN = report_groups.SHOW_ALL_OVER

SECTION = "report-"
LOOKING, KNOWING, LEAVING, IMPORTING = "look", "know", "left", "imported"

NEED_ROM = report_groups.NEED_ROM
NOT_MATCHED = report_groups.NOT_MATCHED
NO_WHEEL = report_groups.NO_WHEEL
DMD_ALONE = report_groups.DMD_ALONE
NOT_BROUGHT = report_groups.NOT_BROUGHT

# What a fixed row says it is, by group.
TICKS = {NEED_ROM: "manager.report.tick.added", NOT_MATCHED: "manager.report.tick.matched",
         NO_WHEEL: "manager.report.tick.added"}

# The wheel slot is filled by either of these.
WHEEL_KINDS = ("wheel", "logo")


def has_report(job: dict[str, Any]) -> bool:
    """Whether the importer kept the lists this page draws, which an older report lacks."""
    result = job.get("result") or {}
    return "items" in result and "left_behind" in result


async def check(job: dict[str, Any]) -> None:
    """Annotate `job` with `checked`: the games of each group the library now has what
    the group lacked for. A read that fails leaves its group as the last check had it."""
    items = (job.get("result") or {}).get("items") or []
    wanted = {str(one.get("group")) for one in items}
    client = ApiClient()
    fixed: dict[str, set[str]] = {}

    async def read(group: str, call: Callable[[], Any],
                   settled: Callable[[list[dict]], set[str]]) -> None:
        if group not in wanted:
            return
        try:
            fixed[group] = settled(await offload.io(call))
        except Exception:  # noqa: BLE001 - an unreadable library is a list as it was written
            return

    await read(NEED_ROM, client.all_tables, lambda rows: {
        str(row["game_id"]) for row in rows if row.get("rom_installed") is True})
    await read(NOT_MATCHED, client.games, lambda rows: {
        str(row["game_id"] if "game_id" in row else row.get("id")) for row in rows
        if row.get("vps_id")})
    if NO_WHEEL in wanted:
        held: set[str] = set()
        try:
            for kind in WHEEL_KINDS:
                held |= {str(row["game_id"]) for row in
                         await offload.io(client.all_media, kind) if row.get("present")}
            fixed[NO_WHEEL] = held
        except Exception:  # noqa: BLE001
            pass
    job["checked"] = {**(job.get("checked") or {}), **fixed}
    games = set(report_groups.made_games(job))
    if games:
        try:
            job["unsupported"] = sum(
                1 for row in await offload.io(client.all_media)
                if str(row.get("game_id")) in games and row.get("present") is not False
                and row.get("via") == media_ownership.UNSUPPORTED)
        except Exception:  # noqa: BLE001 - a count that cannot be read is no line
            pass


def _to(game_id: str = "", *, view: str = "tables", table: str = "", section: str = "",
        slot: str = "") -> str:
    """A game in one of the grids, at a section of its panel or on one of its slots."""
    return "/manager?" + deeplink.query({
        "view": view, "game": game_id, "table": table,
        **({"section": section} if section else {}),
        **({"slot": {"kind": slot}} if slot else {})})


def _displays() -> str:
    """Settings, at its Displays page."""
    return "/manager?" + deeplink.query({"view": "settings", "settings_page": "hardware.displays"})


def _link(label: str, to: str, on_click: Callable[[], Any] | None = None,
          classes: str = "") -> Callable[[], None]:
    return panel.link(label, to=to, on_click=on_click, classes=classes,
                      window="" if on_click else report_groups.MANAGER_WINDOW)


def _row(name: str, note: str = "", *, links: tuple[Callable[[], None], ...] = (),
         tick: str = "", code: bool = False) -> None:
    with ui.element("div").classes("manager-report-row"):
        ui.label(name).classes(
            "manager-report-name" + ("" if note else " manager-report-name--wide"))
        if note:
            ui.label(note).classes(
                "manager-help manager-report-note" + (" manager-report-note--code" if code else ""))
        with ui.element("div").classes("manager-report-act"):
            if tick:
                ui.label(tick).classes("manager-member-chip manager-chip-quiet")
                return
            for draw in links:
                draw()


def _section(key: str, title: str, count: int | None = None, help_: str = "") -> ui.column:
    """A group of the report: its title with how many are in it, and one line on what it is.
    Draw the rest of the group inside the returned column."""
    box = ui.column().classes("manager-report-section w-full").props(f"id={SECTION}{key}")
    with box:
        with ui.row().classes("manager-report-head"):
            ui.label(t(title)).classes("manager-report-title")
            if count is not None:
                ui.label(number(count)).classes("manager-help")
        if help_:
            ui.label(help_).classes("manager-help")
    return box


def _more(job: dict[str, Any], group: str, count: int) -> Callable[[], None]:
    """The link to a group's games in Tables, or what is left of it where there is no
    such group to narrow Tables to."""
    if group in report_groups.groups(job):
        return _link(t("manager.report.show_all", count=count),
                     report_groups.tables_address(str(job.get("id") or ""), group))
    def rest() -> None:
        ui.label(t("manager.report.more", count=count - SHOWN)).classes("manager-help")
    return rest


def _group(job: dict[str, Any], group: str, title: str, rows: list[dict[str, Any]],
           draw: Callable[[dict[str, Any], bool], None], *,
           under: tuple[Callable[[], None], ...] = ()) -> None:
    if not rows:
        return
    fixed = (job.get("checked") or {}).get(group, set())
    ordered = sorted(rows, key=lambda one: str(one.get("game_id") or "") in fixed)
    waiting_here = sum(1 for one in ordered if str(one.get("game_id") or "") not in fixed)
    with ui.element("div").classes("manager-report-group"):
        with ui.row().classes("manager-report-group-head"):
            ui.label(t(title)).classes("manager-report-group-title")
            ui.label(number(waiting_here)).classes("manager-help")
        for one in ordered[:SHOWN]:
            draw(one, str(one.get("game_id") or "") in fixed)
        rest = _more(job, group, len(ordered)) if len(ordered) > SHOWN else None
        if rest is not None or under:
            with ui.row().classes("manager-report-foot"):
                if rest is not None:
                    rest()
                for each in under:
                    each()


def _items(result: dict[str, Any], group: str) -> list[dict[str, Any]]:
    return [one for one in result.get("items") or [] if one.get("group") == group]


def _worded(said: Any) -> tuple[str, str]:
    if isinstance(said, dict):
        text, detail = str(said.get("text") or ""), str(said.get("detail") or "")
        return (text, detail) if text else (detail, "")
    return str(said), ""


@on_page
async def retry(job: dict[str, Any], keys: list[str]) -> None:
    """A second import, started from this one's answers and with these games ticked."""
    result = job.get("result") or {}
    try:
        found = await offload.io(ApiClient().extensions)
    except Exception:  # noqa: BLE001 - no extension list is no importer to open
        found = []
    action = library_folders.import_action_in(found)
    if action is None:
        ui.notify(t("manager.report.importer_off"), type="warning")
        return
    await library_folders.open_importer(
        action, None, values={**(result.get("answers") or {}), "only": keys}, until="games")


def _again(job: dict[str, Any], label: str, keys: list[str]) -> Callable[[], None]:
    async def go() -> None:
        await retry(job, keys)
    return _link(label, report_groups.report_address(str(job.get("id") or "")), go,
                 "font-bold")


def _lists(result: dict[str, Any], failed: list[dict[str, Any]]) -> bool:
    return bool(failed) or any(_items(result, group) for group in LOOK)


def _looking(job: dict[str, Any], result: dict[str, Any], failed: list[dict[str, Any]]) -> None:
    """What needs a look."""
    if not _lists(result, failed):
        return
    with _section(LOOKING, "manager.report.look", waiting(job), t("manager.report.look.help")):
        with _card():
            _failed(job, failed)
            _unsettled(job, result)


def _failed(job: dict[str, Any], failed: list[dict[str, Any]]) -> None:
    _group(job, "failed", "manager.report.failed", failed,
           lambda one, _fixed: _row(
               str(one.get("name") or one.get("key")), _worded(one["error"])[0]),
           under=(_link(t("manager.report.see_log"), "/manager?view=logs"),
                  _again(job, t("manager.report.try_again"),
                         [str(one["key"]) for one in failed])))


def _unsettled(job: dict[str, Any], result: dict[str, Any]) -> None:
    for group, title, note, link in (
            (NEED_ROM, "manager.report.need_rom", lambda one: str(one.get("reason") or ""),
             lambda one: _link(t("manager.report.add_rom"),
                               _to(str(one["game_id"]), view="assets", slot="rom"))),
            (NOT_MATCHED, "manager.report.not_matched",
             lambda one: str(one.get("reason") or ""),
             lambda one: _link(t("manager.report.match"),
                               _to(str(one["game_id"]), section="game_details"))),
            (NO_WHEEL, "manager.report.no_wheel", lambda _one: "",
             lambda one: _link(t("manager.report.add_wheel"),
                               _to(str(one["game_id"]), view="media", slot="wheel"))),
            (NOT_BROUGHT, "manager.report.not_imported",
             lambda one: str(one.get("reason") or ""),
             lambda one: _link(t("manager.report.open"), _to(str(one["game_id"]))))):
        _group(job, group, title, _items(result, group),
               lambda one, fixed, group=group, note=note, link=link: _row(  # type: ignore[misc]
                   str(one.get("name") or one.get("key")), note(one),
                   links=(link(one),), tick=t(TICKS[group]) if fixed else "",
                   code=group == NEED_ROM))


def _known(job: dict[str, Any], result: dict[str, Any]) -> None:
    held = list(result.get("already_here") or [])
    dmd = _items(result, DMD_ALONE)
    unsupported = int(job.get("unsupported") or 0)
    if not held and not dmd and not unsupported:
        return
    with _section(KNOWING, "manager.report.know", len(held) + len(dmd) + bool(unsupported)):
        with _card():
            _group(job, report_groups.ALREADY_HERE, "manager.report.already", held,
                   lambda one, _fixed: _row(
                       str(one.get("name") or one.get("key")), str(one.get("how") or ""),
                       links=(_link(t("manager.report.open"),
                                    _to(str(one.get("game_id") or ""))),)),
                   under=(_again(job, t("manager.report.add_missing"),
                                 [str(one["key"]) for one in held]),))
            _group(job, DMD_ALONE, "manager.report.dmd", dmd,
                   lambda one, _fixed: _row(
                       str(one.get("name") or one.get("key")), str(one.get("reason") or ""),
                       links=(_link(t("manager.report.displays"),
                                    _to(str(one["game_id"]),
                                        table=str(one.get("table_id") or ""),
                                        section="table_settings")),)))
            if unsupported:
                _unsupported(unsupported)


def _unsupported(count: int) -> None:
    """The files the import brought that VPinFE cannot use, and where Convert is."""
    with ui.element("div").classes("manager-report-group"):
        with ui.row().classes("manager-report-group-head"):
            ui.label(t("manager.report.unsupported")).classes("manager-report-group-title")
            ui.label(number(count)).classes("manager-help")
        _row(t("manager.report.unsupported.note", count=count), links=(
            _link(t("manager.media_convert.convert"),
                  report_groups.media_address(report_groups.UNSUPPORTED)),))


def _left(result: dict[str, Any]) -> None:
    behind = list(result.get("left_behind") or [])
    if not behind:
        return
    with _section(LEAVING, "manager.report.left_behind", len(behind),
                  t("manager.report.left_behind.help")):
        with _card(), ui.element("div").classes("manager-report-group"):
            for one in behind:
                _row(str(one.get("what") or ""), str(one.get("detail") or ""), links=(
                    (_link(t("manager.report.displays"), _displays()),)
                    if one.get("displays") else ()))


def _imported(job: dict[str, Any], result: dict[str, Any]) -> None:
    rows = list(result.get("against") or [])
    group = report_groups.groups(job).get(report_groups.IMPORTED)
    games = _games(job, result)
    alone = report_groups.artwork_alone(job)
    with _section(IMPORTING, "manager.report.imported", games):
        if rows:
            with ui.element("div").classes("manager-tiles"):
                for one in rows:
                    _tile(one, alone if one.get("key") == "games" else 0)
        if group is not None and len(group.game_ids) > SHOWN:
            _link(t("manager.report.show_all", count=report_groups.tables_made(job)),
                  report_groups.tables_address(str(job.get("id") or ""),
                                               report_groups.IMPORTED))()


def _games(job: dict[str, Any], result: dict[str, Any]) -> int:
    """The games imported: what the import counted, else what Tables can list."""
    for one in result.get("against") or []:
        if one.get("key") == "games":
            return int(one.get("actual") or 0)
    group = report_groups.groups(job).get(report_groups.IMPORTED)
    return len(group.game_ids) if group is not None else 0


def _tile(row: dict[str, Any], alone: int) -> None:
    want, got = int(row.get("expected") or 0), int(row.get("actual") or 0)
    short = max(want - got, 0)
    with ui.element("div").classes("manager-card"):
        ui.label(number(got)).classes("manager-kpi")
        with ui.row().classes("items-center gap-2"):
            ui.label(str(row.get("label") or row.get("key") or "")).classes("manager-help")
            if short:
                ui.label(t("manager.ext_action.short", short=short)).classes(
                    "manager-member-chip manager-chip-warn")
        if short:
            ui.label(t("manager.ext_action.of_expected", want=number(want))).classes("manager-help")
        if alone:
            ui.label(t("manager.report.artwork_alone", count=alone)).classes("manager-help")


def _card() -> ui.element:
    return ui.element("div").classes("manager-card manager-report-card w-full")


def _jump(key: str, count: int, label: str, *, warm: bool = False) -> None:
    """A figure that scrolls to its group. A script, not an address: a hash would change
    where the Manager thinks it is."""
    cell = ui.element("div").classes(
        "manager-report-cell" + (" manager-report-cell--warm" if warm else ""))
    scroll = (f"() => document.getElementById('{SECTION}{key}')"
              ".scrollIntoView({behavior: 'smooth'})")
    cell.props("tabindex=0 role=link")
    cell.on("click", js_handler=scroll)
    cell.on("keydown.enter", js_handler=scroll)
    with cell:
        ui.label(number(count)).classes("manager-kpi")
        ui.label(label).classes("manager-help")


def _summary(job: dict[str, Any], result: dict[str, Any], failed: list[dict[str, Any]]) -> None:
    """How much there is of each group, above the groups. A figure appears only for a group
    that is drawn."""
    held = list(result.get("already_here") or [])
    dmd = _items(result, DMD_ALONE)
    unsupported = bool(job.get("unsupported"))
    behind = list(result.get("left_behind") or [])
    games = _games(job, result)
    with ui.element("div").classes("manager-card manager-report-strip w-full"):
        if _lists(result, failed):
            left = waiting(job)
            _jump(LOOKING, left, t("manager.report.jump.look"), warm=bool(left))
        if held or dmd or unsupported:
            _jump(KNOWING, len(held) + len(dmd) + unsupported, t("manager.report.know"))
        if behind:
            _jump(LEAVING, len(behind), t("manager.report.left_behind"))
        _jump(IMPORTING, games, t("manager.report.jump.imported"))


def _finished(job: dict[str, Any], result: dict[str, Any]) -> None:
    ended = job.get("finished_at")
    if not ended:
        return
    program = str(result.get("program") or "") or jobs_list.what_ran(IMPORT_KIND)
    at = datetime.fromtimestamp(float(ended)).astimezone()
    ui.label(t("manager.report.finished", program=program, day=when.day(at),
               clock=when.clock(at))).classes("manager-help")


def draw(job: dict[str, Any]) -> None:
    """The report of an import that kept its lists."""
    result = job.get("result") or {}
    failed = [row for row in result.get("rows") or [] if row.get("error")]
    with ui.column().classes("manager-report w-full"):
        _finished(job, result)
        _summary(job, result, failed)
        _looking(job, result, failed)
        _known(job, result)
        _left(result)
        _imported(job, result)


# What the import is called in the Jobs list, and so what its jobs are found by.
IMPORT_KIND = "library_importer.import"
# The groups whose rows are for the person to settle, counted by the line on Locations.
LOOK = (NEED_ROM, NOT_MATCHED, NO_WHEEL, NOT_BROUGHT)
LOOKBACK = jobs_list.LIMIT
# `check` reads the library, and Locations draws again on every folder action.
FRESH_FOR = 15.0
_drawn: dict[str, tuple[float, tuple[str, str]]] = {}


def waiting(job: dict[str, Any]) -> int:
    """The rows under Needs a look that the library has not settled, as `draw` shows them.
    Counts what `check` last found, so check first."""
    result = job.get("result") or {}
    fixed = job.get("checked") or {}
    failed = sum(1 for row in result.get("rows") or [] if row.get("error"))
    return failed + sum(
        1 for one in result.get("items") or [] if one.get("group") in LOOK
        and str(one.get("game_id") or "") not in fixed.get(str(one.get("group")), set()))


async def latest(library: Any) -> tuple[str, str] | None:
    """The line for the newest import that kept a report, as its words and the address of
    the report; None where there is no such import or the jobs cannot be read."""
    try:
        found = await offload.io(library.jobs, LOOKBACK)
        kept = [one for one in found if one.get("kind") == IMPORT_KIND and one.get("report")]
        if not kept:
            return None
        newest = max(kept, key=lambda one: float(one.get("started_at") or 0))
        key = str(newest["id"])
        recent = _drawn.get(key)
        if recent and time.monotonic() - recent[0] < FRESH_FOR:
            return recent[1]
        job = await offload.io(library.job, key)
        await check(job)
        program = str((job.get("result") or {}).get("program") or "") \
            or jobs_list.what_ran(IMPORT_KIND)
        address = report_groups.report_address(key)
        left = waiting(job)
        if left:
            line = t("manager.folders.import_look", program=program, count=left), address
        else:
            ended = datetime.fromtimestamp(float(job["finished_at"])).astimezone()
            line = t("manager.folders.import_done", program=program,
                     when=when.day(ended)), address
    except Exception:  # noqa: BLE001 - a line that cannot be read is no line
        return None
    _drawn[key] = (time.monotonic(), line)
    return line


def latest_line(line: tuple[str, str]) -> Callable[[], None]:
    """The line from `latest`, opening the report in a tab of its own."""
    words, address = line
    return panel.link_out(words, to=address, hint=t("manager.report.own_tab.hint"), opener=True)


wizard.REPORT_DRAWERS.append((has_report, draw))
