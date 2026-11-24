"""The library checks, and the roll-up a rom check needs.

`rom_installed` is three-valued and the distinction is the whole point: PinMAME's audit
answers False, the name match alone answers None, and a table declaring no rom answers
None as well. Only False is a fault. Anything looser reports a library as broken on any
machine where the audit cannot run, which is every machine without VPX configured.
"""

from __future__ import annotations

import unittest

from manager import page
from manager.kit import data
from manager.settings import sections


class FakeLibrary:
    """Just enough of Library for the checks: games, their media, and the flat lens."""

    def __init__(self, games, rows, media=None, hidden=(), collected=("playfield", "backglass")):
        self.games = games
        self.media = media or {g["id"]: {} for g in games}
        self._rows = rows
        self._hidden = set(hidden)
        self._collected = set(collected)

    def kept_kinds(self):
        return {"media": self._collected}

    def table_rows(self):
        return self._rows

    def fillable_media(self):
        return getattr(self, "offered", {})

    def hidden_checks(self):
        return self._hidden


def game(game_id, **rest):
    return {"id": game_id, "name": game_id, "year": "1992", **rest}


def table(game_id, rom_installed, rom="afm_113b"):
    return {"game_id": game_id, "rom": rom, "rom_installed": rom_installed}


class RollupTests(unittest.TestCase):
    def test_audit_says_missing_is_a_fault(self):
        lib = FakeLibrary([game("g1")], [table("g1", False)])
        self.assertTrue(sections.rollups(lib)["g1"]["rom_missing"])

    def test_installed_is_not_a_fault(self):
        lib = FakeLibrary([game("g1")], [table("g1", True)])
        self.assertFalse(sections.rollups(lib)["g1"]["rom_missing"])

    def test_unknown_is_not_a_fault(self):
        """None is "we could not tell" - the audit needs a VPX binary. Reporting it as
        missing would call every table broken on a machine that has none."""
        lib = FakeLibrary([game("g1")], [table("g1", None)])
        self.assertFalse(sections.rollups(lib)["g1"]["rom_missing"])

    def test_no_rom_declared_is_not_a_fault(self):
        lib = FakeLibrary([game("g1")], [table("g1", None, rom="")])
        self.assertFalse(sections.rollups(lib)["g1"]["rom_missing"])

    def test_one_bad_table_flags_the_game(self):
        """A folder holds several builds and they can declare different roms. The game
        reads as missing when any one of them is."""
        lib = FakeLibrary([game("g1")],
                          [table("g1", True), table("g1", False), table("g1", None)])
        self.assertTrue(sections.rollups(lib)["g1"]["rom_missing"])

    def test_games_do_not_leak_into_each_other(self):
        lib = FakeLibrary([game("g1"), game("g2")],
                          [table("g1", False), table("g2", True)])
        rolled = sections.rollups(lib)
        self.assertTrue(rolled["g1"]["rom_missing"])
        self.assertFalse(rolled["g2"]["rom_missing"])


