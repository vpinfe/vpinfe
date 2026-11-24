"""The suite must not be able to power off the machine running it.

This is not a hypothetical. A test wired VPinFE's real lifecycle performers and asked for
a system stop, and a developer's Mac went down mid-run with no prompt - the confirm hook
is off by default, so nothing stands between the request and the command. It happened a
second time from a check meant to prove the guard worked, which ran the suite with the
guard removed.

So these ask the guard rather than triggering it. `subprocess.Popen(["poweroff"])` inside
an assertRaises is not a test of a safety net, it is a test that only passes because the
net is there - and on the run where it is missing, the machine is already going down by
the time the assertion fails.

The guard itself is in `tests/__init__.py`, which every test imports through.
"""

from __future__ import annotations

import os
import subprocess
import sys
import unittest

import tests


class PowerGuardTests(unittest.TestCase):
    """The suite must not be able to power off the machine running it.

    One test wired the real performers and asked for a system stop, and a developer's
    Mac went down mid-run with no prompt. The guard in tests/__init__.py is what stops
    the next one; this is what stops the guard from being quietly removed.
    """

    # Every command line common/host/system_actions.py can produce, on all three
    # platforms - not just this one. CI runs on all three, and the branch that is not
    # taken here is the one nobody checks.
    DANGEROUS = [
        (["systemctl", "poweroff", "-i"], "linux poweroff"),
        (["systemctl", "reboot"], "linux restart"),
        (["osascript", "-e", 'tell app "System Events" to shut down'], "macos poweroff"),
        (["osascript", "-e", 'tell app "System Events" to restart'], "macos restart"),
        (["shutdown", "/s", "/t", "1"], "windows poweroff"),
        (["shutdown", "/r", "/t", "1"], "windows restart"),
        (["C:\\Windows\\System32\\shutdown.exe", "/s"], "windows, full path"),
        (["SHUTDOWN", "/S"], "windows, uppercase"),
        (["/sbin/shutdown", "-h", "now"], "unix shutdown by path"),
        (["poweroff"], "the bare program"),
        ("shutdown /s /t 1", "as a string, the os.system form"),
    ]

    # Things a test may legitimately run. A guard that blocks these is its own outage.
    HARMLESS = [
        (["shutdown", "--help"], "-h is a substring of --help"),
        (["shutdown", "--version"], "reading the version"),
        (["systemctl", "status", "vpinfe"], "systemctl with a safe verb"),
        (["systemctl", "--user", "start", "vpinfe"], "starting a unit"),
        (["osascript", "-e", 'display dialog "hi"'], "osascript, not a power verb"),
        (["echo", "reboot the server tomorrow"], "the word inside an argument"),
        (["git", "log", "--oneline"], "an ordinary command"),
    ]

    def test_every_platforms_power_command_is_refused(self) -> None:
        """Asks the guard directly, never through subprocess.Popen.

        Going through Popen would mean that on a run where the guard was missing, the
        real command fires and *then* the assertion fails - the machine is already going
        down by the time the test reports. That is not hypothetical; it is how this test
        was first written, and it shut a Mac down. Ask the guard, do not pull the trigger.
        """
        for command, label in self.DANGEROUS:
            with self.subTest(label):
                self.assertIsNotNone(tests._is_power_command(command),
                                     f"{label} would have reached the machine")

    def test_ordinary_commands_are_not_refused(self) -> None:
        for command, label in self.HARMLESS:
            with self.subTest(label):
                self.assertIsNone(tests._is_power_command(command),
                                  f"{label} was blocked and should not have been")

    def test_the_guard_covers_every_way_to_spawn_or_replace_a_process(self) -> None:
        """Popen is not the only door: os.system takes a string and never reaches it,
        and os.exec* replaces this process outright - which would end the run mid-suite
        looking like a crash. restart_if_requested uses execvp for exactly that."""
        self.assertIs(subprocess.Popen, tests._GuardedPopen)
        self.assertIs(os.system, tests._guarded_system)
        for name in ("execv", "execvp", "execve"):
            with self.subTest(name):
                self.assertIsNot(getattr(os, name), getattr(tests, f"_real_{name}"),
                                 f"os.{name} can still replace the test process")

    def test_ordinary_commands_still_run(self) -> None:
        """A guard that blocked everything would be its own kind of broken."""
        self.assertEqual(subprocess.check_output(
            [sys.executable, "-c", "print('ok')"]).strip(), b"ok")
