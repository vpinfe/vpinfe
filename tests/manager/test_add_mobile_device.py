"""Adding a VPX Mobile device by hand: what the dialog asks, refuses and sends."""

from __future__ import annotations

import asyncio
import unittest
from contextlib import ExitStack
from typing import Any
from unittest.mock import AsyncMock, MagicMock, Mock, patch

from common.devices import device_client
from common.i18n import t
from manager.devices import mobile_add, send_to_device
from manager.kit.api import ApiClient

ANSWERING = {"state": device_client.ANSWERING, "version": "", "reason": ""}
SILENT = {"state": device_client.UNREACHABLE, "version": "", "reason": "No answer"}
DRAWN = ("name", "address", "port")


class _Box:
    """The dialog: submitting it is what lets `await box` return."""

    def __init__(self) -> None:
        self.answered: asyncio.Future[Any] = asyncio.get_running_loop().create_future()

    def __enter__(self) -> _Box:
        return self

    def __exit__(self, *_exc: Any) -> None:
        return None

    def submit(self, value: Any) -> None:
        self.answered.set_result(value)

    def __await__(self) -> Any:
        return self.answered.__await__()


OFFLOADED: list[Any] = []


async def _inline(call: Any, *args: Any) -> Any:
    """`offload.io` run in place, noting what it was handed."""
    OFFLOADED.append(call)
    await asyncio.sleep(0)
    return call(*args)


class _Props(dict):
    """`control.props` is both a dict to read and a call that adds to it."""

    def __call__(self, *_words: Any) -> None:
        return None


class _Dialog:
    """`add_mobile` open, every control a Mock the test can type into."""

    def __init__(self, probe: dict[str, Any]) -> None:
        self.fields: dict[str, Mock] = {}
        self.keep: Any = None
        self.go = Mock(text=t("word.add"))
        self.client = Mock()
        self.client.add_device.return_value = {"device_id": "m1"}
        self.probed = Mock(return_value=probe)
        self.task: asyncio.Future[Any] | None = None

    def _field(self, value: str = "", **_kw: Any) -> Mock:
        control = Mock(value=value, props=_Props())
        self.fields[DRAWN[len(self.fields)]] = control
        return control

    def _facts(self, _target: Any, entries: list[tuple[Any, Any]]) -> None:
        for _label, draw in entries:
            draw()

    def _answer(self, _label: str, keep: Any, **_kw: Any) -> Mock:
        self.keep = keep
        return self.go

    async def open(self, stack: ExitStack, **typed: str) -> None:
        box = _Box()
        frame, panel = mobile_add.frame, mobile_add.panel
        for target, name, value in (
                (mobile_add.ui, "context", MagicMock()),
                (frame, "opened", Mock(return_value=box)),
                (frame, "field", Mock(side_effect=self._field)),
                (frame, "footer", MagicMock()),
                (frame, "cancel", Mock()),
                (frame, "answer", Mock(side_effect=self._answer)),
                (frame, "focus", Mock()),
                (frame, "enter_presses", Mock()),
                (panel, "facts", Mock(side_effect=self._facts)),
                (mobile_add.ui, "label", MagicMock()),
                (mobile_add, "ApiClient", Mock(return_value=self.client)),
                (mobile_add.device_client, "probe", self.probed),
                (mobile_add.device_client, "for_device", Mock()),
                (mobile_add.offload, "io", _inline)):
            stack.enter_context(patch.object(target, name, value))
        self.task = asyncio.ensure_future(mobile_add.add_mobile())
        await asyncio.sleep(0)
        for key, text in typed.items():
            self.fields[key].value = text


