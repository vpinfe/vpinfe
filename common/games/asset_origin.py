"""Who put a file in a game folder, as far as anyone recorded.

Not the same question as tier. Tier says why a file is the one being used and is derived
from its name every run; origin can only be known because something wrote it down. Art
hand-placed at the fixed name reads as tier `default` exactly like a download.

The ledger is the only source cheap enough per request. Proving a file is the one
vpinmediadb publishes means hashing it, which belongs in a pass that can take its time.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from common.games import info_files

logger = logging.getLogger("vpinfe.common.games.asset_origin")

ASSETS_KEY = "assets"
UNKNOWN = "unknown"
RECORDED = "capture"
# A picture drawn from a backglass file, by the kind of file it was drawn from, which is
# the one a redraw draws it from again.
BACKGLASS_FILE = "backglass_file"
B2SPRO_FILE = "b2spro_file"


def _key(game_dir: Path, path: Path) -> str:
    """The ledger's own key shape: folder-relative, forward slashes."""
    try:
        return os.path.relpath(str(path), str(game_dir)).replace(os.sep, "/")
    except ValueError:
        return path.name


def sources(game_dir: str | Path) -> dict[str, dict]:
    """Every recorded path in this folder and the `source` block against it. Read
    from the file as it is: MetaConfig migrates and can rewrite, and this wants one
    section."""
    try:
        data = info_files.load(game_dir)
    except Exception:
        return {}
    entries = data.get(ASSETS_KEY)
    if not isinstance(entries, dict):
        return {}
    out = {}
    for key, entry in entries.items():
        source = (entry or {}).get("source") if isinstance(entry, dict) else None
        if isinstance(source, dict):
            out[str(key)] = source
    return out


def ledger(game_dir: str | Path) -> dict[str, str]:
    """Every recorded path, and the host that placed it. Paths whose entry names no
    host are left out, so a file bound to a record by hand and placed by nobody does
    not read as having an origin."""
    return {key: str(source.get("host", "") or "").strip()
            for key, source in sources(game_dir).items()
            if str(source.get("host", "") or "").strip()}


def origin_of(recorded: dict[str, str], game_dir: str | Path, path: Path | None) -> str:
    """Who placed this file, or "unknown" - a real answer, not a gap. A copied folder,
    a regenerated .info or art fetched with another tool all leave no record."""
    if path is None:
        return ""
    return recorded.get(_key(Path(game_dir), path), UNKNOWN)


def match_of(recorded: dict[str, dict], game_dir: str | Path,
             path: Path | None) -> str:
    """Which VPS record somebody said this file is, or "" for nobody has said. Absent
    rather than "unmatched" - only a matcher can claim a look came back empty."""
    if path is None:
        return ""
    source = recorded.get(_key(Path(game_dir), path)) or {}
    return str(source.get("vps_file_id", "") or "")


def basis_of(recorded: dict[str, dict], game_dir: str | Path,
             path: Path | None) -> str:
    """Who said this file is the record it is matched to (`confirmed_by`), or "" where it
    is matched to nothing."""
    if path is None or not match_of(recorded, game_dir, path):
        return ""
    return str((recorded.get(_key(Path(game_dir), path)) or {}).get("confirmed_by", "") or "")


def path_of(game_dir: str | Path, path: Path | None) -> str:
    """The ledger's key for a file, which is how anything addresses one."""
    return "" if path is None else _key(Path(game_dir), path)
