"""Who plays on an install, and who the next game counts for.

Each test builds a fresh roster on a temporary file. A new `Roster` on the same path is
what a restart looks like: the kept players come back, the guests and who is up do not.
"""

from __future__ import annotations

import json
import unittest
from configparser import ConfigParser
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from common import events, service_errors
from common.extensions import store as extension_store
from common.players import player_pictures
from common.players.players import OWNER_MIGRATION, Player, Roster, names_nobody, same_initials
from tests.support.pictures import BLUE, RED, animation, banded, png


def _config(initials: str | None = None) -> ConfigParser:
    parser = ConfigParser()
    parser.add_section("vpinplay")
    if initials is not None:
        parser.set("vpinplay", "initials", initials)
    return parser


SCHEMA_1_FILE = {
    "schema": 1,
    "players": [{"install_id": "Aaaa111111", "display_name": "basement cab",
                 "roles": ["player"], "address": "testclient",
                 "first_seen": "2026-08-12T02:18:46Z", "last_seen": "2026-08-12T02:18:46Z"}],
}


class _RosterCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "players.json"
        self.roster = Roster(self.path)

    def _file(self) -> dict:
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _restart(self) -> Roster:
        self.roster = Roster(self.path)
        return self.roster


class SameInitialsTests(unittest.TestCase):
    def test_case_and_padding_do_not_matter(self) -> None:
        self.assertTrue(same_initials("OWN", "own"))
        self.assertTrue(same_initials(" own ", "OWN"))
        self.assertTrue(same_initials("Jd", "jD "))

    def test_different_initials_differ(self) -> None:
        self.assertFalse(same_initials("OWN", "OWM"))
        self.assertFalse(same_initials("OW", "OWW"))

    def test_blank_matches_nothing_not_even_blank(self) -> None:
        self.assertFalse(same_initials("", ""))
        self.assertFalse(same_initials("  ", None))
        self.assertFalse(same_initials("", "OWN"))

    def test_the_duplicate_check_and_attribution_agree_on_case(self) -> None:
        with TemporaryDirectory() as folder:
            roster = Roster(Path(folder) / "players.json")
            roster.add_player("Jordan", "OWN")

            with self.assertRaises(service_errors.RefusedError):
                roster.add_player("Other", "own")
            found = roster.whose(" own")
            assert found is not None
            self.assertEqual(found.name, "Jordan")


class FileTests(_RosterCase):
    def test_no_file_is_an_empty_roster(self) -> None:
        self.assertEqual(self.roster.players(), [])
        self.assertEqual(self.roster.up(), [])
        self.assertIsNone(self.roster.owner())

    def test_a_schema_1_file_is_not_ours_and_the_first_write_replaces_it(self) -> None:
        self.path.write_text(json.dumps(SCHEMA_1_FILE), encoding="utf-8")

        self.assertEqual(self.roster.players(), [], "an install is not a player")

        owner = self.roster.ensure_owner(_config("OWN"))
        assert owner is not None

        stored = self._file()
        self.assertEqual(stored["schema"], 2)
        self.assertEqual(stored["players"], [
            {"id": owner.player_id, "name": "", "initials": "OWN", "owner": True}])
        self.assertNotIn("install_id", json.dumps(stored))

    def test_a_file_with_no_schema_is_not_ours(self) -> None:
        self.path.write_text(json.dumps({"players": [{"id": "Pppp111111", "owner": True}]}),
                             encoding="utf-8")

        self.assertEqual(self.roster.players(), [])

    def test_an_unreadable_file_is_an_empty_roster(self) -> None:
        self.path.write_text("{not json", encoding="utf-8")

        with self.assertLogs("vpinfe.common.players.players", "WARNING"):
            self.assertEqual(self.roster.players(), [])

    def test_kept_players_come_back_after_a_restart(self) -> None:
        self.roster.ensure_owner(_config("OWN"))
        added = self.roster.add_player("Alex", "abc")

        again = self._restart()

        self.assertEqual([(p.name, p.initials, p.owner) for p in again.players()],
                         [("", "OWN", True), ("Alex", "ABC", False)])
        self.assertEqual(again.get(added.player_id), added)

    def test_what_a_newer_build_wrote_is_carried_through(self) -> None:
        self.path.write_text(json.dumps({
            "schema": 3, "migrations": [OWNER_MIGRATION], "later": {"kept": True},
            "players": [{"id": "Pppp111111", "name": "Jordan", "initials": "OWN",
                         "owner": True, "share": {"vpinplay": False}}]}), encoding="utf-8")

        self.roster.add_player("Alex", "ABC")

        stored = self._file()
        self.assertEqual(stored["schema"], 3, "never stamped down")
        self.assertEqual(stored["later"], {"kept": True})
        self.assertEqual(stored["players"][0]["share"], {"vpinplay": False})

    def test_a_hand_edit_with_two_owners_keeps_the_first(self) -> None:
        self.path.write_text(json.dumps({"schema": 2, "players": [
            {"id": "Pppp111111", "initials": "OWN", "owner": True},
            {"id": "Pppp222222", "initials": "ABC", "owner": True}]}), encoding="utf-8")

        self.assertEqual([p.owner for p in self.roster.players()], [True, False])
        self.roster.remove("Pppp222222")
        self.assertEqual([p.player_id for p in self.roster.players()], ["Pppp111111"])

    def test_the_owner_is_listed_first_wherever_it_is_filed(self) -> None:
        self.path.write_text(json.dumps({"schema": 2, "players": [
            {"id": "Pppp222222", "initials": "ABC"},
            {"id": "Pppp111111", "initials": "OWN", "owner": True}]}), encoding="utf-8")

        self.assertEqual([p.player_id for p in self.roster.players()],
                         ["Pppp111111", "Pppp222222"])