class FindingsTests(unittest.TestCase):
    def test_the_check_reports_the_game(self):
        lib = FakeLibrary([game("g1")], [table("g1", False)])
        found = sections.findings(lib)
        self.assertEqual([g["id"] for g in found["rom_missing"]], ["g1"])

    def test_a_game_with_no_tables_is_not_flagged(self):
        """`rollups` never saw it, so the check reads an empty dict rather than raising."""
        lib = FakeLibrary([game("g1")], [])
        self.assertEqual(sections.findings(lib)["rom_missing"], [])

    def test_a_check_the_library_keeps_quiet_is_not_run(self):
        lib = FakeLibrary([game("g1")], [table("g1", False)], hidden=["rom_missing"])
        self.assertNotIn("rom_missing", sections.findings(lib))

    def test_a_game_with_no_match_is_unmatched(self):
        lib = FakeLibrary([game("g1"), game("g2", vps_id="abc")], [])
        self.assertEqual(["g1"], [g["id"] for g in sections.findings(lib)["unmatched"]])

    def test_a_kind_the_library_does_not_collect_is_not_a_gap(self):
        lib = FakeLibrary([game("g1")], [], collected=("playfield",))
        found = sections.findings(lib)
        self.assertEqual(["g1"], [g["id"] for g in found["no_playfield"]])
        self.assertNotIn("no_backglass", found)

    def test_no_media_counts_only_the_kinds_collected(self):
        """A file of a kind nobody collects does not make the game have media."""
        lib = FakeLibrary([game("g1")], [], collected=("playfield",),
                          media={"g1": {"backglass": {"present": True}}})
        self.assertEqual(["g1"], [g["id"] for g in sections.findings(lib)["no_media"]])

    def test_a_game_a_source_can_fill_is_left_to_discovery(self):
        """Missing from Your Games lists it, so the check does not: a game is on one list."""
        lib = FakeLibrary([game("g1"), game("g2")], [])
        lib.offered = {"playfield": {"g1"}}
        found = sections.findings(lib)

        self.assertEqual(["g2"], [g["id"] for g in found["no_playfield"]])
        self.assertEqual(["g1", "g2"], [g["id"] for g in found["no_backglass"]])

    def test_a_game_with_another_slot_offered_is_still_missing_this_one(self):
        lib = FakeLibrary([game("g1")], [])
        lib.offered = {"backglass": {"g1"}}

        self.assertEqual(["g1"], [g["id"] for g in sections.findings(lib)["no_playfield"]])
        self.assertEqual([], sections.findings(lib)["no_backglass"])

    def test_no_media_keeps_a_game_only_while_no_kind_it_collects_is_offered(self):
        lib = FakeLibrary([game("g1"), game("g2"), game("g3")], [])
        lib.offered = {"backglass": {"g1"}, "wheel": {"g2"}}

        self.assertEqual(["g2", "g3"], [g["id"] for g in sections.findings(lib)["no_media"]])

    def test_a_check_that_was_turned_off_stays_off_whatever_is_offered(self):
        lib = FakeLibrary([game("g1")], [], hidden=["no_playfield"])
        lib.offered = {"playfield": {"g1"}}

        self.assertNotIn("no_playfield", sections.findings(lib))

    def test_every_check_gets_the_third_argument(self):
        """The signature changed for one check; the others must still run."""
        lib = FakeLibrary([game("g1", year="")], [table("g1", True)])
        found = sections.findings(lib)
        self.assertEqual([g["id"] for g in found["no_year"]], ["g1"])


class _Client:
    def __init__(self, policy=None, offered=None):
        self.policy = policy
        self.offered = offered
        self.reads = {"tables": 0, "media": 0, "policy": 0}

    def games(self):
        return [game("g1"), game("g2")]

    def media(self, game_id):
        return {"wheel": {"present": False}}

    def all_tables(self, media="", game=""):
        self.reads["tables"] += 1
        return [table("g1", False)]

    def all_media(self, kind="", shared=False):
        self.reads["media"] += 1
        return [{"game_id": "g1", "kind": "wheel", "present": True}]

    def library_policy(self):
        self.reads["policy"] += 1
        if self.policy is None:
            raise OSError("unreachable")
        return self.policy

    def put_library_policy(self, changes):
        self.policy = {**(self.policy or {}), **changes}
        return self.policy

    def library_game_collections(self):
        return {}

    def discovery_missing(self):
        self.reads["offered"] = self.reads.get("offered", 0) + 1
        if self.offered is None:
            raise OSError("unreachable")
        return {"counted": "", "missing": {}, "offered": self.offered}


