"""Value coercion shared across the codebase.

Infrastructure: nothing here knows about games, hardware or any outside service,
so anything may depend on it. It exists because `is_truthy` lived in
`game_metadata` and `config_access` had to reach up into the domain to read a
boolean out of an ini file.
"""

from __future__ import annotations

import re
from typing import Any

_TRUE_VALUES = {"1", "true", "yes", "on"}


def is_truthy(value: Any, default: bool = False) -> bool:
    """Whether a config or metadata value means yes.

    `default` covers the two ways a value can be absent rather than false - None
    and empty string - so a missing setting can still opt in.
    """
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    normalized = str(value).strip().lower()
    if normalized == "":
        return default
    return normalized in _TRUE_VALUES


def parse_version(value: Any) -> tuple[int, ...]:
    """A version string as a comparable tuple. `()` when it is not a version.

    Stops at the first non-numeric part, so `3.0.1-beta.2` compares as `(3, 0, 1)`
    rather than as nothing - a theme tested against a beta still means 3.0.1.
    """
    parts: list[int] = []
    for part in str(value or "").strip().lstrip("vV").split("."):
        digits = re.match(r"\d+", part)
        if not digits:
            break
        parts.append(int(digits.group()))
        if digits.end() < len(part):
            break
    return tuple(parts)


def newer_version(this: tuple[int, ...], than: tuple[int, ...]) -> bool:
    """Whether `this` is a later version than `than`, with 3.0 and 3.0.0 the same one."""
    width = max(len(this), len(than))
    return this + (0,) * (width - len(this)) > than + (0,) * (width - len(than))


# The quarter turns VPinFE applies to its own screen, clockwise.
QUARTERS = (0, 90, 180, 270)


def quarter_turn(rotation: Any) -> int:
    """A stored rotation as the quarter turn VPinFE applies, 0 where it applies none."""
    try:
        turned = int(str(rotation).strip())
    except ValueError:
        return 0
    return turned if turned in QUARTERS else 0
