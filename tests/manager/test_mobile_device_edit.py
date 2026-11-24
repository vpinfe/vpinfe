"""A mobile device's name, address and port are fields on its panel, and an install's are not."""

from __future__ import annotations

import asyncio
import unittest
from typing import Any
from unittest.mock import AsyncMock, Mock, create_autospec, patch

from common.devices import device_client
from common.i18n import t
from manager.devices import devices
from manager.kit.api import ApiClient

ME = "Aaaa111111"
MOBILE = {"device_id": "m1", "kind": "vpx_mobile", "display_name": "Tablet",
          "address": "192.0.2.7", "port": 2112}
INSTALL = {"device_id": "Bbbb222222", "kind": "vpinfe", "display_name": "lab",
           "address": "192.0.2.8", "port": 8001}


def _context(device: dict[str, Any], state: str = device_client.ANSWERING) -> dict[str, Any]:
    return {"device": dict(device), "local_device_id": ME,
            "reach": {"state": state, "reason": ""},
            "library": Mock(), "state": {}, "rebuild": AsyncMock()}


def _client() -> Mock:
    """The API client the panel writes through, held to the real class's methods."""
    return create_autospec(ApiClient, instance=True)


async def _inline(call: Any, *args: Any) -> Any:
    return call(*args)


class _Fields:
    """`panel.field` swapped for something that keeps what each field was given."""

    def __init__(self) -> None:
        self.made: list[dict[str, Any]] = []

    def __call__(self, value: str, on_save: Any, **kw: Any) -> str:
        self.made.append({"value": value, "save": on_save, **kw})
        return f"field:{len(self.made) - 1}"

    def saving(self, label: str) -> Any:
        return self.made[int(label.split(":")[1])]["save"]


def _row(rows: list[tuple[Any, Any]], label: str) -> Any:
    return next(value for name, value in rows if name == label)


