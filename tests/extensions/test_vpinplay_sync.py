"""What VPinPlay is told about a game, built from what core hands over.

Every key below is the service's, asserted by name: a key they require that goes missing
refuses the whole request, and one they do not know is dropped in silence.
"""

from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import MagicMock, patch

import requests

from common.extensions import host
from tests.support import network

# Loaded to import it, then cleared: a loaded VPinPlay listens for games ending.
_loaded = host.Registry()
_loaded.load(host.BUNDLED_DIR / "vpinplay")
_loaded.clear()

import vpinfe_ext_vpinplay  # noqa: E402
from vpinfe_ext_vpinplay import sync  # noqa: E402

GAME: dict[str, Any] = {
    "vps_id": "abcd1234",
    "rom": "afm_113b",
    "manufacturer": "Bally",
    "year": "1995",
    "type": "SS",
    "user": {"rating": 4, "last_played": 1670739887, "play_count": 33,
             "play_time_seconds": 9191,
             # A reading off the hardware, which is a mapping of fields rather than one
             # number - a machine reports several and which is "the" score is its own.
             "score": {"player1": 1234, "grandChampion": 9999}},
    "overrides": {"alt_title": "AFM", "alt_vps_id": "other"},
}
TABLE: dict[str, Any] = {
    "filename": "Attack from Mars.vpx",
    "file_hash": "aaa", "vbs_hash": "bbb", "version": "1.2",
    "release_date": "2020-01-01", "save_date": "2021-02-03", "save_rev": "7",
    "features": {"nfozzy": True, "fleep": False, "ssf": True, "lut": None,
                 "scorbit": True, "fastflips": False, "flexdmd": None},
}


class PayloadTests(unittest.TestCase):
    def test_it_carries_what_the_service_keys_on(self) -> None:
        found = sync.payload_for(GAME, TABLE)

        self.assertEqual(found["info"], {"vpsId": "abcd1234", "rom": "afm_113b"})

    def test_a_game_no_catalog_matched_is_skipped(self) -> None:
        """The service files by catalog id, so a game without one describes nothing it
        can put anywhere."""
        self.assertIsNone(sync.payload_for({**GAME, "vps_id": ""}, TABLE))

    def test_run_time_is_sent_in_their_units(self) -> None:
        """We keep seconds; they take minutes."""
        self.assertEqual(sync.payload_for(GAME, TABLE)["user"]["runTime"], 153)

    def test_a_rating_outside_their_bound_is_clamped(self) -> None:
        """One game outside it fails the whole request, not that game - so a rating
        nobody meant is not worth losing a sync over."""
        wild = {**GAME, "user": {**GAME["user"], "rating": 99}}

        self.assertEqual(sync.payload_for(wild, TABLE)["user"]["rating"], 5)

    def test_the_dates_a_catalog_tells_builds_apart_by(self) -> None:
        """A mod saved today can be a release from years ago."""
        found = sync.payload_for(GAME, TABLE)["vpxFile"]

        self.assertEqual(found["releaseDate"], "2020-01-01")
        self.assertEqual(found["saveDate"], "2021-02-03")
        self.assertEqual(found["saveRev"], "7")

    def test_their_spelling_of_the_features_is_used(self) -> None:
        """Scorbit is the product; the service spells its field Scorebit. A name that
        drifts is dropped in silence by their models rather than refused."""
        found = sync.payload_for(GAME, TABLE)["vpxFile"]

        self.assertTrue(found["detectScorebit"])
        self.assertTrue(found["detectNfozzy"])
        self.assertFalse(found["detectFleep"])

    def test_a_feature_nobody_parsed_is_sent_as_no(self) -> None:
        """Ours is three-valued; theirs is a boolean. Null has to become something and
        false is the only honest choice - claiming a feature nobody looked for is worse
        than under-reporting it."""
        found = sync.payload_for(GAME, TABLE)["vpxFile"]

        self.assertIs(found["detectLUT"], False)
        self.assertIs(found["detectFlex"], False)

    def test_a_game_with_no_table_still_describes_itself(self) -> None:
        """A keyed entry, or one whose file was never imported."""
        found = sync.payload_for(GAME, None)

        self.assertEqual(found["info"]["vpsId"], "abcd1234")
        self.assertEqual(found["vpxFile"]["filename"], "")


    def test_only_a_reading_off_the_hardware_counts_as_a_score(self) -> None:
        """A machine that writes its display rather than its values gives a string, and
        sending that describes nothing their models can file."""
        said = {**GAME, "user": {**GAME["user"], "score": "not-a-reading"}}

        self.assertIsNone(sync.payload_for(said, TABLE)["user"]["score"])
        self.assertEqual(sync.payload_for(GAME, TABLE)["user"]["score"],
                         {"player1": 1234, "grandChampion": 9999})


