"""The lists an extension declares, drawn as Manager grids with their rail entries.

The area value `community`, the `community:` view key and the `manager.community.*` catalog
keys keep that spelling from when this module was community.py.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable, Mapping, Sequence
from functools import partial
from typing import Any, NamedTuple
from urllib.parse import quote

from nicegui import ui

from common import icons
from common.devices import install_identity
from common.extensions.host import SWITCHED_OFF
from common.failures import why
from common.games import rankings
from common.games.community_lists import keep, kept
from common.i18n import t
from manager.extensions import ext_action, ext_links, ext_page
from manager.kit import (
    busy,
    confirm,
    deeplink,
    game_tables,
    grid,
    link_tab,
    offload,
    panel,
    renderers,
    states,
    tag_chips,
    thumbnails,
    verbs,
    views,
    walks,
    when,
)
from manager.kit.api import ApiClient, ApiError
from manager.kit.data import Library, read_state, sources_of
from manager.kit.on_page import on_page
from manager.library import collection_rules

logger = logging.getLogger("vpinfe.manager.extensions.ext_lists")

PREFIX = "community:"
PAGE_PREFIX = "page:"
ICON = "extension"
HELD = "held"
UNDER = "under_said"
# The id of a row its extension gave none: drawn, but it cannot be ticked or act.
UNNAMED = "~unnamed~"

_KIND = {"number": grid.NUMBER_COLUMN,
         "text": {}, "date": {}}
_WIDTH = {"number": 130, "text": 150, "date": 140, "state": 140, "chips": 220, "link": 170,
          "picture": 90}
FIRST_WIDTH = 240
LIBRARY_KINDS = ("game", "table")
LINE = "_line"


class Lookup(NamedTuple):
    """What the library and VPSdb say of the ids one game or table column holds."""

    held: dict[str, dict[str, Any]] = {}
    other: dict[str, dict[str, Any]] = {}
    known: dict[str, dict[str, Any]] = {}


def view_key(extension: str, key: str) -> str:
    return f"{PREFIX}{extension}:{key}"


def page_view_key(extension: str, key: str) -> str:
    """The rail entry of one of an extension's pages, which has no grid to be a list."""
    return f"{PAGE_PREFIX}{extension}:{key}"


def address(extension: str, key: str) -> str:
    return "/manager?" + deeplink.query({"view": view_key(extension, key)})


def lists(extensions: Sequence[dict[str, Any]]) -> list[tuple[dict, dict]]:
    """(extension, list) for every list a running extension declares."""
    return [(one, declared) for one in extensions
            if str(one.get("state") or "") == "loaded"
            for declared in one.get("lists") or []]


# What gates an extension's list, by the area it asked for: the feature that owns the
# area, so a list put in Library is absent from an install that has no library.
AREA_FEATURE = {"library": install_identity.LIBRARY, "frontend": install_identity.FRONTEND,
                "community": install_identity.CORE, "system": install_identity.CORE}


def nav_items(extensions: Sequence[dict[str, Any]]) -> dict[str, tuple]:
    """The rail entries extensions add, by the area each list, page or link asked for. An
    extension's lists come first, then its pages, then its links."""
    found: dict[str, list] = {}
    for one in extensions:
        if str(one.get("state") or "") != "loaded":
            continue
        name = str(one.get("name") or "")
        for declared in one.get("lists") or []:
            area = str(declared["area"])
            found.setdefault(area, []).append((
                view_key(name, str(declared.get("key") or "")),
                str(declared.get("title") or ""), str(declared.get("icon") or ICON),
                AREA_FEATURE.get(area, install_identity.CORE)))
        for declared in one.get("pages") or []:
            area = str(declared["area"])
            found.setdefault(area, []).append((
                page_view_key(name, str(declared.get("key") or "")),
                str(declared.get("title") or ""), str(declared.get("icon") or ICON),
                AREA_FEATURE.get(area, install_identity.CORE)))
        for link in one.get("links") or []:
            area = str(link.get("area") or "")
            if area:
                found.setdefault(area, []).append((
                    ext_links.view_key(name, str(link.get("key") or "")),
                    str(link.get("label") or ""), str(link.get("icon") or ICON),
                    AREA_FEATURE.get(area, install_identity.CORE)))
    return {area: tuple(items) for area, items in found.items()}


def scope_of(extension: dict[str, Any], declared: dict[str, Any]) -> str:
    """What names the list's grid to the page, and so the row an address opens it on."""
    return (f"manager.community.{extension.get('name') or ''}"
            f".{declared.get('key') or ''}")


