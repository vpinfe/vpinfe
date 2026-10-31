"""What the workbench modules share: the section model, the dock and the common helpers."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from nicegui import run, ui

from common.config.labels import field_label, humanize
from common.failures import why
from common.games import asset_registry
from common.i18n import t
from manager.kit import busy, deeplink, game_tables, grid, media_ownership, panel
from manager.kit.on_page import on_page

# Which group a section belongs to, in the order they are shown.
# Short, because the outline and the body would otherwise say the same three phrases
# twice on one screen. What each group means is carried by the lens sitting in it and
# by the sections themselves, not by shouting the scope.
# Where the split sits until somebody drags it. Enough for a preview and the facts
# under it without taking the map's room.
DOCK_PX = 300


# Dragged in the browser, reported once on release - a round trip per pointer move
# would lag badly. Guarded, because the panel is rebuilt on every section change.
_GRIP = """
if (!window.__hubDockGrip) {
  window.__hubDockGrip = true;
  let drag = null;
  document.addEventListener('pointerdown', (e) => {
    const grip = e.target.closest && e.target.closest('.manager-dock-grip');
    if (!grip) return;
    const main = grip.closest('.manager-section-work');
    const dock = main && main.querySelector('.manager-dock');
    if (!dock) return;
    e.preventDefault();
    drag = { y: e.clientY, from: dock.getBoundingClientRect().height, main };
    grip.setPointerCapture(e.pointerId);
  });
  document.addEventListener('pointermove', (e) => {
    if (!drag) return;
    e.preventDefault();
    const room = drag.main.getBoundingClientRect().height;
    // Both regions keep a floor, so a drag cannot leave either as a sliver.
    const next = Math.min(room - 160, Math.max(140, drag.from - (e.clientY - drag.y)));
    drag.main.style.setProperty('--dock-h', Math.round(next) + 'px');
  });
  const done = () => {
    if (!drag) return;
    const px = parseInt(drag.main.style.getPropertyValue('--dock-h'), 10);
    const drag_section = drag.main.dataset.dockSection || '';
    drag = null;
    if (px) emitEvent('hub_dock_px', {section: drag_section, px});
  };
  document.addEventListener('pointerup', done);
  document.addEventListener('pointercancel', done);
}
"""


# Dragging one member to a new place. Pointer events, not HTML5 drag-and-drop, for the
# reason the dock grip uses them: HTML5 drag fires no move events over its own source
# and is inert on touch.
# The panel is rebuilt on every write, so its scroll position has to be carried across
# by hand. Remembered on the window rather than in the panel's state: the value changes
# on every scroll frame and none of those are worth a round trip to Python.
_KEEP_SCROLL = """
(() => {
  const body = document.querySelector('.manager-workbench-body');
  if (!body) return;
  const held = window.__hubListTop || (window.__hubListTop = {});
  const key = '%s';
  if (held[key]) body.scrollTop = held[key];
  if (body.dataset.scrollWired) return;
  body.dataset.scrollWired = '1';
  body.addEventListener('scroll', () => { held[key] = body.scrollTop; },
                        {passive: true});
})()
"""


_ADD_BOX = """
(() => {
  let tries = 0;
  const wire = () => {
    const vm = getElement(%d);
    const box = vm && vm.$refs.qRef;
    if (!box) { if (++tries < 40) setTimeout(wire, 25); return; }
    vm.findFilteredOptions = () => {
      const needle = vm.$el.querySelector('input[type=search]')?.value.toLocaleLowerCase();
      const joined = said => String(said ?? '').toLocaleLowerCase().replace(/[\\s.]+/g, '');
      return needle ? vm.initialOptions.filter(one =>
        String(one.label ?? '').toLocaleLowerCase().includes(needle)
        || joined(one.also).includes(joined(needle))) : vm.initialOptions;
    };
    vm.$watch('filteredOptions', () => setTimeout(() => {
      const typed = vm.$el.querySelector('input')?.value;
      if (typed && box.getOptionIndex() === -1) box.moveOptionSelection(1, true);
    }));
    if (%s) { box.focus(); box.showPopup(); }
  };
  wire();
})()
"""


_FOCUSABLE = "input:not(.hidden), textarea, button, [tabindex='0']"


# Where focus is on a setting, [[setting, which of its controls], which row]; [] for
# elsewhere in the panel or a dialog over it, null for outside both. Marks every setting
# drawn so far, so the one found afterwards is one the new draw made.
_FOCUSED_SETTING = """(() => {
  for (const one of document.querySelectorAll('[data-setting]')) one.dataset.drawn = '1';
  const at = document.activeElement;
  const one = at && at.closest('[data-setting]');
  if (!one) return at && at.closest('.manager-workbench-body, .q-dialog') ? [] : null;
  const lines = [...document.querySelectorAll('.manager-field-line[data-setting]')];
  return [[one.dataset.setting, [...one.querySelectorAll(FOCUSABLE)].indexOf(at)],
          lines.indexOf(at.closest('.manager-field-line'))];
})()""".replace("FOCUSABLE", json.dumps(_FOCUSABLE))


# Focus fell to the page because the control that held it was drawn away: `[setting,
# which of its controls]`. A focus that went elsewhere is left where it is.
_REFOCUS_IN_PLACE = """(() => {
  const [key, index] = %s;
  setTimeout(() => {
    const at = document.activeElement;
    if (at && at !== document.body) return;
    const one = document.querySelector(`[data-setting="${CSS.escape(key)}"]`);
    const controls = one ? [...one.querySelectorAll(FOCUSABLE)] : [];
    (controls[index] || controls[0])?.focus();
  }, 50);
})()""".replace("FOCUSABLE", json.dumps(_FOCUSABLE))


_REFOCUS_SETTING = """(() => {
  const [places, row] = %s;
  let tries = 0;
  const look = () => {
    const drawn = [...document.querySelectorAll(
      '.manager-field-line[data-setting]:not([data-drawn])')];
    if (!drawn.length) {
      if (++tries < 40) setTimeout(look, 25);
      return;
    }
    for (const [key, index] of places) {
      const one = document.querySelector(
        `[data-setting="${CSS.escape(key)}"]:not([data-drawn])`);
      if (!one) continue;
      const controls = [...one.querySelectorAll(FOCUSABLE)];
      (controls[index] || controls[0])?.focus();
      return;
    }
    if (row >= 0) drawn[Math.min(row, drawn.length - 1)].querySelector(FOCUSABLE)?.focus();
  };
  look();
})()""".replace("FOCUSABLE", json.dumps(_FOCUSABLE))


def _rebuilds(context: dict[str, Any], subject: str,
              again: Callable[[], Any], refresh_with: str = "refresh_game",
              refresh_of: str = "game_id", grid_first: bool = False) -> Callable[[], Any]:
    """A rebuild that puts the grid behind the panel right too. `grid_first` where the
    panel is drawn from what the grid holds, as a selection's menu is."""
    async def behind() -> None:
        """The grid, put right. Every write needs this; only some need a rebuild."""
        refresh = context.get("state", {}).get(refresh_with)
        if callable(refresh) and context.get(refresh_of):
            await refresh(context[refresh_of])

    async def rebuild() -> None:
        if grid_first:
            await behind()
            await again()
            return
        # Send nothing to the browser below this line. The slot is gone.
        await again()
        await behind()

    context["saved"] = behind
    return rebuild


