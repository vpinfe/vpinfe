"""What VPinPlay is sent: a game as it ends, for each sharing player who was up or scored.

Driven as core drives it - `table.play_recorded` and `game.rated` on the bus, Send Now
through core's account route - over the real extension. The service is stubbed at the
extension's own two calls, and a request that reaches the network fails the test.
"""

from __future__ import annotations

import sys
import unittest
from typing import Any
from unittest.mock import MagicMock, patch

from common import events
from common.extensions import accounts
from common.extensions import games as offered_games
from common.games import player_records
from common.players import players
from tests.extensions.test_cards import KEY, _2x_card
from tests.extensions.test_vpinplay_accounts import NAME, VPinPlayCase

GAME = "Gme1111111"
OTHER = "Gme2222222"
AT = "2026-09-28T20:00:00Z"
# As core hands it over: a blank already given the one player up's initials, and a `???`
# left as the machine wrote it.
READING: dict[str, Any] = {"rom": "ex", "resolved_rom": "ex",
                           "score_kind": "Leaderboard", "entries": [
    {"section": "HIGH SCORES", "rank": 1, "initials": "OWN", "score": 900},
    {"section": "HIGH SCORES", "rank": 2, "initials": "???", "score": 800},
    {"section": "HIGH SCORES", "rank": 3, "initials": "ABC", "score": 700}]}


def _only(entry: dict) -> dict:
    """The reading, kept to one player's own entry - what a send carries for them."""
    return {**READING, "entries": [entry]}


def _game(game_id: str, **changes: object) -> dict:
    return {"id": game_id, "name": f"Game {game_id}", "vps_id": f"vps-{game_id}",
            "rom": "ex", "private": False,
            "user": {"rating": 8, "last_played": AT, "play_count": 7,
                     "play_time_seconds": 600, "high_scores": None},
            "overrides": {"alt_title": "Mine", "alt_vps_id": ""}, **changes}


class SendingCase(VPinPlayCase):
    def setUp(self) -> None:
        super().setUp()
        self.owner = self.make_owner()
        self.load()
        player_records.reset_for_tests(self.root / "player_records")
        self.addCleanup(player_records.reset_for_tests)
        self.games = {GAME: _game(GAME), OTHER: _game(OTHER)}
        offered_games.offer("get_game", "games:read", lambda game_id: self.games[game_id])
        offered_games.offer("game_tables", "games:read", lambda game_id: {"tables": [
            {"default": True, "filename": "Example.vpx", "file_hash": "h"}]})
        self.addCleanup(offered_games.withdraw_all)
        sync = sys.modules[f"vpinfe_ext_{NAME}.sync"]
        self.send = MagicMock(return_value={"ok": True, "status_code": 200,
                                            "response_body": ""})
        self.their_record = MagicMock(return_value={})
        self.later: list = []
        for module, name, value in (
                (sync, "send", self.send), (sync, "their_record", self.their_record),
                (sys.modules[f"vpinfe_ext_{NAME}.sending"], "_on_a_thread",
                 self.later.append)):
            patched = patch.object(module, name, value)
            patched.start()
            self.addCleanup(patched.stop)

    # -- driving it --------------------------------------------------------------

    def run_later(self) -> None:
        while self.later:
            self.later.pop(0)()

    def sharing(self, player_id: str, user_id: str = "") -> str:
        if user_id:
            # A key too: a send needs a claimed account, and nothing here is testing
            # the claim itself.
            self.fill(player_id, user_id=user_id, key=KEY)
        players.get_roster().set_sharing(player_id, NAME, True)
        return player_id

    def kept(self, initials: str = "ABC") -> players.Player:
        return players.get_roster().add_player("Jordan", initials)

    def play(self, *up: str, game_id: str = GAME, credited: tuple[str, ...] = (),
             private: bool = False, reading: dict | None = READING) -> None:
        """A game ending as `launch_game` ends one: each player up counted in their
        record first, then the event."""
        roster = players.get_roster()
        for player in (roster.player(one) for one in up):
            if not player.owner:
                player_records.get_records().count_start(player, game_id, AT)
                player_records.get_records().add_time(player, game_id, 1800)
        events.emit(events.TABLE_PLAY_RECORDED, game=None, ini_config=None,
                    table_id="t1", game_id=game_id, source="api", private=private,
                    up=[roster.player(one).as_payload() for one in up], seconds=1800,
                    reading=reading,
                    new_entries=[{"player": roster.player(one).as_payload(),
                                  "entries": [READING["entries"][2]]} for one in credited])
        self.run_later()

    # -- reading what went -------------------------------------------------------

    def requests_sent(self) -> list[dict]:
        return [call.args[1] for call in self.send.call_args_list]

    def games_sent(self) -> list[tuple[str, str]]:
        return [(request["client"]["userId"], table["info"]["vpsId"])
                for request in self.requests_sent() for table in request["tables"]]

    def only_table(self) -> dict:
        (request,) = self.requests_sent()
        (table,) = request["tables"]
        return table


