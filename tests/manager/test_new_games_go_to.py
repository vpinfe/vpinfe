"""A setting another page is the home of is drawn there and not a second time under its
own section: whether adding a game asks where it goes on Folders, and the spreadsheet
schedule on Discovery."""

from __future__ import annotations

import unittest

from common.settings import config_service
from manager.settings.settings import settings_here


class HomeTests(unittest.TestCase):
    def test_the_setting_names_the_folders_page_as_its_home(self) -> None:
        described = {(option["section"], option["key"]): option
                     for block in config_service.schema()["sections"]
                     for option in block["options"]}

        self.assertEqual(described[("updates", "ask_where_new_games_go")]["home"],
                         "library.folders")

    def test_the_spreadsheet_schedule_names_the_discovery_page_as_its_home(self) -> None:
        described = {(option["section"], option["key"]): option
                     for block in config_service.schema()["sections"]
                     for option in block["options"]}

        self.assertEqual(described[("updates", "download_spreadsheet")]["home"],
                         "library.discovery")

    def test_settings_leaves_out_what_another_page_is_the_home_of(self) -> None:
        schema = config_service.schema()["sections"]
        kept = {option["key"] for block in settings_here(schema)
                for option in block["options"]}

        self.assertNotIn("ask_where_new_games_go", kept)
        self.assertNotIn("download_spreadsheet", kept)
        self.assertIn("refresh_minutes", kept)


if __name__ == "__main__":
    unittest.main()
