import unittest
from typing import Any
from unittest import mock

from common import events
from common.games.game import Game
from common.host import peripherals


class BusTests(unittest.TestCase):
    def setUp(self) -> None:
        events.clear()
        self.addCleanup(events.clear)

    def test_hooks_run_in_priority_order(self) -> None:
        order = []
        events.hook("t.e", lambda **_: order.append("late"), priority=200)
        events.hook("t.e", lambda **_: order.append("early"), priority=10)

        events.emit("t.e")

        self.assertEqual(order, ["early", "late"])

    def test_equal_priority_keeps_registration_order(self) -> None:
        order = []
        for name in ("first", "second", "third"):
            events.hook("t.e", lambda _n=name, **_: order.append(_n))

        events.emit("t.e")

        self.assertEqual(order, ["first", "second", "third"])

    def test_hooks_all_run_before_any_subscriber(self) -> None:
        """A subscriber must not observe a half-finished operation."""
        order = []
        events.subscribe("t.e", lambda **_: order.append("subscriber"))
        events.hook("t.e", lambda **_: order.append("hook"))

        events.emit("t.e")

        self.assertEqual(order, ["hook", "subscriber"])

    def test_a_failing_hook_stops_the_operation(self) -> None:
        """The point of a hook: if releasing the hardware fails, do not launch."""
        reached = []

        def explode(**_):
            raise RuntimeError("device busy")

        events.hook("t.e", explode, priority=10)
        events.hook("t.e", lambda **_: reached.append("later hook"), priority=20)
        events.subscribe("t.e", lambda **_: reached.append("subscriber"))

        with self.assertRaises(RuntimeError):
            events.emit("t.e")

        self.assertEqual(reached, [], "nothing after a failed hook should have run")

    def test_a_failing_subscriber_cannot_affect_anything(self) -> None:
        """Wanting to know about a launch must not let you prevent one."""
        reached = []

        def explode(**_):
            raise RuntimeError("badly written listener")

        events.subscribe("t.e", explode)
        events.subscribe("t.e", lambda **_: reached.append("still ran"))

        with self.assertLogs("vpinfe.common.events", level="ERROR"):
            events.emit("t.e")

        self.assertEqual(reached, ["still ran"])

    def test_payload_reaches_handlers(self) -> None:
        seen = {}
        events.hook("t.e", lambda **payload: seen.update(payload))

        events.emit("t.e", game="Medieval Madness", ini_config=None)

        self.assertEqual(seen["game"], "Medieval Madness")

    def test_handlers_tolerate_a_growing_payload(self) -> None:
        """Handlers take **payload, so adding a field later is not a breaking change."""
        events.hook("t.e", lambda *, game=None, **_: None)

        events.emit("t.e", game="x", ini_config=None, something_added_later=1)

    def test_emitting_an_event_nobody_listens_to_is_fine(self) -> None:
        events.emit("t.nobody.cares", anything=1)

    def test_unsubscribe_removes_both_kinds(self) -> None:
        def handler(**_):
            raise AssertionError("should not run")

        events.hook("t.e", handler)
        events.subscribe("t.e", handler)
        events.unsubscribe("t.e", handler)

        events.emit("t.e")
        self.assertEqual(events.registered("t.e"), (0, 0))


class EmitLoggingTests(unittest.TestCase):
    def setUp(self) -> None:
        events.clear()
        self.addCleanup(events.clear)

    def test_a_burst_of_progress_writes_no_per_item_lines(self) -> None:
        told = []
        events.subscribe(events.JOB_PROGRESS, lambda **p: told.append(p["pct"]))

        with self.assertNoLogs("vpinfe.common.events", level="DEBUG"):
            for pct in range(191):
                events.emit(events.JOB_PROGRESS, job_id="j", pct=pct, message="")

        self.assertEqual(len(told), 191, "handlers still hear every one")

    def test_the_input_capture_heartbeat_is_not_logged(self) -> None:
        with self.assertNoLogs("vpinfe.common.events", level="DEBUG"):
            events.emit(events.INPUT_CAPTURE_ARMED, armed=True, ttl_ms=1000,
                        browser="b", device="")

    def test_job_done_and_ordinary_events_are_still_logged(self) -> None:
        for name in (events.JOB_DONE, events.GAME_SELECTED):
            with self.subTest(name=name):
                with self.assertLogs("vpinfe.common.events", level="DEBUG") as logged:
                    events.emit(name)
                self.assertIn(f"emit {name} ", logged.output[0])


