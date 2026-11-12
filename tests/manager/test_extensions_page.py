"""One extension's page in Settings, and the rows that lead to it.

What is worth pinning is that every state the host can reach is shown, by a word or by
the switch, that a switch somebody set does not read as something that broke, and that
the index marks only what is wrong or off.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock

from nicegui import ui

from common import i18n
from common.devices import install_identity
from common.extensions import host
from common.settings import feature_checks
from manager.extensions import ext_page
from manager.kit import wizard
from manager.settings import settings
from tests.support import lines


class StateWordTests(unittest.TestCase):
    def test_every_state_the_switch_cannot_show_has_a_word(self) -> None:
        """A state with no word draws no finding, so a stopped extension would look fine."""
        states = {value for name, value in vars(host).items()
                  if name.isupper() and isinstance(value, str)
                  and value in {"loaded", "failed", "disabled", "off"}}

        self.assertEqual(states - {host.LOADED, host.OFF}, set(ext_page.STATE_WORDS))

    def test_running_draws_no_finding(self) -> None:
        self.assertNotIn(host.LOADED, ext_page.STATE_WORDS)

    def test_off_draws_no_finding_beside_the_switch_that_says_it(self) -> None:
        self.assertNotIn(host.OFF, ext_page.STATE_WORDS)


RUNNING = {"name": "sample", "display_name": "Sample", "version": "1.2.0",
           "description": "Does a thing", "state": host.LOADED, "enabled": True,
           "reason": "", "reason_key": "", "actions": [], "surfaces": {}, "scopes": [],
           "capabilities": []}
SWITCHED_OFF = {**RUNNING, "state": host.OFF, "enabled": False, "reason": "Switched off",
                "reason_key": host.SWITCHED_OFF}
WAITING = {**SWITCHED_OFF, "enabled": True, "reason": "Starts at the next restart",
           "reason_key": host.STARTS_AT_RESTART}
WRONG_PLATFORM = {**RUNNING, "state": host.OFF, "reason": "It does not run on macOS",
                  "reason_key": host.WRONG_PLATFORM}
LACKS = {**RUNNING, "state": host.OFF, "reason": "It needs the frontend feature",
         "reason_key": host.LACKS_FEATURES}
BROKE = {**RUNNING, "state": host.FAILED, "reason": "It threw"}
STOPPED = {**RUNNING, "state": host.DISABLED, "reason": "It stopped answering"}


class Page:
    """One extension's page drawn, and what is on it."""

    def __init__(self, found: dict, settings_read: dict | None = None,
                 open_this_device: Any = None) -> None:
        self.redraw = mock.Mock()
        self.open_this_device = open_this_device or mock.Mock()
        with ui.column() as self.body:
            with mock.patch.object(ext_page, "ApiClient"):
                ext_page.build(found, settings_read or {}, self.redraw,
                               self.open_this_device)

    def labels(self, *classes: str) -> list[str]:
        return [one.text for one in self.body.descendants()
                if isinstance(one, ui.label) and all(c in one.classes for c in classes)]

    @property
    def headings(self) -> list[str]:
        return self.labels("manager-fact-heading")

    @property
    def rows(self) -> list[str]:
        return self.labels("manager-fact-label")

    @property
    def buttons(self) -> list[str]:
        return [one.text for one in self.body.descendants() if isinstance(one, ui.button)]

    @property
    def switches(self) -> list[bool]:
        return [bool(one.value) for one in self.body.descendants()
                if isinstance(one, ui.switch)]


class RemoteNameTests(unittest.TestCase):
    def test_a_surface_a_remote_can_open_is_named_by_its_kind_and_key(self) -> None:
        used, remote = ext_page._remote_names([
            "games:read", "play:router", "play:list:scores", "play:page:scores",
            "play:action:reset_all", "play:group:game", "play:verb:game:rate_all"])

        self.assertEqual(used, ["games:read"])
        self.assertEqual(remote, [
            "Its Web Addresses", "The Scores List", "The Scores Page", "The Reset All Action",
            "Its Section on the Game Panel", "The Rate All Button on the Game Panel"])


class HeaderTests(unittest.TestCase):
    def test_the_name_is_the_display_name_with_the_version_under_it(self) -> None:
        page = Page(RUNNING)

        self.assertEqual(page.labels("manager-workbench-title"), ["Sample"])
        self.assertEqual(page.labels("manager-workbench-label"), ["Version 1.2.0"])

    def test_no_version_draws_no_caption(self) -> None:
        page = Page({**RUNNING, "version": ""})

        self.assertEqual(page.labels("manager-workbench-label"), [])

    def test_its_description_is_under_the_name(self) -> None:
        self.assertEqual(Page(RUNNING).labels("manager-panel-note"), ["Does a thing"])


