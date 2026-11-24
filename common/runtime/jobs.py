"""Slow work as a job: start it, watch it on the bus, ask about it later.

A job is the answer to "this takes minutes and someone wants to watch". The event
shape is the one common/events.py documents, and it does not vary with who started
the work - a scan begun in the Manager looks the same on the stream as one begun
over the API, because both come through here.

Two entry points, one registry behind them. `submit` runs the work on its own thread
and hands back immediately, which is what an HTTP caller needs. `track` wraps work
already running on the caller's thread, which is what a caller with its own callbacks
needs: it keeps them and its own return value, and gains the event stream and the busy
check for free.

Kinds are exclusive. Two library scans at once would interleave writes to the same
.info files, so the second caller is refused rather than queued - a queue would mean
a user who clicked twice waits through two full scans.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from common import events, service_errors
from common.atomic_write import write_atomic
from common.config import paths
from common.failures import why
from common.i18n import t

logger = logging.getLogger("vpinfe.common.runtime.jobs")

ProgressCallback = Callable[[int, int, str], None]
LogCallback = Callable[[str], None]

RUNNING = "running"
DONE = "done"
FAILED = "failed"

# Well-known kinds. Named here because both the Manager and the API start this
# work and the exclusivity rule only means anything if they agree on the name.
KIND_LIBRARY_SCAN = "library.scan"
# Its own kind, not the scan's: this only reads, so it may run beside anything.
KIND_VPS_ROLLUP = "library.vps_rollup"
# Putting games on a device that is not an install. One at a time by design: the far end
# is a phone with one small web server, and two transfers into it would interleave
# chunks of different files.
KIND_DEVICE_SEND = "device.send"
# Filling a new game's empty media slots from the online sources. One at a time: two
# fills of the same game would both see the slot empty and both write it.
KIND_MEDIA_FILL = "library.media_fill"
# The daily comparison of the pictures placed with the ones a source now publishes, when it
# only looks. Its own kind so that a clean look is never taken for the outcome of a fill.
KIND_MEDIA_LOOK = "library.media_look"
# Recording the cabinet's screens into media slots.
KIND_MEDIA_CAPTURE = "media.capture"
# Turning pictures and videos in place. One at a time: two turns of one file would each
# read it as it was and the second would undo or double the first.
KIND_MEDIA_ROTATE = "media.rotate"
# Converting pictures, sounds and videos to formats a device plays. One at a time, and not
# beside anything else that writes media files: a file filled, recorded or turned while it is
# converted would be read as it was and overwritten by the result.
KIND_MEDIA_CONVERT = "media.convert"
# Bringing an upload into the library, where it converts files first. One at a time: two
# imports into one folder would each plan against a folder the other is writing.
KIND_UPLOAD_IMPORT = "upload.import"
# Carrying backups into a new backups folder. One at a time: two moves of the same folder
# would each find a file still there and the second would fail on it.
KIND_BACKUPS_MOVE = "backups.move"
# Downloading a Tool VPinFE can get itself, into its own folder.
KIND_TOOL_GET = "tools.get"
# One set of values written into each of several tables' settings. One at a time whichever
# the tables: a second write reaching a table the first has not yet written would be
# overwritten by it.
KIND_TABLE_SETTINGS = "table_settings"
# A selection's ratings, tags and other marks, written into each game's record. One at a
# time: two writes to one game's .info would each read it as it was.
KIND_MARKS = "marks"
# Getting the VPS catalog because there is none on disk, ahead of a read that has games to
# match. The watch's later refreshes are not this: they stay silent.
KIND_SPREADSHEET_DOWNLOAD = "spreadsheet.download"
# Downloading the cabinet's DOF config. One at a time: two downloads write the same folder.
KIND_DOF_CONFIG = "dof.config"
# Bringing each table's own draw order to what its backglass file places, after a change at
# the launcher. One at a time: two passes would write the same table's settings together.
KIND_BACKGLASS_ORDER = "backglass.order"

# Finished jobs kept since startup, for the Jobs view. Memory only: a restart empties it,
# apart from the ones that asked to keep their result (`Job.keep_result`).
HISTORY_LIMIT = 500
# How many of those stay on disk. Each is a row per game for an import of a whole library,
# so the oldest go first.
KEPT_LIMIT = 20
_LISTING = 20


_BUSY = {KIND_LIBRARY_SCAN: "error.jobs.library_busy",
         KIND_VPS_ROLLUP: "error.jobs.vps_compare_busy",
         KIND_DEVICE_SEND: "error.jobs.sending_busy",
         KIND_MEDIA_FILL: "error.media_fill.busy",
         KIND_MEDIA_LOOK: "error.media_look.busy",
         KIND_MEDIA_CAPTURE: "error.capture.busy",
         KIND_MEDIA_ROTATE: "error.media_rotate.busy",
         KIND_MEDIA_CONVERT: "error.media_convert.busy",
         KIND_UPLOAD_IMPORT: "error.uploads.import_busy",
         KIND_BACKUPS_MOVE: "error.backups.move_busy",
         KIND_TOOL_GET: "error.jobs.getting_tool",
         KIND_TABLE_SETTINGS: "error.jobs.settings_busy",
         KIND_MARKS: "error.jobs.marks_busy",
         KIND_SPREADSHEET_DOWNLOAD: "error.jobs.getting_spreadsheet",
         KIND_DOF_CONFIG: "error.jobs.getting_dof_config",
         KIND_BACKGLASS_ORDER: "error.jobs.order_busy"}

# Kinds refused while another kind runs, and what the refusal says.
_BLOCKED_BY = {(KIND_MEDIA_CAPTURE, KIND_MEDIA_FILL): "error.capture.fill_running",
               (KIND_MEDIA_FILL, KIND_MEDIA_CAPTURE): "error.media_fill.recording",
               (KIND_MEDIA_CONVERT, KIND_MEDIA_FILL): "error.media_convert.blocked",
               (KIND_MEDIA_CONVERT, KIND_MEDIA_CAPTURE): "error.media_convert.blocked",
               (KIND_MEDIA_CONVERT, KIND_MEDIA_ROTATE): "error.media_convert.blocked",
               (KIND_MEDIA_FILL, KIND_MEDIA_CONVERT): "error.media_convert.running",
               (KIND_MEDIA_CAPTURE, KIND_MEDIA_CONVERT): "error.media_convert.running",
               (KIND_MEDIA_ROTATE, KIND_MEDIA_CONVERT): "error.media_convert.running"}


class JobBusyError(service_errors.BlockedError):
    """Work of this kind is already running."""


def _busy(kind: str) -> JobBusyError:
    return JobBusyError(t(_BUSY.get(kind, "error.jobs.busy")))


def _refuse_if_busy(kind: str) -> None:
    """Under `_lock`."""
    if kind in _active:
        raise _busy(kind)
    for (wanted, running), key in _BLOCKED_BY.items():
        if wanted == kind and running in _active:
            raise JobBusyError(t(key))


@dataclass
class JobReporter:
    """The reporting surface a long operation writes to.

    Predates the registry and is still what services accept, so a service needs no
    knowledge of jobs to be run as one.
    """

    logger: logging.Logger
    progress_cb: ProgressCallback | None = None
    log_cb: LogCallback | None = None

    def log(self, message: str) -> None:
        self.logger.info(message)
        if self.log_cb:
            self.log_cb(message)

    def progress(self, current: int, total: int, message: str) -> None:
        if not self.progress_cb:
            return
        try:
            self.progress_cb(current, total, message)
        except Exception:
            self.logger.debug("Progress callback failed", exc_info=True)


@dataclass
class Job:
    """One run of one kind of work.

    `progress`/`log` both publish and chain: whatever callbacks the starter passed
    still fire, so an existing caller keeps its own progress bar while the bus gets
    the same information.
    """

    id: str
    kind: str
    # What this run is called where its kind is shared by several operations, as the person
    # reads it. Empty: the kind's own name.
    title: str = ""
    state: str = RUNNING
    pct: int = 0
    message: str = ""
    error: str | None = None
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    # What the work answered. Some jobs are their own outcome - a library scan leaves a
    # scanned library - and some are not: an import's answer is which games were imported
    # and which did not, and without somewhere to put it every such job has to invent a
    # back channel of its own.
    result: object | None = None
    # Whether whoever started it offers a way to stop it part way, and where that is
    # asked for. Stopping sets `halt`, which the work looks at between its units.
    stoppable: bool = False
    stop_link: str = ""
    halt: threading.Event = field(default_factory=threading.Event)
    # Set by the work when it ended early without failing. The job still finishes `done`:
    # `state` has three values and a consumer branches on `failed`.
    stopped: bool = False
    # How many of its parts failed. The job still finishes `done` with the
    # rest; a consumer that says how it went reads this beside `state`.
    problems: int = 0
    # Whether the result is written to disk when it finishes, so it can be opened after a
    # restart and from a page that did not start the work. Set by whoever starts the job;
    # `kept` is whether that has happened.
    keep_result: bool = False
    kept: bool = False
    # One line on what it did, counts mostly. Written when the work finishes, by the
    # describer registered for its kind or by the work itself.
    detail: str = ""
    # What each named part of the work is doing, for work a caller watches part by part:
    # `{"state": "waiting" | "working" | "done", "pct": 0-100, ...}` per part, in order.
    parts: list[dict[str, object]] = field(default_factory=list)
    # The extension that started it and the tier of the request that did, so the API shows
    # it only to callers who reach that tier. Empty `extension` is core's own work.
    extension: str = ""
    tier: str = ""
    progress_cb: ProgressCallback | None = None
    log_cb: LogCallback | None = None

    def progress(self, current: int, total: int, message: str) -> None:
        self.pct = int(100 * current / total) if total else 0
        self.message = message
        if self.progress_cb:
            try:
                self.progress_cb(current, total, message)
            except Exception:
                logger.debug("Progress callback failed", exc_info=True)
        events.emit(events.JOB_PROGRESS, job_id=self.id, pct=self.pct, message=message)

    def log(self, message: str) -> None:
        logger.info(message)
        self.heard(message)

    def heard(self, message: str) -> None:
        """A line handed to whoever started the job, and not logged: for a service whose own
        reporter has logged it, which `log` would repeat."""
        if self.log_cb:
            try:
                self.log_cb(message)
            except Exception:
                logger.debug("Log callback failed", exc_info=True)

    def reporter(self) -> JobReporter:
        """This job as the surface services already accept. The reporter logs a line
        itself, so it hands the job only the listener's half."""
        return JobReporter(logger, progress_cb=self.progress, log_cb=self.heard)

    def snapshot(self, with_result: bool = False) -> dict:
        """What this job is doing. The result is asked for rather than always sent: it
        can be a row per game, and a list of twenty jobs carrying twenty of those would
        make watching the queue expensive."""
        found = {
            "id": self.id,
            "kind": self.kind,
            "title": self.title,
            "state": self.state,
            "pct": self.pct,
            "message": self.message,
            "error": self.error,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "stoppable": self.stoppable,
            "stopped": self.stopped,
            "problems": self.problems,
            "report": self.kept,
            "detail": self.detail,
            "parts": [dict(part) for part in self.parts],
        }
        if with_result:
            found["result"] = self.result
        return found


