"""What each theme source said when it was read, and what adding an address would be.

A source is a theme list or a single theme. Reading one says what it is and whether it
is usable: the states a person sees on Settings > Themes, and the check behind the Add
dialog, which finds out without saving.
"""

from __future__ import annotations

import json
import unittest
from configparser import ConfigParser
from typing import Any
from unittest import mock

import requests

from common.online import theme_ops, theme_sources
from common.online import theme_source_checks as checks
from common.online.theme_registry_client import ThemeRegistryError
from common.online.themes import ThemeRegistry
from common.security.http_client import HostQuietError

STOCK = "https://raw.githubusercontent.com/vpinfe/themes/main/themes.json"
OTHER = "https://git.example.net/someone/themes/raw/branch/main/themes.json"
SINGLE = "https://git.example.net/someone/vpinfe-theme-reference"
REPO = "https://github.com/someone/pack"


def _manifest(name: str, version: str = "1.0", **fields: Any) -> dict:
    return {"name": name, "version": version, "author": "a", "description": "d",
            "preview_image": "p.png", "type": "both", "windows": ["playfield"], **fields}


def _http(status: int) -> ThemeRegistryError:
    """The error the client raises for an answer of `status`."""
    response = requests.Response()
    response.status_code = status
    error = ThemeRegistryError(f"HTTP {status}")
    error.__cause__ = requests.HTTPError(response=response)
    return error


def _unreachable() -> ThemeRegistryError:
    error = ThemeRegistryError("no route")
    error.__cause__ = requests.ConnectionError("no route")
    return error


def _config(registries: tuple = (), repositories: tuple = ()) -> ConfigParser:
    parser = ConfigParser()
    parser.add_section(theme_sources.SECTION)
    parser.set(theme_sources.SECTION, "registries", ",".join(registries))
    parser.set(theme_sources.SECTION, "repositories", ",".join(repositories))
    return parser


class _Net:
    """The network, answered from a dict. A value that is an exception is raised, and
    a url not in it is a 404."""

    def __init__(self, answers: dict, pages: set | None = None) -> None:
        self.answers = answers
        self.pages = pages if pages is not None else set()

    def fetch(self, url: str) -> Any:
        found = self.answers.get(url, _http(404))
        if isinstance(found, Exception):
            raise found
        return found

    def exists(self, url: str) -> bool:
        return url in self.pages

    def __enter__(self) -> _Net:
        self._patches = [
            mock.patch.object(ThemeRegistry, "_fetch_json", lambda _r, url: self.fetch(url)),
            mock.patch.object(ThemeRegistry, "_fetch_any", lambda _r, url: self.fetch(url)),
            mock.patch.object(ThemeRegistry, "_exists", lambda _r, url: self.exists(url)),
        ]
        for one in self._patches:
            one.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        for one in self._patches:
            one.stop()


class ReadingAnAddress(unittest.TestCase):
    """The address table: what a pasted address is stored as."""

    def _stored(self, text: str) -> tuple[str, bool]:
        where = theme_sources.parse(text)
        assert where is not None, text
        return where.url, where.is_file

    def test_a_repository_page_is_the_repository(self) -> None:
        self.assertEqual(self._stored("https://github.com/o/r"), ("https://github.com/o/r", False))
        self.assertEqual(self._stored("https://github.com/o/r.git"),
                         ("https://github.com/o/r", False))
        self.assertEqual(self._stored("https://github.com/o/r/"), ("https://github.com/o/r", False))

    def test_a_tree_page_pins_the_repository_to_that_ref(self) -> None:
        self.assertEqual(self._stored("https://github.com/o/r/tree/v2"),
                         ("https://github.com/o/r#v2", False))

    def test_a_blob_page_is_the_raw_file(self) -> None:
        self.assertEqual(self._stored("https://github.com/o/r/blob/main/themes.json"),
                         ("https://raw.githubusercontent.com/o/r/main/themes.json", True))

    def test_a_raw_address_is_taken_as_pasted(self) -> None:
        self.assertEqual(self._stored(STOCK), (STOCK, True))

    def test_a_forgejo_source_page_is_its_raw_file(self) -> None:
        self.assertEqual(self._stored("https://git.example.net/o/r/src/branch/main/themes.json"),
                         ("https://git.example.net/o/r/raw/branch/main/themes.json", True))

    def test_a_forgejo_branch_page_pins_the_repository(self) -> None:
        self.assertEqual(self._stored("https://git.example.net/o/r/src/branch/dev"),
                         ("https://git.example.net/o/r#dev", False))

    def test_a_hash_pins_any_repository(self) -> None:
        self.assertEqual(self._stored(f"{SINGLE}#v1.4"), (f"{SINGLE}#v1.4", False))

    def test_what_is_no_address_is_none(self) -> None:
        for text in ("", "not an address", "ftp://x.net/o/r", "https://github.com",
                     "https://github.com/o"):
            self.assertIsNone(theme_sources.parse(text), text)

    def test_a_repository_names_the_file_it_would_hold(self) -> None:
        where = theme_sources.parse("https://github.com/o/r/tree/dev")
        assert where is not None
        self.assertEqual(theme_sources.list_file(where),
                         "https://raw.githubusercontent.com/o/r/dev/themes.json")
        where = theme_sources.parse(SINGLE)
        assert where is not None
        self.assertEqual(theme_sources.list_file(where), f"{SINGLE}/raw/HEAD/themes.json")


