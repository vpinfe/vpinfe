"""What a selection of media files or asset files offers: the verbs that apply to any of
them, over the ones they apply to."""

from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import AsyncMock, Mock

from common.i18n import t
from common.online import vps_kinds
from manager.library import file_acts


def _library() -> Mock:
    library = Mock()
    library.games = [{"id": "g1", "name": "One", "vps_id": "v1"}, {"id": "g2", "name": "Two"}]
    library.tables = {}
    library.discovery.return_value = {"capabilities": []}
    return library


def _media(file_id: str, game_id: str = "g1", **more: Any) -> dict[str, Any]:
    return {"id": file_id, "game_id": game_id, "kind": "bg", "label": "Backglass",
            "table": "", "path": f"/m/{file_id}.png", "present": True, "via": "game",
            **more}


def _asset(file_id: str, kind: str = "backglass", **more: Any) -> dict[str, Any]:
    return {"id": file_id, "game_id": "g1", "kind": kind, "label": kind, "table": "",
            "path": f"/a/{file_id}", "present": True, "binding": "game", "serves": 1, **more}


def _media_acts(*rows: dict[str, Any]) -> list:
    library = _library()
    return file_acts.media_acts(library, file_acts.files_of(library, list(rows)), {},
                                AsyncMock())


def _asset_acts(*rows: dict[str, Any]) -> list:
    library = _library()
    return file_acts.asset_acts(library, file_acts.files_of(library, list(rows)), {},
                                AsyncMock())


class MediaFiles(unittest.TestCase):
    def test_a_file_in_use_offers_the_slot_acts_and_ends_on_a_danger_remove(self) -> None:
        made = _media_acts(_media("a"))

        labels = [one.label for one in made]
        self.assertEqual(labels[0], t("manager.files.replace"))
        self.assertNotIn(t("manager.media_rotate.rotate"), labels)
        self.assertEqual(labels[-1], t("word.remove"))
        self.assertTrue(made[-1].danger)

    def test_rotate_is_a_submenu_of_the_three_ways_for_several_files(self) -> None:
        (turn,) = [one for one in _media_acts(_media("a"), _media("b"))
                   if one.label == t("manager.media_rotate.rotate")]

        self.assertEqual([label for label, _run in turn.choices],
                         [t("manager.media_rotate.left"), t("manager.media_rotate.right"),
                          t("manager.media_rotate.around")])

    def test_a_loose_file_has_no_media_menu(self) -> None:
        self.assertEqual(_media_acts(_media("a", via="orphan"), _media("b", via="unused")),
                         [])

    def test_a_loose_file_among_others_is_left_out(self) -> None:
        made = _media_acts(_media("a"), _media("b", via="orphan"))

        self.assertEqual(made[0].label, t("manager.files.replace"))

    def test_the_slot_act_reads_add_where_every_file_is_missing(self) -> None:
        made = _media_acts(_media("a", present=False, via="missing"))

        self.assertEqual(made[0].label, t("manager.files.add"))

    def test_the_slot_act_reads_add_or_replace_over_a_mix(self) -> None:
        made = _media_acts(_media("a"), _media("b", present=False, via="missing"))

        self.assertEqual(made[0].label, t("manager.files.either"))

    def test_get_missing_shows_only_where_a_slot_is_a_gap(self) -> None:
        gap = t("manager.media_fill.get_missing")

        self.assertNotIn(gap, [one.label for one in _media_acts(_media("a"))])
        self.assertIn(gap, [one.label for one in
                            _media_acts(_media("a", present=False, via="missing"))])

    def test_match_to_vps_is_refused_with_why_where_no_game_is_matched(self) -> None:
        (match,) = [one for one in _media_acts(_media("a", game_id="g2", kind="wheel"))
                    if one.label == t("manager.games.match_vps")]

        self.assertIsNone(match.run)
        self.assertTrue(match.hint)

    def test_move_to_game_needs_a_file_a_table_has_of_its_own(self) -> None:
        move = t("manager.file_move.to_game")

        self.assertNotIn(move, [one.label for one in _media_acts(_media("a"))])
        self.assertIn(move, [one.label for one in
                             _media_acts(_media("a", via="table", table="t1",
                                       kind="backglass"))])


class AssetFiles(unittest.TestCase):
    def test_an_asset_offers_add_or_replace_then_a_danger_remove(self) -> None:
        made = _asset_acts(_asset("a"))

        self.assertEqual([one.label for one in made],
                         [t("manager.files.replace"), t("manager.games.match_vps"),
                          t("manager.file_move.choose"), t("word.remove")])
        self.assertTrue(made[-1].danger)

    def test_a_kind_without_acts_has_no_menu(self) -> None:
        self.assertEqual(_asset_acts(_asset("a", kind="not_a_kind")), [])

    def test_a_script_beside_its_table_is_deleted_not_removed(self) -> None:
        made = _asset_acts(_asset("a", kind="script", table="t1", binding="table"))

        self.assertEqual([one.label for one in made],
                         [t("manager.files.replace"), t("word.delete")])
        self.assertTrue(made[-1].danger)

    def test_a_table_settings_file_is_not_offered_move_to_game(self) -> None:
        made = _asset_acts(_asset("a", kind="ini", table="t1", binding="table"))

        self.assertNotIn(t("manager.file_move.to_game"), [one.label for one in made])

    def test_a_loose_asset_can_be_removed_but_not_placed(self) -> None:
        made = _asset_acts(_asset("a", binding="orphaned"))

        self.assertEqual([one.label for one in made],
                         [t("manager.games.match_vps"), t("word.remove")])


class HeldFiles(unittest.TestCase):
    """Replace and Remove for the one file a Discovery card holds, by key."""

    def _held(self, inventory: str, row: dict[str, Any]) -> dict[str, Any]:
        library = _library()
        (one,) = file_acts.files_of(library, [row])
        return file_acts.held_acts(library, one, inventory, {}, AsyncMock())

    def test_a_media_file_offers_replace_and_a_danger_remove(self) -> None:
        made = self._held(vps_kinds.MEDIA, _media("a"))

        self.assertEqual(set(made), {file_acts.REPLACE, file_acts.REMOVE})
        self.assertEqual(made[file_acts.REPLACE].label, t("manager.files.replace"))
        self.assertTrue(made[file_acts.REMOVE].danger)

    def test_an_asset_offers_the_same_two(self) -> None:
        made = self._held(vps_kinds.ASSET, _asset("a"))

        self.assertEqual(set(made), {file_acts.REPLACE, file_acts.REMOVE})

    def test_the_menu_acts_that_are_not_replace_or_remove_are_left_out(self) -> None:
        made = self._held(vps_kinds.MEDIA, _media("a"))

        self.assertNotIn(t("manager.media_rotate.rotate"), [one.label for one in made.values()])

    def test_a_file_the_pane_offers_nothing_for_has_neither(self) -> None:
        self.assertEqual(self._held(vps_kinds.MEDIA, _media("a", via="orphan")), {})


if __name__ == "__main__":
    unittest.main()
