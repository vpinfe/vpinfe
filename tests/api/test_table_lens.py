"""The library seen by launchable file, and the things you can do to one.

A game's row cannot tell its tables apart, which is what this lens is for. What is
asserted here is that a table can be identified without its filename - by version and
author, which come out of the .vpx itself - and that the two acts that are not deleting
it, choosing the default and taking it out of play, do what they say.
"""

from __future__ import annotations

import subprocess
import unittest
from typing import Any
from unittest.mock import patch

from starlette.testclient import TestClient

import httpapi
from common.games import launchers, table_lens, ties
from common.games.info_file import INFO_SCHEMA
from common.host import launch
from common.i18n import t
from tests.support.launchers_api import put_launcher
from tests.support.library import TempTree, fake_game, own_launchers, write_game
from tests.support.saved import saved_record

GAME_ID = "Lens00000001"
FOLDER = "Cactus Canyon (Bally 1998)"
DESKTOP = f"{FOLDER}.vpx"
VR = f"{FOLDER} - VR.vpx"

INFO: dict[str, Any] = {
    "Info": {"Name": "Cactus Canyon", "Manufacturer": "Bally", "Year": "1998"},
    "vpinfe": {"game_id": GAME_ID, "schema": INFO_SCHEMA},
    "tables": {
        "tbl0000001": {"id": "tbl0000001", "filename": DESKTOP,
                       "version": "1.0", "authors": ["someone"]},
        "tbl0000002": {"id": "tbl0000002", "filename": VR,
                       "version": "2.1", "authors": ["someone else"]},
    },
}


class _Lens(TempTree):
    info: dict = INFO

    def setUp(self) -> None:
        super().setUp()
        self.folder = write_game(self.root, FOLDER, info=self.info, vpx=False,
                                 files={DESKTOP: b"vpx", VR: b"vpx"})
        game = fake_game(self.folder, FOLDER, meta=self.info)
        patcher = patch("common.games.game_repository.catalog", return_value={GAME_ID: game})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)

    def _rows(self):
        response = self.client.get("/tables")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["tables"]

    def _info(self) -> dict:
        return saved_record(self.folder / f"{FOLDER}.info")


class TableLensTests(_Lens):
    def test_one_row_per_file_not_one_per_folder(self) -> None:
        """The whole point: two tables of one game are two rows, not one."""
        self.assertEqual(len(self._rows()), 2)

    def test_a_row_carries_its_game_so_it_reads_on_its_own(self) -> None:
        for row in self._rows():
            self.assertEqual(row["game"], "Cactus Canyon")
            self.assertEqual(row["game_id"], GAME_ID)

    def test_a_table_is_identified_without_its_filename(self) -> None:
        """Filenames of one game share forty characters and differ in two. Version and
        author come out of the .vpx and are what tell them apart."""
        by_version = {row["version"]: row["authors"] for row in self._rows()}
        self.assertEqual(by_version, {"1.0": ["someone"], "2.1": ["someone else"]})

    def test_the_apps_that_play_a_table_are_listed(self) -> None:
        body = self.client.get("/tables/apps").json()
        self.assertEqual([app["id"] for app in body["apps"]], ["vpx", "generic"])
        self.assertIn(".vpx", body["apps"][0]["suffixes"])

    def test_generic_claims_nothing_by_extension(self) -> None:
        """It is listed as something to configure, never as something that could take a
        table away from the app that understands it."""
        body = self.client.get("/tables/apps").json()
        generic = next(app for app in body["apps"] if app["id"] == "generic")

        self.assertEqual(generic["suffixes"], [])

    def test_every_table_says_which_app_plays_it(self) -> None:
        self.assertEqual({row["app"] for row in self._rows()}, {"vpx"})

    def test_the_launchers_are_read_once_for_the_whole_list(self) -> None:
        """Each read rebuilds the config store, so a read per table put seconds on the
        Games grid's first load."""
        real = launchers.LauncherStore.launchers
        with patch.object(launchers.LauncherStore, "launchers", autospec=True,
                          side_effect=real) as read:
            self._rows()

        self.assertEqual(read.call_count, 1)


