"""The panel a Media or Assets row opens, and the grid behind it."""

from __future__ import annotations

import unittest
from typing import Any

from common.i18n import t
from manager.kit import art, grid, media_ownership
from manager.library import file_acts, file_facts
from manager.workbench import core, file, game_media, rail


def _row(kind: str = "backglass", table: str = "", present: bool = True, **more: Any) -> dict:
    return {"id": f"g:{kind}:{table}:{present}", "game_id": "g", "kind": kind,
            "table": table, "present": present, **more}


class FindingTheRowAgain(unittest.TestCase):
    def test_by_id_first(self) -> None:
        wanted = _row(table="t1")

        self.assertIs(wanted, file._same_file([_row(), wanted], wanted))

    def test_an_address_finds_it_by_table_and_kind(self) -> None:
        held = _row(table="t1")

        self.assertIs(held, file._same_file([_row(), held],
                                                 {"kind": "backglass", "table": "t1"}))

    def test_a_removed_table_file_leaves_its_shared_row(self) -> None:
        shared = _row(present=False)

        self.assertIs(shared, file._same_file([shared], _row(table="t1")))

    def test_a_missing_row_filled_on_a_table_is_found_there(self) -> None:
        missing = _row(present=False)
        added = {**_row(table="t1"), "id": "g:backglass:AFM.directb2s"}

        self.assertIs(added, file._same_file([_row(kind="wheel"), added], missing))

    def test_a_table_row_is_not_answered_by_another_table(self) -> None:
        self.assertIsNone(file._same_file([_row(table="t2")], _row(table="t1")))

    def test_a_kind_the_game_no_longer_has_is_not_found(self) -> None:
        self.assertIsNone(file._same_file([_row(kind="wheel")], _row()))


class WhoUsesIt(unittest.TestCase):
    def test_media_says_it_by_its_tier(self) -> None:
        self.assertEqual([media_ownership.TABLE, media_ownership.GAME,
                          media_ownership.ORPHAN],
                         [file_acts.tier_of({"via": via})
                          for via in ("table", "default", "orphan")])

    def test_a_file_under_a_slot_says_what_its_media_row_says(self) -> None:
        for tier, serves, via in (("game", 1, "game"), ("default", 1, "default"),
                                  ("default", 0, "unused"), ("set:classic", 0, "unused")):
            with self.subTest(tier=tier, serves=serves):
                self.assertEqual(
                    file_acts.tier_of({"via": via, "serves": serves}),
                    media_ownership.key_of(game_media._used_as(
                        {"tier": tier, "file": "bg.png", "wins": False, "serves": serves})))

    def test_an_asset_named_for_the_folder_that_nothing_loads_is_unused(self) -> None:
        self.assertEqual(media_ownership.UNUSED, file_acts.tier_of(
            {"binding": "game", "present": True, "serves": 0}))

    def test_an_asset_says_it_by_its_binding(self) -> None:
        self.assertEqual([media_ownership.TABLE, media_ownership.GAME,
                          media_ownership.ORPHAN, media_ownership.MISSING],
                         [file_acts.tier_of(one) for one in (
                             {"binding": "table", "present": True},
                             {"binding": "game", "present": True, "serves": 2},
                             {"binding": "orphaned", "present": True},
                             {"binding": "none", "present": False})])


