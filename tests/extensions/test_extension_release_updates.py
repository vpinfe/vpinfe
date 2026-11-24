"""Protects: (a) an update taken from an author's repository keeps the version it replaced and
can put it back, never writes to a folder kept with Git, and leaves the installed version
exactly as it was when the fetch or the check of what arrived fails.
"""

from __future__ import annotations

import io
import json
import os
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from common.config import paths
from common.extensions import contract, host
from common.online import extension_list, extension_releases
from common.online.extension_list import ExtensionListError
from common.online.theme_registry_client import ThemeRegistryClient, ThemeRegistryError
from tests.support.identity import IDENTITY

REPO = "https://github.com/example/alpha"


def _manifest(version: str, **more: object) -> dict:
    return {"name": "alpha", "version": version, "requires_platform": contract.PLATFORM_ABI,
            **IDENTITY, **more}


def _archive(version: str, marker: str, **more: object) -> io.BytesIO:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr("alpha-1/extension.json", json.dumps(_manifest(version, **more)))
        archive.writestr("alpha-1/__init__.py", "def register(ctx): pass\n")
        archive.writestr("alpha-1/marker.txt", marker)
    out.seek(0)
    return out


class Client(ThemeRegistryClient):
    def __init__(self, archive: io.BytesIO | Exception) -> None:
        super().__init__()
        self.archive = archive
        self.downloads: list[str] = []

    def download_zip(self, url: str, limit: int | None = None) -> io.BytesIO:
        self.downloads.append(url)
        if isinstance(self.archive, Exception):
            raise self.archive
        return self.archive


