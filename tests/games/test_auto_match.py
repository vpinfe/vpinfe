"""Matching a game to its VPS entry from its folder name, and leaving a person's alone."""

from __future__ import annotations

import configparser
import json
import tempfile
import unittest
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock, patch

from common.games import (
    auto_match,
    game_identity,
    game_service,
    library_refresh,
    metadata_service,
)
from common.games.game_metadata import (
    MATCHED_BY_USER,
    MATCHED_ON_IMPORT,
    VPS_MATCHED_BY_KEY,
    vps_matched_by,
)
from common.i18n import t
from common.online.vpsdb import VPSdb
from common.runtime import job_details, jobs
from common.uploads import upload_ops
from httpapi import models
from tests.support.library import TempTree, fake_game, game_info, write_game

FOLDER = "Fathom (Bally 1981)"


def _catalog(*entries: dict) -> VPSdb:
    vps = VPSdb.__new__(VPSdb)
    vps.data = list(entries)
    vps.download_media_for_game = MagicMock()  # type: ignore[method-assign]
    return vps


def _entry(vps_id: str, name: str, manufacturer: str = "Bally", year: int = 1981) -> dict:
    return {"id": vps_id, "name": name, "manufacturer": manufacturer, "year": year}


class LookupTests(unittest.TestCase):
    def test_the_closest_name_wins_over_the_first_that_passes(self) -> None:
        vps = _catalog(_entry("plural", "Fathoms"), _entry("exact", "Fathom"))

        found = vps.lookup_name("Fathom", "Bally", 1981)
        assert found is not None
        self.assertEqual(found["id"], "exact")

    def test_a_tie_keeps_the_first_in_the_catalog(self) -> None:
        vps = _catalog(_entry("first", "Fathom"), _entry("second", "Fathom"))

        found = vps.lookup_name("Fathom", "Bally", 1981)
        assert found is not None
        self.assertEqual(found["id"], "first")

    def test_a_different_year_is_no_match(self) -> None:
        vps = _catalog(_entry("other", "Fathom", year=1986))

        self.assertIsNone(vps.lookup_name("Fathom", "Bally", 1981))


def _numbered(vps_id: str, name: str, ipdb: str, manufacturer: str = "Gottlieb",
              year: int = 1978) -> dict:
    return {**_entry(vps_id, name, manufacturer, year),
            "ipdbUrl": f"https://www.ipdb.org/machine.cgi?id={ipdb}"}


