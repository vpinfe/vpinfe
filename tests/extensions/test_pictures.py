"""The pictures an extension's rows name: an address of core's own in each row, and core
fetching or reading the picture behind it only when that address is asked for."""

from __future__ import annotations

import dataclasses
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

import requests

from common import i18n
from common.extensions import contract, host, pictures, store
from common.i18n import t
from common.media import sized_media
from tests.support import pictures as made
from tests.support.accepted import owner_accepts
from tests.support.identity import IDENTITY
from tests.support.served import client_for

MODULE = "tests.extensions.test_pictures"
NAME = "pictured"
WEB = "https://pictures.example/cover.png"

ROWS: list[dict] = []
GRABBED: list[dict] = []


def pictured(ctx: Any) -> None:
    ctx.ui.list("covers", lambda: [dict(one) for one in ROWS], title="Covers",
                columns=[{"field": "name"}, {"field": "art", "kind": "picture"}],
                verbs=[{"key": "grab", "run": lambda rows: GRABBED.extend(rows)}], area="community")
    ctx.ui.list("games", lambda: [dict(one) for one in ROWS], title="Boards",
                columns=[{"field": "vps_id", "kind": "game", "name_from": "name",
                          "picture": "art"}], area="community")
    ctx.ui.list("twice", lambda: [dict(one) for one in ROWS], title="Twice",
                columns=[{"field": "vps_id", "kind": "game", "name_from": "name", "picture": "art"},
                         {"field": "art", "kind": "picture"}], area="community")


def _installed(root: Path, capabilities: list[str]) -> Path:
    directory = root / NAME
    (directory / "i18n").mkdir(parents=True)
    (directory / "art").mkdir()
    (directory / "extension.json").write_text(json.dumps({
        "name": NAME, "display_name": "Pictured", "version": "1.0.0",
        "description": "Rows that name pictures.",
        "requires_platform": contract.PLATFORM_ABI, **IDENTITY,
        "capabilities": capabilities}), encoding="utf-8")
    (directory / "i18n" / "en.json").write_text(
        json.dumps({"list.covers.title": "Covers", "list.games.title": "Boards",
                    "list.twice.title": "Twice",
                    "list.covers.verb.grab.label": "Grab"}), encoding="utf-8")
    (directory / "__init__.py").write_text(
        f"from {MODULE} import pictured as register\n", encoding="utf-8")
    return directory


class PicturedCase(unittest.TestCase):
    capabilities = ["ui:mount", "net:outbound"]

    def setUp(self) -> None:
        owner_accepts(self)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        for patcher in (patch.object(pictures, "ROOT", self.root / "kept"),
                        patch.object(sized_media, "ROOT", self.root / "sized")):
            patcher.start()
            self.addCleanup(patcher.stop)
        pictures._sources.clear()
        pictures._failed.clear()
        ROWS.clear()
        GRABBED.clear()
        self.registry = host.Registry(store.ExtensionStore(self.root))
        self.addCleanup(self.registry.clear)
        self.addCleanup(i18n.disown, f"ext.{NAME}")
        self.folder = _installed(self.root / "installed", self.capabilities)
        self.record = self.registry.load(self.folder)
        self.assertEqual(self.record.state, host.LOADED, self.record.error)
        self.client = client_for(self, self.registry, self.record)

    def rows(self) -> list[dict]:
        response = self.client.get(f"/ext/{NAME}/lists/covers")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["rows"]

    def picture(self, address: str) -> Any:
        return self.client.get(address.replace(f"/api/v1/ext/{NAME}", f"/ext/{NAME}"))

    def put(self, name: str, content: bytes) -> None:
        (self.folder / "art" / name).write_bytes(content)


