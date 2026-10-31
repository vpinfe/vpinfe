"""A kind of file in the words of a sentence: its plural, and what VPS calls one."""

from __future__ import annotations

from common.i18n import first_key, t


def _spaces(media: bool) -> tuple[str, str]:
    return ("media", "asset") if media else ("asset", "media")


def _word(kind: str, media: bool, leaf: str) -> str:
    key = first_key(*(f"{space}.kind.{kind}.{leaf}" for space in _spaces(media)))
    return t(key) if key else ""


def label(kind: str, media: bool = False) -> str:
    return _word(kind, media, "label") or kind


def plural(kind: str, media: bool = False) -> str:
    """Several of the kind: "ROMs", "Points of View"."""
    return _word(kind, media, "plural") or label(kind, media)


def vps_name(kind: str, media: bool = False) -> str:
    """One of the kind as VPS lists it: "VPS ROM", "VPS point of view"."""
    return _word(kind, media, "vps") or label(kind, media)


def in_a_sentence(words: str) -> str:
    """Title Case words set mid-sentence: "Points of View" becomes "points of view" and
    "ROMs" stays, because a word with two capitals is an acronym."""
    return " ".join(word if sum(c.isupper() for c in word) > 1 else word.lower()
                    for word in words.split(" "))
