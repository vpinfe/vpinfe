"""What this machine is doing right now, and what it has been doing this session.

The live half only. Host, OS, build flavour and the browser a device runs are static
facts and live with the device that reports them - a reading and a fact answer different
questions, and putting both here would be one surface doing two jobs.

**History is a ring in memory and goes with the process.** It answers "is something
climbing?", which is the question a single number cannot, and it costs nothing. Anything
surviving a restart is a storage decision that has not been made, and inventing one here
would be answering it by accident.

`psutil` is optional. Where it is missing the readings say so rather than reporting zero:
a machine that cannot be measured and a machine that is idle are different, and a graph
of zeros is a lie that looks like data.
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
import threading
import time
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from common import events
from common.config import path_state
from common.failures import why
from common.host import graphics
from common.i18n import t

try:  # optional, and an install without it still runs
    import psutil
except Exception:  # noqa: BLE001 - any import failure means "not measurable"
    psutil = None

logger = logging.getLogger("vpinfe.common.host.metrics")

# Roughly an hour at the 2s cadence a page reads on, which is as far back as a question
# like "did that scan do this?" reaches. Bounded because it is memory, and unbounded
# history in a process that runs for weeks is a leak with a nice name.
HISTORY = 1800

# Tables launched, kept as long as the history is: a mark older than the graph it sits on
# has nothing to mark.
LAUNCHES = 200

# How many processes What's busy lists.
TOP = 5
# What VPinFE's own process is called, in place of the interpreter's name.
OWN = "VPinFE"
# A program's share is per core where the platform's own monitor says it that way (Activity
# Monitor, top and htop: 100 is one core, a busy program on ten cores can read 800) and of
# the whole machine where it does not (Task Manager).
PER_CORE = sys.platform != "win32"

# Where a processor's temperature is, best first: the chip and the labels to prefer on it.
# A label earlier in the tuple wins; a chip with no preferred label answers with its first
# reading.
_CPU_CHIPS = (("coretemp", ("package",)), ("k10temp", ("tctl", "tdie")),
              ("zenpower", ("tdie", "tctl")), ("cpu_thermal", ()), ("cpu-thermal", ()))
# The chips a platform names for its thermal zones, which stand for the processor when none
# of the above is there. Any other chip is a drive, a card or a radio.
_ZONES = ("acpitz", "thermal")


@dataclass
class _Ring:
    """One session's samples, oldest first."""

    points: deque = field(default_factory=lambda: deque(maxlen=HISTORY))

    def add(self, sample: dict[str, Any]) -> None:
        self.points.append(sample)

    def since(self, seconds: float = 0.0) -> list[dict[str, Any]]:
        if not seconds:
            return list(self.points)
        cutoff = time.time() - seconds
        return [one for one in self.points if one["at"] >= cutoff]


_ring = _Ring()
_launches: deque[dict[str, Any]] = deque(maxlen=LAUNCHES)
_registered = False
_launch_lock = threading.Lock()

# psutil answers `cpu_percent(interval=None)` with 0.0 when asked again within moments, so
# a caller close behind another is handed the sample already taken.
FRESH_FOR = 1.0
_lock = threading.Lock()
_last: dict[str, Any] | None = None
_last_paths: list[str] = []
_listed_processes = False


def measurable() -> tuple[bool, str]:
    """Whether this machine can be read at all, and why not when it cannot.

    Offered disabled with the reason rather than hidden: a page that simply omits the
    readings leaves somebody wondering whether the machine is fine or the page is broken.
    """
    if psutil is None:
        return False, t("error.metrics.psutil_missing")
    return True, ""


