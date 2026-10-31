"""Ties VPinFE makes itself: a file whose authors and version name exactly one release."""

from __future__ import annotations

import copy
import json
import unittest
from typing import Any
from unittest.mock import patch

from common.games import exact_ties, identity_claims, ties
from common.games.game_metadata import set_table_source, table_source
from common.runtime import jobs as job_registry
from tests.support.library import TempTree, fake_game, write_game

GAME_ID = "Exact00001"
FOLDER = "Space Station (Williams 1987)"
VPX = f"{FOLDER}.vpx"
OTHER_VPX = f"{FOLDER} v2.vpx"
ENTRY = "ss_1987"

CATALOG = [{
    "id": ENTRY, "name": "Space Station",
    "tableFiles": [
        {"id": "rel_one", "version": "1.0", "tableFormat": "VPX",
         "authors": ["Ada", "Bruno"]},
        {"id": "rel_two", "version": "2.0", "tableFormat": "VPX",
         "authors": ["Ada", "Bruno"]},
        {"id": "rel_other", "version": "2.0", "tableFormat": "VPX",
         "authors": ["Cleo"]},
        {"id": "rel_dup_a", "version": "3.0", "authors": ["Dana"]},
        {"id": "rel_dup_b", "version": "3.0", "authors": ["Dana", "Eli"]},
        {"id": "rel_future", "version": "4.0", "tableFormat": "FP", "authors": ["Fay"]},
        {"id": "rel_team", "version": "5.0", "authors": ["VPinWorkshop", "Gus"]},
        {"id": "rel_nobody", "version": "6.0"},
    ],
}]


def _table(table_id: str, filename: str, version: str, authors: list[str],
           **more: Any) -> dict[str, Any]:
    return {"id": table_id, "filename": filename, "version": version,
            "authors": authors, **more}


class ExactReleaseTests(unittest.TestCase):
    entry = CATALOG[0]

    def _find(self, version: str, authors: list[str]) -> str:
        return str(exact_ties.exact_release(_table("t", VPX, version, authors),
                                            self.entry).get("id") or "")

    def test_the_release_with_the_files_authors_and_version_is_the_one(self) -> None:
        self.assertEqual("rel_one", self._find("1.0", ["Ada", "Bruno"]))

    def test_the_same_authors_on_another_version_are_another_release(self) -> None:
        self.assertEqual("rel_two", self._find("2.0", ["Ada", "Bruno"]))

    def test_a_file_names_the_lead_and_the_release_credits_the_team(self) -> None:
        self.assertEqual("rel_one", self._find("1.0", ["ada"]))

    def test_a_version_two_releases_share_for_the_authors_is_not_sure(self) -> None:
        self.assertEqual("", self._find("3.0", ["Dana"]))

    def test_the_other_authors_release_at_the_same_version_is_not_taken(self) -> None:
        self.assertEqual("rel_other", self._find("2.0", ["Cleo"]))

    def test_a_version_that_is_the_same_number_spelled_differently_is_not_sure(self) -> None:
        self.assertEqual("", self._find("1", ["Ada", "Bruno"]))
        self.assertEqual("rel_one", self._find("v1.0", ["Ada", "Bruno"]))

    def test_a_team_credited_under_another_spelling_is_the_same_team(self) -> None:
        self.assertEqual("rel_team", self._find("5.0", ["VPW"]))
        self.assertEqual("rel_team", self._find("5.0", ["vpw team", "Gus"]))

    def test_a_release_crediting_nobody_is_not_taken(self) -> None:
        self.assertEqual("", self._find("6.0", ["Ada"]))

    def test_no_authors_or_no_version_is_nothing_to_go_on(self) -> None:
        self.assertEqual("", self._find("1.0", []))
        self.assertEqual("", self._find("", ["Ada"]))
        self.assertEqual("", self._find("final", ["Ada"]))

    def test_a_release_in_a_format_the_table_cannot_play_is_not_taken(self) -> None:
        self.assertEqual("", self._find("4.0", ["Fay"]))


