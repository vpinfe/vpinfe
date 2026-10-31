"""Browsing this machine for a folder, over the wire."""

from __future__ import annotations

import contextlib
import os
import sys
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import httpapi
from common import service_errors
from common.media import folder_browse
from httpapi import auth
from tests.support.library import TempTree

# One non-dot private folder per platform, to prove a parent listing omits a private
# child that dot-folder filtering alone would not catch.
_NON_DOT_PRIVATE = {
    "darwin": ("Library", "Keychains"),
    "linux": (".config", "google-chrome"),
    "win32": ("AppData/Roaming/Microsoft", "Credentials"),
}


def _client() -> TestClient:
    return TestClient(httpapi.create_api_app(), raise_server_exceptions=False)


class FolderApiTests(TempTree):
    def setUp(self) -> None:
        super().setUp()
        self.client = _client()

    def _get(self, path: str = "", *, kind: str = "", suffix: list[str] | None = None,
             hidden: bool = False):
        params: dict[str, str | list[str]] = {}
        if path:
            params["path"] = path
        if kind:
            params["kind"] = kind
        if suffix:
            params["suffix"] = suffix
        if hidden:
            params["hidden"] = "true"
        return self.client.get("/folders", params=params)

    def test_only_subfolders_are_listed_sorted_by_name(self) -> None:
        (self.root / "bravo").mkdir()
        (self.root / "Alpha").mkdir()
        (self.root / ".hidden").mkdir()
        (self.root / "a file.txt").write_text("not a folder")

        body = self._get(str(self.root)).json()

        self.assertEqual([f["name"] for f in body["folders"]], ["Alpha", "bravo"])

    def test_a_subfolders_parent_is_its_parent(self) -> None:
        child = self.root / "child"
        child.mkdir()

        body = self._get(str(child)).json()

        self.assertEqual(body["parent"], str(self.root))

    def test_a_missing_path_is_not_found(self) -> None:
        response = self._get(str(self.root / "nowhere"))

        self.assertEqual(response.status_code, 404)

    def test_an_empty_path_answers_the_home_folder(self) -> None:
        with patch("common.media.folder_browse.Path.home", return_value=self.root):
            body = self._get("").json()

        self.assertEqual(body["path"], str(self.root))

    def test_an_empty_path_opens_at_the_top_of_the_disk_where_home_is_not_a_folder(self) -> None:
        nowhere = self.root / "no-such-home"

        with patch("common.media.folder_browse.Path.home", return_value=nowhere):
            response = self._get("")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["path"], nowhere.anchor)

    def test_a_home_that_is_not_a_folder_is_not_offered_as_a_place(self) -> None:
        nowhere = self.root / "no-such-home"

        with patch("common.media.folder_browse.Path.home", return_value=nowhere):
            for kind in ("dir", "file", "exe"):
                with self.subTest(kind=kind):
                    paths = [one["path"] for one in self._get("", kind=kind).json()["roots"]]
                    self.assertNotIn(str(nowhere), paths)
                    self.assertTrue(paths)

    def test_an_account_with_no_home_at_all_still_opens_somewhere(self) -> None:
        for kind in ("dir", "file", "exe"):
            with self.subTest(kind=kind), \
                    patch("common.media.folder_browse.Path.home",
                          side_effect=RuntimeError("no home")):
                response = self._get("", kind=kind)

                self.assertEqual(response.status_code, 200, response.text)
                self.assertTrue(response.json()["roots"])

    def test_a_home_that_is_a_folder_is_still_where_an_empty_path_opens(self) -> None:
        for kind in ("dir", "file"):
            with self.subTest(kind=kind), \
                    patch("common.media.folder_browse.Path.home", return_value=self.root):
                body = self._get("", kind=kind).json()

                self.assertEqual(body["path"], str(self.root))
                self.assertIn(str(self.root), [one["path"] for one in body["roots"]])

    def test_roots_is_never_empty(self) -> None:
        body = self._get(str(self.root)).json()

        self.assertTrue(body["roots"])

    def test_the_top_of_the_disk_has_no_parent(self) -> None:
        body = self._get(self.root.resolve().anchor).json()

        self.assertEqual(body["parent"], "")

    def test_a_listing_does_not_carry_the_folders_above_it(self) -> None:
        self.assertNotIn("above", self._get(str(self.root)).json())

    def test_home_is_named_home_among_the_places(self) -> None:
        with patch("common.media.folder_browse.Path.home", return_value=self.root):
            body = self._get(str(self.root)).json()

        names = {one["path"]: one["name"] for one in body["roots"]}
        self.assertEqual(names[str(self.root)], "Home")

    def test_downloads_is_a_place_where_the_account_has_one(self) -> None:
        home = self.root / "home"
        (home / "Downloads").mkdir(parents=True)

        with patch("common.media.folder_browse.Path.home", return_value=home), \
                patch("common.media.folder_browse.sys.platform", "darwin"):
            names = {one["path"]: one["name"] for one in folder_browse._roots("file")}

        self.assertEqual(names[str(home / "Downloads")], "Downloads")

    def test_downloads_is_not_offered_where_the_folder_is_not_there(self) -> None:
        home = self.root / "home"
        home.mkdir()

        with patch("common.media.folder_browse.Path.home", return_value=home), \
                patch("common.media.folder_browse.sys.platform", "darwin"):
            self.assertIsNone(folder_browse.downloads_folder())

    def test_downloads_is_not_offered_without_a_home_folder(self) -> None:
        with patch("common.media.folder_browse.Path.home", side_effect=RuntimeError("no home")):
            self.assertIsNone(folder_browse.downloads_folder())

    def test_linux_downloads_that_is_the_home_folder_is_not_offered(self) -> None:
        home = self.root / "home"
        home.mkdir()

        with patch("common.media.folder_browse.Path.home", return_value=home), \
                patch("common.media.folder_browse.sys.platform", "linux"), \
                patch.dict(os.environ, {"XDG_DOWNLOAD_DIR": str(home)}):
            self.assertIsNone(folder_browse.downloads_folder())

    def test_linux_downloads_follows_xdg_before_the_default_folder(self) -> None:
        home = self.root / "home"
        (home / "Downloads").mkdir(parents=True)
        (home / "Fetched").mkdir()

        with patch("common.media.folder_browse.Path.home", return_value=home), \
                patch("common.media.folder_browse.sys.platform", "linux"), \
                patch.dict(os.environ, {"XDG_DOWNLOAD_DIR": str(home / "Fetched")}):
            self.assertEqual(folder_browse.downloads_folder(), home / "Fetched")

    def test_linux_downloads_reads_the_user_dirs_file(self) -> None:
        home = self.root / "home"
        (home / ".config").mkdir(parents=True)
        (home / "Incoming").mkdir()
        (home / ".config" / "user-dirs.dirs").write_text(
            'XDG_DOWNLOAD_DIR="$HOME/Incoming"\n', encoding="utf-8")
        env = {key: value for key, value in os.environ.items()
               if key not in ("XDG_DOWNLOAD_DIR", "XDG_CONFIG_HOME")}

        with patch("common.media.folder_browse.Path.home", return_value=home), \
                patch("common.media.folder_browse.sys.platform", "linux"), \
                patch.dict(os.environ, env, clear=True):
            self.assertEqual(folder_browse.downloads_folder(), home / "Incoming")

    def test_windows_downloads_is_the_known_folder(self) -> None:
        home = self.root / "home"
        home.mkdir()
        moved = self.root / "D-downloads"
        moved.mkdir()

        with patch("common.media.folder_browse.Path.home", return_value=home), \
                patch("common.media.folder_browse.sys.platform", "win32"), \
                patch("common.media.folder_browse._windows_downloads", return_value=moved):
            self.assertEqual(folder_browse.downloads_folder(), moved)

    def test_a_program_pickers_places_name_home_too(self) -> None:
        with patch("common.media.folder_browse.Path.home", return_value=self.root):
            body = self._get(str(self.root), kind="exe").json()

        names = {one["path"]: one["name"] for one in body["roots"]}
        self.assertEqual(names[str(self.root)], "Home")

    def test_places_of_one_name_are_told_apart_by_their_paths(self) -> None:
        home = self.root / "home"
        places = [{"name": "Applications", "path": "/Applications"},
                  {"name": "Applications", "path": str(home / "Applications")},
                  {"name": "bin", "path": "/usr/local/bin"},
                  {"name": "Home", "path": str(home)}]

        with patch("common.media.folder_browse.Path.home", return_value=home), \
                patch("common.media.folder_browse._program_roots", return_value=places):
            shown = folder_browse._roots("exe")

        mine = "~/Applications" if sys.platform != "win32" else str(home / "Applications")
        self.assertEqual([one["name"] for one in shown],
                         ["/Applications", mine, "bin", "Home"])

    def test_the_places_a_listing_offers_are_each_there_once(self) -> None:
        for kind in ("dir", "exe"):
            paths = [one["path"] for one in self._get(str(self.root), kind=kind).json()["roots"]]
            self.assertEqual(len(paths), len(set(paths)))

    def test_a_network_caller_is_refused(self) -> None:
        with patch("httpapi.folders.caller_is_local", return_value=False):
            response = self._get(str(self.root))

        self.assertEqual(response.status_code, 403)

    def test_a_private_folder_cannot_be_listed(self) -> None:
        (self.root / ".ssh").mkdir()

        with patch("common.media.folder_browse.Path.home", return_value=self.root):
            response = self._get(str(self.root / ".ssh"))

        self.assertEqual(response.status_code, 400)

    def test_a_private_folder_is_omitted_from_its_parent(self) -> None:
        case = _NON_DOT_PRIVATE.get(sys.platform)
        if case is None:
            self.skipTest(f"no non-dot private-folder case recorded for {sys.platform}")
        parent_rel, child_name = case
        parent = self.root / parent_rel
        parent.mkdir(parents=True)
        (parent / child_name).mkdir()
        (parent / "visible").mkdir()

        with patch("common.media.folder_browse.Path.home", return_value=self.root):
            body = self._get(str(parent)).json()

        self.assertEqual([f["name"] for f in body["folders"]], ["visible"])

    def test_kind_exe_lists_an_executable_and_omits_a_non_executable_file(self) -> None:
        if sys.platform == "win32":
            self.skipTest("Windows decides this by PATHEXT, not os.access")
        runnable = self.root / "runme"
        runnable.write_text("#!/bin/sh\n")
        runnable.chmod(0o755)
        (self.root / "notes.txt").write_text("not executable")

        body = self._get(str(self.root), kind="exe").json()

        self.assertEqual([f["name"] for f in body["files"]], ["runme"])

    def test_kind_file_with_a_suffix_lists_only_that_suffix(self) -> None:
        (self.root / "a.ini").write_text("")
        (self.root / "b.txt").write_text("")

        body = self._get(str(self.root), kind="file", suffix=[".ini"]).json()

        self.assertEqual([f["name"] for f in body["files"]], ["a.ini"])

    def test_kind_file_with_no_suffix_lists_every_file(self) -> None:
        (self.root / "a.ini").write_text("")
        (self.root / "b.txt").write_text("")

        body = self._get(str(self.root), kind="file").json()

        self.assertEqual(sorted(f["name"] for f in body["files"]), ["a.ini", "b.txt"])

    def test_hidden_files_are_omitted(self) -> None:
        (self.root / ".hidden").write_text("")
        (self.root / "visible.txt").write_text("")

        body = self._get(str(self.root), kind="file").json()

        self.assertEqual([f["name"] for f in body["files"]], ["visible.txt"])

    def test_hidden_lists_dot_named_folders_and_files(self) -> None:
        (self.root / ".local").mkdir()
        (self.root / ".hidden").write_text("")
        (self.root / "visible.txt").write_text("")

        body = self._get(str(self.root), kind="file", hidden=True).json()

        self.assertEqual([f["name"] for f in body["folders"]], [".local"])
        self.assertEqual([f["name"] for f in body["files"]], [".hidden", "visible.txt"])

    def test_hidden_still_omits_a_private_folder(self) -> None:
        (self.root / ".ssh").mkdir()
        (self.root / ".local").mkdir()

        with patch("common.media.folder_browse.Path.home", return_value=self.root):
            body = self._get(str(self.root), hidden=True).json()

        self.assertEqual([f["name"] for f in body["folders"]], [".local"])

    def test_a_private_folders_files_are_never_listed(self) -> None:
        private = self.root / ".ssh"
        private.mkdir()
        (private / "id_ed25519").write_text("not a real key")

        with patch("common.media.folder_browse.Path.home", return_value=self.root):
            response = self._get(str(private), kind="file")

        self.assertEqual(response.status_code, 400)

    def test_a_file_path_lists_its_folder(self) -> None:
        child = self.root / "child"
        child.mkdir()
        (child / "table.vpx").write_text("")

        body = self._get(str(child / "table.vpx"), kind="file").json()

        self.assertEqual(body["path"], str(child))
        self.assertEqual([f["name"] for f in body["files"]], ["table.vpx"])

    def test_kind_exe_with_an_empty_path_starts_at_one_of_its_roots(self) -> None:
        body = self._get(kind="exe").json()

        self.assertIn(body["path"], [root["path"] for root in body["roots"]])

    def test_an_unknown_kind_is_refused(self) -> None:
        response = self._get(str(self.root), kind="folder")

        self.assertEqual(response.status_code, 400)

    def test_a_network_caller_is_refused_for_any_kind(self) -> None:
        with patch("httpapi.folders.caller_is_local", return_value=False):
            response = self._get(str(self.root), kind="exe")

        self.assertEqual(response.status_code, 403)

    def test_an_app_bundle_lands_in_files_under_exe(self) -> None:
        if sys.platform != "darwin":
            self.skipTest("`.app` bundles are a macOS thing")
        (self.root / "Pinball.app" / "Contents" / "MacOS").mkdir(parents=True)

        body = self._get(str(self.root), kind="exe").json()

        self.assertEqual([f["name"] for f in body["folders"]], [])
        self.assertEqual([f["name"] for f in body["files"]], ["Pinball.app"])


