"""Rating a game over HTTP.

`GET /games/{id}` has always reported a rating and nothing could set one, so the only
way to rate a game was the window channel - which addresses games by their position in
one window's filtered list, and is therefore unavailable to every other caller. This is
the write half, addressed by id like the rest of the API.
"""

from __future__ import annotations

import unittest
from configparser import ConfigParser
from unittest.mock import patch

from starlette.testclient import TestClient

import httpapi
from common import events
from common.players import players
from tests.support.library import TempTree, fake_game, write_game
from tests.support.saved import saved_record

GAME_ID = "RateMe0001"


def _info(rating=None) -> dict:
    user = {} if rating is None else {"Rating": rating}
    return {"Info": {"Name": "Attack from Mars"}, "vpinfe": {"game_id": GAME_ID},
            "User": user}


class GameRatingTests(TempTree):
    def setUp(self) -> None:
        super().setUp()
        folder = write_game(self.root, "Attack from Mars (Bally 1995)", info=_info())
        self.info_path = folder / "Attack from Mars (Bally 1995).info"
        self.game = fake_game(folder, "Attack from Mars (Bally 1995)", meta=_info())
        patcher = patch("common.games.game_repository.catalog", return_value={GAME_ID: self.game})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)

    def _put(self, rating):
        return self.client.put(f"/games/{GAME_ID}/rating", json={"rating": rating})

    def test_a_rating_is_stored_and_reported_back(self) -> None:
        response = self._put(4)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"rating": 4})

    def _on_disk(self) -> int:
        """The rating as the `.info` holds it.

        Read from the file rather than through the in-memory game: `set_game_rating`
        updates that copy too, so asserting on it passes whether or not anything was
        ever written.
        """
        user = saved_record(self.info_path).get("User") or {}
        return int(user.get("Rating") or 0)     # absent is unrated, not an error

    def test_the_rating_reaches_the_info_file(self) -> None:
        """The response saying 4 is not the same as 4 being on disk."""
        self._put(4)

        self.assertEqual(self._on_disk(), 4)

    def test_zero_is_a_rating_and_not_a_missing_one(self) -> None:
        self._put(5)

        self.assertEqual(self._put(0).json(), {"rating": 0})
        self.assertEqual(self._on_disk(), 0)

    def test_rating_the_same_game_twice_is_not_two_different_answers(self) -> None:
        """A PUT is the whole value, so sending it again says the same thing."""
        self._put(3)

        self.assertEqual(self._put(3).json(), {"rating": 3})

    def test_a_rating_outside_the_range_is_refused_rather_than_clamped(self) -> None:
        """Storing 5 for a caller that sent 9 would hide the caller's bug."""
        for rating in (6, 9, -1):
            with self.subTest(rating=rating):
                self.assertEqual(self._put(rating).status_code, 422)

        self.assertEqual(self._on_disk(), 0, "nothing was written")

    def test_a_rating_that_is_not_a_number_is_refused(self) -> None:
        self.assertEqual(self._put("great").status_code, 422)

    def test_rating_a_game_that_does_not_exist_is_a_404(self) -> None:
        response = self.client.put("/games/nosuchgame/rating", json={"rating": 3})

        self.assertEqual(response.status_code, 404)

    def test_the_rating_is_announced_as_the_owner_s(self) -> None:
        players.reset_for_tests(self.root / "players.json")
        self.addCleanup(players.reset_for_tests)
        owner = players.get_roster().ensure_owner(ConfigParser())
        assert owner is not None
        heard: list[dict] = []
        hear = events.subscribe(events.GAME_RATED, lambda **payload: heard.append(payload))
        self.addCleanup(events.unsubscribe, events.GAME_RATED, hear)

        self._put(4)

        self.assertEqual(heard, [{"game_id": GAME_ID, "player": owner.as_payload(),
                                  "rating": 4}])

    def test_the_game_resource_links_to_its_rating(self) -> None:
        """Discoverable rather than only documented."""
        links = self.client.get(f"/games/{GAME_ID}").json()["links"]

        self.assertEqual(links["rating"], f"/api/v1/games/{GAME_ID}/rating")


if __name__ == "__main__":
    unittest.main()