def read(paths: Iterable[str] = ()) -> dict[str, Any]:
    """One reading. Cheap enough to call on a timer.

    `cpu_percent` is asked without an interval, so it is the load since the previous
    call rather than a blocking sample - which is what makes this safe on a 2s timer and
    why the first reading after startup is not meaningful.
    """
    ok, reason = measurable()
    cards = graphics.read()
    sample: dict[str, Any] = {
        "at": time.time(),
        "measurable": ok,
        "reason": reason,
        "cpu_percent": None,
        "memory_total": None,
        "memory_used": None,
        "memory_percent": None,
        "load": None,
        "cpu_temperature": None,
        "busiest": None,
        "disks": _disks(paths or []),
        "graphics": cards,
    }
    if not ok:
        return sample

    sample["cpu_percent"] = psutil.cpu_percent(interval=None)
    memory = psutil.virtual_memory()
    sample["memory_total"] = memory.total
    sample["memory_used"] = memory.total - memory.available
    sample["memory_percent"] = memory.percent
    # Not on Windows, and not an error there - it is a number that platform does not
    # have rather than one we failed to read.
    try:
        sample["load"] = [round(one, 2) for one in os.getloadavg()]
    except (OSError, AttributeError):
        sample["load"] = None
    sample["cpu_temperature"] = cpu_temperature()
    sample["busiest"] = busiest()
    return sample


def cpu_temperature() -> float | None:
    """The processor's temperature in degrees C where this platform reports one, which is
    Linux. Left out elsewhere rather than guessed."""
    try:
        chips = psutil.sensors_temperatures()
        for chip, labels in _CPU_CHIPS:
            found = [one for one in chips.get(chip) or () if one.current is not None]
            if found:
                return float(next((one for label in labels for one in found
                                   if one.label.lower().startswith(label)), found[0]).current)
        return next((float(one.current) for chip, readings in chips.items()
                     if any(zone in chip for zone in _ZONES)
                     for one in readings if one.current is not None), None)
    except Exception:  # noqa: BLE001 - no sensors is not an error
        return None


def busiest() -> list[dict[str, Any]] | None:
    """The `TOP` programs using the most of the processor, each as `{name, cpu_percent,
    memory}`, where a program is an app bundle's every process (`Claude Helper (Renderer)`
    is Claude) or, with no bundle, every process of one name.

    `cpu_percent` follows `PER_CORE`. Nothing the first time, and after that whatever the OS
    lets this process read: a process owned by another user, root's on macOS, is skipped.
    `psutil.process_iter` keeps its `Process` objects between calls, which is what lets a
    process's `cpu_percent` measure since the last sample: a new one always reads 0.
    """
    global _listed_processes
    try:
        divisor = 1 if PER_CORE else (psutil.cpu_count() or 1)
        own = os.getpid()
        groups: dict[str, dict[str, Any]] = {}
        for one in psutil.process_iter(["pid", "name", "exe", "cpu_percent", "memory_info"]):
            info = one.info
            if not info["pid"] or info["cpu_percent"] is None or info["memory_info"] is None:
                continue
            name = OWN if info["pid"] == own else _program(info["exe"]) or info["name"] or ""
            row = groups.setdefault(name, {"name": name, "cpu_percent": 0.0, "memory": 0})
            row["cpu_percent"] += info["cpu_percent"] / divisor
            row["memory"] += info["memory_info"].rss
    except Exception:  # noqa: BLE001 - a listing the OS refuses is no list, not a failure
        logger.debug("Could not list the processes", exc_info=True)
        return None
    primed, _listed_processes = _listed_processes, True
    if not primed:
        return None
    return sorted(({**row, "cpu_percent": round(row["cpu_percent"], 1)}
                   for row in groups.values()),
                  key=lambda row: (-row["cpu_percent"], -row["memory"]))[:TOP]


def _program(exe: str | None) -> str:
    """The app a process belongs to, from the outermost `.app` in its path: "Claude" for
    `Claude.app/.../Claude Helper (Renderer).app/...`. Empty when it is in no bundle."""
    for part in (exe or "").replace("\\", "/").split("/"):
        if part.endswith(".app") and len(part) > 4:
            return part[:-4]
    return ""


def _disks(paths: Iterable[str]) -> list[dict[str, Any]]:
    """One row per *volume*, not per path.

    Two watched paths on one disk are one answer, and printing it twice says the same
    thing twice - which the rest of this project's UI rules call a badge on every row.
    Keyed on the device id the filesystem reports, so it is the same volume rather than
    the same prefix.
    """
    found: list[dict[str, Any]] = []
    seen: dict[Any, dict[str, Any]] = {}
    held = list(paths)
    # All asked at once, each bounded: `disk_usage` on a share that has gone blocks with
    # no limit of its own.
    answers = path_state.look_all([path_state.Ask(str(one)) for one in held])
    for path, answer in zip(held, answers, strict=True):
        entry = _disk(path, answer)
        key = entry.pop("device", None)
        if key is not None and key in seen:
            # Named on the row that is already there, so somebody looking for the
            # config directory finds it rather than concluding it is not watched.
            seen[key]["also"].append(entry["path"])
            continue
        entry["also"] = []
        found.append(entry)
        if key is not None:
            seen[key] = entry
    return found


