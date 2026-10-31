"""Grids, and the column layout kept for each.

Layout only - width, order, pinning. Which columns are shown, how they are
sorted and what is filtered belong to a view; see manager/kit/views.py.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import weakref
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from nicegui import run, ui

from common import i18n
from common.i18n import t
from manager.kit import offload, renderers, thumbnails
from manager.kit.on_page import on_page

logger = logging.getLogger("vpinfe.manager.kit.grid")

DEFAULT_COL_DEF: dict[str, Any] = {
    "sortable": True,
    "resizable": True,
    "filter": True,
    "minWidth": 60,
    # Lets a header carrying a newline take the second line it needs.
    "autoHeaderHeight": True,
}

# Layout belongs to the *view*, not to the grid: a tick column wants to be narrow and a
# name column wants to be wide, and they are the same column under two views. Visibility,
# sort and filters are the view's too - what keeps a built-in a constant is that none of
# it is stored against the built-in's definition. See manager/kit/views.py.
#
# Each event, and the part of a `Layout` it changes.
_SAVE_EVENTS = {"columnMoved": "order", "columnResized": "widths", "columnPinned": "pins"}
SAVE_THROTTLE_S = 0.6
# Emits only a person's own gestures, the events whose AG Grid source starts `ui`.
_BY_A_PERSON = ("e => { if (String(e.source || '').startsWith('ui'))"
                " emit({colId: e.colId, pinned: e.pinned}); }")
# How the ids of the columns AG Grid adds for itself begin: the checkbox column, chiefly.
_GENERATED = "ag-Grid-"
_PIN_SIDES = ("left", "right", None)
_BODY_AG_GRID_KEEPS_UNPINNED_PX = 50
# A row whose data says this is true has nothing in its context menu, so right-clicking it
# opens none.
NO_MENU = "menuless"
# A row whose data says this is true cannot be ticked, where the grid is given
# `rows_not_selectable` (an empty list will do).
NOT_SELECTABLE = "unselectable"
# A row whose data says this is true takes no dropped file: it carries the class the page
# script reads to show the no-drop cursor.
NO_DROP = "dropless"
NO_DROP_CLASS = "manager-drop-no"


@dataclass
class Layout:
    """A view's layout as a person left it: the order once they move a column, and the
    width or pin of each column they resized or pinned. A column it does not name takes
    what its definition declares."""

    where: str
    order: list[str] = field(default_factory=list)
    widths: dict[str, float] = field(default_factory=dict)
    pins: dict[str, str | None] = field(default_factory=dict)
    # The widths the grid was last seen at. A resize is found by what changed rather
    # than by the event's column: a shift-drag resizes two columns and names neither.
    seen: dict[str, float] = field(default_factory=dict)
    written: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def read(cls, where: str, held: Any) -> Layout:
        held = held if isinstance(held, dict) else {}
        widths, pins = held.get("widths"), held.get("pins")
        layout = cls(
            where,
            order=[col_id for col_id in held.get("order") or [] if isinstance(col_id, str)],
            widths={str(col_id): width for col_id, width in
                    (widths.items() if isinstance(widths, dict) else ())
                    if isinstance(width, (int, float)) and not isinstance(width, bool)},
            pins={str(col_id): side for col_id, side in
                  (pins.items() if isinstance(pins, dict) else ())
                  if side in _PIN_SIDES})
        layout.written = layout.stored()
        return layout

    def stored(self) -> dict[str, Any]:
        """What goes in the preferences: each part only once a person has set it."""
        return {key: value for key, value in (("order", list(self.order)),
                                              ("widths", dict(self.widths)),
                                              ("pins", dict(self.pins))) if value}


_LAYOUTS: weakref.WeakKeyDictionary[Any, Layout] = weakref.WeakKeyDictionary()
_SAVERS: weakref.WeakKeyDictionary[Any, Callable[[dict[str, Any]], Awaitable[None]]] = \
    weakref.WeakKeyDictionary()


# A header's chrome with the column sorted: padding, sort arrow, filter button and the
# theme's gap before them. 9px/char is the widest average in the header font, so a
# header never has to wrap.
_HEADER_CHROME_PX = 80
_HEADER_CHAR_PX = 9
# The number AG Grid draws beside the arrow once more than one column is sorted.
_SORT_ORDER_PX = 25


def header_width(header: str) -> int:
    """The narrowest this column can be and still show its header in full.

    Measured per line: a header broken over two lines needs the width of its longest
    line, not of the whole string.
    """
    longest = max((len(line) for line in header.split("\n")), default=0)
    # No header, no floor: a column of pictures carries no text, no sort arrow and no
    # filter button, so charging it for their chrome makes it wider than it needs.
    return longest * _HEADER_CHAR_PX + _HEADER_CHROME_PX if longest else 0


def room_for_sort_order(columns: list[dict[str, Any]],
                        sorts: Iterable[Sequence[dict[str, Any]]]) -> None:
    """Widen, in place, each column a sort of two or more keys names, so its header
    reads whole beside the order number too."""
    named = {entry.get("colId") for sort in sorts if len(sort) > 1 for entry in sort}
    for definition in columns:
        floor = header_width(str(definition.get("headerName") or ""))
        if floor and definition.get("field") in named:
            definition["width"] = max(definition.get("width") or 0, floor + _SORT_ORDER_PX)


# A picker in the funnel, where AG Grid's text box would be: on a column of marks that
# box asks the reader to know the word behind a circle, and on a boolean it offers
# "Choose one / True / False". The set filter that would fix it is Enterprise; a custom
# filter is not, and community takes any class implementing the interface.
#
# `choices` is [{value, label, mark, repeat}], the value being what the cell holds - so
# a blank cell is a choice like any other rather than the one state a filter cannot
# express.
CHOICE_FILTER = "window.HubChoiceFilter"
_FILTER_THIS_LIST = t("manager.grid.filter_hint")

_CHOICE_FILTER_JS = """
if (!window.HubChoiceFilter) {
  window.HubChoiceFilter = class {
    init(params) {
      this.params = params;
      this.picked = new Set();
      this.gui = document.createElement('div');
      this.gui.className = 'manager-filter';
      this.draw(params.choices || []);
    }
    draw(choices) {
      this.gui.replaceChildren();
      this.boxes = new Map();
      this.rows = new Map();
      this.counts = new Map();
      this.labels = new Map();
      // A list past this is read by typing, not by scrolling. Below it a box to type in
      // is one more thing to look at for no gain.
      if (choices.length > 8) {
        const search = document.createElement('input');
        search.type = 'text';
        search.className = 'manager-filter-search';
        search.placeholder = (this.params.words || {}).search || '';
        search.addEventListener('input', () => {
          const said = search.value.trim().toLowerCase();
          for (const [value, row] of this.rows) {
            const label = String(this.labels.get(value) || '').toLowerCase();
            // A picked choice stays visible whatever is typed: hiding it would leave a
            // filter in force with nothing on screen saying so.
            row.hidden = said && !label.includes(said) && !this.picked.has(value);
          }
        });
        this.gui.appendChild(search);
      }
      for (const choice of choices) {
        const row = document.createElement('label');
        row.className = 'manager-filter-row';
        if (choice.tip) row.title = choice.tip;
        const box = document.createElement('input');
        box.type = 'checkbox';
        box.checked = this.picked.has(choice.value);
        box.addEventListener('change', () => {
          box.checked ? this.picked.add(choice.value) : this.picked.delete(choice.value);
          this.params.filterChangedCallback();
        });
        row.appendChild(box);
        // One leading slot, always, so an item that draws nothing indents to where the
        // marks are and every label starts on the same edge. `docs/conventions.md`.
        const slot = document.createElement('span');
        slot.className = 'manager-filter-mark';
        // `repeat` draws the mark more than once, which is how a rating says three
        // rather than saying "3" beside a picture of one star.
        for (let n = 0; n < (choice.mark ? (choice.repeat || 1) : 0); n++) {
          const mark = document.createElement('span');
          mark.className = choice.mark;
          slot.appendChild(mark);
        }
        // A state drawn as a character rather than a shape - presence is a tick here.
        if (choice.glyph) {
          const tick = document.createElement('span');
          tick.className = choice.glyphClass || 'manager-tick';
          tick.textContent = choice.glyph;
          slot.appendChild(tick);
        }
        row.appendChild(slot);
        const word = document.createElement('span');
        word.textContent = choice.label;
        if (choice.tone) word.className = 'manager-state manager-state--' + choice.tone;
        row.appendChild(word);
        const tally = document.createElement('span');
        tally.className = 'manager-filter-count';
        row.appendChild(tally);
        this.counts.set(choice.value, tally);
        this.labels.set(choice.value, choice.label);
        this.boxes.set(choice.value, box);
        this.rows.set(choice.value, row);
        this.gui.appendChild(row);
      }
    }
    // What a row is counted under and matched on. A cell with nothing in it is the
    // "missing" choice, whose value is "".
    buckets(node) {
      const held = this.params.getValue(node);
      return [held === null || held === undefined ? '' : held];
    }
    // How big the bucket is, over the whole library rather than over what the other
    // filters have left: a number that moved every time something else was picked would
    // be a different fact under the same label.
    tally() {
      if (!this.params.api || !this.counts.size) return;
      const seen = new Map();
      this.params.api.forEachNode(node => {
        for (const key of new Set(this.buckets(node))) seen.set(key, (seen.get(key) || 0) + 1);
      });
      for (const [value, el] of this.counts) {
        const n = seen.get(value) || 0;
        el.textContent = n ? String(n) : '';
      }
    }
    afterGuiAttached() { this.tally(); }
    getGui() { return this.gui; }
    isFilterActive() { return this.picked.size > 0; }
    doesFilterPass(params) {
      return this.buckets(params.node).some(key => this.picked.has(key));
    }
    getModel() { return this.picked.size ? {values: [...this.picked]} : null; }
    setModel(model) {
      this.picked = new Set((model && model.values) || []);
      for (const [value, box] of this.boxes) box.checked = this.picked.has(value);
    }
  };
}
if (!window.HubWindowFilter) {
  // One of a few windows back from today, or none. The model is the window in days, so a
  // saved view reads it against the day it is applied on.
  window.HubWindowFilter = class {
    init(params) {
      this.params = params;
      this.days = 0;
      this.cutoff = '';
      this.gui = document.createElement('div');
      this.gui.className = 'manager-filter';
      window.HubWindowFilter.made = (window.HubWindowFilter.made || 0) + 1;
      this.group = 'manager-filter-window-' + window.HubWindowFilter.made;
      const words = params.words || {};
      const windows = (params.windows || []).map(one => [one.value, one.label]);
      this.radios = [[0, words.any || ''], ...windows]
        .map(([days, said]) => {
          const row = document.createElement('label');
          row.className = 'manager-filter-row';
          const radio = document.createElement('input');
          radio.type = 'radio';
          radio.name = this.group;
          radio.checked = days === 0;
          radio.addEventListener('change', () => {
            this.take(days);
            this.params.filterChangedCallback();
          });
          const word = document.createElement('span');
          word.textContent = said;
          row.append(radio, word);
          this.gui.appendChild(row);
          return [days, radio];
        });
    }
    take(days) {
      this.days = days;
      const day = new Date();
      day.setDate(day.getDate() - days);
      const pad = n => String(n).padStart(2, '0');
      this.cutoff = days
        ? day.getFullYear() + '-' + pad(day.getMonth() + 1) + '-' + pad(day.getDate()) : '';
    }
    getGui() { return this.gui; }
    isFilterActive() { return this.days > 0; }
    doesFilterPass(params) {
      return String(this.params.getValue(params.node) || '').slice(0, 10) >= this.cutoff;
    }
    getModel() { return this.days ? {days: this.days} : null; }
    setModel(model) {
      this.take(Number((model && model.days) || 0));
      for (const [days, radio] of this.radios) radio.checked = this.days === days;
    }
  };
}
if (!window.HubListFilter) {
  // For a cell holding a list; "" is the row that holds nothing.
  window.HubListFilter = class extends window.HubChoiceFilter {
    init(params) {
      this.all = false;
      window.HubListFilter.made = (window.HubListFilter.made || 0) + 1;
      this.group = 'manager-filter-mode-' + window.HubListFilter.made;
      super.init(params);
    }
    buckets(node) {
      const held = this.params.getValue(node);
      return Array.isArray(held) && held.length ? held : [''];
    }
    // Read again at every opening, so a value given a minute ago is a choice. A picked
    // value nothing holds any more stays one, or the filter would be in force unseen.
    present() {
      const looks = (window.__vpinfeChipLooks || {})[this.params.looks] || (() => ({}));
      const seen = new Set([...this.picked].filter(value => value !== ''));
      this.params.api.forEachNode(node => {
        for (const value of this.buckets(node)) if (value !== '') seen.add(value);
      });
      return [...[...seen].map(value => [value, looks(value) || {}])
        .sort(([a, x], [b, y]) => (__ORDER__)(x.label || a, y.label || b))
        .map(([value, look]) => ({
          value: value, label: look.label || value, mark: look.dot || '',
          glyph: look.mark || '', glyphClass: __MARK_CLASS__, tip: look.tip || ''})),
        {value: '', label: (this.params.words || {}).none || ''}];
    }
    draw(choices) {
      super.draw(choices);
      const words = this.params.words || {};
      const mode = document.createElement('div');
      mode.className = 'manager-filter-mode';
      this.modes = [[false, words.any], [true, words.all]].map(([all, said]) => {
        const label = document.createElement('label');
        const radio = document.createElement('input');
        radio.type = 'radio';
        radio.name = this.group;
        radio.checked = this.all === all;
        radio.addEventListener('change', () => {
          this.all = all;
          if (this.picked.size) this.params.filterChangedCallback();
        });
        label.append(radio, document.createTextNode(said || ''));
        mode.appendChild(label);
        return [all, radio];
      });
      this.gui.prepend(mode);
    }
    afterGuiAttached() {
      this.draw(this.present());
      this.tally();
    }
    doesFilterPass(params) {
      const held = this.buckets(params.node);
      const wanted = [...this.picked];
      return this.all ? wanted.every(value => held.includes(value))
                      : wanted.some(value => held.includes(value));
    }
    getModel() {
      if (!this.picked.size) return null;
      const model = {values: [...this.picked].sort(__ORDER__)};
      if (this.all) model.all = true;
      return model;
    }
    setModel(model) {
      super.setModel(model);
      this.all = Boolean(model && model.all);
      for (const [all, radio] of this.modes || []) radio.checked = this.all === all;
    }
  };
}
""".replace("__ORDER__", renderers.ORDER).replace("__MARK_CLASS__",
                                                   json.dumps(renderers.MARK_CLASS))


def install_filters() -> None:
    """Put the filter components on the page. Once per page, before any grid is built."""
    ui.add_body_html(f"<script>{_CHOICE_FILTER_JS}</script>")


def choice_filter(choices: list[dict[str, Any]], *,
                  formatted: bool = False) -> dict[str, Any]:
    """Column options that filter by picking a state rather than by typing one.

    The cell holds the value, not the label - a filter matches on what the row says and
    a saved view records that, so the stored token must not move when the language
    does. `valueFormatter` is what puts the label on screen.

    `formatted=True` where the column writes its own: a tick column formats a boolean
    and would collide with one written here.
    """
    # Only a string value is a token with a word for it. A boolean or a number is the
    # cell's own content and the column formats it - keying this on `str(True)` built a
    # map a JavaScript `true` never matched, and it landed *after* the column's own
    # formatter in the dict, so every tick column printed `true`/`false`.
    shown = {} if formatted else {
        one["value"]: str(one.get("label"))
        for one in choices
        if isinstance(one.get("value"), str) and one["value"]
        and str(one.get("label") or "") != one["value"]}
    out: dict[str, Any] = {":filter": CHOICE_FILTER,
                           "filterParams": {"choices": choices,
                                            "words": {"search": _FILTER_THIS_LIST}}}
    if shown:
        out[":valueFormatter"] = (
            f"params => ({json.dumps(shown)})[params.value] ?? params.value")
    return out


WINDOW_FILTER = "window.HubWindowFilter"


def window_filter(windows: list[tuple[str, int]]) -> dict[str, Any]:
    """Column options that filter a date column to the last few days, one window at a time.

    `windows` is (label, days). The model is `{"days": n}`, read against today whenever it
    is applied, so a saved view keeps its window moving forward."""
    return {":filter": WINDOW_FILTER,
            "filterParams": {"windows": [{"value": days, "label": label}
                                         for label, days in windows],
                             "words": {"any": t("manager.grid.any_time")}}}


DERIVED_FACETS = {"manufacturer": "manager.games.not_recorded",
                  "game_type": "manager.games.not_recorded",
                  "match": "manager.media.match.not_set"}
FACET_CEILING = 300


def with_derived_facets(columns: list[dict[str, Any]], rows: list[dict[str, Any]],
                        ceiling: int = FACET_CEILING) -> list[dict[str, Any]]:
    """Give the columns whose values come from VPS a checkbox filter of what `rows` hold.

    A column past `ceiling` distinct values keeps its text filter. Returns a new list and
    new dicts for the columns it changes: `COLUMNS` is a module constant and a filter
    written into it outlives the render.
    """
    built = []
    for definition in columns:
        field = str(definition.get("field") or "")
        if field not in DERIVED_FACETS:
            built.append(definition)
            continue
        said = [str(row.get(field) or "").strip() for row in rows]
        seen = sorted({one for one in said if one}, key=str.casefold)
        if not seen or len(seen) > ceiling:
            built.append(definition)
            continue
        choices = [{"value": value, "label": value} for value in seen]
        if "" in said:
            choices.append({"value": "", "label": t(DERIVED_FACETS[field])})
        built.append(definition | choice_filter(choices))
    return built


LIST_FILTER = "window.HubListFilter"

def list_comparator(looks: str = "") -> str:
    """Sorts a list column by what its chips say, first chip first. The grid turns a
    descending result round itself, so an empty row answers the other way round there
    to stay last."""
    return (
        "(a, b, nodeA, nodeB, descending) => {"
        f" const order = {renderers.ORDER}; const said = {renderers.named(looks)};"
        " const drawn = v => (Array.isArray(v) ? v : []).map(said).sort(order);"
        " const x = drawn(a), y = drawn(b);"
        " if (!x.length || !y.length) {"
        "  if (x.length === y.length) return 0;"
        "  const last = x.length ? -1 : 1;"
        "  return descending ? -last : last; }"
        " for (let i = 0; i < Math.min(x.length, y.length); i++) {"
        "  const first = order(x[i], y[i]); if (first) return first; }"
        " return x.length - y.length; }"
    )

_LIST_WORDS = {"none": t("word.none"), "any": t("manager.grid.any_of"),
               "all": t("manager.grid.all_of"), "search": _FILTER_THIS_LIST}


def list_column(field: str, header: str, width: int = 0, help: str = "", *,
                looks: str = "", plain: bool = False, **extra: Any) -> dict[str, Any]:
    """A column whose row holds a list under `field`: drawn as chips, filtered by the
    values the rows hold, sorted by the first chip as drawn.

    `looks` names where a chip takes its looks from, one of `renderers.LOOKS`; empty
    draws the word alone. A look's `label` is what the chip, the filter, the sort and a
    search read in place of the value, which is what the filter keeps. `plain` draws the
    values as one line of text instead of chips.
    """
    said = renderers.named(looks)
    return column(field, header, width, help,
                  **({} if plain else renderers.drawable("chips", looks=looks)),
                  **{":filter": LIST_FILTER,
                     "filterParams": {"looks": looks, "words": _LIST_WORDS},
                     ":comparator": list_comparator(looks),
                     ":getQuickFilterText":
                         f"params => (params.value || []).map({said}).join(' ')",
                     ":valueFormatter":
                         f"params => (params.value || []).map({said}).join(', ')"},
                  **extra)


def on_row_focus(scope: str, handler: Callable[[Any], Any]) -> None:
    """Call `handler` for focus events from this grid, and no other."""
    _row_focus_handlers()[scope] = handler


def row_focused(event: Any) -> Any:
    """A focus event, handed to the grid it came from."""
    args = event.args if isinstance(event.args, dict) else {}
    mine = _row_focus_handlers().get(str(args.get("scope") or ""))
    return mine(event) if mine is not None else None


# Weak, so a client that has gone takes its handlers with it.
_ROW_FOCUS: weakref.WeakKeyDictionary[Any, dict[str, Any]] = weakref.WeakKeyDictionary()


def _row_focus_handlers() -> dict[str, Any]:
    return _ROW_FOCUS.setdefault(ui.context.client, {})


_LANDING: weakref.WeakKeyDictionary[Any, dict[str, list[dict[str, str]]]] = \
    weakref.WeakKeyDictionary()


def land_on(scope: str, *wanted: dict[str, str]) -> None:
    """Have the next grid built under `scope` on this page open on a row: the first one
    whose fields equal every value of a `wanted`, trying each in turn."""
    _LANDING.setdefault(ui.context.client, {})[scope] = list(wanted)


def focused_row(event: Any) -> str:
    """The row id out of a focus event, and `focused_column` the column it landed in.

    The column travels with it because a cell is a more specific place than a row: the
    Games grid opens the workbench on the part of the game the column is about. Older
    payloads were the bare id, so both read either shape.
    """
    args = event.args
    return str((args.get("id") if isinstance(args, dict) else args) or "")


def focused_column(event: Any) -> str:
    args = event.args
    return str((args.get("col") if isinstance(args, dict) else "") or "")


def ticked(event: Any) -> bool:
    """Whether a focus event is the checkbox being pressed, which is a choice of rows and
    not of a row to show."""
    return focused_column(event) == "ag-Grid-SelectionColumn"


_FOCUS_ROW = """new Promise((done) => {
  const api = getElement(%d).api;
  let tries = 0;
  const look = () => {
    const node = api && api.getRowNode(%s);
    if (node && node.rowIndex !== null) {
      api.ensureNodeVisible(node);
      api.setFocusedCell(node.rowIndex, %s);
      return done(true);
    }
    if (node || ++tries >= 40) return done(false);
    setTimeout(look, 25);
  };
  look();
})"""


_LAND = """(() => {
  const kick = (tries) => {
    const found = getElement(%d);
    const api = found && found.api;
    if (!api) {
      if (tries > 0) setTimeout(() => kick(tries - 1), 50);
      return;
    }
    requestAnimationFrame(() => window.__hubFollowFocus(api, api.getGridOption('context')));
  };
  kick(40);
})()"""


@on_page
async def focus_row(table: Any, row_id: str, column: str) -> bool:
    """Scroll to a row and focus it, which fires the grid's row focus as a click would.

    Waits a second for a row a transaction has just added. False where the row never
    arrives or a filter hides it, and the caller opens it some other way.
    """
    # An unawaited `run_grid_method` goes out from a task queued behind this one. Yield
    # first, or the focus lands on a row index a pending transaction is about to shift.
    await asyncio.sleep(0)
    try:
        return bool(await ui.run_javascript(
            _FOCUS_ROW % (table.id, json.dumps(row_id), json.dumps(column)), timeout=2.0))
    except Exception:  # noqa: BLE001 - False is the answer the caller acts on
        return False


def transact(table: Any, held: list[dict[str, Any]], transaction: dict[str, Any],
             by_id: dict[str, Any] | None = None) -> None:
    """Apply `transaction` to the grid and to `held`, the list the grid was built from,
    the way AG Grid applies it: remove, update in place, then add at `addIndex` or last.

    A selection is read against `held`, so a row changed on screen and not there is a
    row a bulk action acts on as it was. `by_id`, given, follows as well.
    """
    gone = {entry["id"] for entry in transaction.get("remove", ())}
    fresh = {row["id"]: row for row in transaction.get("update", ())}
    added = list(transaction.get("add", ()))
    kept = [fresh.get(row["id"], row) for row in held if row["id"] not in gone]
    at = transaction.get("addIndex")
    at = len(kept) if at is None else max(0, min(int(at), len(kept)))
    held[:] = kept[:at] + added + kept[at:]
    if by_id is not None:
        for row_id in gone:
            by_id.pop(row_id, None)
        by_id.update({row["id"]: row for row in [*fresh.values(), *added]})
    table.run_grid_method("applyTransaction", transaction)
    if fresh:
        redraw(table, list(fresh))


def redraw(table: Any, ids: list[str] | None = None) -> None:
    """Draw these rows' cells again, or every row's where `ids` is None.

    The grid redraws a cell only when its own field changed. A renderer that draws from
    another field of the row, such as the subtitle under a name, is not told, so a write
    that changes only that field needs this after it.
    """
    options: dict[str, Any] = {"force": True}
    if ids is not None:
        options[":rowNodes"] = (f"{json.dumps(ids)}.map(id => getElement({table.id})"
                                "?.api?.getRowNode(id)).filter(Boolean)")
    table.run_grid_method("refreshCells", options)


def replace_rows(table: Any, held: list[dict[str, Any]], by_id: dict[str, Any],
                 fresh: list[dict[str, Any]], belongs: Callable[[dict], bool]) -> None:
    """Swap the rows `belongs` picks for `fresh`, so what stayed keeps its focus and
    selection while what was added or went away does.

    What stayed keeps its place unless `fresh` brings it back in another order: then the
    slots those rows held are filled in `fresh`'s order.
    """
    old_ids = [row["id"] for row in held if belongs(row)]
    was, fresh_ids = set(old_ids), {row["id"] for row in fresh}
    kept = [row for row in fresh if row["id"] in was]
    added = [row for row in fresh if row["id"] not in was]
    if [row["id"] for row in kept] == [row_id for row_id in old_ids if row_id in fresh_ids]:
        transact(table, held, {
            "remove": [{"id": row_id} for row_id in old_ids if row_id not in fresh_ids],
            "update": kept, "add": added}, by_id)
        return
    slots = iter(kept)
    held[:] = [next(slots) if row["id"] in was else row for row in held
               if row["id"] in fresh_ids or row["id"] not in was] + added
    for row_id in was - fresh_ids:
        by_id.pop(row_id, None)
    by_id.update({row["id"]: row for row in fresh})
    table.run_grid_method("setGridOption", "rowData", list(held))
    redraw(table)


def two_line(header: str) -> str:
    """Break the last word onto its own line, so a long header stays a narrow column."""
    words = header.split()
    return header if len(words) < 2 else " ".join(words[:-1]) + "\n" + words[-1]


def column(field: str, header: str, width: int = 0, help: str = "",
           **extra: Any) -> dict[str, Any]:
    """A column sized to fit, which is its header unless the content needs more.

    **Omit `width`.** Pass one only where the values are longer than the header - a
    title, an author, a filename - and it is a floor, not a target. Hand-picking every
    width makes a column of one-digit counts as wide as somebody guessed rather than as
    wide as it needs to be. Dragging narrower still works, and what the user drags to is
    what persists.

    `flex=1` makes it the column that takes whatever width the others leave, so a grid in
    a card of its own has no gap to the right; `width` is then its floor, and it is saved
    only once a person drags it.

    Every multi-word header wraps, here rather than at each call site: applied per call
    site it reaches only the generated columns, and `Table Count` sits on one line beside
    media headers that do not.
    """
    header = two_line(header)
    # `help` says what the column is and what its values mean, in words the header does
    # not already carry, and is not optional: `build` refuses a column that has none, so
    # a column the user can hover always explains itself.
    tip = {"headerTooltip": help} if help else {}
    shown = extra.get(":valueFormatter")
    search = {":getQuickFilterText": shown} if shown else {}
    size = max(width, header_width(header))
    floor = {"minWidth": size} if extra.get("flex") else {}
    return {"field": field, "headerName": header, "width": size} | floor | tip | search | extra


# A number column's comparator: a blank is last whichever way it sorts. The grid turns a
# descending result round itself, so a blank answers the other way round there.
BLANK_LAST = (
    "(a, b, nodeA, nodeB, descending) => {"
    " const blank = v => v == null || v === '';"
    " if (blank(a) || blank(b)) {"
    "  if (blank(a) && blank(b)) return 0;"
    "  const last = blank(a) ? 1 : -1; return descending ? -last : last; }"
    " return a - b; }")

NUMBER_COLUMN: dict[str, Any] = {"type": "numericColumn", "filter": "agNumberColumnFilter",
                                 ":comparator": BLANK_LAST}


def year_column(field: str, header: str, width: int = 0, help: str = "",
                **extra: Any) -> dict[str, Any]:
    """A year column, whose rows hold `game_tables.year_of(...)` and never a string."""
    return column(field, header, width, help, **NUMBER_COLUMN, **extra)


# `manager-base.css` colors this class.
IDENTIFIER_CLASS = "manager-cell-identifier"


SUBTITLE_CLASS = "manager-cell-said"
TWO_LINE_CLASS = "manager-cell-two-line"
FILE_CLASS = "manager-file-name"
FILE_LINE_CLASS = "manager-cell-said-file"
PICTURED_CLASS = "manager-cell-art-lead"
THUMBNAIL_CLASS = "manager-cell-art-list"
ONE_LINE_ROW_PX = 42
TWO_LINE_ROW_PX = 56
PICTURED_ROW_PX = 88
_ROW_CLASS = {TWO_LINE_ROW_PX: "manager-grid-two-line", 64: "manager-grid-medium-art",
              PICTURED_ROW_PX: "manager-grid-pictured"}

_SUBTITLE_RENDERER = (
    "params => {"
    " const d = params.data || {};"
    " const esc = s => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;')"
    ".replace(/\"/g, '&quot;');"
    " const made = d['{made}'] || '';"
    " const built = {built};"
    " const file = {file};"
    " const name = params.valueFormatted != null ? params.valueFormatted"
    " : (params.value == null ? '' : params.value);"
    " let said = '';"
    " if (made) said += '<span class=\"manager-cell-made\">' + esc(made) + '</span>';"
    " if (made && built) said += '<span class=\"manager-cell-join\"> \u00b7 </span>';"
    " const tip = {tip};"
    " const hint = tip ? ' title=\"' + esc(tip) + '\"' : '';"
    " if (built) said += file ? '<span class=\"manager-cell-built " + FILE_CLASS + "\"' + hint"
    " + '><span>' + esc(built) + '</span></span>'"
    " : '<span class=\"manager-cell-built\"' + hint + '>' + esc(built) + '</span>';"
    " const href = d['{link}_href'] || '';"
    " const away = /^https?:/.test(href) ? ' target=\"_blank\" rel=\"noopener\"' : '';"
    " const named = href ? '<a class=\"manager-link\" href=\"' + esc(href) + '\"' + away"
    " + ' title=\"' + esc(d['{link}_tip'] || '') + '\">' + esc(name) + '</a>' : esc(name);"
    " const lines = '<span class=\"manager-cell-named\">' + named"
    " + '</span><span class=\"{cls}' + (file ? ' " + FILE_LINE_CLASS + "' : '')"
    " + '\">' + said + '</span>';"
    " const art = {picture};"
    " if (art == null) return lines;"
    " return {frame}; }"
)


def identifier(field: str, header: str, width: int = 0, help: str = "",
               subtitle: str | tuple[str, str, str] = "", link: str = "",
               picture: str = "", glyph: str = "", frame: str = thumbnails.LIST,
               built_tip: str = "", **extra: Any) -> dict[str, Any]:
    """The column this grid's rows are scanned *by*, which is not their unique key.

    Exactly one per grid; `build` refuses anything else.

    `subtitle` names the field holding the line drawn under the value, or a
    `(made, "", built)` triple where the line has two parts to tell apart. Sorting and
    filtering stay on `field`, so the line is shown and never scanned.

    `link` makes the value an anchor on rows carrying `<link>_href`, titled `<link>_tip`.
    A row carrying a true `<built>_file` has a filename for its second part, and keeps
    the end of it when the line runs out.
    `picture` names the field holding an image address drawn in a `frame` ahead of both
    lines, with `glyph` in it where the address is "". A row without the field draws no
    frame. All of them need a subtitle to be drawn.
    """
    extra_classes = extra.pop("cellClass", "")
    classes = f"{extra_classes} {IDENTIFIER_CLASS}".strip() if extra_classes \
        else IDENTIFIER_CLASS
    if subtitle:
        classes = f"{classes} {TWO_LINE_CLASS}" + (
            f" {PICTURED_CLASS}" if picture and frame == thumbnails.PREVIEW else "")
        extra.setdefault(":cellRenderer", _two_lines(subtitle, link, picture, glyph, frame,
                                                      built_tip))
    return column(field, header, width, help, cellClass=classes, **extra)


def _key(name: str) -> str:
    """`name` as the inside of a single-quoted JavaScript string, since an extension names
    the fields a column reads."""
    return json.dumps(name)[1:-1].replace("'", "\\'")


def _two_lines(subtitle: str | tuple[str, str, str], link: str, picture: str, glyph: str,
               frame: str, built_tip: str) -> str:
    made, _, built = subtitle if isinstance(subtitle, tuple) else (subtitle, "", "")
    return (_SUBTITLE_RENDERER
            .replace("{picture}", f"(d['{_key(picture)}'] ?? null)" if picture else "null")
            .replace("{link}", _key(link) or "_")
            .replace("{tip}", json.dumps(built_tip))
            .replace("{made}", _key(made))
            .replace("{built}", f"d['{_key(built)}'] || ''" if built else "''")
            .replace("{file}", f"!!d['{_key(built)}_file']" if built else "false")
            .replace("{cls}", SUBTITLE_CLASS)
            .replace("{frame}", thumbnails.frame_js(glyph, frame) if picture else "lines"))


def named(field: str, header: str, width: int = 0, help: str = "", subtitle: str = "",
          link: str = "", **extra: Any) -> dict[str, Any]:
    """A column that draws a name with its line under it and no art, as the identifier
    does, for a game or table named in a column that is not the first.

    `subtitle` names the field holding the line; `link` as `identifier` takes it."""
    classes = " ".join(part for part in (extra.pop("cellClass", ""), TWO_LINE_CLASS) if part)
    extra.setdefault(":cellRenderer", _two_lines(subtitle, link, "", "", thumbnails.LIST, ""))
    return column(field, header, width, help, cellClass=classes, **extra)


# Ours, not AG Grid's: it names the group a column sits under in the column picker.
# Carried on the definition so the list that declares the columns also declares their
# order and their grouping, and stripped before the defs reach the grid.
GROUP_KEY = "group"
# Ours too: the `thumbnails.Look` an identifier's art is drawn in.
ART_KEY = "thumbnail"


def for_grid(columns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The definitions as AG Grid wants them, without our own keys."""
    return [{k: v for k, v in column.items()
             if k not in (GROUP_KEY, ART_KEY, renderers.CHOICES_KEY)}
            for column in columns]


