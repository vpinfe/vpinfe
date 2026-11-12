"""The Online tab of the file picker: what it lists for each kind, what a pick does, and how
the pick travels with the file."""

from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

from nicegui import ui

from common.i18n import t
from manager.kit import kind_words
from manager.kit.kind_words import in_a_sentence
from manager.library import import_dialog, picker_online
from tests.support.game_offers import answer_from

_PAGE = ui.element()


async def _now(callback: Any, *args: Any, **kwargs: Any) -> Any:
    return callback(*args, **kwargs)


def _texts(element: ui.element) -> list[str]:
    return [str(one.text) for one in element.descendants()
            if isinstance(one, (ui.label, ui.button)) and one.text]


def _record(file_id: str, version: str, author: str = "Ann", url: str = "") -> dict[str, Any]:
    return {"vps_file_id": file_id, "version": version, "authors": [author], "url": url,
            "updated_at": f"2026-0{int(version[0])}-01T00:00:00Z"}


class _Entry:
    """What a picker's `entry` hands back: a row binds to it and a press picks it."""

    def __init__(self, card: Any, commit: Any) -> None:
        self.card = card
        self.commit = commit

    def bind(self, row: Any) -> Any:
        return row

    def pick(self) -> None:
        pass

    async def use(self) -> None:
        pass


def _press(row: ui.element) -> None:
    """The click a person makes on `row`."""
    for listener in row._event_listeners.values():
        if listener.type == "click":
            handler = listener.handler
            assert handler is not None
            handler()


def _row_named(body: ui.element, text: str) -> ui.element:
    return next(row for row in body.descendants()
                if isinstance(row, ui.row) and "manager-source-row--pick" in row.classes
                and text in _texts(row))


class _Host:
    """What a picker gives its Online tab."""

    def __init__(self, library: Mock, game: dict[str, Any], kind: str = "rom",
                 media: bool = False) -> None:
        self.library = library
        self.context = {"game": game, "library": library, "rebuild": AsyncMock()}
        self.game_id = "g-1"
        self.kind = kind
        self.source_kind = kind
        self.listing = picker_online.listing_for(kind, media)
        self.built_in = False
        self.glyph = ""
        self.family = ""
        self.trips: list[Any] = []
        self.took: list[tuple[Any, ...]] = []

    @property
    def destination(self) -> str:
        return ""

    def shown(self) -> bool:
        return True

    def entry(self, card: Any, commit: Any, *, discard: Any = None,
              filename: str = "", arrives: bool = False) -> _Entry:
        return _Entry(card, commit)

    def forget_trip(self) -> None:
        self.trips.clear()

    def choose(self, entry: Any) -> None:
        self.took.append(("choose", entry))

    def choose_trip(self, trip: Any, row: Any) -> None:
        self.trips.append(trip)

    async def finish_plain(self, message: str) -> None:
        self.took.append(("finish", message))

    async def take_offer(self, offer: dict[str, Any], source_name: str, vps_id: str) -> None:
        self.took.append(("offer", offer.get("name"), vps_id))


def _library(records: list[dict[str, Any]], held: dict[str, Any] | None = None) -> Mock:
    library = Mock()
    library.media_sources.return_value = []
    library.vps_entry.return_value = {"name": "Own Game", "manufacturer": "Bally", "year": 1990}
    library.vps_releases.return_value = records
    library.vps_held.return_value = held
    library.tables_for.return_value = []
    library.media_for.return_value = {}
    library.picture_changes.return_value = []
    library.config_values.return_value = {}
    library.games = []
    answer_from(library, library.media_sources, lambda *_: {}, lambda *_: [])
    return library


def _game(vps_id: str = "own", **more: Any) -> dict[str, Any]:
    return {"id": "g-1", "name": "Own Game", "vps_id": vps_id, **more}


