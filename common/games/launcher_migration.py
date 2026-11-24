"""Turning what an install already had into launchers.

Two things become launchers on the way to 3.0: the seven Visual Pinball settings that sat
flat in `general`, and the plugin profiles, which were a full copy of `VPinballX.ini`
saved under a name and opted into per table. Both were one idea written twice - a way of
running Visual Pinball differently - so a profile is not carried alongside launchers, it
is one.

The first half runs inside the conversion of a 2.x ini and consumes the keys it reads, so
a converted file never converts again and a launcher somebody deletes does not come back.
The per-table half runs where the library is read, because a table's assignment needs a
table; it consumes what it reads too, so there is nothing to mark.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from common.config import config_schema
from common.config.config_access import cfg_bool, cfg_get
from common.config.config_store import ConfigStore
from common.config.paths import PLUGIN_PROFILES_DIR
from common.games import game_metadata, launchers, tables
from common.games.game import Game

logger = logging.getLogger("vpinfe.common.games.launcher_migration")

# What the shipped launcher is called before anybody renames it. Its app's name rather
# than something invented: on an install with one launcher, "Visual Pinball X" is what a
# person would call the thing that runs their tables.
SHIPPED_NAME = "Visual Pinball X"

# The field a launcher made from a profile keeps the profile's file in.
PROFILE_FILE = "ini_path"


def seed(store: Any) -> None:
    """Give a converted install its launchers: the shipped one, written first so it is the
    default, then one per plugin profile. An install that had none of the seven set and no
    profiles keeps the list's default."""
    config = store.config
    old = read_old_keys(config)
    shipped = launchers.Launcher(
        launcher_id=launchers.mint_launcher_id(), app="vpx", display_name=SHIPPED_NAME,
        settings=_kept(old))
    found = [shipped, *_from_profiles(shipped, [SHIPPED_NAME])]
    if len(found) == 1 and not any(shipped.settings.values()):
        return
    store.set_list(launchers.LAUNCHERS_KEY, [one.as_item() for one in found])
    for key in _CONSUMED:
        for section, name in _held(config, key):
            config.remove_option(section, name)
    logger.info("Seeded %d launcher(s) from the existing configuration", len(found))


config_schema.register_seed("launchers", seed)


# Where each launcher field was written before it was one, oldest spelling included.
# Spelled out rather than resolved through the schema, because the schema no longer
# declares them - which is the point of this pass - and a reader that asked it would find
# nothing and seed a launcher with no binary.
_OLD_KEYS: tuple[tuple[str, str, str, bool], ...] = (
    ("bin_path", "general", "vpx_bin_path", False),
    ("ini_path", "general", "vpx_ini_path", False),
    ("launch_env", "general", "vpx_launch_env", False),
    ("log_delete_on_start", "general", "vpx_log_delete_on_start", True),
    ("ini_override", "general", "global_ini_override", False),
    ("table_ini_override_enabled", "general", "global_game_ini_override_enabled", True),
    ("table_ini_override_mask", "general", "global_game_ini_override_mask", False),
)

# What 2.x called the section and the keys. A file the config store has already migrated
# holds the first spelling; one written by 2.x and not yet loaded holds the second.
_OLD_SPELLINGS = {
    "vpx_bin_path": "vpxbinpath",
    "vpx_ini_path": "vpxinipath",
    "vpx_launch_env": "vpxlaunchenv",
    "vpx_log_delete_on_start": "vpxlogdeleteonstart",
    "global_ini_override": "globalinioverride",
    "global_game_ini_override_enabled": "globaltableinioverrideenabled",
    "global_game_ini_override_mask": "globaltableinioverridemask",
}


def _held(config: Any, key: str) -> list[tuple[str, str]]:
    """Where the file holds this key. The conversion moves a key it does not declare to
    `general` under its old spelling, so all four places are looked in."""
    return [(section, name) for section in ("general", "Settings")
            for name in (key, _OLD_SPELLINGS[key])
            if config.has_section(section) and config.has_option(section, name)]


# The two override keys stay for `retire_table_pattern`, which needs the files.
_CONSUMED = tuple(key for key in _OLD_SPELLINGS
                  if not key.startswith("global_game_ini_override_"))