class OwnerTests(_RosterCase):
    def test_the_owner_takes_the_2x_initials(self) -> None:
        owner = self.roster.ensure_owner(_config(" own "))
        assert owner is not None

        self.assertTrue(owner.owner)
        self.assertEqual(owner.initials, "OWN")
        self.assertEqual(self.roster.owner(), owner)
        self.assertEqual(self._file()["migrations"], [OWNER_MIGRATION])

    def test_shorter_2x_initials_are_kept_as_they_are(self) -> None:
        owner = self.roster.ensure_owner(_config("OW"))
        assert owner is not None

        self.assertEqual(owner.initials, "OW")

    def test_no_initials_anywhere_makes_an_owner_with_none(self) -> None:
        owner = self.roster.ensure_owner(_config())
        assert owner is not None

        self.assertEqual(owner.initials, "")
        self.assertEqual(self.roster.up(), [owner])

    def test_it_runs_once(self) -> None:
        first = self.roster.ensure_owner(_config("OWN"))
        assert first is not None

        self.assertIsNone(self._restart().ensure_owner(_config("OWN")))
        self.assertEqual(self.roster.players(), [first])

    def test_initials_cleared_afterwards_are_not_brought_back(self) -> None:
        owner = self.roster.ensure_owner(_config("OWN"))
        assert owner is not None
        self.roster.update_player(owner.player_id, initials="")

        self._restart().ensure_owner(_config("OWN"))

        kept = self.roster.owner()
        assert kept is not None
        self.assertEqual(kept.initials, "")

    def test_an_owner_lost_after_the_first_start_comes_back_without_2x_initials(self) -> None:
        self.roster.ensure_owner(_config("OWN"))
        stored = self._file()
        stored["players"] = []
        self.path.write_text(json.dumps(stored), encoding="utf-8")

        owner = self._restart().ensure_owner(_config("OWN"))
        assert owner is not None

        self.assertEqual(owner.initials, "")
        self.assertEqual(self._file()["migrations"], [OWNER_MIGRATION])

    def test_the_vpinplay_extensions_own_store_is_never_read(self) -> None:
        held = extension_store.ExtensionStore(Path(self.tmp.name))
        held.set_setting("vpinplay", "initials", "ZZZ")

        with mock.patch.object(extension_store, "_store", held):
            owner = self.roster.ensure_owner(_config())
            assert owner is not None

        self.assertEqual(owner.initials, "")

    def test_the_owner_cannot_be_removed(self) -> None:
        owner = self.roster.ensure_owner(_config("OWN"))
        assert owner is not None

        with self.assertRaises(service_errors.RefusedError):
            self.roster.remove(owner.player_id)
        self.assertEqual(self.roster.players(), [owner])


