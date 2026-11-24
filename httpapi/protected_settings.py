"""Which settings a Confirm stands in front of: the ones that decide what VPinFE runs.

A change is Protected only when it changes the value, so a page that sends back a section
it did not edit never asks. Answers are about a patch against what is held now; nothing here
reads or writes the config.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

from common.config import config_schema
from common.online import theme_sources

SECTIONS = frozenset({"commands", "table_commands", "tools"})
KEYS = frozenset({
    ("chromium", "path"), ("chromium", "options"), ("chromium", "defaults"),
    ("chromium", "options_exclude"),
    ("capture", "record_command"), ("capture", "encode_command"),
    ("backups", "folder"),
})
# Where a theme is looked for. An address added here is code that can run once its theme
# is accepted, so a new one that is not the stock list's own is Protected.
SOURCE_KEYS = frozenset({("themes", "registries"), ("themes", "repositories")})
LAUNCHER_FIELDS = frozenset({"bin_path", "args", "launch_env", "on_table_start",
                             "on_table_exit"})
LAUNCHERS = "launchers"
_SPLIT = re.compile(r"[,\n]")


def _plain(value: Any) -> str:
    return str(value if value is not None else "").strip()


def _addresses(value: Any) -> set[str]:
    if isinstance(value, (list, tuple)):
        return {_plain(one) for one in value if _plain(one)}
    return {part.strip() for part in _SPLIT.split(_plain(value)) if part.strip()}


def _located(section: str, key: str) -> tuple[str, str]:
    try:
        return config_schema.locate(section, key)
    except Exception:  # noqa: BLE001 - an unknown key is refused later, with its own words
        return (section, key)


def setting_is_protected(section: str, key: str) -> bool:
    section, key = _located(section, key)
    return section in SECTIONS or (section, key) in KEYS or (section, key) in SOURCE_KEYS


def settings_change(patch: Mapping[str, Any], held: Mapping[str, Any]) -> bool:
    """Whether a `settings` patch changes a Protected value."""
    for section, entries in (patch or {}).items():
        for key, value in (entries or {}).items():
            where = _located(section, key)
            if not setting_is_protected(*where):
                continue
            now = (held.get(where[0]) or {}).get(where[1])
            if where in SOURCE_KEYS:
                added = _addresses(value) - _addresses(now)
                if any(not theme_sources.is_stock(one) for one in added):
                    return True
            elif _plain(value) != _plain(now):
                return True
    return False


def _launcher_values(item: Mapping[str, Any]) -> dict[str, str]:
    settings = item.get("settings")
    settings = settings if isinstance(settings, Mapping) else {}
    return {key: _plain(settings.get(key)) for key in LAUNCHER_FIELDS if _plain(settings.get(key))}


def launchers_change(items: Iterable[Mapping[str, Any]], held: Iterable[Mapping[str, Any]]) -> bool:
    """Whether launcher items, as a write would leave them, change a program or a command."""
    kept = {_plain(one.get("id")): _launcher_values(one) for one in held}
    return any(_launcher_values(one) != kept.get(_plain(one.get("id")), {}) for one in items
               if isinstance(one, Mapping) and _launcher_values(one))


def launcher_body_protected(body: Mapping[str, Any]) -> bool:
    """Whether a launcher config write sets a field that decides what runs. Clearing one is
    not Protected: taking a command away is never the dangerous direction."""
    values = body.get("values")
    return isinstance(values, Mapping) and any(
        key in LAUNCHER_FIELDS and _plain(value) for key, value in values.items())


def patch_change(patch: Mapping[str, Any], held: Mapping[str, Any]) -> bool:
    """Whether a whole-config patch, shaped like a read, changes anything Protected."""
    if settings_change(patch.get("settings") or {}, held.get("settings") or {}):
        return True
    items = patch.get(LAUNCHERS)
    return isinstance(items, list) and launchers_change(items, held.get(LAUNCHERS) or [])
