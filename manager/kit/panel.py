"""The panel: a rail of named destinations, one open, and the facts beside it.

The side pane draws it about a game; Settings draws it about the install. Both render
through here, so a treatment is changed in one place rather than in each surface that
happens to show the same kind of value.

Every control constructor returns the callable `facts` takes as a value, so it drops
into an entry list as the second half of a pair.
"""

from __future__ import annotations

import html
import inspect
import json
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, replace
from types import SimpleNamespace
from typing import Any

from nicegui import background_tasks, ui

from common import icons
from common.config import path_state
from common.games.collection_store import LAST_COLLECTION
from common.i18n import t
from manager.kit import dropdown, game_tables, offload, place, tag_chips, thumbnails, verbs
from manager.kit.on_page import on_page

logger = logging.getLogger("vpinfe.manager.kit.panel")

# Rows that are not a fact. A group's title and an action strip span both columns, so
# every group keeps the one shared label width.
HEADING = object()
# A heading that starts with a mark, as (OWNED, (text, mark)): the extension a group came
# from, where it is not VPinFE's own.
OWNED = object()
FULL = object()
# The value column alone, for a line that belongs to the control above it rather than
# to the row. Across both columns it starts at the label's edge and reads as a caption
# for the label instead.
ASIDE = object()
# Both columns, for a line belonging to the heading above it. Sits tight under the
# heading and leaves room before the first row, so it reads as part of the heading
# rather than as something between two groups.
LEDE = object()
# A fact that is only read, as (READ, (label, value)): no control to centre, so its row is
# as tall as the words and a run of them sits close together.
READ = object()
# A fact about the row above it, as (SUB, (label, value)): its label is indented behind an
# elbow so it reads as part of that row.
SUB = object()
# A line across both columns between two groups of rows that have no heading of their own,
# as (RULE, None).
RULE = object()

# A rail row that names the rows under it rather than opening anything.
GROUP = object()

# The rail's width. A lever rather than a literal: the pane is narrow and Settings is
# not, and both rails are the same control.
RAIL_PX = 152

# Where the open section sits among the rows where they stack. Three buckets rather
# than one order per row: flex keeps document order inside a bucket, so the rows above
# the open one and the rows below it each need only a number, and the work goes between.
WORK_ORDER = 1
AFTER_WORK_ORDER = 2


def headed(entries: Sequence[tuple[Any, ...]], first: str) -> list[tuple[Any, ...]]:
    """`entries`, with `first` heading the opening group unless it already has a heading."""
    rows = list(entries)
    if rows and rows[0][0] is not HEADING:
        rows.insert(0, (HEADING, first))
    return rows


def header(name: str, note: str = "",
           findings: Sequence[Callable[[], None]] = (), caption: str = "",
           menu: Sequence[Verb] = (), *, chip: Callable[[], None] | None = None,
           picture: Callable[[], None] | None = None) -> None:
    """What the content under it is about, where the rail row is too far away to say it.

    `caption` sits under the name, as a game's type line does in its panel. `note` is drawn
    inside the same rule as the name, and so is each of `findings`: what is wrong with
    everything under it, before the first of its headings. `menu` is what can be done
    with the subject as a whole, at the end of the name. `chip` is a state of the subject
    drawn beside its name, and `picture` is a frame before it.
    """
    if not note and not findings and not caption and not menu and not chip and not picture:
        ui.label(name).classes("text-base manager-workbench-title manager-panel-heading")
        return
    with ui.element("div").classes("manager-panel-heading"):
        if chip or picture:
            _identity_row(name, caption, menu, chip, picture)
        else:
            if menu:
                with ui.row().classes("items-center no-wrap w-full"):
                    ui.label(name).classes("text-base manager-workbench-title grow min-w-0")
                    subject_menu(ui.row().classes("shrink-0 no-wrap"), menu)
            else:
                ui.label(name).classes("text-base manager-workbench-title")
            if caption:
                ui.label(caption).classes("text-xs manager-workbench-label")
        if note:
            ui.label(note).classes("manager-help manager-panel-note")
        for finding in findings:
            finding()


def _identity_row(name: str, caption: str, menu: Sequence[Verb],
                  chip: Callable[[], None] | None,
                  picture: Callable[[], None] | None) -> None:
    """The name with a frame before it and a chip after it. A long name is cut and shows
    whole as its tooltip; the chip keeps its width."""
    with ui.row().classes("items-center no-wrap w-full gap-3"):
        if picture:
            picture()
        with ui.column().classes("gap-0 min-w-0 grow"):
            with ui.row().classes("items-center no-wrap gap-2 min-w-0 max-w-full"):
                ui.label(name).classes(
                    "text-base manager-workbench-title truncate min-w-0").tooltip(name)
                if chip:
                    with ui.element("div").classes("shrink-0"):
                        chip()
            if caption:
                ui.label(caption).classes("text-xs manager-workbench-label max-w-full break-words")
        if menu:
            subject_menu(ui.row().classes("shrink-0 no-wrap"), menu)


# A control inside the row acts on its own: Add on a missing kind must not also pick it.
_CLICK_OUTSIDE_CONTROLS = "(e) => { if (!e.target.closest('button, a, input')) emit(); }"


@dataclass(frozen=True)
class Pick:
    """A fact row that can be picked: a third element on its entry. The row's two cells
    answer a click and a key together, and the picked one is marked."""

    on_pick: Callable[[], Any]
    picked: bool = False
    # The row's `data-drop` where a file dropped on it means something; see `uploads`.
    drop: str = ""


def facts(target: Any, entries: Sequence[tuple[Any, ...]]) -> None:
    """The facts of one section, as (label, value) pairs.

    One list for all of them, not a row each, so the label column is the width of the
    longest label. A row whose value is not text passes a callable and draws its own;
    it has to be in *this* list, or it sizes a label column of its own and its value
    starts somewhere else entirely.

    `min-w-0` is what lets a value shrink: a grid item refuses to go below its content
    width without it, and the row wraps instead of ellipsing.
    """
    with target.element("div").classes("manager-facts"):
        for entry in entries:
            label, value = entry[0], entry[1]
            pick = entry[2] if len(entry) > 2 else None
            if pick is not None:
                with _pickable(target, pick):
                    _fact_row(target, label, value)
                continue
            _fact_row(target, label, value)


def _pickable(target: Any, pick: Pick) -> Any:
    """The row's wrapper, which takes no box of its own so the cells stay in the list's
    grid."""
    row = target.element("div").classes("manager-fact-pick") \
        .props('tabindex=0 role=button') \
        .on("click", pick.on_pick, js_handler=_CLICK_OUTSIDE_CONTROLS) \
        .on("keydown.enter.self", pick.on_pick) \
        .on("keydown.space.self.prevent", pick.on_pick)
    if pick.drop:
        row._props["data-drop"] = pick.drop
    if pick.picked:
        row.classes(add="manager-fact-pick--on")
        row.props["aria-pressed"] = "true"
    return row


def _fact_row(target: Any, label: Any, value: Any) -> None:
    """One entry of `facts`: a heading, a full-width line, or a label and its value."""
    if label is HEADING:
        target.label(str(value)).classes("manager-fact-heading")
        return
    if label is OWNED:
        text, mark = value
        with target.element("div").classes("manager-fact-heading manager-heading-owned"):
            mark()
            ui.label(str(text))
        return
    if label is FULL:
        with target.element("div").classes("manager-fact-full"):
            value()
        return
    if label is LEDE:
        with target.element("div").classes("manager-fact-full manager-fact-lede"):
            value()
        return
    if label is READ:
        name, shown = value
        target.label(str(name)).classes("manager-fact-label manager-fact-read")
        _draw_value(target, shown)
        return
    if label is SUB:
        name, shown = value
        target.label(str(name)).classes("manager-fact-label manager-fact-sub")
        _draw_value(target, shown)
        return
    if label is RULE:
        target.element("div").classes("manager-fact-rule")
        return
    if label is ASIDE:
        with target.element("div").classes("manager-fact-aside"):
            value()
        return
    # As given. A label that came from a registry is already the answer, and
    # re-casing it is how "RAR Tool Path" reached a user as "Rar Tool Path" -
    # the casing rule is a fallback for a bare key, not a filter over finished
    # words. A surface that writes its labels in prose cases them on the way in.
    target.label(str(label)).classes("manager-fact-label")
    _draw_value(target, value)


_CLIPPED_JS = """
if (!window.__hubClipWatch) {
  window.__hubClipWatch = true;
  const mark = () => {
    for (const el of document.querySelectorAll('.manager-fact-value, .manager-workbench-title')) {
      el.classList.toggle('manager-clipped', el.scrollWidth > el.clientWidth + 1);
    }
  };
  const soon = () => requestAnimationFrame(() => requestAnimationFrame(mark));
  new MutationObserver(soon).observe(document.body,
                                     {childList: true, subtree: true});
  new ResizeObserver(soon).observe(document.body);
  soon();
}
"""


def install_fact_tooltips() -> None:
    """Watch which fact values and pane titles are clipped. Once per page, before any
    panel."""
    ui.run_javascript(_CLIPPED_JS)


def _draw_value(target: Any, value: Any) -> None:
    if callable(value):
        value()
        return
    target.label(str(value)).classes("manager-fact-value truncate min-w-0") \
        .tooltip(str(value))


