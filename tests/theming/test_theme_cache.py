"""The theme list kept between reads, and when each theme last changed."""

from __future__ import annotations

import json
import unittest
from configparser import ConfigParser
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest.mock import patch

from common import timestamps
from common.online import cache_stamps, theme_dates, theme_ops, theme_sync
from common.online.themes import ThemeRegistry
from tests.support.library import TempTree
from tests.support.stamps import OwnStamps


def _registry(updated: str = "", ref: str = "HEAD") -> ThemeRegistry:
    registry = ThemeRegistry()
    registry.themes_index = {"Revolution": {"url": "https://github.com/o/r"}}
    registry.origins = {"Revolution": "https://x.net/themes.json"}
    registry.themes = {"Revolution": {
        "registry_info": {"url": "https://github.com/o/r"}, "source": "https://x.net/themes.json",
        "manifest": {"name": "Revolution", "version": "1.2"},
        "ref": ref, "updated": updated}}
    return registry


class WhenAThemeLastChanged(unittest.TestCase):
    def test_github_answers_for_the_ref_and_the_date_comes_back_in_utc(self) -> None:
        asked = []

        def fetch(url: str) -> dict:
            asked.append(url)
            return {"commit": {"committer": {"date": "2026-04-29T13:26:53+02:00"}}}

        said = theme_dates.committed_at("https://github.com/owner/repo", "v2", fetch)

        self.assertEqual(["https://api.github.com/repos/owner/repo/commits/v2"], asked)
        self.assertEqual(timestamps.epoch_to_iso(timestamps.iso_to_epoch(
            "2026-04-29T11:26:53Z")), said)

    def test_forgejo_is_asked_for_its_default_branch_first(self) -> None:
        asked = []

        def fetch(url: str):
            asked.append(url)
            if url.endswith("/repos/owner/repo"):
                return {"default_branch": "main"}
            return [{"commit": {"committer": {"date": "2026-09-01T10:00:00Z"}}}]

        said = theme_dates.committed_at("https://git.example.net/owner/repo", "HEAD", fetch)

        self.assertIn("sha=main", asked[-1])
        self.assertTrue(said.startswith("2026-09-01"))

    def test_a_host_that_will_not_answer_leaves_it_blank(self) -> None:
        def refuse(url: str) -> dict:
            raise RuntimeError("403 rate limited")

        self.assertEqual("", theme_dates.committed_at("https://github.com/o/r", "", refuse))


class TheListKeptBetweenReads(TempTree):
    def setUp(self) -> None:
        super().setUp()
        self.kept = self.root / "cache" / "themes.json"
        self.sources = {"registries": ["https://x.net/themes.json"], "repositories": []}
        for target, value in (("common.online.theme_ops.THEME_CACHE_PATH", self.kept),):
            patcher = patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        sources_patcher = patch("common.online.theme_ops._sources_now", lambda: self.sources)
        sources_patcher.start()
        self.addCleanup(sources_patcher.stop)

    def test_what_was_read_comes_back_the_same(self) -> None:
        theme_ops._keep(_registry("2026-04-29T11:26:53Z", "v2"), self.sources)

        back = theme_ops._cached()

        self.assertIsNotNone(back)
        assert back is not None
        entry = back.themes["Revolution"]
        self.assertEqual(("v2", "2026-04-29T11:26:53Z", "1.2"),
                         (entry["ref"], entry["updated"], entry["manifest"]["version"]))

    def test_changed_sources_read_again_rather_than_answer_from_the_old_ones(self) -> None:
        theme_ops._keep(_registry(), self.sources)
        self.sources = {"registries": ["https://y.net/themes.json"], "repositories": []}

        self.assertIsNone(theme_ops._cached())

    def test_a_date_the_host_withheld_is_the_one_it_gave_last(self) -> None:
        fresh = _registry("")
        theme_ops._carry_dates(fresh, _registry("2026-04-29T11:26:53Z"))

        self.assertEqual("2026-04-29T11:26:53Z", fresh.themes["Revolution"]["updated"])

    def test_a_new_release_does_not_take_the_old_one_s_date(self) -> None:
        fresh = _registry("", ref="v3")
        theme_ops._carry_dates(fresh, _registry("2026-04-29T11:26:53Z", ref="v2"))

        self.assertEqual("", fresh.themes["Revolution"]["updated"])


