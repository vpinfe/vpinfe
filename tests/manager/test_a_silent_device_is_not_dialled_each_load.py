"""A device that did not answer is not dialled again by the next page loads for a minute."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from common.devices import device_client
from manager.devices import devices

_SILENT = {"state": device_client.UNREACHABLE, "reason": "timed out"}
_HEARD = {"state": device_client.ANSWERING}


class ADeviceThatDidNotAnswer(unittest.TestCase):
    def setUp(self) -> None:
        self.addCleanup(devices._silent.clear)
        self.now = 1000.0
        self.enterContext(patch.object(devices.time, "monotonic", lambda: self.now))

    def test_is_remembered_for_a_minute(self) -> None:
        devices.remember_answer("a", _SILENT)
        self.assertEqual(devices.recent_silence("a"), _SILENT)
        self.now += devices.SILENCE_HELD_S - 1
        self.assertEqual(devices.recent_silence("a"), _SILENT)
        self.now += 1
        self.assertIsNone(devices.recent_silence("a"))

    def test_is_dialled_again_once_it_has_answered(self) -> None:
        devices.remember_answer("a", _SILENT)
        devices.remember_answer("a", _HEARD)
        self.assertIsNone(devices.recent_silence("a"))

    def test_is_not_confused_with_another_device(self) -> None:
        devices.remember_answer("a", _SILENT)
        self.assertIsNone(devices.recent_silence("b"))
