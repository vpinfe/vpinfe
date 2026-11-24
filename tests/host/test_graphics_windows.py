"""The Windows reader with the ctypes layer replaced: the real thing cannot be watched here."""

from __future__ import annotations

import ctypes
import unittest
from typing import Any
from unittest import mock

from common.host import graphics
from common.host.graphics import UnreadableError, windows

GIB = 1 << 30
DISCRETE: dict[str, Any] = {
    "name": "NVIDIA GeForce RTX 3070", "luid": (0, 0xD1C3), "handle": 11, "flags": 0b1,
    "video": 8 * GIB, "temperature": 612, "fan": 1450, "address": (1, 0, 0)}
INTEGRATED: dict[str, Any] = {
    "name": "Intel(R) UHD Graphics 630", "luid": (0, 0xA2B4), "handle": 12, "flags": 0b1,
    "video": 128 << 20, "temperature": 0, "fan": 0}
WARP: dict[str, Any] = {
    "name": "Microsoft Basic Render Driver", "luid": (0, 0x1), "handle": 13, "flags": 0b101,
    "video": 0, "temperature": 0, "fan": 0}
STRUCTS = {windows._ADAPTER_TYPE: windows._AdapterType, windows._REGISTRY_INFO:
           windows._RegistryInfo, windows._GET_SEGMENT_SIZE: windows._SegmentSizes,
           windows._PERF_DATA: windows._PerfData, windows._ADAPTER_ADDRESS:
           windows._AdapterAddress}


class FakeGdi:
    """D3DKMT as a Python object: fills the structures the way the driver stack does."""

    def __init__(self, *adapters: dict[str, Any], refuse: set[int] | None = None) -> None:
        self.adapters = list(adapters)
        self.refuse = refuse or set()
        self.closed: list[int] = []

    def D3DKMTEnumAdapters2(self, pointer: Any) -> int:  # noqa: N802
        ask = pointer.contents
        ask.count = len(self.adapters)
        if ask.adapters:
            for slot, adapter in zip(ask.adapters[:ask.count], self.adapters, strict=True):
                slot.handle = adapter["handle"]
                slot.luid_high, slot.luid_low = adapter["luid"]
        return 0

    def D3DKMTQueryAdapterInfo(self, pointer: Any) -> int:  # noqa: N802
        ask = pointer.contents
        if ask.kind in self.refuse:
            return -1
        adapter = next(one for one in self.adapters if one["handle"] == ask.handle)
        data = ctypes.cast(ask.data, ctypes.POINTER(STRUCTS[ask.kind])).contents
        if ask.kind == windows._ADAPTER_TYPE:
            data.flags = adapter["flags"]
        elif ask.kind == windows._REGISTRY_INFO:
            data.adapter = adapter["name"]
        elif ask.kind == windows._ADAPTER_ADDRESS:
            data.bus, data.device, data.function = adapter.get("address", (0, 0, 0))
        elif ask.kind == windows._GET_SEGMENT_SIZE:
            data.video = adapter["video"]
        else:
            data.temperature, data.fan_rpm = adapter["temperature"], adapter["fan"]
        return 0

    def D3DKMTCloseAdapter(self, pointer: Any) -> int:  # noqa: N802
        self.closed.append(pointer.contents.handle)
        return 0


def instance(luid: tuple[int, int], suffix: str) -> str:
    return f"luid_0x{luid[0]:08x}_0x{luid[1]:08x}_{suffix}"


class FakeQuery:
    answers: dict[str, dict[str, float] | None] = {}
    collected = 0

    def __init__(self, pdh: Any) -> None:
        self.counters = dict.fromkeys(self.answers)

    def collect(self) -> None:
        type(self).collected += 1

    def values(self, name: str) -> dict[str, float] | None:
        return self.answers[name]


