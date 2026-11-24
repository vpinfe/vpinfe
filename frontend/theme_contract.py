"""What a theme was written against, and how to serve it.

A theme declares the oldest VPinFE it runs on - `min_vpinfe` in its manifest.json - and
the contract follows from that; absent means the oldest, which is every theme written
before this existed. The payload is always built in the current shape and projected
backwards, never the reverse - the newest shape is the one that is true.

Adding a field, a media kind or a method does not bump the contract: those are visible
at every level and a theme feature-detects them. Only removing or reshaping something a
theme already reads does. That is what keeps a bump rare.
"""

from __future__ import annotations

import logging

from common.deprecations import announce
from common.games import high_scores
from common.games.game_metadata import DETECTION_KEYS, default_table
from common.online.theme_level import (  # noqa: F401 - the names a theme-facing caller reads here
    CURRENT_CONTRACT,
    MIN_VERSION_KEY,
    OLDEST_CONTRACT,
    declared_contract,
)

logger = logging.getLogger("vpinfe.frontend.theme_contract")

# What contract 1 calls each table field. The .info renamed these; a theme written
# against 2.x still reads the old spelling, so the projection restores it.
_LEGACY_TABLE_KEYS = {
    "file_hash": "filehash",
    "vbs_hash": "vbsHash",
    "release_date": "releaseDate",
    "save_date": "saveDate",
    "save_rev": "saveRev",
    "detect_nfozzy": "detectnfozzy",
    "detect_fleep": "detectfleep",
    "detect_ssf": "detectssf",
    "detect_lut": "detectlut",
    "detect_scorbit": "detectscorebit",
    "detect_fastflips": "detectfastflips",
    "detect_flex": "detectflex",
    "detect_pinmame": "detectpinmame",
}

# What contract 1 calls each key in the section VPinFE owns. The .info moved these to
# snake_case; the projection restores the spellings a 2.x theme could be reading, the
# same way _LEGACY_TABLE_KEYS does for VPXFile. Keys 3.0 added have no 2.x name and are
# passed through - the gate allows additions, never removals.
_LEGACY_VPINFE_KEYS = {
    "delete_nvram_on_close": "deletedNVRamOnClose",
    "alt_title": "alttitle",
    "alt_vpsid": "altvpsid",
}

def _derived_launcher_keys(row: dict) -> dict:
    """`altlauncher` and `pluginprofile`, computed rather than stored.

    Both were `.info` keys until launchers took over, and the contract allows additions
    but never removals - so they are still published, from the resolver that decides what
    actually runs. That makes them *more* correct than the stored values were: a stored
    path could name a binary since moved, or a launcher since switched off.

    Empty where the table is on the default launcher, which is what "alt" always meant.
    Answering for every table would turn a field about the exception into one that says
    the same thing everywhere.
    """
    try:
        from common.games import launchers
    except Exception:  # a theme payload must not fail over a launcher lookup
        return {}

    if not row.get("launcher") or not row.get("launcher_set_here"):
        return {"altlauncher": "", "pluginprofile": ""}
    found = launchers.get_launcher_store().get(str(row.get("launcher")))
    if found is None:
        return {"altlauncher": "", "pluginprofile": ""}
    return {"altlauncher": str(found.value("bin_path") or ""),
            "pluginprofile": str(found.value("ini_path") or "")}


# What contract 1 calls each top-level row key. These are served identically at both
# contracts until now, so the projection never had to touch anything outside meta.
_LEGACY_ROW_KEYS = {
    "PlayfieldImagePath": "TableImagePath",
    "PlayfieldVideoPath": "TableVideoPath",
    "fullPathGame": "fullPathTable",
    "gameDirName": "tableDirName",
}

# 1  the shape 2.x themes read: meta.VPXFile, and Rom and Authors on meta.Info
# 2  the .info's own shape: meta.tables, meta.vpinfe, and neither of those on Info


def project(row: dict, level: int) -> dict:
    """One table's payload as the given contract expects it."""
    if level >= CURRENT_CONTRACT:
        return row
    announce("theme-payload-keys", f"contract {level}")
    return _to_contract_1(row)


def _to_bool(value: object) -> bool:
    """A detect flag as a real boolean. A JSON "false" is truthy to anything that reads
    it without care, and the parser has handed back strings before now."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes", "on")
    return value == 1


def _legacy_table(meta: dict, row: dict) -> dict:
    """The game's default table as VPXFile, plus the addon flags that rode with it."""
    name, entry = default_table(meta)
    vpx = {_LEGACY_TABLE_KEYS.get(key, key): value for key, value in dict(entry).items()}
    vpx["filename"] = name
    for key in DETECTION_KEYS:
        vpx[_LEGACY_TABLE_KEYS.get(key, key)] = _to_bool(entry.get(key, False))
    for flag in ("altSoundExists", "altColorExists", "pupPackExists"):
        vpx[flag] = bool(row.get(flag))
    return vpx


def _to_contract_1(row: dict) -> dict:
    """Contract 1 never sees the sections that replaced what it reads.

    Serving both shapes would let a theme work by accident against fields it never
    declared, which is the failure this exists to prevent.
    """
    meta = dict(row.get("meta") or {})
    vpx = _legacy_table(meta, row)
    meta["VPXFile"] = vpx

    info = dict(meta.get("Info") or {})
    info.setdefault("Rom", vpx.get("rom", ""))
    info.setdefault("Authors", vpx.get("authors", []))
    meta["Info"] = info

    if isinstance(meta.get("User"), dict):
        meta["User"] = high_scores.to_2x(meta["User"], high_scores.rom_of(vpx))

    meta["VPinFE"] = {_LEGACY_VPINFE_KEYS.get(key, key): value
                      for key, value in dict(meta.get("vpinfe") or {}).items()}
    meta["VPinFE"].update(_derived_launcher_keys(row))
    for section in ("tables", "vpinfe", "assets"):
        meta.pop(section, None)

    projected = dict(row)
    projected["meta"] = meta
    # Top-level row keys, not meta - the projection did not reach these until the
    # vocabulary rename moved them, because nothing above meta had ever changed.
    for new, old in _LEGACY_ROW_KEYS.items():
        if new in projected:
            projected[old] = projected.pop(new)
    return projected
