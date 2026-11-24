"""A key as a person reads it, for where nothing has named it.

**The fallback, never the first answer.** `MediaSpec.label`, `AssetSpec.label`,
`FilterAxis.label` and `ConfigOption.label` all name their own things, and a registry
that knows the thing beats a rule that guesses at it.
"""

from __future__ import annotations

import re

# Only the acronyms VPinFE puts in a key. A general list would capitalize words that are
# ordinary here and be wrong more often than right.
ACRONYMS = frozenset({
    "api", "b2s", "cpu", "dmd", "dof", "fps", "fss", "gpu", "http", "https", "id",
    "ini", "ip", "ipdb", "json", "led", "nvram", "pup", "rgb", "rom", "ui", "url",
    "usb", "vni", "vps", "vpx", "vr", "pin2dmd", "wmp",
})

_WORDS = re.compile(r"\{[^{}]*\}(?:'[^\W_]+)?|[^\W_]+(?:'[^\W_]+)*", re.UNICODE)


# Words a title leaves lowercase unless they lead or close it. "Point of View" is the
# house style already - `AssetSpec` writes it by hand - so a rule that produced "Point
# Of View" would disagree with the registry it is the fallback for.
SMALL_WORDS = frozenset({
    "a", "an", "and", "as", "at", "but", "by", "for", "from", "in", "into", "nor",
    "of", "on", "onto", "or", "over", "per", "so", "the", "to", "up", "via", "vs",
    "with", "yet",
})

PARTICLES = frozenset({"in", "on", "over", "up"})


def field_label(text: str) -> str:
    """A label as a person reads it: title case, acronyms as acronyms.

    One rule for every label the Manager shows - a panel's fact, a column header, a picker
    entry - applied where they all pass rather than at each call site, so a hand-typed
    one cannot drift from the rest.

    Takes a key or a phrase, so `vps_id` and `VPS ID` both come back `VPS ID`.
    """
    said = re.sub(r"\{[^{}]*\}|_", lambda one: one.group(0) if len(one.group(0)) > 1 else " ",
                  str(text or ""))
    shouting = not any(one.islower() for one in said)
    found = list(_WORDS.finditer(said))
    spoken = [one for one in found if not one.group(0).startswith("{")]
    last = len(spoken) - 1
    out, at = [], 0
    for match in found:
        out.append(said[at:match.start()])
        at = match.end()
        if match.group(0).startswith("{"):
            out.append(match.group(0))
            continue
        place = spoken.index(match)
        out.append(_titled(match.group(0), lead_or_close=place in (0, last),
                           shouting=shouting))
    out.append(said[at:])
    return "".join(out).strip()


def _titled(word: str, *, lead_or_close: bool, shouting: bool) -> str:
    """One word of a title. A small word stays down unless it leads or closes or is a
    particle its source capitalizes, and a word with capitals after its first letter is
    written as its source writes it, unless the whole phrase is in capitals."""
    if word.lower() in ACRONYMS:
        return word.upper()
    if not shouting and any(one.isupper() for one in word[1:]):
        return word
    named = not shouting and word[:1].isupper() and word.lower() in PARTICLES
    if word.lower() in SMALL_WORDS and not lead_or_close and not named:
        return word.lower()
    return word[:1].upper() + word[1:].lower()


def humanize(key: str) -> str:
    """`real_dmd_color` -> `Real DMD Color`. The older name, kept for the callers that
    read it; one rule now, so a column picker and a panel cannot case a word two ways."""
    return field_label(key)