class WhatTheRowsCarry(PicturedCase):
    def test_a_picture_is_an_address_of_cores_own(self) -> None:
        ROWS.append({"name": "a", "art": WEB})

        (row,) = self.rows()

        self.assertRegex(row["art"], rf"^/api/v1/ext/{NAME}/pictures/[0-9a-f]{{20}}$")
        self.assertNotIn("pictures.example", row["art"])

    def test_one_source_is_one_address(self) -> None:
        ROWS.extend([{"name": "a", "art": WEB}, {"name": "b", "art": WEB},
                     {"name": "c", "art": WEB + "?other"}])

        first, second, third = (row["art"] for row in self.rows())

        self.assertEqual(first, second)
        self.assertNotEqual(first, third)

    def test_a_row_with_no_picture_has_none(self) -> None:
        ROWS.extend([{"name": "a"}, {"name": "b", "art": ""}, {"name": "c", "art": None}])

        self.assertEqual(["", "", ""], [row.get("art", "") for row in self.rows()])

    def test_a_web_picture_without_the_capability_fails_the_read_in_words(self) -> None:
        self.capabilities = ["ui:mount"]
        self.setUp()
        ROWS.append({"name": "a", "art": WEB})

        response = self.client.get(f"/ext/{NAME}/lists/covers")

        self.assertEqual(400, response.status_code)
        self.assertIn(t("error.extensions.picture_outbound", display="Pictured",
                        key="list covers"), response.text)

    def test_a_game_column_s_picture_field_is_an_address_too(self) -> None:
        ROWS.append({"vps_id": "v1", "name": "a", "art": WEB})

        response = self.client.get(f"/ext/{NAME}/lists/games")

        self.assertEqual(response.status_code, 200, response.text)
        (row,) = response.json()["rows"]
        self.assertRegex(row["art"], rf"^/api/v1/ext/{NAME}/pictures/[0-9a-f]{{20}}$")

    def test_a_field_named_by_two_columns_is_addressed_once(self) -> None:
        ROWS.append({"vps_id": "v1", "name": "a", "art": WEB})

        response = self.client.get(f"/ext/{NAME}/lists/twice")

        self.assertEqual(response.status_code, 200, response.text)
        (row,) = response.json()["rows"]
        self.assertRegex(row["art"], rf"^/api/v1/ext/{NAME}/pictures/[0-9a-f]{{20}}$")

    def test_a_file_picture_needs_no_capability(self) -> None:
        self.capabilities = ["ui:mount"]
        self.setUp()
        ROWS.append({"name": "a", "art": "art/one.png"})

        self.assertTrue(self.rows()[0]["art"].startswith(f"/api/v1/ext/{NAME}/pictures/"))


class WhatAVerbRunsOn(PicturedCase):
    def test_is_the_row_as_the_extension_gave_it(self) -> None:
        ROWS.append({"id": "1", "name": "a", "art": "art/one.png"})

        response = self.client.post(f"/ext/{NAME}/lists/covers/verbs/grab", json={"ids": ["1"]})

        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual("art/one.png", GRABBED[0]["art"])


class AFilePicture(PicturedCase):
    def test_is_served_from_the_extensions_folder_at_list_size(self) -> None:
        self.put("one.png", made.png(600, 300))
        ROWS.append({"name": "a", "art": "art/one.png"})

        response = self.picture(self.rows()[0]["art"])

        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual("image/webp", response.headers["content-type"])
        from io import BytesIO

        from PIL import Image
        with Image.open(BytesIO(response.content)) as opened:
            self.assertEqual(sized_media.THUMBNAIL, max(opened.size))

    def test_outside_the_folder_is_not_served(self) -> None:
        (self.root / "outside.png").write_bytes(made.png(10, 10))
        ROWS.append({"name": "a", "art": "../../outside.png"})

        self.assertEqual(404, self.picture(self.rows()[0]["art"]).status_code)

    def test_that_is_not_an_image_is_not_served(self) -> None:
        self.put("notes.png", b"not a picture at all")
        ROWS.append({"name": "a", "art": "art/notes.png"})

        self.assertEqual(404, self.picture(self.rows()[0]["art"]).status_code)

    def test_that_is_missing_is_not_served(self) -> None:
        ROWS.append({"name": "a", "art": "art/none.png"})

        self.assertEqual(404, self.picture(self.rows()[0]["art"]).status_code)

    def test_with_a_nul_in_its_path_is_not_served(self) -> None:
        ROWS.append({"name": "a", "art": "art/a\u0000b.png"})

        self.assertEqual(404, self.picture(self.rows()[0]["art"]).status_code)

    def test_that_has_a_header_and_no_body_is_not_served_whole(self) -> None:
        self.put("cut.png", made.png(600, 300)[:60])
        ROWS.append({"name": "a", "art": "art/cut.png"})

        self.assertEqual(404, self.picture(self.rows()[0]["art"]).status_code)

    def test_is_written_a_moment_after_its_row_appears(self) -> None:
        ROWS.append({"name": "a", "art": "art/late.png"})
        address = self.rows()[0]["art"]
        self.assertEqual(404, self.picture(address).status_code)

        self.put("late.png", made.png(40, 40))

        self.assertEqual(200, self.picture(address).status_code)


