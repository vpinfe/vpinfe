"""An author's, maker's or theme's page in Discovery: its bar, its wall and its tiles."""

from __future__ import annotations

import asyncio
import pathlib
import re
import unittest
from typing import Any, cast
from unittest import mock

from nicegui import background_tasks, events, ui

from common.games import launchers
from manager.library import discovery_ask

REPO = pathlib.Path(__file__).resolve().parent.parent.parent

# Made at import, outside any task, where NiceGUI still hands out the script's own page.
_PAGE = ui.element()

PEOPLE = [{"name": "Wildman", "aliases": ["Wild Man"], "releases": 4},
          {"name": "VPW", "aliases": [], "releases": 2}]
FIELDS = {"tableFiles": "kind_table", "b2sFiles": "kind_backglass"}
KINDS = {"tableFiles": "word.table", "b2sFiles": "asset.kind.backglass.label"}


def _release(release_id: str, **over: Any) -> dict[str, Any]:
    return {"id": release_id, "by": [0], "mod": False, "features": [], "format": "VPX",
            "added": "2025-10-13T00:00:00Z", "held": False, **over}


def _game(game_id: str, name: str, **over: Any) -> dict[str, Any]:
    return {"id": game_id, "name": name, "manufacturer": "Bally", "year": 1990,
            "said": "Bally 1990", "themes": [], "listed": ["tableFiles", "b2sFiles"],
            "is_in_library": False, "in_library": "no", "kind_table": "missing",
            "kind_backglass": "missing", **over}


ROWS = [
    _game("held", "Alpha", is_in_library=True, in_library="yes", kind_table="matched"),
    _game("other", "Beta", is_in_library=True, in_library="yes", kind_table="matched",
          manufacturer="Williams", said="Williams 1990"),
    _game("unknown", "Gamma", is_in_library=True, in_library="yes", kind_table="unmatched"),
    _game("lacking", "Delta", is_in_library=True, in_library="yes"),
    _game("away", "Epsilon", year=1985, said="Bally 1985"),
]
RELEASES = {
    "held": {"tableFiles": [_release("t1", held=True), _release("t2")],
             "b2sFiles": [_release("b1")]},
    "other": {"tableFiles": [_release("t3"), _release("t4", by=[1], held=True)]},
    "unknown": {"tableFiles": [_release("t5")]},
    "lacking": {"tableFiles": [_release("t6")]},
    "away": {"tableFiles": [_release("t7"), _release("t8", mod=True)]},
}


def _launcher(**over: Any) -> launchers.Launcher:
    return launchers.Launcher(launcher_id="l1", app="vpx", **over)


class Standing(unittest.TestCase):
    def _of(self, game_id: str, listings: tuple[str, ...] = ("tableFiles",)
            ) -> tuple[str, list[str]]:
        row = next(one for one in ROWS if one["id"] == game_id)
        mine = RELEASES[game_id]
        fitting = [(listing, one) for listing in listings for one in mine.get(listing, ())
                   if 0 in one["by"]]
        return discovery_ask.standing(row, fitting, mine, FIELDS, PEOPLE, "Wildman",
                                      counted=True)

    def test_a_game_with_a_release_of_theirs_held_is_have(self) -> None:
        self.assertEqual((discovery_ask.HAVE, []), self._of("held"))

    def test_a_kind_held_under_another_authors_release_names_whose(self) -> None:
        self.assertEqual((discovery_ask.OTHER, ["VPW"]), self._of("other"))

    def test_a_kind_held_and_bound_to_no_release_is_author_unknown(self) -> None:
        self.assertEqual((discovery_ask.UNKNOWN, []), self._of("unknown"))

    def test_a_game_in_the_library_with_nothing_of_the_kind_is_missing(self) -> None:
        self.assertEqual((discovery_ask.MISSING, []), self._of("lacking"))

    def test_a_kind_is_judged_by_the_releases_listed_not_by_another_kind(self) -> None:
        self.assertEqual(discovery_ask.MISSING, self._of("held", ("b2sFiles",))[0])

    def test_a_game_the_library_does_not_have_says_nothing(self) -> None:
        self.assertEqual(("", []), self._of("away"))

    def test_nothing_is_said_before_the_library_is_compared(self) -> None:
        row, mine = ROWS[0], RELEASES["held"]
        fitting = [("tableFiles", mine["tableFiles"][0])]

        self.assertEqual(("", []), discovery_ask.standing(
            row, fitting, mine, FIELDS, PEOPLE, "Wildman", counted=False))


