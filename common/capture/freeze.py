"""Take Picture during play. The frozen screens are the preview."""

from __future__ import annotations

import logging
import shutil
import subprocess
import threading
import time
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from common import events
from common.config.config_access import cfg_get
from common.host import launch, launch_state, tools

from . import adapters, compose, geometry, placing, preflight
from .geometry import Turn

logger = logging.getLogger("vpinfe.common.capture.freeze")

BOUNCE_SECONDS = 0.5
PAUSE_SECONDS = 2.0
RESUME_SECONDS = 2.0

# What a press did, as the page that sent it is told.
FROZEN = "frozen"
TAKEN = "taken"
RESUMED = "resumed"
IGNORED = "ignored"
NOT_PAUSED = "not_paused"
NOT_PLAYING = "not_playing"
FAILED = "failed"


@dataclass(frozen=True)
class Playing:
    game: Any
    table_id: str
    game_id: str = ""


@dataclass
class Aim:
    """Which screens can be pictured here, on which outputs, turned how."""

    adapter: Any
    found: Mapping[str, tools.Found]
    outputs: dict[str, adapters.Output]
    turns: dict[str, Turn]


@dataclass
class Shot:
    stills: dict[str, Path] = field(default_factory=dict)
    turns: dict[str, Turn] = field(default_factory=dict)
    taken: datetime = field(default_factory=datetime.now)
    work: Path | None = None


def _work() -> Path:
    from common.config.paths import CAPTURE_DIR

    return CAPTURE_DIR / "picture"


def _table_key(game: Any, table_id: str) -> str | None:
    from common.games import tables

    entry = tables.table_entries(getattr(game, "meta_config", None) or {}).get(table_id) \
        if table_id else None
    return tables.entry_native_key(entry) if entry is not None else None


# A picture no screen of this device can be taken of, whichever screen it is on.
_TOOL_REASONS = (preflight.NEEDS_TOOL, preflight.NO_ENCODER, preflight.NO_GRABBER,
                 preflight.LACKS, adapters.SCREEN_PERMISSION, adapters.NOT_CHOSEN)


def aim(playing: Playing | None = None, config: Any = None) -> Aim | None:
    """The screens this device can picture, on the outputs the running table shows them
    on, or None, saying why, where it can picture none."""
    adapter = adapters.resolve()
    if isinstance(adapter, adapters.Unsupported):
        logger.warning("Take Picture: %s", preflight.words(preflight.reason(
            adapter.reason, adapter.params)))
        return None
    if config is None:
        from common.config.paths import get_ini_config
        config = get_ini_config()
    from common.host import display_service

    found = {tool.id: tools.resolve(tool) for tool in adapter.requirements()}
    try:
        outputs = adapter.outputs()
    except (OSError, ValueError) as exc:
        logger.warning("Take Picture: the screens could not be read: %s", exc)
        return None
    monitors = display_service.get_display_monitors()
    shown = placing.shown(playing.game, _table_key(playing.game, playing.table_id)) \
        if playing is not None else placing.shown()
    report = preflight.report(adapter=adapter, config=config, monitors=monitors,
                              found=found, probe_hardware=False, shown=shown)
    blocked = next((row["picture"]["reason"] for row in report["screens"]
                    if (row["picture"]["reason"] or {}).get("key") in _TOOL_REASONS), None)
    if blocked is not None:
        logger.warning("Take Picture: %s", preflight.words(blocked))
        return None
    seen = placing.seen(adapter, shown) if shown is not None else None
    screens = placing.Placing(outputs, config, monitors, shown).screens(seen)
    raw = str(cfg_get(config, "windows.playfield", "rotation") or "0")
    rotation = int(raw) if raw.isdigit() else 0
    placed: dict[str, adapters.Output] = {}
    turns: dict[str, Turn] = {}
    for window, screen in screens.items():
        if screen.output is None:
            continue
        placed[window] = screen.output
        as_shown = adapter.still_turn(screen.output)
        turns[window] = geometry.playfield(as_shown, rotation, geometry.UPRIGHT) \
            if window == adapters.PLAYFIELD else geometry.screen(as_shown)
    if not placed:
        logger.warning("Take Picture: no screen here can be pictured")
        return None
    logger.info("Take Picture: %s", ", ".join(f"{window} on {output.name}"
                                              for window, output in placed.items()))
    return Aim(adapter, found, placed, turns)