class TieTheLibraryTests(TempTree):
    def setUp(self) -> None:
        super().setUp()
        job_registry.reset_for_tests()
        self.info: dict[str, Any] = {
            "Info": {"Title": "Space Station", "VPSId": ENTRY},
            "vpinfe": {"game_id": GAME_ID},
            "tables": {
                "tbl0000001": _table("tbl0000001", VPX, "1.0", ["Ada"]),
                "tbl0000002": _table("tbl0000002", OTHER_VPX, "9.9", ["Ada"]),
            },
        }
        self.folder = write_game(self.root, FOLDER, info=copy.deepcopy(self.info),
                                 vpx=False, files={VPX: b"vpx", OTHER_VPX: b"vpx"})
        self.game = fake_game(self.folder, FOLDER, meta=copy.deepcopy(self.info))
        for target in ("common.games.exact_ties.load_vpsdb",
                       "common.games.game_service.load_vpsdb"):
            patcher = patch(target, return_value=copy.deepcopy(CATALOG))
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch("common.games.exact_ties.all_games", return_value=[self.game])
        patcher.start()
        self.addCleanup(patcher.stop)

    def _stored(self, table_id: str = "tbl0000001") -> dict:
        written = json.loads((self.folder / f"{FOLDER}.info").read_text())
        return written["tables"][table_id]

    def test_a_sure_table_is_tied_and_records_the_version_listed(self) -> None:
        self.assertEqual(1, exact_ties.tie_library())

        self.assertEqual(
            {"vps_file_id": "rel_one", "confirmed_by": identity_claims.EXACT,
             "vps_version": "1.0"}, table_source(self._stored()))

    def test_a_table_with_no_exact_release_is_left_alone(self) -> None:
        exact_ties.tie_library()

        self.assertNotIn("source", self._stored("tbl0000002"))

    def test_a_second_sweep_changes_nothing(self) -> None:
        exact_ties.tie_library()
        before = (self.folder / f"{FOLDER}.info").read_text()

        self.assertEqual(0, exact_ties.tie_library())
        self.assertEqual(before, (self.folder / f"{FOLDER}.info").read_text())

    def test_a_tie_a_person_made_is_not_replaced(self) -> None:
        set_table_source(self.game, VPX, "rel_two")
        exact_ties.tie_library()

        stored = table_source(self._stored())
        self.assertEqual(("rel_two", identity_claims.USER),
                         (stored["vps_file_id"], stored["confirmed_by"]))

    def test_a_person_picking_replaces_a_tie_made_for_them(self) -> None:
        exact_ties.tie_library()
        set_table_source(self.game, VPX, "rel_two")

        self.assertEqual("rel_two", table_source(self._stored())["vps_file_id"])
        self.assertEqual("user", table_source(self._stored())["confirmed_by"])

    def test_a_table_made_by_applying_a_patch_is_not_tied(self) -> None:
        self.info["tables"]["tbl0000001"]["source"] = {
            "base": {"file": "old.vpx", "hash": "abc"}, "patch": {"format": "jojodiff"}}
        (self.folder / f"{FOLDER}.info").write_text(json.dumps(self.info))
        self.game.meta_config = copy.deepcopy(self.info)

        self.assertEqual(0, exact_ties.tie_library())

    def test_what_a_file_was_built_from_stays_beside_the_tie(self) -> None:
        self.info["tables"]["tbl0000001"]["source"] = {"hash": "abc"}
        (self.folder / f"{FOLDER}.info").write_text(json.dumps(self.info))

        exact_ties.tie_library()

        self.assertEqual("abc", table_source(self._stored())["hash"])
        self.assertEqual("rel_one", table_source(self._stored())["vps_file_id"])

    def test_a_game_matched_to_nothing_is_not_looked_at(self) -> None:
        self.info["Info"]["VPSId"] = ""
        (self.folder / f"{FOLDER}.info").write_text(json.dumps(self.info))

        self.assertEqual(0, exact_ties.tie_library())

    def test_a_game_waiting_on_its_upgrade_is_not_written(self) -> None:
        self.game.info_pending_upgrade = True

        self.assertEqual(0, exact_ties.tie_library())
        self.assertNotIn("source", self._stored())

    def test_no_catalog_ties_nothing(self) -> None:
        with patch("common.games.exact_ties.load_vpsdb", return_value=[]):
            self.assertEqual(0, exact_ties.tie_library())

    def test_one_game_failing_does_not_stop_the_rest(self) -> None:
        with patch("common.games.exact_ties.tie_game", side_effect=[OSError("busy"), 1]):
            with patch("common.games.exact_ties.all_games",
                       return_value=[self.game, self.game]), \
                    self.assertLogs("vpinfe.common.games.exact_ties", level="WARNING"):
                self.assertEqual(1, exact_ties.tie_library())

    def test_another_pass_over_the_info_files_is_not_joined(self) -> None:
        with job_registry.track(job_registry.KIND_LIBRARY_SCAN):
            self.assertIsNone(exact_ties.tie_library())
        self.assertNotIn("source", self._stored())

    def test_the_sweep_is_a_scan_job_and_says_what_it_did(self) -> None:
        exact_ties.tie_library()

        [job] = [one for one in job_registry.recent()
                 if one.kind == job_registry.KIND_LIBRARY_SCAN]
        self.assertEqual("1 table matched to its VPS release", job.detail)

    def test_a_folder_that_cannot_be_written_to_is_neither_read_nor_written(self) -> None:
        with patch("common.games.exact_ties.library_folders.blocked_games",
                   side_effect=lambda games: [(g, "away") for g in games]), \
                patch("common.games.exact_ties.load_game_meta") as read:
            self.assertEqual(0, exact_ties.tie_library())

        read.assert_not_called()
        self.assertNotIn("source", self._stored())

    def test_a_game_with_nothing_to_tie_is_not_read_from_disk(self) -> None:
        self.info["tables"].pop("tbl0000001")
        self.game.meta_config = self.info
        with patch("common.games.exact_ties.load_game_meta") as read, \
                patch("common.games.exact_ties.library_folders.blocked_games",
                      return_value=[]) as asked:
            self.assertEqual(0, exact_ties.tie_library())

        read.assert_not_called()
        self.assertEqual([[]], [list(call.args[0]) for call in asked.call_args_list])

    def test_a_table_a_person_unbound_is_not_tied_again(self) -> None:
        exact_ties.tie_library()
        set_table_source(self.game, VPX, "")

        self.assertEqual(0, exact_ties.tie_library())
        self.assertEqual({"vps_file_id": "", "confirmed_by": identity_claims.USER},
                         table_source(self._stored()))

    def test_picking_after_unbinding_replaces_the_answer(self) -> None:
        set_table_source(self.game, VPX, "")
        set_table_source(self.game, VPX, "rel_two")

        self.assertEqual("rel_two", table_source(self._stored())["vps_file_id"])


