"""Saying which upstream release a table is, and taking it back.

The producer here is a person picking from a list nothing ordered - the only producer
the design allows, because the identifier that would fill this automatically was
measured at chance and is confidently wrong more often than not.

So the claim carries `confirmed_by: user` and never a `kind`: `matched` would be the
only value anything can write, and a constant discriminator invites a reader to trust
it as one.
"""

from __future__ import annotations

import copy
import unittest
from unittest.mock import patch

from starlette.testclient import TestClient

import httpapi
from common.games.game_metadata import set_table_source, table_source
from tests.support.library import TempTree, fake_game, game_info, write_game
from tests.support.saved import save_record, saved_record

GAME_ID = "Sourced001"
FOLDER = "The Addams Family (Bally 1992)"
VPX = f"{FOLDER}.vpx"
TABLE_ID = "tbl0000001"
RELEASE = "Wq40ng8f"
FUTURE_RELEASE = "FpRelease1"

INFO = game_info("The Addams Family", vps_id="aT_GONvw", game_id=GAME_ID,
                 tables={TABLE_ID: {"id": TABLE_ID, "filename": VPX, "version": "2.3.1"}})

CATALOG = [{
    "id": "aT_GONvw", "name": "The Addams Family", "manufacturer": "Bally", "year": 1992,
    "tableFiles": [
        {"id": RELEASE, "version": "2.4.41", "tableFormat": "VPX",
         "authors": ["G5k", "3rdAxis"], "updatedAt": 1719187200000,
         "urls": [{"url": "https://vpuniverse.com/gone", "broken": True},
                  {"url": "https://www.vpforums.org/file"}]},
        {"id": "Other111", "version": "3.0", "authors": ["Bigus1"],
         "urls": [{"url": "26493-mata-hari"}, {"url": "https://mega.nz/dead", "broken": True},
                  {"url": "https://drive.google.com/dead", "broken": True}]},
    ],
}, {
    "id": "futureEntry", "name": "Future Pinball Table", "manufacturer": "Bally", "year": 1980,
    "tableFiles": [{"id": FUTURE_RELEASE, "version": "1.0", "tableFormat": "FP",
                    "authors": ["Someone"]}],
}]