class KeptPlayerTests(_RosterCase):
    def setUp(self) -> None:
        super().setUp()
        owner = self.roster.ensure_owner(_config("OWN"))
        assert owner is not None
        self.owner = owner

    def test_initials_are_held_upper_case(self) -> None:
        self.assertEqual(self.roster.add_player(" Alex ", " abc ").initials, "ABC")
        self.assertEqual(self.roster.players()[-1].name, "Alex")

    def test_initials_that_are_not_three_characters_are_refused(self) -> None:
        for wrong in ("AB", "ABCD", "A"):
            with self.subTest(wrong=wrong), self.assertRaises(service_errors.RefusedError):
                self.roster.add_player("Alex", wrong)
        self.assertEqual(len(self.roster.players()), 1, "nothing was written")

    def test_initials_another_kept_player_has_are_refused_with_who(self) -> None:
        with self.assertRaises(service_errors.RefusedError) as caught:
            self.roster.add_player("Alex", "own")

        self.assertIn("OWN", str(caught.exception), "the owner has no name, so it says whose")

    def test_the_refusal_names_the_player_by_name(self) -> None:
        self.roster.add_player("Alex", "ABC")

        with self.assertRaises(service_errors.RefusedError) as caught:
            self.roster.add_player("Other", "abc")

        self.assertIn("Alex", str(caught.exception))

    def test_a_player_with_no_initials_clashes_with_nobody(self) -> None:
        self.roster.add_player("Alex")
        self.roster.add_player("Sam")

        self.assertEqual(len(self.roster.players()), 3)

    def test_a_rename_leaves_shorter_held_initials_alone(self) -> None:
        self.path.unlink()
        owner = self._restart().ensure_owner(_config("OW"))
        assert owner is not None

        renamed = self.roster.update_player(owner.player_id, name="Jordan", initials="ow")

        self.assertEqual((renamed.name, renamed.initials), ("Jordan", "OW"))

    def test_changing_initials_is_checked_like_adding(self) -> None:
        alex = self.roster.add_player("Alex", "ABC")

        with self.assertRaises(service_errors.RefusedError):
            self.roster.update_player(alex.player_id, initials="AB")
        with self.assertRaises(service_errors.RefusedError):
            self.roster.update_player(alex.player_id, initials="Own")
        self.assertEqual(self.roster.update_player(alex.player_id, initials="xyz").initials,
                         "XYZ")

    def test_a_kept_player_is_removed(self) -> None:
        alex = self.roster.add_player("Alex", "ABC")

        self.assertEqual(self.roster.remove(alex.player_id), alex)

        self.assertEqual(self._restart().players(), [self.owner])

    def test_an_unknown_id_is_not_found(self) -> None:
        for call in (lambda: self.roster.remove("nobody"),
                     lambda: self.roster.update_player("nobody", name="X"),
                     lambda: self.roster.set_up("nobody")):
            with self.assertRaises(service_errors.NotFoundError):
                call()

    def test_adding_a_kept_player_does_not_put_them_up(self) -> None:
        self.roster.add_player("Alex", "ABC")

        self.assertEqual(self.roster.up(), [self.owner])


