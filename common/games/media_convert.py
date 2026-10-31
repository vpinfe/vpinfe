"""Converting pictures, sounds and videos a device cannot use, as one job.

`plan` says what would happen to each file and which choices the person has, each option
with a recommendation and a sentence. The ask, the API and the job all call it, so the count
a person saw is the count the job works from. The job plans again at run time, because
files change.

A file is converted at its own folder under the same stem. The new file is staged beside it
and moved over only when whole, so a file that cannot be converted is skipped with its
reason and left as it was, and a copy taken first is taken only for a conversion that is
about to happen.
"""

from __future__ import annotations

import logging
import os
import shutil
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path, PurePosixPath
from typing import Any

from common import atomic_write, service_errors
from common.backups import folder as backup_folder
from common.backups import media as backup_media
from common.backups.copies import REASON_CONVERT
from common.failures import why
from common.games import convert_preferences, game_lens, media_ops, media_service
from common.games import media_encode as encode
from common.games.media_files import SkipError, Target, inside, stamp
from common.host import frontend_browser, launch_state, tools
from common.i18n import t
from common.media.media_specs import (
    CONVERTIBLE_AUDIO,
    CONVERTIBLE_IMAGE,
    CONVERTIBLE_VIDEO,
    VIDEO_FAMILY,
    media_family,
    supported_named,
    unsupported_named,
)
from common.runtime import jobs, shutdown
from common.runtime.jobs import JobReporter

logger = logging.getLogger("vpinfe.common.games.media_convert")

PICTURE = "picture"
SOUND = "sound"
VIDEO = "video"
CONTAINER = "container"
QUALITY = "quality"
SOUND_IN_VIDEO = "sound_in_video"
ON_CLASH = "on_clash"
KEEP = "keep"
WAIT = "wait_while_playing"

KEEP_SOUND = "keep"
REMOVE_SOUND = "remove"
LEAVE = "leave"
REPLACE = "replace"

CONVERT = "convert"
REPACKAGE = "remux"
RENAME = "rename"
SKIP = "skip"

# What each choice takes, the recommendation first.
_VALUES: dict[str, tuple[str, ...]] = {
    PICTURE: (encode.PNG, encode.WEBP, encode.JPG),
    SOUND: (encode.MP3, encode.OGG),
    CONTAINER: (encode.MP4, encode.WEBM),
    VIDEO: (encode.H264, encode.VP9, encode.AV1),
    QUALITY: (encode.STANDARD, encode.HIGH, encode.SMALL),
    SOUND_IN_VIDEO: (KEEP_SOUND, REMOVE_SOUND),
    ON_CLASH: (LEAVE, REPLACE),
}
_FLAGS = (KEEP, WAIT)
# The video codecs a device's report names.
_CODECS = encode.CONTAINER_VIDEO[encode.MP4]

_PAUSE_SECONDS = 5
_pause = time.sleep


@dataclass(frozen=True)
class Device:
    """Somewhere files are played, and what its browser reports it plays: `True`, `False`, or
    `None` where it has reported nothing, which is never counted as a no."""

    label: str
    plays: Mapping[str, bool | None]


def default_devices() -> list[Device]:
    """This install's own frontend, which is all an API call that names no devices has."""
    formats = frontend_browser.current().get("formats") or {}
    return [Device(t("media_convert.this_device"),
                   {codec: formats.get(codec) for codec in (*_CODECS, "hevc")})]


@dataclass(frozen=True)
class Choices:
    """What the person chose, or what each omitted choice was recommended as."""

    picture: str = encode.PNG
    sound: str = encode.MP3
    container: str = encode.MP4
    video: str = encode.H264
    quality: str = encode.STANDARD
    sound_in_video: str = KEEP_SOUND
    on_clash: str = LEAVE
    keep: bool = True
    wait_while_playing: bool = True
    # The choices that were not given and so took the recommendation.
    defaulted: tuple[str, ...] = ()

    def made(self) -> dict[str, Any]:
        return {key: value for key, value in asdict(self).items() if key != "defaulted"}


@dataclass(frozen=True)
class Option:
    value: str | bool
    recommended: bool
    why: str
    # False where it cannot be made here, with the reason in `why`.
    available: bool = True
    # Held by the Preferred Formats. Independent of `recommended`: both can be true, and the
    # ask words the preference.
    preferred: bool = False


@dataclass(frozen=True)
class Choice:
    key: str
    options: tuple[Option, ...]
    # What the choice is called, and the sentence that says what it changes.
    label: str = ""


@dataclass(frozen=True)
class Item:
    """One file and what would happen to it."""

    game_id: str
    path: str
    action: str
    kind: str = ""
    source_format: str = ""
    target_format: str = ""
    new_path: str = ""
    size: int = 0
    clash: bool = False
    reason: str = ""
    note: str = ""
    has_sound: bool = False
    streams: encode.Streams | None = field(default=None, repr=False, compare=False)
    encoder: str = field(default="", repr=False, compare=False)

    def made(self) -> dict[str, Any]:
        return {key: value for key, value in asdict(self).items()
                if key not in ("streams", "encoder", "has_sound") and value not in ("", False)
                or key in ("game_id", "path", "action", "size")}


