from __future__ import annotations

import plistlib
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from common.host import about, graphics, tools
from common.host.graphics import darwin, nvidia_smi, nvtop
from common.host.tools import NVIDIA_SMI, NVTOP
from tests.support.programs import found, missing, unusable


def _done(text: str, code: int = 0, error: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(["x"], code, stdout=text, stderr=error)


def _plist(*entries: dict) -> str:
    return plistlib.dumps(list(entries)).decode()


class NumberTests(unittest.TestCase):
    def test_the_figure_in_a_reading_with_a_unit(self) -> None:
        self.assertEqual(graphics.number("71C"), 71.0)
        self.assertEqual(graphics.number("54 %"), 54.0)
        self.assertEqual(graphics.number("142.3"), 142.3)
        self.assertEqual(graphics.number(7), 7.0)

    def test_a_reading_the_source_does_not_have_is_none_not_zero(self) -> None:
        for text in ("[N/A]", "[Not Supported]", "", None, "N/A"):
            with self.subTest(text=text):
                self.assertIsNone(graphics.number(text))


class NvtopTests(unittest.TestCase):
    def _cards(self, text: str) -> list[dict]:
        with mock.patch.object(graphics.tools, "resolve", return_value=found(NVTOP)), \
                mock.patch.object(graphics.subprocess, "run", return_value=_done(text)):
            return nvtop.cards()

    def test_values_with_units_are_read_for_their_figures(self) -> None:
        card = self._cards('[{"device_name": "Radeon Pro 5500M", "gpu_util": "54%",'
                           ' "temp": "71C", "gpu_clock": "1830MHz", "power_draw": "35W",'
                           ' "fan_speed": "20%", "mem_util": "12%"}]')[0]
        self.assertEqual((card["busy"], card["temperature"], card["clock"], card["power"]),
                         (54.0, 71.0, 1830.0, 35.0))
        self.assertEqual((card["vendor"], card["source"]), ("amd", "nvtop"))

    def test_a_reading_the_card_lacks_is_left_out(self) -> None:
        card = self._cards('[{"device_name": "GeForce GTX", "gpu_util": "10%",'
                           ' "temp": null, "fan_speed": "N/A"}]')[0]
        self.assertNotIn("temperature", card)
        self.assertNotIn("fan_percent", card)

    def test_two_unnamed_cards_read_apart(self) -> None:
        cards = self._cards('[{"gpu_util": "10%"}, {"device_name": null}]')
        self.assertEqual([one["name"] for one in cards], ["Card 1", "Card 2"])

    def test_a_lone_unnamed_card_is_unknown(self) -> None:
        self.assertEqual(self._cards('[{"temp": "40C"}]')[0]["name"], "Unknown")

    def test_it_says_why_it_cannot_read(self) -> None:
        with mock.patch.object(graphics.tools, "resolve", return_value=missing(NVTOP)):
            with self.assertRaises(graphics.UnreadableError) as caught:
                nvtop.cards()
        self.assertEqual(caught.exception.reason, "nvtop is not installed")
        for text, reason in (("not json", "Could not read what nvtop said"),
                             ("[]", "nvtop reported no cards"),
                             ("[1]", "nvtop reported nothing usable")):
            with self.subTest(text=text), self.assertRaises(graphics.UnreadableError) as caught:
                self._cards(text)
            self.assertEqual(caught.exception.reason, reason)


class NvidiaSmiTests(unittest.TestCase):
    ROW = "00000000:01:00.0, NVIDIA GeForce RTX 3070, 37, 1024, 8192, 52, 41.50, 1725, [N/A]"

    def _read(self, text: str) -> list[dict]:
        with mock.patch.object(graphics.tools, "resolve", return_value=found(NVIDIA_SMI)), \
                mock.patch.object(graphics.subprocess, "run", return_value=_done(text)):
            return nvidia_smi.read()

    def test_a_row_becomes_a_card_in_bytes(self) -> None:
        card = self._read(self.ROW)[0]
        self.assertEqual(card["pci"], "0000:01:00.0")
        self.assertEqual(card["memory_used"], 1024 * 1024 * 1024)
        self.assertEqual(card["memory_total"], 8192 * 1024 * 1024)
        self.assertEqual((card["busy"], card["temperature"], card["power"], card["clock"]),
                         (37.0, 52.0, 41.5, 1725.0))
        self.assertEqual(card["source"], "nvidia-smi")

    def test_a_field_printed_as_not_available_is_left_out(self) -> None:
        self.assertNotIn("fan_percent", self._read(self.ROW)[0])

    def test_it_says_when_it_is_missing_or_prints_nothing_usable(self) -> None:
        with mock.patch.object(graphics.tools, "resolve", return_value=missing(NVIDIA_SMI)), \
                self.assertRaises(graphics.UnreadableError) as caught:
            nvidia_smi.read()
        self.assertEqual(caught.exception.reason, "nvidia-smi is not installed")
        with self.assertRaises(graphics.UnreadableError):
            self._read("garbage")


class ProgramTests(unittest.TestCase):
    def _program(self, answer: tools.Found) -> str:
        with mock.patch.object(graphics.tools, "resolve", return_value=answer):
            return graphics.program(answer.tool)

    def test_a_program_that_runs_is_its_path(self) -> None:
        self.assertEqual(self._program(found(NVTOP)), str(Path("/usr/bin/nvtop")))

    def test_one_that_is_not_here_names_the_tool_so_its_remedy_can_be_offered(self) -> None:
        with self.assertRaises(graphics.UnreadableError) as caught:
            self._program(missing(NVTOP))
        self.assertEqual((caught.exception.reason, caught.exception.tool),
                         ("nvtop is not installed", "nvtop"))

    def test_one_that_does_not_run_says_why(self) -> None:
        with self.assertRaises(graphics.UnreadableError) as caught:
            self._program(unusable(NVTOP, tools.NO_SNAPSHOT))
        self.assertEqual((caught.exception.reason, caught.exception.detail,
                          caught.exception.tool),
                         ("nvtop is installed but can't be used",
                          "Too old to take a reading", "nvtop"))


class NeededTests(unittest.TestCase):
    def _listing(self, *cards: dict) -> None:
        patch = mock.patch.object(graphics, "_listed", return_value=(list(cards), "", ""))
        patch.start()
        self.addCleanup(patch.stop)

    def test_nvidia_smi_is_needed_beside_an_nvidia_card_only(self) -> None:
        self._listing({"vendor": "amd", "source": "linux"})
        self.assertFalse(graphics.needs_nvidia_smi())
        self._listing({"vendor": "amd", "source": "linux"}, {"vendor": "nvidia"})
        self.assertTrue(graphics.needs_nvidia_smi())

    def test_nvtop_is_needed_where_a_card_has_no_reader_of_its_own(self) -> None:
        self._listing({"vendor": "amd", "source": "linux"}, {"vendor": "nvidia"})
        self.assertFalse(graphics.needs_nvtop())
        self._listing({"vendor": "other"})
        self.assertTrue(graphics.needs_nvtop())

    def test_nvtop_stays_where_it_is_the_one_reading_the_card(self) -> None:
        self._listing({"vendor": "other", "source": "nvtop"})
        self.assertTrue(graphics.needs_nvtop())
        self._listing({"vendor": "nvidia", "source": "nvtop"})
        self.assertTrue(graphics.needs_nvtop())

    def test_nvtop_is_not_asked_for_a_card_it_is_never_run_on(self) -> None:
        self._listing({"vendor": "intel"}, {"vendor": "amd"})
        self.assertFalse(graphics.needs_nvtop())

    def test_neither_is_needed_where_no_card_is_listed(self) -> None:
        self._listing()
        self.assertFalse(graphics.needs_nvidia_smi())
        self.assertFalse(graphics.needs_nvtop())


class DarwinTests(unittest.TestCase):
    def _cards(self, text: str) -> list[dict]:
        with mock.patch.object(graphics.subprocess, "run", return_value=_done(text)):
            return darwin.cards()

    def test_apple_silicon_reports_use_of_the_system_memory_and_no_total(self) -> None:
        card = self._cards(_plist({
            "IORegistryEntryID": 4294968000, "model": "Apple M2 Pro",
            "vendor-id": bytes([0x6B, 0x10, 0, 0]),
            "PerformanceStatistics": {"Device Utilization %": 23,
                                      "In use system memory": 1_500_000_000}}))[0]
        self.assertEqual((card["name"], card["vendor"], card["source"]),
                         ("Apple M2 Pro", "apple", "macos"))
        self.assertEqual((card["busy"], card["memory_used"]), (23.0, 1_500_000_000.0))
        self.assertNotIn("memory_total", card)
        self.assertNotIn("temperature", card)

    def test_a_discrete_card_adds_vram_and_sensors(self) -> None:
        card = self._cards(_plist({
            "model": "AMD Radeon Pro 5500M",
            "PerformanceStatistics": {"vramUsedBytes": 1000, "vramFreeBytes": 3000,
                                      "Temperature(C)": 61, "Fan Speed(RPM)": 2100}}))[0]
        self.assertEqual((card["memory_used"], card["memory_total"]), (1000.0, 4000.0))
        self.assertEqual((card["temperature"], card["fan_rpm"], card["vendor"]),
                         (61.0, 2100.0, "amd"))

    def test_a_card_with_no_statistics_is_listed_without_readings(self) -> None:
        card = self._cards(_plist({"model": "Mystery"}))[0]
        self.assertEqual(set(card), {"id", "name", "vendor", "source"})

    def test_text_that_is_not_a_plist_says_so(self) -> None:
        with self.assertRaises(graphics.UnreadableError) as caught:
            self._cards("nonsense")
        self.assertEqual(caught.exception.reason, "Could not read what ioreg said")


class ReadTests(unittest.TestCase):
    def _lister(self, cards: list[dict] | Exception) -> mock.Mock:
        lister = mock.Mock(__name__="common.host.graphics.fake")
        if isinstance(cards, Exception):
            lister.cards.side_effect = cards
        else:
            lister.cards.return_value = cards
        patch = mock.patch.object(graphics, "_lister", return_value=lister)
        patch.start()
        self.addCleanup(patch.stop)
        return lister

    def test_no_cards_is_a_reason_and_not_an_empty_list_alone(self) -> None:
        self._lister([])
        self.assertEqual(graphics.read(),
                         {"cards": [], "reason": "No graphics card found", "detail": ""})

    def test_a_lister_that_cannot_run_gives_its_reason_and_what_it_said(self) -> None:
        self._lister(graphics.UnreadableError("nvtop is not installed", "not on PATH"))
        found = graphics.read()
        self.assertEqual((found["cards"], found["reason"], found["detail"]),
                         ([], "nvtop is not installed", "not on PATH"))

    def test_a_lister_that_breaks_is_a_reason_not_an_exception(self) -> None:
        self._lister(RuntimeError("boom"))
        found = graphics.read()
        self.assertEqual(found["cards"], [])
        self.assertIn("failed", found["reason"])

    def test_a_platform_with_no_lister_says_so(self) -> None:
        with mock.patch.object(graphics, "_lister", return_value=None), \
                mock.patch.object(graphics.platform, "system", return_value="Plan9"):
            self.assertEqual(graphics.read()["reason"], "Graphics cards are not read on Plan9")

    def test_nvidia_smi_replaces_what_the_lister_read_for_a_matching_card(self) -> None:
        self._lister([{"id": "1", "pci": "0000:01:00.0", "name": "RTX", "vendor": "nvidia",
                       "source": "nvtop", "busy": 5.0, "fan_percent": 10.0}])
        smi = [{"pci": "0000:01:00.0", "name": "NVIDIA RTX", "vendor": "nvidia",
                "source": "nvidia-smi", "busy": 37.0}]
        with mock.patch.object(nvidia_smi, "read", return_value=smi):
            card = graphics.read()["cards"][0]
        self.assertEqual((card["source"], card["busy"], card["id"]), ("nvidia-smi", 37.0, "1"))
        self.assertNotIn("fan_percent", card)
        self.assertNotIn("pci", card)

    def test_cards_with_no_address_are_paired_by_name_before_order(self) -> None:
        self._lister([{"id": "1", "name": "RTX B", "vendor": "nvidia", "source": "nvtop"},
                      {"id": "2", "name": "RTX A", "vendor": "nvidia", "source": "nvtop"}])
        smi = [{"pci": "0000:01:00.0", "name": "RTX A", "vendor": "nvidia",
                "source": "nvidia-smi", "busy": 10.0},
               {"pci": "0000:02:00.0", "name": "RTX B", "vendor": "nvidia",
                "source": "nvidia-smi", "busy": 20.0}]
        with mock.patch.object(nvidia_smi, "read", return_value=smi):
            cards = graphics.read()["cards"]
        self.assertEqual([(one["name"], one["busy"]) for one in cards],
                         [("RTX B", 20.0), ("RTX A", 10.0)])

    def test_cards_that_share_a_name_are_numbered_and_a_lone_one_is_not(self) -> None:
        self._lister([{"id": "1", "name": "RTX", "vendor": "other", "source": "nvtop"},
                      {"id": "2", "name": "RTX", "vendor": "other", "source": "nvtop"},
                      {"id": "3", "name": "Arc", "vendor": "other", "source": "nvtop"}])
        self.assertEqual(["RTX 1", "RTX 2", "Arc"],
                         [card["name"] for card in graphics.read()["cards"]])
        self.assertEqual((["RTX 1", "RTX 2", "Arc"], ""), graphics.names())

    def test_an_nvidia_smi_that_breaks_leaves_the_lister_readings(self) -> None:
        self._lister([{"id": "1", "name": "RTX", "vendor": "nvidia", "source": "nvtop",
                       "busy": 5.0}])
        with mock.patch.object(nvidia_smi, "read", side_effect=RuntimeError("boom")):
            card = graphics.read()["cards"][0]
        self.assertEqual((card["source"], card["busy"]), ("nvtop", 5.0))

    def test_an_nvidia_card_nothing_reads_is_listed_with_why(self) -> None:
        self._lister([{"id": "1", "pci": "0000:01:00.0", "name": "RTX", "vendor": "nvidia"}])
        missing = graphics.UnreadableError("nvidia-smi is not installed")
        with mock.patch.object(nvidia_smi, "read", side_effect=missing):
            card = graphics.read()["cards"][0]
        self.assertEqual(card["reason"], "nvidia-smi is not installed")
        self.assertNotIn("source", card)
        self.assertNotIn("busy", card)

    def test_a_card_nothing_reads_carries_what_was_said_and_the_tool_at_fault(self) -> None:
        self._lister([{"id": "1", "pci": "0000:01:00.0", "name": "RTX", "vendor": "nvidia"}])
        stuck = graphics.UnreadableError("nvidia-smi can't be used", "Ran but failed",
                                         "nvidia_smi")
        with mock.patch.object(nvidia_smi, "read", side_effect=stuck):
            card = graphics.read()["cards"][0]
        self.assertEqual((card["reason"], card["detail"], card["tool"]),
                         ("nvidia-smi can't be used", "Ran but failed", "nvidia_smi"))

    def test_a_reason_with_no_tool_or_detail_carries_neither(self) -> None:
        self._lister([{"id": "1", "name": "RTX", "vendor": "nvidia"}])
        with mock.patch.object(nvidia_smi, "read", side_effect=RuntimeError("boom")):
            card = graphics.read()["cards"][0]
        self.assertNotIn("tool", card)
        self.assertNotIn("detail", card)

    def test_a_card_of_another_vendor_never_calls_nvidia_smi(self) -> None:
        self._lister([{"id": "1", "name": "Radeon", "vendor": "amd", "source": "nvtop"}])
        with mock.patch.object(nvidia_smi, "read") as read:
            graphics.read()
        read.assert_not_called()

    def test_about_names_each_card_or_says_why_it_cannot(self) -> None:
        self._lister([{"id": "1", "name": "A", "vendor": "amd"},
                      {"id": "2", "name": "B", "vendor": "amd"}])
        self.assertEqual(about._graphics(), "A, B")
        self._lister(graphics.UnreadableError("nvtop is not installed"))
        self.assertEqual(about._graphics(), "nvtop is not installed")


class RunTests(unittest.TestCase):
    def test_a_program_that_hangs_times_out_with_a_reason(self) -> None:
        with mock.patch.object(graphics.subprocess, "run",
                               side_effect=subprocess.TimeoutExpired("x", 3)), \
                self.assertRaises(graphics.UnreadableError) as caught:
            graphics.run("x", [], "nvtop")
        self.assertEqual(caught.exception.reason, "nvtop timed out")

    def test_a_failure_carries_what_the_program_said(self) -> None:
        with mock.patch.object(graphics.subprocess, "run",
                               return_value=_done("", 1, "no driver")), \
                self.assertRaises(graphics.UnreadableError) as caught:
            graphics.run("x", [], "nvidia-smi")
        self.assertEqual((caught.exception.reason, caught.exception.detail),
                         ("nvidia-smi failed", "no driver"))


if __name__ == "__main__":
    unittest.main()