class GuestTests(_RosterCase):
    def setUp(self) -> None:
        super().setUp()
        owner = self.roster.ensure_owner(_config("OWN"))
        assert owner is not None
        self.owner = owner

    def test_a_guest_is_never_written(self) -> None:
        guest = self.roster.add_guest("abc")

        self.assertTrue(guest.guest)
        self.assertEqual(guest.initials, "ABC")
        self.assertNotIn(guest.player_id, json.dumps(self._file()))
        self.assertEqual(self._restart().players(), [self.owner], "gone when VPinFE closes")

    def test_a_guest_needs_initials(self) -> None:
        for wrong in ("", "  ", "AB"):
            with self.subTest(wrong=wrong), self.assertRaises(service_errors.RefusedError):
                self.roster.add_guest(wrong)

    def test_joining_makes_the_guest_up_alone(self) -> None:
        guest = self.roster.add_guest("ABC")

        self.assertEqual(self.roster.up(), [guest])
        self.assertEqual(self.roster.one_up(), guest)

    def test_the_owner_goes_back_up_for_a_game_together(self) -> None:
        guest = self.roster.add_guest("ABC")

        self.roster.set_up(self.owner.player_id)

        self.assertEqual(self.roster.up(), [self.owner, guest])
        self.assertIsNone(self.roster.one_up(), "with several up, nobody is the one")

    def test_when_the_last_guest_leaves_up_returns_to_the_owner(self) -> None:
        guest = self.roster.add_guest("ABC")

        self.roster.remove(guest.player_id)

        self.assertEqual(self.roster.up(), [self.owner])

    def test_a_kept_player_up_beside_the_last_guest_stays_up_when_they_leave(self) -> None:
        """Kid and a visitor are up and the visitor leaves: Kid's next game is Kid's."""
        kid = self.roster.add_player("Kid", "KID")
        visitor = self.roster.add_guest("ABC")
        self.roster.set_up(kid.player_id)

        self.roster.remove(visitor.player_id)

        self.assertEqual(self.roster.up(), [kid])

    def test_a_guest_who_was_not_up_leaving_changes_nobody_up(self) -> None:
        kid = self.roster.add_player("Kid", "KID")
        visitor = self.roster.add_guest("ABC")
        self.roster.set_who_is_up([kid.player_id])

        self.roster.remove(visitor.player_id)

        self.assertEqual(self.roster.up(), [kid])

    def test_a_guest_leaving_while_another_stays_keeps_the_other_up(self) -> None:
        first = self.roster.add_guest("ABC")
        second = self.roster.add_guest("XYZ")
        self.roster.set_up(first.player_id)

        self.roster.remove(first.player_id)

        self.assertEqual(self.roster.up(), [second])

    def test_a_guest_with_a_kept_players_initials_joins_and_both_are_marked(self) -> None:
        guest = self.roster.add_guest("own")

        rows = {row["id"]: row for row in self.roster.state()["players"]}
        self.assertEqual(rows[guest.player_id]["shares_initials_with"], [self.owner.player_id])
        self.assertEqual(rows[self.owner.player_id]["shares_initials_with"], [guest.player_id])
        self.assertIsNone(self.roster.whose("OWN"), "shared initials name nobody")

        self.roster.remove(guest.player_id)

        self.assertEqual(self.roster.whose("own"), self.owner)

    def test_a_guests_initials_do_not_stop_a_kept_player_either(self) -> None:
        self.roster.add_guest("ABC")

        self.assertEqual(self.roster.add_player("Alex", "ABC").initials, "ABC")


class UpTests(_RosterCase):
    def setUp(self) -> None:
        super().setUp()
        owner = self.roster.ensure_owner(_config("OWN"))
        assert owner is not None
        self.owner = owner
        self.alex = self.roster.add_player("Alex", "ABC")

    def test_the_owner_is_up_by_default(self) -> None:
        self.assertEqual(self.roster.up(), [self.owner])
        self.assertEqual(self.roster.one_up(), self.owner)

    def test_up_is_per_process_and_not_written(self) -> None:
        self.roster.set_up(self.alex.player_id)
        self.roster.set_up(self.owner.player_id, False)

        self.assertEqual(self.roster.up(), [self.alex])
        self.assertEqual(self._restart().up(), [self.owner])

    def test_taking_down_the_last_one_up_leaves_the_owner_up(self) -> None:
        self.roster.set_up(self.alex.player_id)
        self.roster.set_up(self.owner.player_id, False)

        self.roster.set_up(self.alex.player_id, False)

        self.assertEqual(self.roster.up(), [self.owner])

    def test_removing_a_player_who_is_up_takes_them_down(self) -> None:
        self.roster.set_up(self.alex.player_id)
        self.roster.set_up(self.owner.player_id, False)

        self.roster.remove(self.alex.player_id)

        self.assertEqual(self.roster.up(), [self.owner])

    def test_the_whole_set_can_be_named_at_once(self) -> None:
        guest = self.roster.add_guest("XYZ")

        up = self.roster.set_who_is_up([guest.player_id, self.alex.player_id,
                                        guest.player_id])

        self.assertEqual(up, [self.alex, guest], "in roster order, each once")
        self.assertEqual(self.roster.up(), [self.alex, guest])

    def test_naming_nobody_up_leaves_the_owner_up(self) -> None:
        self.roster.set_who_is_up([self.alex.player_id])

        self.assertEqual(self.roster.set_who_is_up([]), [self.owner])

    def test_a_set_naming_someone_unknown_changes_nothing(self) -> None:
        with self.assertRaises(service_errors.NotFoundError):
            self.roster.set_who_is_up([self.alex.player_id, "Nobody0000"])

        self.assertEqual(self.roster.up(), [self.owner])

    def test_whose_is_by_initials_up_or_not(self) -> None:
        self.assertEqual(self.roster.whose("abc"), self.alex)
        self.assertIsNone(self.roster.whose(""))
        self.assertIsNone(self.roster.whose("ZZZ"))


