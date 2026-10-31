"""The Devices page: which devices' browsers can't play what VPinFE shows."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

import requests

from common.devices import device_client
from common.host import frontend_browser as fb
from common.i18n import t
from manager.devices import browser_state, devices

CAB = {"device_id": "Bbbb222222", "address": "192.168.1.50", "port": 8001,
       "browser": {"state": "no_h264", "name": "Chromium 145.0.7632.0",
                   "checked_at": "2026-09-27T12:00:00Z"}}


def _state(text: str, level: str, *, beside: str = "", hint: str = "") -> tuple:
    return ("state", text, level, beside, hint)


class GridTests(unittest.TestCase):
    def _cell(self, device: dict, probe: dict | None = None) -> str:
        reach = {device["device_id"]: probe} if probe is not None else None
        [row] = devices.rows([device], reach)
        return row["browser"]

    def test_a_device_switched_off_shows_what_it_last_said(self) -> None:
        self.assertEqual(self._cell(CAB, {"state": "unreachable"}), "no_h264")

    def test_a_probe_that_heard_a_new_answer_shows_that(self) -> None:
        probe = {"state": "answering", "browser": {"state": "plays", "name": "Chrome"}}

        self.assertEqual(self._cell(CAB, probe), "plays")

    def test_a_device_never_asked_shows_nothing(self) -> None:
        self.assertEqual(self._cell({"device_id": "Pppp444444"}), "")

    def test_only_the_states_worth_noticing_draw_a_chip(self) -> None:
        self.assertEqual(set(browser_state.STATES),
                         {fb.NO_H264, fb.NO_VIDEO, fb.NO_BROWSER, fb.UNKNOWN})

    def test_the_column_is_in_the_view_the_page_opens_on(self) -> None:
        opens_on = devices.VIEWS["manager.view.everything"]
        assert not isinstance(opens_on, list)

        self.assertIn("browser", opens_on.columns)
        self.assertIn("browser", [one["field"] for one in devices.COLUMNS])

    def test_the_all_devices_view_says_the_order_it_sorts_in(self) -> None:
        opens_on = devices.VIEWS["manager.view.everything"]
        assert not isinstance(opens_on, list)
        by_state = next(one for one in opens_on.sort if one["colId"] == "state")
        found = {f"D{index}": {"state": state}
                 for index, state in enumerate(devices._REACH)}
        built = devices.rows([{"device_id": key, "display_name": key, "kind": "vpinfe"}
                              for key in found], found)
        rank = devices.REACH.rank()
        order = sorted(built, key=lambda row: rank[row["state"]],
                       reverse=by_state["sort"] == "desc")

        self.assertEqual(order[0]["state"], device_client.ANSWERING)
        self.assertEqual(t("manager.devices.all_view.help"), "Every device, answering first")


class SoftwareTests(unittest.IsolatedAsyncioTestCase):
    async def _rows(self, device: dict, reach: str = "unreachable") -> list:
        context = {"device": device, "local_device_id": "Aaaa111111",
                   "reach": {"state": reach}}
        with patch.object(devices.offload, "io",
                          new=AsyncMock(side_effect=requests.ConnectTimeout())), \
                patch.object(devices.panel, "state", new=_state), \
                patch.object(devices.panel, "note", new=lambda text: ("note", text)), \
                patch.object(devices.when, "ago", new=lambda stamp, **_: "yesterday"):
            return await devices.software_rows(context)

    async def test_a_limited_browser_is_named_beside_its_chip(self) -> None:
        rows = await self._rows(CAB)

        self.assertEqual(rows[-2], (t("manager.devices.browser"),
                                    ("state", t("manager.devices.browser_no_h264"), "warn",
                                     "Chromium 145.0.7632.0",
                                     t("frontend_browser.finding.no_h264"))))

    async def test_a_device_that_did_not_answer_says_how_old_the_answer_is(self) -> None:
        rows = await self._rows(CAB)

        self.assertEqual(rows[-1], ("note", t("manager.devices.last_checked", when="yesterday")))

    async def test_one_that_just_answered_says_nothing_of_its_age(self) -> None:
        rows = await self._rows(CAB, reach="answering")

        self.assertEqual(rows[-1][0], t("manager.devices.browser"))

    async def test_one_that_plays_everything_is_just_its_name(self) -> None:
        plays = {"device_id": "Bbbb222222", "address": "192.168.1.50", "port": 8001,
                 "browser": {"state": "plays", "name": "Google Chrome 154.0.8037.58"}}

        rows = await self._rows(plays)

        self.assertEqual(rows[-1], (t("manager.devices.browser"),
                                    "Google Chrome 154.0.8037.58"))

    async def test_a_device_that_never_said_has_no_browser_row(self) -> None:
        rows = await self._rows({k: v for k, v in CAB.items() if k != "browser"})

        self.assertNotIn(t("manager.devices.browser"), [label for label, _ in rows])


if __name__ == "__main__":
    unittest.main()
