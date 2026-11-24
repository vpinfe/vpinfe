"""The library as one install reads it off another.

An install with no library of its own asks for entries and builds its own payload from
them. This turns the wire rows into the same objects the resolver produces locally, so
everything downstream - filters, sorts, the payload builder - cannot tell which side the
library came from.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any
from urllib.parse import quote, urlencode, urljoin

import requests

from common.devices.device_registry import paired_header as _paired
from common.games.collection_ops import wheel_paths
from common.games.collection_resolver import Entry
from common.games.wire_entry import WireGame, table_of
from common.i18n import t
from common.security import http_client
from common.service_errors import (
    BlockedError,
    NotFoundError,
    RefusedError,
    ServiceError,
    UnavailableError,
)

logger = logging.getLogger("vpinfe.common.games.remote_library")

# A library is one request, and a cold one on a large library is not instant. Longer than
# the shared default, which is sized for a metadata lookup.
LIBRARY_TIMEOUT = 30

def entries_url(library_url: str, collection: str = "") -> str:
    """Where to ask for entries. Empty means the whole library, which is its own endpoint
    rather than a collection: no stored collection means "all of it"."""
    name = collection.strip()
    path = f"api/v1/collections/{quote(name, safe='')}/entries" if name \
        else "api/v1/library/entries"
    return urljoin(library_url.rstrip("/") + "/", path)


def verify_shared_library(entries: Iterable[Entry] | None,
                          local_games: Iterable[Any] | None) -> dict[str, Any]:
    """Whether this install's own copy of the library is the one it reads, by content.

    Shared storage is what the split assumes and nothing checks: a `game_root_dir` that is
    wrong or unmounted fails one game at a time, at launch, as a file-not-found. This asks
    the question up front and by hash rather than by path, because the same share is
    mounted at different places on different machines - a path comparison would report
    every install as broken.

    Reports rather than decides. `matched`, `missing` (the library has a table this
    install cannot resolve) and `differs` (both have it, the bytes differ) are three
    different problems for a caller to act on, and what to do about each is a policy
    question this does not answer.
    """
    from common.games.tables import table_entries

    local: dict[str, str] = {}
    for game in local_games or ():
        for table in table_entries(getattr(game, "meta_config", {})).values():
            table_id = str(table.get("id", "") or "")
            if table_id:
                local[table_id] = str(table.get("file_hash", "") or "")

    matched, missing, differs, unverifiable = 0, [], [], 0
    for entry in entries or ():
        table_id = getattr(entry, "table_id", "")
        wanted = str((entry.table or {}).get("file_hash", "") or "")
        if not table_id or not wanted:
            # A table the library has not hashed says nothing either way. Counted rather
            # than dropped, so "everything matched" cannot mean "nothing was checked".
            unverifiable += 1
            continue
        if table_id not in local:
            missing.append(table_id)
        elif local[table_id] != wanted:
            differs.append(table_id)
        else:
            matched += 1

    return {"matched": matched, "missing": missing, "differs": differs,
            "unverifiable": unverifiable,
            "shared": not missing and not differs and matched > 0}


def _entry_from_wire(row: dict[str, Any]) -> Entry:
    """One wire row as the Entry the rest of the frontend already reads. `siblings` comes
    from the library, which is the only side that can see a game's other tables."""
    return Entry(game=WireGame(row.get("game") or {}, row),
                 table=table_of(row),
                 siblings=int(row.get("siblings") or 1))


def _get_json(url: str, collection: str, timeout: int) -> Any:
    try:
        return http_client.get_json(url, timeout=timeout, headers=_paired(url))
    except requests.HTTPError as exc:
        if collection.strip() and getattr(exc.response, "status_code", None) == 404:
            raise NotFoundError(t("error.collections.not_found",
                                  name=collection)) from exc
        raise