class DefaultTests(_Lens):
    def test_choosing_a_default_records_the_id_not_the_name(self) -> None:
        """Stored as an id so the choice survives a rename of the file."""
        response = self.client.put(f"/games/{GAME_ID}/default_table",
                                   json={"table": "tbl0000002"})

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self._info()["vpinfe"]["default_table"], "tbl0000002")

    def test_the_choice_shows_up_on_the_table_it_names(self) -> None:
        self.client.put(f"/games/{GAME_ID}/default_table", json={"table": "tbl0000002"})
        by_id = {row["id"]: row["default"] for row in self._rows()}

        self.assertEqual(by_id, {"tbl0000001": False, "tbl0000002": True})

    def test_clearing_it_goes_back_to_resolving_from_the_folder(self) -> None:
        """Absent is not the same as a choice of the first one: it means nobody has an
        opinion, and the resolver is free to answer."""
        self.client.put(f"/games/{GAME_ID}/default_table", json={"table": "tbl0000002"})
        self.client.put(f"/games/{GAME_ID}/default_table", json={"table": ""})

        self.assertNotIn("default_table", self._info().get("vpinfe", {}))

    def test_a_table_this_game_does_not_have_is_refused(self) -> None:
        response = self.client.put(f"/games/{GAME_ID}/default_table",
                                   json={"table": "tbl0009999"})
        self.assertEqual(response.status_code, 404)

    def test_a_hidden_table_cannot_be_the_default(self) -> None:
        self.client.put(f"/games/{GAME_ID}/tables/tbl0000002/hidden", json={"hidden": True})

        response = self.client.put(f"/games/{GAME_ID}/default_table",
                                   json={"table": "tbl0000002"})

        self.assertEqual(response.status_code, 400, response.text)
        self.assertNotIn("default_table", self._info().get("vpinfe", {}))


class HiddenTests(_Lens):
    def test_hiding_takes_it_out_of_play_without_touching_the_file(self) -> None:
        response = self.client.put(f"/games/{GAME_ID}/tables/tbl0000002/hidden",
                                   json={"hidden": True})

        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["table"]["hidden"])
        self.assertTrue((self.folder / VR).is_file(), "the .vpx must stay on disk")

    def test_unhiding_puts_it_back(self) -> None:
        self.client.put(f"/games/{GAME_ID}/tables/tbl0000002/hidden", json={"hidden": True})
        body = self.client.put(f"/games/{GAME_ID}/tables/tbl0000002/hidden",
                               json={"hidden": False}).json()

        self.assertFalse(body["table"]["hidden"])

    def test_hiding_one_leaves_the_other_alone(self) -> None:
        self.client.put(f"/games/{GAME_ID}/tables/tbl0000002/hidden", json={"hidden": True})
        by_id = {row["id"]: row["hidden"] for row in self._rows()}

        self.assertEqual(by_id, {"tbl0000001": False, "tbl0000002": True})


class HidingTheDefaultTests(_Lens):
    def _hide(self, table: str) -> dict:
        response = self.client.put(f"/games/{GAME_ID}/tables/{table}/hidden",
                                   json={"hidden": True})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_hiding_the_chosen_default_clears_the_choice(self) -> None:
        self.client.put(f"/games/{GAME_ID}/default_table", json={"table": "tbl0000002"})

        body = self._hide("tbl0000002")

        self.assertNotIn("default_table", self._info()["vpinfe"])
        self.assertEqual(body["default"]["id"], "tbl0000001")

    def test_hiding_another_table_keeps_the_choice(self) -> None:
        self.client.put(f"/games/{GAME_ID}/default_table", json={"table": "tbl0000002"})

        body = self._hide("tbl0000001")

        self.assertEqual(self._info()["vpinfe"]["default_table"], "tbl0000002")
        self.assertEqual(body["default"]["id"], "tbl0000002")

    def test_hiding_every_table_leaves_nothing_offered(self) -> None:
        self._hide("tbl0000001")

        self.assertIsNone(self._hide("tbl0000002")["default"])


class HidingADefaultNamedByFileTests(_Lens):
    """A 2.x library records the default by filename."""

    info = {**INFO, "vpinfe": {**INFO["vpinfe"], "default_table": VR}}

    def test_hiding_it_clears_the_choice(self) -> None:
        response = self.client.put(f"/games/{GAME_ID}/tables/tbl0000002/hidden",
                                   json={"hidden": True})

        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn("default_table", self._info()["vpinfe"])


