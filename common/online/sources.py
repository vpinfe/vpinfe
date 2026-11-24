"""Every place that offers files, behind one contract and one registry.

A source is something online that lists, offers or hands over files for the games VPS
knows: the spreadsheet itself, a catalog of pictures, an extension's own site. They share
seven answers, and only the first is required - a shelf reads what a source declares, so
a source that declares no `upgrades` simply has none:

- **what it is:** `id`, `name`, the `kinds` it serves, whether it names `versions`;
- **refresh:** `refresh(config)` fetches when it is due, on its own schedule, and says
  whether anything changed;
- **listing:** `listing()` is what it holds now, by id and the date it gives;
- **offers for a game:** `offers(who, kind)`, what it would give that you lack;
- **upgrades for a game:** `upgrades(who, kind)`, what is later than what you hold;
- **take:** `take(who, kind, row)` fetches the file and places it, the source's own doing.
  A source with no `take` answers `offers` with `Offer`s and core fetches each address;
- **acting on its own:** `automatic` names the settings by which it may fill or replace
  anything without being asked.

Built-in sources register here the way an extension's do. A new source costs the code that
talks to its site and one `register`; the pane, Settings and the API read this registry and
nothing else changes. A kind of file with no slot needs the slot added in core first, which
is why `register` refuses a kind core has none for.
"""

from __future__ import annotations

import logging
import re
import threading
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from common.config.config_store import ConfigStore

logger = logging.getLogger("vpinfe.common.online.sources")

KEY_PATTERN = re.compile(r"[a-z][a-z0-9_]*")
# The slot a pick fills when a kind of file has no slot of its own: a table is added.
TABLE = "table"
# `backglass` is also the picture, so the file a game keeps is asked for under its own name.
BACKGLASS_FILE = "backglass_file"
# Asset kinds no Add or Replace dialog is opened for, so no source is ever asked for one.
NO_DIALOG = frozenset({"game_info", "patch", "media"})


@dataclass(frozen=True)
class Offer:
    """One file a source will hand over for a slot.

    `size` is that source's own word for a variant - "4k" against vpinmediadb, empty
    for a source that publishes one of a thing - and only it has to understand it.
    """

    source: str
    name: str
    url: str
    kind: str
    size: str = ""
    md5: str = ""


@dataclass(frozen=True)
class Listed:
    """One thing a source holds now. `id` is the source's own and stays the same for the
    same thing; `version` and `date` are empty where the source gives none, and then
    nothing about it is dated by the source."""

    id: str
    vps_id: str
    kind: str
    version: str = ""
    date: str = ""


def _reachable() -> bool:
    return True


_cached_only: ContextVar[bool] = ContextVar("sources_cached_only", default=False)


@contextmanager
def cached_only() -> Iterator[None]:
    """Within this block a source answers from what it already holds and makes no request.
    For a page that is read on every visit: a source with nothing held yet answers as one
    with nothing to offer, and whatever loads it for real (a fill, an update) fills it."""
    token = _cached_only.set(True)
    try:
        yield
    finally:
        _cached_only.reset(token)


def may_fetch() -> bool:
    """False inside `cached_only`. A source that fetches its index on demand checks this
    before it does."""
    return not _cached_only.get()


@dataclass(frozen=True)
class Source:
    id: str
    name: str
    kinds: frozenset[str] = field(default_factory=frozenset)
    versions: bool = False
    url: str = ""
    # Who contributed it, and the key it has there. Empty for one that ships.
    extension: str = ""
    key: str = ""
    # No switch: every shelf starts from it.
    always_on: bool = False
    refresh: Callable[[ConfigStore], bool] | None = None
    listing: Callable[[], Iterable[Listed]] | None = None
    offers: Callable[[dict, str], Any] | None = None
    upgrades: Callable[[dict, str], Any] | None = None
    # The kinds `upgrades` is asked about, where they are not the `kinds` it serves: a source
    # that offers no file of a kind can still say a later one exists.
    upgrades_for: frozenset[str] = field(default_factory=frozenset)
    take: Callable[[dict, str, str], Any] | None = None
    take_many: Callable[[dict, list[dict]], Any] | None = None
    act: Callable[[dict, str, str, str], Any] | None = None
    look: Callable[[dict, str, str], Any] | None = None
    # (section, key) of each setting by which it may act without being asked.
    automatic: tuple[tuple[str, str], ...] = ()
    # Whether asking now can get an answer, for a caller about to ask about many games, so
    # a source that is down costs one failed request rather than one each.
    reachable: Callable[[], bool] = _reachable

    @property
    def built_in(self) -> bool:
        return not self.extension

    @property
    def hands_files(self) -> bool:
        """Whether core fetches what it offers: `offers` answers `Offer`s and there is no
        `take`."""
        return self.offers is not None and self.take is None


