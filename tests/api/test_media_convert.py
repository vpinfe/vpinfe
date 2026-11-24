"""Converting pictures, sounds and videos a device cannot play, as one job.

What is worth pinning: a file that cannot be converted is left exactly as it was while the rest
of the batch goes on, a copy exists before the file it is a copy of changes, every choice the
plan offers has one recommendation and a sentence, and the plan's counts are the job's.
"""

from __future__ import annotations

import io
import pathlib
import subprocess
import time
import unittest
from typing import Any, cast
from unittest.mock import MagicMock, patch

from PIL import Image, ImageCms

from common import service_errors
from common.backups import folder as backup_folder
from common.backups import media as backup_media
from common.backups.copies import REASON_CONVERT
from common.capture import settings as capture
from common.games import game_repository, media_convert
from common.games import media_encode as encode
from common.games.game_identity import game_id as game_id_of
from common.games.info_file import MetaConfig
from common.games.media_files import SkipError
from common.host import frontend_browser, tools
from common.media.media_specs import media_family, spec_named, unsupported_named
from common.runtime import jobs, shutdown
from httpapi import auth, scopes
from tests.api.test_backups import BackupsCase
from tests.support.library import write_game
from tests.support.pictures import banded, png

GAME = "id-Taxi"
NAME = "Taxi"

EVERYTHING = media_convert.Device("Cabinet", {"h264": True, "vp9": True, "av1": True})
NO_H264 = media_convert.Device("Cabinet", {"h264": False, "vp9": True, "av1": None})
NO_VIDEO = media_convert.Device("Cabinet", {"h264": False, "vp9": False, "av1": False})
LAPTOP = media_convert.Device("Laptop", {"h264": True, "vp9": False, "av1": False})

ENCODERS = frozenset({"libx264", "libvpx-vp9", "libsvtav1", "libmp3lame", "libopus"})

# The case patches these out, and some tests want the real ones.
REAL_RUN = encode.run
REAL_CHECK = encode.check_length


def _found(encoders: frozenset[str] = ENCODERS) -> tools.Found:
    return tools.Found(tools.FFMPEG, tools.State.FOUND, pathlib.Path("/fake/ffmpeg"),
                       tools.Probe(True, can={tools.ENCODERS: encoders}))


def _real() -> tools.Found:
    return tools.resolve(tools.FFMPEG)


def _real_can(*encoders: str) -> bool:
    found = _real()
    return (found.state == tools.State.FOUND and found.probe is not None
            and all(found.probe.has(tools.ENCODERS, name) for name in encoders))


def _save(path: pathlib.Path, fmt: str, size: tuple[int, int] = (40, 20), mode: str = "RGBA",
          **options: object) -> None:
    fill = (200, 30, 30, 128) if mode == "RGBA" else (200, 30, 30)
    Image.new(mode, size, fill).save(path, format=fmt, **options)  # type: ignore[arg-type]


def _opened(path: pathlib.Path) -> Image.Image:
    return Image.open(io.BytesIO(path.read_bytes()))


class ConvertCase(BackupsCase):
    fake_ffmpeg = True

    def setUp(self) -> None:
        super().setUp()
        self.folder = write_game(self.library, NAME, vpx=True,
                                 info={"Info": {"Title": NAME}, "vpinfe": {"schema": 2,
                                                                          "game_id": GAME}},
                                 medias={"wheel.png": banded(60, 30)})
        self.start(patch.object(
            game_repository, "catalog",
            side_effect=lambda: {game_id_of(game): game for game in self.library_games()}))
        self.start(patch.object(media_convert.media_ops, "reread"))
        self.streams = encode.Streams("h264", "aac", "yuv420p", 1.0)
        self.argvs: list[list[str]] = []
        if self.fake_ffmpeg:
            self.use_ffmpeg(_found())
            self.start(patch.object(encode, "streams",
                                    side_effect=lambda binary, path: self.streams))
            self.start(patch.object(encode, "run", side_effect=self.fake_run))
            self.start(patch.object(encode, "check_length",
                                    side_effect=lambda binary, source, staged: None))

    def start(self, patcher: object) -> None:
        cast(MagicMock, patcher).start()
        self.addCleanup(cast(MagicMock, patcher).stop)

    def use_ffmpeg(self, found: tools.Found) -> None:
        self.start(patch.object(media_convert.tools, "resolve", return_value=found))

    def fake_run(self, argv: list[str], **_: object) -> None:
        self.argvs.append(argv)
        pathlib.Path(argv[-1]).write_bytes(b"converted")

    def medias(self, name: str = "") -> pathlib.Path:
        return self.folder / "medias" / name

    def target(self, path: str) -> media_convert.Target:
        return media_convert.Target(GAME, path)

    def plan(self, *paths: str, devices: list[media_convert.Device] | None = None,
             **choices: object) -> media_convert.Plan:
        return media_convert.plan([self.target(path) for path in paths], choices,
                                  devices or [EVERYTHING])

    def run_job(self, paths: list[str], devices: list[media_convert.Device] | None = None,
                **choices: object) -> dict:
        job = media_convert.start([self.target(path) for path in paths], choices,
                                  devices or [EVERYTHING])
        while job.state == jobs.RUNNING:
            time.sleep(0.01)
        self.assertEqual(job.state, jobs.DONE, job.error)
        return cast(dict, job.result)

    def copies(self) -> list[dict]:
        return [one for one in self.listing() if one["kind"] == "media"]