@dataclass(frozen=True)
class Plan:
    items: tuple[Item, ...]
    chosen: Choices
    choices: tuple[Choice, ...]
    ffmpeg: str
    devices: tuple[Device, ...]
    # Why no video can be made, where no device plays any codec.
    video_blocked: str = ""

    def counts(self) -> dict[str, int]:
        counts = dict.fromkeys((CONVERT, REPACKAGE, RENAME, SKIP), 0)
        for item in self.items:
            counts[item.action] += 1
        return counts

    def made(self) -> dict[str, Any]:
        working = [item for item in self.items if item.action != SKIP]
        return {
            "items": [item.made() for item in self.items],
            "counts": self.counts(),
            "bytes": sum(item.size for item in working),
            "ffmpeg": self.ffmpeg,
            "choices": self.chosen.made(),
            "defaulted": list(self.chosen.defaulted),
            "options": [{"key": choice.key, "label": choice.label,
                         "options": [{"value": option.value, "recommended": option.recommended,
                                      "preferred": option.preferred,
                                      "why": option.why, "available": option.available}
                                     for option in choice.options]}
                        for choice in self.choices],
            "video_blocked": self.video_blocked,
        }


class Ffmpeg:
    """FFmpeg found at the first need and not looked for again, whether it was found or not."""

    def __init__(self) -> None:
        self._found: tools.Found | None = None

    @property
    def found(self) -> tools.Found:
        if self._found is None:
            self._found = tools.resolve(tools.FFMPEG)
        return self._found

    @property
    def usable(self) -> bool:
        return self.found.state == tools.State.FOUND and self.found.path is not None

    @property
    def path(self) -> Path:
        if not self.usable or self.found.path is None:
            raise SkipError(t("error.media_convert.no_ffmpeg"))
        return self.found.path

    def encoder(self, names: tuple[str, ...]) -> str:
        return encode.encoder_for(self.found.probe, names) if self.usable else ""


# --- choices -------------------------------------------------------------------------


def _blockers(devices: Sequence[Device]) -> dict[str, list[str]]:
    """The devices that will not play each codec, by their own report."""
    return {codec: [one.label for one in devices if one.plays.get(codec) is False]
            for codec in _CODECS}


def played_by_all(devices: Sequence[Device]) -> tuple[str, ...]:
    """The codecs no device says it cannot play, in the order they are recommended."""
    blockers = _blockers(devices)
    return tuple(codec for codec in _CODECS if not blockers[codec])


def _available(ffmpeg: Ffmpeg, codec: str) -> bool:
    return bool(ffmpeg.encoder(encode.VIDEO_ENCODERS[codec]))


def recommended_video(devices: Sequence[Device], ffmpeg: Ffmpeg,
                      container: str | None = None) -> str:
    """The first of H.264, VP9 and AV1 that every device plays and FFmpeg here can write,
    else the first FFmpeg can write that the most devices play. A WebM takes neither H.264
    nor, so, a recommendation of it."""
    made = [codec for codec in _CODECS if _available(ffmpeg, codec)
            and not (container == encode.WEBM and codec == encode.H264)
            ] if ffmpeg.usable else []
    for codec in made:
        if codec in played_by_all(devices):
            return codec
    blockers = _blockers(devices)
    if made:
        return min(made, key=lambda codec: len(blockers[codec]))
    return encode.VP9 if container == encode.WEBM else encode.H264


def resolve_choices(raw: Mapping[str, Any] | None, devices: Sequence[Device],
                    ffmpeg: Ffmpeg,
                    preferred: convert_preferences.Preferences | None = None) -> Choices:
    """The choices as given, each one omitted taking the preference where one is held and
    this FFmpeg can make it, else the recommendation. Refused for a value that is not one
    of the choice's options."""
    preferred = preferred or convert_preferences.Preferences()
    raw = dict(raw or {})
    unknown = sorted(set(raw) - set(_VALUES) - set(_FLAGS))
    if unknown:
        raise service_errors.RefusedError(t("error.media_convert.unknown_choice",
                                            choice=unknown[0]),
                                          details={"known": [*_VALUES, *_FLAGS]})
    made: dict[str, Any] = {}
    defaulted: list[str] = []
    for key, values in _VALUES.items():
        if key in raw and raw[key] is not None:
            if raw[key] not in values:
                raise service_errors.RefusedError(
                    t("error.media_convert.bad_value", choice=key),
                    details={"choice": key, "known": list(values)})
            made[key] = raw[key]
        else:
            made[key] = _starting_value(key, values, preferred, ffmpeg)
            defaulted.append(key)
    for key in _FLAGS:
        if key in raw and raw[key] is not None:
            if not isinstance(raw[key], bool):
                raise service_errors.RefusedError(
                    t("error.media_convert.bad_value", choice=key),
                    details={"choice": key, "known": [True, False]})
            made[key] = raw[key]
        else:
            made[key] = True if key != KEEP or preferred.keep is None else preferred.keep
            defaulted.append(key)
    if VIDEO in defaulted:
        made[VIDEO] = (preferred.video if _can_make_video(preferred.video, ffmpeg, made[CONTAINER])
                       else recommended_video(devices, ffmpeg, made[CONTAINER]))
    if made[CONTAINER] == encode.WEBM and made[VIDEO] == encode.H264:
        raise service_errors.RefusedError(t("error.media_convert.webm_h264"))
    return Choices(**made, defaulted=tuple(defaulted))


