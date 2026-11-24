"""A docstring states what the code does now, not what it did before or what proved it.

`docs/conventions.md` puts the argument, the alternatives and the measurements in the
commit message, which is where somebody looks when they ask why. The code gets the
conclusion.

The edit hook already quotes an over-long docstring back. It reads the lines a call adds,
so it sees a docstring once and never again: anything dismissed at that moment, or written
before the rule, is invisible from then on. That is how a residue builds up under a check
that reports clean. This is the half that reads what is already here.

Only the two halves a checker can be sure of. History and a measurement are objective -
either a sentence describes a shape that no longer exists, or it does not. The argument
behind a decision is the larger half of the rule and it stays a judgment, because a check
that fires on prose that is fine is one everybody learns to wave through.
"""

from __future__ import annotations

import ast
import pathlib
import re
import subprocess
import unittest

from tests.support import trees

REPO = pathlib.Path(__file__).resolve().parent.parent.parent
PACKAGES = ("apps", "common", "manager", "extensions", "frontend", "httpapi")

# Past-tense history. "used to" also means "employed for" - "cannot be used to ask
# whether a file exists" - and that sense always follows a form of `be`.
HISTORY = re.compile(
    r"(?<!\bbe )(?<!\bis )(?<!\bare )(?<!\bwas )(?<!\bwere )(?<!\bbeen )(?<!\bbeing )"
    r"\bused to\b"
    r"|\bpreviously\b|\boriginally\b|\buntil recently\b|\bwe changed\b",
    re.I)

# A number counted off a real library, and the phrase that introduces one. Three digits
# or more on the counted nouns: a library has hundreds of games, and "the first 5 entries"
# is a limit somebody chose rather than a number somebody counted.
MEASUREMENT = re.compile(
    r"\bmeasured (?:against|across|on|at)\b"
    r"|\b\d+ of \d+\b"
    r"|\b\d+(?:\.\d+)?%"
    r"|\b\d[\d,]{2,}\s*(?:tables|games|roms|files|folders|rows|requests|entries|releases)\b",
    re.I)

# A summary line may name what a value holds, and sometimes that is its own history: "the
# `joy*` key this action used to have" is the definition of the thing being returned, not
# a note about a change. Only the body argues.
#
# Each exemption carries its reason. A count does not - it goes stale and gets believed.
EXEMPT = {
    ("common/games/library_vps_state.py", "compute"):
        "'measured against its own baseline' is what the code does, not a measurement",
    ("manager/library/vps_match.py", "ask"):
        "`place` is the literal format '3 of 34', which is the contract",
    ("frontend/input_api.py", "set_button_mapping"):
        "the old key names are still accepted, so naming them is a live contract",
}


def _body(doc: str) -> str:
    """Everything after the summary. The summary ends at the first blank line."""
    lines = doc.strip().splitlines()
    cut = next((i for i, line in enumerate(lines) if not line.strip()), len(lines))
    return "\n".join(lines[cut:])


def _modules() -> list[pathlib.Path]:
    listed = subprocess.run(["git", "-C", str(REPO), "ls-files", "-z", "*.py"],
                            capture_output=True, text=True, check=True)
    return [REPO / one for one in listed.stdout.split("\0")
            if one and one.split("/")[0] in PACKAGES]


def _offenders() -> list[str]:
    out = []
    for path in _modules():
        relative = path.relative_to(REPO).as_posix()
        try:
            tree = trees.tree_for(path)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Module | ast.ClassDef
                              | ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            doc = ast.get_docstring(node, clean=False)
            if not doc:
                continue
            name = getattr(node, "name", "<module>")
            if (relative, name) in EXEMPT:
                continue
            body = _body(doc)
            for label, pattern in (("history", HISTORY), ("a measurement", MEASUREMENT)):
                found = pattern.search(body)
                if found:
                    line = node.body[0].lineno
                    out.append(f"{relative}:{line} {name}(): {label} - "
                               f"{found.group(0).strip()!r}")
    return sorted(out)


class DocstringsCarryTheConclusionTests(unittest.TestCase):
    @unittest.skipIf(not (REPO / ".git").exists(), "not a git checkout")
    def test_no_docstring_argues_from_history_or_a_measurement(self) -> None:
        """What changed and what proved it belong in the commit that changed it."""
        found = _offenders()
        self.assertEqual(found, [], "\n".join([
            "These docstrings say what the code did before, or what was counted to",
            "settle it. Both belong in the commit message; the docstring keeps the",
            "conclusion. Where the phrase is the contract, exempt it with its reason.",
            *found]))

    def test_every_exemption_still_names_something_that_exists(self) -> None:
        """A stale exemption widens the check without saying so."""
        wrong = []
        for relative, name in sorted(EXEMPT):
            path = REPO / relative
            if not path.exists():
                wrong.append(f"{relative}: gone")
                continue
            tree = trees.tree_for(path)
            names = {getattr(n, "name", "<module>") for n in ast.walk(tree)}
            if name not in names:
                wrong.append(f"{relative}: no {name}()")
        self.assertEqual(wrong, [])

    def test_the_checker_can_actually_fail(self) -> None:
        """The distinctions it turns on, against text written to break them."""
        # History in the body is the defect; the same words in a summary may be the
        # definition of what a value holds.
        self.assertTrue(HISTORY.search(_body("Does a thing.\n\nThis used to raise.")))
        self.assertFalse(HISTORY.search(_body("The key this action used to have.")))
        # "employed for" is not history.
        self.assertFalse(HISTORY.search("this cannot be used to ask whether it exists"))
        self.assertTrue(HISTORY.search("the wheel used to sort entries"))
        # A count off somebody's library, and a number that is part of a contract.
        self.assertTrue(MEASUREMENT.search("measured across 162 real tables"))
        self.assertTrue(MEASUREMENT.search("697 of 702 tables had one"))
        self.assertTrue(MEASUREMENT.search("blank on 98% of them"))
        self.assertFalse(MEASUREMENT.search("returns the first 5 entries it finds"))
        self.assertTrue(MEASUREMENT.search("put 654 games against the folder"))
