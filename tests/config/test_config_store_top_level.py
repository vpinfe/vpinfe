"""Everything in the file beside `schema` and `settings` outlives a save.

A key a newer build wrote must survive an older build's save, and two stores changing
different items of one list in one file must both be there afterwards.
"""

from __future__ import annotations

import json

from common.config.config_store import ConfigStore
from tests.config.test_config_store import ConfigStoreTests


class TopLevelKeyTests(ConfigStoreTests):
    def _write(self, **extra: object) -> None:
        ConfigStore(str(self.ini)).save()
        payload = self._payload()
        payload.update(extra)
        self.json.write_text(json.dumps(payload), encoding="utf-8")

    def _ids(self, key: str = "things") -> list[str]:
        return [item["id"] for item in self._payload()[key]]

    def test_a_key_a_newer_build_wrote_survives_a_save(self) -> None:
        self._write(from_the_future={"anything": [1, 2]})

        store = ConfigStore(str(self.ini))
        store.set_value("Display", "rotation", "90")
        store.save()

        self.assertEqual(self._payload()["from_the_future"], {"anything": [1, 2]})

    def test_a_key_the_file_gained_after_this_store_read_survives_its_save(self) -> None:
        self._write()
        store = ConfigStore(str(self.ini))
        later = self._payload()
        later["added_meanwhile"] = [{"id": "a"}]
        self.json.write_text(json.dumps(later), encoding="utf-8")

        store.set_value("Display", "rotation", "90")
        store.save()

        self.assertEqual(self._payload()["added_meanwhile"], [{"id": "a"}])

    def test_two_stores_changing_different_items_of_one_list_both_survive(self) -> None:
        self._write(things=[{"id": "a", "n": 1}, {"id": "b", "n": 1}])
        first, second = ConfigStore(str(self.ini)), ConfigStore(str(self.ini))

        first.top_level["things"][0]["n"] = 2
        first.save()
        second.top_level["things"][1]["n"] = 3
        second.save()

        self.assertEqual(self._payload()["things"],
                         [{"id": "a", "n": 2}, {"id": "b", "n": 3}])

    def test_two_stores_adding_different_items_both_survive(self) -> None:
        self._write(things=[{"id": "a"}])
        first, second = ConfigStore(str(self.ini)), ConfigStore(str(self.ini))

        first.top_level["things"].append({"id": "b"})
        first.save()
        second.top_level["things"].append({"id": "c"})
        second.save()

        self.assertEqual(self._ids(), ["a", "b", "c"])

    def test_a_removed_item_stays_removed(self) -> None:
        self._write(things=[{"id": "a"}, {"id": "b"}])
        remover, bystander = ConfigStore(str(self.ini)), ConfigStore(str(self.ini))

        remover.top_level["things"] = [{"id": "b"}]
        remover.save()
        bystander.set_value("Display", "rotation", "90")
        bystander.save()

        self.assertEqual(self._ids(), ["b"])

    def test_an_item_the_file_lost_is_not_brought_back_by_a_store_that_left_it_alone(
            self) -> None:
        self._write(things=[{"id": "a"}, {"id": "b"}])
        remover, editor = ConfigStore(str(self.ini)), ConfigStore(str(self.ini))

        remover.top_level["things"] = [{"id": "a"}]
        remover.save()
        editor.top_level["things"][0]["n"] = 1
        editor.save()

        self.assertEqual(self._payload()["things"], [{"id": "a", "n": 1}])

    def test_the_file_order_holds_until_a_store_reorders(self) -> None:
        self._write(things=[{"id": "a"}, {"id": "b"}, {"id": "c"}])
        mover, other = ConfigStore(str(self.ini)), ConfigStore(str(self.ini))

        mover.top_level["things"].reverse()
        mover.save()
        self.assertEqual(self._ids(), ["c", "b", "a"])

        other.top_level["things"][0]["n"] = 1
        other.save()
        self.assertEqual(self._ids(), ["c", "b", "a"])

    def test_a_key_this_store_removed_stays_removed(self) -> None:
        self._write(gone={"x": 1})
        store = ConfigStore(str(self.ini))

        del store.top_level["gone"]
        store.save()

        self.assertNotIn("gone", self._payload())

    def test_reread_takes_in_the_keys_the_file_holds_now(self) -> None:
        self._write()
        store = ConfigStore(str(self.ini))
        later = self._payload()
        later["arrived"] = [1]
        self.json.write_text(json.dumps(later), encoding="utf-8")

        store.reread()

        self.assertEqual(store.top_level["arrived"], [1])

    def test_an_item_one_store_removed_and_the_other_changed_keeps_the_change(self) -> None:
        self._write(things=[{"id": "a"}, {"id": "b"}])
        remover, editor = ConfigStore(str(self.ini)), ConfigStore(str(self.ini))

        remover.top_level["things"] = [{"id": "a"}]
        remover.save()
        editor.top_level["things"][1]["n"] = 1
        editor.save()

        self.assertEqual(self._payload()["things"], [{"id": "a"}, {"id": "b", "n": 1}])

    def test_a_value_changed_from_one_to_true_is_a_change(self) -> None:
        self._write(flag={"v": 1})
        changer, bystander = ConfigStore(str(self.ini)), ConfigStore(str(self.ini))

        changer.top_level["flag"] = {"v": True}
        changer.save()
        bystander.set_value("Display", "rotation", "90")
        bystander.save()
        self.assertEqual(self._payload()["flag"], {"v": True})

    def test_a_list_without_ids_is_the_last_save_whole(self) -> None:
        self._write(plain=[1, 2])
        first, second = ConfigStore(str(self.ini)), ConfigStore(str(self.ini))

        first.top_level["plain"] = [1, 2, 3]
        first.save()
        second.top_level["plain"] = [1, 2, 4]
        second.save()

        self.assertEqual(self._payload()["plain"], [1, 2, 4])

    def test_the_schema_and_settings_are_not_overwritten_by_a_held_key(self) -> None:
        self._write()
        store = ConfigStore(str(self.ini))

        store.top_level["schema"] = 99
        store.top_level["settings"] = ["x"]
        store.save()

        payload = self._payload()
        self.assertNotEqual(payload["schema"], 99)
        self.assertIsInstance(payload["settings"], dict)