class WhatAReadSaid(unittest.TestCase):
    """The state a source ends up in after a real read, and the words with it."""

    def _read(self, sources: theme_sources.ThemeSources, answers: dict,
              pages: set | None = None) -> ThemeRegistry:
        registry = ThemeRegistry(sources=sources)
        with _Net(answers, pages):
            try:
                registry.load_registry()
                registry.load_theme_manifests(dates=False)
            except ThemeRegistryError:
                pass
        return registry

    def test_a_list_that_answers_is_ready_and_counts_its_themes(self) -> None:
        themes = {"A": {"url": "https://x.net/o/a"},
                  "B": {"url": "https://x.net/o/b", "default_install": True}}
        registry = self._read(theme_sources.ThemeSources(registries=(STOCK,)),
                              {STOCK: {"themes": themes},
                               "https://x.net/o/a/raw/HEAD/manifest.json": _manifest("A"),
                               "https://x.net/o/b/raw/HEAD/manifest.json": _manifest("B")})
        said = registry.reports[STOCK]
        self.assertEqual((said["kind"], said["state"], said["count"]), ("list", "ready", 2))
        self.assertEqual(said["installs"], ["B"])

    def test_a_list_nobody_answers_for_is_not_found(self) -> None:
        registry = self._read(theme_sources.ThemeSources(registries=(STOCK, OTHER)),
                              {OTHER: {"themes": {"A": {"url": "u"}}}})
        self.assertEqual(registry.reports[STOCK]["state"], checks.NOT_FOUND)

    def test_a_host_that_cannot_be_reached_is_not_answering(self) -> None:
        registry = self._read(theme_sources.ThemeSources(registries=(STOCK, OTHER)),
                              {STOCK: _unreachable(), OTHER: {"themes": {"A": {"url": "u"}}}})
        self.assertEqual(registry.reports[STOCK]["state"], checks.NOT_ANSWERING)
        self.assertTrue(registry.reports[STOCK]["reason"])

    def test_a_file_with_no_themes_is_not_a_theme(self) -> None:
        registry = self._read(theme_sources.ThemeSources(registries=(STOCK, OTHER)),
                              {STOCK: {"hello": 1}, OTHER: {"themes": {"A": {"url": "u"}}}})
        self.assertEqual(registry.reports[STOCK]["state"], checks.NOT_A_THEME)

    def test_a_theme_that_reads_is_ready_under_the_name_its_manifest_gives(self) -> None:
        registry = self._read(theme_sources.ThemeSources(repositories=(SINGLE,)),
                              {f"{SINGLE}/raw/HEAD/manifest.json": _manifest("Reference", "2.1")})
        said = registry.reports[SINGLE]
        self.assertEqual((said["kind"], said["state"], said["name"], said["version"]),
                         ("theme", "ready", "Reference", "2.1"))

    def test_a_theme_missing_manifest_fields_is_not_a_theme(self) -> None:
        bare = {"name": "Reference", "version": "1.0"}
        registry = self._read(theme_sources.ThemeSources(repositories=(SINGLE,)),
                              {f"{SINGLE}/raw/HEAD/manifest.json": bare})
        said = registry.reports[SINGLE]
        self.assertEqual(said["state"], checks.NOT_A_THEME)
        self.assertIn("author", said["reason"])

    def test_a_theme_with_a_type_that_is_no_type_is_not_a_theme(self) -> None:
        registry = self._read(theme_sources.ThemeSources(repositories=(SINGLE,)),
                              {f"{SINGLE}/raw/HEAD/manifest.json": _manifest("R", type="toaster")})
        self.assertEqual(registry.reports[SINGLE]["state"], checks.NOT_A_THEME)

    def test_a_repository_with_no_manifest_is_not_a_theme_when_the_page_exists(self) -> None:
        registry = self._read(theme_sources.ThemeSources(repositories=(SINGLE,)), {},
                              pages={SINGLE})
        said = registry.reports[SINGLE]
        self.assertEqual(said["state"], checks.NOT_A_THEME)

    def test_a_repository_with_no_page_is_not_found(self) -> None:
        registry = self._read(theme_sources.ThemeSources(repositories=(SINGLE,)), {})
        self.assertEqual(registry.reports[SINGLE]["state"], checks.NOT_FOUND)

    def test_a_theme_that_needs_a_newer_vpinfe_says_which(self) -> None:
        with mock.patch("common.online.themes.get_version", return_value="3.0.1"):
            registry = self._read(
                theme_sources.ThemeSources(repositories=(SINGLE,)),
                {f"{SINGLE}/raw/HEAD/manifest.json": _manifest("Reference", "9.0",
                                                               min_vpinfe="9.9.9")})
        said = registry.reports[SINGLE]
        self.assertEqual((said["state"], said["needs"]), (checks.NEEDS_VPINFE, "9.9.9"))
        self.assertIn("9.9.9", said["reason"])

    def test_a_host_that_is_rate_limiting_is_not_answering(self) -> None:
        limited = ThemeRegistryError("slow down")
        limited.__cause__ = HostQuietError("raw.githubusercontent.com", 4102444800.0)
        registry = self._read(theme_sources.ThemeSources(registries=(STOCK, OTHER)),
                              {STOCK: limited, OTHER: {"themes": {"A": {"url": "u"}}}})
        self.assertEqual(registry.reports[STOCK]["state"], checks.NOT_ANSWERING)

    def test_a_list_some_of_whose_themes_cannot_be_read_says_so(self) -> None:
        registry = self._read(
            theme_sources.ThemeSources(registries=(STOCK,)),
            {STOCK: {"themes": {"Good": {"url": "https://x.net/o/good"},
                                "Bad": {"url": "https://x.net/o/bad"}}},
             "https://x.net/o/good/raw/HEAD/manifest.json": _manifest("Good")})
        said = registry.reports[STOCK]
        self.assertEqual(said["state"], checks.SOME_UNREADABLE)
        self.assertIn("Bad", said["reason"])

    def test_a_list_names_the_themes_a_single_theme_took_from_it(self) -> None:
        registry = self._read(
            theme_sources.ThemeSources(registries=(STOCK,), repositories=(SINGLE,)),
            {STOCK: {"themes": {"Reference": {"url": "https://x.net/o/ref"}}},
             f"{SINGLE}/raw/HEAD/manifest.json": _manifest("Reference", "9.9"),
             "https://x.net/o/ref/raw/HEAD/manifest.json": _manifest("Reference")})
        self.assertEqual(registry.reports[SINGLE]["state"], checks.READY)
        self.assertEqual(registry.reports[STOCK]["took"], ["Reference"])

    def test_a_single_theme_named_like_an_earlier_single_theme_is_not_used(self) -> None:
        other = "https://git.example.net/someone/else"
        registry = self._read(
            theme_sources.ThemeSources(repositories=(SINGLE, other)),
            {f"{SINGLE}/raw/HEAD/manifest.json": _manifest("Reference"),
             f"{other}/raw/HEAD/manifest.json": _manifest("Reference", "9.9")})
        self.assertEqual(registry.reports[SINGLE]["state"], checks.READY)
        self.assertEqual(registry.reports[other]["state"], checks.NOT_USED)


