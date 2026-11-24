
"""What this machine is doing, and what it has been doing this session.

The live half. A machine's host, OS and browser are facts rather than readings and they
belong to the device that reports them - the Devices workbench answers *is that machine
healthy*, and this answers *what is this one doing, and what has it been doing*.

Reading is what fills the history, so leaving this page open is what builds the record.
Nothing samples in the background: a machine nobody is watching does not need a log of
having been idle.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from nicegui import ui

from common import i18n
from common.failures import why
from common.host import tools
from common.i18n import t
from manager.kit import busy, offload, panel, when
from manager.kit.data import Library
from manager.settings import settings as settings_page
from manager.system import jobs_list

logger = logging.getLogger("vpinfe.manager.system.metrics")

# The cadence the job line already uses, so a page with both on it ticks once rather
# than twice out of step.
EVERY_SECONDS = 2.0

# How far back the graphs reach. Ten minutes answers "did that scan do this?" without
# turning a sparkline into a smear.
WINDOW_SECONDS = 600
# The newest marks a graph carries; a bulk run starts hundreds of jobs inside the window.
MARKS_SHOWN = 24

# Where a reading stops being ordinary. Two thresholds rather than a gradient: a color
# that changes continuously says something is changing, which is not the question.
WARN, BAD = 75.0, 90.0


def build(library: Library, state: dict[str, Any], redraw: Callable[[], None]) -> None:
    """Drawn once, then updated in place: only the values move on a tick."""
    held: dict[str, Any] = {}

    with ui.column().classes("w-full gap-3") as readings_page:
        note = ui.element("div")
        note.set_visibility(False)
        with ui.element("div").classes("manager-tiles"):
            cpu = _tile(t("manager.metrics.processor"))
            memory = _tile(t("manager.metrics.memory"))
            cards = ui.element("div").classes("manager-tiles-group")
            programs = _programs_tile()
        disks_title = ui.label(t("manager.metrics.free_space")).classes("manager-group mt-2")
        disks = ui.element("div").classes("manager-tiles")

    def show(said: str, detail: str = "", *, keep_cards: bool = False) -> None:
        if (said, detail) != held.get("said"):
            held["said"] = (said, detail)
            note.clear()
            with note:
                panel.line(said, hint=detail)
        note.set_visibility(bool(said))
        for element in (cpu["root"], memory["root"], programs["root"], disks_title, disks):
            element.set_visibility(not said)
        cards.set_visibility(not said or keep_cards)

    async def tick() -> None:
        try:
            found = await offload.io(library.metrics, WINDOW_SECONDS)
        except Exception as exc:  # noqa: BLE001 - this page says why, never 500s
            show(t("manager.metrics.read_failed"), why(exc))
            return
        now = found.get("now") or {}
        history = found.get("history") or []
        marks = found.get("marks") or []
        if not now.get("measurable"):
            # Offered with the reason rather than hidden: a page that simply omits the
            # readings leaves somebody unsure whether the machine is fine or this is.
            show(str(now.get("reason") or t("manager.metrics.no_readings")), keep_cards=True)
            _draw_cards(cards, now.get("graphics") or {}, history, held, marks)
            return
        show("")
        _fill(cpu, now.get("cpu_percent"), _series(history, "cpu_percent"),
              _cpu_said(now.get("cpu_temperature"), now.get("load")), marks=marks)
        _fill(memory, now.get("memory_percent"), _series(history, "memory_percent"),
              _bytes_said(now.get("memory_used"), now.get("memory_total")), marks=marks)
        _draw_disks(disks, now.get("disks") or [], held)
        _draw_cards(cards, now.get("graphics") or {}, history, held, marks)
        _draw_programs(programs, now.get("busiest"), held)

    busy.fill(readings_page, tick)
    ui.timer(EVERY_SECONDS, tick, immediate=False)


def _tile(title: str, name: str = "", unit: str = "") -> dict[str, Any]:
    """The frame for one reading, kept so the tick can move the number rather than
    build the tile again. A `name` sits under the title, a `unit` beside the number."""
    with ui.element("div").classes("manager-card min-w-0") as root:
        heading = ui.label(title).classes("manager-card-title truncate")
        named = ui.label(name).classes("text-sm truncate") if name else None
        with ui.row().classes("items-baseline gap-2 no-wrap"):
            value = ui.label("-").classes("manager-kpi")
            if unit:
                ui.label(unit).classes("text-xs opacity-60")
        spark = ui.element("div").classes("manager-spark w-full").style("height:28px")
        first = ui.label("").classes("text-xs opacity-60 truncate")
        second = ui.label("").classes("text-xs opacity-60 truncate")
    return {"root": root, "title": heading, "name": named or heading, "value": value,
            "spark": spark, "first": first, "second": second}


def _programs_tile() -> dict[str, Any]:
    """What's busy: the programs using the most of the processor, a row each."""
    with ui.element("div").classes("manager-card min-w-0") as root:
        ui.label(t("manager.metrics.busiest")).classes("manager-card-title truncate")
        rows = ui.element("div").classes("manager-tile-rows")
    return {"root": root, "rows": rows}


