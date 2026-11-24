"""A file kept with a table that is none of the kinds VPinFE shows.

A library moved in from another frontend carries folders of art this one has no slot for.
The file is kept under the naming standard so it stays with its table and nothing that
reads the folder takes it for something else: `(Label) Name.ext` in the game's media
folder, `(Label 2) Name.ext` for a later one. A token no kind owns reads as no kind.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from common.games import media_placement
from common.i18n import t
from common.media.media_specs import (
    AUDIO_FAMILY,
    CONVERTIBLE_AUDIO,
    CONVERTIBLE_IMAGE,
    CONVERTIBLE_VIDEO,
    DOC_FAMILY,
    IMAGE_FAMILY,
    MEDIA_SPECS,
    VIDEO_FAMILY,
    page_token,
)

KEPT_EXTENSIONS = frozenset((*IMAGE_FAMILY, *VIDEO_FAMILY, *AUDIO_FAMILY, *DOC_FAMILY,
                             *CONVERTIBLE_IMAGE, *CONVERTIBLE_VIDEO, *CONVERTIBLE_AUDIO))

_WORD = re.compile(r"[A-Za-z0-9]+")


def label_of(text: str) -> str:
    """A name a person gave a folder as a token: the words joined and capitalized, with
    nothing a file name or a token cannot hold. "Instruction Cards" is `InstructionCards`;
    a word already in capitals keeps them."""
    words = _WORD.findall(text)
    return "".join(word if word.isupper() else word[:1].upper() + word[1:] for word in words)


def reserved(label: str) -> bool:
    """A label a kind reads as its own, in any case: a file under it would stop being
    kept and become that kind's."""
    wanted = label.lower()
    return any(token[1:-1].lower() == wanted
               for spec in MEDIA_SPECS for token in (spec.token, *spec.alt_tokens) if token)


def target_name(label: str, stem: str, extension: str, page: int = 1) -> str:
    """The file name a kept file takes. Refuses a label that is empty, is not one word of
    letters and digits, or that a kind owns, and an extension that is not media."""
    if not _WORD.fullmatch(label):
        raise media_placement.UnplaceableError(t("error.games.other_label"))
    if reserved(label):
        raise media_placement.UnplaceableError(t("error.games.other_label_taken", label=label))
    extension = extension.lower()
    if extension not in KEPT_EXTENSIONS:
        raise media_placement.UnplaceableError(t("error.games.other_extension",
                                                 extension=extension))
    if page < 1:
        raise media_placement.UnplaceableError(t("error.games.no_such_page", slot=label,
                                                 page=page))
    return f"{page_token(f'({label})', page)} {stem}{extension}"


def place(game_dir: str | Path, label: str, stem: str, source: str | Path,
          page: int = 1, *, replace: bool = False) -> Path:
    """Copy `source` into the game's media folder as `(label) stem` and answer where it
    went. A file of that name is left alone and FileExistsError raised, unless `replace`,
    which keeps a copy in Backups first. No other file is touched."""
    game_dir = Path(game_dir)
    target = game_dir / "medias" / target_name(label, stem, Path(source).suffix, page)
    if target.is_file():
        if not replace:
            raise FileExistsError(target)
        media_placement.keep_displaced(game_dir, [target])
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return target
