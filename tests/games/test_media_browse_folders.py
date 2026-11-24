"""Where browsing may start is asked of the disk once, for every folder at the same time, so
a share that is down costs one short wait and is left out rather than hanging the picker.
"""

from __future__ import annotations

from pathlib import Path
from unittest import mock

from common import service_errors
from common.config import path_state
from common.games import library_folders, media_browse
from tests.support.hung_folder import never_answering
from tests.support.library import TempTree


class _Browse(TempTree):
    def setUp(self) -> None:
        super().setUp()
        self.up = self.root / "up"
        self.up.mkdir()
        (self.up / "art.png").write_bytes(b"png")
        self.down = self.root / "down"
        self.down.mkdir()
        held = [library_folders.Location("l1", str(self.up)),
                library_folders.Location("l2", str(self.down))]
        for patcher in (
                mock.patch.object(library_folders, "configured", return_value=held),
                mock.patch.object(path_state, "PROBE_SECONDS", 0.2)):
            patcher.start()
            self.addCleanup(patcher.stop)


class BrowseRootsTests(_Browse):
    def test_every_folder_that_answers_is_offered(self) -> None:
        paths = [one["path"] for one in media_browse.roots()]

        self.assertEqual(paths, [str(self.up.resolve()), str(self.down.resolve())])

    def test_a_folder_that_does_not_answer_is_left_out(self) -> None:
        with never_answering(str(self.down)):
            paths = [one["path"] for one in media_browse.roots()]

        self.assertEqual(paths, [str(self.up.resolve())])

    def test_a_folder_that_is_a_file_is_left_out(self) -> None:
        (self.root / "file").write_text("x", encoding="utf-8")
        held = [library_folders.Location("l3", str(self.root / "file"))]
        with mock.patch.object(library_folders, "configured", return_value=held):
            self.assertEqual(media_browse.roots(), [])


class BrowseEntriesTests(_Browse):
    def test_a_listing_names_what_the_folder_holds(self) -> None:
        listed = media_browse.entries(str(self.up))

        self.assertEqual([one["name"] for one in listed["entries"]], ["art.png"])
        self.assertIsNone(listed["parent"])

    def test_a_folder_that_stops_answering_is_blocked_not_hung(self) -> None:
        inside = Path(self.up) / "sub"
        inside.mkdir()
        with never_answering(str(inside.resolve())), \
                self.assertRaises(service_errors.BlockedError):
            media_browse.entries(str(inside))

    def test_a_file_is_not_a_folder(self) -> None:
        with self.assertRaises(service_errors.RefusedError):
            media_browse.entries(str(self.up / "art.png"))
