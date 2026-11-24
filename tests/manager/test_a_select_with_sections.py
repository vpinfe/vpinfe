"""A dropdown can group its options under headings, and a heading is never an answer.

The headings are entries of the open list that cannot be chosen, so the index the client
sends for a real option still points into the options the caller gave.
"""

from __future__ import annotations

import unittest
from typing import Any

from nicegui import ui

from manager.kit import dropdown

OPTIONS = {"rom": "ROMs", "pov": "Points of View", "wheel": "Wheels", "cab": "Cabs"}
SECTIONS = {"rom": "Assets", "pov": "Assets", "wheel": "Media", "cab": "Media"}


def _shown(control: Any) -> list[str]:
    return [str(one["label"]) for one in control._props["options"]]


class ASelectWithSections(unittest.TestCase):
    def test_a_heading_stands_before_the_first_option_of_each_section(self) -> None:
        with ui.card():
            control = dropdown.Select(OPTIONS, value="rom", sections=SECTIONS)

        self.assertEqual(["Assets", "ROMs", "Points of View", "Media", "Wheels", "Cabs"],
                         _shown(control))

    def test_a_heading_cannot_be_chosen(self) -> None:
        with ui.card():
            control = dropdown.Select(OPTIONS, value="rom", sections=SECTIONS)

        headings = [one for one in control._props["options"] if one.get("section")]
        self.assertEqual(["Assets", "Media"], [one["label"] for one in headings])
        self.assertTrue(all(one["disable"] for one in headings))

    def test_an_option_keeps_the_index_the_server_reads_it_by(self) -> None:
        with ui.card():
            control = dropdown.Select(OPTIONS, value="rom", sections=SECTIONS)

        real = {one["label"]: one["value"] for one in control._props["options"]
                if not one.get("section")}
        self.assertEqual({"ROMs": 0, "Points of View": 1, "Wheels": 2, "Cabs": 3}, real)

    def test_a_redraw_of_the_list_draws_each_heading_once(self) -> None:
        with ui.card():
            control = dropdown.Select(OPTIONS, value="rom", sections=SECTIONS)
            control.set_value("cab")
            control.update()

        self.assertEqual(2, sum(1 for one in control._props["options"] if one.get("section")))
        self.assertEqual("Cabs", control._props["model-value"]["label"])

    def test_a_select_without_sections_is_left_whole(self) -> None:
        with ui.card():
            control = dropdown.Select(OPTIONS, value="rom")

        self.assertEqual(list(OPTIONS.values()), _shown(control))


if __name__ == "__main__":
    unittest.main()
