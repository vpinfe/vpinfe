"""One collection in, one ordered list out.

The cases that matter are the ones the two old engines could not express: a game
appearing twice with different tables, a named table that outranks a filter, an
exclusion that outranks both, and `hidden` outranking everything.
"""

import unittest

from common.games.collection_resolver import (
    UnresolvableCollectionError,
    holding,
    resolve,
    resolve_games,
    visible_entries,
)
from common.games.collection_store import (
    CollectionStore,
    DuplicateMemberError,
)
from common.games.game import Game
from common.i18n import t
from tests.support.library import TempTree


def _game(gid, title, tables, manufacturer="", rating=0, last_run=0, default=""):
    """A game whose tables map is keyed by id, as storage has it."""
    return Game(
        game_dir_name=title,
        creation_time=0,
        meta_config={
            "Info": {"Title": title, "Manufacturer": manufacturer, "Year": "1995",
                     "Type": "SS", "Themes": []},
            "User": {"Rating": rating, "LastRun": last_run, "StartCount": 0, "RunTime": 0},
            "vpinfe": {"game_id": gid, **({"default_table": default} if default else {})},
            "tables": tables,
        },
    )


def _table(tid, filename, hidden=False, absent=False):
    entry = {"id": tid, "filename": filename}
    if hidden:
        entry["hidden"] = True
    if absent:
        entry["absent_since"] = "2026-08-23T18:24:08Z"
    return entry


