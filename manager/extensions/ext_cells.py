"""A game or a table an extension names in a pane, a page or a group, drawn as the name
control: the library's own picture, name and line where it holds it, VPSdb's where it does
not, and the fact's own words where neither knows the id.
"""

from __future__ import annotations

from collections.abc import Sequence
from functools import partial
from typing import Any

from nicegui import ui

from common import icons
from manager.extensions import ext_lists
from manager.kit import game_tables, offload, panel, thumbnails

KINDS = ext_lists.LIBRARY_KINDS
SAYS = "said"
LOCAL = "/manager"


def _library_facts(sections: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    return [fact for section in sections for fact in section.get("facts") or []
            if fact["kind"] in KINDS and fact["value"]]


async def prepared(context: dict[str, Any], sections: Sequence[dict[str, Any]]
                   ) -> dict[str, Any]:
    """`context` with what the library and VPSdb say of the games and tables in `sections`.
    Read off the loop; `draw` only reads it."""
    facts = _library_facts(sections)
    library = context.get("library")
    if not facts or library is None:
        return context
    look = library.thumbnail_look()
    await offload.io(library.load_tables)
    asked: dict[tuple[str, str], set[str]] = {}
    for fact in facts:
        asked.setdefault((fact["keys"], fact["kind"]), set()).add(str(fact["value"]))
    looks = {}
    for (keys, kind), ids in asked.items():
        said = await offload.io(library.owned_as, sorted(ids), keys, kind)
        looks[(keys, kind)] = ext_lists.Lookup(said.get("owned") or {},
                                               said.get("other_versions") or {},
                                               said.get("known") or {})
    return {**context, "looks": looks, "look": look,
            "lens": ext_lists.Lens(library.table_rows(), look is not None)}


def draw(fact: dict[str, Any], context: dict[str, Any]) -> Any:
    """The value slot of a game or table fact."""
    kind, keys = str(fact["kind"]), str(fact["keys"])
    if not fact["value"]:
        return str(fact.get("says") or "")
    column = {"field": "id", "kind": kind, "name_from": SAYS}
    row = {"id": fact["value"], SAYS: fact.get("says") or ""}
    lookup = (context.get("looks") or {}).get((keys, kind)) or ext_lists.Lookup()
    lens = context.get("lens") or ext_lists.Lens((), False)
    face = ext_lists.face_of(column, row, lookup, lens)
    picture = str(face["art"] or fact.get("picture") or "")
    if face["tip"] and not face["href"].startswith(LOCAL):
        face = {**face, "line": game_tables.JOIN.join(
            part for part in (face["line"], face["tip"]) if part)}
    look = context.get("look")
    return partial(_cell, face, picture, look, icons.GAMES if kind == "game" else icons.TABLES,
                   str(fact.get("help") or ""))


def _cell(face: dict[str, str], picture: str, look: Any, glyph: str, hint: str) -> None:
    to = face["href"]
    with thumbnails.beside(picture if look is not None else None, glyph, to=to, look=look), \
            ui.column().classes("gap-0 grow min-w-0"):
        if to.startswith(LOCAL):
            panel.link(face["name"], to=to, hint=hint)()
        elif to:
            panel.link_out(face["name"], to=to, hint=hint)()
        else:
            ui.label(face["name"]).classes("manager-cell-identifier")
        if face["line"]:
            ui.label(face["line"]).classes("manager-cell-made")
