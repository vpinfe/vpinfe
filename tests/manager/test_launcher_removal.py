"""What the Remove confirm says a launcher's tables will do once it is gone."""

from __future__ import annotations

import unittest

from manager.frontend import launchers

KEPT = "The program and any file it points at stay where they are."


def _going(*groups: tuple[str, int]) -> dict:
    return {"tables": sum(count for _name, count in groups),
            "fallbacks": [{"display_name": name, "tables": count} for name, count in groups]}


class RemovalWordsTests(unittest.TestCase):
    def test_a_launcher_no_table_uses_only_says_the_files_stay(self) -> None:
        self.assertEqual(launchers.removal_words(_going()), (KEPT, []))

    def test_the_count_comes_first_and_names_where_they_go(self) -> None:
        detail, lines = launchers.removal_words(_going(("Visual Pinball X", 12)))

        self.assertEqual(detail, "12 tables use it. They will launch with Visual Pinball X "
                                 f"instead. {KEPT}")
        self.assertEqual(lines, [])

    def test_one_table_is_one(self) -> None:
        detail, _lines = launchers.removal_words(_going(("Visual Pinball X", 1)))

        self.assertTrue(detail.startswith("1 table uses it. It will launch with"))

    def test_tables_with_nowhere_to_go_are_said_so(self) -> None:
        detail, _lines = launchers.removal_words(_going(("", 3)))

        self.assertIn("no other launcher can play them", detail)

    def test_tables_that_split_get_a_line_per_launcher(self) -> None:
        detail, lines = launchers.removal_words(_going(("Visual Pinball X", 4), ("", 1)))

        self.assertTrue(detail.startswith(KEPT))
        self.assertEqual(lines, ["4 launch with Visual Pinball X", "1 has no launcher left"])


if __name__ == "__main__":
    unittest.main()
