"""One name and one direction per fact, which is the rule three surfaces broke.

`Hidden` read one way in the grid and the opposite in the panel, and `Missing` did the
same - so the words are asserted here rather than left to each surface to spell.
"""

import unittest
from typing import Any

from common.i18n import t
from manager.kit import data, game_tables
from manager.library import games

WORDS = game_tables.DEFAULT_WORDS


class WordTests(unittest.TestCase):
    def test_the_notable_state_is_the_first_of_the_pair(self) -> None:
        """The direction every one of these reads: true is the state worth spotting."""
        self.assertEqual(game_tables.word_for(game_tables.HIDDEN_WORDS, True), "Hidden")
        self.assertEqual(game_tables.word_for(game_tables.FILE_WORDS, True), "Missing")

    def test_a_file_that_is_there_reads_present(self) -> None:
        """The inversion this helper exists to stop: a `pair[not present]` at the call
        site put "Missing" on a file that was on disk."""
        self.assertEqual(game_tables.word_for(game_tables.FILE_WORDS, False), "Present")
        self.assertEqual(game_tables.word_for(game_tables.HIDDEN_WORDS, False),
                         "Offered")

    def test_a_default_says_what_it_does_not_who_set_it(self) -> None:
        chosen, chosen_why = WORDS[game_tables.CHOSEN]
        automatic, automatic_why = WORDS[game_tables.DERIVED]

        self.assertEqual((chosen, automatic), ("Locked", "Automatic"))
        self.assertNotEqual(chosen_why, automatic_why)

    def test_a_locked_default_wears_a_held_collection_row_s_word(self) -> None:
        self.assertEqual(WORDS[game_tables.CHOSEN][0], game_tables.LOCKED_WORDS[0])

    def test_the_reason_is_what_happens_when_a_table_is_added(self) -> None:
        self.assertIn("Stays", WORDS[game_tables.CHOSEN][1])
        self.assertIn("newest", WORDS[game_tables.DERIVED][1])


def _table(table_id: str, **extra: Any) -> dict[str, Any]:
    return {"id": table_id, "version": table_id.title(), "authors": ["someone"], **extra}


AUTOMATIC = _table("alpha", default=True, default_kind=game_tables.DERIVED, automatic=True)
LOCKED = _table("alpha", default=True, default_kind=game_tables.CHOSEN)
OTHER = _table("beta")


class LockActTests(unittest.TestCase):
    """The act on the default row of a game's tables, and its words."""

    def test_an_automatic_default_is_offered_the_lock(self) -> None:
        self.assertEqual(game_tables.lock_act(AUTOMATIC, [AUTOMATIC, OTHER]),
                         (True, t("manager.game_tables.lock")))

    def test_a_locked_default_is_offered_the_unlock(self) -> None:
        self.assertEqual(game_tables.lock_act(LOCKED, [LOCKED, OTHER]),
                         (False, t("manager.game_tables.unlock")))

    def test_the_unlock_names_where_the_default_goes(self) -> None:
        goes = {**OTHER, "automatic": True}

        lock, words = game_tables.lock_act(LOCKED, [LOCKED, goes]) or (None, "")

        self.assertFalse(lock)
        self.assertIn(game_tables.table_name(goes), words)

    def test_a_leftover_lock_can_be_cleared_with_one_table(self) -> None:
        self.assertEqual(game_tables.lock_act(LOCKED, [LOCKED]),
                         (False, t("manager.game_tables.unlock")))

    def test_one_table_has_nothing_to_lock_against(self) -> None:
        self.assertIsNone(game_tables.lock_act(AUTOMATIC, [AUTOMATIC]))

    def test_a_table_that_is_not_the_default_has_no_lock(self) -> None:
        """Its radio is the act: picking it makes it the default."""
        self.assertIsNone(game_tables.lock_act(OTHER, [AUTOMATIC, OTHER]))


