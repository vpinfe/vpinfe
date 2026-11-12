"""The Online tab with an extension's source in it: its rows, its failures, its words."""

from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

from nicegui import ui

from common.i18n import t
from manager.kit.data import Library
from manager.library import mediasource, picker_online
from tests.support.game_offers import answer_from

_PAGE = ui.element()

BUILT_IN = {"id": "vpinmediadb", "name": "VPinMediaDB", "url": "https://media.example",
            "enabled": True, "kinds": ["wheel", "pup_pack"]}
FILES = {"id": "ext.files", "name": "Example Files", "url": "", "enabled": True,
         "kinds": ["wheel", "pup_pack"], "extension": "ext", "key": "files"}
ELSEWHERE = {"id": "ext.other", "name": "Other Place", "url": "", "enabled": True,
             "kinds": ["topper"], "extension": "ext", "key": "other"}

ROWS = {
    "rows": [{"key": "a", "name": "Wheel A", "site": "example.com",
              "page": "https://example.com/a", "note": "30 MB", "tag": "FSS", "picture": "",
              "current": False},
             {"key": "b", "name": "Wheel B", "site": "", "page": "", "note": "", "tag": "",
              "picture": "", "current": True}],
    "note": "More on the site",
    "link": {"text": "Open the site", "to": "https://example.com/"}}


async def _now(callback: Any, *args: Any, **kwargs: Any) -> Any:
    return callback(*args, **kwargs)


def _library(*known: dict, answer: Any = ROWS, offers: list[dict] | None = None) -> Mock:
    library = Mock()
    library.media_sources.return_value = list(known)
    library.media_offers.return_value = offers or []
    library.source_offers.return_value = answer
    answer_from(library, library.media_sources,
                lambda source, kind, game_id, vps_id, name: library.source_offers(
                    source["extension"], source["key"], kind, game_id, vps_id, name),
                lambda _vps_id, _kind: library.media_offers.return_value)
    library.offers_online.return_value = True
    library.extensions.return_value = [{"name": "ext"}]
    library.vps_entry.return_value = {"name": "Own Game"}
    library.vps_releases.return_value = []
    library.vps_held.return_value = None
    library.media_for.return_value = {}
    library.picture_changes.return_value = []
    library.config_values.return_value = {}
    library.tables_for.return_value = []
    library.games = []
    return library


def _context(library: Mock) -> dict[str, Any]:
    return {"library": library, "game_id": "g-1", "lens": None,
            "game": {"id": "g-1", "name": "Own Game", "vps_id": "own",
                     "folder": "/games/Own"}}


def _slot(library: Mock, built_in: bool = True, kind: str = "wheel",
          asset: bool = False, target: Mock | None = None) -> mediasource._Slot:
    return mediasource._Slot(_context(library), kind, "Wheel", Mock(),
                             target or Mock(online=built_in, file=asset))


def _seen(element: ui.element) -> list[str]:
    return [one.text for one in element.descendants()
            if isinstance(one, (ui.label, ui.button)) and one.text]


def _links(element: ui.element) -> dict[str, str]:
    """Where each link out goes, by the words beside it."""
    found = [one for one in element.descendants() if isinstance(one, ui.link)]
    return {one.props["href"]: one.props.get("target", "") for one in found}


def _offered(library: Mock) -> list[tuple[str, ...]]:
    return [call.args for call in library.source_offers.call_args_list]


def _row(element: ui.element, after: str) -> ui.row:
    """The pickable row naming `after`."""
    for row in (one for one in element.descendants() if isinstance(one, ui.row)):
        if "manager-source-row--pick" in row.classes and any(
                getattr(one, "text", "") == after for one in row.descendants()):
            return row
    raise AssertionError(f"no row named {after}")


def _click(row: ui.element, event: str = "click") -> Any:
    """What a person's click, or double-click, on `row` does; a double-click is a coroutine."""
    for listener in row._event_listeners.values():
        if listener.type == event and listener.handler is not None:
            return listener.handler()
    raise AssertionError(f"nothing listens for {event}")


