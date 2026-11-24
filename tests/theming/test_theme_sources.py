"""Themes come from wherever the user says, not from one hardcoded catalog.

Two lists in the config file: registries, which name many themes, and repositories, which
are each one theme. Only the VPinFE Theme List installs a theme on its own.

What is pinned here is the behavior a second source has to have to be worth adding: one
bad source cannot cost you the others, a name means one thing, and an install that lists
nothing still starts.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from configparser import ConfigParser
from unittest import mock

from common.config.config_store import ConfigStore
from common.i18n import t
from common.online import theme_ops, theme_refs, theme_source_checks, theme_sources
from common.online.theme_registry_client import ThemeRegistryError
from common.online.themes import ThemeRegistry, ThemeVersionError
from common.security import accepted
from tests.support.accepted import isolated

STOCK = "https://raw.githubusercontent.com/vpinfe/themes/main/themes.json"
MINE = "https://git.example.net/someone/vpinfe-theme-reference"


def _config(registries: str = "", repositories: str = "") -> ConfigParser:
    parser = ConfigParser()
    parser.add_section(theme_sources.SECTION)
    parser.set(theme_sources.SECTION, "registries", registries)
    parser.set(theme_sources.SECTION, "repositories", repositories)
    return parser


class NamingTests(unittest.TestCase):
    """What a theme installs as is its key, never what a url looks like or a label says."""

    def test_a_repo_takes_the_name_its_author_put_in_the_manifest(self) -> None:
        entry = theme_sources.repository_entry(MINE)
        self.assertEqual(theme_sources.key_of(MINE, entry, {"name": "Reference"}), "Reference")

    def test_a_list_entry_installs_under_its_key_whatever_its_name_says(self) -> None:
        self.assertEqual(
            theme_sources.key_of("aurora", {"url": MINE, "name": "Aurora Borealis"},
                                 {"name": "X"}),
            "aurora")

    def test_the_old_registry_is_still_keyed_by_the_key(self) -> None:
        """The registry says `cab` where the manifest says `Cab`, and the key is what
        installs."""
        self.assertEqual(theme_sources.key_of("cab", {"theme_base_url": MINE}, {"name": "Cab"}),
                         "cab")

    def test_a_manifest_with_no_usable_name_falls_back_to_the_url(self) -> None:
        entry = theme_sources.repository_entry(MINE)
        self.assertEqual(theme_sources.key_of(MINE, entry, {"name": "  "}), MINE)

    def test_a_theme_is_shown_under_the_entrys_name_then_the_manifests_then_its_key(self) -> None:
        self.assertEqual(theme_sources.title_of("cab", {"name": "Cab Plus"}, {"name": "Cab"}),
                         "Cab Plus")
        self.assertEqual(theme_sources.title_of("cab", {"url": MINE}, {"name": "Cab"}), "Cab")
        self.assertEqual(theme_sources.title_of("cab", {"name": " "}, {"name": ""}), "cab")

    def test_a_repo_stands_in_for_a_registry_entry(self) -> None:
        """A registry entry is a name and a url, so one repo is already that shape."""
        self.assertEqual(ThemeRegistry._base_url(theme_sources.repository_entry(MINE)), MINE)


class PinTests(unittest.TestCase):
    def test_a_ref_can_be_pinned_onto_the_url(self) -> None:
        self.assertEqual(theme_sources.split_ref(f"{MINE}#v1.4"), (MINE, "v1.4"))
        self.assertEqual(theme_sources.split_ref(f"{MINE}/#v1.4"), (MINE, "v1.4"))
        self.assertEqual(theme_sources.split_ref(MINE), (MINE, ""))

    def test_the_entry_carries_the_pin_only_when_there_is_one(self) -> None:
        named = theme_sources.NAMED_BY_MANIFEST
        self.assertEqual(theme_sources.repository_entry(MINE), {"url": MINE, named: True})
        self.assertEqual(theme_sources.repository_entry(f"{MINE}#v1.4"),
                         {"url": MINE, named: True, "ref": "v1.4"})


class ConfigTests(unittest.TestCase):
    def test_both_lists_are_read_from_the_config(self) -> None:
        sources = theme_sources.from_config(_config(f"{STOCK},https://b.net/t.json", MINE))
        self.assertEqual(sources.registries, (STOCK, "https://b.net/t.json"))
        self.assertEqual(sources.repositories, (MINE,))

    def test_an_install_that_lists_nothing_is_not_an_error(self) -> None:
        sources = theme_sources.from_config(_config())
        self.assertEqual((sources.registries, sources.repositories), ((), ()))


class MergeTests(unittest.TestCase):
    def test_sources_combine_into_one_index(self) -> None:
        index = theme_sources.merge([("a", {"one": {"url": "u1"}}),
                                     ("b", {"two": {"url": "u2"}})])
        self.assertEqual(sorted(index), ["one", "two"])

    def test_the_first_mention_of_a_name_wins(self) -> None:
        """Two sources disagreeing about a name is the user's call, not ours to resolve."""
        with self.assertLogs("vpinfe.common.online.theme_sources", "WARNING") as logged:
            index = theme_sources.merge([("mine", {"Revolution": {"url": "mine"}}),
                                         ("stock", {"Revolution": {"url": "stock"}})])
        self.assertEqual(index["Revolution"]["url"], "mine")
        self.assertIn("Revolution", logged.output[0])

    def test_where_each_kept_name_came_from_is_recorded_beside_it(self) -> None:
        origins: dict = {}
        with self.assertLogs("vpinfe.common.online.theme_sources", "WARNING"):
            theme_sources.merge([("a", {"one": {"url": "u1"}}),
                                 ("b", {"one": {"url": "u2"}, "two": {"url": "u3"}})],
                                origins)
        self.assertEqual(origins, {"one": "a", "two": "b"})

    def test_merging_does_not_write_into_the_entries(self) -> None:
        """`registry_info` is handed to the installer, so bookkeeping stays out of it."""
        entry = {"url": "u1"}
        index = theme_sources.merge([("a", {"one": entry})])
        self.assertEqual(index["one"], {"url": "u1"})


