"""The two commands a recording runs, Record and Encode, as templates a person may write
in place of VPinFE's own.

argv, never a shell: a template is split the way a shell would split it and nothing else a
shell does, and each argument is filled in on its own, so a path with a space in it stays
one argument. A token standing for several arguments has to be an argument of its own.
"""

from __future__ import annotations

import os
import shlex
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from common import tokens
from common.host import tools
from common.i18n import t

from . import pipeline
from .adapters import Output

if TYPE_CHECKING:
    from .settings import Settings

RECORD = "record"
ENCODE = "encode"
COMMANDS = (RECORD, ENCODE)

# The setting each command is written in.
SETTINGS = {RECORD: "record_command", ENCODE: "encode_command"}

# Which tokens each command offers, in the order they are listed to a person.
OFFERED: dict[str, tuple[str, ...]] = {
    RECORD: ("ffmpeg", "recorder", "input", "output", "window", "screen", "monitor_index",
             "x", "y", "width", "height", "duration", "fps", "hwaccel", "audio_device"),
    ENCODE: ("ffmpeg", "input", "output", "window", "screen", "width", "height",
             "duration", "fps", "video_filters", "video_codec"),
}
TOKENS = tuple(dict.fromkeys(OFFERED[RECORD] + OFFERED[ENCODE]))
# Several arguments each.
LISTS = frozenset({"input", "video_filters", "video_codec", "hwaccel"})
# Without these a command has nothing to read or nowhere to write that VPinFE can find.
NEEDED = {RECORD: ("output",), ENCODE: ("input", "output")}

UNKNOWN = "capture.command.unknown"
ELSEWHERE = "capture.command.elsewhere"
ALONE = "capture.command.alone"
QUOTE = "capture.command.quote"
NEEDS = "capture.command.needs"

# The adapters' ids, as `adapters.resolve` answers them.
WLR = "wlr"
PORTAL = "portal"
DDAGRAB = "ddagrab"
GDIGRAB = "gdigrab"
X11GRAB = "x11grab"
AVFOUNDATION = "avfoundation"
FFMPEG_GRABS = (DDAGRAB, GDIGRAB, X11GRAB, AVFOUNDATION)

# The program that records a screen where FFmpeg does not.
RECORDERS = {WLR: tools.WF_RECORDER, PORTAL: tools.GSTREAMER}

# The encode where no hardware one was proved: near lossless, and fast enough to keep
# up, since the Encode Command encodes it again. wf-recorder's, FFmpeg's, GStreamer's.
_SOFTWARE = "-c libx264 -p preset=ultrafast -p crf=18"
_FFMPEG_SOFTWARE = "-c:v libx264 -preset ultrafast -crf 18"
_GSTREAMER_SOFTWARE = "x264enc speed-preset=ultrafast pass=qual quantizer=18"

# How often pipewiresrc hands over the last frame again where the screen has not changed.
KEEPALIVE_MS = 1000

# The hardware encoders a recording may use, by the name `hardware` holds, as FFmpeg
# takes each: near lossless, as the software one.
HARDWARE = {
    "h264_nvenc": ["-c:v", "h264_nvenc", "-preset", "p1", "-rc", "constqp", "-qp", "18"],
    "h264_amf": ["-c:v", "h264_amf", "-usage", "lowlatency", "-rc", "cqp",
                 "-qp_i", "18", "-qp_p", "18"],
    "h264_videotoolbox": ["-c:v", "h264_videotoolbox", "-realtime", "1", "-b:v", "25M"],
}

OWN_ENCODE = ("{ffmpeg} -hide_banner -nostdin -loglevel error -y {input} -map 0:v:0 -an "
              "{video_filters} {video_codec} -movflags +faststart {output}")


class CommandError(ValueError):
    """A template that cannot run. `reason` is a catalog key and its values."""

    def __init__(self, reason: dict[str, Any]) -> None:
        self.reason = reason
        super().__init__(words(reason))


def _said(key: str, **params: str) -> dict[str, Any]:
    return {"key": key, "params": params}


def label(command: str) -> str:
    return t(f"config.capture.{SETTINGS[command]}.label")


def words(reason: Mapping[str, Any]) -> str:
    return t(str(reason.get("key") or ""), **dict(reason.get("params") or {}))


