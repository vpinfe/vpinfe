"""A kind's section in the Discovery pane: what the game has, then what else is available,
with the release the game's file is matched to never listed as available."""

from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from typing import Any, cast
from unittest import mock

from nicegui import core, ui

from common.i18n import t
from manager.library import discovery_list
from manager.library.discovery_kinds import Kind, Scene

_PAGE = ui.element()

HELD = {"vps_file_id": "r1", "version": "1.0", "authors": ["Alice"], "name": "Held Rules"}
OTHER = {"vps_file_id": "r2", "version": "2.0", "authors": ["Bob"], "name": "Other Rules"}


def _kind(**more: Any) -> Kind:
    more.setdefault("records", [HELD, OTHER])
    return Kind(key="discovery_rulefiles", word="Rule Sheet", listed_as="ruleFiles", **more)


def _scene() -> Scene:
    return cast(Scene, SimpleNamespace(game_id="g1", title="Alpha", vps_id="E1",
                                       tray=mock.Mock(), sources={}))


class HaveAvailableTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        was = core.loop
        core.loop = asyncio.get_running_loop()
        self.addCleanup(setattr, core, "loop", was)

    def _body(self, kind: Kind) -> ui.element:
        library = mock.Mock()
        library.update_moves.return_value = []
        library.media = {}
        library.games = []
        library.marks.return_value = []
        context = {"library": library, "state": {}, "discovery": {"id": "E1", "name": "Alpha"}}
        with _PAGE:
            holder = ui.column()
            with holder:
                discovery_list.body(context, _scene(), kind)
        return holder

    @staticmethod
    def _classes(holder: ui.element) -> list[str]:
        return [name for one in holder.descendants() for name in one.classes]

    @staticmethod
    def _labels(holder: ui.element) -> list[str]:
        return [one.text for one in holder.descendants() if isinstance(one, ui.label)]

    async def test_a_kind_with_no_file_shows_the_empty_box_and_every_release_as_available(
            self) -> None:
        holder = self._body(_kind())
        labels = self._labels(holder)

        self.assertIn(t("manager.discovery.have"), labels)
        self.assertIn(t("manager.discovery.none"), labels)
        self.assertIn(t("manager.discovery.drop_here"), labels)
        self.assertIn(t("manager.discovery.available"), labels)
        self.assertIn("manager-dropbox", self._classes(holder))

    async def test_a_held_file_replaces_the_empty_box(self) -> None:
        holder = self._body(_kind(held=True, mine=[{"name": "rules.pdf", "vps_file_id": "r1"}]))

        self.assertNotIn("manager-dropbox", self._classes(holder))
        self.assertIn("manager-have-card", self._classes(holder))

    async def test_the_matched_release_is_not_listed_as_available(self) -> None:
        kind = _kind(held=True, mine=[{"name": "rules.pdf", "vps_file_id": "r1"}])

        self.assertEqual(["r2"], [str(one["vps_file_id"])
                                  for one in discovery_list._unheld(kind)])

    async def test_nothing_available_leaves_no_available_heading(self) -> None:
        kind = _kind(records=[HELD], held=True,
                     mine=[{"name": "rules.pdf", "vps_file_id": "r1"}])
        labels = self._labels(self._body(kind))

        self.assertNotIn(t("manager.discovery.available"), labels)

    async def test_a_mod_is_listed_under_the_release_it_is_based_on(self) -> None:
        mod = {"vps_file_id": "m1", "version": "2.1", "authors": ["Cy"], "name": "A Mod",
               "mod_of": {"vps_file_id": "r1"}}
        holder = self._body(_kind(records=[HELD, OTHER, mod]))
        listed = next(one for one in holder.descendants() if "manager-rels" in one.classes)

        nested = ["manager-source-row--under" in one.classes
                  for one in listed.default_slot.children]

        self.assertEqual([False, True, False], nested[:3])

    async def test_the_card_steps_through_the_list_in_the_order_it_is_drawn(self) -> None:
        mod = {"vps_file_id": "m1", "version": "2.1", "authors": ["Cy"], "name": "A Mod",
               "mod_of": {"vps_file_id": "r1"}}
        holder = self._body(_kind(records=[mod, HELD, OTHER]))
        labels = " ".join(self._labels(holder))

        self.assertIn(t("manager.discovery.card_of", n=1, count=3) + " 1.0 · Alice", labels)

    async def test_the_counts_are_set_apart_from_the_headings(self) -> None:
        holder = self._body(_kind())
        counts = [one.text for one in holder.descendants()
                  if isinstance(one, ui.label) and "manager-select-count" in one.classes]

        self.assertEqual(["2"], counts)


if __name__ == "__main__":
    unittest.main()
