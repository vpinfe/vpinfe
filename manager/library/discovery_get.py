"""Getting files from Discovery: what is under way on an entry, and how a press gets a file.

What is under way lives with the page rather than the pane. Every finished download redraws
the pane, and the rows it draws find how far each had got where it was left.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePosixPath
from typing import Any

from nicegui import run, ui

from common.failures import why
from common.games import opened_pages
from common.i18n import t
from common.media.media_specs import MEDIA_SPECS
from common.online import vps_kinds
from manager.kit import offload, panel, verbs
from manager.kit.on_page import on_page
from manager.library import import_dialog, mediasource, uploads

logger = logging.getLogger("vpinfe.manager.library.discovery_get")

QUEUED = "queued"
DOWNLOADING = "downloading"
FAILED = "failed"

# The slot kind of a table, which a game holds any number of.
TABLE = "table"

FIRST_MEDIA = ("wheel", "topper")
# What the foot bar and the rail's corner glyphs are painted under.
FOOT = "foot"
RAIL = "rail"
BODY = "body"

# What an import leaves as a single file, which a record can be tied to by its path.
_FILE_ACTIONS = ("copy", "replace_b2s", "zip_rom", "replace_media")


@dataclass(frozen=True)
class Pick:
    """One file a source will hand over: a picture a media source offers, or a row an
    extension's source answered."""

    key: str
    kind: str
    source: str
    size: str = ""
    row: str = ""


@dataclass(frozen=True)
class Offered:
    """A row an extension's source answered for a file VPS lists. `row` is the answer, with
    the state and buttons the source drew on it."""

    extension: str
    key: str
    source_id: str
    name: str
    kind: str
    row: dict[str, Any]
    pick: Pick

    @property
    def state(self) -> dict[str, Any]:
        return self.row.get("state") or {}

    @property
    def level(self) -> str:
        return str(self.state.get("level") or "")

    @property
    def progress(self) -> int | None:
        """How far a download has got, in whole percent, where the source said."""
        value = self.state.get("progress")
        if isinstance(value, bool) or not isinstance(value, int | float):
            return None
        return round(min(max(float(value), 0.0), 1.0) * 100)


@dataclass
class Move:
    state: str
    reason: str = ""


@dataclass
class Tray:
    """What is being downloaded on one entry, and how far each file has got."""

    moves: dict[str, Move] = field(default_factory=dict)
    stopping: bool = False
    offered: dict[str, Offered] = field(default_factory=dict)
    made: str = ""
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _painters: dict[str, list[tuple[Any, Callable[[], None]]]] = field(default_factory=dict)

    @property
    def running(self) -> bool:
        return any(one.state in (QUEUED, DOWNLOADING) for one in self.moves.values())

    def downloading(self) -> int:
        """How many files are on their way, from either kind of source."""
        ours = sum(1 for one in self.moves.values() if one.state in (QUEUED, DOWNLOADING))
        theirs = sum(1 for key, one in self.offered.items()
                     if one.level == "unknown" and key not in self.moves)
        return ours + theirs

    def summarize(self) -> None:
        """Redraw what counts the files on their way."""
        self.repaint(FOOT, RAIL)

    def paint(self, key: str, holder: Any, draw: Callable[[], None]) -> None:
        """Draw `draw` in `holder` now, and again each time `repaint` is told `key` changed."""
        live = [one for one in self._painters.get(key, []) if not one[0].is_deleted]
        self._painters[key] = [*live, (holder, draw)]
        with holder:
            draw()

    def painted(self, key: str) -> bool:
        """Whether anything alive is drawn under `key`."""
        return any(not holder.is_deleted for holder, _draw in self._painters.get(key, []))

    def repaint(self, *keys: str) -> None:
        for key in keys:
            live = [(holder, draw) for holder, draw in self._painters.get(key, [])
                    if not holder.is_deleted]
            self._painters[key] = live
            for holder, draw in live:
                holder.clear()
                with holder:
                    draw()

    def repaint_all(self) -> None:
        self.repaint(*self._painters)

    def move(self, key: str, state: str, reason: str = "") -> None:
        self.moves[key] = Move(state, reason)
        self.repaint(key, FOOT, RAIL)

    def forget(self, key: str) -> None:
        self.moves.pop(key, None)
        self.repaint(key, FOOT, RAIL)


