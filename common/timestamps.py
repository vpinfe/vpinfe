"""One way to write a moment in time: ISO 8601, UTC, seconds precision - and how to get
there from what people and other programs actually wrote.

`User.LastRun` is the exception and stays epoch: it is a specced key that reaches the
VPinPlay API verbatim, so epoch_to_iso is here to read it.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from typing import Any

_MONTHS = {name.lower(): number for number, name in enumerate(
    ("January", "February", "March", "April", "May", "June",
     "July", "August", "September", "October", "November", "December"), start=1)}

_SEPARATED = re.compile(r"^(\d{1,4})[./_-](\d{1,4})(?:[./_-](\d{1,4}))?$")
_NAMED = re.compile(r"^(?:(\d{1,2})\s+)?([A-Za-z]+)\.?[\s,-]*(?:(\d{1,2})\s*,\s*)?(\d{4})$")
_ANY_YEAR = re.compile(r"\b(19\d{2}|20\d{2})\b")


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def epoch_to_iso(value: Any) -> str:
    """An epoch integer as ISO, or "" if it is not one. For reading fields written
    before this was the standard.

    A value that is already an ISO stamp comes back as one rather than as nothing. The
    spec says this field is an epoch and files in the wild disagree; reading one written
    as ISO as absent reports a game with a last-played date as having none, and refusing
    to read what is plainly there helps nobody.
    """
    try:
        stamp = datetime.fromtimestamp(int(value), UTC)
    except (TypeError, ValueError, OSError, OverflowError):
        said = str(value or "").strip()
        if not said:
            return ""
        try:
            parsed = datetime.fromisoformat(said.replace("Z", "+00:00"))
        except ValueError:
            return ""
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    return stamp.isoformat(timespec="seconds").replace("+00:00", "Z")


def iso_to_epoch(value: object) -> float | None:
    """An ISO stamp back as epoch seconds, or None if it is not one. The inverse of
    `epoch_to_iso`, for reading a time that crossed the wire back into something that
    sorts as a number."""
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def iso_from_asctime(value: object) -> str:
    """A C asctime stamp - `Tue Dec 13 16:03:21 2022` - as naive ISO. No Z: asctime
    carries no timezone, so claiming UTC would be inventing one."""
    try:
        return datetime.strptime(str(value).strip(), "%a %b %d %H:%M:%S %Y").isoformat()
    except (TypeError, ValueError):
        return ""


def _year_within(text: str) -> str:
    """The last resort: a year we can see in text we could not otherwise read."""
    found = _ANY_YEAR.search(text)
    return found.group(1) if found else ""


def _full_year(year: int) -> int:
    if year >= 100:
        return year
    return 2000 + year if year < 70 else 1900 + year


def _iso_day(year: int, month: int, day: int) -> str:
    """A full date, or the year alone when those numbers are not one."""
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return str(year)


def iso_from_authored_date(value: object) -> str:
    """An author-typed release date as ISO, at the precision the author gave.

    Both day-first and month-first orders turn up, so an ambiguous pair falls back to
    the year: 01/04/2017 is 2017, never a coin flip. Precision degrades, correctness
    does not.
    """
    text = str(value or "").strip()
    if not text:
        return ""
    if re.fullmatch(r"\d{4}", text):
        return text

    named = _NAMED.match(text)
    if named:
        leading_day, name, trailing_day, year = named.groups()
        month = _MONTHS.get(name.lower()) or _MONTHS.get(name.lower()[:3]) or next(
            (n for m, n in _MONTHS.items() if m.startswith(name.lower())), 0)
        if not month:
            return _year_within(text)
        day = leading_day or trailing_day
        return _iso_day(int(year), month, int(day)) if day else f"{year}-{month:02d}"

    parts = _SEPARATED.match(text)
    if not parts:
        return _year_within(text)
    first, second, third = parts.groups()

    if third is None:                       # a month and a year, either way round
        if len(first) == 4:
            return f"{first}-{int(second):02d}" if 1 <= int(second) <= 12 else first
        year = _full_year(int(second))
        return f"{year}-{int(first):02d}" if 1 <= int(first) <= 12 else str(year)

    if len(first) == 4:                     # already ISO, whatever the separator was
        return _iso_day(int(first), int(second), int(third))

    day, month, year = int(first), int(second), _full_year(int(third))
    if day == month:                        # both readings give the same date
        return _iso_day(year, month, day)
    if day > 12 and month <= 12:
        return _iso_day(year, month, day)
    if month > 12 and day <= 12:
        return _iso_day(year, day, month)   # month-first: the slots are swapped
    return str(year)                        # undecidable, or neither is a month