class PeripheralLaunchTests(unittest.TestCase):
    """DOF and real-DMD are the bus's first consumers, and the reason hooks exist."""

    def setUp(self) -> None:
        events.clear()
        peripherals.reset_for_tests()
        self.addCleanup(events.clear)
        self.addCleanup(peripherals.reset_for_tests)

    def test_register_attaches_to_both_lifecycle_events(self) -> None:
        peripherals.register()

        self.assertEqual(events.registered(events.TABLE_LAUNCHING)[0], 1)
        self.assertEqual(events.registered(events.TABLE_EXITED)[0], 1)

    def test_register_is_idempotent(self) -> None:
        peripherals.register()
        peripherals.register()

        self.assertEqual(events.registered(events.TABLE_LAUNCHING)[0], 1)

    def test_hardware_is_released_before_anything_else_hooked_to_a_launch(self) -> None:
        """VPX drives the same hardware, so release has to come first - and it has to
        come first even when the other hook was registered earlier."""
        order = []
        events.hook(events.TABLE_LAUNCHING, lambda **_: order.append("other"), priority=50)

        with mock.patch.object(peripherals, "stop_dof_service",
                               side_effect=lambda: order.append("dof released")), \
                mock.patch.object(peripherals, "stop_libdmdutil_service",
                                  side_effect=lambda clear=False: order.append("dmd released")):
            peripherals.register()
            events.emit(events.TABLE_LAUNCHING, game=None, ini_config=None)

        self.assertEqual(order, ["dof released", "dmd released", "other"])

    def test_a_launch_is_abandoned_if_the_hardware_will_not_release(self) -> None:
        """Launching anyway would hand VPX hardware DOF still holds."""
        with mock.patch.object(peripherals, "stop_dof_service",
                               side_effect=RuntimeError("device busy")):
            peripherals.register()

            with self.assertRaises(RuntimeError):
                events.emit(events.TABLE_LAUNCHING, game=None, ini_config=None)

    def test_the_hardware_is_taken_back_when_the_game_exits(self) -> None:
        taken_back = []
        with mock.patch.object(peripherals, "start_dof_service_if_enabled",
                               side_effect=lambda cfg: taken_back.append(cfg)):
            peripherals.register()
            events.emit(events.TABLE_EXITED, game=None, ini_config="the-config")

        self.assertEqual(taken_back, ["the-config"])


class GameSelectionTests(unittest.TestCase):
    """Selecting a game drives two peripherals that must not be able to break each other."""

    def setUp(self) -> None:
        events.clear()
        peripherals.reset_for_tests()
        self.addCleanup(events.clear)
        self.addCleanup(peripherals.reset_for_tests)

    @staticmethod
    def _game(name="Medieval Madness"):
        return Game(game_dir_name=name,
                    meta_config={"vpinfe": {"frontend_dof_event": "E901"}})

    def test_selection_is_subscribers_only(self) -> None:
        """A hook could abandon a selection. Nothing may stop the wheel moving."""
        peripherals.register()

        hooks, subscribers = events.registered(events.GAME_SELECTED)

        self.assertEqual(hooks, 0)
        self.assertEqual(subscribers, 2)

    def test_both_peripherals_react_to_one_selection(self) -> None:
        sent, shown = [], []
        with mock.patch.object(peripherals, "send_frontend_dof_event",
                               side_effect=lambda cfg, token: sent.append(token)), \
                mock.patch.object(peripherals, "_updater") as updater:
            updater.return_value.queue_update.side_effect = (
                lambda name, art: shown.append(name))
            peripherals.register()
            events.emit(events.GAME_SELECTED, game=self._game(), ini_config="cfg")

        self.assertEqual(sent, ["E901"], "the table's own effect, not the default")
        self.assertEqual(shown, ["Medieval Madness"])

    def test_a_dead_dof_still_leaves_the_art_on_the_panel(self) -> None:
        shown = []
        with mock.patch.object(peripherals, "send_frontend_dof_event",
                               side_effect=RuntimeError("dof is not running")), \
                mock.patch.object(peripherals, "_updater") as updater:
            updater.return_value.queue_update.side_effect = (
                lambda name, art: shown.append(name))
            peripherals.register()
            events.emit(events.GAME_SELECTED, game=self._game(), ini_config="cfg")

        self.assertEqual(shown, ["Medieval Madness"])

    def test_a_dead_panel_still_fires_the_dof_effect(self) -> None:
        sent = []
        with mock.patch.object(peripherals, "send_frontend_dof_event",
                               side_effect=lambda cfg, token: sent.append(token)), \
                mock.patch.object(peripherals, "_updater",
                                  side_effect=RuntimeError("no panel attached")):
            peripherals.register()
            events.emit(events.GAME_SELECTED, game=self._game(), ini_config="cfg")

        self.assertEqual(sent, ["E901"])

    def test_one_updater_is_shared_across_frontend_windows(self) -> None:
        """Three windows hold an API instance; the panel must not be written three times."""
        with mock.patch.object(peripherals.real_dmd, "RealDmdUpdater") as updater_class:
            cfg: Any = "cfg"
            first = peripherals._updater(cfg)
            second = peripherals._updater(cfg)

        self.assertIs(first, second)
        self.assertEqual(updater_class.call_count, 1)


if __name__ == "__main__":
    unittest.main()