def fetch_entries(library_url: str, collection: str = "",
                  timeout: int = LIBRARY_TIMEOUT) -> list[Entry]:
    """Another install's entries for a collection, as local Entry objects.

    Raises rather than returning an empty list: a library that cannot be reached is not a
    library with no games, and a caller showing an empty wheel for it would be reporting
    the wrong thing. A 404 for a named collection raises NotFoundError.
    """
    payload = _get_json(entries_url(library_url, collection), collection, timeout)
    rows = payload.get("entries") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        raise ValueError(f"{library_url} did not return an entry list")
    return [_entry_from_wire(row) for row in rows if isinstance(row, dict)]


def _on(library_url: str, path: str) -> str:
    return urljoin(library_url.rstrip("/") + "/", path.lstrip("/"))


def art_url(path: str) -> str:
    """Where a window on this device asks for a picture the library holds."""
    return "/library/" + path.lstrip("/")


def fetch_collections(library_url: str, *,
                      timeout: int = http_client.DEFAULT_TIMEOUT) -> list[dict[str, Any]]:
    """Another install's collections as its API lists them, in its order. Raises when it
    cannot be asked, like `fetch_entries`."""
    asked = _on(library_url, "api/v1/collections")
    payload = http_client.get_json(asked, timeout=timeout, headers=_paired(asked))
    rows = payload.get("collections") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise ValueError(f"{library_url} did not return a collection list")
    return [row for row in rows if isinstance(row, dict) and row.get("name")]


def fetch_collection(library_url: str, name: str, *,
                     timeout: int = http_client.DEFAULT_TIMEOUT) -> dict[str, Any]:
    """One of another install's collections. Raises when it cannot be asked, and
    NotFoundError when that install has no collection by that name."""
    path = f"api/v1/collections/{quote(name, safe='')}"
    row = _get_json(_on(library_url, path), name, timeout)
    if not isinstance(row, dict) or not row.get("name"):
        raise ValueError(f"{library_url} did not return a collection")
    return row


_REFUSALS: dict[int, type[ServiceError]] = {404: NotFoundError, 409: BlockedError,
                                            501: UnavailableError}


def create_collection(library_url: str, body: dict[str, Any], *,
                      timeout: int = http_client.DEFAULT_TIMEOUT) -> dict[str, Any]:
    """Make a collection on another install, `body` being what its create route takes.

    A refusal that install words raises as a ServiceError carrying those words. One it
    does not word raises as it came.
    """
    try:
        made = _on(library_url, "api/v1/collections")
        return http_client.post_json(made, body, timeout=timeout, headers=_paired(made))
    except requests.HTTPError as exc:
        answer = exc.response
        try:
            said = str(answer.json()["error"]["message"]) if answer is not None else ""
        except Exception:
            said = ""
        if answer is None or not said:
            raise
        raise _REFUSALS.get(answer.status_code, RefusedError)(said) from exc


def metadata_row(resource: dict[str, Any]) -> dict[str, Any]:
    """One of another install's collections, shaped as `get_collections_metadata` shapes
    a local one, with that install's count and `art_url` addresses for its art."""
    name = str(resource["name"])
    is_filter = resource.get("type") == "filter"
    image = str(resource.get("image") or "")
    image_url = ""
    if image:
        version = resource.get("image_version")
        query = f"?{urlencode({'v': version})}" if version else ""
        image_url = art_url(f"api/v1/collections/{quote(name, safe='')}/image{query}")
    return {
        "name": name,
        "type": "filter" if is_filter else "vpsid",
        "is_filter": is_filter,
        "image": image,
        "image_url": image_url,
        "in_frontend": bool(resource.get("in_frontend", True)),
        "game_count": resource.get("game_count"),
        "table_count": resource.get("count"),
        "game_wheel_urls": [art_url(str(url)) for url in resource.get("game_wheels") or []],
    }


def glance(entries: list[Entry]) -> dict[str, Any]:
    """How many `entries` that install sent, and addresses of the first few wheels."""
    return {"table_count": len(entries),
            "game_wheel_urls": [art_url(path) for path in wheel_paths(entries)]}
