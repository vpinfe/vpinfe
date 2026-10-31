"""A person's draft of a support package: what was read, what they changed afterwards, and
the places the finished package can go."""

from __future__ import annotations

import unittest
from dataclasses import replace
from unittest.mock import patch

from common.support import destinations, package, pieces, redact
from common.support.draft import Draft
from tests.supportpackage.test_package import SENTINELS, _Folder

SUMMARY = """# VPinFE support package

## VPinFE

- **Version:** 3.0.0
- **Build:** Full

## This Device

- **Operating system:** Linux 6.8
- **Windowing system:** Wayland
- **Player:** Pat Example

## Displays And Layout

### Screen 1

- **Size:** 1920x1080

### Findings

- **Rotation:** playfield is not rotated

## Health Now

### This Run's Log

- **Errors:** 4
- **Warnings:** 2
- **3x** ModuleNotFoundError: no module named thing
- **1x** something rare
"""


class DraftTests(_Folder):
    def setUp(self) -> None:
        super().setUp()
        self.write_logs()
        self.draft = Draft()

    def test_toggling_a_privacy_choice_does_not_read_the_device_again(self) -> None:
        self.draft.gather()
        with patch("common.support.draft.package.collect") as collect:
            self.draft.hide_tables = True
            self.draft.reassemble()
        collect.assert_not_called()

    def test_asking_for_other_parts_reads_again_and_starts_the_edits_over(self) -> None:
        self.draft.gather()
        self.draft.leave_out(package.PREVIOUS_LOG, True)
        self.draft.remove_line(package.LOG, 0)

        self.draft.previous_log = False
        self.draft.gather()

        self.assertEqual((self.draft.left_out, self.draft.removed), ([], []))

    def test_a_file_left_out_is_not_in_the_package_and_comes_back(self) -> None:
        self.draft.gather()
        self.draft.leave_out(package.PREVIOUS_LOG, True)
        made = self.draft.reassemble()
        self.assertIsNone(made.part(package.PREVIOUS_LOG))
        self.assertIsNotNone(made.part(package.README))
        self.assertIsNotNone(made.part(package.SUMMARY))

        self.draft.leave_out(package.PREVIOUS_LOG, False)

        self.assertIsNotNone(self.draft.reassemble().part(package.PREVIOUS_LOG))

    def test_a_removed_line_keeps_the_numbers_of_the_lines_after_it(self) -> None:
        before = self.draft.gather().part(package.LOG)
        assert before is not None
        self.draft.remove_line(package.LOG, 1)

        after = self.draft.reassemble().part(package.LOG)

        assert after is not None
        lines = after.text.split("\n")
        self.assertEqual(lines[1], package.REMOVED_LINE)
        self.assertEqual(len(lines), len(before.text.split("\n")))
        self.assertEqual(lines[2], before.text.split("\n")[2])

    def test_putting_the_lines_back_restores_them(self) -> None:
        before = self.draft.gather().part(package.LOG)
        self.draft.remove_line(package.LOG, 1)
        self.draft.reassemble()

        self.draft.restore_lines()

        after = self.draft.reassemble().part(package.LOG)
        assert before is not None and after is not None
        self.assertEqual(after.text, before.text)

    def test_a_word_to_hide_is_added_once_whatever_its_case(self) -> None:
        self.assertTrue(self.draft.hide_word("  Zedmoon "))
        self.assertFalse(self.draft.hide_word("zedmoon"))
        self.assertFalse(self.draft.hide_word("   "))
        self.assertEqual(self.draft.hide, ["Zedmoon"])

        self.draft.unhide("Zedmoon")

        self.assertEqual(self.draft.hide, [])

    def test_a_word_to_hide_is_replaced_in_the_package(self) -> None:
        self.draft.gather()
        self.draft.hide_word("opened")

        text = self.draft.reassemble().part(package.LOG)

        assert text is not None
        self.assertNotIn("opened", text.text)

    def test_dropping_it_lets_go_of_everything_read(self) -> None:
        self.draft.gather()
        self.draft.remove_line(package.LOG, 0)

        self.draft.drop()

        self.assertIsNone(self.draft.raw)
        self.assertIsNone(self.draft.made)
        self.assertEqual(self.draft.removed, [])
        with self.assertRaises(RuntimeError):
            self.draft.reassemble()

    def _with_repeats(self) -> None:
        self.draft.gather()
        assert self.draft.raw is not None
        self.draft.raw = replace(self.draft.raw, summary=SUMMARY)
        self.draft.reassemble()

    def test_the_summary_keeps_the_repeated_errors_while_the_log_is_whole(self) -> None:
        self._with_repeats()
        assert self.draft.made is not None

        self.assertIn("3x** ModuleNotFoundError", self.draft.made.summary)

    def test_a_log_left_out_takes_the_repeated_errors_from_the_summary(self) -> None:
        self._with_repeats()
        self.draft.leave_out(package.LOG, True)

        summary = self.draft.reassemble().summary

        self.assertNotIn("ModuleNotFoundError", summary)
        self.assertNotIn("something rare", summary)
        self.assertIn("**Errors:** 4", summary)

    def test_a_line_taken_from_the_log_takes_them_too(self) -> None:
        self._with_repeats()
        self.draft.remove_line(package.LOG, 1)

        self.assertNotIn("ModuleNotFoundError", self.draft.reassemble().summary)

    def test_a_line_taken_from_another_file_leaves_them(self) -> None:
        self._with_repeats()
        self.draft.remove_line(package.PREVIOUS_LOG, 0)

        self.assertIn("ModuleNotFoundError", self.draft.reassemble().summary)

    def test_a_choice_marks_the_package_stale_and_refresh_builds_it_once(self) -> None:
        self.draft.gather()
        self.assertFalse(self.draft.stale)

        self.draft.hide_word("opened")
        self.assertTrue(self.draft.stale)
        with patch("common.support.draft.package.assemble",
                   wraps=package.assemble) as assembled:
            self.draft.refresh()
            self.draft.refresh()

        self.assertEqual(assembled.call_count, 1)
        self.assertFalse(self.draft.stale)
        text = self.draft.made.part(package.LOG) if self.draft.made else None
        assert text is not None
        self.assertNotIn("opened", text.text)

    def test_hiding_tables_marks_the_package_stale(self) -> None:
        self.draft.gather()

        self.draft.set_hide_tables(True)

        self.assertTrue(self.draft.stale)
        self.assertTrue(self.draft.options().hide_tables)

    def test_a_blank_name_is_not_left_out_or_removed(self) -> None:
        self.draft.gather()

        self.draft.leave_out("", True)
        self.draft.remove_line("", 0)

        self.assertEqual(self.draft.left_out, [])
        self.assertEqual(self.draft.removed, [])
        self.assertFalse(self.draft.stale)

    def test_dropping_it_forgets_the_words_asked_to_be_hidden(self) -> None:
        self.draft.gather()
        self.draft.hide_word("opened")

        self.draft.drop()

        self.assertEqual(self.draft.hide, [])


