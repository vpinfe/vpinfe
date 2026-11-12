"""The panel of a selection: what a person says about the tables or games, read as one
value or Varies, and written to every one of them with Undo."""

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from nicegui import run, ui

from common.failures import why
from common.i18n import t
from manager.frontend import launchers
from manager.kit import dropdown, game_tables, offload, panel, stars, tag_chips, undo
from manager.kit.on_page import on_page
from manager.kit.panel import HEADING
from manager.workbench.app_settings import JOB_POLL_S
from manager.workbench.launcher import _launchers_for_panel

logger = logging.getLogger("vpinfe.manager.workbench.selection_marks")


@dataclass(frozen=True)
class Kind:
    """Whether the selection is of tables or of games: which rows the context holds, what
    they are called, and how a row names itself to the marks job."""

    rows: str
    count: str
    value_count: str
    changed: str
    table: bool

    def edit(self, row: dict[str, Any], marks: dict[str, Any]) -> dict[str, Any]:
        if self.table:
            return {"game_id": str(row.get("game_id") or ""), "table_id": str(row["id"]),
                    **marks}
        return {"game_id": str(row["id"]), **marks}


TABLES = Kind("tables", "manager.games.tables", "manager.workbench.value_count",
              "manager.selection.changed_tables", True)
GAMES = Kind("games", "manager.games.games", "manager.selection.value_count_games",
             "manager.selection.changed_games", False)


def tags_of(row: dict[str, Any]) -> list[str]:
    return [str(tag) for tag in (row.get("user") or {}).get("tags") or []]


def rating_of(row: dict[str, Any]) -> int:
    return int(row.get("rating") or 0)


def favorite_of(row: dict[str, Any]) -> bool:
    return bool((row.get("user") or {}).get("favorite"))


def private_of(row: dict[str, Any]) -> bool:
    return bool(row.get("private"))


def hidden_of(row: dict[str, Any]) -> bool:
    return bool(row.get("hidden"))


def nvram_of(row: dict[str, Any]) -> bool:
    return bool(row.get("delete_nvram_on_close"))


def named_launcher(context: dict[str, Any], row: dict[str, Any]) -> str:
    """The launcher a table chose for itself, or "" where it follows the default."""
    listing = context.get("launchers") or {}
    known = {one["launcher_id"] for one in listing.get("launchers") or []}
    named = str((listing.get("mappings") or {}).get(str(row.get("id") or "")) or "")
    return named if named in known else ""


def shared(rows: Sequence[dict[str, Any]], read: Callable[[dict[str, Any]], Any]) -> Counter:
    return Counter(read(row) for row in rows)


def _marked(context: dict[str, Any], key: str, draw: Callable[[], None]) -> panel.Region:
    """One mark's control, drawn again from the rows as they are after a write."""
    made = panel.region(draw)
    context.setdefault("marks", {})[key] = made
    return made


async def section(context: dict[str, Any], kind: Kind) -> None:
    context["marks"] = {}
    entries: list[tuple[Any, Any]] = [
        (HEADING, t("manager.workbench.yours")),
        (t("manager.workbench.table_rating" if kind.table else "manager.workbench.game_rating"),
         _marked(context, "rating", _stars(context, kind))),
    ]
    if not kind.table:
        entries += [
            (t("word.favorite"),
             _marked(context, "favorite", _switch(context, kind, "favorite", favorite_of))),
            (game_tables.PRIVATE_WORDS[0],
             _marked(context, "private", _switch(context, kind, "private", private_of))),
            panel.note(game_tables.PRIVATE_HELP),
        ]
    entries.append((t("manager.workbench.tags"), _tags(context, kind)))
    if kind.table:
        entries += [
            (HEADING, game_tables.LAUNCH),
            (t("word.hidden"),
             _marked(context, "hidden", _switch(context, kind, "hidden", hidden_of))),
            (t("manager.workbench.launcher"),
             _marked(context, "launcher", _launcher(context, kind))),
            (t("manager.workbench.nvram_clear"),
             _marked(context, "nvram", _switch(context, kind, "delete_nvram_on_close", nvram_of,
                                                hint=t("manager.workbench.nvram_note")))),
        ]
    panel.facts(ui, entries)


def varies_chip(kind: Kind, counts: Counter, said: Callable[[Any], str]) -> Callable[[], None]:
    """Varies, with the rows holding each value counted on hover."""
    lines = [t(kind.value_count, said=said(value), count=count)
             for value, count in counts.most_common()]
    return panel.state(t("manager.workbench.varies"), "off", hint="\n".join(lines))


