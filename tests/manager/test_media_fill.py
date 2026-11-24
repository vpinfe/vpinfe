"""Get missing media: which kinds start ticked, what is kept, and what it says at the end."""

from __future__ import annotations

import asyncio
import unittest
from typing import Any
from unittest import mock

from manager.library import media_fill
from manager.library.media_fill import outcome, remember, ticked

KINDS = [{"kind": "wheel", "missing": 12, "available": 9},
         {"kind": "backglass", "missing": 4, "available": 2},
         {"kind": "flyer", "missing": 0, "available": 0},
         {"kind": "topper", "missing": 5, "available": 0}]


class TickTests(unittest.TestCase):
    def test_first_time_every_kind_with_something_to_get_is_ticked(self) -> None:
        self.assertEqual(ticked(KINDS, {}), {"wheel": True, "backglass": True,
                                             "flyer": False, "topper": False})

    def test_a_kind_left_unticked_stays_unticked(self) -> None:
        self.assertEqual(ticked(KINDS, {"backglass": False})["backglass"], False)

    def test_a_kind_ticked_before_with_nothing_to_get_now_is_not(self) -> None:
        self.assertEqual(ticked(KINDS, {"topper": True})["topper"], False)

    def test_a_dimmed_row_keeps_what_was_held_for_it(self) -> None:
        held = {"topper": True, "wheel": True}
        chosen = {"wheel": False, "backglass": True, "flyer": False, "topper": False}

        self.assertEqual(remember(held, chosen, KINDS),
                         {"topper": True, "wheel": False, "backglass": True})


class OutcomeTests(unittest.TestCase):
    def test_it_says_how_many_files_it_got(self) -> None:
        self.assertEqual(outcome({"filled": 1}), ("Downloaded 1 file", "positive"))
        self.assertEqual(outcome({"filled": 18, "unmatched": 3}),
                         ("Downloaded 18 files", "positive"))

    def test_failures_are_counted_beside_what_it_got(self) -> None:
        self.assertEqual(outcome({"filled": 5, "failed": 2}),
                         ("Downloaded 5 files, 2 failed", "warning"))
        self.assertEqual(outcome({"failed": 2}), ("Could not download 2 files", "warning"))

    def test_nothing_found_is_not_a_failure(self) -> None:
        self.assertEqual(outcome({"games": 3, "filled": 0}), ("No media found", "info"))

    def test_a_source_that_did_not_answer_is_not_nothing_found(self) -> None:
        self.assertEqual(outcome({"games": 3, "filled": 0, "unreachable": ["VPinMediaDB"]}),
                         ("VPinMediaDB could not be reached", "warning"))

    def test_a_source_that_did_not_answer_is_named_after_what_it_got(self) -> None:
        self.assertEqual(outcome({"filled": 5, "failed": 2, "unreachable": ["Elsewhere"]}),
                         ("Downloaded 5 files, 2 failed, Elsewhere could not be reached",
                          "warning"))
        self.assertEqual(outcome({"filled": 1, "unreachable": ["Elsewhere"]}),
                         ("Downloaded 1 file, Elsewhere could not be reached", "warning"))


def _row(game: str, kind: str, *, present: bool = False, table: str = "",
         standing_in: str = "", vps_id: str = "vps1") -> dict:
    return {"game_id": game, "kind": kind, "present": present, "table": table,
            "standing_in": standing_in, "vps_id": vps_id}


class SlotTests(unittest.TestCase):
    """A Media grid selection: only its rows with no file are asked for."""

    def _confirm(self, rows: list[dict], answer: bool = True) -> tuple[mock.Mock, ...]:
        with mock.patch.object(media_fill.confirm, "ask",
                               mock.AsyncMock(return_value=answer)) as ask, \
                mock.patch.object(media_fill, "get", mock.AsyncMock()) as get, \
                mock.patch.object(media_fill, "ApiClient") as client, \
                mock.patch.object(media_fill.ui, "notify") as notify:
            asyncio.run(media_fill.confirm_slots(rows, {}, lambda: None))
            if get.await_args:
                get.await_args.args[0]()
        return ask, get, client, notify

    def test_only_the_rows_with_no_file_are_fetched(self) -> None:
        rows = [_row("g1", "wheel"), _row("g1", "backglass", present=True),
                _row("g2", "wheel"), _row("g2", "topper", standing_in="set:Classic"),
                _row("g2", "flyer", present=True, table="t1")]

        ask, _get, client, _notify = self._confirm(rows)

        self.assertEqual(ask.await_args.args[0], "Download media for 2 missing files?")
        client.return_value.fill_media.assert_called_once_with(
            slots=(("g1", "wheel"), ("g2", "wheel")))

    def test_a_game_with_no_match_is_named(self) -> None:
        ask, *_ = self._confirm([_row("g1", "wheel", vps_id=""), _row("g2", "wheel")])

        self.assertEqual(ask.await_args.kwargs["lines"],
                         ["1 is not matched to VPS, so nothing can be looked up for it"])

    def test_cancelled_fetches_nothing(self) -> None:
        _ask, get, *_ = self._confirm([_row("g1", "wheel")], answer=False)

        get.assert_not_awaited()

    def test_nothing_missing_asks_nothing(self) -> None:
        ask, get, _client, notify = self._confirm([_row("g1", "wheel", present=True)])

        ask.assert_not_awaited()
        get.assert_not_awaited()
        self.assertEqual(notify.call_args.args[0], "Nothing selected is missing")


