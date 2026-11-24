"""Driving a real DMD panel, in a child process for the same reason DOF is."""

from __future__ import annotations

import logging
import subprocess
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from common.config.config_access import cfg_bool, cfg_get
from common.config.config_store import ConfigStore
from common.config.paths import bundled
from common.failures import why
from common.host import bundled_libraries, tools
from common.host.third_party import (
    find_named_path,
    import_module_from_path,
    third_party_base_candidates,
)
from common.i18n import t

_LOCK = threading.Lock()
_CONTROLLER = None
_CURRENT_IMAGE = None
_PLAYER: _VideoPlayer | None = None
_REPORTED: set[str] = set()
_WRAPPER_NAMES = ('libdmdutil_wrapper.py',)
_DEFAULT_REALDMD_IMAGE = bundled("common", "host", "static", "vpinfe_realdmd.png")
logger = logging.getLogger("vpinfe.common.host.libdmdutil_service")


def _is_enabled(iniconfig: ConfigStore) -> bool:
    # Through `cfg_bool` rather than the parser: the parser answers the one section name
    # it is handed, so a read straight off it sees neither a renamed section nor a 2.x
    # spelling, and a real DMD that is switched on reports itself off.
    return cfg_bool(iniconfig, 'real_dmd', 'enabled', False)


def _find_named_path(base: Path, names: tuple[str, ...]) -> Path | None:
    return find_named_path(base, names)


def _find_wrapper_path(base: Path) -> Path | None:
    return _find_named_path(base, _WRAPPER_NAMES)


def _get_libdmdutil_base_candidates() -> list[Path]:
    return third_party_base_candidates('VPINFE_LIBDMDUTIL_DIR', 'libdmdutil')


def _load_controller_class() -> tuple[Any, Path]:
    candidates = _get_libdmdutil_base_candidates()
    wrapper_path = None
    for candidate in candidates:
        found = _find_wrapper_path(candidate)
        if found is not None:
            wrapper_path = found
            break

    if wrapper_path is None:
        logger.warning(
            "libdmdutil enabled but wrapper not found. Checked: %s",
            ", ".join(str(c) for c in candidates),
        )
        return None, candidates[0]

    module_dir = wrapper_path.parent
    try:
        module = import_module_from_path(wrapper_path)
    except Exception as e:
        logger.error("Failed importing libdmdutil_wrapper.py: %s", e)
        return None, module_dir

    controller_class = getattr(module, 'DMDController', None)
    if controller_class is None:
        logger.error("DMDController class not found in libdmdutil_wrapper.py")
        return None, module_dir
    return controller_class, module_dir


def find_libdmdutil_file(*names: str) -> Path | None:
    candidates = _get_libdmdutil_base_candidates()
    valid_names = tuple(name for name in names if name)
    if not valid_names:
        return None

    for candidate in candidates:
        found = _find_named_path(candidate, valid_names)
        if found is not None:
            return found
    return None


def _build_controller_kwargs(iniconfig: ConfigStore) -> dict[str, str]:
    raw_serial_port = str(
        cfg_get(iniconfig, 'real_dmd', 'zedmd_serial_port', '')
    ).strip()
    if raw_serial_port:
        return {'device': raw_serial_port}

    raw_host = str(
        cfg_get(iniconfig, 'real_dmd', 'zedmd_wifi_address', '')
    ).strip()
    if raw_host:
        return {'host': raw_host}

    return {}


