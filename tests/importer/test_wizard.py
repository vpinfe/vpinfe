"""The guided task the importer offers, as core sees it.

Core draws the wizard from what the extension declares, so what is pinned here is the
declaration and the three calls behind it - not the drawing, which is core's and is the
same for every task.
"""

from __future__ import annotations

import contextlib
import shutil
import tempfile
import unittest
import unittest.mock
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from nicegui import ui

import httpapi
from common import extensions
from common.extensions import host, store
from common.extensions.contract import ContractError
from manager.kit import wizard

REPO = Path(__file__).resolve().parents[2]
FIXTURE = REPO / "tests" / "fixtures" / "pinballx"
BASE = "/ext/library_importer"


class WizardCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.store = store.ExtensionStore(self.root)
        self.registry = host.Registry(self.store)
        extensions.set_registry(self.registry)
        self.addCleanup(extensions.set_registry, host.Registry())
        self.addCleanup(self.registry.clear)
        self.record = self.registry.load(host.BUNDLED_DIR / "library_importer")
        self.assertEqual(self.record.state, host.LOADED, self.record.reason)
        self.client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)

    def _step(self, path, leaving="source", **values) -> dict:
        """The one step after the one named, with whatever has been answered so far."""
        return self.client.post(
            f"{BASE}/actions/import/check",
            json={"values": {"path": str(path), **values}, "step": leaving}).json()

    def _check(self, path, **values) -> dict:
        """Walk to the summary the way somebody pressing Next does.

        Walked rather than asked for, because the summary is what the earlier steps add
        up to: a test that jumped to it would pass over the thing being described.
        """
        found = self._step(path, **values)
        seen: dict[str, Any] = {}
        while "summary" not in found and found.get("step"):
            for field in found.get("fields") or []:
                seen.setdefault(field["key"], field.get("value"))
            seen.pop("path", None)
            found = self._step(path, leaving=found["step"], **{**seen, **values})
        return found


class LookTests(WizardCase):
    """What is asked of the catalog, and which library an answer belongs to."""

    def test_a_game_known_only_by_its_file_name_is_asked_by_that_name(self) -> None:
        from vpinfe_ext_library_importer import api
        from vpinfe_ext_library_importer.source import SourceGame

        asked = api._asked(SourceGame(key="Taxi (Williams 1988)"))

        self.assertEqual(asked["folder"], "Taxi (Williams 1988)")

    def test_a_look_is_kept_only_for_the_library_it_was_made_for(self) -> None:
        from vpinfe_ext_library_importer.held import Held
        from vpinfe_ext_library_importer.source import SourceLibrary

        held, first, second = Held(), SourceLibrary("x", "/a"), SourceLibrary("x", "/a")
        key = ("a", "b", "c")
        held.keep(key, first)
        held.keep(key, second)

        held.remember_look(first, {"ready": True}, {1: {}})
        self.assertIsNone(held.look_of(first))
        self.assertIsNone(held.look_of(second))

        held.remember_look(second, {"ready": True}, {2: {}})
        self.assertEqual(held.look_of(second), ({"ready": True}, {2: {}}))
        held.keep(key, first)
        self.assertIsNone(held.look_of(second))


class DeclarationTests(WizardCase):
    def test_the_action_is_listed_with_the_extension(self) -> None:
        found = self.client.get("/extensions").json()["extensions"][0]

        action = found["actions"][0]
        self.assertEqual(action["key"], "import")
        self.assertEqual(action["base"], "/actions/import")
        self.assertTrue(action["label"])

    def test_an_action_declares_no_mode(self) -> None:
        """How many steps it has is read off what it answers - the fields it asks
        for and the confirm it names. A declared mode would be a second statement
        of the same thing, and the two come apart."""
        found = self.client.get("/extensions").json()["extensions"][0]
        action = found["actions"][0]

        self.assertEqual(
            sorted(action), ["base", "description", "key", "label", "label_key", "tier"])

    def test_an_extension_that_is_not_running_offers_nothing(self) -> None:
        """A button that refuses is worse than no button."""
        with self.assertLogs("vpinfe.common.extensions", "ERROR"):
            self.registry.disable("library_importer", "asked to")

        found = self.client.get("/extensions").json()["extensions"][0]

        self.assertEqual(found["actions"], [])

    def test_offering_one_without_the_capability_is_refused(self) -> None:
        from common.extensions.context import ExtensionUI

        with self.assertRaises(ContractError):
            ExtensionUI("quiet", allowed=False).action("go", "/x")


