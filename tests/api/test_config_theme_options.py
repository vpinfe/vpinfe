"""What was chosen on each theme, over /config.

The map is the one object in `vpinfe.json` beside the lists. A theme declares its own
options, so a write is checked against the theme rather than against the schema, and a
theme that is not installed keeps what it is given.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

import httpapi
from common import events
from common.config.config_store import ConfigStore
from common.online import theme_service
from common.settings import config_service

SCHEMA = {"title": "Reference", "options": [
    {"key": "showFlags", "name": "Show flags", "type": "boolean", "default": False},
    {"key": "wheelSpan", "name": "Wheel span", "type": "number", "default": 3},
]}


class ThemeOptionsRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.file = self.root / "vpinfe.json"
        themes = self.root / "themes"
        (themes / "Reference").mkdir(parents=True)
        (themes / "Reference" / "theme.json").write_text(json.dumps(SCHEMA), encoding="utf-8")
        for target, name, value in (
                (config_service, "get_ini_config", lambda: ConfigStore(self.file)),
                (theme_service, "THEMES_DIR", themes)):
            patcher = mock.patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)
        self.heard: list[dict] = []
        self.listener = lambda **payload: self.heard.append(payload)
        events.subscribe(events.SETTINGS_CHANGED, self.listener)
        self.addCleanup(events.unsubscribe, events.SETTINGS_CHANGED, self.listener)

    def saved(self) -> dict:
        return json.loads(self.file.read_text(encoding="utf-8"))["theme_options"]

    def put(self, entries: object):
        return self.client.put("/config", json={"theme_options": entries})


class ReadingTests(ThemeOptionsRouteTests):
    def test_the_whole_carries_the_map_and_starts_empty(self) -> None:
        self.assertEqual(self.client.get("/config").json()["theme_options"], {})

    def test_the_map_and_one_theme_are_served(self) -> None:
        self.put({"Reference": {"showFlags": True}})

        self.assertEqual(self.client.get("/config/theme_options").json(),
                         {"Reference": {"showFlags": True}})
        self.assertEqual(self.client.get("/config/theme_options/Reference").json(),
                         {"showFlags": True})

    def test_a_theme_nobody_changed_is_not_found(self) -> None:
        self.assertEqual(self.client.get("/config/theme_options/Reference").status_code, 404)


class WritingTests(ThemeOptionsRouteTests):
    def test_an_installed_theme_is_checked_against_what_it_declares(self) -> None:
        answer = self.put({"Reference": {"showFlags": "yes", "wheelSpan": "5", "extra": 1}})

        self.assertEqual(answer.status_code, 200, answer.text)
        self.assertEqual(self.saved(), {"Reference": {"showFlags": True, "wheelSpan": 5}})
        self.assertEqual(answer.json()["theme_options"], self.saved())

    def test_a_value_the_theme_cannot_take_is_refused_and_nothing_is_written(self) -> None:
        answer = self.put({"Reference": {"wheelSpan": "lots"}})

        self.assertEqual(answer.status_code, 400, answer.text)
        self.assertEqual(self.client.get("/config/theme_options").json(), {})

    def test_a_theme_that_is_not_installed_keeps_its_values_as_given(self) -> None:
        self.put({"Elsewhere": {"anything": [1, {"a": 2}]}})

        self.assertEqual(self.saved(), {"Elsewhere": {"anything": [1, {"a": 2}]}})

    def test_a_theme_named_takes_the_values_given_and_the_others_stay(self) -> None:
        self.put({"Reference": {"showFlags": True}, "Elsewhere": {"x": 1}})
        self.put({"Reference": {"wheelSpan": 9}})

        self.assertEqual(self.saved(), {"Reference": {"wheelSpan": 9}, "Elsewhere": {"x": 1}})

    def test_an_empty_entry_takes_the_choices_away(self) -> None:
        self.put({"Reference": {"showFlags": True}})
        self.put({"Reference": {}})

        self.assertEqual(self.saved(), {"Reference": {}})

    def test_a_theme_folder_is_trimmed_and_a_blank_one_refused(self) -> None:
        self.put({" Reference ": {"showFlags": True}})

        self.assertEqual(self.saved(), {"Reference": {"showFlags": True}})
        self.assertEqual(self.put({"  ": {"showFlags": True}}).status_code, 400)

    def test_a_map_that_is_not_an_object_of_objects_is_refused(self) -> None:
        for body in (["x"], {"Reference": 3}, "x"):
            with self.subTest(body=body):
                self.assertEqual(self.put(body).status_code, 400)

    def test_a_change_is_announced_and_a_repeat_is_not(self) -> None:
        self.put({"Reference": {"showFlags": True}})
        self.put({"Reference": {"showFlags": True}})

        self.assertEqual([one["keys"] for one in self.heard], [["theme_options"]])

    def test_a_theme_saved_through_its_own_service_is_read_over_config(self) -> None:
        theme_service.save_theme_option_values("Reference", {"wheelSpan": 6})

        self.assertEqual(self.client.get("/config/theme_options/Reference").json(),
                         {"wheelSpan": 6})


if __name__ == "__main__":
    unittest.main()