class _VideoPlayer:
    """A video decoded by FFmpeg to the panel's size and sent frame by frame, again from the
    start each time it ends, until `stop` returns. A frame the panel is still taking when
    `stop` gives up waiting, after 2 seconds, can finish after it."""

    def __init__(self, controller: Any, path: str, command: Sequence[str],
                 frame_bytes: int) -> None:
        self.path = path
        self._controller = controller
        self._command = list(command)
        self._frame_bytes = frame_bytes
        self._guard = threading.Lock()
        self._stopped = threading.Event()
        self._proc: subprocess.Popen[bytes] | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> str | None:
        """Play from the first frame, or say in words why it cannot."""
        opened = self._open()
        if isinstance(opened, str):
            return opened
        proc, first = opened
        self._thread = threading.Thread(target=self._run, args=(proc, first),
                                        name="realdmd-video", daemon=True)
        self._thread.start()
        return None

    @property
    def playing(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    def stop(self) -> None:
        self._stopped.set()
        with self._guard:
            proc = self._proc
        if proc is not None:
            _kill(proc)
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)

    def _open(self) -> tuple[subprocess.Popen[bytes], bytes] | str:
        if self._stopped.is_set():
            return t("error.real_dmd_video.stopped")
        try:
            proc = subprocess.Popen(self._command, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    creationflags=tools.NO_WINDOW,
                                    env=bundled_libraries.for_child())
        except OSError as exc:
            return why(exc)
        with self._guard:
            self._proc = proc
            stopped = self._stopped.is_set()
        if stopped:
            _end(proc)
            return t("error.real_dmd_video.stopped")
        assert proc.stdout is not None
        first = proc.stdout.read(self._frame_bytes)
        if len(first) == self._frame_bytes:
            if proc.stderr is not None:
                threading.Thread(target=_drain, args=(proc.stderr,), name="realdmd-video-log",
                                 daemon=True).start()
            return proc, first
        last = (proc.stderr.read() if proc.stderr is not None else b"").decode(
            errors="replace").strip().splitlines()
        _end(proc)
        return last[-1] if last else t("error.real_dmd_video.no_picture")

    def _run(self, proc: subprocess.Popen[bytes], frame: bytes) -> None:
        try:
            while not self._stopped.is_set():
                self._controller.send_rgb_frame(frame)
                assert proc.stdout is not None
                frame = proc.stdout.read(self._frame_bytes)
                if len(frame) < self._frame_bytes:
                    _end(proc)
                    opened = self._open()
                    if isinstance(opened, str):
                        logger.debug("Real DMD video stopped at its end: %s", opened)
                        return
                    proc, frame = opened
        except Exception:
            if not self._stopped.is_set():
                logger.exception("Real DMD video stopped")
        finally:
            _end(proc)


def _drain(pipe: Any) -> None:
    """FFmpeg's complaints are read as they come, so a file that makes one per frame cannot
    fill the pipe and stall the video."""
    try:
        while pipe.read(4096):
            pass
    except (OSError, ValueError):
        pass


def _kill(proc: subprocess.Popen[bytes]) -> None:
    try:
        if proc.poll() is None:
            proc.kill()
    except OSError:
        pass


def _end(proc: subprocess.Popen[bytes]) -> None:
    _kill(proc)
    try:
        proc.wait(timeout=5)
    except Exception:
        pass
    for pipe in (proc.stdout, proc.stderr):
        if pipe is not None:
            pipe.close()


def _resolve_display_image_path(image_path: str | Path | None) -> Path | None:
    resolved_path = Path(image_path).expanduser().resolve() if image_path else None
    if resolved_path is not None and resolved_path.exists():
        return resolved_path
    if _DEFAULT_REALDMD_IMAGE.exists():
        return _DEFAULT_REALDMD_IMAGE
    return None


def is_running() -> bool:
    with _LOCK:
        return _CONTROLLER is not None


def start_libdmdutil_service_if_enabled(iniconfig: ConfigStore) -> bool:
    global _CONTROLLER, _CURRENT_IMAGE
    if not _is_enabled(iniconfig):
        return False

    with _LOCK:
        if _CONTROLLER is not None:
            return False

        controller_class, module_dir = _load_controller_class()
        if controller_class is None:
            return False

        kwargs = _build_controller_kwargs(iniconfig)
        try:
            controller = controller_class(**kwargs)
            info = controller.load()
            _CONTROLLER = controller
            _CURRENT_IMAGE = None
            logger.info(
                "libdmdutil service started from %s with %s",
                module_dir,
                kwargs or {'auto': True},
            )
            if info:
                logger.info("libdmdutil device info: %s", info)
            return True
        except Exception as e:
            _CONTROLLER = None
            _CURRENT_IMAGE = None
            logger.error("Failed to start libdmdutil service: %s", e)
            return False


def stop_libdmdutil_service(clear: bool = False) -> bool:
    global _CONTROLLER, _CURRENT_IMAGE
    _stop_player()
    with _LOCK:
        controller = _CONTROLLER
        if controller is None:
            return True
        _CONTROLLER = None
        _CURRENT_IMAGE = None

    try:
        try:
            controller.stop(clear=clear)
        except Exception:
            if clear:
                try:
                    controller.clear()
                except Exception:
                    pass
        controller.unload()
        logger.info("libdmdutil service stopped.")
        return True
    except Exception as e:
        logger.error("Error while stopping libdmdutil service: %s", e)
        return False


