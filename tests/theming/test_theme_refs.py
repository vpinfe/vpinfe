"""Where a theme list entry says to read a theme from.

An entry is a key, a `url`, and optionally a `name` and a `ref`. A `ref` pins a branch or a
tag; without one the theme is read from the default branch. An author who wants a contract 2
branch beside a contract 1 master lists the branch's entry, so no file in the theme's own
repository decides anything.
"""

from __future__ import annotations

import unittest

from common.online import theme_ops, theme_refs
from common.online.theme_installer import ThemeInstallStore
from common.online.themes import ThemeRegistry

BASE = "https://github.com/someone/vpinfe-theme-example"


class RefTests(unittest.TestCase):
    def test_a_ref_is_spelled_the_one_way_both_hosts_serve(self) -> None:
        """GitHub 404s on Forgejo's raw form and Forgejo 404s on GitHub's, so a fully
        qualified ref is reduced to a bare one, which both resolve."""
        self.assertEqual(theme_refs.bare_ref("refs/heads/v2"), "v2")
        self.assertEqual(theme_refs.bare_ref("refs/tags/v1.4"), "v1.4")
        self.assertEqual(theme_refs.bare_ref("v2"), "v2")
        self.assertEqual(theme_refs.bare_ref(""), "HEAD")
        self.assertEqual(theme_refs.bare_ref("HEAD"), "HEAD")

    def test_only_a_fully_qualified_tag_is_a_tag(self) -> None:
        self.assertTrue(theme_refs.is_tag("refs/tags/v1"))
        self.assertFalse(theme_refs.is_tag("v1"))
        self.assertFalse(theme_refs.is_tag("refs/heads/v1"))
        self.assertFalse(theme_refs.is_tag(""))

    def test_a_raw_file_is_asked_for_at_the_bare_ref(self) -> None:
        self.assertEqual(theme_refs.raw_url(BASE + "/", "refs/heads/v2", "manifest.json"),
                         f"{BASE}/raw/v2/manifest.json")


class ArchiveTests(unittest.TestCase):
    def test_the_archive_follows_the_pinned_ref(self) -> None:
        self.assertEqual(ThemeInstallStore.build_zip_url(BASE, "refs/tags/v1.4"),
                         f"{BASE}/archive/v1.4.zip")

    def test_head_is_not_rewritten_to_master(self) -> None:
        """Both hosts resolve HEAD to the repo's real default branch. Assuming master
        is wrong on every repo that defaults to main."""
        self.assertEqual(ThemeInstallStore.build_zip_url(BASE, "HEAD"),
                         f"{BASE}/archive/HEAD.zip")
        self.assertEqual(ThemeInstallStore.build_zip_url(BASE), f"{BASE}/archive/HEAD.zip")


class ListShapeTests(unittest.TestCase):
    """The client reads the list it has today and the one it will have."""

    def test_a_theme_is_located_under_either_shape(self) -> None:
        self.assertEqual(ThemeRegistry._base_url({"url": BASE}), BASE)
        self.assertEqual(ThemeRegistry._base_url({"theme_base_url": BASE}), BASE)
        self.assertEqual(ThemeRegistry._base_url({}), "")


class ManifestUrlTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = ThemeRegistry()

    def test_an_entry_with_only_a_url_reads_the_default_branch(self) -> None:
        self.assertEqual(self.registry._manifest_url(BASE, {"url": BASE}),
                         f"{BASE}/raw/HEAD/manifest.json")

    def test_an_old_entry_keeps_the_manifest_url_it_names(self) -> None:
        legacy = f"{BASE}/raw/refs/heads/master/manifest.json"
        self.assertEqual(self.registry._manifest_url(
            BASE, {"theme_base_url": BASE, "theme_manifest_url": legacy}), legacy)

    def test_a_pinned_ref_is_where_the_manifest_is_read(self) -> None:
        """The pin is the author's contract 2 branch beside a contract 1 master."""
        entry = {"url": BASE, "ref": "refs/heads/v2"}
        self.assertEqual(self.registry._manifest_url(BASE, entry),
                         f"{BASE}/raw/v2/manifest.json")

    def test_a_pin_wins_over_the_manifest_url_an_old_entry_names(self) -> None:
        entry = {"theme_base_url": BASE, "ref": "v2",
                 "theme_manifest_url": f"{BASE}/raw/master/manifest.json"}
        self.assertEqual(self.registry._manifest_url(BASE, entry),
                         f"{BASE}/raw/v2/manifest.json")

    def test_a_blank_ref_pins_nothing(self) -> None:
        self.assertEqual(self.registry._ref({"url": BASE, "ref": "  "}), "HEAD")
        self.assertEqual(self.registry._ref({"url": BASE, "ref": "v2"}), "v2")


class PreviewTests(unittest.TestCase):
    """A theme that is not installed shows the picture its manifest names, from the same ref."""

    def test_a_url_entry_shows_its_picture_from_the_default_branch(self) -> None:
        self.assertEqual(theme_ops._preview("aurora", {"preview_image": "p.png"},
                                            {"url": BASE}, installed=False),
                         f"{BASE}/raw/HEAD/p.png")

    def test_a_pinned_entry_shows_its_picture_from_the_pin(self) -> None:
        self.assertEqual(theme_ops._preview("aurora", {"preview_image": "p.png"},
                                            {"url": BASE, "ref": "v2"}, installed=False),
                         f"{BASE}/raw/v2/p.png")

    def test_an_old_entry_shows_its_picture_beside_the_manifest_it_names(self) -> None:
        info = {"theme_base_url": BASE,
                "theme_manifest_url": f"{BASE}/raw/refs/heads/master/manifest.json"}
        self.assertEqual(theme_ops._preview("cab", {"preview_image": "p.png"}, info,
                                            installed=False),
                         f"{BASE}/raw/refs/heads/master/p.png")

    def test_an_installed_theme_shows_the_copy_on_disk(self) -> None:
        self.assertEqual(theme_ops._preview("aurora", {"preview_image": "p.png"},
                                            {"url": BASE}, installed=True),
                         "/themes/aurora/p.png")


if __name__ == "__main__":
    unittest.main()
