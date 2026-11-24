"""A collection can cap how many rows it keeps.

`limit` is an addition to schema 2 rather than a version of its own: absent means all
of them, which is every collection written before the key existed, so nothing migrates.
The resolver applies it last, and these pin the storage half.
"""

from __future__ import annotations

import json

from common.games.collection_store import CollectionStore
from tests.support.library import TempTree


class CollectionLimitTests(TempTree):
    def setUp(self) -> None:
        super().setUp()
        self.path = self.root / "collections.json"
        self.store = CollectionStore(str(self.path))
        self.store.add_filter_collection("Recent", sort_by="LastRun")

    def test_a_collection_has_no_limit_until_one_is_set(self) -> None:
        self.assertIsNone(self.store.get_limit("Recent"))

    def test_a_limit_round_trips_through_the_file(self) -> None:
        self.store.set_limit("Recent", 20)
        self.store.save()

        self.assertEqual(CollectionStore(str(self.path)).get_limit("Recent"), 20)

    def test_setting_none_lifts_the_cap_and_leaves_no_key(self) -> None:
        """Absent rather than null, so a file with no limits reads the same as one
        written before the key existed."""
        self.store.set_limit("Recent", 5)
        self.store.set_limit("Recent", None)
        self.store.save()

        record = json.loads(self.path.read_text())["collections"][0]
        self.assertNotIn("limit", record)

    def test_new_criteria_leave_the_limit_alone(self) -> None:
        self.store.set_limit("Recent", 20)

        self.store.make_filter_collection("Recent", {"played": True})

        self.assertEqual(self.store.get_limit("Recent"), 20)

    def test_new_criteria_given_a_limit_replace_it(self) -> None:
        self.store.set_limit("Recent", 20)

        self.store.make_filter_collection("Recent", {"played": True}, limit=30)

        self.assertEqual(self.store.get_limit("Recent"), 30)

    def test_a_limit_has_to_be_a_positive_count(self) -> None:
        for bad in (0, -1):
            with self.subTest(limit=bad), self.assertRaises(ValueError):
                self.store.set_limit("Recent", bad)

    def test_a_stored_value_that_is_not_a_count_reads_as_no_limit(self) -> None:
        """A hand-edited file should degrade to showing everything rather than raising
        on every resolve."""
        for junk in ("twenty", "", None, -3, 0):
            with self.subTest(stored=junk):
                self.store._require("Recent")["limit"] = junk
                self.assertIsNone(self.store.get_limit("Recent"))

    def test_a_builtin_collection_refuses_a_limit(self) -> None:
        """Capping the whole library hides most of it with nothing on screen to say
        why, and it is what everything else falls back to."""
        self.store._require("Recent")["builtin"] = True

        with self.assertRaises(ValueError):
            self.store.set_limit("Recent", 10)