def _draw_programs(tile: dict[str, Any], programs: list[dict[str, Any]] | None,
                   held: dict[str, Any]) -> None:
    """Redrawn only when the list is not the one already shown. None is a list not yet
    measured: a process's first reading is 0, so the first sample does not give one."""
    if "programs" in held and held["programs"] == programs:
        return
    held["programs"] = programs
    tile["rows"].clear()
    with tile["rows"]:
        if programs is None:
            ui.label(t("manager.metrics.collecting")).classes("text-xs opacity-40")
            return
        for one in programs:
            ui.label(str(one.get("name") or "")).classes("text-sm truncate")
            ui.label(t("manager.metrics.share", value=one.get("cpu_percent") or 0)) \
                .classes("text-sm")
            ui.label(i18n.size(one.get("memory") or 0)).classes("text-xs opacity-60")


def _series(history: list[dict[str, Any]], field: str) -> list[tuple[float, Any]]:
    return [(one.get("at") or 0.0, one.get(field)) for one in history]


def _fill(tile: dict[str, Any], value: Any, series: list[tuple[float, Any]], first: str = "",
          second: str = "", marks: list[dict[str, Any]] | None = None) -> None:
    tile["value"].text = "-" if value is None else f"{value:.0f}%"
    tile["value"].style(f"color: {_tone(value)}")
    tile["first"].text = first
    tile["second"].text = second
    _spark(tile["spark"], [one for one in series if one[1] is not None], marks or [])


def _note_tile(title: str, reason: str, detail: str = "", name: str = "",
               tool: str = "") -> dict[str, Any]:
    """A tile with words where the numbers would be: a card nothing reads, or no card
    at all. The reason is the content, so it wraps rather than truncating. `tool` is the
    Tool whose Settings row would fix it, linked beneath."""
    with ui.element("div").classes("manager-card min-w-0") as root:
        heading = ui.label(title).classes("manager-card-title truncate")
        named = ui.label(name).classes("text-sm truncate") if name else None
        ui.label("-").classes("manager-kpi").style("color: var(--ink-3)")
        panel.line(reason, hint=detail)
        if (missing := next((one for one in tools.TOOLS if one.id == tool), None)) is not None:
            panel.link(tools.hint(missing), to=settings_page.address_for(settings_page.TOOLS))()
    return {"root": root, "title": heading, "name": named or heading}


def _draw_cards(target: Any, graphics: dict[str, Any], history: list[dict[str, Any]],
                held: dict[str, Any], marks: list[dict[str, Any]] | None = None) -> None:
    """One tile per card rather than averaged: two cards averaged is a number that
    describes neither, and a second card is why somebody opened this. The tiles are
    drawn again only when the cards are not the same ones; otherwise the numbers move."""
    cards = graphics.get("cards") or []
    shape = (graphics.get("reason"), graphics.get("detail"),
             tuple((one.get("id"), one.get("name"), one.get("source"), one.get("reason"),
                    one.get("detail"), one.get("tool")) for one in cards))
    if shape != held.get("shape"):
        held.pop("shape", None)
        held["tiles"] = {}
        target.clear()
        with target:
            if not cards:
                # "No card" and "the program that reads one is missing" are different
                # answers, and only the second can be acted on.
                _note_tile(t("manager.metrics.graphics"),
                           str(graphics.get("reason") or t("manager.metrics.no_readings")),
                           str(graphics.get("detail") or ""))
            for card in cards:
                name = str(card.get("name") or t("word.unknown"))
                if card.get("reason"):
                    tile = _note_tile(t("manager.metrics.graphics"), str(card["reason"]),
                                      str(card.get("detail") or ""), name,
                                      str(card.get("tool") or ""))
                else:
                    tile = _tile(t("manager.metrics.graphics"), name, t("manager.metrics.busy"))
                    held["tiles"][card.get("id")] = tile
                tile["name"].tooltip(_card_tip(card, name))
        held["shape"] = shape
    for card in cards:
        tile = held["tiles"].get(card.get("id"))
        if tile is None:
            continue
        series = [(one.get("at") or 0.0, (one.get("gpus") or {}).get(card.get("id")))
                  for one in history]
        first, second = _card_lines(card)
        _fill(tile, card.get("busy"), series, first, second, marks)


def _card_tip(card: dict[str, Any], name: str) -> str:
    """The whole name, which a tile may cut, and what read it."""
    source = card.get("source")
    if not source:
        return name
    named = t(f"manager.metrics.source.{str(source).replace('-', '_')}")
    return "\n".join([name, t("manager.metrics.card.source", source=named)])