def _stars(context: dict[str, Any], kind: Kind) -> Callable[[], None]:
    async def rate(value: int) -> None:
        await change(context, kind, "rating", lambda _row: value, rating_of)

    def draw() -> None:
        counts = shared(context[kind.rows], rating_of)
        with ui.row().classes("items-center gap-2 no-wrap"):
            stars.draw(next(iter(counts)) if len(counts) == 1 else 0, rate)()
            if len(counts) > 1:
                varies_chip(kind, counts,
                            lambda value: t("manager.stars.5", n=stars.shown(value)))()

    return draw


def _switch(context: dict[str, Any], kind: Kind, name: str,
            read: Callable[[dict[str, Any]], bool], *, hint: str = "") -> Callable[[], None]:
    async def flip(event: Any) -> None:
        wanted = bool(event.value)
        await change(context, kind, name, lambda _row: wanted, read)

    def draw() -> None:
        counts = shared(context[kind.rows], read)
        with ui.row().classes("items-center gap-2 no-wrap"):
            panel.switch(len(counts) == 1 and next(iter(counts)), flip, hint=hint)()
            if len(counts) > 1:
                varies_chip(kind, counts, lambda value: t("word.on" if value else "word.off"))()

    return draw


def _carried(rows: Sequence[dict[str, Any]]) -> tuple[list[str], list[tuple[str, int]]]:
    """The tags every row carries, and each tag only some carry with how many do."""
    counts = Counter(tag for row in rows for tag in set(tags_of(row)))
    return (sorted(tag for tag, count in counts.items() if count == len(rows)),
            sorted((tag, count) for tag, count in counts.items() if count < len(rows)))


def _tags(context: dict[str, Any], kind: Kind) -> Callable[[], None]:
    """Every tag all carry is a chip to take away; one some carry is drawn quiet with its
    count, and picking it gives it to all.

    The picker stays where it is while the quiet chips are drawn again, so a person adding
    one tag after another keeps the field. After a write it is set to what all carry
    without writing that back."""
    library = context["library"]
    setting = {"picker": False}

    async def retag(chosen: list[str]) -> None:
        everyone, _ = _carried(context[kind.rows])
        added = [tag for tag in chosen if tag not in everyone]
        removed = {tag for tag in everyone if tag not in chosen}
        await change(context, kind, "tags", lambda row: [
            *(tag for tag in tags_of(row) if tag not in removed),
            *(tag for tag in added if tag not in tags_of(row))], tags_of)

    async def drop(tag: str) -> None:
        await change(context, kind, "tags",
                     lambda row: [one for one in tags_of(row) if one != tag], tags_of)

    def quiet() -> None:
        rows = context[kind.rows]
        everyone, some = _carried(rows)
        if not some:
            return
        looks = library.tag_looks()
        with ui.element("span").classes(tag_chips.BOX):
            for tag, count in some:
                color = tag_chips.color_of(tag, looks)
                with ui.element("span").classes(
                        f"{tag_chips.chip_class(color)} manager-tag--some") as chip:
                    ui.element("span").classes(tag_chips.dot_class(color))
                    ui.label(tag)
                    ui.label(t("manager.selection.of_all", count=count,
                               total=len(rows))).classes("manager-help")
                    ui.icon("close").props("size=14px") \
                        .on("click.stop", lambda _e=None, tag=tag: drop(tag)) \
                        .tooltip(t("manager.selection.take_away"))
                chip.classes("cursor-pointer")
                chip.on("click", lambda _e=None, tag=tag: retag([*everyone, tag]))
                chip.tooltip(t("manager.selection.give_all"))

    def settle() -> None:
        picker = context.get("tag_picker")
        everyone, _ = _carried(context[kind.rows])
        if picker is None or picker.is_deleted or set(picker.value or ()) == set(everyone):
            return
        setting["picker"] = True
        try:
            picker.value = everyone
        finally:
            setting["picker"] = False

    context["settle_tags"] = settle

    def draw() -> None:
        everyone, _ = _carried(context[kind.rows])
        with ui.column().classes("gap-1 w-full min-w-0"):
            control = tag_chips.Picker(library.tags(), value=everyone,
                                       looks=library.tag_looks(), adds=True,
                                       look=dropdown.OUTLINED) \
                .props('hide-dropdown-icon popup-content-class="manager-picker-popup"') \
                .classes("w-full min-w-0")
            context["tag_picker"] = control
            _marked(context, "tags", quiet)()
        control.on_value_change(
            lambda: None if setting["picker"] else retag(list(control.value or [])))

    return draw


