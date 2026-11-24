"""Turning pictures and videos in place, and the copies kept before it.

What is worth pinning: a file that cannot be turned is left exactly as it was while the rest
of the batch goes on, a copy exists before the file it is a copy of changes, and the copy can
be listed, put back and deleted like any other.
"""

from __future__ import annotations

import io
import pathlib
import subprocess
import time
import unittest
from typing import cast
from unittest.mock import MagicMock, patch

from PIL import Image

from common import service_errors
from common.backups import folder as backup_folder
from common.backups import media as backup_media
from common.games import game_repository, media_placement, media_rotate
from common.games.game_identity import game_id as game_id_of
from common.host import tools
from common.runtime import jobs
from tests.api.test_backups import BackupsCase
from tests.support.library import write_game
from tests.support.pictures import RED, animation, banded, png

GAME = "id-Taxi"
NAME = "Taxi"
WIDE = (60, 30)


def _size(data: bytes) -> tuple[int, int]:
    return Image.open(io.BytesIO(data)).size


def _format(path: pathlib.Path) -> str | None:
    return Image.open(io.BytesIO(path.read_bytes())).format


def _pixel(data: bytes, where: tuple[int, int]) -> tuple[int, ...]:
    return Image.open(io.BytesIO(data)).convert("RGB").getpixel(where)  # type: ignore[return-value]


def _ffmpeg() -> pathlib.Path | None:
    found = tools.resolve(tools.FFMPEG)
    return found.path if found.state == tools.State.FOUND else None


