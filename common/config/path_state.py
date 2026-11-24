"""What the disk says about a path, asked one way for everything that names one.

A setting's executable, a library folder, a folder somebody added to import from: each is
a path that is perfectly well-formed and may still be gone, on a share that is not mounted,
on a drive that went read-only, or on a server that is not answering. Each asks the same
question, so each gets the same answers in the same words.

Asked of the disk when somebody looks and never stored. A stored answer is wrong the
moment a mount drops, and a mount dropping is the ordinary case this exists to report.

Answered on the machine the path belongs to. One install cannot stat another's disk.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from common import mounts, service_errors
from common.config import config_schema
from common.failures import why
from common.i18n import t
from common.launcher_path import resolve_launcher_path

# What a path is wanted as. These are `config_schema.PATH_KINDS`.
DIR = "dir"
FILE = "file"
EXE = "exe"

# What the answer can be. `unset` is not a failure: most paths are optional, and a blank
# one means "use the default", which is a choice rather than a mistake.
UNSET = "unset"
OK = "ok"
# There, and not writable. Only asked of a path wanted writable.
READ_ONLY = "read_only"
# On a share the OS does not have mounted.
NOT_CONNECTED = "not_connected"
MISSING = "missing"
WRONG_KIND = "wrong_kind"
# Its own state rather than "missing": the path is right and the file is there, which is a
# permissions problem and not a typo, and they are fixed differently.
NOT_EXECUTABLE = "not_executable"
NOT_ANSWERING = "not_answering"

# How long a caller waits for the disk before calling a path Not answering.
PROBE_SECONDS = 2.0
# How long the scan and a launch wait for a folder.
MOUNT_SECONDS = 30.0

_WANTED = {
    FILE: "error.path.expected_file",
    EXE: "error.path.expected_program",
}


def canonical(path: str) -> str:
    """One spelling per place on disk, so a folder reached directly and through a parent
    root is recognized as the same one. Symlinks resolved, case left alone: a
    case-insensitive filesystem is not a reason to rewrite what somebody typed."""
    wanted = str(path or "").strip()
    if not wanted:
        return ""
    return os.path.normpath(os.path.realpath(os.path.expanduser(wanted)))


@dataclass(frozen=True)
class PathState:
    """What the disk says about one path right now."""

    state: str
    # Written for the person who typed it, so it says what was found rather than what was
    # expected: "that is a folder" is the useful half when the field wanted a file, because
    # the path is right there to compare against. Empty when there is nothing to say.
    reason: str = ""
    # The share it is on, or None on this device or when it did not answer.
    origin: mounts.Origin | None = None

    @property
    def reachable(self) -> bool:
        return self.state in (OK, READ_ONLY)

    @property
    def writable(self) -> bool:
        """Only a look that asked for a writable path can say READ_ONLY; for any other the
        answer is the same as `state == OK`."""
        return self.state == OK


@dataclass(frozen=True)
class Ask:
    """One path and what it is wanted as."""

    path: str
    kind: str = DIR
    writable: bool = False
    # The share it was last seen on, for naming it when it is not mounted now.
    recorded: mounts.Origin | None = None

    @property
    def key(self) -> tuple[str, str, bool]:
        return self.path, self.kind, self.writable


def _look(ask: Ask) -> PathState:
    wanted = str(resolve_launcher_path(ask.path)) if ask.kind == EXE else ask.path
    # Resolved the way `canonical` resolves it. Reading the raw string would call `~/tables`
    # missing while the scan happily walked it.
    path = Path(canonical(wanted) or wanted)
    # The path is touched before the mount table is read: touching an automounted share is
    # what mounts it.
    there = path.exists()
    where = mounts.where(str(path), ask.recorded)
    origin = where.origin
    if origin is not None and not where.connected:
        return PathState(NOT_CONNECTED, t("error.path.not_connected"), origin)
    if not there:
        return PathState(MISSING, t("error.path.nothing_there"), origin)
    if ask.kind == DIR:
        if not path.is_dir():
            return PathState(WRONG_KIND, t("error.path.expected_folder"), origin)
    else:
        if path.is_dir():
            return PathState(WRONG_KIND, t(_WANTED[ask.kind]), origin)
        if ask.kind == EXE and not os.access(path, os.X_OK):
            return PathState(NOT_EXECUTABLE, t("error.path.not_executable"), origin)
    if ask.writable and not os.access(path, os.W_OK):
        return PathState(READ_ONLY, t("error.path.unwritable"), origin)
    return PathState(OK, origin=origin)


class _Probe:
    def __init__(self, ask: Ask) -> None:
        self.ask = ask
        self.started = time.monotonic()
        self.done = threading.Event()
        self.answer = PathState(NOT_ANSWERING, t("error.path.not_answering"))

    def run(self) -> None:
        try:
            self.answer = _look(self.ask)
        except (OSError, ValueError) as exc:
            self.answer = PathState(MISSING, why(exc, self.ask.path))
        finally:
            with _PROBES_LOCK:
                if _PROBES.get(self.ask.key) is self:
                    del _PROBES[self.ask.key]
            self.done.set()


_PROBES: dict[tuple[str, str, bool], _Probe] = {}
_PROBES_LOCK = threading.Lock()


def _probe(ask: Ask) -> _Probe:
    """The question in flight for this path, or a new one. Nothing cancels a stat on a
    dead mount, so a second question would be a second thread lost to it."""
    with _PROBES_LOCK:
        probe = _PROBES.get(ask.key)
        if probe is None:
            probe = _PROBES[ask.key] = _Probe(ask)
            threading.Thread(target=probe.run, daemon=True, name="path-probe").start()
    return probe


def look_all(asks: Iterable[Ask], wait: float | None = None) -> list[PathState]:
    """What the disk says about each path, asked of all of them at once, in the order given.

    `wait` counts from when a question was first asked, not from this call, so a question
    already out that long answers Not answering at once.
    """
    limit = PROBE_SECONDS if wait is None else wait
    held = list(asks)
    probes = [None if not one.path.strip() or one.kind not in config_schema.PATH_KINDS
              else _probe(one) for one in held]
    answers = []
    for probe in probes:
        if probe is None:
            answers.append(PathState(UNSET))
            continue
        probe.done.wait(max(0.0, probe.started + limit - time.monotonic()))
        answers.append(probe.answer)
    return answers


def look(path: str, kind: str = DIR, *, writable: bool = False,
         wait: float | None = None) -> PathState:
    return look_all([Ask(str(path or ""), kind, writable)], wait)[0]


def refusal(name: str, state: PathState) -> str:
    """Why a path cannot be used as asked, or "" when it can."""
    if state.state == OK:
        return ""
    if state.state == READ_ONLY:
        return t("error.path.read_only", name=name)
    if state.state == UNSET:
        return t("error.path.not_set", name=name)
    return t("error.path.not_reachable", name=name, reason=state.reason)


def require(path: str, kind: str = DIR, *, writable: bool = False, name: str = "",
            wait: float | None = None) -> PathState:
    """The state of a path a caller is about to use, or a refusal naming why it cannot be.

    **Asked before the work, not after it fails.** A write to a dropped share ends in an
    errno nobody can act on; this ends in "its share is not mounted on this device".
    Raises `BlockedError`: the request is fine and something is in the way.
    """
    state = look(path, kind, writable=writable, wait=wait)
    said = refusal(name or path, state)
    if said:
        raise service_errors.BlockedError(said)
    return state


def path_options() -> tuple:
    """Every setting that names something on disk, in schema order. A `list` option is
    left out for the same reason `check_option` refuses one: its `path` says what its
    entries are, not what the setting itself is."""
    return tuple(option for option in config_schema.CONFIG_OPTIONS
                 if option.path and option.type != "list" and not option.internal)


@dataclass(frozen=True)
class ItemPath:
    """A path held by one item of a list, and what it is wanted as."""

    list_key: str
    id: str
    field: str
    kind: str
    value: str


def item_paths(lists: Mapping[str, Sequence[Mapping[str, Any]]]) -> tuple[ItemPath, ...]:
    """Every path an item of these lists holds, in list order. A field of a variant's kind
    is read from the item's `settings`; a kind this install lacks has none to check."""
    found: list[ItemPath] = []
    for key, items in lists.items():
        spec = config_schema.config_list(key)
        if spec is None:
            continue
        for item in items:
            if not isinstance(item, Mapping):
                continue
            where = [(item, field) for field in spec.fields]
            if spec.variant:
                kind = str(item.get(spec.variant.field) or "")
                given = item.get(config_schema.VARIANT_SETTINGS)
                if not isinstance(given, Mapping):
                    given = {}
                where += [(given, field) for field in spec.variant.fields(kind) or ()]
            found += [ItemPath(key, str(item.get("id") or ""), field.key, field.path,
                               str(held.get(field.key) or ""))
                      for held, field in where if field.path]
    return tuple(found)
