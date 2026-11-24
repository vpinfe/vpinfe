"""Test: a few seconds of the playfield through the commands, and what came out."""

from __future__ import annotations

import configparser
import shlex
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from starlette.testclient import TestClient

import httpapi
from common import service_errors
from common.capture import adapters, placing, run, session, trial
from common.capture.adapters import wlr
from common.host import launch_state
from common.i18n import t
from common.runtime import jobs
from tests.capture.test_adapters import SWAY_OUTPUTS
from tests.capture.test_preflight import found, report

KIT = session.Kit
PLAYFIELD = {one.name: one for one in wlr.sway_outputs(SWAY_OUTPUTS)}["DP-1"]
DESCRIBED = ("Input #0, mov,mp4,m4a,3gp,3g2,mj2, from 'playfield.mp4':\n"
             "  Stream #0:0[0x1](und): Video: h264 (High) (avc1 / 0x31637661), "
             "yuv420p(progressive), 1920x1080, 612 kb/s, 30 fps, 30 tbr (default)\n")


class Playfield:
    """A recorder that writes its file when stopped, and an FFmpeg that writes whatever
    it is asked to and describes what it wrote."""

    def __init__(self, *, records: bool = True, encodes: bool = True) -> None:
        self.records = records
        self.encodes = encodes
        self.spawned: list[list[str]] = []

    def popen(self, argv: list[str], **kwargs: Any) -> Any:
        self.spawned.append(argv)
        records, log = self.records, kwargs.get("stderr")

        class Recorder:
            returncode = None

            def poll(self) -> int | None:
                return self.returncode

            def send_signal(self, _sig: int) -> None:
                if records:
                    Path(argv[-1]).write_bytes(b"recorded")
                elif log is not None:
                    log.write(b"wf-recorder: failed to find output DP-9\n")
                self.returncode = 0

            def wait(self, timeout: float | None = None) -> int:
                return 0

        return Recorder()

    def runner(self, argv: list[str], **_: Any) -> Any:
        if "-progress" in argv:
            return SimpleNamespace(stdout="frame=90\nprogress=end\n", stderr="")
        if argv[-2] == "-i":
            return SimpleNamespace(stdout="", stderr=DESCRIBED, returncode=1)
        if not self.encodes and argv[-1].endswith(".mp4"):
            import subprocess
            raise subprocess.CalledProcessError(1, argv, "", "Unrecognized option 'x'.\n")
        Path(argv[-1]).write_bytes(b"made")
        return SimpleNamespace(stdout="", stderr="", returncode=0)

    def kit(self) -> session.Kit:
        return KIT(popen=self.popen, runner=self.runner)


class _Device(unittest.TestCase):
    def setUp(self) -> None:
        held = tempfile.TemporaryDirectory()
        self.addCleanup(held.cleanup)
        adapter = wlr.WlrAdapter({})
        adapter.hardware = lambda ffmpeg: ""  # type: ignore[method-assign]
        config = configparser.ConfigParser()
        config["windows.playfield"] = {"rotation": "0"}
        device = run.Device(adapter, found(), config, {"playfield": PLAYFIELD},
                            placing.Placing([PLAYFIELD], config, []))
        self.unsupported = report(adapters.Unsupported("wayland", adapters.NO_WAY))
        for target, value in (("common.capture.run.reach", device),
                              ("common.capture.preflight.report", report()),
                              ("common.capture.trial.WORK", Path(held.name) / "test")):
            patcher = patch(target, return_value=value) if target.endswith(("reach",
                                                                           "report")) \
                else patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        jobs.reset_for_tests()
        launch_state.clear()
        self.addCleanup(jobs.reset_for_tests)
        self.addCleanup(launch_state.clear)

    def tried(self, playfield: Playfield, **overrides: str) -> dict[str, Any]:
        return trial.test(overrides, kit=playfield.kit(), seconds=0.05)


class TrialTests(_Device):
    def test_what_came_out_is_said_with_a_picture_to_see_which_way_up_it_is(self) -> None:
        said = self.tried(Playfield())

        self.assertTrue(said["ok"])
        self.assertEqual((said["size"], said["fps"], said["frames"]), ([1920, 1080], 30.0, 90))
        self.assertTrue(said["picture"].startswith("data:image/jpeg;base64,"))
        self.assertTrue(said["record"].startswith(
            shlex.join([str(Path("/usr/bin/wf_recorder")), "-o", "DP-1", "-c", "libx264"])))
        self.assertIn("fps=30,", said["encode"])

    def test_a_record_command_that_records_nothing_says_what_it_said(self) -> None:
        said = self.tried(Playfield(records=False))

        self.assertEqual((said["ok"], said["step"], said["reason"]["key"]),
                         (False, "record", trial.RECORDED_NOTHING))
        self.assertEqual(said["detail"], "wf-recorder: failed to find output DP-9")

    def test_an_encode_command_that_fails_says_what_ffmpeg_said(self) -> None:
        said = self.tried(Playfield(encodes=False),
                            encode_command="{ffmpeg} {input} -x {output}")

        self.assertEqual((said["ok"], said["step"], said["detail"]),
                         (False, "encode", "Unrecognized option 'x'."))
        self.assertIn(" -x ", said["encode"])

    def test_the_commands_asked_for_are_the_ones_run(self) -> None:
        playfield = Playfield()

        self.tried(playfield, record_command="{recorder} -o {screen} -f {output}")

        self.assertEqual(playfield.spawned[0][1:3], ["-o", "DP-1"])

    def test_a_command_that_cannot_run_is_refused_before_anything_runs(self) -> None:
        playfield = Playfield()

        with self.assertRaises(service_errors.RefusedError):
            self.tried(playfield, record_command="{recorder} {output} {nope}")
        self.assertEqual(playfield.spawned, [])

    def test_it_waits_for_a_table_or_a_run_to_finish(self) -> None:
        launch_state.set_launching("Medieval Madness", source=launch_state.SOURCE_API)
        with self.assertRaises(service_errors.BlockedError):
            self.tried(Playfield())
        launch_state.clear()

        with jobs.track(jobs.KIND_MEDIA_CAPTURE), self.assertRaises(jobs.JobBusyError):
            self.tried(Playfield())

    def test_a_device_that_records_nothing_says_why(self) -> None:
        with patch("common.capture.preflight.report", return_value=self.unsupported), \
                self.assertRaises(service_errors.UnavailableError) as refused:
            self.tried(Playfield())

        self.assertEqual(str(refused.exception), t(adapters.NO_WAY))


class TrialRouteTests(_Device):
    def test_the_test_is_served(self) -> None:
        client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)
        playfield = Playfield()
        with patch("common.capture.trial.SECONDS", 0.05), \
                patch("common.capture.session.Kit", lambda: playfield.kit()):
            response = client.post("/capture/test", json={"settings": {"fps": "60"}})

        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["ok"])
        self.assertIn("fps=60", response.json()["encode"])


if __name__ == "__main__":
    unittest.main()
