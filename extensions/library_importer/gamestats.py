"""What a PinballY library remembers about playing.

A play count and a last-played date are the part of a library somebody actually misses
when they move machines: a collection that sorts by either is wrong on arrival without
them, and nobody can reconstruct them by hand.

`GameStats.csv` sits beside the frontend and is UTF-16 with a BOM, which is the same
trap its ini carries - read as UTF-8 it fails outright. Every column is text and most
are empty, so a blank is the normal case rather than a malformed row.
"""

from __future__ import annotations

import codecs
import csv
import io
import logging
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from common.extensions.contract import words

from .source import Note, failed

logger = logging.getLogger(__name__)
t = words("library_importer")

STATS_FILE = "GameStats.csv"

# `Game` is "<display name>.<system>" - the same display name the artwork is filed
# under, not the table's filename. The suffix is how one name is told from another
# system's, and is dropped once the system is known.
KEY = "Game"
LAST_PLAYED = "Last Played"
PLAY_COUNT = "Play Count"
PLAY_TIME = "Play Time"
FAVORITE = "Is Favorite"
RATING = "Rating"
CATEGORIES = "Categories"

MARKS = ((codecs.BOM_UTF8, "utf-8-sig"), (codecs.BOM_UTF16_LE, "utf-16"),
         (codecs.BOM_UTF16_BE, "utf-16"))
UNMARKED = ("utf-8", "utf-16-le", "utf-16-be")


@dataclass(frozen=True)
class Played:
    """What one game's row remembers. Absent is None, which is not zero - a game nobody
    played and a game whose count was never written are different, and only one of them
    should overwrite what is already here."""

    name: str
    system: str
    play_count: int | None = None
    play_time_seconds: int | None = None
    last_played: int | None = None
    favorite: bool | None = None
    rating: int | None = None
    tags: tuple[str, ...] = ()

    @property
    def remembers_anything(self) -> bool:
        return any(one is not None for one in
                   (self.play_count, self.play_time_seconds, self.last_played,
                    self.favorite, self.rating)) or bool(self.tags)

    @property
    def has_play_record(self) -> bool:
        return any(one is not None for one in
                   (self.play_count, self.play_time_seconds, self.last_played))

    @property
    def counts_as_history(self) -> bool:
        return self.has_play_record or bool(self.tags)


def path_for(root: Path | str) -> Path:
    return Path(root) / STATS_FILE


def read(path: Path | str) -> tuple[list[Played], list[Note]]:
    """Every row that remembers something, and anything worth saying about the read."""
    path = Path(path)
    if not path.is_file():
        return [], []
    try:
        raw = path.read_bytes()
    except OSError as exc:
        logger.warning("Could not read %s: %s", path, exc)
        return [], [failed(t("note.unreadable", file=path.name), exc)]

    text, note = _text(raw, path.name)
    notes = [note] if note else []
    found = []
    try:
        for row in csv.DictReader(io.StringIO(text)):
            one = _played(row)
            if one is not None and one.remembers_anything:
                found.append(one)
    except csv.Error as exc:
        notes.append(failed(t("note.read_stopped", file=path.name), exc))
    return found, notes


def _text(raw: bytes, name: str) -> tuple[str, Note]:
    """The file as text, and a note when some of it may be wrong."""
    marked = next((encoding for mark, encoding in MARKS if raw.startswith(mark)), "")
    if marked:
        try:
            return raw.decode(marked), ""
        except UnicodeDecodeError:
            return raw.decode(marked, "replace"), t("note.bytes_lost", file=name)
    for encoding in UNMARKED:
        try:
            text = raw.decode(encoding)
            header = next(csv.reader(io.StringIO(text)), [])
        except (UnicodeDecodeError, csv.Error):
            continue
        if KEY in header:
            return text, ""
    try:
        return raw.decode("utf-8"), ""
    except UnicodeDecodeError:
        return raw.decode("cp1252", "replace"), t("note.bytes_lost", file=name)


def _played(row: dict) -> Played | None:
    said = str(row.get(KEY) or "").strip()
    if not said:
        return None
    # Split from the right: a game's own name holds dots far more often than a system's
    # does - "1.2.3... (Talleres 1973).Visual Pinball X" is one real row.
    name, _, system = said.rpartition(".")
    if not name:
        name, system = said, ""
    return Played(
        name=name,
        system=system,
        play_count=_number(row.get(PLAY_COUNT)),
        play_time_seconds=_number(row.get(PLAY_TIME)),
        last_played=_when(row.get(LAST_PLAYED)),
        favorite=_flag(row.get(FAVORITE)),
        rating=stars(row.get(RATING)),
        tags=_tags(row.get(CATEGORIES)),
    )


def _number(value: Any) -> int | None:
    said = str(value or "").strip()
    if not said:
        return None
    try:
        return int(float(said))
    except ValueError:
        return None


def stars(value: Any) -> int | None:
    """A rating in whole stars, 0-5, the scale the sources use. A game is rated in half
    stars, so a caller doubles it.

    A half star is rounded up rather than dropped, so 3.5 arrives as 4. Python's own
    round() goes to the even neighbour, which would send 2.5 to 2 and 3.5 to 4.
    """
    said = str(value or "").strip()
    if not said:
        return None
    try:
        number = float(said)
    except ValueError:
        return None
    if not math.isfinite(number):
        return None
    return max(0, min(5, math.floor(number + 0.5)))


def _flag(value: Any) -> bool | None:
    said = str(value or "").strip().lower()
    if not said:
        return None
    return said not in ("0", "no", "false")


def _tags(value: Any) -> tuple[str, ...]:
    said = str(value or "").strip()
    if not said:
        return ()
    return tuple(one.strip() for one in said.split(",") if one.strip())


def _when(value: Any) -> int | None:
    """`20221211012447` as epoch seconds.

    Read as local time, because that is what the frontend wrote: it recorded when
    somebody was standing there, with no zone beside it, and reading it as UTC would
    move every date by however far the machine is from Greenwich.
    """
    said = str(value or "").strip()
    if len(said) != 14 or not said.isdigit():
        return None
    try:
        when = datetime.strptime(said, "%Y%m%d%H%M%S")
    except ValueError:
        return None
    return int(when.astimezone(UTC).timestamp())