def _video(path: pathlib.Path, size: str = "64x32") -> None:
    subprocess.run([str(_ffmpeg()), "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    f"testsrc=size={size}:rate=10:duration=1", "-pix_fmt", "yuv420p", str(path)],
                   check=True, stdin=subprocess.DEVNULL)


def _video_size(path: pathlib.Path) -> tuple[int, int]:
    found = subprocess.run(
        [str(_ffmpeg()).replace("ffmpeg", "ffprobe"), "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", str(path)],
        capture_output=True, text=True, check=True, stdin=subprocess.DEVNULL)
    width, height = found.stdout.strip().split("x")
    return int(width), int(height)


class RotateCase(BackupsCase):
    def setUp(self) -> None:
        super().setUp()
        self.folder = write_game(self.library, NAME, vpx=True,
                                 info={"Info": {"Title": NAME}, "vpinfe": {"schema": 2,
                                                                          "game_id": GAME}},
                                 medias={"wheel.png": banded(*WIDE), "loop.gif": animation(),
                                         "theme.mp3": b"not audio"},
                                 files={"fanart.jpg": b""})
        self.bigger = self.library.parent / "bigger"
        self.bigger.mkdir()
        self.catalog = patch.object(
            game_repository, "catalog",
            side_effect=lambda: {game_id_of(game): game for game in self.library_games()})
        self.catalog.start()
        self.addCleanup(self.catalog.stop)
        for patcher in (patch.object(media_rotate.media_ops, "reread"),
                        patch.object(media_placement, "record_origin")):
            patcher.start()
            self.addCleanup(patcher.stop)

    def wheel(self) -> pathlib.Path:
        return self.folder / "medias" / "wheel.png"

    def run_job(self, targets: list[tuple[str, str]], turn: str, keep: bool) -> dict:
        job = media_rotate.start([media_rotate.Target(*one) for one in targets], turn, keep)
        while job.state == jobs.RUNNING:
            time.sleep(0.01)
        self.assertEqual(job.state, jobs.DONE, job.error)
        return cast(dict, job.result)

    def copies(self) -> list[dict]:
        return [one for one in self.listing() if one["kind"] == "media"]


class TurningPicturesTests(RotateCase):
    def test_a_turn_to_the_left_swaps_the_sides_and_puts_the_left_edge_at_the_bottom(self) -> None:
        result = self.run_job([(GAME, "medias/wheel.png")], media_rotate.LEFT, keep=False)

        data = self.wheel().read_bytes()
        self.assertEqual(_size(data), (WIDE[1], WIDE[0]))
        self.assertEqual(_pixel(data, (WIDE[1] // 2, WIDE[0] - 1)), RED)
        self.assertEqual((result["turned"], result["games"], result["skipped"]), (1, 1, []))

    def test_a_turn_to_the_right_puts_the_left_edge_at_the_top(self) -> None:
        self.run_job([(GAME, "medias/wheel.png")], media_rotate.RIGHT, keep=False)

        self.assertEqual(_pixel(self.wheel().read_bytes(), (WIDE[1] // 2, 0)), RED)

    def test_a_turn_around_keeps_the_size_and_puts_the_left_edge_at_the_right(self) -> None:
        self.run_job([(GAME, "medias/wheel.png")], media_rotate.AROUND, keep=False)

        data = self.wheel().read_bytes()
        self.assertEqual(_size(data), WIDE)
        self.assertEqual(_pixel(data, (WIDE[0] - 1, WIDE[1] // 2)), RED)

    def test_a_jpeg_is_still_a_jpeg_and_a_webp_a_webp(self) -> None:
        (self.folder / "medias" / "a.jpg").write_bytes(png(40, 20, fmt="JPEG"))
        (self.folder / "medias" / "b.webp").write_bytes(png(40, 20, fmt="WEBP"))

        self.run_job([(GAME, "medias/a.jpg"), (GAME, "medias/b.webp")],
                     media_rotate.LEFT, keep=False)

        formats = [_format(self.folder / "medias" / name) for name in ("a.jpg", "b.webp")]
        self.assertEqual(formats, ["JPEG", "WEBP"])
        self.assertEqual(_size((self.folder / "medias" / "a.jpg").read_bytes()), (20, 40))

    def test_a_picture_in_the_game_folder_itself_is_turned(self) -> None:
        (self.folder / "flyer.png").write_bytes(png(40, 20))

        self.run_job([(GAME, "flyer.png")], media_rotate.LEFT, keep=False)

        self.assertEqual(_size((self.folder / "flyer.png").read_bytes()), (20, 40))

    def test_a_sideways_photo_is_turned_as_it_is_seen_and_its_tag_goes(self) -> None:
        tagged = Image.Exif()
        tagged[0x0112] = 6
        raw = io.BytesIO()
        Image.new("RGB", WIDE).save(raw, format="JPEG", exif=tagged)
        (self.folder / "medias" / "photo.jpg").write_bytes(raw.getvalue())

        self.run_job([(GAME, "medias/photo.jpg")], media_rotate.LEFT, keep=False)

        turned = Image.open(io.BytesIO((self.folder / "medias" / "photo.jpg").read_bytes()))
        self.assertEqual(turned.size, WIDE, "seen as 30x60, turned left it is 60x30")
        self.assertIn(turned.getexif().get(0x0112), (None, 1))

    def test_the_same_file_named_twice_is_turned_once(self) -> None:
        result = self.run_job([(GAME, "medias/wheel.png"), (GAME, "medias/wheel.png")],
                              media_rotate.LEFT, keep=True)

        self.assertEqual(_size(self.wheel().read_bytes()), (WIDE[1], WIDE[0]))
        self.assertEqual((result["turned"], len(self.copies())), (1, 1))

    def test_a_picture_in_a_media_set_is_turned_and_its_copy_keeps_the_folder(self) -> None:
        sets = self.folder / "medias" / "wheels" / "classic"
        sets.mkdir(parents=True)
        (sets / "a.png").write_bytes(png(40, 20))

        self.run_job([(GAME, "medias/wheels/classic/a.png")], media_rotate.LEFT, keep=True)

        self.assertEqual(_size((sets / "a.png").read_bytes()), (20, 40))
        self.assertEqual([one["file"] for one in self.copies()], ["medias/wheels/classic/a.png"])

    def test_the_game_is_read_again_and_the_file_is_recorded_as_the_users(self) -> None:
        self.run_job([(GAME, "medias/wheel.png")], media_rotate.LEFT, keep=False)

        cast(MagicMock, media_placement.record_origin).assert_called_once_with(
            self.folder, self.wheel())
        cast(MagicMock, media_rotate.media_ops.reread).assert_called_once_with(self.folder)


class WhatIsLeftAloneTests(RotateCase):
    def test_a_file_that_cannot_be_turned_is_skipped_and_the_rest_are_turned(self) -> None:
        before = (self.folder / "medias" / "loop.gif").read_bytes()
        result = self.run_job([(GAME, "medias/loop.gif"), (GAME, "medias/wheel.png"),
                               (GAME, "medias/theme.mp3"), (GAME, "medias/nothing.png"),
                               (GAME, "../../escape.png"), (GAME, "medias/a/b.png"),
                               ("id-Nobody", "medias/wheel.png")],
                              media_rotate.LEFT, keep=True)

        self.assertEqual(result["turned"], 1)
        self.assertEqual({one["path"] for one in result["skipped"]},
                         {"medias/loop.gif", "medias/theme.mp3", "medias/nothing.png",
                          "../../escape.png", "medias/a/b.png", "medias/wheel.png"})
        self.assertEqual((self.folder / "medias" / "loop.gif").read_bytes(), before)
        self.assertEqual(len(self.copies()), 1, "a skipped file takes no copy")

    def test_a_link_is_not_followed(self) -> None:
        outside = self.library.parent / "outside.png"
        outside.write_bytes(png(40, 20))
        (self.folder / "medias" / "link.png").symlink_to(outside)

        result = self.run_job([(GAME, "medias/link.png")], media_rotate.LEFT, keep=False)

        self.assertEqual(result["turned"], 0)
        self.assertEqual(_size(outside.read_bytes()), (40, 20))

    def test_a_turn_that_fails_leaves_the_file_and_no_stray_files(self) -> None:
        before = self.wheel().read_bytes()
        with patch.object(media_rotate, "_turn_image", side_effect=OSError("disk full")):
            result = self.run_job([(GAME, "medias/wheel.png")], media_rotate.LEFT, keep=True)

        self.assertEqual(self.wheel().read_bytes(), before)
        self.assertEqual(result["turned"], 0)
        self.assertEqual(sorted(path.name for path in (self.folder / "medias").iterdir()),
                         ["loop.gif", "theme.mp3", "wheel.png"])
        self.assertEqual(self.copies(), [])

    def test_a_copy_that_cannot_be_kept_leaves_the_file_unturned(self) -> None:
        before = self.wheel().read_bytes()
        with patch.object(backup_media, "keep", side_effect=OSError("no room")):
            result = self.run_job([(GAME, "medias/wheel.png")], media_rotate.LEFT, keep=True)

        self.assertEqual(self.wheel().read_bytes(), before)
        self.assertEqual(result["turned"], 0)

    def test_a_path_that_climbs_or_names_a_drive_turns_nothing(self) -> None:
        outside = self.library.parent / "x.png"
        outside.write_bytes(png(40, 20))

        result = self.run_job([(GAME, "..\\..\\x.png"), (GAME, "medias\\..\\..\\x.png"),
                               (GAME, "C:x.png"), (GAME, "medias/../../x.png")],
                              media_rotate.LEFT, keep=True)

        self.assertEqual(result["turned"], 0)
        self.assertEqual(len(result["skipped"]), 4)
        self.assertEqual(_size(outside.read_bytes()), (40, 20))
        self.assertEqual(self.copies(), [])

    def test_a_folder_that_is_a_link_is_not_followed(self) -> None:
        outside = self.library.parent / "elsewhere"
        outside.mkdir()
        (outside / "p.png").write_bytes(png(40, 20))
        (self.folder / "medias" / "sub").symlink_to(outside, target_is_directory=True)

        result = self.run_job([(GAME, "medias/sub/p.png")], media_rotate.LEFT, keep=False)

        self.assertEqual(result["turned"], 0)
        self.assertEqual(_size((outside / "p.png").read_bytes()), (40, 20))

    def test_a_file_another_job_changed_while_it_was_turned_is_left_as_that_job_wrote_it(
            self) -> None:
        real = media_rotate._turn_image
        newer = png(50, 50)

        def turn_and_change(source: pathlib.Path, staged: pathlib.Path, ccw: int) -> None:
            real(source, staged, ccw)
            source.write_bytes(newer)

        with patch.object(media_rotate, "_turn_image", side_effect=turn_and_change):
            result = self.run_job([(GAME, "medias/wheel.png")], media_rotate.LEFT, keep=True)

        self.assertEqual(self.wheel().read_bytes(), newer)
        self.assertEqual(result["turned"], 0)
        self.assertEqual(len(result["skipped"]), 1)
        self.assertEqual(self.copies(), [])

    def test_something_it_was_not_asked_to_turn_is_left_alone(self) -> None:
        before = (self.folder / "medias" / "theme.mp3").read_bytes()

        self.run_job([(GAME, "medias/wheel.png")], media_rotate.LEFT, keep=False)

        self.assertEqual((self.folder / "medias" / "theme.mp3").read_bytes(), before)


class RefusalTests(RotateCase):
    def test_an_unknown_turn_and_an_empty_list_are_refused(self) -> None:
        with self.assertRaises(service_errors.RefusedError):
            media_rotate.start([media_rotate.Target(GAME, "medias/wheel.png")], "up", False)
        with self.assertRaises(service_errors.RefusedError):
            media_rotate.start([], media_rotate.LEFT, False)

    def test_nowhere_to_keep_copies_refuses_before_anything_is_turned(self) -> None:
        before = self.wheel().read_bytes()
        with patch.object(backup_folder, "for_writing",
                          side_effect=service_errors.BlockedError("not writable")):
            with self.assertRaises(service_errors.BlockedError):
                media_rotate.start([media_rotate.Target(GAME, "medias/wheel.png")],
                                   media_rotate.LEFT, True)

        self.assertEqual(self.wheel().read_bytes(), before)
        self.assertEqual(jobs.active(jobs.KIND_MEDIA_ROTATE), [])

    def test_it_will_not_run_beside_another_turn(self) -> None:
        with jobs.track(jobs.KIND_MEDIA_ROTATE):
            response = self.client.post(
                "/library/media/rotate",
                json={"files": [{"game_id": GAME, "path": "medias/wheel.png"}],
                      "turn": "left", "keep": False})

        self.assertEqual(response.status_code, 409)

    def test_the_route_accepts_and_hands_back_a_job(self) -> None:
        response = self.client.post(
            "/library/media/rotate",
            json={"files": [{"game_id": GAME, "path": "medias/wheel.png"}], "turn": "left"})
        job = jobs.get(response.json()["id"])
        while job is not None and job.state == jobs.RUNNING:
            time.sleep(0.01)

        assert job is not None
        self.assertEqual(response.status_code, 202, response.text)
        self.assertEqual(response.json()["kind"], jobs.KIND_MEDIA_ROTATE)
        self.assertIn("/api/v1/jobs/", response.headers["Location"])
        self.assertEqual(cast(dict, job.result)["kept"], 1,
                         "copies are kept unless the caller says not")


class TheCopyTests(RotateCase):
    def setUp(self) -> None:
        super().setUp()
        self.before = self.wheel().read_bytes()
        self.run_job([(GAME, "medias/wheel.png")], media_rotate.LEFT, keep=True)

    def test_the_copy_is_the_file_as_it_was_and_sits_under_the_game_id(self) -> None:
        (found,) = self.copies()

        kept = pathlib.Path(found["path"])
        self.assertEqual(kept.read_bytes(), self.before)
        self.assertEqual(kept.parent, self.library.parent / "config" / "backups" / "media"
                         / GAME / "medias")
        self.assertRegex(kept.name, r"^wheel\.png\.vpinfe-\d{8}T\d{6}Z-turn$")

    def test_it_is_listed_as_a_media_copy_of_the_game(self) -> None:
        (found,) = self.copies()

        self.assertEqual((found["subject_kind"], found["subject_id"], found["subject_name"]),
                         ("game", GAME, NAME))
        self.assertEqual((found["file"], found["reason"], found["gone"]),
                         ("medias/wheel.png", "turn", False))

    def test_it_is_listed_with_the_game_it_is_a_copy_of(self) -> None:
        listed = self.client.get(f"/backups?subject_kind=game&subject_id={GAME}").json()

        self.assertEqual([one["kind"] for one in listed["copies"]], ["media"])

    def test_two_turns_in_a_row_keep_two_copies(self) -> None:
        self.run_job([(GAME, "medias/wheel.png")], media_rotate.LEFT, keep=True)

        self.assertEqual(len(self.copies()), 2)

    def test_putting_it_back_restores_the_file_and_keeps_what_it_replaced(self) -> None:
        (found,) = self.copies()
        turned = self.wheel().read_bytes()

        response = self.client.post(f"/backups/{found['id']}/restore")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.wheel().read_bytes(), self.before)
        reasons = sorted(one["reason"] for one in self.copies())
        self.assertEqual(reasons, ["restore", "turn"])
        kept = next(pathlib.Path(one["path"]) for one in self.copies()
                    if one["reason"] == "restore")
        self.assertEqual(kept.read_bytes(), turned)

    def test_deleting_it_removes_the_folders_it_leaves_empty(self) -> None:
        (found,) = self.copies()

        response = self.client.delete(f"/backups/{found['id']}")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.copies(), [])
        self.assertFalse((self.library.parent / "config" / "backups" / "media" / GAME).exists())

    def test_a_copy_of_a_game_that_is_gone_says_so(self) -> None:
        import shutil
        shutil.rmtree(self.folder)

        (found,) = self.copies()

        self.assertTrue(found["gone"])
        self.assertEqual(found["subject_name"], GAME)

    def test_a_game_id_that_climbs_out_lists_nothing(self) -> None:
        listed = self.client.get("/backups?subject_kind=game&subject_id=../..").json()

        self.assertEqual(listed["copies"], [])


class TurningVideosTests(RotateCase):
    @unittest.skipUnless(_ffmpeg(), "FFmpeg is not installed here")
    def test_a_video_is_turned_and_its_original_kept(self) -> None:
        _video(self.folder / "medias" / "clip.mp4")

        result = self.run_job([(GAME, "medias/clip.mp4")], media_rotate.LEFT, keep=True)

        self.assertEqual(result["turned"], 1, result["skipped"])
        self.assertEqual(_video_size(self.folder / "medias" / "clip.mp4"), (32, 64))
        self.assertEqual([one["file"] for one in self.copies()], ["medias/clip.mp4"])

    @unittest.skipUnless(_ffmpeg(), "FFmpeg is not installed here")
    def test_a_file_that_is_not_a_video_is_skipped_with_what_ffmpeg_said(self) -> None:
        (self.folder / "medias" / "clip.mp4").write_bytes(b"not a video")

        result = self.run_job([(GAME, "medias/clip.mp4")], media_rotate.LEFT, keep=True)

        self.assertEqual(result["turned"], 0)
        self.assertEqual((self.folder / "medias" / "clip.mp4").read_bytes(), b"not a video")
        self.assertEqual(self.copies(), [])

    def test_without_ffmpeg_the_videos_are_skipped_and_the_pictures_are_turned(self) -> None:
        (self.folder / "medias" / "clip.mp4").write_bytes(b"video")
        missing = tools.Found(tools.FFMPEG, tools.State.MISSING)

        with patch.object(tools, "resolve", return_value=missing) as resolved:
            result = self.run_job([(GAME, "medias/clip.mp4"), (GAME, "medias/wheel.png")],
                                  media_rotate.LEFT, keep=False)

        self.assertEqual(result["turned"], 1)
        self.assertEqual([one["path"] for one in result["skipped"]], ["medias/clip.mp4"])
        self.assertEqual((self.folder / "medias" / "clip.mp4").read_bytes(), b"video")
        resolved.assert_called_once()


if __name__ == "__main__":
    unittest.main()
