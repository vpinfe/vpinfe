"""Changing a collection, and setting the order of its games.

Against the service rather than the routes: these are rules about what gets written, and
the route adds a path and a model to them and nothing else.

The order is the part worth pinning. A manual collection's member array *is* its order,
but only when the collection records `manual` - otherwise the resolver sorts by title and
the array is written and never read. That made reordering silently do nothing, and a
write nobody can see is worse than one that fails.
"""

from __future__ import annotations

import contextlib
import unittest

from common import service_errors
from common.games import collection_ops as ops
from common.games.collection_store import LAST_COLLECTION, MANUAL_ORDER
from tests.support.library import fake_game


class Manager:
    """Enough CollectionStore for the write operations."""

    def __init__(self, members, is_filter=False, name="Coll"):
        self.name = name
        self.members = list(members)
        self.filter = is_filter
        self.filters = {}
        self.excluded = []
        self.paging = None
        self.order = None
        self.direction = None
        self.limit = None
        self.image = None
        self.renamed_to = None

    # -- reads the routes make
    def get_collections_name(self):
        return [self.name]

    def is_filter_based(self, section):
        return self.filter

    def get_members(self, section):
        """Game ids, de-duplicated - what the store's own accessor returns."""
        seen = []
        for ref in self._refs():
            if ref["game"] not in seen:
                seen.append(ref["game"])
        return seen

    def get_member_refs(self, section):
        return [dict(ref) for ref in self._refs()]

    def _refs(self):
        return [m if isinstance(m, dict) else {"game": m} for m in self.members]

    # -- writes
    def add_member(self, section, game_id, table_id="", after_table=None):
        ref = {"game": game_id} | ({"table": table_id} if table_id else {})
        if ref in self._refs():
            return
        at = None
        if after_table is not None:
            sibling = {"game": game_id} | ({"table": after_table} if after_table else {})
            at = next((i for i, m in enumerate(self._refs()) if m == sibling), None)
        if at is None:
            self.members.append(ref)
        else:
            self.members.insert(at + 1, ref)

    def remove_member(self, section, game_id, table_id=""):
        self.members = [r for r in self._refs()
                        if not (r["game"] == game_id
                                and (not table_id or r.get("table") == table_id))]

    def get_excluded_refs(self, section):
        return [dict(r) for r in self.excluded]

    def exclude(self, section, game_id, table_id=""):
        ref = {"game": game_id} | ({"table": table_id} if table_id else {})
        if ref not in self.excluded:
            self.excluded.append(ref)

    def unexclude(self, section, game_id, table_id=""):
        self.excluded = [r for r in self.excluded
                         if not (r["game"] == game_id
                                 and (not table_id or r.get("table") == table_id))]

    def set_members(self, section, members):
        """Normalized to refs, as the store does - a caller may hand it either, and a
        fake that kept bare ids would let a route pass here and lose tables in real
        use."""
        self.members = [m if isinstance(m, dict) else {"game": m} for m in members]

    def set_order(self, section, by, direction="asc", paging_group=None):
        """Normalized as the store does it: anything it cannot read - "" included -
        is the collection saying nothing, and the key is dropped rather than stored."""
        from common.games.collection_store import normalize_paging_group
        self.order = by
        self.direction = direction
        self.paging = normalize_paging_group(paging_group)

    def get_order(self, section):
        return {"by": self.order or "title", "direction": self.direction or "asc",
                "paging_group": self.paging}

    def set_limit(self, section, limit):
        self.limit = limit

    def set_image(self, section, filename):
        self.image = filename

    def has_filters(self, section):
        return self.filter

    def clear_filters(self, section):
        self.filter = False
        self.filters = {}

    def rename_collection(self, old, new):
        self.renamed_to = new
        self.name = new

    def make_filter_collection(self, section, filters, order=None, limit=None):
        self.filter = True

    @contextlib.contextmanager
    def mutate(self):
        yield self


