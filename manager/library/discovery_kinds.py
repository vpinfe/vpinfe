"""What the Discovery pane is drawn from: the kinds an entry lists and a game holds.

`read` gathers everything the rail's sections draw, once per drawing of the pane, so the
sections, the rail's corner marks and the foot bar agree. Each kind is one section; its key
is `section_key`.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from common import icons
from common.failures import why
from common.games import asset_registry, opened_pages
from common.i18n import t
from common.media.media_specs import MEDIA_SPECS, media_family
from manager.kit import candidates, offload
from manager.library import discovery, discovery_ext, discovery_get, vps_records
from manager.library.discovery_get import Pick, Tray

logger = logging.getLogger("vpinfe.manager.library.discovery_kinds")

# A kind with more releases than this gets a filter above its list.
LONG_KIND = 10
GAME = "discovery_game"
TABLE_LISTING = "tableFiles"
_FAMILY_GLYPHS = {"image": "image", "video": "movie", "audio": "graphic_eq"}


def section_key(kind: str) -> str:
    """The rail's key for a kind: VPS's name for a file kind, our name for a media kind."""
    return f"discovery_{kind}".lower()


def vocabulary() -> list[str]:
    """Every key a rail of this pane can have, for an address to name one before the pane
    is drawn."""
    return [GAME, *(section_key(kind[0]) for kind in discovery.KINDS),
            *(section_key(spec.kind) for spec in MEDIA_SPECS)]


@dataclass
class Kind:
    """One section of the rail: a kind of file or of media, and everything it draws."""

    key: str
    word: str
    listed_as: str = ""
    media: str = ""
    records: list[dict[str, Any]] = field(default_factory=list)
    offers: list[dict[str, Any]] = field(default_factory=list)
    mine: list[dict[str, str]] = field(default_factory=list)
    held: bool = False
    missing: bool = False
    standing: str = ""
    slot: dict[str, Any] = field(default_factory=dict)
    detail: dict[str, Any] = field(default_factory=dict)
    unread: str = ""
    picks: list[str] = field(default_factory=list)
    opened: dict[str, dict[str, Any]] = field(default_factory=dict)
    # The picture a source has published to replace the one VPinFE placed in this slot.
    change: dict[str, Any] = field(default_factory=dict)
    # The Games pane's rows for the files the game holds of this kind, which its Replace and
    # Remove act on.
    rows: list[dict[str, Any]] = field(default_factory=list)
    # What the Games pane's file panel reads of each of those asset files, by row id.
    details: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def label(self) -> str:
        return t(self.word)

    @property
    def count(self) -> int:
        return len(self.records) + len(self.offers)

    @property
    def family(self) -> str:
        return media_family(self.media) if self.media else ""

    @property
    def blank(self) -> bool:
        """Nothing to show here: VPS lists none, no source offers one and the game holds none."""
        return self.standing == discovery.BLANK and not self.unread

    @property
    def held_family(self) -> str:
        """What the game's file draws as: a rule sheet is a document by kind, and a picture
        when its header gives it pixels."""
        if self.family == "doc" and self.detail.get("width"):
            return "image"
        return self.family

    @property
    def glyph(self) -> str:
        if self.listed_as == TABLE_LISTING:
            return icons.TABLES
        return _FAMILY_GLYPHS.get(self.family, candidates.FALLBACK)


@dataclass
class Scene:
    """What the pane is drawn from."""

    counted: bool
    in_library: bool
    game_id: str
    vps_id: str
    tray: Tray
    files: list[Kind]
    media: list[Kind]
    sources: dict[str, dict[str, Any]]
    failed: BaseException | None
    listed: list[str]
    version: int
    title: str
    newest: list[tuple[Kind, Pick]] = field(default_factory=list)

    @property
    def compare(self) -> bool:
        """Whether Missing can be said: the game is in the library and the library is counted."""
        return self.counted and self.in_library

    def kind(self, key: str) -> Kind | None:
        return next((one for one in (*self.files, *self.media) if one.key == key), None)


def short_name(name: str, title: str) -> str:
    """A file's name without the game's title, which every file of a game starts with."""
    start = name.lower().find(title.lower()) if title else -1
    if start != 0:
        return name
    rest = name[len(title):].lstrip(" -_.")
    return rest or name


def _standing(compared: bool, holds: bool, listed: bool) -> str:
    """Have, Missing or Blank, as the grid reads them: Have when the game holds one, Missing
    when it lacks one that VPS lists or a source offers, Blank when there is nothing to hold.
    Empty where the library says nothing: the game is not in it or has not been counted."""
    if not compared:
        return ""
    if holds:
        return discovery.HAVE
    return discovery.MISSING if listed else discovery.BLANK


async def _releases(library: Any, vps_id: str, listed_as: str) -> tuple[list[dict[str, Any]], str]:
    try:
        return await offload.io(library.vps_releases, vps_id, listed_as), ""
    except Exception as exc:  # noqa: BLE001 - the other kinds still draw
        logger.warning("manager: could not read %s for %s", listed_as, vps_id, exc_info=True)
        return [], why(exc)


async def _changes(library: Any, game_id: str) -> dict[str, dict[str, Any]]:
    """The pictures a source has replaced in this game's slots, by media kind."""
    if not game_id:
        return {}
    try:
        return {str(one["kind"]): one
                for one in await offload.io(library.picture_changes, game_id)}
    except Exception:  # noqa: BLE001 - the pane is drawn without them
        logger.debug("manager: could not read the pictures changed for %s", game_id,
                     exc_info=True)
        return {}


