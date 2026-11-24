"""What Loading Screen and Launch Audio mean for one game, said against its launch tiles.

A game's Loading Video, Loading Image and Launch Audio tiles are its own files, but what a
launch shows is decided by the two settings. For each tile this says, in a line, either what
the empty slot gets instead or why the game's file is not the one used. Each line is
worked out from the same ladder a launch walks, so the words and the launch agree.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from common.config import loading_choices as choices
from common.i18n import t
from common.media import launch_media
from common.values import is_truthy

LOADING = "loading"
LOADING_IMAGE = "loading_image"
PLAYFIELD = "playfield"
LAUNCH_AUDIO = "audio_launch"
MUTE_KEY = "mute_audio"
# The tiles that carry a line, and the choice that reads each.
TILES = (LOADING, LOADING_IMAGE, LAUNCH_AUDIO)
# The kinds `present` has to say something about.
READ = (LOADING, LOADING_IMAGE, PLAYFIELD, LAUNCH_AUDIO)

_SETTING = "config.behavior.loading_screen.label"
_AUDIO_SETTING = "config.behavior.launch_audio.label"
_SAY = {kind: f"manager.launchline.say.{kind}" for kind in (LOADING, LOADING_IMAGE, PLAYFIELD)}
_THING = {family: f"manager.loading.thing.{family}"
          for family in (choices.VIDEO, choices.IMAGE, choices.AUDIO)}


@dataclass(frozen=True)
class Theme:
    """The theme in use, as far as the loading rows are concerned."""

    name: str = ""
    video: str = ""
    image: str = ""
    audio: str = ""
    own: bool = False

    def has(self, family: str) -> bool:
        return bool({choices.VIDEO: self.video, choices.IMAGE: self.image,
                     choices.AUDIO: self.audio}[family])


def theme_in_use(listing: Mapping[str, Any]) -> Theme:
    """The theme in use from the themes listing; nothing when none is active."""
    active: dict = next((one for one in listing.get("themes") or () if one.get("active")), {})
    named = active.get("loading") or {}
    return Theme(name=str(active.get("name") or ""), video=str(named.get("video") or ""),
                 image=str(named.get("image") or ""), audio=str(named.get("audio") or ""),
                 own=bool(named.get("own")))


@dataclass(frozen=True)
class _Given:
    choice: str
    family: str
    what: str
    own: bool = False


def _setting(sound: bool) -> str:
    return t(_AUDIO_SETTING if sound else _SETTING)


def _verb(family: str, *, lower: bool = False) -> str:
    plays = family in (choices.VIDEO, choices.AUDIO)
    return t(f"manager.launchline.verb.{'plays' if plays else 'shows'}{'_lower' if lower else ''}")


def _chosen(behavior: Mapping[str, Any], family: str) -> str:
    key = {choices.VIDEO: choices.VIDEO_FILE_KEY, choices.IMAGE: choices.IMAGE_FILE_KEY,
           choices.AUDIO: choices.SOUND_FILE_KEY}[family]
    name = str(behavior.get(key) or "")
    return name if launch_media.path_of(name) else ""


def _theme_what(theme: Theme, family: str) -> str:
    return t("manager.launchline.theme_thing", theme=theme.name, thing=t(_THING[family]))


def _screen_given(one: str, present: Mapping[str, bool], theme: Theme,
                  behavior: Mapping[str, Any]) -> _Given | None:
    choice = choices.BY_ID[one]
    if choice.kind:
        return (_Given(one, choice.family, t(_SAY[choice.kind]), own=True)
                if present.get(choice.kind) else None)
    if choice.theme:
        return _Given(one, choice.family, _theme_what(theme, choice.family)) \
            if theme.has(choice.family) else None
    if choice.mine:
        name = _chosen(behavior, choice.family)
        return _Given(one, choice.family, name) if name else None
    return _Given(one, choice.family, t("manager.launchline.say." + one))


def _screen_winner(main: str, fallback: str, present: Mapping[str, bool], theme: Theme,
                   behavior: Mapping[str, Any]) -> _Given | None:
    for one in choices.ladder(main, fallback):
        given = _screen_given(one, present, theme, behavior)
        if given is not None:
            return given
    return None


def _screen_line(tile: str, present: Mapping[str, bool], theme: Theme,
                 behavior: Mapping[str, Any]) -> str:
    main = choices.screen_or_default(str(behavior.get(choices.SCREEN_KEY) or ""))
    fallback = choices.fallback_or_default(str(behavior.get(choices.FALLBACK_KEY) or ""), main)
    setting = _setting(False)
    own_choice = choices.TABLE_VIDEO if tile == LOADING else choices.TABLE_IMAGE
    won = _screen_winner(main, fallback, present, theme, behavior)
    chosen = choices.BY_ID[main]
    every = (_screen_given(main, present, theme, behavior) is not None) and not chosen.kind
    if won is None:
        return ""
    if present.get(tile):
        if won.choice == own_choice:
            return ""
        if chosen.group == choices.GROUP_PLAIN:
            return t("manager.launchline.not_while", setting=setting,
                     choice=t(f"config.behavior.loading_screen.choice.{main}"))
        if every:
            return t("manager.launchline.not_every", setting=setting,
                     verb=_verb(won.family, lower=True), what=won.what)
        in_path = main == own_choice or (choices.can_be_missing(main) and fallback == own_choice)
        if in_path:
            return t("manager.launchline.comes_first", noun=_noun(won.choice))
        return t("manager.launchline.no_table", setting=setting, noun=_noun(own_choice))
    return t("manager.launchline.every" if every else "manager.launchline.instead",
             verb=_verb(won.family), what=won.what, setting=setting)


def _noun(choice: str) -> str:
    one = choices.BY_ID[choice]
    if one.kind == PLAYFIELD:
        return t("manager.loading.playfield_image")
    return t("media.kind.loading.label" if one.family == choices.VIDEO
             else "media.kind.loading_image.label")


def _sound_given(one: str, present: Mapping[str, bool], theme: Theme,
                 behavior: Mapping[str, Any]) -> _Given | None:
    if one == choices.TABLE_SOUND:
        return (_Given(one, choices.AUDIO, t("manager.launchline.say.audio_launch"), own=True)
                if present.get(LAUNCH_AUDIO) else None)
    if one == choices.THEME_SOUND:
        return _Given(one, choices.AUDIO, _theme_what(theme, choices.AUDIO)) \
            if theme.has(choices.AUDIO) else None
    name = _chosen(behavior, choices.AUDIO) if one == choices.MY_SOUND else ""
    return _Given(one, choices.AUDIO, name) if name else None


def _sound_line(present: Mapping[str, bool], theme: Theme, behavior: Mapping[str, Any]) -> str:
    main = choices.sound_or_default(str(behavior.get(choices.SOUND_KEY) or ""))
    fallback = choices.sound_fallback_or_default(
        str(behavior.get(choices.SOUND_FALLBACK_KEY) or ""), main)
    setting = _setting(True)
    on = bool(present.get(LAUNCH_AUDIO))
    if main == choices.SOUND_OFF:
        return t("manager.launchline.sound_off_on" if on else "manager.launchline.sound_off",
                 setting=setting)
    won = next((given for one in choices.sound_ladder(main, fallback)
                if (given := _sound_given(one, present, theme, behavior)) is not None), None)
    first = _sound_given(main, present, theme, behavior)
    every = first is not None and main != choices.TABLE_SOUND
    if every and won is not None:
        if on:
            return t("manager.launchline.not_every", setting=setting,
                     verb=_verb(choices.AUDIO, lower=True), what=won.what)
        return t("manager.launchline.every", verb=_verb(choices.AUDIO), what=won.what,
                 setting=setting)
    if on:
        return "" if won is not None and won.own else t(
            "manager.launchline.no_table_sound", setting=setting)
    if won is not None:
        return t("manager.launchline.instead", verb=_verb(choices.AUDIO), what=won.what,
                 setting=setting)
    return t("manager.launchline.silence", setting=setting)


def lines(behavior: Mapping[str, Any], theme: Theme,
          present: Mapping[str, bool]) -> dict[str, str]:
    """The line for each of the tiles Loading Video, Loading Image and Launch Audio, "" where
    there is nothing to say. `behavior` is the Behavior settings, `present` says, by kind,
    whether the game has a file of that kind (`READ` are the kinds it needs)."""
    if theme.own:
        own = t("manager.launchline.own", theme=theme.name)
        return {tile: own for tile in TILES}
    found = {LOADING: _screen_line(LOADING, present, theme, behavior),
             LOADING_IMAGE: _screen_line(LOADING_IMAGE, present, theme, behavior),
             LAUNCH_AUDIO: _sound_line(present, theme, behavior)}
    if is_truthy(behavior.get(MUTE_KEY)):
        found[LAUNCH_AUDIO] = t("config.behavior.launch_audio.muted")
    return found