class FormTests(WizardCase):
    def test_the_first_step_asks_where_the_library_is(self) -> None:
        found = self.client.get(f"{BASE}/actions/import").json()

        self.assertTrue(found["title"])
        self.assertEqual([one["key"] for one in found["fields"]],
                         ["source_type", "path"])
        self.assertEqual(
            next(one["type"] for one in found["fields"] if one["key"] == "path"),
            "path")


class CheckTests(WizardCase):
    def test_it_says_what_would_come_across(self) -> None:
        """Counted before it runs, so the report afterwards has something to be
        measured against."""
        found = self._check(FIXTURE)

        self.assertTrue(found["ready"])
        summary = dict(one for one in found["summary"] if len(one) == 2)
        self.assertEqual(summary["Games"], "4")
        self.assertEqual(summary["Reads As"], "PinballX / PinballY")

    def test_the_play_history_is_a_file_and_the_rest_are_folders(self) -> None:
        """A folder check marks a file missing, and the history is one file."""
        found = self._step(FIXTURE)
        wants = {one["key"]: one.get("wants") for one in found["fields"]
                 if one["type"] == "path"}

        self.assertEqual(wants["history"], "file")
        self.assertIsNone(wants["tables"])

    def test_a_kind_with_no_source_says_it_is_not_coming(self) -> None:
        """"No Media" should be a choice somebody can see they made, not a silence
        they notice afterwards."""
        found = self._check(FIXTURE, media="")
        summary = dict(one for one in found["summary"] if len(one) == 2)

        self.assertEqual(summary["No Media"], "not being imported")

    def test_every_kind_of_source_is_offered_and_can_be_cleared(self) -> None:
        """Derived where it can be, overridable always, and empty means not imported."""
        found = self._step(FIXTURE)
        fields = {one["key"]: one for one in found["fields"]}

        for key in ("tables", "media"):
            self.assertIn(key, fields)
            self.assertEqual(fields[key]["type"], "path")

    def test_the_notes_travel_with_the_counts(self) -> None:
        """A summary that said four games and stayed quiet about their tables being on a
        machine that is not here would describe an import that will not happen."""
        found = self._check(FIXTURE)

        self.assertTrue(any("not reachable from here" in note
                            for note in found["notes"]))

    def test_the_confirm_says_what_it_will_do(self) -> None:
        self.assertEqual(self._check(FIXTURE)["confirm"], "Import 4 games")

    def test_several_systems_are_a_choice_and_one_is_not(self) -> None:
        found = self._step(FIXTURE)
        fields = {one["key"]: one for one in found["fields"]}

        self.assertIn("systems", fields)
        self.assertEqual(len(fields["systems"]["choices"]), 2)

    def test_a_folder_holding_nothing_readable_is_asked_again(self) -> None:
        """Asked again rather than carried forward. Somebody who typed the wrong folder
        wants that question back, not a summary of nothing four steps later."""
        empty = self.root / "empty"
        empty.mkdir()

        found = self._step(empty)

        self.assertEqual(found["step"], "source")
        self.assertTrue(found["errors"]["path"])

    def test_choosing_the_folder_here_is_what_lets_core_read_it(self) -> None:
        """It is set at the check rather than at the end, because there is nothing to
        summarize until core may read the folder at all."""
        from common.games import media_browse as filesystem

        self._check(FIXTURE)

        self.assertEqual(filesystem.within_roots(str(FIXTURE / "Config")).name, "Config")


