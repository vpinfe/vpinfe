"""The sources watch: what it asks, what it keeps, and when the comparison runs.

The comparison is owed from the start of the process and again whenever the spreadsheet
changes. It is started here and nowhere else; a page only shows it.
"""

from __future__ import annotations

import tempfile
import threading
import unittest
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from common.online import (
    arrivals,
    sources,
    sources_watch,
    vpinmediadb,
    vps_lens,
    vps_source,
    vpsdb_sync,
)
from common.online.sources import Listed, Source
from tests.support.sources import only


def _spreadsheet(changed: bool = False, holds: tuple[Listed, ...] = ()) -> Source:
    return Source(id=vps_source.ID, name="Spreadsheet", always_on=True,
                  refresh=lambda config: changed, listing=lambda: iter(holds))


def _site(changed: bool, holds: tuple[Listed, ...] = ()) -> Source:
    return Source(id="site", name="Site", refresh=lambda config: changed,
                  listing=lambda: iter(holds))


class _Watch(unittest.TestCase):
    def setUp(self) -> None:
        held = tempfile.TemporaryDirectory()
        self.addCleanup(held.cleanup)
        patches: list[Any] = [
            patch.object(arrivals, "PATH", Path(held.name) / "arrivals.json"),
            patch.object(sources_watch, "_comparison_owed", True),
            patch.object(sources, "enabled", return_value=True),
            patch.object(vpsdb_sync, "adopt_due", return_value=False),
            patch.object(vpsdb_sync, "art_due", return_value=False),
        ]
        for one in patches:
            one.start()
            self.addCleanup(one.stop)
        self.compared = patch.object(sources_watch, "compare", return_value=True).start()
        self.addCleanup(patch.stopall)
        self.config: Any = MagicMock()


class Refreshing(_Watch):
    def test_every_source_that_can_refresh_is_asked(self) -> None:
        asked: list[str] = []

        def refresh(name: str) -> Callable[[Any], bool]:
            def ask(config: Any) -> bool:
                asked.append(name)
                return False

            return ask

        with only(Source(id="one", name="One", refresh=refresh("one")),
                  Source(id="two", name="Two", refresh=refresh("two")),
                  Source(id="mute", name="Mute")):
            sources_watch.watch_once(self.config)

        self.assertEqual(["one", "two"], asked)

    def test_a_source_that_cannot_be_reached_costs_only_itself(self) -> None:
        def down(config: Any) -> bool:
            raise OSError("unreachable")

        stamped = (Listed("a", "vps-1", "tableFiles"),)
        with only(Source(id="down", name="Down", refresh=down), _site(True, stamped)):
            sources_watch.watch_once(self.config)

        self.assertTrue(arrivals.seeded("site"))

    def test_a_source_switched_off_is_not_asked(self) -> None:
        asked: list[str] = []
        def ask(config: Any) -> bool:
            asked.append("off")
            return False

        off = Source(id="off", name="Off", refresh=ask)
        with only(off), patch.object(sources, "enabled", return_value=False):
            sources_watch.watch_once(self.config)

        self.assertEqual([], asked)


class Stamping(_Watch):
    def test_what_a_source_lists_is_taken_the_first_time_it_is_seen(self) -> None:
        held = (Listed("a", "vps-1", "tableFiles"),)
        with only(_site(False, held)):
            sources_watch.watch_once(self.config)

        self.assertTrue(arrivals.seeded("site"))

    def test_a_source_that_changed_has_its_new_items_dated(self) -> None:
        old = (Listed("a", "vps-1", "tableFiles"),)
        new = (*old, Listed("b", "vps-1", "tableFiles"))
        with only(_site(False, old)):
            sources_watch.watch_once(self.config)
        with only(_site(True, new)):
            sources_watch.watch_once(self.config)

        self.assertNotEqual("", arrivals.first_seen("site", "b"))

    def test_a_source_that_did_not_change_is_not_read_again(self) -> None:
        listed = MagicMock(return_value=iter(()))
        quiet = Source(id="site", name="Site", refresh=lambda config: False, listing=listed)
        with only(quiet):
            arrivals.stamp("site", [Listed("a", "vps-1", "tableFiles")])
            sources_watch.watch_once(self.config)

        listed.assert_not_called()


