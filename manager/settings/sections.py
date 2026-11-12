"""Overview, the folder lists behind its rows, and the checks that decide what needs a person."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Collection, Mapping
from datetime import UTC, datetime
from typing import Any

from nicegui import run, ui

from common.failures import why
from common.i18n import number, t
from common.media.media_specs import media_label_map
from common.online import vps_home
from common.security import approvals
from manager.extensions import ext_mark
from manager.kit import dialog as frame
from manager.kit import game_tables, offload, panel, verbs, when
from manager.kit.data import Library
from manager.kit.on_page import on_page
from manager.library import media_fill
from manager.overview import front, needs, set_aside
from manager.system import jobs_list

# key, label, why it matters, predicate over (game, its media entries, facts). The label and
# the sentence are what a row on Overview says, so a check is about something a person can
# act on. `facts` carries the collected media kinds and whether a rom is missing.
CHECKS: tuple[tuple[str, str, str, Callable[[dict, dict, dict], bool]], ...] = (
    ("rom_missing", t("manager.sections.rom_missing"),
     t("manager.sections.rom_missing_why"),
     lambda g, m, x: bool(x.get("rom_missing"))),
    ("unmatched", t("manager.sections.unmatched"),
     t("manager.sections.unmatched_why"),
     lambda g, m, x: not g.get("vps_id")),
    ("no_tables", t("manager.sections.no_tables"),
     t("manager.sections.no_tables_why"),
     lambda g, m, x: g.get("table_count") == 0),
    ("no_playfield", t("manager.sections.playfield_missing"),
     t("manager.sections.playfield_empty"),
     lambda g, m, x: not m.get("playfield", {}).get("present")),
    ("no_backglass", t("manager.sections.backglass_missing"),
     t("manager.sections.screen_empty"),
     lambda g, m, x: not m.get("backglass", {}).get("present")),
    ("no_media", t("manager.sections.media_none"),
     t("manager.sections.nothing_found"),
     lambda g, m, x: not any(e.get("present") for kind, e in m.items()
                             if kind in x["collected"])),
    ("no_year", t("manager.sections.year_missing"),
     t("manager.sections.year_sort"),
     lambda g, m, x: game_tables.year_of(g.get("year")) is None),
)

# The checks a source can answer, and the kind each one fetches.
FILLS = {"no_playfield": "playfield", "no_backglass": "backglass"}


def _a_source_fills(key: str, fillable: Collection[str], collected: Collection[str]) -> bool:
    """Whether a source has a file for a slot this check calls a gap. Discovery lists such a
    game under Missing from Your Games, so the check leaves it there. `no_media` is filled
    when any kind the library collects can be."""
    if key in FILLS:
        return FILLS[key] in fillable
    return key == "no_media" and any(kind in collected for kind in fillable)


def source_filled(library: Library) -> dict[str, set[str]]:
    """Per check a source can fill, the games it leaves out because a source has a file for
    the slot: the ones `findings` skips."""
    collected = library.kept_kinds()["media"]
    offered = library.fillable_media()
    out: dict[str, set[str]] = {key: set() for key in (*FILLS, "no_media")}
    for game in library.games:
        fillable = {kind for kind, ids in offered.items() if game["id"] in ids}
        for key, ids in out.items():
            if _a_source_fills(key, fillable, collected):
                ids.add(str(game["id"]))
    return out


def rollups(library: Library) -> dict[str, dict[str, Any]]:
    """Per-game facts a check needs that the game payload does not carry.

    Whether a rom is installed is resolved per table, because two builds of one machine
    can declare different ones. A game reads as missing a rom when any of its tables
    declares one that PinMAME's audit says is not there.

    `rom_installed` is three-valued and only `False` counts. `None` is "we could not
    tell" - the audit needs a configured VPX binary, and the name match alone cannot
    see a clone set's parent zip. Treating not-known as missing would report a whole
    library as broken on any machine without VPX.
    """
    out: dict[str, dict[str, Any]] = {}
    for row in library.table_rows():
        fact = out.setdefault(str(row.get("game_id") or ""), {"rom_missing": False})
        if row.get("rom_installed") is False:
            fact["rom_missing"] = True
    return out


def reported(library: Library) -> tuple:
    """The checks this library asks to hear about.

    The library's answer, not this install's: a gap is a fact about the files, so two
    installs reading one library report the same ones. An unreadable policy reports
    everything, which is the answer a fresh library gives anyway.
    """
    hidden = library.hidden_checks()
    return tuple(one for one in CHECKS if one[0] not in hidden)


def findings(library: Library) -> dict[str, list[dict[str, Any]]]:
    """Run the checks this library asks for. Keyed by check, so a section shows counts."""
    collected = library.kept_kinds()["media"]
    # A kind the library does not collect has no column to land on and is not a gap.
    asked = tuple(one for one in reported(library)
                  if one[0] not in FILLS or FILLS[one[0]] in collected)
    out: dict[str, list[dict[str, Any]]] = {key: [] for key, _, _, _ in asked}
    extra = rollups(library)
    offered = library.fillable_media()
    for game in library.games:
        entries = library.media.get(game["id"], {})
        facts = {"collected": collected, **extra.get(game["id"], {})}
        fillable = {kind for kind, ids in offered.items() if game["id"] in ids}
        for key, _, _, predicate in asked:
            try:
                if _a_source_fills(key, fillable, collected):
                    continue
                if predicate(game, entries, facts):
                    out[key].append(game)
            except Exception:
                # A check that throws is a broken check, not a broken library. It
                # reports nothing rather than taking the page down with it.
                continue
    return out


def _bar(fraction: float) -> None:
    with ui.element("div").classes("manager-bar w-full"):
        ui.element("div").style(f"width:{max(0.0, min(1.0, fraction)) * 100:.0f}%")


# --- Overview --------------------------------------------------------------------

def kind_holdings(library: Library) -> list[tuple[str, int]]:
    """How many games hold each kind the library collects, fewest first.

    Only the kinds collected: a kind switched off is not a gap, and listing it would offer
    a bar for a file nobody wants."""
    held = {kind: 0 for kind in library.kinds() if kind in library.kept_kinds()["media"]}
    ids = {game["id"] for game in library.games}
    for game_id, entries in library.media.items():
        if game_id not in ids:
            continue
        for kind, entry in entries.items():
            if kind in held and entry.get("present"):
                held[kind] += 1
    return sorted(held.items(), key=lambda item: (item[1], item[0]))


def _local_name(registry: list[dict], discovery: dict) -> str:
    from manager.devices import devices

    local = discovery.get("install_id")
    entry = next((one for one in registry if one.get("device_id") == local), {})
    return devices.device_label(entry)


def _need_menu(menu: Any, need: needs.Need, fix: Callable[..., Any],
               acts: set_aside.Acts) -> None:
    """What a row offers beyond opening: the fix, for just the new ones where some joined,
    then setting the row aside. The fix is only here: a row's end carries the menu and
    nothing else, so every row lays out alike."""
    menu.clear()
    with menu:
        if (mine := need.fix) is not None:
            panel.menu_entry(mine.label, lambda: fix(need), under=mine.tip)
            if need.new and mine.key == needs.GET_MEDIA:
                panel.menu_entry(
                    t("manager.needs.fix_new", fix=mine.label.removesuffix("…"),
                      count=len(need.new)),
                    lambda: fix(need, need.new))
        panel.menu_entry(
            t("manager.needs.skip_version") if need.key == needs.UPDATE
            else t("manager.needs.dismiss"),
            lambda: acts.dismiss(need), under=needs.comes_back(need.key, need))
        panel.menu_entry(
            t("manager.needs.stop_checking"), lambda: acts.stop(need.key, need.label),
            under=t("manager.needs.stop_checking.extension", name=need.source)
            if needs.is_extension(need.key) else "")


def _need_row(need: needs.Need, state: dict[str, Any], fix: Callable[..., Any],
              acts: set_aside.Acts) -> None:
    """Icon in its tone, label, what joined since it was dismissed, count in its own unit,
    and the whole row opens what it counted. Its menu is the button at its end and a right
    click anywhere on it."""
    if need.answer:
        _ask_row(need, acts)
        return
    row = ui.element("div").classes(f"manager-need manager-need--{need.tone}") \
        .props("tabindex=0 role=link")
    row.on("click", lambda: state["open_on"](need.opens))
    row.on("keydown.enter", lambda: state["open_on"](need.opens))
    with row:
        ui.icon(need.icon).classes("manager-need-icon")
        ui.label(need.label).classes("manager-setting grow min-w-0").tooltip(need.tip)
        if need.new and need.opens.view in ("games", "tables"):
            # Only a grid has rows to narrow to the new ones.
            from manager.library import games

            def only_new() -> Any:
                return state["open_on"](needs.Opens(
                    need.opens.view, need.opens.arriving,
                    {**need.opens.keys, games.NEW_KEY: need.new}, need.opens.then))

            with ui.element("div").on("click.stop", lambda: None) \
                    .on("keydown.enter.stop", lambda: None):
                chip = ui.label(t("manager.needs.new", count=len(need.new))) \
                    .classes("manager-member-chip manager-chip-warn manager-need-new") \
                    .props("tabindex=0 role=link") \
                    .tooltip(t("manager.needs.new.tip"))
                chip.on("click", only_new)
                chip.on("keydown.enter", only_new)
        if said := need.counted() or need.source:
            ui.label(said).classes("manager-need-count shrink-0")
        if not needs.can_set_aside(need.key):
            return
        with ui.element("div").classes("manager-need-more").on("click.stop", lambda: None) \
                .on("keydown.enter.stop", lambda: None):
            _button, menu = panel.menu_button(verbs.MORE, t("manager.needs.row_menu"))
        _need_menu(menu, need, fix, acts)
        _need_menu(ui.context_menu(), need, fix, acts)


def _ask_row(need: needs.Need, acts: set_aside.Acts) -> None:
    """A program's ask: what it would change, exactly, and the two answers. Nothing here
    opens, because nothing else is where it is decided."""
    row = ui.element("div").classes(f"manager-need manager-need--static manager-need--{need.tone}")
    with row:
        ui.icon(need.icon).classes("manager-need-icon")
        with ui.element("div").classes("grow min-w-0"):
            ui.label(need.label).classes("manager-setting")
            ui.label(need.detail).classes("manager-help manager-need-change")
        panel.action(t("manager.needs.decline"), lambda: acts.answer(need, False), inline=True,
                     icon=verbs.IGNORE, hint=t("manager.needs.decline.help"))()
        panel.action(t("manager.needs.approve"), lambda: acts.answer(need, True), inline=True,
                     icon=verbs.ACCEPT, hint=t("manager.needs.approve.help"))()
    row.tooltip(need.tip)


def _aside_line(one: needs.Aside, acts: set_aside.Acts) -> None:
    with ui.row().classes("manager-aside-line items-center gap-3 w-full no-wrap"):
        ui.icon(one.icon).classes("manager-need-icon manager-need--muted")
        ui.label(one.label).classes("manager-setting grow min-w-0")
        ui.label(one.chip).classes("manager-member-chip manager-chip-quiet") \
            .tooltip(one.tip)
        again = acts.check_again if one.stopped else acts.show_again
        panel.action(t("manager.needs.check_again") if one.stopped
                     else t("manager.needs.show_again"),
                     lambda: again(one.key, one.label), inline=True,
                     icon=verbs.REFRESH if one.stopped else verbs.UNHIDE,
                     hint=t("manager.needs.check_again.help") if one.stopped
                     else t("manager.needs.show_again.help"))()


def _needs_you(arranged: needs.Arranged, state: dict[str, Any], fix: Callable[..., Any],
               acts: set_aside.Acts) -> None:
    ui.label(t("manager.needs.title")).classes("manager-group")
    with ui.element("div").classes("manager-card manager-needs"):
        if not arranged.rows:
            with ui.row().classes("items-center gap-3 w-full no-wrap"):
                ui.icon("check_circle").classes("manager-need-icon manager-need--none")
                ui.label(t("manager.needs.nothing")).classes("manager-setting grow")
                panel.action(t("manager.needs.choose_checks"),
                             lambda: state["open_on"](needs.Opens(
                                 "settings", keys={"settings_page": "library.checks"})),
                             icon=verbs.GO, hint=t("manager.needs.choose_checks.help"))()
        for group in needs.grouped(arranged.rows):
            with ui.element("div").classes(
                    "manager-need-group" + ("" if group.label else " manager-need-group--loose")):
                if group.label:
                    _group_heading(group, state)
                for need in group.rows:
                    _need_row(need, state, fix, acts)
    if arranged.aside:
        shown = panel.disclosure(t("manager.needs.set_aside", count=len(arranged.aside))) \
            .classes("manager-disclosure-rows")
        shown.value = bool(state.get(_ASIDE_OPEN))
        shown.on_value_change(lambda event: state.__setitem__(_ASIDE_OPEN, event.value))
        with shown, ui.element("div").classes("manager-card manager-needs w-full"):
            for one in arranged.aside:
                _aside_line(one, acts)


def _group_heading(group: needs.Group, state: dict[str, Any]) -> None:
    mark = ext_mark.drawer(ext_mark.by_name(
        state.get("extensions") or [], group.owner[len(needs.EXTENSION):])) \
        if group.owner.startswith(needs.EXTENSION) else None
    if mark is None:
        ui.label(group.label).classes("manager-group")
        return
    with ui.row().classes("manager-group items-center gap-1 no-wrap"):
        mark()
        ui.label(group.label)


def _opens(row: Any, state: dict[str, Any], where: needs.Opens) -> None:
    """Make a row a link: it opens `where` on a click or Enter."""
    row.props("tabindex=0 role=link")
    row.on("click", lambda: state["open_on"](where))
    row.on("keydown.enter", lambda: state["open_on"](where))


def _figure(count: int, label: str, state: dict[str, Any], where: needs.Opens) -> None:
    figure = ui.element("div").classes("manager-figure")
    _opens(figure, state, where)
    with figure:
        ui.label(number(count)).classes("manager-kpi")
        ui.label(label).classes("manager-help")


def home_tab(kind: str) -> str:
    """The Discovery Home tab of missing media a media kind is counted under: the VPS kind
    a source files it with (`vps_home.MEDIA_OF`), else its own name."""
    return next((listing for listing, media in vps_home.MEDIA_OF.items() if media == kind), kind)


def _kind_row(kind: str, held: int, total: int, state: dict[str, Any],
              lacking: tuple[str, ...] | None = None) -> None:
    """One bar of what the library holds. `lacking` is the games Home's tab for the kind
    lists, which is the figure the row links to and the tooltip names, so the row and the
    tab open the same set. None where Home could not be read: the row then counts every
    game without the kind and opens those."""
    name = media_label_map().get(kind, kind)
    without = total - held if lacking is None else len(lacking)
    row = ui.element("div").classes("manager-need")
    if not without:
        row.classes("manager-need--static")
    elif lacking is None:
        _opens(row, state, needs.Opens("games", needs.blank(f"media_{kind}")))
    else:
        from manager.library import games
        _opens(row, state, needs.Opens("games", keys={games.LACKING_KEY: lacking}))
    with row:
        ui.label(name).classes("manager-setting w-40 shrink-0")
        with ui.element("div").classes("grow min-w-0"):
            _bar(held / total if total else 0)
        ui.label(t("manager.sections.held_of_games", held=held, total=total)) \
            .classes("manager-need-count shrink-0")
    if without:
        row.tooltip(t("manager.sections.kind_tip" if lacking is not None
                      else "manager.sections.kind_without",
                      held=held, total=total, kind=name, without=without))
    elif held < total:
        row.tooltip(t("manager.sections.kind_held", held=held, total=total, kind=name))
    else:
        row.tooltip(t("manager.sections.kind_all", total=total, kind=name))


def _hide_kinds(library: Library, state: dict[str, Any], kinds: list[str]) -> Any:
    @on_page
    async def hide() -> None:
        try:
            await run.io_bound(library.hide_media_kinds, kinds)
        except Exception as exc:  # noqa: BLE001 - the reason belongs on screen
            ui.notify(t("manager.sections.hide_failed"), caption=why(exc), type="negative")
            return
        await state["open_on"](needs.Opens("settings", keys={"settings_page": "library.media"}))

    return hide


_NAMED_KINDS = 2


def _library(library: Library, state: dict[str, Any]) -> None:
    held = kind_holdings(library)
    total = len(library.games)
    ui.label(t("manager.sections.library")).classes("manager-group")
    with ui.element("div").classes("manager-card w-full"):
        with ui.element("div").classes("manager-figures"):
            _figure(total, t("manager.section.games"), state, needs.Opens("games"))
            _figure(len(library.table_rows()), t("manager.section.tables"), state,
                    needs.Opens("tables"))
        present = [(kind, count) for kind, count in held if count]
        if present:
            ui.label(t("manager.sections.games_with")).classes("manager-fact-heading")
            tabs = library.home_missing()
            for kind, count in present:
                lacking = None if tabs is None else tuple(
                    tabs.get(home_tab(kind), {}).get("game_ids") or ())
                _kind_row(kind, count, total, state, lacking)
        empty = [kind for kind, count in held if not count]
        if empty:
            names = [media_label_map().get(kind, kind) for kind in empty]
            shown = ", ".join(names[:_NAMED_KINDS])
            with ui.row().classes("items-center gap-3 w-full no-wrap"):
                folded = len(names) > _NAMED_KINDS
                panel.line(t("manager.sections.none_have", count=len(empty),
                             kinds=t("manager.sections.more", names=shown) if folded else shown),
                           hint=", ".join(names) if folded else "",
                           classes="manager-help grow min-w-0")
                panel.action(t("manager.sections.hide_kinds"),
                             _hide_kinds(library, state, empty), inline=True, icon=verbs.HIDE,
                             hint=t("manager.sections.hide_kinds.help"))()


RECENT_JOBS = 5

# How a job went: its glyph and the tone it is drawn in.
_JOB_MARKS = {
    "running": (verbs.JOB_RUNNING, "accent"),
    "done": (verbs.DONE, "none"),
    jobs_list.WITH_ERRORS: (verbs.HAD_ERRORS, "warm"),
    "failed": (verbs.JOB_FAILED, "danger"),
    jobs_list.STOPPED: (verbs.STOP, "muted"),
}


def _latest_jobs(library: Library) -> list[dict[str, Any]] | None:
    """The last few jobs, newest first. None where they could not be read."""
    listed = library.front().get("listed")
    if listed is None:
        return None
    progress = {str(one.get("id") or ""): float(one.get("pct") or 0) for one in listed}
    return [{**one, "pct": progress.get(one["id"], 0.0)}
            for one in jobs_list.rows(listed, time.time())[:RECENT_JOBS]]


def _job_line(one: dict[str, Any], state: dict[str, Any]) -> None:
    icon, tone = _JOB_MARKS.get(one["outcome"], (verbs.DONE, "muted"))
    row = ui.element("div").classes("manager-need")
    if tone != "none":
        row.classes(f"manager-need--{tone}")
    _opens(row, state, needs.Opens("jobs", keys={"jobs_find": one["what"],
                                                  "jobs_outcome": ""}))
    with row:
        ui.icon(icon).classes("manager-need-icon" + (" manager-need--none" if tone == "none"
                                                      else ""))
        with ui.element("div").classes("grow min-w-0"):
            ui.label(one["what"]).classes("manager-setting")
            if one["detail"]:
                ui.label(one["detail"]).classes("manager-help manager-job-detail")
            if one["outcome"] == "running":
                bar = ui.linear_progress(value=one["pct"] / 100, show_value=False).props("rounded")
                if not one["pct"]:
                    bar.props("indeterminate")
        ui.label(when.ago(datetime.fromtimestamp(one["started"], UTC).isoformat())) \
            .classes("manager-need-count shrink-0")
    row.tooltip(t("manager.sections.job_open", outcome=t(f"manager.jobs.outcome.{one['outcome']}")))


def _recent_jobs(library: Library, state: dict[str, Any]) -> None:
    ui.label(t("manager.sections.recent_jobs")).classes("manager-group")

    def signature() -> tuple[Any, ...]:
        shown = _latest_jobs(library)
        return (int(time.time() // 60), None if shown is None else tuple(
            (one["id"], one["outcome"], one["detail"], int(one["pct"])) for one in shown))

    held = {"signature": signature()}

    @ui.refreshable
    def card() -> None:
        shown = _latest_jobs(library)
        if shown is None:
            ui.label(t("manager.sections.jobs_unread")).classes("manager-help px-2")
        elif not shown:
            ui.label(t("manager.sections.no_jobs")).classes("manager-help px-2")
        for one in shown or []:
            _job_line(one, state)
        if shown:
            with ui.row().classes("justify-end w-full"):
                panel.action(t("manager.sections.all_jobs"),
                             lambda: state["open_on"](needs.Opens(
                                 "jobs", keys={"jobs_find": "", "jobs_outcome": ""})),
                             inline=True, icon=verbs.GO,
                             hint=t("manager.sections.all_jobs.help"))()

    def tick() -> None:
        if (now := signature()) != held["signature"]:
            held["signature"] = now
            card.refresh()

    with ui.element("div").classes("manager-card manager-needs w-full"):
        card()
    ui.timer(front.POLL_S, tick)


_ASIDE_OPEN = "aside_open"


def _prune(library: Library, record: Mapping[str, list[str]]) -> None:
    """Write the dismissals back without the ids no row counts any more. Skipped where the
    record could not be read, which says nothing about what is in it."""
    if not library.dismissed_was_read():
        return
    changed = [(row, list(record.get(row, ()))) for row in list(library.dismissed())
               if list(record.get(row, ())) != library.dismissed()[row]]
    if not changed:
        return

    async def write() -> None:
        for row, ids in changed:
            try:
                await run.io_bound(library.set_dismissed, row, ids)
            except Exception:  # noqa: BLE001 - kept as it was; the next visit tries again
                return

    ui.timer(0.05, write, once=True)


def overview(library: Library, registry: list[dict], discovery: dict,
             go: Callable[[str], None], state: dict[str, Any]) -> None:
    arranged = needs.arrange(needs.collect(library, state), library.dismissed(),
                             library.stopped(), library.watched().get("unread") or (),
                             needs.blind(library), source_filled(library))
    _prune(library, arranged.record)

    def placed(game_ids: tuple[str, ...]) -> None:
        for game_id in game_ids:
            library.forget_media(game_id)
        if state.get("view") == "overview":
            go("overview")

    def fix(need: needs.Need, only: tuple[str, ...] | None = None) -> Any:
        if need.fix is None:
            return None
        if need.fix.key == needs.GET_MEDIA:
            ids = need.ids if only is None else only
            return media_fill.confirm_kind(list(ids), need.fix.kind, state,
                                           lambda: placed(ids))
        if need.fix.key == needs.UPGRADE:
            return _metadata_action(library, state, drawn_again)("upgrade")
        from manager.devices import devices

        return devices.confirm_update(library, _local_name(registry, discovery),
                                      library.watched().get("update") or {})

    def drawn_again() -> None:
        if state.get("view") == "overview":
            go("overview")

    acts = set_aside.Acts(library, drawn_again)

    def waiting() -> tuple[str, ...]:
        return tuple(one.id for one in approvals.instance().waiting())

    asking = {"seen": waiting()}

    def an_ask_came_or_went() -> None:
        if (now := waiting()) != asking["seen"]:
            asking["seen"] = now
            library.forget_watched()
            drawn_again()

    ui.timer(front.POLL_S, an_ask_came_or_went)

    # The container the two columns answer to: this page's own width, not the window's,
    # which the workbench pane shares.
    with ui.element("div").classes("manager-overview"):
        front.top(library, state, go)
        front.strip(library, go)
        with ui.element("div").classes("manager-overview-columns"):
            with ui.element("div").classes("manager-overview-needs"):
                _needs_you(arranged, state, fix, acts)
            with ui.element("div").classes("manager-overview-library"):
                _library(library, state)
            with ui.element("div").classes("manager-overview-jobs"):
                _recent_jobs(library, state)


# --- The library's own metadata ---------------------------------------------------
#
# The upgrade rewrites a file in every game folder, so it asks first and says what it
# costs. It keeps a copy of each, and the Backups page puts them back.


_ASKS = {
    "upgrade": (t("manager.sections.upgrade_confirm"),
                t("manager.sections.backup_note"),
                t("word.upgrade"), verbs.ACCEPT, False),
}


_POLL_S = 1.0
_POLLS = 3600
_LEFT = "metadata_left"


def _metadata_action(library: Library, state: dict[str, Any],
                     then: Callable[[], None]) -> Callable[[str], Any]:
    """Ask, start the job, say it is under way, and say what came of it once it ends."""
    from manager.kit import confirm
    from manager.kit.api import ApiClient

    @on_page
    async def start(which: str) -> None:
        title, detail, word, icon, danger = _ASKS[which]
        if not await confirm.ask(title, detail=detail, confirm=word, icon=icon, danger=danger):
            return
        client = ApiClient()
        try:
            job = await offload.io(client.upgrade_info)
        except Exception as exc:
            ui.notify(t("said.start_failed"), caption=why(exc), type="negative")
            return
        ui.notify(t("manager.sections.way", word=(word)), type="positive")
        watch = state.get("watch_jobs")
        if callable(watch):
            watch()
        found = await _ended(client, str(job.get("id") or ""))
        if found is None:
            return
        # The counts the rows are drawn from are now stale. Asked again off the loop,
        # for the same reason they were read there in the first place.
        await run.io_bound(library.read_metadata_state)
        left: list[tuple[str, str]] = []
        if found.get("state") == "failed":
            ui.notify(t("manager.sections.upgrade_failed"),
                      caption=str(found.get("error") or ""), type="negative")
        else:
            said, left = upgrade_outcome(found.get("result") or {}, library.metadata_state())
            ui.notify(said, type="warning" if left else "positive")
        state.setdefault(_LEFT, {})[which] = left
        then()

    return start


async def _ended(client: Any, job_id: str) -> dict[str, Any] | None:
    """The job once it is no longer running, or None if it could not be asked."""
    for _ in range(_POLLS):
        await asyncio.sleep(_POLL_S)
        try:
            found = await offload.io(client.job, job_id)
        except Exception:  # noqa: BLE001 - the footer line still reports it
            return None
        if found.get("state") != "running":
            return found
    return None


def _failures(result: dict[str, Any]) -> list[tuple[str, str]]:
    return [(str(one[0]), str(one[1])) for one in result.get("failures") or ()]


def upgrade_outcome(result: dict[str, Any],
                    metadata: dict[str, Any]) -> tuple[str, list[tuple[str, str]]]:
    """What a finished upgrade says, and each folder it left that is still waiting for
    one, with why."""
    waiting = set(metadata.get("pending_games") or ())
    left = [one for one in _failures(result) if one[0] in waiting]
    done = int(result.get("upgraded") or 0)
    if left and done:
        return t("manager.sections.upgraded_some", done=done, total=done + len(left)), left
    if left:
        return t("manager.sections.upgrade_count_failed", count=len(left)), left
    return t("manager.sections.upgraded", count=done), left


def _folder_lines(rows: list[tuple[str, str]], most: int = 4) -> list[tuple[str, str]]:
    lines = rows[:most]
    if len(rows) > most:
        lines.append((t("said.and_more", count=len(rows) - most), ""))
    return lines


_FOLDERS_MOST = 200


def _folder_rows(metadata: Mapping[str, Any], left: Mapping[str, Any] | None,
                 which: str) -> list[tuple[str, str]]:
    """The folders behind one Needs You row, each with why where it is known, by name.
    `left` is each folder the last Upgrade could not write, with why."""
    if which == "older_format":
        said = dict((left or {}).get("upgrade") or ())
        found = [(str(name), str(said.get(str(name)) or ""))
                 for name in metadata.get("pending_games") or ()]
    elif which in ("unreadable", "unwritten"):
        found = [(str(row.get("folder") or ""), str(row.get("error") or ""))
                 for row in metadata.get(which) or ()]
    else:
        found = []
    return sorted(found, key=lambda row: row[0].lower())


async def metadata_folders(library: Library, state: dict[str, Any], which: str,
                           redraw: Callable[[], None]) -> None:
    """The folders one Needs You row counts, over the page, each with why on screen where
    it is known. A list that has emptied since its row was drawn opens nothing and calls
    `redraw`."""
    rows = _folder_rows(library.metadata_state(), state.get(_LEFT), which)
    if not rows:
        redraw()
        return
    count = len(rows)
    title, intro = {
        "unreadable": (t("manager.needs.unreadable_title"),
                       t("manager.sections.unreadable_one") if count == 1
                       else t("manager.sections.unreadable_many", count=count)),
        "unwritten": (t("manager.needs.unwritten"),
                      t("manager.sections.could_not_write", count=count)),
        "older_format": (t("manager.needs.older_format"),
                         t("manager.sections.written_older", count=count)),
    }[which]

    async def upgrade() -> None:
        box.close()
        await _metadata_action(library, state, redraw)("upgrade")

    with frame.opened(title) as box:
        ui.label(intro).classes("manager-help px-3")
        for name, why in _folder_lines(rows, _FOLDERS_MOST):
            with ui.column().classes("gap-0 w-full px-3 py-1"):
                ui.label(name).classes("manager-setting")
                if why:
                    ui.label(why).classes("manager-help")
        with frame.footer():
            frame.cancel(box.close, t("word.close"))
            if which == "older_format":
                frame.answer(t("word.upgrade"), upgrade, icon=verbs.ACCEPT)
    box.open()


def _line_said(state: Mapping[str, Any], stopped: Collection[str]) -> str:
    """What the Games page says about the library's metadata, or "" for nothing. Counts
    only what Overview lists: a check in `stopped` is left out, and the folders that could
    not be written are not a check."""
    missing = 0 if "unreadable" in stopped else len(state.get("unreadable") or ())
    pending = 0 if "older_format" in stopped else int(state.get("pending_upgrade") or 0)
    unwritten = len(state.get("unwritten") or ())
    return " · ".join(said for said in (
        t("manager.sections.unlisted", count=missing) if missing else "",
        t("manager.sections.older_format", count=pending) if pending else "",
        t("manager.sections.not_written", count=unwritten) if unwritten else "") if said)


def metadata_line(library: Library, state: dict[str, Any]) -> None:
    """The line above the Games grid, drawn only while `_line_said` has something to
    say, with Overview behind its button."""
    async def show() -> None:
        await state["open_on"](needs.Opens("overview"))

    with ui.element("div").classes("w-full shrink-0"):
        said = _line_said(library.metadata_state(), library.stopped())
        if not said:
            return
        with ui.element("div").classes("manager-attention mb-2"):
            ui.icon("error_outline").classes("manager-attention-icon")
            ui.label(said).classes("manager-attention-line grow")
            panel.action(t("manager.sections.show"), show, icon=verbs.GO,
                         hint=t("manager.sections.show_needs.help"))()


def no_tables_line(library: Library, state: dict[str, Any]) -> None:
    """The notice above the Tables grid while some game has no table, which the grid
    cannot show: it lists files, and these have none. Drawn once; the page redraws it."""
    count = sum(1 for game in library.games if game.get("table_count") == 0)
    if not count or "no_tables" in library.hidden_checks():
        return

    def show() -> Any:
        return state["open_on"](needs.no_tables_opens())

    with ui.element("div").classes("w-full shrink-0"), \
            ui.element("div").classes("manager-attention mb-2"):
        ui.icon(verbs.NO_TABLE).classes("manager-attention-icon")
        ui.label(t("manager.sections.no_tables_line", count=count)) \
            .classes("manager-attention-line grow")
        panel.action(t("manager.sections.show"), show, icon=verbs.GO,
                     hint=t("manager.sections.show_no_tables.help"))()
