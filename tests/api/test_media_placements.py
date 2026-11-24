"""Where a file can go for a kind, and what putting it there would replace.

The tier is a filename, so choosing where art lands is choosing what it is called.
That used to be inferred from whichever lens happened to be open; this is the list a
client offers instead, with the cost of each choice attached.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from starlette.testclient import TestClient

import httpapi
from tests.support.library import TempTree, fake_game, write_game

GAME_ID = "Placed00002"
FOLDER = "Cactus Canyon (Bally 1998)"
DESKTOP = f"{FOLDER}.vpx"
VR = f"{FOLDER} - VR.vpx"

INFO = {
    "Info": {"Name": "Cactus Canyon"},
    "VPinFE": {"game_id": GAME_ID},
    "tables": {"tbl0000001": {"id": "tbl0000001", "filename": DESKTOP},
               "tbl0000002": {"id": "tbl0000002", "filename": VR}},
}


class PlacementTests(TempTree):
    def setUp(self) -> None:
        super().setUp()
        self.folder = write_game(self.root, FOLDER, info=INFO, vpx=False,
                                 files={DESKTOP: b"vpx", VR: b"vpx"},
                                 medias={"table.png": b"\x89PNGdefault"})
        game = fake_game(self.folder, FOLDER, meta=INFO)
        patcher = patch("common.games.game_repository.catalog", return_value={GAME_ID: game})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)

    def _placements(self, kind: str = "playfield"):
        response = self.client.get(f"/games/{GAME_ID}/media/{kind}/placements")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_the_shared_name_comes_first_then_the_builds(self) -> None:
        """Shared leads because it is the common answer. The builds follow in filename
        order, which is the order the tables list uses - here that puts " - VR.vpx"
        ahead of ".vpx", since a hyphen sorts before a dot."""
        found = self._placements()["placements"]
        self.assertEqual((found[0]["table"], found[0]["label"]), ("", ""))
        self.assertEqual([item["label"] for item in found[1:]], [VR])

    def test_each_choice_shows_the_name_the_file_would_take(self) -> None:
        by_table = {item["table"]: item["base"] for item in self._placements()["placements"]}
        self.assertEqual(by_table[""], f"(Playfield) {FOLDER}")
        self.assertEqual(by_table["tbl0000002"], f"(Playfield) {FOLDER} - VR")

    def test_a_build_named_after_its_folder_is_not_offered_twice(self) -> None:
        """Cactus Canyon (Bally 1998).vpx sits in Cactus Canyon (Bally 1998), so its
        own name and the shared name are the same string - and then the same file.
        Most single-table folders look like this."""
        found = self._placements()["placements"]

        self.assertNotIn("tbl0000001", [item["table"] for item in found])
        self.assertEqual(len({item["base"] for item in found}), len(found),
                         "no two choices should write the same filename")

    def test_a_build_named_after_its_folder_in_another_case_is_not_offered_either(
            self) -> None:
        lower = f"{FOLDER.lower()}.vpx"
        info = {**INFO, "tables": {"tbl0000003": {"id": "tbl0000003", "filename": lower}}}
        folder = write_game(self.root / "other", FOLDER, info=info, vpx=False,
                            files={lower: b"vpx"})
        with patch("common.games.game_repository.catalog",
                   return_value={GAME_ID: fake_game(folder, FOLDER, meta=info)}):
            found = self._placements()["placements"]

        self.assertEqual([item["table"] for item in found], [""])

    def test_the_extension_is_left_off_because_the_file_decides_it(self) -> None:
        body = self._placements()
        self.assertNotIn(".", body["placements"][0]["base"].rsplit(")", 1)[-1])
        self.assertEqual(body["extensions"][0], ".png")

    def test_nothing_there_means_nothing_replaced(self) -> None:
        """The default-tier table.png is not at either of these tiers, so it is not
        in anybody's way - and reporting it would be inventing a conflict."""
        for item in self._placements()["placements"]:
            self.assertEqual(item["displaces"], [], item["label"])

    def test_a_file_already_at_that_tier_is_reported_against_it(self) -> None:
        (self.folder / "medias" / f"(Playfield) {FOLDER}.png").write_bytes(b"\x89PNG")
        found = self._placements()["placements"]

        by_table = {item["table"]: item["displaces"] for item in found}
        self.assertEqual(by_table[""], [f"medias/(Playfield) {FOLDER}.png"])
        self.assertEqual(by_table["tbl0000002"], [],
                         "a shared file is not in a build's way")

    def test_the_whole_family_goes_whatever_extension_arrives(self) -> None:
        """A .jpg landing over a .png takes the .png, so the count cannot depend on
        an extension the caller has not chosen yet."""
        (self.folder / "medias" / f"(Playfield) {FOLDER}.jpg").write_bytes(b"\xff\xd8")
        self.assertEqual(self._placements()["placements"][0]["displaces"],
                         [f"medias/(Playfield) {FOLDER}.jpg"])

    def test_an_unknown_kind_is_refused(self) -> None:
        self.assertEqual(
            self.client.get(f"/games/{GAME_ID}/media/nonesuch/placements").status_code,
            400)


