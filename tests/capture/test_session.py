"""One table recorded."""

from __future__ import annotations

import configparser
import signal
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from apps.vpx.capture import VPXCapture
from common import events, service_errors
from common.capture import geometry, placing, session, settings
from common.capture.adapters import Window, wlr
from common.capture.settings import Settings
from common.games import asset_origin
from common.host import launch, launch_state
from tests.capture.test_adapters import SWAY_OUTPUTS
from tests.capture.test_preflight import found

OUTPUTS = {output.name: output for output in wlr.sway_outputs(SWAY_OUTPUTS)}
SCREENS = {"playfield": OUTPUTS["DP-1"], "backglass": OUTPUTS["DP-2"],
           "scoreview": OUTPUTS["HDMI-A-1"]}
VIDEOS = ("playfield_video", "backglass_video", "scoreview_video")

# Short enough to run in a test; the session reads them as seconds.
QUICK = Settings(length=0.3, wait=0, picture_at=0.1, fps=30, size="1920",  # type: ignore[arg-type]
                 video_codec="auto", playfield_orientation="bottom_right",
                 quality="standard", sound=False, sound_source="auto")


class Cabinet:
    """A launch that is up at once and runs until stopped, recorders that write their file
    when told to stop, and an FFmpeg that writes whatever it is asked to."""

    def __init__(self, *, closes_itself: float = 0.0, refuses: str = "",
                 frames: dict[str, list[int]] | None = None, peak: float = -20.0,
                 one_color: dict[str, tuple[bool, bool]] | None = None) -> None:
        """`one_color`, by recording or still, is whether its key frames and then two
        frames a second of the whole of it are one color."""
        self.closes_itself = closes_itself
        self.refuses = refuses
        self.frames = frames or {}
        self.peak = peak
        self.one_color = one_color or {}
        self.stopped = threading.Event()
        self.running: list[Any] = []
        self.log: list[tuple[str, list[str], bool, int]] = []
        self.launched_with: dict[str, Any] = {}
        self.placed: list[tuple] = []
        self.proposed: list[tuple] = []

    def launch(self, game: Any, config: Any, **kwargs: Any) -> None:
        self.launched_with = kwargs
        if self.refuses:
            raise launch.LaunchUnavailableError(self.refuses)
        source = kwargs["source"]
        events.emit(events.TABLE_LAUNCHED, game=game, table_id="", source=source, up=[])
        self.stopped.wait(self.closes_itself or 30)
        events.emit(events.TABLE_EXITED, game=game, table_id="", source=source)

    def stop(self) -> bool:
        self.stopped.set()
        return True

    def popen(self, argv: list[str], **_: Any) -> Any:
        cabinet = self

        class Recorder:
            returncode: int | None
            signals: list[int]

            def __init__(self) -> None:
                self.argv, self.returncode, self.signals = argv, None, []

            def poll(self) -> int | None:
                return self.returncode

            def send_signal(self, sig: int) -> None:
                self.signals.append(sig)
                Path(argv[-1]).write_bytes(b"recorded")
                self.returncode = 0
                cabinet.running.remove(self)

            def wait(self, timeout: float | None = None) -> int | None:
                return self.returncode

            def kill(self) -> None:
                self.returncode = -9

        recorder = Recorder()
        self.running.append(recorder)
        self.log.append(("spawn", argv, self.stopped.is_set(), len(self.running)))
        return recorder

    def runner(self, argv: list[str], **_: Any) -> Any:
        self.log.append(("run", argv, self.stopped.is_set(), len(self.running)))
        if "-progress" in argv:
            source = Path(argv[argv.index("-i") + 1]).stem
            counts = self.frames.get(source)
            count = counts.pop(0) if counts else 1000
            return SimpleNamespace(stdout=f"frame={count}\nprogress=end\n", stderr="")
        if "volumedetect" in argv:
            return SimpleNamespace(stdout="", stderr=f"max_volume: {self.peak} dB")
        if _levels(argv):
            keys, whole = self.one_color.get(Path(argv[argv.index("-i") + 1]).stem,
                                             (False, False))
            top = 16 if (keys if "-skip_frame" in argv else whole) else 235
            said = "".join(f"lavfi.signalstats.{plane}MIN=16\nlavfi.signalstats.{plane}MAX={top}\n"
                           for plane in "YUV")
            return SimpleNamespace(stdout="", stderr=f"frame:0 pts:0\n{said}")
        Path(argv[-1]).write_bytes(b"made")
        return SimpleNamespace(stdout="", stderr="", returncode=0)

    def place(self, *args: Any) -> dict[str, str]:
        self.placed.append(args)
        return {"written": f"{args[1]}{Path(args[3]).suffix}"}

    def propose(self, *args: Any) -> dict[str, str]:
        self.proposed.append(args)
        return {"id": f"p{len(self.proposed)}"}

    def kit(self) -> session.Kit:
        return session.Kit(launch=self.launch, stop=self.stop, popen=self.popen,
                           runner=self.runner, place=self.place, propose=self.propose)

    def encodes(self) -> list[tuple[str, list[str], bool, int]]:
        """Every FFmpeg run that makes a file, as against counting frames or loudness."""
        return [one for one in self.log if one[0] == "run" and "-progress" not in one[1]
                and "volumedetect" not in one[1] and not one[1][0].endswith("grim")
                and not _levels(one[1])]


