from __future__ import annotations

import configparser
import io
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import Any
from unittest import mock

from common.host import libdmdutil_service, peripherals


class LibDmdUtilServiceTests(unittest.TestCase):
    def setUp(self):
        self.config = configparser.ConfigParser()
        self.config["real_dmd"] = {"enabled": "true"}
        self.iniconfig = mock.Mock(config=self.config)
        self.addCleanup(self._reset_service_state)
        self._reset_service_state()

    def _reset_service_state(self):
        with libdmdutil_service._LOCK:
            libdmdutil_service._CONTROLLER = None
            libdmdutil_service._CURRENT_IMAGE = None
        libdmdutil_service._stop_player()
        libdmdutil_service._REPORTED.clear()

    def test_show_image_uses_default_realdmd_image_when_image_is_missing(self):
        controller = mock.Mock()
        with libdmdutil_service._LOCK:
            libdmdutil_service._CONTROLLER = controller

        self.assertTrue(
            libdmdutil_service.show_image(self.iniconfig, "/tmp/does-not-exist-realdmd.png"))

        fallback = str(libdmdutil_service._DEFAULT_REALDMD_IMAGE)
        controller.hold_image.assert_called_once_with(fallback)
        controller.clear.assert_not_called()
        self.assertEqual(libdmdutil_service._CURRENT_IMAGE, fallback)

    def test_show_image_uses_default_realdmd_image_when_image_is_none(self):
        controller = mock.Mock()
        with libdmdutil_service._LOCK:
            libdmdutil_service._CONTROLLER = controller

        self.assertTrue(libdmdutil_service.show_image(self.iniconfig, None))

        controller.hold_image.assert_called_once_with(str(libdmdutil_service._DEFAULT_REALDMD_IMAGE))
        controller.clear.assert_not_called()

    def test_show_image_clears_when_requested_and_default_images_are_missing(self):
        controller = mock.Mock()
        with libdmdutil_service._LOCK:
            libdmdutil_service._CONTROLLER = controller

        with TemporaryDirectory() as temp_dir:
            missing_default = Path(temp_dir) / "missing-default.png"
            with mock.patch.object(libdmdutil_service, "_DEFAULT_REALDMD_IMAGE", missing_default):
                self.assertFalse(libdmdutil_service.show_image(self.iniconfig, None))

        controller.clear.assert_called_once_with()
        controller.hold_image.assert_not_called()
        self.assertIsNone(libdmdutil_service._CURRENT_IMAGE)


FRAME = 24
ONE, TWO = b"\x01" * FRAME, b"\x02" * FRAME


class _Controller:
    """Records what reaches the panel, in the order it arrives."""

    def __init__(self, enough: int = 0) -> None:
        self.calls: list[tuple[str, object]] = []
        self.enough = enough
        self.reached = threading.Event()
        self.stopped = False

    def info(self) -> SimpleNamespace:
        return SimpleNamespace(width=4, height=2)

    def send_rgb_frame(self, frame: bytes) -> bool:
        self.calls.append(("frame", frame))
        if self.enough and len(self.frames) >= self.enough:
            self.reached.set()
        return True

    def hold_image(self, path: str) -> None:
        self.calls.append(("image", path))

    def clear(self) -> None:
        self.calls.append(("clear", None))

    def stop(self, *, clear: bool = False) -> None:
        self.stopped = True

    def unload(self) -> None:
        pass

    @property
    def frames(self) -> list[bytes]:
        return [one for kind, one in self.calls if kind == "frame"]  # type: ignore[misc]


class _Process:
    def __init__(self, frames: list[bytes], error: bytes = b"") -> None:
        self.stdout: Any = io.BytesIO(b"".join(frames))
        self.stderr: Any = io.BytesIO(error)
        self.returncode: int | None = None

    def poll(self) -> int | None:
        return self.returncode

    def kill(self) -> None:
        self.returncode = -9

    def wait(self, timeout: float | None = None) -> int | None:
        return self.returncode


class _Counted(io.BytesIO):
    got = 0

    def read(self, size: int | None = -1) -> bytes:
        data = super().read(size)
        self.got += len(data)
        return data


class _Stalled(_Process):
    """A file that never gives a first frame, until the process is killed."""

    def __init__(self) -> None:
        super().__init__([])
        self._killed = threading.Event()
        self.stdout = self

    def read(self, _size: int = -1) -> bytes:
        self._killed.wait(10)
        return b""

    def close(self) -> None:
        pass

    def kill(self) -> None:
        super().kill()
        self._killed.set()


class VideoTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = configparser.ConfigParser()
        self.config["real_dmd"] = {"enabled": "true"}
        self.iniconfig = mock.Mock(config=self.config)
        self.spawned: list[list[str]] = []
        self.options: list[dict] = []
        self.addCleanup(self._reset)
        self._reset()
        found = mock.patch.object(libdmdutil_service, "ffmpeg_path",
                                  return_value=Path("/usr/bin/ffmpeg"))
        found.start()
        self.addCleanup(found.stop)

    def _reset(self) -> None:
        libdmdutil_service._stop_player()
        with libdmdutil_service._LOCK:
            libdmdutil_service._CONTROLLER = None
            libdmdutil_service._CURRENT_IMAGE = None
        libdmdutil_service._REPORTED.clear()

    def _serve(self, controller: _Controller, *processes: _Process) -> None:
        """The panel is connected, and each ffmpeg started is the next of `processes`. Once
        they run out, each one plays two frames."""
        with libdmdutil_service._LOCK:
            libdmdutil_service._CONTROLLER = controller
        queue = list(processes)

        def spawn(argv, **kwargs):
            self.spawned.append(list(argv))
            self.options.append(kwargs)
            return queue.pop(0) if queue else _Process([ONE, TWO])

        patched = mock.patch.object(libdmdutil_service.subprocess, "Popen", side_effect=spawn)
        patched.start()
        self.addCleanup(patched.stop)

    def test_a_video_plays_and_starts_again_when_it_ends(self) -> None:
        controller = _Controller(enough=5)
        self._serve(controller)

        self.assertTrue(libdmdutil_service.show_video(self.iniconfig, "/art/dmd.mp4", None))
        self.assertTrue(controller.reached.wait(5))
        libdmdutil_service._stop_player()

        self.assertEqual(controller.frames[:5], [ONE, TWO, ONE, TWO, ONE])
        self.assertGreaterEqual(len(self.spawned), 2)

    def test_it_is_decoded_to_the_panels_size(self) -> None:
        controller = _Controller(enough=1)
        self._serve(controller)

        libdmdutil_service.show_video(self.iniconfig, "/art/dmd.mp4", None)
        controller.reached.wait(5)
        libdmdutil_service._stop_player()

        argv = self.spawned[0]
        self.assertEqual(argv[0], str(Path("/usr/bin/ffmpeg")))
        self.assertIn("scale=4:2", argv)
        self.assertEqual(argv[argv.index("-pix_fmt") + 1], "rgb24")
        self.assertIn(str(Path("/art/dmd.mp4")), argv)

    def test_the_same_video_is_not_started_twice(self) -> None:
        controller = _Controller(enough=1)
        self._serve(controller)

        libdmdutil_service.show_video(self.iniconfig, "/art/dmd.mp4", None)
        playing = libdmdutil_service._PLAYER
        again = libdmdutil_service.show_video(self.iniconfig, "/art/dmd.mp4", None)
        kept = libdmdutil_service._PLAYER
        libdmdutil_service._stop_player()

        self.assertTrue(again)
        self.assertIsNotNone(playing)
        self.assertIs(kept, playing)

    def test_a_new_selection_stops_it_before_anything_is_sent(self) -> None:
        controller = _Controller(enough=3)
        self._serve(controller)
        libdmdutil_service.show_video(self.iniconfig, "/art/dmd.mp4", None)
        self.assertTrue(controller.reached.wait(5))

        libdmdutil_service.show_image(self.iniconfig, "/art/next.png")

        kinds = [kind for kind, _ in controller.calls]
        self.assertEqual(kinds.count("image"), 1)
        self.assertNotIn("frame", kinds[kinds.index("image"):],
                         "no frame of the old video after the next picture")

    def test_another_video_replaces_it(self) -> None:
        controller = _Controller(enough=3)
        self._serve(controller)
        libdmdutil_service.show_video(self.iniconfig, "/art/one.mp4", None)
        self.assertTrue(controller.reached.wait(5))

        libdmdutil_service.show_video(self.iniconfig, "/art/two.mp4", None)
        libdmdutil_service._stop_player()

        self.assertTrue(any(str(Path("/art/two.mp4")) in argv for argv in self.spawned))

    def test_a_launch_stops_it_before_the_panel_is_released(self) -> None:
        controller = _Controller(enough=3)
        self._serve(controller)
        libdmdutil_service.show_video(self.iniconfig, "/art/dmd.mp4", None)
        self.assertTrue(controller.reached.wait(5))

        with mock.patch.object(peripherals, "stop_dof_service"):
            peripherals.release_for_launch()

        sent = len(controller.frames)
        self.assertTrue(controller.stopped)
        threading.Event().wait(0.1)
        self.assertEqual(len(controller.frames), sent)
        self.assertIsNone(libdmdutil_service._PLAYER)

    def test_without_ffmpeg_the_picture_is_sent_and_the_log_says_why_once(self) -> None:
        controller = _Controller()
        self._serve(controller)
        with mock.patch.object(libdmdutil_service, "ffmpeg_path", return_value=None), \
                self.assertLogs(libdmdutil_service.logger, "WARNING") as logged:
            libdmdutil_service.show_video(self.iniconfig, "/art/dmd.mp4", "/art/dmd.png")
            libdmdutil_service.show_video(self.iniconfig, "/art/dmd.mp4", "/art/dmd.png")

        self.assertEqual(self.spawned, [])
        self.assertEqual(controller.frames, [])
        self.assertEqual(len(logged.records), 1)
        self.assertIn("FFmpeg was not found", logged.output[0])

    def test_a_file_ffmpeg_cannot_read_shows_the_picture(self) -> None:
        controller = _Controller()
        self._serve(controller, _Process([], error=b"noise\ndmd.mp4: Invalid data found\n"),
                    _Process([]))
        with self.assertLogs(libdmdutil_service.logger, "WARNING") as logged:
            libdmdutil_service.show_video(self.iniconfig, "/art/dmd.mp4", "/art/dmd.png")

        self.assertEqual([kind for kind, _ in controller.calls], ["image"])
        self.assertIn("Invalid data found", logged.output[0])
        self.assertIsNone(libdmdutil_service._PLAYER)

    def test_a_video_is_not_played_while_the_panel_is_off(self) -> None:
        self.config["real_dmd"]["enabled"] = "false"
        controller = _Controller()
        self._serve(controller)

        self.assertFalse(libdmdutil_service.show_video(self.iniconfig, "/art/dmd.mp4", None))
        self.assertEqual(self.spawned, [])

    def test_a_launch_stops_a_video_still_waiting_for_its_first_frame(self) -> None:
        controller = _Controller()
        self._serve(controller, _Stalled())
        opening = threading.Thread(target=libdmdutil_service.show_video,
                                   args=(self.iniconfig, "/art/slow.mp4", "/art/slow.png"))
        opening.start()
        while not self.spawned:
            threading.Event().wait(0.01)

        with mock.patch.object(peripherals, "stop_dof_service"):
            peripherals.release_for_launch()
        opening.join(5)

        self.assertFalse(opening.is_alive())
        self.assertEqual(controller.calls, [])
        self.assertTrue(controller.stopped)
        self.assertIsNone(libdmdutil_service._PLAYER)

    def test_a_video_that_has_ended_is_started_again_when_asked_for(self) -> None:
        controller = _Controller()
        self._serve(controller)
        with mock.patch.object(libdmdutil_service.subprocess, "Popen",
                               side_effect=[_Process([ONE]), OSError("gone"),
                                            _Process([ONE, TWO]), _Process([ONE, TWO])]) as popen:
            libdmdutil_service.show_video(self.iniconfig, "/art/dmd.mp4", None)
            first = libdmdutil_service._PLAYER
            assert first is not None and first._thread is not None
            first._thread.join(5)
            self.assertFalse(first.playing)

            libdmdutil_service.show_video(self.iniconfig, "/art/dmd.mp4", None)
            libdmdutil_service._stop_player()

        self.assertIsNot(libdmdutil_service._PLAYER, first)
        self.assertGreaterEqual(popen.call_count, 3)

    def test_ffmpeg_is_given_the_environment_a_child_of_vpinfe_gets(self) -> None:
        controller = _Controller(enough=1)
        self._serve(controller)

        libdmdutil_service.show_video(self.iniconfig, "/art/dmd.mp4", None)
        controller.reached.wait(5)
        libdmdutil_service._stop_player()

        self.assertIn("env", self.options[0])

    def test_what_ffmpeg_says_while_it_plays_is_read(self) -> None:
        controller = _Controller()
        noisy = _Process([ONE, TWO])
        noisy.stderr = _Counted(b"x" * 1000)
        self._serve(controller, noisy, _Process([ONE, TWO]))

        libdmdutil_service.show_video(self.iniconfig, "/art/dmd.mp4", None)
        for _ in range(500):
            if noisy.stderr.got == 1000:
                break
            threading.Event().wait(0.01)
        libdmdutil_service._stop_player()

        self.assertEqual(noisy.stderr.got, 1000)



if __name__ == "__main__":
    unittest.main()
