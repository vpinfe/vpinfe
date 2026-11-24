"""Protects: (d) a table that pauses on a Mac, because another of its windows took the
keyboard, is put back on its playfield with the person's own shortcut or a click, and nothing
is posted when the person turned Table Focus off, asked for the pause, paused it in Visual
Pinball, or is in another program.
The order VPX writes its lines in is the case covered: the table runs for a moment before the
keyboard goes, so a pause just after the start counts even though a resume line came first.
The pause the table opens with is not taken for a lost keyboard.

A fake system and a fake clock stand in for the window backend; nothing is posted.
"""

from __future__ import annotations

import unittest
from collections.abc import Callable
from unittest import mock

from common.host import front
from common.host.front import Window

GAME, OWN = 100, 1
PLAYFIELD = Window(1, GAME, "Visual Pinball Player")
BACKGLASS = Window(2, GAME, "Visual Pinball Backglass")
KIOSK = Window(3, OWN + 1, "VPinFE")
TITLES = ("Visual Pinball Player",)


def _tree(pid: int) -> set[int]:
    return {GAME} if pid == GAME else {OWN, OWN + 1, GAME}


class _Mac:
    """Presses and clicks are counted; the table runs after `resumes_at` of them."""

    name = "Fake"
    any_table_window_will_do = True
    titles_readable = True

    def __init__(self, *, holder: Window, shortcut: tuple[int, int] | None = (50, 0x100000),
                 resumes_at: int | None = None, refuse: str = "") -> None:
        self.held = holder
        self.shortcut = shortcut
        self.resumes_at = resumes_at
        self.refuse = refuse
        self.presses = 0
        self.clicks = 0
        self.handed: list[Window] = []
        self.on_resume: Callable[[], None] = lambda: None
        self.clock: Callable[[], float] = lambda: 0.0
        self.pressed_at: list[float] = []

    def windows(self) -> list[Window]:
        return [PLAYFIELD, BACKGLASS, KIOSK]

    def holder(self) -> Window | None:
        return self.held

    def hand_over(self, window: Window) -> str:
        self.handed.append(window)
        if self.held.pid != GAME:
            self.held = window
        return ""

    def next_window_shortcut(self) -> tuple[int, int] | None:
        return self.shortcut

    def press_next_window(self) -> str:
        if self.refuse:
            return self.refuse
        self.presses += 1
        self.pressed_at.append(self.clock())
        self._maybe_resume()
        return ""

    def click_playfield(self, table: list[Window]) -> str:
        if self.refuse:
            return self.refuse
        assert {one.pid for one in table} == {GAME}
        self.clicks += 1
        self._maybe_resume()
        return ""

    def _maybe_resume(self) -> None:
        if self.resumes_at is not None and self.presses + self.clicks >= self.resumes_at:
            self.on_resume()