# Where a fresh client lands, per rail. Identity, because selecting a row is usually
# navigation rather than curation: the first question a selection asks is what this is,
# and the answer is also where the things you can do to it live.
#
# Per rail rather than one value, because a rail declares its own landing place - and a
# table selected on purpose should not open on the machine that contains it.
DEFAULT_SECTION = {"game": "game_details", "table": "table_details",
                   # Games, not Details: a collection is opened to see
                   # what is in it far more often than to rename it.
                   "collection": "collection_games",
                   "theme": "theme_details", "player": "player_details"}


# Below this the outline costs more than it gives: 132px of it against a 320px
# workbench leaves the map too narrow to read, and the section headers are already
# a list of what is here. It earns its place once there is room for both.
#
# Enforced in CSS by a container query, not here. Quasar resizes the pane live while
# you drag and only tells the server on release, so a server-side threshold changed
# the layout a beat after you let go - which is exactly when you can no longer see
# what you were aiming at.
OUTLINE_FROM_PX = 520


# The panel's own vocabulary, which this surface no longer keeps a second copy of.
# Still named here because the call sites below read better for it.
HEADING = panel.HEADING


FULL = panel.FULL


@dataclass(frozen=True)
class Section:
    """One block in the panel: what it is called and how to draw it.

    One name, on the row that opens it. The content is headed by its own groups.

    `label` takes the context, since a name that counts something has to count this
    game's. `build` is async because a section may need a fetch.
    """

    key: str
    label: Callable[[dict[str, Any]], str]
    build: Callable[[dict[str, Any]], Any]
    # What this section is about. The rail is what the selected subject can be asked,
    # so a section that only makes sense for one of them says so and is simply absent
    # for the other - rather than being present and answering a question nobody asked.
    # Keys stay unique across every rail, so `section=` in an address means one thing.
    subjects: frozenset[str] = frozenset({"game", "table"})
    # Whether this subject can answer for it right now. A section that would open onto
    # nothing leaves the rail rather than sitting there empty - offering a settings
    # editor for a program that is not on this machine is a form of lying, and an empty
    # row is a place somebody goes once and learns to stop trusting.
    shown: Callable[[dict[str, Any]], bool] | None = None
    # Whether this section works on a picked thing beside its browse region. Only the
    # one that has something to pick declares it: reserving the room everywhere left
    # a section like Game details as four lines of text over an empty half-panel,
    # with the sections under it pushed to the bottom edge. A predicate on the context
    # where it depends on the subject: a file with nothing to show has no stage.
    dock: bool | Callable[[dict[str, Any]], bool] = False
    # A heading over the run of sections it starts. Empty for a rail short enough to
    # read without one, which every rail now is.
    group: str = ""
    # Whether the section opens with its own name as a heading. Only one whose content
    # draws no heading of its own sets it.
    titled: bool = False
    # A Material icon drawn before the name; only an extension's section sets one.
    icon: str = ""
    # Drawn after the name, for a section an extension VPinFE did not ship added.
    mark: Callable[[], None] | None = None
    # Draws into the holder at the row's right edge, called again whenever the thing
    # it reports changes. Only an exception has a glyph there.
    corner: Callable[[dict[str, Any], Any], None] | None = None


