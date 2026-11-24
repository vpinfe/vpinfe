"""Protects: (e) the wheel fetched is the one that runs on the system asking, and a pure Python
one is shared; (d) a fetch that fails part way leaves no dependency folder behind, so the next
start does not put a half-unpacked one on the import path.

Also pins that a wheel which brings a module VPinFE has, or needs a dependency the extension
does not list, is refused before anything of it is kept.
"""

from __future__ import annotations

import hashlib
import io
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from packaging.tags import Tag

from common.extensions import dependencies
from common.extensions.dependencies import Dependency
from common.i18n import t
from common.online import extension_dependencies as fetching
from common.online.extension_dependencies import DependencyError

HOST = "https://files.pythonhosted.org/packages/ab/cd"
LINUX = [Tag("cp312", "cp312", "manylinux_2_17_x86_64"), Tag("cp312", "none", "any"),
         Tag("py3", "none", "any")]
MAC = [Tag("cp312", "cp312", "macosx_14_0_arm64"), Tag("py3", "none", "any")]
WINDOWS = [Tag("cp312", "cp312", "win_amd64"), Tag("py3", "none", "any")]


def _wheel(name: str, version: str, *, top: str | None = None, needs: tuple[str, ...] = (),
           ) -> bytes:
    top = top or name.replace("-", "_")
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr(f"{top}/__init__.py", "VALUE = 1\n")
        metadata = f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n"
        metadata += "".join(f"Requires-Dist: {line}\n" for line in needs)
        archive.writestr(f"{name.replace('-', '_')}-{version}.dist-info/METADATA", metadata)
    return out.getvalue()


def _listing(name: str, version: str, files: dict[str, bytes]) -> dict:
    return {"urls": [
        {"packagetype": "bdist_wheel", "filename": filename, "url": f"{HOST}/{filename}",
         "digests": {"sha256": hashlib.sha256(data).hexdigest()}}
        for filename, data in files.items()]}


class FakeIndex:
    """Stands in for the index and its file host, and counts what was asked of them."""

    def __init__(self) -> None:
        self.listings: dict[str, dict] = {}
        self.files: dict[str, bytes] = {}
        self.asked: list[str] = []

    def add(self, name: str, version: str, files: dict[str, bytes]) -> None:
        self.listings[f"{fetching.INDEX}/{name}/{version}/json"] = _listing(name, version, files)
        self.files.update({f"{HOST}/{filename}": data for filename, data in files.items()})

    def json(self, url: str) -> object:
        self.asked.append(url)
        if url not in self.listings:
            import requests
            response = requests.Response()
            response.status_code = 404
            raise requests.HTTPError(response=response)
        return self.listings[url]

    def bytes(self, url: str, limit: int) -> bytes:
        self.asked.append(url)
        return self.files[url]


class FetchCase(unittest.TestCase):
    def setUp(self) -> None:
        self.index = FakeIndex()
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.root = Path(self.dir.name) / "dependencies"
        for target, fake in (("_get_json", self.index.json), ("_get_bytes", self.index.bytes)):
            patcher = mock.patch.object(fetching, target, fake)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(fetching, "sys_tags", mock.Mock(return_value=LINUX))
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch.object(dependencies, "sys_tags", mock.Mock(return_value=LINUX))
        patcher.start()
        self.addCleanup(patcher.stop)

    def leftovers(self) -> list[str]:
        if not self.root.exists():
            return []
        return sorted(one.relative_to(self.root).as_posix() for one in self.root.rglob("*")
                      if one.is_file() or not any(one.iterdir()))


