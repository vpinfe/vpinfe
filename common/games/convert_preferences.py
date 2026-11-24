"""The Preferred Formats: what a person has said Convert should start with.

A preference is a value the person set. Automatic, and a quality left at Standard, hold
none, so the option the ask recommends is the one it preselects. Videos and Quality are
Recording's own settings, read by both.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from common.config.config_access import cfg_get
from common.games import media_encode as encode

MEDIA = "media"
CAPTURE = "capture"
AUTO = "auto"
KEEP = "keep"
DISCARD = "discard"


@dataclass(frozen=True)
class Preferences:
    """Each field is empty, or None for `keep`, where the person holds no preference."""

    picture: str = ""
    sound: str = ""
    video: str = ""
    quality: str = ""
    keep: bool | None = None


def read(config: Any = None) -> Preferences:
    """The preferences the device's file holds. A value a setting does not take reads as none."""
    if config is None:
        from common.config.paths import get_ini_config
        config = get_ini_config()

    def held(section: str, key: str, taken: tuple[str, ...]) -> str:
        said = cfg_get(config, section, key, AUTO).strip().lower()
        return said if said in taken else ""

    kept = held(MEDIA, "keep_originals", (KEEP, DISCARD))
    return Preferences(
        picture=held(MEDIA, "preferred_picture", (encode.PNG, encode.WEBP, encode.JPG)),
        sound=held(MEDIA, "preferred_sound", (encode.MP3, encode.OGG)),
        video=held(CAPTURE, "video_codec", (encode.H264, encode.VP9)),
        quality=held(CAPTURE, "quality", (encode.HIGH,)),
        keep=None if not kept else kept == KEEP,
    )
