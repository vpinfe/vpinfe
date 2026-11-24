"""The question put before a device replaces itself with the published build."""

from __future__ import annotations

import inspect
import re
import threading
import unittest
from unittest.mock import AsyncMock, Mock, patch

import requests

from common.i18n import t
from common.online import app_updater
from manager.devices import devices
from manager.kit import verbs
from tests.support.clicks import press

UPDATE = {"latest_version": "v3.1.0"}


class UpdateConfirm(unittest.IsolatedAsyncioTestCase):
    async def _ask(self, playing: dict) -> AsyncMock:
        ask = AsyncMock(return_value=False)
        with patch.object(devices.offload, "io", new=AsyncMock(return_value=playing)), \
                patch.object(devices.confirm, "ask", new=ask), \
                patch.object(devices.ui, "notify", new=Mock()):
            await press(devices.confirm_update, Mock(), "Basement", UPDATE)
        return ask

    async def test_with_nothing_running_it_asks_to_update(self) -> None:
        ask = await self._ask({"launching": False})

        self.assertEqual(ask.call_args.kwargs["confirm"], t("manager.devices.update"))
        self.assertEqual(ask.call_args.kwargs["icon"], verbs.UPDATE)
        self.assertFalse(ask.call_args.kwargs["danger"])

    async def test_with_a_table_running_it_asks_to_stop_it_first(self) -> None:
        ask = await self._ask({"launching": True, "game_name": "Medieval Madness"})

        self.assertEqual(ask.call_args.kwargs["confirm"],
                         t("manager.devices.stop_confirm"))
        self.assertEqual(ask.call_args.kwargs["icon"], verbs.STOP)
        self.assertTrue(ask.call_args.kwargs["danger"])


class EachAnswerLandsOnItsOwn(unittest.IsolatedAsyncioTestCase):
    """A device that refuses at once is shown so while one that is off is still timing out."""

    OFF = {"device_id": "Oooo111111"}
    REFUSES = {"device_id": "Rrrr222222"}

    async def test_a_quick_answer_does_not_wait_behind_a_slow_one(self) -> None:
        refused = threading.Event()
        landed: list[str] = []

        def probe(device_id: str) -> dict:
            if device_id == self.OFF["device_id"]:
                refused.wait(timeout=2)
            return {"device_id": device_id, "state": "unreachable"}

        async def land(device_id: str, found: dict) -> None:
            landed.append(device_id)
            if device_id == self.REFUSES["device_id"]:
                refused.set()

        await devices.ask_each([self.OFF, self.REFUSES], probe, land)

        self.assertEqual(landed, [self.REFUSES["device_id"], self.OFF["device_id"]])

    async def test_one_that_could_not_be_asked_is_handed_back_and_the_rest_land(self) -> None:
        broken = RuntimeError("the API went away")
        landed: list[str] = []

        def probe(device_id: str) -> dict:
            if device_id == self.OFF["device_id"]:
                raise broken
            return {"device_id": device_id, "state": "unreachable"}

        async def land(device_id: str, found: dict) -> None:
            landed.append(device_id)

        failed = await devices.ask_each([self.OFF, self.REFUSES], probe, land)

        self.assertEqual(failed, [broken])
        self.assertEqual(landed, [self.REFUSES["device_id"]])


CHECKED = "2026-09-25T08:00:00Z"
FAILED = {"current_version": "v3.0.0", "latest_version": "v3.1.0", "update_available": True,
          "error": "remote_check_failed", "checked_at": CHECKED}