class Reader(unittest.TestCase):
    def setUp(self) -> None:
        windows._held.clear()
        self.addCleanup(windows._held.clear)
        FakeQuery.answers, FakeQuery.collected = {}, 0
        self.clock = mock.Mock(return_value=100.0)
        for patch in (mock.patch.object(windows, "_pdh", return_value=object()),
                      mock.patch.object(windows, "_Query", FakeQuery),
                      mock.patch.object(windows.time, "monotonic", self.clock)):
            patch.start()
            self.addCleanup(patch.stop)

    def gdi(self, *adapters: dict[str, Any], refuse: set[int] | None = None) -> FakeGdi:
        gdi = FakeGdi(*adapters, refuse=refuse)
        patch = mock.patch.object(windows, "_gdi32", return_value=gdi)
        patch.start()
        self.addCleanup(patch.stop)
        return gdi

    def counters(self, engine: dict[str, float] | None = None,
                 dedicated: dict[str, float] | None = None,
                 shared: dict[str, float] | None = None) -> None:
        FakeQuery.answers = {name: values for name, values in
                             (("engine", engine), ("dedicated", dedicated), ("shared", shared))
                             if values is not None}

    def sample(self) -> list[dict[str, Any]]:
        """Two samples a second apart: busy needs the second."""
        windows.cards()
        self.clock.return_value += 1.0
        return windows.cards()


