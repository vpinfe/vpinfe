"""Keeping the DOF config current from the DOF Config Tool, and knowing when it is due.

The download is `ledcontrol_pull.py`, run in a child process. A press forces it; a
scheduled run lets the script ask the version first.
"""

from __future__ import annotations

import hashlib
import logging
import os
import subprocess
import sys
import threading
from pathlib import Path

from common import service_errors, timestamps
from common.config.config_access import cfg_get
from common.config.config_store import ConfigStore
from common.config.paths import CACHE_DIR
from common.host import dof_config, dof_service
from common.i18n import t
from common.online import cache_stamps
from common.online.vpsdb_sync import EVERY, NEVER
from common.runtime import jobs

logger = logging.getLogger("vpinfe.common.online.dof_sync")

SECTION = "dof"
STAMPS = "dof_config"
SCRIPT = "ledcontrol_pull.py"
_FROZEN_FLAG = "--dof-pull"
_KEY_FLAG = "-A"
_TARGET_FLAG = "-T"
_FORCE_FLAG = "--force"
_ZIP_FLAG = "-F"
# The tool answers in seconds; two minutes is a connection that is not going to.
_TIMEOUT = 120.0
_WAKE_SECONDS = 60 * 60

# One run at a time, whether a person pressed it or the schedule came round.
_running = threading.Lock()


class DofConfigError(RuntimeError):
    """A download that did not work; the message is the line a person reads."""


def schedule(config: ConfigStore) -> str:
    return (cfg_get(config, SECTION, "download_config", "weekly") or "weekly").strip().lower()


def key(config: ConfigStore) -> str:
    return (cfg_get(config, SECTION, "config_tool_api_key", "") or "").strip()


def checked_at() -> str:
    """When the tool was last asked, ISO 8601 UTC, or "" if it never has been."""
    return cache_stamps.get(STAMPS, "checked")


def state(config: ConfigStore) -> dict:
    """What a page needs to say how fresh the config is: never the key itself."""
    return {"schedule": schedule(config), "checked": checked_at(), "due": due(config),
            "has_key": bool(key(config))}


def _fingerprint(api_key: str) -> str:
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:16]


def due(config: ConfigStore, now: float | None = None) -> bool:
    """Whether a check is owed. Never checked counts as due, and so does a key other than
    the one last tried, so pasting a key fetches the config on the next tick; no key, or
    `never`, is never due."""
    wanted = schedule(config)
    api_key = key(config)
    if wanted == NEVER or wanted not in EVERY or not api_key:
        return False
    tried = cache_stamps.get(STAMPS, "key")
    if tried and tried != _fingerprint(api_key):
        return True
    was = timestamps.iso_to_epoch(checked_at())
    if was is None:
        return True
    moment = timestamps.iso_to_epoch(timestamps.utc_now_iso()) if now is None else now
    return (moment or 0) - was >= EVERY[wanted].total_seconds()


def stamp(api_key: str) -> None:
    """Written whether or not the tool answered, so one that cannot be reached is not
    asked again on every wake. It records which key was tried."""
    cache_stamps.put(STAMPS, checked=timestamps.utc_now_iso(), key=_fingerprint(api_key))


def _hash(folder: Path) -> str:
    """What the folder holds: every file's name and bytes, in a fixed order."""
    digest = hashlib.sha256()
    if folder.is_dir():
        for path in sorted(p for p in folder.rglob("*") if p.is_file()):
            digest.update(path.relative_to(folder).as_posix().encode("utf-8"))
            digest.update(b"\0")
            try:
                digest.update(path.read_bytes())
            except OSError:
                digest.update(b"?")
    return digest.hexdigest()


def _launcher(script: Path) -> list[str]:
    """What runs the script: this interpreter, or the frozen build re-entering itself."""
    if getattr(sys, "frozen", False):
        return [sys.executable, _FROZEN_FLAG, str(script)]
    return [sys.executable, str(script)]


