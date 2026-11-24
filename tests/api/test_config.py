"""Settings: what this install has, what it is set to, and what it refuses.

The refusals are the part worth pinning. A settings write that half-applies leaves an
install in a state nobody asked for, and a theme source is a URL this install fetches
code from - so an unknown key fails the whole request and a theme source is not settable
over HTTP at all.

Split by what it tests. The rules are the service's and are tested against it; the route
is tested for the one thing only it does, which is turning a refusal into a status code.
"""

from __future__ import annotations

import unittest
import unittest.mock

from fastapi.testclient import TestClient

import httpapi
from common.config import config_schema
from common.settings import config_service


class Store:
    """Enough ConfigStore for the service: typed reads, staged writes, one save."""

    def __init__(self) -> None:
        self.written: dict[tuple[str, str], object] = {}
        self.saves = 0

    def value(self, section, key):
        entry = config_schema.option(section, key)
        return self.written.get((section, key), entry.default if entry else "")

    def set_value(self, section, key, value):
        self.written[(section, key)] = value

    def get_list(self, key):
        return []

    def theme_options(self):
        return {}

    def save(self):
        self.saves += 1


class ConfigServiceTests(unittest.TestCase):
    def setUp(self):
        self.store = Store()
        self._real = config_service.get_ini_config
        config_service.get_ini_config = lambda: self.store

    def tearDown(self):
        config_service.get_ini_config = self._real

    # --- schema ---------------------------------------------------------------

    def test_schema_describes_every_settable_option(self):
        schema = config_service.schema()
        served = sum(len(s["options"]) for s in schema["sections"])
        self.assertEqual(schema["count"], served)
        self.assertEqual(served, len(config_schema.settable()))

    def test_schema_leaves_out_internal_options(self):
        """Runtime state that happens to live in the file is not a setting. Offering a
        last-played pointer invites someone to set it."""
        served = {(o["section"], o["key"]) for s in config_service.schema()["sections"]
                  for o in s["options"]}
        internal = {(o.section, o.key) for o in config_schema.options() if o.internal}
        self.assertTrue(internal, "expected some internal options to exist")
        self.assertFalse(served & internal)

    def test_theme_sources_are_writable(self):
        sections = {s["name"]: s for s in config_service.schema()["sections"]}
        self.assertIn("themes", sections)
        self.assertTrue(sections["themes"]["writable"])
        self.assertTrue(all(o["writable"] for o in sections["themes"]["options"]))

    # --- writes ---------------------------------------------------------------

    def test_a_known_setting_is_written_and_saved(self):
        config_service.set_values({"logger": {"terminal": False}})
        self.assertEqual(self.store.written[("logger", "terminal")], False)
        self.assertEqual(self.store.saves, 1)

    def test_a_retired_spelling_writes_to_the_current_key(self):
        """`console` is what 2.x called terminal logging. A client written
        against the old name must not write a second, dead key beside the live one."""
        config_service.set_values({"logger": {"console": False}})
        self.assertEqual(self.store.written[("logger", "terminal")], False)
        self.assertNotIn(("logger", "console"), self.store.written)

    def test_an_unknown_key_fails_the_whole_request(self):
        """Not just the bad key - the good one must not land either, or a save is
        half-applied and no screen reflects the result."""
        with self.assertRaises(config_service.UnknownSettingsError) as caught:
            config_service.set_values({"logger": {"terminal": False},
                                       "general": {"not_a_setting": "x"}})
        self.assertEqual(caught.exception.keys, ["general.not_a_setting"])
        self.assertEqual(self.store.written, {})
        self.assertEqual(self.store.saves, 0)

    def test_setup_state_is_refused_like_any_internal_key(self):
        """Only Setup finishing clears `device.setup_pending`; a settings write that
        could would end it, or restart it, from outside."""
        for key in ("setup_pending",):
            with self.subTest(key=key):
                with self.assertRaises(config_service.UnknownSettingsError) as caught:
                    config_service.set_values({"device": {key: "false"}})
                self.assertEqual(caught.exception.keys, [f"device.{key}"])
        self.assertEqual(self.store.written, {})
        self.assertEqual(self.store.saves, 0)

    def test_setup_state_is_not_among_the_values_served(self):
        served = config_service.values().get("device", {})

        self.assertNotIn("setup_pending", served)

    def test_a_theme_source_is_written(self):
        config_service.set_values({"themes": {"registries": "http://elsewhere"}})
        self.assertIn(("themes", "registries"), self.store.written)

    def test_nothing_is_read_only_and_the_refusal_still_works(self):
        """The set is empty, not gone: the path that refuses a write is what a later
        origin check reuses, so it is exercised rather than left to rot."""
        self.assertEqual(config_service.READ_ONLY_SECTIONS, frozenset())
        with unittest.mock.patch.object(config_service, "READ_ONLY_SECTIONS",
                                        frozenset({"themes"})):
            with self.assertRaises(config_service.ReadOnlySettingsError) as caught:
                config_service.set_values({"themes": {"registries": "http://elsewhere"}})
        self.assertEqual(caught.exception.keys, ["themes.registries"])
        self.assertEqual(self.store.written, {})

    def test_an_empty_patch_writes_nothing(self):
        config_service.set_values({})
        self.assertEqual(self.store.saves, 0)

    def test_a_former_spelling_lands_on_the_canonical_key(self):
        """Keys moved to snake_case and the old ones stay aliases, so a client written
        against an older name keeps working and the file still gets one spelling."""
        aliased = next((o for o in config_schema.settable()
                        if o.aliases
                        and o.section not in config_service.READ_ONLY_SECTIONS),
                       None)
        if aliased is None:
            self.skipTest("no aliased option in the schema")
        config_service.set_values({aliased.section: {aliased.aliases[0]: "1"}})
        self.assertIn((aliased.section, aliased.key), self.store.written)


