import subprocess
import threading
import unittest
from collections.abc import Callable
from typing import Any, cast

from common import events
from common.host import launch_state


def stamped(state: dict) -> dict:
    """A launching state with its start time checked for being there and left out of
    the comparison, since the clock is not something a test sets."""
    assert state["started_at"] is None or isinstance(state["started_at"], str)
    return {key: value for key, value in state.items() if key != "started_at"}


class FakeProcess:
    """A launched table, as much of one as stopping it needs."""

    def __init__(self, *, stops_when_asked: bool = True) -> None:
        self.stops_when_asked = stops_when_asked
        self.terminated = False
        self.killed = False
        self.alive = True

    def poll(self):
        return None if self.alive else 0

    def terminate(self) -> None:
        self.terminated = True
        if self.stops_when_asked:
            self.alive = False

    def kill(self) -> None:
        self.killed = True
        self.alive = False

    def wait(self, timeout=None):
        if self.alive:
            raise subprocess.TimeoutExpired("vpx", timeout)
        return 0


def _attach(process: FakeProcess, pause: Callable[[], bool] | None = None) -> None:
    launch_state.attach(cast("subprocess.Popen[Any]", process), pause)


class LaunchStateTests(unittest.TestCase):
    def setUp(self) -> None:
        launch_state.clear()
        events.clear()
        self.addCleanup(events.clear)
        self.addCleanup(launch_state.clear)

    def test_starts_idle(self) -> None:
        state = launch_state.current()

        self.assertFalse(state.launching)
        self.assertIsNone(state.game_name)

    def test_set_launching_records_the_game(self) -> None:
        launch_state.set_launching("Medieval Madness", source=launch_state.SOURCE_REMOTE)

        state = launch_state.current()
        self.assertTrue(state.launching)
        self.assertEqual(state.game_name, "Medieval Madness")

    def test_the_state_records_who_asked(self) -> None:
        """The frontend has to tell its own launches from everyone else's, and
        every other consumer needs the state to be true either way."""
        launch_state.set_launching("Medieval Madness", source=launch_state.SOURCE_FRONTEND)

        self.assertEqual(launch_state.current().source, "frontend")

    def test_clear_returns_to_idle(self) -> None:
        launch_state.set_launching("Medieval Madness", source=launch_state.SOURCE_REMOTE)

        launch_state.clear()

        self.assertEqual(launch_state.current().as_dict(),
                         {"launching": False, "game_name": None, "source": None,
                          "paused": False, "game_id": None, "started_at": None})

    def test_clearing_when_idle_is_harmless(self) -> None:
        """The remote page clears in a finally and in an except; both can run."""
        launch_state.clear()
        launch_state.clear()

        self.assertFalse(launch_state.current().launching)

    def test_state_is_immutable_from_the_outside(self) -> None:
        """Readers get a snapshot, not a handle on the live state."""
        launch_state.set_launching("Medieval Madness", source=launch_state.SOURCE_REMOTE)
        snapshot = launch_state.current()

        launch_state.clear()

        self.assertTrue(snapshot.launching, "the snapshot must not change underneath")
        self.assertFalse(launch_state.current().launching)

    def test_concurrent_writes_leave_a_coherent_state(self) -> None:
        """The page writes from a worker thread while the API reads from the loop."""
        errors = []

        def hammer(name):
            try:
                for _ in range(200):
                    launch_state.set_launching(name, source=launch_state.SOURCE_REMOTE)
                    state = launch_state.current()
                    # never a half-written pair: launching implies a name
                    if state.launching and state.game_name is None:
                        errors.append(state)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=hammer, args=(f"Table {i}",)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])


class LaunchStateStopTests(unittest.TestCase):
    """Closing the table somebody else launched."""

    def setUp(self) -> None:
        launch_state.clear()
        events.clear()
        self.addCleanup(events.clear)
        self.addCleanup(launch_state.clear)

    def test_stopping_nothing_says_so(self) -> None:
        self.assertFalse(launch_state.stop())

    def test_a_running_table_is_asked_first(self) -> None:
        process = FakeProcess()
        _attach(process)

        self.assertTrue(launch_state.stop())
        self.assertTrue(process.terminated)
        self.assertFalse(process.killed, "it went when asked, so nothing to kill")

    def test_a_table_that_will_not_go_is_killed(self) -> None:
        process = FakeProcess(stops_when_asked=False)
        _attach(process)

        with self.assertLogs("vpinfe.common.host.launch_state", level="WARNING"):
            self.assertTrue(launch_state.stop(timeout=0.01))

        self.assertTrue(process.killed)

    def test_a_table_that_already_exited_is_not_stopped_again(self) -> None:
        """The launching thread clears in a finally, but a stop can race it."""
        process = FakeProcess()
        process.alive = False
        _attach(process)

        self.assertFalse(launch_state.stop())
        self.assertFalse(process.terminated)

    def test_clearing_releases_the_process(self) -> None:
        process = FakeProcess()
        _attach(process)

        launch_state.clear()

        self.assertFalse(launch_state.stop())
        self.assertFalse(process.terminated)

    def test_the_state_can_be_read_while_a_table_is_being_stopped(self) -> None:
        """The lock is released before the wait. Holding it would deadlock against the
        launching thread, which takes it in `clear()` the moment the process dies."""
        process = FakeProcess()
        read_back = []

        original_wait = process.wait

        def wait_and_read(timeout=None):
            read_back.append(launch_state.current().launching)
            return original_wait(timeout)

        cast(Any, process).wait = wait_and_read
        launch_state.set_launching("Medieval Madness", source=launch_state.SOURCE_API)
        _attach(process)

        self.assertTrue(launch_state.stop())
        self.assertEqual(read_back, [True])


