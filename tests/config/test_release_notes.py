"""The release notes an update carries, and which of them a person has not read."""

from __future__ import annotations

import unittest

from common.release import release_notes
from tests.support.library import TempTree

CURRENT = """## VPinFE Release Notes

### Summary
The third one.

### Before you upgrade
- Back up first, and read this
  before you do.

### What's New
- **Core** - A `thing` that is **new**.
- **Frontend** - Another.
"""


class VersionTests(unittest.TestCase):
    def test_a_tag_is_a_release_and_a_build_name_is_not(self) -> None:
        for version in ("v3.0.0", "3.0.0", "v3.0.0-beta.1", "v3.0-beta.1", "v3.0"):
            self.assertTrue(release_notes.is_release(version), version)
        for version in ("dev-local", "dev-abc1234", "", "v3"):
            self.assertFalse(release_notes.is_release(version), version)

    def test_a_beta_named_without_a_patch_sorts_before_its_release(self) -> None:
        self.assertTrue(release_notes.newer("v3.0.0", "v3.0-beta.1"))
        self.assertTrue(release_notes.newer("v3.0-beta.2", "v3.0-beta.1"))

    def test_later_is_later(self) -> None:
        self.assertTrue(release_notes.newer("v3.0.1", "v3.0.0"))
        self.assertTrue(release_notes.newer("v3.1.0", "v3.0.9"))
        self.assertFalse(release_notes.newer("v3.0.0", "v3.0.0"))
        self.assertFalse(release_notes.newer("v2.9.9", "v3.0.0"))

    def test_a_beta_comes_before_its_release_and_after_the_one_before_it(self) -> None:
        self.assertTrue(release_notes.newer("v3.0.0", "v3.0.0-beta.2"))
        self.assertTrue(release_notes.newer("v3.0.0-beta.2", "v3.0.0-beta.1"))
        self.assertTrue(release_notes.newer("v3.0.0-beta.1", "v2.6.3"))
        self.assertTrue(release_notes.newer("v3.0.0-beta.10", "v3.0.0-beta.9"))


class ParseTests(unittest.TestCase):
    def test_sections_hold_their_bullets_with_the_area_split_off(self) -> None:
        sections = release_notes.parse(CURRENT)

        self.assertEqual([one.heading for one in sections],
                         ["Summary", "Before you upgrade", "What's New"])
        self.assertEqual(sections[0].entries[0].text, "The third one.")
        self.assertEqual(sections[0].entries[0].area, "")
        self.assertEqual(sections[1].entries[0].text,
                         "Back up first, and read this before you do.")
        self.assertEqual([(one.area, one.text) for one in sections[2].entries],
                         [("Core", "A thing that is new."), ("Frontend", "Another.")])


class CrossedTests(TempTree):
    def setUp(self) -> None:
        super().setUp()
        self.folder = self.root / "releases"
        self.folder.mkdir()
        for version in ("v2.6.3", "v3.0.0", "v3.1.0", "v3.2.0"):
            (self.folder / f"{version}.md").write_text(
                f"### Summary\nNotes for {version}.\n", encoding="utf-8")
        self.current = self.root / "RELEASE_NOTES.md"
        self.current.write_text(CURRENT, encoding="utf-8")

    def crossed(self, since: str, running: str) -> list[str]:
        return [one.version for one in release_notes.crossed(
            since, running, folder=self.folder, current=self.current)]

    def test_every_version_after_the_last_run_up_to_the_running_one_newest_first(self) -> None:
        self.assertEqual(self.crossed("v2.6.3", "v3.1.0"), ["v3.1.0", "v3.0.0"])

    def test_the_running_version_reads_the_current_file_not_its_archived_copy(self) -> None:
        found = release_notes.crossed("v3.0.0", "v3.1.0", folder=self.folder,
                                      current=self.current)

        self.assertEqual(found[0].version, "v3.1.0")
        self.assertEqual(found[0].sections[0].entries[0].text, "The third one.")

    def test_a_later_version_than_the_running_one_is_not_shown(self) -> None:
        self.assertNotIn("v3.2.0", self.crossed("v2.6.3", "v3.1.0"))

    def test_nothing_is_recorded_so_only_the_running_version_is_shown(self) -> None:
        self.assertEqual(self.crossed("", "v3.1.0"), ["v3.1.0"])

    def test_an_archive_that_is_missing_still_shows_the_running_version(self) -> None:
        found = release_notes.crossed("v2.6.3", "v3.1.0", folder=self.root / "nowhere",
                                      current=self.current)

        self.assertEqual([one.version for one in found], ["v3.1.0"])

    def test_a_file_that_is_not_a_version_is_left_out(self) -> None:
        (self.folder / "README.md").write_text("how to add one", encoding="utf-8")

        self.assertEqual(self.crossed("v2.6.3", "v3.1.0"), ["v3.1.0", "v3.0.0"])

    def test_the_current_file_missing_leaves_the_archived_copy(self) -> None:
        self.current = self.root / "absent"

        self.assertEqual(self.crossed("v2.6.3", "v3.1.0"), ["v3.1.0", "v3.0.0"])


if __name__ == "__main__":
    unittest.main()
