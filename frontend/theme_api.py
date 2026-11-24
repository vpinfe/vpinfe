"""Which theme is active, where it lives, and what its manifest says."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any
from urllib.parse import quote

from common.config.config_access import ConfigSource, SettingsConfig
from common.config.paths import THEMES_DIR
from common.devices import serving
from common.security import accepted
from common.settings import theme_options
from frontend import theme_contract

logger = logging.getLogger("vpinfe.frontend.theme_api")


_warned: set[str] = set()


def get_theme_name(config: ConfigSource) -> str:
    """The theme to play: the one chosen, unless nobody has accepted it, which plays the
    default so a screen is never left with a theme it must not run."""
    chosen = SettingsConfig.from_config(config).theme
    if accepted.theme_runs(chosen):
        return chosen
    if chosen not in _warned:
        _warned.add(chosen)
        logger.warning("Theme %s is not accepted; playing %s instead", chosen,
                       accepted.default_theme())
    return accepted.default_theme()


def resolve_theme_dir(theme_name: str) -> Path | None:
    theme_dir = THEMES_DIR / theme_name
    return theme_dir if theme_dir.is_dir() else None


def read_manifest(theme_dir: str | Path) -> dict | None:
    """A theme's manifest.json, or None when it has none or it is unreadable."""
    return _read_json_object(Path(theme_dir) / "manifest.json")


def _read_json_object(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        logger.warning("Theme config is invalid JSON: %s", path, exc_info=True)
        return None
    except OSError:
        logger.warning("Could not read theme config: %s", path, exc_info=True)
        return None
    return data if isinstance(data, dict) else None


def _deep_merge(base: dict, overlay: dict) -> dict:
    merged = dict(base)
    for key, value in overlay.items():
        existing = merged.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            merged[key] = _deep_merge(existing, value)
        else:
            merged[key] = value
    return merged


def _deep_set(config: dict, dotted_key: str, value: Any) -> None:
    current = config
    parts = dotted_key.split(".")
    for part in parts[:-1]:
        next_value = current.get(part)
        if not isinstance(next_value, dict):
            next_value = {}
            current[part] = next_value
        current = next_value
    current[parts[-1]] = value


def _build_theme_config_from_schema(schema: dict) -> dict | None:
    raw_options = schema.get("options")
    if not isinstance(raw_options, list):
        return None

    config: dict = {}
    found_option = False
    for raw_option in raw_options:
        if not isinstance(raw_option, dict):
            continue
        key = str(raw_option.get("key") or raw_option.get("id") or "").strip()
        if not key:
            continue
        value = raw_option.get("default") if "default" in raw_option else raw_option.get("value")
        _deep_set(config, key, value)
        found_option = True
    return config if found_option else None


def get_theme_config(config: ConfigSource) -> dict | None:
    """The theme's config: what the author set, with the user's option values over it.

    Three sources, narrowing: config.json is the author's fixed settings, theme.json
    declares the options and their defaults, and the user's own file says what they
    picked. Returning theme.json alone drops every author value the moment a theme has
    one option.
    """
    theme_dir = resolve_theme_dir(get_theme_name(config))
    if not theme_dir:
        return None

    authored = _read_json_object(theme_dir / "config.json")
    schema = _read_json_object(theme_options.schema_path(
        theme_dir, theme_contract.declared_contract(theme_dir)))
    options = _build_theme_config_from_schema(schema) if schema is not None else None
    # What the user actually chose lives outside the theme, so an update cannot take it.
    # It goes over the schema's defaults and under nothing.
    chosen = theme_options.load(Path(theme_dir).name)
    if chosen:
        options = {**(options or {}), **chosen}

    if authored is None and options is None:
        logger.debug("Theme config not found: %s/{config,theme}.json", theme_dir)
        return None
    return _deep_merge(authored or {}, options or {})


def get_audio_muted(config: ConfigSource) -> bool:
    return SettingsConfig.from_config(config).mute_audio


# Windows core draws itself when the theme ships no page for them, and the page it uses.
CORE_WINDOW_PAGES = {
    "topper": "index_topper.html",
    "backglass": "index_backglass.html",
    "scoreview": "index_scoreview.html",
}


def window_page_path(config: ConfigSource, window_name: str) -> str:
    """Where a window's page is served from, relative to the server root: the theme's own,
    or core's where core draws this window and the theme has no page for it."""
    theme_name = get_theme_name(config)
    page = f"index_{window_name}.html"
    core_page = CORE_WINDOW_PAGES.get(window_name)
    if core_page:
        theme_dir = resolve_theme_dir(theme_name)
        if theme_dir is None or not (theme_dir / page).is_file():
            return f"core/{core_page}"
    return f"themes/{quote(theme_name, safe='')}/{page}"


def get_theme_index_page(config: ConfigSource, window_name: str) -> str:
    port = serving.own_port(config)
    return f"http://127.0.0.1:{port}/{window_page_path(config, window_name)}?window={window_name}"