class TableScriptTests(_Lens):
    """The sidecar script. VPX runs a `<table>.vbs` in place of the one inside the .vpx,
    so this is not housekeeping - it decides which script the table plays with."""

    def _vbs(self):
        return self.folder / f"{VR[:-4]}.vbs"

    def test_a_sidecar_is_reported_as_the_script_that_resolves(self) -> None:
        self._vbs().write_text("' patched", encoding="utf-8")

        table = next(t for t in self._rows() if t["id"] == "tbl0000002")
        detail = self.client.get(f"/games/{GAME_ID}/tables").json()["tables"]
        script = next(t["assets"]["script"] for t in detail if t["id"] == table["id"])
        self.assertEqual(script["resolution"], "dedicated")

    def test_deleting_it_puts_the_table_back_on_its_own_script(self) -> None:
        self._vbs().write_text("' patched", encoding="utf-8")

        response = self.client.delete(f"/games/{GAME_ID}/tables/tbl0000002/script")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(self._vbs().exists())
        self.assertEqual(response.json()["assets"]["script"]["resolution"], "none")

    def test_deleting_one_that_is_not_there_is_refused(self) -> None:
        """Not silently fine: a client that thinks it removed something did not."""
        response = self.client.delete(f"/games/{GAME_ID}/tables/tbl0000002/script")

        self.assertEqual(response.status_code, 404)

    def test_the_other_table_keeps_its_own(self) -> None:
        """Stem-named, with no folder fallback - one table's sidecar is not the
        game's."""
        self._vbs().write_text("' patched", encoding="utf-8")
        (self.folder / f"{DESKTOP[:-4]}.vbs").write_text("' other", encoding="utf-8")

        self.client.delete(f"/games/{GAME_ID}/tables/tbl0000002/script")

        self.assertTrue((self.folder / f"{DESKTOP[:-4]}.vbs").is_file())

    def test_extracting_where_there_is_no_launcher_says_so(self) -> None:
        """501, the answer /launch gives for the same condition: the table is fine, the
        machine cannot do the work. A 404 would say the table was not found."""
        response = self.client.post(f"/games/{GAME_ID}/tables/tbl0000002/script")

        self.assertEqual(response.status_code, 501, response.text)
        self.assertEqual(response.json()["error"]["code"], "feature_unavailable")

    def test_a_script_visual_pinball_did_not_write_is_said_once(self) -> None:
        failed = subprocess.CompletedProcess([], 1, stdout="", stderr="Unhandled exception")
        with patch.object(launch, "binary_for", return_value="/opt/vpx/VPinballX"), \
                patch("subprocess.run", return_value=failed), \
                self.assertLogs("vpinfe.common.games.game_service", "WARNING") as logged:
            response = self.client.post(f"/games/{GAME_ID}/tables/tbl0000002/script")

        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(response.json()["error"]["message"],
                         t("error.games.vpx_no_script"))
        self.assertIn("Unhandled exception", "\n".join(logged.output))

    def test_visual_pinball_that_will_not_start_is_said_in_words(self) -> None:
        with patch.object(launch, "binary_for", return_value="/opt/vpx/VPinballX"), \
                patch("subprocess.run", side_effect=PermissionError(13, "Permission denied")):
            response = self.client.post(f"/games/{GAME_ID}/tables/tbl0000002/script")

        self.assertEqual(response.json()["error"]["message"],
                         t("error.games.vpx_failed"))


class OwnSettingsTests(_Lens):
    def setUp(self) -> None:
        super().setUp()
        store = own_launchers(self, str(self.root))
        store.save([], {})
        patcher = patch.object(launchers, "get_launcher_store", return_value=store)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)
        put_launcher(self.client, "vpx", json={"app": "vpx", "display_name": "VPX"})

    def _held(self) -> dict:
        got = self.client.get(f"/games/{GAME_ID}/tables")
        self.assertEqual(got.status_code, 200, got.text)
        return {one["id"]: (one["launcher_settings_here"],
                            one["launcher_settings_from_folder"],
                            one["launcher_point_of_view"])
                for one in got.json()["tables"]}

    def test_a_table_with_no_file_holds_nothing(self) -> None:
        self.assertEqual(self._held(), {"tbl0000001": (0, 0, False),
                                        "tbl0000002": (0, 0, False)})

    def test_the_file_named_for_the_folder_is_its_table_s_and_reaches_the_other(self) -> None:
        (self.folder / f"{FOLDER}.ini").write_text(
            "[Backglass]\nBackglassOutput = 0\n[TableOverride]\nViewCabMode = 1\n")

        self.assertEqual(self._held(), {"tbl0000001": (2, 0, True),
                                        "tbl0000002": (0, 2, True)})

    def test_the_library_wide_list_counts_them_as_the_game_s_tables_do(self) -> None:
        (self.folder / f"{FOLDER}.ini").write_text(
            "[Backglass]\nBackglassOutput = 0\n[TableOverride]\nViewCabMode = 1\n")

        self.assertEqual({one["id"]: (one["launcher_settings_here"],
                                      one["launcher_settings_from_folder"],
                                      one["launcher_point_of_view"],
                                      one["launcher_app_configurable"])
                          for one in self._rows()},
                         {"tbl0000001": (2, 0, True, True),
                          "tbl0000002": (0, 2, True, True)})

    def test_both_lists_name_the_settings_and_the_camera_once(self) -> None:
        (self.folder / f"{FOLDER}.ini").write_text(
            "[Backglass]\nBackglassOutput = 0\n[TableOverride]\nViewCabMode = 1\n")
        got = self.client.get(f"/games/{GAME_ID}/tables")
        self.assertEqual(got.status_code, 200, got.text)
        named = {"tbl0000001": ["Backglass.BackglassOutput", table_lens.POINT_OF_VIEW],
                 "tbl0000002": ["Backglass.BackglassOutput", table_lens.POINT_OF_VIEW]}

        self.assertEqual({one["id"]: one["launcher_settings_keys"]
                          for one in got.json()["tables"]}, named)
        self.assertEqual({one["id"]: one["launcher_settings_keys"]
                          for one in self._rows()}, named)


