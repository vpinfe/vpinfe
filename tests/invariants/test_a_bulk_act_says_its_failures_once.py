"""An act over several items says what failed once, not once per item.

A `ui.notify` inside a loop fires once per pass, so a refusal every item shares reaches the
person as the same toast three times. The loop hands its failures to
`manager.kit.failed_together.Failures` and says them after it. Where a toast per pass is
the right thing, the site is named below with the reason.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from tests.support import trees

ROOT = Path(__file__).resolve().parents[2]

_ONCE = "ends the act at the first failure, so it says one thing"
_POLLING = "a poll of one job: each notify is followed by a return"
_WALK = "a walk: each toast answers the step just taken"

# (file, function) -> why each pass's toast is right.
KEPT: dict[tuple[str, str], str] = {
    ("manager/app_control.py", "_notice"): "one toast per open tab, each told once",
    ("manager/backups/backup_cleanup.py", "go"): _ONCE,
    ("manager/backups/backups.py", "delete"): _ONCE,
    ("manager/backups/backups.py", "restore"): _ONCE,
    ("manager/backups/backups_move.py", "_report"): _POLLING,
    ("manager/devices/send_to_device.py", "send"): _POLLING,
    ("manager/extensions/ext_lists.py", "open_over"): _WALK,
    ("manager/frontend/players.py", "_gone"): _ONCE,
    ("manager/frontend/themes.py", "_install"):
        "the toast in the loop is each theme's success; its failures are collected",
    ("manager/kit/walks.py", "each"): _WALK,
    ("manager/library/discovery_get.py", "hand_over"):
        "a source's own message about what it did, not a failure",
    ("manager/library/import_dialog.py", "open_for"):
        "what became of each file, one fact per file, not a failure",
    ("manager/library/media_fill.py", "get"): _POLLING,
    ("manager/library/media_rotate.py", "run"): _POLLING,
    ("manager/library/uploads.py", "confirmed_import"):
        "each distinct reason once, then the act ends",
    ("manager/page.py", "_rescan"): _POLLING,
}


def notifying_loops(tree: ast.Module) -> list[tuple[int, str]]:
    """(line, function) for each `notify` that sits in a loop of its own function."""
    found: list[tuple[int, str]] = []

    def walk(node: ast.AST, function: str, looping: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                walk(child, getattr(child, "name", "<lambda>"), False)
                continue
            if isinstance(child, ast.Call) and getattr(child.func, "attr", "") == "notify" \
                    and looping:
                found.append((child.lineno, function))
            walk(child, function, looping or isinstance(
                child, (ast.For, ast.AsyncFor, ast.While)))

    walk(tree, "", False)
    return found


def _sources() -> list[Path]:
    return [path for path in sorted((ROOT / "manager").rglob("*.py"))
            if "__pycache__" not in path.parts]


class ABulkActSaysItsFailuresOnce(unittest.TestCase):
    def test_no_loop_notifies_per_item_outside_the_kept_sites(self) -> None:
        found = [f"{path.relative_to(ROOT).as_posix()}:{line} {function}"
                 for path in _sources()
                 for line, function in notifying_loops(trees.tree_for(path))
                 if (path.relative_to(ROOT).as_posix(), function) not in KEPT]
        self.assertEqual(found, [], "collect with Failures and say it after the loop, "
                                    "or name the site in KEPT with the reason")

    def test_each_kept_site_is_still_there(self) -> None:
        present = {(path.relative_to(ROOT).as_posix(), function)
                   for path in _sources()
                   for _, function in notifying_loops(trees.tree_for(path))}
        self.assertEqual(sorted(set(KEPT) - present), [], "a kept site that is gone")

    def test_the_scan_finds_what_it_is_there_to_find(self) -> None:
        source = ("async def act(items):\n"
                  "    for one in items:\n"
                  "        ui.notify('x')\n"
                  "    ui.notify('after')\n"
                  "    while True:\n"
                  "        def inner():\n"
                  "            ui.notify('own function')\n"
                  "        ui.notify('y')\n")

        found = notifying_loops(trees.parse_snippet(source))

        self.assertEqual(found, [(3, "act"), (8, "act")])


if __name__ == "__main__":
    unittest.main()