def _kept(old: dict[str, object]) -> dict[str, object]:
    """What a launcher holds of the seven: the override was the file Visual Pinball
    started with, so it is the Settings File where one was set."""
    kept = {key: old.get(key, "") for key in ("bin_path", "launch_env",
                                               "log_delete_on_start")}
    kept["ini_path"] = old.get("ini_override") or old.get("ini_path") or ""
    return kept


def read_old_keys(config: ConfigStore) -> dict[str, object]:
    """The seven values, under whichever spelling the file happens to hold."""
    found: dict[str, object] = {}
    for field, _section, key, is_bool in _OLD_KEYS:
        places = _held(getattr(config, "config", config), key)
        if is_bool:
            found[field] = any(cfg_bool(config, section, name, False) for section, name in places)
        else:
            found[field] = next((value for section, name in places
                                 if (value := cfg_get(config, section, name, "").strip())), "")
    return found


def _from_profiles(shipped: launchers.Launcher,
                   taken: list[str]) -> list[launchers.Launcher]:
    """One launcher per plugin profile, each a copy of the shipped one pointed at its ini.

    A copy rather than a bare launcher because that is what a profile was: the same
    Visual Pinball, the same binary, one file different. Anything else would silently
    drop the environment and the overrides a person had set.

    `owns_ini` is True - VPinFE wrote these files, so removing the launcher may offer to
    delete them. The shipped launcher's ini is Visual Pinball's own and is never offered.
    """
    if not PLUGIN_PROFILES_DIR.exists():
        return []

    found = []
    for path in sorted(PLUGIN_PROFILES_DIR.iterdir(), key=lambda p: p.name.lower()):
        if not path.is_file() or path.suffix.lower() != ".ini":
            continue
        taken.append(launchers.free_name(path.stem, taken))
        found.append(launchers.replace(
            shipped,
            launcher_id=launchers.mint_launcher_id(),
            display_name=taken[-1],
            owns_ini=True,
            settings={**shipped.settings, PROFILE_FILE: str(path)},
        ))
    return found


def migrate_assignments(store: launchers.LauncherStore,
                        games: Iterable[Game]) -> dict[str, int]:
    """Turn each table's own override into a launcher and an assignment.

    `alt_launcher` carried a raw binary path and `plugin_profile` a named ini, and they
    were two partial ways of saying the same thing: this table launches differently. A
    launcher id says it once.

    Distinct binaries are de-duplicated by path, against the launchers already held as
    well as within one pass, so a library where forty tables name one alternative build
    gets one launcher rather than forty, and a later pass reuses it. A profile already became a
    launcher when the install was seeded, so a table naming one is matched to it rather
    than making a second.

    The assignment lands on every table in the game, because the override was written per
    game and there is no record of which of its tables it was meant for. Splitting them
    afterwards is a thing a person can do; guessing here is not.

    The two keys are **consumed**: read here and then written out of the `.info`. They are
    published to contract 1 themes, and a dead key in the theme API is a lie to
    third-party code - so contract 1 computes them from the resolver instead, and the
    stored ones go. Only the games that had one are rewritten.

    This is why the schema migration does not drop them: a table has no id until the
    minting pass runs over the migrated library, so dropping them earlier would destroy
    the values before anything could record which table they belonged to.
    """
    held = store.launchers()
    mappings = dict(store.mappings())
    shipped = held[0] if held else None
    by_path = {path: one.launcher_id for one in held
               if (path := str(one.settings.get("bin_path") or "").strip())}
    counts = {"launchers": 0, "tables": 0, "games": 0}

    for game in games or []:
        meta = getattr(game, "meta_config", None)
        if not isinstance(meta, dict):
            continue
        vpinfe = game_metadata.vpinfe_section(meta)
        alt = str(vpinfe.get("alt_launcher", "") or "").strip()
        profile = str(vpinfe.get("plugin_profile", "") or "").strip()
        if not alt and not profile:
            continue

        wanted = ""
        if alt:
            wanted = by_path.get(alt, "")
            if not wanted:
                made = _for_binary(alt, shipped, [one.display_name for one in held])
                held.append(made)
                by_path[alt] = wanted = made.launcher_id
                counts["launchers"] += 1
        elif profile:
            wanted = _profile_launcher(profile, held)

        if not wanted:
            logger.warning("No launcher for plugin profile %r; leaving %s on the default",
                           profile, game.game_dir_name)
            continue

        for table_id in tables.table_entries(meta):
            mappings[str(table_id)] = wanted
            counts["tables"] += 1
        counts["games"] += 1
        _consume(game, vpinfe)

    if counts["games"]:
        store.save(held, mappings)
        logger.info("Moved %d game(s) onto launchers: %d table(s), %d new launcher(s)",
                    counts["games"], counts["tables"], counts["launchers"])
    return counts