class Region:
    """The part of a page that reads a value another control sets.

    The control's handler saves and calls `refill`, which draws this part again where it
    stands: nothing dims, nothing around it is cleared, and scroll stays. A control inside
    the region is replaced by the refill it triggers: the page gives focus, and what was
    typed, to the control that takes its place, and anything a handler reads afterwards comes
    from the new part.

    `build` may read before it draws, so it may be async. The new part is then drawn out of
    sight beside the old one, and the two trade places in one step: the page is never empty
    and never dimmed while it is read. A second `refill` while one is under way wins. A build
    that fails keeps the part on screen; with none on screen the error is raised, since an
    empty region would pass for a part with nothing to say.

    Calling it draws it, so it is a `facts` value as it is. Its container takes no part in
    the grid, so as a facts value its build draws one element.
    """

    def __init__(self, build: Callable[[], Any],
                 swapped: Callable[[], Any] | None = None) -> None:
        """`swapped` is called each time a new part replaces the old one on screen, for state
        that has to describe what is shown rather than what was just built."""
        self._build = build
        self._swapped = swapped
        self._box: ui.element | None = None
        self._turn = 0

    def __call__(self) -> None:
        self._box = ui.element("div").classes("manager-region")
        if not inspect.iscoroutinefunction(self._build):
            with self._box:
                self._build()

    def refill(self) -> None:
        """Draw the part again in the same container. A page that has been left is let be."""
        box = self._box
        if box is None or box.is_deleted:
            return
        if inspect.iscoroutinefunction(self._build):
            background_tasks.create(self.refilled(), name="region-refill")
            return
        box.clear()
        with box:
            self._build()
        place.give_back(box)

    async def refilled(self) -> None:
        """`refill`, for a build that waits, ended when the new part is in place."""
        box = self._box
        if box is None or box.is_deleted:
            return
        self._turn += 1
        turn = self._turn
        with box:
            fresh = ui.element("div").classes("manager-region")
            fresh.set_visibility(False)
        try:
            with fresh:
                await self._build()
        except Exception:  # noqa: BLE001 - what is drawn stays; the part is read again at the next change
            kept = any(one is not fresh for one in box.default_slot.children)
            if not fresh.is_deleted:
                fresh.delete()
            if not kept:
                raise
            logger.exception("A region could not be drawn again")
            return
        if turn != self._turn or box.is_deleted:
            if not fresh.is_deleted:
                fresh.delete()
            return
        for one in list(box.default_slot.children):
            if one is not fresh and not one.is_deleted:
                one.delete()
        fresh.set_visibility(True)
        place.give_back(box)
        if self._swapped is not None:
            self._swapped()


def region(build: Callable[[], Any], swapped: Callable[[], Any] | None = None) -> Region:
    """A part of the page drawn by `build`, which `refill()` draws again in place."""
    return Region(build, swapped)


# A redraw rebuilds the column at the top, and a deep link opens a row far down it: where the
# rows stack, the open one is brought to the top so its work is what shows.
_OPEN_ROW_IN_VIEW_JS = """
const row = document.querySelector('.manager-section-on');
const column = row && row.closest('.manager-sections');
if (column && getComputedStyle(column).flexDirection === 'column') {
  row.scrollIntoView({block: 'start'});
}
"""


KEEP_RAIL_SCROLL_JS = """
(() => {
  const held = window.__hubRailTop || (window.__hubRailTop = {});
  const key = %(key)s, id = %(id)s;
  let tries = 0;
  const wire = () => {
    const rail = document.getElementById(id);
    if (!rail) { if (++tries < 40) setTimeout(wire, 25); return; }
    if (held[key] && rail.scrollHeight <= rail.clientHeight && ++tries < 40) {
      setTimeout(wire, 25);
      return;
    }
    if (held[key]) rail.scrollTop = held[key];
    rail.addEventListener('scroll', () => { held[key] = rail.scrollTop; }, {passive: true});
  };
  wire();
})()
"""


def sections(entries: Sequence[tuple[Any, ...]], current: str,
             on_pick: Callable[[str], Any], *, rail_px: int = RAIL_PX, keep: str = ""
             ) -> tuple[Any, Callable[[Sequence[tuple[Any, ...]], str], None]]:
    """The rail and the region it opens into: that region, and a call that draws the rail
    again from other `entries` where it stands.

    `entries` are `(key, catalog key)` with an optional third element for a hint's key
    and a fourth for a mark drawn before the name, or `(GROUP, name)` for a heading over the rows
    that follow it.

    Two regions, not loose rows: the rail scrolls on its own and the open page beside it
    holds still. Left loose, every row takes a grid track of its own and the region they
    are meant to sit beside gets whatever is left, which on a long index is a few lines.

    `keep` names a rail whose scroll position survives the view being drawn again, as
    every pick of a row does.
    """
    with ui.element("div").classes("w-full grow min-h-0 manager-sections-box"):
        frame = ui.element("div").classes("w-full grow min-h-0 manager-sections") \
            .style(f"--rail-w: {rail_px}px")
        with frame:
            rail = ui.element("div").classes("min-h-0 manager-section-rail")
            work = ui.element("div").classes("min-w-0 manager-section-work") \
                .style(f"order: {WORK_ORDER}")

    def draw(rows: Sequence[tuple[Any, ...]], open_key: str) -> None:
        if rail.is_deleted:
            return
        rail.clear()
        with rail:
            # Where the rows stack, the work sits under the open row: rows after it are
            # ordered past the work, which is ordered just after the rows before it.
            after = 0
            for entry in rows:
                key, label = entry[0], entry[1]
                hint = entry[2] if len(entry) > 2 else ""
                mark = entry[3] if len(entry) > 3 else None
                if key is GROUP:
                    _rail_group(t(label), mark, order=after)
                    continue
                _rail_row(str(key), t(label), str(key) == open_key, on_pick, t(hint), mark,
                          order=after)
                if str(key) == open_key:
                    after = AFTER_WORK_ORDER

    draw(entries, current)
    ui.run_javascript(_OPEN_ROW_IN_VIEW_JS)
    if keep:
        ui.run_javascript(KEEP_RAIL_SCROLL_JS % {"key": json.dumps(keep),
                                                 "id": json.dumps(f"c{rail.id}")})
    return work, draw


def _rail_group(label: str, mark: Callable[[], None] | None = None, *,
                order: int = 0) -> None:
    """A heading over the run of rows that follows it, with a corner for a mark.

    The mark is drawn over the heading rather than beside it, so a group reads at the
    same place on the line whether or not something under it wants attention.
    """
    if mark is None:
        ui.label(label).classes("manager-group manager-rail-group").style(f"order: {order}")
        return
    with ui.element("div").classes("manager-group manager-rail-group") \
            .style(f"order: {order}"):
        ui.label(label)
        mark()


def _rail_row(key: str, label: str, open_now: bool,
              on_pick: Callable[[str], Any], hint: str = "",
              mark: Callable[[], None] | None = None, *, order: int = 0) -> None:
    """One destination's name, which is both the rail entry and the accordion header.

    The chevron says the row opens, which is a fact about the control rather than a
    label for the destination. Without it the stacked rows are words with no sign that
    any of them do anything.

    `mark` draws in the row's own corner, over the name rather than beside it: a state
    about the destination must not move the word that names it, and the gutter it sits
    in is reserved on every row whether one is drawn or not.
    """
    row = ui.row().classes("items-stretch gap-0 no-wrap manager-section-row") \
        .style(f"order: {order}")
    if open_now:
        row.classes(add="manager-section-on")
    if hint:
        row.tooltip(hint)
    with row:
        # `no-wrap`, or a long name is two lines and one taller row. The label ellipses
        # instead, which is what `truncate` was already there to do.
        with ui.row().classes("items-center grow min-w-0 no-wrap manager-section-hit"):
            ui.label(label).classes("truncate")
            if mark is not None:
                mark()
        with ui.row().classes("items-center manager-section-caret"):
            ui.icon("expand_more", size="18px")
    # The whole band, name and chevron alike - a header that answers on the word but
    # not on the arrow is a control with two rules to learn.
    row.on("click", lambda: on_pick(key))


def switch(value: bool | None, on_change: Callable[[Any], Any], *,
           disabled: bool = False, hint: str = "",
           on_control: Callable[[Any], Any] | None = None) -> Callable[[], None]:
    """Every binary value the user can set, drawn the same way. None draws it neither
    on nor off, and a click from there turns it on.

    With `wrap`, a one-line field is drawn as a box that grows a line at a time instead of
    cropping: a value that runs long, such as a command, stays readable. Enter still
    leaves it, so it holds one logical line.

    `on_control` is handed the control once it exists, for a caller that needs it back -
    the wizard's own first-field focus and per-field refusals among them.
    """
    def draw() -> None:
        # Green, the same token a present chip takes: on means the same thing whether
        # the panel found it or the user set it, and the shape already says which.
        control = ui.switch(value=value, on_change=on_change) \
            .props("dense color=positive").classes("manager-fact-switch")
        if disabled:
            control.disable()
        if hint:
            control.tooltip(hint)
        if on_control is not None:
            on_control(control)

    return draw


class _GlyphToggle(ui.toggle):
    """A toggle whose options each carry a glyph, kept across the rebuild every `props()`
    and `classes()` call makes of the payload."""

    def __init__(self, options: Any, *, glyphs: Sequence[str], value: Any,
                 on_change: Callable[[Any], Any]) -> None:
        # Before `super().__init__`, which builds the payload for the first time.
        self.glyphs = list(glyphs)
        super().__init__(options, value=value, on_change=on_change)

    def _update_options(self) -> None:
        super()._update_options()
        for option, glyph in zip(self._props["options"], self.glyphs, strict=False):
            option["icon"] = glyph


def segmented(options: Any, value: Any, on_change: Callable[[Any], Any], *,
              glyphs: Sequence[str] = ()) -> ui.toggle:
    """A choice between a few named states that is one control with an edge and a
    filled segment for the one in force, wherever it is drawn.

    `glyphs` are drawn before the words, one to an option in order; the words stay, so
    the glyph is taught where it is met alone."""
    control = _GlyphToggle(options, glyphs=glyphs, value=value, on_change=on_change) \
        if glyphs else ui.toggle(options, value=value, on_change=on_change)
    return control.props("dense no-caps unelevated").classes("manager-segmented")


def choice_buttons(options: Any, value: Any, on_change: Callable[[Any], Any], *,
                   disabled: bool = False) -> Callable[[], None]:
    """`segmented` as a row's control: a choice of two or three whose names are short."""
    def draw() -> None:
        with ui.element("div").classes("manager-fact-edit"):
            control = segmented(options, value, on_change)
            if disabled:
                control.disable()

    return draw


def state(text: str, level: str, *, beside: str = "", hint: str = "",
          glyph: str = "") -> Callable[[], None]:
    """A state the panel found and the user cannot set, as a chip.

    The counterpart of the switch: a switch is a setting, a chip is a finding, and the
    shape is what says which. `level` is what the absence costs - `on`, `off`, `unknown`,
    `warn`, `bad`. `glyph` is drawn before the word, so the word teaches what the glyph
    means where it is met alone.
    """
    def draw() -> None:
        with ui.element("div").classes("manager-fact-edit"):
            if beside:
                ui.label(beside).classes("manager-fact-value truncate min-w-0") \
                    .tooltip(beside)
            if glyph:
                chip = ui.element("span").classes(
                    f"manager-tier manager-tier--{level} manager-tier--glyph")
                with chip:
                    ui.icon(glyph).classes("manager-tier-glyph")
                    ui.label(text)
            else:
                chip = ui.label(text).classes(f"manager-tier manager-tier--{level}")
            if hint:
                chip.tooltip(hint)

    return draw


