"""The High Scores group in a game's Play section, as rows before anything is drawn."""

from __future__ import annotations

import unittest
from dataclasses import asdict

from common.config.labels import field_label
from common.games import high_scores
from common.games.score_parser import ParsedEntry
from common.i18n import t
from manager.kit import panel
from manager.kit.play_facts import score_cells, score_lines
from manager.workbench.game_play import (
    _high_score_rows,
    _high_scores_read,
    _ranks_entries,
    _score_columns,
    _section_label,
)
from tests.games.test_high_scores import CHAMPION_AND_RANKED, MULTILINE, ONE_NUMBER


def _table(table_id: str, filename: str, rom: str, **rest) -> dict:
    return {"id": table_id, "filename": filename,
            "dependencies": {"pinmame": {"declared": rom}}, **rest}


TABLES = [_table("t1", "Example.vpx", "abc_l1", default=True,
                 overrides={"delete_nvram_on_close": False}),
          _table("t2", "Example (Mod).vpx", "abc_l1",
                 overrides={"delete_nvram_on_close": True})]


def _scores(reading: dict, *, table_id: str = "t1", new: tuple[int, ...] = ()) -> dict:
    held = high_scores.record(reading, None, "2026-09-27T12:00:00Z")
    held["new"] = list(new)
    return high_scores.shown("abc_l1", table_id, held)


def _context(lens: str = "", tables: list[dict] | None = None) -> dict:
    return {"lens": lens, "tables": tables if tables is not None else TABLES}


def _aside(rows: list) -> list[tuple[str, str]]:
    """The lines under the heading, as (text, hint)."""
    (drawn,) = [value for label, value in rows if label is panel.LEDE]
    return drawn.args[0]


def _labels(rows: list) -> list:
    return [label for label, _value in rows if label not in (panel.LEDE, panel.ASIDE)]


class HighScoreRowsTests(unittest.TestCase):
    def test_nothing_to_show_is_no_group(self) -> None:
        self.assertEqual(_high_score_rows(_context(), {}), [])

    def test_one_row_per_entry_the_section_named_on_its_first(self) -> None:
        rows = _high_score_rows(_context(), _scores(CHAMPION_AND_RANKED))

        self.assertEqual(_labels(rows), [panel.HEADING, "GRAND CHAMPION", "HIGH SCORES",
                                         "", ""])
        self.assertEqual(rows[0][1], t("manager.workbench.high_scores"))

    def test_a_section_name_is_the_machines_word_not_a_catalog_one(self) -> None:
        reading = {"score_kind": "Leaderboard", "entries": [
            {"section": "ZORBLAX CHAMPION", "rank": None, "initials": "ABC", "score": 1}]}

        (label,) = _labels(_high_score_rows(_context(), _scores(reading)))[1:]

        self.assertEqual(field_label(label), "Zorblax Champion")

    def test_a_one_number_machine_is_one_row_under_the_maps_name(self) -> None:
        rows = _high_score_rows(_context(), _scores(ONE_NUMBER, new=(0,)))

        self.assertEqual(_labels(rows)[1:], ["HIGHEST SCORE"])

    def test_lines_past_the_first_sit_under_their_row(self) -> None:
        rows = _high_score_rows(_context(), _scores(MULTILINE))

        self.assertEqual([label for label, _value in rows].count(panel.ASIDE), 1)

    def test_a_state_with_nothing_to_list_is_one_line(self) -> None:
        for state in ("none", "unsupported", "unreadable"):
            with self.subTest(state=state):
                scores = high_scores.shown("abc_l1", "t1", None, state=state, reason="why")

                rows = _high_score_rows(_context(), scores)

                self.assertEqual([label for label, _value in rows],
                                 [panel.HEADING, panel.LEDE])

    def test_whose_table_is_named_only_where_the_roms_differ(self) -> None:
        mixed = [TABLES[0], _table("t2", "Example (Mod).vpx", "abc_mod")]

        same = _high_score_rows(_context(), _scores(CHAMPION_AND_RANKED))
        differ = _high_score_rows(_context(tables=mixed), _scores(CHAMPION_AND_RANKED))
        under_table = _high_score_rows(_context("t1", mixed), _scores(CHAMPION_AND_RANKED))

        said = [[text for text, _hint in _aside(rows)] for rows in (same, differ, under_table)]
        self.assertEqual([len(lines) for lines in said], [1, 2, 1])
        self.assertTrue(said[1][0].startswith("From"), said[1])

    def test_a_table_that_clears_on_exit_says_so_instead_of_when(self) -> None:
        rows = _high_score_rows(_context("t2"), _scores(CHAMPION_AND_RANKED, table_id="t2"))

        self.assertEqual(_aside(rows), [(t("manager.workbench.high_scores_cleared"), "")])


def _group(reading: dict, *, players: dict[int, str] | None = None) -> dict:
    """The wire's sections for `reading`, with `players` naming entries by position."""
    shown = _scores(reading)
    flat = [entry for section in shown["sections"] for entry in section["entries"]]
    for position, entry in enumerate(flat):
        entry["player"] = (players or {}).get(position, "")
    return shown


def _columns(shown: dict) -> dict:
    sections = shown["sections"]
    cells = [[score_cells(entry) for entry in section["entries"]] for section in sections]
    return _score_columns(sections, cells, [_ranks_entries(section) for section in sections])