class WhatMergingRecords(unittest.TestCase):
    def test_a_source_records_the_names_it_lost(self) -> None:
        lost: dict = {}
        with self.assertLogs("vpinfe.common.online.theme_sources", "WARNING"):
            theme_sources.merge([("a", {"one": {}}), ("b", {"one": {}, "two": {}})],
                                lost=lost)
        self.assertEqual(lost, {"b": ["one"]})


class WhatIsKept(unittest.TestCase):
    def test_what_each_source_said_survives_a_snapshot(self) -> None:
        registry = ThemeRegistry(sources=theme_sources.ThemeSources(registries=(STOCK,)))
        with _Net({STOCK: {"themes": {"A": {"url": "https://x.net/o/a"}}},
                   "https://x.net/o/a/raw/HEAD/manifest.json": _manifest("A")}):
            registry.load_registry()
            registry.load_theme_manifests(dates=False)
        again = ThemeRegistry()
        again.restore(registry.snapshot())
        self.assertEqual(again.reports, registry.reports)


class WhatTheListShows(unittest.TestCase):
    def _sources(self, registries: tuple, repositories: tuple,
                 reports: dict | None = None) -> dict:
        known = mock.Mock(reports=reports or {})
        with mock.patch.object(theme_ops, "get_ini_config",
                               return_value=_config(registries, repositories)), \
                mock.patch.object(theme_ops, "_known", return_value=known if reports else None):
            return theme_ops.sources()

    def test_a_source_never_read_is_not_checked(self) -> None:
        shown = self._sources((STOCK,), (SINGLE,))
        self.assertEqual([one["state"] for one in shown["registries"]], ["not_checked"])
        self.assertEqual(shown["registries"][0]["level"], "unknown")
        self.assertEqual(shown["repositories"][0]["kind"], "theme")

    def test_sources_come_in_the_order_they_are_configured(self) -> None:
        shown = self._sources((STOCK, OTHER), ())
        self.assertEqual([one["address"] for one in shown["registries"]], [STOCK, OTHER])

    def test_a_read_source_shows_what_it_said_and_a_level(self) -> None:
        said = checks.failed(STOCK, checks.LIST, checks.NOT_ANSWERING, "It could not be reached")
        shown = self._sources((STOCK,), (), {STOCK: said})
        self.assertEqual(shown["registries"][0]["state"], "not_answering")
        self.assertEqual(shown["registries"][0]["level"], "bad")

    def test_the_levels(self) -> None:
        self.assertEqual(checks.level(checks.READY), "")
        self.assertEqual(checks.level(checks.NOT_FOUND), "bad")
        self.assertEqual(checks.level(checks.NOT_USED), "warn")
        self.assertEqual(checks.level(checks.NOT_CHECKED), "unknown")


