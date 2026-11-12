"""The Online tab of a file picker: what VPS and VPinMediaDB hold of one kind of file for a
game, and the VPS file picked there that the next file taken is matched to.

The tab is drawn in the picker's own words, for the kind being filled. It never says
"release": a file the game holds and a file VPS lists are both files of the kind.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path, PurePath
from typing import Any, Protocol

from nicegui import run, ui

from common import icons
from common.failures import why
from common.games import asset_registry, asset_resolver, opened_pages
from common.i18n import t
from common.media.media_specs import canonical_kind
from common.online import sources as source_registry
from common.online import vps_formats, vps_kinds
from common.online.vpsdb_media import MANIFEST_KINDS
from manager.extensions import ext_lists, ext_page
from manager.kit import (
    candidates,
    deeplink,
    game_tables,
    kind_words,
    offload,
    panel,
    pick_pane,
    verbs,
)
from manager.kit.on_page import on_page
from manager.library import (
    import_dialog,
    match_group,
    release_match,
    vps_match,
    vps_records,
)

logger = logging.getLogger("vpinfe.manager.library.picker_online")

SIZE_NAMES = {"4k": "config.media.playfield_resolution.choice.4k",
              "1k": "config.media.playfield_resolution.choice.1k"}

FULL = panel.FULL

# The kinds whose resolution the Playfield Video setting decides, not the picture's.
_VIDEOS = frozenset({"playfield_video", "scoreview_video"})


@dataclass(frozen=True)
class Listing:
    """What VPS and VPinMediaDB call one kind of file.

    `ours` is the kind the words are under. `listed_as` is VPS's own list for it and
    `manifest` VPinMediaDB's, each empty where that source does not carry the kind.
    """

    ours: str
    media: bool
    listed_as: str = ""
    manifest: str = ""

    def offered(self) -> bool:
        return bool(self.listed_as or self.manifest)


def listing_for(kind: str, media: bool) -> Listing:
    """The listing of a picker's kind: `media` is whether the file is a media slot's."""
    if media:
        ours = canonical_kind(kind)
        found = vps_kinds.BY_OURS.get(ours)
        listed = found.listed_as if found and found.held_in == vps_kinds.MEDIA else ""
        return Listing(ours, True, listed, ours if ours in MANIFEST_KINDS else "")
    if kind == "table":
        return Listing(kind, False, "tableFiles")
    if kind == "alt_color":
        return Listing(kind, False, "altColorFiles")
    found = vps_kinds.BY_OURS.get({"alt_sound": "altsound"}.get(kind, kind))
    listed = found.listed_as if found and found.held_in == vps_kinds.ASSET else ""
    return Listing(kind, False, listed)


@dataclass(frozen=True)
class VpsPick:
    """The VPS file the next file taken is matched to, with the words that name it."""

    file_id: str
    label: str
    picture: str = ""


@dataclass(frozen=True)
class HeldFile:
    """A file the game holds of the kind: its name, the VPS file it is matched to, and
    who put it there."""

    name: str
    file_id: str = ""
    origin: str = ""


@dataclass
class Found:
    """Everything one read of the tab learned. A part that could not be read is empty and
    says so, so what could be read is still drawn."""

    entry: dict[str, Any] = field(default_factory=dict)
    guess: dict[str, Any] = field(default_factory=dict)
    records: list[dict[str, Any]] = field(default_factory=list)
    unread: bool = False
    held: list[HeldFile] = field(default_factory=list)
    slot: dict[str, Any] = field(default_factory=dict)
    offers: dict[str, Any] = field(default_factory=dict)
    offers_error: str = ""
    formats: set[str] = field(default_factory=set)
    changed: bool = False
    size: str = ""


class Host(Protocol):
    """What the picker the tab is in gives it."""

    library: Any
    context: dict[str, Any]
    game_id: str
    kind: str
    source_kind: str
    listing: Listing
    built_in: bool
    glyph: str
    family: str

    @property
    def destination(self) -> str: ...

    def shown(self) -> bool: ...
    def entry(self, card: pick_pane.Card | None, commit: Callable[[], Any], *,
              discard: Callable[[], Any] | None = None, filename: str = "",
              arrives: bool = False) -> Any: ...
    def choose(self, entry: Any) -> None: ...
    def choose_trip(self, trip: pick_pane.Trip | None, row: Any) -> None: ...
    def forget_trip(self) -> None: ...
    async def finish_plain(self, message: str) -> None: ...
    async def take_offer(self, offer: dict[str, Any], source_name: str, vps_id: str) -> None: ...

def _guarded(call: Callable[..., Any], default: Any, *args: Any) -> Any:
    try:
        return call(*args)
    except Exception:  # noqa: BLE001 - a part that cannot be read leaves the rest drawn
        logger.debug("Online tab: %s did not answer", getattr(call, "__name__", call),
                     exc_info=True)
        return default


def read(library: Any, game: dict[str, Any], listing: Listing, *, asks_sources: bool,
         source_kind: str, name: str) -> Found:
    """Read what the tab draws. Blocking."""
    found = Found()
    game_id = str(game.get("id") or "")
    vps_id = str(game.get("vps_id") or "")
    guess = str((game.get("discovered") or {}).get("vps_id") or "")
    if not vps_id and guess and (game.get("overrides") or {}).get("alt_vps_id", "") is not None:
        found.guess = _guarded(library.vps_entry, {}, guess) or {}
        found.guess = {**found.guess, "vps_id": guess} if found.guess else {}
    if vps_id:
        found.entry = _guarded(library.vps_entry, {}, vps_id) or {}
    if vps_id and listing.listed_as:
        try:
            found.records = list(library.vps_releases(vps_id, listing.listed_as))
        except Exception:  # noqa: BLE001
            logger.debug("Online tab: VPS did not answer", exc_info=True)
            found.unread = True
    held = _guarded(library.vps_held, None, game_id) if game_id else None
    if listing.media and game_id:
        found.slot = (_guarded(library.media_for, {}, game_id, None) or {}).get(listing.ours) or {}
    if listing.listed_as:
        found.held = [HeldFile(str(one.get("name") or ""), str(one.get("vps_file_id") or ""),
                               _origin_of(found.slot, str(one.get("name") or "")))
                      for one in vps_records.held_of(held, listing.listed_as)]
    elif found.slot.get("present"):
        found.held = [HeldFile(str(found.slot.get("file") or ""),
                               str(found.slot.get("matched_to") or ""),
                               str(found.slot.get("origin") or ""))]
    if listing.listed_as == "tableFiles":
        for row in _guarded(library.tables_for, [], game_id) or []:
            found.formats |= {str(one) for one in row.get("vps_formats") or ()}
    if listing.media and game_id:
        found.changed = any(one.get("kind") == listing.ours for one in
                            _guarded(library.picture_changes, [], game_id) or [])
        settings = ((_guarded(library.config_values, {}) or {}).get("media") or {})
        found.size = str(settings.get("playfield_video_resolution" if listing.ours in _VIDEOS
                                      else "playfield_resolution") or "")
    if vps_id and asks_sources:
        try:
            found.offers = library.game_offers(game_id, vps_id, name, [source_kind]) or {}
        except Exception as exc:  # noqa: BLE001 - said on the tab
            found.offers_error = why(exc)
    return found


def _origin_of(slot: dict[str, Any], name: str) -> str:
    return str(slot.get("origin") or "") if slot.get("present") and slot.get("file") == name \
        else ""


class Online:
    """The tab, for one picker: it draws the game's match to VPS, what the game has and
    what else is available, and holds the VPS file picked."""

    def __init__(self, host: Host) -> None:
        self.host = host
        self.pick: VpsPick | None = None
        self.found = Found()
        self.head: Any = None
        self.well: Any = None
        self.known: list[dict[str, Any]] | None = None
        self._asked = 0

    def choose(self, record: dict[str, Any]) -> None:
        self.pick = VpsPick(str(record.get("vps_file_id") or ""), vps_records.told(record),
                            str(record.get("img_url") or ""))

    def clear(self) -> None:
        self.pick = None

    # --- the file taken ---------------------------------------------------------------

    def vps_kind(self) -> str:
        return kind_words.vps_name(self.host.listing.ours, self.host.listing.media)

    def release(self, source: str) -> import_dialog.ReleaseMatch:
        """What the import dialog is told of the file arriving: the VPS file picked here, or
        none, and the picker it asks the other with."""
        listing = self.host.listing
        match = import_dialog.ReleaseMatch(
            partial(self.chooser, source), kind_words.label(listing.ours, listing.media),
            self.vps_kind(), picture_of=self._picture_of)
        if self.pick is not None:
            match.vps_file_id = self.pick.file_id
            match.label = self.pick.label
            match.reason = t("manager.import_dialog.reason_picked", vps=self.vps_kind())
        return match

    def _picture_of(self, vps_file_id: str) -> str:
        return next((str(one.get("img_url") or "") for one in self.found.records
                     if one.get("vps_file_id") == vps_file_id), "")

    async def chooser(self, source: str, current: str) -> tuple[str, str] | None:
        """Ask which VPS file a file taken here is: its id and words, `("", "")` for none, or
        None where the person left."""
        game = self.game()
        listing = self.host.listing
        if not self.found.records and listing.listed_as and game.get("vps_id"):
            try:
                self.found.records = list(await offload.io(
                    self.host.library.vps_releases, str(game["vps_id"]), listing.listed_as))
            except Exception as exc:  # noqa: BLE001
                ui.notify(t("manager.picker.vps_unread"), caption=why(exc), type="warning")
                return None
        picked = await release_match.ask(
            release_match.Yours(str(game.get("name") or ""), source,
                                glyph=self.host.glyph or icons.GAMES),
            list(self.found.records), bound=current, formats=self.found.formats,
            empty=t(self._said("none" if game.get("vps_id") else "waits"),
                    kinds=kind_words.in_a_sentence(self._kinds())),
            files=True)
        if not isinstance(picked, str):
            return None
        record = next((one for one in self.found.records
                       if one.get("vps_file_id") == picked), None)
        return (picked, vps_records.told(record)) if record else ("", "")

    # --- reading ----------------------------------------------------------------------

    def game(self) -> dict[str, Any]:
        """The game as it is now: a match made here changes it under the picker."""
        game = self.host.context["game"]
        games = _guarded(lambda: list(self.host.library.games), [])
        return next((one for one in games if one.get("id") == self.host.game_id), game)

    def _from_extensions(self) -> list[dict[str, Any]]:
        """The switched-on sources from extensions that offer this kind."""
        names = source_registry.names_for(self.host.source_kind)
        return [item for item in self.known or []
                if item.get("extension") and item.get("enabled")
                and names & set(item.get("kinds") or ())]

    async def draw(self, body: ui.column) -> None:
        body.clear()
        with body:
            self.head = ui.column().classes("w-full gap-1")
            self.well = ui.column().classes("w-full gap-1 manager-source-offers")
        try:
            self.known = await offload.io(self.host.library.media_sources)
        except Exception:  # noqa: BLE001 - the names label the rows; the rows still draw
            self.known = []
        if self.host.shown():
            await self.refresh()

    async def refresh(self) -> None:
        self._asked += 1
        asked = self._asked
        game = self.game()
        found = await offload.io(partial(
            read, self.host.library, game, self.host.listing,
            asks_sources=bool(self.host.built_in or self._from_extensions()),
            source_kind=self.host.source_kind, name=str(game.get("name") or "")))
        if asked != self._asked or not self.host.shown():
            return
        self.found = found
        if self.pick and not any(str(one.get("vps_file_id")) == self.pick.file_id
                                 for one in found.records):
            self.pick = None
            self.host.forget_trip()
        self._paint_head()
        self._paint_well()

    # --- the head: which game this is the list of ------------------------------------

    def _paint_head(self) -> None:
        self.head.clear()
        game = self.game()
        vps_id = str(game.get("vps_id") or "")
        declared = (game.get("overrides") or {}).get("alt_vps_id", "") is None
        with self.head:
            if self.found.entry.get("name"):
                self._named_head(game)
                return
            panel.facts(ui, match_group.unmatched(
                match_group.game_match_gap(vps_id, declared, bool(self.found.entry))))
            if not vps_id and self.found.guess:
                self._looks_like()
            match_group.change_match(self._match_another, matched=bool(vps_id) or declared)()

    def _named_head(self, game: dict[str, Any]) -> None:
        """"A band naming the game the list is of, with the way to a different one at its end."""
        entry = self.found.entry
        made = game_tables.made(entry) or game_tables.made(game)
        named = str(entry.get("name") or game.get("name") or "")
        with ui.row().classes("items-start gap-2 w-full no-wrap manager-source-head"):
            with ui.column().classes("gap-0 min-w-0 grow"):
                ui.label(t("manager.picker.head_for", kinds=self._kinds())) \
                    .classes("manager-fact-heading")
                ui.label(named).classes("manager-cell-named")
                if made:
                    ui.label(made).classes("manager-cell-said manager-cell-made")
            ui.label(t("manager.picker.wrong_game")) \
                .classes("manager-link cursor-pointer shrink-0") \
                .on("click", self._match_another)

    def _looks_like(self) -> None:
        guess = self.found.guess
        with ui.row().classes("items-center gap-3 w-full no-wrap manager-source-row"):
            candidates.thumb(str(guess.get("img_url") or ""), icons.GAMES, vps_match.NO_PICTURE)
            with ui.column().classes("gap-0 min-w-0 grow"):
                ui.label(str(guess.get("name") or "")).classes("manager-source-name")
                ui.label(t("manager.discovery.unmatched.looks_like",
                           made=game_tables.made(guess))).classes("manager-help")
            panel.action(t("manager.picker.match"),
                         partial(self._match_to, str(guess.get("vps_id") or "")),
                         icon=verbs.MATCH, inline=True, hint=t(
                             "manager.picker.match.help", name=str(guess.get("name") or "")))()

    @on_page
    async def _match_to(self, vps_id: str) -> None:
        library = self.host.library
        try:
            await match_group.match_to(library, self.host.game_id, vps_id)
        except Exception as exc:  # noqa: BLE001
            ui.notify(t("manager.workbench.save_failed"), caption=why(exc), type="negative")
            return
        await self._matched()

    @on_page
    async def _match_another(self) -> None:
        await match_group.pick_a_match(self.host.context)
        await self.refresh()

    async def _matched(self) -> None:
        rebuild = self.host.context.get("rebuild")
        if rebuild is not None:
            await rebuild()
        await self.refresh()

    # --- the well: what the game has and what else is available -------------------

    def _kinds(self) -> str:
        return kind_words.plural(self.host.listing.ours, self.host.listing.media)

    def _said(self, which: str) -> str:
        """The key of a line about the catalogs the kind is in. What is waited for names the
        catalog the kind is first in; what is missing names one only where it is the only
        one, and is general where several sources were asked."""
        listing = self.host.listing
        if which == "waits":
            where = "vps" if listing.listed_as else "media" if listing.manifest else "other"
        else:
            where = "vps" if listing.listed_as and not listing.manifest \
                else "media" if listing.manifest and not listing.listed_as else "other"
        return f"manager.picker.{which}_{where}"

    def _paint_well(self) -> None:
        self.well.clear()
        game = self.game()
        sentence = kind_words.in_a_sentence(self._kinds())
        with self.well:
            if not str(game.get("vps_id") or ""):
                ui.label(t(self._said("waits"), kinds=sentence)).classes("manager-help")
                return
            used = {file.file_id for file in self.found.held if file.file_id}
            offers = self._database_offers()
            rows = [one for one in vps_records.newest_first(self.found.records)
                    if self._playable(one)]
            for offer, source in offers:
                self._database_row(offer, source)
            for record, nested in release_match.in_lineage(rows):
                self._vps_row(record, nested, str(record.get("vps_file_id") or "") in used)
            if self.found.unread:
                panel.line(t("manager.picker.vps_unread"))
            failed = self._catalog_failed()
            if failed:
                panel.line(t("manager.mediasource.sources_failed"))
            drew = self._extension_sections(game)
            if not (offers or rows or self.found.unread or self.found.offers_error
                    or failed or drew):
                ui.label(t(self._said("none"), kinds=sentence)).classes("manager-help")

    def _playable(self, record: dict[str, Any]) -> bool:
        if self.host.listing.listed_as != "tableFiles":
            return True
        return vps_formats.fits(record.get("format"), self.found.formats)

    def _used_as(self) -> str:
        listing = self.host.listing
        return t("manager.mediasource.used_as",
                 kind=kind_words.label(listing.ours, listing.media))

    def _vps_row(self, record: dict[str, Any], nested: bool, used: bool) -> None:
        """A file VPS lists. Picking it says where to fetch it: the file itself comes from
        the other site, so the pick is complete only once the person has dropped it."""
        features = self.host.listing.listed_as == "tableFiles"
        detail = game_tables.JOIN.join(part for part in (
            _day(record), vps_records.site_of(record)[0],
            vps_records.what_it_is(record, features=features)) if part)
        site, page = vps_records.site_of(record)

        def pick() -> None:
            self.choose(record)
            self.host.choose_trip(
                pick_pane.Trip(site, page, partial(self._opened, record)) if page else None,
                row)

        row = candidates.choice(
            str(record.get("img_url") or ""),
            vps_records.told(record) or t("manager.discovery.unnamed_file"), detail, pick,
            glyph=self.host.glyph or icons.GAMES, slot=True, use=None,
            more=() if page else (t("manager.picker.no_page"),),
            tag=self._used_as() if used else "", tag_here=used, missing=vps_match.NO_PICTURE)
        if nested:
            row.classes(add="manager-source-row--under")

    @on_page
    async def _opened(self, record: dict[str, Any]) -> None:
        """The page was opened, which VPS keeps track of so the game's files can say it."""
        listed = self.host.listing.listed_as
        if listed:
            vps_id = str(self.game().get("vps_id") or "")
            await run.io_bound(_guarded, opened_pages.note, None, vps_id, listed,
                               str(record.get("vps_file_id") or ""),
                               vps_records.site_of(record)[1])

    # VPinMediaDB's own files, first within the others.

    def _database_offers(self) -> list[tuple[dict[str, Any], dict[str, Any]]]:
        named = {str(item["id"]): item for item in self.known or []}
        found: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for one in self.found.offers.get("sources") or ():
            if one.get("extension"):
                continue
            for each in one.get("kinds") or ():
                for offer in each.get("offers") or ():
                    found.append((offer, named.get(str(offer["source"]), {})))
        return found

    def _catalog_failed(self) -> bool:
        return any(each.get("error") for one in self.found.offers.get("sources") or ()
                   if not one.get("extension") for each in one.get("kinds") or ())

    def _is_current(self, offer: dict[str, Any]) -> bool:
        """Whether the game already holds this offer: the slot came from VPinMediaDB, no
        picture of it has changed since, and the size is the one the settings ask for."""
        slot = self.found.slot
        if not slot.get("present") or slot.get("origin") != "vpinmediadb" or self.found.changed:
            return False
        size = str(offer.get("size") or "")
        buckets = self.host.listing.manifest in MANIFEST_KINDS \
            and MANIFEST_KINDS[self.host.listing.manifest][1]
        return not buckets or not size or size == self.found.size

    def _database_row(self, offer: dict[str, Any], source: dict[str, Any]) -> None:
        name = str(source.get("name") or offer["source"])
        root = str(source.get("url") or "").rstrip("/")
        size = str(offer.get("size") or "")
        vps_id = str(self.game().get("vps_id") or "")
        meta = " · ".join(part for part in (
            name, t(SIZE_NAMES[size]) if size in SIZE_NAMES else size) if part)
        used = self._is_current(offer)
        src = str(offer.get("url") or "")
        taken = self.host.entry(
            pick_pane.card_for(self.host.family or "image", src, str(offer.get("name") or ""),
                               meta),
            partial(self.host.take_offer, offer, name, vps_id),
            filename=str(offer.get("name") or ""))

        def pick() -> None:
            self.clear()
            taken.pick()

        async def use() -> None:
            self.clear()
            await taken.use()

        def trailing() -> None:
            if not root:
                return
            from urllib.parse import quote
            with ui.element("span").classes("shrink-0").on("click.stop", js_handler="() => {}"):
                panel.out(to=f"{root}/tree/main/{quote(vps_id, safe='')}",
                          hint=t("manager.discovery.folder_hint", site=name))()

        taken.bind(candidates.choice(
            src, str(offer.get("name") or ""), meta, pick, family=self.host.family or "image",
            glyph=self.host.glyph, slot=True, use=use, trailing=trailing,
            tag=self._used_as() if used else "", tag_here=used))

    # --- extension sources ----------------------------------------------------------

    def _extension_sections(self, game: dict[str, Any]) -> bool:
        """Draw each extension source's answer; whether anything was drawn."""
        said = {str(one["id"]): one for one in self.found.offers.get("sources") or ()
                if one.get("extension") and one.get("kinds")}
        who = {"game_id": self.host.game_id, "vps_id": str(game.get("vps_id") or ""),
               "name": str(game.get("name") or "")}
        drew = bool(self.found.offers_error)
        if self.found.offers_error:
            self._failed_line(t("manager.mediasource.sources_failed"),
                              RuntimeError(self.found.offers_error))
        for source in self._from_extensions():
            one = said.get(str(source["id"]))
            if one is None:
                self._failed_line(
                    t("manager.mediasource.source_failed", source_name=source["name"]),
                    RuntimeError(t("said.why.nothing_answers")))
                drew = True
                continue
            each = one["kinds"][0]
            if each.get("error"):
                self._failed_line(
                    t("manager.mediasource.source_failed", source_name=source["name"]),
                    RuntimeError(each["error"]))
                drew = True
            else:
                drew = self._source_section(source, each, who) or drew
        return drew

    @staticmethod
    def _failed_line(said: str, exc: Exception) -> None:
        panel.line(said, hint=why(exc))

    def _kind_for(self, source: dict[str, Any]) -> str:
        """The name this source gave the kind, which is what it is asked in."""
        offered = set(source.get("kinds") or ())
        if self.host.source_kind in offered:
            return self.host.source_kind
        return sorted(source_registry.names_for(self.host.source_kind) & offered)[0]

    def _source_section(self, source: dict[str, Any], answer: dict[str, Any],
                        who: dict[str, str]) -> bool:
        """An extension source's rows, then what it says under them; whether it drew any."""
        for row in answer.get("rows") or []:
            self._source_row(source, row, who)
        if answer.get("note"):
            ui.label(str(answer["note"])).classes("manager-help")
        if answer.get("link"):
            self._source_link(source, answer["link"])
        return bool(answer.get("rows") or answer.get("note") or answer.get("link"))

    def _source_link(self, source: dict[str, Any], link: dict[str, Any]) -> None:
        if link.get("to"):
            panel.link_out(link["text"], to=link["to"])()
        elif link.get("page"):
            panel.link(link["text"], to="/manager?" + deeplink.query(
                {"view": ext_lists.page_view_key(source["extension"], link["page"])}))()
        else:
            panel.link(link["text"], to=ext_page.address(source["extension"]))()

    def _source_row(self, source: dict[str, Any], row: dict[str, Any],
                    who: dict[str, str]) -> None:
        @on_page
        async def take() -> None:
            taking = {**who, "table_id": self.host.destination}
            try:
                answer = await offload.io(
                    self.host.library.source_take, source["extension"], source["key"],
                    self._kind_for(source), row["key"], taking["game_id"], taking["vps_id"],
                    taking["name"], taking["table_id"])
            except Exception as exc:  # noqa: BLE001
                ui.notify(t("manager.mediasource.fetch_failed"), caption=why(exc),
                          type="negative")
                return
            await self._taken(source, row["key"], answer or {}, taking)

        pieces: list[str | tuple[str, ...]] = [str(source["name"])]
        if row.get("site"):
            pieces.append((row["site"], row["page"], t("manager.discovery.page_hint",
                                                       site=row["site"]))
                          if row.get("page") else row["site"])
        if row.get("note"):
            pieces.append(row["note"])
        here = bool(row.get("current"))
        picture = str(row.get("picture") or "")
        taken = self.host.entry(
            pick_pane.card_for("image", picture, str(row["name"]),
                               " · ".join(str(one) for one in pieces if isinstance(one, str))),
            take)

        async def use() -> None:
            self.clear()
            await taken.use()

        def pick() -> None:
            self.clear()
            taken.pick()

        taken.bind(candidates.row(
            picture, row["name"], candidates.credits(*pieces),
            str(row.get("tag") or "") or (self._used_as() if here else ""), pick,
            family="image", glyph=self.host.glyph, use=use, tag_here=here))

    @on_page
    async def _taken(self, source: dict[str, Any], row_key: str, answer: dict[str, Any],
                     who: dict[str, str]) -> None:
        """What the source made of a pick: a dialog of its own to open over this one, a job
        it started, or just a word."""
        if answer.get("action"):
            record = next((one for one in await offload.io(self.host.library.extensions)
                           if one.get("name") == source["extension"]), None)
            if record is None:
                ui.notify(t("said.action_failed"), type="negative")
                return
            await ext_lists.open_over(record, str(answer["action"]), [row_key], who=who)
        elif answer.get("job"):
            await self.host.finish_plain(str(answer.get("message") or t(
                "manager.mediasource.ext_started", source_name=source["name"])))
        elif answer.get("message"):
            ui.notify(str(answer["message"]))


