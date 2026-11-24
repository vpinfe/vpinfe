"""A state column is built from one `states.Table`, and every Table follows the same rules.

The first half pins what the helper builds. The second half reads every Table the pages
define, so a new one that breaks a rule fails here rather than on a screen.
"""

from __future__ import annotations

import unittest

from manager.backups import backups
from manager.devices import browser_state, devices
from manager.frontend import launchers, themes
from manager.kit import states
from manager.library import (
    collections,
    discovery_states,
    file_states,
    game_states,
    tageditor,
)
from manager.system import jobs_list

SMALL_WORDS = {"a", "an", "and", "as", "at", "by", "for", "in", "of", "on", "or", "the",
               "to", "with"}

_MIXED = states.Table(
    covers="What it covers",
    states=(states.State("ok", "In Place", "it is there", glyph=states.TICK),
            states.State("bad", "Broken", "it stops play", tone=states.BAD),
            states.State("later", "Unknown", "not known yet", glyph=states.UNKNOWN)),
    blank=states.State("", "Not Applicable", "it does not apply"))


def every_table() -> dict[str, states.Table]:
    return {
        "rom": game_states.rom(),
        "release": game_states.release(),
        "default": game_states.default(),
        "hidden game": game_states.hidden(game=True),
        "hidden table": game_states.hidden(game=False),
        "private": game_states.private(),
        "missing": game_states.missing(),
        "vps match": game_states.vps_match(),
        "how matched": game_states.how_matched(),
        "vps entry": game_states.vps_entry(),
        "asset": game_states.asset(),
        "kind": discovery_states.kind(),
        "in library": discovery_states.in_library(),
        "playable": discovery_states.playable(),
        "matched": discovery_states.matched(),
        "other version": discovery_states.other_version(),
        "gap": discovery_states.gap(),
        "held": discovery_states.held(),
        "mod": discovery_states.mod(),
        "marks": discovery_states.marks("Neither"),
        "files": file_states.table("manager.media.unused_meaning"),
        "launchers": launchers.STATES,
        "theme status": themes.STATUS,
        "theme trust": themes.TRUST,
        "in menu": collections.IN_MENU,
        "attention": collections.ATTENTION,
        "jobs": jobs_list.OUTCOME_STATES,
        "tag use": tageditor.USE,
        "tag spelling": tageditor.SPELLING,
        "browser": browser_state.TABLE,
        "reach": devices.REACH,
        "original": backups.ORIGINAL,
    }


class TheHelperBuilds(unittest.TestCase):
    def test_the_header_names_what_it_covers_then_each_state_blank_last(self) -> None:
        lines = _MIXED.header_tip().split("\n")

        self.assertEqual(lines[0], "What it covers")
        self.assertEqual([line.split(" - ")[0] for line in lines[1:]],
                         ["In Place", "Broken", "Unknown", "Not Applicable"])

    def test_the_filter_lists_the_same_order_with_the_cell_s_own_mark(self) -> None:
        choices = _MIXED.choices()

        self.assertEqual([one["value"] for one in choices], ["ok", "bad", "later", ""])
        self.assertEqual(choices[0]["glyph"], "✓")
        self.assertEqual(choices[1]["tone"], states.BAD)
        self.assertEqual(choices[2]["glyph"], "?")
        self.assertNotIn("glyph", choices[3])

    def test_a_tick_is_always_green_and_an_unknown_always_quiet(self) -> None:
        looks = _MIXED.looks()

        self.assertEqual(looks["ok"]["tone"], states.ON)
        self.assertEqual(looks["later"]["tone"], states.QUIET)

    def test_the_blank_has_no_look_because_the_cell_is_empty(self) -> None:
        self.assertNotIn("", _MIXED.looks())

    def test_the_sort_puts_blank_after_every_state(self) -> None:
        rank = _MIXED.rank()

        self.assertEqual(sorted(rank, key=rank.__getitem__), ["ok", "bad", "later", ""])

    def test_a_column_carries_a_header_tooltip_a_filter_and_a_sort(self) -> None:
        column = _MIXED.column("fact", "Fact", 120)

        self.assertEqual(column["field"], "fact")
        self.assertEqual(column["headerTooltip"].split("\n")[0], "What it covers")
        self.assertIn(":comparator", column)
        self.assertEqual([one["value"] for one in column["filterParams"]["choices"]],
                         ["ok", "bad", "later", ""])

    def test_a_page_s_own_help_replaces_the_covers_line(self) -> None:
        column = _MIXED.column("fact", "Fact", help="The page says this")

        self.assertEqual(column["headerTooltip"].split("\n")[0], "The page says this")

    def test_a_table_refuses_a_repeated_or_blank_token(self) -> None:
        with self.assertRaises(ValueError):
            states.Table(covers="", states=(states.State("a", "A", ""),
                                            states.State("a", "B", "")))
        with self.assertRaises(ValueError):
            states.Table(covers="", states=(states.State("", "A", ""),))
        with self.assertRaises(ValueError):
            states.Table(covers="", states=(), blank=states.State("x", "A", ""))

    def test_a_line_without_a_meaning_is_the_word(self) -> None:
        self.assertEqual(states.line("Word", ""), "Word")
        self.assertEqual(states.line("Word", "meaning"), "Word - meaning")


class EveryTableFollowsTheRules(unittest.TestCase):
    def _words(self) -> list[tuple[str, states.State]]:
        return [(name, one) for name, table in every_table().items()
                for one in table.everything]

    def test_every_word_is_title_case(self) -> None:
        for name, one in self._words():
            with self.subTest(table=name, word=one.word):
                parts = one.word.replace("-", " ").split()
                for place, part in enumerate(parts):
                    if place and part in SMALL_WORDS:
                        continue
                    self.assertTrue(part[:1].isupper() or not part[:1].isalpha(),
                                    f"{one.word!r} is not Title Case")

    def test_every_state_says_what_it_means(self) -> None:
        for name, one in self._words():
            with self.subTest(table=name, word=one.word):
                self.assertTrue(one.meaning.strip())

    def test_a_fragment_takes_no_full_stop(self) -> None:
        """A word never ends in one, and a meaning does only when it is more than one
        sentence."""
        for name, one in self._words():
            with self.subTest(table=name, word=one.word):
                self.assertFalse(one.word.endswith("."))
                if one.meaning.endswith("."):
                    self.assertIn(". ", one.meaning)

    def test_a_table_holds_one_word_per_state(self) -> None:
        for name, table in every_table().items():
            with self.subTest(table=name):
                words = [one.word for one in table.everything]
                self.assertEqual(len(words), len(set(words)))

    def test_an_unknown_is_last_before_the_blank(self) -> None:
        for name, table in every_table().items():
            with self.subTest(table=name):
                held = [one.glyph for one in table.states]
                if states.UNKNOWN in held:
                    self.assertEqual(held.index(states.UNKNOWN), len(held) - 1)

    def test_an_in_place_tick_comes_before_every_state_that_costs_something(self) -> None:
        """Only the active state, which is not a cost, may sit ahead of it."""
        for name, table in every_table().items():
            with self.subTest(table=name):
                ahead = []
                for one in table.states:
                    if one.glyph == states.TICK:
                        break
                    ahead.append(one.shown_tone)
                else:
                    continue
                self.assertEqual(set(ahead) - {states.ACCENT}, set())


if __name__ == "__main__":
    unittest.main()
