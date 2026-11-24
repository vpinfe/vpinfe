"""The Record and Encode Commands: which tokens each offers, what each stands for on every
platform, and a template that cannot run refused where it is written."""

from __future__ import annotations

import unittest
from pathlib import Path
from typing import Any, cast

from starlette.testclient import TestClient

import httpapi
from common import service_errors, tokens
from common.capture import commands, pipeline, settings
from common.capture.adapters import Output
from common.capture.geometry import Turn
from common.capture.settings import Settings
from common.host import tools
from common.i18n import t
from common.settings import config_service
from tests.api.test_config import Store
from tests.capture.test_preflight import found

# The token table in the design, and the capture input it gives on each platform for a
# screen laid out at 1080,0 at 1920x1080 and 59.94 Hz, which the capture API numbers 1 or
# 2: with no hardware encoder, then with the one each platform proves.
DESIGNED = {"ffmpeg", "recorder", "input", "output", "window", "screen", "monitor_index",
            "x", "y", "width", "height", "duration", "fps", "video_filters", "video_codec",
            "hwaccel", "audio_device"}
NODE = "/dev/dri/renderD128"
INPUTS = {
    commands.WLR: (1, ["-o", "DP-2"]),
    commands.DDAGRAB: (1, ["-f", "lavfi", "-i", "ddagrab=output_idx=1:framerate=60:"
                                                "draw_mouse=0,hwdownload,format=bgra"]),
    commands.GDIGRAB: (1, ["-f", "gdigrab", "-framerate", "60", "-draw_mouse", "0",
                           "-offset_x", "1080", "-offset_y", "0",
                           "-video_size", "1920x1080", "-i", "desktop"]),
    commands.X11GRAB: (1, ["-f", "x11grab", "-framerate", "60", "-draw_mouse", "0",
                           "-video_size", "1920x1080", "-i", ":0.0+1080,0"]),
    commands.AVFOUNDATION: (2, ["-f", "avfoundation", "-framerate", "60",
                                "-capture_cursor", "0", "-i", "Capture screen 2:none"]),
    commands.PORTAL: (64, ["pipewiresrc", "fd=-1", "path=64", "do-timestamp=true",
                           "keepalive-time=1000"]),
}
HARDWARE = {
    commands.WLR: (NODE, ["-c", "h264_vaapi", "-d", NODE, "-p", "qp=18"]),
    commands.DDAGRAB: ("h264_nvenc", ["-c:v", "h264_nvenc", "-preset", "p1", "-rc",
                                      "constqp", "-qp", "18"]),
    commands.GDIGRAB: ("h264_amf", ["-c:v", "h264_amf", "-usage", "lowlatency", "-rc",
                                    "cqp", "-qp_i", "18", "-qp_p", "18"]),
    commands.X11GRAB: (NODE, ["-vaapi_device", NODE, "-vf", "format=nv12,hwupload",
                              "-c:v", "h264_vaapi", "-qp", "18"]),
    commands.AVFOUNDATION: ("h264_videotoolbox", ["-c:v", "h264_videotoolbox",
                                                  "-realtime", "1", "-b:v", "25M"]),
    commands.PORTAL: ("", []),
}
CHOSEN = Settings(length=20, wait=15, picture_at=5, fps=30, size="1920",
                  video_codec="auto", playfield_orientation="bottom_right",
                  quality="standard", sound=False, sound_source="auto")
FFMPEG = Path("/usr/bin/ffmpeg")


def _path(one: str) -> str:
    """How a path reaches an argument on this platform."""
    return str(Path(one))


def _screen(index: int = 1) -> Output:
    return Output("DP-2", 1080, 0, 1920, 1080, (1920, 1080), 59.94, Turn(), index)


def _every(command: str) -> str:
    """A template naming every token its command offers, each an argument of its own."""
    return " ".join(tokens.written(name) for name in commands.OFFERED[command])


def _job(source: Path = Path("raw.mkv")) -> pipeline.Encode:
    return pipeline.Encode(source, 0.5, 20, Turn(ccw=90), 30, 1920, settings.H264,
                           settings.STANDARD)


