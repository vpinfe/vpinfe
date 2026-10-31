"""One read of the library, shared by every view."""

from __future__ import annotations

import logging
import time
from collections import Counter
from collections.abc import Callable, Iterable
from functools import partial
from typing import Any

from common.config.labels import field_label
from common.extensions import host
from common.games import library_policy, ties
from common.i18n import t
from common.media import folder_browse
from common.media.media_specs import MEDIA_SPECS, media_family
from common.online import sources as source_registry
from common.runtime.jobs import KIND_MEDIA_FILL
from manager.kit import art, game_tables, media_ownership, need_keys, thumbnails, when
from manager.kit.api import ApiClient
from manager.kit.thumbnails import Look
from manager.kit.together import together

logger = logging.getLogger("vpinfe.manager.kit.data")

# Long enough that somebody waited for it. A warm read is a fifth of a second and says
# nothing; a cold one over a share has been seconds, and that is the number worth having
# when somebody says the Manager is slow.
_SLOW_READ = 1.0

# How long what Overview lists of the install itself (an update, the recordings waiting,
# the last fill) is taken as current before the next visit reads it again, and how many
# jobs that read looks back over for the fill.
WATCHED_FRESH_S = 30.0

# How long the top of Overview, which says what is playing and what is running, is taken as
# current before a visit reads it again.
FRONT_FRESH_S = 5.0
WATCHED_JOBS = 100

# What `_watch_media` keeps of a media row: enough to judge it and to name it.
_WATCHED_FILE = ("game_id", "path", "kind", "via", "present", "video_codec")

# The cell holds the *word*, never the mark. What a cell is worth filtering and sorting
# on is the state; how it is drawn is the column's business. Holding the drawn mark makes
# the text filter match `<span class="manager-mark manager-mark--full">`, so typing "full"
# finds rows and typing "This game" finds none.
#
# The words are `media_ownership`'s, which is the module that owns them. A set of its own
# here would split the folder-named file from the fixed-name slot, which is about our
# filename conventions rather than anything a reader has a concept for.


# A media slot, and what one nothing serves looks like. The fields the per-game read
# returns, less its `links` block, which nothing here draws.
_MEDIA_ABSENT: dict[str, Any] = {"present": False, "file": None, "path": None,
                                 "via": None, "origin": None, "matched_to": None,
                                 "version": None, "pages": 0}


# A video shows the frame at `#t=0.1` - metadata alone paints nothing, and the fragment
# is what makes the browser seek. Muted, or a hover cannot start it. `mediamap.py` uses
# the same two elements for the same reason; the sizing is the cell's.
_ART = "height:52px;max-width:100%;object-fit:contain"
_PICTURE = f'<img src="{{src}}" loading="lazy" style="{_ART}">'
_VIDEO = (f'<video src="{{src}}#t=0.1" preload="metadata" muted playsinline loop'
          f' style="{_ART}"></video>')


def _thumb(game_id: str, kind: str, entry: dict) -> str:
    """The art for a cell, and nothing where the kind has none: audio and rule sheets
    drew a broken icon while every present file was asked to be an `<img>`. The kind's
    name is not the test - `loading` is a video."""
    family = media_family(kind)
    if not entry.get("present") or family not in ("image", "video"):
        return ""
    src = art.media(game_id, kind, version=entry.get("version"), size=art.CELL)
    return (_VIDEO if family == "video" else _PICTURE).format(src=src)


def _glyph(entry: dict) -> str:
    """The word for this cell: what the filter matches and the sort orders on.

    Blank when nothing is there - a sparse matrix stays scannable and a column of
    "Missing" says nothing. A present file whose tier we cannot name still reads as
    present rather than vanishing: an unknown tier is a gap in our knowledge, not an
    absent file.
    """
    if not entry.get("present"):
        return ""
    if media_ownership.key_of(entry.get("via")) == media_ownership.MISSING:
        return media_ownership.tier_for(media_ownership.STAND_IN).noun
    return media_ownership.noun(entry.get("via"))


def _listed(section: dict, key: str) -> set[str]:
    """One of the hidden-kind lists, however the config layer handed it over - a list
    from JSON, or the comma string the ini holds."""
    value = section.get(key) or []
    if isinstance(value, str):
        value = value.split(",")
    return {str(item).strip() for item in value if str(item).strip()}


def _art_of(shown: dict[str, str] | None, game_id: Any) -> dict[str, str]:
    """A row's thumbnail as a field: none while the lists draw none, "" for no art."""
    return {} if shown is None else {thumbnails.FIELD: shown.get(str(game_id or ""), "")}