class LinkTests(unittest.TestCase):
    PANEL = {"key": "panel", "area": "", "label": "Open the panel",
             "tip": "Opens in a new tab", "url": "http://{host}:9100/ui"}

    def items(self, found: dict) -> list[ui.menu_item]:
        return [one for one in Page(found).body.descendants() if isinstance(one, ui.menu_item)]

    def test_a_link_is_a_menu_item_at_the_end_of_the_name(self) -> None:
        (item,) = self.items({**RUNNING, "links": [self.PANEL]})

        self.assertEqual("http://localhost:9100/ui", item.props["href"])
        self.assertEqual("_blank", item.props["target"])
        self.assertEqual("noopener noreferrer", item.props["rel"])

    def test_the_menu_is_in_the_heading_with_the_name(self) -> None:
        page = Page({**RUNNING, "links": [self.PANEL]})

        self.assertEqual(["Sample"], page.labels("manager-workbench-title"))
        headings = [one for one in page.body.descendants()
                    if "manager-panel-heading" in one.classes
                    and not isinstance(one, ui.label)]
        self.assertEqual(["Open the panel"],
                         [inner.text for heading in headings for inner in heading.descendants()
                          if isinstance(inner, ui.label) and inner.text == "Open the panel"])

    def test_an_extension_with_no_links_has_no_menu(self) -> None:
        self.assertEqual([], self.items(RUNNING))
        self.assertEqual([], self.items({**RUNNING, "links": []}))

    def test_a_link_whose_address_is_not_a_web_address_is_not_drawn(self) -> None:
        self.assertEqual([], self.items(
            {**RUNNING, "links": [{**self.PANEL, "url": "javascript:alert(1)"}]}))


class FindingTests(unittest.TestCase):
    def finding(self, found: dict) -> list[str]:
        return Page(found).labels("manager-attention-line")

    def test_one_running_or_only_switched_off_says_nothing_under_its_name(self) -> None:
        self.assertEqual(self.finding(RUNNING), [])
        self.assertEqual(self.finding(SWITCHED_OFF), [])
        self.assertEqual(self.finding(WAITING), [])

    def test_one_that_broke_is_failed_and_says_why(self) -> None:
        page = Page(BROKE)

        self.assertEqual(page.labels("manager-attention-line"),
                         [i18n.t("manager.ext_page.failed")])
        self.assertEqual(page.labels("manager-member-table"), ["It threw"])

    def test_one_that_stopped_is_stopped(self) -> None:
        self.assertEqual(self.finding(STOPPED), [i18n.t("manager.ext_page.stopped")])

    def test_one_that_cannot_run_here_says_so_with_nothing_to_press(self) -> None:
        page = Page(WRONG_PLATFORM)

        self.assertEqual(page.labels("manager-attention-line"),
                         [i18n.t("manager.ext_page.cannot_run")])
        self.assertEqual(page.labels("manager-member-table"), ["It does not run on macOS"])
        self.assertEqual(page.buttons, [])

    def test_one_missing_a_feature_offers_this_device(self) -> None:
        opened = mock.Mock()
        with mock.patch.object(ext_page.panel, "action",
                               wraps=ext_page.panel.action) as action:
            page = Page(LACKS, open_this_device=opened)

        self.assertEqual(page.buttons, [i18n.t("manager.ext_page.open_device")])
        action.call_args.args[1]()
        opened.assert_called_once_with()


class SettingsGroupTests(unittest.TestCase):
    def test_enabled_is_the_first_row_under_a_headed_settings_group(self) -> None:
        page = Page(RUNNING)

        self.assertEqual(page.headings[0], i18n.t("manager.ext_page.settings"))
        self.assertEqual(page.rows[0], i18n.t("manager.ext_page.enabled"))

    def test_the_switch_shows_what_was_chosen_not_what_is_running(self) -> None:
        """Switched on and waiting for a restart is on, though the state is off."""
        self.assertEqual(Page(WAITING).switches, [True])
        self.assertEqual(Page(SWITCHED_OFF).switches, [False])
        self.assertEqual(Page(LACKS).switches, [True])

    def test_one_waiting_for_a_restart_says_so_under_the_switch(self) -> None:
        self.assertIn(i18n.t(host.STARTS_AT_RESTART), Page(WAITING).labels("manager-help"))
        self.assertNotIn(i18n.t(host.STARTS_AT_RESTART),
                         Page(SWITCHED_OFF).labels("manager-help"))

    def test_its_own_settings_are_rows_beside_enabled(self) -> None:
        page = Page({**RUNNING, "surfaces": {"settings": "/settings"}},
                    {"fields": [{"key": "endpoint", "label": "Server", "value": "x"}]})

        self.assertEqual(page.rows[:2], [i18n.t("manager.ext_page.enabled"), "Server"])

    def test_a_setting_that_would_not_read_says_why(self) -> None:
        page = Page({**RUNNING, "surfaces": {"settings": "/settings"}},
                    {"error": "No answer"})

        self.assertIn(i18n.t("manager.ext_page.list_failed"), page.labels("manager-intro"))