class OverrideTests(PlacementTests):
    """What the game's own lens cannot otherwise see.

    Resolving without a table stem never looks at the table tier, so a slot one table
    differs on looks settled from the game's view. These are the only thing that says
    otherwise, and the reason a curator can find the odd one out in a folder.
    """

    def _overrides(self) -> dict:
        response = self.client.get(f"/games/{GAME_ID}/media/overrides")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["overrides"]

    def test_nothing_of_its_own_means_nothing_reported(self) -> None:
        self.assertEqual(self._overrides(), {})

    def test_a_table_with_its_own_file_is_named(self) -> None:
        (self.folder / "medias" / f"(Playfield) {FOLDER} - VR.png").write_bytes(b"\x89PNG")
        found = self._overrides()

        self.assertEqual(list(found), ["playfield"])
        self.assertEqual([item["table"] for item in found["playfield"]], ["tbl0000002"])

    def test_it_carries_what_tells_that_table_apart(self) -> None:
        """The id is for following; the filename and version are for reading."""
        (self.folder / "medias" / f"(Playfield) {FOLDER} - VR.png").write_bytes(b"\x89PNG")
        item = self._overrides()["playfield"][0]

        self.assertEqual(item["filename"], VR)
        self.assertEqual(item["file"], f"(Playfield) {FOLDER} - VR.png")

    def test_the_game_s_own_file_is_not_an_override_of_itself(self) -> None:
        """A file every table shares is the thing being overridden, not an override."""
        (self.folder / "medias" / f"(Playfield) {FOLDER}.png").write_bytes(b"\x89PNG")
        self.assertEqual(self._overrides(), {})


