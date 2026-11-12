"""What an import actually writes, and what it refuses to write outside the game folder."""

from __future__ import annotations

import unittest
from unittest import mock

from common.games.tables import entry_for_filename, table_filenames
from common.uploads import asset_import_service
from common.uploads.asset_analyzer_service import analyze_path
from common.uploads.asset_import_service import (
    _safe_dest,
    build_import_plan,
    execute_import_plan,
    merge_info,
    select_plan_items,
)
from tests.support.library import fake_game, game_info
from tests.support.saved import save_record, saved_record
from tests.support.swaps import library_of
from tests.support.uploads import make_zip


class MergeInfoTests(unittest.TestCase):
    def test_info_adopted_only_when_unmatched(self):
        incoming = {"Info": {"VPSId": "new1", "Title": "New"}}
        self.assertEqual(merge_info(incoming, {})["Info"]["VPSId"], "new1")
        self.assertEqual(
            merge_info(incoming, {"Info": {"VPSId": "old1"}})["Info"]["VPSId"], "old1")

    def test_user_fills_gaps_never_replaces(self):
        incoming = {"User": {"Rating": 4, "StartCount": 300, "Tags": ["fav"]}}
        existing = {"User": {"Rating": 0, "StartCount": 7, "Tags": []}}
        merged = merge_info(incoming, existing)["User"]
        self.assertEqual(merged["Rating"], 4)        # local was empty -> filled
        self.assertEqual(merged["StartCount"], 7)    # local had history -> kept
        self.assertEqual(merged["Tags"], ["fav"])    # local empty list -> filled

    def test_vpxfile_and_assets_always_local(self):
        incoming = {"VPXFile": {"filename": "old.vpx"},
                    "assets": {"medias/wheel.png": {"source": {"host": "user"}}}}
        existing = {"VPXFile": {"filename": "local.vpx"}, "assets": {}}
        merged = merge_info(incoming, existing)
        self.assertEqual(merged["VPXFile"]["filename"], "local.vpx")
        self.assertEqual(merged["assets"], {},
                         "an imported list describes files that are not in this folder")

    def test_an_imported_medias_section_does_not_come_back(self):
        """Nothing writes Medias any more, but a .info from a 2.x build still carries
        one. It must not slip in as an unmanaged section and be preserved forever."""
        merged = merge_info({"Medias": {"wheel": {"Source": "user"}}}, {})
        self.assertNotIn("Medias", merged)

    def test_machine_local_overrides_must_resolve(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as tmp:
            real_launcher = Path(tmp) / "vpx-custom"
            real_launcher.write_bytes(b"x")
            incoming = {"vpinfe": {"alt_launcher": "/nonexistent/vpx", "alt_title": "Cool Name"}}
            merged = merge_info(incoming, {"vpinfe": {"alt_launcher": "", "alt_title": ""}})
            self.assertEqual(merged["vpinfe"]["alt_launcher"], "")   # dropped, does not resolve
            self.assertEqual(merged["vpinfe"]["alt_title"], "Cool Name")
            incoming2 = {"vpinfe": {"alt_launcher": str(real_launcher)}}
            merged2 = merge_info(incoming2, {"vpinfe": {"alt_launcher": ""}})
            self.assertEqual(merged2["vpinfe"]["alt_launcher"], str(real_launcher))

    def test_unknown_sections_added_not_replaced(self):
        incoming = {"CustomTool": {"a": 1}, "Shared": {"x": "incoming"}}
        existing = {"Shared": {"x": "local"}}
        merged = merge_info(incoming, existing)
        self.assertEqual(merged["CustomTool"], {"a": 1})
        self.assertEqual(merged["Shared"]["x"], "local")


class GameInfoImportTests(unittest.TestCase):
    def _bundle(self, tmp, info: dict):
        import json
        import zipfile
        from pathlib import Path
        zip_path = Path(tmp) / "bundle.zip"
        with zipfile.ZipFile(zip_path, "w") as archive:
            archive.writestr("Old Name (Mfg 1999).vpx", b"x")
            archive.writestr("Old Name (Mfg 1999).info", json.dumps(info))
        return zip_path

    def test_new_game_adopts_and_renames_info(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        with mock.patch.object(asset_import_service, "refresh_game"):
            with TemporaryDirectory() as tmp:
                zip_path = self._bundle(tmp, {"Info": {"VPSId": "abc"}, "User": {"Rating": 5}})
                analysis = analyze_path(zip_path)
                plan = build_import_plan(analysis, allow_new_game=True, games_path=tmp)
                plan = select_plan_items(plan, None, "New Name (Mfg 2000)")
                execute_import_plan(plan, zip_path)
                dest = Path(tmp) / "New Name (Mfg 2000)" / "New Name (Mfg 2000).info"
                self.assertTrue(dest.exists())
                data = saved_record(dest)
                self.assertEqual(data["User"]["Rating"], 5)

    def test_a_renamed_plan_names_the_files_the_import_writes(self):
        import zipfile
        from pathlib import Path
        from tempfile import TemporaryDirectory
        with mock.patch.object(asset_import_service, "refresh_game"):
            with TemporaryDirectory() as tmp:
                zip_path = self._bundle(tmp, {"Info": {"VPSId": "abc"}})
                with zipfile.ZipFile(zip_path, "a") as archive:
                    archive.writestr("wheel.png", b"x")
                    archive.writestr("Old Name (Mfg 1999).directb2s", b"x")
                plan = build_import_plan(analyze_path(zip_path), allow_new_game=True,
                                         games_path=tmp)
                plan = select_plan_items(plan, None, "New Name (Mfg 2000)")
                shown = {item.asset.kind: Path(item.destination).name for item in plan.items}
                before = {path for path in Path(tmp).rglob("*")}
                execute_import_plan(plan, zip_path)
                written = {path.name for path in Path(tmp).rglob("*") if path not in before}

                self.assertEqual(shown["media"], "(Wheel) New Name (Mfg 2000).png")
                self.assertEqual(shown["game_info"], "New Name (Mfg 2000).info")
                self.assertEqual(shown["backglass"], "Old Name (Mfg 1999).directb2s")
                for kind, name in shown.items():
                    with self.subTest(kind=kind):
                        self.assertIn(name, written)

    def test_existing_game_merges_and_backs_up(self):
        import json
        from pathlib import Path
        from tempfile import TemporaryDirectory
        with mock.patch.object(asset_import_service, "refresh_game"):
            with TemporaryDirectory() as tmp:
                game_dir = Path(tmp) / "Foo (Bar 1999)"
                game_dir.mkdir()
                (game_dir / "Foo.vpx").write_bytes(b"x")
                library_of(self)
                local = {"Info": {"VPSId": "local-id"}, "User": {"Rating": 3, "StartCount": 12}}
                info_path = game_dir / "Foo (Bar 1999).info"
                info_path.write_text(json.dumps(local))
                zip_path = self._bundle(
                    tmp, {"Info": {"VPSId": "foreign-id"}, "User": {"Rating": 5, "StartCount": 99}})
                analysis = analyze_path(zip_path)
                plan = build_import_plan(analysis, game_dir=game_dir)
                execute_import_plan(plan, zip_path)

                data = saved_record(info_path)
                self.assertEqual(data["Info"]["VPSId"], "local-id")     # association kept
                self.assertEqual(data["User"]["Rating"], 3)             # history kept
                self.assertEqual(data["User"]["StartCount"], 12)
                copies = sorted(game_dir.glob("Foo (Bar 1999).info.vpinfe-*-import"))
                self.assertEqual(len(copies), 1)
                kept = saved_record(copies[0])
                self.assertEqual(kept["Info"], local["Info"])
                self.assertEqual({key: kept["User"][key] for key in local["User"]}, local["User"])
                self.assertFalse((game_dir / "Foo (Bar 1999).info.bak").exists())

    def test_a_2x_file_is_kept_as_it_was_before_an_import_rewrites_it(self):
        import json
        from pathlib import Path
        from tempfile import TemporaryDirectory
        with mock.patch.object(asset_import_service, "refresh_game"):
            with TemporaryDirectory() as tmp:
                game_dir = Path(tmp) / "Foo (Bar 1999)"
                game_dir.mkdir()
                (game_dir / "Foo.vpx").write_bytes(b"x")
                library_of(self)
                local = {"Info": {"VPSId": "local-id"}, "User": {"Rating": 3}, "VPXFile": {}}
                info_path = game_dir / "Foo (Bar 1999).info"
                info_path.write_text(json.dumps(local))
                zip_path = self._bundle(tmp, {"Info": {"VPSId": "x"}})
                plan = build_import_plan(analyze_path(zip_path), game_dir=game_dir)
                execute_import_plan(plan, zip_path)

                kept = sorted(game_dir.glob("Foo (Bar 1999).info.vpinfe-????????T??????Z"))
                self.assertEqual(len(kept), 1)
                self.assertEqual(json.loads(kept[0].read_text()), local)
                self.assertEqual(saved_record(info_path)["User"]["Rating"], 3)


class ImportExecuteTests(unittest.TestCase):
    def test_execute_places_assets_in_existing_game(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        with mock.patch.object(asset_import_service, "refresh_game"):
            with TemporaryDirectory() as tmp:
                game_dir = Path(tmp) / "Foo (Bar 1999)"
                game_dir.mkdir()
                (game_dir / "Foo.vpx").write_bytes(b"old")
                (game_dir / "Foo.directb2s").write_bytes(b"old-b2s")
                zip_path = Path(tmp) / "assets.zip"
                make_zip(zip_path, [
                    "Foo.vpx",
                    "roms/mm.zip",
                    "MyPup/screens.pup",
                    "MyPup/s1/a.mp4",
                    "mm.crz",
                ])
                analysis = analyze_path(zip_path)
                plan = build_import_plan(analysis, game_dir=game_dir, rom_name="mm_rom")
                report = execute_import_plan(plan, zip_path)

                self.assertTrue((game_dir / "pinmame" / "roms" / "mm.zip").exists())
                self.assertTrue((game_dir / "pupvideos" / "s1" / "a.mp4").exists())
                self.assertTrue((game_dir / "serum" / "mm_rom" / "mm_rom.cRZ").exists())
                self.assertIn("rom", report["imported"])

    def test_the_game_is_read_again_once_its_files_are_in(self) -> None:
        from pathlib import Path
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as tmp:
            game_dir = Path(tmp) / "Foo (Bar 1999)"
            game_dir.mkdir()
            (game_dir / "Foo.vpx").write_bytes(b"old")
            zip_path = Path(tmp) / "assets.zip"
            make_zip(zip_path, ["MyPup/screens.pup", "MyPup/s1/a.mp4"])
            plan = build_import_plan(analyze_path(zip_path), game_dir=game_dir)
            seen: list[bool] = []
            with mock.patch.object(asset_import_service, "refresh_game",
                                   side_effect=lambda folder: seen.append(
                                       (Path(folder) / "pupvideos" / "s1" / "a.mp4").exists())
                                   ) as refresh:
                execute_import_plan(plan, zip_path)
            refresh.assert_called_with(game_dir)
            self.assertEqual(seen[-1:], [True])

    def test_execute_replace_vpx_restems_backglass(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        with mock.patch.object(asset_import_service, "refresh_game"):
            with TemporaryDirectory() as tmp:
                game_dir = Path(tmp) / "Foo (Bar 1999)"
                game_dir.mkdir()
                (game_dir / "Old.vpx").write_bytes(b"old")
                (game_dir / "Old.directb2s").write_bytes(b"b2s")
                self._held(game_dir, {"Old.vpx": {}})
                zip_path = Path(tmp) / "new.zip"
                make_zip(zip_path, ["New.vpx"])
                analysis = analyze_path(zip_path)
                plan = build_import_plan(analysis, game_dir=game_dir)
                execute_import_plan(plan, zip_path)

                self.assertTrue((game_dir / "New.vpx").exists())
                self.assertFalse((game_dir / "Old.vpx").exists())
                # sibling backglass follows the new vpx stem
                self.assertTrue((game_dir / "New.directb2s").exists())
                self.assertFalse((game_dir / "Old.directb2s").exists())

    def test_a_replacing_drop_s_backglass_is_the_tables_and_the_old_one_is_kept_aside(self):
        """A backglass the drop brings is not overwritten by the carried one, so the
        old table's goes to Backups and the new name has the drop's."""
        from pathlib import Path
        from tempfile import TemporaryDirectory
        with mock.patch.object(asset_import_service, "refresh_game"), \
                TemporaryDirectory() as tmp:
            game_dir = Path(tmp) / "Foo (Bar 1999)"
            game_dir.mkdir()
            (game_dir / "Old.vpx").write_bytes(b"old")
            (game_dir / "Old.directb2s").write_bytes(b"old-b2s")
            self._held(game_dir, {"Old.vpx": {}})
            zip_path = Path(tmp) / "new.zip"
            make_zip(zip_path, ["New.vpx", "New.directb2s"])

            execute_import_plan(build_import_plan(analyze_path(zip_path),
                                                  game_dir=game_dir), zip_path)

            self.assertEqual(sorted(one.name for one in game_dir.iterdir()
                                    if one.suffix != ".info"),
                             ["New.directb2s", "New.vpx"])
            self.assertEqual((game_dir / "New.directb2s").read_bytes(), b"x" * 16)

    def _held(self, game_dir, tables: dict | None = None, parsed: dict | None = None,
              **sections) -> None:
        """Write the game's .info with `tables` keyed by file name, and have the library
        hold it. A table that is named but given no file is given an entry of its own."""
        by_id = {f"t{index}": {"id": f"t{index}", "filename": name, **entry}
                 for index, (name, entry) in enumerate((tables or {}).items())}
        info = game_info(game_dir.name, game_id="Game00000001", tables=by_id)
        for section, values in sections.items():
            info.setdefault(section, {}).update(values)
        save_record(game_dir / f"{game_dir.name}.info", info)
        library_of(self, {"Game00000001": fake_game(game_dir, game_dir.name, meta=info)},
                   parsed=parsed)

    def _replace_game(self, tmp, game_dir, info: dict, new_name: str, parsed):
        """Drop new_name onto an existing game, and hand back the resulting .info."""
        from pathlib import Path
        (game_dir / "Old.vpx").write_bytes(b"old")
        info_path = game_dir / f"{game_dir.name}.info"
        self._held(game_dir, info.get("tables"), parsed,
                   **{k: v for k, v in info.items() if k != "tables"})
        zip_path = Path(tmp) / "new.zip"
        make_zip(zip_path, [new_name])

        plan = build_import_plan(analyze_path(zip_path), game_dir=game_dir)
        with mock.patch.object(asset_import_service, "refresh_game"):
            execute_import_plan(plan, zip_path)
        return saved_record(info_path)

    def test_a_replaced_game_is_described_and_the_old_entry_dropped(self):
        """The new .vpx had no entry until the next metadata build, and the old one kept
        an entry for a file that is gone."""
        from pathlib import Path
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as tmp:
            game_dir = Path(tmp) / "Foo (Bar 1999)"
            game_dir.mkdir()
            saved = self._replace_game(
                tmp, game_dir,
                {"tables": {"Old.vpx": {"file_hash": "old-hash", "rom": "old_rom"}}},
                "New.vpx", {"file_hash": "new-hash", "rom": "new_rom"})

            self.assertEqual(table_filenames(saved["tables"]), ["New.vpx"])
            self.assertEqual(
                entry_for_filename(saved["tables"], "New.vpx")[1]["rom"], "new_rom")

    def test_replacing_the_default_table_keeps_the_vps_match(self):
        """The match names the machine. Replacing one of its files with a newer build
        does not make it a different machine."""
        from pathlib import Path
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as tmp:
            game_dir = Path(tmp) / "Foo (Bar 1999)"
            game_dir.mkdir()
            saved = self._replace_game(
                tmp, game_dir,
                {"vpinfe": {"alt_vpsid": "chosen-against-the-old-file"},
                 "tables": {"Old.vpx": {"file_hash": "old-hash"}}},
                "Old.vpx", {"file_hash": "new-hash"})

            self.assertEqual(saved["vpinfe"]["alt_vpsid"],
                             "chosen-against-the-old-file")

    def test_adding_a_table_does_not_drop_the_vps_override(self):
        """A second table is not a reason to discard the user's match."""
        from pathlib import Path
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as tmp:
            game_dir = Path(tmp) / "Foo (Bar 1999)"
            game_dir.mkdir()
            saved = self._replace_game(
                tmp, game_dir,
                {"vpinfe": {"alt_vpsid": "still-this-machine",
                            "default_table": "Old.vpx"},
                 "tables": {"Old.vpx": {"file_hash": "old-hash"}}},
                "Old.vpx", {"file_hash": "old-hash"})

            self.assertEqual(saved["vpinfe"]["alt_vpsid"], "still-this-machine")

    def test_execute_new_bundle_creates_folder(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        with mock.patch.object(asset_import_service, "refresh_game"):
            with TemporaryDirectory() as tmp:
                zip_path = Path(tmp) / "Medieval Madness.zip"
                make_zip(zip_path, ["Medieval Madness.vpx", "wheel.png"])
                analysis = analyze_path(zip_path)
                plan = build_import_plan(analysis, allow_new_game=True, games_path=tmp)
                report = execute_import_plan(plan, zip_path)
                new_dir = Path(tmp) / "Medieval Madness"
                self.assertTrue((new_dir / "Medieval Madness.vpx").exists())
                self.assertTrue((new_dir / "medias" / "(Wheel) Medieval Madness.png").exists())
                self.assertTrue(report["new_game"])

    def test_execute_new_bundle_collision_raises(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as tmp:
            (Path(tmp) / "Medieval Madness").mkdir()
            zip_path = Path(tmp) / "Medieval Madness.zip"
            make_zip(zip_path, ["Medieval Madness.vpx"])
            analysis = analyze_path(zip_path)
            plan = build_import_plan(analysis, allow_new_game=True, games_path=tmp)
            with self.assertRaises(ValueError):
                execute_import_plan(plan, zip_path)


class AddTableExecuteTests(unittest.TestCase):
    """What adding a table to a game writes."""

    def setUp(self) -> None:
        from pathlib import Path
        from tempfile import TemporaryDirectory
        held = TemporaryDirectory()
        self.addCleanup(held.cleanup)
        self.tmp = Path(held.name)
        self.game_dir = self.tmp / "Foo (Bar 1999)"
        self.game_dir.mkdir()
        (self.game_dir / "Foo.vpx").write_bytes(b"old")
        (self.game_dir / "Foo.directb2s").write_bytes(b"old-b2s")
        self.info = self.game_dir / "Foo (Bar 1999).info"
        save_record(self.info, game_info("Foo", tables={
            "t-old": {"id": "t-old", "filename": "Foo.vpx"}}))
        for patched in (mock.patch.object(asset_import_service, "refresh_game"),
                        mock.patch("common.games.library_enrichment.read_one",
                                   return_value={"rom": "foo_12", "version": "1.2"})):
            patched.start()
            self.addCleanup(patched.stop)

    def _added(self, names, declared=None):
        zip_path = self.tmp / "drop.zip"
        make_zip(zip_path, names)
        plan = build_import_plan(analyze_path(zip_path), game_dir=self.game_dir,
                                 add_table=True)
        report = execute_import_plan(plan, zip_path, declared=declared)
        return report, saved_record(self.info)

    def test_the_game_keeps_its_table_and_gains_one(self):
        report, saved = self._added(["Foo 1.2.vpx", "Foo 1.2.directb2s"])

        self.assertEqual((self.game_dir / "Foo.vpx").read_bytes(), b"old")
        self.assertEqual((self.game_dir / "Foo.directb2s").read_bytes(), b"old-b2s")
        self.assertTrue((self.game_dir / "Foo 1.2.directb2s").exists())
        self.assertEqual(sorted(table_filenames(saved["tables"])),
                         ["Foo 1.2.vpx", "Foo.vpx"])
        self.assertEqual(entry_for_filename(saved["tables"], "Foo.vpx")[0], "t-old")

    def test_the_report_names_the_new_table_described(self):
        report, saved = self._added(["Foo 1.2.vpx"])
        table_id, entry = entry_for_filename(saved["tables"], "Foo 1.2.vpx")

        self.assertEqual(report["added_tables"], [table_id])
        self.assertEqual(entry["rom"], "foo_12")

    def test_a_file_that_arrived_since_the_plan_is_not_overwritten(self):
        zip_path = self.tmp / "drop.zip"
        make_zip(zip_path, ["Foo 1.2.vpx"])
        plan = build_import_plan(analyze_path(zip_path), game_dir=self.game_dir,
                                 add_table=True)
        (self.game_dir / "Foo 1.2.vpx").write_bytes(b"theirs")

        with self.assertRaises(ValueError):
            execute_import_plan(plan, zip_path)
        self.assertEqual((self.game_dir / "Foo 1.2.vpx").read_bytes(), b"theirs")

    def test_each_declared_file_reports_where_it_went(self):
        from pathlib import Path

        from common.games.identity_claims import DeclaredIdentity
        said = DeclaredIdentity(game_id="g1", host="user", confirmed_by="user")

        report, _ = self._added(["Foo 1.2.vpx", "Foo 1.2.directb2s"],
                                {"Foo 1.2.vpx": said, "Foo 1.2.directb2s": said})

        self.assertEqual(sorted(Path(one).name for one in report["declared"]),
                         ["Foo 1.2.directb2s", "Foo 1.2.vpx"])


class FirstTableGetsThePovTests(unittest.TestCase):
    """A POV downloaded before any table sits on the folder's name, which no other table
    reads. The first table to arrive takes it."""

    GAME_ID = "Pov0000001"

    def setUp(self) -> None:
        from pathlib import Path
        from tempfile import TemporaryDirectory

        from common.backups import folder as backup_folder
        from common.games import game_repository, media_ops
        from tests.support.library import fake_game, game_info
        held = TemporaryDirectory()
        self.addCleanup(held.cleanup)
        self.tmp = Path(held.name)
        self.game_dir = self.tmp / "Foo"
        self.game_dir.mkdir()
        self.info = self.game_dir / "Foo.info"
        save_record(self.info, game_info("Foo", game_id=self.GAME_ID))
        (self.game_dir / "Foo.pov").write_bytes(b"pov")

        def current():
            return {self.GAME_ID: fake_game(self.game_dir, "Foo",
                                            meta=saved_record(self.info))}

        for patched in (mock.patch.object(game_repository, "catalog", side_effect=current),
                        mock.patch.object(asset_import_service, "refresh_game"),
                        mock.patch.object(media_ops, "reread"),
                        mock.patch.object(asset_import_service.VPXParser,
                                          "single_file_extract", return_value={"rom": "foo"}),
                        mock.patch.object(backup_folder, "current",
                                          return_value=self.tmp / "backups")):
            patched.start()
            self.addCleanup(patched.stop)

    def _import(self, name: str) -> None:
        zip_path = self.tmp / "drop.zip"
        make_zip(zip_path, [name])
        plan = build_import_plan(analyze_path(zip_path), game_dir=self.game_dir)
        execute_import_plan(plan, zip_path)

    def _povs(self) -> list[str]:
        return sorted(path.name for path in self.game_dir.glob("*.pov"))

    def test_the_first_table_takes_the_pov_the_folder_held(self) -> None:
        self._import("Foo Remix.vpx")

        self.assertEqual(self._povs(), ["Foo Remix.pov"])

    def test_a_table_named_like_the_folder_already_reads_it(self) -> None:
        self._import("Foo.vpx")

        self.assertEqual(self._povs(), ["Foo.pov"])

    def test_a_game_that_had_a_table_keeps_the_pov_where_it_is(self) -> None:
        (self.game_dir / "Foo Old.vpx").write_bytes(b"old")

        self._import("Foo Remix.vpx")

        self.assertEqual(self._povs(), ["Foo.pov"])

    def test_a_failed_move_does_not_fail_the_import(self) -> None:
        with mock.patch.object(asset_import_service.file_move, "to_tables",
                               side_effect=OSError("share gone")):
            self._import("Foo Remix.vpx")

        self.assertTrue((self.game_dir / "Foo Remix.vpx").exists())
        self.assertEqual(self._povs(), ["Foo.pov"])


class TraversalGuardTests(unittest.TestCase):
    def test_safe_dest_rejects_traversal(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            for bad in ["../evil.txt", "/abs/path", "a/../../b", "C:/win"]:
                with self.subTest(path=bad):
                    with self.assertRaises(ValueError):
                        _safe_dest(base, bad)

    def test_safe_dest_allows_nested(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            dest = _safe_dest(base, "a/b/c.txt")
            self.assertTrue(str(dest).startswith(str(base.resolve())))

    def test_malicious_archive_member_blocked_on_extract(self):
        import zipfile
        from pathlib import Path
        from tempfile import TemporaryDirectory
        with mock.patch.object(asset_import_service, "refresh_game"):
            with TemporaryDirectory() as tmp:
                game_dir = Path(tmp) / "Foo (Bar 1999)"
                game_dir.mkdir()
                (game_dir / "Foo.vpx").write_bytes(b"x")
                zip_path = Path(tmp) / "evil.zip"
                with zipfile.ZipFile(zip_path, "w") as archive:
                    archive.writestr("Pack/screens.pup", b"x")
                    archive.writestr("Pack/../../escape.mp4", b"x")
                analysis = analyze_path(zip_path)
                plan = build_import_plan(analysis, game_dir=game_dir)
                with self.assertRaises(ValueError):
                    execute_import_plan(plan, zip_path)


if __name__ == "__main__":
    unittest.main()