class StepOrderTests(WizardCase):
    """Which question comes next, and why it does not depend on how somebody got here."""

    def test_the_source_step_is_followed_by_the_source_map(self) -> None:
        self.assertEqual(self._step(FIXTURE)["step"], "sources")

    def test_going_back_and_forward_asks_the_same_thing_again(self) -> None:
        """The step being left decides what comes next, not what has been answered.

        Answered values decide the *content* of a step; if they decided the order too,
        pressing Back and then Next would skip the step just returned to - which is the
        one thing somebody pressing Back is trying to reach.
        """
        first = self._step(FIXTURE)
        again = self._step(FIXTURE, leaving="source",
                           **{one["key"]: one.get("value")
                              for one in first["fields"] if one["key"] != "path"})

        self.assertEqual(again["step"], first["step"])

    def test_the_games_found_are_listed_before_anything_else_is_asked(self) -> None:
        found = self._step(FIXTURE, leaving="sources")
        field = found["fields"][0]

        self.assertEqual((found["step"], field["type"]), ("games", "checklist"))
        self.assertEqual([one["key"] for one in field["rows"]], field["value"])

    def test_a_caller_that_starts_past_the_list_is_not_shown_it(self) -> None:
        found = self._step(FIXTURE, leaving="sources", skip_existing=True)

        self.assertNotIn(found["step"], ("games", "matches"))
        self.assertEqual(self._step(FIXTURE, leaving=found["step"], skip_existing=True)["step"],
                         "summary")

    def test_the_matches_page_is_left_out_when_every_game_is_settled(self) -> None:
        """No catalog in this test, so no row is left for somebody to look at."""
        found = self._step(FIXTURE, leaving="games")

        self.assertNotEqual(found["step"], "matches")

    def test_the_summary_is_the_last_step(self) -> None:
        found = self._check(FIXTURE)

        self.assertEqual(found["step"], "summary")
        self.assertIn("summary", found)
        self.assertNotIn("fields", found)