class TokenTests(unittest.TestCase):
    def test_the_tokens_are_the_designs(self) -> None:
        self.assertEqual(set(commands.TOKENS), DESIGNED)

    def test_every_record_token_expands_for_each_platforms_input(self) -> None:
        for adapter_id, (index, expected) in INPUTS.items():
            for hardware in dict.fromkeys(("", HARDWARE[adapter_id][0])):
                with self.subTest(adapter_id, hardware=hardware):
                    values = commands.record_values(
                        adapter_id, found(), _screen(index), "backglass",
                        Path("/tmp/a b.mkv"), CHOSEN, hardware, display=":0")

                    argv = commands.expand(_every(commands.RECORD), commands.RECORD, values)

                    recorder = commands.RECORDERS.get(adapter_id, tools.FFMPEG)
                    self.assertEqual(argv, [
                        _path("/usr/bin/ffmpeg"), _path(f"/usr/bin/{recorder.id}"),
                        *commands.inputs(adapter_id, _screen(index), ":0", hardware),
                        _path("/tmp/a b.mkv"), "backglass", "DP-2", str(index), "1080", "0",
                        "1920", "1080", "20", "60",
                        *(HARDWARE[adapter_id][1] if hardware else []),
                        "@DEFAULT_MONITOR@"])
                    self.assertEqual(commands.inputs(adapter_id, _screen(index), ":0"),
                                     expected)

    def test_ddagrab_keeps_its_frames_on_the_card_only_for_an_encoder_there(self) -> None:
        for hardware, kept in (("", False), ("h264_nvenc", True), ("h264_amf", True),
                               (NODE, False)):
            with self.subTest(hardware):
                graph = commands.inputs(commands.DDAGRAB, _screen(), hardware=hardware)[-1]

                self.assertEqual("hwdownload" not in graph, kept)

    def test_x_names_its_first_screen_when_the_display_does_not(self) -> None:
        for display, said in ((":0", ":0.0+1080,0"), (":1.0", ":1.0+1080,0"),
                              ("host:2", "host:2.0+1080,0")):
            with self.subTest(display):
                self.assertEqual(commands.inputs(commands.X11GRAB, _screen(), display)[-1],
                                 said)

    def test_every_encode_token_expands(self) -> None:
        argv = commands.expand(_every(commands.ENCODE), commands.ENCODE,
                               commands.encode_values(FFMPEG, _job(), Path("out.mp4"),
                                                      "playfield", _screen()))

        self.assertFalse([one for one in argv if "{" in one], argv)
        self.assertEqual(argv[:8], [_path("/usr/bin/ffmpeg"), "-ss", "0.500", "-t", "20.000",
                                    "-i", "raw.mkv", "out.mp4"])
        self.assertEqual(argv[8:15], ["playfield", "DP-2", "1920", "1080", "20", "30",
                                      "-vf"])
        self.assertTrue(argv[15].startswith("transpose=2,fps=30,"))
        self.assertEqual(argv[16:], pipeline.codec_args(settings.H264, settings.STANDARD))

    def test_fps_is_the_screens_refresh_when_recording_and_frame_rate_when_encoding(
            self) -> None:
        recording = commands.record_values(commands.WLR, found(), _screen(), "playfield",
                                           Path("a.mkv"), CHOSEN, "")
        encoding = commands.encode_values(FFMPEG, _job(), Path("b.mp4"), "playfield",
                                          _screen())

        self.assertEqual((recording["fps"], encoding["fps"]), ("60", "30"))

    def test_a_scalar_fills_its_place_inside_an_argument(self) -> None:
        argv = commands.expand("{ffmpeg} -i ddagrab=output_idx={monitor_index} {output}",
                               commands.RECORD,
                               commands.record_values(commands.DDAGRAB, found(), _screen(3),
                                                      "playfield", Path("o.mkv"), CHOSEN,
                                                      ""))

        self.assertEqual(argv[2], "ddagrab=output_idx=3")

    def test_the_portals_input_is_the_stream_through_the_remote_lent_to_it(
            self) -> None:
        lent = Output("DP-2", 1080, 0, 1920, 1080, (1920, 1080), 0.0, Turn(), 64, 21)

        self.assertEqual(commands.inputs(commands.PORTAL, lent),
                         ["pipewiresrc", "fd=21", "path=64", "do-timestamp=true",
                          "keepalive-time=1000"])

    def test_gstreamer_records_the_portals_screens(self) -> None:
        values = commands.record_values(commands.PORTAL, found(), _screen(), "playfield",
                                        Path("o.mkv"), CHOSEN, "")

        self.assertEqual(values["recorder"], _path("/usr/bin/gstreamer"))

    def test_ffmpeg_everywhere_but_wlroots_is_its_own_recorder(self) -> None:
        for adapter_id in commands.FFMPEG_GRABS:
            with self.subTest(adapter_id):
                values = commands.record_values(adapter_id, found(), _screen(), "playfield",
                                                Path("o.mkv"), CHOSEN, "")

                self.assertEqual(values["recorder"], values["ffmpeg"])

    def test_an_ffmpeg_filter_label_passes_through_as_written(self) -> None:
        argv = commands.expand("{ffmpeg} {input} -filter_complex [0:v]null[v] "
                               "-map [v] {output}", commands.ENCODE,
                               commands.encode_values(FFMPEG, _job(), Path("o.mp4"),
                                                      "playfield", _screen()))

        self.assertIn("[0:v]null[v]", argv)
        self.assertIn("[v]", argv)

    def test_a_path_with_a_space_stays_one_argument(self) -> None:
        argv = commands.expand('{ffmpeg} {input} -vf "scale=1920:-2" {output}',
                               commands.ENCODE,
                               commands.encode_values(FFMPEG, _job(Path("/r/a b.mkv")),
                                                      Path("/o/c d.mp4"), "playfield",
                                                      _screen()))

        self.assertIn(_path("/r/a b.mkv"), argv)
        self.assertEqual(argv[-1], _path("/o/c d.mp4"))
        self.assertIn("scale=1920:-2", argv)


