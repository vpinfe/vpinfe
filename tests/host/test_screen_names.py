from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from common.host import edid, screen_names
from tests.support import edids


def monitor(name: str, width_mm: int | None = None,
            height_mm: int | None = None) -> SimpleNamespace:
    return SimpleNamespace(name=name, width_mm=width_mm, height_mm=height_mm)


def source(connector: str, fixture: str) -> screen_names.Source:
    parsed = edid.parse(edids.load(fixture))
    assert parsed is not None
    return screen_names.Source(connector, parsed)


class ADrmTree(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def connector(self, folder: str, fixture: str | None) -> None:
        (self.root / folder).mkdir()
        (self.root / folder / "edid").write_bytes(edids.load(fixture) if fixture else b"")


class DrmSourceTests(ADrmTree):
    def test_each_connector_is_read_under_the_name_the_kernel_gives_it(self) -> None:
        self.connector("card0-HDMI-A-1", "dell_p2419h_a")
        self.connector("card0-eDP-1", "no_model_name")

        found = screen_names.drm_sources(self.root)

        self.assertEqual([(one.connector, one.edid.name) for one in found],
                         [("HDMI-A-1", "DELL P2419H"), ("eDP-1", "")])

    def test_a_connector_with_nothing_plugged_in_is_left_out(self) -> None:
        self.connector("card0-DP-1", None)
        self.connector("card0-HDMI-A-1", "dell_p2419h_a")

        self.assertEqual([one.connector for one in screen_names.drm_sources(self.root)],
                         ["HDMI-A-1"])

    def test_entries_that_are_not_connectors_are_left_alone(self) -> None:
        (self.root / "card0").mkdir()
        (self.root / "version").write_text("1")
        self.connector("card0-HDMI-A-1", "dell_p2419h_a")

        self.assertEqual(len(screen_names.drm_sources(self.root)), 1)

    def test_a_machine_with_no_drm_tree_has_no_sources(self) -> None:
        self.assertEqual(screen_names.drm_sources(self.root / "absent"), [])


class MatchedTests(unittest.TestCase):
    def test_a_connector_name_picks_the_edid_whatever_the_order(self) -> None:
        names = screen_names.matched(
            [monitor("HDMI-1"), monitor("DP-1")],
            [source("DP-1", "dell_p2419h_a"), source("HDMI-A-1", "television_with_extension")])

        self.assertEqual(names, ["SAMSUNG", "DELL P2419H"])

    def test_the_connector_comparison_ignores_case_and_the_kernels_subtype(self) -> None:
        names = screen_names.matched([monitor("hdmi-1")],
                                     [source("HDMI-A-1", "dell_p2419h_a")])

        self.assertEqual(names, ["DELL P2419H"])

    def test_a_driver_that_numbers_its_connectors_differently_is_matched_by_size(self) -> None:
        names = screen_names.matched(
            [monitor("DisplayPort-0", 527, 296), monitor("HDMI-A-0", 1220, 690)],
            [source("DP-1", "dell_p2419h_a"), source("HDMI-A-1", "television_with_extension")])

        self.assertEqual(names, ["DELL P2419H", "SAMSUNG"])

    def test_a_size_turned_on_its_side_still_fits(self) -> None:
        names = screen_names.matched([monitor("Unknown", 300, 530)],
                                     [source("DP-1", "dell_p2419h_a")])

        self.assertEqual(names, ["DELL P2419H"])

    def test_identical_panels_are_each_named_though_nothing_tells_them_apart(self) -> None:
        names = screen_names.matched(
            [monitor("A", 530, 300), monitor("B", 530, 300)],
            [source("DP-1", "dell_p2419h_a"), source("DP-2", "dell_p2419h_b")])

        self.assertEqual(names, ["DELL P2419H", "DELL P2419H"])

    def test_one_edid_is_not_given_to_two_screens(self) -> None:
        names = screen_names.matched(
            [monitor("A", 530, 300), monitor("B", 530, 300)],
            [source("DP-1", "dell_p2419h_a")])

        self.assertEqual(names, ["DELL P2419H", ""])

    def test_a_size_that_fits_two_different_models_names_neither(self) -> None:
        names = screen_names.matched(
            [monitor("A", 530, 300)],
            [screen_names.Source("DP-1", edid.Edid("DELL P2419H", 53, 30)),
             screen_names.Source("DP-2", edid.Edid("AOC Q2790PQ", 53, 30))])

        self.assertEqual(names, [""])

    def test_a_size_that_fits_two_panels_of_one_model_names_the_model(self) -> None:
        names = screen_names.matched(
            [monitor("A", 530, 300)],
            [source("DP-1", "dell_p2419h_a"), source("DP-2", "dell_p2419h_b")])

        self.assertEqual(names, ["DELL P2419H"])

    def test_a_screen_with_no_size_and_no_connector_match_is_left_unnamed(self) -> None:
        self.assertEqual(screen_names.matched([monitor("Unknown")],
                                              [source("DP-1", "dell_p2419h_a")]), [""])

    def test_a_connector_whose_panel_names_no_model_stays_unnamed(self) -> None:
        names = screen_names.matched([monitor("eDP-1", 344, 194)],
                                     [source("eDP-1", "no_model_name"),
                                      source("DP-1", "dell_p2419h_a")])

        self.assertEqual(names, [""])


class WindowsNamesTests(unittest.TestCase):
    GUID = "{4d36e96e-e325-11ce-bfc1-08002be10318}"

    def registry(self, held: dict[str, list[tuple[str, str]]]):
        return lambda hardware_id: iter(
            (driver, edids.load(fixture)) for driver, fixture in held.get(hardware_id, []))

    def test_a_monitor_is_named_by_the_instance_holding_its_driver_key(self) -> None:
        names = screen_names.windows_names(
            [screen_names.WindowsMonitor(r"\\.\DISPLAY1", "DEL40E2", rf"{self.GUID}\0004")],
            self.registry({"DEL40E2": [(rf"{self.GUID}\0003", "television_with_extension"),
                                       (rf"{self.GUID}\0004", "dell_p2419h_a")]}))

        self.assertEqual(names, {r"\\.\DISPLAY1": "DELL P2419H"})

    def test_two_monitors_of_one_hardware_id_are_told_apart_by_the_driver_key(self) -> None:
        names = screen_names.windows_names(
            [screen_names.WindowsMonitor(r"\\.\DISPLAY1", "DEL40E2", rf"{self.GUID}\0004"),
             screen_names.WindowsMonitor(r"\\.\DISPLAY2", "DEL40E2", rf"{self.GUID}\0005")],
            self.registry({"DEL40E2": [(rf"{self.GUID}\0005", "name_fills_its_field"),
                                       (rf"{self.GUID}\0004", "dell_p2419h_a")]}))

        self.assertEqual(names, {r"\\.\DISPLAY1": "DELL P2419H",
                                 r"\\.\DISPLAY2": "DELL S2721DGF"})

    def test_the_driver_key_is_compared_without_regard_to_case(self) -> None:
        names = screen_names.windows_names(
            [screen_names.WindowsMonitor(r"\\.\DISPLAY1", "DEL40E2",
                                         rf"{self.GUID.upper()}\0004")],
            self.registry({"DEL40E2": [(rf"{self.GUID}\0004", "dell_p2419h_a")]}))

        self.assertEqual(names, {r"\\.\DISPLAY1": "DELL P2419H"})

    def test_a_monitor_with_no_registry_instance_is_left_out(self) -> None:
        names = screen_names.windows_names(
            [screen_names.WindowsMonitor(r"\\.\DISPLAY1", "GSM5B7F", rf"{self.GUID}\0001")],
            self.registry({}))

        self.assertEqual(names, {})


class FakeWinreg:
    """`winreg` over a dict of key path to its values, for the paths a test names."""
    HKEY_LOCAL_MACHINE = ""

    class Key:
        def __init__(self, path: str) -> None:
            self.path = path

        def __enter__(self) -> FakeWinreg.Key:
            return self

        def __exit__(self, *_: object) -> None:
            return None

    def __init__(self, keys: dict[str, dict[str, object]]) -> None:
        self.keys = keys

    def OpenKey(self, parent: object, sub: str) -> FakeWinreg.Key:  # noqa: N802
        base = getattr(parent, "path", "")
        path = f"{base}\\{sub}" if base else sub
        if path not in self.keys:
            raise FileNotFoundError(path)
        return self.Key(path)

    def _children(self, key: Key) -> list[str]:
        depth = key.path.count("\\") + 1
        return sorted({path.split("\\")[depth] for path in self.keys
                       if path.startswith(key.path + "\\") and path.count("\\") >= depth})

    def QueryInfoKey(self, key: Key) -> tuple[int, int, int]:  # noqa: N802
        return len(self._children(key)), 0, 0

    def EnumKey(self, key: Key, index: int) -> str:  # noqa: N802
        return self._children(key)[index]

    def QueryValueEx(self, key: Key, name: str) -> tuple[object, int]:  # noqa: N802
        if name not in self.keys[key.path]:
            raise FileNotFoundError(name)
        return self.keys[key.path][name], 0


class WindowsRegistryTests(unittest.TestCase):
    BASE = r"SYSTEM\CurrentControlSet\Enum\DISPLAY"
    GUID = "{4d36e96e-e325-11ce-bfc1-08002be10318}"

    def read(self, keys: dict[str, dict[str, object]], hardware_id: str) -> list[tuple[str, bytes]]:
        with patch.object(screen_names.sys, "platform", "win32"), \
                patch.dict("sys.modules", {"winreg": FakeWinreg(keys)}):
            return list(screen_names.windows_registry()(hardware_id))

    def monitor_keys(self, hardware_id: str, fixture: str) -> dict[str, dict[str, object]]:
        instance = rf"{self.BASE}\{hardware_id}\5&1&0&UID1"
        return {rf"{self.BASE}\{hardware_id}": {},
                instance: {"Driver": rf"{self.GUID}\0004"},
                rf"{instance}\Device Parameters": {"EDID": edids.load(fixture)}}

    def test_an_instance_yields_its_driver_key_and_edid(self) -> None:
        found = self.read(self.monitor_keys("DEL40E2", "dell_p2419h_a"), "DEL40E2")

        self.assertEqual(found, [(rf"{self.GUID}\0004", edids.load("dell_p2419h_a"))])

    def test_a_hardware_id_with_no_key_yields_nothing_rather_than_raising(self) -> None:
        self.assertEqual(self.read(self.monitor_keys("DEL40E2", "dell_p2419h_a"), "GONE123"), [])

    def test_an_instance_with_no_edid_is_skipped(self) -> None:
        keys = self.monitor_keys("DEL40E2", "dell_p2419h_a")
        del keys[rf"{self.BASE}\DEL40E2\5&1&0&UID1\Device Parameters"]

        self.assertEqual(self.read(keys, "DEL40E2"), [])

    def test_one_hardware_id_missing_leaves_the_others_named(self) -> None:
        keys = self.monitor_keys("DEL40E2", "dell_p2419h_a")
        monitors = [screen_names.WindowsMonitor(r"\\.\DISPLAY1", "VIRTUAL0", rf"{self.GUID}\0002"),
                    screen_names.WindowsMonitor(r"\\.\DISPLAY2", "DEL40E2", rf"{self.GUID}\0004")]
        with patch.object(screen_names.sys, "platform", "win32"), \
                patch.dict("sys.modules", {"winreg": FakeWinreg(keys)}):
            names = screen_names.windows_names(monitors, screen_names.windows_registry())

        self.assertEqual(names, {r"\\.\DISPLAY2": "DELL P2419H"})


class ModelNamesTests(unittest.TestCase):
    def test_a_platform_with_no_reader_names_nothing(self) -> None:
        with patch.object(screen_names.sys, "platform", "freebsd"):
            self.assertEqual(screen_names.model_names([monitor("a"), monitor("b")]), ["", ""])

    def test_a_reader_that_fails_names_nothing_rather_than_raising(self) -> None:
        with patch.object(screen_names.sys, "platform", "linux"), \
                patch.object(screen_names, "drm_sources", side_effect=RuntimeError("no")):
            self.assertEqual(screen_names.model_names([monitor("a")]), [""])

    def test_linux_reads_the_drm_tree_and_matches_it(self) -> None:
        with patch.object(screen_names.sys, "platform", "linux"), \
                patch.object(screen_names, "drm_sources",
                             return_value=[source("DP-1", "dell_p2419h_a")]):
            self.assertEqual(screen_names.model_names([monitor("DP-1")]), ["DELL P2419H"])

    def test_windows_names_a_screen_by_the_adapter_screeninfo_reports(self) -> None:
        with patch.object(screen_names.sys, "platform", "win32"), \
                patch.object(screen_names, "windows_monitors", return_value=[]), \
                patch.object(screen_names, "windows_registry", return_value=iter), \
                patch.object(screen_names, "windows_names",
                             return_value={r"\\.\DISPLAY2": "DELL P2419H"}):
            self.assertEqual(
                screen_names.model_names([monitor(r"\\.\DISPLAY1"), monitor(r"\\.\DISPLAY2")]),
                ["", "DELL P2419H"])


if __name__ == "__main__":
    unittest.main()