class OverviewReadTests(unittest.TestCase):
    """Everything the Overview counts is read before it draws."""

    def _library(self, client):
        library = data.Library(client)
        library.games = [game("g1"), game("g2")]
        return library

    def test_it_reads_the_tables_media_and_quiet_checks(self):
        library = self._library(_Client({"hidden_checks": ["no_year"]}))
        self.assertFalse(library.has_overview())

        library.load_overview()

        self.assertTrue(library.has_overview())
        self.assertEqual({"no_year"}, library.hidden_checks())
        self.assertEqual(["g1"], [g["id"] for g in sections.findings(library)["rom_missing"]])

    def test_it_reads_which_games_a_source_can_fill_and_the_checks_leave_them(self):
        library = self._library(_Client({}, {"playfield": ["g1"]}))
        library.load_overview()

        self.assertEqual({"playfield": {"g1"}}, library.fillable_media())
        self.assertEqual(["g2"], [g["id"] for g in sections.findings(library)["no_playfield"]])

    def test_an_unreadable_answer_leaves_every_gap_counted(self):
        library = self._library(_Client({}))
        library.load_overview()

        self.assertEqual({}, library.fillable_media())
        self.assertEqual(["g1", "g2"],
                         [g["id"] for g in sections.findings(library)["no_playfield"]])

    def test_the_draw_reads_nothing(self):
        client = _Client({"hidden_checks": []})
        library = self._library(client)
        library.load_overview()
        before = dict(client.reads)

        sections.findings(library)

        self.assertEqual(before, client.reads)

    def test_media_an_import_emptied_is_read_again(self):
        client = _Client({})
        library = self._library(client)
        library.load_overview()
        library.refresh_after_import()

        self.assertFalse(library.has_overview())
        library.load_overview()
        self.assertTrue(library.media["g1"]["wheel"]["present"])

    def test_one_game_the_panel_read_does_not_stand_for_the_library(self):
        client = _Client({})
        library = self._library(client)
        library.load_overview()
        library.refresh_after_import()
        library.media_for("g2", None)

        self.assertFalse(library.has_overview())
        library.load_overview()
        self.assertEqual(["g1", "g2"], sorted(library.media))
        self.assertFalse(library.media["g2"]["wheel"]["present"])

    def test_an_unreadable_policy_reports_every_check(self):
        library = self._library(_Client(None))
        library.load_overview()
        self.assertEqual(set(), library.hidden_checks())

    def test_it_reads_the_quiet_checks_every_time_it_loads(self):
        client = _Client({"hidden_checks": []})
        library = self._library(client)
        library.load_overview()
        client.policy = {"hidden_checks": ["no_year"]}

        library.load_overview()

        self.assertEqual({"no_year"}, library.hidden_checks())


class GridReadTests(unittest.TestCase):
    """The line above the Games and Tables grids counts what Overview counts, so each
    grid reads the stopped checks itself instead of waiting for Overview to."""

    VIEWS = ("games", "tables")

    def _library(self, client):
        library = data.Library(client)
        library.games = [game("g1"), game("g2")]
        return library

    @staticmethod
    def _held(view, library):
        return page.reads_before_drawing(view, library) is None

    @staticmethod
    def _read(view, library):
        reads = page.reads_before_drawing(view, library)
        assert reads is not None
        reads()

    def test_a_grid_read_alone_holds_the_stopped_checks(self):
        for view in self.VIEWS:
            with self.subTest(view=view):
                library = self._library(_Client({"hidden_checks": ["older_format"]}))
                self.assertFalse(self._held(view, library))

                self._read(view, library)

                self.assertIn("older_format", library.stopped())
                self.assertTrue(self._held(view, library))

    def test_a_policy_write_has_the_grid_read_it_again(self):
        for view in self.VIEWS:
            with self.subTest(view=view):
                client = _Client({"hidden_checks": ["older_format"]})
                library = self._library(client)
                self._read(view, library)

                library.put_library_policy({"hidden_checks": []})

                self.assertFalse(self._held(view, library))
                before = client.reads["policy"]
                self._read(view, library)
                self.assertEqual(before + 1, client.reads["policy"])
                self.assertNotIn("older_format", library.stopped())
                self.assertTrue(self._held(view, library))

    def test_an_unreadable_policy_leaves_the_line_counting_every_check(self):
        for view in self.VIEWS:
            with self.subTest(view=view):
                library = self._library(_Client(None))

                self._read(view, library)

                self.assertEqual(set(), library.stopped())
                self.assertTrue(self._held(view, library))
                if view == "games":
                    self.assertEqual(["g1", "g2"], [one["id"] for one in library.game_rows()])
                else:
                    self.assertEqual(1, len(library.table_rows()))


if __name__ == "__main__":
    unittest.main()
