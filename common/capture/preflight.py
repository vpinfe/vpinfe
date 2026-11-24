"""What this device can record, and exactly why not."""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from typing import Any

from common.host import display_service, tools
from common.i18n import t
from common.timestamps import utc_now_iso

from . import adapters, commands, placing, settings

NEEDS_TOOL = "capture.tool.needed"
NO_ENCODER = "capture.encoder.missing"
UNREADABLE = "capture.screen.unreadable"
NO_SOUND_INPUT = "capture.sound.no_input"
NO_SOUND_SERVER = "capture.sound.no_server"
NO_MP3 = "capture.sound.no_mp3"
NO_GRABBER = "capture.input.missing"
LACKS = "capture.tool.lacks"

# Who can fix each reason.
REASONS = {
    adapters.NO_SCREEN: tools.FIX_NONE,
    adapters.NOT_FOUND: tools.FIX_NONE,
    adapters.ASLEEP: tools.FIX_NONE,
    placing.NOT_SHOWN: tools.FIX_NONE,
    adapters.NO_WAY: tools.FIX_NONE,
    adapters.NO_SESSION: tools.FIX_NONE,
    UNREADABLE: tools.FIX_NONE,
    NEEDS_TOOL: tools.FIX_USER,
    NO_ENCODER: tools.FIX_USER,
    NO_SOUND_INPUT: tools.FIX_USER,
    NO_SOUND_SERVER: tools.FIX_NONE,
    NO_MP3: tools.FIX_USER,
    NO_GRABBER: tools.FIX_USER,
    LACKS: tools.FIX_USER,
    adapters.SCREEN_PERMISSION: tools.FIX_USER,
    adapters.NOT_CHOSEN: tools.FIX_AUTO,
    adapters.SOUND_NOT_YET: tools.FIX_NONE,
    adapters.SOUND_LOOPBACK: tools.FIX_NONE,
}

# A remedy that is no Tool's, by the reason it answers.
REMEDIES = {adapters.SCREEN_PERMISSION: {"key": "capture.permission.screen.remedy",
                                         "params": {}},
            adapters.NOT_CHOSEN: {"key": "capture.portal.not_chosen.remedy", "params": {}}}

# The encoder each stored format is written with, as FFmpeg names it.
ENCODERS = {settings.H264: "libx264", settings.VP9: "libvpx-vp9"}
_FORMAT_NAMES = {settings.H264: "H.264", settings.VP9: "VP9"}


def reason(key: str, params: Mapping[str, str] | None = None,
           remedy: Mapping[str, Any] | None = None) -> dict[str, Any]:
    remedy = remedy or REMEDIES.get(key)
    fix = tools.FIX_AUTO if remedy and remedy.get("get") else REASONS[key]
    return {"key": key, "params": dict(params or {}), "fix": fix,
            "remedy": dict(remedy) if fix != tools.FIX_NONE and remedy else None}


def words(said: Mapping[str, Any]) -> str:
    """A reason in this install's language, with its remedy where it has one."""
    params = dict(said.get("params") or {})
    if "window" in params:
        params["window"] = t(f"media.kind.{params['window']}.label")
    text = t(str(said.get("key") or ""), **params)
    remedy = said.get("remedy")
    return t("capture.reason.with_remedy", reason=text, remedy=tools.words(remedy)) \
        if remedy else text


def _able(blocked: dict[str, Any] | None) -> dict[str, Any]:
    return {"available": blocked is None, "reason": blocked}


def _needs(found: Mapping[str, tools.Found], tool: tools.Tool,
           encoder: str = "", format_name: str = "") -> dict[str, Any] | None:
    """Why `tool` cannot do its part, or None where it can."""
    have = found[tool.id]
    if have.state is not tools.State.FOUND or have.probe is None:
        return reason(NEEDS_TOOL, {"tool": tool.name}, tools.remedy(tool, missing=True))
    if encoder and not have.probe.has(tools.ENCODERS, encoder):
        return reason(NO_ENCODER, {"format": format_name}, tools.remedy(tool))
    return None


def sound_server(env: Mapping[str, str]) -> bool:
    """PulseAudio's socket, or PipeWire's stand-in for it."""
    if env.get("PULSE_SERVER"):
        return True
    runtime = str(env.get("XDG_RUNTIME_DIR") or "")
    return bool(runtime) and os.path.exists(os.path.join(runtime, "pulse", "native"))


def _grabber(adapter: adapters.Adapter,
             found: Mapping[str, tools.Found]) -> dict[str, Any] | None:
    """Why the Tools found cannot read this desktop's screens, where they have to."""
    ffmpeg = found[tools.FFMPEG.id]
    if ffmpeg.state is tools.State.FOUND and not adapter.grabs(ffmpeg):
        return reason(NO_GRABBER, {}, tools.remedy(tools.FFMPEG))
    lacking = adapter.lacks(found)
    if lacking is not None:
        tool, part = lacking
        return reason(LACKS, {"tool": tool.name, "part": part}, tools.remedy(tool))
    return None


