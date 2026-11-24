"""Windows, macOS and X11: every screen through FFmpeg's own capture input - `ddagrab`
(`gdigrab` where the FFmpeg has no `ddagrab`), `avfoundation` and `x11grab`."""

from __future__ import annotations

import logging
import subprocess
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from common.failures import why as why_failed
from common.host import tools

from .. import commands, geometry
from . import (
    SCREEN_PERMISSION,
    SOUND_LOOPBACK,
    SOUND_NOT_YET,
    AsleepError,
    Output,
    Screen,
    Window,
)
from .wlr import vaapi_node

logger = logging.getLogger("vpinfe.common.capture.adapters.ffmpeg")

Run = Callable[..., Any]

# By FFmpeg and encoder, for the life of the process.
_encodes: dict[tuple[str, str], bool] = {}


def encode_probe(ffmpeg: Path, encoder: str) -> list[str]:
    """One frame through `encoder`, to nowhere."""
    return [str(ffmpeg), "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "color=c=black:s=256x256:d=0.1",
            "-frames:v", "1", "-c:v", encoder, "-f", "null", "-"]


def encodes(ffmpeg: tools.Found, encoder: str, run: Run = subprocess.run) -> bool:
    """Whether a frame goes through `encoder` on this device."""
    if ffmpeg.path is None or ffmpeg.probe is None \
            or not ffmpeg.probe.has(tools.ENCODERS, encoder):
        return False
    key = (str(ffmpeg.path), encoder)
    if key not in _encodes:
        try:
            done = run(encode_probe(ffmpeg.path, encoder), capture_output=True,
                       stdin=subprocess.DEVNULL, timeout=tools.TIMEOUT, check=False,
                       creationflags=tools.NO_WINDOW)
            _encodes[key] = done.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            _encodes[key] = False
    return _encodes[key]


class FfmpegAdapter:
    """What the three have in common: FFmpeg alone reads and records every screen."""

    id = ""
    picture_tool = tools.FFMPEG
    video_tool = tools.FFMPEG
    display: str | None = None

    def requirements(self) -> tuple[tools.Tool, ...]:
        return (tools.FFMPEG,)

    def outputs(self) -> list[Output]:
        raise NotImplementedError

    def windows(self) -> list[Window]:
        """Not asked here: the app's own settings and VPinFE's screens place each window."""
        return []

    def grabs(self, ffmpeg: tools.Found) -> bool:
        return ffmpeg.probe is not None and ffmpeg.probe.has(tools.INPUTS, self.id)

    def lacks(self, found: Mapping[str, tools.Found]) -> tuple[tools.Tool, str] | None:
        return None

    def refused(self) -> tuple[str, Mapping[str, str]] | None:
        return None

    def begin(self, screens: Mapping[str, Output]) -> dict[str, Screen]:
        return {window: Screen(window, output) for window, output in screens.items()}

    def lend(self, output: Output) -> Output:
        return output

    def end(self) -> None:
        pass

    def no_sound(self) -> tuple[str, Mapping[str, str]] | None:
        return None

    def hardware(self, ffmpeg: tools.Found) -> str:
        return ""

    def at_once(self, ffmpeg: tools.Found) -> bool:
        return bool(self.hardware(ffmpeg))

    def still(self, found: Mapping[str, tools.Found], output: Output,
              dest: Path) -> list[str]:
        return [str(found[tools.FFMPEG.id].path), "-hide_banner", "-loglevel", "error", "-y",
                *commands.inputs(self.id, output, self.display),
                "-frames:v", "1", "-update", "1", str(dest)]

    def still_turn(self, output: Output) -> geometry.Turn:
        return geometry.NONE

    def recording_turn(self, output: Output) -> geometry.Turn:
        return geometry.NONE


# --- X11 -------------------------------------------------------------------------------

def x11_outputs(monitors: Sequence[Any]) -> list[Output]:
    """The display model's monitors."""
    return [Output(str(getattr(one, "name", "") or ""), int(one.x), int(one.y),
                   int(one.width), int(one.height), (int(one.width), int(one.height)),
                   0.0, geometry.NONE, index)
            for index, one in enumerate(monitors)]


