"""Rows of the Games and Tables grids dragged onto a collection: the drag, the places it
can be let go, and what a drop adds."""

from __future__ import annotations

import logging
from typing import Any

from nicegui import ui

from common.i18n import t
from manager.kit import grid, offload, verbs
from manager.library import collection_adds

logger = logging.getLogger("vpinfe.manager.library.row_drag")

GAMES = collection_adds.GAMES
TABLES = collection_adds.TABLES

# The event a drop sends, and the one a drag starting in this window sends.
DROPPED = "hub_rows_dropped"
DRAGGED = "hub_rows_dragged"

# On an element a drop can land on: the collection it adds to. Inside it, LIST marks the
# list whose rows a drop lands between, PLACED on that list says the place is kept, and
# END says a drop elsewhere lands at its end.
TARGET = "data-drop-collection"
LIST = "data-drop-list"
PLACED = "data-drop-at"
END = "data-drop-end"


def source(what: str) -> dict[str, Any]:
    """What makes a grid's identifier column the place a drag starts."""
    return {"dndSource": True,
            ":dndSourceOnRowDrag": f"params => window.__hubRowDrag(params, '{what}')"}


_SCRIPT = r"""
if (!window.__hubRowDrop) {
  window.__hubRowDrop = true;
  const TYPE = 'text/uri-list';

  window.__hubRowDrag = (params, what) => {
    const picked = params.api.getSelectedNodes();
    const nodes = picked.includes(params.rowNode) ? picked : [params.rowNode];
    const rows = nodes.map(node => what === 'tables'
      ? {game: String(node.data.game_id), table: String(node.data.id)}
      : {game: String(node.data.id), table: ''});
    const address = (row) => location.origin + '/manager?' + new URLSearchParams(
      what === 'tables' ? {view: 'tables', game: row.game, table: row.table}
                        : {view: 'games', game: row.game}).toString();
    const names = nodes.map(node => node.data.name || node.data.game || '');
    const moving = params.dragEvent.dataTransfer;
    moving.effectAllowed = 'copy';
    moving.setData(TYPE, rows.map(address).join('\r\n'));
    moving.setData('application/json', JSON.stringify({from: location.origin, rows: rows}));
    moving.setData('text/plain', names.join('\n'));
    const image = document.createElement('div');
    image.className = 'manager-drag-image';
    const name = document.createElement('span');
    name.className = 'manager-drag-name';
    name.textContent = names[Math.max(0, nodes.indexOf(params.rowNode))];
    image.appendChild(name);
    if (nodes.length > 1) {
      const count = document.createElement('span');
      count.className = 'manager-drag-count';
      count.textContent = String(nodes.length);
      image.appendChild(count);
    }
    document.body.appendChild(image);
    moving.setDragImage(image, 16, 16);
    setTimeout(() => image.remove());
    const entry = document.querySelector('a.manager-nav-row[href="/manager?view=collections"]');
    const drawer = document.querySelector('.q-drawer');
    if (entry && drawer) {
      document.body.style.setProperty('--drops-top', entry.getBoundingClientRect().top + 'px');
      document.body.style.setProperty('--drops-left', drawer.getBoundingClientRect().right + 'px');
    }
    document.body.classList.add('manager-dragging-rows');
    emitEvent('__DRAGGED__');
  };

  // What a drop carries, read back: rows from this install, and how many from another.
  const readRows = (moving) => {
    const rows = [];
    let foreign = 0;
    for (const line of (moving.getData(TYPE) || '').split(/\r?\n/)) {
      const said = line.trim();
      if (!said || said.startsWith('#')) continue;
      let url;
      try { url = new URL(said); } catch (err) { continue; }
      const view = url.searchParams.get('view');
      const game = url.searchParams.get('game');
      if (!game || (view !== 'games' && view !== 'tables')) continue;
      if (url.host !== location.host) { foreign += 1; continue; }
      rows.push({game: game, table: view === 'tables' ? (url.searchParams.get('table') || '')
                                                        : ''});
    }
    return {rows: rows, foreign: foreign};
  };

  const carries = (event) => {
    const types = Array.from((event.dataTransfer && event.dataTransfer.types) || []);
    return types.includes(TYPE) && !types.includes('Files');
  };

  // The collection a drop here adds to. Anywhere in a collection's panel is that
  // collection, its header included; in the rail each collection is its own.
  const zoneAt = (el) => {
    if (!el || !el.closest) return null;
    const own = el.closest('[data-drop-collection]');
    if (own) return own;
    const panel = el.closest('.manager-workbench');
    return panel ? panel.querySelector('[data-drop-collection]') : null;
  };
  const ringOf = (zone) => zone.closest('.manager-workbench') || zone;

  // Where a drop at this height lands: between two rows over a list that keeps a place,
  // at the end of a list in Custom Order from anywhere else, and null where the order
  // decides and there is nothing to show.
  const placing = (zone, y) => {
    const list = zone.querySelector('[data-drop-list]');
    if (!list) return null;
    const rows = [...list.querySelectorAll('.manager-member-row')];
    if (!rows.length) return null;
    const box = list.getBoundingClientRect();
    if (list.hasAttribute('data-drop-at') && y >= box.top && y <= box.bottom) {
      const at = rows.findIndex(row => {
        const edge = row.getBoundingClientRect();
        return y < edge.top + edge.height / 2;
      });
      return {list: list, rows: rows, at: at < 0 ? rows.length : at};
    }
    return list.hasAttribute('data-drop-end') ? {list: list, rows: rows, at: null} : null;
  };

  let ring = null, line = null, quiet = 0;
  const clear = () => {
    clearTimeout(quiet);
    if (ring) ring.classList.remove('manager-drop-hot');
    ring = null;
    if (line) line.remove();
    line = null;
  };

  const show = (zone, y) => {
    const lit = ringOf(zone);
    if (ring !== lit) { clear(); ring = lit; ring.classList.add('manager-drop-hot'); }
    clearTimeout(quiet);
    quiet = setTimeout(clear, 1000);
    const where = placing(zone, y);
    if (!where) { if (line) line.remove(); line = null; return; }
    const last = where.rows[where.rows.length - 1];
    const top = where.at === null || where.at >= where.rows.length
      ? last.offsetTop + last.offsetHeight : where.rows[where.at].offsetTop;
    if (!line) { line = document.createElement('div'); line.className = 'manager-drop-line'; }
    if (line.parentElement !== where.list) where.list.appendChild(line);
    line.style.top = top + 'px';
  };

  // Refused anywhere that is not a collection.
  document.addEventListener('dragover', (event) => {
    if (!carries(event)) return;
    event.preventDefault();
    const zone = zoneAt(event.target);
    event.dataTransfer.dropEffect = zone ? 'copy' : 'none';
    if (zone) show(zone, event.clientY);
    else clear();
  }, true);

  const inside = (el, event) => {
    const box = el.getBoundingClientRect();
    return event.clientX > box.left && event.clientX < box.right
      && event.clientY > box.top && event.clientY < box.bottom;
  };

  document.addEventListener('dragleave', (event) => {
    if (!ring) return;
    const into = event.relatedTarget;
    if (into ? ring.contains(into) : inside(ring, event)) return;
    clear();
  }, true);

  // Captured and stopped here, so a row drop never reaches the handler for dropped
  // files, which would call it an empty drop.
  document.addEventListener('drop', (event) => {
    if (!carries(event)) return;
    event.preventDefault();
    event.stopPropagation();
    const zone = zoneAt(event.target);
    const where = zone ? placing(zone, event.clientY) : null;
    clear();
    document.body.classList.remove('manager-dragging-rows');
    if (!zone) return;
    const {rows, foreign} = readRows(event.dataTransfer);
    if (!rows.length && !foreign) return;
    const turn = window.__hubDropTurn = (window.__hubDropTurn || 0) + 1;
    const parts = [[]];
    let size = 0;
    for (const row of rows) {
      const cost = row.game.length + row.table.length + 8;
      if (size + cost > __CHARS__ && parts[parts.length - 1].length) {
        parts.push([]);
        size = 0;
      }
      parts[parts.length - 1].push([row.game, row.table]);
      size += cost;
    }
    const collection = zone.getAttribute('data-drop-collection');
    const place = where ? where.at : null;
    parts.forEach((part, at) => emitEvent('__DROPPED__', {
      collection, place, foreign, turn, at, of: parts.length, rows: part}));
  }, true);

  document.addEventListener('dragend', () => {
    clear();
    document.body.classList.remove('manager-dragging-rows');
  }, true);
}
"""


