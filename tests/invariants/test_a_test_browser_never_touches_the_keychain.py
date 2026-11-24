"""Protects: (e) a Chromium started for a test reaches the macOS login keychain, which a
scratch HOME does not have, and the system puts "Keychain Not Found" on the screen.

Three places can start one: `BrowserSession` itself, the app a `LiveInstance` runs when it
opens a window, and any launcher a test adds. Each must carry `NO_KEYCHAIN_FLAGS`.
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from common.config.config_access import cfg_get
from common.config.config_store import ConfigStore
from tests.support import browser_session
from tests.support.browser_session import NO_KEYCHAIN_FLAGS, BrowserSession
from tests.support.live_instance import LiveInstance

TESTS = Path(__file__).resolve().parents[1]
# A flag only a launcher writes: the app's own `--headless` is a different program's.
LAUNCH_MARKERS = ("--remote-debugging-port", "--headless=new")


class _StoppedError(Exception):
    pass


class EveryTestBrowserAvoidsTheKeychainTests(unittest.TestCase):
    instance: LiveInstance

    def test_the_flags_are_the_two_that_keep_a_browser_off_the_keychain(self) -> None:
        self.assertEqual(("--use-mock-keychain", "--password-store=basic"), NO_KEYCHAIN_FLAGS)

    def test_a_browser_session_starts_its_browser_with_them(self) -> None:
        started: list[list[str]] = []

        def popen(args: list[str], **_kwargs: object) -> None:
            started.append(args)
            raise _StoppedError

        async def start() -> None:
            async with BrowserSession("a-browser"):
                pass

        with mock.patch.object(browser_session.subprocess, "Popen", popen), \
                self.assertRaises(_StoppedError):
            asyncio.run(start())

        self.assertEqual(1, len(started))
        for flag in NO_KEYCHAIN_FLAGS:
            self.assertIn(flag, started[0])

    @classmethod
    def setUpClass(cls) -> None:
        root = Path(cls.enterClassContext(tempfile.TemporaryDirectory()))
        cls.instance = LiveInstance(root)
        cls.addClassCleanup(cls.instance.__exit__)
        cls.instance.ports = {"manager": 1, "ws": 2}

    def _written_options(self, own: str = "") -> str:
        self.instance.extra_settings = {("chromium", "options"): own} if own else {}
        self.instance._write_config()
        return cfg_get(ConfigStore(str(self.instance.config_dir / "vpinfe.ini")),
                       "chromium", "options", "")

    def test_a_window_the_app_opens_under_a_live_instance_gets_them(self) -> None:
        written = self._written_options().split()

        for flag in NO_KEYCHAIN_FLAGS:
            self.assertIn(flag, written)

    def test_a_test_s_own_browser_options_are_added_to_them_not_instead(self) -> None:
        written = self._written_options("--window-size=800,600").split()

        for flag in (*NO_KEYCHAIN_FLAGS, "--window-size=800,600"):
            self.assertIn(flag, written)

    def test_no_other_test_launcher_starts_a_browser_without_them(self) -> None:
        bare = []
        for path in sorted(TESTS.rglob("*.py")):
            if path == Path(__file__).resolve():
                continue
            text = path.read_text(encoding="utf-8")
            if any(marker in text for marker in LAUNCH_MARKERS) \
                    and "NO_KEYCHAIN_FLAGS" not in text:
                bare.append(path.relative_to(TESTS).as_posix())

        self.assertEqual([], bare, "A file that starts a browser takes its flags from "
                         "tests.support.browser_session.NO_KEYCHAIN_FLAGS.")


if __name__ == "__main__":
    unittest.main()
