"""What a device reports it can record, and why not."""

from __future__ import annotations

import ast
import configparser
import itertools
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from common.capture import adapters, commands, placing, preflight, settings
from common.capture.adapters import ffmpeg, portal, wlr
from common.host import frontend_browser, tools
from common.i18n import t
from tests.capture.test_adapters import SWAY_OUTPUTS
from tests.support import trees

REPO = Path(__file__).resolve().parents[2]

ENCODERS = frozenset({"libx264", "libvpx-vp9", "png", "libmp3lame", "h264_vaapi"})
ELEMENTS = frozenset(portal.PortalAdapter.NEEDS)
SOUND_ENV = {"PULSE_SERVER": "unix:/run/user/1000/pulse/native"}


class FakeAdapter(wlr.WlrAdapter):
    """sway's, with its outputs and its hardware encoder handed to it."""

    def __init__(self, outputs: Any = None, hardware: bool = True) -> None:
        super().__init__({})
        self._outputs = wlr.sway_outputs(SWAY_OUTPUTS) if outputs is None else outputs
        self.node = "/dev/dri/renderD128" if hardware else ""

    def outputs(self):
        if isinstance(self._outputs, Exception):
            raise self._outputs
        return self._outputs

    def at_once(self, ffmpeg):
        return bool(self.node)

    def hardware(self, ffmpeg):
        return self.node


def found(missing: tuple[str, ...] = (), encoders: frozenset[str] = ENCODERS,
          inputs: frozenset[str] = frozenset({"pulse"}),
          elements: frozenset[str] = ELEMENTS) -> dict[str, tools.Found]:
    out = {}
    for tool in (tools.FFMPEG, tools.GRIM, tools.WF_RECORDER, tools.GSTREAMER):
        if tool.id in missing:
            out[tool.id] = tools.Found(tool, tools.State.MISSING)
            continue
        can = {tools.ENCODERS: encoders, tools.INPUTS: inputs} if tool is tools.FFMPEG \
            else {tools.ELEMENTS: elements} if tool is tools.GSTREAMER else {}
        out[tool.id] = tools.Found(tool, tools.State.FOUND, Path(f"/usr/bin/{tool.id}"),
                                   tools.Probe(True, "1.0", can))
    return out


def config(**values: str) -> configparser.ConfigParser:
    held = configparser.ConfigParser()
    held["windows.playfield"] = {"screen_id": "0"}
    held["windows.backglass"] = {"screen_id": "1"}
    held["windows.scoreview"] = {"screen_id": "2"}
    held["capture"] = values
    return held


MONITORS = [SimpleNamespace(x=0, y=0, width=1080, height=1920, name="DP-1"),
            SimpleNamespace(x=1080, y=0, width=1920, height=1080, name="DP-2"),
            SimpleNamespace(x=3000, y=0, width=1920, height=1080, name="HDMI-A-1")]


def report(adapter: Any = None, held: Any = None, **kwargs: Any) -> dict[str, Any]:
    kwargs.setdefault("found", found())
    kwargs.setdefault("env", SOUND_ENV)
    kwargs.setdefault("browser_state", frontend_browser.PLAYS)
    # An app that says nothing, so no test reads this machine's own settings.
    kwargs.setdefault("shown", placing.Shown(""))
    with patch("common.host.vpinos.detected", return_value=False), \
            patch("common.host.tools.here", return_value=tools.LINUX):
        return preflight.report(adapter=FakeAdapter() if adapter is None else adapter,
                                config=config() if held is None else held,
                                monitors=MONITORS, **kwargs)