class SettleTests(unittest.TestCase):
    """Which catalog entry a source's game is, and why not where that cannot be told."""

    def settle(self, catalog: list[dict], **row) -> dict:
        return auto_match.settle(catalog, [row])[0]

    def test_the_sources_own_vps_id_wins_over_everything(self) -> None:
        catalog = [_numbered("own", "Dragon", "700"), _numbered("named", "Other", "701")]

        found = self.settle(catalog, vps_id="own", ipdb_id="701", title="Other",
                            manufacturer="Gottlieb", year=1978)

        self.assertEqual((found["vps_id"], found["how"]), ("own", "vps_id"))

    def test_the_entry_carries_the_folder_name_vpinfe_would_give_the_game(self) -> None:
        catalog = [{"id": "x", "name": "Dragon", "manufacturer": "Gottlieb", "year": 1978}]

        found = self.settle(catalog, vps_id="x")

        self.assertEqual(found["entry"]["folder_name"], "Dragon (Gottlieb 1978)")

    def test_an_id_the_catalog_does_not_hold_is_set_aside(self) -> None:
        catalog = [_numbered("real", "Dragon", "700")]

        found = self.settle(catalog, vps_id="gone", ipdb_id="700")

        self.assertEqual((found["vps_id"], found["how"]), ("real", "ipdb"))

    def test_one_ipdb_number_names_the_entry_whose_name_agrees(self) -> None:
        catalog = [_numbered("a", "Dragon", "700"), _numbered("b", "Dragon", "701")]

        found = self.settle(catalog, ipdb_id="701", title="Dragon",
                            manufacturer="Gottlieb", year=1978)

        self.assertEqual((found["vps_id"], found["how"]), ("b", "ipdb"))

    def test_a_number_alone_is_enough_when_nothing_else_points_elsewhere(self) -> None:
        catalog = [_numbered("a", "Dragon", "700")]

        found = self.settle(catalog, ipdb_id="700")

        self.assertEqual((found["vps_id"], found["how"]), ("a", "ipdb"))

    def test_entries_sharing_a_number_are_told_apart_by_name(self) -> None:
        catalog = [_numbered("plain", "Dragon", "700"),
                   _numbered("remake", "Dragon Remake", "700")]

        found = self.settle(catalog, ipdb_id="700", title="Dragon Remake",
                            manufacturer="Gottlieb", year=1978)

        self.assertEqual(found["vps_id"], "remake")

    def test_entries_sharing_a_number_that_the_name_cannot_tell_apart_are_left(self) -> None:
        catalog = [_numbered("plain", "Dragon", "700"),
                   _numbered("remake", "Dragon Remake", "700")]

        found = self.settle(catalog, ipdb_id="700", title="Something Else",
                            manufacturer="Gottlieb", year=1978)

        self.assertEqual(found["vps_id"], "")
        self.assertEqual(found["reason"], auto_match.TWO_ENTRIES)
        self.assertEqual(len(found["candidates"]), 2)

    def test_a_number_and_a_name_that_disagree_are_left_for_a_person(self) -> None:
        catalog = [_numbered("by_number", "Dragon", "700"),
                   _numbered("by_name", "Eclipse", "701")]

        found = self.settle(catalog, ipdb_id="700", title="Eclipse",
                            manufacturer="Gottlieb", year=1978)

        self.assertEqual(found["vps_id"], "")
        self.assertEqual(found["reason"], auto_match.IPDB_DIFFERS)
        self.assertEqual({one["id"] for one in found["candidates"]},
                         {"by_number", "by_name"})

    def test_with_no_number_the_name_maker_and_year_decide(self) -> None:
        catalog = [_entry("fathom", "Fathom")]

        found = self.settle(catalog, title="Fathom", manufacturer="Bally", year=1981)

        self.assertEqual((found["vps_id"], found["how"]), ("fathom", "name"))

    def test_a_folder_name_stands_in_for_a_name_the_source_lacks(self) -> None:
        catalog = [_entry("fathom", "Fathom")]

        found = self.settle(catalog, folder="Fathom (Bally 1981)")

        self.assertEqual(found["vps_id"], "fathom")

    def test_nothing_in_the_catalog_says_so(self) -> None:
        found = self.settle([_entry("fathom", "Fathom")], title="Nowhere",
                            manufacturer="Bally", year=1981)

        self.assertEqual((found["vps_id"], found["reason"]), ("", auto_match.NOT_FOUND))

    def test_rows_are_answered_in_the_order_they_came(self) -> None:
        catalog = [_entry("fathom", "Fathom")]

        found = auto_match.settle(catalog, [
            {"title": "Nowhere", "manufacturer": "Bally", "year": 1981},
            {"title": "Fathom", "manufacturer": "Bally", "year": 1981}])

        self.assertEqual([one["vps_id"] for one in found], ["", "fathom"])


class MatchedByTests(unittest.TestCase):
    def test_an_import_pick_reads_as_one(self) -> None:
        meta = game_info(vpinfe={VPS_MATCHED_BY_KEY: MATCHED_ON_IMPORT})

        self.assertEqual(vps_matched_by(meta), MATCHED_ON_IMPORT)

    def test_no_record_is_a_guess(self) -> None:
        self.assertEqual(vps_matched_by(game_info()), "")

    def test_an_override_is_a_persons_whatever_the_record_says(self) -> None:
        meta = game_info(vpinfe={"alt_vpsid": "chosen"})

        self.assertEqual(vps_matched_by(meta), MATCHED_BY_USER)

    def test_a_declared_no_match_is_a_persons(self) -> None:
        meta = game_info(vps_id="", vpinfe={"alt_vpsid": None})

        self.assertEqual(vps_matched_by(meta), MATCHED_BY_USER)

    def test_a_record_with_no_entry_to_describe_says_nothing(self) -> None:
        meta = game_info(vps_id="", vpinfe={VPS_MATCHED_BY_KEY: MATCHED_BY_USER})

        self.assertEqual(vps_matched_by(meta), "")