def _choices_for(raw: Mapping[str, Any] | None, devices: Sequence[Device],
                 ffmpeg: Ffmpeg) -> Choices:
    """`resolve_choices` with the Preferred Formats this device holds, so what a person is
    shown is what the job makes."""
    return resolve_choices(raw, devices, ffmpeg, convert_preferences.read())


def _can_make_video(codec: str, ffmpeg: Ffmpeg, container: str) -> bool:
    return bool(codec) and ffmpeg.usable and _available(ffmpeg, codec) and not (
        container == encode.WEBM and codec == encode.H264)


def _starting_value(key: str, values: tuple[str, ...],
                    preferred: convert_preferences.Preferences, ffmpeg: Ffmpeg) -> str:
    """The value a choice nobody made starts at."""
    held = {PICTURE: preferred.picture, SOUND: preferred.sound,
            QUALITY: preferred.quality}.get(key, "")
    if held in values and (key != SOUND or not ffmpeg.usable
                           or ffmpeg.encoder(encode.SOUND_ENCODERS[held])):
        return held
    return _default_sound(ffmpeg) if key == SOUND else values[0]


def _default_sound(ffmpeg: Ffmpeg) -> str:
    """The first sound format this FFmpeg can write, MP3 where it cannot tell."""
    if ffmpeg.usable:
        for value in _VALUES[SOUND]:
            if ffmpeg.encoder(encode.SOUND_ENCODERS[value]):
                return value
    return _VALUES[SOUND][0]


# --- the plan ------------------------------------------------------------------------


def plan(targets: Sequence[Target], raw: Mapping[str, Any] | None = None,
         devices: Sequence[Device] | None = None) -> Plan:
    """What converting these files would do, and the choices the person has."""
    devices = list(devices) if devices else default_devices()
    ffmpeg = Ffmpeg()
    held = convert_preferences.read()
    chosen = resolve_choices(raw, devices, ffmpeg, held)
    claimed: set[tuple[str, str]] = set()
    variant = media_service.media_settings()[0]
    items = []
    for target in dict.fromkeys(targets):
        try:
            game = game_lens.game_or_refuse(target.game_id)
            items.append(_plan_file(target, Path(str(game.full_path_game)), chosen, devices,
                                    ffmpeg, claimed, variant))
        except (SkipError, service_errors.ServiceError) as exc:
            items.append(Item(target.game_id, target.path, SKIP, reason=why(exc)))
    blocked = _blocked_video(devices)
    return Plan(tuple(items), chosen,
                _choices(items, chosen, devices, ffmpeg, held),
                _ffmpeg_state(ffmpeg), tuple(devices), blocked)


def _ffmpeg_state(ffmpeg: Ffmpeg) -> str:
    return str(ffmpeg.found.state.value)


def _blocked_video(devices: Sequence[Device]) -> str:
    blockers = _blockers(devices)
    if devices and all(len(blockers[codec]) == len(devices) for codec in _CODECS):
        return t("error.media_convert.no_device_plays")
    return ""


def _plan_file(target: Target, game_dir: Path, chosen: Choices, devices: Sequence[Device],
               ffmpeg: Ffmpeg, claimed: set[tuple[str, str]], variant: str) -> Item:
    source = inside(game_dir, target.path)
    suffix = source.suffix.lower()
    if suffix in CONVERTIBLE_IMAGE:
        kind, named = PICTURE, unsupported_named(source.name, variant)
    elif suffix in CONVERTIBLE_AUDIO:
        kind, named = SOUND, unsupported_named(source.name, variant)
    elif suffix in CONVERTIBLE_VIDEO:
        kind, named = VIDEO, unsupported_named(source.name, variant)
    elif suffix in VIDEO_FAMILY:
        found = supported_named(source.name, variant)
        kind, named = VIDEO, found if found and media_family(found.kind) == "video" else None
    else:
        raise SkipError(t("error.media_convert.not_convertible"))
    if named is None:
        raise SkipError(t("error.media_convert.not_named"))
    size = source.stat().st_size
    if kind == PICTURE:
        item = _plan_picture(target, source, suffix, chosen, size)
    elif kind == SOUND:
        item = _plan_sound(target, suffix, chosen, ffmpeg, size)
    else:
        item = _plan_video(target, source, suffix, chosen, devices, ffmpeg, size)
    return _settle(item, game_dir, target, chosen, claimed)


def _new_path(path: str, extension: str) -> str:
    """The same name under another extension. A name already ending in it, in any case, is
    kept as spelled: on a filesystem that ignores case the two spellings are one file."""
    if PurePosixPath(path).suffix.lower() == extension:
        return path
    return str(PurePosixPath(path).with_suffix(extension))


def _plan_picture(target: Target, source: Path, suffix: str, chosen: Choices,
                  size: int) -> Item:
    goal = chosen.picture
    new = _new_path(target.path, encode.PICTURE_EXTENSION[goal])
    action = RENAME if goal == encode.JPG and suffix in encode.JPEG_NAMES else CONVERT
    note = t("media_convert.note.flattened") if (
        goal == encode.JPG and action == CONVERT and _has_see_through(source)) else ""
    return Item(target.game_id, target.path, action, PICTURE, suffix[1:].upper(),
                goal.upper(), new, size, note=note)