def states(chips: Sequence[tuple[str, str, str]], note: str = "") -> Callable[[], None]:
    """A run of `state` chips as (text, level, hint), wrapping where the panel is narrow,
    with `note` as a help line under them."""
    def row() -> None:
        with ui.element("div").classes("manager-fact-edit flex-wrap"):
            for text, level, hint in chips:
                chip = ui.label(text).classes(f"manager-tier manager-tier--{level}")
                if hint:
                    chip.tooltip(hint)

    def draw() -> None:
        if not note:
            row()
            return
        with ui.element("div").classes("flex flex-col gap-1"):
            row()
            ui.label(note).classes("manager-help")

    return draw


def field(value: str, on_save: Callable[[str], Any], *, lines: int = 0,
          placeholder: str = "", disabled: bool = False,
          status: Callable[[Any], Any] | None = None,
          refuses: bool = False, left_empty: str = "",
          browse: Callable[[str], Any] | None = None,
          on_control: Callable[[Any], Any] | None = None,
          prefix: str = "", suffix: str = "", wrap: bool = False,
          held: dict[str, str] | None = None,
          default: str = "", whose: str = "", secret: bool = False) -> Callable[[], None]:
    """Free text the user can set.

    Written when you leave it or when you press Enter, and `debounce=0` is what makes
    that safe: nicegui's model is only current if every keystroke reaches it, and reading
    it without that gets whatever the last sync happened to hold. Several lines settle as
    you stop typing instead, because a paragraph has no natural moment of leaving - and
    Enter belongs to the text there rather than to finishing it.

    Enter blurs rather than saving on the spot, so there is one write path and not two
    that can both fire on the way out. Leaving is what saves; Enter is a way of leaving.

    `status` draws inside the control's own append slot rather than after it, which is
    where Quasar puts an input's state and where a reader already looks for one - a mark
    in the next grid column would read as a fact about the row, not about the value.

    With `refuses`, a one-line field's `on_save` answers why it refused the value, or
    empty when it saved, and the answer is the field's error.

    With `left_empty`, a one-line field's tooltip holds its value, else `left_empty`.

    `browse` makes it a path field. It takes the field's current value and answers the
    path chosen, or nothing where browsing was cancelled; a path it answers goes through
    `leave` exactly as typing and leaving does. A path field shows its end while it is not
    focused, copies its path, and says the whole path on hover over its text alone.

    `default` is the path in use while the field is empty: it is the field's text, quiet,
    and `whose` says whose it is on hover. Typing it, or choosing it, clears the setting.
    Any other path draws the dot before it and Reset after it.

    `prefix` and `suffix` are drawn around a one-line field's text without being part of
    it: what is typed, and what is saved, is the middle.

    `secret` hides what is typed and tells the browser not to offer to remember it.

    `on_control` is handed the control once it exists, for a caller that needs it back -
    the wizard's own first-field focus and per-field refusals among them.

    `held` is the text last saved, which leaving the field compares against. A caller that
    sets the control's value itself sets `held["was"]` with it, or typing that text again
    is taken for no change.
    """
    held = {} if held is None else held
    held["was"] = value or ""
    path = browse is not None
    marks = path and bool(default)
    tip = PathTip()
    parts: dict[str, Any] = {}

    def show(control: Any) -> None:
        """The dot, Reset, the quiet ink and the hover for what the field holds."""
        current = str(control.value or "")
        changed = marks and bool(current) and current != default
        parts["dot"].set_visibility(changed)
        parts["row"].classes(add=None if changed else "manager-field-row--default",
                             remove="manager-field-row--default" if changed else None)
        tip.say(t("manager.workbench.changed_from", value=default, whose=whose) if changed
                else whose)
        if parts.get("changed") is changed:
            return
        parts["changed"] = changed
        parts["end"].clear()
        if changed and not disabled:
            @on_page
            async def back() -> None:
                control.value = default
                await leave(control, default)
                path_ends(control)

            with parts["end"]:
                icon_action(t("word.reset"), back, icon=verbs.RESET,
                            hint=t("manager.workbench.back_to", value=default,
                                   whose=whose))()

    async def leave(control: ui.input, text: str) -> None:
        if marks:
            text = "" if text == default else text
            if not text and not control.is_deleted:
                control.value = default
                path_ends(control)
            show(control)
        if text == held["was"]:
            return
        said = on_save(text)
        if inspect.isawaitable(said):
            said = await said
        if not (refuses and said):
            held["was"] = text
        if refuses and not control.is_deleted:
            if said:
                control.props["error"] = True
                control.props["error-message"] = str(said)
            else:
                control.props(remove="error error-message")
            control.update()

    def draw() -> None:
        if not marks:
            draw_control()
            return
        with ui.row().classes("items-center gap-1 no-wrap manager-field-line"):
            with ui.row().classes("items-center gap-1 no-wrap manager-field-row") as row:
                parts["row"] = row
                parts["dot"] = ui.element("span").classes(
                    "manager-mark manager-mark--full manager-named-mark")
                control = draw_control()
            parts["end"] = ui.element("div").classes("manager-row-action")
        show(control)

    def draw_control() -> Any:
        with ui.element("div").classes("manager-fact-edit"):
            control: ui.textarea | ui.input
            if lines:
                control = ui.textarea(placeholder=placeholder)
                control.value = value
                control.props(f"dense borderless rows={lines} debounce=800") \
                    .classes("manager-edit-field")
                control.on_value_change(lambda: on_save(control.value or ""))
            else:
                control = (ui.textarea if wrap else ui.input)(placeholder=placeholder)
                control.value = value or (default if marks else "")
                control.props("dense borderless debounce=0" + (" autogrow" if wrap else "")) \
                    .classes("manager-edit-field")
                if secret:
                    control.props("type=password autocomplete=new-password")
                if prefix:
                    control.props["prefix"] = prefix
                if suffix:
                    control.props["suffix"] = suffix
                control.on("blur", lambda: leave(control, control.value or ""))
                control.on("keydown.enter.prevent" if wrap else "keydown.enter",
                           lambda: control.run_method("blur"))
                if path:
                    path_input(control, tip, empty_says=left_empty)
                elif left_empty:
                    with control:
                        ui.tooltip().bind_text_from(
                            control, "value", backward=lambda said: said or left_empty)
                if status is not None or browse is not None:
                    control.classes("manager-adorned")
                    with control.add_slot("append"):
                        if status is not None:
                            status(control)
                        if browse is not None:
                            @on_page
                            async def _browsed() -> None:
                                chosen = await browse(control.value or "")
                                if not chosen:
                                    return
                                if not control.is_deleted:
                                    control.value = chosen
                                await leave(control, chosen)
                                if not control.is_deleted:
                                    path_ends(control)

                            path_buttons(control, _browsed)
            if disabled:
                control.disable()
            if on_control is not None:
                on_control(control)
        return control

    setattr(draw, "hover_tip", tip if path else None)  # noqa: B010 - read by `_marked`
    return draw


class GridBar:
    """The bar above a grid: two rows, each with a left side and a right-aligned end."""

    def __init__(self) -> None:
        self.top = ui.row().classes("w-full items-center gap-2 no-wrap manager-bar-row")
        self.bottom = ui.row().classes(
            "w-full items-center gap-2 no-wrap manager-bar-row manager-bar-foot")


def grid_bar() -> GridBar:
    """Drawn into the caller's own panel row, which keeps its surface and spacing."""
    return GridBar()


def bar_end() -> Any:
    """The right-aligned end of a bar row. It shrinks; the stylesheet says what gives."""
    return ui.row().classes("items-center gap-2 no-wrap min-w-0 manager-bar-end")


def add_action(choices: Any, *, empty: bool, heading: str = "") -> Any:
    """The verb that fills a page, from one choice or several.

    Several collapse into one `+` with a menu rather than a row of identical glyphs:
    two of the same icon side by side say there are two of something without saying
    which is which.

    An icon once there are rows and the full words while there are none: the bar is
    scarce width on a page you already know, and a glyph is a guess on one you do not.
    """
    items = list(choices.items()) if isinstance(choices, dict) else list(choices)
    if len(items) == 1:
        label, act = items[0]
        if empty:
            return ui.button(label, icon="add", on_click=act) \
                .props("flat dense no-caps size=sm").classes("shrink-0 manager-action")
        return ui.button(icon="add", on_click=act) \
            .props("flat dense round size=sm").classes("shrink-0 manager-action") \
            .tooltip(label)

    button = ui.button(icon="add").props("flat dense round size=sm") \
        .classes("shrink-0 manager-action").tooltip(heading or t("word.add"))
    with button, ui.menu():
        if heading:
            ui.item_label(heading).props("header").classes("manager-menu-header")
        for label, act in items:
            ui.menu_item(label, act).classes("manager-menu-item")
    return button


def search(placeholder: str) -> Any:
    """The box above a grid that narrows what is in it.

    Not a fact row - it has no label column and it sets nothing - but it is a control,
    and six pages had written the same three props out by hand. Returned rather than
    drawn into a callable, because the caller wires the grid to it and needs the control
    itself.
    """
    return ui.input(placeholder=placeholder) \
        .props("dense outlined clearable clear-icon=close").classes("w-64")


def refresh(on_click: Callable[[], Any], hint: str) -> Any:
    """The round Refresh at the end of a grid's bar, after its count. Returned, so a
    caller whose read takes a while can disable it until the read is back."""
    return ui.button(icon=verbs.REFRESH, on_click=on_click) \
        .props("flat dense round size=sm").classes("shrink-0").tooltip(hint)


def icon_only(icon: str, on_click: Callable[[], Any], *, hint: str = "") -> Any:
    """A round button that is only its icon - Up in the folder picker. Returned, so a
    caller can `.disable()` it, the way Up does at a root."""
    control = ui.button(icon=icon, on_click=on_click).props("flat round dense")
    if hint:
        control.tooltip(hint)
    return control


def chip(label: str, on: bool, on_click: Callable[..., Any], hint: str) -> ui.button:
    """One choice in a row of them, words alone as a segmented option has them: the lit
    one is the choice in force. Returned, so a caller can light another."""
    return ui.button(text=label, color=None).props("no-caps unelevated dense") \
        .classes("remote-chip" + (" remote-chip--on" if on else "")) \
        .on("click", on_click).tooltip(hint)


def open_row(label: str, on_click: Callable[..., Any]) -> ui.button:
    """The chevron at the end of a tappable row, which opens what the row stands for. Its
    tap stops here: the row's own tap does something else."""
    return ui.button(icon="chevron_right").props(
        f'flat round dense aria-label="{html.escape(label)}"') \
        .classes("remote-row-open").on("click.stop", on_click).tooltip(label)


