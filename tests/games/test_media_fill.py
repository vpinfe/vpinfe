"""Getting a newly matched game the art it is missing, and nothing more."""

from __future__ import annotations

import configparser
import hashlib
import time
import unittest
from pathlib import Path
from typing import Any, cast
from unittest.mock import MagicMock, patch

from common import i18n, service_errors, timestamps
from common.config.config_store import ConfigStore
from common.devices import device_registry
from common.games import (
    asset_origin,
    backglass_ops,
    library_policy,
    media_fill,
    media_placement,
    metadata_service,
)
from common.games.formats import directb2s_parser
from common.games.game import Game
from common.i18n import t
from common.media import sized_media
from common.online import picture_changes, sources, vpinmediadb, vps_marks, vpsdb_sync
from common.online.vpsdb import VPSdb
from common.online.vpsdb_media import VPSMediaDownloader
from common.runtime import job_details, jobs
from tests.api.test_backglass_picture import _directb2s
from tests.support.library import TempTree, fake_game, game_info, write_game
from tests.support.skips import needs_posix_permissions

FOLDER = "Fathom (Bally 1981)"
OTHER = "Eight Ball (Bally 1977)"


def _url(name: str) -> str:
    return f"https://media.invalid/{name}"


# Largest first, the way the catalog lists them, so taking the first offer is visibly
# different from taking the configured size.
MANIFEST = {
    "fathom": {
        "wheel": _url("wheel.png"), "wheel_md5": "wheel-md5",
        "4k": {"table": _url("table-4k.png"), "fss": _url("fss-4k.png"),
               "table_video": _url("table-4k.mp4")},
        "1k": {"bg": _url("bg-1k.png"), "table": _url("table-1k.png"),
               "fss": _url("fss-1k.png"), "table_video": _url("table-1k.mp4")},
    },
    "eightball": {"wheel": _url("eightball-wheel.png")},
}


def _downloaded(url: str, path: Path) -> None:
    Path(path).write_bytes(url.encode())


class _Library(TempTree):
    launched_as = ""
    config: Any

    def setUp(self) -> None:
        super().setUp()
        self.games: dict[str, Game] = {}
        self.sets: dict[str, str] = {}
        self.folder = self.game(FOLDER, "fathom")
        self.downloads = MagicMock(side_effect=_downloaded)
        self.config = configparser.ConfigParser()
        policy = library_policy.reset_for_tests(ConfigStore(str(self.root / "vpinfe.ini")))
        self.addCleanup(library_policy.reset_for_tests)
        self.policy = policy
        for patcher in (
                patch("common.games.game_repository.catalog", return_value=self.games),
                patch("common.games.game_repository.refresh_game"),
                patch("common.security.http_client.download_file", self.downloads),
                patch.object(vpinmediadb, "_MANIFEST", MANIFEST),
                patch.object(media_fill, "get_ini_config", return_value=self.config),
                patch.object(picture_changes, "get_ini_config", return_value=self.config),
                patch.object(picture_changes, "ANSWER_PATH", self.root / "picture-changes.json"),
                patch.object(vps_marks, "PATH", self.root / "marks.json"),
                patch("common.games.media_service.media_settings",
                      side_effect=lambda: (self.variant(), self.sets)),
                patch("socket.socket.connect", side_effect=OSError("offline"))):
            patcher.start()
            self.addCleanup(patcher.stop)

    def game(self, name: str, vps_id: str, **medias: bytes) -> Path:
        game_id = f"id{len(self.games):09d}"
        launched = f"{self.launched_as or name}.vpx"
        info = game_info(name, vps_id=vps_id, game_id=game_id,
                         tables={"tbl0000001": {"id": "tbl0000001", "filename": launched}})
        folder = write_game(self.root, name, info=info, medias=medias, vpx=False,
                            files={launched: b"not really a vpx"})
        self.games[game_id] = fake_game(folder, name, meta=info,
                                        full_path_vpx_file=str(folder / launched))
        return folder

    def locked(self, folder: Path) -> None:
        folder.chmod(0o555)
        self.addCleanup(folder.chmod, 0o755)

    def variant(self) -> str:
        return self.config.get("media", "playfield_variant", fallback="table")

    def placed(self, folder: Path | None = None) -> dict[str, str]:
        medias = (folder or self.folder) / "medias"
        return {path.name: path.read_bytes().decode(errors="replace")
                for path in medias.iterdir() if path.is_file()} if medias.is_dir() else {}


