"""The device inventory reads each OS's own record of what is plugged in, and `ctx.hardware`
offers it under `hardware:read` and nothing wider.

The macOS and Windows readers take the text their command prints and the Linux ones a
directory, so each is run here on a sample of what the OS writes. None of the three is run
on its own OS by this module.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from common import i18n
from common.extensions import host, serving, store
from common.extensions.contract import ContractError
from common.extensions.games import withdraw_all
from common.extensions.hardware import ExtensionHardware
from common.host import hardware
from httpapi import extension_ops
from tests.support.accepted import owner_accepts
from tests.support.skips import needs_colons_in_names

RECIPE = (Path(__file__).resolve().parents[2]
          / "docs" / "extensions" / "examples" / "recipes" / "plugged_in")

MACOS = json.dumps({"SPUSBHostDataType": [
    {"_name": "USB 3.1 Bus", "USBKeyLocationID": "0x00000000"},
    {"_items": [
        {"_name": "Pinscape Controller", "USBDeviceKeyVendorID": "0x1209",
         "USBDeviceKeyProductID": "0xEAEA", "USBDeviceKeyVendorName": "Pinscape",
         "USBDeviceKeySerialNumber": "SECRET-1"},
        {"_name": "USB Hub", "USBDeviceKeyVendorID": "0x0424",
         "USBDeviceKeyProductID": "0x2514", "_items": [
             {"_name": "DOF Board", "USBDeviceKeyVendorID": "0x20a0",
              "USBDeviceKeyProductID": "0x4173", "USBDeviceKeyVendorName": "Teensy"}]},
    ]},
]})

WINDOWS_USB = json.dumps([
    {"FriendlyName": "Pinscape Controller", "Manufacturer": "Pinscape",
     "InstanceId": r"USB\VID_1209&PID_EAEA\SECRET-1"},
    {"FriendlyName": "Pinscape Controller", "Manufacturer": "(Standard system devices)",
     "InstanceId": r"USB\VID_1209&PID_EAEA&MI_01\6&2ABC&0&0001"},
    {"FriendlyName": "Concentrateur USB g\u00e9n\u00e9rique", "Manufacturer": "(Standard USB HUBs)",
     "InstanceId": r"USB\VID_8087&PID_0026\5&1A2B3C", "Service": "USBHUB3"},
    {"FriendlyName": "Hub Controller", "Manufacturer": "Acme",
     "InstanceId": r"USB\VID_1234&PID_0001\7", "Service": "HidUsb"},
])

WINDOWS_SERIAL = json.dumps(
    {"FriendlyName": "USB Serial Device (COM3)", "Manufacturer": "Microsoft",
     "InstanceId": r"USB\VID_2341&PID_0043\SECRET-2"})


class ParsedInventoryTests(unittest.TestCase):
    def test_macos_lists_devices_without_hubs_or_serial_numbers(self) -> None:
        found = hardware.parse_macos_usb(MACOS)

        self.assertEqual([(one["vendor_id"], one["product_id"], one["product"])
                          for one in found],
                         [("1209", "eaea", "Pinscape Controller"),
                          ("20a0", "4173", "DOF Board")])
        self.assertNotIn("SECRET", json.dumps(found))

    def test_macos_text_that_is_not_json_gives_none(self) -> None:
        self.assertEqual(hardware.parse_macos_usb("no usb"), [])

    def test_windows_drops_interfaces_hubs_and_the_serial_number(self) -> None:
        found = hardware.parse_windows_usb(WINDOWS_USB)

        self.assertEqual([(one["vendor_id"], one["product_id"], one["vendor"])
                          for one in found],
                         [("1209", "eaea", "Pinscape"), ("1234", "0001", "Acme")])
        self.assertNotIn("SECRET", json.dumps(found))

    def test_windows_serial_takes_the_com_port_from_the_name(self) -> None:
        (one,) = hardware.parse_windows_serial(WINDOWS_SERIAL)

        self.assertEqual((one["port"], one["product"], one["vendor_id"], one["accessible"]),
                         ("COM3", "USB Serial Device", "2341", None))
        self.assertNotIn("SECRET", json.dumps(one))

    def test_windows_ftdi_ports_keep_their_ids_and_ports_without_ids_are_left_out(self) -> None:
        found = hardware.parse_windows_serial(json.dumps([
            {"FriendlyName": "USB Serial Port (COM4)", "Manufacturer": "FTDI",
             "InstanceId": r"FTDIBUS\VID_0403+PID_6001+SECRET-3A\0000"},
            {"FriendlyName": "Communications Port (COM1)", "Manufacturer": "(Standard port types)",
             "InstanceId": r"ACPI\PNP0501\0"}]))

        self.assertEqual([(one["port"], one["vendor_id"], one["product_id"]) for one in found],
                         [("COM4", "0403", "6001")])
        self.assertNotIn("SECRET", json.dumps(found))

    def test_windows_output_that_is_empty_gives_none(self) -> None:
        self.assertEqual(hardware.parse_windows_usb(""), [])
        self.assertEqual(hardware.parse_windows_serial("not json"), [])


class MacosSerialTests(unittest.TestCase):
    def test_the_usb_serial_driver_names_are_all_found(self) -> None:
        with tempfile.TemporaryDirectory() as held:
            dev = Path(held)
            for name in ("cu.usbmodem1101", "cu.SLAB_USBtoUART", "cu.wchusbserial1410",
                         "cu.Bluetooth-Incoming-Port", "tty.usbmodem1101"):
                (dev / name).write_text("")

            ports = [Path(one["port"]).name for one in hardware.macos_serial(dev)]

        self.assertEqual(sorted(ports),
                         ["cu.SLAB_USBtoUART", "cu.usbmodem1101", "cu.wchusbserial1410"])


class LinuxInventoryTests(unittest.TestCase):
    def setUp(self) -> None:
        held = tempfile.TemporaryDirectory()
        self.addCleanup(held.cleanup)
        self.root = Path(held.name).resolve()
        self.sysfs = self.root / "sys" / "bus" / "usb" / "devices"
        self.tty = self.root / "sys" / "class" / "tty"
        self.dev = self.root / "dev"
        self.dev.mkdir()
        self.sysfs.mkdir(parents=True)
        self.tty.mkdir(parents=True)

    def _usb(self, name: str, **files: str) -> Path:
        folder = self.sysfs / name
        folder.mkdir()
        for file, text in files.items():
            (folder / file).write_text(text + "\n")
        return folder

    def _node(self, bus: str, number: str) -> Path:
        node = self.dev / "bus" / "usb" / bus / number
        node.parent.mkdir(parents=True, exist_ok=True)
        node.write_text("")
        return node

    @needs_colons_in_names
    def test_a_device_is_read_and_hubs_and_interfaces_are_left_out(self) -> None:
        self._usb("1-2", idVendor="1209", idProduct="eaea", manufacturer="Pinscape",
                  product="Pinscape Controller", bDeviceClass="00", busnum="1", devnum="7",
                  serial="SECRET-1")
        self._usb("1-3", idVendor="0424", idProduct="2514", bDeviceClass="09")
        self._usb("1-2:1.0", bInterfaceClass="03")
        self._node("001", "007")

        found = hardware.linux_usb(self.sysfs, self.dev)

        self.assertEqual(len(found), 1)
        self.assertEqual((found[0]["vendor_id"], found[0]["product"], found[0]["accessible"]),
                         ("1209", "Pinscape Controller", True))
        self.assertNotIn("SECRET", json.dumps(found))

    def test_a_node_this_user_may_not_open_is_not_accessible(self) -> None:
        self._usb("1-2", idVendor="1209", idProduct="eaea", bDeviceClass="00", busnum="1",
                  devnum="7")
        self._node("001", "007")

        with patch.object(hardware.os, "access", return_value=False):
            found = hardware.linux_usb(self.sysfs, self.dev)

        self.assertIs(found[0]["accessible"], False)

    @needs_colons_in_names
    def test_a_usb_serial_port_is_named_by_the_device_behind_it(self) -> None:
        parent = self._usb("1-2", idVendor="2341", idProduct="0043", product="Arduino Uno",
                           manufacturer="Arduino")
        interface = parent / "1-2:1.0"
        interface.mkdir()
        (self.tty / "ttyACM0").mkdir()
        (self.tty / "ttyACM0" / "device").symlink_to(interface)
        (self.dev / "ttyACM0").write_text("")
        (self.dev / "ttyS0").write_text("")

        (one,) = hardware.linux_serial(self.tty, self.dev)

        self.assertEqual((one["port"], one["vendor_id"], one["product"]),
                         (str(self.dev / "ttyACM0"), "2341", "Arduino Uno"))

    def test_nothing_in_sysfs_gives_none(self) -> None:
        self.assertEqual(hardware.linux_usb(self.root / "missing", self.dev), [])


class OfferedHardwareTests(unittest.TestCase):
    def setUp(self) -> None:
        withdraw_all()
        self.addCleanup(withdraw_all)
        extension_ops.offer_all()
        self.reader = ExtensionHardware("tester", ("hardware:read",))

    def test_the_devices_come_from_the_inventory(self) -> None:
        with patch.dict(hardware._READERS, {hardware.USB: lambda: [{"kind": "usb"}]}):
            found = self.reader.devices("usb")

        self.assertEqual(found, [{"kind": "usb"}])

    def test_an_extension_without_the_scope_reaches_nothing(self) -> None:
        bare = ExtensionHardware("bare", ("devices:read",))

        self.assertEqual(bare.reaches(), ())
        with self.assertRaises(ContractError):
            bare.devices()

    def test_only_the_two_reads_are_offered(self) -> None:
        self.assertEqual(self.reader.reaches(), ("access_help", "devices"))

    def test_the_help_is_the_sentence_for_this_os(self) -> None:
        for platform, word in (("linux", "udev"), ("darwin", "Privacy"), ("win32", "driver")):
            with patch.object(hardware.sys, "platform", platform):
                self.assertIn(word, self.reader.access_help())

    def test_the_inventory_answers_on_this_device(self) -> None:
        found = hardware.list_devices()

        self.assertTrue(all(one["kind"] in hardware.KINDS for one in found))

    def test_an_unknown_kind_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            hardware.list_devices("bluetooth")


class PluggedInRecipeTests(unittest.TestCase):
    """The cookbook's recipe, loaded as the host loads it, over a fixed inventory."""

    BOARD = {"kind": "usb", "vendor_id": "1209", "product_id": "eaea", "vendor": "Pinscape",
             "product": "Controller", "name": "Pinscape Controller", "port": "",
             "accessible": True}
    LOCKED = {"kind": "usb", "vendor_id": "2341", "product_id": "0043", "vendor": "Arduino",
              "product": "Uno", "name": "Arduino Uno", "port": "", "accessible": False}
    SCREEN = {"kind": "display", "vendor_id": "", "product_id": "", "vendor": "",
              "product": "", "name": "Backglass", "port": "", "accessible": None,
              "x": 0, "y": 0, "width": 1280, "height": 390}

    def setUp(self) -> None:
        owner_accepts(self)
        withdraw_all()
        self.addCleanup(withdraw_all)
        extension_ops.offer_all()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.registry = host.Registry(store.ExtensionStore(Path(tmp.name)))
        self.addCleanup(self.registry.clear)
        self.record = self.registry.load(RECIPE)
        self.addCleanup(i18n.disown, "ext.plugged_in")
        self.assertEqual(self.record.state, host.LOADED, self.record.error)

    def inventory(self, usb: list[dict], display: list[dict] | None = None):
        return patch.dict(hardware._READERS, {
            hardware.USB: lambda: list(usb), hardware.SERIAL: lambda: [],
            hardware.DISPLAY: lambda: list(display or [])})

    def test_each_device_is_a_row_with_its_ids_and_whether_it_may_be_opened(self) -> None:
        with self.inventory([self.BOARD, self.LOCKED], [self.SCREEN]):
            rows = serving.list_rows(self.record, "devices")["rows"]

        self.assertEqual(rows, [
            {"name": "Pinscape Controller", "kind": "usb", "ids": "1209:eaea",
             "access": "yes"},
            {"name": "Arduino Uno", "kind": "usb", "ids": "2341:0043", "access": "no"},
            {"name": "Backglass", "kind": "display", "ids": "", "access": "unknown"}])

    def test_the_line_under_the_list_shows_only_when_a_device_cannot_be_opened(self) -> None:
        with self.inventory([self.BOARD]):
            quiet = serving.list_about(self.record, "devices")["status"]
        with self.inventory([self.BOARD, self.LOCKED]):
            told = serving.list_about(self.record, "devices")["status"]

        self.assertEqual(quiet, "")
        assert self.record.context is not None
        self.assertEqual(told, self.record.context.hardware.access_help())

    def test_a_device_with_nothing_plugged_in_has_no_rows_and_no_line(self) -> None:
        with self.inventory([]):
            rows = serving.list_rows(self.record, "devices")["rows"]
            status = serving.list_about(self.record, "devices")["status"]

        self.assertEqual((rows, status), ([], ""))

    def test_a_device_that_cannot_list_a_kind_leaves_it_out(self) -> None:
        def broken() -> list:
            raise OSError("no sysfs")

        with self.inventory([self.BOARD]), patch.dict(hardware._READERS,
                                                      {hardware.SERIAL: broken}):
            rows = serving.list_rows(self.record, "devices")["rows"]

        self.assertEqual([one["name"] for one in rows], ["Pinscape Controller"])


if __name__ == "__main__":
    unittest.main()
