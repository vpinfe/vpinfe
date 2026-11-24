"""One table: launched, waited for, recorded, closed, then encoded and placed."""

from __future__ import annotations

import logging
import shutil
import subprocess
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from common import events, service_errors
from common.apps.contract import VIEW_FSS
from common.config.config_access import cfg_get
from common.failures import why
from common.games import asset_origin
from common.host import launch, launch_state, tools

from . import adapters, commands, geometry, pipeline, placing, proposals, slots, unshown
from .adapters import Output, Recording
from .placing import Placing
from .settings import Settings

logger = logging.getLogger("vpinfe.common.capture.session")

# How long a table has to say it is up.
START_TIMEOUT = 120.0
CLOSE_TIMEOUT = 15.0
# The share of Length times the refresh a recording must hold to have kept up.
ENOUGH_FRAMES = 0.95

KINDS = {adapters.PLAYFIELD: ("playfield", "playfield_video"),
         adapters.BACKGLASS: ("backglass", "backglass_video"),
         adapters.SCOREVIEW: ("scoreview", "scoreview_video"),
         adapters.TOPPER: ("topper", "topper_video")}
AUDIO = "audio"
# The playfield window with the table started in Full Single Screen. A picture only, and a
# launch of its own, since the other kinds are the table's own view.
FSS = "playfield_fss"
RECORDABLE = frozenset({kind for pair in KINDS.values() for kind in pair} | {AUDIO, FSS})

RECORDED = "recorded"
FAILED = "failed"
CLOSED = "closed"
SKIPPED = "skipped"
# Halted by the run: nothing placed, the recording thrown away.
STOPPED = "stopped"

WOULD_NOT_START = "capture.outcome.start_failed"
CLOSED_AT_CABINET = "capture.outcome.closed"
NO_FRAMES = "capture.outcome.no_frames"
SILENT = "capture.outcome.silent"
NOT_WRITTEN = "capture.outcome.not_written"
NOTHING_TO_RECORD = "capture.outcome.nothing_to_record"
ONE_COLOR = "capture.outcome.one_color"


def said(key: str, **params: str) -> dict[str, Any]:
    return {"key": key, "params": params}


@dataclass
class Kit:
    """What a session runs things with. Tests hand it stand-ins."""

    launch: Callable[..., Any] = launch.launch_game
    stop: Callable[[], Any] = launch_state.stop
    popen: Callable[..., Any] = subprocess.Popen
    runner: Callable[..., Any] = subprocess.run
    place: Callable[..., Any] | None = None
    clock: Callable[[], float] = time.monotonic
    propose: Callable[..., Any] | None = None


@dataclass
class Result:
    state: str
    placed: list[dict[str, str]] = field(default_factory=list)
    failed: list[dict[str, Any]] = field(default_factory=list)
    reason: dict[str, Any] | None = None
    # Whether screens still record at once, for the rest of a run.
    at_once: bool = True
    proposed: list[dict[str, str]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"state": self.state, "placed": self.placed, "failed": self.failed,
                "proposed": self.proposed, "reason": self.reason, "at_once": self.at_once}


@dataclass(frozen=True)
class Target:
    game_id: str
    game: Any
    # "" for the game's own tier.
    table_id: str
    # What the launch is asked to play: the table's filename or key, or None for the
    # game's default.
    table: str | None
    kinds: tuple[str, ...]
    # Kept as proposals rather than placed.
    propose: frozenset[str] = frozenset()
    # Placed over a file, which goes where nothing else uses it.
    replace: frozenset[str] = frozenset()
    # The view the table is started in: "" for the app's own, or `VIEW_FSS`, where `kinds`
    # is `FSS` alone.
    view: str = ""


def playfield_turn(config: Any, base: geometry.Turn, orientation: str) -> geometry.Turn:
    """The playfield's buffer to its stored orientation, under this device's rotation."""
    raw = str(cfg_get(config, "windows.playfield", "rotation") or "0")
    return geometry.playfield(base, int(raw) if raw.isdigit() else 0, orientation)


def _place(game_id: str, kind: str, table_id: str, source: Path, origin: str,
           md5: str) -> Any:
    from common.games import media_ops

    return media_ops.place_file(game_id, kind, table_id, source, origin, md5)


