"""What an extension adds to an entry a theme is handed.

A theme reads `entry.ext.<key>` and never learns which extension answered. That is the
whole point: the surface a theme sees stops growing a method per connector, which is what
it does today for one vendor.
"""

from __future__ import annotations

import unittest

from common.extensions import contributions


class RegisterTests(unittest.TestCase):
    def setUp(self) -> None:
        contributions.clear()
        self.addCleanup(contributions.clear)

    def test_a_contributor_answers_under_its_declared_key(self) -> None:
        contributions.register("rater", "rating", lambda game: {"stars": 4})

        found = contributions.refresh({"game_id": "abc"})

        self.assertEqual(found, {"rating": {"stars": 4}})
        self.assertEqual(contributions.held("abc"), {"rating": {"stars": 4}})

    def test_two_extensions_cannot_claim_one_key(self) -> None:
        """A theme reading entry.ext.rating has to be reading one thing."""
        contributions.register("rater", "rating", lambda game: None)

        with self.assertRaises(ValueError):
            contributions.register("other", "rating", lambda game: None)

    def test_a_game_nothing_knows_about_carries_an_empty_slot(self) -> None:
        """Present and empty, so a theme written as `if (entry.ext.rating)` is right
        without ever knowing there is a waiting state."""
        self.assertEqual(contributions.held("abc"), {})

    def test_a_contributor_is_asked_once_per_game(self) -> None:
        """One fetch serves every window and survives a reload, where a page's own cache
        is lost on each one and a three-screen cabinet fetches thrice."""
        asked = []
        contributions.register("rater", "rating",
                               lambda game: asked.append(game["game_id"]) or {"stars": 1})  # type: ignore[func-returns-value]

        contributions.refresh({"game_id": "abc"})
        contributions.refresh({"game_id": "abc"})

        self.assertEqual(asked, ["abc"])

    def test_nothing_to_say_is_remembered_as_an_answer(self) -> None:
        """Or a game with no rating is asked about again on every wheel step."""
        asked = []
        contributions.register("rater", "rating",
                               lambda game: asked.append(1) or None)  # type: ignore[func-returns-value]

        contributions.refresh({"game_id": "abc"})
        contributions.refresh({"game_id": "abc"})

        self.assertEqual(len(asked), 1)
        self.assertEqual(contributions.held("abc"), {})

    def test_one_contributor_failing_costs_only_its_own_key(self) -> None:
        """A connector's server being down is not a reason to stop showing another's."""
        def broken(game):
            raise RuntimeError("down")

        contributions.register("rater", "rating", broken)
        contributions.register("badger", "badge", lambda game: {"text": "new"})

        with self.assertLogs("vpinfe.common.extensions.contributions", "ERROR"):
            found = contributions.refresh({"game_id": "abc"})

        self.assertEqual(found, {"badge": {"text": "new"}})

    def test_taking_an_extension_out_takes_its_answers_with_it(self) -> None:
        """They came from something that is no longer running, and leaving them would
        show a rating nothing can refresh."""
        contributions.register("rater", "rating", lambda game: {"stars": 4})
        contributions.refresh({"game_id": "abc"})

        contributions.forget("rater")

        self.assertEqual(contributions.held("abc"), {})
        self.assertEqual(contributions.keys(), ())


class SelectionTests(unittest.TestCase):
    """What core does when the player moves to a game."""

    def setUp(self) -> None:
        from frontend import ext_data

        contributions.clear()
        self.addCleanup(contributions.clear)
        self.ext_data = ext_data
        ext_data.reset_for_tests()
        self.addCleanup(ext_data.reset_for_tests)
        self.sent: list[dict] = []
        ext_data.register(self.sent.append)

    def _game(self, game_id: str, name: str = "Taxi"):
        from types import SimpleNamespace

        return SimpleNamespace(
            game_dir_name=name, full_path_game=f"/library/{name}",
            meta_config={"Info": {"Title": name, "VPSId": "vps-1"},
                         "vpinfe": {"game_id": game_id}})

    def _settle(self) -> None:
        import threading

        for thread in threading.enumerate():
            if thread.name == "ext-data":
                thread.join(timeout=5)

    def test_the_message_names_the_game_it_is_about(self) -> None:
        """So an answer arriving after the wheel has moved lands on the entry it belongs
        to rather than the one now in front of the player."""
        contributions.register("rater", "rating", lambda game: {"stars": 4})

        self.ext_data.on_selected(game=self._game("abc"))
        self._settle()

        self.assertEqual(self.sent, [{"type": "EntryDataChange", "game_id": "abc",
                                      "ext": {"rating": {"stars": 4}}}])

    def test_the_neighbours_are_fetched_and_not_announced(self) -> None:
        """Fetched to be ready, so the gap only shows on the first game of a cold start.
        Announcing one would be a message per wheel step about a game nobody is looking
        at."""
        asked = []
        contributions.register("rater", "rating",
                               lambda game: asked.append(game["game_id"]) or {"stars": 1})  # type: ignore[func-returns-value]

        self.ext_data.on_selected(game=self._game("abc"),
                                  neighbors=[self._game("next"), self._game("prev")])
        self._settle()

        self.assertEqual(asked, ["abc", "next", "prev"])
        self.assertEqual([one["game_id"] for one in self.sent], ["abc"])

    def test_nothing_is_fetched_when_no_extension_contributes(self) -> None:
        self.ext_data.on_selected(game=self._game("abc"))
        self._settle()

        self.assertEqual(self.sent, [])

    def test_a_contributor_is_never_handed_our_game(self) -> None:
        """One that held it could reach the whole library through it, which is what the
        context exists to prevent."""
        seen = []
        contributions.register("rater", "rating",
                               lambda game: seen.append(game) or None)  # type: ignore[func-returns-value]

        self.ext_data.on_selected(game=self._game("abc"))
        self._settle()

        self.assertEqual(seen[0].__class__, dict)
        self.assertEqual(sorted(seen[0]),
                         ["game_id", "ipdb_id", "manufacturer", "name", "vps_id", "year"])


if __name__ == "__main__":
    unittest.main()
