"""What a surface says about a media file, in one vocabulary.

Two axes, and they answer different questions. **Tier** is whose file it is: the names
are the resolver's own, where a *table* is one .vpx and a *game* is the folder holding
them, and its five tiers collapse to four states here since two of the distinctions are
about our filename conventions and not about anything a user has a concept for.
**Source** is who put it there, which nothing about the file itself can say - only a
record can.

Six surfaces said the first of these five different ways before this existed, because
each new one invented its own words.
"""

from __future__ import annotations

from dataclasses import dataclass

from nicegui import ui

from common.games import asset_origin
from common.i18n import t
from common.media.media_specs import media_label_map

# Who placed a file, where the answer is not a catalog. "Unknown" is honest and common:
# anything predating the ledger, or placed with another tool, leaves no record.
YOU = t("manager.media_ownership.you")
UNKNOWN = t("word.unknown")
RECORDED = t("manager.media_ownership.recorded")
BACKGLASS_FILE = t("manager.media_ownership.backglass_file")
B2SPRO_FILE = t("manager.media_ownership.b2spro_file")


def source_name(origin: str) -> str:
    """The word for who put a file here.

    A catalog is named by the registry that owns it rather than by the id it is stored
    under, so the screen says VPinMediaDB where the ledger says vpinmediadb.
    """
    if not origin:
        return ""
    if origin == "user":
        return YOU
    if origin == asset_origin.RECORDED:
        return RECORDED
    if origin == asset_origin.BACKGLASS_FILE:
        return BACKGLASS_FILE
    if origin == asset_origin.B2SPRO_FILE:
        return B2SPRO_FILE
    if origin == UNKNOWN.lower():
        return UNKNOWN
    from common.online import sources
    return {source.id: source.name for source in sources.catalogs()}.get(origin, origin)


def checked_for_updates(origin: str) -> bool:
    """Whether a file placed by `origin` is one a catalog keeps current, so that changing
    the bytes makes it the person's own."""
    from common.online import sources
    return bool(origin) and origin in {source.id for source in sources.catalogs()}


def source_names() -> list[str]:
    """Every source a file could report, for a filter that offers them all."""
    from common.online import sources
    return sorted({source.name for source in sources.catalogs()}
                  | {YOU, UNKNOWN, RECORDED, BACKGLASS_FILE, B2SPRO_FILE})


# One .vpx owns it; the game owns it; something else is standing in; nothing is here.
# Two ways a file answers for nobody, and they differ in what you would do about it.
# An ORPHAN names a table the folder does not have: nothing will ever look for that
# name, so it is dead. UNUSED is correctly named and covered by something more specific
# - it is the fallback, and deleting it is giving up a safety net rather than tidying.
#
# Neither is one of `STATES`. The matrices resolve per kind and can produce neither, so
# offering them as filter choices there would be states the grid cannot draw. Only a
# lens that enumerates files can.
ORPHAN = "orphan"
UNUSED = "unused"
# A file named for a slot but written in a format VPinFE cannot use, so it answers for
# nobody until it is converted.
UNSUPPORTED = "unsupported"
# The `via` values of a file that serves no slot, which a lens that lists slots leaves out.
LOOSE = (ORPHAN, UNUSED, UNSUPPORTED)

TABLE = "table"
GAME = "game"
STAND_IN = "stand_in"
MISSING = "missing"


@dataclass(frozen=True)
class Tier:
    """The forms a surface might need: `noun` for a badge or a cell, `phrase` for
    inline in somebody else's sentence, `css` to color it, `mark` to draw it, `why`
    to explain it on hover. Whole sentences are built rather than stored, because two
    of them depend on what is being looked at."""

    key: str
    noun: str
    phrase: str
    css: str
    mark: str
    why: str


# The marks are a ramp by specificity: filled is bound to one table, hollow is the
# least specific, and a stand-in is the odd one out so it does not read as a degree of
# the others.
_TIERS = {
    TABLE: Tier(TABLE, "manager.media_ownership.table",
             "manager.media_ownership.table_2", "manager-tier--table",
                "manager-mark--full", "manager.media_ownership.table_tip"),
    GAME: Tier(GAME, "manager.media_ownership.game",
             "manager.media_ownership.game_short", "manager-tier--game",
               "manager-mark--outline", "manager.media_ownership.game_tip"),
    STAND_IN: Tier(STAND_IN, "manager.media_ownership.stand",
             "manager.media_ownership.standing", "manager-tier--standin",
                   "manager-mark--set",
                            "manager.media_ownership.stand_tip"),
    MISSING: Tier(MISSING, "manager.media_ownership.missing",
             "manager.media_ownership.not", "manager-tier--missing",
                  "manager-mark--dashed", "manager.media_ownership.nothing"),
    ORPHAN: Tier(ORPHAN, "manager.media_ownership.orphan",
             "manager.media_ownership.orphan_short",
                 "manager-tier--missing", "manager-mark--dashed",
                 "manager.media_ownership.orphan_tip"),
    UNUSED: Tier(UNUSED, "manager.media_ownership.unused",
             "manager.media_ownership.unused_short",
                 "manager-tier--standin", "manager-mark--set",
                 "manager.media_ownership.unused_tip"),
    UNSUPPORTED: Tier(UNSUPPORTED, "manager.media_ownership.unsupported",
             "manager.media_ownership.unsupported_short",
                      "manager-tier--warn", "manager-mark--dashed",
                      "manager.media_ownership.unsupported_tip"),
}

