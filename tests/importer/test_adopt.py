"""Converting a source into entries, through the context and nothing else.

The importer holds no path of ours and opens no file of ours. Everything it does to the
library goes through what it was handed, which is the guarantee the whole model rests on
- so a wall it hits here is a gap in the contract rather than a special case to carve.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from fastapi.testclient import TestClient

import httpapi
from common import extensions
from common.extensions import host, store
from common.extensions.contract import ContractError
from common.games import game_repository
from tests.support.library import own_locations

REPO = Path(__file__).resolve().parents[2]
FIXTURE = REPO / "tests" / "fixtures" / "pinballx"
BASE = "/ext/library_importer"
VPX = "Visual Pinball X"


class AdoptCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.library = self.root / "library"
        self.library.mkdir()

        self.locations = own_locations(self, self.root / "config")

        self.store = store.ExtensionStore(self.root)
        self.registry = host.Registry(self.store)
        extensions.set_registry(self.registry)
        self.addCleanup(extensions.set_registry, host.Registry())
        self.addCleanup(self.registry.clear)
        self.record = self.registry.load(host.BUNDLED_DIR / "library_importer")
        self.assertEqual(self.record.state, host.LOADED, self.record.reason)

        self.client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)
        self.client.put("/config/locations/one", json={"path": str(self.library)})
        self.client.put("/config", json={
            "settings": {"updates": {"new_games_location": "one"}}})
        game_repository.all_games(reload=True)
        self.addCleanup(game_repository.all_games, True)

        self.client.put(f"{BASE}/source", json={"path": str(FIXTURE)})

    def imported(self) -> dict:
        """Run the conversion on this thread, so the assertions are about a finished
        import rather than a race with one."""
        from vpinfe_ext_library_importer import adopt, pinballx

        library = pinballx.read(FIXTURE)
        return adopt.run(self.ctx(), library, [VPX])

    def ctx(self):
        """The context the host handed the extension, which is the only thing under
        test here - reaching past it is what the whole design forbids."""
        return self._ctx


class ImportTests(AdoptCase):
    def setUp(self) -> None:
        super().setUp()
        # The same context register() was given, so what the test drives is what runs.
        from common.extensions.context import ExtensionContext
        assert self.record.manifest is not None
        self._ctx = ExtensionContext(self.record.manifest, self.store,
                                     on_failure=lambda why: None)
        self._ctx.files.set_roots([str(FIXTURE)])

    def test_every_game_in_the_system_becomes_an_entry(self) -> None:
        report = self.imported()

        self.assertEqual(report["games"], 3)
        self.assertEqual(report["failed"], 0)

    def test_the_report_records_the_groups_each_game_belongs_to(self) -> None:
        result = self.imported()
        made = {row["name"]: row["game_id"] for row in result["rows"]}

        found = {(one["group"], one["name"]): one["game_id"] for one in result["items"]}

        self.assertEqual(made["Taxi 2 (Homebrew 2020)"],
                         found[("no_wheel", "Taxi 2 (Homebrew 2020)")])
        self.assertIn(("not_matched", "Attack from Mars (Bally 1995)"), found)
        self.assertEqual([], result["left_behind"])

    def test_what_beside_the_tables_could_not_be_read_is_left_behind_in_the_report(self) -> None:
        from vpinfe_ext_library_importer import adopt, saved

        with unittest.mock.patch.object(adopt.saved, "read", return_value=saved.Saved(
                lost=({"what": "VPReg.stg could not be read", "detail": "bad"},))):
            result = self.imported()

        self.assertEqual(result["left_behind"], [
            {"what": "VPReg.stg could not be read", "detail": "bad"}])

    def test_a_folder_is_named_the_way_our_own_are(self) -> None:
        """The source's description is already Title (Manufacturer Year), which is the
        convention we arrived at separately."""
        self.imported()

        self.assertTrue((self.library / "Attack from Mars (Bally 1995)").is_dir())
        self.assertTrue((self.library / "Taxi (Williams 1988)").is_dir())

    def test_what_the_source_knew_about_the_machine_comes_across(self) -> None:
        """The name is the machine, not the folder. PinballX has no title of its own, so
        without lifting one out of the description every imported game would repeat its
        manufacturer and year in the column beside the ones that hold them."""
        self.imported()
        game_repository.all_games(reload=True)

        rows = {row["name"]: row for row in self.client.get("/games").json()["games"]}
        afm = rows["Attack from Mars"]
        self.assertEqual(afm["manufacturer"], "Bally")
        self.assertEqual(afm["year"], "1995")
        self.assertEqual(afm["themes"], ["Aliens", "Outer Space"])

    def test_artwork_lands_in_our_slots(self) -> None:
        self.imported()

        folder = self.library / "Attack from Mars (Bally 1995)"
        landed = sorted(one.name for one in (folder / "medias").iterdir())
        self.assertTrue(any("Wheel" in one for one in landed), landed)
        self.assertTrue(any("Playfield" in one for one in landed), landed)

    def test_the_real_dmd_pictures_instruction_card_and_flyer_front_land(self) -> None:
        self.imported()

        taxi = self.library / "Taxi (Williams 1988)" / "medias"
        landed = sorted(one.name for one in taxi.iterdir())
        for part in ("(RealDMD)", "(RealColorDMD)", "(GameHelp)", "(GameInfo)"):
            self.assertTrue(any(one.startswith(part) for one in landed),
                            (part, landed))

    def test_a_second_build_fills_the_slots_the_first_left_empty_and_replaces_none(
            self) -> None:
        import dataclasses

        from vpinfe_ext_library_importer import adopt, pinballx
        from vpinfe_ext_library_importer.source import SourceMedia

        other = self.root / "other"
        other.mkdir()
        self._ctx.files.set_roots([str(FIXTURE), str(other)])
        (other / "playfield.png").write_bytes(b"the second build's playfield")
        (other / "topper.png").write_bytes(b"the second build's topper")
        read = pinballx.read(FIXTURE)
        taxi = next(game for game in read.games if game.key == "Taxi")
        again = dataclasses.replace(taxi, media=(
            SourceMedia(source_kind="Table Images", path=str(other / "playfield.png")),
            SourceMedia(source_kind="Topper Images", path=str(other / "topper.png"))))
        systems = tuple(
            dataclasses.replace(one, games=(*one.games, again) if any(
                game.key == "Taxi" for game in one.games) else one.games)
            for one in read.systems)

        report = adopt.run(self._ctx, dataclasses.replace(read, systems=systems), [VPX])

        landed = {one.name: one.read_bytes()
                  for one in (self.library / "Taxi (Williams 1988)" / "medias").iterdir()}
        self.assertEqual(sum(1 for one in report["rows"] if one.get("joined")), 1)
        self.assertIn(b"the second build's topper", landed.values())
        self.assertNotIn(b"the second build's playfield", landed.values())

    def _planned(self, cleared: str, fill: bool = False, other_taxi: bool = False):
        """The fixture library with a game file and a backglass behind every game.

        `fill` plans against what the library holds, the way a second run does, with
        *Add what they are missing*. `other_taxi` gives Taxi a game file of another name.
        """
        import dataclasses

        from vpinfe_ext_library_importer import pinballx, plan

        read = pinballx.read(FIXTURE)
        files = self.root / "game files"
        files.mkdir(exist_ok=True)
        self._ctx.files.set_roots([str(FIXTURE), str(files)])

        def with_file(game):
            stem = game.display_name or game.key
            (files / f"{stem}.vpx").write_bytes(b"table")
            (files / f"{stem}.directb2s").write_bytes(b"beside it")
            if other_taxi and game.key == "Taxi":
                stem = "Taxi, the other build"
                (files / f"{stem}.vpx").write_bytes(b"another table")
            return dataclasses.replace(game, table_file=str(files / f"{stem}.vpx"))

        library = dataclasses.replace(read, systems=tuple(
            dataclasses.replace(one, games=tuple(with_file(game) for game in one.games))
            for one in read.systems))
        made = plan.build(library, self._ctx.games.existing() if fill else [],
                          systems=[VPX],
                          ticked=list(plan.row_keys(library).values()) if fill else None,
                          folder_name_for=self._ctx.games.folder_name_for,
                          source_id=library.source_id, kinds=self._ctx.games.kinds(),
                          companions_of=self._ctx.games.companions_of)
        made.sources = plan.derive_sources(library, {cleared: ""} if cleared else {})
        return library, made

    def _counts_with(self, cleared: str) -> dict[str, tuple[int, int, int]]:
        from vpinfe_ext_library_importer import adopt, plan

        library, made = self._planned(cleared)
        report = adopt.run(self._ctx, library, [VPX], "", made)
        return {row["key"]: (row["expected"], row["actual"], row["short"])
                for row in plan.against(plan.expected(made), report)}

    def _brought(self, source_id: str, key: str, files: dict[str, tuple[str, bytes]], **asked):
        """One game with a file in each of the folders given, imported on its own.

        `files` maps a file name to the source folder it sits in and its bytes. Answers the
        row and the names that landed beside the game.
        """
        from vpinfe_ext_library_importer import adopt
        from vpinfe_ext_library_importer.source import SourceGame, SourceMedia

        folder = self.root / f"artwork {key}"
        folder.mkdir()
        self._ctx.files.set_roots([str(FIXTURE), str(folder)])
        media = []
        for name, (source_kind, data) in files.items():
            (folder / name).write_bytes(data)
            media.append(SourceMedia(source_kind=source_kind, path=str(folder / name)))
        game = SourceGame(key=key, media=tuple(media))
        row = adopt._one(self._ctx, source_id, game, self._ctx.games.kinds(), "", **asked)
        landed = self.library / key / "medias"
        return row, sorted(one.name for one in landed.iterdir()) if landed.is_dir() else []

    def _pages_of(self, key: str, kind: str) -> list[bytes]:
        """What a game holds of one kind, page one first."""
        held = {}
        for one in (self.library / key / "medias").iterdir():
            word, _, rest = one.name.partition(") ")
            if word == f"({kind}":
                held[1] = one.read_bytes()
            elif word.startswith(f"({kind} "):
                held[int(word.rpartition(" ")[2])] = one.read_bytes()
        return [held[page] for page in sorted(held)]

    def _filled(self, row, files: dict[str, tuple[str, bytes]]) -> None:
        """What a second build or a fill does with these files: places them without a
        replace, into the game `row` made."""
        from vpinfe_ext_library_importer import adopt
        from vpinfe_ext_library_importer.source import SourceGame, SourceMedia

        folder = self.root / "artwork again"
        folder.mkdir()
        self._ctx.files.set_roots([str(FIXTURE), str(folder)])
        media = []
        for name, (source_kind, data) in files.items():
            (folder / name).write_bytes(data)
            media.append(SourceMedia(source_kind=source_kind, path=str(folder / name)))
        game = SourceGame(key=row["key"], media=tuple(media))
        adopt._place_media(self._ctx, row["game_id"], "pinballx", game,
                           self._ctx.games.kinds(), "", False, dict(row, media=0))

    def test_wheel_images_kept_as_logos_land_in_the_logo_slot(self) -> None:
        _row, landed = self._brought("pinballx", "Doom", {
            "Doom.png": ("Wheel Images", b"a title")}, wheels_as="logo")

        self.assertTrue(any(one.startswith("(Logo)") for one in landed), landed)
        self.assertFalse(any(one.startswith("(Wheel)") for one in landed), landed)

    def test_wheel_images_stay_wheels_unless_asked(self) -> None:
        _row, landed = self._brought("pinballx", "Doom", {
            "Doom.png": ("Wheel Images", b"a wheel")})

        self.assertTrue(any(one.startswith("(Wheel)") for one in landed), landed)

    def test_a_game_with_logos_of_its_own_keeps_its_wheel_a_wheel(self) -> None:
        _row, landed = self._brought("pinballx", "Doom", {
            "Doom.png": ("Wheel Images", b"a wheel"),
            "Doom logo.png": ("Logos", b"a title")}, wheels_as="logo")

        self.assertTrue(any(one.startswith("(Wheel)") for one in landed), landed)
        self.assertTrue(any(one.startswith("(Logo)") for one in landed), landed)

    def _after(self, converted: bool = True, **plan):
        from vpinfe_ext_library_importer import adopt

        ctx = unittest.mock.MagicMock()
        ctx.games.get_missing_media.return_value = {"started": True}
        made = [{"game_id": "g1", "matched": True}, {"game_id": "g2", "matched": True},
                {"game_id": "g1", "matched": True}, {"game_id": "g3"}]
        wanted = {"wheels_as": "wheel", "fetch_media": True, **plan}
        done = adopt._media_after(
            ctx, unittest.mock.Mock(spec=["wheels_as", "fetch_media"], **wanted), made, converted)
        return ctx, done

    def test_missing_media_is_asked_for_once_for_the_matched_games_made(self) -> None:
        ctx, done = self._after()

        ctx.games.get_missing_media.assert_called_once_with(["g1", "g2"])
        self.assertEqual(done, {"wheel_logos": False, "media_started": True})

    def test_the_wheel_shows_logos_only_for_wheel_images_kept_as_logos(self) -> None:
        ctx, done = self._after(wheels_as="logo")
        other, other_done = self._after(wheels_as="wheel")

        ctx.games.use_logos_for_wheels.assert_called_once_with()
        self.assertTrue(done["wheel_logos"])
        other.games.use_logos_for_wheels.assert_not_called()
        self.assertFalse(other_done["wheel_logos"])

    def test_the_wheel_is_left_alone_when_no_wheel_image_became_a_logo(self) -> None:
        ctx, done = self._after(converted=False, wheels_as="logo")

        ctx.games.use_logos_for_wheels.assert_not_called()
        self.assertFalse(done["wheel_logos"])

    def test_no_matched_game_means_no_fill_is_asked_for(self) -> None:
        from vpinfe_ext_library_importer import adopt

        ctx = unittest.mock.MagicMock()
        plan = unittest.mock.Mock(wheels_as="wheel", fetch_media=True)

        done = adopt._media_after(ctx, plan, [{"game_id": "g1"}], False)

        ctx.games.get_missing_media.assert_not_called()
        self.assertFalse(done["media_started"])

    def test_a_stopped_run_neither_switches_the_wheel_nor_asks_for_a_fill(self) -> None:
        from vpinfe_ext_library_importer import adopt

        library, made = self._planned("")
        made.wheels_as, made.fetch_media = "logo", True
        self._ctx.games.use_logos_for_wheels = unittest.mock.Mock()  # type: ignore[attr-defined]
        self._ctx.games.get_missing_media = unittest.mock.Mock()  # type: ignore[attr-defined]

        report = adopt.run(self._ctx, library, [VPX], "", made, halted=lambda: True)

        self.assertTrue(report["stopped"])
        self._ctx.games.use_logos_for_wheels.assert_not_called()
        self._ctx.games.get_missing_media.assert_not_called()

    def test_nothing_is_asked_when_the_switch_is_off(self) -> None:
        ctx, done = self._after(fetch_media=False)

        ctx.games.get_missing_media.assert_not_called()
        self.assertFalse(done["media_started"])

    def test_a_failure_asking_is_said_and_does_not_stop_the_report(self) -> None:
        from vpinfe_ext_library_importer import adopt

        ctx = unittest.mock.MagicMock()
        ctx.games.get_missing_media.side_effect = RuntimeError("down")
        ctx.games.use_logos_for_wheels.side_effect = RuntimeError("down")
        plan = unittest.mock.Mock(wheels_as="logo", fetch_media=True)

        done = adopt._media_after(ctx, plan, [{"game_id": "g1", "matched": True}], True)

        self.assertEqual(done, {"wheel_logos": False, "media_started": False})
        self.assertEqual(ctx.logger.warning.call_count, 2)

    def test_poppers_info_screen_is_the_flyer_and_its_help_screen_the_card(self) -> None:
        row, landed = self._brought("popper", "Doom", {
            "Doom.png": ("GameInfo", b"the flyer"),
            "Doom01.png": ("GameHelp", b"the card")})

        self.assertEqual(row["media"], 2)
        self.assertTrue(any(one.startswith("(GameInfo)") for one in landed), landed)
        self.assertTrue(any(one.startswith("(GameHelp)") for one in landed), landed)

    def test_a_popper_help_set_becomes_pages_in_order_whatever_order_the_folder_lists(
            self) -> None:
        row, landed = self._brought("popper", "Doom", {
            "Doom02.png": ("GameHelp", b"page three"),
            "Doom01.png": ("GameHelp", b"page two"),
            "Doom.png": ("GameHelp", b"page one")})

        self.assertEqual(row["media"], 3)
        self.assertEqual(self._pages_of("Doom", "GameHelp"),
                         [b"page one", b"page two", b"page three"], landed)

    def test_a_flyer_with_front_inside2_and_back_lands_as_pages_one_to_three(self) -> None:
        row, landed = self._brought("pinballx", "Doom", {
            "Doom back.png": ("Flyer Images/Back", b"the back"),
            "Doom two.png": ("Flyer Images/Inside2", b"inside two"),
            "Doom front.png": ("Flyer Images/Front", b"the front")})

        self.assertEqual(row["media"], 3)
        self.assertEqual(self._pages_of("Doom", "GameInfo"),
                         [b"the front", b"inside two", b"the back"], landed)

    def test_numbered_cards_compare_as_numbers_so_ten_comes_last(self) -> None:
        self._brought("pinballx", "Doom", {
            "Doom 10.png": ("Instruction Cards", b"ten"),
            "Doom.png": ("Instruction Cards", b"one"),
            "Doom 2.png": ("Instruction Cards", b"two")})

        self.assertEqual(self._pages_of("Doom", "GameHelp"), [b"one", b"two", b"ten"])

    def test_a_second_build_adds_no_pages_to_a_flyer_the_first_build_made(self) -> None:
        row, _landed = self._brought("pinballx", "Doom", {
            "Doom.png": ("Flyer Images/Front", b"first")})

        self._filled(row, {"Doom.png": ("Flyer Images/Front", b"second"),
                           "Doom back.png": ("Flyer Images/Back", b"second's back")})

        self.assertEqual(self._pages_of("Doom", "GameInfo"), [b"first"])

    def test_a_kind_with_no_first_page_gets_all_of_the_pages_a_second_build_has(self) -> None:
        row, _landed = self._brought("pinballx", "Doom", {
            "Doom.png": ("Flyer Images/Front", b"first")})

        self._filled(row, {"Doom.png": ("Instruction Cards", b"card one"),
                           "Doom 2.png": ("Instruction Cards", b"card two")})

        self.assertEqual(self._pages_of("Doom", "GameHelp"), [b"card one", b"card two"])
        self.assertEqual(self._pages_of("Doom", "GameInfo"), [b"first"])

    def test_a_page_core_refuses_does_not_stop_the_pages_after_it(self) -> None:
        row, _landed = self._brought("pinballx", "Doom", {
            "Doom.pdf": ("Flyer Images/Front", b"not a picture"),
            "Doom i.png": ("Flyer Images/Inside1", b"inside"),
            "Doom b.png": ("Flyer Images/Back", b"back")})

        self.assertEqual(self._pages_of("Doom", "GameInfo"), [b"inside", b"back"])
        self.assertEqual(row["skipped_media"], ["Flyer Images/Front"])

    def test_a_real_dmd_video_lands_in_its_own_slot_and_an_f4v_is_named(self) -> None:
        row, landed = self._brought("pinballx", "Doom", {
            "Doom.mp4": ("Real DMD Videos", b"a clip"),
            "Doom.f4v": ("Real DMD Color Videos", b"a flash clip")})

        self.assertEqual(landed, ["(RealDMD) Doom.mp4"])
        self.assertEqual(row["skipped_media"], ["Real DMD Color Videos"])

    def test_a_video_in_a_picture_folder_never_reaches_core(self) -> None:
        with unittest.mock.patch.object(self._ctx.games, "put_media") as put:
            row, _landed = self._brought("popper", "Doom", {
                "Doom.png": ("GameHelp", b"the card"),
                "Doom.mp4": ("GameHelp", b"a clip")})

        self.assertEqual([Path(call.args[2]).name for call in put.call_args_list],
                         ["Doom.png"])
        self.assertEqual(row["media"], 1)

    def test_a_file_core_refuses_is_named_on_the_row_and_not_counted(self) -> None:
        row, _landed = self._brought("pinballx", "Doom", {
            "Doom.pdf": ("Instruction Cards", b"not a picture")})

        self.assertEqual(row["media"], 0)
        self.assertEqual(row["skipped_media"], ["Instruction Cards"])
        self.assertEqual(row["refused"], ["Instruction Cards"])

    def test_a_cleared_media_source_brings_no_artwork_and_the_report_agrees(self) -> None:
        from vpinfe_ext_library_importer import plan

        self.assertGreater(plan.expected(self._planned("")[1])["media"], 0)

        self.assertEqual((0, 0, 0), self._counts_with("media")["media"])
        folder = self.library / "Attack from Mars (Bally 1995)"
        self.assertFalse((folder / "medias").exists() and any((folder / "medias").iterdir()))

    def test_the_plan_counts_every_page_and_the_report_agrees(self) -> None:
        from vpinfe_ext_library_importer import plan

        counts = self._counts_with("")["media"]

        self.assertEqual(counts[0], counts[1])
        self.assertEqual(counts[2], 0)
        flyers = [one for one in (self.library / "Taxi (Williams 1988)" / "medias").iterdir()
                  if one.name.startswith("(GameInfo")]
        self.assertEqual(len(flyers), 2)
        self.assertEqual(plan.expected(self._planned("")[1])["media"], counts[0])

    def test_a_cleared_tables_source_brings_no_game_files_and_the_report_agrees(self) -> None:
        from vpinfe_ext_library_importer import plan

        wanted = plan.expected(self._planned("")[1])
        self.assertGreater(wanted["tables"], 0)
        self.assertGreater(wanted["companions"], 0)

        counts = self._counts_with("tables")

        self.assertEqual((0, 0, 0), counts["tables"])
        self.assertEqual((0, 0, 0), counts["companions"])

    def test_a_kind_we_have_no_slot_for_is_reported_not_guessed_at(self) -> None:
        report = self.imported()
        rows = {row["key"]: row for row in report["rows"]}

        self.assertEqual(rows["Taxi"]["skipped_media"], [])
        self.assertEqual(rows["Taxi 2"]["skipped_media"], [])

    def test_a_source_whose_tables_are_elsewhere_still_imports(self) -> None:
        """Entries with artwork and no game file, which the library has a word for."""
        report = self.imported()

        self.assertEqual(report["tables"], 0)
        self.assertEqual(report["games"], 3)

    def test_one_game_failing_does_not_stop_the_rest(self) -> None:
        """An import of six hundred stopping on the one folder somebody already had
        would be worse than useless."""
        (self.library / "Taxi (Williams 1988)").mkdir()

        report = self.imported()

        self.assertEqual(report["games"], 2)
        self.assertEqual(report["failed"], 1)
        failed = next(row for row in report["rows"] if row["key"] == "Taxi")
        self.assertEqual(failed["error"]["text"], "no folder could be made for it")
        self.assertEqual(f"Something is already at {self.library / 'Taxi (Williams 1988)'}",
                         failed["error"]["detail"])

    def _with_history(self, *rows: str):
        """The fixture with a GameStats.csv beside it, planned the way the extension
        plans an import."""
        from vpinfe_ext_library_importer import pinballx, plan

        source = self.root / "source"
        shutil.copytree(FIXTURE, source)
        (source / "GameStats.csv").write_bytes(
            "".join(f"{row}\n" for row in ("Game,Play Count,Is Favorite", *rows))
            .encode("utf-16"))
        self._ctx.files.set_roots([str(source)])
        library = pinballx.read(source)
        made = plan.build(library, [], systems=[VPX],
                          folder_name_for=self._ctx.games.folder_name_for,
                          source_id=library.source_id, kinds=self._ctx.games.kinds())
        return library, made

    def test_what_the_source_remembers_about_playing_comes_across(self) -> None:
        from vpinfe_ext_library_importer import adopt

        library, made = self._with_history("Taxi (Williams 1988).Visual Pinball X,33,")

        report = adopt.run(self._ctx, library, [VPX], "", made)

        taxi = next(row for row in report["rows"] if row["key"] == "Taxi")
        self.assertEqual(taxi["history"], 1)
        game = self.client.get(f"/games/{taxi['game_id']}").json()
        self.assertEqual(game["user"]["play_count"], 33)

    PLAYED = ("Taxi (Williams 1988).Visual Pinball X,33,",
              "Attack from Mars (Bally 1995).Visual Pinball X,5,",
              "Taxi 2 (Homebrew 2020).Visual Pinball X,,1",
              "Medieval Madness (Williams 1997).Visual Pinball X,12,")

    def _history_row(self, library, made) -> dict:
        from vpinfe_ext_library_importer import adopt, plan

        want = plan.expected(made)
        report = adopt.run(self._ctx, library, [VPX], "", made)
        return next(row for row in plan.against(want, report) if row["key"] == "history")

    def test_the_report_counts_the_games_whose_play_history_came_across(self) -> None:
        """Taxi 2 holds only a favorite, and the source has no Medieval Madness."""
        row = self._history_row(*self._with_history(*self.PLAYED))

        self.assertEqual((2, 2, 0), (row["expected"], row["actual"], row["short"]))
        self.assertEqual("Games with Play History", row["label"])

    def test_a_game_that_does_not_come_across_leaves_its_play_history_short(self) -> None:
        (self.library / "Taxi (Williams 1988)").mkdir()

        row = self._history_row(*self._with_history(*self.PLAYED))

        self.assertEqual((2, 1, 1), (row["expected"], row["actual"], row["short"]))

    def test_running_it_twice_creates_nothing_the_second_time(self) -> None:
        """It only ever creates, so the second run has nowhere to put anything rather
        than quietly writing over the first."""
        self.imported()

        again = self.imported()

        self.assertEqual(again["games"], 0)
        self.assertEqual(again["failed"], 3)

    def test_adding_what_a_copy_is_missing_fills_it_and_replaces_nothing(self) -> None:
        """A matched game is filled at its own id: an empty slot is filled, a held one
        is left, and a game file joins only where none of that name is there."""
        from vpinfe_ext_library_importer import adopt

        library, made = self._planned("")
        self.assertEqual(0, adopt.run(self._ctx, library, [VPX], "", made)["failed"])
        afm = self.library / "Attack from Mars (Bally 1995)"
        taxi = self.library / "Taxi (Williams 1988)"
        kept = next((taxi / "medias").iterdir())
        kept.write_bytes(b"what somebody chose")
        shutil.rmtree(afm / "medias")
        (taxi / "Taxi.vpx").write_bytes(b"edited since")

        library, made = self._planned("", fill=True, other_taxi=True)
        report = adopt.run(self._ctx, library, [VPX], "", made)

        self.assertEqual(0, report["failed"], [row["error"] for row in report["rows"]])
        self.assertTrue(all(row["game_id"] for row in report["rows"]))
        self.assertTrue(any("Playfield" in one.name for one in (afm / "medias").iterdir()))
        self.assertEqual(b"what somebody chose", kept.read_bytes())
        self.assertEqual(b"edited since", (taxi / "Taxi.vpx").read_bytes())
        self.assertEqual(b"another table", (taxi / "Taxi, the other build.vpx").read_bytes())
        rows = {row["key"]: row for row in report["rows"]}
        self.assertFalse(rows["Attack from Mars (Bally 1995)"]["table"])
        self.assertTrue(rows["Taxi"]["table"])

    def test_a_second_run_fills_nothing_that_is_already_there(self) -> None:
        from vpinfe_ext_library_importer import adopt

        library, made = self._planned("")
        adopt.run(self._ctx, library, [VPX], "", made)

        library, made = self._planned("", fill=True)
        again = adopt.run(self._ctx, library, [VPX], "", made)

        self.assertEqual(0, again["failed"])
        self.assertEqual((0, 0, 0), (again["tables"], again["media"], again["companions"]))

    def _filled_with(self, *rows: str):
        """The fixture and a GameStats.csv, planned against what the library holds."""
        from vpinfe_ext_library_importer import pinballx, plan

        source = self.root / "source"
        shutil.rmtree(source, ignore_errors=True)
        shutil.copytree(FIXTURE, source)
        (source / "GameStats.csv").write_bytes(
            "".join(f"{row}\n" for row in ("Game,Play Count,Is Favorite", *rows))
            .encode("utf-16"))
        self._ctx.files.set_roots([str(source)])
        library = pinballx.read(source)
        made = plan.build(library, self._ctx.games.existing(), systems=[VPX],
                          ticked=list(plan.row_keys(library).values()),
                          folder_name_for=self._ctx.games.folder_name_for,
                          source_id=library.source_id, kinds=self._ctx.games.kinds(),
                          companions_of=self._ctx.games.companions_of)
        return library, made

    def test_filling_adds_the_history_a_game_lacks_and_keeps_what_it_has(self) -> None:
        from vpinfe_ext_library_importer import adopt

        library, made = self._with_history("Taxi (Williams 1988).Visual Pinball X,33,")
        report = adopt.run(self._ctx, library, [VPX], "", made)
        taxi_id = next(row["game_id"] for row in report["rows"] if row["key"] == "Taxi")
        afm_id = next(row["game_id"] for row in report["rows"]
                      if row["key"].startswith("Attack"))
        self._ctx.games.set_play_record(taxi_id, play_count=40)

        library, made = self._filled_with(
            "Taxi (Williams 1988).Visual Pinball X,99,",
            "Attack from Mars (Bally 1995).Visual Pinball X,5,1")
        again = adopt.run(self._ctx, library, [VPX], "", made)

        self.assertEqual(0, again["failed"])
        users = {one: self.client.get(f"/games/{one}").json()["user"]
                 for one in (taxi_id, afm_id)}
        self.assertEqual(40, users[taxi_id]["play_count"])
        self.assertEqual(5, users[afm_id]["play_count"])
        self.assertTrue(users[afm_id]["favorite"])

    def test_a_fill_over_a_complete_library_fills_nothing(self) -> None:
        from vpinfe_ext_library_importer import adopt

        library, made = self._with_history("Taxi (Williams 1988).Visual Pinball X,33,")
        adopt.run(self._ctx, library, [VPX], "", made)

        library, made = self._filled_with("Taxi (Williams 1988).Visual Pinball X,33,")
        again = adopt.run(self._ctx, library, [VPX], "", made)

        self.assertEqual((0, 0), (again["filled"], again["failed"]))

    def test_a_refused_tag_does_not_keep_the_rest_of_the_history_back(self) -> None:
        from vpinfe_ext_library_importer import adopt, plan

        library, made = self._planned("")
        report = adopt.run(self._ctx, library, [VPX], "", made)
        game_id = report["rows"][0]["game_id"]
        adds = plan.Missing(counters={"play_count": 7}, tags=("Odd",), rating=3)

        refused: list[str] = []
        with unittest.mock.patch.object(self._ctx.games, "set_tags",
                                        side_effect=ValueError("derived")), \
                self.assertLogs(self._ctx.logger, "WARNING"):
            counted = adopt._add_missing(self._ctx, game_id, adds, plan.Held(), refused)

        user = self.client.get(f"/games/{game_id}").json()["user"]
        self.assertEqual((1, 7, 6), (counted, user["play_count"], user["rating"]))
        self.assertEqual(refused, [self._ctx.t("refused.history")])

    def test_two_refused_history_writes_are_each_logged_and_named_once(self) -> None:
        from vpinfe_ext_library_importer import adopt, plan

        library, made = self._planned("")
        report = adopt.run(self._ctx, library, [VPX], "", made)
        game_id = report["rows"][0]["game_id"]
        adds = plan.Missing(tags=("Odd",), rating=3)
        refused: list[str] = []

        with unittest.mock.patch.object(self._ctx.games, "set_tags",
                                        side_effect=ValueError("derived")), \
                unittest.mock.patch.object(self._ctx.games, "rate_game",
                                           side_effect=ValueError("range")), \
                self.assertLogs(self._ctx.logger, "WARNING") as logged:
            adopt._add_missing(self._ctx, game_id, adds, plan.Held(), refused)

        self.assertEqual(len(logged.records), 2)
        self.assertEqual(refused, [self._ctx.t("refused.history")])

    def test_filling_takes_the_rom_from_the_game_it_fills(self) -> None:
        """The game file is held and does not arrive again, so the ROM comes from the
        game; without it nothing keyed on the ROM is placed."""
        from vpinfe_ext_library_importer import adopt, plan

        library, made = self._planned("")
        adopt.run(self._ctx, library, [VPX], "", made)
        library, made = self._planned("", fill=True)
        for one in made.sources:
            if one.key == "roms":
                one.path = "/roms"

        with unittest.mock.patch.object(
                adopt, "_held", return_value=plan.Held(rom="taxi_l4")), \
                unittest.mock.patch.object(adopt, "_rom_state", return_value="placed") as bring:
            report = adopt.run(self._ctx, library, [VPX], "", made)

        self.assertEqual(0, report["failed"])
        self.assertEqual({"taxi_l4"}, {call.args[2] for call in bring.call_args_list})
        self.assertEqual(3, report["roms"])


class BoundsTests(AdoptCase):
    def setUp(self) -> None:
        super().setUp()
        from common.extensions.context import ExtensionContext
        assert self.record.manifest is not None
        self._ctx = ExtensionContext(self.record.manifest, self.store,
                                     on_failure=lambda why: None)

    def test_a_file_outside_what_it_declared_is_refused(self) -> None:
        """Tighter than the same check on a route, and it can be: this one knows which
        extension is asking."""
        self._ctx.files.set_roots([str(self.root / "somewhere-else")])
        game_id = self._ctx.games.create("Probe")
        stray = self.root / "stray.png"
        stray.write_bytes(b"art")

        with self.assertRaises(ContractError):
            self._ctx.games.put_media(game_id, "wheel", stray)

    def test_a_scope_the_manifest_never_declared_is_refused(self) -> None:
        """Which is what makes declaring them mean something."""
        from common.extensions.games import ExtensionGames

        modest = ExtensionGames("modest", ("games:read",), self._ctx.files)

        with self.assertRaises(ContractError):
            modest.create("Probe")

    def test_an_unknown_media_kind_is_refused(self) -> None:
        self._ctx.files.set_roots([str(FIXTURE)])
        game_id = self._ctx.games.create("Probe")
        art = (FIXTURE / "Media" / VPX / "Wheel Images"
               / "Attack from Mars (Bally 1995).png")

        with self.assertRaises(ValueError):
            self._ctx.games.put_media(game_id, "hologram", art)


class FolderNameTests(unittest.TestCase):
    """The folder a name becomes is core's rule, and the importer asks rather than
    reproduces it.

    A real library held both `Star Trek: The Next Generation (Williams 1993)` and
    `Star Trek - The Next Generation (Williams 1993)`. Core strips the colon, so the
    first two are one folder; the importer's own copy of the rule did not, so it thought
    they were two and the second failed on a name already taken.
    """

    def test_core_answers_what_a_name_becomes(self) -> None:
        from common.extensions.games import ExtensionGames

        games = ExtensionGames("probe", ("games:read",), None)  # type: ignore[arg-type]

        self.assertEqual(
            games.folder_name_for("Star Trek: The Next Generation (Williams 1993)"),
            "Star Trek The Next Generation (Williams 1993)")

    def test_reading_a_name_is_a_read(self) -> None:
        """It tells an extension what core would do, so it is gated like anything else
        that reads."""
        from common.extensions.games import ExtensionGames

        with self.assertRaises(ContractError):
            ExtensionGames("silent", (), None).folder_name_for("Anything")  # type: ignore[arg-type]


class CompanionTests(unittest.TestCase):
    """A table's backglass and settings come with it.

    Measured on a real library: 696 of 702 tables had a `.directb2s` beside them and 704
    had an `.ini`. Bringing the `.vpx` alone left every one of those behind, which is a
    game with no backglass - the single largest thing the import was losing.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.source = self.root / "Tables"
        self.source.mkdir()

    def _table(self, stem: str, *companions: str) -> Path:
        table = self.source / f"{stem}.vpx"
        table.write_bytes(b"table")
        for one in companions:
            (self.source / one).write_bytes(b"beside it")
        return table

    def test_what_travels_with_a_table(self) -> None:
        from common.games.game_service import companions_beside

        table = self._table("Taxi (Williams 1988)",
                            "Taxi (Williams 1988).directb2s",
                            "Taxi (Williams 1988).ini",
                            "Taxi (Williams 1988).vbs",
                            "Taxi (Williams 1988).pov")

        found = {one.name for one in companions_beside(table)}

        self.assertEqual(len(found), 4)
        self.assertIn("Taxi (Williams 1988).directb2s", found)

    def test_a_neighbour_is_not_a_companion(self) -> None:
        """Matched on the exact stem, or `Taxi.vpx` collects `Taxi 2`'s backglass."""
        from common.games.game_service import companions_beside

        table = self._table("Taxi", "Taxi.directb2s")
        (self.source / "Taxi 2.directb2s").write_bytes(b"another game")

        found = {one.name for one in companions_beside(table)}

        self.assertEqual(found, {"Taxi.directb2s"})

    def test_something_that_is_not_a_companion_kind_stays(self) -> None:
        """The list is the one the export bundle ships, because these are the same
        question asked in two directions."""
        from common.games.game_service import companions_beside

        table = self._table("Taxi", "Taxi.directb2s", "Taxi.png", "Taxi.txt")

        found = {one.name for one in companions_beside(table)}

        self.assertEqual(found, {"Taxi.directb2s"})