class _Drawn(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        patched = patch("manager.kit.offload.run.io_bound", new=_now)
        patched.start()
        self.addCleanup(patched.stop)

    async def drawn(self, probe: mediasource._OnlineTab) -> ui.column:
        with _PAGE:
            body = ui.column()
        await probe.online_tab(body)
        return body


class Rows(_Drawn):
    async def test_a_source_is_asked_for_the_slots_kind_and_the_entry_it_looks_up(self) -> None:
        library = _library(BUILT_IN, FILES)
        await self.drawn(_slot(library))

        self.assertEqual([("ext", "files", "wheel", "g-1", "own", "Own Game")],
                         _offered(library))

    async def test_every_source_is_asked_in_the_one_call(self) -> None:
        library = _library(BUILT_IN, FILES, ELSEWHERE)
        await self.drawn(_slot(library))

        library.game_offers.assert_called_once_with("g-1", "own", "Own Game", ["wheel"])
        library.media_offers.assert_not_called()

    async def test_a_call_that_fails_is_said_once_for_the_sources(self) -> None:
        library = _library(BUILT_IN, FILES)
        library.game_offers.side_effect = RuntimeError("no answer")

        shown = await self.drawn(_slot(library))

        self.assertEqual(1, _seen(shown).count(t("manager.mediasource.sources_failed")))
        self.assertNotIn("Wheel A", _seen(shown))

    async def test_a_source_the_call_says_failed_is_named_and_the_rest_are_drawn(self) -> None:
        library = _library(BUILT_IN, FILES, offers=[
            {"source": "vpinmediadb", "name": "w.png", "url": "https://m.example/w.png",
             "kind": "wheel", "size": ""}])
        library.source_offers.side_effect = RuntimeError("down")

        shown = await self.drawn(_slot(library))

        self.assertIn(t("manager.mediasource.source_failed", source_name="Example Files"),
                      _seen(shown))
        self.assertIn("w.png", " ".join(_seen(shown)))

    async def test_its_rows_name_the_source_first_then_the_site_as_a_link_to_the_page(
            self) -> None:
        shown = await self.drawn(_slot(_library(BUILT_IN, FILES)))

        words = _seen(shown)
        self.assertEqual(["Example Files", "·", "example.com", "·", "30 MB", "FSS"],
                         words[words.index("Wheel A") + 1:words.index("Wheel A") + 7])
        self.assertEqual("_blank", _links(shown)["https://example.com/a"])
        tips = [one.text for one in shown.descendants() if isinstance(one, ui.tooltip)]
        self.assertIn(t("manager.discovery.page_hint", site="example.com"), tips)

    async def test_a_row_the_slot_already_holds_says_so_and_no_row_has_a_button(self) -> None:
        shown = await self.drawn(_slot(_library(BUILT_IN, FILES)))

        self.assertEqual(1, _seen(shown).count(t("manager.mediasource.used_as", kind="Wheel")))
        self.assertEqual([], [one for one in shown.descendants() if isinstance(one, ui.button)])

    async def test_what_the_source_says_under_its_rows_is_drawn(self) -> None:
        shown = await self.drawn(_slot(_library(BUILT_IN, FILES)))

        self.assertIn("More on the site", _seen(shown))
        self.assertEqual("_blank", _links(shown)["https://example.com/"])

    async def test_the_catalogs_rows_link_to_the_games_folder_on_their_site(self) -> None:
        offered = [{"source": "vpinmediadb", "id": "own/wheel.png", "name": "wheel.png",
                    "size": "1 MB"}]
        shown = await self.drawn(_slot(_library(BUILT_IN, FILES, offers=offered)))

        self.assertIn("https://media.example/tree/main/own", _links(shown))

    async def test_a_source_that_fails_says_so_and_the_others_are_still_drawn(self) -> None:
        library = _library(BUILT_IN, FILES, offers=[
            {"source": "vpinmediadb", "id": "own/wheel.png", "name": "wheel.png",
             "size": "1 MB"}])
        library.source_offers.side_effect = RuntimeError("down")

        shown = await self.drawn(_slot(library))

        self.assertIn(t("manager.mediasource.source_failed", source_name="Example Files"),
                      _seen(shown))
        self.assertIn("wheel.png", _seen(shown))

    async def test_nothing_that_offers_the_kind_makes_no_call_at_all(self) -> None:
        library = _library(FILES)

        await self.drawn(_slot(library, built_in=False, kind="topper"))

        library.game_offers.assert_not_called()

    async def test_a_source_the_answer_does_not_hold_is_said_to_have_failed(self) -> None:
        library = _library(BUILT_IN, FILES)
        library.game_offers.side_effect = None
        library.game_offers.return_value = {"sources": []}

        shown = await self.drawn(_slot(library))

        self.assertIn(t("manager.mediasource.source_failed", source_name="Example Files"),
                      _seen(shown))

    async def test_a_catalog_that_failed_is_said_beside_the_files_of_the_others(self) -> None:
        library = _library(BUILT_IN, dict(BUILT_IN, id="other", name="Other"))
        library.game_offers.side_effect = None
        library.game_offers.return_value = {"sources": [
            {"id": "vpinmediadb", "name": "VPinMediaDB", "extension": "", "key": "", "kinds": [
                {"kind": "wheel", "offers": [], "rows": [], "note": "", "link": None,
                 "error": "it is down"}]},
            {"id": "other", "name": "Other", "extension": "", "key": "", "kinds": [
                {"kind": "wheel", "offers": [
                    {"source": "other", "id": "own/wheel.png", "name": "wheel.png",
                     "size": "1 MB", "url": "https://x.example/wheel.png", "kind": "wheel"}],
                 "rows": [], "note": "", "link": None, "error": ""}]}]}

        shown = await self.drawn(_slot(library))

        self.assertIn(t("manager.mediasource.sources_failed"), _seen(shown))
        self.assertIn("wheel.png", _seen(shown))

    async def test_a_catalog_that_failed_alone_is_not_followed_by_nothing_found(self) -> None:
        library = _library(BUILT_IN)
        library.game_offers.side_effect = None
        library.game_offers.return_value = {"sources": [
            {"id": "vpinmediadb", "name": "VPinMediaDB", "extension": "", "key": "", "kinds": [
                {"kind": "wheel", "offers": [], "rows": [], "note": "", "link": None,
                 "error": "it is down"}]}]}

        shown = await self.drawn(_slot(library))

        self.assertIn(t("manager.mediasource.sources_failed"), _seen(shown))
        self.assertFalse([one for one in _seen(shown) if "for this game" in one])

    async def test_a_source_switched_off_is_not_asked(self) -> None:
        library = _library(BUILT_IN, dict(FILES, enabled=False))

        await self.drawn(_slot(library))

        library.source_offers.assert_not_called()

    async def test_a_source_that_does_not_offer_the_kind_is_not_asked(self) -> None:
        library = _library(BUILT_IN, ELSEWHERE)

        await self.drawn(_slot(library))

        library.source_offers.assert_not_called()


class Words(_Drawn):
    async def test_nothing_found_says_so_for_the_catalog_the_kind_is_in(self) -> None:
        library = _library(BUILT_IN, FILES, ELSEWHERE, answer={"rows": []})

        shown = await self.drawn(_slot(library))

        self.assertIn(t("manager.picker.none_other", kinds="wheels"), _seen(shown))

    async def test_a_media_slot_vps_lists_says_vps_has_none(self) -> None:
        library = _library(BUILT_IN, answer={"rows": []})

        shown = await self.drawn(_slot(library, kind="topper"))

        self.assertIn(t("manager.picker.none_vps", kinds="toppers"), _seen(shown))

    async def test_a_media_slot_only_vpinmediadb_has_says_it_has_none(self) -> None:
        library = _library(dict(BUILT_IN, kinds=["flyer"]), answer={"rows": []})

        shown = await self.drawn(_slot(library, kind="flyer"))

        self.assertIn(t("manager.picker.none_media", kinds="flyers"), _seen(shown))

    async def test_a_slot_no_catalog_has_says_none_online(self) -> None:
        library = _library(dict(FILES, kinds=["logo"]), answer={"rows": []})

        shown = await self.drawn(_slot(library, built_in=False, kind="logo"))

        self.assertIn(t("manager.picker.none_other", kinds="logos"), _seen(shown))

    async def test_every_source_switched_off_says_none_online(self) -> None:
        library = _library(dict(FILES, kinds=["logo"], enabled=False))

        shown = await self.drawn(_slot(library, built_in=False, kind="logo"))

        self.assertIn(t("manager.picker.none_other", kinds="logos"), _seen(shown))


class Slots(_Drawn):
    def test_a_slot_no_source_offers_has_no_online_tab(self) -> None:
        library = _library(BUILT_IN)
        library.offers_online.return_value = False

        self.assertFalse(_slot(library, built_in=False, kind="logo").online)

    def test_a_slot_a_catalog_has_keeps_its_tab_with_no_extension_at_all(self) -> None:
        library = _library(BUILT_IN)
        library.offers_online.return_value = False

        self.assertTrue(_slot(library, built_in=True).online)

    def test_a_slot_an_extension_offers_has_one_without_a_catalog(self) -> None:
        self.assertTrue(_slot(_library(FILES), built_in=False).online)

    def test_a_folder_has_the_tab_only_when_a_source_offers_its_kind(self) -> None:
        library = _library(FILES)
        context = _context(library)

        self.assertTrue(mediasource._Folder(context, "pup_pack", "PUP pack", Mock()).online)
        library.offers_online.return_value = False
        self.assertFalse(mediasource._Folder(context, "music", "Music", Mock()).online)
        library.offers_online.return_value = True
        self.assertTrue(mediasource._Folder(context, "music", "Music", Mock()).online)


class TheBackglass(_Drawn):
    """The picture and the .directb2s a game keeps are both `backglass` to core."""

    async def test_the_file_slot_is_asked_under_its_own_name(self) -> None:
        library = _library(dict(FILES, kinds=["backglass_file"]))

        await self.drawn(_slot(library, built_in=False, kind="backglass", asset=True))

        self.assertEqual(("ext", "files", "backglass_file"), _offered(library)[0][:3])

    async def test_the_picture_slot_is_asked_under_the_pictures_name(self) -> None:
        library = _library(dict(FILES, kinds=["backglass"]))

        await self.drawn(_slot(library, built_in=False, kind="backglass"))

        self.assertEqual(("ext", "files", "backglass"), _offered(library)[0][:3])

    async def test_a_source_for_the_picture_is_not_asked_about_the_file(self) -> None:
        library = _library(dict(FILES, kinds=["backglass"]))

        shown = await self.drawn(_slot(library, built_in=False, kind="backglass", asset=True))

        self.assertEqual([], _offered(library))
        self.assertNotIn("Example Files", " ".join(_seen(shown)))

    def test_the_tab_is_decided_by_the_slots_own_name(self) -> None:
        library = _library(FILES)

        file = _slot(library, built_in=False, kind="backglass", asset=True)
        picture = _slot(library, built_in=False, kind="backglass")

        self.assertEqual(["backglass_file", "backglass"],
                         [file.source_kind, picture.source_kind])


class Taking(_Drawn):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.library = _library(BUILT_IN, FILES)
        self.target = Mock(online=True, file=False, accept=(".png", ".jpg"),
                           displaced=Mock(return_value=[]))
        self.probe = _slot(self.library, target=self.target)
        self.probe.pane, self.probe.verb = Mock(), Mock()
        self.shown = await self.drawn(self.probe)

    async def pick(self, answer: Any) -> None:
        """Click the row, then press the verb: the pick is complete only at the verb."""
        self.library.source_take.return_value = answer
        _click(_row(self.shown, "Wheel A"))
        with _PAGE:
            await self.probe.go()

    async def test_a_click_picks_and_asks_nothing_of_the_source(self) -> None:
        _click(_row(self.shown, "Wheel A"))

        self.library.source_take.assert_not_called()
        pending = self.probe.pending
        self.assertEqual("Wheel A", pending.card.name if pending and pending.card else "")
        self.probe.verb.enable.assert_called_once()

    async def test_a_double_click_picks_and_takes(self) -> None:
        self.library.source_take.return_value = {"message": "Nothing to do"}

        with _PAGE:
            await _click(_row(self.shown, "Wheel A"), "dblclick")

        self.library.source_take.assert_called_once()

    async def test_the_pick_names_the_row_the_kind_and_the_game(self) -> None:
        await self.pick({"message": "Nothing to do"})

        self.library.source_take.assert_called_once_with(
            "ext", "files", "wheel", "a", "g-1", "own", "Own Game", "")

    async def test_the_table_chosen_to_save_it_for_goes_with_the_pick(self) -> None:
        self.probe.placed_at = {"table": "t-2"}

        await self.pick({"message": "Nothing to do"})

        self.assertEqual(("g-1", "own", "Own Game", "t-2"),
                         self.library.source_take.call_args.args[4:])

    async def test_the_table_it_was_chosen_for_goes_on_to_a_dialog_it_opens(self) -> None:
        self.probe.placed_at = {"table": "t-2"}

        with patch.object(picker_online.ext_lists, "open_over", new=AsyncMock()) as opened:
            await self.pick({"action": "get"})

        self.assertEqual("t-2", opened.await_args_list[0].kwargs["who"]["table_id"])

    async def test_a_folder_has_no_table_to_save_it_for(self) -> None:
        folder = mediasource._Folder(_context(self.library), "pup_pack", "PUP pack", Mock())

        self.assertEqual("", folder.destination)

    async def test_a_message_is_said(self) -> None:
        with patch.object(mediasource.ui, "notify") as notify:
            await self.pick({"message": "Nothing to do"})

        notify.assert_called_once_with("Nothing to do")

    async def test_a_job_closes_the_dialog_and_says_so(self) -> None:
        with patch.object(mediasource._Sources, "finish", new=AsyncMock()) as finish:
            await self.pick({"job": "j1", "message": "Started it"})
            await self.pick({"job": "j1"})

        self.assertEqual(
            [("Started it",), (t("manager.mediasource.ext_started",
                                 source_name="Example Files"),)],
            [call.args[1:] for call in finish.call_args_list])

    async def test_a_dialog_opens_over_this_one_for_the_row_picked(self) -> None:
        with patch.object(picker_online.ext_lists, "open_over", new=AsyncMock()) as opened:
            await self.pick({"action": "get"})

        opened.assert_awaited_once_with({"name": "ext"}, "get", ["a"], who={
            "game_id": "g-1", "vps_id": "own", "name": "Own Game", "table_id": ""})

    async def test_a_source_that_refuses_the_pick_says_so_and_does_nothing_more(self) -> None:
        self.library.source_take.side_effect = RuntimeError("no")

        with patch.object(mediasource.ui, "notify") as notify:
            await self.pick({})

        self.assertEqual(t("manager.mediasource.fetch_failed"), notify.call_args.args[0])

    def _described(self, going: list[str], current: str = "", placements: bool = True) -> Mock:
        self.probe.current = current
        self.probe.placements = [{"table": ""}] if placements else []
        self.probe.placed_at = {"table": "", "base": "wheel", "displaces": going}
        self.probe.pending = None
        self.probe.pane = Mock()
        self.probe._describe_placement()
        return self.probe.pane

    async def test_a_slot_that_would_overwrite_says_so_under_the_pair(self) -> None:
        pane = self._described(["wheel.png"])

        pane.backs_up.assert_called_once_with(True)

    async def test_the_name_a_pick_would_take_carries_the_files_extension(self) -> None:
        self._described([])
        self.probe.pending = Mock(filename="Party.PNG")

        self.assertEqual("wheel.png", self.probe._file_name())
        self.probe.pending = None
        self.assertEqual("", self.probe._file_name())
        self.probe.pending = Mock(filename="1.0 Someone")
        self.assertEqual("", self.probe._file_name())

    async def test_new_is_named_for_the_file_it_becomes_once_something_is_picked(self) -> None:
        pane = self._described([])
        pane.save_as.assert_called_once_with("")

        self.probe.pending = Mock(filename="Party.PNG")
        self.probe._describe_placement()

        pane.save_as.assert_called_with("wheel.png")

    async def test_a_slot_with_nothing_there_says_nothing_goes_to_backups(self) -> None:
        pane = self._described([])

        pane.backs_up.assert_called_once_with(False)

    async def test_a_file_in_use_with_no_placement_to_read_still_goes_to_backups(self) -> None:
        pane = self._described([], current="/x/wheel.png", placements=False)

        pane.backs_up.assert_called_once_with(True)


class OnlineKinds(unittest.TestCase):
    def library(self, *known: dict) -> tuple[Library, Mock]:
        client = Mock()
        client.media_sources.return_value = list(known)
        return Library(client), client

    def test_a_kind_is_offered_by_a_source_that_is_switched_on(self) -> None:
        library, _client = self.library(BUILT_IN, FILES, dict(ELSEWHERE, enabled=False))
        library.load_online_kinds()

        self.assertTrue(library.offers_online("wheel"))
        self.assertFalse(library.offers_online("topper"))

    def test_a_catalog_alone_is_not_an_extension_that_offers(self) -> None:
        library, _client = self.library(BUILT_IN)
        library.load_online_kinds()

        self.assertFalse(library.offers_online("wheel"))

    def test_a_kind_is_found_under_any_name_its_dialog_may_be_opened_with(self) -> None:
        library, _client = self.library(dict(FILES, kinds=["altcolor_serum"]))
        library.load_online_kinds()

        self.assertTrue(library.offers_online("alt_color"))
        self.assertTrue(library.offers_online("altcolor_serum"))
        self.assertFalse(library.offers_online("altcolor_vni"))

    def test_a_table_source_is_not_one_for_the_playfield_picture(self) -> None:
        library, _client = self.library(dict(FILES, kinds=["table"]))
        library.load_online_kinds()

        self.assertTrue(library.offers_online("table"))
        self.assertFalse(library.offers_online("playfield"))

    def test_asking_what_is_offered_reads_nothing_and_offers_nothing_before_the_load(self) -> None:
        library, client = self.library(FILES)

        self.assertFalse(library.offers_online("wheel"))
        client.media_sources.assert_not_called()

    def test_a_read_that_fails_leaves_the_page_opening_with_no_extension_tabs(self) -> None:
        library, client = self.library(FILES)
        client.media_sources.side_effect = RuntimeError("down")

        with self.assertLogs("vpinfe.manager.kit.data", "DEBUG"):
            self.assertEqual(frozenset(), library.load_online_kinds())
        self.assertFalse(library.offers_online("wheel"))

    def test_switching_a_source_off_in_settings_is_read_again(self) -> None:
        library, client = self.library(FILES)
        client.put_library_policy.return_value = {}
        library.load_online_kinds()
        self.assertTrue(library.offers_online("wheel"))

        client.media_sources.return_value = [dict(FILES, enabled=False)]
        library.put_library_policy({"hidden_sources": ["ext.files"]})

        self.assertFalse(library.offers_online("wheel"))

    def test_a_policy_that_is_not_about_sources_is_not_a_reason_to_read_them(self) -> None:
        library, client = self.library(FILES)
        client.put_library_policy.return_value = {}
        library.load_online_kinds()
        client.media_sources.reset_mock()

        library.put_library_policy({"hidden_media_kinds": ["wheel"]})

        client.media_sources.assert_not_called()


if __name__ == "__main__":
    unittest.main()
