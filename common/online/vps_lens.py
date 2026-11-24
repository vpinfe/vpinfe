"""VPSdb as this install reports it: one entry, its builds, a search, and how fresh any
of that is.

Deliberately unordered by anything resembling quality or likeness. A scorer over exactly
that question is confidently wrong more often than not, so an order implying "yours is
probably this one" would carry a confidence nothing supports.
"""

from __future__ import annotations

from typing import Any

from common import service_errors, timestamps
from common.config.paths import get_ini_config
from common.games import mods
from common.i18n import t
from common.online import sites, vps_kinds, vpsdb_sync
from common.uploads.asset_import_service import vps_folder_name


def _resource(entry: dict) -> dict:
    """One VPSdb entry as it is reported, for the search and for a single lookup.

    One builder because both answer with the same shape: two copies of a field list drift
    a field at a time and nothing catches it.
    """
    return {
        "vps_id": entry.get("id"), "name": entry.get("name"),
        "manufacturer": entry.get("manufacturer"), "year": entry.get("year"),
        "type": entry.get("type"), "folder_name": vps_folder_name(entry),
        "releases": len(entry.get("tableFiles") or []),
        "img_url": picture(entry),
        "url": entry_address(str(entry.get("id") or "")),
    }


def picture(entry: dict) -> str:
    """The picture VPS's own page leads with for this machine, or "" for none."""
    if entry.get("imgUrl"):
        return str(entry["imgUrl"])
    for listed_as in ("b2sFiles", "tableFiles"):
        pictured = [one for one in (entry.get(listed_as) or []) if one.get("imgUrl")]
        if pictured:
            return str(max(pictured, key=lambda one: one.get("createdAt") or 0)["imgUrl"])
    return ""


def entry_address(vps_id: str) -> str:
    return f"https://virtualpinballspreadsheet.github.io/?game={vps_id}"


def _entry_or_refuse(vps_id: str) -> dict:
    from common.games.game_service import load_vpsdb

    found = next((one for one in load_vpsdb()
                  if str(one.get("id") or "") == vps_id), None)
    if found is None:
        raise service_errors.NotFoundError(t("error.uploads.vps_not_found"),
                                           details={"vps_id": vps_id})
    return found


def entry(vps_id: str) -> dict[str, Any]:
    """What a game is matched to, so a surface can show the match rather than its id."""
    return _resource(_entry_or_refuse(vps_id))


def _release(release: dict, entry: dict) -> dict:
    """One build of a machine, in what somebody would recognize their own copy by.

    Version and authors, because that is what a `.vpx` carries and so what a person can
    compare against. Not a filename: VPS records one on almost no releases, so a surface
    built around matching names would be empty almost always.
    """
    links = [{"url": url, "broken": bool(item.get("broken"))}
             for item in (release.get("urls") or [])
             if sites.is_web(url := str(item.get("url") or "").strip())]
    return {
        "vps_file_id": str(release.get("id") or ""),
        "version": str(release.get("version") or ""),
        "authors": [str(name) for name in (release.get("authors") or [])],
        "format": str(release.get("tableFormat") or ""),
        "features": [str(word) for word in (release.get("features") or [])],
        "comment": str(release.get("comment") or ""),
        "img_url": str(release.get("imgUrl") or ""),
        "updated_at": _as_iso(release.get("updatedAt")),
        "url": next((one["url"] for one in links if not one["broken"]),
                    links[0]["url"] if links else ""),
        "urls": links,
        "mod_of": mods.based_on(release, entry),
    }


def _as_iso(stamp: Any) -> str:
    """VPS keeps epoch milliseconds; everything else here is ISO 8601 UTC seconds."""
    try:
        return timestamps.epoch_to_iso(int(stamp) // 1000)
    except (TypeError, ValueError):
        return ""


def releases(vps_id: str, listed_as: str = "tableFiles") -> dict[str, Any]:
    """Every record of one kind this machine has, in the order VPSdb holds them.

    `listed_as` is VPS's own key, not ours: one of theirs is two of ours, so a local kind
    cannot address this. `tableFiles` is not in the kind map on purpose - which build a
    `.vpx` is gets answered by binding a release - so it is named separately.
    """
    if listed_as != "tableFiles" and listed_as not in vps_kinds.BY_LISTING:
        raise service_errors.RefusedError(
            t("error.uploads.unknown_kind"),
            details={"listed_as": listed_as,
                     "known": ["tableFiles", *sorted(vps_kinds.BY_LISTING)]})
    found = _entry_or_refuse(vps_id)
    listed = [_release(one, found) for one in (found.get(listed_as) or [])]
    mods.hold(one["mod_of"] for one in listed)
    return {"releases": listed}


def search(term: str = "", limit: int = 20) -> dict[str, Any]:
    from common.games.game_service import search_vpsdb

    return {"results": [_resource(one) for one in search_vpsdb(term, limit=limit)]}


def sync_state() -> dict[str, Any]:
    """What a surface needs to say how fresh the answers it is giving are."""
    from common.games.game_service import load_vpsdb

    config = get_ini_config()
    return {"schedule": vpsdb_sync.schedule(config),
            "checked": vpsdb_sync.checked_at(),
            "due": vpsdb_sync.due(config),
            "entries": len(load_vpsdb())}


def sync_now() -> dict[str, Any]:
    """Asked for, so it ignores the schedule - a manual sync that answered "not due" would
    be reporting a rule back to the person overriding it. A catalog that changed is stamped
    into the arrivals and compared like one the watch found."""
    from common.online import sources_watch, vps_source

    result = vpsdb_sync.sync(get_ini_config(), True)
    sources_watch.refreshed(vps_source.SOURCE, bool(result.get("changed")))
    sources_watch.compare_if_owed()
    return result
