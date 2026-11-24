"""The Linux readers, on a fake sysfs and proc tree: the real thing cannot be watched here."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from common.host import graphics
from common.host.graphics import linux
from common.host.tools import NVTOP
from tests.support.programs import found, missing
from tests.support.skips import needs_colons_in_names

PCI_IDS = """\
# a comment
10de  NVIDIA Corporation
\t2484  GA104 [GeForce RTX 3070]
1002  Advanced Micro Devices, Inc. [AMD/ATI]
\t73bf  Navi 21 [Radeon RX 6800/6800 XT / 6900 XT]
\t\t1002 0e3a  Subsystem line
8086  Intel Corporation
\t3e92  CoffeeLake-S GT2 [UHD Graphics 630]
102b  Matrox Electronics Systems Ltd.
\t0522  MGA G200e
C 03  Display controller
"""
AMD_NAME = "AMD Radeon RX 6800/6800 XT / 6900 XT"


def _done(text: str) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(["x"], 0, stdout=text, stderr="")


class Tree(unittest.TestCase):
    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        for name in ("drm", "pci", "proc", "devices", "drivers"):
            (self.root / name).mkdir()
        ids = self.root / "pci.ids"
        ids.write_text(PCI_IDS)
        for target, value in (("DRM", self.root / "drm"), ("PCI", self.root / "pci"),
                              ("PROC", self.root / "proc"), ("PCI_IDS", (ids,))):
            patch = mock.patch.object(linux, target, value)
            patch.start()
            self.addCleanup(patch.stop)
        linux._pci_names.cache_clear()
        linux._before.clear()
        self.addCleanup(linux._pci_names.cache_clear)
        self.addCleanup(linux._before.clear)

    def device(self, address: str, vendor: str, device: str, driver: str,
               files: dict[str, str] | None = None, kind: str = "0x030000") -> Path:
        path = self.root / "devices" / address
        path.mkdir()
        for name, text in {"vendor": vendor, "device": device, "class": kind,
                           **(files or {})}.items():
            (path / name).parent.mkdir(parents=True, exist_ok=True)
            (path / name).write_text(text)
        (self.root / "drivers" / driver).mkdir(exist_ok=True)
        (path / "driver").symlink_to(self.root / "drivers" / driver)
        (self.root / "pci" / address).symlink_to(path)
        return path

    def node(self, name: str, device: Path, files: dict[str, str] | None = None) -> Path:
        node = self.root / "drm" / name
        node.mkdir()
        (node / "device").symlink_to(device)
        for relative, text in (files or {}).items():
            (node / relative).parent.mkdir(parents=True, exist_ok=True)
            (node / relative).write_text(text)
        return node


@needs_colons_in_names
class ListingTests(Tree):
    def amd(self, files: dict[str, str] | None = None) -> None:
        self.node("card0", self.device("0000:03:00.0", "0x1002\n", "0x73bf\n", "amdgpu", files))

    def test_an_amd_card_is_read_from_its_files_and_named_from_pci_ids(self) -> None:
        self.amd({"gpu_busy_percent": "37\n", "mem_info_vram_used": "1073741824\n",
                  "mem_info_vram_total": "17163091968\n",
                  "hwmon/hwmon3/temp1_input": "54000\n",
                  "hwmon/hwmon3/power1_average": "41500000\n",
                  "hwmon/hwmon3/freq1_input": "1725000000\n",
                  "hwmon/hwmon3/fan1_input": "1200\n",
                  "hwmon/hwmon3/pwm1": "64\n", "hwmon/hwmon3/pwm1_max": "255\n"})
        card = linux.cards()[0]
        self.assertEqual((card["name"], card["vendor"], card["source"], card["pci"], card["id"]),
                         (AMD_NAME, "amd", "linux", "0000:03:00.0", "card0"))
        self.assertEqual((card["busy"], card["memory_used"], card["memory_total"]),
                         (37.0, 1073741824.0, 17163091968.0))
        self.assertEqual((card["temperature"], card["power"], card["clock"], card["fan_rpm"]),
                         (54.0, 41.5, 1725.0, 1200.0))
        self.assertAlmostEqual(card["fan_percent"], 100 * 64 / 255)

    def test_what_an_amd_card_does_not_report_is_left_out(self) -> None:
        self.amd({"gpu_busy_percent": "0\n"})
        card = linux.cards()[0]
        self.assertEqual(card["busy"], 0.0)
        for key in ("temperature", "power", "clock", "fan_rpm", "fan_percent", "memory_used"):
            self.assertNotIn(key, card)

    def test_the_clock_marked_in_use_is_read_where_hwmon_has_none(self) -> None:
        self.amd({"gpu_busy_percent": "5", "pp_dpm_sclk": "0: 500Mhz\n1: 1500Mhz *\n2: 2100Mhz\n"})
        self.assertEqual(linux.cards()[0]["clock"], 1500.0)

    def test_a_card_with_nothing_to_read_is_listed_without_a_source(self) -> None:
        self.amd()
        card = linux.cards()[0]
        self.assertNotIn("source", card)
        self.assertEqual(card["name"], AMD_NAME)

    def test_a_device_pci_ids_does_not_know_is_named_by_its_ids(self) -> None:
        self.node("card0", self.device("0000:03:00.0", "0x1002", "0xffff", "amdgpu"))
        self.node("card1", self.device("0000:04:00.0", "0x102b", "0xffff", "mgag200"))
        self.node("card2", self.device("0000:05:00.0", "0x1234", "0x5678", "bochs"))
        self.assertEqual([one["name"] for one in linux.cards()],
                         ["AMD graphics 1002:ffff",
                          "Matrox Electronics Systems Ltd. graphics 102b:ffff",
                          "Graphics 1234:5678"])

    def test_a_name_without_brackets_gets_its_vendor_in_front(self) -> None:
        self.node("card0", self.device("0000:04:00.0", "0x102b", "0x0522", "mgag200"))
        self.assertEqual(linux.cards()[0]["name"], "Matrox Electronics Systems Ltd. MGA G200e")

    def test_with_no_pci_ids_file_the_ids_are_the_name(self) -> None:
        self.amd()
        with mock.patch.object(linux, "PCI_IDS", (self.root / "missing",)):
            self.assertEqual(linux.cards()[0]["name"], "AMD graphics 1002:73bf")

    def test_an_nvidia_card_is_listed_by_its_pci_address_for_nvidia_smi(self) -> None:
        self.node("card0", self.device("0000:01:00.0", "0x10de", "0x2484", "nvidia"))
        card = linux.cards()[0]
        self.assertEqual((card["name"], card["vendor"], card["pci"]),
                         ("NVIDIA GeForce RTX 3070", "nvidia", "0000:01:00.0"))
        self.assertNotIn("source", card)

    def test_a_display_device_the_kernel_has_no_node_for_is_still_listed(self) -> None:
        self.amd()
        self.device("0000:01:00.0", "0x10de", "0x2484", "nvidia")
        self.device("0000:00:1f.3", "0x8086", "0xa0c8", "snd_hda", kind="0x040300")
        self.assertEqual([(one["id"], one["vendor"]) for one in linux.cards()],
                         [("card0", "amd"), ("0000:01:00.0", "nvidia")])

    def test_connectors_render_nodes_and_software_devices_are_not_cards(self) -> None:
        self.amd()
        for name in ("card0-HDMI-A-1", "renderD128", "version"):
            (self.root / "drm" / name).mkdir()
        self.node("card1", self.device("virtual", "0x0000", "0x0000", "vkms"))
        self.assertEqual([one["id"] for one in linux.cards()], ["card0"])

    def test_cards_come_in_the_kernels_numbering_not_alphabetical(self) -> None:
        for number in (10, 2):
            self.node(f"card{number}",
                      self.device(f"0000:0{number % 10}:00.0", "0x1002", "0x73bf", "amdgpu"))
        self.assertEqual([one["id"] for one in linux.cards()], ["card2", "card10"])


class NvtopTests(Tree):
    def vendorless(self, name: str = "gpu", driver: str = "v3d", renders: bool = True) -> None:
        path = self.root / "devices" / name
        path.mkdir()
        (self.root / "drivers" / driver).mkdir()
        (path / "driver").symlink_to(self.root / "drivers" / driver)
        if renders:
            (path / "drm" / "renderD128").mkdir(parents=True)
        self.node("card1", path)

    def test_a_display_controller_that_draws_nothing_is_not_a_card(self) -> None:
        self.vendorless("display", "vc4", renders=False)
        with mock.patch.object(graphics.tools, "resolve", return_value=missing(NVTOP)):
            self.assertEqual(linux.cards(), [])

    def test_with_nothing_in_sysfs_nvtop_lists_the_cards_and_without_it_there_are_none(
            self) -> None:
        with mock.patch.object(graphics.tools, "resolve", return_value=missing(NVTOP)):
            self.assertEqual(linux.cards(), [])
        text = '[{"device_name": "Radeon", "gpu_util": "9%"}]'
        with mock.patch.object(graphics.tools, "resolve", return_value=found(NVTOP)), \
                mock.patch.object(graphics.subprocess, "run", return_value=_done(text)):
            self.assertEqual(linux.cards()[0]["source"], "nvtop")

    @needs_colons_in_names
    def test_a_card_with_no_pci_vendor_is_read_by_nvtop_and_the_others_are_not(self) -> None:
        self.vendorless()
        self.node("card0", self.device("0000:03:00.0", "0x1002", "0x73bf", "amdgpu",
                                       {"gpu_busy_percent": "1"}))
        entries = ('[{"device_name": "Radeon", "gpu_util": "9%"},'
                   ' {"device_name": "V3D 4.2", "gpu_util": "40%", "temp": "55C"}]')
        with mock.patch.object(graphics.tools, "resolve", return_value=found(NVTOP)), \
                mock.patch.object(graphics.subprocess, "run", return_value=_done(entries)):
            mine, other = linux.cards()
        self.assertEqual((mine["source"], mine["busy"]), ("linux", 1.0))
        self.assertEqual((other["id"], other["name"], other["source"], other["busy"],
                          other["temperature"]), ("card1", "V3D 4.2", "nvtop", 40.0, 55.0))

    def test_a_card_with_no_pci_vendor_says_why_where_nvtop_cannot_read_it(self) -> None:
        self.vendorless()
        with mock.patch.object(graphics.tools, "resolve", return_value=missing(NVTOP)):
            card = linux.cards()[0]
        self.assertEqual((card["name"], card["vendor"], card["reason"]),
                         ("v3d", "other", "nvtop is not installed"))
        with mock.patch.object(graphics.tools, "resolve", return_value=missing(NVTOP)), \
                mock.patch.object(graphics, "_lister", return_value=linux):
            self.assertEqual(graphics.read()["cards"][0]["reason"], "nvtop is not installed")


@needs_colons_in_names
class IntelTests(Tree):
    ADDRESS = "0000:00:02.0"

    def intel(self, driver: str = "i915") -> None:
        self.node("card0", self.device(self.ADDRESS, "0x8086", "0x3e92", driver),
                  {"gt_cur_freq_mhz": "650\n"})

    def client(self, pid: int, fd: int, text: str) -> None:
        (self.root / "proc" / str(pid) / "fd").mkdir(parents=True, exist_ok=True)
        (self.root / "proc" / str(pid) / "fdinfo").mkdir(parents=True, exist_ok=True)
        link = self.root / "proc" / str(pid) / "fd" / str(fd)
        link.unlink(missing_ok=True)
        link.symlink_to("/dev/dri/card0")
        (self.root / "proc" / str(pid) / "fdinfo" / str(fd)).write_text(text)

    def info(self, ident: int, render_ns: int, video_ns: int = 0, address: str = "") -> str:
        return (f"pos:\t0\ndrm-driver:\ti915\ndrm-pdev:\t{address or self.ADDRESS}\n"
                f"drm-client-id:\t{ident}\ndrm-engine-render:\t{render_ns} ns\n"
                f"drm-engine-video:\t{video_ns} ns\ndrm-engine-capacity-video:\t2\n")

    def sample(self, at: float) -> dict:
        with mock.patch.object(linux.time, "monotonic", return_value=at):
            return linux.cards()[0]

    def test_the_clock_is_read_and_the_first_sample_has_no_busy_figure(self) -> None:
        self.intel()
        self.client(10, 4, self.info(1, 1_000_000_000))
        card = self.sample(100.0)
        self.assertEqual((card["name"], card["source"], card["clock"]),
                         ("Intel UHD Graphics 630", "linux", 650.0))
        self.assertNotIn("busy", card)

    def test_busy_is_the_busiest_engines_share_of_the_time_between_samples(self) -> None:
        self.intel()
        self.client(10, 4, self.info(1, 1_000_000_000))
        self.sample(100.0)
        self.client(10, 4, self.info(1, 2_000_000_000, 1_000_000_000))
        self.client(10, 5, self.info(1, 2_000_000_000, 1_000_000_000))
        self.client(20, 3, self.info(7, 0))
        self.assertAlmostEqual(self.sample(102.0)["busy"], 50.0)

    def test_an_engine_class_with_two_engines_is_a_share_of_both(self) -> None:
        self.intel()
        self.client(10, 4, self.info(1, 0, 0))
        self.sample(100.0)
        self.client(10, 4, self.info(1, 0, 1_000_000_000))
        self.assertAlmostEqual(self.sample(101.0)["busy"], 50.0)

    def test_clients_add_up_and_the_figure_stops_at_a_hundred(self) -> None:
        self.intel()
        self.client(10, 4, self.info(1, 0))
        self.client(20, 4, self.info(2, 0))
        self.sample(100.0)
        self.client(10, 4, self.info(1, 1_500_000_000))
        self.client(20, 4, self.info(2, 1_500_000_000))
        self.assertEqual(self.sample(101.0)["busy"], 100.0)

    def test_a_client_that_appears_between_samples_counts_from_its_next_one(self) -> None:
        self.intel()
        self.client(10, 4, self.info(1, 0))
        self.sample(100.0)
        self.client(20, 4, self.info(2, 9_000_000_000))
        self.assertAlmostEqual(self.sample(101.0)["busy"], 0.0)

    def test_a_client_held_by_two_processes_counts_once(self) -> None:
        self.intel()
        self.client(10, 4, self.info(1, 0))
        self.client(11, 4, self.info(1, 0))
        self.sample(100.0)
        self.client(10, 4, self.info(1, 500_000_000))
        self.client(11, 4, self.info(1, 500_000_000))
        self.assertAlmostEqual(self.sample(101.0)["busy"], 50.0)

    def test_a_window_too_short_or_gone_stale_gives_no_figure_and_keeps_the_baseline(
            self) -> None:
        self.intel()
        self.client(10, 4, self.info(1, 0))
        self.sample(100.0)
        self.client(10, 4, self.info(1, 100_000_000))
        self.assertNotIn("busy", self.sample(100.1))
        self.assertAlmostEqual(self.sample(101.0)["busy"], 10.0)
        self.assertNotIn("busy", self.sample(500.0))

    def test_no_client_on_the_card_leaves_busy_out_rather_than_zero(self) -> None:
        self.intel()
        self.sample(100.0)
        self.assertNotIn("busy", self.sample(101.0))

    def test_another_cards_clients_are_not_this_cards(self) -> None:
        self.intel()
        self.client(10, 4, self.info(1, 0, address="0000:03:00.0"))
        self.sample(100.0)
        self.client(10, 4, self.info(1, 1_000_000_000, address="0000:03:00.0"))
        self.assertNotIn("busy", self.sample(101.0))

    def test_xe_counts_cycles_out_of_the_cycles_that_passed(self) -> None:
        self.intel("xe")

        def info(used: int, total: int) -> str:
            return (f"drm-driver:\txe\ndrm-pdev:\t{self.ADDRESS}\ndrm-client-id:\t3\n"
                    f"drm-cycles-rcs:\t{used}\ndrm-total-cycles-rcs:\t{total}\n")
        self.client(10, 4, info(100, 1000))
        self.sample(100.0)
        self.client(10, 4, info(400, 2000))
        self.assertAlmostEqual(self.sample(101.0)["busy"], 30.0)

    def test_the_clock_comes_from_whichever_file_the_driver_has(self) -> None:
        self.intel()
        (self.root / "drm" / "card0" / "gt_cur_freq_mhz").unlink()
        self.assertNotIn("source", linux.cards()[0])
        newer = self.root / "drm" / "card0" / "gt" / "gt0"
        newer.mkdir(parents=True)
        (newer / "rps_cur_freq_mhz").write_text("700")
        self.assertEqual(linux.cards()[0]["clock"], 700.0)


class PairTests(unittest.TestCase):
    def test_an_address_pairs_with_its_own_reading_and_nothing_else(self) -> None:
        card = {"pci": "a", "name": "X"}
        self.assertEqual(graphics.pair([card], [{"pci": "b", "name": "X"}]), [])
        self.assertEqual(graphics.pair([card], [{"pci": "a", "name": "Y"}]),
                         [(card, {"pci": "a", "name": "Y"})])

    def test_names_pair_before_order_and_either_may_hold_the_other(self) -> None:
        pairs = graphics.pair([{"name": "V3D"}, {"name": "Mali"}],
                              [{"name": "mali g52"}, {"name": "v3d 4.2"}])
        self.assertEqual([(a["name"], b["name"]) for a, b in pairs],
                         [("V3D", "v3d 4.2"), ("Mali", "mali g52")])

    def test_order_pairs_only_where_the_counts_agree(self) -> None:
        cards = [{"name": "one"}, {"name": "two"}]
        self.assertEqual(len(graphics.pair(cards, [{"name": "x"}, {"name": "y"}])), 2)
        self.assertEqual(graphics.pair(cards, [{"name": "x"}]), [])


if __name__ == "__main__":
    unittest.main()