class MakeFolderApiTests(TempTree):
    def setUp(self) -> None:
        super().setUp()
        self.client = _client()

    def _post(self, parent: str, name: str):
        return self.client.post("/folders", json={"parent": parent, "name": name})

    def test_the_folder_is_made_and_its_own_listing_comes_back(self) -> None:
        response = self._post(str(self.root), "Games")

        self.assertEqual(response.status_code, 200)
        self.assertTrue((self.root / "Games").is_dir())
        self.assertEqual(response.json()["path"], str(self.root / "Games"))
        self.assertEqual(response.json()["parent"], str(self.root))
        self.assertEqual(response.json()["folders"], [])

    def test_the_name_is_trimmed(self) -> None:
        self._post(str(self.root), "  Games  ")

        self.assertTrue((self.root / "Games").is_dir())

    def test_a_name_is_not_a_path(self) -> None:
        """A separator would make folders the picker never showed."""
        for name in ("a/b", "a\\b", "..", "."):
            with self.subTest(name=name):
                self.assertEqual(self._post(str(self.root), name).status_code, 400)

        self.assertEqual(list(self.root.iterdir()), [])

    def test_a_name_is_needed(self) -> None:
        self.assertEqual(self._post(str(self.root), "   ").status_code, 400)

    def test_a_name_with_a_control_character_is_refused_rather_than_failing(self) -> None:
        """A NUL reaches the filesystem call as a ValueError, which is a 500."""
        for name in ("a\x00b", "a\nb", "a\tb"):
            with self.subTest(name=repr(name)):
                self.assertEqual(self._post(str(self.root), name).status_code, 400)

        self.assertEqual(list(self.root.iterdir()), [])

    def test_a_parent_is_needed_or_it_would_be_made_where_the_server_runs(self) -> None:
        with contextlib.chdir(self.root):
            for parent in ("", "   "):
                with self.subTest(parent=parent):
                    self.assertEqual(self._post(parent, "Games").status_code, 400)

        self.assertEqual(list(self.root.iterdir()), [])

    def test_a_folder_already_there_is_never_reused(self) -> None:
        (self.root / "Games").mkdir()
        (self.root / "Games" / "kept.txt").write_text("mine")

        response = self._post(str(self.root), "Games")

        self.assertEqual(response.status_code, 400)
        self.assertTrue((self.root / "Games" / "kept.txt").exists())

    def test_a_folder_to_make_it_in_must_be_there(self) -> None:
        self.assertEqual(self._post(str(self.root / "nowhere"), "Games").status_code, 404)

    def test_a_private_folder_is_not_written_to(self) -> None:
        (self.root / ".ssh").mkdir()

        with patch("common.media.folder_browse.Path.home", return_value=self.root):
            response = self._post(str(self.root / ".ssh"), "Games")

        self.assertEqual(response.status_code, 400)
        self.assertFalse((self.root / ".ssh" / "Games").exists())

    def test_a_network_caller_is_refused_and_nothing_is_made(self) -> None:
        with patch("httpapi.folders.caller_is_local", return_value=False):
            response = self._post(str(self.root), "Games")

        self.assertEqual(response.status_code, 403)
        self.assertFalse((self.root / "Games").exists())

    def test_it_takes_the_scope_adding_a_library_folder_takes(self) -> None:
        """Making a folder is writing to the disk for the same reason adding one is."""
        scopes = {(route.methods and sorted(route.methods)[0], path): auth.route_scope(route)
                  for path, route in auth.iter_api_routes(httpapi.create_api_app())}

        self.assertEqual(scopes[("POST", "/folders")],
                         scopes[("PUT", "/config/{list_key}/{item_id}")])


