"""A screen's reported size is offered only where it could be a real panel's."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from common.host import screen_size


class PlausibleTests(unittest.TestCase):
    def test_a_real_panel_is_plausible(self) -> None:
        self.assertTrue(screen_size.plausible(344, 222))
        self.assertTrue(screen_size.plausible(1210, 680))

    def test_a_screen_that_reports_nothing_is_not(self) -> None:
        self.assertFalse(screen_size.plausible(0, 0))
        self.assertFalse(screen_size.plausible(344, 0))

    def test_a_placeholder_is_not(self) -> None:
        # Virtual machines and screens with no EDID say 1 x 1 or a square.
        self.assertFalse(screen_size.plausible(1, 1))
        self.assertFalse(screen_size.plausible(300, 300))
        self.assertFalse(screen_size.plausible(160, 90000))

    def test_a_side_beyond_a_television_is_not(self) -> None:
        self.assertFalse(screen_size.plausible(4000, 2250))

    def test_the_short_side_cannot_be_the_longer(self) -> None:
        self.assertFalse(screen_size.plausible(222, 344))


class ReportedTests(unittest.TestCase):
    def _monitors(self, *sizes: tuple[int, int]) -> mock._patch:
        return mock.patch.object(
            screen_size.display_service, "get_display_monitors",
            return_value=[SimpleNamespace(width_mm=w, height_mm=h) for w, h in sizes])

    def test_the_long_side_comes_first_whichever_way_the_screen_is_turned(self) -> None:
        with self._monitors((222, 344)):
            self.assertEqual((344.0, 222.0), screen_size.reported_mm(0, "linux"))

    def test_the_screen_asked_for_is_the_one_counted_from_zero(self) -> None:
        with self._monitors((1, 1), (527, 296)):
            self.assertEqual((527.0, 296.0), screen_size.reported_mm(1, "linux"))
            self.assertIsNone(screen_size.reported_mm(0, "linux"))

    def test_a_screen_that_is_not_there_has_no_size(self) -> None:
        with self._monitors((527, 296)):
            self.assertIsNone(screen_size.reported_mm(3, "linux"))
            self.assertIsNone(screen_size.reported_mm(-1, "linux"))

    def test_an_implausible_report_is_no_size(self) -> None:
        with self._monitors((1, 1)):
            self.assertIsNone(screen_size.reported_mm(0, "linux"))

    def test_a_host_that_cannot_be_read_has_no_size(self) -> None:
        with mock.patch.object(screen_size.display_service, "get_display_monitors",
                               side_effect=RuntimeError("no display")), \
             self.assertLogs("vpinfe.common.host.screen_size", level="WARNING"):
            self.assertIsNone(screen_size.reported_mm(0, "linux"))


if __name__ == "__main__":
    unittest.main()
