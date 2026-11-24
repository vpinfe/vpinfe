"""Protects: (d) a table that is started gets the keyboard, or is left alone when the person
or another program has it, so it neither opens paused nor fights the person for it.

A fake system and a fake clock stand in for the window backend; no window is touched.
"""

from __future__ import annotations

import threading
import unittest
from collections.abc import Callable
from unittest import mock

from common.host import front
from common.host.front import Window

GAME, OWN, OTHER = 100, 1, 500
PLAYFIELD = Window(1, GAME, "Visual Pinball Player")
BACKGLASS = Window(2, GAME + 1, "Visual Pinball Backglass")
KIOSK = Window(3, OWN + 1, "VPinFE")
BROWSER = Window(4, OTHER, "A Browser")
TITLES = ("Visual Pinball Player",)


def _tree(pid: int) -> set[int]:
    return {GAME, GAME + 1} if pid == GAME else {OWN, OWN + 1, GAME, GAME + 1}


class World:
    """The windows, who holds the keyboard, and a clock that moves only when slept."""

    def __init__(self, *, holder: Window | None, end: float = 12.0,
                 windows: list[Window] | None = None, obeys: bool = True) -> None:
        self.now = 0.0
        self.end = end
        self.held = holder
        self.windows = list(windows if windows is not None else [PLAYFIELD, BACKGLASS, KIOSK])
        self.obeys = obeys
        self.handed: list[Window] = []
        self.timeline: list[tuple[float, Callable[[], None]]] = []

    def at(self, moment: float, action: Callable[[], None]) -> None:
        self.timeline.append((moment, action))

    def sleep(self, seconds: float) -> bool:
        self.now += seconds
        due = [one for one in self.timeline if one[0] <= self.now]
        for one in due:
            self.timeline.remove(one)
            one[1]()
        return self.now >= self.end

    def take(self, holder: Window | None) -> Callable[[], None]:
        def action() -> None:
            self.held = holder
        return action

    def list_windows(self) -> list[Window]:
        return list(self.windows)

    def holder(self) -> Window | None:
        return self.held

    def hand_over(self, window: Window) -> str:
        self.handed.append(window)
        if self.obeys:
            self.held = window
        return ""


class _Backend:
    def __init__(self, world: World) -> None:
        self.name = "Fake"
        self._world = world

    def windows(self) -> list[Window]:
        return self._world.list_windows()

    def holder(self) -> Window | None:
        return self._world.holder()

    def hand_over(self, window: Window) -> str:
        return self._world.hand_over(window)


def _program(pid: int) -> str:
    return {GAME: "vpinball.exe", GAME + 1: "vpinball.exe", OWN + 1: "chrome.exe",
            OTHER: "browser.exe"}.get(pid, f"process {pid}")