class RebuildTests(TempTree):
    """`--update-all` over one game whose folder name guesses `fathom`."""

    def setUp(self) -> None:
        super().setUp()
        self.vps = _catalog(_entry("fathom", "Fathom"),
                            _entry("chosen", "Fathom Deluxe", year=1983))

    def rebuild(self, info: dict | None) -> dict:
        folder = write_game(self.root, FOLDER, info=info)
        config = configparser.ConfigParser()
        config["Settings"] = {"gamerootdir": str(self.root)}
        config["updates"] = {"update_downloaded_media": "false"}
        parser = MagicMock()
        parser.single_file_extract.return_value = {"filename": f"{FOLDER}.vpx"}
        with patch("common.games.game_repository.all_games",
                   return_value=[fake_game(folder, FOLDER)]), \
                patch.object(metadata_service, "VPSdb", return_value=self.vps), \
                patch.object(metadata_service, "VPXParser", return_value=parser):
            metadata_service.build_metadata(
                update_all=True, iniconfig=config)  # type: ignore[arg-type]
        return json.loads((folder / f"{FOLDER}.info").read_text(encoding="utf-8"))

    def art_fetched_for(self) -> list[str]:
        download = cast(MagicMock, self.vps.download_media_for_game)
        return [call.args[1] for call in download.call_args_list]

    def test_an_import_pick_survives_a_rebuild(self) -> None:
        saved = self.rebuild(game_info("Fathom Deluxe", vps_id="chosen",
                                       vpinfe={VPS_MATCHED_BY_KEY: MATCHED_ON_IMPORT}))

        self.assertEqual(saved["Info"]["VPSId"], "chosen")
        self.assertEqual(saved["vpinfe"][VPS_MATCHED_BY_KEY], MATCHED_ON_IMPORT)
        self.assertEqual(self.art_fetched_for(), ["chosen"])

    def test_an_override_keeps_its_details_and_gets_its_own_art(self) -> None:
        saved = self.rebuild(game_info("Fathom Deluxe", vps_id="fathom",
                                       vpinfe={"alt_vpsid": "chosen"}))

        self.assertEqual(saved["vpinfe"]["alt_vpsid"], "chosen")
        self.assertEqual(saved["Info"], {"Title": "Fathom Deluxe", "VPSId": "fathom"})
        self.assertEqual(self.art_fetched_for(), ["chosen"])

    def test_a_declared_no_match_stays_and_fetches_nothing(self) -> None:
        saved = self.rebuild(game_info("Homebrew", vps_id="", vpinfe={"alt_vpsid": None}))

        self.assertIsNone(saved["vpinfe"]["alt_vpsid"])
        self.assertEqual(saved["Info"]["VPSId"], "")
        self.assertEqual(self.art_fetched_for(), [])

    def test_a_guess_is_guessed_again(self) -> None:
        saved = self.rebuild(game_info("Fathom Deluxe", vps_id="chosen"))

        self.assertEqual(saved["Info"]["VPSId"], "fathom")
        self.assertNotIn(VPS_MATCHED_BY_KEY, saved["vpinfe"])
        self.assertEqual(self.art_fetched_for(), ["fathom"])

    def test_a_game_with_no_record_is_guessed(self) -> None:
        saved = self.rebuild(None)

        self.assertEqual(saved["Info"]["VPSId"], "fathom")
        self.assertEqual(self.art_fetched_for(), ["fathom"])


