"""Players over the wire: the roster, who is up, and the refusals in the catalog's words.

Each test gets a roster on a temporary file with an owner holding OWN, which is what an
install looks like once `main.py` has made its owner.
"""

from __future__ import annotations

import json
import unittest
from configparser import ConfigParser
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from starlette.testclient import TestClient

import httpapi
from common import events
from common.games import player_records
from common.i18n import t
from common.players import players
from httpapi import auth, scopes
from httpapi import events as event_stream
from tests.support.library import fake_game, write_game
from tests.support.pictures import RED, png


def _owner_config() -> ConfigParser:
    parser = ConfigParser()
    parser.add_section("vpinplay")
    parser.set("vpinplay", "initials", "OWN")
    return parser


class _PlayersCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        players.reset_for_tests(Path(tmp.name) / "players.json")
        self.addCleanup(players.reset_for_tests)
        owner = players.get_roster().ensure_owner(_owner_config())
        assert owner is not None
        self.owner = owner.player_id
        self.app = httpapi.create_api_app()
        self.addCleanup(event_stream.reset)
        self.client = TestClient(self.app, raise_server_exceptions=False)

    def _roster(self) -> list[dict]:
        response = self.client.get("/players")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["players"]

    def _up(self) -> list[str]:
        return [row["id"] for row in self._roster() if row["up"]]

    def _add(self, name: str, initials: str) -> str:
        response = self.client.post("/players", json={"name": name, "initials": initials})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["id"]

    def _guest(self, initials: str) -> str:
        response = self.client.post("/players/guests", json={"initials": initials})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["id"]

    def _refused(self, response, status: int, code: str, message: str) -> None:
        self.assertEqual(response.status_code, status, response.text)
        self.assertEqual(response.json()["error"]["code"], code)
        self.assertEqual(response.json()["error"]["message"], message)