def path_text(path: str, *, icon: str = "", classes: str = "") -> Any:
    """A path drawn as text: its end in view with the start given up to an ellipsis, the
    whole path as the tooltip, and Copy path after it. Returns the row.

    The path reads right to left so the start is what gets cut; the left-to-right marks
    around it keep its slashes and colon where they were typed.
    """
    ltr = "\u200e"
    with ui.row().classes(f"items-center gap-1 no-wrap min-w-0 manager-path-text {classes}"
                          .strip()) as row:
        if icon:
            ui.icon(icon).classes("manager-path-mark")
        ui.label(f"{ltr}{path}{ltr}").classes("manager-path-end min-w-0").tooltip(path)
        copy_text(path)
    return row


def path_value(path: str, *, icon: str = "") -> Callable[[], None]:
    """`path_text` as the value of a `facts` row, which draws what it is handed."""
    def draw() -> None:
        path_text(path, icon=icon)
    return draw


def selection_actions(hint: str) -> tuple[Any, Any]:
    """A grid bar's `more_vert` for the selected rows, and the menu it opens. Hidden until
    the caller shows it, which is when some rows are selected."""
    control = ui.button(icon=verbs.MORE).props("flat round dense").tooltip(hint)
    with control:
        menu = ui.menu().props("no-parent-event")
    control.set_visibility(False)
    return control, menu


def edit_selected(label: str, on_click: Callable[[], Any]) -> Callable[[int], None]:
    """The way into the panel for the rows ticked, where the page shows the list or the
    panel but not both. Hidden by the stylesheet wherever they sit side by side.

    `label` is the catalog key of its plural text. Answers the function that sets it,
    given how many are ticked: it shows for two or more and hides otherwise.
    """
    button = ui.button(t(label, count=2), icon=verbs.EDIT, on_click=on_click) \
        .props("flat dense no-caps size=sm") \
        .classes("manager-action manager-edit-selected")
    button.set_visibility(False)

    def update(ticked: int) -> None:
        button.text = t(label, count=ticked)
        button.set_visibility(ticked >= 2)

    return update


def selection_menu(hint: str, rows: Callable[[], list[Any]],
                   acts: Callable[[list[Any]], Sequence[Verb]],
                   clear: Callable[[], Any],
                   ready: Callable[[], Awaitable[Any]] | None = None) -> Callable[[bool], None]:
    """A grid bar's `more_vert` for the selected rows, drawing the subject's own list.

    `acts` is given the rows chosen when the menu opens and answers what can be done to
    them, the list the row menu and the panel draw; `for_selection` and the way out
    (*Clear selection*, under a rule of its own) are added here. `ready` is awaited first,
    for what `acts` draws from and cannot read without waiting. Answers the function that
    shows the button, given whether any row is selected.
    """
    control, menu = selection_actions(hint)

    async def fill() -> None:
        if ready is not None:
            await ready()
        chosen = rows()
        menu.clear()
        with menu:
            shown = for_selection(acts(chosen), len(chosen))
            verb_entries(shown, menu.close)
            if shown:
                ui.separator()
            _verb_entry(Verb(t("word.clear_selection"), clear))
        menu.open()

    control.on_click(fill)
    return control.set_visibility


def trouble_mark(reason: str = "") -> Callable[[], None]:
    """The mark that says something under here is misconfigured.

    A glyph rather than the count the nav badge carries: a heading and a rail row are
    signposts, and what is actually wrong is on the page they lead to.
    """
    def draw() -> None:
        icon = ui.icon("error", size="14px").classes("manager-trouble-mark")
        if reason:
            icon.tooltip(reason)

    return draw


def quiet_mark(word: str) -> Callable[[], None]:
    """A word in the corner a mark would take, for a row that is not in trouble but is
    not doing anything."""
    def draw() -> None:
        ui.label(word).classes("manager-quiet-mark")

    return draw


# A state a value is in, as the mark and the color that say so. `unset` draws nothing:
# an optional setting left blank is a choice, and a mark on every empty field is a page
# full of marks that mean nothing.
# Blank where blank is not allowed. Its own state because `unset` deliberately draws
# nothing - an optional path left empty is a choice - and a feature's requirement left
# empty is the thing that broke it.
REQUIRED = "required"
# A program that is there and does not run.
UNUSABLE = "unusable"

_VALUE_STATES = {
    REQUIRED: ("cancel", "negative"),
    UNUSABLE: ("error", "warning"),
    path_state.OK: ("check_circle", "positive"),
    path_state.MISSING: ("cancel", "negative"),
    path_state.WRONG_KIND: ("cancel", "negative"),
    path_state.NOT_CONNECTED: ("cancel", "negative"),
    path_state.NOT_EXECUTABLE: ("error", "warning"),
    path_state.NOT_ANSWERING: ("error", "warning"),
}


def value_state(state: str, reason: str = "") -> Callable[[Any], None]:
    """The mark that says whether a value is good, for `field(status=...)`.

    A tick and a cross rather than a chip: this is about the text in the box beside it,
    and a chip in the append slot would be a second control where a mark is wanted.
    """
    def draw(_control: Any) -> None:
        pair = _VALUE_STATES.get(state)
        if pair is None:
            return
        icon, color = pair
        mark = ui.icon(icon).classes(f"text-{color} manager-value-state")
        if reason:
            mark.tooltip(reason)

    setattr(draw, "draws", state in _VALUE_STATES)  # noqa: B010 - read by `drawn`
    return draw


def drawn(status: Callable[[Any], None] | None) -> bool:
    """Whether `status` puts a mark in the append slot; a state with no mark draws nothing."""
    return status is not None and getattr(status, "draws", True)


class DescribedSelect(dropdown.Select):
    """A picker whose options each carry a line saying what they are for.

    `describes` maps an option's label to its line. The slot reads it as `opt.help`,
    and `manager-menu-tip` is what keeps the tooltip visible while the menu is open.
    """

    SLOT = """
        <q-item v-bind="props.itemProps">
          <q-item-section>
            <q-item-label>{{ props.opt.label }}</q-item-label>
          </q-item-section>
          <q-tooltip v-if="props.opt.help" class="manager-menu-tip"
                     anchor="center right" self="center left">
            {{ props.opt.help }}
          </q-tooltip>
        </q-item>
    """

    def __init__(self, options: Any, *, value: Any, label: str | None = None,
                 describes: dict[str, str] | None = None,
                 on_change: Callable[[Any], Any] | None = None,
                 look: str = dropdown.FIELD) -> None:
        # Before `super().__init__`, which builds the payload for the first time.
        self.describes: dict[str, str] = dict(describes or {})
        super().__init__(options, value=value, label=label, on_change=on_change, look=look)
        self.add_slot("option", self.SLOT)

    def _update_options(self) -> None:
        super()._update_options()
        for option in self._props["options"]:
            option["help"] = self.describes.get(str(option.get("label") or ""), "")

    def describe_options(self, describes: dict[str, str]) -> None:
        """The lines, and the rebuild that puts them on the payload."""
        self.describes = dict(describes)
        self.update()


class CountedSelect(dropdown.Select):
    """Several from a list, each option saying how many games hold it.

    `counts` maps a value to its count. The chips of what is chosen carry the value
    alone.
    """

    SLOT = """
        <q-item v-bind="props.itemProps">
          <q-item-section>
            <q-item-label>{{ props.opt.label }}</q-item-label>
          </q-item-section>
          <q-item-section side v-if="props.opt.count != null">
            <q-item-label caption>{{ props.opt.count }}</q-item-label>
          </q-item-section>
        </q-item>
    """

    def __init__(self, options: Sequence[str], counts: dict[str, int], *,
                 value: Sequence[str], typed: bool = False) -> None:
        # Before `super().__init__`, which builds the payload for the first time.
        self.counts = dict(counts)
        super().__init__(list(options), value=list(value), multiple=True,
                         with_input=typed)
        self.add_slot("option", self.SLOT)

    def _update_options(self) -> None:
        super()._update_options()
        for option in self._props["options"]:
            option["count"] = self.counts.get(str(option.get("label") or ""))


class MarkedSelect(dropdown.Select):
    """One from a list, each option with a word in its trailing slot where `marks` has
    one, and `heading` above the option `heading_at`, where the list changes kind."""

    SLOT = """
        <q-item-label v-if="props.opt.heading" header class="manager-menu-header">
          {{ props.opt.heading }}</q-item-label>
        <q-item v-bind="props.itemProps">
          <q-item-section>
            <q-item-label>{{ props.opt.label }}</q-item-label>
          </q-item-section>
          <q-item-section side v-if="props.opt.mark">
            <q-item-label caption>{{ props.opt.mark }}</q-item-label>
          </q-item-section>
        </q-item>
    """

    def __init__(self, options: dict[str, str], *, value: str | None,
                 marks: dict[str, str] | None = None, heading: str = "",
                 heading_at: str | None = None,
                 look: str = dropdown.OUTLINED) -> None:
        # Before `super().__init__`, which builds the payload for the first time.
        self.marks = dict(marks or {})
        self.heading = heading
        self.heading_at = heading_at
        super().__init__(options, value=value, look=look)
        self.add_slot("option", self.SLOT)

    def _update_options(self) -> None:
        super()._update_options()
        for option in self._props["options"]:
            chosen = self._values[option["value"]]
            option["mark"] = self.marks.get(chosen, "")
            option["heading"] = self.heading if chosen == self.heading_at else ""


class ReasonedSelect(dropdown.Select):
    """One from a list, where `reasons` maps an option's value to why it cannot be picked
    now: it stays in the list, greyed, with that line under its label."""

    SLOT = """
        <q-item v-bind="props.itemProps">
          <q-item-section>
            <q-item-label>{{ props.opt.label }}</q-item-label>
            <q-item-label caption v-if="props.opt.reason" class="manager-cell-made">
              {{ props.opt.reason }}</q-item-label>
          </q-item-section>
        </q-item>
    """

    def __init__(self, options: dict[str, str], *, value: str | None,
                 reasons: dict[str, str], on_change: Callable[[Any], Any] | None = None,
                 look: str = dropdown.FIELD) -> None:
        # Before `super().__init__`, which builds the payload for the first time.
        self.reasons = dict(reasons)
        super().__init__(options, value=value, on_change=on_change, look=look)
        self.add_slot("option", self.SLOT)

    def _update_options(self) -> None:
        super()._update_options()
        for option in self._props["options"]:
            reason = self.reasons.get(str(self._values[option["value"]]), "")
            option.update(reason=reason, disable=bool(reason))


