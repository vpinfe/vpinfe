"""The Library card and Recent Jobs on Overview: what each kind's bar counts, which kinds fold
into one line, how the jobs are listed and what hiding the empty kinds writes.

The readers are apart from the page, so this runs without one.
"""

from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, Mock, patch

from manager.kit.data import Library
from manager.library import games
from manager.overview import needs
from manager.settings import sections


def library(games: dict[str, dict[str, bool]], *, hidden: tuple[str, ...] = (),
            listed: list[dict] | None = None) -> Library:
    client = Mock()
    client.extension_list.return_value = {}
    client.extensions.return_value = []
    client.library_policy.return_value = {"hidden_media_kinds": list(hidden)}
    client.put_library_policy.side_effect = lambda changes: {"hidden_media_kinds":
                                                              changes["hidden_media_kinds"]}
    lib = Library(client)
    lib.load_kept_kinds()
    lib.games = [{"id": game_id} for game_id in games]
    lib.media = {game_id: {kind: {"present": present} for kind, present in kinds.items()}
                 for game_id, kinds in games.items()}
    lib._front = {"listed": listed}
    return lib


class WhatEachBarCounts(unittest.TestCase):
    def test_a_kind_counts_the_games_that_hold_it_fewest_first(self):
        lib = library({"a": {"wheel": True, "backglass": True},
                       "b": {"wheel": True, "backglass": False},
                       "c": {"wheel": True, "backglass": False}})

        self.assertEqual([("backglass", 1), ("wheel", 3)], sections.kind_holdings(lib))

    def test_a_kind_no_game_holds_is_listed_at_zero_for_the_fold(self):
        lib = library({"a": {"wheel": True, "topper": False}})

        self.assertEqual([("topper", 0), ("wheel", 1)], sections.kind_holdings(lib))

    def test_a_game_no_longer_in_the_library_is_not_counted(self):
        lib = library({"a": {"wheel": True}, "gone": {"wheel": True}})
        lib.games = [{"id": "a"}]

        self.assertEqual([("wheel", 1)], sections.kind_holdings(lib))

    def test_a_kind_the_library_does_not_collect_has_no_bar(self):
        lib = library({"a": {"wheel": True, "topper": True}}, hidden=("topper",))

        self.assertEqual([("wheel", 1)], sections.kind_holdings(lib))


def drawn_row(kind: str, held: int, total: int, lacking: tuple[str, ...] | None):
    """`_kind_row` drawn on a stand-in page: what the row links to and what its tooltip says."""
    row = MagicMock()
    opens = Mock()
    with patch.object(sections, "ui") as ui, patch.object(sections, "_opens", opens), \
            patch.object(sections, "_bar"):
        ui.element.return_value.classes.return_value = row
        sections._kind_row(kind, held, total, {}, lacking)
    return row, opens


class TheRowAndTheTabItLinksTo(unittest.TestCase):
    """A row's figure is Discovery Home's count of the games the tab lists, and its link opens
    those games."""

    def test_a_media_kind_the_vps_lists_is_counted_under_the_tab_that_holds_both(self):
        self.assertEqual("wheelArtFiles", sections.home_tab("wheel"))
        self.assertEqual("topperFiles", sections.home_tab("topper"))

    def test_a_media_kind_the_vps_does_not_list_has_a_tab_of_its_own(self):
        self.assertEqual("playfield", sections.home_tab("playfield"))
        self.assertEqual("backglass", sections.home_tab("backglass"))

    def test_the_link_opens_the_games_the_tab_lists(self):
        row, opens = drawn_row("backglass", 7, 10, ("g1", "g2"))

        where = opens.call_args.args[2]
        self.assertEqual(("g1", "g2"), where.keys[games.LACKING_KEY])

    def test_the_tooltip_counts_the_tabs_games_not_every_game_without(self):
        row, _opens = drawn_row("backglass", 7, 10, ("g1", "g2"))

        said = row.tooltip.call_args.args[0]
        self.assertIn("7 of 10", said)
        self.assertIn("2 Discovery", said)

    def test_a_game_nothing_can_fill_is_no_link_and_the_tooltip_still_says_what_is_held(self):
        row, opens = drawn_row("backglass", 7, 10, ())

        opens.assert_not_called()
        row.classes.assert_called_with("manager-need--static")
        self.assertEqual("7 of 10 games have Backglass", row.tooltip.call_args.args[0])

    def test_a_kind_every_game_holds_says_so(self):
        row, opens = drawn_row("backglass", 10, 10, ())

        opens.assert_not_called()
        self.assertEqual("All 10 games have Backglass", row.tooltip.call_args.args[0])

    def test_a_home_that_could_not_be_read_leaves_the_row_counting_every_game_without(self):
        row, opens = drawn_row("backglass", 7, 10, None)

        self.assertEqual("7 of 10 games have Backglass. Click to see the 3 without.",
                         row.tooltip.call_args.args[0])
        self.assertEqual(needs.blank("media_backglass"), opens.call_args.args[2].arriving)


