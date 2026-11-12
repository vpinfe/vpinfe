"""What games and tables can be told to do, for the grid's row menu, the panel's header menu
and the grid's selection menu alike.

Each list takes the rows it acts on: one, or every row selected. A verb shows when it applies
to any of them and acts on those it applies to; one with no meaning over several is `one`.

`refresh` and `after` are the caller's way of putting its own surface right once a write has
landed: the Games and Tables pages repaint the rows they hold, the panel rebuilds itself.
`after` is also told whether a table is gone, because a panel about a table that no longer
exists has nowhere to stay.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from functools import partial
from pathlib import Path
from typing import Any

from nicegui import run, ui

from common.failures import why
from common.i18n import t
from manager.backups import backup_rows
from manager.devices import send_to_device
from manager.extensions import ext_verbs
from manager.kit import confirm, game_tables, offload, panel, stars, undo, verbs, walks
from manager.kit import dialog as frame
from manager.kit.on_page import on_page
from manager.library import collection_adds, getting_file, media_fill, vps_match
from manager.library import record as recorder

Refresh = Callable[[list[str]], Awaitable[None]]
After = Callable[[list[str], bool], Awaitable[None]]
Rate = Callable[[list[str], int], Awaitable[bool]]
Collections = Callable[[Callable[[], Any]], None]

PLAYED = ("last_played", "play_count", "play_time_seconds")

# The blocks a menu is drawn in, a rule where one gives way to the next (`Verb.group`).
PLAY, DETAILS, MEDIA, MOVE, FILES, COLLECTIONS = (
    "play", "details", "media", "move", "files", "collections")


def has_record(record: dict[str, Any] | None) -> bool:
    """Whether a play record holds anything to clear."""
    return any((record or {}).get(key) for key in PLAYED)


def auto_match_outcome(result: dict[str, Any]) -> tuple[str, bool]:
    """What Auto-match says when it is done, and whether any match moved."""
    moved = int(result.get("changed") or 0)
    waiting = int(result.get("unmatched") or 0)
    skipped = int(result.get("skipped") or 0)
    parts = [t(key, count=count) for key, count in (
        ("manager.games.auto_matched", moved), ("manager.games.unmatched", waiting),
        ("manager.games.auto_match_skipped", skipped)) if count]
    if not parts:
        return t("manager.vps_match.nothing_changed"), False
    if len(parts) == 1:
        return parts[0], bool(moved)
    if len(parts) == 2:
        return t("manager.games.auto_matched_some", matched=parts[0], waiting=parts[1]), bool(moved)
    return t("manager.games.auto_matched_all", matched=parts[0], waiting=parts[1],
             skipped=parts[2]), True


def _ids(rows: list[dict[str, Any]]) -> list[str]:
    return [str(one["id"]) for one in rows]


def _only_name(games: list[dict[str, Any]]) -> str:
    return str(games[0].get("name") or "") if len(games) == 1 else ""


# --- games ------------------------------------------------------------------------


def game_acts(library: Any, games: list[dict[str, Any]], state: dict[str, Any],
              refresh: Refresh, *, rate: Rate | None = None,
              match: Callable[[list[dict[str, Any]]], Awaitable[Any]] | None = None,
              collections: Collections | None = None) -> list[panel.Verb]:
    """What can be done to these games. `rate` writes a rating and repaints the grid's
    rows, absent where no grid holds them. `match` stands in for the walk through the VPS
    picker, for a caller with its own way of asking about one game. `state` is the page's,
    which the transfers and the media jobs use to start the drawer's job watch."""
    ids = _ids(games)
    name = _only_name(games)
    marked = [bool(one.get("private")) for one in games]

    @on_page
    async def launch() -> None:
        try:
            await run.io_bound(library.launch, ids[0])
        except Exception as exc:  # noqa: BLE001
            ui.notify(t("manager.workbench.launch_failed"), caption=why(exc), type="negative")
            return
        ui.notify(t("manager.games.launching", name=str(games[0].get("name") or "")),
                  type="positive")

    @on_page
    async def mark_private(private: bool) -> None:
        flipping = [str(one["id"]) for one in games if bool(one.get("private")) != private]
        try:
            await run.io_bound(library.set_games_private, flipping, private)
        except Exception as exc:  # noqa: BLE001 - said, and the grid is as it was
            ui.notify(t("said.save_failed"), caption=why(exc), type="negative")
            return
        await refresh(flipping)
        said = t("manager.games.marked_private" if private
                 else "manager.games.cleared_private", count=len(flipping))

        @on_page
        async def put_back() -> None:
            await run.io_bound(library.set_games_private, flipping, not private)
            await refresh(flipping)

        if flipping:
            undo.offer(said, put_back)
        else:
            ui.notify(said, type="positive")

    @on_page
    async def matched() -> None:
        if match is not None:
            await match(games)
            return
        await vps_match.walk(library, games)
        await refresh(ids)

    @on_page
    async def auto_match() -> None:
        try:
            result = await offload.io(library.auto_match, ids)
        except Exception as exc:  # noqa: BLE001 - said, and the grid is as it was
            ui.notify(t("manager.games.match_failed"), caption=why(exc), type="warning")
            return
        said, moved = auto_match_outcome(result)
        ui.notify(said, type="positive" if moved else "info")
        if moved:
            await refresh(ids)

    @on_page
    async def placed() -> None:
        await run.io_bound(library.reread_media, ids)
        await refresh(ids)

    async def get_missing_media() -> None:
        await media_fill.ask(ids, state, placed, name=name)

    async def record_media() -> None:
        title = t("manager.record.title", name=name) if name \
            else t("manager.record.title_games", count=len(ids))
        await recorder.ask(library, [(game_id, "") for game_id in ids], title, state, placed)

    offered = [panel.Verb(t("manager.games.launch"), launch, one=True, group=PLAY)]
    if rate is not None:
        offered.append(panel.Verb(t("manager.games.rate"), partial(_rate, games, rate),
                                  group=DETAILS))
    if not all(marked):
        offered.append(panel.Verb(t("manager.games.mark_private"), partial(mark_private, True),
                                  group=DETAILS))
    if any(marked):
        offered.append(panel.Verb(t("manager.games.clear_private"),
                                  partial(mark_private, False), group=DETAILS))
    offered.append(panel.Verb(t("manager.games.match_vps"), matched, group=DETAILS))
    offered.append(panel.Verb(t("manager.games.auto_match"), auto_match,
                              tip=t("manager.games.auto_match.help"), group=DETAILS))
    offered.append(panel.Verb(t("manager.media_fill.get_missing"), get_missing_media,
                              group=MEDIA))
    if recording := recorder.verb(library, record_media):
        offered.append(replace(recording, group=MEDIA))
    offered.append(panel.Verb(t("manager.games.send_device"),
                              lambda: send_to_device.ask_where(games, state), group=MOVE))
    offered.append(panel.Verb(
        t("manager.backups.take_now"),
        lambda: backup_rows.back_up_games(library, games, lambda: refresh(ids)), group=MOVE))
    if collections is not None:
        offered.append(panel.Verb(t("manager.adds.verb"), draw=collections,
                                  group=COLLECTIONS))
    if reset := reset_games(library, ids, refresh):
        offered.append(reset)
    if deleter := delete_games(library, games, refresh):
        offered.append(deleter)
    offered += ext_verbs.offered(
        state, "game", [{"game_id": one} for one in ids], lambda: refresh(ids),
        public=[{"game_id": str(one["id"])} for one in games if not one.get("private")])
    return offered