class FrontTests(unittest.TestCase):
    def setUp(self) -> None:
        for patched in (mock.patch.object(front, "_tree_of", side_effect=_tree),
                        mock.patch.object(front, "_program_of", side_effect=_program),
                        mock.patch.object(front, "_started_after", return_value=False)):
            patched.start()
            self.addCleanup(patched.stop)

    def _watch(self, world: World, *, titles: tuple[str, ...] = TITLES,
               pause_asked: Callable[[], bool] | None = None,
               on_window: Callable[[], None] | None = None) -> front.Watch:
        return front.Watch(_Backend(world), GAME, titles=titles,
                           pause_asked=pause_asked, on_window=on_window,
                           clock=lambda: world.now, sleep=world.sleep, own_pid=OWN)

    def _run(self, watch: front.Watch) -> str:
        with self.assertLogs("vpinfe.common.host.front", level="INFO") as logged:
            watch.run()
        return "\n".join(logged.output)

    def test_a_table_that_already_has_the_keyboard_is_left_alone(self) -> None:
        world = World(holder=PLAYFIELD)

        said = self._run(self._watch(world))

        self.assertEqual(world.handed, [])
        self.assertIn("the table has the keyboard", said)

    def test_a_system_that_hides_titles_gets_the_tables_first_window(self) -> None:
        blank = [Window(1, GAME, ""), Window(2, GAME + 1, ""), Window(3, OWN + 1, "")]
        world = World(holder=blank[2], windows=blank)
        watch = self._watch(world)
        watch._backend.titles_readable = False  # type: ignore[union-attr]

        said = self._run(watch)

        self.assertEqual(world.handed, [blank[0]])
        self.assertIn("does not give out window titles", said)

    def test_the_keyboard_goes_to_the_playfield_when_vpinfe_has_it(self) -> None:
        world = World(holder=KIOSK)

        said = self._run(self._watch(world))

        self.assertEqual(world.handed, [PLAYFIELD])
        self.assertIn("try 1 of 5 passed", said)

    def test_five_tries_then_it_gives_up_and_says_so(self) -> None:
        world = World(holder=KIOSK, obeys=False)

        said = self._run(self._watch(world))

        self.assertEqual(len(world.handed), 5)
        self.assertIn("gave up after 5 tries", said)

    def test_a_system_that_refuses_gives_its_own_words(self) -> None:
        world = World(holder=KIOSK, obeys=False)
        world.hand_over = lambda window: "Access is denied."  # type: ignore[method-assign]

        said = self._run(self._watch(world))

        self.assertIn("the system refused: Access is denied.", said)

    def test_the_backglass_taking_it_is_answered_with_the_playfield(self) -> None:
        world = World(holder=PLAYFIELD)
        world.at(2.0, world.take(BACKGLASS))

        said = self._run(self._watch(world))

        self.assertEqual(world.handed, [PLAYFIELD])
        self.assertIn("vpinball.exe, 'Visual Pinball Backglass'", said)

    def test_a_pause_the_person_asked_for_is_not_undone(self) -> None:
        world = World(holder=KIOSK)

        said = self._run(self._watch(world, pause_asked=lambda: True))

        self.assertEqual(world.handed, [])
        self.assertIn("a pause the person asked for", said)

    def _stuck(self, *, wrong: bool = True, asked: bool = False,
               resumes_after_bounce: bool = True, holder: Window = PLAYFIELD,
               ran: bool = False) -> tuple[World, front.Watch, str]:
        """The table said it is up at 0 s and says it is paused; `ran` is a table that
        has resumed before."""
        world = World(holder=holder, end=60.0)
        paused = [True]
        plain = world.hand_over

        def hand_over(window: Window) -> str:
            came_from = world.held
            plain(window)
            if resumes_after_bounce and window == PLAYFIELD and came_from == KIOSK:
                paused[0] = False
            return ""

        world.hand_over = hand_over  # type: ignore[method-assign]
        watch = front.Watch(_Backend(world), GAME, titles=TITLES,
                            pause_asked=lambda: asked, paused=lambda: paused[0],
                            clock=lambda: world.now, sleep=world.sleep, own_pid=OWN)
        watch._backend.holder_can_be_wrong = wrong  # type: ignore[union-attr]
        watch.note_startup_done()
        if ran:
            watch.note_resume_line()
        return world, watch, self._run(watch)

    def test_a_table_that_has_the_keyboard_but_stays_paused_is_passed_through_a_vpinfe_window(
            self) -> None:
        world, _, said = self._stuck()

        self.assertEqual(world.handed, [KIOSK, PLAYFIELD])
        self.assertIn("passing it through VPinFE's 'VPinFE'", said)
        self.assertIn("try 1 of 5 passed", said)

    def test_a_table_that_stays_paused_through_every_try_gives_up_and_says_so(self) -> None:
        world, _, said = self._stuck(resumes_after_bounce=False)

        self.assertEqual(world.handed, [KIOSK, PLAYFIELD] * 5)
        self.assertIn("gave up after 5 tries", said)

    def test_a_system_whose_foreground_is_trusted_leaves_a_paused_table_alone(self) -> None:
        world, _, said = self._stuck(wrong=False)

        self.assertEqual(world.handed, [])
        self.assertIn("the table has the keyboard", said)

    def test_a_pause_the_person_asked_for_is_not_passed_through_a_window(self) -> None:
        world, _, _ = self._stuck(asked=True)

        self.assertEqual(world.handed, [])

    def test_a_pause_after_the_table_has_run_is_not_passed_through_a_window(self) -> None:
        world, _, _ = self._stuck(ran=True)

        self.assertEqual(world.handed, [])

    def test_vpinfe_having_the_keyboard_is_answered_with_the_table_alone(self) -> None:
        world, _, _ = self._stuck(holder=KIOSK)

        self.assertEqual(world.handed, [PLAYFIELD])

    def test_a_refused_vpinfe_window_still_gets_the_table_asked(self) -> None:
        world = World(holder=PLAYFIELD, end=60.0)
        plain = world.hand_over
        world.hand_over = lambda window: ("Access is denied." if window == KIOSK  # type: ignore[method-assign]
                                          else plain(window))
        watch = front.Watch(_Backend(world), GAME, titles=TITLES,
                            paused=lambda: True, clock=lambda: world.now, sleep=world.sleep,
                            own_pid=OWN)
        watch._backend.holder_can_be_wrong = True  # type: ignore[union-attr]
        watch.note_startup_done()
        world.sleep(front.STUCK_SECONDS)

        said = self._run(watch)

        self.assertIn(PLAYFIELD, world.handed)
        self.assertIn("the system refused VPinFE's window: Access is denied.", said)

    def test_a_pause_in_the_first_moments_after_startup_is_not_yet_a_stuck_table(self) -> None:
        world = World(holder=PLAYFIELD, end=1.0)
        watch = front.Watch(_Backend(world), GAME, titles=TITLES,
                            paused=lambda: True, clock=lambda: world.now, sleep=world.sleep,
                            own_pid=OWN)
        watch._backend.holder_can_be_wrong = True  # type: ignore[union-attr]
        watch.note_startup_done()

        self._run(watch)

        self.assertEqual(world.handed, [])

    def test_with_no_vpinfe_window_the_table_is_handed_the_keyboard_alone(self) -> None:
        world = World(holder=PLAYFIELD, windows=[PLAYFIELD, BACKGLASS])
        watch = front.Watch(_Backend(world), GAME, titles=TITLES,
                            paused=lambda: True, clock=lambda: world.now, sleep=world.sleep,
                            own_pid=OWN)
        watch._backend.holder_can_be_wrong = True  # type: ignore[union-attr]
        watch.note_startup_done()
        world.sleep(front.STUCK_SECONDS)

        said = self._run(watch)

        self.assertEqual(set(world.handed), {PLAYFIELD})
        self.assertIn("VPinFE has no window to pass it through", said)

    def test_a_feature_that_is_done_can_ask_for_one_more_round(self) -> None:
        world = World(holder=KIOSK)
        watch = self._watch(world, pause_asked=lambda: True)
        world.at(1.0, watch.request_round)

        self._run(watch)

        self.assertEqual(world.handed, [PLAYFIELD])

    def test_another_program_having_the_keyboard_is_left_alone_and_named(self) -> None:
        world = World(holder=BROWSER, windows=[PLAYFIELD, BACKGLASS, KIOSK, BROWSER])

        said = self._run(self._watch(world))

        self.assertEqual(world.handed, [])
        self.assertIn("browser.exe has the keyboard", said)

    def test_a_program_that_takes_it_during_the_tries_is_left_alone(self) -> None:
        world = World(holder=KIOSK, obeys=False)
        world.at(0.5, world.take(BROWSER))

        said = self._run(self._watch(world))

        self.assertLess(len(world.handed), 5)
        self.assertIn("took the keyboard", said)

    def test_with_no_playfield_title_any_window_of_the_table_passes(self) -> None:
        world = World(holder=BACKGLASS)

        self._run(self._watch(world, titles=()))

        self.assertEqual(world.handed, [])

    def test_a_table_window_that_is_not_the_playfield_does_not_count(self) -> None:
        world = World(holder=BACKGLASS)

        self._run(self._watch(world))

        self.assertEqual(world.handed, [PLAYFIELD])

    def test_the_window_appearing_says_the_table_is_running_once(self) -> None:
        world = World(holder=PLAYFIELD)
        appeared = mock.Mock()

        self._run(self._watch(world, on_window=appeared))

        appeared.assert_called_once_with()

    def test_a_window_of_a_program_started_since_the_launch_is_the_table(self) -> None:
        elsewhere = Window(9, 900, "Visual Pinball Player")
        world = World(holder=KIOSK, windows=[elsewhere, KIOSK])
        with mock.patch.object(front, "_started_after", return_value=True):
            self._run(self._watch(world))

        self.assertEqual(world.handed, [elsewhere])

    def test_no_window_of_the_table_in_half_a_minute_is_logged_and_nothing_is_asked(self) -> None:
        world = World(holder=KIOSK, windows=[KIOSK], end=60.0)
        appeared = mock.Mock()

        said = self._run(self._watch(world, on_window=appeared))

        self.assertEqual(world.handed, [])
        self.assertIn("no window of the table appeared in 30 seconds", said)
        appeared.assert_not_called()

    def test_a_window_that_comes_up_after_half_a_minute_still_gets_the_keyboard(self) -> None:
        world = World(holder=KIOSK, windows=[KIOSK], end=90.0)
        world.at(45.0, lambda: world.windows.append(PLAYFIELD))

        said = self._run(self._watch(world))

        self.assertIn("no window of the table appeared in 30 seconds, still looking", said)
        self.assertEqual(world.handed, [PLAYFIELD])

    def test_a_window_that_closes_is_not_handed_the_keyboard(self) -> None:
        world = World(holder=PLAYFIELD)
        world.at(2.0, world.take(KIOSK))
        world.at(2.0, lambda: world.windows.remove(PLAYFIELD))

        said = self._run(self._watch(world))

        self.assertEqual(world.handed, [])
        self.assertIn("the table's window is gone", said)

    def test_a_table_that_closes_during_a_try_is_not_a_refusal(self) -> None:
        world = World(holder=KIOSK, obeys=False)

        def closing(window: Window) -> str:
            world.windows.remove(PLAYFIELD)
            return "Invalid window handle."

        world.hand_over = closing  # type: ignore[method-assign]

        said = self._run(self._watch(world))

        self.assertNotIn("refused", said)
        self.assertNotIn("failed", said)
        self.assertNotIn("gave up", said)
        self.assertIn("the table's window is gone", said)

    def test_a_backend_that_raises_does_not_end_the_watch(self) -> None:
        world = World(holder=PLAYFIELD)
        backend = _Backend(world)
        calls = {"n": 0}

        def holder() -> Window | None:
            calls["n"] += 1
            if calls["n"] == 1:
                raise OSError("no answer")
            return world.held

        backend.holder = holder  # type: ignore[method-assign]
        watch = front.Watch(backend, GAME, titles=TITLES,
                            clock=lambda: world.now, sleep=world.sleep, own_pid=OWN)

        said = self._run(watch)

        self.assertIn("could not ask who has the keyboard: no answer", said)
        self.assertIn("the table has the keyboard", said)

    def test_a_system_with_no_backend_says_so_and_ends(self) -> None:
        watch = front.Watch(None, GAME)

        with mock.patch.object(front.sys, "platform", "freebsd"):
            said = self._run(watch)

        self.assertIn("no window backend", said)

    def test_a_linux_desktop_with_no_backend_says_which_and_why(self) -> None:
        env = {"XDG_SESSION_TYPE": "wayland", "WAYLAND_DISPLAY": "wayland-0",
               "XDG_CURRENT_DESKTOP": "GNOME", "XDG_DATA_HOME": "/no/such/data"}

        with mock.patch.object(front.sys, "platform", "linux"), \
                mock.patch.dict(front.os.environ, env, clear=True):
            self.assertIsNone(front.backend_for_this_system())
            said = self._run(front.Watch(None, GAME))

        self.assertIn("GNOME is not letting VPinFE bring the table forward", said)

    def test_a_linux_backend_that_cannot_start_is_none_and_says_why(self) -> None:
        with mock.patch.object(front.sys, "platform", "linux"), \
                mock.patch("common.host.front_linux.backend_for",
                           side_effect=OSError("no X server")), \
                self.assertLogs("vpinfe.common.host.front", "INFO") as logged:
            found = front.backend_for_this_system()

        self.assertIsNone(found)
        self.assertIn("did not start", "\n".join(logged.output))

    def test_the_summary_is_one_line_at_the_end(self) -> None:
        world = World(holder=KIOSK)

        said = self._run(self._watch(world))

        self.assertEqual(said.count("Table focus summary"), 1)
        self.assertIn("1 tries", said)

    def test_stopping_ends_a_watch_that_is_waiting(self) -> None:
        world = World(holder=KIOSK, windows=[KIOSK])
        watch = front.Watch(_Backend(world), GAME, titles=TITLES, own_pid=OWN)
        watch.start()

        watch.stop()

        self.assertFalse(any(one.name == "table-focus" and one.is_alive()
                             for one in threading.enumerate()))


if __name__ == "__main__":
    unittest.main()