class LibraryScoreTests(unittest.TestCase):
    """Core publishes a machine's high score table as `high_scores`; the service files
    the reading shape the score parser gives."""

    HIGH_SCORES = {"rom": "afm_113b", "table_id": "t1", "read_at": "2026-09-27T12:00:00Z",
                   "state": "read", "reason": "", "sections": [
                       {"name": "GRAND CHAMPION", "entries": [
                           {"rank": None, "initials": "ABC", "score": 9999, "prefix": "",
                            "suffix": "", "text": "9,999", "new": False}]},
                       {"name": "MARTIAN CHAMPION", "entries": [
                           {"rank": None, "initials": "", "score": 20, "prefix": "",
                            "suffix": " MARTIANS", "text": "20 MARTIANS", "new": True}]},
                       {"name": "RULER", "entries": [
                           {"rank": 1, "initials": "OWN", "score": None, "prefix": "",
                            "suffix": "", "text": "INAUGURATED\n5 JAN, 2024", "new": False}]}]}

    def test_the_table_is_sent_as_a_reading_blanks_blank(self) -> None:
        game = {**GAME, "user": {**GAME["user"], "high_scores": self.HIGH_SCORES}}
        del game["user"]["score"]

        sent = sync.payload_for(sync.from_library(game), TABLE)["user"]["score"]

        self.assertEqual(sent["rom"], "afm_113b")
        self.assertEqual([(one["section"], one["initials"], one["score"])
                          for one in sent["entries"]],
                         [("GRAND CHAMPION", "ABC", 9999), ("MARTIAN CHAMPION", "", 20),
                          ("RULER", "OWN", None)])
        self.assertEqual(sent["entries"][1]["value_suffix"], "MARTIANS")
        self.assertEqual(sent["entries"][2]["extra_lines"], ["INAUGURATED", "5 JAN, 2024"])

    def test_nothing_read_is_no_score(self) -> None:
        game = {**GAME, "user": {**GAME["user"], "high_scores": None}}

        self.assertIsNone(sync.payload_for(sync.from_library(game), TABLE)["user"]["score"])


class EnvelopeTests(unittest.TestCase):
    def test_it_says_which_program_is_speaking(self) -> None:
        found = sync.envelope("u", "ABC", "m1", [], "3.0.0", "2026-09-11T00:00:00Z")

        self.assertEqual(found["source"], {"program": "VPinFE",
                                           "programVersion": "3.0.0"})
        self.assertEqual(found["client"]["initials"], "ABC")


class WireTests(unittest.TestCase):
    """What VPinPlay's request models require. Every other field in them is optional."""

    def setUp(self) -> None:
        self.sent = sync.envelope("u", "ABC", "m" * 64, [sync.payload_for(GAME, TABLE)],
                                  "3.0.0", "2026-09-11T00:00:00Z")

    def assert_carries(self, found: dict, required: set[str]) -> None:
        self.assertLessEqual(required, set(found),
                             f"missing {sorted(required - set(found))}")

    def test_the_request_carries_what_they_require(self) -> None:
        self.assert_carries(self.sent, {"source", "client", "sentAt", "tables"})
        self.assert_carries(self.sent["source"], {"program", "programVersion"})
        self.assert_carries(self.sent["client"], {"userId", "initials", "machineId"})

    def test_each_game_carries_what_they_require(self) -> None:
        for game in (sync.payload_for(GAME, TABLE), sync.payload_for(GAME, None)):
            self.assert_carries(game, {"info", "user", "vpxFile", "vpinfe"})
            self.assert_carries(game["info"], {"vpsId"})
            self.assert_carries(game["vpxFile"],
                                {"filename", "filehash", "version", "vbsHash", "rom"})

    def test_the_machine_id_minted_here_is_the_length_they_take(self) -> None:
        self.assertEqual(len(vpinfe_ext_vpinplay.accounts.new_key()), 64)