class RefusalTests(unittest.TestCase):
    def said(self, template: str, command: str = commands.RECORD) -> list[str]:
        return [commands.words(one) for one in commands.problems(template, command)]

    def test_an_empty_template_is_vpinfes_own(self) -> None:
        self.assertEqual(self.said("  "), [])

    def test_an_unknown_token_is_refused_by_name(self) -> None:
        self.assertEqual(self.said("{recorder} -o {monitor} {output}"),
                         [t(commands.UNKNOWN, token="monitor")])

    def test_a_token_from_the_other_command_is_refused(self) -> None:
        self.assertEqual(self.said("{ffmpeg} {input} {video_codec} {output}"),
                         [t(commands.ELSEWHERE, token="video_codec",
                            command=t("config.capture.encode_command.label"))])
        self.assertEqual(self.said("{ffmpeg} {input} {hwaccel} {output}", commands.ENCODE),
                         [t(commands.ELSEWHERE, token="hwaccel",
                            command=t("config.capture.record_command.label"))])

    def test_a_list_token_sharing_an_argument_is_refused(self) -> None:
        self.assertEqual(self.said("{ffmpeg} x{input} {output}"),
                         [t(commands.ALONE, token="input")])

    def test_nowhere_to_write_or_nothing_to_read_is_refused(self) -> None:
        self.assertEqual(self.said("{recorder} {input}"),
                         [t(commands.NEEDS, token="output")])
        self.assertEqual(self.said("{ffmpeg} -i raw.mkv {output}", commands.ENCODE),
                         [t(commands.NEEDS, token="input")])

    def test_an_unclosed_quote_is_refused(self) -> None:
        self.assertEqual(self.said('{recorder} "{input} {output}'), [t(commands.QUOTE)])

    def test_expanding_what_cannot_run_raises(self) -> None:
        with self.assertRaises(commands.CommandError):
            commands.expand("{recorder} {nope} {output}", commands.RECORD, {})

    def test_a_runs_own_command_is_refused_before_anything_runs(self) -> None:
        with self.assertRaises(service_errors.RefusedError) as refused:
            settings.read(None, {"encode_command": "{ffmpeg} {input} {hwaccel} {output}"})

        self.assertTrue(str(refused.exception).startswith(
            t("config.capture.encode_command.label")))

    def test_a_command_the_file_holds_that_cannot_run_reads_as_vpinfes_own(self) -> None:
        import configparser

        held = configparser.ConfigParser()
        held["capture"] = {"record_command": "{recorder} {nope} {output}",
                           "encode_command": "{ffmpeg} {input} -an {output}"}

        chosen = settings.read(held)

        self.assertEqual((chosen.record_command, chosen.encode_command),
                         ("", "{ffmpeg} {input} -an {output}"))