def grab(aimed: Aim, runner: Callable[..., Any] = subprocess.run) -> Shot:
    """Every screen at once: each is taken when asked, so none waits on another."""
    work = _work() / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    work.mkdir(parents=True, exist_ok=True)
    shot = Shot(turns=dict(aimed.turns), work=work)

    def one(window: str, output: adapters.Output) -> tuple[str, Path | None]:
        dest = work / f"{window}.png"
        argv = aimed.adapter.still(aimed.found, output, dest)
        try:
            runner(argv, capture_output=True, stdin=subprocess.DEVNULL,
                   timeout=tools.TIMEOUT, check=True, creationflags=tools.NO_WINDOW,
                   **adapters.passing(output))
        except (OSError, subprocess.SubprocessError) as exc:
            logger.warning("Take Picture: no picture of the %s screen: %s", window, exc)
            return window, None
        return window, dest if dest.is_file() and dest.stat().st_size else None

    try:
        reached: dict[str, adapters.Output] = {}
        for window, screen in aimed.adapter.begin(aimed.outputs).items():
            if screen.output is None:
                logger.warning("Take Picture: no picture of the %s screen: %s", window,
                               preflight.words({"key": screen.reason,
                                                "params": {"window": window,
                                                           **screen.params}}))
                continue
            reached[window] = aimed.adapter.lend(screen.output)
        if reached:
            with ThreadPoolExecutor(max_workers=len(reached)) as pool:
                for window, path in pool.map(one, list(reached), list(reached.values())):
                    if path is not None:
                        shot.stills[window] = path
    finally:
        aimed.adapter.end()
    return shot


def keep(shot: Shot, playing: Playing) -> Path | None:
    """The screens as one picture in the game's Pictures, or None, saying why."""
    from common.games import pictures

    folder = str(getattr(playing.game, "full_path_game", "") or "")
    try:
        if not folder:
            logger.warning("Take Picture: the game being played has no folder to keep it in")
            return None
        image = compose.compose(shot.stills, shot.turns)
        return pictures.keep(image, folder, table_id=playing.table_id, taken=shot.taken)
    except (OSError, ValueError) as exc:
        logger.warning("Take Picture: the picture could not be kept: %s", exc)
        return None
    finally:
        if shot.work is not None:
            shutil.rmtree(shot.work, ignore_errors=True)


def _in_the_background(work: Callable[[], None]) -> None:
    threading.Thread(target=work, daemon=True, name="take-picture").start()


@dataclass
class Kit:
    """What the flow runs things with. Tests hand it stand-ins."""

    toggle: Callable[[], bool] = launch_state.toggle_pause
    wait: Callable[[bool, float], bool] = launch_state.wait_paused
    state: Callable[[], launch_state.LaunchState] = launch_state.current
    clock: Callable[[], float] = time.monotonic
    aim: Callable[[Playing | None], Aim | None] = aim
    grab: Callable[[Aim], Shot] = grab
    keep: Callable[[Shot, Playing], Path | None] = keep
    later: Callable[[Callable[[], None]], None] = _in_the_background


def said(state: str, **more: Any) -> dict[str, Any]:
    return {"state": state, **more}


