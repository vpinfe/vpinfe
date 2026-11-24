"""Which build of Visual Pinball wrote an ini, and so which settings that build has."""

from __future__ import annotations

import re

from .setting_types import LINE, SPANS

_REV = re.compile(r"[0-9]+")

PLUGINS_LINE = "1081"
LINES = tuple(dict.fromkeys((PLUGINS_LINE, "1090", LINE)))


def rev_of(version: str | None) -> int | None:
    """The Rev of a build in a line the settings were read from, out of the
    `[Version] VPinball` an ini records. None for a build of any other line, and for none.
    """
    said = (version or "").strip()
    for line in LINES:
        if line and said.startswith(line) and _REV.fullmatch(said[len(line):]):
            return int(said[len(line):])
    return None


def predates_plugins(version: str | None) -> bool:
    """Whether a `[Version] VPinball` is of a line before `PLUGINS_LINE`, the first with
    plugins."""
    said = (version or "").strip()
    head = said[:len(PLUGINS_LINE)]
    return len(head) == len(PLUGINS_LINE) and head.isascii() and head.isdigit() \
        and int(head) < int(PLUGINS_LINE)


def declares(qualified: str, rev: int | None, plugin_present: bool = False) -> bool:
    """Whether the build at `rev` has this setting. True for every setting we hold no span
    for, and for a build whose Rev we do not know.

    An ini older than the program that reads it lacks the settings added since, so a
    setting of a plugin the program has is not held back for a Rev below its first. A setting
    the program dropped stays dropped whatever plugin carries it.
    """
    span = SPANS.get(qualified)
    if span is None or rev is None:
        return True
    if span.until is not None and rev >= span.until:
        return False
    return rev >= span.since or plugin_present


EDITOR_LOG = "Editor.EnableLog"
GLOBAL_LOG = "Global.EnableLog"


def log_switch(version: str | None) -> str:
    """The setting that turns the build's log on. A build of an earlier line, and one of
    this line from before Rev 5640, keeps it under Editor; every later build, and one whose
    `[Version] VPinball` is not known, under Global."""
    if predates_plugins(version):
        return EDITOR_LOG
    return GLOBAL_LOG if declares(GLOBAL_LOG, rev_of(version)) else EDITOR_LOG


def logs_unless_told_not_to(version: str | None) -> bool:
    """Whether the build logs while the ini leaves its switch out. False for 10.8.0 and
    earlier; True for every later build and for one whose `[Version] VPinball` is unknown."""
    return not predates_plugins(version)


# The Rev from which a plugin's Enable, left out of the ini, is on rather than off.
_ENABLED_BY_DEFAULT_FROM = 6274
# Plugins whose Enable the build, rather than the plugin, decides when the ini is silent.
DEFAULTED = ("B2S", "B2SLegacy")


def enable_default(plugin: str, rev: int | None, windows: bool = False) -> str | None:
    """What a plugin's Enable holds while the ini leaves it out, at `rev`: "1", "0", or
    None for a plugin whose default is its own or a build we do not know.

    Before the build that made B2S the renderer, B2S was off and B2SLegacy was on, except
    on Windows where B2SLegacy was off too. From it, B2S is on and B2SLegacy is off.
    """
    if rev is None or plugin not in DEFAULTED:
        return None
    if rev >= _ENABLED_BY_DEFAULT_FROM:
        return "1" if plugin == "B2S" else "0"
    return "0" if plugin == "B2S" or windows else "1"


# The Rev of the build that dropped B2SLegacy from the plugins folder.
_LEGACY_DROPPED_AT = 6293


def settled_rev(rev: int | None, present: frozenset[str] | None) -> int | None:
    """`rev` as the plugins folder proves it. An ini keeps the Rev of the build that last
    wrote it, which an upgrade leaves behind until the program next runs; a folder with
    Nixie and no B2SLegacy is a build from `_LEGACY_DROPPED_AT` on whatever the ini says.
    """
    if present is not None and "Nixie" in present and "B2SLegacy" not in present:
        return max(rev or 0, _LEGACY_DROPPED_AT)
    return rev
