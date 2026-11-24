"""The card of a file the game holds in the Discovery pane: the release it is matched to, the
Source only where a catalog is known to have supplied it, and the verbs it carries."""

from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from typing import Any, cast
from unittest import mock

from nicegui import core, ui

from common.i18n import t
from manager.library import discovery_list, file_acts
from manager.library.discovery_kinds import Kind, Scene
from manager.library.discovery_list import _Held

_PAGE = ui.element()

RECORD = {"vps_file_id": "r1", "version": "1.0", "authors": ["Alice"]}
TIED = {"name": "rulesheet.pdf", "vps_file_id": "r1"}
LOOSE = {"name": "rulesheet.pdf", "vps_file_id": ""}


def _kind(**more: Any) -> Kind:
    return Kind(key="discovery_rulefiles", word="Rule Sheet", listed_as="ruleFiles",
                records=[RECORD], **more)


def _scene() -> Scene:
    return cast(Scene, SimpleNamespace(game_id="g1", title="Alpha", vps_id="E1"))


class HeldCardTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        was = core.loop
        core.loop = asyncio.get_running_loop()
        self.addCleanup(setattr, core, "loop", was)

    def _card(self, held: dict[str, str], *, origin: str = "",
              acts: dict[str, Any] | None = None) -> tuple[list[str], list[str]]:
        library = mock.Mock()
        library.update_moves.return_value = []
        library.set_aside_moves.return_value = []
        library.media = {}
        kind = _kind(mine=[held])
        one = _Held(held["name"], held["vps_file_id"], {"origin": origin} if origin else None)
        with _PAGE:
            body = ui.column()
            with body:
                discovery_list._held_card({"library": library, "state": {}}, _scene(), kind,
                                          one, acts or {})
        labels = [each.text for each in body.descendants() if isinstance(each, ui.label)]
        buttons = [each.text for each in body.descendants() if isinstance(each, ui.button)]
        return labels, buttons

    async def test_a_file_matched_to_a_release_names_it(self) -> None:
        labels, buttons = self._card(TIED)

        self.assertIn(t("manager.discovery.matched_to",
                        release=discovery_list._head(RECORD)), labels)
        self.assertIn(t("manager.workbench.change_match_2"), buttons)

    async def test_a_file_matched_to_nothing_says_so_and_offers_to_match_it(self) -> None:
        labels, buttons = self._card(LOOSE)

        self.assertIn(t("manager.discovery.not_matched"), labels)
        self.assertIn(t("manager.workbench.match"), buttons)

    async def test_the_old_local_and_vps_source_lines_are_gone(self) -> None:
        for held in (TIED, LOOSE):
            labels, _buttons = self._card(held)
            self.assertFalse([one for one in labels if one.startswith("Source:")], held)

    async def test_a_source_is_named_where_a_catalog_is_known_to_have_supplied_it(self) -> None:
        labels, _buttons = self._card(TIED, origin="vpinmediadb")

        self.assertEqual(1, len([one for one in labels if one.startswith("Source:")]))

    async def test_the_verbs_the_games_pane_offers_ride_on_the_card(self) -> None:
        acts = {file_acts.REPLACE: mock.Mock(label="Replace", run=mock.Mock(), hint=""),
                file_acts.REMOVE: mock.Mock(label="Remove", run=mock.Mock(), hint="")}
        _labels, buttons = self._card(TIED, acts=acts)

        self.assertEqual(["Replace", t("manager.workbench.change_match_2"), "Remove"], buttons)


class HeldActsTests(unittest.TestCase):
    def _acts(self, kind: Kind, detail: dict[str, Any]) -> list[str]:
        both = {file_acts.REPLACE: mock.Mock(), file_acts.REMOVE: mock.Mock()}
        row = {"id": "g1:wheel:", "game_id": "g1", "kind": "wheel", "via": "default"}
        library = mock.Mock(games=[])
        with mock.patch.object(file_acts, "held_acts", return_value=both):
            acts = discovery_list._acts({"library": library, "state": {}}, _scene(), kind,
                                        _Held("wheel.png", "", row, detail))
        return list(acts)

    def test_remove_is_offered_for_the_game_s_file_and_for_the_default_that_shows(self) -> None:
        kind = Kind(key="discovery_wheel", word="Wheel", media="wheel")

        for tier in ("game", "default"):
            with self.subTest(tier=tier):
                self.assertEqual(
                    [file_acts.REPLACE, file_acts.REMOVE],
                    self._acts(kind, {"tiers": [{"tier": tier, "file": "wheel.png",
                                                 "wins": True}]}))

    def test_remove_is_left_off_a_default_the_game_does_not_show(self) -> None:
        kind = Kind(key="discovery_wheel", word="Wheel", media="wheel")

        self.assertEqual([file_acts.REPLACE],
                         self._acts(kind, {"tiers": [{"tier": "default", "file": "wheel.png",
                                                      "wins": False}]}))


class HeldsTests(unittest.TestCase):
    def test_a_media_kind_holds_the_file_of_its_slot_and_the_release_it_is_matched_to(self) -> None:
        rows = [{"path": "medias/other.png"}, {"path": "medias/wheel.png", "file": "wheel.png"}]
        kind = Kind(key="discovery_wheel", word="Wheel", media="wheel", held=True, rows=rows,
                    slot={"path": "medias/wheel.png", "file": "wheel.png", "matched_to": "r1"})

        (one,) = discovery_list._helds(kind)

        self.assertEqual(("wheel.png", "r1", rows[1]), (one.name, one.vps_file_id, one.row))
        self.assertEqual({"r1"}, discovery_list._tied(kind))

    def test_a_media_kind_with_no_file_holds_nothing(self) -> None:
        kind = Kind(key="discovery_wheel", word="Wheel", media="wheel", held=False,
                    slot={"matched_to": "r1"})

        self.assertEqual([], discovery_list._helds(kind))
        self.assertEqual(set(), discovery_list._tied(kind))

    def test_a_file_kind_holds_each_file_with_the_rows_the_games_pane_draws_for_it(self) -> None:
        rows = [{"id": "a", "file": "RuleSheet.PDF"}, {"id": "b", "file": "other.pdf"}]
        kind = _kind(mine=[TIED, {"name": "gone.pdf", "vps_file_id": ""}], rows=rows,
                     details={"a": {"size_bytes": 1}})

        found = discovery_list._helds(kind)

        self.assertEqual([("rulesheet.pdf", "r1"), ("gone.pdf", "")],
                         [(one.name, one.vps_file_id) for one in found])
        self.assertEqual({"r1"}, discovery_list._tied(kind))
        self.assertEqual((rows[0], {"size_bytes": 1}), (found[0].row, found[0].detail))
        self.assertIsNone(found[1].row)


if __name__ == "__main__":
    unittest.main()
