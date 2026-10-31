"""The release match: the line, its buttons and the record and release pickers."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from nicegui import ui

from common import icons
from common.failures import why
from common.i18n import t
from common.online import vps_kinds
from manager.kit import (
    candidates,
    deeplink,
    game_tables,
    offload,
    panel,
    table_features,
    verbs,
    when,
)
from manager.kit.on_page import on_page
from manager.library import file_acts, release_match, vps_match
from manager.workbench.core import _write

logger = logging.getLogger("vpinfe.manager.workbench.game_match")


def _based_on(mod: dict[str, Any] | None, classes: str = "") -> None:
    """What a table's release is a mod of, one level up: the way to that table where the
    library holds it, in whichever game, and Missing with the way to VPS where not."""
    line = game_tables.mod_line(mod)
    if not mod or not line:
        return
    with ui.row().classes(f"items-center gap-2 w-full no-wrap min-w-0 {classes}"):
        if mod.get("table_id"):
            panel.link(line, to="/manager?" + deeplink.query(
                {"view": "tables", "game": str(mod.get("game_id") or ""),
                 "table": str(mod["table_id"])}))()
            return
        ui.label(line).classes("manager-help truncate min-w-0")
        if not mod.get("vps_file_id"):
            return
        word, why = game_tables.GONE_WORDS
        ui.label(word).tooltip(why) \
            .classes("manager-member-chip manager-tier manager-tier--off")
        if mod.get("url"):
            panel.out(to=str(mod["url"]), hint=t("manager.vps_match.open_in_vps"))()


async def _match_line(context: dict[str, Any], kind: str, matched_to: Any,
                      inventory: str = vps_kinds.MEDIA) -> str:
    """What the bound record is called. Resolved here because the catalog is already
    open on this side and an id is not something to put on screen."""
    said = str(matched_to or "")
    listed = file_acts.listed_as(kind, inventory)
    if not said or listed is None:
        return ""
    library = context["library"]
    vps_id = str(context["game"].get("vps_id") or "")
    try:
        records = (await offload.io(library.vps_releases, vps_id, listed.listed_as)
                   if vps_id else [])
        held = bool(records) or bool(vps_id and await offload.io(library.vps_catalog_held))
    except Exception:
        logger.warning("manager: could not read %s for %s", listed.listed_as, vps_id,
                       exc_info=True)
        return t("manager.workbench.matched_file")
    for record in records:
        if str(record.get("vps_file_id") or "") == said:
            told = " · ".join(part for part in (
                str(record.get("version") or ""),
                ", ".join(str(name) for name in (record.get("authors") or [])[:2]),
            ) if part)
            return t("manager.workbench.matched",
                    told=(told)) if told else t("manager.workbench.matched_file")
    # Worth saying: it is why no update will ever be reported for this file.
    return (t("manager.workbench.delisted_file") if held
            else t("manager.workbench.matched_file"))


def _match_button(context: dict[str, Any], kind: str, label: str,
                  entry: dict[str, Any], redraw: Callable[[], None],
                  inventory: str = vps_kinds.MEDIA) -> None:
    """The way to say which VPS record one file is. Only for kinds VPSdb lists in this
    inventory - a picker over a kind nothing publishes opens empty every time."""
    listed = file_acts.listed_as(kind, inventory)
    if listed is None:
        return
    path = str(entry.get("path") or "")
    if not path:
        return
    bound = str(entry.get("matched_to") or "")
    button = ui.button(t("manager.games.match_vps"), icon=verbs.MATCH,
                       on_click=lambda: _pick_a_record(context, listed.listed_as,
                                                       label, path, bound, redraw)) \
        .props("flat dense no-caps size=sm").classes("manager-action")
    if context["game"].get("vps_id"):
        button.tooltip(t("manager.workbench.published_file"))
        return
    # The records belong to an entry, so there is nothing to choose among until the
    # game is matched. Said on the control rather than in a dialog that opens empty.
    button.disable()
    button.tooltip(t("manager.workbench.files_unmatched"))


@on_page
async def _pick_a_record(context: dict[str, Any], listed_as: str, label: str,
                         path: str, bound: str,
                         redraw: Callable[[], None]) -> None:
    """Bind one file to a record VPSdb publishes, or take the binding back.

    The assets ledger's twin of `_pick_a_release`.
    """
    picked = await ask_a_record(context, listed_as, label, path, bound)
    if picked is None:
        return
    await _write(context, context["library"].set_asset_source, context["game_id"], path,
                 str(picked))
    redraw()


async def ask_a_record(context: dict[str, Any], listed_as: str, label: str, path: str,
                       bound: str, place: str = "") -> Any:
    """The record the person picked, "" to take the binding back, or what leaving answers:
    nothing, or in a walk (`place` is its position) Skip and Stop."""
    records, empty, reason = await _listed_by_vps(
        context, t("manager.workbench.kind_unlisted", kind=label), listed_as)
    name = Path(path).name or path
    return await release_match.ask(
        release_match.Yours(name=name, file=name, facts=label, glyph="inventory_2"),
        records, bound=bound, empty=empty, reason=reason, place=place, files=True)


async def _listed_by_vps(context: dict[str, Any], none_listed: str,
                         *listed_as: str) -> tuple[list[dict[str, Any]], str, str]:
    """What VPS lists for the game, read off the loop, the line a picker shows in place
    of the list - `none_listed`, or why there is no list to show - and why a read
    failed. The two lines are empty while there is a list."""
    library = context["library"]
    vps_id = str(context["game"].get("vps_id") or "")
    try:
        found = (list(await offload.io(library.vps_releases, vps_id, *listed_as))
                 if vps_id else [])
        held = bool(found) or await offload.io(library.vps_catalog_held)
    except Exception as exc:
        logger.warning("manager: could not read VPS for %s", vps_id, exc_info=True)
        return [], t("manager.workbench.vps_failed"), why(exc)
    if found:
        return found, "", ""
    return [], none_listed if held else t("manager.workbench.no_vps_data"), ""


@on_page
async def _pick_a_release(context: dict[str, Any], table: dict[str, Any]) -> None:
    """Bind this table to one of the entry's builds, or take the binding back."""
    library = context["library"]
    bound = str((table.get("source") or {}).get("vps_file_id") or "")
    releases, empty, reason = await _listed_by_vps(
        context, t("manager.workbench.tables_unlisted"))

    picked = await release_match.ask(
        _yours(table), releases, bound=bound,
        formats=[str(one) for one in table.get("vps_formats") or []],
        empty=empty, reason=reason)
    if picked is None:
        return
    await _write(context, library.set_table_source, context["game_id"],
                 str(table.get("id") or ""), str(picked))