class TableSourceTests(TempTree):
    def setUp(self) -> None:
        super().setUp()
        # Copied, or one test writing into the game's config mutates the module-level
        # fixture and the next test starts from what the last one left.
        meta = copy.deepcopy(INFO)
        self.folder = write_game(self.root, FOLDER, info=meta, vpx=False,
                                 files={VPX: b"vpx"})
        self.game = fake_game(self.folder, FOLDER, meta=copy.deepcopy(meta))
        for target in ("common.games.game_service.load_vpsdb",):
            patcher = patch(target, return_value=list(CATALOG))
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch("common.games.game_repository.catalog", return_value={GAME_ID: self.game})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = TestClient(httpapi.create_api_app(),
                                 raise_server_exceptions=False)

    def _stored(self) -> dict:
        return saved_record(self.folder / f"{FOLDER}.info")["tables"][TABLE_ID]

    def _write_source(self, source: dict) -> None:
        """Onto the file, not the in-memory copy: the writer re-reads from disk first,
        exactly so that a surface holding an older copy cannot win."""
        path = self.folder / f"{FOLDER}.info"
        written = saved_record(path)
        written["tables"][TABLE_ID]["source"] = source
        save_record(path, written)

    def _table(self) -> dict:
        response = self.client.get(f"/games/{GAME_ID}/tables")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["tables"][0]

    def _bind(self, release: str) -> dict:
        response = self.client.put(f"/games/{GAME_ID}/tables/{TABLE_ID}/source",
                                   json={"vps_file_id": release})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_a_table_starts_claiming_nothing(self) -> None:
        """Absent, not "unmatched" - nothing has looked, and only a matcher could
        produce the state that says a look came back empty."""
        self.assertIsNone(self._table()["source"])

    def test_binding_records_who_said_so(self) -> None:
        self._bind(RELEASE)

        self.assertEqual(table_source(self._stored()),
                         {"vps_file_id": RELEASE, "confirmed_by": "user",
                          "vps_version": "2.4.41"})

    def test_the_tie_keeps_the_version_the_catalog_listed_when_it_was_made(self) -> None:
        self._bind(RELEASE)

        self.assertEqual(self._table()["source"]["vps_version"], "2.4.41")

    def test_a_release_the_catalog_does_not_hold_records_no_version(self) -> None:
        self._write_source({"vps_file_id": RELEASE, "confirmed_by": "user",
                            "vps_version": "1.0"})
        self._bind("GoneFromVps")

        self.assertNotIn("vps_version", table_source(self._stored()))

    def test_no_kind_is_written(self) -> None:
        """`matched` would be the only value anything can produce, and a field that is
        constant stores nothing while reading as a discriminator."""
        self._bind(RELEASE)

        self.assertNotIn("kind", table_source(self._stored()))

    def test_the_release_comes_back_named(self) -> None:
        """A client rendering the row would otherwise put a catalog id on screen, or
        fetch every build of the machine to avoid it."""
        source = self._bind(RELEASE)["source"]

        self.assertEqual(source["version"], "2.4.41")
        self.assertEqual(source["authors"], ["G5k", "3rdAxis"])

    def test_unbinding_leaves_a_persons_answer_that_the_table_is_none_of_them(self) -> None:
        self._bind(RELEASE)
        self._bind("")

        self.assertEqual({"vps_file_id": "", "confirmed_by": "user"},
                         table_source(self._stored()))
        self.assertEqual("", self._table()["source"]["vps_file_id"])
        self.assertIsNone(self._table()["update_available"])

    def test_picking_again_replaces_the_answer(self) -> None:
        self._bind("")
        self._bind(RELEASE)

        self.assertEqual(RELEASE, table_source(self._stored())["vps_file_id"])

    def test_unbinding_keeps_what_the_file_was_built_from(self) -> None:
        """`base` and `patch` say how the bytes were made. Taking back a claim about
        which record they are must not lose them - a patched file cannot be rebuilt
        without its base."""
        built = {"base": {"file": "old.vpx", "hash": "abc"},
                 "patch": {"format": "jojodiff"}}
        self._write_source(dict(built))
        set_table_source(self.game, VPX, RELEASE)
        set_table_source(self.game, VPX, "")

        self.assertEqual(table_source(self._stored()),
                         {**built, "vps_file_id": "", "confirmed_by": "user"})

    def test_a_claim_sits_beside_what_it_was_built_from(self) -> None:
        self._write_source({"base": {"file": "old.vpx"},
                            "confirmed_by": "construction"})
        set_table_source(self.game, VPX, RELEASE)
        stored = table_source(self._stored())

        self.assertEqual(stored["base"], {"file": "old.vpx"})
        self.assertEqual(stored["vps_file_id"], RELEASE)
        # An explicit re-match overrides a stronger basis: the ranking governs
        # automatic writes, not somebody correcting one by hand.
        self.assertEqual(stored["confirmed_by"], "user")

    def test_an_id_the_catalog_no_longer_holds_still_reports_the_claim(self) -> None:
        """The binding is the user's statement; the catalog is a lookup that can fail."""
        source = self._bind("GoneFromVps")["source"]

        self.assertEqual(source["vps_file_id"], "GoneFromVps")
        self.assertEqual(source["version"], "")

    def test_a_release_in_another_format_is_refused(self) -> None:
        response = self.client.put(f"/games/{GAME_ID}/tables/{TABLE_ID}/source",
                                   json={"vps_file_id": FUTURE_RELEASE})

        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn("Visual Pinball X", response.text)
        self.assertIn("Future Pinball", response.text)
        self.assertNotIn("source", self._stored())

    def test_a_release_filed_under_no_format_is_accepted(self) -> None:
        self.assertEqual(self._bind("Other111")["source"]["vps_file_id"], "Other111")

    def test_a_stored_match_in_another_format_reads_as_no_match(self) -> None:
        self._write_source({"vps_file_id": FUTURE_RELEASE, "confirmed_by": "user"})

        self.assertIsNone(self._table()["source"])

    def test_what_a_stored_cross_format_match_sat_beside_survives_in_the_lens(self) -> None:
        self._write_source({"vps_file_id": FUTURE_RELEASE, "confirmed_by": "user",
                            "base": {"file": "old.vpx"}})

        source = self._table()["source"]
        self.assertEqual(source["vps_file_id"], "")
        self.assertEqual(source["base"]["file"], "old.vpx")

    def test_the_row_carries_the_formats_its_app_plays(self) -> None:
        self.assertEqual(self._table()["vps_formats"], ["VPX"])

    def test_the_releases_route_lists_the_entry_builds(self) -> None:
        response = self.client.get("/vps/entry/aT_GONvw/releases")
        self.assertEqual(response.status_code, 200, response.text)
        listed = response.json()["releases"]

        self.assertEqual([item["vps_file_id"] for item in listed],
                         [RELEASE, "Other111"])
        self.assertEqual(listed[0]["updated_at"], "2024-06-24T00:00:00Z")

    def test_a_release_lists_every_link_and_leads_with_one_that_works(self) -> None:
        listed = self.client.get("/vps/entry/aT_GONvw/releases").json()["releases"]

        self.assertEqual(listed[0]["urls"],
                         [{"url": "https://vpuniverse.com/gone", "broken": True},
                          {"url": "https://www.vpforums.org/file", "broken": False}])
        self.assertEqual(listed[0]["url"], "https://www.vpforums.org/file")

    def test_a_value_that_is_not_a_web_address_is_not_a_link(self) -> None:
        links = self.client.get("/vps/entry/aT_GONvw/releases").json()["releases"][1]["urls"]

        self.assertEqual([one["url"] for one in links],
                         ["https://mega.nz/dead", "https://drive.google.com/dead"])

    def test_where_every_link_is_broken_the_release_still_leads_with_the_first(self) -> None:
        listed = self.client.get("/vps/entry/aT_GONvw/releases").json()["releases"]

        self.assertEqual(listed[1]["url"], "https://mega.nz/dead")

    def test_a_release_with_no_link_has_none_to_lead_with(self) -> None:
        listed = self.client.get("/vps/entry/futureEntry/releases").json()["releases"]

        self.assertEqual((listed[0]["url"], listed[0]["urls"]), ("", []))

    def test_releases_for_an_unknown_entry_are_a_404(self) -> None:
        self.assertEqual(self.client.get("/vps/entry/nope/releases").status_code, 404)


if __name__ == "__main__":
    unittest.main()