class Case(unittest.TestCase):
    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.target = self.root / "extensions" / "alpha"
        for owner, attribute, value in (
                (host, "INSTALLED_DIR", self.root / "extensions"),
                (paths, "EXTENSION_PREVIOUS_DIR", self.root / "previous"),
                (paths, "EXTENSION_RELEASES_PATH", self.root / "releases.json")):
            patcher = mock.patch.object(owner, attribute, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.target.mkdir(parents=True)
        (self.target / "extension.json").write_text(json.dumps(_manifest("1.0.0")),
                                                    encoding="utf-8")
        (self.target / "marker.txt").write_text("old", encoding="utf-8")

    def found(self, **changes: object) -> extension_releases.Latest:
        values: dict = {"kind": extension_releases.NEWER, "address": REPO, "version": "1.1.0",
                        "tag": "v1.1.0", "source": REPO}
        values.update(changes)
        return extension_releases.Latest(**values)

    def marker(self) -> str:
        return (self.target / "marker.txt").read_text(encoding="utf-8")

    def update(self, client: Client, **changes: object) -> str:
        return extension_releases.update("alpha", self.target, self.found(**changes),
                                         source=REPO, client=client)

    def leftovers(self) -> list[str]:
        return sorted(one.name for one in self.root.iterdir() if one.name.startswith("."))


class UpdateTests(Case):
    def test_the_version_an_update_replaced_is_kept(self) -> None:
        version = self.update(Client(_archive("1.1.0", "new")))

        self.assertEqual((version, self.marker()), ("1.1.0", "new"))
        kept = extension_releases.previous("alpha", "1.1.0")
        assert kept is not None
        self.assertEqual(kept["version"], "1.0.0")
        held = paths.EXTENSION_PREVIOUS_DIR / "alpha" / "folder" / "marker.txt"
        self.assertEqual(held.read_text(encoding="utf-8"), "old")
        self.assertEqual(self.leftovers(), [])

    def test_going_back_puts_the_replaced_version_back_and_keeps_nothing_behind_it(self) -> None:
        self.update(Client(_archive("1.1.0", "new")))

        back = extension_releases.go_back("alpha", self.target, installed_version="1.1.0")

        self.assertEqual((back, self.marker()), ("1.0.0", "old"))
        self.assertIsNone(extension_releases.previous("alpha", "1.0.0"))
        self.assertEqual(self.leftovers(), [])

    def test_a_copy_added_by_hand_since_leaves_nothing_to_go_back_to(self) -> None:
        self.update(Client(_archive("1.1.0", "new")))

        self.assertIsNone(extension_releases.previous("alpha", "1.2.0"))
        with self.assertRaises(ExtensionListError):
            extension_releases.go_back("alpha", self.target, installed_version="1.2.0")
        self.assertEqual(self.marker(), "new")


class NothingIsLostTests(Case):
    def test_a_folder_kept_with_git_is_never_touched(self) -> None:
        (self.target / ".git").mkdir()
        (self.target / ".git" / "HEAD").write_text("ref: refs/heads/main", encoding="utf-8")
        client = Client(_archive("1.1.0", "new"))

        with self.assertRaises(ExtensionListError):
            self.update(client)

        self.assertEqual(client.downloads, [])
        self.assertEqual(self.marker(), "old")
        self.assertTrue((self.target / ".git" / "HEAD").is_file())
        self.assertFalse(paths.EXTENSION_PREVIOUS_DIR.exists())

    def test_a_failed_fetch_leaves_the_installed_version_as_it_was(self) -> None:
        with self.assertRaises(ThemeRegistryError):
            self.update(Client(ThemeRegistryError("no connection")))

        self.assertEqual(self.marker(), "old")
        self.assertEqual(extension_releases.previous("alpha", "1.1.0"), None)
        self.assertFalse(paths.EXTENSION_PREVIOUS_DIR.exists())
        self.assertEqual(self.leftovers(), [])

    def test_what_arrives_must_be_what_the_check_showed(self) -> None:
        for arrives in (_archive("1.2.0", "new"),
                        _archive("1.1.0", "new", capabilities=["net"])):
            with self.subTest(), self.assertRaises(ExtensionListError):
                self.update(Client(arrives))

            self.assertEqual(self.marker(), "old")
            self.assertFalse(paths.EXTENSION_PREVIOUS_DIR.exists())
            self.assertEqual(self.leftovers(), [])

    def test_a_refusal_after_the_files_are_in_puts_the_old_ones_back(self) -> None:
        def refuse(_arrived: object) -> None:
            raise ExtensionListError("not accepted")

        with self.assertRaises(ExtensionListError):
            extension_releases.update(
                "alpha", self.target, self.found(), source=REPO,
                client=Client(_archive("1.1.0", "new")), trust=refuse)

        self.assertEqual(self.marker(), "old")
        self.assertFalse((paths.EXTENSION_PREVIOUS_DIR / "alpha").exists())
        self.assertEqual(self.leftovers(), [])

    def test_an_update_that_was_not_checked_for_this_repository_is_refused(self) -> None:
        client = Client(_archive("1.1.0", "new"))

        with self.assertRaises(ExtensionListError):
            self.update(client, address="https://github.com/someone/else")

        self.assertEqual((client.downloads, self.marker()), ([], "old"))

    def test_the_same_update_twice_keeps_the_version_it_first_replaced(self) -> None:
        self.update(Client(_archive("1.1.0", "new")))
        client = Client(_archive("1.1.0", "newer"))

        with self.assertRaises(ExtensionListError):
            self.update(client)

        kept = extension_releases.previous("alpha", "1.1.0")
        assert kept is not None
        self.assertEqual((client.downloads, kept["version"], self.marker()), ([], "1.0.0", "new"))

    def test_a_release_older_than_what_was_copied_in_is_refused(self) -> None:
        (self.target / "extension.json").write_text(json.dumps(_manifest("3.0.0")),
                                                    encoding="utf-8")
        client = Client(_archive("1.1.0", "new"))

        with self.assertRaises(ExtensionListError):
            self.update(client)

        self.assertEqual((client.downloads, self.marker()), ([], "old"))

    def test_going_back_never_touches_a_folder_that_has_since_gained_git(self) -> None:
        self.update(Client(_archive("1.1.0", "new")))
        (self.target / ".git").mkdir()
        (self.target / ".git" / "HEAD").write_text("ref: refs/heads/main", encoding="utf-8")

        with self.assertRaises(ExtensionListError):
            extension_releases.go_back("alpha", self.target, installed_version="1.1.0")

        self.assertEqual(self.marker(), "new")
        self.assertTrue((self.target / ".git" / "HEAD").is_file())

    def test_a_work_folder_still_holding_a_replaced_copy_is_never_swept(self) -> None:
        stranded = self.root / ".update-stranded"
        (stranded / "old").mkdir(parents=True)
        empty = self.root / ".update-empty"
        empty.mkdir()
        long_ago = time.time() - 10 * extension_list.STALE_WORK_S
        for one in (stranded, empty):
            os.utime(one, (long_ago, long_ago))

        self.update(Client(_archive("1.1.0", "new")))

        self.assertEqual(self.leftovers(), [".update-stranded"])


if __name__ == "__main__":
    unittest.main()
