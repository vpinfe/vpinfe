"""A live instance resolves Visual Pinball's preferences folder under a home of its own.

The first-run Setup writes the program's ini, so an instance sharing the developer's home
rewrites the real file on every run. Nothing is booted here: the environment `_start`
hands the child is captured, and a Python given it resolves the folders the way the app
does.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

from tests.support.live_instance import REPO_ROOT, LiveInstance

ASK = ("import json; from pathlib import Path; from apps.vpx import config; "
       "base, old = config._machine_folders(); "
       "print(json.dumps([str(Path.home()), str(base), str(old)]))")


class LiveInstanceHome(unittest.TestCase):
    instance: LiveInstance

    @classmethod
    def setUpClass(cls) -> None:
        cls.instance = LiveInstance(Path(os.devnull))

    @classmethod
    def tearDownClass(cls) -> None:
        cls.instance._home_tmp.cleanup()
        cls.instance._tmp.cleanup()

    def test_every_folder_the_app_resolves_is_under_the_instances_home(self) -> None:
        with mock.patch("subprocess.Popen") as popen:
            self.instance._start()
        assert self.instance._log is not None
        self.instance._log.close()
        given = popen.call_args.kwargs["env"]
        answered = subprocess.run(
            [sys.executable, "-c", ASK], cwd=str(REPO_ROOT), capture_output=True,
            text=True, check=True, env=given).stdout
        home = self.instance.home.resolve()
        self.assertNotEqual(home, Path(os.environ.get("HOME", "/")).resolve())
        for found in json.loads(answered):
            if found != "None":
                self.assertTrue(Path(found).resolve().is_relative_to(home), found)


if __name__ == "__main__":
    unittest.main()