class DetailTests(PlacementTests):
    """One slot: the file that wins it, and every other file that could."""

    def _tiers(self, table: str = "") -> list[tuple[str, str, bool]]:
        at = f"/tables/{table}" if table else ""
        response = self.client.get(f"/games/{GAME_ID}{at}/media/playfield/detail")
        self.assertEqual(response.status_code, 200, response.text)
        return [(item["tier"], item["file"], item["wins"])
                for item in response.json()["tiers"]]

    def test_a_table_named_as_its_folder_is_served_the_game_s_file(self) -> None:
        """Its sibling is served the same file, so the two can only be told the same."""
        (self.folder / "medias" / f"(Playfield) {FOLDER}.png").write_bytes(b"\x89PNG")

        for table in ("tbl0000001", "tbl0000002"):
            with self.subTest(table=table):
                self.assertEqual(self._tiers(table),
                                 [("game", f"(Playfield) {FOLDER}.png", True),
                                  ("default", "table.png", False)])

    def test_a_table_with_a_name_of_its_own_lists_both_files(self) -> None:
        (self.folder / "medias" / f"(Playfield) {FOLDER}.png").write_bytes(b"\x89PNG")
        (self.folder / "medias" / f"(Playfield) {FOLDER} - VR.png").write_bytes(b"\x89PNG")

        self.assertEqual([tier for tier, _, _ in self._tiers("tbl0000002")],
                         ["table", "game", "default"])

    def test_what_takes_over_after_a_remove_is_what_the_resolver_picks_next(self) -> None:
        medias = self.folder / "medias"
        for kind, present, table, then in (
                ("playfield", (f"(Playfield) {FOLDER} - VR.png", f"(Playfield) {FOLDER}.png"),
                 "tbl0000002", f"(Playfield) {FOLDER}.png"),
                ("playfield", (f"(Playfield) {FOLDER} - VR.png",), "tbl0000002", "table.png"),
                ("playfield", (f"(Playfield) {FOLDER}.png",), "", "table.png"),
                ("playfield", (), "", None),
                ("wheel", (f"(Wheel) {FOLDER} - VR.png", f"(Logo) {FOLDER}.png"),
                 "tbl0000002", f"(Logo) {FOLDER}.png"),
                ("backglass", (f"(Backglass) {FOLDER} - VR.png",), "tbl0000002", None)):
            with self.subTest(kind=kind, present=present, table=table):
                for name in present:
                    (medias / name).write_bytes(b"\x89PNG")
                at = f"/tables/{table}" if table else ""
                response = self.client.get(f"/games/{GAME_ID}{at}/media/{kind}/detail")
                self.assertEqual(then, response.json().get("after_remove"))
                for name in present:
                    (medias / name).unlink()

    def test_each_file_is_shown_to_as_many_tables_from_every_lens_as_the_media_view_says(
            self) -> None:
        own = self.folder / "medias" / f"(Playfield) {FOLDER} - VR.png"
        own.write_bytes(b"\x89PNG")
        for shared in (True, False):
            game_file = self.folder / "medias" / f"(Playfield) {FOLDER}.png"
            if shared:
                game_file.write_bytes(b"\x89PNG")
            else:
                game_file.unlink()
            listing = self.client.get("/media", params={"game": GAME_ID,
                                                        "kind": "playfield"})
            counted = {row["file"]: row["serves"] for row in listing.json()["media"]}
            for table in ("", "tbl0000001", "tbl0000002"):
                with self.subTest(shared=shared, table=table):
                    at = f"/tables/{table}" if table else ""
                    tiers = self.client.get(
                        f"/games/{GAME_ID}{at}/media/playfield/detail").json()["tiers"]
                    self.assertEqual({item["file"]: counted[item["file"]] for item in tiers},
                                     {item["file"]: item.get("serves") for item in tiers})


class ListingTests(PlacementTests):
    def test_a_file_named_as_the_folder_is_one_row_serving_every_table(self) -> None:
        (self.folder / "medias" / f"(Playfield) {FOLDER}.png").write_bytes(b"\x89PNG")
        response = self.client.get("/media", params={"game": GAME_ID, "kind": "playfield"})
        self.assertEqual(response.status_code, 200, response.text)

        self.assertEqual([(row["table"], row["file"], row["via"], row["serves"])
                          for row in response.json()["media"]],
                         [("", f"(Playfield) {FOLDER}.png", "game", 2),
                          ("", "table.png", "unused", 0)])

    def test_a_file_named_for_one_table_is_a_row_serving_that_one(self) -> None:
        (self.folder / "medias" / f"(Playfield) {FOLDER} - VR.png").write_bytes(b"\x89PNG")
        response = self.client.get("/media", params={"game": GAME_ID, "kind": "playfield"})
        self.assertEqual(response.status_code, 200, response.text)

        self.assertEqual([(row["table"], row["file"], row["via"], row["serves"])
                          for row in response.json()["media"]],
                         [("", "table.png", "default", 1),
                          ("tbl0000002", f"(Playfield) {FOLDER} - VR.png", "table", 1)])


if __name__ == "__main__":
    unittest.main()