def _sound(adapter: adapters.Adapter, found: Mapping[str, tools.Found],
           env: Mapping[str, str]) -> dict[str, Any]:
    never = adapter.no_sound()
    if never is not None:
        return _able(reason(never[0], never[1]))
    ffmpeg = found[tools.FFMPEG.id]
    blocked = _needs(found, tools.FFMPEG)
    if blocked is None and ffmpeg.probe is not None:
        if not ffmpeg.probe.has(tools.INPUTS, "pulse"):
            blocked = reason(NO_SOUND_INPUT, {}, tools.remedy(tools.FFMPEG))
        elif not sound_server(env):
            blocked = reason(NO_SOUND_SERVER)
        elif not ffmpeg.probe.has(tools.ENCODERS, "libmp3lame"):
            blocked = reason(NO_MP3, {}, tools.remedy(tools.FFMPEG))
    return _able(blocked)


def _screen(screen: adapters.Screen, adapter: adapters.Adapter,
            found: Mapping[str, tools.Found], codec: str,
            unreadable: str) -> dict[str, Any]:
    output = screen.output
    where = None
    if output is None:
        key = unreadable if unreadable and screen.reason == adapters.NOT_FOUND \
            else screen.reason
        where = reason(key, {"window": screen.window, **screen.params})
    refused = adapter.refused()
    where = where or (reason(*refused) if refused else None)
    grabber = _grabber(adapter, found)
    picture = where or _needs(found, adapter.picture_tool) or grabber \
        or _needs(found, tools.FFMPEG, "png", "PNG")
    video = where or _needs(found, adapter.video_tool) or grabber \
        or _needs(found, tools.FFMPEG, ENCODERS[codec], _FORMAT_NAMES[codec])
    return {"window": screen.window,
            "output": output.name if output else None,
            "size": [output.width, output.height] if output else None,
            "surface": output.surface if output else None,
            "picture": _able(picture), "video": _able(video)}


def report(*, adapter: adapters.Adapter | adapters.Unsupported | None = None,
           config: Any = None, monitors: Sequence[Any] | None = None,
           found: Mapping[str, tools.Found] | None = None,
           env: Mapping[str, str] | None = None, browser_state: str | None = None,
           probe_hardware: bool = True, shown: placing.Shown | None = None
           ) -> dict[str, Any]:
    """The report `GET /capture` serves. `probe_hardware` false skips encoding a frame to
    find out whether every screen records at once, and says they do not. `shown` is what
    the default launcher's app says of its windows, asked where it is None."""
    env = os.environ if env is None else env
    adapter = adapters.resolve(env) if adapter is None else adapter
    if config is None:
        from common.config.paths import get_ini_config
        config = get_ini_config()
    codec = settings.video_codec(settings.read(config).video_codec, browser_state)
    head = {"observed_at": utc_now_iso(), "adapter": adapter.id, "video_codec": codec}
    if isinstance(adapter, adapters.Unsupported):
        why = reason(adapter.reason, adapter.params)
        return {**head, "available": False, "reason": why, "screens": [],
                "sound": _able(why), "at_once": False, "tools": [],
                "commands": commands.own(adapter.id, {}, "")}

    found = {tool.id: tools.resolve(tool) for tool in adapter.requirements()} \
        if found is None else found
    unreadable = ""
    try:
        outputs = adapter.outputs()
    except adapters.AsleepError:
        outputs, unreadable = [], adapters.ASLEEP
    except (OSError, ValueError):
        outputs, unreadable = [], UNREADABLE
    monitors = display_service.get_display_monitors() if monitors is None else monitors
    placed = placing.Placing(outputs, config, monitors,
                             placing.shown() if shown is None else shown)
    screens = [_screen(screen, adapter, found, codec, unreadable)
               for screen in placed.screens().values()]
    ffmpeg = found[tools.FFMPEG.id]
    hardware = adapter.hardware(ffmpeg) if probe_hardware else ""
    able = [one for one in screens if one["picture"]["available"]
            or one["video"]["available"]]
    first = next((one[kind]["reason"] for one in screens
                  for kind in ("video", "picture") if one[kind]["reason"]), None)
    return {**head,
            "available": bool(able),
            "reason": None if able else first,
            "screens": screens,
            "sound": _sound(adapter, found, env),
            "at_once": bool(probe_hardware and adapter.at_once(ffmpeg)),
            "tools": [tools.row(found[tool.id]) for tool in adapter.requirements()],
            "commands": commands.own(adapter.id, found, hardware)}


def available() -> bool | tuple[bool, str]:
    """For the `capture` capability."""
    found = report(probe_hardware=False)
    if found["available"]:
        return True
    return False, words(found["reason"])