class Comparing(_Watch):
    def test_a_new_process_owes_one_comparison(self) -> None:
        with only(_spreadsheet()):
            sources_watch.watch_once(self.config)

        self.compared.assert_called_once_with()

    def test_it_is_not_repeated_while_nothing_has_changed(self) -> None:
        with only(_spreadsheet()):
            sources_watch.watch_once(self.config)
            sources_watch.watch_once(self.config)

        self.compared.assert_called_once_with()

    def test_a_changed_spreadsheet_owes_another(self) -> None:
        with only(_spreadsheet(changed=False)):
            sources_watch.watch_once(self.config)
        with only(_spreadsheet(changed=True)):
            sources_watch.watch_once(self.config)

        self.assertEqual(2, self.compared.call_count)

    def test_another_source_changing_does_not(self) -> None:
        with only(_spreadsheet()):
            sources_watch.watch_once(self.config)
        with only(_site(True)):
            sources_watch.watch_once(self.config)

        self.compared.assert_called_once_with()

    def test_a_comparison_that_could_not_start_is_still_owed(self) -> None:
        self.compared.return_value = False
        with only(_spreadsheet()):
            sources_watch.watch_once(self.config)
        self.assertTrue(sources_watch._comparison_owed)

        self.compared.return_value = True
        self.compared.reset_mock()
        with only(_spreadsheet()):
            sources_watch.watch_once(self.config)

        self.compared.assert_called_once_with()
        self.assertFalse(sources_watch._comparison_owed)

    def test_a_catalog_already_on_disk_is_compared_before_any_source_is_asked(self) -> None:
        order: list[str] = []

        def compare() -> bool:
            order.append("compare")
            return True

        self.compared.side_effect = compare

        def ask(config: Any) -> bool:
            order.append("refresh")
            return False

        with only(Source(id="slow", name="Slow", refresh=ask)):
            sources_watch.watch_once(self.config)

        self.assertEqual("compare", order[0])

    def test_a_source_that_throws_does_not_hold_back_the_comparison(self) -> None:
        def down(config: Any) -> bool:
            raise OSError("unreachable")

        with only(Source(id="down", name="Down", refresh=down)):
            sources_watch.watch_once(self.config)

        self.compared.assert_called_once_with()

    def test_a_change_that_arrives_while_one_is_starting_is_not_lost(self) -> None:
        def starting() -> bool:
            sources_watch.refreshed(_spreadsheet(), True)
            return True

        self.compared.side_effect = starting
        sources_watch.compare_if_owed()

        self.assertTrue(sources_watch._comparison_owed)

    def test_one_refused_as_busy_is_owed_again(self) -> None:
        self.compared.return_value = False
        sources_watch.compare_if_owed()

        self.assertTrue(sources_watch._comparison_owed)


class Ending(_Watch):
    """A comparison is a job; how it ends decides whether another is owed."""

    def _ends(self, kind: str, *, failed: bool) -> None:
        job = MagicMock(kind=kind)
        with patch.object(sources_watch.job_registry, "get", return_value=job):
            if failed:
                sources_watch._on_failed("j1")
            else:
                sources_watch._on_done("j1")

    def test_a_failed_comparison_is_owed_again(self) -> None:
        sources_watch._comparison_owed = False

        self._ends(sources_watch.job_registry.KIND_VPS_ROLLUP, failed=True)

        self.assertTrue(sources_watch._comparison_owed)
        self.compared.assert_not_called()

    def test_a_done_comparison_with_another_owed_starts_it(self) -> None:
        sources_watch._comparison_owed = True

        self._ends(sources_watch.job_registry.KIND_VPS_ROLLUP, failed=False)

        self.compared.assert_called_once_with()
        self.assertFalse(sources_watch._comparison_owed)

    def test_a_done_comparison_with_nothing_owed_starts_nothing(self) -> None:
        sources_watch._comparison_owed = False

        self._ends(sources_watch.job_registry.KIND_VPS_ROLLUP, failed=False)

        self.compared.assert_not_called()

    def test_another_kind_of_job_changes_nothing(self) -> None:
        sources_watch._comparison_owed = False

        self._ends("library.refresh", failed=True)

        self.assertFalse(sources_watch._comparison_owed)

    def test_a_job_that_is_gone_changes_nothing(self) -> None:
        sources_watch._comparison_owed = False
        with patch.object(sources_watch.job_registry, "get", return_value=None):
            sources_watch._on_failed("j1")

        self.assertFalse(sources_watch._comparison_owed)

    def test_the_real_events_reach_it(self) -> None:
        from common import events

        sources_watch.listen()
        self.addCleanup(events.unsubscribe, events.JOB_FAILED, sources_watch._on_failed)
        self.addCleanup(events.unsubscribe, events.JOB_DONE, sources_watch._on_done)
        sources_watch._comparison_owed = False
        job = MagicMock(kind=sources_watch.job_registry.KIND_VPS_ROLLUP)
        with patch.object(sources_watch.job_registry, "get", return_value=job):
            events.emit(events.JOB_FAILED, job_id="j1")

        self.assertTrue(sources_watch._comparison_owed)