class Harness(unittest.TestCase):
    """The store and the library stood in front of the service.

    The rules under test are about what gets written, so what is stood in is everything
    that reads from disk. Restored in tearDown, or a test that relaxes the table check
    leaves it relaxed for every test after it - and `game_id` is worse, because that
    module is shared with everything else.
    """

    def use(self, manager, catalog=("g1", "g2", "g3")):
        self.manager = manager
        self._mgr = ops.get_collections_manager
        self._cat = ops.game_repository.catalog
        self._row = ops._row_or_refuse
        self._res = ops._resource_for
        self._one = ops._one_table_of
        self._res_fn = ops._resolved
        self._game_id = ops.game_identity.game_id
        ops.get_collections_manager = lambda: manager
        ops.game_repository.catalog = lambda: {
            g: fake_game(f"/games/{g}", f"Title {g}") for g in catalog}
        ops._row_or_refuse = lambda name: {"name": name}
        ops._resource_for = lambda row: {"name": row["name"]}

    def tearDown(self):
        if hasattr(self, "_mgr"):
            ops.get_collections_manager = self._mgr
            ops.game_repository.catalog = self._cat
            ops._row_or_refuse = self._row
            ops._resource_for = self._res
            ops._one_table_of = self._one
            ops._resolved = self._res_fn
            ops.game_identity.game_id = self._game_id


class OrderTests(Harness):
    def test_the_order_is_stored_and_recorded_as_manual(self):
        """Both halves. Storing the array without recording `manual` leaves the resolver
        sorting by title, which is the same as not having written it."""
        self.use(Manager(["g1", "g2", "g3"]))
        ops.set_arrangement("Coll", ["g3", "g1", "g2"])
        self.assertEqual(self.manager.members,
                         [{"game": "g3"}, {"game": "g1"}, {"game": "g2"}])
        self.assertEqual(self.manager.order, MANUAL_ORDER)

    def test_an_order_missing_a_member_is_refused(self):
        """Otherwise a dropped id is indistinguishable from a deliberate removal."""
        self.use(Manager(["g1", "g2", "g3"]))
        with self.assertRaises(service_errors.RefusedError):
            ops.set_arrangement("Coll", ["g1", "g2"])
        self.assertEqual(self.manager.members, ["g1", "g2", "g3"])

    def test_an_order_adding_a_member_is_refused(self):
        self.use(Manager(["g1", "g2"]))
        with self.assertRaises(service_errors.RefusedError):
            ops.set_arrangement("Coll", ["g1", "g2", "g3"])
        self.assertEqual(self.manager.members, ["g1", "g2"])

    def test_a_filter_collection_has_no_order_to_set(self):
        self.use(Manager(["g1"], is_filter=True))
        with self.assertRaises(service_errors.BlockedError):
            ops.set_arrangement("Coll", ["g1"])


class OrderKeepsNamedTablesTests(Harness):
    """A member names a game and optionally one of its tables. Rebuilding
    the array from bare ids threw every named table away - measured, a three-ref
    tournament list came back as two bare games on a 204.

    **The list is one entry per row, changed 2026-08-30.** It used to name each game
    once and move that game's refs as a block, which cannot express the thing 2.10 opens
    by asking for - *"multiple tables for a single game, in an arbitrary order"* - and
    the route said so itself. manager now draws one row per ref with its own handle, so a
    drag has to be able to move one of them; naming the game once could only move both.
    """

    def test_a_named_table_survives_a_reorder(self):
        self.use(Manager([{"game": "g1", "table": "t1"}, {"game": "g2"}]))
        ops.set_arrangement("Coll", ["g2", "g1"])
        self.assertEqual(self.manager.members,
                         [{"game": "g2"}, {"game": "g1", "table": "t1"}])

    def test_two_tables_of_one_game_can_be_placed_apart(self):
        """Naming the game at two positions
        deals its refs out one each, in their own stored order."""
        self.use(Manager([{"game": "g1", "table": "t1"}, {"game": "g2"},
                          {"game": "g1", "table": "t2"}]))
        ops.set_arrangement("Coll", ["g1", "g1", "g2"])
        self.assertEqual(self.manager.members,
                         [{"game": "g1", "table": "t1"},
                          {"game": "g1", "table": "t2"},
                          {"game": "g2"}])

    def test_the_membership_check_counts_refs_not_games(self):
        """A row is a ref, so a game holding two of them is named twice. Listing it once
        is a short order, and a short order drops what it left out - which is the silent
        removal this check exists to refuse."""
        self.use(Manager([{"game": "g1", "table": "t1"}, {"game": "g1", "table": "t2"}]))
        with self.assertRaises(service_errors.RefusedError):
            ops.set_arrangement("Coll", ["g1"])
        self.assertEqual(len(self.manager.members), 2, "and nothing was written")