async def _rate(games: list[dict[str, Any]], rate: Rate) -> None:
    """Set a rating on every game passed in, through the writer the star click uses."""
    @on_page
    async def apply(value: int) -> None:
        was: dict[int, list[str]] = {}
        for one in games:
            was.setdefault(int(one.get("rating") or 0), []).append(str(one["id"]))
        if not await rate(_ids(games), value):
            return
        box.close()

        @on_page
        async def put_back() -> None:
            for prior, ids in was.items():
                await rate(ids, prior)

        undo.offer(t("manager.games.rated_game_s", count=len(games), value=stars.shown(value)),
                   put_back)

    chosen: dict[str, int | None] = {"value": None}

    def choose(value: int) -> None:
        chosen["value"] = value
        draw.refresh()
        go.enable()

    @ui.refreshable
    def draw() -> None:
        stars.draw(chosen["value"] or 0, choose)()

    with frame.opened(t("manager.games.rate_game_s", count=len(games))) as box:
        with ui.row().classes("px-3"):
            draw()
        with frame.footer():
            frame.cancel(box.close)
            go = frame.answer(t("word.apply"), lambda: asyncio.create_task(
                apply(int(chosen["value"] or 0))), icon=verbs.DONE)
            go.disable()
    box.open()


def reset_games(library: Any, game_ids: list[str],
                refresh: Refresh) -> panel.Verb | None:
    """Clear the play record of those of these games that hold one, or None where none does."""
    held = {str(one.get("id")): one.get("user") for one in library.games
            if str(one.get("id")) in game_ids}
    clearing = [game_id for game_id in game_ids if has_record(held.get(game_id))]
    if not clearing:
        return None

    @on_page
    async def reset() -> None:
        if not await confirm.ask(
                t("manager.workbench.reset_game_question", count=len(clearing)),
                detail=t("manager.workbench.reset_game_detail"),
                confirm=t("word.reset"), icon=verbs.RESET):
            return
        try:
            for game_id in clearing:
                await run.io_bound(library.reset_play_record, game_id)
        except Exception as exc:  # noqa: BLE001
            ui.notify(t("manager.workbench.save_failed"), caption=why(exc), type="negative")
            return
        ui.notify(t("manager.workbench.reset_done"), type="positive")
        await refresh(clearing)

    return panel.Verb(t("manager.workbench.reset_game_action"), reset, danger=True)