class FileApiTests(TempTree):
    """`GET /folders/file`: a picture, clip or sound anywhere on the disk, to this device."""

    def setUp(self) -> None:
        super().setUp()
        self.client = _client()
        self.cover = self.root / "cover.png"
        self.cover.write_bytes(b"png bytes")

    def _get(self, path: object):
        return self.client.get("/folders/file", params={"path": str(path)})

    def test_media_outside_every_library_root_is_served(self) -> None:
        response = self._get(self.cover)

        self.assertEqual((response.status_code, response.content), (200, b"png bytes"))

    def test_an_image_a_video_and_a_sound_are_served_in_any_case(self) -> None:
        for name in ("clip.mp4", "song.MP3", "wheel.WebP", "loop.ogg"):
            with self.subTest(name=name):
                (self.root / name).write_bytes(b"x")
                self.assertEqual(self._get(self.root / name).status_code, 200)

    def test_a_file_that_is_not_media_is_refused(self) -> None:
        for name in ("table.vpx", "notes.txt", "view.pov", "backglass.directb2s", "noext"):
            with self.subTest(name=name):
                (self.root / name).write_text("not media")
                self.assertEqual(self._get(self.root / name).status_code, 400)

    def test_a_network_caller_is_refused(self) -> None:
        with patch("httpapi.folders.caller_is_local", return_value=False):
            response = self._get(self.cover)

        self.assertEqual(response.status_code, 403)

    def test_a_file_in_a_private_folder_is_refused_even_when_it_looks_like_media(self) -> None:
        private = self.root / ".ssh"
        private.mkdir()
        (private / "key.png").write_bytes(b"not a picture")

        with patch("common.media.folder_browse.Path.home", return_value=self.root):
            response = self._get(private / "key.png")

        self.assertEqual(response.status_code, 400)

    @unittest.skipUnless(sys.platform == "darwin", "the volumes that ignore case by default")
    def test_a_private_folder_is_refused_under_another_spelling_of_its_name(self) -> None:
        private = self.root / ".ssh"
        private.mkdir()
        (private / "key.png").write_bytes(b"not a picture")
        variant = self.root / ".SSH" / "key.png"
        if not variant.exists():
            self.skipTest("this volume tells .SSH from .ssh")

        with patch("common.media.folder_browse.Path.home", return_value=self.root):
            response = self._get(variant)

        self.assertEqual(response.status_code, 400)

    def test_a_link_is_judged_as_what_it_points_at(self) -> None:
        private = self.root / ".ssh"
        private.mkdir()
        (private / "key.png").write_bytes(b"not a picture")
        (self.root / "document.txt").write_text("not media")
        try:
            (self.root / "to_key.png").symlink_to(private / "key.png")
            (self.root / "to_document.png").symlink_to(self.root / "document.txt")
        except OSError:
            self.skipTest("symlinks need a privilege here")

        with patch("common.media.folder_browse.Path.home", return_value=self.root):
            responses = {name: self._get(self.root / name).status_code
                         for name in ("to_key.png", "to_document.png")}

        self.assertEqual(responses, {"to_key.png": 400, "to_document.png": 400})

    def test_a_link_to_media_is_served(self) -> None:
        try:
            (self.root / "alias.png").symlink_to(self.cover)
        except OSError:
            self.skipTest("symlinks need a privilege here")

        self.assertEqual(self._get(self.root / "alias.png").content, b"png bytes")

    def test_a_missing_file_and_a_folder_are_not_found(self) -> None:
        (self.root / "folder.png").mkdir()

        self.assertEqual([self._get(self.root / "nowhere.png").status_code,
                          self._get(self.root / "folder.png").status_code], [404, 404])

    def test_a_path_with_a_null_byte_is_refused_rather_than_failing(self) -> None:
        self.assertEqual(self._get(str(self.cover) + "\x00.png").status_code, 400)

    def test_it_takes_the_scope_listing_the_disk_takes(self) -> None:
        scopes = {(route.methods and sorted(route.methods)[0], path): auth.route_scope(route)
                  for path, route in auth.iter_api_routes(httpapi.create_api_app())}

        self.assertEqual(scopes[("GET", "/folders/file")], scopes[("GET", "/folders")])
        self.assertEqual(scopes[("GET", "/folders/file")], "config:write")