class ExplainedSelect(dropdown.Select):
    """One from a list where each option says what choosing it does, under its label.

    `says` maps an option's value to that sentence. `unavailable` maps a value to why it
    cannot be picked now: it stays in the list, greyed, with the reason in the sentence's
    place, or with no sentence where the reason is empty. `apart` is the value a rule is
    drawn above, for the one answer that is not a format like the rest.
    """

    SLOT = """
        <q-separator v-if="props.opt.rule" />
        <q-item v-bind="props.itemProps">
          <q-item-section>
            <q-item-label>{{ props.opt.label }}</q-item-label>
            <q-item-label caption v-if="props.opt.says" class="manager-cell-made">
              {{ props.opt.says }}</q-item-label>
          </q-item-section>
        </q-item>
    """

    def __init__(self, options: dict[str, str], *, value: str | None,
                 says: dict[str, str] | None = None,
                 unavailable: dict[str, str] | None = None, apart: str | None = None,
                 on_change: Callable[[Any], Any] | None = None,
                 look: str = dropdown.FIELD) -> None:
        # Before `super().__init__`, which builds the payload for the first time.
        self.says = dict(says or {})
        self.unavailable = dict(unavailable or {})
        self.apart = apart
        super().__init__(options, value=value, on_change=on_change, look=look)
        self.add_slot("option", self.SLOT)

    def _update_options(self) -> None:
        super()._update_options()
        for option in self._props["options"]:
            key = str(self._values[option["value"]])
            reason = self.unavailable.get(key, "")
            option.update(says=reason or self.says.get(key, ""),
                          disable=key in self.unavailable, rule=key == self.apart)


class GamePicker(dropdown.Select):
    """One game from the library, typed into, each option with its maker and year. A
    game in `held` is ticked and cannot be picked. `art` is each game's thumbnail, or None
    while the lists draw none."""

    LINES = """
            <q-item-label>{{ props.opt.label }}</q-item-label>
            <q-item-label caption v-if="props.opt.made" class="manager-cell-made">
              {{ props.opt.made }}</q-item-label>"""
    SLOT = f"""
        <q-item v-bind="props.itemProps">
          <q-item-section>
            {thumbnails.option_html(LINES, icons.GAMES)}
          </q-item-section>
          <q-item-section side class="manager-pick-held">
            <q-icon v-if="props.opt.held" name="check" class="manager-tick" />
          </q-item-section>
        </q-item>
    """

    def __init__(self, games: Sequence[dict[str, Any]], held: set[str], *,
                 label: str, art: dict[str, str] | None = None) -> None:
        # Before `super().__init__`, which builds the payload for the first time.
        self.made = {str(game["id"]): game_tables.made(game) for game in games}
        self.held = set(held)
        self.art = art
        super().__init__({str(game["id"]): str(game.get("name") or game["id"])
                          for game in games}, with_input=True, label=label,
                         look=dropdown.OUTLINED)
        self.add_slot("option", self.SLOT)

    def hold(self, held: set[str]) -> None:
        """Another set of games ticked and out of reach, with the picker left where it is."""
        self.held = set(held)
        self._update_options()
        self.update()

    def _update_options(self) -> None:
        super()._update_options()
        for option in self._props["options"]:
            game = str(self._values[option["value"]])
            option["made"] = self.made.get(game, "")
            option["held"] = option["disable"] = game in self.held
            if self.art is not None:
                option[thumbnails.FIELD] = self.art.get(game, "")


class SettingPicker(dropdown.Select):
    """One setting, typed into, each option with its area under it and a heading above
    the option in `headings` that starts a run. An option's `also` is its key, for
    `workbench.core._ADD_BOX`'s filter.

    A heading is an option of its own with an empty label, so anything typed filters it
    out: a heading left above a filtered list would claim a run that is no longer whole."""

    HEADING = "heading:"
    SLOT = """
        <q-item-label v-if="props.opt.heading" header class="manager-menu-header">
          {{ props.opt.heading }}</q-item-label>
        <q-item v-else v-bind="props.itemProps">
          <q-item-section>
            <q-item-label>{{ props.opt.label }}</q-item-label>
            <q-item-label caption v-if="props.opt.area" class="manager-cell-made">
              {{ props.opt.area }}</q-item-label>
          </q-item-section>
        </q-item>
    """

    def __init__(self, options: dict[str, str], *, areas: dict[str, str],
                 headings: dict[str, str], label: str) -> None:
        # Before `super().__init__`, which builds the payload for the first time.
        self.areas = dict(areas)
        self.headings = {}
        listed: dict[str, str] = {}
        for key, text in options.items():
            if key in headings:
                listed[self.HEADING + key] = ""
                self.headings[self.HEADING + key] = headings[key]
            listed[key] = text
        super().__init__(listed, with_input=True, label=label, look=dropdown.OUTLINED)
        self.add_slot("option", self.SLOT)

    def _update_options(self) -> None:
        super()._update_options()
        for option in self._props["options"]:
            key = str(self._values[option["value"]])
            if key in self.headings:
                option.update(heading=self.headings[key], disable=True)
            else:
                option.update(area=self.areas.get(key, ""), also=key)


def hint(control: Any, said: str) -> None:
    """A line under a field. The field must carry `bottom-slots`."""
    if said:
        control.props(f'hint="{said}"')
    else:
        control.props(remove="hint")


def browse_button(on_click: Callable[[], Any]) -> ui.button:
    """Browse, drawn in a field's append slot."""
    return ui.button(icon=verbs.BROWSE, on_click=on_click) \
        .props("flat round dense").tooltip(t("manager.folder_picker.browse"))


PATH_INPUT = "manager-path-input"


class PathTip:
    """What a field's hover says after its text: whose it is. `say` redraws the tooltip."""

    def __init__(self) -> None:
        self.whose = ""
        self.redraw: Callable[[], None] | None = None

    def say(self, whose: str) -> None:
        self.whose = whose
        if self.redraw is not None:
            self.redraw()


def native_id(control: ui.input) -> str:
    """The id of the browser input inside a path field, which Quasar gives the field's `for`."""
    return "pf" + str(control.id)


def path_input(control: ui.input, tip: PathTip | None = None, *,
               empty_says: str = "") -> PathTip:
    """Make `control` a path's input: its end showing while it is not focused, and a
    tooltip on the text alone.

    The tooltip targets the native input, not the field. Quasar draws the append slot
    inside the field, so a tooltip that is a child of it also fires over Copy path and
    Browse, and the buttons' own tips sit on top of it.
    """
    tip = tip or PathTip()
    native = native_id(control)
    control.props(f"for={native} input-class={PATH_INPUT}")
    with control:
        hover = ui.tooltip().props(f'target="#{native}" anchor="bottom left" self="top left"')

    def redraw() -> None:
        path = str(control.value or "")
        said = "\n".join(line for line in (path, tip.whose if path else "") if line) \
            or empty_says
        hover.text = said
        if said:
            hover.props(remove="no-parent-event")
        else:
            hover.props("no-parent-event")

    control.on_value_change(redraw)
    tip.redraw = redraw
    redraw()
    return tip


def value_hover(control: Any, tip: PathTip) -> None:
    """The hover of a picker that draws a mark: whose the value is, on the value alone.

    The row's own tooltip would cover the mark too, so the mark's reason would share its
    hover with a second tip.
    """
    with control:
        # A picker's html_id is its inner input; the field is the label that names it.
        hover = ui.tooltip().props(
            f'target="label[for={control.html_id}] .q-field__control-container" '
            'anchor="bottom left" self="top left"')

    def redraw() -> None:
        hover.text = tip.whose
        if tip.whose:
            hover.props(remove="no-parent-event")
        else:
            hover.props("no-parent-event")

    tip.redraw = redraw
    redraw()


def path_box(value: str, *, name: str) -> ui.input:
    """A path's input drawn as the Manager draws one, for a caller that makes its own
    append slot; `name` is its accessible name."""
    control = ui.input(value=value).props("outlined dense debounce=0") \
        .classes("grow min-w-0 manager-adorned")
    control.props["aria-label"] = name
    return control


def path_ends(control: ui.input) -> None:
    """Put the end of the path back in view after the value is set from here: the page
    script does it for what the browser changes."""
    ui.run_javascript(f"window.managerPathEnd && managerPathEnd('{native_id(control)}')")


def _copy_button(browser_text: str, text: Callable[[], str]) -> ui.button:
    """A Copy path button. `browser_text` is the JavaScript expression for the text, read
    in the browser at the click, which is the only moment the browser will copy."""
    from manager.kit import clipboard

    button = ui.button(icon=verbs.COPY).props("flat round dense").tooltip(t("word.copy_path"))
    button.props["aria-label"] = t("word.copy_path")
    button.on("click", lambda event: clipboard.copied(bool(event.args), text()),
              js_handler=clipboard.COPY_JS % browser_text)
    return button


def copy_path(control: ui.input) -> ui.button:
    """Copy path, drawn in a path field's append slot. It copies what the field holds."""
    button = _copy_button(f"document.getElementById('{native_id(control)}').value",
                          lambda: str(control.value or ""))
    button.bind_visibility_from(control, "value", backward=bool)
    return button


def copy_text(path: str) -> ui.button:
    """Copy path after a path drawn as text."""
    return _copy_button(json.dumps(path), lambda: path)


def path_buttons(control: ui.input, browsed: Callable[[], Any]) -> None:
    """Copy path, then Browse, into the append slot being drawn."""
    copy_path(control)
    browse_button(browsed)


