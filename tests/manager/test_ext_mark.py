"""The mark on what an extension VPinFE did not ship put on screen: drawn for one that was
found, never for one that ships, and the same glyph wherever it is."""

from __future__ import annotations

import unittest
from typing import Any

from nicegui import ui

from common.security import accepted
from manager import page as app_page
from manager.extensions import ext_groups, ext_lists, ext_mark, ext_page, ext_verbs
from manager.kit import panel
from manager.overview import needs
from manager.settings import sections
from manager.workbench import core, rail

_PAGE = ui.element()


def _found(origin: str = accepted.IN_FOLDER, **fields: Any) -> dict[str, Any]:
    return {"name": "tally", "display_name": "Tally", "origin": origin, "library_verbs": [],
            **fields}


def _verb(key: str, label: str) -> dict[str, Any]:
    return {"subject": "game", "key": key, "label": label, "tip": "", "one": False,
            "danger": False, "confirm_key": "", "private_ok": False}


async def _nothing() -> None:
    return None


async def _nothing_built(context: dict[str, Any]) -> None:
    return None


def _glyphs(root: ui.element) -> list[ui.element]:
    return [one for one in root.descendants()
            if isinstance(one, ui.icon) and one.props.get("name") == ext_mark.GLYPH]


class Unofficial(unittest.TestCase):
    def test_what_was_found_is_marked_and_what_ships_is_not(self) -> None:
        self.assertTrue(ext_mark.unofficial(_found(accepted.IN_FOLDER)))
        self.assertTrue(ext_mark.unofficial(_found(accepted.REPLACES)))
        self.assertFalse(ext_mark.unofficial(_found(accepted.SHIPPED)))

    def test_a_record_that_says_nothing_of_its_origin_is_taken_as_shipped(self) -> None:
        self.assertFalse(ext_mark.unofficial({"name": "tally"}))
        self.assertFalse(ext_mark.unofficial(None))

    def test_the_tooltip_names_the_extension(self) -> None:
        self.assertEqual(ext_mark.tip(_found()), "Unofficial, from Tally")

    def test_nothing_is_drawn_for_one_that_ships(self) -> None:
        with _PAGE:
            self.assertIsNotNone(ext_mark.draw(_found()))
            self.assertIsNone(ext_mark.draw(_found(accepted.SHIPPED)))
        self.assertIsNone(ext_mark.drawer(_found(accepted.SHIPPED)))
        self.assertIsNone(ext_mark.drawer(None))

    def test_a_name_finds_its_extension_among_the_running(self) -> None:
        one, other = _found(), _found(name="other")
        self.assertIs(ext_mark.by_name([other, one], "tally"), one)
        self.assertIsNone(ext_mark.by_name([other], "tally"))


class InAMenu(unittest.TestCase):
    STATE = {"extensions": [
        {**_found(), "library_verbs": [_verb("a", "Ask")]},
        {**_found(accepted.SHIPPED), "name": "pins", "display_name": "Pins",
         "library_verbs": [_verb("p", "Pin")]},
    ]}

    def _offered(self) -> list[panel.Verb]:
        return ext_verbs.offered(self.STATE, "game", [{"game_id": "afm"}], _nothing)

    def test_only_a_block_from_an_extension_that_was_found_is_marked(self) -> None:
        self.assertEqual([(one.block, one.block_mark is not None) for one in self._offered()],
                         [("Pins", False), ("Tally", True)])

    def test_the_heading_of_a_marked_block_holds_one_glyph(self) -> None:
        with _PAGE, ui.menu() as menu:
            panel.verb_entries(self._offered())
        self.assertEqual(len(_glyphs(menu)), 1)