def _docked(item: Section, context: dict[str, Any]) -> bool:
    return item.dock(context) if callable(item.dock) else item.dock


def dock_height(state: dict[str, Any], section: str) -> int:
    """The height the user dragged this section's dock to, or the default."""
    return int((state.get("dock_px") or {}).get(section) or DOCK_PX)


def dock_dragged(state: dict[str, Any], args: Any) -> None:
    """The browser's report of a drag: the section it was in and the height it ended at."""
    if not isinstance(args, dict):
        return
    section = str(args.get("section") or "")
    px = args.get("px")
    if section and isinstance(px, (int, float)) and px > 0:
        state.setdefault("dock_px", {})[section] = int(px)


async def _pane_build(container: ui.column, state: dict[str, Any],
                      draw: Callable[[], Awaitable[None]]) -> None:
    """One pane build, after any already running, with the pane busy from the call
    until the build that draws has drawn.

    Builds are serialized, and a superseded one gives up rather than drawing. Without
    this the panel doubles: clearing happens after the reads and the drawing after that,
    so two builds that overlap both clear an empty container and then both append. A
    drag can start three - the divider, the mode it settles, and the window listener -
    so this is the ordinary case, not the rare one.
    """
    lock: asyncio.Lock = state.setdefault("build_lock", asyncio.Lock())
    state["build_seq"] = mine = state.get("build_seq", 0) + 1
    if (declare_drop := state.get("declare_drop")) is not None:
        declare_drop()
    with busy.held(container, over=True):
        async with lock:
            if state["build_seq"] != mine:
                return
            # Cleared again under the lock: a build that was already drawing declares its
            # own subject after this build's first clear, and a panel that registers
            # neither leaves no rebuild behind for a drop to run.
            state["panel_rebuild"] = None
            if declare_drop is not None:
                declare_drop()
            await draw()


def _blank(container: ui.column, title: ui.column, heading: str, said: str) -> None:
    """The panel with nothing to show. Its own helper so the drawing paths can read
    their subject before clearing, and still say this when there is none."""
    container.clear()
    title.clear()
    with container:
        _title(title, heading, said)


def _section_row(context: dict[str, Any], section: Section, open_now: bool,
                 *, order: int = 0) -> None:
    """One section's name - the rail entry, at either width.

    The name is text: a badge said nothing about a game's identity, and the sections
    still to come would each need a picture that means only itself. Words already do.

    The chevron is the exception, and it is not the same thing - it says the row opens,
    which is a fact about the control rather than a label for the section. Without it
    the stacked rows are four words with no sign that any of them do anything.
    """
    row = ui.row().classes("items-stretch gap-0 no-wrap manager-section-row") \
        .style(f"order: {order}")
    if open_now:
        row.classes(add="manager-section-on")
    with row:
        name = ui.row().classes("items-center no-wrap grow min-w-0 manager-section-hit")
        with name:
            if section.icon:
                ui.icon(section.icon, size="18px").classes("mr-2")
            text = ui.label(section.label(context)).classes("manager-section-name")
            if section.mark is not None:
                # Inside the label, so it wraps with the words and stays on the last one.
                with text:
                    section.mark()
        if section.corner is not None:
            holder = ui.element("span").classes("manager-section-corner")
            section.corner(context, holder)
        # Shown only where the rows stack, because only there does one open under
        # another. Beside its content it would be pointing at nothing.
        with ui.row().classes("items-center manager-section-caret"):
            ui.icon("expand_more", size="18px")
    # The whole band, name and chevron alike - a header that answers on the word but
    # not on the arrow is a control with two rules to learn.
    row.on("click", lambda key=section.key: _pick(context, key))

    async def relabel() -> None:
        # A count in the name has to follow the thing it counts.
        text.text = section.label(context)

    context["redraws"].append(relabel)
    context.setdefault("relabels", []).append(relabel)