_FILE_ACTIONS = ("copy", "replace_b2s", "zip_rom", "replace_media")
_FOLDERS = frozenset({"pupvideos", "serum", "vni"})
_ALTSOUND = "altsound"


def kinds_of(listing: Listing) -> frozenset[str]:
    """The kinds an import's plan names for the files this picker's kind stands for."""
    found = vps_kinds.BY_LISTING.get(listing.listed_as)
    return frozenset({listing.ours, *(found.ours if found else ())})


def matched_after(library: Any, game_id: str, game_dir: str, listing: Listing,
                  release: import_dialog.ReleaseMatch) -> Callable[..., Any]:
    """Once an import is done, record that what it brought of the picker's kind is the VPS
    file `release` names by then. A kind that arrives as a folder is matched by the folder's
    name, the key the game's held files are read under; a table by its id; any other file
    by its path."""
    ours = kinds_of(listing)

    @on_page
    async def matched(report: dict[str, Any], plan: dict[str, Any]) -> None:
        if not release.vps_file_id:
            return
        tables = [*(str(one) for one in report.get("added_tables") or ()),
                  *(str(one["table"]["id"]) for one in report.get("replaced_tables") or ())] \
            if "table" in ours else []
        keys: list[str] = []
        brought = set(report.get("imported") or ())
        for item in plan.get("items") or ():
            if item.get("kind") not in brought or "destination" not in item:
                continue
            if (item.get("media_kind") or item.get("kind")) not in ours:
                continue
            found = _asset_key(game_dir, item)
            if found and found not in keys:
                keys.append(found)
        try:
            for table in tables:
                await run.io_bound(library.set_table_source, game_id, table, release.vps_file_id)
            for key in keys:
                where = await run.io_bound(_altsound_folder, game_dir) \
                    if key == _ALTSOUND else key
                await run.io_bound(library.set_asset_source, game_id, where,
                                   release.vps_file_id)
        except Exception as exc:  # noqa: BLE001 - the file is in; only the match failed
            logger.warning("Online tab: could not match %s", release.vps_file_id, exc_info=True)
            ui.notify(t("manager.import_dialog.match_failed"), caption=why(exc), type="warning")

    return matched


def _altsound_folder(game_dir: str) -> str:
    """The folder the game's AltSound is read from, which is not always the one an import
    writes."""
    return asset_registry.altsound_folder(
        Path(game_dir), asset_resolver.folder_listing(game_dir)[1]) or _ALTSOUND


def _asset_key(game_dir: str, item: dict[str, Any]) -> str:
    try:
        inside = PurePath(str(item["destination"])).relative_to(game_dir)
    except ValueError:
        return ""
    first = inside.parts[0].lower() if inside.parts else ""
    if first in _FOLDERS or first == _ALTSOUND:
        return first
    if item.get("action") == "extract_tree":
        return inside.parts[0] if inside.parts else ""
    if item.get("action") in _FILE_ACTIONS:
        return inside.as_posix()
    return ""


def _day(record: dict[str, Any]) -> str:
    from manager.kit import when
    stamp = when.parsed(record.get("updated_at"))
    return when.day(stamp.astimezone()) if stamp else ""
