"""The Network page: the address another device opens, and a bind the list does not offer."""

from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import patch

from common.config import config_schema
from common.i18n import t
from manager.settings import settings


async def _inline(work: Any) -> Any:
    return work()


class AddressLineTests(unittest.IsolatedAsyncioTestCase):
    async def _under(self, values: dict[str, Any], address: str = "http://cab.local:8001"):
        with patch.object(settings.offload, "io", _inline), \
                patch.object(settings.addresses, "best", return_value=address):
            return await settings._network_address(values)

    async def test_the_address_is_drawn_under_the_port(self) -> None:
        under = await self._under({})

        self.assertEqual(list(under), ["http_port"])
        self.assertEqual(len(under["http_port"]), 1)

    async def test_the_default_answers_the_network_so_the_default_has_a_line(self) -> None:
        self.assertTrue(await self._under({"network": {"http_bind": ""}}))

    async def test_a_device_that_answers_only_itself_has_no_other_device_to_tell(self) -> None:
        self.assertEqual(await self._under({"network": {"http_bind": "127.0.0.1"}}), {})

    async def test_no_address_to_give_draws_nothing(self) -> None:
        self.assertEqual(await self._under({}, address=""), {})


class ReachableFromTests(unittest.TestCase):
    OPTION = {"type": "choice", "default": "0.0.0.0", "choices": ["0.0.0.0", "127.0.0.1"],
              "choice_labels": {"0.0.0.0": "Your Network", "127.0.0.1": "This Device Only"}}

    def _shown(self, value: str) -> tuple[Any, str]:
        with patch.object(settings.panel, "select") as select:
            settings.control_for(self.OPTION, value, lambda _v: True)
        args = select.call_args.args
        return args[0], args[1]

    def test_the_two_answers_are_named_for_the_person(self) -> None:
        offered, shown = self._shown("127.0.0.1")

        self.assertEqual(offered, {"0.0.0.0": "Your Network", "127.0.0.1": "This Device Only"})
        self.assertEqual(shown, "127.0.0.1")

    def test_an_address_the_list_does_not_offer_is_drawn_as_itself(self) -> None:
        offered, shown = self._shown("192.168.1.20")

        self.assertEqual(shown, "192.168.1.20")
        self.assertEqual(offered["192.168.1.20"], "192.168.1.20")

    def test_nothing_stored_is_the_default(self) -> None:
        self.assertEqual(self._shown("")[1], "0.0.0.0")


class SchemaTests(unittest.TestCase):
    def test_the_page_reads_port_first_and_the_window_port_last(self) -> None:
        order = [e.key for e in config_schema.settable() if e.section == "network"]

        self.assertEqual(order, ["http_port", "http_bind", "ws_port", "library_url",
                                 "verify_shared_library"])

    def test_the_headings_name_what_the_rows_set(self) -> None:
        self.assertEqual(
            [t(f"config.group.{token}") for token in ("connections", "shared_library")],
            ["Connections", "Shared Library"])


if __name__ == "__main__":
    unittest.main()
