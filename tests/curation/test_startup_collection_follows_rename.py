"""Renaming a collection used to leave `behavior.startup_collection` behind.

The setting holds a name because a collection has no id to hold instead. So renaming
one the frontend was set to open on left the setting naming something that no longer
existed - and the failure was quiet: the frontend logged it and showed the whole
library, which is what it also does when the setting was never set at all.

Deleting one had the same shape: the setting kept naming it. That clears instead of
following, which is the same outcome the frontend already fell back to, said out loud.

These pin the following, not the storage. A collection id would make the whole problem
go away and there is no id yet.

The section comes from the schema rather than a literal. Spelled by hand it was
`general` on both sides, so the fake answered to the same wrong name the service asked
for and the pair agreed with each other while the setting sat in `behavior`.
"""

from __future__ import annotations

import unittest
from unittest import mock

from common.config import config_schema
from common.devices import device_registry
from common.games import collections_service
from tests.support.library import TempTree

SECTION = next(o.section for o in config_schema.options()
               if o.key == "startup_collection")


class _Store:
    """The settings file, as much of it as this cares about."""

    def __init__(self, startup: str) -> None:
        self.values = {(SECTION, "startup_collection"): startup}
        self.saved = 0

    def value(self, section: str, key: str):
        return self.values.get((section, key))

    def set_value(self, section: str, key: str, value) -> None:
        self.values[(section, key)] = value

    def save(self) -> None:
        self.saved += 1

    @property
    def startup(self):
        return self.values[(SECTION, "startup_collection")]


class StartupCollectionFollowsRenameTests(TempTree):
    def _rename(self, store, old, new):
        with mock.patch.object(collections_service, "get_ini_config",
                               return_value=store):
            collections_service.follow_rename_in_settings(old, new)

    def test_the_setting_moves_with_the_collection_it_names(self) -> None:
        store = _Store("Friday Night")
        self._rename(store, "Friday Night", "Friday Nights")

        self.assertEqual(store.startup, "Friday Nights")
        self.assertEqual(store.saved, 1)

    def test_a_collection_it_does_not_name_leaves_it_alone(self) -> None:
        store = _Store("Friday Night")
        self._rename(store, "Solid State", "Solid States")

        self.assertEqual(store.startup, "Friday Night")

    def test_nothing_is_written_when_nothing_changed(self) -> None:
        """A rename of some other collection must not touch the settings file at all -
        writing it back rewrites every key through the parser."""
        store = _Store("Friday Night")
        self._rename(store, "Solid State", "Solid States")

        self.assertEqual(store.saved, 0)

    def test_an_unset_setting_stays_unset(self) -> None:
        store = _Store("")
        self._rename(store, "Friday Night", "Friday Nights")

        self.assertEqual(store.startup, "")
        self.assertEqual(store.saved, 0)

    def _delete(self, store, name):
        with mock.patch.object(collections_service, "get_ini_config",
                               return_value=store):
            collections_service.forget_in_settings(name)

    def test_deleting_the_collection_clears_the_setting(self) -> None:
        store = _Store("Friday Night")
        self._delete(store, "Friday Night")

        self.assertEqual(store.startup, "")
        self.assertEqual(store.saved, 1)

    def test_deleting_some_other_collection_leaves_it_alone(self) -> None:
        store = _Store("Friday Night")
        self._delete(store, "Solid State")

        self.assertEqual(store.startup, "Friday Night")
        self.assertEqual(store.saved, 0)

    def test_surrounding_space_does_not_stop_the_match(self) -> None:
        """The frontend strips before it resolves, so a setting with a stray space
        still opens the collection - and still has to follow it."""
        store = _Store("  Friday Night  ")
        self._rename(store, "Friday Night", "Friday Nights")

        self.assertEqual(store.startup, "Friday Nights")

    @staticmethod
    def _remembered() -> str:
        return device_registry.get_device_registry().self_get("last_collection", "")

    def test_the_remembered_collection_moves_with_it(self) -> None:
        store = _Store("Last Collection")
        device_registry.get_device_registry().set_self(last_collection="Friday Night")
        self._rename(store, "Friday Night", "Friday Nights")

        self.assertEqual((store.startup, self._remembered()),
                         ("Last Collection", "Friday Nights"))
        self.assertEqual(store.saved, 0)

    def test_deleting_the_remembered_collection_forgets_it(self) -> None:
        store = _Store("Last Collection")
        device_registry.get_device_registry().set_self(last_collection="Friday Night")
        self._delete(store, "Friday Night")

        self.assertEqual((store.startup, self._remembered()), ("Last Collection", ""))

    def test_another_collection_leaves_the_remembered_one_alone(self) -> None:
        store = _Store("Last Collection")
        device_registry.get_device_registry().set_self(last_collection="Friday Night")
        self._rename(store, "Solid State", "Solid States")
        self._delete(store, "Solid State")

        self.assertEqual((self._remembered(), store.saved), ("Friday Night", 0))


if __name__ == "__main__":
    unittest.main()
