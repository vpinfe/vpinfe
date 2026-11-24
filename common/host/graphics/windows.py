"""Windows: the graphics adapters Windows lists, read with no program to install.

D3DKMT (gdi32) lists the adapters and gives each its name, its kind and the temperature and
fan its driver reports. The performance counters (PDH) say what each is doing: busy as Task
Manager reads it, and the memory in use. NVIDIA goes to nvidia-smi in `graphics.read`.

Busy is a rate, so the counters are held between samples. It is absent for the first sample
and after a long gap, and a second caller moments after the first gets the answer the first
got.
"""

from __future__ import annotations

import ctypes
import functools
import logging
import re
import threading
import time
from typing import Any

from common.failures import why
from common.host.graphics import UnreadableError, only_present, vendor_from_name
from common.i18n import t

logger = logging.getLogger("vpinfe.common.host.graphics.windows")

_GET_SEGMENT_SIZE, _ADAPTER_ADDRESS, _REGISTRY_INFO, _ADAPTER_TYPE, _PERF_DATA = 3, 6, 8, 15, 62
_RENDER, _SOFTWARE, _INDIRECT, _COMPUTE_ONLY = 1 << 0, 1 << 2, 1 << 6, 1 << 11
_DISCRETE_MEMORY = 1 << 30
_MIN_WINDOW, _MAX_WINDOW = 0.5, 10.0
_READINGS = ("busy", "memory_used", "memory_total", "temperature", "fan_rpm")

_FMT_DOUBLE, _MORE_DATA = 0x200, 0x800007D2
_PATHS = {"engine": r"\GPU Engine(*)\Utilization Percentage",
          "dedicated": r"\GPU Adapter Memory(*)\Dedicated Usage",
          "shared": r"\GPU Adapter Memory(*)\Shared Usage"}
_LUID = re.compile(r"luid_0x([0-9a-f]{8})_0x([0-9a-f]{8})", re.IGNORECASE)
_PROCESS = re.compile(r"^pid_\d+_")

Luid = tuple[int, int]
Counters = dict[str, dict[str, float]]


class _AdapterInfo(ctypes.Structure):
    _fields_ = [("handle", ctypes.c_uint32), ("luid_low", ctypes.c_uint32),
                ("luid_high", ctypes.c_int32), ("sources", ctypes.c_uint32),
                ("precise_present", ctypes.c_int32)]


class _EnumAdapters(ctypes.Structure):
    _fields_ = [("count", ctypes.c_uint32), ("adapters", ctypes.POINTER(_AdapterInfo))]


class _QueryAdapterInfo(ctypes.Structure):
    _fields_ = [("handle", ctypes.c_uint32), ("kind", ctypes.c_int32),
                ("data", ctypes.c_void_p), ("size", ctypes.c_uint32)]


class _CloseAdapter(ctypes.Structure):
    _fields_ = [("handle", ctypes.c_uint32)]


class _RegistryInfo(ctypes.Structure):
    _fields_ = [("adapter", ctypes.c_wchar * 260), ("bios", ctypes.c_wchar * 260),
                ("dac", ctypes.c_wchar * 260), ("chip", ctypes.c_wchar * 260)]


class _AdapterAddress(ctypes.Structure):
    _fields_ = [("bus", ctypes.c_uint32), ("device", ctypes.c_uint32),
                ("function", ctypes.c_uint32)]


class _AdapterType(ctypes.Structure):
    _fields_ = [("flags", ctypes.c_uint32)]


class _SegmentSizes(ctypes.Structure):
    _fields_ = [("video", ctypes.c_uint64), ("system", ctypes.c_uint64),
                ("shared", ctypes.c_uint64)]


class _PerfData(ctypes.Structure):
    _fields_ = [("index", ctypes.c_uint32), ("memory_frequency", ctypes.c_uint64),
                ("max_memory_frequency", ctypes.c_uint64),
                ("max_memory_frequency_oc", ctypes.c_uint64),
                ("memory_bandwidth", ctypes.c_uint64), ("pcie_bandwidth", ctypes.c_uint64),
                ("fan_rpm", ctypes.c_uint32), ("power", ctypes.c_uint32),
                ("temperature", ctypes.c_uint32), ("power_state_override", ctypes.c_ubyte)]


class _CounterItem(ctypes.Structure):
    _fields_ = [("name", ctypes.c_void_p), ("status", ctypes.c_uint32),
                ("value", ctypes.c_double)]


