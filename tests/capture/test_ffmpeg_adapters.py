"""Windows, macOS and X11: the screens each reports, what FFmpeg is run with to picture and
record one, which way its frames come, and what it says it cannot do."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from common.capture import adapters, commands, geometry, preflight
from common.capture.adapters import ffmpeg
from common.capture.geometry import Turn
from common.host import tools
from tests.capture.test_preflight import MONITORS, config, report

FFMPEG = Path("/usr/bin/ffmpeg")


def _ffmpeg(*, inputs: frozenset[str] = frozenset(), filters: frozenset[str] = frozenset(),
            encoders: frozenset[str] = frozenset({"libx264", "png"})) -> tools.Found:
    return tools.Found(tools.FFMPEG, tools.State.FOUND, FFMPEG, tools.Probe(
        True, "9.0.2", {tools.ENCODERS: encoders, tools.INPUTS: inputs,
                        tools.FILTERS: filters}))


def _found(ffmpeg_found: tools.Found) -> dict[str, tools.Found]:
    return {tools.FFMPEG.id: ffmpeg_found}


class X11Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.adapter = ffmpeg.X11Adapter({"DISPLAY": ":1"}, lambda: MONITORS)

    def test_the_outputs_are_the_display_models_monitors_by_name_and_place(self) -> None:
        outputs = self.adapter.outputs()

        self.assertEqual([(one.name, one.x, one.width, one.height, one.index)
                          for one in outputs],
                         [("DP-1", 0, 1080, 1920, 0), ("DP-2", 1080, 1920, 1080, 1),
                          ("HDMI-A-1", 3000, 1920, 1080, 2)])
        self.assertTrue(all(one.transform == geometry.NONE for one in outputs))

    def test_a_picture_is_one_frame_of_that_monitors_part_of_the_display(self) -> None:
        output = self.adapter.outputs()[1]

        self.assertEqual(self.adapter.still(_found(_ffmpeg()), output, Path("/w/bg.png")),
                         [str(FFMPEG), "-hide_banner", "-loglevel", "error", "-y",
                          "-f", "x11grab", "-framerate", "60", "-draw_mouse", "0",
                          "-video_size", "1920x1080", "-i", ":1.0+1080,0",
                          "-frames:v", "1", "-update", "1", str(Path("/w/bg.png"))])

    def test_x11grab_reads_the_screen_as_shown(self) -> None:
        output = self.adapter.outputs()[0]

        self.assertEqual((self.adapter.still_turn(output), self.adapter.recording_turn(output)),
                         (geometry.NONE, geometry.NONE))

    def test_it_needs_an_ffmpeg_that_reads_x11(self) -> None:
        self.assertTrue(self.adapter.grabs(_ffmpeg(inputs=frozenset({"x11grab"}))))
        self.assertFalse(self.adapter.grabs(_ffmpeg(inputs=frozenset({"pulse"}))))

    def test_va_api_is_its_hardware_encoder_as_on_wayland(self) -> None:
        with patch.object(ffmpeg, "vaapi_node", return_value="/dev/dri/renderD128"):
            self.assertEqual(self.adapter.hardware(_ffmpeg()), "/dev/dri/renderD128")
            self.assertTrue(self.adapter.at_once(_ffmpeg()))

    def test_sound_is_pulseaudios_as_on_wayland(self) -> None:
        self.assertIsNone(self.adapter.no_sound())


# Three outputs of the first adapter: a portrait playfield turned by Windows, and two as
# they are, one of them not on the desktop.
DXGI = [ffmpeg.DxgiOutput(0, "\\\\.\\DISPLAY2", 1920, 0, 3000, 1920, True, 4),
        ffmpeg.DxgiOutput(1, "\\\\.\\DISPLAY1", 0, 0, 1920, 1080, True, 1),
        ffmpeg.DxgiOutput(2, "\\\\.\\DISPLAY3", 3000, 0, 4280, 390, False, 1)]


class WindowsTests(unittest.TestCase):
    def _adapter(self, **probe: Any) -> ffmpeg.WindowsAdapter:
        return ffmpeg.WindowsAdapter(lambda: DXGI, _ffmpeg(**probe), lambda _: True)

    def test_each_attached_output_is_ddagrabs_index_by_windows_own_name(self) -> None:
        outputs = self._adapter().outputs()

        self.assertEqual([(one.name, one.index, one.x, one.width, one.height, one.mode)
                          for one in outputs],
                         [("\\\\.\\DISPLAY2", 0, 1920, 1080, 1920, (1920, 1080)),
                          ("\\\\.\\DISPLAY1", 1, 0, 1920, 1080, (1920, 1080))])

    def test_each_dxgi_rotation_is_the_turn_from_the_frame_to_the_screen(self) -> None:
        for rotation, turn in ((0, geometry.NONE), (1, geometry.NONE), (2, Turn(ccw=270)),
                               (3, Turn(ccw=180)), (4, Turn(ccw=90)), (9, geometry.NONE)):
            with self.subTest(rotation):
                said = [ffmpeg.DxgiOutput(0, "D", 0, 0, 1080, 1920, True, rotation)]

                self.assertEqual(ffmpeg.windows_outputs(said)[0].transform, turn)

    def test_ddagrab_is_used_where_the_ffmpeg_has_it_and_gdigrab_where_not(self) -> None:
        cases = {"ddagrab and gdigrab": ({"filters": frozenset({"ddagrab"}),
                                          "inputs": frozenset({"gdigrab"})}, "ddagrab", True),
                 "gdigrab alone": ({"inputs": frozenset({"gdigrab"})}, "gdigrab", True),
                 "neither": ({}, "ddagrab", False)}
        for name, (probe, expected, grabs) in cases.items():
            with self.subTest(name):
                adapter = self._adapter(**probe)
                assert adapter._ffmpeg is not None

                self.assertEqual(adapter.id, expected)
                self.assertEqual(adapter.grabs(adapter._ffmpeg), grabs)

    def test_gdigrab_is_chosen_up_front_where_ddagrab_cannot_take_a_frame(self) -> None:
        both = {"filters": frozenset({"ddagrab"}), "inputs": frozenset({"gdigrab"})}
        adapter = ffmpeg.WindowsAdapter(lambda: DXGI, _ffmpeg(**both), lambda _: False)
        assert adapter._ffmpeg is not None
        argv = adapter.still(_found(adapter._ffmpeg), adapter.outputs()[1], Path("pf.png"))

        self.assertEqual(adapter.id, "gdigrab")
        self.assertEqual(argv[5:7], ["-f", "gdigrab"])

    def test_ddagrab_is_not_tried_where_there_is_no_gdigrab_to_fall_back_on(self) -> None:
        tried: list[tools.Found] = []
        for probe in ({"filters": frozenset({"ddagrab"})}, {"inputs": frozenset({"gdigrab"})}):
            def probe_fails(found: tools.Found) -> bool:
                tried.append(found)
                return False

            adapter = ffmpeg.WindowsAdapter(lambda: DXGI, _ffmpeg(**probe), probe_fails)

            self.assertEqual(adapter.id, "gdigrab" if "inputs" in probe else "ddagrab")

        self.assertEqual(tried, [])

    def test_ddagrabs_frame_is_the_outputs_before_windows_turns_it(self) -> None:
        adapter = self._adapter(filters=frozenset({"ddagrab"}))
        playfield = adapter.outputs()[0]

        self.assertEqual(adapter.recording_turn(playfield), Turn(ccw=90))
        self.assertEqual(adapter.still_turn(playfield), Turn(ccw=90))

    def test_gdigrab_reads_the_desktop_as_shown(self) -> None:
        adapter = self._adapter(inputs=frozenset({"gdigrab"}))

        self.assertEqual(adapter.recording_turn(adapter.outputs()[0]), geometry.NONE)

    def test_a_picture_is_one_frame_brought_off_the_graphics_card(self) -> None:
        adapter = self._adapter(filters=frozenset({"ddagrab"}))
        assert adapter._ffmpeg is not None
        argv = adapter.still(_found(adapter._ffmpeg), adapter.outputs()[1], Path("pf.png"))

        self.assertEqual(argv[5:9], ["-f", "lavfi", "-i", "ddagrab=output_idx=1:framerate="
                                     "60:draw_mouse=0,hwdownload,format=bgra"])
        self.assertEqual(argv[-5:], ["-frames:v", "1", "-update", "1", "pf.png"])

    def test_nvenc_then_amf_is_the_hardware_encoder_that_encodes_a_frame(self) -> None:
        adapter = self._adapter()
        assert adapter._ffmpeg is not None
        for works, expected in (({"h264_nvenc", "h264_amf"}, "h264_nvenc"),
                                ({"h264_amf"}, "h264_amf"), (set(), "")):
            with (self.subTest(works=sorted(works)),
                  patch.object(ffmpeg, "encodes",
                               side_effect=lambda _found, name, w=works: name in w)):
                self.assertEqual(adapter.hardware(adapter._ffmpeg), expected)

    def test_sound_is_not_supported_yet(self) -> None:
        self.assertEqual(self._adapter().no_sound(),
                         (adapters.SOUND_NOT_YET, {"desktop": "Windows"}))


# Core Graphics' own order; the second display is the portrait playfield at the left.
DISPLAYS = [ffmpeg.MacDisplay(0, 0, 0, 1512, 982, (3024, 1964), 0.0),
            ffmpeg.MacDisplay(1, -1080, 0, 1080, 1920, (1080, 1920), 60.0)]


class MacTests(unittest.TestCase):
    def _adapter(self, allowed: bool = True) -> ffmpeg.MacAdapter:
        return ffmpeg.MacAdapter(lambda: DISPLAYS, lambda: allowed)

    def test_each_display_is_the_capture_screen_avfoundation_numbers_it(self) -> None:
        outputs = self._adapter().outputs()

        self.assertEqual([(one.name, one.index, one.x, one.width, one.mode, one.refresh)
                          for one in outputs],
                         [("Capture screen 0", 0, 0, 1512, (3024, 1964), 0.0),
                          ("Capture screen 1", 1, -1080, 1080, (1080, 1920), 60.0)])

    def test_a_picture_names_the_screen_so_a_camera_cannot_take_its_number(self) -> None:
        adapter = self._adapter()
        argv = adapter.still(_found(_ffmpeg()), adapter.outputs()[1], Path("pf.png"))

        self.assertEqual(argv[5:13], ["-f", "avfoundation", "-framerate", "60",
                                      "-capture_cursor", "0", "-i", "Capture screen 1:none"])

    def test_a_display_that_says_no_refresh_is_recorded_at_60(self) -> None:
        self.assertEqual(commands.rate(self._adapter().outputs()[0]), 60)

    def test_the_permission_is_macos_own_answer(self) -> None:
        self.assertIsNone(self._adapter(allowed=True).refused())
        self.assertEqual(self._adapter(allowed=False).refused(),
                         (adapters.SCREEN_PERMISSION, {}))

    def test_sound_needs_a_loopback_device(self) -> None:
        self.assertEqual(self._adapter().no_sound(), (adapters.SOUND_LOOPBACK, {}))

    def test_videotoolbox_is_the_hardware_encoder_where_a_frame_goes_through_it(self) -> None:
        for works, expected in ((True, "h264_videotoolbox"), (False, "")):
            with self.subTest(works), patch.object(ffmpeg, "encodes", return_value=works):
                self.assertEqual(self._adapter().hardware(_ffmpeg()), expected)


class EncodesTests(unittest.TestCase):
    def setUp(self) -> None:
        ffmpeg.reset_for_tests()
        self.addCleanup(ffmpeg.reset_for_tests)
        self.asked: list[list[str]] = []

    def _run(self, code: int) -> Any:
        def run(argv: list[str], **kwargs: Any) -> Any:
            self.asked.append(argv)
            self.assertEqual(kwargs["creationflags"], tools.NO_WINDOW)
            return SimpleNamespace(returncode=code)
        return run

    def test_one_frame_is_encoded_to_nowhere_once_per_ffmpeg_and_encoder(self) -> None:
        listed = _ffmpeg(encoders=frozenset({"h264_nvenc"}))

        self.assertTrue(ffmpeg.encodes(listed, "h264_nvenc", self._run(0)))
        self.assertTrue(ffmpeg.encodes(listed, "h264_nvenc", self._run(1)))
        self.assertEqual(self.asked, [ffmpeg.encode_probe(FFMPEG, "h264_nvenc")])
        self.assertEqual(self.asked[0][-4:], ["h264_nvenc", "-f", "null", "-"])

    def test_listed_is_not_enough(self) -> None:
        self.assertFalse(ffmpeg.encodes(_ffmpeg(encoders=frozenset({"h264_amf"})),
                                        "h264_amf", self._run(1)))

    def test_an_encoder_the_build_lacks_is_never_run(self) -> None:
        self.assertFalse(ffmpeg.encodes(_ffmpeg(), "h264_nvenc", self._run(0)))
        self.assertEqual(self.asked, [])

    def test_one_that_cannot_start_is_no(self) -> None:
        def cannot(*_args: Any, **_kwargs: Any) -> Any:
            raise OSError

        self.assertFalse(ffmpeg.encodes(_ffmpeg(encoders=frozenset({"h264_amf"})),
                                        "h264_amf", cannot))


class StoppingTests(unittest.TestCase):
    """Windows gives a child no SIGINT: there FFmpeg is asked on its stdin."""

    class Process:
        signals: list[int]
        returncode: int | None

        def __init__(self, stdin: Any = None) -> None:
            self.stdin, self.signals, self.returncode = stdin, [], None

        def poll(self) -> int | None:
            return self.returncode

        def send_signal(self, sig: int) -> None:
            self.signals.append(sig)

        def wait(self, timeout: float | None = None) -> int:
            self.open_while_waited = self.stdin is not None and not self.stdin.closed
            self.returncode = 0
            return 0

    class Pipe:
        def __init__(self) -> None:
            self.written, self.flushed, self.closed = b"", False, False

        def write(self, data: bytes) -> None:
            self.written += data

        def flush(self) -> None:
            self.flushed = True

        def close(self) -> None:
            self.closed = True

    def test_a_recorder_with_its_stdin_open_is_asked_to_quit_there(self) -> None:
        pipe = self.Pipe()
        process = self.Process(pipe)

        adapters.Recording("playfield", Path("a.mkv"), process, 0.0).stop()

        self.assertEqual((pipe.written, pipe.flushed, pipe.closed), (b"q", True, True))
        self.assertTrue(process.open_while_waited)
        self.assertEqual(process.signals, [])

    def test_elsewhere_it_is_interrupted(self) -> None:
        process = self.Process()

        adapters.Recording("playfield", Path("a.mkv"), process, 0.0).stop()

        self.assertEqual(len(process.signals), 1)

    def test_a_recorder_is_started_so_it_can_be_stopped_and_opens_no_window(self) -> None:
        for where, stdin in ((tools.WINDOWS, subprocess.PIPE), (tools.LINUX, subprocess.DEVNULL),
                             (tools.DARWIN, subprocess.DEVNULL)):
            with self.subTest(where), patch.object(tools, "here", return_value=where):
                started: dict[str, Any] = {}

                adapters.spawn(lambda argv, held=started, **kwargs: held.update(kwargs),
                               ["ffmpeg"], stderr="log")

                self.assertEqual((started["stdin"], started["creationflags"],
                                  started["stdout"], started["stderr"]),
                                 (stdin, tools.NO_WINDOW, subprocess.DEVNULL, "log"))


class ReportTests(unittest.TestCase):
    """What each says a device can record."""

    def _screens(self, said: dict[str, Any]) -> dict[str, Any]:
        return {one["window"]: one for one in said["screens"]}

    def test_macos_without_the_permission_records_nothing_and_says_how_to_allow_it(
            self) -> None:
        adapter = ffmpeg.MacAdapter(lambda: [ffmpeg.MacDisplay(
            index, one.x, one.y, one.width, one.height, (one.width, one.height), 60.0)
            for index, one in enumerate(MONITORS)], lambda: False)
        mac = _found(_ffmpeg(inputs=frozenset({"avfoundation"}),
                             encoders=frozenset({"libx264", "png"})))

        said = report(adapter, found=mac)

        self.assertFalse(said["available"])
        self.assertEqual(said["reason"]["key"], adapters.SCREEN_PERMISSION)
        self.assertEqual(said["reason"]["fix"], tools.FIX_USER)
        self.assertEqual(said["reason"]["remedy"], preflight.REMEDIES[
            adapters.SCREEN_PERMISSION])
        self.assertEqual(said["sound"]["reason"]["key"], adapters.SOUND_LOOPBACK)
        self.assertEqual(said["tools"][0]["id"], "ffmpeg")

    def test_macos_with_every_display_asleep_says_the_screens_are_asleep(self) -> None:
        def sleeping() -> list[ffmpeg.MacDisplay]:
            raise adapters.AsleepError

        mac = _found(_ffmpeg(inputs=frozenset({"avfoundation"})))

        said = report(ffmpeg.MacAdapter(sleeping, lambda: True), found=mac,
                      probe_hardware=False)

        self.assertFalse(said["available"])
        self.assertEqual(said["reason"]["key"], adapters.ASLEEP)

    def test_core_graphics_listing_no_active_display_while_all_sleep_is_asleep(self) -> None:
        quartz = SimpleNamespace(
            CGGetActiveDisplayList=lambda *_: (0, [], 0),
            CGGetOnlineDisplayList=lambda *_: (0, [1, 2], 2),
            CGDisplayIsAsleep=lambda _: True)

        with patch.dict("sys.modules", {"Quartz": quartz}):
            with self.assertRaises(adapters.AsleepError):
                ffmpeg.mac_displays()

    def test_core_graphics_with_no_display_at_all_is_not_asleep(self) -> None:
        quartz = SimpleNamespace(
            CGGetActiveDisplayList=lambda *_: (0, [], 0),
            CGGetOnlineDisplayList=lambda *_: (0, [], 0),
            CGDisplayIsAsleep=lambda _: True)

        with patch.dict("sys.modules", {"Quartz": quartz}):
            self.assertEqual(ffmpeg.mac_displays(), [])

    def test_macos_with_it_records_each_screen_placed_by_the_display_model(self) -> None:
        adapter = ffmpeg.MacAdapter(lambda: [ffmpeg.MacDisplay(
            index, one.x, one.y, one.width, one.height, (one.width, one.height), 60.0)
            for index, one in enumerate(MONITORS)], lambda: True)
        mac = _found(_ffmpeg(inputs=frozenset({"avfoundation"})))

        screens = self._screens(report(adapter, found=mac, probe_hardware=False))

        self.assertEqual(screens["playfield"]["output"], "Capture screen 0")
        self.assertTrue(screens["playfield"]["video"]["available"])
        self.assertEqual(screens["scoreview"]["output"], "Capture screen 2")

    def test_an_ffmpeg_that_cannot_read_the_screens_says_so_with_its_remedy(self) -> None:
        adapter = ffmpeg.X11Adapter({"DISPLAY": ":0"}, lambda: MONITORS)

        said = report(adapter, found=_found(_ffmpeg(inputs=frozenset({"pulse"}))),
                      probe_hardware=False)

        self.assertEqual(said["reason"]["key"], preflight.NO_GRABBER)
        self.assertEqual(said["reason"]["remedy"]["setting"], tools.FFMPEG.option)

    def test_x11_records_every_screen_with_sound_from_pulseaudio(self) -> None:
        adapter = ffmpeg.X11Adapter({"DISPLAY": ":0"}, lambda: MONITORS)
        x11 = _found(_ffmpeg(inputs=frozenset({"x11grab", "pulse"}),
                             encoders=frozenset({"libx264", "png", "libmp3lame"})))

        said = report(adapter, found=x11, held=config(), probe_hardware=False)

        self.assertTrue(said["available"])
        self.assertTrue(said["sound"]["available"])
        self.assertEqual(said["commands"][commands.RECORD],
                         commands.own_record(commands.X11GRAB, x11, ""))

    def test_windows_says_sound_is_not_supported_yet(self) -> None:
        adapter = ffmpeg.WindowsAdapter(lambda: DXGI, _ffmpeg(filters=frozenset({"ddagrab"})),
                                        lambda _: True)

        assert adapter._ffmpeg is not None
        said = report(adapter, found=_found(adapter._ffmpeg), probe_hardware=False)

        self.assertEqual(said["sound"]["reason"]["key"], adapters.SOUND_NOT_YET)
        self.assertEqual(said["adapter"], "ddagrab")


class DdagrabWorksTests(unittest.TestCase):
    def setUp(self) -> None:
        ffmpeg._ddagrab_works.clear()
        self.addCleanup(ffmpeg._ddagrab_works.clear)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.program = Path(folder.name) / "ffmpeg.exe"
        self.program.write_bytes(b"one")
        self.found = tools.Found(tools.FFMPEG, tools.State.FOUND, self.program, None)

    def _ran(self, **said: Any) -> tuple[bool, list[list[str]]]:
        ran: list[list[str]] = []

        def run(argv: list[str], **_: Any) -> Any:
            ran.append(argv)
            if "raises" in said:
                raise said["raises"]
            return SimpleNamespace(returncode=said.get("returncode", 0),
                                   stderr=said.get("stderr", ""))
        return ffmpeg.ddagrab_works(self.found, run), ran

    def _asked(self, times: int, **said: Any) -> int:
        count = 0
        for _ in range(times):
            count += len(self._ran(**said)[1])
        return count

    def test_a_frame_that_comes_out_is_a_ddagrab_that_works(self) -> None:
        works, ran = self._ran()

        self.assertTrue(works)
        self.assertEqual(ran[0][-5:], ["-frames:v", "1", "-f", "null", "-"])
        self.assertIn("ddagrab=output_idx=0:framerate=1:draw_mouse=0,hwdownload,format=bgra",
                      ran[0])

    def test_a_failed_frame_or_a_program_that_will_not_run_is_one_that_does_not(self) -> None:
        answers: dict[str, dict[str, Any]] = {
            "fails": {"returncode": 1, "stderr": "Failed to create D3D11VA"},
            "will not start": {"raises": OSError("nope")},
            "hangs": {"raises": subprocess.TimeoutExpired("ffmpeg", 10)}}
        for name, said in answers.items():
            with self.subTest(name):
                ffmpeg._ddagrab_works.clear()

                self.assertFalse(self._ran(**said)[0])

    def test_a_machine_with_no_direct3d_device_is_asked_once(self) -> None:
        self.assertEqual(self._asked(3, returncode=1, stderr="Failed to create D3D11VA device."), 1)

    def test_a_grab_that_works_is_asked_once(self) -> None:
        self.assertEqual(self._asked(3), 1)

    def test_any_other_failure_is_asked_again_after_a_minute(self) -> None:
        said = {"returncode": 1, "stderr": "Access is denied."}
        now = [1000.0]
        with patch.object(ffmpeg.time, "monotonic", lambda: now[0]):
            first = self._asked(2, **said)
            now[0] += ffmpeg.DDAGRAB_RETRY_AFTER + 1
            later = self._asked(1, **said)

        self.assertEqual((first, later), (1, 1))

    def test_a_failure_that_passes_gives_a_ddagrab_after_the_minute(self) -> None:
        now = [1000.0]
        with patch.object(ffmpeg.time, "monotonic", lambda: now[0]):
            self.assertFalse(self._ran(returncode=1, stderr="Access is denied.")[0])
            now[0] += ffmpeg.DDAGRAB_RETRY_AFTER + 1

            self.assertTrue(self._ran()[0])

    def test_a_program_installed_over_the_old_one_is_asked_again(self) -> None:
        self.assertFalse(self._ran(returncode=1, stderr="Failed to create D3D11VA device.")[0])
        self.program.write_bytes(b"a newer build")

        self.assertTrue(self._ran()[0])

    def test_a_program_that_is_not_there_is_not_asked(self) -> None:
        self.program.unlink()

        works, ran = self._ran()

        self.assertEqual((works, ran), (False, []))


class WarmTests(unittest.TestCase):
    def test_only_windows_asks_which_grab_it_can_use(self) -> None:
        for here, asks in ((tools.WINDOWS, 1), (tools.LINUX, 0), (tools.DARWIN, 0)):
            with self.subTest(here), patch.object(adapters.tools, "here", return_value=here), \
                    patch.object(ffmpeg.WindowsAdapter, "warm") as warm:
                adapters.warm()

                self.assertEqual(warm.call_count, asks)


if __name__ == "__main__":
    unittest.main()
