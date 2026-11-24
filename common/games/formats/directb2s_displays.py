"""The DMD placed in a `.directb2s` file: the `<DMD>` children of its `<Displays>` element.

Visual Pinball's B2S plugin keeps the DMD's place here, in pixels of the backglass art
counted from its top left, one `<DMD Target="Backglass|ScoreView" X Y W H .../>` per window.
Having the element is what turns the DMD on.

A file is megabytes of picture text, so nothing here parses it as a document. `read` looks
only at the `<Displays>` element, and `place` splices bytes into it: every other byte of the
file, every attribute of an element this does not move, and an attribute's own text where
its whole number is unchanged, come back as they were. A `<DMD>` with a `Source`, or a
`Target` other than the two windows, is never reported and never touched.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import NamedTuple

GLASS = "Backglass"
SCORE = "ScoreView"
TARGETS = (GLASS, SCORE)
_WINDOW = {"": GLASS, "backglass": GLASS, "scoreview": SCORE}
LIMIT = 100000

_QUOTED = rb'(?:"[^"]*"|\'[^\']*\')'
_ATTRIBUTES = rb'((?:\s+[A-Za-z_:][\w:.-]*\s*=\s*' + _QUOTED + rb')*)\s*(/?)>'
_DISPLAYS = re.compile(rb"<Displays" + _ATTRIBUTES)
_DISPLAYS_END = re.compile(rb"</Displays\s*>")
_DMD = re.compile(rb"<DMD" + _ATTRIBUTES)
_DMD_END = re.compile(rb"</DMD\s*>")
_ATTRIBUTE = re.compile(rb'([A-Za-z_:][\w:.-]*)\s*=\s*(?:"([^"]*)"|\'([^\']*)\')')
_DISPLAYS_WORD = re.compile(rb"<Displays[\s/>]")
_DMD_WORD = re.compile(rb"<DMD[\s/>]")
_ROOT_END = b"</DirectB2SData"
_ROOT = re.compile(rb"<DirectB2SData[\s>]")
_FIRST_CHILD = re.compile(rb"\n([ \t]*)<")


class Placement(NamedTuple):
    """Where a DMD sits: its top left corner and its size, in pixels of the art."""

    x: int
    y: int
    w: int
    h: int


@dataclass(frozen=True, slots=True)
class Display:
    """One `<DMD>` of the file. `source` names a controller resource, and is empty for the
    default DMD, the one a placement is about."""

    target: str
    placement: Placement
    source: str = ""


class DisplaysError(ValueError):
    """The file's `<Displays>` cannot be changed safely; nothing was written. The message is
    a code for a log, and never reaches a person."""


class _Element(NamedTuple):
    start: int
    end: int
    tag_end: int
    attributes: dict[str, str]


def _attributes(raw: bytes) -> dict[str, str]:
    return {name.decode("ascii"): (double or single).decode("utf-8", "replace")
            for name, double, single in _ATTRIBUTE.findall(raw)}


def _number(text: str | None) -> int:
    try:
        return int(float(text or 0))
    except (ValueError, OverflowError):
        return 0


def _target_of(attributes: dict[str, str]) -> str | None:
    """A missing Target is the backglass, as Visual Pinball reads it. None for a DMD that
    names any other window."""
    return _WINDOW.get(attributes.get("Target", "").strip().lower())


def _section(data: bytes) -> tuple[int, int, int, int] | None:
    """The `<Displays>` element as (start, end of its open tag, start of its close tag, end),
    the last two equal to the second for one with no children. None where the file has none."""
    opened = _DISPLAYS.search(data)
    if opened is None:
        if _DISPLAYS_WORD.search(data):
            raise DisplaysError("displays_unreadable")
        return None
    if opened.group(2):
        found = opened.start(), opened.end(), opened.end(), opened.end()
    else:
        closed = _DISPLAYS_END.search(data, opened.end())
        if closed is None:
            raise DisplaysError("displays_unclosed")
        found = opened.start(), opened.end(), closed.start(), closed.end()
    if _DISPLAYS_WORD.search(data, found[3]):
        raise DisplaysError("displays_twice")
    return found


def _elements(data: bytes, begin: int, finish: int) -> list[_Element]:
    found: list[_Element] = []
    at = begin
    while (match := _DMD.search(data, at, finish)) is not None:
        end = match.end()
        if not match.group(2):
            closed = _DMD_END.search(data, end, finish)
            if closed is None:
                raise DisplaysError("dmd_unclosed")
            end = closed.end()
        found.append(_Element(match.start(), end, match.end(), _attributes(match.group(1))))
        at = end
    if len(_DMD_WORD.findall(data, begin, finish)) != len(found):
        raise DisplaysError("dmd_unreadable")
    return found


def read(data: bytes) -> tuple[Display, ...]:
    """The file's DMDs, in file order. A file with no `<Displays>`, or one this cannot read,
    has none."""
    try:
        section = _section(data)
        if section is None:
            return ()
        return tuple(
            Display(target,
                    Placement(*(_number(one.attributes.get(name)) for name in "XYWH")),
                    one.attributes.get("Source", ""))
            for one in _elements(data, section[1], section[2])
            if (target := _target_of(one.attributes)) is not None)
    except DisplaysError:
        return ()


def placed(displays: tuple[Display, ...], target: str) -> Placement | None:
    """The place of the target's default DMD, or None where it has none."""
    return next((one.placement for one in displays
                 if one.target == target and not one.source), None)


