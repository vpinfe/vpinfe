"""This install is addressed on the ports its servers were started on, never on the setting.

A port setting saved while VPinFE runs moves nothing until the next start, so a read of the
setting sends the caller to a port nothing is listening on. `common.devices.serving.network`
answers with the started ports.
"""

from __future__ import annotations

import ast
import unittest

from tests.support import trees
from tests.support.project import REPO, project_files

# What reads the network settings themselves, and why that is right for it.
READS_THE_SETTING = {
    # The fallback for a port nothing in this process has started.
    "common/devices/serving.py",
    # Starts the window channel and the HTTP port, so the setting is what it binds.
    "frontend/runtime.py",
    "main.py",
    # Another process, asking whether an install is running.
    "cli.py",
    # Reads the library address, which is not a port.
    "frontend/library_resolver.py",
}


def _settings_reads(tree: ast.Module) -> list[int]:
    return [node.lineno for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "from_config"
            and isinstance(node.func.value, ast.Name) and node.func.value.id == "NetworkConfig"]


class RunningInstallIsReachedWhereItListens(unittest.TestCase):
    def test_nothing_else_reads_the_network_settings(self) -> None:
        offenders, readers = [], set()
        for path in project_files("*.py"):
            name = path.relative_to(REPO).as_posix()
            if name.startswith("tests/") or name == "common/config/config_access.py":
                continue
            lines = _settings_reads(trees.tree_for(path))
            if lines:
                readers.add(name)
            if lines and name not in READS_THE_SETTING:
                offenders += [f"{name}:{line}" for line in lines]

        self.assertEqual(offenders, [], "ask common.devices.serving.network(config) instead:\n  "
                                        + "\n  ".join(offenders))
        # A sweep that finds nothing passes and measures nothing.
        self.assertTrue(readers >= {"common/devices/serving.py", "frontend/runtime.py"})


if __name__ == "__main__":
    unittest.main()
