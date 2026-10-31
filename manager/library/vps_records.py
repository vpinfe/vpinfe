"""What a VPS file record says, in the words every surface that lists them uses."""

from __future__ import annotations

from typing import Any

from common.online import sites, vps_formats
from manager.kit import game_tables


def kind_state(held: dict[str, Any], listed_as: str) -> dict[str, Any]:
    return next((one for one in held.get("kinds") or [] if one.get("kind") == listed_as), {})


def held_of(held: dict[str, Any] | None, listed_as: str) -> list[dict[str, str]]:
    """The files the owning game holds of one VPS kind, each with the record it is tied to."""
    if held is None:
        return []
    if listed_as == "tableFiles":
        return list(held.get("tables") or [])
    return list(kind_state(held, listed_as).get("files") or [])


def newest_first(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(records, key=lambda one: str(one.get("updated_at") or ""), reverse=True)


def what_it_is(record: dict[str, Any], *, features: bool) -> str:
    """That the file is a mod and of what, its features, and a format other than VPX."""
    facts = game_tables.release_facts(record, features=features)
    kept = str(record.get("format") or "").strip().upper()
    if kept and kept != "VPX":
        facts.append(vps_formats.name(kept))
    return game_tables.JOIN.join(facts)


def site_of(record: dict[str, Any]) -> tuple[str, str]:
    """The site the file's page is on, and the page: the first link VPS has not marked
    broken, or the first it has where all are. `("", "")` for a file with no link."""
    page = str(record.get("url") or "")
    return (sites.site_name(page), page) if page else ("", "")


def page_is_broken(record: dict[str, Any]) -> bool:
    """Whether VPS marks the page `site_of` gives as no longer working."""
    page = str(record.get("url") or "")
    return any(one.get("broken") for one in (record.get("urls") or []) if one.get("url") == page)


def told(record: dict[str, Any]) -> str:
    return " · ".join(part for part in (
        str(record.get("version") or ""),
        ", ".join(str(name) for name in (record.get("authors") or [])[:2])) if part)
