"""The loading video, loading image and launch sound a theme names in its `config.json`.

A theme names them under `loading_screen` as `video`, `image` and `audio`, each a path
inside the theme's own folder. A name that is not a file of the right type there, or that
leaves the folder, is no name at all.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath

from common.config import loading_choices as choices

from .media_specs import AUDIO_FAMILY, IMAGE_FAMILY, VIDEO_FAMILY

BLOCK = "loading_screen"
KINDS = (choices.VIDEO, choices.IMAGE, choices.AUDIO)
_FAMILIES = {choices.VIDEO: VIDEO_FAMILY, choices.IMAGE: IMAGE_FAMILY,
             choices.AUDIO: AUDIO_FAMILY}


def _inside(theme_dir: Path, name: str, kind: str) -> str:
    leaf = PurePosixPath(name.replace("\\", "/"))
    if not name or leaf.is_absolute() or ".." in leaf.parts or ":" in name:
        return ""
    if leaf.suffix.lower() not in _FAMILIES[kind]:
        return ""
    path = theme_dir.joinpath(*leaf.parts)
    return leaf.as_posix() if path.is_file() and not path.is_symlink() else ""


def files(theme_dir: Path | None, config: dict | None) -> dict[str, str]:
    """The files the theme names, by kind, relative to its folder with `/` between parts;
    a kind it names nothing usable for maps to ""."""
    block = (config or {}).get(BLOCK)
    named = block if isinstance(block, dict) else {}
    return {kind: _inside(theme_dir, str(named.get(kind) or "").strip(), kind)
            if theme_dir is not None else ""
            for kind in _FAMILIES}


def draws_its_own(config: dict | None, on_by_default: bool) -> bool:
    """Whether the theme draws its own loading screen: it turned core's off, or said nothing
    and its contract leaves core's off."""
    block = (config or {}).get(BLOCK)
    stated = block.get("enabled") if isinstance(block, dict) else None
    return not (stated if isinstance(stated, bool) else on_by_default)
