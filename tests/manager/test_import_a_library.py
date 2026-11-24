"""Library > Locations offers the importer beside Add, and the folder picker names a new folder."""

from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import Mock, patch

from manager.kit import folder_picker
from manager.settings import library_folders

ACTION = {"key": "import", "label": "Import a Library"}


async def _now(callback: Any, *args: Any, **kwargs: Any) -> Any:
    return callback(*args, **kwargs)


class ImporterOnLocations(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.enterContext(patch("nicegui.run.io_bound", new=_now))
        self.library = Mock()

    async def test_the_importer_import_is_offered_when_it_is_running(self) -> None:
        self.library.extensions.return_value = [
            {"name": "library_importer", "actions": [ACTION]}]
        self.assertEqual(await library_folders.import_action(self.library), ACTION)

    async def test_an_extension_with_no_actions_offers_nothing(self) -> None:
        self.library.extensions.return_value = [{"name": "library_importer", "actions": []}]
        self.assertIsNone(await library_folders.import_action(self.library))

    async def test_an_importer_that_is_not_listed_offers_nothing(self) -> None:
        self.library.extensions.return_value = []
        self.assertIsNone(await library_folders.import_action(self.library))

    async def test_another_extensions_import_is_not_the_importers(self) -> None:
        self.library.extensions.return_value = [
            {"name": "something_else", "actions": [ACTION]}]
        self.assertIsNone(await library_folders.import_action(self.library))

    async def test_another_action_of_the_importer_is_not_the_import(self) -> None:
        self.library.extensions.return_value = [
            {"name": "library_importer", "actions": [{"key": "other", "label": "Other"}]}]
        self.assertIsNone(await library_folders.import_action(self.library))

    async def test_a_list_that_will_not_load_offers_nothing(self) -> None:
        self.library.extensions.side_effect = OSError("down")
        self.assertIsNone(await library_folders.import_action(self.library))


class NameForANewFolder(unittest.TestCase):
    def test_the_plain_name_when_it_is_free(self) -> None:
        self.assertEqual(folder_picker._free_name([{"name": "tables"}]), "New folder")

    def test_the_next_number_when_it_is_taken(self) -> None:
        held = [{"name": "New folder"}]
        self.assertEqual(folder_picker._free_name(held), "New folder 2")

    def test_the_first_free_number_when_several_are_taken(self) -> None:
        held = [{"name": "New folder"}, {"name": "New folder 2"}, {"name": "New folder 4"}]
        self.assertEqual(folder_picker._free_name(held), "New folder 3")

    def test_case_does_not_make_a_name_free(self) -> None:
        self.assertEqual(folder_picker._free_name([{"name": "new FOLDER"}]), "New folder 2")

    def test_an_empty_folder_gets_the_plain_name(self) -> None:
        self.assertEqual(folder_picker._free_name([]), "New folder")


if __name__ == "__main__":
    unittest.main()
