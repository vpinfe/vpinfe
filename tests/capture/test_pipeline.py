"""The FFmpeg command lines from a recording to the stored file."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from collections.abc import Sequence
from pathlib import Path

from common.capture import commands, geometry, pipeline, settings
from common.capture.adapters import Output
from common.capture.geometry import Turn

FFMPEG = Path("/usr/bin/ffmpeg")
PLAYFIELD = Output("DP-1", 0, 0, 1080, 1920, (1920, 1080), 60.0, Turn(ccw=270))


def _encode(ffmpeg: Path, job: pipeline.Encode, dest: Path) -> list[str]:
    """VPinFE's own Encode Command."""
    return commands.encode("", ffmpeg, job, dest, window="playfield", output=PLAYFIELD)


def _value(argv: list[str], flag: str) -> str:
    return argv[argv.index(flag) + 1]


class CommandTests(unittest.TestCase):
    def test_each_format_and_quality_is_the_measured_crf(self) -> None:
        for (codec, quality), crf in {(settings.H264, settings.STANDARD): "34",
                                      (settings.H264, settings.HIGH): "23",
                                      (settings.VP9, settings.STANDARD): "60",
                                      (settings.VP9, settings.HIGH): "41"}.items():
            with self.subTest(codec=codec, quality=quality):
                self.assertEqual(_value(pipeline.codec_args(codec, quality), "-crf"), crf)

    def test_h264_is_x264_veryfast(self) -> None:
        argv = pipeline.codec_args(settings.H264, settings.STANDARD)

        self.assertEqual((_value(argv, "-c:v"), _value(argv, "-preset")),
                         ("libx264", "veryfast"))

    def test_vp9_is_realtime_with_quality_by_crf_and_no_bitrate_target(self) -> None:
        argv = pipeline.codec_args(settings.VP9, settings.STANDARD)

        self.assertEqual((_value(argv, "-c:v"), _value(argv, "-deadline"),
                          _value(argv, "-cpu-used"), _value(argv, "-b:v")),
                         ("libvpx-vp9", "realtime", "8", "0"))

    def test_an_encode_is_cut_turned_evened_out_and_silent(self) -> None:
        job = pipeline.Encode(Path("raw.mkv"), 0.25, 20, Turn(ccw=90), 30, 1920,
                              settings.H264, settings.STANDARD)

        argv = _encode(FFMPEG, job, Path("out.mp4"))

        self.assertEqual((_value(argv, "-ss"), _value(argv, "-t")), ("0.250", "20.000"))
        self.assertLess(argv.index("-ss"), argv.index("-i"))
        self.assertIn("-an", argv)
        self.assertTrue(_value(argv, "-vf").startswith("transpose=2,fps=30,scale="))
        self.assertEqual(argv[-1], "out.mp4")

    def test_nothing_turns_where_the_steps_cancel(self) -> None:
        turn = geometry.playfield(geometry.sway_transform("270"), 0, geometry.BOTTOM_RIGHT)

        self.assertTrue(pipeline.video_filters(turn, 30, 1920).startswith("fps=30,"))

    def test_the_screens_own_size_is_only_evened(self) -> None:
        self.assertEqual(pipeline.cap_of(settings.SCREENS_OWN), None)
        self.assertEqual(pipeline.scale(None), "scale=trunc(iw/2)*2:trunc(ih/2)*2")
        self.assertEqual(pipeline.cap_of("1920"), 1920)

    def test_a_picture_is_one_frame_at_picture_at_or_a_whole_still(self) -> None:
        cut = pipeline.picture(FFMPEG, Path("raw.mkv"), Turn(), 1920, Path("p.png"), at=5.5)
        still = pipeline.picture(FFMPEG, Path("still.png"), Turn(), 1920, Path("p.png"))

        self.assertEqual((_value(cut, "-ss"), _value(cut, "-frames:v")), ("5.500", "1"))
        self.assertNotIn("-ss", still)

    def test_sound_comes_from_the_default_output_unless_one_is_named(self) -> None:
        for chosen, source in ((settings.AUTO, "@DEFAULT_MONITOR@"),
                               ("alsa_output.usb.monitor", "alsa_output.usb.monitor")):
            with self.subTest(chosen):
                argv = pipeline.sound(FFMPEG, chosen, Path("s.wav"))

                self.assertEqual((_value(argv, "-f"), _value(argv, "-i")), ("pulse", source))

    def test_the_counts_ffmpeg_reports_are_read(self) -> None:
        self.assertEqual(pipeline.frames_in("frame=12\nfps=0\nframe=1200\nprogress=end\n"),
                         1200)
        self.assertEqual(pipeline.frames_in(""), 0)
        self.assertEqual(pipeline.peak_in("[Parsed_volumedetect_0] max_volume: -84.0 dB"),
                         -84.0)
        self.assertEqual(pipeline.peak_in("max_volume: -inf dB"), float("-inf"))

    def test_levels_are_read_off_the_key_frames_or_a_few_frames_a_second(self) -> None:
        keys = pipeline.levels(FFMPEG, Path("raw.mkv"))
        spread = pipeline.levels(FFMPEG, Path("raw.mkv"), 2)

        self.assertEqual(_value(keys, "-skip_frame"), "nokey")
        self.assertLess(keys.index("-skip_frame"), keys.index("-i"))
        self.assertEqual(_value(keys, "-vf"), "signalstats,metadata=mode=print")
        self.assertNotIn("-skip_frame", spread)
        self.assertEqual(_value(spread, "-vf"), "fps=2,signalstats,metadata=mode=print")

    def test_one_color_is_every_frame_spreading_no_more_than_four_levels(self) -> None:
        cases = {
            "the desktop's background": ([(32, 32, 132, 132, 127, 127)] * 5, True),
            "a level or four of rounding": ([(30, 34, 130, 134, 127, 131)], True),
            "five": ([(30, 35, 132, 132, 127, 127)], False),
            "a dim dot on black": ([(16, 36, 116, 128, 127, 143)], False),
            "one frame of five drawn": ([(16, 16, 128, 128, 128, 128)] * 4
                                        + [(16, 235, 16, 240, 16, 240)], False),
            "nothing read": ([], False),
        }
        for name, (frames, expected) in cases.items():
            with self.subTest(name):
                self.assertIs(pipeline.flat_in(_printed(frames)), expected)

    def test_a_frame_missing_a_plane_is_not_read(self) -> None:
        said = _printed([(16, 16, 128, 128, 128, 128)]) \
            + "\nframe:1 pts:1\nlavfi.signalstats.YMIN=16\nlavfi.signalstats.YMAX=235\n"

        self.assertTrue(pipeline.flat_in(said))