class Flow:
    def __init__(self, kit: Kit | None = None) -> None:
        self.kit = kit or Kit()
        self._busy = threading.Lock()
        self._last = float("-inf")
        self._aimed: Aim | None = None
        self.playing: Playing | None = None

    # --- the table ------------------------------------------------------------------

    def launched(self, **payload: Any) -> None:
        if payload.get("source") == launch_state.SOURCE_CAPTURE:
            return
        self.playing = Playing(payload.get("game"), str(payload.get("table_id") or ""),
                               str(payload.get("game_id") or ""))
        self._aimed = None

    def exited(self, **_payload: Any) -> None:
        self.playing = None
        self._aimed = None

    def _running(self) -> launch_state.LaunchState | None:
        state = self.kit.state()
        if not state.launching or state.source == launch_state.SOURCE_CAPTURE \
                or self.playing is None:
            return None
        return state

    # --- the presses ----------------------------------------------------------------

    def take_picture(self) -> dict[str, Any]:
        """Freeze the table, or take the picture where it is frozen."""
        now = self.kit.clock()
        if now - self._last < BOUNCE_SECONDS:
            return said(IGNORED)
        self._last = now
        if not self._busy.acquire(blocking=False):
            return said(IGNORED)
        try:
            state = self._running()
            if state is None:
                logger.info("Take Picture: no table is being played")
                return said(NOT_PLAYING)
            if not state.paused:
                return self._freeze()
            return self._take()
        finally:
            self._busy.release()

    def resume(self) -> dict[str, Any]:
        """Back while frozen: resume the table and take nothing."""
        if not self._busy.acquire(blocking=False):
            return said(IGNORED)
        try:
            state = self._running()
            if state is None or not state.paused:
                return said(IGNORED)
            logger.info("Take Picture: resuming without a picture")
            self._aimed = None
            if not self.kit.toggle():
                return said(NOT_PAUSED)
            if not self.kit.wait(False, RESUME_SECONDS):
                logger.warning("Take Picture: the table did not say it resumed")
            return said(RESUMED)
        finally:
            self._busy.release()

    def _freeze(self) -> dict[str, Any]:
        logger.info("Take Picture: pausing the table")
        if not self.kit.toggle():
            logger.warning("Take Picture: the table's pause key could not be pressed")
            return said(NOT_PAUSED)
        if not self.kit.wait(True, PAUSE_SECONDS):
            logger.warning("Take Picture: the table did not say it paused within %ss",
                           PAUSE_SECONDS)
            return said(NOT_PAUSED)
        logger.info("Take Picture: the table is paused; press again to take the picture, "
                    "or Back to resume")
        self._aimed = None

        playing = self.playing

        def aiming() -> None:
            self._aimed = self.kit.aim(playing)

        self.kit.later(aiming)
        return said(FROZEN)

    def _take(self) -> dict[str, Any]:
        aimed = self._aimed or self.kit.aim(self.playing)
        self._aimed = None
        shot = self.kit.grab(aimed) if aimed is not None else Shot()
        if shot.stills:
            logger.info("Take Picture: took %s", ", ".join(sorted(shot.stills)))
        resumed = self.kit.toggle()
        playing = self.playing

        def finish() -> None:
            if resumed and not self.kit.wait(False, RESUME_SECONDS):
                logger.warning("Take Picture: the table did not say it resumed")
            if shot.stills and playing is not None:
                kept = self.kit.keep(shot, playing)
                if kept is not None:
                    logger.info("Take Picture: kept %s", kept)
                    events.emit(events.TABLE_PICTURE_TAKEN, game=playing.game,
                                game_id=playing.game_id, table_id=playing.table_id,
                                file=kept.name,
                                taken=shot.taken.isoformat(timespec="seconds"),
                                private=launch.is_private(playing.game))

        self.kit.later(finish)
        return said(TAKEN) if shot.stills else said(FAILED)


_flow = Flow()
_registered = False


def take_picture() -> dict[str, Any]:
    return _flow.take_picture()


def resume() -> dict[str, Any]:
    return _flow.resume()


def register() -> None:
    """Follow which table is being played. Idempotent."""
    global _registered
    if _registered:
        return
    events.subscribe(events.TABLE_LAUNCHED, _flow.launched)
    events.subscribe(events.TABLE_EXITED, _flow.exited)
    _registered = True


def reset_for_tests(kit: Kit | None = None) -> Flow:
    global _flow, _registered
    events.unsubscribe(events.TABLE_LAUNCHED, _flow.launched)
    events.unsubscribe(events.TABLE_EXITED, _flow.exited)
    _flow = Flow(kit)
    _registered = False
    return _flow