class PatchTests(Harness):
    def test_a_rename_leaves_everything_else_alone(self):
        self.use(Manager(["g1", "g2"]))
        ops.patch("Coll", new_name="Renamed")
        self.assertEqual(self.manager.renamed_to, "Renamed")
        self.assertEqual(self.manager.members, ["g1", "g2"])
        self.assertIsNone(self.manager.limit)

    def test_membership_replaces_in_the_order_given(self):
        self.use(Manager(["g1"]))
        ops.patch("Coll", games=["g3", "g1"])
        self.assertEqual(self.manager.members, [{"game": "g3"}, {"game": "g1"}])

    def test_an_unknown_game_is_refused_and_nothing_is_written(self):
        self.use(Manager(["g1"]))
        with self.assertRaises(service_errors.RefusedError):
            ops.patch("Coll", games=["g1", "nope"])
        self.assertEqual(self.manager.members, ["g1"])

    def test_games_and_filters_together_are_written(self):
        """Both, in one patch. They are combinable and the resolver
        applies members over what the criteria matched - refusing the pair was this API
        carrying 2.x's two kinds forward."""
        self.use(Manager(["g1"]))
        ops.patch("Coll", games=["g1"], criteria={"letter": "All"})
        self.assertEqual(self.manager.members, [{"game": "g1"}])
        self.assertTrue(self.manager.filter)

    def test_a_cap_below_one_is_refused(self):
        self.use(Manager(["g1"]))
        with self.assertRaises(service_errors.RefusedError):
            ops.patch("Coll", limit=0)
        self.assertIsNone(self.manager.limit)

    def test_clear_limit_lifts_the_cap(self):
        """Absent and null are the same thing over JSON, so lifting a cap needs a word
        of its own rather than a null nobody can distinguish."""
        self.use(Manager(["g1"]))
        ops.patch("Coll", clear_limit=True)
        self.assertIsNone(self.manager.limit)

    def test_membership_can_be_set_on_a_collection_that_filters(self):
        """A member of a collection that also carries criteria states what the
        collection holds for that game, whether or not the criteria matched it."""
        self.use(Manager(["g1"], is_filter=True))
        ops.patch("Coll", games=["g2", "g1"])
        self.assertEqual(self.manager.members, [{"game": "g2"}, {"game": "g1"}])

    def test_taking_the_rules_away_keeps_what_was_added(self):
        manager = Manager(["g1"], is_filter=True)
        manager.excluded = [{"game": "g2"}]
        manager.limit = 5
        self.use(manager)
        ops.patch("Coll", clear_criteria=True)
        self.assertFalse(self.manager.filter)
        self.assertEqual(self.manager.members, ["g1"])
        self.assertEqual(self.manager.excluded, [])
        self.assertEqual(self.manager.limit, 5)

    def test_rules_and_no_rules_at_once_are_refused(self):
        self.use(Manager(["g1"], is_filter=True))
        with self.assertRaises(service_errors.RefusedError):
            ops.patch("Coll", criteria={"letter": "A"}, clear_criteria=True)
        self.assertTrue(self.manager.filter)

    def test_renaming_onto_an_existing_name_is_refused(self):
        manager = Manager(["g1"])
        manager.get_collections_name = lambda: ["Coll", "Taken"]
        self.use(manager)
        with self.assertRaises(service_errors.BlockedError):
            ops.patch("Coll", new_name="Taken")
        self.assertIsNone(manager.renamed_to)

    def test_a_collection_cannot_be_named_for_the_open_on_choice(self):
        self.use(Manager(["g1"]))
        for name in (LAST_COLLECTION, LAST_COLLECTION.upper(), f"  {LAST_COLLECTION} "):
            with self.subTest(name=name), self.assertRaises(service_errors.BlockedError):
                ops.create(name)

    def test_renaming_to_the_open_on_choice_is_refused(self):
        manager = Manager(["g1"])
        self.use(manager)
        with self.assertRaises(service_errors.BlockedError):
            ops.patch("Coll", new_name=LAST_COLLECTION.lower())
        self.assertIsNone(manager.renamed_to)

    def test_a_collection_that_is_gone(self):
        manager = Manager(["g1"])
        manager.get_collections_name = lambda: []
        self.use(manager)
        with self.assertRaises(service_errors.NotFoundError):
            ops.patch("Coll", new_name="x")


