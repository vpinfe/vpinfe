"""Tracked code does not point at a document only one machine can read.

The design notes live outside the clone and are not published. A reference to one tells a
reader something exists, names it, and then cannot show it to them - which is worse than
saying nothing, because the reason is now somewhere they cannot go.

A PreToolUse hook already refuses a write that adds one. It checks what a call adds, so it
cannot see what predates it: two survived in `tests/` for weeks until a sweep found them.
This is the half that looks at what is already here.

It reads each file as one run of text rather than line by line. A pointer that wraps -
"(MEDIA decisions" ending a line and "6-8)" starting the next - is invisible to a check
that matches within a line, and is the same pointer.

The rule, in `docs/conventions.md`: state the reason instead. If the reason is too long to
state, it belongs in the design note alone and the code says nothing.
"""

from __future__ import annotations

import pathlib
import re
import subprocess
import unittest
from collections.abc import Callable

REPO = pathlib.Path(__file__).resolve().parent.parent.parent

# Assembled rather than written out, for the same reason `test_no_private_library_data.py`
# assembles its own: a rule against naming these cannot name one to test itself.
SECTION_MARK = chr(0xA7)
PRIVATE_DOC = re.compile(r"[A-Za-z0-9_-]+\." + "local" + r"\.md")

# The other spelling, and the one that got past the first version of this: the note
# named in caps with one of its section numbers, as in "<NOTE> 2.11". It reads like a
# citation and points at the same unpublished file.
CITED_SECTION = re.compile(r"\b([A-Z][A-Z0-9-]{3,})[ -][0-9]+\.[0-9]+")

# The spelling that needs no note name at all, and the one every offender in the first
# sweep of this used: the bare word and a number, as in "Section 14.2" or "decision 15".
# A reader outside this machine has nothing to open either way.
NUMBERED_SECTION = re.compile(r"\b[Ss]ections?\s+[0-9]|"
                              r"\b[Dd]ecisions?\s+[0-9]|"
                              r"\b[Nn]otes?\s+in\s+[0-9]")

DECISION_RECORD = re.compile(r"\badr[- ]?[0-9]|\badr/", re.I)

COMMENT_LEAD = re.compile(r"^(?:#+|//+)\s*")

# Published standards read the same way and are the opposite case: a reader can open
# them. The check is about a pointer nobody outside this machine can follow.
PUBLIC_STANDARDS = frozenset({"WCAG", "ARIA", "HTML", "HTTP", "RFC", "ISO", "IEEE",
                              "ECMA", "UNICODE", "POSIX", "SEMVER", "JSON"})

# Only what git holds. An untracked note beside the code is nobody's business but this
# machine's, and the design documents themselves are exactly that.
SUFFIXES = {".py", ".md", ".js", ".toml", ".yml", ".yaml", ".spec", ".json"}

# The public docs may cite their own numbered sections; that is a reader following a link
# they can actually open. Only a pointer *out* of the tree is the problem.
ALLOWED = {
    "docs/conventions.md",
    # This file has to spell the shapes out to test itself.
    "tests/invariants/test_no_private_doc_pointers.py",
}


def _tracked() -> list[pathlib.Path]:
    found = subprocess.run(["git", "-C", str(REPO), "ls-files", "-z"],
                           capture_output=True, text=True, check=True)
    return [REPO / one for one in found.stdout.split("\0") if one]


def _flattened(text: str) -> tuple[str, Callable[[int], int]]:
    """The file as one run of text, and a way back to the line an offset fell on.

    Runs of whitespace collapse to a single space, so a pointer broken over two lines
    reads as one, and a comment marker starting a continuation line goes with them - a
    wrapped pointer is as common in a comment block as in a docstring. The map back is
    what keeps the report a line number somebody can open.
    """
    flat: list[str] = []
    lines: list[int] = []
    for number, line in enumerate(text.splitlines(), 1):
        if flat:
            flat.append(" ")
            lines.append(number)
        for char in COMMENT_LEAD.sub("", line.strip()):
            flat.append(char)
            lines.append(number)

    def line_of(offset: int) -> int:
        return lines[min(offset, len(lines) - 1)] if lines else 1

    return "".join(flat), line_of


