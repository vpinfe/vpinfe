"""What a rail badge says, for an entry and for the group holding it.

Pure: the shell draws what these return. Trouble is something already broken and draws
red; a warm mark is worth doing when you get to it. An entry that has both shows the
trouble.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Mark:
    count: int
    tooltip: str
    trouble: bool = False


def trouble_mark(items: Iterable[Any]) -> Mark | None:
    """Red, a count of the problems and their reasons once each. One setting two features
    both need is one problem: the count is what the person has to fix."""
    found = list(items)
    if not found:
        return None
    problems = {(one.section, one.key, one.reason) for one in found}
    return Mark(len(problems),
                " ".join(dict.fromkeys(one.reason for one in found if one.reason)),
                trouble=True)


def entry_mark(trouble: Iterable[Any], warm: Mark | None) -> Mark | None:
    return trouble_mark(trouble) or warm


def group_mark(marks: Iterable[Mark | None]) -> Mark | None:
    """A closed group says the most urgent thing under it: the trouble, counted over
    every entry that has some, else the warm marks summed with their tooltips joined."""
    held = [one for one in marks if one is not None]
    for trouble in (True, False):
        of_kind = [one for one in held if one.trouble is trouble]
        if of_kind:
            return Mark(sum(one.count for one in of_kind),
                        ("; " if not trouble else " ").join(
                            dict.fromkeys(one.tooltip for one in of_kind if one.tooltip)),
                        trouble=trouble)
    return None