class ReadTests(_PlayersCase):
    def test_an_install_of_one_lists_its_owner_up(self) -> None:
        self.assertEqual(self._roster(), [
            {"id": self.owner, "name": "", "initials": "OWN", "owner": True,
             "guest": False, "up": True, "shares_initials_with": [],
             "picture_version": None}])

    def test_one_player_reads_as_the_roster_lists_them(self) -> None:
        response = self.client.get(f"/players/{self.owner}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), self._roster()[0])

    def test_an_unknown_id_is_a_404_in_the_catalogs_words(self) -> None:
        self._refused(self.client.get("/players/Nobody0000"), 404, "not_found",
                      t("error.players.no_player", player_id="Nobody0000"))


class KeptPlayerTests(_PlayersCase):
    def test_a_player_is_added_and_is_not_up(self) -> None:
        response = self.client.post("/players", json={"name": "Kid", "initials": "kid"})

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual((body["name"], body["initials"], body["owner"], body["guest"],
                          body["up"]), ("Kid", "KID", False, False, False))
        self.assertEqual([row["id"] for row in self._roster()], [self.owner, body["id"]])

    def test_initials_another_player_has_are_refused_with_who(self) -> None:
        self._add("Kid", "KID")

        self._refused(self.client.post("/players", json={"name": "Other", "initials": "kid"}),
                      400, "invalid_request",
                      t("error.players.initials_taken", player="Kid"))

    def test_initials_that_are_not_three_characters_are_refused(self) -> None:
        self._refused(self.client.post("/players", json={"initials": "AB"}),
                      400, "invalid_request", t("error.players.initials_length"))

    def test_a_player_is_renamed_and_their_initials_left_alone(self) -> None:
        kid = self._add("Kid", "KID")

        response = self.client.patch(f"/players/{kid}", json={"name": "Robin"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual((response.json()["name"], response.json()["initials"]),
                         ("Robin", "KID"))

    def test_the_owner_can_be_given_initials(self) -> None:
        response = self.client.patch(f"/players/{self.owner}", json={"initials": "abc"})

        self.assertEqual(response.json()["initials"], "ABC")

    def test_changing_an_unknown_player_is_a_404(self) -> None:
        self.assertEqual(self.client.patch("/players/Nobody0000",
                                           json={"name": "x"}).status_code, 404)

    def test_a_player_is_removed(self) -> None:
        kid = self._add("Kid", "KID")

        response = self.client.delete(f"/players/{kid}")

        self.assertEqual(response.status_code, 204)
        self.assertEqual([row["id"] for row in self._roster()], [self.owner])

    def test_the_owner_cannot_be_removed(self) -> None:
        self._refused(self.client.delete(f"/players/{self.owner}"), 400, "invalid_request",
                      t("error.players.owner_not_removable"))
        self.assertEqual([row["id"] for row in self._roster()], [self.owner])


class GuestTests(_PlayersCase):
    def test_a_guest_joins_from_initials_and_is_up_alone(self) -> None:
        response = self.client.post("/players/guests", json={"initials": "abc"})

        self.assertEqual(response.status_code, 201)
        guest = response.json()
        self.assertEqual((guest["initials"], guest["guest"], guest["up"]), ("ABC", True, True))
        self.assertEqual(self._up(), [guest["id"]])

    def test_a_guest_with_no_initials_is_refused(self) -> None:
        self._refused(self.client.post("/players/guests", json={"initials": " "}),
                      400, "invalid_request", t("error.players.guest_initials"))

    def test_a_guest_with_a_kept_players_initials_joins_and_both_say_so(self) -> None:
        guest = self._guest("own")

        rows = {row["id"]: row for row in self._roster()}
        self.assertEqual(rows[guest]["shares_initials_with"], [self.owner])
        self.assertEqual(rows[self.owner]["shares_initials_with"], [guest])

    def test_signing_the_last_guest_out_puts_the_owner_back_up(self) -> None:
        guest = self._guest("ABC")

        self.assertEqual(self.client.delete(f"/players/{guest}").status_code, 204)

        self.assertEqual(self._up(), [self.owner])
        self.assertEqual([row["id"] for row in self._roster()], [self.owner])

    def test_kid_stays_up_when_the_visitor_beside_them_signs_out(self) -> None:
        """Kid and a visitor are up, the visitor leaves: Kid is still up and the owner
        is not, so Kid's next game is not counted for the owner."""
        kid = self._add("Kid", "KID")
        visitor = self._guest("ABC")
        self.client.put(f"/players/{kid}/up", json={"up": True})
        self.assertEqual(self._up(), [kid, visitor])

        self.client.delete(f"/players/{visitor}")

        self.assertEqual(self._up(), [kid])


class UpTests(_PlayersCase):
    def setUp(self) -> None:
        super().setUp()
        self.kid = self._add("Kid", "KID")

    def test_one_player_is_put_up_beside_whoever_is(self) -> None:
        response = self.client.put(f"/players/{self.kid}/up", json={"up": True})

        self.assertEqual(response.status_code, 200)
        self.assertEqual([row["id"] for row in response.json()["players"] if row["up"]],
                         [self.owner, self.kid])

    def test_taking_the_last_one_down_answers_with_the_owner_up(self) -> None:
        self.client.put("/players/up", json={"ids": [self.kid]})

        response = self.client.put(f"/players/{self.kid}/up", json={"up": False})

        self.assertEqual([row["id"] for row in response.json()["players"] if row["up"]],
                         [self.owner])

    def test_the_whole_set_is_named_at_once(self) -> None:
        guest = self._guest("ABC")

        response = self.client.put("/players/up", json={"ids": [guest, self.kid]})

        self.assertEqual(response.status_code, 200)
        self.assertEqual([row["id"] for row in response.json()["players"] if row["up"]],
                         [self.kid, guest])

    def test_naming_nobody_leaves_the_owner_up(self) -> None:
        self.client.put("/players/up", json={"ids": [self.kid]})

        response = self.client.put("/players/up", json={"ids": []})

        self.assertEqual([row["id"] for row in response.json()["players"] if row["up"]],
                         [self.owner])

    def test_a_set_naming_someone_unknown_is_a_404_and_changes_nothing(self) -> None:
        response = self.client.put("/players/up", json={"ids": [self.kid, "Nobody0000"]})

        self.assertEqual(response.status_code, 404)
        self.assertEqual(self._up(), [self.owner])

    def test_putting_an_unknown_player_up_is_a_404(self) -> None:
        self.assertEqual(self.client.put("/players/Nobody0000/up",
                                         json={"up": True}).status_code, 404)


class RecordTests(_PlayersCase):
    """A player's own record and rating, beside the library's, which stays the owner's."""

    GAME = "RateMe0001"

    def setUp(self) -> None:
        super().setUp()
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.records_dir = Path(tmp.name) / "player_records"
        player_records.reset_for_tests(self.records_dir)
        self.addCleanup(player_records.reset_for_tests)
        info = {"Info": {"Name": "Example"}, "VPinFE": {"game_id": self.GAME}}
        folder = write_game(Path(tmp.name) / "library", "Example", info=info)
        self.info_path = folder / "Example.info"
        patcher = mock.patch("common.games.game_repository.catalog",
                             return_value={self.GAME: fake_game(folder, meta=info)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def _rate(self, player: str, rating, game: str = GAME):
        return self.client.put(f"/players/{player}/ratings/{game}", json={"rating": rating})

    def _record(self, player: str) -> dict:
        response = self.client.get(f"/players/{player}/record")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_a_player_s_rating_is_theirs_and_not_the_library_s(self) -> None:
        kept = self._add("Jordan", "ABC")

        response = self._rate(kept, 4)

        self.assertEqual((response.status_code, response.json()), (200, {"rating": 4}))
        (game,) = self._record(kept)["games"]
        self.assertEqual((game["game_id"], game["rating"], game["play_count"]),
                         (self.GAME, 4, 0))
        self.assertNotIn("User", json.loads(self.info_path.read_text(encoding="utf-8")))

    def test_the_rating_takes_the_library_route_s_scale(self) -> None:
        kept = self._add("Jordan", "ABC")

        for rating in (11, -1, "great"):
            with self.subTest(rating=rating):
                self.assertEqual(self._rate(kept, rating).status_code, 422)
        self.assertEqual(self._record(kept)["games"], [])

    def test_a_game_or_a_player_nobody_has_is_not_found(self) -> None:
        kept = self._add("Jordan", "ABC")

        self.assertEqual(self._rate(kept, 3, game="nosuchgame").status_code, 404)
        self.assertEqual(self._rate("nobody", 3).status_code, 404)
        self.assertEqual(self.client.get("/players/nobody/record").status_code, 404)

    def test_the_owner_s_are_the_library_s(self) -> None:
        said = t("error.players.owner_record")

        self._refused(self._rate(self.owner, 3), 400, "invalid_request", said)
        self._refused(self.client.get(f"/players/{self.owner}/record"), 400,
                      "invalid_request", said)

    def test_a_kept_player_s_record_goes_when_they_do(self) -> None:
        kept = self._add("Jordan", "ABC")
        self._rate(kept, 4)
        self.assertTrue((self.records_dir / f"{kept}.json").exists())

        self.assertEqual(self.client.delete(f"/players/{kept}").status_code, 204)

        self.assertFalse((self.records_dir / f"{kept}.json").exists())

    def test_a_guest_s_record_goes_when_they_sign_out(self) -> None:
        guest = self._guest("GST")
        self._rate(guest, 5)
        self.assertEqual(len(self._record(guest)["games"]), 1)
        held = players.get_roster().player(guest)

        self.client.delete(f"/players/{guest}")

        self.assertEqual(player_records.get_records().games(held), {})
        self.assertFalse(self.records_dir.exists())


class PictureTests(_PlayersCase):
    def _put(self, player_id: str, content: bytes, name: str = "me.png"):
        return self.client.put(f"/players/{player_id}/picture",
                               files={"file": (name, content, "image/png")})

    def test_a_player_has_none_until_one_is_set(self) -> None:
        self.assertIsNone(self._roster()[0]["picture_version"])
        self._refused(self.client.get(f"/players/{self.owner}/picture"), 404, "not_found",
                      t("error.players.no_picture"))

    def test_setting_one_answers_the_player_with_its_version_and_serves_it(self) -> None:
        response = self._put(self.owner, png(400, 300))

        self.assertEqual(response.status_code, 200, response.text)
        version = response.json()["picture_version"]
        self.assertTrue(version)
        self.assertEqual(self._roster()[0]["picture_version"], version)
        served = self.client.get(f"/players/{self.owner}/picture", params={"v": version})
        self.assertEqual(served.status_code, 200)
        self.assertEqual(served.headers["content-type"], "image/webp")
        self.assertIn("immutable", served.headers["cache-control"])

    def test_a_size_is_served_and_another_is_refused(self) -> None:
        self._put(self.owner, png(600, 600))

        small = self.client.get(f"/players/{self.owner}/picture", params={"size": 256})
        self.assertEqual(small.status_code, 200)
        self.assertEqual(self.client.get(f"/players/{self.owner}/picture",
                                         params={"size": 100}).status_code, 400)

    def test_a_replacement_changes_the_version(self) -> None:
        first = self._put(self.owner, png(300, 300)).json()["picture_version"]
        second = self._put(self.owner, png(300, 300, RED, "JPEG"), "me.jpg").json()
        self.assertNotEqual(first, second["picture_version"])

    def test_clearing_answers_the_player_with_none(self) -> None:
        self._put(self.owner, png(300, 300))

        response = self.client.delete(f"/players/{self.owner}/picture")

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()["picture_version"])
        self.assertEqual(self.client.get(f"/players/{self.owner}/picture").status_code, 404)

    def test_a_file_that_is_not_an_image_is_refused_and_changes_nothing(self) -> None:
        self._refused(self._put(self.owner, b"not a picture", "me.png"), 400,
                      "invalid_request", t("error.players.picture_not_image"))
        self.assertIsNone(self._roster()[0]["picture_version"])

    def test_a_guest_cannot_have_one(self) -> None:
        guest = self._guest("GST")

        self._refused(self._put(guest, png(300, 300)), 400, "invalid_request",
                      t("error.players.picture_guest"))

    def test_a_player_nobody_has_is_a_404(self) -> None:
        self._refused(self._put("Nobody0000", png(300, 300)), 404, "not_found",
                      t("error.players.no_player", player_id="Nobody0000"))

    def test_a_player_taken_away_takes_their_picture(self) -> None:
        kid = self._add("Kid", "KID")
        self._put(kid, png(300, 300))
        folder = players.get_roster().pictures_dir

        self.client.delete(f"/players/{kid}")

        self.assertEqual(list(folder.glob("*")), [])


class ContractTests(_PlayersCase):
    def test_reads_and_writes_carry_their_own_scopes(self) -> None:
        declared = {(method, path): auth.route_scope(route)
                    for path, route in auth.iter_api_routes(self.app)
                    if path.startswith("/players") for method in route.methods or ()}

        self.assertEqual({scope for (method, _), scope in declared.items()
                          if method == "GET"}, {scopes.PLAYERS_READ})
        self.assertEqual({scope for (method, _), scope in declared.items()
                          if method != "GET"},
                         {scopes.PLAYERS_WRITE, scopes.PLAY_RATE, scopes.PLAY_PLAYERS})

    def test_every_caller_is_granted_them(self) -> None:
        """The Manager and the Remote reach the API as any caller does, under the local
        trust policy."""
        self.assertLessEqual({scopes.PLAYERS_READ, scopes.PLAYERS_WRITE}, scopes.CORE)

    def test_the_snapshot_a_new_subscriber_gets_is_the_roster_as_read(self) -> None:
        self._guest("ABC")

        snapshot = event_stream._snapshots[events.PLAYERS_CHANGED]()

        self.assertEqual(snapshot, {"state": {"players": self._roster()}})

    def test_discovery_offers_players_where_the_frontend_is(self) -> None:
        declared = {c["name"]: c for c in self.client.get("/").json()["capabilities"]}

        self.assertEqual(declared["players"]["feature"], "frontend")


if __name__ == "__main__":
    unittest.main()
