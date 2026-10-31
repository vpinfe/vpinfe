"""What leaves in a support package is the bytes that were reviewed, and none of them name
the install.

The canary seeds one value of every kind the redactor knows into the summary and both logs,
then looks for each in every place a zip can carry text.
"""

from __future__ import annotations

import io
import re
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from common import i18n
from common.host.log_sources import Source
from common.support import package, redact
from common.support.redact import Identity
from common.support.sections import Section, SectionText

SENTINELS = {
    "home": "/home/someone",
    "login": "patex",
    "host": "cabinet-zed",
    "device": "Zed Display Wall",
    "domain": "zed.example.net",
    "player": "Pat Example",
    "identifier": "zedinstall0123456789abcdef",
    "library": "/home/someone/zedtables",
    "table": "Zedmoon Classic",
    "address": "10.77.66.55",
}

IDENTITY = Identity(
    homes=(SENTINELS["home"],), logins=(SENTINELS["login"],), hosts=(SENTINELS["host"],),
    devices=(SENTINELS["device"],), domains=(SENTINELS["domain"],),
    players=(SENTINELS["player"],), identifiers=(SENTINELS["identifier"],),
    libraries=(SENTINELS["library"],), tables=(SENTINELS["table"],))

RECORD = "2026-01-02 03:04:{second:02d} {level} [vpinfe.test] {message}\n"


def part_of(made: package.Package, name: str) -> package.Part:
    found = made.part(name)
    assert found is not None, name
    return found


def _every_sentinel() -> str:
    return " | ".join(f"{kind}: {value}" for kind, value in SENTINELS.items())


def _record(second: int, message: str, level: str = "INFO") -> str:
    return RECORD.format(second=second % 60, level=level, message=message)


def _seeded_section() -> Section:
    return Section("seeded", "Seeded", 1,
                   lambda: SectionText.ok([f"- {_every_sentinel()}"]))


