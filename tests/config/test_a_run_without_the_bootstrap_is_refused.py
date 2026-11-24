"""A unittest run without the test bootstrap is refused the real config folder."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from common.config.paths import APP_ROOT

# Imports the one module that decides where the config folder is, and has a test so that
# a run which gets that far exits 0 on every Python.
PROBE = """\
import unittest

import common.config.paths


class Probe(unittest.TestCase):
    def test_resolved(self) -> None:
        self.assertTrue(common.config.paths.CONFIG_DIR)
"""


class BareDiscoverTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scratch = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (self.scratch / "test_probe.py").write_text(PROBE)

    def _discover(self, **env: str) -> subprocess.CompletedProcess[str]:
        """The wrong invocation: the folder holding the tests is the top level."""
        environment = {k: v for k, v in os.environ.items() if k != "VPINFE_CONFIG_DIR"}
        environment.update(PYTHONPATH=str(APP_ROOT), **env)
        return subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "-s", str(self.scratch)],
            cwd=self.scratch, env=environment, capture_output=True, text=True, timeout=120)

    def test_a_bare_discover_is_refused_with_the_command_to_use(self) -> None:
        run = self._discover()

        self.assertNotEqual(run.returncode, 0, run.stderr)
        self.assertIn("python -m unittest discover -t . -s tests", run.stderr)

    def test_the_same_run_goes_through_when_it_is_given_a_config_dir(self) -> None:
        given = self.scratch / "config"

        run = self._discover(VPINFE_CONFIG_DIR=str(given))

        self.assertEqual(run.returncode, 0, run.stderr)


if __name__ == "__main__":
    unittest.main()