class ScoreColumnsTests(unittest.TestCase):
    def test_a_rank_is_drawn_only_where_a_section_ranks_more_than_one_entry(self) -> None:
        both = _columns(_group(CHAMPION_AND_RANKED))
        champion_only = _columns(_group({"score_kind": "Leaderboard", "entries": [
            CHAMPION_AND_RANKED["entries"][0], CHAMPION_AND_RANKED["entries"][1]]}))

        self.assertTrue(both["rank"])
        self.assertFalse(champion_only["rank"])

    def test_a_column_nothing_needs_is_not_drawn(self) -> None:
        columns = _columns(_group(ONE_NUMBER))

        self.assertEqual((columns["rank"], columns["initials"], columns["player"],
                          columns["unit"]), (False, False, False, False))

    def test_the_player_column_is_drawn_when_any_entry_names_one(self) -> None:
        self.assertFalse(_columns(_group(CHAMPION_AND_RANKED))["player"])
        self.assertTrue(_columns(_group(CHAMPION_AND_RANKED, players={1: "Alex"}))["player"])

    def test_the_unit_column_is_drawn_when_any_entry_has_a_unit(self) -> None:
        self.assertTrue(_columns(_group(MULTILINE))["unit"])

    def test_every_row_is_sent_the_widths_of_the_longest_entry(self) -> None:
        shown = _group(CHAMPION_AND_RANKED, players={0: "Alexandra", 1: "DJ MAX"})
        style = _columns(shown)["style"]

        self.assertIn("--hs-rank: 1.0;", style)
        self.assertIn("--hs-init: 3.6;", style)
        # 200,000,000: nine digits and two separators.
        self.assertIn("--hs-score: 9.7;", style)
        self.assertIn("--hs-player: 11.7;", style)
        self.assertIn("--hs-unit: 0.0", style)

    def test_a_unit_is_as_wide_as_its_longest_suffix(self) -> None:
        self.assertIn("--hs-unit: 4.5", _columns(_group(MULTILINE))["style"])

    def test_letters_in_a_score_make_its_column_wider_than_digits(self) -> None:
        self.assertIn("--hs-score: 13.2;", _columns(_group(MULTILINE))["style"])


class SectionLabelTests(unittest.TestCase):
    def test_a_lone_section_named_like_the_heading_carries_no_label(self) -> None:
        sections = _group({"score_kind": "Leaderboard", "entries": [
            asdict(ParsedEntry(section="HIGH SCORES", rank=1, initials="OWN",
                               score=150_000_000))]})["sections"]

        self.assertEqual(_section_label(sections, sections[0]), "")

    def test_a_section_among_several_keeps_its_name(self) -> None:
        sections = _group(CHAMPION_AND_RANKED)["sections"]

        self.assertEqual([_section_label(sections, one) for one in sections],
                         ["GRAND CHAMPION", "HIGH SCORES"])

    def test_a_lone_section_with_another_name_keeps_it(self) -> None:
        sections = _group(ONE_NUMBER)["sections"]

        self.assertEqual(_section_label(sections, sections[0]), "HIGHEST SCORE")


class ReadLineTests(unittest.TestCase):
    def test_a_text_file_is_named_in_the_line(self) -> None:
        plain, _ = _high_scores_read("2020-01-01T00:00:00Z", False)
        named, _ = _high_scores_read("2020-01-01T00:00:00Z", False, "Scores.txt")

        self.assertNotIn("Scores.txt", plain)
        self.assertTrue(named.startswith("Read from Scores.txt after the last game - "), named)

    def test_a_reading_with_no_time_says_after_the_last_game(self) -> None:
        self.assertEqual(_high_scores_read("", False, "Scores.txt")[0],
                         "Read from Scores.txt after the last game")

    def test_a_reading_just_made_says_just_now(self) -> None:
        self.assertEqual(_high_scores_read("2999-01-01T00:00:00Z", False, "Scores.txt")[0],
                         "Read from Scores.txt just now")


class ScoreCellsTests(unittest.TestCase):
    def test_the_unit_is_the_machines_suffix_in_lower_case(self) -> None:
        loops = {"score": 12, "prefix": "", "suffix": " LOOPS", "text": "12 LOOPS"}
        way = {"score": 4, "prefix": "", "suffix": "-WAY", "text": "4-WAY"}

        self.assertEqual(score_cells(loops), ("12", "loops", []))
        self.assertEqual(score_cells(way), ("4", "-way", []))

    def test_a_prefix_stays_with_the_number(self) -> None:
        entry = {"score": 5000, "prefix": "$ ", "suffix": "", "text": "$ 5,000"}

        self.assertEqual(score_cells(entry), ("$ 5,000", "", []))

    def test_lines_past_the_first_are_kept(self) -> None:
        entry = {"score": None, "prefix": "", "suffix": "",
                 "text": "INAUGURATED\n5 JAN, 2024 9:15 PM"}

        self.assertEqual(score_cells(entry), ("INAUGURATED", "", ["5 JAN, 2024 9:15 PM"]))


class ScoreLinesTests(unittest.TestCase):
    def _entries(self, reading: dict) -> list[dict]:
        return [entry for section in _scores(reading)["sections"]
                for entry in section["entries"]]

    def test_a_number_is_grouped_as_the_language_groups_digits(self) -> None:
        first = self._entries(CHAMPION_AND_RANKED)[0]

        self.assertEqual(score_lines(first), ["200,000,000"])

    def test_the_machines_words_stay_around_the_number(self) -> None:
        ruler, loops, combos, dollars = self._entries(MULTILINE)

        self.assertEqual([score_lines(one) for one in (loops, combos, dollars)],
                         [["12 LOOPS"], ["4-WAY"], ["$ 5,000"]])
        self.assertEqual(score_lines(ruler), ["INAUGURATED", "5 JAN, 2024 9:15 PM"])

    def test_a_number_the_machine_writes_its_own_way_is_left_as_written(self) -> None:
        entry = {"score": 255, "prefix": "", "suffix": "", "text": "FF"}

        self.assertEqual(score_lines(entry), ["FF"])


if __name__ == "__main__":
    unittest.main()
