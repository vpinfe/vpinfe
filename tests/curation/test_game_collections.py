"""Which collections hold a game, and how."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from common.games import collection_ops
from common.games.collection_store import CollectionStore
from tests.curation.test_collection_resolver import _game, _table
from tests.support.library import TempTree


class WhichCollectionsHoldAGame(TempTree):
    def setUp(self) -> None:
        super().setUp()
        store = CollectionStore(str(self.root / "collections.json"))
        self.store = store
        store.add_filter_collection("Bally", manufacturer="Bally", sort_by="Alpha")
        store.add_member("Bally", "mm")
        store.add_filter_collection("First Bally", manufacturer="Bally", sort_by="Alpha")
        store.set_limit("First Bally", 1)
        games = {"mm": _game("mm", "Medieval Madness", {"v": _table("v", "MM.vpx")},
                             manufacturer="Williams"),
                 "afm": _game("afm", "Attack from Mars", {"a": _table("a", "AFM.vpx")},
                              manufacturer="Bally"),
                 "taf": _game("taf", "The Addams Family", {"t": _table("t", "TAF.vpx")},
                              manufacturer="Bally"),
                 "bk": _game("bk", "Black Knight", {"b": _table("b", "BK.vpx")},
                             manufacturer="Williams")}
        rows = [{"name": name, "is_filter": True} for name in store.get_collections_name()]
        replacements: tuple[tuple[str, Any], ...] = (
            ("get_collections_manager", lambda: store),
            ("get_collections_metadata", lambda: rows),
            ("_game_or_refuse", lambda game_id: None))
        for target, value in replacements:
            patcher = patch.object(collection_ops, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.object(collection_ops.game_repository, "catalog", lambda: games)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _held(self, game_id: str) -> list[tuple[str, str]]:
        return [(one["name"], one["how"])
                for one in collection_ops.collections_of(game_id)["collections"]]

    def test_a_game_written_in_is_added(self) -> None:
        self.assertEqual([("Bally", "added")], self._held("mm"))

    def test_a_game_the_rule_brought_in_is_matched(self) -> None:
        self.assertEqual([("Bally", "matched"), ("First Bally", "matched")],
                         self._held("afm"))

    def test_a_game_the_limit_cuts_is_not_held(self) -> None:
        self.assertEqual([("Bally", "matched")], self._held("taf"))

    def test_one_read_for_every_game_agrees_with_each_game_s_own(self) -> None:
        every = {one["game"]: one["collections"]
                 for one in collection_ops.game_collections()["games"]}
        for game_id in ("mm", "afm", "taf", "bk"):
            with self.subTest(game=game_id):
                self.assertEqual(collection_ops.collections_of(game_id)["collections"],
                                 every.get(game_id, []))

    def test_a_collection_keeping_it_out_is_listed_apart(self) -> None:
        self.store.exclude("Bally", "afm")

        found = collection_ops.collections_of("afm")

        self.assertEqual([("First Bally", "matched")],
                         [(one["name"], one["how"]) for one in found["collections"]])
        self.assertEqual(["Bally"], [one["name"] for one in found["taken_out"]])

    def test_a_game_nothing_keeps_out_is_taken_out_of_nothing(self) -> None:
        self.assertEqual([], collection_ops.collections_of("mm")["taken_out"])

    def test_a_game_nothing_holds_is_not_listed(self) -> None:
        listed = [one["game"] for one in collection_ops.game_collections()["games"]]
        self.assertEqual(["afm", "mm", "taf"], sorted(listed))
