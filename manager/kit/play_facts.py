"""How a play record reads: when it was last played, for how long, and the score."""

from __future__ import annotations

from typing import Any

from common import i18n
from common.i18n import t
from manager.kit import when


def played_when(stamp: str | None) -> str:
    """A date, not a timestamp. Nobody reads a play record to the second."""
    return when.ago(stamp) if when.parsed(stamp) else t("word.never")


def played_for(seconds: int) -> str:
    """Play time in the largest unit that is still true, because the number is read at
    a glance and 41,400 seconds is not a length anybody pictures."""
    if seconds < 60:
        return t("word.none") if not seconds \
            else t("manager.workbench.sec", count=seconds)
    minutes = seconds // 60
    if minutes < 90:
        return t("manager.workbench.min", count=minutes)
    hours, rest = divmod(minutes, 60)
    return (t("manager.workbench.hr_min", hours=(hours), rest=(rest)) if rest
            else t("manager.workbench.hr", count=hours))


def score_lines(entry: dict[str, Any]) -> list[str]:
    """A number the machine writes its own way - hex - is left as `text` has it."""
    score, text = entry.get("score"), str(entry.get("text") or "")
    prefix, suffix = str(entry.get("prefix") or ""), str(entry.get("suffix") or "")
    if isinstance(score, int) and text == f"{prefix}{score:,}{suffix}":
        return [f"{prefix}{i18n.number(score)}{suffix}"]
    return text.split("\n") or [""]


def score_cells(entry: dict[str, Any]) -> tuple[str, str, list[str]]:
    """An entry's score as (the number with its prefix, its unit in lower case, the lines
    after the first). The unit is the machine's suffix, which it writes in its own case."""
    first, *rest = score_lines(entry)
    suffix = str(entry.get("suffix") or "")
    if suffix and first.endswith(suffix):
        return first[:-len(suffix)], suffix.strip().lower(), rest
    return first, "", rest
