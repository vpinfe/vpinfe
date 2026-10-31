"""What's new: the first start after an update, and the same page asked for again from About.

It runs on the wizard's dialog host (`manager/kit/wizard.py`), as Setup does, with three parts
in the order a person asks about them: what changed, what it did to their files, and what
needs them. A part with nothing in it is not a step, and where none has anything the page
does not open.

Reading it is the whole answer - nothing here is asked - so however it is left, the
update is read.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from nicegui import run, ui

from common.i18n import literal_or, t
from common.release import release_notes, whats_new
from common.vpinfe_version import get_version
from manager.kit import offload, panel, verbs, wizard
from manager.kit.data import Library
from manager.kit.on_page import on_page
from manager.overview import needs as overview_needs
from manager.system import early_access

CHANGED = "changed"
FILES = "files"
NEEDS = "needs"

# The section the notes lead with, drawn as the sentence under a version rather than as
# a heading of its own.
LEAD_SECTION = "Summary"


@dataclass(frozen=True)
class Part:
    key: str
    label: str
    title: str
    draw: Callable[[], None]
    notes: tuple[str, ...] = ()


def _config() -> Any:
    from common.config.paths import get_ini_config

    return get_ini_config()


def _wrapped(text: str) -> Callable[[], None]:
    def draw() -> None:
        ui.label(text).classes("manager-fact-value min-w-0")
    return draw


@dataclass(frozen=True)
class Line:
    """A thing an update leaves for a person to do about their own files, counted now
    rather than declared with the release, and gone once the count is."""

    label: str
    text: str
    actions: tuple[tuple[str, overview_needs.Opens], ...]


def lines(found: overview_needs.Unplayable) -> list[Line]:
    """One line for each kind of file that cannot be used as it is, with the ways to fix
    it. None offered is presumed: the sentence says either fixes it."""
    out: list[Line] = []
    if found.wont_play:
        out.append(Line(
            t("manager.needs.wont_play"),
            t("manager.whats_new.wont_play", count=len(found.wont_play),
              devices=", ".join(found.devices)),
            (*(((t("manager.whats_new.browser_settings"),
                 overview_needs.Opens("settings", keys={"settings_page": "frontend.chromium"})),)
               if found.here else ()),
             (t("manager.whats_new.convert_videos"), overview_needs.wont_play_opens()))))
    if found.unsupported:
        out.append(Line(
            t("manager.needs.unsupported"),
            t("manager.whats_new.unsupported", count=len(found.unsupported)),
            ((t("manager.media_convert.convert"), overview_needs.unsupported_opens()),)))
    return out


def _release(found: release_notes.Release) -> list[tuple[Any, Any]]:
    entries: list[tuple[Any, Any]] = [(panel.HEADING, found.version)]
    for section in found.sections:
        if section.heading != LEAD_SECTION:
            entries.append((panel.HEADING, section.heading))
        for entry in section.entries:
            if entry.area:
                entries.append((entry.area, _wrapped(entry.text)))
            else:
                entries.append((panel.FULL, _wrapped(entry.text)))
    return entries


def _changed(found: whats_new.Review) -> Callable[[], None]:
    def draw() -> None:
        entries: list[tuple[Any, Any]] = []
        for release in found.releases:
            entries += _release(release)
        panel.facts(ui, entries)
    return draw


def _file_row(entry: Any) -> tuple[str, Callable[[], None]]:
    label, _ = literal_or("", f"manager.whats_new.file.{entry.name}.label", fallback=entry.name)
    _, key = literal_or("", f"manager.whats_new.file.{entry.name}.done")
    return label, _wrapped(t(key, count=entry.count) if key else "")


def _files(found: whats_new.Review) -> Callable[[], None]:
    def draw() -> None:
        panel.facts(ui, [_file_row(one) for one in found.files])
    return draw


def _dialog_around() -> ui.dialog | None:
    element: Any = ui.context.slot.parent
    while element is not None and not isinstance(element, ui.dialog):
        slot = element.parent_slot
        element = slot.parent if slot is not None else None
    return element


def _line_value(one: Line, go: Callable[[overview_needs.Opens], None]) -> Callable[[], None]:
    def draw() -> None:
        ui.label(one.text).classes("manager-fact-value min-w-0")
        dialog = _dialog_around()
        with ui.row().classes("items-center gap-2"):
            for label, opens in one.actions:
                def leave(opens: overview_needs.Opens = opens) -> None:
                    go(opens)
                    if dialog is not None:
                        dialog.submit(True)

                panel.action(label, leave, icon=verbs.GO,
                             hint=t("manager.whats_new.go_hint", label=label))()
    return draw


def _needs(found: whats_new.Review, asked: list[Line],
           go: Callable[[overview_needs.Opens], None]) -> Callable[[], None]:
    def draw() -> None:
        panel.facts(ui, [(t(f"needs.{one.key}.label"), _wrapped(t(f"needs.{one.key}.help")))
                         for one in found.needs]
                    + [(one.label, _line_value(one, go)) for one in asked])
    return draw


def parts(found: whats_new.Review, asked: list[Line] | None = None,
          go: Callable[[overview_needs.Opens], None] = lambda opens: None) -> list[Part]:
    """The parts that have something to say, in the order they are read."""
    asked = asked or []
    def part(key: str, draw: Callable[[], None], notes: tuple[str, ...] = ()) -> Part:
        return Part(key, f"manager.whats_new.step.{key}", f"manager.whats_new.title.{key}",
                    draw, notes)

    shown = []
    if found.releases:
        shown.append(part(CHANGED, _changed(found)))
    if found.files:
        shown.append(part(FILES, _files(found), (t("manager.whats_new.files_undo"),)))
    if found.needs or asked:
        shown.append(part(NEEDS, _needs(found, asked, go)))
    return shown


def _row(shown: list[Part], here: Part) -> list[dict[str, Any]]:
    before = True
    row = []
    for one in shown:
        row.append({"key": one.key, "label": t(one.label), "done": before and one is not here,
                    "holds": [one.key]})
        before = before and one is not here
    return row


def _step(found: whats_new.Review, shown: list[Part], part: Part,
          offer: Callable[[], None] | None = None) -> dict[str, Any]:
    step: dict[str, Any] = {
        "step": part.key,
        "title": t(part.title),
        "draw": lambda values: part.draw(),
        "notes": list(part.notes),
    }
    if part is shown[0] and found.since:
        step["help"] = t("manager.whats_new.since", version=found.since)
    if len(shown) > 1:
        step["steps"] = _row(shown, part)
    if part is shown[-1]:
        step["confirm"] = t("word.done")
        if offer is not None:
            step["after"] = lambda values: offer()
    return step


def calls(found: whats_new.Review, shown: list[Part],
          offer: Callable[[], None] | None = None) -> wizard.Calls:
    """The wizard's four calls over the parts. Reading is the only thing a step asks, so
    Next is a step forward and the last one's answer is the end."""
    def part(key: str | None) -> Part:
        return next((one for one in shown if one.key == key), shown[0])

    def after(key: str) -> Part:
        index = shown.index(part(key))
        return shown[min(index + 1, len(shown) - 1)]

    async def first(key: str | None) -> dict:
        return _step(found, shown, part(key), offer)

    async def check(values: dict[str, Any], key: str) -> dict:
        return _step(found, shown, after(key), offer)

    async def act(key: str, values: dict[str, Any], step: str) -> dict:
        return _step(found, shown, part(step), offer)

    async def finish(values: dict[str, Any], key: str) -> dict:
        return {"ok": True}

    async def job(job_id: str) -> dict:
        return {"state": "done"}

    return wizard.Calls(first, check, act, finish, job)


@on_page
async def open_whats_new(library: Library, state: dict[str, Any], *, again: bool = False) -> bool:
    """The review over the page this is called from, until it is closed. `again` is the
    running version alone, from About; otherwise it is everything since the last read,
    and closing it, however it is left, says it has been read. Returns whether a dialog
    opened."""
    found = await offload.io(whats_new.review, _config(), get_version(), again=again)
    asked = lines(overview_needs.unplayable(
        await offload.io(library.read_media_watch),
        str(library.discovery().get("install_id") or ""),
        await offload.io(library.refresh_dismissed), library.stopped()))
    chosen: list[overview_needs.Opens] = []
    shown = parts(found, asked, chosen.append)
    offer = await early_access.offer(get_version(), again=again) if shown else None
    if again and not shown:
        ui.notify(t("manager.whats_new.nothing"))
    try:
        if shown:
            await wizard.open_dialog(
                label=t("manager.whats_new.title"), calls=calls(found, shown, offer),
                under="whats_new", aside=t("word.close"), close_when_done=True)
    finally:
        if not again:
            await run.io_bound(whats_new.mark_seen)
    if chosen:
        await state["open_on"](chosen[-1])
    return bool(shown)