@dataclass(frozen=True)
class Standing:
    """What a row says in place of its end glyph while its file is on the way, wants the
    person, or failed."""

    word: str
    level: str
    hint: str = ""
    percent: int | None = None
    acts: tuple[dict[str, Any], ...] = ()
    retry: bool = False


def standing_of(tray: Tray, key: str) -> Standing | None:
    """None where the file is not on its way, in which case the row draws its glyph."""
    move = tray.moves.get(key)
    if move is not None:
        if move.state == FAILED:
            return Standing(t("manager.discovery.state.failed"), "bad", move.reason, retry=True)
        return Standing(t(f"manager.discovery.state.{move.state}"), "unknown")
    offered = tray.offered.get(key)
    if offered is None or not offered.state or offered.level in ("", "on"):
        return None
    text = str(offered.state.get("text") or "")
    hint = str(offered.state.get("hint") or "")
    acts = tuple(offered.row.get("acts") or ())
    if offered.level == "unknown":
        return Standing(text or t("manager.discovery.state.downloading"), "unknown", hint,
                        percent=offered.progress)
    return Standing(text, offered.level, hint, acts=acts,
                    retry=offered.level == "bad" and not acts)


def tray_of(state: dict[str, Any], vps_id: str) -> Tray:
    return state.setdefault("downloads", {}).setdefault(vps_id, Tray())


def key_of(source: str, kind: str, size: str) -> str:
    return f"{source}:{kind}:{size}"


def pick_of(offer: dict[str, Any]) -> Pick:
    kind, source, size = str(offer["kind"]), str(offer["source"]), str(offer.get("size") or "")
    return Pick(key_of(source, kind, size), kind, source, size)


def media_order(kind: str) -> tuple[int, int]:
    kinds = [spec.kind for spec in MEDIA_SPECS]
    first = FIRST_MEDIA.index(kind) if kind in FIRST_MEDIA else len(FIRST_MEDIA)
    return (first, kinds.index(kind) if kind in kinds else len(kinds))


@dataclass(frozen=True)
class Catalogs:
    """The built-in sources being asked, by id, and the media kinds they hold that the
    library keeps, in the order the kinds are drawn. `failed` is why the sources could not be
    read."""

    asked: dict[str, dict[str, Any]] = field(default_factory=dict)
    kinds: list[str] = field(default_factory=list)
    failed: BaseException | None = None


async def catalogs_asked(library: Any) -> Catalogs:
    """Which built-in sources are being asked, and for which kinds."""
    try:
        sources = await offload.io(library.media_sources)
    except Exception as exc:  # noqa: BLE001 - the files still draw
        logger.warning("manager: could not read the media sources", exc_info=True)
        return Catalogs(failed=exc)
    asked = {str(one["id"]): one for one in sources
             if one.get("enabled") and not one.get("extension")}
    holds = {kind for one in asked.values() for kind in one.get("kinds") or ()}
    return Catalogs(asked, sorted(holds & library.kept_kinds()["media"], key=media_order))


async def game_offers(library: Any, game_id: str, vps_id: str, name: str,
                      kinds: list[str]) -> dict[str, Any] | BaseException:
    """What every source has for a game of the library, in one call; or why it could not
    be read. With no kinds wanted there is nothing to ask: an empty list to the API would
    mean every kind."""
    if not kinds:
        return {"game_id": game_id, "vps_id": vps_id, "sources": []}
    try:
        return await offload.io(library.game_offers, game_id, vps_id, name, kinds)
    except Exception as exc:  # noqa: BLE001 - the files still draw
        logger.warning("manager: could not read what the sources have for %s", vps_id,
                       exc_info=True)
        return exc