class WhatEachKindOffers(unittest.TestCase):
    """One cell per kind: the lists VPS and VPinMediaDB hold for it."""

    FILES = {
        "rom": ("romFiles", ""), "pup_pack": ("pupPackFiles", ""),
        "alt_color": ("altColorFiles", ""), "alt_sound": ("altSoundFiles", ""),
        "table": ("tableFiles", ""), "backglass": ("b2sFiles", ""), "pov": ("povFiles", ""),
        "music": ("", ""), "serum": ("", ""), "vni": ("", ""), "readme": ("", ""),
    }
    MEDIA = {
        "wheel": ("wheelArtFiles", "wheel"), "playfield": ("", "playfield"),
        "topper": ("topperFiles", ""), "rule_sheet": ("ruleFiles", ""),
        "backglass": ("", "backglass"), "scoreview": ("", "scoreview"),
        "flyer": ("", "flyer"), "cab": ("", "cab"), "audio": ("", "audio"),
        "real_dmd": ("", "real_dmd"), "playfield_video": ("", "playfield_video"),
    }
    NOTHING_ONLINE = ("logo", "instruction_card", "loading", "audio_launch",
                      "backglass_video", "real_dmd_video", "real_dmd_color_video",
                      "full_dmd", "full_dmd_video")

    def test_each_file_kind_is_listed_where_the_catalogs_list_it(self) -> None:
        for kind, (listed, manifest) in self.FILES.items():
            with self.subTest(kind=kind):
                found = picker_online.listing_for(kind, False)
                self.assertEqual((listed, manifest), (found.listed_as, found.manifest))
                self.assertEqual(bool(listed), found.offered())

    def test_each_media_kind_is_listed_where_the_catalogs_list_it(self) -> None:
        for kind, (listed, manifest) in self.MEDIA.items():
            with self.subTest(kind=kind):
                found = picker_online.listing_for(kind, True)
                self.assertEqual((listed, manifest), (found.listed_as, found.manifest))
                self.assertTrue(found.offered())

    def test_a_media_kind_neither_catalog_carries_has_no_online_tab(self) -> None:
        for kind in self.NOTHING_ONLINE:
            with self.subTest(kind=kind):
                self.assertFalse(picker_online.listing_for(kind, True).offered())