_lock = threading.RLock()
_sources: dict[str, Source] = {}
_built_in_loaded = False


def slot_kinds() -> frozenset[str]:
    """Every kind a dialog is opened for, by the name it is opened with: a media kind, an
    asset kind, the name the asset lens gives a kind that is a folder of files, and
    `table`."""
    from common.games import asset_registry
    from common.media.media_specs import MEDIA_SPECS

    asked = [spec for spec in asset_registry.ASSET_SPECS if spec.kind not in NO_DIALOG]
    return frozenset({TABLE, BACKGLASS_FILE, *(spec.kind for spec in MEDIA_SPECS),
                      *(spec.kind for spec in asked),
                      *(asset_registry.lens_kind(spec.kind) for spec in asked)})


def file_kind(kind: str) -> str:
    """The name a source knows an asset slot by: the registry's, except where a media kind
    shares it."""
    return BACKGLASS_FILE if kind == "backglass" else kind


def names_for(kind: str) -> frozenset[str]:
    """Every name a source may have offered a dialog opened for `kind` under: the name the
    dialog was opened with, the registry's kind behind it and the name the lens gives it."""
    from common.games import asset_registry
    from common.media.media_specs import canonical_kind

    # `table` is also an old spelling of the playfield picture, which is not what a
    # table is here.
    names = {kind} if kind in (TABLE, BACKGLASS_FILE) else {kind, canonical_kind(kind)}
    try:
        for spec in asset_registry.specs_named(kind):
            names |= {spec.kind, asset_registry.lens_kind(spec.kind)}
    except KeyError:
        pass
    return frozenset(names)


def register(source: Source) -> None:
    """Add a source, or replace the one with its id. Refuses one that serves a kind core has
    no slot for, one that lists kinds and cannot offer them, and one that offers none."""
    if not source.id or not source.name:
        raise ValueError("a source has an id and a name")
    if source.kinds and source.offers is None:
        raise ValueError(f"{source.id!r} serves {', '.join(sorted(source.kinds))} and "
                         "cannot offer them")
    if source.offers is not None and not source.kinds:
        raise ValueError(f"{source.id!r} offers no kind of file")
    unknown = sorted(source.kinds - slot_kinds())
    if unknown:
        raise ValueError(f"{source.id!r} offers {', '.join(unknown)}, which core has no slot "
                         f"for: {', '.join(sorted(slot_kinds()))}")
    with _lock:
        _sources[source.id] = source
    logger.info("%s is a source of %s", source.name, ", ".join(sorted(source.kinds)) or "nothing")


def contribute(extension: str, key: str, name: str, kinds: frozenset[str],
               offers: Callable[[dict, str], Any], take: Callable[[dict, str, str], Any],
               take_many: Callable[[dict, list[dict]], Any] | None = None,
               act: Callable[[dict, str, str, str], Any] | None = None,
               look: Callable[[dict, str, str], Any] | None = None) -> None:
    """Add the source an extension offers. Its id is namespaced by the extension, so two
    may name a source the same and a built-in id never collides with either."""
    if not KEY_PATTERN.fullmatch(key):
        raise ValueError(f"{key!r} is not a key: it is lower-case words joined by underscores")
    if not kinds:
        raise ValueError(f"{key!r} offers no kind of file")
    register(Source(id=f"{extension}.{key}", name=name, kinds=frozenset(kinds),
                    extension=extension, key=key, offers=offers, take=take,
                    take_many=take_many, act=act, look=look))


def unregister(source_id: str) -> None:
    with _lock:
        _sources.pop(source_id, None)


def forget(extension: str) -> None:
    """Drop every source an extension contributed."""
    with _lock:
        for one in [one for one in _sources.values() if one.extension == extension]:
            _sources.pop(one.id, None)


def _load_built_in() -> None:
    """Register the sources that ship, once, through `register` like any other. The lock is
    held throughout and the flag set last, so no caller sees a half-loaded registry and an
    import that fails is tried again by the next call."""
    global _built_in_loaded
    with _lock:
        if _built_in_loaded:
            return
        from common.online import vpinmediadb, vps_source

        for module in (vps_source, vpinmediadb):
            register(module.SOURCE)
        _built_in_loaded = True


def all_sources() -> list[Source]:
    """Every source: the ones that ship in the order they were declared, then the ones
    extensions contributed in the order they came."""
    _load_built_in()
    with _lock:
        held = list(_sources.values())
    return [*(one for one in held if one.built_in), *(one for one in held if not one.built_in)]


def contributed() -> list[Source]:
    return [one for one in all_sources() if not one.built_in]


def get(source_id: str) -> Source | None:
    _load_built_in()
    with _lock:
        return _sources.get(source_id)


def named(extension: str, key: str) -> Source | None:
    return get(f"{extension}.{key}")