# Clicking a cell takes focus and nothing else. Click-selection in multiRow mode
# *replaces* the set, so a cell click would clear every checkbox a bulk action is about
# to read.
ROW_SELECTION = {"mode": "multiRow", "checkboxes": True, "headerCheckbox": True,
                 "enableClickSelection": False, "selectAll": "filtered"}

# A socket message over a megabyte is dropped without a word, so a selection is sent as
# its row ids, in parts. Characters, not bytes: a part stays under the cap even at
# three bytes a character.
SELECTION_PART_CHARS = 100_000
_COUNT_HIDDEN = """
  const picked = grid.api.getSelectedNodes();
  const shown = new Set();
  if (picked.length) grid.api.forEachNodeAfterFilter(node => shown.add(node.id));
  const hidden = grid.__hubHidden = picked.filter(node => !shown.has(node.id)).length;
"""
_SEND_SELECTION = """() => {
  const grid = getElement(%(id)d);
  if (!grid || !grid.api) return;
  %(count)s
  const turn = window.__hubSelectionTurn = (window.__hubSelectionTurn || 0) + 1;
  const parts = [[]];
  let size = 0;
  for (const node of picked) {
    if (size + node.id.length > %(chars)d && parts[parts.length - 1].length) {
      parts.push([]);
      size = 0;
    }
    parts[parts.length - 1].push(node.id);
    size += node.id.length + 3;
  }
  parts.forEach((ids, at) => emit({turn, at, of: parts.length, ids, hidden}));
}"""
_SEND_HIDDEN = """() => {
  const grid = getElement(%(id)d);
  if (!grid || !grid.api) return;
  const was = grid.__hubHidden || 0;
  %(count)s
  if (hidden !== was) emit({hidden});
}"""


