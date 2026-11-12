"""An app for a program VPinFE genuinely does not understand.

A binary and arguments, and an entry that is either a file or a key its program knows
how to look up. No parsing, no ROM resolution, no configuration surface, and no way to
tell when a session ended.

It is an app rather than a special case so that answering almost nothing is declared and
visible instead of discovered. It is also the contract's test case: two implementations,
one deliberately poor, is how we find out where the contract quietly assumed Visual
Pinball. It must never grow knowledge of a particular program - the first `if` on a
program's name is where the boundary dies, and a program worth special-casing has earned
its own app.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from common.apps.contract import (
    SESSION_NONE,
    App,
    Claim,
    Entry,
    Field,
    Kinds,
    Session,
    arguments,
    places_entry,
)

# A wheel, a video and a backglass are as true of an emulator or a video game as of a
# table. A script, a point of view, a ROM in our sense and the sidecars around them are
# not, and without saying so coverage reads "2 of 13" for an entry that can never have
# eleven of them.
KINDS: frozenset[str] = frozenset({
    "game_info", "media", "readme",
    "backglass", "scoreview", "playfield", "playfield_fss", "wheel", "cab",
    "real_dmd", "real_dmd_color", "real_dmd_video", "real_dmd_color_video",
    "full_dmd", "full_dmd_video", "flyer",
    "playfield_video", "backglass_video", "scoreview_video", "audio", "instruction_card",
    "topper", "topper_video", "loading", "audio_launch", "rule_sheet", "logo",
})

FIELDS: tuple[Field, ...] = (
    Field("bin_path", path="exe"),
    Field("args"),
    Field("launch_env", type="text", lines=3),
)


class GenericLaunch:
    def command(self, entry: Entry, settings: Mapping[str, Any]) -> list[str]:
        """The entry goes where `{table}` or `{key}` puts it, and last otherwise."""
        kept = arguments(entry, settings)
        if not places_entry(settings):
            target = entry.table or entry.key
            if target:
                kept.append(target)
        return [str(settings.get("bin_path") or ""), *kept]

    def session(self, settings: Mapping[str, Any]) -> Session:
        """Unobservable, and said rather than implied. A program we know nothing about
        may hand off to another process and exit immediately, so waiting on the child
        would record a three-second session instead of a game."""
        return Session(kind=SESSION_NONE)


# Claims no suffix at all: a file this app could play is one a person pointed a launcher
# at, and claiming an extension here would let it take tables away from the app that
# actually understands them.
GENERIC = App(
    id="generic",
    claim=Claim(accepts_keys=True),
    fields=FIELDS,
    kinds=Kinds(applicable=KINDS),
    launch=GenericLaunch(),
)