def path_field(placeholder: str = "", *, wants: str, value: str = "",
               width: str = "w-96",
               on_checked: Callable[[str, str], str] | None = None,
               browse: Callable[[str], dict] | None = None,
               suffixes: tuple[str, ...] = (), opens_at: str = "") -> Any:
    """A path typed by hand, saying whether it is there. Returns the input.

    `wants` is what should be at the end of it - `dir`, `file` or `exe`, the words
    `path_state` answers in. The mark draws in the control's append slot, beside the
    text it is about, and an empty box draws none.

    `on_checked` returns the line to show under the field - what the path turned out to
    be. It goes in the hint, the row the error message uses, so a field with something
    to say and one without are the same height.

    `browse` draws Browse beside the field, whatever `wants` is. `suffixes` narrows a
    `file` field's picker to those extensions; empty means every file. `opens_at` is where
    the picker starts while the field is empty.

    `debounce=0` stays on the input and the wait goes on a timer: a debounce here leaves
    the value stale at the moment a button is pressed.
    """

    control = ui.input(placeholder=placeholder)
    control.value = value
    control.props("outlined dense debounce=0 bottom-slots").classes(f"{width} manager-adorned")
    with control.add_slot("append"):
        holder = ui.element("div").classes("manager-value-state")
        if browse is not None:
            from manager.kit import folder_picker

            @on_page
            async def _browsed() -> None:
                chosen = await folder_picker.pick_path(
                    browse, control.value or opens_at, kind=wants, suffixes=suffixes)
                if chosen:
                    control.value = chosen
                    path_ends(control)

            path_buttons(control, _browsed)
        else:
            copy_path(control)
    path_input(control)
    seen: dict[str, Any] = {"was": object()}

    async def look() -> None:
        said = str(control.value or "").strip()
        if said == seen["was"]:
            return
        seen["was"] = said
        found = await offload.io(path_state.look, said, wants)
        holder.clear()
        with holder:
            value_state(found.state, found.reason)(control)
        if on_checked is not None:
            hint(control, on_checked(found.state, said))

    # One timer comparing against what it last asked about, rather than one restarted
    # per keystroke: a cancelled one-shot leaves an element behind and the restarts stop
    # arriving, which shows up as a mark that answers the first path and no other.
    ui.timer(0.3, look)
    return control


def select(options: Any, value: str, on_change: Callable[[Any], Any], *,
           disabled: bool = False,
           describes: dict[str, str] | None = None,
           unavailable: dict[str, str] | None = None,
           status: Callable[[Any], Any] | None = None,
           on_control: Callable[[Any], Any] | None = None) -> Callable[[], None]:
    """A list to pick from, where the reader already knows what the names mean, or
    `describes` says, by label, on each option.

    `unavailable` maps an option's value to why it cannot be picked now; it stays in the
    list, greyed, with the reason under it. It cannot be combined with `describes`.

    Where the label of each option is itself the thing being decided, the set goes on
    screen whole as radios instead - a closed control makes the reader open it to
    compare.

    `on_control` is handed the control once it exists, for a caller that needs it back -
    the wizard's own first-field focus and per-field refusals among them.
    """
    if describes and unavailable:
        raise ValueError("select() takes `describes` or `unavailable`, not both")
    tip = PathTip() if drawn(status) else None

    def draw() -> None:
        with ui.element("div").classes("manager-fact-edit"):
            if describes:
                control: dropdown.Select = DescribedSelect(
                    options, value=value, describes=describes, on_change=on_change)
            elif unavailable:
                control = ReasonedSelect(options, value=value, reasons=unavailable,
                                         on_change=on_change)
            else:
                control = dropdown.Select(options, value=value, on_change=on_change)
            if status is not None:
                with control.add_slot("append"):
                    status(control)
            if tip is not None:
                value_hover(control, tip)
            if disabled:
                control.disable()
            if on_control is not None:
                on_control(control)

    setattr(draw, "hover_tip", tip)  # noqa: B010 - read by `_marked`
    return draw


def multi_select(options: Any, value: Sequence[str], on_change: Callable[[Any], Any], *,
                 disabled: bool = False, typed: bool = False, placeholder: str = "",
                 on_control: Callable[[Any], Any] | None = None) -> Callable[[], None]:
    """Several from a list, where the row is a choice rather than a fact.

    Closed rather than a column of checkboxes: the set here is as long as whatever it is
    over - the systems in somebody's old library - and a control that grows down the
    dialog would push what it is for off the screen. Everything selected shows, so what
    is chosen is still readable without opening it.

    `typed` lets what is typed narrow the list, for one too long to scroll, and
    `placeholder` is what it says while nothing is chosen.

    `on_control` is handed the control once it exists, for a caller that needs it back -
    the wizard's own first-field focus and per-field refusals among them.
    """
    def draw() -> None:
        with ui.element("div").classes("manager-fact-edit"):
            control = dropdown.Select(options, value=list(value or []), multiple=True,
                                      on_change=on_change, with_input=typed)
            if typed:
                def clear_filter(_event: Any = None) -> None:
                    control.run_method("updateInputValue", "", True)

                control.on_value_change(clear_filter)
                control.on("blur", clear_filter)
            if placeholder:
                def say_empty() -> None:
                    control.props["placeholder"] = "" if control.value else placeholder
                    control.update()

                say_empty()
                control.on_value_change(say_empty)
            if disabled:
                control.disable()
            if on_control is not None:
                on_control(control)

    return draw


def open_on_choices() -> dict[str, str]:
    """What `behavior.startup_collection` can say besides a collection, by stored value."""
    return {LAST_COLLECTION: t("manager.workbench.open_on.last_collection"),
            "": t("manager.workbench.open_on.all_games")}


class DividedSelect(dropdown.Select):
    """One from a list whose first `apart` options are not like the rest, a rule under
    them."""

    SLOT = """
        <q-item v-bind="props.itemProps">
          <q-item-section>
            <q-item-label>{{ props.opt.label }}</q-item-label>
          </q-item-section>
        </q-item>
        <q-separator v-if="props.opt.rule" />
    """

    def __init__(self, options: Any, *, apart: int, **kwargs: Any) -> None:
        # Before `super().__init__`, which builds the payload for the first time.
        self.apart = apart
        super().__init__(options, **kwargs)
        self.add_slot("option", self.SLOT)

    def _update_options(self) -> None:
        super()._update_options()
        for at, option in enumerate(self._props["options"]):
            option["rule"] = at == self.apart - 1


def combo(value: str, options: Any, on_change: Callable[[Any], Any], *,
          disabled: bool = False, status: Callable[[Any], Any] | None = None,
          placeholder: str = "", clearable: bool = True,
          first: dict[str, str] | None = None) -> Callable[[], None]:
    """A list to pick from that can also be typed into. Not `clearable` where the row
    has a Clear of its own, and never where `first` names the empty choice. `first` are
    choices that are not part of what `options` lists, set above it with a rule between.

    For a value something else can offer good answers to and still be wrong about - which
    installs are on the network, say. A closed select would leave a filtered network with
    no way to name a machine by hand; a plain field would make the ordinary case a typing
    exercise with a URL in it.
    """
    tip = PathTip()

    def draw() -> None:
        offered = {**(first or {}), **dict(options or {})}
        # What is stored is always one of the choices, even when nothing is offering it
        # now: a machine that is switched off has to still read as the choice that was
        # made, and a value outside the list is refused outright.
        if value and value not in offered:
            offered[value] = value
        # An empty value is a choice of its own where `first` names it.
        shown = value if value or "" in (first or {}) else None
        with ui.element("div").classes("manager-fact-edit"):
            kwargs: dict[str, Any] = {"value": shown, "on_change": on_change,
                                      "with_input": True, "new_value_mode": "add-unique"}
            control = (DividedSelect(offered, apart=len(first), **kwargs) if first
                       else dropdown.Select(offered, **kwargs))
            control.props("input-debounce=0"
                          + (" clearable clear-icon=close" if clearable and not first else ""))
            if placeholder:
                control.props(f'placeholder="{placeholder}"')
            control.classes("manager-edit-combo")
            if status is not None:
                with control.add_slot("append"):
                    status(control)
                if drawn(status):
                    value_hover(control, tip)
            if disabled:
                control.disable()

    setattr(draw, "hover_tip", tip if drawn(status) else None)  # noqa: B010 - read by `_marked`
    return draw


def swatch(color: str, on_pick: Callable[[str], Any], *,
           disabled: bool = False) -> Callable[[], None]:
    """A color the user can set, drawn as itself, `#RRGGBB` on hover."""
    def draw() -> None:
        with ui.element("div").classes("manager-fact-edit"):
            button = ui.button().props("flat round dense").classes("manager-swatch")
            with button:
                dot = ui.element("span").classes("manager-tag-dot") \
                    .style(f"background: {color}")
                tip = ui.tooltip(color)

                async def pick(event: Any) -> None:
                    chosen = str(event.color or "").upper()
                    dot.style(f"background: {chosen}")
                    tip.text = chosen
                    said = on_pick(chosen)
                    if inspect.isawaitable(said):
                        await said

                if not disabled:
                    ui.color_picker(on_pick=pick).set_color(color)
            if disabled:
                button.disable()

    return draw


