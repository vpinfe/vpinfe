"""The session that holds an upload between arriving and being imported."""

from __future__ import annotations

import unittest
from unittest import mock

from common.i18n import t
from common.uploads import upload_session_service


class UploadSessionServiceTests(unittest.TestCase):
    def test_begin_store_finish_reassembles_tree(self):
        import io
        session = upload_session_service.begin_session()
        try:
            upload_session_service.store_file(session.upload_id, "a/b/c.txt", io.BytesIO(b"hello"))
            upload_session_service.store_file(session.upload_id, "top.txt", io.BytesIO(b"hi"))
            info = upload_session_service.finish_session(session.upload_id)
            self.assertEqual(info["file_count"], 2)
            directory = upload_session_service.get_session_dir(session.upload_id)
            self.assertEqual((directory / "a" / "b" / "c.txt").read_bytes(), b"hello")
        finally:
            upload_session_service.cleanup_session(session.upload_id)

    def test_unknown_session_raises(self):
        with self.assertRaises(upload_session_service.UnknownSessionError):
            upload_session_service.get_session_dir("does-not-exist")

    def test_unsafe_relpath_rejected(self):
        import io
        session = upload_session_service.begin_session()
        try:
            for bad in ["../escape.txt", "/abs.txt", "a/../../b.txt"]:
                with self.subTest(path=bad):
                    with self.assertRaises(upload_session_service.UnsafePathError):
                        upload_session_service.store_file(session.upload_id, bad, io.BytesIO(b"x"))
        finally:
            upload_session_service.cleanup_session(session.upload_id)

    def test_over_limit_rejected(self):
        import io
        session = upload_session_service.begin_session()
        self.addCleanup(upload_session_service.cleanup_session, session.upload_id)
        with mock.patch.object(upload_session_service, "MAX_TOTAL_BYTES", 4):
            with self.assertRaises(upload_session_service.UploadTooLargeError):
                upload_session_service.store_file(session.upload_id, "big.bin",
                                                 io.BytesIO(b"toolong"))

    def test_cleanup_removes_directory(self):
        session = upload_session_service.begin_session()
        directory = upload_session_service.get_session_dir(session.upload_id)
        self.assertTrue(directory.exists())
        upload_session_service.cleanup_session(session.upload_id)
        self.assertFalse(directory.exists())

    def test_expired_sessions_are_swept(self):
        session = upload_session_service.begin_session()
        directory = upload_session_service.get_session_dir(session.upload_id)
        # Force the session to look old, then a new begin() sweeps it.
        with mock.patch.object(upload_session_service, "time") as fake_time:
            fake_time.time.return_value = (
                session.created + upload_session_service.SESSION_TTL_SECONDS + 1)
            other = upload_session_service.begin_session()
        self.addCleanup(upload_session_service.cleanup_session, other.upload_id)
        self.assertFalse(directory.exists())
        with self.assertRaises(upload_session_service.UnknownSessionError) as caught:
            upload_session_service.get_session_dir(session.upload_id)
        self.assertEqual(str(caught.exception), t("error.uploads.upload_gone"))


if __name__ == "__main__":
    unittest.main()