class AssociateTests(TempTree):
    def associate(self, info: dict, **kwargs) -> dict:
        folder = write_game(self.root, FOLDER, info=info)
        parser = MagicMock()
        parser.single_file_extract.return_value = {"filename": f"{FOLDER}.vpx"}
        with patch.object(game_service, "VPXParser", return_value=parser), \
                patch.object(game_service, "refresh_game"):
            game_service.associate_vps_to_folder(
                folder, _entry("chosen", "Fathom Deluxe", year=1983), **kwargs)
        return json.loads((folder / f"{FOLDER}.info").read_text(encoding="utf-8"))

    def test_a_pick_is_recorded_and_puts_an_override_aside(self) -> None:
        saved = self.associate(game_info(vps_id="fathom", vpinfe={"alt_vpsid": "other"}))

        self.assertEqual(saved["Info"]["VPSId"], "chosen")
        self.assertEqual(saved["vpinfe"]["alt_vpsid"], "")
        self.assertEqual(saved["vpinfe"][VPS_MATCHED_BY_KEY], MATCHED_BY_USER)

    def test_an_import_says_so(self) -> None:
        saved = self.associate(game_info(vps_id=""), matched_by=MATCHED_ON_IMPORT)

        self.assertEqual(saved["vpinfe"][VPS_MATCHED_BY_KEY], MATCHED_ON_IMPORT)

    def test_a_pick_replaces_a_declared_no_match(self) -> None:
        saved = self.associate(game_info(vps_id="", vpinfe={"alt_vpsid": None}))

        self.assertEqual(vps_matched_by(saved), MATCHED_BY_USER)
        self.assertEqual(saved["vpinfe"]["alt_vpsid"], "")

    def test_a_folder_with_no_table_says_so_in_words(self) -> None:
        folder = write_game(self.root, FOLDER, info=game_info(vps_id=""), vpx=False)

        with self.assertRaises(FileNotFoundError) as said:
            game_service.associate_vps_to_folder(folder, _entry("chosen", "Fathom"))

        self.assertEqual(str(said.exception), t("error.games.folder_empty"))


def _offline(case: TempTree, catalog: list[dict]) -> None:
    """The catalog on disk, and every way to the network refused."""
    held = case.root / "vpsdb.json"
    held.write_text(json.dumps(catalog), encoding="utf-8")
    for patcher in (patch.object(game_service, "VPSDB_JSON_PATH", held),
                    patch.object(game_service, "_vpsdb_cache", None),
                    patch.object(VPSdb, "__init__", side_effect=AssertionError("VPSdb")),
                    patch("socket.socket.connect", side_effect=OSError("offline"))):
        patcher.start()
        case.addCleanup(patcher.stop)


def _read(folder: Path) -> dict:
    return json.loads((folder / f"{folder.name}.info").read_text(encoding="utf-8"))


NEW = "Fathom (Bally 1981)"
UNKNOWN = "Nothing Like It (Nobody 1901)"
SEEN = "Fathom Deluxe (Bally 1983)"
DECLARED = "Fathom (Bally 1981) (homebrew)"
SEEN_DECLARED = "Fathom Deluxe (Bally 1983) (homebrew)"