def problems(template: str, command: str) -> list[dict[str, Any]]:
    """Why `template` cannot run as `command`, first found first; none where it can. An
    empty template is VPinFE's own."""
    if not str(template or "").strip():
        return []
    try:
        argv = shlex.split(template)
    except ValueError:
        return [_said(QUOTE)]
    found: list[dict[str, Any]] = []
    named: set[str] = set()
    other = next(one for one in COMMANDS if one != command)
    for argument in argv:
        for match in tokens.TOKEN.finditer(argument):
            name = match.group(1)
            named.add(name)
            if name not in TOKENS:
                found.append(_said(UNKNOWN, token=name))
            elif name not in OFFERED[command]:
                found.append(_said(ELSEWHERE, token=name, command=label(other)))
            elif name in LISTS and match.group(0) != argument:
                found.append(_said(ALONE, token=name))
    found += [_said(NEEDS, token=name) for name in NEEDED[command] if name not in named]
    return list({str(one): one for one in found}.values())


def expand(template: str, command: str,
           values: Mapping[str, str | Sequence[str]]) -> list[str]:
    """The template as argv. Raises CommandError where it cannot run."""
    wrong = problems(template, command)
    if wrong:
        raise CommandError(wrong[0])

    argv: list[str] = []
    for argument in shlex.split(template):
        whole = tokens.TOKEN.fullmatch(argument)
        if whole and whole.group(1) in LISTS:
            argv += [str(one) for one in values[whole.group(1)]]
        else:
            argv.append(tokens.fill(argument, {name: str(value) for name, value
                                               in values.items() if name not in LISTS}))
    return argv


def _x_screen(display: str) -> str:
    """`:0` as X names its first screen, `:0.0`."""
    said = display or ":0"
    return said if "." in said.rpartition(":")[2] else f"{said}.0"


def inputs(adapter_id: str, output: Output, display: str | None = None,
           hardware: str = "") -> list[str]:
    """VPinFE's capture input for one screen on each platform, as FFmpeg or wf-recorder
    takes it: at the screen's rate, without the pointer. Raises ValueError for an adapter
    with no input of its own."""
    fps = str(rate(output))
    size = f"{output.width}x{output.height}"
    if adapter_id == WLR:
        return ["-o", output.name]
    if adapter_id == PORTAL:
        return ["pipewiresrc", f"fd={output.remote}", f"path={output.index}",
                "do-timestamp=true", f"keepalive-time={KEEPALIVE_MS}"]
    if adapter_id == DDAGRAB:
        graph = f"ddagrab=output_idx={output.index}:framerate={fps}:draw_mouse=0"
        return ["-f", "lavfi", "-i",
                graph if hardware in HARDWARE else f"{graph},hwdownload,format=bgra"]
    if adapter_id == GDIGRAB:
        return ["-f", "gdigrab", "-framerate", fps, "-draw_mouse", "0",
                "-offset_x", str(output.x), "-offset_y", str(output.y),
                "-video_size", size, "-i", "desktop"]
    if adapter_id == X11GRAB:
        shown = os.environ.get("DISPLAY", "") if display is None else display
        return ["-f", "x11grab", "-framerate", fps, "-draw_mouse", "0",
                "-video_size", size, "-i", f"{_x_screen(shown)}+{output.x},{output.y}"]
    if adapter_id == AVFOUNDATION:
        return ["-f", "avfoundation", "-framerate", fps, "-capture_cursor", "0",
                "-i", f"Capture screen {output.index}:none"]
    raise ValueError(adapter_id)


def hwaccel(adapter_id: str, hardware: str) -> list[str]:
    """The hardware encoder's arguments for recording, or none. `hardware` is the VA-API
    render node on Linux, and the encoder's name elsewhere."""
    if not hardware:
        return []
    if adapter_id == WLR:
        return ["-c", "h264_vaapi", "-d", hardware, "-p", "qp=18"]
    if adapter_id == X11GRAB:
        return ["-vaapi_device", hardware, "-vf", "format=nv12,hwupload",
                "-c:v", "h264_vaapi", "-qp", "18"]
    return list(HARDWARE.get(hardware, []))


