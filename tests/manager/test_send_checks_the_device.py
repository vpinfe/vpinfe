"""Sending asks every mobile device first, and a device that does not answer stays visible."""

from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import AsyncMock, MagicMock, Mock, create_autospec, patch

from common.devices import device_client
from manager.devices import send_to_device
from manager.kit import confirm
from manager.kit.api import ApiClient

ANSWERING = {"state": device_client.ANSWERING, "version": "", "reason": ""}
SILENT = {"state": device_client.UNREACHABLE, "version": "", "reason": "Asleep"}
TABLET = {"device_id": "m1", "kind": "vpx_mobile", "display_name": "Tablet",
          "address": "192.0.2.7", "port": 2112}
PHONE = {"device_id": "m2", "kind": "vpx_mobile", "display_name": "Mobile",
         "address": "192.0.2.8", "port": 2112}
GAMES = [{"id": "g1", "name": "Alpha"}, {"id": "g2", "name": "Beta"}]


class _Ask:
    """`ask_where` run with the devices, their answers and each dialog stood in for."""

    def __init__(self, devices: list[dict[str, Any]], answers: dict[str, dict[str, Any]],
                 picks: dict[str, Any] | None = None, *, plan: list[str] | None = None,
                 answer: confirm.Answer = confirm.Answer.CONFIRM) -> None:
        self.which = AsyncMock(return_value=picks)
        self.confirm = AsyncMock(return_value=answer)
        self.plan = plan or []
        self.send = AsyncMock()
        self.devices = devices
        self.answers = answers

    async def run(self) -> None:
        client = create_autospec(ApiClient, instance=True)
        client.devices.return_value = self.devices
        client.plan_send_to_device.return_value = self.plan
        with patch.object(send_to_device.ui, "context", MagicMock()), \
                patch.object(send_to_device, "ApiClient", return_value=client), \
                patch.object(send_to_device.offload, "io", self._inline), \
                patch.object(send_to_device, "_asked", AsyncMock(return_value=self.answers)), \
                patch.object(send_to_device, "_which", self.which), \
                patch.object(send_to_device.confirm, "ask", self.confirm), \
                patch.object(send_to_device, "send", self.send):
            await send_to_device.ask_where(GAMES)

    @staticmethod
    async def _inline(call: Any, *args: Any) -> Any:
        return call(*args)


class WhichDevice(unittest.IsolatedAsyncioTestCase):
    async def test_one_device_that_answers_skips_the_picker(self) -> None:
        ask = _Ask([TABLET], {"m1": ANSWERING})
        await ask.run()

        ask.which.assert_not_awaited()
        assert ask.send.await_args is not None
        self.assertEqual(ask.send.await_args.args[1], TABLET)

    async def test_one_device_that_does_not_answer_is_shown_dimmed_not_skipped(self) -> None:
        ask = _Ask([TABLET], {"m1": SILENT})
        await ask.run()

        ask.which.assert_awaited_once_with([TABLET], {"m1": SILENT})
        ask.send.assert_not_awaited()

    async def test_several_devices_are_always_a_picker(self) -> None:
        ask = _Ask([TABLET, PHONE], {"m1": ANSWERING, "m2": ANSWERING}, picks=PHONE)
        await ask.run()

        ask.which.assert_awaited_once()
        assert ask.send.await_args is not None
        self.assertEqual(ask.send.await_args.args[1], PHONE)

    async def test_cancelling_the_picker_sends_nothing_and_does_not_confirm(self) -> None:
        ask = _Ask([TABLET, PHONE], {"m1": SILENT, "m2": SILENT}, picks=None)
        await ask.run()

        ask.confirm.assert_not_awaited()
        ask.send.assert_not_awaited()


