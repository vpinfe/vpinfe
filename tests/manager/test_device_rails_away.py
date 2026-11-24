"""A rail on a device that is not answering says so and asks nothing.

Asked anyway, the Software and Logs rails hold the panel for the whole request timeout,
and every rail opened meanwhile queues behind that build.
"""

import asyncio
import unittest
from unittest import mock

from common.devices import device_client
from common.i18n import t
from manager.devices import devices

LOCAL = "Aaaa111111"
OTHER = "Bbbb222222"


def _context(reach: dict | None, device_id: str = OTHER, **extra: object) -> dict:
    device = {"device_id": device_id, "kind": "vpinfe", "address": "10.0.0.9", "port": 8001}
    return {"device": device, "local_device_id": LOCAL, "reach": reach, "library": None,
            "state": {}, **extra}


def _client() -> mock.Mock:
    client = mock.Mock()
    client.logs.side_effect = AssertionError("the log was asked of a device not answering")
    client.update_check.side_effect = AssertionError("the version was asked of it")
    return client


def _states(rows: list) -> list:
    return [label for label, _value in rows if isinstance(label, str)]


class AwayTests(unittest.TestCase):
    def test_a_device_that_did_not_answer_the_probe_is_away(self) -> None:
        self.assertTrue(devices.away(_context({"state": device_client.UNREACHABLE})))

    def test_a_device_the_probe_has_not_reached_yet_is_away(self) -> None:
        self.assertTrue(devices.away(_context(None)))

    def test_a_device_that_answers_is_not(self) -> None:
        self.assertFalse(devices.away(_context({"state": device_client.ANSWERING})))

    def test_an_entry_with_no_port_is_left_to_the_rail(self) -> None:
        context = _context({"state": device_client.UNASKABLE})
        context["device"]["port"] = 0

        self.assertFalse(devices.away(context))

    def test_this_install_is_never_away(self) -> None:
        self.assertFalse(devices.away(_context({"state": device_client.UNREACHABLE}, LOCAL)))


class RailTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = _client()
        patch = mock.patch("common.devices.device_client.for_device", return_value=self.client)
        patch.start()
        self.addCleanup(patch.stop)

    def test_logs_ask_nothing_of_a_device_not_answering(self) -> None:
        context = _context({"state": device_client.UNREACHABLE, "reason": "It is off"})

        rows = asyncio.run(devices.log_rows(context))

        self.assertEqual(_states(rows), ["State", "Last seen"])
        self.client.logs.assert_not_called()

    def test_software_asks_nothing_of_a_device_not_answering(self) -> None:
        context = _context({"state": device_client.UNREACHABLE, "reason": "It is off"})

        rows = asyncio.run(devices.software_rows(context))

        self.assertEqual(_states(rows)[:2], ["State", "Last seen"])
        self.client.update_check.assert_not_called()

    def test_the_reason_is_said_under_the_state(self) -> None:
        with mock.patch.object(devices.panel, "note", new=lambda text: ("note", text)):
            rows = asyncio.run(devices.software_rows(
                _context({"state": device_client.UNREACHABLE, "reason": "It is off"})))

        self.assertIn(("note", "It is off"), rows)

    def test_a_probe_still_out_says_checking_and_asks_nothing(self) -> None:
        with mock.patch.object(devices.panel, "state", new=lambda text, level: (text, level)):
            rows = asyncio.run(devices.software_rows(_context(None)))

        self.assertEqual(rows[0], ("State", (t("manager.devices.checking"), "unknown")))
        self.client.update_check.assert_not_called()

    def test_try_again_is_offered_where_the_page_can_ask_again(self) -> None:
        without = _context({"state": device_client.UNREACHABLE})
        with_ask = _context({"state": device_client.UNREACHABLE},
                            state={"ask_device": mock.AsyncMock()})

        self.assertEqual(len(devices.away_rows(with_ask)), len(devices.away_rows(without)) + 1)

    def test_carrying_asks_nothing_of_a_mobile_device_not_answering(self) -> None:
        context = _context({"state": device_client.UNREACHABLE, "reason": "It is off"},
                           state={"ask_device": mock.AsyncMock()})
        context["device"]["kind"] = "vpx_mobile"
        api = mock.Mock()
        api.device_games.side_effect = AssertionError("the games were asked of it")

        with mock.patch.object(devices, "ApiClient", return_value=api), \
                mock.patch.object(devices.panel, "note", new=lambda text, hint="": ("note", text)):
            rows = asyncio.run(devices._carrying_rows(context))

        api.device_games.assert_not_called()
        self.assertEqual(rows[0], ("note", t("manager.devices.carrying_away")))
        self.assertEqual(len(rows), 2)
        self.assertNotIn(t("word.state"), [label for label, _value in rows])

    def test_carrying_still_asks_a_mobile_device_that_answers(self) -> None:
        context = _context({"state": device_client.ANSWERING})
        context["device"]["kind"] = "vpx_mobile"
        api = mock.Mock()
        api.device_games.return_value = []

        with mock.patch.object(devices, "ApiClient", return_value=api), \
                mock.patch.object(devices.panel, "note", new=lambda text, hint="": ("note", text)):
            asyncio.run(devices._carrying_rows(context))

        api.device_games.assert_called_once_with(OTHER)

    def test_a_device_that_answers_is_still_asked(self) -> None:
        self.client.logs.side_effect = None
        self.client.logs.return_value = {"records": []}

        asyncio.run(devices.log_rows(_context({"state": device_client.ANSWERING})))

        self.client.logs.assert_called_once()


if __name__ == "__main__":
    unittest.main()