class LockSaidTests(unittest.TestCase):
    def test_a_lock_names_the_table(self) -> None:
        said = game_tables.lock_said("Sample Game", OTHER, [], lock=True)

        self.assertIn(game_tables.table_name(OTHER), said)

    def test_an_unlock_that_moves_the_default_names_where_it_went(self) -> None:
        after = [{**LOCKED, "default": False}, {**OTHER, "default": True}]

        said = game_tables.lock_said("Sample Game", LOCKED, after, lock=False)

        self.assertIn("Sample Game", said)
        self.assertIn(game_tables.table_name(OTHER), said)

    def test_an_unlock_that_moves_nothing_says_only_that(self) -> None:
        said = game_tables.lock_said("Sample Game", LOCKED, [LOCKED], lock=False)

        self.assertEqual(said, t("manager.game_tables.unlocked"))


class DefaultCellTests(unittest.TestCase):
    """The Tables grid's Default Table column."""

    def _cells(self, *rows: dict[str, Any]) -> list[str]:
        return [row["default_state"] for row in games.table_rows(list(rows))]

    def test_a_game_with_a_choice_says_which_and_how(self) -> None:
        rows = [{**AUTOMATIC, "game_id": "g1"}, {**OTHER, "game_id": "g1"}]

        self.assertEqual(self._cells(*rows), [game_tables.DERIVED, ""])

    def test_a_lock_is_said_even_on_a_game_with_one_table(self) -> None:
        self.assertEqual(self._cells({**LOCKED, "game_id": "g1"}), [game_tables.CHOSEN])

    def test_a_game_with_one_table_says_nothing_a_filter_would_name(self) -> None:
        (cell,) = self._cells({**AUTOMATIC, "game_id": "g1"})
        named = {choice["value"] for choice in _default_column()["filterParams"]["choices"]}

        self.assertNotIn(cell, named)


def _default_column() -> dict[str, Any]:
    return next(column for column in games.TABLE_COLUMNS
                if column.get("field") == "default_state")


HIDDEN = _table("gamma", hidden=True)
GONE = _table("delta", absent_since="2026-01-01T00:00:00Z")
GAME = "Sample Game"


class DefaultOfferTests(unittest.TestCase):
    """Which tables the radio and the grid menu offer as the default."""

    def test_an_offered_table_can_be(self) -> None:
        self.assertEqual(game_tables.why_not_default(OTHER), "")

    def test_a_hidden_table_cannot_and_says_why(self) -> None:
        self.assertEqual(game_tables.why_not_default(HIDDEN),
                         t("manager.game_tables.hidden_blocked"))

    def test_a_table_not_on_disk_cannot_and_says_why(self) -> None:
        self.assertEqual(game_tables.why_not_default(GONE),
                         t("manager.workbench.default_absent"))


class HiddenHintTests(unittest.TestCase):
    def test_hiding_one_of_several_says_where_it_is_left_out(self) -> None:
        self.assertEqual(game_tables.hidden_hint(OTHER, [AUTOMATIC, OTHER]),
                         t("manager.game_tables.hidden.help"))

    def test_hiding_the_last_offered_table_says_the_game_goes_with_it(self) -> None:
        self.assertEqual(game_tables.hidden_hint(AUTOMATIC, [AUTOMATIC, HIDDEN, GONE]),
                         t("manager.game_tables.hidden_last.help"))

    def test_a_hidden_table_is_not_the_last_offered_one(self) -> None:
        self.assertEqual(game_tables.hidden_hint(HIDDEN, [HIDDEN]),
                         t("manager.game_tables.hidden.help"))