_lock = threading.RLock()
_active: dict[str, Job] = {}
_history: list[Job] = []
_loaded = False


def _detail(job: Job) -> str:
    """What the work wrote, else its result put into words. A line that cannot be written
    is no line: it must not fail a job that finished."""
    if job.detail or job.result is None:
        return job.detail
    from common.runtime import job_details  # imports this module for the kinds

    try:
        return job_details.of(job.kind, job.result)
    except Exception:  # noqa: BLE001
        logger.debug("Describing %s failed", job.kind, exc_info=True)
        return ""


def _finish(job: Job, error: BaseException | None) -> None:
    # The work is over, so nothing else writes these fields; the disk is touched outside the
    # lock, which every reader of the registry waits on.
    finished_at = time.time()
    detail = _detail(job) if error is None else job.detail
    kept = error is None and job.keep_result and _keep(job, finished_at, detail)
    with _lock:
        if _active.get(job.kind) is job:
            del _active[job.kind]
        job.finished_at = finished_at
        if error is None:
            job.detail = detail
            # Set before the state turns, so nobody sees a finished job whose report is not
            # yet there.
            job.kept = kept
            job.state = DONE
            job.pct = 100
        else:
            job.state = FAILED
            job.error = why(error)
        _history.append(job)
        del _history[:-HISTORY_LIMIT]
    if error is None:
        events.emit(events.JOB_DONE, job_id=job.id)
    else:
        events.emit(events.JOB_FAILED, job_id=job.id, error=job.error)


