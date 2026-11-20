"""Getting the keyboard to a table that has started, and keeping it there.

A table that does not hold the keyboard pauses itself, so the window the person is
looking at has to be the one that has it. The program this module is told about starts
the table and goes on running; it watches the table's windows, asks the system who has
the keyboard, and hands it to the table's playfield when something else has it.

Each system has one backend of three calls (list the windows, say who has the keyboard,
give a window the keyboard); everything else is here. A system with no backend is
watched for nothing and logged once.
"""

from __future__ import annotations

import logging
import os
import platform
import sys
import threading
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import psutil

from common.config.config_access import cfg_bool
from common.failures import why

logger = logging.getLogger("vpinfe.common.host.front")

SECTION = "behavior"
KEY = "experimental_table_focus"

FIND_SECONDS = 30.0
POLL_SECONDS = 0.25
SETTLE_SECONDS = 0.15
GAPS = (0.3, 0.6, 1.2, 2.4)
TRIES = len(GAPS) + 1
# How long a walk of the process tree is believed: the tree changes when a program
# starts a window of its own, not four times a second.
TREE_SECONDS = 1.0
LATE_POLL_SECONDS = 1.0
# On a system whose foreground can be wrong: how long after the table says it is up a
# pause is believed to be the table's own, how long the keyboard stays on VPinFE's window
# when it is passed through it, and how long to wait for the table to say it resumed.
STUCK_SECONDS = 1.5
BOUNCE_SECONDS = 0.5
RESUME_SECONDS = 2.5


@dataclass(frozen=True)
class Window:
    """A top-level window as the system describes it. `handle` is the system's own."""

    handle: int | str
    pid: int
    title: str


class Backend(Protocol):
    """What one system answers. A call that cannot be answered raises, and says why."""

    name: str

    def windows(self) -> list[Window]:
        """The visible top-level windows."""
        ...

    def holder(self) -> Window | None:
        """The window that has the keyboard, or None where no window has it. The desktop
        and the taskbar are not windows of a program, so they answer None."""
        ...

    def hand_over(self, window: Window) -> str:
        """Ask for the keyboard to go to `window`. The system's own error text where it
        refused, "" where it did not."""
        ...


def on_wayland(env: Mapping[str, str]) -> bool:
    return env.get("XDG_SESSION_TYPE") == "wayland" or bool(env.get("WAYLAND_DISPLAY"))


def enabled(ini_config: Any) -> bool:
    """Whether the person turned hand-over on. Read at each launch."""
    return cfg_bool(ini_config, SECTION, KEY, False)


def backend_for_this_system() -> Backend | None:
    if sys.platform == "win32":
        from common.host.front_windows import WindowsBackend
        return WindowsBackend()
    if sys.platform == "darwin":
        from common.host.front_macos import MacBackend
        return MacBackend()
    if sys.platform.startswith("linux"):
        from common.host import front_linux
        try:
            return front_linux.backend_for()
        except Exception as exc:  # noqa: BLE001 - a desktop that cannot be asked is no backend, not a failed launch
            logger.info("Table focus: the window backend did not start: %s", why(exc))
    return None


def _no_backend_text() -> str:
    if sys.platform.startswith("linux"):
        from common.host import front_linux
        return front_linux.why_not()
    return "this system has no window backend yet, so nothing is watched"


def describe_system() -> str:
    """The system and, on Linux, the display system the person's session runs."""
    parts = [platform.system() or sys.platform, platform.release()]
    if sys.platform == "darwin":
        parts[1] = platform.mac_ver()[0] or parts[1]
    elif sys.platform.startswith("linux"):
        session = os.environ.get("XDG_SESSION_TYPE") or (
            "wayland" if os.environ.get("WAYLAND_DISPLAY") else
            "x11" if os.environ.get("DISPLAY") else "no display")
        parts.append(f"{session}, {os.environ.get('XDG_CURRENT_DESKTOP') or 'unnamed desktop'}")
    return " ".join(part for part in parts if part)