class PictureTests(ConvertCase):
    def test_each_format_converts_to_a_png_of_the_same_size_with_its_alpha(self) -> None:
        _save(self.medias("wheel.tga"), "TGA")
        _save(self.medias("logo.ico"), "ICO", (32, 32))
        _save(self.medias("bg.dds"), "DDS")
        _save(self.medias("flyer.tif"), "TIFF")

        result = self.run_job(["medias/wheel.tga", "medias/logo.ico", "medias/bg.dds",
                               "medias/flyer.tif"], keep=False, on_clash=media_convert.REPLACE)

        self.assertEqual(result["skipped"], [])
        for name, size in (("wheel", (40, 20)), ("logo", (32, 32)), ("bg", (40, 20)),
                           ("flyer", (40, 20))):
            made = _opened(self.medias(f"{name}.png"))
            self.assertEqual((made.format, made.size), ("PNG", size), name)
        self.assertEqual(cast(tuple, _opened(self.medias("wheel.png")).getpixel((5, 5)))[3],
                         128)
        self.assertFalse(self.medias("wheel.tga").exists())

    def test_the_color_profile_and_the_resolution_come_with_the_picture(self) -> None:
        profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
        _save(self.medias("wheel.tif"), "TIFF", mode="RGB", icc_profile=profile, dpi=(300, 300))

        self.run_job(["medias/wheel.tif"], keep=False, on_clash=media_convert.REPLACE)

        made = _opened(self.medias("wheel.png"))
        self.assertEqual(made.info.get("icc_profile"), profile)
        self.assertEqual(tuple(round(one) for one in made.info["dpi"]), (300, 300))

    def test_webp_and_jpg_are_made_when_chosen_and_jpg_says_it_fills_the_see_through(
            self) -> None:
        _save(self.medias("flyer.tga"), "TGA")
        _save(self.medias("logo.ico"), "ICO", (32, 32))

        planned = self.plan("medias/flyer.tga", picture=encode.JPG)
        self.run_job(["medias/flyer.tga"], keep=False, picture=encode.WEBP)
        self.run_job(["medias/logo.ico"], keep=False, picture=encode.JPG)

        self.assertEqual(planned.items[0].note, "See-through areas are filled with black")
        self.assertEqual(_opened(self.medias("flyer.webp")).format, "WEBP")
        flat = _opened(self.medias("logo.jpg"))
        self.assertEqual((flat.format, flat.mode), ("JPEG", "RGB"))

    def test_a_deep_a_palette_and_a_cmyk_source_come_out_eight_bit_rgb(self) -> None:
        Image.new("I;16", (8, 8), 40000).save(self.medias("logo.tif"), format="TIFF")
        Image.new("P", (8, 8), 3).save(self.medias("flyer.tga"), format="TGA")
        Image.new("CMYK", (8, 8), (10, 20, 30, 40)).save(self.medias("bg.tif"), format="TIFF")

        self.run_job(["medias/logo.tif", "medias/flyer.tga", "medias/bg.tif"], keep=False)

        modes = {name: _opened(self.medias(f"{name}.png")).mode
                 for name in ("logo", "flyer", "bg")}
        self.assertTrue(set(modes.values()) <= {"RGB", "RGBA"}, modes)

    def test_a_jfif_is_renamed_byte_for_byte_and_a_chosen_png_makes_it_a_re_encode(self) -> None:
        data = png(40, 20, fmt="JPEG")
        self.medias("flyer.jfif").write_bytes(data)
        self.medias("logo.jfif").write_bytes(data)

        renamed = self.plan("medias/flyer.jfif", picture=encode.JPG).items[0]
        recoded = self.plan("medias/flyer.jfif", picture=encode.PNG).items[0]
        kept = self.run_job(["medias/flyer.jfif"], keep=False, picture=encode.JPG)
        self.run_job(["medias/logo.jfif"], keep=False, picture=encode.PNG)

        self.assertEqual((renamed.action, recoded.action),
                         (media_convert.RENAME, media_convert.CONVERT))
        self.assertEqual(self.medias("flyer.jpg").read_bytes(), data)
        self.assertEqual(kept["renamed"], 1)
        self.assertEqual(_opened(self.medias("logo.png")).format, "PNG")

    def test_an_animated_source_is_skipped_with_its_reason_and_left_as_it_was(self) -> None:
        frames = [Image.new("RGB", (8, 8), shade) for shade in ((255, 0, 0), (0, 255, 0))]
        frames[0].save(self.medias("flyer.tif"), format="TIFF", save_all=True,
                       append_images=frames[1:])
        before = self.medias("flyer.tif").read_bytes()

        result = self.run_job(["medias/flyer.tif"], keep=True)

        self.assertEqual(self.medias("flyer.tif").read_bytes(), before)
        self.assertEqual(result["converted"], 0)
        self.assertIn("several frames", result["skipped"][0]["reason"])
        self.assertEqual(self.copies(), [])
        self.assertFalse(self.medias("flyer.png").exists())

    def test_a_source_that_does_not_open_is_skipped_and_nothing_is_written(self) -> None:
        self.medias("flyer.tga").write_bytes(b"not a picture at all")
        before = sorted(path.name for path in self.medias().iterdir())

        result = self.run_job(["medias/flyer.tga"], keep=True)

        self.assertEqual(result["skipped"][0]["reason"], "The picture could not be read")
        self.assertEqual(sorted(path.name for path in self.medias().iterdir()), before)
        self.assertEqual(self.copies(), [])


