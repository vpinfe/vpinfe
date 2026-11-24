"""What belongs in a game export - one answer for every transport.

The VPXZ download and the mobile Web Send are the same operation with different
plumbing, and they answer the same way, but for a table's settings file: the download
keeps it and a send leaves it unless asked. The default is a standalone bundle for one
table - export a game, not a folder - which is also what makes a multi-.vpx folder
come out right. `everything=True` ships the whole folder as the explicit choice.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

from common.games import info_codec, info_files, table_files
from common.games.asset_registry import (
    is_readme,  # noqa: F401  (one matcher, import and export)
)
from common.games.asset_resolver import VPX_ASSET_KINDS, resolve_for_table
from common.games.game_metadata import VPINFE_SECTION, vpinfe_section
from common.games.info_file import ASSETS_KEY
from common.games.info_migration import migrate, needs_migration
from common.games.tables import (
    DEFAULT_TABLE_KEY,
    TABLES_KEY,
    default_entry,
    entry_filename,
    recorded_default,
    table_entries,
    table_names,
)

# Directories a running game reads, whole. Media is browsing artwork, not part
# of playing the game, so medias/ is deliberately absent.
#
# VPX's list, and the one part of a bundle still named for one engine. The file-level
# question next to it is already app-agnostic - `resolve_for_table` takes its `kinds`
# as a parameter, so its rule (a dedicated file wins, a folder-named one is the shared
# fallback) holds for whatever the launcher is. `VPinFE.altlauncher` already lets a game
# declare a different one. When a second app arrives, this tuple is what moves beside
# `VPX_ASSET_KINDS` into that app's description rather than being extended in place.
BUNDLE_DIRS = ("pinmame", "music", "serum", "vni", "altsound", "pupvideos")


def choose_table(game_dir: Path, table: str | None = None) -> str | None:
    """The bundle's table: the caller's pick, else the game's default."""
    try:
        listing = [entry.name for entry in game_dir.iterdir() if entry.is_file()]
    except OSError:
        return None
    names = table_names(listing)
    if table:
        return table if table in names else None

    meta: dict = {}
    try:
        meta = info_files.load(game_dir)
    except (OSError, ValueError):
        pass
    entries = table_entries(meta)
    recorded = recorded_default(vpinfe_section(meta))
    chosen = entry_filename(default_entry(entries, recorded, listing)[1])
    return chosen if chosen in names else (names[0] if names else None)


def prune_info(info_text: str, bundled_arcnames: set[str]) -> str:
    """The .info that ships: file records and tables only for what is actually in the bundle.

    The manifest should describe the archive, in both modes. A table whose file is not
    in the bundle goes, and the default points at one that is; everything else in the
    file - authors, VPS identity, user data - passes through untouched. A table's own file
    describes one table and is shipped as it is.

    Both keys are folder-relative paths, so this is a direct comparison. Matching on
    basenames cannot tell medias/wheel.png from a wheel.png at the folder root.
    """
    try:
        data = json.loads(info_text)
    except ValueError:
        return info_text
    section = data.get("vpinfe") if isinstance(data, dict) else None
    if isinstance(section, dict) and table_files.TABLE_SECTION in section:
        return info_text
    schema_3 = info_codec.is_schema_3(data)
    if schema_3:
        record = migrate(data) if needs_migration(data) else info_codec.decode(data)
    else:
        record = data
    assets = record.get(ASSETS_KEY)
    if isinstance(assets, dict):
        record[ASSETS_KEY] = {
            path: entry for path, entry in assets.items() if path in bundled_arcnames
        }
    _keep_shipped_tables(record, bundled_arcnames)
    return json.dumps(info_codec.encode(record) if schema_3 else record, indent=2)