async def _detail(library: Any, game_id: str, kind: str) -> dict[str, Any]:
    try:
        return await offload.io(library.media_detail, game_id, None, kind) or {}
    except Exception:  # noqa: BLE001 - the tile is drawn without its size
        logger.debug("manager: could not read the picture %s has for %s", game_id, kind,
                     exc_info=True)
        return {}


async def _asset_detail(library: Any, game_id: str, row: dict[str, Any]) -> dict[str, Any]:
    path = str(row.get("path") or "")
    if not path:
        return {}
    try:
        return await offload.io(library.asset_detail, game_id, path) or {}
    except Exception:  # noqa: BLE001 - the card is drawn without its size
        logger.debug("manager: could not read %s for %s", path, game_id, exc_info=True)
        return {}


async def _lens_rows(library: Any, game_id: str) -> tuple[list[dict[str, Any]],
                                                           list[dict[str, Any]]]:
    """The asset and media rows the Games pane draws for this game, the files it holds."""
    if not game_id:
        return [], []
    try:
        assets, media = await asyncio.gather(offload.io(library.files_of, "asset", game_id),
                                             offload.io(library.files_of, "media", game_id))
    except Exception:  # noqa: BLE001 - the cards are drawn without Replace and Remove
        logger.debug("manager: could not read the files %s holds", game_id, exc_info=True)
        return [], []
    return ([one for one in assets if one.get("present")],
            [one for one in media if one.get("present") and int(one.get("page") or 1) == 1])


def _newest(context: dict[str, Any], kind: Kind) -> Pick | None:
    """The newest release of the kind that a source which downloads can get and the game
    does not hold."""
    hosted = context.get("hosted", {})
    tied = {one["vps_file_id"] for one in kind.mine if one.get("vps_file_id")}
    for record in vps_records.newest_first(kind.records):
        file_id = str(record.get("vps_file_id") or "")
        if file_id in hosted and file_id not in tied and not hosted[file_id].level:
            return hosted[file_id].pick
    return None


def _opened(owner: str, listing: str) -> dict[str, dict[str, Any]]:
    """The releases of a listing whose page was opened for this entry, by release."""
    if not listing:
        return {}
    return {str(one["vps_file_id"]): one for one in opened_pages.recent(owner, listing)}


def _file_picks(context: dict[str, Any], records: list[dict[str, Any]]) -> list[str]:
    hosted = context.get("hosted", {})
    return [hosted[one].pick.key for one in (str(r.get("vps_file_id") or "") for r in records)
            if one in hosted]