class ResolverTests(TempTree):
    def setUp(self) -> None:
        super().setUp()
        self.path = self.root / "collections.json"
        self.collections = CollectionStore(str(self.path))

        self.mm = _game("mm", "Medieval Madness",
                        {"vpw": _table("vpw", "MM VPW.vpx"),
                         "jp": _table("jp", "MM JP.vpx")},
                        manufacturer="Williams", rating=5, last_run=200, default="vpw")
        self.afm = _game("afm", "Attack from Mars",
                         {"a1": _table("a1", "AFM.vpx"),
                          "vr": _table("vr", "AFM VR.vpx")},
                         manufacturer="Bally", rating=3, last_run=100, default="a1")
        self.taf = _game("taf", "The Addams Family",
                         {"t1": _table("t1", "TAF.vpx")},
                         manufacturer="Bally", rating=4, last_run=50)
        self.games = [self.mm, self.afm, self.taf]

    def _ids(self, entries):
        return [(e.game.meta_config["vpinfe"]["game_id"], e.table_id) for e in entries]

    def _titles(self, name):
        return [e.game.game_dir_name for e in resolve(name, self.collections, self.games)]

    # --- the case the whole design exists for ------------------------------------

    def test_one_game_appears_twice_with_different_tables_in_a_curated_order(self) -> None:
        self.collections.add_collection("Friday Night")
        self.collections.set_order("Friday Night", "manual")
        self.collections.add_member("Friday Night", "mm", table_id="vpw")
        self.collections.add_member("Friday Night", "afm")
        self.collections.add_member("Friday Night", "mm", table_id="jp")

        entries = resolve("Friday Night", self.collections, self.games)

        # mm twice because two of its tables were named; afm once because only the
        # game was. Nothing de-duplicates by game, which is what makes this possible.
        self.assertEqual(self._ids(entries),
                         [("mm", "vpw"), ("afm", "a1"), ("mm", "jp")])

    def test_changing_which_table_a_member_names_keeps_its_place(self) -> None:
        """The reason this is a store method and not remove-then-add.

        Curated order is the whole point of a manual collection, and rebuilding the ref
        would send the row it edits to the end of the list.
        """
        self.collections.add_collection("Friday Night")
        self.collections.set_order("Friday Night", "manual")
        for member in ("mm", "afm", "taf"):
            self.collections.add_member("Friday Night", member)

        self.collections.set_member_table("Friday Night", "afm", "vr")

        self.assertEqual(self._ids(resolve("Friday Night", self.collections,
                                           self.games)),
                         [("mm", "vpw"), ("afm", "vr"), ("taf", "t1")],
                         "the middle row changed table without moving")

    def test_a_member_can_be_handed_back_its_game_default(self) -> None:
        """The return trip, which is what makes the control reversible."""
        self.collections.add_collection("Friday Night")
        self.collections.set_order("Friday Night", "manual")
        self.collections.add_member("Friday Night", "mm", table_id="jp")
        self.collections.add_member("Friday Night", "afm")

        self.collections.set_member_table("Friday Night", "mm", "", was="jp")

        entries = resolve("Friday Night", self.collections, self.games)
        self.assertEqual(self._ids(entries), [("mm", "vpw"), ("afm", "a1")],
                         "mm follows its default again, still first")
        self.assertNotIn("table", self.collections.get_member_refs("Friday Night")[0],
                         "and the key is gone rather than written empty")

    def test_a_second_table_lands_beside_the_row_it_was_asked_from(self) -> None:
        """Not at the end. In a long manual collection the end is off screen, and a new
        row nobody can see is indistinguishable from a click that did nothing."""
        self.collections.add_collection("Friday Night")
        self.collections.set_order("Friday Night", "manual")
        self.collections.add_member("Friday Night", "mm", table_id="vpw")
        self.collections.add_member("Friday Night", "afm")
        self.collections.add_member("Friday Night", "taf")

        self.collections.add_member("Friday Night", "mm", table_id="jp",
                                    after_table="vpw")

        self.assertEqual(self._ids(resolve("Friday Night", self.collections,
                                           self.games)),
                         [("mm", "vpw"), ("mm", "jp"), ("afm", "a1"), ("taf", "t1")])

    def test_a_second_table_appends_when_no_sibling_is_named(self) -> None:
        """Every caller before this one appended, and a collection with no curated
        order does not care where a ref sits."""
        self.collections.add_collection("Friday Night")
        self.collections.set_order("Friday Night", "manual")
        self.collections.add_member("Friday Night", "mm", table_id="vpw")
        self.collections.add_member("Friday Night", "afm")

        self.collections.add_member("Friday Night", "mm", table_id="jp")

        self.assertEqual(self._ids(resolve("Friday Night", self.collections,
                                           self.games)),
                         [("mm", "vpw"), ("afm", "a1"), ("mm", "jp")])

    def test_lifting_a_whole_game_exclusion_leaves_the_per_table_ones(self) -> None:
        """`""` is the exclusion that names no table, not every exclusion for the game.
        They were the same value, so an exclusion of a whole game could not be lifted
        without also lifting per-table ones somebody meant to keep - and the UI, which
        sends the table a row *resolves to*, matched no ref at all and lifted nothing."""
        self.collections.add_collection("Friday Night")
        self.collections.exclude("Friday Night", "mm")
        self.collections.exclude("Friday Night", "mm", table_id="jp")

        self.collections.unexclude("Friday Night", "mm", "")

        self.assertEqual(self.collections.get_excluded_refs("Friday Night"),
                         [{"game": "mm", "table": "jp"}])

    def test_lifting_every_exclusion_for_a_game(self) -> None:
        """Which is what "want this game back whole" means, and is now said with None."""
        self.collections.add_collection("Friday Night")
        self.collections.exclude("Friday Night", "mm")
        self.collections.exclude("Friday Night", "mm", table_id="jp")
        self.collections.exclude("Friday Night", "afm")

        self.collections.unexclude("Friday Night", "mm", None)

        self.assertEqual(self.collections.get_excluded_refs("Friday Night"),
                         [{"game": "afm"}])

    def test_removing_the_following_row_leaves_the_named_ones(self) -> None:
        """`""` is the ref that names no table, not every ref for the game. It used to
        be both, so deleting the row that follows a game's default silently deleted
        every other row for that game with it."""
        self.collections.add_collection("Friday Night")
        self.collections.set_order("Friday Night", "manual")
        self.collections.add_member("Friday Night", "mm")
        self.collections.add_member("Friday Night", "mm", table_id="jp")
        self.collections.add_member("Friday Night", "mm", table_id="vpw")

        self.collections.remove_member("Friday Night", "mm", "")

        self.assertEqual(self._ids(resolve("Friday Night", self.collections,
                                           self.games)),
                         [("mm", "jp"), ("mm", "vpw")])

    def test_removing_a_game_with_no_table_named_takes_every_row(self) -> None:
        """Which is what "remove this game" means, and is now said with `None`."""
        self.collections.add_collection("Friday Night")
        self.collections.add_member("Friday Night", "mm")
        self.collections.add_member("Friday Night", "mm", table_id="jp")
        self.collections.add_member("Friday Night", "afm")

        self.collections.remove_member("Friday Night", "mm", None)

        self.assertEqual(self._ids(resolve("Friday Night", self.collections,
                                           self.games)), [("afm", "a1")])

    def test_pointing_a_member_at_a_table_the_collection_already_names(self) -> None:
        """Refused, not repaired. Two refs naming one table is the duplicate 2.10
        forbids, and the obvious repair - dropping one - takes a row away without
        saying so, which is the removal nobody can explain."""
        self.collections.add_collection("Friday Night")
        self.collections.set_order("Friday Night", "manual")
        self.collections.add_member("Friday Night", "mm", table_id="jp")
        self.collections.add_member("Friday Night", "afm")
        self.collections.add_member("Friday Night", "mm", table_id="vpw")

        with self.assertRaises(DuplicateMemberError) as caught:
            self.collections.set_member_table("Friday Night", "mm", "vpw", was="jp")

        self.assertEqual(str(caught.exception),
                         t("error.collections.table_already_in", name="Friday Night"))

        self.assertEqual(self._ids(resolve("Friday Night", self.collections,
                                           self.games)),
                         [("mm", "jp"), ("afm", "a1"), ("mm", "vpw")],
                         "and the collection is untouched")

    def test_changing_a_ref_that_is_not_there_is_refused(self) -> None:
        """`was` has to name a ref that exists, or the caller is editing a row it has
        not got and a silent append would look like it worked."""
        self.collections.add_collection("Friday Night")
        self.collections.add_member("Friday Night", "mm")

        with self.assertRaises(ValueError) as caught:
            self.collections.set_member_table("Friday Night", "mm", "jp", was="vpw")

        self.assertEqual(str(caught.exception),
                         t("error.collections.row_gone", name="Friday Night"))

    def test_a_named_table_and_a_named_game_sit_side_by_side(self) -> None:
        self.collections.add_collection("Friday Night")
        self.collections.set_order("Friday Night", "manual")
        self.collections.add_member("Friday Night", "mm", table_id="jp")
        self.collections.add_member("Friday Night", "afm")

        entries = resolve("Friday Night", self.collections, self.games)

        self.assertEqual(self._ids(entries), [("mm", "jp"), ("afm", "a1")],
                         "the table this collection named, then the other game's default")

    def test_naming_a_game_takes_its_default_table(self) -> None:
        """A member naming a game means the game, so the other tables it offers are not
        in the collection. `siblings` says they exist and /games/{id}/tables lists them."""
        self.collections.add_collection("All")
        self.collections.add_member("All", "afm")

        entries = resolve("All", self.collections, self.games)

        self.assertEqual(self._ids(entries), [("afm", "a1")])
        self.assertEqual([e.siblings for e in entries], [2])

    # --- precedence ---------------------------------------------------------------

    def test_a_member_overrides_what_the_filter_would_have_taken(self) -> None:
        """Explicit beats implicit: naming a game says exactly what it contributes."""
        self.collections.add_filter_collection("Bally", manufacturer="Bally")
        self.collections.add_member("Bally", "afm", table_id="vr")

        entries = resolve("Bally", self.collections, self.games)

        self.assertIn(("afm", "vr"), self._ids(entries))
        self.assertNotIn(("afm", "a1"), self._ids(entries),
                         "the filter must not re-add the table the member excluded")

    def test_an_exclusion_overrules_a_filter(self) -> None:
        self.collections.add_filter_collection("Bally", manufacturer="Bally")
        self.collections.exclude("Bally", "taf")

        entries = resolve("Bally", self.collections, self.games)

        self.assertEqual({gid for gid, _ in self._ids(entries)}, {"afm"})

    def test_excluding_one_table_leaves_the_rest_of_the_game(self) -> None:
        self.collections.add_filter_collection("Bally", manufacturer="Bally")
        self.collections.exclude("Bally", "afm", table_id="vr")

        entries = resolve("Bally", self.collections, self.games)

        self.assertIn(("afm", "a1"), self._ids(entries))
        self.assertNotIn(("afm", "vr"), self._ids(entries))

    # --- tags on a table ----------------------------------------------------------

    def _tag(self, game, table_id, *tags):
        game.meta_config["tables"][table_id]["user"] = {"tags": list(tags)}

    def _rule(self, name, **criteria):
        self.collections.add_collection(name)
        self.collections.make_filter_collection(name, criteria)

    def test_a_table_s_own_tag_brings_in_that_table(self) -> None:
        self._tag(self.afm, "vr", "VR")
        self._rule("VR", tags="VR")

        self.assertEqual([("afm", "vr")],
                         self._ids(resolve("VR", self.collections, self.games)))

    def test_a_game_s_tag_still_brings_in_its_default(self) -> None:
        self.afm.meta_config["User"]["Tags"] = ["VR"]
        self._rule("VR", tags="VR")

        self.assertEqual([("afm", "a1")],
                         self._ids(resolve("VR", self.collections, self.games)))

    def test_two_tagged_tables_of_one_game_are_both_in(self) -> None:
        self._tag(self.mm, "vpw", "Night")
        self._tag(self.mm, "jp", "Night")
        self._rule("Night", tags="Night")

        self.assertEqual({("mm", "vpw"), ("mm", "jp")},
                         set(self._ids(resolve("Night", self.collections, self.games))))

    def test_the_rest_of_the_rule_still_applies_to_the_game(self) -> None:
        self._tag(self.afm, "vr", "VR")
        self._tag(self.mm, "jp", "VR")
        self._rule("Bally VR", tags="VR", manufacturer="Bally")

        self.assertEqual([("afm", "vr")],
                         self._ids(resolve("Bally VR", self.collections, self.games)))

    def test_none_of_a_tag_leaves_out_only_the_table_carrying_it(self) -> None:
        self._tag(self.afm, "a1", "VR")
        self._rule("No VR", tags_none_of="VR")

        self.assertEqual({("mm", "vpw"), ("afm", "vr"), ("taf", "t1")},
                         set(self._ids(resolve("No VR", self.collections, self.games))),
                         "the default carries it, so the game comes in by its other table")

    def test_none_of_a_tag_the_game_carries_leaves_out_the_game(self) -> None:
        self.afm.meta_config["User"]["Tags"] = ["VR"]
        self._rule("No VR", tags_none_of="VR")

        self.assertEqual({"mm", "taf"}, {gid for gid, _ in self._ids(
            resolve("No VR", self.collections, self.games))})

    def test_everything_except_one_manufacturer(self) -> None:
        self._rule("Not Bally", manufacturer_none_of="Bally")

        self.assertEqual([("mm", "vpw")],
                         self._ids(resolve("Not Bally", self.collections, self.games)))

    def test_the_management_lens_holds_the_game_and_names_the_table(self) -> None:
        self._tag(self.afm, "vr", "VR")
        self._rule("VR", tags="VR")

        held = holding("VR", self.collections, self.games)

        self.assertEqual((["Attack from Mars"], {"afm": ("vr",)}),
                         ([g.game_dir_name for g in held.matched], held.tables))

    def test_taking_out_the_tagged_table_takes_out_the_game(self) -> None:
        self._tag(self.afm, "vr", "VR")
        self._rule("VR", tags="VR")
        self.collections.exclude("VR", "afm", table_id="vr")

        self.assertEqual([], resolve("VR", self.collections, self.games))
        self.assertEqual([], holding("VR", self.collections, self.games).games)

    def test_a_hidden_tagged_table_is_held_and_not_handed_out(self) -> None:
        self._tag(self.afm, "vr", "VR")
        self.afm.meta_config["tables"]["vr"]["hidden"] = True
        self._rule("VR", tags="VR")

        self.assertEqual([], resolve("VR", self.collections, self.games))
        self.assertEqual({"afm": ("vr",)},
                         holding("VR", self.collections, self.games).tables)

    def test_hidden_beats_a_named_table(self) -> None:
        """`hidden` is library-wide and exists so a patch base can stay on disk."""
        self.mm.meta_config["tables"]["jp"]["hidden"] = True
        self.collections.add_collection("Friday Night")
        self.collections.add_member("Friday Night", "mm", table_id="jp")

        entries = resolve("Friday Night", self.collections, self.games)

        self.assertEqual(entries, [])

    def test_a_hidden_table_is_not_offered_when_only_the_game_is_named(self) -> None:
        self.afm.meta_config["tables"]["vr"]["hidden"] = True
        self.collections.add_collection("All")
        self.collections.add_member("All", "afm")

        entries = resolve("All", self.collections, self.games)

        self.assertEqual(self._ids(entries), [("afm", "a1")])

    # --- ordering -----------------------------------------------------------------

    def test_a_filter_collection_sorts_by_its_stored_criteria(self) -> None:
        """Titles sort as they are displayed, so a leading "The" is moved to the end -
        "The Addams Family" sorts under A. Long-standing behavior, kept."""
        self.collections.add_filter_collection("Everything", sort_by="Alpha",
                                               order_by="Ascending")

        entries = resolve("Everything", self.collections, self.games)

        self.assertEqual([e.game.game_dir_name for e in entries],
                         ["The Addams Family", "Attack from Mars", "Medieval Madness"])

    def test_last_played_sorts_most_recent_first(self) -> None:
        self.collections.add_filter_collection("Recent", sort_by="LastRun")

        entries = resolve("Recent", self.collections, self.games)

        self.assertEqual([e.game.game_dir_name for e in entries],
                         ["Medieval Madness", "Attack from Mars", "The Addams Family"])

    def test_every_sort_reads_the_stored_direction(self) -> None:
        """`order.direction` used to be a no-op for everything but title and year: the
        other sorts negated their value, so the list arrived largest-first and the
        reverse step had nothing left to turn around.
        """
        self.collections.add_filter_collection("Everything")
        for by in ("title", "rating", "last_played"):
            with self.subTest(by=by):
                self.collections.set_order("Everything", by, "asc")
                up = self._titles("Everything")
                self.collections.set_order("Everything", by, "desc")

                self.assertEqual(self._titles("Everything"), list(reversed(up)))

    def test_a_descending_sort_keeps_its_tiebreak_in_title_order(self) -> None:
        """Reversing the finished list would reverse the tiebreak with it, so two games
        level on the field would come back Z to A. Every game here is from 1995."""
        self.collections.add_filter_collection("Everything")
        self.collections.set_order("Everything", "year", "desc")

        self.assertEqual(self._titles("Everything"),
                         ["The Addams Family", "Attack from Mars", "Medieval Madness"])

    # --- limit ----------------------------------------------------------------------

    def test_a_limit_keeps_the_first_rows_of_the_ordered_list(self) -> None:
        """"Last 20 played" is an order plus a limit, so the cap has to come after the
        sort or it would keep an arbitrary twenty."""
        self.collections.add_filter_collection("Recent", sort_by="LastRun")
        self.collections.set_limit("Recent", 2)

        entries = resolve("Recent", self.collections, self.games)

        self.assertEqual([e.game.game_dir_name for e in entries],
                         ["Medieval Madness", "Attack from Mars"])

    def test_a_limit_past_the_end_keeps_everything(self) -> None:
        self.collections.add_filter_collection("Recent", sort_by="LastRun")
        self.collections.set_limit("Recent", 99)

        self.assertEqual(len(resolve("Recent", self.collections, self.games)), 3)

    def test_no_limit_keeps_everything(self) -> None:
        self.collections.add_filter_collection("Recent", sort_by="LastRun")

        self.assertIsNone(self.collections.get_limit("Recent"))
        self.assertEqual(len(resolve("Recent", self.collections, self.games)), 3)

    def test_a_limit_counts_rows_not_games(self) -> None:
        """A row is a table. A collection naming two tables of one game asked for both,
        so both count against the cap."""
        self.collections.add_collection("Friday Night")
        self.collections.set_order("Friday Night", "manual")
        self.collections.add_member("Friday Night", "mm", table_id="vpw")
        self.collections.add_member("Friday Night", "mm", table_id="jp")
        self.collections.add_member("Friday Night", "afm")
        self.collections.set_limit("Friday Night", 2)

        entries = resolve("Friday Night", self.collections, self.games)

        self.assertEqual(self._ids(entries), [("mm", "vpw"), ("mm", "jp")])

    def test_the_management_lens_applies_the_limit_too(self) -> None:
        """A capped collection contains what the cap leaves. If the lens you go to in
        order to edit one showed more, the two would disagree about what is in it."""
        self.collections.add_filter_collection("Recent", sort_by="LastRun")
        self.collections.set_limit("Recent", 2)

        games = resolve_games("Recent", self.collections, self.games)

        self.assertEqual([g.game_dir_name for g in games],
                         ["Medieval Madness", "Attack from Mars"])

    def test_it_reports_how_it_holds_what_it_holds(self) -> None:
        self.collections.add_filter_collection("Mixed", manufacturer="Bally",
                                               sort_by="Alpha")
        self.collections.add_member("Mixed", "mm")
        self.collections.exclude("Mixed", "taf")

        held = holding("Mixed", self.collections, self.games)

        self.assertEqual((["Medieval Madness"], ["Attack from Mars"], 1),
                         ([g.game_dir_name for g in held.added],
                          [g.game_dir_name for g in held.matched], held.excluded))

    def test_a_table_taken_out_is_not_a_game_excluded(self) -> None:
        self.collections.add_filter_collection("Bally", manufacturer="Bally",
                                               sort_by="Alpha")
        self.collections.exclude("Bally", "afm", "a1")

        self.assertEqual(0, holding("Bally", self.collections, self.games).excluded)

    def test_its_buckets_are_counted_before_the_limit(self) -> None:
        self.collections.add_filter_collection("Capped", manufacturer="Bally",
                                               sort_by="Alpha")
        self.collections.set_limit("Capped", 1)

        held = holding("Capped", self.collections, self.games)

        self.assertEqual((1, 2), (len(held.games), len(held.matched)))

    def test_two_resolutions_of_the_same_input_agree(self) -> None:
        """Peers that tie on the sort key must not shuffle between refreshes."""
        self.collections.add_filter_collection("Everything", sort_by="Alpha")

        first = resolve("Everything", self.collections, self.games)
        second = resolve("Everything", self.collections, self.games)

        self.assertEqual(self._ids(first), self._ids(second))

    # --- refusal ------------------------------------------------------------------

    def test_a_filter_this_build_cannot_read_refuses_by_name(self) -> None:
        """Resolving what is left would answer a different question, silently."""
        self.collections.add_filter_collection("VR Room")
        self.collections.set_filter("VR Room", "app", "VPX VR")

        with self.assertRaises(UnresolvableCollectionError) as caught:
            resolve("VR Room", self.collections, self.games)

        self.assertIn("app", caught.exception.axes)
        self.assertIn("VR Room", str(caught.exception))

    def test_a_member_the_library_does_not_hold_is_skipped_not_dropped(self) -> None:
        self.collections.add_collection("Fav")
        self.collections.add_member("Fav", "not-here")
        self.collections.add_member("Fav", "taf")

        entries = resolve("Fav", self.collections, self.games)

        self.assertEqual(self._ids(entries), [("taf", "t1")])
        self.assertEqual(self.collections.get_members("Fav"), ["not-here", "taf"],
                         "the membership stays on disk; the game may come back")