class OwnCommandTests(unittest.TestCase):
    def test_vpinfes_own_record_is_the_hardware_encoder_where_there_is_one(self) -> None:
        with_d = found()
        with_d[tools.WF_RECORDER.id] = tools.Found(
            tools.WF_RECORDER, tools.State.FOUND, Path("/usr/bin/wf-recorder"),
            tools.Probe(True, "0.5", {tools.OPTIONS: frozenset({"-D"})}))

        self.assertEqual(commands.own_record(commands.WLR, with_d, "/dev/dri/renderD128"),
                         "{recorder} -D {input} {hwaccel} -f {output}")
        self.assertEqual(commands.own_record(commands.WLR, found(), ""),
                         "{recorder} {input} -c libx264 -p preset=ultrafast -p crf=18 "
                         "-f {output}")

    def test_everywhere_else_it_is_ffmpeg_reading_the_platforms_input(self) -> None:
        for adapter_id in commands.FFMPEG_GRABS:
            with self.subTest(adapter_id):
                self.assertEqual(commands.own_record(adapter_id, found(), "h264_nvenc"),
                                 "{ffmpeg} -hide_banner -loglevel error -y {input} "
                                 "{hwaccel} -f matroska {output}")
                self.assertEqual(commands.own_record(adapter_id, found(), ""),
                                 "{ffmpeg} -hide_banner -loglevel error -y {input} "
                                 "-c:v libx264 -preset ultrafast -crf 18 -f matroska "
                                 "{output}")

    def test_the_portals_is_gstreamer_to_a_near_lossless_file_it_finishes_on_ctrl_c(
            self) -> None:
        argv = commands.record(commands.PORTAL, found(), _screen(64), "playfield",
                               Path("/tmp/a b/playfield.mkv"), CHOSEN, "")

        self.assertEqual(argv, [
            _path("/usr/bin/gstreamer"), "-q", "-e", "pipewiresrc", "fd=-1", "path=64",
            "do-timestamp=true", "keepalive-time=1000", "!", "videoconvert", "!",
            "videorate", "!", "video/x-raw,framerate=60/1", "!", "x264enc",
            "speed-preset=ultrafast", "pass=qual", "quantizer=18", "!", "matroskamux", "!",
            "filesink", f"location={_path('/tmp/a b/playfield.mkv')}"])

    def test_a_session_with_no_way_to_record_has_no_record_command(self) -> None:
        self.assertEqual(commands.own("wayland", {}, ""),
                         {commands.RECORD: "", commands.ENCODE: commands.OWN_ENCODE})

    def test_vpinfes_own_commands_are_ones_a_person_could_have_written(self) -> None:
        for adapter_id in (commands.WLR, commands.PORTAL, *commands.FFMPEG_GRABS):
            for hardware in ("", "x"):
                for command, template in commands.own(adapter_id, found(),
                                                      hardware).items():
                    with self.subTest(adapter_id, hardware=hardware, command=command):
                        self.assertEqual(commands.problems(template, command), [])


class ConfigRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = Store()
        real = config_service.get_ini_config
        cast(Any, config_service).get_ini_config = lambda: self.store
        self.addCleanup(setattr, config_service, "get_ini_config", real)
        self.client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)

    def test_a_command_that_cannot_run_is_refused_at_the_field(self) -> None:
        response = self.client.put("/config", json={"settings": {
            "capture": {"record_command": "{recorder} {input} {video_codec} {output}"}}})

        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn("{video_codec}", response.json()["error"]["message"])
        self.assertEqual(self.store.saves, 0)

    def test_one_that_can_is_written(self) -> None:
        response = self.client.put("/config", json={"settings": {
            "capture": {"encode_command": "{ffmpeg} {input} {video_filters} -an {output}"}}})

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.store.saves, 1)


if __name__ == "__main__":
    unittest.main()