class RowShapeTests(unittest.TestCase):
    """Every row the run produces has the same keys.

    The report sums across all of them, so one row built somewhere else and missing a
    key fails the whole import at the end - after the copying, which is the worst moment
    to find out. It happened: a second build of a machine builds its own row, and only
    the first path had been taught the new counts.
    """

    def test_both_kinds_of_row_carry_the_same_counts(self) -> None:
        import inspect

        from extensions.library_importer import adopt

        source = inspect.getsource(adopt)
        rows = [one for one in source.splitlines() if '"key": game.key, "name": name' in one]
        self.assertEqual(len(rows), 2, "expected one row shape per path")

        counted = ("media", "companions", "roms", "altdata", "music", "pups")
        for name in ("_one", "_another_build"):
            body = inspect.getsource(getattr(adopt, name))
            for key in counted:
                with self.subTest(function=name, key=key):
                    self.assertIn(f'"{key}"', body)


class MatchingTests(AdoptCase):
    """A game the look found an entry for is matched once its file is in."""

    def setUp(self) -> None:
        super().setUp()
        from common.extensions.context import ExtensionContext
        assert self.record.manifest is not None
        self._ctx = ExtensionContext(self.record.manifest, self.store,
                                     on_failure=lambda why: None)
        self._ctx.files.set_roots([str(FIXTURE)])

    CATALOG = [{"id": "vps-taxi", "name": "Taxi", "manufacturer": "Williams", "year": 1988}]

    def run_with(self, found: dict[str, str], how: str = "vps_id",
                 catalog: list[dict] | None = None, **chosen: object) -> dict:
        """Run the import with the look's answer for the games named, by source key.

        `chosen` is what the person picked for the plan: names, details, detail_fields.
        """
        from vpinfe_ext_library_importer import adopt, pinballx
        from vpinfe_ext_library_importer import plan as plan_for

        from common.games import auto_match

        held = catalog or self.CATALOG
        library = pinballx.read(FIXTURE)
        looked = {id(game): {"vps_id": found[game.key], "reason": "", "how": how,
                             "entry": auto_match._said(next(
                                 one for one in held if one["id"] == found[game.key]))
                             if found[game.key] in {one["id"] for one in held} else {}}
                  for system in library.systems for game in system.games
                  if game.key in found}
        made = plan_for.build(library, [], systems=[VPX], source_id="pinballx",
                              kinds=self._ctx.games.kinds(), looked=looked,
                              folder_name_for=self._ctx.games.folder_name_for, **chosen)
        with unittest.mock.patch("common.games.game_service.load_vpsdb",
                                 return_value=held):
            return adopt.run(self._ctx, library, [VPX], plan=made)

    PREMIER = [{"id": "vps-taxi", "name": "Taxi", "manufacturer": "Premier", "year": 1989,
                "type": "SS"}]

    def info_of(self, folder: str) -> dict:
        import json

        return json.loads((self.library / folder / f"{folder}.info").read_text("utf-8"))

    def taxi_key(self) -> str:
        from vpinfe_ext_library_importer import pinballx

        games = pinballx.read(FIXTURE).systems[0].games
        return next(game.key for game in games if game.display_name.startswith("Taxi"))

    def taxi(self) -> dict:
        game_repository.all_games(reload=True)
        rows = {row["name"]: row for row in self.client.get("/games").json()["games"]}
        return rows["Taxi"]

    def test_a_new_game_is_matched_and_the_report_counts_it(self) -> None:
        taxi = self.taxi_key()

        report = self.run_with({taxi: "vps-taxi"})

        self.assertEqual(report["matched"], 1)
        self.assertEqual(self.taxi()["vps_id"], "vps-taxi")

    def matched_by(self) -> str:
        game_repository.all_games(reload=True)
        rows = {row["name"]: row for row in self.client.get("/games").json()["games"]}
        return rows["Taxi"]["vps_matched_by"]

    def test_an_entry_the_source_named_is_matched_on_import(self) -> None:
        self.run_with({self.taxi_key(): "vps-taxi"}, how="ipdb")

        self.assertEqual(self.matched_by(), "import")

    def test_an_entry_found_by_name_alone_stays_a_guess_a_rematch_may_replace(self) -> None:
        self.run_with({self.taxi_key(): "vps-taxi"}, how="name")

        self.assertEqual(self.taxi()["vps_id"], "vps-taxi")
        self.assertEqual(self.matched_by(), "")

    def test_a_game_the_look_found_nothing_for_is_made_and_not_matched(self) -> None:
        report = self.run_with({})

        self.assertEqual(report["matched"], 0)
        self.assertEqual(report["games"], 3)
        self.assertFalse(self.taxi()["vps_id"])

    def test_a_matched_game_is_named_and_described_by_its_entry(self) -> None:
        self.run_with({self.taxi_key(): "vps-taxi"}, catalog=self.PREMIER)

        self.assertFalse((self.library / "Taxi (Williams 1988)").exists())
        info = self.info_of("Taxi (Premier 1989)")
        self.assertEqual(info["Info"]["Manufacturer"], "Premier")
        from common.games.game_metadata import GAME_OVERRIDES

        self.assertFalse([key for key, _theirs in GAME_OVERRIDES.values()
                          if key in info.get("vpinfe", {})])

    def test_a_detail_the_entry_leaves_blank_comes_from_the_source(self) -> None:
        blank = [{"id": "vps-taxi", "name": "Taxi", "manufacturer": "Premier"}]

        self.run_with({self.taxi_key(): "vps-taxi"}, catalog=blank, names="source")

        vpinfe = self.info_of("Taxi (Williams 1988)")["vpinfe"]
        self.assertEqual(vpinfe["alt_year"], "1988")
        self.assertNotIn("alt_manufacturer", vpinfe)

    def test_the_source_names_the_folder_where_asked(self) -> None:
        self.run_with({self.taxi_key(): "vps-taxi"}, catalog=self.PREMIER, names="source")

        self.assertTrue((self.library / "Taxi (Williams 1988)").is_dir())

    def test_a_game_nothing_matched_keeps_the_source_name_and_details(self) -> None:
        self.run_with({}, catalog=self.PREMIER)

        info = self.info_of("Taxi (Williams 1988)")
        self.assertEqual(info["vpinfe"]["alt_manufacturer"], "Williams")

    def test_the_source_s_details_are_kept_where_asked(self) -> None:
        self.run_with({self.taxi_key(): "vps-taxi"}, catalog=self.PREMIER,
                      names="source", details="source")

        vpinfe = self.info_of("Taxi (Williams 1988)")["vpinfe"]
        self.assertEqual((vpinfe["alt_manufacturer"], vpinfe["alt_year"]), ("Williams", "1988"))

    def test_only_the_chosen_details_are_kept(self) -> None:
        self.run_with({self.taxi_key(): "vps-taxi"}, catalog=self.PREMIER,
                      names="source", details="some", detail_fields=["manufacturer"])

        vpinfe = self.info_of("Taxi (Williams 1988)")["vpinfe"]
        self.assertEqual(vpinfe["alt_manufacturer"], "Williams")
        self.assertNotIn("alt_year", vpinfe)

    def test_a_match_that_cannot_be_written_leaves_the_source_s_details(self) -> None:
        with self.assertLogs(level="WARNING"):
            self.run_with({self.taxi_key(): "no-such-entry"})

        self.assertEqual(self.info_of("Taxi (Williams 1988)")["vpinfe"]["alt_year"], "1988")

    def test_a_match_that_cannot_be_written_leaves_the_game_made(self) -> None:
        taxi = self.taxi_key()

        with self.assertLogs(level="WARNING"):
            report = self.run_with({taxi: "no-such-entry"})

        self.assertEqual((report["games"], report["matched"], report["failed"]), (3, 0, 0))


