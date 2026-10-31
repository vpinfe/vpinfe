"""A section is a gap when it fails, never an error, and the collectors that ship read
through what already exists."""

from __future__ import annotations

import logging
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from common.host import display_service
from common.runtime import log_setup
from common.support import banner, sections, session
from common.support.collect import SECTIONS
from common.support.sections import Section, SectionText


def _boom() -> SectionText:
    raise RuntimeError("secret detail that must not travel")


class RunningASection(unittest.TestCase):
    def test_a_collector_that_raises_is_a_gap_with_the_kind_of_failure(self) -> None:
        with self.assertLogs("vpinfe.common.support.sections", "WARNING"):
            found = sections.collected(Section("x", "X", 1, _boom))
        self.assertEqual((found.status, found.reason), (sections.NOT_REPORTED, "RuntimeError"))
        self.assertNotIn("secret detail", repr(found))

    def test_a_section_for_another_platform_is_not_applicable_and_is_not_run(self) -> None:
        ran: list[int] = []

        def collect() -> SectionText:
            ran.append(1)
            return SectionText.ok([])

        other = "linux" if sys.platform.startswith("win") else "win32"
        section = Section("x", "X", 1, collect, platforms=(other,))
        self.assertEqual(sections.collected(section).status, sections.NOT_APPLICABLE)
        self.assertEqual(ran, [])

    def test_a_platform_prefix_matches(self) -> None:
        section = Section("x", "X", 1, lambda: SectionText.ok([]), platforms=("linux",))
        self.assertTrue(section.applies("linux"))
        self.assertTrue(section.applies("linux2"))
        self.assertFalse(section.applies("darwin"))
        self.assertTrue(Section("y", "Y", 2, lambda: SectionText.ok([])).applies("anything"))

    def test_each_state_reads_as_what_it_is(self) -> None:
        section = Section("x", "Title", 1, lambda: SectionText.ok([]))
        self.assertEqual(sections.rendered(section, SectionText.ok(["- a", "- b"])),
                         "## Title\n\n- a\n- b\n")
        self.assertIn("Not reported: why",
                      sections.rendered(section, SectionText.not_reported("why")))
        self.assertIn("Not applicable on this device: why",
                      sections.rendered(section, SectionText.not_applicable("why")))
        self.assertIn("Not reported.",
                      sections.rendered(section, SectionText.not_reported("")))


class TheShippedSections(unittest.TestCase):
    def test_they_are_in_order_with_distinct_ids_and_titles(self) -> None:
        orders = [one.order for one in SECTIONS]
        self.assertEqual(orders, sorted(orders))
        self.assertEqual(len({one.id for one in SECTIONS}), len(SECTIONS))
        self.assertEqual(len({one.title for one in SECTIONS}), len(SECTIONS))

    def test_each_one_collects_without_raising(self) -> None:
        for section in SECTIONS:
            with self.subTest(section=section.id):
                found = sections.collected(section)
                self.assertIn(found.status, (sections.OK, sections.NOT_REPORTED,
                                             sections.NOT_APPLICABLE))
                self.assertIsInstance(sections.rendered(section, found), str)

    def test_a_collector_that_fails_inside_does_not_stop_the_rest(self) -> None:
        with patch("common.support.collect.device.about.build_flavor",
                   side_effect=RuntimeError("x")), \
                self.assertLogs("vpinfe.common.support.sections", "WARNING"):
            found = [sections.collected(one) for one in SECTIONS]
        self.assertEqual(len(found), len(SECTIONS))


class TheSessionVariables(unittest.TestCase):
    def test_only_the_named_variables_are_read_and_in_a_fixed_order(self) -> None:
        env = {"XDG_SESSION_TYPE": "wayland", "HOME": "/home/someone", "PATH": "/bin",
               "SWAYSOCK": "/run/user/1000/sway.sock"}
        found = dict(session.read(env, platform="linux"))
        self.assertEqual(found["XDG_SESSION_TYPE"], "wayland")
        self.assertEqual(found["SWAYSOCK"], "set")
        self.assertNotIn("HOME", found)
        self.assertEqual([name for name, _ in session.read(env, platform="linux")][:2],
                         ["XDG_SESSION_TYPE", "XDG_CURRENT_DESKTOP"])

    def test_a_value_that_is_not_a_plain_setting_is_reported_as_set(self) -> None:
        found = dict(session.read({"DISPLAY": "/home/someone/odd/socket"}, platform="linux"))
        self.assertEqual(found["DISPLAY"], "set")

    def test_a_display_on_another_host_is_not_copied(self) -> None:
        for given, shown in ((":0", ":0"), (":1.0", ":1.0"), ("mybox:10.0", "set")):
            with self.subTest(given):
                found = dict(session.read({"DISPLAY": given}, platform="linux"))
                self.assertEqual(found["DISPLAY"], shown)

    def test_an_unset_variable_says_so(self) -> None:
        self.assertEqual(dict(session.read({}, platform="linux"))["DISPLAY"], "not set")

    def test_there_is_nothing_to_report_off_linux(self) -> None:
        self.assertEqual(session.read({"DISPLAY": ":0"}, platform="win32"), [])
        self.assertEqual(session.read({"DISPLAY": ":0"}, platform="darwin"), [])


class TheScreensLine(unittest.TestCase):
    def test_screens_are_numbered_from_zero_like_the_config_and_the_summary(self) -> None:
        monitors = [SimpleNamespace(width=100, height=50, x=0, y=0),
                    SimpleNamespace(width=200, height=80, x=100, y=0)]
        with self.assertLogs("vpinfe.common.host.display_service", "INFO") as seen:
            display_service._log_screens(monitors)
        self.assertEqual(seen.records[0].getMessage(),
                         "Screens: 0: 100x50 at 0,0; 1: 200x80 at 100,0")


class TheLogLocation(unittest.TestCase):
    def test_a_reader_can_name_the_log_without_opening_it(self) -> None:
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(log_setup, "_LOCATED", None), \
                patch.object(logging.getLogger(), "handlers", []):
            self.assertIsNone(log_setup.log_file())
            log_setup.locate_for_reading(Path(folder))
            self.assertEqual(log_setup.log_file(), Path(folder) / log_setup.DEFAULT_LOG_FILE_NAME)
            self.assertEqual(list(Path(folder).iterdir()), [])


class TheBanner(unittest.TestCase):
    def test_it_names_the_build_and_the_system_and_nothing_private(self) -> None:
        text = "\n".join(banner.lines())
        for label in ("Build:", "Release target:", "System:", "Python:", "Windowing system:",
                      "Language:"):
            self.assertIn(label, text)
        for word in ("Host", "User", "Home", "Screens"):
            self.assertNotIn(f"  {word}:", text)

    def test_it_is_written_as_one_record_with_the_version(self) -> None:
        with self.assertLogs("vpinfe.common.support.banner", "INFO") as seen:
            banner.write()
        self.assertEqual(len(seen.records), 1)
        self.assertRegex(seen.records[0].getMessage(), r"^Starting VPinFE \S+\n  Build: ")

    def test_a_failure_to_gather_it_is_a_warning_and_never_raises(self) -> None:
        with patch.object(banner, "lines", side_effect=RuntimeError("x")), \
                self.assertLogs("vpinfe.common.support.banner", "WARNING"):
            banner.write()


if __name__ == "__main__":
    unittest.main()
