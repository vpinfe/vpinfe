"""The DOF helper stops its runner on every way out, not just a clean shutdown."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import types
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from common.config.paths import APP_ROOT
from common.host import dof_service_worker

STUB_RUNNER = '''
import os


class SingleEventDofRunner:
    def __init__(self, rom, **kwargs):
        self._running = False
        with open(os.environ["VPINFE_TEST_DOF_MARKER"] + ".base", "w", encoding="utf-8") as seen:
            seen.write(kwargs.get("base_path", "<not given>"))

    def start(self):
        self._running = True
        return True

    def is_running(self):
        return self._running

    def send_event_token(self, event_token, **kwargs):
        pass

    def stop_event(self):
        pass

    def stop(self, timeout=10.0):
        self._running = False
        with open(os.environ["VPINFE_TEST_DOF_MARKER"], "w", encoding="utf-8") as marker:
            marker.write("stopped")
        return True
'''


@unittest.skipIf(sys.platform == "win32", "SIGTERM does not run handlers on Windows")
class DofHelperShutdownTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        (root / "dof_runner.py").write_text(STUB_RUNNER, encoding="utf-8")
        self.marker = root / "stopped.marker"

        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONPATH"] = str(APP_ROOT)
        env["VPINFE_DOF_DIR"] = str(root)
        env["VPINFE_CONFIG_DIR"] = str(root / "config")
        env["VPINFE_TEST_DOF_MARKER"] = str(self.marker)

        self.proc = subprocess.Popen(
            [sys.executable, "-m", "common.host.dof_service_worker"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=env,
            cwd=str(APP_ROOT),
        )
        self.addCleanup(self._kill_helper)

    def _kill_helper(self):
        if self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait(timeout=5)
        for pipe in (self.proc.stdin, self.proc.stdout):
            if pipe is not None and not pipe.closed:
                pipe.close()

    def _request(self, request_id, command, **payload):
        message = {"id": request_id, "command": command}
        message.update(payload)
        self.proc.stdin.write(json.dumps(message) + "\n")
        self.proc.stdin.flush()

        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline:
            line = self.proc.stdout.readline()
            if not line:
                self.fail(f"helper exited before answering {command}")
            response = json.loads(line)
            if response.get("type") == "response" and response.get("id") == request_id:
                return response
        self.fail(f"timed out waiting for {command}")

    def _start_with_active_event(self):
        started = self._request(1, "start")
        self.assertTrue(started.get("ok"), started)
        self.assertTrue(started.get("started"), started)
        sent = self._request(2, "send_event_token", event_token="E905")
        self.assertTrue(sent.get("ok"), sent)

    def _assert_runner_stopped(self):
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            if self.marker.exists():
                return
            time.sleep(0.05)
        self.fail("helper exited without stopping the runner; DOF outputs stay on")

    def test_the_runner_is_given_the_base_path_the_parent_sent(self):
        started = self._request(1, "start", base_path="/prefs/10.8")

        self.assertTrue(started.get("started"), started)
        self.assertEqual(Path(str(self.marker) + ".base").read_text(encoding="utf-8"),
                         "/prefs/10.8")

    def test_the_runner_is_given_an_empty_base_path_when_none_was_sent(self):
        self._request(1, "start")

        self.assertEqual(Path(str(self.marker) + ".base").read_text(encoding="utf-8"), "")

    def test_sigterm_stops_the_runner(self):
        self._start_with_active_event()

        os.kill(self.proc.pid, signal.SIGTERM)
        self.proc.wait(timeout=10)

        self._assert_runner_stopped()

    def test_sigint_stops_the_runner(self):
        self._start_with_active_event()

        os.kill(self.proc.pid, signal.SIGINT)
        self.proc.wait(timeout=10)

        self._assert_runner_stopped()

    def test_losing_the_parent_stops_the_runner(self):
        self._start_with_active_event()

        self.proc.stdin.close()
        self.proc.wait(timeout=10)

        self._assert_runner_stopped()


class _Refusing:
    def __init__(self, **_kwargs):
        pass

    def start(self):
        return False

    def is_running(self):
        return False

    def last_error(self):
        return "Could not open the boards"


class WhyItDidNotStartTests(unittest.TestCase):
    def _start(self, runner):
        worker = dof_service_worker._Worker()
        with mock.patch.object(dof_service_worker.dof_parent, "_load_runner_class",
                               return_value=(runner, Path("/dof"))):
            return worker._start()

    def test_a_runner_that_would_not_start_gives_what_it_says(self):
        self.assertEqual(self._start(_Refusing), (False, "start_failed:Could not open the boards"))

    def test_a_runner_that_cannot_say_gives_an_empty_reason(self):
        class Mute(_Refusing):
            last_error = None

        self.assertEqual(self._start(Mute), (False, "start_failed:"))

    def test_no_runner_at_all_is_its_own_answer(self):
        self.assertEqual(self._start(None), (False, "runner_unavailable"))

    def test_a_runner_that_is_there_and_will_not_load_is_not_no_runner(self):
        worker = dof_service_worker._Worker()
        with mock.patch.object(dof_service_worker.dof_parent, "_load_runner_class",
                               side_effect=dof_service_worker.dof_parent.RunnerLoadError):
            self.assertEqual(worker._start(), (False, "runner_unloadable"))


class LoadingTheRunnerTests(unittest.TestCase):
    def _runner(self, source):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        (Path(tmp.name) / "dof_runner.py").write_text(source, encoding="utf-8")
        self.enterContext(mock.patch.dict(os.environ, {"VPINFE_DOF_DIR": tmp.name}))
        return dof_service_worker.dof_parent._load_runner_class

    def test_nothing_installed_is_no_class(self):
        with TemporaryDirectory() as empty, \
                mock.patch.dict(os.environ, {"VPINFE_DOF_DIR": empty}), \
                mock.patch.object(dof_service_worker.dof_parent, "_get_dof_base_candidates",
                                  return_value=[Path(empty)]):
            runner_class, _ = dof_service_worker.dof_parent._load_runner_class()

        self.assertIsNone(runner_class)

    def test_a_runner_that_will_not_import_is_an_error_not_an_absence(self):
        load = self._runner("raise ImportError('no dof module')")

        with self.assertRaises(dof_service_worker.dof_parent.RunnerLoadError):
            load()

    def test_a_runner_without_its_class_is_an_error(self):
        load = self._runner("x = 1")

        with self.assertRaises(dof_service_worker.dof_parent.RunnerLoadError):
            load()

    def test_a_library_that_will_not_load_is_an_error_before_the_runner_starts(self):
        load = self._runner("class SingleEventDofRunner: pass")
        broken = types.SimpleNamespace(load_lib=mock.Mock(side_effect=OSError("dlopen failed")))

        with mock.patch.dict(sys.modules, {"dof": broken}), \
                self.assertRaises(dof_service_worker.dof_parent.RunnerLoadError):
            load()

    def test_a_loader_that_raises_anything_is_an_unloadable_library(self):
        load = self._runner("class SingleEventDofRunner: pass")
        broken = types.SimpleNamespace(load_lib=mock.Mock(side_effect=ImportError("no ctypes")))

        with mock.patch.dict(sys.modules, {"dof": broken}), \
                self.assertRaises(dof_service_worker.dof_parent.RunnerLoadError):
            load()

    def test_a_library_that_loads_gives_the_class(self):
        load = self._runner("class SingleEventDofRunner: pass")
        fine = types.SimpleNamespace(load_lib=mock.Mock())

        with mock.patch.dict(sys.modules, {"dof": fine}):
            runner_class, _ = load()

        self.assertEqual(runner_class.__name__, "SingleEventDofRunner")
        fine.load_lib.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