class KeyedSetTests(unittest.TestCase):
    """What a source keeps for a game by its ROM or its name, found and placed."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.ctx = unittest.mock.MagicMock()
        self.ctx.games.add_table.return_value = {"companions": (), "rom": "taxi_l4"}

    def made(self, *parts: str, as_file: bool = False) -> Path:
        path = self.root.joinpath(*parts)
        if as_file:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"x")
        else:
            path.mkdir(parents=True, exist_ok=True)
        return path

    def game(self, **fields):
        from vpinfe_ext_library_importer.source import SourceGame

        return SourceGame(key="Taxi", display_name="Taxi (Williams 1988)",
                          table_file=str(self.root / "Taxi.vpx"), **fields)

    def test_a_rom_spelled_in_another_case_is_found(self) -> None:
        from vpinfe_ext_library_importer import adopt

        wanted = self.made("roms", "TAXI_L4.ZIP", as_file=True)

        self.assertEqual(adopt._bring_rom(self.ctx, "g1", "taxi_l4", str(self.root / "roms")), 1)
        self.ctx.games.put_asset.assert_called_once_with("g1", "rom", wanted)

    def test_a_sound_folder_spelled_in_another_case_is_found(self) -> None:
        from vpinfe_ext_library_importer import adopt

        wanted = self.made("AltSound", "TAXI_L4")

        self.assertEqual(adopt._bring_alt_data(
            self.ctx, "g1", "taxi_l4", str(self.root)), 1)
        self.ctx.games.put_asset.assert_called_once_with("g1", "altsound", wanted, "taxi_l4")

    def test_music_and_a_pup_pack_are_found_by_the_name_the_game_goes_by(self) -> None:
        from vpinfe_ext_library_importer import adopt

        music = self.made("Music", "Taxi")
        pack = self.made("PUPVideos", "TAXI_L4")
        self.made("Music", "Somebody Else")
        row = {"music": 0, "pups": 0, "roms": 0, "altdata": 0, "refused": []}

        adopt._bring_keyed(self.ctx, "g1", self.game(), "Taxi (Williams 1988)", "taxi_l4",
                           {"music": str(self.root / "Music"),
                            "pups": str(self.root / "PUPVideos")}, row)

        self.assertEqual((row["music"], row["pups"]), (1, 1))
        self.assertEqual(
            {(call.args[1], call.args[2]) for call in self.ctx.games.put_asset.call_args_list},
            {("music", music), ("pup_pack", pack)})

    def test_a_set_named_for_no_game_here_stays_where_it_is(self) -> None:
        from vpinfe_ext_library_importer import adopt

        self.made("Music", "Somebody Else")
        row = {"music": 0, "pups": 0, "roms": 0, "altdata": 0, "refused": []}

        adopt._bring_keyed(self.ctx, "g1", self.game(), "Taxi (Williams 1988)", "taxi_l4",
                           {"music": str(self.root / "Music")}, row)

        self.assertEqual(row["music"], 0)
        self.ctx.games.put_asset.assert_not_called()

    def test_a_game_with_no_rom_has_nothing_keyed_on_one(self) -> None:
        from vpinfe_ext_library_importer import adopt

        self.made("roms", "taxi_l4.zip", as_file=True)
        row = {"music": 0, "pups": 0, "roms": 0, "altdata": 0, "refused": []}

        adopt._bring_keyed(self.ctx, "g1", self.game(), "Taxi (Williams 1988)", "",
                           {"roms": str(self.root / "roms")}, row)

        self.assertEqual(row["roms"], 0)

    def test_a_second_build_brings_the_rom_its_own_script_names(self) -> None:
        from vpinfe_ext_library_importer import adopt

        self.made("roms", "taxi_l5.zip", as_file=True)
        self.ctx.games.add_table.return_value = {"companions": (), "rom": "taxi_l5"}

        row = adopt._another_build(self.ctx, "pinballx", self.game(), (), "Taxi", "g1",
                                   sources={"roms": str(self.root / "roms")})

        self.assertEqual(row["roms"], 1)
        self.ctx.games.put_asset.assert_called_once_with(
            "g1", "rom", self.root / "roms" / "taxi_l5.zip")

    def test_a_second_build_on_the_same_rom_finds_it_already_there(self) -> None:
        from vpinfe_ext_library_importer import adopt

        self.made("roms", "taxi_l4.zip", as_file=True)
        self.ctx.games.put_asset.side_effect = FileExistsError

        row = adopt._another_build(self.ctx, "pinballx", self.game(), (), "Taxi", "g1",
                                   sources={"roms": str(self.root / "roms")})

        self.assertEqual((row["roms"], row["error"], row["refused"]), (0, "", []))

    def test_a_file_core_refuses_is_named_on_the_row_and_the_game_still_counts(self) -> None:
        from vpinfe_ext_library_importer import adopt

        self.made("roms", "taxi_l4.zip", as_file=True)
        self.ctx.games.put_asset.side_effect = ValueError("not a ROM set")

        row = adopt._another_build(self.ctx, "pinballx", self.game(), (), "Taxi", "g1",
                                   sources={"roms": str(self.root / "roms")})

        self.assertEqual((row["roms"], row["error"], row["refused"]),
                         (0, "", ["taxi_l4.zip"]))


class BesideTheTablesTests(AdoptCase):
    """What a source keeps beside its tables, through the real context and real core.

    The name map, a script the program does not ship, a DMD folder and the backglass
    server's entry each belong to a table because its script mentions them, so the
    table's script is the one thing faked: everything it decides runs for real.
    """

    GRILL = "Plugin.B2SLegacy.B2SHideGrill"
    SCRIPT = {"rom": "taxi_l1",
              "game_data": 'Const cGameName = "taxi_l1"\nExecuteGlobal "Granny.vbs"\n'
                           'x = "Taxi.UltraDMD"\n'}

    def setUp(self) -> None:
        super().setUp()
        from common.extensions.context import ExtensionContext
        from tests.support.launchers_api import put_launcher
        from tests.support.library import own_launchers

        own_launchers(self, self.root / "config")
        for patcher in (
                unittest.mock.patch("apps.vpx.config._machine_folders",
                                    return_value=(self.root / "vpx-home", None)),
                unittest.mock.patch("common.games.library_enrichment.read_one",
                                    return_value=dict(self.SCRIPT))):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client.put("/config/locations/one", json={"path": str(self.library)})
        put_launcher(self.client, "l1", json={"app": "vpx", "settings": {"bin_path": "/opt/vpx"}})
        self.assertEqual(self.client.put("/launchers/l1/config", json={
            "scope": "launcher", "create": True, "values": {self.GRILL: "0"}}).status_code, 200)

        install = self.root / "Pinball"
        self.tables = install / "Tables"
        for folder in (self.tables / "Taxi.UltraDMD", install / "Scripts", install / "User",
                       install / "VPinMAME" / "roms"):
            folder.mkdir(parents=True)
        (self.tables / "Taxi.vpx").write_bytes(b"table")
        (self.tables / "Taxi.UltraDMD" / "a.png").write_bytes(b"png")
        (self.tables / "B2STableSettings.xml").write_text(
            "<B2STableSettings><taxi_l1><HideGrill>1</HideGrill></taxi_l1></B2STableSettings>")
        (install / "Scripts" / "Granny.vbs").write_text("x")
        (install / "Scripts" / "WPC.vbs").write_text("x")
        (install / "VPinMAME" / "VPMAlias.txt").write_text("taxi_l1,taxi_l4\n")
        (install / "VPinMAME" / "roms" / "taxi_l4.zip").write_bytes(b"rom")

        assert self.record.manifest is not None
        self._ctx = ExtensionContext(self.record.manifest, self.store,
                                     on_failure=lambda why: None)
        self.places = {"tables": str(self.tables), "roms": str(install / "VPinMAME" / "roms"),
                       "user": str(install / "User")}
        from vpinfe_ext_library_importer import beside
        self._ctx.files.set_roots([*self.places.values(), *beside.roots(self.places)])

    def run_import(self, **switches) -> dict:
        from vpinfe_ext_library_importer import adopt
        from vpinfe_ext_library_importer.plan import Plan, Source
        from vpinfe_ext_library_importer.source import SourceGame, SourceLibrary, SourceSystem

        game = SourceGame(key="Taxi", display_name="Taxi (Williams 1988)",
                          table_file=str(self.tables / "Taxi.vpx"), manufacturer="Williams")
        plan = Plan(sources=[Source(key, key, "", path) for key, path in self.places.items()],
                    **switches)
        library = SourceLibrary("pinballx", str(self.root),
                                (SourceSystem(name=VPX, games=(game,)),))
        return adopt.run(self._ctx, library, [VPX], plan=plan)

    def test_each_thing_lands_with_the_table_that_asked_for_it(self) -> None:
        report = self.run_import()
        self.assertEqual((report["games"], report["failed"]), (1, 0), report)

        home = self.library / "Taxi (Williams 1988)"
        self.assertEqual((home / "pinmame" / "alias.txt").read_text(), "taxi_l1,taxi_l4\n")
        self.assertTrue((home / "pinmame" / "roms" / "taxi_l4.zip").is_file())
        self.assertTrue((home / "scripts" / "Granny.vbs").is_file())
        self.assertFalse((home / "scripts" / "WPC.vbs").exists())
        self.assertTrue((home / "Taxi.UltraDMD" / "a.png").is_file())

    def test_the_backglass_entry_is_the_tables_own_and_the_launchers_is_unchanged(self) -> None:
        from common.games import launcher_ops

        self.run_import()

        table = (self.library / "Taxi (Williams 1988)" / "Taxi.ini")
        self.assertTrue(table.is_file(), "the table has no settings of its own")
        self.assertRegex(table.read_text(), r"B2SHideGrill\s*=\s*1")
        self.assertEqual(launcher_ops.effective_values("l1")[self.GRILL], "0")


class LogosOnImportTests(BesideTheTablesTests):
    """The source's `Company Logos` reach the logos folder for the makers imported."""

    def setUp(self) -> None:
        super().setUp()
        from vpinfe_ext_library_importer import beside

        from common.media.manufacturer_logos import configure_manufacturer_logos

        self.logos = self.root / "manufacturer_logos"
        configure_manufacturer_logos(self.logos)
        self.addCleanup(configure_manufacturer_logos, None)
        media = self.root / "Pinball" / "Media"
        (media / "Company Logos").mkdir(parents=True)
        (media / "Company Logos" / "Williams.png").write_bytes(b"logo")
        (media / "Company Logos" / "Kodi.png").write_bytes(b"other")
        self.places["media"] = str(media)
        self._ctx.files.set_roots([*self.places.values(), *beside.roots(self.places)])

    def test_the_logo_of_a_maker_imported_is_added_and_the_rest_stay_behind(self) -> None:
        report = self.run_import()

        self.assertEqual(report["logos"], 1, report)
        self.assertEqual([one.name for one in self.logos.iterdir()], ["williams.png"])

    def test_the_switch_off_adds_none(self) -> None:
        report = self.run_import(logos=False)

        self.assertEqual(report["logos"], 0)
        self.assertFalse(self.logos.exists())


if __name__ == "__main__":
    unittest.main()