class StoppedSettingsLineTests(unittest.TestCase):
    LINE = i18n.t("manager.ext_page.settings_when_running")

    def says(self, found: dict) -> bool:
        return self.LINE in Page({**found, "has_settings": True}).labels("manager-help")

    def test_one_that_is_not_running_says_where_its_settings_will_be(self) -> None:
        for found in (SWITCHED_OFF, WAITING, BROKE, STOPPED):
            with self.subTest(state=found["state"]):
                self.assertTrue(self.says(found))

    def test_one_that_is_running_draws_its_settings_instead(self) -> None:
        self.assertFalse(self.says(RUNNING))

    def test_one_that_cannot_run_here_makes_no_promise(self) -> None:
        for found in (WRONG_PLATFORM, LACKS):
            with self.subTest(reason=found["reason_key"]):
                self.assertFalse(self.says(found))

    def test_one_that_declares_no_settings_says_nothing(self) -> None:
        self.assertNotIn(self.LINE, Page(SWITCHED_OFF).labels("manager-help"))


class ActionsGroupTests(unittest.TestCase):
    ACTIONS = [{"key": "run", "label": "Import a Library", "description": "Imports one"}]

    def test_a_running_extension_offers_its_actions_under_a_heading(self) -> None:
        page = Page({**RUNNING, "actions": self.ACTIONS})

        self.assertIn(i18n.t("word.actions"), page.headings)
        self.assertIn("Import a Library", page.buttons)

    def test_one_that_is_not_running_offers_none(self) -> None:
        page = Page({**SWITCHED_OFF, "actions": self.ACTIONS})

        self.assertNotIn(i18n.t("word.actions"), page.headings)
        self.assertNotIn("Import a Library", page.buttons)

    def test_one_with_none_draws_no_empty_heading(self) -> None:
        self.assertNotIn(i18n.t("word.actions"), Page(RUNNING).headings)

    def _pressed(self, ran: bool) -> mock.Mock:
        pressed: list[Callable[[], Any]] = []
        redraw = mock.Mock()

        def action(label: str, on_click: Callable[[], Any], **kwargs: Any) -> Callable[[], None]:
            pressed.append(on_click)
            return lambda: None

        for target, name, replacement in (
                (ext_page.panel, "action", action),
                (ext_page, "on_page", lambda handler: handler),
                (ext_page.ext_action, "open_action", mock.AsyncMock(return_value=ran))):
            patch = mock.patch.object(target, name, replacement)
            patch.start()
            self.addCleanup(patch.stop)
        with ui.column():
            ext_page._actions(self.ACTIONS, "sample", redraw)
        asyncio.run(pressed[0]())
        return redraw

    def test_the_page_is_drawn_again_once_an_action_has_run(self) -> None:
        self._pressed(True).assert_called_once_with()

    def test_an_action_cancelled_first_draws_nothing_again(self) -> None:
        self._pressed(False).assert_not_called()


IDENTIFIED = {**RUNNING, "author": "Example Author", "home": "https://example.net/tally/",
              "source": "https://github.com/example/tally", "path": "/found/tally",
              "picture_shown": False,
              "trust": {"origin": "in_folder", "accepted": True, "folder": "found"}}
SHIPPED_IDENTITY = {**IDENTIFIED, "trust": {"origin": "shipped", "accepted": True}}


def _words(page: Page) -> list[str]:
    return [one.text for one in page.body.descendants() if isinstance(one, ui.label)]


