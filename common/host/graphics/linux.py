"""Linux: the cards the kernel lists, read from sysfs. No program to install.

The listing is `/sys/class/drm` plus any PCI display device with no DRM node (NVIDIA's
driver can leave one out). AMD is read from the amdgpu files and Intel from its clock file
and from the DRM client counters in `/proc/<pid>/fdinfo`. NVIDIA goes to nvidia-smi and a
card with no PCI vendor (a Raspberry Pi's V3D) to nvtop, in `graphics.read`.

Intel has no utilization file, so its busy figure is what the processes this user can read
spent on the card since the previous sample. It is absent for the first sample and where
no process holds the card.
"""

from __future__ import annotations

import functools
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

from common.host.graphics import (
    UnreadableError,
    number,
    nvtop,
    only_present,
    pair,
    vendor_from_id,
)
from common.i18n import t

logger = logging.getLogger("vpinfe.common.host.graphics.linux")

DRM = Path("/sys/class/drm")
PCI = Path("/sys/bus/pci/devices")
PROC = Path("/proc")
PCI_IDS = (Path("/usr/share/hwdata/pci.ids"), Path("/usr/share/misc/pci.ids"))

_SOFTWARE = {"vkms", "vgem"}
_VENDOR_NAMES = {"amd": "AMD", "intel": "Intel", "nvidia": "NVIDIA"}
_PCI_ADDRESS = re.compile(r"[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-9a-f]")
_NS = 1_000_000_000

Engines = dict[str, tuple[float, float | None, float]]
_MIN_WINDOW, _MAX_WINDOW = 0.5, 10.0
_before: dict[str, tuple[float, dict[str, Engines]]] = {}
_lock = threading.Lock()


def cards() -> list[dict[str, Any]]:
    found = _listed()
    if not found:
        try:
            return nvtop.cards()
        except UnreadableError:
            return []
    _read_others(found)
    return found


def _listed() -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    seen: set[str] = set()
    nodes = [one for one in DRM.glob("card*") if re.fullmatch(r"card\d+", one.name)]
    for node in sorted(nodes, key=lambda one: int(one.name[4:])):
        card = _card(node.name, node / "device", node)
        if card is not None:
            found.append(card)
            seen.add(str(card.get("pci", "")))
    for device in sorted(PCI.glob("*")):
        if device.name in seen or not _text(device / "class").startswith("0x03"):
            continue
        card = _card(device.name, device, None)
        if card is not None:
            found.append(card)
    return found


def _card(ident: str, device: Path, node: Path | None) -> dict[str, Any] | None:
    driver = _link_name(device / "driver")
    if driver in _SOFTWARE:
        return None
    vendor_id, device_id = _hex(device / "vendor"), _hex(device / "device")
    if vendor_id is None and not any((device / "drm").glob("renderD*")):
        return None  # a display controller on a SoC: it draws no frames of its own
    vendor = "other" if vendor_id is None else vendor_from_id(vendor_id)
    where = _link_name(device)
    address = where if _PCI_ADDRESS.fullmatch(where) else ""
    card: dict[str, Any] = {"id": ident, "name": _name(vendor_id, device_id, driver),
                            "vendor": vendor, **({"pci": address} if address else {})}
    readings: dict[str, Any] = {}
    if vendor == "amd":
        readings = _amd(device)
    elif vendor == "intel":
        readings = _intel(device, node, address)
    if readings:
        card.update(readings, source="linux")
    return card


def _read_others(found: list[dict[str, Any]]) -> None:
    mine = [card for card in found if card["vendor"] == "other"]
    if not mine:
        return
    try:
        entries = [one for one in nvtop.cards() if one["vendor"] == "other"]
    except UnreadableError as exc:
        said = exc
    except Exception:  # noqa: BLE001 - a reader that breaks is a reason, not a 500
        logger.debug("Could not read with nvtop", exc_info=True)
        said = UnreadableError(t("error.graphics.tool_failed", tool=nvtop.TOOL))
    else:
        for card, entry in pair(mine, entries):
            card.update({key: value for key, value in entry.items() if key not in ("id", "pci")})
        return
    for card in mine:
        card.update(only_present({"reason": said.reason, "detail": said.detail or None,
                                  "tool": said.tool or None}))


def _amd(device: Path) -> dict[str, Any]:
    clock = _hwmon(device, "freq1_input")
    fan = _hwmon(device, "pwm1")
    return only_present({
        "busy": _number(device / "gpu_busy_percent"),
        "memory_used": _number(device / "mem_info_vram_used"),
        "memory_total": _number(device / "mem_info_vram_total"),
        "temperature": _scaled(_hwmon(device, "temp1_input"), 1000),
        "power": _scaled(_hwmon(device, "power1_average", "power1_input"), 1_000_000),
        "clock": _scaled(clock, 1_000_000) if clock is not None else _starred_clock(device),
        "fan_rpm": _hwmon(device, "fan1_input"),
        "fan_percent": None if fan is None else
        100 * fan / (_hwmon(device, "pwm1_max") or 255)})


def _starred_clock(device: Path) -> float | None:
    """The line `pp_dpm_sclk` marks with `*` is the clock in use: `1: 1500Mhz *`."""
    found = re.search(r"(\d+)\s*Mhz\s*\*", _text(device / "pp_dpm_sclk"), re.IGNORECASE)
    return float(found.group(1)) if found else None