def _display_monitors() -> Sequence[Any]:
    from common.host import display_service

    return display_service.get_display_monitors()


class X11Adapter(FfmpegAdapter):
    id = commands.X11GRAB

    def __init__(self, env: Mapping[str, str],
                 monitors: Callable[[], Sequence[Any]] = _display_monitors) -> None:
        self.env = env
        self.display = str(env.get("DISPLAY") or "")
        self._monitors = monitors

    def outputs(self) -> list[Output]:
        return x11_outputs(self._monitors())

    def hardware(self, ffmpeg: tools.Found) -> str:
        return vaapi_node(ffmpeg)


# --- Windows ---------------------------------------------------------------------------

# DXGI_MODE_ROTATION, as the turn from `ddagrab`'s frame to the screen.
_DXGI_TURNS = {2: geometry.Turn(ccw=270), 3: geometry.Turn(ccw=180), 4: geometry.Turn(ccw=90)}


@dataclass(frozen=True)
class DxgiOutput:
    """One output of the first graphics adapter, as `IDXGIOutput::GetDesc` says it."""

    index: int
    name: str
    left: int
    top: int
    right: int
    bottom: int
    attached: bool
    rotation: int


def windows_outputs(said: Sequence[DxgiOutput]) -> list[Output]:
    """`index` is `ddagrab`'s `output_idx`."""
    found = []
    for one in said:
        if not one.attached:
            continue
        turn = _DXGI_TURNS.get(one.rotation, geometry.NONE)
        width, height = one.right - one.left, one.bottom - one.top
        found.append(Output(one.name, one.left, one.top, width, height,
                            turn.size(width, height), 0.0, turn, one.index))
    return found


def dxgi_outputs() -> list[DxgiOutput]:
    """Every output of the adapter `ddagrab` opens when handed no device: DXGI's first.
    Raises OSError where DXGI cannot be asked."""
    import ctypes
    from ctypes import wintypes

    class Guid(ctypes.Structure):
        _fields_ = [("data1", ctypes.c_uint32), ("data2", ctypes.c_uint16),
                    ("data3", ctypes.c_uint16), ("data4", ctypes.c_ubyte * 8)]

    class OutputDesc(ctypes.Structure):
        _fields_ = [("device_name", wintypes.WCHAR * 32), ("desktop", wintypes.RECT),
                    ("attached", wintypes.BOOL), ("rotation", ctypes.c_uint),
                    ("monitor", wintypes.HANDLE)]

    def method(obj: ctypes.c_void_p, index: int, *argtypes: Any) -> Callable[..., int]:
        """A COM method by its place in the object's table, as C calls it."""
        table = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        call = ctypes.WINFUNCTYPE(  # type: ignore[attr-defined]
            ctypes.c_long, ctypes.c_void_p, *argtypes)(table[index])
        return lambda *args: call(obj, *args)

    try:
        dxgi = ctypes.WinDLL("dxgi")  # type: ignore[attr-defined]
    except (AttributeError, OSError) as exc:
        raise OSError from exc
    iid_factory1 = Guid(0x770AAE78, 0xF26F, 0x4DBA,
                        (ctypes.c_ubyte * 8)(0xA8, 0x29, 0x25, 0x3C, 0x83, 0xD1, 0xB3, 0x87))
    factory = ctypes.c_void_p()
    failed = dxgi.CreateDXGIFactory1(ctypes.byref(iid_factory1), ctypes.byref(factory))
    if failed:
        raise OSError(failed)
    release = 2
    adapter = ctypes.c_void_p()
    try:
        # IDXGIFactory::EnumAdapters, then IDXGIAdapter::EnumOutputs and
        # IDXGIOutput::GetDesc: each the seventh after IUnknown's three and
        # IDXGIObject's four.
        failed = method(factory, 7, ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p))(
            0, ctypes.byref(adapter))
        if failed:
            raise OSError(failed)
        found = []
        index = 0
        while True:
            output = ctypes.c_void_p()
            if method(adapter, 7, ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p))(
                    index, ctypes.byref(output)) != 0:
                break
            desc = OutputDesc()
            try:
                if method(output, 7, ctypes.POINTER(OutputDesc))(ctypes.byref(desc)) == 0:
                    rect = desc.desktop
                    found.append(DxgiOutput(index, desc.device_name, rect.left, rect.top,
                                            rect.right, rect.bottom, bool(desc.attached),
                                            desc.rotation))
            finally:
                method(output, release)()
            index += 1
        return found
    finally:
        if adapter:
            method(adapter, release)()
        method(factory, release)()


