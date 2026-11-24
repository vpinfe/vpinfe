"""Showing a game's art on a real DMD panel.

Nothing here draws anything itself - it resolves which video or image a table should
show and hands it to libdmdutil on a worker thread, because the panel is slow
enough that the wheel would stutter waiting for it.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from common.config.config_access import MediaConfig
from common.config.config_store import ConfigStore
from common.games.game import Game

logger = logging.getLogger("vpinfe.common.host.real_dmd")


@dataclass(frozen=True)
class RealDmdArt:
    """What a table has for the panel. A video plays in preference to any picture; the
    picture is what shows where the video cannot be played."""

    video: Path | None = None
    image: Path | None = None


def _priority(iniconfig: ConfigStore | None) -> str:
    if iniconfig is None:
        return "color"
    return MediaConfig.from_config(iniconfig).realdmd_media_priority


def _first(*candidates: str | None) -> Path | None:
    chosen = next((one for one in (str(c or "").strip() for c in candidates) if one), "")
    if not chosen:
        return None
    path = Path(chosen).expanduser()
    try:
        return path.resolve()
    except Exception:
        return path


def get_realdmd_image_for_game(game: Game,
                               iniconfig: ConfigStore | None = None) -> Path | None:
    standard, color = game.real_dmd_image_path, game.real_dmd_color_image_path
    if _priority(iniconfig) == "standard":
        return _first(standard, color)
    return _first(color, standard)


def get_realdmd_video_for_game(game: Game,
                               iniconfig: ConfigStore | None = None) -> Path | None:
    standard, color = game.real_dmd_video_path, game.real_dmd_color_video_path
    if _priority(iniconfig) == "standard":
        return _first(standard, color)
    return _first(color, standard)


def get_realdmd_art_for_game(game: Game, iniconfig: ConfigStore | None = None) -> RealDmdArt:
    return RealDmdArt(video=get_realdmd_video_for_game(game, iniconfig),
                      image=get_realdmd_image_for_game(game, iniconfig))


class RealDmdUpdater:
    """Sends a video or an image to a physical DMD, one selection at a time and never from
    two threads at once. A newer selection replaces the one before it, a playing video
    included."""

    def __init__(self, iniconfig: ConfigStore, window_name: str | None,
                 show_image_func: Callable[[ConfigStore, Path | None], bool],
                 show_video_func: Callable[[ConfigStore, Path, Path | None], bool] | None = None
                 ) -> None:
        self._iniconfig = iniconfig
        self._window_name = window_name or "unknown"
        self._show_image = show_image_func
        self._show_video = show_video_func
        self._video_path: Path | None = None
        self._lock = threading.Lock()
        self._event = threading.Event()
        self._image_path: Path | None = None
        self._game_name = ""
        self._queued = False
        self._thread: threading.Thread | None = None

    def queue_update(self, game_name: str, art: RealDmdArt) -> None:
        self._ensure_worker()
        with self._lock:
            self._game_name = game_name
            self._image_path = art.image
            self._video_path = art.video
            self._queued = True
            self._event.set()

    def show_again(self) -> None:
        """Send the last selection once more, for a panel that was just restarted or a table
        that has exited. Nothing before the first table was selected, so a restart does not
        draw over the logo the panel starts with."""
        with self._lock:
            if not self._queued:
                return
        self._ensure_worker()
        self._event.set()

    def _ensure_worker(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._thread = threading.Thread(
                target=self._worker_loop,
                name=f"realdmd-worker-{self._window_name}",
                daemon=True,
            )
            self._thread.start()

    def _worker_loop(self) -> None:
        while True:
            self._event.wait()
            self._process_pending()

    def _process_pending(self) -> None:
        with self._lock:
            image_path = self._image_path
            video_path = self._video_path
            game_name = self._game_name
            self._event.clear()

        try:
            if video_path is not None and self._show_video is not None:
                sent = self._show_video(self._iniconfig, video_path, image_path)
            else:
                sent = self._show_image(self._iniconfig, image_path)
            logger.debug(
                "Async real DMD update for %s -> sent=%s video=%s image=%s",
                game_name,
                sent,
                video_path,
                image_path,
            )
        except Exception:
            logger.exception(
                "Async real DMD update failed for %s (video=%s image=%s)",
                game_name,
                video_path,
                image_path,
            )
