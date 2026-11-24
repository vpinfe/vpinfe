"""A kill signal during startup stops us between steps, not inside one."""

from __future__ import annotations

import os
import signal
import sys
import unittest
from unittest import mock

from common.runtime import shutdown


# Windows defines SIGTERM but does not deliver it: os.kill falls through to
# TerminateProcess, so the test run is killed outright - no failure, no summary, exit 1.
# The guard tested that the name exists, which it does. What matters is whether raising
# it runs a handler, and only a POSIX platform does.
@unittest.skipIf(sys.platform.startswith("win"),
                 "Windows terminates the process instead of delivering SIGTERM")
@unittest.skipIf(not hasattr(signal, "SIGTERM"), "no SIGTERM on this platform")
class StartupShutdownTests(unittest.TestCase):
    def setUp(self):
        for name in ("SIGTERM", "SIGINT", "SIGBREAK"):
            sig = getattr(signal, name, None)
            if sig is not None:
                self.addCleanup(signal.signal, sig, signal.getsignal(sig))
        shutdown.reset_for_tests()
        self.addCleanup(shutdown.reset_for_tests)
        # Absorb the signal first, so a regression fails these tests instead of killing
        # the whole run - which is what SIGTERM does by default to the process sending it.
        signal.signal(signal.SIGTERM, lambda *_args: None)

    def test_a_step_boundary_after_a_signal_ends_the_process(self):
        shutdown.watch_during_startup()
        os.kill(os.getpid(), signal.SIGTERM)

        self.assertTrue(shutdown.requested())
        with self.assertRaises(SystemExit) as caught:
            shutdown.exit_if_requested(mock.Mock())
        self.assertEqual(caught.exception.code, 0)

    def test_a_step_boundary_without_a_signal_carries_on(self):
        shutdown.watch_during_startup()

        self.assertFalse(shutdown.requested())
        shutdown.exit_if_requested(mock.Mock())

    def test_a_second_signal_is_left_to_the_default_handler(self):
        shutdown.handle_termination(mock.Mock())
        os.kill(os.getpid(), signal.SIGTERM)

        self.assertEqual(signal.getsignal(signal.SIGTERM), signal.SIG_DFL)


if __name__ == "__main__":
    unittest.main()
