"""The suite's own bootstrap ran, so its guards are actually in place.

`python -m unittest discover tests` makes `tests/` the top-level directory, so the group
folders load as `api.*` and `config.*` and `tests/__init__.py` is never imported - which
is where the power guard is installed.

Run the suite as `python -m unittest discover -t . -s tests`.
"""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

from platformdirs import user_config_dir


class BootstrapTests(unittest.TestCase):
    USE = "Use: python -m unittest discover -t . -s tests"

    def test_the_tests_package_was_imported(self) -> None:
        # assertTrue, not assertIn: assertIn prints the haystack, and every module
        # loaded so far is not a useful thing to read under a one-line failure.
        self.assertTrue("tests" in sys.modules,
                        f"discovery never imported tests/__init__.py. {self.USE}")

    def test_the_power_guard_is_installed(self) -> None:
        """Checked by identity, not by running one: the point is to never reach it."""
        self.assertEqual(subprocess.Popen.__name__, "_GuardedPopen",
                         "subprocess.Popen is not guarded, so a test reaching a real "
                         f"poweroff or reboot would run it. {self.USE}")

    def test_the_config_directory_is_not_the_developers_own(self) -> None:
        from common.config import paths
        self.assertNotEqual(paths.CONFIG_DIR, _resolve_config_dir_without_override(),
                            "tests are reading the real vpinfe.json, so what they "
                            f"assert depends on whose machine they run on. {self.USE}")


    def test_visual_pinballs_own_settings_are_out_of_reach(self) -> None:
        from apps.vpx import config
        self.assertEqual(config._machine_folders(), (None, None),
                         "a launcher with an empty Settings File would read and write "
                         f"this machine's real VPinballX.ini. {self.USE}")


def _resolve_config_dir_without_override() -> Path:
    """Where a real install would keep its config on this machine."""
    return Path(user_config_dir("vpinfe", "vpinfe"))