def _pick(context: dict[str, Any], key: str) -> None:
    """Open this section. The row that is already open stays open: a rail names where
    you are, so a click on it must never blank the pane."""
    if context["state"].get("section") != key:
        _choose(context, key)


def _choose(context: dict[str, Any], key: str) -> None:
    """Make it the section on screen."""
    context["state"]["section"] = key
    deeplink.sync(context["state"])
    asyncio.create_task(context["rebuild"]())


def _title(target: ui.column, name: str, subtitle: str) -> None:
    with target:
        ui.label(name).classes("text-base manager-workbench-title leading-tight truncate") \
            .tooltip(name)
        ui.label(subtitle).classes("text-xs manager-workbench-label leading-none truncate")


def _table_address(context: dict[str, Any], table_id: str) -> str:
    """The Tables grid at one file, as an address the browser follows.

    A link rather than a click handler: the shell's view is switched by loading the
    address, which no panel-level redraw does.
    """
    return "/manager?" + deeplink.query({"view": "tables",
                                         "game": context.get("game_id") or "",
                                         "table": table_id})


def _provenance(context: dict[str, Any], via: str | None, borrowed: bool) -> None:
    """Whose file the slot shows, in a line. A borrowed one names where it comes from,
    and the game's file links to the game's view."""
    key = media_ownership.key_of(via)
    if not borrowed or key != media_ownership.GAME:
        ui.label(media_ownership.sentence(via, viewing_a_table=bool(context["lens"]))) \
            .classes("manager-help")
    elif str(via) == "default":
        ui.label(t("manager.media_ownership.from_default")).classes("manager-help")
    else:
        game_id = str(context["game_id"])
        panel.link(t("manager.media_ownership.from_game"),
                   to="/manager?" + deeplink.query({"view": "games", "game": game_id}),
                   hint=t("manager.media_ownership.via_game"))()


def _said_line(made: str, table: dict[str, Any] | None = None) -> None:
    said = game_tables.table_name(table) if table else ""
    if not (made or said):
        return
    with ui.row().classes("items-center gap-1 w-full no-wrap min-w-0 manager-member-table"):
        if made:
            ui.label(made).classes("manager-cell-made whitespace-nowrap")
        if made and said:
            ui.label(game_tables.JOIN.strip()).classes("manager-cell-join")
        if said:
            _built_label(table or {}, said).classes("min-w-0")


def _built_label(table: dict[str, Any], said: str) -> ui.element:
    return _said_name(table, said).classes("manager-cell-built")


def _said_name(table: dict[str, Any], said: str) -> ui.element:
    tip = str(table.get("filename") or "")
    if not game_tables.names_a_file(table):
        return ui.label(said).classes("truncate").tooltip(tip)
    with ui.element("div").classes(grid.FILE_CLASS).tooltip(tip) as box:
        ui.label(said)
    return box


def _said_file(line: str, tip: str) -> ui.element:
    """A file name under a table's name, cut at its front like a file name standing as
    the table's name: what tells two builds apart is at the end."""
    with ui.element("div").classes(f"{grid.FILE_CLASS} manager-member-table").tooltip(tip) \
            as box:
        ui.label(line)
    return box


def _table_line(table: dict[str, Any], tables: list[dict[str, Any]] | None = None) -> str:
    """Which of a game's tables this is, told apart from the rest of `tables`. One form,
    owned by `subject`.

    The header is the one place a long filename cannot ellipse gracefully, so the
    fallback is trimmed from the end - where the part that separates two tables of a
    game sits.
    """
    said = game_tables.table_name(table)
    if said == str(table.get("filename") or ""):
        return _tail(said, 40)
    return game_tables.name_among(table, tables or [])