@functools.cache
def _gdi32() -> Any:
    gdi = ctypes.WinDLL("gdi32")  # type: ignore[attr-defined]
    for name, argument in (("D3DKMTEnumAdapters2", _EnumAdapters),
                           ("D3DKMTQueryAdapterInfo", _QueryAdapterInfo),
                           ("D3DKMTCloseAdapter", _CloseAdapter)):
        function = getattr(gdi, name)
        function.argtypes = [ctypes.POINTER(argument)]
        function.restype = ctypes.c_int32
    return gdi


@functools.cache
def _pdh() -> Any:
    pdh = ctypes.WinDLL("pdh")  # type: ignore[attr-defined]
    handle, text, size = ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_size_t
    for name, arguments in (
            ("PdhOpenQueryW", [text, size, ctypes.POINTER(handle)]),
            ("PdhAddEnglishCounterW", [handle, text, size, ctypes.POINTER(handle)]),
            ("PdhCollectQueryData", [handle]),
            ("PdhGetFormattedCounterArrayW", [handle, ctypes.c_uint32,
                                              ctypes.POINTER(ctypes.c_uint32),
                                              ctypes.POINTER(ctypes.c_uint32),
                                              ctypes.c_void_p])):
        function = getattr(pdh, name)
        function.argtypes = arguments
        function.restype = ctypes.c_uint32
    return pdh


class _Query:
    """One PDH query holding a wildcard counter for each path in `_PATHS`. A counter this
    Windows does not have is left out."""

    def __init__(self, pdh: Any) -> None:
        self.pdh = pdh
        self.handle = ctypes.c_void_p()
        status = pdh.PdhOpenQueryW(None, 0, ctypes.pointer(self.handle))
        if status != 0:
            raise RuntimeError(f"PdhOpenQueryW {status:#x}")
        self.counters: dict[str, ctypes.c_void_p] = {}
        for name, path in _PATHS.items():
            counter = ctypes.c_void_p()
            if pdh.PdhAddEnglishCounterW(self.handle, path, 0, ctypes.pointer(counter)) == 0:
                self.counters[name] = counter

    def collect(self) -> None:
        status = self.pdh.PdhCollectQueryData(self.handle)
        if status != 0:
            raise RuntimeError(f"PdhCollectQueryData {status:#x}")

    def values(self, name: str) -> dict[str, float] | None:
        """The counter's value for each instance, by instance name. None where it could
        not be read, which is not the same as an adapter with nothing running on it."""
        counter = self.counters[name]
        size, count = ctypes.c_uint32(0), ctypes.c_uint32(0)
        ask = (counter, _FMT_DOUBLE, ctypes.pointer(size), ctypes.pointer(count))
        if self.pdh.PdhGetFormattedCounterArrayW(*ask, None) != _MORE_DATA or not size.value:
            return None
        buffer = ctypes.create_string_buffer(size.value)
        if self.pdh.PdhGetFormattedCounterArrayW(*ask, buffer) != 0:
            return None
        items = ctypes.cast(buffer, ctypes.POINTER(_CounterItem))
        found = {ctypes.wstring_at(items[index].name): items[index].value
                 for index in range(count.value)
                 if items[index].name and items[index].status in (0, 1)}
        return found or None


_lock = threading.Lock()
_held: dict[str, Any] = {}


def cards() -> list[dict[str, Any]]:
    try:
        gdi = _gdi32()
    except (OSError, AttributeError) as exc:
        raise UnreadableError(t("error.graphics.tool_failed", tool="D3DKMT"), why(exc)) from exc
    found = _listed(gdi)
    if not found:
        return []
    try:
        counters = _counters()
    except Exception:  # noqa: BLE001 - a counter that breaks is a missing reading, not a 500
        logger.debug("Could not read the performance counters", exc_info=True)
        counters = {}
    busy, dedicated, shared = (_by_adapter(counters.get(name, {}))
                               for name in ("engine", "dedicated", "shared"))
    for luid, card in found:
        if "engine" in counters:
            card["busy"] = min(100.0, max(busy.get(luid, {}).values(), default=0.0))
        used = _memory(card, dedicated.get(luid), shared.get(luid))
        if used is not None:
            card["memory_used"] = used
        if any(key in card for key in _READINGS):
            card["source"] = "windows"
    return [card for _, card in found]