class TableFeatureTests(_Lens):
    """The library-wide rows list their fields by hand, so anything a client reads there
    has to be named or it is silently dropped - which is how `rating` read as 0 twice."""

    def test_a_row_carries_what_the_script_uses(self) -> None:
        row = self._rows()[0]

        self.assertIn("features", row)
        self.assertIn("ssf", row["features"])

    def test_a_table_nobody_parsed_answers_null_not_false(self) -> None:
        """Three states. False here would report a clean bill of health for a script
        nothing has read."""
        row = self._rows()[0]

        self.assertIsNone(row["features"]["ssf"])


class TableRatingTests(_Lens):
    """A table's own rating. How a user sets one when the wheel shows a single entry
    per game is answered by the Manager's Tables grid: the row you rate is the file."""

    def test_rating_a_table_stores_it_against_that_table(self) -> None:
        response = self.client.put(f"/games/{GAME_ID}/tables/tbl0000002/rating",
                                   json={"rating": 4})

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["rating"], 4)

    def test_the_other_table_is_untouched(self) -> None:
        self.client.put(f"/games/{GAME_ID}/tables/tbl0000002/rating", json={"rating": 4})

        by_id = {row["id"]: row["rating"] for row in self._rows()}
        self.assertEqual(by_id, {"tbl0000001": 0, "tbl0000002": 4})

    def test_zero_clears_it(self) -> None:
        self.client.put(f"/games/{GAME_ID}/tables/tbl0000002/rating", json={"rating": 4})
        body = self.client.put(f"/games/{GAME_ID}/tables/tbl0000002/rating",
                               json={"rating": 0}).json()

        self.assertEqual(body["rating"], 0)

    def test_a_row_carries_its_game_s_rating_beside_its_own(self) -> None:
        """A library rated before tables had ratings of their own has only these."""
        rated = {**INFO, "User": {"Rating": 3}}
        with patch("common.games.game_repository.catalog", return_value={
                GAME_ID: fake_game(self.folder, FOLDER, meta=rated)}):
            rows = self._rows()

        self.assertEqual({(row["game_rating"], row["rating"]) for row in rows}, {(3, 0)})

    def test_a_rating_outside_the_scale_is_refused(self) -> None:
        """Refused rather than clamped, the same as a game's - storing 5 for a caller
        that sent 9 hides its bug."""
        response = self.client.put(f"/games/{GAME_ID}/tables/tbl0000002/rating",
                                   json={"rating": 9})

        self.assertEqual(response.status_code, 422)

    def test_it_survives_a_reread_and_leaves_the_play_counters_alone(self) -> None:
        """The rating shares the table's `user` block with the counters, so writing one
        must not disturb the other - the block is created by whichever comes first."""
        self.client.put(f"/games/{GAME_ID}/tables/tbl0000002/rating", json={"rating": 2})

        stored = self._info()
        user = stored["tables"]["tbl0000002"]["user"]
        self.assertEqual(user["rating"], 2)
        self.assertEqual(user["start_count"], 0)
        self.assertIsNone(user["last_run"])


