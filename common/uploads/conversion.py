"""What a dropped picture, sound or video is written as.

Kept apart from the converter so the analyzer and the import service can carry it on a
detected asset without importing the code that runs FFmpeg.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Conversion:
    """`kind` is `picture`, `sound` or `video`. `suffix` is the extension the file is
    written with. `as_is` writes the file untouched under its own extension, so the library
    holds it and shows it as unsupported until it is converted."""

    kind: str
    suffix: str
    as_is: bool = False
    # Why it cannot be converted here, where `as_is` is forced and not chosen: FFmpeg is
    # missing, or the file cannot be read. A forced file is offered unticked.
    blocked: str = ""
    # The converter's choices for this one file, as `media_convert.resolve_choices` takes
    # them. Not part of the identity: the suffix is what a plan is compared by.
    choices: Mapping[str, object] = field(default_factory=dict, compare=False, hash=False)