def _kept_path(job_id: str) -> Path:
    return paths.JOBS_DIR / f"{job_id}.json"


def _keep(job: Job, finished_at: float, detail: str) -> bool:
    """Write a finished job's result to disk, and say whether it is there. A result that
    cannot be written is no result: it must not fail a job that finished."""
    try:
        paths.JOBS_DIR.mkdir(parents=True, exist_ok=True)
        write_atomic(_kept_path(job.id),
                     lambda handle: json.dump({**job.snapshot(with_result=True),
                                                  "state": DONE, "pct": 100,
                                                  "finished_at": finished_at,
                                                  "detail": detail,
                                                  "extension": job.extension,
                                                  "tier": job.tier}, handle))
    except (OSError, TypeError, ValueError):
        logger.warning("Could not keep the result of job %s", job.id, exc_info=True)
        return False
    try:
        stale = sorted(paths.JOBS_DIR.glob("*.json"), key=lambda one: one.stat().st_mtime)
        for old in stale[:-KEPT_LIMIT]:
            old.unlink(missing_ok=True)
    except OSError:
        logger.debug("Could not prune the kept jobs", exc_info=True)
    return True


def _load_kept() -> None:
    """Under `_lock`. The kept results of earlier runs, as finished jobs in the history,
    read once. A file that cannot be read is left where it is."""
    global _loaded
    if _loaded:
        return
    _loaded = True
    found: list[Job] = []
    for path in paths.JOBS_DIR.glob("*.json") if paths.JOBS_DIR.is_dir() else ():
        try:
            said = json.loads(path.read_text(encoding="utf-8"))
            found.append(Job(
                id=str(said["id"]), kind=str(said["kind"]),
                title=str(said.get("title") or ""), state=DONE,
                pct=100, message=str(said.get("message") or ""),
                started_at=float(said["started_at"]),
                finished_at=float(said["finished_at"]),
                result=said.get("result"), stopped=bool(said.get("stopped")),
                problems=int(said.get("problems") or 0),
                detail=str(said.get("detail") or ""), keep_result=True, kept=True,
                extension=str(said.get("extension") or ""),
                tier=str(said.get("tier") or "")))
        except (OSError, ValueError, KeyError, TypeError):
            logger.warning("Could not read the kept job %s", path.name, exc_info=True)
    known = {one.id for one in _history}
    found = [one for one in found if one.id not in known]
    _history[:0] = sorted(found, key=lambda one: one.finished_at or 0.0)
    del _history[:-HISTORY_LIMIT]