class WhatAddingWouldBe(unittest.TestCase):
    def _check(self, address: str, answers: dict, pages: set | None = None,
               registries: tuple = (), repositories: tuple = (),
               offered: dict | None = None) -> dict:
        known = mock.Mock(themes={key: {"source": src} for key, src in (offered or {}).items()})
        with _Net(answers, pages), \
                mock.patch.object(theme_ops, "get_ini_config",
                                  return_value=_config(registries, repositories)), \
                mock.patch.object(theme_ops, "_known", return_value=known):
            return theme_ops.check_source(address)

    def test_a_list_file_is_a_list_and_addable(self) -> None:
        said = self._check(STOCK, {STOCK: {"themes": {"A": {"url": "https://x.net/o/a"}}}})
        self.assertEqual((said["kind"], said["state"], said["addable"], said["group"]),
                         ("list", "ready", True, "registries"))
        self.assertEqual(said["address"], STOCK)

    def test_a_blob_page_is_stored_as_the_raw_file(self) -> None:
        raw = "https://raw.githubusercontent.com/o/r/main/themes.json"
        said = self._check("https://github.com/o/r/blob/main/themes.json",
                           {raw: {"themes": {"A": {"url": "u"}}}})
        self.assertEqual(said["address"], raw)

    def test_a_repository_with_a_manifest_is_a_theme(self) -> None:
        said = self._check(SINGLE, {f"{SINGLE}/raw/HEAD/manifest.json": _manifest("Reference")})
        self.assertEqual((said["kind"], said["state"], said["name"], said["group"]),
                         ("theme", "ready", "Reference", "repositories"))

    def test_a_repository_with_no_manifest_but_a_list_is_a_list(self) -> None:
        file = f"{REPO}".replace("github.com", "raw.githubusercontent.com") + "/HEAD/themes.json"
        said = self._check(REPO, {file: {"themes": {"A": {"url": "https://x.net/o/a"}}}},
                           pages={REPO})
        self.assertEqual((said["kind"], said["state"], said["address"]),
                         ("list", "ready", file))

    def test_a_repository_that_is_neither_is_refused(self) -> None:
        said = self._check(SINGLE, {}, pages={SINGLE})
        self.assertEqual(said["state"], checks.NOT_A_THEME)
        self.assertFalse(said["addable"])

    def test_an_address_with_nothing_there_is_not_found_and_refused(self) -> None:
        said = self._check(SINGLE, {})
        self.assertEqual(said["state"], checks.NOT_FOUND)
        self.assertFalse(said["addable"])

    def test_a_host_that_does_not_answer_is_still_addable(self) -> None:
        said = self._check(STOCK, {STOCK: _unreachable()})
        self.assertEqual(said["state"], checks.NOT_ANSWERING)
        self.assertTrue(said["addable"])

    def test_what_is_no_address_is_refused(self) -> None:
        said = self._check("hello", {})
        self.assertEqual(said["state"], checks.NOT_A_THEME)
        self.assertFalse(said["addable"])

    def test_an_address_already_added_is_refused(self) -> None:
        said = self._check(STOCK, {}, registries=(STOCK,))
        self.assertEqual(said["state"], checks.ALREADY_ADDED)
        self.assertFalse(said["addable"])

    def test_the_same_address_pasted_another_way_is_already_added(self) -> None:
        raw = "https://raw.githubusercontent.com/o/r/main/themes.json"
        said = self._check("https://github.com/o/r/blob/main/themes.json", {},
                           registries=(raw,))
        self.assertEqual(said["state"], checks.ALREADY_ADDED)

    def test_a_list_says_which_of_its_themes_are_already_offered(self) -> None:
        said = self._check(OTHER, {OTHER: {"themes": {"A": {"url": "u"}, "B": {"url": "v"}}}},
                           offered={"A": STOCK})
        self.assertEqual(said["took"], ["A"])

    def test_a_theme_says_which_listed_theme_it_would_replace(self) -> None:
        said = self._check(SINGLE, {f"{SINGLE}/raw/HEAD/manifest.json": _manifest("Reference")},
                           offered={"Reference": STOCK}, registries=(STOCK,))
        self.assertEqual(said["replaces"], ["Reference"])
        self.assertEqual(said["state"], checks.READY)

    def test_a_theme_another_single_theme_already_offers_is_not_used(self) -> None:
        other = "https://git.example.net/someone/else"
        said = self._check(SINGLE, {f"{SINGLE}/raw/HEAD/manifest.json": _manifest("Reference")},
                           offered={"Reference": other}, repositories=(other,))
        self.assertEqual(said["state"], checks.NOT_USED)
        self.assertTrue(said["addable"])

    def test_the_repository_page_of_a_list_already_added_is_already_added(self) -> None:
        """The stock list is held under `main`; its repository page names no branch."""
        said = self._check("https://github.com/vpinfe/themes", {},
                           pages={"https://github.com/vpinfe/themes"},
                           registries=(STOCK,))
        self.assertEqual((said["state"], said["address"]), (checks.ALREADY_ADDED, STOCK))
        self.assertFalse(said["addable"])

    def test_an_address_no_url_parser_accepts_is_refused_rather_than_raised(self) -> None:
        for text in ("https://[abc", "http://[::1", "https://exa℀mple.com/o/r"):
            with self.subTest(text=text):
                said = self._check(text, {})
                self.assertEqual(said["state"], checks.NOT_A_THEME)
                self.assertFalse(said["addable"])

    def test_a_theme_named_like_one_already_added_by_hand_is_not_used(self) -> None:
        other = "https://git.example.net/someone/else"
        said = self._check(SINGLE, {f"{SINGLE}/raw/HEAD/manifest.json": _manifest("Reference")},
                           offered={"Reference": other}, repositories=(other,))
        self.assertEqual((said["state"], said["level"]), (checks.NOT_USED, "warn"))