def find(view: str, extensions: Sequence[dict[str, Any]]) -> tuple[dict, dict] | None:
    return next(((one, declared) for one, declared in lists(extensions)
                 if view_key(str(one.get("name") or ""), str(declared.get("key") or ""))
                 == view), None)


def ranked_orders(extensions: Sequence[dict[str, Any]]) -> dict[str, str]:
    """The order a collection stores for each ranked view, and what the menu calls it."""
    return {rankings.token(str(one.get("name") or ""), str(declared.get("key") or ""),
                           str(view.get("key") or "")):
            ranked_label({"title": declared.get("title"), "name": view.get("name")})
            for one, declared in lists(extensions) for view in rankings.views_of(declared)}


def ranked_label(ranking: dict[str, Any]) -> str:
    """A collection's `ranking` as the order menu and the grid name it."""
    if ranking.get("offered", True):
        return t("order.by.ranked", title=str(ranking.get("title") or ""),
                 name=str(ranking.get("name") or ""))
    return t("order.by.ranked_off", extension=str(ranking.get("display_name")
                                                  or ranking.get("extension") or ""))


def library_columns(declared: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The columns that hold a game or a table, the identifier first where it is one."""
    held = [one for one in declared.get("columns") or [] if one.get("kind") in LIBRARY_KINDS]
    return sorted(held, key=lambda one: not one.get("identifier"))


def identifier_of(declared: Mapping[str, Any]) -> dict[str, Any]:
    """The column drawn as the row's name: the one core marked, else the first."""
    found = declared.get("columns") or []
    return next((one for one in found if one.get("identifier")), found[0] if found else {})


def _glyph(column: Mapping[str, Any]) -> str:
    """What the identifier's frame holds where there is no picture."""
    return (str(column.get("icon") or "")
            or {"game": icons.GAMES, "table": icons.TABLES}.get(str(column.get("kind")), ICON))


def _frames(column: Mapping[str, Any]) -> bool:
    return column.get("kind") in LIBRARY_KINDS or bool(column.get("picture") or column.get("icon"))


def columns(declared: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    subject = identifier_of(declared)
    for one in declared.get("columns") or []:
        kind = str(one.get("kind") or "text")
        extra = {**_KIND.get(kind, {}), **(when.cell(one["field"]) if kind == "date" else {})}
        header = str(one.get("header") or one["field"])
        said = str(one.get("help") or grid.NO_HELP)
        if one is subject:
            out.append(grid.identifier(
                one["field"], header, FIRST_WIDTH, help=said, subtitle=UNDER, link=HELD,
                **({"picture": thumbnails.FIELD, "glyph": _glyph(one)} if _frames(one) else {}),
                **extra))
            if declared.get("relation"):
                out.append(_in_library())
        elif kind in LIBRARY_KINDS:
            out.append(grid.named(one["field"], header, FIRST_WIDTH, help=said,
                                  subtitle=one["field"] + LINE, link=one["field"]))
        elif kind == "chips":
            out.append(grid.list_column(one["field"], header, _WIDTH[kind], help=said))
        else:
            out.append(grid.column(one["field"], header, _WIDTH.get(kind, 0), help=said,
                                   **extra, **_drawn(kind, one, header)))
    return out


# An extension names a state's cost as a tier; the Manager draws it as a tone. `unknown` is
# the question mark, and a state with neither a tier nor an icon is a plain word.
_TONES = {"on": states.ON, "off": states.QUIET, "warn": states.WARN, "bad": states.BAD}


def _state_of(value: str, look: dict[str, Any]) -> states.State:
    tier = look.get("tier") or ""
    return states.State(value, look["label"], look.get("why") or "",
                        tone=_TONES.get(tier, states.PLAIN),
                        glyph=states.UNKNOWN if tier == "unknown" else "",
                        icon=look.get("icon") or "")


def _drawn(kind: str, column: dict[str, Any], header: str) -> dict[str, Any]:
    if kind == "state":
        table = states.Table(covers="", states=tuple(
            _state_of(value, look) for value, look in column["states"].items()))
        return {**grid.choice_filter(table.choices(), formatted=True),
                ":valueFormatter": "params => (("
                + json.dumps({one.token: one.word for one in table.states})
                + ")[params.value] ?? params.value ?? '')",
                **renderers.drawable("state", states=table.looks(), plain=True)}
    if kind == "link":
        return renderers.drawable("link", says=column.get("name_from") or "", header=header)
    if kind == "picture":
        return {**renderers.drawable("image"), "sortable": False, "filter": False}
    return {}


def _in_library() -> dict[str, Any]:
    return grid.column(HELD, t("manager.community.in_library"),
                       help=grid.NO_HELP,
                       **grid.choice_filter(
                           [{"value": True, "label": t("manager.community.held")},
                            {"value": False, "label": t("manager.community.not_held")}],
                           formatted=True))


def presets(declared: dict[str, Any]) -> dict[str, views.Preset]:
    """The extension's views, preceded by Yours, which takes the first view's columns and
    sort. In Library is in the column picker and in no view: the name is the link."""
    out = {view["key"]: views.Preset(
        name=view["name"],
        columns=tuple(view["columns"]),
        sort=tuple({"colId": one["field"], "sort": "desc" if one.get("desc") else "asc",
                    "sortIndex": index} for index, one in enumerate(view.get("sort") or [])),
        help=str(view.get("help") or "")) for view in declared.get("views") or []}
    shown = tuple(one["field"] for one in declared.get("columns") or [])
    first = next(iter(out.values()), None)
    if not out:
        out["manager.view.everything"] = views.Preset(columns=shown)
    if not declared.get("relation"):
        return out
    yours = views.Preset(
        columns=first.columns if first else shown, sort=first.sort if first else (),
        filters={HELD: {"values": [True]}}, help=t("manager.community.yours.help"))
    return {"manager.community.yours": yours, **out}


class Lens:
    """The library's tables as the Tables grid has them, found by game or by table."""

    def __init__(self, tables: Sequence[dict[str, Any]], art: bool) -> None:
        self.art = art
        self.game: dict[str, dict[str, Any]] = {}
        self.table: dict[tuple[str, str], dict[str, Any]] = {}
        for row in tables:
            game = str(row.get("game_id") or "")
            if game not in self.game or row.get("default"):
                self.game[game] = row
            self.table[(game, str(row.get("id") or ""))] = row

    def face(self, kind: str, mine: Mapping[str, Any]) -> tuple[str, str, str]:
        """(name, line, art) of the game or table the library holds."""
        game, table = str(mine.get("game_id") or ""), str(mine.get("table_id") or "")
        hit = self.table.get((game, table)) if kind == "table" and table else self.game.get(game)
        if hit is None:
            return str(mine.get("name") or ""), "", ""
        line = [game_tables.made(hit)]
        if kind == "table" and table:
            line.append(game_tables.table_name(hit))
        return (str(hit.get("game") or mine.get("name") or ""),
                game_tables.JOIN.join(part for part in line if part),
                thumbnails.address(hit) if self.art else "")


def face_of(column: Mapping[str, Any], row: Mapping[str, Any], lookup: Lookup,
            lens: Lens) -> dict[str, str]:
    """Name, line, art, address and tip of the game or table a row's id names."""
    said = str(row.get(column["field"]) or "")
    kind = str(column["kind"])
    mine = lookup.held.get(said)
    if mine:
        name, line, art = lens.face(kind, mine)
        return {"name": name, "line": line, "art": art,
                "href": _address_of(mine, kind), "tip": t("manager.community.held")}
    known = lookup.known.get(said) or {}
    name = str(known.get("name") or row.get(column.get("name_from") or "") or said)
    line = game_tables.made(known) if kind == "game" else game_tables.table_name(known)
    elsewhere = lookup.other.get(said)
    if not elsewhere:
        return {"name": name, "line": line, "art": "", "href": "", "tip": ""}
    version = str(elsewhere.get("version") or "")
    tip = (t("manager.community.other_version", version=version) if version
           else t("manager.community.other_version_unknown"))
    return {"name": name, "line": line, "art": "", "href": str(elsewhere.get("url") or ""),
            "tip": tip}


def rows(found: list[dict[str, Any]], declared: dict[str, Any],
         held: dict[str, dict[str, Any]],
         other: dict[str, dict[str, Any]] | None = None, *,
         known: dict[str, dict[str, Any]] | None = None,
         later: Mapping[str, Lookup] | None = None,
         tables: Sequence[dict[str, Any]] = (), art: bool = False) -> list[dict[str, Any]]:
    """`other` is the releases held as another version, by release id, and `known` what
    VPSdb calls the ids not held. `later` is the same for each later game or table column,
    by field; `tables` is the library's tables, and `art` whether the list draws pictures."""
    declared_columns = declared.get("columns") or []
    first = identifier_of(declared)
    subject = first.get("kind") in LIBRARY_KINDS
    dates = [one["field"] for one in declared_columns if one.get("kind") == "date"]
    under = list(first.get("under") or [])
    lens = Lens(tables, art)
    out = []
    for index, row in enumerate(found):
        line = " ".join(str(row[one]) for one in under if row.get(one) not in (None, ""))
        mine = held.get(str(row.get(first["field"]) or "")) if subject else None
        shown: dict[str, Any] = {}
        href = tip = ""
        if subject:
            face = face_of(first, row, Lookup(held, other or {}, known or {}), lens)
            shown[first["field"]] = face["name"]
            href, tip = face["href"], face["tip"]
            line = game_tables.JOIN.join(
                part for part in (face["line"], line, "" if mine else tip) if part)
        if art and _frames(first):
            shown[thumbnails.FIELD] = (face["art"] if subject else "") \
                or str(row.get(first.get("picture") or "") or "")
        for one in declared_columns:
            if one is not first and one.get("kind") in LIBRARY_KINDS:
                face = face_of(one, row, (later or {}).get(one["field"], Lookup()), lens)
                shown |= {one["field"]: face["name"], one["field"] + LINE: face["line"],
                          one["field"] + "_href": face["href"],
                          one["field"] + "_tip": face["tip"]}
        named = str(row.get("id") or "")
        built = {**row, "id": named or f"{UNNAMED}{index}", HELD: bool(mine), UNDER: line,
                 **({} if named else {grid.NO_MENU: True, grid.NOT_SELECTABLE: True}),
                 f"{HELD}_href": href, f"{HELD}_tip": tip, **shown}
        for field in dates:
            built = when.said(built, field)
        out.append(built)
    return out


def _address_of(mine: Mapping[str, Any], kind: str) -> str:
    game, table = str(mine.get("game_id") or ""), str(mine.get("table_id") or "")
    if kind == "table" and table:
        return "/manager?" + deeplink.query({"view": "tables", "game": game, "table": table})
    return "/manager?" + deeplink.query({"view": "games", "game": game})


# Where a list's line may send somebody, by the name its `about` gives the place.
PLACES: dict[str, Callable[[str], str]] = {
    "players": lambda _extension: "/manager?" + deeplink.query({"view": "players"}),
    "settings": ext_page.address,
}


def standing(extension: str, about: str) -> dict[str, Any]:
    """What a list's `about` route answers now, or nothing where there is none to ask or
    it cannot say."""
    if not about:
        return {}
    try:
        said = ApiClient().ext_get(f"/ext/{extension}{about}")
    except (ApiError, OSError) as exc:
        logger.warning("%s did not say how its list stands: %s", extension, exc)
        return {}
    return said if isinstance(said, dict) else {}


def status_of(said: dict[str, Any]) -> dict[str, str]:
    """The line, as `{text, detail, to}`, from a string or from an object saying so."""
    found = said.get("status")
    if not isinstance(found, dict):
        found = {"text": found}
    to = str(found.get("to") or "")
    return {"text": str(found.get("text") or "").strip(),
            "detail": str(found.get("detail") or ""), "to": to if to in PLACES else ""}


def web(url: Any) -> str:
    """An address a browser may open for an extension, or "": nothing but the web."""
    said = str(url or "").strip()
    return said if said.startswith(("https://", "http://")) else ""


def acts(extension: dict[str, Any], said: dict[str, Any],
         run: Callable[[dict[str, Any]], Any]) -> list[panel.Verb]:
    """The menu: what the list's `about` offers, in its order, then Settings where the
    extension has some. An act with a `url` is a link and never reaches the extension."""
    def pressed(act: dict[str, Any]) -> Callable[[], Any]:
        return lambda: run(act)

    offered = []
    for act in said.get("acts") or []:
        if not isinstance(act, dict) or not str(act.get("key") or "").strip():
            continue
        label = str(act.get("label") or act["key"])
        if "url" not in act:
            offered.append(panel.Verb(label, run=pressed(act)))
        elif web(act["url"]):
            offered.append(panel.Verb(label, to=web(act["url"])))
    if (extension.get("surfaces") or {}).get("settings"):
        offered.append(panel.Verb(t("manager.community.settings"),
                                  to=ext_page.address(str(extension.get("name") or ""))))
    return offered


def said_status(slot: Any, status: dict[str, str], extension: str) -> None:
    """The line into `slot`, a link where it names a place."""
    slot.clear()
    if not status["text"]:
        return
    with slot:
        if status["to"]:
            with ui.element("div").classes("manager-help"):
                panel.link(status["text"], to=PLACES[status["to"]](extension),
                           hint=status["detail"])()
        else:
            panel.line(status["text"], hint=status["detail"])


def declared_action(extension: dict[str, Any], key: str) -> dict[str, Any]:
    """The dialog `key` of `extension`, as the extension record carries it."""
    return next((one for one in extension.get("actions") or []
                 if one.get("key") == key), {})


@on_page
async def open_over(extension: dict[str, Any], key: str, ids: Sequence[str], *,
                    each: bool = False, who: Mapping[str, str] | None = None) -> bool:
    """Open the dialog `key` over the rows `ids`: one dialog for all of them, or with
    `each` one per row in turn, with Skip and Stop. `who` is the game it is opened for,
    for a dialog that is not opened over a row of a list. True once any of them ran."""
    name, action = str(extension.get("name") or ""), declared_action(extension, key)
    if not action:
        ui.notify(t("said.action_failed"), type="negative")
        return False
    batches = [list(ids)] if not each else [[one] for one in ids]
    ran = False
    for index, batch in enumerate(batches, 1):
        place = t("manager.walk.position", index=index, total=len(batches)) \
            if each and len(batches) > 1 else ""
        try:
            answer = await (
                ext_action.open_action(name, action, rows=batch, who=who, place=place,
                                       walking=True)
                if each else ext_action.open_action(name, action, rows=batch, who=who))
        except Exception as exc:  # noqa: BLE001 - the reason belongs on screen
            ui.notify(t("said.action_failed"), caption=why(exc), type="negative")
            answer = False
        if answer == walks.STOPPED:
            break
        ran = ran or answer is True
    return ran


@on_page
async def run_act(extension: dict[str, Any], about: str, act: dict[str, Any],
                  again: Callable[[], Any]) -> None:
    """Do one of the list's acts, say what came of it, and read the menu again. An act that
    names a dialog opens it instead."""
    if act.get("action"):
        await open_over(extension, str(act["action"]), [])
        await again()
        return
    try:
        said = await offload.io(ApiClient().ext_post,
                                f"/ext/{extension.get('name')}{about}/acts/"
                                f"{quote(str(act['key']), safe='')}", {})
    except Exception as exc:  # noqa: BLE001 - the reason belongs on screen
        ui.notify(t("said.action_failed"), caption=why(exc), type="negative")
        return
    await told(said, again)


async def told(said: Any, again: Callable[[], Any]) -> None:
    """What an act or a verb answered: its address opened, its message shown, the list
    and the line read again."""
    said = said if isinstance(said, dict) else {}
    if web(said.get("url")):
        link_tab.open_link(web(said["url"]))
    if said.get("message"):
        ui.notify(str(said["message"]))
    await again()


def verb_entries(declared: dict[str, Any], chosen: list[dict[str, Any]],
                 press: Callable[[dict[str, Any], list[dict[str, Any]]], Any]
                 ) -> list[panel.Verb]:
    """What the list's verbs offer for the rows `chosen`. A link verb opens the address in
    the row's own field; one that `needs` a field acts on the rows where it is true and is
    dimmed, with the extension's reason, where it is true on none."""
    out = []
    for verb in declared.get("verbs") or []:
        label, tip = str(verb.get("label") or verb["key"]), str(verb.get("tip") or "")
        if verb.get("link"):
            to = web(chosen[0].get(verb["link"])) if len(chosen) == 1 else ""
            out.append(panel.Verb(
                label, to=to, one=True, tip=tip,
                hint=t("manager.community.verb.no_address") if len(chosen) == 1 else ""))
            continue
        needs = str(verb.get("needs") or "")
        fit = [row for row in chosen if row.get(needs)] if needs else chosen
        out.append(panel.Verb(
            label, run=partial(press, verb, fit) if fit else None,
            danger=bool(verb.get("danger")), one=bool(verb.get("one")), tip=tip,
            hint=str(verb.get("needs_reason") or t("manager.community.verb.inapplicable"))))
    return out


@on_page
async def run_verb(extension: dict[str, Any], declared: dict[str, Any], verb: dict[str, Any],
                   chosen: list[dict[str, Any]], again: Callable[[], Any]) -> None:
    """Press one of the list's verbs on `chosen`, asking first where it is a danger. A verb
    that names a dialog opens it over the rows."""
    if verb.get("action"):
        if await open_over(extension, str(verb["action"]),
                           [str(row["id"]) for row in chosen], each=bool(verb.get("each"))):
            await again()
        return
    label = str(verb.get("label") or verb["key"])
    if verb.get("danger"):
        question = t(str(verb.get("confirm_key") or "") or "manager.community.verb.confirm",
                     label=label, count=len(chosen))
        if not await confirm.ask(question, confirm=label, icon=verbs.ACCEPT):
            return
    try:
        said = await offload.io(
            ApiClient().ext_post,
            f"/ext/{extension.get('name')}{declared['base']}"
            f"/verbs/{quote(str(verb['key']), safe='')}",
            {"ids": [str(row["id"]) for row in chosen]})
    except Exception as exc:  # noqa: BLE001 - the reason belongs on screen
        ui.notify(t("said.action_failed"), caption=why(exc), type="negative")
        await again()
        return
    await told(said, again)


def read(extension: str, key: str, fetch: Callable[[], dict]) -> dict[str, Any]:
    """Read a list and keep it. A read that fails answers with the last good one, said
    to be stale, and with `rows` None when there is none."""
    try:
        found = [one for one in (fetch() or {}).get("rows") or [] if isinstance(one, dict)]
    except (ApiError, OSError) as exc:
        return {**kept(extension, key), "stale": True, "error": why(exc)}
    return keep(extension, key, found)


def collection_for(collections: Sequence[dict[str, Any]], tag: str) -> str:
    """The smart collection whose rule is this tag alone, or ""."""
    return next((str(one.get("name") or "") for one in collections
                 if one.get("type") == "filter"
                 and collection_rules.named((one.get("filters") or {}).get("tags")) == [tag]),
                "")


def collection_ordered_by(collections: Sequence[dict[str, Any]], order_by: str) -> str:
    """The smart collection in this order, or ""."""
    return next((str(one.get("name") or "") for one in collections
                 if one.get("type") == "filter" and one.get("order_by") == order_by), "")


def free_name(wanted: str, collections: Sequence[dict[str, Any]]) -> str:
    taken = {str(one.get("name") or "").casefold() for one in collections}
    name, number = wanted, 2
    while name.casefold() in taken:
        name = t("manager.community.collection_n", name=wanted, n=number)
        number += 1
    return name


def _collection_address(name: str) -> str:
    return "/manager?" + deeplink.query({"view": "collections", "collection": name})


@on_page
async def _make_collection(library: Library, title: str, filters: dict[str, Any]) -> None:
    collections = await offload.io(library.load_collections)
    try:
        made = await offload.io(library.create_collection, free_name(title, collections),
                                filters)
    except Exception as exc:  # noqa: BLE001 - the reason belongs on screen
        ui.notify(t("said.action_failed"), caption=why(exc), type="negative")
        return
    name = str(made.get("name") or "")
    ui.notify(t("manager.collections.created", name=name), type="positive")
    ui.navigate.to(_collection_address(name))


def _collection_action(library: Library, existing: str, title: str,
                       filters: dict[str, Any]) -> None:
    if existing:
        panel.action(t("manager.community.open_collection"),
                     lambda: ui.navigate.to(_collection_address(existing)),
                     icon=verbs.GO, hint=existing)()
    else:
        panel.action(t("manager.community.make_collection"),
                     lambda: _make_collection(library, title, filters),
                     icon=verbs.CREATE,
                     hint=t("manager.community.make_collection.help"))()


def _tag_chip(said: dict[str, Any], library: Library) -> None:
    tag_chips.draw([str(said["tag"])], library.tag_looks())


def _said_age(age: Any, state: dict[str, Any]) -> None:
    age.clear()
    with age:
        panel.line(read_state(state), hint=str(state.get("error") or ""),
                   classes="text-xs manager-label")


def as_it_stands(extension: dict[str, Any]) -> dict[str, Any]:
    """The extension as the API has it now, or as given when the API cannot say."""
    name = str(extension.get("name") or "")
    try:
        found = ApiClient().extensions()
    except (ApiError, OSError):
        return extension
    return next((one for one in found if str(one.get("name") or "") == name), extension)


def _why(extension: dict[str, Any]) -> str:
    return str(extension.get("reason") or "") or t("extension.reason.none_recorded")


def not_running(extension: dict[str, Any]) -> None:
    """Drawn in Refresh's place."""
    off = str(extension.get("state") or "") == "off"
    chip = ui.label(t("word.off") if off else t("manager.community.stopped")) \
        .classes("manager-member-chip "
                 + ("manager-chip-quiet" if off else "manager-chip-warn"))
    if extension.get("reason_key") != SWITCHED_OFF:
        chip.tooltip(_why(extension))


def build(extension: dict[str, Any], declared: dict[str, Any], library: Library,
          subject: Any = None, on_open: Callable[..., Any] | None = None) -> None:
    """`subject` is the page header's slot for the list's menu. `on_open(extension,
    declared, rows, again)` is told which rows the pane beside the list is about: one when a row
    is focused, several when rows are ticked, none when the ticks go."""
    body = ui.column().classes("w-full grow min-h-0 gap-0")
    busy.fill(body, lambda: _fill(extension, declared, library, body, subject, on_open))


@on_page
async def _fill(extension: dict[str, Any], declared: dict[str, Any], library: Library,
                body: Any, subject: Any = None,
                on_open: Callable[..., Any] | None = None) -> None:
    from manager.library.games import view_control

    name = str(extension.get("name") or "")
    key = str(declared.get("key") or "")
    said = str(extension.get("display_name") or name)
    tagging, existing = {}, ""
    ranked = rankings.views_of(declared)
    collections = await offload.io(library.load_collections) \
        if declared.get("tag") or ranked else []
    if declared.get("tag"):
        await offload.io(library.read_tags)
        tagging = sources_of(library.tag_looks(), name, key)
        if tagging:
            existing = collection_for(collections, str(tagging["tag"]))
    route = f"/ext/{name}{declared.get('base') or ''}"

    def fetch() -> dict:
        return ApiClient().ext_get(route)

    now = await offload.io(as_it_stands, extension)
    stopped = str(now.get("state") or "") != "loaded"
    about = "" if stopped else str(declared.get("about") or "")
    line: list[Any] = []
    reread: list[Callable[[], Any]] = []

    async def after_act() -> None:
        await stands()
        if reread:
            await reread[0]()

    def stood(found: dict[str, Any]) -> None:
        if line and not line[0].is_deleted:
            said_status(line[0], status_of(found), name)
        if subject is not None and not subject.is_deleted:
            panel.subject_menu(subject, acts(
                now, found, lambda act: run_act(now, about, act, after_act)))

    async def stands() -> None:
        stood(await offload.io(standing, name, about))

    standing_now = await offload.io(standing, name, about)
    state = await offload.io(kept, name, key)
    reading = state["rows"] is None and not stopped
    if stopped:
        state = {**state, "stale": True}
    if reading:
        state = await offload.io(read, name, key, fetch)
    if state["rows"] is None:
        body.clear()
        with body:
            panel.facts(ui, [
                panel.intro(t("manager.community.not_running", name=said, reason=_why(now)))
                if stopped else panel.intro(t("manager.community.read_failed", name=said),
                                            hint=str(state["error"]))])
            with ui.row().classes("items-center gap-2 px-3"):
                line.append(ui.element("div"))
                if tagging:
                    _tag_chip(tagging, library)
        stood(standing_now)
        return
    subjects = library_columns(declared)
    look = library.thumbnail_look()

    async def drawn(found: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if subjects or look is not None:
            await offload.io(library.load_tables)
        looked = {}
        for one in subjects:
            said = await offload.io(library.owned_as,
                                    [str(row.get(one["field"]) or "") for row in found],
                                    str(one["keys"]), str(one["kind"]))
            looked[one["field"]] = Lookup(said.get("owned") or {},
                                          said.get("other_versions") or {},
                                          said.get("known") or {})
        mine = looked.get(identifier_of(declared).get("field")) or Lookup()
        return rows(found, declared, dict(mine.held), dict(mine.other), known=mine.known,
                    later=looked, tables=library.table_rows(), art=look is not None)

    built = await drawn(state["rows"])
    by_id = {row["id"]: row for row in built}
    framed = bool(declared.get("columns")) and _frames(identifier_of(declared))
    shown = grid.with_art(columns(declared), look if framed else None)
    scope = scope_of(extension, declared)
    fields = [one["field"] for one in shown]
    offering = bool(declared.get("verbs")) and not stopped
    opener = on_open if declared.get("detail") and not stopped else None

    async def pressed(verb: dict[str, Any], chosen: list[dict[str, Any]]) -> None:
        await run_verb(now, declared, verb, chosen, lambda: read_again())

    @on_page
    async def added(action: str) -> None:
        if await open_over(now, action, []):
            await read_again()

    adding = [] if stopped else [
        (str(one["label"]), partial(added, str(one["action"])))
        for one in declared.get("add") or []]

    body.clear()
    with body:
        with ui.row().classes("w-full items-center gap-2 px-3 py-2 mb-2 shrink-0 "
                              "manager-panel manager-grid-bar"):
            bar = panel.grid_bar()
            wire_views, _picker, showing, describe = view_control(
                library, scope, presets(declared), fields, shown, bar=bar)
            describe()
            with bar.top, panel.bar_end():
                for view in ranked:
                    order_by = rankings.token(name, key, view["key"])
                    with ui.row().classes("items-center gap-2 no-wrap") \
                            .bind_visibility_from(_picker, "value",
                                                  value=views.builtin_id(view["key"])):
                        _collection_action(
                            library, collection_ordered_by(collections, order_by),
                            ranked_label({"title": declared.get("title"),
                                          "name": view.get("name")}),
                            {"order_by": order_by})
                if tagging:
                    ranked_ids = {views.builtin_id(view["key"]) for view in ranked}
                    with ui.row().classes("items-center gap-2 no-wrap") \
                            .bind_visibility_from(_picker, "value",
                                                  backward=lambda on: on not in ranked_ids):
                        _collection_action(library, existing,
                                           str(declared.get("title") or ""),
                                           {"tags": [str(tagging["tag"])]})
                if adding:
                    panel.add_action(adding, empty=not built)
                search = panel.search(t("manager.community.search"))
            with bar.bottom, panel.bar_end():
                line.append(ui.element("div"))
                if tagging:
                    _tag_chip(tagging, library)
                age = ui.element("div")
                _said_age(age, state)
                count = ui.label(t("manager.community.rows", count=len(built))) \
                    .classes("text-xs manager-label")
                if offering:
                    show_selection = panel.selection_menu(
                        t("manager.community.verbs_menu"), lambda: grid.selection(table),
                        lambda chosen: verb_entries(declared, chosen, pressed),
                        lambda: table.run_grid_method("deselectAll"))
                if stopped:
                    not_running(now)
                else:
                    again = panel.refresh(lambda: read_again(asked=True),
                                          t("manager.community.read_again", name=said))

        async def on_header_context(col_id: str | None) -> None:
            await grid.header_menu(menu, table, shown, col_id)

        picked: list[dict[str, Any]] = []
        on_screen = {"rows": len(built)}

        def counted_said() -> str:
            seen = on_screen["rows"]
            if picked:
                return grid.selection_said(table, len(picked), t(
                    "manager.community.selected", picked=len(picked), shown=seen))
            return (t("manager.community.rows", count=len(built)) if seen == len(built)
                    else t("manager.community.rows_of", shown=seen, count=len(built)))

        several = {"on": False}

        def on_select_rows(chosen: list[dict[str, Any]]) -> Any:
            picked[:] = chosen
            show_selection(bool(chosen))
            count.text = counted_said()
            if opener is not None and (len(chosen) > 1 or several["on"]):
                several["on"] = len(chosen) > 1
                return opener(now, declared, chosen if len(chosen) > 1 else [], read_again)
            return None

        def on_context(row: dict | None) -> None:
            if not row or row.get(grid.NO_MENU):
                menu.clear()
                return
            on = grid.menu_rows(table, row)
            panel.verb_menu(
                menu, str(row.get(identifier_of(declared).get("field")) or "") if len(on) == 1
                else t("manager.community.rows", count=len(on)),
                panel.for_selection(verb_entries(declared, on, pressed), len(on)))

        with ui.element("div").classes("w-full grow min-h-0 flex flex-col"):
            table = grid.build(
                shown, built, scope, on_select_rows if offering else None,
                on_context if offering else None, on_header_context, view_of=showing,
                rows_not_selectable=[] if offering else None)
            menu = ui.context_menu()

        if opener is not None:
            def focused(event: Any) -> Any:
                if grid.ticked(event):
                    return None
                row = by_id.get(grid.focused_row(event))
                if row is None or str(row["id"]).startswith(UNNAMED):
                    return None
                several["on"] = False
                return opener(now, declared, [row], read_again)

            grid.on_row_focus(scope, focused)

        async def counted() -> None:
            seen = await table.run_grid_method("getDisplayedRowCount")
            on_screen["rows"] = int(seen if isinstance(seen, int) else len(built))
            count.text = counted_said()

        table.on("modelUpdated", counted)
        wire_views(table)
        search.on_value_change(
            lambda: table.run_grid_method("setGridOption", "quickFilterText",
                                          search.value or ""))
    stood(standing_now)

    @on_page
    async def read_again(asked: bool = False) -> None:
        again.disable()
        fresh = await offload.io(read, name, key, fetch)
        found = None if fresh["stale"] else await drawn(fresh["rows"])
        if table.is_deleted:
            return
        again.enable()
        _said_age(age, fresh)
        if found is not None:
            grid.replace_rows(table, built, by_id, found, lambda _row: True)
        elif asked:
            ui.notify(t("manager.community.read_failed", name=said),
                      caption=str(fresh["error"]), type="negative")

    reread.append(read_again)
    if not reading and not stopped:
        asyncio.create_task(read_again())