async def offers_for(library: Any, vps_id: str, plan: Catalogs,
                     combined: dict[str, Any] | BaseException | None = None,
                     ) -> tuple[dict[str, list[dict[str, Any]]], dict[str, dict[str, Any]],
                                BaseException | None]:
    """What the built-in sources have for this game, by media kind in the order the kinds are
    drawn; the sources by id; and what went wrong.

    Only the kinds some source holds and the library keeps are asked about. `combined` is the
    answer of `game_offers`, from which they are read; without it each kind is asked once.
    """
    if plan.failed is not None:
        return {}, {}, plan.failed
    if combined is None:
        found = await asyncio.gather(
            *(offload.io(library.media_offers, vps_id, kind) for kind in plan.kinds),
            return_exceptions=True)
        failed = next((one for one in found if isinstance(one, BaseException)), None)
        if failed is not None:
            logger.warning("manager: could not read what the sources have for %s", vps_id,
                           exc_info=failed)
        by_kind = {kind: list(one) for kind, one in zip(plan.kinds, found, strict=True)
                   if not isinstance(one, BaseException) and one}
        return by_kind, plan.asked, failed
    if isinstance(combined, BaseException):
        return {}, plan.asked, combined
    by_kind = {}
    failed = None
    for source in combined.get("sources") or ():
        if source.get("extension") or source["id"] not in plan.asked:
            continue
        for each in source.get("kinds") or ():
            if each.get("error"):
                failed = failed or RuntimeError(each["error"])
            elif each["kind"] in plan.kinds:
                by_kind.setdefault(each["kind"], []).extend(each.get("offers") or ())
    return ({kind: by_kind[kind] for kind in plan.kinds if by_kind.get(kind)}, plan.asked,
            failed)


def end(tray: Tray, pick: Pick, rest: Callable[[], None],
        retry: Callable[[], Awaitable[Any]],
        act: Callable[[str], Callable[[], Awaitable[Any]]] | None = None) -> None:
    """The end of a row: `rest` until its file is on the way, then what the file is doing,
    with the one thing to do about a failure or a wait."""
    holder = ui.row().classes("items-center gap-1 no-wrap shrink-0 manager-row-end")

    def draw() -> None:
        said = standing_of(tray, pick.key)
        if said is None:
            rest()
            return
        with ui.column().classes("gap-0 items-end"):
            if said.percent is not None:
                ui.label(f"{said.percent}%").classes("manager-help")
                with ui.element("div").classes("manager-bar manager-bar--row"):
                    ui.element("div").style(f"width: {said.percent}%")
            elif said.level == "unknown":
                with ui.row().classes("items-center gap-1 no-wrap"):
                    ui.spinner(size="xs")
                    ui.label(said.word).classes("manager-help")
            else:
                panel.state(said.word, said.level, hint=said.hint)()
        for each in said.acts:
            if act is not None:
                panel.action(str(each["label"]), act(str(each["key"])), icon=verbs.GO,
                             inline=True, hint=t("manager.discovery.source_act.help"))()
        if said.retry:
            panel.icon_action(t("manager.page.try_again"), retry, icon=verbs.RESET,
                              hint=t("manager.discovery.try_again.help"))()

    tray.paint(pick.key, holder, draw)


def foot(context: dict[str, Any], tray: Tray, add: Callable[[], Awaitable[Any]],
         end_hosted: Callable[[], Awaitable[Any]] | None = None, *,
         offer_add: bool = True) -> None:
    """The foot of the page. It says how many files are on their way, with Stop, while any
    are; for a game that is not in the library it offers to add it, unless `offer_add` is
    False. Otherwise it is empty."""
    in_library = bool(context["discovery"]["is_in_library"])
    offers = offer_add and not in_library
    holder = ui.row().classes(
        "items-center gap-x-3 gap-y-1 w-full manager-draft-bar manager-draft-bar--foot")

    def draw() -> None:
        count = tray.downloading()
        holder.set_visibility(bool(count) or offers)
        if count:
            ui.label(t("manager.discovery.downloading_n", count=count)).classes(
                "manager-draft-said grow")
            panel.action(t("word.stop"), _stop(tray, end_hosted), icon=verbs.STOP,
                         enabled=not tray.stopping, hint=t("manager.discovery.stop.help"))()
        elif offers:
            ui.label(t("manager.discovery.absent")).classes("manager-draft-said grow")
            panel.draft_button(t("manager.discovery.add"), add, icon=verbs.ADD, primary=True)

    tray.paint(FOOT, holder, draw)