class OnAMobileDevicePanel(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.fields = _Fields()
        self.client = _client()
        for target in (patch.object(devices, "ApiClient", return_value=self.client),
                       patch.object(devices.panel, "field", self.fields),
                       patch.object(devices.offload, "io", _inline),
                       patch.object(devices.background_tasks, "create",
                                    lambda task, **_kw: asyncio.ensure_future(task))):
            target.start()
            self.addCleanup(target.stop)

    async def test_name_is_a_field_with_the_product_name_for_a_placeholder(self) -> None:
        rows = await devices._identity_rows(_context(MOBILE))

        saved = self.fields.made[0]
        self.assertEqual((saved["value"], saved["placeholder"]),
                         ("Tablet", t("manager.devices.mobile_name")))
        self.assertEqual(_row(rows, t("word.name")), "field:0")
        self.assertNotIn(t("manager.devices.name_note"),
                         [v for _l, v in rows if isinstance(v, str)])

    async def test_a_cleared_name_is_stored_as_the_product_name(self) -> None:
        context = _context(MOBILE)
        context["retitle"] = Mock()
        await devices._identity_rows(context)

        said = await self.fields.saving("field:0")("  ")

        self.assertEqual(said, "")
        self.client.edit_device.assert_called_once_with(
            "m1", "192.0.2.7", 2112, t("manager.devices.mobile_name"))
        context["retitle"].assert_called_once_with()
        context["rebuild"].assert_not_awaited()

    async def test_address_and_port_are_fields(self) -> None:
        rows = devices._connection_rows(_context(MOBILE), "192.0.2.7")

        self.assertEqual(_row(rows, t("manager.devices.address")), "field:0")
        self.assertEqual(_row(rows, t("manager.devices.port")), "field:1")
        self.assertEqual([made["value"] for made in self.fields.made], ["192.0.2.7", "2112"])
        self.assertTrue(all(made["refuses"] for made in self.fields.made))

    async def test_a_new_address_is_written_with_the_rest_of_the_entry_kept(self) -> None:
        context = _context(MOBILE)
        devices._connection_rows(context, "192.0.2.7")

        said = await self.fields.saving("field:0")(" 192.0.2.50 ")

        self.assertEqual(said, "")
        self.client.edit_device.assert_called_once_with(
            "m1", "192.0.2.50", 2112, "Tablet")

    async def test_a_new_port_is_written_with_the_rest_of_the_entry_kept(self) -> None:
        context = _context(MOBILE)
        devices._connection_rows(context, "192.0.2.7")

        said = await self.fields.saving("field:1")("2113")

        self.assertEqual(said, "")
        self.client.edit_device.assert_called_once_with(
            "m1", "192.0.2.7", 2113, "Tablet")

    async def test_what_is_not_an_address_or_a_port_is_refused_and_not_written(self) -> None:
        context = _context(MOBILE)
        devices._connection_rows(context, "192.0.2.7")

        self.assertEqual(await self.fields.saving("field:0")(""),
                         t("error.devices.address_needed"))
        self.assertEqual(await self.fields.saving("field:0")("http://192.0.2.7"),
                         t("error.devices.address_invalid"))
        self.assertEqual(await self.fields.saving("field:1")("0"),
                         t("manager.devices.port_invalid"))
        self.client.edit_device.assert_not_called()
        context["rebuild"].assert_not_awaited()

    async def test_a_write_that_fails_says_why_and_redraws_nothing(self) -> None:
        context = _context(MOBILE)
        self.client.edit_device.side_effect = RuntimeError("Refused")
        devices._connection_rows(context, "192.0.2.7")

        said = await self.fields.saving("field:0")("192.0.2.50")

        self.assertTrue(said)
        context["rebuild"].assert_not_awaited()

    async def test_a_second_edit_starts_from_what_the_first_wrote(self) -> None:
        context = _context(MOBILE)
        devices._connection_rows(context, "192.0.2.7")

        await self.fields.saving("field:0")("192.0.2.50")
        await self.fields.saving("field:1")("2113")

        self.assertEqual(self.client.edit_device.call_args.args,
                         ("m1", "192.0.2.50", 2113, "Tablet"))

    async def test_a_new_address_forgets_what_was_probed_at_the_old_one_and_asks_again(
            self) -> None:
        asked: list[str] = []

        async def ask(device_id: str) -> None:
            asked.append(device_id)

        context = _context(MOBILE)
        context["state"] = {"device_reach": {"m1": {"state": "x"}},
                            "device_actions": {"m1": ["y"]}, "ask_device": ask,
                            "device_landed": Mock()}
        devices._connection_rows(context, "192.0.2.7")

        await self.fields.saving("field:0")("192.0.2.50")
        await asyncio.sleep(0)

        self.assertEqual(context["state"]["device_reach"], {})
        self.assertEqual(context["state"]["device_actions"], {})
        self.assertEqual(asked, ["m1"])
        context["state"]["device_landed"].assert_called_once_with("m1")
        context["rebuild"].assert_awaited_once()

    async def test_a_new_name_keeps_what_was_probed(self) -> None:
        context = _context(MOBILE)
        context["state"] = {"device_reach": {"m1": {"state": "x"}}, "ask_device": AsyncMock()}
        await devices._identity_rows(context)

        await self.fields.saving("field:0")("Cab tablet")

        self.assertEqual(context["state"]["device_reach"], {"m1": {"state": "x"}})
        context["state"]["ask_device"].assert_not_called()

    async def test_what_to_check_waits_for_an_answer_that_is_no(self) -> None:
        context = _context(MOBILE)
        context["reach"] = None
        with patch.object(devices.panel, "note", lambda said: ("note", said)):
            rows = devices._connection_rows(context, "192.0.2.7")
        self.assertNotIn(("note", t("manager.devices.mobile_check")), rows)

    async def test_what_to_check_shows_only_while_it_is_not_answering(self) -> None:
        check = ("note", t("manager.devices.mobile_check"))
        with patch.object(devices.panel, "note", lambda said: ("note", said)):
            silent = devices._connection_rows(
                _context(MOBILE, device_client.UNREACHABLE), "192.0.2.7")
            answering = devices._connection_rows(_context(MOBILE), "192.0.2.7")

        self.assertIn(check, silent)
        self.assertNotIn(check, answering)


class OnAnInstancePanel(unittest.IsolatedAsyncioTestCase):
    async def test_its_address_stays_a_fact_and_its_name_is_not_a_field(self) -> None:
        fields = _Fields()
        with patch.object(devices.panel, "field", fields):
            rows = devices._connection_rows(_context(INSTALL), "192.0.2.8")
            identity = await devices._identity_rows(_context(INSTALL))

        self.assertEqual(_row(rows, t("manager.devices.address")), "192.0.2.8")
        self.assertEqual(fields.made, [])
        self.assertEqual(_row(identity, t("word.name")), "lab")


class TheForgetQuestion(unittest.TestCase):
    def test_a_phone_is_not_promised_back_by_the_network(self) -> None:
        self.assertEqual(devices._forget_detail_key([MOBILE], "manager.devices.forget_detail"),
                         "manager.devices.forget_by_hand")
        self.assertEqual(devices._forget_detail_key([INSTALL, MOBILE], "x"),
                         "manager.devices.forget_by_hand")

    def test_an_install_still_says_it_comes_back_when_seen(self) -> None:
        self.assertEqual(devices._forget_detail_key([INSTALL], "manager.devices.forget_detail"),
                         "manager.devices.forget_detail")


if __name__ == "__main__":
    unittest.main()