class SoftwareRows(unittest.TestCase):
    def _rows(self, update: dict, check: Mock | None = None) -> list:
        def state(text: str, level: str, *, beside: str = "", hint: str = "") -> tuple:
            return ("state", text, level, beside, hint)

        with patch.object(devices.panel, "state", new=state), \
                patch.object(devices.settings_page, "last_checked",
                             new=lambda checked, now: ("last checked", checked, now)):
            return devices._software_rows({}, True, Mock(), update, check)

    def test_a_failed_check_is_not_a_version_in_green(self) -> None:
        with patch.object(devices.when, "ago", new=lambda stamp, **_: "yesterday"):
            (label, chip), = self._rows(FAILED)

        self.assertEqual(label, t("word.version"))
        self.assertEqual(chip, ("state", t("manager.devices.check_failed"), "unknown",
                                "v3.0.0", t("manager.devices.last_checked", when="yesterday")))

    def test_a_check_that_never_worked_says_so(self) -> None:
        (_, chip), = self._rows({**FAILED, "checked_at": None})

        self.assertEqual(chip[-1], t("manager.devices.never_checked"))

    def test_a_current_answer_is_the_version(self) -> None:
        (_, chip), = self._rows({"current_version": "v3.1.0", "checked_at": CHECKED})

        self.assertEqual(chip[1:3], ("v3.1.0", "on"))

    def test_a_build_with_no_version_to_weigh_names_the_release_without_offering_it(
            self) -> None:
        with patch.object(devices.panel, "note", new=lambda text: ("note", text)):
            (_, chip), note = self._rows({
                "current_version": "dev-local", "latest_version": "v2.5.1",
                "update_available": None, "support_reason": "source_build",
                "checked_at": CHECKED})

        self.assertEqual(chip, ("state", t("manager.devices.latest", latest="v2.5.1"),
                                "unknown", "dev-local", ""))
        self.assertEqual(note, ("note", t("manager.devices.why_not.source")))

    def test_check_now_sits_under_every_answer(self) -> None:
        now = Mock()
        for update in (FAILED, {**FAILED, "error": None},
                       {"current_version": "v3.1.0", "checked_at": CHECKED}):
            self.assertEqual(self._rows(update, now)[-1], ("last checked", CHECKED, now))

    def test_no_answer_offers_nothing_to_check(self) -> None:
        rows = self._rows({}, Mock())

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][1][1], t("manager.devices.not_known"))


class NobodyAnswered(unittest.IsolatedAsyncioTestCase):
    """A version that could not be asked for says why, the way Connection does."""

    async def _rows(self, failure: Exception) -> list:
        context = {"device": {"device_id": "Bbbb222222", "address": "192.168.1.50",
                              "port": 8001},
                   "local_device_id": "Aaaa111111",
                   "reach": {"state": devices.device_client.ANSWERING}}
        with patch.object(devices.offload, "io", new=AsyncMock(side_effect=failure)), \
                patch.object(devices.panel, "state", new=lambda text, level: (text, level)), \
                patch.object(devices.panel, "note", new=lambda text: ("note", text)), \
                patch.object(devices.settings_page, "last_checked", new=Mock()):
            return await devices.software_rows(context)

    async def test_one_that_timed_out_may_be_asleep(self) -> None:
        rows = await self._rows(requests.ConnectTimeout())

        self.assertEqual(rows, [(t("word.version"), (t("manager.devices.not_known"), "unknown")),
                                ("note", t("device.reason.timed_out"))])

    async def test_nothing_on_its_port_is_said_as_that(self) -> None:
        refused = requests.ConnectionError(ConnectionRefusedError(61, "Connection refused"))

        self.assertEqual((await self._rows(refused))[-1], ("note", t("device.reason.refused")))


class EveryReasonIsSaid(unittest.TestCase):
    def test_every_reason_the_updater_gives_has_words(self) -> None:
        given = set(re.findall(r'\["(?:support_)?reason"\] = "(\w+)"',
                               inspect.getsource(app_updater)))

        self.assertIn("no_matching_asset", given)
        self.assertEqual(given - set(devices.WHY_NOT), set())

    def test_an_incomplete_release_is_not_blamed_on_the_device(self) -> None:
        with patch.object(devices.panel, "note", new=lambda text: ("note", text)):
            rows = devices._software_rows({}, True, Mock(), {
                "current_version": "v3.0.0", "latest_version": "v3.1.0",
                "update_available": True, "update_supported": False,
                "support_reason": "asset_not_attached_to_release"})

        self.assertEqual(rows[-1], ("note", t("manager.devices.why_not.release_incomplete")))


class CheckNow(unittest.IsolatedAsyncioTestCase):
    async def test_it_asks_the_device_to_ask_now_and_redraws(self) -> None:
        ask, rebuild = Mock(return_value={}), AsyncMock()
        io = AsyncMock(side_effect=lambda call, *args: call(*args))
        with patch.object(devices.offload, "io", new=io), \
                patch.object(devices.ui, "notification", new=Mock()):
            await press(devices._check_now({"rebuild": rebuild}, ask))

        ask.assert_called_once_with(True)
        rebuild.assert_awaited_once()

    async def test_a_device_that_does_not_answer_says_so_and_redraws_nothing(self) -> None:
        rebuild, notify = AsyncMock(), Mock()
        with patch.object(devices.offload, "io", new=AsyncMock(side_effect=OSError("down"))), \
                patch.object(devices.ui, "notification", new=Mock()), \
                patch.object(devices.ui, "notify", new=notify):
            await press(devices._check_now({"rebuild": rebuild, "device": {"name": "Basement"}},
                                           Mock()))

        rebuild.assert_not_awaited()
        self.assertEqual(notify.call_args.kwargs["type"], "negative")


if __name__ == "__main__":
    unittest.main()