class FillTests(_Library):
    def test_each_missing_kind_the_catalog_has_is_fetched(self) -> None:
        result = media_fill.fill([self.folder])

        placed = self.placed()
        self.assertEqual(placed[f"(Wheel) {FOLDER}.png"], _url("wheel.png"))
        self.assertEqual(placed[f"(Backglass) {FOLDER}.png"], _url("bg-1k.png"))
        self.assertEqual(result["filled"], len(placed))
        self.assertEqual(result["failed"], 0)

    def test_the_progress_says_what_the_job_is_doing(self) -> None:
        reporter = MagicMock()

        media_fill.fill([self.folder], reporter=reporter)

        said = reporter.progress.call_args.args[2]
        self.assertEqual(said, i18n.t("said.media_fetch", game=FOLDER))
        self.assertNotEqual(said, FOLDER)

    def test_where_it_came_from_is_recorded(self) -> None:
        media_fill.fill([self.folder])

        hosts = {str(entry.get("host")) for entry in
                 asset_origin.sources(self.folder).values()}
        self.assertEqual(hosts, {"vpinmediadb"})

    def test_a_slot_with_a_file_is_left_alone(self) -> None:
        self.folder = self.game(OTHER, "fathom", **{"wheel.png": b"mine"})

        media_fill.fill([self.folder])

        placed = self.placed()
        self.assertEqual(placed["wheel.png"], "mine")
        self.assertNotIn(f"(Wheel) {OTHER}.png", placed)

    def test_a_slot_an_active_set_answers_is_left_alone(self) -> None:
        self.sets = {"wheel": "Classic"}
        self.folder = self.game(OTHER, "fathom", **{"wheels/Classic/wheel.png": b"set"})

        media_fill.fill([self.folder])

        self.assertNotIn(f"(Wheel) {OTHER}.png", self.placed())
        self.assertIn(f"(Backglass) {OTHER}.png", self.placed())

    def test_a_kind_this_library_does_not_collect_is_not_fetched(self) -> None:
        self.policy.set("hidden_media_kinds", ["wheel"])

        media_fill.fill([self.folder])

        self.assertNotIn(f"(Wheel) {FOLDER}.png", self.placed())

    def test_the_size_the_display_is_set_to_is_the_one_fetched(self) -> None:
        media_fill.fill([self.folder])

        placed = self.placed()
        self.assertEqual(placed[f"(Playfield) {FOLDER}.png"], _url("table-4k.png"))
        self.assertEqual(placed[f"(Playfield) {FOLDER}.mp4"], _url("table-1k.mp4"))

    def test_under_fss_the_playfield_is_the_fss_render_with_no_video(self) -> None:
        self.config.read_dict({"media": {"playfield_variant": "fss"}})

        media_fill.fill([self.folder])

        placed = self.placed()
        self.assertEqual(placed[f"(Playfield) {FOLDER}.png"], _url("fss-4k.png"))
        self.assertNotIn(f"(Playfield) {FOLDER}.mp4", placed)

    def test_a_game_with_no_match_fetches_nothing(self) -> None:
        folder = self.game(OTHER, "")

        result = media_fill.fill([folder])

        self.assertEqual(result["unmatched"], 1)
        self.downloads.assert_not_called()

    def test_a_source_that_cannot_be_reached_is_asked_once(self) -> None:
        other = self.game(OTHER, "eightball")
        with patch.object(vpinmediadb, "_MANIFEST", None), \
                patch("common.online.vpsdb_cache.VPinMediaDatabase.load",
                      return_value=None) as load:
            result = media_fill.fill([self.folder, other])

        self.assertEqual(load.call_count, 1)
        self.assertEqual(result["filled"], 0)
        self.downloads.assert_not_called()

    def test_a_download_that_fails_leaves_the_gap_and_the_rest_go_on(self) -> None:
        def flaky(url: str, path: Path) -> None:
            if url == _url("wheel.png"):
                raise OSError("offline")
            _downloaded(url, path)

        self.downloads.side_effect = flaky

        result = media_fill.fill([self.folder])

        placed = self.placed()
        self.assertNotIn(f"(Wheel) {FOLDER}.png", placed)
        self.assertIn(f"(Backglass) {FOLDER}.png", placed)
        self.assertEqual(result["failed"], 1)

    @needs_posix_permissions
    def test_a_folder_that_cannot_be_written_is_skipped_and_said_once(self) -> None:
        other = self.game(OTHER, "eightball")
        self.locked(self.folder)

        with self.assertLogs("vpinfe.common.games.media_fill", "WARNING") as logged:
            result = media_fill.fill([self.folder, other])

        self.assertEqual(self.placed(), {})
        self.assertTrue(self.placed(other))
        self.assertEqual(result["skipped"], 1)
        self.assertEqual(result["failed"], 0)
        self.assertEqual(len(logged.records), 1)
        self.assertIn(FOLDER, logged.records[0].getMessage())

    def test_the_job_line_counts_the_folders_it_left(self) -> None:
        self.assertEqual(job_details.of(jobs.KIND_MEDIA_FILL,
                                       {"games": 3, "filled": 2, "unmatched": 1,
                                               "failed": 0, "skipped": 1}),
                         t("said.job_filled_left", count=3, filled=2, no=1,
                           failed=0, skip=1))

    def test_the_replace_line_counts_the_folders_it_left(self) -> None:
        self.assertEqual(job_details.of(jobs.KIND_MEDIA_FILL,
                                        {"games": 2, "updated": 1, "failed": 0,
                                         "skipped": 3}),
                         t("said.job_replaced_left", count=2, updated=1,
                           failed=0, skipped=3))

    def test_turned_off_it_stops_between_games(self) -> None:
        other = self.game(OTHER, "eightball")
        answers = iter([True, False])

        media_fill._fill([str(self.folder), str(other)], media_fill.kept_kinds(),
                         ("vpinmediadb",), None, lambda: next(answers))

        self.assertTrue(self.placed())
        self.assertEqual(self.placed(other), {})


