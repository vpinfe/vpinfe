"""Protects: (e) the Plasma hand-over reads KWin's windows and keyboard holder and activates the
right window, whichever of Plasma 5 and 6 names things which way.

The bus is a fake; no KWin is asked.
"""

from __future__ import annotations

import json
import unittest

from common.host import front_kde
from common.host.front import Window


class _Bus:
    service = ":1.77"

    def __init__(self, report: object = None) -> None:
        self.report = report
        self.ran: list[str] = []
        self.refuses = False

    def run(self, source: str) -> str:
        self.ran.append(source)
        if self.refuses and "activeWindow = " in source:
            raise OSError("no such window")
        return "done" if "activeWindow = " in source else json.dumps(self.report)


REPORT = {"active": "{b}", "windows": [{"id": "{a}", "pid": 4001, "title": "Player"},
                                         {"id": "{b}", "pid": 4002, "title": "Table"}]}


class KWinBackendTests(unittest.TestCase):
    def _backend(self, report: object = REPORT):
        self.now = 0.0
        bus = _Bus(report)
        return front_kde.KWinBackend(bus, clock=lambda: self.now), bus

    def test_windows_are_what_the_script_reports(self) -> None:
        backend, _ = self._backend()

        self.assertEqual(backend.windows(), [Window("{a}", 4001, "Player"),
                                             Window("{b}", 4002, "Table")])

    def test_the_holder_is_the_active_window(self) -> None:
        backend, _ = self._backend()

        self.assertEqual(backend.holder(), Window("{b}", 4002, "Table"))

    def test_no_active_window_is_nobody(self) -> None:
        backend, _ = self._backend({**REPORT, "active": ""})

        self.assertIsNone(backend.holder())

    def test_a_poll_is_one_script_and_a_stale_answer_is_asked_again(self) -> None:
        backend, bus = self._backend()

        backend.windows()
        backend.holder()
        self.assertEqual(len(bus.ran), 1)
        self.now = 5.0
        backend.holder()

        self.assertEqual(len(bus.ran), 2)

    def test_an_answer_that_is_not_the_scripts_is_an_error(self) -> None:
        reports: list[object] = ["nonsense", {"windows": []},
                                 {"active": "", "windows": [{"id": "x"}]}]
        for report in reports:
            with self.subTest(report=report):
                backend, _ = self._backend(report)
                with self.assertRaises(OSError):
                    backend.windows()

    def test_a_hand_over_runs_a_script_that_names_the_window_and_says_when_it_is_done(self) -> None:
        backend, bus = self._backend()
        backend.windows()

        said = backend.hand_over(Window("{a}", 4001, "Player"))

        source = bus.ran[-1]
        self.assertEqual(said, "")
        self.assertIn('"{a}"', source)
        self.assertIn("activeWindow", source)
        self.assertIn("activeClient", source)
        self.assertIn('callDBus(":1.77", "/vpinfe/front"', source)

    def test_a_hand_over_makes_the_next_answer_fresh(self) -> None:
        backend, bus = self._backend()
        backend.windows()

        backend.hand_over(Window("{a}", 4001, "Player"))
        backend.holder()

        self.assertEqual(len(bus.ran), 3)

    def test_a_refusal_comes_back_as_words(self) -> None:
        backend, bus = self._backend()
        bus.refuses = True

        self.assertIn("no such window", backend.hand_over(Window("{a}", 4001, "Player")))


class ScriptTests(unittest.TestCase):
    def test_the_report_goes_back_to_this_process_by_its_bus_name(self) -> None:
        source = front_kde.report_script(":1.77")

        self.assertIn('callDBus(":1.77", "/vpinfe/front"', source)
        self.assertIn("windowList", source)
        self.assertIn("clientList", source)

    def test_the_desktop_and_panels_are_never_a_programs_window(self) -> None:
        source = front_kde.report_script(":1.77")

        for word in ("desktopWindow", "dock", "notification"):
            self.assertIn(f"w.{word}", source)

    def test_a_window_id_is_written_as_a_quoted_string(self) -> None:
        source = front_kde.activate_script('a"; evil(); "', ":1.77")

        self.assertIn(r'"a\"; evil(); \""', source)


class SelectionTests(unittest.TestCase):
    def test_plasma_is_the_desktop_name_containing_kde(self) -> None:
        self.assertTrue(front_kde.applies({"XDG_CURRENT_DESKTOP": "KDE"}))
        self.assertTrue(front_kde.applies({"XDG_CURRENT_DESKTOP": "ubuntu:KDE"}))
        self.assertFalse(front_kde.applies({"XDG_CURRENT_DESKTOP": "GNOME"}))
        self.assertFalse(front_kde.applies({}))


if __name__ == "__main__":
    unittest.main()
