"""A rail entry that opens an extension's own UI: a row that leaves the Manager in a new tab,
and never one of its views."""

from __future__ import annotations

import unittest
from typing import Any

from nicegui import ui

from manager import page
from manager.extensions import ext_links
from manager.kit import panel, verbs

KEY = ext_links.view_key("linked", "panel")
EXTENSIONS = [{"name": "linked", "links": [
    {"key": "panel", "area": "library", "label": "Open the panel", "tip": "Opens in a new tab",
     "url": "http://{host}:9100/ui"}]}]


def drawn(extensions: list[dict[str, Any]] = EXTENSIONS, **options: Any) -> tuple[ui.link, list]:
    labels: list = []
    with ui.column() as body:
        state = {"extensions": extensions, "host": "localhost"}
        page._nav_link(KEY, "Open the panel", "extension", state, labels, **options)
    (row,) = [one for one in body.descendants() if isinstance(one, ui.link)]
    return row, labels


class TheRow(unittest.TestCase):
    def test_opens_the_extensions_address_in_a_new_tab(self) -> None:
        row, _labels = drawn()

        self.assertEqual("http://localhost:9100/ui", row.props["href"])
        self.assertEqual("_blank", row.props["target"])
        self.assertEqual("noopener noreferrer", row.props["rel"])

    def test_tells_a_screen_reader_it_opens_a_new_tab(self) -> None:
        row, _labels = drawn()

        self.assertEqual("Open the panel, opens in a new tab", row.props["aria-label"])

    def test_is_marked_as_leaving_so_the_in_place_navigation_leaves_it_alone(self) -> None:
        row, _labels = drawn()

        self.assertIn("manager-nav-row--out", row.classes)
        self.assertIn("a.manager-nav-row:not(.manager-nav-row--out)", panel.NAV_CLICK)

    def test_gives_its_label_and_arrow_to_the_list_a_narrow_rail_hides(self) -> None:
        _row, labels = drawn()

        self.assertEqual(["Open the panel", verbs.OPEN_OUT],
                         [one.text if isinstance(one, ui.label) else one.props["name"]
                          for one in labels])

    def test_nested_under_a_parent_it_is_indented_and_held_with_its_siblings(self) -> None:
        held: list = []
        row, _labels = drawn(nested=True, held=held)

        self.assertIn("manager-nav-row--nested", row.classes)
        self.assertEqual([row], held)

    def test_whose_link_has_gone_draws_nothing(self) -> None:
        with ui.column() as body:
            page._nav_link(KEY, "Open the panel", "extension",
                           {"extensions": [], "host": "localhost"}, [])

        self.assertEqual([], [one for one in body.descendants() if isinstance(one, ui.link)])


if __name__ == "__main__":
    unittest.main()