class Following(_Watch):
    """A game re-read owes a comparison, settled a few seconds on, and its entry is brought
    up to date at once."""

    def setUp(self) -> None:
        super().setUp()
        sources_watch._tied.clear()
        sources_watch._comparison_owed = False
        self.followed = patch("common.games.library_vps_state.follow", return_value=False).start()
        self.counted = patch("common.games.library_vps_state.counted_as",
                             return_value=False).start()
        self.timer = patch.object(sources_watch.threading, "Timer").start()
        self.addCleanup(lambda: sources_watch._tied.clear())
        patch("common.games.game_identity.game_id", side_effect=lambda game: game.id).start()
        patch("common.games.library_vps_state.ties_of", side_effect=lambda game: game.ties).start()

    def _changed(self, ties: str, game_id: str = "g1") -> None:
        sources_watch._on_game_changed(game=MagicMock(id=game_id, ties=ties))

    def test_a_game_first_seen_that_the_count_already_holds_is_left_alone(self) -> None:
        self.counted.return_value = True

        self._changed("a")

        self.followed.assert_not_called()
        self.assertFalse(sources_watch._comparison_owed)

    def test_a_game_first_seen_that_the_count_does_not_hold_is_followed_and_owes_it(
            self) -> None:
        self._changed("a")

        self.followed.assert_called_once()
        self.assertTrue(sources_watch._comparison_owed)

        self.assertTrue(sources_watch._comparison_owed)
        self.timer.assert_called_once_with(sources_watch._SETTLE_SECONDS,
                                           sources_watch.compare_if_owed)
        self.timer.return_value.start.assert_called_once_with()

    def test_a_game_whose_ties_moved_is_followed_and_owes_the_comparison(self) -> None:
        self._changed("a")
        self.followed.reset_mock()

        self._changed("b")

        self.followed.assert_called_once()
        self.assertTrue(sources_watch._comparison_owed)

    def test_a_game_re_read_with_the_same_ties_is_not_followed(self) -> None:
        self.counted.return_value = True
        self._changed("a")
        self.followed.reset_mock()

        self._changed("a")

        self.followed.assert_not_called()
        self.assertFalse(sources_watch._comparison_owed)

    def test_a_burst_of_changes_restarts_the_wait_rather_than_adding_one(self) -> None:
        self._changed("a")
        self._changed("b")
        self._changed("c")

        self.assertEqual(self.timer.return_value.cancel.call_count, 2)

    def test_a_game_with_no_id_is_left_alone(self) -> None:
        self._changed("a", game_id="")

        self.followed.assert_not_called()
        self.assertFalse(sources_watch._comparison_owed)

    def test_a_game_that_is_gone_owes_the_comparison(self) -> None:
        sources_watch._on_game_changed(game=None)

        self.assertTrue(sources_watch._comparison_owed)

    def test_a_failure_to_follow_still_owes_the_comparison(self) -> None:
        self.followed.side_effect = OSError("unreadable")

        self._changed("a")

        self.assertTrue(sources_watch._comparison_owed)

    def test_the_comparisons_own_ties_owe_nothing(self) -> None:
        sources_watch._own.tying = True
        self.addCleanup(setattr, sources_watch._own, "tying", False)

        self._changed("a")
        sources_watch._on_done("j1")

        self.followed.assert_not_called()
        self.assertFalse(sources_watch._comparison_owed)

    def test_a_scan_that_ends_owes_the_comparison(self) -> None:
        job = MagicMock(kind=sources_watch.job_registry.KIND_LIBRARY_SCAN)
        with patch.object(sources_watch.job_registry, "get", return_value=job):
            sources_watch._on_done("j1")

        self.assertTrue(sources_watch._comparison_owed)

    def test_the_real_events_reach_it(self) -> None:
        from common import events

        sources_watch.listen()
        self.addCleanup(events.unsubscribe, events.GAME_CHANGED, sources_watch._on_game_changed)
        self.addCleanup(events.unsubscribe, events.JOB_FAILED, sources_watch._on_failed)
        self.addCleanup(events.unsubscribe, events.JOB_DONE, sources_watch._on_done)

        events.emit(events.GAME_CHANGED, game=MagicMock(id="g9", ties="a"), path="x")

        self.followed.assert_called_once()


