"""What the page says when files it was sending stop arriving."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from common.i18n import t
from manager.library import uploads


class WhatAStoppedUploadSays(unittest.TestCase):
    def _notified(self, payload: dict) -> mock.Mock:
        with mock.patch.object(uploads, "context"), \
                mock.patch.object(uploads, "ui") as ui:
            uploads.listener(mock.Mock())(SimpleNamespace(args=payload))
        return ui.notify

    def test_an_install_that_could_not_be_reached_is_named(self) -> None:
        notify = self._notified({"status": "error", "unreached": "192.168.1.50"})

        notify.assert_called_once_with(
            t("manager.uploads.read_failed"),
            caption=t("said.why.unreachable_at", host="192.168.1.50"), type="negative")

    def test_an_install_that_refused_says_why_in_its_own_words(self) -> None:
        refused = t("error.uploads.over_size_limit", limit=4)

        notify = self._notified({"status": "error", "said": refused})

        notify.assert_called_once_with(t("manager.uploads.read_failed"),
                                       caption=refused, type="negative")

    def test_the_browser_s_own_words_are_never_shown(self) -> None:
        notify = self._notified({"status": "error", "message": "Failed to fetch"})

        notify.assert_called_once_with(t("manager.uploads.read_failed"), caption="",
                                       type="negative")

    def test_the_script_sends_no_exception_text(self) -> None:
        self.assertNotIn("err.message", uploads._DND_SCRIPT)

    def test_a_stopped_upload_is_logged_with_its_reason(self) -> None:
        with self.assertLogs(uploads.logger, "WARNING") as logged:
            self._notified({"status": "error", "said": "Too big", "message": "Failed to fetch"})

        self.assertIn("Too big", logged.output[0])
        self.assertIn("Failed to fetch", logged.output[0])


class WhatAFailedImportLeavesInTheLog(unittest.IsolatedAsyncioTestCase):
    """The toast is gone in seconds; the log is what says afterwards what went wrong."""

    def setUp(self) -> None:
        self.enterContext(mock.patch("manager.kit.on_page.ui"))
        self.ui = self.enterContext(mock.patch.object(uploads, "ui"))
        runner = self.enterContext(mock.patch.object(uploads, "run"))
        runner.io_bound = mock.AsyncMock()
        self.io = self.enterContext(mock.patch.object(uploads.offload, "io",
                                                      mock.AsyncMock()))
        self.library = mock.Mock(games=[])

    async def test_an_upload_that_could_not_be_read_is_logged(self) -> None:
        self.io.return_value = {"error": "Nothing in it was recognized"}

        with self.assertLogs(uploads.logger, "WARNING") as logged:
            self.assertIsNone(await uploads.analyzed(self.library, "u-1"))

        self.assertIn("u-1", logged.output[0])
        self.assertIn("Nothing in it was recognized", logged.output[0])

    async def test_unnamed_pictures_dropped_on_a_game_go_on_to_be_placed(self) -> None:
        self.io.return_value = {"error": t("error.uploads.nothing_recognized"),
                                "placeable": ["IMG_0001.png", "IMG_0002.jpg"]}

        found = await uploads.analyzed(self.library, "u-1", game_named=True)

        self.assertIsNotNone(found)
        self.assertNotIn("error", found)
        self.library.abort_upload.assert_not_called()

    async def test_files_with_no_place_dropped_on_a_game_are_still_turned_away(self) -> None:
        self.io.return_value = {"error": t("error.uploads.nothing_recognized"),
                                "placeable": []}

        self.assertIsNone(await uploads.analyzed(self.library, "u-1", game_named=True))

    async def test_unnamed_pictures_dropped_where_no_game_is_named_are_turned_away(self) -> None:
        self.io.return_value = {"error": t("error.uploads.nothing_recognized"),
                                "placeable": ["IMG_0001.png"]}

        self.assertIsNone(await uploads.analyzed(self.library, "u-1"))

    async def test_the_install_s_language_does_not_decide_whether_pictures_go_on(self) -> None:
        self.io.return_value = {"error": "Aucun fichier reconnu", "placeable": ["IMG_0001.png"]}

        self.assertIsNotNone(await uploads.analyzed(self.library, "u-1", game_named=True))

    async def test_a_drop_on_an_asset_cell_does_not_get_the_pictures_allowance(self) -> None:
        asked = mock.AsyncMock(return_value=None)
        with mock.patch.object(uploads, "analyzed", asked):
            await uploads.arrived(self.library, "u-1", source="drop", on_done=mock.Mock(),
                                  game_dir="/lib/Game", asset_kind="rom")
            await uploads.arrived(self.library, "u-2", source="drop", on_done=mock.Mock(),
                                  game_dir="/lib/Game")

        self.assertEqual([False, True],
                         [call.kwargs["game_named"] for call in asked.await_args_list])

    async def test_an_analysis_that_raised_is_logged_with_its_traceback(self) -> None:
        self.io.side_effect = RuntimeError("bad archive")

        with self.assertLogs(uploads.logger, "WARNING") as logged:
            await uploads.analyzed(self.library, "u-1")

        self.assertIn("bad archive", "\n".join(logged.output))

    async def test_an_upload_with_no_destination_is_logged(self) -> None:
        self.io.side_effect = RuntimeError("no room")

        with self.assertLogs(uploads.logger, "WARNING") as logged:
            await uploads.confirmed_import(self.library, "u-1", {}, source="a.zip",
                                           on_done=mock.Mock())

        self.assertIn("u-1", logged.output[0])
        self.assertIn("no room", "\n".join(logged.output))

    async def test_an_upload_with_nothing_to_import_is_logged(self) -> None:
        self.io.return_value = {"items": [], "blocked": []}

        with self.assertLogs(uploads.logger, "WARNING") as logged:
            await uploads.confirmed_import(self.library, "u-1", {}, source="a.zip",
                                           on_done=mock.Mock())

        self.assertIn(t("manager.uploads.nothing_import"), logged.output[0])


if __name__ == "__main__":
    unittest.main()
