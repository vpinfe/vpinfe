"""What the user set on a theme, kept where a theme update cannot reach it.

Updating a theme deletes the installed package, so an option written inside it is an
option the next update silently resets. These are the `theme_options` map of `vpinfe.json`
instead, one entry per theme, and survive an update, a reinstall and a delete.

Keyed by folder name rather than by the registry key, because only the folder name is
universal: a local or side-loaded theme has no registry entry, and `[Settings] theme`
stores the folder. For a registry theme the two strings are already the same, since the
installer renames the extracted folder to the key.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from common.i18n import t
from common.settings import config_service

logger = logging.getLogger("vpinfe.common.settings.theme_options")


def load(folder: str) -> dict[str, Any]:
    """The values the user set for this theme, or {} when they have set none."""
    return config_service.theme_options().get(str(folder or "").strip(), {})


def _read(path: Path) -> dict[str, Any]:
    try:
        with path.open(encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        logger.warning("Ignoring unreadable theme schema at %s", path)
        return {}


_warned_schema: set[str] = set()


def schema_path(theme_dir: Path, contract: int) -> Path:
    """The file this theme declares its options in: options.json at contract 2 and above,
    theme.json below it.

    A contract 2 theme that ships only theme.json is not read from it - the options it
    lists simply do not appear - and the log says so once, so the cause is not a silent
    absence.
    """
    if contract < 2:
        return theme_dir / "theme.json"
    path = theme_dir / "options.json"
    legacy = theme_dir / "theme.json"
    key = str(theme_dir)
    if not path.is_file() and key not in _warned_schema and legacy.is_file() \
            and isinstance(_read(legacy).get("options"), list):
        _warned_schema.add(key)
        logger.warning("%s lists its options in theme.json, but a theme on contract 2 reads "
                       "them from options.json. Move them there - until then the theme "
                       "has no options.", theme_dir.name)
    return path


def values_in_package(theme_dir: Path) -> dict[str, Any]:
    """The values a pre-3.0 build wrote into the theme's own schema file."""
    for name in ("options.json", "theme.json"):
        schema = _read(theme_dir / name)
        options = schema.get("options")
        if not isinstance(options, list):
            continue
        found = {}
        for option in options:
            if not isinstance(option, dict) or "value" not in option:
                continue
            key = str(option.get("key") or option.get("id") or "").strip()
            if key:
                found[key] = option["value"]
        if found:
            return found
    return {}


def migrate_from_packages(themes_dir: Path) -> list[str]:
    """Lift values out of installed theme packages, once, before anything updates them.

    Must run before `main.py` auto-installs and updates themes, or the release that
    fixes the data loss is the release that causes it: an update deletes the package
    these values are still sitting in. A theme that already has an entry is never
    overwritten - it has already moved.
    """
    moved: dict[str, dict[str, Any]] = {}
    if not themes_dir.is_dir():
        return []
    held = config_service.theme_options()
    for theme_dir in sorted(p for p in themes_dir.iterdir() if p.is_dir()):
        folder = theme_dir.name
        if folder in held:
            continue
        values = values_in_package(theme_dir)
        if values:
            moved[folder] = values
    if not moved:
        return []
    config_service.put_theme_options(moved)
    logger.info("Moved theme options out of %s installed theme%s: %s",
                len(moved), "" if len(moved) == 1 else "s", ", ".join(moved))
    return list(moved)


def save(folder: str, values: dict[str, Any]) -> None:
    """Replace this theme's values."""
    name = str(folder or "").strip()
    if not name:
        raise ValueError(t("error.themes.folder_name_unusable", folder=folder))
    config_service.put_theme_options({name: dict(values)})