class PatchOrderTests(Harness):
    """A collection's order, set without restating anything else.

    Before this existed the only way a manual collection got an order was by being
    arranged, so every other sort was unreachable for one - the field was on the
    resource and there was no route that wrote it.
    """

    def test_the_order_is_written(self):
        self.use(Manager(["g1"]))
        ops.patch("Coll", order_by="year",
                                                            direction="desc")
        self.assertEqual(self.manager.order, "year")
        self.assertEqual(self.manager.direction, "desc")

    def test_a_direction_alone_keeps_the_field(self):
        """Only what is sent is written, so a direction must not reset the sort."""
        manager = Manager(["g1"])
        manager.order = "rating"
        self.use(manager)
        ops.patch("Coll", direction="desc")
        self.assertEqual(self.manager.order, "rating")
        self.assertEqual(self.manager.direction, "desc")

    def test_a_field_nothing_sorts_by_is_refused(self):
        self.use(Manager(["g1"]))
        with self.assertRaises(service_errors.RefusedError):
            ops.patch("Coll", order_by="sideways")
        self.assertIsNone(self.manager.order)

    def test_manual_is_offered_where_there_is_an_arrangement(self):
        self.use(Manager(["g1"]))
        ops.patch("Coll", order_by="manual")
        self.assertEqual(self.manager.order, "manual")

    def test_a_filter_collection_has_no_arrangement_to_follow(self):
        """`manual` is the stored member array, and a filter collection has none - so
        the order would name something that does not exist."""
        self.use(Manager(["g1"], is_filter=True))
        with self.assertRaises(service_errors.BlockedError):
            ops.patch("Coll", order_by="manual")
        self.assertIsNone(self.manager.order)

    def test_an_order_beside_filters_is_the_one_that_wins(self):
        """Sent together, the explicit order is the answer - not the one carried in
        the filter block, which is where a filter collection's order also lives."""
        self.use(Manager(["g1"]))
        ops.patch("Coll", criteria={"letter": "All"},
                  criteria_order={"by": "title", "direction": "asc"},
                  order_by="last_played", direction="desc")
        self.assertEqual(self.manager.order, "last_played")
        self.assertEqual(self.manager.direction, "desc")

    def test_saying_nothing_about_order_writes_nothing(self):
        self.use(Manager(["g1"]))
        ops.patch("Coll", new_name="Renamed")
        self.assertIsNone(self.manager.order)