class ConfigRouteTests(unittest.TestCase):
    """What the route adds: a refusal becomes a status code and a message."""

    def setUp(self):
        self.store = Store()
        self._real = config_service.get_ini_config
        config_service.get_ini_config = lambda: self.store
        self.client = TestClient(httpapi.create_api_app(),
                                 raise_server_exceptions=False)

    def tearDown(self):
        config_service.get_ini_config = self._real

    def test_the_schema_is_served(self):
        body = self.client.get("/config/schema").json()
        self.assertEqual(body["count"], len(config_schema.settable()))

    def test_every_field_the_service_describes_reaches_the_wire(self):
        described = {field for section in config_service.schema()["sections"]
                     for option in section["options"] for field in option}
        served = {field for section in self.client.get("/config/schema").json()["sections"]
                  for option in section["options"] for field in option}
        self.assertEqual(described - served, set())

    def test_an_unknown_key_is_a_bad_request_naming_it(self):
        response = self.client.put("/config",
                                   json={"settings": {"general": {"not_a_setting": "x"}}})
        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn("not_a_setting", response.text)

    def test_a_number_outside_its_declared_range_is_refused_and_changes_nothing(self):
        for value in (0, 9, 3601):
            with self.subTest(value=value):
                response = self.client.put(
                    "/config", json={"settings": {"behavior": {"attract_idle": value}}})
                self.assertEqual(response.status_code, 400, response.text)
                self.assertIn("behavior.attract_idle", response.text)
        self.assertEqual(self.store.written, {})

    def test_a_number_inside_its_declared_range_is_written(self):
        response = self.client.put(
            "/config", json={"settings": {"behavior": {"attract_idle": 10}}})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.store.written[("behavior", "attract_idle")], 10)

    def test_a_write_to_the_setup_mark_is_a_bad_request_and_changes_nothing(self):
        response = self.client.put(
            "/config", json={"settings": {"device": {"setup_pending": True, "name": "x"}}})

        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn("device.setup_pending", response.text)
        self.assertEqual(self.store.written, {})

    def test_a_read_says_whether_the_dof_key_is_set_and_never_what_it_is(self):
        self.store.written[("dof", "config_tool_api_key")] = "abc-secret-key"

        for path in ("/config", "/config/settings", "/config/settings/dof"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertNotIn("abc-secret-key", response.text)
        self.assertIs(self.client.get("/config/settings/dof").json()["config_tool_api_key"], True)
        self.store.written[("dof", "config_tool_api_key")] = ""
        self.assertIs(self.client.get("/config/settings/dof").json()["config_tool_api_key"], False)

    def test_a_write_answers_without_the_key_it_stored(self):
        response = self.client.put(
            "/config", json={"settings": {"dof": {"config_tool_api_key": "abc-secret-key"}}})

        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn("abc-secret-key", response.text)
        self.assertEqual(self.store.written[("dof", "config_tool_api_key")], "abc-secret-key")

    def test_the_answer_a_read_gives_cannot_be_written_back_as_the_key(self):
        response = self.client.put(
            "/config", json={"settings": {"dof": {"config_tool_api_key": True}}})

        self.assertEqual(response.status_code, 400, response.text)
        self.assertNotIn(("dof", "config_tool_api_key"), self.store.written)

    def test_the_boolean_is_refused_under_any_spelling_of_the_key(self):
        for body in ({"dof": {"dofconfigtoolapikey": True}},
                     {"DOF": {"Config_Tool_Api_Key": True}}):
            with self.subTest(body=body):
                response = self.client.put("/config", json={"settings": body})

                self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(self.store.written, {})

    def test_a_theme_source_is_accepted(self):
        response = self.client.put("/config",
                                   json={"settings": {"themes": {"registries": "http://elsewhere"}}})
        self.assertEqual(response.status_code, 200, response.text)


if __name__ == "__main__":
    unittest.main()
