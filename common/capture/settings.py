"""The Recording settings as one recording reads them: this device's, with a run's own
values in place of any it names."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, fields
from typing import Any

from common import service_errors
from common.config import config_schema
from common.config.config_access import cfg_get
from common.host import frontend_browser
from common.i18n import t

logger = logging.getLogger("vpinfe.common.capture.settings")

SECTION = "capture"

AUTO = "auto"
H264 = "h264"
VP9 = "vp9"
STANDARD = "standard"
HIGH = "high"
SCREENS_OWN = "screen"

_PLAYS_NO_H264 = (frontend_browser.NO_H264, frontend_browser.NO_VIDEO,
                  frontend_browser.NO_BROWSER)


@dataclass(frozen=True)
class Settings:
    length: int
    wait: int
    picture_at: int
    fps: int
    size: str
    video_codec: str
    playfield_orientation: str
    quality: str
    sound: bool
    sound_source: str
    # Empty is VPinFE's own.
    record_command: str = ""
    encode_command: str = ""


def _options() -> dict[str, config_schema.ConfigOption]:
    return {option.key: option for option in config_schema.CONFIG_OPTIONS
            if option.section == SECTION}


def _value(option: config_schema.ConfigOption, raw: Any) -> Any:
    """Raises ValueError for anything the option does not take."""
    said = str(raw).strip().lower() if isinstance(raw, bool) else str(raw).strip()
    if option.type == "int":
        number = int(said)
        if number < 0:
            raise ValueError(said)
        return number
    if option.type == "bool":
        if said.lower() not in ("true", "false"):
            raise ValueError(said)
        return said.lower() == "true"
    if option.choices and said not in option.choices:
        raise ValueError(said)
    return said


def _refused(key: str, raw: Any = "") -> service_errors.RefusedError:
    return service_errors.RefusedError(t("error.capture.setting_refused", setting=key,
                                         value=raw))


def command_refused(key: str, raw: Any) -> str:
    """Why a command setting cannot hold `raw`, naming the command; "" where it can."""
    from . import commands

    command = next((one for one, setting in commands.SETTINGS.items() if setting == key),
                   None)
    if command is None:
        return ""
    wrong = commands.problems(str(raw or ""), command)
    return t("capture.command.in", command=commands.label(command),
             problem=commands.words(wrong[0])) if wrong else ""


def refused(wanted: Mapping[str, Mapping[str, Any]]) -> str:
    """Why a settings write cannot take what it holds for this section; "" where it
    can."""
    for key, raw in (wanted.get(SECTION) or {}).items():
        said = command_refused(key, raw)
        if said:
            return said
    return ""


def read(config: Any = None, overrides: Mapping[str, Any] | None = None) -> Settings:
    """A value the device's file holds that the setting does not take reads as its
    default; one in `overrides` is refused."""
    if config is None:
        from common.config.paths import get_ini_config
        config = get_ini_config()
    options = _options()
    values: dict[str, Any] = {}
    for key, option in options.items():
        try:
            values[key] = _value(option, cfg_get(config, SECTION, key, option.default))
        except ValueError:
            values[key] = _value(option, option.default)
        if command_refused(key, values[key]):
            logger.warning("The %s setting cannot run, so VPinFE's own is used: %s", key,
                           values[key])
            values[key] = _value(option, option.default)
    for key, raw in (overrides or {}).items():
        if key not in options:
            raise _refused(key, raw)
        try:
            values[key] = _value(options[key], raw)
        except (TypeError, ValueError) as exc:
            raise _refused(key, raw) from exc
        said = command_refused(key, values[key])
        if said:
            raise service_errors.RefusedError(said)
    if values["length"] < 1 or values["picture_at"] >= values["length"]:
        if overrides and {"length", "picture_at"} & set(overrides):
            raise _refused("picture_at", values["picture_at"])
        values["length"] = max(1, values["length"])
        values["picture_at"] = min(values["picture_at"], values["length"] - 1)
    values["fps"] = int(values["fps"])
    return Settings(**{field.name: values[field.name] for field in fields(Settings)})


def video_codec(chosen: str, browser_state: str | None = None) -> str:
    """The stored format, or the one Automatic resolves to on this device."""
    if chosen != AUTO:
        return chosen
    state = frontend_browser.current()["state"] if browser_state is None else browser_state
    return VP9 if state in _PLAYS_NO_H264 else H264
