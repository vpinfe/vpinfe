"""The versioned surface themes are written against.

A theme declares what it was built for; the payload is built in the current shape and
projected back. The point of the levels is that a theme never has to guess.
"""

from __future__ import annotations

import json
import unittest

from frontend.theme_contract import (
    CURRENT_CONTRACT,
    OLDEST_CONTRACT,
    declared_contract,
    project,
)
from tests.support.library import TempTree

ROW = {
    "gameDirName": "Example",
    "WheelImagePath": "/t/wheel.png",
    "meta": {
        "Info": {"Title": "Example", "VPSId": "vps-1"},
        "User": {"Rating": 4},
        "vpinfe": {"schema": 2, "game_id": "tuF3WogthK", "alt_title": "Alt"},
        "tables": {"E.vpx": {"rom": "afm_113b", "authors": ["jpsalas"]}},
        "assets": {"medias/bg.png": {"source": {"host": "user"}}},
    },
    "pupPackExists": True,
    "altSoundExists": False,
    "altColorExists": False,
}


class DeclarationTests(TempTree):
    def setUp(self):
        super().setUp()
        self.theme = self.root / "MyTheme"
        self.theme.mkdir()

    def _manifest(self, body):
        (self.theme / "manifest.json").write_text(json.dumps(body), encoding="utf-8")

    def test_a_theme_that_says_nothing_gets_the_oldest_contract(self):
        """Every theme written before this existed, which is all of them."""
        self._manifest({"name": "MyTheme", "version": "1.0"})

        self.assertEqual(declared_contract(self.theme), OLDEST_CONTRACT)

    def test_the_version_a_theme_needs_decides_its_contract(self):
        self._manifest({"name": "MyTheme", "min_vpinfe": "3.0"})

        self.assertEqual(declared_contract(self.theme), 2)

    def test_a_theme_that_runs_on_2_x_gets_the_oldest(self):
        """The reason the key is a version: a theme states what it runs on, not what
        payload it wants, and 2.x never served anything but contract 1."""
        for minimum in ("2.0", "2.6.0", "1.0"):
            with self.subTest(minimum=minimum):
                self._manifest({"name": "MyTheme", "min_vpinfe": minimum})
                self.assertEqual(declared_contract(self.theme), OLDEST_CONTRACT)

    def test_a_point_release_of_3_0_still_reaches_contract_2(self):
        """A theme needing 3.1 needs 3.0 too, so it must not fall back to contract 1."""
        for minimum in ("3", "3.0.1", "3.1", "4.0", "v3.0", "3.0.0-beta.1"):
            with self.subTest(minimum=minimum):
                self._manifest({"name": "MyTheme", "min_vpinfe": minimum})
                self.assertEqual(declared_contract(self.theme), 2)

    def test_a_theme_from_the_future_is_served_what_we_have(self):
        """Refusing to draw anything would be worse than drawing most of it."""
        self._manifest({"name": "MyTheme", "min_vpinfe": "99.0"})

        self.assertEqual(declared_contract(self.theme), CURRENT_CONTRACT)

    def test_a_manifest_we_cannot_read_is_not_fatal(self):
        for body in ("{ not json", '{"min_vpinfe": "three"}', '{"min_vpinfe": null}'):
            with self.subTest(body=body):
                (self.theme / "manifest.json").write_text(body, encoding="utf-8")
                self.assertEqual(declared_contract(self.theme), OLDEST_CONTRACT)

    def test_the_retired_contract_key_is_not_read(self):
        """It was 3.0-internal and no published theme ever declared it, so it is gone
        rather than aliased. A theme still carrying one must not get contract 2 by it."""
        self._manifest({"name": "MyTheme", "contract": 2})

        self.assertEqual(declared_contract(self.theme), OLDEST_CONTRACT)

    def test_a_missing_manifest_is_not_fatal(self):
        self.assertEqual(declared_contract(self.theme), OLDEST_CONTRACT)


