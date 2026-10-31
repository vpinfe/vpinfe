"""Where a file-taking picker opens: the folder the last file of its kind came from."""

from __future__ import annotations

import asyncio
import sys
import unittest
from typing import Any
from unittest.mock import patch

from manager.kit import folder_picker
from manager.library import picker_memory


class _Store:
    def __init__(self) -> None:
        self.kept: dict[str, Any] = {}

    def __enter__(self) -> _Store:
        self._patches: list[Any] = [
            patch.object(picker_memory.remembered, "get",
                         lambda key, default=None: self.kept.get(key, default)),
            patch.object(picker_memory.remembered, "put",
                         lambda key, value: self.kept.__setitem__(key, value))]
        for one in self._patches:
            one.start()
        return self

    def __exit__(self, *_: object) -> None:
        for one in self._patches:
            one.stop()


class RememberingTests(unittest.TestCase):
    def test_a_taken_file_is_remembered_by_its_folder_for_its_kind_and_for_any(self) -> None:
        with _Store() as store:
            picker_memory.record("backglass", "/fetched/one/bg.directb2s")

            self.assertEqual(store.kept, {"picker.backglass": "/fetched/one",
                                          "picker.any": "/fetched/one"})

    def test_a_kind_opens_where_its_own_last_file_came_from(self) -> None:
        with _Store():
            picker_memory.record("backglass", "/one/bg.directb2s")
            picker_memory.record("rom", "/two/rom.zip")

            self.assertEqual(picker_memory.last("backglass"), "/one")

    def test_a_kind_with_none_opens_where_the_last_of_any_kind_came_from(self) -> None:
        with _Store():
            picker_memory.record("rom", "/two/rom.zip")

            self.assertEqual(picker_memory.last("wheel"), "/two")

    def test_nothing_taken_yet_remembers_nothing(self) -> None:
        with _Store():
            self.assertEqual(picker_memory.last("wheel"), "")

    def test_a_path_with_no_folder_above_it_is_not_remembered(self) -> None:
        with _Store() as store:
            picker_memory.record("wheel", "wheel.png")

            self.assertEqual(store.kept, {})

    def test_windows_paths_are_compared_without_regard_to_case(self) -> None:
        self.assertTrue(picker_memory.same(r"C:\Users\p\Downloads", r"c:\users\p\downloads\\"))

    @unittest.skipIf(sys.platform == "win32", "every path is compared without regard to case there")
    def test_other_paths_are_compared_with_regard_to_case(self) -> None:
        self.assertFalse(picker_memory.same("/Volumes/share/Downloads", "/volumes/share/downloads"))


class PlacesTests(unittest.TestCase):
    LISTED = [{"name": "/", "path": "/"}, {"name": "Home", "path": "/home/player"},
              {"name": "Downloads", "path": "/home/player/Downloads"},
              {"name": "Game Folder", "path": "/games/x", "source": "game"}]

    def names(self, listed: list[dict[str, Any]] | None = None) -> list[str]:
        return [one["name"] for one in
                picker_memory.places("backglass", "Backglasses", listed or self.LISTED)]

    def test_with_nothing_remembered_downloads_leads_the_rest(self) -> None:
        with _Store():
            self.assertEqual(self.names(), ["Downloads", "/", "Home", "Game Folder"])

    def test_the_last_used_folder_comes_first_named_for_the_kind(self) -> None:
        with _Store():
            picker_memory.record("backglass", "/elsewhere/bg.directb2s")

            self.assertEqual(self.names()[:2], ["Last Used for Backglasses", "Downloads"])

    def test_where_the_kind_has_none_the_last_of_any_kind_stands_in(self) -> None:
        with _Store():
            picker_memory.record("rom", "/elsewhere/rom.zip")

            self.assertEqual(self.names()[:2], ["Last Used", "Downloads"])

    def test_a_folder_that_is_also_another_place_is_listed_once_as_last_used(self) -> None:
        with _Store():
            picker_memory.record("backglass", "/home/player/Downloads/bg.directb2s")

            self.assertEqual(self.names(), ["Last Used for Backglasses", "/", "Home",
                                            "Game Folder"])

    def test_a_game_place_called_downloads_is_not_taken_for_the_downloads_folder(self) -> None:
        listed = [{"name": "Downloads", "path": "/games/y", "source": "game"},
                  {"name": "Downloads", "path": "/home/player/Downloads"}]
        with _Store():
            ordered = picker_memory.places("backglass", "Backglasses", listed)

        self.assertEqual([one["path"] for one in ordered], ["/home/player/Downloads", "/games/y"])


class _Listings:
    """A disk where only the named folders exist."""

    def __init__(self, *there: str) -> None:
        self.there = set(there)
        self.asked: list[str] = []

    def __call__(self, path: str, kind: str, suffixes: tuple[str, ...]) -> dict[str, Any]:
        self.asked.append(path)
        if path and path not in self.there:
            raise FileNotFoundError(path)
        return {"path": path or "/start"}


class GoneFolderTests(unittest.TestCase):
    def start(self, listing: _Listings, path: str, fallback: str = "") -> folder_picker.Start:
        async def io(function: Any, *args: Any) -> Any:
            return function(*args)

        with patch.object(folder_picker.offload, "io", io):
            return asyncio.run(folder_picker.read_start(
                listing, path, kind="file", nearest=True, fallback=fallback))

    def test_a_gone_folder_opens_at_the_closest_one_above_it(self) -> None:
        found = self.start(_Listings("/home", "/home/player"), "/home/player/gone/deeper")

        self.assertEqual(found.here["path"], "/home/player")
        self.assertIsNone(found.refused)

    def test_a_folder_on_a_drive_that_is_gone_does_not_open_at_the_volumes_folder(self) -> None:
        found = self.start(_Listings("/Volumes", "/games/x"), "/Volumes/Tables/a/b",
                           fallback="/games/x")

        self.assertEqual(found.here["path"], "/games/x")
        self.assertIsInstance(found.refused, FileNotFoundError)
        self.assertEqual(found.start, "/Volumes/Tables/a/b")

    def test_a_windows_drive_that_is_gone_does_not_open_at_its_letter(self) -> None:
        found = self.start(_Listings("E:\\", "C:\\games"), "E:\\Tables\\a", fallback="C:\\games")

        self.assertEqual(found.here["path"], "C:\\games")
        self.assertIsNotNone(found.refused)

    def test_without_a_fallback_a_disk_top_is_still_the_closest_folder(self) -> None:
        found = self.start(_Listings("C:\\"), "C:\\vPinball\\Tables")

        self.assertEqual(found.here["path"], "C:\\")
        self.assertIsNone(found.refused)

    def test_with_nowhere_to_fall_back_to_it_opens_at_the_kinds_own_start(self) -> None:
        found = self.start(_Listings("/media"), "/media/disk/a", fallback="/gone")

        self.assertEqual(found.here["path"], "/start")
        self.assertIsNotNone(found.refused)


if __name__ == "__main__":
    unittest.main()