def install() -> None:
    """Put the drag and the drop on the page. Once per page, before any grid."""
    ui.run_javascript(_SCRIPT.replace("__DRAGGED__", DRAGGED).replace("__DROPPED__", DROPPED)
                      .replace("__CHARS__", str(grid.SELECTION_PART_CHARS)))


def rail(state: dict[str, Any]) -> None:
    """The collections a drag can land on in the rail, drawn under its Collections
    entry and shown only while rows are being dragged. Filled when a drag starts."""
    state["rail_drops"] = ui.element("div").classes("manager-rail-drops")


async def dragging(library: Any, state: dict[str, Any]) -> None:
    """A drag has started here: fill the rail with the collections as they are now."""
    box = state.get("rail_drops")
    if box is None or box.is_deleted:
        return
    try:
        collections = await offload.io(library.load_collections)
    except Exception:  # noqa: BLE001 - the rail stays as it was, and a menu still adds
        logger.warning("manager: could not read the collections for the rail",
                       exc_info=True)
        return
    box.clear()
    with box:
        for one in sorted(collections, key=lambda one: str(one.get("name") or "").casefold()):
            name = str(one.get("name") or "")
            row = ui.element("div").classes("manager-drop-target")
            row._props[TARGET] = name
            with row:
                if (one.get("type") or "") == "filter":
                    ui.icon(verbs.SMART).classes("manager-menu-mark")
                else:
                    ui.element("span").classes("manager-menu-mark")
                ui.label(name).classes("manager-drop-name")
    box.client.run_javascript(
        f"setTimeout(() => document.getElementById('c{box.id}')"
        "?.scrollIntoView({block: 'nearest'}))")


async def dropped(library: Any, state: dict[str, Any], args: Any) -> None:
    """Rows let go on a collection: added through the one add, or refused where they
    came from another install. `args` is one part of the drop; nothing happens until the
    last is in."""
    said = args if isinstance(args, dict) else {}
    pairs = state.setdefault("drop_parts", grid.Parts("rows")).take(said)
    if pairs is None:
        return
    name = str(said.get("collection") or "")
    if int(said.get("foreign") or 0):
        ui.notify(t("manager.adds.from_elsewhere"), type="warning")
        return
    rows = [collection_adds.Row(str(pair[0]), str(pair[1] or ""))
            for pair in pairs if isinstance(pair, list) and len(pair) == 2 and pair[0]]
    if not name or not rows:
        return
    at = said.get("place")

    async def redrawn() -> None:
        if state.get("view") == "collections":
            reread = state.get("refresh_collections")
            if callable(reread):
                await reread(name if state.get("collection") == name else "")
        elif state.get("view") == "games":
            refresh = state.get("refresh_games")
            if callable(refresh):
                await refresh([row.game for row in rows])

    await collection_adds.add(library, name, rows,
                              what=TABLES if any(row.table for row in rows) else GAMES,
                              at=at if isinstance(at, int) else None, then=redrawn)