class Parts:
    """A list the browser sends in parts, `{turn, at, of, <key>: [...]}`, gathered."""

    def __init__(self, key: str = "ids") -> None:
        self.key = key
        self._turn = 0
        self._parts: dict[int, list[Any]] = {}

    def take(self, part: Any) -> list[Any] | None:
        """Keep one part. The whole list once every part of its turn is here, else None;
        a part of a turn older than the newest seen is dropped."""
        if not isinstance(part, dict):
            return None
        turn, at, of, items = (part.get(key) for key in ("turn", "at", "of", self.key))
        if not (isinstance(turn, int) and isinstance(at, int) and isinstance(of, int)
                and isinstance(items, list)) or turn < self._turn:
            return None
        if turn > self._turn:
            self._turn, self._parts = turn, {}
        self._parts[at] = items
        if len(self._parts) < of:
            return None
        whole = [item for index in range(of) for item in self._parts.get(index, ())]
        self._parts = {}
        return whole


class Selection:
    """The ids a grid has selected, gathered from the parts they arrive in, and read
    against `held`, the rows the grid was built from."""

    def __init__(self, held: list[dict[str, Any]]) -> None:
        self.held = held
        self.ids: list[str] = []
        self.hidden = 0
        self._parts = Parts()

    def take(self, part: Any) -> bool:
        """Keep one part. True once every part of its turn is here."""
        ids = self._parts.take(part)
        if ids is None:
            return False
        self.ids = [str(row_id) for row_id in ids]
        self.hide(part)
        return True

    def hide(self, said: Any) -> bool:
        """Keep how many of the selection the rows on screen leave out. True if that
        changed."""
        hidden = said.get("hidden") if isinstance(said, dict) else None
        if not isinstance(hidden, int) or hidden < 0 or hidden == self.hidden:
            return False
        self.hidden = hidden
        return True

    def rows(self) -> list[dict[str, Any]]:
        """The selected rows as held now, in the order they were picked."""
        by_id = {str(row["id"]): row for row in self.held}
        return [by_id[row_id] for row_id in self.ids if row_id in by_id]