class WhatIsConvertedTests(ConvertCase):
    def test_a_video_named_for_the_playfield_converts_and_one_named_for_the_wheel_is_not_a_row(
            self) -> None:
        self.medias("(Playfield) Doom.avi").write_bytes(b"avi")
        self.medias("(Wheel) Doom.avi").write_bytes(b"avi")
        self.streams = encode.Streams("mpeg4", "mp3", "yuv420p", 1.0)

        result = self.run_job(["medias/(Playfield) Doom.avi", "medias/(Wheel) Doom.avi"],
                              keep=False)

        spec = spec_named("(Playfield) Doom.mp4")
        self.assertTrue(self.medias("(Playfield) Doom.mp4").is_file())
        assert spec is not None
        self.assertEqual((spec.kind, media_family(spec.kind)), ("playfield_video", "video"))
        self.assertIsNone(unsupported_named("(Wheel) Doom.avi"))
        self.assertEqual([one["path"] for one in result["skipped"]], ["medias/(Wheel) Doom.avi"])

    def test_the_playfield_is_named_by_the_variant_the_library_is_set_to(self) -> None:
        _save(self.medias("desktop.tga"), "TGA")
        _save(self.medias("table.tga"), "TGA")
        self.start(patch.object(media_convert.media_service, "media_settings",
                                return_value=("desktop", None)))

        found = {item.path: item.action for item in
                 self.plan("medias/desktop.tga", "medias/table.tga").items}

        self.assertEqual(found, {"medias/desktop.tga": media_convert.CONVERT,
                                 "medias/table.tga": media_convert.SKIP})

    def test_a_file_not_named_for_a_kind_is_left_alone(self) -> None:
        self.medias("holiday.tga").write_bytes(b"x")

        result = self.run_job(["medias/holiday.tga"], keep=False)

        self.assertEqual(result["converted"], 0)
        self.assertEqual(self.medias("holiday.tga").read_bytes(), b"x")

    def test_a_vp9_mkv_is_repackaged_never_encoded_whichever_codec_is_chosen_for_new_video(
            self) -> None:
        self.streams = encode.Streams("vp9", "opus", "yuv420p", 1.0)
        for video in (encode.VP9, encode.AV1):
            self.medias("bg.mkv").write_bytes(b"mkv")
            self.medias("bg.mp4").unlink(missing_ok=True)
            self.argvs.clear()

            item = self.plan("medias/bg.mkv", video=video, container=encode.MP4).items[0]
            self.run_job(["medias/bg.mkv"], keep=False, video=video, container=encode.MP4)

            self.assertEqual(item.action, media_convert.REPACKAGE, video)
            argv = self.argvs[0]
            self.assertEqual(argv[argv.index("-c:v") + 1], "copy", video)

    def test_a_webm_every_device_plays_is_not_converted_at_all(self) -> None:
        self.medias("(Backglass) Doom.webm").write_bytes(b"webm")
        self.streams = encode.Streams("vp9", "opus", "yuv420p", 1.0)

        item = self.plan("medias/(Backglass) Doom.webm", container=encode.WEBM,
                         video=encode.VP9).items[0]

        self.assertEqual(item.action, media_convert.SKIP)
        self.assertEqual(item.reason, "Every device already plays this file")

    def test_an_mkv_of_h264_is_repackaged_where_devices_play_it_and_encoded_where_one_does_not(
            self) -> None:
        self.medias("bg.mkv").write_bytes(b"mkv")

        played = self.plan("medias/bg.mkv", devices=[EVERYTHING]).items[0]
        refused = self.plan("medias/bg.mkv", devices=[NO_H264]).items[0]

        self.assertEqual(played.action, media_convert.REPACKAGE)
        self.assertEqual((refused.action, refused.target_format), (media_convert.CONVERT, "VP9"))

    def test_a_ten_bit_h264_is_encoded_not_copied(self) -> None:
        self.medias("bg.mkv").write_bytes(b"mkv")
        self.streams = encode.Streams("h264", "aac", "yuv420p10le", 1.0)

        self.assertEqual(self.plan("medias/bg.mkv").items[0].action, media_convert.CONVERT)

    def test_when_no_device_plays_any_codec_videos_are_left_alone_and_it_says_why(self) -> None:
        self.medias("bg.mkv").write_bytes(b"mkv")

        planned = self.plan("medias/bg.mkv", devices=[NO_VIDEO])

        self.assertEqual(planned.items[0].action, media_convert.SKIP)
        self.assertIn("None of the devices", planned.items[0].reason)
        self.assertIn("None of the devices", planned.video_blocked)

    def test_a_target_that_exists_is_left_under_leave_and_replaced_after_a_copy_under_replace(
            self) -> None:
        _save(self.medias("wheel.tga"), "TGA")
        before = self.medias("wheel.png").read_bytes()

        left = self.run_job(["medias/wheel.tga"], keep=True, on_clash=media_convert.LEAVE)

        self.assertEqual(self.medias("wheel.png").read_bytes(), before)
        self.assertEqual((left["converted"], left["skipped"][0]["reason"]),
                         (0, "wheel.png already exists, so the file was left as it was"))
        self.assertEqual(self.copies(), [])

        replaced = self.run_job(["medias/wheel.tga"], keep=True, on_clash=media_convert.REPLACE)

        self.assertEqual(replaced["converted"], 1)
        self.assertEqual(_opened(self.medias("wheel.png")).size, (40, 20))
        self.assertEqual(sorted(one["file"] for one in self.copies()),
                         ["medias/wheel.png", "medias/wheel.tga"])

    def test_two_files_that_would_be_written_to_one_name_leave_the_second_out(self) -> None:
        _save(self.medias("flyer.tga"), "TGA")
        _save(self.medias("flyer.tif"), "TIFF")

        planned = self.plan("medias/flyer.tga", "medias/flyer.tif")

        self.assertEqual([one.action for one in planned.items],
                         [media_convert.CONVERT, media_convert.SKIP])
        self.assertIn("also written as flyer.png", planned.items[1].reason)

    def test_a_name_already_ending_in_the_new_extension_keeps_its_spelling(self) -> None:
        self.assertEqual(media_convert._new_path("medias/bg.MP4", ".mp4"), "medias/bg.MP4")
        self.assertEqual(media_convert._new_path("medias/bg.mkv", ".mp4"), "medias/bg.mp4")

    def test_a_file_that_appears_while_converting_is_never_overwritten(self) -> None:
        _save(self.medias("flyer.tga"), "TGA")

        def appears(item: object, source: object, staged: pathlib.Path, *rest: object) -> None:
            self.medias("flyer.png").write_bytes(b"made by someone else")
            staged.write_bytes(b"made")

        with patch.object(media_convert, "_make", side_effect=appears):
            result = self.run_job(["medias/flyer.tga"], keep=False)

        self.assertEqual(result["converted"], 0)
        self.assertEqual(result["skipped"][0]["reason"],
                         "flyer.png already exists, so the file was left as it was")
        self.assertEqual(self.medias("flyer.png").read_bytes(), b"made by someone else")
        self.assertTrue(self.medias("flyer.tga").is_file())

    def test_a_link_and_a_path_outside_the_game_are_left_alone(self) -> None:
        outside = self.library.parent / "outside.tga"
        _save(outside, "TGA")
        self.medias("flyer.tga").symlink_to(outside)

        result = self.run_job(["medias/flyer.tga", "../../outside.tga", "medias/a/b.tga"],
                              keep=False)

        self.assertEqual((result["converted"], len(result["skipped"])), (0, 3))
        self.assertEqual(_opened(outside).format, "TGA")


