"""Asking to start, stop or restart something: who gets asked, and what happens then."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any, cast
from unittest import mock

from common import events
from common.host import action_ops
from common.runtime import lifecycle
from frontend import lifecycle_host


class LifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        lifecycle.reset_for_tests()
        self.addCleanup(lifecycle.reset_for_tests)
        self.done: list[Any] = []
        for scope, action in (
            (lifecycle.FRONTEND, lifecycle.START),
            (lifecycle.FRONTEND, lifecycle.STOP),
            (lifecycle.FRONTEND, lifecycle.RESTART),
            (lifecycle.VPINFE, lifecycle.STOP),
            (lifecycle.VPINFE, lifecycle.RESTART),
            (lifecycle.SYSTEM, lifecycle.STOP),
            (lifecycle.SYSTEM, lifecycle.RESTART),
            (lifecycle.TABLE, lifecycle.STOP),
        ):
            lifecycle.register_performer(
                scope, action, lambda request: self.done.append(request.pair))

    def _at(self, surface, address="window-1"):
        return lifecycle.Origin(surface, address)

    def test_an_unconfigured_install_never_asks(self) -> None:
        """The default is what VPinFE has always done: quit means quit."""
        asked = []

        def confirm(request: Any) -> bool:
            asked.append(request)
            return True

        lifecycle.register_confirmer(lifecycle.SURFACE_FRONTEND, confirm)

        self.assertTrue(lifecycle.request(
            lifecycle.VPINFE, lifecycle.STOP, origin=self._at(lifecycle.SURFACE_FRONTEND)))
        self.assertEqual(asked, [])
        self.assertEqual(self.done, [(lifecycle.VPINFE, lifecycle.STOP)])

    def test_saying_no_stops_it(self) -> None:
        lifecycle.register_confirmer(lifecycle.SURFACE_FRONTEND, lambda _request: False)

        self.assertFalse(lifecycle.request(
            lifecycle.SYSTEM, lifecycle.STOP, origin=self._at(lifecycle.SURFACE_FRONTEND),
            confirm_scopes=["system"]))
        self.assertEqual(self.done, [])

    def test_only_the_configured_scopes_are_asked_about(self) -> None:
        """"Ask before touching the machine" must not start asking before quitting."""
        lifecycle.register_confirmer(lifecycle.SURFACE_FRONTEND, lambda _request: False)

        self.assertTrue(lifecycle.request(
            lifecycle.VPINFE, lifecycle.STOP, origin=self._at(lifecycle.SURFACE_FRONTEND),
            confirm_scopes=["system"]))
        self.assertEqual(self.done, [(lifecycle.VPINFE, lifecycle.STOP)])

    def test_the_question_goes_to_the_surface_that_asked(self) -> None:
        """The whole point of an addressed origin: a confirm follows the request home."""
        asked = []

        def confirm_for(surface: str):
            def confirm(_request: Any) -> bool:
                asked.append(surface)
                return True
            return confirm

        lifecycle.register_confirmer(lifecycle.SURFACE_FRONTEND, confirm_for("frontend"))
        lifecycle.register_confirmer(lifecycle.SURFACE_MANAGER, confirm_for("manager"))

        lifecycle.request(lifecycle.SYSTEM, lifecycle.RESTART,
                          origin=self._at(lifecycle.SURFACE_MANAGER, "client-7"),
                          confirm_scopes=["system"])

        self.assertEqual(asked, ["manager"], "the wrong surface was asked")

    def test_a_signal_has_nobody_to_ask_and_is_not_blocked_by_the_setting(self) -> None:
        """A SIGTERM that waits on a dialog is a process that will not die."""
        lifecycle.register_confirmer(lifecycle.SURFACE_FRONTEND, lambda _request: False)

        self.assertTrue(lifecycle.request(
            lifecycle.VPINFE, lifecycle.STOP,
            origin=lifecycle.Origin(lifecycle.SURFACE_SIGNAL),
            confirm_scopes=["app", "system", "frontend"]))
        self.assertEqual(self.done, [(lifecycle.VPINFE, lifecycle.STOP)])

    def test_a_surface_that_cannot_answer_denies(self) -> None:
        """The window went away mid-request, so the person is not there either."""
        def gone(_request):
            raise ConnectionError("the window is closed")

        lifecycle.register_confirmer(lifecycle.SURFACE_FRONTEND, gone)

        self.assertFalse(lifecycle.request(
            lifecycle.SYSTEM, lifecycle.STOP, origin=self._at(lifecycle.SURFACE_FRONTEND),
            confirm_scopes=["system"]))
        self.assertEqual(self.done, [])

    def test_every_surface_is_told_and_none_of_them_can_veto(self) -> None:
        told: list[Any] = []
        lifecycle.register_notifier(lambda request: told.append(request.describe()))
        lifecycle.register_notifier(lambda _request: (_ for _ in ()).throw(RuntimeError))
        lifecycle.register_notifier(lambda request: told.append(request.scope))

        self.assertTrue(lifecycle.request(
            lifecycle.SYSTEM, lifecycle.STOP,
            origin=self._at(lifecycle.SURFACE_FRONTEND)))
        # The broken one sits between the other two, so both running proves it neither
        # stopped the announcement nor stopped the action.
        self.assertEqual(told, ["Power off", "system"])
        self.assertEqual(self.done, [(lifecycle.SYSTEM, lifecycle.STOP)])

    def test_nobody_is_told_about_something_that_was_declined(self) -> None:
        told: list[Any] = []
        lifecycle.register_notifier(told.append)
        lifecycle.register_confirmer(lifecycle.SURFACE_FRONTEND, lambda _request: False)

        lifecycle.request(lifecycle.SYSTEM, lifecycle.STOP,
                          origin=self._at(lifecycle.SURFACE_FRONTEND),
                          confirm_scopes=["system"])

        self.assertEqual(told, [])

    def test_a_build_that_cannot_do_it_says_so_rather_than_half_doing_it(self) -> None:
        lifecycle.reset_for_tests()
        self.assertFalse(lifecycle.request(
            lifecycle.FRONTEND, lifecycle.START,
            origin=self._at(lifecycle.SURFACE_MANAGER)))

    def test_actions_that_are_not_things_are_refused(self) -> None:
        """`start` the machine and `start` VPinFE have nothing to act on: the process is
        already running and the machine is already on."""
        for scope, action in ((lifecycle.SYSTEM, lifecycle.START),
                              (lifecycle.VPINFE, lifecycle.START),
                              ("everything", lifecycle.STOP)):
            with self.subTest(scope=scope, action=action):
                with self.assertRaises(ValueError):
                    lifecycle.request(scope, action,
                                      origin=self._at(lifecycle.SURFACE_FRONTEND))

    def test_every_request_is_described_in_words_a_person_uses(self) -> None:
        """This is read off a confirm dialog by someone deciding whether they meant it.

        Built from the scope and the action it said "stop the app" - naming an internal
        scope - and "restart the system", which reads as restarting VPinFE on the machine
        rather than rebooting the machine.
        """
        expected = {
            (lifecycle.FRONTEND, lifecycle.START): "Open the Frontend",
            (lifecycle.FRONTEND, lifecycle.STOP): "Close the Frontend",
            (lifecycle.FRONTEND, lifecycle.RESTART): "Restart the Frontend",
            (lifecycle.VPINFE, lifecycle.STOP): "Quit VPinFE",
            (lifecycle.VPINFE, lifecycle.RESTART): "Restart VPinFE",
            (lifecycle.SYSTEM, lifecycle.STOP): "Power off",
            (lifecycle.SYSTEM, lifecycle.RESTART): "Reboot",
        }
        for (scope, action), wording in expected.items():
            with self.subTest(scope=scope, action=action):
                request = lifecycle.Request(scope, action,
                                            self._at(lifecycle.SURFACE_FRONTEND))
                self.assertEqual(request.describe(), wording)

    def test_the_wording_covers_every_pair_the_build_allows(self) -> None:
        """A new pair with no wording falls back to the template that produced
        "stop the app", and nobody would notice until it was on screen."""
        described = {pair for pair in lifecycle._ALLOWED
                     if lifecycle.Request(*pair, self._at(lifecycle.SURFACE_FRONTEND))
                     .describe() != f"{pair[1]} the {pair[0]}"}

        self.assertEqual(described, lifecycle._ALLOWED)


    def test_the_performer_reaches_for_the_real_thing(self) -> None:
        """The shape of the mistake that shut a Mac down, made safe.

        install() registers a system-stop performer that calls system_actions for real.
        What went wrong was a test invoking it, so this never does: it patches the
        function first and asserts it was called. Assert on the call, do not make it.
        """
        lifecycle.reset_for_tests()
        self.addCleanup(lifecycle.reset_for_tests)
        with mock.patch("common.host.system_actions.shutdown_system") as power_off:
            lifecycle_host.install(
                config_store=cast(Any, None),
                frontend_browser=cast(Any, SimpleNamespace(terminate_all=lambda: None)),
                shutdown_event=cast(Any, SimpleNamespace(set=lambda: None)),
            )
            lifecycle.request(lifecycle.SYSTEM, lifecycle.STOP,
                              origin=lifecycle.Origin(lifecycle.SURFACE_SIGNAL))

        power_off.assert_called_once_with()

class FrontendPerformerTests(unittest.TestCase):
    """What the frontend scope does, and for whom it exists at all."""

    def setUp(self) -> None:
        lifecycle.reset_for_tests()
        self.addCleanup(lifecycle.reset_for_tests)
        self.browser = mock.Mock()
        self.shutdown = mock.Mock()

    def _install(self, *, windows: bool = False, state: str | None = None,
                 keeps_going: bool = False) -> None:
        self.closed: list[int] = []
        self.opened: list[int] = []
        extra: dict[str, Any] = ({"open_windows": lambda: self.opened.append(1),
                  "close_windows": lambda: self.closed.append(1)} if windows else {})
        if state is not None:
            extra["windows_state"] = lambda: state
        lifecycle_host.install(config_store=cast(Any, None),
                               frontend_browser=self.browser,
                               shutdown_event=self.shutdown,
                               exit_closes_windows=keeps_going, **extra)

    def _ask(self, action: str) -> bool:
        return lifecycle.request(lifecycle.FRONTEND, action,
                                 origin=lifecycle.Origin(lifecycle.SURFACE_API))

    def test_an_install_given_no_windows_to_open_performs_none_of_it(self) -> None:
        self._install()
        for action in (lifecycle.START, lifecycle.STOP, lifecycle.RESTART):
            self.assertFalse(lifecycle.performable(lifecycle.FRONTEND, action))

    def test_an_install_with_windows_performs_all_three(self) -> None:
        self._install(windows=True)
        for action in (lifecycle.START, lifecycle.STOP, lifecycle.RESTART):
            self.assertTrue(lifecycle.performable(lifecycle.FRONTEND, action))

    def test_stopping_closes_the_windows_and_leaves_the_run_going(self) -> None:
        self._install(windows=True)

        self.assertTrue(self._ask(lifecycle.STOP))

        self.assertEqual(self.closed, [1])
        self.browser.terminate_all.assert_not_called()
        self.shutdown.set.assert_not_called()

    def test_starting_and_restarting_open_the_windows(self) -> None:
        self._install(windows=True)

        self._ask(lifecycle.START)
        self._ask(lifecycle.RESTART)

        self.assertEqual(len(self.opened), 2)

    def _listed(self) -> dict[tuple[str, str], dict[str, Any]]:
        return {(one["scope"], one["action"]): one
                for one in action_ops.listing()["actions"]
                if one["scope"] == lifecycle.FRONTEND}

    def test_with_the_windows_up_closing_and_reopening_can_run_and_opening_cannot(self) -> None:
        self._install(windows=True, state=lifecycle.WINDOWS_OPEN)

        listed = self._listed()

        self.assertTrue(listed[(lifecycle.FRONTEND, lifecycle.STOP)]["available"])
        self.assertTrue(listed[(lifecycle.FRONTEND, lifecycle.RESTART)]["available"])
        opening = listed[(lifecycle.FRONTEND, lifecycle.START)]
        self.assertFalse(opening["available"])
        self.assertEqual(opening["reason_key"], "error.actions.windows_open")

    def test_with_the_windows_closed_only_opening_and_reopening_can_run(self) -> None:
        self._install(windows=True, state=lifecycle.WINDOWS_CLOSED)

        listed = self._listed()

        self.assertTrue(listed[(lifecycle.FRONTEND, lifecycle.START)]["available"])
        self.assertTrue(listed[(lifecycle.FRONTEND, lifecycle.RESTART)]["available"])
        closing = listed[(lifecycle.FRONTEND, lifecycle.STOP)]
        self.assertFalse(closing["available"])
        self.assertEqual(closing["reason_key"], "error.actions.windows_closed")

    def test_while_setup_is_open_none_of_it_can_run(self) -> None:
        self._install(windows=True, state=lifecycle.WINDOWS_SETUP)

        for one in self._listed().values():
            self.assertFalse(one["available"])
            self.assertEqual(one["reason_key"], "error.actions.setup_open")

    def test_the_state_is_asked_each_time_it_is_listed(self) -> None:
        state = [lifecycle.WINDOWS_OPEN]
        lifecycle_host.install(config_store=cast(Any, None), frontend_browser=self.browser,
                               shutdown_event=self.shutdown, open_windows=lambda: None,
                               close_windows=lambda: None, windows_state=lambda: state[0])

        self.assertFalse(self._listed()[(lifecycle.FRONTEND, lifecycle.START)]["available"])
        state[0] = lifecycle.WINDOWS_CLOSED
        self.assertTrue(self._listed()[(lifecycle.FRONTEND, lifecycle.START)]["available"])

    def _exit_from(self, surface: str) -> bool:
        return lifecycle_host.request(lifecycle.VPINFE, lifecycle.STOP,
                                      origin=lifecycle.Origin(surface, "window-1"))

    def test_the_frontends_exit_closes_the_windows_on_a_run_that_keeps_going(self) -> None:
        self._install(windows=True, keeps_going=True)

        self.assertTrue(self._exit_from(lifecycle.SURFACE_FRONTEND))

        self.assertEqual(self.closed, [1])
        self.shutdown.set.assert_not_called()

    def test_the_frontends_exit_still_quits_while_setup_is_open(self) -> None:
        self._install(windows=True, keeps_going=True, state=lifecycle.WINDOWS_SETUP)

        self._exit_from(lifecycle.SURFACE_FRONTEND)

        self.assertEqual(self.closed, [])
        self.shutdown.set.assert_called_once_with()

    def test_the_frontends_exit_quits_a_run_that_did_not_start_headless(self) -> None:
        self._install(windows=True)

        self._exit_from(lifecycle.SURFACE_FRONTEND)

        self.assertEqual(self.closed, [])
        self.shutdown.set.assert_called_once_with()

    def test_a_quit_from_anywhere_but_the_frontend_still_quits_a_run_that_keeps_going(self) -> None:
        self._install(windows=True, keeps_going=True)

        self._exit_from(lifecycle.SURFACE_MANAGER)

        self.assertEqual(self.closed, [])
        self.shutdown.set.assert_called_once_with()

    def test_the_frontends_exit_is_a_window_close_on_a_run_that_keeps_going(self) -> None:
        self._install(windows=True, keeps_going=True)

        self.assertEqual(lifecycle_host.effective(
            lifecycle.VPINFE, lifecycle.STOP, lifecycle.SURFACE_FRONTEND),
            (lifecycle.FRONTEND, lifecycle.STOP))

    def test_the_frontends_exit_asks_nothing_when_it_only_closes_the_windows(self) -> None:
        self._install(windows=True, keeps_going=True)

        scope, action = lifecycle_host.effective(
            lifecycle.VPINFE, lifecycle.STOP, lifecycle.SURFACE_FRONTEND)

        with mock.patch.object(lifecycle_host, "confirm_scopes",
                               return_value=(lifecycle.VPINFE,)):
            self.assertTrue(lifecycle_host.wants_confirmation(lifecycle.VPINFE))
            self.assertFalse(lifecycle_host.wants_confirmation(scope))
        self.assertEqual(lifecycle.label(scope, action), "Close the Frontend")

    def test_an_install_that_is_not_a_frontend_cannot_run_any_of_it(self) -> None:
        self._install(windows=True, state=lifecycle.WINDOWS_OFF)

        for one in self._listed().values():
            self.assertFalse(one["available"])
            self.assertEqual(one["reason_key"], "error.actions.frontend_off")

    def test_one_listing_reads_the_state_once(self) -> None:
        reads = [lifecycle.WINDOWS_OPEN, lifecycle.WINDOWS_CLOSED, lifecycle.WINDOWS_CLOSED]
        lifecycle_host.install(config_store=cast(Any, None), frontend_browser=self.browser,
                               shutdown_event=self.shutdown, open_windows=lambda: None,
                               close_windows=lambda: None,
                               windows_state=lambda: reads.pop(0) if reads else "")

        listed = self._listed()

        self.assertFalse(listed[(lifecycle.FRONTEND, lifecycle.START)]["available"])
        self.assertTrue(listed[(lifecycle.FRONTEND, lifecycle.STOP)]["available"])
        self.assertEqual(len(reads), 2)

    def test_an_install_given_no_windows_to_open_lists_no_frontend_action(self) -> None:
        self._install(state=lifecycle.WINDOWS_CLOSED)

        self.assertEqual(self._listed(), {})

    def test_an_install_that_says_nothing_about_its_windows_lists_them_all_available(self) -> None:
        self._install(windows=True)

        self.assertTrue(all(one["available"] for one in self._listed().values()))

    def test_a_pair_that_is_not_a_lifecycle_action_cannot_be_given_a_reason(self) -> None:
        with self.assertRaises(ValueError):
            lifecycle.register_availability("table", "start", lambda: "x")

    def test_quitting_vpinfe_still_ends_the_run(self) -> None:
        self._install(windows=True)

        lifecycle.request(lifecycle.VPINFE, lifecycle.STOP,
                          origin=lifecycle.Origin(lifecycle.SURFACE_API))

        self.shutdown.set.assert_called_once_with()
        self.browser.terminate_all.assert_called_once_with()


class NoticeTests(unittest.TestCase):
    """Every listening surface that did not start it gets told."""

    def setUp(self) -> None:
        lifecycle.reset_for_tests()
        events.clear()
        self.addCleanup(events.clear)
        self.addCleanup(lifecycle.reset_for_tests)
        lifecycle_host._config_store = None
        lifecycle_host._bridge = None

    def _install(self, bridge):
        """Wire the real notice path, with the power performers stubbed out.

        install() registers performers that really do power this machine off. A test
        that asks for a system stop and means only to watch the notice will shut the
        developer's machine down without a prompt - the confirm hook is off by default,
        so nothing stands between the request and osascript. It has happened.

        Patched for the whole test, not just install(), because the performers are
        closures that call system_actions at request time.
        """
        for name in ("shutdown_system", "reboot_system"):
            patcher = mock.patch(f"common.host.system_actions.{name}")
            self.addCleanup(patcher.stop)
            setattr(self, f"fake_{name}", patcher.start())

        lifecycle_host.install(
            config_store=cast(Any, None),
            frontend_browser=cast(Any, SimpleNamespace(terminate_all=lambda: None)),
            shutdown_event=cast(Any, SimpleNamespace(set=lambda: None)),
            ws_bridge=bridge,
        )

    def test_the_windows_are_told_when_the_manager_asked(self) -> None:
        bridge = _Bridge()
        self._install(bridge)

        lifecycle.request(lifecycle.SYSTEM, lifecycle.STOP,
                          origin=lifecycle.Origin(lifecycle.SURFACE_MANAGER, "client-1"))

        self.assertEqual(len(bridge.sent), 1)
        message, excluded = bridge.sent[0]
        self.assertEqual(message["type"], "LifecycleActing")
        self.assertEqual(message["description"], "This device is powering off")
        self.assertEqual(message["origin"], lifecycle.SURFACE_MANAGER)
        self.assertIsNone(excluded, "no window asked, so no window is left out")

    def test_the_window_that_asked_is_not_told(self) -> None:
        """It already has the answer on screen; a second copy talks over it."""
        bridge = _Bridge()
        self._install(bridge)

        lifecycle.request(lifecycle.VPINFE, lifecycle.STOP,
                          origin=lifecycle.Origin(lifecycle.SURFACE_FRONTEND, "table"))

        self.assertEqual(bridge.sent[0][1], "table")

    def test_a_declined_request_tells_nobody(self) -> None:
        bridge = _Bridge()
        self._install(bridge)
        lifecycle.register_confirmer(lifecycle.SURFACE_FRONTEND, lambda _request: False)

        lifecycle.request(lifecycle.SYSTEM, lifecycle.STOP,
                          origin=lifecycle.Origin(lifecycle.SURFACE_FRONTEND, "table"),
                          confirm_scopes=["system"])

        self.assertEqual(bridge.sent, [])

    def test_a_signal_still_tells_the_windows(self) -> None:
        """Nobody could be asked, but the screens should still say what is happening."""
        bridge = _Bridge()
        self._install(bridge)

        lifecycle.request(lifecycle.VPINFE, lifecycle.STOP,
                          origin=lifecycle.Origin(lifecycle.SURFACE_SIGNAL))

        self.assertEqual(len(bridge.sent), 1)
        self.assertIsNone(bridge.sent[0][1])

    def test_only_what_takes_the_windows_away_is_announced(self) -> None:
        """The notice has no way down, so one sent for an action that leaves the windows
        up would sit on every screen."""
        said = {
            (lifecycle.VPINFE, lifecycle.RESTART): "VPinFE is restarting",
            (lifecycle.VPINFE, lifecycle.STOP): "VPinFE is quitting",
            (lifecycle.SYSTEM, lifecycle.RESTART): "This device is rebooting",
            (lifecycle.SYSTEM, lifecycle.STOP): "This device is powering off",
        }
        silent = [(lifecycle.TABLE, lifecycle.STOP)] + [
            (lifecycle.FRONTEND, action)
            for action in (lifecycle.START, lifecycle.STOP, lifecycle.RESTART)]
        for pair, sentence in said.items():
            with self.subTest(pair=pair):
                bridge = _Bridge()
                self._install(bridge)
                lifecycle_host._notice(scope=pair[0], action=pair[1],
                                       surface=lifecycle.SURFACE_MANAGER, address="")
                self.assertEqual([m["description"] for m, _ in bridge.sent], [sentence])
        for pair in silent:
            with self.subTest(pair=pair):
                bridge = _Bridge()
                self._install(bridge)
                lifecycle_host._notice(scope=pair[0], action=pair[1],
                                       surface=lifecycle.SURFACE_MANAGER, address="")
                self.assertEqual(bridge.sent, [])

    def test_installing_twice_does_not_say_it_twice(self) -> None:
        bridge = _Bridge()
        self._install(bridge)
        self._install(bridge)

        lifecycle.request(lifecycle.VPINFE, lifecycle.STOP,
                          origin=lifecycle.Origin(lifecycle.SURFACE_SIGNAL))

        self.assertEqual(len(bridge.sent), 1, bridge.sent)


class _Bridge:
    """Records what would have gone to the windows."""

    def __init__(self) -> None:
        self.sent: list[tuple[Any, Any]] = []

    def send_event_all(self, message, exclude=None) -> None:
        self.sent.append((message, exclude))


if __name__ == "__main__":
    unittest.main()