def _card_lines(card: dict[str, Any]) -> tuple[str, str]:
    """What a card has beyond how busy it is, on two lines: what it holds, then what it
    draws. A reading the source lacks is not listed."""
    holds: list[str] = []
    used, total = card.get("memory_used"), card.get("memory_total")
    if used is not None and total is not None:
        holds.append(t("manager.metrics.card.memory",
                       value=t("manager.metrics.usage", used=i18n.size(used),
                               total=i18n.size(total))))
    elif used is not None:
        holds.append(t("manager.metrics.card.shared_memory", used=i18n.size(used)))
    elif card.get("memory_percent") is not None:
        holds.append(t("manager.metrics.card.memory",
                       value=t("metrics.gpu.value.percent", value=card["memory_percent"])))
    if card.get("temperature") is not None:
        holds.append(t("metrics.gpu.value.celsius", value=card["temperature"]))
    draws: list[str] = []
    if card.get("power") is not None:
        draws.append(t("metrics.gpu.value.watts", value=card["power"]))
    if card.get("clock") is not None:
        draws.append(t("metrics.gpu.value.megahertz", value=card["clock"]))
    if card.get("fan_percent") is not None:
        draws.append(t("manager.metrics.card.fan",
                       value=t("metrics.gpu.value.percent", value=card["fan_percent"])))
    elif card.get("fan_rpm") is not None:
        draws.append(t("manager.metrics.card.fan",
                       value=t("metrics.gpu.value.rpm", value=card["fan_rpm"])))
    return ", ".join(holds), ", ".join(draws)


def _draw_disks(target: Any, disks: list[dict[str, Any]], held: dict[str, Any]) -> None:
    """One tile per volume. The tiles are drawn again only when the volumes are not the
    same ones; a reading that moved is redrawn inside its tile, so a tooltip held open
    over a title is not torn down by the next tick."""
    several = sum(1 for disk in disks for one in disk.get("holds") or []
                  if one.get("role") == "library") > 1
    shape = (several, tuple((disk.get("path"), tuple(disk.get("also") or []),
                             repr(disk.get("holds"))) for disk in disks))
    if shape != held.get("disk_shape"):
        held.pop("disk_shape", None)
        held["disk_readings"] = []
        target.clear()
        with target:
            for disk in disks:
                held["disk_readings"].append(_disk_tile(disk, several))
        held["disk_shape"] = shape
        held.pop("disk_read", None)
    if disks != held.get("disk_read"):
        held["disk_read"] = disks
        for disk, reading in zip(disks, held["disk_readings"], strict=True):
            _disk_reading(reading, disk)


def _volume_title(disk: dict[str, Any], several_libraries: bool) -> str:
    """What this volume is for, in the person's words. The path is the fallback and the
    tooltip: a path says where, and the question is which of the things they keep."""
    holds = disk.get("holds") or []
    libraries = [one for one in holds if one.get("role") == "library"]
    words = []
    if libraries:
        # Named by its folder only where there are several and this volume holds one:
        # two volumes both titled "Library" cannot be told apart.
        if several_libraries and len(libraries) == 1:
            words.append(t("manager.metrics.disk.library_named", name=libraries[0].get("name")))
        else:
            words.append(t("manager.metrics.disk.library"))
    if any(one.get("role") == "config" for one in holds):
        words.append(t("manager.metrics.disk.config"))
    if any(one.get("role") == "cache" for one in holds):
        words.append(t("manager.metrics.disk.cache"))
    return ", ".join(words) or str(disk.get("path") or "")


def _disk_tile(disk: dict[str, Any], several_libraries: bool) -> ui.element:
    """The tile for one volume, returning the part that holds its reading."""
    with ui.element("div").classes("manager-card min-w-0"):
        title = ui.label(_volume_title(disk, several_libraries)) \
            .classes("manager-card-title truncate")
        # Every path on the volume, so somebody looking for one finds it rather than
        # concluding it is not watched.
        title.tooltip("\n".join([str(disk.get("path") or ""), *(disk.get("also") or [])]))
        return ui.element("div").classes("w-full")


def _disk_reading(target: ui.element, disk: dict[str, Any]) -> None:
    target.clear()
    with target:
        if disk.get("error"):
            # A share that has gone away is exactly what this reports, and saying
            # nothing would show it as a path with no numbers.
            said = t("manager.metrics.cannot_read")
            ui.label(said).classes("text-xs").style(f"color: {_tone(100)}")
            if str(disk["error"]).casefold() != said.casefold():
                ui.label(str(disk["error"])).classes("manager-help")
            return
        with ui.element("div").classes("w-full mt-2"):
            _bar((disk.get("percent") or 0) / 100, _tone(disk.get("percent")))
        # The bar fills as the disk fills, so the words say full too. A bar reading
        # "used" beside a number reading "free" is two directions in one row.
        free = disk.get("free")
        ui.label(t("manager.metrics.full_free", value=(disk.get("percent") or 0),
                   free="-" if free is None else i18n.size(free))) \
            .classes("text-xs opacity-60 truncate mt-1")