def _listed(gdi: Any) -> list[tuple[Luid, dict[str, Any]]]:
    found: list[tuple[Luid, dict[str, Any]]] = []
    opened = _adapters(gdi)
    try:
        for info in opened:
            card = _card(gdi, info)
            if card is not None:
                found.append(((info.luid_high & 0xFFFFFFFF, info.luid_low), card))
    finally:
        for info in opened:
            gdi.D3DKMTCloseAdapter(ctypes.pointer(_CloseAdapter(info.handle)))
    return found


def _adapters(gdi: Any) -> list[_AdapterInfo]:
    ask = _EnumAdapters(0, None)
    if gdi.D3DKMTEnumAdapters2(ctypes.pointer(ask)) != 0 or not ask.count:
        return []
    listing = (_AdapterInfo * ask.count)()
    ask.adapters = ctypes.cast(listing, ctypes.POINTER(_AdapterInfo))
    if gdi.D3DKMTEnumAdapters2(ctypes.pointer(ask)) != 0:
        return []
    return list(listing[:ask.count])


def _card(gdi: Any, info: _AdapterInfo) -> dict[str, Any] | None:
    kind = _AdapterType()
    if _query(gdi, info.handle, _ADAPTER_TYPE, kind):
        flags = kind.flags
        if not flags & _RENDER or flags & (_SOFTWARE | _INDIRECT | _COMPUTE_ONLY):
            return None
    registry = _RegistryInfo()
    name = registry.adapter.strip() if _query(gdi, info.handle, _REGISTRY_INFO, registry) \
        else ""
    ident = f"luid_0x{info.luid_high & 0xFFFFFFFF:08x}_0x{info.luid_low:08x}"
    card: dict[str, Any] = {"id": ident,
                            "name": name or t("metrics.gpu.by_number", id=f"{info.luid_low:x}"),
                            "vendor": vendor_from_name(name)}
    place = _AdapterAddress()
    if _query(gdi, info.handle, _ADAPTER_ADDRESS, place):
        card["pci"] = f"0000:{place.bus:02x}:{place.device:02x}.{place.function:x}"
    sizes = _SegmentSizes()
    if _query(gdi, info.handle, _GET_SEGMENT_SIZE, sizes) and sizes.video >= _DISCRETE_MEMORY:
        card["memory_total"] = float(sizes.video)
    perf = _PerfData()
    if _query(gdi, info.handle, _PERF_DATA, perf):
        card.update(only_present({
            "temperature": perf.temperature / 10 if perf.temperature else None,
            "fan_rpm": float(perf.fan_rpm) if perf.fan_rpm else None}))
    return card


def _query(gdi: Any, handle: int, kind: int, data: ctypes.Structure) -> bool:
    ask = _QueryAdapterInfo(handle, kind, ctypes.addressof(data), ctypes.sizeof(data))
    return bool(gdi.D3DKMTQueryAdapterInfo(ctypes.pointer(ask)) == 0)


def _counters() -> Counters:
    with _lock:
        now = time.monotonic()
        taken = _held.get("taken")
        if taken is not None and now - taken < _MIN_WINDOW:
            return dict(_held["values"])
        query = _held.get("query")
        if query is None:
            query = _held["query"] = _Query(_pdh())
        query.collect()
        values: Counters = {name: read for name in query.counters
                            if (read := query.values(name)) is not None}
        if taken is None or now - taken > _MAX_WINDOW:
            values.pop("engine", None)
        _held["taken"], _held["values"] = now, values
        return dict(values)


def _by_adapter(instances: dict[str, float]) -> dict[Luid, dict[str, float]]:
    """Each adapter's instances by what they count, with the processes added up: a
    process is no part of what an engine is busy with, so two of them on one engine are
    one engine busy for both."""
    found: dict[Luid, dict[str, float]] = {}
    for instance, value in instances.items():
        luid = _LUID.search(instance)
        if luid is not None:
            key = (int(luid.group(1), 16), int(luid.group(2), 16))
            engine = _PROCESS.sub("", instance)
            found.setdefault(key, {})[engine] = found.get(key, {}).get(engine, 0.0) + value
    return found


def _memory(card: dict[str, Any], dedicated: dict[str, float] | None,
            shared: dict[str, float] | None) -> float | None:
    """What the card has in use. With a total, the card's own memory; without one, the
    system's memory it is using."""
    own = None if dedicated is None else sum(dedicated.values())
    if "memory_total" in card:
        return own
    borrowed = None if shared is None else sum(shared.values())
    parts = [one for one in (own, borrowed) if one is not None]
    return sum(parts) if parts else None