class TheConfirm(unittest.IsolatedAsyncioTestCase):
    async def test_the_main_answer_sends_without_the_settings_file(self) -> None:
        ask = _Ask([TABLET], {"m1": ANSWERING})
        await ask.run()

        assert ask.send.await_args is not None
        self.assertIs(ask.send.await_args.kwargs["settings"], False)

    async def test_the_second_answer_sends_with_it(self) -> None:
        ask = _Ask([TABLET], {"m1": ANSWERING}, answer=confirm.Answer.SECOND)
        await ask.run()

        assert ask.send.await_args is not None
        self.assertIs(ask.send.await_args.kwargs["settings"], True)

    async def test_cancelling_sends_nothing(self) -> None:
        ask = _Ask([TABLET], {"m1": ANSWERING}, answer=confirm.Answer.CANCEL)
        await ask.run()

        ask.send.assert_not_awaited()

    async def test_a_game_with_a_mobile_settings_file_is_named_in_the_confirm(self) -> None:
        ask = _Ask([TABLET], {"m1": ANSWERING}, plan=["g2"])
        await ask.run()

        assert ask.confirm.await_args is not None
        kwargs = ask.confirm.await_args.kwargs
        self.assertEqual(kwargs["lines"], ["Beta"])
        self.assertEqual(len(kwargs["detail"]), 2)
        self.assertTrue(kwargs["detail"][1])

    async def test_with_none_the_confirm_does_not_mention_them(self) -> None:
        ask = _Ask([TABLET], {"m1": ANSWERING})
        await ask.run()

        assert ask.confirm.await_args is not None
        kwargs = ask.confirm.await_args.kwargs
        self.assertEqual(kwargs["lines"], [])
        self.assertFalse(kwargs["detail"][1])

    async def test_a_plan_that_cannot_be_read_does_not_stop_the_send(self) -> None:
        ask = _Ask([TABLET], {"m1": ANSWERING})
        client = create_autospec(ApiClient, instance=True)
        client.devices.return_value = [TABLET]
        client.plan_send_to_device.side_effect = RuntimeError("down")
        with patch.object(send_to_device.ui, "context", MagicMock()), \
                patch.object(send_to_device, "ApiClient", return_value=client), \
                patch.object(send_to_device.offload, "io", ask._inline), \
                patch.object(send_to_device, "_asked", AsyncMock(return_value=ask.answers)), \
                patch.object(send_to_device.confirm, "ask", ask.confirm), \
                patch.object(send_to_device, "send", ask.send):
            await send_to_device.ask_where(GAMES)

        ask.send.assert_awaited_once()


class AskingEveryDevice(unittest.IsolatedAsyncioTestCase):
    async def _asked(self, client: Mock, found: list[dict[str, Any]]) -> dict[str, Any]:
        async def inline(call: Any, *args: Any) -> Any:
            return call(*args)

        with patch.object(send_to_device, "ApiClient", return_value=client), \
                patch.object(send_to_device.offload, "io", inline), \
                patch.object(send_to_device.ui, "notification", MagicMock()):
            return await send_to_device._asked(found)

    async def test_each_device_is_asked_and_its_answer_kept_under_its_id(self) -> None:
        client = create_autospec(ApiClient, instance=True)
        client.probe_device.side_effect = lambda device_id: (
            ANSWERING if device_id == "m1" else SILENT)

        answers = await self._asked(client, [TABLET, PHONE])

        self.assertEqual(answers, {"m1": ANSWERING, "m2": SILENT})

    async def test_a_device_that_cannot_be_asked_reads_as_not_answering(self) -> None:
        client = create_autospec(ApiClient, instance=True)
        client.probe_device.side_effect = RuntimeError("Down")

        answers = await self._asked(client, [TABLET])

        self.assertEqual(answers["m1"]["state"], device_client.UNREACHABLE)
        self.assertTrue(answers["m1"]["reason"])


class OneRowOfThePicker(unittest.TestCase):
    def setUp(self) -> None:
        self.panel = MagicMock()
        self.ui = MagicMock()
        for target in (patch.object(send_to_device, "panel", self.panel),
                       patch.object(send_to_device, "ui", self.ui)):
            target.start()
            self.addCleanup(target.stop)

    def test_a_device_that_answers_is_a_verb_that_picks_it(self) -> None:
        box = Mock()

        send_to_device._row(TABLET, ANSWERING, box, Mock())
        self.panel.action.call_args.args[1]()

        self.assertEqual(self.panel.action.call_args.args[0], "Tablet")
        self.assertNotIn("enabled", self.panel.action.call_args.kwargs)
        box.submit.assert_called_once_with(TABLET)

    def test_a_device_that_does_not_answer_is_dimmed_with_its_address_and_reason(self) -> None:
        send_to_device._row(TABLET, SILENT, Mock(), Mock())

        dimmed = self.panel.action.call_args_list[0]
        self.assertEqual((dimmed.args[0], dimmed.kwargs["enabled"]), ("Tablet", False))
        said = [call.args[0] for call in self.panel.line.call_args_list]
        self.assertEqual(said, ["192.0.2.7:2112", "Asleep"])

    def test_try_again_asks_that_device_again(self) -> None:
        again = Mock()

        send_to_device._row(TABLET, SILENT, Mock(), again)
        self.panel.action.call_args_list[1].args[1]()

        again.assert_called_once_with(TABLET)


if __name__ == "__main__":
    unittest.main()