# What was asked of each FFmpeg file (path, modified, size): whether `ddagrab` can take a frame
# here, and until when that stands (None: for the life of the process).
_ddagrab_works: dict[tuple[str, int, int], tuple[bool, float | None]] = {}
_ddagrab_lock = threading.Lock()
# A failure that is not the machine's own (the lock screen, another capture program holding the
# desktop, a slow first start) can pass, so it is asked again after this many seconds.
DDAGRAB_RETRY_AFTER = 60.0


def ddagrab_probe(ffmpeg: Path) -> list[str]:
    """One frame of the first output through `ddagrab`, to nowhere."""
    return [str(ffmpeg), "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
            "ddagrab=output_idx=0:framerate=1:draw_mouse=0,hwdownload,format=bgra",
            "-frames:v", "1", "-f", "null", "-"]


def ddagrab_works(ffmpeg: tools.Found, run: Run = subprocess.run) -> bool:
    """Whether `ddagrab` takes a frame on this device. A machine without Direct3D 11 video (a
    virtual machine, an old driver) has the filter and cannot use it: that is asked once for
    a given FFmpeg file. Any other failure stands for `DDAGRAB_RETRY_AFTER` seconds."""
    if ffmpeg.path is None:
        return False
    try:
        held = ffmpeg.path.stat()
    except OSError:
        return False
    key = (str(ffmpeg.path), held.st_mtime_ns, held.st_size)
    with _ddagrab_lock:
        known = _ddagrab_works.get(key)
        if known is not None and (known[1] is None or time.monotonic() < known[1]):
            return known[0]
        stands: float | None = None
        try:
            done = run(ddagrab_probe(ffmpeg.path), capture_output=True, text=True,
                       errors="replace", stdin=subprocess.DEVNULL,
                       timeout=tools.TIMEOUT, check=False, creationflags=tools.NO_WINDOW)
            said = (done.stderr or "").strip().splitlines()
            works = done.returncode == 0
            why = "it took a frame" if works \
                else (said[-1] if said else f"it exited with {done.returncode}")
            if not works and "D3D11" not in (done.stderr or ""):
                stands = time.monotonic() + DDAGRAB_RETRY_AFTER
        except (OSError, subprocess.TimeoutExpired) as exc:
            works = False
            why = why_failed(exc)
            stands = time.monotonic() + DDAGRAB_RETRY_AFTER
        _ddagrab_works[key] = (works, stands)
        logger.info("Windows screen grab: %s, because ddagrab %s: %s",
                    commands.DDAGRAB if works else commands.GDIGRAB,
                    "works" if works else "cannot take a frame", why)
        return works


class WindowsAdapter(FfmpegAdapter):
    """`ddagrab`, Desktop Duplication, where the FFmpeg has it and a frame comes out of it;
    `gdigrab` otherwise."""

    def __init__(self, enumerate_outputs: Callable[[], Sequence[DxgiOutput]] = dxgi_outputs,
                 ffmpeg: tools.Found | None = None,
                 tries_ddagrab: Callable[[tools.Found], bool] = ddagrab_works) -> None:
        self._enumerate = enumerate_outputs
        self._ffmpeg = ffmpeg
        self._tries = tries_ddagrab

    @property
    def id(self) -> str:  # type: ignore[override]
        if self._ffmpeg is None:
            self._ffmpeg = tools.resolve(tools.FFMPEG)
        probe = self._ffmpeg.probe
        if probe is None or not probe.has(tools.INPUTS, commands.GDIGRAB):
            return commands.DDAGRAB
        if not probe.has(tools.FILTERS, commands.DDAGRAB):
            return commands.GDIGRAB
        return commands.DDAGRAB if self._tries(self._ffmpeg) else commands.GDIGRAB

    def warm(self) -> None:
        """Ask now, from a thread, so the first picture does not wait on the probe."""
        threading.Thread(target=lambda: self.id, daemon=True,
                         name="windows-grab-warm").start()

    def outputs(self) -> list[Output]:
        return windows_outputs(self._enumerate())

    def grabs(self, ffmpeg: tools.Found) -> bool:
        probe = ffmpeg.probe
        return probe is not None and (probe.has(tools.FILTERS, commands.DDAGRAB)
                                      or probe.has(tools.INPUTS, commands.GDIGRAB))

    def no_sound(self) -> tuple[str, Mapping[str, str]] | None:
        return SOUND_NOT_YET, {"desktop": "Windows"}

    def hardware(self, ffmpeg: tools.Found) -> str:
        return next((one for one in ("h264_nvenc", "h264_amf") if encodes(ffmpeg, one)), "")

    def still_turn(self, output: Output) -> geometry.Turn:
        return output.transform if self.id == commands.DDAGRAB else geometry.NONE

    def recording_turn(self, output: Output) -> geometry.Turn:
        return self.still_turn(output)


