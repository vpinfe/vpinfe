"""Upgrading the themes of a 2.x install to 3.0's versions.

`run` runs at every start, after the lists are read and before the default themes install,
and moves an official theme to its 3.0 version and folds a merged twin into its target.
`move_stock_address` runs before the lists are read. Copying the 2.x folders over is
`theme_seed`, which runs with the ini conversion.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from common.config.config_access import cfg_get
from common.online import theme_service, theme_sources
from common.online.theme_level import MIN_VERSION_KEY
from common.online.themes import ThemeRegistry
from common.release import migration_ledger
from common.settings import config_service
from common.values import newer_version, parse_version
from common.vpinfe_version import get_version

logger = logging.getLogger("vpinfe.common.online.theme_upgrade")

FIRST_3X_VERSION = (3, 0)
UPDATED = "theme_update"


@dataclass(frozen=True)
class Twin:
    """A 2.x theme that 3.0 merged into another, and the option of the other that gives
    the look the twin had. A target that needs no option, because core's media priority
    already gives that look, has none."""
    target: str
    option: str | None
    ledger: str


TWINS = {
    "Slider Video": Twin("slider", None, "theme_slider_video"),
    "Texal-Flyer": Twin("Texal", "showFlyer", "theme_texal_flyer"),
}

# The VPinFE Theme List before it moved to the vpinfe organization. A config an earlier
# 3.0 build wrote still holds it as `themes.registries`.
RETIRED_STOCK_LIST = "https://raw.githubusercontent.com/superhac/vpinfe-themes/master/themes.json"


def move_stock_address() -> bool:
    """Point a config that holds the retired VPinFE Theme List at the current one, in the
    same place in the order, and leave every other address as it is. Where the current list
    is already there, the retired one is only dropped. True when the settings changed.

    Runs before the registry reads its lists: `is_stock` is what lets a theme count as
    official, so a registry built on the retired address would skip the 3.0 updates."""
    try:
        held = list(theme_sources.from_config(config_service.get_ini_config()).registries)
    except Exception:  # noqa: BLE001 - retried at the next start
        logger.warning("Could not read the theme lists, leaving the stock one for the next start")
        return False
    stock = theme_sources.stock_list()
    retired = _fetched(RETIRED_STOCK_LIST)
    moved: list[str] = []
    for address in held:
        if _fetched(address) == retired:
            if stock in held:
                continue
            address = stock
        moved.append(address)
    if moved == held:
        return False
    try:
        config_service.set_values({"themes": {"registries": moved}})
    except Exception:  # noqa: BLE001 - retried at the next start
        logger.warning("Could not move the theme list to the VPinFE Theme List's new address")
        return False
    logger.info("The VPinFE Theme List is now read from %s", stock)
    return True


def _fetched(address: str) -> str:
    """The address VPinFE fetches for what was written, so spellings of one list compare equal."""
    where = theme_sources.parse(address)
    return where.url if where is not None else address.strip()


def wanted(registry: ThemeRegistry) -> set[str]:
    """The themes `run` needs read although they do not install on their own: the
    official ones that are installed, and the target of every twin that is."""
    themes_dir = Path(registry.themes_dir)
    found = set()
    for key in registry.themes_index:
        if not theme_sources.is_stock(registry.origins.get(key, "")):
            continue
        twinned = any(one.target == key and os.path.lexists(themes_dir / twin)
                      for twin, one in TWINS.items())
        if twinned or os.path.lexists(themes_dir / key):
            found.add(key)
    return found


def run(registry: ThemeRegistry) -> bool:
    """Update the official themes still on their 2.x build, then fold in the twins.
    True when anything changed on disk or in the settings."""
    updated = _update_official(registry)
    merged = _merge_twins(registry)
    if updated:
        migration_ledger.record(UPDATED, get_version(), len(updated))
    for twin in merged:
        migration_ledger.record(TWINS[twin].ledger, get_version(), 1)
    return bool(updated or merged)


def _update_official(registry: ThemeRegistry) -> list[str]:
    """An installed theme from the VPinFE Theme List is a 2.x build when it declares no
    minimum version while the list's version declares 3.0 or later. Once installed over,
    it declares one, so this stops matching on its own."""
    updated = []
    for key, entry in registry.themes.items():
        if not theme_sources.is_stock(str(entry.get("source") or "")):
            continue
        folder = Path(registry.themes_dir) / key
        if folder.is_symlink() or not folder.is_dir() or _declares_minimum(folder):
            continue
        offered = parse_version((entry.get("manifest") or {}).get(MIN_VERSION_KEY))
        if not offered or newer_version(FIRST_3X_VERSION, offered):
            continue
        try:
            registry.install_theme(key, force=True)
        except Exception as exc:  # noqa: BLE001 - offline or a bad download: retried next start
            logger.warning("Could not upgrade %s to its 3.0 version: %s", key, exc)
            continue
        logger.info("Upgraded %s to its 3.0 version", key)
        updated.append(key)
    return updated


def _merge_twins(registry: ThemeRegistry) -> list[str]:
    """What the person was looking at is what they get. The target comes first and has
    to be a 3.0 copy, because the option is one only that copy has."""
    merged = []
    themes_dir = Path(registry.themes_dir)
    for name, twin in TWINS.items():
        if not os.path.lexists(themes_dir / name):
            continue
        target = themes_dir / twin.target
        if not os.path.lexists(target) and twin.target in registry.themes:
            try:
                registry.install_theme(twin.target)
            except Exception as exc:  # noqa: BLE001 - a bad download must not cost the default install
                logger.warning("Could not install %s for %s: %s", twin.target, name, exc)
        if not target.is_dir() or not _declares_minimum(target):
            continue
        active = _active_theme()
        if active is None:
            continue
        if active == name:
            if twin.option:
                held = config_service.theme_options().get(name, {})
                config_service.put_theme_options({twin.target: {**held, twin.option: True}})
            theme_service.set_active_theme(twin.target)
        config_service.drop_theme_options([name])
        _remove(themes_dir / name)
        logger.info("%s is now part of %s", name, twin.target)
        merged.append(name)
    return merged


def _active_theme() -> str | None:
    """The chosen theme, or None when the settings cannot be read. `get_active_theme`
    answers Revolution then, which would read as the twin not being the one in use."""
    try:
        return cfg_get(config_service.get_ini_config(), "general", "theme", "Revolution").strip()
    except Exception:  # noqa: BLE001 - retried at the next start
        logger.warning("Could not read the active theme, leaving the twins for the next start")
        return None


def _declares_minimum(folder: Path) -> bool:
    return bool(parse_version(_manifest(folder).get(MIN_VERSION_KEY)))


def _manifest(folder: Path) -> dict[str, Any]:
    try:
        found = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return found if isinstance(found, dict) else {}


def _remove(path: Path) -> None:
    with contextlib.suppress(OSError):
        if path.is_symlink():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)
