"""What can be done to a game: make one, describe it, rate it, record what it has done.

The game is the machine; `table_ops` handles the launchable files inside it. Everything
here writes the game's `.info` and answers with what a caller would ask for next.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Iterable
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from common import service_errors
from common.config import path_state
from common.failures import why
from common.games import (
    auto_match,
    derived_tags,
    game_identity,
    game_lens,
    game_metadata,
    game_service,
    library_folders,
    library_vps_state,
    media_fill,
    order_record,
)
from common.games.game_metadata import (
    GUIDES_FIELD,
    adopt_vps_details,
    game_private,
    guide_address,
    guide_source,
    guides_on_wire,
    is_catalog_guide,
    load_game_meta,
    persist_game_meta,
    reset_game_play_record,
    set_asset_source,
    set_game_favorite,
    set_game_play_record,
    set_game_private,
    set_game_rating,
    set_game_tags,
    vps_details_differ,
)
from common.games.game_repository import refresh_game, reread_game
from common.games.tables import table_entries
from common.host import dof_service, launch_state
from common.i18n import t
from common.uploads.asset_import_service import vps_folder_name

# Which keys this level will accept, and what each is called on disk. Declared rather than
# inferred, so sending a table's key here is refused instead of silently doing nothing - a
# write that reports success and changes nothing is the worst of the options.
GAME_OVERRIDES = {
    **{alt: alt for alt, _ in game_metadata.GAME_OVERRIDES.values()},
    "alt_vps_id": "alt_vpsid",
    "frontend_dof_event": "frontend_dof_event",
}


def create(name: str, location_id: str = "") -> dict:
    """Make a folder with a record in it, in the location new games go to.

    The only way to bring a game into being without a file arriving: an upload creates one
    on the way past, and every other write needs a game that already exists.
    """
    try:
        folder = game_service.create_game(name, location_id)
    except FileExistsError as exc:
        raise service_errors.BlockedError(t("error.games.folder_exists"),
                                          details={"path": exc.filename}) from exc
    except ValueError as exc:
        raise service_errors.RefusedError(
            why(exc), details=_where_else(location_id)) from exc
    except OSError as exc:
        raise service_errors.BlockedError(why(exc)) from exc

    # Canonically, never by spelling. Re-reading one folder resolves the path it is given,
    # so the game just created carries the real path while every game a scan found carries
    # the location's own spelling - and under /var on macOS those differ.
    wanted = path_state.canonical(str(folder))
    made = next((game for game in game_lens.catalog().values()
                 if path_state.canonical(str(game.full_path_game)) == wanted),
                None)
    if made is None:
        # The folder and its record are on disk and the library did not pick them up.
        # Saying so beats a 500: what was asked for happened, and what is wrong is that the
        # location it landed in is not one this install reads.
        raise service_errors.BlockedError(t("error.games.created_unseen"),
                                          details={"path": str(folder)})
    return game_lens.detail(game_identity.game_id(made))


def create_from_vps(vps_id: str, location_id: str = "") -> dict:
    """Make a game with no tables from the catalog entry `vps_id` names, matched to it.

    The folder is named as an import names one, so the same machine added here and by a
    table upload meets in one folder. Nothing is downloaded; the automatic media fill is
    only requested, and runs when that setting is on.
    """
    wanted = str(vps_id or "").strip()
    entry = next((one for one in game_service.load_vpsdb()
                  if str(one.get("id") or "") == wanted), None) if wanted else None
    if entry is None:
        raise service_errors.NotFoundError(t("error.games.unknown_entry"),
                                           details={"vps_id": wanted})
    made = create(vps_folder_name(entry), location_id)
    folder = Path(made["folder"])
    try:
        made = match_game(made["id"], wanted)
    except Exception:
        # Taken back whole: left behind, an unmatched folder would answer every retry with
        # "already there" and leave a game nobody asked for in the library.
        shutil.rmtree(folder, ignore_errors=True)
        refresh_game(folder)
        raise
    library_vps_state.note_in_library(wanted)
    media_fill.request([made["folder"]])
    return made


def _where_else(wanted: str) -> dict:
    """The locations that would have worked, for a refusal to offer."""
    where = library_folders.destination(wanted)
    return {"alternatives": [{"location_id": one.location_id, "name": one.name,
                              "path": one.path} for one in where.alternatives]}


def set_details(game_id: str, values: dict) -> dict:
    """Describe a game no catalog has matched.

    Everything in a game's details normally arrives from VPSdb, which leaves nothing for
    one that came from somewhere else - a library converted from another frontend carries
    a year and a manufacturer, and they would otherwise be read and then dropped.

    Not where a VPS id goes. Saying which catalog record a game is claims an identity
    rather than describing a machine, and the alt_vps_id override is where that is said.
    """
    game = game_lens.game_for_writing(game_id)
    try:
        game_service.set_details(Path(str(game.full_path_game)), values)
    except FileNotFoundError as exc:
        raise service_errors.BlockedError(
            t("error.games.info_missing"),
            details={"path": os.fsdecode(exc.filename or game.full_path_game)}) from exc
    except (OSError, ValueError) as exc:
        raise service_errors.BlockedError(why(exc)) from exc
    return game_lens.detail(game_id)


def match_game(game_id: str, vps_id: str, guess: bool = False) -> dict:
    """Match a game to the catalog entry `vps_id` names, as an import does.

    Recorded as *Matched on import* - which a later rematch leaves alone - because the
    source named the entry or its IPDB number. With `guess` the entry was found by name
    alone, and is recorded as the plain guess a rematch is free to replace. The match
    alone: the game's tables and anything the person overrode stay as they are.
    """
    game = game_lens.game_for_writing(game_id)
    wanted = str(vps_id or "").strip()
    entry = next((one for one in game_service.load_vpsdb()
                  if str(one.get("id") or "") == wanted), None) if wanted else None
    if entry is None:
        raise service_errors.NotFoundError(t("error.games.unknown_entry"),
                                           details={"vps_id": wanted})
    try:
        auto_match.adopt_guess(
            game, entry, matched_by="" if guess else game_metadata.MATCHED_ON_IMPORT)
    except OSError as exc:
        raise service_errors.BlockedError(why(exc)) from exc
    reread_game(game)
    return game_lens.detail(game_id)


def set_rating(game_id: str, rating: Any) -> dict:
    """Set `User.Rating` on a game, 0-10."""
    game = game_lens.game_for_writing(game_id)
    stored = set_game_rating(game, rating)
    reread_game(game)
    return {"rating": stored}


def set_tags(game_id: str, tags: Iterable[str]) -> dict:
    """The whole set, not a bag: a repeat is dropped, and case is left alone so two
    spellings stay two tags until somebody merges them."""
    game = game_lens.game_for_writing(game_id)
    derived_tags.refuse_added(game, tags := list(tags))
    stored = set_game_tags(game, tags)
    reread_game(game)
    return {"tags": stored}


def set_guides(game_id: str, wanted: list[dict[str, Any]]) -> dict:
    """The game's guides, in `wanted`'s order. An entry naming a stored guide by its
    address keeps it and sets whether it is hidden; any other entry is a new guide of
    the person's own."""
    from common.games.info_file import GUIDES_KEY

    game = game_lens.game_for_writing(game_id)
    config = load_game_meta(game)
    config[GUIDES_KEY] = curate_guides(config.get(GUIDES_KEY) or [], wanted)
    persist_game_meta(game, config)
    reread_game(game)
    return {"guides": guides_on_wire(config, hidden=True)}