# --- macOS -----------------------------------------------------------------------------

@dataclass(frozen=True)
class MacDisplay:
    """One active display, in Core Graphics' order, which `avfoundation` numbers its
    `Capture screen N` by. The bounds are points on the desktop, top left first."""

    index: int
    x: int
    y: int
    width: int
    height: int
    pixels: tuple[int, int]
    refresh: float


def mac_outputs(said: Sequence[MacDisplay]) -> list[Output]:
    return [Output(f"Capture screen {one.index}", one.x, one.y, one.width, one.height,
                   one.pixels, one.refresh, geometry.NONE, one.index) for one in said]


def mac_displays() -> list[MacDisplay]:
    """Raises OSError where Core Graphics cannot be asked, and AsleepError where every
    display sleeps, since Core Graphics then lists none as active."""
    try:
        import Quartz
    except ImportError as exc:
        raise OSError from exc
    error, ids, _ = Quartz.CGGetActiveDisplayList(32, None, None)
    if error:
        raise OSError(error)
    if not ids:
        _, online, _ = Quartz.CGGetOnlineDisplayList(32, None, None)
        if online and all(Quartz.CGDisplayIsAsleep(one) for one in online):
            raise AsleepError
    found = []
    for index, display in enumerate(ids or ()):
        bounds = Quartz.CGDisplayBounds(display)
        mode = Quartz.CGDisplayCopyDisplayMode(display)
        pixels = (int(Quartz.CGDisplayModeGetPixelWidth(mode)),
                  int(Quartz.CGDisplayModeGetPixelHeight(mode))) if mode else (0, 0)
        found.append(MacDisplay(
            index, int(bounds.origin.x), int(bounds.origin.y), int(bounds.size.width),
            int(bounds.size.height), pixels,
            float(Quartz.CGDisplayModeGetRefreshRate(mode)) if mode else 0.0))
    return found


def screen_recording_allowed() -> bool:
    """Whether macOS lets this process record the screen, asked without prompting."""
    try:
        import Quartz
    except ImportError:
        return False
    check = getattr(Quartz, "CGPreflightScreenCaptureAccess", None)
    return bool(check()) if check is not None else True


class MacAdapter(FfmpegAdapter):
    id = commands.AVFOUNDATION

    def __init__(self, displays: Callable[[], Sequence[MacDisplay]] = mac_displays,
                 allowed: Callable[[], bool] = screen_recording_allowed) -> None:
        self._displays = displays
        self._allowed = allowed

    def outputs(self) -> list[Output]:
        return mac_outputs(self._displays())

    def refused(self) -> tuple[str, Mapping[str, str]] | None:
        return None if self._allowed() else (SCREEN_PERMISSION, {})

    def no_sound(self) -> tuple[str, Mapping[str, str]] | None:
        return SOUND_LOOPBACK, {}

    def hardware(self, ffmpeg: tools.Found) -> str:
        return "h264_videotoolbox" if encodes(ffmpeg, "h264_videotoolbox") else ""


def reset_for_tests() -> None:
    _encodes.clear()
