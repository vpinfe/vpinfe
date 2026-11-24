"""A candidate's frame with no picture says why, where the row's caller knows."""

from __future__ import annotations

import unittest

from nicegui import ui

from manager.kit import candidates
from manager.library import vps_match


def _tips(body: ui.element) -> list[str]:
    return [one.text for one in body.descendants() if isinstance(one, ui.tooltip)]


def _drawn(src: str, missing: str = "") -> ui.element:
    with ui.column() as body:
        candidates.choice(src, "Centaur", "Bally 1981", missing=missing)
    return body


class EmptyFrameTests(unittest.TestCase):
    def test_an_empty_frame_says_what_it_was_given(self) -> None:
        self.assertEqual(["No picture here"], _tips(_drawn("", "No picture here")))

    def test_a_frame_with_a_picture_says_nothing_about_absence(self) -> None:
        self.assertNotIn("No picture here",
                         _tips(_drawn("https://example.invalid/a.png", "No picture here")))

    def test_a_frame_nobody_explained_stays_quiet(self) -> None:
        self.assertEqual([], _tips(_drawn("")))

    def test_a_vps_entry_with_no_picture_says_so(self) -> None:
        with ui.column() as body:
            vps_match.entry_row({"name": "Centaur", "img_url": ""})

        self.assertEqual([vps_match.NO_PICTURE], _tips(body))


if __name__ == "__main__":
    unittest.main()