def _keep_shipped_tables(record: dict, bundled_arcnames: set[str]) -> None:
    """Drop the tables whose files are not in the bundle, unless none of them is."""
    entries = table_entries(record)
    kept = {key: entry for key, entry in entries.items()
            if isinstance(entry, dict) and entry_filename(entry) in bundled_arcnames}
    if not kept or len(kept) == len(entries):
        return
    record[TABLES_KEY] = kept
    section = record.get(VPINFE_SECTION)
    if not isinstance(section, dict):
        return
    ids = {str(entry.get("id") or key) for key, entry in kept.items()}
    if section.get(DEFAULT_TABLE_KEY) not in ids and section.get(DEFAULT_TABLE_KEY):
        section[DEFAULT_TABLE_KEY] = sorted(ids)[0]


def mobile_settings_file(game_dir: Path, word: str, table: str | None = None) -> Path | None:
    """`<table>.<word>.ini` where the folder has one, matched the way VPX matches names."""
    chosen = choose_table(game_dir, table)
    if not word or not chosen:
        return None
    wanted = f"{Path(chosen).stem}.{word}.ini".lower()
    try:
        return next((entry for entry in sorted(game_dir.iterdir())
                     if entry.is_file() and entry.name.lower() == wanted), None)
    except OSError:
        return None


# A VPXZ is opened by Visual Pinball's standalone build, which has no B2S Pro Server to
# read a `.b2spro` with.
_BUNDLED_KINDS = tuple(kind for kind in VPX_ASSET_KINDS if kind.key != "b2spro")


def bundle_paths(game_dir: Path, *, everything: bool = False, table: str | None = None,
                 settings: bool = True, mobile_word: str = "") -> Iterator[tuple[Path, str]]:
    """(absolute path, folder-relative arcname) for everything the export holds.

    The .info is included here by name; writers call prune_info on its content
    rather than copying the file raw.

    The settings file VPX would read goes unless `settings` is False. A mobile copy named
    by `mobile_word` goes in its place as `<table>.ini` whichever way `settings` is set.
    Neither applies to `everything`, which is the folder as it is.
    """
    if everything:
        for path in sorted(game_dir.rglob("*")):
            if path.is_file():
                # Zip entry names are forward-slashed by the format's own spec, so
                # `str` on a Windows path would write an archive other tools read as
                # one long filename with backslashes in it.
                yield path, path.relative_to(game_dir).as_posix()
        return

    chosen = choose_table(game_dir, table)
    folder_stem = game_dir.name.lower()

    try:
        entries = sorted(game_dir.iterdir())
    except OSError:
        return

    # What VPX would actually open for this table, asked of the resolver rather than
    # matched by hand. Stem-matching accepts the table's own companion *and* the
    # folder-named fallback, where the resolver takes the dedicated one and stops - so a
    # table with its own .directb2s would ship the shadowed one too, and the far side
    # could never load it.
    resolved = resolve_for_table(chosen or "", game_dir.name,
                                 [entry.name for entry in entries if entry.is_file()],
                                 _BUNDLED_KINDS)
    resolved_companions = {str(picked["file"]).lower()
                           for picked in resolved.values() if picked.get("file")}
    mobile = mobile_settings_file(game_dir, mobile_word, table)
    settings_file = str((resolved.get("ini") or {}).get("file") or "").lower()
    for entry in entries:
        name = entry.name
        if entry.is_dir():
            if name.lower() in BUNDLE_DIRS:
                for path in sorted(entry.rglob("*")):
                    if path.is_file():
                        yield path, path.relative_to(game_dir).as_posix()
            continue

        lower = name.lower()
        if chosen and name == chosen:
            yield entry, name
        elif lower == info_files.info_name(folder_stem):
            yield entry, name
        elif (chosen and not table_files.shares_game_file(chosen, game_dir.name)
              and lower == table_files.own_name(chosen).lower()):
            yield entry, name
        elif is_readme(name):
            yield entry, name
        elif lower in resolved_companions:
            if lower == settings_file and (mobile or not settings):
                continue
            yield entry, name
    if mobile and chosen:
        yield mobile, f"{Path(chosen).stem}.ini"
