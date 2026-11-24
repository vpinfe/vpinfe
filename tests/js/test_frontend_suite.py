"""Runs the `*.test.js` files beside this one under Node's own runner.

Skips, naming the reason, when node is missing or older than `engines.node` in
package.json, so a run that left `vpinfe-core.js` untested shows as skipped.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
TIMEOUT = 300


def _minimum_node() -> int:
    engines = json.loads((HERE / "package.json").read_text(encoding="utf-8"))["engines"]
    return int(re.search(r"\d+", engines["node"]).group())  # type: ignore[union-attr]


def _node_major(node: str) -> int | None:
    try:
        out = subprocess.run([node, "--version"], capture_output=True, text=True,
                             timeout=30, check=False).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    found = re.match(r"v(\d+)\.", out.strip())
    return int(found.group(1)) if found else None


class FrontendSuite(unittest.TestCase):
    def test_every_node_test_passes(self) -> None:
        minimum = _minimum_node()
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is not on PATH, so tests/js (vpinfe-core.js) did not run; "
                          f"install Node {minimum} or later")
        major = _node_major(node)
        if major is None or major < minimum:
            self.skipTest(f"{node} is Node {major or 'of unknown version'} and tests/js "
                          f"needs {minimum} or later, so vpinfe-core.js did not run")
        self.assertTrue(list(HERE.glob("*.test.js")), "no *.test.js beside this module")

        try:
            run = subprocess.run([node, "--test", "--test-reporter=spec"], cwd=HERE,
                                 capture_output=True, text=True, timeout=TIMEOUT,
                                 check=False)
        except subprocess.TimeoutExpired as stopped:
            said = stopped.stdout or ""
            if isinstance(said, bytes):
                said = said.decode("utf-8", "replace")
            self.fail(f"node --test did not finish in {TIMEOUT}s\n{said[-4000:]}")

        if run.returncode != 0:
            self.fail(f"node --test exited {run.returncode} in tests/js\n"
                      + run.stdout + run.stderr)


if __name__ == "__main__":
    unittest.main()
