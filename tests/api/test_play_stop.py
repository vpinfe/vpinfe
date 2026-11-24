"""Closing the table this play host is running, over HTTP.

The route goes through the lifecycle scope rather than reaching for the process, so
what these pin is that a stop is announced like every other one and that the answer
tells a caller whether there was anything to stop.
"""

from __future__ import annotations

import unittest

import httpapi
from common.host import launch_state
from common.runtime import lifecycle

try:
    from starlette.testclient import TestClient
except ImportError:  # pragma: no cover
    TestClient = None  # type: ignore[assignment,misc]


@unittest.skipIf(TestClient is None, "starlette test client unavailable")
class PlayStopTests(unittest.TestCase):
    def setUp(self) -> None:
        launch_state.clear()
        lifecycle.reset_for_tests()
        self.addCleanup(launch_state.clear)
        self.addCleanup(lifecycle.reset_for_tests)
        self.performed: list = []
        lifecycle.register_performer(
            lifecycle.TABLE, lifecycle.STOP,
            lambda request: self.performed.append(request))
        self.client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)

    def test_stopping_when_nothing_runs_says_so(self) -> None:
        body = self.client.post("/play/stop").json()

        self.assertEqual(body, {"stopped": False, "game_name": None})

    def test_nothing_is_announced_when_there_is_no_table(self) -> None:
        """A lifecycle request with no table would tell every surface one was closing."""
        self.client.post("/play/stop")

        self.assertEqual(self.performed, [])

    def test_a_running_table_is_stopped_and_named(self) -> None:
        launch_state.set_launching("Medieval Madness", source=launch_state.SOURCE_API)

        body = self.client.post("/play/stop").json()

        self.assertEqual(body, {"stopped": True, "game_name": "Medieval Madness"})
        self.assertEqual(len(self.performed), 1)

    def test_the_stop_goes_through_the_lifecycle_scope(self) -> None:
        """So the other surfaces hear `lifecycle.acting` rather than a table vanishing."""
        launch_state.set_launching("Medieval Madness", source=launch_state.SOURCE_API)

        self.client.post("/play/stop")

        request = self.performed[0]
        self.assertEqual(request.pair, (lifecycle.TABLE, lifecycle.STOP))
        self.assertEqual(request.origin.surface, lifecycle.SURFACE_API)

    def test_an_api_origin_is_never_asked_to_confirm(self) -> None:
        """The request is over by the time anything could ask, so the caller asks."""
        self.assertFalse(lifecycle.Origin(lifecycle.SURFACE_API).is_answerable)

    def test_a_build_that_cannot_stop_a_table_says_so_rather_than_claiming_it_did(self):
        lifecycle.reset_for_tests()
        launch_state.set_launching("Medieval Madness", source=launch_state.SOURCE_API)

        with self.assertLogs("vpinfe.common.runtime.lifecycle", level="ERROR"):
            body = self.client.post("/play/stop").json()

        self.assertEqual(body, {"stopped": False, "game_name": None})


if __name__ == "__main__":
    unittest.main()
