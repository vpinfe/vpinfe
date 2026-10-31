"""The walk over a selection: what it writes, and what it leaves alone.

The picker itself is a dialog and is driven in the browser; what is worth pinning here is
the part a person's fingers cannot easily prove - that a game passed over is not rewritten
because it happened to be in the selection.
"""

from __future__ import annotations

import asyncio
import unittest
from typing import Any
from unittest.mock import patch

from manager.library import vps_match


async def _ran_here(fn, *args, **kwargs):
    """Stands in for `run.io_bound`, and must stay awaitable: a plain value makes the
    walk's `await` raise into its own except, and every assertion still passes."""
    return fn(*args, **kwargs)


class FakeLibrary:
    """Records the writes rather than making them."""

    def __init__(self) -> None:
        self.writes: list[tuple[str, dict]] = []
        self.declared: list[str] = []
        self.games: list[dict[str, Any]] = []

    def set_game_overrides(self, game_id: str, changes: dict) -> dict:
        self.writes.append((game_id, changes))
        return {}

    def declare_no_match(self, game_id: str) -> None:
        self.declared.append(game_id)


def games(*names: str) -> list[dict[str, Any]]:
    return [{"id": f"id-{name}", "name": name} for name in names]


def walk_answering(answers: list, chosen: list[dict[str, Any]]) -> FakeLibrary:
    """Run a walk where the picker returns `answers` in order."""
    library = FakeLibrary()
    handed = iter(answers)

    async def fake_ask(_library, _game, place="", walking=False):
        several = len(chosen) > 1
        assert walking == several, "only a walk of several asks in walking mode, with a Skip"
        assert bool(place) == several, "every game in a walk says where it is in the run"
        return next(handed)

    with patch.object(vps_match, "ask", fake_ask), \
            patch.object(vps_match.ui, "notify", lambda *a, **k: None), \
            patch.object(vps_match.run, "io_bound",
                         _ran_here):
        asyncio.run(vps_match.walk(library, chosen))
    return library


class TheSeedQuery(unittest.TestCase):
    def test_a_matched_game_searches_on_its_name(self) -> None:
        said = vps_match._seed({"name": "Bad Cats", "vps_id": "abc",
                                "overrides": {"alt_manufacturer": "Williams"}})

        self.assertEqual(said, "Bad Cats")

    def test_an_unmatched_game_adds_what_somebody_answered(self) -> None:
        said = vps_match._seed({"name": "Bad Cats", "vps_id": "",
                                "overrides": {"alt_manufacturer": "Williams",
                                              "alt_year": "1989"}})

        self.assertEqual(said, "Bad Cats Williams 1989")

    def test_nothing_answered_leaves_the_name_alone(self) -> None:
        said = vps_match._seed({"name": "Bad Cats", "vps_id": "", "overrides": {}})

        self.assertEqual(said, "Bad Cats")

    def test_a_parsed_value_is_not_a_seed(self) -> None:
        said = vps_match._seed({"name": "Bad Cats", "vps_id": "",
                                "manufacturer": "Some Homebrew Co", "year": "2011",
                                "overrides": {}})

        self.assertEqual(said, "Bad Cats")


class TheWalk(unittest.TestCase):

    def test_a_pick_is_written_against_the_game_it_was_asked_for(self) -> None:
        library = walk_answering(["vps-1"], games("A"))
        self.assertEqual(library.writes, [("id-A", {"alt_vps_id": "vps-1"})])

    def test_each_game_is_asked_as_the_library_holds_it(self) -> None:
        """A grid row says only whether a game is unmatched. Handed to the picker, it
        drew every game as unmatched: no Matched to, no Current."""
        library = FakeLibrary()
        library.games = [{"id": "id-A", "name": "A", "vps_id": "vps-1"}]
        asked: list[dict[str, Any]] = []

        async def fake_ask(_library, game, place="", walking=False):
            asked.append(game)
            return vps_match.CANCELLED

        with patch.object(vps_match, "ask", fake_ask), \
                patch.object(vps_match.ui, "notify", lambda *a, **k: None), \
                patch.object(vps_match.run, "io_bound", _ran_here):
            asyncio.run(vps_match.walk(library, [{"id": "id-A", "name": "A",
                                                  "vps_match": "matched"}]))

        self.assertEqual(asked[0].get("vps_id"), "vps-1")

    def test_skipping_writes_nothing_at_all(self) -> None:
        """The reason this walks rather than running: a game in the selection that the
        user passes over keeps whatever match it already had."""
        library = walk_answering([vps_match.CANCELLED, vps_match.CANCELLED],
                                 games("A", "B"))
        self.assertEqual(library.writes, [])

    def test_stopping_leaves_the_rest_of_the_selection_untouched(self) -> None:
        library = walk_answering(["vps-1", vps_match.STOPPED], games("A", "B", "C"))
        self.assertEqual(library.writes, [("id-A", {"alt_vps_id": "vps-1"})])

    def test_clearing_declares_a_none_and_is_not_a_skip(self) -> None:
        """Three outcomes that look alike: a declared none is a decision, `CANCELLED` is
        no decision, and an empty override says nobody has looked."""
        library = walk_answering([vps_match.CLEARED], games("A"))
        self.assertEqual(library.declared, ["id-A"])
        self.assertEqual(library.writes, [])

    def test_a_failed_write_does_not_end_the_run(self) -> None:
        """Thirty games in, one bad write should cost that game and not the other
        twenty-nine."""
        library = FakeLibrary()
        handed = iter(["vps-1", "vps-2"])
        calls: list[str] = []

        def sometimes(game_id: str, changes: dict) -> dict:
            calls.append(game_id)
            if game_id == "id-A":
                raise RuntimeError("no")
            return {}

        library.set_game_overrides = sometimes  # type: ignore[method-assign]

        async def fake_ask(_l, _g, place="", walking=False):
            return next(handed)

        with patch.object(vps_match, "ask", fake_ask), \
                patch.object(vps_match.ui, "notify", lambda *a, **k: None), \
                patch.object(vps_match.run, "io_bound",
                             _ran_here):
            asyncio.run(vps_match.walk(library, games("A", "B")))

        self.assertEqual(calls, ["id-A", "id-B"])

    def test_the_stop_sentinel_can_never_be_a_catalog_id(self) -> None:
        """It travels the same return as an id, so it has to be something the catalog
        cannot answer with."""
        self.assertIn("\x00", vps_match.STOPPED)


if __name__ == "__main__":
    unittest.main()