class ListingTests(Reader):
    def test_a_card_is_named_and_given_its_memory_temperature_and_fan(self) -> None:
        gdi = self.gdi(DISCRETE)
        self.counters(dedicated={instance(DISCRETE["luid"], "phys_0"): 2.5 * GIB})
        card = windows.cards()[0]
        self.assertEqual(card, {
            "id": "luid_0x00000000_0x0000d1c3", "name": "NVIDIA GeForce RTX 3070",
            "vendor": "nvidia", "pci": "0000:01:00.0", "memory_total": float(8 * GIB),
            "memory_used": 2.5 * GIB,
            "temperature": 61.2, "fan_rpm": 1450.0, "source": "windows"})
        self.assertEqual(gdi.closed, [11])

    def test_the_software_renderer_is_not_a_card(self) -> None:
        self.gdi(WARP, DISCRETE)
        self.counters()
        self.assertEqual([card["name"] for card in windows.cards()], [DISCRETE["name"]])

    def test_an_adapter_that_cannot_render_is_not_a_card(self) -> None:
        self.gdi({**DISCRETE, "flags": 1 << 11})
        self.assertEqual(windows.cards(), [])

    def test_a_renderer_that_is_software_indirect_or_compute_only_is_not_a_card(self) -> None:
        for bit in (2, 6, 11):
            with self.subTest(bit=bit):
                self.gdi({**DISCRETE, "flags": 0b1 | 1 << bit})
                self.assertEqual(windows.cards(), [])

    def test_the_header_values_are_what_windows_answers_to(self) -> None:
        self.assertEqual((windows._GET_SEGMENT_SIZE, windows._ADAPTER_ADDRESS,
                          windows._REGISTRY_INFO, windows._ADAPTER_TYPE, windows._PERF_DATA),
                         (3, 6, 8, 15, 62))
        self.assertEqual((windows._RENDER, windows._SOFTWARE, windows._INDIRECT,
                          windows._COMPUTE_ONLY), (1, 4, 64, 2048))

    def test_the_structures_are_the_size_windows_expects(self) -> None:
        self.assertEqual((ctypes.sizeof(windows._AdapterInfo), ctypes.sizeof(windows._PerfData),
                          ctypes.sizeof(windows._CounterItem), ctypes.sizeof(windows._SegmentSizes),
                          ctypes.sizeof(windows._AdapterAddress)), (20, 64, 24, 24, 12))
        if ctypes.sizeof(ctypes.c_void_p) == 8:
            self.assertEqual((ctypes.sizeof(windows._QueryAdapterInfo),
                              ctypes.sizeof(windows._EnumAdapters)), (24, 16))

    def test_the_handles_of_every_adapter_are_closed_when_one_breaks(self) -> None:
        gdi = self.gdi(DISCRETE, INTEGRATED)
        with mock.patch.object(windows, "_card", side_effect=[OSError("boom"), None]):
            with self.assertRaises(OSError):
                windows.cards()
        self.assertEqual(gdi.closed, [11, 12])

    def test_a_card_is_given_its_pci_address_to_be_matched_by(self) -> None:
        self.gdi({**DISCRETE, "address": (0x2a, 3, 1)})
        self.counters()
        self.assertEqual(windows.cards()[0]["pci"], "0000:2a:03.1")

    def test_a_zero_temperature_or_fan_is_a_reading_the_driver_does_not_have(self) -> None:
        self.gdi({**DISCRETE, "temperature": 0, "fan": 0})
        self.counters()
        card = windows.cards()[0]
        self.assertNotIn("temperature", card)
        self.assertNotIn("fan_rpm", card)

    def test_a_card_with_no_total_borrows_system_memory_and_has_none_of_its_own(self) -> None:
        self.gdi(INTEGRATED)
        luid = INTEGRATED["luid"]
        self.counters(dedicated={instance(luid, "phys_0"): 100.0},
                      shared={instance(luid, "phys_0"): 400.0})
        card = windows.cards()[0]
        self.assertNotIn("memory_total", card)
        self.assertEqual(card["memory_used"], 500.0)

    def test_a_card_with_a_total_leaves_the_shared_memory_out(self) -> None:
        self.gdi(DISCRETE)
        luid = DISCRETE["luid"]
        self.counters(dedicated={instance(luid, "phys_0"): 100.0},
                      shared={instance(luid, "phys_0"): 400.0})
        self.assertEqual(windows.cards()[0]["memory_used"], 100.0)

    def test_counters_are_kept_to_the_adapter_they_name(self) -> None:
        self.gdi(DISCRETE, INTEGRATED)
        self.counters(dedicated={instance(INTEGRATED["luid"], "phys_0"): 7.0})
        first, second = windows.cards()
        self.assertNotIn("memory_used", first)
        self.assertEqual(second["memory_used"], 7.0)

    def test_a_card_whose_queries_all_fail_is_still_listed_with_no_source(self) -> None:
        self.gdi(DISCRETE, refuse={windows._GET_SEGMENT_SIZE, windows._PERF_DATA,
                                   windows._ADAPTER_TYPE})
        self.counters()
        card = windows.cards()[0]
        self.assertEqual(card["name"], "NVIDIA GeForce RTX 3070")
        self.assertNotIn("source", card)

    def test_a_card_whose_name_cannot_be_read_is_named_by_its_number(self) -> None:
        self.gdi(DISCRETE, refuse={windows._REGISTRY_INFO})
        self.counters()
        card = windows.cards()[0]
        self.assertIn("d1c3", card["name"])
        self.assertEqual(card["vendor"], "other")

    def test_no_adapters_is_no_cards(self) -> None:
        self.gdi()
        self.assertEqual(windows.cards(), [])

    def test_gdi32_that_will_not_load_says_so(self) -> None:
        with mock.patch.object(windows, "_gdi32", side_effect=OSError("no gdi32")):
            with self.assertRaises(UnreadableError) as raised:
                windows.cards()
        self.assertIn("D3DKMT", raised.exception.reason)
        self.assertIn("no gdi32", raised.exception.detail)

    def test_counters_that_fail_leave_the_card_without_busy_and_memory_in_use(self) -> None:
        self.gdi(DISCRETE)
        with mock.patch.object(windows, "_pdh", side_effect=OSError("no pdh")):
            card = windows.cards()[0]
        self.assertEqual(card["memory_total"], float(8 * GIB))
        self.assertNotIn("busy", card)
        self.assertNotIn("memory_used", card)