def _stop(tray: Tray, end_hosted: Callable[[], Awaitable[Any]] | None) -> Callable[[], Any]:
    async def stop() -> None:
        tray.stopping = True
        tray.repaint(FOOT)
        if end_hosted is not None:
            await end_hosted()

    return stop


async def redraw(state: dict[str, Any]) -> None:
    again = state.get("redraw_discovery")
    if not callable(again):
        return
    try:
        await again()
    except RuntimeError:
        logger.info("manager: the page was left before it could be redrawn")


async def settle(state: dict[str, Any], entry_id: str) -> None:
    """After something changed what the entry holds, without the game or the rail changing:
    its grid row and the open section are drawn again where they stand. The whole pane is
    redrawn where that cannot be done in place."""
    inplace = state.get("settle_discovery")
    try:
        if callable(inplace) and await inplace(entry_id):
            return
    except RuntimeError:
        logger.info("manager: the page was left before it could be settled")
        return
    await redraw(state)


@on_page
async def make_game(context: dict[str, Any]) -> dict[str, Any] | None:
    """A game from this entry, in the place new games go; None where the person declined
    the choice of place or the game could not be made, which is said on screen."""
    library, row = context["library"], context["discovery"]
    where = await import_dialog.ask_where(library)
    if where is None:
        return None
    try:
        made = await offload.io(library.add_game_from_vps, row["id"], where)
    except Exception as exc:  # noqa: BLE001
        logger.warning("manager: could not add %s", row["id"], exc_info=True)
        ui.notify(t("manager.discovery.add_failed", name=row["name"]), caption=why(exc),
                  type="negative")
        return None
    return made


@on_page
async def ensure_game(context: dict[str, Any], tray: Tray,
                      game_id: str) -> tuple[str, str]:
    """The game this entry goes into and its folder, empty where the library has not listed
    it. The library is asked rather than the row, which knows nothing of the library until it
    is compared. Empty too where the game could not be read or the place was declined, which
    is said on screen. Held under `tray.lock`, so two arrivals make one game."""
    library, row, state = context["library"], context["discovery"], context["state"]
    if game_id or tray.made:
        return game_id or tray.made, ""
    try:
        owned, _other = await offload.io(library.owned, [row["id"]])
    except Exception as exc:  # noqa: BLE001
        logger.warning("manager: could not tell whether %s is in the library", row["id"],
                       exc_info=True)
        ui.notify(t("manager.discovery.game_unread"), caption=why(exc), type="negative")
        return "", ""
    made, folder = str((owned.get(row["id"]) or {}).get("game_id") or ""), ""
    if not made:
        added = await make_game(context)
        if added is None:
            return "", ""
        made, folder = str(added.get("id") or ""), str(added.get("folder") or "")
    tray.made = made
    await redraw(state)
    return made, folder


@on_page
async def get(context: dict[str, Any], tray: Tray, game_id: str, picks: list[Pick]) -> None:
    """Get these files, one after another, into the game that holds this entry.

    A press while another download runs waits its turn behind it. A game not in the library
    is made first, once. Stop ends the files not yet started.
    """
    library, row, state = context["library"], context["discovery"], context["state"]
    wanted = [one for one in picks
              if one.key not in tray.moves or tray.moves[one.key].state == FAILED]
    if not wanted:
        return
    for one in wanted:
        tray.move(one.key, QUEUED)
    mine = {one.key for one in wanted}
    landed = False
    try:
        async with tray.lock:
            game_id, _folder = await ensure_game(context, tray, game_id)
            if not game_id:
                return
            await hand_over(context, tray, game_id, [one for one in wanted if one.row])
            for one in (one for one in wanted if not one.row):
                if tray.stopping:
                    break
                tray.move(one.key, DOWNLOADING)
                try:
                    await offload.io(library.fetch_media, game_id, None, one.kind, one.source,
                                     row["id"], one.size)
                except Exception as exc:  # noqa: BLE001 - one refusal does not end the rest
                    logger.warning("manager: could not get %s for %s", one.key, row["id"],
                                   exc_info=True)
                    tray.move(one.key, FAILED, why(exc))
                    continue
                tray.forget(one.key)
                landed = True
    finally:
        for key in [key for key in mine if key in tray.moves
                    and tray.moves[key].state in (QUEUED, DOWNLOADING)]:
            del tray.moves[key]
        if not tray.running:
            tray.stopping = False
        tray.repaint_all()
    if landed:
        await settle(state, row["id"])