class FirstSightTests(TempTree):
    """Look for new tables over three new folders and two already seen."""

    def setUp(self) -> None:
        super().setUp()
        _offline(self, [_entry("fathom", "Fathom"),
                        _entry("chosen", "Fathom Deluxe", year=1983)])
        self.tables = {"tbl0000001": {"id": "tbl0000001", "filename": f"{NEW}.vpx"}}
        self.folders = {
            NEW: write_game(self.root, NEW, info=game_info(
                vps_id="", tables=self.tables, vpinfe={"alt_title": "My Fathom"})),
            UNKNOWN: write_game(self.root, UNKNOWN),
            SEEN: write_game(self.root, SEEN, info=game_info(vps_id="", game_id="seen1")),
            DECLARED: write_game(self.root, DECLARED, info=game_info(
                vps_id="", vpinfe={"alt_vpsid": None})),
            SEEN_DECLARED: write_game(self.root, SEEN_DECLARED, info=game_info(
                vps_id="", game_id="seen2", vpinfe={"alt_vpsid": None})),
        }
        for patcher in (patch.object(library_refresh, "discover", return_value={"found": 0}),
                        patch.object(library_refresh, "enrich", return_value={"read": 0})):
            patcher.start()
            self.addCleanup(patcher.stop)
        art = patch("common.games.media_fill.request")
        self.art = art.start()
        self.addCleanup(art.stop)

    def games(self) -> list:
        return [fake_game(folder, name, meta=_read(folder)
                          if (folder / f"{name}.info").exists() else {})
                for name, folder in self.folders.items()]

    def refresh(self) -> dict:
        with patch("common.games.game_repository.all_games", return_value=self.games()):
            return library_refresh.refresh()

    def test_a_new_game_is_matched_from_its_folder_name(self) -> None:
        self.refresh()

        saved = _read(self.folders[NEW])
        self.assertEqual(saved["Info"]["VPSId"], "fathom")
        self.assertEqual(vps_matched_by(saved), "")

    def test_only_the_match_is_written(self) -> None:
        self.refresh()

        saved = _read(self.folders[NEW])
        self.assertEqual({key: entry["filename"] for key, entry in saved["tables"].items()},
                         {"tbl0000001": f"{NEW}.vpx"})
        self.assertEqual(saved["vpinfe"]["alt_title"], "My Fathom")
        self.assertTrue(saved["vpinfe"]["game_id"])

    def test_a_game_seen_before_without_a_match_is_guessed_again(self) -> None:
        self.refresh()

        saved = _read(self.folders[SEEN])
        self.assertEqual(saved["Info"]["VPSId"], "chosen")
        self.assertEqual(vps_matched_by(saved), "")

    def test_a_game_seen_before_and_declared_no_match_is_left_alone(self) -> None:
        self.refresh()

        saved = _read(self.folders[SEEN_DECLARED])
        self.assertIsNone(saved["vpinfe"]["alt_vpsid"])
        self.assertEqual(saved["Info"]["VPSId"], "")

    def test_the_result_counts_the_games_guessed_again_apart(self) -> None:
        result = self.refresh()

        self.assertEqual((result["waiting_games"], result["waiting_matched"],
                          result["waiting_unmatched"]), (1, 1, 0))

    def test_startup_guesses_again_too(self) -> None:
        games = self.games()
        unseen = [game for game in games if not game_identity.game_id(game)]
        game_identity.ensure_unique_ids(games)

        result = library_refresh.read_at_startup(games, unseen)

        self.assertEqual(_read(self.folders[SEEN])["Info"]["VPSId"], "chosen")
        self.assertEqual(result["waiting_matched"], 1)

    def test_a_declared_no_match_is_left_alone(self) -> None:
        self.refresh()

        saved = _read(self.folders[DECLARED])
        self.assertIsNone(saved["vpinfe"]["alt_vpsid"])
        self.assertEqual(saved["Info"]["VPSId"], "")

    def test_the_result_counts_the_new_games(self) -> None:
        result = self.refresh()

        self.assertEqual((result["new_games"], result["new_matched"],
                          result["new_unmatched"]), (3, 1, 1))

    def test_the_result_names_the_new_games_waiting_for_a_match(self) -> None:
        result = self.refresh()

        self.assertEqual(result["new_unmatched_ids"],
                         [_read(self.folders[UNKNOWN])["vpinfe"]["game_id"]])

    def test_startup_names_them_too(self) -> None:
        """Startup gives the ids out before its read, the way `main` does."""
        games = self.games()
        unseen = [game for game in games if not game_identity.game_id(game)]
        game_identity.ensure_unique_ids(games)

        result = library_refresh.read_at_startup(games, unseen)

        self.assertEqual(result["new_unmatched_ids"],
                         [_read(self.folders[UNKNOWN])["vpinfe"]["game_id"]])

    def test_startup_says_how_many_games_it_read(self) -> None:
        games = self.games()
        unseen = [game for game in games if not game_identity.game_id(game)]
        game_identity.ensure_unique_ids(games)

        result = library_refresh.read_at_startup(games, unseen)

        self.assertEqual(result["games"], len(games))
        self.assertIn(str(len(games)), job_details.of(jobs.KIND_LIBRARY_SCAN, result))

    def test_switched_off_nothing_is_matched(self) -> None:
        config = configparser.ConfigParser()
        config["updates"] = {"match_new_games": "false"}
        with patch.object(auto_match, "get_ini_config", return_value=config):
            result = self.refresh()

        self.assertEqual(_read(self.folders[NEW])["Info"]["VPSId"], "")
        self.assertEqual(_read(self.folders[SEEN])["Info"]["VPSId"], "")
        self.assertEqual((result["new_matched"], result["new_unmatched"]), (0, 2))
        self.assertEqual(result["waiting_matched"], 0)

    def test_every_new_game_and_the_waiting_ones_now_matched_are_handed_to_the_media_fill(
            self) -> None:
        self.refresh()

        (folders,), _ = self.art.call_args
        self.assertEqual([Path(str(folder)).name for folder in folders],
                         [NEW, UNKNOWN, DECLARED, SEEN])

    def test_a_waiting_game_still_unmatched_is_not_handed_again(self) -> None:
        config = configparser.ConfigParser()
        config["updates"] = {"match_new_games": "false"}
        with patch.object(auto_match, "get_ini_config", return_value=config):
            self.refresh()

        (folders,), _ = self.art.call_args
        self.assertEqual([Path(str(folder)).name for folder in folders],
                         [NEW, UNKNOWN, DECLARED])


