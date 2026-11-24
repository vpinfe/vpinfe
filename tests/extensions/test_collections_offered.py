"""An extension's collection calls change the file an HTTP client's would, and are gated.

Against a real `collections.json` on disk, not a stand-in store.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpapi
from common import i18n
from common.extensions import host, serving, store
from common.extensions.contract import ContractError
from common.extensions.games import withdraw_all
from common.extensions.library_collections import ExtensionCollections
from common.games.collection_store import CollectionStore
from tests.support.accepted import owner_accepts
from tests.support.library import TempTree, fake_game, write_game

RECIPE = (Path(__file__).resolve().parents[2]
          / "docs" / "extensions" / "examples" / "recipes" / "weekend_picks")

IDS = ("Col000000001", "Col000000002", "Col000000003")


class CollectionsCase(TempTree):
    def setUp(self) -> None:
        super().setUp()
        withdraw_all()
        self.addCleanup(withdraw_all)
        self.store = CollectionStore(str(self.root / "collections.json"))
        games = {}
        for index, game_id in enumerate(IDS):
            folder = write_game(self.root, f"Table {index}",
                                info={"Info": {"Name": f"Table {index}"},
                                      "VPinFE": {"game_id": game_id}})
            games[game_id] = fake_game(folder, f"Table {index}")
        for target, value in (
            ("common.games.game_repository.catalog", lambda: games),
            ("common.games.collection_ops.get_collections_manager", lambda: self.store),
            ("common.games.collections_service.get_collections_manager",
             lambda: self.store),
        ):
            held = patch(target, value)
            held.start()
            self.addCleanup(held.stop)
        httpapi.create_api_app()
        self.ext = ExtensionCollections(
            "tester", ("collections:read", "collections:write"))

    def _members(self, name: str) -> list[str]:
        self.store.reload()
        return list(self.store.get_members(name))


class OfferedCollectionTests(CollectionsCase):
    def test_a_collection_made_by_an_extension_is_in_the_file_with_its_games(self) -> None:
        self.ext.create_collection("Williams", games=IDS[:2], description="Two of them")

        self.assertEqual(self._members("Williams"), list(IDS[:2]))

    def test_adding_and_removing_a_game_changes_the_members(self) -> None:
        self.ext.create_collection("Williams", games=IDS[:1])
        self.ext.add_to_collection("Williams", IDS[1])
        self.assertEqual(self._members("Williams"), list(IDS[:2]))

        self.ext.remove_from_collection("Williams", IDS[0])
        self.assertEqual(self._members("Williams"), [IDS[1]])

    def test_adding_a_game_already_in_it_succeeds_and_changes_nothing(self) -> None:
        self.ext.create_collection("Williams", games=IDS[:1])
        self.ext.add_to_collection("Williams", IDS[0])

        self.assertEqual(self._members("Williams"), [IDS[0]])

    def test_an_order_is_kept_as_given(self) -> None:
        self.ext.create_collection("Williams", games=IDS)
        self.ext.set_collection_order("Williams", [IDS[2], IDS[0], IDS[1]])

        self.assertEqual(self._members("Williams"), [IDS[2], IDS[0], IDS[1]])

    def test_a_rename_and_a_description_are_written(self) -> None:
        self.ext.create_collection("Williams", games=IDS[:1])
        self.ext.rename_collection("Williams", "Bally")
        self.ext.describe_collection("Bally", "Pinball 2000 and friends")
        self.store.reload()

        self.assertIn("Bally", self.store.get_collections_name())
        self.assertNotIn("Williams", self.store.get_collections_name())
        self.assertEqual(self.store.get_description("Bally"),
                         "Pinball 2000 and friends")

    def test_a_delete_removes_it(self) -> None:
        self.ext.create_collection("Williams", games=IDS[:1])
        self.ext.delete_collection("Williams")
        self.store.reload()

        self.assertNotIn("Williams", self.store.get_collections_name())

    def test_reading_lists_what_was_made(self) -> None:
        self.ext.create_collection("Williams", games=IDS[:2])

        names = [one["name"] for one in self.ext.list_collections()["collections"]]
        self.assertIn("Williams", names)
        self.assertEqual(len(self.ext.collection_members("Williams")["members"]), 2)

    def test_reading_alone_cannot_write(self) -> None:
        reader = ExtensionCollections("reader", ("collections:read",))

        with self.assertRaises(ContractError):
            reader.create_collection("Nope")
        self.assertEqual(reader.reaches(), (
            "collection_games", "collection_members", "get_collection", "list_collections"))

    def test_without_either_scope_nothing_is_reachable(self) -> None:
        bare = ExtensionCollections("bare", ())

        self.assertEqual(bare.reaches(), ())
        with self.assertRaises(ContractError):
            bare.list_collections()

    def test_only_the_listed_calls_are_offered(self) -> None:
        """Rules, exclusions and pictures stay out: a name added to the offers has to be
        added here too."""
        self.assertEqual(self.ext.reaches(), (
            "add_to_collection", "collection_games", "collection_members",
            "create_collection", "delete_collection", "describe_collection",
            "get_collection", "list_collections", "remove_from_collection",
            "rename_collection", "set_collection_order"))


class WeekendPicksRecipeTests(CollectionsCase):
    """The cookbook's recipe, loaded as the host loads it, over the same collections file."""

    def setUp(self) -> None:
        super().setUp()
        owner_accepts(self)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.registry = host.Registry(store.ExtensionStore(Path(tmp.name)))
        self.addCleanup(self.registry.clear)
        self.record = self.load(RECIPE)

    def load(self, folder: Path) -> host.Record:
        record = self.registry.load(folder)
        self.addCleanup(i18n.disown, f"ext.{record.name}")
        self.assertEqual(record.state, host.LOADED, record.error)
        return record

    def verb(self, key: str, *ids: str) -> dict:
        return serving.library_verb(self.record, "game", key, [{"game_id": one} for one in ids])

    def test_picking_makes_the_collection_and_a_second_pick_changes_nothing(self) -> None:
        first = self.verb("pick", IDS[0], IDS[1])
        again = self.verb("pick", IDS[1])

        self.assertEqual(first, {"message": "Weekend Picks now holds 2"})
        self.assertEqual(again, {"message": "Weekend Picks now holds 2"})
        self.assertEqual(self._members("Weekend Picks"), list(IDS[:2]))
        self.store.reload()
        self.assertEqual(self.store.get_description("Weekend Picks"),
                         "Games picked for the weekend")

    def test_taking_out_leaves_the_rest_and_says_so_where_there_is_no_collection(self) -> None:
        self.assertEqual(self.verb("unpick", IDS[0]),
                         {"message": "There is no collection called Weekend Picks yet"})
        self.verb("pick", *IDS)

        left = self.verb("unpick", IDS[1])

        self.assertEqual(left, {"message": "Weekend Picks now holds 2"})
        self.assertEqual(self._members("Weekend Picks"), [IDS[0], IDS[2]])

    def test_taking_out_a_game_that_was_never_picked_does_not_stop_the_rest(self) -> None:
        self.verb("pick", IDS[0], IDS[2])

        left = self.verb("unpick", IDS[1], IDS[0])

        self.assertEqual(left, {"message": "Weekend Picks now holds 1"})
        self.assertEqual(self._members("Weekend Picks"), [IDS[2]])

    def test_the_setting_names_the_collection_the_next_pick_goes_in(self) -> None:
        self.verb("pick", IDS[0])
        assert self.record.context is not None
        self.record.context.config.set("collection", "Late Night")

        self.verb("pick", IDS[2])

        self.assertEqual(self._members("Late Night"), [IDS[2]])
        self.assertEqual(self._members("Weekend Picks"), [IDS[0]])

    def test_without_the_write_scope_a_pick_is_refused_and_names_the_scope(self) -> None:
        folder = Path(tempfile.mkdtemp()) / "weekend_picks"
        self.addCleanup(shutil.rmtree, folder.parent, True)
        shutil.copytree(RECIPE, folder)
        manifest = json.loads((folder / "extension.json").read_text())
        manifest["scopes"].remove("collections:write")
        (folder / "extension.json").write_text(json.dumps(manifest))
        self.registry.clear()
        self.record = self.load(folder)

        with self.assertRaises(serving.ExtensionError) as refused:
            self.verb("pick", IDS[0])

        self.assertIn("collections:write", str(refused.exception))


if __name__ == "__main__":
    unittest.main()