class WhatALastGoodReadKeeps(unittest.TestCase):
    """A source that fails contributes what it said when it last could, and its state
    says why."""

    A = "https://x.net/o/a"
    B = "https://x.net/o/b"
    GOOD = {STOCK: {"themes": {"A": {"url": A}, "B": {"url": B, "default_install": True}}},
            f"{A}/raw/HEAD/manifest.json": _manifest("A"),
            f"{B}/raw/HEAD/manifest.json": _manifest("B")}

    def _read(self, sources: theme_sources.ThemeSources, answers: dict,
              previous: ThemeRegistry | None = None) -> ThemeRegistry:
        registry = ThemeRegistry(sources=sources)
        with _Net(answers):
            try:
                registry.load_registry(previous=previous)
                registry.load_theme_manifests(dates=False, previous=previous)
            except ThemeRegistryError:
                pass
        return registry

    def _again(self, registry: ThemeRegistry) -> ThemeRegistry:
        """What a restart sees: the last read through the file it is kept in."""
        held = ThemeRegistry()
        held.restore(json.loads(json.dumps(registry.snapshot())))
        return held

    def test_a_list_that_stops_answering_keeps_its_themes(self) -> None:
        sources = theme_sources.ThemeSources(registries=(STOCK,))
        before = self._read(sources, self.GOOD)
        now = self._read(sources, {**self.GOOD, STOCK: _unreachable()}, before)
        self.assertEqual(sorted(now.themes), ["A", "B"])
        said = now.reports[STOCK]
        self.assertEqual((said["state"], said["count"], said["installs"]),
                         (checks.NOT_ANSWERING, 2, ["B"]))

    def test_a_list_that_stops_being_a_list_keeps_its_themes(self) -> None:
        sources = theme_sources.ThemeSources(registries=(STOCK,))
        before = self._read(sources, self.GOOD)
        now = self._read(sources, {**self.GOOD, STOCK: {"hello": 1}}, before)
        self.assertEqual(sorted(now.themes), ["A", "B"])
        self.assertEqual(now.reports[STOCK]["state"], checks.NOT_A_THEME)

    def test_the_last_read_comes_back_from_the_file_it_is_kept_in(self) -> None:
        sources = theme_sources.ThemeSources(registries=(STOCK,))
        before = self._again(self._read(sources, self.GOOD))
        now = self._read(sources, {STOCK: _http(404)}, before)
        self.assertEqual(sorted(now.themes), ["A", "B"])
        self.assertEqual(now.reports[STOCK]["state"], checks.NOT_FOUND)

    def test_a_theme_a_failing_source_used_to_offer_does_not_shadow_a_live_one(self) -> None:
        sources = theme_sources.ThemeSources(repositories=(SINGLE,), registries=(STOCK,))
        answers = {f"{SINGLE}/raw/HEAD/manifest.json": _manifest("A"), **self.GOOD}
        before = self._read(sources, answers)
        self.assertEqual(before.themes["A"]["source"], SINGLE)
        moved = {**self.GOOD, f"{SINGLE}/raw/HEAD/manifest.json": _unreachable()}
        now = self._read(sources, moved, before)
        self.assertEqual(now.themes["A"]["source"], STOCK)

    def test_an_address_that_changed_kind_keeps_nothing_from_the_old_one(self) -> None:
        before = self._read(theme_sources.ThemeSources(registries=(STOCK,)), self.GOOD)
        now = self._read(theme_sources.ThemeSources(repositories=(STOCK,)),
                         {f"{STOCK}/raw/HEAD/manifest.json": _unreachable()}, before)
        self.assertEqual(now.themes, {})

    def test_a_theme_that_stops_reading_stays_listed_and_the_list_says_so(self) -> None:
        sources = theme_sources.ThemeSources(registries=(STOCK,))
        before = self._read(sources, self.GOOD)
        answers = {**self.GOOD, f"{self.B}/raw/HEAD/manifest.json": _unreachable()}
        now = self._read(sources, answers, before)
        self.assertEqual(sorted(now.themes), ["A", "B"])
        self.assertEqual(now.reports[STOCK]["state"], checks.SOME_UNREADABLE)

    def test_a_single_theme_that_stops_reading_stays_listed_under_its_name(self) -> None:
        sources = theme_sources.ThemeSources(repositories=(SINGLE,))
        before = self._read(sources, {f"{SINGLE}/raw/HEAD/manifest.json":
                                      _manifest("Reference", "2.1")})
        now = self._read(sources, {f"{SINGLE}/raw/HEAD/manifest.json": _unreachable()}, before)
        self.assertEqual(list(now.themes), ["Reference"])
        said = now.reports[SINGLE]
        self.assertEqual((said["state"], said["name"], said["version"]),
                         (checks.NOT_ANSWERING, "Reference", "2.1"))

    def test_nothing_is_kept_for_a_source_no_longer_configured(self) -> None:
        before = self._read(theme_sources.ThemeSources(registries=(STOCK,)), self.GOOD)
        now = self._read(theme_sources.ThemeSources(registries=(OTHER,)),
                         {OTHER: _unreachable()}, before)
        self.assertEqual(now.themes, {})

    def test_a_list_nobody_has_read_keeps_nothing(self) -> None:
        now = self._read(theme_sources.ThemeSources(registries=(STOCK, OTHER)),
                         {STOCK: _unreachable(), OTHER: {"themes": {"A": {"url": self.A}}},
                          f"{self.A}/raw/HEAD/manifest.json": _manifest("A")},
                         ThemeRegistry())
        self.assertEqual(list(now.themes), ["A"])
        self.assertEqual(now.reports[STOCK]["count"], 0)


