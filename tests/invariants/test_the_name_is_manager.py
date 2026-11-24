"""The configuration app is called Manager, in every path, word and catalog key.

The word "console" still appears where it means something else; each of those is listed
here with what it means, so a new occurrence is a failure unless it is one of them.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from tests.support.catalogs import served
from tests.support.project import REPO, project_files

TEXT = {".py", ".json", ".md", ".js", ".html", ".css", ".yml", ".yaml", ".toml", ".spec",
        ".sh", ".txt", ".cfg", ".ini", ".svg", ""}
SKIPPED_DIRS = ("frontend/static/themes/", "third_party/", "web/")
GENERATED = {"common/i18n/catalogs/qps.json", "common/i18n/catalogs/en.hashes.json"}

# (path regex, line regex, what it means). A line is clean once these are removed.
MEANS_SOMETHING_ELSE = [
    (r".", r"console_out", "the theme call that prints to the terminal VPinFE started from"),
    (r"\.(js|html)$",
     r"\bconsole\.(log|warn|error|info|debug|table|trace|assert|group|groupEnd|time|timeEnd|dir)\b",
     "the browser's own logging object"),
    (r"\.(js|html)$", r"\bconsole\b(?=\s*[,)\[]|\s*=)", "the browser's own logging object"),
    (r"\.(js|html)$", r"originalConsole|overrideConsole",
     "wrappers around the browser's logging object"),
    (r".", r"consoleAPICalled", "the Chrome DevTools Protocol event for a page's log line"),
    (r".", r"\[Console\]::", "the .NET class that reads the Windows terminal"),
    (r".", r"GetConsoleWindow", "the Win32 call that finds the terminal window"),
    (r"\.spec$", r"\bconsole\s*=", "PyInstaller's flag for a terminal window"),
    (r".", r"""\.on\(\s*["']console["']""", "Playwright's event for a page's log line"),
    (r"^(common/(runtime/log_setup|config/config_schema|config/config_store)|tests/(config/test_config_store"
     r"|api/test_config)|tests/fixtures/config_legacy_names)\.(py|json)$", r"console",
     "the 2.x [logger] console key, still read and written back as terminal"),
    (r"^docs/extensions/(cookbook|reference/ui)\.md$", r"console",
     "the tool_link recipe's own link to a hardware tool's console"),
    (r"^docs/extensions/examples/recipes/tool_link/", r"console",
     "the tool_link recipe's own link to a hardware tool's console"),
    (r"^tests/extensions/test_guide\.py$", r"console", "the tool_link recipe's guide test"),
    (r"^docs/conventions\.md$", r"console\.log", "a JS example logging to the browser"),
    (r"^docs/compatibility-3\.0\.md$", r"console", "what 2.x called it"),
    (r"^tests/invariants/test_the_name_is_manager\.py$", r"console", "this file"),
]
PATTERNS = [(re.compile(path), re.compile(line, re.I if line.startswith("console") else 0), why)
            for path, line, why in MEANS_SOMETHING_ELSE]


def _files() -> list[tuple[str, Path]]:
    found = []
    for path in project_files("*"):
        rel = path.relative_to(REPO).as_posix()
        if rel in GENERATED or rel.startswith(SKIPPED_DIRS) or path.suffix not in TEXT:
            continue
        found.append((rel, path))
    return found


def _leftovers(rel: str, text: str) -> list[int]:
    lines = []
    for number, line in enumerate(text.splitlines(), 1):
        for path, pattern, _why in PATTERNS:
            if path.search(rel):
                line = pattern.sub("", line)
        if re.search("console", line, re.I):
            lines.append(number)
    return lines


class TheNameIsManager(unittest.TestCase):
    def test_no_path_is_called_console(self) -> None:
        named = [rel for rel, _ in _files()
                 if any("console" in part.lower() for part in rel.split("/"))
                 and not re.search(r"^docs/extensions/examples/recipes/tool_link/", rel)]

        self.assertEqual(named, [])

    def test_no_file_says_console_for_the_manager(self) -> None:
        found = {}
        for rel, path in _files():
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            if lines := _leftovers(rel, text):
                found[rel] = lines[:5]

        self.assertEqual(found, {})

    def test_the_catalog_has_no_console_key(self) -> None:
        self.assertEqual(sorted(key for key in served() if "console" in key.lower()), [])

    def test_the_manager_answers_at_its_own_address_and_the_old_ones_are_gone(self) -> None:
        from nicegui import Client, app

        import manager.page  # noqa: F401  registers the pages
        routes = set(Client.page_routes.values()) | {getattr(r, "path", "") for r in app.routes}

        self.assertIn("/manager", routes)
        self.assertEqual(routes & {"/console", "/mobile"}, set())


if __name__ == "__main__":
    unittest.main()
