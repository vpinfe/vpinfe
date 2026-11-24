"""Discovery's front page, answered in one read: what VPS added lately, whose work the
library holds, which of its games lack a kind VPS lists, and which games VPS lists that it
has none of, which of those share a title with a game it holds, which games of the
library need a look, and which games a release page was opened for.

Drawn from the answer `library_vps_state.discovery` gives, so Home and the grid never
disagree about what is held. The VPS side is stored state; the media gaps a source can fill
come from the sources' index, which the caller reads.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from collections.abc import Collection, Iterable, Mapping
from datetime import date, timedelta
from typing import Any

from common import timestamps
from common.online import (
    arrivals,
    vps_formats,
    vps_kinds,
    vps_marked,
    vps_marks,
    vps_releases,
    vps_source,
    vps_updates,
)

ALL = "all"
YOURS = "yours"
# Rule sheets are in Releases, not on Home.
NOT_SHOWN = ("ruleFiles",)
# The kinds the New shelf has a chip for, in the order the chips run.
CHIP_KINDS = ("tableFiles", "b2sFiles", "wheelArtFiles", "topperFiles", "pupPackFiles")
# The kinds a game can lack, in the order the shelf's tabs run.
GAP_KINDS = ("b2sFiles", "romFiles", "povFiles", "wheelArtFiles", "topperFiles")

# The two findings of the Library Health shelf, as `health` keys them.
DUPLICATES, NO_TABLES = "duplicates", "no_tables"

SHELF_SHOWN = 12
AUTHORS_SHOWN = 8
# The most games a tab shows that only VPS lists. A game a source has a file for is always shown.
GAPS_SHOWN = 8
# The media kind a source files under what VPS lists as a kind: one tab holds both. A media
# kind not named here has a tab of its own. Backglass pictures are not here: a b2s file is
# an asset and a picture is media, so each has its own tab.
MEDIA_OF = {"wheelArtFiles": "wheel", "topperFiles": "topper"}
# The kinds in GAP_KINDS that are assets; the others are media.
ASSET_GAPS = tuple(one for one in GAP_KINDS
                   if vps_kinds.BY_LISTING[one].held_in == vps_kinds.ASSET)


def since(days: int, today: date | None = None) -> str:
    """The day `days` back, as a date: compared as text with each release's `added`."""
    return ((today or date.today()) - timedelta(days=days)).isoformat()


def arrivals_in(found: dict[str, Any], days: int, kept: Collection[str],
                today: date | None = None) -> list[dict[str, Any]]:
    """The releases of the kinds the library collects that VPS added in the last `days`,
    newest first, one per release. `was` is the version a release held before VPS last
    changed it, which is what a re-dated release says it was."""
    cutoff = since(days, today)
    names = [one["name"] for one in found.get("people") or []]
    shelf = []
    for row in found.get("rows") or []:
        game = (row.get("name") or "").strip()
        if not game:
            continue
        for listing, releases in (row.get("releases") or {}).items():
            if listing not in kept or listing in NOT_SHOWN:
                continue
            for release in releases:
                added = release.get("added") or ""
                if added < cutoff:
                    continue
                version = release.get("version") or ""
                before = arrivals.was(vps_source.ID, release["id"])
                shelf.append({
                    "vps_id": row["vps_id"], "game": game, "img_url": row.get("img_url") or "",
                    "in_library": bool(row.get("in_library")), "listing": listing,
                    "id": release["id"], "added": added, "version": version,
                    "was": before if before != version else "",
                    "held": bool(release.get("held")), "mod": bool(release.get("mod")),
                    "authors": [names[at] for at in release.get("by") or [] if at < len(names)]})
    shelf.sort(key=lambda one: (one["added"], one["game"].casefold()), reverse=True)
    return shelf


