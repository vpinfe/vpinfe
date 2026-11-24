"""A list of objects in `vpinfe.json`, declared in the schema.

No list is declared yet, so each test declares its own and the store sees it for the
length of the test.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from unittest import mock

from common.apps.contract import Field
from common.config import config_schema
from common.config.config_schema import ConfigList, ConfigListError, ListVariant
from common.config.config_store import ConfigStore
from common.settings import config_service
from tests.config.test_config_store import ConfigStoreTests

WIDGET_FIELDS = (
    Field("name"),
    Field("enabled", type="bool", default="true"),
    Field("size", type="int", default="3", minimum=1, maximum=9),
    Field("shape", type="choice", default="round", choices=(("round", "Round"),
                                                             ("square", "Square"))),
    Field("tags", type="list"),
    Field("owns", type="map"),
)


def _shipped() -> list[dict[str, Any]]:
    return [{"name": "Shipped"}]


@contextmanager
def declared(*lists: ConfigList, normalizers: dict | None = None,
             seeds: dict | None = None) -> Iterator[None]:
    with mock.patch.object(config_schema, "CONFIG_LISTS", lists), \
            mock.patch.dict(config_schema._NORMALIZERS, normalizers or {}, clear=True), \
            mock.patch.dict(config_schema._SEEDS, seeds or {}, clear=True):
        yield


WIDGETS = ConfigList("widgets", WIDGET_FIELDS, home="widgets", default=_shipped)


class ListStoreTests(ConfigStoreTests):
    def _store(self) -> ConfigStore:
        return ConfigStore(str(self.ini))


class DefaultTests(ListStoreTests):
    def test_a_fresh_install_holds_the_default_with_a_fresh_id(self) -> None:
        with declared(WIDGETS):
            self._store()

        [item] = self._payload()["widgets"]
        self.assertEqual(item["name"], "Shipped")
        self.assertTrue(item["id"])
        self.assertEqual(item["size"], 3)

    def test_a_file_without_the_key_gets_it_at_once(self) -> None:
        self._store().save()
        self.assertNotIn("widgets", self._payload())

        with declared(WIDGETS):
            store = self._store()
            names = [i["name"] for i in store.get_list("widgets")]

        self.assertEqual([i["name"] for i in self._payload()["widgets"]], ["Shipped"])
        self.assertEqual(names, ["Shipped"])

    def test_an_empty_list_is_what_the_person_has(self) -> None:
        with declared(WIDGETS):
            store = self._store()
            store.set_list("widgets", [])
            store.save()

            held = self._store().get_list("widgets")

        self.assertEqual(held, [])
        self.assertEqual(self._payload()["widgets"], [])

    def test_the_default_is_not_shared_between_reads(self) -> None:
        with declared(WIDGETS):
            store = self._store()
            store.get_list("widgets")[0]["name"] = "Changed"

            self.assertEqual(store.get_list("widgets")[0]["name"], "Shipped")

    def test_a_key_that_is_not_a_list_reads_as_the_default_and_is_left_alone(self) -> None:
        self._store().save()
        payload = self._payload()
        payload["widgets"] = "by hand"
        self.json.write_text(json.dumps(payload), encoding="utf-8")

        with declared(WIDGETS):
            store = self._store()
            names = [i["name"] for i in store.get_list("widgets")]
            store.set_value("general", "game_root_dir", "/library")
            store.save()

        self.assertEqual(names, ["Shipped"])
        self.assertEqual(self._payload()["widgets"], "by hand")

    def test_a_list_absent_after_a_reread_keeps_the_ids_it_reads_with(self) -> None:
        with declared(WIDGETS):
            store = self._store()
            payload = self._payload()
            del payload["widgets"]
            self.json.write_text(json.dumps(payload), encoding="utf-8")
            store.reread()

            self.assertEqual(store.get_list("widgets"), store.get_list("widgets"))

    def test_a_key_that_is_not_declared_is_not_a_list(self) -> None:
        store = self._store()

        with declared(WIDGETS), self.assertRaises(ConfigListError):
            store.get_list("gadgets")


class WriteTests(ListStoreTests):
    def test_a_list_round_trips_with_defaults_filled_and_ids_minted(self) -> None:
        with declared(WIDGETS):
            store = self._store()
            store.set_list("widgets", [{"name": "A", "size": "5", "tags": "x, y"},
                                       {"id": "keep", "name": "B", "enabled": False,
                                        "owns": {"t": "1"}}])
            store.save()

            [a, b] = self._store().get_list("widgets")

        self.assertTrue(a["id"])
        self.assertEqual((a["name"], a["size"], a["enabled"], a["shape"], a["tags"], a["owns"]),
                         ("A", 5, True, "round", ["x", "y"], {}))
        self.assertEqual((b["id"], b["enabled"], b["owns"]), ("keep", False, {"t": "1"}))

    def test_the_file_holds_real_types(self) -> None:
        with declared(WIDGETS):
            store = self._store()
            store.set_list("widgets", [{"name": "A", "size": "5"}])
            store.save()

        [item] = self._payload()["widgets"]
        self.assertEqual((item["size"], item["enabled"], item["tags"]), (5, True, []))

    def test_an_unknown_field_is_refused_and_nothing_is_staged(self) -> None:
        with declared(WIDGETS):
            store = self._store()
            before = store.get_list("widgets")

            with self.assertRaisesRegex(ConfigListError, "has no field color"):
                store.set_list("widgets", [{"name": "A", "color": "red"}])

            self.assertEqual(store.get_list("widgets"), before)

    def test_a_value_the_field_cannot_hold_is_refused(self) -> None:
        wrong: list[dict[str, Any]] = [{"size": 0}, {"size": 10}, {"size": "many"}, {"size": True},
                 {"shape": "oval"}, {"enabled": "maybe"}, {"tags": [1]}, {"owns": {"a": 1}},
                 {"name": 5}]
        with declared(WIDGETS):
            store = self._store()
            for item in wrong:
                with self.subTest(item=item), self.assertRaises(ConfigListError):
                    store.set_list("widgets", [item])

    def test_a_number_beyond_reach_is_refused_not_raised(self) -> None:
        with declared(WIDGETS):
            store = self._store()
            for size in (float("inf"), "inf", 1e999, "1e999"):
                with self.subTest(size=size), self.assertRaises(ConfigListError):
                    store.set_list("widgets", [{"size": size}])

    def test_a_field_the_file_already_holds_passes_through_a_write(self) -> None:
        self._store().save()
        payload = self._payload()
        payload["widgets"] = [{"id": "a", "name": "A", "from_a_newer_build": {"x": 1}}]
        self.json.write_text(json.dumps(payload), encoding="utf-8")

        with declared(WIDGETS):
            store = self._store()
            store.set_list("widgets", [*store.get_list("widgets"), {"id": "b", "name": "B"}])
            store.save()

            with self.assertRaisesRegex(ConfigListError, "has no field from_a_newer_build"):
                store.set_list("widgets", [{"id": "c", "from_a_newer_build": 1}])

        first, second = self._payload()["widgets"]
        self.assertEqual(first["from_a_newer_build"], {"x": 1})
        self.assertEqual(second["id"], "b")

    def test_two_items_cannot_share_an_id(self) -> None:
        with declared(WIDGETS):
            store = self._store()

            with self.assertRaisesRegex(ConfigListError, "share an id"):
                store.set_list("widgets", [{"id": "x"}, {"id": "x"}])

    def test_an_item_that_is_not_an_object_is_refused(self) -> None:
        with declared(WIDGETS):
            with self.assertRaises(ConfigListError):
                self._store().set_list("widgets", ["a"])  # type: ignore[list-item]
            with self.assertRaises(ConfigListError):
                self._store().set_list("widgets", {"id": "a"})  # type: ignore[arg-type]

    def test_two_stores_writing_different_items_both_survive(self) -> None:
        with declared(WIDGETS):
            first, second = self._store(), self._store()
            first.set_list("widgets", [*first.get_list("widgets"), {"id": "b", "name": "B"}])
            first.save()
            second.set_list("widgets", [*second.get_list("widgets"), {"id": "c", "name": "C"}])
            second.save()

        self.assertEqual([i["name"] for i in self._payload()["widgets"]],
                         ["Shipped", "B", "C"])


class NormalizeTests(ListStoreTests):
    @staticmethod
    def _one_per_name(items: list[dict]) -> list[dict]:
        return list({item["name"]: item for item in items}.values())

    def test_a_write_keeps_what_the_normalizer_answers(self) -> None:
        with declared(WIDGETS, normalizers={"widgets": self._one_per_name}):
            store = self._store()
            store.set_list("widgets", [{"id": "a", "name": "Same"}, {"id": "b", "name": "Same"}])

            self.assertEqual([i["id"] for i in store.get_list("widgets")], ["b"])

    def test_what_two_stores_each_added_goes_through_it_when_they_meet(self) -> None:
        with declared(WIDGETS, normalizers={"widgets": self._one_per_name}):
            first, second = self._store(), self._store()
            first.set_list("widgets", [*first.get_list("widgets"), {"id": "b", "name": "Same"}])
            first.save()
            second.set_list("widgets", [*second.get_list("widgets"), {"id": "c", "name": "Same"}])
            second.save()

        self.assertEqual([i["name"] for i in self._payload()["widgets"]], ["Shipped", "Same"])

    def test_the_default_goes_through_it_too(self) -> None:
        def rename(items: list[dict]) -> list[dict]:
            return [{**item, "name": item["name"].upper()} for item in items]

        with declared(WIDGETS, normalizers={"widgets": rename}):
            self._store()

        self.assertEqual(self._payload()["widgets"][0]["name"], "SHIPPED")


class VariantTests(ListStoreTests):
    KINDS = {"a": (Field("speed", type="int", default="1"),), "b": ()}
    MACHINES = ConfigList(
        "machines", (Field("app"), Field("name")),
        variant=ListVariant("app", kinds=lambda: list(VariantTests.KINDS),
                            fields=lambda kind: VariantTests.KINDS.get(kind)))

    def test_a_kinds_settings_are_filled_and_typed(self) -> None:
        with declared(self.MACHINES):
            store = self._store()
            store.set_list("machines", [{"app": "a", "name": "N", "settings": {"speed": "4"}},
                                         {"app": "a", "name": "M"}])

            first, second = store.get_list("machines")

        self.assertEqual(first["settings"], {"speed": 4})
        self.assertEqual(second["settings"], {"speed": 1})

    def test_a_setting_its_kind_does_not_declare_is_refused(self) -> None:
        with declared(self.MACHINES):
            with self.assertRaisesRegex(ConfigListError, "kind a has no setting fast"):
                self._store().set_list("machines", [{"app": "a", "settings": {"fast": "1"}}])

    def test_a_kind_this_install_lacks_keeps_its_settings_as_written(self) -> None:
        with declared(self.MACHINES):
            store = self._store()
            store.set_list("machines", [{"app": "gone", "settings": {"x": [1, 2]}}])

            self.assertEqual(store.get_list("machines")[0]["settings"], {"x": [1, 2]})

    def test_settings_must_be_an_object(self) -> None:
        with declared(self.MACHINES):
            with self.assertRaises(ConfigListError):
                self._store().set_list("machines", [{"app": "a", "settings": "x"}])

    def test_a_list_without_a_variant_has_no_settings(self) -> None:
        with declared(WIDGETS):
            with self.assertRaisesRegex(ConfigListError, "has no field settings"):
                self._store().set_list("widgets", [{"settings": {}}])


class SeedTests(ListStoreTests):
    saw: tuple[bool, str]

    def _seed(self, store: ConfigStore) -> None:
        self.calls += 1
        self.saw = store.config.has_option("general", "game_root_dir"), \
            store.config.get("general", "game_root_dir", fallback="")
        store.set_list("widgets", [{"name": "From 2.x"}])
        if self.saw[0]:
            store.config.remove_option("general", "game_root_dir")

    def setUp(self) -> None:
        super().setUp()
        self.calls = 0
        self.saw = (False, "")

    def test_a_seed_runs_once_when_an_ini_is_converted(self) -> None:
        self.ini.write_text("[Settings]\ntablerootdir = /library\n", encoding="utf-8")

        with declared(WIDGETS, seeds={"widgets": self._seed}):
            self._store()
            self._store()

        self.assertEqual(self.calls, 1)
        self.assertEqual([i["name"] for i in self._payload()["widgets"]], ["From 2.x"])

    def test_a_seed_sees_the_current_key_names_and_only_what_the_person_set(self) -> None:
        self.ini.write_text("[Settings]\ntablerootdir = /library\n", encoding="utf-8")

        with declared(WIDGETS, seeds={"widgets": self._seed}):
            self._store()
        self.assertEqual(self.saw, (True, "/library"))

        self.ini.unlink()
        self.json.unlink()
        self.ini.write_text("[Settings]\n[Display]\nrotation = 90\n", encoding="utf-8")
        with declared(WIDGETS, seeds={"widgets": self._seed}):
            self._store()
        self.assertEqual(self.saw, (False, ""))

    def test_what_a_seed_consumes_is_not_written(self) -> None:
        self.ini.write_text("[Settings]\ntablerootdir = /library\n", encoding="utf-8")

        with declared(WIDGETS, seeds={"widgets": self._seed}):
            store = self._store()

        self.assertNotEqual(store.value("general", "game_root_dir"), "/library")

    def test_a_seed_never_runs_on_a_json_load(self) -> None:
        with declared(WIDGETS):
            self._store()

        with declared(WIDGETS, seeds={"widgets": self._seed}):
            self._store()

        self.assertEqual(self.calls, 0)

    def test_a_seed_never_runs_on_a_fresh_install(self) -> None:
        with declared(WIDGETS, seeds={"widgets": self._seed}):
            self._store()

        self.assertEqual(self.calls, 0)

    def test_a_list_no_seed_wrote_still_gets_its_default_on_conversion(self) -> None:
        self.ini.write_text("[Settings]\n", encoding="utf-8")

        with declared(WIDGETS):
            self._store()

        self.assertEqual([i["name"] for i in self._payload()["widgets"]], ["Shipped"])

    def test_a_seed_that_fails_does_not_stop_the_conversion(self) -> None:
        def broken(store: ConfigStore) -> None:
            raise RuntimeError("no")

        self.ini.write_text("[Settings]\ntablerootdir = /library\n", encoding="utf-8")

        with declared(WIDGETS, seeds={"widgets": broken}), self.assertLogs(
                "vpinfe.common.config.config_store", "ERROR"):
            store = self._store()

        self.assertEqual(store.value("general", "game_root_dir"), "/library")
        self.assertEqual([i["name"] for i in self._payload()["widgets"]], ["Shipped"])

    def test_a_seed_that_fails_part_way_leaves_no_half_a_list(self) -> None:
        def half(store: ConfigStore) -> None:
            store.set_list("widgets", [{"id": "a", "name": "Half"}])
            raise RuntimeError("no")

        self.ini.write_text("[Settings]\ntablerootdir = /library\n", encoding="utf-8")

        with declared(WIDGETS, seeds={"widgets": half}), self.assertLogs(
                "vpinfe.common.config.config_store", "ERROR"):
            self._store()

        self.assertEqual([i["name"] for i in self._payload()["widgets"]], ["Shipped"])


class SchemaTests(ListStoreTests):
    def test_the_schema_describes_each_list_and_its_fields(self) -> None:
        with declared(WIDGETS):
            described = config_service.schema()["lists"]

        [widgets] = described
        self.assertEqual((widgets["key"], widgets["home"]), ("widgets", "widgets"))
        by_key = {f["key"]: f for f in widgets["fields"]}
        self.assertEqual(list(by_key), [f.key for f in WIDGET_FIELDS])
        self.assertEqual(by_key["size"]["minimum"], 1)
        self.assertEqual(by_key["shape"]["choices"],
                         [{"value": "round", "label": "Round"},
                          {"value": "square", "label": "Square"}])
        self.assertIsNone(widgets["variant"])

    def test_a_variant_lists_the_fields_of_each_kind_it_has_now(self) -> None:
        with declared(VariantTests.MACHINES):
            [launchers] = config_service.schema()["lists"]

        kinds = {k["kind"]: [f["key"] for f in k["fields"]]
                 for k in launchers["variant"]["kinds"]}
        self.assertEqual((launchers["variant"]["field"], kinds), ("app", {"a": ["speed"], "b": []}))