def _replaced(rows: list[dict[str, Any]], game_id: str,
              fresh: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """`rows` with one game's rows swapped for `fresh`, where the first of them stood."""
    at = next((index for index, row in enumerate(rows) if row.get("game_id") == game_id),
              len(rows))
    others = [row for row in rows if row.get("game_id") != game_id]
    return others[:at] + fresh + others[at:]


def _kept_of(policy: dict) -> dict[str, set[str]]:
    return library_policy.kept_kinds(policy)


# What a tag row holds for whether a game or table carries it, and for whether another tag
# is the same word in different case or spacing.
TAG_IN_USE, TAG_UNUSED = "in_use", "unused"
TAG_ONE, TAG_TWO = "one", "two"


def tag_source(said: dict[str, Any]) -> str:
    return t("manager.tags.source", extension=str(said.get("display_name")
                                                 or said.get("extension") or ""),
             title=str(said.get("title") or said.get("list") or ""))


def read_state(said: dict[str, Any]) -> str:
    """How old the last good read of a tagged list is, or since when none has worked."""
    read = when.ago(said.get("read_at"), inline=True)
    if not read:
        return t("manager.tags.never_read")
    return t("manager.tags.stale" if said.get("stale") else "manager.tags.read", ago=read)


def sources_of(looks: dict[str, dict[str, Any]], extension: str,
               key: str) -> dict[str, Any]:
    """What the Tags list says about one extension's list: its tag and its last read."""
    return next(({**one, "tag": tag} for tag, look in looks.items()
                 for one in look.get("sources") or []
                 if one.get("extension") == extension and one.get("list") == key), {})


def config_groups(found: dict[str, Any]) -> list:
    """The groups a launcher's config read declares, as the panel's control grammar
    wants them, each setting's label title-cased unless a catalog gave it."""
    from types import SimpleNamespace

    return [SimpleNamespace(
        key=g["key"], label=g["label"], summarized=bool(g.get("summarized")),
        rows=tuple(g.get("rows") or ()), read_only=bool(g.get("read_only")),
        curated=[SimpleNamespace(
            key=h["key"], label=h["label"],
            note=h.get("note") or h.get("description", ""),
            keys=tuple(h["keys"]), enabled_by=h.get("enabled_by", ""),
            rivals=tuple(h.get("rivals") or ()),
            kinds=tuple(h.get("kinds") or ()),
            versions=tuple(h.get("versions") or ()),
            pairs=tuple(SimpleNamespace(
                key=p["key"], label=p["label"], note=p.get("note", ""),
                joiner=p.get("joiner", ""), keys=tuple(p["keys"]))
                for p in h.get("pairs") or ()),
            switched=tuple(SimpleNamespace(enabled_by=s["enabled_by"], keys=tuple(s["keys"]),
                                           on=s.get("on", True))
                           for s in h.get("switched") or ()))
            for h in g.get("curated") or ()],
        settings=[SimpleNamespace(
            key=f["key"], label=f["label"] if f.get("label_key") else field_label(f["label"]),
            type=f["type"],
            default=f["default"], description=f["description"],
            help=f.get("help", ""),
            choices=tuple(tuple(pair) for pair in f.get("choices") or ()),
            choice_help=dict(f.get("choice_help") or {}),
            minimum=f.get("minimum"), maximum=f.get("maximum"),
            blank=f.get("blank", ""), left_empty=f.get("left_empty", ""),
            named=tuple(tuple(pair) for pair in f.get("named") or ()),
            reported=tuple(f.get("reported") or ()),
            per_table=bool(f.get("per_table")),
            path=f.get("path", ""), suffixes=tuple(f.get("suffixes") or ()),
            scopes=tuple(f.get("scopes") or ()))
            for f in g["settings"]])
        for g in found.get("groups") or []]


class Library:
    """Games, their media and their tables, fetched once per page load.

    Games and Assets are the same data shaped two ways, so they share this rather than
    each paying the per-game media call. Nothing here is cached across page loads: a
    library that changed underneath us would otherwise serve a stale grid.
    """

    # Game id to the collections holding it, read with the Games grid.
    _game_collections: dict[str, list[dict[str, Any]]] | None = None
    # The by-file lens, read on first use rather than at load: it is a second walk of
    # every folder, and a session with thumbnails off may never want it. Read with the
    # art kind the lists draw, and each game in `_art_stale` read again before it is
    # drawn. The Games grid's rows read it for their art.
    _table_rows: list[dict[str, Any]] | None = None
    _kept: dict[str, set[str]] | None = None
    _online: frozenset[str] | None = None
    _online_sources: list[dict] = []
    _opens_on = ""

    def __init__(self, client: ApiClient) -> None:
        self._client = client
        self.games: list[dict[str, Any]] = []
        self.media: dict[str, dict[str, Any]] = {}
        self.table_media: dict[tuple[str, str], dict[str, Any]] = {}
        self.tables: dict[str, list[dict[str, Any]]] = {}
        self._collections: list[dict[str, Any]] | None = None
        # Each collection's stored membership, for the menus that say where a game is.
        self._held_members: dict[str, list[dict[str, Any]]] = {}
        self._metadata_state: dict[str, Any] | None = None
        self._table_art = ""
        self._art_stale: set[str] = set()
        # Each launcher's settings as its settings file declares them, by launcher id.
        self._setting_groups: dict[str, list] = {}
        # The by-file lenses over media and assets, read the same way and for the same
        # reason.
        self._media_rows: list[dict[str, Any]] | None = None
        self._asset_rows: list[dict[str, Any]] | None = None
        self._catalog: dict[str, Any] | None = None
        self._vps_entries: dict[str, dict[str, Any]] = {}
        self._vps_held: bool | None = None
        self._vps_releases: dict[tuple[str, str], list[dict[str, Any]]] = {}
        self._overrides: dict[str, dict[str, Any]] = {}
        self._prefs: dict[str, dict[str, Any]] = {}
        self._launch_apps: list[dict[str, Any]] | None = None
        self._discovery: dict[str, Any] = {}
        self._hidden_checks: set[str] | None = None
        self._hidden_overview: set[str] | None = None
        self._dismissed: dict[str, list[str]] = {}
        self._marks: list[dict[str, Any]] | None = None
        self._update_moves: dict[str, list[dict[str, Any]]] = {}
        self.after_tie: Callable[[], None] | None = None
        self._dismissed_read = False
        self._watched: dict[str, Any] | None = None
        self._watched_at = 0.0
        self._front: dict[str, Any] | None = None
        self._front_at = 0.0
        self._front_player = ""
        self._tags: list[dict[str, Any]] = []
        self._games_at = 0.0

    def load(self) -> None:
        """Everything the first draw needs, read off the event loop.

        The kept kinds are read here too, for the same reason the media and asset lenses
        read them: Overview filters on them while it is drawing, which is on the loop,
        and a call made there cannot be answered by the process it is made from.
        """
        started = time.perf_counter()
        games, media_rows, discovery, *_ = together(
            self._client.games, partial(self._client.all_media, shared=True),
            self._client.discovery,
            self.load_kept_kinds, self.load_online_kinds, self.read_metadata_state,
            self.read_tags)
        self.games = games
        self._games_at = time.monotonic()
        self.media = self._shared_media(media_rows)
        self._discovery = discovery
        # Info only when it took long enough to be worth knowing. This runs on every
        # draw, so at info always it is a line per page load saying the cache is warm -
        # and the reason this timing is logged at all is the cold read, which is the
        # one that has been slow before.
        took = time.perf_counter() - started
        logger.log(logging.INFO if took >= _SLOW_READ else logging.DEBUG,
                   t("manager.data.read_log"), len(self.games), took)

    def _shared_media(self, listing: list[dict[str, Any]] | None = None
                      ) -> dict[str, dict[str, Any]]:
        """Every game's shared media, from one listing rather than a call per game.

        Seeded absent because the listing reports no shared row where every table owns
        that kind. Taken as-is, such a folder would leave the kind out of the coverage
        totals altogether instead of counting it as the gap the per-game read calls it.
        """
        if listing is None:
            listing = self._client.all_media(shared=True)
        rows = [row for row in listing if not row.get("table")
                and row.get("via") not in media_ownership.LOOSE]
        media = {game["id"]: {spec.kind: dict(_MEDIA_ABSENT) for spec in MEDIA_SPECS}
                 for game in self.games}
        for row in rows:
            if (row.get("page") or 1) == 1:
                slots = media.setdefault(row["game_id"], {})
                slots[row["kind"]] = {field: row.get(field) for field in _MEDIA_ABSENT}
                slots[row["kind"]]["pages"] = 1 if row.get("present") else 0
        for row in rows:
            if (row.get("page") or 1) > 1 and row.get("present"):
                media[row["game_id"]][row["kind"]]["pages"] += 1
        return media

    def media_for(self, game_id: str, table_id: str | None) -> dict[str, Any]:
        """The shared media, or one build's. `None` is the game, which is already read.

        Cached per build because the lens is a control someone flips back and forth,
        and a folder holds a handful of tables at most.
        """
        if not table_id:
            # Fetched when missing rather than read blindly, so dropping the entry
            # after a write is all invalidation has to do.
            if game_id not in self.media:
                self.media[game_id] = self._client.media(game_id)
            return self.media[game_id]
        key = (game_id, table_id)
        if key not in self.table_media:
            self.table_media[key] = self._client.table_media(game_id, table_id)
        return self.table_media[key]

    def placements(self, game_id: str, kind: str) -> dict:
        """Never cached: it reports conflicts, and a stale one would either hide a
        replacement or invent one."""
        return self._client.placements(game_id, kind)

    def media_overrides(self, game_id: str) -> dict:
        """Cached with the media it qualifies: both go stale on the same writes."""
        if game_id not in self._overrides:
            self._overrides[game_id] = self._client.media_overrides(game_id)
        return self._overrides[game_id]

    def media_detail(self, game_id: str, table_id: str | None, kind: str,
                     page: int = 1) -> dict:
        """Never cached: it is read when a slot is opened, which is exactly when
        someone has just changed the thing it describes."""
        return self._client.media_detail(game_id, table_id or "", kind, page)

    def video_codecs(self, game_id: str, table_id: str | None) -> dict[str, str]:
        """Each video's codec in one lens, by kind, where its header says. Never cached,
        for the reason `media_detail` is not."""
        return {kind: str(entry["video_codec"]) for kind, entry
                in self._client.media_codecs(game_id, table_id or "").items()
                if entry.get("video_codec")}

    def known_devices(self) -> list[dict]:
        """`devices`, or none where the registry cannot be read."""
        try:
            return self._client.devices()
        except Exception:  # noqa: BLE001 - no answer draws no warning
            logger.debug("Could not read the devices", exc_info=True)
            return []

    def browse_roots(self, game_id: str = "") -> list[dict]:
        return self._client.browse_roots(game_id)

    def browse(self, path: str, asset_kind: str = "", archives: bool = False) -> dict:
        return self._client.browse(path, asset_kind, archives)

    def folders(self, path: str = "", kind: str = "dir",
               suffixes: Iterable[str] = (), hidden: bool = False) -> dict:
        return self._client.folders(path, kind, suffixes, hidden)

    def make_folder(self, parent: str, name: str) -> dict:
        return self._client.make_folder(parent, name)

    def browsed_file_url(self, path: str) -> str:
        return self._client.browsed_file_url(path)

    def disk_file_url(self, path: str) -> str:
        return self._client.disk_file_url(path)

    def disk_starts(self, game_id: str = "") -> list[dict]:
        """Where a picker over this machine may begin: the places the library reads from,
        then this machine's own roots. A root the library already named keeps its name."""
        library = self._client.browse_roots(game_id)
        named = {item["path"] for item in library}
        return library + [one for one in self._client.folders()["roots"]
                          if one["path"] not in named]

    def disk_file(self, path: str, suffixes: Iterable[str]) -> bytes:
        """A file on this machine, for a write that takes the bytes rather than the path.
        Read here, not asked of the API: the API is never handed a path to trust."""
        return folder_browse.file_of_kind(path, tuple(suffixes)).read_bytes()

    def media_file(self, game_id: str, table_id: str | None, kind: str) -> tuple[bytes, str]:
        return self._client.media_file(game_id, table_id or "", kind)

    def media_sources(self) -> list[dict]:
        return self._client.media_sources()

    def wheel_sets(self) -> list[str]:
        """Not cached: a set appears when somebody puts a folder there."""
        return self._client.wheel_sets()

    def media_offers(self, vps_id: str, kind: str) -> list[dict]:
        return self._client.media_offers(vps_id, kind)

    def offers_online(self, kind: str) -> bool:
        """Whether some switched-on online source from an extension offers `kind`. Asks
        nothing, so a dialog on the event loop may decide its tabs with it."""
        return bool(self._online and source_registry.names_for(kind) & self._online)

    def load_online_kinds(self) -> frozenset[str]:
        """Read which kinds an extension's online source offers. Off the loop. A failed read
        leaves none, so a page that never needed an extension's source still opens."""
        try:
            asked = [one for one in self.media_sources()
                     if one.get("extension") and one.get("enabled")]
        except Exception:
            logger.debug("Could not read the online sources", exc_info=True)
            asked = []
        self._online_sources = asked
        self._online = frozenset(str(kind) for one in asked for kind in one.get("kinds") or ())
        return self._online

    def online_sources(self) -> list[dict]:
        """The switched-on sources extensions contribute, as the last read left them. Asks
        nothing."""
        return list(self._online_sources)

    def source_take_many(self, extension: str, key: str, picks: list[dict], game_id: str,
                         vps_id: str, name: str) -> dict:
        return self._client.source_take_many(extension, key, picks, game_id, vps_id, name)

    def source_act(self, extension: str, key: str, kind: str, row: str, act: str,
                   game_id: str, vps_id: str, name: str) -> dict:
        return self._client.source_act(extension, key, kind, row, act, game_id, vps_id, name)

    def source_look(self, extension: str, key: str, kind: str, row: str, game_id: str,
                    vps_id: str, name: str) -> dict:
        return self._client.source_look(extension, key, kind, row, game_id, vps_id, name)

    def source_changes(self, since: int) -> dict:
        return self._client.source_changes(since)

    def game_offers(self, game_id: str, vps_id: str, name: str, kinds: list[str]) -> dict:
        return self._client.game_offers(game_id, vps_id, name, kinds)

    def source_offers(self, extension: str, key: str, kind: str, game_id: str,
                      vps_id: str, name: str) -> dict:
        return self._client.source_offers(extension, key, kind, game_id, vps_id, name)

    def source_take(self, extension: str, key: str, kind: str, row: str, game_id: str,
                    vps_id: str, name: str, table_id: str = "") -> dict:
        return self._client.source_take(extension, key, kind, row, game_id, vps_id, name,
                                        table_id)

    def search_vps(self, query: str, limit: int = 12) -> list[dict]:
        return self._client.search_vps(query, limit)

    def fetch_media(self, game_id: str, table_id: str | None, kind: str, source: str,
                    vps_id: str, size: str = "") -> dict:
        result = self._client.fetch_media(game_id, table_id or "", kind, source,
                                          vps_id, size)
        self.forget_media(game_id)
        return result

    def draw_backglass_picture(self, game_id: str, table_id: str) -> dict:
        result = self._client.draw_backglass_picture(game_id, table_id)
        self.forget_media(game_id)
        return result

    def file_move_state(self, game_id: str, family: str, kind: str) -> dict:
        """Never cached: it is asked to decide a move, and a stale answer would offer a
        table a file it no longer has."""
        return self._client.file_move_state(game_id, family, kind)

    def move_file_to_tables(self, game_id: str, family: str, kind: str,
                            table_ids: list[str]) -> dict:
        result = self._client.move_file_to_tables(game_id, family, kind, table_ids)
        self._forget_move(game_id)
        return result

    def move_file_to_game(self, game_id: str, family: str, kind: str,
                          table_id: str) -> dict:
        result = self._client.move_file_to_game(game_id, family, kind, table_id)
        self._forget_move(game_id)
        return result

    def undo_file_move(self, game_id: str, token: str) -> dict:
        result = self._client.undo_file_move(game_id, token)
        self._forget_move(game_id)
        return result

    def _forget_move(self, game_id: str) -> None:
        """A move changes which files every tier resolves, media and assets alike."""
        self.forget_media(game_id)
        self._forget_tables(game_id)

    def displaced_by(self, game_id: str, table_id: str, kind: str, filename: str,
                     page: int = 1) -> list[str]:
        """Never cached: it is asked to decide a write, and a stale answer would either
        hide a replacement or invent one."""
        return self._client.displaced_by(game_id, table_id, kind, filename, page)

    def place_media(self, game_id: str, table_id: str, kind: str,
                    filename: str, data: bytes, page: int = 1) -> dict:
        result = self._client.place_media(game_id, table_id, kind, filename, data, page)
        self.forget_media(game_id)
        return result

    def move_media_page(self, game_id: str, table_id: str, kind: str, page: int,
                        to: int) -> dict:
        result = self._client.move_media_page(game_id, table_id, kind, page, to)
        self.forget_media(game_id)
        return result

    def remove_media(self, game_id: str, table_id: str, kind: str,
                     page: int | None = None) -> dict:
        result = self._client.remove_media(game_id, table_id, kind, page)
        self.forget_media(game_id)
        return result

    def asset_placements(self, game_id: str, kind: str) -> dict:
        return self._client.asset_placements(game_id, kind)

    def asset_displaced_by(self, game_id: str, table_id: str, kind: str,
                           filename: str) -> list[str]:
        return self._client.asset_displaced_by(game_id, table_id, kind, filename)

    def place_asset(self, game_id: str, table_id: str, kind: str,
                    filename: str, data: bytes) -> dict:
        result = self._client.place_asset(game_id, table_id, kind, filename, data)
        self._forget_tables(game_id)
        return result

    def remove_asset(self, game_id: str, path: str) -> dict:
        result = self._client.remove_asset(game_id, path)
        self._forget_tables(game_id)
        return result

    def set_table_hidden(self, game_id: str, table_id: str, hidden: bool) -> dict:
        result = self._client.set_table_hidden(game_id, table_id, hidden)
        self._forget_tables(game_id)
        self._forget_game(game_id)
        return result

    def set_game_rating(self, game_id: str, rating: int) -> None:
        """The by-file rows carry it too."""
        self._client.rate(game_id, rating)
        self._forget_game(game_id)
        self._table_rows = None

    def set_table_rating(self, game_id: str, table_id: str, rating: int) -> None:
        self._client.rate_table(game_id, table_id, rating)
        self._forget_tables(game_id)

    def set_game_guides(self, game_id: str, guides: list[dict]) -> list[dict]:
        stored = self._client.set_game_guides(game_id, guides)
        for game in self.games:
            if game.get("id") == game_id:
                game["guides"] = stored
        return stored

    def set_game_favorite(self, game_id: str, favorite: bool) -> None:
        self._client.set_favorite(game_id, favorite)
        self._forget_game(game_id)

    def set_game_private(self, game_id: str, private: bool) -> None:
        self._client.set_private(game_id, private)
        self._forget_game(game_id)

    def set_games_private(self, game_ids: list[str], private: bool) -> dict:
        result = self._client.set_private_many(game_ids, private)
        named = set(game_ids)
        for game in self.games:
            if game.get("id") in named:
                game["private"] = bool(result.get("private", private))
        return result

    def put_marks(self, edits: list[dict]) -> dict:
        return self._client.put_marks(edits)

    def marks_written(self) -> None:
        """A selection's marks went in under the lists this holds: read them again."""
        self.tables.clear()
        self._table_rows = None
        self._asset_rows = None
        self._forget_games()
        self.read_tags()

    def reset_play_record(self, game_id: str, table_id: str = "") -> None:
        self._client.reset_play_record(game_id, table_id)
        if table_id:
            self._forget_tables(game_id)
        else:
            self._forget_game(game_id)

    def _forget_game(self, game_id: str) -> None:
        """Re-read one game in place. The list is held for the page's life, so a write
        that changed a game and left the copy alone would report the old answer until
        something else refreshed it."""
        fresh = self._client.game(game_id)
        for index, game in enumerate(self.games):
            if game.get("id") == game_id:
                self.games[index] = fresh
                return

    def set_default_table(self, game_id: str, table_id: str) -> dict:
        result = self._client.set_default_table(game_id, table_id)
        self._forget_tables(game_id)
        return result

    def launch(self, game_id: str, file: str = "") -> None:
        """Play one of this game's tables. Empty file means the game's default."""
        self._client.launch(game_id, file)

    def config_schema(self) -> list[dict]:
        """Never cached: the active theme's windows are in it."""
        return self._client.config_schema()

    def config_values(self) -> dict:
        """Never cached - it is read when a settings page opens, which is exactly when
        somebody may have just changed it from somewhere else."""
        return self._client.config_values()

    def config_waiting(self) -> dict:
        """Never cached: it changes with every save and with every restart."""
        return self._client.config_waiting()

    def metrics(self, history_seconds: float = 0) -> dict:
        return self._client.metrics(history_seconds)

    def themes(self, refresh: bool = False) -> dict:
        """Never cached here: this page installs and removes them, and a cached list
        would show the state before the act that just happened."""
        return self._client.themes(refresh)

    def theme_sources(self) -> dict:
        """Never cached, for the same reason as `themes`."""
        return self._client.theme_sources()

    def check_theme_source(self, address: str) -> dict:
        return self._client.check_theme_source(address)

    def name_theme_source(self, address: str, name: str) -> dict:
        return self._client.name_theme_source(address, name)

    def install_theme(self, key: str) -> dict:
        return self._client.install_theme(key)

    def sessions(self) -> list[dict]:
        return self._client.sessions()

    def sign_out_session(self, session_id: str) -> None:
        self._client.sign_out_session(session_id)

    def sign_out_everyone(self) -> None:
        self._client.sign_out_everyone()

    def authenticator(self) -> dict:
        return self._client.authenticator()

    def authenticator_setup(self) -> dict:
        return self._client.authenticator_setup()

    def authenticator_finish(self, code: str) -> dict:
        return self._client.authenticator_finish(code)

    def authenticator_remove(self) -> None:
        self._client.authenticator_remove()

    def set_screen_codes(self, on: bool) -> dict:
        return self._client.set_screen_codes(on)

    def tokens(self) -> list[dict]:
        return self._client.tokens()

    def make_token(self, name: str, access: str, can_power: bool) -> dict:
        return self._client.make_token(name, access, can_power=can_power)

    def set_token_power(self, token_id: str, can_power: bool) -> dict:
        return self._client.set_token_power(token_id, can_power)

    def revoke_token(self, token_id: str) -> None:
        self._client.revoke_token(token_id)

    def encrypted(self) -> dict:
        return self._client.encrypted()

    def add_trusted_proxy(self, address: str) -> dict:
        return self._client.add_trusted_proxy(address)

    def remove_trusted_proxy(self, address: str) -> None:
        self._client.remove_trusted_proxy(address)

    def add_encrypted_network(self, text: str) -> dict:
        return self._client.add_encrypted_network(text)

    def add_tailscale(self) -> dict:
        return self._client.add_tailscale()

    def remove_encrypted_network(self, text: str) -> None:
        self._client.remove_encrypted_network(text)

    def choose_certificate(self, certfile: str, keyfile: str) -> dict:
        return self._client.choose_certificate(certfile, keyfile)

    def clear_certificate(self) -> None:
        self._client.clear_certificate()

    def renew_remote_code(self) -> int:
        return self._client.renew_remote_code()

    def accept_theme(self, key: str) -> dict:
        return self._client.accept_theme(key)

    def accept_extension(self, name: str) -> dict:
        return self._client.accept_extension(name)

    def remove_theme(self, key: str) -> dict:
        return self._client.remove_theme(key)

    def activate_theme(self, key: str) -> dict:
        return self._client.activate_theme(key)

    def theme_options(self, key: str) -> dict:
        return self._client.theme_options(key)

    def save_theme_options(self, key: str, values: dict) -> dict:
        return self._client.save_theme_options(key, values)

    def about(self, refresh: bool = False) -> dict:
        """Not cached here - the API holds it for the life of the process, and a second
        cache in front of that one only makes `refresh` mean less than it says."""
        return self._client.about(refresh)

    def launchers(self) -> dict:
        """Read fresh every time: a launcher is edited from this page, and a cache would
        show the value that was there before the edit that just happened."""
        return self._client.launchers()

    def players(self) -> list[dict]:
        """Read fresh every time: a phone or the frontend can change who is up, and a
        guest can join from either."""
        return self._client.players()

    def add_player(self, name: str, initials: str) -> dict:
        return self._client.add_player(name, initials)

    def add_guest(self, initials: str) -> dict:
        return self._client.add_guest(initials)

    def add_guest_from_card(self, card: str) -> dict:
        return self._client.add_guest_from_card(card)

    def change_player(self, player_id: str, *, name: str | None = None,
                      initials: str | None = None) -> dict:
        return self._client.change_player(player_id, name=name, initials=initials)

    def remove_player(self, player_id: str) -> None:
        self._client.remove_player(player_id)

    def set_player_up(self, player_id: str, up: bool) -> list[dict]:
        return self._client.set_player_up(player_id, up)

    def set_player_picture(self, player_id: str, filename: str, content: bytes) -> dict:
        return self._client.set_player_picture(player_id, filename, content)

    def clear_player_picture(self, player_id: str) -> dict:
        return self._client.clear_player_picture(player_id)

    def player_picture_bytes(self, player_id: str) -> bytes | None:
        return self._client.player_picture_bytes(player_id)

    def player_record(self, player_id: str) -> list[dict]:
        return self._client.player_record(player_id)

    def player_accounts(self, player_id: str) -> list[dict]:
        return self._client.player_accounts(player_id)

    def account_available(self, extension: str, check: str, candidate: str) -> bool:
        return self._client.account_available(extension, check, candidate)

    def put_account(self, player_id: str, extension: str, values: dict) -> dict:
        return self._client.put_account(player_id, extension, values)

    def put_share(self, player_id: str, extension: str, share: bool) -> dict:
        return self._client.put_share(player_id, extension, share)

    def use_card(self, player_id: str, extension: str, card: str) -> dict:
        return self._client.use_card(player_id, extension, card)

    def use_account_picture(self, player_id: str, extension: str) -> dict:
        return self._client.use_account_picture(player_id, extension)

    def account_act(self, player_id: str, extension: str, act: str) -> dict:
        return self._client.account_act(player_id, extension, act)

    def player_card(self, player_id: str, extension: str) -> bytes:
        return self._client.player_card(player_id, extension)

    def player_rating(self, player_id: str, game_id: str, rating: int) -> None:
        self._client.player_rating(player_id, game_id, rating)

    def locations(self) -> dict:
        """Read fresh every time, for the reason launchers are - this page edits it, and
        what the disk says can change without anybody editing anything."""
        return self._client.locations()

    def extensions(self) -> list[dict]:
        return self._client.extensions()

    def put_location(self, location_id: str, body: dict) -> dict:
        return self._client.put_location(location_id, body)

    def delete_location(self, location_id: str) -> None:
        self._client.delete_location(location_id)

    def set_location_write_to(self, location_id: str) -> dict:
        return self._client.set_location_write_to(location_id)

    def set_location_order(self, order: list[str]) -> list[str]:
        """Which location outranks which. Changing it changes which folder answers for
        a shared id, so every game read since is describing the old answer."""
        result = self._client.set_location_order(order)
        self._forget_games()
        self._table_rows = None
        return result

    def shadowed_here(self, location_id: str) -> list[dict]:
        return self._client.shadowed_here(location_id)

    def adopt_shadowed(self, location_id: str, path: str) -> dict:
        """The folder becomes a game of its own, so the library has one more in it."""
        result = self._client.adopt_shadowed(location_id, path)
        self._forget_games()
        self._table_rows = None
        return result

    def launcher_config_groups(self, launcher_id: str, table: str = "",
                               scope: str = "launcher") -> list:
        return config_groups(self._client.launcher_config(launcher_id, table, scope))

    def launcher_program(self, launcher_id: str) -> dict | None:
        """What the launcher's installed program is, as its settings depend on it."""
        return self._client.launcher_config(launcher_id).get("program")

    def launcher_config_values(self, launcher_id: str, table: str = "",
                               scope: str = "launcher") -> dict:
        return dict(self._client.launcher_config(
            launcher_id, table, scope).get("values") or {})

    def backups(self, subject_kind: str = "", subject_id: str = "") -> dict:
        return self._client.backups(subject_kind, subject_id)

    def take_backup(self, scope: str, *, games: list[str] | None = None,
                    launcher: str = "", note: str = "", name: str = "") -> dict:
        return self._client.take_backup(scope, games=games, launcher=launcher, note=note,
                                        name=name)

    def set_backup_note(self, copy_id: str, note: str) -> dict:
        return self._client.set_backup_note(copy_id, note)

    def restore_backup(self, copy_id: str, *, reread: bool = True) -> dict:
        """A restored game or launcher file changes what the library says, so what was
        read before is read again. A run of restores passes `reread=False` and calls
        `restored_backups` once."""
        result = self._client.restore_backup(copy_id)
        if reread:
            self.restored_backups()
        return result

    def restored_backups(self) -> None:
        self._forget_games()
        self.tables.clear()
        self._table_rows = None

    def delete_backup(self, copy_id: str) -> None:
        self._client.delete_backup(copy_id)

    def going_back_plan(self) -> dict:
        return self._client.going_back_plan()

    def go_back_to_2x(self) -> dict:
        return self._client.go_back_to_2x()

    def launcher_config(self, launcher_id: str, table: str = "",
                        scope: str = "launcher") -> dict:
        return self._client.launcher_config(launcher_id, table, scope)

    def make_launcher_settings_file(self, launcher_id: str) -> dict:
        return self._client.make_launcher_settings_file(launcher_id)

    def launcher_leftovers(self, launcher_id: str) -> dict:
        """Read fresh every time: moving them is what changes the answer."""
        return self._client.launcher_leftovers(launcher_id)

    def move_launcher_leftovers(self, launcher_id: str) -> dict:
        """Backglass files and the launcher's settings both change, so what Overview holds
        of the install is read again."""
        result = self._client.move_launcher_leftovers(launcher_id)
        self.forget_watched()
        return result

    def turn_on_launcher_leftover(self, launcher_id: str, key: str) -> dict:
        result = self._client.turn_on_launcher_leftover(launcher_id, key)
        self.forget_watched()
        return result

    def undo_launcher_leftovers(self, launcher_id: str, token: str) -> dict:
        result = self._client.undo_launcher_leftovers(launcher_id, token)
        self.forget_watched()
        return result

    def write_launcher_config(self, launcher_id: str, values: dict, *,
                              table: str = "", scope: str = "launcher",
                              create: bool = False) -> dict:
        """At a table, every game's tables are read again, as `assign_launcher` does:
        each carries how many settings it has of its own. `create` makes the program's
        settings file where it has none yet."""
        result = self._client.write_launcher_config(launcher_id, values, table=table,
                                                    scope=scope, create=create)
        if table:
            self.tables.clear()
            self._table_rows = None
        return result

    def write_launcher_config_tables(self, launcher_id: str, values: dict,
                                     tables: list[str]) -> dict:
        """The same values at each of `tables`, as `write_launcher_config` does at one:
        several come back as a job, whose result says which were written."""
        result = self._client.write_launcher_config_tables(launcher_id, values, tables)
        self.settings_written()
        return result

    def settings_written(self) -> None:
        """Each table carries how many settings it has of its own; read them again."""
        self.tables.clear()
        self._table_rows = None

    def put_launcher(self, launcher_id: str, body: dict) -> dict:
        return self._client.put_launcher(launcher_id, body)

    def delete_launcher(self, launcher_id: str) -> None:
        self._client.delete_launcher(launcher_id)

    def launcher_standing(self, app: str, settings: dict) -> dict:
        return self._client.launcher_standing(app, settings)

    def launcher_fallback(self, launcher_id: str) -> dict:
        return self._client.launcher_fallback(launcher_id)

    def make_launcher_default(self, launcher_id: str) -> dict:
        return self._client.make_launcher_default(launcher_id)

    def assign_launcher(self, table_id: str, launcher_id: str) -> dict:
        """Every game's tables are read again: the table id does not say which game
        holds it."""
        result = self._client.assign_launcher(table_id, launcher_id)
        self.tables.clear()
        self._table_rows = None
        return result

    def put_config(self, changes: dict) -> dict:
        self._collections = None
        return self._client.put_config(changes)

    def backups_folder_change(self, folder: str) -> dict:
        return self._client.backups_folder_change(folder)

    def move_backups_folder(self, folder: str) -> dict:
        self._collections = None
        return self._client.move_backups_folder(folder)

    def config_path_checks(self) -> list[dict]:
        """Never cached: it is re-read after a write to say whether the new value is any
        good, and a cached answer would be about the value it replaced."""
        return self._client.config_path_checks()

    def config_tools(self) -> list[dict]:
        """Never cached, for the same reason."""
        return self._client.config_tools()

    def get_tool(self, tool_id: str) -> dict:
        return self._client.get_tool(tool_id)

    def input_play(self) -> dict:
        """Never cached: a Tool installed or a permission granted changes it."""
        return self._client.input_play()

    def devices(self) -> list[dict]:
        """Never cached: the page holding a device reads this back after changing it,
        and a cached copy would hand it the value it just replaced."""
        return self._client.devices()

    def discovery(self) -> dict:
        """What `load` read, or nothing before it. Asks nothing, so a draw may call it."""
        return self._discovery

    def play_state(self) -> dict:
        return self._client.play_state()

    def perform_update(self, *, stop_table: bool = False) -> dict:
        return self._client.perform_update(stop_table=stop_table)

    def forget_device(self, device_id: str, kind: str = "") -> None:
        self._client.forget_device(device_id, kind)

    def actions(self) -> list[dict]:
        """Never cached: whether a thing can be done depends on what is running."""
        return self._client.actions()

    def perform_action(self, scope: str, action: str, reason: str = "") -> dict:
        return self._client.perform_action(scope, action, reason)

    def frontend_browser(self) -> dict:
        """Never cached: the frontend reports again each time it opens."""
        return self._client.frontend_browser()

    def capture_report(self) -> dict:
        """Never cached: it is worked out when asked, and a Tool set a moment ago changes
        it."""
        return self._client.capture_report()

    def test_capture(self, settings: dict | None = None) -> dict:
        return self._client.test_capture(settings)

    def choose_capture_screens(self) -> dict:
        return self._client.choose_capture_screens()

    def plan_capture(self, body: dict) -> dict:
        return self._client.plan_capture(body)

    def start_capture(self, body: dict) -> dict:
        return self._client.start_capture(body)

    def capture_job(self, job_id: str) -> dict:
        return self._client.job(job_id)

    def capture_proposals(self) -> dict:
        return self._client.capture_proposals()

    def use_proposal(self, proposal_id: str, use: bool) -> dict:
        return self._client.use_proposal(proposal_id, use)

    def discard_proposals(self) -> dict:
        return self._client.discard_proposals()

    def logs(self, limit: int = 200, level: str = "", contains: str = "",
             source: str = "") -> dict:
        """Never cached, obviously: the whole question is what has happened since."""
        return self._client.logs(limit, level, contains, source)

    def log_file(self, source: str = "") -> bytes:
        return self._client.log_file(source)

    def job(self, job_id: str) -> dict:
        return self._client.job(job_id)

    def jobs(self, limit: int = 20) -> list[dict]:
        """Never cached either: what has run since the last read is the question."""
        return self._client.jobs(limit)

    def discovered_installs(self) -> list[dict]:
        """Never cached: what is on the network is the question, and an answer from when
        the page opened is the one thing it must not be."""
        return self._client.discovered_installs()

    def set_game_overrides(self, game_id: str, changes: dict) -> dict:
        """An override changes the name a game sorts under, so the whole list is stale,
        not just this game's row."""
        result = self._client.set_game_overrides(game_id, changes)
        self.load()
        return result

    def declare_no_match(self, game_id: str) -> None:
        """The match drives the name a game sorts under, so the whole list is stale."""
        self._client.declare_no_match(game_id)
        self.load()

    def auto_match(self, game_ids: list[str]) -> dict:
        result = self._client.auto_match(game_ids)
        if result.get("changed"):
            self.load()
        return result

    def set_table_overrides(self, game_id: str, table_id: str, changes: dict) -> dict:
        result = self._client.set_table_overrides(game_id, table_id, changes)
        self._forget_tables(game_id)
        return result

    def _forget_tables(self, game_id: str) -> None:
        """Every lens over tables goes stale when one changes - including assets, whose
        rows are files a script extract or delete adds and removes."""
        self.tables.pop(game_id, None)
        self._table_rows = None
        self._asset_rows = None

    def extract_script(self, game_id: str, table_id: str) -> None:
        """The tables read is now wrong: the sidecar is what the next read reports."""
        self._client.extract_script(game_id, table_id)
        self.tables.pop(game_id, None)
        self._table_rows = None

    def delete_script(self, game_id: str, table_id: str) -> None:
        self._client.delete_script(game_id, table_id)
        self.tables.pop(game_id, None)
        self._table_rows = None

    def launch_apps(self) -> list[dict]:
        """Cached for the page's life: what this build knows how to launch cannot
        change while the process is up."""
        if self._launch_apps is None:
            self._launch_apps = self._client.launch_apps()
        return self._launch_apps

    def add_keyed_table(self, game_id: str, app: str, key: str) -> dict:
        """Add an entry with no file. The game gains a table, so both lenses on it are
        now describing a library that has one more thing in it."""
        result = self._client.add_keyed_table(game_id, app, key)
        self._forget_after_a_table_write(game_id)
        return result

    def add_referenced_table(self, game_id: str, file_path: str) -> dict:
        """Point this game at a file elsewhere."""
        result = self._client.add_referenced_table(game_id, file_path)
        self._forget_after_a_table_write(game_id)
        return result

    def import_table_file(self, game_id: str, file_path: str) -> dict:
        """Copy a file in as one more table, with what sits beside it under its name."""
        result = self._client.import_table_file(game_id, file_path)
        self._forget_after_a_table_write(game_id)
        self.forget_media(game_id)
        return result

    def contain_table(self, game_id: str, table_id: str) -> dict:
        """Copy a referenced file in. A file arrived in the folder, so the media the
        game resolves can have changed with it."""
        result = self._client.contain_table(game_id, table_id)
        self._forget_after_a_table_write(game_id)
        self.forget_media(game_id)
        return result

    def _forget_after_a_table_write(self, game_id: str) -> None:
        """Both lenses and the game's own row. The table count is a column on the game,
        read off it rather than counted here, so a write that leaves the game alone
        reports the old number."""
        self.tables.pop(game_id, None)
        self._table_rows = None
        self._forget_game(game_id)

    def forget_table(self, game_id: str, table_id: str) -> dict:
        """Drop a gone table's record. The tables list is what changes, and the media
        cache with it - a per-build read keyed on that table is now describing nothing."""
        result = self._client.forget_table(game_id, table_id)
        self.tables.pop(game_id, None)
        self._table_rows = None
        self.forget_media(game_id)
        self._forget_game(game_id)
        return result

    def table_swap_plan(self, game_id: str, table_id: str) -> dict:
        return self._client.table_swap_plan(game_id, table_id)

    def delete_table_file(self, game_id: str, table_id: str) -> dict:
        """Delete a table and its files. The game's tables, its media and its row change."""
        result = self._client.delete_table_file(game_id, table_id)
        self._forget_after_a_table_write(game_id)
        self.forget_media(game_id)
        return result

    def swap_table_back(self, game_id: str, table_id: str, copies: list[str],
                        source: dict | None = None) -> dict:
        result = self._client.swap_table_back(game_id, table_id, copies, source)
        self._forget_after_a_table_write(game_id)
        self.forget_media(game_id)
        return result

    def forget_media(self, game_id: str) -> None:
        """Every tier, not just the one written: a shared file changes what each build
        resolves, so leaving the per-build reads cached would show the old answer."""
        self.media.pop(game_id, None)
        self._overrides.pop(game_id, None)
        self._client.forget_media(game_id)
        for key in [k for k in self.table_media if k[0] == game_id]:
            self.table_media.pop(key, None)
        # The media lens is the same files counted the other way, so a write that
        # changes one changes it too. Dropped whole rather than by game: a placement
        # can add or remove a row, not only alter one.
        self._media_rows = None
        self._art_stale.add(game_id)

    def thumbnail(self) -> str:
        """The kind the lists draw beside a name, or "" for none. Asks nothing, so a
        draw on the event loop may call it."""
        return thumbnails.chosen(self.kept_kinds()["media"])

    def thumbnail_look(self) -> Look | None:
        """How the lists draw their art, or None for none. Asks nothing, like
        `thumbnail`."""
        kind = self.thumbnail()
        return thumbnails.look(kind) if kind else None

    def load_thumbnails(self) -> None:
        """Read what the lists draw beside a name, while they draw something. Off the
        loop."""
        if self.thumbnail():
            self.load_tables()

    def _holds_thumbnails(self) -> bool:
        return not self.thumbnail() or self.has_table_rows()

    def game_art(self) -> dict[str, str] | None:
        """Each game's thumbnail, or None while the lists draw none."""
        return thumbnails.by_game(self._table_rows or []) if self.thumbnail() else None

    def with_entry_art(self, lens: dict[str, Any] | None) -> dict[str, Any] | None:
        """A collection's members, each with its thumbnail: the table it names, else its
        game's."""
        shown = self.game_art()
        if shown is None or not lens or "members" not in lens:
            return lens
        named = thumbnails.by_table(self._table_rows or [])
        return {**lens, "members": [
            {**one, thumbnails.FIELD: named.get((str(one.get("game") or ""),
                                                str(one["ref_table"])), "")
             if one.get("ref_table") else shown.get(str(one.get("game") or ""), "")}
            for one in lens["members"]]}

    def table_rows(self) -> list[dict[str, Any]]:
        """The by-file lens as it stands, or empty if nobody has read it yet.

        Deliberately does not fetch. This is read while a page is being drawn, which
        is on the event loop, and an HTTP call there is refused - so the fetch is
        `load_tables`, which a caller runs off the loop before it draws.
        """
        return self._table_rows or []

    def has_table_rows(self) -> bool:
        kind = self.thumbnail()
        return self._table_rows is not None and (
            not kind or (kind == self._table_art and not self._art_stale))

    def load_overview(self) -> None:
        """What the Overview draws from. Off the event loop."""
        *_, watched, front = together(
            self.load_tables, self._read_missing_media, self._read_hidden_checks,
            self._read_overview_hidden, self._read_dismissed,
            self._read_watched, self._read_front)
        self._watched = watched
        self._front = front
        self._front_at = time.monotonic()
        self._watched_at = time.monotonic()

    def _read_overview_hidden(self) -> None:
        try:
            manager = self.manager_policy()
        except Exception:  # noqa: BLE001 - as above
            manager = {}
        self._hidden_overview = {str(one) for one in (manager.get("hidden_overview") or [])}

    def _read_dismissed(self) -> None:
        try:
            self._dismissed = self._client.dismissed()
            self._dismissed_read = True
        except Exception:  # noqa: BLE001 - a row that comes back is better than a page that cannot draw
            logger.debug("manager: could not read what was dismissed", exc_info=True)
            self._dismissed, self._dismissed_read = {}, False

    def has_overview(self) -> bool:
        return (self._table_rows is not None and self._hidden_checks is not None
                and self._hidden_overview is not None and self._front is not None
                and not self._media_missing() and self._watched is not None
                and time.monotonic() - self._watched_at < WATCHED_FRESH_S
                and time.monotonic() - self._front_at < FRONT_FRESH_S)

    def forget_watched(self) -> None:
        """Read what Overview lists of the install again on the next visit."""
        self._watched = None

    def front(self) -> dict[str, Any]:
        """What the top of Overview draws from, as `_read_front` read it. Asks nothing."""
        return self._front or {}

    def forget_front(self) -> None:
        """Read the top of Overview again on the next draw."""
        self._front = None

    def pick_front_player(self, player_id: str) -> None:
        self._front_player = player_id
        self._front = None

    def refresh_front(self, play: dict[str, Any], listed: list[dict[str, Any]]) -> None:
        """The parts of the top that change while the page is open."""
        if self._front is not None:
            self._front = {**self._front, "play": play, "listed": listed,
                           "jobs": [one for one in listed if one.get("state") == "running"]}

    def _read_front(self) -> dict[str, Any]:
        """Each read stands alone, as `_read_watched` does: a roster that cannot be read
        still leaves the owner's plays, which are the library's own.

        `player` is whose plays `records` holds by game id, `recent` the game ids newest
        first, `top` the machine's best score for the game playing, else the first of them,
        `play` is `/play/state`, `jobs` the ones still running and `listed` the latest
        jobs, None where they could not be read."""
        out: dict[str, Any] = {"players": [], "player": None, "records": {}, "recent": [],
                               "top": None, "play": {}, "jobs": [], "listed": None}
        try:
            if time.monotonic() - self._games_at >= FRONT_FRESH_S:
                self._forget_games()
        except Exception:  # noqa: BLE001 - the list already held still has the owner's plays
            logger.debug("manager: could not read the games again", exc_info=True)
        try:
            out["players"] = self._client.players()
        except Exception:  # noqa: BLE001 - not knowing who is up is not a failed page
            logger.debug("manager: could not read who is up", exc_info=True)
        roster = out["players"]
        out["player"] = (next((one for one in roster if one.get("id") == self._front_player), None)
                         or next((one for one in roster if one.get("up")), None)
                         or next((one for one in roster if one.get("owner")), None))
        records: dict[str, dict[str, Any]] = {}
        if out["player"] is None or out["player"].get("owner"):
            for game in self.games:
                user = game.get("user") or {}
                if user.get("last_played"):
                    records[str(game["id"])] = user
        else:
            held = {str(game["id"]) for game in self.games}
            try:
                for row in self._client.player_record(str(out["player"]["id"])):
                    if row.get("last_played") and str(row.get("game_id") or "") in held:
                        records[str(row.get("game_id") or "")] = row
            except Exception:  # noqa: BLE001 - the strip then has nothing to show
                logger.debug("manager: could not read a player's record", exc_info=True)
        out["records"] = records
        out["recent"] = sorted(records, key=lambda one: str(records[one]["last_played"]),
                               reverse=True)
        try:
            out["play"] = self._client.play_state()
        except Exception:  # noqa: BLE001 - nothing is shown as playing
            logger.debug("manager: could not read what is playing", exc_info=True)
        try:
            out["listed"] = self._client.jobs()
            out["jobs"] = [one for one in out["listed"] if one.get("state") == "running"]
        except Exception:  # noqa: BLE001
            logger.debug("manager: could not read the jobs running", exc_info=True)
        if out["play"].get("launching"):
            subject = str(out["play"].get("game_id") or "")
        else:
            subject = out["recent"][0] if out["recent"] else ""
        if subject:
            try:
                scores = self._client.high_scores(subject)
                out["top"] = next((entry for section in scores.get("sections") or []
                                   for entry in section.get("entries") or []), None)
            except Exception:  # noqa: BLE001 - a hero without a score is still a hero
                logger.debug("manager: could not read the top score", exc_info=True)
        return out

    def watched(self) -> dict[str, Any]:
        """What the install reports of itself that Overview lists: `update` as
        `GET /update` answered, `recordings` the ids waiting for a decision, `fill` the
        last media fill job, `unread` the reads that failed. Already read - this asks nothing."""
        return self._watched or {}

    def fillable_media(self) -> dict[str, set[str]]:
        """Per media kind, the games a source has a file for the empty slot of: Discovery
        lists those, so Overview does not. Already read - this asks nothing, and an
        unread answer is none offered."""
        return (self._watched or {}).get("offered") or {}

    def home_missing(self) -> dict[str, Any] | None:
        """Discovery Home's tabs of missing media, by tab, as `vps_home.home` counts them:
        `count`, `game_ids` and `games`. Overview's rows read their figure here so that a
        row and the tab it links to name the same games. Already read - this asks nothing.

        None where the tabs say nothing about the library: Home could not be read, or the
        library has never been compared with VPS, when its tabs count no one lacking a
        file. An empty tab of a compared library is a count of none, and is returned."""
        home = (self._watched or {}).get("home")
        if home is None or not home.get("counted"):
            return None
        return dict(home.get("missing") or {})

    def _read_watched(self) -> dict[str, Any]:
        """Each read stands alone: an install with no network has no update to report,
        and that must not take the recordings or the jobs down with it. `unread` names
        the reads that failed, so a page that could not look never reports that
        nothing is there. Asked at once, and set down in the order they are listed here."""
        out: dict[str, Any] = {"update": {}, "recordings": [], "fill": None, "unread": []}
        for found, unread in together(
                self._watch_update, self._watch_recordings, self._watch_fill,
                self._watch_extension_needs, self._watch_unaccepted,
                self._watch_home, self._watch_leftovers, self._watch_asks,
                self._watch_media):
            out.update(found)
            out["unread"] += unread
        return out

    def _watch_update(self) -> tuple[dict[str, Any], list[str]]:
        try:
            update = self._client.update_check() or {}
        except Exception:  # noqa: BLE001 - not knowing is not an update
            logger.debug("manager: could not ask whether an update is out", exc_info=True)
            return {}, ["update"]
        return {"update": update}, ["update"] if update.get("error") else []

    def _watch_recordings(self) -> tuple[dict[str, Any], list[str]]:
        try:
            return {"recordings": [str(one.get("id") or "") for one in
                                   self._client.capture_proposals().get("proposals") or []]}, []
        except Exception:  # noqa: BLE001 - recordings are not Overview's to fail on
            logger.debug("manager: could not read the recordings waiting", exc_info=True)
            return {}, ["recordings"]

    def _watch_fill(self) -> tuple[dict[str, Any], list[str]]:
        try:
            fills = [job for job in self._client.jobs(WATCHED_JOBS)
                     if job.get("kind") == KIND_MEDIA_FILL]
            return {"fill": max(fills, key=lambda job: float(job.get("started_at") or 0),
                                default=None)}, []
        except Exception:  # noqa: BLE001
            logger.debug("manager: could not read the last media fill", exc_info=True)
            return {}, ["fill"]

    def _watch_extension_needs(self) -> tuple[dict[str, Any], list[str]]:
        try:
            raised = self._client.extension_needs()
            unread = [need_keys.AWAY + name for name in raised.get("away") or []]
            if raised.get("failed"):
                unread.append("extensions")
            return {"extension_needs": list(raised.get("needs") or [])}, unread
        except Exception:  # noqa: BLE001 - what extensions ask for is not Overview's to fail on
            logger.debug("manager: could not read what extensions ask for", exc_info=True)
            return {}, ["extensions", need_keys.AWAY_ALL]

    def _watch_unaccepted(self) -> tuple[dict[str, Any], list[str]]:
        try:
            return {"unaccepted": {
                "themes": list(self._client.unaccepted_themes().get("themes") or []),
                "extensions": [one for one in self._client.extensions()
                               if host.NOT_ACCEPTED in (
                                   one.get("reason_key"),
                                   (one.get("waiting") or {}).get("reason_key"))]}}, []
        except Exception:  # noqa: BLE001 - what waits to be accepted is not Overview's to fail on
            logger.debug("manager: could not read what waits to be accepted", exc_info=True)
            return {}, ["unaccepted"]

    def _watch_home(self) -> tuple[dict[str, Any], list[str]]:
        try:
            answer = self._client.discovery_missing()
            return {"home": answer,
                    "offered": {kind: set(ids) for kind, ids in
                                (answer.get("offered") or {}).items()}}, []
        except Exception:  # noqa: BLE001 - without it a row counts what the library holds, no more, and a gap is counted whether or not a source could fill it
            logger.debug("manager: could not read what Discovery's Home counts", exc_info=True)
            return {}, []

    def _watch_leftovers(self) -> tuple[dict[str, Any], list[str]]:
        try:
            return {"leftovers": self._read_leftovers()}, []
        except Exception:  # noqa: BLE001 - what an upgrade left is not Overview's to fail on
            logger.debug("manager: could not read what an upgrade left behind", exc_info=True)
            return {}, ["leftovers"]

    def _watch_media(self) -> tuple[dict[str, Any], list[str]]:
        """The files that could be refused, as the media lens reads them, and the devices
        that would refuse: `media_files` holds only an unsupported file or a video with a
        codec, of the kinds this library collects, so a library of pictures costs nothing."""
        try:
            kept = self.load_kept_kinds()["media"]
            files = [{key: row.get(key) for key in _WATCHED_FILE}
                     for row in self._client.all_media()
                     if row.get("kind") in kept
                     and (row.get("via") == media_ownership.UNSUPPORTED or row.get("video_codec"))]
            return {"media_files": files, "devices": self.devices()}, []
        except Exception:  # noqa: BLE001 - what cannot be played is not Overview's to fail on
            logger.debug("manager: could not read the media that cannot be played", exc_info=True)
            return {}, ["media"]

    def read_media_watch(self) -> dict[str, Any]:
        """What `_watch_media` found, for a page that reads only that. Off the loop; none
        where the read failed."""
        return self._watch_media()[0]

    def _watch_asks(self) -> tuple[dict[str, Any], list[str]]:
        try:
            return {"asked": self._client.asks()}, []
        except Exception:  # noqa: BLE001 - a Manager not allowed to see them has none to show
            logger.debug("manager: could not read what programs ask", exc_info=True)
            return {}, []

    def _read_leftovers(self) -> list[dict[str, Any]]:
        """Each launcher whose program left settings behind, with the rows to act on, in the
        launchers' own order. Off the loop."""
        asked = [one for one in self._client.launchers().get("launchers") or ()
                 if one.get("has_config") and one.get("enabled")]
        answers = together(*(partial(self._client.launcher_leftovers, str(one["launcher_id"]))
                             for one in asked))
        return [{"launcher_id": str(one["launcher_id"]),
                 "name": str(one.get("display_name") or ""), "items": rows}
                for one, answer in zip(asked, answers, strict=True)
                if (rows := answer.get("items") or [])]

    def answer_ask(self, ask_id: str, approve: bool) -> None:
        """Approve or decline one ask, and drop it from what Overview holds. Off the loop."""
        self._client.answer_ask(ask_id, approve)
        if self._watched is not None:
            self._watched["asked"] = [one for one in self._watched.get("asked") or ()
                                      if one.get("id") != ask_id]

    def _media_missing(self) -> list[str]:
        return [game["id"] for game in self.games if game["id"] not in self.media]

    def _read_missing_media(self) -> None:
        if self._media_missing():
            shared = self._shared_media()
            for game_id in self._media_missing():
                if game_id in shared:
                    self.media[game_id] = shared[game_id]

    def reread_media(self, game_ids: Iterable[str]) -> None:
        """These games' media after a write across them, from one listing. Off the loop."""
        for game_id in game_ids:
            self.forget_media(game_id)
        self._read_missing_media()

    def _read_hidden_checks(self) -> None:
        """Off the loop. A policy that cannot be read leaves every check on."""
        try:
            policy = self.library_policy()
        except Exception:  # noqa: BLE001 - a report that cannot read a preference still runs
            policy = {}
        self._hidden_checks = {str(one) for one in (policy.get("hidden_checks") or [])}

    def hidden_checks(self) -> set[str]:
        return self._hidden_checks or set()

    def hidden_overview(self) -> set[str]:
        """The device rows Overview has stopped checking, as the last read held them."""
        return self._hidden_overview or set()

    def stopped(self) -> set[str]:
        """Every Overview row not being checked, library and device alike."""
        return self.hidden_checks() | self.hidden_overview()

    def dismissed(self) -> dict[str, list[str]]:
        """Each row set aside, with the ids it counted. Already read - this asks nothing."""
        return self._dismissed

    def refresh_dismissed(self) -> dict[str, list[str]]:
        """The record read again, for a page that is not Overview and so did not load it.
        Off the loop. What was held stays where the read fails."""
        try:
            self._dismissed = self._client.dismissed()
            self._dismissed_read = True
        except Exception:  # noqa: BLE001 - a notice that draws whole is better than none
            logger.debug("manager: could not read what was dismissed", exc_info=True)
        return self._dismissed

    def merge_dismissed(self, row: str, ids: list[str], *, scope: str) -> None:
        """Set `row` aside over `ids` for the ids that start with `scope`, leaving what it
        was dismissed over for any other scope. Reads the record fresh. Off the loop."""
        current = self._client.dismissed().get(row) or []
        kept = [one for one in current if not one.startswith(scope)]
        self.set_dismissed(row, sorted({*kept, *ids}))

    def dismissed_was_read(self) -> bool:
        """False where the record could not be read, which is when it must not be pruned
        against what the rows now count."""
        return self._dismissed_read

    def set_dismissed(self, row: str, ids: list[str]) -> None:
        """Replace what `row` was dismissed over; none brings it back. Off the loop."""
        self._client.put_dismissed(row, ids)
        if ids:
            self._dismissed[row] = list(ids)
        else:
            self._dismissed.pop(row, None)

    def set_checking(self, key: str, on: bool, *, device: bool) -> None:
        """Start or stop checking one Overview row: a device row in the Manager's list, a
        library row in the library's. Off the loop.

        The list is read again here, not taken from the page: that is empty after any write
        and after a failed read, and writing from it would switch on every other row that is
        off."""
        policy = self.manager_policy() if device else self.library_policy()
        kept = {str(one) for one in
                (policy.get("hidden_overview" if device else "hidden_checks") or [])}
        after = sorted(kept - {key} if on else kept | {key})
        if device:
            self.put_manager_policy({"hidden_overview": after})
        else:
            self.put_library_policy({"hidden_checks": after})

    # --- collections ----------------------------------------------------------
    # Held, because render() runs on the event loop and the client refuses an HTTP call
    # there - the same reason the by-file lens is warmed rather than fetched. Every
    # write drops it, so what comes back next is the change and not the memory of it.

    def has_collections(self) -> bool:
        return self._collections is not None

    def metadata_state(self) -> dict[str, Any]:
        """What the library's `.info` files need. Already read - this asks nothing.

        Read in `load` for the same reason the kept kinds are: Overview wants it while
        it is drawing, which is on the event loop, and a call made there cannot be
        answered by the process it is made from.
        """
        return self._metadata_state or {}

    def untied_tables(self) -> tuple[str, ...]:
        """The tables of games on VPS whose release nobody has picked, by id."""
        rows = self.table_rows()
        bases = ties.patch_bases(rows)
        return tuple(sorted(str(row["id"]) for row in rows
                            if ties.release_state(row, bases) == ties.UNTIED))

    def read_metadata_state(self) -> dict[str, Any]:
        """Ask again. Off the event loop, and after anything that rewrites a `.info`."""
        try:
            self._metadata_state = self._client.info_maintenance()
        except Exception:
            logger.warning("manager: could not read the library's metadata state",
                           exc_info=True)
            self._metadata_state = {}
        return self._metadata_state

    def _collections_changed(self) -> None:
        self._collections = None
        self._game_collections = None
        self._held_members = {}

    def held_members(self, names: list[str]) -> dict[str, list[dict[str, Any]]]:
        """Each named collection's stored membership, read once and kept until a write
        through this library; empty, and not kept, for one that cannot be read. Off the
        event loop."""
        found: dict[str, list[dict[str, Any]]] = {}
        for name in names:
            if name not in self._held_members:
                try:
                    self._held_members[name] = list(
                        self._client.collection_members(name).get("members") or [])
                except Exception:
                    logger.debug("manager: could not read what %r holds", name,
                                 exc_info=True)
                    found[name] = []
                    continue
            found[name] = self._held_members[name]
        return found

    def load_game_collections(self, again: bool = False) -> None:
        """Which collections hold each game. Off the event loop; `again` after a write
        that may have moved a game in or out of one."""
        if again or self._game_collections is None:
            try:
                self._game_collections = self._client.library_game_collections()
            except Exception:
                logger.warning("manager: could not read which collections hold each game",
                               exc_info=True)
                self._game_collections = {}

    def has_game_collections(self) -> bool:
        return self._game_collections is not None

    def load_games_grid(self, again: bool = False) -> None:
        """What the Games grid draws from, whole or a row at a time. Off the event loop;
        `again` as `load_game_collections` takes it."""
        self.load_game_collections(again)
        self._read_missing_media()
        self.load_thumbnails()
        if self._hidden_checks is None:
            self._read_hidden_checks()

    def has_games_grid(self) -> bool:
        return (self.has_game_collections() and not self._media_missing()
                and self._holds_thumbnails() and self._hidden_checks is not None)

    def smart_collections(self) -> set[str]:
        """The smart ones among the collections holding a game. Read with the Games grid,
        so this asks nothing."""
        return {str(one.get("name") or "")
                for held in (self._game_collections or {}).values()
                for one in held if one.get("type") == "filter"}

    def load_collections(self) -> list[dict[str, Any]]:
        """Read the list, and which of them the cabinet opens on. Off the event loop, and
        again after any write."""
        if self._collections is None:
            self._collections = self._client.collections()
            try:
                behavior = self._client.config_values().get("behavior") or {}
                self._opens_on = str(behavior.get("startup_collection") or "").strip()
            except Exception:
                logger.warning("manager: could not read which collection the cabinet "
                               "opens on", exc_info=True)
                self._opens_on = ""
        return self._collections

    def opens_on(self) -> str:
        """The collection the cabinet opens on, read with the list. "" for all games."""
        return self._opens_on

    def filter_axes(self) -> list[dict[str, Any]]:
        """The axes a rule can be written on. Read from core's registry, never listed
        here: that registry is the only place an axis is named."""
        return self._client.filter_axes()

    def game_collections(self, game_id: str) -> dict[str, list[dict[str, Any]]]:
        """Never cached: every write in the section changes it."""
        return self._client.game_collections(game_id)

    def collections(self) -> list[dict[str, Any]]:
        """What `load_collections` last read. Empty before the first read rather than
        fetching here: this is called from render()."""
        return self._collections or []

    def collection_games(self, name: str) -> list[dict[str, Any]]:
        """What a collection resolves to now. For a manual one that is its members
        *that still exist* - a stored member naming a game this library does not have
        resolves to nothing, which is why this can be shorter than `game_count`."""
        return self._client.collection_games(name)

    def collection_entries(self, name: str) -> list[dict[str, Any]]:
        return self._client.collection_entries(name)

    def collection_members(self, name: str) -> dict:
        """Stored membership and the state of each - the lens an editor needs. Not
        cached: every write in the panel changes it."""
        return self._client.collection_members(name)

    def preview_filters(self, filters: dict | None, limit: int | None = None) -> dict:
        """What a rule would match, storing nothing."""
        return self._client.preview_filters(filters, limit)

    def preview_collection(self, name: str, filters: dict) -> dict:
        """The collection's stored membership as it would read with these rules, or none
        where `filters` is empty. Nothing is stored."""
        return self._client.preview_collection(name, filters)

    def exclude_from_collection(self, name: str, game_id: str, table: str = "") -> None:
        self._collections_changed()
        self._client.exclude_from_collection(name, game_id, table)

    def unexclude_from_collection(self, name: str, game_id: str,
                                  table: str | None = None) -> None:
        self._collections_changed()
        self._client.unexclude_from_collection(name, game_id, table)

    def keep_collection_result(self, name: str) -> dict:
        self._collections_changed()
        return self._client.keep_collection_result(name)

    def set_collection_image(self, name: str, filename: str, content: bytes) -> dict:
        self._collections_changed()
        return self._client.set_collection_image(name, filename, content)

    def clear_collection_image(self, name: str) -> None:
        self._collections_changed()
        self._client.clear_collection_image(name)

    def create_collection(self, name: str, filters: dict | None = None) -> dict:
        self._collections_changed()
        return self._client.create_collection(name, filters=filters)

    def copy_collection(self, name: str, source: str) -> dict:
        self._collections_changed()
        return self._client.create_collection(name, copy_of=source)

    def arrange_collections(self, names: list[str]) -> None:
        self._collections_changed()
        self._client.arrange_collections(names)

    def patch_collection(self, name: str, changes: dict) -> dict:
        self._collections_changed()
        return self._client.patch_collection(name, changes)

    def delete_collection(self, name: str) -> None:
        self._collections_changed()
        self._client.delete_collection(name)

    def add_to_collection(self, name: str, game_id: str, table: str = "",
                          after_table: str | None = None) -> None:
        self._collections_changed()
        self._client.add_to_collection(name, game_id, table, after_table)

    def set_member_table(self, name: str, game_id: str, table: str = "",
                         was: str = "") -> None:
        self._collections_changed()
        self._client.set_member_table(name, game_id, table, was)

    def remove_from_collection(self, name: str, game_id: str,
                               table: str | None = None) -> None:
        self._collections_changed()
        self._client.remove_from_collection(name, game_id, table)

    def set_collection_order(self, name: str, games: list[str]) -> None:
        self._collections_changed()
        self._client.set_collection_order(name, games)

    def load_tables(self) -> list[dict[str, Any]]:
        """Read the by-file lens with the art the lists draw. Off the event loop: once
        per session, whole again for a new kind, and a game at a time after its media
        changed. With it, the settings of each launcher that plays one of them and keeps
        some, once each."""
        kind = self.thumbnail()
        if self._table_rows is None or (kind and kind != self._table_art):
            self._table_rows = self._client.all_tables(media=kind)
            self._table_art = kind
            self._art_stale.clear()
            self._load_setting_groups(self._table_rows)
        elif kind and self._art_stale:
            for game_id in sorted(self._art_stale):
                self._table_rows = _replaced(self._table_rows, game_id,
                                             self._client.all_tables(media=kind, game=game_id))
            self._art_stale.clear()
        return self._table_rows

    def load_tables_view(self) -> None:
        """What the Tables grid draws from. Off the event loop."""
        self.load_tables()
        if self._hidden_checks is None:
            self._read_hidden_checks()

    def has_tables_view(self) -> bool:
        return self.has_table_rows() and self._hidden_checks is not None

    def _load_setting_groups(self, rows: list[dict[str, Any]]) -> None:
        for launcher in dict.fromkeys(str(row.get("launcher") or "") for row in rows
                                      if row.get("launcher_app_configurable")):
            if not launcher or launcher in self._setting_groups:
                continue
            try:
                self._setting_groups[launcher] = self.launcher_config_groups(launcher)
            except Exception:  # noqa: BLE001 - one launcher that cannot say is not all
                self._setting_groups[launcher] = []

    def setting_groups(self) -> dict[str, list]:
        """Each launcher's settings, by id, as far as `load_tables` has read them: none
        for one that could not say."""
        return dict(self._setting_groups)

    def load_media_rows(self) -> list[dict[str, Any]]:
        """Read the media lens. Off the event loop, once per session.

        The kept kinds are read here too, because `media_rows` filters on them while
        the page is drawing - which is on the loop, where the policy read is refused.
        """
        rows = self._media_rows
        if rows is None:
            rows = self._media_rows = self._client.all_media()
        self.load_kept_kinds()
        self.load_tables()
        return self._kept_media(rows)

    def media_rows(self) -> list[dict[str, Any]]:
        """The media lens as it stands, filtered to the kinds this library collects.

        Filtered here rather than in the API, which serves every kind either way: a
        file already on disk still resolves and a theme asking for a topper still gets
        one. What a library says it does not collect governs what it is shown.
        """
        return self._kept_media(self._media_rows or [])

    def _kept_media(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        kept = self.kept_kinds()["media"]
        shown = self.game_art()
        tables = self._tables_by_id()
        return [{**row, "said": game_tables.made(row), **game_tables.named_in(row, tables),
                 **_art_of(shown, row.get("game_id"))}
                for row in rows if row.get("kind") in kept]

    def _tables_by_id(self) -> dict[str, dict[str, Any]]:
        """The tables as read, for naming the one a file belongs to."""
        return {str(row.get("id") or ""): row for row in self._table_rows or []}

    def has_media_rows(self) -> bool:
        return (self._media_rows is not None and self._table_rows is not None
                and self._holds_thumbnails())

    def load_asset_rows(self) -> list[dict[str, Any]]:
        """Read the asset lens, and the kept kinds it is filtered by. Off the loop."""
        rows = self._asset_rows
        if rows is None:
            rows = self._asset_rows = self._client.all_assets()
        self.load_kept_kinds()
        self.load_tables()
        return self._kept_assets(rows)

    def asset_rows(self) -> list[dict[str, Any]]:
        """The asset lens, filtered to the kinds this library collects."""
        return self._kept_assets(self._asset_rows or [])

    def _kept_assets(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        kept = self.kept_kinds()["asset"]
        shown = self.game_art()
        tables = self._tables_by_id()
        return [{**row, "said": game_tables.made(row), **game_tables.named_in(row, tables),
                 **_art_of(shown, row.get("game_id"))}
                for row in rows if row.get("kind") in kept]

    def files_of(self, family: str, game_id: str) -> list[dict[str, Any]]:
        """One game's rows in the media or asset lens, read fresh and kept as the whole
        lens is."""
        self.load_tables()
        if family == "media":
            return self._kept_media(self._client.media_of(game_id))
        return self._kept_assets(self._client.assets_of(game_id))

    def roms_of(self, game_id: str) -> list[dict[str, Any]]:
        """One game's ROM files, read fresh; none where the library does not collect ROMs."""
        if "rom" not in self.kept_kinds()["asset"]:
            return []
        return self._kept_assets(self._client.assets_of(game_id, "rom"))

    def outside_links(self, game_id: str, table: str = "",
                      path: str = "") -> list[dict[str, Any]]:
        """What extensions link this game, table or file to. A failed read is no links:
        they are somebody else's, and never the reason a panel does not draw."""
        try:
            return self._client.outside_links(game_id, table, path)
        except Exception:
            logger.debug("No outside links for %s", game_id, exc_info=True)
            return []

    def asset_detail(self, game_id: str, path: str, lines: int = 40) -> dict[str, Any]:
        return self._client.asset_detail(game_id, path, lines)

    def asset_text(self, game_id: str, path: str) -> dict[str, Any]:
        return self._client.asset_text(game_id, path)

    def save_asset_text(self, game_id: str, path: str, text: str, version: str,
                        replace_changed: bool = False,
                        keep_copy: bool = True) -> dict[str, Any]:
        return self._client.put_asset_text(game_id, path, text, version,
                                           replace_changed, keep_copy)

    def place_backglass_displays(self, game_id: str, path: str, target: str,
                                 box: tuple[int, int, int, int] | None) -> dict[str, Any]:
        """The file is shared by the game's tables, so what they resolve is unchanged."""
        return self._client.put_backglass_displays(game_id, path, target, box)

    def undo_backglass_displays(self, game_id: str, token: str) -> dict[str, Any]:
        return self._client.undo_backglass_displays(game_id, token)

    def has_asset_rows(self) -> bool:
        return (self._asset_rows is not None and self._table_rows is not None
                and self._holds_thumbnails())

    def high_scores(self, game_id: str, table_id: str = "") -> dict[str, Any]:
        """Asked each time the panel opens: that is when a newer score file is read."""
        return self._client.high_scores(game_id, table_id)

    def pictures(self, game_id: str, table_id: str = "") -> list[dict[str, Any]]:
        return list(self._client.pictures(game_id, table_id).get("pictures") or [])

    def remove_picture(self, game_id: str, name: str) -> bytes:
        """Answers the file's bytes, for Undo."""
        held = self._client.picture_bytes(game_id, name)
        self._client.remove_picture(game_id, name)
        return held

    def put_picture(self, game_id: str, name: str, data: bytes) -> dict[str, Any]:
        return self._client.put_picture(game_id, name, data)

    def tables_for(self, game_id: str) -> list[dict[str, Any]]:
        """Fetched when something asks, not with the library.

        Prefetching tables doubles the read across the whole library, and only the workbench
        - one game at a time - ever needs them.
        """
        if game_id not in self.tables:
            self.tables[game_id] = self._client.tables(game_id)
        return self.tables[game_id]

    def kinds_present(self) -> list[str]:
        """Kinds any game in this library actually has.

        A column blank for every row costs width and tells nobody anything, and a good
        many kinds are empty across a whole library. The user can still add them back -
        the column picker holds every kind the library collects, this only sets the
        default.
        """
        seen: set[str] = set()
        for entries in self.media.values():
            seen.update(kind for kind, entry in entries.items() if entry.get("present"))
        return [kind for kind in self.kinds() if kind in seen]

    def tags(self) -> list[str]:
        """Every tag a picker offers: those carried, and those only written down. Not
        one an extension derives."""
        seen = {str(one.get("name") or "") for one in self._tags}
        for game in self.games:
            seen.update((game.get("user") or {}).get("tags") or [])
        return sorted(seen - {""} - self.derived_tags(), key=str.lower)

    def derived_tags(self) -> set[str]:
        return {str(one.get("name") or "") for one in self._tags if one.get("sources")}

    def tag_rows(self) -> list[dict[str, Any]]:
        """One row per tag: what it is, how many games carry it, and which tags it may
        be a spelling of.

        `same` is the case- and space-insensitive key. Entry does not fold case on
        purpose, so this is where two spellings of one word become findable - which is
        the whole reason the editor exists rather than being a rename box.
        """
        keys: dict[str, int] = {}
        for one in self._tags:
            key = " ".join(str(one.get("name") or "").split()).casefold()
            keys[key] = keys.get(key, 0) + 1
        rows = []
        for one in self._tags:
            name = str(one.get("name") or "")
            key = " ".join(name.split()).casefold()
            rows.append({"id": name, "tag": name, "tag_list": [name],
                         "games": int(one.get("games") or 0),
                         "tables": int(one.get("tables") or 0),
                         "description": str(one.get("description") or ""),
                         "same": key,
                         "sources": list(one.get("sources") or []),
                         "source": ", ".join(tag_source(said)
                                             for said in one.get("sources") or []),
                         "spelling": TAG_TWO if keys[key] > 1 else TAG_ONE,
                         "use": TAG_IN_USE if (int(one.get("games") or 0)
                                               or int(one.get("tables") or 0)) else TAG_UNUSED})
        return rows

    def vps_search(self, term: str, limit: int = 40) -> list[dict]:
        return self._client.vps_search(term, limit)

    def vps_sync_state(self) -> dict:
        return self._client.vps_sync_state()

    def dof_event_state(self) -> dict:
        return self._client.dof_event_state()

    def test_dof_event(self, event: str) -> dict:
        return self._client.test_dof_event(event)

    def stop_dof_event(self) -> None:
        self._client.stop_dof_event()

    def dof_config_state(self) -> dict:
        """Never cached: a press and the schedule both move it."""
        return self._client.dof_config_state()

    def download_dof_config(self) -> dict:
        return self._client.download_dof_config()

    def vps_catalog_held(self) -> bool:
        """Whether there is a catalog to look a match up in. Held for the page's life,
        as `vps_entry` is, and for the same reason."""
        if self._vps_held is None:
            self._vps_held = bool(self._client.vps_sync_state().get("entries"))
        return self._vps_held

    def sync_vps(self) -> dict:
        """Not cached and nothing invalidated here: the catalog is read per request by
        whatever asks for it, so a new copy is picked up on the next read."""
        return self._client.sync_vps()

    def kept_kinds(self) -> dict[str, set[str]]:
        """What `load_kept_kinds` or a policy write last held, and every kind before
        either. Asks nothing, so a draw on the event loop may call it."""
        if self._kept is None:
            return _kept_of({})
        return self._kept

    def load_kept_kinds(self) -> dict[str, set[str]]:
        """Read the kept kinds, once. Off the loop."""
        if self._kept is None:
            self._kept = _kept_of(self.library_policy())
        return self._kept

    def library_policy(self) -> dict:
        """Never cached beyond the page: it is read when a settings page opens, which is
        exactly when somebody may have just changed it from somewhere else."""
        return self._client.library_policy()

    def put_library_policy(self, changes: dict) -> dict:
        self._hidden_checks = None
        answer = self._client.put_library_policy(changes)
        self._kept = _kept_of(answer)
        if "hidden_sources" in changes:
            self.load_online_kinds()
        return answer

    def hide_media_kinds(self, kinds: Iterable[str]) -> None:
        """Stop collecting these kinds, keeping the ones already switched off. Off the loop."""
        held = _listed(self.library_policy(), "hidden_media_kinds")
        self.put_library_policy({"hidden_media_kinds": sorted(held | set(kinds))})

    def manager_policy(self) -> dict:
        """What this install's Manager shows, read when asked for."""
        return self._client.manager_policy()

    def put_manager_policy(self, changes: dict) -> dict:
        self._hidden_overview = None
        return self._client.put_manager_policy(changes)

    def offered_media(self, game_id: str) -> dict[str, int]:
        """How many files the catalog lists for each of our media kinds, counting only
        the ones that are a file. A kind the catalog has only as a folder to browse
        answers zero, which is the whole reason the count is not `listed`."""
        offered: dict[str, int] = {}
        for kind in self._client.vps_state(game_id):
            # Media only. `backglass` names a picture here and a .directb2s among the
            # assets, and it is the second that VPS lists - counted against the tile it
            # would have marked the wrong thing with the wrong number.
            if kind.get("held_in") != "media":
                continue
            for name in (kind.get("ours") or []):
                offered[name] = int(kind.get("obtainable") or 0)
        return offered

    def vps_held(self, game_id: str) -> dict:
        """Never cached: what a game holds is what a draw is about to show."""
        return self._client.vps_held(game_id)

    def vps_details(self, game_id: str) -> list[dict]:
        """Where this game's details disagree with the entry it is matched to."""
        return self._client.vps_details(game_id)

    def adopt_vps_details(self, game_id: str,
                          fields: list[str] | None = None) -> None:
        self._client.adopt_vps_details(game_id, fields)
        self._forget_games()

    def set_table_source(self, game_id: str, table_id: str, vps_file_id: str) -> None:
        self._client.set_table_source(game_id, table_id, vps_file_id)
        self._forget_tables(game_id)
        self._forget_games()
        self._tied()

    def set_asset_source(self, game_id: str, path: str, vps_file_id: str) -> None:
        self._client.set_asset_source(game_id, path, vps_file_id)
        self._forget_tables(game_id)
        self.forget_media(game_id)
        self._forget_games()
        self._tied()

    def _tied(self) -> None:
        if self.after_tie is not None:
            self.after_tie()

    def vps_releases(self, vps_id: str, listed_as: str = "tableFiles") -> list[dict]:
        """Held like the entry is. Keyed by kind as well: a cache on the id alone would
        hand a backglass picker the table builds."""
        held = (vps_id, listed_as)
        if held not in self._vps_releases:
            self._vps_releases[held] = self._client.vps_releases(vps_id, listed_as)
        return self._vps_releases[held]

    def vps_entry(self, vps_id: str) -> dict:
        """Held for the page's life: the catalog does not change while it is open, and
        a panel redraw would otherwise re-read the same entry on every keystroke."""
        if vps_id not in self._vps_entries:
            self._vps_entries[vps_id] = self._client.vps_entry(vps_id)
        return self._vps_entries[vps_id]

    def merge_tags(self, sources: list[str], into: str) -> int:
        """Rename is one source into a new name; merge is several into one."""
        changed = self._client.merge_tags(sources, into)
        self._forget_games()
        self.read_tags()
        return changed

    def delete_tag(self, tag: str) -> int:
        changed = self._client.delete_tag(tag)
        self._forget_games()
        self.read_tags()
        return changed

    def owned(self, ids: list[str]) -> tuple[dict[str, dict[str, Any]],
                                              dict[str, dict[str, Any]]]:
        """Which of these VPS ids this library holds, and which releases it holds another
        version of, each by id."""
        said = self.owned_as(ids)
        return said.get("owned") or {}, said.get("other_versions") or {}

    def owned_as(self, ids: list[str], keys: str = "vps", kind: str = "") -> dict[str, Any]:
        """`owned` whole, for ids of `keys` (`vps` or `vpinfe`) and a `kind` (`game`,
        `table`, or "" for a VPS id of either): what the library holds, the other versions
        it holds, and what VPSdb calls an id it does not (`known`)."""
        return self._client.owned(ids, keys, kind)

    def read_tags(self) -> list[dict[str, Any]]:
        """Every tag and what it wears. Off the loop; `tag_looks` is what a draw reads."""
        try:
            self._tags = self._client.tags()
        except Exception:
            logger.warning("manager: could not read the tags", exc_info=True)
            self._tags = []
        return self._tags

    def tag_looks(self) -> dict[str, dict[str, Any]]:
        return {str(one.get("name") or ""): one for one in self._tags}

    def put_tag(self, tag: str, changes: dict[str, Any]) -> dict[str, Any]:
        said = self._client.put_tag(tag, changes)
        self.read_tags()
        return said

    # --- imports ------------------------------------------------------------
    # Pass-throughs: the browser uploads straight to the API, so what is left is the
    # three questions after that and none of them is cached - each is about a session
    # that exists only until it is answered.

    def upload_analysis(self, upload_id: str) -> dict:
        return self._client.upload_analysis(upload_id)

    def upload_from_path(self, path: str) -> str:
        return self._client.upload_from_path(path)

    def upload_image(self, upload_id: str) -> tuple[str, bytes]:
        return self._client.upload_image(upload_id)

    def add_game_from_vps(self, vps_id: str, location_id: str = "") -> dict:
        made = self._client.add_game_from_vps(vps_id, location_id)
        self._forget_games()
        held = self._catalog or {}
        if held.get("counted"):
            for row in held.get("rows") or []:
                if row.get("vps_id") == vps_id:
                    row["in_library"] = True
        return made

    def delete_game_folder(self, game_id: str) -> None:
        self._client.delete_game_folder(game_id)
        self._forget_games()
        self.tables.pop(game_id, None)

    def has_catalog(self) -> bool:
        return self._catalog is not None

    def load_catalog(self) -> dict[str, Any]:
        """Every catalog entry against the library as last counted. Off the loop."""
        if self._catalog is None:
            self._catalog = self._client.catalog_discovery()
        return self._catalog

    def catalog(self) -> dict[str, Any]:
        return self._catalog or {"counted": "", "rows": []}

    def forget_catalog(self) -> None:
        self._catalog = None

    def discovery_home(self, days: int) -> dict[str, Any]:
        """Discovery's front page for a window of `days`. Off the loop."""
        return self._client.discovery_home(days)

    def picture_changes(self, game_id: str = "") -> list[dict[str, Any]]:
        """The pictures a source has replaced since they were placed, of one game or every
        game. Off the loop."""
        return list(self._client.picture_changes(game_id).get("pictures") or [])


    def load_updates(self, game_id: str) -> list[dict[str, Any]]:
        """Read what of this game has a later version. Off the loop."""
        shelf = self._client.discovery_updates(game_id)
        moves = [dict(move) for one in shelf.get("games") or () for move in one.get("moves") or ()]
        self._update_moves[game_id] = moves
        return moves

    def update_moves(self, game_id: str) -> list[dict[str, Any]]:
        """What of this game has a later version, as last read; none before a read."""
        return list(self._update_moves.get(game_id) or ())

    def load_marks(self) -> list[dict[str, Any]]:
        """Read what is marked Wanted or Ignored. Off the loop."""
        self._marks = self._client.discovery_marks()
        return self._marks

    def marks(self) -> list[dict[str, Any]]:
        """The marks as last read; none before the first read. Asks nothing."""
        return self._marks or []

    def mark_discovery(self, state: str, scope: str, vps_id: str, detail: str = "") -> str:
        """Put a state on a thing; the state it held, "" if none. Off the loop."""
        done = self._client.put_discovery_mark(state, scope, vps_id, detail)
        self._marks = list(done.get("marks") or [])
        return str(done.get("replaced") or "")

    def unmark_discovery(self, scope: str, vps_id: str, detail: str = "") -> str:
        """Take the mark off a thing; the state it held, "" if none. Off the loop."""
        done = self._client.delete_discovery_mark(scope, vps_id, detail)
        self._marks = list(done.get("marks") or [])
        return str(done.get("replaced") or "")

    def new_game_destination(self) -> dict:
        return self._client.new_game_destination()

    def upload_plan(self, upload_id: str, **asked: Any) -> dict:
        return self._client.upload_plan(upload_id, **asked)

    def upload_import(self, upload_id: str, **asked: Any) -> dict:
        return self._client.upload_import(upload_id, **asked)

    def start_upload_import(self, upload_id: str, **asked: Any) -> dict:
        return self._client.start_upload_import(upload_id, **asked)

    def stop_upload_import(self) -> bool:
        return self._client.stop_upload_import()

    def abort_upload(self, upload_id: str) -> None:
        self._client.abort_upload(upload_id)

    def refresh_after_import(self) -> None:
        """Everything an import can have changed, which is nearly everything.

        A new game, a new table in an existing one, a media file that changed what a
        game resolves - one import can do all three, and there is no cheap way to know
        which. So this is the blunt one, and it is only called after a write somebody
        watched happen rather than on any ordinary draw.
        """
        self._forget_games()
        self._table_rows = None
        self._media_rows = None
        self._asset_rows = None
        self._catalog = None
        self.media.clear()
        self.table_media.clear()
        self._overrides.clear()
        self.tables.clear()
        self.read_metadata_state()

    def _forget_games(self) -> None:
        """Re-read the whole list, for a write that touched more of it than one game.

        The list is held for the page's life and `_forget_game` replaces one entry, so a
        sweep across the library left every copy stale - the write landed and the screen
        showed what it had before. The third time that shape has bitten: a cached list
        needs telling whenever something other than it does the writing.
        """
        self.games = self._client.games()
        self._games_at = time.monotonic()

    def set_game_tags(self, game_id: str, tags: list[str]) -> None:
        self._client.set_tags(game_id, tags)
        self._forget_game(game_id)
        self.read_tags()

    def set_table_tags(self, game_id: str, table_id: str, tags: list[str]) -> None:
        self._client.set_table_tags(game_id, table_id, tags)
        self._forget_tables(game_id)
        self.read_tags()

    def asset_keys(self) -> list[str]:
        """The asset kinds this library reports, in registry order where it knows them.

        The games resource has an asset vocabulary of its own - `ini` for the
        table INI, one `alt_color` covering Serum and VNI - so the keys are taken from
        what it sends rather than from `asset_registry`. Only the kinds the library
        collects: the resource sends every one, and a column for a kind switched off
        would ask a question the library said it does not.
        """
        seen: set[str] = set()
        for game in self.games:
            seen.update(game.get("assets") or {})
        return sorted(seen & self.kept_kinds()["asset"])

    def kinds(self) -> list[str]:
        seen: set[str] = set()
        for entries in self.media.values():
            seen.update(entries)
        return sorted(seen)

    def preferences(self, scope: str) -> dict[str, Any]:
        """A stored preference, as it stands.

        Cached, and deliberately does not fetch: this is read while a grid is being
        built, which is on the event loop, where an HTTP call is refused. `warm` does
        the reading, off the loop, before anything draws.
        """
        return self._prefs.get(scope) or {}

    def warm(self, *scopes: str) -> None:
        """Read these preferences. Off the event loop, once per session."""
        together(*(partial(self._warm_one, scope) for scope in scopes
                   if scope not in self._prefs))

    def _warm_one(self, scope: str) -> None:
        try:
            self._prefs[scope] = self._client.preferences(scope) or {}
        except Exception:
            logger.warning("manager: could not read %s", scope, exc_info=True)
            self._prefs[scope] = {}

    def put_preferences(self, scope: str, value: dict[str, Any]) -> None:
        """Write it, and keep the cache honest without a round trip to prove it."""
        self._client.put_preferences(scope, value)
        self._prefs[scope] = value

    def game_rows(self) -> list[dict[str, Any]]:
        rows = []
        shown = self.game_art()
        on_entry = Counter(one["vps_id"] for one in self.games if one.get("vps_id"))
        for game in self.games:
            game_id = game["id"]
            entries = self.media.get(game_id, {})
            rows.append({
                "id": game_id,
                "name": game.get("name") or "",
                "manufacturer": game.get("manufacturer") or "",
                "year": game_tables.year_of(game.get("year")),
                # Its own field, so sorting and filtering stay on the name.
                "said": game_tables.made(game),
                **_art_of(shown, game_id),
                "game_type": game.get("type") or "",
                # No rom or version: both were the default table's reported as the
                # game's, the columns that showed them are gone, and nothing has read
                # them since.
                "table_count": int(game.get("table_count") or 0),
                # The absence, not the id: the grid marks what needs attention, and
                # nobody reads a VPS id down a column. The id itself is one click away
                # in the panel, which is where it can be checked against the catalog.
                "vps_match": game_tables.vps_match(game),
                "vps_shared": game_tables.VPS_SHARED
                if on_entry[game.get("vps_id") or ""] > 1 else "",
                "vps_matched_by": game_tables.how_matched(game),
                "hidden": game_tables.HIDDEN_CELL if game.get("hidden") else "",
                "private": game_tables.PRIVATE_CELL if game.get("private") else "",
                "rating": game.get("rating") or 0,
                "themes": list(game.get("themes") or []),
                "tags": [*((game.get("user") or {}).get("tags") or []),
                         *(game.get("derived_tags") or [])],
                "collections": [str(one.get("name") or "") for one in
                                (self._game_collections or {}).get(game_id, [])],
                # One field per asset kind, the same shape as media below. What used
                # to sit here was a single "Assets" count computed from `entries` -
                # the *media* map - so the column read as assets and counted media,
                # beside a Media group showing the same thing per kind.
                **{f"asset_{key}": game_tables.ASSET_PRESENT if entry.get("present")
                   else game_tables.ASSET_MISSING
                   for key, entry in (game.get("assets") or {}).items()},
                # One field per kind, so media reads as columns over game rows. Blank
                # rather than a cross when absent: a sparse matrix stays scannable,
                # a full one does not.
                **{f"media_{kind}": _glyph(entry) for kind, entry in entries.items()},
                # The same cell, drawn as a picture. Both live on the row so switching
                # renderer is a redraw rather than a reload - which is the whole point
                # of separating what a field holds from how it is shown.
                **{f"thumb_{kind}": _thumb(game_id, kind, entry)
                   for kind, entry in entries.items()},
            })
        return rows