class TheOnlineTab(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        patched = patch("manager.kit.offload.run.io_bound", new=_now)
        patched.start()
        self.addCleanup(patched.stop)

    async def _drawn(self, host: _Host) -> tuple[picker_online.Online, ui.element]:
        view = picker_online.Online(host)
        with _PAGE:
            body = ui.column()
        await view.draw(body)
        return view, body

    async def test_an_unmatched_game_says_so_and_the_list_waits(self) -> None:
        game = _game("", discovered={"vps_id": "guess"})
        library = _library([_record("f1", "1.0")])
        library.vps_entry.return_value = {"name": "Guessed Game", "manufacturer": "Bally",
                                          "year": 1990}
        host = _Host(library, game)
        _, body = await self._drawn(host)
        said = _texts(body)

        self.assertIn(t("manager.workbench.not_matched"), said)
        self.assertIn("Guessed Game", said)
        self.assertIn(t("manager.picker.match"), said)
        self.assertIn(t("manager.picker.waits_vps", kinds=in_a_sentence("ROMs")), said)
        self.assertFalse([one for one in said if "1.0" in one])
        self.assertFalse([one for one in said if "another game" in one.lower()])

    async def test_the_files_are_one_list_and_the_one_the_game_has_says_so(self) -> None:
        held = {"kinds": [{"kind": "romFiles", "files": [
            {"name": "sttng_l7", "vps_file_id": "f1"}]}]}
        library = _library([_record("f1", "1.0"), _record("f2", "2.0", "Bea")], held)
        _, body = await self._drawn(_Host(library, _library_game(library)))
        used = t("manager.mediasource.used_as", kind=kind_words.label("rom", False))

        first = _row_named(body, "1.0 · Ann")
        second = _row_named(body, "2.0 · Bea")
        self.assertIn(used, _texts(first))
        self.assertNotIn(used, _texts(second))
        self.assertEqual(1, [one for one in _texts(body)].count("1.0 · Ann"))

    async def test_the_word_release_is_nowhere_on_the_tab(self) -> None:
        library = _library([_record("f1", "1.0")])
        view, body = await self._drawn(_Host(library, _library_game(library)))
        view.choose(_record("f1", "1.0"))
        for one in _texts(body):
            self.assertNotIn("release", one.lower())

    async def test_a_press_picks_the_file_and_sends_the_person_to_its_page(self) -> None:
        record = _record("f1", "1.0", "Ann", url="https://example.org/page")
        library = _library([record])
        host = _Host(library, _library_game(library))
        view, body = await self._drawn(host)
        self.assertIsNone(view.pick)

        _press(_row_named(body, "1.0 · Ann"))

        self.assertEqual("f1", view.pick.file_id if view.pick else "")
        self.assertEqual(1, len(host.trips))
        self.assertEqual("https://example.org/page", host.trips[0].to)

    async def test_a_file_with_no_page_is_picked_and_sends_nowhere(self) -> None:
        library = _library([_record("f1", "1.0", "Ann")])
        host = _Host(library, _library_game(library))
        view, body = await self._drawn(host)
        row = _row_named(body, "1.0 · Ann")
        self.assertIn(t("manager.picker.no_page"), _texts(row))

        _press(row)

        self.assertEqual("f1", view.pick.file_id if view.pick else "")
        self.assertEqual([None], host.trips)

    async def test_clear_takes_the_pick_back(self) -> None:
        library = _library([_record("f1", "1.0")])
        view, _ = await self._drawn(_Host(library, _library_game(library)))
        view.choose(_record("f1", "1.0"))
        view.clear()

        self.assertIsNone(view.pick)

    async def test_opening_a_page_is_noted_for_the_file(self) -> None:
        record = _record("f1", "1.0", url="https://example.org/page")
        library = _library([record])
        view, _ = await self._drawn(_Host(library, _library_game(library)))
        with patch.object(picker_online.opened_pages, "note") as note, \
                patch("manager.library.picker_online.run.io_bound", new=_now), _PAGE:
            await view._opened(record)

        note.assert_called_once()
        self.assertEqual(("own", "romFiles", "f1"), note.call_args.args[0:3])

    async def test_a_file_dropped_before_any_pick_is_matched_to_nothing(self) -> None:
        library = _library([_record("f1", "1.0")])
        view, _ = await self._drawn(_Host(library, _library_game(library)))
        match = view.release("/x/file.zip")

        self.assertEqual("", match.vps_file_id)
        self.assertEqual("VPS ROM", match.vps)

    async def test_a_file_dropped_after_a_pick_carries_it_and_says_why(self) -> None:
        library = _library([_record("f1", "1.0", "Ann")])
        view, _ = await self._drawn(_Host(library, _library_game(library)))
        view.choose(_record("f1", "1.0", "Ann"))
        match = view.release("/x/file.zip")

        self.assertEqual(("f1", "1.0 · Ann"), (match.vps_file_id, match.label))
        self.assertEqual(t("manager.import_dialog.reason_picked", vps="VPS ROM"), match.reason)

    async def test_a_table_a_launcher_cannot_play_is_left_off(self) -> None:
        library = _library([{**_record("f1", "1.0"), "format": "VPX"},
                            {**_record("f2", "2.0"), "format": "FP"}])
        library.tables_for.return_value = [{"vps_formats": ["VPX"]}]
        host = _Host(library, _library_game(library), kind="table")
        _, body = await self._drawn(host)
        said = _texts(body)

        self.assertTrue([one for one in said if one.startswith("1.0")])
        self.assertFalse([one for one in said if one.startswith("2.0")])


def _library_game(library: Mock) -> dict[str, Any]:
    game = _game()
    library.games = [game]
    return game


class WhatTravelsWithTheFile(unittest.IsolatedAsyncioTestCase):
    """After an import, the VPS file picked is recorded on what the import brought."""

    async def asyncSetUp(self) -> None:
        patched = patch("manager.kit.offload.run.io_bound", new=_now)
        patched.start()
        self.addCleanup(patched.stop)
        self.library = Mock()
        self.dir = "/games/Own Game"

    def _match(self, file_id: str = "f1") -> import_dialog.ReleaseMatch:
        return import_dialog.ReleaseMatch(AsyncMock(), "ROM", "VPS ROM", vps_file_id=file_id)

    async def _after(self, report: dict[str, Any], plan: dict[str, Any],
                     file_id: str = "f1", kind: str = "rom") -> None:
        after = picker_online.matched_after(self.library, "g-1", self.dir,
                                            picker_online.listing_for(kind, False),
                                            self._match(file_id))
        with patch("manager.library.picker_online.run.io_bound", new=_now), _PAGE:
            await after(report, plan)

    async def test_nothing_is_recorded_without_a_pick(self) -> None:
        await self._after({"imported": ["rom"]}, {"items": []}, file_id="")

        self.library.set_asset_source.assert_not_called()
        self.library.set_table_source.assert_not_called()

    async def test_a_rom_is_matched_by_its_path(self) -> None:
        plan = {"items": [{"kind": "rom", "action": "zip_rom",
                           "destination": f"{self.dir}/roms/sttng_l7.zip"}]}
        await self._after({"imported": ["rom"]}, plan)

        self.library.set_asset_source.assert_called_once_with(
            "g-1", "roms/sttng_l7.zip", "f1")

    async def test_a_folder_kind_is_matched_by_the_folders_name(self) -> None:
        for kind, folder, action, key in (
                ("pup_pack", "PUPVideos", "extract_tree", "pupvideos"),
                ("alt_color", "serum", "copy", "serum"),
                ("alt_color", "vni", "copy", "vni")):
            self.library.reset_mock()
            with self.subTest(folder=folder):
                found = {"pup_pack": "pup_pack", "alt_color": "altcolor_serum"}[kind]
                plan = {"items": [{"kind": found, "action": action,
                                   "destination": f"{self.dir}/{folder}/pack/file.bin"}]}
                await self._after({"imported": [found]}, plan, kind=kind)
                self.library.set_asset_source.assert_called_once_with("g-1", key, "f1")

    async def test_an_altsound_is_matched_by_the_folder_it_is_read_from(self) -> None:
        plan = {"items": [{"kind": "altsound", "action": "extract_tree",
                           "destination": f"{self.dir}/altsound/rom/sound.ogg"}]}
        with patch("manager.library.picker_online.asset_registry.altsound_folder",
                   return_value="pinmame/altsound"), \
                patch("manager.library.picker_online.asset_resolver.folder_listing",
                      return_value=([], [])):
            await self._after({"imported": ["altsound"]}, plan, kind="alt_sound")

        self.library.set_asset_source.assert_called_once_with("g-1", "pinmame/altsound", "f1")

    async def test_only_what_the_picker_is_for_is_matched(self) -> None:
        plan = {"items": [
            {"kind": "backglass", "action": "replace_b2s",
             "destination": f"{self.dir}/x.directb2s"},
            {"kind": "rom", "action": "zip_rom", "destination": f"{self.dir}/roms/x.zip"},
            {"kind": "pup_pack", "action": "extract_tree",
             "destination": f"{self.dir}/pupvideos/x/a.mp4"}]}
        await self._after({"imported": ["backglass", "rom", "pup_pack"],
                           "added_tables": ["t-9"]}, plan, kind="rom")

        self.library.set_asset_source.assert_called_once_with("g-1", "roms/x.zip", "f1")
        self.library.set_table_source.assert_not_called()

    async def test_a_table_is_matched_by_its_id(self) -> None:
        await self._after({"imported": ["table"], "added_tables": ["t-9"],
                           "replaced_tables": [{"table": {"id": "t-3"}}]}, {"items": []},
                          kind="table")

        self.assertEqual(["t-9", "t-3"], [call.args[1] for call in
                                           self.library.set_table_source.call_args_list])

    async def test_a_file_the_import_did_not_bring_is_not_matched(self) -> None:
        plan = {"items": [{"kind": "rom", "action": "zip_rom",
                           "destination": f"{self.dir}/roms/other.zip"}]}
        await self._after({"imported": []}, plan)

        self.library.set_asset_source.assert_not_called()

    async def test_a_destination_outside_the_game_folder_is_left_alone(self) -> None:
        plan = {"items": [{"kind": "rom", "action": "zip_rom",
                           "destination": "/elsewhere/roms/x.zip"}]}
        await self._after({"imported": ["rom"]}, plan)

        self.library.set_asset_source.assert_not_called()

    async def test_a_failed_match_is_said_and_the_import_stands(self) -> None:
        self.library.set_table_source.side_effect = RuntimeError("no")
        with patch("manager.library.picker_online.ui.notify") as notify:
            await self._after({"imported": ["table"], "added_tables": ["t-9"]}, {"items": []},
                              kind="table")

        notify.assert_called_once()
        self.assertEqual(t("manager.import_dialog.match_failed"), notify.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
