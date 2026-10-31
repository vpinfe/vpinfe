"""Patch files: recognized as their own kind, and imported without losing the base.

A patched table is derived from a file that has to stay on disk, which is the one rule
that makes patches different from every other asset the upload path handles.
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest import mock

from common.games.tables import entry_for_filename, is_parsed
from common.i18n import t
from common.uploads import asset_import_service
from common.uploads.asset_analyzer_service import analyze_path
from common.uploads.asset_import_service import build_import_plan, execute_import_plan
from tests.support.uploads import kinds, make_zip


class PatchAssetTests(unittest.TestCase):
    def test_dif_is_claimed_as_a_patch(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "mod.zip")
            make_zip(path, ["CactusCanyon.dif", "CactusCanyon.ini"])
            self.assertEqual(kinds(analyze_path(path)), ["ini", "patch"])

    def test_patch_needs_a_game_to_apply_to(self):
        """A .dif is a delta against one exact base. Without a table there is nothing
        to patch, and applying it to the wrong one corrupts rather than errors."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "mod.zip")
            make_zip(path, ["CactusCanyon.dif"])
            plan = build_import_plan(analyze_path(path), game_dir=Path(tmp))
            self.assertFalse(plan.items)
            self.assertEqual([b.reason for b in plan.blocked],
                             [t("error.uploads.no_table")])

    def test_the_patched_game_takes_the_patch_name(self):
        """A mod's .ini, .directb2s and artwork are named for the mod, and all of them
        are found by matching the .vpx stem."""
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "Table.vpx").write_bytes(b"x" * 64)
            path = os.path.join(tmp, "mod.zip")
            make_zip(path, ["CactusCanyon VPW Mod 1.2.dif"])
            plan = build_import_plan(analyze_path(path), game_dir=Path(tmp))
            self.assertEqual([i.action for i in plan.items], ["apply_patch"])
            dest = Path(plan.items[0].destination)
            self.assertEqual(dest.name, "CactusCanyon VPW Mod 1.2.vpx")

    def test_a_patch_named_after_its_base_is_tagged_instead(self):
        """Writing over the base would destroy it, and it has to survive - the patched
        table cannot be rebuilt without it."""
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "Cactus Canyon.vpx").write_bytes(b"x" * 64)
            path = os.path.join(tmp, "mod.zip")
            make_zip(path, ["cactus canyon.dif"])   # same name, other case
            plan = build_import_plan(analyze_path(path), game_dir=Path(tmp))
            dest = Path(plan.items[0].destination)
            self.assertEqual(dest.name, "cactus canyon [patched].vpx")

    def test_a_patch_named_after_another_table_is_tagged_too(self):
        """The base is whichever .vpx the patch applies to; any other table in the
        folder is just as much somebody's table."""
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "Cactus Canyon.vpx").write_bytes(b"x" * 64)
            Path(tmp, "Cactus Canyon (VR).vpx").write_bytes(b"x" * 32)
            path = os.path.join(tmp, "mod.zip")
            make_zip(path, ["Cactus Canyon (VR).dif"])
            plan = build_import_plan(analyze_path(path), game_dir=Path(tmp))
            dest = Path(plan.items[0].destination)
            self.assertEqual(dest.name, "Cactus Canyon (VR) [patched].vpx")

    def test_a_mods_sidecars_are_named_for_the_patched_table(self):
        """The .ini and .directb2s in a mod bundle describe the table the patch makes.
        Named for the base, they overwrite the base's own and attach to the wrong
        game file."""
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "Cactus Canyon.vpx").write_bytes(b"x" * 64)
            path = os.path.join(tmp, "mod.zip")
            make_zip(path, ["CC VPW Mod 1.2.dif", "CC VPW Mod 1.2.ini",
                             "CC VPW Mod 1.2.directb2s"])
            plan = build_import_plan(analyze_path(path), game_dir=Path(tmp))
            by_kind = {i.asset.kind: Path(i.destination).name for i in plan.items}

            self.assertEqual(by_kind["patch"], "CC VPW Mod 1.2.vpx")
            self.assertEqual(by_kind["ini"], "CC VPW Mod 1.2.ini")
            self.assertEqual(by_kind["backglass"], "CC VPW Mod 1.2.directb2s")

    def test_sidecars_without_a_patch_still_follow_the_game(self):
        """Only a mod bundle redirects them. A backglass dropped on a game is for the
        table that is there."""
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "Cactus Canyon.vpx").write_bytes(b"x" * 64)
            path = os.path.join(tmp, "b2s.zip")
            make_zip(path, ["Whatever It Was Called.directb2s"])
            plan = build_import_plan(analyze_path(path), game_dir=Path(tmp))

            self.assertEqual(Path(plan.items[0].destination).name, "Cactus Canyon.directb2s")

    def test_a_patched_table_records_its_base_and_patch(self):
        """Construction is the one origin we witness, and the result cannot be rebuilt
        without the exact base it was made from."""
        import hashlib
        import json
        import tempfile
        import zipfile
        from pathlib import Path

        from common.uploads.jdiff_patch import EQL, ESC
        with tempfile.TemporaryDirectory() as tmp:
            game_dir = Path(tmp) / "Foo (Bar 1999)"
            game_dir.mkdir()
            base_vpx = game_dir / "Table.vpx"
            base_vpx.write_bytes(b"ABCDEF")
            zip_path = Path(tmp) / "mod.zip"
            with zipfile.ZipFile(zip_path, "w") as archive:
                archive.writestr("Mod.dif", bytes([ESC, EQL, 5]))   # copy all six bytes

            plan = build_import_plan(analyze_path(zip_path), game_dir=game_dir)
            execute_import_plan(plan, zip_path)

            patched = game_dir / "Mod.vpx"
            self.assertEqual(patched.read_bytes(), b"ABCDEF")
            saved = json.loads((game_dir / "Foo (Bar 1999).info").read_text())
            source = entry_for_filename(saved["tables"], patched.name)[1]["source"]
            self.assertEqual(source["base"], {"file": "Table.vpx",
                                              "hash": hashlib.sha256(b"ABCDEF").hexdigest()})
            self.assertEqual(source["patch"]["format"], "jojodiff")

    def test_a_patch_made_for_another_table_says_which_table_it_tried(self):
        import tempfile
        import zipfile

        from common.uploads.jdiff_patch import EQL, ESC
        with tempfile.TemporaryDirectory() as tmp:
            game_dir = Path(tmp) / "Foo (Bar 1999)"
            game_dir.mkdir()
            (game_dir / "Table.vpx").write_bytes(b"ABCDEF")
            zip_path = Path(tmp) / "mod.zip"
            with zipfile.ZipFile(zip_path, "w") as archive:
                archive.writestr("Mod.dif", bytes([ESC, EQL, 50]))  # longer than the base

            plan = build_import_plan(analyze_path(zip_path), game_dir=game_dir)
            with self.assertRaises(ValueError) as caught:
                execute_import_plan(plan, zip_path)

            self.assertEqual(str(caught.exception),
                             t("error.uploads.patch_mismatch", table="Table.vpx"))
            self.assertFalse((game_dir / "Mod.vpx").exists())

    def test_the_patched_table_is_parsed_when_it_is_made(self):
        """Otherwise it sits with no version, ROM or authors until the next metadata
        game file - and it can be the folder's default straight away."""
        import json
        import tempfile
        import zipfile
        from pathlib import Path

        from common.uploads.jdiff_patch import EQL, ESC
        with tempfile.TemporaryDirectory() as tmp:
            game_dir = Path(tmp) / "Foo (Bar 1999)"
            game_dir.mkdir()
            (game_dir / "Table.vpx").write_bytes(b"ABCDEF")
            zip_path = Path(tmp) / "mod.zip"
            with zipfile.ZipFile(zip_path, "w") as archive:
                archive.writestr("Mod.dif", bytes([ESC, EQL, 5]))

            parsed = {"file_hash": "abc123", "version": "1.2", "rom": "mod_rom",
                      "author_name": "VPW", "detect_ssf": True}
            plan = build_import_plan(analyze_path(zip_path), game_dir=game_dir)
            with mock.patch.object(asset_import_service, "VPXParser") as parser:
                parser.return_value.single_file_extract.return_value = parsed
                execute_import_plan(plan, zip_path)

            entry = entry_for_filename(
                json.loads((game_dir / "Foo (Bar 1999).info").read_text())["tables"],
                "Mod.vpx")[1]
            self.assertEqual(entry["rom"], "mod_rom")
            self.assertEqual(entry["version"], "1.2")
            self.assertEqual(entry["authors"], ["VPW"])
            self.assertTrue(entry["detect_ssf"])
            self.assertIn("source", entry, "the parse must not displace where it came from")

    def test_a_table_we_cannot_parse_is_not_recorded_as_empty(self):
        """"Nothing has read this table" is true; "it declares no ROM" is not."""
        import json
        import tempfile
        import zipfile
        from pathlib import Path

        from common.uploads.jdiff_patch import EQL, ESC
        with tempfile.TemporaryDirectory() as tmp:
            game_dir = Path(tmp) / "Foo (Bar 1999)"
            game_dir.mkdir()
            (game_dir / "Table.vpx").write_bytes(b"ABCDEF")
            zip_path = Path(tmp) / "mod.zip"
            with zipfile.ZipFile(zip_path, "w") as archive:
                archive.writestr("Mod.dif", bytes([ESC, EQL, 5]))

            plan = build_import_plan(analyze_path(zip_path), game_dir=game_dir)
            with mock.patch.object(asset_import_service, "VPXParser") as parser:
                parser.return_value.single_file_extract.return_value = None
                execute_import_plan(plan, zip_path)

            entry = entry_for_filename(
                json.loads((game_dir / "Foo (Bar 1999).info").read_text())["tables"],
                "Mod.vpx")[1]
            # Identity and the name are bookkeeping; what must be absent is any
            # parsed field, which would read as "we opened it and it said nothing".
            self.assertEqual(set(entry) - {"id", "filename"}, {"source"})
            self.assertFalse(is_parsed(entry))

    def test_an_unrecordable_source_does_not_fail_the_import(self):
        """The patched table is on disk and playable by then. Losing the provenance is
        worth a warning, not an import the user has to redo."""
        import tempfile
        import zipfile
        from pathlib import Path

        from common.uploads.jdiff_patch import EQL, ESC
        with tempfile.TemporaryDirectory() as tmp:
            game_dir = Path(tmp) / "Foo (Bar 1999)"
            game_dir.mkdir()
            (game_dir / "Table.vpx").write_bytes(b"ABCDEF")
            (game_dir / "Foo (Bar 1999).info").write_text("{not json")
            zip_path = Path(tmp) / "mod.zip"
            with zipfile.ZipFile(zip_path, "w") as archive:
                archive.writestr("Mod.dif", bytes([ESC, EQL, 5]))

            plan = build_import_plan(analyze_path(zip_path), game_dir=game_dir)
            with self.assertLogs("vpinfe.common.uploads.asset_import_service", level="WARNING"):
                execute_import_plan(plan, zip_path)

            self.assertTrue((game_dir / "Mod.vpx").exists())

    def test_the_report_names_the_table_the_patch_made(self):
        import json
        import tempfile
        import zipfile
        from pathlib import Path

        from common.uploads.jdiff_patch import EQL, ESC
        with tempfile.TemporaryDirectory() as tmp:
            game_dir = Path(tmp) / "Foo (Bar 1999)"
            game_dir.mkdir()
            (game_dir / "Table.vpx").write_bytes(b"ABCDEF")
            zip_path = Path(tmp) / "mod.zip"
            with zipfile.ZipFile(zip_path, "w") as archive:
                archive.writestr("Mod.dif", bytes([ESC, EQL, 5]))

            plan = build_import_plan(analyze_path(zip_path), game_dir=game_dir)
            report = execute_import_plan(plan, zip_path)

            saved = json.loads((game_dir / "Foo (Bar 1999).info").read_text())
            table_id = entry_for_filename(saved["tables"], "Mod.vpx")[0]
            self.assertEqual(report["patched_tables"], [table_id])