class StandingFollowsTheFilteredSet(unittest.TestCase):
    ROW = _game("g", "Zeta", is_in_library=True, in_library="yes", kind_table="matched")

    def _of(self, fitting: list[dict[str, Any]], mine: list[dict[str, Any]]
            ) -> tuple[str, list[str]]:
        return discovery_ask.standing(
            self.ROW, [("tableFiles", one) for one in fitting], {"tableFiles": mine}, FIELDS,
            PEOPLE, "Wildman", counted=True)

    def test_an_own_release_the_page_leaves_out_reads_missing_not_have(self) -> None:
        hidden = _release("m1", mod=True, held=True)
        shown = _release("t1")

        self.assertEqual((discovery_ask.MISSING, []), self._of([shown], [hidden, shown]))

    def test_that_game_is_the_one_without_theirs_lists(self) -> None:
        shown = [("tableFiles", _release("t1"))]

        self.assertTrue(discovery_ask.in_show(
            discovery_ask.WITHOUT, self.ROW, shown, FIELDS, of_author=True))

    def test_a_held_release_with_no_credit_is_author_unknown(self) -> None:
        self.assertEqual((discovery_ask.UNKNOWN, []),
                         self._of([_release("t1", by=[], held=True)], []))

    def test_a_held_release_with_no_credit_the_page_leaves_out_is_author_unknown(self) -> None:
        mine = [_release("t1"), _release("t2", by=[], held=True)]

        self.assertEqual((discovery_ask.UNKNOWN, []), self._of(mine[:1], mine))

    def test_another_authors_held_release_is_named_before_an_uncredited_one(self) -> None:
        mine = [_release("t1"), _release("t2", by=[], held=True),
                _release("t3", by=[1], held=True)]

        self.assertEqual((discovery_ask.OTHER, ["VPW"]), self._of(mine[:1], mine))


