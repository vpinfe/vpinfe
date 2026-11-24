"""The one list of acts for collections and for tags, as the row menu, the panel and the
selection each draw it."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any

from manager.kit import panel
from manager.library import collections, tageditor


async def _after(_next: str | None) -> None:
    return None


def _labels(verbs: list[panel.Verb]) -> list[str]:
    return [verb.label for verb in verbs]


def _collection(name: str, kind: str = "manual") -> dict[str, Any]:
    return {"name": name, "type": kind, "count": 3}


class ForCollections(unittest.TestCase):
    def test_a_hand_picked_collection_has_no_convert(self) -> None:
        verbs = collections.acts(None, {}, [_collection("Favorites")], _after)

        self.assertEqual(["Rename…", "Duplicate", "Delete"], _labels(verbs))

    def test_a_smart_one_offers_convert_between_duplicate_and_delete(self) -> None:
        verbs = collections.acts(None, {}, [_collection("Recent", "filter")], _after)

        self.assertEqual(["Rename…", "Duplicate", "Convert to Hand-Picked", "Delete"],
                         _labels(verbs))

    def test_a_selection_offers_convert_when_any_one_can(self) -> None:
        verbs = collections.acts(None, {}, [_collection("Favorites"),
                                            _collection("Recent", "filter")], _after)

        self.assertIn("Convert to Hand-Picked", _labels(verbs))

    def test_unsaved_rules_keep_convert_away(self) -> None:
        state: dict[str, Any] = {"collection_drafts": {"Recent": {"rules": []}}}

        verbs = collections.acts(None, state, [_collection("Recent", "filter")], _after)

        self.assertNotIn("Convert to Hand-Picked", _labels(verbs))

    def test_the_grids_moves_sit_before_delete_and_are_for_one(self) -> None:
        moves = [panel.Verb("Move Up", in_panel=False, one=True)]

        verbs = collections.acts(None, {}, [_collection("Favorites")], _after, moves=moves)

        self.assertEqual("Move Up", verbs[-2].label)
        self.assertTrue(verbs[-1].danger)
        several = panel.for_selection(verbs, 2)
        self.assertIsNone(next(v for v in several if v.label == "Move Up").run)

    def test_rename_belongs_to_the_grid_and_not_the_panel(self) -> None:
        verbs = collections.acts(None, {}, [_collection("Favorites")], _after)

        self.assertFalse(next(v for v in verbs if v.label == "Rename…").in_panel)


def _library(*names: str, derived: tuple[str, ...] = ()) -> Any:
    return SimpleNamespace(derived_tags=lambda: set(derived),
                           tag_looks=lambda: {name: {} for name in (*names, *derived)})


class ForTags(unittest.TestCase):
    def test_one_tag_offers_rename_merge_and_delete(self) -> None:
        verbs = tageditor.acts(_library("a", "b"), [{"tag": "a", "games": 2}], _after)

        self.assertEqual(["Rename…", "Merge Into", "Delete"], _labels(verbs))

    def test_merge_into_leaves_out_every_tag_chosen(self) -> None:
        verbs = tageditor.acts(_library("a", "b", "c"),
                               [{"tag": "a", "games": 1}, {"tag": "b", "games": 1}], _after)

        into = next(verb for verb in verbs if verb.choices)
        self.assertEqual(["c"], [label for label, _ in into.choices])

    def test_no_other_tag_means_no_merge(self) -> None:
        verbs = tageditor.acts(_library("a"), [{"tag": "a", "games": 1}], _after)

        self.assertEqual(["Rename…", "Delete"], _labels(verbs))

    def test_a_derived_tag_in_the_selection_is_left_out(self) -> None:
        library = _library("a", "b", derived=("Weekly",))

        self.assertEqual([], tageditor.acts(library, [{"tag": "Weekly", "games": 0}], _after))
        verbs = tageditor.acts(library, [{"tag": "Weekly", "games": 0},
                                         {"tag": "a", "games": 1}], _after)
        into = next(verb for verb in verbs if verb.choices)
        self.assertEqual(["b"], [label for label, _ in into.choices])


if __name__ == "__main__":
    unittest.main()