class VisibleEntryTests(unittest.TestCase):
    """A scanned game always carries `full_path_vpx_file`, so every fixture here sets it.
    Without it the unparsed-game fallback returns nothing and these tests pass whatever
    the filter does - which is how a game with every table hidden went on being offered.
    """

    def _game_with_path(self, tables):
        game = _game("x", "T", tables)
        game.full_path_vpx_file = "/lib/T/a.vpx"
        return game

    def test_a_game_with_no_tables_offers_nothing(self) -> None:
        self.assertEqual(visible_entries(_game("x", "Empty", {})), [])

    def test_every_table_hidden_offers_nothing(self) -> None:
        game = self._game_with_path({"a": _table("a", "a.vpx", hidden=True)})
        self.assertEqual(visible_entries(game), [])

    def test_every_table_absent_offers_nothing(self) -> None:
        """The file is gone, so there is nothing to launch."""
        game = self._game_with_path({"a": _table("a", "a.vpx", absent=True)})
        self.assertEqual(visible_entries(game), [])

    def test_an_absent_table_is_dropped_and_its_siblings_stay(self) -> None:
        game = self._game_with_path({"a": _table("a", "a.vpx"),
                                     "b": _table("b", "b.vpx", absent=True)})
        self.assertEqual([e["filename"] for e in visible_entries(game)], ["a.vpx"])

    def test_a_game_nothing_has_parsed_still_offers_the_scan_s_vpx(self) -> None:
        """No tables section at all is the unparsed case, and it keeps its fallback."""
        game = self._game_with_path({})
        self.assertEqual([e["filename"] for e in visible_entries(game)], ["a.vpx"])


