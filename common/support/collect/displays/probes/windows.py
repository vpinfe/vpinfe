"""Windows: the display adapters and their current modes, read through `ctypes`.

Asks `EnumDisplayDevicesW` which adapters are on the desktop, `EnumDisplaySettingsExW` for
each one's current mode, and `GetDpiForMonitor` for its scale. No PowerShell, no WMI, and a
monitor's `DeviceID` is never read: it holds the hardware id, so only its model string is.
"""

from __future__ import annotations

import dataclasses
import platform
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from common.capture import geometry

from ..model import OK, Reading, ScreenFact
from ..run import Run

SOURCE = "Windows"

ATTACHED = 0x1
PRIMARY = 0x4
MIRRORING_DRIVER = 0x8
CURRENT_MODE = -1
EFFECTIVE_DPI = 0
BASE_DPI = 96
WINDOWS_11_BUILD = 22000

# `GetAwarenessFromDpiAwarenessContext`: what scale Windows tells this program about. Only a
# per-monitor program is told each screen's own.
UNAWARE = 0
SYSTEM_AWARE = 1


@dataclass(frozen=True)
class Mode:
    x: int
    y: int
    width: int
    height: int
    hz: int
    # `dmDisplayOrientation`: 0, 1, 2 or 3 for 0, 90, 180 or 270 degrees.
    orientation: int


@dataclass(frozen=True)
class Device:
    name: str
    flags: int
    # The monitor's model string as Windows lists it, "" where it lists none.
    monitor: str
    mode: Mode | None
    dpi: int | None


@dataclass(frozen=True)
class Snapshot:
    devices: tuple[Device, ...]
    awareness: int | None = None


Reader = Callable[[], Snapshot]
Release = Callable[[], tuple[str, str, str, str]]


def screens(devices: tuple[Device, ...]) -> list[ScreenFact]:
    """One screen for each adapter on the desktop with a mode. An adapter that shows the
    same place and size as an earlier one is a copy of it, which is what Duplicate is."""
    found: list[ScreenFact] = []
    for device in devices:
        if not device.flags & ATTACHED or device.flags & MIRRORING_DRIVER or device.mode is None:
            continue
        mode = device.mode
        same = next((one for one in found if (one.x, one.y, one.width, one.height)
                     == (mode.x, mode.y, mode.width, mode.height)), None)
        degrees = mode.orientation * 90
        found.append(ScreenFact(
            connector=device.name, model=device.monitor or None,
            x=mode.x, y=mode.y, width=mode.width, height=mode.height,
            scale=device.dpi / BASE_DPI if device.dpi else None,
            refresh_hz=float(mode.hz) if mode.hz > 1 else None,
            turn=geometry.degrees_turn(degrees), said=f"{degrees}°",
            primary=bool(device.flags & PRIMARY), enabled=True,
            mirrors=same.connector if same is not None else None, source=SOURCE))
    return found


def release_line(release: str, version: str) -> str:
    """`11, build 22631` from `platform.win32_ver`, which says `10` for both."""
    build = version.split(".")[2] if version.count(".") >= 2 else ""
    name = "11" if build.isdigit() and int(build) >= WINDOWS_11_BUILD else release
    return f"Windows {name}" + (f", build {build}" if build else "") + " (DWM)"


def probe(env: Mapping[str, str], run: Run, read: Reader | None = None,
          release: Release = platform.win32_ver) -> Reading:
    try:
        snapshot = (read or _read)()
    except Exception:
        return Reading.failed(SOURCE, "Windows would not say")
    found = screens(snapshot.devices)
    told_each = snapshot.awareness not in (UNAWARE, SYSTEM_AWARE)
    if not told_each:
        found = [dataclasses.replace(one, scale=None) for one in found]
    facts = [("Desktop", release_line(*release()[:2]))]
    if len(found) > 1:
        copies = any(one.mirrors for one in found)
        facts.append(("Multiple displays", "Duplicated" if copies else "Extended"))
    notes = []
    if not told_each:
        notes.append("VPinFE is not told each screen's own scale here, so the scale of "
                     "each screen is Not tested")
    return Reading(SOURCE, OK, screens=tuple(found), facts=tuple(facts), notes=tuple(notes))