def _levels(argv: list[str]) -> bool:
    return any("signalstats" in one for one in argv)


def _config(rotation: str = "0") -> configparser.ConfigParser:
    held = configparser.ConfigParser()
    held["windows.playfield"] = {"rotation": rotation}
    return held


class _Sessions(unittest.TestCase):
    def setUp(self) -> None:
        held = tempfile.TemporaryDirectory()
        self.addCleanup(held.cleanup)
        self.work = Path(held.name) / "recording"
        launch_state.clear()
        self.addCleanup(launch_state.clear)

    def record(self, cabinet: Cabinet, kinds: tuple[str, ...], *, at_once: bool = True,
               chosen: Settings = QUICK, rotation: str = "0",
               shown: placing.Shown | None = None,
               desktop: list[Window] | None = None,
               propose: frozenset[str] = frozenset(),
               replace: frozenset[str] = frozenset(),
               halt: threading.Event | None = None, view: str = "") -> session.Result:
        """With `shown`, where the app's settings put its windows; with `desktop`, what the
        desktop says once the table is up."""
        wlr.reset_for_tests()
        adapter = wlr.WlrAdapter({})
        adapter.hardware = lambda ffmpeg: "/dev/dri/renderD128" if at_once else ""  # type: ignore[method-assign]
        if desktop is not None:
            adapter.windows = lambda: desktop  # type: ignore[method-assign]
        target = session.Target("game1", SimpleNamespace(full_path_game=""), "", None, kinds,
                                propose=propose, replace=replace, view=view)
        placed = None if shown is None else placing.Placing(
            list(OUTPUTS.values()), _config(rotation), [], shown)
        screens = SCREENS if placed is None else {
            window: screen.output for window, screen in placed.screens().items()
            if screen.output}
        return session.Session(target, chosen, adapter=adapter, screens=screens,
                               found=found(), at_once=at_once, codec=settings.H264,
                               work=self.work, config=_config(rotation),
                               kit=cabinet.kit(), placed=placed, halt=halt).run()

    def spawned(self, cabinet: Cabinet) -> list[str]:
        """The output each recorder was started on."""
        return [argv[argv.index("-o") + 1] for kind, argv, *_ in cabinet.log
                if kind == "spawn" and "-o" in argv]