class LoadTests(unittest.TestCase):
    """`load_registry` over several sources, with the network answered from a dict."""

    def setUp(self) -> None:
        isolated(self)

    def _accept(self, registry: ThemeRegistry, key: str = "Reference") -> None:
        accepted.accept_theme(key, str(registry.themes[key]["source"]))

    def _registry(self, sources, responses: dict) -> ThemeRegistry:
        registry = ThemeRegistry(sources=sources)

        def fetch(url):
            if url not in responses:
                raise ThemeRegistryError(f"unreachable: {url}")
            return responses[url]

        registry._fetch_json = fetch  # type: ignore[method-assign]
        registry._fetch_any = fetch  # type: ignore[method-assign]
        return registry

    def test_a_repository_needs_no_catalog_to_be_offered(self) -> None:
        """The whole point: a theme nobody published is still installable, and it lands
        under the name its author gave it rather than one derived from the url."""
        manifest = {"name": "Reference", "version": "0.1.0", "author": "a",
                    "description": "d", "preview_image": "p.png", "type": "both",
                    "windows": ["playfield"]}
        registry = self._registry(theme_sources.ThemeSources(repositories=(MINE,)),
                                  {f"{MINE}/raw/HEAD/manifest.json": manifest})
        registry.load_registry()
        self.assertEqual(list(registry.themes_index), [MINE])  # provisional
        registry.load_theme_manifests()
        self.assertEqual(list(registry.themes), ["Reference"])  # settled

    def test_a_pinned_ref_is_what_gets_installed(self) -> None:
        manifest = self._manifest("Reference", "1.4")
        registry = self._registry(theme_sources.ThemeSources(repositories=(f"{MINE}#v1.4",)),
                                  {f"{MINE}/raw/v1.4/manifest.json": manifest})
        registry.load_registry()
        registry.load_theme_manifests()
        downloads: list[str] = []
        registry._download_zip = lambda url: downloads.append(url) or b""  # type: ignore[method-assign, assignment, func-returns-value, return-value]
        registry.store.install_zip = lambda *args: None  # type: ignore[method-assign]
        self._accept(registry)
        registry.install_theme("Reference")
        self.assertEqual(downloads, [f"{MINE}/archive/v1.4.zip"])

    def test_a_users_own_repo_is_version_gated_like_any_other(self) -> None:
        """Where a theme came from must not decide what it is allowed to install: the
        manifest's `min_vpinfe` is read for a repository exactly as for a list's theme."""
        manifest = {**self._manifest("Reference", "9.0"), "min_vpinfe": "9.9.9"}
        registry = self._registry(theme_sources.ThemeSources(repositories=(MINE,)),
                                  {f"{MINE}/raw/HEAD/manifest.json": manifest})
        registry.load_registry()
        registry.load_theme_manifests()
        downloads: list[str] = []
        registry._download_zip = lambda url: downloads.append(url) or b""  # type: ignore[method-assign, assignment, func-returns-value, return-value]
        self._accept(registry)
        with mock.patch("common.online.themes.get_version", return_value="3.0.1"):
            with self.assertRaises(ThemeVersionError):
                registry.install_theme("Reference")
        self.assertEqual(downloads, [])

    def _manifest(self, name: str, version: str = "1.0") -> dict:
        return {"name": name, "version": version, "author": "a", "description": "d",
                "preview_image": "p.png", "type": "both", "windows": ["playfield"]}

    def test_a_repo_that_turns_out_to_be_a_registry_theme_loses_to_it(self) -> None:
        """The clash only becomes visible once the manifest names it, so it is caught
        there rather than at merge, where a repository is still just a url."""
        repo = "https://git.example.net/me/my-fork"
        registry = self._registry(
            theme_sources.ThemeSources(registries=(STOCK,), repositories=(repo,)),
            {STOCK: {"themes": {"Revolution": {"url": "https://x.net/o/r"}}},
             f"{repo}/raw/HEAD/manifest.json": self._manifest("Revolution", "9.9"),
             "https://x.net/o/r/raw/HEAD/manifest.json": self._manifest("Revolution", "1.0")})
        registry.load_registry()
        with self.assertLogs("vpinfe.common.online.themes", "WARNING") as logged:
            registry.load_theme_manifests()
        self.assertEqual(list(registry.themes), ["Revolution"])
        self.assertEqual(registry.themes["Revolution"]["manifest"]["version"], "9.9")
        self.assertIn("keeping the first", logged.output[0])

    def test_a_theme_knows_which_source_it_came_from(self) -> None:
        repo = "https://git.example.net/me/my-cab"
        registry = self._registry(
            theme_sources.ThemeSources(registries=(STOCK,), repositories=(repo,)),
            {STOCK: {"themes": {"Revolution": {"url": "https://x.net/o/r"}}},
             f"{repo}/raw/HEAD/manifest.json": self._manifest("Cab"),
             "https://x.net/o/r/raw/HEAD/manifest.json": self._manifest("Revolution")})
        registry.load_registry()
        registry.load_theme_manifests()
        self.assertEqual(registry.themes["Revolution"]["source"], STOCK)
        self.assertEqual(registry.themes["Cab"]["source"], repo)

    def test_which_source_wins_does_not_depend_on_who_answered_first(self) -> None:
        """Results are collected in source order, not completion order - otherwise a
        slow network would decide which of two same-named themes you got."""
        repo = "https://git.example.net/me/my-fork"
        responses = {STOCK: {"themes": {"Revolution": {"url": "https://x.net/o/r"}}},
                     f"{repo}/raw/HEAD/manifest.json": self._manifest("Revolution", "9.9"),
                     "https://x.net/o/r/raw/HEAD/manifest.json": self._manifest("Revolution")}
        for _ in range(8):
            registry = self._registry(
                theme_sources.ThemeSources(registries=(STOCK,), repositories=(repo,)),
                responses)
            registry.load_registry()
            with self.assertLogs("vpinfe.common.online.themes", "WARNING"):
                registry.load_theme_manifests()
            self.assertEqual(registry.themes["Revolution"]["manifest"]["version"], "9.9")

    def test_a_case_only_twin_is_called_out(self) -> None:
        """`cab` in the registry beside a manifest saying `Cab` installs both."""
        repo = "https://git.example.net/me/my-cab"
        registry = self._registry(
            theme_sources.ThemeSources(registries=(STOCK,), repositories=(repo,)),
            {STOCK: {"themes": {"cab": {"url": "https://x.net/o/cab"}}},
             f"{repo}/raw/HEAD/manifest.json": self._manifest("Cab"),
             "https://x.net/o/cab/raw/HEAD/manifest.json": self._manifest("cab")})
        registry.load_registry()
        with self.assertLogs("vpinfe.common.online.themes", "WARNING") as logged:
            registry.load_theme_manifests()
        self.assertEqual(sorted(registry.themes), ["Cab", "cab"])
        self.assertIn("only in case", logged.output[0])

    def test_a_dead_source_does_not_cost_the_user_the_others(self) -> None:
        with self.assertLogs("vpinfe.common.online.themes", "ERROR"):
            registry = self._registry(
                theme_sources.ThemeSources(registries=("https://gone.net/t.json", STOCK)),
                {STOCK: {"themes": {"Revolution": {"url": "u"}}}})
            registry.load_registry()
        self.assertEqual(list(registry.themes_index), ["Revolution"])

    def test_a_source_with_no_themes_object_is_reported_not_raised(self) -> None:
        with self.assertLogs("vpinfe.common.online.themes", "ERROR"):
            registry = self._registry(
                theme_sources.ThemeSources(registries=(STOCK, "https://b.net/t.json")),
                {STOCK: {"nothing": {}}, "https://b.net/t.json": {"themes": {"T": {"url": "u"}}}})
            registry.load_registry()
        self.assertEqual(list(registry.themes_index), ["T"])

    def test_two_registries_naming_the_same_theme_settle_at_merge(self) -> None:
        """Registry keys are known without the network, so that clash resolves early."""
        other = "https://b.net/t.json"
        with self.assertLogs("vpinfe.common.online.theme_sources", "WARNING"):
            registry = self._registry(
                theme_sources.ThemeSources(registries=(STOCK, other)),
                {STOCK: {"themes": {"Revolution": {"url": "first"}}},
                 other: {"themes": {"Revolution": {"url": "second"}}}})
            registry.load_registry()
        self.assertEqual(registry.themes_index["Revolution"]["url"], "first")

    def test_listing_no_sources_leaves_an_empty_index_rather_than_failing(self) -> None:
        """An offline cab that dropped the stock registry still runs its installed theme."""
        registry = self._registry(theme_sources.ThemeSources(), {})
        registry.load_registry()
        registry.load_theme_manifests()
        self.assertEqual(registry.themes_index, {})
        self.assertEqual(registry.themes, {})

    def test_a_theme_no_source_offers_is_refused_in_words(self) -> None:
        registry = self._registry(theme_sources.ThemeSources(), {})
        registry.load_registry()
        registry.load_theme_manifests()
        with mock.patch.object(theme_ops, "_loaded", return_value=registry), \
                self.assertRaises(theme_ops.InstallFailedError) as raised:
            theme_ops.install("Revolution")

        self.assertEqual(str(raised.exception),
                         t("error.themes.not_offered", key="Revolution"))

    def test_every_source_failing_is_still_an_error(self) -> None:
        """Sources were listed and none answered - that is a broken install, not a choice."""
        with self.assertLogs("vpinfe.common.online.themes", "ERROR"):
            registry = self._registry(
                theme_sources.ThemeSources(registries=("https://gone.net/t.json",)), {})
            with self.assertRaises(ThemeRegistryError):
                registry.load_registry()


