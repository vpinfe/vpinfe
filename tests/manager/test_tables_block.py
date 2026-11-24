"""What a game's Tables block draws, read from the elements it builds."""

import unittest
from pathlib import Path
from typing import Any

from nicegui import ui

from common.i18n import t
from manager.kit import game_tables
from manager.workbench import game_table


def _matched(**extra: Any) -> dict[str, Any]:
    return {"id": "m", "filename": "Sample Game - 1.0.vpx", "available": True,
            "version": "1.0", "authors": ["Someone"], "default": True,
            "default_kind": game_tables.CHOSEN, "locked": True,
            "source": {"vps_file_id": "file-1", "vps_version": "1.3",
                       "authors": ["Burger", "DaRdog"], "version": "1.3",
                       "confirmed_by": "user"}, **extra}


def _plain(table_id: str, **extra: Any) -> dict[str, Any]:
    return {"id": table_id, "filename": f"Sample Game {table_id}.vpx", "available": True,
            **extra}


def _context(tables: list[dict[str, Any]]) -> dict[str, Any]:
    return {"library": object(), "game": {"name": "Sample Game"}, "game_id": "g1",
            "tables": tables, "state": {}, "lens": "", "redraws": []}


def _draw(tables: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
    """Every label's text, and every address a link on it follows."""
    with ui.card() as card:
        game_table._tables_block(_context(tables))
    labels = [child.text for child in card.descendants() if isinstance(child, ui.label)]
    links = [str(child.props.get("href") or "") for child in card.descendants()
             if isinstance(child, ui.link)]
    return labels, links


class ATableMatchedToADifferentRelease(unittest.TestCase):
    def test_the_row_names_the_release_and_not_the_file(self) -> None:
        labels, _ = _draw([_matched(), _plain("b")])

        self.assertIn("1.3 · Burger, DaRdog", labels)
        self.assertNotIn("1.0 · Someone", labels)

    def test_the_row_has_no_second_line_naming_a_version(self) -> None:
        labels, _ = _draw([_matched(), _plain("b")])

        self.assertEqual([one for one in labels if "Burger" in one],
                         ["1.3 · Burger, DaRdog"])


class TheTablesBlockOfSeveralTables(unittest.TestCase):
    def setUp(self) -> None:
        self.labels, self.links = _draw([_matched(), _plain("b", automatic=True)])

    def test_it_says_what_the_selection_is(self) -> None:
        self.assertIn(t("manager.workbench.tables_default_help"), self.labels)

    def test_automatic_comes_first_and_says_what_it_plays(self) -> None:
        self.assertIn(t("manager.game_tables.automatic"), self.labels)
        self.assertIn(t("manager.workbench.default_recent"), self.labels)
        self.assertLess(self.labels.index(t("manager.game_tables.automatic")),
                        self.labels.index("1.3 · Burger, DaRdog"))

    def test_the_table_that_plays_is_marked_default(self) -> None:
        self.assertEqual(self.labels.count(t("manager.workbench.default_word")), 1)

    def test_each_row_has_an_arrow_to_its_own_details(self) -> None:
        self.assertEqual(len(self.links), 2)
        self.assertTrue(all("table=" in link for link in self.links))


class TheArrowToATablesDetails(unittest.TestCase):
    def test_it_is_not_underlined_at_rest_on_hover_or_on_focus(self) -> None:
        css = (Path(__file__).resolve().parents[2] / "manager" / "static"
               / "manager-base.css").read_text(encoding="utf-8")

        for state in ("", ":hover", ":focus-visible"):
            rule = rf"a\.manager-member-go{state}[^{{]*\{{[^}}]*text-decoration: none"
            self.assertRegex(css, rule)

    def test_the_link_carries_the_class_those_rules_name(self) -> None:
        with ui.card() as card:
            game_table._tables_block(_context([_plain("a"), _plain("b")]))
        arrows = [child for child in card.descendants()
                  if isinstance(child, ui.link) and "table=" in str(child.props.get("href"))]

        self.assertEqual(len(arrows), 2)
        self.assertTrue(all("manager-member-go" in child.classes for child in arrows))


class TheTablesBlockOfOneTable(unittest.TestCase):
    def test_it_draws_no_default_control(self) -> None:
        labels, _ = _draw([_plain("a", default=True)])

        for word in (t("manager.game_tables.automatic"),
                     t("manager.workbench.tables_default_help"),
                     t("manager.workbench.default_word")):
            self.assertNotIn(word, labels)


class ALockedTableThatCannotBePlayed(unittest.TestCase):
    def test_the_block_says_automatic_plays_another(self) -> None:
        gone = _matched(default=False, absent_since="2026-10-01T00:00:00Z", available=False)
        labels, _ = _draw([gone, _plain("b", default=True, default_kind=game_tables.DERIVED,
                                        automatic=True)])

        self.assertIn(t("manager.workbench.default_fell", locked="1.3 · Burger, DaRdog",
                        plays="Sample Game b.vpx"), labels)
        self.assertIn(game_tables.word_for(game_tables.FILE_WORDS, True), labels)

    def test_a_locked_table_that_is_only_hidden_says_the_same(self) -> None:
        hidden = _matched(default=False, hidden=True)
        labels, _ = _draw([hidden, _plain("b", default=True, default_kind=game_tables.DERIVED,
                                          automatic=True)])

        self.assertIn(t("manager.workbench.default_fell", locked="1.3 · Burger, DaRdog",
                        plays="Sample Game b.vpx"), labels)
        self.assertIn(game_tables.HIDDEN_WORDS[0], labels)

    def test_a_table_both_gone_and_hidden_shows_both_chips(self) -> None:
        both = _plain("a", hidden=True, absent_since="2026-10-01T00:00:00Z")
        labels, _ = _draw([both, _plain("b", default=True)])

        self.assertIn(game_tables.HIDDEN_WORDS[0], labels)
        self.assertIn(game_tables.word_for(game_tables.FILE_WORDS, True), labels)

    def test_a_hidden_table_says_so_and_no_match_chip_is_drawn(self) -> None:
        labels, _ = _draw([_plain("a", default=True), _plain("b", hidden=True)])

        self.assertIn(game_tables.HIDDEN_WORDS[0], labels)
        self.assertFalse([one for one in labels if one.startswith("Matched")])


if __name__ == "__main__":
    unittest.main()
