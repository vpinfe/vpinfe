"""One view, however many windows are showing it.

A cabinet opens three windows onto one library. They were three independent copies that
happened to agree - each re-read the library, re-sorted it, rebuilt its own entry list
and serialized its own payload. Only the controller window takes input, so only one of
them could ever change what they were all deriving.

What is asserted here is that they now share it: one derivation, one payload, and a
change made through any window visible from all of them.
"""

from __future__ import annotations

import configparser
import json
import sys
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from frontend import game_state
from frontend import library_resolver as frontend_library
from frontend.api import API
from tests.support.entries import entries_for
from tests.support.library_loader import start_library_of

NAMES = ["Bravo", "Alpha", "Charlie"]


def _game(title, created=0):
    return SimpleNamespace(
        meta_config={"Info": {"Title": title}, "User": {}},
        game_dir_name=title, full_path_game=f"/g/{title}",
        full_path_vpx_file=f"/g/{title}/{title}.vpx", creation_time=created,
        pup_pack_exists=False, alt_color_exists=False, alt_sound_exists=False)


def _rows(payload: str) -> list:
    loaded = json.loads(payload)
    return loaded if isinstance(loaded, list) else loaded["entries"]


def _ini():
    parser = configparser.ConfigParser()
    parser.add_section("general")
    parser.set("general", "startup_collection", "")
    return SimpleNamespace(config=parser, save=lambda: None)


class SharedViewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.games = [_game(name, index) for index, name in enumerate(NAMES)]
        start_library_of(self, self.games)

        ini = _ini()
        self.library = frontend_library.LibraryResolver(ini, games=self.games)
        self.windows = {name: API(ini, window_name=name, library=self.library)
                        for name in ("playfield", "backglass", "scoreview")}

    def _counted_builds(self):
        """Count how many times the payload is actually serialized."""
        builds = []
        real = game_state.games_json

        def counting(*args, **kwargs):
            builds.append(1)
            return real(*args, **kwargs)

        return builds, patch.object(game_state, "games_json", counting)

    def test_every_window_steps_through_the_same_entries(self) -> None:
        playfield, backglass = self.windows["playfield"], self.windows["backglass"]

        self.assertIs(playfield.entries, backglass.entries)

    def test_the_payload_is_built_once_for_all_of_them(self) -> None:
        """Three windows asking at startup was three serializations of one answer."""
        builds, counting = self._counted_builds()

        with counting:
            payloads = [window.get_tables() for window in self.windows.values()]

        self.assertEqual(len(builds), 1)
        self.assertEqual(len(set(payloads)), 1, "and they all got the same one")

    def test_a_change_from_one_window_is_what_the_others_see(self) -> None:
        """The controller sorts; the display windows are showing that sort, not a copy
        of it that happens to match."""
        before = self.windows["playfield"].get_tables()

        self.windows["playfield"].apply_sort("Newest", "Descending")
        after = self.windows["playfield"].get_tables()

        self.assertNotEqual(after, before)
        self.assertEqual(self.windows["backglass"].get_tables(), after)
        self.assertEqual(self.windows["scoreview"].get_tables(), after)

    def test_the_others_pay_nothing_to_see_it(self) -> None:
        self.windows["playfield"].apply_sort("Newest", "Descending")
        self.windows["playfield"].get_tables()

        builds, counting = self._counted_builds()
        with counting:
            self.windows["backglass"].get_tables()
            self.windows["scoreview"].get_tables()

        self.assertEqual(builds, [])

    def test_a_stale_library_is_re_derived_once_not_once_per_window(self) -> None:
        """All three ask after the same TableDataChange, and the second and third were
        rebuilding what the first had just rebuilt."""
        for window in self.windows.values():
            window.get_tables()

        self.library.mark_stale()
        refreshes = []
        with patch.object(game_state, "refresh_view",
                          side_effect=lambda api: refreshes.append(1)):
            for window in self.windows.values():
                window.get_tables()

        self.assertEqual(len(refreshes), 1)

    def test_a_window_asking_during_the_refresh_gets_the_refreshed_list(self) -> None:
        for window in self.windows.values():
            window.get_tables()
        shorter = self.games[:2]
        refreshing, finish = threading.Event(), threading.Event()

        def slow_reload():
            refreshing.set()
            finish.wait(timeout=10)
            return shorter

        answers = {}

        def ask(name):
            answers[name] = _rows(self.windows[name].get_tables())

        self.library.mark_stale()
        with patch.object(self.library, "reload", side_effect=slow_reload):
            playfield = threading.Thread(target=ask, args=("playfield",))
            playfield.start()
            self.assertTrue(refreshing.wait(timeout=10))
            backglass = threading.Thread(target=ask, args=("backglass",))
            backglass.start()
            backglass.join(timeout=0.3)
            finish.set()
            playfield.join(timeout=10)
            backglass.join(timeout=10)

        self.assertEqual(len(answers["playfield"]), len(shorter))
        self.assertEqual(answers["backglass"], answers["playfield"])

    def test_a_window_built_without_one_gets_its_own(self) -> None:
        """A gamepad diagnostic or a test builds an API on its own; it must not reach
        into a view meant for somebody else's windows."""
        solo = API(_ini(), window_name="gamepad")

        self.assertIsNot(solo.library, self.library)

    def test_a_bare_instance_still_works(self) -> None:
        """`API.__new__(API)` is how paging and input mapping are tested - no library
        behind it, and no view handed in."""
        bare = API.__new__(API)
        bare._ini_config = _ini()
        bare.filtered_games = entries_for(self.games)

        self.assertEqual(len(bare.entries), len(self.games))


class ViewConcurrencyTests(unittest.TestCase):
    """One shared view makes two windows' calls genuinely concurrent, where three
    separate copies were accidentally safe."""

    # Preempt hard. The window this catches is a few bytecodes wide, so a laptop runs
    # right through it - this failed on CI while passing here every time.
    SWITCH_INTERVAL = 1e-6

    def setUp(self) -> None:
        previous = sys.getswitchinterval()
        sys.setswitchinterval(self.SWITCH_INTERVAL)
        self.addCleanup(sys.setswitchinterval, previous)

    def test_sorting_while_another_window_reads_does_not_tear(self) -> None:
        """A sort mutates `filtered_games` in place. A reader that rebuilds while that is
        happening walks a list being reordered, and sees a wheel of nothing.

        Two sorters and four readers rather than one each: the failing interleaving needs
        a reader to land inside a rebuild, and one thread of each rarely arranges it.
        """
        games = [_game(name, index) for index, name in enumerate(NAMES * 100)]
        with patch("frontend.library_resolver.all_games", return_value=games):
            view = frontend_library.LibraryResolver(_ini(), games=games)

        errors = []

        def sorter():
            for _ in range(40):
                try:
                    with view.lock:
                        game_state.apply_sort(view.filtered_games, "Newest", "Descending")
                        view.rebuild_entries()
                except Exception as exc:      # noqa: BLE001 - the assertion is the report
                    errors.append(exc)

        def reader():
            for _ in range(40):
                try:
                    self.assertEqual(len(view.entries), len(games))
                except Exception as exc:      # noqa: BLE001
                    errors.append(exc)

        threads = ([threading.Thread(target=sorter) for _ in range(2)]
                   + [threading.Thread(target=reader) for _ in range(4)])
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
