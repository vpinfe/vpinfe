"""What a VPS release is a mod of, as VPS records it, and whether the library holds that."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from common.games import owned
from common.games.game_service import find_vps_release_and_entry

MOD_TAG = "MOD"


def mod_of(vps_file_id: str) -> dict[str, Any] | None:
    """What the release with this id is a mod of, or None where it is not one."""
    release, entry = find_vps_release_and_entry(vps_file_id)
    return based_on(release, entry) if release else None


def _link_of(release: dict) -> str:
    """The base `release` is linked to, or "" for none; a link to itself is no link."""
    link = str(release.get("parentId") or "").strip()
    return link if link != str(release.get("id") or "") else ""


def is_mod(release: dict) -> bool:
    return bool(_link_of(release)) or MOD_TAG in (release.get("features") or [])


def based_on(release: dict, entry: dict) -> dict[str, Any] | None:
    """What `release`, listed under `entry`, is a mod of, or None where it is not one.

    A mod is linked to the release it is based on (`parentId`) or tagged `MOD`. A link to
    itself is no link, and neither is one the catalog cannot find. Where there is no link,
    `vps_file_id` is empty and `note` is VPS's own comment on a tagged mod, if it has one.
    `game_id` and `table_id` stay empty until `hold` fills them.
    """
    link = _link_of(release)
    linked = bool(link)
    tagged = MOD_TAG in (release.get("features") or [])
    if not is_mod(release):
        return None
    parent, machine = find_vps_release_and_entry(link) if linked else ({}, {})
    said: dict[str, Any] = {"vps_file_id": "", "version": "", "authors": [], "game": "",
                            "url": "", "note": "", "game_id": "", "table_id": ""}
    if not parent:
        said["note"] = str(release.get("comment") or "").strip() if tagged else ""
        return said
    from common.online.vps_lens import entry_address

    machine_id = str(machine.get("id") or "")
    return {**said, "vps_file_id": link,
            "version": str(parent.get("version") or ""),
            "authors": [str(name) for name in (parent.get("authors") or [])],
            "game": (str(machine.get("name") or "")
                     if machine_id != str(entry.get("id") or "") else ""),
            "url": entry_address(machine_id)}


def hold(mods: Iterable[dict[str, Any] | None]) -> None:
    """Say where the library holds each release these are mods of, in one read."""
    linked = [one for one in mods if one and one.get("vps_file_id")]
    if not linked:
        return
    found = owned.held(one["vps_file_id"] for one in linked)
    for one in linked:
        where = found.get(one["vps_file_id"]) or {}
        one["game_id"] = where.get("game_id", "")
        one["table_id"] = where.get("table_id", "")