class AboutTests(unittest.TestCase):
    def test_the_group_comes_before_the_switch(self) -> None:
        page = Page(IDENTIFIED)

        self.assertEqual(page.headings[:2], [i18n.t("manager.ext_page.about"),
                                             i18n.t("manager.ext_page.settings")])

    def test_home_and_source_are_rows_of_their_own_even_when_the_same(self) -> None:
        same = "https://github.com/vpinfe/vpinfe"
        page = Page({**SHIPPED_IDENTITY, "home": same, "source": same})

        self.assertEqual(page.rows[:2], [i18n.t("manager.ext_page.home"),
                                         i18n.t("manager.ext_page.source")])

    def test_each_address_is_shown_in_the_short_form_and_links_to_the_full_one(self) -> None:
        body = Page(IDENTIFIED).body

        links = {one.props["href"]: one for one in body.descendants() if one.props.get("href")}
        self.assertEqual(set(links), {"https://example.net/tally/",
                                      "https://github.com/example/tally"})
        shown = _words(Page(IDENTIFIED))
        self.assertIn("example.net/tally", shown)
        self.assertIn("example/tally", shown)

    def test_a_link_out_opens_in_a_new_tab_without_the_referrer(self) -> None:
        body = Page(IDENTIFIED).body

        links = [one for one in body.descendants() if one.props.get("href")]
        self.assertEqual(len(links), 2)
        for one in links:
            self.assertEqual(one.props.get("target"), "_blank")
            self.assertEqual(one.props.get("rel"), "noopener noreferrer")

    def test_the_folder_path_is_for_what_was_found_not_what_ships(self) -> None:
        self.assertIn(i18n.t("manager.ext_page.folder_path"), Page(IDENTIFIED).rows)
        self.assertNotIn(i18n.t("manager.ext_page.folder_path"), Page(SHIPPED_IDENTITY).rows)

    def test_what_it_reports_is_one_row_last_in_the_group_with_the_line_that_says_so(self) -> None:
        page = Page({**IDENTIFIED, "scopes": ["games:read"], "capabilities": ["net:outbound"]})

        about = page.rows[:page.rows.index(i18n.t("manager.ext_page.enabled"))]
        self.assertEqual(about[-1], i18n.t("manager.trust.uses"))
        self.assertEqual(page.labels("manager-tier", "manager-tier--on"),
                         [i18n.t("manager.ext_page.read_library"),
                          i18n.t("manager.ext_page.reach_internet")])
        self.assertIn(i18n.t("manager.ext_page.reach_help"), page.labels("manager-help"))

    def test_it_is_read_from_the_manifest_whatever_the_state(self) -> None:
        page = Page({**IDENTIFIED, "state": host.OFF, "enabled": False,
                     "scopes": ["games:read"]})

        self.assertIn(i18n.t("manager.trust.uses"), page.rows)

    def test_one_that_reports_nothing_draws_no_row_and_no_line(self) -> None:
        page = Page(IDENTIFIED)

        self.assertNotIn(i18n.t("manager.trust.uses"), page.rows)
        self.assertNotIn(i18n.t("manager.ext_page.reach_help"), page.labels("manager-help"))

    def test_what_a_remote_can_open_is_its_own_row(self) -> None:
        page = Page({**IDENTIFIED, "play": ["router"]})

        self.assertIn(i18n.t("manager.trust.remote"), page.rows)

    def test_one_with_no_manifest_draws_no_group(self) -> None:
        page = Page(RUNNING)

        self.assertNotIn(i18n.t("manager.ext_page.about"), page.headings)
        self.assertEqual(page.rows, [i18n.t("manager.ext_page.enabled")])


class IdentityHeaderTests(unittest.TestCase):
    def test_the_author_is_beside_the_version_under_the_name(self) -> None:
        self.assertEqual(Page(IDENTIFIED).labels("manager-workbench-label"),
                         ["Example Author · Version 1.2.0"])

    def test_a_chip_beside_the_name_says_it_is_not_from_vpinfe(self) -> None:
        self.assertIn(i18n.t("manager.ext_page.unofficial_chip"), _words(Page(IDENTIFIED)))

    def test_one_not_yet_accepted_says_so_in_the_chip(self) -> None:
        page = Page({**IDENTIFIED, "trust": {"origin": "in_folder", "accepted": False,
                                              "folder": "found"}})

        self.assertIn(i18n.t("manager.ext_page.not_trusted_chip"), _words(page))

    def test_one_that_ships_draws_no_chip(self) -> None:
        words = _words(Page(SHIPPED_IDENTITY))

        self.assertNotIn(i18n.t("manager.ext_page.unofficial_chip"), words)
        self.assertNotIn(i18n.t("manager.ext_page.not_trusted_chip"), words)

    def test_a_picture_is_drawn_only_once_it_is_to_be_shown(self) -> None:
        def images(found: dict) -> list[ui.image]:
            return [one for one in Page(found).body.descendants()
                    if isinstance(one, ui.image)]

        (image,) = images({**IDENTIFIED, "picture_shown": True})

        self.assertEqual(image.source, "/api/v1/extensions/sample/picture?v=1.2.0")
        self.assertEqual(images(IDENTIFIED), [])

    def test_one_with_no_manifest_has_no_frame(self) -> None:
        self.assertEqual([one for one in Page(RUNNING).body.descendants()
                          if "manager-ext-picture" in one.classes], [])

    def test_the_frame_is_there_whether_or_not_a_picture_follows(self) -> None:
        self.assertEqual(len([one for one in Page(IDENTIFIED).body.descendants()
                              if "manager-ext-picture" in one.classes]), 1)


class SwitchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        # Outside the test's task, which has no page to draw into.
        self.body = ui.column()

    def draw(self, found: dict, answer: object) -> tuple[mock.Mock, mock.AsyncMock,
                                                         mock.Mock]:
        switch = self.enterContext(mock.patch.object(ext_page.panel, "switch"))
        io = self.enterContext(mock.patch.object(
            ext_page.offload, "io",
            new=mock.AsyncMock(**({"side_effect": answer} if isinstance(answer, Exception)
                                  else {"return_value": answer}))))
        self.client = self.enterContext(mock.patch.object(ext_page, "ApiClient")).return_value
        redraw = mock.Mock()
        with self.body:
            ext_page.build(found, {}, redraw, mock.Mock())
        return switch, io, redraw

    async def press(self, switch: mock.Mock, value: bool = False) -> None:
        with self.body:
            await switch.call_args.args[1](SimpleNamespace(value=value))

    async def test_it_asks_the_api_and_draws_the_page_again(self) -> None:
        switch, io, redraw = self.draw(RUNNING, None)

        await self.press(switch)

        [asked] = io.await_args_list
        self.assertIs(asked.args[0], self.client.set_extension_enabled)
        self.assertEqual(asked.args[1:], ("sample", False))
        redraw.assert_called_once_with()

    async def test_a_refused_switch_says_so_and_draws_the_page_as_it_is(self) -> None:
        notify = self.enterContext(mock.patch.object(ext_page.ui, "notify"))
        switch, _io, redraw = self.draw(RUNNING, RuntimeError("Not allowed"))

        await self.press(switch)

        notify.assert_called_once_with(i18n.t("said.off_failed"), caption="Not allowed",
                                       type="negative")
        redraw.assert_called_once_with()

    async def test_a_refusal_to_turn_it_on_says_that(self) -> None:
        notify = self.enterContext(mock.patch.object(ext_page.ui, "notify"))
        switch, _io, _redraw = self.draw(SWITCHED_OFF, RuntimeError("Not allowed"))

        await self.press(switch, True)

        notify.assert_called_once_with(i18n.t("said.on_failed"), caption="Not allowed",
                                       type="negative")


class IndexTests(unittest.TestCase):
    def test_the_group_is_last_and_lists_each_by_name(self) -> None:
        pages = settings.system_pages(install_identity.FEATURES, [
            {"name": "zed", "display_name": "Zed"}, {"name": "ant", "display_name": "ant"},
            {"name": "mid", "display_name": "Mid"}])
        tail = [(page[0], page[1]) for group, page in pages
                if group == settings.EXTENSIONS_GROUP]

        self.assertEqual(tail, [("extensions.ant", "ant"), ("extensions.mid", "Mid"),
                                ("extensions.zed", "Zed")])
        self.assertEqual(pages[-1][0], settings.EXTENSIONS_GROUP)

    def test_an_extension_for_another_platform_has_no_page_and_one_that_lacks_a_feature_has(
            self) -> None:
        pages = settings.system_pages(install_identity.FEATURES, [
            {"name": "away", "display_name": "Away", "state": "off",
             "reason_key": host.WRONG_PLATFORM},
            {"name": "short", "display_name": "Short", "state": "off",
             "reason_key": host.LACKS_FEATURES}])

        self.assertEqual(["extensions.short"], [page[0] for group, page in pages
                                                if group == settings.EXTENSIONS_GROUP])

    def test_a_page_key_survives_the_address_lowercasing_the_page(self) -> None:
        self.assertEqual(ext_page.key("My_Ext"), "extensions.my_ext")

    def test_none_installed_has_no_group(self) -> None:
        pages = settings.system_pages(install_identity.FEATURES, [])

        self.assertNotIn(settings.EXTENSIONS_GROUP, [group for group, _page in pages])

    def test_the_group_is_there_for_an_install_with_nothing_switched_on(self) -> None:
        pages = settings.system_pages([], [{"name": "ant", "display_name": "Ant"}])

        self.assertIn(settings.EXTENSIONS_GROUP, [group for group, _page in pages])

    def test_an_extension_that_broke_marks_its_page(self) -> None:
        item = feature_checks.Unmet(feature=install_identity.CORE,
                                    section=feature_checks.EXTENSIONS, key="sample",
                                    state="failed", reason="It threw")

        self.assertEqual(settings.pages_in_trouble([item]), {"extensions.sample": [item]})