def _launcher(context: dict[str, Any], kind: Kind) -> Callable[[], None]:
    async def pick(chosen: str) -> None:
        await change(context, kind, "launcher", lambda _row: chosen,
                     lambda row: named_launcher(context, row))

    def draw() -> None:
        rows = context[kind.rows]
        listing = context.get("launchers") or {}
        held = listing.get("launchers") or []
        playing = shared(rows, lambda row: str(row.get("launcher") or ""))
        names = {str(row.get("launcher") or ""): str(row.get("launcher_name") or "")
                 for row in rows}
        apps = {str(row.get("app") or "") for row in rows}
        offer = launchers.launcher_offer(held, next(iter(apps)) if len(apps) == 1 else "")
        anyone_named = any(named_launcher(context, row) for row in rows)
        with ui.row().classes("items-center gap-2 no-wrap"):
            only = next(iter(playing)) if len(playing) == 1 else None
            field = panel.MarkedSelect(
                {one["id"]: one["name"] for one in offer},
                value=only if any(one["id"] == only for one in offer) else None,
                marks={one["id"]: one["mark"] for one in offer}) \
                .classes("w-full min-w-0")
            if anyone_named:
                field.props("clearable")
            if not held:
                field.disable()
                field.tooltip(t("manager.workbench.no_launcher"))
            if len(playing) > 1:
                varies_chip(kind, playing,
                            lambda value: names.get(value) or t("word.default"))()
            field.on_value_change(lambda: pick(str(field.value or "")))

    return draw


async def change(context: dict[str, Any], kind: Kind, name: str,
                 new_of: Callable[[dict[str, Any]], Any],
                 old_of: Callable[[dict[str, Any]], Any]) -> None:
    """`name` to what `new_of` says for each row that does not hold it yet, remembering
    what each held for Undo. The rows are the selection as it is now, not as it was when
    the control was drawn."""
    forward: list[dict[str, Any]] = []
    back: list[dict[str, Any]] = []
    for row in context[kind.rows]:
        new, old = new_of(row), old_of(row)
        if new == old:
            continue
        forward.append(kind.edit(row, {name: new}))
        back.append(kind.edit(row, {name: old}))
    if forward:
        await apply(context, kind, forward, back)


@on_page
async def apply(context: dict[str, Any], kind: Kind, forward: list[dict[str, Any]],
                back: list[dict[str, Any]]) -> None:
    """Write `forward` as a job, then say how many it changed with Undo writing `back`
    for those."""
    try:
        written, failed = await write(context, forward)
    except Exception as exc:  # noqa: BLE001 - said, never raised into the page
        ui.notify(t("manager.workbench.save_failed"), caption=why(exc), type="negative")
        await _drawn_again(context, kind)
        return
    await _drawn_again(context, kind)
    wrote = set(written)
    mine = [edit for edit in back if _key(edit) in wrote]

    @on_page
    async def reverse() -> None:
        _, unwritten = await write(context, mine)
        await _drawn_again(context, kind)
        if unwritten:
            raise RuntimeError(t("manager.selection.not_written", count=len(unwritten)))

    if not written:
        ui.notify(t("manager.workbench.save_failed"),
                  caption="; ".join(sorted({one["reason"] for one in failed if one["reason"]})),
                  type="negative")
        return
    undo.offer(t(kind.changed, count=len(written)), reverse, warn=bool(failed),
               caption=t("manager.selection.not_written", count=len(failed)) if failed else "")


async def _drawn_again(context: dict[str, Any], kind: Kind) -> None:
    """After a write, or a refused one: the grid put right, the selection's rows read
    again in place, and every mark drawn from them. A selection that has gone from the
    library is let be."""
    await context["saved"]()
    library = context["library"]
    wanted = {str(row["id"]) for row in context[kind.rows]}
    if kind.table:
        found = await offload.io(library.load_tables)
        context["launchers"] = await offload.io(_launchers_for_panel, library)
    else:
        found = await offload.io(getattr, library, "games")
    fresh = [row for row in found if str(row.get("id")) in wanted]
    if not fresh:
        return
    context[kind.rows][:] = fresh
    for one in context.get("marks", {}).values():
        one.refill()
    settle = context.get("settle_tags")
    if callable(settle):
        settle()


def _key(edit: dict[str, Any]) -> tuple[str, str]:
    return (str(edit.get("game_id") or ""), str(edit.get("table_id") or ""))


async def write(context: dict[str, Any],
                edits: list[dict[str, Any]]) -> tuple[list[tuple[str, str]], list[dict[str, str]]]:
    """The edits as one job, waited out. Returns the rows written and each that was not,
    with its reason."""
    library = context["library"]
    state = context["state"]
    done = await offload.io(library.put_marks, edits)
    job_id = str(done.get("id") or "")
    if callable(watch := state.get("watch_jobs")):
        watch()
    found: dict[str, Any] = {}
    while job_id:
        await asyncio.sleep(JOB_POLL_S)
        found = await offload.io(library.job, job_id)
        if found.get("state") != "running":
            break
    await run.io_bound(library.marks_written)
    if found.get("state") == "failed":
        raise RuntimeError(str(found.get("error") or ""))
    result = found.get("result") or {}
    return ([(str(one.get("game") or ""), str(one.get("table") or ""))
             for one in result.get("written") or ()],
            [{"reason": str(one.get("reason") or "")} for one in result.get("failed") or ()])
