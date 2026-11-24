from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from common.games import game_identity, game_repository
from common.games.game_metadata import default_table, game_title, game_vps_id, normalize_meta
from common.games.tables import table_entries, table_id

VPS = "vps"
VPINFE = "vpinfe"


def owned(ids: Iterable[Any], keys: str = VPS, kind: str = "") -> dict[str, Any]:
    """What the library holds of `ids`. `keys` says whose ids they are, VPS's or VPinFE's;
    `kind` says `game` or `table`, or "" for a VPS id of either. `known` is what VPSdb
    calls each VPS id the library does not hold."""
    wanted = {str(one or "").strip() for one in ids} - {""}
    if not wanted:
        return {"owned": {}, "other_versions": {}, "known": {}}
    games = list(game_repository.all_games())
    found = _held(wanted, games, keys, kind)
    missing = wanted - set(found)
    return {"owned": found,
            "other_versions": (_other_versions(missing, games)
                               if keys == VPS and kind != "game" else {}),
            "known": _known(missing, kind) if keys == VPS and kind else {}}


def held(vps_ids: Iterable[Any]) -> dict[str, dict[str, str]]:
    """`owned` without the search for other versions of what is missing."""
    wanted = {str(one or "").strip() for one in vps_ids} - {""}
    return _held(wanted, list(game_repository.all_games())) if wanted else {}


def _held(wanted: set[str], games: list[Any], keys: str = VPS,
          kind: str = "") -> dict[str, dict[str, str]]:
    found: dict[str, dict[str, str]] = {}
    for game in games:
        game_id = game_identity.game_id(game)
        name = game_title(game)
        if kind != "table":
            entry = game_vps_id(game) if keys == VPS else game_id
            if entry in wanted and entry not in found:
                found[entry] = {"game_id": game_id, "table_id": "", "name": name}
        if kind == "game":
            continue
        for key, table in table_entries(normalize_meta(game.meta_config)).items():
            source = table.get("source") if isinstance(table, dict) else None
            said = (str((source or {}).get("vps_file_id") or "").strip()
                    if keys == VPS else str(key))
            if said in wanted and said not in found:
                found[said] = {"game_id": game_id, "table_id": str(key), "name": name}
    return found


def _known(missing: set[str], kind: str) -> dict[str, dict[str, Any]]:
    """VPSdb's name and the facts a line is made of for each id, a machine's or one
    build's."""
    if not missing:
        return {}
    from common.games.game_service import find_vps_release_and_entry, load_vpsdb

    said: dict[str, dict[str, Any]] = {}
    if kind == "game":
        for entry in load_vpsdb():
            each = str(entry.get("id") or "")
            if each in missing:
                said[each] = {"name": str(entry.get("name") or ""),
                              "manufacturer": str(entry.get("manufacturer") or ""),
                              "year": str(entry.get("year") or "")}
        return said
    for each in missing:
        release, entry = find_vps_release_and_entry(each)
        if entry:
            said[each] = {"name": str(entry.get("name") or ""),
                          "version": str(release.get("version") or ""),
                          "authors": [str(one) for one in release.get("authors") or []]}
    return said


def _other_versions(missing: set[str], games: list[Any]) -> dict[str, dict[str, str]]:
    if not missing:
        return {}
    from common.games.game_service import load_vpsdb
    from common.online.vps_lens import entry_address

    machines: dict[str, tuple[str, str]] = {}
    for entry in load_vpsdb():
        for release in entry.get("tableFiles") or []:
            said = str(release.get("id") or "")
            if said in missing:
                urls = [str(one.get("url") or "") for one in release.get("urls") or []]
                machines[said] = (str(entry.get("id") or ""),
                                  next((one for one in urls if one),
                                       entry_address(str(entry.get("id") or ""))))
    by_entry = {game_vps_id(game): game for game in games if game_vps_id(game)}
    other: dict[str, dict[str, str]] = {}
    for release, (machine, address) in machines.items():
        game = by_entry.get(machine)
        if game is None:
            continue
        meta = normalize_meta(game.meta_config)
        _filename, table = default_table(meta)
        other[release] = {"game_id": game_identity.game_id(game),
                          "table_id": table_id(table),
                          "name": game_title(game),
                          "version": str(table.get("version") or ""), "url": address}
    return other