class AskedTests(_Library):
    """Getting missing art because someone asked, for the games and kinds they picked."""

    def setUp(self) -> None:
        super().setUp()
        jobs.reset_for_tests()
        self.addCleanup(jobs.reset_for_tests)
        self.other = self.game(OTHER, "eightball")
        self.loose = self.game("Loose Ends", "")

    def id_of(self, folder: Path) -> str:
        return next(game_id for game_id, game in self.games.items()
                    if Path(str(getattr(game, "full_path_game", ""))) == folder)

    def ran(self, job: jobs.Job) -> dict:
        deadline = time.monotonic() + 10
        while job.state == jobs.RUNNING:
            self.assertLess(time.monotonic(), deadline, "the fill never finished")
            time.sleep(0.02)
        self.assertIsNone(job.error)
        return job.result if isinstance(job.result, dict) else {}

    @staticmethod
    def rows(found: dict) -> dict[str, tuple[int, int]]:
        return {row["kind"]: (row["missing"], row["available"]) for row in found["kinds"]}

    def test_the_plan_counts_what_is_missing_and_what_can_be_had(self) -> None:
        found = media_fill.plan()

        self.assertEqual((found["games"], found["unmatched"]), (3, 1))
        self.assertEqual(found["sources"], ["VPinMediaDB"])
        rows = self.rows(found)
        self.assertEqual(rows["wheel"], (3, 2))
        self.assertEqual(rows["backglass"], (3, 1))
        self.assertEqual(rows["flyer"], (3, 0))
        self.assertEqual(found["unreachable"], [])
        self.downloads.assert_not_called()

    def test_the_gaps_a_source_can_fill_name_each_game_and_its_entry(self) -> None:
        gaps = {kind: {one["vps_id"]: one["available"] for one in games}
                for kind, games in media_fill.offered_listing().items()}

        self.assertEqual(gaps["wheel"], {"fathom": 1, "eightball": 1})
        self.assertEqual(gaps["backglass"], {"fathom": 1})
        self.assertNotIn("flyer", gaps)
        self.downloads.assert_not_called()

    def test_a_gap_names_the_file_taking_it_would_fill_the_slot_with(self) -> None:
        listed = media_fill.offered_listing()

        wheel = next(one for one in listed["wheel"] if one["vps_id"] == "fathom")
        self.assertEqual((wheel["kind"], wheel["source"], wheel["size"], wheel["take"]),
                         ("wheel", "vpinmediadb", "", True))
        self.assertEqual(wheel["picture"], _url("wheel.png"))
        playfield = next(one for one in listed["playfield"] if one["vps_id"] == "fathom")
        self.assertEqual((playfield["size"], playfield["picture"]), ("4k", _url("table-4k.png")))

    def test_a_video_gap_offers_no_picture_to_show(self) -> None:
        video = next(one for one in media_fill.offered_listing()["playfield_video"]
                     if one["vps_id"] == "fathom")

        self.assertEqual(video["picture"], "")
        self.assertTrue(video["take"])

    def test_under_fss_the_playfield_gap_cannot_be_filled_by_taking_the_plain_one(self) -> None:
        self.config.read_dict({"media": {"playfield_variant": "fss"}})

        playfield = next(one for one in media_fill.offered_listing()["playfield"]
                         if one["vps_id"] == "fathom")

        self.assertFalse(playfield["take"])
        self.assertEqual(playfield["picture"], _url("fss-4k.png"))

    def test_each_game_is_listed_with_its_id_and_name_even_when_two_share_an_entry(self) -> None:
        twin = self.game("Fathom Twin", "fathom")

        listed = media_fill.offered_listing()["wheel"]

        ids = sorted(one["game_id"] for one in listed if one["vps_id"] == "fathom")
        self.assertEqual(ids, sorted([self.id_of(self.folder), self.id_of(twin)]))
        self.assertTrue(all(one["name"] for one in listed))

    def test_the_games_a_source_can_fill_are_named_by_id(self) -> None:
        games = media_fill.offered_games()

        self.assertEqual(len(games["wheel"]), 2)
        self.assertEqual(len(games["backglass"]), 1)
        self.assertEqual(games["wheel"], sorted(games["wheel"]))
        self.assertTrue(set(games["backglass"]) <= set(games["wheel"]))
        self.assertNotIn(self.id_of(self.loose), games["wheel"])

    def test_a_game_with_no_match_has_no_gap_a_source_can_fill(self) -> None:
        self.assertNotIn("", {one["vps_id"] for one in media_fill.offered_listing()["wheel"]})

    def test_a_slot_with_a_file_is_not_a_gap_to_fill(self) -> None:
        self.games.clear()
        self.game("Held", "fathom", **{"wheel.png": b"mine"})

        gaps = media_fill.offered_listing()

        self.assertNotIn("wheel", gaps)
        self.assertIn("backglass", gaps)

    def test_a_hidden_kind_is_not_offered(self) -> None:
        self.policy.set("hidden_media_kinds", ["wheel"])

        self.assertNotIn("wheel", media_fill.offered_listing())

    def test_the_holders_are_the_games_by_the_entry_each_is_matched_to(self) -> None:
        self.game("Unmatched", "")

        found = media_fill.holders()

        self.assertNotIn("", found)
        self.assertEqual(found["fathom"], [self.id_of(self.folder)])

    def test_no_source_reached_offers_nothing(self) -> None:
        with patch.object(vpinmediadb, "_MANIFEST", None), \
                patch("common.online.vpsdb_cache.VPinMediaDatabase.load", return_value=None):
            self.assertEqual(media_fill.offered_listing(), {})

    def test_the_read_makes_no_request_while_the_index_is_not_held(self) -> None:
        with patch.object(vpinmediadb, "_MANIFEST", None), \
                patch("common.online.vpsdb_cache.VPinMediaDatabase.load") as load, \
                patch("common.online.vpsdb_cache.get_json") as get:
            self.assertEqual(media_fill.offered_listing(), {})
            self.assertEqual(media_fill.offered_games(), {})
            self.assertIsNone(vpinmediadb._MANIFEST)

        load.assert_not_called()
        get.assert_not_called()

    def test_the_read_uses_the_index_already_held_without_a_request(self) -> None:
        with patch("common.online.vpsdb_cache.VPinMediaDatabase.load") as load:
            self.assertIn("wheel", media_fill.offered_listing())

        load.assert_not_called()

    def test_the_plan_still_loads_the_index_when_none_is_held(self) -> None:
        with patch.object(vpinmediadb, "_MANIFEST", None), \
                patch("common.online.vpsdb_cache.VPinMediaDatabase.load",
                      return_value=MANIFEST) as load:
            media_fill.plan()

        load.assert_called()

    def test_a_game_a_source_fills_is_on_the_shelf_and_every_other_blank_is_in_needs_you(
            self) -> None:
        """Whatever the last comparison says: the two halves are cut from one answer."""
        from common.games import media_lens
        from common.online import vps_home
        from manager.settings import sections

        blank = {row["game_id"] for row in media_lens.library_rows()
                 if row["kind"] == "backglass" and media_fill.is_gap(row)}
        offered = media_fill.offered_games().get("backglass", [])
        uncompared = {"counted": "", "rows": []}

        shelf = vps_home.home(uncompared, (), 7, media=media_fill.offered_listing())
        on_shelf = {one["game_id"] for one in shelf["missing"]["backglass"]["games"]}
        in_needs_you = {game_id for game_id in blank
                        if not sections._a_source_fills(
                            "no_backglass", {"backglass"} if game_id in offered else set(),
                            {"backglass"})}

        self.assertTrue(on_shelf and in_needs_you)
        self.assertEqual(on_shelf, set(offered))
        self.assertEqual(on_shelf | in_needs_you, blank)
        self.assertFalse(on_shelf & in_needs_you)

    def test_a_source_that_cannot_be_reached_is_named_and_offers_nothing(self) -> None:
        with patch.object(vpinmediadb, "_MANIFEST", None), \
                patch("common.online.vpsdb_cache.VPinMediaDatabase.load",
                      return_value=None) as load:
            found = media_fill.plan()

        self.assertEqual(found["unreachable"], ["VPinMediaDB"])
        self.assertEqual(self.rows(found)["wheel"], (3, 0))
        self.assertEqual(load.call_count, 1)

    def test_a_fill_names_the_source_it_could_not_reach(self) -> None:
        with patch.object(vpinmediadb, "_MANIFEST", None), \
                patch("common.online.vpsdb_cache.VPinMediaDatabase.load", return_value=None):
            result = self.ran(media_fill.start(kinds=["wheel"]))

        self.assertEqual((result["filled"], result["unreachable"]), (0, ["VPinMediaDB"]))
        self.downloads.assert_not_called()

    def test_a_fill_that_reached_every_source_names_none(self) -> None:
        result = self.ran(media_fill.start([self.id_of(self.folder)], ["wheel"]))

        self.assertEqual(result["unreachable"], [])

    def test_the_plan_leaves_out_a_hidden_kind_and_a_slot_with_a_file(self) -> None:
        self.policy.set("hidden_media_kinds", ["backglass"])
        held = self.game("Held", "fathom", **{"wheel.png": b"mine"})

        found = media_fill.plan([self.id_of(held), self.id_of(self.other)])

        self.assertEqual((found["games"], found["unmatched"]), (2, 0))
        rows = self.rows(found)
        self.assertNotIn("backglass", rows)
        self.assertEqual(rows["wheel"], (1, 1))

    def test_a_game_the_library_does_not_hold_is_left_out(self) -> None:
        found = media_fill.plan(["gone"])

        self.assertEqual(found["games"], 0)
        self.assertEqual({counts for counts in self.rows(found).values()}, {(0, 0)})

    def test_only_the_kinds_asked_are_fetched(self) -> None:
        result = self.ran(media_fill.start([self.id_of(self.folder)], ["wheel"]))

        self.assertEqual(self.placed(), {WHEEL: _url("wheel.png")})
        self.assertEqual(self.placed(self.other), {})
        self.assertEqual(result["filled"], 1)

    def test_no_games_named_is_the_whole_library(self) -> None:
        result = self.ran(media_fill.start(kinds=["wheel"]))

        self.assertIn(WHEEL, self.placed())
        self.assertIn(f"(Wheel) {OTHER}.png", self.placed(self.other))
        self.assertEqual((result["games"], result["unmatched"]), (3, 1))

    def test_a_hidden_kind_is_never_fetched_even_when_asked(self) -> None:
        self.policy.set("hidden_media_kinds", ["wheel"])

        self.ran(media_fill.start([self.id_of(self.folder)], ["wheel", "backglass"]))
        self.ran(media_fill.start(slots=[(self.id_of(self.other), "wheel")]))

        self.assertEqual(set(self.placed()), {f"(Backglass) {FOLDER}.png"})
        self.assertEqual(self.placed(self.other), {})

    def test_a_slot_fetches_that_one_kind(self) -> None:
        self.ran(media_fill.start(slots=[(self.id_of(self.folder), "backglass")]))

        self.assertEqual(set(self.placed()), {f"(Backglass) {FOLDER}.png"})

    def test_the_size_the_display_is_set_to_is_the_one_fetched(self) -> None:
        self.config.read_dict({"media": {"playfield_resolution": "1k"}})

        self.ran(media_fill.start([self.id_of(self.folder)], ["playfield"]))

        self.assertEqual(self.placed(), {f"(Playfield) {FOLDER}.png": _url("table-1k.png")})

    def test_a_file_already_there_is_not_replaced(self) -> None:
        held = self.game("Held", "fathom", **{"wheel.png": b"mine"})

        self.ran(media_fill.start([self.id_of(held)], ["wheel"]))

        self.assertEqual(self.placed(held), {"wheel.png": "mine"})
        self.downloads.assert_not_called()

    def test_it_will_not_run_beside_another_fill(self) -> None:
        with jobs.track(jobs.KIND_MEDIA_FILL), self.assertRaises(service_errors.BlockedError):
            media_fill.start()


