"""Reading Visual Pinball's own settings file, and writing one back.

The file documents itself. Every setting VPX knows carries a comment above it giving the
label, the description, the default, and - where the answers are a closed set - what each
value means:

    ; Output Mode: Disabled, floating, or embedded [Default: 'Disabled', 0='Disabled',
    ; 1='Floating', 2='Embedded in playfield']
    BackglassOutput = 1

So the schema is read rather than authored, and a setting VPX adds in a later build
appears without anything here changing. 1133 of the 1195 keys in a current file carry
one.

Writing keeps the file's own shape: a value is replaced on the line it is already on and
nothing else moves, because the comments are the only documentation these settings have
and rewriting the file from a parse would throw them away.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field

# `; Label: description [Default: ...]`, or `; Label:  [Default: ...]:` with the
# description on the lines below it. The block is optional, and the label runs to the
# first colon.
_DEFAULT_BLOCK = re.compile(r"\[Default:(?P<body>.*?)\]:?[ \t]*(?:\n|$)", re.DOTALL)
# `in 0.0 .. 360.0 by 90.0 steps`, for a number that moves in steps.
_STEPS = re.compile(r"\s+by\s+\S+\s+steps\b")
# `0='None', 1='Dubois'` inside that block.
_CHOICE = re.compile(r"(-?\d+)\s*=\s*'([^']*)'")
# `5 in 0 .. 10`, the range VPX states for a number.
_RANGE = re.compile(r"in\s+(-?[\dXA-Fa-f.]+)\s*\.\.\s*(-?[\dXA-Fa-f.]+)")
_QUOTED = re.compile(r"^'(.*)'$")
_SECTION = re.compile(r"^\[(.+)\]\s*$")
_KEY = re.compile(r"^([^=\[;]+?)=(.*)$")

# What a value is, from what its default looks like. VPX states no type, so these are
# the only signals there are.
KIND_CHOICE = "choice"
KIND_INT = "int"
KIND_NUMBER = "number"
KIND_COLOR = "color"
KIND_STRING = "string"


@dataclass(frozen=True)
class Setting:
    """One key, as the file describes it."""

    section: str
    key: str
    value: str
    label: str = ""
    description: str = ""
    default: str = ""
    kind: str = KIND_STRING
    choices: tuple[tuple[str, str], ...] = ()
    minimum: float | None = None
    maximum: float | None = None
    # Where the value sits, so a write replaces it in place.
    line: int = -1

    @property
    def qualified(self) -> str:
        """`Section.Key`, which is what a layered lookup is keyed by. A key is only
        unique within its section - `Width` is in eight of them."""
        return f"{self.section}.{self.key}"


@dataclass
class Ini:
    lines: list[str] = field(default_factory=list)
    settings: dict[str, Setting] = field(default_factory=dict)

    def value(self, qualified: str) -> str | None:
        """What this file sets, or None where it sets nothing.

        A key written with nothing after the `=` sets nothing. VPX's own header says so
        - "when a property is not defined (nothing after the equal sign), VPX will use
        the default value for it" - and it writes every key it knows into the file, most
        of them blank. Reading blank as a value would make every setting in the
        program look like somebody had chosen it.
        """
        found = self.settings.get(qualified)
        return None if found is None or found.value == "" else found.value

    def mentions(self, qualified: str) -> bool:
        """Whether the file carries the key at all, blank or not. For writing back,
        which needs the line, rather than for reading what is in force."""
        return qualified in self.settings


def parse(text: str) -> Ini:
    """Every section, key and the comment block above it."""
    lines = text.splitlines()
    settings: dict[str, Setting] = {}
    section = ""
    pending: list[str] = []

    for index, raw in enumerate(lines):
        stripped = raw.strip()
        found = _SECTION.match(stripped)
        if found:
            section, pending = found.group(1).strip(), []
            continue
        if stripped.startswith(";"):
            pending.append(stripped[1:].strip())
            continue
        if not stripped:
            pending = []
            continue
        pair = _KEY.match(raw)
        if pair is None or not section:
            pending = []
            continue
        key = pair.group(1).strip()
        one = _describe(section, key, pair.group(2).strip(), pending, index)
        settings[one.qualified] = one
        pending = []

    return Ini(lines=lines, settings=settings)


def _describe(section: str, key: str, value: str, comments: list[str],
              line: int) -> Setting:
    label, description, default_block = _split("\n".join(c for c in comments if c))
    choices = tuple((num, said) for num, said in _CHOICE.findall(default_block))
    low, high = _bounds(default_block)
    return Setting(
        section=section, key=key, value=value, line=line,
        label=label or key, description=description,
        default=_default_of(default_block, choices),
        kind=_kind(default_block, choices),
        choices=choices, minimum=low, maximum=high,
    )


def _split(text: str) -> tuple[str, str, str]:
    """`(label, description, the default block)`. The label runs to the first colon,
    which is why the block is taken out first: several descriptions contain one."""
    found = _DEFAULT_BLOCK.search(text)
    if found is None:
        return (*_labelled(text), "")
    block = _STEPS.sub("", found.group("body").replace("\n", " ")).strip()
    label, before = _labelled(text[: found.start()])
    after = text[found.end():].strip()
    return label, "\n".join(part for part in (before, after) if part), block


def _labelled(text: str) -> tuple[str, str]:
    text = text.replace("\n", " ")
    if ":" in text:
        label, description = text.split(":", 1)
        return label.strip(), description.strip()
    return text.strip(), ""


def _default_of(block: str, choices: tuple[tuple[str, str], ...]) -> str:
    """The default as it would be stored, not as it is shown.

    An enumerated setting states its default by label - `'Luminance', 0='None', ...` -
    and what goes in the file is the number, so the label is looked back up.
    """
    head = block.split(",", 1)[0].strip() if choices else block.strip()
    head = _RANGE.sub("", head).strip()
    quoted = _QUOTED.match(head)
    said = quoted.group(1) if quoted else head
    if choices:
        return next((num for num, label in choices if label == said), said)
    return said


def _kind(block: str, choices: tuple[tuple[str, str], ...]) -> str:
    if choices:
        return KIND_CHOICE
    head = _RANGE.sub("", block).strip()
    if _QUOTED.match(head):
        return KIND_STRING
    if re.fullmatch(r"-?0X[0-9A-Fa-f]+", head):
        return KIND_COLOR
    if re.fullmatch(r"-?\d+", head):
        return KIND_INT
    if re.fullmatch(r"-?\d*\.\d+", head):
        return KIND_NUMBER
    return KIND_STRING


def _bounds(block: str) -> tuple[float | None, float | None]:
    found = _RANGE.search(block)
    if found is None:
        return None, None
    try:
        return float(found.group(1)), float(found.group(2))
    except ValueError:
        # A color states its range in hex. It is a range, but not one a number field
        # can use, so it is left unstated rather than reported wrong.
        return None, None


def written(ini: Ini, changes: dict[str, str],
            remove: Iterable[str] = ()) -> str:
    """The file with those values replaced, and nothing else touched.

    A key the file does not carry is appended under its section, or under a new section
    at the end where there is none. Rewriting from the parse would be shorter and would
    discard every comment, which is the only documentation these settings have.

    `remove` takes a key out rather than blanking it, which is what the program does to
    a table's settings when it has no value for one. Blanking reads the same on the way
    back in, and leaves a stub the program deletes the next time it saves. A section it
    leaves without a key goes too, heading and all.
    """
    lines = list(ini.lines)
    appended: dict[str, list[str]] = {}

    for qualified, value in changes.items():
        found = ini.settings.get(qualified)
        if found is not None and 0 <= found.line < len(lines):
            lines[found.line] = f"{found.key} = {value}"
            continue
        section, key = section_and_key(qualified)
        appended.setdefault(section, []).append(f"{key} = {value}")

    dropped = {ini.settings[q].line for q in remove
               if q in ini.settings and 0 <= ini.settings[q].line < len(lines)}
    # The parse's line numbers, so this runs before an append moves any of them.
    for line in sorted(_emptied(lines, dropped, keep=set(appended)), reverse=True):
        del lines[line]

    for section, rows in appended.items():
        at = _section_end(lines, section)
        if at is None:
            if lines and lines[-1].strip():
                lines.append("")
            lines.extend([f"[{section}]", *rows])
            continue
        lines[at:at] = rows
    return "\n".join(lines) + "\n"


def _emptied(lines: list[str], dropped: set[int], keep: set[str]) -> set[int]:
    """Those lines, each section they leave without a key but not one in `keep`, and
    the blank lines above whatever that takes off the end of the file."""
    gone = set(dropped)
    headings = [(n, found.group(1).strip()) for n, raw in enumerate(lines)
                if (found := _SECTION.match(raw.strip()))]
    for index, (start, name) in enumerate(headings):
        end = headings[index + 1][0] if index + 1 < len(headings) else len(lines)
        body = range(start + 1, end)
        if (name in keep or not gone.intersection(body)
                or any(_holds_key(lines[n]) for n in body if n not in gone)):
            continue
        gone.update(range(start, end))
    last = len(lines) - 1
    if last in gone:
        while last >= 0 and (last in gone or not lines[last].strip()):
            gone.add(last)
            last -= 1
    return gone


def _holds_key(raw: str) -> bool:
    stripped = raw.strip()
    return bool(stripped) and not stripped.startswith(";") and _KEY.match(raw) is not None


def section_and_key(qualified: str) -> tuple[str, str]:
    """The heading the program looks a qualified name up under, and the key beneath
    it: `[Plugin.<id>]` for a plugin's settings, the first part for the rest."""
    head, _, rest = qualified.partition(".")
    if head == "Plugin" and "." in rest:
        plugin, _, key = rest.partition(".")
        return f"{head}.{plugin}", key
    return head, rest


def _section_end(lines: list[str], section: str) -> int | None:
    """The line after the last one belonging to a section that is not blank, or None if
    it has none."""
    start = next((n for n, raw in enumerate(lines)
                  if (heading := _SECTION.match(raw.strip()))
                  and heading.group(1).strip() == section), None)
    if start is None:
        return None
    end = next((n for n in range(start + 1, len(lines)) if _SECTION.match(lines[n].strip())),
               len(lines))
    while end > start + 1 and not lines[end - 1].strip():
        end -= 1
    return end