def _newline(data: bytes) -> bytes:
    return b"\r\n" if b"\r\n" in data[:4096] else b"\n"


def _indent(data: bytes) -> bytes:
    root = _ROOT.search(data)
    child = _FIRST_CHILD.search(data, root.end() if root else 0, (root.end() if root else 0) + 512)
    return child.group(1) if child else b"  "


def _element(target: str, where: Placement) -> bytes:
    return (f'<DMD Target="{target}" X="{where.x}" Y="{where.y}" W="{where.w}" '
            f'H="{where.h}" />').encode("ascii")


def _line_around(data: bytes, start: int, end: int) -> tuple[int, int]:
    """`start` to `end` widened over the whitespace before it on its line and the line break
    after it, when nothing else is on the line."""
    line = data.rfind(b"\n", 0, start) + 1
    if data[line:start].strip():
        return start, end
    if data.startswith(b"\r\n", end):
        return line, end + 2
    if data.startswith(b"\n", end):
        return line, end + 1
    return line, end


def _moved(tag: bytes, where: Placement) -> bytes:
    """An open tag with the four numbers set. An attribute whose whole number is already the
    asked one keeps its text, and every other attribute is left alone."""
    shut = tag.endswith(b"/>")
    body = tag[:-2].rstrip() if shut else tag[:-1]
    for name, value in zip(b"XYWH", where, strict=True):
        letter = bytes([name])
        pattern = re.compile(rb"(\s" + letter + rb"\s*=\s*)(" + _QUOTED + rb")")
        found = pattern.search(body)
        if found is None:
            body += b' %s="%d"' % (letter, value)
        elif _number(found.group(2)[1:-1].decode("utf-8", "replace")) != value:
            quote = found.group(2)[:1]
            body = (body[:found.start()] + found.group(1) + quote + b"%d" % value + quote
                    + body[found.end():])
    return body + (b" />" if shut else b">")


