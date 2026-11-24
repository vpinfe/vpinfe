"""What library path a program started from a frozen Linux build is handed."""

from __future__ import annotations

import sys
import unittest
from unittest import mock

from common.games.launchers import Launcher
from common.host import bundled_libraries, launch, tools

BUNDLE = "/opt/vpinfe/_internal"


def _frozen_linux():
    return (mock.patch.object(bundled_libraries.platform, "system", return_value="Linux"),
            mock.patch.object(sys, "frozen", True, create=True))


def _launcher(launch_env: str = "") -> Launcher:
    return Launcher(launcher_id="l1", app="vpx", settings={"launch_env": launch_env})


class ForChildTests(unittest.TestCase):
    def test_the_original_path_comes_back_when_there_was_one(self) -> None:
        env = {"LD_LIBRARY_PATH": BUNDLE, "LD_LIBRARY_PATH_ORIG": "/usr/lib/mine"}
        linux, frozen = _frozen_linux()
        with linux, frozen:
            self.assertEqual(bundled_libraries.for_child(env)["LD_LIBRARY_PATH"], "/usr/lib/mine")

    def test_no_path_is_left_when_there_was_none_before_the_bundle(self) -> None:
        linux, frozen = _frozen_linux()
        with linux, frozen:
            self.assertNotIn("LD_LIBRARY_PATH",
                             bundled_libraries.for_child({"LD_LIBRARY_PATH": BUNDLE}))

    def test_an_empty_original_is_restored_as_empty(self) -> None:
        env = {"LD_LIBRARY_PATH": BUNDLE, "LD_LIBRARY_PATH_ORIG": ""}
        linux, frozen = _frozen_linux()
        with linux, frozen:
            self.assertEqual(bundled_libraries.for_child(env)["LD_LIBRARY_PATH"], "")

    def test_a_build_run_from_source_is_left_alone(self) -> None:
        env = {"LD_LIBRARY_PATH": "/usr/lib/mine"}
        with mock.patch.object(bundled_libraries.platform, "system", return_value="Linux"):
            self.assertEqual(bundled_libraries.for_child(env), env)

    def test_other_systems_are_left_alone(self) -> None:
        env = {"LD_LIBRARY_PATH": BUNDLE}
        with (mock.patch.object(bundled_libraries.platform, "system", return_value="Darwin"),
              mock.patch.object(sys, "frozen", True, create=True)):
            self.assertEqual(bundled_libraries.for_child(env), env)

    def test_the_callers_environment_is_not_changed(self) -> None:
        env = {"LD_LIBRARY_PATH": BUNDLE}
        linux, frozen = _frozen_linux()
        with linux, frozen:
            bundled_libraries.for_child(env)
        self.assertEqual(env, {"LD_LIBRARY_PATH": BUNDLE})


class LaunchEnvTests(unittest.TestCase):
    def test_a_path_set_in_the_launcher_wins_over_the_restore(self) -> None:
        linux, frozen = _frozen_linux()
        with (linux, frozen,
              mock.patch.dict("os.environ", {"LD_LIBRARY_PATH": BUNDLE,
                                             "LD_LIBRARY_PATH_ORIG": "/usr/lib/mine"})):
            env = launch._launch_env(_launcher("LD_LIBRARY_PATH=/opt/dof"))
        self.assertEqual(env["LD_LIBRARY_PATH"], "/opt/dof")

    def test_the_bundle_does_not_reach_vpx_when_the_launcher_sets_nothing(self) -> None:
        linux, frozen = _frozen_linux()
        with linux, frozen, mock.patch.dict("os.environ", {"LD_LIBRARY_PATH": BUNDLE}):
            env = launch._launch_env(_launcher())
        self.assertNotIn("LD_LIBRARY_PATH", env)


class ProbeTests(unittest.TestCase):
    def test_a_probe_is_run_without_the_bundle(self) -> None:
        linux, frozen = _frozen_linux()
        done = mock.Mock(returncode=0, stdout="ok", stderr="")
        with (linux, frozen, mock.patch.dict("os.environ", {"LD_LIBRARY_PATH": BUNDLE}),
              mock.patch.object(tools.subprocess, "run", return_value=done) as run):
            tools.ask(tools.Path("/usr/bin/ffmpeg"), "-version")
        self.assertNotIn("LD_LIBRARY_PATH", run.call_args.kwargs["env"])


if __name__ == "__main__":
    unittest.main()