class UpdateTests(_Lens):
    """A matched table says whether VPS lists a later version of its release."""

    info = {**INFO, "tables": {
        "tbl0000001": {**INFO["tables"]["tbl0000001"], "version": "3.0",
                       "source": {"vps_file_id": "release01", "confirmed_by": "user",
                                  "vps_version": "1.0"}},
        "tbl0000002": INFO["tables"]["tbl0000002"]}}

    def setUp(self) -> None:
        release = patch("common.games.table_lens.find_vps_release",
                        return_value={"version": "1.2", "authors": ["someone"]})
        release.start()
        self.addCleanup(release.stop)
        super().setUp()

    def _by_id(self) -> dict:
        return {row["id"]: row for row in self._rows()}

    def test_a_table_behind_its_release_says_so(self) -> None:
        row = self._by_id()["tbl0000001"]

        self.assertIs(row["update_available"], True)
        self.assertEqual("1.2", row["source"]["version"])

    def test_the_file_s_own_version_does_not_decide(self) -> None:
        """The file says 3.0 and VPS lists 1.2: it is behind because VPS moved since the
        tie, not because 3.0 is less than anything."""
        row = self._by_id()["tbl0000001"]

        self.assertEqual("3.0", row["version"])
        self.assertEqual("1.0", row["source"]["vps_version"])
        self.assertIs(row["update_available"], True)

    def test_a_table_matched_to_nothing_cannot_say(self) -> None:
        """Null, not false: nothing was weighed, which is not the same as up to date."""
        self.assertIsNone(self._by_id()["tbl0000002"]["update_available"])

    def test_a_game_s_own_tables_say_the_same(self) -> None:
        body = self.client.get(f"/games/{GAME_ID}/tables").json()
        tables = body["tables"] if isinstance(body, dict) else body

        said = {table["id"]: table["update_available"] for table in tables}
        self.assertEqual({"tbl0000001": True, "tbl0000002": None}, said)


class DerivedTagTests(_Lens):
    """A tag an extension's list derives rides on the table it names, in both lists."""

    info = UpdateTests.info

    def setUp(self) -> None:
        listed = patch("common.games.derived_tags._index",
                       return_value={("vps_release", "release01"): {"Weekly Challenge"}})
        listed.start()
        self.addCleanup(listed.stop)
        super().setUp()

    def test_the_library_list_carries_it(self) -> None:
        said = {row["id"]: row["derived_tags"] for row in self._rows()}

        self.assertEqual({"tbl0000001": ["Weekly Challenge"], "tbl0000002": []}, said)

    def test_a_game_s_own_tables_carry_it(self) -> None:
        body = self.client.get(f"/games/{GAME_ID}/tables").json()
        tables = body["tables"] if isinstance(body, dict) else body

        said = {table["id"]: table["derived_tags"] for table in tables}
        self.assertEqual({"tbl0000001": ["Weekly Challenge"], "tbl0000002": []}, said)


class GameOnVpsTests(_Lens):
    """A row says which VPS entry its game is matched to, where the catalog holds it."""

    info = {**INFO, "Info": {**INFO["Info"], "VPSId": "entry01"}}

    def _listed(self, catalog: list[dict]) -> list[dict]:
        patcher = patch("common.games.game_service.load_vpsdb", return_value=catalog)
        patcher.start()
        self.addCleanup(patcher.stop)
        return self._rows()

    def test_every_row_of_a_game_the_catalog_holds_names_its_entry(self) -> None:
        rows = self._listed([{"id": "entry01"}])

        self.assertEqual(["entry01", "entry01"], [row["game_vps_id"] for row in rows])

    def test_an_entry_the_catalog_no_longer_holds_is_not_named(self) -> None:
        rows = self._listed([{"id": "another"}])

        self.assertEqual(["", ""], [row["game_vps_id"] for row in rows])


class UpdateAvailableTests(unittest.TestCase):
    def test_the_version_at_the_tie_is_weighed_against_the_one_listed_now(self) -> None:
        for tied, listed, expected in (("1.0", "1.2", True), ("1.2", "1.2.0", False),
                                       ("1.3", "1.2", False), ("VP10", "1.2", None),
                                       ("1.0", "", None), ("", "1.2", None)):
            with self.subTest(tied=tied, listed=listed):
                source = {"vps_file_id": "release01", "version": listed, "vps_version": tied}
                self.assertIs(expected, ties.update_available(source))

    def test_a_version_with_no_release_behind_it_weighs_nothing(self) -> None:
        self.assertIsNone(ties.update_available({"version": "1.2", "vps_version": "1.0"}))
        self.assertIsNone(ties.update_available(None))


if __name__ == "__main__":
    unittest.main()
