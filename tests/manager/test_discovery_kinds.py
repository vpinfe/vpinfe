"""What the Discovery pane is drawn from: the kinds an entry lists and a game holds."""

from __future__ import annotations

import unittest
from typing import Any, cast
from unittest import mock

from common.media.media_specs import MEDIA_SPECS
from manager.kit import offload
from manager.kit.data import Library
from manager.library import discovery, discovery_get, discovery_kinds, discovery_list
from manager.library.discovery_kinds import Kind, Scene
from tests.support.game_offers import answer_from

TABLE = "tableFiles"
BACKGLASS = "b2sFiles"
ROM = "romFiles"


def _entry(**over: Any) -> dict[str, Any]:
    return {"vps_id": "v1", "name": "Attack from Mars", "manufacturer": "Bally", "year": 1995,
            "type": "SS", "themes": [], "url": "https://vps/v1",
            "listed": [TABLE, BACKGLASS, ROM], "in_library": True, "held": [TABLE],
            **over}


def _held(**files: list[dict[str, Any]]) -> dict[str, Any]:
    kinds = [{"kind": listed_as, "held": bool(files.get(listed_as)),
              "files": files.get(listed_as, [])}
             for listed_as in (BACKGLASS, ROM, "wheelArtFiles")]
    return {"kinds": kinds, "tables": files.get(TABLE, [])}


def _release(file_id: str, updated: str = "2026-01-01") -> dict[str, Any]:
    return {"vps_file_id": file_id, "updated_at": updated, "name": file_id}


class Naming(unittest.TestCase):
    def test_a_section_is_named_for_its_kind_in_lower_case(self) -> None:
        self.assertEqual("discovery_b2sfiles", discovery_kinds.section_key(BACKGLASS))
        self.assertEqual("discovery_wheel", discovery_kinds.section_key("wheel"))

    def test_the_vocabulary_has_the_game_every_listing_and_every_media_kind(self) -> None:
        words = discovery_kinds.vocabulary()

        self.assertIn(discovery_kinds.GAME, words)
        for listed_as, _field, _word in discovery.KINDS:
            self.assertIn(discovery_kinds.section_key(listed_as), words)
        for spec in MEDIA_SPECS:
            self.assertIn(discovery_kinds.section_key(spec.kind), words)

    def test_a_file_loses_the_title_every_file_of_the_game_starts_with(self) -> None:
        self.assertEqual("VPW 1.2", discovery_kinds.short_name("Attack from Mars - VPW 1.2",
                                                                "Attack from Mars"))

    def test_a_name_that_is_only_the_title_keeps_it(self) -> None:
        self.assertEqual("Attack from Mars",
                         discovery_kinds.short_name("Attack from Mars", "Attack from Mars"))

    def test_a_name_that_does_not_start_with_the_title_is_left_whole(self) -> None:
        self.assertEqual("AFM (Bally 1995)",
                         discovery_kinds.short_name("AFM (Bally 1995)", "Attack from Mars"))

    def test_a_name_that_would_keep_only_a_year_or_a_bracket_is_left_whole(self) -> None:
        for name in ("Star Trek (Williams 1993).vpx", "Star Trek [patched].vpx",
                     "Star Trek 1993.vpx", "Star Trek.vpx", "Star Trek.directb2s",
                     "Star Trek - 1993.directb2s"):
            with self.subTest(name=name):
                self.assertEqual(name, discovery_kinds.short_name(name, "Star Trek"))

    def test_a_name_with_words_after_the_title_still_loses_it(self) -> None:
        self.assertEqual("VPW 1.2 (Williams 1993).vpx",
                         discovery_kinds.short_name("Star Trek - VPW 1.2 (Williams 1993).vpx",
                                                    "Star Trek"))

    def test_no_title_leaves_the_name_whole(self) -> None:
        self.assertEqual("a.vpx", discovery_kinds.short_name("a.vpx", ""))


class Dropping(unittest.TestCase):
    def test_a_file_dropped_on_topper_may_be_a_picture_or_a_video(self) -> None:
        ours = discovery_get.media_ours("topperFiles", "topper")

        self.assertEqual("topper", discovery_get.slot_of(ours, "media", "t.png"))
        self.assertEqual("topper_video", discovery_get.slot_of(ours, "media", "t.mp4"))

    def test_a_kind_vps_lists_nothing_for_is_its_own(self) -> None:
        self.assertEqual(("flyer",), discovery_get.media_ours("", "flyer"))


