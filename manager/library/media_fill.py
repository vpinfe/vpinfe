"""Get missing art: which kinds, what each would fetch, then the fetch as a job.

The ticks are this browser's and only this dialog reads them. The fill for new games
never does.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable
from typing import Any, Literal

from nicegui import ui

from common.failures import why
from common.games import media_fill
from common.i18n import t
from common.media.media_specs import media_label_map
from manager.kit import confirm, offload, remembered, verbs
from manager.kit import dialog as frame
from manager.kit.api import ApiClient
from manager.kit.on_page import on_page

TICKS = "media_fill.kinds"
_POLL_S = 1.0
_POLLS = 3600


def ticked(kinds: list[dict[str, Any]], held: dict[str, bool]) -> dict[str, bool]:
    """Which rows start ticked: a kind with nothing to get never is, and one this
    browser has not answered for yet is."""
    return {str(row["kind"]): int(row.get("available") or 0) > 0
            and bool(held.get(str(row["kind"]), True)) for row in kinds}


def remember(held: dict[str, bool], chosen: dict[str, bool],
             kinds: list[dict[str, Any]]) -> dict[str, bool]:
    """`held` with the answers given now. A dimmed row was not answered, so what was
    held for it stays."""
    open_rows = {str(row["kind"]) for row in kinds if int(row.get("available") or 0)}
    return {**held, **{kind: on for kind, on in chosen.items() if kind in open_rows}}


def outcome(result: dict[str, Any]) -> tuple[str, Literal["positive", "warning", "info"]]:
    """What the fill says when it ends, and the notice type to say it with."""
    got, failed = int(result.get("filled") or 0), int(result.get("failed") or 0)
    unreachable = ", ".join(str(name) for name in result.get("unreachable") or ())
    if unreachable and not (got or failed):
        return t("manager.media_fill.unreachable", sources=unreachable), "warning"
    if unreachable:
        said, _ = outcome({"filled": got, "failed": failed})
        return t("manager.media_fill.also_unreachable", said=said, sources=unreachable), "warning"
    if got and failed:
        return t("manager.media_fill.got_failed", got=t("manager.media_fill.got", count=got),
                 failed=failed), "warning"
    if got:
        return t("manager.media_fill.got", count=got), "positive"
    if failed:
        return t("manager.media_fill.get_failed", count=failed), "warning"
    return t("manager.media_fill.got_nothing"), "info"


@on_page
async def ask(game_ids: list[str] | None, state: dict[str, Any],
              then: Callable[[], Any], name: str = "") -> None:
    """Which kinds to get for these games, then get them. `None` is every game, and
    `name` stands for the count when the dialog is about one game."""
    try:
        found = await offload.io(ApiClient().missing_media, game_ids)
    except Exception as exc:  # noqa: BLE001 - said, and nothing was fetched
        ui.notify(t("manager.media_fill.read_failed"), caption=why(exc), type="warning")
        return
    if not _anything_to_ask(found):
        ui.notify(t("manager.media_fill.no_sources"), type="warning")
        return
    kinds = await _choose(found, name)
    if kinds:
        await get(lambda: ApiClient().fill_media(game_ids, kinds), state, then)


@on_page
async def confirm_slots(rows: list[dict[str, Any]], state: dict[str, Any],
                        then: Callable[[], Any]) -> None:
    """Get art for exactly these media rows, the ones among them that have no file."""
    gaps = [row for row in rows if media_fill.is_gap(row)]
    if not gaps:
        ui.notify(t("manager.media_fill.nothing_missing"), type="info")
        return
    games = {str(row["game_id"]): bool(row.get("vps_id")) for row in gaps}
    if not await confirm.ask(
            t("manager.media_fill.ask_slots", count=len(gaps)),
            lines=_unmatched(len(games), sum(not matched for matched in games.values())),
            confirm=t("manager.media_fill.get_media"), icon=verbs.FETCH, danger=False):
        return
    slots = tuple((str(row["game_id"]), str(row["kind"])) for row in gaps)
    await get(lambda: ApiClient().fill_media(slots=slots), state, then)


@on_page
async def confirm_kind(game_ids: list[str], kind: str, state: dict[str, Any],
                       then: Callable[[], Any]) -> None:
    """Get one kind for these games, asked as a plain question with no choices."""
    try:
        found = await offload.io(ApiClient().missing_media, game_ids)
    except Exception as exc:  # noqa: BLE001 - said, and nothing was fetched
        ui.notify(t("manager.media_fill.read_failed"), caption=why(exc), type="warning")
        return
    if not _anything_to_ask(found):
        ui.notify(t("manager.media_fill.no_sources"), type="warning")
        return
    label = media_label_map().get(kind, kind)
    row: dict[str, Any] = next(
        (one for one in found.get("kinds") or [] if one.get("kind") == kind), {})
    missing, available = int(row.get("missing") or 0), int(row.get("available") or 0)
    games, unmatched = int(found.get("games") or 0), int(found.get("unmatched") or 0)
    unreachable = ([t("manager.media_fill.unreachable", sources=", ".join(found["unreachable"]))]
                   if found.get("unreachable") else [])
    if not available:
        said = (unreachable or (_unmatched(games, unmatched) if unmatched == games else [])
                or [t("manager.media_fill.none_for_kind", kind=label)])
        ui.notify(said[0], type="warning" if unreachable else "info")
        return
    question = (t("manager.media_fill.ask_kind", kind=label, count=available)
                if available == missing
                else t("manager.media_fill.ask_kind_of", kind=label, count=available,
                       missing=missing))
    if await confirm.ask(question, lines=_unmatched(games, unmatched) + unreachable,
                         confirm=t("manager.media_fill.get_media"), icon=verbs.FETCH,
                         danger=False):
        await get(lambda: ApiClient().fill_media(game_ids, [kind]), state, then)


def _anything_to_ask(found: dict[str, Any]) -> bool:
    """Whether there is a source to ask: an online one that is on, or a game's own file."""
    return bool(found.get("sources")) or any(
        int(row.get("local") or 0) for row in found.get("kinds") or [])


