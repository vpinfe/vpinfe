"""Installing, removing and switching frontend themes, and the options one declares.

Here rather than under a UI package: this is what a theme *is* and what can be done to
one, and the HTTP layer may not reach into a user interface.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from common.config.config_access import cfg_get
from common.config.config_store import ConfigStore
from common.config.paths import THEMES_DIR, VPINFE_INI_PATH
from common.i18n import t
from common.media import theme_loading
from common.online.theme_level import OLDEST_CONTRACT, declared_contract
from common.online.themes import ThemeRegistry
from common.settings import config_service, theme_options


def get_active_theme() -> str:
    try:
        config = ConfigStore(str(VPINFE_INI_PATH))
        theme_name = cfg_get(config, "general", "theme", "Revolution").strip()
        return theme_name or "Revolution"
    except Exception:
        return "Revolution"


def set_active_theme(theme_key: str) -> None:
    """Through the settings write, so what is waiting on a restart is told of it."""
    config_service.set_values({"themes": {"active": theme_key}})


def load_registry(previous: ThemeRegistry | None = None) -> ThemeRegistry:
    registry = ThemeRegistry()
    registry.load_registry(previous=previous)
    registry.load_theme_manifests(previous=previous)
    return registry


def install_theme(registry: ThemeRegistry, theme_key: str) -> None:
    registry.install_theme(theme_key, force=True)


def delete_theme(registry: ThemeRegistry, theme_key: str) -> None:
    registry.delete_theme(theme_key)


def get_installed_theme_dir(theme_key: str, registry: ThemeRegistry | None = None) -> Path | None:
    if registry is not None:
        try:
            folder_name = registry.get_installed_folder(theme_key)
        except Exception:
            folder_name = None
        if folder_name:
            theme_dir = THEMES_DIR / folder_name
            if theme_dir.is_dir():
                return theme_dir

    theme_dir = THEMES_DIR / str(theme_key or "").strip()
    return theme_dir if theme_dir.is_dir() else None


def _folder_for(theme_key: str, registry: ThemeRegistry | None = None) -> str:
    """The folder a theme is installed under - what its options are keyed by.

    Falls back to the key, which is what a registry install renames the folder to.
    """
    theme_dir = get_installed_theme_dir(theme_key, registry)
    return theme_dir.name if theme_dir is not None else str(theme_key or "").strip()


def _read_json_object(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


def _normalize_option_type(raw_value: Any) -> str:
    normalized = str(raw_value or "text").strip().lower()
    return {
        "str": "text",
        "string": "text",
        "integer": "number",
        "int": "number",
        "float": "number",
        "bool": "boolean",
        "checkbox": "boolean",
        "choice": "select",
        "dropdown": "select",
        "multiline": "textarea",
        "object": "json",
    }.get(normalized, normalized or "text")


def _normalize_select_options(raw_options: Any) -> list[Any]:
    if not isinstance(raw_options, list):
        return []

    normalized: list[Any] = []
    for item in raw_options:
        if isinstance(item, dict):
            label = str(item.get("label", "") or "").strip()
            value = item.get("value")
            if value in ("", None):
                continue
            normalized.append({
                "label": label or str(value),
                "value": value,
            })
        elif item not in ("", None):
            normalized.append(item)
    return normalized


def _dynamic_select_options(source: str) -> list[dict[str, Any]] | None:
    """Options a theme cannot know ahead of time, discovered from the library.

    "wheelsets" is every wheel set folder across the games plus the virtual
    "logo" set. The leading Default entry saves as "" - no set, plain wheels.
    """
    if source != "wheelsets":
        return None
    from common.games.media_service import wheel_sets

    names: list[str] = []
    try:
        names = wheel_sets()
    except Exception:
        pass
    return ([{"label": "Default", "value": ""}]
            + [{"label": name, "value": name} for name in names])


ATTRACT_TWO_X = "contract_1"
ATTRACT_OWN = "own"


def attract_left_to_the_theme(theme_key: str, registry: ThemeRegistry | None = None) -> str:
    """Why core's attract mode does not run under this theme, or "" where it does.

    The same rule vpinfe-core.js applies at its `core_attract` capability: what the theme's
    config.json states under `attract.enabled` decides, and a theme that states nothing gets
    it from contract 2 and not from contract 1.
    """
    theme_dir = get_installed_theme_dir(theme_key, registry)
    if theme_dir is None:
        return ""
    block = (_read_json_object(theme_dir / "config.json") or {}).get("attract")
    if isinstance(block, dict) and "enabled" in block:
        return "" if block["enabled"] else ATTRACT_OWN
    if declared_contract(theme_dir) <= OLDEST_CONTRACT:
        return ATTRACT_TWO_X
    return ""


NO_LOADING: dict[str, Any] = {"video": "", "image": "", "audio": "", "own": False}


def _with_option_values(config: dict[str, Any] | None, values: dict[str, Any]) -> dict[str, Any]:
    """The theme's config.json with its option values laid over it, the way the launch reads
    it. An option's key is a dotted path into the config."""
    merged: dict[str, Any] = json.loads(json.dumps(config or {}))
    for dotted, value in values.items():
        node = merged
        *parents, leaf = str(dotted).split(".")
        for part in parents:
            child = node.get(part)
            if not isinstance(child, dict):
                child = node[part] = {}
            node = child
        node[leaf] = value
    return merged


