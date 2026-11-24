"""The USB, serial and display devices attached to this device, one inventory for every OS.

Read from what each OS already keeps, without a driver library: sysfs and `/dev` on Linux,
`system_profiler` on macOS and PowerShell's PnP list on Windows. A serial number is never read.

`accessible` is whether this process may open the device, and `None` where the OS has no
answer short of opening it.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from common.host import display_service, tools
from common.i18n import t

logger = logging.getLogger("vpinfe.common.host.hardware")

USB = "usb"
SERIAL = "serial"
DISPLAY = "display"
KINDS = (USB, SERIAL, DISPLAY)

SYSFS_USB = Path("/sys/bus/usb/devices")
SYSFS_TTY = Path("/sys/class/tty")
DEV = Path("/dev")

_HUB_CLASS = "09"
_TIMEOUT = 15

Device = dict[str, Any]


def _device(kind: str, *, vendor_id: str = "", product_id: str = "", vendor: str = "",
            product: str = "", port: str = "", accessible: bool | None = None) -> Device:
    return {"kind": kind, "vendor_id": vendor_id, "product_id": product_id,
            "vendor": vendor, "product": product, "name": product or port,
            "port": port, "accessible": accessible}


def _hex4(value: Any) -> str:
    """`0x05ac`, `05AC` or `1452` in hex, as `05ac`; "" for anything else."""
    text = str(value or "").strip().lower().removeprefix("0x")
    return text.zfill(4) if re.fullmatch(r"[0-9a-f]{1,4}", text) else ""


def list_devices(kind: str | None = None) -> list[Device]:
    """Every device of `kind` ("usb", "serial" or "display"), or of all three. Each is a
    dict of `kind`, `vendor_id`, `product_id`, `vendor`, `product`, `name`, `port` and
    `accessible`; a display adds `x`, `y`, `width` and `height`. One that cannot be
    enumerated is left out, never raised."""
    if kind is not None and kind not in KINDS:
        raise ValueError(t("hardware.bad_kind", kind=kind))
    found: list[Device] = []
    for one in (USB, SERIAL, DISPLAY):
        if kind in (None, one):
            try:
                found.extend(_READERS[one]())
            except Exception:
                logger.warning("could not list %s devices", one, exc_info=True)
    return found


def _usb() -> list[Device]:
    if sys.platform == "darwin":
        return parse_macos_usb(_run(["system_profiler", "SPUSBHostDataType", "-json"]))
    if sys.platform.startswith("linux"):
        return linux_usb(SYSFS_USB, DEV)
    if sys.platform == "win32":
        return parse_windows_usb(_run(_POWERSHELL_USB))
    return []


def _serial() -> list[Device]:
    if sys.platform == "darwin":
        return macos_serial(DEV)
    if sys.platform.startswith("linux"):
        return linux_serial(SYSFS_TTY, DEV)
    if sys.platform == "win32":
        return parse_windows_serial(_run(_POWERSHELL_SERIAL))
    return []


def _displays() -> list[Device]:
    names = display_service.get_display_names()
    shown = []
    for index, monitor in enumerate(display_service.monitors_as_dicts()):
        model = names[index] if index < len(names) else ""
        shown.append({**_device(DISPLAY, product=model or monitor["name"]),
                      **{key: monitor[key] for key in ("x", "y", "width", "height")}})
    return shown


_READERS = {USB: _usb, SERIAL: _serial, DISPLAY: _displays}


def _run(command: list[str]) -> str:
    done = subprocess.run(command, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=_TIMEOUT, check=False,
                          creationflags=tools.NO_WINDOW)
    return done.stdout


# --- macOS ----------------------------------------------------------------

def parse_macos_usb(text: str) -> list[Device]:
    """`system_profiler SPUSBHostDataType -json`: devices nest under their hub, which is
    left out, and under the controllers, which carry no ids."""
    try:
        roots = json.loads(text).get("SPUSBHostDataType", [])
    except (ValueError, AttributeError):
        return []
    found: list[Device] = []

    def walk(items: list[dict[str, Any]]) -> None:
        for item in items:
            children = item.get("_items") or []
            vendor_id = _hex4(item.get("USBDeviceKeyVendorID"))
            if vendor_id and not children:
                found.append(_device(
                    USB, vendor_id=vendor_id,
                    product_id=_hex4(item.get("USBDeviceKeyProductID")),
                    vendor=str(item.get("USBDeviceKeyVendorName") or ""),
                    product=str(item.get("_name") or "")))
            walk(children)

    walk(roots)
    return found


# The names the OS and the common USB-serial drivers (FTDI, Silicon Labs, WCH) give a port.
_MACOS_SERIAL = ("cu.usb*", "cu.SLAB_USBtoUART*", "cu.wchusbserial*")


def macos_serial(dev: Path) -> list[Device]:
    ports = sorted(path for pattern in _MACOS_SERIAL for path in dev.glob(pattern))
    return [_device(SERIAL, port=str(path), accessible=os.access(path, os.R_OK | os.W_OK))
            for path in ports]


# --- Linux ----------------------------------------------------------------

def _text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""


def _usb_device(directory: Path, dev: Path) -> Device | None:
    vendor_id = _hex4(_text(directory / "idVendor"))
    if not vendor_id or _text(directory / "bDeviceClass") == _HUB_CLASS:
        return None
    bus, number = (_text(directory / "busnum"), _text(directory / "devnum"))
    node = dev / "bus" / "usb" / bus.zfill(3) / number.zfill(3)
    return _device(USB, vendor_id=vendor_id, product_id=_hex4(_text(directory / "idProduct")),
                   vendor=_text(directory / "manufacturer"),
                   product=_text(directory / "product"),
                   accessible=os.access(node, os.R_OK | os.W_OK) if node.exists() else None)


def linux_usb(sysfs: Path, dev: Path) -> list[Device]:
    """Every entry in sysfs with an `idVendor` is a device; the ones named `1-2:1.0` are its
    interfaces and carry none."""
    try:
        directories = sorted(sysfs.iterdir())
    except OSError:
        return []
    return [one for one in (_usb_device(path, dev) for path in directories) if one]


def _usb_parent(start: Path) -> Path | None:
    for directory in (start, *start.parents):
        if (directory / "idVendor").exists():
            return directory
    return None


def linux_serial(sysfs: Path, dev: Path) -> list[Device]:
    """The USB serial ports (`ttyUSB*`, `ttyACM*`), named by the USB device behind each."""
    found = []
    for path in sorted(dev.glob("ttyUSB*")) + sorted(dev.glob("ttyACM*")):
        parent = None
        try:
            parent = _usb_parent((sysfs / path.name / "device").resolve())
        except OSError:
            pass
        found.append(_device(
            SERIAL, port=str(path),
            vendor_id=_hex4(_text(parent / "idVendor")) if parent else "",
            product_id=_hex4(_text(parent / "idProduct")) if parent else "",
            vendor=_text(parent / "manufacturer") if parent else "",
            product=_text(parent / "product") if parent else "",
            accessible=os.access(path, os.R_OK | os.W_OK)))
    return found


# --- Windows --------------------------------------------------------------

_UTF8 = "[Console]::OutputEncoding = [Text.Encoding]::UTF8; "
_POWERSHELL_USB = [
    "powershell", "-NoProfile", "-Command",
    _UTF8 + r"Get-PnpDevice -PresentOnly | Where-Object { $_.InstanceId -like 'USB\VID_*' } | "
    "Select-Object FriendlyName,Manufacturer,InstanceId,Service | ConvertTo-Json -Compress"]
_POWERSHELL_SERIAL = [
    "powershell", "-NoProfile", "-Command",
    _UTF8 + "Get-PnpDevice -Class Ports -PresentOnly | "
    "Select-Object FriendlyName,Manufacturer,InstanceId | ConvertTo-Json -Compress"]

_INSTANCE_IDS = re.compile(r"VID_([0-9A-Fa-f]{4})[&+]PID_([0-9A-Fa-f]{4})")
_COM_PORT = re.compile(r"\((COM\d+)\)")


def _powershell_rows(text: str) -> Iterator[dict[str, Any]]:
    """One object comes back bare and several as a list."""
    try:
        rows = json.loads(text) if text.strip() else []
    except ValueError:
        return
    yield from (row for row in ([rows] if isinstance(rows, dict) else rows)
                if isinstance(row, dict))


def _windows_manufacturer(row: dict[str, Any]) -> str:
    """Windows fills this with `(Standard system devices)` where it has no name."""
    named = str(row.get("Manufacturer") or "")
    return "" if named.startswith("(") else named


def parse_windows_usb(text: str) -> list[Device]:
    """`USB\\VID_xxxx&PID_xxxx\\<serial>` is a device; the same ids with `&MI_nn` are one of
    its interfaces, and a hub is the one served by `usbhub`. The part after the ids, which can
    be the serial number, is dropped."""
    found = []
    for row in _powershell_rows(text):
        instance = str(row.get("InstanceId") or "")
        ids = _INSTANCE_IDS.search(instance)
        name = str(row.get("FriendlyName") or "")
        if not ids or "&MI_" in instance.upper() \
                or str(row.get("Service") or "").lower().startswith("usbhub"):
            continue
        found.append(_device(USB, vendor_id=ids.group(1).lower(),
                             product_id=ids.group(2).lower(),
                             vendor=_windows_manufacturer(row), product=name))
    return found


def parse_windows_serial(text: str) -> list[Device]:
    found = []
    for row in _powershell_rows(text):
        name = str(row.get("FriendlyName") or "")
        port = _COM_PORT.search(name)
        if not port:
            continue
        ids = _INSTANCE_IDS.search(str(row.get("InstanceId") or ""))
        if not ids:
            continue
        found.append(_device(
            SERIAL, port=port.group(1), product=name.replace(port.group(0), "").strip(),
            vendor_id=ids.group(1).lower(), product_id=ids.group(2).lower(),
            vendor=_windows_manufacturer(row)))
    return found


def access_help_key() -> str:
    if sys.platform == "darwin":
        return "hardware.access.macos"
    if sys.platform == "win32":
        return "hardware.access.windows"
    return "hardware.access.linux"