class TablesAndMatchesTests(WizardCase):
    CATALOG = [{"id": "vps-taxi", "name": "Taxi", "manufacturer": "Williams", "year": 1988},
               {"id": "vps-zebra", "name": "Zebra Dash", "manufacturer": "Gottlieb",
                "year": 1971}]

    def _with_catalog(self):
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(unittest.mock.patch(
            "common.games.game_service.load_vpsdb", return_value=self.CATALOG))
        stack.enter_context(unittest.mock.patch(
            "common.online.vpsdb_sync.ensure_catalog", return_value=True))

    def _matches(self, **values) -> dict:
        self._with_catalog()
        return self._step(FIXTURE, leaving="games", **values)

    def test_a_game_not_ticked_is_left_out_of_the_summary_counts(self) -> None:
        games = ["Attack from Mars (Bally 1995)", "Taxi"]

        found = self._check(FIXTURE, games=games)

        rows = dict(one for one in found["summary"] if len(one) == 2)
        self.assertEqual(rows["Games"], "2")
        self.assertEqual(rows["Skipped"], "2 not selected")

    def test_nothing_is_left_out_when_every_game_is_ticked(self) -> None:
        found = self._check(FIXTURE)

        self.assertNotIn("Skipped", dict(one for one in found["summary"] if len(one) == 2))

    def test_the_games_the_look_could_not_settle_are_listed_to_look_at(self) -> None:
        found = self._matches()
        field = found["fields"][0]
        settled = {one["key"]: one["settled"] for one in field["rows"]}

        self.assertEqual((found["step"], field["type"]), ("matches", "settle"))
        self.assertEqual(settled, {"Attack from Mars (Bally 1995)": False, "Taxi": True,
                                   "Taxi 2": False, "Big Bang Bar (Capcom 1996)": False})
        self.assertTrue(found["help"].startswith("VPS, the Virtual Pinball Spreadsheet, is "))
        self.assertIn("3 games could not be matched on their own", found["help"])
        self.assertIn("A game you leave is still imported, as Not matched", found["help"])

    def test_ticks_given_about_other_systems_are_not_carried_over(self) -> None:
        narrowed = self._step(FIXTURE, leaving="sources", systems=["Visual Pinball X"],
                              games=["Taxi"])
        widened = self._step(FIXTURE, leaving="sources",
                             systems=["Visual Pinball X", "Visual Pinball X Beta"],
                             games=["Taxi"])

        self.assertEqual(narrowed["fields"][0]["key"], "games")
        field = widened["fields"][0]
        self.assertNotEqual(field["key"], "games")
        self.assertEqual(field["value"], [one["key"] for one in field["rows"]
                                          if "new" in one["groups"]])

    def test_games_named_to_try_again_are_the_only_ones_ticked(self) -> None:
        found = self._step(FIXTURE, leaving="sources", only=["Taxi"])
        field = found["fields"][0]

        self.assertEqual("games", field["key"].split(".")[0])
        self.assertEqual(["Taxi"], field["value"])

    def test_a_tick_made_on_the_tables_page_outranks_the_ones_named_to_try_again(self) -> None:
        summary = self._check(FIXTURE, only=["Taxi"], games=["Taxi 2"])

        rows = dict(one for one in summary["summary"] if len(one) == 2)
        self.assertEqual("1", rows["Games"])

    def test_a_game_left_unticked_is_not_asked_about(self) -> None:
        found = self._matches(games=["Taxi"])

        self.assertNotEqual(found["step"], "matches")

    def test_a_pick_is_matched_on_import_and_counted(self) -> None:
        found = self._matches(picks={"Taxi 2": "vps-zebra"})
        rows = {one["key"]: one for one in found["fields"][0]["rows"]}

        self.assertTrue(rows["Taxi 2"]["settled"])
        self.assertIn("2 games could not be matched on their own", found["help"])

        summary = dict(one for one in self._check(FIXTURE, picks={"Taxi 2": "vps-zebra"})
                       ["summary"] if len(one) == 2)
        self.assertEqual(summary["Matched to VPS"], "2")
        self.assertEqual(summary["Not Matched to VPS"], "2 not found")

    def test_a_pick_that_lands_on_a_game_already_here_leaves_it_unticked(self) -> None:
        held = [{"game_id": "g1", "folder_name": "Zebra Dash (Gottlieb 1971)",
                 "name": "Zebra Dash", "vps_id": "vps-zebra"}]
        self._with_catalog()
        with unittest.mock.patch("common.extensions.games.ExtensionGames.existing",
                                 return_value=held):
            picked = self._check(FIXTURE, picks={"Taxi 2": "vps-zebra"})
            unpicked = self._check(FIXTURE)

        rows = dict(one for one in picked["summary"] if len(one) == 2)
        before = dict(one for one in unpicked["summary"] if len(one) == 2)
        self.assertEqual(rows["Games"], "3")
        self.assertEqual(before["Games"], "4")

    def test_a_game_the_person_says_is_in_no_catalog_is_settled_as_not_matched(self) -> None:
        found = self._matches(picks={"Taxi": ""})
        rows = {one["key"]: one for one in found["fields"][0]["rows"]}

        self.assertTrue(rows["Taxi"]["settled"])
        self.assertEqual(rows["Taxi"]["vps_id"], "")

    def test_an_answer_for_a_game_the_catalog_does_not_hold_is_not_matched(self) -> None:
        self._with_catalog()
        summary = dict(one for one in self._check(FIXTURE, picks={"Taxi 2": "gone"})
                       ["summary"] if len(one) == 2)

        self.assertEqual(summary["Matched to VPS"], "1")