def _intel(device: Path, node: Path | None, address: str) -> dict[str, Any]:
    clocks = [device / "tile0/gt0/freq0/cur_freq"]
    if node is not None:
        clocks = [node / "gt_cur_freq_mhz", node / "gt/gt0/rps_cur_freq_mhz", *clocks]
    clock = next((one for one in map(_number, clocks) if one is not None), None)
    return only_present({"clock": clock, "busy": _busy(address)})


def _busy(address: str) -> float | None:
    """The busiest engine's share of the time since the last sample. The baseline is shared
    by every caller of `cards()`, so a second caller moments after the first reads a window
    too short to mean anything: that, or one gone stale, reads as no figure."""
    if not address:
        return None
    clients = _clients(address)
    now = time.monotonic()
    with _lock:
        previous = _before.get(address)
        if previous is None or now - previous[0] >= _MIN_WINDOW:
            _before[address] = (now, clients)
    if previous is None or not clients:
        return None
    elapsed = now - previous[0]
    if not _MIN_WINDOW <= elapsed <= _MAX_WINDOW:
        return None
    share: dict[str, float] = {}
    for key, engines in clients.items():
        for engine, (spent, total, capacity) in engines.items():
            old = previous[1].get(key, {}).get(engine)
            if old is None:
                continue
            if total is None:
                part = (spent - old[0]) / (capacity * elapsed * _NS)
            elif old[1] is not None and total > old[1]:
                part = (spent - old[0]) / (total - old[1])
            else:
                continue
            share[engine] = share.get(engine, 0.0) + part
    return min(100.0, 100 * max(share.values())) if share else None


def _clients(address: str) -> dict[str, Engines]:
    """Each DRM client on this card by its id, which a forked or passed-on fd keeps, so a
    client held by two processes counts once."""
    found: dict[str, Engines] = {}
    for pid in (one for one in _listdir(PROC) if one.isdigit()):
        for fd in _listdir(PROC / pid / "fd"):
            try:
                if not os.readlink(PROC / pid / "fd" / fd).startswith("/dev/dri/"):
                    continue
                said = _fields((PROC / pid / "fdinfo" / fd).read_text())
            except OSError:
                continue
            if said.get("drm-pdev") == address and "drm-client-id" in said:
                found[said["drm-client-id"]] = _engines(said)
    return found


def _engines(said: dict[str, str]) -> Engines:
    """i915 counts nanoseconds; xe counts cycles out of the cycles that passed."""
    engines: Engines = {}
    for key, value in said.items():
        spent = number(value)
        if spent is None:
            continue
        if key.startswith("drm-engine-") and not key.startswith("drm-engine-capacity-"):
            name = key.removeprefix("drm-engine-")
            engines[name] = (spent, None, number(said.get(f"drm-engine-capacity-{name}")) or 1.0)
        elif key.startswith("drm-cycles-"):
            name = key.removeprefix("drm-cycles-")
            total = number(said.get(f"drm-total-cycles-{name}"))
            if total is not None:
                engines[name] = (spent, total, 1.0)
    return engines


def _fields(text: str) -> dict[str, str]:
    return {key.strip(): value.strip() for key, _, value in
            (line.partition(":") for line in text.splitlines()) if value}


def _name(vendor_id: int | None, device_id: int | None, driver: str) -> str:
    if vendor_id is None:
        return driver or t("word.unknown")
    vendor, model = _pci_names(vendor_id, device_id)
    label = _VENDOR_NAMES.get(vendor_from_id(vendor_id), vendor)
    if model:
        bracketed = re.search(r"\[(.+)\]\s*$", model)
        model = bracketed.group(1) if bracketed else model
        return model if label.lower() in model.lower() else " ".join(filter(None, (label, model)))
    ident = f"{vendor_id:04x}" + ("" if device_id is None else f":{device_id:04x}")
    return t("metrics.gpu.by_id", vendor=label, id=ident) if label else \
        t("metrics.gpu.by_number", id=ident)


@functools.cache
def _pci_names(vendor_id: int, device_id: int | None) -> tuple[str, str]:
    """The vendor and the device as `pci.ids` names them, each empty where it does not."""
    for path in PCI_IDS:
        try:
            lines = path.read_text(errors="replace").splitlines()
        except OSError:
            continue
        vendor, inside = "", False
        for line in lines:
            if not inside:
                if line.startswith(f"{vendor_id:04x}  "):
                    vendor, inside = line[6:].strip(), True
            elif not line.startswith("\t"):
                break
            elif device_id is not None and line.startswith(f"\t{device_id:04x}  "):
                return vendor, line[7:].strip()
        if vendor:
            return vendor, ""
    return "", ""


def _hwmon(device: Path, *names: str) -> float | None:
    for directory in sorted((device / "hwmon").glob("hwmon*")):
        for name in names:
            found = _number(directory / name)
            if found is not None:
                return found
    return None


def _scaled(value: float | None, divisor: float) -> float | None:
    return None if value is None else value / divisor


def _number(path: Path) -> float | None:
    return number(_text(path))


def _text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _hex(path: Path) -> int | None:
    try:
        return int(_text(path), 16)
    except ValueError:
        return None


def _link_name(path: Path) -> str:
    """What the link points at, by name; empty where there is no link."""
    return os.path.basename(os.path.realpath(path)) if path.exists() else ""


def _listdir(path: Path) -> list[str]:
    try:
        return os.listdir(path)
    except OSError:
        return []