def _has_see_through(source: Path) -> bool:
    from PIL import Image
    try:
        with Image.open(source) as opened:
            return "A" in opened.getbands() or "transparency" in opened.info
    except Exception:  # noqa: BLE001 - a picture that does not open is skipped later
        return False


def _plan_sound(target: Target, suffix: str, chosen: Choices, ffmpeg: Ffmpeg,
                size: int) -> Item:
    if not ffmpeg.usable:
        raise SkipError(t("error.media_convert.no_ffmpeg"))
    encoder = ffmpeg.encoder(encode.SOUND_ENCODERS[chosen.sound])
    if not encoder:
        raise SkipError(t("error.media_convert.no_sound_encoder",
                          format=_FORMAT_NAMES[chosen.sound]))
    return Item(target.game_id, target.path, CONVERT, SOUND, suffix[1:].upper(),
                chosen.sound.upper(), _new_path(target.path, encode.SOUND_EXTENSION[chosen.sound]),
                size, encoder=encoder)


def _plan_video(target: Target, source: Path, suffix: str, chosen: Choices,
                devices: Sequence[Device], ffmpeg: Ffmpeg, size: int) -> Item:
    if not ffmpeg.usable:
        raise SkipError(t("error.media_convert.no_ffmpeg"))
    if blocked := _blocked_video(devices):
        raise SkipError(blocked)
    streams = streams_of(ffmpeg.path, source)
    if not streams.video:
        raise SkipError(t("error.media_convert.video_unreadable"))
    if (suffix in VIDEO_FAMILY and streams.video in played_by_all(devices)
            and streams.pixels in encode.PLAYABLE_PIXELS):
        raise SkipError(t("error.media_convert.plays"))
    container = chosen.container
    new = _new_path(target.path, encode.CONTAINER_EXTENSION[container])
    repackage = (streams.video in played_by_all(devices)
                 and streams.video in encode.CONTAINER_VIDEO[container]
                 and streams.pixels in encode.PLAYABLE_PIXELS)
    encoder = ""
    if not repackage:
        encoder = ffmpeg.encoder(encode.VIDEO_ENCODERS[chosen.video])
        if not encoder:
            raise SkipError(t("error.media_convert.no_video_encoder",
                              format=_CODEC_NAMES[chosen.video]))
    if new == target.path and repackage:
        raise SkipError(t("error.media_convert.plays"))
    if streams.audio and chosen.sound_in_video == KEEP_SOUND and not encode.audio_can_be_kept(
            container, streams.video if repackage else chosen.video, streams.audio):
        webm = container == encode.WEBM
        if not ffmpeg.encoder((encode.WEBM_SOUND_ENCODER if webm
                               else encode.MP4_SOUND_ENCODER,)):
            raise SkipError(t("error.media_convert.no_sound_encoder",
                              format="OPUS" if webm else "MP3"))
    return Item(target.game_id, target.path, REPACKAGE if repackage else CONVERT, VIDEO,
                _CODEC_NAMES.get(streams.video, streams.video.upper()),
                _CODEC_NAMES[streams.video if repackage else chosen.video], new, size,
                has_sound=bool(streams.audio), streams=streams, encoder=encoder)


_CODEC_NAMES = {encode.H264: "H.264", encode.VP9: "VP9", encode.AV1: "AV1", "hevc": "HEVC"}
_FORMAT_NAMES = {**_CODEC_NAMES, encode.MP3: "MP3", encode.OGG: "OGG", encode.MP4: "MP4",
                 encode.WEBM: "WebM", encode.PNG: "PNG", encode.JPG: "JPG",
                 encode.WEBP: "WebP"}


def _settle(item: Item, game_dir: Path, target: Target, chosen: Choices,
            claimed: set[tuple[str, str]]) -> Item:
    """Where the new file goes: a name taken by another file of the same plan is skipped
    whatever the choice, and one already on disk is left or replaced as chosen."""
    if item.new_path == item.path:
        return item
    key = (target.game_id, item.new_path)
    if key in claimed:
        raise SkipError(t("error.media_convert.same_name", name=PurePosixPath(item.new_path).name))
    claimed.add(key)
    onto = game_dir.joinpath(*PurePosixPath(item.new_path).parts)
    if not onto.exists():
        return item
    taken = Item(**{**_fields(item), "clash": True})
    if chosen.on_clash == LEAVE:
        return Item(**{**_fields(taken), "action": SKIP,
                       "reason": t("error.media_convert.exists",
                                   name=PurePosixPath(item.new_path).name)})
    return taken


def _fields(item: Item) -> dict[str, Any]:
    return {name: getattr(item, name) for name in item.__dataclass_fields__}


# --- the choices a plan has to offer --------------------------------------------------