class WhoseScoreTests(_RosterCase):
    def setUp(self) -> None:
        super().setUp()
        owner = self.roster.ensure_owner(_config("OWN"))
        assert owner is not None
        self.owner = owner
        self.alex = self.roster.add_player("Alex", "ABC")

    def test_a_score_goes_by_its_initials_up_or_not(self) -> None:
        self.assertEqual(self.roster.whose_score(" abc", [self.owner]), self.alex)

    def test_initials_nobody_has_go_to_nobody_even_with_one_up(self) -> None:
        self.assertIsNone(self.roster.whose_score("ZZZ", [self.owner]))

    def test_no_initials_go_to_the_one_player_up(self) -> None:
        for none in ("", "  ", "???", "?"):
            with self.subTest(initials=none):
                self.assertEqual(self.roster.whose_score(none, [self.alex]), self.alex)

    def test_no_initials_go_to_nobody_with_several_up(self) -> None:
        self.assertIsNone(self.roster.whose_score("", [self.owner, self.alex]))
        self.assertIsNone(self.roster.whose_score("???", [self.owner, self.alex]))

    def test_initials_two_players_share_go_to_neither(self) -> None:
        guest = self.roster.add_guest("ABC")

        self.assertIsNone(self.roster.whose_score("ABC", [guest]))

    def test_only_question_marks_name_nobody(self) -> None:
        self.assertTrue(names_nobody(" ??? "))
        self.assertFalse(names_nobody("A?C"))


class ChangedEventTests(_RosterCase):
    def setUp(self) -> None:
        super().setUp()
        self.heard: list[dict] = []
        handler = events.subscribe(events.PLAYERS_CHANGED,
                                   lambda **payload: self.heard.append(payload["state"]))
        self.addCleanup(events.unsubscribe, events.PLAYERS_CHANGED, handler)

    def test_every_change_carries_the_whole_roster(self) -> None:
        owner = self.roster.ensure_owner(_config("OWN"))
        assert owner is not None
        guest = self.roster.add_guest("ABC")

        self.assertEqual(len(self.heard), 2)
        self.assertEqual(self.heard[-1], self.roster.state())
        self.assertEqual(self.heard[-1]["players"], [
            {"id": owner.player_id, "name": "", "initials": "OWN", "owner": True,
             "guest": False, "up": False, "picture_version": None,
             "shares_initials_with": []},
            {"id": guest.player_id, "name": "", "initials": "ABC", "owner": False,
             "guest": True, "up": True, "picture_version": None,
             "shares_initials_with": []},
        ])

    def test_each_kind_of_change_is_announced(self) -> None:
        owner = self.roster.ensure_owner(_config("OWN"))
        assert owner is not None
        alex = self.roster.add_player("Alex", "ABC")
        self.roster.update_player(alex.player_id, name="Alexandra")
        self.roster.set_up(alex.player_id)
        guest = self.roster.add_guest("XYZ")
        self.roster.remove(guest.player_id)
        self.roster.remove(alex.player_id)

        self.assertEqual(len(self.heard), 7)
        self.assertEqual([row["id"] for row in self.heard[-1]["players"]], [owner.player_id])

    def test_nothing_that_changes_nothing_is_announced(self) -> None:
        owner = self.roster.ensure_owner(_config("OWN"))
        assert owner is not None
        self.heard.clear()

        self.roster.ensure_owner(_config("OWN"))
        self.roster.set_up(owner.player_id)
        self.roster.set_up(owner.player_id, False)
        self.roster.update_player(owner.player_id, initials="own")

        self.assertEqual(self.heard, [])

    def test_a_refused_change_is_not_announced(self) -> None:
        self.roster.ensure_owner(_config("OWN"))
        self.heard.clear()

        with self.assertRaises(service_errors.RefusedError):
            self.roster.add_player("Alex", "OWN")

        self.assertEqual(self.heard, [])


