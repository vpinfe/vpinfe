"""A few seconds of the playfield through the Record and Encode Commands, launching
nothing, so a command that does not work fails in the settings page."""

from __future__ import annotations

import base64
import shlex
import shutil
import subprocess
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from common import service_errors
from common.config.paths import CAPTURE_DIR
from common.host import launch_state, tools
from common.i18n import t
from common.runtime import jobs

from . import adapters, commands, geometry, pipeline, preflight, run, session, settings
from .adapters import Recording

SECONDS = 3.0
# Where in the stored file the picture is cut, and its long side: enough to see which way
# up the playfield is.
PICTURE_AT = 1.0
PICTURE_SIDE = 480

WORK = CAPTURE_DIR / "test"

RECORDED_NOTHING = "capture.test.recorded_nothing"
WROTE_NOTHING = "capture.test.wrote_nothing"


def _last_line(said: str | bytes | None) -> str:
    text = said.decode(errors="replace") if isinstance(said, bytes) else str(said or "")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1] if lines else ""


def _failed(step: str, key: str, detail: str, record: list[str],
            encode: list[str], seconds: float) -> dict[str, Any]:
    return {"ok": False, "step": step, "reason": {"key": key, "params": {}},
            "detail": detail, "size": None, "fps": None, "frames": 0,
            "seconds": seconds, "picture": "", "record": shlex.join(record),
            "encode": shlex.join(encode)}


def test(overrides: Mapping[str, Any] | None = None, *, kit: session.Kit | None = None,
         seconds: float | None = None,
         report: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """What the commands make of the playfield. A command that records or writes nothing
    is an answer, not an error; a device that cannot record at all raises as a run
    does."""
    report = preflight.report() if report is None else report
    if not report["available"]:
        raise service_errors.UnavailableError(preflight.words(report["reason"]))
    if launch_state.current().launching:
        raise service_errors.BlockedError(t("error.launch.already_launching"))
    chosen = settings.read(None, overrides)
    device = run.reach(report)
    output = device.screens.get(adapters.PLAYFIELD)
    if output is None:
        playfield = next((one for one in report["screens"]
                          if one["window"] == adapters.PLAYFIELD), None)
        blocked = (playfield or {}).get("video", {}).get("reason") or report["reason"]
        raise service_errors.UnavailableError(preflight.words(blocked))
    with jobs.track(jobs.KIND_MEDIA_CAPTURE) as job:
        job.progress(0, 1, t("capture.progress.testing"))
        shutil.rmtree(WORK, ignore_errors=True)
        WORK.mkdir(parents=True)
        reached = device.adapter.begin({adapters.PLAYFIELD: output})[adapters.PLAYFIELD]
        try:
            if reached.output is None:
                raise service_errors.UnavailableError(preflight.words({
                    "key": reached.reason,
                    "params": {"window": adapters.PLAYFIELD, **reached.params}}))
            return _test(device, device.adapter.lend(reached.output), chosen,
                         kit or session.Kit(), SECONDS if seconds is None else seconds)
        finally:
            device.adapter.end()
            shutil.rmtree(WORK, ignore_errors=True)


def _test(device: run.Device, output: adapters.Output, chosen: settings.Settings,
          kit: session.Kit, seconds: float) -> dict[str, Any]:
    ffmpeg = Path(str(device.found[tools.FFMPEG.id].path))
    hardware = device.adapter.hardware(device.found[tools.FFMPEG.id])
    recorded, encoded = WORK / "playfield.mkv", WORK / "playfield.mp4"
    record = commands.record(device.adapter.id, device.found, output, adapters.PLAYFIELD,
                             recorded, chosen, hardware)
    turn = session.playfield_turn(device.config, device.adapter.recording_turn(output),
                                  chosen.playfield_orientation)
    job = pipeline.Encode(recorded, 0.0, seconds, turn, chosen.fps,
                          pipeline.cap_of(chosen.size),
                          settings.video_codec(chosen.video_codec), chosen.quality)
    encode = commands.encode(chosen.encode_command, ffmpeg, job, encoded,
                             window=adapters.PLAYFIELD, output=output)

    said = WORK / "record.log"
    with said.open("wb") as log:
        process = adapters.spawn(kit.popen, record, output, stdout=log, stderr=log)
        threading.Event().wait(seconds)
        Recording(adapters.PLAYFIELD, recorded, process, kit.clock()).stop()
    if not _frames(ffmpeg, recorded, kit):
        return _failed(commands.RECORD, RECORDED_NOTHING,
                       _last_line(said.read_bytes()), record, encode, seconds)

    try:
        pipeline.run(encode, kit.runner)
    except subprocess.CalledProcessError as exc:
        return _failed(commands.ENCODE, WROTE_NOTHING, _last_line(exc.stderr), record,
                       encode, seconds)
    except (OSError, subprocess.SubprocessError):
        return _failed(commands.ENCODE, WROTE_NOTHING, "", record, encode, seconds)
    if not encoded.is_file() or not encoded.stat().st_size:
        return _failed(commands.ENCODE, WROTE_NOTHING, "", record, encode, seconds)

    described = kit.runner(pipeline.describe(ffmpeg, encoded), capture_output=True,
                           text=True, errors="replace", stdin=subprocess.DEVNULL,
                           timeout=tools.TIMEOUT, check=False,
                           creationflags=tools.NO_WINDOW)
    size, fps = pipeline.video_in(described.stderr)
    return {"ok": True, "step": None, "reason": None, "detail": "",
            "size": list(size) if size else None, "fps": fps,
            "frames": _frames(ffmpeg, encoded, kit), "seconds": seconds,
            "picture": _picture(ffmpeg, encoded, kit),
            "record": shlex.join(record), "encode": shlex.join(encode)}


def _frames(ffmpeg: Path, path: Path, kit: session.Kit) -> int:
    if not path.is_file():
        return 0
    try:
        return pipeline.frames_in(pipeline.run(pipeline.frames(ffmpeg, path),
                                               kit.runner).stdout)
    except (OSError, subprocess.SubprocessError):
        return 0


def _picture(ffmpeg: Path, encoded: Path, kit: session.Kit) -> str:
    """A small JPEG of the stored file as a data URL, or "" where none could be cut."""
    dest = WORK / "picture.jpg"
    try:
        pipeline.run(pipeline.picture(ffmpeg, encoded, geometry.NONE, PICTURE_SIDE, dest,
                                      at=PICTURE_AT), kit.runner)
        data = dest.read_bytes()
    except (OSError, subprocess.SubprocessError):
        return ""
    return "data:image/jpeg;base64," + base64.b64encode(data).decode("ascii")