class BackglassFileTests(_Library):
    """A backglass picture drawn from the game's own backglass file, with no source."""

    def setUp(self) -> None:
        super().setUp()
        jobs.reset_for_tests()
        self.addCleanup(jobs.reset_for_tests)
        row = {"id": "tbl0000001", "filename": f"{FOLDER}.vpx", "launcher": "vpx",
               "launcher_app_configurable": True}
        for patcher in (patch.object(backglass_ops, "_table_rows", return_value=[row]),
                        patch("common.games.launcher_ops.effective_values", return_value={}),
                        patch.object(sized_media, "ROOT", self.root / "sized"),
                        patch.object(directb2s_parser, "ROOT", self.root / "glass")):
            patcher.start()
            self.addCleanup(patcher.stop)
        directb2s_parser._facts.clear()
        self.addCleanup(directb2s_parser._facts.clear)
        self.glass = self.folder / f"{FOLDER}.directb2s"
        self.glass.write_bytes(_directb2s())
        self.unmatched = self.game(OTHER, "")
        (self.unmatched / f"{OTHER}.directb2s").write_bytes(_directb2s())
        self.backglass = f"(Backglass) {FOLDER}.png"

    def sources_off(self) -> None:
        patcher = patch.object(sources, "enabled_ids", return_value=())
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_game_with_no_match_gets_its_backglass_from_the_file(self) -> None:
        result = media_fill.fill([self.unmatched])

        self.assertIn(f"(Backglass) {OTHER}.png", self.placed(self.unmatched))
        self.assertEqual(result["filled"], 1)
        self.downloads.assert_not_called()

    def test_with_no_source_on_the_file_still_fills(self) -> None:
        self.sources_off()

        result = media_fill.fill([self.folder])

        self.assertEqual(set(self.placed()), {self.backglass})
        self.assertEqual(result["filled"], 1)
        self.downloads.assert_not_called()

    def test_the_picture_is_recorded_as_drawn_from_the_file(self) -> None:
        media_fill.fill([self.folder])

        recorded = asset_origin.sources(self.folder)[f"medias/{self.backglass}"]
        self.assertEqual(recorded["host"], asset_origin.BACKGLASS_FILE)

    def test_a_game_with_only_a_b2spro_gets_its_backglass_from_it(self) -> None:
        self.glass.rename(self.folder / f"{FOLDER}.b2spro")
        directb2s_parser._facts.clear()
        self.sources_off()

        media_fill.fill([self.folder])

        recorded = asset_origin.sources(self.folder)[f"medias/{self.backglass}"]
        self.assertEqual(recorded["host"], asset_origin.B2SPRO_FILE)

    def test_the_slot_the_file_filled_is_not_fetched_online(self) -> None:
        media_fill.fill([self.folder])

        self.assertNotEqual(self.placed()[self.backglass], _url("bg-1k.png"))
        self.assertNotIn(_url("bg-1k.png"),
                         [call.args[0] for call in self.downloads.call_args_list])
        self.assertEqual(self.placed()[f"(Wheel) {FOLDER}.png"], _url("wheel.png"))

    def test_a_picture_already_there_is_left(self) -> None:
        held = self.game("Held", "fathom", **{"bg.png": b"mine"})
        (held / "Held.directb2s").write_bytes(_directb2s())

        media_fill.fill([held], kinds=["backglass"])

        self.assertEqual(self.placed(held), {"bg.png": "mine"})

    def test_a_file_that_cannot_be_read_falls_back_to_the_online_picture(self) -> None:
        self.glass.write_bytes(b"not a backglass at all")
        directb2s_parser._facts.clear()

        media_fill.fill([self.folder], kinds=["backglass"])

        self.assertEqual(self.placed()[self.backglass], _url("bg-1k.png"))

    def test_a_file_that_offers_but_fails_to_draw_falls_back_online(self) -> None:
        with patch.object(backglass_ops, "place_picture", side_effect=OSError("gone")):
            result = media_fill.fill([self.folder], kinds=["backglass"])

        self.assertEqual(self.placed()[self.backglass], _url("bg-1k.png"))
        self.assertEqual((result["filled"], result["failed"]), (1, 0))

    def test_a_matched_game_is_not_read_for_kinds_only_a_source_can_give(self) -> None:
        with patch.object(media_fill, "_gaps", return_value=[]) as gaps:
            media_fill.fill([self.unmatched], kinds=["wheel"])

        gaps.assert_not_called()

    def test_a_hidden_backglass_is_not_drawn(self) -> None:
        self.policy.set("hidden_media_kinds", ["backglass"])

        media_fill.fill([self.unmatched])

        self.assertEqual(self.placed(self.unmatched), {})

    def test_the_plan_counts_the_file_as_available_and_as_local(self) -> None:
        found = media_fill.plan()

        row = next(one for one in found["kinds"] if one["kind"] == "backglass")
        self.assertEqual((row["missing"], row["available"], row["local"]), (2, 2, 2))
        self.assertEqual(found["unmatched"], 1)

    def test_the_plan_offers_the_file_with_every_source_off(self) -> None:
        self.sources_off()

        row = next(one for one in media_fill.plan()["kinds"] if one["kind"] == "backglass")

        self.assertEqual((row["available"], row["local"]), (2, 2))

    def test_a_game_with_no_file_has_nothing_local(self) -> None:
        self.glass.unlink()
        directb2s_parser._facts.clear()

        found = media_fill.plan([next(iter(self.games))])

        row = next(one for one in found["kinds"] if one["kind"] == "backglass")
        self.assertEqual((row["available"], row["local"]), (1, 0))

    def test_a_fill_started_for_the_backglass_places_it(self) -> None:
        self.sources_off()
        job = media_fill.start(kinds=["backglass"])
        deadline = time.monotonic() + 10
        while job.state == jobs.RUNNING:
            self.assertLess(time.monotonic(), deadline, "the fill never finished")
            time.sleep(0.02)

        result = cast(dict[str, Any], job.result)
        self.assertEqual(result["filled"], 2)
        self.assertIn(self.backglass, self.placed())


