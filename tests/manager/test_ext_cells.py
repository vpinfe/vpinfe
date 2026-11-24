"""A game or a table an extension names in a pane, a page or a group: the library's words
where it holds the id, VPSdb's where it does not, and the fact's own where neither does.
"""

from __future__ import annotations

import unittest
from unittest.mock import Mock

from nicegui import ui

from manager.extensions import ext_cells, ext_lists, ext_row
from manager.kit import grid

TABLES = [{"id": "t1", "game_id": "afm", "game": "Attack from Mars",
           "manufacturer": "Bally", "year": 1995, "version": "1.2", "authors": ["Ann"],
           "media_kind": "wheel", "media_version": "v1", "default": True}]
HELD = {"vps-afm": {"game_id": "afm", "table_id": "t1", "name": "AFM"}}
GAME = {"label": "Machine", "kind": "game", "keys": "vps", "value": "vps-afm", "says": "",
        "picture": ""}


def _library(owned: dict | None = None) -> Mock:
    return Mock(table_rows=Mock(return_value=TABLES), thumbnail_look=Mock(return_value=None),
                owned_as=Mock(return_value=owned or {}))


def _section(*facts: dict) -> dict:
    return {"key": "about", "facts": list(facts)}


class WhatIsAsked(unittest.IsolatedAsyncioTestCase):
    async def test_nothing_is_read_where_no_fact_names_a_game_or_a_table(self) -> None:
        library = _library()
        context = {"library": library}

        found = await ext_cells.prepared(
            context, [_section({"label": "Plays", "kind": "number", "value": 3})])

        self.assertIs(context, found)
        library.owned_as.assert_not_called()

    async def test_each_kind_of_id_is_asked_once_with_every_id_of_it(self) -> None:
        library = _library()
        release = {**GAME, "kind": "table", "value": "rel-1"}

        found = await ext_cells.prepared(
            {"library": library},
            [_section(GAME, {**GAME, "value": "vps-b"}, release)])

        asked = sorted((call.args[1:], call.args[0]) for call in library.owned_as.call_args_list)
        self.assertEqual([(("vps", "game"), ["vps-afm", "vps-b"]),
                          (("vps", "table"), ["rel-1"])], asked)
        self.assertEqual({("vps", "game"), ("vps", "table")}, set(found["looks"]))

    async def test_a_fact_with_no_value_asks_nothing(self) -> None:
        library = _library()

        await ext_cells.prepared({"library": library}, [_section({**GAME, "value": ""})])

        library.owned_as.assert_not_called()

    async def test_no_library_leaves_the_context_as_it_was(self) -> None:
        context: dict = {}

        self.assertIs(context, await ext_cells.prepared(context, [_section(GAME)]))


class WhatIsDrawn(unittest.IsolatedAsyncioTestCase):
    async def drawn(self, fact: dict, owned: dict | None = None):
        context = await ext_cells.prepared({"library": _library(owned)}, [_section(fact)])
        return ext_cells.draw(fact, context)

    async def test_a_held_game_is_the_library_s_name_and_line_linked_to_the_game(self) -> None:
        cell = await self.drawn(GAME, {"owned": HELD})

        face, _picture, _look, _glyph, _hint = cell.args
        self.assertEqual(("Attack from Mars", "Bally 1995", "/manager?view=games&game=afm"),
                         (face["name"], face["line"], face["href"]))

    async def test_a_game_the_library_lacks_takes_vpsdb_s_name_with_no_link(self) -> None:
        cell = await self.drawn(GAME, {"known": {"vps-afm": {
            "name": "VPS name", "manufacturer": "Williams", "year": "1993"}}})

        face = cell.args[0]
        self.assertEqual(("VPS name", "Williams 1993", ""),
                         (face["name"], face["line"], face["href"]))

    async def test_an_id_nobody_knows_is_the_fact_s_words_and_then_the_id(self) -> None:
        said = await self.drawn({**GAME, "says": "Own words"})
        bare = await self.drawn(GAME)

        self.assertEqual(("Own words", "vps-afm"),
                         (said.args[0]["name"], bare.args[0]["name"]))

    async def test_the_fact_s_picture_stands_in_where_the_library_has_none(self) -> None:
        cell = await self.drawn({**GAME, "picture": "/api/v1/ext/x/pictures/abc"})

        self.assertEqual("/api/v1/ext/x/pictures/abc", cell.args[1])


class InTheValueSlot(unittest.TestCase):
    def test_a_game_fact_draws_its_name_and_its_line(self) -> None:
        context = {"looks": {("vps", "game"): ext_lists.Lookup(HELD, {}, {})},
                   "lens": ext_lists.Lens(TABLES, False), "look": None}
        with ui.column() as body:
            ext_row._fact_value(GAME, context)()
        link = next(one for one in body.descendants() if isinstance(one, ui.link))
        labels = [one.text for one in body.descendants() if isinstance(one, ui.label)]

        self.assertEqual(("Attack from Mars", ["Bally 1995"]), (link.text, labels))
        self.assertEqual("/manager?view=games&game=afm", link.props["href"])


class AColumnThatIsNotTheFirst(unittest.TestCase):
    def test_draws_a_name_and_its_line_and_no_art(self) -> None:
        column = grid.named("other", "Other", subtitle="other_line", link="other")

        self.assertIn(grid.TWO_LINE_CLASS, column["cellClass"])
        self.assertIn("d['other_line']", column[":cellRenderer"])
        self.assertIn("d['other_href']", column[":cellRenderer"])
        self.assertIn("const art = null;", column[":cellRenderer"])

    def test_reads_only_the_fields_it_was_given_however_they_are_spelled(self) -> None:
        field = "x'] || alert(1) || d['y"
        column = grid.named(field, "Other", subtitle=field + "_line", link=field)

        self.assertNotIn("d['x'] ||", column[":cellRenderer"])
        self.assertIn("d['x\\'] || alert(1) || d[\\'y_line']", column[":cellRenderer"])


if __name__ == "__main__":
    unittest.main()
