"""Protects: (c) a large import is refused in plain words before it starts when the drive beside
the library has no room, and never works in the system temp folder.
"""

from __future__ import annotations

import errno
import io
import os
import time
import unittest
from collections import namedtuple
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from fastapi.testclient import TestClient

import httpapi
import tests
from common import service_errors
from common.games import library_folders
from common.uploads import upload_session_service, working_folder
from tests.support.library import own_locations

Usage = namedtuple("Usage", "total used free")
GB = 1024 ** 3


class WorkingFolderTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.library = self.root / "library"
        self.library.mkdir()
        self.store = own_locations(self, self.root / "config")
        # The suite answers for the folder everywhere else; this is the real answer.
        where = mock.patch.object(working_folder, "_where", tests.REAL_WORKING_WHERE)
        where.start()
        self.addCleanup(where.stop)

    def hold_library(self) -> None:
        self.store.put(library_folders.Location(library_folders.mint_location_id(),
                                                str(self.library)))

    def free(self, free: int):
        return mock.patch("common.uploads.working_folder.shutil.disk_usage",
                          return_value=Usage(100 * GB, 100 * GB - free, free))

    def test_a_session_works_beside_the_library(self) -> None:
        self.hold_library()
        session = upload_session_service.begin_session()
        self.addCleanup(upload_session_service.cleanup_session, session.upload_id)
        self.assertEqual(Path(session.directory).parent,
                         self.library / working_folder.NAME)

    def test_the_library_scan_does_not_see_the_working_folder(self) -> None:
        self.hold_library()
        working_folder.root()
        (self.library / "A Game").mkdir()
        listed = [d.name for d in self.library.iterdir()
                  if d.is_dir() and not d.name.startswith(".")]
        self.assertEqual(listed, ["A Game"])

    def test_no_room_refuses_before_a_session_exists(self) -> None:
        self.hold_library()
        with self.free(GB), self.assertRaises(service_errors.BlockedError) as caught:
            upload_session_service.begin_session(expected_bytes=3 * GB)
        said = str(caught.exception)
        self.assertIn("Not enough room", said)
        self.assertIn("1.0 GB", said)
        self.assertEqual(list((self.library / working_folder.NAME).iterdir()), [])

    def test_room_for_it_begins(self) -> None:
        self.hold_library()
        with self.free(10 * GB):
            session = upload_session_service.begin_session(expected_bytes=3 * GB)
        self.addCleanup(upload_session_service.cleanup_session, session.upload_id)

    def test_a_drive_that_fills_while_writing_is_said_plainly(self) -> None:
        self.hold_library()
        session = upload_session_service.begin_session()
        self.addCleanup(upload_session_service.cleanup_session, session.upload_id)

        class Full(io.BytesIO):
            def read(self, size: int | None = -1) -> bytes:
                raise OSError(errno.ENOSPC, "No space left on device")

        with self.assertRaises(service_errors.BlockedError) as caught:
            upload_session_service.store_file(session.upload_id, "pack.zip", Full())
        self.assertIn("is full", str(caught.exception))

    def test_a_file_that_stopped_part_way_is_not_kept(self) -> None:
        self.hold_library()
        session = upload_session_service.begin_session()
        self.addCleanup(upload_session_service.cleanup_session, session.upload_id)

        class Cut(io.BytesIO):
            sent = False

            def read(self, size: int | None = -1) -> bytes:
                if self.sent:
                    raise OSError(errno.ENOSPC, "No space left on device")
                self.sent = True
                return b"x" * 1000

        with self.assertRaises(service_errors.BlockedError):
            upload_session_service.store_file(session.upload_id, "pack.zip", Cut())
        self.assertEqual(upload_session_service.finish_session(session.upload_id),
                         {"file_count": 0, "total_bytes": 0})

    def test_a_session_over_a_folder_already_here_needs_no_working_folder(self) -> None:
        here = self.root / "already-here"
        here.mkdir()
        session = upload_session_service.begin_session(source=here)
        self.addCleanup(upload_session_service.cleanup_session, session.upload_id)
        self.assertTrue(here.is_dir())
        upload_session_service.cleanup_session(session.upload_id)
        self.assertTrue(here.is_dir())

    def test_a_file_streamed_to_the_api_lands_beside_the_library(self) -> None:
        self.hold_library()
        client = TestClient(httpapi.create_api_app())
        begun = client.post("/uploads", json={"bytes": 5}).json()
        self.addCleanup(upload_session_service.cleanup_session, begun["id"])
        sent = client.put(f"/uploads/{begun['id']}/file?relpath=dir/pack.zip",
                          content=b"hello")
        self.assertEqual(sent.json()["bytes"], 5)
        stored = Path(upload_session_service.get_session_dir(begun["id"]))
        self.assertEqual((stored / "dir" / "pack.zip").read_bytes(), b"hello")
        self.assertEqual(stored.parent, self.library / working_folder.NAME)

    def test_the_api_refuses_a_drop_there_is_no_room_for(self) -> None:
        self.hold_library()
        client = TestClient(httpapi.create_api_app())
        with self.free(GB):
            refused = client.post("/uploads", json={"bytes": 3 * GB})
        self.assertEqual(refused.status_code, 409)
        self.assertIn("Not enough room", refused.text)

    def test_with_nowhere_to_write_it_refuses_rather_than_using_the_system_temp(self) -> None:
        with self.assertRaises(service_errors.BlockedError):
            upload_session_service.begin_session()

    def test_what_a_cut_off_import_left_is_cleared_but_not_what_is_in_use(self) -> None:
        self.hold_library()
        folder = working_folder.root()
        old, live, recent = (folder / name for name in ("old", "live", "recent"))
        for one in (old, live, recent):
            one.mkdir()
        week_ago = time.time() - 7 * 24 * 3600
        for one in (old, live):
            os.utime(one, (week_ago, week_ago))
        working_folder.clear_leftovers({str(live)})
        self.assertEqual(sorted(p.name for p in folder.iterdir()), ["live", "recent"])


if __name__ == "__main__":
    unittest.main()
