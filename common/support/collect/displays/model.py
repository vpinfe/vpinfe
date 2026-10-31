"""The shapes every display probe returns, so the section can compare any desktop's answer
with VPinFE's and with Visual Pinball's."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from common.capture.geometry import NONE, Turn

OK = "ok"
NOT_HERE = "not_here"
FAILED = "failed"
TIMED_OUT = "timed_out"

# The lines of an unreadable answer kept for a maintainer to fix the parser by.
UNREAD_LINES = 20
UNREAD_WIDTH = 2000

# What names a monitor's own unit, in JSON and in a printed Python value alike.
_IDENTITY = re.compile(
    r"""(?P<key>(?P<quote>["']?)\b(?:serial|description|make|vendor|manufacturer|edid|uuid)"""
    r"""(?P=quote))(?P<sep>\s*[:=]\s*)"""
    r"""(?P<value>"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|[^\s,}\]]+)""",
    re.IGNORECASE)
_UUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
                   re.IGNORECASE)


def without_identity(text: str) -> str:
    """`text` with the serial, description, maker and uuid of each monitor replaced, so an
    answer kept to fix a parser by names no unit."""
    return _UUID.sub("-", _IDENTITY.sub(r'\g<key>\g<sep>"-"', text))


@dataclass(frozen=True)
class Seen:
    """One screen as VPinFE sees it, numbered as the config numbers them. Satisfies the
    `Seen` shape Visual Pinball's comparison reads."""

    number: int
    name: str
    x: int
    y: int
    width: int
    height: int


@dataclass(frozen=True)
class ScreenFact:
    """One screen as the desktop says it is. Every field but `source` may be unknown."""

    connector: str | None = None
    model: str | None = None
    x: int | None = None
    y: int | None = None
    width: int | None = None
    height: int | None = None
    scale: float | None = None
    refresh_hz: float | None = None
    # The turn the device applies to the screen, in `Turn`'s convention, and the
    # operating system's own word for it, which prints beside the degrees until the
    # direction of each word is checked against a really turned screen.
    turn: Turn | None = None
    said: str = ""
    primary: bool | None = None
    enabled: bool | None = None
    asleep: bool | None = None
    # The connector this screen shows a copy of.
    mirrors: str | None = None
    source: str = ""

    @property
    def placed(self) -> bool:
        return None not in (self.x, self.y, self.width, self.height)

    @property
    def shape(self) -> str | None:
        if self.width is None or self.height is None or self.width == self.height:
            return None
        return "portrait" if self.height > self.width else "landscape"

    @property
    def is_turned(self) -> bool:
        return self.turn is not None and self.turn.ccw != NONE.ccw


@dataclass(frozen=True)
class WindowFact:
    """A window of VPinFE's or of Visual Pinball's, and the output it is on."""

    name: str
    output: str


@dataclass(frozen=True)
class Reading:
    """What one probe found. A probe never raises: a program that is not installed is
    `not_here`, and a failure is `failed`, and either prints as one honest line."""

    source: str
    state: str = OK
    reason: str = ""
    screens: tuple[ScreenFact, ...] = ()
    windows: tuple[WindowFact, ...] = ()
    # Labelled facts about the session: a version, a window manager.
    facts: tuple[tuple[str, str], ...] = ()
    # Plain sentences the desktop answers, such as "Window placement here is its own".
    notes: tuple[str, ...] = ()
    # What the desktop printed when it could not be read, cut to the first lines.
    unread: tuple[str, ...] = field(default=())

    @classmethod
    def not_here(cls, program: str) -> Reading:
        return cls(program, NOT_HERE, program)

    @classmethod
    def failed(cls, source: str, reason: str, unread: str = "") -> Reading:
        lines = tuple(line[:UNREAD_WIDTH]
                      for line in without_identity(unread).splitlines()[:UNREAD_LINES])
        return cls(source, FAILED, reason, unread=lines)

    @classmethod
    def timed_out(cls, source: str) -> Reading:
        return cls(source, TIMED_OUT)

    @property
    def ok(self) -> bool:
        return self.state == OK


@dataclass(frozen=True)
class Finding:
    """One thing the comparison found, in a sentence a person can act on."""

    id: str
    problem: bool
    text: str
