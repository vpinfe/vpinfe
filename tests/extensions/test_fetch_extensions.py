"""Protects: (d) a build includes the extensions the list names at the version it names, and
fails rather than ship without one or with half of them.

Nothing under the output folder changes unless every included extension was fetched, so a
failed fetch cannot leave a build with a new copy of one and an old copy of the other.
"""

from __future__ import annotations

import importlib.util
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from common.extensions import contract
from common.online import extension_list
from common.online.theme_registry_client import ThemeRegistryClient, ThemeRegistryError
from tests.support.identity import IDENTITY

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "fetch_extensions.py"
LIST_URL = extension_list.OFFICIAL_LIST


def _load():
    spec = importlib.util.spec_from_file_location("fetch_extensions_script", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fetch_extensions = _load()


def _repo(name: str) -> str:
    return f"https://github.com/example/extension-{name}"


def _manifest(name: str, version: str) -> dict:
    return {"name": name, "version": version, "requires_platform": contract.PLATFORM_ABI,
            **IDENTITY}


def _archive(name: str, version: str, marker: str) -> io.BytesIO:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr(f"extension-{name}-main/extension.json",
                         json.dumps(_manifest(name, version)))
        archive.writestr(f"extension-{name}-main/marker.txt", marker)
    out.seek(0)
    return out


class Client(ThemeRegistryClient):
    def __init__(self, names: dict[str, str], archives: dict[str, io.BytesIO | Exception],
                 minimums: dict[str, str] | None = None) -> None:
        super().__init__()
        self.names = names
        self.archives = archives
        self.minimums = minimums or {}

    def fetch_json(self, url: str) -> dict:
        if url == LIST_URL:
            return {"extensions": [{"name": name, "url": _repo(name), "ref": "main",
                                    **({"min_vpinfe": self.minimums[name]}
                                       if name in self.minimums else {})}
                                   for name in self.names]}
        for name, version in self.names.items():
            if url.startswith(_repo(name)):
                return _manifest(name, version)
        raise ThemeRegistryError("not found")

    def fetch_any(self, url: str) -> dict:
        return {}

    def download_zip(self, url: str, limit: int | None = None) -> io.BytesIO:
        for name, archive in self.archives.items():
            if url.startswith(_repo(name)):
                if isinstance(archive, Exception):
                    raise archive
                return archive
        raise ThemeRegistryError("not found")


class FetchIncludedTests(unittest.TestCase):
    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.out = Path(folder.name) / "extensions"
        running = mock.patch.object(extension_list, "get_version", return_value="3.0.0")
        running.start()
        self.addCleanup(running.stop)

    def both(self) -> Client:
        return Client({"alpha": "1.2.0", "beta": "2.0.0"},
                      {"alpha": _archive("alpha", "1.2.0", "a"),
                       "beta": _archive("beta", "2.0.0", "b")})

    def fetch(self, client: Client) -> dict[str, str]:
        return fetch_extensions.fetch_included(client, LIST_URL, self.out,
                                               ("alpha", "beta"))

    def test_each_included_extension_is_put_in_place_at_the_version_the_list_names(self) -> None:
        versions = self.fetch(self.both())

        self.assertEqual(versions, {"alpha": "1.2.0", "beta": "2.0.0"})
        self.assertEqual((self.out / "alpha" / "marker.txt").read_text(encoding="utf-8"), "a")
        self.assertEqual((self.out / "beta" / "marker.txt").read_text(encoding="utf-8"), "b")
        self.assertTrue((self.out / "beta" / "extension.json").is_file())

    def test_a_copy_already_there_is_replaced_not_merged_with(self) -> None:
        (self.out / "alpha").mkdir(parents=True)
        (self.out / "alpha" / "stale.txt").write_text("old", encoding="utf-8")

        self.fetch(self.both())

        self.assertFalse((self.out / "alpha" / "stale.txt").exists())

    def test_an_extension_the_list_does_not_name_fails_the_build(self) -> None:
        client = Client({"alpha": "1.2.0"}, {"alpha": _archive("alpha", "1.2.0", "a")})

        with self.assertRaises(fetch_extensions.FetchError) as caught:
            self.fetch(client)

        self.assertIn("beta", str(caught.exception))
        self.assertFalse(self.out.exists())

    def test_one_that_needs_a_newer_vpinfe_fails_the_build(self) -> None:
        client = self.both()
        client.minimums = {"beta": "9.0.0"}

        with self.assertRaises(fetch_extensions.FetchError) as caught:
            self.fetch(client)

        self.assertIn("beta", str(caught.exception))
        self.assertFalse(self.out.exists())

    def test_a_failure_on_the_second_leaves_the_first_where_it_was(self) -> None:
        (self.out / "alpha").mkdir(parents=True)
        (self.out / "alpha" / "marker.txt").write_text("kept", encoding="utf-8")
        client = self.both()
        client.archives["beta"] = ThemeRegistryError("connection lost")

        with self.assertRaises(ThemeRegistryError):
            self.fetch(client)

        self.assertEqual((self.out / "alpha" / "marker.txt").read_text(encoding="utf-8"), "kept")
        self.assertFalse((self.out / "beta").exists())

    def test_an_archive_holding_another_extension_fails_the_build(self) -> None:
        client = self.both()
        client.archives["beta"] = _archive("gamma", "2.0.0", "b")

        with self.assertRaises(extension_list.ExtensionListError):
            self.fetch(client)

        self.assertFalse(self.out.exists())


if __name__ == "__main__":
    unittest.main()