def _by_window(said: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {one["window"]: one for one in said["screens"]}


class ReportTests(unittest.TestCase):
    def test_a_cabinet_with_its_tools_records_every_screen_it_has(self) -> None:
        said = report()
        screens = _by_window(said)

        self.assertTrue(said["available"])
        self.assertIsNone(said["reason"])
        for window in ("playfield", "backglass", "scoreview"):
            with self.subTest(window):
                self.assertTrue(screens[window]["picture"]["available"])
                self.assertTrue(screens[window]["video"]["available"])
        self.assertEqual((screens["playfield"]["output"], screens["playfield"]["size"],
                          screens["playfield"]["surface"]), ("DP-1", [1080, 1920], "portrait"))
        self.assertTrue(said["sound"]["available"])
        self.assertTrue(said["at_once"])
        self.assertEqual([row["id"] for row in said["tools"]],
                         ["ffmpeg", "grim", "wf_recorder"])

    def test_the_topper_has_no_screen_to_record(self) -> None:
        topper = _by_window(report())["topper"]

        self.assertEqual(topper["picture"]["reason"]["key"], adapters.NO_SCREEN)
        self.assertEqual(topper["picture"]["reason"]["params"], {"window": "topper"})
        self.assertIsNone(topper["output"])

    def test_each_window_is_where_the_app_shows_it_and_one_it_shows_nowhere_says_so(
            self) -> None:
        said = report(shown=placing.Shown("Visual Pinball X", {
            "playfield": "HDMI-A-1", "backglass": "DP-1", "topper": ""}))
        screens = _by_window(said)

        self.assertEqual([screens[window]["output"] for window in adapters.WINDOWS],
                         ["HDMI-A-1", "DP-1", "HDMI-A-1", None])
        topper = screens["topper"]["video"]["reason"]
        self.assertEqual((topper["key"], topper["params"], topper["fix"]),
                         (placing.NOT_SHOWN, {"window": "topper", "app": "Visual Pinball X"},
                          tools.FIX_NONE))
        self.assertEqual(preflight.words(topper),
                         t(placing.NOT_SHOWN, app="Visual Pinball X",
                           window=t("media.kind.topper.label")))

    def test_without_wf_recorder_pictures_are_still_taken(self) -> None:
        playfield = _by_window(report(found=found(missing=("wf_recorder",))))["playfield"]

        self.assertTrue(playfield["picture"]["available"])
        blocked = playfield["video"]["reason"]
        self.assertEqual((blocked["key"], blocked["fix"]), (preflight.NEEDS_TOOL, "user"))
        self.assertEqual(blocked["remedy"]["setting"], "tools.wf_recorder_path")

    def test_nothing_recordable_says_the_first_reason(self) -> None:
        said = report(found=found(missing=("ffmpeg",)))

        self.assertFalse(said["available"])
        self.assertEqual(said["reason"]["params"], {"tool": "FFmpeg"})

    def test_automatic_records_what_this_devices_browser_plays(self) -> None:
        for state, codec in ((frontend_browser.PLAYS, settings.H264),
                             (frontend_browser.UNKNOWN, settings.H264),
                             (frontend_browser.NO_H264, settings.VP9),
                             (frontend_browser.NO_VIDEO, settings.VP9)):
            with self.subTest(state):
                self.assertEqual(report(browser_state=state)["video_codec"], codec)

    def test_a_format_chosen_outright_is_recorded_whatever_the_browser(self) -> None:
        said = report(held=config(video_codec="vp9"))

        self.assertEqual(said["video_codec"], settings.VP9)

    def test_an_ffmpeg_that_cannot_write_the_format_blocks_video_only(self) -> None:
        playfield = _by_window(report(
            held=config(video_codec="vp9"),
            found=found(encoders=ENCODERS - {"libvpx-vp9"})))["playfield"]

        self.assertTrue(playfield["picture"]["available"])
        self.assertEqual(playfield["video"]["reason"]["key"], preflight.NO_ENCODER)
        self.assertEqual(playfield["video"]["reason"]["params"], {"format": "VP9"})

    def test_a_desktop_that_does_not_answer_says_so_for_each_screen(self) -> None:
        said = report(FakeAdapter(OSError("refused")))

        self.assertEqual(_by_window(said)["playfield"]["video"]["reason"]["key"],
                         preflight.UNREADABLE)
        self.assertEqual(_by_window(said)["topper"]["video"]["reason"]["key"],
                         adapters.NO_SCREEN)

    def test_without_a_hardware_encoder_screens_record_in_turn(self) -> None:
        self.assertFalse(report(FakeAdapter(hardware=False))["at_once"])
        self.assertFalse(report(probe_hardware=False)["at_once"])

    def test_an_unsupported_session_reports_only_why(self) -> None:
        said = report(adapters.Unsupported("wayland", adapters.NO_WAY))

        self.assertFalse(said["available"])
        self.assertEqual((said["adapter"], said["screens"], said["tools"]),
                         ("wayland", [], []))
        self.assertEqual(preflight.words(said["reason"]), t(adapters.NO_WAY))
        self.assertEqual(said["commands"]["record"], "")

    def test_vpinfes_own_commands_are_this_devices(self) -> None:
        with_hardware = report()["commands"]
        without = report(FakeAdapter(hardware=False))["commands"]

        self.assertEqual(with_hardware["record"], "{recorder} {input} {hwaccel} -f {output}")
        self.assertIn("libx264", without["record"])
        self.assertEqual(with_hardware["encode"], commands.OWN_ENCODE)


class SoundTests(unittest.TestCase):
    def test_each_thing_sound_needs_is_its_own_reason(self) -> None:
        for name, kwargs, key in (
                ("no server", {"env": {}}, preflight.NO_SOUND_SERVER),
                ("no pulse input", {"found": found(inputs=frozenset())},
                 preflight.NO_SOUND_INPUT),
                ("no MP3 encoder", {"found": found(encoders=ENCODERS - {"libmp3lame"})},
                 preflight.NO_MP3)):
            with self.subTest(name):
                sound = report(**kwargs)["sound"]

                self.assertFalse(sound["available"])
                self.assertEqual(sound["reason"]["key"], key)

    def test_pipewires_pulse_socket_is_a_sound_server(self) -> None:
        with patch("os.path.exists", return_value=True):
            self.assertTrue(preflight.sound_server({"XDG_RUNTIME_DIR": "/run/user/1000"}))
        self.assertFalse(preflight.sound_server({}))


def _reasons(value: Any) -> list[dict[str, Any]]:
    """Every reason anywhere in a report."""
    if isinstance(value, dict):
        own = [value] if {"key", "fix"} <= set(value) else []
        return own + [one for inner in value.values() for one in _reasons(inner)]
    if isinstance(value, list):
        return [one for inner in value for one in _reasons(inner)]
    return []


def _blocked_reports() -> list[dict[str, Any]]:
    """A report for each way recording is blocked, and each pair of them."""
    ways: list[dict[str, Any]] = [{"found": found(missing=(tool,))}
                                  for tool in ("ffmpeg", "grim", "wf_recorder")]
    ways += [{"found": found(encoders=ENCODERS - {encoder})}
             for encoder in ("libx264", "png", "libmp3lame")]
    ways += [{"held": config(video_codec="vp9"),
              "found": found(encoders=ENCODERS - {"libvpx-vp9"})},
             {"found": found(inputs=frozenset())}, {"env": {}},
             {"adapter": FakeAdapter(OSError())}, {"adapter": FakeAdapter([])},
             {"shown": placing.Shown("Visual Pinball X", {"backglass": "", "topper": ""})}]
    ways += [{"adapter": adapters.resolve(env, tools.LINUX)}
             for env in ({"WAYLAND_DISPLAY": "w"}, {})]
    ways += [{"adapter": portal.PortalAdapter("KDE Plasma", lambda: MONITORS,
                                              kept_at=Path("/nonexistent/portal.json"))},
             {"adapter": portal_with_a_choice(),
              "found": found(elements=ELEMENTS - {"pipewiresrc"})}]
    ways += [{"adapter": ffmpeg.MacAdapter(mac_displays, lambda: False)},
             {"adapter": ffmpeg.MacAdapter(mac_displays, lambda: True)},
             {"adapter": ffmpeg.MacAdapter(mac_asleep, lambda: True)},
             {"adapter": ffmpeg.WindowsAdapter(dxgi_outputs, found()["ffmpeg"])},
             {"adapter": ffmpeg.X11Adapter({"DISPLAY": ":0"}, lambda: MONITORS)}]
    return [report(**{**first, **second})
            for first, second in itertools.combinations_with_replacement(ways, 2)]


def portal_with_a_choice() -> portal.PortalAdapter:
    """KDE Plasma's portal, told once which screens VPinFE may record."""
    adapter = portal.PortalAdapter("KDE Plasma", lambda: MONITORS)
    adapter.refused = lambda: None  # type: ignore[method-assign]
    return adapter


def mac_displays() -> list[ffmpeg.MacDisplay]:
    return [ffmpeg.MacDisplay(index, one.x, one.y, one.width, one.height,
                              (one.width, one.height), 60.0)
            for index, one in enumerate(MONITORS)]


def mac_asleep() -> list[ffmpeg.MacDisplay]:
    raise adapters.AsleepError


def dxgi_outputs() -> list[ffmpeg.DxgiOutput]:
    return [ffmpeg.DxgiOutput(index, one.name, one.x, one.y, one.x + one.width,
                              one.y + one.height, True, 1)
            for index, one in enumerate(MONITORS)]


def _capture_keys_in_source() -> set[str]:
    """Every catalog key common/capture names outright."""
    found_keys = set()
    for path in sorted((REPO / "common" / "capture").rglob("*.py")):
        for node in ast.walk(trees.tree_for(path)):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                    and node.value.startswith("capture.") and " " not in node.value:
                found_keys.add(node.value)
    return found_keys


class ReasonCheckTests(unittest.TestCase):
    def test_every_capture_key_the_code_names_resolves(self) -> None:
        keys = _capture_keys_in_source() | set(preflight.REASONS)

        self.assertTrue(keys)
        self.assertEqual(sorted(key for key in keys if t(key) == key), [])

    def test_every_reason_in_every_blocked_report_resolves_with_its_remedy(self) -> None:
        seen = set()
        for said in _blocked_reports():
            for one in _reasons(said):
                seen.add(one["key"])
                with self.subTest(one["key"]):
                    self.assertNotEqual(t(one["key"], **one["params"]), one["key"])
                    if one["fix"] == tools.FIX_USER:
                        self.assertTrue(one["remedy"], one)
                        self.assertNotEqual(tools.words(one["remedy"]),
                                            one["remedy"]["key"])
                        self.assertIn(tools.words(one["remedy"]), preflight.words(one))
        self.assertEqual(seen, set(preflight.REASONS))


class CapabilityTests(unittest.TestCase):
    def test_it_is_available_when_anything_can_be_recorded(self) -> None:
        with patch.object(preflight, "report", return_value={"available": True}):
            self.assertIs(preflight.available(), True)

    def test_otherwise_it_says_the_first_reason_with_its_remedy(self) -> None:
        blocked = report(found=found(missing=("ffmpeg",)))
        with patch.object(preflight, "report", return_value=blocked):
            result = preflight.available()

        assert isinstance(result, tuple)
        available, said = result
        self.assertFalse(available)
        self.assertEqual(said, preflight.words(blocked["reason"]))
        self.assertIn(t(preflight.NEEDS_TOOL, tool="FFmpeg"), said)


if __name__ == "__main__":
    unittest.main()