class AddMobileDevice(unittest.IsolatedAsyncioTestCase):
    async def _open(self, probe: dict[str, Any], **typed: str) -> _Dialog:
        dialog = _Dialog(probe)
        stack = ExitStack()
        self.addCleanup(stack.close)
        await dialog.open(stack, **typed)
        assert dialog.task is not None
        self.addCleanup(dialog.task.cancel)
        return dialog

    async def test_a_device_that_answers_is_added_with_the_port_it_was_given(self) -> None:
        dialog = await self._open(ANSWERING, name="Tablet", address="192.0.2.7", port="2112")
        await dialog.keep()
        assert dialog.task is not None
        self.assertEqual(await dialog.task, {"device_id": "m1"})
        dialog.client.add_device.assert_called_once_with("192.0.2.7", 2112, "Tablet")

    async def test_the_probe_and_the_add_are_both_taken_off_the_loop(self) -> None:
        OFFLOADED.clear()
        dialog = await self._open(ANSWERING, address="192.0.2.7", port="2112")
        await dialog.keep()
        self.assertEqual(OFFLOADED, [dialog.probed, dialog.client.add_device])

    async def test_a_second_press_while_the_first_is_asking_adds_nothing_more(self) -> None:
        dialog = await self._open(ANSWERING, address="192.0.2.7", port="2112")
        await asyncio.gather(dialog.keep(), dialog.keep())
        dialog.client.add_device.assert_called_once()

    async def test_the_refusal_to_add_is_said_under_the_address_and_the_dialog_stays(
            self) -> None:
        dialog = await self._open(ANSWERING, address="192.0.2.7", port="2112")
        dialog.client.add_device.side_effect = RuntimeError("Refused")
        await dialog.keep()
        assert dialog.task is not None
        self.assertFalse(dialog.task.done())
        self.assertTrue(dialog.fields["address"].props["error"])

    async def test_an_address_with_a_scheme_or_a_port_is_refused(self) -> None:
        dialog = await self._open(ANSWERING, address="http://192.0.2.7", port="2112")
        for typed in ("http://192.0.2.7", "192.0.2.7:2112", "192.0.2.7/files"):
            dialog.fields["address"].value = typed
            await dialog.keep()
            self.assertEqual(dialog.fields["address"].props["error-message"],
                             t("error.devices.address_invalid"), typed)
        dialog.probed.assert_not_called()

    async def test_a_blank_name_is_stored_as_the_product_name(self) -> None:
        dialog = await self._open(ANSWERING, name="", address="192.0.2.7", port="2112")
        await dialog.keep()
        assert dialog.task is not None
        await dialog.task
        dialog.client.add_device.assert_called_once_with(
            "192.0.2.7", 2112, t("manager.devices.mobile_name"))

    async def test_a_device_that_does_not_answer_says_why_and_is_added_on_the_second_press(
            self) -> None:
        dialog = await self._open(SILENT, address="192.0.2.7", port="2112")
        await dialog.keep()
        assert dialog.task is not None
        self.assertFalse(dialog.task.done())
        dialog.client.add_device.assert_not_called()
        self.assertEqual(dialog.fields["address"].props["error-message"], "No answer")
        self.assertEqual(dialog.go.text, t("manager.devices.add_anyway"))

        await dialog.keep()
        self.assertEqual(await dialog.task, {"device_id": "m1"})
        self.assertEqual(dialog.probed.call_count, 1)

    async def test_changing_the_address_asks_again(self) -> None:
        dialog = await self._open(SILENT, address="192.0.2.7", port="2112")
        await dialog.keep()
        self.assertEqual(dialog.probed.call_count, 1)

        forget = dialog.fields["address"].on_value_change.call_args[0][0]
        forget()
        self.assertEqual(dialog.go.text, t("word.add"))
        await dialog.keep()
        self.assertEqual(dialog.probed.call_count, 2)

    async def test_no_address_and_no_port_are_refused_before_anything_is_asked(self) -> None:
        dialog = await self._open(ANSWERING, address="", port="2112")
        await dialog.keep()
        self.assertEqual(dialog.fields["address"].props["error-message"],
                         t("error.devices.address_needed"))

        dialog.fields["address"].value = "192.0.2.7"
        for bad in ("", "0", "65536", "port"):
            dialog.fields["port"].value = bad
            await dialog.keep()
            self.assertEqual(dialog.fields["port"].props["error-message"],
                             t("manager.devices.port_invalid"), bad)
        dialog.probed.assert_not_called()
        dialog.client.add_device.assert_not_called()


class TheRequest(unittest.TestCase):
    ITEM = {"id": "m1", "name": "Tablet", "address": "192.0.2.7", "port": 2112}

    def test_a_device_is_listed_as_a_mobile_device_with_the_name_given(self) -> None:
        with patch.object(ApiClient, "_post", return_value=self.ITEM) as post:
            added = ApiClient().add_device("192.0.2.7", 2112, "Tablet")

        post.assert_called_once_with("/config/mobile_devices", {
            "name": "Tablet", "address": "192.0.2.7", "port": 2112})
        self.assertEqual(added, {"device_id": "m1", "kind": "vpx_mobile",
                                 "display_name": "Tablet", "address": "192.0.2.7",
                                 "port": 2112})

    def test_an_edit_rewrites_the_listed_entry_under_its_id(self) -> None:
        with patch.object(ApiClient, "_put", return_value=self.ITEM) as put:
            ApiClient().edit_device("m1", "192.0.2.7", 2112, "Tablet")

        put.assert_called_once_with("/config/mobile_devices/m1", {
            "name": "Tablet", "address": "192.0.2.7", "port": 2112})

    def test_forgetting_a_mobile_device_removes_its_list_entry(self) -> None:
        with patch.object(ApiClient, "_delete") as delete:
            ApiClient().forget_device("m1", "vpx_mobile")

        delete.assert_called_once_with("/config/mobile_devices/m1")


class SendWithNoMobileDevice(unittest.IsolatedAsyncioTestCase):
    async def _ask(self, *, wants: bool, added: dict | None) -> tuple[Mock, Mock]:
        send = AsyncMock()
        add = AsyncMock(return_value=added)
        confirm = AsyncMock(side_effect=[wants, True])
        with patch.object(mobile_add.ui, "context", MagicMock()), \
                patch.object(send_to_device, "ApiClient", return_value=Mock(devices=Mock())), \
                patch.object(send_to_device.offload, "io", AsyncMock(return_value=[])), \
                patch.object(send_to_device, "_asked",
                             AsyncMock(return_value={"m1": ANSWERING})), \
                patch.object(send_to_device.confirm, "ask", confirm), \
                patch.object(send_to_device.mobile_add, "add_mobile", add), \
                patch.object(send_to_device, "send", send):
            await send_to_device.ask_where([{"id": "g1"}])
        return add, send

    async def test_the_picker_offers_to_add_one_and_sends_to_it(self) -> None:
        add, send = await self._ask(wants=True, added={"device_id": "m1", "display_name": "Tablet"})
        add.assert_awaited_once()
        self.assertEqual(send.await_args.args[1]["device_id"], "m1")

    async def test_declining_the_offer_sends_nothing(self) -> None:
        add, send = await self._ask(wants=False, added=None)
        add.assert_not_awaited()
        send.assert_not_awaited()

    async def test_cancelling_the_add_sends_nothing(self) -> None:
        add, send = await self._ask(wants=True, added=None)
        add.assert_awaited_once()
        send.assert_not_awaited()


class PortOf(unittest.TestCase):
    def test_a_port_is_a_number_in_range(self) -> None:
        self.assertEqual(mobile_add.port_of(" 2112 "), 2112)
        self.assertEqual(mobile_add.port_of("65535"), 65535)
        for bad in ("", "0", "65536", "-1", "21x2", "2.5"):
            self.assertIsNone(mobile_add.port_of(bad), bad)


if __name__ == "__main__":
    unittest.main()
