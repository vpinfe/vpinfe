"""Protects: (a) an install or removal that fails part way leaves the extension that was there, and
nothing else in the official folder, and a removal can only reach VPinFE's own folder.

Also pins (d): the kept copy of the list survives a failed check, an extension that needs a
newer VPinFE is refused before anything is downloaded, and a receipt exists for exactly
what was installed, since the receipt is what decides whether it still counts as official.
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
from common.extensions import contract, fingerprint, host
from common.online import extension_list
from common.online.theme_registry_client import ThemeRegistryClient, ThemeRegistryError
from tests.support.identity import IDENTITY

REPO = "https://github.com/example/extension-alpha"


def _archive(name: str = "alpha", version: str = "1.0.0", *, top: str = "extension-alpha-main",
             marker: str = "new", also: tuple[str, ...] = ()) -> io.BytesIO:
    manifest = {"name": name, "version": version, "requires_platform": contract.PLATFORM_ABI,
                **IDENTITY}
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr(f"{top}/extension.json", json.dumps(manifest))
        archive.writestr(f"{top}/__init__.py", "def register(ctx): pass\n")
        archive.writestr(f"{top}/marker.txt", marker)
        for member in also:
            archive.writestr(f"{top}/{member}", "x")
    out.seek(0)
    return out


class Client(ThemeRegistryClient):
    """Answers the list and each manifest from what the test gives it, and counts downloads."""

    def __init__(self, entries: list[dict] | None = None, manifests: dict[str, dict] | None = None,
                 archive: io.BytesIO | Exception | None = None) -> None:
        super().__init__()
        self.entries = entries or []
        self.manifests = manifests or {}
        self.archive = archive
        self.downloads: list[str] = []
        self.limits: list[int | None] = []

    def fetch_json(self, url: str) -> dict:
        if url == extension_list.OFFICIAL_LIST:
            return {"extensions": self.entries}
        for repository, manifest in self.manifests.items():
            if url.startswith(repository):
                return manifest
        raise ThemeRegistryError("not found")

    def fetch_any(self, url: str) -> dict:
        return {}

    def download_zip(self, url: str, limit: int | None = None) -> io.BytesIO:
        self.downloads.append(url)
        self.limits.append(limit)
        if isinstance(self.archive, Exception):
            raise self.archive
        assert self.archive is not None
        return self.archive


class Case(unittest.TestCase):
    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.official = self.root / "official"
        running = mock.patch.object(extension_list, "get_version", return_value="3.0.0")
        running.start()
        self.addCleanup(running.stop)
        for owner, attribute, value in (
                (host, "OFFICIAL_DIR", self.official),
                (paths, "EXTENSION_LIST_PATH", self.root / "cache" / "list.json"),
                (paths, "EXTENSION_RECEIPTS_PATH", self.root / "receipts.json")):
            patcher = mock.patch.object(owner, attribute, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def listed(self, **changes: str) -> extension_list.Listed:
        values = {"name": "alpha", "repository": REPO, "ref": "main", "version": "1.0.0"}
        values.update(changes)
        return extension_list.Listed(**values)

    def installed_marker(self) -> str:
        return (self.official / "alpha" / "marker.txt").read_text(encoding="utf-8")


class InstallTests(Case):
    def test_an_install_puts_the_extension_in_the_official_folder_with_a_receipt(self) -> None:
        client = Client(archive=_archive())

        target = extension_list.install(self.listed(), client)

        self.assertEqual(target, self.official / "alpha")
        self.assertEqual(self.installed_marker(), "new")
        receipt = fingerprint.receipt("alpha")
        assert receipt is not None
        self.assertEqual((receipt.repository, receipt.release), (REPO, "1.0.0"))
        self.assertEqual(receipt.files, fingerprint.hash_files(target))

    def test_an_archive_installs_without_its_tests_and_its_repository_files(self) -> None:
        archive = _archive(also=("tests/test_x.py", ".github/workflows/t.yml", ".idea/a",
                                 "data/tests/keep.txt"))

        target = extension_list.install(self.listed(), Client(archive=archive))

        self.assertEqual(sorted(one.name for one in target.iterdir()),
                         ["__init__.py", "data", "extension.json", "marker.txt"])
        self.assertTrue((target / "data" / "tests" / "keep.txt").is_file())
        receipt = fingerprint.receipt("alpha")
        assert receipt is not None
        self.assertEqual(receipt.files, fingerprint.hash_files(target))

    def test_a_failed_swap_puts_the_old_copy_back_and_leaves_nothing_beside_it(self) -> None:
        extension_list.install(self.listed(), Client(archive=_archive(marker="old")))
        before = fingerprint.receipt("alpha")

        with mock.patch.object(extension_list.shutil, "move", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                extension_list.install(self.listed(version="1.1.0"),
                                       Client(archive=_archive("alpha", "1.1.0")))

        self.assertEqual(self.installed_marker(), "old")
        self.assertEqual(fingerprint.receipt("alpha"), before)
        self.assertEqual([one.name for one in self.official.iterdir()], ["alpha"])
        self.assertEqual([one.name for one in self.root.iterdir() if one.name.startswith(".")], [])

    def test_a_receipt_that_cannot_be_written_puts_the_old_copy_back(self) -> None:
        extension_list.install(self.listed(), Client(archive=_archive(marker="old")))
        before = fingerprint.receipt("alpha")

        with mock.patch.object(extension_list.fingerprint, "write_receipt",
                               side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                extension_list.install(self.listed(version="1.1.0"),
                                       Client(archive=_archive("alpha", "1.1.0")))

        self.assertEqual(self.installed_marker(), "old")
        self.assertEqual(fingerprint.receipt("alpha"), before)

    def test_work_folders_a_dead_install_left_are_cleared_and_a_recent_one_is_not(self) -> None:
        self.root.mkdir(exist_ok=True)
        stale, recent = self.root / ".install-dead", self.root / ".remove-running"
        for one in (stale, recent):
            (one / "new").mkdir(parents=True)
        old = time.time() - extension_list.STALE_WORK_S - 60
        os.utime(stale, (old, old))

        extension_list.install(self.listed(), Client(archive=_archive()))

        self.assertFalse(stale.exists())
        self.assertTrue(recent.exists())

    def test_a_bad_archive_leaves_the_installed_copy_alone(self) -> None:
        extension_list.install(self.listed(), Client(archive=_archive(marker="old")))

        for archive in (io.BytesIO(b"not a zip"), _archive("beta")):
            with self.subTest(archive=type(archive)):
                with self.assertRaises(extension_list.ExtensionListError):
                    extension_list.install(self.listed(), Client(archive=archive))
                self.assertEqual(self.installed_marker(), "old")
                self.assertEqual([one.name for one in self.official.iterdir()], ["alpha"])

    def test_one_that_needs_a_newer_vpinfe_is_refused_before_anything_is_downloaded(self) -> None:
        client = Client(archive=_archive())

        with self.assertRaises(extension_list.ExtensionListError):
            extension_list.install(self.listed(needs="99.0.0"), client)

        self.assertEqual(client.downloads, [])
        self.assertFalse(self.official.exists() and any(self.official.iterdir()))

    def test_an_archive_larger_than_an_extension_can_be_is_refused_and_keeps_the_copy(self) -> None:
        extension_list.install(self.listed(), Client(archive=_archive(marker="old")))
        archive = _archive(marker="new")
        client = Client(archive=archive)
        too_big = zipfile.ZipFile(archive, "a")
        too_big.writestr("extension-alpha-main/big.bin", b"0" * 4096)
        too_big.close()
        archive.seek(0)

        for limit in ("UNPACKED_LIMIT", "FILE_LIMIT"):
            with self.subTest(limit=limit), mock.patch.object(extension_list, limit, 3):
                with self.assertRaises(extension_list.ExtensionListError):
                    extension_list.install(self.listed(), client)
                self.assertEqual(self.installed_marker(), "old")

        self.assertEqual(client.limits, [extension_list.ARCHIVE_LIMIT] * 2)

    def test_the_archive_is_the_one_for_the_ref_the_list_names(self) -> None:
        client = Client(archive=_archive())
        extension_list.install(self.listed(ref="v2.0.0"), client)
        self.assertEqual(client.downloads, [f"{REPO}/archive/v2.0.0.zip"])


class UninstallTests(Case):
    def test_it_removes_the_folder_and_forgets_the_receipt(self) -> None:
        extension_list.install(self.listed(), Client(archive=_archive()))

        self.assertTrue(extension_list.uninstall("alpha"))

        self.assertFalse((self.official / "alpha").exists())
        self.assertIsNone(fingerprint.receipt("alpha"))
        self.assertEqual([one.name for one in self.root.iterdir() if one.name.startswith(".")], [])

    def test_it_never_reaches_outside_the_official_folder(self) -> None:
        keep = self.root / "keep"
        keep.mkdir()
        (keep / "precious.txt").write_text("mine", encoding="utf-8")
        self.official.mkdir()

        for name in ("../keep", "..", "keep/../../keep", "ALPHA", ""):
            with self.subTest(name=name):
                self.assertFalse(extension_list.uninstall(name))

        self.assertTrue((keep / "precious.txt").is_file())

    def test_a_name_that_is_not_installed_is_not_removed(self) -> None:
        self.assertFalse(extension_list.uninstall("alpha"))


class KeptListTests(Case):
    ENTRY = {"name": "alpha", "url": REPO, "ref": "main"}
    MANIFEST = {"name": "alpha", "version": "1.2.0", "display_name": "Alpha"}

    def test_a_check_keeps_what_the_list_and_each_manifest_say(self) -> None:
        client = Client([self.ENTRY], {REPO: self.MANIFEST})

        made = extension_list.refresh(client)
        kept = extension_list.held()

        assert kept is not None
        self.assertEqual(kept, made)
        found = kept.get("alpha")
        assert found is not None
        self.assertEqual((found.version, found.display_name, found.repository),
                         ("1.2.0", "Alpha", REPO))

    def test_a_name_the_list_gives_twice_means_its_first_entry(self) -> None:
        other = {"name": "alpha", "url": "https://github.com/example/other", "ref": "main"}
        client = Client([self.ENTRY, other], {REPO: self.MANIFEST,
                                              other["url"]: self.MANIFEST})

        extension_list.refresh(client)
        kept = extension_list.held()

        assert kept is not None
        self.assertEqual([(one.name, one.repository) for one in kept.entries],
                         [("alpha", REPO)])

    def test_a_failed_check_keeps_the_copy_already_there(self) -> None:
        extension_list.refresh(Client([self.ENTRY], {REPO: self.MANIFEST}))
        before = extension_list.held()

        class Down(Client):
            def fetch_json(self, url: str) -> dict:
                raise ThemeRegistryError("offline")

        with self.assertRaises(ThemeRegistryError):
            extension_list.refresh(Down())
        with self.assertRaises(extension_list.ExtensionListError):
            extension_list.refresh(mock.Mock(fetch_json=mock.Mock(return_value={"extensions": 3})))

        self.assertEqual(extension_list.held(), before)

    def test_a_manifest_that_cannot_be_read_keeps_what_the_last_check_held(self) -> None:
        """Dropping it would make an installed extension look as if it had left the list."""
        beta = {"name": "beta", "url": "https://github.com/example/extension-beta"}
        manifests = {REPO: self.MANIFEST,
                     beta["url"]: {"name": "beta", "version": "2.0.0"}}
        extension_list.refresh(Client([self.ENTRY, beta], manifests))

        kept = extension_list.refresh(Client([self.ENTRY, beta], {REPO: self.MANIFEST}))

        self.assertEqual({one.name: one.version for one in kept.entries},
                         {"alpha": "1.2.0", "beta": "2.0.0"})

    def test_a_check_that_read_no_manifest_fails_and_keeps_the_copy(self) -> None:
        extension_list.refresh(Client([self.ENTRY], {REPO: self.MANIFEST}))
        before = extension_list.held()

        with self.assertRaises(extension_list.ExtensionListError):
            extension_list.refresh(Client([self.ENTRY], {}))

        self.assertEqual(extension_list.held(), before)

    def test_an_entry_over_plain_http_is_not_listed(self) -> None:
        entry = {**self.ENTRY, "url": "http://github.com/example/extension-alpha"}
        kept = extension_list.refresh(Client([entry, self.ENTRY], {REPO: self.MANIFEST}))
        self.assertEqual([one.repository for one in kept.entries], [REPO])

    def test_what_an_entry_needs_is_judged_against_the_vpinfe_running_now(self) -> None:
        entry = {**self.ENTRY, "min_vpinfe": "3.1.0"}
        extension_list.refresh(Client([entry], {REPO: self.MANIFEST}))

        for running, needs in (("3.0.0", "3.1.0"), ("3.1.0", "")):
            with self.subTest(running=running), \
                    mock.patch.object(extension_list, "get_version", return_value=running):
                found = (extension_list.held() or mock.Mock()).get("alpha")
                assert found is not None
                self.assertEqual(found.needs, needs)

    def test_an_unreadable_copy_reads_as_none_rather_than_raising(self) -> None:
        paths.EXTENSION_LIST_PATH.parent.mkdir(parents=True)
        paths.EXTENSION_LIST_PATH.write_text("{not json", encoding="utf-8")
        self.assertIsNone(extension_list.held())

    def test_an_entry_whose_manifest_names_another_extension_is_left_out(self) -> None:
        other = {"name": "beta", "version": "1.0.0"}
        kept = extension_list.refresh(Client([self.ENTRY], {REPO: other}))
        self.assertEqual(kept.entries, ())

    def test_one_manifest_that_cannot_be_read_does_not_fail_the_others(self) -> None:
        beta = {"name": "beta", "url": "https://github.com/example/extension-beta"}
        kept = extension_list.refresh(Client([self.ENTRY, beta], {REPO: self.MANIFEST}))
        self.assertEqual([one.name for one in kept.entries], ["alpha"])

    def test_an_entry_that_asks_for_a_newer_vpinfe_is_marked_and_offers_no_update(self) -> None:
        entry = {**self.ENTRY, "min_vpinfe": "99.0.0"}
        kept = extension_list.refresh(Client([entry], {REPO: self.MANIFEST}))
        found = kept.get("alpha")
        assert found is not None
        self.assertEqual(found.needs, "99.0.0")

        self.assertEqual(extension_list.updates({"alpha": "1.0.0"}, kept), {})

    def test_an_update_is_a_higher_version_of_something_installed(self) -> None:
        kept = extension_list.refresh(Client([self.ENTRY], {REPO: self.MANIFEST}))

        self.assertEqual(list(extension_list.updates({"alpha": "1.0.0"}, kept)), ["alpha"])
        self.assertEqual(extension_list.updates({"alpha": "1.2.0"}, kept), {})
        self.assertEqual(extension_list.updates({}, kept), {})
        self.assertEqual(extension_list.updates({"alpha": "1.0.0"}, None), {})


if __name__ == "__main__":
    unittest.main()
