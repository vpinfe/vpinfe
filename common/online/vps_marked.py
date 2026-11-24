"""The marks drawn against the catalog: what the Wanted shelf lists, and every mark the
Marks grid lists.

`vps_marks` knows ids only. This reads `library_vps_state.discovery`'s answer to say what
each marked id is, and leaves out the ones the catalog no longer lists.
"""

from __future__ import annotations

from collections.abc import Collection
from typing import Any

from common.online import vps_marks

# The scopes whose id is a release, or a picture a source has replaced.
_RELEASE_SCOPES = (vps_marks.RELEASE, vps_marks.UPDATES, vps_marks.VERSION)


def _releases_by_id(found: dict[str, Any], only: Collection[str] | None = None
                    ) -> dict[str, tuple[dict[str, Any], str, dict[str, Any]]]:
    """Every release of the catalog by its id, with its entry and the kind it is listed under.
    With `only`, the walk stops once each of those is found."""
    out: dict[str, tuple[dict[str, Any], str, dict[str, Any]]] = {}
    left = set(only) if only is not None else None
    for row in found.get("rows") or []:
        for listing, releases in (row.get("releases") or {}).items():
            for release in releases:
                out[str(release["id"])] = (row, listing, release)
                if left is not None:
                    left.discard(str(release["id"]))
        if left is not None and not left:
            break
    return out


def wanted(found: dict[str, Any], marks: vps_marks.Marks) -> list[dict[str, Any]]:
    """The things marked Wanted, the newest mark first: a game by `scope` `game`, a release
    by `release`. `vps_id` is always the entry, `id` the thing itself."""
    entries = {str(row["vps_id"]): row for row in found.get("rows") or []}
    names = [str(one["name"]) for one in found.get("people") or []]
    releases = _releases_by_id(found) if marks.with_state(
        vps_marks.WANTED, (vps_marks.RELEASE,)) else {}
    out = []
    for one in marks.with_state(vps_marks.WANTED, vps_marks.WANTABLE):
        if one["scope"] == vps_marks.GAME:
            row = entries.get(one["id"])
            if row is None or not (row.get("name") or "").strip():
                continue
            out.append({"scope": vps_marks.GAME, "id": one["id"], "vps_id": one["id"],
                        "game": str(row["name"]).strip(), "img_url": row.get("img_url") or "",
                        "in_library": bool(row.get("in_library")), "listing": "",
                        "version": "", "authors": [], "held": False,
                        "since": one["since"]})
            continue
        found_release = releases.get(one["id"])
        if found_release is None:
            continue
        row, listing, release = found_release
        out.append({"scope": vps_marks.RELEASE, "id": one["id"], "vps_id": str(row["vps_id"]),
                    "game": str(row.get("name") or "").strip(),
                    "img_url": row.get("img_url") or "",
                    "in_library": bool(row.get("in_library")), "listing": listing,
                    "version": release.get("version") or "",
                    "authors": [names[at] for at in release.get("by") or [] if at < len(names)],
                    "held": bool(release.get("held")), "since": one["since"]})
    return [one for one in out if one["game"]]


def listed(found: dict[str, Any], marks: vps_marks.Marks,
           state: str = "") -> list[dict[str, Any]]:
    """Every mark that names something the catalog lists, the newest first: what the Marks
    grid has a row for. Unlike `wanted` it holds every state and every scope.

    `vps_id` is always the entry, `id` and `detail` the mark's own. `listing` is the VPS
    kind the release or the gap is of (a gap on a tab only a source fills carries the media
    kind), `media` the media kind of a picture's mark, and `version` the release's version -
    or, for a skipped one, the version skipped. A mark whose entry or release the catalog no
    longer lists is left out: nothing is drawn for it, and nothing can be done to it. `state`
    keeps only the marks in that state."""
    entries = {str(row["vps_id"]): row for row in found.get("rows") or []}
    names = [str(one["name"]) for one in found.get("people") or []]
    chosen = [one for one in marks.found if not state or one["state"] == state]
    wanted_ids = {one["id"] for one in chosen if one["scope"] in _RELEASE_SCOPES}
    by_release = _releases_by_id(found, wanted_ids) if wanted_ids else {}
    out = []
    for one in sorted(chosen, key=lambda mark: mark["since"], reverse=True):
        scope, mark_id, detail = one["scope"], one["id"], one.get("detail") or ""
        row: dict[str, Any] | None = None
        listing = media = version = ""
        authors: list[str] = []
        held = False
        if scope in (vps_marks.GAME, vps_marks.GAP):
            row = entries.get(mark_id)
            listing = detail if scope == vps_marks.GAP else ""
        elif scope in _RELEASE_SCOPES and mark_id in by_release:
            row, listing, release = by_release[mark_id]
            version = str(release.get("version") or "")
            authors = [names[at] for at in release.get("by") or [] if at < len(names)]
            held = bool(release.get("held"))
        elif scope != vps_marks.RELEASE:
            # A picture a source replaced: the entry and the slot, as `picture_changes.key`.
            entry, _colon, media = mark_id.rpartition(":")
            row = entries.get(entry)
        if scope == vps_marks.VERSION:
            version = detail
        if row is None or not (row.get("name") or "").strip():
            continue
        out.append({"scope": scope, "id": mark_id, "detail": detail, "state": one["state"],
                    "since": one["since"], "vps_id": str(row["vps_id"]),
                    "game": str(row["name"]).strip(), "img_url": row.get("img_url") or "",
                    "manufacturer": row.get("manufacturer") or "", "year": row.get("year"),
                    "in_library": bool(row.get("in_library")), "listing": listing,
                    "media": media, "version": version, "authors": authors, "held": held})
    return out