def _spark(target: Any, series: list[tuple[float, float]],
           marks: list[dict[str, Any]] | None = None) -> None:
    """The shape of the last few minutes, drawn as bars rather than a line.

    A number says where it is; this says whether it got there. Nothing at all until
    there are two points - one bar is a shape that implies a trend it cannot have. A mark
    is a line through the bars at the moment something happened, so a climb can be read
    against what was started.

    The mark elements are kept from one draw to the next and moved in place: a tooltip
    is parented to its mark, and one rebuilt every tick closes under the pointer.
    """
    held: dict[tuple[float, str, str], Any] = target.__dict__.setdefault("_marks_held", {})
    if "_bars" not in target.__dict__:
        with target:
            target.__dict__["_bars"] = ui.row().classes("items-end gap-px w-full no-wrap h-full")
    bars = target.__dict__["_bars"]
    bars.clear()
    if len(series) < 2:
        _keep_marks(target, held, {})
        with bars:
            ui.label(t("manager.metrics.collecting")).classes("text-xs opacity-40")
        return
    # Every other point at most, so a ten-minute window is a readable width rather than
    # three hundred hairlines.
    step = max(1, len(series) // 60)
    shown = series[::step][-60:]
    with bars:
        for _, one in shown:
            ui.element("div").style(
                f"flex:1 1 0; min-width:1px; height:{max(2, min(100, one)):.0f}%;"
                f" background:{_tone(one)}; opacity:0.55; border-radius:1px")
    times = [at for at, _ in shown]
    placed: dict[tuple[float, str, str], tuple[str, dict[str, Any]]] = {}
    for mark in (marks or [])[-MARKS_SHOWN:]:
        happened = float(mark.get("at") or 0)
        if happened < times[0]:
            continue
        before = sum(1 for at in times if at < happened)
        key = (happened, str(mark.get("kind")), str(mark.get("name")))
        placed[key] = (f"left:{100 * before / len(times):.2f}%;", mark)
    _keep_marks(target, held, placed)


def _keep_marks(target: Any, held: dict[tuple[float, str, str], Any],
                placed: dict[tuple[float, str, str], tuple[str, dict[str, Any]]]) -> None:
    """Move the marks already drawn, add the new ones, drop those that left the window."""
    for key in [one for one in held if one not in placed]:
        target.remove(held.pop(key))
    for key, (style, mark) in placed.items():
        if key in held:
            held[key].style(style)
            continue
        with target:
            held[key] = ui.element("div").classes("manager-spark-mark").style(style)
            held[key].tooltip(_mark_said(mark))


def _mark_said(mark: dict[str, Any]) -> str:
    """What happened and when: a job by what it is, a table by its game."""
    at = when.from_epoch(float(mark.get("at") or 0))
    if mark.get("kind") == "job":
        what = str(mark.get("title") or "") or jobs_list.what_ran(str(mark.get("name") or ""))
    elif mark.get("name"):
        what = t("manager.metrics.mark.launched", name=mark["name"])
    else:
        what = t("manager.metrics.mark.unnamed")
    return t("manager.metrics.mark_at", what=what, time=when.clock(at))


def _bar(fraction: float, color: str) -> None:
    with ui.element("div").classes("manager-bar w-full"):
        ui.element("div").style(
            f"width:{max(0.0, min(1.0, fraction)) * 100:.0f}%; background:{color}")


def _tone(value: Any) -> str:
    """The declared tokens, not the obvious names: this tree has `--warm` and `--danger`,
    and `--warning`/`--negative` are not declared - a var() naming one is invisible."""
    if value is None:
        return "var(--ink-3)"
    if value >= BAD:
        return "var(--danger)"
    if value >= WARN:
        return "var(--warm)"
    return "var(--accent)"


def _bytes_said(used: Any, total: Any) -> str:
    if used is None or total is None:
        return ""
    return t("manager.metrics.usage", used=i18n.size(used), total=i18n.size(total))


def _cpu_said(temperature: Any, load: Any) -> str:
    """The processor's detail line: how hot, where this platform says, then the load."""
    heat = [] if temperature is None else [t("metrics.gpu.value.celsius", value=temperature)]
    return ", ".join([*heat, *filter(None, [_load_said(load)])])


def _load_said(load: Any) -> str:
    """Load average, where the platform has one. Windows does not, and that is a number
    it does not have rather than one we failed to read."""
    if not load:
        return ""
    return t("manager.metrics.load", figures=", ".join(f"{one:g}" for one in load))