class TieTests(unittest.TestCase):
    def test_tying_records_the_basis_and_the_version_listed(self) -> None:
        self.assertEqual({"vps_file_id": "r", "confirmed_by": "user", "vps_version": "1.2"},
                         ties.tie({}, "r", "user", {"version": " 1.2 "}))

    def test_a_release_listing_no_version_records_none_and_drops_the_old_one(self) -> None:
        self.assertEqual({"vps_file_id": "r2", "confirmed_by": "user"},
                         ties.tie({"vps_file_id": "r", "vps_version": "1.0"}, "r2", "user", {}))

    def test_untying_leaves_what_else_the_source_held(self) -> None:
        held = {"vps_file_id": "r", "confirmed_by": "user", "vps_version": "1.0", "hash": "h"}

        self.assertEqual({"hash": "h"}, ties.untie(held))

    def test_a_table_row_says_what_the_release_state_is(self) -> None:
        tied = {"vps_file_id": "r", "confirmed_by": "user"}
        for row, expected in (
                ({}, ""),
                ({"game_vps_id": "e"}, ties.UNTIED),
                ({"game_vps_id": "e", "source": {"hash": "h"}}, ties.UNTIED),
                ({"game_vps_id": "e", "source": {"vps_file_id": "", "confirmed_by": "user"}},
                 ties.UNTIED),
                ({"game_vps_id": "e", "source": {"base": {"file": "a.vpx"}}}, ""),
                ({"game_vps_id": "e", "source": {"patch": {"format": "jojodiff"}}}, ""),
                ({"game_vps_id": "e", "source": {**tied, "base": {"file": "a.vpx"}}}, "user"),
                ({"game_vps_id": "e", "source": tied}, "user"),
                ({"game_vps_id": "e", "source": {"vps_file_id": "r"}}, ties.TIED),
                ({"source": tied}, "")):
            with self.subTest(row=row):
                self.assertEqual(expected, ties.release_state(row))

    def test_a_hidden_table_a_patch_was_applied_to_has_nothing_to_pick(self) -> None:
        rows: list[dict[str, Any]] = [
            {"id": "a", "game_vps_id": "e", "hidden": True},
            {"id": "b", "game_vps_id": "e", "hidden": True},
            {"id": "c", "game_vps_id": "e", "source": {"base": {"table_id": "a"}}}]
        bases = ties.patch_bases(rows)

        self.assertEqual(frozenset({"a"}), bases)
        self.assertEqual(["", ties.UNTIED, ""],
                         [ties.release_state(row, bases) for row in rows])

    def test_declining_leaves_a_persons_answer_and_what_the_file_was_built_from(self) -> None:
        held = {"vps_file_id": "r", "confirmed_by": "exact", "vps_version": "1.0",
                "base": {"file": "a.vpx"}}

        declined = ties.decline(held)

        self.assertEqual({"vps_file_id": "", "confirmed_by": "user", "base": {"file": "a.vpx"}},
                         declined)
        self.assertTrue(ties.declined(declined))
        self.assertFalse(ties.declined({"vps_file_id": "r", "confirmed_by": "user"}))
        self.assertFalse(ties.declined({}))


if __name__ == "__main__":
    unittest.main()