_WHY = {
    (PICTURE, encode.PNG): "media_convert.why.picture_png",
    (PICTURE, encode.WEBP): "media_convert.why.picture_webp",
    (PICTURE, encode.JPG): "media_convert.why.picture_jpg",
    (SOUND, encode.MP3): "media_convert.why.sound_mp3",
    (SOUND, encode.OGG): "media_convert.why.sound_ogg",
    (CONTAINER, encode.MP4): "media_convert.why.container_mp4",
    (CONTAINER, encode.WEBM): "media_convert.why.container_webm",
    (VIDEO, encode.H264): "media_convert.why.video_h264",
    (VIDEO, encode.VP9): "media_convert.why.video_vp9",
    (VIDEO, encode.AV1): "media_convert.why.video_av1",
    (QUALITY, encode.STANDARD): "media_convert.why.quality_standard",
    (QUALITY, encode.HIGH): "media_convert.why.quality_high",
    (QUALITY, encode.SMALL): "media_convert.why.quality_small",
    (SOUND_IN_VIDEO, KEEP_SOUND): "media_convert.why.sound_keep",
    (SOUND_IN_VIDEO, REMOVE_SOUND): "media_convert.why.sound_remove",
    (ON_CLASH, LEAVE): "media_convert.why.clash_leave",
    (ON_CLASH, REPLACE): "media_convert.why.clash_replace",
}
_LABELS = {PICTURE: "media_convert.label.picture", SOUND: "media_convert.label.sound",
           CONTAINER: "media_convert.label.container", VIDEO: "media_convert.label.video",
           QUALITY: "media_convert.label.quality",
           SOUND_IN_VIDEO: "media_convert.label.sound_in_video",
           ON_CLASH: "media_convert.label.on_clash", KEEP: "media_convert.label.keep",
           WAIT: "media_convert.label.wait"}


def _choices(items: Sequence[Item], chosen: Choices, devices: Sequence[Device],
             ffmpeg: Ffmpeg, preferred: convert_preferences.Preferences) -> tuple[Choice, ...]:
    """Only the choices this plan has something to decide in."""
    working = [item for item in items if item.action != SKIP]
    pictures = [item for item in working if item.kind == PICTURE]
    sounds = [item for item in working if item.kind == SOUND]
    videos = [item for item in working if item.kind == VIDEO]
    lossy = (any(item.action == CONVERT and chosen.picture != encode.PNG for item in pictures)
             or any(item.action == CONVERT for item in sounds + videos))
    made: list[Choice] = []
    if pictures:
        made.append(_choice(PICTURE, chosen.picture, [
            (value, True) for value in _VALUES[PICTURE]], preferred.picture))
    if sounds:
        made.append(_choice(SOUND, chosen.sound, [
            (value, bool(ffmpeg.encoder(encode.SOUND_ENCODERS[value])))
            for value in _VALUES[SOUND]], preferred.sound))
    if videos:
        made.append(_choice(CONTAINER, chosen.container, [
            (encode.MP4, True), (encode.WEBM, chosen.video != encode.H264)]))
        made.append(_video_choice(chosen, devices, ffmpeg, preferred.video))
    if lossy:
        made.append(_choice(QUALITY, chosen.quality, [(v, True) for v in _VALUES[QUALITY]],
                            preferred.quality))
    if any(item.has_sound for item in videos):
        made.append(_choice(SOUND_IN_VIDEO, chosen.sound_in_video, [
            (v, True) for v in _VALUES[SOUND_IN_VIDEO]]))
    if any(item.clash for item in items):
        made.append(_choice(ON_CLASH, chosen.on_clash, [(v, True) for v in _VALUES[ON_CLASH]]))
    if working:
        kept = sum(item.size for item in working)
        made.append(Choice(KEEP, (
            Option(True, True, t("media_convert.why.keep_yes", size=_size_text(kept)),
                   preferred=preferred.keep is True),
            Option(False, False, t("media_convert.why.keep_no", size=_size_text(kept)),
                   preferred=preferred.keep is False)),
            t(_LABELS[KEEP])))
        made.append(Choice(WAIT, (
            Option(True, True, t("media_convert.why.wait_yes")),
            Option(False, False, t("media_convert.why.wait_no"))), t(_LABELS[WAIT])))
    return tuple(made)


def _choice(key: str, chosen: str, values: Sequence[tuple[str, bool]],
            preferred: str = "") -> Choice:
    """One choice whose recommendation is the first of its values and whose sentence is the
    catalog's; a value that cannot be made says so instead. `preferred` is the value the
    Preferred Formats hold, where one is."""
    options = []
    first = next((index for index, (_, available) in enumerate(values) if available), 0)
    for index, (value, available) in enumerate(values):
        said = t(_WHY[(key, value)])
        if not available:
            said = t("media_convert.why.unavailable", format=_FORMAT_NAMES.get(value, str(value)))
            if key == CONTAINER:
                said = t("media_convert.why.webm_needs")
        options.append(Option(value, index == first, said, available,
                              available and value == preferred))
    return Choice(key, tuple(options), t(_LABELS[key]))


def _video_choice(chosen: Choices, devices: Sequence[Device], ffmpeg: Ffmpeg,
                  preferred: str = "") -> Choice:
    """The codecs, each naming the devices that will not play it, the recommended one first
    in its sentence's order of preference."""
    blockers = _blockers(devices)
    best = recommended_video(devices, ffmpeg, chosen.container)
    options = []
    for codec in _CODECS:
        said = t(_WHY[(VIDEO, codec)])
        if blockers[codec]:
            names = ", ".join(blockers[codec])
            said = f"{said} {t('media_convert.why.cannot_play', devices=names)}"
        usable = _available(ffmpeg, codec) and not (
            chosen.container == encode.WEBM and codec == encode.H264)
        if not _available(ffmpeg, codec):
            said = t("media_convert.why.no_encoder", format=_CODEC_NAMES[codec])
        elif not usable:
            said = t("media_convert.why.webm_not_h264")
        options.append(Option(codec, codec == best, said, usable, usable and codec == preferred))
    if not any(option.recommended for option in options):
        options[0] = replace(options[0], recommended=True)
    return Choice(VIDEO, tuple(options), t(_LABELS[VIDEO]))