@on_page
async def hand_over(context: dict[str, Any], tray: Tray, game_id: str,
                    picks: list[Pick]) -> None:
    """Give each extension the files wanted from its source as one batch. What it answers
    to each row from here on is drawn from the offers, not from the move."""
    library, row = context["library"], context["discovery"]
    by_source: dict[tuple[str, str], list[Pick]] = {}
    for one in picks:
        offered = tray.offered.get(one.key)
        if offered is None:
            tray.move(one.key, FAILED, t("manager.discovery.offer_gone"))
            continue
        by_source.setdefault((offered.extension, offered.key), []).append(one)
    for (extension, key), batch in by_source.items():
        try:
            answer = await offload.io(
                library.source_take_many, extension, key,
                [{"kind": one.kind, "key": one.row} for one in batch], game_id, row["id"],
                row["name"])
        except Exception as exc:  # noqa: BLE001 - one source's refusal does not end the rest
            logger.warning("manager: %s.%s would not take %d files", extension, key, len(batch),
                           exc_info=True)
            for one in batch:
                tray.move(one.key, FAILED, why(exc))
            continue
        if answer.get("message"):
            ui.notify(str(answer["message"]), type="info")
    refresh = context.get("refresh_hosted")
    if picks and callable(refresh):
        await refresh()


def try_again(context: dict[str, Any], tray: Tray, game_id: str,
              pick: Pick) -> Callable[[], Awaitable[Any]]:
    async def again() -> None:
        tray.forget(pick.key)
        await get(context, tray, game_id, [pick])

    return again


def of_listing(listed_as: str) -> tuple[tuple[str, ...], str]:
    """Our kinds behind a VPS kind, and which inventory holds them. A table is an asset kind
    of its own, which `asset_kind` restricts an import to like any other."""
    kind = vps_kinds.BY_LISTING.get(listed_as)
    return (kind.ours, kind.held_in) if kind else (("table",), vps_kinds.ASSET)


def media_ours(listed_as: str, media_kind: str) -> tuple[str, ...]:
    """The media kinds a release listed under `listed_as` can be: a topper is a picture or a
    video, so a file dropped on Topper may be either. A kind VPS lists nothing for is its own."""
    return of_listing(listed_as)[0] if listed_as in vps_kinds.BY_LISTING else (media_kind,)