class ShareTests(_RosterCase):
    """Whether a player's account with an extension shares: core's, per account."""

    def setUp(self) -> None:
        super().setUp()
        owner = self.roster.ensure_owner(_config("OWN"))
        assert owner is not None
        self.owner = owner

    def test_nobody_shares_until_somebody_says_so(self) -> None:
        self.assertFalse(self.roster.sharing(self.owner.player_id, "sample"))
        self.assertNotIn("share", self._file()["players"][0])

    def test_a_kept_players_share_survives_a_restart(self) -> None:
        self.roster.set_sharing(self.owner.player_id, "sample", True)

        self.assertTrue(self._restart().sharing(self.owner.player_id, "sample"))
        self.assertEqual(self._file()["players"][0]["share"], {"sample": True})

    def test_share_is_per_extension(self) -> None:
        self.roster.set_sharing(self.owner.player_id, "sample", True)

        self.assertFalse(self.roster.sharing(self.owner.player_id, "elsewhere"))

    def test_turned_off_it_says_so(self) -> None:
        self.roster.set_sharing(self.owner.player_id, "sample", True)
        self.roster.set_sharing(self.owner.player_id, "sample", False)

        self.assertFalse(self.roster.sharing(self.owner.player_id, "sample"))
        self.assertEqual(self._file()["players"][0]["share"], {"sample": False})

    def test_a_guests_share_is_never_written(self) -> None:
        guest = self.roster.add_guest("ABC")
        before = self.path.read_bytes()

        self.roster.set_sharing(guest.player_id, "sample", True)

        self.assertTrue(self.roster.sharing(guest.player_id, "sample"))
        self.assertEqual(self.path.read_bytes(), before)

    def test_someone_nobody_has_is_refused(self) -> None:
        with self.assertRaises(service_errors.NotFoundError):
            self.roster.set_sharing("Nobody0000", "sample", True)

    def test_a_share_that_moves_is_announced_with_whose_it_is(self) -> None:
        heard = self._hear()
        guest = self.roster.add_guest("ABC")

        self.roster.set_sharing(self.owner.player_id, "sample", True)
        self.roster.set_sharing(guest.player_id, "sample", True)
        self.roster.set_sharing(self.owner.player_id, "sample", False)

        self.assertEqual([(one["player"]["id"], one["extension"], one["share"])
                          for one in heard],
                         [(self.owner.player_id, "sample", True),
                          (guest.player_id, "sample", True),
                          (self.owner.player_id, "sample", False)])
        self.assertEqual(heard[1]["player"], {"id": guest.player_id, "name": "",
                                              "initials": "ABC", "owner": False,
                                              "guest": True})

    def test_a_share_set_to_what_it_was_is_not_announced(self) -> None:
        heard = self._hear()

        self.roster.set_sharing(self.owner.player_id, "sample", False)
        self.roster.set_sharing(self.owner.player_id, "sample", True)
        self.roster.set_sharing(self.owner.player_id, "sample", True)

        self.assertEqual([one["share"] for one in heard], [True])

    def _hear(self) -> list[dict]:
        heard: list[dict] = []
        handler = events.subscribe(events.ACCOUNT_SHARE_CHANGED,
                                   lambda **payload: heard.append(payload))
        self.addCleanup(events.unsubscribe, events.ACCOUNT_SHARE_CHANGED, handler)
        return heard


class CardGuestTests(_RosterCase):
    """A guest joining with a card keeps the card's initials, and shares."""

    def setUp(self) -> None:
        super().setUp()
        owner = self.roster.ensure_owner(_config("OWN"))
        assert owner is not None
        self.owner = owner

    def test_a_card_s_initials_are_one_to_three_characters_as_they_are(self) -> None:
        for initials in ("a", "ab", " abc "):
            guest = self.roster.add_guest_with_card("sample", initials)
            self.assertEqual(guest.initials, initials.strip().upper())

    def test_none_or_more_than_three_are_refused(self) -> None:
        for initials in ("", "ABCD"):
            with self.assertRaises(service_errors.RefusedError, msg=initials):
                self.roster.add_guest_with_card("sample", initials)

    def test_they_join_up_alone_sharing_with_the_card_s_extension(self) -> None:
        guest = self.roster.add_guest_with_card("sample", "AB")

        self.assertEqual(self.roster.up(), [guest])
        self.assertEqual((self.roster.sharing(guest.player_id, "sample"),
                          self.roster.sharing(guest.player_id, "elsewhere")), (True, False))


