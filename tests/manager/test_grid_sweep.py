"""The views each Manager grid opens on, in the order the picker lists them. A person reads
the first few columns of a view at panel width, so which ones come first is the contract."""

from __future__ import annotations

import unittest

from manager.devices import devices
from manager.frontend import themes
from manager.library import games, tageditor


def _columns(presets: dict, key: str) -> list[str]:
    return list(presets[key].columns)


class Games(unittest.TestCase):
    def test_the_picker_opens_on_details_and_unmatched_has_a_place(self) -> None:
        self.assertEqual("manager.view.details", next(iter(games.GAME_VIEWS)))
        self.assertIn("manager.view.unmatched", games.GAME_VIEWS)
        unmatched = games.GAME_VIEWS["manager.view.unmatched"]
        assert not isinstance(unmatched, list)
        self.assertEqual({"vps_match": {"values": ["unmatched"]}}, unmatched.filters)

    def test_a_game_s_facts_lead_with_the_name_and_the_exceptions_come_last(self) -> None:
        fields = [one["field"] for one in games.COLUMNS]

        self.assertEqual("name", fields[0])
        self.assertLess(fields.index("vps_match"), fields.index("manufacturer"))
        self.assertLess(fields.index("rating"), fields.index("manufacturer"))
        self.assertLess(fields.index("manufacturer"), fields.index("hidden"))

    def test_the_frontend_view_is_about_what_the_frontend_offers(self) -> None:
        self.assertEqual(["name", "collections", "hidden", "private"],
                         _columns(games.GAME_VIEWS, "manager.view.frontend"))


class Tables(unittest.TestCase):
    def test_ratings_have_a_view_of_their_own_and_leave_the_table_file_view(self) -> None:
        file_view = _columns(games.TABLE_VIEWS, "manager.game_tables.table_file")

        self.assertEqual(["game", games.GAME_RATING, "rating", "tags"],
                         _columns(games.TABLE_VIEWS, "manager.view.ratings"))
        self.assertNotIn("rating", file_view)
        self.assertNotIn(games.GAME_RATING, file_view)


class Themes(unittest.TestCase):
    def test_everything_leads_and_active_is_not_a_view_of_its_own(self) -> None:
        self.assertEqual(["manager.view.everything", "manager.view.themes_installed",
                          "manager.view.updates", "manager.view.themes_available"],
                         list(themes.VIEWS))

    def test_status_is_shown_only_where_the_rows_differ_in_it(self) -> None:
        for key, quiet in (("manager.view.updates", True), ("manager.view.themes_available", True),
                           ("manager.view.themes_installed", False),
                           ("manager.view.everything", False)):
            with self.subTest(view=key):
                preset = themes.VIEWS[key]
                assert not isinstance(preset, list)
                self.assertEqual(quiet, "status" not in preset.columns)


class Devices(unittest.TestCase):
    def test_everything_shows_the_address_that_tells_stale_rows_apart(self) -> None:
        columns = _columns(devices.VIEWS, "manager.view.everything")

        self.assertEqual(["name", "kind", "state", "last_seen", "address"], columns[:5])
        self.assertEqual("manager.view.everything", next(iter(devices.VIEWS)))


class Tags(unittest.TestCase):
    def test_two_spellings_shows_the_group_beside_the_tag(self) -> None:
        self.assertEqual(["tag", "same"],
                         _columns(tageditor.VIEWS, "manager.tageditor.two_spellings")[:2])
        self.assertEqual("same", [one["field"] for one in tageditor.COLUMNS][1])


if __name__ == "__main__":
    unittest.main()
