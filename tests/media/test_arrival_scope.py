"""Which table a drop is about, and what each kind of file does with it.

A media file or a per-table asset dropped on a table is named for that table; dropped on
the game it is named for the game. A game with one table has no second to tell apart, so
a table target means the game there.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from common.games import media_placement
from common.i18n import t
from common.uploads import arrival
from tests.support.library import fake_game, game_info
from tests.support.swaps import library_of
from tests.support.uploads import make_zip, plan_kinds_by_action

GAME = "Multi00000001"
SOLO = "Solo00000001"


def _names(plan) -> dict[str, str]:
    return {item.asset.kind: Path(item.destination).name for item in plan.items}


class _Games(unittest.TestCase):
    """A game with three tables and one with a single table, both held by the library."""

    def setUp(self) -> None:
        held = TemporaryDirectory()
        self.addCleanup(held.cleanup)
        self.tmp = Path(held.name)
        self.many = self._game("Multi (Bar 1999)", GAME, ["Alpha", "Bravo", "Charlie"])
        self.solo = self._game("Solo (Bar 1999)", SOLO, ["Alone"])
        stems = ["Alpha", "Bravo", "Charlie"]
        library_of(self, {
            GAME: fake_game(self.many, self.many.name, meta=self._info(GAME, stems)),
            SOLO: fake_game(self.solo, self.solo.name, meta=self._info(SOLO, ["Alone"]))})

    @staticmethod
    def _info(game_id: str, stems: list[str]) -> dict:
        tables = {f"t{n}": {"id": f"t{n}", "filename": f"{stem}.vpx"}
                  for n, stem in enumerate(stems, start=1)}
        info = game_info(stems[0], game_id=game_id, tables=tables)
        info["vpinfe"]["default_table"] = "t1"
        return info

    def _game(self, name: str, game_id: str, stems: list[str]) -> Path:
        folder = self.tmp / name
        folder.mkdir()
        for stem in stems:
            (folder / f"{stem}.vpx").write_bytes(b"x")
        (folder / f"{name}.info").write_text(json.dumps(self._info(game_id, stems)))
        return folder

    def _picture(self, name: str = "IMG_0001.png") -> Path:
        path = self.tmp / name
        path.write_bytes(b"x" * 16)
        return path

    def _arrival(self, names: list[str], **target):
        archive = self.tmp / "drop.zip"
        make_zip(archive, names)
        return arrival.read(archive, **target)

    def _planned(self, names: list[str], *, picks=None, **options):
        game_dir = options.pop("game_dir", None)
        target = {key: options.pop(key) for key in ("table_id", "scope") if key in options}
        found = self._arrival(names, game_dir=game_dir, **target)
        return arrival.plan(found, picks=picks, **options)

    def _slot(self, path, game_dir, **target):
        return arrival.plan(arrival.read(path, game_dir=game_dir, **target), game_dir=game_dir)

    def _wheel(self, stem: str) -> str:
        return media_placement.target_name("wheel", stem, ".png")


class MediaScopeTests(_Games):
    def test_media_on_a_game_target_is_named_for_the_folder(self) -> None:
        plan = self._slot(self._picture(), self.many, media_kind="wheel")

        self.assertEqual(Path(plan.items[0].destination).name, self._wheel(self.many.name))

    def test_media_on_a_table_target_is_named_for_that_tables_file_stem(self) -> None:
        plan = self._slot(self._picture(), self.many, media_kind="wheel", table_id="t2")

        self.assertEqual(Path(plan.items[0].destination).name, self._wheel("Bravo"))

    def test_a_game_with_one_table_collapses_a_table_target_to_the_game(self) -> None:
        plan = self._slot(self._picture(), self.solo, media_kind="wheel", table_id="t1")

        self.assertEqual(Path(plan.items[0].destination).name, self._wheel(self.solo.name))

    def test_a_table_named_in_a_game_with_one_table_shares_a_place_with_the_game(self) -> None:
        found = self._arrival(["(Wheel) Anything.png", "IMG_0001.png"], game_dir=self.solo)
        found = arrival.apply(found, [{"member": "IMG_0001.png", "kind": "media",
                                       "media_kind": "wheel", "table_id": "t1"}])

        (group,) = found.groups
        self.assertEqual((group.key, len(group.candidates)), ("media:wheel", 2))

    def test_a_scope_asked_for_is_kept_whatever_the_table_count(self) -> None:
        for folder, table_id, scope, stem in (
                (self.solo, "t1", "table", "Alone"),
                (self.many, "t2", "game", self.many.name)):
            with self.subTest(scope=scope):
                plan = self._slot(self._picture(), folder, media_kind="wheel",
                                  table_id=table_id, scope=scope)

                self.assertEqual(Path(plan.items[0].destination).name, self._wheel(stem))

    def test_a_table_the_game_does_not_hold_is_refused(self) -> None:
        with self.assertRaises(ValueError) as caught:
            self._slot(self._picture(), self.many, media_kind="wheel", table_id="t9")

        self.assertEqual(str(caught.exception), t("error.games.swap_not_contained"))

    def test_a_page_names_the_page_it_is_written_as(self) -> None:
        medias = self.many / "medias"
        medias.mkdir()
        (medias / media_placement.target_name("flyer", self.many.name, ".png")).write_bytes(b"x")

        plan = self._slot(self._picture(), self.many, media_kind="flyer", page=2)

        self.assertEqual(Path(plan.items[0].destination).name,
                         media_placement.target_name("flyer", self.many.name, ".png", 2))

    def test_media_in_a_general_drop_follows_the_table_it_landed_on(self) -> None:
        plan = self._planned(["(Wheel) Anything.png"], game_dir=self.many, table_id="t3")

        self.assertEqual(_names(plan)["media"], self._wheel("Charlie"))


class PerTableAssetTests(_Games):
    def _pov(self, name: str = "Other.pov") -> Path:
        path = self.tmp / name
        path.write_bytes(b"x" * 16)
        return path

    def test_a_per_table_asset_on_a_table_target_is_named_for_that_table(self) -> None:
        loose = self._slot(self._pov(), self.many, asset_kind="pov", table_id="t2")
        packed = self._planned(["Other.directb2s", "Other.ini"], game_dir=self.many,
                               table_id="t3")

        self.assertEqual(_names(loose), {"pov": "Bravo.pov"})
        self.assertEqual(_names(packed), {"backglass": "Charlie.directb2s", "ini": "Charlie.ini"})

    def test_an_asset_named_for_one_of_the_tables_is_named_for_that_table(self) -> None:
        loose = self._slot(self._pov("Charlie.pov"), self.many, asset_kind="pov")
        packed = self._planned(["charlie.directb2s"], game_dir=self.many)

        self.assertEqual(_names(loose), {"pov": "Charlie.pov"})
        self.assertEqual(_names(packed), {"backglass": "Charlie.directb2s"})

    def test_an_asset_named_for_no_table_goes_to_the_default_and_lists_the_tables(self) -> None:
        loose = self._slot(self._pov(), self.many, asset_kind="pov")
        packed = self._planned(["Other.directb2s"], game_dir=self.many)

        for plan, kind, name in ((loose, "pov", "Alpha.pov"),
                                 (packed, "backglass", "Alpha.directb2s")):
            with self.subTest(kind=kind):
                self.assertEqual(_names(plan), {kind: name})
                self.assertEqual([(one["id"], one["default"]) for one in plan.tables],
                                 [("t1", True), ("t2", False), ("t3", False)])

    def test_a_game_with_one_table_needs_no_table_choice(self) -> None:
        loose = self._slot(self._pov(), self.solo, asset_kind="pov")
        packed = self._planned(["Other.directb2s"], game_dir=self.solo)

        self.assertEqual((loose.tables, packed.tables), ((), ()))

    def test_a_table_named_by_the_drop_needs_no_table_choice(self) -> None:
        plan = self._slot(self._pov(), self.many, asset_kind="pov", table_id="t2")

        self.assertEqual(plan.tables, ())

    def test_media_on_one_of_several_tables_lists_the_tables_to_name_it_for(self) -> None:
        found = arrival.read(self._picture(), game_dir=self.many, media_kind="wheel", table_id="t2")

        self.assertEqual([one["id"] for one in found.tables], ["t1", "t2", "t3"])

    def test_media_on_the_only_table_lists_no_tables(self) -> None:
        found = arrival.read(self._picture(), game_dir=self.solo, media_kind="wheel", table_id="t1")

        self.assertEqual(found.tables, ())

    def test_a_pov_on_a_named_table_still_lists_no_tables(self) -> None:
        found = arrival.read(self._pov(), game_dir=self.many, asset_kind="pov", table_id="t2")

        self.assertEqual(found.tables, ())

    def test_a_drop_with_nothing_per_table_in_it_lists_no_tables(self) -> None:
        plan = self._planned(["(Wheel) Anything.png"], game_dir=self.many)

        self.assertEqual(plan.tables, ())

    def test_an_arriving_table_names_what_comes_with_it_over_the_table_landed_on(self) -> None:
        plan = self._planned(["Bravo 1.1.vpx", "Other.directb2s"], game_dir=self.many,
                             replace_table="t2", table_id="t3")

        self.assertEqual(_names(plan)["backglass"], "Bravo 1.1.directb2s")

    def test_a_lone_file_that_is_not_the_kinds_own_is_refused(self) -> None:
        found = arrival.read(self._picture(), game_dir=self.many, asset_kind="pov")

        self.assertTrue(found.refusal)
        self.assertEqual(found.claims, ())

    def test_a_file_on_a_kind_that_is_not_a_sidecar_is_that_kinds_own(self) -> None:
        found = arrival.read(self._pov("x.crz"), game_dir=self.many, asset_kind="altcolor_serum",
                             rom_name="mm")

        self.assertEqual([(one.kind, one.basis) for one in found.claims],
                         [("altcolor_serum", arrival.DECLARED)])


class TableFileScopeTests(_Games):
    def test_a_table_file_on_a_table_target_proposes_updating_that_table(self) -> None:
        plan = self._planned(["Bravo 1.1.vpx"], game_dir=self.many, replace_table="t2")

        item = plan.items[0]
        self.assertEqual((item.action, item.table_id, item.replaces),
                         ("replace_vpx", "t2", "Bravo.vpx"))

    def test_adding_it_as_another_table_stays_available(self) -> None:
        plan = self._planned(["Bravo 1.1.vpx"], game_dir=self.many, add_table=True)

        self.assertEqual(plan_kinds_by_action(plan), {"table": "add_table"})

    def test_a_table_file_on_a_game_target_is_added(self) -> None:
        plan = self._planned(["Delta.vpx"], game_dir=self.many, add_table=True)

        self.assertEqual(plan_kinds_by_action(plan), {"table": "add_table"})

    def test_a_table_file_on_the_library_makes_a_new_game(self) -> None:
        plan = self._planned(["Delta.vpx"], allow_new_game=True, games_path=str(self.tmp))

        self.assertEqual(plan.new_game_dir_name, "Delta")

    def test_a_table_the_game_does_not_hold_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            self._planned(["Other.pov"], game_dir=self.many, table_id="t9")


class OneFileToAnAddressTests(_Games):
    def test_two_pictures_for_one_slot_ask_which_one(self) -> None:
        names = ["(Wheel) One.png", "(Wheel) Two.jpg", "(Wheel) Three.png"]

        with self.assertRaises(arrival.ChooseOneError) as caught:
            self._planned(names, game_dir=self.many)

        self.assertEqual([group.key for group in caught.exception.groups], ["media:wheel"])

    def test_the_one_picked_is_the_one_written(self) -> None:
        names = ["(Wheel) One.png", "(Wheel) Two.png", "(Wheel) Three.png"]

        plan = self._planned(names, game_dir=self.many, picks={"media:wheel": "(Wheel) Two.png"})

        self.assertEqual([Path(one.destination).name for one in plan.items],
                         [self._wheel(self.many.name)])
        self.assertEqual(plan.items[0].member, "(Wheel) Two.png")

    def test_pictures_for_different_slots_are_both_kept(self) -> None:
        plan = self._planned(["(Wheel) One.png", "(Backglass) Two.png"], game_dir=self.many)

        self.assertEqual(len(plan.items), 2)
        self.assertEqual(plan.blocked, ())


if __name__ == "__main__":
    unittest.main()
