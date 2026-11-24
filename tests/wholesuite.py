"""One whole-suite run at a time, and a count of how many there have been."""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from datetime import date, datetime
from pathlib import Path

from platformdirs import user_cache_dir

CACHE = Path(user_cache_dir("vpinfe"))
LOCK = CACHE / "whole-suite.lock"
LOG = CACHE / "whole-suite-runs.jsonl"

# A holder that has not finished in this long is taken to have died without releasing.
STALE_AFTER_S = 3 * 3600


def _alive(pid: int) -> bool:
    if sys.platform == "win32":
        return True  # os.kill(pid, 0) ends the process there; only the age check applies
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _holder(lock: Path, now: float) -> dict | None:
    try:
        held = json.loads(lock.read_text(encoding="utf-8"))
        pid, started = int(held["pid"]), float(held["started"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if not _alive(pid) or now - started > STALE_AFTER_S:
        return None
    return held


def take(lock: Path = LOCK, now: float | None = None) -> str | None:
    """None once this process holds the lock; otherwise the sentence naming who does.

    The lock appears whole or not at all: it is linked into place from a file already
    written, so no other process reads it half-written. A folder that cannot be written
    takes no lock and refuses nothing.
    """
    now = time.time() if now is None else now
    mine = json.dumps({"pid": os.getpid(), "started": now, "from": str(Path.cwd())})
    try:
        lock.parent.mkdir(parents=True, exist_ok=True)
        draft = lock.with_name(f"{lock.name}.{os.getpid()}.{threading.get_ident()}")
        draft.write_text(mine, encoding="utf-8")
    except OSError:
        return None
    try:
        for _ in range(2):
            try:
                os.link(draft, lock)
            except FileExistsError:
                held = _holder(lock, now)
                if held is not None:
                    at = datetime.fromtimestamp(held["started"]).strftime("%H:%M")
                    return (f"A whole-suite run is already going (process {held['pid']}, "
                            f"started {at}, from {held.get('from', '?')}). Two at once are "
                            f"slower and flaky; wait for it, run part of the suite, or pass "
                            f"--overlap.")
                lock.unlink(missing_ok=True)
                continue
            except OSError:
                return None
            return None
        return "Could not take the whole-suite lock."
    finally:
        draft.unlink(missing_ok=True)


def release(lock: Path = LOCK) -> None:
    try:
        held = json.loads(lock.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if isinstance(held, dict) and held.get("pid") == os.getpid():
        lock.unlink(missing_ok=True)


def record(seconds: float, tests: int, ok: bool, log: Path = LOG, now: float | None = None,
           failed: list[str] | None = None, sandboxed: list[str] | None = None) -> None:
    """Append one run to the log. A log that cannot be written is skipped.

    `failed` names the modules that failed, errored or never finished; `sandboxed` the browser
    modules that skipped, which ran without a browser and so have not passed.
    """
    line = {"at": time.time() if now is None else now, "seconds": round(seconds, 1),
            "tests": tests, "ok": ok, "failed": failed or [], "sandboxed": sandboxed or []}
    try:
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(line) + "\n")
    except OSError:
        pass


def today(log: Path = LOG, day: date | None = None) -> tuple[int, float]:
    """(runs, seconds) logged on `day`, which is today's date unless given."""
    day = day or date.today()
    runs, seconds = 0, 0.0
    try:
        lines = log.read_text(encoding="utf-8").splitlines()
    except OSError:
        return 0, 0.0
    for text in lines:
        try:
            entry = json.loads(text)
            when = datetime.fromtimestamp(entry["at"]).date()
            took = float(entry["seconds"])
        except (ValueError, KeyError, TypeError):
            continue
        if when == day:
            runs += 1
            seconds += took
    return runs, seconds
