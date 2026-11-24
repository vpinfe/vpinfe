"""Every file under `tests/fixtures/` is committed, and git is not ignoring one.

`.gitignore` excludes `*.json` and names the exceptions, because config, logs and caches
are JSON and there are far more of those than fixtures worth keeping. The cost is that
adding a fixture without its negation fails **silently**: the file is on the author's
disk, every test passes, and a clean checkout is missing it. That has nearly happened
three times.

This is the check that makes it loud. It reads git rather than the filesystem, so it sees
what a clean checkout would.
"""

from __future__ import annotations

import pathlib
import subprocess
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent.parent
FIXTURES = REPO / "tests" / "fixtures"


def _git(*args: str) -> list[str]:
    result = subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True)
    return [line for line in result.stdout.splitlines() if line.strip()]


@unittest.skipIf(not (REPO / ".git").exists(), "not a git checkout")
class FixturesAreCommittedTests(unittest.TestCase):
    def test_every_fixture_on_disk_is_tracked(self) -> None:
        """A fixture git does not know about is one a clean checkout will not have."""
        tracked = {REPO / path for path in _git("ls-files", "tests/fixtures")}
        on_disk = {path for path in FIXTURES.rglob("*")
                   if path.is_file() and "__pycache__" not in path.parts
                   and path.name != ".DS_Store"}

        untracked = sorted(path.relative_to(REPO).as_posix() for path in on_disk - tracked)

        self.assertEqual(untracked, [],
                         "these exist locally but are not committed - if .gitignore is "
                         "swallowing them, add a negation in the same commit that adds "
                         "the fixture")

    def test_no_tracked_fixture_is_also_ignored(self) -> None:
        """Tracked-but-ignored is the state that bites on the *next* edit: git keeps
        serving the committed copy and quietly declines to notice changes to it."""
        tracked = _git("ls-files", "tests/fixtures")
        ignored = _git("check-ignore", *tracked) if tracked else []

        self.assertEqual(ignored, [],
                         "committed but matched by .gitignore - the negation is missing "
                         "or was written after the file was added")

    def test_the_fixtures_the_suite_reads_are_all_present(self) -> None:
        """Named rather than globbed: a fixture that vanished would otherwise make this
        pass by having nothing to check."""
        expected = {
            "config_defaults.json",
            "config_legacy_names.json",
            "parity_baseline_master.json",
            "theme_payload.json",
            "theme-harness/manifest.json",
        }

        missing = sorted(name for name in expected if not (FIXTURES / name).exists())

        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()