class SendTests(unittest.TestCase):
    def setUp(self) -> None:
        network.stubbed(self)

    def answer(self, status_code: int, body: dict) -> MagicMock:
        response = MagicMock(status_code=status_code, ok=200 <= status_code < 400,
                             text=str(body))
        response.json.return_value = body
        return response

    def test_a_refusal_with_a_200_is_not_ok(self) -> None:
        refused = self.answer(200, {"status": "error", "summary": {"errors": 1}})

        with patch.object(requests, "post", return_value=refused):
            found = sync.send("http://vpinplay.test/api/v1/sync", {}, 1)

        self.assertFalse(found["ok"])

    def test_an_accepted_sync_is_ok(self) -> None:
        accepted = self.answer(200, {"status": "ok", "summary": {"errors": 0}})

        with patch.object(requests, "post", return_value=accepted):
            found = sync.send("http://vpinplay.test/api/v1/sync", {}, 1)

        self.assertTrue(found["ok"])


READING = {"rom": "afm_113b", "entries": [{"initials": "OWN", "score": 9000},
                                         {"initials": "ABC", "score": 5000},
                                         {"initials": "XYZ", "score": 4000}]}
MINE = {"rating": 0, "last_played": "2026-09-28T20:00:00Z", "play_count": 1,
        "play_time_seconds": 1800}


class PlayerPayloadTests(unittest.TestCase):
    """A player other than the owner: their numbers here, and what VPinPlay holds for the
    rest, since a send replaces their whole record for the table."""

    def test_their_rating_and_titles_are_kept(self) -> None:
        held = {"rating": 4, "alttitle": "Theirs", "altvpsid": "theirs-id"}

        found = sync.payload_for_player(GAME, TABLE, MINE, held, READING, "ABC")

        self.assertEqual(found["user"]["rating"], 4)
        self.assertEqual(found["vpinfe"], {"alttitle": "Theirs", "altvpsid": "theirs-id"})

    def test_a_rating_they_gave_here_is_theirs(self) -> None:
        found = sync.payload_for_player(GAME, TABLE, {**MINE, "rating": 4}, {"rating": 4},
                                        READING, "ABC")

        self.assertEqual(found["user"]["rating"], 2)

    def test_nothing_of_this_librarys_record_goes_with_it(self) -> None:
        found = sync.payload_for_player(GAME, TABLE, MINE, {}, READING, "ABC")

        self.assertEqual((found["user"]["startCount"], found["user"]["runTime"],
                          found["user"]["lastRun"]), (1, 30, 1790625600))
        self.assertEqual(found["user"]["rating"], 0)
        self.assertEqual(found["vpinfe"], {"alttitle": "", "altvpsid": ""})

    def test_only_this_players_own_entries_go(self) -> None:
        """The reading holds OWN, ABC and XYZ; sent for OWN, only OWN's entry goes -
        never a housemate's, whose entries stay on the machine, not on this player's
        wire record."""
        found = sync.payload_for_player(GAME, TABLE, MINE, {}, READING, "own")

        self.assertEqual(found["user"]["score"],
                         {"rom": "afm_113b", "entries": [{"initials": "OWN", "score": 9000}]})

    def test_a_player_with_no_entry_gets_what_is_already_held(self) -> None:
        held = {"score": {"rom": "afm_113b", "entries": [{"initials": "DEF", "score": 1}]}}

        found = sync.payload_for_player(GAME, TABLE, MINE, held, READING, "DEF")

        self.assertEqual(found["user"]["score"], held["score"])

    def test_a_player_with_no_entry_and_nothing_held_sends_no_score(self) -> None:
        found = sync.payload_for_player(GAME, TABLE, MINE, {}, READING, "DEF")

        self.assertIsNone(found["user"]["score"])

    def test_a_reading_of_one_number_is_theirs_only_when_it_was_credited_to_them(self) -> None:
        one_number = {"rom": "x", "value": 1234}
        held = {"score": {"rom": "x", "value": 99}}

        credited = sync.payload_for_player(GAME, TABLE, MINE, held, one_number, "ABC",
                                           credited=True)
        not_theirs = sync.payload_for_player(GAME, TABLE, MINE, held, one_number, "ABC")

        self.assertEqual(credited["user"]["score"], one_number)
        self.assertEqual(not_theirs["user"]["score"], held["score"])


