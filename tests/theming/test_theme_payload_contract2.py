"""The theme payload at both contracts.

Contract 1 is what twelve published themes read and the parity gate holds against
master. Contract 2 is the shape the API serves, so a theme and a REST client describe
a game the same way.
"""

import json
import unittest

from common.games.collection_resolver import visible_entries
from frontend.game_state import games_json
from tests.support.entries import entries_for
from tests.support.library import fake_game


def _game(title="Medieval Madness", tables=None, vpx="/g/MM/MM.vpx"):
    return fake_game(
               "/g/MM", title, full_path_vpx_file=vpx,
               pup_pack_exists=True, alt_color_exists=False, alt_sound_exists=False,
               creation_time=0,
               meta={
            "Info": {"Title": title, "Manufacturer": "Williams", "Year": "1997",
                     "Type": "SS", "VPSId": "vps-mm", "Themes": ["Fantasy"]},
            "User": {"Rating": 5},
            # An explicit default, so which table collapses is stated rather than
            # falling out of filename order.
            "vpinfe": {"game_id": "mm00000001", "default_table": "t1"},
            **({"tables": tables} if tables is not None else {}),
        },
           )


TWO_TABLES = {
    "t1": {"id": "t1", "filename": "MM VPW.vpx", "version": "1.2", "rom": "mm_109c",
           "authors": ["VPW"], "detect_ssf": True},
    "t2": {"id": "t2", "filename": "MM JP.vpx", "version": "1.0", "rom": "mm_109c"},
}


class ContractOneTests(unittest.TestCase):
    """Unchanged, and it has to stay that way - see tests/invariants/test_parity.py."""

    def test_it_is_a_bare_array_of_rows(self) -> None:
        payload = json.loads(games_json(entries_for([_game(tables=TWO_TABLES)]),
                                        contract=1))

        self.assertIsInstance(payload, list)
        self.assertEqual(len(payload), 1, "one row per game, collapsed")
        # The contract 1 spellings, which is the whole point of the projection.
        self.assertIn("tableDirName", payload[0])
        self.assertIn("fullPathTable", payload[0])
        self.assertIn("VPXFile", payload[0]["meta"])
        self.assertNotIn("tables", payload[0]["meta"], "contract 1 never sees these")

    def test_a_game_with_several_tables_is_still_one_row(self) -> None:
        """Contract 1 predates a game offering more than one table."""
        payload = json.loads(games_json(entries_for([_game(tables=TWO_TABLES)]),
                                        contract=1))

        self.assertEqual(len(payload), 1)


class TheRatingScaleTests(unittest.TestCase):
    """Protects: (b) a theme reads a rating on the scale its contract was written for."""

    def _rated(self, halves: int):
        game = _game()
        game.meta_config["User"] = {"Rating": halves}
        return game

    def test_contract_1_gives_whole_stars_a_half_rounds_down_but_never_to_none(self) -> None:
        for halves, stars in ((0, 0), (1, 1), (2, 1), (3, 1), (7, 3), (8, 4), (10, 5)):
            with self.subTest(halves=halves):
                (row,) = json.loads(games_json(entries_for([self._rated(halves)]),
                                               contract=1))

                self.assertEqual(row["meta"]["User"]["Rating"], stars)

    def test_contract_2_gives_half_stars(self) -> None:
        for halves in (0, 1, 7, 8, 10):
            with self.subTest(halves=halves):
                body = json.loads(games_json(entries_for([self._rated(halves)]),
                                             contract=2, collection="C"))

                self.assertEqual(body["entries"][0]["game"]["user"]["rating"], halves)

    def test_what_a_theme_reads_does_not_change_what_is_held(self) -> None:
        game = self._rated(7)

        games_json(entries_for([game]), contract=1)

        self.assertEqual(game.meta_config["User"]["Rating"], 7)