class BackToPlayfieldTests(unittest.TestCase):
    def setUp(self) -> None:
        for patched in (mock.patch.object(front, "_tree_of", side_effect=_tree),
                        mock.patch.object(front, "_program_of", return_value="VPinballX"),
                        mock.patch.object(front, "_started_after", return_value=False)):
            patched.start()
            self.addCleanup(patched.stop)

    def _run(self, mac: _Mac, *, pause_asked: Callable[[], bool] | None = None,
             resume_at: float | None = 0.20, lost_at: float | None = 0.75,
             lost_line: bool = True, back_by_itself_at: float | None = None,
             takes: tuple[float, Window] | None = None,
             end: float = 8.0) -> str:
        clock = [0.0]
        state = {"paused": False}

        def sleep(seconds: float) -> bool:
            clock[0] += seconds
            for moment, action in list(timeline):
                if moment <= clock[0]:
                    timeline.remove((moment, action))
                    action()
            return clock[0] >= end

        watch = front.Watch(mac, GAME, titles=TITLES, pause_asked=pause_asked,
                            paused=lambda: state["paused"], clock=lambda: clock[0],
                            sleep=sleep, own_pid=OWN)

        def pause() -> None:
            state["paused"] = True
            watch.note_pause_line()

        def lost() -> None:
            pause()
            if lost_line:
                watch.note_focus_lost()

        def take(window: Window) -> Callable[[], None]:
            def action() -> None:
                mac.held = window
            return action

        def resume() -> None:
            state["paused"] = False
            watch.note_resume_line()

        mac.on_resume = resume
        mac.clock = lambda: clock[0]
        timeline: list[tuple[float, Callable[[], None]]] = [
            (0.05, pause), (0.10, watch.note_startup_done)]
        if resume_at is not None:
            timeline.append((resume_at, resume))
        if takes is not None:
            timeline.append((takes[0], take(takes[1])))
        if lost_at is not None:
            timeline.append((lost_at, lost))
        if back_by_itself_at is not None:
            timeline.append((back_by_itself_at, resume))
        with self.assertLogs("vpinfe.common.host.front", level="INFO") as logged:
            watch.run()
        return "\n".join(logged.output)

    def test_the_table_that_runs_after_the_second_press_gets_no_third(self) -> None:
        mac = _Mac(holder=BACKGLASS, resumes_at=2)

        said = self._run(mac)

        self.assertEqual((mac.presses, mac.clicks), (2, 0))
        self.assertIn("the table ran after 2 press(es)", said)

    def test_three_presses_then_a_click_then_it_says_nothing_worked(self) -> None:
        mac = _Mac(holder=BACKGLASS)

        said = self._run(mac)

        self.assertEqual((mac.presses, mac.clicks), (3, 1))
        self.assertIn("nothing brought the table back after 3 press(es)", said)

    def test_the_click_backs_up_the_presses(self) -> None:
        mac = _Mac(holder=BACKGLASS, resumes_at=4)

        said = self._run(mac)

        self.assertEqual((mac.presses, mac.clicks), (3, 1))
        self.assertIn("the table ran after 3 press(es) of the shortcut, 1 click(s)", said)

    def test_a_shortcut_that_is_turned_off_goes_straight_to_the_click(self) -> None:
        mac = _Mac(holder=BACKGLASS, shortcut=None, resumes_at=1)

        said = self._run(mac)

        self.assertEqual((mac.presses, mac.clicks), (0, 1))
        self.assertIn("the shortcut being off", said)

    def test_a_refused_press_is_said_and_the_click_is_tried(self) -> None:
        mac = _Mac(holder=BACKGLASS, refuse="macOS has not allowed VPinFE to post")

        said = self._run(mac)

        self.assertEqual((mac.presses, mac.clicks), (0, 0))
        self.assertIn("the shortcut did not go: macOS has not allowed", said)
        self.assertNotIn("the click did not go", said)
        self.assertIn("nothing brought the table back after 0 press(es)", said)

    def test_nothing_is_posted_for_a_pause_the_person_asked_for(self) -> None:
        mac = _Mac(holder=BACKGLASS)

        self._run(mac, pause_asked=lambda: True)

        self.assertEqual((mac.presses, mac.clicks), (0, 0))

    def test_a_table_that_ran_for_a_moment_before_the_keyboard_went_is_brought_back(self) -> None:
        mac = _Mac(holder=BACKGLASS, resumes_at=1)

        said = self._run(mac)

        self.assertEqual((mac.presses, mac.clicks), (1, 0))
        self.assertIn("the table ran after 1 press(es)", said)

    def test_the_pause_the_table_opens_with_gets_no_press(self) -> None:
        mac = _Mac(holder=BACKGLASS)

        self._run(mac, resume_at=1.0, lost_at=None)

        self.assertEqual((mac.presses, mac.clicks), (0, 0))

    def test_a_table_that_never_ran_is_brought_back_once_its_start_has_had_time(self) -> None:
        mac = _Mac(holder=BACKGLASS, resumes_at=1)

        said = self._run(mac, resume_at=None, lost_at=None)

        self.assertEqual((mac.presses, mac.clicks), (1, 0))
        self.assertGreaterEqual(mac.pressed_at[0], 0.10 + front.STUCK_SECONDS)
        self.assertIn("the table ran after 1 press(es)", said)

    def test_a_pause_the_person_makes_gets_no_press(self) -> None:
        mac = _Mac(holder=BACKGLASS)

        self._run(mac, lost_line=False)

        self.assertEqual((mac.presses, mac.clicks), (0, 0))

    def test_a_table_whose_window_titles_are_held_back_gets_the_same_presses(self) -> None:
        mac = _Mac(holder=BACKGLASS, resumes_at=2)
        mac.titles_readable = False

        said = self._run(mac)

        self.assertEqual((mac.presses, mac.clicks), (2, 0))
        self.assertIn("the table ran after 2 press(es)", said)

    def test_another_window_of_the_table_taking_the_keyboard_is_not_handed_over(self) -> None:
        mac = _Mac(holder=PLAYFIELD, resumes_at=1)

        self._run(mac, takes=(0.5, BACKGLASS))

        self.assertEqual(mac.presses, 1)
        self.assertEqual(mac.handed, [])

    def test_a_pause_long_after_the_start_is_left_alone(self) -> None:
        mac = _Mac(holder=BACKGLASS)

        self._run(mac, lost_at=0.10 + front.BACK_WITHIN_SECONDS + 1.0,
                  end=0.10 + front.BACK_WITHIN_SECONDS + 4.0)

        self.assertEqual((mac.presses, mac.clicks), (0, 0))

    def test_a_table_that_gets_the_keyboard_back_by_itself_gets_no_press(self) -> None:
        mac = _Mac(holder=BACKGLASS)

        self._run(mac, back_by_itself_at=0.80)

        self.assertEqual((mac.presses, mac.clicks), (0, 0))

    def test_nothing_is_posted_while_another_program_is_in_front(self) -> None:
        other = Window(9, 500, "A Browser")
        mac = _Mac(holder=PLAYFIELD)

        self._run(mac, takes=(0.75, other))

        self.assertEqual((mac.presses, mac.clicks), (0, 0))

    def test_nothing_is_posted_while_vpinfes_own_window_is_in_front(self) -> None:
        mac = _Mac(holder=PLAYFIELD)
        mac.hand_over = lambda window: "the system refused"  # type: ignore[method-assign]

        self._run(mac, takes=(0.75, KIOSK))

        self.assertEqual((mac.presses, mac.clicks), (0, 0))

    def test_a_table_that_never_paused_is_left_alone(self) -> None:
        mac = _Mac(holder=BACKGLASS)
        mac.windows = lambda: [PLAYFIELD, BACKGLASS]  # type: ignore[method-assign]
        clock = [0.0]

        def sleep(seconds: float) -> bool:
            clock[0] += seconds
            return clock[0] >= 4.0

        watch = front.Watch(mac, GAME, titles=TITLES,
                            clock=lambda: clock[0], sleep=sleep, own_pid=OWN)
        with self.assertLogs("vpinfe.common.host.front", level="INFO"):
            watch.run()

        self.assertEqual((mac.presses, mac.clicks), (0, 0))

    def test_it_is_one_go_for_each_launch(self) -> None:
        mac = _Mac(holder=BACKGLASS)

        self._run(mac, end=20.0)

        self.assertEqual((mac.presses, mac.clicks), (3, 1))

    def test_a_fresh_answer_is_asked_before_each_press(self) -> None:
        """The backend may keep an answer for a moment; a press never goes on that."""
        class Keeps(_Mac):
            fresh_asked = 0

            def fresh_holder(self) -> Window | None:
                self.fresh_asked += 1
                return self.held

        mac = Keeps(holder=BACKGLASS)

        self._run(mac, end=20.0)

        self.assertEqual(mac.fresh_asked, mac.presses + mac.clicks)
        self.assertGreater(mac.presses, 0)

    def test_nothing_is_posted_when_launch_services_cannot_say_who_is_in_front(self) -> None:
        class Cannot(_Mac):
            def fresh_holder(self) -> Window | None:
                raise RuntimeError("Launch Services did not answer")

        mac = Cannot(holder=BACKGLASS)

        self._run(mac, end=20.0)

        self.assertEqual((mac.presses, mac.clicks), (0, 0))

    def test_a_system_that_cannot_press_a_shortcut_is_never_pressed_for(self) -> None:
        class Windowed(_Mac):
            press_next_window = None  # type: ignore[assignment]

        mac = Windowed(holder=BACKGLASS)

        self._run(mac, end=20.0)

        self.assertEqual((mac.presses, mac.clicks), (0, 0))


if __name__ == "__main__":
    unittest.main()