class TheirRecordTests(unittest.TestCase):
    def setUp(self) -> None:
        network.stubbed(self)

    WHERE = "https://vpinplay.test/api/v1/sync"

    def ask(self, **answer: Any) -> tuple[object, MagicMock]:
        with patch.object(requests, "get", **answer) as get:
            found = sync.their_record(self.WHERE, "some one", "abcd1234", 1)
        return found, get

    def test_it_asks_for_that_user_and_table(self) -> None:
        found, get = self.ask(return_value=MagicMock(status_code=200, ok=True,
                                                     json=lambda: {"rating": 3}))

        self.assertEqual(found, {"rating": 3})
        self.assertEqual(get.call_args.args[0],
                         "https://vpinplay.test/api/v1/users/some%20one/tables/abcd1234")

    def test_a_record_they_do_not_hold_is_empty(self) -> None:
        found, _get = self.ask(return_value=MagicMock(status_code=404, ok=False))

        self.assertEqual(found, {})

    def test_a_server_that_cannot_answer_is_not_read_as_empty(self) -> None:
        down, _get = self.ask(side_effect=requests.ConnectionError("down"))
        failing, _get = self.ask(return_value=MagicMock(status_code=500, ok=False))

        self.assertIsNone(down)
        self.assertIsNone(failing)


class AddressTests(unittest.TestCase):
    def test_all_four_ways_to_type_it_end_at_the_same_place(self) -> None:
        typed = ["pinball.lan", "pinball.lan:8888", "http://pinball.lan:8888/",
                 "http://pinball.lan:8888/api/v1", "http://pinball.lan:8888/api/v1/sync"]

        self.assertEqual({sync.api_root(one) for one in typed[2:]},
                         {"http://pinball.lan:8888/api/v1"})
        self.assertEqual([sync.endpoint_for(one) for one in typed[:2]],
                         ["http://pinball.lan/api/v1/sync",
                          "http://pinball.lan:8888/api/v1/sync"])
        self.assertEqual(sync.endpoint_for(typed[4]), "http://pinball.lan:8888/api/v1/sync")

    def test_a_scheme_typed_is_kept(self) -> None:
        self.assertEqual(sync.api_root("https://api.example:8888"),
                         "https://api.example:8888/api/v1")

    def test_a_blank_or_unusable_address_says_so_in_words(self) -> None:
        with self.assertRaises(ValueError) as blank:
            sync.api_root("  ")
        with self.assertRaises(ValueError) as odd:
            sync.api_root("http://")

        self.assertEqual([str(blank.exception), str(odd.exception)],
                         ["The server address is blank",
                          "http:// is not an address VPinPlay can reach"])

    def test_an_address_that_cannot_be_parsed_is_said_as_unreachable(self) -> None:
        said = []
        for typed in ("[::1", "pinball.lan:abc"):
            with self.assertRaises(ValueError) as raised:
                sync.api_root(typed)
            said.append(str(raised.exception))

        self.assertEqual(said, ["[::1 is not an address VPinPlay can reach",
                                "pinball.lan:abc is not an address VPinPlay can reach"])

    def test_a_query_or_capital_letters_do_not_double_the_path(self) -> None:
        self.assertEqual([sync.api_root("http://pinball.lan/api/v1?x=1"),
                          sync.api_root("http://pinball.lan/api/v1/sync?x=1#top"),
                          sync.api_root("HTTP://PINBALL.LAN/API/V1")],
                         ["http://pinball.lan/api/v1", "http://pinball.lan/api/v1",
                          "http://PINBALL.LAN/api/v1"])


if __name__ == "__main__":
    unittest.main()