def _yours(table: dict[str, Any]) -> release_match.Yours:
    """What this file says about itself, as a row of the list it is compared against.

    A `.vpx` carries a version and its authors and VPS names releases by the same two,
    which is the only honest handle here - VPS records a filename on almost none of them.
    """
    version = str(table.get("version") or "")
    authors = tuple(str(name) for name in (table.get("authors") or []))
    filename = str(table.get("filename") or "")
    told = game_tables.JOIN.join(part for part in (version, ", ".join(authors[:4])) if part)
    facts = [", ".join(str(one) for one in table.get("vps_formats") or [])]
    held = table.get("features") or {}
    facts += [label for key, label in table_features.LABELS.items() if held.get(key) is True]
    if table.get("release_date"):
        facts.append(t("manager.release_match.released", when=when.ago(table["release_date"])))
    return release_match.Yours(
        name=told or filename, file=filename, authors=authors,
        facts=game_tables.JOIN.join(part for part in facts if part), version=version)


def _release_words(release: dict[str, Any]) -> tuple[str, str]:
    """A build's name and the line under it: its version, then format, makers and date."""
    meta = [str(release.get("format") or "")]
    made_by = ", ".join(str(name) for name in (release.get("authors") or [])[:3])
    if made_by:
        meta.append(made_by)
    if release.get("updated_at"):
        meta.append(when.ago(release["updated_at"]))
    name = str(release.get("version") or "") or t("manager.workbench.no_version")
    return name, " \u00b7 ".join(part for part in meta if part)


def _release_shown(release: dict[str, Any]) -> None:
    """The build a table is matched to, in the shape the picker offered it."""
    url = str(release.get("url") or "")

    def end() -> None:
        if url:
            panel.out(to=url, hint=t("manager.workbench.open_release"))()

    name, meta = _release_words(release)
    candidates.choice(str(release.get("img_url") or ""), name, meta,
                      glyph=icons.TABLES, trailing=end, entry=True,
                      more=_mod_note(release), missing=vps_match.NO_PICTURE)


def _mod_note(release: dict[str, Any]) -> tuple[str, ...]:
    """The lines VPS's note adds under a mod's build row; none for any other release."""
    comment = str(release.get("comment") or "").strip()
    is_mod = release.get("mod_of") or "MOD" in (release.get("features") or [])
    return (comment,) if comment and is_mod else ()


async def _read_releases(context: dict[str, Any],
                         vps_id: str) -> tuple[list[dict[str, Any]], str]:
    """The entry's builds, read off the loop, and why they could not be read. Empty
    where they were."""
    if not vps_id:
        return [], ""
    try:
        return list(await offload.io(context["library"].vps_releases, vps_id)), ""
    except Exception as exc:
        logger.warning("manager: could not read VPS for %s", vps_id, exc_info=True)
        return [], why(exc)