class ProjectionTests(unittest.TestCase):
    def test_the_current_contract_is_handed_the_payload_as_built(self):
        self.assertIs(project(ROW, CURRENT_CONTRACT), ROW)

    def test_contract_1_reads_the_shape_it_was_written_against(self):
        """VPXFile is synthesised from the default table - the payload itself has
        not carried that section since the .info stopped having one."""
        meta = project(ROW, 1)["meta"]

        self.assertEqual(meta["VPXFile"]["filename"], "E.vpx")
        self.assertEqual(meta["VPXFile"]["rom"], "afm_113b")
        self.assertEqual(meta["Info"]["Rom"], "afm_113b")
        self.assertEqual(meta["Info"]["Authors"], ["jpsalas"])
        # The 2.x spelling, not the .info's. A theme written before 3.0 reads
        # meta.VPinFE.alttitle, and this asserted the new name until the parity gate
        # learned to look inside meta.
        self.assertEqual(meta["VPinFE"]["alttitle"], "Alt")
        self.assertNotIn("alt_title", meta["VPinFE"])

    def test_contract_1_reads_the_score_2x_kept(self):
        """2.x kept one reading as `User.Score`; the .info keeps one per ROM now."""
        entries = [{"section": "HIGH SCORES", "rank": 1, "initials": "ABC", "score": 10}]
        row = {**ROW, "meta": {**ROW["meta"], "User": {"Rating": 4, "HighScores": {
            "afm_113b": {"read_at": None, "score_kind": "Leaderboard", "entries": entries,
                         "new": []}}}}}

        user = project(row, 1)["meta"]["User"]

        self.assertEqual(user["Score"], {"rom": "afm_113b", "score_type": "Leaderboard",
                                         "entries": entries})
        self.assertNotIn("HighScores", user)
        self.assertEqual(user["Rating"], 4)

    def test_contract_1_keeps_the_old_spelling_of_the_detect_flags(self):
        row = {**ROW, "meta": {**ROW["meta"],
                               "tables": {"E.vpx": {"detect_ssf": True,
                                                        "detect_scorbit": True}}}}

        vpx = project(row, 1)["meta"]["VPXFile"]

        self.assertTrue(vpx["detectssf"])
        self.assertTrue(vpx["detectscorebit"], "the old name kept its typo on the wire")
        self.assertNotIn("detect_ssf", vpx)

    def test_a_detect_flag_written_as_a_string_is_still_a_boolean(self):
        """A JSON "false" is truthy to anything that reads it without care."""
        row = {**ROW, "meta": {**ROW["meta"],
                               "tables": {"E.vpx": {"detect_ssf": "false",
                                                        "detect_lut": "true"}}}}

        vpx = project(row, 1)["meta"]["VPXFile"]

        self.assertIs(vpx["detectssf"], False)
        self.assertIs(vpx["detectlut"], True)

    def test_contract_1_still_gets_the_addon_flags(self):
        vpx = project(ROW, 1)["meta"]["VPXFile"]

        self.assertTrue(vpx["pupPackExists"])
        self.assertFalse(vpx["altSoundExists"])

    def test_the_current_contract_does_not_carry_the_retired_section(self):
        self.assertNotIn("VPXFile", project(ROW, CURRENT_CONTRACT)["meta"])

    def test_contract_1_does_not_see_what_replaced_it(self):
        """Serving both shapes lets a theme work by accident against fields it never
        declared, which is the failure this exists to prevent."""
        meta = project(ROW, 1)["meta"]

        for section in ("tables", "vpinfe", "assets"):
            self.assertNotIn(section, meta, section)

    def test_projecting_does_not_touch_the_payload_it_was_given(self):
        original = json.dumps(ROW, sort_keys=True)

        project(ROW, 1)

        self.assertEqual(json.dumps(ROW, sort_keys=True), original)

    def test_the_renamed_row_keys_come_back_with_their_old_names(self):
        """PAR-22. The four keys the vocabulary rename moved are the only things
        outside meta the projection touches, and a contract 1 theme must not be able
        to tell they moved."""
        projected = project(ROW, 1)

        self.assertEqual(projected["tableDirName"], "Example")
        self.assertNotIn("gameDirName", projected,
                         "serving both spellings would let a theme work by accident")

    def test_everything_else_outside_meta_is_untouched(self):
        projected = project(ROW, 1)

        self.assertEqual(projected["WheelImagePath"], "/t/wheel.png")

    def test_a_game_with_no_table_still_projects(self):
        row = {"meta": {"Info": {"Title": "x"}, "vpinfe": {}, "tables": {}}}

        meta = project(row, 1)["meta"]

        self.assertEqual(meta["Info"]["Rom"], "")
        self.assertNotIn("tables", meta)


if __name__ == "__main__":
    unittest.main()
