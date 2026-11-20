"""The files a person added for the loading screen and the launch audio.

They live in `launch_media` in VPinFE's settings folder, all in one place, told apart by
file type: a video, a picture or a sound. The settings name the one in use for each of the
three by its file name.
"""

from __future__ import annotations

import shutil
import threading
from collections.abc import Callable
from pathlib import Path

from common import atomic_write, service_errors
from common.config import loading_choices as choices
from common.config import paths
from common.config.config_access import ConfigSource, cfg_get
from common.config.paths import get_ini_config
from common.i18n import t

from .media_specs import AUDIO_FAMILY, IMAGE_FAMILY, VIDEO_FAMILY

KINDS = (choices.VIDEO, choices.IMAGE, choices.AUDIO)
EXTENSIONS = {choices.VIDEO: VIDEO_FAMILY, choices.IMAGE: IMAGE_FAMILY,
              choices.AUDIO: AUDIO_FAMILY}
_FILE_KEYS = {choices.VIDEO: choices.VIDEO_FILE_KEY, choices.IMAGE: choices.IMAGE_FILE_KEY,
              choices.AUDIO: choices.SOUND_FILE_KEY}

_placing = threading.Lock()


def folder() -> Path:
    return paths.LAUNCH_MEDIA_DIR


def kind_of(name: str) -> str:
    """Which of the three a file is, by its type, or "" for a type none of them takes."""
    suffix = Path(name).suffix.lower()
    return next((kind for kind in KINDS if suffix in EXTENSIONS[kind]), "")


def names(kind: str) -> list[str]:
    """The files of one kind, by name."""
    try:
        held = [one.name for one in folder().iterdir() if one.is_file()]
    except OSError:
        return []
    return sorted((one for one in held if kind_of(one) == kind), key=str.casefold)


def path_of(name: str) -> Path | None:
    """The file with this name, or None unless it is a file of a kind in the folder itself."""
    if not name or Path(name).name != name or not kind_of(name):
        return None
    path = folder() / name
    return path if path.is_file() and not path.is_symlink() else None


def _place(name: str, write: Callable[[Path], object]) -> str:
    """Write a new file into the folder under `name`, or under a numbered name when it is
    taken, so nothing in the folder is replaced. Returns the name it has there."""
    if not kind_of(name):
        raise service_errors.RefusedError(t("error.launch_media.type", name=name))
    leaf = Path(name)
    try:
        with _placing:
            target = folder() / leaf.name
            number = 1
            while target.exists():
                number += 1
                target = folder() / f"{leaf.stem} ({number}){leaf.suffix}"
            target.parent.mkdir(parents=True, exist_ok=True)
            with atomic_write.staged_for(target) as staged:
                write(staged)
    except OSError as exc:
        raise service_errors.RefusedError(t("error.launch_media.write", name=leaf.name)) from exc
    return target.name


def add(source: Path) -> str:
    """Copy a file into the folder and return the name it has there."""
    return _place(source.name, lambda staged: shutil.copy2(source, staged))


def add_bytes(name: str, data: bytes) -> str:
    """The same for a file that arrived as bytes."""
    return _place(Path(name).name, lambda staged: staged.write_bytes(data))


def chosen(kind: str, config: ConfigSource | None = None) -> str:
    """The name the settings hold for this kind, or "" when it names no file now."""
    name = cfg_get(config or get_ini_config(), choices.SECTION, _FILE_KEYS[kind]).strip()
    return name if path_of(name) else ""


def used_by(kind: str, config: ConfigSource | None = None) -> str:
    """The setting that plays the chosen file of this kind, or "" when none does: Loading
    Screen for a video or picture, Launch Audio for a sound."""
    source = config or get_ini_config()
    if kind == choices.AUDIO:
        main = choices.sound_or_default(cfg_get(source, choices.SECTION, choices.SOUND_KEY))
        fallback = choices.sound_fallback_or_default(
            cfg_get(source, choices.SECTION, choices.SOUND_FALLBACK_KEY), main)
        rungs = choices.sound_ladder(main, fallback)
        return choices.SOUND_KEY if choices.MY_SOUND in rungs else ""
    main = choices.screen_or_default(cfg_get(source, choices.SECTION, choices.SCREEN_KEY))
    fallback = choices.fallback_or_default(
        cfg_get(source, choices.SECTION, choices.FALLBACK_KEY), main)
    mine = choices.MY_VIDEO if kind == choices.VIDEO else choices.MY_IMAGE
    return choices.SCREEN_KEY if mine in choices.ladder(main, fallback) else ""


def in_use(name: str, config: ConfigSource | None = None) -> str:
    """The setting that plays this file, or "" when none does."""
    kind = kind_of(name)
    if not kind or chosen(kind, config).casefold() != name.casefold():
        return ""
    return used_by(kind, config)
