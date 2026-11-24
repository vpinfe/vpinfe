"""Which frontend devices won't play a video slot's file, and what the slot says of it."""

from __future__ import annotations

import unittest
from typing import Any, cast
from unittest.mock import Mock

import requests

from common.i18n import t
from manager.devices import plays_on
from manager.kit.data import Library

HERE = "Aaaa111111"
THERE = "Bbbb222222"
BROWSER_PAGE = "Settings › Frontend › Browser"


def _device(device_id: str, name: str, state: str, **formats: bool | None) -> dict:
    return {"device_id": device_id, "display_name": name,
            "browser": {"state": state, "name": "Chromium 145.0.7632.0", "formats": formats}}


def _playing(device: dict, library: str) -> dict:
    """The device, its frontend having said which library it plays."""
    return {**device, "browser": {**device["browser"], "library": library}}


NO_H264_HERE = _device(HERE, "Cab 1", "no_h264", h264=False, vp9=True, hevc=False)
PLAYS_THERE = _playing(_device(THERE, "Cab 2", "plays", h264=True, vp9=True, hevc=False), HERE)
NO_H264_THERE = _playing(_device(THERE, "Cab 2", "no_h264", h264=False, vp9=True), HERE)


class RefusalTests(unittest.TestCase):
    def _said(self, codec: str | None, *devices: dict) -> list[tuple[str, str]]:
        return [(one.label, one.why) for one in plays_on.refusals(codec, devices, HERE)]

    def test_a_device_whose_browser_said_no_is_named(self) -> None:
        self.assertEqual(
            self._said("h264", NO_H264_HERE, NO_H264_THERE),
            [(t("manager.plays_on.wont_play_on", device="Cab 1", codec="H.264"),
              t("manager.plays_on.why_fix_here", codec="H.264", place=BROWSER_PAGE)),
             (t("manager.plays_on.wont_play_on", device="Cab 2", codec="H.264"),
              t("manager.plays_on.why_fix_there", codec="H.264", place=BROWSER_PAGE))])

    def test_a_paired_device_that_plays_another_library_is_not_judged(self) -> None:
        elsewhere = _playing(NO_H264_THERE, "Cccc333333")

        self.assertEqual(self._said("h264", NO_H264_HERE, elsewhere),
                         [(t("manager.plays_on.wont_play_here", codec="H.264"),
                           t("manager.plays_on.why_fix_here", codec="H.264",
                             place=BROWSER_PAGE))])

    def test_a_device_that_has_not_said_which_library_it_plays_is_not_judged(self) -> None:
        silent = _device(THERE, "Cab 2", "no_h264", h264=False)

        self.assertEqual(self._said("h264", silent), [])

    def test_a_frontend_whose_library_did_not_answer_is_not_judged_this_install_included(
            self) -> None:
        unknown = _playing(NO_H264_HERE, "")
        elsewhere = _playing(NO_H264_THERE, "")

        self.assertEqual(self._said("h264", unknown, elsewhere), [])

    def test_this_installs_own_frontend_counts_until_it_says_it_plays_another_library(
            self) -> None:
        elsewhere = _playing(NO_H264_HERE, "Cccc333333")

        self.assertEqual(len(self._said("h264", NO_H264_HERE)), 1)
        self.assertEqual(self._said("h264", elsewhere), [])

    def test_the_why_says_when_the_device_last_reported(self) -> None:
        stale = _playing(_device(THERE, "Cab 2", "no_h264", h264=False), HERE)
        stale["browser"]["checked_at"] = "2026-10-01T12:00:00Z"

        [(_label, why)] = self._said("h264", stale)

        self.assertTrue(why.endswith(t("manager.plays_on.as_of",
                                       when=plays_on.when.local("2026-10-01T12:00:00Z"))))

    def test_on_an_install_that_knows_no_other_device_it_is_this_device(self) -> None:
        [(label, _why)] = self._said("h264", NO_H264_HERE)

        self.assertEqual(label, t("manager.plays_on.wont_play_here", codec="H.264"))

    def test_a_device_that_plays_it_says_nothing(self) -> None:
        self.assertEqual(self._said("h264", PLAYS_THERE), [])
        self.assertEqual(self._said("vp9", NO_H264_HERE, PLAYS_THERE), [])

    def test_a_format_nobody_answered_draws_nothing(self) -> None:
        unasked = _device(THERE, "Cab 2", "unknown", h264=None)

        self.assertEqual(self._said("h264", unasked, {"device_id": "Pppp333333"}), [])

    def test_a_file_whose_codec_is_not_known_draws_nothing(self) -> None:
        self.assertEqual(self._said(None, NO_H264_HERE, NO_H264_THERE), [])

    def test_a_codec_its_browser_settings_do_not_fix_gives_the_cause_alone(self) -> None:
        [(_label, why)] = self._said("hevc", NO_H264_HERE, _device(THERE, "Cab 2", "plays"))

        self.assertEqual(why, t("manager.plays_on.why", codec="HEVC"))


class AsksCodecsTests(unittest.TestCase):
    def test_only_a_no_to_a_video_format_is_worth_reading_codecs_for(self) -> None:
        self.assertFalse(plays_on.asks_codecs([
            _device(HERE, "Cab 1", "plays", h264=True, vp9=True, aac=False),
            _device(THERE, "Cab 2", "unknown", h264=None), {"device_id": "Pppp333333"}], HERE))
        self.assertTrue(plays_on.asks_codecs([NO_H264_THERE], HERE))

    def test_a_no_from_a_device_that_plays_another_library_asks_nothing(self) -> None:
        self.assertFalse(plays_on.asks_codecs([_playing(NO_H264_THERE, "Cccc333333")], HERE))


class LibraryTests(unittest.TestCase):
    def _library(self, **client: object) -> Library:
        library = Library.__new__(Library)
        library._client = Mock(**client)
        return library

    def test_only_the_videos_whose_codec_was_read_are_kept(self) -> None:
        library = self._library(media_codecs=Mock(return_value={
            "playfield_video": {"video_codec": "h264"}, "backglass_video": {"video_codec": None},
            "wheel": {"video_codec": None}}))

        self.assertEqual(library.video_codecs("g1", None), {"playfield_video": "h264"})
        cast(Any, library._client.media_codecs).assert_called_once_with("g1", "")

    def test_a_registry_that_cannot_be_read_is_no_devices(self) -> None:
        library = self._library(devices=Mock(side_effect=requests.ConnectionError("down")))

        self.assertEqual(library.known_devices(), [])


if __name__ == "__main__":
    unittest.main()
