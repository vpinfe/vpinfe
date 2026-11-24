"""What a theme is told when a call it made fails."""

from __future__ import annotations

import contextlib
import json
import unittest
from typing import Any, cast

from common.i18n import t
from common.service_errors import NotFoundError
from frontend.device_channel import DeviceChannel


class _Socket:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send(self, text: str) -> None:
        self.sent.append(json.loads(text))


class _Raises:
    def __init__(self, exc: Exception) -> None:
        self.exc = exc

    def get_tables(self, *args):
        raise self.exc


class CallFailureTests(unittest.IsolatedAsyncioTestCase):
    async def _answer(self, exc: Exception, *, logged: bool) -> dict:
        channel = DeviceChannel(port=0)
        channel.register_api("playfield", cast(Any, _Raises(exc)))
        socket = _Socket()
        watching = (self.assertLogs("vpinfe.frontend.device_channel", "ERROR") if logged
                    else contextlib.nullcontext())
        with watching:
            await channel._handle_api_call("playfield", cast(Any, socket), {
                "type": "api_call", "id": "7", "method": "get_tables", "args": []})
        [answer] = socket.sent
        self.assertEqual(answer["id"], "7")
        self.assertNotIn("result", answer)
        return answer

    async def test_a_refusal_reaches_the_theme_in_its_own_words(self) -> None:
        answer = await self._answer(NotFoundError("Nothing goes by that name"), logged=False)
        self.assertEqual(answer["error"], "Nothing goes by that name")

    async def test_anything_else_is_worded_and_logged(self) -> None:
        answer = await self._answer(KeyError("vpx_path"), logged=True)
        self.assertEqual(answer["error"], t("error.frontend.call_failed", method="get_tables"))

    async def test_an_exception_with_no_text_is_still_an_error(self) -> None:
        answer = await self._answer(RuntimeError(), logged=True)
        self.assertTrue(answer["error"])


if __name__ == "__main__":
    unittest.main()