class EachRowIsGivenItsTabsGames(unittest.TestCase):
    def rows(self, tabs: dict | None) -> dict[str, tuple | None]:
        lib = library({"a": {"backglass": True, "playfield": True},
                       "b": {"backglass": False, "playfield": True}})
        lib.home_missing = Mock(return_value=tabs)  # type: ignore[method-assign]
        kept: dict[str, tuple | None] = {}
        with patch.object(sections, "ui"), patch.object(sections, "_figure"), \
                patch.object(sections, "_kind_row",
                             lambda kind, held, total, state, lacking=None:
                             kept.__setitem__(kind, lacking)):
            sections._library(lib, {})
        return kept

    def test_the_games_come_from_the_tab_the_kind_is_counted_under(self):
        kept = self.rows({"backglass": {"game_ids": ["b"]}, "playfield": {"game_ids": []}})

        self.assertEqual(("b",), kept["backglass"])
        self.assertEqual((), kept["playfield"])

    def test_a_kind_with_no_tab_has_nothing_a_source_can_fill(self):
        self.assertEqual((), self.rows({})["backglass"])

    def test_a_home_not_read_gives_none(self):
        self.assertIsNone(self.rows(None)["backglass"])

    def test_a_library_never_compared_leaves_the_rows_counting_the_games_without(self):
        lib = library({"a": {"backglass": True}, "b": {"backglass": False}})
        client: Any = lib._client
        client.discovery_missing.return_value = {
            "counted": "", "missing": {"b2sFiles": {"count": 0, "game_ids": []}}}
        lib._watched = lib._read_watched()
        kept: dict[str, tuple | None] = {}
        with patch.object(sections, "ui"), patch.object(sections, "_figure"), \
                patch.object(sections, "_kind_row",
                             lambda kind, held, total, state, lacking=None:
                             kept.__setitem__(kind, lacking)):
            sections._library(lib, {})

        self.assertIsNone(kept["backglass"])


class WhatASourceFills(unittest.TestCase):
    """The games each row leaves out because a source has a file for them: the ones a
    dismissal is kept for."""

    def test_a_row_names_the_games_a_source_has_a_file_for_its_slot(self):
        lib = library({"a": {"backglass": False}, "b": {"backglass": False}, "c": {"wheel": False}})
        lib.fillable_media = Mock(return_value={"backglass": {"a"}, "wheel": {"c"}})  # type: ignore[method-assign]

        filled = sections.source_filled(lib)

        self.assertEqual({"a"}, filled["no_backglass"])
        self.assertEqual(set(), filled["no_playfield"])
        self.assertEqual({"a", "c"}, filled["no_media"])

    def test_with_no_source_offering_anything_no_game_is_left_out(self):
        lib = library({"a": {"backglass": False}})
        lib.fillable_media = Mock(return_value={})  # type: ignore[method-assign]

        self.assertEqual({"no_playfield": set(), "no_backglass": set(), "no_media": set()},
                         sections.source_filled(lib))


class WhatTheLibraryReadsOfHome(unittest.TestCase):
    def read(self, home) -> Library:
        lib = library({})
        client: Any = lib._client
        if isinstance(home, Exception):
            client.discovery_missing.side_effect = home
        else:
            client.discovery_missing.return_value = home
        lib._watched = lib._read_watched()
        return lib

    def test_the_tabs_are_those_of_the_one_read_home_made(self):
        tabs = {"b2sFiles": {"count": 1, "game_ids": ["g1"]}}

        self.assertEqual(tabs, self.read({"counted": "2026-10-01T10:00:00Z",
                                          "missing": tabs}).home_missing())

    def test_a_tab_with_nothing_in_it_of_a_compared_library_is_a_count_of_none(self):
        lib = self.read({"counted": "2026-10-01T10:00:00Z", "missing": {}})

        self.assertEqual({}, lib.home_missing())

    def test_a_library_never_compared_is_none_since_its_tabs_say_nothing(self):
        lib = self.read({"counted": "", "missing": {"b2sFiles": {"count": 0, "game_ids": []}}})

        self.assertIsNone(lib.home_missing())

    def test_a_home_that_could_not_be_read_is_none_not_nothing_missing(self):
        self.assertIsNone(self.read(OSError("down")).home_missing())

    def test_before_anything_is_read_it_is_none(self):
        self.assertIsNone(library({}).home_missing())


