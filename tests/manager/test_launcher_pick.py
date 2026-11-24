"""What a table's Launcher picker offers, and what is said under it."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from common.i18n import t
from manager.frontend import launchers
from manager.kit import panel
from manager.library import games
from manager.workbench import launcher


def _one(launcher_id: str, app: str = "vpx", *, enabled: bool = True,
         default: bool = False) -> dict:
    return {"launcher_id": launcher_id, "display_name": launcher_id.upper(), "app": app,
            "app_name": {"vpx": "Visual Pinball X", "generic": "Generic"}[app],
            "enabled": enabled, "is_default": default}


HELD = [_one("wrap", "generic", default=True), _one("vpx", default=True),
        _one("fourk"), _one("old", enabled=False)]


class OfferTests(unittest.TestCase):

    def test_its_own_app_s_launchers_come_before_other_programs(self) -> None:
        offer = launchers.launcher_offer(HELD, "vpx")

        self.assertEqual([(one["id"], one["other"]) for one in offer],
                         [("vpx", False), ("fourk", False), ("wrap", True)])

    def test_the_default_marked_is_its_own_app_s(self) -> None:
        marks = {one["id"]: one["mark"] for one in launchers.launcher_offer(HELD, "vpx")}

        self.assertEqual(marks, {"vpx": t("word.default"), "fourk": "", "wrap": ""})

    def test_a_switched_off_one_is_offered_only_to_a_table_that_names_it(self) -> None:
        named = launchers.launcher_offer(HELD, "vpx", named="old")

        self.assertNotIn("old", [one["id"] for one in launchers.launcher_offer(HELD, "vpx")])
        self.assertEqual(next(one["mark"] for one in named if one["id"] == "old"),
                         launchers.STATES.word(launchers.STATE_OFF))


class NoteTests(unittest.TestCase):

    LISTING = {"launchers": HELD,
               "apps": [{"id": "vpx", "name": "Visual Pinball X", "has_config": True},
                        {"id": "generic", "name": "Generic", "has_config": False}]}

    def _said(self, table: dict, listing: dict | None = None) -> list[str]:
        with patch.object(panel, "note", side_effect=lambda text: text):
            return [str(one) for one in launcher._launcher_notes(
                {"launchers": self.LISTING if listing is None else listing}, table)]

    def test_another_program_says_what_the_table_does_without(self) -> None:
        self.assertEqual(self._said({"app": "vpx", "launcher": "wrap"}),
                         [t("manager.workbench.runs_with_other", app="Generic",
                            own="Visual Pinball X")])

    def test_its_own_app_says_nothing(self) -> None:
        self.assertEqual(self._said({"app": "vpx", "launcher": "fourk"}), [])

    def test_nor_does_a_table_whose_app_has_nothing_to_give_up(self) -> None:
        self.assertEqual(self._said({"app": "generic", "launcher": "vpx"}), [])

    def test_an_install_with_none_says_so(self) -> None:
        self.assertEqual(self._said({"app": "vpx"}, {}),
                         [t("manager.workbench.launcher_hint")])


class GridRowTests(unittest.TestCase):
    """The grid's dot says what the picker's does."""

    def test_a_table_that_falls_back_says_so_beside_the_one_that_plays_it(self) -> None:
        (row,) = games.table_rows([{"id": "t", "launcher_name": "VPX",
                                    "launcher_set_here": True,
                                    "launcher_falls_back": True}])

        self.assertEqual((row["launcher"], row["launcher_falls_back"]),
                         (f"{games.SET_HERE_MARK}VPX", True))

    def test_a_table_that_follows_the_default_carries_neither(self) -> None:
        (row,) = games.table_rows([{"id": "t", "launcher_name": "VPX"}])

        self.assertEqual((row["launcher"], row["launcher_falls_back"]), ("VPX", False))


if __name__ == "__main__":
    unittest.main()