class PatchBaseTests(unittest.TestCase):
    """A .dif names nothing about the table it applies to, so the folder decides."""

    def _folder(self, tmp: str, tables: dict[str, bytes], entries: dict | None = None) -> Path:
        import json
        game_dir = Path(tmp) / "Foo (Bar 1999)"
        game_dir.mkdir()
        for name, data in tables.items():
            (game_dir / name).write_bytes(data)
        if entries is not None:
            (game_dir / "Foo (Bar 1999).info").write_text(json.dumps({"tables": entries}))
        return game_dir

    def _made(self, filename: str, base: str, data: bytes) -> dict:
        import hashlib
        return {"id": filename, "filename": filename,
                "source": {"base": {"file": base, "hash": hashlib.sha256(data).hexdigest()},
                           "patch": {"format": "jojodiff"}}}

    def test_the_largest_table_is_the_base_where_the_folder_records_nothing(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            game_dir = self._folder(tmp, {"Small.vpx": b"AB", "Large.vpx": b"ABCDEF"})

            chosen = asset_import_service._patch_base(game_dir, game_dir / "Mod.vpx")

            self.assertEqual(chosen.name, "Large.vpx")

    def test_a_table_an_earlier_patch_made_is_never_the_base(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            game_dir = self._folder(
                tmp, {"Other.vpx": b"ABCDEF", "Patched.vpx": b"ABCDEFGHIJ"},
                {"t1": self._made("Patched.vpx", "Gone.vpx", b"ABCDEF")})

            chosen = asset_import_service._patch_base(game_dir, game_dir / "Mod.vpx")

            self.assertEqual(chosen.name, "Other.vpx")

    def test_the_base_an_earlier_patch_used_beats_a_larger_table(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            game_dir = self._folder(
                tmp, {"Table.vpx": b"ABCDEF", "Other.vpx": b"ABCDEFGHIJ",
                      "Patched.vpx": b"ABCDEFG"},
                {"t1": self._made("Patched.vpx", "Table.vpx", b"ABCDEF")})

            chosen = asset_import_service._patch_base(game_dir, game_dir / "Mod.vpx")

            self.assertEqual(chosen.name, "Table.vpx")

    def test_a_recorded_base_that_has_changed_since_does_not_decide(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            game_dir = self._folder(
                tmp, {"Table.vpx": b"CHANGED", "Other.vpx": b"ABCDEFGHIJ",
                      "Patched.vpx": b"ABCDEFG"},
                {"t1": self._made("Patched.vpx", "Table.vpx", b"ABCDEF")})

            chosen = asset_import_service._patch_base(game_dir, game_dir / "Mod.vpx")

            self.assertEqual(chosen.name, "Other.vpx")

    def test_an_unreadable_record_leaves_size_to_decide(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            game_dir = self._folder(tmp, {"Small.vpx": b"AB", "Large.vpx": b"ABCDEF"})
            (game_dir / "Foo (Bar 1999).info").write_text("{not json")

            chosen = asset_import_service._patch_base(game_dir, game_dir / "Mod.vpx")

            self.assertEqual(chosen.name, "Large.vpx")

    def test_a_folder_with_no_table_has_no_base(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            game_dir = self._folder(tmp, {})

            self.assertIsNone(asset_import_service._patch_base(game_dir, game_dir / "Mod.vpx"))


if __name__ == "__main__":
    unittest.main()
