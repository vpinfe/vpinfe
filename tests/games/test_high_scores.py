"""A machine's high score table as it is kept, and as the wire carries it."""

from __future__ import annotations

import unittest
from dataclasses import asdict
from typing import Any

from common.games import high_scores
from common.games.score_parser import ParsedEntry


def _entry(section: str, rank: int | None, initials: str, score: int | None = None,
           **rest) -> dict:
    return asdict(ParsedEntry(section=section, rank=rank, initials=initials, score=score,
                              **rest))


# The parser's fixture shapes: a champion above ranked sections, a single number, a blank
# set of initials and a multiline entry.
CHAMPION_AND_RANKED = {"rom": "abc_l1", "score_kind": "Leaderboard", "entries": [
    _entry("GRAND CHAMPION", None, "ABC", 200_000_000),
    _entry("HIGH SCORES", 1, "OWN", 150_000_000),
    _entry("HIGH SCORES", 2, "", 135_000_000),
    _entry("HIGH SCORES", 3, "J K", 120_000_000),
]}
ONE_NUMBER = {"rom": "abc_l1", "score_kind": "HIGHEST SCORE", "value": 1_234_560}
MULTILINE = {"rom": "abc_l1", "score_kind": "Leaderboard", "entries": [
    _entry("RULER", 1, "", extra_lines=["INAUGURATED", "5 JAN, 2024 9:15 PM"],
           multiline=True),
    _entry("LOOP CHAMPION", None, "ABC", 12, value_suffix="LOOPS"),
    _entry("COMBO CHAMPION", None, "ABC", 4, value_suffix="-WAY"),
    _entry("GRAND CHAMPION", None, "ABC", 5_000, value_prefix="$ "),
]}


class RecordTests(unittest.TestCase):
    def test_the_reading_is_kept_as_the_machine_wrote_it(self) -> None:
        held = high_scores.record(CHAMPION_AND_RANKED, None, "2026-09-27T12:00:00Z")

        self.assertEqual(held["entries"], CHAMPION_AND_RANKED["entries"])
        self.assertEqual(held["read_at"], "2026-09-27T12:00:00Z")
        self.assertEqual(held["new"], [])
        self.assertNotIn("rom", held, "the key names the ROM")

    def test_a_text_files_name_is_kept_and_shown_a_roms_is_not(self) -> None:
        text = {**ONE_NUMBER, "rom": "Scores.txt", "file": "Scores.txt"}

        held = high_scores.record(text, None, "t")

        self.assertEqual(held["file"], "Scores.txt")
        self.assertEqual(high_scores.shown("Scores.txt", "t1", held)["file"], "Scores.txt")
        self.assertNotIn("file", high_scores.record(ONE_NUMBER, None, "t"))
        self.assertEqual(high_scores.shown("abc_l1", "t1", high_scores.record(
            ONE_NUMBER, None, "t"))["file"], "")

    def test_new_is_where_the_entries_the_game_added_sit(self) -> None:
        before = {"entries": [_entry("HIGH SCORES", 1, "OWN", 300),
                              _entry("HIGH SCORES", 2, "ABC", 200)]}
        after = {"entries": [_entry("HIGH SCORES", 1, "OWN", 300),
                             _entry("HIGH SCORES", 2, "", 250),
                             _entry("HIGH SCORES", 3, "ABC", 200)]}

        self.assertEqual(high_scores.record(after, before, "t")["new"], [1])

    def test_a_first_reading_credits_nobody(self) -> None:
        self.assertEqual(high_scores.record(CHAMPION_AND_RANKED, None, "t")["new"], [])

    def test_a_one_number_reading_is_new_when_the_number_moved(self) -> None:
        self.assertEqual(high_scores.record(ONE_NUMBER, {"value": 1}, "t")["new"], [0])
        self.assertEqual(high_scores.record(ONE_NUMBER, dict(ONE_NUMBER), "t")["new"], [])

    def test_a_read_nobody_timed_is_no_baseline(self) -> None:
        self.assertIsNone(high_scores.as_reading(high_scores.record(ONE_NUMBER, None, None)))
        self.assertEqual(high_scores.as_reading(high_scores.record(ONE_NUMBER, None, "t")),
                         {"value": 1_234_560})

    def test_kept_per_rom(self) -> None:
        config: dict = {"User": {"Rating": 3}}

        high_scores.keep(config, "abc_l1", {"read_at": "t"})
        high_scores.keep(config, "abc_l2", {"read_at": "u"})

        self.assertEqual(high_scores.kept(config, "abc_l1"), {"read_at": "t"})
        self.assertEqual(high_scores.kept(config, "abc_l2"), {"read_at": "u"})
        self.assertEqual(config["User"]["Rating"], 3)
        self.assertIsNone(high_scores.kept(config, "other"))