class WhichWheel(FetchCase):
    def test_each_system_is_given_the_wheel_built_for_it(self) -> None:
        files = {"crypt-1.0-cp312-cp312-manylinux_2_17_x86_64.whl": b"linux",
                 "crypt-1.0-cp312-cp312-macosx_14_0_arm64.whl": b"mac",
                 "crypt-1.0-cp312-cp312-win_amd64.whl": b"win"}
        self.index.add("crypt", "1.0", files)
        one = Dependency("crypt", "1.0")
        for tags, wanted in ((LINUX, "manylinux_2_17_x86_64"), (MAC, "macosx_14_0_arm64"),
                             (WINDOWS, "win_amd64")):
            wheel = fetching.choose(one, tags)
            self.assertIn(wanted, wheel.filename)
            self.assertEqual(wheel.folder, f"cp312-{wanted}")

    def test_a_pure_python_wheel_goes_in_the_folder_every_system_shares(self) -> None:
        self.index.add("plain", "2.0", {"plain-2.0-py3-none-any.whl": b"x"})
        for tags in (LINUX, MAC, WINDOWS):
            self.assertEqual(fetching.choose(Dependency("plain", "2.0"), tags).folder,
                             dependencies.PURE)

    def test_the_closest_match_wins_over_a_pure_one(self) -> None:
        self.index.add("both", "1.0", {"both-1.0-py3-none-any.whl": b"p",
                                       "both-1.0-cp312-cp312-win_amd64.whl": b"w"})
        self.assertIn("win_amd64", fetching.choose(Dependency("both", "1.0"), WINDOWS).filename)
        self.assertIn("py3-none-any", fetching.choose(Dependency("both", "1.0"), LINUX).filename)

    def test_a_file_that_needs_another_python_is_not_chosen(self) -> None:
        self.index.add("new", "1.0", {"new-1.0-py3-none-any.whl": b"w"})
        for entry in self.index.listings[f"{fetching.INDEX}/new/1.0/json"]["urls"]:
            entry["requires_python"] = ">=99"
        with self.assertRaises(DependencyError):
            fetching.choose(Dependency("new", "1.0"), LINUX)

    def test_a_dependency_with_no_wheel_for_this_system_says_so(self) -> None:
        self.index.add("only", "1.0", {"only-1.0-cp312-cp312-win_amd64.whl": b"w"})
        with self.assertRaises(DependencyError) as raised:
            fetching.choose(Dependency("only", "1.0"), LINUX)
        self.assertEqual(str(raised.exception),
                         t("error.extensions.dependency_no_wheel", dependency="only==1.0"))

    def test_a_dependency_the_index_does_not_have_says_so(self) -> None:
        with self.assertRaises(DependencyError) as raised:
            fetching.choose(Dependency("absent", "1.0"), LINUX)
        self.assertEqual(str(raised.exception),
                         t("error.extensions.dependency_not_found", dependency="absent==1.0"))

    def test_a_file_from_another_host_is_never_taken(self) -> None:
        self.index.add("odd", "1.0", {"odd-1.0-py3-none-any.whl": b"x"})
        listing = self.index.listings[f"{fetching.INDEX}/odd/1.0/json"]
        listing["urls"][0]["url"] = "https://example.com/odd-1.0-py3-none-any.whl"
        with self.assertRaises(DependencyError):
            fetching.choose(Dependency("odd", "1.0"), LINUX)
        listing["urls"][0]["url"] = f"http://{fetching.FILE_HOST}/odd-1.0-py3-none-any.whl"
        with self.assertRaises(DependencyError):
            fetching.choose(Dependency("odd", "1.0"), LINUX)