def _offenders() -> list[str]:
    out = []
    for path in _tracked():
        if path.suffix.lower() not in SUFFIXES:
            continue
        relative = path.relative_to(REPO).as_posix()
        if relative in ALLOWED:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        flat, line_of = _flattened(text)
        if SECTION_MARK in flat:
            out.append(f"{relative}:{line_of(flat.index(SECTION_MARK))}: "
                       "names a section of a document")
        for found in PRIVATE_DOC.finditer(flat):
            out.append(f"{relative}:{line_of(found.start())}: "
                       "names a note that is not published")
        for found in CITED_SECTION.finditer(flat):
            if found.group(1) not in PUBLIC_STANDARDS:
                out.append(f"{relative}:{line_of(found.start())}: "
                           "cites a section of an unpublished note")
        for found in NUMBERED_SECTION.finditer(flat):
            out.append(f"{relative}:{line_of(found.start())}: "
                       "cites a section of an unpublished note")
        for found in DECISION_RECORD.finditer(flat):
            out.append(f"{relative}:{line_of(found.start())}: "
                       "cites a decision record; say the conclusion instead")
    return out


class PrivateDocPointerTests(unittest.TestCase):
    @unittest.skipIf(not (REPO / ".git").exists(), "not a git checkout")
    def test_nothing_tracked_points_at_an_unpublished_note(self) -> None:
        """Say the reason. A pointer a reader cannot follow is worse than silence."""
        self.assertEqual(_offenders(), [], "\n".join([
            "These name a document, or a section of one, that is not in the tree.",
            "State the reason instead; if it is too long to state, leave it out.",
            *_offenders()]))

    def test_the_allowlist_only_names_files_that_exist(self) -> None:
        """A stale exemption silently widens the check."""
        gone = sorted(one for one in ALLOWED if not (REPO / one).exists())
        self.assertEqual(gone, [])

    def test_the_checker_can_actually_fail(self) -> None:
        """Both halves, against text written to break them."""
        self.assertIn(SECTION_MARK, f"see {SECTION_MARK}9 of the design")
        self.assertTrue(PRIVATE_DOC.search("read PLAYBACK." + "local" + ".md first"))
        self.assertFalse(PRIVATE_DOC.search("read docs/conventions.md first"))
        self.assertTrue(CITED_SECTION.search("the two are combinable (NOTES 2.11)"))
        self.assertFalse(CITED_SECTION.search("VPX 10.8.1 is the supported floor"))
        standard = CITED_SECTION.search("WCAG 1.4.11 puts a floor under it")
        self.assertIsNotNone(standard)
        assert standard is not None
        self.assertIn(standard.group(1), PUBLIC_STANDARDS)
        for spelling in ("see ADR-0008", "see ADR 0008", "adr/0008-a-title",
                         "docs/adr/0008", "adr0008"):
            self.assertTrue(DECISION_RECORD.search(spelling), spelling)
        for innocent in ("the quadrant is 0 based", "a padre", "address 0.0.0.0"):
            self.assertFalse(DECISION_RECORD.search(innocent), innocent)

        self.assertTrue(NUMBERED_SECTION.search("Section 14.2's order says so"))
        self.assertTrue(NUMBERED_SECTION.search("offered disabled - decision 15's rule"))
        self.assertTrue(NUMBERED_SECTION.search("see the note in 5.4a"))
        self.assertFalse(NUMBERED_SECTION.search("the settings section of the file"))

    def test_a_pointer_broken_over_two_lines_is_still_found(self) -> None:
        """The shape that got past the line-by-line version of this."""
        flat, line_of = _flattened("x = 1\n# ...plain default (MEDIA decisions\n# 6-8)\n")
        self.assertTrue(NUMBERED_SECTION.search(flat))
        self.assertEqual(line_of(flat.index("decisions")), 2)