class AWebPicture(PicturedCase):
    def fetching(self, content: bytes | Exception):
        def download(url: str, dest: Path, **options: Any) -> None:
            self.asked.append(url)
            if isinstance(content, Exception):
                raise content
            dest.write_bytes(content)
            if options.get("progress"):
                options["progress"](len(content))

        self.asked: list[str] = []
        return patch.object(pictures.http_client, "download_file", download)

    def test_is_fetched_once_and_kept(self) -> None:
        ROWS.append({"name": "a", "art": WEB})
        address = self.rows()[0]["art"]

        with self.fetching(made.png(500, 500)):
            first, second = self.picture(address), self.picture(address)

        self.assertEqual((200, 200), (first.status_code, second.status_code))
        self.assertEqual([WEB], self.asked)

    def test_that_is_not_an_image_is_not_served_and_not_kept(self) -> None:
        ROWS.append({"name": "a", "art": WEB})
        address = self.rows()[0]["art"]

        with self.fetching(b"<html>sign in</html>"):
            response = self.picture(address)

        self.assertEqual(404, response.status_code)
        self.assertEqual([], list((self.root / "kept").rglob("*.*")))

    def test_that_would_not_come_is_not_asked_for_again_for_a_while(self) -> None:
        ROWS.append({"name": "a", "art": WEB})
        address = self.rows()[0]["art"]

        with self.fetching(requests.ConnectionError("down")):
            first = self.picture(address)
            second = self.picture(address)

        self.assertEqual((404, 404), (first.status_code, second.status_code))
        self.assertEqual([WEB], self.asked)

    def test_that_fails_again_leaves_the_one_that_came(self) -> None:
        ROWS.append({"name": "a", "art": WEB})
        address = self.rows()[0]["art"]
        with self.fetching(made.png(500, 500)):
            self.assertEqual(200, self.picture(address).status_code)
        kept = list((self.root / "kept").rglob("*.*")) or list((self.root / "kept").rglob("*"))

        with self.fetching(requests.ConnectionError("down")):
            pictures._fetched(self.record, WEB, address.rsplit("/", 1)[1])

        self.assertTrue(all(one.exists() for one in kept if one.is_file()))

    def test_that_is_too_large_is_not_served(self) -> None:
        ROWS.append({"name": "a", "art": WEB})
        address = self.rows()[0]["art"]

        with patch.object(pictures, "LARGEST_BYTES", 10), self.fetching(made.png(50, 50)):
            response = self.picture(address)

        self.assertEqual(404, response.status_code)

    def test_that_no_row_named_is_never_fetched(self) -> None:
        ROWS.append({"name": "a", "art": WEB})
        self.rows()

        with self.fetching(made.png(5, 5)):
            response = self.client.get(f"/ext/{NAME}/pictures/{'0' * 20}")

        self.assertEqual(404, response.status_code)
        self.assertEqual([], self.asked)


class TheCapabilityIsAskedAgainAtFetchTime(PicturedCase):
    def test_a_kept_picture_is_not_served_once_the_capability_is_gone(self) -> None:
        ROWS.append({"name": "a", "art": WEB})
        address = self.rows()[0]["art"]
        with AWebPicture.fetching(cast(AWebPicture, self), made.png(500, 500)):
            self.assertEqual(200, self.picture(address).status_code)

        assert self.record.manifest is not None
        self.record.manifest = dataclasses.replace(self.record.manifest,
                                                   capabilities=("ui:mount",))

        self.assertEqual(404, self.picture(address).status_code)


    def test_a_record_without_a_manifest_holds_nothing(self) -> None:
        record = host.Record.__new__(host.Record)
        record.manifest = None
        record.name = "bare"
        with self.assertRaises(pictures.OutboundNotDeclaredError):
            pictures.address(record, WEB)


if __name__ == "__main__":
    unittest.main()
