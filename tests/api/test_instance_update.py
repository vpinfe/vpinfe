"""Taking the published build, over HTTP.

The half that only reported is covered by the contract tests; these are about the half
that acts, where the order of operations is the design: nothing is stopped until there
is a verified package to stop it for.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

import requests
from starlette.testclient import TestClient

import httpapi
from common.host import launch_state
from common.i18n import t
from common.online import app_updater
from common.runtime import lifecycle

SUPPORTED = {"supported": True, "reason": None, "triplet": "linux-x64",
             "current_version": "v3.0.0", "install_root": "/opt/vpinfe",
             "launch_target": "/opt/vpinfe/vpinfe", "platform": "Linux"}
SOURCE_BUILD = {**SUPPORTED, "supported": False, "reason": "source_build"}
PREPARED = {"latest_version": "v3.1.0", "zip_path": "/tmp/x.zip"}


class PerformUpdateTests(unittest.TestCase):
    def setUp(self) -> None:
        launch_state.clear()
        lifecycle.reset_for_tests()
        self.addCleanup(launch_state.clear)
        self.addCleanup(lifecycle.reset_for_tests)
        self.performed: list[tuple[str, str]] = []
        for scope in (lifecycle.TABLE, lifecycle.VPINFE):
            lifecycle.register_performer(
                scope, lifecycle.STOP,
                lambda request: self.performed.append(request.pair))
        self.launched: list[dict] = []
        self.forced: list[bool] = []
        self.client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)

    def _updater(self, context=None, prepare=None):
        """Patch the four functions the route imports, at their home module."""
        module = "common.online.app_updater"
        return (
            patch(f"{module}.get_install_context", return_value=context or SUPPORTED),
            patch(f"{module}.prepare_update",
                  side_effect=prepare or (lambda: dict(PREPARED))),
            patch(f"{module}.launch_prepared_update", self.launched.append),
            patch(f"{module}.force_exit_after_handoff",
                  lambda *a, **k: self.forced.append(True)),
        )

    def _post(self, body=None, **kwargs):
        patches = self._updater(**kwargs)
        for one in patches:
            one.start()
            self.addCleanup(one.stop)
        return self.client.post("/update", json=body)

    def test_an_install_that_cannot_replace_itself_is_told_why(self) -> None:
        response = self._post(context=SOURCE_BUILD)

        self.assertEqual(response.status_code, 501)
        error = response.json()["error"]
        self.assertEqual(error["details"], {"support_reason": "source_build"})

    def test_a_source_build_never_downloads_anything(self) -> None:
        self._post(context=SOURCE_BUILD)

        self.assertEqual(self.launched, [])

    def test_a_running_table_stops_an_unasked_update(self) -> None:
        launch_state.set_launching("Medieval Madness", source=launch_state.SOURCE_API)

        response = self._post()

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["details"],
                         {"game_name": "Medieval Madness"})

    def test_a_refused_update_takes_nothing_down(self) -> None:
        """The whole point of asking first: a table is still running afterwards."""
        launch_state.set_launching("Medieval Madness", source=launch_state.SOURCE_API)

        self._post()

        self.assertEqual(self.performed, [])
        self.assertEqual(self.launched, [])

    def test_an_update_with_nothing_running_goes_ahead(self) -> None:
        response = self._post()

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json(),
                         {"latest_version": "v3.1.0", "stopped_table": None})
        self.assertEqual(self.launched, [dict(PREPARED)])

    def test_the_table_is_closed_when_the_caller_says_it_may_be(self) -> None:
        launch_state.set_launching("Medieval Madness", source=launch_state.SOURCE_API)

        response = self._post({"stop_table": True})

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["stopped_table"], "Medieval Madness")
        self.assertIn((lifecycle.TABLE, lifecycle.STOP), self.performed)

    def test_nothing_is_stopped_before_the_package_is_verified(self) -> None:
        """A download that fails must not have cost somebody their game first."""
        launch_state.set_launching("Medieval Madness", source=launch_state.SOURCE_API)

        def explode():
            raise RuntimeError("checksum failed")

        self._post({"stop_table": True}, prepare=explode)

        self.assertEqual(self.performed, [])

    def test_a_build_that_cannot_be_taken_says_why(self) -> None:
        def damaged():
            raise app_updater.UpdateError(t("error.instance.download_damaged"))

        response = self._post(prepare=damaged)

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["message"],
                         t("error.instance.download_damaged"))

    def test_a_release_nothing_answers_for_is_said_and_takes_nothing_down(self) -> None:
        launch_state.set_launching("Medieval Madness", source=launch_state.SOURCE_API)

        def away():
            raise requests.ConnectTimeout("HTTPSConnectionPool(host='api.github.com')")

        with self.assertLogs("vpinfe.common.online.app_updater", "WARNING"):
            response = self._post({"stop_table": True}, prepare=away)

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["message"],
                         t("error.instance.latest_unreachable"))
        self.assertEqual(self.performed, [])

    def test_this_install_goes_down_so_the_updater_can_run(self) -> None:
        self._post()

        self.assertIn((lifecycle.VPINFE, lifecycle.STOP), self.performed)
        self.assertEqual(self.forced, [True])


class UpdateCheckTests(unittest.TestCase):
    ANSWER = {"update_available": True, "current_version": "v3.0.0",
              "latest_version": "v3.1.0", "update_supported": False,
              "support_reason": "source_build", "triplet": None, "asset_name": None,
              "error": "remote_check_failed", "checked_at": "2026-09-25T08:00:00Z"}

    def setUp(self) -> None:
        self.asked: list[bool] = []

        def check_now(refresh: bool = False) -> dict:
            self.asked.append(refresh)
            return dict(self.ANSWER)

        patcher = patch("common.online.app_updater.check_now", check_now)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)

    def test_the_kept_answer_is_served_with_when_it_was_checked(self) -> None:
        response = self.client.get("/update")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.asked, [False])
        self.assertEqual(response.json()["checked_at"], "2026-09-25T08:00:00Z")
        self.assertEqual(response.json()["error"], "remote_check_failed")

    def test_refresh_asks_now(self) -> None:
        self.client.get("/update", params={"refresh": "true"})

        self.assertEqual(self.asked, [True])


if __name__ == "__main__":
    unittest.main()