def _near(got: tuple[int, ...], want: tuple[int, ...]) -> bool:
    """Whether a pixel is a color WebP would give back for `want`."""
    return all(abs(one - other) < 12 for one, other in zip(got, want, strict=True))


class PictureTests(_RosterCase):
    """A kept player's picture: the centre square as WebP, in a folder beside the roster."""

    def setUp(self) -> None:
        super().setUp()
        self.roster.ensure_owner(_config("OWN"))
        self.alex = self.roster.add_player("Alex", "ABC")
        self.heard: list[dict] = []
        handler = events.subscribe(events.PLAYERS_CHANGED,
                                   lambda **payload: self.heard.append(payload["state"]))
        self.addCleanup(events.unsubscribe, events.PLAYERS_CHANGED, handler)

    def _stored(self, player: Player | None = None):
        from PIL import Image

        folder = self.roster.pictures_dir
        found = sorted(folder.iterdir()) if folder.exists() else []
        self.assertEqual([one.name for one in found],
                         [f"{(player or self.alex).player_id}.webp"])
        return Image.open(found[0])

    def _version(self, player: Player | None = None) -> str | None:
        return self.roster.player_state((player or self.alex).player_id)["picture_version"]

    def test_a_wide_picture_is_cropped_to_its_centre_square_as_webp(self) -> None:
        self.roster.set_picture(self.alex.player_id, "face.png", banded(600, 300))

        with self._stored() as stored:
            self.assertEqual((stored.format, stored.size), ("WEBP", (300, 300)))
            self.assertTrue(_near(stored.convert("RGB").getpixel((150, 150)), BLUE))
            self.assertTrue(_near(stored.convert("RGB").getpixel((5, 5)), BLUE))

    def test_a_large_picture_is_scaled_to_at_most_1024_and_a_small_one_is_not_enlarged(
            self) -> None:
        self.roster.set_picture(self.alex.player_id, "big.jpg", png(3000, 2000, fmt="JPEG"))
        with self._stored() as big:
            self.assertEqual(big.size, (1024, 1024))

        self.roster.set_picture(self.alex.player_id, "small.png", png(100, 60))
        with self._stored() as small:
            self.assertEqual(small.size, (60, 60))

    def test_an_animation_keeps_its_first_frame(self) -> None:
        self.roster.set_picture(self.alex.player_id, "spin.gif", animation())

        with self._stored() as stored:
            self.assertTrue(_near(stored.convert("RGB").getpixel((10, 10)), RED))

    def test_a_replacement_leaves_one_file_whatever_the_upload_was(self) -> None:
        self.roster.set_picture(self.alex.player_id, "one.png", png(50, 50))
        self.roster.set_picture(self.alex.player_id, "two.jpeg", png(70, 70, BLUE, "JPEG"))

        with self._stored() as stored:
            self.assertEqual(stored.size, (70, 70))

    def test_the_version_is_none_without_a_picture_and_changes_on_a_replacement(self) -> None:
        self.assertIsNone(self._version())

        self.roster.set_picture(self.alex.player_id, "one.png", png(50, 50))
        first = self._version()
        self.roster.set_picture(self.alex.player_id, "two.png", png(60, 60, BLUE))

        self.assertTrue(first)
        self.assertNotEqual(self._version(), first)

    def test_a_change_is_announced_with_the_version_in_the_roster(self) -> None:
        before = len(self.heard)
        self.roster.set_picture(self.alex.player_id, "one.png", png(50, 50))
        self.roster.clear_picture(self.alex.player_id)
        self.roster.clear_picture(self.alex.player_id)

        heard = [next(one for one in state["players"] if one["id"] == self.alex.player_id)
                 ["picture_version"] for state in self.heard[before:]]
        self.assertEqual(len(heard), 2)
        self.assertTrue(heard[0])
        self.assertIsNone(heard[1])

    def test_clearing_deletes_the_file(self) -> None:
        self.roster.set_picture(self.alex.player_id, "one.png", png(50, 50))

        self.roster.clear_picture(self.alex.player_id)

        self.assertEqual(list(self.roster.pictures_dir.iterdir()), [])
        self.assertIsNone(self._version())

    def test_the_picture_is_served_at_the_size_asked_for_with_the_version(self) -> None:
        self.roster.set_picture(self.alex.player_id, "big.png", png(900, 900))

        original = self.roster.picture(self.alex.player_id)
        thumbnail = self.roster.picture(self.alex.player_id, 256)

        self.assertEqual(original.version, self._version())
        self.assertEqual(thumbnail.version, original.version)
        from PIL import Image

        with Image.open(thumbnail.path) as served:
            self.assertEqual(served.size, (256, 256))

    def test_a_player_with_no_picture_has_none_to_serve(self) -> None:
        with self.assertRaises(service_errors.NotFoundError):
            self.roster.picture(self.alex.player_id)
        with self.assertRaises(service_errors.NotFoundError):
            self.roster.picture("nobody")

    def test_an_unknown_size_is_refused(self) -> None:
        self.roster.set_picture(self.alex.player_id, "one.png", png(50, 50))

        with self.assertRaises(service_errors.RefusedError):
            self.roster.picture(self.alex.player_id, 99)

    def test_a_guest_cannot_have_one_and_an_unknown_id_is_not_found(self) -> None:
        guest = self.roster.add_guest("GST")

        with self.assertRaises(service_errors.RefusedError):
            self.roster.set_picture(guest.player_id, "one.png", png(50, 50))
        with self.assertRaises(service_errors.RefusedError):
            self.roster.clear_picture(guest.player_id)
        with self.assertRaises(service_errors.NotFoundError):
            self.roster.set_picture("nobody", "one.png", png(50, 50))
        self.assertFalse(self.roster.pictures_dir.exists())

    def test_what_is_not_an_image_is_refused_and_nothing_is_written(self) -> None:
        refused = [("face.png", b""), ("face.png", b"not an image at all"),
                   ("face.heic", png(50, 50)), ("face.txt", png(50, 50))]
        for filename, content in refused:
            with self.assertRaises(service_errors.RefusedError, msg=filename):
                self.roster.set_picture(self.alex.player_id, filename, content)

        self.assertFalse(self.roster.pictures_dir.exists())

    def test_a_format_outside_the_list_is_refused_whatever_the_name_says(self) -> None:
        tiff = png(50, 50, fmt="TIFF")

        with self.assertRaises(service_errors.RefusedError):
            self.roster.set_picture(self.alex.player_id, "face.png", tiff)
        self.assertFalse(self.roster.pictures_dir.exists())

    def test_a_picture_with_too_many_pixels_is_refused(self) -> None:
        with mock.patch.object(player_pictures, "_MAX_PIXELS", 100), \
                self.assertRaises(service_errors.RefusedError):
            self.roster.set_picture(self.alex.player_id, "face.png", png(11, 11))
        self.assertFalse(self.roster.pictures_dir.exists())

    def test_the_sized_copies_go_with_the_picture(self) -> None:
        self.roster.set_picture(self.alex.player_id, "one.png", png(600, 600))
        served = self.roster.picture(self.alex.player_id, 256)
        self.assertNotEqual(served.path, self.roster.picture(self.alex.player_id).path)
        self.assertTrue(served.path.is_file())

        self.roster.clear_picture(self.alex.player_id)

        self.assertFalse(served.path.exists())

    def test_a_refused_replacement_leaves_the_picture_as_it_was(self) -> None:
        self.roster.set_picture(self.alex.player_id, "one.png", png(50, 50))
        kept = self._version()

        with self.assertRaises(service_errors.RefusedError):
            self.roster.set_picture(self.alex.player_id, "two.png", b"broken")

        self.assertEqual(self._version(), kept)

    def test_it_survives_a_restart(self) -> None:
        self.roster.set_picture(self.alex.player_id, "one.png", png(50, 50))
        kept = self._version()

        self.assertEqual(self._restart().player_state(self.alex.player_id)["picture_version"],
                         kept)

    def test_an_id_that_cannot_name_a_file_is_refused(self) -> None:
        self._write_players([{"id": "../escape", "name": "X", "initials": "XXX"}])

        with self.assertRaises(service_errors.RefusedError):
            self.roster.set_picture("../escape", "one.png", png(50, 50))
        self.assertFalse((Path(self.tmp.name) / "escape.webp").exists())

    def _write_players(self, rows: list[dict]) -> None:
        held = self._file()
        held["players"] = held["players"] + rows
        self.path.write_text(json.dumps(held), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
