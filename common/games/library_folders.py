"""Where this install looks for entries.

A location is either a **root**, whose children are game folders, or a **single game
folder**. Both kinds coexist in one list, so a library can be a share plus the two
folders somebody keeps elsewhere without either being second class.

The list is `locations` in `vpinfe.json`, and which one new entries are created in is
`updates.new_games_location`. **Per install, not per library** - a path is a fact about
one machine, and a share reachable from the cabinet may not be reachable from the laptop
reading the same library. The share a folder was last seen on is not a setting, so it is
kept on this install's row of the device registry, where a config file carried to another
machine does not take it along.

Whether a location is reachable and whether it can be written to are asked of the disk
when somebody looks, never stored. A stored answer is wrong the moment a mount drops,
and a mount dropping is the ordinary case this exists to report well.
"""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Iterable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from common import mounts, service_errors
from common.config import config_schema, path_state
from common.config.config_store import ConfigStore
from common.config.paths import get_ini_config
from common.devices.device_registry import DeviceRegistry, get_device_registry
from common.devices.install_identity import mint_id
from common.i18n import t
from common.settings import config_service

logger = logging.getLogger("vpinfe.common.games.library_folders")

LOCATIONS_KEY = "locations"
NEW_GAMES_SECTION = "updates"
NEW_GAMES_KEY = "new_games_location"
ORIGINS_KEY = "location_origins"

# Its children are game folders.
KIND_ROOT = "root"
# It is one game folder.
KIND_GAME = "game"
KINDS = (KIND_ROOT, KIND_GAME)


def mint_location_id() -> str:
    return mint_id()


class FolderHeldError(ValueError):
    """The folder a row was moved onto is another row's. `held` is that row."""

    def __init__(self, held: Location) -> None:
        super().__init__(held.path)
        self.held = held


@dataclass(frozen=True)
class Location:
    location_id: str
    path: str
    kind: str = KIND_ROOT
    # The share it was last seen on.
    origin: mounts.Origin | None = None

    @property
    def name(self) -> str:
        """What to call it where the whole path will not fit.

        The last two segments, not the last one: a library is very often a folder called
        `tables` or `games`, and two of those side by side would draw the same row twice.
        """
        parts = [p for p in Path(self.path).parts if p not in ("/", "\\")]
        return "/".join(parts[-2:]) if len(parts) > 1 else (parts[0] if parts else self.path)

    def as_item(self) -> dict[str, Any]:
        """The row as `locations` holds it. The share it was seen on is not part of it."""
        return {"id": self.location_id, "path": self.path, "kind": self.kind}

    @classmethod
    def from_item(cls, raw: dict[str, Any]) -> Location | None:
        location_id = str(raw.get("id", "") or "").strip()
        path = str(raw.get("path", "") or "").strip()
        if not location_id or not path:
            return None
        kind = str(raw.get("kind", "") or "").strip() or KIND_ROOT
        return cls(location_id=location_id, path=path,
                   kind=kind if kind in KINDS else KIND_ROOT)


def states_of(held: list[Location],
              wait: float | None = None) -> dict[str, path_state.PathState]:
    """What the disk says about each location, keyed by id, asked of all of them at once.

    Asked as a folder that can be written to: a location nothing can be written to is Read
    only, which still reads and is not the same as one that is away.
    """
    answers = dict(zip((one.location_id for one in held),
                       path_state.look_all(
                           [path_state.Ask(one.path, path_state.DIR, writable=True,
                                           recorded=one.origin) for one in held], wait),
                       strict=True))
    seen = {one.location_id: origin for one in held
            if (origin := answers[one.location_id].origin) is not None
            and answers[one.location_id].state != path_state.NOT_CONNECTED
            and origin != one.origin}
    if seen:
        get_location_store().record_origins(seen)
    return answers


def state_of(location: Location) -> path_state.PathState:
    return states_of([location])[location.location_id]


