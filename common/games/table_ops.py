"""What can be done to one of a game's tables: name it, rate it, bind it, forget it.

A table is the launchable artifact; the game is the machine. Everything here writes the
game's `.info` and answers with the table as the lens describes it, because a caller that
just changed one is about to redraw the row.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

from common import apps, service_errors
from common.config import path_state
from common.failures import why
from common.games import (
    derived_tags,
    game_lens,
    info_files,
    library_folders,
    mods,
    order_record,
    table_lens,
    tables,
)
from common.games.collection_resolver import visible_entries
from common.games.game import Game
from common.games.game_metadata import (
    game_title,
    load_game_meta,
    reset_table_play_record,
    set_table_rating,
    set_table_source,
    set_table_tags,
    vpinfe_section,
)
from common.games.game_repository import game_to_row, reread_game
from common.games.game_service import find_vps_release, release_fits
from common.games.info_file import MetaConfig
from common.games.tables import ABSENT_SINCE_KEY, entry_filename, table_entries
from common.host import launch
from common.i18n import t
from common.ids import new_id
from common.online import vps_formats

# Which keys each level will accept. Declared rather than inferred, so sending a table's
# key to the game route is refused instead of silently doing nothing - a write that
# reports success and changes nothing is the worst of the options.
TABLE_OVERRIDES = ("alt_launcher", "plugin_profile", "delete_nvram_on_close")


def _game_tables(game: Game) -> list[dict]:
    rows = table_lens.table_rows(game, game_to_row(game), launcher_settings=True)
    mods.hold((row.get("source") or {}).get("mod_of") for row in rows)
    return rows


def rows_of(game_id: str) -> dict:
    """Every table this game offers."""
    game = game_lens.game_or_refuse(game_id)
    return {"tables": _game_tables(game)}


def row_or_refuse(game: Game, table_id: str) -> dict:
    """One table as the lens describes it, or a refusal naming it."""
    found = next((row for row in _game_tables(game)
                  if row.get("id") == table_id), None)
    if found is None:
        raise service_errors.NotFoundError(t("error.games.table_not_found"),
                                           details={"table": table_id})
    return found


def filename_or_refuse(game: Game, table_id: str) -> str:
    """The .vpx an id names. Distinct from the media lens's stem lookup: this one wants
    the name on disk, not the stem a file would be named after."""
    entry = table_entries(load_game_meta(game)).get(table_id)
    filename = entry_filename(entry) if isinstance(entry, dict) else ""
    if not filename:
        raise service_errors.NotFoundError(
            t("error.games.table_not_found"),
            details={"game": game.game_dir_name, "table": table_id})
    return filename


def set_hidden(game_id: str, table_id: str, hidden: bool) -> dict:
    """Take a table out of play without taking it off disk.

    The two are different acts and only one is reversible by itself: a hidden table is
    still there with its stats and its match, and a patch base that stopped being offered
    is exactly what this is for.
    """
    game = game_lens.game_for_writing(game_id)
    filename = filename_or_refuse(game, table_id)
    MetaConfig(str(info_files.info_path_of(game))).set_table_hidden(filename, bool(hidden))
    game = reread_game(game)
    return {"table": row_or_refuse(game, table_id), "default": _offered_first(game)}


def _offered_first(game: Game) -> dict | None:
    """The row of the table this game offers first, or None when it offers none."""
    offered = visible_entries(game)
    wanted = offered[0].get(tables.TABLE_ID_KEY) if offered else ""
    if not wanted:
        return None
    return next((row for row in _game_tables(game)
                 if row.get("id") == wanted), None)


def extract_script(game_id: str, table_id: str) -> dict:
    """Write the table's script out as a `<table>.vbs` next to the .vpx.

    **This changes which script the table runs.** VPX loads a sidecar in place of the one
    inside the .vpx, so extracting is how a table is patched - and it is why the install
    reports the script as internal or external rather than as merely extracted.

    Runs the configured launcher, so it answers for the machine it is called on: an
    install with no VPX cannot do this.
    """
    from common.games import game_service

    game = game_lens.game_for_writing(game_id)
    filename = filename_or_refuse(game, table_id)
    game_dir = Path(game.full_path_game or "")
    if not (game_dir / filename).is_file():
        raise service_errors.NotFoundError(t("error.games.file_gone"),
                                           details={"table": table_id})
    table_id = tables.entry_for_filename(table_entries(game.meta_config), filename)[0]
    # Asked before the work rather than read out of the failure: with the table's file
    # accounted for, every remaining launcher error means this machine cannot do it,
    # which is what /launch already answers - not a missing resource.
    try:
        launch.binary_for(table_id, filename)
    except launch.LaunchUnavailableError as exc:
        raise service_errors.UnavailableError(why(exc)) from exc
    try:
        game_service.extract_vbs(game_dir, filename, table_id)
    except (OSError, launch.LaunchUnavailableError) as exc:
        raise service_errors.RefusedError(why(exc)) from exc
    return row_or_refuse(game, table_id)


def delete_script(game_id: str, table_id: str) -> dict:
    """Take the sidecar away, which puts the table back on the script inside its .vpx.

    Whatever the sidecar held goes with it - a patch, an edit - and nothing else knows
    what was in it, which is why the surface asks first.
    """
    from common.games import media_ops

    game = game_lens.game_for_writing(game_id)
    game_dir = Path(game.full_path_game or "")
    script = game_dir / f"{media_ops.stem_or_refuse(game, table_id)}.vbs"
    if not script.is_file():
        raise service_errors.NotFoundError(t("error.games.no_script"),
                                           details={"table": table_id})
    script.unlink()
    return row_or_refuse(game, table_id)


def set_rating(game_id: str, table_id: str, rating: Any) -> dict:
    """A table's own rating, which refines the game's rather than replacing it."""
    game = game_lens.game_for_writing(game_id)
    set_table_rating(game, filename_or_refuse(game, table_id), rating)
    game = reread_game(game)
    return row_or_refuse(game, table_id)


def refuse_unplayable_release(release: dict, entry: dict, table_id: str,
                              vps_file_id: str) -> None:
    """Refuse a release filed under a format the app playing this entry does not play."""
    if release and not release_fits(release, entry):
        raise service_errors.RefusedError(
            t("error.games.release_other_format",
              app=apps.app_name(tables.app_of(entry) or apps.default_app().id),
              format=vps_formats.name(str(release.get("tableFormat") or ""))),
            details={"table": table_id, "vps_file_id": vps_file_id})


def set_source(game_id: str, table_id: str, vps_file_id: str) -> dict:
    """Bind one table to the upstream release a person says it is, or record that it is none.

    A person's answer, which replaces a tie VPinFE made itself. An empty id takes the tie
    back and keeps the sweep from tying the table again. A release filed under a format
    this table's app does not play is refused: it would name a build the table cannot be.
    """
    game = game_lens.game_for_writing(game_id)
    filename = filename_or_refuse(game, table_id)
    wanted = str(vps_file_id or "").strip()
    release = find_vps_release(wanted) if wanted else {}
    entry = table_entries(load_game_meta(game)).get(table_id) or {}
    refuse_unplayable_release(release, entry, table_id, wanted)
    set_table_source(game, filename, vps_file_id)
    game = reread_game(game)
    return row_or_refuse(game, table_id)


def set_tags(game_id: str, table_id: str, tags: Any) -> dict:
    game = game_lens.game_for_writing(game_id)
    derived_tags.refuse_added(game, tags := list(tags))
    stored = set_table_tags(game, filename_or_refuse(game, table_id), tags)
    reread_game(game)
    return {"tags": stored}


def reset_play_record(game_id: str, table_id: str) -> dict:
    """One table's counters. The game's total is not touched: they are two records of two
    things, and a game played on one build has still been played."""
    game = game_lens.game_for_writing(game_id)
    record = reset_table_play_record(game, filename_or_refuse(game, table_id))
    reread_game(game)
    return record


def set_default(game_id: str, table_id: str) -> dict:
    """Record the choice, or clear it by naming nothing.

    A game's default is the one a member naming only the game resolves to, so this is the
    difference between a collection playing the VR build and the desktop one.
    """
    game = game_lens.game_for_writing(game_id)
    if table_id:
        filename_or_refuse(game, table_id)
        if table_entries(load_game_meta(game))[table_id].get("hidden") is True:
            raise service_errors.RefusedError(t("error.games.hidden_default"),
                                              details={"table": table_id})
    try:
        MetaConfig(str(info_files.info_path_of(game))).set_default_table(table_id)
    except ValueError as exc:
        raise service_errors.RefusedError(why(exc),
                                          details={"table": table_id}) from exc
    game = reread_game(game)
    return {"tables": _game_tables(game)}


def set_overrides(game_id: str, table_id: str, changes: dict) -> dict:
    """What the user says about one launchable file.

    Written onto the table's own entry, never onto the folder - a folder value is read as
    a fallback for a 2.x library but is not where anything lands now.
    """
    from common.games import game_service

    game = game_lens.game_for_writing(game_id)
    unknown = set(changes) - set(TABLE_OVERRIDES)
    if unknown:
        raise service_errors.RefusedError(
            t("error.games.game_keys", keys=", ".join(sorted(unknown))))

    game_dir = Path(game.full_path_game)
    if table_id not in table_entries(load_game_meta(game)):
        raise service_errors.NotFoundError(
            t("error.games.table_id_unknown", table_id=table_id, game=game_title(game)))
    try:
        for name, value in changes.items():
            game_service.update_table_vpinfe_setting(game_dir, table_id, name, value)
    except (OSError, LookupError, ValueError) as exc:
        raise service_errors.BlockedError(why(exc)) from exc
    fresh = game_lens.game_or_refuse(game_id)
    return table_lens.table_overrides(
        table_entries(load_game_meta(fresh)).get(table_id) or {},
        vpinfe_section(fresh.meta_config))


def _settled(game_id: str) -> None:
    """A table has been added: its order is brought to what the backglass beside it places."""
    from common.games import backglass_order

    backglass_order.settle(game_id)


def import_file(game_id: str, path: str) -> dict:
    """Import a game file from a path this install may read.

    One call rather than pointing at it and then importing it: an import interrupted
    between those two leaves a library of entries referencing a folder that was only ever
    meant to be read from. A copy, not a move.
    """
    from common.games import game_service

    game = game_lens.game_for_writing(game_id)
    source = Path(path)
    if not source.is_file():
        raise service_errors.RefusedError(t("error.games.not_file"),
                                          details={"path": str(path)})
    if apps.app_for(source.name) is None:
        raise service_errors.RefusedError(
            t("error.games.no_player", name=(source.name)))

    game_dir = Path(game.full_path_game or "")
    table_id = new_id()
    try:
        table_id = game_service.add_table_file(game_dir, source, table_id)["table_id"]
    except FileExistsError as exc:
        raise service_errors.BlockedError(t("error.games.file_exists"),
                                          details={"filename": Path(exc.filename).name}) from exc
    except (OSError, ValueError) as exc:
        raise service_errors.BlockedError(why(exc)) from exc

    game = reread_game(game)
    _settled(game_id)
    return row_or_refuse(game, table_id)


def add_keyed(game_id: str, app_id: str, key: str) -> dict:
    """Record a ROM, a Pinball FX table, or anything else its program finds by name.

    Nothing is scanned into existence here, and nothing can be: a folder scan finds files,
    and this is the one kind of entry that has none.

    We never resolve the key. The whole reason a key is not a path is that the program
    already looks it up, and better - pointing at `roms/mm.zip` would break the moment
    somebody reorganized their rompath.
    """
    game = game_lens.game_for_writing(game_id)
    app_id = str(app_id or "").strip()
    app = apps.get(app_id)
    if app is None:
        raise service_errors.RefusedError(
            t("error.games.unknown_app", app_id=(app_id),
              apps=", ".join(one.id for one in apps.all_apps())))
    if not app.claim.accepts_keys:
        raise service_errors.RefusedError(
            t("error.games.needs_file", app_name=(apps.app_name(app_id))))
    key = str(key or "").strip()
    if not key:
        raise service_errors.RefusedError(t("error.games.id_needed"))

    table_id = new_id()
    if not MetaConfig(str(info_files.info_path_of(game))).add_keyed_table(app_id, key, table_id):
        raise service_errors.BlockedError(t("error.games.duplicate"),
                                          details={"app": app_id, "key": key})
    game = reread_game(game)
    return row_or_refuse(game, table_id)


def add_referenced(game_id: str, path: str) -> dict:
    """A game file that lives somewhere else - on a read-only share, or one file two games
    both point at.

    Checked against the disk now, because a path that is wrong the moment it is typed is a
    typo and should be refused rather than kept as a broken record. A path that stops
    resolving later is a different thing: the location is unreachable, the entry stands,
    and nothing here is lost.
    """
    game = game_lens.game_for_writing(game_id)
    game_dir = str(game.full_path_game or "")
    target = os.path.expanduser(str(path or "").strip())
    if not target:
        raise service_errors.RefusedError(t("error.games.location_needed"))
    if not os.path.isabs(target):
        raise service_errors.RefusedError(t("error.games.path_needed"))
    if not os.path.isfile(target):
        raise service_errors.NotFoundError(t("error.games.no_file"),
                                           details={"path": target})
    if apps.app_for(os.path.basename(target)) is None:
        raise service_errors.RefusedError(
            t("error.games.no_player", name=(os.path.basename(target))))
    if path_state.canonical(os.path.dirname(target)) == path_state.canonical(game_dir):
        raise service_errors.RefusedError(t("error.games.already_inside"))

    # Relative where it survives the library moving, absolute where it would not.
    stored = library_folders.portable_reference(game_dir, target)
    table_id = new_id()
    if not MetaConfig(str(info_files.info_path_of(game))).add_referenced_table(stored, table_id):
        raise service_errors.BlockedError(t("error.games.already_linked"),
                                          details={"path": stored})
    game = reread_game(game)
    _settled(game_id)
    return row_or_refuse(game, table_id)


def contain(game_id: str, table_id: str) -> dict:
    """Bring the referenced file into the game folder, so the entry stops depending on
    somewhere else being there.

    The id survives, which is the point - a collection that named this table and the play
    record on it both stay pointed at it. The file is copied and not moved: what it
    references may be read-only, shared with another game, or somebody else's.
    """
    game = game_lens.game_for_writing(game_id)
    entry = table_entries(load_game_meta(game)).get(table_id)
    if not isinstance(entry, dict) or not tables.entry_reference(entry):
        raise service_errors.NotFoundError(t("error.games.reference_not_found"),
                                           details={"table": table_id})

    game_dir = Path(game.full_path_game or "")
    source = Path(tables.resolved_reference(str(game_dir), tables.entry_reference(entry)))
    if not source.is_file():
        raise service_errors.BlockedError(t("error.games.file_unreachable"),
                                          details={"path": str(source)})
    landing = game_dir / source.name
    if landing.exists():
        raise service_errors.BlockedError(t("error.games.file_exists"),
                                          details={"filename": source.name})

    try:
        shutil.copy2(source, landing)
    except OSError as exc:
        raise service_errors.BlockedError(why(exc)) from exc

    if not MetaConfig(str(info_files.info_path_of(game))).contain_referenced_table(table_id,
                                                                         source.name):
        # The copy landed and the record did not, which is the one state worth undoing: a
        # file in the folder with nothing describing it becomes a second table on the next
        # scan, beside the reference that is still there.
        landing.unlink(missing_ok=True)
        raise service_errors.BlockedError(t("error.games.no_info_entry"),
                                          details={"table": table_id})
    game = reread_game(game)
    _settled(game_id)
    return row_or_refuse(game, table_id)


def forget(game_id: str, table_id: str) -> dict:
    """Drop the record of a table whose file is no longer there.

    No file is deleted, because there is none - what goes is the entry describing it.
    Refused while the .vpx is on disk: that entry describes something the user owns, and
    the next refresh would mint it again anyway.
    """
    game = game_lens.game_for_writing(game_id)
    entry = table_entries(load_game_meta(game)).get(table_id)
    if not isinstance(entry, dict):
        raise service_errors.NotFoundError(
            t("error.games.table_not_found"),
            details={"game": game.game_dir_name, "table": table_id})
    meta = MetaConfig(str(info_files.info_path_of(game)))
    if tables.entry_key(entry) or tables.entry_reference(entry):
        # Nothing in this folder will mint one of these again, which is exactly why
        # forgetting it is safe where forgetting a table that is there is not. A reference
        # forgotten leaves the file it pointed at alone.
        meta.forget_keyed_table(table_id)
    elif not entry.get(ABSENT_SINCE_KEY):
        raise service_errors.BlockedError(
            t("error.games.file_present"),
            details={"table": table_id, "filename": entry.get("filename", "")})
    else:
        meta.forget_table(table_id)
    order_record.drop([table_id])
    reread_game(game)
    return {"forgotten": table_id}