def restart_libdmdutil_service_if_enabled(iniconfig: ConfigStore) -> bool:
    stop_libdmdutil_service(clear=False)
    return start_libdmdutil_service_if_enabled(iniconfig)


def _stop_player() -> None:
    global _PLAYER
    with _LOCK:
        player, _PLAYER = _PLAYER, None
    if player is not None:
        player.stop()


def _controller_for(iniconfig: ConfigStore) -> Any:
    with _LOCK:
        controller = _CONTROLLER
    if controller is None:
        started = start_libdmdutil_service_if_enabled(iniconfig)
        if not started and not is_running():
            return None
        with _LOCK:
            controller = _CONTROLLER
    return controller


def show_image(iniconfig: ConfigStore, image_path: str | Path | None) -> bool:
    global _CURRENT_IMAGE
    if not _is_enabled(iniconfig):
        return False

    resolved_path = _resolve_display_image_path(image_path)

    _stop_player()
    controller = _controller_for(iniconfig)
    if controller is None:
        return False
    with _LOCK:
        current_image = _CURRENT_IMAGE

    try:
        if resolved_path is None:
            controller.clear()
            with _LOCK:
                _CURRENT_IMAGE = None
            logger.debug("Cleared libdmdutil display because image was missing.")
            return False

        image_str = str(resolved_path)
        if current_image == image_str:
            return True

        controller.hold_image(image_str)
        with _LOCK:
            _CURRENT_IMAGE = image_str
        logger.debug("Displayed real DMD image: %s", image_str)
        return True
    except Exception as e:
        logger.error("Failed to display real DMD image %s: %s", resolved_path, e)
        return False


def ffmpeg_path() -> Path | None:
    found = tools.resolve(tools.FFMPEG)
    return found.path if found.state == tools.State.FOUND else None


def _frame_command(ffmpeg: Path, video: str, width: int, height: int) -> list[str]:
    """Raw RGB at the panel's size, read at the file's own frame rate."""
    return [str(ffmpeg), "-hide_banner", "-nostdin", "-loglevel", "error", "-re",
            "-i", video, "-map", "0:v:0", "-an", "-sn",
            "-vf", f"scale={width}:{height}", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"]


def _start_player(controller: Any, video: str) -> tuple[_VideoPlayer | None, str | None]:
    """The player now playing, or why it cannot. `(None, None)` when something else took
    the panel while the video was opening, which is then not this video's to show."""
    global _PLAYER
    ffmpeg = ffmpeg_path()
    if ffmpeg is None:
        return None, t("error.real_dmd_video.no_ffmpeg")
    try:
        info = controller.info()
        width, height = int(info.width), int(info.height)
    except Exception:
        logger.debug("Asking the panel its size failed", exc_info=True)
        return None, t("error.real_dmd_video.size_unknown")
    if width < 1 or height < 1:
        return None, t("error.real_dmd_video.size_unknown")
    player = _VideoPlayer(controller, video, _frame_command(ffmpeg, video, width, height),
                          width * height * 3)
    # Held while it opens: a launch or the next selection stops it through `_stop_player`,
    # and the first frame can take as long as the file's disk does.
    with _LOCK:
        _PLAYER = player
    reason = player.start()
    with _LOCK:
        if _PLAYER is not player or _CONTROLLER is not controller:
            if _PLAYER is player:
                _PLAYER = None
            superseded = True
        else:
            superseded = False
            if reason is not None:
                _PLAYER = None
    if superseded:
        player.stop()
        return None, None
    return (player, "") if reason is None else (None, reason)


def show_video(iniconfig: ConfigStore, video_path: str | Path,
               image_path: str | Path | None = None) -> bool:
    """Play a video on the panel until the next thing is shown, and the picture in its
    place where it cannot be played."""
    global _CURRENT_IMAGE
    if not _is_enabled(iniconfig):
        return False

    video = str(Path(video_path).expanduser())
    with _LOCK:
        playing = _PLAYER
    if playing is not None and playing.path == video and playing.playing:
        return True

    _stop_player()
    controller = _controller_for(iniconfig)
    if controller is None:
        return False
    player, reason = _start_player(controller, video)
    if player is None and reason is None:
        return True
    if player is None:
        if video not in _REPORTED:
            _REPORTED.add(video)
            logger.warning("Real DMD video %s is not played, so the picture shows: %s",
                           video, reason)
        return show_image(iniconfig, image_path)
    with _LOCK:
        _CURRENT_IMAGE = None
    logger.debug("Playing real DMD video: %s", video)
    return True
