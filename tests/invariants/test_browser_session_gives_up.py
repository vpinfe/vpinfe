"""A BrowserSession call the browser never takes or never answers fails within the
session's timeout."""

from __future__ import annotations

import asyncio
import unittest
from typing import Any

from tests.support import browser_session


class _Stuck:
    async def send(self, _text: str) -> None:
        await asyncio.Event().wait()


class _Deaf:
    async def send(self, _text: str) -> None:
        return None


class SendGivesUpTests(unittest.TestCase):
    def _send_through(self, socket: Any) -> None:
        session = browser_session.BrowserSession("ignored-binary", timeout=0.05)
        session._ws = socket

        async def call() -> None:
            await asyncio.wait_for(session.send("Runtime.evaluate"), 5)

        asyncio.run(call())

    def test_a_message_the_browser_never_takes(self) -> None:
        with self.assertRaisesRegex(TimeoutError, "never taken"):
            self._send_through(_Stuck())

    def test_an_answer_that_never_comes(self) -> None:
        with self.assertRaisesRegex(TimeoutError, "did not answer"):
            self._send_through(_Deaf())


if __name__ == "__main__":
    unittest.main()
