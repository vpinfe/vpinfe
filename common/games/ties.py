"""A tie: the record that a file is one release VPS lists, and what version VPS listed then.

A tie lives in a `source` block - on a table's entry, or on a file's entry in the assets
ledger - as `vps_file_id`, the basis that put it there as `confirmed_by`, and `vps_version`.
The version is what an update is measured from: a later listing of the same release is an
update, whatever the file's own version says.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from common.games import identity_claims
from common.values import newer_version, parse_version

FILE_KEY = "vps_file_id"
BASIS_KEY = "confirmed_by"
VERSION_KEY = "vps_version"

_KEYS = (FILE_KEY, BASIS_KEY, VERSION_KEY)

# What a table's row says about its release: no tie where one could be made, and a tie that
# records no basis.
UNTIED = "untied"
TIED = "tied"


def listed_version(release: Mapping[str, Any] | None) -> str:
    """The version VPS lists on a release, or "" where it lists none or there is no release."""
    return str((release or {}).get("version") or "").strip()


def tie(source: dict[str, Any], vps_file_id: str, basis: str,
        release: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Put a tie on `source`: the release, the basis, and the version it lists now.

    A release the catalog does not hold, or that lists no version, records none - and a
    version recorded by an earlier tie is dropped with it, because it belonged to the
    release that was tied then.
    """
    source[FILE_KEY] = vps_file_id
    source[BASIS_KEY] = basis
    if version := listed_version(release):
        source[VERSION_KEY] = version
    else:
        source.pop(VERSION_KEY, None)
    return source


def untie(source: dict[str, Any]) -> dict[str, Any]:
    """Take a tie off `source` and leave everything else on it."""
    for key in _KEYS:
        source.pop(key, None)
    return source


def decline(source: dict[str, Any]) -> dict[str, Any]:
    """Take a tie off `source` as a person's answer: no release, said by a person. A sweep
    that ties tables for people leaves it alone, and picking a release replaces it."""
    untie(source)
    source[FILE_KEY] = ""
    source[BASIS_KEY] = identity_claims.USER
    return source


def declined(source: Mapping[str, Any] | None) -> bool:
    """Whether a person took this table's tie back, as `decline` writes it."""
    held = source or {}
    return not held.get(FILE_KEY) and held.get(BASIS_KEY) == identity_claims.USER


def update_available(source: Mapping[str, Any] | None) -> bool | None:
    """Whether VPS now lists a later version of the tied release than it listed at the tie.

    `source` is a tie that has been named, so `version` is what VPS lists now. None where
    there is nothing to weigh: no tie, no version recorded with it, or a version on either
    side that does not read as one.
    """
    held = source or {}
    if not held.get(FILE_KEY):
        return None
    then = parse_version(held.get(VERSION_KEY))
    now = parse_version(held.get("version"))
    return newer_version(now, then) if then and now else None


def patch_bases(rows: Iterable[Mapping[str, Any]]) -> frozenset[str]:
    """The ids of the tables a patch was applied to, from the rows that name them."""
    return frozenset(str(((row.get("source") or {}).get("base") or {}).get("table_id") or "")
                     for row in rows) - {""}


def release_state(row: Mapping[str, Any], bases: frozenset[str] = frozenset()) -> str:
    """What a table row says about its release: the basis of the tie where there is one
    (`tied` where it records none), `untied` where the game is on VPS and a release could
    be picked, and "" where there is nothing to pick: a game VPS does not hold, a table a
    patch made, or a hidden table kept as the base of one (`bases` names those)."""
    if not row.get("game_vps_id"):
        return ""
    source = row.get("source") or {}
    if source.get(FILE_KEY):
        return str(source.get(BASIS_KEY) or "") or TIED
    if source.get("base") or source.get("patch"):
        return ""
    if row.get("hidden") and str(row.get("id") or "") in bases:
        return ""
    return UNTIED