def _reason(output: str) -> str:
    """The failure in words, from the lines the script prints (`** Failed ...`)."""
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    starred = [line.lstrip("* ").strip() for line in lines if line.startswith("**")]
    line = (starred or lines or [""])[-1]
    text = "\n".join(starred or lines).lower()
    if any(word in text for word in ("could not create destination", "permissionerror",
                                     "read-only", "no space left")):
        words = t("said.dof_config.folder")
    elif any(word in text for word in ("http 401", "not a valid zip")):
        words = t("said.dof_config.refused")
    elif any(word in text for word in ("failed to retrieve online version", "failed download",
                                       "connectionerror", "timeout")):
        words = t("said.dof_config.unreached")
    else:
        words = t("said.dof_config.failed")
    return t("said.dof_config.because", reason=words, detail=line) if line else words


def run(config: ConfigStore, *, force: bool) -> dict:
    """Run the download. Answers `{changed, at}`; raises `DofConfigError` in words.

    `changed` is what the folder held before against after, not the script's word for it:
    a forced run says it updated when it wrote the same files again.
    """
    api_key, script, target = _ready(config)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    command = [*_launcher(script), _KEY_FLAG, api_key, _TARGET_FLAG, str(target),
               _ZIP_FLAG, str(CACHE_DIR / "dof_config.zip")]
    if force:
        command.append(_FORCE_FLAG)
    with _running:
        before = _hash(target)
        try:
            done = subprocess.run(
                command, capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=_TIMEOUT, cwd=script.parent,
                env={**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"})
        except subprocess.TimeoutExpired:
            stamp(api_key)
            raise DofConfigError(t("said.dof_config.unreached")) from None
        except OSError as exc:
            stamp(api_key)
            logger.warning("The DOF config downloader would not start", exc_info=True)
            raise DofConfigError(t("said.dof_config.failed")) from exc
        stamp(api_key)
        output = f"{done.stdout}\n{done.stderr}".replace(api_key, "***")
        logger.info("DOF config pull exited %s: %s", done.returncode,
                    " | ".join(line.strip() for line in output.splitlines() if line.strip()))
        if done.returncode != 0:
            raise DofConfigError(_reason(output))
        return {"changed": _hash(target) != before, "at": checked_at()}


def _ready(config: ConfigStore) -> tuple[str, Path, Path]:
    """What a download needs, or the refusal in words: the key, the script, the folder."""
    api_key = key(config)
    if not api_key:
        raise service_errors.RefusedError(t("error.dof_config.no_key"))
    script = dof_service.find_dof_file(SCRIPT)
    if script is None:
        raise service_errors.UnavailableError(t("error.dof_config.not_installed"))
    target = dof_config.config_folder()
    if target is None:
        raise service_errors.UnavailableError(t("error.dof_config.no_folder"))
    return api_key, script, target


def start(config: ConfigStore) -> jobs.Job:
    """A press: download now, whatever the version says. Refused in words before a job
    exists when there is no key, no script or no folder; BlockedError while one runs."""
    _ready(config)

    def work(job: jobs.Job) -> dict:
        job.progress(0, 1, t("said.dof_config.download"))
        done = run(config, force=True)
        if done["changed"]:
            try:
                dof_service.reload_config(config)
            except Exception:  # noqa: BLE001 - the files are in; the helper reads them next start
                logger.warning("DOF config downloaded, but the helper did not restart",
                               exc_info=True)
        return done

    return jobs.submit(jobs.KIND_DOF_CONFIG, work)


def start_watch(config: ConfigStore, shutdown: threading.Event | None = None) -> threading.Thread:
    """Check for newer config whenever one is due, for as long as this process runs.

    Silent: nothing waits on it, so a failure is logged and the next wake tries again.
    """
    stop = shutdown or threading.Event()

    def loop() -> None:
        while not stop.is_set():
            try:
                if due(config) and not jobs.active(jobs.KIND_DOF_CONFIG):
                    if run(config, force=False)["changed"]:
                        logger.info("DOF config updated from the DOF Config Tool")
            except service_errors.UnavailableError:
                pass
            except Exception:
                logger.warning("The DOF config could not be checked; will try again",
                               exc_info=True)
            stop.wait(_WAKE_SECONDS)

    thread = threading.Thread(target=loop, name="dof-sync", daemon=True)
    thread.start()
    return thread