class OrderDefaultTests(TempTree):
    """A collection curated before curated order existed was shown alphabetically.
    Honouring its insertion order would reshuffle a list the user is used to, so
    `manual` is opt-in and everything else falls back to title."""

    def setUp(self) -> None:
        super().setUp()
        self.collections = CollectionStore(str(self.root / "collections.json"))
        self.games = [
            _game("zz", "Zaccaria", {"a": _table("a", "z.vpx")}),
            _game("aa", "Apollo 13", {"b": _table("b", "a.vpx")}),
            _game("mm", "Medieval Madness", {"c": _table("c", "m.vpx")}),
        ]
        self.collections.add_collection("Favorites")
        for gid in ("zz", "mm", "aa"):
            self.collections.add_member("Favorites", gid)

    def _titles(self):
        return [e.game.game_dir_name
                for e in resolve("Favorites", self.collections, self.games)]

    def test_a_manual_collection_defaults_to_title_order(self) -> None:
        self.assertEqual(self._titles(), ["Apollo 13", "Medieval Madness", "Zaccaria"])

    def test_saying_manual_uses_the_order_the_user_arranged(self) -> None:
        self.collections.set_order("Favorites", "manual")

        self.assertEqual(self._titles(), ["Zaccaria", "Medieval Madness", "Apollo 13"])

    def test_the_order_block_survives_a_round_trip(self) -> None:
        self.collections.set_order("Favorites", "manual")
        self.collections.save()

        reopened = CollectionStore(str(self.collections.path))
        self.assertEqual(reopened.get_order("Favorites"),
                         {"by": "manual", "direction": "asc", "paging_group": None})

    def test_a_filter_collections_stored_sort_still_applies(self) -> None:
        """Read from where it has always been stored, in the new vocabulary."""
        self.collections.add_filter_collection("Recent", sort_by="LastRun",
                                               order_by="Descending")

        self.assertEqual(self.collections.get_order("Recent"),
                         {"by": "last_played", "direction": "desc", "paging_group": None})


if __name__ == "__main__":
    unittest.main()
