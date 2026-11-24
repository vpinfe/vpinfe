"""A tag an extension derives, as the Manager shows it: in the grids beside the user's
own, never offered by hand, moved by nobody but its extension, and offered as a
collection from its list."""

from __future__ import annotations

import inspect
import unittest
from typing import Any

from nicegui import ui

from common.timestamps import utc_now_iso
from manager.extensions import ext_lists
from manager.kit.data import Library, read_state, sources_of
from manager.library import collection_rules, games, tageditor
from manager.workbench import tag
from tests.support import lines

SOURCE = {"extension": "challenge", "display_name": "Challenge", "list": "releases",
          "title": "Weekly Challenge", "read_at": "2026-09-23T12:00:00Z", "stale": False}
DERIVED = "Weekly Challenge"


class _Client:
    def tags(self) -> list[dict[str, Any]]:
        return [{"name": "Wide Body", "games": 1, "tables": 0},
                {"name": "wide body", "games": 1, "tables": 0},
                {"name": DERIVED, "games": 0, "tables": 1, "sources": [SOURCE]},
                {"name": "weekly challenge", "games": 1, "tables": 0}]


def _library() -> Library:
    library = Library(_Client())  # type: ignore[arg-type]
    library.games = [{"id": "afm", "name": "Attack from Mars",
                      "user": {"tags": ["Wide Body"]}, "derived_tags": [DERIVED]}]
    library.read_tags()
    return library


async def _after(_next: str | None) -> None:
    return None


def _hovered(said: dict[str, Any]) -> dict[str, str]:
    """The read state the panel draws for one source, with what hovering it shows."""
    with ui.column() as body:
        tag._tag_source(said)
    return lines.details(body)


class InTheGrids(unittest.TestCase):
    def test_a_game_row_carries_both(self) -> None:
        (row,) = _library().game_rows()

        self.assertEqual(["Wide Body", DERIVED], row["tags"])

    def test_a_table_row_carries_both(self) -> None:
        (row,) = games.table_rows([{"id": "t", "game_id": "afm",
                                    "user": {"tags": ["Wide Body"]},
                                    "derived_tags": [DERIVED]}])

        self.assertEqual(["Wide Body", DERIVED], row["tags"])


class OnTheTagsPage(unittest.TestCase):
    def test_the_picker_does_not_offer_one(self) -> None:
        self.assertNotIn(DERIVED, _library().tags())

    def test_its_row_names_the_source(self) -> None:
        said = {row["tag"]: row for row in _library().tag_rows()}

        self.assertEqual("Challenge: Weekly Challenge", said[DERIVED]["source"])
        self.assertEqual("", said["Wide Body"]["source"])

    def test_it_is_not_a_spelling_to_merge(self) -> None:
        groups = tageditor.rows_by_key(_library().tag_rows())

        self.assertEqual([["Wide Body", "wide body"]],
                         [sorted(row["tag"] for row in group) for group in groups])

    def test_its_menu_offers_nothing(self) -> None:
        self.assertEqual([], tageditor.acts(_library(), [{"tag": DERIVED, "games": 0}], _after))

    def test_a_refresh_gives_the_grid_rows_with_the_flag_too(self) -> None:
        # Both the first draw and the refresh hand rows to the grid.
        self.assertEqual(2, inspect.getsource(tageditor.build)
                         .count("without_menu_where_derived("))

    def test_its_row_opens_no_menu_and_another_still_does(self) -> None:
        rows = tageditor.without_menu_where_derived(_library().tag_rows())

        self.assertEqual({DERIVED: True, "Wide Body": False},
                         {row["tag"]: row[tageditor.grid.NO_MENU] for row in rows
                          if row["tag"] in (DERIVED, "Wide Body")})

    def test_another_tag_cannot_be_merged_into_it(self) -> None:
        verbs = tageditor.acts(_library(), [{"tag": "weekly challenge", "games": 1}], _after)
        into = next(verb for verb in verbs if verb.choices)

        self.assertNotIn(DERIVED, [label for label, _ in into.choices])


def _smart(name: str, tags: Any, **others: Any) -> dict[str, Any]:
    return {"name": name, "type": "filter",
            "filters": {"tags": tags, "manufacturer": ["All"], **others}}


class FromItsList(unittest.TestCase):
    def test_the_list_finds_its_tag_and_last_read(self) -> None:
        said = sources_of(_library().tag_looks(), "challenge", "releases")

        self.assertEqual((DERIVED, SOURCE["read_at"]), (said["tag"], said["read_at"]))
        self.assertEqual({}, sources_of(_library().tag_looks(), "challenge", "machines"))

    def test_a_failed_read_says_when_the_last_good_one_was(self) -> None:
        self.assertTrue(read_state({**SOURCE, "stale": True}).startswith(
            "Last good read "))
        self.assertTrue(read_state(SOURCE).startswith("Read "))
        self.assertEqual("Not read yet", read_state({"stale": True}))

    def test_a_failed_read_hovers_why_on_the_tags_panel(self) -> None:
        failed = {**SOURCE, "stale": True, "error": "Nothing answers at vpinplay.example"}

        self.assertEqual({read_state(failed): "Nothing answers at vpinplay.example",
                          read_state(SOURCE): ""},
                         {**_hovered(failed), **_hovered(SOURCE)})

    def test_a_read_a_moment_ago_reads_as_part_of_the_sentence(self) -> None:
        self.assertEqual("Read just now", read_state({"read_at": utc_now_iso()}))

    def test_the_collection_on_that_tag_alone_is_the_one_it_opens(self) -> None:
        collections = [{"name": "Mine", "type": "manual", "filters": None},
                       _smart("Wider", [DERIVED, "Wide Body"]),
                       _smart("Challenge", [DERIVED])]

        self.assertEqual("Challenge", ext_lists.collection_for(collections, DERIVED))
        self.assertEqual("", ext_lists.collection_for(collections[:2], DERIVED))

    def test_a_new_one_takes_the_lists_title_numbered_if_taken(self) -> None:
        collections = [{"name": "weekly challenge"}, {"name": "Weekly Challenge 2"}]

        self.assertEqual("Weekly Challenge 3",
                         ext_lists.free_name("Weekly Challenge", collections))
        self.assertEqual("Other", ext_lists.free_name("Other", collections))

    def test_an_empty_collection_can_start_from_it(self) -> None:
        known = [collection_rules.Field("tags", "Tags", "", "choice")]

        (one,) = collection_rules.tagged([("Challenge: Weekly Challenge", DERIVED)], known)

        self.assertEqual("Challenge: Weekly Challenge", one.reads())
        self.assertEqual({"tags": [DERIVED]},
                         collection_rules.filters_from(list(one.rows), known))
        self.assertEqual([], collection_rules.tagged([("x", DERIVED)], []))


if __name__ == "__main__":
    unittest.main()