class SessionTests(_Sessions):
    def test_every_screen_records_at_once_and_lands_as_recorded(self) -> None:
        cabinet = Cabinet()

        result = self.record(cabinet, (*VIDEOS, "playfield"))

        self.assertEqual(result.state, session.RECORDED)
        self.assertEqual({one["kind"] for one in result.placed},
                         {*VIDEOS, "playfield"})
        spawns = [one for one in cabinet.log if one[0] == "spawn"]
        self.assertEqual([one[3] for one in spawns], [1, 2, 3])
        for placed in cabinet.placed:
            self.assertEqual((placed[0], placed[2], placed[4], placed[5]),
                             ("game1", "", asset_origin.RECORDED, ""))
        self.assertFalse(self.work.exists())

    def test_nothing_is_encoded_until_the_table_has_closed(self) -> None:
        cabinet = Cabinet()

        self.record(cabinet, (*VIDEOS, "playfield", "audio"))

        self.assertTrue(cabinet.encodes())
        for _, argv, table_closed, recorders in cabinet.encodes():
            with self.subTest(argv[-1]):
                self.assertTrue(table_closed)
                self.assertEqual(recorders, 0)
        for kind, argv, _, recorders in cabinet.log:
            if kind == "run" and "-progress" in argv:
                self.assertEqual(recorders, 0, "frames are counted with nothing recording")

    def test_every_recorder_is_stopped_as_ctrl_c_would(self) -> None:
        cabinet = Cabinet()
        seen: list[Any] = []
        popen = cabinet.popen

        def spy(argv: list[str], **kw: Any) -> Any:
            seen.append(popen(argv, **kw))
            return seen[-1]

        cabinet.popen = spy  # type: ignore[method-assign]

        self.record(cabinet, VIDEOS)

        self.assertEqual([one.signals for one in seen], [[signal.SIGINT]] * 3)

    def test_the_recorder_copies_every_refresh_in_hardware(self) -> None:
        cabinet = Cabinet()

        self.record(cabinet, ("playfield_video",))

        argv = next(one[1] for one in cabinet.log if one[0] == "spawn")
        self.assertEqual(argv[1:], ["-o", "DP-1", "-c", "h264_vaapi", "-d",
                                    "/dev/dri/renderD128", "-p", "qp=18", "-f",
                                    str(self.work / "playfield.mkv")])

    def test_without_hardware_screens_record_one_after_another(self) -> None:
        cabinet = Cabinet()

        result = self.record(cabinet, VIDEOS, at_once=False)

        spawns = [one for one in cabinet.log if one[0] == "spawn"]
        self.assertEqual([one[3] for one in spawns], [1, 1, 1])
        self.assertIn("libx264", spawns[0][1])
        self.assertEqual(len(result.placed), 3)

    def test_a_screen_that_did_not_keep_up_records_again_one_at_a_time(self) -> None:
        cabinet = Cabinet(frames={"backglass": [3]})

        result = self.record(cabinet, VIDEOS)

        spawns = [one[3] for one in cabinet.log if one[0] == "spawn"]
        self.assertEqual(spawns, [1, 2, 3, 1, 1, 1])
        self.assertFalse(result.at_once)
        self.assertEqual(len(result.placed), 3)

    def test_a_screen_that_gave_nothing_fails_with_why(self) -> None:
        cabinet = Cabinet(frames={"scoreview": [0, 0]})

        result = self.record(cabinet, VIDEOS, at_once=False)

        self.assertEqual(result.failed, [{"kind": "scoreview_video", "reason": {
            "key": session.NO_FRAMES, "params": {"window": "scoreview"}}}])
        self.assertEqual(len(result.placed), 2)

    def test_the_sound_is_recorded_beside_the_screens_and_vpx_is_told(self) -> None:
        cabinet = Cabinet()

        result = self.record(cabinet, ("playfield_video", "audio"), at_once=False)

        self.assertTrue(cabinet.launched_with["record_sound"])
        spawned = [one[1] for one in cabinet.log if one[0] == "spawn"]
        self.assertIn("pulse", spawned[1])
        self.assertIn({"kind": "audio"}, result.placed)

    def test_vpx_is_muted_when_sound_is_not_recorded(self) -> None:
        cabinet = Cabinet()

        self.record(cabinet, ("playfield_video",))

        self.assertFalse(cabinet.launched_with["record_sound"])
        self.assertEqual(cabinet.launched_with["source"], launch_state.SOURCE_CAPTURE)

    def test_silence_is_thrown_away_and_said(self) -> None:
        cabinet = Cabinet(peak=-84.0)

        result = self.record(cabinet, ("playfield_video", "audio"))

        self.assertIn({"kind": "audio", "reason": {"key": session.SILENT, "params": {}}},
                      result.failed)
        self.assertNotIn({"kind": "audio"}, result.placed)

    def test_a_screen_that_showed_one_color_is_not_placed(self) -> None:
        cabinet = Cabinet(one_color={"scoreview": (True, True)})

        result = self.record(cabinet, (*VIDEOS, "scoreview"))

        self.assertEqual(result.failed, [
            {"kind": kind, "reason": {"key": session.ONE_COLOR,
                                      "params": {"window": "scoreview"}}}
            for kind in ("scoreview", "scoreview_video")])
        self.assertEqual({one["kind"] for one in result.placed},
                         {"playfield_video", "backglass_video"})
        self.assertFalse([argv for _, argv, *_ in cabinet.encodes()
                          if "scoreview.mkv" in " ".join(argv)])

    def test_key_frames_of_one_color_are_read_again_before_it_is_refused(self) -> None:
        cabinet = Cabinet(one_color={"scoreview": (True, False)})

        result = self.record(cabinet, VIDEOS)

        self.assertEqual((result.failed, len(result.placed)), ([], 3))
        read = [argv for _, argv, *_ in cabinet.log
                if _levels(argv) and "scoreview.mkv" in " ".join(argv)]
        self.assertEqual(["-skip_frame" in argv for argv in read], [True, False])

    def test_a_still_of_one_color_is_not_placed(self) -> None:
        cabinet = Cabinet(one_color={"backglass": (True, False)})

        result = self.record(cabinet, ("playfield", "backglass"))

        self.assertEqual(result.failed, [{"kind": "backglass", "reason": {
            "key": session.ONE_COLOR, "params": {"window": "backglass"}}}])
        self.assertEqual(result.placed, [{"kind": "playfield"}])

    def test_pictures_alone_are_stills_and_nothing_is_recorded(self) -> None:
        cabinet = Cabinet()

        result = self.record(cabinet, ("playfield", "backglass"))

        self.assertFalse([one for one in cabinet.log if one[0] == "spawn"])
        grims = [one[1] for one in cabinet.log if one[1][0].endswith("grim")]
        self.assertEqual([argv[1:3] for argv in grims], [["-o", "DP-1"], ["-o", "DP-2"]])
        self.assertEqual({one["kind"] for one in result.placed}, {"playfield", "backglass"})

    def test_a_launch_in_full_single_screen_takes_one_still_of_the_playfield_screen(self) -> None:
        cabinet = Cabinet()

        result = self.record(cabinet, (session.FSS,), view="fss")

        self.assertEqual(cabinet.launched_with["record_view"], "fss")
        grims = [one[1] for one in cabinet.log if one[1][0].endswith("grim")]
        self.assertEqual([argv[1:3] for argv in grims], [["-o", "DP-1"]])
        self.assertEqual(result.placed, [{"kind": session.FSS}])
        self.assertEqual(result.state, session.RECORDED)

    def test_a_launch_of_the_tables_own_views_asks_for_no_view(self) -> None:
        cabinet = Cabinet()

        self.record(cabinet, ("playfield",))

        self.assertEqual(cabinet.launched_with["record_view"], "")

    def test_the_playfield_is_turned_from_its_buffer_to_the_stored_orientation(self) -> None:
        for rotation, orientation in (("0", "bottom_right"), ("0", "upright"),
                                      ("90", "bottom_left")):
            with self.subTest(rotation=rotation, orientation=orientation):
                cabinet = Cabinet()
                chosen = Settings(**{**QUICK.__dict__, "playfield_orientation": orientation})

                self.record(cabinet, ("playfield_video",), chosen=chosen, rotation=rotation)

                argv = cabinet.encodes()[0][1]
                expected = geometry.playfield(OUTPUTS["DP-1"].transform, int(rotation),
                                              orientation)
                vf = argv[argv.index("-vf") + 1]
                self.assertTrue(vf.startswith(",".join([*expected.filters, "fps=30"])))

    def test_a_proposed_kind_is_kept_for_a_person_and_nothing_is_placed(self) -> None:
        cabinet = Cabinet()

        result = self.record(cabinet, ("playfield_video", "backglass_video"),
                             propose=frozenset({"playfield_video"}))

        self.assertEqual(result.state, session.RECORDED)
        self.assertEqual(result.proposed, [{"kind": "playfield_video", "id": "p1"}])
        self.assertEqual([one[1] for one in cabinet.placed], ["backglass_video"])
        self.assertEqual(cabinet.proposed[0][:3], ("game1", "", "playfield_video"))

    def test_a_replaced_kind_keeps_then_removes_the_file_that_served_its_slot(self) -> None:
        cabinet = Cabinet()
        held = {"path": "medias/table.mp4", "table": ""}
        with patch("common.capture.slots.serving", return_value=held) as serving, \
                patch("common.capture.slots.can_keep") as can_keep, \
                patch("common.capture.slots.remove", return_value=[]) as remove:
            self.record(cabinet, ("playfield_video", "backglass_video"),
                        replace=frozenset({"playfield_video"}))

        self.assertEqual(serving.call_args.args, ("game1", "", "playfield_video"))
        self.assertEqual([call.args for call in can_keep.call_args_list],
                         [("game1", held, ""), ("game1", None, "")])
        self.assertEqual([call.args for call in remove.call_args_list],
                         [("game1", held, "", "playfield_video.mp4"),
                          ("game1", None, "", "backglass_video.mp4")])

    def test_a_backups_folder_that_cannot_be_written_to_places_nothing(self) -> None:
        cabinet = Cabinet()
        held = {"path": "medias/table.mp4", "table": ""}
        refusal = service_errors.BlockedError("the backups folder cannot be written to")
        with patch("common.capture.slots.serving", return_value=held), \
                patch("common.capture.slots.can_keep", side_effect=refusal), \
                patch("common.capture.slots.remove") as remove:
            result = self.record(cabinet, ("playfield_video",),
                                 replace=frozenset({"playfield_video"}))

        self.assertEqual((cabinet.placed, result.placed), ([], []))
        remove.assert_not_called()
        self.assertEqual(result.failed[0]["kind"], "playfield_video")
        self.assertEqual(result.failed[0]["reason"]["key"], session.NOT_WRITTEN)

    def test_with_a_persons_encode_command_the_picture_is_cut_from_what_it_wrote(
            self) -> None:
        cabinet = Cabinet()
        chosen = replace(QUICK, encode_command="{ffmpeg} {input} -vf hflip {output}")

        self.record(cabinet, ("playfield", "playfield_video"), chosen=chosen)

        encode, picture = [one[1] for one in cabinet.encodes()]
        self.assertEqual(encode[1:3], ["-ss", "0.000"])
        self.assertEqual(encode[-3:], ["-vf", "hflip", str(self.work / "playfield_video.mp4")])
        self.assertEqual(picture[picture.index("-i") + 1],
                         str(self.work / "playfield_video.mp4"))
        self.assertNotIn("transpose", " ".join(picture))

    def test_a_table_closed_at_the_cabinet_places_nothing(self) -> None:
        cabinet = Cabinet(closes_itself=0.05)
        chosen = Settings(**{**QUICK.__dict__, "wait": 5})

        result = self.record(cabinet, VIDEOS, chosen=chosen)

        self.assertEqual((result.state, result.reason),
                         (session.CLOSED, {"key": session.CLOSED_AT_CABINET, "params": {}}))
        self.assertEqual(cabinet.placed, [])
        self.assertFalse(cabinet.encodes())

    def test_a_table_the_run_closed_is_stopped_and_keeps_nothing(self) -> None:
        cabinet = Cabinet()
        halt = threading.Event()

        def run_stops() -> None:
            halt.set()
            cabinet.stop()

        threading.Timer(0.05, run_stops).start()
        result = self.record(cabinet, VIDEOS, chosen=Settings(**{**QUICK.__dict__, "wait": 5}),
                             halt=halt)

        self.assertEqual((result.state, result.placed, result.failed),
                         (session.STOPPED, [], []))
        self.assertFalse(cabinet.encodes())

    def test_a_halt_while_the_table_starts_records_nothing(self) -> None:
        cabinet = Cabinet()
        halt = threading.Event()
        halt.set()
        cabinet.stopped.set()

        result = self.record(cabinet, VIDEOS, halt=halt)

        self.assertEqual(result.state, session.STOPPED)
        self.assertFalse([one for one in cabinet.log if one[0] == "spawn"])

    def test_a_table_that_would_not_start_says_why(self) -> None:
        cabinet = Cabinet(refuses="No launcher plays this table")

        result = self.record(cabinet, VIDEOS)

        self.assertEqual(result.state, session.FAILED)
        assert result.reason is not None
        self.assertEqual(result.reason["key"], session.WOULD_NOT_START)
        self.assertEqual(result.reason["detail"], "No launcher plays this table")
        self.assertFalse([one for one in cabinet.log if one[0] == "spawn"])

    def test_nothing_asked_for_launches_nothing(self) -> None:
        cabinet = Cabinet()

        result = self.record(cabinet, ())

        self.assertEqual(result.state, session.SKIPPED)
        self.assertEqual(cabinet.launched_with, {})


