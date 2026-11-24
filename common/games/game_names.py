"""How a file's name is held against a game's name.

A game folder is often named with its maker and year in a trailing parenthesis, and a
file arrives named for the game without it, with a media token in front.
"""

from __future__ import annotations

import re

_TRAILING_NOTE = re.compile(r"\s*\([^)]*\)\s*$")


def squashed(text: str) -> str:
    """`text` as lowercase letters and digits only."""
    return re.sub(r"[^a-z0-9]", "", text.lower())


def of_game(name: str) -> str:
    """A game's name as it is compared: the trailing parenthesis dropped, then squashed."""
    return squashed(_TRAILING_NOTE.sub("", name))


def names_game(text: str, game_name: str) -> bool:
    """Whether `text` - a file's stem, a folder's name - carries the game's name."""
    game = of_game(game_name)
    return bool(game) and game in squashed(text)