class Reading(unittest.TestCase):
    def test_a_kind_counts_its_releases_and_the_sources_pictures(self) -> None:
        kind = Kind("discovery_wheel", "word.wheel", records=[{}, {}], offers=[{}])

        self.assertEqual(3, kind.count)

    def test_the_table_kind_wears_the_tables_glyph_and_a_picture_kind_its_family(self) -> None:
        self.assertNotEqual(Kind("a", "w", listed_as=TABLE).glyph,
                            Kind("b", "w", media="wheel").glyph)

    def test_a_scene_compares_only_a_counted_library_that_has_the_game(self) -> None:
        tray = discovery_get.Tray()

        def scene(counted: bool, in_library: bool) -> Scene:
            return Scene(counted, in_library, "", "v1", tray, [], [], {}, None, [], 0, "t")

        self.assertTrue(scene(True, True).compare)
        self.assertFalse(scene(True, False).compare)
        self.assertFalse(scene(False, True).compare)

    def test_a_scene_finds_a_kind_among_the_files_and_the_media(self) -> None:
        tray = discovery_get.Tray()
        file, media = Kind("discovery_tablefiles", "w"), Kind("discovery_wheel", "w")
        scene = Scene(True, True, "g1", "v1", tray, [file], [media], {}, None, [], 0, "t")

        self.assertIs(file, scene.kind("discovery_tablefiles"))
        self.assertIs(media, scene.kind("discovery_wheel"))
        self.assertIsNone(scene.kind("discovery_nothing"))


class Ordering(unittest.TestCase):
    def _kind(self, **over: Any) -> Kind:
        records = [_release("old", "2024-01-01"), _release("new", "2026-01-01"),
                   _release("mid", "2025-01-01")]
        return Kind("k", "w", listed_as=TABLE, records=records, **over)

    def _ids(self, kind: Kind) -> list[str]:
        return [str(one["vps_file_id"]) for one in discovery_list._unheld(kind)]

    def test_releases_are_newest_first(self) -> None:
        self.assertEqual(["new", "mid", "old"], self._ids(self._kind()))

    def test_a_release_whose_page_was_opened_comes_first(self) -> None:
        kind = self._kind(opened={"old": {"vps_file_id": "old"}})

        self.assertEqual(["old", "new", "mid"], self._ids(kind))

    def test_a_release_the_game_holds_is_left_to_the_held_rows(self) -> None:
        kind = self._kind(mine=[{"vps_file_id": "new"}], opened={"new": {"vps_file_id": "new"}})

        self.assertEqual(["mid", "old"], self._ids(kind))


class HeldFamily(unittest.TestCase):
    def test_a_rule_sheet_with_pixels_is_drawn_as_a_picture(self) -> None:
        kind = Kind("k", "w", media="rule_sheet", detail={"format": "PNG", "width": 800})

        self.assertEqual("image", kind.held_family)

    def test_a_rule_sheet_that_is_a_pdf_stays_a_document(self) -> None:
        kind = Kind("k", "w", media="rule_sheet", detail={"format": "PDF", "width": None})

        self.assertEqual("doc", kind.held_family)

    def test_a_video_is_not_read_as_a_picture(self) -> None:
        kind = Kind("k", "w", media="topper_video", detail={"format": "MP4", "width": 1280})

        self.assertEqual("video", kind.held_family)