def _read() -> Snapshot:
    """The adapters on this desktop. Raises `OSError` where Windows will not answer."""
    if sys.platform != "win32":
        raise OSError("not Windows")
    import ctypes
    from ctypes import wintypes

    class DisplayDevice(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("DeviceName", wintypes.WCHAR * 32),
                    ("DeviceString", wintypes.WCHAR * 128), ("StateFlags", wintypes.DWORD),
                    ("DeviceID", wintypes.WCHAR * 128), ("DeviceKey", wintypes.WCHAR * 128)]

    # DEVMODEW as the display variant of its union reads it, 220 bytes.
    class DevMode(ctypes.Structure):
        _fields_ = [("dmDeviceName", wintypes.WCHAR * 32), ("dmSpecVersion", wintypes.WORD),
                    ("dmDriverVersion", wintypes.WORD), ("dmSize", wintypes.WORD),
                    ("dmDriverExtra", wintypes.WORD), ("dmFields", wintypes.DWORD),
                    ("dmPositionX", wintypes.LONG), ("dmPositionY", wintypes.LONG),
                    ("dmDisplayOrientation", wintypes.DWORD),
                    ("dmDisplayFixedOutput", wintypes.DWORD),
                    ("dmColor", ctypes.c_short), ("dmDuplex", ctypes.c_short),
                    ("dmYResolution", ctypes.c_short), ("dmTTOption", ctypes.c_short),
                    ("dmCollate", ctypes.c_short), ("dmFormName", wintypes.WCHAR * 32),
                    ("dmLogPixels", wintypes.WORD), ("dmBitsPerPel", wintypes.DWORD),
                    ("dmPelsWidth", wintypes.DWORD), ("dmPelsHeight", wintypes.DWORD),
                    ("dmDisplayFlags", wintypes.DWORD), ("dmDisplayFrequency", wintypes.DWORD),
                    ("dmICMMethod", wintypes.DWORD), ("dmICMIntent", wintypes.DWORD),
                    ("dmMediaType", wintypes.DWORD), ("dmDitherType", wintypes.DWORD),
                    ("dmReserved1", wintypes.DWORD), ("dmReserved2", wintypes.DWORD),
                    ("dmPanningWidth", wintypes.DWORD), ("dmPanningHeight", wintypes.DWORD)]

    # Private handles, so the types set below are not set on the copy the rest of the process
    # shares.
    user32 = ctypes.WinDLL("user32")  # type: ignore[attr-defined]
    user32.EnumDisplayDevicesW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD,
                                           ctypes.POINTER(DisplayDevice), wintypes.DWORD]
    user32.EnumDisplayDevicesW.restype = wintypes.BOOL
    user32.EnumDisplaySettingsExW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD,
                                              ctypes.POINTER(DevMode), wintypes.DWORD]
    user32.EnumDisplaySettingsExW.restype = wintypes.BOOL
    user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
    user32.MonitorFromPoint.restype = wintypes.HANDLE
    shcore = ctypes.WinDLL("shcore")  # type: ignore[attr-defined]
    shcore.GetDpiForMonitor.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                        ctypes.POINTER(wintypes.UINT),
                                        ctypes.POINTER(wintypes.UINT)]
    shcore.GetDpiForMonitor.restype = ctypes.c_long

    def enum(name: str | None, index: int) -> DisplayDevice | None:
        found = DisplayDevice()
        found.cb = ctypes.sizeof(DisplayDevice)
        return found if user32.EnumDisplayDevicesW(name, index, ctypes.byref(found), 0) else None

    def mode_of(name: str) -> Mode | None:
        found = DevMode()
        found.dmSize = ctypes.sizeof(DevMode)
        if not user32.EnumDisplaySettingsExW(name, CURRENT_MODE, ctypes.byref(found), 0):
            return None
        return Mode(found.dmPositionX, found.dmPositionY, found.dmPelsWidth,
                    found.dmPelsHeight, found.dmDisplayFrequency, found.dmDisplayOrientation)

    def dpi_at(mode: Mode) -> int | None:
        try:
            monitor = user32.MonitorFromPoint(wintypes.POINT(mode.x + 1, mode.y + 1), 0)
            across, down = wintypes.UINT(), wintypes.UINT()
            said = shcore.GetDpiForMonitor(monitor, EFFECTIVE_DPI, ctypes.byref(across),
                                           ctypes.byref(down))
        except Exception:
            return None
        return int(across.value) if monitor and said == 0 and across.value else None

    devices = []
    index = 0
    while (adapter := enum(None, index)) is not None:
        index += 1
        mode = mode_of(adapter.DeviceName) if adapter.StateFlags & ATTACHED else None
        monitor = enum(adapter.DeviceName, 0)
        devices.append(Device(adapter.DeviceName, int(adapter.StateFlags),
                              monitor.DeviceString if monitor is not None else "", mode,
                              dpi_at(mode) if mode is not None else None))
    return Snapshot(tuple(devices), _awareness(user32))


def _awareness(user32: Any) -> int | None:
    """Which of the awareness levels this thread runs at, None where Windows is older than the
    call (before 10 version 1607)."""
    import ctypes
    from ctypes import wintypes

    try:
        user32.GetThreadDpiAwarenessContext.restype = wintypes.HANDLE
        user32.GetAwarenessFromDpiAwarenessContext.argtypes = [wintypes.HANDLE]
        user32.GetAwarenessFromDpiAwarenessContext.restype = ctypes.c_int
        context = user32.GetThreadDpiAwarenessContext()
        return int(user32.GetAwarenessFromDpiAwarenessContext(context))
    except Exception:
        return None