# What VPX's settings name, and where its windows were once it was up: nothing on the
# output its settings name for the DMD.
FROM_VPX = placing.Shown("Visual Pinball X",
                         {"playfield": "DP-1", "backglass": "DP-2", "scoreview": "HDMI-A-1",
                          "topper": ""}, VPXCapture().window)
UP = [Window("chromium", "VPinFE Table", "DP-1"),
      Window("VPinballX_BGFX", "Visual Pinball Player", "DP-1"),
      Window("VPinballX_BGFX", "Visual Pinball Backglass", "DP-2")]


class DesktopTests(_Sessions):
    """Once the table is up, the desktop says where the app's windows are."""

    def test_a_screen_holding_none_of_the_apps_windows_is_not_recorded(self) -> None:
        cabinet = Cabinet()

        result = self.record(cabinet, VIDEOS, shown=FROM_VPX, desktop=UP)

        self.assertEqual(self.spawned(cabinet), ["DP-1", "DP-2"])
        self.assertEqual(result.failed, [{"kind": "scoreview_video", "reason": {
            "key": placing.NOT_SHOWN,
            "params": {"window": "scoreview", "app": "Visual Pinball X"}}}])
        self.assertEqual({one["kind"] for one in result.placed},
                         {"playfield_video", "backglass_video"})

    def test_a_window_the_desktop_shows_elsewhere_is_recorded_there(self) -> None:
        cabinet = Cabinet()
        moved = [*UP[:2], Window("VPinballX_BGFX", "Visual Pinball Backglass", "HDMI-A-1")]

        with self.assertLogs("vpinfe.common.capture.session", "WARNING") as logged:
            self.record(cabinet, ("playfield_video", "backglass_video"), shown=FROM_VPX,
                        desktop=moved)

        self.assertEqual(self.spawned(cabinet), ["DP-1", "HDMI-A-1"])
        self.assertIn("the backglass is on HDMI-A-1, not DP-2", logged.output[0])

    def test_a_desktop_that_shows_no_playfield_window_leaves_the_plan(self) -> None:
        for desktop in ([UP[2]], []):
            with self.subTest(desktop=desktop):
                cabinet = Cabinet()

                result = self.record(cabinet, VIDEOS, shown=FROM_VPX, desktop=desktop)

                self.assertEqual(self.spawned(cabinet), ["DP-1", "DP-2", "HDMI-A-1"])
                self.assertEqual(result.failed, [])

    def test_a_window_the_app_shows_nowhere_fails_before_the_launch(self) -> None:
        cabinet = Cabinet()

        result = self.record(cabinet, ("topper_video",), shown=FROM_VPX, desktop=UP)

        self.assertEqual(result.state, session.FAILED)
        self.assertEqual(result.failed[0]["reason"]["key"], placing.NOT_SHOWN)
        self.assertEqual(cabinet.launched_with, {})


if __name__ == "__main__":
    unittest.main()
