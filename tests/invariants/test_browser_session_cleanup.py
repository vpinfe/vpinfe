"""BrowserSession must not leak a process or a profile when its handshake fails."""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
import time
import unittest
from unittest import mock

import psutil

from tests.support import browser_session


class _FakeChromeProcess:
    """Enough of `subprocess.Popen` for `__aenter__`/`__aexit__` to run against."""

    def __init__(self) -> None:
        self.terminated = False
        self.stdout = None
        self.stderr = None
        self.stdin = None

    def terminate(self) -> None:
        self.terminated = True

    def wait(self, timeout: float | None = None) -> int:
        return 0

    def poll(self) -> int | None:
        return None


class _AlreadyExitedChromeProcess(_FakeChromeProcess):
    """`terminate()` on a pid already reaped elsewhere: `ProcessLookupError`, not a no-op."""

    def terminate(self) -> None:
        raise ProcessLookupError("no such process")


class HandshakeFailureTests(unittest.TestCase):
    def test_a_handshake_that_never_finishes_stops_the_process_and_its_profile(self) -> None:
        fake_proc = _FakeChromeProcess()

        async def enter_and_fail() -> browser_session.BrowserSession:
            session = browser_session.BrowserSession("ignored-binary")
            with mock.patch.object(browser_session.subprocess, "Popen",
                                   return_value=fake_proc), \
                    mock.patch.object(browser_session.BrowserSession, "_page_endpoint",
                                      side_effect=RuntimeError("never came up")), \
                    self.assertRaises(RuntimeError):
                async with session:
                    pass
            return session

        session = asyncio.run(enter_and_fail())
        profile = session._profile

        self.assertTrue(fake_proc.terminated)
        self.assertIsNotNone(profile)
        assert profile is not None  # for the type checker; asserted above
        self.assertFalse(os.path.exists(profile))

    def test_a_process_already_gone_still_loses_its_profile(self) -> None:
        """The Chromium child can exit on its own before `__aexit__` gets to it - a
        crash, or the race `_page_endpoint` already detects through `poll()`. Its
        profile is still ours to remove."""
        session = browser_session.BrowserSession("ignored-binary")
        session._proc = _AlreadyExitedChromeProcess()  # type: ignore[assignment]
        session._profile = tempfile.mkdtemp(prefix="vpinfe-smoke-test-")

        asyncio.run(session.__aexit__(None, None, None))

        self.assertFalse(os.path.exists(session._profile))


class ProfileRemovalRetryTests(unittest.TestCase):
    def test_a_removal_that_only_takes_hold_on_a_later_try_still_finishes(self) -> None:
        profile = tempfile.mkdtemp(prefix="vpinfe-smoke-test-")
        calls: list[int] = []

        def flaky_rmtree(path: str, ignore_errors: bool = False) -> None:
            calls.append(1)
            if len(calls) >= 3:
                shutil.rmtree(path, ignore_errors=True)

        with mock.patch.object(browser_session, "shutil",
                               mock.Mock(rmtree=flaky_rmtree)), \
                mock.patch.object(browser_session, "_PROFILE_REMOVE_DELAY", 0):
            asyncio.run(browser_session._rmtree_persistently(profile))

        self.assertEqual(3, len(calls))
        self.assertFalse(os.path.exists(profile))

    def test_a_removal_that_never_takes_hold_still_gives_up(self) -> None:
        """Best effort, not a hang: a lock that never clears must not spin forever."""
        profile = tempfile.mkdtemp(prefix="vpinfe-smoke-test-")
        self.addCleanup(shutil.rmtree, profile, True)
        calls: list[int] = []

        with mock.patch.object(browser_session, "shutil",
                               mock.Mock(rmtree=lambda *a, **k: calls.append(1))), \
                mock.patch.object(browser_session, "_PROFILE_REMOVE_DELAY", 0):
            asyncio.run(browser_session._rmtree_persistently(profile))

        self.assertEqual(browser_session._PROFILE_REMOVE_ATTEMPTS, len(calls))


def _stopped(pid: int) -> bool:
    """Gone, or killed and not yet reaped: `os.kill(pid, 0)` still succeeds on a zombie."""
    try:
        return psutil.Process(pid).status() == psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return True


@unittest.skipUnless(os.name == "posix", "process groups are POSIX")
class HelperProcessTests(unittest.TestCase):
    def test_a_helper_that_outlives_the_browser_is_stopped_before_the_profile_goes(self) -> None:
        profile = tempfile.mkdtemp(prefix="vpinfe-smoke-")
        browser = browser_session.subprocess.Popen(
            ["sh", "-c", "sleep 30 & echo $!; wait"], stdout=browser_session.subprocess.PIPE,
            text=True, start_new_session=True)
        assert browser.stdout is not None
        helper = int(browser.stdout.readline())
        session = browser_session.BrowserSession("ignored-binary")
        session._proc, session._profile, session._group = browser, profile, True

        asyncio.run(session.__aexit__(None, None, None))

        deadline = time.monotonic() + 5
        while not _stopped(helper) and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertTrue(_stopped(helper))
        self.assertFalse(os.path.exists(profile))


if __name__ == "__main__":
    unittest.main()
