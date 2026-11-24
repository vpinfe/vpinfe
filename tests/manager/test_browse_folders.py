"""Settings > Library > Folders: which settings the page is the home of, and what Settings
hands it to draw under the library's folders."""

from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from common.config import path_state
from common.settings import config_service
from manager.kit import offload, panel
from manager.settings import folders
from manager.settings import settings as settings_page
from manager.settings.settings import settings_here


class HomeTests(unittest.TestCase):
    def test_the_logos_setting_names_the_folders_page_as_its_home(self) -> None:
        described = {(option["section"], option["key"]): option
                     for block in config_service.schema()["sections"]
                     for option in block["options"]}

        self.assertEqual(described[("media", "manufacturer_logos_dir")]["home"],
                         "library.folders")

    def test_settings_leaves_out_what_another_page_is_the_home_of(self) -> None:
        schema = config_service.schema()["sections"]
        kept = {option["key"] for block in settings_here(schema)
                for option in block["options"]}

        self.assertNotIn("manufacturer_logos_dir", kept)
        self.assertIn("playfield_variant", kept)

    def test_it_has_a_heading_of_its_own_on_the_page(self) -> None:
        described = {(option["section"], option["key"]): option
                     for block in config_service.schema()["sections"]
                     for option in block["options"]}

        self.assertEqual(described[("media", "manufacturer_logos_dir")]["group_label"],
                         "Logos")

    def test_no_setting_lists_folders_to_add_from(self) -> None:
        keys = {option["key"] for block in config_service.schema()["sections"]
                for option in block["options"]}

        self.assertNotIn("browse_dirs", keys)


class PageTests(unittest.IsolatedAsyncioTestCase):
    """What Settings hands the Folders page to draw under the library's folders."""

    def setUp(self) -> None:
        self.enterContext(patch.object(
            offload, "io", new=AsyncMock(side_effect=lambda call, *args: call(*args))))
        self.enterContext(patch.object(
            path_state, "look_all",
            side_effect=lambda asks: [path_state.PathState(path_state.OK) for _ in asks]))
        self.enterContext(patch.object(settings_page, "_waiting", new=AsyncMock(return_value=[])))
        self.page = self.enterContext(patch.object(
            folders, "page", new=AsyncMock(return_value=lambda: None)))
        self.redraw = MagicMock()
        self.library = MagicMock()
        self.library.config_schema.side_effect = lambda: config_service.schema()["sections"]
        self.library.config_values.return_value = {"media": {"manufacturer_logos_dir": ""}}
        self.library.config_path_checks.return_value = []

    async def _below(self) -> list[tuple[Any, ...]]:
        await settings_page._folders_page(self.library, self.redraw, {})
        return await self.page.await_args.args[3]()  # type: ignore[union-attr]

    async def test_the_logos_setting_is_under_a_heading_of_its_own(self) -> None:
        found = await self._below()

        self.assertEqual([one[1] for one in found if one[0] is panel.HEADING], ["Logos"])

    async def test_a_settings_read_that_fails_says_so_in_place_of_them(self) -> None:
        self.library.config_values.side_effect = OSError("disk")

        found = await self._below()

        self.assertEqual(len(found), 1)
        self.assertIs(found[0][0], panel.FULL)


if __name__ == "__main__":
    unittest.main()
