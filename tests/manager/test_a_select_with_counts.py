"""A dropdown can set a number apart from the name it belongs to, and word its closed box
differently from its open list."""

from __future__ import annotations

import unittest
from typing import Any

from nicegui import ui

from manager.kit import dropdown

OPTIONS = {"asset": "Backglasses", "media": "Backglasses", "wheel": "Wheels"}


def _by_index(control: Any) -> dict[Any, dict[str, Any]]:
    return {one["value"]: one for one in control._props["options"]}


class ASelectWithCounts(unittest.TestCase):
    def test_the_label_stays_the_name_and_the_count_rides_beside_it(self) -> None:
        with ui.card():
            control = dropdown.Select(OPTIONS, value="wheel", counts={"asset": 6, "wheel": 1234})

        options = _by_index(control)
        self.assertEqual("Wheels", options[2]["label"])
        self.assertEqual("1,234", options[2]["count"])
        self.assertEqual("6", options[0]["count"])
        self.assertNotIn("count", options[1])

    def test_the_closed_box_carries_the_count_and_the_wording_for_it(self) -> None:
        with ui.card():
            control = dropdown.Select(OPTIONS, value="asset", counts={"asset": 6},
                                      shown={"asset": "Backglasses (Asset)"})

        self.assertEqual({"value": 0, "label": "Backglasses", "count": "6",
                          "shown": "Backglasses (Asset)"}, control._props["model-value"])

    def test_the_box_follows_a_new_pick(self) -> None:
        with ui.card():
            control = dropdown.Select(OPTIONS, value="asset", counts={"asset": 6, "wheel": 2},
                                      shown={"asset": "Backglasses (Asset)"})
            control.set_value("wheel")

        self.assertEqual({"value": 2, "label": "Wheels", "count": "2"},
                         control._props["model-value"])

    def test_counts_and_headings_share_the_open_list(self) -> None:
        with ui.card():
            control = dropdown.Select(OPTIONS, value="asset", counts={"asset": 6},
                                      sections={"asset": "Assets", "media": "Media",
                                                "wheel": "Media"})

        self.assertEqual(["Assets", "Backglasses", "Media", "Backglasses", "Wheels"],
                         [str(one["label"]) for one in control._props["options"]])
        self.assertEqual("6", control._props["options"][1]["count"])

    def test_a_select_given_neither_draws_as_it_always_did(self) -> None:
        with ui.card():
            control = dropdown.Select(OPTIONS, value="wheel")

        self.assertFalse(any("count" in one or "shown" in one
                             for one in control._props["options"]))
        self.assertEqual({"value": 2, "label": "Wheels"}, control._props["model-value"])
        self.assertNotIn("option", control.slots)
        self.assertNotIn("selected-item", control.slots)


if __name__ == "__main__":
    unittest.main()