def named_as(value: Any, named: dict[str, str]) -> str:
    """Of `named`, the one `value` is as a number; "" for any other."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ""
    return next((one for one in named if float(one) == number), "")


def named_number(value: Any, named: dict[str, str], on_pick: Callable[[Any], Any], *,
                 disabled: bool = False, whole: bool = True,
                 low: Any = None, high: Any = None, unit: str = "") -> Callable[[], None]:
    """A number some of whose values have names: the names and Custom to pick from, and
    the number itself beside Custom, followed by `unit`."""
    said = named_as(value, named)

    async def call(chosen: Any) -> None:
        answer = on_pick(chosen)
        if inspect.isawaitable(answer):
            await answer

    def draw() -> None:
        with ui.element("div").classes("manager-fact-edit"):
            picker = dropdown.Select({**named, "": t("word.custom")}, value=said)
            box = ui.number(value=None if said else value, format="%d" if whole else None,
                            min=low, max=high, suffix=unit or None) \
                .props("dense borderless").classes("manager-edit-field manager-edit-narrow")
            box.set_visibility(not said)

            written = [None if said else value]

            async def picked(event: Any) -> None:
                box.set_visibility(not event.value)
                if event.value:
                    written[0] = None
                    await call(event.value)
                else:
                    box.run_method("focus")

            async def typed() -> None:
                if box.value is None:
                    return
                number = int(box.value) if whole else float(box.value)
                if low is not None:
                    number = max(low, number)
                if high is not None:
                    number = min(high, number)
                box.set_value(number)
                if number != written[0]:
                    written[0] = number
                    await call(number)

            picker.on_value_change(picked)
            box.on("blur", typed)
            box.on("keydown.enter", typed)
            if disabled:
                picker.disable()
                box.disable()

    return draw


def number(value: Any, on_change: Callable[[Any], Any], *,
           disabled: bool = False, whole: bool = True,
           low: Any = None, high: Any = None, step: Any = None,
           placeholder: str = "", unit: str = "", hint: str = "",
           on_control: Callable[[Any], Any] | None = None,
           on_leave: bool = False) -> Callable[[], None]:
    """A number. Narrow, because a four-digit box in a full-width field says the value
    might be long.

    With `on_leave`, `on_change` is called when the field is left or Enter is pressed and
    not on each keystroke: a value that is checked when it is saved has not been typed
    yet at "8" on the way to "8005".

    `whole` because not every number is a count: a port is, and a theme's scale factor
    is not. Formatting a fraction as an integer does not round it on the way in - it
    shows a different value than the one that is stored.

    Bounds are the control's, not a check afterwards: a spinner that will not go past
    the limit says what the limit is without anybody being told off for passing it.

    `on_control` is handed the control once it exists, for a caller that needs it back -
    the wizard's own first-field focus and per-field refusals among them.
    """
    def draw() -> None:
        with ui.element("div").classes("manager-fact-edit"):
            control = ui.number(value=value if value != "" else None,
                                placeholder=placeholder or None,
                                format="%d" if whole else None,
                                min=low, max=high, step=step, suffix=unit or None,
                                on_change=None if on_leave else on_change) \
                .props("dense borderless debounce=0" if on_leave else "dense borderless") \
                .classes("manager-edit-field manager-edit-narrow")
            if on_leave:
                held = {"was": control.value}

                def leave() -> Any:
                    if control.value == held["was"]:
                        return None
                    held["was"] = control.value
                    return on_change(SimpleNamespace(value=control.value))

                control.on("blur", leave)
                control.on("keydown.enter", lambda: control.run_method("blur"))
            if placeholder:
                control.style(f"--blank: {len(placeholder) + 1}ch")
            if disabled:
                control.disable()
            if hint:
                control.tooltip(hint)
            if on_control is not None:
                on_control(control)

    return draw


def choice(options: Sequence[tuple[str, str, str]], value: str | None,
           on_change: Callable[[Any], Any], *,
           on_control: Callable[[Any], Any] | None = None) -> Callable[[], None]:
    """`options` is `(key, label, help)`. The control itself is `ui.radio`; every
    option's sentence follows below it, in the same order. No value, or "", draws none
    picked.

    `on_control` is handed the control once it exists, for a caller that needs it back -
    the wizard's own first-field focus and per-field refusals among them.
    """
    def draw() -> None:
        with ui.column().classes("manager-fact-edit gap-1"):
            control = ui.radio({key: label for key, label, _help in options},
                               value=value or None, on_change=on_change).props("dense")
            for _key, _label, help_text in options:
                if help_text:
                    ui.label(help_text).classes("manager-help")
            if on_control is not None:
                on_control(control)

    return draw


def action(label: str,
           # The event or nothing, which is NiceGUI's own Handler type: `js` emits a
           # result and this is what receives it.
           on_click: Callable[[Any], Any] | Callable[[], Any] | None = None, *,
           icon: str = "", inline: bool = False, danger: bool = False, hint: str = "",
           enabled: bool = True, js: str = "") -> Callable[[], None]:
    """A verb, which follows the value it acts on.

    Weight follows the target: an action on a field or a section takes `.manager-action`;
    one sitting beside a state takes `.manager-action--inline`, the same control at the
    chip's type scale.

    `js` is for the few acts a browser will only allow while it still believes a person
    just asked for them. The Manager is server-rendered, so an ordinary click goes to the
    server and comes back - and by then the click's own permission has expired. A handler
    given here runs in the browser at the moment of the click and `emit(...)`s its result,
    which `on_click` then receives.
    """
    def draw() -> None:
        classes = "manager-action manager-action--inline" if inline else "manager-action"
        control = ui.button(label, icon=icon or None,
                            on_click=None if js else on_click) \
            .props("flat dense no-caps size=sm").classes(classes)
        if danger:
            control.classes("manager-action manager-action--danger")
        if js:
            control.on("click", on_click, js_handler=js)
        if not enabled:
            control.disable()
        if hint:
            control.tooltip(hint)

    return draw


def link_action(label: str, *, to: str, icon: str = "", hint: str = "") -> Callable[[], None]:
    """An `action` that goes somewhere else in the Manager.

    Drawn as an anchor, so "open in a new tab", middle-click and copy-link-address keep
    working as they do on `link`, in the button's look.
    """
    def draw() -> None:
        control = ui.button(label, icon=icon or None) \
            .props("flat dense no-caps size=sm").classes("manager-action")
        control.props["href"] = to
        if hint:
            control.tooltip(hint)

    return draw


def split_action(label: str, on_click: Callable[[Any], Any] | None, *, icon: str,
                 js: str, hint: str, also: str, also_js: str, also_hint: str) -> Callable[[], None]:
    """One `action` with a second way of doing the same thing behind a caret.

    Both halves take `js`, as `action` does: the menu item is the click the browser's
    folder picker needs to see.
    """
    def draw() -> None:
        with ui.element("div").classes("manager-split"):
            action(label, on_click, icon=icon, hint=hint, js=js)()
            caret = ui.button(icon=verbs.PLACES).props("flat dense no-caps size=sm") \
                .classes("manager-action manager-split-caret")
            caret.tooltip(also_hint)
            caret.props["aria-label"] = also_hint
            with caret, ui.menu().props('anchor="bottom right" self="top right"'):
                ui.menu_item(also, auto_close=True).classes("manager-menu-item") \
                    .on("click", on_click, js_handler=also_js)

    return draw


def draft_button(label: str, on_click: Callable[[], Any], *, icon: str, hint: str = "",
                 primary: bool = False) -> ui.button:
    """Cancel or Save at the foot of a draft. Returned, for the caller to dim until the
    draft has something in it. `primary` is the filled one, Save."""
    control = ui.button(label, icon=icon, on_click=on_click)
    if primary:
        control.props("dense no-caps no-wrap unelevated size=sm")
    else:
        control.props("flat dense no-caps size=sm").classes("manager-action")
    if hint:
        control.tooltip(hint)
    return control


def remote_action(label: str, on_click: Callable[..., Any] | None = None, *, icon: str,
                  primary: bool = False, danger: bool = False, hint: str = "") -> ui.button:
    """A verb on the Remote, the width of its row. `primary` for the one a screen is for,
    `danger` for one that ends what the others are for."""
    control = ui.button(label, icon=icon, on_click=on_click)
    if primary:
        control = control.props("no-caps unelevated color=primary") \
            .classes("remote-action remote-action--primary")
    else:
        control = control.props("no-caps flat") \
            .classes("remote-action" + (" remote-action--danger" if danger else ""))
    if hint:
        control.tooltip(hint)
    return control


def remote_key(label: str, on_click: Callable[..., Any] | None = None, *, icon: str,
               hint: str, size: str = "large", hold: str = "",
               icon_after: bool = False) -> ui.button:
    """A key of the Remote's wheel. `size` is "large" (glyph over name), "page" (a short
    row) or "select" (round). `hold` names a held action: while a thumb stays on it the
    page's own script keeps the press renewed, and `on_click` is not used."""
    control = ui.button(label, icon=icon, on_click=on_click)
    props = "no-caps flat"
    if icon_after:
        # `icon-right` takes the glyph's name; as a bare flag Quasar tries to draw `true`.
        control.props(f"icon-right={icon}", remove="icon")
    if size != "page":
        props += " stack"
    control = control.props(props).classes(f"remote-key remote-key--{size}")
    if hold:
        control = control.props(f"data-hold-action={hold}")
    control.tooltip(hint)
    return control


def icon_action(label: str, on_click: Callable[[], Any], *, icon: str, hint: str = "",
                enabled: bool = True) -> Callable[[], None]:
    """A verb drawn as its icon alone, for one repeated down a column of rows. `label` is
    its name to a screen reader, and `hint` what it says on hover."""
    def draw() -> None:
        control = ui.button(icon=icon, on_click=on_click).props("flat dense round size=sm")
        control.props["aria-label"] = label
        if not enabled:
            control.disable()
        if hint:
            control.tooltip(hint)

    return draw


def menu_entry(label: str, on_click: Callable[..., Any] | None = None, *,
               mark: Callable[[], Any] | None = None,
               trail: Callable[[], Any] | None = None,
               classes: str = "", auto_close: bool = True, refused: str = "",
               under: str = "") -> Any:
    """An entry in a menu whose items share the leading slot: `mark` draws into it, and an
    entry without one indents to it so the labels line up. `trail` draws the state at the
    end. `under` is a line of detail below the label. `refused` dims it, with those words
    under it, and it does nothing."""
    if refused:
        on_click, auto_close = None, False
        classes = f"manager-menu-blocked {classes}"
    item = ui.menu_item(on_click=on_click, auto_close=auto_close) \
        .classes(f"manager-menu-item {classes}".strip())
    with item, ui.row().classes("items-center gap-2 no-wrap w-full"):
        if mark is None:
            ui.element("span").classes("manager-menu-mark")
        else:
            mark()
        with ui.column().classes("gap-0 grow min-w-0"):
            ui.label(label)
            if refused or under:
                ui.label(refused or under).classes("manager-menu-sub")
        if trail is not None:
            trail()
    return item


@dataclass(frozen=True)
class Verb:
    """One thing that can be done to a subject as a whole.

    Written once per subject and drawn in both places it is offered: the panel header's
    menu and the grid row's context menu. `run` absent is offered and refused, and
    `hint` says why. `choices` opens a second menu instead of acting. `to` makes it a
    link to that address instead, one outside the Manager opening a new tab and marked
    as leaving. `in_panel` false keeps it to the grid, for a verb whose panel home is a
    field. `icon` is for where it is also drawn as a button; a menu item takes none.
    `draw` stands in for an entry of its own: it draws several into the menu being built,
    given what closes that menu, for the one list that is a submenu with state of its own.
    `one` is a verb with no meaning over several rows: with several chosen it stays in the
    menu, dimmed and saying so (`for_selection`). `tip` is what a hover adds to the label.
    `block` is the extension an entry comes from: its entries follow core's, each block under
    a rule and its name. `block_mark` draws before that name, for an extension VPinFE did
    not ship. `group` names what an entry acts on: a rule is drawn where it changes.
    """
    label: str
    run: Callable[[], Any] | None = None
    danger: bool = False
    hint: str = ""
    choices: tuple[tuple[str, Callable[[], Any]], ...] = ()
    in_panel: bool = True
    icon: str = ""
    to: str = ""
    draw: Callable[[Callable[[], Any]], None] | None = None
    one: bool = False
    tip: str = ""
    block: str = ""
    block_mark: Callable[[], None] | None = None
    group: str = ""


def for_selection(entries: Sequence[Verb], count: int) -> list[Verb]:
    """The list for `count` rows chosen: a one-at-a-time verb is refused while there are
    several, so the menu keeps its shape as the selection grows."""
    if count < 2:
        return list(entries)
    return [replace(one, run=None, choices=(), to="", draw=None,
                    hint=t("word.not_for_several")) if one.one else one
            for one in entries]


def leaves(to: str) -> bool:
    """Whether an address is outside the Manager, which is anything not a path here."""
    return not to.startswith("/")


