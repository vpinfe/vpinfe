"""The Library pages of Settings that list switches: Checks, Discovery and Media."""

from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from common.settings import config_service
from manager.kit import panel
from manager.settings import sections, settings, shelves
from tests.support.regions import Immediate

MARKERS = {panel.HEADING: "heading", panel.LEDE: "lede", panel.ASIDE: "note",
           panel.SUB: "sub", panel.RULE: "rule", panel.FULL: "full"}

VPINMEDIADB = {"id": "vpinmediadb", "name": "VPinMediaDB"}
EXTENSION = {"id": "ext.files", "name": "Example Files"}


def _outline(entries: list[tuple[Any, ...]]) -> list[str]:
    """What a page draws, top to bottom: a heading by its words, a switch or setting by
    its label, and any other kind of row by what kind it is."""
    outline = []
    for entry in entries:
        label, value = entry[0], entry[1]
        if label is panel.HEADING:
            outline.append(str(value))
        elif label is panel.SUB:
            outline.append(f"sub {value[0]}")
        elif label in MARKERS:
            outline.append(MARKERS[label])
        else:
            outline.append(str(label))
    return outline


def _library(policy: dict[str, list[str]] | None = None, *known: dict) -> MagicMock:
    library = MagicMock()
    library.library_policy.return_value = policy or {}
    library.media_sources.return_value = list(known)
    library.vps_sync_state.return_value = {"checked": ""}
    schema = config_service.schema()["sections"]
    library.config_schema.return_value = [{**block, "writable": True} for block in schema]
    library.config_values.return_value = {}
    library.config_path_checks.return_value = {}
    return library


