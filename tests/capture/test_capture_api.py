"""The report and the capability over HTTP."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from starlette.testclient import TestClient

import httpapi
from httpapi import capabilities
from tests.capture.test_preflight import found, report


class CaptureApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)

    def test_the_report_is_served_as_computed(self) -> None:
        said = report(found=found(missing=("wf_recorder",)))
        with patch("common.capture.preflight.report", return_value=said):
            response = self.client.get("/capture")

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["adapter"], "wlr")
        playfield = body["screens"][0]
        self.assertEqual(playfield["video"]["reason"]["remedy"]["setting"],
                         "tools.wf_recorder_path")
        self.assertEqual({row["id"]: row["state"] for row in body["tools"]}["wf_recorder"],
                         "missing")

    def test_discovery_says_whether_this_device_records_and_why_not(self) -> None:
        with patch("common.capture.preflight.available",
                   return_value=(False, "Needs grim")), \
                patch("httpapi.capabilities._enabled_features",
                      return_value=frozenset(capabilities.FEATURES)):
            declared = {one["name"]: one for one in capabilities.declared()}

        self.assertEqual(declared["capture"]["feature"], "frontend")
        self.assertEqual((declared["capture"]["available"], declared["capture"]["reason"]),
                         (False, "Needs grim"))


if __name__ == "__main__":
    unittest.main()