def _from_files(found: dict[str, Any]) -> int:
    return sum(int(row.get("local") or 0) for row in found.get("kinds") or [])


def _unmatched(games: int, unmatched: int) -> list[str]:
    if not unmatched:
        return []
    return [t("manager.media_fill.game_unmatched") if games == 1
            else t("manager.media_fill.unmatched", count=unmatched)]


async def _choose(found: dict[str, Any], name: str) -> list[str]:
    rows = list(found.get("kinds") or [])
    held = dict(remembered.get(TICKS) or {})
    chosen = ticked(rows, held)
    offered = {str(row["kind"]): int(row.get("available") or 0) for row in rows}
    games, unmatched = int(found.get("games") or 0), int(found.get("unmatched") or 0)

    def total() -> int:
        return sum(offered[kind] for kind, on in chosen.items() if on)

    def recount() -> None:
        go.text = t("manager.media_fill.get", count=total())
        go.set_enabled(total() > 0)

    sources = list(found["sources"])
    from_files = _from_files(found)
    with frame.opened(t("manager.media_fill.title", source=sources[0])
                      if len(sources) == 1 and not from_files
                      else t("manager.media_fill.title_several"), size="wide") as box:
        ui.label(name or t("manager.media_fill.games", count=games)) \
            .classes("manager-help")
        with ui.grid(columns="minmax(0, 1fr) max-content max-content") \
                .classes("w-full items-center gap-x-4 gap-y-1"):
            labels = media_label_map()
            for row in rows:
                _kind_row(row, labels.get(str(row["kind"]), str(row["kind"])), chosen,
                          recount)
        if from_files:
            ui.label(t("manager.media_fill.from_files", count=from_files)) \
                .classes("manager-help")
        for line in _unmatched(games, unmatched):
            ui.label(line).classes("manager-help")
        if found.get("unreachable"):
            ui.label(t("manager.media_fill.unreachable",
                       sources=", ".join(found["unreachable"]))) \
                .classes("manager-help")
        with frame.footer():
            frame.cancel(lambda: box.submit([]))
            go = frame.answer(t("manager.media_fill.get", count=total()),
                              lambda: box.submit([kind for kind, on in chosen.items()
                                                  if on and offered[kind]]),
                              icon=verbs.FETCH)
            go.set_enabled(total() > 0)
        frame.enter_presses(go)

    picked = list(await box or [])
    if picked:
        remembered.put(TICKS, remember(held, chosen, rows))
    return picked


def _kind_row(row: dict[str, Any], label: str, chosen: dict[str, bool],
              changed: Callable[[], None]) -> None:
    """A kind, how many games lack it, and how many of those a source can fill."""
    kind = str(row["kind"])
    missing, available = int(row.get("missing") or 0), int(row.get("available") or 0)

    def picked(on: bool) -> None:
        chosen[kind] = on
        changed()

    box = ui.checkbox(label, value=chosen[kind]).props("dense")
    box.on_value_change(lambda event: picked(bool(event.value)))
    box.set_enabled(available > 0)
    if not missing:
        ui.label(t("manager.media_fill.none_missing")).classes("manager-member-qualifier")
        ui.element("div")
        return
    ui.label(t("manager.media_fill.missing", count=missing)) \
        .classes("manager-member-qualifier")
    ui.label(t("manager.media_fill.available", count=available) if available
             else t("manager.media_fill.none_available")) \
        .classes("manager-member-qualifier")


@on_page
async def get(start: Callable[[], dict], state: dict[str, Any],
              then: Callable[[], Any]) -> None:
    """Start the fill and say what it got once it ends; the footer line reports it
    while it runs. `then` runs only when something was placed."""
    client = ApiClient()
    try:
        job = await offload.io(start)
    except Exception as exc:  # noqa: BLE001 - said, and nothing was fetched
        ui.notify(t("manager.media_fill.could_not_start"), caption=why(exc), type="warning")
        return
    watch = state.get("watch_jobs")
    if callable(watch):
        watch()
    for _ in range(_POLLS):
        await asyncio.sleep(_POLL_S)
        try:
            found = await offload.io(client.job, str(job.get("id") or ""))
        except Exception:  # noqa: BLE001 - the footer line still reports it
            return
        if found.get("state") == "running":
            continue
        if found.get("state") == "failed":
            ui.notify(t("manager.media_fill.failed"), caption=str(found.get("error") or ""),
                      type="negative")
            return
        result = found.get("result") or {}
        said, level = outcome(result)
        ui.notify(said, type=level)
        if int(result.get("filled") or 0):
            answer = then()
            if inspect.isawaitable(answer):
                await answer
        return