STALE = "Fathom (Bally 1981)"
PICKED = "Fathom (Bally 1981) (mine)"
RIGHT = "Fathom Deluxe (Bally 1983)"


class MatchAgainTests(TempTree):
    """Auto-match over a stale guess, a right one, a person's pick, a declared no-match
    and a folder the catalog has nothing for."""

    def setUp(self) -> None:
        super().setUp()
        _offline(self, [_entry("fathom", "Fathom"),
                        _entry("chosen", "Fathom Deluxe", year=1983)])
        self.folders = {
            STALE: write_game(self.root, STALE, info=game_info(vps_id="wrong")),
            RIGHT: write_game(self.root, RIGHT, info=game_info(vps_id="chosen")),
            PICKED: write_game(self.root, PICKED, info=game_info(
                vps_id="chosen", vpinfe={VPS_MATCHED_BY_KEY: MATCHED_BY_USER})),
            DECLARED: write_game(self.root, DECLARED, info=game_info(
                vps_id="", vpinfe={"alt_vpsid": None})),
            UNKNOWN: write_game(self.root, UNKNOWN, info=game_info(vps_id="")),
        }

    def match_again(self) -> tuple[dict, list]:
        games = [fake_game(folder, name, meta=_read(folder))
                 for name, folder in self.folders.items()]
        return auto_match.match_again(games)

    def test_a_stale_guess_is_guessed_again(self) -> None:
        self.match_again()

        saved = _read(self.folders[STALE])
        self.assertEqual(saved["Info"]["VPSId"], "fathom")
        self.assertEqual(vps_matched_by(saved), "")

    def test_a_person_s_match_and_a_declared_no_match_stay(self) -> None:
        self.match_again()

        self.assertEqual(_read(self.folders[PICKED])["Info"]["VPSId"], "chosen")
        self.assertIsNone(_read(self.folders[DECLARED])["vpinfe"]["alt_vpsid"])

    def test_the_counts_say_what_moved_and_what_waits(self) -> None:
        counts, moved = self.match_again()

        self.assertEqual(counts, {"games": 5, "changed": 1, "unmatched": 1, "yours": 2})
        self.assertEqual([game.game_dir_name for game in moved], [STALE])

    def test_nothing_found_leaves_the_match_it_had(self) -> None:
        _offline(self, [])

        counts, _ = self.match_again()

        self.assertEqual(_read(self.folders[STALE])["Info"]["VPSId"], "wrong")
        self.assertEqual(counts["changed"], 0)


