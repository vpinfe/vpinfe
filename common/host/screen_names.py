"""Each screen's model name.

macOS hands one over (`NSScreen.localizedName`). Windows keeps each monitor's EDID in the
registry and Linux, X11 or Wayland alike, in `/sys/class/drm`. `screeninfo` names a screen
by a device path or a connector, which is no model, so a name is looked up and matched to
the screen it belongs to.
"""

from __future__ import annotations

import logging
import re
import sys
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from common.host import edid

logger = logging.getLogger("vpinfe.common.host.screen_names")

DRM_ROOT = Path("/sys/class/drm")
_CARD = re.compile(r"card\d+-")
# `HDMI-A-1` in the kernel is `HDMI-1` to X11's RandR.
_SUBTYPE = re.compile(r"-[ab]-(?=\d)")
# EDID holds whole centimeters and a monitor reports millimeters.
_SIZE_SLACK_CM = 1


@dataclass(frozen=True)
class Source:
    """One EDID, and the connector it was read from where that is known."""
    connector: str
    edid: edid.Edid


def model_names(monitors: Sequence[Any]) -> list[str]:
    """One name per monitor, in its order, and "" where none could be read."""
    try:
        if sys.platform == "darwin":
            return mac_names(len(monitors))
        if sys.platform == "win32":
            held = windows_names(windows_monitors(), windows_registry())
            return [held.get(str(getattr(monitor, "name", "") or ""), "")
                    for monitor in monitors]
        if sys.platform.startswith("linux"):
            return matched(monitors, drm_sources())
    except Exception:  # noqa: BLE001 - an unnamed screen is drawn by its size instead
        logger.debug("Could not read the monitors' EDID", exc_info=True)
    return [""] * len(monitors)


def mac_names(count: int) -> list[str]:
    """In the order `get_mac_screens` lists them, which is `NSScreen.screens()`'s."""
    import AppKit

    names = [str(screen.localizedName() or "") for screen in AppKit.NSScreen.screens()]
    return (names + [""] * count)[:count]


def drm_sources(root: Path = DRM_ROOT) -> list[Source]:
    found = []
    for folder in sorted(root.glob("card*-*"), key=lambda one: one.name):
        try:
            parsed = edid.parse((folder / "edid").read_bytes())
        except OSError:
            continue
        if parsed is not None:
            found.append(Source(_CARD.sub("", folder.name, count=1), parsed))
    return found


def _connector(name: Any) -> str:
    return _SUBTYPE.sub("-", str(name or "").lower())


def _fits(monitor: Any, source: Source) -> bool:
    width, height = (getattr(monitor, "width_mm", None), getattr(monitor, "height_mm", None))
    if not width or not height:
        return False
    cm = (width / 10, height / 10)
    return any(abs(cm[0] - a) <= _SIZE_SLACK_CM and abs(cm[1] - b) <= _SIZE_SLACK_CM
               for a, b in ((source.edid.width_cm, source.edid.height_cm),
                            (source.edid.height_cm, source.edid.width_cm)))


def matched(monitors: Sequence[Any], sources: Iterable[Source]) -> list[str]:
    """Each monitor with the EDID of its connector, else of the one EDID its size fits.

    Connector names differ by driver (`HDMI-A-1` against `HDMI-A-0`, `DP-1` against
    `DisplayPort-0`), so a name that matches is trusted and one that does not falls back
    to the physical size. Several EDIDs fitting a size are used only when they agree on
    the name: identical panels are told apart by nothing here, and need not be.
    """
    free = list(sources)
    names: list[str | None] = [None] * len(monitors)
    for at, monitor in enumerate(monitors):
        wanted = _connector(getattr(monitor, "name", ""))
        for source in free:
            if wanted and _connector(source.connector) == wanted:
                names[at] = source.edid.name
                free.remove(source)
                break
    for at, monitor in enumerate(monitors):
        if names[at] is not None:
            continue
        fitting = [source for source in free if _fits(monitor, source)]
        agreed = {source.edid.name for source in fitting}
        if len(agreed) == 1:
            names[at] = agreed.pop()
            free.remove(fitting[0])
    return [name or "" for name in names]


# Windows keeps each monitor's EDID under Enum\DISPLAY\<hardware id>\<instance>, and a
# display device points at its instance only by the driver key both carry.
@dataclass(frozen=True)
class WindowsMonitor:
    adapter: str
    hardware_id: str
    driver: str


def windows_names(monitors: Iterable[WindowsMonitor],
                  instances: Callable[[str], Iterator[tuple[str, bytes]]]) -> Mapping[str, str]:
    """Model names keyed by the adapter name `screeninfo` reports (`\\\\.\\DISPLAY1`).

    `instances(hardware_id)` yields each registry instance's driver key and EDID.
    """
    held: dict[str, str] = {}
    for monitor in monitors:
        for driver, raw in instances(monitor.hardware_id):
            parsed = edid.parse(raw)
            if driver.lower() == monitor.driver.lower() and parsed is not None:
                held.setdefault(monitor.adapter, parsed.name)
                break
    return held


def windows_monitors() -> list[WindowsMonitor]:
    """Every monitor attached to an adapter, by `EnumDisplayDevices`."""
    if sys.platform != "win32":
        return []
    import ctypes
    from ctypes import wintypes

    class DisplayDevice(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("DeviceName", wintypes.WCHAR * 32),
                    ("DeviceString", wintypes.WCHAR * 128), ("StateFlags", wintypes.DWORD),
                    ("DeviceID", wintypes.WCHAR * 128), ("DeviceKey", wintypes.WCHAR * 128)]

    def enum(device: str | None, index: int) -> DisplayDevice | None:
        found = DisplayDevice()
        found.cb = ctypes.sizeof(DisplayDevice)
        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        return found if user32.EnumDisplayDevicesW(device, index, ctypes.byref(found), 0) else None

    monitors = []
    adapter_index = 0
    while (adapter := enum(None, adapter_index)) is not None:
        adapter_index += 1
        monitor_index = 0
        while (monitor := enum(adapter.DeviceName, monitor_index)) is not None:
            monitor_index += 1
            # MONITOR\<hardware id>\{class guid}\<number>
            parts = monitor.DeviceID.split("\\")
            if len(parts) >= 4:
                monitors.append(WindowsMonitor(adapter.DeviceName, parts[1],
                                               "\\".join(parts[2:4])))
    return monitors


def windows_registry() -> Callable[[str], Iterator[tuple[str, bytes]]]:
    def instances(hardware_id: str) -> Iterator[tuple[str, bytes]]:
        if sys.platform != "win32":
            return
        import winreg

        base = rf"SYSTEM\CurrentControlSet\Enum\DISPLAY\{hardware_id}"
        try:
            parent = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base)
        except OSError:
            return
        with parent:
            for index in range(winreg.QueryInfoKey(parent)[0]):
                name = winreg.EnumKey(parent, index)
                try:
                    with winreg.OpenKey(parent, name) as instance:
                        driver = str(winreg.QueryValueEx(instance, "Driver")[0])
                    with winreg.OpenKey(parent, rf"{name}\Device Parameters") as parameters:
                        yield driver, bytes(winreg.QueryValueEx(parameters, "EDID")[0])
                except OSError:
                    continue

    return instances