class HidingTheEmptyKinds(unittest.TestCase):
    def test_the_kinds_are_added_to_the_ones_already_hidden(self):
        lib = library({"a": {"wheel": True}}, hidden=("topper",))

        lib.hide_media_kinds(["launch_audio", "instruction_card"])

        lib._client.put_library_policy.assert_called_once_with(
            {"hidden_media_kinds": ["instruction_card", "launch_audio", "topper"]})

    def test_the_kinds_the_library_keeps_follow_the_write(self):
        lib = library({"a": {"wheel": True, "topper": False}})

        lib.hide_media_kinds(["topper"])

        self.assertNotIn("topper", lib.kept_kinds()["media"])


class TheHideButton(unittest.TestCase):
    def press(self, lib: Library, state: dict, *, writes: AsyncMock) -> None:
        with patch("manager.kit.on_page.ui", MagicMock()), \
                patch.object(sections.run, "io_bound", writes):
            asyncio.run(sections._hide_kinds(lib, state, ["topper"])())

    def test_it_writes_the_kinds_then_opens_the_media_settings(self):
        opened = AsyncMock()
        writes = AsyncMock()

        self.press(library({}), {"open_on": opened}, writes=writes)

        writes.assert_awaited_once()
        opened.assert_awaited_once()
        self.assertEqual("library.media", opened.await_args.args[0].keys["settings_page"])

    def test_a_failed_write_opens_nothing(self):
        opened = AsyncMock()

        with patch.object(sections.ui, "notify") as notify:
            self.press(library({}), {"open_on": opened}, writes=AsyncMock(side_effect=OSError()))

        opened.assert_not_called()
        notify.assert_called_once()


class TheLatestJobs(unittest.TestCase):
    def job(self, job_id: str, started: float, **fields) -> dict:
        return {"id": job_id, "kind": "media.fill", "state": "done", "started_at": started,
                "finished_at": started + 5, **fields}

    def test_newest_first_and_no_more_than_the_card_holds(self):
        listed = [self.job(str(n), 1000.0 + n) for n in range(sections.RECENT_JOBS + 3)]

        shown = sections._latest_jobs(library({}, listed=listed))

        self.assertEqual(sections.RECENT_JOBS, len(shown))
        self.assertEqual(str(sections.RECENT_JOBS + 2), shown[0]["id"])

    def test_a_job_that_finished_with_problems_reads_as_done_with_errors(self):
        shown = sections._latest_jobs(library({}, listed=[self.job("1", 1.0, problems=2)]))

        self.assertEqual("with_errors", shown[0]["outcome"])

    def test_a_running_job_carries_how_far_it_has_got(self):
        running = self.job("1", 1.0, state="running", pct=40)

        shown = sections._latest_jobs(library({}, listed=[running]))

        self.assertEqual(40.0, shown[0]["pct"])

    def test_jobs_that_could_not_be_read_are_not_an_empty_list(self):
        self.assertIsNone(sections._latest_jobs(library({}, listed=None)))
        self.assertEqual([], sections._latest_jobs(library({}, listed=[])))


class TheFrontKeepsTheList(unittest.TestCase):
    def test_a_refresh_carries_the_list_and_the_running_ones(self):
        lib = library({})
        lib._front = {"play": {}, "jobs": [], "listed": []}
        running = {"id": "1", "state": "running"}
        done = {"id": "2", "state": "done"}

        lib.refresh_front({}, [running, done])

        self.assertEqual([running, done], lib.front()["listed"])
        self.assertEqual([running], lib.front()["jobs"])

    def test_a_failed_read_leaves_none_not_an_empty_list(self):
        lib = Library.__new__(Library)
        lib._client = SimpleNamespace(
            players=lambda: [], play_state=lambda: {}, high_scores=lambda *a, **k: {},
            player_record=lambda player_id: [],
            jobs=Mock(side_effect=OSError("down")))
        lib.games = []
        lib._front_player = ""

        self.assertIsNone(lib._read_front()["listed"])


if __name__ == "__main__":
    unittest.main()