def on_chip(shelf: list[dict[str, Any]], chip: str) -> list[dict[str, Any]]:
    if chip == ALL:
        return shelf
    if chip == YOURS:
        return [one for one in shelf if one["in_library"]]
    return [one for one in shelf if one["listing"] == chip]


def authors_collected(found: dict[str, Any], shelf: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Whose work the library holds the most of, each with how many of those releases it
    holds and how many of theirs are on the shelf. Empty until the library is compared."""
    people = found.get("people") or []
    holding: Counter[int] = Counter()
    for row in found.get("rows") or []:
        for releases in (row.get("releases") or {}).values():
            for release in releases:
                if release.get("held"):
                    holding.update(release.get("by") or [])
    new: Counter[str] = Counter()
    for one in shelf:
        new.update(one["authors"])
    ranked = sorted((at for at in holding if at < len(people)),
                    key=lambda at: (-holding[at], people[at]["name"].casefold()))
    return [{"name": people[at]["name"], "held": holding[at], "new": new[people[at]["name"]]}
            for at in ranked[:AUTHORS_SHOWN]]


def lacking(found: dict[str, Any], listing: str,
            holders: Mapping[str, Iterable[str]] | None = None) -> list[dict[str, Any]]:
    """The library's games whose entry lists `listing` while none of it is held, the ones
    with the most on VPS first. `holders` is the library's game ids by entry, as
    `media_fill.holders` answers: a game is then listed once for each of them, and an entry no
    game is matched to is not listed. Without it each entry is one record with no game id."""
    out = []
    for row in found.get("rows") or []:
        if not row.get("in_library") or not (row.get("name") or "").strip() \
                or listing not in (row.get("listed") or []):
            continue
        if listing in (row.get("held") or []):
            continue
        for game_id in ("",) if holders is None else sorted(holders.get(row["vps_id"], ())):
            out.append({"vps_id": row["vps_id"], "game_id": game_id,
                        "name": (row.get("name") or "").strip(),
                        "img_url": row.get("img_url") or "",
                        "vps": len((row.get("releases") or {}).get(listing) or []),
                        "source": 0})
    out.sort(key=lambda one: (-one["vps"], one["name"].casefold(), one["game_id"]))
    return out


def lacking_media(found: dict[str, Any],
                  offered: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The games a source has a file for in a slot they have none in, `offered` being
    `media_fill.offered_listing`'s records for one kind: every one is listed, in the library
    or not as the last comparison saw it, since the games are the library's own. `img_url` is
    the comparison's picture where it knows the entry; `picture` is what the source offers for
    the slot, and `take` names the file that would fill it where taking it does. The ones with
    most offered come first."""
    pictures = {row.get("vps_id"): row.get("img_url") or "" for row in found.get("rows") or []}
    out = [{"vps_id": one["vps_id"], "game_id": one["game_id"],
            "name": (one.get("name") or "").strip() or one["vps_id"],
            "img_url": pictures.get(one["vps_id"], ""), "vps": 0, "source": one["available"],
            "picture": one.get("picture") or "",
            "take": {"kind": one["kind"], "source": one["source"], "size": one["size"]}
            if one.get("take") else None}
           for one in offered]
    out.sort(key=lambda one: (-one["source"], one["name"].casefold(), one["game_id"]))
    return out


def table_authors(row: Mapping[str, Any], formats: Collection[str] = ()) -> int | None:
    """How many people made a table of this game in one of `formats`, each counted once by
    the author a release names first however many tables or versions they made. None for a
    game with no such table, which is a different fact from a table that names no one.

    `formats` is `launchers.playable_formats`; empty counts every format, and a table filed
    under none counts as it does for Playable Only."""
    tables = [one for one in (row.get("releases") or {}).get(vps_releases.TABLES) or []
              if vps_formats.fits(one.get("format"), formats)]
    if not tables:
        return None
    return len({one["by"][0] for one in tables if one.get("by")})


def dont_have(found: dict[str, Any], marks: vps_marks.Marks | None = None,
              formats: Collection[str] = ()) -> list[dict[str, Any]]:
    """The games VPS lists that the library has none of and that have a table in one of
    `formats`, the ones the most people made a table for first. A game counts a person
    once, by the author a release names first, however many tables or versions they made.

    Nothing is listed until the library has been compared, since a game that has not been
    checked is not a game you lack. An ignored game is left out."""
    if not found.get("counted"):
        return []
    marks = marks or vps_marks.Marks()
    out = []
    for row in found.get("rows") or []:
        name = (row.get("name") or "").strip()
        if not name or row.get("in_library") or marks.game_ignored(str(row["vps_id"])):
            continue
        authors = table_authors(row, formats)
        if authors is None:
            continue
        year = row.get("year")
        out.append({"vps_id": row["vps_id"], "game": name, "img_url": row.get("img_url") or "",
                    "manufacturer": (row.get("manufacturer") or "").strip(),
                    "year": str(year) if year else "",
                    "authors": authors})
    # Entries that tie keep the catalog's order, which is the order the grid keeps them in.
    out.sort(key=lambda one: (-one["authors"], one["game"].casefold()))
    return out


def title_key(name: str) -> str:
    """A game's title as the same title is spelled by every entry that shares it: accents,
    case, punctuation and any parenthesised part ("(Pro)") dropped."""
    plain = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    plain = re.sub(r"\([^)]*\)", "", plain)
    return re.sub(r"[^a-z0-9]+", " ", plain.casefold()).strip()


def held_titles(found: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """The entries the library holds, by title key, the oldest first. Empty until the
    library has been compared."""
    if not found.get("counted"):
        return {}
    out: dict[str, list[dict[str, Any]]] = {}
    for row in found.get("rows") or []:
        name = (row.get("name") or "").strip()
        key = title_key(name)
        if row.get("in_library") and key:
            year = row.get("year")
            out.setdefault(key, []).append(
                {"vps_id": row["vps_id"], "game": name,
                 "manufacturer": (row.get("manufacturer") or "").strip(),
                 "year": str(year) if year else ""})
    for held in out.values():
        held.sort(key=lambda one: (one["year"], one["game"].casefold(), one["vps_id"]))
    return out


def family(found: dict[str, Any], marks: vps_marks.Marks | None = None,
           formats: Collection[str] = ()) -> list[dict[str, Any]]:
    """The games VPS lists that the library has none of, that share a title with a game it
    holds, and that have a table in one of `formats`: the same name by another maker or in
    another year. Each carries `held`, the entries of that title the library holds. The ones
    the most people made a table for come first, as on Games You Don't Have, and an ignored
    game is left out."""
    held = held_titles(found)
    marks = marks or vps_marks.Marks()
    out = []
    for one in dont_have(found, marks, formats):
        mine = held.get(title_key(one["game"]))
        if mine:
            out.append({**one, "held": mine})
    return out


def health(found: dict[str, Any], games: Iterable[Mapping[str, Any]],
           marks: vps_marks.Marks | None = None) -> dict[str, Any]:
    """The games of the library that need a look, `games` being `library_ops.library_games`'
    records. `duplicates` is the entries two or more of them are matched to, the ones with the
    most games first, then by name; `no_tables` the games with no table at all, by name.
    Entries that tie on both keep the catalog's order, and ones the catalog does not list come
    after those it does. A game matched to an ignored entry is left out; neither finding
    needs the library to be compared. `vps_id` is empty where the catalog lists no such
    entry."""
    marks = marks or vps_marks.Marks()
    games = list(games)
    entries = {str(row["vps_id"]): row for row in found.get("rows") or []}
    by_entry: dict[str, list[Mapping[str, Any]]] = {}
    for one in games:
        if one["vps_id"] and not marks.game_ignored(str(one["vps_id"])):
            by_entry.setdefault(str(one["vps_id"]), []).append(one)
    known = [vps_id for vps_id in entries if vps_id in by_entry]
    duplicates = []
    for vps_id in [*known, *(vps_id for vps_id in by_entry if vps_id not in entries)]:
        if len(held := by_entry[vps_id]) < 2:
            continue
        duplicates.append({**_about(entries.get(vps_id), held[0]["name"]),
                           "vps_id": vps_id if vps_id in entries else "",
                           "game_ids": [str(one["game_id"]) for one in held]})
    duplicates.sort(key=lambda one: (-len(one["game_ids"]), one["game"].casefold()))
    bare = [{**_about(entries.get(str(one["vps_id"])), one["name"]), "game": one["name"],
             "vps_id": str(one["vps_id"]) if str(one["vps_id"]) in entries else "",
             "game_id": str(one["game_id"])}
            for one in games
            if not one["tables"] and not (one["vps_id"] and marks.game_ignored(
                str(one["vps_id"])))]
    bare.sort(key=lambda one: one["game"].casefold())
    return {DUPLICATES: {"count": len(duplicates), "games": duplicates[:SHELF_SHOWN]},
            NO_TABLES: {"count": len(bare), "games": bare[:SHELF_SHOWN]}}


def _about(row: Mapping[str, Any] | None, name: str) -> dict[str, Any]:
    """What a health tile says of its game: the entry's name, picture, maker and year where
    VPS lists it, else the library's own name."""
    row = row or {}
    year = row.get("year")
    return {"game": (row.get("name") or "").strip() or name,
            "img_url": row.get("img_url") or "",
            "manufacturer": (row.get("manufacturer") or "").strip(),
            "year": str(year) if year else ""}


def opened_games(found: dict[str, Any], journal: Iterable[Mapping[str, Any]],
                 marks: vps_marks.Marks | None = None) -> list[dict[str, Any]]:
    """The games VPS lists that a release page was opened for and the library still has none
    of, the most recently opened first, each once with its latest `at`. `journal` is
    `opened_pages.all_recent`, keyed by the game's VPS id. Nothing is listed until the
    library has been compared; an ignored game is left out."""
    if not found.get("counted"):
        return []
    marks = marks or vps_marks.Marks()
    rows = {str(row["vps_id"]): row for row in found.get("rows") or []}
    latest: dict[str, tuple[float, str]] = {}
    for one in journal:
        at = str(one.get("at") or "")
        then = timestamps.iso_to_epoch(at)
        game_id = str(one.get("game_id") or "")
        if then is not None and then > latest.get(game_id, (-1.0, ""))[0]:
            latest[game_id] = (then, at)
    out = []
    for game_id, (_then, at) in sorted(latest.items(), key=lambda each: -each[1][0]):
        row = rows.get(game_id)
        name = ((row or {}).get("name") or "").strip()
        if not row or not name or row.get("in_library") or marks.game_ignored(game_id):
            continue
        year = row.get("year")
        out.append({"vps_id": game_id, "game": name, "img_url": row.get("img_url") or "",
                    "manufacturer": (row.get("manufacturer") or "").strip(),
                    "year": str(year) if year else "", "at": at})
    return out


def _year(value: Any) -> int | None:
    text = str(value).strip()
    return int(text) if re.fullmatch(r"\d{4}", text) else None


def suggestion(game: Mapping[str, Any], by_title: Mapping[str, list[dict[str, Any]]],
               marks: vps_marks.Marks | None = None) -> dict[str, Any] | None:
    """The entry `game` is likely to be: the one whose normalized title is the game's and whose
    year is within one of the game's, the nearest year first, then the same maker, then the
    entry id. An ignored entry is never offered. None where the game says no year, since a
    title alone does not pick between versions of it. `by_title` is `found`'s named entries
    by `title_key`."""
    marks = marks or vps_marks.Marks()
    year = _year(game.get("year"))
    if year is None:
        return None
    maker = str(game.get("manufacturer") or "").strip().casefold()
    near = []
    for entry in by_title.get(title_key(str(game.get("name") or "")), ()):
        listed = _year(entry.get("year"))
        if (listed is not None and abs(listed - year) <= 1
                and not marks.game_ignored(str(entry["vps_id"]))):
            made = str(entry.get("manufacturer") or "").strip().casefold()
            near.append((abs(listed - year), not (maker and maker == made),
                         str(entry["vps_id"]), entry))
    if not near:
        return None
    best = min(near, key=lambda one: one[:3])[3]
    return {"vps_id": best["vps_id"], "game": (best.get("name") or "").strip(),
            "img_url": best.get("img_url") or "",
            "manufacturer": (best.get("manufacturer") or "").strip(),
            "year": str(best.get("year") or "")}


def unmatched(found: dict[str, Any], games: Iterable[Mapping[str, Any]],
              marks: vps_marks.Marks | None = None) -> list[dict[str, Any]]:
    """The library's games with no VPS match that have a likely entry, by name and year. `games`
    are the library's unmatched games, each `{game_id, name, manufacturer, year}`. Listed by
    name; a game with no likely entry is left to the Games grid's Unmatched view."""
    by_title: dict[str, list[dict[str, Any]]] = {}
    for row in found.get("rows") or []:
        if key := title_key(str(row.get("name") or "")):
            by_title.setdefault(key, []).append(row)
    out = []
    for game in games:
        if likely := suggestion(game, by_title, marks):
            out.append({"game_id": game["game_id"], "game": str(game.get("name") or "").strip(),
                        "manufacturer": str(game.get("manufacturer") or "").strip(),
                        "year": str(game.get("year") or "").strip(), "suggestion": likely})
    out.sort(key=lambda one: (one["game"].casefold(), one["game_id"]))
    return out


def _same_game(listed: Mapping[str, Any], offered: Mapping[str, Any]) -> bool:
    """Whether a record from VPS and one from a source are one game: the same library id, or
    where VPS's has none, the same entry."""
    if listed["game_id"]:
        return listed["game_id"] == offered["game_id"]
    return listed["vps_id"] == offered["vps_id"]


def gaps(listed: list[dict[str, Any]], offered: list[dict[str, Any]]) -> dict[str, Any]:
    """One tab: the games `lacking` lists and the games `lacking_media` lists, each game once.
    A game both name keeps its library id and carries both counts. Every game a source has a
    file for is shown; of the ones only VPS lists, the first `GAPS_SHOWN`. `game_ids` is every
    game the tab counts, which is what See All opens; `on_vps` is how many of them VPS lists."""
    for one in offered:
        one["vps"] = max((other["vps"] for other in listed if _same_game(other, one)), default=0)
    merged = [*offered, *(one for one in listed
                          if not any(_same_game(one, other) for other in offered))]
    merged.sort(key=lambda one: (-(one["vps"] + one["source"]), one["name"].casefold(),
                                 one["game_id"]))
    shown, only_vps = [], 0
    for one in merged:
        if one["source"] == 0:
            only_vps += 1
            if only_vps > GAPS_SHOWN:
                continue
        shown.append(one)
    return {"count": len(merged), "on_vps": len(listed),
            "game_ids": [one["game_id"] for one in merged if one["game_id"]], "games": shown}


def missing_tabs(found: dict[str, Any], kept: Collection[str], marks: vps_marks.Marks,
                 media: Mapping[str, Iterable[Mapping[str, Any]]] | None = None,
                 holders: Mapping[str, Iterable[str]] | None = None) -> dict[str, Any]:
    """Home's `missing` alone: each tab `gaps` builds, with the arguments `home` takes."""
    filed = {kind: listing for listing, kind in MEDIA_OF.items()}
    sourced = {}
    for kind, offered in (media or {}).items():
        tab = filed.get(kind, kind)
        games = [one for one in lacking_media(found, offered)
                 if not marks.gap_ignored(one["vps_id"], tab)]
        if games:
            sourced[tab] = games
    missing = {}
    for listing in GAP_KINDS:
        if listing in kept:
            listed = [one for one in lacking(found, listing, holders)
                      if not marks.gap_ignored(one["vps_id"], listing)]
            missing[listing] = gaps(listed, sourced.pop(listing, []))
    for tab, games in sourced.items():
        missing[tab] = gaps([], games)
    return missing


def home(found: dict[str, Any], kept: Collection[str], days: int,
         today: date | None = None,
         media: Mapping[str, Iterable[Mapping[str, Any]]] | None = None,
         holders: Mapping[str, Iterable[str]] | None = None,
         marks: vps_marks.Marks | None = None,
         updates: list[dict[str, Any]] | None = None,
         formats: Collection[str] = (),
         library: Iterable[Mapping[str, Any]] = (),
         opened: Iterable[Mapping[str, Any]] = (),
         unmatched_games: Iterable[Mapping[str, Any]] = ()) -> dict[str, Any]:
    """Every shelf. `found` is `library_vps_state.discovery`'s answer, `kept` the VPS kinds
    the library collects, and `days` the window of the New shelf. `media` is the games a
    source can fill a slot of, by media kind, as `media_fill.offered_listing` answers. A kind
    VPS lists too (`MEDIA_OF`) is one tab with it, a game once; the other media kinds follow
    the VPS kinds, one tab each. Every game a source can fill is listed, none cut to a row.
    `holders` is the library's game ids by entry; with it a game VPS lists is one record for each
    game matched to the entry, so a tab counts games and `game_ids` names them all.

    New is cut to what a tile row shows, once for each chip, with the count each chip holds
    so a chip with nothing in it is left out by the page. Whatever `marks` ignores is on no
    shelf, and what it holds Wanted is its own. `dont_have` is the games the library lacks, cut
    to a row of tiles with the count of all of them, counting the tables in `formats`. `family` is
    the part of those that share a title with a game the library holds. `library` is the
    library's games as `library_ops.library_games` answers, which `health` reads. `opened` is
    the journal of release pages opened, and the shelf lists every game of it the library
    still lacks.
    `updates` is what `library_updates.found` answers: the games with a later version of
    something they hold. `unmatched_games` is the library's games with no VPS match, for
    the shelf that names the entry each is likely to be."""
    marks = marks or vps_marks.Marks()
    shelf = [one for one in arrivals_in(found, days, kept, today)
             if not marks.release_ignored(one["id"], one["vps_id"])]
    new = {}
    for chip in (ALL, YOURS, *CHIP_KINDS):
        shown = on_chip(shelf, chip)
        new[chip] = {"count": len(shown), "releases": shown[:SHELF_SHOWN]}
    missing = missing_tabs(found, kept, marks, media, holders)
    held = vps_marked.wanted(found, marks)
    wanted = {"count": len(held), "items": held[:SHELF_SHOWN]}
    lacked = dont_have(found, marks, formats)
    related = family(found, marks, formats)
    pages = opened_games(found, opened, marks)
    likely = unmatched(found, unmatched_games, marks)
    return {"counted": found.get("counted") or "", "days": days, "since": since(days, today),
            "new": new, "authors": authors_collected(found, shelf), "missing": missing,
            "wanted": wanted, "dont_have": {"count": len(lacked), "games": lacked[:SHELF_SHOWN]},
            "family": {"count": len(related), "games": related[:SHELF_SHOWN]},
            "health": health(found, library, marks),
            "opened": {"count": len(pages), "games": pages},
            "unmatched": {"count": len(likely), "games": likely[:SHELF_SHOWN]},
            "updates": vps_updates.shelf(updates or [], marks, cut=vps_updates.SHELF_SHOWN)}
