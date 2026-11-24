"""A section names its first group, whether or not other groups follow it."""

from __future__ import annotations

import unittest

from common.i18n import t
from manager.kit import panel
from manager.workbench.game_play import _play_section

GAME = t("manager.workbench.game_section")
TABLE = t("manager.workbench.table_section")
SCORES = {"state": "none", "table_id": "t1"}


def _headings(rows: list) -> list[str]:
    return [value for label, value in rows if label is panel.HEADING]


class HeadedTests(unittest.TestCase):
    def test_a_later_heading_gives_the_opening_group_one(self) -> None:
        rows = [("Last Played", "Never"), (panel.HEADING, "High Scores")]
        self.assertEqual(panel.headed(rows, "Game"), [(panel.HEADING, "Game"), *rows])

    def test_a_section_headed_from_the_top_is_left_alone(self) -> None:
        rows = [(panel.HEADING, "Game"), ("Last Played", "Never"),
                (panel.HEADING, "Table")]
        self.assertEqual(panel.headed(rows, "Other"), rows)

    def test_a_section_of_one_group_is_headed(self) -> None:
        rows = [("Last Played", "Never"), ("Times Played", "0")]
        self.assertEqual(panel.headed(rows, "Game"), [(panel.HEADING, "Game"), *rows])


class ThePlaySectionTests(unittest.TestCase):
    def test_a_game_with_no_table_chosen_opens_under_a_heading(self) -> None:
        context = {"game": {"user": {}}, "tables": [], "lens": ""}
        rows = _play_section(context, SCORES, {})
        self.assertEqual(_headings(rows), [GAME, t("manager.workbench.high_scores")])
        self.assertIs(rows[0][0], panel.HEADING)

    def test_a_chosen_table_keeps_its_one_heading_each(self) -> None:
        context = {"game": {"user": {}}, "tables": [{"id": "t1", "user": {}}],
                   "lens": "t1"}
        rows = _play_section(context, SCORES, {})
        self.assertEqual(_headings(rows),
                         [GAME, TABLE, t("manager.workbench.high_scores")])

    def test_a_game_without_scores_is_one_group_and_is_headed(self) -> None:
        context = {"game": {"user": {}}, "tables": [], "lens": ""}
        self.assertEqual(_headings(_play_section(context, {}, {})), [GAME])


if __name__ == "__main__":
    unittest.main()
