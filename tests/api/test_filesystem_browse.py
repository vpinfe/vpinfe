"""Browsing this machine for artwork, and the boundary that makes it safe to offer.

Reading directories over HTTP is a real capability on a device sitting on someone's
home network, so it is bounded rather than trusted: the game library, plus whatever
the owner listed, and nothing else. Everything here is about the edge of that - the
inside case is one assertion and the ways out are the rest of the file.
"""

from __future__ import annotations

import unittest
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from starlette.testclient import TestClient

import httpapi
from tests.support.library import TempTree, fake_game, write_game

GAME_ID = "Browse000001"
FOLDER = "Cactus Canyon (Bally 1998)"
INFO = {"Info": {"Name": "Cactus Canyon"}, "VPinFE": {"game_id": GAME_ID}}


class _Tree(TempTree):
    def setUp(self) -> None:
        super().setUp()
        self.folder = write_game(self.root, FOLDER, info=INFO, vpx=True,
                                 medias={"table.png": b"\x89PNGdefault"})
        (self.root / "loose art.png").write_bytes(b"\x89PNGloose")
        (self.root / "notes.vpx").write_bytes(b"vpx")

        # Outside the library entirely, standing in for anything else on the disk.
        self.outside = Path(self.enterContext(TemporaryDirectory()))
        (self.outside / "secret.png").write_bytes(b"\x89PNGsecret")

        game = fake_game(self.folder, FOLDER, meta=INFO)
        for target, value in (("common.games.game_repository.catalog", {GAME_ID: game}),):
            patcher = patch(target, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self._allow(str(self.root))
        self.client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)

    def _allow(self, *paths: str) -> None:
        """Stand in for the configured roots, which is the only thing that widens this."""
        roots = [{"path": str(Path(p).resolve()), "name": Path(p).name,
                  "source": "library"} for p in paths]
        patcher = patch("common.games.media_browse.roots", return_value=roots)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _entries(self, path: Path | str):
        return self.client.get("/filesystem/entries", params={"path": str(path)})


class BrowseTests(_Tree):
    def test_a_root_lists_its_folders_and_media_and_nothing_else(self) -> None:
        body = self._entries(self.root).json()
        names = [item["name"] for item in body["entries"]]

        self.assertIn(FOLDER, names)
        self.assertIn("loose art.png", names)
        self.assertNotIn("notes.vpx", names,
                         "a table file is not artwork and must not be offered as it")

    def test_a_listing_for_an_asset_kind_offers_that_kinds_files(self) -> None:
        (self.folder / "Cactus Canyon.directb2s").write_bytes(b"b2s")
        (self.folder / "Cactus Canyon.ini").write_bytes(b"ini")

        body = self.client.get("/filesystem/entries",
                               params={"path": str(self.folder), "kind": "backglass"}).json()
        names = [item["name"] for item in body["entries"]]

        self.assertIn("Cactus Canyon.directb2s", names)
        self.assertNotIn("Cactus Canyon.ini", names)
        self.assertNotIn(f"{FOLDER}.vpx", names)

    def test_a_kind_named_as_the_asset_lens_names_it_offers_every_kind_under_it(self) -> None:
        (self.folder / "afm.cRZ").write_bytes(b"serum")
        (self.folder / "afm.pal").write_bytes(b"vni")

        body = self.client.get("/filesystem/entries",
                               params={"path": str(self.folder), "kind": "alt_color"})
        names = [item["name"] for item in body.json()["entries"]]

        self.assertEqual(body.status_code, 200, body.text)
        self.assertIn("afm.cRZ", names)
        self.assertIn("afm.pal", names)

    def test_archives_are_listed_only_when_asked_for(self) -> None:
        (self.folder / "afm pup.zip").write_bytes(b"PK")

        def listed(**params: str) -> list[str]:
            body = self.client.get("/filesystem/entries",
                                   params={"path": str(self.folder), **params}).json()
            return [item["name"] for item in body["entries"]]

        self.assertNotIn("afm pup.zip", listed(kind="pup_pack"))
        self.assertIn("afm pup.zip", listed(kind="pup_pack", archives="true"))

    def test_without_a_kind_a_backglass_is_not_offered(self) -> None:
        (self.folder / "Cactus Canyon.directb2s").write_bytes(b"b2s")

        names = [item["name"] for item in self._entries(self.folder).json()["entries"]]

        self.assertNotIn("Cactus Canyon.directb2s", names)

    def test_a_kind_nothing_declares_is_refused(self) -> None:
        response = self.client.get("/filesystem/entries",
                                   params={"path": str(self.folder), "kind": "nonsense"})

        self.assertEqual(response.status_code, 400)

    def test_a_root_has_no_parent_so_up_stops_there(self) -> None:
        self.assertIsNone(self._entries(self.root).json()["parent"])
        self.assertIsNotNone(self._entries(self.folder).json()["parent"],
                             "inside a root there is somewhere to go back to")

    def test_a_folder_outside_every_root_is_refused(self) -> None:
        self.assertEqual(self._entries(self.outside).status_code, 400)

    def test_walking_out_with_dots_is_refused(self) -> None:
        """Resolved before it is checked, so this is the same question as a symlink."""
        self.assertEqual(self._entries(f"{self.root}/../outside").status_code, 400)

    def test_a_link_leaving_the_roots_is_not_even_listed(self) -> None:
        """Listing it would offer something the import refuses - a menu item that
        cannot work, on a name its author chose to look like artwork."""
        try:
            (Path(self.root) / "sleeve.png").symlink_to(self.outside / "secret.png")
        except (OSError, NotImplementedError):
            self.skipTest("this platform will not make a symlink")

        names = [item["name"] for item in self._entries(self.root).json()["entries"]]
        self.assertNotIn("sleeve.png", names)

    def test_a_link_that_stays_inside_is_offered(self) -> None:
        """The rule is where it points, not that it is a link."""
        try:
            (Path(self.root) / "alias.png").symlink_to(self.root / "loose art.png")
        except (OSError, NotImplementedError):
            self.skipTest("this platform will not make a symlink")

        names = [item["name"] for item in self._entries(self.root).json()["entries"]]
        self.assertIn("alias.png", names)


