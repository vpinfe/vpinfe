"""Whose an extension is: the author and addresses its manifest must carry, the picture it may
name, and what the host and the API say of them.

An extension VPinFE did not ship picks its own picture and its own author, so what has to
hold is that none of it is drawn or served before the Owner has accepted the extension, and
that a manifest which cannot say who made it does not load.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

from fastapi import FastAPI

from common.extensions import check, contract, host, pictures, store
from common.i18n import t
from common.media import sized_media
from common.security import accepted
from httpapi import extensions as extensions_api
from tests.support import pictures as made
from tests.support.accepted import isolated, owner_accepts
from tests.support.identity import IDENTITY
from tests.support.served import client_for

NAME = "identified"
MANIFEST = {"name": NAME, "display_name": "Identified", "version": "1.0.0",
            "description": "Says whose it is.", "requires_platform": contract.PLATFORM_ABI,
            **IDENTITY}


def _said(refused: contract.ManifestError) -> str:
    return t(refused.key, **refused.values)


class TheAuthor(unittest.TestCase):
    def refused(self, **fields: Any) -> contract.ManifestError:
        with self.assertRaises(contract.ManifestError) as caught:
            contract.parse({**MANIFEST, **fields})
        return caught.exception

    def test_is_required(self) -> None:
        raw = {k: v for k, v in MANIFEST.items() if k != "author"}

        with self.assertRaises(contract.ManifestError) as caught:
            contract.parse(raw)

        self.assertEqual(caught.exception.key, "extension.reason.manifest_no_author")

    def test_blank_is_the_same_as_missing(self) -> None:
        self.assertEqual(self.refused(author="  ").key, "extension.reason.manifest_no_author")

    def test_is_one_line_of_at_most_sixty_characters(self) -> None:
        for bad in ("x" * 61, "two\nlines", 7, ["a"]):
            with self.subTest(author=bad):
                self.assertEqual(self.refused(author=bad).key,
                                 "extension.reason.manifest_bad_author")

    def test_holds_no_control_or_direction_character(self) -> None:
        for bad in ("a‮b", "a\x1b[2Jb", "a b", "a\x00b", "a\x0bb", "a\x85b"):
            with self.subTest(author=bad):
                self.assertEqual(self.refused(author=bad).key,
                                 "extension.reason.manifest_bad_author")

    def test_is_kept_without_the_space_around_it(self) -> None:
        self.assertEqual(contract.parse({**MANIFEST, "author": "  Someone  "}).author, "Someone")


class TheVersion(unittest.TestCase):
    def test_starts_with_a_number(self) -> None:
        for good in ("1", "1.0", "1.2.3", "10.0.1-beta"):
            with self.subTest(version=good):
                self.assertEqual(contract.parse({**MANIFEST, "version": good}).version, good)

    def test_that_does_not_is_refused(self) -> None:
        for bad in ("", "v1.0.0", "latest", "x.1", "1\nline2", "1 - official", "1" * 41,
                    "1.0‮"):
            with self.subTest(version=bad), self.assertRaises(contract.ManifestError) as caught:
                contract.parse({**MANIFEST, "version": bad})

            self.assertEqual(caught.exception.key, "extension.reason.manifest_bad_version")


class TheAddresses(unittest.TestCase):
    def refused(self, **fields: Any) -> str:
        with self.assertRaises(contract.ManifestError) as caught:
            contract.parse({**MANIFEST, **fields})
        return caught.exception.key

    def test_the_source_is_required(self) -> None:
        raw = {k: v for k, v in MANIFEST.items() if k != "source"}

        with self.assertRaises(contract.ManifestError) as caught:
            contract.parse(raw)

        self.assertEqual(caught.exception.key, "extension.reason.manifest_bad_source")

    def test_the_source_is_an_https_host_owner_and_repository(self) -> None:
        for bad in ("http://github.com/o/r", "https://github.com/o", "https://github.com/o/r/x",
                    "github.com/o/r", "https://github.com//r", "https://github.com/o/r with",
                    "https://github.com/" + "o" * 300 + "/r", "",
                    "https://github.com@elsewhere.example/o/r", "https://github.com?x/o/r",
                    "https://github.com#x/o/r", "https://github.com\\x/o/r",
                    "https://github.com/o/r‮"):
            with self.subTest(source=bad):
                self.assertEqual(self.refused(source=bad),
                                 "extension.reason.manifest_bad_source")

    def test_a_trailing_slash_or_dot_git_is_tolerated_and_kept_as_written(self) -> None:
        for good in ("https://github.com/o/r", "https://github.com/o/r/",
                     "https://github.com/o/r.git", "https://git.example.net/o/r"):
            with self.subTest(source=good):
                self.assertEqual(contract.parse({**MANIFEST, "source": good}).source, good)

    def test_the_repository_is_owner_and_name(self) -> None:
        self.assertEqual(contract.parse({**MANIFEST, "source": "https://github.com/o/r.git"})
                         .repository, "o/r")

    def test_the_home_is_optional(self) -> None:
        self.assertEqual(contract.parse(MANIFEST).home, "")

    def test_the_home_is_any_web_address(self) -> None:
        for good in ("https://example.net", "http://example.net/a/b?c=d"):
            with self.subTest(home=good):
                self.assertEqual(contract.parse({**MANIFEST, "home": good}).home, good)

    def test_a_home_that_is_not_one_is_refused(self) -> None:
        for bad in ("example.net", "ftp://example.net", "javascript:alert(1)", "https://",
                    "https://exa mple.net", "", 5, "https://example.net/" + "a" * 300,
                    "https://github.com@elsewhere.example/o"):
            with self.subTest(home=bad):
                self.assertEqual(self.refused(home=bad), "extension.reason.manifest_bad_home")

    def test_every_refusal_says_what_to_write(self) -> None:
        for fields in ({"author": ""}, {"author": "x" * 61}, {"version": "v1"},
                       {"home": "nope"}, {"source": "nope"}, {"picture": "../a.png"}):
            with self.subTest(fields=fields), self.assertRaises(contract.ManifestError) as caught:
                contract.parse({**MANIFEST, **fields})

            self.assertNotIn("{", _said(caught.exception))
            self.assertNotEqual(_said(caught.exception), caught.exception.key)


class ThePicturePath(unittest.TestCase):
    def test_is_a_path_inside_the_folder(self) -> None:
        for good in ("icon.png", "art/icon.png", "a/b/c.webp"):
            with self.subTest(picture=good):
                self.assertEqual(contract.parse({**MANIFEST, "picture": good}).picture, good)

    def test_that_could_leave_it_is_refused(self) -> None:
        for bad in ("../icon.png", "a/../../b.png", "/etc/passwd", "~/icon.png",
                    "C:/icon.png", "a//b.png", "./icon.png", "", 4, "a\\..\\b.png", "art\\icon.png",
                    "icon\u202e.png"):
            with self.subTest(picture=bad), self.assertRaises(contract.ManifestError) as caught:
                contract.parse({**MANIFEST, "picture": bad})

            self.assertEqual(caught.exception.key, "extension.reason.manifest_bad_picture")

    def test_left_out_is_none(self) -> None:
        self.assertEqual(contract.parse(MANIFEST).picture, "")


class Installed(unittest.TestCase):
    """An extension in a folder of its own, on a registry of its own."""

    def setUp(self) -> None:
        isolated(self)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        for patcher in (patch.object(pictures, "ROOT", self.root / "kept"),
                        patch.object(sized_media, "ROOT", self.root / "sized")):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.registry = host.Registry(store.ExtensionStore(self.root))
        self.addCleanup(self.registry.clear)

    def install(self, **fields: Any) -> Path:
        directory = self.root / "installed" / NAME
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "extension.json").write_text(
            json.dumps({**MANIFEST, **fields}), encoding="utf-8")
        (directory / "__init__.py").write_text("def register(ctx):\n    pass\n",
                                               encoding="utf-8")
        return directory

    def accept(self, record: host.Record) -> None:
        assert record.manifest is not None
        accepted.accept_extension(
            record.name, record.origin, folder=record.folder, version=record.manifest.version,
            scopes=record.manifest.scopes, capabilities=record.manifest.capabilities,
            play=record.manifest.play)


class WhatTheHostKnows(Installed):
    def test_it_carries_the_folder_it_runs_from(self) -> None:
        directory = self.install()

        record = self.registry.load(directory)

        self.assertEqual(record.as_dict()["path"], str(directory))
        self.assertEqual(record.folder, str(directory.parent))

    def test_a_picture_that_is_not_there_refuses_the_manifest(self) -> None:
        record = self.registry.load(self.install(picture="icon.png"))

        self.assertEqual(record.state, host.FAILED)
        self.assertEqual(record.why, "extension.reason.manifest_bad_picture")

    def test_a_picture_that_is_not_an_image_file_by_name_refuses_the_manifest(self) -> None:
        directory = self.install(picture="icon.txt")
        (directory / "icon.txt").write_bytes(made.png(8, 8))

        self.assertEqual(self.registry.load(directory).why,
                         "extension.reason.manifest_bad_picture")

    def test_a_picture_through_a_link_that_leaves_the_folder_refuses_the_manifest(self) -> None:
        directory = self.install(picture="icon.png")
        (self.root / "outside.png").write_bytes(made.png(8, 8))
        (directory / "icon.png").symlink_to(self.root / "outside.png")

        self.assertEqual(self.registry.load(directory).why,
                         "extension.reason.manifest_bad_picture")

    def test_a_picture_is_not_shown_before_it_is_accepted(self) -> None:
        directory = self.install(picture="icon.png")
        (directory / "icon.png").write_bytes(made.png(8, 8))

        record = self.registry.load(directory)

        self.assertFalse(record.as_dict()["picture_shown"])

    def test_a_picture_is_shown_once_it_is_accepted(self) -> None:
        directory = self.install(picture="icon.png")
        (directory / "icon.png").write_bytes(made.png(8, 8))
        owner_accepts(self)

        record = self.registry.load(directory)

        self.assertTrue(record.as_dict()["picture_shown"])

    def test_an_extension_that_names_none_shows_none(self) -> None:
        owner_accepts(self)

        self.assertFalse(self.registry.load(self.install()).as_dict()["picture_shown"])

    def test_one_that_ships_shows_its_picture_without_being_accepted(self) -> None:
        directory = self.install(picture="icon.png")
        (directory / "icon.png").write_bytes(made.png(8, 8))
        with patch.object(host, "BUNDLED_DIR", self.root / "installed"):
            record = self.registry.load(directory)

            self.assertEqual(record.origin, accepted.SHIPPED)
            self.assertTrue(record.picture_shown)


class WhatWasAccepted(Installed):
    def test_the_version_is_what_the_owner_last_accepted(self) -> None:
        record = self.registry.load(self.install(version="1.0.0"))
        self.assertEqual(record.trust()["accepted_version"], "")

        self.accept(record)

        self.assertEqual(record.trust()["accepted_version"], "1.0.0")
        self.assertEqual(
            accepted.accepted_version(NAME, record.origin, record.folder), "1.0.0")

    def test_one_that_was_never_accepted_has_none(self) -> None:
        self.assertEqual(accepted.accepted_version(NAME, accepted.IN_FOLDER, "/nowhere"), "")


class TheCheck(Installed):
    def report(self, directory: Path) -> list[str]:
        return check.check(directory)[0]

    def test_it_names_the_author_and_the_source(self) -> None:
        lines = self.report(self.install())

        self.assertIn(f"author     {IDENTITY['author']}", lines)
        self.assertIn(f"source     {IDENTITY['source']}", lines)

    def test_a_home_is_named_when_there_is_one(self) -> None:
        lines = self.report(self.install(home="https://example.net/x"))

        self.assertIn("home       https://example.net/x", lines)

    def test_no_home_is_not_named(self) -> None:
        self.assertFalse([one for one in self.report(self.install()) if one.startswith("home")])

    def test_a_picture_that_reads_is_named(self) -> None:
        directory = self.install(picture="icon.png")
        (directory / "icon.png").write_bytes(made.png(8, 8))

        self.assertIn("picture    icon.png", self.report(directory))

    def test_a_picture_that_does_not_decode_fails_the_check(self) -> None:
        directory = self.install(picture="icon.png")
        (directory / "icon.png").write_bytes(b"not a picture")

        self.assertTrue([one for one in self.report(directory)
                         if one.startswith("fails") and "icon.png" in one])


class ThePictureRoute(Installed):
    def served(self, *, accepts: bool = True, **fields: Any) -> Any:
        if accepts:
            owner_accepts(self)
        directory = self.install(**fields)
        (directory / "icon.png").write_bytes(made.png(600, 300))
        self.directory = directory
        record = self.registry.load(directory)
        self.client = client_for(self, self.registry, record)
        cast(FastAPI, self.client.app).include_router(extensions_api.router, prefix="/api/v1")
        return record

    def get(self, name: str = NAME, version: str = "1.0.0") -> Any:
        return self.client.get(f"/api/v1/extensions/{name}/picture?v={version}")

    def test_serves_the_picture_at_list_size(self) -> None:
        self.served(picture="icon.png")

        response = self.get()

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["content-type"], "image/webp")

    def test_an_extension_that_is_not_there_is_not_found(self) -> None:
        self.served(picture="icon.png")

        self.assertEqual(self.get("nothing").status_code, 404)

    def test_one_that_names_no_picture_is_not_found(self) -> None:
        self.served()

        self.assertEqual(self.get().status_code, 404)

    def test_one_not_accepted_is_not_served(self) -> None:
        self.served(accepts=False, picture="icon.png")

        self.assertEqual(self.get().status_code, 404)

    def test_one_whose_picture_has_gone_is_not_served(self) -> None:
        self.served(picture="icon.png")
        (self.directory / "icon.png").unlink()

        self.assertEqual(self.get().status_code, 404)

    def test_one_whose_picture_cannot_be_decoded_is_not_served(self) -> None:
        self.served(picture="icon.png")
        (self.directory / "icon.png").write_bytes(b"not a picture")

        self.assertEqual(self.get().status_code, 404)

    def test_the_version_in_the_address_is_not_read(self) -> None:
        self.served(picture="icon.png")

        self.assertEqual(self.get(version="9.9.9").status_code, 200)


class EveryManifestInTheTree(unittest.TestCase):
    """The manifests VPinFE ships and the ones the guide teaches are read by the same parser a
    person's extension is, so one without an author or a source fails here and not on a device."""

    ROOT = Path(__file__).resolve().parents[2]

    def test_every_one_parses(self) -> None:
        found = sorted([*(self.ROOT / "extensions").glob("*/extension.json"),
                        *(self.ROOT / "docs/extensions/examples").rglob("extension.json")])
        self.assertGreater(len(found), 30)
        for path in found:
            with self.subTest(path=path.relative_to(self.ROOT).as_posix()):
                contract.read_manifest(path.parent)

    def test_what_ships_is_by_vpinfe_and_has_its_logo(self) -> None:
        for path in sorted((self.ROOT / "extensions").glob("*/extension.json")):
            with self.subTest(path=path.parent.name):
                shipped = contract.read_manifest(path.parent)
                self.assertEqual(shipped.author, "VPinFE")
                self.assertEqual(shipped.repository,
                                 f"vpinfe/extension-{path.parent.name.replace('_', '-')}")
                self.assertEqual(shipped.picture, "logo.png")
                contract.check_picture(path.parent, shipped)


if __name__ == "__main__":
    unittest.main()