class SummaryShapeTests(WizardCase):
    def test_a_row_of_one_is_a_heading(self) -> None:
        """Which is how the counts get separated from the settings above them without
        the extension deciding anything about how either is drawn."""
        found = self._check(FIXTURE)

        headings = [one[0] for one in found["summary"] if len(one) == 1]
        self.assertEqual(headings, ["What Is Imported"])

    def test_the_counts_come_after_the_heading(self) -> None:
        found = self._check(FIXTURE)
        at = next(i for i, one in enumerate(found["summary"]) if len(one) == 1)

        after = [one[0] for one in found["summary"][at + 1:]]
        self.assertEqual(after, ["Games", "Tables", "Media Files",
                                 "Backglasses and Settings", "Games with Play History",
                                 "Matched to VPS", "Files Not Imported"])

    def test_with_no_catalog_the_summary_says_no_game_is_matched(self) -> None:
        found = self._check(FIXTURE)

        self.assertIn(["VPS Matching", "VPS is not available, so no game is "
                                       "matched"], found["summary"])
        self.assertIn(["Matched to VPS", "0"], found["summary"])

    def test_with_a_catalog_the_summary_counts_the_matches_and_names_the_rest(self) -> None:
        catalog = [{"id": "vps-taxi", "name": "Taxi", "manufacturer": "Williams",
                    "year": 1988}]

        with unittest.mock.patch("common.games.game_service.load_vpsdb",
                                 return_value=catalog), \
                unittest.mock.patch("common.online.vpsdb_sync.ensure_catalog",
                                    return_value=True):
            found = self._check(FIXTURE)

        rows = dict(one for one in found["summary"] if len(one) == 2)
        self.assertEqual(rows["Matched to VPS"], "1")
        self.assertEqual(rows["Not Matched to VPS"], "3 not found")
        self.assertNotIn("VPS Matching", rows)

    def test_with_matching_switched_off_the_summary_says_so(self) -> None:
        with unittest.mock.patch("common.games.auto_match.enabled", return_value=False):
            found = self._check(FIXTURE)

        rows = dict(one for one in found["summary"] if len(one) == 2)
        self.assertEqual(rows["VPS Matching"], "Switched off, so no game is matched")

    def test_the_folders_with_no_slot_are_named_with_their_file_counts(self) -> None:
        """Said before the copy starts, so nobody finds out from the report that a folder
        of theirs was not carried."""
        found = self._check(FIXTURE)

        left = dict(one for one in found["summary"] if len(one) == 2)["Files Not Imported"]
        self.assertEqual(left, "Wheel Images, later pages (1)")

    def test_a_clip_in_the_cards_folder_is_named_as_videos(self) -> None:
        source = self.root / "pinballx"
        shutil.copytree(FIXTURE, source)
        (source / "Media" / "Instruction Cards" / "Taxi.mp4").write_bytes(b"a clip")

        found = self._check(source)

        left = dict(one for one in found["summary"] if len(one) == 2)["Files Not Imported"]
        self.assertIn("Instruction Cards videos (1)", left)

    def test_the_summary_counts_the_games_whose_play_history_comes_across(self) -> None:
        source = self.root / "pinballx"
        shutil.copytree(FIXTURE, source)
        (source / "GameStats.csv").write_text(
            "Game,Play Count\nTaxi (Williams 1988).Visual Pinball X,33\n", encoding="utf-8")

        found = self._check(source)

        self.assertIn(["Games with Play History", "1"], found["summary"])

    def test_a_database_it_could_not_read_hovers_why_in_the_manager(self) -> None:
        source = self.root / "pinballx"
        shutil.copytree(FIXTURE, source)
        (source / "Databases" / "Visual Pinball X" / "Visual Pinball X.xml").write_text(
            '<menu>\n<game name="A & B"/></menu>')

        found = self._check(source)
        with ui.column() as body:
            wizard._lines(found["notes"], "")
        tips = {one.props["target"]: one.text
                for one in body.descendants() if isinstance(one, ui.tooltip)}
        hovers = {one.text: tips.get(f"#{one.html_id}", "")
                  for one in body.descendants() if isinstance(one, ui.label)}

        self.assertEqual("Line 2 is not written as it should be",
                         hovers["Visual Pinball X.xml could not be read"])

    def test_what_reading_the_play_history_said_is_on_the_summary(self) -> None:
        source = self.root / "pinballx"
        shutil.copytree(FIXTURE, source)
        (source / "GameStats.csv").write_bytes(
            b"Game,Play Count\nCaf\xe9.Visual Pinball X,33\n")

        found = self._check(source)

        self.assertIn("GameStats.csv is in no encoding this reads cleanly, so some "
                      "characters may be wrong", found["notes"])