def _printed(frames: Sequence[tuple[int, ...]]) -> str:
    """What `metadata=mode=print` logs after signalstats: a frame line, then its values."""
    lines = ["Stream mapping:", "  Stream #0:0 -> #0:0 (h264 (native) -> wrapped_avframe)"]
    for index, levels in enumerate(frames):
        lines.append(f"[Parsed_metadata_1 @ 0x1] frame:{index}    pts:{index}    "
                     f"pts_time:{index}")
        lines.append("[Parsed_metadata_1 @ 0x1] lavfi.signalstats.YAVG=40.2")
        for (plane, end), value in zip(((p, e) for p in "YUV" for e in ("MIN", "MAX")),
                                       levels, strict=True):
            lines.append(f"[Parsed_metadata_1 @ 0x1] lavfi.signalstats.{plane}{end}={value}")
    return "\n".join(lines)


_FFMPEG = shutil.which("ffmpeg")
_FFPROBE = shutil.which("ffprobe")


@unittest.skipUnless(_FFMPEG and _FFPROBE, "FFmpeg is not installed here")
class RealFfmpegTests(unittest.TestCase):
    tmp: Path
    ffmpeg: Path
    raw: Path

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp())
        cls.ffmpeg = Path(str(_FFMPEG))
        cls.raw = cls.tmp / "raw.mkv"
        subprocess.run([str(_FFMPEG), "-hide_banner", "-loglevel", "error", "-y", "-f",
                        "lavfi", "-i", "testsrc2=size=1920x1080:rate=60:duration=2",
                        "-c:v", "libx264", "-preset", "ultrafast", "-qp", "18",
                        str(cls.raw)], check=True, timeout=120)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _probe(self, path: Path) -> dict:
        said = subprocess.run([str(_FFPROBE), "-v", "error", "-show_entries",
                               "stream=codec_name,codec_type,width,height,r_frame_rate",
                               "-of", "json", str(path)], capture_output=True, text=True,
                              check=True, timeout=60)
        return json.loads(said.stdout)["streams"]

    def test_the_frames_are_counted(self) -> None:
        done = pipeline.run(pipeline.frames(self.ffmpeg, self.raw))

        self.assertEqual(pipeline.frames_in(done.stdout), 120)

    def test_each_format_comes_out_turned_at_the_constant_rate_with_no_sound(self) -> None:
        for codec, name in ((settings.H264, "h264"), (settings.VP9, "vp9")):
            with self.subTest(codec):
                dest = self.tmp / f"{codec}.mp4"
                job = pipeline.Encode(self.raw, 0.5, 1.0, Turn(ccw=270), 30, 1920, codec,
                                      settings.STANDARD)

                pipeline.run(_encode(self.ffmpeg, job, dest))

                streams = self._probe(dest)
                self.assertEqual([one["codec_type"] for one in streams], ["video"])
                self.assertEqual((streams[0]["codec_name"], streams[0]["width"],
                                  streams[0]["height"], streams[0]["r_frame_rate"]),
                                 (name, 1080, 1920, "30/1"))

    def test_a_files_size_and_rate_are_read_from_what_ffmpeg_says_of_it(self) -> None:
        done = subprocess.run(pipeline.describe(self.ffmpeg, self.raw), capture_output=True,
                              text=True, timeout=60, check=False)

        self.assertEqual(pipeline.video_in(done.stderr), ((1920, 1080), 60.0))

    def test_a_smaller_screen_is_never_scaled_up(self) -> None:
        dest = self.tmp / "own.png"

        pipeline.run(pipeline.picture(self.ffmpeg, self.raw, Turn(), 3840, dest, at=0.5))

        self.assertEqual((self._probe(dest)[0]["width"], self._probe(dest)[0]["height"]),
                         (1920, 1080))

    def test_a_tone_is_heard_and_silence_is_not(self) -> None:
        peaks = {}
        for name, source in (("tone", "sine=frequency=440:duration=1"),
                             ("silence", "anullsrc=r=48000:cl=stereo:d=1")):
            wav = self.tmp / f"{name}.wav"
            subprocess.run([str(_FFMPEG), "-hide_banner", "-loglevel", "error", "-y", "-f",
                            "lavfi", "-i", source, str(wav)], check=True, timeout=60)
            peaks[name] = pipeline.peak_in(
                pipeline.run(pipeline.loudness(self.ffmpeg, wav)).stderr)

        self.assertGreater(peaks["tone"], pipeline.SILENT_DB)
        self.assertLess(peaks["silence"], pipeline.SILENT_DB)
        dest = self.tmp / "audio.mp3"
        pipeline.run(pipeline.mp3(self.ffmpeg, self.tmp / "tone.wav", 0.1, 0.5, dest))
        self.assertEqual(self._probe(dest)[0]["codec_name"], "mp3")

    def test_a_background_is_one_color_and_a_drawn_screen_is_not(self) -> None:
        sources = {"background": "color=c=0x12121a:size=640x360:rate=60:duration=2",
                   "dim dot": "color=c=black:size=640x360:rate=60:duration=2,"
                              "drawbox=x=100:y=100:w=8:h=8:color=0x301000:t=fill"}
        one_color = {}
        for name, source in sources.items():
            raw = self.tmp / f"{name}.mkv"
            subprocess.run([str(_FFMPEG), "-hide_banner", "-loglevel", "error", "-y", "-f",
                            "lavfi", "-i", source, "-c:v", "libx264", "-preset", "ultrafast",
                            "-crf", "18", str(raw)], check=True, timeout=120)
            one_color[name] = [
                pipeline.flat_in(pipeline.run(pipeline.levels(self.ffmpeg, raw, every)).stderr)
                for every in (0, pipeline.CONFIRM_PER_SECOND)]
        one_color["moving"] = [
            pipeline.flat_in(pipeline.run(pipeline.levels(self.ffmpeg, self.raw, every)).stderr)
            for every in (0, pipeline.CONFIRM_PER_SECOND)]

        self.assertEqual(one_color, {"background": [True, True], "dim dot": [False, False],
                                     "moving": [False, False]})


if __name__ == "__main__":
    unittest.main()
