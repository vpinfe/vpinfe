"""Which settings the build that wrote an ini has."""

from __future__ import annotations

import unittest
from unittest import mock

from apps.vpx import build
from apps.vpx.setting_types import Span


class RevTests(unittest.TestCase):
    def test_the_rev_is_what_follows_the_line(self) -> None:
        self.assertEqual(build.rev_of("10815985"), 5985)

    def test_a_10_9_build_has_the_rev_it_would_have_had_on_the_earlier_line(self) -> None:
        self.assertEqual(build.rev_of("10906400"), 6400)

    def test_a_build_of_a_line_before_both_has_no_rev_to_compare(self) -> None:
        self.assertIsNone(build.rev_of("10800000"))
        self.assertIsNone(build.rev_of("1080123"))

    def test_nothing_or_nonsense_has_none(self) -> None:
        for said in (None, "", "   ", "1081", "1081x", "10.8.1", "1081\u00b2", "1081\u0665"):
            with self.subTest(said=said):
                self.assertIsNone(build.rev_of(said))

    def test_surrounding_space_is_ignored(self) -> None:
        self.assertEqual(build.rev_of(" 10815353\n"), 5353)


class DeclaresTests(unittest.TestCase):
    def setUp(self) -> None:
        patch = mock.patch.dict(build.SPANS, {
            "A.added": Span(100, None),
            "A.removed": Span(0, 200),
            "A.between": Span(100, 200),
        }, clear=True)
        patch.start()
        self.addCleanup(patch.stop)

    def test_a_setting_with_no_span_is_in_every_build(self) -> None:
        self.assertTrue(build.declares("A.plain", 1))

    def test_an_added_setting_starts_at_its_first_rev(self) -> None:
        self.assertFalse(build.declares("A.added", 99))
        self.assertTrue(build.declares("A.added", 100))

    def test_a_removed_setting_is_gone_from_the_rev_that_took_it_out(self) -> None:
        self.assertTrue(build.declares("A.removed", 199))
        self.assertFalse(build.declares("A.removed", 200))

    def test_a_setting_present_for_a_while_is_in_between_only(self) -> None:
        self.assertEqual([build.declares("A.between", rev) for rev in (99, 100, 199, 200)],
                         [False, True, True, False])

    def test_a_plugin_the_program_has_is_offered_its_new_setting_to_an_older_ini(self) -> None:
        self.assertFalse(build.declares("A.added", 99))
        self.assertTrue(build.declares("A.added", 99, plugin_present=True))

    def test_a_plugin_the_program_has_is_not_offered_a_setting_it_dropped(self) -> None:
        self.assertFalse(build.declares("A.removed", 200, plugin_present=True))

    def test_a_build_with_no_known_rev_is_shown_everything(self) -> None:
        for key in ("A.added", "A.removed", "A.between"):
            with self.subTest(key=key):
                self.assertTrue(build.declares(key, None))


class PredatesPluginsTests(unittest.TestCase):
    def test_a_line_before_the_one_with_plugins_does(self) -> None:
        for said in ("10800000", "10700123"):
            with self.subTest(said=said):
                self.assertTrue(build.predates_plugins(said))

    def test_the_line_itself_and_later_ones_do_not(self) -> None:
        for said in ("10815353", "10900000", "10906400"):
            with self.subTest(said=said):
                self.assertFalse(build.predates_plugins(said))

    def test_nothing_or_nonsense_is_not_known_to(self) -> None:
        for said in (None, "", "  ", "10.8.0", "abc", "10"):
            with self.subTest(said=said):
                self.assertFalse(build.predates_plugins(said))


class EnableDefaultTests(unittest.TestCase):
    def test_before_the_switch_b2s_is_off_and_the_legacy_renderer_on(self) -> None:
        self.assertEqual([build.enable_default(one, 6273) for one in ("B2S", "B2SLegacy")],
                         list(("0", "1")))

    def test_from_the_switch_b2s_is_on_and_the_legacy_renderer_off(self) -> None:
        self.assertEqual([build.enable_default(one, 6274) for one in ("B2S", "B2SLegacy")],
                         list(("1", "0")))

    def test_on_windows_the_legacy_renderer_was_off_before_the_switch_too(self) -> None:
        self.assertEqual(build.enable_default("B2SLegacy", 6273, windows=True), "0")
        self.assertEqual(build.enable_default("B2S", 6273, windows=True), "0")

    def test_a_plugin_with_its_own_default_and_an_unknown_build_have_none(self) -> None:
        self.assertIsNone(build.enable_default("PinMAME", 6300))
        self.assertIsNone(build.enable_default("B2S", None))


class SettledRevTests(unittest.TestCase):
    def test_nixie_without_the_legacy_renderer_is_a_build_that_dropped_it(self) -> None:
        self.assertEqual(build.settled_rev(6100, frozenset({"B2S", "Nixie"})), 6293)
        self.assertEqual(build.settled_rev(None, frozenset({"B2S", "Nixie"})), 6293)

    def test_a_newer_rev_in_the_file_stands(self) -> None:
        self.assertEqual(build.settled_rev(6400, frozenset({"Nixie"})), 6400)

    def test_the_legacy_renderer_beside_nixie_leaves_the_rev_alone(self) -> None:
        self.assertEqual(build.settled_rev(6270, frozenset({"B2SLegacy", "Nixie"})), 6270)

    def test_no_folder_to_read_leaves_the_rev_alone(self) -> None:
        self.assertEqual(build.settled_rev(6100, None), 6100)
        self.assertIsNone(build.settled_rev(None, frozenset()))


if __name__ == "__main__":
    unittest.main()