class LaunchStateEventTests(unittest.TestCase):
    """Every change is announced, so a consumer can be told instead of polling."""

    def setUp(self) -> None:
        launch_state.clear()
        events.clear()
        self.addCleanup(events.clear)
        self.addCleanup(launch_state.clear)
        self.seen: list[dict] = []
        events.subscribe(events.PLAY_STATE_CHANGED, lambda **p: self.seen.append(p["state"]))

    def test_a_launch_is_announced(self) -> None:
        launch_state.set_launching("Medieval Madness", source=launch_state.SOURCE_REMOTE,
                                   game_id="mm")

        self.assertEqual([stamped(one) for one in self.seen],
                         [{"launching": True, "game_name": "Medieval Madness",
                           "source": "remote", "paused": False, "game_id": "mm"}])

    def test_clearing_is_announced(self) -> None:
        launch_state.set_launching("Medieval Madness", source=launch_state.SOURCE_REMOTE)
        launch_state.clear()

        self.assertEqual(self.seen[-1], {"launching": False, "game_name": None,
                                         "source": None, "paused": False,
                                         "game_id": None, "started_at": None})

    def test_an_unchanged_state_is_not_announced(self) -> None:
        """The remote page clears in both a finally and an except; both can run."""
        launch_state.clear()
        launch_state.clear()

        self.assertEqual(self.seen, [], "nothing changed, so nothing to say")

    def test_each_event_carries_the_whole_state(self) -> None:
        """A consumer that missed one is still correct after the next."""
        launch_state.set_launching("A", source=launch_state.SOURCE_REMOTE)
        launch_state.set_launching("B", source=launch_state.SOURCE_API)

        self.assertEqual(stamped(self.seen[-1]),
                         {"launching": True, "game_name": "B", "source": "api",
                          "paused": False, "game_id": None})

    def test_a_handler_may_read_the_state_back(self) -> None:
        """The event goes out after the lock is released, so this cannot deadlock."""
        read_back = []
        events.subscribe(events.PLAY_STATE_CHANGED,
                         lambda **_: read_back.append(launch_state.current().game_name))

        launch_state.set_launching("Medieval Madness", source=launch_state.SOURCE_REMOTE)

        self.assertEqual(read_back, ["Medieval Madness"])

    def test_a_broken_listener_cannot_break_a_launch(self) -> None:
        def explode(**_):
            raise RuntimeError("bad listener")

        events.subscribe(events.PLAY_STATE_CHANGED, explode)

        with self.assertLogs("vpinfe.common.events", level="ERROR"):
            launch_state.set_launching("Medieval Madness", source=launch_state.SOURCE_REMOTE)

        self.assertTrue(launch_state.current().launching)



class PauseTests(unittest.TestCase):
    """What the running table said about pausing, and pressing its key."""

    def setUp(self) -> None:
        launch_state.clear()
        events.clear()
        self.addCleanup(events.clear)
        self.addCleanup(launch_state.clear)

    def test_a_running_table_says_it_paused_and_resumed(self) -> None:
        launch_state.set_launching("A", source=launch_state.SOURCE_FRONTEND)

        launch_state.set_paused(True)
        self.assertTrue(launch_state.current().as_dict()["paused"])
        launch_state.set_paused(False)
        self.assertFalse(launch_state.current().paused)

    def test_nothing_running_is_never_paused(self) -> None:
        launch_state.set_paused(True)
        self.assertFalse(launch_state.current().paused)

    def test_the_table_closing_ends_the_pause(self) -> None:
        launch_state.set_launching("A", source=launch_state.SOURCE_FRONTEND)
        launch_state.set_paused(True)

        launch_state.clear()

        self.assertFalse(launch_state.current().paused)

    def test_a_wait_ends_when_the_table_says_so(self) -> None:
        launch_state.set_launching("A", source=launch_state.SOURCE_FRONTEND)
        threading.Timer(0.05, launch_state.set_paused, (True,)).start()

        self.assertTrue(launch_state.wait_paused(True, 2.0))
        self.assertFalse(launch_state.wait_paused(False, 0.05))

    def test_the_pause_key_is_the_running_tables_own(self) -> None:
        pressed: list[str] = []

        def press() -> bool:
            pressed.append("key")
            return True

        _attach(FakeProcess(), press)

        self.assertTrue(launch_state.toggle_pause())
        self.assertEqual(pressed, ["key"])
        launch_state.clear()
        self.assertFalse(launch_state.toggle_pause())

    def test_a_table_with_no_pause_presses_nothing(self) -> None:
        _attach(FakeProcess())
        self.assertFalse(launch_state.toggle_pause())

    def test_a_press_on_a_running_table_is_a_pause_the_person_asked_for(self) -> None:
        launch_state.set_launching("A", source=launch_state.SOURCE_FRONTEND)
        _attach(FakeProcess(), lambda: True)

        launch_state.toggle_pause()

        self.assertTrue(launch_state.pause_asked())

    def test_a_press_on_a_paused_table_is_not_one(self) -> None:
        launch_state.set_launching("A", source=launch_state.SOURCE_FRONTEND)
        launch_state.set_paused(True)
        _attach(FakeProcess(), lambda: True)

        launch_state.toggle_pause()

        self.assertFalse(launch_state.pause_asked())


if __name__ == "__main__":
    unittest.main()
