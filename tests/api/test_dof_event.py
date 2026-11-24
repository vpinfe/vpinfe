"""Turning a DOF event on from outside: what is accepted, and each way it is refused."""

from __future__ import annotations

import unittest
from unittest import mock

import httpapi
from common.host import dof_service
from common.i18n import t

try:
    from starlette.testclient import TestClient
except ImportError:  # pragma: no cover
    TestClient = None  # type: ignore[assignment,misc]


@unittest.skipIf(TestClient is None, "starlette TestClient unavailable")
class DofEventRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)
        self.fired: list[str] = []

    def _answering(self, outcome: dof_service.Outcome):
        def fire(_ini, event):
            self.fired.append(event)
            return outcome
        return mock.patch.object(dof_service, "test_event", fire)

    def test_an_event_goes_on_and_comes_back_normalized(self) -> None:
        with self._answering(dof_service.OK):
            answer = self.client.post("/dof/event", json={"event": " e905 "})

        self.assertEqual(answer.status_code, 200)
        self.assertEqual(answer.json(), {"event": "E905", "no_boards": False})
        self.assertEqual(self.fired, ["E905"])

    def test_an_event_with_no_board_to_show_it_says_so(self) -> None:
        with self._answering(dof_service.OK), \
                mock.patch.object(dof_service, "no_boards", return_value=True):
            fired = self.client.post("/dof/event", json={"event": "E905"})
        with mock.patch.object(dof_service, "blocked", return_value=dof_service.OK), \
                mock.patch.object(dof_service, "current_event", return_value="E905"), \
                mock.patch.object(dof_service, "no_boards", return_value=True):
            state = self.client.get("/dof/event")

        with mock.patch.object(dof_service, "blocked", return_value=dof_service.OK), \
                mock.patch.object(dof_service, "current_event", return_value=None), \
                mock.patch.object(dof_service, "no_boards", return_value=True):
            idle = self.client.get("/dof/event")

        self.assertEqual(idle.json(), {"event": None, "blocked": "", "no_boards": False})
        self.assertEqual(fired.json(), {"event": "E905", "no_boards": True})
        self.assertEqual(state.json(), {"event": "E905", "blocked": "", "no_boards": True})

    def test_anything_but_a_letter_and_a_number_is_refused_unfired(self) -> None:
        for event in ("905", "", "E9x", "EE905"):
            with self.subTest(event=event), self._answering(dof_service.OK):
                answer = self.client.post("/dof/event", json={"event": event})

                self.assertEqual(answer.status_code, 400)
                self.assertEqual(answer.json()["error"]["message"], t("dof.event.malformed"))
        self.assertEqual(self.fired, [])

    def test_each_reason_is_refused_in_its_own_words(self) -> None:
        for reason, status in ((dof_service.PLAYING, 409), (dof_service.OFF, 501),
                               (dof_service.NO_RUNNER, 501), (dof_service.UNLOADABLE, 501),
                               (dof_service.NO_CONFIG, 409), (dof_service.NO_HELPER, 409),
                               (dof_service.FAILED, 409)):
            outcome = dof_service.Outcome(False, reason)
            with self.subTest(reason=reason), self._answering(outcome):
                answer = self.client.post("/dof/event", json={"event": "E905"})

                self.assertEqual(answer.status_code, status)
                self.assertEqual(answer.json()["error"]["message"], outcome.words())

    def test_the_state_says_which_event_is_on_and_why_none_could_be(self) -> None:
        for blocked, event, said in ((dof_service.OK, "E905", ""),
                                     (dof_service.Outcome(False, dof_service.OFF), None,
                                      t("dof.event.off")),
                                     (dof_service.Outcome(False, dof_service.PLAYING), None,
                                      t("dof.event.playing"))):
            with self.subTest(said=said), \
                    mock.patch.object(dof_service, "blocked", return_value=blocked), \
                    mock.patch.object(dof_service, "current_event", return_value=event):
                answer = self.client.get("/dof/event")

                self.assertEqual(answer.status_code, 200)
                self.assertEqual(answer.json(),
                                 {"event": event, "blocked": said, "no_boards": False})

    def test_stopping_clears_the_event_and_is_fine_with_none_on(self) -> None:
        with mock.patch.object(dof_service, "clear_active_dof_event",
                               return_value=False) as clear:
            answer = self.client.delete("/dof/event")

        self.assertEqual(answer.status_code, 200)
        self.assertEqual(answer.json(), {"event": None, "no_boards": False})
        clear.assert_called_once()


if __name__ == "__main__":
    unittest.main()