# Most specific first. `LEGEND` omits Missing, which a legend does not need - a blank
# cell is not a state anybody looks up - and `STATES` is all four, for a caller that
# has to map every one.
STATES = (TABLE, GAME, STAND_IN, MISSING)
# The two states that say whose file this is, as against the ones that say what is wrong.
SCOPES = (TABLE, GAME)
LEGEND = (TABLE, GAME, STAND_IN)


def tier_for(key: str) -> Tier:
    """The entry for a state that is already named, which a legend walks."""
    return _TIERS[key]


def key_of(via: str | None) -> str:
    """Which state a resolver tier is, in this vocabulary.

    `game` and `default` are one state: both are the folder's and every table in it
    uses them. What separates them is which filename convention they follow, which is
    ours and not the user's. `set` and `fallback` are one state too - either way the
    file serving this slot was named for something other than this slot.

    The ones the resolver never says are passed through, because the file lenses do say
    them: a file can be here and answer for nobody, which is not a tier at all.
    """
    via = str(via or "")
    if via in LOOSE:
        return via
    if via == TABLE:
        return TABLE
    if via in (GAME, "default"):
        return GAME
    if via.startswith(("set:", "fallback:")):
        return STAND_IN
    return MISSING


# What the asset resolver calls the same three states. It answers `dedicated` /
# `shared` / `none` where media answers a `via`, and the question is identical - which
# file wins for this table - so the words are these and not a second set.
# `superseded` is Unused, which already says "covered by something more specific" - a
# point of view file beside a table that has settings is exactly that, and it needs no
# word of its own.
_RESOLUTIONS = {"dedicated": TABLE, "shared": GAME, "none": MISSING,
                "superseded": UNUSED}


def key_of_resolution(resolution: str | None) -> str:
    """Which tier an `asset_resolver` resolution is."""
    return _RESOLUTIONS.get(str(resolution or ""), MISSING)


def of(via: str | None) -> Tier:
    return _TIERS[key_of(via)]


def for_resolution(resolution: str | None) -> Tier:
    return _TIERS[key_of_resolution(resolution)]


def noun(via: str | None) -> str:
    return of(via).noun


def phrase(via: str | None) -> str:
    return of(via).phrase


def sentence(via: str | None, *, viewing_a_table: bool = False) -> str:
    """Who uses this file, in a line.

    No full stop: it is one line of helper text under a name, not prose, and a stop at
    the end of a lone fragment reads like the start of a paragraph that never came.
    """
    key = key_of(via)
    if key == TABLE:
        return t("manager.media_ownership.table_uses")
    if key == GAME:
        return (t("manager.media_ownership.via_shared")
                if viewing_a_table else t("manager.media_ownership.via_game"))
    if key == STAND_IN:
        detail = str(via or "").split(":", 1)[-1]
        if str(via or "").startswith("set:"):
            return t("manager.media_ownership.set", detail=(detail))
        borrowed = media_label_map().get(detail, detail).lower()
        return t("manager.media_ownership.via_stand", borrowed=(borrowed))
    return ""


def _chip(tier: Tier, extra: str) -> ui.element:
    return ui.label(t(tier.noun)).classes(f"manager-tier {tier.css} {extra}".strip())


def badge(via: str | None, *, extra: str = "") -> ui.element:
    """The one visual mark for who owns a file, wherever a file is shown."""
    return _chip(of(via), extra)


def default_badge(*, extra: str = "") -> ui.element:
    """The mark for a file the library supplies, which `key_of` counts as the game's but a
    surface naming where a file comes from must not call This game."""
    return ui.label(t("manager.media_ownership.from_default")) \
        .classes(f"manager-tier {_TIERS[GAME].css} {extra}".strip())


def badge_for(key: str, *, extra: str = "") -> ui.element:
    """The same mark for a state named outright rather than read off a resolver -
    a chooser saying what a file *would* be has no `via` to derive it from."""
    return _chip(tier_for(key), extra)
