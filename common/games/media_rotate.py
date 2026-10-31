"""Turning pictures and videos in place, a quarter or a half, as one job.

A file is turned at its own path. The new file is staged beside it and moved over only when
whole, so a file that cannot be turned is skipped with its reason and left as it was, and a
copy taken first is taken only for a turn that is about to happen.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Any

from PIL import Image, ImageOps

from common import atomic_write, service_errors
from common.backups import folder as backup_folder
from common.backups import media as backup_media
from common.capture.geometry import Turn
from common.failures import why
from common.games import game_lens, media_ops, media_placement
from common.games.media_files import SkipError, Target, inside, stamp
from common.host import tools
from common.i18n import t
from common.media.media_specs import IMAGE_FAMILY, VIDEO_FAMILY
from common.runtime import jobs, shutdown
from common.runtime.jobs import JobReporter

logger = logging.getLogger("vpinfe.common.games.media_rotate")

LEFT = "left"
RIGHT = "right"
AROUND = "around"

# Counter-clockwise degrees, which is how Pillow and `Turn` both count.
_CCW = {LEFT: 90, RIGHT: 270, AROUND: 180}
TURNS = tuple(_CCW)

_TRANSPOSE = {90: Image.Transpose.ROTATE_90, 180: Image.Transpose.ROTATE_180,
              270: Image.Transpose.ROTATE_270}

# A long clip on a small machine takes minutes.
_VIDEO_TIMEOUT = 3600

_JPEG_QUALITY = 95


def start(targets: list[Target], turn: str, keep_copies: bool) -> jobs.Job:
    """Turn these files. Refused before anything is turned when there is nowhere to keep
    the copies; a file that cannot be turned is skipped and said in the result."""
    if turn not in _CCW:
        raise service_errors.RefusedError(t("error.media_rotate.turn"),
                                          details={"known": list(TURNS)})
    if not targets:
        raise service_errors.RefusedError(t("error.media_rotate.nothing"))
    targets = list(dict.fromkeys(targets))
    folder = backup_folder.for_writing() if keep_copies else None

    def work(job: jobs.Job) -> dict[str, Any]:
        return _turn_all(targets, _CCW[turn], folder, job.reporter())

    return jobs.submit(jobs.KIND_MEDIA_ROTATE, work)


def _turn_all(targets: list[Target], ccw: int, folder: Path | None,
              reporter: JobReporter) -> dict[str, Any]:
    ffmpeg = _FfmpegOnce()
    turned = kept = 0
    skipped: list[dict[str, str]] = []
    touched: dict[str, Path] = {}
    for index, target in enumerate(targets):
        if shutdown.requested():
            break
        reporter.progress(index, len(targets), t("said.media_rotate",
                                                 file=PurePosixPath(target.path).name))
        try:
            game_dir, took = _turn_one(target, ccw, folder, ffmpeg)
        except Exception as exc:  # noqa: BLE001 - one file is skipped, the rest go on
            if not isinstance(exc, (SkipError, service_errors.ServiceError)):
                logger.warning("Turning %s of game %s failed", target.path, target.game_id,
                               exc_info=True)
            skipped.append({"game_id": target.game_id, "path": target.path,
                            "reason": why(exc)})
            continue
        turned += 1
        kept += took
        touched[target.game_id] = game_dir
    for game_dir in touched.values():
        try:
            media_ops.reread(game_dir)
        except Exception:  # noqa: BLE001 - the files are turned; the result still says so
            logger.warning("Reading %s again after a turn failed", game_dir, exc_info=True)
    logger.info("Turned %s file(s) across %s game(s), %s copied first, %s skipped",
                turned, len(touched), kept, len(skipped))
    return {"turned": turned, "games": len(touched), "kept": kept, "skipped": skipped}


def _turn_one(target: Target, ccw: int, folder: Path | None,
              ffmpeg: Callable[[], Path]) -> tuple[Path, int]:
    """One file turned, and the copy of it taken first, as (its game folder, 0 or 1)."""
    game = game_lens.game_for_writing(target.game_id)
    game_dir = Path(str(game.full_path_game))
    source = inside(game_dir, target.path)
    suffix = source.suffix.lower()
    if suffix not in IMAGE_FAMILY and suffix not in VIDEO_FAMILY:
        raise SkipError(t("error.media_rotate.not_media"))
    before = stamp(source)
    with atomic_write.staged_for(source) as staged:
        if suffix in IMAGE_FAMILY:
            _turn_image(source, staged, ccw)
        else:
            _turn_video(source, staged, ccw, ffmpeg())
        if stamp(source) != before:
            raise SkipError(t("error.media_rotate.changed"))
        if folder is not None:
            backup_media.keep(folder, target.game_id, source, target.path)
        shutil.copymode(source, staged)
    media_placement.record_origin(game_dir, source)
    return game_dir, int(folder is not None)


def _turn_image(source: Path, staged: Path, ccw: int) -> None:
    with Image.open(source) as opened:
        if getattr(opened, "is_animated", False):
            raise SkipError(t("error.media_rotate.animated"))
        made = opened.format
        options: dict[str, Any] = {key: opened.info[key]
                                   for key in ("icc_profile", "dpi", "transparency")
                                   if key in opened.info}
        if made == "JPEG":
            options["quality"] = _JPEG_QUALITY
        elif made == "WEBP":
            options.update(quality=100, method=4)
        # A sideways picture with an orientation tag is shown upright, so the turn is of
        # what is seen, and the tag goes with the pixels it described.
        upright = ImageOps.exif_transpose(opened)
        if made in ("JPEG", "WEBP") and (exif := upright.getexif()):
            options["exif"] = exif.tobytes()
        upright.transpose(_TRANSPOSE[ccw]).save(staged, format=made, **options)


def _turn_video(source: Path, staged: Path, ccw: int, ffmpeg: Path) -> None:
    """The sound is copied as it is; the picture is encoded again, which a turn needs. A WebM
    stays WebM, in VP9, since H.264 does not go in one."""
    if source.suffix.lower() == ".webm":
        codec = ["-c:v", "libvpx-vp9", "-crf", "24", "-b:v", "0", "-row-mt", "1"]
        container = ["-f", "webm"]
    else:
        codec = ["-c:v", "libx264", "-preset", "medium", "-crf", "18"]
        container = ["-movflags", "+faststart", "-f", "mp4"]
    argv = [str(ffmpeg), "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
            "-i", str(source), "-map", "0:v:0", "-map", "0:a?",
            "-vf", ",".join(Turn(ccw=ccw).filters), *codec, "-pix_fmt", "yuv420p",
            "-c:a", "copy", *container, str(staged)]
    try:
        subprocess.run(argv, capture_output=True, text=True, errors="replace",
                       stdin=subprocess.DEVNULL, timeout=_VIDEO_TIMEOUT, check=True,
                       creationflags=tools.NO_WINDOW)
    except subprocess.CalledProcessError as exc:
        last = (exc.stderr or "").strip().splitlines()
        raise SkipError(t("error.media_rotate.video_failed",
                          reason=last[-1] if last else "")) from exc
    except subprocess.TimeoutExpired as exc:
        raise SkipError(t("error.media_rotate.video_timed_out")) from exc


class _FfmpegOnce:
    """FFmpeg found at the first video and not looked for again, whether it was found or not."""

    def __init__(self) -> None:
        self._found: tools.Found | None = None

    def __call__(self) -> Path:
        if self._found is None:
            self._found = tools.resolve(tools.FFMPEG)
        if self._found.state != tools.State.FOUND or self._found.path is None:
            raise SkipError(t("error.media_rotate.no_ffmpeg"))
        return self._found.path