class WhatAPlaySends(SendingCase):
    def test_the_owner_s_game_goes_with_the_library_s_record_of_it(self) -> None:
        self.sharing(self.owner, "owner-id")

        self.play(self.owner)

        (request,) = self.requests_sent()
        self.assertEqual(request["client"], {"userId": "owner-id", "initials": "OWN",
                                             "machineId": self.held_key(self.owner)})
        table = request["tables"][0]
        self.assertEqual((table["info"]["vpsId"], table["user"]["rating"],
                          table["user"]["startCount"], table["user"]["runTime"]),
                         (f"vps-{GAME}", 4, 7, 10))
        self.assertEqual(table["vpinfe"]["alttitle"], "Mine")
        self.assertEqual(self.their_record.call_args.args[1:3], ("owner-id", f"vps-{GAME}"))

    def test_the_score_is_the_reading_as_core_filled_it_kept_to_their_own_entry(self) -> None:
        """A blank took the one player up's initials; a `???` stays `???` - and since it
        is not OWN's, it stays off OWN's send along with the other rank's entry."""
        self.sharing(self.owner, "owner-id")

        self.play(self.owner)

        self.assertEqual(self.only_table()["user"]["score"], _only(READING["entries"][0]))

    def test_an_owner_send_with_no_score_of_theirs_keeps_the_one_vpinplay_holds(self) -> None:
        held = {"rom": "ex", "entries": [{"section": "HIGH SCORES", "rank": 1,
                                          "initials": "OWN", "score": 5000}]}
        self.sharing(self.owner, "owner-id")
        self.their_record.return_value = {"score": held}

        self.play(self.owner, reading={**READING, "entries": [READING["entries"][2]]})

        self.assertEqual(self.only_table()["user"]["score"], held)

    def test_an_owner_score_of_their_own_replaces_the_one_vpinplay_holds(self) -> None:
        self.sharing(self.owner, "owner-id")
        self.their_record.return_value = {"score": {"rom": "ex", "entries": []}}

        self.play(self.owner)

        self.assertEqual(self.only_table()["user"]["score"], _only(READING["entries"][0]))

    def test_an_owner_rating_never_given_here_is_the_one_vpinplay_holds(self) -> None:
        self.sharing(self.owner, "owner-id")
        self.games[GAME]["user"]["rating"] = 0
        self.their_record.return_value = {"rating": 3}

        self.play(self.owner)

        self.assertEqual(self.only_table()["user"]["rating"], 3)

    def test_an_owner_rating_given_here_replaces_the_one_vpinplay_holds(self) -> None:
        self.sharing(self.owner, "owner-id")
        self.their_record.return_value = {"rating": 3}

        self.play(self.owner)

        self.assertEqual(self.only_table()["user"]["rating"], 4)

    def test_a_half_star_goes_as_the_whole_star_below_it(self) -> None:
        self.sharing(self.owner, "owner-id")
        self.games[GAME] = _game(GAME, user={**_game(GAME)["user"], "rating": 7})

        self.play(self.owner)

        self.assertEqual(self.only_table()["user"]["rating"], 3)

    def test_a_lone_half_star_goes_as_one_star_and_does_not_clear_what_vpinplay_holds(self) -> None:
        self.sharing(self.owner, "owner-id")
        self.games[GAME] = _game(GAME, user={**_game(GAME)["user"], "rating": 1})
        self.their_record.return_value = {"rating": 3}

        self.play(self.owner)

        self.assertEqual(self.only_table()["user"]["rating"], 1)

    def test_an_owner_with_nothing_held_and_nothing_to_send_sends_none(self) -> None:
        self.sharing(self.owner, "owner-id")
        self.games[GAME]["user"]["rating"] = 0

        self.play(self.owner, reading=None)

        self.assertEqual((self.only_table()["user"]["score"],
                          self.only_table()["user"]["rating"]), (None, 0))

    def test_a_kept_player_s_game_goes_with_their_own_numbers(self) -> None:
        kept = self.sharing(self.kept().player_id, "jordan")
        player_records.get_records().set_rating(players.get_roster().player(kept), GAME, 4)
        self.their_record.return_value = {"rating": 5, "alttitle": "Theirs", "altvpsid": "t"}

        self.play(kept)

        table = self.only_table()
        self.assertEqual(self.requests_sent()[0]["client"]["userId"], "jordan")
        self.assertEqual((table["user"]["rating"], table["user"]["startCount"],
                          table["user"]["runTime"]), (2, 1, 30))
        self.assertEqual(table["vpinfe"], {"alttitle": "Theirs", "altvpsid": "t"})
        self.assertEqual(table["user"]["score"], _only(READING["entries"][2]),
                         "their own entry, not the other ranks on the machine")
        self.assertEqual(self.their_record.call_args.args[1:3], ("jordan", f"vps-{GAME}"))

    def test_a_rating_they_never_gave_here_is_the_one_vpinplay_holds(self) -> None:
        kept = self.sharing(self.kept().player_id, "jordan")
        self.their_record.return_value = {"rating": 5}

        self.play(kept)

        self.assertEqual(self.only_table()["user"]["rating"], 5)

    def test_a_guest_who_joined_with_a_card_is_sent_their_game(self) -> None:
        guest = self.ok(self.client.post("/players/guests/card", json={
            "card": _2x_card("visitor", "vis", KEY)[1]}), 201)["id"]

        self.play(guest)

        (request,) = self.requests_sent()
        self.assertEqual(request["client"], {"userId": "visitor", "initials": "VIS",
                                             "machineId": KEY})
        self.assertEqual(request["tables"][0]["user"]["startCount"], 1)

    def test_every_sharing_player_up_is_sent_the_game(self) -> None:
        self.sharing(self.owner, "owner-id")
        kept = self.sharing(self.kept().player_id, "jordan")

        self.play(self.owner, kept)

        self.assertEqual(sorted(self.games_sent()),
                         [("jordan", f"vps-{GAME}"), ("owner-id", f"vps-{GAME}")])

    def test_a_player_who_scored_without_being_up_is_sent_the_score_and_no_play(self) -> None:
        kept = self.sharing(self.kept().player_id, "jordan")

        self.play(self.owner, credited=(kept,))

        table = self.only_table()
        self.assertEqual((table["user"]["startCount"], table["user"]["runTime"]), (0, 0))
        self.assertEqual(table["user"]["score"], _only(READING["entries"][2]))
        self.assertEqual(player_records.get_records().game(
            players.get_roster().player(kept), GAME)["play_count"], 0)

    def test_it_goes_off_the_thread_that_ended_the_game(self) -> None:
        self.sharing(self.owner, "owner-id")
        roster = players.get_roster()

        events.emit(events.TABLE_PLAY_RECORDED, game_id=GAME, private=False,
                    up=[roster.player(self.owner).as_payload()], reading=READING,
                    new_entries=[])

        self.send.assert_not_called()
        self.run_later()
        self.send.assert_called_once()

    def held_key(self, player_id: str) -> str:
        return accounts.values(NAME, player_id, self.store)["key"]


