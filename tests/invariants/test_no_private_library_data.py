"""Nothing from anybody's own library is in the public tree.

No library asset and no library metadata belongs in the public repo. This tree is
public and the machines it gets developed against are not. A real
frontend install is the best possible test corpus and the worst possible thing to commit
from, and the two facts pull in opposite directions every time somebody works from one.

So the rule is that **format facts cross and data does not**. Key names, encodings,
layering rules, the casing traps - those are facts about PinballY and VPinMAME, and they
belong here. A table list, a rating, a play count, a high score, a media file, a path off
somebody's drive: none of it, in any form, including trimmed or anonymized.

Checked rather than remembered, because the failure is silent. A fixture pasted from a
real install looks exactly like a fixture somebody wrote, and by the time anyone notices
it is in the history.
"""

from __future__ import annotations

import pathlib
import re
import subprocess
import unittest

REPO = pathlib.Path(__file__).resolve().parents[2]

# A media fixture stands in for a file; it is never the file. Seven bytes of ASCII is
# what the importer fixtures hold. The cap is generous enough that a legitimately small
# real asset would still trip it - a 1x1 PNG is 68 bytes and no test needs one.
PLACEHOLDER_BYTES = 64
ASSET_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp",
                  ".mp4", ".mkv", ".avi", ".webm", ".f4v",
                  ".mp3", ".wav", ".ogg", ".flac",
                  ".vpx", ".fpt", ".directb2s", ".stg", ".nv", ".zip"}

# The asset rule is scoped to tests/, because that is where the leak comes from. This
# project ships plenty of real images of its own - a DMD graphic, installer artwork - and
# a rule broad enough to cover them would need an exception list long enough to hide the
# next real one in.
FIXTURES = "tests/"

# Structural, and it names no private host: a rule listing somebody's machines has to
# spell them out to test itself, which puts the strings it protects into the public tree.
# The stand-ins a test actually writes are the only volumes let through.
PRIVATE = re.compile(
    r"""(?xi)
    /Volumes/(?!(?:share|source|library|tables|media)\b)[A-Za-z0-9_-]+
    | /Users/(?!\.{3}/)[a-z0-9_.-]+/       # a home directory; `/Users/.../` is elided
    | /home/(?!(?:\.{3}|cab|pi|player|someone|user)/)[a-z0-9_.-]+/
                                          # the same on Linux, which is what a cabinet
                                          # runs. The personas a test writes go through,
                                          # exactly as the volume stand-ins above do.
    | \\\\[A-Za-z0-9_-]+\\[A-Za-z0-9_$-]+  # a UNC path to a named host
    """)


# README.md shows a terminal transcript of a real table folder, and the person it names is
# the project owner publishing his own username in his own project. That is authorship, not
# somebody's private data leaking, so it is the one file this does not read.
PUBLISHED_BY_THE_PERSON_IT_NAMES = {"README.md"}


def _tracked() -> list[pathlib.Path]:
    """What git holds, which is the only thing that can leak. The working tree is not
    the subject: an untracked scratch file is nobody's business but this machine's."""
    found = subprocess.run(["git", "-C", str(REPO), "ls-files", "-z"],
                           capture_output=True, text=True, check=True)
    return [REPO / one for one in found.stdout.split("\0") if one]


def _fixture(path: pathlib.Path) -> bool:
    return path.relative_to(REPO).as_posix().startswith(FIXTURES)


class AssetTests(unittest.TestCase):
    @unittest.skipIf(not (REPO / ".git").exists(), "not a git checkout")
    def test_no_committed_media_is_a_real_file(self) -> None:
        """Every media fixture is a stand-in, not somebody's artwork."""
        too_big = []
        for path in _tracked():
            if path.suffix.lower() not in ASSET_SUFFIXES or not _fixture(path):
                continue
            if not path.is_file():
                continue
            size = path.stat().st_size
            if size > PLACEHOLDER_BYTES:
                too_big.append(f"{path.relative_to(REPO).as_posix()} ({size} bytes)")
        self.assertEqual(too_big, [], "\n".join([
            "These look like real files rather than placeholders.",
            "A fixture names a file; it does not contain one.",
            *too_big]))


class IdentifierTests(unittest.TestCase):
    @unittest.skipIf(not (REPO / ".git").exists(), "not a git checkout")
    def test_no_tracked_file_names_a_private_machine_or_person(self) -> None:
        """A path off somebody's drive is metadata about them, even in a comment."""
        found = []
        for path in _tracked():
            if path.suffix.lower() in ASSET_SUFFIXES:
                continue
            if path.relative_to(REPO).as_posix() in PUBLISHED_BY_THE_PERSON_IT_NAMES:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for number, line in enumerate(text.splitlines(), 1):
                hit = PRIVATE.search(line)
                if hit:
                    found.append(f"{path.relative_to(REPO).as_posix()}:{number}: {hit.group(0)}")
        self.assertEqual(found, [], "\n".join([
            "These name one particular machine, drive or person.",
            "Say the shape instead: /Volumes/share, C:\\Visual Pinball\\tables.",
            *found]))


class TheLineTests(unittest.TestCase):
    """What the rule catches and what it deliberately does not.

    Pinned because the interesting half is the allowances: a guard that also fires on
    the generic stand-ins gets switched off, and then it guards nothing.
    """

    def test_it_catches_a_mounted_volume_home_directory_or_named_host(self) -> None:
        """Assembled rather than written out, so this file holds no line its own rule
        would flag. Spelled whole, the examples make the check fail on itself - and the
        fix for that is an exemption, which is a hole in the one file that must not
        have one."""
        for line in ("source = /Volumes/" + "SomebodysCab/Users",
                     "root = /Volumes/" + "Retro2019/Visual Pinball",
                     "path = /Users/" + "someone/Development/vpinfe",
                     "unc = " + "\\\\" + "somehost" + "\\pinball"):
            with self.subTest(line=line):
                self.assertTrue(PRIVATE.search(line), line)

    def test_it_allows_the_stand_ins_a_test_actually_writes(self) -> None:
        for line in (r"tablerootdir = C:\Visual Pinball\tables",
                     "drivemap.looks_foreign('/Volumes/share/Tables')",
                     "root = /Volumes/source/Tables",
                     "media = /Volumes/media/Wheel Images",
                     "# cropped '/Users/.../VPinballX/10.8/VPinballX.ini'"):
            with self.subTest(line=line):
                self.assertIsNone(PRIVATE.search(line), line)

    def test_a_placeholder_passes_and_a_real_file_does_not(self) -> None:
        """Seven bytes is a fixture. A 1x1 PNG is 68 and is still a real file."""
        self.assertLessEqual(len(b"placeholder"[:7]), PLACEHOLDER_BYTES)
        self.assertGreater(68, PLACEHOLDER_BYTES)


if __name__ == "__main__":
    unittest.main()