class InAPanel(unittest.TestCase):
    def test_a_group_heading_is_marked_only_for_an_extension_that_was_found(self) -> None:
        marked = ext_groups._heading(_found(), "Tally")
        plain = ext_groups._heading(_found(accepted.SHIPPED), "Tally")
        self.assertIs(marked[0][0], panel.OWNED)
        self.assertEqual(marked[0][1][0], "Tally")
        self.assertEqual(plain, [(panel.HEADING, "Tally")])

    def test_a_group_row_carries_a_mark_only_for_an_extension_that_was_found(self) -> None:
        group = {"key": "g", "title": "Tally"}
        self.assertIsNotNone(rail._group_row("g", _found(), group).mark)
        self.assertIsNone(rail._group_row("g", _found(accepted.SHIPPED), group).mark)

    def test_a_chip_with_a_glyph_draws_it_and_one_without_draws_none(self) -> None:
        with _PAGE:
            with_it = ui.column()
            with with_it:
                panel.state("Unofficial", "warn", glyph=ext_mark.GLYPH)()
            without = ui.column()
            with without:
                panel.state("Running", "on")()
        self.assertEqual(len(_glyphs(with_it)), 1)
        self.assertEqual(_glyphs(without), [])


class InTheRailAndSettings(unittest.TestCase):
    def test_a_rail_entry_is_owned_by_the_extension_that_declared_its_panel(self) -> None:
        panel_entry = {"key": "panel", "area": "community"}
        found = _found(state="loaded", panels=[panel_entry])
        shipped = _found(accepted.SHIPPED, name="pins", state="loaded", panels=[panel_entry])
        view = ext_lists.panel_view_key("tally")
        self.assertIs(app_page._owner_of(view, [shipped, found]), found)
        self.assertIsNone(app_page._owner_of("games", [found]))

    def test_the_origin_chip_carries_the_glyph(self) -> None:
        found = _found(trust={"origin": accepted.IN_FOLDER, "accepted": True, "folder": "tally"})
        chip = ext_page.origin_chip(found)
        assert chip is not None
        with _PAGE:
            holder = ui.column()
            with holder:
                chip()
        self.assertEqual(len(_glyphs(holder)), 1)
        self.assertIsNone(ext_page.origin_chip(
            _found(trust={"origin": accepted.SHIPPED, "accepted": True})))


class TheSourceShown(unittest.TestCase):
    def test_github_is_shown_as_owner_and_repo(self) -> None:
        self.assertEqual(ext_mark.source_shown("https://github.com/example/example"),
                         "example/example")

    def test_another_host_keeps_its_name(self) -> None:
        self.assertEqual(ext_mark.source_shown("https://git.example.net/example/example"),
                         "git.example.net/example/example")

    def test_a_value_that_is_not_an_address_is_shown_as_it_is(self) -> None:
        self.assertEqual(ext_mark.source_shown("not an address"), "not an address")


class InAFactList(unittest.TestCase):
    def test_an_owned_heading_draws_its_mark_before_the_words(self) -> None:
        drawn: list[str] = []
        with _PAGE:
            holder = ui.column()
            with holder:
                panel.facts(ui, [(panel.OWNED, ("Tally", lambda: drawn.append("mark")))])
        self.assertEqual(drawn, ["mark"])
        self.assertIn("Tally", [one.text for one in holder.descendants()
                                if isinstance(one, ui.label)])


class InASectionRow(unittest.TestCase):
    def test_the_row_draws_the_mark_the_section_carries(self) -> None:
        drawn: list[str] = []
        section = core.Section("tally", lambda context: "Tally", _nothing_built,
                                    mark=lambda: drawn.append("mark"))
        context: dict[str, Any] = {"redraws": []}
        with _PAGE:
            core._section_row(context, section, False)
        self.assertEqual(drawn, ["mark"])


class InNeedsYou(unittest.TestCase):
    STATE = {"extensions": [_found(), _found(accepted.SHIPPED, name="pins")]}

    def _drawn(self, owner: str) -> list[ui.element]:
        group = needs.Group(owner, "Heading", [])
        with _PAGE:
            holder = ui.column()
            with holder:
                sections._group_heading(group, self.STATE)
        return _glyphs(holder)

    def test_a_group_an_extension_raised_is_marked_if_it_was_found(self) -> None:
        self.assertEqual(len(self._drawn(needs.EXTENSION + "tally")), 1)

    def test_a_shipped_extension_s_group_and_core_s_are_not(self) -> None:
        self.assertEqual(self._drawn(needs.EXTENSION + "pins"), [])
        self.assertEqual(self._drawn(needs.LIBRARY), [])


if __name__ == "__main__":
    unittest.main()