class Session:
    def __init__(self, target: Target, chosen: Settings, *, adapter: adapters.Adapter,
                 screens: Mapping[str, Output], found: Mapping[str, tools.Found],
                 at_once: bool, codec: str, work: Path, config: Any,
                 kit: Kit | None = None, placed: Placing | None = None,
                 halt: threading.Event | None = None) -> None:
        self.target = target
        self.chosen = chosen
        self.adapter = adapter
        self.found = found
        self.at_once = at_once
        self.codec = codec
        self.work = work
        self.config = config
        self.kit = kit or Kit()
        self.placed = placed
        self.halt = halt or threading.Event()
        self.hardware = adapter.hardware(found[tools.FFMPEG.id]) if at_once else ""
        self.wanted = set(target.kinds)
        self.kinds = {adapters.PLAYFIELD: (FSS, "")} if target.view == VIEW_FSS else KINDS
        self.screens = dict(screens)
        self.dropped: set[str] = set()
        self.sound = AUDIO in self.wanted
        self.launched = threading.Event()
        self.exited = threading.Event()
        self.either = threading.Event()
        self.failure: list[BaseException] = []
        self.counted: dict[Path, int] = {}
        self.result = Result(RECORDED, at_once=at_once)
        if placed is not None:
            self._place(placed.screens())
        self._choose()

    # --- which output each window is on ---------------------------------------------

    def _choose(self) -> None:
        self.video = [window for window, (_, video) in self.kinds.items()
                      if video in self.wanted and window in self.screens]
        self.stills = [window for window, (picture, video) in self.kinds.items()
                       if picture in self.wanted and video not in self.wanted
                       and window in self.screens]

    def _place(self, screens: Mapping[str, adapters.Screen]) -> None:
        """Each window asked for goes where `screens` says, or fails with its reason."""
        for window, screen in screens.items():
            kinds = [kind for kind in self.kinds.get(window, ()) if kind in self.wanted]
            if not kinds or window in self.dropped:
                continue
            before = self.screens.get(window)
            if screen.output is None:
                if before is not None:
                    logger.warning("Recording %s: nothing to record on %s for the %s (%s)",
                                   self.target.game_id, before.name, window, screen.reason)
                self.screens.pop(window, None)
                self.dropped.add(window)
                for kind in kinds:
                    self._fail(kind, said(screen.reason, window=window, **screen.params))
                continue
            if before is not None and before.name != screen.output.name:
                logger.warning("Recording %s: the %s is on %s, not %s", self.target.game_id,
                               window, screen.output.name, before.name)
            self.screens[window] = screen.output

    def _confirm(self) -> None:
        """Where the desktop can say which output holds each of the app's windows, that is
        where this recording looks."""
        if self.placed is None or self.placed.shown is None:
            return
        seen = placing.seen(self.adapter, self.placed.shown)
        if seen is None:
            return
        try:
            unshown.learn(self.target.game_id, self.placed.shown.placer, seen,
                          [window for window in [*self.video, *self.stills]
                           if window not in seen])
        except OSError:
            logger.warning("Recording %s: could not keep which windows were shown",
                           self.target.game_id, exc_info=True)
        self._place(self.placed.screens(seen))
        self._choose()

    # --- the launch -----------------------------------------------------------------

    def _heard(self, flag: threading.Event) -> Callable[..., None]:
        def heard(**payload: Any) -> None:
            if payload.get("source") == launch_state.SOURCE_CAPTURE:
                flag.set()
                self.either.set()
        return heard

    def _launch(self) -> threading.Thread:
        def launching() -> None:
            try:
                self.kit.launch(self.target.game, self.config,
                                source=launch_state.SOURCE_CAPTURE, table=self.target.table,
                                record_sound=self.sound, record_view=self.target.view)
            except BaseException as exc:  # noqa: BLE001 - said as the table's outcome
                self.failure.append(exc)
            finally:
                self.exited.set()
                self.either.set()

        thread = threading.Thread(target=launching, daemon=True, name="capture-launch")
        thread.start()
        return thread

    # --- recording ------------------------------------------------------------------

    def _start(self, window: str) -> Recording:
        dest = self.work / f"{window}.mkv"
        output = self.adapter.lend(self.screens[window])
        argv = commands.record(self.adapter.id, self.found, output, window, dest,
                               self.chosen, self.hardware)
        return Recording(window, dest, self._spawn(argv, output), self.kit.clock())

    def _start_sound(self) -> Recording:
        dest = self.work / "sound.wav"
        argv = pipeline.sound(self._ffmpeg(), self.chosen.sound_source, dest)
        return Recording(AUDIO, dest, self._spawn(argv), self.kit.clock())

    def _spawn(self, argv: list[str], output: Output | None = None) -> Any:
        logger.info("Recording: %s", argv)
        return adapters.spawn(self.kit.popen, argv, output)

    def _ffmpeg(self) -> Path:
        return Path(str(self.found[tools.FFMPEG.id].path))

    def _take_stills(self) -> None:
        for window in self.stills:
            dest = self.work / f"{window}.png"
            output = self.adapter.lend(self.screens[window])
            try:
                self.kit.runner(self.adapter.still(self.found, output, dest),
                                capture_output=True, stdin=subprocess.DEVNULL,
                                timeout=tools.TIMEOUT, check=True,
                                creationflags=tools.NO_WINDOW, **adapters.passing(output))
            except (OSError, subprocess.SubprocessError):
                logger.warning("Recording: no picture of the %s screen", window,
                               exc_info=True)

    def _hold(self, started: float, stills: bool) -> bool:
        """Until Length has passed since `started`, taking the stills at Picture At.
        True where the table closed first."""
        if stills:
            if self.exited.wait(max(0.0, started + self.chosen.picture_at
                                    - self.kit.clock())):
                return True
            self._take_stills()
        return self.exited.wait(max(0.0, started + self.chosen.length - self.kit.clock()))

    def _frames(self, recording: Recording) -> int:
        if recording.path not in self.counted:
            try:
                done = pipeline.run(pipeline.frames(self._ffmpeg(), recording.path),
                                    self.kit.runner)
                self.counted[recording.path] = pipeline.frames_in(done.stdout)
            except (OSError, subprocess.SubprocessError):
                self.counted[recording.path] = 0
        return self.counted[recording.path]

    def _one_color(self, source: Path, per_second: int = 0) -> bool:
        """False where the levels cannot be read."""
        try:
            done = pipeline.run(pipeline.levels(self._ffmpeg(), source, per_second),
                                self.kit.runner)
        except (OSError, subprocess.SubprocessError):
            return False
        return pipeline.flat_in(done.stderr)

    def _kept_up(self, recording: Recording) -> bool:
        refresh = self.screens[recording.window].refresh or 60.0
        return self._frames(recording) >= ENOUGH_FRAMES * self.chosen.length * refresh

    def _at_once(self) -> tuple[dict[str, Recording], Recording | None, bool]:
        recordings = {window: self._start(window) for window in self.video}
        sound = self._start_sound() if self.sound else None
        started = max((one.started for one in recordings.values()), default=self.kit.clock())
        closed = self._hold(started, bool(self.stills))
        for one in [*recordings.values(), *([sound] if sound else [])]:
            one.stop()
        return recordings, sound, closed

    def _in_turn(self, sound_too: bool) -> tuple[dict[str, Recording], Recording | None,
                                                 bool]:
        recordings: dict[str, Recording] = {}
        sound = None
        for index, window in enumerate(self.video):
            one = self._start(window)
            if index == 0 and sound_too:
                sound = self._start_sound()
            closed = self._hold(one.started, index == 0 and bool(self.stills))
            one.stop()
            if sound is not None and index == 0:
                sound.stop()
            recordings[window] = one
            if closed:
                return recordings, sound, True
        return recordings, sound, False

    def _no_video(self) -> tuple[dict[str, Recording], Recording | None, bool]:
        if not self.sound:
            closed = self.exited.wait(self.chosen.picture_at)
            if not closed:
                self._take_stills()
            return {}, None, closed
        sound = self._start_sound()
        closed = self._hold(sound.started, bool(self.stills))
        sound.stop()
        return {}, sound, closed

    def _record(self) -> tuple[dict[str, Recording], Recording | None, bool]:
        if not self.video:
            return self._no_video()
        if not (self.at_once and len(self.video) > 1):
            return self._in_turn(sound_too=self.sound)
        recordings, sound, closed = self._at_once()
        if closed or all(self._kept_up(one) for one in recordings.values()):
            return recordings, sound, closed
        logger.warning("Recording at once did not keep up; recording %s one screen at a "
                       "time from here", self.target.game_id)
        self.at_once = self.result.at_once = False
        self._clear(recordings.values())
        recordings, _, closed = self._in_turn(sound_too=False)
        return recordings, sound, closed

    def _clear(self, recordings: Any) -> None:
        for one in list(recordings):
            one.path.unlink(missing_ok=True)
            self.counted.pop(one.path, None)

    # --- after the table has closed -----------------------------------------------------

    def _turn(self, window: str, base: geometry.Turn) -> geometry.Turn:
        if window == adapters.PLAYFIELD:
            return playfield_turn(self.config, base, self.chosen.playfield_orientation)
        return geometry.screen(base)

    def _made(self, kind: str, argv: list[str], dest: Path,
              made: dict[str, Path]) -> None:
        try:
            pipeline.run(argv, self.kit.runner)
        except (OSError, subprocess.SubprocessError):
            logger.warning("Recording: could not write %s", dest, exc_info=True)
        if dest.is_file() and dest.stat().st_size:
            made[kind] = dest
        else:
            self._fail(kind, said(NOT_WRITTEN))

    def _fail(self, kind: str, reason: dict[str, Any]) -> None:
        self.result.failed.append({"kind": kind, "reason": reason})

    def _encode(self, recordings: dict[str, Recording],
                sound: Recording | None) -> dict[str, Path]:
        ffmpeg, cap = self._ffmpeg(), pipeline.cap_of(self.chosen.size)
        wanted = set(self.target.kinds)
        first = max((one.started for one in recordings.values()), default=0.0) \
            if self.result.at_once else 0.0
        made: dict[str, Path] = {}
        for window, one in recordings.items():
            if self.halt.is_set():
                return made
            picture, video = KINDS[window]
            skip = max(0.0, first - one.started) if self.result.at_once else 0.0
            if not one.path.is_file() or not self._frames(one):
                for kind in (picture, video):
                    if kind in wanted:
                        self._fail(kind, said(NO_FRAMES, window=window))
                continue
            if self._one_color(one.path) \
                    and self._one_color(one.path, pipeline.CONFIRM_PER_SECOND):
                for kind in (picture, video):
                    if kind in wanted:
                        self._fail(kind, said(ONE_COLOR, window=window))
                continue
            turn = self._turn(window, self.adapter.recording_turn(self.screens[window]))
            encoded = self.work / f"{video}.mp4"
            job = pipeline.Encode(one.path, skip, self.chosen.length, turn,
                                  self.chosen.fps, cap, self.codec, self.chosen.quality)
            self._made(video, commands.encode(self.chosen.encode_command, ffmpeg, job, encoded,
                                              window=window, output=self.screens[window]),
                       encoded, made)
            if picture in wanted:
                dest = self.work / f"{picture}.png"
                cut = (pipeline.picture(ffmpeg, encoded, geometry.NONE, None, dest,
                                        at=self.chosen.picture_at)
                       if self.chosen.encode_command
                       else pipeline.picture(ffmpeg, one.path, turn, cap, dest,
                                             at=skip + self.chosen.picture_at))
                self._made(picture, cut, dest, made)
        for window in self.stills:
            picture = self.kinds[window][0]
            still = self.work / f"{window}.png"
            if not still.is_file():
                self._fail(picture, said(NO_FRAMES, window=window))
                continue
            if self._one_color(still):
                self._fail(picture, said(ONE_COLOR, window=window))
                continue
            dest = self.work / f"{picture}.stored.png"
            turn = self._turn(window, self.adapter.still_turn(self.screens[window]))
            self._made(picture, pipeline.picture(ffmpeg, still, turn, cap, dest), dest, made)
        if sound is not None:
            self._encode_sound(sound, first, made)
        return made

    def _encode_sound(self, sound: Recording, first: float, made: dict[str, Path]) -> None:
        ffmpeg = self._ffmpeg()
        try:
            heard = pipeline.run(pipeline.loudness(ffmpeg, sound.path), self.kit.runner)
            peak = pipeline.peak_in(heard.stderr)
        except (OSError, subprocess.SubprocessError):
            peak = float("-inf")
        if peak < pipeline.SILENT_DB:
            self._fail(AUDIO, said(SILENT))
            return
        skip = max(0.0, first - sound.started) if self.result.at_once else 0.0
        dest = self.work / "audio.mp3"
        self._made(AUDIO, pipeline.mp3(ffmpeg, sound.path, skip, self.chosen.length, dest),
                   dest, made)

    def _land(self, made: dict[str, Path]) -> None:
        place = self.kit.place or _place
        propose = self.kit.propose or proposals.keep
        game_id, table_id = self.target.game_id, self.target.table_id
        for kind, path in made.items():
            try:
                if kind in self.target.propose:
                    kept = propose(game_id, table_id, kind, path)
                    self.result.proposed.append({"kind": kind, "id": str(kept["id"])})
                    continue
                row = slots.serving(game_id, table_id, kind) \
                    if kind in self.target.replace else None
                slots.can_keep(game_id, row, table_id)
                written = place(game_id, kind, table_id, path, asset_origin.RECORDED, "")
                slots.remove(game_id, row, table_id,
                             str((written or {}).get("written") or ""))
            except (OSError, service_errors.ServiceError) as exc:
                self._fail(kind, {"key": NOT_WRITTEN, "params": {}, "detail": why(exc)})
                continue
            self.result.placed.append({"kind": kind})

    # --- the whole of it ------------------------------------------------------------

    def run(self) -> Result:
        if self.video or self.stills:
            self._place(self.adapter.begin({window: self.screens[window]
                                            for window in (*self.video, *self.stills)}))
            self._choose()
        try:
            return self._recorded()
        finally:
            self.adapter.end()

    def _recorded(self) -> Result:
        if not self.video and not self.stills and not self.sound:
            if self.result.failed:
                self.result.state = FAILED
            else:
                self.result.state, self.result.reason = SKIPPED, said(NOTHING_TO_RECORD)
            return self.result
        shutil.rmtree(self.work, ignore_errors=True)
        self.work.mkdir(parents=True)
        subscriptions = ((events.TABLE_LAUNCHED, self._heard(self.launched)),
                         (events.TABLE_EXITED, self._heard(self.exited)))
        for name, handler in subscriptions:
            events.subscribe(name, handler)
        try:
            return self._run()
        finally:
            for name, handler in subscriptions:
                events.unsubscribe(name, handler)
            shutil.rmtree(self.work, ignore_errors=True)

    def _run(self) -> Result:
        thread = self._launch()
        self.either.wait(START_TIMEOUT)
        if self.halt.is_set():
            self.kit.stop()
            thread.join(CLOSE_TIMEOUT)
            return self._stopped()
        if not self.launched.is_set():
            self.kit.stop()
            thread.join(CLOSE_TIMEOUT)
            self.result.state = FAILED
            self.result.reason = {"key": WOULD_NOT_START, "params": {},
                                  "detail": why(self.failure[0]) if self.failure else ""}
            return self.result
        if self.exited.wait(self.chosen.wait):
            return self._closed(thread)
        self._confirm()
        recordings, sound, closed = self._record()
        if closed:
            return self._closed(thread)
        self.kit.stop()
        thread.join(CLOSE_TIMEOUT)
        if self.halt.is_set():
            return self._stopped()
        made = self._encode(recordings, sound)
        if self.halt.is_set():
            return self._stopped()
        self._land(made)
        if self.result.placed:
            self._refresh()
        if not self.result.placed and not self.result.proposed:
            self.result.state = FAILED
        return self.result

    def _closed(self, thread: threading.Thread) -> Result:
        thread.join(CLOSE_TIMEOUT)
        if self.halt.is_set():
            return self._stopped()
        self.result.state, self.result.reason = CLOSED, said(CLOSED_AT_CABINET)
        return self.result

    def _stopped(self) -> Result:
        self.result = Result(STOPPED, at_once=self.result.at_once)
        return self.result

    def _refresh(self) -> None:
        from common.games import game_repository, media_service

        media_service.invalidate_media_cache()
        folder = getattr(self.target.game, "full_path_game", None)
        if folder:
            game_repository.refresh_game(Path(str(folder)))
