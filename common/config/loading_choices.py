"""What Loading Screen and Launch Audio can be set to, and the order a launch tries them.

A choice is either read off the table being launched (its own Loading Video, Loading Image or
Playfield image, its own Launch Audio), off the theme in use, off a file the person added to
`launch_media`, or plain (a spinner, a dark screen, silence). The first three can be empty for
a given table, so the person also picks a fallback; `ladder` and `sound_ladder` are the order
of tries a launch walks.
"""

from __future__ import annotations

from dataclasses import dataclass

SECTION = "behavior"

SCREEN_KEY = "loading_screen"
FALLBACK_KEY = "loading_screen_fallback"
ENDS_KEY = "loading_screen_ends"
VIDEO_FILE_KEY = "loading_video_file"
IMAGE_FILE_KEY = "loading_image_file"
SOUND_KEY = "launch_audio"
SOUND_FALLBACK_KEY = "launch_audio_fallback"
SOUND_FILE_KEY = "launch_audio_file"

VIDEO = "video"
IMAGE = "image"
AUDIO = "audio"

# Screen choices.
TABLE_VIDEO = "table_video"
TABLE_IMAGE = "table_image"
TABLE_PLAYFIELD = "table_playfield"
THEME_VIDEO = "theme_video"
THEME_IMAGE = "theme_image"
MY_VIDEO = "my_video"
MY_IMAGE = "my_image"
SPINNER = "spinner"
OFF = "off"

# Sound choices.
TABLE_SOUND = "table"
THEME_SOUND = "theme"
MY_SOUND = "mine"
SOUND_OFF = "off"
SILENCE = "silence"

GROUP_TABLE = "table"
GROUP_EVERY = "every"
GROUP_PLAIN = "plain"


@dataclass(frozen=True)
class Choice:
    """One entry of the Loading Screen dropdown."""

    id: str
    group: str
    family: str
    # The media kind of the game it reads, for the choices that read one.
    kind: str = ""
    theme: bool = False
    mine: bool = False


CHOICES = (
    Choice(TABLE_VIDEO, GROUP_TABLE, VIDEO, kind="loading"),
    Choice(TABLE_IMAGE, GROUP_TABLE, IMAGE, kind="loading_image"),
    Choice(TABLE_PLAYFIELD, GROUP_TABLE, IMAGE, kind="playfield"),
    Choice(THEME_VIDEO, GROUP_EVERY, VIDEO, theme=True),
    Choice(THEME_IMAGE, GROUP_EVERY, IMAGE, theme=True),
    Choice(MY_VIDEO, GROUP_EVERY, VIDEO, mine=True),
    Choice(MY_IMAGE, GROUP_EVERY, IMAGE, mine=True),
    Choice(SPINNER, GROUP_PLAIN, IMAGE),
    Choice(OFF, GROUP_PLAIN, IMAGE),
)
SCREEN_CHOICES = tuple(one.id for one in CHOICES)
GROUPS = (GROUP_TABLE, GROUP_EVERY, GROUP_PLAIN)
BY_ID = {one.id: one for one in CHOICES}

SOUND_CHOICES = (TABLE_SOUND, THEME_SOUND, MY_SOUND, SOUND_OFF)
SOUND_FALLBACKS = (TABLE_SOUND, THEME_SOUND, MY_SOUND, SILENCE)

DEFAULT_SCREEN = TABLE_VIDEO
DEFAULT_FALLBACK = TABLE_IMAGE
DEFAULT_SOUND = TABLE_SOUND
DEFAULT_SOUND_FALLBACK = SILENCE

# Where the fallback starts when the main choice changes to one that can be empty.
FALLBACK_START = {TABLE_VIDEO: TABLE_IMAGE, TABLE_IMAGE: TABLE_PLAYFIELD,
                  TABLE_PLAYFIELD: SPINNER, THEME_VIDEO: TABLE_VIDEO,
                  THEME_IMAGE: TABLE_IMAGE}
SOUND_FALLBACK_START = {TABLE_SOUND: SILENCE, THEME_SOUND: TABLE_SOUND}


def screen_or_default(value: str) -> str:
    one = str(value).strip().lower()
    return one if one in BY_ID else DEFAULT_SCREEN


def fallback_or_default(value: str, main: str) -> str:
    one = str(value).strip().lower()
    return one if one in fallback_choices(main) else FALLBACK_START.get(main, SPINNER)


def sound_or_default(value: str) -> str:
    one = str(value).strip().lower()
    return one if one in SOUND_CHOICES else DEFAULT_SOUND


def sound_fallback_or_default(value: str, main: str) -> str:
    one = str(value).strip().lower()
    if one in sound_fallback_choices(main):
        return one
    return SOUND_FALLBACK_START.get(main, SILENCE)


def can_be_missing(choice: str) -> bool:
    """Whether a table can have nothing for this choice: a table's own media, or the theme's."""
    one = BY_ID.get(choice)
    return bool(one and (one.group == GROUP_TABLE or one.theme))


def sound_can_be_missing(choice: str) -> bool:
    return choice in (TABLE_SOUND, THEME_SOUND)


def fallback_choices(main: str) -> tuple[str, ...]:
    """Every choice but the main one and Off, and not the other theme choice when the main
    one is a theme's."""
    chosen = BY_ID.get(main)
    return tuple(one.id for one in CHOICES
                 if one.id not in (main, OFF) and not (chosen and chosen.theme and one.theme))


def sound_fallback_choices(main: str) -> tuple[str, ...]:
    other = THEME_SOUND if main == TABLE_SOUND else TABLE_SOUND
    return (other, MY_SOUND, SILENCE)


def ladder(main: str, fallback: str) -> list[str]:
    """The choices a launch tries, in order: the first that has something is shown. A table's
    Playfield image comes next when neither choice was it, then the spinner. Off is the
    whole ladder, and so is the spinner, wherever it is chosen: nothing follows it."""
    if main in (OFF, SPINNER):
        return [main]
    rungs = [main]
    if can_be_missing(main):
        rungs.append(fallback)
    if SPINNER in rungs:
        return rungs[:rungs.index(SPINNER) + 1]
    if TABLE_PLAYFIELD not in rungs:
        rungs.append(TABLE_PLAYFIELD)
    rungs.append(SPINNER)
    return rungs


def sound_ladder(main: str, fallback: str) -> list[str]:
    """The sounds a launch tries, in order, silence being what is left."""
    if main == SOUND_OFF:
        return []
    rungs = [main]
    if sound_can_be_missing(main):
        rungs.append(fallback)
    return [one for one in rungs if one != SILENCE]