class NamedTableTests(Harness):
    """A member names a game, or it names a table. Until the route
    took a table it could only ever say the first."""

    def _tables(self, *ids):
        ops._one_table_of = lambda game_id, table_id: (
            None if not table_id or table_id in ids
            else (_ for _ in ()).throw(service_errors.NotFoundError(f"no table {table_id}")))

    def test_a_member_can_name_one_table(self):
        self.use(Manager([]))
        self._tables("t1")
        ops.add_member("Coll", "g1", "t1")
        self.assertEqual(self.manager.members, [{"game": "g1", "table": "t1"}])

    def test_no_table_names_the_game(self):
        """Which resolves to whichever table is its default, so the collection follows
        a replacement rather than holding the one that was there when it was added."""
        self.use(Manager([]))
        self._tables()
        ops.add_member("Coll", "g1")
        self.assertEqual(self.manager.members, [{"game": "g1"}])

    def test_two_tables_of_one_game_are_two_members(self):
        self.use(Manager([]))
        self._tables("t1", "t2")
        ops.add_member("Coll", "g1", "t1")
        ops.add_member("Coll", "g1", "t2")
        self.assertEqual(self.manager.members,
                         [{"game": "g1", "table": "t1"}, {"game": "g1", "table": "t2"}])

    def test_removing_without_a_table_removes_every_ref_for_the_game(self):
        self.use(Manager([{"game": "g1", "table": "t1"},
                          {"game": "g1", "table": "t2"}, {"game": "g2"}]))
        ops.remove_member("Coll", "g1")
        self.assertEqual(self.manager.members, [{"game": "g2"}])

    def test_removing_one_named_table_leaves_the_other(self):
        self.use(Manager([{"game": "g1", "table": "t1"},
                          {"game": "g1", "table": "t2"}]))
        ops.remove_member("Coll", "g1", table="t1")
        self.assertEqual(self.manager.members, [{"game": "g1", "table": "t2"}])

    def test_removing_something_that_is_not_a_member(self):
        self.use(Manager([{"game": "g1"}]))
        with self.assertRaisesRegex(service_errors.NotFoundError, "^Title g2 is not in Coll$"):
            ops.remove_member("Coll", "g2")

    def test_a_game_the_library_does_not_hold_is_named_by_its_id(self):
        self.use(Manager([{"game": "g1"}]))
        with self.assertRaisesRegex(service_errors.NotFoundError, "^gone is not in Coll$"):
            ops.remove_member("Coll", "gone")


class ExclusionTests(Harness):
    """The other half of naming a table. It freezes a choice; excluding
    one says "everything except this" and keeps tracking what is added later. Neither
    substitutes for the other, and only naming had a route."""

    def test_a_game_can_be_excluded(self):
        self.use(Manager([]))
        ops._one_table_of = lambda game_id, table_id: None
        ops.exclude("Coll", "g1")
        self.assertEqual(self.manager.excluded, [{"game": "g1"}])

    def test_one_table_can_be_excluded(self):
        self.use(Manager([]))
        ops._one_table_of = lambda game_id, table_id: None
        ops.exclude("Coll", "g1", "t2")
        self.assertEqual(self.manager.excluded, [{"game": "g1", "table": "t2"}])

    def test_excluding_twice_is_idempotent(self):
        self.use(Manager([]))
        ops._one_table_of = lambda game_id, table_id: None
        ops.exclude("Coll", "g1")
        ops.exclude("Coll", "g1")
        self.assertEqual(self.manager.excluded, [{"game": "g1"}])

    def test_an_exclusion_can_be_lifted(self):
        self.use(Manager([]))
        ops._one_table_of = lambda game_id, table_id: None
        ops.exclude("Coll", "g1")
        ops.unexclude("Coll", "g1")
        self.assertEqual(self.manager.excluded, [])

    def test_lifting_one_that_is_not_there(self):
        self.use(Manager([]))
        with self.assertRaisesRegex(service_errors.NotFoundError,
                                    "^Title g1 is not excluded from Coll$"):
            ops.unexclude("Coll", "g1")