class Listing(unittest.TestCase):
    def test_without_theirs_is_a_game_of_the_library_none_of_whose_releases_is_held(self) -> None:
        theirs = [("tableFiles", RELEASES["held"]["tableFiles"][0])]
        none = [("tableFiles", RELEASES["lacking"]["tableFiles"][0])]

        def without(row: dict[str, Any], fitting: Any) -> bool:
            return discovery_ask.in_show(discovery_ask.WITHOUT, row, fitting, FIELDS,
                                         of_author=True)

        self.assertFalse(without(ROWS[0], theirs))
        self.assertTrue(without(ROWS[3], none))
        self.assertFalse(without(ROWS[4], none))

    def test_without_on_a_makers_page_is_a_game_of_the_library_missing_a_file_of_a_kind(
            self) -> None:
        tables = [("tableFiles", RELEASES["held"]["tableFiles"][0])]
        glass = [("b2sFiles", RELEASES["held"]["b2sFiles"][0])]

        def without(row: dict[str, Any], fitting: Any) -> bool:
            return discovery_ask.in_show(discovery_ask.WITHOUT, row, fitting, FIELDS,
                                         of_author=False)

        self.assertFalse(without(ROWS[0], tables))
        self.assertTrue(without(ROWS[0], glass))
        self.assertTrue(without(ROWS[3], tables))
        self.assertFalse(without(ROWS[2], tables))
        self.assertFalse(without(ROWS[4], tables))

    def test_yours_and_not_in_your_library_split_the_games_by_the_library(self) -> None:
        def shown(show: str, row: dict[str, Any]) -> bool:
            return discovery_ask.in_show(show, row, [], FIELDS, of_author=True)

        self.assertTrue(shown(discovery_ask.YOURS, ROWS[0]))
        self.assertFalse(shown(discovery_ask.YOURS, ROWS[4]))
        self.assertTrue(shown(discovery_ask.ELSEWHERE, ROWS[4]))
        self.assertFalse(shown(discovery_ask.ELSEWHERE, ROWS[0]))
        self.assertTrue(shown(discovery_ask.EVERYTHING, ROWS[4]))

    def test_every_word_typed_must_be_in_the_name_the_maker_or_the_year(self) -> None:
        self.assertTrue(discovery_ask.narrows(ROWS[0], "alp"))
        self.assertTrue(discovery_ask.narrows(ROWS[0], "bally 1990"))
        self.assertFalse(discovery_ask.narrows(ROWS[0], "bally 1985"))
        self.assertTrue(discovery_ask.narrows(ROWS[0], ""))

    def test_a_tile_counts_releases_in_the_kinds_own_noun(self) -> None:
        tables = [("tableFiles", RELEASES["held"]["tableFiles"][0])] * 2
        mixed = [("tableFiles", RELEASES["held"]["tableFiles"][0]),
                 ("b2sFiles", RELEASES["held"]["b2sFiles"][0])]

        self.assertEqual("Bally 1990 · 2 tables", discovery_ask.tile_line(ROWS[0], tables))
        self.assertEqual("Bally 1990 · 1 backglass",
                         discovery_ask.tile_line(ROWS[0], [mixed[1]]))
        self.assertEqual("Bally 1990 · 2 VPS releases", discovery_ask.tile_line(ROWS[0], mixed))

    def test_a_game_with_no_release_listed_has_no_count_at_all(self) -> None:
        self.assertEqual("Bally 1990", discovery_ask.tile_line(ROWS[0], []))
        self.assertEqual("", discovery_ask.tile_line({**ROWS[0], "said": ""}, []))

    def test_a_tile_with_no_maker_or_year_is_its_count_alone(self) -> None:
        bare = {**ROWS[0], "said": ""}

        self.assertEqual("1 table", discovery_ask.tile_line(
            bare, [("tableFiles", RELEASES["held"]["tableFiles"][0])]))


async def _settle() -> None:
    for _ in range(8):
        await asyncio.sleep(0)


def _words(body: ui.element) -> list[str]:
    return [str(one.text) for one in body.descendants()
            if isinstance(one, ui.label) and not one.is_deleted]


async def _press(button: ui.button) -> None:
    started: list[Any] = []
    with mock.patch.object(background_tasks, "create_or_defer",
                           lambda awaitable, **_: started.append(awaitable)):
        for listener in button._event_listeners.values():
            if listener.type == "click" and listener.handler is not None:
                listener.handler(events.ClickEventArguments(sender=button,
                                                            client=button.client))
    await asyncio.gather(*started)


def _playable_row(body: ui.element) -> ui.element:
    label = next(one for one in body.descendants()
                 if isinstance(one, ui.label) and one.text == "Playable Only")
    return cast(ui.element, cast(Any, label.parent_slot).parent)


def _selects(body: ui.element) -> dict[str, ui.select]:
    return {str(one.props.get("label")): one for one in body.descendants()
            if isinstance(one, ui.select) and not one.is_deleted}


def _options(select: ui.select) -> list[str]:
    return [str(label) for label in cast(dict[Any, Any], select.options).values()]