class Starting(unittest.TestCase):
    def setUp(self) -> None:
        self.tied = patch("common.games.exact_ties.tie_library", return_value=0).start()
        self.addCleanup(patch.stopall)

    def test_the_comparison_waits_for_a_catalog_to_compare_with(self) -> None:
        with tempfile.TemporaryDirectory() as held, \
                patch("common.games.game_service.VPSDB_JSON_PATH", Path(held) / "none.json"), \
                patch("common.games.library_ops.recount_vps_state") as job:
            self.assertFalse(sources_watch.compare())

        job.assert_not_called()

    def test_it_starts_as_the_job_the_pages_show(self) -> None:
        with tempfile.TemporaryDirectory() as held:
            catalog = Path(held) / "vpsdb.json"
            catalog.write_text("[]", encoding="utf-8")
            with patch("common.games.game_service.VPSDB_JSON_PATH", catalog), \
                    patch("common.games.library_ops.recount_vps_state") as job:
                self.assertTrue(sources_watch.compare())

        job.assert_called_once_with()

    def test_the_tables_the_catalog_names_exactly_are_tied_before_they_are_counted(self) -> None:
        order: list[str] = []

        def tie() -> int:
            order.append("tie")
            return 0

        self.tied.side_effect = tie
        with tempfile.TemporaryDirectory() as held:
            catalog = Path(held) / "vpsdb.json"
            catalog.write_text("[]", encoding="utf-8")
            with patch("common.games.game_service.VPSDB_JSON_PATH", catalog), \
                    patch("common.games.library_ops.recount_vps_state",
                          side_effect=lambda: order.append("count")):
                sources_watch.compare()

        self.assertEqual(["tie", "count"], order)

    def test_tying_that_fails_does_not_hold_back_the_comparison(self) -> None:
        self.tied.side_effect = RuntimeError("unreadable")
        with tempfile.TemporaryDirectory() as held:
            catalog = Path(held) / "vpsdb.json"
            catalog.write_text("[]", encoding="utf-8")
            with patch("common.games.game_service.VPSDB_JSON_PATH", catalog), \
                    patch("common.games.library_ops.recount_vps_state") as job:
                self.assertTrue(sources_watch.compare())

        job.assert_called_once_with()

    def test_a_pass_over_the_info_files_running_leaves_the_comparison_owed(self) -> None:
        self.tied.return_value = None
        with tempfile.TemporaryDirectory() as held:
            catalog = Path(held) / "vpsdb.json"
            catalog.write_text("[]", encoding="utf-8")
            with patch("common.games.game_service.VPSDB_JSON_PATH", catalog), \
                    patch("common.games.library_ops.recount_vps_state") as job:
                self.assertFalse(sources_watch.compare())

        job.assert_not_called()

    def test_one_already_running_leaves_it_owed(self) -> None:
        with tempfile.TemporaryDirectory() as held:
            catalog = Path(held) / "vpsdb.json"
            catalog.write_text("[]", encoding="utf-8")
            with patch("common.games.game_service.VPSDB_JSON_PATH", catalog), \
                    patch("common.games.library_ops.recount_vps_state",
                          side_effect=RuntimeError("busy")):
                self.assertFalse(sources_watch.compare())


class TheThread(_Watch):
    def test_it_wakes_again_and_stops_when_told(self) -> None:
        woke = threading.Event()
        stop = threading.Event()

        def once(config: Any) -> None:
            woke.set()

        with patch.object(sources_watch, "watch_once", once), \
                patch.object(sources_watch, "_WAKE_SECONDS", 0.01):
            thread = sources_watch.start_watch(self.config, stop)
            self.assertTrue(woke.wait(5))
            stop.set()
            thread.join(5)

        self.assertFalse(thread.is_alive())

    def test_a_wake_that_fails_does_not_end_the_watch(self) -> None:
        calls = threading.Semaphore(0)
        stop = threading.Event()

        def fails(config: Any) -> None:
            calls.release()
            raise RuntimeError("one bad wake")

        with patch.object(sources_watch, "watch_once", fails), \
                patch.object(sources_watch, "_WAKE_SECONDS", 0.01):
            thread = sources_watch.start_watch(self.config, stop)
            self.assertTrue(calls.acquire(timeout=5))
            self.assertTrue(calls.acquire(timeout=5))
            stop.set()
            thread.join(5)


