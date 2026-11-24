"""How a mod reads, and where it sits in the release picker."""

from __future__ import annotations

import unittest
from functools import partial
from typing import Any
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlparse

from manager.kit import game_tables, panel
from manager.library.release_match import in_lineage
from manager.workbench import game_match, game_table
from tests.support.workbench_patch import patch_workbench


async def _now(callback: Any, *args: Any, **kwargs: Any) -> Any:
    return callback(*args, **kwargs)


def _mod(parent: str = "", **said: Any) -> dict[str, Any]:
    return {"vps_file_id": parent, "version": "", "authors": [], "game": "", "url": "",
            "note": "", "game_id": "", "table_id": "", **said}


def _release(own: str, mod_of: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"vps_file_id": own, "mod_of": mod_of}


def _order(releases: list[dict[str, Any]]) -> list[tuple[str, bool]]:
    return [(one["vps_file_id"], under) for one, under in in_lineage(releases)]


class Words(unittest.TestCase):
    def test_a_linked_mod_names_the_version_and_makers(self) -> None:
        self.assertEqual("Mod of 1.2 · VPW", game_tables.mod_line(
            _mod("p", version="1.2", authors=["VPW"])))

    def test_the_game_is_named_only_where_it_is_another(self) -> None:
        self.assertEqual("Mod of The Addams Family · 1.2 · VPW", game_tables.mod_line(
            _mod("p", version="1.2", authors=["VPW"], game="The Addams Family")))

    def test_a_tagged_mod_reads_as_vps_wrote_it(self) -> None:
        self.assertEqual("Mod · FSS MOD", game_tables.mod_line(_mod(note="FSS MOD")))
        self.assertEqual("FSS MOD", game_tables.mod_of_said(_mod(note="FSS MOD")))

    def test_with_nothing_to_go_on_it_is_a_mod_of_something_unknown(self) -> None:
        self.assertEqual("Mod of Unknown", game_tables.mod_line(_mod()))

    def test_a_release_that_is_no_mod_says_nothing(self) -> None:
        self.assertEqual(("", ""), (game_tables.mod_line(None),
                                    game_tables.mod_of_said(None)))


class Lineage(unittest.TestCase):
    def test_a_mod_follows_the_release_it_is_based_on_one_step_in(self) -> None:
        releases = [_release("mod", _mod("base")), _release("other"), _release("base")]
        self.assertEqual([("other", False), ("base", False), ("mod", True)],
                         _order(releases))

    def test_a_mod_of_a_mod_is_one_step_in_too_and_follows_its_own(self) -> None:
        releases = [_release("base"), _release("deeper", _mod("mod")),
                    _release("mod", _mod("base")), _release("second", _mod("base"))]
        self.assertEqual([("base", False), ("mod", True), ("deeper", True),
                          ("second", True)], _order(releases))

    def test_a_mod_of_something_not_listed_keeps_its_place(self) -> None:
        releases = [_release("a"), _release("elsewhere", _mod("another-game")),
                    _release("tagged", _mod()), _release("b")]
        self.assertEqual([("a", False), ("elsewhere", False), ("tagged", False),
                          ("b", False)], _order(releases))

    def test_a_loop_of_links_is_listed_once_each(self) -> None:
        releases = [_release("x"), _release("one", _mod("two")),
                    _release("two", _mod("one"))]
        self.assertEqual([("x", False), ("one", False), ("two", True)], _order(releases))


class Line(unittest.TestCase):
    def setUp(self) -> None:
        self.ui = self.enterContext(patch_workbench("ui"))
        self.link = self.enterContext(patch.object(panel, "link"))
        self.out = self.enterContext(patch.object(panel, "out"))

    def _said(self) -> list[str]:
        return [one.args[0] for one in self.ui.label.call_args_list]

    def test_a_base_the_library_holds_opens_that_table_in_its_own_game(self) -> None:
        game_match._based_on(_mod("p", version="1.2", authors=["VPW"], game="Other Game",
                                 game_id="g2", table_id="t9"))

        self.assertEqual(("Mod of Other Game · 1.2 · VPW",), self.link.call_args.args)
        self.assertEqual({"view": ["tables"], "game": ["g2"], "table": ["t9"]},
                         parse_qs(urlparse(self.link.call_args.kwargs["to"]).query))
        self.out.assert_not_called()
        self.assertEqual([], self._said())

    def test_a_base_it_does_not_hold_is_missing_and_found_on_vps(self) -> None:
        game_match._based_on(_mod("p", version="1.2", authors=["VPW"],
                                 url="https://vps.example/?game=m"))

        self.assertEqual(["Mod of 1.2 · VPW", game_tables.GONE_WORDS[0]], self._said())
        self.assertEqual("https://vps.example/?game=m", self.out.call_args.kwargs["to"])
        self.link.assert_not_called()

    def test_a_mod_with_no_link_has_nowhere_to_go(self) -> None:
        game_match._based_on(_mod(note="FSS MOD"))

        self.assertEqual(["Mod · FSS MOD"], self._said())
        self.link.assert_not_called()
        self.out.assert_not_called()

    def test_a_release_that_is_no_mod_draws_nothing(self) -> None:
        game_match._based_on(None)

        self.ui.row.assert_not_called()


class MatchGroup(unittest.IsolatedAsyncioTestCase):
    async def test_a_table_matched_to_a_mod_says_what_it_is_a_mod_of(self) -> None:
        mod = _mod("p", version="1.2")
        library = Mock()
        library.vps_releases.return_value = [{"vps_file_id": "r-1", "version": "1.3"}]
        context = {"library": library, "game": {"vps_id": "e-1"}}
        with patch("manager.kit.offload.run.io_bound", new=_now):
            rows = await game_table._release_match(
                context, {"source": {"vps_file_id": "r-1", "mod_of": mod}})

        drawn = [one.args for _, one in rows
                 if isinstance(one, partial) and one.func is game_match._based_on]
        self.assertEqual([(mod,)], drawn)


if __name__ == "__main__":
    unittest.main()
