"""What VPS calls a kind of file against what VPinFE calls it.

Not a renaming - the vocabularies were built for different jobs. One of theirs is two
of ours (`altColorFiles` is Serum and VNI, separate specs in separate folders), some of
theirs have no local form at all (`tutorialFiles` is a link to a video), and the table
itself is absent: which build a `.vpx` is gets answered by binding a release to it.

`mediaPackFiles` and `soundFiles` are left out until the mapping is a fact rather than
a guess - a bundle spanning several of our kinds, and audio that may be music or may be
the table's own.
"""

from __future__ import annotations

from dataclasses import dataclass

ASSET = "asset"
MEDIA = "media"


@dataclass(frozen=True)
class VpsKind:
    """`listed_as` is the key on a VPSdb entry; `ours` are the local kinds it maps to,
    and `held_in` says which inventory answers whether one is here."""

    listed_as: str
    ours: tuple[str, ...]
    held_in: str


KINDS = (
    VpsKind("b2sFiles", ("backglass",), ASSET),
    VpsKind("romFiles", ("rom",), ASSET),
    VpsKind("altColorFiles", ("altcolor_serum", "altcolor_vni"), ASSET),
    VpsKind("altSoundFiles", ("altsound",), ASSET),
    VpsKind("pupPackFiles", ("pup_pack",), ASSET),
    VpsKind("povFiles", ("pov",), ASSET),
    VpsKind("ruleFiles", ("rule_sheet",), MEDIA),
    VpsKind("wheelArtFiles", ("wheel",), MEDIA),
    VpsKind("topperFiles", ("topper", "topper_video"), MEDIA),
)

BY_LISTING = {kind.listed_as: kind for kind in KINDS}

# Our kind to theirs. Many of ours map onto one of theirs - Serum and VNI are both
# `altColorFiles` - so this direction is many-to-one and the reverse is not a lookup.
BY_OURS = {ours: kind for kind in KINDS for ours in kind.ours}