class WhatIsKept(FetchCase):
    def add_plain(self, name: str, version: str = "1.0", **kwargs) -> Dependency:
        filename = f"{name}-{version}-py3-none-any.whl"
        self.index.add(name, version, {filename: _wheel(name, version, **kwargs)})
        return Dependency(name, version)

    def test_a_fetched_dependency_is_unpacked_where_the_device_finds_it(self) -> None:
        one = self.add_plain("plain")
        folders = fetching.fetch([one], self.root)
        self.assertEqual(folders, [self.root / dependencies.PURE / "plain-1.0"])
        self.assertTrue((folders[0] / "plain" / "__init__.py").is_file())
        self.assertEqual(dependencies.folder_of(one, self.root), folders[0])

    def test_a_dependency_already_here_costs_no_connection(self) -> None:
        one = self.add_plain("plain")
        fetching.fetch([one], self.root)
        self.index.asked.clear()
        fetching.fetch([one], self.root)
        self.assertEqual(self.index.asked, [])

    def test_a_wheel_that_does_not_match_its_checksum_is_refused(self) -> None:
        one = self.add_plain("plain")
        url = f"{HOST}/plain-1.0-py3-none-any.whl"
        self.index.files[url] = b"something else"
        with self.assertRaises(DependencyError) as raised:
            fetching.fetch([one], self.root)
        self.assertEqual(str(raised.exception),
                         t("error.extensions.dependency_bad_file", dependency="plain==1.0"))
        self.assertEqual(self.leftovers(), [])

    def test_a_failure_part_way_removes_what_this_fetch_made(self) -> None:
        good = self.add_plain("good")
        bad = self.add_plain("bad", needs=("unlisted>=1",))
        with self.assertRaises(DependencyError):
            fetching.fetch([good, bad], self.root)
        self.assertEqual(self.leftovers(), [])

    def test_a_failure_leaves_what_was_there_before(self) -> None:
        good = self.add_plain("good")
        fetching.fetch([good], self.root)
        bad = self.add_plain("bad", needs=("unlisted>=1",))
        with self.assertRaises(DependencyError):
            fetching.fetch([good, bad], self.root)
        self.assertIsNotNone(dependencies.folder_of(good, self.root))
        self.assertEqual([one for one in self.leftovers() if not one.startswith("py3")], [])

    def test_a_wheel_that_needs_a_listed_dependency_is_kept(self) -> None:
        base = self.add_plain("base")
        top = self.add_plain("top", needs=("base>=1", "requests>=2"))
        self.assertEqual(len(fetching.fetch([base, top], self.root)), 2)

    def test_a_needed_dependency_at_a_version_it_does_not_accept_is_refused(self) -> None:
        base = self.add_plain("base", "1.0")
        top = self.add_plain("top", needs=("base>=2",))
        with self.assertRaises(DependencyError):
            fetching.fetch([base, top], self.root)

    def test_a_marker_that_does_not_hold_here_asks_for_nothing(self) -> None:
        one = self.add_plain("top", needs=('base>=1; sys_platform == "nowhere"',))
        self.assertEqual(len(fetching.fetch([one], self.root)), 1)

    def test_a_wheel_that_brings_a_module_vpinfe_has_is_refused(self) -> None:
        for taken in ("requests", "json", "common"):
            with self.subTest(taken):
                one = self.add_plain("fake", top=taken)
                with self.assertRaises(DependencyError) as raised:
                    fetching.fetch([one], self.root)
                self.assertEqual(str(raised.exception), t(
                    "error.extensions.dependency_shipped", dependency="fake==1.0", module=taken))
                self.assertEqual(self.leftovers(), [])

    def test_a_wheel_with_too_many_files_is_refused(self) -> None:
        one = self.add_plain("plain")
        with mock.patch.object(fetching, "FILE_LIMIT", 1):
            with self.assertRaises(DependencyError):
                fetching.fetch([one], self.root)
        self.assertEqual(self.leftovers(), [])

    def test_a_download_that_is_not_a_wheel_is_refused(self) -> None:
        data = b"not a zip"
        self.index.add("junk", "1.0", {"junk-1.0-py3-none-any.whl": data})
        with self.assertRaises(DependencyError):
            fetching.fetch([Dependency("junk", "1.0")], self.root)
        self.assertEqual(self.leftovers(), [])


class WhatIsSwept(FetchCase):
    def test_a_folder_that_cannot_be_read_stops_the_sweep(self) -> None:
        self.index.add("keep", "1.0", {"keep-1.0-py3-none-any.whl": _wheel("keep", "1.0")})
        fetching.fetch([Dependency("keep", "1.0")], self.root)
        unreadable = Path(self.dir.name) / "extensions"
        unreadable.mkdir()
        with mock.patch.object(fetching.host, "SEARCH_PATH", [unreadable]), \
                mock.patch.object(Path, "iterdir", side_effect=PermissionError("denied")), \
                self.assertLogs(fetching.logger, "WARNING"):
            self.assertEqual(fetching.sweep_unused(), [])
        self.assertIsNotNone(dependencies.folder_of(Dependency("keep", "1.0"), self.root))

    def test_a_dependency_no_extension_names_is_removed_and_the_rest_stays(self) -> None:
        for name in ("keep", "drop"):
            self.index.add(name, "1.0", {f"{name}-1.0-py3-none-any.whl": _wheel(name, "1.0")})
        fetching.fetch([Dependency("keep", "1.0"), Dependency("drop", "1.0")], self.root)
        removed = fetching.sweep([Dependency("keep", "1.0")], self.root)
        self.assertEqual([one.name for one in removed], ["drop-1.0"])
        self.assertIsNotNone(dependencies.folder_of(Dependency("keep", "1.0"), self.root))
        self.assertIsNone(dependencies.folder_of(Dependency("drop", "1.0"), self.root))

    def test_a_leftover_without_its_receipt_is_removed(self) -> None:
        half = self.root / dependencies.PURE / "half-1.0"
        half.mkdir(parents=True)
        (half / "half.py").write_text("x", encoding="utf-8")
        (self.root / f"{fetching.WORK_PREFIX}abc").mkdir()
        fetching.sweep([Dependency("half", "1.0")], self.root)
        self.assertEqual(self.leftovers(), [])


if __name__ == "__main__":
    unittest.main()
