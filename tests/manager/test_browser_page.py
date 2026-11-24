"""Settings > Frontend > Browser: what the browser plays, and Use Google Chrome."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import requests
from fastapi.testclient import TestClient

import httpapi
from common.config import paths
from common.host import frontend_browser
from common.i18n import t
from manager.kit import panel, undo
from manager.kit.api import ApiClient
from manager.kit.data import Library
from manager.settings import settings
from tests.support.clicks import press
from tests.support.regions import Immediate

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
BROWSER = next(page for _group, pages in settings.DEVICE_INDEX for page in pages
               if page[0] == "frontend.chromium")
NO_H264 = {"h264": False, "hevc": False, "vp9": True, "av1": True, "aac": False,
           "mp3": True, "vorbis": True, "opus": True}
PLAYS_ALL = dict.fromkeys(NO_H264, True)


class Served:
    """The Manager's client, answered by this install's API in-process, with a frontend
    whose restarting the test holds."""

    def __init__(self, client: TestClient) -> None:
        self._client = client
        self.perform_action = Mock(return_value={})

    @staticmethod
    def _answered(response: Any) -> Any:
        if response.is_error:
            raise RuntimeError(response.text)
        return response.json()

    def config_schema(self) -> list[dict]:
        return list(self._answered(self._client.get("/config/schema"))["sections"])

    def config_values(self) -> dict:
        return dict(self._answered(self._client.get("/config/settings")))

    def config_path_checks(self) -> list[dict]:
        return list(self._answered(self._client.get("/config/paths"))["checks"])

    def put_config(self, changes: dict) -> dict:
        sent = self._client.put("/config", json={"settings": changes})
        return dict(self._answered(sent)["settings"])

    def frontend_browser(self) -> dict:
        return dict(self._answered(self._client.get("/frontend/browser")))

    def config_waiting(self) -> dict:
        return dict(self._answered(self._client.get("/config/waiting")))


class BrowserPageTests(unittest.IsolatedAsyncioTestCase):
    """A browser in use that is not Google Chrome, with Chrome installed beside it."""

    def setUp(self) -> None:
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.in_use = root / "chromium"
        self.in_use.touch()
        self.bundled = False
        self.chrome = CHROME
        self.report = root / "frontend_browser.json"
        self.enterContext(patch.object(paths, "VPINFE_INI_PATH", root / "vpinfe.ini"))
        self.enterContext(patch.object(paths, "FRONTEND_BROWSER_PATH", self.report))
        self.enterContext(patch.object(frontend_browser, "_in_use",
                                       side_effect=lambda: (str(self.in_use), self.bundled)))
        self.unset: Path | None = None
        self.enterContext(patch.object(
            frontend_browser, "_unset",
            side_effect=lambda: (str(self.unset or self.in_use), self.bundled)))
        self.enterContext(patch.object(frontend_browser, "_google_chrome",
                                       side_effect=lambda: self.chrome))
        self.client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)
        self.served = Served(self.client)
        self.library = Library(self.served)  # type: ignore[arg-type]
        self.enterContext(patch.object(
            settings.run, "io_bound",
            new=AsyncMock(side_effect=lambda call, *args, **kwargs: call(*args, **kwargs))))
        self.ui = self.enterContext(patch.object(settings, "ui"))
        self.enterContext(patch.object(settings, "page_head"))
        self.enterContext(patch.object(panel, "region", new=Immediate))
        self.facts = self.enterContext(patch.object(panel, "facts"))
        self.enterContext(patch.object(
            panel, "states", side_effect=lambda chips, note="": list(chips)))
        self.action = self.enterContext(patch.object(panel, "action"))
        self.link = self.enterContext(patch.object(panel, "link"))
        self.offer = self.enterContext(patch.object(undo, "offer"))

    def _reported(self, formats: dict[str, bool], browser: str = "Chromium 145.0.7632.0",
                  path: Path | None = None) -> None:
        self.report.write_text(json.dumps({
            "browser": browser, "path": str(path or self.in_use), "bundled": self.bundled,
            "formats": formats, "reported_at": "2026-09-28T01:20:00Z"}), encoding="utf-8")

    def _program(self) -> str:
        return str(self.served.config_values()["chromium"]["path"])

    async def _heads(self) -> list[tuple[Any, Any]]:
        self.facts.reset_mock()
        await settings._draw_system_page(self.library, Mock(), MagicMock(), BROWSER, {}, [])
        [drawn] = self.facts.call_args_list
        return list(drawn.args[1])

    async def _drawn(self) -> list[tuple[Any, Any]]:
        entries = await self._heads()
        return entries[1:] if entries[0][0] is panel.HEADING else entries

    @staticmethod
    def _labels(entries: list[tuple[Any, Any]]) -> list[Any]:
        return [label for label, _value in entries]

    def _value(self, entries: list[tuple[Any, Any]], label: str) -> Any:
        return dict((one, value) for one, value in entries if isinstance(one, str))[label]

    def _use_google_chrome(self, entries: list[tuple[Any, Any]]) -> Any:
        """The finding drawn, and the act it offers, if any."""
        self.action.reset_mock()
        entries[0][1]()
        offered = [call.args[1] for call in self.action.call_args_list
                   if call.args[0] == t("manager.settings.use_chrome")]
        return offered[0] if offered else None

    def _levels(self, entries: list[tuple[Any, Any]], label: str) -> dict[str, str]:
        return {text: level for text, level, _hint in self._value(entries, label)}

    async def test_what_it_plays_sits_under_the_program_and_before_the_next_group(self) -> None:
        self._reported(NO_H264)

        labels = self._labels(await self._drawn())

        order = [labels.index(t(key)) for key in (
            "config.chromium.path.label", "manager.settings.browser_video",
            "manager.settings.browser_audio")]
        self.assertEqual(order, sorted(order))
        self.assertEqual(order[2] - order[1], 1)
        self.assertIs(labels[order[2] + 1], panel.HEADING)

    def test_rows_go_after_the_first_group_even_when_a_note_opens_the_page(self) -> None:
        first, second = object(), object()
        own = ["note", (panel.HEADING, "A"), ("Program", 1), (panel.HEADING, "B"), ("Other", 2)]

        placed = settings._after_first_group(own, [(first, 1), (second, 2)])

        self.assertEqual([one[0] if isinstance(one, tuple) else one for one in placed],
                         ["note", panel.HEADING, "Program", first, second, panel.HEADING,
                          "Other"])

    async def test_the_page_opens_with_a_heading_and_the_report_sits_under_it(self) -> None:
        self._reported(NO_H264)

        entries = await self._heads()

        self.assertIs(entries[0][0], panel.HEADING)
        self.assertLess(self._labels(entries).index(t("config.chromium.path.label")),
                        self._labels(entries).index(t("manager.settings.browser_video")))

    async def test_a_finding_leads_the_page_across_both_columns(self) -> None:
        self._reported(NO_H264)

        entries = await self._drawn()

        self.assertIs(entries[0][0], panel.FULL)

    async def test_formats_that_play_are_green_and_the_rest_are_not(self) -> None:
        self._reported(NO_H264)

        entries = await self._drawn()

        self.assertEqual(self._levels(entries, t("manager.settings.browser_video")),
                         {"H.264": "warn", "HEVC": "warn", "VP9": "on", "AV1": "on"})
        self.assertEqual(self._levels(entries, t("manager.settings.browser_audio")),
                         {"AAC": "warn", "MP3": "on", "Vorbis": "on", "Opus": "on"})

    async def test_nothing_that_plays_is_red_on_the_formats_it_cannot_do_without(self) -> None:
        self._reported(dict.fromkeys(NO_H264, False))

        entries = await self._drawn()

        self.assertEqual(self._levels(entries, t("manager.settings.browser_video")),
                         {"H.264": "bad", "HEVC": "warn", "VP9": "bad", "AV1": "warn"})

    async def test_a_browser_that_plays_everything_is_green_throughout(self) -> None:
        self._reported(PLAYS_ALL)

        entries = await self._drawn()

        self.assertIsNot(entries[0][0], panel.FULL)
        for label in ("browser_video", "browser_audio"):
            self.assertEqual(
                set(self._levels(entries, t(f"manager.settings.{label}")).values()), {"on"})

    async def test_a_format_the_page_did_not_answer_is_unknown_not_a_no(self) -> None:
        self._reported({**PLAYS_ALL, "av1": None})  # type: ignore[dict-item]

        entries = await self._drawn()

        self.assertEqual(self._levels(entries, t("manager.settings.browser_video"))["AV1"],
                         "unknown")

    async def test_the_browser_and_when_it_was_checked_are_on_each_chip_s_hover(self) -> None:
        self._reported(PLAYS_ALL)

        chips = self._value(await self._drawn(), t("manager.settings.browser_video"))

        self.assertTrue(all("Chromium 145.0.7632.0" in hint for _t, _l, hint in chips))

    def _program_field(self) -> dict:
        found = frontend_browser.current()
        [block] = [one for one in settings.with_browser_found(
            self.served.config_schema(), found) if one["name"] == "chromium"]
        return next(one for one in block["options"] if one["key"] == "path")

    async def test_an_empty_browser_program_stands_for_the_browser_it_found(self) -> None:
        self._reported(PLAYS_ALL)

        field = self._program_field()

        self.assertEqual(field["default_path"], str(self.in_use))
        self.assertEqual(field["default_whose"], t("manager.settings.browser_found_here"))

    async def test_a_set_browser_program_is_not_taken_for_the_default(self) -> None:
        elsewhere = self.in_use.parent / "found-elsewhere"
        elsewhere.touch()
        self.unset = elsewhere
        self._reported(PLAYS_ALL)

        field = self._program_field()

        self.assertEqual(field["default_path"], str(elsewhere))

    async def test_the_bundled_copy_is_said_by_name(self) -> None:
        self.bundled = True
        self._reported(PLAYS_ALL)

        field = self._program_field()

        self.assertEqual(field["default_path"], str(self.in_use))
        self.assertEqual(field["default_whose"], t("manager.settings.browser_bundled"))

    async def test_no_browser_at_all_reads_not_found_with_the_fix_on_hover(self) -> None:
        self.in_use.unlink()

        with patch.object(frontend_browser, "_variant", return_value=frontend_browser.FULL):
            field = self._program_field()

        self.assertEqual(field["blank"], t("word.not_found"))
        self.assertEqual(field["left_empty"], t("frontend_browser.fix.no_browser"))

    async def test_on_a_slim_build_the_hover_says_why_there_is_no_browser_of_its_own(
            self) -> None:
        self.in_use.unlink()

        with patch.object(frontend_browser, "_variant", return_value=frontend_browser.SLIM):
            field = self._program_field()

        self.assertEqual(field["left_empty"], t("manager.settings.browser_slim_build"))

    async def test_there_is_no_from_row(self) -> None:
        self._reported(PLAYS_ALL)

        self.assertNotIn("From", self._labels(await self._drawn()))

    async def test_a_browser_that_has_not_reported_shows_every_format_as_unknown_in_both_rows(
            self) -> None:
        entries = await self._drawn()

        for label, names in ((t("manager.settings.browser_video"), ["H.264", "HEVC", "VP9", "AV1"]),
                             (t("manager.settings.browser_audio"),
                              ["AAC", "MP3", "Vorbis", "Opus"])):
            self.assertEqual(self._levels(entries, label), dict.fromkeys(names, "unknown"))
        self.assertIsNot(entries[0][0], panel.FULL)

    async def test_the_not_checked_note_sits_under_the_video_row_only(self) -> None:
        notes: list[str] = []

        def record(chips: Any, note: str = "") -> list[Any]:
            notes.append(note)
            return list(chips)

        with patch.object(panel, "states", side_effect=record):
            await self._drawn()

        self.assertEqual(notes, [t("manager.settings.browser_not_checked"), ""])

    async def test_no_browser_at_all_is_one_chip_saying_so(self) -> None:
        self.in_use.unlink()

        entries = await self._drawn()

        [(text, level, _hint)] = self._value(entries, t("manager.settings.browser_video"))
        self.assertEqual((text, level), (t("manager.devices.browser_no_browser"), "bad"))

    async def test_a_report_on_another_browser_is_not_this_one_s(self) -> None:
        self._reported(NO_H264, path=self.in_use.with_name("elsewhere"))

        entries = await self._drawn()

        self.assertEqual(set(self._levels(entries, t("manager.settings.browser_audio")).values()),
                         {"unknown"})

    async def test_an_install_too_old_to_say_draws_its_settings_alone(self) -> None:
        with patch.object(self.served, "frontend_browser", return_value={}):
            labels = self._labels(await self._drawn())

        self.assertEqual(labels[0], t("config.chromium.path.label"))

    async def test_a_report_that_cannot_be_read_says_so_above_the_settings(self) -> None:
        with patch.object(self.served, "frontend_browser", side_effect=RuntimeError("gone")), \
                patch.object(panel, "line") as line:
            entries = await self._drawn()
            entries[0][1]()

        self.assertEqual(line.call_args.args[0], t("manager.settings.browser_failed"))
        self.assertIn(t("config.chromium.path.label"), self._labels(entries))

    async def test_use_google_chrome_sets_browser_program_and_restarts_nothing(
            self) -> None:
        self._reported(NO_H264)
        use = self._use_google_chrome(await self._drawn())

        await press(use)

        self.assertEqual(self._program(), CHROME)
        self.served.perform_action.assert_not_called()

    async def test_once_chosen_the_frontend_follows_and_nothing_is_offered_again(
            self) -> None:
        self._reported(NO_H264)
        await press(self._use_google_chrome(await self._drawn()))
        chrome = self.in_use.parent / "chrome"
        chrome.touch()
        self.chrome = str(chrome)
        self.in_use = chrome
        self.action.reset_mock()

        entries = await self._drawn()

        self.assertIsNot(entries[0][0], panel.FULL)
        self.assertFalse([call for call in self.action.call_args_list
                          if call.args[0] == t("manager.settings.use_chrome")])

    async def test_undo_puts_the_previous_browser_program_back(self) -> None:
        self.served.put_config({"chromium": {"path": str(self.in_use)}})
        self._reported(NO_H264)
        await press(self._use_google_chrome(await self._drawn()))
        [(said, reverse)] = [call.args for call in self.offer.call_args_list]

        await press(reverse)

        self.assertEqual(said, t("manager.settings.chrome_active"))
        self.assertEqual(self._program(), str(self.in_use))
        self.served.perform_action.assert_not_called()
        self.offer.assert_called_once()

    async def test_it_is_offered_only_where_chrome_is_installed_and_not_in_use(self) -> None:
        self.chrome = ""
        self._reported(NO_H264)

        entries = await self._drawn()

        self.assertIs(entries[0][0], panel.FULL)
        self.assertIsNone(self._use_google_chrome(entries))


    async def test_a_browser_without_h264_is_offered_recording_as_vp9(self) -> None:
        self._reported(NO_H264)
        self.ui.label.reset_mock()

        (await self._drawn())[0][1]()

        said = [call.args[0] for call in self.ui.label.call_args_list if call.args]
        self.assertIn(t("manager.recording.vp9_offer"), said)
        self.assertEqual(self.link.call_args.kwargs["to"],
                         settings.address_for("capture"))

    async def test_a_browser_that_plays_no_video_is_not_offered_vp9(self) -> None:
        self._reported(dict.fromkeys(NO_H264, False))
        self.ui.label.reset_mock()

        (await self._drawn())[0][1]()

        said = [call.args[0] for call in self.ui.label.call_args_list if call.args]
        self.assertNotIn(t("manager.recording.vp9_offer"), said)


class TooOldToSayTests(unittest.TestCase):
    def test_an_install_without_the_report_answers_empty_never_none(self) -> None:
        gone = requests.Response()
        gone.status_code = 404
        with patch.object(requests.adapters.HTTPAdapter, "send", return_value=gone):
            self.assertEqual(ApiClient("http://127.0.0.1:1").frontend_browser(), {})


if __name__ == "__main__":
    unittest.main()
