"""Which names a table's script mentions.

A table that saves something - a text file of scores, a set in VPReg - says so in its
script, as a string. This answers which of a list of names a script holds, so a file in a
shared folder can be matched to the tables that use it.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from common.games.formats.vpx_parser import VPXParser

_STRING = re.compile(r'"((?:[^"\n]|"")*)"')


def strings_in(script: str) -> str:
    """Every string literal outside a comment, lowercased and joined by line breaks."""
    code = VPXParser.strip_vbscript_comments(script or "")
    return "\n".join(one.replace('""', '"') for one in _STRING.findall(code)).casefold()


def named_in(script: str, names: Iterable[str]) -> set[str]:
    """The names the script's strings hold, as whole words and in any case.

    A name is a file's name with or without its extension, or a VPReg set. Whole words,
    so `scores` is not found in `highscores`.
    """
    return named_in_strings(strings_in(script), names)


def named_in_strings(held: str, names: Iterable[str]) -> set[str]:
    """`named_in` for a script already reduced by `strings_in`, so the script is read
    once for any number of questions."""
    if not held:
        return set()
    found = set()
    for name in names:
        wanted = str(name or "").strip().casefold()
        if not wanted or wanted not in held:
            continue
        if re.search(rf"(?<![a-z0-9]){re.escape(wanted)}(?![a-z0-9])", held):
            found.add(name)
    return found
