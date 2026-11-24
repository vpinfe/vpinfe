"""A screen's model name beside the list of screens."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from common.host import display_service, screen_names

ONE = SimpleNamespace(x=0, y=0, width=1920, height=1080)
TWO = SimpleNamespace(x=1920, y=0, width=1280, height=390)


class DisplayNamesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.found = [ONE]
        self.query = self.enterContext(mock.patch.object(
            display_service, "_query_monitors", side_effect=lambda: list(self.found)))
        self.names = self.enterContext(mock.patch.object(
            screen_names, "model_names",
            side_effect=lambda monitors: [f"Model {at}" for at, _ in enumerate(monitors)]))
        for name in ("_monitors_cache", "_names_cache"):
            self.addCleanup(setattr, display_service, name, None)
            setattr(display_service, name, None)

    def test_each_monitor_is_named_in_its_order(self) -> None:
        self.found = [ONE, TWO]

        self.assertEqual(display_service.get_display_names(), ["Model 0", "Model 1"])

    def test_the_monitors_are_named_from_the_list_they_are_listed_in(self) -> None:
        self.found = [ONE, TWO]

        display_service.get_display_names()

        self.names.assert_called_once_with([ONE, TWO])

    def test_the_names_are_read_once_until_the_screens_are_listed_again(self) -> None:
        display_service.get_display_names()
        display_service.get_display_names()

        self.assertEqual(self.names.call_count, 1)

    def test_listing_the_screens_again_reads_the_names_again(self) -> None:
        display_service.get_display_names()
        self.found = [ONE, TWO]

        display_service.get_display_monitors(refresh=True)

        self.assertEqual(display_service.get_display_names(), ["Model 0", "Model 1"])
        self.assertEqual(self.names.call_count, 2)

    def test_asking_for_a_refresh_lists_the_screens_again(self) -> None:
        display_service.get_display_names()
        self.found = [ONE, TWO]

        self.assertEqual(len(display_service.get_display_names(refresh=True)), 2)

    def test_what_comes_back_is_a_copy(self) -> None:
        display_service.get_display_names().append("changed")

        self.assertEqual(display_service.get_display_names(), ["Model 0"])

    def test_no_screens_are_no_names(self) -> None:
        self.found = []

        self.assertEqual(display_service.get_display_names(), [])


if __name__ == "__main__":
    unittest.main()