_SELECTIONS: weakref.WeakKeyDictionary[Any, Selection] = weakref.WeakKeyDictionary()


def selection(table: Any) -> list[dict[str, Any]]:
    """What a bulk action on `table` acts on: its selected rows as the grid holds them."""
    chosen = _SELECTIONS.get(table)
    return chosen.rows() if chosen is not None else []


def keep_only(table: Any, ids: Iterable[str]) -> None:
    """Leave only these rows ticked."""
    wanted = json.dumps([str(one) for one in ids])
    ui.run_javascript(
        f"(() => {{ const grid = getElement({table.id}); if (!grid || !grid.api) return;"
        f" const keep = new Set({wanted});"
        " grid.api.forEachNode(node => node.setSelected(keep.has(node.id))); })()")


def menu_rows(table: Any, row: dict[str, Any]) -> list[dict[str, Any]]:
    """The rows a right-click on `row` acts on. Inside a selection of several that is the
    selection; on a row outside it, that row alone and the ticks stay as they are."""
    chosen = selection(table)
    ids = {str(one.get("id")) for one in chosen}
    return chosen if len(chosen) > 1 and str(row.get("id")) in ids else [row]


def hidden_count(table: Any) -> int:
    """How many of `table`'s selected rows a search, a filter or a view leaves out."""
    chosen = _SELECTIONS.get(table)
    return chosen.hidden if chosen is not None else 0