def _plan(missing: int, available: int, *, unmatched: int = 0,
          unreachable: tuple[str, ...] = ()) -> dict:
    return {"games": missing, "unmatched": unmatched, "sources": ["VPinMediaDB"],
            "unreachable": list(unreachable),
            "kinds": [{"kind": "playfield", "missing": missing, "available": available},
                      {"kind": "wheel", "missing": 1, "available": 1}]}


class KindTests(unittest.TestCase):
    """An Overview finding: one kind for its games, asked plainly."""

    def _confirm(self, plan: dict, answer: bool = True) -> tuple[mock.Mock, ...]:
        with mock.patch.object(media_fill.offload, "io",
                               mock.AsyncMock(return_value=plan)), \
                mock.patch.object(media_fill.confirm, "ask",
                                  mock.AsyncMock(return_value=answer)) as ask, \
                mock.patch.object(media_fill, "get", mock.AsyncMock()) as get, \
                mock.patch.object(media_fill, "ApiClient") as client, \
                mock.patch.object(media_fill.ui, "notify") as notify:
            asyncio.run(media_fill.confirm_kind(["g1", "g2"], "playfield", {},
                                              lambda: None))
            if get.await_args:
                get.await_args.args[0]()
        return ask, get, client, notify

    def test_it_gets_that_kind_for_those_games(self) -> None:
        ask, _get, client, _notify = self._confirm(_plan(2, 2))

        self.assertEqual(ask.await_args.args[0], "Download Playfield media for 2 games?")
        client.return_value.fill_media.assert_called_once_with(["g1", "g2"], ["playfield"])

    def test_the_question_counts_what_a_source_has(self) -> None:
        ask, *_ = self._confirm(_plan(2, 1, unmatched=1))

        self.assertEqual(ask.await_args.args[0], "Download Playfield media for 1 of 2 games?")
        self.assertEqual(ask.await_args.kwargs["lines"],
                         ["1 is not matched to VPS, so nothing can be looked up for it"])

    def test_cancelled_fetches_nothing(self) -> None:
        _ask, get, *_ = self._confirm(_plan(2, 2), answer=False)

        get.assert_not_awaited()

    def test_nothing_to_get_says_why_and_asks_nothing(self) -> None:
        cases = ((_plan(2, 0), "No source has Playfield media for these games"),
                 (_plan(2, 0, unmatched=2),
                  "2 are not matched to VPS, so nothing can be looked up for those"),
                 (_plan(2, 0, unreachable=("VPinMediaDB",)),
                  "VPinMediaDB could not be reached"))
        for plan, said in cases:
            with self.subTest(said=said):
                ask, get, _client, notify = self._confirm(plan)

                ask.assert_not_awaited()
                get.assert_not_awaited()
                self.assertEqual(notify.call_args.args[0], said)

    def test_no_source_on_still_asks_when_the_games_own_files_have_art(self) -> None:
        plan = {"games": 2, "unmatched": 2, "sources": [], "unreachable": [],
                "kinds": [{"kind": "backglass", "missing": 2, "available": 2, "local": 2}]}
        with mock.patch.object(media_fill.offload, "io", mock.AsyncMock(return_value=plan)), \
                mock.patch.object(media_fill.confirm, "ask",
                                  mock.AsyncMock(return_value=False)) as ask, \
                mock.patch.object(media_fill, "get", mock.AsyncMock()), \
                mock.patch.object(media_fill, "ApiClient"), \
                mock.patch.object(media_fill.ui, "notify") as notify:
            asyncio.run(media_fill.confirm_kind(["g1", "g2"], "backglass", {}, lambda: None))

        ask.assert_awaited_once()
        notify.assert_not_called()


class FromFilesTests(unittest.TestCase):
    def test_a_source_or_a_file_is_something_to_ask(self) -> None:
        local = {"kinds": [{"kind": "backglass", "local": 1}]}
        cases: tuple[tuple[dict[str, Any], bool], ...] = (
            ({"sources": ["VPinMediaDB"], "kinds": []}, True),
            ({"sources": [], **local}, True),
            ({"sources": [], "kinds": [{"kind": "backglass", "local": 0}]}, False),
            ({}, False))
        for found, expected in cases:
            with self.subTest(found=found):
                self.assertEqual(media_fill._anything_to_ask(found), expected)

    def test_the_files_are_counted_across_kinds(self) -> None:
        found = {"kinds": [{"kind": "backglass", "local": 3}, {"kind": "wheel"},
                           {"kind": "scoreview", "local": 2}]}

        self.assertEqual(media_fill._from_files(found), 5)


if __name__ == "__main__":
    unittest.main()