class TroubleTests(unittest.TestCase):
    def trouble(self, records: list) -> list[Any]:
        with mock.patch.object(settings.extensions, "records", return_value=records):
            return settings._extension_trouble()

    def record(self, state: str) -> Any:
        return SimpleNamespace(name="sample", state=state, reason="said")

    def test_failed_and_stopped_are_trouble(self) -> None:
        for state in (host.FAILED, host.DISABLED):
            [item] = self.trouble([self.record(state)])
            self.assertEqual((item.section, item.key, item.reason),
                             (feature_checks.EXTENSIONS, "sample", "said"))

    def test_running_and_off_are_not(self) -> None:
        self.assertEqual(self.trouble([self.record(host.LOADED),
                                       self.record(host.OFF)]), [])


class IndexMarkTests(unittest.TestCase):
    def marks(self, installed: list, trouble: list) -> dict[str, Any]:
        entries: list = []

        def sections(given: list, *_args: Any, **_kwargs: Any) -> Any:
            entries.extend(given)
            return ui.column(), lambda *_rows: None

        state = {"extensions": installed, "trouble": trouble,
                 "settings_page": "extensions.sample"}
        with mock.patch.object(settings.panel, "sections", sections), \
                mock.patch.object(settings.busy, "fill"):
            settings.build_system(mock.Mock(), state, mock.Mock(),
                                  {"features": list(install_identity.FEATURES)})
        return {entry[1]: entry[3] for entry in entries if len(entry) > 3}

    def test_off_is_said_at_the_end_of_its_row_and_nowhere_else(self) -> None:
        marks = self.marks([SWITCHED_OFF, {**RUNNING, "name": "other",
                                           "display_name": "Other"}], [])

        self.assertIsNotNone(marks["Sample"])
        self.assertIsNone(marks["Other"])
        self.assertIsNone(marks[settings.EXTENSIONS_GROUP])

    def test_one_that_broke_wears_the_trouble_mark_and_so_does_its_group(self) -> None:
        item = feature_checks.Unmet(feature=install_identity.CORE,
                                    section=feature_checks.EXTENSIONS, key="sample",
                                    state="failed", reason="It threw")
        marks = self.marks([BROKE], [item])

        self.assertIsNotNone(marks["Sample"])
        self.assertIsNotNone(marks[settings.EXTENSIONS_GROUP])

    def test_one_running_is_marked_with_nothing(self) -> None:
        self.assertIsNone(self.marks([RUNNING], [])["Sample"])


class SecretSettingTests(unittest.TestCase):
    """A secret is drawn empty whatever it holds, leaving it empty keeps it, and Clear asks
    first and then empties it."""

    def _drawn(self, field: dict, asked: bool = True) -> dict[str, Any]:
        seen: dict[str, Any] = {"actions": []}

        def fake(value, on_save, **kwargs):
            seen.update(value=value, on_save=on_save, **kwargs)
            return lambda: None

        def action(label, on_click, **kwargs):
            seen["actions"].append((label, on_click))
            return lambda: None

        async def ask(*args, **kwargs):
            seen["asked"] = (args, kwargs)
            return asked

        for target, name, replacement in (
                (ext_page.panel, "field", fake), (ext_page.panel, "action", action),
                (ext_page, "on_page", lambda handler: handler),
                (ext_page.confirm, "ask", ask), (ext_page.ui, "row", mock.MagicMock())):
            patch = mock.patch.object(target, name, replacement)
            patch.start()
            self.addCleanup(patch.stop)
        seen["saves"] = []

        async def save(new_value, *, under=False):
            seen["saves"].append((new_value, under))
            return ""

        ext_page.control(mock.Mock(), {"label": "Token", **field}, save)()
        return seen

    def test_a_value_that_arrived_anyway_is_not_drawn(self) -> None:
        seen = self._drawn({"key": "token", "type": "secret", "value": "leaked", "set": True})

        self.assertEqual((seen["value"], seen["placeholder"]),
                         ("", i18n.t("manager.ext_page.secret_set")))

    def test_one_not_set_says_so_and_has_no_clear(self) -> None:
        seen = self._drawn({"key": "token", "type": "secret", "set": False})

        self.assertEqual(seen["placeholder"], i18n.t("manager.ext_page.secret_not_set"))
        self.assertEqual(seen["actions"], [])

    def test_the_box_is_masked(self) -> None:
        self.assertTrue(self._drawn({"key": "token", "type": "secret"})["secret"])

    def test_leaving_it_empty_writes_nothing(self) -> None:
        seen = self._drawn({"key": "token", "type": "secret", "set": True})

        self.assertIsNone(seen["on_save"](""))
        self.assertEqual(seen["saves"], [])

    def test_a_typed_value_is_saved_under_the_box(self) -> None:
        seen = self._drawn({"key": "token", "type": "secret", "set": True})

        asyncio.run(seen["on_save"]("abc"))

        self.assertEqual(seen["saves"], [("abc", True)])

    def test_clear_asks_first_and_then_saves_none(self) -> None:
        seen = self._drawn({"key": "token", "type": "secret", "set": True})
        [(label, clear)] = seen["actions"]

        asyncio.run(clear())

        self.assertEqual(label, i18n.t("manager.ext_page.clear"))
        self.assertEqual(seen["saves"], [(None, False)])
        self.assertIn(i18n.t("manager.ext_page.clear_question", label="Token"),
                      seen["asked"][0])

    def test_clear_declined_changes_nothing(self) -> None:
        seen = self._drawn({"key": "token", "type": "secret", "set": True}, asked=False)
        [(_, clear)] = seen["actions"]

        asyncio.run(clear())

        self.assertEqual(seen["saves"], [])