def selection_said(table: Any, picked: int, plain: str) -> str:
    """The bar's count of a selection: `plain` while every selected row is on screen."""
    hidden = hidden_count(table)
    return t("manager.grid.selected_hidden", count=picked, hidden=hidden) if hidden else plain


def _art_look(columns: list[dict[str, Any]]) -> thumbnails.Look | None:
    return next((definition[ART_KEY] for definition in columns if ART_KEY in definition),
                None)


def base_row_px(columns: list[dict[str, Any]]) -> int:
    """The row height the grid's own cells need, before any drawing asks for more."""
    classes = " ".join(str(definition.get("cellClass") or "") for definition in columns)
    if PICTURED_CLASS in classes:
        return PICTURED_ROW_PX
    own = TWO_LINE_ROW_PX if TWO_LINE_CLASS in classes else ONE_LINE_ROW_PX
    look = _art_look(columns)
    return max(own, look.row_px) if look is not None else own


def with_art(columns: list[dict[str, Any]],
             look: thumbnails.Look | None) -> list[dict[str, Any]]:
    """`columns`, with the identifier declared wide enough for the art beside the name,
    and its rows tall enough for the frame, while the list draws it in `look`."""
    if look is None:
        return columns
    return [definition | {"width": definition["width"] + look.room_px,
                          "cellClass": f"{definition['cellClass']} {THUMBNAIL_CLASS}",
                          ART_KEY: look}
            if IDENTIFIER_CLASS in str(definition.get("cellClass") or "") else definition
            for definition in columns]