class TheLineUnderIt(unittest.TestCase):
    def _said(self, kind: str, serves: int, tables: int, lens: str = "") -> str:
        row = {"binding": "game", "present": True, "serves": serves, "kind": kind}
        return file._asset_said(
            {"kind": kind, "lens": lens,
             "tables": [{"id": f"t{number}"} for number in range(tables)],
             "served": [{"id": f"t{number}"} for number in range(serves)]},
            file_acts.tier_of(row))

    def test_a_file_every_table_loads_adds_nothing_to_its_chip(self) -> None:
        self.assertEqual("", self._said("ini", 2, 2))

    def test_a_file_some_tables_have_their_own_beside_says_who_is_left(self) -> None:
        self.assertEqual(t("manager.workbench.shared_by_some"), self._said("ini", 1, 2))

    def test_a_table_looking_at_the_shared_file_adds_nothing_to_its_chip(self) -> None:
        self.assertEqual("", self._said("ini", 1, 2, lens="t1"))

    def test_a_file_every_table_has_its_own_beside_is_covered_not_inert(self) -> None:
        self.assertEqual(t(media_ownership.tier_for(media_ownership.UNUSED).why),
                         self._said("ini", 0, 2))

    def test_a_kind_the_folder_name_never_reaches_is_inert(self) -> None:
        self.assertEqual(t("manager.workbench.inert_asset"), self._said("pov", 0, 2))

    def test_a_rom_file_adds_nothing_to_its_chip(self) -> None:
        self.assertEqual("", self._said("rom", 0, 2))

    def test_a_folder_counts_its_files(self) -> None:
        said = file_facts.asset_spec({"folder": True, "files": 12, "size_bytes": 2048})

        self.assertTrue(said.startswith("12 files"), said)

    def test_a_file_says_its_format(self) -> None:
        self.assertTrue(file_facts.asset_spec({"format": "VBS", "size_bytes": 41})
                        .startswith("VBS"))


class ItsOwnRail(unittest.TestCase):
    def test_a_file_is_answered_by_file_alone(self) -> None:
        for subject, keys in (("media_file", ["media_file"]),
                              ("asset_file", ["asset_file"])):
            with self.subTest(subject=subject):
                self.assertEqual(keys, [item.key
                                        for item in rail.sections_for(subject)])


def _section(key: str) -> core.Section:
    return next(item for item in rail.SECTIONS if item.key == key)


class TheStage(unittest.TestCase):
    def _docked(self, key: str, context: dict) -> bool:
        return core._docked(_section(key), context)

    def test_a_media_file_that_is_there_has_a_stage(self) -> None:
        self.assertTrue(self._docked("media_file", {"row": {"present": True}}))
        self.assertFalse(self._docked("media_file", {"row": {"present": False}}))

    def test_a_loose_media_file_has_one_only_where_its_path_can_be_drawn(self) -> None:
        loose = {"present": True, "via": "orphan", "path": "media/bg.png"}

        self.assertTrue(self._docked("media_file", {"row": loose,
                                                    "game": {"folder": "/lib/g"}}))
        self.assertFalse(self._docked("media_file", {"row": loose, "game": {}}))
        self.assertFalse(self._docked("media_file", {"row": {**loose, "path": ""},
                                                     "game": {"folder": "/lib/g"}}))

    def test_a_backglass_has_a_stage_unless_it_is_damaged(self) -> None:
        for damaged in (False, True):
            with self.subTest(damaged=damaged):
                context = {"kind": "backglass",
                           "detail": {"backglass": _glass(damaged=damaged)}}

                self.assertEqual(not damaged, self._docked("asset_file", context))

    def test_a_script_has_a_stage_for_its_first_lines(self) -> None:
        self.assertTrue(self._docked("asset_file", {"kind": "vbs", "detail": {"head": "x"}}))

    def test_a_folder_or_an_absent_file_is_only_an_icon_so_has_none(self) -> None:
        for context in ({"kind": "pup_pack", "detail": {"files": 3}},
                        {"kind": "backglass", "detail": {}}):
            with self.subTest(kind=context["kind"]):
                self.assertFalse(self._docked("asset_file", context))

    def test_a_dock_that_is_a_flag_is_one_still(self) -> None:
        self.assertTrue(core._docked(core.Section("x", lambda _: "", None, dock=True),  # type: ignore[arg-type]
                                          {}))


class TheDocksHeight(unittest.TestCase):
    def test_each_section_keeps_its_own(self) -> None:
        state: dict = {}

        core.dock_dragged(state, {"section": "media_file", "px": 410})

        self.assertEqual((410, core.DOCK_PX),
                         (core.dock_height(state, "media_file"),
                          core.dock_height(state, "asset_file")))

    def test_a_report_with_no_section_or_height_is_ignored(self) -> None:
        state: dict = {}

        for args in (None, 300, {"px": 300}, {"section": "media_file"},
                     {"section": "a", "px": 0}, {"section": "a", "px": "tall"}):
            core.dock_dragged(state, args)

        self.assertEqual({}, state.get("dock_px", {}))