class FfmpegTests(ConvertCase):
    def test_a_sound_is_made_as_chosen_at_the_chosen_quality(self) -> None:
        self.medias("audio.wav").write_bytes(b"wav")
        self.streams = encode.Streams("", "pcm_s16le", "", 1.0)

        self.run_job(["medias/audio.wav"], keep=False, sound=encode.MP3, quality=encode.HIGH)
        self.medias("audio.wav").write_bytes(b"wav")
        self.run_job(["medias/audio.wav"], keep=False, sound=encode.OGG, quality=encode.SMALL,
                     on_clash=media_convert.REPLACE)

        mp3, ogg = self.argvs
        self.assertEqual((mp3[mp3.index("-c:a") + 1], mp3[mp3.index("-b:a") + 1],
                          mp3[mp3.index("-f") + 1]), ("libmp3lame", "256k", "mp3"))
        self.assertEqual((ogg[ogg.index("-c:a") + 1], ogg[ogg.index("-b:a") + 1],
                          ogg[ogg.index("-f") + 1]), ("libopus", "96k", "ogg"))

    @staticmethod
    def video_argv(container: str, codec: str, encoder: str, sound: str,
                   keep: bool = True) -> list[str]:
        return encode.video_argv(
            pathlib.Path("ffmpeg"), pathlib.Path("in"), pathlib.Path("out"), container,
            codec=codec, encoder=encoder, quality=encode.STANDARD, threads=2, keep_sound=keep,
            source_streams=encode.Streams("mpeg4", sound, "yuv420p", 1.0), repackage=False)

    def test_a_video_forces_its_container_pixel_format_codec_threads_and_sound(self) -> None:
        mp4 = self.video_argv(encode.MP4, encode.H264, "libx264", "wmav2")
        webm = self.video_argv(encode.WEBM, encode.VP9, "libvpx-vp9", "aac")
        silent = self.video_argv(encode.MP4, encode.H264, "libx264", "aac", keep=False)

        self.assertEqual((mp4[-3], mp4[-2]), ("-f", "mp4"))
        self.assertIn("+faststart", mp4)
        self.assertEqual(mp4[mp4.index("-c:a") + 1], "libmp3lame")
        self.assertEqual((webm[-3], webm[-2]), ("-f", "webm"))
        self.assertNotIn("+faststart", webm)
        self.assertEqual(webm[webm.index("-c:a") + 1], "libopus")
        self.assertEqual(webm[webm.index("-threads") + 1], "2")
        self.assertEqual(webm[webm.index("-pix_fmt") + 1], "yuv420p")
        self.assertIn("-an", silent)
        self.assertNotIn("-c:a", silent)

    def test_sound_the_container_holds_is_copied(self) -> None:
        argv = self.video_argv(encode.MP4, encode.H264, "libx264", "mp3")

        self.assertEqual(argv[argv.index("-c:a") + 1], "copy")

    def test_webm_is_offered_with_vp9_and_av1_and_refused_with_h264(self) -> None:
        self.medias("bg.mkv").write_bytes(b"mkv")
        self.streams = encode.Streams("mpeg4", "aac", "yuv420p", 1.0)

        def webm_option(**choices: Any) -> media_convert.Option:
            container = next(one for one in self.plan("medias/bg.mkv", **choices).choices
                             if one.key == media_convert.CONTAINER)
            return next(one for one in container.options if one.value == encode.WEBM)

        with self.assertRaises(service_errors.RefusedError) as refused:
            self.plan("medias/bg.mkv", container=encode.WEBM, video=encode.H264)
        defaulted = self.plan("medias/bg.mkv", container=encode.WEBM)

        self.assertTrue(webm_option(video=encode.VP9).available)
        self.assertTrue(webm_option(video=encode.AV1).available)
        self.assertFalse(webm_option(video=encode.H264).available)
        self.assertIn("WebM cannot hold H.264", str(refused.exception))
        self.assertIn(defaulted.chosen.video, (encode.VP9, encode.AV1))

    def test_a_job_that_ffmpeg_fails_leaves_the_original_and_no_new_file(self) -> None:
        self.medias("audio.wav").write_bytes(b"wav")
        self.streams = encode.Streams("", "pcm_s16le", "", 1.0)

        with patch.object(encode, "run", side_effect=SkipError("FFmpeg could not convert: boom")):
            result = self.run_job(["medias/audio.wav"], keep=True)

        self.assertEqual(result["skipped"][0]["reason"], "FFmpeg could not convert: boom")
        self.assertEqual(self.medias("audio.wav").read_bytes(), b"wav")
        self.assertFalse(self.medias("audio.mp3").exists())
        self.assertEqual(self.copies(), [])

    def test_a_failing_ffmpeg_says_its_last_line(self) -> None:
        with patch.object(encode.subprocess, "Popen") as popen:
            popen.return_value.communicate.return_value = ("", "noise\nConversion failed!\n")
            popen.return_value.returncode = 1
            with self.assertRaises(SkipError) as caught:
                REAL_RUN(["ffmpeg"])

        self.assertEqual(str(caught.exception),
                         "FFmpeg could not convert the file: Conversion failed!")

    def test_a_failing_ffmpeg_never_shows_a_path(self) -> None:
        with patch.object(encode.subprocess, "Popen") as popen:
            popen.return_value.communicate.return_value = (
                "", "noise\n/Volumes/share/in.mkv: Invalid data found\n")
            popen.return_value.returncode = 1
            with self.assertRaises(SkipError) as caught:
                REAL_RUN(["ffmpeg"])

        self.assertEqual(str(caught.exception), "FFmpeg could not convert the file")

    def test_a_failure_nobody_planned_for_says_nothing_of_its_cause(self) -> None:
        _save(self.medias("flyer.tga"), "TGA")

        with patch.object(media_convert, "_convert_one",
                          side_effect=RuntimeError("/private/place boom")), \
                self.assertLogs(media_convert.logger, "WARNING"):
            result = self.run_job(["medias/flyer.tga"], keep=False)

        self.assertEqual(result["skipped"][0]["reason"],
                         "The file could not be converted, so it was left as it was")

    def test_a_conversion_that_takes_too_long_is_stopped(self) -> None:
        with patch.object(encode.subprocess, "Popen") as popen, \
                patch.object(encode, "_TIMEOUT", -1):
            popen.return_value.communicate.side_effect = [
                subprocess.TimeoutExpired("ffmpeg", 1), ("", "")]
            with self.assertRaises(SkipError) as caught:
                REAL_RUN(["ffmpeg"])

        self.assertEqual(str(caught.exception), "Converting the file took too long")
        popen.return_value.kill.assert_called_once()

    def test_a_result_that_is_empty_or_the_wrong_length_is_refused(self) -> None:
        staged = self.medias("made.mp3")
        staged.write_bytes(b"")
        with self.assertRaises(SkipError) as empty:
            REAL_CHECK(pathlib.Path("ffmpeg"), encode.Streams("", "wav", "", 10.0), staged)
        staged.write_bytes(b"audio")
        self.streams = encode.Streams("", "mp3", "", 2.0)
        with self.assertRaises(SkipError) as short:
            REAL_CHECK(pathlib.Path("ffmpeg"), encode.Streams("", "wav", "", 10.0), staged)

        self.assertIn("came out empty", str(empty.exception))
        self.assertIn("not the same length", str(short.exception))

    def test_a_result_that_cannot_be_read_back_is_refused(self) -> None:
        staged = self.medias("made.mp3")
        staged.write_bytes(b"audio")
        self.streams = encode.Streams("", "", "", 0.0)

        with self.assertRaises(SkipError) as caught:
            REAL_CHECK(pathlib.Path("ffmpeg"), encode.Streams("", "wav", "", 10.0), staged)

        self.assertIn("could not be read back", str(caught.exception))

    def test_a_result_within_a_second_of_the_original_is_accepted(self) -> None:
        staged = self.medias("made.mp3")
        staged.write_bytes(b"audio")
        self.streams = encode.Streams("", "mp3", "", 9.4)

        REAL_CHECK(pathlib.Path("ffmpeg"), encode.Streams("", "wav", "", 10.0), staged)