class WhatIsNeverSent(SendingCase):
    def test_nothing_goes_while_share_is_off(self) -> None:
        self.fill(self.owner, user_id="owner-id")

        self.play(self.owner)

        self.send.assert_not_called()
        self.assertEqual(self.account(self.owner)["status"], "")

    def test_a_private_game_never_goes(self) -> None:
        self.sharing(self.owner, "owner-id")

        self.play(self.owner, private=True)
        self.games[GAME] = _game(GAME, private=True)
        self.play(self.owner)

        self.send.assert_not_called()
        self.assertEqual(self.account(self.owner)["status"], "")

    def test_a_game_played_before_share_was_on_is_never_sent(self) -> None:
        self.fill(self.owner, user_id="owner-id", key=KEY)
        self.play(self.owner, game_id=GAME)

        self.sharing(self.owner)
        self.play(self.owner, game_id=OTHER)
        said = self.ok(self.act(self.owner, "send_now"))

        self.assertEqual(self.games_sent(), [("owner-id", f"vps-{OTHER}")])
        self.assertEqual(said, {"message": "Nothing waiting to send"})

    def test_an_account_with_no_user_id_is_sent_nothing(self) -> None:
        kept = self.sharing(self.kept().player_id)
        self.sharing(self.owner, "owner-id")
        self.fill(self.owner, user_id="")

        self.play(self.owner, kept)

        self.send.assert_not_called()

    def test_a_game_no_catalog_matched_is_not_sent_and_does_not_wait(self) -> None:
        self.sharing(self.owner, "owner-id")
        self.games[GAME] = _game(GAME, vps_id="")

        self.play(self.owner)

        self.send.assert_not_called()
        self.assertEqual(self.account(self.owner)["status"], "")

    def test_a_rating_on_a_game_never_sent_waits_for_its_next_play(self) -> None:
        kept = self.sharing(self.kept().player_id, "jordan")

        player_records.get_records().set_rating(players.get_roster().player(kept), GAME, 6)
        self.run_later()

        self.send.assert_not_called()