class WhichListInstallsOnItsOwn(unittest.TestCase):
    """Only the VPinFE Theme List's `default_install` marks act: another list cannot put a
    theme on the machine, or make one impossible to delete."""

    THIRD_PARTY = "https://git.example.net/someone/vpinfe-themes/raw/branch/main/themes.json"
    BASE = "https://git.example.net/someone/neon"
    MANIFEST = {"name": "Neon", "version": "1.0", "author": "a", "description": "d",
                "preview_image": "p.png", "type": "both", "windows": ["playfield"]}

    def _read(self, address: str, mark: bool = True, *, at_start: bool = True) -> ThemeRegistry:
        entry = {"url": self.BASE, "default_install": mark}
        responses: dict[str, dict] = {
            address: {"themes": {"Neon": entry}},
            f"{self.BASE}/raw/HEAD/manifest.json": self.MANIFEST}

        def fetch(url: str) -> dict:
            if url not in responses:
                raise ThemeRegistryError(f"unreachable: {url}")
            return responses[url]

        registry = ThemeRegistry(sources=theme_sources.ThemeSources(registries=(address,)))
        registry._fetch_json = fetch  # type: ignore[method-assign]
        registry._fetch_any = fetch  # type: ignore[method-assign]
        registry.load_registry()
        registry.load_theme_manifests(default_only=at_start, dates=False)
        return registry

    def _installed_by_start(self, registry: ThemeRegistry) -> list[str]:
        with mock.patch.object(ThemeRegistry, "install_theme") as install:
            registry.auto_install_defaults()
        return [call.args[0] for call in install.call_args_list]

    def test_the_stock_list_is_the_address_the_setting_defaults_to(self) -> None:
        self.assertEqual(STOCK, theme_sources.stock_list())

    def test_a_first_run_and_a_2x_ini_both_read_the_stock_list(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            fresh = ConfigStore(os.path.join(folder, "fresh.ini"))
            from_2x = os.path.join(folder, "from_2x.ini")
            with open(from_2x, "w", encoding="utf-8") as handle:
                handle.write("[Settings]\ntheme = Revolution\n")

            for store in (fresh, ConfigStore(from_2x)):
                self.assertEqual(theme_sources.from_config(store).registries, (STOCK,))

    def test_the_stock_list_is_still_the_stock_list_under_another_spelling(self) -> None:
        shown = "https://github.com/vpinfe/themes/blob/main/themes.json"

        self.assertTrue(theme_sources.is_stock(shown))
        self.assertFalse(theme_sources.is_stock(self.THIRD_PARTY))
        self.assertFalse(theme_sources.is_stock(""))

    def test_a_third_party_lists_mark_installs_nothing(self) -> None:
        at_start = self._read(self.THIRD_PARTY)
        listed = self._read(self.THIRD_PARTY, at_start=False)

        self.assertEqual([], list(at_start.themes))
        self.assertEqual([], self._installed_by_start(listed))

    def test_a_theme_from_a_third_party_list_can_be_deleted(self) -> None:
        registry = self._read(self.THIRD_PARTY, at_start=False)

        self.assertFalse(registry.installs_on_its_own("Neon"))
        with mock.patch.object(registry, "get_installed_folder", return_value="neon"), \
                mock.patch.object(registry.store, "delete") as delete:
            registry.delete_theme("Neon")
        delete.assert_called_once_with("neon")

    def test_the_stock_lists_mark_still_installs_the_theme_and_keeps_it(self) -> None:
        registry = self._read(STOCK)

        self.assertEqual(["Neon"], self._installed_by_start(registry))
        with mock.patch.object(registry, "get_installed_folder", return_value="neon"), \
                self.assertRaises(ThemeRegistryError):
            registry.delete_theme("Neon")

    def test_the_mark_survives_a_restart_from_the_kept_read(self) -> None:
        held = ThemeRegistry()
        held.restore(json.loads(json.dumps(self._read(STOCK).snapshot())))

        self.assertTrue(held.installs_on_its_own("Neon"))

    def test_an_unmarked_theme_from_the_stock_list_installs_nothing(self) -> None:
        self.assertEqual([], self._installed_by_start(self._read(STOCK, mark=False)))

    def test_the_check_names_marks_for_the_stock_list_only(self) -> None:
        themes = {"Neon": {"url": self.BASE, "default_install": True}}

        self.assertEqual(["Neon"], theme_source_checks.listed(STOCK, themes)["installs"])
        self.assertEqual([], theme_source_checks.listed(self.THIRD_PARTY, themes)["installs"])


class WhenADateIsAsked(unittest.TestCase):
    BASE = "https://github.com/o/theme"

    def _read(self, ref: str, *, dates: bool = True,
              previous: ThemeRegistry | None = None) -> tuple[ThemeRegistry, list[str]]:
        entry = {"url": self.BASE, "default_install": True, **({"ref": ref} if ref else {})}
        responses: dict[str, dict] = {
            STOCK: {"themes": {"Theme": entry}},
            theme_refs.raw_url(self.BASE, ref or "HEAD", "manifest.json"): {
                "name": "Theme", "version": "1.0", "author": "a", "description": "d",
                "preview_image": "p.png", "type": "both", "windows": ["playfield"]},
            f"https://api.github.com/repos/o/theme/commits/"
            f"{theme_refs.bare_ref(ref)}": {"commit": {"committer": {
                "date": "2026-09-01T10:00:00Z"}}},
        }
        asked: list[str] = []

        def fetch(url: str) -> dict:
            asked.append(url)
            if url not in responses:
                raise ThemeRegistryError(f"unreachable: {url}")
            return responses[url]

        registry = ThemeRegistry(sources=theme_sources.ThemeSources(registries=(STOCK,)))
        registry._fetch_json = fetch  # type: ignore[method-assign]
        registry._fetch_any = fetch  # type: ignore[method-assign]
        registry.load_registry()
        registry.load_theme_manifests(default_only=not dates, dates=dates, previous=previous)
        return registry, [url for url in asked if "/commits/" in url]

    def _kept(self, ref: str, updated: str) -> ThemeRegistry:
        kept = ThemeRegistry()
        kept.themes = {"Theme": {"ref": ref, "updated": updated}}
        return kept

    def test_a_start_asks_no_date(self) -> None:
        registry, dated = self._read("", dates=False)
        self.assertEqual(dated, [])
        self.assertEqual(registry.themes["Theme"]["updated"], "")

    def test_a_read_asks_for_a_theme_on_its_default_branch(self) -> None:
        registry, dated = self._read("", previous=self._kept("HEAD", "2026-01-01T00:00:00Z"))
        self.assertEqual(len(dated), 1)
        self.assertEqual(registry.themes["Theme"]["updated"], "2026-09-01T10:00:00Z")

    def test_a_tag_keeps_the_date_it_had_without_asking(self) -> None:
        tag = "refs/tags/v1"
        registry, dated = self._read(tag, previous=self._kept(tag, "2026-01-01T00:00:00Z"))
        self.assertEqual(dated, [])
        self.assertEqual(registry.themes["Theme"]["updated"], "2026-01-01T00:00:00Z")

    def test_a_moved_tag_is_asked(self) -> None:
        registry, dated = self._read("refs/tags/v2",
                                     previous=self._kept("refs/tags/v1", "2026-01-01T00:00:00Z"))
        self.assertEqual(len(dated), 1)
        self.assertEqual(registry.themes["Theme"]["updated"], "2026-09-01T10:00:00Z")

    def test_a_bare_ref_is_not_taken_for_a_tag(self) -> None:
        _, dated = self._read("v1", previous=self._kept("v1", "2026-01-01T00:00:00Z"))
        self.assertEqual(len(dated), 1)


if __name__ == "__main__":
    unittest.main()
