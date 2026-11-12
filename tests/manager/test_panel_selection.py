"""The list a selection menu draws: the subject's own, with one-at-a-time verbs refused."""

from __future__ import annotations

import unittest

from manager.kit import panel


def verbs() -> list[panel.Verb]:
    return [panel.Verb("Restore", lambda: None),
            panel.Verb("Copy Path", lambda: None, one=True),
            panel.Verb("Delete", lambda: None, danger=True)]


class ForSelection(unittest.TestCase):
    def test_one_row_keeps_every_verb_as_it_was(self) -> None:
        entries = verbs()

        self.assertEqual(entries, panel.for_selection(entries, 1))

    def test_several_rows_dim_a_one_at_a_time_verb_with_no_words_under_it(self) -> None:
        shown = panel.for_selection(verbs(), 3)

        self.assertEqual(["Restore", "Copy Path", "Delete"], [v.label for v in shown])
        self.assertIsNone(shown[1].run)
        self.assertEqual("", shown[1].hint)

    def test_several_rows_leave_the_other_verbs_alone(self) -> None:
        entries = verbs()
        shown = panel.for_selection(entries, 3)

        self.assertEqual([entries[0], entries[2]], [shown[0], shown[2]])


if __name__ == "__main__":
    unittest.main()