def _md5(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


WHEEL = f"(Wheel) {FOLDER}.png"
PLAYFIELD = f"(Playfield) {FOLDER}.png"
OLD_WHEEL = b"the wheel as it was"
OLD_TABLE = b"the playfield as it was"

# The catalog after it replaced the wheel and the playfield.
REPLACED = {"fathom": {
    "wheel": _url("wheel-new.png"), "wheel_md5": "wheel-new-md5",
    "4k": {"table": _url("table-4k-new.png"), "table_md5": "table-4k-new-md5"},
    "1k": {"table": _url("table-1k-new.png"), "table_md5": "table-1k-new-md5"},
}}


class _Replaced(_Library):
    def setUp(self) -> None:
        super().setUp()
        loaded = patch("common.online.vpsdb_cache.VPinMediaDatabase.load",
                       return_value=REPLACED)
        self.load = loaded.start()
        self.addCleanup(loaded.stop)
        replaced = patch("common.online.vpsdb_media.download_file", self.downloads)
        replaced.start()
        self.addCleanup(replaced.stop)

    def ours(self, name: str, data: bytes, recorded: bytes | None = None) -> Path:
        path = self.folder / "medias" / name
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(data)
        media_placement.record_origin(self.folder, path, "vpinmediadb",
                                      _md5(data if recorded is None else recorded))
        return path

    def recorded(self, name: str) -> str:
        return str(asset_origin.sources(self.folder)[f"medias/{name}"]["hash"])


class UpdateTests(_Replaced):
    def test_ours_and_stale_is_replaced_at_the_configured_size(self) -> None:
        self.ours(WHEEL, OLD_WHEEL)
        self.ours(PLAYFIELD, OLD_TABLE)

        result = media_fill.update_downloaded()

        placed = self.placed()
        self.assertEqual(placed[WHEEL], _url("wheel-new.png"))
        self.assertEqual(placed[PLAYFIELD], _url("table-4k-new.png"))
        self.assertEqual(self.recorded(WHEEL), "wheel-new-md5")
        self.assertEqual(result["updated"], 2)

    @needs_posix_permissions
    def test_a_folder_that_cannot_be_written_is_left_and_said_once(self) -> None:
        self.ours(WHEEL, OLD_WHEEL)
        self.locked(self.folder)

        with self.assertLogs("vpinfe.common.games.media_fill", "WARNING") as logged:
            result = media_fill.update_downloaded()

        self.assertEqual(self.placed()[WHEEL], OLD_WHEEL.decode())
        self.assertEqual((result["updated"], result["failed"], result["skipped"]), (0, 0, 1))
        self.assertEqual(len(logged.records), 1)

    def test_the_catalog_is_read_afresh(self) -> None:
        self.ours(WHEEL, OLD_WHEEL)
        held = {"fathom": {"wheel": _url("wheel.png"), "wheel_md5": _md5(OLD_WHEEL)}}

        with patch.object(vpinmediadb, "_MANIFEST", held):
            media_fill.update_downloaded()

        self.assertEqual(self.placed()[WHEEL], _url("wheel-new.png"))

    def test_ours_and_current_is_not_read(self) -> None:
        self.load.return_value = {"fathom": {"wheel": _url("wheel.png"),
                                             "wheel_md5": _md5(OLD_WHEEL)}}
        self.ours(WHEEL, OLD_WHEEL)

        with patch("common.online.vpsdb_media.file_md5") as hashed:
            result = media_fill.update_downloaded()

        hashed.assert_not_called()
        self.assertEqual(self.placed()[WHEEL], OLD_WHEEL.decode())
        self.assertEqual(result["updated"], 0)

    def test_art_still_published_at_another_size_is_left(self) -> None:
        self.load.return_value = {"fathom": {
            "1k": {"table": _url("table-1k.png"), "table_md5": _md5(OLD_TABLE)},
            "4k": {"table": _url("table-4k-new.png"), "table_md5": "table-4k-new-md5"}}}
        self.ours(PLAYFIELD, OLD_TABLE)

        media_fill.update_downloaded()

        self.assertEqual(self.placed()[PLAYFIELD], OLD_TABLE.decode())

    def test_art_changed_since_it_was_recorded_is_left(self) -> None:
        self.ours(WHEEL, b"drawn over", recorded=OLD_WHEEL)

        media_fill.update_downloaded()

        self.assertEqual(self.placed()[WHEEL], "drawn over")
        self.downloads.assert_not_called()

    def test_art_with_no_ledger_entry_is_left(self) -> None:
        path = self.folder / "medias" / WHEEL
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(OLD_WHEEL)

        media_fill.update_downloaded()

        self.assertEqual(self.placed()[WHEEL], OLD_WHEEL.decode())
        self.downloads.assert_not_called()

    def test_a_file_another_host_placed_is_never_replaced(self) -> None:
        self.ours(PLAYFIELD, OLD_TABLE)
        recording = self.ours(WHEEL, OLD_WHEEL)
        media_placement.record_origin(self.folder, recording, asset_origin.RECORDED,
                                      _md5(OLD_WHEEL))

        media_fill.update_downloaded()

        placed = self.placed()
        self.assertEqual(placed[WHEEL], OLD_WHEEL.decode())
        self.assertEqual(placed[PLAYFIELD], _url("table-4k-new.png"))

    def test_a_kind_this_library_does_not_collect_is_left(self) -> None:
        self.policy.set("hidden_media_kinds", ["wheel"])
        self.ours(WHEEL, OLD_WHEEL)

        media_fill.update_downloaded()

        self.assertEqual(self.placed()[WHEEL], OLD_WHEEL.decode())

    def test_a_source_switched_off_is_not_asked(self) -> None:
        self.policy.set("hidden_sources", ["vpinmediadb"])
        self.ours(WHEEL, OLD_WHEEL)

        media_fill.update_downloaded()

        self.assertEqual(self.placed()[WHEEL], OLD_WHEEL.decode())
        self.downloads.assert_not_called()

    def test_a_failed_download_keeps_the_file_and_its_record(self) -> None:
        self.ours(WHEEL, OLD_WHEEL)
        self.downloads.side_effect = OSError("offline")

        result = media_fill.update_downloaded()

        self.assertEqual(self.placed(), {WHEEL: OLD_WHEEL.decode()})
        self.assertEqual(self.recorded(WHEEL), _md5(OLD_WHEEL))
        self.assertEqual(result["failed"], 1)

    def test_the_progress_says_what_the_job_is_doing(self) -> None:
        reporter = MagicMock()

        media_fill.update_downloaded(reporter)

        self.assertEqual(reporter.progress.call_args.args[2],
                         i18n.t("said.media_update", game=FOLDER))


RENAMED = "Fathom VPW"
OWN_WHEEL = b"the renamed table's own wheel"


class BuildMetadataTests(_Replaced):
    """The wheel this game launches with is its table's own; the folder-named one is
    unused and ours."""

    launched_as = RENAMED

    def setUp(self) -> None:
        super().setUp()
        self.ours(WHEEL, OLD_WHEEL)
        self.own = self.folder / "medias" / f"(Wheel) {RENAMED}.png"
        self.own.write_bytes(OWN_WHEEL)

    def build(self, update_downloaded_media: bool = True) -> None:
        self.config["Settings"] = {"gamerootdir": str(self.root)}
        self.config["updates"] = {"update_downloaded_media": str(update_downloaded_media)}
        game = next(iter(self.games.values()))
        game.wheel_image_path = str(self.own)
        vps = VPSdb.__new__(VPSdb)
        vps.data = [{"id": "fathom", "name": "Fathom", "manufacturer": "Bally",
                     "year": 1981}]
        vps.vpinmediadbjson = REPLACED
        vps._media_downloader = VPSMediaDownloader(
            REPLACED, playfieldvariant="table", playfieldresolution="4k",
            playfieldvideoresolution="4k", update_downloaded=update_downloaded_media)
        parser = MagicMock()
        parser.single_file_extract.return_value = {"filename": f"{RENAMED}.vpx"}
        with patch("common.games.game_repository.all_games", return_value=[game]), \
                patch.object(metadata_service, "VPSdb", return_value=vps), \
                patch.object(metadata_service, "VPXParser", return_value=parser):
            metadata_service.build_metadata(update_all=True, iniconfig=self.config)

    def test_stale_art_named_as_the_folder_is_replaced(self) -> None:
        self.build()

        self.assertEqual(self.placed()[WHEEL], _url("wheel-new.png"))
        self.assertEqual(self.recorded(WHEEL), "wheel-new-md5")

    def test_the_catalog_the_downloader_holds_is_the_one_the_update_reads(self) -> None:
        with patch("common.online.vpsdb_cache.VPinMediaDatabase.load") as load:
            self.build()

        load.assert_not_called()

    def test_the_tables_own_wheel_is_left_byte_for_byte(self) -> None:
        self.build()

        self.assertEqual(self.own.read_bytes(), OWN_WHEEL)
        self.assertNotIn(f"medias/{self.own.name}", asset_origin.sources(self.folder))

    def test_with_updates_off_stale_art_is_left(self) -> None:
        self.build(update_downloaded_media=False)

        self.assertEqual(self.placed()[WHEEL], OLD_WHEEL.decode())


class LookTests(_Replaced):
    """The daily comparison while the automatic replacement is off: it only looks."""

    def setUp(self) -> None:
        super().setUp()
        self.config.read_dict({"updates": {"update_downloaded_media": "false"}})
        self.game_id = next(iter(self.games))

    def look(self, proceed: Any = lambda: True) -> dict:
        return media_fill.update_downloaded(None, proceed, replace=False)

    def test_a_stale_picture_is_kept_as_a_change_and_the_file_is_left(self) -> None:
        self.ours(WHEEL, OLD_WHEEL)

        result = self.look()

        self.assertEqual(self.placed()[WHEEL], OLD_WHEEL.decode())
        self.assertEqual(self.recorded(WHEEL), _md5(OLD_WHEEL))
        self.downloads.assert_not_called()
        self.assertEqual((result["found"], result["updated"]), (1, 0))
        (change,) = picture_changes.pending()
        self.assertEqual({name: change[name] for name in ("game_id", "vps_id", "kind", "source",
                                                          "md5", "id")},
                         {"game_id": self.game_id, "vps_id": "fathom", "kind": "wheel",
                          "source": "vpinmediadb", "md5": "wheel-new-md5",
                          "id": "fathom:wheel"})

    def test_the_source_says_what_is_later_for_the_game_and_slot(self) -> None:
        self.ours(WHEEL, OLD_WHEEL)
        self.look()
        who = {"game_id": self.game_id, "vps_id": "fathom"}

        self.assertEqual(
            sources.upgrades(who, "wheel"),
            {"vpinmediadb": {"source": "vpinmediadb", "size": "", "md5": "wheel-new-md5"}})
        self.assertEqual(sources.upgrades(who, "playfield"), {})
        self.assertEqual(sources.upgrades({"game_id": "elsewhere"}, "wheel"), {})
        self.assertEqual(sources.upgrades({"vps_id": "fathom"}, "wheel"), {})

    def test_a_picture_the_person_changed_is_never_a_change(self) -> None:
        self.ours(WHEEL, b"drawn over", recorded=OLD_WHEEL)

        self.assertEqual(self.look()["found"], 0)
        self.assertEqual(picture_changes.pending(), [])

    def test_a_change_goes_when_the_file_changes_under_it(self) -> None:
        path = self.ours(WHEEL, OLD_WHEEL)
        self.look()

        path.write_bytes(b"edited since")

        self.assertEqual(picture_changes.pending(), [])

    def test_a_change_goes_when_the_picture_is_taken(self) -> None:
        self.ours(WHEEL, OLD_WHEEL)
        self.look()

        media_fill.update_downloaded()

        self.assertEqual(self.placed()[WHEEL], _url("wheel-new.png"))
        self.assertEqual(picture_changes.pending(), [])

    def test_ignore_updates_sets_the_picture_aside(self) -> None:
        self.ours(WHEEL, OLD_WHEEL)
        self.look()
        (change,) = picture_changes.pending()

        vps_marks.mark(vps_marks.IGNORED, vps_marks.UPDATES, change["id"])

        self.assertEqual(picture_changes.pending(), [])
        self.assertEqual(picture_changes.pending(self.game_id), [])

    def test_keep_mine_sets_aside_that_version_and_not_the_next(self) -> None:
        self.ours(WHEEL, OLD_WHEEL)
        self.look()
        (change,) = picture_changes.pending()
        vps_marks.mark(vps_marks.IGNORED, vps_marks.VERSION, change["id"], change["md5"])
        self.assertEqual(picture_changes.pending(), [])

        newer = {"fathom": {"wheel": _url("wheel-newer.png"), "wheel_md5": "wheel-newer-md5"}}
        self.load.return_value = newer
        self.look()

        self.assertEqual([one["md5"] for one in picture_changes.pending()], ["wheel-newer-md5"])

    def test_an_ignored_game_has_no_change_offered(self) -> None:
        self.ours(WHEEL, OLD_WHEEL)
        self.look()
        vps_marks.mark(vps_marks.IGNORED, vps_marks.GAME, "fathom")

        self.assertEqual(picture_changes.pending(), [])

    def test_nothing_is_offered_while_the_replacement_is_automatic(self) -> None:
        self.ours(WHEEL, OLD_WHEEL)
        self.look()
        self.config.read_dict({"updates": {"update_downloaded_media": "true"}})

        self.assertEqual(picture_changes.pending(), [])

    def test_nothing_is_offered_while_the_source_is_off(self) -> None:
        self.ours(WHEEL, OLD_WHEEL)
        self.look()
        self.policy.set("hidden_sources", ["vpinmediadb"])

        self.assertEqual(picture_changes.pending(), [])
        self.assertEqual(sources.upgrades({"game_id": self.game_id}, "wheel"), {})

    def test_a_look_that_finds_none_clears_the_last_answer(self) -> None:
        self.ours(WHEEL, OLD_WHEEL)
        self.look()
        self.load.return_value = {"fathom": {"wheel": _url("wheel.png"),
                                             "wheel_md5": _md5(OLD_WHEEL)}}
        self.look()

        self.assertEqual(picture_changes.pending(), [])

    def test_a_look_that_stopped_early_keeps_the_last_answer(self) -> None:
        self.ours(WHEEL, OLD_WHEEL)
        self.look()

        self.look(proceed=lambda: False)

        self.assertEqual(len(picture_changes.pending()), 1)

    def test_a_look_that_cannot_ask_keeps_the_last_answer(self) -> None:
        self.ours(WHEEL, OLD_WHEEL)
        self.look()
        with patch.object(media_fill, "_reachable", return_value=()):
            self.look()

        self.assertEqual(len(picture_changes.pending()), 1)

    def test_an_open_pane_is_told_what_moved(self) -> None:
        self.ours(WHEEL, OLD_WHEEL)
        with patch.object(sources, "changed") as told:
            self.look()

        told.assert_called_once_with("fathom")

    def test_the_daily_sweep_with_the_setting_off_only_looks(self) -> None:
        self.ours(WHEEL, OLD_WHEEL)
        with patch.object(jobs, "submit",
                          side_effect=lambda kind, work, **_: work(MagicMock())), \
                patch.object(backglass_ops, "redraw_all") as redrawn:
            self.assertTrue(media_fill.start_update())

        redrawn.assert_not_called()
        self.assertEqual(self.placed()[WHEEL], OLD_WHEEL.decode())
        self.assertEqual(len(picture_changes.pending()), 1)

    def test_the_daily_sweep_is_owed_with_the_setting_off(self) -> None:
        was = timestamps.iso_to_epoch("2026-01-01T00:00:00Z") or 0
        with patch.object(vpsdb_sync.device_registry, "get_device_registry") as registry:
            registry.return_value.self_get.return_value = "2026-01-01T00:00:00Z"

            self.assertFalse(vpsdb_sync.art_due(was + 3600))
            self.assertTrue(vpsdb_sync.art_due(was + 86400 + 1))

    def test_a_look_is_its_own_kind_of_job_and_a_replacement_is_not(self) -> None:
        kinds: list[str] = []
        with patch.object(jobs, "submit", side_effect=lambda kind, work, **_: kinds.append(kind)):
            media_fill.start_update()
            self.config.read_dict({"updates": {"update_downloaded_media": "true"}})
            media_fill.start_update()

        self.assertEqual(kinds, [jobs.KIND_MEDIA_LOOK, jobs.KIND_MEDIA_FILL])

    def test_with_the_source_off_nothing_is_asked_and_no_job_is_made(self) -> None:
        self.policy.set("hidden_sources", ["vpinmediadb"])
        with patch.object(vpinmediadb, "refresh_manifest") as asked, \
                patch.object(jobs, "submit") as submitted:
            self.assertFalse(media_fill.start_update())
            result = media_fill.update_downloaded(None, replace=False)

        asked.assert_not_called()
        submitted.assert_not_called()
        self.assertEqual(result["games"], 0)

    def test_a_replacement_stopped_early_keeps_the_answer_a_look_left(self) -> None:
        self.ours(WHEEL, OLD_WHEEL)
        self.look()

        media_fill.update_downloaded(None, lambda: False, replace=True)

        self.assertEqual(len(picture_changes.pending()), 1)

    @needs_posix_permissions
    def test_a_look_reads_a_folder_that_cannot_be_written(self) -> None:
        self.ours(WHEEL, OLD_WHEEL)
        self.locked(self.folder)

        result = self.look()

        self.assertEqual((result["found"], result["skipped"]), (1, 0))

    def test_a_game_a_look_could_not_read_keeps_what_the_last_look_said_of_it(self) -> None:
        self.ours(WHEEL, OLD_WHEEL)
        self.look()

        with patch.object(media_fill, "_stale", side_effect=OSError("unreadable")):
            result = self.look()

        self.assertEqual((result["unread"], result["games"]), (1, 0))
        self.assertEqual(len(picture_changes.pending()), 1)

    def test_a_game_the_look_read_is_replaced_whatever_another_could_not_be(self) -> None:
        self.ours(WHEEL, OLD_WHEEL)
        self.look()
        self.load.return_value = {"fathom": {"wheel": _url("wheel.png"),
                                             "wheel_md5": _md5(OLD_WHEEL)}}
        other = self.game("Other (Williams 1990)", "other")
        broken = next(key for key, game in self.games.items()
                      if str(game.full_path_game) == str(other))
        real = media_fill._stale

        def stale(game_id: str, *args: Any) -> Any:
            if game_id == broken:
                raise OSError("unreadable")
            return real(game_id, *args)

        with patch.object(media_fill, "_stale", stale):
            self.look()

        self.assertEqual(picture_changes.pending(), [])

    def test_a_look_that_finds_what_it_found_before_tells_no_pane(self) -> None:
        self.ours(WHEEL, OLD_WHEEL)
        self.look()

        with patch.object(sources, "changed") as told:
            self.look()

        told.assert_not_called()

    def test_a_look_tells_the_pane_of_the_entry_whose_picture_went(self) -> None:
        self.ours(WHEEL, OLD_WHEEL)
        self.look()
        self.load.return_value = {"fathom": {"wheel": _url("wheel.png"),
                                             "wheel_md5": _md5(OLD_WHEEL)}}

        with patch.object(sources, "changed") as told:
            self.look()

        told.assert_called_once_with("fathom")


class ArtDueTests(TempTree):
    def test_never_compared_is_due(self) -> None:
        self.assertTrue(vpsdb_sync.art_due())

    def test_compared_today_is_not_due_and_yesterday_is(self) -> None:
        device_registry.get_device_registry().set_self(
            vps_art_checked="2026-01-01T00:00:00Z")
        was = timestamps.iso_to_epoch("2026-01-01T00:00:00Z") or 0

        self.assertFalse(vpsdb_sync.art_due(was + 3600))
        self.assertTrue(vpsdb_sync.art_due(was + 86400))

    def test_a_running_fill_leaves_it_due(self) -> None:
        with patch.object(media_fill, "start_update", return_value=False):
            self.assertFalse(vpsdb_sync.update_art())
        self.assertTrue(vpsdb_sync.art_due())

    def test_a_started_sweep_is_stamped(self) -> None:
        with patch.object(media_fill, "start_update", return_value=True):
            self.assertTrue(vpsdb_sync.update_art())
        self.assertFalse(vpsdb_sync.art_due())


class QueueTests(_Library):
    def setUp(self) -> None:
        super().setUp()
        media_fill.reset_for_tests()
        jobs.reset_for_tests()
        self.addCleanup(media_fill.reset_for_tests)
        self.addCleanup(jobs.reset_for_tests)

    def test_a_request_runs_as_a_job(self) -> None:
        media_fill.request([self.folder])

        deadline = time.monotonic() + 10
        while media_fill._running or jobs.active(jobs.KIND_MEDIA_FILL):
            self.assertLess(time.monotonic(), deadline, "the fill never finished")
            time.sleep(0.02)
        self.assertIn(f"(Wheel) {FOLDER}.png", self.placed())

    def test_switched_off_a_request_does_nothing(self) -> None:
        self.config.read_dict({"updates": {"get_media_for_new_games": "false"}})
        with patch.object(media_fill, "_start") as start:
            media_fill.request([self.folder])

        start.assert_not_called()

    def test_a_request_during_a_run_joins_it(self) -> None:
        batches: list[list[str]] = []

        def one_batch(folders: list[str], *_: object) -> dict[str, int]:
            batches.append(list(folders))
            if len(batches) == 1:
                media_fill.request(["second"])
            return {"games": len(folders), "filled": 0, "unmatched": 0, "failed": 0}

        with patch.object(media_fill, "_start") as start, \
                patch.object(media_fill, "_fill", side_effect=one_batch):
            media_fill.request(["first"])
            media_fill._work(MagicMock())

        self.assertEqual(batches, [["first"], ["second"]])
        start.assert_called_once()
        self.assertFalse(media_fill._running)


if __name__ == "__main__":
    unittest.main()
