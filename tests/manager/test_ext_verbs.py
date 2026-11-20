"""What running extensions add to a game's and a table's menus: one block each after core's,
bundled first, then the rest by name, in the three menus that draw the one list."""

from __future__ import annotations

import asyncio
import unittest
import unittest.mock
from typing import Any
from unittest.mock import MagicMock

from nicegui import ui

from manager.extensions import ext_verbs
from manager.kit import panel
from manager.library import game_acts

_PAGE = ui.element()


async def _after(_games: list[str], _gone: bool) -> None:
    return None


async def _refresh(_ids: list[str]) -> None:
    return None


def _verb(subject: str, key: str, label: str, **fields: Any) -> dict[str, Any]:
    return {"subject": subject, "key": key, "label": label, "tip": "", "one": False,
            "danger": False, "confirm_key": "", "private_ok": False, **fields}


def _extension(name: str, display: str, *verbs: dict[str, Any], bundled: bool = False
               ) -> dict[str, Any]:
    return {"name": name, "display_name": display, "bundled": bundled,
            "library_verbs": list(verbs)}


STATE = {"extensions": [
    _extension("zeta", "Zeta", _verb("game", "z", "Zap"), _verb("table", "t", "Tag")),
    _extension("alpha", "Alpha", _verb("game", "a", "Ask", danger=True, tip="Asks first"),
               _verb("game", "b", "Bin", one=True)),
    _extension("pins", "Pins", _verb("game", "p", "Pin"), bundled=True),
    _extension("quiet", "Quiet"),
]}


async def _nothing() -> None:
    return None


class Offered(unittest.TestCase):
    def test_each_extension_is_a_block_bundled_first_then_by_name(self) -> None:
        offered = ext_verbs.offered(STATE, "game", [{"game_id": "afm"}], _nothing)

        self.assertEqual([(one.block, one.label) for one in offered],
                         [("Pins", "Pin"), ("Alpha", "Ask"), ("Alpha", "Bin"), ("Zeta", "Zap")])

    def test_a_table_is_offered_only_the_table_entries(self) -> None:
        offered = ext_verbs.offered(STATE, "table", [{"game_id": "afm", "table_id": "t"}],
                                    _nothing)

        self.assertEqual([(one.block, one.label) for one in offered], [("Zeta", "Tag")])

    def test_an_entry_keeps_what_the_extension_said_of_it(self) -> None:
        ask = next(one for one in ext_verbs.offered(STATE, "game", [{"game_id": "afm"}],
                                                    _nothing) if one.label == "Ask")
        bin_ = next(one for one in ext_verbs.offered(STATE, "game", [{"game_id": "afm"}],
                                                     _nothing) if one.label == "Bin")

        self.assertEqual((ask.danger, ask.one, ask.tip), (True, False, "Asks first"))
        self.assertEqual((bin_.danger, bin_.one), (False, True))

    def test_nothing_chosen_or_nothing_running_offers_nothing(self) -> None:
        self.assertEqual(ext_verbs.offered(STATE, "game", [], _nothing), [])
        self.assertEqual(ext_verbs.offered({}, "game", [{"game_id": "afm"}], _nothing), [])


def _acting(**declared: Any) -> dict[str, Any]:
    return {"name": "acts", "display_name": "Acts", "bundled": False, "library_verbs": [],
            "actions": [{"key": "share", "label": "Share", "add": [], "menu": [],
                         "private_ok": False, **declared}]}


GAMES = {"afm": {"id": "afm", "name": "AFM", "vps_id": "v1", "private": False},
         "hid": {"id": "hid", "name": "Hidden", "vps_id": "", "private": True}}