class RootTests(TempTree):
    """What makes a folder browsable, which is the whole of the boundary."""

    @contextmanager
    def _browsing(self, library: tuple[str, ...]):
        from common.games import media_browse as filesystem
        from common.games.library_folders import Location

        held = [Location(f"l{index}", path) for index, path in enumerate(library)]
        with patch("common.games.library_folders.configured", return_value=held):
            yield filesystem

    def _roots(self, library: tuple[str, ...]):
        with self._browsing(library) as filesystem:
            return filesystem.roots()

    def test_the_library_is_browsable_without_being_configured(self) -> None:
        found = self._roots((str(self.root),))
        self.assertEqual([item["source"] for item in found], ["library"])

    def test_every_library_folder_is_browsable(self) -> None:
        second = Path(self.enterContext(TemporaryDirectory()))
        found = self._roots((str(self.root), str(second)))

        self.assertEqual([item["source"] for item in found], ["library", "library"])
        self.assertIn(str(second.resolve()), [item["path"] for item in found])

    def test_a_file_in_a_second_library_folder_is_inside_the_bound(self) -> None:
        second = Path(self.enterContext(TemporaryDirectory()))
        (second / "art.png").write_bytes(b"png")

        with self._browsing((str(self.root), str(second))) as filesystem:
            self.assertEqual(filesystem.within_roots(str(second / "art.png")),
                             (second / "art.png").resolve())

    def test_no_library_means_nothing_browsable(self) -> None:
        """Not an error, and not a reason to fall back to the whole disk."""
        self.assertEqual(self._roots(()), [])


class ImportTests(_Tree):
    def _import(self, path: Path | str, kind: str = "playfield"):
        return self.client.post(f"/games/{GAME_ID}/media/{kind}/import",
                                json={"path": str(path)})

    def test_a_file_under_a_root_lands_under_the_slots_name(self) -> None:
        response = self._import(self.root / "loose art.png")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["written"], f"(Playfield) {FOLDER}.png")

    def test_the_original_is_left_where_it_was(self) -> None:
        self._import(self.root / "loose art.png")
        self.assertTrue((Path(self.root) / "loose art.png").is_file())

    def test_a_file_outside_every_root_is_refused(self) -> None:
        self.assertEqual(self._import(self.outside / "secret.png").status_code, 400)

    def test_an_absolute_path_out_of_the_roots_is_refused(self) -> None:
        for attempt in ("/etc/hosts", f"{self.root}/../outside/secret.png"):
            with self.subTest(attempt=attempt):
                self.assertEqual(self._import(attempt).status_code, 400)


if __name__ == "__main__":
    unittest.main()