class WhenNothingAnswers(unittest.TestCase):
    """A read that finds no usable source builds no registry, and the page still has to
    say what each source said."""

    def test_each_source_says_why_after_a_read_that_found_nothing(self) -> None:
        config = _config(registries=(STOCK,))
        self.addCleanup(setattr, theme_ops, "_unread", {})
        self.addCleanup(setattr, theme_ops, "_registry", None)
        theme_ops._registry = None
        with _Net({STOCK: _unreachable()}), \
                mock.patch.object(theme_ops, "get_ini_config", return_value=config), \
                mock.patch("common.online.themes.get_ini_config", return_value=config), \
                mock.patch.object(theme_ops, "_cached", return_value=None):
            with self.assertRaises(theme_ops.SourceUnavailableError):
                theme_ops._readable(refresh=True)
            shown = theme_ops.sources()
        self.assertEqual((shown["registries"][0]["state"], shown["registries"][0]["level"]),
                         ("not_answering", "bad"))

    def test_a_refresh_that_finds_the_list_down_keeps_what_the_last_one_listed(self) -> None:
        config = _config(registries=(STOCK,))
        good = WhatALastGoodReadKeeps.GOOD
        self.addCleanup(setattr, theme_ops, "_unread", {})
        self.addCleanup(setattr, theme_ops, "_registry", None)
        theme_ops._registry = None
        with mock.patch.object(theme_ops, "get_ini_config", return_value=config), \
                mock.patch("common.online.themes.get_ini_config", return_value=config), \
                mock.patch.object(theme_ops, "_cached", return_value=None), \
                mock.patch.object(theme_ops, "_keep"), \
                mock.patch.object(theme_ops.theme_sync, "stamp"):
            with _Net(good):
                theme_ops._readable(refresh=True)
            with _Net({**good, STOCK: _unreachable()}):
                again = theme_ops._readable(refresh=True)
            shown = theme_ops.sources()
        self.assertEqual(sorted(again.themes), ["A", "B"])
        self.assertEqual((shown["registries"][0]["state"], shown["registries"][0]["count"]),
                         ("not_answering", 2))

    def test_a_refresh_where_every_source_failed_is_not_counted_as_a_read(self) -> None:
        config = _config(registries=(STOCK,))
        good = WhatALastGoodReadKeeps.GOOD
        self.addCleanup(setattr, theme_ops, "_unread", {})
        self.addCleanup(setattr, theme_ops, "_registry", None)
        theme_ops._registry = None
        with mock.patch.object(theme_ops, "get_ini_config", return_value=config), \
                mock.patch("common.online.themes.get_ini_config", return_value=config), \
                mock.patch.object(theme_ops, "_cached", return_value=None), \
                mock.patch.object(theme_ops, "_keep"), \
                mock.patch.object(theme_ops.theme_sync, "stamp") as stamp:
            with _Net(good):
                theme_ops._readable(refresh=True)
            self.assertEqual(stamp.call_count, 1)
            with _Net({**good, STOCK: _unreachable()}):
                theme_ops._readable(refresh=True)
        self.assertEqual(stamp.call_count, 1)

    def test_a_bad_address_in_the_config_costs_the_other_sources_nothing(self) -> None:
        registry = ThemeRegistry(sources=theme_sources.ThemeSources(
            registries=("https://[abc", STOCK)))
        with _Net({STOCK: {"themes": {"A": {"url": "https://x.net/o/a"}}}}), \
                self.assertLogs("vpinfe.common.online.themes", "ERROR"):
            registry.load_registry()
        self.assertEqual(list(registry.themes_index), ["A"])


if __name__ == "__main__":
    unittest.main()
