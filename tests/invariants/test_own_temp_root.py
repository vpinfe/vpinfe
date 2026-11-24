"""tests/__init__.py isolates the suite's temp dir the same way it already isolates
its config dir - both resolved at import time, so a fresh interpreter is the only way
to see either decision made."""

from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _import_tests(env: dict[str, str]) -> tuple[str, str]:
    proc = subprocess.run(
        [sys.executable, "-c", "import tests, os; print(os.environ['TMPDIR'])"],
        cwd=str(REPO), env=env, capture_output=True, text=True, timeout=30)
    return proc.stdout, proc.stderr


class OwnTempRootTests(unittest.TestCase):
    def test_a_bare_run_gets_an_isolated_root_removed_once_it_exits(self) -> None:
        env = {k: v for k, v in os.environ.items() if k != "VPINFE_CONFIG_DIR"}

        stdout, stderr = _import_tests(env)

        self.assertEqual("", stderr, stderr)
        made = stdout.strip()
        self.assertNotEqual(env.get("TMPDIR", ""), made)
        self.assertTrue(Path(made).name.startswith("vpinfe-tmp0"), made)
        self.assertFalse(os.path.exists(made))

    def test_a_caller_that_hands_in_a_config_dir_keeps_its_own_tmpdir(self) -> None:
        env = {**os.environ, "VPINFE_CONFIG_DIR": "/tmp/vpinfe-test-caller-config",
              "TMPDIR": "/tmp/vpinfe-test-caller-tmp"}

        stdout, stderr = _import_tests(env)

        self.assertEqual("", stderr, stderr)
        self.assertEqual("/tmp/vpinfe-test-caller-tmp", stdout.strip())


if __name__ == "__main__":
    unittest.main()