class Drawn(unittest.IsolatedAsyncioTestCase):
    SUBJECT = {"type": "author", "name": "Wildman"}

    def setUp(self) -> None:
        self.formats = (_launcher(),)
        self.state: dict[str, Any] = {}
        self.opened: list[tuple[dict[str, Any], str]] = []
        self.picked: list[dict[str, Any]] = []

    def _draw(self, subject: dict[str, str] | None = None, *,
              rows: list[dict[str, Any]] | None = None,
              releases: dict[str, Any] | None = None) -> tuple[ui.element, Any]:
        store = mock.Mock(launchers=lambda: list(self.formats))

        async def open_game(row: Any) -> None:
            self.picked.append(row)

        async def leave() -> None:
            return None

        async def see_all(question: dict[str, Any], search: str) -> None:
            self.opened.append((question, search))

        self.state = {}
        with mock.patch.object(launchers, "get_launcher_store", return_value=store), _PAGE:
            body = ui.column()
            with body:
                narrow = discovery_ask.page(
                    subject=subject or self.SUBJECT, state=self.state, people=PEOPLE,
                    rows=rows or ROWS,
                    releases=releases or RELEASES, kinds=KINDS, fields=FIELDS, address="/x",
                    open_game=open_game, leave=leave, see_all=see_all)
        return body, narrow

    def test_kind_and_show_are_labeled_dropdowns_with_each_count_in_the_list(self) -> None:
        body, _ = self._draw()
        selects = _selects(body)

        self.assertEqual(["All 5", "Tables 5", "Backglasses 1"], _options(selects["Kind"]))
        self.assertEqual(["Everything 5", "Your Games 4", "Your Games Without Theirs 3",
                          "Not in Your Library 1"], _options(selects["Show"]))

    def test_without_is_worded_for_a_file_on_a_makers_page(self) -> None:
        body, _ = self._draw({"type": "maker", "name": "Bally"})

        self.assertEqual(["Everything 4", "Your Games 3", "Your Games Missing Files 2",
                          "Not in Your Library 1"], _options(_selects(body)["Show"]))

    def test_the_name_is_over_its_word_and_the_count_is_games(self) -> None:
        body, _ = self._draw()

        words = _words(body)
        self.assertLess(words.index("Wildman"), words.index("Author"))
        self.assertIn("5 games", words)

    def test_kind_and_show_are_the_only_pickers_and_nothing_is_a_toggle(self) -> None:
        body, _ = self._draw()

        self.assertEqual({"Kind", "Show"}, set(_selects(body)))
        self.assertEqual([], [one for one in body.descendants()
                              if isinstance(one, (ui.toggle, ui.radio))])

    def test_hide_mods_is_an_authors_alone_and_playable_only_waits_for_tables(self) -> None:
        body, _ = self._draw()
        self.assertIn("Hide Mods", _words(body))
        row = _playable_row(body)
        self.assertFalse(row.visible)

        maker, _ = self._draw({"type": "maker", "name": "Bally"})
        self.assertNotIn("Hide Mods", _words(maker))

    async def test_playable_only_shows_on_tables_and_names_the_formats(self) -> None:
        body, _ = self._draw()
        _selects(body)["Kind"].set_value("tableFiles")
        await _settle()

        row = _playable_row(body)
        self.assertTrue(row.visible)
        tip = next(one for one in body.descendants() if isinstance(one, ui.tooltip)
                   and one.props.get("target") == f"#{row.html_id}")
        self.assertEqual("Tables in a format your launchers play: VPX", tip.text)

    async def test_a_show_choice_narrows_the_wall_and_the_count_in_place(self) -> None:
        body, _ = self._draw()
        before = _selects(body)["Show"]

        before.set_value(discovery_ask.WITHOUT)
        await _settle()

        self.assertIs(before, _selects(body)["Show"])
        self.assertEqual(["Beta", "Gamma", "Delta"], [
            text for text in _words(body) if text in {"Alpha", "Beta", "Gamma", "Delta", "Epsilon"}
        ])
        self.assertIn("3 games", _words(body))

    async def test_open_in_grid_hands_over_the_question_the_page_asks(self) -> None:
        body, _ = self._draw()
        _selects(body)["Show"].set_value(discovery_ask.WITHOUT)
        await _settle()
        button = next(one for one in body.descendants()
                      if isinstance(one, ui.button) and one.props.get("icon") == "open_in_new")

        await _press(button)

        self.assertEqual(({"by": ["Wildman"], "owned": "yes", "held": "missing"}, ""),
                         self.opened[0])

    async def test_open_in_grid_hands_over_the_words_typed(self) -> None:
        body, narrow = self._draw()
        narrow("epsi")
        await _settle()

        await self._asked(body)

        self.assertEqual("epsi", self.opened[-1][1])

    async def test_a_search_cleared_is_not_handed_over(self) -> None:
        body, narrow = self._draw()
        narrow("epsi")
        narrow("")
        await _settle()

        await self._asked(body)

        self.assertEqual("", self.opened[-1][1])

    async def test_typing_narrows_the_wall_to_the_games_it_names(self) -> None:
        body, narrow = self._draw()

        narrow("epsi")
        await _settle()

        words = _words(body)
        self.assertIn("Epsilon", words)
        self.assertNotIn("Alpha", words)
        self.assertIn("Bally 1985 · 2 tables", words)

    async def test_the_third_line_is_for_a_game_in_the_library_only(self) -> None:
        body, _ = self._draw()
        _selects(body)["Kind"].set_value("tableFiles")
        await _settle()

        words = _words(body)
        self.assertEqual(1, words.count("Have"))
        self.assertIn("Have another author's", words)
        self.assertIn("Have, author unknown", words)
        self.assertEqual(1, words.count("Missing"))

    async def test_the_unmatched_tooltip_names_the_kind_it_is_about(self) -> None:
        rows = [_game("glass", "Zeta", is_in_library=True, in_library="yes",
                      kind_table="missing", kind_backglass="unmatched")]
        releases = {"glass": {"b2sFiles": [_release("b9")]}}
        body, _ = self._draw(rows=rows, releases=releases)
        _selects(body)["Kind"].set_value("b2sFiles")
        await _settle()

        tips = [one.text for one in body.descendants() if isinstance(one, ui.tooltip)]
        self.assertIn("Your backglass of Zeta isn't matched to a VPS release, so VPinFE "
                      "can't tell whose it is. Match it in the game's pane.", tips)

    async def test_the_count_follows_what_the_wall_shows_after_typing(self) -> None:
        body, narrow = self._draw()
        self.assertIn("5 games", _words(body))

        narrow("epsi")
        await _settle()

        self.assertIn("1 game", _words(body))
        self.assertNotIn("5 games", _words(body))

        narrow("")
        await _settle()

        self.assertIn("5 games", _words(body))

    async def test_hide_mods_leaves_the_mods_out_of_the_counts(self) -> None:
        body, _ = self._draw()
        switch = next(one for one in body.descendants() if isinstance(one, ui.switch)
                      and not one.value)
        _selects(body)["Kind"].set_value("tableFiles")
        await _settle()
        self.assertIn("Bally 1985 · 2 tables", _words(body))

        switch.set_value(True)
        await _settle()

        self.assertIn("Bally 1985 · 1 table", _words(body))

    async def _asked(self, body: ui.element) -> dict[str, Any]:
        button = next(one for one in body.descendants()
                      if isinstance(one, ui.button) and one.props.get("icon") == "open_in_new")
        await _press(button)
        return self.opened[-1][0]

    @staticmethod
    def _playable_switch(body: ui.element) -> ui.switch:
        return next(one for one in _playable_row(body).descendants()
                    if isinstance(one, ui.switch))

    PLAYABLE_ROWS = [
        _game("fp", "Eta", listed=["tableFiles"]),
        _game("vp", "Theta", listed=["tableFiles"]),
        _game("none", "Iota", listed=["tableFiles"]),
    ]
    PLAYABLE_RELEASES = {
        "fp": {"tableFiles": [_release("f1", format="FP")]},
        "vp": {"tableFiles": [_release("v1")]},
        "none": {"tableFiles": [_release("n1", format="")]},
    }

    def _playable_page(self, rows: list[dict[str, Any]] | None = None) -> ui.element:
        body, _ = self._draw(rows=rows or self.PLAYABLE_ROWS,
                             releases=self.PLAYABLE_RELEASES)
        return body

    async def test_playable_only_changes_the_question_and_the_wall(self) -> None:
        body = self._playable_page()
        _selects(body)["Kind"].set_value("tableFiles")
        await _settle()

        words = _words(body)
        self.assertEqual(["Iota", "Theta"],
                         sorted(w for w in words if w in {"Eta", "Theta", "Iota"}))
        self.assertEqual(["VPX"], (await self._asked(body))["format"])

        self._playable_switch(body).set_value(False)
        await _settle()

        words = _words(body)
        self.assertEqual(["Eta", "Iota", "Theta"],
                         sorted(w for w in words if w in {"Eta", "Theta", "Iota"}))
        self.assertNotIn("format", await self._asked(body))

    async def test_a_release_filed_under_no_format_fits_any_launcher(self) -> None:
        body = self._playable_page()
        _selects(body)["Kind"].set_value("tableFiles")
        await _settle()

        self.assertIn("Iota", _words(body))

    async def test_tables_stay_listed_when_none_of_them_is_playable(self) -> None:
        body = self._playable_page([self.PLAYABLE_ROWS[0]])

        self.assertEqual(["All 1", "Tables 0"], _options(_selects(body)["Kind"]))
        _selects(body)["Kind"].set_value("tableFiles")
        await _settle()
        self.assertTrue(_playable_row(body).visible)

        self._playable_switch(body).set_value(False)
        await _settle()

        self.assertEqual(["All 1", "Tables 1"], _options(_selects(body)["Kind"]))
        self.assertIn("Eta", _words(body))

    async def test_the_playable_choice_stays_through_a_change_of_kind(self) -> None:
        body, _ = self._draw(rows=self.PLAYABLE_ROWS + [_game("g", "Kappa")],
                             releases={**self.PLAYABLE_RELEASES,
                                       "g": {"b2sFiles": [_release("b2")]}})
        _selects(body)["Kind"].set_value("tableFiles")
        await _settle()
        self._playable_switch(body).set_value(False)
        await _settle()

        _selects(body)["Kind"].set_value("b2sFiles")
        await _settle()
        _selects(body)["Kind"].set_value("tableFiles")
        await _settle()

        self.assertFalse(self._playable_switch(body).value)
        self.assertIn("Eta", _words(body))

    async def test_open_in_grid_asks_what_each_show_choice_and_the_switches_say(self) -> None:
        body, _ = self._draw()
        _selects(body)["Kind"].set_value("tableFiles")
        await _settle()
        next(one for one in body.descendants() if isinstance(one, ui.switch)
             and not one.value).set_value(True)
        await _settle()
        base = {"by": ["Wildman"], "kind": "tableFiles", "mod": "originals", "format": ["VPX"]}

        for show, extra in ((discovery_ask.EVERYTHING, {}),
                            (discovery_ask.YOURS, {"owned": "yes"}),
                            (discovery_ask.WITHOUT, {"owned": "yes", "held": "missing"}),
                            (discovery_ask.ELSEWHERE, {"owned": "no"})):
            with self.subTest(show=show):
                _selects(body)["Show"].set_value(show)
                await _settle()

                self.assertEqual({**base, **extra}, await self._asked(body))

    async def test_typing_stays_through_a_change_of_kind_and_starts_the_wall_over(self) -> None:
        body, narrow = self._draw()
        narrow("epsi")
        await _settle()

        _selects(body)["Kind"].set_value("tableFiles")
        await _settle()
        self.assertIn("Epsilon", _words(body))
        self.assertNotIn("Alpha", _words(body))

        self.state["page"]["limit"] = 999
        narrow("a")
        self.assertEqual(discovery_ask.WALL_AT_FIRST, self.state["page"]["limit"])

    async def test_a_wall_nothing_matches_says_so_in_games(self) -> None:
        body, narrow = self._draw()

        narrow("zzzz")
        await _settle()

        self.assertIn("No games match", _words(body))
        self.assertIn("0 games", _words(body))

    def test_the_bar_wraps_by_its_own_width_below_the_row_it_needs(self) -> None:
        css = (REPO / "manager" / "static" / "manager-base.css").read_text(encoding="utf-8")
        block = re.search(r"@container manager-subject \(max-width: 1020px\) \{(.*?)\n\}",
                          css, re.S)

        self.assertIsNotNone(block)
        self.assertIn(".manager-subject-end { order: 3; }", cast(Any, block).group(1))
        self.assertIn(".manager-subject-filters { order: 4; flex-basis: 100%; }",
                      cast(Any, block).group(1))
        self.assertIn(".manager-subject-root { container: manager-subject / inline-size; }", css)
        body, _ = self._draw()
        self.assertTrue(any("manager-subject-root" in one.classes
                            for one in [body, *body.descendants()]))


if __name__ == "__main__":
    unittest.main()
