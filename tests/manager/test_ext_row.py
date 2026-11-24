"""An extension's list opens a pane on one row: its address, its rail and its facts."""

from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import patch

from nicegui import ui

from manager.extensions import ext_lists, ext_row
from manager.kit import deeplink

EXTENSION = {"name": "weekly"}
DECLARED = {"key": "board", "detail": "/lists/board/detail"}
VIEW = "community:weekly:board"


class TheAddress(unittest.TestCase):
    def test_the_view_prefix_is_the_one_the_list_is_named_with(self) -> None:
        self.assertEqual(deeplink.COMMUNITY_PREFIX, ext_lists.PREFIX)

    def test_an_open_row_is_in_the_address_of_an_extension_list(self) -> None:
        state = {"view": VIEW, "ext_row": "mm"}

        self.assertEqual(f"view={VIEW}&row=mm", deeplink.query(state))

    def test_a_row_means_nothing_on_a_core_page(self) -> None:
        self.assertEqual("view=games", deeplink.query({"view": "games", "ext_row": "mm"}))

    def test_the_address_seeds_the_row_on_an_extension_list_only(self) -> None:
        state: dict[str, Any] = {}
        deeplink.apply(state, {"view": VIEW, "row": " mm "}, views=[VIEW], sections=[])
        self.assertEqual("mm", state["ext_row"])

        other: dict[str, Any] = {}
        deeplink.apply(other, {"view": "games", "row": "mm"}, views=["games"], sections=[])
        self.assertNotIn("ext_row", other)

    def test_a_row_id_is_escaped_in_the_route(self) -> None:
        self.assertEqual("/ext/weekly/lists/board/detail/a%2Fb%20c",
                         ext_row.route(EXTENSION, DECLARED, "a/b c"))

    def test_the_grid_is_named_for_its_extension_and_list(self) -> None:
        self.assertEqual("manager.community.weekly.board",
                         ext_lists.scope_of(EXTENSION, DECLARED))


class TheRow(unittest.TestCase):
    def test_a_row_the_last_read_holds_is_the_one_found_by_its_id(self) -> None:
        held = {"rows": [{"id": "afm", "name": "Attack"}, {"id": "mm", "name": "Medieval"}]}
        with patch.object(ext_lists, "kept", return_value=held):
            self.assertEqual({"id": "mm", "name": "Medieval"},
                             ext_row.row_of(EXTENSION, DECLARED, "mm"))

    def test_a_row_that_read_left_out_is_just_its_id(self) -> None:
        with patch.object(ext_lists, "kept", return_value={"rows": None}):
            self.assertEqual({"id": "gone"}, ext_row.row_of(EXTENSION, DECLARED, "gone"))


class TheRail(unittest.TestCase):
    def test_each_section_is_prefixed_and_labelled_by_the_extension(self) -> None:
        found = {"sections": [{"key": "about", "label": "About"},
                              {"key": "mine", "label": "Yours"}]}

        rail = ext_row.sections({}, found)

        self.assertEqual(["ext:about", "ext:mine"], [one.key for one in rail])
        self.assertEqual(["About", "Yours"], [one.label({}) for one in rail])
        self.assertTrue(all(ext_row.SUBJECT in one.subjects for one in rail))

    def test_an_answer_with_no_sections_has_no_rail(self) -> None:
        self.assertEqual((), ext_row.sections({}, {"sections": []}))


class TheFacts(unittest.TestCase):
    def test_a_number_fact_is_its_text(self) -> None:
        self.assertEqual("7", ext_row._fact_value({"kind": "number", "value": 7}, {}))

    def test_a_link_that_cannot_be_filled_is_left_as_its_words(self) -> None:
        fact = {"kind": "link", "value": "Rules", "to": "ftp://nowhere"}

        self.assertEqual("Rules", ext_row._fact_value(fact, {"state": {}}))

    def test_a_link_leaves_without_an_opener_or_a_referrer(self) -> None:
        fact = {"kind": "link", "value": "Rules", "to": "https://example.com/rules"}
        with ui.column() as body:
            ext_row._fact_value(fact, {"state": {}})()
        link = next(one for one in body.descendants() if isinstance(one, ui.link))

        self.assertEqual("noopener noreferrer", link.props["rel"])


if __name__ == "__main__":
    unittest.main()