class _Offloaded(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.enterContext(patch.object(
            settings.offload, "io",
            new=AsyncMock(side_effect=lambda call, *args, **kwargs: call(*args, **kwargs))))
        self.enterContext(patch.object(
            settings.run, "io_bound",
            new=AsyncMock(side_effect=lambda call, *args, **kwargs: call(*args, **kwargs))))
        self.enterContext(patch("manager.kit.on_page.ui"))
        self.enterContext(patch.object(settings, "ui"))
        self.facts = self.enterContext(patch.object(panel, "facts"))

    @property
    def drawn(self) -> list[tuple[Any, ...]]:
        return list(self.facts.call_args.args[1])


class TheChecksPage(_Offloaded):
    HEADINGS = ["Game Folders", "Matching", "Media", "Tables"]
    UNDER_EACH = [
        ["Unreadable Folders", "Games With No Tables", "Games on an Older Format"],
        ["Unmatched Games", "Tables With No VPS Release Matched", "Games With No Year"],
        ["Games With No Playfield", "Games With No Backglass", "Games With No Media"],
        ["Missing ROMs"],
    ]

    async def test_the_rows_are_drawn_under_four_headings_in_that_order(self) -> None:
        entries = await settings._kind_rows(_library(), None, "library_checks")

        expected: list[str] = []
        for heading, rows in zip(self.HEADINGS, self.UNDER_EACH, strict=True):
            expected += [heading, *rows]
        self.assertEqual(expected, _outline(entries))

    async def test_the_note_is_the_page_s_and_not_a_line_above_the_first_heading(self) -> None:
        self.assertEqual("manager.settings.checks_note", settings.PAGE_NOTES["library.checks"])
        self.assertEqual("", settings.KIND_PAGES["library_checks"][0])

    async def test_a_switch_off_is_drawn_off_and_the_rest_on(self) -> None:
        library = _library({"hidden_checks": ["no_year"]})
        with patch.object(panel, "switch") as switch:
            await settings._kind_rows(library, None, "library_checks")

        shown = {call.args[0] for call in switch.call_args_list}
        self.assertEqual({True, False}, shown)
        self.assertEqual(1, [call.args[0] for call in switch.call_args_list].count(False))

    async def test_every_check_a_row_on_overview_can_come_from_has_a_switch(self) -> None:
        listed = {key for key, _name, _why, _predicate in sections.CHECKS}

        self.assertLessEqual(listed, set(settings._check_labels()))
        self.assertEqual({"unreadable", "no_tables", "older_format", "unmatched", "untied",
                          "no_year", "no_playfield", "no_backglass", "no_media", "rom_missing"},
                         set(settings._check_labels()))

    async def test_the_page_is_drawn_from_the_one_table_that_names_the_checks(self) -> None:
        declared = [key for _heading, checks in settings.CHECK_GROUPS for key, _label in checks]

        self.assertEqual(declared, list(settings._check_labels()))

    async def test_the_page_is_named_checks(self) -> None:
        self.assertEqual("Checks", settings._page_label("library.checks"))

    async def test_no_row_has_a_help_line(self) -> None:
        entries = await settings._kind_rows(_library(), None, "library_checks")

        self.assertNotIn("note", _outline(entries))

    async def test_the_switches_write_hidden_checks(self) -> None:
        library = _library()
        with patch.object(panel, "switch") as switch:
            await settings._kind_rows(library, None, "library_checks")
        [(_, on_change), *_] = [call.args for call in switch.call_args_list]

        await on_change(MagicMock(value=False))

        library.put_library_policy.assert_called_once_with({"hidden_checks": ["unreadable"]})


class TheDiscoveryPage(_Offloaded):
    def setUp(self) -> None:
        super().setUp()
        self.refilled = MagicMock()
        self.enterContext(patch.object(
            panel, "region",
            new=lambda build, swapped=None: Immediate(build, swapped, on_refill=self.refilled)))
        self.enterContext(patch.object(panel, "Region", new=Immediate))

    async def _page(self, library: MagicMock, redraw: Any = None) -> panel.Region:
        """The page as the Manager draws it: handed back as a region, then drawn."""
        region = await settings._discovery_page(library, redraw or MagicMock(), {})
        await region.refilled()
        return region

    async def _draw(self, *known: dict, library: MagicMock | None = None) -> list[str]:
        await self._page(library or _library({}, *known))
        return _outline(self.drawn)

    async def test_the_spreadsheet_comes_first_then_the_time_it_was_checked_then_the_sources(
            self) -> None:
        shown = await self._draw(EXTENSION, VPINMEDIADB)

        self.assertEqual(["Updates", "Show Update Count", "note", "Home Shelves", "lede", "full",
                          "Sources", "lede"], shown[:8])
        self.assertEqual("Virtual Pinball Spreadsheet", shown[8])
        at = shown.index("sub Last Checked")
        self.assertGreater(at, 8)
        self.assertEqual("rule", shown[at + 1])

    async def test_the_shelves_are_drawn_from_the_values_the_page_read(self) -> None:
        library = _library()
        library.config_values.return_value = {"manager": {
            "hidden_discovery_shelves": ["new"]}}
        state: dict[str, Any] = {}

        region = await settings._discovery_page(library, MagicMock(), state)
        await region.refilled()

        block = state[shelves.KEY]
        self.assertEqual(["new"], block.off)
        library.manager_policy.assert_not_called()

    async def test_the_catalog_is_first_with_a_line_under_it_and_a_rule_between_sources(
            self) -> None:
        shown = await self._draw(EXTENSION, VPINMEDIADB)

        sources = shown[shown.index("sub Last Checked") + 2:]
        self.assertEqual(["VPinMediaDB", "note", "rule", "Example Files"], sources)

    async def test_one_source_has_no_rule_after_it(self) -> None:
        shown = await self._draw(VPINMEDIADB)

        self.assertEqual(["VPinMediaDB", "note"], shown[shown.index("sub Last Checked") + 2:])

    async def test_the_catalog_line_is_the_words_for_it(self) -> None:
        await self._page(_library({}, VPINMEDIADB))
        entries = self.drawn
        [catalog] = [at for at, entry in enumerate(entries) if entry[0] == "VPinMediaDB"]

        self.assertIs(panel.ASIDE, entries[catalog + 1][0])
        with patch.object(panel, "line") as said:
            entries[catalog + 1][1]()
        self.assertEqual("Pictures and videos for matched games", said.call_args.args[0])

    async def test_sources_that_cannot_be_read_are_said_and_the_spreadsheet_stays(self) -> None:
        library = _library({}, VPINMEDIADB)
        library.media_sources.side_effect = OSError("down")

        shown = await self._draw(library=library)

        self.assertIn("Virtual Pinball Spreadsheet", shown)
        self.assertIn("sub Last Checked", shown)
        self.assertEqual("full", shown[-1])

    async def test_the_sources_switches_write_hidden_sources(self) -> None:
        library = _library({}, VPINMEDIADB)
        with patch.object(panel, "switch") as switch:
            await self._page(library)
        [*_, (_, on_change)] = [call.args for call in switch.call_args_list]

        await on_change(MagicMock(value=False))

        library.put_library_policy.assert_called_once_with({"hidden_sources": ["vpinmediadb"]})

    async def test_the_update_count_switch_is_on_and_counts_again_once_it_is_written(
            self) -> None:
        library = _library({}, VPINMEDIADB)
        recount = AsyncMock()
        with patch.object(panel, "switch") as switch:
            region = await settings._discovery_page(
                library, MagicMock(), {"recount_updates": recount})
            await region.refilled()
        [(value, on_change), *_] = [call.args for call in switch.call_args_list]

        self.assertTrue(value)
        recount.assert_not_awaited()
        await on_change(MagicMock(value=False))

        library.put_config.assert_called_once_with({"updates": {"show_update_count": False}})
        recount.assert_awaited_once()

    async def test_a_count_setting_that_was_refused_does_not_count_again(self) -> None:
        library = _library({}, VPINMEDIADB)
        library.put_config.side_effect = OSError("refused")
        recount = AsyncMock()
        with patch.object(panel, "switch") as switch, patch.object(settings.ui, "notify"):
            region = await settings._discovery_page(
                library, MagicMock(), {"recount_updates": recount})
            await region.refilled()
        [(_, on_change), *_] = [call.args for call in switch.call_args_list]

        await on_change(MagicMock(value=False))

        recount.assert_not_awaited()

    async def test_the_sync_state_failing_is_said_in_place_of_the_time(self) -> None:
        library = _library({}, VPINMEDIADB)
        library.vps_sync_state.side_effect = OSError("down")

        shown = await self._draw(library=library)

        self.assertNotIn("sub Last Checked", shown)
        self.assertIn("full", shown)
        self.assertIn("VPinMediaDB", shown)

    async def test_check_now_draws_the_page_again_in_place_and_not_the_whole_settings_page(
            self) -> None:
        library = _library({}, VPINMEDIADB)
        library.sync_vps.return_value = {"changed": False}
        redraw = MagicMock()
        await self._page(library, redraw)
        [(_, checked)] = [entry[1] for entry in self.drawn if entry[0] is panel.SUB]

        with patch.object(panel, "action") as action:
            checked()
        [(_, now)] = [call.args[:2] for call in action.call_args_list]
        await now()

        library.sync_vps.assert_called_once_with()
        self.refilled.assert_called_once_with()
        redraw.assert_not_called()

    async def test_the_settings_page_draws_the_region_a_built_page_hands_back(self) -> None:
        library = _library({}, VPINMEDIADB)
        body = MagicMock()
        page = (settings.DISCOVERY_PAGE, "manager.settings.page_discovery",
                settings.BUILT_PAGE, (), "library")

        with patch.object(settings, "page_head") as head:
            await settings._draw_system_page(library, MagicMock(), body, page, {}, [])

        head.assert_called_once_with(settings.DISCOVERY_PAGE)
        self.assertIn("sub Last Checked", _outline(self.drawn))

    async def test_a_built_page_that_hands_back_a_plain_draw_is_drawn_as_it_was(self) -> None:
        drawn = MagicMock()

        async def plain(*_: Any) -> Any:
            return drawn

        page = ("test.plain", "manager.settings.page_discovery", settings.BUILT_PAGE, (),
                "library")
        with patch.dict(settings.PAGES, {"test.plain": plain}), \
                patch.object(settings, "page_head"):
            await settings._draw_system_page(_library(), MagicMock(), MagicMock(), page, {}, [])

        drawn.assert_called_once_with()


class TheMediaPage(_Offloaded):
    async def _headings(self) -> list[str]:
        library = _library()
        with patch.object(settings, "_tools_found", new=AsyncMock(return_value=[])), \
                patch.object(settings, "_browser_found", new=AsyncMock(return_value=None)), \
                patch.object(settings, "_play_input", new=AsyncMock(return_value=None)), \
                patch.object(settings, "_suggestions", new=AsyncMock(return_value={})), \
                patch.object(settings, "_waiting", new=AsyncMock(return_value={})), \
                patch.object(settings.binding_table, "opened_elsewhere", return_value=False), \
                patch.object(settings, "page_head"):
            await settings._draw_schema_page(
                library, "library.media", ("media",), [], None, lambda: None, AsyncMock())
        return [str(entry[1]) for entry in self.drawn if entry[0] is panel.HEADING]

    async def test_the_groups_are_playfield_media_then_kinds_then_wheels(self) -> None:
        self.assertEqual(["Playfield Media", "Kinds", "Wheels"], await self._headings())

    async def test_the_online_sources_are_not_on_this_page(self) -> None:
        self.assertEqual((("media_kinds", "manager.settings.heading_kinds", "Wheels"),),
                         settings.PAGE_KINDS["library.media"])


class TheAutomaticPage(unittest.TestCase):
    def test_the_options_are_drawn_in_the_order_the_schema_declares_them(self) -> None:
        schema = settings.settings_here(config_service.schema()["sections"])
        [updates] = [block for block in schema if block["name"] == "updates"]

        self.assertEqual(["refresh_minutes", "match_new_games", "update_game_details",
                          "get_media_for_new_games", "update_downloaded_media"],
                         [one["key"] for one in updates["options"]])

    def test_the_page_carries_no_last_checked_foot(self) -> None:
        self.assertNotIn("updates", settings.FOOTERS)

    def test_the_spreadsheet_schedule_is_left_to_the_discovery_page(self) -> None:
        schema = settings.settings_here(config_service.schema()["sections"])
        [updates] = [block for block in schema if block["name"] == "updates"]

        self.assertNotIn("download_spreadsheet", [one["key"] for one in updates["options"]])


class TheThumbnailCache(unittest.TestCase):
    def test_it_is_with_the_other_thumbnail_settings_on_the_manager_page(self) -> None:
        schema = settings.settings_here(config_service.schema()["sections"])
        [manager] = [block for block in schema if block["name"] == "manager"]
        [media] = [block for block in schema if block["name"] == "media"]

        self.assertIn("thumb_cache_max_mb", [one["key"] for one in manager["options"]])
        self.assertNotIn("thumb_cache_max_mb", [one["key"] for one in media["options"]])


if __name__ == "__main__":
    unittest.main()