def delete_games(library: Any, games: list[dict[str, Any]], refresh: Refresh) -> panel.Verb:
    """Remove each game's whole folder from the disk, after a question that names them."""
    @on_page
    async def delete() -> None:
        if not await confirm.ask(
                t("manager.games.delete_question", count=len(games)),
                detail=t("manager.games.delete_detail"),
                lines=[str(one.get("folder") or one.get("name") or "") for one in games]):
            return
        gone: list[str] = []
        try:
            for one in games:
                await run.io_bound(library.delete_game_folder, str(one["id"]))
                gone.append(str(one["id"]))
        except Exception as exc:  # noqa: BLE001 - said, with what was already removed
            ui.notify(t("manager.games.delete_failed"), caption=why(exc), type="negative")
        if gone:
            ui.notify(t("manager.games.deleted", count=len(gone)), type="positive")
            await refresh(gone)

    return panel.Verb(t("manager.games.delete"), delete, danger=True)


# --- tables -----------------------------------------------------------------------


@dataclass(frozen=True)
class Target:
    """One table and what the acts on it need to know of its game: `siblings` is every
    table of that game, which says whether it can be locked and what a forget would take
    with it."""

    game_id: str
    game: str
    table: dict[str, Any]
    siblings: list[dict[str, Any]]

    @property
    def id(self) -> str:
        return str(self.table.get("id") or "")


