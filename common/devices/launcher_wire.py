"""A launcher as a caller names it, and as `/config/launchers` holds it.

Callers describe one with `launcher_id` and `display_name`; the list calls those `id` and
`name` and keeps the tables that run on it. No domain imports, so a device client may use
it.
"""

from __future__ import annotations

from typing import Any

_ITEM_KEYS = ("app", "enabled", "owns_ini", "settings")


def to_item(launcher_id: str, body: dict[str, Any],
            held: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """The list item for `body`. `held` is the list as the install has it: a body names no
    tables, so the ones already assigned to this launcher stay assigned."""
    was = next((one for one in held or [] if one.get("id") == launcher_id), None)
    item: dict[str, Any] = {"id": launcher_id,
                            "name": str(body.get("display_name") or "").strip(),
                            "tables": list((was or {}).get("tables") or [])}
    item.update({key: body[key] for key in _ITEM_KEYS if key in body})
    return item


def from_item(item: dict[str, Any]) -> dict[str, Any]:
    """What a caller was given before launchers were a list."""
    return {"launcher_id": item.get("id", ""), "display_name": item.get("name", ""),
            **{key: item[key] for key in _ITEM_KEYS if key in item}}