class FakePdh:
    """Answers PdhGetFormattedCounterArrayW the way PDH does: the size needed first, then
    the items with their names after them in the same buffer."""

    def __init__(self, items: list[tuple[str, int, float]], second: int = 0) -> None:
        self.items, self.second = items, second

    def PdhGetFormattedCounterArrayW(self, counter: Any, fmt: int, size: Any,  # noqa: N802
                                     count: Any, buffer: Any) -> int:
        width = ctypes.sizeof(ctypes.c_wchar)
        encoding = "utf-16-le" if width == 2 else "utf-32-le"
        names = [name.encode(encoding) + b"\0" * width for name, _, _ in self.items]
        head = ctypes.sizeof(windows._CounterItem) * len(self.items)
        size.contents.value = head + sum(len(one) for one in names)
        count.contents.value = len(self.items)
        if buffer is None:
            return windows._MORE_DATA
        if self.second:
            return self.second
        at = head
        for index, ((_, status, value), name) in enumerate(zip(self.items, names, strict=True)):
            item = windows._CounterItem.from_buffer(buffer, ctypes.sizeof(
                windows._CounterItem) * index)
            ctypes.memmove(ctypes.addressof(buffer) + at, name, len(name))
            item.name, item.status, item.value = ctypes.addressof(buffer) + at, status, value
            at += len(name)
        return 0


class CounterTests(unittest.TestCase):
    def query(self, pdh: FakePdh) -> windows._Query:
        query = windows._Query.__new__(windows._Query)
        query.pdh, query.counters = pdh, {"engine": ctypes.c_void_p()}
        return query

    def test_each_instance_comes_back_by_name_with_its_value(self) -> None:
        said = self.query(FakePdh([("pid_4_luid_0x0_0x1_eng_0", 0, 12.5),
                                   ("luid_0x0_0x1", 1, 3.0)]))
        self.assertEqual(said.values("engine"),
                         {"pid_4_luid_0x0_0x1_eng_0": 12.5, "luid_0x0_0x1": 3.0})

    def test_an_instance_whose_value_is_not_valid_is_left_out(self) -> None:
        said = self.query(FakePdh([("a", 0, 1.0), ("b", 0xC0000BBA, 9.0)]))
        self.assertEqual(said.values("engine"), {"a": 1.0})

    def test_a_counter_with_nothing_valid_is_unread(self) -> None:
        self.assertIsNone(self.query(FakePdh([("b", 0xC0000BBA, 9.0)])).values("engine"))
        self.assertIsNone(self.query(FakePdh([])).values("engine"))

    def test_a_counter_pdh_refuses_the_second_call_for_is_unread(self) -> None:
        self.assertIsNone(self.query(FakePdh([("a", 0, 1.0)], second=0x800007D5)).values("engine"))


class BusyTests(Reader):
    def test_the_first_sample_has_no_busy(self) -> None:
        self.gdi(DISCRETE)
        self.counters(engine={instance(DISCRETE["luid"], "phys_0_eng_0_engtype_3D"): 40.0})
        self.assertNotIn("busy", windows.cards()[0])

    def test_the_second_sample_a_second_later_has_it(self) -> None:
        self.gdi(DISCRETE)
        self.counters(engine={instance(DISCRETE["luid"], "phys_0_eng_0_engtype_3D"): 40.0})
        self.assertEqual(self.sample()[0]["busy"], 40.0)

    def test_busy_is_the_busiest_engine_and_the_processes_on_one_engine_add_up(self) -> None:
        self.gdi(DISCRETE)
        luid = DISCRETE["luid"]
        self.counters(engine={
            "pid_10_" + instance(luid, "phys_0_eng_0_engtype_3D"): 20.0,
            "pid_11_" + instance(luid, "phys_0_eng_0_engtype_3D"): 25.0,
            "pid_10_" + instance(luid, "phys_0_eng_1_engtype_Copy"): 30.0})
        self.assertEqual(self.sample()[0]["busy"], 45.0)

    def test_busy_cannot_pass_a_hundred(self) -> None:
        self.gdi(DISCRETE)
        luid = DISCRETE["luid"]
        self.counters(engine={"pid_1_" + instance(luid, "phys_0_eng_0_engtype_3D"): 80.0,
                              "pid_2_" + instance(luid, "phys_0_eng_0_engtype_3D"): 80.0})
        self.assertEqual(self.sample()[0]["busy"], 100.0)

    def test_an_adapter_nothing_is_drawing_on_is_idle(self) -> None:
        self.gdi(DISCRETE)
        self.counters(engine={instance(INTEGRATED["luid"], "phys_0_eng_0_engtype_3D"): 5.0})
        self.assertEqual(self.sample()[0]["busy"], 0.0)

    def test_a_counter_that_cannot_be_read_is_no_busy_rather_than_idle(self) -> None:
        self.gdi(DISCRETE)
        self.counters(engine={})
        FakeQuery.answers["engine"] = None
        card = self.sample()[0]
        self.assertNotIn("busy", card)

    def test_memory_in_use_is_absent_where_its_counter_cannot_be_read(self) -> None:
        self.gdi(DISCRETE)
        self.counters(dedicated={})
        FakeQuery.answers["dedicated"] = None
        self.assertNotIn("memory_used", windows.cards()[0])

    def test_a_second_caller_moments_after_the_first_gets_the_same_sample(self) -> None:
        self.gdi(DISCRETE)
        self.counters(engine={instance(DISCRETE["luid"], "phys_0_eng_0_engtype_3D"): 40.0})
        windows.cards()
        self.clock.return_value += 1.0
        windows.cards()
        collected = FakeQuery.collected
        self.clock.return_value += 0.1
        self.assertEqual(windows.cards()[0]["busy"], 40.0)
        self.assertEqual(FakeQuery.collected, collected)

    def test_after_a_long_gap_the_window_is_too_wide_to_say(self) -> None:
        self.gdi(DISCRETE)
        self.counters(engine={instance(DISCRETE["luid"], "phys_0_eng_0_engtype_3D"): 40.0})
        self.sample()
        self.clock.return_value += 60.0
        self.assertNotIn("busy", windows.cards()[0])