class WhatWaits(SendingCase):
    def fail(self) -> None:  # type: ignore[override]
        self.send.return_value = {"ok": False, "status_code": 503, "response_body": "down"}

    def succeed(self) -> None:
        self.send.return_value = {"ok": True, "status_code": 200, "response_body": ""}

    def acts(self, player_id: str) -> list[str]:
        return [one["key"] for one in self.account(player_id)["acts"]]

    def test_a_failed_send_waits_and_send_now_retries_it(self) -> None:
        self.sharing(self.owner, "owner-id")
        self.fail()
        self.play(self.owner)

        self.assertEqual(self.account(self.owner)["status"], "1 game waiting to send")
        self.assertIn("send_now", self.acts(self.owner))

        self.succeed()
        said = self.ok(self.act(self.owner, "send_now"))

        self.assertEqual(said, {"message": "Sent 1 game"})
        self.assertEqual(self.account(self.owner)["status"], "Sent just now")
        self.assertNotIn("send_now", self.acts(self.owner))
        self.assertEqual(self.games_sent(), [("owner-id", f"vps-{GAME}")] * 2)

    def test_a_server_that_cannot_be_reached_leaves_the_game_waiting(self) -> None:
        self.sharing(self.owner, "owner-id")
        self.send.side_effect = ConnectionError("unreachable")

        self.play(self.owner)

        self.assertEqual(self.account(self.owner)["status"], "1 game waiting to send")

    def test_send_now_that_fails_again_says_how_many_wait(self) -> None:
        self.sharing(self.owner, "owner-id")
        self.fail()
        self.play(self.owner, game_id=GAME)
        self.play(self.owner, game_id=OTHER)

        said = self.ok(self.act(self.owner, "send_now"))

        self.assertEqual(said, {"message": "2 games waiting to send"})

    def test_what_waits_goes_with_the_next_game(self) -> None:
        self.sharing(self.owner, "owner-id")
        self.fail()
        self.play(self.owner, game_id=GAME)
        self.succeed()

        self.play(self.owner, game_id=OTHER)

        last = self.requests_sent()[-1]
        self.assertEqual([one["info"]["vpsId"] for one in last["tables"]],
                         [f"vps-{OTHER}", f"vps-{GAME}"])
        self.assertEqual(self.account(self.owner)["status"], "Sent just now")

    def test_a_game_made_private_while_it_waits_is_never_sent(self) -> None:
        self.sharing(self.owner, "owner-id")
        self.fail()
        self.play(self.owner)
        self.games[GAME] = _game(GAME, private=True)
        self.succeed()

        self.ok(self.act(self.owner, "send_now"))

        self.assertEqual(len(self.requests_sent()), 1, "only the send that failed")
        self.assertEqual(self.account(self.owner)["status"], "")

    def test_nothing_goes_for_a_player_vpinplay_cannot_say_it_holds(self) -> None:
        kept = self.sharing(self.kept().player_id, "jordan")
        self.their_record.return_value = None

        self.play(kept)

        self.send.assert_not_called()
        self.assertEqual(self.account(kept)["status"], "1 game waiting to send")

    def test_nothing_goes_for_an_owner_vpinplay_cannot_say_it_holds(self) -> None:
        self.sharing(self.owner, "owner-id")
        self.their_record.return_value = None

        self.play(self.owner)

        self.send.assert_not_called()
        self.assertEqual(self.account(self.owner)["status"], "1 game waiting to send")

    def test_a_new_user_id_forgets_what_the_old_one_was_sent_and_owes(self) -> None:
        self.sharing(self.owner, "owner-id")
        self.fail()
        self.play(self.owner)

        self.fill(self.owner, user_id="owner-id")
        self.assertEqual(self.account(self.owner)["status"], "1 game waiting to send")
        self.fill(self.owner, user_id="someone-else")

        self.assertEqual(self.account(self.owner)["status"], "")
        self.assertEqual(set(accounts.values(NAME, self.owner, self.store)),
                         {"user_id", "key"})

    def test_a_guest_s_waiting_games_go_when_they_do(self) -> None:
        guest = self.ok(self.client.post("/players/guests/card", json={
            "card": _2x_card("visitor", "vis", KEY)[1]}), 201)["id"]
        self.fail()
        self.play(guest)

        self.assertEqual(self.client.delete(f"/players/{guest}").status_code, 204)

        self.assertEqual(accounts.values(NAME, guest), {})

    def test_the_same_card_after_a_send_is_the_same_guest(self) -> None:
        card = _2x_card("visitor", "vis", KEY)[1]
        guest = self.ok(self.client.post("/players/guests/card", json={"card": card}),
                        201)["id"]
        self.play(guest)
        players.get_roster().set_who_is_up([self.owner])

        again = self.ok(self.client.post("/players/guests/card", json={"card": card}), 201)

        self.assertEqual(again["id"], guest)
        self.assertEqual(len([one for one in players.get_roster().players() if one.guest]),
                         1)