class ControlTypeTests(unittest.TestCase):
    """Each type a settings page or an account draws is drawn by the control it asks for."""

    def _which(self, field: dict) -> tuple[str, dict[str, Any]]:
        seen: dict[str, Any] = {}

        def recorder(name: str) -> Callable[..., Any]:
            def fake(*args: Any, **kwargs: Any) -> Callable[[], None]:
                seen.update(name=name, args=args, kwargs=kwargs)
                return lambda: None
            return fake

        for target, name in ((ext_page.panel, "switch"), (ext_page.panel, "select"),
                             (ext_page.panel, "multi_select"), (ext_page.panel, "number"),
                             (ext_page.panel, "field"), (ext_page, "_path_control")):
            patch = mock.patch.object(target, name, recorder(name))
            patch.start()
            self.addCleanup(patch.stop)
        ext_page.control(mock.Mock(), field, mock.AsyncMock())
        return seen["name"], seen

    def test_a_multi_is_ticked_from_its_stored_list(self) -> None:
        kind, seen = self._which({"key": "m", "type": "multi", "value": ["a"],
                                  "choices": [["a", "A"], ["b", "B"]]})

        self.assertEqual(kind, "multi_select")
        self.assertEqual(seen["args"][:2], ({"a": "A", "b": "B"}, ["a"]))

    def test_a_number_carries_its_limits_and_unit(self) -> None:
        kind, seen = self._which({"key": "n", "type": "number", "value": "7", "min": 1,
                                  "max": 9, "unit": "dB"})

        self.assertEqual(kind, "number")
        self.assertEqual(seen["args"][0], 7.0)
        self.assertEqual((seen["kwargs"]["low"], seen["kwargs"]["high"], seen["kwargs"]["unit"]),
                         (1, 9, "dB"))

    def test_a_number_that_is_not_set_is_drawn_empty(self) -> None:
        _, seen = self._which({"key": "n", "type": "number", "value": ""})

        self.assertIsNone(seen["args"][0])

    def test_a_path_is_drawn_by_the_path_control(self) -> None:
        kind, _ = self._which({"key": "p", "type": "path", "value": "/x"})

        self.assertEqual(kind, "_path_control")


class LanguageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.addCleanup(i18n.set_language, i18n.language())

    def test_what_a_scope_allows_is_read_in_the_language_set(self) -> None:
        english = ext_page._plainly("games:read")
        i18n.set_language("qps")

        self.assertNotEqual(english, ext_page._plainly("games:read"))

    def test_a_scope_nobody_described_is_shown_as_it_is(self) -> None:
        self.assertEqual("games:teleport", ext_page._plainly("games:teleport"))


def _said(job: dict, under: str = "ext.sample.action.run") -> list[str]:
    with ui.column() as body:
        wizard.report(body, job, under)
    return [one.text for one in body.descendants() if isinstance(one, ui.label)]


def _reported(job: dict) -> dict[str, str]:
    with ui.column() as body:
        wizard.report(body, job, "ext.sample.action.run")
    return lines.details(body)


def _noted(notes: list) -> dict[str, str]:
    with ui.column() as body:
        wizard._lines(notes, "")
    return lines.details(body)


class NoteTests(unittest.TestCase):
    def test_a_plain_note_hovers_nothing(self) -> None:
        self.assertEqual({"Kiss.pov was left behind": ""},
                         _noted(["Kiss.pov was left behind"]))

    def test_a_note_with_a_detail_hovers_it(self) -> None:
        self.assertEqual(
            {"Settings.xml could not be read": "Nothing is at /pbx/Config/Settings.xml"},
            _noted([{"text": "Settings.xml could not be read",
                     "detail": "Nothing is at /pbx/Config/Settings.xml"}]))

    def test_a_note_with_only_a_detail_says_the_detail(self) -> None:
        self.assertEqual({"Nothing answers at example.org": ""},
                         _noted([{"detail": "Nothing answers at example.org"}]))


