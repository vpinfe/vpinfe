"""From a recording to the stored file: one FFmpeg pipeline for every platform.

Each function answers an argv; `run` runs one.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from common.host import tools

from . import settings
from .geometry import Turn

TIMEOUT = 300

CRF = {(settings.H264, settings.STANDARD): 34, (settings.H264, settings.HIGH): 23,
       (settings.VP9, settings.STANDARD): 60, (settings.VP9, settings.HIGH): 41}

# Peak loudness below which a recording has nothing to hear.
SILENT_DB = -60.0
# The most a frame's levels may spread in any plane, of 255, and it still be one color.
FLAT_SPREAD = 4
# Frames a second read of the whole recording, once its key frames were all one color.
CONFIRM_PER_SECOND = 2

# PulseAudio's name, which PipeWire's stand-in keeps, for the default output's monitor.
DEFAULT_MONITOR = "@DEFAULT_MONITOR@"

Run = Callable[..., Any]


def _quiet(ffmpeg: Path) -> list[str]:
    return [str(ffmpeg), "-hide_banner", "-nostdin", "-loglevel", "error", "-y"]


def _window(skip: float, length: float) -> list[str]:
    return ["-ss", f"{skip:.3f}", "-t", f"{length:.3f}"]


def scale(cap: int | None) -> str:
    """To `cap` on the long side and never up, or the source's own size; always even, as
    4:2:0 needs."""
    if not cap:
        return "scale=trunc(iw/2)*2:trunc(ih/2)*2"
    return (f"scale='if(gte(iw,ih),min({cap},iw),-2)'"
            f":'if(gte(iw,ih),-2,min({cap},ih))'")


def cap_of(size: str) -> int | None:
    return None if size == settings.SCREENS_OWN else int(size)


def video_filters(turn: Turn, fps: int, cap: int | None) -> str:
    return ",".join([*turn.filters, f"fps={fps}", scale(cap), "format=yuv420p"])


def codec_args(codec: str, quality: str) -> list[str]:
    crf = str(CRF[(codec, quality)])
    if codec == settings.VP9:
        return ["-c:v", "libvpx-vp9", "-deadline", "realtime", "-cpu-used", "8",
                "-crf", crf, "-b:v", "0", "-row-mt", "1"]
    return ["-c:v", "libx264", "-preset", "veryfast", "-crf", crf]


@dataclass(frozen=True)
class Encode:
    """One recorded window to its stored video. `skip` is cut from the head so every
    window starts at the same moment."""

    source: Path
    skip: float
    length: float
    turn: Turn
    fps: int
    cap: int | None
    codec: str
    quality: str


def picture(ffmpeg: Path, source: Path, turn: Turn, cap: int | None, dest: Path,
            at: float | None = None) -> list[str]:
    """One frame, `at` seconds into a recording, or the whole of a still where None."""
    seek = ["-ss", f"{at:.3f}"] if at is not None else []
    return [*_quiet(ffmpeg), *seek, "-i", str(source), "-frames:v", "1", "-update", "1",
            "-vf", ",".join([*turn.filters, scale(cap)]), str(dest)]


def sound_source(chosen: str) -> str:
    """Sound From as PulseAudio names it: a source's name, or Automatic for the default
    output's monitor."""
    return DEFAULT_MONITOR if chosen in ("", settings.AUTO) else chosen


def sound(ffmpeg: Path, chosen: str, dest: Path) -> list[str]:
    """What the device plays, from PulseAudio or PipeWire's stand-in, until stopped."""
    return [*_quiet(ffmpeg), "-f", "pulse", "-i", sound_source(chosen), "-ac", "2",
            "-ar", "48000", "-c:a", "pcm_s16le", str(dest)]


def mp3(ffmpeg: Path, source: Path, skip: float, length: float, dest: Path) -> list[str]:
    return [*_quiet(ffmpeg), *_window(skip, length), "-i", str(source), "-vn",
            "-c:a", "libmp3lame", "-b:a", "192k", str(dest)]


def loudness(ffmpeg: Path, source: Path) -> list[str]:
    return [str(ffmpeg), "-hide_banner", "-nostdin", "-i", str(source),
            "-af", "volumedetect", "-f", "null", "-"]


def frames(ffmpeg: Path, source: Path) -> list[str]:
    """Counts by copying the stream to nowhere, which decodes nothing."""
    return [str(ffmpeg), "-hide_banner", "-nostdin", "-loglevel", "error",
            "-progress", "pipe:1", "-i", str(source), "-map", "0:v:0", "-c", "copy",
            "-f", "null", "-"]


def describe(ffmpeg: Path, source: Path) -> list[str]:
    """FFmpeg reading a file and writing nothing, which says what is in it and exits 1."""
    return [str(ffmpeg), "-hide_banner", "-nostdin", "-i", str(source)]


def levels(ffmpeg: Path, source: Path, per_second: int = 0) -> list[str]:
    """Each frame's levels, printed to the log: the key frames alone, which decodes a
    handful, or with `per_second`, that many frames a second of the whole."""
    only = [] if per_second else ["-skip_frame", "nokey"]
    sample = [f"fps={per_second}"] if per_second else []
    return [str(ffmpeg), "-hide_banner", "-nostdin", "-nostats", *only, "-i", str(source),
            "-map", "0:v:0", "-vf", ",".join([*sample, "signalstats", "metadata=mode=print"]),
            "-f", "null", "-"]


_FRAME = re.compile(r"^frame=\s*(\d+)", re.MULTILINE)
_PEAK = re.compile(r"max_volume:\s*(-?[\d.]+|-inf) dB")
_VIDEO = re.compile(r"Stream #\S+.*?: Video: (.*)")
_SIZE = re.compile(r"(?<![\w.])(\d{2,5})x(\d{2,5})(?![\w.])")
_RATE = re.compile(r"([\d.]+) fps")
_PRINTED = re.compile(r"\bframe:\d+")
_LEVEL = re.compile(r"lavfi\.signalstats\.([YUV])(MIN|MAX)=(\d+)")


def flat_in(said: str) -> bool:
    """Whether every frame `levels` printed is one color. False where it printed none."""
    spreads = []
    for frame in _PRINTED.split(said or "")[1:]:
        found = {plane + end: int(value) for plane, end, value in _LEVEL.findall(frame)}
        if len(found) == 6:
            spreads.append(max(found[f"{plane}MAX"] - found[f"{plane}MIN"]
                               for plane in "YUV"))
    return bool(spreads) and max(spreads) <= FLAT_SPREAD


def frames_in(said: str) -> int:
    counts = _FRAME.findall(said or "")
    return int(counts[-1]) if counts else 0


def video_in(said: str) -> tuple[tuple[int, int] | None, float | None]:
    """The first video stream's size and rate, from what `describe` printed."""
    stream = _VIDEO.search(said or "")
    if not stream:
        return None, None
    size = _SIZE.search(stream.group(1))
    rate = _RATE.search(stream.group(1))
    return ((int(size.group(1)), int(size.group(2))) if size else None,
            float(rate.group(1)) if rate else None)


def peak_in(said: str) -> float:
    found = _PEAK.search(said or "")
    if not found or found.group(1) == "-inf":
        return float("-inf")
    return float(found.group(1))


def run(argv: list[str], runner: Run = subprocess.run) -> Any:
    """Raises CalledProcessError where FFmpeg fails, TimeoutExpired where it hangs."""
    return runner(argv, capture_output=True, text=True, errors="replace",
                  stdin=subprocess.DEVNULL, timeout=TIMEOUT, check=True,
                  creationflags=tools.NO_WINDOW)