async def read(context: dict[str, Any]) -> Scene:
    """Ask for everything the sections draw, and set what the foot bar and the refresh read
    from `context`. The scene is also kept in `context["scene"]`."""
    row, library, state = context["discovery"], context["library"], context["state"]
    counted = bool(library.catalog().get("counted"))
    in_library = bool(row["is_in_library"])
    game_id, held = await discovery.owner_state(context)
    if game_id:
        try:
            await offload.io(library.load_updates, game_id)
        except Exception:  # noqa: BLE001 - the rows go without the mark rather than not at all
            logger.warning("manager: could not read what has an update", exc_info=True)
    tray = discovery_get.tray_of(state, row["id"])
    listed = [kind for kind in discovery.KINDS
              if kind[0] in row["listed"] and discovery.kept(library, kind[0])]
    listed_as = [kind[0] for kind in listed]
    version = await discovery_ext.version_of(library)
    plan = await discovery_get.catalogs_asked(library)
    combined = None
    if game_id:
        combined = await discovery_get.game_offers(
            library, game_id, row["id"], row["name"],
            [*discovery_ext.slots_for(library.online_sources(), listed_as), *plan.kinds])
    discovery_ext.adopt(context, tray, await discovery_ext.ask(
        library, row, game_id, listed_as, keep=tray.offered, combined=combined))
    context["refresh_hosted"] = lambda: discovery_ext.refresh(context, tray, game_id, listed_as)
    offers, sources, failed = await discovery_get.offers_for(library, row["id"], plan, combined)
    present = await discovery.media_present(library, game_id)
    said = discovery.standing(library, row, held)
    compare = counted and in_library
    owner = row["id"]

    answers = dict(zip(listed_as, await asyncio.gather(
        *(_releases(library, row["id"], one) for one in listed_as)), strict=True))
    files: list[Kind] = []
    for listing, _field, word in discovery.KINDS:
        if listing in discovery.MEDIA_LISTINGS or not discovery.kept(library, listing):
            continue
        records, unread = answers.get(listing, ([], ""))
        mine = vps_records.held_of(held, listing) if compare else []
        holds = compare and discovery.holds(held, listing)
        if not records and not unread and not holds:
            continue
        files.append(Kind(
            section_key(listing), word, listed_as=listing, records=records, mine=mine,
            held=holds, missing=compare and said.get(listing) == discovery.MISSING,
            standing=_standing(compare, holds, bool(records)),
            unread=unread, picks=_file_picks(context, records),
            opened=_opened(owner, listing)))

    media: list[Kind] = []
    changed = await _changes(library, game_id)
    bands = dict(discovery.media_bands(listed, offers))
    if in_library:
        for kind in library.kept_kinds()["media"]:
            bands.setdefault(kind, ("", "", f"media.kind.{kind}.label"))
    for media_kind in sorted(bands, key=discovery_get.media_order):
        kind = bands[media_kind]
        records, unread = answers.get(kind[0], ([], "")) if kind[0] else ([], "")
        slot = (present or {}).get(media_kind) or {}
        offered = offers.get(media_kind) or []
        # What VPS lists as a topper is satisfied by a picture or a video of one.
        has = any(((present or {}).get(one) or {}).get("present")
                  for one in discovery_get.media_ours(kind[0], media_kind))
        media.append(Kind(
            section_key(media_kind), kind[2], listed_as=kind[0], media=media_kind,
            records=records, offers=offered, slot=slot, held=bool(slot.get("present")),
            missing=compare and present is not None and not has,
            standing=_standing(compare and present is not None, bool(has),
                               bool(records or offered)),
            unread=unread, opened=_opened(owner, kind[0]),
            change=changed.get(media_kind) or {},
            picks=[*_file_picks(context, records),
                   *(discovery_get.pick_of(one).key for one in offered)]))
    if game_id:
        holding = [one for one in media if one.held]
        for one, found in zip(holding, await asyncio.gather(
                *(_detail(library, game_id, one.media) for one in holding)), strict=True):
            one.detail = found
        asset_rows, media_rows = await _lens_rows(library, game_id)
        for one in files:
            if one.listed_as != TABLE_LISTING:
                lens = {asset_registry.lens_kind(name) for name in discovery_get.of_listing(
                    one.listed_as)[0]}
                one.rows = [row for row in asset_rows if row.get("kind") in lens]
        placed = [(one, file) for one in files for file in one.rows]
        for (one, file), found in zip(placed, await asyncio.gather(
                *(_asset_detail(library, game_id, file) for _one, file in placed)),
                strict=True):
            one.details[str(file["id"])] = found
        for one in media:
            one.rows = [row for row in media_rows if row.get("kind") == one.media]
    newest = [(one, pick) for one in files if one.missing
              if (pick := _newest(context, one)) is not None]
    scene = Scene(counted, in_library, game_id, str(row["id"]), tray, files, media, sources,
                  failed, listed_as, version, str(row["name"]), newest)
    context["scene"] = scene
    return scene