class Actions(unittest.TestCase):
    def test_an_action_in_a_menu_opens_once_for_each_public_game_with_who(self) -> None:
        state = {"extensions": [_acting(menu=["game", "table"])]}
        targets = [{"game_id": "afm", "table_id": "t1"}, {"game_id": "hid", "table_id": "t2"}]
        opened: list[Any] = []

        async def open_for(_extension: Any, key: str, whos: list[dict[str, str]]) -> bool:
            opened.append((key, whos))
            return False

        with unittest.mock.patch.object(ext_verbs.ext_lists, "open_for", open_for):
            (entry,) = ext_verbs.offered(state, "table", targets, _nothing, games=GAMES)
            self.assertEqual((entry.block, entry.label), ("Acts", "Share…"))
            asyncio.run(entry.run.func(*entry.run.args))  # type: ignore[union-attr]

        self.assertEqual(opened, [("share", [{"game_id": "afm", "vps_id": "v1", "name": "AFM",
                                              "table_id": "t1"}])])

    def test_a_private_game_is_offered_to_an_action_that_says_so_and_to_none_otherwise(
            self) -> None:
        hidden = [{"game_id": "hid"}]

        self.assertEqual(ext_verbs.offered({"extensions": [_acting(menu=["game"])]}, "game",
                                           hidden, _nothing, games=GAMES), [])
        self.assertEqual(len(ext_verbs.offered(
            {"extensions": [_acting(menu=["game"], private_ok=True)]}, "game", hidden,
            _nothing, games=GAMES)), 1)

    def test_the_plus_of_a_page_gets_the_actions_that_name_it(self) -> None:
        state = {"extensions": [_acting(add=["games"]), _acting(add=["media"])]}

        self.assertEqual([one.label for one in ext_verbs.added(state, "games")], ["Share…"])
        self.assertEqual(ext_verbs.added(state, "assets"), [])


class InTheMenus(unittest.TestCase):
    def test_a_game_s_verbs_end_with_the_extension_blocks(self) -> None:
        library = MagicMock()
        library.games = [{"id": "afm", "name": "Attack from Mars"}]

        verbs = game_acts.game_acts(library, library.games, STATE, _refresh)

        self.assertEqual([one.label for one in verbs][-4:], ["Pin", "Ask", "Bin", "Zap"])
        self.assertEqual({one.block for one in verbs[:-4]}, {""})

    def test_a_private_game_is_not_offered_to_extensions(self) -> None:
        library = MagicMock()
        library.games = [{"id": "afm", "name": "Attack from Mars"},
                         {"id": "tz", "name": "Twilight Zone", "private": True}]

        shown = game_acts.game_acts(library, library.games, STATE, _refresh)
        hidden = game_acts.game_acts(library, library.games[1:], STATE, _refresh)

        self.assertEqual([one.label for one in shown][-4:], ["Pin", "Ask", "Bin", "Zap"])
        self.assertFalse({"Pin", "Ask", "Bin", "Zap"} & {one.label for one in hidden})

    def test_an_entry_that_works_on_private_games_is_offered_on_them(self) -> None:
        library = MagicMock()
        library.games = [{"id": "afm", "name": "Attack from Mars"},
                         {"id": "tz", "name": "Twilight Zone", "private": True}]
        state = {"extensions": [_extension(
            "tidy", "Tidy", _verb("game", "t", "Tidy", private_ok=True),
            _verb("game", "s", "Send"))]}

        shown = game_acts.game_acts(library, library.games, state, _refresh)
        hidden = game_acts.game_acts(library, library.games[1:], state, _refresh)

        self.assertEqual([one.label for one in shown][-2:], ["Tidy", "Send"])
        self.assertEqual([one.label for one in hidden][-1:], ["Tidy"])

    def test_a_table_s_verbs_end_with_the_extension_blocks(self) -> None:
        table = {"id": "t1", "game_id": "afm", "available": True, "filename": "t1.vpx"}
        target = game_acts.Target("afm", "Attack from Mars", table, [table])

        verbs = game_acts.table_acts(MagicMock(), [target], _after, STATE, None)

        self.assertEqual([(one.block, one.label) for one in verbs][-1:], [("Zeta", "Tag")])

    def test_a_block_is_drawn_under_its_name_with_the_destructive_entry_last(self) -> None:
        entries = [panel.Verb("Launch", lambda: None),
                   panel.Verb("Forget", lambda: None, danger=True),
                   panel.Verb("Ask", lambda: None, danger=True, block="Alpha"),
                   panel.Verb("Bin", lambda: None, block="Alpha"),
                   panel.Verb("Zap", lambda: None, block="Zeta")]

        with _PAGE, ui.menu() as menu:
            panel.verb_entries(entries)

        drawn = [(type(one).__name__, getattr(one, "text", "") or one.props.get("label", ""))
                 for one in menu.descendants()]
        order = [text or kind for kind, text in drawn if text or kind == "Separator"]
        self.assertEqual(order, [
            "Launch", "Separator", "Forget",
            "Separator", "Alpha", "Bin", "Separator", "Ask",
            "Separator", "Zeta", "Zap"])


if __name__ == "__main__":
    unittest.main()