def _switched_off() -> set[str]:
    from common.games.library_policy import get_library_policy

    return {name.lower() for name in get_library_policy().get("hidden_sources")}


def enabled(source: Source) -> bool:
    """Whether this install has not switched the source off in Settings."""
    return source.always_on or source.id.lower() not in _switched_off()


# What hands files over directly: the catalogs core fetches from.

def catalogs(enabled_ids: tuple[str, ...] | None = None) -> list[Source]:
    """The sources core fetches from, in the order they were declared. None means all of
    them."""
    found = [one for one in all_sources() if one.hands_files]
    if enabled_ids is None:
        return found
    wanted = {name.strip().lower() for name in enabled_ids if name.strip()}
    return [one for one in found if one.id.lower() in wanted]


def enabled_ids() -> tuple[str, ...]:
    """The catalogs this install has not switched off. Empty asks none."""
    off = _switched_off()
    return tuple(one.id for one in catalogs() if one.id.lower() not in off)


def offers(kind: str, vps_id: str, enabled_ids: tuple[str, ...] | None = None) -> list[Offer]:
    """What every enabled catalog has for this kind, for this game.

    One source being down is not the others failing: a source that raises is logged
    and skipped, because a catalog that cannot be reached should cost its own results
    and nothing else.
    """
    found: list[Offer] = []
    for source in catalogs(enabled_ids):
        if kind not in source.kinds or source.offers is None:
            continue
        try:
            found.extend(source.offers({"vps_id": vps_id}, kind))
        except Exception:
            logger.warning("Source %s could not be asked for %s", source.id, kind,
                           exc_info=True)
    return found


def url_for(source_id: str, kind: str, vps_id: str, size: str = "",
            enabled_ids: tuple[str, ...] | None = None) -> Offer | None:
    """The offer a named catalog publishes, or None. The only way a URL is produced."""
    for offer in offers(kind, vps_id, enabled_ids):
        if offer.source == source_id and (not size or offer.size == size):
            return offer
    return None


def watched() -> list[Source]:
    """The sources the watch asks to refresh, switched-off ones left out."""
    return [one for one in all_sources() if one.refresh is not None and enabled(one)]


def listing_sources() -> list[Source]:
    """The sources that say what they hold, switched-off ones left out."""
    return [one for one in all_sources() if one.listing is not None and enabled(one)]


def upgrade_sources() -> list[Source]:
    """The sources that can say what is later than what you hold. `versions` says only that
    a listing carries them, so a source that names none still answers."""
    return [one for one in all_sources() if one.upgrades is not None and enabled(one)]


def upgrade_kinds() -> frozenset[str]:
    """Every kind some enabled source can say a later one of: what it declares in
    `upgrades_for`, else the kinds it serves."""
    return frozenset(kind for one in upgrade_sources() for kind in (one.upgrades_for or one.kinds))


def upgrades(who: Mapping[str, Any], kind: str,
             among: Iterable[Source] | None = None) -> dict[str, Any]:
    """What each source that can say what is later says, by source id. A source with
    nothing later for this game is left out, and so is one that raises (logged), like
    `offers`. `among` is the sources to ask where the caller asks about many games and
    has `upgrade_sources` already: which are enabled is read from the settings, which
    costs more than the answers."""
    found: dict[str, Any] = {}
    for source in upgrade_sources() if among is None else among:
        asked = source.upgrades_for or source.kinds
        if source.upgrades is None or (asked and kind not in asked):
            continue
        try:
            later = source.upgrades(dict(who), kind)
            if later:
                found[source.id] = later
        except Exception:
            logger.warning("Source %s could not be asked what is later for %s", source.id,
                           kind, exc_info=True)
    return found


_changes: list[tuple[int, str]] = []
# Enough for a pane that was closed a while to learn what moved; past it, it redraws whole.
CHANGES_KEPT = 200


def changed(vps_id: str) -> None:
    """Note that what a source would offer for this VPS entry has moved, so an open
    Discovery pane draws it again. Safe from any thread."""
    vps_id = str(vps_id or "").strip()
    with _lock:
        _changes.append((_changes[-1][0] + 1 if _changes else 1, vps_id))
        del _changes[:-CHANGES_KEPT]


def changes_since(version: int) -> tuple[int, list[str]]:
    """The newest version, and the VPS ids that moved after `version`. An empty id means
    "any of them": the caller was too far behind to be told which."""
    with _lock:
        newest = _changes[-1][0] if _changes else 0
        if version > newest or (_changes and version < _changes[0][0] - 1):
            return newest, [""]
        return newest, sorted({vps_id for number, vps_id in _changes if number > version})


def clear() -> None:
    """For tests: forget every source an extension contributed and every change noted. The
    ones that ship stay."""
    with _lock:
        for one in [one for one in _sources.values() if not one.built_in]:
            _sources.pop(one.id, None)
        _changes.clear()