@unittest.skipUnless(_real_can("libmp3lame", "libx264"), "needs FFmpeg with MP3 and H.264")
class RealFfmpegTests(ConvertCase):
    fake_ffmpeg = False

    def make(self, *args: str) -> None:
        subprocess.run([str(_real().path), "-loglevel", "error", "-y", *args], check=True,
                       stdin=subprocess.DEVNULL)

    def test_a_wav_becomes_an_mp3_and_the_wav_is_gone(self) -> None:
        self.make("-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                  str(self.medias("audio.wav")))

        result = self.run_job(["medias/audio.wav"], keep=False)

        self.assertEqual((result["converted"], result["skipped"]), (1, []))
        self.assertGreater(self.medias("audio.mp3").stat().st_size, 0)
        self.assertFalse(self.medias("audio.wav").exists())

    def test_an_h264_mkv_is_repackaged_to_mp4_without_being_encoded(self) -> None:
        self.make("-f", "lavfi", "-i", "testsrc=size=64x32:rate=10:duration=1",
                  "-pix_fmt", "yuv420p", "-c:v", "libx264", str(self.medias("bg.mkv")))

        result = self.run_job(["medias/bg.mkv"], keep=False)

        self.assertEqual((result["converted"], result["repackaged"]), (1, 1), result)
        made = encode.streams(_real().path, self.medias("bg.mp4"))  # type: ignore[arg-type]
        self.assertEqual(made.video, "h264")
        self.assertFalse(self.medias("bg.mkv").exists())


class CopiesTests(ConvertCase):
    def test_the_copy_is_taken_once_for_a_conversion_about_to_happen_and_listed(self) -> None:
        _save(self.medias("wheel.tga"), "TGA")
        before = self.medias("wheel.tga").read_bytes()

        result = self.run_job(["medias/wheel.tga", "medias/wheel.tga", "medias/missing.tga"],
                              keep=True, on_clash=media_convert.REPLACE)

        kept = sorted(self.copies(), key=lambda one: one["file"])
        self.assertEqual([one["file"] for one in kept], ["medias/wheel.png", "medias/wheel.tga"])
        original = pathlib.Path(kept[1]["path"])
        self.assertEqual(original.read_bytes(), before)
        self.assertRegex(original.name,
                         rf"^wheel\.tga\.vpinfe-\d{{8}}T\d{{6}}Z-{REASON_CONVERT}$")
        self.assertEqual(result["kept"], 2, "the replaced PNG is kept as well")

    def test_dont_keep_takes_none(self) -> None:
        _save(self.medias("flyer.tga"), "TGA")

        result = self.run_job(["medias/flyer.tga"], keep=False)

        self.assertEqual((result["kept"], self.copies()), (0, []))

    def test_a_copy_that_cannot_be_kept_leaves_the_file_unconverted(self) -> None:
        _save(self.medias("flyer.tga"), "TGA")
        before = self.medias("flyer.tga").read_bytes()

        with patch.object(backup_media, "keep", side_effect=OSError("no room")):
            result = self.run_job(["medias/flyer.tga"], keep=True)

        self.assertEqual(self.medias("flyer.tga").read_bytes(), before)
        self.assertFalse(self.medias("flyer.png").exists())
        self.assertEqual(result["converted"], 0)

    def test_nowhere_to_keep_copies_refuses_before_anything_is_converted(self) -> None:
        _save(self.medias("flyer.tga"), "TGA")

        with patch.object(backup_folder, "for_writing",
                          side_effect=service_errors.BlockedError("not writable")):
            with self.assertRaises(service_errors.BlockedError):
                media_convert.start([self.target("medias/flyer.tga")], {}, [EVERYTHING])

        self.assertTrue(self.medias("flyer.tga").is_file())
        self.assertEqual(jobs.active(jobs.KIND_MEDIA_CONVERT), [])


class LedgerTests(ConvertCase):
    def test_the_new_path_is_the_users_with_no_md5_and_the_old_key_is_gone(self) -> None:
        _save(self.medias("flyer.tga"), "TGA")
        info = self.folder / f"{self.folder.name}.info"
        MetaConfig(str(info)).add_asset(str(self.medias("flyer.tga")), "vpsdb", "abc123")

        self.run_job(["medias/flyer.tga"], keep=False)

        held = MetaConfig(str(info)).data["assets"]
        self.assertNotIn("medias/flyer.tga", held)
        self.assertEqual(held["medias/flyer.png"], {"source": {"host": "user"}})


class WaitingTests(ConvertCase):
    def test_with_wait_it_pauses_while_a_table_launches_and_without_it_does_not(self) -> None:
        _save(self.medias("flyer.tga"), "TGA")
        _save(self.medias("poster.tga"), "TGA")
        pauses: list[int] = []

        def pause(seconds: float) -> None:
            pauses.append(1)
            if len(pauses) == 2:
                self.playing = False

        self.playing = True
        with patch.object(media_convert, "_pause", side_effect=pause):
            self.run_job(["medias/flyer.tga"], keep=False, wait_while_playing=True)
        waited = len(pauses)
        self.playing = True
        pauses.clear()
        with patch.object(media_convert, "_pause", side_effect=pause):
            self.run_job(["medias/poster.tga"], keep=False, wait_while_playing=False)

        self.assertEqual((waited, pauses), (2, []))

    def test_a_wait_ends_at_shutdown_and_the_file_it_waited_for_is_left_alone(self) -> None:
        _save(self.medias("flyer.tga"), "TGA")
        self.playing = True
        paused: list[int] = []

        with patch.object(media_convert, "_pause", side_effect=lambda seconds: paused.append(1)), \
                patch.object(shutdown, "requested", side_effect=lambda: bool(paused)):
            result = self.run_job(["medias/flyer.tga"], keep=False, wait_while_playing=True)

        self.assertEqual(len(paused), 1)
        self.assertEqual(result["converted"], 0)
        self.assertEqual([one["reason"] for one in result["skipped"]], ["Conversion was stopped"])
        self.assertTrue(self.medias("flyer.tga").is_file())

    def test_a_stop_reports_every_file_it_did_not_reach(self) -> None:
        _save(self.medias("flyer.tga"), "TGA")
        _save(self.medias("poster.tga"), "TGA")

        with patch.object(shutdown, "requested", return_value=True):
            result = self.run_job(["medias/flyer.tga", "medias/poster.tga"], keep=False)

        self.assertEqual([(one["path"], one["reason"]) for one in result["skipped"]],
                         [("medias/flyer.tga", "Conversion was stopped"),
                          ("medias/poster.tga", "Conversion was stopped")])


class OneAtATimeTests(ConvertCase):
    def test_a_second_conversion_is_refused_busy_and_so_is_a_turn_either_way(self) -> None:
        _save(self.medias("flyer.tga"), "TGA")
        body = {"files": [{"game_id": GAME, "path": "medias/flyer.tga"}], "keep": False}
        turn = {"files": [{"game_id": GAME, "path": "medias/wheel.png"}], "turn": "left",
                "keep": False}

        with jobs.track(jobs.KIND_MEDIA_CONVERT):
            again = self.client.post("/library/media/convert", json=body)
            turning = self.client.post("/library/media/rotate", json=turn)
        with jobs.track(jobs.KIND_MEDIA_ROTATE):
            converting = self.client.post("/library/media/convert", json=body)

        self.assertEqual((again.status_code, turning.status_code, converting.status_code),
                         (409, 409, 409))


class PreferredFormatsTests(ConvertCase):
    """What the Preferred Formats change: where a choice starts and which option says so."""

    def hold(self, **held: object) -> None:
        self.start(patch.object(
            media_convert.convert_preferences, "read",
            return_value=media_convert.convert_preferences.Preferences(**cast(Any, held))))

    def everything(self, *, devices: list[media_convert.Device] | None = None,
                   **choices: object) -> media_convert.Plan:
        _save(self.medias("flyer.tga"), "TGA")
        self.medias("audio.wav").write_bytes(b"wav")
        self.medias("bg.mkv").write_bytes(b"mkv")
        self.streams = encode.Streams("mpeg4", "aac", "yuv420p", 1.0)
        return self.plan("medias/flyer.tga", "medias/audio.wav", "medias/bg.mkv",
                         devices=devices, **choices)

    def option(self, planned: media_convert.Plan, key: str,
               value: object) -> media_convert.Option:
        choice = next(one for one in planned.choices if one.key == key)
        return next(one for one in choice.options if one.value == value)

    def test_a_choice_nobody_made_starts_at_the_preference(self) -> None:
        self.hold(picture=encode.WEBP, sound=encode.OGG, video=encode.VP9,
                  quality=encode.HIGH, keep=False)

        chosen = self.everything().chosen

        self.assertEqual((chosen.picture, chosen.sound, chosen.video, chosen.quality,
                          chosen.keep),
                         (encode.WEBP, encode.OGG, encode.VP9, encode.HIGH, False))

    def test_a_choice_that_was_made_beats_the_preference(self) -> None:
        self.hold(picture=encode.WEBP, video=encode.VP9, keep=False)

        chosen = self.everything(picture=encode.JPG, video=encode.H264, keep=True).chosen

        self.assertEqual((chosen.picture, chosen.video, chosen.keep),
                         (encode.JPG, encode.H264, True))

    def test_the_preferred_option_says_so_and_the_advised_one_stays_recommended(self) -> None:
        self.hold(picture=encode.WEBP, keep=False)
        planned = self.everything()

        webp = self.option(planned, media_convert.PICTURE, encode.WEBP)
        png = self.option(planned, media_convert.PICTURE, encode.PNG)
        keep, drop = (self.option(planned, media_convert.KEEP, value) for value in (True, False))

        self.assertEqual((webp.preferred, webp.recommended), (True, False))
        self.assertEqual((png.preferred, png.recommended), (False, True))
        self.assertEqual((keep.preferred, keep.recommended), (False, True))
        self.assertEqual((drop.preferred, drop.recommended), (True, False))

    def test_a_preference_that_is_also_the_recommendation_is_both(self) -> None:
        self.hold(picture=encode.PNG)

        png = self.option(self.everything(), media_convert.PICTURE, encode.PNG)

        self.assertEqual((png.preferred, png.recommended), (True, True))

    def test_without_a_preference_nothing_is_preferred(self) -> None:
        planned = self.everything()

        self.assertFalse([option for choice in planned.choices for option in choice.options
                          if option.preferred])

    def test_a_preferred_codec_the_container_cannot_hold_takes_the_recommendation(self) -> None:
        self.hold(video=encode.H264)

        webm = self.everything(container=encode.WEBM)
        mp4 = self.everything()

        self.assertEqual(webm.chosen.video, encode.VP9)
        self.assertFalse(self.option(webm, media_convert.VIDEO, encode.H264).preferred)
        self.assertEqual(mp4.chosen.video, encode.H264)

    def test_a_preferred_format_this_ffmpeg_cannot_write_is_not_the_start(self) -> None:
        self.hold(video=encode.VP9, sound=encode.OGG)
        self.use_ffmpeg(_found(frozenset({"libx264", "libmp3lame"})))

        planned = self.everything()

        self.assertEqual((planned.chosen.video, planned.chosen.sound),
                         (encode.H264, encode.MP3))
        self.assertFalse(self.option(planned, media_convert.VIDEO, encode.VP9).preferred)
        self.assertFalse(self.option(planned, media_convert.SOUND, encode.OGG).preferred)

    def test_the_job_makes_what_the_ask_started_at(self) -> None:
        self.hold(picture=encode.JPG, keep=False)
        _save(self.medias("flyer.tga"), "TGA")

        planned = self.plan("medias/flyer.tga")
        result = self.run_job(["medias/flyer.tga"])

        self.assertEqual(planned.chosen.picture, encode.JPG)
        self.assertEqual(result["converted"], 1)
        self.assertTrue(self.medias("flyer.jpg").is_file())
        self.assertEqual(self.copies(), [], "the originals were not to be kept")


class PlanTests(ConvertCase):
    def everything(self) -> media_convert.Plan:
        _save(self.medias("flyer.tga"), "TGA")
        _save(self.medias("logo.ico"), "ICO", (32, 32))
        self.medias("audio.wav").write_bytes(b"wav")
        self.medias("bg.mkv").write_bytes(b"mkv")
        self.streams = encode.Streams("mpeg4", "aac", "yuv420p", 1.0)
        return self.plan("medias/flyer.tga", "medias/logo.ico", "medias/audio.wav",
                         "medias/bg.mkv", devices=[NO_H264, LAPTOP])

    def test_the_counts_are_the_jobs_converted_renamed_and_skipped(self) -> None:
        self.medias("flyer.jfif").write_bytes(png(40, 20, fmt="JPEG"))
        self.medias("holiday.tga").write_bytes(b"x")
        _save(self.medias("logo.tga"), "TGA")
        paths = ["medias/logo.tga", "medias/flyer.jfif", "medias/holiday.tga",
                 "medias/missing.tga"]

        planned = self.plan(*paths, picture=encode.JPG)
        result = self.run_job(paths, keep=False, picture=encode.JPG)

        counts = planned.counts()
        self.assertEqual(counts[media_convert.CONVERT] + counts[media_convert.REPACKAGE],
                         result["converted"])
        self.assertEqual(counts[media_convert.RENAME], result["renamed"])
        self.assertEqual(counts[media_convert.SKIP], len(result["skipped"]))
        self.assertEqual(sum(counts.values()), len(paths))

    def test_every_choice_has_exactly_one_recommended_option_and_a_sentence_on_each(self) -> None:
        planned = self.everything()

        self.assertEqual({one.key for one in planned.choices},
                         {media_convert.PICTURE, media_convert.SOUND, media_convert.CONTAINER,
                          media_convert.VIDEO, media_convert.QUALITY,
                          media_convert.SOUND_IN_VIDEO, media_convert.KEEP, media_convert.WAIT})
        for choice in planned.choices:
            recommended = [one for one in choice.options if one.recommended]
            self.assertEqual(len(recommended), 1, choice.key)
            self.assertTrue(choice.label, choice.key)
            for option in choice.options:
                self.assertTrue(option.why.strip(), (choice.key, option.value))

    def test_a_choice_with_nothing_to_decide_is_absent(self) -> None:
        _save(self.medias("flyer.tga"), "TGA")

        keys = {one.key for one in self.plan("medias/flyer.tga").choices}

        self.assertEqual(keys, {media_convert.PICTURE, media_convert.KEEP, media_convert.WAIT})

    def test_the_clash_choice_appears_only_where_a_name_is_taken(self) -> None:
        _save(self.medias("wheel.tga"), "TGA")
        _save(self.medias("flyer.tga"), "TGA")

        both = {one.key for one in self.plan("medias/wheel.tga", "medias/flyer.tga").choices}
        only = {one.key for one in self.plan("medias/flyer.tga").choices}

        self.assertIn(media_convert.ON_CLASH, both)
        self.assertNotIn(media_convert.ON_CLASH, only)

    def test_the_video_options_name_each_device_that_will_not_play_them(self) -> None:
        video = next(one for one in self.everything().choices if one.key == media_convert.VIDEO)
        said = {one.value: one.why for one in video.options}

        self.assertIn("Cabinet", said[encode.H264])
        self.assertIn("Laptop", said[encode.VP9])
        self.assertIn("Laptop", said[encode.AV1])
        self.assertNotIn("Laptop", said[encode.H264])

    def test_the_recommended_codec_is_the_first_every_device_plays(self) -> None:
        third = media_convert.Device("Third", {"h264": False, "vp9": True})
        ffmpeg = media_convert.Ffmpeg()

        self.assertEqual(media_convert.recommended_video([EVERYTHING], ffmpeg), encode.H264)
        self.assertEqual(media_convert.recommended_video([NO_H264], ffmpeg), encode.VP9)
        self.assertEqual(media_convert.recommended_video([LAPTOP, NO_H264, third], ffmpeg),
                         encode.VP9, "nothing is played by all, so the one fewest refuse")

    def test_an_encoder_ffmpeg_lacks_is_unavailable_and_never_recommended(self) -> None:
        self.use_ffmpeg(_found(frozenset({"libx264", "libmp3lame"})))
        self.medias("bg.mkv").write_bytes(b"mkv")
        self.streams = encode.Streams("mpeg4", "aac", "yuv420p", 1.0)

        planned = self.plan("medias/bg.mkv", devices=[EVERYTHING, LAPTOP])
        video = next(one for one in planned.choices if one.key == media_convert.VIDEO)

        self.assertEqual({one.value: one.available for one in video.options},
                         {encode.H264: True, encode.VP9: False, encode.AV1: False})
        self.assertEqual([one.value for one in video.options if one.recommended],
                         [encode.H264])

    def test_the_defaults_are_the_first_option_ffmpeg_can_make(self) -> None:
        self.use_ffmpeg(_found(frozenset({"libvpx-vp9", "libopus"})))
        self.medias("audio.wav").write_bytes(b"wav")
        self.medias("bg.mkv").write_bytes(b"mkv")
        self.streams = encode.Streams("mpeg4", "aac", "yuv420p", 1.0)

        planned = self.plan("medias/audio.wav", "medias/bg.mkv")
        sound = next(one for one in planned.choices if one.key == media_convert.SOUND)

        self.assertEqual([one.value for one in sound.options if one.recommended], [encode.OGG])
        self.assertEqual((planned.chosen.sound, planned.chosen.video),
                         (encode.OGG, encode.VP9))

    def test_the_codec_agrees_with_what_recording_picks(self) -> None:
        for state, plays in ((frontend_browser.PLAYS, True), (frontend_browser.NO_H264, False)):
            device = media_convert.Device("Here", {"h264": plays, "vp9": True, "av1": None})

            ours = media_convert.recommended_video([device], media_convert.Ffmpeg())

            self.assertEqual(ours, capture.video_codec(capture.AUTO, state), state)


class RouteTests(ConvertCase):
    def body(self, **more: object) -> dict:
        return {"files": [{"game_id": GAME, "path": "medias/flyer.tga"}],
                "devices": [{"label": "Cabinet", "plays": {"h264": True, "vp9": True}}], **more}

    def wait(self, response: object) -> dict:
        job = jobs.get(cast(MagicMock, response).json()["id"])
        assert job is not None
        while job.state == jobs.RUNNING:
            time.sleep(0.01)
        return cast(dict, job.result)

    def test_convertible_answers_the_plan_without_changing_anything(self) -> None:
        _save(self.medias("flyer.tga"), "TGA")

        response = self.client.post("/library/media/convertible", json=self.body())

        self.assertEqual(response.status_code, 200, response.text)
        plan = response.json()
        self.assertEqual(plan["counts"]["convert"], 1)
        self.assertIn("picture", plan["defaulted"])
        self.assertTrue(self.medias("flyer.tga").is_file())
        self.assertFalse(self.medias("flyer.png").exists())

    def test_the_option_a_preference_names_says_so_over_the_wire(self) -> None:
        _save(self.medias("flyer.tga"), "TGA")
        held = media_convert.convert_preferences.Preferences(picture="webp", keep=False)

        with patch.object(media_convert.convert_preferences, "read", return_value=held):
            plan = self.client.post("/library/media/convertible", json=self.body()).json()

        choices = {one["key"]: one["options"] for one in plan["options"]}
        self.assertEqual(plan["choices"]["picture"], "webp")
        self.assertEqual({"webp"}, {one["value"] for one in choices["picture"] if one["preferred"]})
        self.assertEqual({"png"},
                         {one["value"] for one in choices["picture"] if one["recommended"]})
        self.assertEqual({False},
                         {one["value"] for one in choices["keep"] if one["preferred"]})

    def test_with_no_choices_the_recommendations_are_used_and_the_result_says_so(self) -> None:
        _save(self.medias("flyer.tga"), "TGA")

        response = self.client.post("/library/media/convert", json=self.body())
        result = self.wait(response)

        self.assertEqual(response.status_code, 202, response.text)
        self.assertEqual(result["choices"]["picture"], "png")
        self.assertIn("picture", result["defaulted"])
        self.assertEqual(result["kept"], 1, "copies are kept unless the caller says not")

    def test_with_choices_those_are_used(self) -> None:
        _save(self.medias("flyer.tga"), "TGA")

        response = self.client.post("/library/media/convert",
                                    json=self.body(picture="webp", keep=False))
        result = self.wait(response)

        self.assertEqual(_opened(self.medias("flyer.webp")).format, "WEBP")
        self.assertNotIn("picture", result["defaulted"])
        self.assertEqual(result["kept"], 0)

    def test_a_value_that_is_not_an_option_is_refused_and_nothing_starts(self) -> None:
        _save(self.medias("flyer.tga"), "TGA")

        response = self.client.post("/library/media/convert", json=self.body(picture="gif"))

        self.assertGreaterEqual(response.status_code, 400)
        self.assertEqual(jobs.active(jobs.KIND_MEDIA_CONVERT), [])
        self.assertTrue(self.medias("flyer.tga").is_file())

    def test_no_backups_folder_with_keep_is_a_409(self) -> None:
        _save(self.medias("flyer.tga"), "TGA")

        with patch.object(backup_folder, "for_writing",
                          side_effect=service_errors.BlockedError("not writable")):
            response = self.client.post("/library/media/convert", json=self.body(keep=True))

        self.assertEqual(response.status_code, 409)
        self.assertTrue(self.medias("flyer.tga").is_file())


class ScopeTests(ConvertCase):
    def setUp(self) -> None:
        super().setUp()
        self.addCleanup(auth.set_policy, auth.SignInPolicy())

    def grant(self, *granted: str) -> None:
        class Policy:
            name = "test"

            def identify(self, request: object) -> auth.Identity:
                return auth.Identity(name="test", scopes=frozenset(granted))

        auth.set_policy(Policy())  # type: ignore[arg-type]

    def test_the_plan_takes_games_read_and_the_job_takes_games_write(self) -> None:
        _save(self.medias("flyer.tga"), "TGA")
        body = {"files": [{"game_id": GAME, "path": "medias/flyer.tga"}], "keep": False}

        self.grant(scopes.GAMES_READ)
        read_plans = self.client.post("/library/media/convertible", json=body).status_code
        read_converts = self.client.post("/library/media/convert", json=body).status_code
        self.grant(scopes.GAMES_WRITE)
        write_plans = self.client.post("/library/media/convertible", json=body).status_code

        self.assertEqual((read_plans, read_converts, write_plans), (200, 403, 403))


if __name__ == "__main__":
    unittest.main()