@contextmanager
def track(kind: str, *, progress_cb: ProgressCallback | None = None,
          log_cb: LogCallback | None = None, title: str = "") -> Iterator[Job]:
    """Run work on the caller's thread as a job. Raises JobBusyError if one is running."""
    job = Job(id=uuid.uuid4().hex, kind=kind, title=title, progress_cb=progress_cb,
              log_cb=log_cb)
    with _lock:
        _refuse_if_busy(kind)
        _active[kind] = job
    try:
        yield job
    except BaseException as exc:
        _finish(job, exc)
        raise
    else:
        _finish(job, None)


def submit(kind: str, work: Callable[[Job], object], *,
           progress_cb: ProgressCallback | None = None,
           log_cb: LogCallback | None = None, stoppable: bool = False,
           stop_link: str = "", keep_result: bool = False, title: str = "",
           extension: str = "", tier: str = "") -> Job:
    """Start work on its own thread and return its job immediately.

    With `keep_result` the result is written to disk when the work finishes, and the job
    can be opened from the Jobs view after the page that started it has gone, and after a
    restart."""
    job = Job(id=uuid.uuid4().hex, kind=kind, title=title, progress_cb=progress_cb,
              log_cb=log_cb, stoppable=stoppable or bool(stop_link), stop_link=stop_link,
              keep_result=keep_result, extension=extension, tier=tier)
    with _lock:
        _refuse_if_busy(kind)
        _active[kind] = job

    def _run() -> None:
        try:
            job.result = work(job)
        except BaseException as exc:  # noqa: BLE001 - the thread's job is to record it
            logger.exception("Job %s (%s) failed", job.id, job.kind)
            _finish(job, exc)
        else:
            _finish(job, None)

    threading.Thread(target=_run, daemon=True, name=f"job-{kind}").start()
    return job


def get(job_id: str) -> Job | None:
    with _lock:
        _load_kept()
        for job in _active.values():
            if job.id == job_id:
                return job
        for job in reversed(_history):
            if job.id == job_id:
                return job
    return None


def active(kind: str | None = None) -> list[Job]:
    with _lock:
        jobs = list(_active.values())
    return [job for job in jobs if kind is None or job.kind == kind]


def recent(limit: int = _LISTING) -> list[Job]:
    """Running jobs first, then the most recently finished."""
    with _lock:
        _load_kept()
        return list(_active.values()) + list(reversed(_history))[:limit]


def reset_for_tests() -> None:
    global _loaded
    with _lock:
        _active.clear()
        _history.clear()
        # Not read: a test that wants the kept results of an earlier run sets this itself,
        # and one that does not must not be handed the machine's.
        _loaded = True