def _consume(game: Game, vpinfe: dict) -> None:
    """Take the two keys out of this game's `.info`, now that a launcher holds them.

    Written per game rather than in one sweep at the end, so a failure part-way through
    leaves the files it already handled correct rather than all of them half-converted.
    A game that cannot be written is logged and left: its keys stay, which reads as
    not-yet-migrated rather than as data loss.
    """
    for key in ("alt_launcher", "plugin_profile"):
        vpinfe.pop(key, None)
    try:
        from common.games.game_metadata import persist_game_meta

        persist_game_meta(game, game.meta_config or {})
    except Exception:
        logger.exception("Could not rewrite %s without its launcher keys",
                         game.game_dir_name)


def _for_binary(path: str, shipped: launchers.Launcher | None,
                taken: list[str]) -> launchers.Launcher:
    """A launcher for a binary a table named, copied from the shipped one.

    A copy because `alt_launcher` only ever replaced the program: everything else about
    the launch - the ini, the environment, the overrides - came from the install, and a
    bare launcher would silently drop all of it.
    """
    base = shipped or launchers.Launcher(launcher_id="", app="vpx")
    return launchers.replace(
        base,
        launcher_id=launchers.mint_launcher_id(),
        display_name=launchers.free_name(Path(path).stem or path, taken),
        owns_ini=False,
        settings={**base.settings, "bin_path": path},
    )


def _profile_launcher(name: str, held: Iterable[launchers.Launcher]) -> str:
    """The launcher the seeding pass made from this profile, by the profile's file."""
    wanted = str(PLUGIN_PROFILES_DIR / f"{name.strip()}.ini").casefold()
    found = next((one for one in held
                  if str(one.settings.get(PROFILE_FILE) or "").casefold() == wanted), None)
    return found.launcher_id if found is not None else ""


_PATTERN_KEYS = ("global_game_ini_override_enabled", "global_game_ini_override_mask")


def retire_table_pattern(config: ConfigStore, games: Iterable[Game]) -> int:
    """Where 2.x started each table with `{stem}.{mask}.ini`, make that file the
    table's `{stem}.ini`, which Visual Pinball reads unaided. Returns how many moved.

    A table that already has a `{stem}.ini` keeps both, and the log names it: which of
    the two somebody meant is not something to guess by overwriting one. The two keys
    are removed once the files have been looked at.
    """
    parser = config.config
    held = [place for key in _PATTERN_KEYS for place in _held(parser, key)]
    if not held:
        return 0
    old = read_old_keys(config)
    mask = str(old.get("table_ini_override_mask") or "").strip()
    moved = 0
    if old.get("table_ini_override_enabled") and mask:
        for game in games or []:
            game_dir = str(getattr(game, "full_path_game", "") or "")
            meta = getattr(game, "meta_config", None)
            for entry in tables.table_entries(meta if isinstance(meta, dict) else {}).values():
                table = tables.entry_file(game_dir, entry)
                if table and _to_table_ini(Path(table), mask):
                    moved += 1
    for section, name in held:
        parser.remove_option(section, name)
    config.save()
    if moved:
        logger.info("Renamed %d per-table pattern file(s) to the table's own", moved)
    return moved


def _to_table_ini(table: Path, mask: str) -> bool:
    masked = table.with_name(f"{table.stem}.{mask}.ini")
    if not masked.is_file():
        return False
    own = table.with_suffix(".ini")
    if own.exists():
        logger.warning("Left %s beside %s, which already has a settings file of its own",
                       masked, own.name)
        return False
    try:
        masked.rename(own)
    except OSError:
        logger.exception("Could not rename %s to %s", masked, own.name)
        return False
    return True
