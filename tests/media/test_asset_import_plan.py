"""What an import proposes: which items, which action each, and what it refuses."""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from unittest import mock

from common.i18n import t
from common.media.media_specs import media_family
from common.uploads import arrival
from common.uploads.asset_analyzer_service import analyze_path
from common.uploads.asset_import_service import (
    build_import_plan,
    execute_import_plan,
    find_vps_entry,
    select_plan_items,
    vps_folder_name,
)
from tests.support.library import fake_game, game_info
from tests.support.saved import save_record
from tests.support.swaps import library_of
from tests.support.uploads import blocked_reasons, make_zip, plan_kinds_by_action


class ImportPlanTests(unittest.TestCase):
    def test_new_game_bundle_routes_vpx_and_blocks_rom_color(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as tmp:
            zip_path = Path(tmp) / "Medieval Madness.zip"
            make_zip(zip_path, ["Medieval Madness.vpx", "Medieval Madness.directb2s", "mm.crz"])
            analysis = analyze_path(zip_path)
            plan = build_import_plan(analysis, allow_new_game=True, games_path=tmp)
            self.assertEqual(plan.new_game_dir_name, "Medieval Madness")
            actions = plan_kinds_by_action(plan)
            self.assertEqual(actions["table"], "copy")
            self.assertEqual(actions["backglass"], "replace_b2s")
            # serum color needs a ROM name the fresh game doesn't have yet
            self.assertIn("altcolor_serum", blocked_reasons(plan))

    def test_existing_game_routing(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as tmp:
            game_dir = Path(tmp) / "Foo (Bar 1999)"
            game_dir.mkdir()
            (game_dir / "Foo.vpx").write_bytes(b"x")
            info = game_info("Foo", game_id="Foo00000001", tables={
                "t1": {"id": "t1", "filename": "Foo.vpx"}})
            save_record(game_dir / "Foo (Bar 1999).info", info)
            library_of(self, {"Foo00000001": fake_game(game_dir, game_dir.name, meta=info)})
            zip_path = Path(tmp) / "assets.zip"
            make_zip(zip_path, ["new.vpx", "MyPup/screens.pup", "MyPup/s1/a.mp4", "wheel.png"])
            analysis = analyze_path(zip_path)
            plan = build_import_plan(analysis, game_dir=game_dir, rom_name="mm")
            actions = plan_kinds_by_action(plan)
            self.assertEqual(actions["table"], "replace_vpx")
            self.assertEqual(actions["pup_pack"], "extract_tree")
            self.assertEqual(actions["media"], "replace_media")

    def test_no_context_blocks_all(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as tmp:
            wheel = Path(tmp) / "wheel.png"
            wheel.write_bytes(b"x")
            analysis = analyze_path(wheel)
            plan = build_import_plan(analysis)
            self.assertEqual(plan.items, ())
            self.assertIn("media", blocked_reasons(plan))


class SwitchedOffKindTests(unittest.TestCase):
    """A kind the library switched off is not imported, and the refusal is one line."""

    def _plan(self, kept: set[str]):
        from tempfile import TemporaryDirectory
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        game_dir = Path(tmp.name) / "Foo (Bar 1999)"
        game_dir.mkdir()
        zip_path = Path(tmp.name) / "art.zip"
        make_zip(zip_path, ["(Flyer) Foo.png", "(Topper) Foo.png", "(Wheel) Foo.png"])
        with mock.patch("common.games.library_policy.kept_kinds",
                        return_value={"media": kept, "asset": set()}):
            return build_import_plan(analyze_path(zip_path), game_dir=game_dir)

    def test_a_switched_off_kind_is_refused_with_its_own_name(self):
        plan = self._plan({"wheel"})

        self.assertEqual([one.asset.media_kind for one in plan.items], ["wheel"])
        self.assertEqual(sorted(one.asset.media_kind for one in plan.blocked), ["flyer", "topper"])
        self.assertEqual({one.reason for one in plan.blocked},
                         {t("error.uploads.kind_switched_off")})

    def test_a_kind_that_is_kept_is_planned(self):
        plan = self._plan({"wheel", "flyer", "topper"})

        self.assertEqual(plan.blocked, ())


class AddTablePlanTests(unittest.TestCase):
    """A table dropped on a game to join the tables it has, not to replace one."""

    def setUp(self) -> None:
        from pathlib import Path
        from tempfile import TemporaryDirectory
        held = TemporaryDirectory()
        self.addCleanup(held.cleanup)
        self.tmp = Path(held.name)
        self.game_dir = self.tmp / "Foo (Bar 1999)"
        self.game_dir.mkdir()
        (self.game_dir / "Foo.vpx").write_bytes(b"x")

    def _plan(self, names, add_table=True):
        zip_path = self.tmp / "drop.zip"
        make_zip(zip_path, names)
        return build_import_plan(analyze_path(zip_path), game_dir=self.game_dir,
                                 add_table=add_table)

    def _names(self, plan):
        from pathlib import Path
        return {item.asset.kind: Path(item.destination).name for item in plan.items}

    def test_the_table_is_added(self):
        plan = self._plan(["Foo 1.2.vpx"])

        self.assertEqual(plan_kinds_by_action(plan), {"table": "add_table"})

    def test_one_the_game_holds_by_that_name_is_refused(self):
        from common.i18n import t

        plan = self._plan(["Foo.vpx"])

        self.assertEqual(blocked_reasons(plan),
                         {"table": t("error.games.file_exists")})

    def test_what_comes_with_it_is_named_for_it(self):
        plan = self._plan(["Foo 1.2.vpx", "Foo 1.2.directb2s", "settings.ini",
                           "Foo 1.2.vbs", "Foo 1.2.pov"])

        self.assertEqual(self._names(plan), {
            "table": "Foo 1.2.vpx", "backglass": "Foo 1.2.directb2s",
            "ini": "Foo 1.2.ini", "script": "Foo 1.2.vbs", "pov": "Foo 1.2.pov"})

    def test_a_replacing_drop_names_them_for_the_table_it_brings(self):
        plan = self._plan(["Foo 1.2.vpx", "Other.directb2s"], add_table=False)

        self.assertEqual(self._names(plan)["backglass"], "Foo 1.2.directb2s")

    def test_without_a_table_they_are_named_for_the_game_s(self):
        plan = self._plan(["Other.directb2s"])

        self.assertEqual(self._names(plan), {"backglass": "Foo.directb2s"})

    def test_a_game_with_no_table_yet_names_them_for_its_folder(self):
        (self.game_dir / "Foo.vpx").unlink()

        plan = self._plan(["Other.directb2s", "Other.ini"])

        self.assertEqual(self._names(plan), {
            "backglass": "Foo (Bar 1999).directb2s", "ini": "Foo (Bar 1999).ini"})

    def test_the_default_table_s_name_wins_over_the_first_one(self):
        (self.game_dir / "Aardvark.vpx").write_bytes(b"x")
        (self.game_dir / "Foo (Bar 1999).info").write_text(
            json.dumps({"tables": {"t1": {"id": "t1", "filename": "Foo.vpx"},
                                   "t2": {"id": "t2", "filename": "Aardvark.vpx"}},
                        "vpinfe": {"default_table": "t1"}}), encoding="utf-8")

        plan = self._plan(["Other.directb2s"])

        self.assertEqual(self._names(plan), {"backglass": "Foo.directb2s"})


class SelectPlanItemsTests(unittest.TestCase):
    def _bundle_plan(self, tmp):
        from pathlib import Path
        zip_path = Path(tmp) / "Medieval Madness.zip"
        make_zip(zip_path, ["Medieval Madness.vpx", "wheel.png",
                            "MyPup/screens.pup", "MyPup/s/a.mp4"])
        analysis = analyze_path(zip_path)
        return build_import_plan(analysis, allow_new_game=True, games_path=tmp)

    def test_none_keeps_all_items(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as tmp:
            plan = self._bundle_plan(tmp)
            self.assertEqual(len(select_plan_items(plan).items), len(plan.items))

    def test_indices_filter_items(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as tmp:
            plan = self._bundle_plan(tmp)
            narrowed = select_plan_items(plan, indices=[0])
            self.assertEqual(len(narrowed.items), 1)
            self.assertEqual(narrowed.items[0].asset.kind, "table")

    def test_rename_rebases_destinations(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as tmp:
            plan = self._bundle_plan(tmp)
            renamed = select_plan_items(plan, new_game_dir_name="Renamed MM")
            self.assertEqual(renamed.new_game_dir_name, "Renamed MM")
            for item in renamed.items:
                self.assertIn(f"{os.sep}Renamed MM{os.sep}", item.destination)

    def test_blank_rename_raises(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as tmp:
            plan = self._bundle_plan(tmp)
            with self.assertRaises(ValueError):
                select_plan_items(plan, new_game_dir_name='<>:"/\\|?*')


def _slot_plan(src, game_dir, media_kind):
    """The plan for a drop of one file on a slot, or the refusal the arrival made."""
    found = arrival.read(src, game_dir=game_dir, media_kind=media_kind)
    return found, (None if found.refusal else arrival.plan(found))


class MediaSlotPlanTests(unittest.TestCase):
    def test_family_validation(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as tmp:
            for filename, media_kind, ok in [
                ("art.png", "wheel", True),
                ("art.jpg", "backglass", True),
                ("clip.mp4", "scoreview_video", True),
                ("song.mp3", "audio", True),
                ("song.ogg", "audio", True),
                ("song.ogg", "audio_launch", True),
                ("rules.pdf", "rule_sheet", True),
                ("rules.txt", "rule_sheet", True),
                ("art.png", "scoreview_video", False),   # image into a video slot
                ("clip.mp4", "wheel", False),      # video into an image slot
                ("song.mp3", "backglass", False),
                ("art.png", "rule_sheet", True),    # a rule sheet is often a picture
                ("clip.mp4", "rule_sheet", False),
                ("rules.pdf", "wheel", False),
            ]:
                with self.subTest(filename=filename, media_kind=media_kind):
                    src = Path(tmp) / filename
                    src.write_bytes(b"x")
                    found, plan = _slot_plan(src, Path(tmp), media_kind)
                    if ok:
                        self.assertEqual(len(plan.items), 1)
                        self.assertEqual(plan.items[0].action, "replace_media")
                        self.assertEqual(plan.items[0].asset.media_kind, media_kind)
                    else:
                        self.assertIsNone(plan)
                        self.assertTrue(found.refusal)

    def test_a_refusal_names_the_slot_and_what_it_takes(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory

        from common.media.media_specs import MEDIA_SPECS
        spec_for_kind = {spec.kind: spec for spec in MEDIA_SPECS}.__getitem__
        with TemporaryDirectory() as tmp:
            for filename, media_kind in (("clip.mp4", "rule_sheet"), ("song.mp3", "wheel"),
                                         ("clip.webp", "audio")):
                with self.subTest(media_kind=media_kind):
                    src = Path(tmp) / filename
                    src.write_bytes(b"x")
                    found, _plan = _slot_plan(src, Path(tmp), media_kind)
                    self.assertEqual(found.refusal, t(
                        "arrival.refusal.nothing_fits", slot=spec_for_kind(media_kind).label,
                        what=t(f"arrival.family.{media_family(media_kind)}")))

    def test_archive_and_unknown_slot_rejected(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as tmp:
            archive = Path(tmp) / "pack.zip"
            archive.write_bytes(b"x")
            found, plan = _slot_plan(archive, Path(tmp), "wheel")
            self.assertIsNone(plan)
            self.assertTrue(found.refusal)
            with self.assertRaises(ValueError):
                arrival.read(archive, game_dir=Path(tmp), media_kind="not_a_slot")

    def test_a_drop_is_the_games_own_file_and_the_games_remove_takes_it(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory

        from common.games import media_placement
        with TemporaryDirectory() as tmp:
            game_dir = Path(tmp) / "Foo (Bar 1999)"
            (game_dir / "medias").mkdir(parents=True)
            fixed = game_dir / "medias" / "logo.png"
            fixed.write_bytes(b"from a catalog")
            src = Path(tmp) / "cool-art.jpg"
            src.write_bytes(b"jpg-bytes")

            _found, plan = _slot_plan(src, game_dir, "logo")
            report = execute_import_plan(plan, src)

            own = game_dir / "medias" / "(Logo) Foo (Bar 1999).jpg"
            self.assertEqual((plan.items[0].destination, own.read_bytes()),
                             (str(own), b"jpg-bytes"))
            self.assertEqual(report["media_kinds"], ["logo"])
            self.assertEqual(fixed.read_bytes(), b"from a catalog")
            self.assertEqual(media_placement.remove(game_dir, "logo", game_dir.name),
                             ["medias/(Logo) Foo (Bar 1999).jpg"])

    def test_a_package_media_file_takes_the_same_name_as_a_drop(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as tmp:
            game_dir = Path(tmp) / "Foo (Bar 1999)"
            game_dir.mkdir()
            (game_dir / "Foo.vpx").write_bytes(b"x")
            zip_path = Path(tmp) / "assets.zip"
            make_zip(zip_path, ["wheel.png"])

            plan = build_import_plan(analyze_path(zip_path), game_dir=game_dir)

            self.assertEqual([item.destination for item in plan.items],
                             [str(game_dir / "medias" / "(Wheel) Foo (Bar 1999).png")])


class MediaSlotReplacesTests(unittest.TestCase):
    """What the confirm says a drop does to the slot, asked of the name it is written at."""

    def _said(self, *present: str) -> str:
        from pathlib import Path
        from tempfile import TemporaryDirectory

        from common.uploads import upload_ops
        with TemporaryDirectory() as tmp:
            game_dir = Path(tmp) / "Foo (Bar 1999)"
            (game_dir / "medias").mkdir(parents=True)
            for name in present:
                (game_dir / "medias" / name).write_bytes(b"x")
            src = Path(tmp) / "art.png"
            src.write_bytes(b"x")
            _found, plan = _slot_plan(src, game_dir, "wheel")
            return upload_ops._plan_to_dict(plan)["items"][0]["replaces"]

    def test_nothing_showing_is_an_empty_slot(self):
        self.assertEqual(self._said(), "slot is empty")

    def test_the_games_own_file_in_another_extension_is_replaced(self):
        self.assertEqual(self._said("(Wheel) Foo (Bar 1999).jpg"), "replaces the current one")

    def test_a_file_at_a_lower_tier_is_named_as_the_one_it_goes_in_front_of(self):
        self.assertEqual(self._said("wheel.png"), "used instead of wheel.png, which stays")


class VpsHelperTests(unittest.TestCase):
    def test_vps_folder_name_variants(self):
        cases = [
            ({"name": "Medieval Madness", "manufacturer": "Bally", "year": "1997"},
             "Medieval Madness (Bally 1997)"),
            ({"name": "Foo", "manufacturer": "Bally", "year": ""}, "Foo (Bally)"),
            ({"name": "Foo", "year": "1997"}, "Foo (1997)"),
            ({"name": "Foo"}, "Foo"),
            ({"name": 'Bad<>:"/\\|?*Name', "manufacturer": "X", "year": "2000"},
             "BadName (X 2000)"),
        ]
        for entry, expected in cases:
            with self.subTest(entry=entry):
                self.assertEqual(vps_folder_name(entry), expected)

    def test_find_vps_entry(self):
        rows = [{"id": "abc123", "name": "Foo"}, {"id": "def456", "name": "Bar"}]
        with mock.patch("common.games.game_service.load_vpsdb", return_value=rows):
            self.assertEqual(find_vps_entry("def456")["name"], "Bar")
            self.assertIsNone(find_vps_entry("nope"))
            self.assertIsNone(find_vps_entry(""))


class ColorFileNameTests(unittest.TestCase):
    """A color file lands under the name its plugin loads, or is refused."""

    def setUp(self) -> None:
        from tempfile import TemporaryDirectory
        held = TemporaryDirectory()
        self.addCleanup(held.cleanup)
        self.tmp = Path(held.name)
        self.game_dir = self.tmp / "Foo (Bar 1999)"
        self.game_dir.mkdir()
        (self.game_dir / "Foo.vpx").write_bytes(b"x")

    def _plan(self, names):
        zip_path = self.tmp / "drop.zip"
        make_zip(zip_path, names)
        return build_import_plan(analyze_path(zip_path), game_dir=self.game_dir,
                                 rom_name="mm_105")

    def _lands(self, plan):
        return sorted(Path(item.destination).relative_to(self.game_dir).as_posix()
                      for item in plan.items)

    def test_a_serum_file_is_named_for_the_rom(self):
        for dropped, loaded in (("anything.crz", "mm_105.cRZ"),
                                ("Other.CROMC", "mm_105.cROMc")):
            with self.subTest(dropped):
                self.assertEqual(self._lands(self._plan([dropped])),
                                 [f"serum/mm_105/{loaded}"])

    def test_a_pal_and_vni_pair_both_arrive_named_for_the_rom(self):
        plan = self._plan(["pack/colors.pal", "pack/colors.vni"])

        self.assertEqual(self._lands(plan), ["vni/mm_105/mm_105.pal", "vni/mm_105/mm_105.vni"])

    def test_the_fallback_name_is_kept(self):
        plan = self._plan(["PIN2DMD.PAL", "pin2dmd.vni"])

        self.assertEqual(self._lands(plan), ["vni/mm_105/pin2dmd.pal", "vni/mm_105/pin2dmd.vni"])

    def test_a_pac_is_refused_because_vni_never_loads_it(self):
        from common.i18n import t

        plan = self._plan(["colors.pac"])

        self.assertEqual((plan.items, blocked_reasons(plan)),
                         ((), {"altcolor_vni": t("error.uploads.pac_not_loaded")}))

    def test_two_files_for_one_name_leave_the_second_refused(self):
        plan = self._plan(["a/one.pal", "b/two.pal"])

        self.assertEqual(len(plan.items), 1)
        self.assertIn("altcolor_vni", blocked_reasons(plan))
        self.assertIn("mm_105.pal", blocked_reasons(plan)["altcolor_vni"])

    def test_a_mixed_group_brings_what_loads_and_refuses_the_pac(self):
        plan = self._plan(["colors.pal", "colors.vni", "colors.pac"])

        self.assertEqual(self._lands(plan), ["vni/mm_105/mm_105.pal", "vni/mm_105/mm_105.vni"])
        self.assertIn("altcolor_vni", blocked_reasons(plan))

    def test_serum_files_differing_only_in_case_clash(self):
        plan = self._plan(["a/x.crz", "b/Y.CRZ"])

        self.assertEqual((len(plan.items), len(plan.blocked)), (1, 1))

    def test_a_rom_with_a_separator_is_refused(self):
        zip_path = self.tmp / "drop.zip"
        make_zip(zip_path, ["x.crz"])

        with self.assertRaises(ValueError):
            build_import_plan(analyze_path(zip_path), game_dir=self.game_dir,
                              rom_name="../up")

    def test_the_report_says_what_was_renamed(self):
        zip_path = self.tmp / "drop.zip"
        make_zip(zip_path, ["colors.crz", "pin2dmd.pal"])
        plan = build_import_plan(analyze_path(zip_path), game_dir=self.game_dir,
                                 rom_name="mm_105")

        report = execute_import_plan(plan, zip_path)

        self.assertEqual(report["renamed"], [{"was": "colors.crz", "now": "mm_105.cRZ"}])
        self.assertTrue((self.game_dir / "serum" / "mm_105" / "mm_105.cRZ").is_file())
        self.assertTrue((self.game_dir / "vni" / "mm_105" / "pin2dmd.pal").is_file())


if __name__ == "__main__":
    unittest.main()