def table_acts(library: Any, targets: list[Target], after: After, state: dict[str, Any],
               collections: Collections | None) -> list[panel.Verb]:
    """What can be done to these tables."""
    if not targets:
        return []
    one = len(targets) == 1
    first = targets[0]

    def games_of(some: list[Target]) -> list[str]:
        return list(dict.fromkeys(item.game_id for item in some))

    @on_page
    async def write(some: list[Target], call: Callable[..., Any],
                    args: Callable[[Target], tuple[Any, ...]], failed: str) -> list[Any] | None:
        """Write to each in turn. Answers what the writes returned, or None where one failed
        and said why; what was written before it is put right on screen either way."""
        answers: list[Any] = []
        try:
            for item in some:
                answers.append(await run.io_bound(call, item.game_id, *args(item)))
        except Exception as exc:  # noqa: BLE001
            ui.notify(failed, caption=why(exc), type="negative")
            if answers:
                await after(games_of(some[:len(answers)]), False)
            return None
        return answers

    @on_page
    async def make_default() -> None:
        locked = game_tables.is_locked(first.siblings)
        was = next((row["id"] for row in first.siblings if row.get("default")), "") \
            if locked else ""
        answers = await write([first], library.set_default_table, lambda item: (item.id,),
                              t("manager.workbench.change_failed"))
        if answers is None:
            return

        async def put_back() -> None:
            await run.io_bound(library.set_default_table, first.game_id, was)
            await after([first.game_id], False)

        undo.offer(game_tables.now_plays(first.game, first.table), put_back)
        await after([first.game_id], False)

    @on_page
    async def lock(locking: bool) -> None:
        answers = await write([first], library.set_default_table,
                              lambda item: (item.id if locking else "",),
                              t("manager.workbench.change_failed"))
        if answers is not None:
            ui.notify(game_tables.lock_said(first.game, first.table,
                                            (answers[0] or {}).get("tables") or [],
                                            lock=locking), type="positive")
            await after([first.game_id], False)

    @on_page
    async def hide(hidden: bool) -> None:
        applies = [item for item in targets if bool(item.table.get("hidden")) != hidden]
        answers = await write(applies, library.set_table_hidden,
                              lambda item: (item.id, hidden), t("manager.workbench.save_failed"))
        if answers is None:
            return
        said = (game_tables.hidden_said(applies[0].game, applies[0].table, answers[0],
                                        hidden=hidden)
                if len(applies) == 1 else
                t("manager.game_tables.hid_n" if hidden else "manager.game_tables.unhid_n",
                  count=len(applies)))
        await after(games_of(applies), False)

        @on_page
        async def put_back() -> None:
            await write(applies, library.set_table_hidden, lambda item: (item.id, not hidden),
                        t("manager.workbench.save_failed"))
            await after(games_of(applies), False)

        undo.offer(said, put_back)

    games_by_id = {str(one.get("id")): one for one in library.games}
    matched_to_vps = [item for item in targets
                      if (games_by_id.get(item.game_id) or {}).get("vps_id")]

    @on_page
    async def match_releases() -> None:
        """Each selected table of a matched game in the release picker, one at a time. The
        picker opens on the table's own release, so a matched table can be kept (Skip),
        changed or cleared."""
        from manager.workbench import game_match

        by_row = {id(item.table): item for item in matched_to_vps}
        held: dict[str, list[dict[str, Any]]] = {}
        changed: list[str] = []

        async def ask(row: dict[str, Any], place: str) -> Any:
            item = by_row[id(row)]
            try:
                if item.game_id not in held:
                    held[item.game_id] = await offload.io(library.tables_for, item.game_id)
            except Exception as error:
                ui.notify(t("manager.vps_match.set_failed"), caption=why(error), type="negative")
                return walks.SKIPPED
            table = next((one for one in held[item.game_id] if str(one.get("id")) == item.id),
                         None)
            if table is None:
                return walks.SKIPPED
            context = {"library": library, "game": games_by_id[item.game_id],
                       "game_id": item.game_id}
            return await game_match.ask_a_release(
                context, table, place if len(matched_to_vps) > 1 else "")

        async def write(row: dict[str, Any], answer: Any) -> None:
            item = by_row[id(row)]
            await run.io_bound(library.set_table_source, item.game_id, item.id, str(answer))
            changed.append(item.game_id)

        try:
            await walks.each([item.table for item in matched_to_vps], ask, write,
                             t("manager.vps_match.set_failed"))
        finally:
            if changed:
                await after(games_of([item for item in matched_to_vps
                                      if item.game_id in changed]), False)

    @on_page
    async def extract() -> None:
        applies = [item for item in targets if not game_tables.runs_its_own_script(item.table)]
        if await write(applies, library.extract_script, lambda item: (item.id,),
                       t("manager.workbench.extract_failed")) is not None:
            ui.notify(t("manager.workbench.extract_done", count=len(applies)), type="positive")
            await after(games_of(applies), False)

    @on_page
    async def drop_script() -> None:
        applies = [item for item in targets if game_tables.runs_its_own_script(item.table)]
        every = all(game_tables.shares_its_script(item.table) for item in applies)
        if not await confirm.ask(
                t("manager.workbench.delete_question", count=len(applies)),
                detail=t("manager.game_tables.script_detail" if every
                         else "manager.workbench.delete_detail"),
                lines=[f"{Path(item.table.get('filename') or '').stem}.vbs"
                       for item in applies]):
            return
        if await write(applies, library.delete_script, lambda item: (item.id,),
                       t("manager.workbench.delete_failed")) is not None:
            ui.notify(t("manager.game_tables.script_deleted" if every
                        else "manager.workbench.delete_done", count=len(applies)),
                      type="positive")
            await after(games_of(applies), False)

    @on_page
    async def reset() -> None:
        applies = [item for item in targets if has_record(item.table.get("user"))]
        if not await confirm.ask(
                t("manager.workbench.reset_table_question", count=len(applies)),
                detail=t("manager.workbench.reset_table_detail"),
                confirm=t("word.reset"), icon=verbs.RESET):
            return
        if await write(applies, library.reset_play_record, lambda item: (item.id,),
                       t("manager.workbench.save_failed")) is not None:
            ui.notify(t("manager.workbench.reset_done"), type="positive")
            await after(games_of(applies), False)

    @on_page
    async def forget() -> None:
        applies = [item for item in targets if game_tables.can_forget(item.table)]
        if len(applies) == 1:
            only = applies[0]
            detail = ("manager.workbench.forget_keyed" if game_tables.is_keyed(only.table)
                      else "manager.workbench.forget_reference"
                      if game_tables.is_referenced(only.table)
                      else "manager.workbench.forget_missing")
            made = game_tables.made_from_it_line(
                [game_tables.name_among(derived, only.siblings)
                 for derived in game_tables.made_from_it(only.table, only.siblings)])
            lines = [game_tables.name_among(only.table, only.siblings),
                     *([made] if made else [])]
        else:
            detail = "manager.workbench.forget_several"
            lines = [game_tables.JOIN.join((item.game, game_tables.name_among(
                item.table, item.siblings))) for item in applies]
        if not await confirm.ask(
                t("manager.workbench.forget_table", count=len(applies)), detail=t(detail),
                lines=lines, confirm=t("word.forget"), icon=verbs.FORGET):
            return
        if await write(applies, library.forget_table, lambda item: (item.id,),
                       t("manager.workbench.forget_failed")) is not None:
            ui.notify(t("manager.workbench.table_forgotten", count=len(applies)),
                      type="positive")
            await after(games_of(applies), True)

    async def replace_file() -> None:
        game: dict[str, Any] = next(
            (one for one in library.games if str(one.get("id")) == first.game_id), {})
        getting_file.open_for(
            library, game_id=first.game_id, game_dir=str(game.get("folder") or ""),
            table_id=first.id,
            title=t("manager.table_swap.replace_title",
                    name=game_tables.name_among(first.table, first.siblings)),
            on_done=partial(after, [first.game_id], False))

    @on_page
    async def delete_file() -> None:
        try:
            plan = await offload.io(library.table_swap_plan, first.game_id, first.id)
        except Exception as exc:  # noqa: BLE001
            ui.notify(t("manager.table_swap.delete_failed"), caption=why(exc), type="negative")
            return
        label = str(plan["label"])
        made = game_tables.made_from_it_line(list(plan.get("patched") or []))
        is_last = len(first.siblings) <= 1
        if not await confirm.ask(
                t("manager.table_swap.delete_question", label=label),
                detail=[t("manager.table_swap.delete_detail"),
                        t("manager.table_swap.delete_last" if is_last
                          else "manager.table_swap.delete_next", name=first.game)],
                lines=[plan["filename"], *(one["file"] for one in plan["files"]),
                       *([made] if made else [])]):
            return
        answers = await write([first], library.delete_table_file, lambda item: (item.id,),
                              t("manager.table_swap.delete_failed"))
        if answers is None:
            return
        copies = [str(one["id"]) for one in (answers[0] or {}).get("kept") or ()]

        async def put_back() -> None:
            for each in copies:
                await run.io_bound(library.restore_backup, each, reread=False)
            await run.io_bound(library.restored_backups)
            library.forget_media(first.game_id)
            await after([first.game_id], True)

        await after([first.game_id], True)
        if copies:
            undo.offer(t("manager.table_swap.deleted", label=label), put_back,
                       done=t("manager.table_swap.put_back", label=label))
        else:
            ui.notify(t("manager.table_swap.deleted", label=label), type="positive")

    @on_page
    async def placed() -> None:
        await run.io_bound(library.reread_media, games_of(targets))
        await after(games_of(targets), False)

    async def record_media() -> None:
        title = t("manager.record.title_table", name=first.game,
                  table=game_tables.table_name(first.table)) if one \
            else t("manager.record.title_tables", count=len(targets))
        await recorder.ask(library, [(item.game_id, item.id) for item in targets],
                           title, state, placed)

    offered: list[panel.Verb] = []
    table = first.table
    if not one:
        offered.append(panel.Verb(t("word.make_default"), one=True, group=DETAILS))
    elif not table.get("default"):
        if not game_tables.why_not_default(table):
            offered.append(panel.Verb(t("word.make_default"), make_default, one=True,
                                      group=DETAILS))
    elif locking := game_tables.lock_act(table, first.siblings):
        locks, words = locking
        offered.append(panel.Verb(words, partial(lock, locks), one=True, group=DETAILS))
    hidden = [bool(item.table.get("hidden")) for item in targets]
    if not all(hidden):
        offered.append(panel.Verb(t("manager.games.hide"), partial(hide, True),
                                  group=DETAILS))
    if any(hidden):
        offered.append(panel.Verb(t("manager.games.unhide"), partial(hide, False),
                                  group=DETAILS))
    offered.append(panel.Verb(
        t("manager.games.match_vps"), match_releases if matched_to_vps else None,
        hint="" if matched_to_vps else t("manager.workbench.releases_unmatched"),
        group=DETAILS))
    if not all(game_tables.runs_its_own_script(item.table) for item in targets):
        offered.append(panel.Verb(t("manager.games.extract_script"), extract, group=FILES))
    if any(game_tables.can_swap(item.table) for item in targets):
        offered.append(panel.Verb(t("manager.table_swap.replace_file"), replace_file, one=True,
                                  group=FILES))
    if recording := recorder.verb(library, record_media):
        offered.append(replace(recording, group=MEDIA))
    if collections is not None:
        offered.append(panel.Verb(t("manager.adds.verb"), draw=collections,
                                  group=COLLECTIONS))
    if any(game_tables.runs_its_own_script(item.table) for item in targets):
        offered.append(panel.Verb(t("manager.games.delete_script"), drop_script, danger=True))
    if any(has_record(item.table.get("user")) for item in targets):
        offered.append(panel.Verb(t("manager.workbench.reset_table_action"), reset, danger=True))
    if reset_game := reset_games(library, games_of(targets), lambda ids: after(ids, False)):
        offered.append(reset_game)
    if any(game_tables.can_forget(item.table) for item in targets):
        offered.append(panel.Verb(t("word.forget"), forget, danger=True))
    if any(game_tables.can_swap(item.table) for item in targets):
        offered.append(panel.Verb(t("manager.table_swap.delete_file"), delete_file,
                                  danger=True, one=True))
    offered += ext_verbs.offered(
        state, "table", [{"game_id": item.game_id, "table_id": item.id} for item in targets],
        lambda: after(games_of(targets), False))
    return offered


def collections_of(offer: collection_adds.Offer,
                   known: collection_adds.Read | None) -> Collections | None:
    """The add-to-collection entries, drawn into whichever menu is being built; the rule
    above them is the menu's own (`Verb.group`). None until the library's collections have
    been read."""
    if known is None:
        return None

    def draw(close: Callable[[], Any]) -> None:
        collection_adds.draw(offer, known, close)

    return draw