def use_a_file(context: dict[str, Any], game_id: str, holder: Any, *, ours: tuple[str, ...],
               held_in: str, add_table: bool = False,
               match: import_dialog.ReleaseMatch | None = None,
               opened: Callable[[], tuple[str, str]] | None = None) -> Callable[[], None]:
    """Make `holder` take a file dropped on it as this kind, and return the control that
    asks for one. The import dialog offers `match` as the release the file is, and the file
    is tied to whatever it holds when the import is done. Where `match` names no release,
    `opened` is asked for the one whose page was opened for this game, as its id and words.
    It goes into the game whether or not the game is in the library yet.
    """
    @on_page
    async def arrived(drop: uploads.Drop) -> None:
        library, state = context["library"], context["state"]
        analysis: dict[str, Any] = {}
        single = slot_of(ours, held_in, drop.name)
        if not (held_in == vps_kinds.MEDIA and single):
            # A kind that names its own slot is not read off the file's name: the analyzer
            # rejects exactly the file a slot is for, one whose name says nothing.
            read = await uploads.analyzed(library, drop.upload_id)
            if read is None:
                return
            analysis = read
        tray = tray_of(state, context["discovery"]["id"])
        async with tray.lock:
            made, folder = await ensure_game(context, tray, game_id)
        if not made:
            await run.io_bound(library.abort_upload, drop.upload_id)
            return
        if not folder:
            found: dict[str, Any] = next(
                (one for one in library.games if str(one.get("id")) == made), {})
            folder = str(found.get("folder") or "")

        release = replace(match) if match else None
        if release is not None and release.vps_file_id:
            release.reason = t("manager.import_dialog.reason_from_release", vps=release.vps)
        elif release is not None and opened is not None:
            release.vps_file_id, release.label = opened()
            if release.vps_file_id:
                release.reason = t("manager.import_dialog.reason_opened", vps=release.vps)

        async def tie(report: dict[str, Any], plan: dict[str, Any]) -> None:
            await _tie(library, made, context["discovery"]["id"], folder,
                       release.vps_file_id if release else "", ours, report, plan)

        async def done() -> None:
            await settle(state, context["discovery"]["id"])

        await uploads.arrived(
            library, drop.upload_id, analysis=analysis, source=drop.name, file_count=drop.count,
            on_done=done, game_id=made, game_dir=folder,
            asset_kind=single if held_in == vps_kinds.ASSET else "",
            media_kind=single if held_in == vps_kinds.MEDIA else "",
            add_table=add_table, on_imported=tie, release=release)

    mediasource.take_drops(holder, arrived)

    def draw() -> None:
        heard = uploads.listener(arrived)
        panel.action(t("manager.discovery.use_file"), heard, icon=verbs.FROM_FILE,
                     hint=t("manager.discovery.use_file.help"),
                     js="() => window.__managerChoose(false, emit, true)")()

    return draw


def slot_of(ours: tuple[str, ...], held_in: str, name: str) -> str:
    """The one kind a dropped file is for: the only one `ours` holds, or for a media kind
    that comes as a picture or a video, the one whose family has the file's extension.
    A file no family takes goes to the first, so the slot's own message says what it takes."""
    if len(ours) == 1:
        return ours[0]
    if held_in != vps_kinds.MEDIA:
        return ""
    suffix = Path(name).suffix.lower()
    return next((spec.kind for spec in MEDIA_SPECS
                 if spec.kind in ours and suffix in spec.family), ours[0])


@on_page
async def _tie(library: Any, game_id: str, entry_id: str, folder: str, vps_file_id: str,
               ours: tuple[str, ...], report: dict[str, Any], plan: dict[str, Any]) -> None:
    """Record that what an import imported is this record. A tree of files is not one
    file, so a kind that arrives as one has nothing to tie."""
    if not vps_file_id or not game_id:
        return
    brought = set(report.get("imported") or ())
    try:
        if "table" in ours:
            await _tie_tables(library, game_id, vps_file_id, report, plan)
        else:
            for item in plan.get("items") or ():
                kind = item.get("media_kind") or item.get("kind")
                if item.get("kind") not in brought or kind not in ours:
                    continue
                if item.get("action") not in _FILE_ACTIONS:
                    continue
                path = PurePosixPath(
                    Path(str(item["destination"])).relative_to(folder)).as_posix()
                await run.io_bound(library.set_asset_source, game_id, path, vps_file_id)
        await run.io_bound(opened_pages.forget, entry_id, vps_file_id)
    except Exception as exc:  # noqa: BLE001 - the file is in; only the tracking failed
        logger.warning("manager: could not tie %s to %s", vps_file_id, game_id, exc_info=True)
        ui.notify(t("manager.discovery.tie_failed"), caption=why(exc), type="warning")


async def _tie_tables(library: Any, game_id: str, vps_file_id: str,
                      report: dict[str, Any], plan: dict[str, Any]) -> None:
    tables = [*(report.get("added_tables") or ()),
              *(report.get("patched_tables") or ()),
              *(str(one["table"]["id"]) for one in report.get("replaced_tables") or ())]
    if not tables:
        names = {Path(str(item["destination"])).name for item in plan.get("items") or ()
                 if item.get("action") == "replace_vpx"}
        rows = await offload.io(library.tables_for, game_id)
        tables = [str(one["id"]) for one in rows if one.get("filename") in names]
    for table_id in tables:
        await run.io_bound(library.set_table_source, game_id, str(table_id), vps_file_id)