def loading_of_the_theme(theme_key: str, registry: ThemeRegistry | None = None,
                         ) -> dict[str, Any]:
    """What the theme says about the loading screen: the video, image and sound it names
    (paths inside its folder, "" for none) and whether it draws its own screen, as the launch
    reads it: a theme that does not say takes it from its contract."""
    theme_dir = get_installed_theme_dir(theme_key, registry)
    if theme_dir is None:
        return dict(NO_LOADING)
    config = _with_option_values(_read_json_object(theme_dir / "config.json"),
                                 get_theme_option_values(theme_key, registry))
    on_by_default = declared_contract(theme_dir) > OLDEST_CONTRACT
    return {**theme_loading.files(theme_dir, config),
            "own": theme_loading.draws_its_own(config, on_by_default)}


def load_theme_option_schema(
        theme_key: str, registry: ThemeRegistry | None = None) -> dict[str, Any] | None:
    theme_dir = get_installed_theme_dir(theme_key, registry)
    if theme_dir is None:
        return None

    schema = _read_json_object(theme_options.schema_path(theme_dir, declared_contract(theme_dir)))
    if not schema:
        return None

    raw_options = schema.get("options")
    if not isinstance(raw_options, list):
        return None

    normalized_options: list[dict[str, Any]] = []
    for raw_option in raw_options:
        if not isinstance(raw_option, dict):
            continue
        key = str(raw_option.get("key") or raw_option.get("id") or "").strip()
        if not key:
            continue

        option_type = _normalize_option_type(raw_option.get("type"))
        option = dict(raw_option)
        option["key"] = key
        named = raw_option.get("name") or raw_option.get("label") or key
        option["name"] = str(named).strip() or key
        option["description"] = str(raw_option.get("description") or "").strip()
        option["type"] = option_type
        if option_type == "select":
            dynamic = _dynamic_select_options(str(raw_option.get("source") or "").strip().lower())
            option["options"] = (dynamic if dynamic is not None
                                 else _normalize_select_options(raw_option.get("options")))
        normalized_options.append(option)

    if not normalized_options:
        return None

    return {
        "title": str(schema.get("title") or "").strip(),
        "description": str(schema.get("description") or "").strip(),
        "options": normalized_options,
    }


def get_theme_option_values(
        theme_key: str, registry: ThemeRegistry | None = None) -> dict[str, Any]:
    schema = load_theme_option_schema(theme_key, registry)
    if schema is None:
        return {}

    # The author's schema supplies the default; `theme_options` supplies what they
    # chose. A `value` still sitting in the package is a pre-3.0 leftover and is read
    # only until the migration lifts it out.
    chosen = theme_options.load(_folder_for(theme_key, registry))
    values: dict[str, Any] = {}
    for option in schema["options"]:
        key = option["key"]
        if key in chosen:
            values[key] = chosen[key]
            continue
        values[key] = option.get("default") if "default" in option else option.get("value")

    return values


def _coerce_theme_option_value(option: dict[str, Any], raw_value: Any) -> Any:
    option_type = option.get("type", "text")
    if option_type == "boolean":
        if isinstance(raw_value, bool):
            return raw_value
        return str(raw_value or "").strip().lower() in {"1", "true", "yes", "on"}

    if option_type == "number":
        if raw_value in ("", None):
            return option.get("default") if "default" in option else None
        try:
            number = float(raw_value)
        except (TypeError, ValueError) as exc:
            raise ValueError(t("error.themes.option_takes_number", name=option["name"])) from exc
        return int(number) if number.is_integer() else number

    if option_type == "json":
        if raw_value in ("", None):
            return option.get("default", {})
        if isinstance(raw_value, (dict, list, int, float, bool)):
            return raw_value
        try:
            return json.loads(str(raw_value))
        except json.JSONDecodeError as exc:
            raise ValueError(t("error.themes.option_not_json", name=option["name"])) from exc

    if option_type == "select":
        allowed_values = []
        for item in option.get("options", []):
            if isinstance(item, dict):
                allowed_values.append(item.get("value"))
            else:
                allowed_values.append(item)
        if raw_value in ("", None):
            return option.get("default") if "default" in option else ""
        if allowed_values and raw_value not in allowed_values:
            raise ValueError(t("error.themes.option_bad_choice", name=option["name"]))
        return raw_value

    if raw_value is None:
        return option.get("default") if "default" in option else ""
    return str(raw_value)


def coerce_option_values(theme_folder: str, values: dict[str, Any]) -> dict[str, Any]:
    """`values` checked against what the theme in that folder declares.

    A value the theme does not declare is left out. A theme that is not installed, or
    declares no options, has nothing to check against and keeps what it is given.
    """
    schema = load_theme_option_schema(theme_folder)
    if schema is None:
        return dict(values)
    declared = {option["key"]: option for option in schema["options"]}
    return {key: _coerce_theme_option_value(declared[key], raw_value)
            for key, raw_value in values.items() if key in declared}


def save_theme_option_values(
    theme_key: str,
    values: dict[str, Any],
    registry: ThemeRegistry | None = None,
) -> None:
    if not isinstance(values, dict):
        raise TypeError("Theme option values must be a mapping.")

    schema = load_theme_option_schema(theme_key, registry)
    theme_dir = get_installed_theme_dir(theme_key, registry)
    if schema is None or theme_dir is None:
        raise ValueError(t("error.themes.no_settings", key=theme_key))

    # Written outside the theme, because an update deletes the package: values saved
    # into it were reset by the next update, every time, with no backup and no warning.
    schema_options_by_key = {option["key"]: option for option in schema["options"]}
    folder = _folder_for(theme_key, registry)
    keep = dict(theme_options.load(folder))
    for key, raw_value in values.items():
        if key in schema_options_by_key:
            keep[key] = _coerce_theme_option_value(schema_options_by_key[key], raw_value)

    theme_options.save(folder, keep)