def _size_text(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1000 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1000
    return ""


# --- the job -------------------------------------------------------------------------


def start(targets: Sequence[Target], raw: Mapping[str, Any] | None = None,
          devices: Sequence[Device] | None = None) -> jobs.Job:
    """Convert these files. Refused before anything is converted when a choice is not one of
    its options or there is nowhere to keep the originals; a file that cannot be converted is
    skipped and said in the result."""
    if not targets:
        raise service_errors.RefusedError(t("error.media_convert.nothing"))
    devices = list(devices) if devices else default_devices()
    ffmpeg = Ffmpeg()
    chosen = _choices_for(raw, devices, ffmpeg)
    targets = list(dict.fromkeys(targets))
    folder = backup_folder.for_writing() if chosen.keep else None

    def work(job: jobs.Job) -> dict[str, Any]:
        return _convert_all(targets, chosen, devices, folder, job.reporter())

    return jobs.submit(jobs.KIND_MEDIA_CONVERT, work)


def _convert_all(targets: list[Target], chosen: Choices, devices: Sequence[Device],
                 folder: Path | None, reporter: JobReporter) -> dict[str, Any]:
    ffmpeg = Ffmpeg()
    converted = renamed = repackaged = kept = 0
    skipped: list[dict[str, str]] = []
    touched: dict[str, Path] = {}
    claimed: set[tuple[str, str]] = set()
    variant = media_service.media_settings()[0]
    for index, target in enumerate(targets):
        if not shutdown.requested():
            _wait_for_table(chosen, reporter, index, len(targets))
        if shutdown.requested():
            skipped.extend({"game_id": left.game_id, "path": left.path,
                            "reason": t("error.media_convert.stopped")}
                           for left in targets[index:])
            break
        reporter.progress(index, len(targets), t("said.media_convert",
                                                 file=PurePosixPath(target.path).name))
        try:
            game = game_lens.game_for_writing(target.game_id)
            game_dir = Path(str(game.full_path_game))
            item = _plan_file(target, game_dir, chosen, devices, ffmpeg, claimed, variant)
            if item.action == SKIP:
                raise SkipError(item.reason)
            took = _convert_one(item, game_dir, chosen, folder, ffmpeg)
        except (SkipError, service_errors.ServiceError) as exc:
            skipped.append({"game_id": target.game_id, "path": target.path,
                            "reason": why(exc)})
            continue
        except Exception:  # noqa: BLE001 - one file is skipped, the rest go on
            logger.warning("Converting %s of game %s failed", target.path, target.game_id,
                           exc_info=True)
            skipped.append({"game_id": target.game_id, "path": target.path,
                            "reason": t("error.media_convert.failed")})
            continue
        if item.action == RENAME:
            renamed += 1
        else:
            converted += 1
            repackaged += item.action == REPACKAGE
        kept += took
        touched[target.game_id] = game_dir
    for game_dir in touched.values():
        try:
            media_ops.reread(game_dir)
        except Exception:  # noqa: BLE001 - the files are converted; the result still says so
            logger.warning("Reading %s again after a conversion failed", game_dir,
                           exc_info=True)
    logger.info("Converted %s file(s) across %s game(s), %s renamed, %s copied first, "
                "%s skipped", converted, len(touched), renamed, kept, len(skipped))
    return {"converted": converted, "repackaged": repackaged, "renamed": renamed,
            "games": len(touched), "kept": kept, "skipped": skipped,
            "choices": chosen.made(), "defaulted": list(chosen.defaulted)}


def _wait_for_table(chosen: Choices, reporter: JobReporter, index: int, total: int) -> None:
    while (chosen.wait_while_playing and launch_state.current().launching
           and not shutdown.requested()):
        reporter.progress(index, total, t("said.media_convert_waiting"))
        _pause(_PAUSE_SECONDS)


def _convert_one(item: Item, game_dir: Path, chosen: Choices, folder: Path | None,
                 ffmpeg: Ffmpeg) -> int:
    """One file converted, and the copies taken first, as how many copies."""
    source = game_dir.joinpath(*PurePosixPath(item.path).parts)
    new = game_dir.joinpath(*PurePosixPath(item.new_path).parts)
    before = stamp(source)
    copies = 0
    with atomic_write.staged_for(new) as staged:
        _make(item, source, staged, chosen, ffmpeg)
        if stamp(source) != before:
            raise SkipError(t("error.media_convert.changed"))
        if folder is not None:
            backup_media.keep(folder, item.game_id, source, item.path, REASON_CONVERT)
            copies += 1
            if item.clash:
                backup_media.keep(folder, item.game_id, new, item.new_path, REASON_CONVERT)
                copies += 1
            if stamp(source) != before:
                raise SkipError(t("error.media_convert.changed"))
        if not item.clash and new != source and new.exists():
            raise SkipError(t("error.media_convert.exists",
                              name=PurePosixPath(item.new_path).name))
        shutil.copymode(source, staged)
    _record(game_dir, item.path, item.new_path,
            dropped=new == source or _removed(source, new))
    return copies


def _removed(source: Path, new: Path) -> bool:
    """The original gone, or the same file as the new one under another spelling, which is
    never removed. False where it could not be removed."""
    try:
        if source.exists() and new.exists() and os.path.samefile(source, new):
            return True
        source.unlink()
    except OSError:
        logger.warning("Converted %s but could not remove the original", source,
                       exc_info=True)
        return False
    return True


def _make(item: Item, source: Path, staged: Path, chosen: Choices, ffmpeg: Ffmpeg,
          on_progress: Callable[[float], None] | None = None,
          halted: Callable[[], bool] | None = None) -> None:
    if item.kind == PICTURE:
        if item.action == RENAME and encode.is_jpeg(source):
            shutil.copyfile(source, staged)
            return
        wanted = encode.picture_size(source)
        encode.convert_picture(source, staged, chosen.picture, chosen.quality)
        if encode.picture_size(staged) != wanted:
            raise SkipError(t("error.media_convert.size"))
        return
    binary = ffmpeg.path
    streams = item.streams or streams_of(binary, source)
    if item.kind == SOUND:
        argv = encode.sound_argv(binary, source, staged, chosen.sound, chosen.quality,
                                 item.encoder)
    else:
        argv = encode.video_argv(
            binary, source, staged, chosen.container, codec=chosen.video, encoder=item.encoder,
            quality=chosen.quality, threads=encode.threads_for(),
            keep_sound=chosen.sound_in_video == KEEP_SOUND, source_streams=streams,
            repackage=item.action == REPACKAGE)
    encode.run(argv, seconds=streams.seconds, on_progress=on_progress, halted=halted)
    encode.check_length(binary, streams, staged)


def _record(game_dir: Path, old: str, new: str, dropped: bool = True) -> None:
    """The new file is the person's, whoever made the one it came from, and the old name is
    no longer anyone's."""
    info = game_dir / f"{game_dir.name}.info"
    if not info.is_file():
        return
    try:
        from common.games.info_file import MetaConfig

        entries: dict[str, dict | None] = {new: {"source": {"host": "user"}}}
        if old != new and dropped:
            entries[old] = None
        MetaConfig(str(info)).rewrite_assets(entries)
    except Exception:  # noqa: BLE001 - the file is converted; provenance is what is lost
        logger.exception("Converted %s but could not record where it came from", new)


# --- a file on its way in ------------------------------------------------------------

AS_IS = "asis"

_TO = {PICTURE: PICTURE, SOUND: SOUND, VIDEO: CONTAINER}
_STREAMS: dict[tuple[str, int, int], encode.Streams] = {}


def streams_of(binary: Path, source: Path) -> encode.Streams:
    """What FFmpeg reports `source` holds, asked once per file as it stands: a sheet plans
    again at every change a person makes to it."""
    status = source.stat()
    key = (str(source), status.st_mtime_ns, status.st_size)
    if key not in _STREAMS:
        _STREAMS[key] = encode.streams(binary, source)
    return _STREAMS[key]


def kind_of(suffix: str) -> str:
    """`picture`, `sound` or `video` for an extension that converts, else empty."""
    suffix = suffix.lower()
    if suffix in CONVERTIBLE_IMAGE:
        return PICTURE
    if suffix in CONVERTIBLE_AUDIO:
        return SOUND
    if suffix in CONVERTIBLE_VIDEO:
        return VIDEO
    return ""


def needs_ffmpeg(kind: str) -> bool:
    return kind in (SOUND, VIDEO)


def row_choices(kind: str, row: Mapping[str, Any]) -> dict[str, Any]:
    """One file's own choices, as `resolve_choices` takes them: `to` names the format its
    kind is made as, and what the row leaves out takes the recommendation."""
    raw = {key: row[key] for key in (VIDEO, QUALITY, SOUND_IN_VIDEO)
           if row.get(key) is not None and row[key] != ""}
    if row.get("to") and row["to"] != AS_IS:
        raw[_TO[kind]] = row["to"]
    return raw


def plan_staged(source: Path, row: Mapping[str, Any], devices: Sequence[Device] | None = None,
                ffmpeg: Ffmpeg | None = None) -> Item:
    """What converting this file, which is not in a game yet, would do. The name is the
    placer's to give, so the item's new path is only the extension it carries. Raises
    `SkipError` where it cannot be converted."""
    devices = list(devices) if devices else default_devices()
    ffmpeg = ffmpeg or Ffmpeg()
    suffix = source.suffix.lower()
    kind = kind_of(suffix)
    if not kind:
        raise SkipError(t("error.media_convert.not_convertible"))
    chosen = _choices_for(row_choices(kind, row), devices, ffmpeg)
    target = Target("", f"file{suffix}")
    size = source.stat().st_size
    if kind == PICTURE:
        return _plan_picture(target, source, suffix, chosen, size)
    if kind == SOUND:
        return _plan_sound(target, suffix, chosen, ffmpeg, size)
    return _plan_video(target, source, suffix, chosen, devices, ffmpeg, size)


def convert_staged(source: Path, row: Mapping[str, Any], staged: Path,
                   devices: Sequence[Device] | None = None, ffmpeg: Ffmpeg | None = None,
                   on_progress: Callable[[float], None] | None = None,
                   halted: Callable[[], bool] | None = None) -> Item:
    """Write `source` converted to `staged`, whose extension must be the one `plan_staged`
    says. Nothing is placed and nothing is backed up: the original is still in the upload."""
    devices = list(devices) if devices else default_devices()
    ffmpeg = ffmpeg or Ffmpeg()
    item = plan_staged(source, row, devices, ffmpeg)
    if staged.suffix.lower() != PurePosixPath(item.new_path).suffix.lower():
        raise ValueError(staged.name)
    chosen = _choices_for(row_choices(item.kind, row), devices, ffmpeg)
    _make(item, source, staged, chosen, ffmpeg, on_progress, halted)
    return item


def target_suffix(item: Item) -> str:
    return PurePosixPath(item.new_path).suffix.lower()


_VALUE_LABEL_KEYS = {encode.STANDARD: "media_convert.value.standard",
                     encode.HIGH: "media_convert.value.high",
                     encode.SMALL: "media_convert.value.small",
                     KEEP_SOUND: "media_convert.value.keep",
                     REMOVE_SOUND: "media_convert.value.remove"}


def _option_view(option: Option) -> dict[str, Any]:
    value = option.value
    label = (t(_VALUE_LABEL_KEYS[str(value)]) if str(value) in _VALUE_LABEL_KEYS
             else _FORMAT_NAMES.get(str(value), str(value)))
    return {"value": value, "label": label, "recommended": option.recommended,
            "preferred": option.preferred, "why": option.why, "available": option.available}


def _choice_view(choice: Choice, value: str) -> dict[str, Any]:
    return {"key": choice.key, "label": choice.label, "value": value,
            "options": [_option_view(one) for one in choice.options]}


def staged_view(source: Path, row: Mapping[str, Any], devices: Sequence[Device] | None = None,
                ffmpeg: Ffmpeg | None = None) -> dict[str, Any]:
    """What one dropped file's row shows and offers: the formats it can be made as, each with
    a recommendation and a sentence, the settings that follow from the one chosen, and
    the one-line summary of them. `problem` is why it cannot be converted at all."""
    devices = list(devices) if devices else default_devices()
    ffmpeg = ffmpeg or Ffmpeg()
    suffix = source.suffix.lower()
    kind = kind_of(suffix)
    as_is = Option(AS_IS, False, t("media_convert.why.asis"))
    view: dict[str, Any] = {"kind": kind, "from": suffix[1:].upper(), "to": AS_IS,
                            "options": [], "settings": [], "line": "", "note": "",
                            "problem": "", "reencodes": False}
    if needs_ffmpeg(kind) and not ffmpeg.usable:
        view["problem"] = t("error.media_convert.no_ffmpeg")
        view["options"] = [_option_view(Option(value, index == 0, "", False))
                           for index, value in enumerate(_VALUES[SOUND if kind == SOUND
                                                                  else CONTAINER])
                           ] + [_option_view(as_is)]
        return view
    held = convert_preferences.read()
    chosen = resolve_choices(row_choices(kind, row), devices, ffmpeg, held)
    to = row.get("to") or ""
    asis = to == AS_IS
    if kind == PICTURE:
        first = _choice(PICTURE, chosen.picture, [(v, True) for v in _VALUES[PICTURE]],
                        held.picture)
        picked = chosen.picture
    elif kind == SOUND:
        first = _choice(SOUND, chosen.sound, [
            (v, bool(ffmpeg.encoder(encode.SOUND_ENCODERS[v]))) for v in _VALUES[SOUND]],
            held.sound)
        picked = chosen.sound
    else:
        first = _choice(CONTAINER, chosen.container, [
            (encode.MP4, True), (encode.WEBM, chosen.video != encode.H264)])
        picked = chosen.container
    view["options"] = [_option_view(one) for one in first.options] + [_option_view(as_is)]
    view["to"] = AS_IS if asis else picked
    if asis:
        return view
    try:
        item = plan_staged(source, row, devices, ffmpeg)
    except SkipError as exc:
        view["problem"] = why(exc)
        view["to"] = AS_IS
        return view
    settings: list[tuple[Choice, str]] = []
    parts: list[str] = []
    reencoded = item.kind == VIDEO and item.action == CONVERT
    view["reencodes"] = reencoded
    if item.kind == VIDEO:
        view["note"] = t("media_convert.note.reencoded" if reencoded
                         else "media_convert.note.repackaged")
        if reencoded:
            settings.append((_video_choice(chosen, devices, ffmpeg, held.video), chosen.video))
            parts.append(_CODEC_NAMES[chosen.video])
    lossy = (item.kind == SOUND or reencoded
             or (item.kind == PICTURE and chosen.picture != encode.PNG))
    if lossy:
        settings.append((_choice(QUALITY, chosen.quality, [(v, True) for v in _VALUES[QUALITY]],
                                 held.quality), chosen.quality))
        parts.append(t(_VALUE_LABEL_KEYS[chosen.quality]))
    if item.has_sound:
        settings.append((_choice(SOUND_IN_VIDEO, chosen.sound_in_video,
                                 [(v, True) for v in _VALUES[SOUND_IN_VIDEO]]),
                         chosen.sound_in_video))
        parts.append(t("media_convert.summary.sound_kept" if chosen.sound_in_video == KEEP_SOUND
                       else "media_convert.summary.sound_removed"))
    if item.note:
        view["note"] = item.note
    view["settings"] = [_choice_view(choice, value) for choice, value in settings]
    view["line"] = " \u00b7 ".join(parts)
    return view
