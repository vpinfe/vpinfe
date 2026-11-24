"""Installing a theme must not destroy a folder the user owns.

Two ways it did. `remove_existing_install` deleted every entry whose name merely
started with the repo's, and `install_zip` then rmtree'd the destination outright, so
a registry key colliding with a local theme erased it silently. Both are data loss and
neither asked, which is why these are pinned rather than left to review.
"""

from __future__ import annotations

import io
import json
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from common.config.paths import ASIDE_SUFFIX
from common.online import themes
from common.online.theme_installer import ThemeInstallStore
from common.security import accepted
from tests.support.accepted import isolated

BASE_URL = "https://github.com/someone/Reference"


def _archive(folder: str = "Reference-master", version: str = "2.0.0") -> io.BytesIO:
    """A GitHub source zip: one top-level <repo>-<branch> folder."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(f"{folder}/manifest.json", json.dumps({"version": version}))
        archive.writestr(f"{folder}/index_playfield.html", "<html></html>")
    buffer.seek(0)
    return buffer


def _theme(root: Path, name: str, marker: str = "mine") -> Path:
    folder = root / name
    folder.mkdir(parents=True)
    (folder / "manifest.json").write_text(json.dumps({"version": "1.0.0"}))
    (folder / "marker.txt").write_text(marker, encoding="utf-8")
    return folder


class ThemeInstallStoreTests(unittest.TestCase):
    def test_a_key_that_leaves_the_themes_folder_is_refused(self) -> None:
        with TemporaryDirectory() as tmp:
            store = ThemeInstallStore(str(Path(tmp) / "themes"))
            for key in ("../outside", "/etc", "a/b", "..", ""):
                with self.subTest(key=key), self.assertRaises(ValueError):
                    store._path(key)

    def test_replacing_a_theme_keeps_the_folder_it_replaced(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _theme(root, "Reference", marker="the user's edits")
            store = ThemeInstallStore(str(root))

            store.install_zip("Reference", BASE_URL, _archive())

            self.assertEqual(store.installed_version("Reference"), "2.0.0")
            kept = root / f"Reference{ASIDE_SUFFIX}" / "marker.txt"
            self.assertTrue(kept.exists(), "the replaced folder must survive")
            self.assertEqual(kept.read_text(encoding="utf-8"), "the user's edits")

    def test_a_theme_sharing_a_prefix_is_left_alone(self) -> None:
        """`Reference-mine` used to go with `Reference`, on a startswith match."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _theme(root, "Reference-mine", marker="not the one being installed")
            store = ThemeInstallStore(str(root))

            store.install_zip("Reference", BASE_URL, _archive())

            survivor = root / "Reference-mine" / "marker.txt"
            self.assertTrue(survivor.exists(), "an unrelated theme must not be removed")
            self.assertEqual(survivor.read_text(encoding="utf-8"),
                             "not the one being installed")

    def test_the_promoted_folder_is_the_one_this_extraction_created(self) -> None:
        """Found by diffing the directory - the name match is what went wrong."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _theme(root, "Reference-leftover", marker="older, unrelated")
            store = ThemeInstallStore(str(root))

            store.install_zip("Reference", BASE_URL, _archive())

            self.assertTrue((root / "Reference" / "index_playfield.html").exists())
            self.assertEqual((root / "Reference-leftover" / "marker.txt")
                             .read_text(encoding="utf-8"), "older, unrelated")

    def test_a_failed_install_puts_the_old_theme_back(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _theme(root, "Reference", marker="still here afterwards")
            store = ThemeInstallStore(str(root))

            with self.assertRaises(zipfile.BadZipFile):
                store.install_zip("Reference", BASE_URL, io.BytesIO(b"not a zip"))

            restored = root / "Reference" / "marker.txt"
            self.assertTrue(restored.exists(), "a failed install must not lose the theme")
            self.assertEqual(restored.read_text(encoding="utf-8"),
                             "still here afterwards")

    def test_a_set_aside_folder_is_not_mistaken_for_the_install(self) -> None:
        """It shares the theme's name, so the prefix match would claim it."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _theme(root, f"Reference{ASIDE_SUFFIX}")
            store = ThemeInstallStore(str(root))

            self.assertIsNone(store.installed_folder("Reference", BASE_URL))

    def test_a_symlinked_theme_is_moved_aside_not_followed(self) -> None:
        """The reference theme's dev workflow symlinks the folder in."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            real = _theme(root / "elsewhere", "Reference", marker="the working copy")
            (root / "Reference").symlink_to(real, target_is_directory=True)
            store = ThemeInstallStore(str(root))

            store.install_zip("Reference", BASE_URL, _archive())

            self.assertEqual((real / "marker.txt").read_text(encoding="utf-8"),
                             "the working copy")
            self.assertTrue((root / f"Reference{ASIDE_SUFFIX}").is_symlink())



class ThemeStoreDetectionTests(unittest.TestCase):
    def test_theme_install_store_detects_folders_and_versions(self) -> None:
        with TemporaryDirectory() as tmp:
            themes_dir = Path(tmp)
            installed = themes_dir / "ExampleTheme"
            installed.mkdir()
            (installed / "manifest.json").write_text(
                json.dumps({"version": "1.2.3"}),
                encoding="utf-8",
            )

            store = ThemeInstallStore(str(themes_dir))

            self.assertEqual(store.installed_folder("ExampleTheme"), "ExampleTheme")
            self.assertEqual(store.installed_version("ExampleTheme"), "1.2.3")
            self.assertTrue(store.is_version_newer("1.2.4", "1.2.3"))
            self.assertTrue(store.is_version_newer("1.3.0-beta", "1.2"))
            self.assertFalse(store.is_version_newer("1.2.0", "1.2"))


class MinimumVersionGateTests(unittest.TestCase):
    """A theme states the oldest build it runs on. Nothing was checking it at install.

    The frontend's gate decides which contract a theme *gets* once it is installed. That
    is no help when the theme needs a build newer than this one: it arrives, replaces a
    working theme, and renders against a contract this build does not serve. On a
    cabinet that is a black screen after an unattended auto-update.
    """

    def setUp(self) -> None:
        isolated(self)
        accepted.accept_theme("Fancy", "")

    def _registry(self, min_vpinfe):
        registry = themes.ThemeRegistry.__new__(themes.ThemeRegistry)
        registry.themes = {"Fancy": {
            "manifest": {"version": "2.0", "min_vpinfe": min_vpinfe},
            "registry_info": {}, "ref": "HEAD"}}
        registry._base_url = lambda info: "http://example.invalid"
        registry._get_installed_version = lambda key: None
        registry.downloads = []
        registry._download_zip = lambda url: registry.downloads.append(url)
        return registry

    def test_a_theme_from_an_address_nobody_accepted_downloads_nothing(self) -> None:
        registry = self._registry("3.0")
        registry.themes["Fancy"]["source"] = "https://git.example.net/someone/themes.json"

        with mock.patch.object(themes, "get_version", return_value="3.0.1"):
            with self.assertRaises(themes.ThemeNotAcceptedError):
                registry.install_theme("Fancy")

        self.assertEqual(registry.downloads, [])

    def test_a_theme_needing_a_newer_build_is_refused(self) -> None:
        registry = self._registry("4.0")

        with mock.patch.object(themes, "get_version", return_value="3.0.1"):
            with self.assertRaises(themes.ThemeVersionError) as caught:
                registry.install_theme("Fancy")

        self.assertIn("4.0", str(caught.exception))
        self.assertIn("3.0.1", str(caught.exception), "say what is running, not just what "
                                                      "was wanted")

    def test_it_refuses_before_downloading_anything(self) -> None:
        """Ordered ahead of the fetch on purpose - a refusal after the download has
        already spent the bandwidth and, on a slow share, the wait."""
        registry = self._registry("4.0")

        with mock.patch.object(themes, "get_version", return_value="3.0.1"):
            with self.assertRaises(themes.ThemeVersionError):
                registry.install_theme("Fancy")

        self.assertEqual(registry.downloads, [])

    def test_a_new_enough_build_passes_the_gate(self) -> None:
        registry = self._registry("3.0")

        with mock.patch.object(themes, "get_version", return_value="3.0.1"):
            with self.assertRaises(Exception) as caught:
                registry.install_theme("Fancy")

        self.assertNotIsInstance(caught.exception, themes.ThemeVersionError)

    def test_a_beta_build_passes_the_gate_for_its_own_version(self) -> None:
        for running, needs in (("v3.0-beta.1", "3.0"), ("v3.0.0-beta.1", "3.0.0"),
                               ("3.0", "3.0.0")):
            with self.subTest(running=running, needs=needs):
                registry = self._registry(needs)
                with mock.patch.object(themes, "get_version", return_value=running):
                    with self.assertRaises(Exception) as caught:
                        registry.install_theme("Fancy")
                self.assertNotIsInstance(caught.exception, themes.ThemeVersionError)

    def test_a_theme_that_states_nothing_is_not_gated(self) -> None:
        """Saying nothing means contract 1, which every build serves."""
        registry = self._registry(None)

        with mock.patch.object(themes, "get_version", return_value="3.0.1"):
            with self.assertRaises(Exception) as caught:
                registry.install_theme("Fancy")

        self.assertNotIsInstance(caught.exception, themes.ThemeVersionError)


class ThemesPutThereByHand(unittest.TestCase):
    def _registry(self, root: Path) -> themes.ThemeRegistry:
        registry = themes.ThemeRegistry.__new__(themes.ThemeRegistry)
        registry.themes = {"Reference": {"registry_info": {"theme_base_url": BASE_URL},
                                         "manifest": {"version": "1.0.0"}}}
        registry.themes_dir = str(root)
        registry.store = ThemeInstallStore(str(root))
        registry._base_url = lambda info: info.get("theme_base_url")  # type: ignore[method-assign, assignment, return-value]
        return registry

    def test_only_a_folder_no_source_accounts_for_is_one(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _theme(root, "Reference")
            _theme(root, f"Reference{ASIDE_SUFFIX}")
            _theme(root, "Mine")
            (root / "not-a-theme").mkdir()

            self.assertEqual(["Mine"], list(self._registry(root).local_themes()))

    def test_it_counts_as_installed(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _theme(root, "Mine")

            self.assertTrue(self._registry(root).is_installed("Mine"))
            self.assertFalse(self._registry(root).is_installed("Elsewhere"))


if __name__ == "__main__":
    unittest.main()
