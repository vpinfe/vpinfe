"""`ctx.uploads`: the import an upload route runs, asked for by an extension.

The pipeline itself is tested where it lives. What is held here is what an extension adds
to it: the scope, the bound on the source, a game named by its id, and requests and
claims that are checked rather than dropped.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any
from unittest import mock

from common import service_errors
from common.extensions import contract, store
from common.extensions.context import ExtensionContext, ExtensionFiles
from common.extensions.contract import ContractError
from common.extensions.games import ExtensionGames
from common.extensions.uploads import EXECUTE_KEYS, PLAN_KEYS, ExtensionUploads
from common.games import convert_preferences
from common.uploads import asset_import_service, upload_ops
from httpapi import models
from tests.support.library import TempTree

BOTH = ("uploads:write", "games:read")


class _Uploads(TempTree):
    def setUp(self) -> None:
        super().setUp()
        self.game = self.root / "Foo (Bar 1999)"
        self.game.mkdir()
        (self.game / "Foo.vpx").write_bytes(b"old")
        (self.game / "Foo (Bar 1999).info").write_text(
            json.dumps({"tables": {"t-old": {"filename": "Foo.vpx"}}}))
        self.incoming = self.root / "incoming"
        self.incoming.mkdir()
        (self.incoming / "Foo 1.2.vpx").write_bytes(b"new")
        (self.incoming / "wheel.png").write_bytes(b"x")
        self.elsewhere = self.root / "elsewhere"
        self.elsewhere.mkdir()
        (self.elsewhere / "Other.vpx").write_bytes(b"x")
        self.games_folder = self.root / "games"
        self.games_folder.mkdir()
        for patched in (mock.patch.object(asset_import_service, "refresh_game"),
                        mock.patch.object(asset_import_service, "_new_games_under",
                                          return_value=str(self.games_folder)),
                        mock.patch("common.games.library_enrichment.read_one",
                                   return_value={}),
                        mock.patch("common.games.game_service.load_vpsdb", return_value=[])):
            patched.start()
            self.addCleanup(patched.stop)
        self.files = ExtensionFiles("probe", allowed=True)
        self.files.set_roots([str(self.incoming)])
        self.games = ExtensionGames("probe", ("games:read",), self.files)
        self.games.folder = lambda game_id: str(self.game)  # type: ignore[method-assign]
        self.uploads = ExtensionUploads("probe", BOTH, self.files, self.games)


class AnalyzeAndPlanTests(_Uploads):
    def test_it_says_what_a_folder_holds(self) -> None:
        found = self.uploads.analyze(self.incoming)

        self.assertTrue(found["has_game"])
        self.assertIn("media", {one["kind"] for one in found["assets"]})

    def test_the_files_pass_the_wire_model(self) -> None:
        upload = upload_ops.begin_over(self.incoming)["id"]
        self.addCleanup(upload_ops.abort, upload)

        found = models.Analysis(**upload_ops.analysis_of(upload))

        self.assertIn("wheel.png", found.files)

    def test_it_lists_every_file_the_source_holds_once(self) -> None:
        (self.incoming / "notes.xyz").write_bytes(b"x")

        found = self.uploads.analyze(self.incoming)

        self.assertIn("wheel.png", found["files"])
        self.assertIn("notes.xyz", found["files"])
        self.assertEqual(len(found["files"]), len(set(found["files"])))

    def test_it_names_the_unrecognized_pictures_an_import_still_offers_places_to(self) -> None:
        (self.incoming / "IMG_0001.png").write_bytes(b"x")
        (self.incoming / "notes.xyz").write_bytes(b"x")

        found = self.uploads.analyze(self.incoming)

        self.assertIn("notes.xyz", found["unrecognized"])
        self.assertEqual(["IMG_0001.png"],
                         [one for one in found["placeable"] if one.startswith(("IMG", "notes"))])

    def test_a_new_game_is_planned_where_the_library_puts_them(self) -> None:
        plan = self.uploads.plan(self.incoming, allow_new_game=True)

        self.assertTrue(plan["new_game_dir_name"])
        self.assertTrue(plan["items"])

    def test_a_game_named_by_its_id_is_planned_into_its_folder(self) -> None:
        plan = self.uploads.plan(self.incoming, game_id="g1", add_table=True)

        self.assertEqual(str(self.game), plan["game_dir"])
        actions = {item["kind"]: item["action"] for item in plan["items"]}
        self.assertEqual("add_table", actions["table"])

    def test_planning_changes_nothing(self) -> None:
        self.uploads.plan(self.incoming, game_id="g1", add_table=True)

        self.assertEqual(b"old", (self.game / "Foo.vpx").read_bytes())
        self.assertEqual(["Foo 1.2.vpx", "wheel.png"],
                         sorted(one.name for one in self.incoming.iterdir()))


class ExecuteTests(_Uploads):
    def test_the_report_says_what_came_in(self) -> None:
        report = self.uploads.execute(self.incoming, game_id="g1", add_table=True)

        self.assertEqual(str(self.game), report["game_dir"])
        self.assertEqual(1, len(report["added_tables"]))
        self.assertTrue((self.game / "Foo 1.2.vpx").is_file())

    def test_the_source_is_left_where_it_was(self) -> None:
        self.uploads.execute(self.incoming, game_id="g1", add_table=True)

        self.assertTrue((self.incoming / "Foo 1.2.vpx").is_file())

    def test_what_the_extension_says_each_file_is_is_recorded(self) -> None:
        said = {"Foo 1.2.vpx": {"host": "example", "host_item_id": "item-9",
                                "vps_file_id": "file-3", "confirmed_by": "declared"}}

        report = self.uploads.execute(self.incoming, game_id="g1", add_table=True,
                                      declared=said)

        self.assertEqual([str(self.game / "Foo 1.2.vpx")], report["declared"])
        info = json.loads((self.game / "Foo (Bar 1999).info").read_text())
        self.assertIn("item-9", json.dumps(info))

    def test_a_claim_without_a_basis_is_refused_and_nothing_comes_in(self) -> None:
        said = {"Foo 1.2.vpx": {"host_item_id": "item-9", "vps_file_id": "file-3"}}

        with self.assertRaises(service_errors.RefusedError):
            self.uploads.execute(self.incoming, game_id="g1", add_table=True, declared=said)

        self.assertFalse((self.game / "Foo 1.2.vpx").exists())

    def test_a_claim_with_a_field_it_does_not_take_is_refused(self) -> None:
        said = {"Foo 1.2.vpx": {"url": "https://example.com/a"}}

        with self.assertRaises(ContractError) as refused:
            self.uploads.execute(self.incoming, game_id="g1", add_table=True, declared=said)

        self.assertIn("url", str(refused.exception))

    def test_a_claim_that_is_not_a_dict_of_text_is_refused(self) -> None:
        wrong: list[Any] = [{"Foo 1.2.vpx": "item-9"},
                            {"Foo 1.2.vpx": {"host": 7, "confirmed_by": "declared"}},
                            {"Foo 1.2.vpx": {"host": None}}]
        for said in wrong:
            with self.subTest(said=said), self.assertRaises(ContractError):
                self.uploads.execute(self.incoming, game_id="g1", add_table=True,
                                     declared=said)

        self.assertFalse((self.game / "Foo 1.2.vpx").exists())

    def test_it_runs_on_a_thread_of_its_own(self) -> None:
        done: list[Any] = []

        def work() -> None:
            done.append(self.uploads.execute(self.incoming, game_id="g1", add_table=True))

        runner = threading.Thread(target=work)
        runner.start()
        runner.join(timeout=30)

        self.assertFalse(runner.is_alive())
        self.assertEqual(1, len(done[0]["added_tables"]))


class SessionTests(_Uploads):
    def _begun(self) -> list[str]:
        begun: list[str] = []
        real = upload_ops.begin_over

        def noting(source: Path) -> dict[str, Any]:
            found = real(source)
            begun.append(found["id"])
            return found

        patched = mock.patch.object(upload_ops, "begin_over", noting)
        patched.start()
        self.addCleanup(patched.stop)
        return begun

    def _ended(self, upload: str) -> bool:
        try:
            upload_ops.analysis_of(upload)
        except service_errors.NotFoundError:
            return True
        return False

    def test_it_ends_after_an_analysis(self) -> None:
        begun = self._begun()

        self.uploads.analyze(self.incoming)

        self.assertTrue(self._ended(begun[0]))

    def test_it_ends_after_a_plan(self) -> None:
        begun = self._begun()

        self.uploads.plan(self.incoming, game_id="g1", add_table=True)

        self.assertTrue(self._ended(begun[0]))

    def test_it_ends_when_the_import_is_refused(self) -> None:
        begun = self._begun()

        with self.assertRaises(service_errors.RefusedError):
            self.uploads.execute(self.incoming, vps_id="no-such-entry", allow_new_game=True)

        self.assertTrue(self._ended(begun[0]))

    def test_it_ends_when_the_import_is_done(self) -> None:
        begun = self._begun()

        self.uploads.execute(self.incoming, game_id="g1", add_table=True)

        self.assertTrue(self._ended(begun[0]))


class TheBoundTests(_Uploads):
    def test_the_scope_is_uploads_write(self) -> None:
        nothing = ExtensionUploads("probe", ("games:read",), self.files, self.games)

        for call in (lambda: nothing.analyze(self.incoming),
                     lambda: nothing.plan(self.incoming),
                     lambda: nothing.execute(self.incoming)):
            with self.subTest(call=call), self.assertRaises(ContractError) as refused:
                call()
            self.assertIn("uploads:write", str(refused.exception))

    def test_a_folder_it_never_declared_is_refused(self) -> None:
        for call in (lambda: self.uploads.analyze(self.elsewhere),
                     lambda: self.uploads.plan(self.elsewhere),
                     lambda: self.uploads.execute(self.elsewhere)):
            with self.subTest(call=call), self.assertRaises(ContractError):
                call()

    def test_a_folder_the_library_could_read_is_still_refused(self) -> None:
        roots = [{"path": str(self.root.resolve()), "name": "root", "source": "library"}]

        with mock.patch("common.games.media_browse.roots", return_value=roots), \
                self.assertRaises(ContractError):
            self.uploads.analyze(self.elsewhere)

    def test_a_link_out_of_a_declared_folder_is_refused(self) -> None:
        (self.incoming / "away").symlink_to(self.elsewhere)

        with self.assertRaises(ContractError):
            self.uploads.analyze(self.incoming / "away")

    def test_an_empty_path_is_refused(self) -> None:
        for path in ("", "  ", None):
            with self.subTest(path=path), self.assertRaises(ContractError):
                self.uploads.analyze(path)  # type: ignore[arg-type]

    def test_a_path_with_nothing_there_is_refused(self) -> None:
        with self.assertRaises(service_errors.RefusedError):
            self.uploads.analyze(self.incoming / "not-here")

    def test_naming_a_game_needs_the_scope_to_read_the_library(self) -> None:
        games = ExtensionGames("probe", (), self.files)
        uploads = ExtensionUploads("probe", ("uploads:write",), self.files, games)

        with self.assertRaises(ContractError) as refused:
            uploads.plan(self.incoming, game_id="g1")

        self.assertIn("games:read", str(refused.exception))


class TheRequestTests(_Uploads):
    def test_a_game_is_not_named_by_its_folder(self) -> None:
        with self.assertRaises(ContractError) as refused:
            self.uploads.plan(self.incoming, game_dir=str(self.game))

        self.assertIn("game_id", str(refused.exception))

    def test_a_key_the_import_does_not_read_is_refused(self) -> None:
        with self.assertRaises(ContractError) as refused:
            self.uploads.plan(self.incoming, add_tables=True)

        self.assertIn("add_tables", str(refused.exception))

    def test_assignments_and_picks_are_taken_as_lists_and_a_mapping_of_text(self) -> None:
        for bad in ({"picks": ["a"]}, {"picks": {"k": 1}}, {"assignments": {"member": "a"}},
                    {"assignments": ["a"]}):
            with self.subTest(bad=bad), self.assertRaises(ContractError):
                self.uploads.plan(self.incoming, game_id="g1", **bad)

    def test_a_game_and_a_new_game_together_are_refused(self) -> None:
        with self.assertRaises(ContractError) as refused:
            self.uploads.execute(self.incoming, game_id="g1", allow_new_game=True)

        self.assertIn("new one", str(refused.exception))
        self.assertEqual([], list(self.games_folder.iterdir()))

    def test_a_rom_name_that_is_not_a_plain_name_is_refused(self) -> None:
        for name in ("..", ".", "../out", "a/b", "a\\b", "/abs"):
            with self.subTest(name=name), self.assertRaises(ContractError) as refused:
                self.uploads.plan(self.incoming, allow_new_game=True, rom_name=name)
            self.assertIn("rom_name", str(refused.exception))

    def test_a_plain_rom_name_is_accepted(self) -> None:
        plan = self.uploads.plan(self.incoming, allow_new_game=True, rom_name="taxi")

        self.assertTrue(plan["items"])

    def test_a_value_of_the_wrong_type_is_refused(self) -> None:
        for asked in ({"add_table": "yes"}, {"allow_new_game": 1}, {"vps_id": 7},
                      {"rom_name": ["taxi"]}, {"selected": "0"}, {"selected": ["0"]},
                      {"selected": [True]}, {"new_game_dir_name": 3}, {"table_id": 2},
                      {"scope": "everything"}, {"page": "2"}, {"page": 0}, {"page": True}):
            with self.subTest(asked=asked), self.assertRaises(ContractError) as refused:
                self.uploads.execute(self.incoming, **asked)
            self.assertIn(next(iter(asked)), str(refused.exception))

    def test_a_plan_does_not_take_what_to_select(self) -> None:
        with self.assertRaises(ContractError):
            self.uploads.plan(self.incoming, game_id="g1", selected=[0])

    def test_an_import_takes_what_to_select(self) -> None:
        plan = self.uploads.plan(self.incoming, game_id="g1", add_table=True)
        table = next(one["index"] for one in plan["items"] if one["kind"] == "table")

        report = self.uploads.execute(self.incoming, game_id="g1", add_table=True,
                                      selected=[table])

        self.assertEqual(["table"], report["imported"])

    def test_the_keys_are_the_ones_the_routes_read(self) -> None:
        route = {"game_id" if key == "game_dir" else key
                 for key in models.PlanRequest.model_fields}

        self.assertEqual(route, set(PLAN_KEYS))
        self.assertEqual(route | {"selected"}, set(EXECUTE_KEYS))
        self.assertEqual(set(models.ImportRequest.model_fields)
                         - {"declared", "game_dir"} | {"game_id"},
                         set(EXECUTE_KEYS))

    def test_a_convert_of_the_wrong_shape_is_refused(self) -> None:
        wrong: list[Any] = ["webp", {"rows": []}, {"rows": {"a.tga": "webp"}},
                            {"rows": {"a.tga": {"format": "webp"}}},
                            {"rows": {"a.tga": {"to": 3}}}, {"wait": "yes"}, {"speed": "fast"}]
        for asked in wrong:
            with self.subTest(asked=asked), self.assertRaises(ContractError) as refused:
                self.uploads.plan(self.incoming, game_id="g1", convert=asked)
            self.assertIn("convert", str(refused.exception))

    def _a_wheel(self) -> Path:
        from PIL import Image

        wheel = self.incoming / "wheel.tga"
        Image.new("RGB", (64, 48), (30, 120, 200)).save(wheel, "TGA")
        return wheel

    def _made(self) -> list[str]:
        return sorted(one.suffix for one in (self.game / "medias").iterdir())

    def test_a_convert_choice_is_the_format_written(self) -> None:
        wheel = self._a_wheel()

        self.uploads.execute(wheel, game_id="g1", media_kind="wheel",
                             convert={"rows": {"wheel.tga": {"to": "webp"}}})

        self.assertEqual([".webp"], self._made())

    def test_a_choice_left_as_none_is_no_choice(self) -> None:
        wheel = self._a_wheel()

        self.uploads.execute(wheel, game_id="g1", media_kind="wheel",
                             convert={"rows": {"wheel.tga": {"to": None, "quality": None}}})

        self.assertEqual([".png"], self._made())

    def test_where_it_passes_none_the_recommendation_is_written(self) -> None:
        wheel = self._a_wheel()

        self.uploads.execute(wheel, game_id="g1", media_kind="wheel")

        self.assertEqual([".png"], self._made())

    def test_where_it_passes_none_the_preference_is_written(self) -> None:
        wheel = self._a_wheel()
        held = convert_preferences.Preferences(picture="jpg")

        with mock.patch.object(convert_preferences, "read", return_value=held):
            self.uploads.execute(wheel, game_id="g1", media_kind="wheel")

        self.assertEqual([".jpg"], self._made())

    def test_a_claim_takes_the_fields_the_routes_take(self) -> None:
        from dataclasses import fields

        from common.games.identity_claims import DeclaredIdentity

        self.assertEqual(set(models.DeclaredIdentity.model_fields),
                         {one.name for one in fields(DeclaredIdentity)})


class OnTheContextTests(_Uploads):
    def test_it_shares_the_folders_and_the_library_the_context_holds(self) -> None:
        manifest = contract.parse({
            "name": "probe", "version": "1.0.0", "author": "Example Author",
            "source": "https://github.com/example/probe", "requires_platform": 1,
            "scopes": list(BOTH), "capabilities": ["fs:read"]})
        ctx = ExtensionContext(manifest, store.ExtensionStore(self.root / "kept"),
                               on_failure=lambda *_, **__: None)
        ctx.files.set_roots([str(self.incoming)])
        ctx.games.folder = lambda game_id: str(self.game)  # type: ignore[method-assign]

        plan = ctx.uploads.plan(self.incoming, game_id="g1", add_table=True)

        self.assertEqual(str(self.game), plan["game_dir"])
        with self.assertRaises(ContractError):
            ctx.uploads.analyze(self.elsewhere)
