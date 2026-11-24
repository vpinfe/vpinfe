"""Which groups an extension puts on a panel, and where the panel asks for them."""

from __future__ import annotations

import unittest
from typing import Any

from manager.extensions import ext_groups

GAME_GROUP = {"subject": "game", "title": "Notes", "base": "/groups/game", "save": True}
TABLE_GROUP = {"subject": "table", "title": "Here", "base": "/groups/table", "save": False}
CURATING = {**GAME_GROUP, "title": "Tidy", "private_ok": True}
EXTENSIONS: list[dict[str, Any]] = [{"name": "weekly", "groups": [GAME_GROUP, TABLE_GROUP]},
                                    {"name": "quiet", "groups": []},
                                    {"name": "bare"}]


def context(*, lens: str = "", private: bool = False) -> dict[str, Any]:
    return {"game": {"private": private}, "lens": lens, "game_id": "g 1",
            "state": {"extensions": EXTENSIONS}}


class Declared(unittest.TestCase):
    def test_a_games_panel_carries_the_groups_on_a_game(self) -> None:
        self.assertEqual(ext_groups.declared(context()),
                         [(EXTENSIONS[0], GAME_GROUP)])

    def test_a_tables_panel_carries_both(self) -> None:
        self.assertEqual(ext_groups.declared(context(lens="t1")),
                         [(EXTENSIONS[0], GAME_GROUP), (EXTENSIONS[0], TABLE_GROUP)])

    def test_a_private_game_has_no_group_on_either_panel(self) -> None:
        self.assertEqual(ext_groups.declared(context(private=True)), [])
        self.assertEqual(ext_groups.declared(context(lens="t1", private=True)), [])

    def test_a_private_game_has_the_groups_declared_to_work_on_one(self) -> None:
        held = {**context(private=True), "state": {"extensions": [
            {"name": "curator", "groups": [CURATING, TABLE_GROUP]}]}}

        self.assertEqual(ext_groups.declared(held), [({"name": "curator", "groups": [
            CURATING, TABLE_GROUP]}, CURATING)])

    def test_no_extensions_read_yet_is_no_group(self) -> None:
        held = {**context(), "state": {}}

        self.assertEqual(ext_groups.declared(held), [])


class TheRoute(unittest.TestCase):
    def test_a_game_group_is_asked_at_the_game(self) -> None:
        self.assertEqual(ext_groups.route(EXTENSIONS[0], GAME_GROUP, "g 1"),
                         "/ext/weekly/groups/game/g%201")

    def test_a_table_group_is_asked_at_the_game_and_the_table(self) -> None:
        self.assertEqual(ext_groups.route(EXTENSIONS[0], TABLE_GROUP, "g 1", "a/b"),
                         "/ext/weekly/groups/table/g%201/a%2Fb")


if __name__ == "__main__":
    unittest.main()