def _disk(path: str, answer: path_state.PathState) -> dict[str, Any]:
    """One monitored path. Per path rather than per filesystem: a person watches the
    place their tables live, and which device that is on is not the question. Touches the
    path only where `answer`, the bounded look at it, found it there."""
    entry: dict[str, Any] = {"path": str(path), "total": None, "used": None,
                             "free": None, "percent": None, "error": "", "device": None}
    if not answer.reachable:
        entry["error"] = answer.reason or t("error.metrics.path_unreadable")
        return entry
    try:
        found = Path(path).expanduser()
        total, used, free = shutil.disk_usage(found)
        entry["device"] = found.stat().st_dev
    except Exception as exc:  # noqa: BLE001 - a share that has gone is the case this reports
        logger.debug("Cannot read %s: %s", path, exc)
        entry["error"] = why(exc, path)
        return entry
    entry.update({"total": total, "used": used, "free": free,
                  "percent": (used / total * 100) if total else 0.0})
    return entry


def sample(paths: Iterable[str] = ()) -> dict[str, Any]:
    """Read once and keep it. What a page calls on its timer.

    Under `FRESH_FOR` seconds after the last real reading the same reading comes back and
    nothing is added to the history. Only the volumes follow the caller's paths.
    """
    global _last, _last_paths
    asked = [str(one) for one in paths]
    with _lock:
        if _last is not None and 0 <= time.time() - _last["at"] < FRESH_FOR:
            found = dict(_last)
            if asked != _last_paths:
                found["disks"] = _disks(asked)
            return found
        found = read(asked)
        _last, _last_paths = found, asked
        if found["measurable"]:
            _ring.add({"at": found["at"], "cpu_percent": found["cpu_percent"],
                       "memory_percent": found["memory_percent"],
                       "gpus": {card["id"]: card["busy"] for card in found["graphics"]["cards"]
                                if "busy" in card}})
        return dict(found)


def history(seconds: float = 0.0) -> list[dict[str, Any]]:
    """What has been kept this session, oldest first."""
    with _lock:
        return _ring.since(seconds)


def register() -> None:
    """Remember the tables launched, for the history to mark. Idempotent."""
    global _registered
    if _registered:
        return
    events.subscribe(events.TABLE_LAUNCHED, on_launched)
    _registered = True


def on_launched(game: Any = None, private: bool = False, **_payload: Any) -> None:
    """A private game is marked without its name: the history is read by whoever has the
    Manager open, and Private is what keeps a game out of what others see."""
    name = ""
    if game is not None and not private:
        try:
            from common.games.game_metadata import game_title

            name = game_title(game)
        except Exception:  # noqa: BLE001 - an unnamed mark is better than a lost one
            logger.debug("Could not name the launched table", exc_info=True)
    with _launch_lock:
        _launches.append({"at": time.time(), "kind": "table", "name": name})


def marks(seconds: float = 0.0) -> list[dict[str, Any]]:
    """What happened this session worth setting beside a graph, oldest first: each job
    started and each table launched, as `{at, kind, name}`. A job's `name` is its kind,
    which the page puts in words, and it adds `title` where the job has one of its own; a
    table's `name` is the game's title, empty when it is private."""
    from common.runtime import jobs

    cutoff = time.time() - seconds if seconds else 0.0
    found = [{"at": one.started_at, "kind": "job", "name": one.kind,
              **({"title": one.title} if one.title else {})}
             for one in jobs.recent(jobs.HISTORY_LIMIT) if one.started_at >= cutoff]
    with _launch_lock:
        found += [dict(one) for one in _launches if one["at"] >= cutoff]
    return sorted(found, key=lambda one: one["at"])


def reset_for_tests() -> None:
    global _last, _last_paths, _listed_processes, _registered
    _ring.points.clear()
    with _launch_lock:
        _launches.clear()
    _last, _last_paths, _listed_processes = None, [], False
    _registered = False