class ContractTwoTests(unittest.TestCase):
    def _payload(self, games, **kwargs):
        return json.loads(games_json(entries_for(games, **kwargs), contract=2,
                                     collection="Friday Night",
                                     ))

    def test_it_says_which_collection_it_is(self) -> None:
        body = self._payload([_game(tables=TWO_TABLES)])

        self.assertEqual(body["collection"], "Friday Night")
        self.assertEqual(body["count"], len(body["entries"]))

    def test_an_entry_names_its_game_and_its_table(self) -> None:
        entry = self._payload([_game(tables=TWO_TABLES)])["entries"][0]

        self.assertEqual(entry["game"]["id"], "mm00000001")
        self.assertEqual(entry["game"]["name"], "Medieval Madness")
        self.assertEqual(entry["table"]["id"], "t1")
        self.assertEqual(entry["table"]["version"], "1.2")
        self.assertEqual(entry["siblings"], 2, "so a theme can offer a switcher")

    def test_detect_flags_lose_their_prefix(self) -> None:
        """`detects.ssf`, not `detect_ssf` - the prefix was storage, not vocabulary."""
        entry = self._payload([_game(tables=TWO_TABLES)])["entries"][0]

        self.assertTrue(entry["table"]["detects"]["ssf"])
        self.assertFalse(entry["table"]["detects"]["fleep"])

    def test_a_game_contributes_its_default_table(self) -> None:
        """The list is one entry per game unless a collection named a second table of
        one. `siblings` is how a theme knows there are others to offer."""
        body = self._payload([_game(tables=TWO_TABLES)])

        self.assertEqual(body["count"], 1)
        self.assertEqual([e["table"]["id"] for e in body["entries"]], ["t1"])
        self.assertEqual(body["entries"][0]["siblings"], 2)

    def test_the_entry_carries_play_stats_at_both_levels(self) -> None:
        """Texal-Flyer reads meta.User.StartCount and RunTime at contract 1. Contract 2
        served only the rating, so a theme showing play stats could not be ported."""
        game = _game(tables=TWO_TABLES)
        game.meta_config["User"] = {"Rating": 7, "Favorite": 1, "Tags": ["night"],
                                   "LastRun": 1754000000, "StartCount": 12, "RunTime": 90}
        game.meta_config["tables"]["t1"]["user"] = {
            "last_run": "2026-08-01T20:14:00Z", "start_count": 5, "run_time_seconds": 3600}

        entry = self._payload([game])["entries"][0]

        self.assertEqual(entry["game"]["user"]["play_count"], 12)
        self.assertEqual(entry["game"]["user"]["play_time_seconds"], 90 * 60,
                         "User.RunTime is minutes; the payload names its unit")
        self.assertTrue(entry["game"]["user"]["last_played"].startswith("20"),
                        "User.LastRun is an epoch int; the payload serves ISO")
        self.assertEqual(entry["game"]["user"]["rating"], 7)
        self.assertTrue(entry["game"]["user"]["favorite"])
        self.assertEqual(entry["game"]["user"]["tags"], ["night"])

        self.assertEqual(entry["table"]["user"]["play_count"], 5,
                         "the table keeps its own count, not a share of the game's")
        self.assertEqual(entry["table"]["user"]["play_time_seconds"], 3600)

    def test_the_entry_carries_the_default_tables_high_scores(self) -> None:
        game = _game(tables=TWO_TABLES)
        game.meta_config["User"]["HighScores"] = {"mm_109c": {
            "read_at": "2026-09-27T12:00:00Z", "score_kind": "Leaderboard", "new": [0],
            "entries": [{"section": "GRAND CHAMPION", "rank": None, "initials": "ABC",
                         "score": 52_000_000}]}}

        user = self._payload([game])["entries"][0]["game"]["user"]

        self.assertNotIn("score", user)
        shown = user["high_scores"]
        self.assertEqual((shown["rom"], shown["table_id"], shown["state"]),
                         ("mm_109c", "t1", "read"))
        (section,) = shown["sections"]
        self.assertEqual(section["name"], "GRAND CHAMPION")
        self.assertEqual([(one["initials"], one["text"], one["new"])
                          for one in section["entries"]], [("ABC", "52,000,000", True)])

    def test_rating_lives_with_the_other_user_values(self) -> None:
        entry = self._payload([_game(tables=TWO_TABLES)])["entries"][0]

        self.assertIn("rating", entry["game"]["user"])
        self.assertNotIn("rating", entry["game"],
                         "one home for it, not two")

    def test_media_names_the_kinds_that_resolved(self) -> None:
        """Names, not paths. A theme composes /media/<game id>/<kind>; naming the files
        here would leak the filesystem into a web page and cost several hundred kilobytes
        on a real library."""
        entry = self._payload([_game(tables=TWO_TABLES)])["entries"][0]

        self.assertIsInstance(entry["media"], list)
        for name in entry["media"]:
            self.assertNotIn("/", name, "a kind name, never a path")
        for attr in ("PlayfieldImagePath", "BGImagePath", "ManufacturerLogoPath"):
            self.assertNotIn(attr, entry["media"],
                             "the canonical kind names, not contract 1's attributes")

    def test_the_manufacturer_logo_belongs_to_the_game(self) -> None:
        """Shared art keyed on the manufacturer, not one of this game's media kinds -
        it used to sit inside the media block under a PascalCase name of its own."""
        entry = self._payload([_game(tables=TWO_TABLES)])["entries"][0]

        self.assertIn("manufacturer_logo", entry["game"])


class UnparsedGameTests(unittest.TestCase):
    """A folder no metadata build has touched still has to appear and launch."""

    def test_it_gets_an_entry_from_the_scan(self) -> None:
        offered = visible_entries(_game(tables=None))

        self.assertEqual(len(offered), 1)
        self.assertEqual(offered[0]["filename"], "MM.vpx")
        self.assertEqual(offered[0]["id"], "", "the id arrives with the parse")

    def test_a_game_with_no_vpx_at_all_offers_nothing(self) -> None:
        """Nothing to launch, so nothing in the play lens - by design."""
        self.assertEqual(visible_entries(_game(tables={}, vpx="")), [])


if __name__ == "__main__":
    unittest.main()
