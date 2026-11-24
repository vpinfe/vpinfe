"""Where an extension says a game, a table or a file can be reached."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from common.extensions import catalogs
from common.extensions.context import ContractError, ExtensionCatalogs


class Registry(unittest.TestCase):
    def setUp(self) -> None:
        catalogs.clear()
        self.addCleanup(catalogs.clear)

    def test_a_link_answers_for_its_own_subject_only(self) -> None:
        catalogs.register("ext", "site", "Site", "table",
                          lambda one: f"https://site.example/{one['table_id']}")

        self.assertEqual(([], [{"key": "site", "name": "Site",
                                "url": "https://site.example/t1"}]),
                         (catalogs.links("game", {}),
                          catalogs.links("table", {"table_id": "t1"})))

    def test_nothing_to_link_is_no_row(self) -> None:
        catalogs.register("ext", "site", "Site", "game", lambda one: "")

        self.assertEqual([], catalogs.links("game", {}))

    def test_one_that_raises_costs_only_itself(self) -> None:
        def broken(one: dict) -> str:
            raise RuntimeError("down")

        catalogs.register("ext", "broken", "Broken", "game", broken)
        catalogs.register("ext", "fine", "Fine", "game", lambda one: "https://fine.example/")

        with self.assertLogs("vpinfe.common.extensions.catalogs", "ERROR"):
            found = catalogs.links("game", {"game_id": "g"})

        self.assertEqual(["fine"], [one["key"] for one in found])

    def test_only_a_web_address_is_drawn(self) -> None:
        catalogs.register("ext", "script", "Script", "game", lambda one: "javascript:alert(1)")

        self.assertEqual([], catalogs.links("game", {}))

    def test_a_subject_core_does_not_have_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            catalogs.register("ext", "list", "List", "collection", lambda one: "")

    def test_two_extensions_cannot_claim_one_key(self) -> None:
        catalogs.register("one", "site", "Site", "game", lambda one: "")

        with self.assertRaises(ValueError):
            catalogs.register("two", "site", "Site", "game", lambda one: "")

    def test_taking_an_extension_out_takes_its_links(self) -> None:
        catalogs.register("ext", "site", "Site", "game", lambda one: "https://site.example/")

        catalogs.forget("ext")

        self.assertEqual([], catalogs.links("game", {}))


class TheContract(unittest.TestCase):
    def setUp(self) -> None:
        catalogs.clear()
        self.addCleanup(catalogs.clear)

    def test_a_refusal_is_the_contract_s_own_error(self) -> None:
        with self.assertRaises(ContractError):
            ExtensionCatalogs("ext").contribute("site", "Site", "collection", MagicMock())

    def test_a_link_needs_a_key_and_a_name(self) -> None:
        with self.assertRaises(ContractError):
            ExtensionCatalogs("ext").contribute("site", " ", "game", MagicMock())


if __name__ == "__main__":
    unittest.main()