def _glass(**more: Any) -> dict:
    return {"author": "Pat Maker", "artwork": "Pat Maker", "rom": "exmpl_l1", "players": 4,
            "grill": 0, "two_versions": False, "size": [2000, 1565], "lamps": 12, "still": False,
            "scoreview_art": None, "problems": [], "damaged": False, "version": "abc",
            **more}


def _rom(name: str) -> dict:
    return {"dependencies": {"pinmame": {"effective": name}}}


class WhatABackglassFileSays(unittest.TestCase):
    def _facts(self, glass: dict, *served: dict) -> dict:
        return dict(file._backglass_facts({"served": list(served)}, glass))

    def test_an_author_who_made_the_art_too_is_said_once(self) -> None:
        said = self._facts(_glass())

        self.assertEqual({"Author", "ROM", "Players", "Picture"}, set(said))

    def test_art_by_someone_else_is_named(self) -> None:
        self.assertEqual("Sam Artist", self._facts(_glass(artwork="Sam Artist"))["Artwork"])

    def test_the_picture_is_its_size(self) -> None:
        self.assertEqual("2000 × 1565", self._facts(_glass())["Picture"])

    def test_a_scoreview_picture_is_there_only_when_the_file_has_one(self) -> None:
        said = self._facts(_glass(scoreview_art=[1024, 256]))

        self.assertEqual("1024 × 256", said["Score View Art"])

    def test_a_rom_the_table_also_reads_carries_no_chip(self) -> None:
        said = self._facts(_glass(), _rom("EXMPL_L1"))

        self.assertEqual("exmpl_l1", said["ROM"])

    def test_a_rom_the_table_does_not_read_is_marked_different(self) -> None:
        said = self._facts(_glass(), _rom("other"))

        self.assertTrue(callable(said["ROM"]))

    def test_a_table_with_no_rom_has_nothing_to_differ_from(self) -> None:
        self.assertEqual("exmpl_l1", self._facts(_glass(), {})["ROM"])

    def test_one_table_reading_another_rom_is_enough_to_mark_it(self) -> None:
        said = self._facts(_glass(), _rom("exmpl_l1"), _rom("other"))

        self.assertTrue(callable(said["ROM"]))

    def test_the_roms_are_those_of_the_tables_that_read_it(self) -> None:
        context = {"served": [_rom("b"), _rom("a"), _rom("b"), {}]}

        self.assertEqual(["a", "b"], file._backglass_roms(context))

    def test_the_picture_address_carries_the_files_version(self) -> None:
        src = art.backglass("g1", "my glass.directb2s", version="abc", size=art.PANEL)

        self.assertEqual(
            "/api/v1/games/g1/assets/backglass/picture?path=my+glass.directb2s&look=on"
            "&version=authentic&grill=true&size=1024&v=abc", src)


class _Grid:
    id = 7

    def __init__(self) -> None:
        self.sent: list[tuple] = []

    def run_grid_method(self, *args: Any) -> None:
        self.sent.append(args)


class OneGamesRowsPutRight(unittest.TestCase):
    def test_what_stayed_updates_what_is_new_adds_and_what_went_goes(self) -> None:
        held = [{"id": "a:1", "game_id": "a"}, {"id": "a:2", "game_id": "a"},
                {"id": "b:1", "game_id": "b"}]
        by_id = {row["id"]: row for row in held}
        table = _Grid()

        grid.replace_rows(table, held, by_id, [{"id": "a:1", "game_id": "a", "x": 1},
                                               {"id": "a:3", "game_id": "a"}],
                          lambda row: row["game_id"] == "a")

        method, change = table.sent[0]
        self.assertEqual(("applyTransaction", [{"id": "a:2"}], ["a:1"], ["a:3"]),
                         (method, change["remove"], [r["id"] for r in change["update"]],
                          [r["id"] for r in change["add"]]))
        self.assertEqual({"a:1", "a:3", "b:1"}, set(by_id))
        self.assertEqual(3, len(held))


if __name__ == "__main__":
    unittest.main()
