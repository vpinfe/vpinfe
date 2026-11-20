"""Protects: (b) where an extension's panel sits in the navigation and how an address finds it."""

from __future__ import annotations

import unittest

from manager import page
from manager.extensions import ext_lists, ext_page, ext_pages, ext_sections
from manager.kit import deeplink

DECLARED = {"key": "panel", "title": "Site", "area": "community", "icon": ""}
LOADED = {"name": "site", "state": "loaded", "display_name": "Site", "panels": [DECLARED],
          "sections": [{"key": "week", "title": "This week"}]}
VIEW = "panel:site"


class TheNav(unittest.TestCase):
    def test_a_running_extensions_panel_sits_under_community(self) -> None:
        items = ext_lists.nav_items([LOADED, {**LOADED, "name": "off", "state": "failed"}])
        groups = dict(page.nav_for(None, items))

        self.assertEqual([VIEW], [one[0] for one in groups[page.NAV_COMMUNITY]])

    def test_a_panel_in_system_sits_before_about(self) -> None:
        system = {**DECLARED, "area": "system"}
        items = ext_lists.nav_items([{**LOADED, "panels": [system]}])
        keys = [one[0] for one in dict(page.nav_for(None, items))[page.NAV_SYSTEM]]

        self.assertEqual([VIEW, "about"], keys[-2:])

    def test_a_panel_follows_the_extensions_lists_in_its_area(self) -> None:
        listed = {"key": "plays", "title": "Plays", "area": "community", "columns": [],
                  "views": [], "verbs": [], "add": [], "detail": False}
        items = ext_lists.nav_items([{**LOADED, "lists": [listed]}])
        keys = [one[0] for one in dict(page.nav_for(None, items))[page.NAV_COMMUNITY]]

        self.assertEqual(["community:site:plays", VIEW], keys)


class TheAddress(unittest.TestCase):
    def test_an_address_in_other_case_finds_the_same_view(self) -> None:
        state: dict = {}

        deeplink.apply(state, {"view": "GAMES"}, views=["games"], sections=[])

        self.assertEqual("games", state["view"])

    def test_an_address_names_a_section_the_extension_declared(self) -> None:
        state: dict = {}
        address = ext_page.address("site", "week")

        params = dict(item.split("=") for item in address.split("?", 1)[1].split("&"))
        deeplink.apply(state, params, views=["extensions"],
                       sections=ext_sections.keys([LOADED]))

        self.assertEqual(("extensions", "site", "own:week"),
                         (state["view"], state["extension"], state["section"]))

    def test_a_section_the_extension_never_declared_is_dropped(self) -> None:
        state: dict = {}

        deeplink.apply(state, {"view": "extensions", "extension": "site",
                               "section": "own:other"}, views=["extensions"],
                       sections=ext_sections.keys([LOADED]))

        self.assertNotIn("section", state)


class Finding(unittest.TestCase):
    def test_a_view_finds_the_panel_it_names(self) -> None:
        self.assertEqual(ext_pages.find(VIEW, [LOADED]), (LOADED, DECLARED))

    def test_a_list_view_or_an_unknown_one_finds_none(self) -> None:
        self.assertIsNone(ext_pages.find("community:site:week", [LOADED]))
        self.assertIsNone(ext_pages.find("panel:other", [LOADED]))

    def test_an_extension_that_is_not_running_has_no_panel(self) -> None:
        self.assertIsNone(ext_pages.find(VIEW, [{**LOADED, "state": "failed"}]))

    def test_is_panel_reads_the_view_key(self) -> None:
        self.assertTrue(ext_pages.is_panel(VIEW))
        self.assertFalse(ext_pages.is_panel("community:site:week"))


if __name__ == "__main__":
    unittest.main()