def _fit_of(columns: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The column `window.__hubFit` narrows to the grid, and the width it stops at: the
    identifier while art is beside it, never below its width without the art."""
    return next(({"col": definition["field"],
                  "least": definition["width"] - definition[ART_KEY].room_px}
                 for definition in columns if ART_KEY in definition), None)


_FIT = "params => window.__hubFit && window.__hubFit(params.api, params.context)"


def build(columns: list[dict[str, Any]], rows: list[dict[str, Any]], scope: str,
          # Any, not None: NiceGUI takes a sync or an async handler and so do these,
          # so a coroutine is as valid a return as nothing. Its own Handler type is
          # spelled the same way, for the same reason.
          on_select_rows: Callable[[list[dict[str, Any]]], Any] | None = None,
          on_context: Callable[[dict | None], Any] | None = None,
          on_header_context: Callable[[str | None], Any] | None = None,
          view_of: Callable[[], str] | None = None,
          rows_without_a_menu: list[str] | None = None,
          rows_not_selectable: list[str] | None = None) -> ui.aggrid:
    """A grid whose column layout is restored from, and saved to, the API.

    `view_of` names the view showing now. Given one, geometry is stored per view - the
    grid outlives a view change, so without it every view shares one set of widths.

    `on_select_rows` is what draws the checkboxes: a grid without one has none. A grid
    with a row menu has a header menu and the reverse (`on_context`, `on_header_context`).

    Raises `ValueError` unless exactly one column is a `grid.identifier()`, and unless
    every column carries a header tooltip.
    """
    marked = [definition.get("field") for definition in columns
              if IDENTIFIER_CLASS in str(definition.get("cellClass") or "")]
    if len(marked) != 1:
        raise ValueError(
            f"{scope}: a grid declares exactly one grid.identifier() column, "
            f"the one its rows are scanned by; this one declares {len(marked)}"
            + (f" ({', '.join(str(m) for m in marked)})" if marked else ""))
    bare = [str(definition.get("field")) for definition in columns
            if not definition.get("headerTooltip")]
    if bare:
        raise ValueError(f"{scope}: every grid column carries a header tooltip, "
                         f"passed as help=; these do not: {', '.join(bare)}")
    fit = _fit_of(columns)
    grid = ui.aggrid({
        "columnDefs": for_grid(columns),
        "rowData": rows,
        "rowHeight": base_row_px(columns),
        # Which grid a cell belongs to, for a column drawn by name. The focus follow keeps
        # its state here too, since the grid hands the same object to every callback.
        "context": {"scope": scope, "identifier": marked[0],
                    "landing": _LANDING.get(ui.context.client, {}).pop(scope, None),
                    "fit": fit},
        "defaultColDef": DEFAULT_COL_DEF,
        "accentedSort": True,
        **({"rowSelection": {**ROW_SELECTION, **({
            ":isRowSelectable":
                f"node => !{json.dumps(rows_not_selectable)}.includes(node.data.id)"
                f" && !node.data[{json.dumps(NOT_SELECTABLE)}]"}
            if rows_not_selectable is not None else {})}} if on_select_rows is not None else {}),
        # The ":" prefix marks this as JavaScript. Without it AG Grid calls a string and
        # the grid dies as an empty table rather than an error.
        ":getRowId": "params => params.data.id",
        ":getRowClass": f"params => params.data && params.data[{json.dumps(NO_DROP)}] "
                        f"? {json.dumps(NO_DROP_CLASS)} : undefined",
        # The checkbox belongs to the row, so it stays with the row's left edge.
        "selectionColumnDef": {"pinned": "left"},
        # The workbench follows the focused row, and focus is not selection: arrowing
        # must not disturb the checkboxes a bulk action reads.
        #
        # Marked on every fragment: AG Grid splits a row across the pinned and center
        # containers, each its own .ag-row.
        #
        # A focus the follow below makes is `quiet`: the row is the one already open.
        # Until the person's first key or press, a focus nobody caused is put back on the
        # landed row, not reported as them choosing the row that slid under the cell.
        ":onCellFocused":
            "params => { const c = params.context; "
            "const r = params.api.getDisplayedRowAtIndex(params.rowIndex); "
            "if (r && c.landedAt && !params.sourceEvent && r.data.id !== c.focusId "
            "&& Date.now() - c.landedAt < 3000) { window.__hubFocusRow = null; "
            "requestAnimationFrame(() => window.__hubFollowFocus(params.api, c)); return; } "
            "const quiet = r && c.quiet === r.data.id; c.quiet = null; "
            "if (r) { c.focusId = r.data.id; "
            "c.focusCol = params.column && params.column.getColId(); } "
            "if (r && !quiet) emitEvent('hub_row_focus', {id: r.data.id, col: c.focusCol, "
            f"scope: {json.dumps(scope)}}}); "
            "window.__hubFocusRow = params.rowIndex; "
            "window.__hubMarkFocus && window.__hubMarkFocus(); }",
        # Picking a row is a press or Enter, which arrowing through the list is not. A
        # window too narrow for the list and the panel side by side shows one at a time,
        # and this is what moves from the list to the panel. A press that did not move
        # the focus (a touch screen's does not always) still has to open that row.
        ":onCellClicked":
            "params => { const hit = params.event && params.event.target; "
            "if (params.column.getColId() === 'ag-Grid-SelectionColumn' || "
            "(hit && hit.closest && hit.closest('button, a, input, .q-btn'))) return; "
            "const c = params.context; "
            "if (params.data && c.focusId !== params.data.id) { "
            "c.focusId = params.data.id; c.focusCol = params.column.getColId(); "
            "window.__hubFocusRow = params.rowIndex; "
            "emitEvent('hub_row_focus', {id: c.focusId, col: c.focusCol, "
            f"scope: {json.dumps(scope)}}}); }} "
            f"emitEvent('hub_row_pick', {{scope: {json.dumps(scope)}}}); }}",
        ":onCellKeyDown":
            "params => { const key = params.event; "
            "if (key && key.key === 'Enter' && !(key.target.closest && "
            "key.target.closest('button, a, input, .q-btn'))) "
            f"emitEvent('hub_row_pick', {{scope: {json.dumps(scope)}}}); }}",
        # Rows are recycled as you scroll, so the mark rides the wrong row without
        # this.
        ":onBodyScroll": "() => { window.__hubMarkFocus && window.__hubMarkFocus(); }",
        ":onRowDataUpdated": "params => { params.context.changed = true; }",
        ":onModelUpdated":
            "params => window.__hubFollowFocus && "
            "window.__hubFollowFocus(params.api, params.context)",
        ":onFirstDataRendered":
            "params => window.__hubFollowFocus && "
            "window.__hubFollowFocus(params.api, params.context)",
        **({":onGridSizeChanged": _FIT, ":onDisplayedColumnsChanged": _FIT,
            ":onColumnResized": _FIT} if fit else {}),
        # AG Grid's own words - the filter menu on every column, "No Rows To Show", the
        # column menu. Empty in English, where its built-ins are already right.
        **({"localeText": grid_locale} if (grid_locale := i18n.under("grid")) else {}),
        "suppressDragLeaveHidesColumns": True,
        "animateRows": False,
        "suppressColumnMoveAnimation": True,
        # False deliberately: preventing the default stops the event reaching Quasar,
        # and ui.context_menu never opens.
        "preventDefaultOnContextMenu": False,
    },
        theme="quartz",
        # nicegui defaults this True, which fits columns to the grid width and so
        # overrides both the declared widths and any the user saved.
        auto_size_columns=False,
    ).classes(f"w-full grow min-h-0 {_ROW_CLASS.get(base_row_px(columns), '')}".strip())
    look = _art_look(columns)
    if look is not None:
        look.apply(grid)

    ui.run_javascript(
        f"if (window.__hubFocusScope !== {json.dumps(scope)}) {{"
        f" window.__hubFocusScope = {json.dumps(scope)};"
        " window.__hubFocusRow = null; }")
    # Installed once per page. Idempotent, so a second grid does not stack it.
    ui.run_javascript("""
    window.__hubMarkFocus = () => {
      const i = window.__hubFocusRow;
      document.querySelectorAll('.ag-row.manager-row-focus')
        .forEach(e => e.classList.remove('manager-row-focus'));
      if (i === undefined || i === null) return;
      document.querySelectorAll(`.ag-row[row-index="${i}"]`)
        .forEach(e => e.classList.add('manager-row-focus'));
    };
    window.__hubFollowFocus = (api, c) => {
      const changed = c.changed;
      c.changed = false;
      if (window.__hubFocusScope !== c.scope) return;
      if (c.landing && !c.focusId) return window.__hubLand(api, c);
      if (!c.focusId) return;
      const node = api.getRowNode(c.focusId);
      const at = node && node.displayed && node.rowIndex != null ? node.rowIndex : null;
      const active = document.activeElement;
      const inside = !!(active && active.closest('.ag-cell') && active.closest(
        `.ag-root-wrapper[grid-id="${api.getGridId()}"]`));
      if (at === window.__hubFocusRow) return window.__hubMarkFocus();
      window.__hubFocusRow = at;
      if (at !== null && inside) {
        api.ensureNodeVisible(node);
        c.quiet = c.focusId;
        api.setFocusedCell(at, c.focusCol);
      } else if (!inside) {
        if (at !== null && changed) api.ensureNodeVisible(node);
        api.clearFocusedCell();
      }
      window.__hubMarkFocus();
    };
    window.__hubLand = (api, c) => {
      let node = null, at = -1;
      for (const wanted of c.landing) {
        let place = 0;
        api.forEachNodeAfterFilterAndSort(n => {
          if (!node && Object.entries(wanted)
              .every(([key, value]) => String(n.data[key] ?? '') === value)) {
            node = n;
            at = place;
          }
          place++;
        });
        if (node) break;
      }
      if (!node) return;
      c.landing = null;
      c.landedAt = Date.now();
      for (const kind of ['keydown', 'pointerdown']) {
        document.addEventListener(kind, () => { c.landedAt = null; },
                                  {capture: true, once: true});
      }
      c.focusId = node.id;
      c.focusCol = c.identifier;
      window.__hubFocusRow = at;
      api.ensureNodeVisible(node, 'middle');
      const active = document.activeElement;
      if (!active || active === document.body) {
        c.quiet = node.id;
        api.setFocusedCell(at, c.identifier);
      }
      window.__hubMarkFocus();
    };
    window.__hubFit = (api, c) => {
      const fit = c && c.fit;
      const col = fit && api.getColumn(fit.col);
      if (!col) return;
      const now = col.getActualWidth(), most = col.getColDef().width;
      fit.own = now === most || now === fit.at;
      if (!fit.own || !col.isVisible() || col.getPinned() === 'right') return;
      const body = document.querySelector(
        `.ag-root-wrapper[grid-id="${api.getGridId()}"] .ag-body-viewport`);
      if (!body || !body.clientWidth) return;
      const shown = api.getAllDisplayedColumns(), at = shown.indexOf(col);
      const taken = shown.reduce((sum, one, i) => sum +
        (i < at || one.getPinned() === 'right' ? one.getActualWidth() : 0), 0);
      const handle = 8;
      fit.at = Math.max(fit.least, Math.min(most, body.clientWidth - taken - handle));
      if (fit.at !== now) api.setColumnWidths([{key: col, newWidth: fit.at}]);
    };
    """)
    if grid.options["context"]["landing"]:
        # The grid can draw before the script above arrives, and then nothing
        # after it would call it.
        ui.run_javascript(_LAND % grid.id)
    renderers.install()
    _restore(grid, scope, columns, view_of)
    _save_on_change(grid, scope, view_of,
                    frozenset(str(one["field"]) for one in columns if one.get("flex")))
    if on_select_rows is not None:
        chosen = _SELECTIONS[grid] = Selection(rows)

        async def said() -> None:
            # The count only; the focused row owns which game is on screen.
            result = on_select_rows(chosen.rows())
            if inspect.isawaitable(result):
                await result

        async def changed(event: Any) -> None:
            if chosen.take(event.args):
                await said()

        async def narrowed(event: Any) -> None:
            if chosen.hide(event.args):
                await said()

        # Not `get_selected_rows()`: whole rows pass the socket's cap at a few hundred.
        grid.on("selectionChanged", changed, js_handler=_SEND_SELECTION % {
            "id": grid.id, "chars": SELECTION_PART_CHARS, "count": _COUNT_HIDDEN})
        grid.on("modelUpdated", narrowed, js_handler=_SEND_HIDDEN % {
            "id": grid.id, "count": _COUNT_HIDDEN})
    if on_context is not None:
        # Only `data`: the full payload can fail to serialize and is then never sent.
        grid.on("cellContextMenu",
                lambda event: on_context((event.args or {}).get("data")), args=["data"])
    if on_header_context is not None:
        # Fires in Community and carries colId. The native column menu is Enterprise.
        grid.on("columnHeaderContextMenu",
                lambda event: on_header_context((event.args or {}).get("colId")),
                args=["colId"])
    _suppress_empty_menu(grid, rows=on_context is not None,
                         headers=on_header_context is not None,
                         without=rows_without_a_menu)
    return grid


def _suppress_empty_menu(grid: Any, *, rows: bool, headers: bool,
                         without: list[str] | None = None) -> None:
    """Stop a right-click with nothing behind it from opening this grid's menu.

    `rows` and `headers` say whether this grid has a menu for each; `without` names the
    row ids that have nothing even where the rest do, and a row whose data says `NO_MENU`
    has nothing right now, unless it sits inside a selection of several. Guarded by
    `tests/manager/test_context_menus_are_guarded.py`.
    """
    ui.run_javascript(f"""
    (() => {{
      // Retried rather than assumed: this runs while the page is still being built, and
      // an element that is not mounted yet would take the guard silently - which reads
      // exactly like the bug it fixes.
      const install = (tries) => {{
      const found = getElement({grid.id});
      const el = found && found.$el;
      if (!el) {{ if (tries > 0) requestAnimationFrame(() => install(tries - 1)); return; }}
      if (el.__hubMenuGuard) return;
      el.__hubMenuGuard = true;
      el.addEventListener('contextmenu', (event) => {{
        const header = event.target.closest('.ag-header-cell');
        const row = event.target.closest('.ag-row');
        let offer = false;
        if (header) {{
          // AG Grid's own selection column: no name to head a menu, nothing to pin or
          // hide. `column_menu` answers False for it.
          const id = header.getAttribute('col-id') || '';
          offer = {str(headers).lower()} && !id.startsWith('ag-Grid-');
        }} else if (row) {{
          const without = {json.dumps(list(without or []))};
          const id = row.getAttribute('row-id') || '';
          const node = found.api && found.api.getRowNode(id);
          // Inside a selection of several the menu is the selection's, whatever this row has.
          const inside = node && node.isSelected() && found.api.getSelectedNodes().length > 1;
          offer = {str(rows).lower()} && !without.includes(id)
                  && (inside || !(node && node.data && node.data[{json.dumps(NO_MENU)}]));
        }}
        if (!offer) {{
          event.stopPropagation();
        }}
      }}, true);
      }};
      install(60);
    }})()
    """)


_PINNING = """(() => {
  const grid = getElement(%(grid)d), api = grid && grid.api;
  const col = api && api.getColumn(%(col)s);
  const body = grid && grid.$el.querySelector('.ag-body-viewport');
  if (!col || !body) return null;
  const style = getComputedStyle(body);
  const padding = style.boxSizing === 'border-box'
    ? parseFloat(style.paddingLeft) + parseFloat(style.paddingRight) : 0;
  const side = to => api.getAllDisplayedColumns().filter(one => one.getPinned() === to)
    .reduce((sum, one) => sum + one.getActualWidth(), 0);
  return {pinned: col.getPinned(), width: col.getActualWidth(), left: side('left'),
          right: side('right'), body: parseFloat(style.width) - padding};
})()"""


@dataclass(frozen=True)
class Pinning:
    """Whether a column is pinned, and whether the grid has room to pin it."""

    pinned: bool = False
    room: bool = True

    @classmethod
    def read(cls, measured: dict[str, Any] | None) -> Pinning:
        """From the grid's own measures, put to the rule AG Grid unpins by. Nothing
        measured offers the pin."""
        if not measured:
            return cls()
        pinned = bool(measured.get("pinned"))
        taken = measured["left"] + measured["right"] + (0 if pinned else measured["width"])
        return cls(pinned, taken < measured["body"] - _BODY_AG_GRID_KEEPS_UNPINNED_PX)


async def pinning(table: Any, col_id: str | None) -> Pinning:
    """Asked of the grid rather than tracked beside it: a column can also be dragged in
    and out of the pinned area, and the room changes with the window."""
    if not col_id:
        return Pinning()
    return Pinning.read(await table.client.run_javascript(
        _PINNING % {"grid": table.id, "col": json.dumps(col_id)}))


async def header_menu(menu: Any, table: Any, columns: list[dict[str, Any]],
                      col_id: str | None) -> None:
    """Fill the menu for a right-click on a column header."""
    now = await pinning(table, col_id)
    menu.clear()
    with menu:
        column_menu(menu, table, columns, col_id, now)


def column_menu(menu: Any, table: Any, columns: list[dict[str, Any]],
                col_id: str | None, now: Pinning) -> bool:
    """Fill a context menu with what can be done to a column, or answer False.

    The header half of every grid's menu, in one place. Written out per grid, it is how
    one ends up without the pinning every other one has.
    """
    if not col_id or col_id.startswith("ag-Grid-"):
        return False
    header = next((definition.get("headerName") for definition in columns
                   if definition.get("field") == col_id), col_id)
    ui.item_label(str(header).replace("\n", " ")).props("header") \
        .classes("manager-menu-header")
    ui.separator()
    # One entry that says what it will do, rather than two where one is always a no-op.
    if now.pinned or now.room:
        ui.menu_item(t("word.unpin") if now.pinned else t("word.pin_left"),
                     lambda: pin(table, col_id, None if now.pinned else "left")) \
            .classes("manager-menu-item")
    else:
        with ui.menu_item(t("word.pin_left"), auto_close=False) \
                .classes("manager-menu-item manager-menu-blocked") \
                .props("aria-disabled=true"):
            ui.tooltip(t("manager.grid.pin_refused")).classes("manager-menu-tip")
    ui.menu_item(t("word.hide_column"),
                 lambda: table.run_grid_method("setColumnsVisible", [col_id], False)) \
        .classes("manager-menu-item")
    return True


async def pin(table: Any, col_id: str, pinned: str | None) -> None:
    """Pin a column, or unpin it with None, and save that as the person's layout."""
    table.run_grid_method("applyColumnState",
                          {"state": [{"colId": col_id, "pinned": pinned}]})
    saver = _SAVERS.get(table)
    if saver is not None:
        await saver({"colId": col_id, "pinned": pinned})


def layout_scope(scope: str, view_of: Callable[[], str] | None) -> str:
    """Where this grid's geometry lives, which is per view.

    Falls back to the bare scope when no view is known - a grid without views keeps the
    one layout it always had.
    """
    showing = (view_of() if view_of else "") or ""
    return f"{scope}::{showing}" if showing else scope


async def apply_layout(grid: ui.aggrid, scope: str, columns: list[dict[str, Any]],
                       view_of: Callable[[], str] | None = None) -> None:
    """Put the showing view's geometry on the grid.

    Called on gridReady and again on every view change, because the grid outlives a
    view: switching without this leaves the last view's widths on the new one's columns.
    """
    from manager.kit.api import ApiClient

    where = layout_scope(scope, view_of)
    try:
        held = await offload.io(ApiClient().preferences, where)
    except Exception:
        logger.warning("manager: could not read column state for %s", where, exc_info=True)
        _LAYOUTS.pop(grid, None)
        return
    kept = _LAYOUTS[grid] = Layout.read(where, held)
    grid.run_grid_method("applyColumnState",
                         {"state": applied_state(columns, kept), "applyOrder": True})
    try:
        kept.seen = _widths(await grid.run_grid_method("getColumnState"))
    except TimeoutError:
        logger.debug("manager: the grid did not answer in time; widths for %s not seen",
                     where)


def applied_state(columns: list[dict[str, Any]], kept: Layout) -> list[dict[str, Any]]:
    """The column state `apply_layout` puts on a grid: the person's order, then every
    column it does not name in declared order, each at the person's width and pin or
    else its declared ones."""
    declared = {definition["field"]: definition for definition in columns}
    order = list(dict.fromkeys(col_id for col_id in kept.order if col_id in declared))
    order += [field_id for field_id in declared if field_id not in order]
    # Every column gets a definite width and pin, or a view with none of its own keeps
    # the last view's. `defaultState` reads as if it would do this and does not - a
    # widened column survives the switch - and a pin there reaches the selection column,
    # which is not among `columns`, and unpins it.
    state = []
    for field_id in order:
        want = {"colId": field_id,
                "pinned": kept.pins.get(field_id, declared[field_id].get("pinned"))}
        chosen = kept.widths.get(field_id)
        if declared[field_id].get("flex"):
            # A width the person chose ends the fill, and a view without one restores it.
            want["flex"] = None if chosen is not None else declared[field_id]["flex"]
            width = chosen
        else:
            width = chosen if chosen is not None else declared[field_id].get("width")
        if width is not None:
            want["width"] = width
        state.append(want)
    return state


def _widths(state: list[dict[str, Any]] | None) -> dict[str, float]:
    return {str(entry["colId"]): entry["width"] for entry in state or []
            if entry.get("colId") and entry.get("width") is not None
            and not str(entry["colId"]).startswith(_GENERATED)}


def _fitted(context: Any) -> set[str]:
    """The column whose width `window.__hubFit` still owns, which no one chose."""
    fit = (context or {}).get("fit") or {}
    return {fit["col"]} if fit.get("own") else set()


def _restore(grid: ui.aggrid, scope: str, columns: list[dict[str, Any]],
             view_of: Callable[[], str] | None) -> None:
    # gridReady rather than a timer: a timer outlives the grid when the view changes,
    # and firing under a cleared container raises "parent slot has been deleted".
    grid.on("gridReady", lambda: apply_layout(grid, scope, columns, view_of))


def _save_on_change(grid: ui.aggrid, scope: str, view_of: Callable[[], str] | None,
                    filling: frozenset[str] = frozenset()) -> None:
    """Keep the layout a person leaves. `filling` names the `flex` columns, whose width
    moves with every other column's and is saved only when dragged itself."""
    from manager.kit.api import ApiClient

    async def save(done: dict[str, Any], change: str) -> None:
        """Keep what the person just changed, `done` being the column the event names."""
        kept = _LAYOUTS.get(grid)
        where = layout_scope(scope, view_of)
        # No layout read for the view showing, so none to write over it.
        if kept is None or kept.where != where:
            return
        try:
            state = [entry for entry in await grid.run_grid_method("getColumnState") or []
                     if not str(entry.get("colId", "")).startswith(_GENERATED)]
            now = _widths(state)
            resized = {col_id for col_id, width in now.items()
                       if kept.seen and kept.seen.get(col_id) != width}
            resized -= filling
            named = done.get("colId") if change == "widths" else None
            if resized - {named}:
                resized -= _fitted(await grid.run_grid_method("getGridOption", "context"))
            if named in now:
                resized.add(named)
            kept.widths.update({col_id: now[col_id] for col_id in resized})
            kept.seen = now
            if change == "order":
                kept.order = [str(entry["colId"]) for entry in state]
            if (change == "pins" and done.get("colId") and "pinned" in done
                    and done["pinned"] in _PIN_SIDES):
                kept.pins[str(done["colId"])] = done["pinned"]
            stored = kept.stored()
            if stored == kept.written:
                return
            await run.io_bound(ApiClient().put_preferences, where, stored)
            kept.written = stored
        except TimeoutError:
            # The browser did not answer in time. This fires on every resize, so a busy
            # moment is ordinary and the next one saves - a stack trace for it is what
            # teaches somebody to stop reading the log.
            logger.debug("manager: the grid did not answer in time; layout for %s not "
                         "saved this time", where)
        except Exception:
            # A layout that fails to save is worth a log and nothing more - it must
            # never take down the grid the user is working in.
            logger.warning("manager: could not save column state for %s", where, exc_info=True)

    _SAVERS[grid] = lambda done: save(done, "pins")
    for event, change in _SAVE_EVENTS.items():
        # A resize fires per pixel. nicegui's own throttle, so no timer outlives the
        # element; trailing_events keeps the final width.
        grid.on(event, lambda said, change=change: save(said.args or {}, change),
                throttle=SAVE_THROTTLE_S, trailing_events=True, js_handler=_BY_A_PERSON)