class _Folder(unittest.TestCase):
    """A log folder of its own, and a package module that reads nothing else."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.current = self.root / "vpinfe.log"
        self.previous = self.root / "vpinfe.log.1"
        self.vpx = self.root / "vpinball.log"
        package._held.clear()
        self.addCleanup(package._held.clear)
        for target, value in (
                ("common.support.package.SECTIONS", (_seeded_section(),)),
                ("common.support.package.build_identity", lambda: IDENTITY),
                ("common.support.package.log_setup.log_file", lambda: self.current),
                ("common.support.package.log_sources.available", self._sources)):
            started = patch(target, value)
            started.start()
            self.addCleanup(started.stop)

    def _sources(self) -> list[Source]:
        found = [Source(one.name, one) for one in (self.current, self.previous)
                 if one.exists()]
        if self.vpx.exists():
            found.append(Source(self.vpx.name, self.vpx, re.compile(r"^\d{2}:\d{2}")))
        return found

    def write_logs(self) -> None:
        self.current.write_text(
            _record(1, f"opened {SENTINELS['library']}/x for {SENTINELS['player']}")
            + _record(2, f"{SENTINELS['host']} at {SENTINELS['address']} "
                         f"via {SENTINELS['domain']}")
            + _record(3, f"loaded {SENTINELS['table']} as {SENTINELS['identifier']}")
            + _record(4, "Sign-in code for 10.9.8.7: 123456", "WARNING"), encoding="utf-8")
        self.previous.write_text(
            _record(5, f"{SENTINELS['login']} on {SENTINELS['device']} "
                       f"in {SENTINELS['home']}/.config"), encoding="utf-8")
        self.vpx.write_text(f"00:01 opened {SENTINELS['library']}/y\n", encoding="utf-8")


class TheCanary(_Folder):
    """Every place a zip can carry text, for every kind of private value."""

    def setUp(self) -> None:
        super().setUp()
        self.write_logs()
        self.made = package.build_package(package.Options(hide_tables=True))

    def test_no_value_is_in_the_bytes_that_leave(self) -> None:
        for kind, value in SENTINELS.items():
            with self.subTest(kind=kind):
                self.assertNotIn(value.encode(), self.made.data)

    def test_no_value_is_in_any_file_once_unpacked(self) -> None:
        with zipfile.ZipFile(io.BytesIO(self.made.data)) as archive:
            for name in archive.namelist():
                text = archive.read(name).decode("utf-8")
                for kind, value in SENTINELS.items():
                    with self.subTest(file=name, kind=kind):
                        self.assertNotIn(value, text)
                        self.assertNotIn(value.casefold(), text.casefold())

    def test_no_value_is_in_a_name_a_comment_or_an_extra_field(self) -> None:
        with zipfile.ZipFile(io.BytesIO(self.made.data)) as archive:
            self.assertEqual(archive.comment, b"")
            for entry in archive.infolist():
                self.assertEqual((entry.comment, entry.extra), (b"", b""), entry.filename)
                for value in SENTINELS.values():
                    self.assertNotIn(value, entry.filename)

    def test_the_files_name_the_kind_that_was_replaced(self) -> None:
        text = part_of(self.made, package.LOG).text
        for placeholder in ("[[library-1]]", "[[player-1]]", "[[host-1]]", "[[table-1]]"):
            with self.subTest(placeholder=placeholder):
                self.assertIn(placeholder, text)

    def test_a_sign_in_code_that_reached_a_log_is_hidden(self) -> None:
        self.assertNotIn("123456", part_of(self.made, package.LOG).text)

    def test_what_a_person_reviews_never_carries_a_replaced_value(self) -> None:
        shown = repr(package.review(self.made))
        for kind, value in SENTINELS.items():
            with self.subTest(kind=kind):
                self.assertNotIn(value, shown)

    def test_the_package_does_not_serialize_its_originals(self) -> None:
        self.assertNotIn(SENTINELS["player"], repr(self.made))
        self.assertTrue(self.made.originals)


class TheZip(_Folder):
    def setUp(self) -> None:
        super().setUp()
        self.write_logs()
        self.made = package.build_package()

    def test_the_summary_is_in_english_whatever_language_is_set(self) -> None:
        worded = Section("worded", "Worded", 1,
                         lambda: SectionText.ok([i18n.t("word.unknown")]))
        with patch("common.support.package.SECTIONS", (worded,)), \
                patch.object(i18n, "_language", "qps"):
            made = package.build_package()
            self.assertNotEqual(i18n.t("word.unknown"), "Unknown")
        self.assertIn("## Worded\n\nUnknown\n", part_of(made, package.SUMMARY).text)

    def test_the_files_are_the_ones_listed_in_order(self) -> None:
        with zipfile.ZipFile(io.BytesIO(self.made.data)) as archive:
            self.assertEqual(
                archive.namelist(),
                [package.README, package.SUMMARY, package.LOG, package.VISUAL_PINBALL_LOG,
                 package.PREVIOUS_LOG])

    def test_it_reads_back_clean(self) -> None:
        with zipfile.ZipFile(io.BytesIO(self.made.data)) as archive:
            self.assertIsNone(archive.testzip())

    def test_a_second_reader_lists_the_same_files(self) -> None:
        target = self.root / self.made.filename
        target.write_bytes(self.made.data)
        listed = subprocess.run([sys.executable, "-I", "-m", "zipfile", "-l", str(target)],
                                capture_output=True, text=True, check=False)
        self.assertEqual(listed.returncode, 0, listed.stderr)
        for one in self.made.parts:
            self.assertIn(one.name, listed.stdout)

    def test_every_entry_has_the_same_fixed_time(self) -> None:
        with zipfile.ZipFile(io.BytesIO(self.made.data)) as archive:
            self.assertEqual({entry.date_time for entry in archive.infolist()},
                             {package.ENTRY_TIME})

    def test_the_same_content_is_the_same_bytes(self) -> None:
        again = package.build_package()
        self.assertEqual((again.checksum, again.data), (self.made.checksum, self.made.data))

    def test_the_name_says_the_version_and_the_minute(self) -> None:
        self.assertRegex(self.made.filename,
                         r"^vpinfe-support-[\w.+-]+-\d{8}-\d{4}\.zip$")

    def test_the_checksum_covers_the_files_but_the_readme(self) -> None:
        body = [one for one in self.made.parts if one.name != package.README]
        self.assertEqual(self.made.checksum, package._checksum(body))
        self.assertIn(self.made.checksum, part_of(self.made, package.README).text)

    def test_the_readme_counts_by_kind_and_says_cleanup_is_not_perfect(self) -> None:
        text = part_of(self.made, package.README).text
        self.assertIn("Automatic cleanup is thorough but not perfect. Read it before you send "
                      "it.", text)
        self.assertRegex(text, r"Private details replaced: [1-9]\d*")
        self.assertIn("player name", text)
        self.assertNotIn(SENTINELS["player"], text)

    def test_a_part_turned_off_is_not_in_it(self) -> None:
        made = package.build_package(package.Options(logs=False))
        self.assertEqual([one.name for one in made.parts], [package.README, package.SUMMARY])
        made = package.build_package(package.Options(previous_log=False))
        self.assertNotIn(package.PREVIOUS_LOG, [one.name for one in made.parts])

    def test_a_word_to_hide_is_hidden_everywhere(self) -> None:
        self.current.write_text(_record(1, "the word swordfish"), encoding="utf-8")
        made = package.build_package(package.Options(hide=("swordfish",)))
        for one in made.parts:
            self.assertNotIn("swordfish", one.text)

    def test_a_table_is_kept_unless_hiding_it_is_asked_for(self) -> None:
        kept = package.build_package(package.Options(hide_tables=False))
        self.assertIn(SENTINELS["table"], part_of(kept, package.LOG).text)
        hidden = package.build_package(package.Options(hide_tables=True))
        self.assertNotIn(SENTINELS["table"], part_of(hidden, package.LOG).text)

    def test_a_build_is_logged_by_kind_and_count_only(self) -> None:
        with self.assertLogs("vpinfe.common.support.package", "INFO") as seen:
            package.build_package()
        line = "\n".join(seen.output)
        self.assertIn("Support package built", line)
        for value in SENTINELS.values():
            self.assertNotIn(value, line)


class TheLogCaps(_Folder):
    def lines(self, count: int) -> list[str]:
        return [_record(n, f"line {n:04d} filler filler filler") for n in range(count)]

    def test_a_log_over_the_cap_keeps_its_start_and_its_end(self) -> None:
        self.current.write_text("".join(self.lines(600)), encoding="utf-8")
        with patch.object(package, "MAX_LOG_BYTES", 6000):
            made = package.build_package()
        part = part_of(made, package.LOG)
        self.assertGreater(part.omitted, 0)
        self.assertIn("line 0000", part.text)
        self.assertIn("line 0599", part.text)
        self.assertNotIn("line 0300", part.text)

    def test_the_marker_is_a_line_of_its_own_and_counts_what_is_gone(self) -> None:
        self.current.write_text("".join(self.lines(600)), encoding="utf-8")
        with patch.object(package, "MAX_LOG_BYTES", 6000):
            part = part_of(package.build_package(), package.LOG)
        marker = [one for one in part.text.splitlines() if "lines left out" in one]
        self.assertEqual(marker, [f"-- {part.omitted} lines left out --"])
        kept = len(re.findall(r"line \d{4}", part.text))
        self.assertEqual(kept + part.omitted, 600)

    def test_a_log_that_fits_is_whole(self) -> None:
        self.current.write_text("".join(self.lines(20)), encoding="utf-8")
        part = part_of(package.build_package(), package.LOG)
        self.assertEqual((part.omitted, part.lines), (0, 20))
        self.assertNotIn("left out", part.text)

    def test_nothing_is_read_once_the_total_is_spent(self) -> None:
        self.write_logs()
        with patch.object(package, "MAX_READ_BYTES", 0):
            made = package.build_package()
        self.assertEqual([one.name for one in made.parts], [package.README, package.SUMMARY])

    def test_a_package_over_its_cap_is_refused(self) -> None:
        self.write_logs()
        with patch.object(package, "MAX_BUILT_BYTES", 100), \
                self.assertRaises(package.PackageTooLargeError):
            package.build_package()

    def test_a_log_that_cannot_be_read_is_left_out_and_the_rest_is_kept(self) -> None:
        self.write_logs()
        real = package._read_log

        def fail_for_current(path: Path, budget: int) -> tuple[str, int, int]:
            if path == self.current:
                raise OSError("denied")
            return real(path, budget)

        with patch.object(package, "_read_log", fail_for_current), \
                self.assertLogs("vpinfe.common.support.package", "WARNING"):
            made = package.build_package()
        names = [one.name for one in made.parts]
        self.assertNotIn(package.LOG, names)
        self.assertIn(package.PREVIOUS_LOG, names)


class HoldingAPackage(_Folder):
    def setUp(self) -> None:
        super().setUp()
        self.write_logs()

    def test_it_is_found_by_its_id_until_it_expires(self) -> None:
        made = package.hold(package.build_package(), now=1000.0)
        self.assertIs(package.held(made.id, now=1000.0 + package.TTL_SECONDS - 1), made)
        self.assertIsNone(package.held(made.id, now=1000.0 + package.TTL_SECONDS))

    def test_the_id_is_random_and_long(self) -> None:
        first, second = package.build_package(), package.build_package()
        self.assertNotEqual(first.id, second.id)
        self.assertGreaterEqual(len(first.id), 32)

    def test_only_the_newest_two_are_held(self) -> None:
        made = [package.hold(package.build_package(), now=1.0) for _ in range(3)]
        self.assertIsNone(package.held(made[0].id, now=2.0))
        self.assertIs(package.held(made[1].id, now=2.0), made[1])
        self.assertIs(package.held(made[2].id, now=2.0), made[2])

    def test_an_expired_one_does_not_count_against_the_limit(self) -> None:
        old = package.hold(package.build_package(), now=1.0)
        later = 1.0 + package.TTL_SECONDS + 1
        fresh = [package.hold(package.build_package(), now=later) for _ in range(2)]
        self.assertIsNone(package.held(old.id, now=later))
        self.assertTrue(all(package.held(one.id, now=later) for one in fresh))

    def test_an_unknown_id_is_nothing(self) -> None:
        self.assertIsNone(package.held("nope"))

    def test_forgetting_drops_it(self) -> None:
        made = package.hold(package.build_package())
        package.forget(made.id)
        self.assertIsNone(package.held(made.id))

    def test_the_review_counts_down_to_the_expiry(self) -> None:
        made = package.hold(package.build_package(), now=10.0)
        self.assertEqual(package.review(made, now=10.0)["expires_in"], package.TTL_SECONDS)
        self.assertEqual(package.review(made, now=10.0 + package.TTL_SECONDS + 5)["expires_in"],
                         0)


class TheKinds(unittest.TestCase):
    def test_every_kind_the_redactor_counts_has_a_word_for_the_readme(self) -> None:
        self.assertEqual(set(package.KIND_LABELS), set(redact.KINDS))


if __name__ == "__main__":
    unittest.main()
