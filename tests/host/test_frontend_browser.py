"""What the frontend's browser reports it can play, the state and fix read off it, and the
route and capability that serve it."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from starlette.testclient import TestClient

import httpapi
from common.config.config_access import NetworkConfig
from common.devices import device_client, device_registry, install_identity
from common.host import frontend_browser as fb

BUNDLED = "/opt/vpinfe/chromium/linux/chrome/chrome"
CHROME = "/usr/bin/google-chrome"

BUNDLED_145: dict[str, Any] = {
    "user_agent": "Mozilla/5.0 (X11; Linux x86_64) Chrome/145.0.0.0 Safari/537.36",
    "brands": [{"brand": "Chromium", "version": "145.0.7632.0"},
               {"brand": "Not.A/Brand", "version": "99.0.0.0"}],
    "can_play": {"h264": "", "hevc": "", "vp9": "probably", "av1": "probably", "aac": "",
                 "mp3": "probably", "vorbis": "probably", "opus": "probably"},
    "decoded": {"h264": False, "vp9": True},
}


class _Isolated(unittest.TestCase):
    """A report file of its own, a browser in use that exists, and no Chrome. The log is
    silent unless the test is about the log."""

    quiet = True

    def setUp(self) -> None:
        if self.quiet:
            silent = mock.patch.object(fb, "_log")
            silent.start()
            self.addCleanup(silent.stop)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = Path(tmp.name) / "cache" / "frontend_browser.json"
        self.browser = Path(tmp.name) / "chrome"
        self.browser.write_text("")
        device_registry.reset_for_tests(Path(tmp.name) / "devices.json")
        self.addCleanup(device_registry.reset_for_tests)
        for patch in (mock.patch.object(fb.paths, "FRONTEND_BROWSER_PATH", self.store),
                      mock.patch.object(fb, "_in_use", return_value=(str(self.browser), True)),
                      mock.patch.object(fb, "_google_chrome", return_value=""),
                      mock.patch.object(fb.platform, "system", return_value="Linux")):
            patch.start()
            self.addCleanup(patch.stop)
        fb.reset_for_tests()


class StateTests(unittest.TestCase):
    def test_h264_is_what_plays_means(self) -> None:
        self.assertEqual(fb.state_of({"h264": True, "vp9": False}), fb.PLAYS)

    def test_vp9_without_h264_is_no_h264(self) -> None:
        self.assertEqual(fb.state_of({"h264": False, "vp9": True}), fb.NO_H264)

    def test_neither_is_no_video(self) -> None:
        self.assertEqual(fb.state_of({"h264": False, "vp9": False}), fb.NO_VIDEO)

    def test_an_answer_that_did_not_come_is_unknown_not_no(self) -> None:
        for formats in ({}, {"h264": None, "vp9": False}, {"h264": False, "vp9": None}):
            self.assertEqual(fb.state_of(formats), fb.UNKNOWN, formats)


class FixTests(unittest.TestCase):
    def fix(self, state: str, system: str, bundled: bool, chrome: str = "") -> fb.Fix | None:
        return fb.fix_for(state, system=system, bundled=bundled, in_use=BUNDLED, chrome=chrome)

    def found_fix(self, state: str, system: str, bundled: bool, chrome: str = "") -> fb.Fix:
        found = self.fix(state, system, bundled, chrome)
        assert found is not None
        return found

    def test_nothing_to_fix_where_it_plays_or_has_not_said(self) -> None:
        self.assertIsNone(self.fix(fb.PLAYS, "Linux", True))
        self.assertIsNone(self.fix(fb.UNKNOWN, "Linux", True))

    def test_chrome_installed_and_not_in_use_is_one_press(self) -> None:
        found = self.found_fix(fb.NO_H264, "Windows", True, chrome=CHROME)
        self.assertEqual((found.action, found.chrome_path), (fb.USE_CHROME, CHROME))

    def test_chrome_already_in_use_is_not_offered_again(self) -> None:
        found = fb.fix_for(fb.NO_H264, system="Linux", bundled=False, in_use=CHROME,
                           chrome=CHROME)
        assert found is not None
        self.assertEqual(found.action, "")

    def test_windows_needs_the_setting_because_the_bundled_copy_wins(self) -> None:
        self.assertEqual(self.found_fix(fb.NO_H264, "Windows", True).key,
                         "frontend_browser.fix.set_chrome")

    def test_macos_needs_only_the_install(self) -> None:
        self.assertEqual(self.found_fix(fb.NO_H264, "Darwin", True).key,
                         "frontend_browser.fix.install_chrome_mac")

    def test_linux_with_the_bundled_copy_needs_only_the_install(self) -> None:
        self.assertEqual(self.found_fix(fb.NO_VIDEO, "Linux", True).key,
                         "frontend_browser.fix.install_chrome_linux")

    def test_linux_with_a_distribution_chromium_needs_the_setting(self) -> None:
        # `chromium` is found ahead of `google-chrome`, so installing Chrome changes nothing.
        self.assertEqual(self.found_fix(fb.NO_H264, "Linux", False).key,
                         "frontend_browser.fix.set_chrome")

    def test_no_browser_has_its_own_fix(self) -> None:
        self.assertEqual(self.found_fix(fb.NO_BROWSER, "Windows", False).key,
                         "frontend_browser.fix.no_browser")


class BrowserNameTests(unittest.TestCase):
    def test_the_made_up_brand_is_skipped(self) -> None:
        self.assertEqual(fb._browser_name(BUNDLED_145["brands"], ""), "Chromium 145.0.7632.0")

    def test_google_chrome_is_named_over_the_chromium_it_is_built_on(self) -> None:
        brands = [{"brand": "Not)A;Brand", "version": "8"},
                  {"brand": "Chromium", "version": "140.0.1"},
                  {"brand": "Google Chrome", "version": "140.0.1"}]
        self.assertEqual(fb._browser_name(brands, ""), "Google Chrome 140.0.1")

    def test_without_brands_the_user_agent_gives_the_major_version(self) -> None:
        self.assertEqual(fb._browser_name([], BUNDLED_145["user_agent"]), "Chromium 145")

    def test_nothing_readable_is_blank(self) -> None:
        self.assertEqual(fb._browser_name("junk", "curl/8"), "")


class VariantTests(_Isolated):
    def test_a_build_says_whether_it_ships_a_browser(self) -> None:
        for ships, variant in ((True, fb.FULL), (False, fb.SLIM)):
            with mock.patch.object(device_client.LocalDevice, "has_bundled_browser",
                                   return_value=ships):
                self.assertEqual(fb.current()["variant"], variant)


class RecordTests(_Isolated):
    def test_a_report_is_kept_and_read_back(self) -> None:
        answer = fb.record(BUNDLED_145)
        self.assertEqual(answer["state"], fb.NO_H264)
        self.assertEqual(answer["browser"], "Chromium 145.0.7632.0")
        self.assertEqual(answer["plays"], ["VP9", "AV1", "MP3", "Vorbis", "Opus"])
        self.assertEqual(answer["does_not_play"], ["H.264", "HEVC", "AAC"])
        self.assertTrue(self.store.is_file())
        self.assertEqual(fb.current()["state"], fb.NO_H264)

    def test_a_decode_that_ran_beats_can_play_type(self) -> None:
        # canPlayType says maybe, the real file shows no picture: no.
        answer = fb.record({**BUNDLED_145, "can_play": {"h264": "maybe"},
                            "decoded": {"h264": False, "vp9": True}})
        self.assertIs(answer["formats"]["h264"], False)

    def test_only_known_formats_are_kept(self) -> None:
        fb.record({**BUNDLED_145, "can_play": {"h264": "", "flac": "probably"}})
        held = json.loads(self.store.read_text(encoding="utf-8"))
        self.assertNotIn("flac", held["formats"])

    def test_something_that_is_not_a_report_is_unknown(self) -> None:
        self.assertEqual(fb.record("not a report")["state"], fb.UNKNOWN)

    def test_this_install_s_own_device_entry_keeps_it(self) -> None:
        registry = device_registry.get_device_registry()
        own = "Iiii111111"
        registry.record(own)

        with mock.patch.object(install_identity, "install_id", return_value=own):
            fb.record(BUNDLED_145)

        device = registry.get(own)
        assert device is not None
        kept = device.browser
        self.assertEqual((kept["state"], kept["name"]), (fb.NO_H264, "Chromium 145.0.7632.0"))
        self.assertEqual((kept["formats"]["h264"], kept["formats"]["vp9"]), (False, True))
        self.assertTrue(kept["checked_at"])

    def test_a_report_from_another_browser_is_no_answer(self) -> None:
        fb.record(BUNDLED_145)
        other = self.browser.with_name("other")
        other.write_text("")
        with mock.patch.object(fb, "_in_use", return_value=(str(other), False)):
            self.assertEqual(fb.current()["state"], fb.UNKNOWN)

    def test_a_browser_that_is_not_there_is_no_browser(self) -> None:
        self.browser.unlink()
        answer = fb.current()
        self.assertEqual(answer["state"], fb.NO_BROWSER)
        self.assertEqual(answer["fix"]["key"], "frontend_browser.fix.no_browser")

    def test_not_reported_yet_says_nothing_is_wrong(self) -> None:
        answer = fb.current()
        self.assertEqual((answer["state"], answer["finding"], answer["fix"]),
                         (fb.UNKNOWN, "", None))


class LogTests(_Isolated):
    quiet = False

    def test_once_per_browser_and_state_with_the_fix(self) -> None:
        with self.assertLogs(fb.logger, "INFO") as seen:
            fb.record(BUNDLED_145)
            fb.record(BUNDLED_145)
        infos = [line for line in seen.output if line.startswith("INFO")]
        warnings = [line for line in seen.output if line.startswith("WARNING")]
        self.assertEqual(len(infos), 1)
        self.assertIn("Chromium 145.0.7632.0 (bundled). Plays VP9, AV1", infos[0])
        self.assertEqual(len(warnings), 1)
        self.assertIn("Install Google Chrome, and VPinFE uses it from then on", warnings[0])

    def test_a_browser_that_plays_everything_gets_no_warning(self) -> None:
        plays = {**BUNDLED_145, "decoded": {"h264": True, "vp9": True}}
        with self.assertLogs(fb.logger, "INFO") as seen:
            fb.record(plays)
        self.assertFalse([line for line in seen.output if line.startswith("WARNING")])


class LibraryPlayedTests(_Isolated):
    """Which install's library this frontend plays, which is what lets another install's
    badge judge only the devices that use its media."""

    def setUp(self) -> None:
        super().setUp()
        install_identity.ensure_id()

    def _network(self, url: str) -> Any:
        patch = mock.patch.object(fb.serving, "network",
                                  return_value=NetworkConfig(library_url=url))
        patch.start()
        self.addCleanup(patch.stop)

    def _asks(self, **result: Any) -> Any:
        patch = mock.patch.object(fb.http_client, "get_json", **result)
        asked = patch.start()
        self.addCleanup(patch.stop)
        return asked

    def test_an_install_holding_its_library_plays_its_own(self) -> None:
        self._network("")

        self.assertEqual(fb.library_played(), install_identity.install_id())
        self.assertTrue(fb.library_played())

    def test_a_named_library_is_the_install_that_answered_for_it(self) -> None:
        self._network("http://nas:8001/")
        asked = self._asks(return_value={"install_id": "Rrrr111111"})

        self.assertEqual(fb.library_played(), "Rrrr111111")
        self.assertEqual(fb.library_played(), "Rrrr111111")
        self.assertEqual(asked.call_count, 1)
        self.assertEqual(asked.call_args.args[0], "http://nas:8001/api/v1")

    def test_a_library_that_does_not_answer_is_nobodys_until_it_is_asked_again(self) -> None:
        self._network("http://nas:8001")
        asked = self._asks(side_effect=OSError("down"))

        self.assertEqual(fb.library_played(), "")
        self.assertEqual(fb.library_played(), "")
        self.assertEqual(asked.call_count, 1)
        with mock.patch.object(fb.time, "monotonic", return_value=1e9):
            asked.side_effect = None
            asked.return_value = {"install_id": "Rrrr111111"}
            self.assertEqual(fb.library_played(), "Rrrr111111")

    def test_an_id_that_was_read_is_asked_for_again_after_a_while(self) -> None:
        self._network("http://nas:8001")
        asked = self._asks(return_value={"install_id": "Rrrr111111"})
        fb.library_played()
        asked.return_value = {"install_id": "Ssss222222"}

        self.assertEqual(fb.library_played(), "Rrrr111111")
        with mock.patch.object(fb.time, "monotonic", return_value=1e9):
            self.assertEqual(fb.library_played(), "Ssss222222")

    def test_a_library_that_did_not_answer_is_filed_as_empty_in_this_installs_entry(self) -> None:
        self._network("http://nas:8001")
        self._asks(side_effect=OSError("down"))
        fb.record(BUNDLED_145)

        held = device_registry.get_device_registry().get(install_identity.install_id())
        assert held is not None
        self.assertEqual(held.browser["library"], "")

    def test_an_answer_with_no_install_id_names_no_library(self) -> None:
        self._network("http://nas:8001")
        self._asks(return_value=["not", "a", "document"])

        self.assertEqual(fb.library_played(), "")

    def test_the_report_is_filed_with_the_library_in_this_installs_entry(self) -> None:
        self._network("")
        fb.record(BUNDLED_145)

        held = device_registry.get_device_registry().get(install_identity.install_id())
        assert held is not None
        self.assertEqual(held.browser["library"], install_identity.install_id())


class ServedTests(_Isolated):
    def test_the_route_names_the_library_the_frontend_plays(self) -> None:
        install_identity.ensure_id()
        self._own()
        client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)

        self.assertEqual(client.get("/frontend/browser").json()["library"],
                         install_identity.install_id())

    def _own(self) -> None:
        patch = mock.patch.object(fb.serving, "network",
                                  return_value=NetworkConfig(library_url=""))
        patch.start()
        self.addCleanup(patch.stop)

    def test_the_route_serves_the_report(self) -> None:
        fb.record(BUNDLED_145)
        client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)
        found = client.get("/frontend/browser")
        self.assertEqual(found.status_code, 200)
        body = found.json()
        self.assertEqual((body["state"], body["formats"]["vp9"]), (fb.NO_H264, True))
        self.assertEqual(body["finding"], "Most shared videos won't play in this browser")
        found_here = fb.current()
        self.assertTrue(found_here["default_path"])
        self.assertEqual((body["default_path"], body["default_bundled"]),
                         (found_here["default_path"], found_here["default_bundled"]))

    def test_capability_is_unavailable_only_where_nothing_plays(self) -> None:
        self.assertIs(fb.available(), True)
        fb.record({**BUNDLED_145, "decoded": {"h264": False, "vp9": False}})
        self.assertEqual(fb.available(), (False, "This browser can't play video"))
