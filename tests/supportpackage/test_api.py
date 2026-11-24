"""The routes that hand a reviewed package to the page, and refuse anyone without the scope."""

from __future__ import annotations

import io
import unittest
import zipfile
from unittest.mock import patch

from starlette.testclient import TestClient

import httpapi
from common.support import package
from httpapi import auth, scopes
from tests.supportpackage.test_package import SENTINELS, _Folder


class _Served(_Folder):
    def setUp(self) -> None:
        super().setUp()
        self.write_logs()
        self.client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)

    def made(self, body: dict | None = None) -> dict:
        answer = self.client.post("/support", json=body or {})
        self.assertEqual(answer.status_code, 200, answer.text)
        return answer.json()


class MakingOne(_Served):
    def test_the_answer_is_what_a_person_reviews(self) -> None:
        body = self.made()
        self.assertEqual(
            set(body), {"id", "filename", "checksum", "bytes", "expires_in", "files",
                        "counts", "precautions", "summary"})
        self.assertEqual([one["name"] for one in body["files"]],
                         [package.README, package.SUMMARY, package.LOG,
                          package.VISUAL_PINBALL_LOG, package.PREVIOUS_LOG])
        self.assertEqual(body["expires_in"], package.TTL_SECONDS)

    def test_the_answer_never_carries_a_replaced_value(self) -> None:
        text = self.client.post("/support", json={"hide_tables": True}).text
        for kind, value in SENTINELS.items():
            with self.subTest(kind=kind):
                self.assertNotIn(value, text)
        self.assertNotIn("originals", text)

    def test_what_was_chosen_is_what_is_made(self) -> None:
        body = self.made({"logs": False})
        self.assertEqual([one["name"] for one in body["files"]],
                         [package.README, package.SUMMARY])

    def test_a_word_it_was_not_told_about_is_refused(self) -> None:
        self.assertIn(self.client.post("/support", json={"bogus": 1}).status_code, (400, 422))

    def test_a_package_over_the_cap_is_a_413(self) -> None:
        with patch.object(package, "MAX_BUILT_BYTES", 100):
            self.assertEqual(self.client.post("/support", json={}).status_code, 413)


class ReadingOneBack(_Served):
    def test_the_file_is_the_zip_that_was_reviewed(self) -> None:
        body = self.made()
        answer = self.client.get(f"/support/{body['id']}/file")
        self.assertEqual(answer.status_code, 200)
        self.assertEqual(answer.headers["content-type"], "application/zip")
        self.assertIn(body["filename"], answer.headers["content-disposition"])
        self.assertEqual(len(answer.content), body["bytes"])
        with zipfile.ZipFile(io.BytesIO(answer.content)) as archive:
            self.assertIsNone(archive.testzip())
            self.assertEqual(archive.read(package.SUMMARY).decode("utf-8"), body["summary"])

    def test_two_downloads_are_the_same_bytes(self) -> None:
        body = self.made()
        first = self.client.get(f"/support/{body['id']}/file").content
        self.assertEqual(self.client.get(f"/support/{body['id']}/file").content, first)

    def test_the_summary_is_markdown(self) -> None:
        body = self.made()
        answer = self.client.get(f"/support/{body['id']}/summary")
        self.assertEqual(answer.status_code, 200)
        self.assertTrue(answer.headers["content-type"].startswith("text/markdown"))
        self.assertEqual(answer.text, body["summary"])
        self.assertTrue(answer.text.startswith("# VPinFE Support Summary"))

    def test_an_unknown_or_expired_id_is_a_404_on_both(self) -> None:
        for route in ("file", "summary"):
            with self.subTest(route=route):
                self.assertEqual(self.client.get(f"/support/nope/{route}").status_code, 404)
        body = self.made()
        package.forget(body["id"])
        self.assertEqual(self.client.get(f"/support/{body['id']}/file").status_code, 404)

    def test_a_third_package_pushes_the_first_out(self) -> None:
        first = self.made()["id"]
        self.made()
        self.made()
        self.assertEqual(self.client.get(f"/support/{first}/file").status_code, 404)


class TheScope(_Served):
    def setUp(self) -> None:
        super().setUp()
        self.addCleanup(auth.set_policy, auth.SignInPolicy())

    def grant(self, *granted: str) -> None:
        class Policy(auth.SignInPolicy):
            def identify(self, request):
                return auth.Identity(name="test", scopes=frozenset(granted))

        auth.set_policy(Policy())

    def test_without_the_system_scope_every_route_is_refused(self) -> None:
        body = self.made()
        self.grant(scopes.GAMES_READ)
        self.assertEqual(self.client.post("/support", json={}).status_code, 403)
        self.assertEqual(self.client.get(f"/support/{body['id']}/file").status_code, 403)
        self.assertEqual(self.client.get(f"/support/{body['id']}/summary").status_code, 403)

    def test_with_it_they_all_answer(self) -> None:
        self.grant(scopes.SYSTEM_READ)
        body = self.made()
        self.assertEqual(self.client.get(f"/support/{body['id']}/file").status_code, 200)


if __name__ == "__main__":
    unittest.main()
