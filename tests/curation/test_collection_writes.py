"""Two surfaces writing collections do not lose each other's edit.

The whole file is rewritten on every save, so a writer that read it before another one
saved is holding a stale copy - and writing that copy back drops the other's collection
while reporting success. The theme's collection menu, the API and the
launch tracker all write the same file through stores of their own, so the window is
reachable from any pair of them.

`mutate` is what closes it: reload and save happen under one lock, so a writer's copy is
current for as long as it is being edited.
"""

from __future__ import annotations

import threading
import unittest
from pathlib import Path

from common.games.collection_store import LAST_COLLECTION, CollectionStore
from tests.support.library import TempTree

FILTERS = ("All", "All", "All", "All", "All", "false", "Alpha", "Ascending")


class CollectionWriteTests(TempTree):
    def setUp(self) -> None:
        super().setUp()
        self.path = Path(self.root) / "collections.json"

    def _store(self) -> CollectionStore:
        """A caller's own store, which is what each surface builds for itself."""
        return CollectionStore(str(self.path))

    def _names(self) -> list[str]:
        return self._store().get_collections_name()

    def test_an_edit_made_against_a_stale_copy_is_not_written_back(self) -> None:
        """The two-writer case, in the order that loses one: both read, both then save."""
        first, second = self._store(), self._store()
        with first.mutate() as store:
            store.add_filter_collection("From the theme", "A", *FILTERS)
        with second.mutate() as store:
            store.add_filter_collection("From the API", "B", *FILTERS)

        self.assertEqual(self._names(), ["From the theme", "From the API"])

    def test_concurrent_writers_all_survive(self) -> None:
        """Started together so they genuinely overlap rather than run in turn."""
        ready = threading.Barrier(12)

        def write(index: int) -> None:
            store = self._store()
            ready.wait()
            with store.mutate() as opened:
                opened.add_filter_collection(f"c{index:02d}", "A", *FILTERS)

        threads = [threading.Thread(target=write, args=(i,)) for i in range(12)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(sorted(self._names()), [f"c{i:02d}" for i in range(12)])

    def test_the_lock_alone_would_not_have_been_enough(self) -> None:
        """Serialising the writes without re-reading still loses one: the second store
        read the file before the first wrote it. This is the shape the code had, and it
        is why `mutate` reloads rather than only locking."""
        first, second = self._store(), self._store()
        first.add_filter_collection("From the theme", "A", *FILTERS)
        second.add_filter_collection("From the API", "B", *FILTERS)
        first.save()
        second.save()

        self.assertEqual(self._names(), ["From the API"])

    def test_raising_inside_a_block_writes_nothing(self) -> None:
        """What lets a caller validate against the just-reloaded file and refuse."""
        with self._store().mutate() as store:
            store.add_filter_collection("kept", "A", *FILTERS)

        with self.assertRaises(ValueError):
            with self._store().mutate() as store:
                store.add_filter_collection("rolled back", "B", *FILTERS)
                raise ValueError("a route refusing after it looked")

        self.assertEqual(self._names(), ["kept"])

    def test_no_way_in_names_a_collection_for_the_open_on_choice(self) -> None:
        store = self._store()
        store.add_collection("Mine", ["g1"])
        attempts = (
            lambda: store.add_collection(LAST_COLLECTION),
            lambda: store.add_filter_collection(LAST_COLLECTION.upper(), "A", *FILTERS),
            lambda: store.copy_collection("Mine", f" {LAST_COLLECTION} "),
            lambda: store.rename_collection("Mine", LAST_COLLECTION.lower()),
        )
        for attempt in attempts:
            with self.subTest(), self.assertRaises(ValueError):
                attempt()

        self.assertEqual(store.get_collections_name(), ["Mine"])


if __name__ == "__main__":
    unittest.main()