class WireTests(unittest.TestCase):
    def _shown(self, reading: dict, new: tuple[int, ...] = ()) -> dict:
        held = high_scores.record(reading, None, "2026-09-27T12:00:00Z")
        held["new"] = list(new)
        return high_scores.shown("abc_l1", "t1", held)

    def test_entries_group_under_the_machines_sections_in_its_order(self) -> None:
        shown = self._shown(CHAMPION_AND_RANKED, new=(2,))

        self.assertEqual([section["name"] for section in shown["sections"]],
                         ["GRAND CHAMPION", "HIGH SCORES"])
        champion, ranked = shown["sections"]
        self.assertEqual(champion["entries"][0]["rank"], None)
        self.assertEqual([(one["rank"], one["initials"], one["score"], one["new"])
                          for one in ranked["entries"]],
                         [(1, "OWN", 150_000_000, False), (2, "", 135_000_000, True),
                          (3, "J K", 120_000_000, False)])
        self.assertEqual(ranked["entries"][0]["text"], "150,000,000")
        self.assertEqual((shown["rom"], shown["table_id"], shown["state"]),
                         ("abc_l1", "t1", high_scores.READ))

    def test_a_one_number_machine_is_one_section_named_by_the_map(self) -> None:
        (section,) = self._shown(ONE_NUMBER, new=(0,))["sections"]

        self.assertEqual(section["name"], "HIGHEST SCORE")
        self.assertEqual([(one["score"], one["new"]) for one in section["entries"]],
                         [(1_234_560, True)])

    def test_words_around_a_number_carry_their_own_spacing(self) -> None:
        ruler, loops, combos, dollars = [section["entries"][0] for section in
                                         self._shown(MULTILINE)["sections"]]

        self.assertEqual((ruler["score"], ruler["text"]),
                         (None, "INAUGURATED\n5 JAN, 2024 9:15 PM"))
        self.assertEqual((loops["suffix"], loops["text"]), (" LOOPS", "12 LOOPS"))
        self.assertEqual((combos["suffix"], combos["text"]), ("-WAY", "4-WAY"))
        self.assertEqual((dollars["prefix"], dollars["text"]), ("$ ", "$ 5,000"))

    def test_nothing_read_is_no_view(self) -> None:
        entry = {"id": "t1", "rom": "abc_l1"}

        self.assertIsNone(high_scores.view({"User": {}}, entry, table_id="t1"))
        self.assertIsNone(high_scores.view({}, {"id": "t1"}, table_id="t1"))


class TablesWithNoRomTests(unittest.TestCase):
    def _kept(self, *table_ids: str) -> dict:
        config: dict = {}
        for table_id in table_ids:
            held = high_scores.record({"score_kind": "txt", "file": "Scores.txt",
                                       "entries": [_entry("", 1, "AAA", 7500)]}, None, "t")
            high_scores.keep(config, "Scores.txt", held, table_id=table_id)
        return config

    def test_a_table_is_added_to_the_ones_a_record_was_kept_for(self) -> None:
        config = self._kept("t1", "t2", "t1")

        self.assertEqual(config["User"]["HighScores"]["Scores.txt"]["tables"], ["t1", "t2"])

    def test_the_record_of_a_table_is_found_by_its_id_and_not_by_anothers(self) -> None:
        meta = self._kept("t1")

        self.assertEqual(high_scores.kept_for_table(meta, "t1"), "Scores.txt")
        self.assertEqual(high_scores.kept_for_table(meta, "t2"), "")
        self.assertEqual(high_scores.kept_for_table(meta, ""), "")

    def test_a_rom_keeps_no_table_ids(self) -> None:
        config: dict = {}
        high_scores.keep(config, "abc_l1", high_scores.record(ONE_NUMBER, None, "t"))

        self.assertNotIn("tables", config["User"]["HighScores"]["abc_l1"])

    def test_the_view_of_a_table_with_no_rom_is_its_kept_file(self) -> None:
        shown = high_scores.view(self._kept("t1"), {}, table_id="t1")

        self.assertEqual((shown or {}).get("rom"), "Scores.txt")
        self.assertIsNone(high_scores.view(self._kept("t1"), {}, table_id="t2"))


class TwoPointXTests(unittest.TestCase):
    TWO_X = {"rom": "abc_l1", "resolved_rom": "abc_l1", "score_type": "Leaderboard",
             "entries": CHAMPION_AND_RANKED["entries"]}

    def test_the_2x_reading_moves_into_the_rom_it_names(self) -> None:
        user: dict[str, Any] = {"Rating": 2, "Score": dict(self.TWO_X)}

        high_scores.from_2x(user)

        self.assertNotIn("Score", user)
        held = user["HighScores"]["abc_l1"]
        self.assertEqual(held["entries"], self.TWO_X["entries"])
        self.assertEqual((held["read_at"], held["score_kind"], held["new"]),
                         (None, "Leaderboard", []))

    def test_a_reading_that_holds_nothing_to_show_is_left_alone(self) -> None:
        readings: tuple[Any, ...] = ({"rom": "abc_l1"}, {"entries": []}, "12,345", None)
        for score in readings:
            with self.subTest(score=score):
                user = {"Score": score}
                high_scores.from_2x(user)
                self.assertEqual(user, {"Score": score})

    def test_contract_1_reads_the_default_tables_record_as_2x_wrote_it(self) -> None:
        user = {"Rating": 2, "Score": dict(self.TWO_X)}
        high_scores.from_2x(user)

        legacy = high_scores.to_2x(user, "abc_l1")

        self.assertNotIn("HighScores", legacy)
        self.assertEqual(legacy["Score"], {"rom": "abc_l1", "score_type": "Leaderboard",
                                           "entries": self.TWO_X["entries"]})
        self.assertEqual(legacy["Rating"], 2)
        self.assertNotIn("Score", high_scores.to_2x(user, "other"))


if __name__ == "__main__":
    unittest.main()