class TheSpreadsheet(unittest.TestCase):
    def test_it_lists_every_release_with_the_version_and_date_it_gives(self) -> None:
        built = {"entries": {"vps-1": {"tableFiles": [
            {"id": "r1", "version": "1.2", "added": "2026-02-02"},
            {"id": "", "version": "9", "added": ""}],
            "b2sFiles": [{"id": "r2", "version": "", "added": ""}]}}}
        with patch("common.online.vps_releases.index", return_value=built):
            found = list(vps_source.listing())

        self.assertEqual([Listed("r1", "vps-1", "tableFiles", "1.2", "2026-02-02"),
                          Listed("r2", "vps-1", "b2sFiles", "", "")], found)

    def test_it_is_the_first_source_and_has_no_switch(self) -> None:
        with patch.object(sources, "_sources", {}), patch.object(sources, "_built_in_loaded",
                                                                  False):
            first = sources.all_sources()[0]

        self.assertEqual(vps_source.ID, first.id)
        self.assertTrue(first.always_on)
        self.assertTrue(first.versions)

    def test_it_says_whether_the_catalog_changed(self) -> None:
        with patch.object(vpsdb_sync, "sync", return_value={"changed": True, "version": "v2"}):
            self.assertTrue(vps_source.refresh(MagicMock()))
        with patch.object(vpsdb_sync, "sync", return_value={"changed": False}):
            self.assertFalse(vps_source.refresh(MagicMock()))


class AskedForByHand(_Watch):
    def test_a_manual_sync_that_found_a_new_catalog_is_taken_and_compared(self) -> None:
        held = (Listed("a", "vps-1", "tableFiles"),)
        with patch.object(vpsdb_sync, "sync", return_value={"changed": True}), \
                patch.object(vps_source, "SOURCE", _spreadsheet(True, held)):
            result = vps_lens.sync_now()

        self.assertEqual({"changed": True}, result)
        self.assertTrue(arrivals.seeded(vps_source.ID))
        self.compared.assert_called_once_with()


class WarmingTheMediaIndex(_Watch):
    """Pages read the index as held; the watch is what loads it, so it is held by the time a
    page is opened."""

    INDEX: dict[str, Any] = {"entries": {}}

    def test_the_first_wake_loads_the_index(self) -> None:
        with patch.object(vpinmediadb, "_MANIFEST", None), \
                patch("common.online.vpsdb_cache.VPinMediaDatabase.load",
                      return_value=self.INDEX) as load, \
                only(vpinmediadb.SOURCE):
            sources_watch.watch_once(self.config)

            self.assertEqual(self.INDEX, vpinmediadb._MANIFEST)
        load.assert_called_once_with()

    def test_offline_leaves_nothing_held_and_raises_nothing(self) -> None:
        with patch.object(vpinmediadb, "_MANIFEST", None), \
                patch("common.online.vpsdb_cache.VPinMediaDatabase.load",
                      return_value=None), \
                only(vpinmediadb.SOURCE):
            sources_watch.watch_once(self.config)

            self.assertIsNone(vpinmediadb._MANIFEST)

    def test_a_read_that_raises_is_silent_too(self) -> None:
        with patch.object(vpinmediadb, "_MANIFEST", None), \
                patch("common.online.vpsdb_cache.VPinMediaDatabase.load",
                      side_effect=OSError("down")), \
                only(vpinmediadb.SOURCE):
            self.assertFalse(vpinmediadb.warm(self.config))
            self.assertIsNone(vpinmediadb._MANIFEST)

    def test_the_next_wake_asks_again_after_a_failed_one(self) -> None:
        with patch.object(vpinmediadb, "_MANIFEST", None), \
                patch("common.online.vpsdb_cache.VPinMediaDatabase.load",
                      side_effect=[None, self.INDEX]) as load, \
                only(vpinmediadb.SOURCE):
            sources_watch.watch_once(self.config)
            sources_watch.watch_once(self.config)

            self.assertEqual(self.INDEX, vpinmediadb._MANIFEST)
        self.assertEqual(2, load.call_count)

    def test_an_index_already_held_is_not_fetched_again(self) -> None:
        with patch.object(vpinmediadb, "_MANIFEST", self.INDEX), \
                patch("common.online.vpsdb_cache.VPinMediaDatabase.load") as load:
            self.assertFalse(vpinmediadb.warm(self.config))
        load.assert_not_called()


if __name__ == "__main__":
    unittest.main()

    def test_tying_tables_for_the_comparison_forgets_what_games_were_last_seen_tied_to(
            self) -> None:
        sources_watch._tied["g1"] = ("E1", ())
        self.addCleanup(sources_watch._tied.clear)
        with tempfile.TemporaryDirectory() as held:
            catalog = Path(held) / "vpsdb.json"
            catalog.write_text("[]", encoding="utf-8")
            with patch("common.games.game_service.VPSDB_JSON_PATH", catalog), \
                    patch("common.games.library_ops.recount_vps_state"):
                sources_watch.compare()

        self.assertEqual(sources_watch._tied, {})