class KeepTheResultTests(Harness):
    """Criteria as a way of building a list rather than a rule to keep: what they match
    becomes the membership, and the criteria go."""

    def _resolves_to(self, *pairs):
        class _Entry:
            def __init__(self, game, table):
                self.game, self.table, self.table_id = game, {"id": table}, table
        ops._resolved = lambda name: [_Entry(g, t) for g, t in pairs]  # type: ignore[misc]
        ops.game_identity.game_id = lambda game: game  # type: ignore[return-value]

    def test_the_matches_become_members_following_their_default(self):
        self.use(Manager([], is_filter=True))
        self._resolves_to(("g1", "t1"), ("g2", "t2"))
        ops.keep_result("Coll")
        self.assertEqual(self.manager.members, [{"game": "g1"}, {"game": "g2"}])

    def test_a_table_somebody_held_stays_held(self):
        self.use(Manager([{"game": "g1", "table": "t1"}], is_filter=True))
        self._resolves_to(("g1", "t1"), ("g2", "t2"))
        ops.keep_result("Coll")
        self.assertEqual(self.manager.members,
                         [{"game": "g1", "table": "t1"}, {"game": "g2"}])

    def test_a_game_found_on_two_tables_is_one_member(self):
        self.use(Manager([], is_filter=True))
        self._resolves_to(("g1", "t1"), ("g1", "t2"))
        ops.keep_result("Coll")
        self.assertEqual(self.manager.members, [{"game": "g1"}])

    def test_the_criteria_are_dropped(self):
        """Which is what makes the collection static - it stops changing under its
        owner, and only then is a hand arrangement a coherent thing to store."""
        self.use(Manager([], is_filter=True))
        self._resolves_to(("g1", "t1"))
        ops.keep_result("Coll")
        self.assertFalse(self.manager.filter)

    def test_the_cap_is_lifted(self):
        """It capped the rule's output. The membership now *is* that output, so leaving
        it in place would cut the same list a second time."""
        manager = Manager([], is_filter=True)
        manager.limit = 3
        self.use(manager)
        self._resolves_to(("g1", "t1"))
        ops.keep_result("Coll")
        self.assertIsNone(self.manager.limit)

    def test_exclusions_go_with_the_criteria(self):
        """They said "everything except this" about a rule. With no rule left there is
        nothing to except, and keeping them would subtract from a hand-edited list."""
        manager = Manager([], is_filter=True)
        manager.excluded = [{"game": "g9"}]
        self.use(manager)
        self._resolves_to(("g1", "t1"))
        ops.keep_result("Coll")
        self.assertEqual(self.manager.excluded, [])

    def test_a_collection_with_no_criteria_has_no_result_to_keep(self):
        self.use(Manager(["g1"]))
        with self.assertRaises(service_errors.BlockedError):
            ops.keep_result("Coll")


class PagingGroupTests(Harness):
    def test_a_group_is_written(self):
        self.use(Manager(["g1"]))
        ops.patch("Coll", paging_group="sort")
        self.assertEqual(self.manager.paging, "sort")

    def test_nonsense_is_refused_rather_than_normalized_away(self):
        """`normalize_paging_group` answers None for anything unreadable, so accepting
        this would turn a typo into "follow the player" and report success."""
        self.use(Manager(["g1"]))
        with self.assertRaises(service_errors.RefusedError):
            ops.patch("Coll", paging_group="sideways")

    def test_empty_clears_it(self):
        """"" is a value here - it says follow the player - so it is not the same as
        saying nothing about paging at all."""
        manager = Manager(["g1"])
        manager.paging = "sort"
        self.use(manager)
        ops.patch("Coll", paging_group="")
        self.assertIsNone(self.manager.paging)


if __name__ == "__main__":
    unittest.main()