class ImportWithoutPickTests(TempTree):
    def imported(self, name: str) -> tuple[dict, Path]:
        _offline(self, [_entry("fathom", "Fathom")])
        folder = write_game(self.root, name)
        plan = MagicMock(new_game_dir_name=name)
        with patch.object(upload_ops, "_analysis_for", return_value=(None, self.root)), \
                patch.object(upload_ops, "_built_plan", return_value=plan), \
                patch.object(upload_ops, "select_plan_items", return_value=plan), \
                patch.object(upload_ops, "_run", return_value={
                    "imported": [], "skipped": [], "media_kinds": [], "blocked": [],
                    "new_game": True, "game_dir": str(folder)}), \
                patch("common.games.game_repository.refresh_game"), \
                patch("common.games.media_fill.request") as self.art:
            report = upload_ops.execute("upload1", {})
        return report, folder

    def test_a_matched_import_is_handed_to_the_media_fill(self) -> None:
        _, folder = self.imported(NEW)

        self.art.assert_called_once_with([folder])

    def test_an_unmatched_one_is_too_for_its_backglass_file_needs_no_match(self) -> None:
        _, folder = self.imported(UNKNOWN)

        self.art.assert_called_once_with([folder])

    def test_the_new_game_is_matched_from_its_folder_name(self) -> None:
        report, folder = self.imported(NEW)

        self.assertIs(models.ImportReport(**report).vps_matched, True)
        self.assertEqual(_read(folder)["Info"]["VPSId"], "fathom")

    def test_the_report_says_when_it_needs_a_match(self) -> None:
        report, folder = self.imported(UNKNOWN)

        self.assertIs(models.ImportReport(**report).vps_matched, False)
        self.assertFalse((folder / f"{UNKNOWN}.info").exists())


class ImportWithPickTests(TempTree):
    def test_its_art_comes_from_the_fill_rather_than_inline(self) -> None:
        report = {"game_dir": str(self.root / NEW)}
        with patch.object(game_service, "associate_vps_to_folder") as associate, \
                patch("common.games.media_fill.request") as art:
            upload_ops._associate(report, _entry("fathom", "Fathom"))

        self.assertFalse(associate.call_args.args[2])
        art.assert_called_once_with([report["game_dir"]])

    def test_a_match_that_failed_for_a_worded_reason_says_it(self) -> None:
        report = {"game_dir": str(self.root / NEW)}
        with patch.object(game_service, "associate_vps_to_folder",
                          side_effect=FileNotFoundError(t("error.games.folder_empty"))):
            upload_ops._associate(report, _entry("fathom", "Fathom"))

        self.assertEqual(report["vps_error"], t("error.games.folder_empty"))

    def test_one_that_failed_for_any_other_reason_points_at_the_log(self) -> None:
        report = {"game_dir": str(self.root / NEW)}
        with patch.object(game_service, "associate_vps_to_folder",
                          side_effect=KeyError("tableFiles")), \
                self.assertLogs("vpinfe.common.uploads.upload_ops", "ERROR") as logged:
            upload_ops._associate(report, _entry("fathom", "Fathom"))

        self.assertIs(report["vps_associated"], False)
        self.assertEqual(report["vps_error"], t("error.uploads.see_log"))
        self.assertIn("tableFiles", "\n".join(logged.output))


class VpsdbNotThereYetTests(unittest.TestCase):
    """The catalog is downloaded in the background, so a first start asks before it is there."""

    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        for patcher in (patch.object(game_service, "VPSDB_JSON_PATH", self.root / "vpsdb.json"),
                        patch.object(game_service, "_vpsdb_cache", None)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_a_catalog_not_downloaded_yet_is_an_info_line_and_an_empty_list(self) -> None:
        with self.assertLogs(game_service.logger, "INFO") as logged:
            self.assertEqual(game_service.load_vpsdb(), [])

        self.assertEqual([record.levelname for record in logged.records], ["INFO"])

    def test_a_catalog_that_will_not_parse_is_still_an_error(self) -> None:
        (self.root / "vpsdb.json").write_text("{not json", encoding="utf-8")

        with self.assertLogs(game_service.logger, "ERROR"):
            self.assertEqual(game_service.load_vpsdb(), [])


if __name__ == "__main__":
    unittest.main()