class PlaceholderKindTests(unittest.TestCase):
    def test_a_slot_names_its_kind(self) -> None:
        self.assertEqual(redact.kind_of("[[host-3]]"), redact.HOST)
        self.assertEqual(redact.kind_of("[[player-1]]"), redact.PERSON)
        self.assertEqual(redact.kind_of("[[table-12]]"), redact.TABLE)

    def test_a_plain_marker_names_its_kind(self) -> None:
        self.assertEqual(redact.kind_of("[[secret]]"), redact.SECRET)

    def test_ordinary_text_is_no_kind(self) -> None:
        self.assertIsNone(redact.kind_of("host-3"))
        self.assertIsNone(redact.kind_of("[[host-x]]"))
        self.assertIsNone(redact.kind_of("[[nothing-1]]"))

    def test_a_line_is_cut_where_something_was_replaced(self) -> None:
        found = pieces.line_pieces("at [[host-1]] now", {"[[host-1]]": "cabinet"})

        self.assertEqual([one.text for one in found], ["at ", "[[host-1]]", " now"])
        self.assertEqual([one.kind for one in found], [None, redact.HOST, None])
        self.assertEqual(found[1].original, "cabinet")


class SearchTests(_Folder):
    def test_a_search_finds_a_line_in_any_file_and_is_capped(self) -> None:
        self.write_logs()
        made = package.build_package(package.Options())

        found = pieces.matching(made, "SIGN-IN CODE")
        self.assertEqual([name for name, _ in found], [package.LOG])
        self.assertEqual(pieces.matching(made, " "), [])
        self.assertEqual(len(pieces.matching(made, "e", limit=3)), 3)

    def test_a_replaced_value_is_never_a_search_hit_for_its_original(self) -> None:
        self.write_logs()
        made = package.build_package(package.Options(hide_tables=True))

        self.assertEqual(pieces.matching(made, SENTINELS["table"]), [])


class DiscordTests(unittest.TestCase):
    def test_it_says_what_runs_where_and_what_repeats(self) -> None:
        text = destinations.discord_summary(SUMMARY)

        self.assertIn("VPinFE 3.0.0 (Full) on Linux 6.8, Wayland", text)
        self.assertIn("Screens found: 1", text)
        self.assertIn("- 3x ModuleNotFoundError", text)
        self.assertIn("4 errors, 2 warnings", text)

    def test_it_never_goes_past_the_limit(self) -> None:
        many = SUMMARY + "".join(f"- **{n}x** " + "long " * 80 + "\n" for n in range(2, 40))

        text = destinations.discord_summary(many)

        self.assertLessEqual(len(text), destinations.DISCORD_LIMIT)
        self.assertTrue(text.endswith("attached."))

    def test_a_summary_with_nothing_in_it_still_makes_a_post(self) -> None:
        self.assertLessEqual(len(destinations.discord_summary("")), destinations.DISCORD_LIMIT)


class GitHubTests(unittest.TestCase):
    def test_only_the_named_facts_go_in_the_link(self) -> None:
        self.assertEqual(destinations.github_fields(SUMMARY), {
            "version": "3.0.0", "build": "Full", "os": "Linux 6.8", "session": "Wayland"})

    def test_a_fact_outside_the_list_is_not_in_the_link(self) -> None:
        link = destinations.github_link(SUMMARY)

        self.assertNotIn("Pat", link)
        self.assertNotIn("Example", link)
        self.assertIn("template=support.yml", link)

    def test_a_runaway_value_falls_back_to_the_bare_template(self) -> None:
        link = destinations.github_link(SUMMARY.replace("Linux 6.8", "x" * 3000))

        self.assertEqual(link, f"{destinations.GITHUB_NEW_ISSUE}?template=support.yml")


if __name__ == "__main__":
    unittest.main()