def _tree_of(pid: int) -> set[int]:
    """The process and everything it started."""
    try:
        process = psutil.Process(pid)
        return {pid, *(child.pid for child in process.children(recursive=True))}
    except psutil.Error:
        return {pid}


def _program_of(pid: int) -> str:
    try:
        return psutil.Process(pid).name()
    except psutil.Error:
        return f"process {pid}"


def _started_after(pid: int, moment: float) -> bool:
    try:
        return psutil.Process(pid).create_time() >= moment
    except psutil.Error:
        return False


class Watch:
    """The keyboard for one table, from its start until `stop()`.

    Windows are looked for every quarter second for half a minute. Once the table's has
    appeared the keyboard is checked as often, and handed to it whenever VPinFE's own
    windows, one of the table's other windows or nothing has it. A program that is not
    VPinFE and not the table having the keyboard is the person's doing, or the
    system's, and is left alone. Where the system's answer can be wrong (Windows), a
    table that has the keyboard but still says it is paused is given it through one of
    VPinFE's windows.
    """

    def __init__(self, backend: Backend | None, root_pid: int, *,
                 titles: Iterable[str] = (), hand_over_on: bool = False,
                 launched_at: float | None = None,
                 on_window: Callable[[], object] | None = None,
                 pause_asked: Callable[[], bool] | None = None,
                 paused: Callable[[], bool] | None = None,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], bool] | None = None,
                 own_pid: int | None = None) -> None:
        self._backend = backend
        self._root = root_pid
        self._own_root = own_pid if own_pid is not None else os.getpid()
        self._titles = tuple(title for title in titles if title)
        self._on = hand_over_on
        self._launched_at = launched_at if launched_at is not None else time.time()
        self._on_window = on_window
        self._pause_asked = pause_asked or (lambda: False)
        self._paused = paused or (lambda: False)
        self._clock = clock
        self._sleep = sleep
        self._wake = threading.Event()
        self._stopping = threading.Event()
        self._closing = False
        self._thread: threading.Thread | None = None
        self._extra = False
        self._tree: tuple[float, set[int], set[int]] | None = None

        self._target: Window | None = None
        self._accepted: set[int] = set()
        self._reported: set[int | str] = set()
        self._holder_key: tuple[int, str] | None = (-1, "")
        self._told: set[tuple[str, tuple[int, str] | None]] = set()
        self._said: set[tuple[str, str]] = set()
        self._gave_up = False
        self._started_at = clock()
        self._window_at: float | None = None
        self._startup_at: float | None = None
        self._held_at: float | None = None
        self._tries = 0
        self._pause_lines = 0
        self._resumed = False
        self._last_holder: Window | None = None

    # -- running ------------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self.run, name="table-focus", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._closing = True
        self._stopping.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def nudge(self) -> None:
        """Look now rather than at the next turn: the table has said it paused."""
        self._wake.set()

    def note_pause_line(self) -> None:
        self._pause_lines += 1
        self.nudge()

    def note_resume_line(self) -> None:
        """The table has run: a pause after this is not the one it opened with."""
        self._resumed = True

    def note_startup_done(self) -> None:
        if self._startup_at is None:
            self._startup_at = self._clock()

    def request_round(self) -> None:
        """Another try, for a feature that has just given the keyboard away on purpose
        and is done with it: lifts a give-up and a pause the person asked for, once. It
        does nothing for a person who has not turned hand-over on."""
        self._extra = True
        self.nudge()

    def run(self) -> None:
        try:
            if self._backend is None:
                logger.info("Table focus: %s", _no_backend_text())
                return
            if self._find():
                self._keep()
        except Exception:
            logger.exception("Table focus stopped on an error")
        finally:
            self._summary()
            close = getattr(self._backend, "close", None)
            if close is not None:
                try:
                    close()
                except Exception:  # noqa: BLE001 - a connection that will not close is not a failure of the watch
                    logger.debug("Table focus: could not close the backend", exc_info=True)

    def _wait(self, seconds: float) -> bool:
        """Until the next look, which a nudge brings forward. True when it is time to stop."""
        if self._sleep is not None:
            return self._sleep(seconds)
        self._wake.wait(seconds)
        self._wake.clear()
        return self._closing

    def _pause(self, seconds: float) -> bool:
        """The gaps inside a round: only stopping cuts them short, or a nudge would spend
        the backoff. True when it is time to stop."""
        if self._sleep is not None:
            return self._sleep(seconds)
        self._stopping.wait(seconds)
        return self._closing

    # -- finding the table's window -------------------------------------------

    def _find(self) -> bool:
        """Looks until the table's window appears or the watch is stopped: a table that
        loads for longer than FIND_SECONDS still gets the keyboard when it comes up."""
        deadline = self._clock() + FIND_SECONDS
        late = False
        while True:
            if self._pick() is not None:
                return True
            if not late and self._clock() >= deadline:
                late = True
                logger.warning("Table focus: no window of the table appeared in %d seconds, "
                               "still looking", FIND_SECONDS)
            if self._wait(LATE_POLL_SECONDS if late else POLL_SECONDS):
                return False

    def _trees(self) -> tuple[set[int], set[int]]:
        """The table's processes, and VPinFE's own that are not the table's."""
        now = self._clock()
        if self._tree is None or now - self._tree[0] >= TREE_SECONDS:
            game = _tree_of(self._root)
            own = _tree_of(self._own_root) - game
            self._tree = (now, game, own)
        return self._tree[1], self._tree[2]

    def _pick(self) -> Window | None:
        assert self._backend is not None
        try:
            windows = self._backend.windows()
        except Exception as exc:
            self._say("windows-error", why(exc),
                      "Table focus: could not list the windows: %s")
            return self._target
        if self._target is not None and not any(
                one.handle == self._target.handle for one in windows):
            logger.info("Table focus: the table's window is gone")
            self._target = None
        if self._titles and not getattr(self._backend, "titles_readable", True):
            logger.info("Table focus: this system does not give out window titles, so the "
                        "table's first window is taken as its playfield")
            self._titles = ()
        game, own = self._trees()
        ours = [one for one in windows if one.pid in game]
        for one in ours:
            if one.handle not in self._reported:
                self._reported.add(one.handle)
                logger.info("Table focus: window of the table: %s, %r",
                            _program_of(one.pid), one.title)

        def named(candidates: Sequence[Window]) -> list[Window]:
            return [one for one in candidates if one.title.strip() in self._titles]

        reason = "its title is the playfield's" if self._titles \
            else "it is the first window of the table"
        found = named(ours) if self._titles else list(ours)
        if not found:
            newer = [one for one in windows
                     if one.pid not in game and one.pid not in own
                     and _started_after(one.pid, self._launched_at - 1.0)]
            for one in newer:
                if one.handle not in self._reported:
                    self._reported.add(one.handle)
                    logger.info("Table focus: window of a program started since the "
                                "launch: %s, %r", _program_of(one.pid), one.title)
            found = named(newer) if self._titles else newer
            reason = "it is the first window of a program started since the launch"
            if not found:
                return self._target
        chosen = found[0]
        self._accepted = {*game, chosen.pid}
        if self._target is None or self._target.handle != chosen.handle:
            first = self._window_at is None
            self._target = chosen
            logger.info("Table focus: the table's window is %s, %r, because %s",
                        _program_of(chosen.pid), chosen.title, reason)
            if first:
                self._window_at = self._clock()
                if self._on_window is not None:
                    try:
                        self._on_window()
                    except Exception:
                        logger.exception("Table focus: marking the table running failed")
        return self._target

    # -- keeping the keyboard on it ---------------------------------------------

    def _keep(self) -> None:
        while True:
            self._check()
            if self._wait(POLL_SECONDS):
                return

    def _holder(self) -> Window | None:
        assert self._backend is not None
        try:
            holder = self._backend.holder()
        except Exception as exc:
            self._say("holder-error", why(exc),
                      "Table focus: could not ask who has the keyboard: %s")
            return self._last_holder
        self._last_holder = holder
        return holder

    def _passes(self, holder: Window | None) -> bool:
        if holder is None or self._target is None or holder.pid not in self._accepted:
            return False
        if holder.handle == self._target.handle:
            return True
        return not self._titles or holder.title.strip() in self._titles

    def _stuck(self, holder: Window | None) -> bool:
        """The table has the keyboard and is still paused from its start: on a system
        whose foreground can be wrong, its own input never heard it gained the focus.
        Never a pause after the table has run, and never one the person asked for."""
        if not self._on or not getattr(self._backend, "holder_can_be_wrong", False):
            return False
        if self._resumed or not self._passes(holder):
            return False
        if self._startup_at is None or self._clock() - self._startup_at < STUCK_SECONDS:
            return False
        return self._paused() and not self._pause_asked()

    def _fine(self, holder: Window | None) -> bool:
        return self._passes(holder) and not self._stuck(holder)

    def _who(self, holder: Window | None) -> str:
        if holder is None:
            return "nobody"
        if holder.pid in self._accepted:
            return "table"
        if holder.pid in self._trees()[1]:
            return "vpinfe"
        return "other"

    def _note_holder(self, holder: Window | None) -> None:
        key = None if holder is None else (holder.pid, holder.title)
        if key == self._holder_key:
            return
        self._holder_key = key
        if holder is None:
            logger.info("Table focus: no window has the keyboard")
            return
        belongs = {"table": "the table's", "vpinfe": "VPinFE's",
                   "other": "another program's"}[self._who(holder)]
        logger.info("Table focus: the keyboard is with %s, %r (%s window)",
                    _program_of(holder.pid), holder.title, belongs)

    def _say(self, kind: str, detail: str, message: str) -> None:
        """Once for each distinct thing, so a refusal that repeats is not logged as many
        times as it is asked."""
        marker = (kind, detail)
        if marker in self._said:
            return
        self._said.add(marker)
        logger.warning(message, detail)

    def _hold_back(self, reason: str, holder: Window | None, *, kind: str = "") -> None:
        key = (kind or reason, None if holder is None else (holder.pid, holder.title))
        if key in self._told:
            return
        self._told.add(key)
        logger.info("Table focus: held back, %s", reason)

    def _check(self) -> None:
        target = self._pick()
        if target is None:
            return
        holder = self._holder()
        self._note_holder(holder)
        extra, self._extra = self._extra, False
        if self._fine(holder):
            if self._held_at is None:
                self._held_at = self._clock()
                logger.info("Table focus: the table has the keyboard, %.1f s after its "
                            "window appeared", self._held_at - (self._window_at or
                                                                self._started_at))
            self._gave_up = False
            self._told.clear()
            return
        if self._who(holder) == "other":
            assert holder is not None
            self._hold_back(f"{_program_of(holder.pid)} has the keyboard, {holder.title!r}, "
                            "which is left alone", holder, kind="other")
            return
        if not self._on:
            self._hold_back("the Experimental Table Focus switch is off", holder)
            return
        if self._pause_asked() and not extra:
            self._hold_back("a pause the person asked for", holder)
            return
        if self._gave_up and not extra:
            return
        self._round()

    def _round(self) -> None:
        assert self._backend is not None and self._target is not None
        for index, gap in enumerate((0.0, *GAPS)):
            if gap and self._pause(gap):
                return
            if index:
                if self._pick() is None:
                    return
                holder = self._holder()
                self._note_holder(holder)
                if self._fine(holder):
                    return
                if self._who(holder) == "other":
                    assert holder is not None
                    self._hold_back(f"{_program_of(holder.pid)} took the keyboard, "
                                    f"{holder.title!r}, which is left alone", holder,
                                    kind="other")
                    return
            self._tries += 1
            stuck = self._stuck(self._holder())
            try:
                refused = self._give(stuck)
            except Exception as exc:
                refused = why(exc)
            if refused and self._pick() is None:
                self._tries -= 1
                return
            if refused:
                logger.warning("Table focus: try %d of %d, the system refused: %s",
                               index + 1, TRIES, refused)
            if self._pause(RESUME_SECONDS if stuck else SETTLE_SECONDS):
                return
            holder = self._holder()
            if self._fine(holder):
                self._note_holder(holder)
                logger.info("Table focus: try %d of %d passed", index + 1, TRIES)
                return
            logger.info("Table focus: try %d of %d failed, the keyboard is with %s",
                        index + 1, TRIES, self._describe(holder))
        self._gave_up = True
        logger.warning("Table focus: gave up after %d tries, the keyboard is with %s",
                       TRIES, self._describe(self._last_holder))

    def _give(self, stuck: bool) -> str:
        """Hands the keyboard to the table. When the table already has it and is paused
        anyway, VPinFE's own window gets it first, so the table is given it afresh."""
        assert self._backend is not None and self._target is not None
        if stuck:
            own = self._own_window()
            if own is None:
                logger.info("Table focus: the table has the keyboard but says it is "
                            "paused, and VPinFE has no window to pass it through")
            else:
                logger.info("Table focus: the table has the keyboard but says it is "
                            "paused, passing it through VPinFE's %r", own.title)
                refused = self._backend.hand_over(own)
                if refused:
                    logger.warning("Table focus: the system refused VPinFE's window: %s",
                                   refused)
                if self._pause(BOUNCE_SECONDS):
                    return ""
        return self._backend.hand_over(self._target)

    def _own_window(self) -> Window | None:
        assert self._backend is not None
        own = self._trees()[1]
        return next((one for one in self._backend.windows() if one.pid in own), None)

    @staticmethod
    def _describe(holder: Window | None) -> str:
        if holder is None:
            return "no window"
        return f"{_program_of(holder.pid)}, {holder.title!r}"

    def _summary(self) -> None:
        now = self._clock()
        held = ("never" if self._held_at is None
                else f"{self._held_at - (self._window_at or self._started_at):.1f} s "
                     "after its window appeared")
        if self._startup_at is None or self._window_at is None:
            startup = ""
        else:
            gap = self._window_at - self._startup_at
            startup = (f", window appeared {abs(gap):.1f} s "
                       f"{'after' if gap >= 0 else 'before'} startup done")
        logger.info("Table focus summary: %d tries, the table first held the keyboard %s%s, "
                    "now with %s, %d pause lines from the table, hand-over %s, %.0f s watched",
                    self._tries, held, startup, self._describe(self._last_holder),
                    self._pause_lines, "on" if self._on else "off", now - self._started_at)


# -- the watch of the table that is running -------------------------------------------

_active: Watch | None = None
_active_lock = threading.Lock()


def watch_launch(root_pid: int, *, ini_config: Any, titles: Iterable[str] = (),
                 on_window: Callable[[], object] | None = None,
                 pause_asked: Callable[[], bool] | None = None,
                 paused: Callable[[], bool] | None = None) -> Watch:
    """Start watching a table that has just been started."""
    on = enabled(ini_config)
    logger.info("Table focus: hand-over is %s, on %s", "on" if on else "off",
                describe_system())
    watch = Watch(backend_for_this_system(), root_pid, titles=titles, hand_over_on=on,
                  on_window=on_window, pause_asked=pause_asked, paused=paused)
    global _active
    with _active_lock:
        _active = watch
    watch.start()
    return watch


def finish(watch: Watch) -> None:
    global _active
    watch.stop()
    with _active_lock:
        if _active is watch:
            _active = None


def one_more_round() -> None:
    """For a feature that moved the keyboard on purpose and has finished: asks the watch
    of the table that is running to check once more and, if hand-over is on, give it back."""
    with _active_lock:
        watch = _active
    if watch is not None:
        watch.request_round()