class ReportTests(unittest.TestCase):
    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        (Path(folder.name) / "en.json").write_text(
            json.dumps({"action.run.result.games": "Games made"}), encoding="utf-8")
        i18n.own("ext.sample", Path(folder.name))
        self.addCleanup(i18n.disown, "ext.sample")
        self.addCleanup(i18n.set_language, i18n.language())

    def test_a_count_is_titled_by_its_extension(self) -> None:
        self.assertIn("Games made", _said({"state": "done", "result": {"games": 3}}))

    def test_a_count_with_no_word_is_titled_by_its_field(self) -> None:
        self.assertIn("games_skipped",
                      _said({"state": "done", "result": {"games_skipped": 1}}))

    def test_a_row_that_does_not_say_how_it_matched_is_named_alone(self) -> None:
        said = _said({"state": "done",
                      "result": {"already_here": [{"key": "k", "name": "Kiss"}]}})

        self.assertIn("Kiss", said)

    def test_a_row_that_did_not_come_across_is_worded_by_the_catalog(self) -> None:
        with mock.patch.object(wizard, "t", side_effect=lambda key, **_values: key):
            said = _said({"state": "done",
                          "result": {"rows": [{"name": "Kiss", "error": "no table"}]}})

        self.assertIn("manager.ext_action.missed", said)

    def test_a_row_error_with_a_detail_hovers_it(self) -> None:
        drawn = _reported({"state": "done", "result": {"rows": [
            {"name": "Kiss", "error": {"text": "the game file was not imported",
                                       "detail": "Something is already at Kiss.vpx"}}]}})

        self.assertEqual("Something is already at Kiss.vpx",
                         drawn["Kiss - the game file was not imported"])

    def test_a_row_error_as_a_string_hovers_nothing(self) -> None:
        drawn = _reported({"state": "done", "result": {"rows": [
            {"name": "Kiss", "error": "the game file was not imported"}]}})

        self.assertEqual("", drawn["Kiss - the game file was not imported"])


class SavedSettingTests(unittest.TestCase):
    """A saved setting redraws the page only where the extension reads back more than the
    one value that was set."""

    HELD = {"fields": [{"key": "volume", "type": "number", "value": 3},
                       {"key": "name", "type": "string", "value": "a"}]}

    def stands(self, fresh: dict | None, *, error: Exception | None = None,
               value: Any = 5) -> tuple[bool, dict]:
        client = mock.Mock()
        client.ext_get.side_effect = error
        client.ext_get.return_value = fresh
        held = json.loads(json.dumps(self.HELD))

        async def at_once(call: Callable[..., Any], *args: Any) -> Any:
            return call(*args)

        with mock.patch.object(ext_page.run, "io_bound", at_once):
            return asyncio.run(ext_page._stands(client, "/ext/x", held, "volume", value)), held

    def test_a_read_back_that_differs_only_in_the_value_set_stands(self) -> None:
        fresh = {"fields": [{"key": "volume", "type": "number", "value": 5},
                            {"key": "name", "type": "string", "value": "a"}]}

        stood, held = self.stands(fresh)

        self.assertTrue(stood)
        self.assertEqual(5, held["fields"][0]["value"])

    def test_another_value_that_moved_is_drawn_again(self) -> None:
        fresh = {"fields": [{"key": "volume", "type": "number", "value": 5},
                            {"key": "name", "type": "string", "value": "b"}]}

        self.assertFalse(self.stands(fresh)[0])

    def test_a_value_the_extension_changed_is_drawn_again(self) -> None:
        fresh = {"fields": [{"key": "volume", "type": "number", "value": 4},
                            {"key": "name", "type": "string", "value": "a"}]}

        self.assertFalse(self.stands(fresh)[0])

    def test_a_secret_is_drawn_again_whatever_it_reads_back(self) -> None:
        held = {"fields": [{"key": "token", "type": "secret", "set": True}]}
        client = mock.Mock()
        client.ext_get.return_value = json.loads(json.dumps(held))

        with mock.patch.object(ext_page.run, "io_bound",
                               lambda call, *args: asyncio.sleep(0, call(*args))):
            stood = asyncio.run(ext_page._stands(client, "/ext/x", held, "token", "abc"))

        self.assertFalse(stood)

    def test_a_page_that_cannot_be_read_back_is_drawn_again(self) -> None:
        self.assertFalse(self.stands(None, error=RuntimeError("down"))[0])


if __name__ == "__main__":
    unittest.main()