def place(data: bytes, target: str, where: Placement | None) -> bytes:
    """`data` with the target's default DMD set to `where`, added if the file has none, or
    taken out when `where` is None, and the `<Displays>` element with it when that leaves
    an attribute-free one empty. Raises `DisplaysError` for a file this cannot splice
    into, and for a splice that does not read back as asked."""
    if target not in TARGETS:
        raise DisplaysError("unknown_window")
    if where is not None and not (0 <= where.x <= LIMIT and 0 <= where.y <= LIMIT
                                  and 1 <= where.w <= LIMIT and 1 <= where.h <= LIMIT):
        raise DisplaysError("out_of_range")
    if _ROOT.search(data) is None:
        raise DisplaysError("not_b2s")
    section = _section(data)
    mine = None if section is None else next(
        (one for one in _elements(data, section[1], section[2])
         if _target_of(one.attributes) == target and not one.attributes.get("Source")), None)
    if mine is not None:
        out = _replaced(data, mine, where)
    elif where is None:
        return data
    else:
        out = _added(data, section, _element(target, where))
    before = tuple(one for one in read(data) if not (one.target == target and not one.source))
    after = tuple(one for one in read(out) if not (one.target == target and not one.source))
    if before != after or placed(read(out), target) != where:
        raise DisplaysError("read_back_differs")
    return out


def _replaced(data: bytes, one: _Element, where: Placement | None) -> bytes:
    if where is None:
        start, end = _line_around(data, one.start, one.end)
        return _without_empty_displays(data[:start] + data[end:])
    tag = _moved(bytes(data[one.start:one.tag_end]), where)
    return data[:one.start] + tag + data[one.tag_end:]


def _without_empty_displays(data: bytes) -> bytes:
    """`data` less its `<Displays>` element, whole lines and all, where the element has no
    attributes and holds only whitespace. Nothing else lives in an element like that, and a
    file with none reads the same."""
    section = _section(data)
    if section is None or section[2] == section[1] or data[section[0]:section[1]] != b"<Displays>":
        return data
    if data[section[1]:section[2]].strip():
        return data
    start, end = _line_around(data, section[0], section[3])
    return data[:start] + data[end:]


def _added(data: bytes, section: tuple[int, int, int, int] | None, element: bytes) -> bytes:
    nl, step = _newline(data), _indent(data)
    if section is None:
        root = data.rfind(_ROOT_END)
        if root < 0:
            raise DisplaysError("last_tag_missing")
        lead = b"" if root == 0 or data[root - 1:root] == b"\n" else nl
        block = (lead + step + b"<Displays>" + nl + step * 2 + element + nl + step
                 + b"</Displays>" + nl)
        return data[:root] + block + data[root:]
    start, opened, closing, end = section
    if closing == opened and data[opened - 2:opened] == b"/>":
        shut = data[start:opened].rstrip()[:-2].rstrip()
        return data[:start] + shut + b">" + nl + step * 2 + element + nl + step \
            + b"</Displays>" + data[end:]
    inside = data[opened:closing]
    if not inside.strip():
        return data[:opened] + nl + step * 2 + element + nl + step + data[closing:]
    last = opened + len(inside.rstrip())
    return data[:last] + nl + step * 2 + element + data[last:]


class Splice(NamedTuple):
    """The one stretch of a file a change replaced: where it starts, the bytes that were
    there and the bytes that are there now."""

    start: int
    removed: bytes
    inserted: bytes

    def back(self, data: bytes) -> bytes:
        """`data`, which holds `inserted` at `start`, with `removed` there again. Raises
        `DisplaysError` where it does not."""
        end = self.start + len(self.inserted)
        if data[self.start:end] != self.inserted:
            raise DisplaysError("not_as_written")
        return data[:self.start] + self.removed + data[end:]


def _same_head(one: bytes, other: bytes) -> int:
    low, high = 0, min(len(one), len(other))
    while low < high:
        mid = (low + high + 1) // 2
        if one[:mid] == other[:mid]:
            low = mid
        else:
            high = mid - 1
    return low


def splice_of(before: bytes, after: bytes) -> Splice:
    """What `after` changed in `before`, as the bytes a change put in and took out. The two
    differ in one stretch only, which is all `place` ever changes."""
    head = _same_head(before, after)
    tail = _same_head(before[head:][::-1], after[head:][::-1])
    return Splice(head, before[head:len(before) - tail], after[head:len(after) - tail])
