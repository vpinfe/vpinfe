"""A field made with `room` shows a refusal under itself and drops it when the value changes."""

from __future__ import annotations

import unittest
from typing import Any

from manager.kit import dialog


class _Control:
    def __init__(self) -> None:
        self.props: dict[str, Any] = {}
        self.redrawn = 0

    def update(self) -> None:
        self.redrawn += 1


class SayingWhy(unittest.TestCase):
    def test_a_message_marks_the_field_and_carries_the_words(self) -> None:
        control = _Control()

        dialog.said(control, "Enter an address")

        self.assertIs(control.props["error"], True)
        self.assertEqual(control.props["error-message"], "Enter an address")
        self.assertEqual(control.redrawn, 1)

    def test_an_empty_message_takes_the_error_away(self) -> None:
        control = _Control()
        dialog.said(control, "Enter an address")

        dialog.said(control, "")

        self.assertIs(control.props["error"], False)
        self.assertEqual(control.props["error-message"], "")
        self.assertEqual(control.redrawn, 2)


if __name__ == "__main__":
    unittest.main()
