"""`ctx.system` restarts and powers off only for a declared scope and a Protected request."""

from __future__ import annotations

import contextvars
import logging
import threading
import time
import unittest
from unittest.mock import patch

from common.extensions import reach, system
from common.extensions.contract import ContractError
from common.extensions.system import ExtensionSystem
from common.runtime import lifecycle

WIRED = patch.object(lifecycle, "performable", return_value=True)


class SystemCallTests(unittest.TestCase):
    def setUp(self) -> None:
        self.asked: list[tuple] = []
        for held in (
            WIRED,
            patch.object(system, "DELAY", 0),
            patch.object(system.device_client.local(), "request",
                         side_effect=lambda *a, **k: self.asked.append((a, k)) or True),  # type: ignore[func-returns-value]
        ):
            held.start()
            self.addCleanup(held.stop)
        self.ext = ExtensionSystem("tester", ("system:admin",))

    def _wait(self) -> None:
        deadline = time.monotonic() + 3
        while not self.asked and time.monotonic() < deadline:
            time.sleep(0.01)
        for one in threading.enumerate():
            if isinstance(one, threading.Timer):
                one.join(1)

    def test_the_answer_comes_before_the_request_is_made(self) -> None:
        with patch.object(system, "DELAY", 30), reach.marking(reach.PROTECTED):
            answer = self.ext.reboot()
            self.assertEqual(self.asked, [])
            for one in threading.enumerate():
                if isinstance(one, threading.Timer):
                    one.cancel()
        self.assertTrue(answer["scheduled"])

    def test_a_protected_request_asks_with_the_extension_as_origin(self) -> None:
        with reach.marking(reach.PROTECTED, "this device"):
            answer = self.ext.reboot("kernel update")
        self._wait()

        self.assertTrue(answer["scheduled"])
        (scope, action), kwargs = self.asked[0]
        self.assertEqual((scope, action), (lifecycle.SYSTEM, lifecycle.RESTART))
        self.assertEqual(kwargs["origin"].surface, lifecycle.SURFACE_EXTENSION)
        self.assertIn("kernel update", kwargs["reason"])

    def test_each_call_asks_for_its_own_pair(self) -> None:
        with reach.marking(reach.PROTECTED):
            self.ext.restart_vpinfe()
            self.ext.shut_down()
        self._wait()

        self.assertEqual(sorted(a for a, _k in self.asked),
                         sorted([(lifecycle.VPINFE, lifecycle.RESTART),
                                 (lifecycle.SYSTEM, lifecycle.STOP)]))

    def test_a_request_below_protected_is_refused(self) -> None:
        for tier in (reach.PLAY, reach.LIBRARY):
            with reach.marking(tier), self.assertRaises(PermissionError):
                self.ext.reboot()

        self.assertEqual(self.asked, [])

    def test_no_request_at_all_is_refused(self) -> None:
        with self.assertRaises(PermissionError):
            self.ext.shut_down()

    def test_a_task_after_the_request_ended_is_refused(self) -> None:
        with reach.marking(reach.PROTECTED):
            copied = contextvars.copy_context()
        with self.assertRaises(PermissionError):
            copied.run(self.ext.reboot)

    def test_a_failure_after_the_delay_reaches_the_log(self) -> None:
        log = logging.getLogger("vpinfe.ext.tester")
        with patch.object(system.device_client.local(), "request",
                          side_effect=OSError("no systemctl")), \
                self.assertLogs(log, "ERROR") as caught:
            system._perform(log, lifecycle.SYSTEM, lifecycle.RESTART,
                            lifecycle.Origin(lifecycle.SURFACE_EXTENSION, "tester"), "x")

        self.assertIn("could not", caught.output[0])

    def test_a_declined_request_reaches_the_log(self) -> None:
        log = logging.getLogger("vpinfe.ext.tester")
        with patch.object(system.device_client.local(), "request", return_value=False), \
                self.assertLogs(log, "WARNING"):
            system._perform(log, lifecycle.SYSTEM, lifecycle.RESTART,
                            lifecycle.Origin(lifecycle.SURFACE_EXTENSION, "tester"), "x")

    def test_without_the_scope_nothing_is_asked(self) -> None:
        bare = ExtensionSystem("bare", ("system:read",))
        with reach.marking(reach.PROTECTED), self.assertRaises(ContractError):
            bare.reboot()
        self.assertEqual(self.asked, [])

    def test_an_action_nothing_performs_is_refused(self) -> None:
        with patch.object(lifecycle, "performable", return_value=False), \
                reach.marking(reach.PROTECTED), self.assertRaises(PermissionError):
            self.ext.reboot()


if __name__ == "__main__":
    unittest.main()