class ReadTests(Reader):
    def setUp(self) -> None:
        super().setUp()
        patch = mock.patch.object(graphics, "_lister", return_value=windows)
        patch.start()
        self.addCleanup(patch.stop)

    def test_a_card_with_no_reading_is_listed_with_the_reason(self) -> None:
        self.gdi({**WARP, "flags": 0b1, "name": "Some Display Adapter"})
        self.counters()
        card = graphics.read()["cards"][0]
        self.assertEqual(card["name"], "Some Display Adapter")
        self.assertTrue(card["reason"])
        self.assertNotIn("source", card)

    def test_an_nvidia_card_goes_to_nvidia_smi(self) -> None:
        self.gdi(DISCRETE)
        self.counters()
        reading = {"id": "0", "pci": "0000:01:00.0", "name": "NVIDIA GeForce RTX 3070",
                   "vendor": "nvidia", "source": "nvidia-smi", "busy": 12.0}
        with mock.patch("common.host.graphics.nvidia_smi.read", return_value=[reading]):
            card = graphics.read()["cards"][0]
        self.assertEqual(card["source"], "nvidia-smi")
        self.assertEqual(card["busy"], 12.0)
        self.assertEqual(card["id"], "0")

    def test_two_identical_nvidia_cards_are_matched_by_address_not_order(self) -> None:
        first = {**DISCRETE, "address": (1, 0, 0)}
        second = {**DISCRETE, "handle": 14, "luid": (0, 0xE0E0), "address": (2, 0, 0)}
        self.gdi(first, second)
        self.counters()
        readings = [{"pci": "0000:02:00.0", "name": DISCRETE["name"], "vendor": "nvidia",
                     "source": "nvidia-smi", "busy": 90.0},
                    {"pci": "0000:01:00.0", "name": DISCRETE["name"], "vendor": "nvidia",
                     "source": "nvidia-smi", "busy": 10.0}]
        with mock.patch("common.host.graphics.nvidia_smi.read", return_value=readings):
            cards = graphics.read()["cards"]
        self.assertEqual([card["busy"] for card in cards], [10.0, 90.0])

    def test_an_nvidia_card_nvidia_smi_cannot_read_keeps_what_windows_read(self) -> None:
        self.gdi(DISCRETE)
        self.counters()
        with mock.patch("common.host.graphics.nvidia_smi.read",
                        side_effect=UnreadableError("missing")):
            card = graphics.read()["cards"][0]
        self.assertEqual(card["source"], "windows")
        self.assertEqual(card["temperature"], 61.2)


if __name__ == "__main__":
    unittest.main()