def _verb_entry(verb: Verb) -> None:
    live = verb.run is not None or bool(verb.choices) or bool(verb.to)
    classes = "manager-menu-item"
    if not live:
        classes += " manager-menu-blocked"
    elif verb.danger:
        classes += " manager-menu-danger"
    acts = live and not verb.choices and not verb.to
    item = ui.menu_item(on_click=verb.run if acts else None,
                        auto_close=live and not verb.choices).classes(classes)
    if verb.to:
        item.props["href"] = verb.to
        if leaves(verb.to):
            item.props["target"] = "_blank"
            item.props["rel"] = "noopener noreferrer"
    if verb.tip:
        item.tooltip(verb.tip)
    with item, ui.row().classes("items-center gap-2 no-wrap w-full"):
        with ui.column().classes("gap-0 grow min-w-0"):
            ui.label(verb.label)
            if verb.hint and not live:
                ui.label(verb.hint).classes("manager-menu-sub")
        if verb.choices:
            ui.icon(verbs.DRILL).classes("manager-menu-trail")
        elif verb.to and leaves(verb.to):
            ui.icon(verbs.OPEN_OUT).classes("manager-menu-trail")
    if verb.choices:
        with item, ui.menu().props('anchor="top end" self="top start"'):
            for label, run in verb.choices:
                ui.menu_item(label, run).classes("manager-menu-item")


def verb_entries(entries: Sequence[Verb], close: Callable[[], Any] | None = None) -> None:
    """Into the menu being built. In each block a rule where the group changes, then the
    destructive ones last, under a rule. Core's block is first and each extension's follows
    under its name. `close` shuts that menu, for the entries that draw their own."""
    shut = close or (lambda: None)
    blocks: dict[str, list[Verb]] = {"": []}
    marks: dict[str, Callable[[], None] | None] = {}
    for one in entries:
        blocks.setdefault(one.block, []).append(one)
        marks.setdefault(one.block, one.block_mark)
    for name, held in blocks.items():
        if name and held:
            ui.separator()
            mark = marks.get(name)
            if mark is None:
                ui.item_label(name).props("header").classes("manager-menu-header")
            else:
                with ui.item_label().props("header").classes(
                        "manager-menu-header manager-menu-header--owned"):
                    mark()
                    ui.label(name)
        safe = [one for one in held if not one.danger]
        risky = [one for one in held if one.danger]
        last: str | None = None
        for one in safe:
            if last is not None and one.group != last:
                ui.separator()
            last = one.group
            _draw_verb(one, shut)
        if safe and risky:
            ui.separator()
        for one in risky:
            _draw_verb(one, shut)


def _draw_verb(verb: Verb, close: Callable[[], Any]) -> None:
    if verb.draw is not None:
        verb.draw(close)
    else:
        _verb_entry(verb)


def verb_menu(menu: Any, name: str, entries: Sequence[Verb],
              tail: Sequence[Verb] = ()) -> None:
    """A grid row's context menu: the row's name, then what can be done to it. `tail` is
    what the person using the menu can do about themselves, last under a rule of its own."""
    menu.clear()
    if not entries and not tail:
        return
    with menu:
        if entries:
            ui.item_label(name).props("header").classes("manager-menu-header")
            ui.separator()
            verb_entries(entries, menu.close)
        if tail:
            if entries:
                ui.separator()
            verb_entries(tail, menu.close)
    # A menu is filled after the click opened it, and Quasar placed it for the content it
    # had then; one that grew would run off the bottom of the window.
    menu.run_method("updatePosition")


def menu_button(icon: str, hint: str) -> tuple[Any, Any]:
    """A round icon button and the menu it opens, for the caller to fill."""
    button = ui.button(icon=icon).props("flat dense round size=sm").tooltip(hint)
    with button:
        menu = ui.menu()
    return button, menu


def subject_menu(slot: Any, entries: Sequence[Verb]) -> None:
    """The panel header's menu, for the subject the panel is about."""
    slot.clear()
    shown = [one for one in entries if one.in_panel]
    if not shown:
        return
    with slot:
        _button, menu = menu_button(verbs.MORE, t("word.actions"))
        with menu:
            verb_entries(shown, menu.close)


# A plain left click on a rail entry or an in-place link navigates in place; anything else is
# the browser's. Captured on the document because the rows are rebuilt, and a listener per
# row would have to be re-attached every time one is. A page that draws `link(on_click=...)`
# runs it too, or the browser follows the address and takes the page's connection with it.
#
# A right click on a link that leaves the Manager is the browser's too (new tab, new window,
# copy address). A row's `ui.context_menu` hears it on the row, above the link, so it is
# stopped on the way down; the default is left alone, which is what opens the native menu.
NAV_CLICK = """
(() => {
  document.addEventListener('click', (event) => {
    const link = event.target.closest && event.target.closest(
      'a.manager-nav-row:not(.manager-nav-row--out), a.manager-link--inplace');
    if (!link) return;
    if (event.button !== 0 || event.metaKey || event.ctrlKey ||
        event.shiftKey || event.altKey) return;
    event.preventDefault();
  }, true);
  document.addEventListener('contextmenu', (event) => {
    if (event.target.closest && event.target.closest('a.manager-link-out')) {
      event.stopPropagation();
    }
  }, true);
})()
"""


def link(label: str, *, to: str, on_click: Callable[[], Any] | None = None,
         hint: str = "", classes: str = "", window: str = "") -> Callable[[], None]:
    """A link to somewhere else in the Manager.

    An anchor, never a click handler on a label. A destination has an address, so a row
    that only listens for a click hides that from the browser and kills "open in a new
    tab", middle-click and copy-link-address on it.

    With `on_click` the row also takes `manager-link--inplace`, which is what the page's
    click handler looks for: it stops the browser following the href on a plain click and
    leaves every modified one alone, so the fast path works and the address still means
    something. Without one the browser simply follows the href.

    `window` is the named window the address opens in, so a link in a tab of its own lands
    in the tab that already holds the Manager.

    Carries no icon; `link_out` is the one that does.
    """
    def draw() -> None:
        row = ui.link(label, target=to).classes(f"manager-link {classes}".strip())
        if window:
            row.props(f"target={window}")
        if on_click is not None:
            row.classes("manager-link--inplace")
            row.on("click", on_click)
        if hint:
            row.tooltip(hint)

    return draw


def link_tile(*, to: str, classes: str = "") -> ui.link:
    """An anchor that holds a picture and words, to be entered with `with`. The browser
    follows the address, which is how a game opens from outside the Games page."""
    return ui.link(target=to).classes(classes)


def menu_link(label: str, *, to: str) -> None:
    """A menu row that goes somewhere else in the Manager: an item's look, not accent."""
    row = ui.menu_item(label).classes("manager-menu-item")
    row.props["href"] = to


def tag_link(tag: str, color: str, *, to: str, mark: str = "",
             hint: str = "") -> Callable[[], None]:
    """A tag chip that goes somewhere: the chip's look, and an address like any link."""
    def draw() -> None:
        with ui.link(target=to).classes(tag_chips.chip_class(color)) as row:
            ui.element("span").classes(tag_chips.dot_class(color))
            ui.label(tag)
            if mark:
                ui.icon(mark)
        if hint:
            row.tooltip(hint)

    return draw


def link_out(label: str, *, to: str, hint: str = "", opener: bool = False,
             rel: str = "") -> Callable[[], None]:
    """A link that leaves the Manager, opening in a new tab and marked `open_in_new`.

    `label` is the thing being opened wherever the surface has one to give - a name
    already on the row reads better than a verb added beside it.

    A new tab has no opener unless `opener` asks for one, and only a tab with an opener can
    find the Manager's window by name from a link of its own. `rel` sets the link's `rel`
    outright, for an address the Manager did not choose.
    """
    def draw() -> None:
        row = ui.link(target=to, new_tab=True).classes("manager-link manager-link-out")
        if opener:
            row.props("rel=opener")
        elif rel:
            row.props["rel"] = rel
        with row:
            ui.label(label)
            ui.icon("open_in_new")
        if hint:
            row.tooltip(hint)

    return draw


def out(*, to: str, hint: str,
        on_click: Callable[[], Any] | None = None) -> Callable[[], None]:
    """The way out to somewhere else, as its marker alone. `hint` is required; `on_click`
    runs when the link is followed, without stopping the browser's own navigation."""
    def draw() -> None:
        row = ui.link(target=to, new_tab=True) \
            .classes("manager-link manager-link-out manager-link-out--mark")
        with row:
            ui.icon(verbs.OPEN_OUT)
        row.tooltip(hint)
        if on_click is not None:
            row.on("click.stop", on_click)

    return draw


def line(text: str, *, hint: str = "", classes: str = "manager-help") -> ui.label:
    """A line of words, ending in a mark that opens `hint` when there is one."""
    shown = ui.label(text).classes(classes)
    if not hint:
        return shown
    shown.classes("manager-detailed")
    with shown:
        mark = ui.icon(verbs.SAYS_MORE).props('tabindex=0 role=button aria-hidden=false')
        mark.props["aria-label"] = hint
        mark.on("click.stop", js_handler="() => {}")
        with mark, ui.menu().classes("manager-detailed"):
            ui.label(hint)
    ui.tooltip(hint).classes("manager-detailed").props["target"] = f"#{shown.html_id}"
    return shown


def note(text: str, hint: str = "") -> tuple[Any, Callable[[], None]]:
    """The sentence under a control that says what it does, and `hint` behind its mark.

    Written out rather than left to a tooltip, and only where the label cannot carry
    the meaning on its own: a config key's name says what it is called, not what
    turning it off costs. Help you have to already suspect you need is not help.
    """
    def draw() -> None:
        line(text, hint=hint)

    return (ASIDE, draw)


def disclosure(label: str) -> Any:
    """A label that opens onto more of what is already here. Used as a context."""
    built = ui.expansion(label).props("dense dense-toggle").classes("manager-disclosure")
    built.props["toggle-aria-label"] = label
    return built


def lede(text: str) -> tuple[Any, Callable[[], None]]:
    """The line under a heading that says what the group is for."""
    def draw() -> None:
        ui.label(text).classes("manager-help")

    return (LEDE, draw)


def intro(text: str, hint: str = "") -> tuple[Any, Callable[[], None]]:
    """What a whole page cannot say row by row, said once above the rows.

    The width of the panel, because it is not about any one control - which is the
    difference between this and `note`.
    """
    def draw() -> None:
        line(text, hint=hint, classes="manager-help manager-intro")

    return (FULL, draw)
