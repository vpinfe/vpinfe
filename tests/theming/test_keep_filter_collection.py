from __future__ import annotations

import configparser
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from common.games import remote_library
from common.games.collection_store import CollectionStore
from frontend.library_resolver import LibraryResolver
from tests.support.library import TempTree

WILLIAMS = {"letter": "All", "theme": "All", "game_type": "All", "manufacturer": "Williams",
            "year": "1992", "rating": "All", "rating_or_higher": False}
LIBRARY = "http://library.example:8001"


def _ini(url: str = "") -> SimpleNamespace:
    config = configparser.ConfigParser()
    config.read_string(f"[general]\n[network]\nlibrary_url = {url}\n")
    return SimpleNamespace(config=config)


class LocalTests(TempTree):
    def setUp(self) -> None:
        super().setUp()
        self.store = CollectionStore(str(self.root / "collections.json"))
        for where in ("frontend.library_resolver.get_collections_manager",
                      "common.games.collections_service.get_collections_manager"):
            patcher = patch(where, lambda: self.store)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.library = LibraryResolver(_ini(), games=[])

    def _keep(self, name: str = "Williams, 1992", criteria: dict | None = None,
              order_by: str = "title", direction: str = "asc") -> tuple[str, bool]:
        return self.library.keep_filter(name, criteria or WILLIAMS, order_by, direction)

    def test_the_rules_are_saved_under_the_name_given(self) -> None:
        self.assertEqual(self._keep(), ("Williams, 1992", False))
        saved = self.store.get_filters("Williams, 1992")
        assert saved is not None
        self.assertEqual(saved["manufacturer"], "Williams")

    def test_the_same_rules_twice_is_one_collection(self) -> None:
        self._keep()

        self.assertEqual(self._keep(), ("Williams, 1992", True))
        self.assertEqual(self.store.get_collections_name(), ["Williams, 1992"])

    def test_the_same_rules_under_another_name_are_found(self) -> None:
        self._keep(name="My Williams")

        self.assertEqual(self._keep(), ("My Williams", True))

    def test_a_name_other_rules_hold_gets_the_next_number(self) -> None:
        self._keep(criteria={**WILLIAMS, "manufacturer": "Bally"})
        self._keep(criteria={**WILLIAMS, "manufacturer": "Stern"})

        self.assertEqual(self._keep(), ("Williams, 1992 3", False))

    def test_another_order_is_other_rules(self) -> None:
        self._keep()

        self.assertEqual(self._keep(direction="desc"), ("Williams, 1992 2", False))

    def test_games_added_by_hand_make_other_rules(self) -> None:
        self._keep()
        self.store.add_member("Williams, 1992", "mm")

        self.assertEqual(self._keep(), ("Williams, 1992 2", False))

    def test_a_hand_picked_collection_by_that_name_is_passed_over(self) -> None:
        self.store.add_collection("Williams, 1992", ["mm"])

        self.assertEqual(self._keep(), ("Williams, 1992 2", False))


class LibraryOverTheNetworkTests(unittest.TestCase):
    def setUp(self) -> None:
        self.listed: list[dict] = []
        self.created: list[dict] = []
        for patcher in (
                patch.object(remote_library.http_client, "get_json", self._get),
                patch.object(remote_library.http_client, "post_json", self._post)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.library = LibraryResolver(_ini(LIBRARY))

    def _get(self, url: str, **_: object) -> dict:
        assert url == f"{LIBRARY}/api/v1/collections", url
        return {"collections": self.listed}

    def _post(self, url: str, body: dict, **_: object) -> dict:
        self.created.append(body)
        return {"name": body["name"]}

    def _listed(self, name: str, manufacturer: list[str], **more: object) -> dict:
        return {"name": name, "type": "filter", "added": 0, "excluded": 0, "limit": None,
                "order_by": "title", "direction": "asc",
                "filters": {"letter": ["All"], "theme": ["All"], "game_type": ["All"],
                            "manufacturer": manufacturer, "year": ["1992"],
                            "rating": "All", "rating_or_higher": False, "played": None,
                            "favorite": None, "tags": ["All"], "year_range": None},
                **more}

    def test_the_same_rules_there_are_found_and_nothing_is_made(self) -> None:
        self.listed = [self._listed("Williams, 1992", ["Williams"])]

        kept = self.library.keep_filter("Williams, 1992", WILLIAMS, "title", "asc")

        self.assertEqual(kept, ("Williams, 1992", True))
        self.assertEqual(self.created, [])

    def test_a_name_other_rules_hold_there_is_made_under_the_next_number(self) -> None:
        self.listed = [self._listed("Williams, 1992", ["Bally"])]

        kept = self.library.keep_filter("Williams, 1992", WILLIAMS, "title", "asc")

        self.assertEqual(kept, ("Williams, 1992 2", False))
        self.assertEqual([body["name"] for body in self.created], ["Williams, 1992 2"])

    def test_a_limit_there_makes_other_rules(self) -> None:
        self.listed = [self._listed("Williams, 1992", ["Williams"], limit=20)]

        kept = self.library.keep_filter("Williams, 1992", WILLIAMS, "title", "asc")

        self.assertEqual(kept, ("Williams, 1992 2", False))


if __name__ == "__main__":
    unittest.main()