class WhatARatingSends(SendingCase):
    def test_the_owner_s_rating_on_a_game_already_sent_goes_at_once(self) -> None:
        self.sharing(self.owner, "owner-id")
        self.play(self.owner)
        self.games[GAME] = _game(GAME, user={**_game(GAME)["user"], "rating": 2})

        events.emit(events.GAME_RATED, game_id=GAME, rating=2,
                    player=players.get_roster().player(self.owner).as_payload())
        self.run_later()

        self.assertEqual(len(self.requests_sent()), 2)
        self.assertEqual(self.requests_sent()[-1]["tables"][0]["user"]["rating"], 1)

    def test_a_kept_player_s_rating_on_a_game_already_sent_goes_at_once(self) -> None:
        kept = self.sharing(self.kept().player_id, "jordan")
        self.play(kept)

        player_records.get_records().set_rating(players.get_roster().player(kept), GAME, 6)
        self.run_later()

        self.assertEqual(self.requests_sent()[-1]["tables"][0]["user"]["rating"], 3)

    def test_a_rating_after_share_went_off_waits(self) -> None:
        self.sharing(self.owner, "owner-id")
        self.play(self.owner)
        players.get_roster().set_sharing(self.owner, NAME, False)

        events.emit(events.GAME_RATED, game_id=GAME, rating=1,
                    player=players.get_roster().player(self.owner).as_payload())
        self.run_later()

        self.assertEqual(len(self.requests_sent()), 1)


