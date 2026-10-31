"""Collections as the pages and themes use them - names, icons, and what is in one."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import quote

from common.config.paths import COLLECTIONS_PATH, get_ini_config
from common.devices.device_registry import get_device_registry
from common.games.collection_store import CollectionStore
from common.i18n import t
from common.media.media_specs import IMAGE_FAMILY
from common.values import is_truthy

COLLECTION_ICONS_DIR = COLLECTIONS_PATH.parent / "collection_icons"
COLLECTION_IMAGE_KEY = "image"
# How many of its games' wheels stand in for a collection with no picture of its own.
WHEELS_SHOWN = 4


def get_collections_manager() -> CollectionStore:
    return CollectionStore(str(COLLECTIONS_PATH))


def ensure_collection_icons_dir() -> Path:
    COLLECTION_ICONS_DIR.mkdir(parents=True, exist_ok=True)
    return COLLECTION_ICONS_DIR


# What a collection icon may be. Checked on the way in, because the file is served
# straight back out and the extension is what a browser reads it as.
IMAGE_EXTENSIONS = set(IMAGE_FAMILY)


def _safe_icon_stem(filename: str) -> str:
    stem = Path(filename).stem.strip()
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._-")
    return stem or "collection"


def save_collection_icon(filename: str, content: bytes) -> str:
    """Write an icon and answer the name it was stored under.

    In core rather than in one surface: an icon is a property of a collection, so every
    surface that edits one needs to write it. A name already taken gets a counter rather than being
    overwritten - two collections may reasonably both offer "logo.png".
    """
    suffix = Path(filename).suffix.lower()
    if suffix not in IMAGE_EXTENSIONS:
        raise ValueError(t("error.collections.not_image"))

    icon_dir = ensure_collection_icons_dir()
    stem = _safe_icon_stem(filename)
    candidate = f"{stem}{suffix}"
    target = icon_dir / candidate
    counter = 1
    while target.exists():
        candidate = f"{stem}_{counter}{suffix}"
        target = icon_dir / candidate
        counter += 1
    target.write_bytes(content)
    return candidate


def collection_icon_path(filename: str | None) -> Path | None:
    """Where one icon is on disk, or None when the name escapes the icon folder."""
    name = Path(filename or "").name.strip()
    if not name:
        return None
    here = ensure_collection_icons_dir() / name
    return here if here.is_file() else None


# What names a collection: the setting the frontend opens on, and the field on this
# install's row holding the one it last had open for Last Collection to reopen.
_STARTUP = ("behavior", "startup_collection")
_LAST = "last_collection"


def follow_rename_in_settings(old_name: str, new_name: str) -> None:
    """Move the settings that name a collection along with it.

    The setting holds a name because a collection has no id to hold instead, so without
    this a rename leaves it naming a collection that is not there. The frontend logs the
    failure and shows the whole library, which reads as a setting nobody set rather than
    as something broken.
    """
    store = get_ini_config()
    if str(store.value(*_STARTUP) or "").strip() == old_name:
        store.set_value(*_STARTUP, new_name)
        store.save()
    row = get_device_registry()
    if str(row.self_get(_LAST, "") or "").strip() == old_name:
        row.set_self(**{_LAST: new_name})


def forget_in_settings(name: str) -> None:
    """Clear the settings that name a collection when it is deleted.

    The alternative is leaving it naming something gone, which shows the whole library
    and raises on every start. This shows the whole library and says so on the settings
    screen, which is the same outcome told honestly.
    """
    store = get_ini_config()
    if str(store.value(*_STARTUP) or "").strip() == name:
        store.set_value(*_STARTUP, "")
        store.save()
    row = get_device_registry()
    if str(row.self_get(_LAST, "") or "").strip() == name:
        row.set_self(**{_LAST: None})


def collection_icon_url(filename: str | None) -> str:
    filename = Path(filename or "").name.strip()
    if not filename:
        return ""
    return f"/collection_icons/{quote(filename)}"


def get_collection_image(collection: str) -> str:
    manager = get_collections_manager()
    if collection not in manager:
        return ""
    return manager.get_image(collection)


def get_collection_image_url(collection: str) -> str:
    return collection_icon_url(get_collection_image(collection))


def get_collections_metadata() -> list[dict]:
    manager = get_collections_manager()
    rows = []
    for name in manager.get_collections_name():
        is_filter = manager.is_filter_based(name)
        image = manager.get_image(name)
        rows.append({
            "name": name,
            "type": "filter" if is_filter else "vpsid",
            "is_filter": is_filter,
            "image": image,
            "image_url": collection_icon_url(image),
            "in_frontend": manager.get_in_frontend(name),
            # The stored membership, whatever else the collection carries. Criteria and
            # named members are combinable, so reporting null for
            # anything that filters hid the members it also held.
            "game_count": len(manager.get_members(name)),
        })
    return rows


def offered(rows: list[dict], showing: str = "") -> list[dict]:
    """The rows the frontend offers, and `showing` whether offered or not."""
    return [row for row in rows
            if row["in_frontend"] or (showing and row["name"] == showing)]


def get_frontend_collections(showing: str = "") -> list[dict]:
    """The collections the frontend offers, and `showing` whether offered or not."""
    return offered(get_collections_metadata(), showing)


def save_filter_collection(
    name: str,
    letter: str = "All",
    theme: str = "All",
    game_type: str = "All",
    manufacturer: str = "All",
    year: str = "All",
    rating: str = "All",
    rating_or_higher: object = False,
    sort_by: str = "Alpha",
    order_by: str = "desc",
) -> None:
    with get_collections_manager().mutate() as manager:
        manager.add_filter_collection(
            name,
            letter,
            theme,
            game_type,
            manufacturer,
            year,
            rating,
            "true" if is_truthy(rating_or_higher) else "false",
            sort_by,
            order_by,
        )
