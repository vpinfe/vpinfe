"""Protects: (b) a downloaded web page is served so it cannot run as the Manager.

A game's `.html` file is downloaded content opened from the app's own address. Without the
two headers it would run with the Manager's origin; with them it has none, and a browser
does not read a file as a type it was not sent as.
"""

from __future__ import annotations

from unittest.mock import patch

from starlette.testclient import TestClient

import httpapi
from common.games import media_browse
from common.media import sized_media
from tests.support.library import TempTree, fake_game, write_game

GAME_ID = "Headers0001"
FOLDER = "Cactus Canyon (Bally 1998)"
INFO = {
    "Info": {"Name": "Cactus Canyon"},
    "VPinFE": {"game_id": GAME_ID},
    "tables": {"tbl0000001": {"id": "tbl0000001", "filename": f"{FOLDER}.vpx"}},
}
PAGE = b"<html><body><script>parent.document.title = 'taken'</script></body></html>"
PICTURE = b"\x89PNG\r\n\x1a\nnot really a picture"
HEADERS = {"content-security-policy": "sandbox", "x-content-type-options": "nosniff"}


class LibraryFileHeaderTests(TempTree):
    def setUp(self) -> None:
        super().setUp()
        copies = self.root / "sized"
        patcher = patch.object(sized_media, "ROOT", copies)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.folder = write_game(self.root, FOLDER, info=INFO,
                                 medias={"rulesheet.html": PAGE, "wheel.png": PICTURE})
        game = fake_game(self.folder, FOLDER, meta=INFO)
        catalog = patch("common.games.game_repository.catalog", return_value={GAME_ID: game})
        catalog.start()
        self.addCleanup(catalog.stop)
        self.client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)

    def _version(self, kind: str) -> str:
        return self.client.get(f"/games/{GAME_ID}/media").json()["media"][kind]["version"]

    def _assert_sandboxed(self, response) -> None:
        for name, value in HEADERS.items():
            self.assertEqual(response.headers.get(name), value, response.headers)

    def test_a_web_page_is_served_sandboxed_by_address_and_by_version(self) -> None:
        by_address = self.client.get(f"/games/{GAME_ID}/media/rule_sheet")
        by_version = self.client.get(
            f"/games/{GAME_ID}/media/rule_sheet?v={self._version('rule_sheet')}")

        for response in (by_address, by_version):
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.content, PAGE)
            self._assert_sandboxed(response)

    def test_the_header_is_on_a_not_modified_and_a_range_too(self) -> None:
        first = self.client.get(f"/games/{GAME_ID}/media/rule_sheet")
        again = self.client.get(f"/games/{GAME_ID}/media/rule_sheet",
                                headers={"If-None-Match": first.headers["etag"]})
        ranged = self.client.get(f"/games/{GAME_ID}/media/rule_sheet",
                                 headers={"Range": "bytes=0-5"})

        self.assertEqual(again.status_code, 304)
        self.assertEqual(ranged.status_code, 206)
        self._assert_sandboxed(again)
        self._assert_sandboxed(ranged)

    def test_a_picture_still_serves(self) -> None:
        response = self.client.get(f"/games/{GAME_ID}/media/wheel")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.content, PICTURE)
        self.assertEqual(response.headers["content-type"], "image/png")
        self._assert_sandboxed(response)

    def test_a_file_browsed_for_is_served_the_same_way(self) -> None:
        page = self.folder / "medias" / "rulesheet.html"
        with patch.object(media_browse, "media_file", return_value=page):
            response = self.client.get("/filesystem/file", params={"path": str(page)})

        self.assertEqual(response.status_code, 200, response.text)
        self._assert_sandboxed(response)