class WhatClearingARatingSends(SendingCase):
    def test_an_owner_clearing_their_rating_sends_the_clear_not_the_one_held(self) -> None:
        self.sharing(self.owner, "owner-id")
        self.play(self.owner)
        self.send.reset_mock()
        self.games[GAME]["user"]["rating"] = 0
        self.their_record.return_value = {"rating": 3}

        events.emit(events.GAME_RATED, game_id=GAME, rating=0,
                    player=players.get_roster().player(self.owner).as_payload())
        self.run_later()

        self.assertEqual(self.only_table()["user"]["rating"], 0)


    def test_a_kept_player_clearing_their_rating_sends_the_clear_not_the_one_held(self) -> None:
        kept = self.sharing(self.kept().player_id, "jordan")
        player = players.get_roster().player(kept)
        player_records.get_records().set_rating(player, GAME, 4)
        self.play(kept)
        self.send.reset_mock()
        self.their_record.return_value = {"rating": 5}

        player_records.get_records().set_rating(player, GAME, 0)
        self.run_later()

        self.assertEqual(self.only_table()["user"]["rating"], 0)


class WhatShareOffDrops(SendingCase):
    def refuse(self) -> None:
        self.send.return_value = {"ok": False, "status_code": 503, "response_body": "down"}

    def share(self, player_id: str, on: bool) -> dict:
        return self.ok(self.client.put(f"/players/{player_id}/accounts/{NAME}/share",
                                       json={"share": on}))

    def test_turning_share_off_drops_what_waits(self) -> None:
        self.sharing(self.owner, "owner-id")
        self.refuse()
        self.play(self.owner)
        self.assertEqual(self.account(self.owner)["status"], "1 game waiting to send")

        said = self.share(self.owner, False)

        self.assertEqual((said["share"], said["status"]), (False, ""))
        self.assertNotIn("send_now", [one["key"] for one in said["acts"]])
        self.send.return_value = {"ok": True, "status_code": 200, "response_body": ""}
        self.assertEqual(self.ok(self.act(self.owner, "send_now")),
                         {"message": "Nothing waiting to send"})
        self.assertEqual(len(self.requests_sent()), 1, "only the send that failed")

    def test_turning_it_on_again_does_not_bring_them_back(self) -> None:
        self.sharing(self.owner, "owner-id")
        self.refuse()
        self.play(self.owner)

        self.share(self.owner, False)
        self.share(self.owner, True)

        self.assertEqual(self.account(self.owner)["status"], "")

    def test_what_was_sent_is_kept(self) -> None:
        self.sharing(self.owner, "owner-id")
        self.play(self.owner)
        self.refuse()
        self.play(self.owner, game_id=OTHER)

        self.share(self.owner, False)

        self.assertEqual(listed_values(accounts.values(NAME, self.owner, self.store)),
                         {"sent": [GAME], "waiting": []})

    def test_a_send_on_the_wire_as_share_goes_off_keeps_nothing_waiting(self) -> None:
        self.sharing(self.owner, "owner-id")

        def refused_after_share_went_off(*_args: object) -> dict:
            players.get_roster().set_sharing(self.owner, NAME, False)
            return {"ok": False, "status_code": 503, "response_body": "down"}

        self.send.side_effect = refused_after_share_went_off
        self.play(self.owner)

        self.assertEqual(self.account(self.owner)["status"], "")

    def test_another_player_s_games_still_wait(self) -> None:
        kept = self.sharing(self.kept().player_id, "jordan")
        self.sharing(self.owner, "owner-id")
        self.refuse()
        self.play(self.owner, kept)

        self.share(self.owner, False)

        self.assertEqual((self.account(self.owner)["status"], self.account(kept)["status"]),
                         ("", "1 game waiting to send"))

    def test_another_extension_s_share_is_not_this_one_s(self) -> None:
        self.sharing(self.owner, "owner-id")
        self.refuse()
        self.play(self.owner)

        players.get_roster().set_sharing(self.owner, "elsewhere", True)
        players.get_roster().set_sharing(self.owner, "elsewhere", False)

        self.assertEqual(self.account(self.owner)["status"], "1 game waiting to send")