def curate_guides(held: list[Any], wanted: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from common.games.info_file import GUIDE_KINDS, GUIDE_TUTORIAL
    from common.games.info_migration import GUIDE_ORIGIN

    stored = {guide_address(one): one for one in held if isinstance(one, dict)}
    out: list[dict[str, Any]] = []
    for one in wanted:
        address = str(one.get("url") or "").strip()
        if not address:
            raise service_errors.RefusedError(t("error.games.guide_without_address"))
        if any(guide_address(kept) == address for kept in out):
            raise service_errors.RefusedError(t("error.games.guide_twice", url=address))
        kept = stored.get(address)
        if kept is None:
            kind = str(one.get("kind") or GUIDE_TUTORIAL)
            if kind not in GUIDE_KINDS:
                raise service_errors.RefusedError(t("error.games.guide_kind_unknown",
                                                    kind=kind, kinds=", ".join(GUIDE_KINDS)))
            if urlparse(address).scheme not in ("http", "https"):
                raise service_errors.RefusedError(t("error.games.guide_bad_address",
                                                    url=address))
            kept = {"kind": kind, GUIDE_ORIGIN: "user",
                    "title": str(one.get("title") or "").strip(), "authors": [],
                    "url": address, "youtube_id": ""}
        record = {key: value for key, value in kept.items() if key != "hidden"}
        if one.get("hidden"):
            record["hidden"] = True
        out.append(record)
    kept_addresses = {guide_address(one) for one in out}
    if any(is_catalog_guide(one) and address not in kept_addresses
           for address, one in stored.items()):
        raise service_errors.RefusedError(t("error.games.guide_listed"))
    return out


def delete_from_disk(game_id: str) -> dict:
    """Remove a game's whole folder: its tables, media, .info and everything else in it.

    Refused while that game is running, and for a folder that is, or holds, a location the
    library reads - removing it would take every other game with it. Nothing goes to a
    trash, so the caller asks first.
    """
    game = game_lens.game_for_writing(game_id)
    folder = Path(str(game.full_path_game))
    wanted = path_state.canonical(str(folder))
    if launch_state.running(game_id, game.game_dir_name):
        raise service_errors.BlockedError(t("error.games.delete_running"),
                                          details={"path": str(folder)})
    for place in library_folders.answering():
        held = path_state.canonical(place.path)
        if library_folders.inside(wanted, held):
            raise service_errors.RefusedError(t("error.games.delete_location"),
                                              details={"path": str(folder)})
    meta: dict[str, Any] | None
    try:
        meta = load_game_meta(game)
    except (ValueError, OSError):
        meta = getattr(game, "meta_config", None)
    table_ids = list(table_entries(meta))
    try:
        shutil.rmtree(folder)
    except OSError as exc:
        refresh_game(folder)
        raise service_errors.BlockedError(why(exc), details={"path": str(folder)}) from exc
    order_record.drop(table_ids)
    refresh_game(folder)
    return {"deleted": True, "folder": str(folder)}


def set_favorite(game_id: str, favorite: bool) -> dict:
    """Set `User.Favorite` on a game."""
    game = game_lens.game_for_writing(game_id)
    stored = set_game_favorite(game, favorite)
    reread_game(game)
    return {"favorite": stored}


def set_private(game_id: str, private: bool) -> dict:
    """Set `vpinfe.private` on a game."""
    game = game_lens.game_for_writing(game_id)
    stored = set_game_private(game, private)
    reread_game(game)
    return {"private": stored}


def set_private_many(game_ids: Iterable[str], private: bool) -> dict:
    """Set `vpinfe.private` on every game named. An id the library does not hold refuses
    the lot before anything is written."""
    games = [game_lens.game_for_writing(one) for one in dict.fromkeys(game_ids)]
    wanted = bool(private)
    changed = [game for game in games if game_private(load_game_meta(game)) != wanted]
    for game in changed:
        set_game_private(game, wanted)
        reread_game(game)
    return {"private": wanted, "games": len(games), "changed": len(changed)}


def set_play_record(game_id: str, play_count: int | None = None,
                    play_time_seconds: int | None = None,
                    last_played: int | None = None) -> dict:
    """Set a game's counters, for a library that arrives already played.

    A library converted from another frontend carries a play count and a last-played date,
    and dropping them makes a collection that sorts by either of those wrong on arrival.
    A field left out is left alone.
    """
    game = game_lens.game_for_writing(game_id)
    record = set_game_play_record(game, play_count=play_count,
                                  run_time_seconds=play_time_seconds,
                                  last_played=last_played)
    reread_game(game)
    return record


def reset_play_record(game_id: str) -> dict:
    """Put the counters back to nothing, leaving rating, favorite and tags alone.
    Setting a count to a number is the migration case, and is `set_play_record`."""
    game = game_lens.game_for_writing(game_id)
    record = reset_game_play_record(game)
    reread_game(game)
    return record


def _dof_event(value: Any) -> str:
    """A letter and a number as the helper is told it, or empty for a different event on
    every selection. Anything else would be written and then fail when the game is
    selected."""
    try:
        return dof_service.normalize_event_token(str(value)) or ""
    except ValueError as exc:
        raise service_errors.RefusedError(t("dof.event.malformed")) from exc


def set_overrides(game_id: str, changes: dict) -> dict:
    """What the user says about the machine, kept beside what VPS said.

    A patch: only what is sent is written, because the overrides are edited one field at a
    time and restating the others would make every save a race with whatever another
    surface changed meanwhile.
    """
    game = game_lens.game_for_writing(game_id)
    unknown = set(changes) - set(GAME_OVERRIDES)
    if unknown:
        raise service_errors.RefusedError(
            t("error.games.table_keys", keys=", ".join(sorted(unknown))))
    if changes.get("frontend_dof_event") is not None:
        changes = {**changes, "frontend_dof_event": _dof_event(changes["frontend_dof_event"])}

    game_dir = Path(game.full_path_game)
    try:
        for name, value in changes.items():
            game_service.update_vpinfe_setting(game_dir, GAME_OVERRIDES[name], value)
    except (OSError, ValueError) as exc:
        raise service_errors.BlockedError(why(exc)) from exc
    return game_lens.detail(game_id)["overrides"]


def declare_no_match(game_id: str) -> dict:
    """Say this machine is in no catalog, and answer with the overrides that leaves."""
    game = game_lens.game_for_writing(game_id)
    try:
        game_service.update_vpinfe_setting(Path(game.full_path_game), "alt_vpsid", None)
    except (OSError, ValueError) as exc:
        raise service_errors.BlockedError(why(exc)) from exc
    return game_lens.detail(game_id)["overrides"]


def set_file_source(game_id: str, path: str, vps_file_id: str) -> dict:
    """Bind one media or asset file to the upstream record somebody says it is.

    Addressed by path rather than by kind and tier, because the ledger is keyed by path and
    a folder can hold several files of one kind. The path is not required to resolve to
    anything: binding a file the resolver currently passes over is legitimate, and refusing
    it would make the tier rules govern what may be recorded.
    """
    game = game_lens.game_for_writing(game_id)
    try:
        bound = set_asset_source(game, path, vps_file_id)
    except ValueError as exc:
        raise service_errors.RefusedError(why(exc)) from exc
    reread_game(game)
    return bound


def vps_details(game_id: str) -> dict:
    """Where the game's details and the entry it is matched to disagree."""
    game = game_lens.game_or_refuse(game_id)
    entry = game_service.matched_vps_entry(game)
    if not entry:
        return {"differs": []}
    found = vps_details_differ(load_game_meta(game), entry)
    return {"differs": [{"field": field, **_both_said(field, ours, theirs), "new": new}
                        for field, (ours, theirs, new) in found.items()]}


def adopt_details(game_id: str, fields: Iterable[str] | None = None) -> dict:
    """Make the named details describe the entry the game is matched to.

    None takes everything that differs. `Info.VPSId` is never among them - it is what
    VPS supplied, and the value a surface offers to revert a corrected match to.
    """
    game = game_lens.game_for_writing(game_id)
    entry = game_service.matched_vps_entry(game)
    if not entry:
        raise service_errors.NotFoundError(t("error.games.unmatched"),
                                           details={"game_id": game_id})
    adopt_vps_details(game, entry, fields)
    reread_game(game)
    return vps_details(game_id)


def _both_said(field: str, ours: Any, theirs: Any) -> dict[str, str]:
    """The two sides as lines a person reads. Guides are named by the site they live on,
    then by address, then by everything that is compared, until the two sides differ."""
    mine, yours = _said(ours, field), _said(theirs, field)
    if field == GUIDES_FIELD:
        for name in (guide_address, _guide_in_full):
            if mine != yours:
                break
            mine, yours = (", ".join(name(one) for one in side) for side in (ours, theirs))
    return {"ours": mine, "theirs": yours}


def _guide_in_full(guide: dict[str, Any]) -> str:
    return " ".join(part for part in (guide_address(guide), str(guide.get("youtube_id") or ""),
                                      str(guide.get("kind") or "")) if part)


def _said(value: Any, field: str = "") -> str:
    """One line a person reads, whatever the field holds - a year is a number and themes
    are a list, and a caller rendering a comparison wants neither shape."""
    if field == GUIDES_FIELD:
        return ", ".join(name for name in map(guide_source, value) if name)
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    return str(value if value is not None else "")