class FileOfKindTests(TempTree):
    """What the Manager reads on this machine before it sends the bytes."""

    def test_a_file_with_one_of_the_extensions_is_answered_resolved(self) -> None:
        (self.root / "View.POV").write_bytes(b"pov")

        found = folder_browse.file_of_kind(str(self.root / "View.POV"), (".pov",))

        self.assertEqual(found, (self.root / "View.POV").resolve())

    def test_another_extension_is_refused(self) -> None:
        (self.root / "view.png").write_bytes(b"png")

        with self.assertRaises(service_errors.RefusedError):
            folder_browse.file_of_kind(str(self.root / "view.png"), (".pov",))

    def test_a_private_folder_is_refused_whatever_the_extension(self) -> None:
        private = self.root / ".aws"
        private.mkdir()
        (private / "credentials.ini").write_text("secret")

        with patch("common.media.folder_browse.Path.home", return_value=self.root), \
                self.assertRaises(service_errors.RefusedError):
            folder_browse.file_of_kind(str(private / "credentials.ini"), (".ini",))

    def test_a_link_into_a_private_folder_is_refused(self) -> None:
        private = self.root / ".aws"
        private.mkdir()
        (private / "credentials.ini").write_text("secret")
        try:
            (self.root / "tweaks.ini").symlink_to(private / "credentials.ini")
        except OSError:
            self.skipTest("symlinks need a privilege here")

        with patch("common.media.folder_browse.Path.home", return_value=self.root), \
                self.assertRaises(service_errors.RefusedError):
            folder_browse.file_of_kind(str(self.root / "tweaks.ini"), (".ini",))

    def test_a_missing_file_is_not_found(self) -> None:
        with self.assertRaises(service_errors.NotFoundError):
            folder_browse.file_of_kind(str(self.root / "nowhere.ini"), (".ini",))


if __name__ == "__main__":
    unittest.main()
