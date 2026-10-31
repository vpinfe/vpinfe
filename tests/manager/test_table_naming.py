"""How a table is named, and the helpers behind the game's default-table list."""

import unittest
from typing import Any

from common.i18n import t
from manager.kit import game_tables


def _table(**extra: Any) -> dict[str, Any]:
    return {"id": "t1", "filename": "Sample Game - 1.0.vpx", **extra}


def _matched(**source: Any) -> dict[str, Any]:
    return {"vps_file_id": "file-1", "vps_version": "1.3", "authors": ["Burger", "DaRdog"],
            "version": "1.3", **source}


class ATableIsNamedByItsRelease(unittest.TestCase):
    def test_a_matched_table_takes_the_release_s_version_and_authors(self) -> None:
        table = _table(version="1.0", author="Someone", source=_matched())

        self.assertEqual(game_tables.table_name(table), "1.3 · Burger, DaRdog")

    def test_the_version_is_the_one_listed_when_it_was_matched(self) -> None:
        table = _table(source=_matched(vps_version="1.2", version="1.3"))

        self.assertEqual(game_tables.table_name(table), "1.2 · Burger, DaRdog")

    def test_an_unmatched_table_is_named_by_its_file_s_own_data(self) -> None:
        table = _table(version="1.0", authors=["Someone"])

        self.assertEqual(game_tables.table_name(table), "1.0 · Someone")

    def test_a_table_the_file_says_nothing_about_is_named_by_its_file(self) -> None:
        self.assertEqual(game_tables.table_name(_table()), "Sample Game - 1.0.vpx")

    def test_a_release_the_catalog_no_longer_lists_names_nothing(self) -> None:
        delisted = _table(version="1.0", source={"vps_file_id": "file-1", "vps_version": "1.3"})

        self.assertEqual(game_tables.table_name(delisted), "1.0")

    def test_the_file_s_own_data_is_never_overwritten(self) -> None:
        table = _table(version="1.0", source=_matched())

        game_tables.table_name(table)

        self.assertEqual(table["version"], "1.0")


class WhatTheFileSays(unittest.TestCase):
    def test_it_is_shown_where_the_release_names_the_table_differently(self) -> None:
        table = _table(version="1.0", authors=["Someone"], source=_matched())

        self.assertEqual(game_tables.file_says(table), "1.0 · Someone")

    def test_it_is_blank_where_the_two_agree(self) -> None:
        table = _table(version="1.3", authors=["Burger", "DaRdog"], source=_matched())

        self.assertEqual(game_tables.file_says(table), "")

    def test_it_is_blank_where_the_file_reads_as_nothing(self) -> None:
        self.assertEqual(game_tables.file_says(_table(source=_matched())), "")

    def test_it_is_blank_for_an_unmatched_table(self) -> None:
        self.assertEqual(game_tables.file_says(_table(version="1.0")), "")


class TableDetailsFileFacts(unittest.TestCase):
    def test_a_matched_table_reads_what_the_file_says_on_one_line(self) -> None:
        from manager.workbench import game_table
        table = _table(version="1.0", authors=["Someone"], source=_matched())

        self.assertEqual(game_table._file_facts(table),
                         [(t("manager.workbench.file_says"), "1.0 · Someone")])

    def test_an_unmatched_table_reads_its_version_and_author(self) -> None:
        from manager.workbench import game_table
        table = _table(version="1.0", authors=["Someone"])

        self.assertEqual(game_table._file_facts(table),
                         [(t("word.version"), "1.0"), (t("word.author"), "Someone")])


class TheColumnsFollowTheName(unittest.TestCase):
    def test_a_matched_table_s_columns_are_the_release_s(self) -> None:
        table = _table(version="1.0", author="Someone", source=_matched())

        self.assertEqual(game_tables.named_version_and_authors(table),
                         ("1.3", "Burger, DaRdog"))

    def test_an_unmatched_table_s_columns_are_the_file_s(self) -> None:
        table = _table(version="1.0", authors=["Someone", "Else"])

        self.assertEqual(game_tables.named_version_and_authors(table),
                         ("1.0", "Someone, Else"))


class TheFileLine(unittest.TestCase):
    def test_it_drops_the_game_s_own_name_from_the_front(self) -> None:
        table = _table(version="1.0")

        self.assertEqual(game_tables.file_line(table, "Sample Game"), "1.0.vpx")

    def test_it_keeps_the_whole_name_where_the_game_s_does_not_begin_it(self) -> None:
        table = _table(version="1.0")

        self.assertEqual(game_tables.file_line(table, "Other"), "Sample Game - 1.0.vpx")

    def test_it_is_blank_where_the_name_is_already_the_file(self) -> None:
        self.assertEqual(game_tables.file_line(_table(), "Sample Game"), "")


class TheDefaultList(unittest.TestCase):
    def _rows(self, **marks: Any) -> list[dict[str, Any]]:
        return [{"id": "a", "default": True, **marks.get("a", {})},
                {"id": "b", **marks.get("b", {})}]

    def test_a_game_is_locked_only_where_a_chosen_table_is_its_default(self) -> None:
        self.assertTrue(game_tables.is_locked(
            self._rows(a={"default_kind": game_tables.CHOSEN})))
        self.assertFalse(game_tables.is_locked(
            self._rows(a={"default_kind": game_tables.DERIVED})))

    def test_automatic_names_the_table_it_plays(self) -> None:
        rows = self._rows(b={"automatic": True})

        self.assertEqual((game_tables.automatic_table(rows) or {}).get("id"), "b")

    def test_a_locked_table_that_cannot_be_played_is_what_the_default_fell_from(self) -> None:
        rows = self._rows(a={"default_kind": game_tables.DERIVED},
                          b={"locked": True, "absent_since": "2026-10-01"})

        self.assertEqual((game_tables.fell_from(rows) or {}).get("id"), "b")

    def test_a_locked_table_that_is_the_default_fell_from_nothing(self) -> None:
        rows = self._rows(a={"locked": True, "default_kind": game_tables.CHOSEN})

        self.assertIsNone(game_tables.fell_from(rows))

    def test_locking_says_the_game_now_plays_it(self) -> None:
        table = _table(version="1.0")

        self.assertEqual(game_tables.locked_said("Sample Game", table),
                         t("manager.game_tables.result_locked", game="Sample Game",
                           table="1.0"))

    def test_going_back_to_automatic_says_what_it_plays(self) -> None:
        after = [_table(version="2.0", default=True)]

        self.assertEqual(game_tables.automatic_said("Sample Game", after),
                         t("manager.game_tables.result_automatic", game="Sample Game",
                           table="2.0"))


if __name__ == "__main__":
    unittest.main()
