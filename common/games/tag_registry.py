"""What a tag means and what color it wears, where somebody has said."""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from pathlib import Path
from typing import Any

from common import service_errors
from common.atomic_write import write_atomic
from common.config import paths
from common.games.game_metadata import normalize_tag
from common.i18n import t

logger = logging.getLogger("vpinfe.common.games.tag_registry")

SCHEMA = 1
COLORS = ("red", "orange", "amber", "green", "teal", "blue", "purple", "pink", "gray")
DERIVED = ("green", "teal", "blue", "purple", "pink", "gray")

_lock = threading.RLock()


def _path() -> Path:
    return paths.TAGS_PATH


def load() -> dict[str, dict[str, str]]:
    try:
        held = json.loads(_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        logger.warning("tags: could not read %s", _path(), exc_info=True)
        return {}
    tags = held.get("tags") if isinstance(held, dict) else None
    return {str(name): {"description": str((one or {}).get("description") or ""),
                        "color": str((one or {}).get("color") or "")}
            for name, one in (tags or {}).items() if normalize_tag(str(name))}


def _save(entries: dict[str, dict[str, str]]) -> None:
    body = {"schema": SCHEMA, "tags": dict(sorted(entries.items(),
                                                  key=lambda one: one[0].casefold()))}
    _path().parent.mkdir(parents=True, exist_ok=True)
    write_atomic(_path(), lambda handle: json.dump(body, handle, indent=2,
                                                   ensure_ascii=False))


def derived_color(name: str) -> str:
    digest = hashlib.sha1(normalize_tag(name).encode("utf-8")).digest()
    return DERIVED[digest[0] % len(DERIVED)]


def describe(name: str, entries: dict[str, dict[str, str]] | None = None) -> dict[str, Any]:
    held = (load() if entries is None else entries).get(normalize_tag(name)) or {}
    chosen = str(held.get("color") or "")
    return {"description": str(held.get("description") or ""),
            "color": chosen or derived_color(name), "chosen": bool(chosen)}


def put(name: str, *, description: str | None = None,
        color: str | None = None) -> dict[str, Any]:
    """Write down a tag, or change what it says. `color` "" goes back to the derived one."""
    said = normalize_tag(name)
    if not said:
        raise service_errors.RefusedError(t("error.tags.no_name"))
    if color and color not in COLORS:
        raise service_errors.RefusedError(t("error.tags.no_such_color", color=color),
                                          details={"colors": list(COLORS)})
    with _lock:
        entries = load()
        held = entries.setdefault(said, {"description": "", "color": ""})
        if description is not None:
            held["description"] = " ".join(str(description).split())
        if color is not None:
            held["color"] = color
        _save(entries)
    return describe(said, entries)


def moved(sources: list[str], into: str) -> None:
    """Carry an entry to the name a rename or merge left. The survivor keeps its own where
    it has one; otherwise it takes the first source's."""
    survivor = normalize_tag(into)
    with _lock:
        entries = load()
        taken = [entries.pop(one) for one in (normalize_tag(name) for name in sources)
                 if one != survivor and one in entries]
        if not taken:
            return
        if survivor and survivor not in entries:
            entries[survivor] = taken[0]
        _save(entries)


def dropped(name: str) -> None:
    with _lock:
        entries = load()
        if entries.pop(normalize_tag(name), None) is not None:
            _save(entries)