class RomSummaryTests(WizardCase):
    CATALOG = [{"id": "vps-taxi", "name": "Taxi", "manufacturer": "Williams", "year": 1988,
                "type": "SS"}]

    def summary(self, source=FIXTURE, **values) -> dict[str, str]:
        with unittest.mock.patch("common.games.game_service.load_vpsdb",
                                 return_value=self.CATALOG), \
                unittest.mock.patch("common.online.vpsdb_sync.ensure_catalog",
                                    return_value=True), \
                unittest.mock.patch(
                    "common.extensions.games.ExtensionGames.reads_of",
                    side_effect=lambda paths, names=(): {
                        str(one): ("taxi_l4", ()) for one in paths}):
            found = self._check(source, **{"into": str(self.root / "library"), **values})
        return {row[0]: row[1] for row in found["summary"] if len(row) == 2}

    def installed(self) -> Path:
        """A copy of the fixture whose game files are on disk, Taxi's among them."""
        source = self.root / "frontend"
        shutil.copytree(FIXTURE, source)
        tables = source / "tables"
        tables.mkdir()
        (tables / "Taxi.vpx").write_bytes(b"table")
        (self.root / "library").mkdir()
        ini = source / "Config" / "PinballX.ini"
        ini.write_bytes(ini.read_text(encoding="utf-16")
                        .replace(r"C:\Visual Pinball\tables", str(tables))
                        .encode("utf-16"))
        return source

    def test_a_folder_that_was_looked_for_and_not_found_says_that(self) -> None:
        rows = self.summary()

        self.assertEqual(rows["No ROMs"], "No ROM folder was found")
        self.assertEqual(rows["No Music"], "No music folder was found")

    def test_a_folder_left_empty_on_purpose_says_it_is_not_being_imported(self) -> None:
        source = self.installed()
        (source / "Music").mkdir()

        rows = self.summary(source, music="")

        self.assertEqual(rows["No Music"], "not being imported")

    def test_the_games_that_need_a_rom_the_folder_lacks_are_counted(self) -> None:
        roms = self.root / "roms"
        roms.mkdir()

        rows = self.summary(self.installed(), roms=str(roms))

        self.assertEqual(rows["Missing ROMs"], "1 table needs a ROM that is not there")

    def test_a_rom_the_folder_holds_in_another_case_is_not_missing(self) -> None:
        roms = self.root / "roms"
        roms.mkdir()
        (roms / "TAXI_L4.ZIP").write_bytes(b"r")

        rows = self.summary(self.installed(), roms=str(roms))

        self.assertNotIn("Missing ROMs", rows)

    def test_nothing_is_said_of_missing_roms_where_none_are_imported(self) -> None:
        rows = self.summary(self.installed(), roms="")

        self.assertNotIn("Missing ROMs", rows)

    def rom_folder(self) -> str:
        roms = self.root / "roms"
        roms.mkdir(exist_ok=True)
        (roms / "taxi_l4.zip").write_bytes(b"r")
        return str(roms)

    def things(self, ticking: list | None = None, **extra) -> dict:
        """The ROMs and more page, reached the way somebody pressing Next reaches it.
        `ticking` is the answer to the games page, where it is not left as offered."""
        source = self.installed()
        answers = {"roms": self.rom_folder(), "into": str(self.root / "library"), **extra}
        with unittest.mock.patch("common.games.game_service.load_vpsdb",
                                 return_value=self.CATALOG), \
                unittest.mock.patch("common.online.vpsdb_sync.ensure_catalog",
                                    return_value=True), \
                unittest.mock.patch(
                    "common.extensions.games.ExtensionGames.reads_of",
                    side_effect=lambda paths, names=(): {
                        str(one): ("taxi_l4", ()) for one in paths}):
            found = self._step(source, leaving="sources", **answers)
            while found.get("step") not in ("things", "summary"):
                if found["step"] == "games" and ticking is not None:
                    answers[found["fields"][0]["key"]] = ticking
                found = self._step(source, leaving=found["step"], **answers)
        return found

    def test_the_roms_a_game_comes_with_are_counted_on_a_switch(self) -> None:
        found = self.things()

        self.assertEqual(found["step"], "things")
        field = found["fields"][0]
        self.assertEqual((field["key"], field["label"], field["value"], field["help"]),
                         ("use_roms", "ROMs", True, "1 game"))

    def test_a_thing_switched_off_is_left_out_of_what_comes_across(self) -> None:
        rows = self.summary(self.installed(), roms=self.rom_folder(), use_roms=False)

        self.assertEqual(rows["ROMs"], "Skipped")

    def with_scores(self) -> None:
        nvram = self.root / "nvram"
        nvram.mkdir(exist_ok=True)
        (nvram / "taxi_l4.nv").write_bytes(b"scores")

    def switches(self, **extra) -> dict[str, object]:
        return {one["key"]: one["value"] for one in self.things(**extra)["fields"]}

    def test_the_scores_a_rom_has_are_offered_beside_it(self) -> None:
        self.with_scores()

        self.assertEqual(self.switches(), {"use_roms": True, "use_scores": True})

    def test_high_scores_that_travel_with_a_rom_go_when_roms_are_switched_off(self) -> None:
        self.with_scores()

        self.assertEqual(self.switches(use_roms=False), {"use_roms": False})

    def test_a_switch_the_person_turned_off_stays_off_when_the_page_is_shown_again(self) -> None:
        self.with_scores()

        self.assertEqual(self.switches(use_scores=False)["use_scores"], False)

    def test_a_game_the_run_does_not_act_on_comes_with_nothing_to_count(self) -> None:
        self.with_scores()

        found = self.things(ticking=[])

        self.assertEqual(found["step"], "summary")