class HiddenSaidTests(unittest.TestCase):
    """The notification once a table is hidden or offered again, from what the write
    answered."""

    def test_hiding_a_spare_table_says_only_that(self) -> None:
        said = game_tables.hidden_said(GAME, OTHER, {"default": AUTOMATIC}, hidden=True)

        self.assertEqual(said, game_tables.HIDDEN_WORDS[0])

    def test_hiding_the_default_names_the_table_that_now_plays(self) -> None:
        said = game_tables.hidden_said(GAME, AUTOMATIC, {"default": OTHER}, hidden=True)

        self.assertEqual(said, t("manager.game_tables.result_hidden", game=GAME,
                                 table=game_tables.table_name(OTHER)))

    def test_hiding_a_locked_default_says_it_was_unlocked(self) -> None:
        said = game_tables.hidden_said(GAME, LOCKED, {"default": OTHER}, hidden=True)

        self.assertEqual(said, t("manager.game_tables.result_both",
                                 game=GAME, table=game_tables.table_name(OTHER)))

    def test_hiding_the_last_table_says_the_frontend_leaves_the_game_out(self) -> None:
        said = game_tables.hidden_said(GAME, AUTOMATIC, {"default": None}, hidden=True)

        self.assertEqual(said, t("manager.game_tables.hidden_none_left", game=GAME))

    def test_offering_the_table_the_game_now_plays_says_so(self) -> None:
        now = {**HIDDEN, "hidden": False, "default": True}

        said = game_tables.hidden_said(GAME, HIDDEN, {"default": now}, hidden=False)

        self.assertEqual(said, t("manager.game_tables.result_offered", game=GAME,
                                 table=game_tables.table_name(now)))

    def test_offering_one_that_leaves_the_default_says_only_that(self) -> None:
        said = game_tables.hidden_said(GAME, HIDDEN, {"default": AUTOMATIC}, hidden=False)

        self.assertEqual(said, game_tables.HIDDEN_WORDS[1])


class HiddenGameColumnTests(unittest.TestCase):
    """The Games grid column that finds the games the frontend leaves out."""

    def setUp(self) -> None:
        self.column = next(one for one in games.COLUMNS if one["field"] == "hidden")

    def test_only_a_hidden_game_is_drawn(self) -> None:
        states = self.column["cellRendererParams"]["states"]

        self.assertEqual(list(states), [game_tables.HIDDEN_CELL])
        self.assertEqual(states[game_tables.HIDDEN_CELL]["label"], game_tables.HIDDEN_WORDS[0])

    def test_the_funnel_offers_the_pair_notable_first(self) -> None:
        choices = self.column["filterParams"]["choices"]

        self.assertEqual([one["label"] for one in choices],
                         list(game_tables.HIDDEN_WORDS))
        self.assertEqual([one["value"] for one in choices], [game_tables.HIDDEN_CELL, ""])

    def test_the_frontend_view_shows_it(self) -> None:
        preset = games.GAME_VIEWS["manager.view.frontend"]

        self.assertIn("hidden", getattr(preset, "columns", ()))

    def test_a_game_row_carries_the_flag(self) -> None:
        library = data.Library.__new__(data.Library)
        library.games = [{"id": "g1", "name": "A", "hidden": True},
                         {"id": "g2", "name": "B"}]
        library.media = {}

        self.assertEqual([row["hidden"] for row in library.game_rows()],
                         [game_tables.HIDDEN_CELL, ""])


class ReferenceNameTests(unittest.TestCase):
    """A table played from somewhere else is named by its file, from either wire."""

    PATH = "../Other Game (Maker 1990)/Other Game.vpx"

    def test_the_game_s_own_read_names_the_file(self) -> None:
        table = {"form": "referenced", "filename": "",
                 "reference": {"path": self.PATH, "resolved": "/tables/x.vpx"}}

        self.assertEqual(game_tables.table_name(table), "Other Game.vpx")

    def test_the_grid_row_carries_the_path_as_a_string_and_still_draws(self) -> None:
        (row,) = games.table_rows([{"id": "t1", "game_id": "g1", "game": "A",
                                    "form": "referenced", "filename": "",
                                    "reference": self.PATH}])

        self.assertEqual(row["said_built"], "Other Game.vpx")

    def test_a_windows_path_is_cut_at_its_own_separator(self) -> None:
        table = {"reference": {"path": "D:\\Tables\\Other Game.vpx"}}

        self.assertEqual(game_tables.table_name(table), "Other Game.vpx")