class Scenes(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        async def inline(callback, *args):
            return callback(*args)

        self.enterContext(mock.patch.object(offload, "io", inline))
        self.enterContext(mock.patch.object(discovery_kinds.opened_pages, "recent",
                                            mock.Mock(return_value=[])))
        client = mock.Mock()
        client.library_policy.return_value = {"hidden_asset_kinds": [],
                                              "hidden_media_kinds": []}
        library = Library(client)
        library.load_kept_kinds()
        self.library = cast(Any, library)
        self.releases: dict[str, Any] = {}
        self.sources: list[dict[str, Any]] = []
        self.offers: dict[str, Any] = {}
        self.library.catalog = mock.Mock(return_value={"counted": "2026-10-01T10:00:00Z"})
        self.library.owned = mock.Mock(return_value=({"v1": {"game_id": "g1"}}, {}))
        self.library.vps_held = mock.Mock(return_value=_held(
            tableFiles=[{"name": "a.vpx", "vps_file_id": "f1"}]))
        self.library.vps_releases = self._releases
        self.library.media_for = mock.Mock(return_value={"wheel": {"present": True}})
        self.library.media_detail = mock.Mock(return_value={"size": 10})
        self.library.media_sources = mock.Mock(return_value=[])
        self.library.online_sources = lambda: self.sources
        self.library.source_changes = mock.Mock(return_value={"version": 3})
        self.catalog: dict[str, list[dict[str, Any]]] = {}
        answer_from(self.library, lambda: [*self.library.media_sources(), *self.sources],
                    self._offers, lambda _vps_id, kind: self.catalog.get(kind, []))

    def _offers(self, _source: dict[str, Any], _kind: str, *_rest: Any) -> Any:
        return self.offers.get(_source["key"], {"rows": []})

    def _releases(self, _vps_id: str, listed_as: str) -> list[dict[str, Any]]:
        answer = self.releases.get(listed_as, [])
        if isinstance(answer, Exception):
            raise answer
        return answer

    async def _read(self, **entry: Any) -> Scene:
        row = discovery.rows({"counted": "2026-10-01T10:00:00Z",
                              "rows": [_entry(**entry)]})[0]
        context = {"library": self.library, "state": {}, "discovery": row}
        return await discovery_kinds.read(context)

    async def test_a_kind_vps_lists_that_the_game_lacks_is_missing_and_one_it_holds_is_not(
            self) -> None:
        self.releases = {TABLE: [_release("f1")], BACKGLASS: [_release("f2")]}

        scene = await self._read()

        by_key = {one.key: one for one in scene.files}
        table, backglass = by_key["discovery_tablefiles"], by_key["discovery_b2sfiles"]
        self.assertEqual((True, False), (table.held, table.missing))
        self.assertEqual((False, True), (backglass.held, backglass.missing))
        self.assertEqual(["f1"], [one["vps_file_id"] for one in table.mine])
        self.assertEqual(("have", "missing"), (table.standing, backglass.standing))

    async def test_a_kind_nothing_lists_and_the_game_does_not_hold_has_no_section(self) -> None:
        self.releases = {TABLE: [_release("f1")]}

        scene = await self._read()

        self.assertEqual(["discovery_tablefiles"], [one.key for one in scene.files])

    async def test_a_kind_the_library_does_not_keep_has_no_section(self) -> None:
        self.releases = {TABLE: [_release("f1")], ROM: [_release("f3")]}
        self.library.kept_kinds = mock.Mock(return_value={"asset": {TABLE, BACKGLASS},
                                                          "media": set()})
        self.enterContext(mock.patch.object(discovery, "kept",
                                            lambda _library, listed_as: listed_as != ROM))

        scene = await self._read()

        self.assertNotIn("discovery_romfiles", [one.key for one in scene.files])

    async def test_a_kind_that_could_not_be_read_keeps_its_section_and_says_why(self) -> None:
        self.releases = {BACKGLASS: RuntimeError("the catalog is down")}

        scene = await self._read()

        broken = scene.kind("discovery_b2sfiles")
        assert broken is not None
        self.assertTrue(broken.unread)
        self.assertEqual([], broken.records)

    async def test_a_game_not_in_the_library_is_missing_nothing(self) -> None:
        self.library.owned = mock.Mock(return_value=({}, {}))
        self.releases = {TABLE: [_release("f1")], BACKGLASS: [_release("f2")]}

        scene = await self._read(in_library=False, held=[])

        self.assertEqual("", scene.game_id)
        self.assertFalse(scene.compare)
        self.assertFalse(any(one.missing for one in (*scene.files, *scene.media)))
        self.assertEqual({""}, {one.standing for one in (*scene.files, *scene.media)})

    async def test_a_picture_the_game_has_is_held_and_one_it_lacks_is_missing(self) -> None:
        self.library.media_for = mock.Mock(return_value={"wheel": {"present": True},
                                                         "topper": {"present": False}})
        self.library.kept_kinds = mock.Mock(return_value={
            "asset": {TABLE, BACKGLASS, ROM}, "media": {"wheel", "topper"}})

        scene = await self._read()

        wheel, topper = scene.kind("discovery_wheel"), scene.kind("discovery_topper")
        assert wheel is not None and topper is not None
        self.assertEqual((True, False, {"size": 10}), (wheel.held, wheel.missing, wheel.detail))
        self.assertEqual((False, True), (topper.held, topper.missing))

    async def test_a_picture_nothing_lists_or_offers_is_blank_not_missing(self) -> None:
        self.library.media_for = mock.Mock(return_value={"wheel": {"present": True},
                                                         "topper": {"present": False}})
        self.library.kept_kinds = mock.Mock(return_value={
            "asset": {TABLE}, "media": {"wheel", "topper"}})

        scene = await self._read()

        wheel, topper = scene.kind("discovery_wheel"), scene.kind("discovery_topper")
        assert wheel is not None and topper is not None
        self.assertEqual(("have", "blank"), (wheel.standing, topper.standing))

    async def test_wheel_and_topper_come_first_among_the_media(self) -> None:
        self.library.kept_kinds = mock.Mock(return_value={
            "asset": {TABLE}, "media": {"playfield", "topper", "wheel"}})

        scene = await self._read()

        self.assertEqual(["discovery_wheel", "discovery_topper", "discovery_playfield"],
                         [one.key for one in scene.media])

    async def test_the_newest_release_a_source_can_get_is_offered_for_each_missing_kind(
            self) -> None:
        self.library.vps_held = mock.Mock(return_value=_held())
        self.sources = [{"id": "ext.files", "extension": "ext", "key": "files",
                         "name": "Example", "kinds": ["table"]}]
        self.offers = {"files": {"rows": [
            {"key": "old", "name": "old", "vps_file_id": "f1"},
            {"key": "new", "name": "new", "vps_file_id": "f2"}]}}
        self.releases = {TABLE: [_release("f1", "2026-01-01"), _release("f2", "2026-06-01")]}

        scene = await self._read(held=[])

        self.assertEqual(["discovery_tablefiles"], [kind.key for kind, _pick in scene.newest])
        self.assertEqual("new", scene.newest[0][1].row)

    async def test_a_file_the_game_already_holds_is_not_the_newest_to_get(self) -> None:
        self.sources = [{"id": "ext.files", "extension": "ext", "key": "files",
                         "name": "Example", "kinds": ["table"]}]
        self.offers = {"files": {"rows": [{"key": "a", "name": "a", "vps_file_id": "f1"}]}}
        self.releases = {TABLE: [_release("f1")]}

        scene = await self._read()

        self.assertEqual([], scene.newest)

    async def test_the_pages_opened_for_a_kind_are_read_by_release(self) -> None:
        self.releases = {TABLE: [_release("f1")]}
        discovery_kinds.opened_pages.recent.return_value = [  # type: ignore[attr-defined]
            {"vps_file_id": "f1", "at": "2026-10-18T10:00:00Z"}]

        scene = await self._read()

        table = scene.kind("discovery_tablefiles")
        assert table is not None
        self.assertEqual({"f1"}, set(table.opened))

    async def test_a_topper_vps_lists_is_held_by_its_video_as_well_as_its_picture(self) -> None:
        self.library.kept_kinds = mock.Mock(return_value={
            "asset": {TABLE}, "media": {"topper", "topper_video"}})
        self.library.media_for = mock.Mock(return_value={"topper": {"present": False},
                                                         "topper_video": {"present": True}})
        self.releases = {"topperFiles": [_release("t1")]}

        scene = await self._read(listed=[TABLE, "topperFiles"])

        topper, video = scene.kind("discovery_topper"), scene.kind("discovery_topper_video")
        assert topper is not None and video is not None
        self.assertEqual(("have", False), (topper.standing, topper.missing))
        self.assertEqual("have", video.standing)

    async def test_the_pages_opened_for_a_picture_kind_are_read_too(self) -> None:
        self.library.kept_kinds = mock.Mock(return_value={
            "asset": {TABLE}, "media": {"wheel", "topper"}})
        self.releases = {"wheelArtFiles": [_release("w1")]}
        discovery_kinds.opened_pages.recent.return_value = [  # type: ignore[attr-defined]
            {"vps_file_id": "w1", "at": "2026-10-18T10:00:00Z"}]

        scene = await self._read(listed=[TABLE, "wheelArtFiles"])

        wheel, topper = scene.kind("discovery_wheel"), scene.kind("discovery_topper")
        assert wheel is not None and topper is not None
        self.assertEqual({"w1"}, set(wheel.opened))
        self.assertEqual({}, topper.opened)

    async def test_a_rule_sheet_vps_lists_is_one_section_among_the_media(self) -> None:
        self.library.kept_kinds = mock.Mock(return_value={
            "asset": {TABLE}, "media": {"rule_sheet"}})
        self.library.media_for = mock.Mock(return_value={"rule_sheet": {"present": True}})
        self.releases = {"ruleFiles": [_release("r1")]}

        scene = await self._read(listed=[TABLE, "ruleFiles"])

        sheet = scene.kind("discovery_rule_sheet")
        assert sheet is not None
        self.assertEqual(("ruleFiles", "rule_sheet", "have"),
                         (sheet.listed_as, sheet.media, sheet.standing))
        self.assertEqual(["r1"], [one["vps_file_id"] for one in sheet.records])
        self.assertIsNone(scene.kind("discovery_rulefiles"))


    async def test_a_game_of_the_library_is_asked_of_every_source_in_one_call(self) -> None:
        self.library.media_sources = mock.Mock(return_value=[
            {"id": "vpinmediadb", "name": "DB", "enabled": True, "kinds": ["wheel"]}])
        self.sources = [{"id": "ext.files", "extension": "ext", "key": "files",
                         "name": "Example", "kinds": ["table"]}]
        self.catalog = {"wheel": [{"source": "vpinmediadb", "name": "w.png", "url": "u",
                                   "kind": "wheel", "size": ""}]}
        self.offers = {"files": {"rows": [{"key": "a", "name": "a", "vps_file_id": "f1"}]}}
        self.releases = {TABLE: [_release("f1")]}

        scene = await self._read()

        self.library.game_offers.assert_called_once()
        game_id, vps_id, _name, kinds = self.library.game_offers.call_args.args
        self.assertEqual(("g1", "v1"), (game_id, vps_id))
        self.assertEqual({"table", "wheel"}, set(kinds))
        wheel = scene.kind("discovery_wheel")
        assert wheel is not None
        self.assertEqual(["w.png"], [one["name"] for one in wheel.offers])
        self.assertEqual(["ext.files:table:a"],
                         [one.pick.key for one in scene.tray.offered.values()])

    async def test_a_catalog_that_cannot_answer_says_why(self) -> None:
        self.library.media_sources = mock.Mock(return_value=[
            {"id": "vpinmediadb", "name": "DB", "enabled": True, "kinds": ["wheel"]}])
        self.library.game_offers = mock.Mock(return_value={"sources": [
            {"id": "vpinmediadb", "name": "DB", "extension": "", "key": "", "kinds": [
                {"kind": "wheel", "offers": [], "rows": [], "note": "", "link": None,
                 "error": "that source is down"}]}]})

        scene = await self._read()

        self.assertEqual("that source is down", str(scene.failed))
        self.assertEqual([], [one for one in scene.media if one.offers])

    async def test_nothing_is_read_when_the_call_itself_fails(self) -> None:
        self.library.game_offers = mock.Mock(side_effect=RuntimeError("no answer"))
        self.sources = [{"id": "ext.files", "extension": "ext", "key": "files",
                         "name": "Example", "kinds": ["table"]}]

        with self.assertLogs("vpinfe.manager.library.discovery_get", "WARNING"):
            scene = await self._read()

        self.assertEqual("no answer", str(scene.failed))
        self.assertEqual({}, scene.tray.offered)

    async def test_an_entry_the_library_does_not_hold_is_asked_of_each_source(self) -> None:
        self.library.owned = mock.Mock(return_value=({}, {}))
        self.library.source_offers = mock.Mock(return_value={"rows": []})
        self.sources = [{"id": "ext.files", "extension": "ext", "key": "files",
                         "name": "Example", "kinds": ["table"]}]

        await self._read()

        self.library.game_offers.assert_not_called()
        self.library.source_offers.assert_called_once()


    async def test_nothing_wanted_of_any_source_makes_no_call(self) -> None:
        await self._read()

        self.library.game_offers.assert_not_called()


if __name__ == "__main__":
    unittest.main()