class NamesArtAndThingsTests(WizardCase):
    """The pages that ask how a matched game is named and described, what the source's
    wheel images are, and which of the things beside the games to bring."""

    CATALOG = [{"id": "vps-taxi", "name": "Taxi", "manufacturer": "Midway", "year": 1999,
                "type": "EM"}]
    PICKS = {"Taxi": "vps-taxi"}

    def walk(self, source=FIXTURE, matched: bool = True, **values) -> list[dict]:
        """Every page reached, in order, answering each with the value it offered."""
        pages = []
        seen: dict = {"picks": dict(self.PICKS)} if matched else {}
        with unittest.mock.patch("common.games.game_service.load_vpsdb",
                                 return_value=self.CATALOG), \
                unittest.mock.patch("common.online.vpsdb_sync.ensure_catalog",
                                    return_value=True):
            found = self._step(source)
            while "summary" not in found and found.get("step"):
                pages.append(found)
                for field in found.get("fields") or []:
                    seen.setdefault(field["key"], field.get("value"))
                seen.pop("path", None)
                found = self._step(source, leaving=found["step"], **{**seen, **values})
            pages.append(found)
        return pages

    def page(self, step: str, **kwargs) -> Any:
        return next((one for one in self.walk(**kwargs) if one.get("step") == step), None)

    def rows(self, **kwargs) -> dict[str, str]:
        return {row[0]: row[1] for row in self.walk(**kwargs)[-1]["summary"] if len(row) == 2}

    def with_wheels(self, data: bytes, name: str = "frontend") -> Path:
        """A copy of the fixture whose wheel images are the given bytes."""
        source = self.root / name
        shutil.copytree(FIXTURE, source)
        for one in (source / "Media" / "Visual Pinball X" / "Wheel Images").iterdir():
            one.write_bytes(data)
        return source

    def test_the_names_page_shows_what_a_folder_would_be_called(self) -> None:
        page = self.page("names")

        names = page["fields"][0]
        self.assertEqual((names["value"], [one[0] for one in names["choices"]]),
                         ("vps", ["vps", "source"]))
        self.assertEqual(names["choices"][0][1], "From VPS")
        self.assertEqual(names["choices"][1][1], "As in PinballX / PinballY")
        self.assertIn("Taxi (Williams 1988) becomes Taxi (Midway 1999)", names["help"])

    def test_the_details_page_counts_where_the_two_disagree(self) -> None:
        page = self.page("names")

        details = page["fields"][1]
        self.assertEqual([one[0] for one in details["choices"]], ["vps", "source", "some"])
        self.assertEqual(details["help"], "Where they differ: Maker 1, Year 1")
        self.assertEqual(page["fields"][2]["value"], ["manufacturer", "year"])

    def test_nothing_is_asked_where_the_entry_changes_nothing(self) -> None:
        self.assertIsNone(self.page("names", matched=False))

    def test_keeping_the_sources_names_is_said_on_the_summary(self) -> None:
        rows = self.rows(names="source", details="source")

        self.assertEqual(rows["Folder Names"], "As in PinballX / PinballY")
        self.assertEqual(rows["Details"], "Keep PinballX / PinballY's")

    def test_choosing_which_details_to_keep_names_them(self) -> None:
        rows = self.rows(details="some", details_some=["year"])

        self.assertEqual(rows["Details"], "Keep yours for Year")

    def test_the_art_page_offers_a_few_of_the_wheel_images_to_judge_by(self) -> None:
        page = self.page("media")

        wheels_as = page["fields"][0]
        self.assertEqual([one[0] for one in wheels_as["choices"]], ["logo", "wheel"])
        self.assertEqual([one[1] for one in wheels_as["choices"]], ["Logos", "Wheels"])
        self.assertTrue(all(one[3] for one in wheels_as["choices"]))
        self.assertIn("Wheel 1", page["help"])
        self.assertNotIn("show_logos", [one["key"] for one in page["fields"]])

    def test_wheel_images_that_are_not_pictures_it_can_read_are_wheels(self) -> None:
        self.assertEqual(self.page("media")["fields"][0]["value"], "wheel")

    def test_wide_transparent_wheel_images_are_offered_as_logos(self) -> None:
        from tests.importer.test_shapes import png

        page = self.page("media", source=self.with_wheels(png(800, 200)))

        self.assertEqual(page["fields"][0]["value"], "logo")

    def test_the_option_the_images_look_like_says_so(self) -> None:
        from tests.importer.test_shapes import png

        wheels = self.page("media", source=self.with_wheels(png(300, 300)))[
            "fields"][0]["choices"]
        logos = self.page("media", source=self.with_wheels(png(800, 200), "logos"))[
            "fields"][0]["choices"]

        self.assertNotIn("Yours look like this", wheels[0][2])
        self.assertIn("Yours look like this", wheels[1][2])
        self.assertIn("Yours look like this", logos[0][2])
        self.assertNotIn("Yours look like this", logos[1][2])

    def test_nothing_is_claimed_of_images_that_could_not_be_measured(self) -> None:
        choices = self.page("media")["fields"][0]["choices"]

        for choice in choices:
            self.assertNotIn("Yours look like this", choice[2])

    def test_the_summary_says_what_the_wheel_images_are_kept_as(self) -> None:
        wheels = self.rows(wheels_as="wheel")
        logos = self.rows(wheels_as="logo")

        self.assertEqual(wheels["Wheel Images"], "Wheels")
        self.assertEqual(logos["Wheel Images"], "Logos, shown in place of wheels")
        self.assertEqual(logos["Download Missing Media"], "Yes")

    def test_missing_media_is_not_fetched_when_the_switch_is_off(self) -> None:
        self.assertEqual(self.rows(fetch_media=False)["Download Missing Media"], "No")

    def test_an_answer_kept_from_an_earlier_import_starts_the_page(self) -> None:
        self.store.set_setting("library_importer", "answers",
                               '{"fetch_media": false, "names": "source", "other": 1}')

        pages = self.walk()
        art = next(one for one in pages if one["step"] == "media")
        names = next(one for one in pages if one["step"] == "names")

        self.assertIs(next(one for one in art["fields"] if one["key"] == "fetch_media")["value"],
                      False)
        self.assertEqual(names["fields"][0]["value"], "source")

    def test_what_is_kept_is_one_text_that_can_be_unreadable(self) -> None:
        self.store.set_setting("library_importer", "answers", "not json")

        self.assertIs(self.page("media")["fields"][1]["value"], True)

    def test_a_thing_the_source_has_none_of_gets_no_switch(self) -> None:
        self.assertIsNone(self.page("things"))


if __name__ == "__main__":
    unittest.main()