def listed_values(held: dict[str, str]) -> dict[str, list[str]]:
    return {book: [one for one in held.get(book, "").split(",") if one]
            for book in ("sent", "waiting")}


class WhatCommunitySays(SendingCase):
    """The Community list's line and menu, from `GET /lists/tables/about`."""

    def about(self) -> dict:
        return self.ok(self.client.get(f"/ext/{NAME}/lists/tables/about"))

    def line(self) -> dict:
        return self.about()["status"]

    def menu(self) -> list[tuple[str, str]]:
        return [(one["label"], one.get("url", "")) for one in self.about()["acts"]]

    def test_the_list_is_declared_under_community(self) -> None:
        (listed,) = [one for one in self.ok(self.client.get("/extensions"))["extensions"]
                     if one["name"] == NAME]

        self.assertEqual([(one["key"], one["area"]) for one in listed["lists"]],
                         [("tables", "community")])

    def test_with_no_account_anywhere_it_says_so_and_links_to_players(self) -> None:
        self.assertEqual(self.line(), {"text": "Not sharing - no player has a VPinPlay "
                                               "account", "to": "players"})
        self.assertEqual(self.menu(), [("Open VPinPlay", "https://www.vpinplay.com")])

    def test_an_account_with_share_off_says_share_is_off(self) -> None:
        self.fill(self.owner, user_id="owner-id")

        self.assertEqual(self.line(), {"text": "Not sharing - Share is off",
                                       "to": "players"})
        self.assertEqual(self.menu(), [
            ("Open VPinPlay", "https://www.vpinplay.com"),
            ("Your Page", "https://www.vpinplay.com/players.html?userid=owner-id")])

    def test_sharing_says_as_whom_in_roster_order(self) -> None:
        kept = self.kept("ABC")
        self.sharing(kept.player_id, "jordan")
        self.sharing(self.owner, "owner-id")
        self.fill(self.kept("XYZ").player_id, user_id="not-sharing")

        self.assertEqual(self.line(), {"text": "Sharing as OWN, ABC"})

    def test_sharing_with_no_initials_to_send_under_says_so(self) -> None:
        kept = players.get_roster().add_player("Jordan", "")
        self.sharing(kept.player_id, "jordan")

        self.assertEqual(self.line(), {"text": "Not sharing - needs initials",
                                       "to": "players"})

    def test_your_page_is_the_owner_s_only(self) -> None:
        self.sharing(self.kept().player_id, "jordan")

        self.assertEqual([label for label, _url in self.menu()], ["Open VPinPlay"])

    def test_send_now_is_offered_while_something_waits_and_sends_everyone_s(self) -> None:
        kept = self.sharing(self.kept().player_id, "jordan")
        self.sharing(self.owner, "owner-id")
        self.assertNotIn("Send Now", [label for label, _url in self.menu()])
        self.send.return_value = {"ok": False, "status_code": 503, "response_body": "down"}
        self.play(self.owner, kept)
        self.assertEqual(self.menu()[0], ("Send Now", ""))

        self.send.return_value = {"ok": True, "status_code": 200, "response_body": ""}
        said = self.ok(self.client.post(f"/ext/{NAME}/lists/tables/about/acts/send_now"))

        self.assertEqual(said, {"message": "Sent 2 games"})
        self.assertEqual(sorted(self.games_sent()[2:]),
                         [("jordan", f"vps-{GAME}"), ("owner-id", f"vps-{GAME}")])
        self.assertNotIn("Send Now", [label for label, _url in self.menu()])

    def test_send_now_that_fails_again_says_how_many_wait(self) -> None:
        self.sharing(self.owner, "owner-id")
        self.send.return_value = {"ok": False, "status_code": 503, "response_body": "down"}
        self.play(self.owner)

        said = self.ok(self.client.post(f"/ext/{NAME}/lists/tables/about/acts/send_now"))

        self.assertEqual(said, {"message": "1 game waiting to send"})

    def test_an_act_it_does_not_offer_is_refused(self) -> None:
        said = self.client.post(f"/ext/{NAME}/lists/tables/about/acts/dance")

        self.assertEqual(said.status_code, 404)


if __name__ == "__main__":
    unittest.main()
