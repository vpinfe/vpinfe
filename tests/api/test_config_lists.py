"""The /config routes over a list: each verb, and what a refused write leaves behind.

The tests declare their own lists, so they hold whatever the schema comes to declare.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from fastapi.testclient import TestClient

import httpapi
from common import events
from common.apps.contract import Field
from common.config import config_schema
from common.config.config_schema import ConfigList
from common.config.config_store import ConfigStore
from common.settings import config_service
from tests.config.test_config_lists import WIDGET_FIELDS, declared

WIDGETS = ConfigList("widgets", WIDGET_FIELDS, home="widgets")
FOLDERS = ConfigList("folders", (Field("name"), Field("where", path="dir")), home="folders")


class ConfigListRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.ini = self.root / "vpinfe.ini"
        self.store = ConfigStore(str(self.ini))
        real = config_service.get_ini_config
        config_service.get_ini_config = lambda: self.store
        self.addCleanup(setattr, config_service, "get_ini_config", real)
        context = declared(WIDGETS, FOLDERS)
        context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        self.client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)
        self.heard: list[dict[str, Any]] = []
        self.listener = lambda **payload: self.heard.append(payload)
        events.subscribe(events.SETTINGS_CHANGED, self.listener)
        self.addCleanup(events.unsubscribe, events.SETTINGS_CHANGED, self.listener)

    def _add(self, name: str) -> dict[str, Any]:
        answer = self.client.post("/config/widgets", json={"name": name})
        self.assertEqual(answer.status_code, 201, answer.text)
        return answer.json()

    def _names(self) -> list[str]:
        return [item["name"] for item in self.client.get("/config/widgets").json()]


class ReadingTests(ConfigListRouteTests):
    def test_the_whole_is_the_settings_and_each_declared_list(self) -> None:
        self._add("One")

        body = self.client.get("/config").json()

        self.assertEqual(set(body), {"settings", "widgets", "folders", "theme_options"})
        self.assertIn("network", body["settings"])
        self.assertEqual([item["name"] for item in body["widgets"]], ["One"])

    def test_the_settings_are_served_whole_and_by_section(self) -> None:
        whole = self.client.get("/config/settings").json()
        section = self.client.get("/config/settings/network").json()

        self.assertEqual(section, whole["network"])

    def test_a_section_that_does_not_exist_is_not_found(self) -> None:
        self.assertEqual(self.client.get("/config/settings/nothing").status_code, 404)

    def test_a_list_and_an_item_are_served(self) -> None:
        made = self._add("One")

        self.assertEqual(self.client.get(f"/config/widgets/{made['id']}").json(), made)
        self.assertEqual(self.client.get("/config/widgets").json(), [made])

    def test_a_list_that_is_not_declared_is_not_found(self) -> None:
        for method, path in (("get", "/config/gadgets"), ("put", "/config/gadgets"),
                             ("post", "/config/gadgets"), ("get", "/config/gadgets/x"),
                             ("put", "/config/gadgets/x"), ("delete", "/config/gadgets/x")):
            with self.subTest(method=method, path=path):
                answer = self.client.request(method, path, json={} if method != "put" or
                                             path.count("/") > 2 else [])
                self.assertEqual(answer.status_code, 404, answer.text)

    def test_an_item_that_is_not_there_is_not_found(self) -> None:
        self.assertEqual(self.client.get("/config/widgets/nope").status_code, 404)
        self.assertEqual(self.client.delete("/config/widgets/nope").status_code, 404)


class WritingAListTests(ConfigListRouteTests):
    def test_post_adds_one_with_a_minted_id_and_the_declared_defaults(self) -> None:
        made = self._add("One")

        self.assertTrue(made["id"])
        self.assertEqual((made["size"], made["shape"], made["enabled"]), (3, "round", True))
        self.assertEqual(self._names(), ["One"])

    def test_post_refuses_an_id_the_caller_chose(self) -> None:
        answer = self.client.post("/config/widgets", json={"id": "mine", "name": "One"})

        self.assertEqual(answer.status_code, 400, answer.text)
        self.assertEqual(self._names(), [])

    def test_put_on_the_list_replaces_it_in_the_order_sent(self) -> None:
        first, second = self._add("One"), self._add("Two")

        answer = self.client.put("/config/widgets", json=[second, first, {"name": "Three"}])

        self.assertEqual(answer.status_code, 200, answer.text)
        self.assertEqual([item["name"] for item in answer.json()], ["Two", "One", "Three"])
        self.assertTrue(answer.json()[2]["id"])
        self.assertEqual(self._names(), ["Two", "One", "Three"])

    def test_put_on_an_item_replaces_it_where_it_stands(self) -> None:
        first, second = self._add("One"), self._add("Two")

        answer = self.client.put(f"/config/widgets/{first['id']}", json={"name": "Uno"})

        self.assertEqual(answer.status_code, 200, answer.text)
        self.assertEqual(answer.json()["id"], first["id"])
        self.assertEqual(answer.json()["size"], 3)
        self.assertEqual(self._names(), ["Uno", "Two"])
        self.assertEqual(self.client.get(f"/config/widgets/{second['id']}").json(), second)

    def test_put_on_an_item_that_is_not_there_adds_it_under_that_id(self) -> None:
        self._add("One")

        answer = self.client.put("/config/widgets/from-elsewhere", json={"name": "Two"})

        self.assertEqual(answer.status_code, 200, answer.text)
        self.assertEqual(answer.json()["id"], "from-elsewhere")
        self.assertEqual(self._names(), ["One", "Two"])

    def test_put_on_an_item_refuses_a_different_id_in_the_body(self) -> None:
        answer = self.client.put("/config/widgets/a", json={"id": "b", "name": "Two"})

        self.assertEqual(answer.status_code, 400, answer.text)
        self.assertEqual(self._names(), [])

    def test_delete_removes_one(self) -> None:
        first, _ = self._add("One"), self._add("Two")

        answer = self.client.delete(f"/config/widgets/{first['id']}")

        self.assertEqual(answer.status_code, 204, answer.text)
        self.assertEqual(self._names(), ["Two"])

    def test_a_field_the_list_does_not_declare_is_refused_and_nothing_is_written(self) -> None:
        self._add("One")

        for answer in (self.client.post("/config/widgets", json={"name": "x", "color": "red"}),
                       self.client.put("/config/widgets", json=[{"name": "x", "color": "red"}]),
                       self.client.put("/config/widgets/a", json={"color": "red"})):
            self.assertEqual(answer.status_code, 400, answer.text)
            self.assertIn("color", answer.text)

        self.assertEqual(self._names(), ["One"])

    def test_a_value_of_the_wrong_type_is_refused(self) -> None:
        answer = self.client.post("/config/widgets", json={"name": "x", "size": 99})

        self.assertEqual(answer.status_code, 400, answer.text)
        self.assertIn("widgets.size", answer.text)

    def test_what_is_written_is_in_the_file(self) -> None:
        made = self._add("One")

        again = ConfigStore(str(self.ini)).get_list("widgets")

        self.assertEqual(again, [made])

    def test_a_write_names_the_list_in_settings_changed(self) -> None:
        self._add("One")

        self.assertEqual(self.heard[-1]["keys"], ["widgets"])

    def test_a_write_that_changes_nothing_says_nothing(self) -> None:
        made = self._add("One")
        heard = len(self.heard)

        self.client.put("/config/widgets", json=[made])

        self.assertEqual(len(self.heard), heard)


class RefusalTests(ConfigListRouteTests):
    def test_a_section_that_is_not_an_object_is_refused(self) -> None:
        for section in ("network", "capture"):
            with self.subTest(section=section):
                answer = self.client.put("/config", json={"settings": {section: 5}})

                self.assertEqual(answer.status_code, 400, answer.text)

    def test_an_id_with_a_slash_is_refused_since_no_address_could_name_it(self) -> None:
        answer = self.client.put("/config/widgets", json=[{"id": "a/b", "name": "x"}])

        self.assertEqual(answer.status_code, 400, answer.text)

        self.assertEqual(self._names(), [])

    def test_an_item_the_normalizer_drops_is_refused_not_a_server_error(self) -> None:
        self._add("One")
        with mock.patch.object(config_schema, "normalize", lambda key, items: items[:1]):
            added = self.client.post("/config/widgets", json={"name": "Two"})
            put = self.client.put("/config/widgets/other", json={"name": "Three"})

        self.assertEqual((added.status_code, put.status_code), (400, 400), added.text + put.text)

    def test_a_list_that_cannot_be_saved_is_not_left_staged(self) -> None:
        self._add("One")
        with mock.patch.object(ConfigStore, "save", side_effect=OSError("disk full")):
            answer = self.client.post("/config/widgets", json={"name": "Two"})

        self.assertEqual(answer.status_code, 409, answer.text)
        self.assertEqual(self._names(), ["One"])

    def test_an_entry_that_is_not_an_object_in_the_file_does_not_break_the_items(self) -> None:
        made = self._add("One")
        self.store.top_level["widgets"].append(5)

        self.assertEqual(self.client.get(f"/config/widgets/{made['id']}").json(), made)
        self.assertEqual(self.client.get("/config/widgets").json(), [made])
        self.assertEqual(self.client.delete(f"/config/widgets/{made['id']}").status_code, 204)


class PatchTests(ConfigListRouteTests):
    def test_one_patch_takes_settings_and_a_list(self) -> None:
        answer = self.client.put("/config", json={
            "settings": {"network": {"http_port": 8123}},
            "widgets": [{"name": "One"}, {"name": "Two"}]})

        self.assertEqual(answer.status_code, 200, answer.text)
        body = answer.json()
        self.assertEqual(body["settings"]["network"]["http_port"], 8123)
        self.assertEqual([item["name"] for item in body["widgets"]], ["One", "Two"])
        self.assertEqual(sorted(self.heard[-1]["keys"]), ["network.http_port", "widgets"])

    def test_a_list_left_out_is_left_alone(self) -> None:
        self._add("One")

        self.client.put("/config", json={"settings": {"network": {"http_port": 8123}}})

        self.assertEqual(self._names(), ["One"])

    def test_a_patch_naming_neither_settings_nor_a_list_is_refused(self) -> None:
        answer = self.client.put("/config", json={"network": {"http_port": 8123}})

        self.assertEqual(answer.status_code, 400, answer.text)
        self.assertIn("network", answer.text)

    def test_a_refused_list_writes_the_settings_beside_it_no_more_than_itself(self) -> None:
        before = self.client.get("/config").json()

        answer = self.client.put("/config", json={
            "settings": {"network": {"http_port": 8123}},
            "widgets": [{"name": "x", "color": "red"}]})

        self.assertEqual(answer.status_code, 400, answer.text)
        self.assertEqual(self.client.get("/config").json(), before)
        self.assertEqual(self.heard, [])

    def test_a_refused_setting_writes_the_list_beside_it_no_more_than_itself(self) -> None:
        answer = self.client.put("/config", json={
            "settings": {"general": {"not_a_setting": "x"}},
            "widgets": [{"name": "One"}]})

        self.assertEqual(answer.status_code, 400, answer.text)
        self.assertEqual(self._names(), [])

    def test_two_lists_are_written_or_neither(self) -> None:
        answer = self.client.put("/config", json={
            "widgets": [{"name": "One"}], "folders": [{"name": "x", "hue": "red"}]})

        self.assertEqual(answer.status_code, 400, answer.text)
        self.assertEqual(self._names(), [])

    def test_settings_must_be_an_object(self) -> None:
        self.assertEqual(self.client.put("/config", json={"settings": []}).status_code, 400)


class PathChecksTests(ConfigListRouteTests):
    def test_a_path_in_an_item_is_checked_beside_the_settings(self) -> None:
        there = self.root / "there"
        there.mkdir()
        self.client.put("/config/folders", json=[
            {"id": "a", "name": "ok", "where": str(there)},
            {"id": "b", "name": "gone", "where": str(self.root / "gone")},
            {"id": "c", "name": "blank"}])

        checks = self.client.get("/config/paths").json()["checks"]
        items = {check["id"]: check for check in checks if "id" in check}

        self.assertEqual({key: (one["list"], one["field"], one["state"])
                          for key, one in items.items()},
                         {"a": ("folders", "where", "ok"), "b": ("folders", "where", "missing"),
                          "c": ("folders", "where", "unset")})
        self.assertTrue(all("section" not in one for one in items.values()))
        self.assertTrue(any("section" in check and "id" not in check for check in checks))


class ReservedNameTests(unittest.TestCase):
    RESERVED = {"schema", "paths", "tools", "waiting", "settings", "theme_options"}

    def test_no_list_takes_a_name_a_route_has(self) -> None:
        """A settings section may: `tools` is one, and sections are served under
        `/config/settings/`, where no list is."""
        taken = {spec.key for spec in config_schema.lists()}

        self.assertEqual(taken & self.RESERVED, set())

    def test_every_static_route_under_config_is_reserved(self) -> None:
        from httpapi import config as routes

        named = {route.path.split("/")[2] for route in routes.router.routes  # type: ignore[attr-defined]
                 if route.path.count("/") >= 2}  # type: ignore[attr-defined]

        self.assertEqual(named - {"{list_key}"} - self.RESERVED, set())


if __name__ == "__main__":
    unittest.main()