def _one_per_place(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per place. A file edited by hand can hold the same folder twice, and two
    rows for one place would each report their own state; the id is what the state, the
    write target and the order are all keyed by. Compared as spelled, not through
    path_state.canonical(): resolving a path on a dead mount blocks."""
    seen: set[str] = set()
    kept = []
    for item in items:
        path = str(item.get("path") or "").strip()
        key = os.path.normpath(os.path.expanduser(path))
        if not path or key in seen:
            logger.warning("Ignoring a second location for %s", path)
            continue
        seen.add(key)
        kept.append(item)
    return kept


config_schema.register_normalizer(LOCATIONS_KEY, _one_per_place)


def _refuse_bad_places(items: list[dict[str, Any]], _held: list[dict[str, Any]]) -> None:
    """A write that names no folder, a relative one, or one another row holds is
    refused by name; the normalizer would drop the row without saying which."""
    held: dict[str, str] = {}
    for item in items:
        path = str(item.get("path") or "").strip()
        if not path:
            raise config_schema.ConfigListError(t("error.library_folders.needs_path"))
        if not os.path.isabs(os.path.expanduser(path)):
            raise config_schema.ConfigListError(t("error.library_folders.not_absolute"))
        key = path_state.canonical(path)
        if key in held:
            raise config_schema.ConfigListError(
                t("error.library_folders.duplicate", name=os.path.basename(key) or key))
        held[key] = str(item.get("id"))


config_schema.register_validator(LOCATIONS_KEY, _refuse_bad_places)


class LocationStore:
    """Every location this install looks in, and which one new entries are created in."""

    def __init__(self, folder: Path | str | None = None) -> None:
        """Without `folder` this is the install's own. With one, the list lives in its
        `vpinfe.json` and the learned shares in its `devices.json`, so a caller that
        relocates one cannot leave the other on this machine's."""
        self._folder = Path(folder) if folder is not None else None
        self._config: ConfigStore | None = None
        self._registry: DeviceRegistry | None = None
        self._lock = threading.RLock()

    def _core(self) -> ConfigStore:
        if self._folder is None:
            return get_ini_config()
        if self._config is None:
            self._folder.mkdir(parents=True, exist_ok=True)
            self._config = ConfigStore(str(self._folder / "vpinfe.json"))
        else:
            self._config.reread()
        return self._config

    def _devices(self) -> DeviceRegistry:
        if self._folder is None:
            return get_device_registry()
        if self._registry is None:
            self._registry = DeviceRegistry(self._folder / "devices.json")
        return self._registry

    # -- reading -------------------------------------------------------------

    def locations(self) -> list[Location]:
        """Every location, in the order the list holds them."""
        with self._lock:
            return self._load(self._core())

    def get(self, location_id: str) -> Location | None:
        wanted = str(location_id or "").strip()
        if not wanted:
            return None
        return next((one for one in self.locations()
                     if one.location_id == wanted), None)

    def write_to(self) -> Location | None:
        """Where a new entry's folder is created. A user choice, so it is stored rather
        than derived - but it falls back to the first writable one, because an install
        that has never been asked still has to be able to create something."""
        held = self.locations()
        wanted = self.chosen_write_to()
        states = states_of(held)
        named = next((one for one in held if one.location_id == wanted), None)
        if named is not None and states[named.location_id].writable:
            return named
        return next((one for one in held
                     if one.kind == KIND_ROOT and states[one.location_id].writable), None)

    def chosen_write_to(self) -> str:
        """The location somebody chose, or "" - without `write_to`'s fallback, so a
        destination gone read-only is refused by name, not swapped for another."""
        with self._lock:
            return str(self._core().value(NEW_GAMES_SECTION, NEW_GAMES_KEY) or "").strip()

    # -- writing -------------------------------------------------------------

    def save(self, locations: list[Location], write_to: str = "") -> None:
        """Replace the whole list, in the order given, and the folder new entries go to."""
        with config_service.writing(), self._lock:
            config = self._core()
            config.set_list(LOCATIONS_KEY, [one.as_item() for one in locations])
            config.set_value(NEW_GAMES_SECTION, NEW_GAMES_KEY, write_to)
            config_service.written(config, [LOCATIONS_KEY,
                                            f"{NEW_GAMES_SECTION}.{NEW_GAMES_KEY}"])

    def put(self, location: Location) -> Location:
        """Change the row that has this id, or add one last.

        With no row under this id, a folder already held is the row changed, under its own
        id. Either way the row keeps its place in the order, which is the priority.

        Raises `FolderHeldError` when this id's row is moved onto a folder another row
        holds.
        """
        wanted = path_state.canonical(location.path)
        with config_service.writing(), self._lock:
            config = self._core()
            held = self._load(config)
            named = next((i for i, one in enumerate(held)
                          if one.location_id == location.location_id), None)
            same = next((i for i, one in enumerate(held)
                         if path_state.canonical(one.path) == wanted), None)
            if named is not None and same not in (None, named):
                raise FolderHeldError(held[same])
            at = named if named is not None else same
            if at is None:
                config.set_list(LOCATIONS_KEY, [one.as_item() for one in [*held, location]])
                config_service.written(config, [LOCATIONS_KEY])
                return location
            location = Location(location_id=held[at].location_id,
                                path=location.path, kind=location.kind)
            config.set_list(LOCATIONS_KEY, [(location if i == at else one).as_item()
                                            for i, one in enumerate(held)])
            config_service.written(config, [LOCATIONS_KEY])
        return location

    def record_origins(self, origins: dict[str, mounts.Origin]) -> None:
        """Note the share each location was just seen on, with the path it was seen at, so
        a row later pointed somewhere else does not keep naming the old share. Ids it does
        not hold are ignored, and nothing is written when nothing changed."""
        with self._lock:
            held = {one.location_id: one for one in self.locations()}
            kept = self._origins()
            learned = {key: value for key, value in kept.items() if key in held}
            learned.update({key: {"path": held[key].path, "origin": origin.as_dict()}
                            for key, origin in origins.items() if key in held})
            if learned != kept:
                self._devices().set_self(**{ORIGINS_KEY: learned or None})

    # -- the list ------------------------------------------------------------

    def _load(self, config: ConfigStore) -> list[Location]:
        origins = self._origins()
        held: dict[str, Location] = {}
        for raw in _one_per_place([item for item in config.get_list(LOCATIONS_KEY)
                                   if isinstance(item, dict)]):
            one = Location.from_item(raw)
            if one is None or one.location_id in held:
                continue
            seen = origins.get(one.location_id)
            if isinstance(seen, dict) and seen.get("path") == one.path:
                one = replace(one, origin=mounts.Origin.from_dict(seen.get("origin")))
            held[one.location_id] = one
        return list(held.values())

    def _origins(self) -> dict[str, Any]:
        found = self._devices().self_get(ORIGINS_KEY, {})
        return found if isinstance(found, dict) else {}


@dataclass(frozen=True)
class Destination:
    """Where a new game folder would be created, or why it would not be.

    Both, because the answer a caller needs on a refusal is not only that it failed: it
    is which other places would work, so somebody can send this one there instead.
    """

    location: Location | None = None
    # Empty when there is somewhere to write. A sentence when there is not.
    reason: str = ""
    # Every writable root other than the one that was wanted, for a one-time override.
    alternatives: tuple[Location, ...] = ()

    @property
    def path(self) -> str:
        return self.location.path if self.location is not None else ""


def _writable_roots(held: list[Location],
                    states: dict[str, path_state.PathState]) -> tuple[Location, ...]:
    return tuple(one for one in held
                 if one.kind == KIND_ROOT and states[one.location_id].writable)


def destination(location_id: str = "") -> Destination:
    """Where a new game goes, or the reason it cannot go anywhere.

    **Refuses rather than quietly landing somewhere else.** The location marked "create
    new games here" is a choice somebody made and a row on screen says so; writing to a
    different one because that row went read-only makes the screen a lie, and they find
    out by looking for a game where they expected it.

    `location_id` names a one-time override, which is checked the same way - an override
    that cannot be written to is refused rather than falling back in its turn.
    """
    store = get_location_store()
    held = store.locations()
    states = states_of(held)
    writable = _writable_roots(held, states)
    chosen = store.chosen_write_to()
    if not any(one.location_id == chosen for one in held):
        chosen = ""
    wanted = str(location_id or "").strip() or chosen

    if not wanted:
        # Nothing chosen. The first writable root is the honest default rather than a
        # refusal: an install that has never been asked still has to be able to create.
        if writable:
            return Destination(location=writable[0])
        return Destination(reason=t("error.library_folders.nowhere_to_create"))

    named = next((one for one in held if one.location_id == wanted), None)
    others = tuple(one for one in writable if one.location_id != wanted)
    if named is None:
        return Destination(reason=t("error.library_folders.default_removed"),
                           alternatives=others)
    state = states[named.location_id]
    if named.kind != KIND_ROOT:
        return Destination(reason=t("error.library_folders.single_game", name=named.name),
                           alternatives=others)
    said = path_state.refusal(named.name, state)
    if said:
        return Destination(reason=said, alternatives=others)
    return Destination(location=named, alternatives=others)


def _folder_refusal(raw: str, state: path_state.PathState, called: str = "") -> str:
    if state.state == path_state.OK:
        return ""
    where = os.path.normpath(raw)
    home = next((one for one in configured()
                 if one.kind == KIND_ROOT and inside(os.path.normpath(one.path), where)),
                None)
    # A folder that refuses writes is that folder's own fact; one that cannot be reached
    # is, as often as not, the share the whole library hangs off.
    own = Path(raw).name or called
    name = own if home is None or state.state == path_state.READ_ONLY else home.name
    return path_state.refusal(name, state)


def writable_game_folder(game_dir: str | Path, called: str = "") -> Path:
    """A game's folder, once the disk has said it can be written to - or a refusal.

    The one question every writer into a game folder asks first, so a dropped share or a
    read-only one reads as the folder it hangs off ("its share is not mounted on this
    device") rather than as an errno halfway through a copy. Paths are compared as written
    and not resolved: resolving touches the disk, and that is the call that hangs on a
    dead mount. `called` names a game whose folder is not set, which has no folder name
    of its own to give.
    """
    raw = str(game_dir)
    said = _folder_refusal(raw, path_state.look(raw, path_state.DIR, writable=True), called)
    if said:
        raise service_errors.BlockedError(said)
    return Path(raw)


def blocked_games(games: Iterable[Any]) -> list[tuple[Any, str]]:
    """The games whose folders cannot be written to, each with the reason, for a sweep
    over many games to skip or refuse on. Every folder is asked at once."""
    held = list(games)
    asks = [path_state.Ask(str(g.full_path_game or ""), path_state.DIR, True) for g in held]
    out = []
    for game, state in zip(held, path_state.look_all(asks), strict=True):
        said = _folder_refusal(str(game.full_path_game or ""), state,
                               str(game.game_dir_name or ""))
        if said:
            out.append((game, said))
    return out


def portable_reference(game_dir: str, target: str) -> str:
    """How to store a path to a file outside a game folder, so a library survives being
    moved.

    Relative where the target is inside the same configured location as the game,
    because moving or sharing that whole tree then keeps the reference pointing at the
    same file. Absolute otherwise: a relative path out of the library would be a chain
    of `..` that means nothing once the library has moved, which is worse than an
    absolute path that at least fails honestly.
    """
    from common.games.tables import stored_reference

    wanted = path_state.canonical(target)
    home = path_state.canonical(game_dir)
    if not wanted or not home:
        return stored_reference(target)
    for location in answering():
        root = path_state.canonical(location.path)
        if not root:
            continue
        # Compared canonically, because two spellings of one place have to be recognized
        # as one - but the relative form is computed from those same canonical ends, so
        # what it walks is real rather than through whichever alias was typed.
        if inside(root, wanted) and inside(root, home):
            return Path(os.path.relpath(wanted, home)).as_posix()
    # Stored as given, not as resolved. Somebody who points through a symlink means the
    # symlink: baking today's target in would stop the reference following it, and a
    # mount point is exactly the kind of path that gets repointed.
    return stored_reference(os.path.normpath(os.path.expanduser(str(target or ""))))


def inside(root: str, path: str) -> bool:
    """Whether a path is at or under a root, compared as paths rather than as text -
    `/games2` starts with `/games` and is not in it."""
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def configured() -> list[Location]:
    """Every location the scan walks."""
    return get_location_store().locations()


def answering() -> list[Location]:
    """The configured locations that answered a look in time. A caller that resolves or
    lists each location's path takes these: doing so on one that did not answer never
    returns."""
    held = configured()
    answers = path_state.look_all([path_state.Ask(one.path) for one in held])
    return [one for one, answer in zip(held, answers, strict=True) if answer.reachable]


def seed(store: Any) -> None:
    """Turn the single root a 2.x install was configured with into its first location,
    and the folder new games go to. Runs while the ini is converted; what it read is
    removed after the writes, so a failed one leaves the 2.x value where the conversion
    restores it."""
    config = store.config
    root = str(config.get("general", "game_root_dir", fallback="") or "").strip()
    if not root:
        return
    location_id = mint_location_id()
    store.set_list(LOCATIONS_KEY, [*store.get_list(LOCATIONS_KEY),
                                   Location(location_id, root).as_item()])
    store.set_value(NEW_GAMES_SECTION, NEW_GAMES_KEY, location_id)
    config.remove_option("general", "game_root_dir")
    logger.info("Seeded the library location from the configured root: %s", root)


config_schema.register_seed("library_folders", seed)

_store: LocationStore | None = None


def get_location_store() -> LocationStore:
    global _store
    if _store is None:
        _store = LocationStore()
    return _store
