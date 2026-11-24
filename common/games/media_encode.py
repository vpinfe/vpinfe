"""How one file is made into another: the pictures Pillow rewrites, the argv FFmpeg is given
for a sound or a video, and what FFmpeg says a file holds.

Nothing here decides what to convert or where the result goes; `media_convert` does, and
hands each file's staged path to these.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image

from common.games.media_files import SkipError
from common.host import tools
from common.i18n import t
from common.runtime import shutdown

logger = logging.getLogger("vpinfe.common.games.media_encode")

PNG = "png"
WEBP = "webp"
JPG = "jpg"
MP3 = "mp3"
OGG = "ogg"
MP4 = "mp4"
WEBM = "webm"
H264 = "h264"
VP9 = "vp9"
AV1 = "av1"
STANDARD = "standard"
HIGH = "high"
SMALL = "small"

PICTURE_EXTENSION = {PNG: ".png", WEBP: ".webp", JPG: ".jpg"}
SOUND_EXTENSION = {MP3: ".mp3", OGG: ".ogg"}
CONTAINER_EXTENSION = {MP4: ".mp4", WEBM: ".webm"}

# A JPEG by another name: renamed, not decoded and encoded again, when JPG is the target.
JPEG_NAMES = (".jfif", ".jpe")

# What each container takes without being made again.
CONTAINER_VIDEO = {MP4: (H264, VP9, AV1), WEBM: (VP9, AV1)}

# Encoders by codec, most wanted first.
VIDEO_ENCODERS = {H264: ("libx264",), VP9: ("libvpx-vp9",), AV1: ("libsvtav1", "libaom-av1")}
SOUND_ENCODERS = {MP3: ("libmp3lame",), OGG: ("libopus", "libvorbis")}
WEBM_SOUND_ENCODER = "libopus"
MP4_SOUND_ENCODER = "libmp3lame"

# A long clip at a slow setting takes hours.
_TIMEOUT = 4 * 3600
# A converted file's length may differ from the source's by the container's rounding.
_DURATION_SLACK = 1.0
# What a see-through picture is laid on when the target has no see-through areas.
_BACKDROP = (0, 0, 0)

_JPEG = {STANDARD: 90, HIGH: 95, SMALL: 80}
_WEBP = {STANDARD: 90, SMALL: 75}
_MP3_RATE = {STANDARD: "192k", HIGH: "256k", SMALL: "128k"}
_OPUS_RATE = {STANDARD: "128k", HIGH: "160k", SMALL: "96k"}
_VORBIS_Q = {STANDARD: "5", HIGH: "7", SMALL: "3"}
_H264 = {STANDARD: ("medium", "18"), HIGH: ("slow", "14"), SMALL: ("fast", "26")}
_VP9 = {STANDARD: ("4", "30"), HIGH: ("2", "24"), SMALL: ("4", "36")}
_SVTAV1 = {STANDARD: ("8", "32"), HIGH: ("6", "26"), SMALL: ("10", "38")}
_AOM = {STANDARD: ("6", "32"), HIGH: ("4", "26"), SMALL: ("8", "38")}
_AUDIO_FOR_COPY = {MP4: ("mp3", "opus"), WEBM: ("opus", "vorbis")}
# The 8-bit 4:2:0 every browser plays.
PLAYABLE_PIXELS = ("yuv420p", "yuvj420p")

_POSITION = re.compile(r"^out_time_us=(\d+)$", re.MULTILINE)
_DURATION = re.compile(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)")
_VIDEO = re.compile(r"Stream #\S+: Video: (\w+)(.*)")
_AUDIO = re.compile(r"Stream #\S+: Audio: (\w+)")
_PIXELS = re.compile(r"\b(yuvj?\d{3}p\w*|gray\w*|nv\d+\w*|rgb\w*|bgr\w*|gbr\w*|pal8)\b")


@dataclass(frozen=True)
class Streams:
    """What FFmpeg reports a file holds. A field is empty where the file has no such stream."""

    video: str = ""
    audio: str = ""
    pixels: str = ""
    seconds: float | None = None


def streams(ffmpeg: Path, path: Path) -> Streams:
    """FFmpeg's own account of a file, read off the listing it prints when given an input
    and no output."""
    try:
        done = subprocess.run([str(ffmpeg), "-hide_banner", "-nostdin", "-i", str(path)],
                              capture_output=True, text=True, errors="replace",
                              stdin=subprocess.DEVNULL, timeout=60,
                              creationflags=tools.NO_WINDOW)
    except (subprocess.TimeoutExpired, OSError):
        return Streams()
    text = done.stderr
    seconds = None
    if found := _DURATION.search(text):
        hours, minutes, secs = found.groups()
        seconds = int(hours) * 3600 + int(minutes) * 60 + float(secs)
    video = _VIDEO.search(text)
    audio = _AUDIO.search(text)
    pixels = ""
    if video and (match := _PIXELS.search(video.group(2))):
        pixels = match.group(1)
    return Streams(video.group(1) if video else "", audio.group(1) if audio else "",
                   pixels, seconds)


def encoder_for(ffmpeg_probe: tools.Probe | None, names: tuple[str, ...]) -> str:
    """The first of these FFmpeg can write with, or "" where it has none."""
    if ffmpeg_probe is None:
        return ""
    return next((name for name in names if ffmpeg_probe.has(tools.ENCODERS, name)), "")


# --- pictures ------------------------------------------------------------------------


def convert_picture(source: Path, staged: Path, target: str, quality: str) -> None:
    """The picture in `source` as `target` at `staged`, keeping its profile, resolution and
    see-through areas where the target keeps them."""
    try:
        with Image.open(source) as opened:
            opened.load()
            if getattr(opened, "is_animated", False) and getattr(opened, "n_frames", 1) > 1:
                raise SkipError(t("error.media_convert.frames"))
            _write(opened, staged, target, quality)
    except SkipError:
        raise
    except Exception as exc:  # noqa: BLE001 - an unreadable picture is one file's skip
        raise SkipError(t("error.media_convert.picture_unreadable")) from exc


def _write(opened: Image.Image, staged: Path, target: str, quality: str) -> None:
    options: dict[str, Any] = {}
    cmyk = opened.mode == "CMYK"
    if (profile := opened.info.get("icc_profile")) and not cmyk:
        options["icc_profile"] = profile
    if target != WEBP and (dpi := opened.info.get("dpi")):
        options["dpi"] = dpi
    picture = _eight_bit(opened)
    if target == JPG:
        if "A" in picture.getbands():
            backdrop = Image.new("RGB", picture.size, _BACKDROP)
            backdrop.paste(picture, mask=picture.getchannel("A"))
            picture = backdrop
        options.update(quality=_JPEG[quality], optimize=True)
        if quality == HIGH:
            options["subsampling"] = 0
        picture.save(staged, format="JPEG", **options)
    elif target == WEBP:
        if quality == HIGH:
            options.update(lossless=True, method=4)
        else:
            options.update(quality=_WEBP[quality], method=4)
        picture.save(staged, format="WEBP", **options)
    else:
        options["optimize"] = True
        picture.save(staged, format="PNG", **options)


def _eight_bit(picture: Image.Image) -> Image.Image:
    """8-bit RGB or RGBA from whatever the source was: a palette, 16 bits a channel, CMYK,
    grey with or without see-through."""
    mode = picture.mode
    if mode.startswith("I;16") or mode == "I":
        picture = picture.point(lambda value: value / 256).convert("L")
        mode = "L"
    if mode in ("RGBA", "LA", "La", "RGBa", "PA") or (
            mode == "P" and "transparency" in picture.info):
        return picture.convert("RGBA")
    if mode == "RGB":
        return picture
    return picture.convert("RGB")


def picture_size(path: Path) -> tuple[int, int]:
    try:
        with Image.open(path) as opened:
            return opened.size
    except Exception as exc:  # noqa: BLE001 - an unreadable picture is one file's skip
        raise SkipError(t("error.media_convert.picture_unreadable")) from exc


def is_jpeg(path: Path) -> bool:
    try:
        with Image.open(path) as opened:
            return opened.format == "JPEG"
    except Exception:  # noqa: BLE001 - not a picture Pillow opens
        return False


# --- FFmpeg --------------------------------------------------------------------------


def sound_argv(ffmpeg: Path, source: Path, staged: Path, target: str, quality: str,
               encoder: str) -> list[str]:
    codec = ["-c:a", encoder]
    if encoder == "libmp3lame":
        codec += ["-b:a", _MP3_RATE[quality]]
    elif encoder == "libopus":
        codec += ["-b:a", _OPUS_RATE[quality]]
    else:
        codec += ["-q:a", _VORBIS_Q[quality]]
    fmt = "mp3" if target == MP3 else "ogg"
    return [*_head(ffmpeg), "-i", str(source), "-vn", "-map", "0:a:0", "-map_metadata", "0",
            *codec, "-f", fmt, str(staged)]


def audio_can_be_kept(container: str, video: str, audio: str) -> bool:
    """Whether a sound goes into the container as it is: MP3 or Opus in an MP4, Opus or Vorbis
    in a WebM, and AAC beside H.264."""
    if audio in _AUDIO_FOR_COPY[container]:
        return True
    return container == MP4 and audio == "aac" and video == H264


def video_argv(ffmpeg: Path, source: Path, staged: Path, container: str, *, codec: str,
               encoder: str, quality: str, threads: int, keep_sound: bool,
               source_streams: Streams, repackage: bool) -> list[str]:
    """One video made into `container`: its picture copied where it is already a codec the
    container takes and plays, and encoded as `codec` otherwise; its sound copied, encoded
    for the container, or left out."""
    sound = keep_sound and bool(source_streams.audio)
    argv = [*_head(ffmpeg), "-i", str(source), "-map", "0:v:0"]
    if sound:
        argv += ["-map", "0:a:0"]
    if repackage:
        argv += ["-c:v", "copy"]
    else:
        argv += ["-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2", "-pix_fmt", "yuv420p",
                 *_video_codec(codec, encoder, quality, threads)]
    if not sound:
        argv += ["-an"]
    elif audio_can_be_kept(container, source_streams.video if repackage else codec,
                           source_streams.audio):
        argv += ["-c:a", "copy"]
    elif container == WEBM:
        argv += ["-c:a", WEBM_SOUND_ENCODER, "-b:a", _OPUS_RATE[STANDARD]]
    else:
        argv += ["-c:a", MP4_SOUND_ENCODER, "-b:a", _MP3_RATE[STANDARD]]
    if container == MP4:
        argv += ["-movflags", "+faststart"]
    return [*argv, "-f", container, str(staged)]


def _video_codec(codec: str, encoder: str, quality: str, threads: int) -> list[str]:
    if codec == H264:
        preset, crf = _H264[quality]
        return ["-c:v", encoder, "-preset", preset, "-crf", crf, "-threads", str(threads)]
    if codec == VP9:
        speed, crf = _VP9[quality]
        return ["-c:v", encoder, "-b:v", "0", "-crf", crf, "-deadline", "good", "-cpu-used",
                speed, "-row-mt", "1", "-threads", str(threads)]
    if encoder == "libsvtav1":
        preset, crf = _SVTAV1[quality]
        return ["-c:v", encoder, "-crf", crf, "-preset", preset,
                "-svtav1-params", f"lp={threads}"]
    speed, crf = _AOM[quality]
    return ["-c:v", encoder, "-b:v", "0", "-crf", crf, "-cpu-used", speed, "-row-mt", "1",
            "-threads", str(threads)]


def _head(ffmpeg: Path) -> list[str]:
    """Low priority, because the cabinet is also where tables are played."""
    argv = [str(ffmpeg), "-hide_banner", "-nostdin", "-loglevel", "error", "-y"]
    if os.name != "nt" and (nice := shutil.which("nice")):
        return [nice, "-n", "10", *argv]
    return argv


def threads_for() -> int:
    return max(1, (os.cpu_count() or 2) // 2)


def run(argv: list[str], *, seconds: float | None = None,
        on_progress: Callable[[float], None] | None = None,
        halted: Callable[[], bool] | None = None) -> None:
    """Run FFmpeg to the end, or until the app is asked to stop, and raise a skip with the
    reason where it did not finish. `on_progress` is told how far through the file it is, 0
    to 1, about once a second, where `seconds` (the file's length) is known. `halted` is asked
    each second and stops it the way the app being asked to stop does."""
    flags = tools.NO_WINDOW | getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)
    with tempfile.TemporaryDirectory(prefix="vpinfe-ffmpeg-") as scratch:
        log = Path(scratch) / "progress.txt"
        if on_progress and seconds:
            at = argv.index("-y") + 1
            argv = [*argv[:at], "-progress", str(log), *argv[at:]]
        try:
            proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.PIPE, text=True, errors="replace",
                                    creationflags=flags)
        except OSError as exc:
            raise SkipError(t("error.media_convert.no_ffmpeg")) from exc
        started = time.monotonic()
        stderr = ""
        while True:
            try:
                _, stderr = proc.communicate(timeout=1)
                break
            except subprocess.TimeoutExpired:
                if shutdown.requested() or (halted and halted()):
                    proc.kill()
                    proc.communicate()
                    raise SkipError(t("error.media_convert.stopped")) from None
                if time.monotonic() - started > _TIMEOUT:
                    proc.kill()
                    proc.communicate()
                    raise SkipError(t("error.media_convert.timed_out")) from None
                if on_progress and seconds:
                    _report(log, seconds, on_progress)
    if proc.returncode != 0:
        logger.warning("FFmpeg exited %s: %s", proc.returncode, (stderr or "").strip()[-2000:])
        if reason := _reason(stderr):
            raise SkipError(t("error.media_convert.ffmpeg_failed", reason=reason))
        raise SkipError(t("error.media_convert.ffmpeg_failed_bare"))


def _report(log: Path, seconds: float, on_progress: Callable[[float], None]) -> None:
    """The last position FFmpeg wrote to its progress file, as a fraction of `seconds`."""
    try:
        text = log.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    found = _POSITION.findall(text)
    if found:
        on_progress(max(0.0, min(1.0, int(found[-1]) / 1_000_000 / seconds)))


def _reason(stderr: str) -> str:
    """FFmpeg's last line, unless it names a file: a path is not a reason a person can use."""
    lines = (stderr or "").strip().splitlines()
    last = lines[-1].strip() if lines else ""
    return "" if "/" in last or "\\" in last else last


def check_length(ffmpeg: Path, source: Streams, staged: Path) -> None:
    """The converted file holds something and is as long as the one it was made from."""
    if not staged.is_file() or staged.stat().st_size == 0:
        raise SkipError(t("error.media_convert.empty"))
    made = streams(ffmpeg, staged)
    if not (made.video or made.audio):
        raise SkipError(t("error.media_convert.unreadable"))
    if (source.seconds is not None and made.seconds is not None
            and abs(source.seconds - made.seconds) > _DURATION_SLACK):
        raise SkipError(t("error.media_convert.length"))