def _built(table_id: str, filename: str, version: str = "1.0") -> dict[str, Any]:
    return {"id": table_id, "version": version, "authors": ["someone"],
            "filename": filename}


class ToldApartTests(unittest.TestCase):
    """What a name gains where two of a game's tables would read the same."""

    def test_a_name_no_other_row_shares_gains_nothing(self) -> None:
        tables = [_built("a", "Game (Maker 1990).vpx", "1.0"),
                  _built("b", "Game (Maker 1990) - mod.vpx", "1.1")]

        self.assertEqual(game_tables.told_apart(tables), {})

    def test_two_that_differ_at_the_tail_gain_the_tail(self) -> None:
        tables = [_built("a", "Game (Maker 1990)12.vpx"),
                  _built("b", "Game (Maker 1990)lw.vpx")]

        self.assertEqual(game_tables.told_apart(tables), {"a": "12", "b": "lw"})

    def test_the_run_between_the_first_and_last_word_that_differ_is_kept_whole(
            self) -> None:
        tables = [_built("a", "Game (Maker 1990) - someone (1) - VPF_101.vpx"),
                  _built("b", "Game (Maker 1990) - another (1.6.1) - VPF_202.vpx")]

        self.assertEqual(game_tables.told_apart(tables),
                         {"a": "someone (1) - VPF_101",
                          "b": "another (1.6.1) - VPF_202"})

    def test_the_one_with_nothing_of_its_own_is_the_one_without_an_addition(
            self) -> None:
        tables = [_built("a", "Game (Maker 1990) - alt build.vpx"),
                  _built("b", "Game (Maker 1990).vpx")]

        self.assertEqual(game_tables.told_apart(tables), {"a": "alt build"})

    def test_only_the_rows_that_collide_are_told_apart(self) -> None:
        tables = [_built("a", "Game - one.vpx"), _built("b", "Game - two.vpx"),
                  _built("c", "Game - three.vpx", "2.0")]

        self.assertEqual(game_tables.told_apart(tables), {"a": "one", "b": "two"})

    def test_a_name_said_on_its_own_carries_what_tells_it_apart(self) -> None:
        """The Keep on an add's message and Forget's confirmation name one row."""
        tables = [_built("a", "Game - one.vpx"), _built("b", "Game - two.vpx")]

        self.assertEqual(game_tables.name_among(tables[0], tables),
                         game_tables.JOIN.join((game_tables.table_name(tables[0]), "one")))
        self.assertEqual(game_tables.name_among(tables[0], tables[:1]),
                         game_tables.table_name(tables[0]))


class UsualLauncherTests(unittest.TestCase):
    def test_it_is_the_default_table_s(self) -> None:
        tables: list[dict[str, Any]] = [{"id": "a", "launcher": "one"},
                                        {"id": "b", "launcher": "two", "default": True}]

        self.assertEqual(game_tables.usual_launcher(tables), "two")

    def test_a_game_with_no_default_has_none(self) -> None:
        self.assertEqual(game_tables.usual_launcher([{"id": "a", "launcher": "one"}]), "")


class HowMatchedTests(unittest.TestCase):
    def test_a_match_nobody_made_is_auto_matched(self) -> None:
        self.assertEqual(game_tables.how_matched({"vps_id": "abc", "vps_matched_by": ""}),
                         "auto")

    def test_an_import_or_a_person_says_so(self) -> None:
        for how in ("import", "user"):
            with self.subTest(how=how):
                self.assertEqual(game_tables.how_matched(
                    {"vps_id": "abc", "vps_matched_by": how}), how)

    def test_no_match_has_no_state_even_one_a_person_cleared(self) -> None:
        self.assertEqual(game_tables.how_matched({"vps_id": "", "vps_matched_by": "user"}),
                         "")

    def test_each_state_has_its_word(self) -> None:
        self.assertEqual(list(game_tables.HOW_MATCHED.values()),
                         ["Auto-Matched", "Matched on Import", "Matched by You"])


if __name__ == "__main__":
    unittest.main()
