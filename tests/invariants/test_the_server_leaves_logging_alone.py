"""The web server starts without uvicorn's logging configuration.

`logging.config.dictConfig` closes every handler while holding the logging module's lock,
and `main.reconfigure_app_logging` closes and rebuilds the same handlers on another
thread. Each takes one lock and waits for the other's, so a start that interleaves them
never reaches the WebSocket bridge. Handing uvicorn no configuration leaves one thread
changing the handlers.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from tests.support import trees

SOURCE = Path(__file__).resolve().parents[2] / "manager" / "host.py"


def _run_calls() -> list[ast.Call]:
    return [node for node in ast.walk(trees.tree_for(SOURCE))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "run" and getattr(node.func.value, "id", "") == "ui"]


class TheServerLeavesLoggingAlone(unittest.TestCase):

    def test_it_starts_nicegui_with_no_log_config(self):
        calls = _run_calls()
        self.assertEqual(len(calls), 1, "expected one ui.run in host.py")
        keywords = {kw.arg: kw.value for kw in calls[0].keywords}
        self.assertIn("log_config", keywords, "ui.run must be given log_config=None")
        self.assertIsInstance(keywords["log_config"], ast.Constant)
        self.assertIsNone(keywords["log_config"].value)


if __name__ == "__main__":
    unittest.main()