def _every_refresh(recorder: tools.Found | None) -> bool:
    """Whether this wf-recorder takes `-D`: a static screen otherwise gives one frame, and
    a stop that waits for the next forever."""
    probe = recorder.probe if recorder is not None else None
    return probe is not None and (probe.has(tools.OPTIONS, "-D")
                                  or probe.has(tools.OPTIONS, "--no-damage"))


def own_record(adapter_id: str, found: Mapping[str, tools.Found], hardware: str) -> str:
    """VPinFE's Record Command on this device, or "" where it has none yet."""
    if adapter_id in FFMPEG_GRABS:
        return " ".join(["{ffmpeg} -hide_banner -loglevel error -y {input}",
                         "{hwaccel}" if hardware else _FFMPEG_SOFTWARE,
                         "-f matroska {output}"])
    if adapter_id == PORTAL:
        return ("{recorder} -q -e {input} ! videoconvert ! videorate ! "
                f"video/x-raw,framerate={{fps}}/1 ! {_GSTREAMER_SOFTWARE} ! matroskamux ! "
                "filesink location={output}")
    if adapter_id != WLR:
        return ""
    return " ".join(["{recorder}",
                     *(["-D"] if _every_refresh(found.get(tools.WF_RECORDER.id)) else []),
                     "{input}", "{hwaccel}" if hardware else _SOFTWARE, "-f", "{output}"])


def _path(found: Mapping[str, tools.Found], tool: tools.Tool) -> str:
    one = found.get(tool.id)
    return str(one.path) if one is not None and one.path is not None else ""


def rate(output: Output) -> int:
    """The rate a screen records at: its refresh."""
    return round(output.refresh) or 60


def record_values(adapter_id: str, found: Mapping[str, tools.Found], output: Output,
                  window: str, dest: Path, chosen: Settings, hardware: str,
                  display: str | None = None) -> dict[str, str | list[str]]:
    ffmpeg = _path(found, tools.FFMPEG)
    recorder = RECORDERS.get(adapter_id)
    return {"ffmpeg": ffmpeg,
            "recorder": _path(found, recorder) if recorder is not None else ffmpeg,
            "input": inputs(adapter_id, output, display, hardware), "output": str(dest),
            "window": window,
            "screen": output.name, "monitor_index": str(output.index),
            "x": str(output.x), "y": str(output.y),
            "width": str(output.width), "height": str(output.height),
            "duration": str(chosen.length), "fps": str(rate(output)),
            "hwaccel": hwaccel(adapter_id, hardware),
            "audio_device": pipeline.sound_source(chosen.sound_source)}


def record(adapter_id: str, found: Mapping[str, tools.Found], output: Output,
           window: str, dest: Path, chosen: Settings, hardware: str) -> list[str]:
    """One screen's recorder, from the Record Command or VPinFE's own."""
    template = chosen.record_command or own_record(adapter_id, found, hardware)
    return expand(template, RECORD, record_values(adapter_id, found, output, window, dest,
                                                  chosen, hardware))


def encode_values(ffmpeg: Path, job: pipeline.Encode, dest: Path, window: str,
                  output: Output) -> dict[str, str | list[str]]:
    return {"ffmpeg": str(ffmpeg),
            "input": ["-ss", f"{job.skip:.3f}", "-t", f"{job.length:.3f}",
                      "-i", str(job.source)],
            "output": str(dest), "window": window, "screen": output.name,
            "width": str(output.width), "height": str(output.height),
            "duration": str(job.length), "fps": str(job.fps),
            "video_filters": ["-vf", pipeline.video_filters(job.turn, job.fps, job.cap)],
            "video_codec": pipeline.codec_args(job.codec, job.quality)}


def encode(template: str, ffmpeg: Path, job: pipeline.Encode, dest: Path, *,
           window: str, output: Output) -> list[str]:
    """One recording to its stored video, from the Encode Command or VPinFE's own."""
    return expand(template or OWN_ENCODE, ENCODE,
                  encode_values(ffmpeg, job, dest, window, output))


def own(adapter_id: str, found: Mapping[str, tools.Found], hardware: str) -> dict[str, str]:
    """VPinFE's two commands on this device, their tokens unexpanded."""
    return {RECORD: own_record(adapter_id, found, hardware), ENCODE: OWN_ENCODE}

