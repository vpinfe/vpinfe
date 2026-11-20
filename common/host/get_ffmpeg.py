"""Get FFmpeg: this device's pinned build, downloaded into VPinFE's own folder."""

from __future__ import annotations

import hashlib
import logging
import platform
import shutil
import tempfile
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from common import service_errors
from common.atomic_write import staged_for
from common.config.paths import CONFIG_DIR
from common.i18n import size, t
from common.runtime import jobs
from common.security import http_client

logger = logging.getLogger("vpinfe.common.host.get_ffmpeg")

FOLDER = CONFIG_DIR / "tools" / "ffmpeg"

DAMAGED = "error.get_ffmpeg.damaged"
NOT_HERE = "error.get_ffmpeg.not_here"
DOES_NOT_RUN = "error.get_ffmpeg.does_not_run"


@dataclass(frozen=True)
class Build:
    version: str
    url: str
    sha256: str
    size: int
    # The program's path inside the archive.
    member: str
    # Who publishes it, as a person would look them up.
    source: str


WINDOWS_X64 = Build(
    "9.0.2",
    "https://github.com/GyanD/codexffmpeg/releases/download/9.0.2/"
    "ffmpeg-9.0.2-essentials_build.7z",
    "4705843ccaaf54257c16ad90f3e952ece33c17df964ecf7bfdbb0f49c7171077", 35430500,
    "ffmpeg-9.0.2-essentials_build/bin/ffmpeg.exe", "gyan.dev")
MACOS_ARM64 = Build(
    "9.0.2",
    "https://ffmpeg.martin-riedl.de/download/macos/arm64/1789931890_9.0.2/ffmpeg.zip",
    "c8ed4c4e6978a03c485edbfe4e0a5dc2380f8a30bba5150531b31b094492d924", 28395699,
    "ffmpeg", "martin-riedl.de")
MACOS_X64 = Build(
    "9.0.2",
    "https://ffmpeg.martin-riedl.de/download/macos/amd64/1789931006_9.0.2/ffmpeg.zip",
    "7c6b4125b191cbf773832dc51f424cf2b6bb7da43007d1e066f95909e47cacd4", 33816391,
    "ffmpeg", "martin-riedl.de")


class GetFFmpegError(service_errors.ServiceError):
    """Nothing was kept, for the reason in the message."""


def build(system: str | None = None, machine: str | None = None) -> Build | None:
    """The build for this device, or None where there is none to get."""
    from . import tools

    system = tools.here() if system is None else system
    machine = (platform.machine() if machine is None else machine).lower()
    if system == tools.WINDOWS:
        return WINDOWS_X64
    if system == tools.DARWIN:
        return MACOS_ARM64 if machine in ("arm64", "aarch64") else MACOS_X64
    return None


def offer() -> dict[str, Any] | None:
    """What Get FFmpeg would fetch here, for a person to agree to."""
    chosen = build()
    if chosen is None:
        return None
    return {"version": chosen.version, "source": chosen.source, "size": chosen.size}


def program(chosen: Build) -> Path:
    return FOLDER / PurePosixPath(chosen.member).name


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as held:
        for block in iter(lambda: held.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _unpack(archive: Path, member: str, dest: Path) -> None:
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as held, held.open(member) as source, \
                dest.open("wb") as out:
            shutil.copyfileobj(source, out)
        return
    import py7zr

    with tempfile.TemporaryDirectory(dir=FOLDER) as work:
        with py7zr.SevenZipFile(archive) as held:
            held.extract(path=work, targets=[member])
        shutil.move(str(Path(work) / member), dest)


Fetch = Callable[..., None]


def get(progress: Callable[[int, int], None] | None = None, *,
        fetch: Fetch = http_client.download_file, chosen: Build | None = None) -> Path:
    """This device's build, downloaded, checked and unpacked, and run once to see that it
    works. Raises GetFFmpegError, UnavailableError where there is none for this device, and
    what the download raises."""
    chosen = build() if chosen is None else chosen
    if chosen is None:
        raise service_errors.UnavailableError(t(NOT_HERE))
    FOLDER.mkdir(parents=True, exist_ok=True)
    archive = FOLDER / f"download{PurePosixPath(chosen.url).suffix}"
    dest = program(chosen)
    try:
        fetch(chosen.url, archive, progress=(lambda done: progress(done, chosen.size))
              if progress is not None else None)
        if archive.stat().st_size != chosen.size or _sha256(archive) != chosen.sha256:
            logger.warning("Get FFmpeg: %s is not the file pinned for %s", chosen.url,
                           chosen.version)
            raise GetFFmpegError(t(DAMAGED))
        with staged_for(dest) as staged:
            _unpack(archive, chosen.member, staged)
            staged.chmod(0o755)
    finally:
        archive.unlink(missing_ok=True)
    from . import tools

    if not tools.probed(tools.FFMPEG, dest).works:
        dest.unlink(missing_ok=True)
        raise GetFFmpegError(t(DOES_NOT_RUN))
    logger.info("Get FFmpeg: %s from %s is at %s", chosen.version, chosen.source, dest)
    return dest


def start() -> jobs.Job:
    """Get FFmpeg as a job. Raises UnavailableError where there is nothing to get, and
    JobBusyError while it is being got already."""
    chosen = build()
    if chosen is None:
        raise service_errors.UnavailableError(t(NOT_HERE))

    def work(job: jobs.Job) -> dict[str, Any]:
        def said(done: int, total: int) -> None:
            job.progress(done, total, t("tools.get.progress", tool="FFmpeg",
                                        done=size(done), total=size(total)))

        dest = get(said, chosen=chosen)
        from common.capture import adapters

        adapters.warm()
        return {"path": str(dest), "version": chosen.version}

    return jobs.submit(jobs.KIND_TOOL_GET, work)
