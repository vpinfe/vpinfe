"""The Discovery rail: which heading a kind sits under, and the order inside it."""

from __future__ import annotations

import unittest

from common.i18n import t
from manager.library import discovery, discovery_get
from manager.library.discovery_kinds import Kind, Scene
from manager.workbench import discovery_rail


def _scene(files: list[Kind], media: list[Kind]) -> Scene:
    return Scene(True, True, "g1", "v1", discovery_get.Tray(), files, media, {}, None, [], 0, "t")


def _file(key: str, listed_as: str, word: str) -> Kind:
    return Kind(key, word, listed_as=listed_as)


def _media(key: str, word: str) -> Kind:
    return Kind(key, word, media=key)


class Rail(unittest.TestCase):
    def setUp(self) -> None:
        self.scene = _scene(
            [_file("rom", "romFiles", "asset.kind.rom.label"),
             _file("table", "tableFiles", "word.table"),
             _file("b2s", "b2sFiles", "asset.kind.backglass.label"),
             _file("alt", "altSoundFiles", "asset.kind.altsound.label")],
            [_media("wheel", "media.kind.wheel.label"),
             _media("cab", "media.kind.cab.label"),
             _media("rule_sheet", "media.kind.rule_sheet.label"),
             _media("topper", "media.kind.topper.label")])

    def _rail(self) -> list[tuple[str, str]]:
        return [(one.key, one.group) for one in discovery_rail.sections(self.scene)]

    def test_the_table_sits_under_the_game_with_no_heading(self) -> None:
        rail = self._rail()

        self.assertEqual([("discovery_game", ""), ("table", "")], rail[:2])

    def test_there_is_no_heading_for_guides(self) -> None:
        groups = {group for _key, group in self._rail()}

        self.assertEqual(2, len(groups - {""}))
        self.assertNotIn(t("manager.workbench.guides"), groups)

    def test_a_rule_sheet_sits_with_the_media(self) -> None:
        groups = dict(self._rail())

        self.assertEqual(groups["wheel"], groups["rule_sheet"])

    def test_each_heading_lists_its_kinds_alphabetically(self) -> None:
        keys = [key for key, _group in self._rail()]

        self.assertEqual(["alt", "b2s", "rom"], keys[2:5])
        self.assertEqual(["cab", "rule_sheet", "topper", "wheel"], keys[5:])

    def test_a_media_kind_with_nothing_to_show_is_not_in_the_rail(self) -> None:
        for one in self.scene.media:
            one.standing = discovery.BLANK
        self.scene.media[0].standing = discovery.HAVE
        self.scene.media[2].standing = discovery.MISSING

        keys = [key for key, _group in self._rail()]

        self.assertEqual(["rule_sheet", "wheel"], keys[5:])

    def test_a_blank_media_kind_that_could_not_be_read_stays(self) -> None:
        for one in self.scene.media:
            one.standing = discovery.BLANK
        self.scene.media[1].unread = "offline"

        keys = [key for key, _group in self._rail()]

        self.assertEqual(["cab"], keys[5:])

    def test_the_rail_never_reorders_when_a_kind_is_held(self) -> None:
        before = [key for key, _group in self._rail()]
        for one in self.scene.files:
            one.held = True

        self.assertEqual(before, [key for key, _group in self._rail()])