class TheListHeldInMemory(TempTree):
    def setUp(self) -> None:
        super().setUp()
        self.sources = {"registries": ["https://x.net/themes.json"], "repositories": []}
        self.asked: list[ThemeRegistry] = []
        self.before: list[ThemeRegistry | None] = []
        for target, value in (("common.online.theme_ops.THEME_CACHE_PATH",
                               self.root / "cache" / "themes.json"),
                              ("common.online.theme_ops._sources_now", lambda: self.sources),
                              ("common.online.theme_ops.theme_sync.stamp", lambda: None),
                              ("common.online.theme_ops.get_ini_config", ConfigParser),
                              ("common.online.theme_ops.theme_service.load_registry",
                               self._read)):
            patcher = patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        for name in ("_registry", "_held_for"):
            cleared = patch.object(theme_ops, name, None)
            cleared.start()
            self.addCleanup(cleared.stop)

    def _read(self, previous: ThemeRegistry | None = None) -> ThemeRegistry:
        fresh = _registry()
        self.asked.append(fresh)
        self.before.append(previous)
        return fresh

    def test_sources_unchanged_are_answered_from_memory(self) -> None:
        first = theme_ops._loaded()

        self.assertIs(first, theme_ops._loaded())
        self.assertEqual(1, len(self.asked))

    def test_a_source_added_since_is_read_at_the_next_listing(self) -> None:
        first = theme_ops._loaded()
        self.sources = {**self.sources, "repositories": ["https://github.com/o/r"]}

        second = theme_ops._loaded()

        self.assertIsNot(first, second)
        self.assertEqual(2, len(self.asked))
        self.assertIs(first, self.before[1])
        self.assertIs(second, theme_ops._loaded())
        self.assertEqual(2, len(self.asked))

    def test_the_copy_kept_is_for_the_sources_the_read_used(self) -> None:
        theme_ops._loaded()

        kept = json.loads(theme_ops.THEME_CACHE_PATH.read_text())

        self.assertEqual(self.sources, kept["sources"])


class TheSchedule(OwnStamps):
    def setUp(self) -> None:
        super().setUp()
        held = TemporaryDirectory()
        self.addCleanup(held.cleanup)
        self.themes = Path(held.name) / "themes.json"
        self.themes.write_text("{}", encoding="utf-8")
        patcher = patch("common.online.theme_sync.THEME_CACHE_PATH", self.themes)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _config(self, refresh: str, checked: str = "") -> Any:
        config = ConfigParser()
        config.add_section(theme_sync.SECTION)
        config.set(theme_sync.SECTION, "refresh", refresh)
        if checked:
            cache_stamps.put(theme_sync.SECTION, read=checked)
        return config

    def test_never_read_is_due_and_never_is_not(self) -> None:
        self.assertTrue(theme_sync.due(self._config("daily")))
        self.assertFalse(theme_sync.due(self._config("never")))

    def test_a_read_within_the_day_is_not_due_and_one_a_day_old_is(self) -> None:
        stamp = "2026-09-20T12:00:00Z"
        was = timestamps.iso_to_epoch(stamp) or 0
        config = self._config("daily", stamp)
        self.assertFalse(theme_sync.due(config, now=was + 3600))
        self.assertTrue(theme_sync.due(config, now=was + 86400))

    def test_a_list_that_is_gone_is_due_however_recently_it_was_read(self) -> None:
        stamp = "2026-09-20T12:00:00Z"
        was = timestamps.iso_to_epoch(stamp) or 0
        config = self._config("daily", stamp)
        self.themes.unlink()
        self.assertTrue(theme_sync.due(config, now=was + 3600))


if __name__ == "__main__":
    unittest.main()