def _tail(name: str, limit: int) -> str:
    """A filename cut from the front, keeping the end.

    The tables in one game share a long prefix and differ at the tail - "(Williams
    1993)12" against "(Williams 1993)lw" - so trimming the end trims the only half
    that tells them apart.
    """
    stem = name[:-4] if name.lower().endswith(".vpx") else name
    return stem if len(stem) <= limit else "\u2026" + stem[-(limit - 1):]


@on_page
async def _put(call: Callable[..., Any], *args: Any) -> bool:
    """One write, off the loop. False where it was refused, and the user has been told.

    Off the loop because this is an HTTP call to our own process: made on the event
    loop it blocks the server from answering it, and the browser reports the socket as
    lost rather than slow.
    """
    try:
        await run.io_bound(call, *args)
    except Exception as exc:
        ui.notify(t("manager.workbench.save_failed"), caption=why(exc), type="negative")
        return False
    return True


@on_page
async def _save(context: dict[str, Any], call: Callable[..., Any], *args: Any) -> bool:
    """A value written and the grid row put right. The panel is left as it is: what reads
    the value is the caller's to refill."""
    if not await _put(call, *args):
        return False
    await context["saved"]()
    return True


@on_page
async def _write(context: dict[str, Any], call: Callable[..., Any],
                 *args: Any, shape: bool = True) -> None:
    """One write, then put right whatever it changed.

    `shape=False` where the panel is unchanged but for the value just saved.
    """
    if await _put(call, *args):
        await (context["rebuild"]() if shape else context["saved"]())


@on_page
async def _save_overrides(context: dict[str, Any], changes: dict[str, Any], *,
                          table: bool) -> bool:
    """Write one field and rebuild; False where the write was refused. Off the loop:
    this is an HTTP call to our own process, and made on the event loop it blocks the
    server from answering it."""
    library = context["library"]
    try:
        if table:
            await run.io_bound(library.set_table_overrides, context["game_id"],
                               context["lens"], changes)
        else:
            await run.io_bound(library.set_game_overrides, context["game_id"], changes)
    except Exception as exc:
        ui.notify(t("manager.workbench.save_failed"), caption=why(exc), type="negative")
        return False
    await context["rebuild"]()
    return True


@on_page
async def _save_game_overrides(context: dict[str, Any], changes: dict[str, Any]) -> bool:
    """Write the game's own fields and put right what reads them, in place: the grid row
    and the header. The caller refills the sections that read the field it wrote. False
    where the write was refused."""
    library = context["library"]
    try:
        await run.io_bound(library.set_game_overrides, context["game_id"], changes)
    except Exception as exc:
        ui.notify(t("manager.workbench.save_failed"), caption=why(exc), type="negative")
        return False
    fresh = next((one for one in library.games if one["id"] == context["game_id"]), None)
    if fresh is not None and fresh is not context["game"]:
        context["game"].clear()
        context["game"].update(fresh)
    await context["saved"]()
    context["retitle"]()
    return True


def _outside(links: Sequence[dict[str, Any]]) -> list[tuple[Any, Any]]:
    return [(str(one.get("name") or ""),
             panel.link_out(urlparse(str(one["url"])).netloc.removeprefix("www."),
                            to=str(one["url"])))
            for one in links if one.get("url")]


def _outside_lines(links: Sequence[dict[str, Any]]) -> None:
    for one in links:
        if one.get("url"):
            with ui.element("div").classes("manager-help"):
                panel.link_out(str(one.get("name") or ""), to=str(one["url"]))()


def _asset_name(kind: str) -> str:
    """A kind's own word for itself. `asset_registry` cases the acronyms once."""
    try:
        return asset_registry.spec_for(kind).label
    except KeyError:
        return humanize(kind)


def _switch(value: bool, on_change: Callable[[Any], Any], *,
            disabled: bool = False, hint: str = "") -> None:
    """The panel's switch, drawn where it is called rather than handed back."""
    panel.switch(value, on_change, disabled=disabled, hint=hint)()


def _rows(target: Any, entries: Sequence[tuple[Any, ...]]) -> None:
    """The panel's fact list, with this surface's labels cased on the way in.

    The pane writes them as prose - `Made by`, `Last played` - so the casing rule is
    applied here rather than inside the list, where it would also reach labels a
    registry has already settled and turn `RAR Tool Path` into `Rar Tool Path`.
    """
    panel.facts(target, [(entry[0] if entry[0] in (panel.HEADING, panel.FULL,
                                                   panel.ASIDE, panel.LEDE)
                          else field_label(str(entry[0])), *entry[1:])
                         for entry in entries])
