"""The cabinet drawn as its parts, and this device's screens dragged onto them.

One control wherever a screen is chosen for a part: the caller passes the screens, what is
on each part now and the acts it reports to, and the control writes nothing itself. A part
takes one screen and a screen sits on one part, so putting a screen where another is sends
that one to where this one came from. A part that can be drawn inside the playfield's window
holds `INSIDE` there, which is no screen and displaces nothing.

Without a cabinet the screens are drawn where the system arranges them, in their own shape,
each saying its part; a part goes onto a screen by dragging it there, or from either menu.

The picture also asks whether the playfield screen is rotated correctly, with a
test card the caller opens on that screen and three turns that add to the stored rotation.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from functools import partial
from typing import Any

from nicegui import ui

from common.i18n import t
from common.values import quarter_turn
from manager.kit import panel, verbs
from manager.kit.on_page import on_page
from manager.settings.screens import Screen

BACKGLASS = "backglass"
SCOREVIEW = "scoreview"
PLAYFIELD = "playfield"
TOPPER = "topper"
INSIDE = "inside"

# A screen's number, INSIDE, or None for no screen.
Placement = int | str | None

# The turns the panel offers: what each adds, its words and its icon. Around has no icon of
# its own, as in Media Rotation.
_TURNS = ((270, "manager.screens.turn_left", verbs.ROTATE_LEFT),
          (180, "manager.screens.turn_around", verbs.ROTATE_RIGHT),
          (90, "manager.screens.turn_right", verbs.ROTATE_RIGHT))
_WAYS = {270: "manager.screens.way_left", 180: "manager.screens.way_around",
         90: "manager.screens.way_right"}
# The chip a turned answer wears, and what it says on hover.
_TURNED = {90: ("manager.screens.turned_right", "manager.screens.turned_right.help"),
           180: ("manager.screens.turned_around", "manager.screens.turned_around.help"),
           270: ("manager.screens.turned_left", "manager.screens.turned_left.help")}

# Where the panel is: before the card is asked for, with it up, with it gone and no answer
# given, after it looked right.
_IDLE, _CHECKING, _ASKING, _DONE = "idle", "checking", "asking", "done"
# How often the page says the card is still wanted; `screen_card.HOLD` has to stay above it.
_HOLD_S = 2.0

# The tallest the desk picture grows, in pixels.
_DESK_HEIGHT = 200

# A strip this much wider than it is tall is a DMD, and nothing else is.
_STRIP = 2.5

_CARRIES = "vpinfe-screen:"
_DRAG_END = "(e) => e.currentTarget.classList.remove('manager-screen-dragging')"
# Stopped here, or the page's own drop handler reads it as files and says nothing was
# uploaded.
_DRAG_OVER = ("(e) => { if (!Array.from(e.dataTransfer.types || []).includes('text/plain')) "
              "return; e.preventDefault(); e.stopPropagation(); "
              "e.currentTarget.classList.add('manager-drop-hot'); }")
_DRAG_LEAVE = ("(e) => { if (!e.currentTarget.contains(e.relatedTarget)) "
               "e.currentTarget.classList.remove('manager-drop-hot'); }")
_DROP = ("(e) => { e.preventDefault(); e.stopPropagation(); "
         "e.currentTarget.classList.remove('manager-drop-hot'); "
         "const held = e.dataTransfer.getData('text/plain'); "
         f"if (held.startsWith('{_CARRIES}')) emit(Number(held.slice({len(_CARRIES)}))); }}")


_PART = "vpinfe-part:"
_DROP_PART = ("(e) => { e.preventDefault(); e.stopPropagation(); "
              "e.currentTarget.classList.remove('manager-drop-hot'); "
              "const held = e.dataTransfer.getData('text/plain'); "
              f"if (held.startsWith('{_PART}')) emit(held.slice({len(_PART)})); }}")


def _drag_part(key: str) -> str:
    return (f"(e) => {{ e.dataTransfer.setData('text/plain', '{_PART}{key}'); "
            "e.dataTransfer.effectAllowed = 'move'; "
            "e.currentTarget.classList.add('manager-screen-dragging'); }")


def _drag_start(number: int) -> str:
    return (f"(e) => {{ e.dataTransfer.setData('text/plain', '{_CARRIES}{number}'); "
            "e.dataTransfer.effectAllowed = 'move'; "
            "e.currentTarget.classList.add('manager-screen-dragging'); }")


@dataclass(frozen=True)
class Part:
    key: str
    label: str
    hint: str = ""
    # Whether the menu offers to draw it inside the playfield's window.
    inside: bool = False


def vpinfe_parts() -> tuple[Part, ...]:
    """The four parts VPinFE opens a window for. A topper is opened only where it has a
    screen."""
    return (Part(TOPPER, t("manager.screens.topper")),
            Part(BACKGLASS, t("manager.screens.backglass")),
            Part(SCOREVIEW, t("manager.screens.scoreview"),
                 t("manager.screens.scoreview.help")),
            Part(PLAYFIELD, t("manager.screens.playfield")))


def vpx_parts() -> tuple[Part, ...]:
    """Visual Pinball's: VPinFE's four, each but the playfield able to be drawn inside the
    playfield."""
    return (Part(TOPPER, t("manager.screens.topper"), inside=True),
            Part(BACKGLASS, t("manager.screens.backglass"), inside=True),
            Part(SCOREVIEW, t("manager.screens.scoreview"),
                 t("manager.screens.scoreview.help"), inside=True),
            Part(PLAYFIELD, t("manager.screens.playfield")))


def placed(on: Mapping[str, Placement], part: str,
           number: Placement) -> dict[str, Placement]:
    """`number` on `part`, and what `part` held on the part `number` came from."""
    after = dict(on)
    after[part] = number
    if isinstance(number, int):
        for other, held in on.items():
            if other != part and held == number:
                after[other] = on.get(part)
    return after


def listed(labels: Sequence[str]) -> str:
    """The labels as a person says them: "A", "A and B", "A, B and C"."""
    if len(labels) < 2:
        return "".join(labels)
    return t("manager.screens.and_last", most=", ".join(labels[:-1]), last=labels[-1])


def changes(before: Mapping[str, Placement],
            after: Mapping[str, Placement]) -> dict[str, Placement]:
    return {part: number for part, number in after.items() if before.get(part) != number}


def turn(degrees: int, screen: Screen | None) -> dict[str, str]:
    """The playfield's `orientation` and `rotation` for a turn of `degrees` on this screen."""
    tall = screen is not None and screen.height > screen.width
    sideways = degrees in (90, 270)
    return {"orientation": "portrait" if tall != sideways else "landscape",
            "rotation": str(degrees)}


def guess(screens: Sequence[Screen]) -> dict[str, int]:
    """A part for each screen whose size tells it from the rest, and no part for the others.

    A strip is the Score View, the biggest of what is left the Playfield, the next the
    Backglass and the one after that the Topper. Two screens of one size are not told apart
    by their size, so neither is placed, and a lone screen is the Playfield.
    """
    if len(screens) == 1:
        return {PLAYFIELD: screens[0].number}
    sizes = [(screen.width, screen.height) for screen in screens]
    alone = [screen for screen in screens if sizes.count((screen.width, screen.height)) == 1]
    strips = [screen for screen in alone if screen.width >= _STRIP * screen.height]
    found: dict[str, int] = {}
    if len(strips) == 1:
        found[SCOREVIEW] = strips[0].number
    rest = sorted((screen for screen in alone if screen not in strips),
                  key=lambda screen: screen.width * screen.height, reverse=True)
    for part, screen in zip((PLAYFIELD, BACKGLASS, TOPPER), rest, strict=False):
        found[part] = screen.number
    return found


def _mini(screen: Screen, *, big: bool = False) -> None:
    width = 54 if big else 28
    height = max(14 if big else 8, round(width * screen.height / screen.width))
    ui.label(str(screen.number)).classes("manager-screen-mini") \
        .style(f"width:{width}px;height:{height}px")


def _no_mini() -> None:
    ui.label("").classes("manager-screen-mini manager-screen-mini--none")


def _size_of(screen: Screen) -> None:
    ui.label(screen.size).classes("manager-menu-size")


def _said(words: str) -> None:
    ui.label(words).classes("manager-menu-size")


@dataclass(frozen=True)
class Test:
    """What the rotation check asks of the device. `open` puts the test card on a screen
    turned by the degrees and answers whether it is up; `turn` turns it; `close` takes it
    down; `hold` says it is still wanted, which a card left with no one holding it closes
    itself without. `apply` runs when the answer is Looks Right, to put the kept turn into
    effect on the windows that are open; it is not run by any other way out of the check."""
    open: Callable[[Screen, int], Awaitable[bool]]
    turn: Callable[[int], Awaitable[Any]]
    close: Callable[[], Awaitable[Any]]
    hold: Callable[[], Awaitable[Any]]
    apply: Callable[[], Awaitable[Any]] | None = None


class Picture:
    """The parts and the screens, drawn from what the caller says is where.

    `move` is told every part whose screen changed - two, when a screen swaps places - and
    answers whether the change was kept; the picture follows it only if it was. `show` runs
    Identify Screens, given the lines each screen's window shows by screen number: its part,
    then any parts drawn inside it, and none for a screen no part uses. `rotation` is the
    playfield's stored turn, which the small card on the playfield draws; `point` is told the
    turn the panel's buttons make and the playfield's screen and answers whether it was kept,
    and `test` is the card on the screen itself. The picture offers each only when it is
    given. `cabinet` chooses the cabinet drawing; without it the screens are drawn at a desk.

    `differs` names the parts held otherwise than the thing this picture is compared with,
    and `match` puts them right. `vpinfe_at` is the address of the page that holds what the
    picture is compared with: given, the picture says so while nothing differs, and says
    what differs, with `match`, once something does. `presets` opens the preset dialog,
    offered beside Identify Screens when it is given.
    """

    def __init__(self, screens: Sequence[Screen], on: Mapping[str, Placement], *,
                 move: Callable[[dict[str, Placement]], Awaitable[bool]],
                 parts: Sequence[Part] = (), rotation: Any = 0, cabinet: bool = True,
                 point: Callable[[int, Screen], Awaitable[bool]] | None = None,
                 test: Test | None = None,
                 show: Callable[[dict[int, list[str]]], Awaitable[Any]] | None = None,
                 differs: Sequence[str] = (),
                 match: Callable[[], Awaitable[Any]] | None = None,
                 vpinfe_at: str = "",
                 presets: Callable[[], Awaitable[Any]] | None = None) -> None:
        self.screens = list(screens)
        self.on = dict(on)
        self.differs = tuple(differs)
        self.match = match
        self.vpinfe_at = vpinfe_at
        self.parts = tuple(parts) or vpinfe_parts()
        self.rotation = quarter_turn(rotation)
        self.turn_known = rotation is not None
        self.cabinet = cabinet
        self.move = move
        self.point = point
        self.test = test
        self.show = show
        self.presets = presets
        self.showing = False
        self.phase = _IDLE
        self.opening = False
        self.turning = asyncio.Lock()
        self.answering = False
        self.container = ui.element("div").classes("w-full manager-screens-host")
        self.draw()

    def screen(self, number: Placement) -> Screen | None:
        return next((one for one in self.screens if one.number == number), None)

    def draw(self) -> None:
        if self.container.is_deleted:
            return
        self.container.clear()
        with self.container:
            with ui.element("div").classes("manager-screens-head"):
                ui.label(t("manager.screens.hint_cabinet" if self.cabinet
                           else "manager.screens.hint_desk")).classes("manager-help")
                if self.show is not None or self.presets is not None:
                    with ui.element("div").classes("manager-screens-head-actions"):
                        if self.show is not None:
                            self._identify()
                        if self.presets is not None:
                            panel.action(t("manager.presets.button"), self.presets,
                                         icon=verbs.TUNE, hint=t("manager.presets.button.help"))()
            with ui.element("div").classes(
                    "manager-screens" + ("" if self.cabinet else " manager-screens--desk")):
                if self.cabinet:
                    self._cabinet()
                else:
                    self._desk()
                with ui.column().classes("manager-screens-side gap-2"):
                    with ui.element("div").classes("manager-screens-tray"):
                        if self.cabinet:
                            for screen in self.screens:
                                self._card(screen)
                        else:
                            for part in self.parts:
                                self._row(part)
                        if not self.screens:
                            ui.label(t("manager.screens.none_found")).classes("manager-help")
                    if self._asks_rotation():
                        self._rotation()
                    self._differs()

    def _identify(self) -> None:
        panel.action(t("manager.screens.showing" if self.showing
                       else "manager.screens.identify"),
                     self._show, icon=verbs.SHOW_NUMBERS,
                     hint=t("manager.screens.identify.help"),
                     enabled=not self.showing and self.phase != _CHECKING
                     and bool(self.screens))()

    def _uses(self) -> dict[int, list[str]]:
        """What each screen's window says it is for: the part on it, then the parts drawn
        inside the playfield when it is the playfield's screen. A screen no part uses has
        none."""
        found: dict[int, list[str]] = {screen.number: [] for screen in self.screens}
        for part in self.parts:
            number = self.on.get(part.key)
            if isinstance(number, int) and number in found:
                found[number].append(part.label)
        host = self.on.get(PLAYFIELD)
        if isinstance(host, int) and host in found and self._inside():
            found[host].append(self._inside())
        return found

    def _inside(self) -> str:
        """The parts drawn inside the playfield, as "with Backglass inside", or nothing."""
        inside = [part.label for part in self.parts if self.on.get(part.key) == INSIDE]
        return t("manager.screens.with_inside", parts=listed(inside)) if inside else ""

    @on_page
    async def _show(self) -> None:
        if self.show is None:
            return
        self.showing = True
        self.draw()
        try:
            await self.show(self._uses())
        finally:
            self.showing = False
            self.draw()

    def _differs(self) -> None:
        said = [part.label for part in self.parts if part.key in self.differs]
        if not said:
            if self.vpinfe_at:
                self._sentence(t("manager.screens.same_as", where="\x00"),
                               t("manager.screens.vpinfe_displays"), "manager-screens-same")
            return
        with ui.element("div").classes("manager-screens-differs"):
            with ui.column().classes("gap-0 min-w-0 grow"):
                ui.label(t("manager.screens.different")).classes("manager-screens-differs-title")
                ui.label(", ".join(said)).classes("manager-help")
                if self.vpinfe_at:
                    self._sentence(t("manager.screens.vpinfe_at", where="\x00"),
                                   t("manager.screens.vpinfe_displays"), "manager-help")
            if self.match is not None:
                panel.action(t("manager.screens.match_vpinfe"), self._match,
                             icon=verbs.REVERT, hint=t("manager.screens.match_vpinfe.help"))()

    def _sentence(self, words: str, name: str, classes: str) -> None:
        """A sentence whose `where` is a link to VPinFE's page, drawn wherever the
        translation puts it."""
        before, _, after = words.partition("\x00")
        with ui.element("div").classes(f"manager-screens-sentence {classes}"):
            if before.strip():
                ui.label(before.strip())
            panel.link(name, to=self.vpinfe_at)()
            if after.strip():
                ui.label(after.strip())

    @on_page
    async def _match(self) -> None:
        if self.match is not None:
            await self.match()

    def _cabinet(self) -> None:
        with ui.element("div").classes("manager-cab"):
            for part in self.parts:
                self._part(part)

    def _part(self, part: Part) -> None:
        number = self.on.get(part.key)
        screen = self.screen(number)
        inside = number == INSIDE
        with ui.element("div").classes(f"manager-part manager-part--{part.key}"):
            with ui.element("div").classes("manager-part-slot-wrap"):
                slot = ui.element("div").classes(
                    "manager-part-slot" + (" manager-part-slot--filled" if screen else
                                           " manager-part-slot--inside" if inside else
                                           " manager-part-slot--missing" if number else ""))
                slot.on("dragover", js_handler=_DRAG_OVER)
                slot.on("dragleave", js_handler=_DRAG_LEAVE)
                slot.on("drop", lambda event, key=part.key: self._dropped(key, event),
                        js_handler=_DROP)
                if isinstance(number, int):
                    slot.props("draggable=true")
                    slot.on("dragstart", js_handler=_drag_start(number))
                    slot.on("dragend", js_handler=_DRAG_END)
                with slot:
                    self._slot_face(part, number, screen)
                    self._menu(part, number)
            name = ui.label(part.label).classes(
                "manager-part-name" + (" manager-part-name--differs"
                                       if part.key in self.differs else ""))
            if part.key in self.differs:
                name.tooltip(t("manager.screens.different"))
            elif part.hint:
                name.tooltip(part.hint)

    def _desk(self) -> None:
        """The screens where the system arranges them, each in its own shape."""
        with ui.element("div").classes("manager-desk"):
            if not self.screens:
                return
            left = min(screen.x for screen in self.screens)
            top = min(screen.y for screen in self.screens)
            wide = max(1, max(screen.x + screen.width for screen in self.screens) - left)
            high = max(1, max(screen.y + screen.height for screen in self.screens) - top)
            arrangement = ui.element("div").classes("manager-desk-arr").style(
                f"aspect-ratio:{wide} / {high};"
                f"width:min(100%, {round(_DESK_HEIGHT * wide / high)}px)")
            with arrangement:
                for screen in self.screens:
                    self._monitor(screen, ((screen.x - left) / wide, (screen.y - top) / high,
                                           screen.width / wide, screen.height / high))

    def _monitor(self, screen: Screen, box: tuple[float, float, float, float]) -> None:
        held = next((part for part in self.parts if self.on.get(part.key) == screen.number),
                    None)
        inside = self._inside() if held is not None and held.key == PLAYFIELD else ""
        monitor = ui.element("div").classes("manager-mon" + (" manager-mon--held" if held else ""))
        monitor.props("role=button tabindex=0 aria-haspopup=menu")
        monitor.props["aria-label"] = t("manager.screens.numbered", number=screen.number)
        left, top, wide, high = (round(100 * one, 3) for one in box)
        monitor.style(f"left:calc({left}% + 3px);top:calc({top}% + 3px);"
                      f"width:calc({wide}% - 6px);height:calc({high}% - 6px)")
        monitor.on("dragover", js_handler=_DRAG_OVER)
        monitor.on("dragleave", js_handler=_DRAG_LEAVE)
        monitor.on("drop", lambda event, n=screen.number: self._dropped_on(n, event),
                   js_handler=_DROP_PART)
        with monitor:
            ui.label(str(screen.number)).classes("manager-mon-num")
            ui.label(held.label if held else t("manager.screens.not_used")).classes(
                "manager-mon-part" + ("" if held else " manager-mon-part--none"))
            if inside:
                ui.label(inside).classes("manager-mon-inside")
            if screen.name:
                ui.label(screen.name).classes("manager-mon-name")
            menu = self._monitor_menu(screen, held)
        monitor.on("keydown.enter", menu.open)

    def _monitor_menu(self, screen: Screen, held: Part | None) -> ui.menu:
        with ui.menu().props("auto-close") as menu:
            for part in self.parts:
                at = self.on.get(part.key)
                elsewhere = (t("manager.screens.inside") if at == INSIDE
                             else t("manager.screens.numbered", number=at)
                             if isinstance(at, int) and at != screen.number else "")
                panel.menu_entry(
                    part.label,
                    lambda _e=None, key=part.key, n=screen.number: self._put(key, n),
                    trail=partial(_said, elsewhere) if elsewhere else None,
                    classes="manager-menu-on" if at == screen.number else "")
            panel.menu_entry(
                t("manager.screens.not_used"),
                (lambda _e=None, key=held.key: self._put(key, None)) if held else None,
                mark=_no_mini, classes="" if held else "manager-menu-on")
        return menu

    def _row(self, part: Part) -> None:
        """One part, with where it is, to drag onto a screen or to pick a screen for."""
        number = self.on.get(part.key)
        row = ui.element("div").classes(
            "manager-prow" + (" manager-prow--differs" if part.key in self.differs else ""))
        row.props("role=button tabindex=0 aria-haspopup=menu draggable=true")
        row.on("dragstart", js_handler=_drag_part(part.key))
        row.on("dragend", js_handler=_DRAG_END)
        with row:
            name = ui.label(part.label).classes("manager-prow-name")
            if part.key in self.differs:
                name.tooltip(t("manager.screens.different"))
            elif part.hint:
                name.tooltip(part.hint)
            words, kind = (
                (t("manager.screens.inside"), "set") if number == INSIDE
                else (t("manager.screens.no_screen"), "none") if number is None
                else (t("manager.screens.numbered", number=number), "set")
                if self.screen(number) is not None
                else (t("manager.screens.not_connected"), "missing"))
            ui.label(words).classes(f"manager-prow-where manager-prow-where--{kind}")
            ui.icon(verbs.DRAG).classes("manager-screen-grip")
            menu = self._menu(part, number)
        row.on("keydown.enter", menu.open)

    @on_page
    async def _dropped_on(self, number: int, event: Any) -> None:
        if isinstance(event.args, str) and any(part.key == event.args for part in self.parts):
            await self._put(event.args, number)

    def _slot_face(self, part: Part, number: Placement, screen: Screen | None) -> None:
        if number is None:
            ui.label(t("manager.screens.no_screen")).classes("manager-part-slot-none")
            return
        if number == INSIDE:
            ui.label(t("manager.screens.inside")).classes("manager-part-slot-none")
            self._clear(part)
            return
        ui.label(str(number)).classes("manager-part-slot-num")
        if screen is None:
            ui.label(t("manager.screens.not_connected")).classes("manager-part-slot-size")
        else:
            ui.label(screen.said).classes("manager-part-slot-size truncate").tooltip(screen.size)
        if part.key == PLAYFIELD and screen is not None and self.turn_known:
            self._reference()
        self._clear(part)

    def _clear(self, part: Part) -> None:
        clear = ui.icon(verbs.UNPLACE).classes("manager-part-slot-clear") \
            .props("tabindex=0 role=button")
        clear.props["aria-label"] = t("manager.screens.no_screen")
        clear.tooltip(t("manager.screens.no_screen"))
        clear.on("click.stop", lambda _e, key=part.key: self._put(key, None))
        clear.on("keydown.enter.stop", lambda _e, key=part.key: self._put(key, None))

    def _reference(self) -> None:
        """The card as the stored turn has it, small, on the playfield."""
        card = ui.element("div").classes("manager-pf-card").style(
            f"transform: rotate({self.rotation}deg)")
        with card:
            ui.label(t("manager.screens.card_top")).classes("manager-pf-card-band")
            ui.icon(verbs.UP).classes("manager-pf-card-arrow")
            ui.label(t("manager.screens.card_bottom")).classes("manager-pf-card-band")
        card.tooltip(t("manager.screens.card_reference"))

    def _menu(self, part: Part, number: Placement) -> ui.menu:
        with ui.menu().props("auto-close") as menu:
            for screen in self.screens:
                panel.menu_entry(
                    screen.name or t("manager.screens.numbered", number=screen.number),
                    lambda _e=None, key=part.key, n=screen.number: self._put(key, n),
                    mark=partial(_mini, screen),
                    trail=partial(_size_of, screen),
                    classes="manager-menu-on" if screen.number == number else "")
            if part.inside:
                panel.menu_entry(t("manager.screens.inside"),
                                 lambda _e=None, key=part.key: self._put(key, INSIDE),
                                 mark=_no_mini,
                                 classes="manager-menu-on" if number == INSIDE else "")
            panel.menu_entry(t("manager.screens.no_screen"),
                             lambda _e=None, key=part.key: self._put(key, None),
                             mark=_no_mini)
        return menu

    def _card(self, screen: Screen) -> None:
        using = [part.label for part in self.parts if self.on.get(part.key) == screen.number]
        card = ui.element("div").classes("manager-source-row manager-screen-card")
        card.props("draggable=true")
        card.on("dragstart", js_handler=_drag_start(screen.number))
        card.on("dragend", js_handler=_DRAG_END)
        with card:
            _mini(screen, big=True)
            with ui.column().classes("gap-0 min-w-0 grow"):
                ui.label(screen.said).classes("manager-screen-card-name truncate")
                if screen.name:
                    ui.label(screen.size).classes("manager-help truncate")
            ui.label(", ".join(using) if using else t("manager.screens.not_used")).classes(
                "manager-help manager-screen-use" + (" manager-screen-used" if using else ""))
            ui.icon(verbs.DRAG).classes("manager-screen-grip")

    @on_page
    async def _dropped(self, part: str, event: Any) -> None:
        if isinstance(event.args, int) and self.screen(event.args) is not None:
            await self._put(part, event.args)

    @on_page
    async def _put(self, part: str, number: Placement) -> None:
        after = placed(self.on, part, number)
        if any(held == INSIDE and self.on.get(key) != INSIDE and not self._may_be_inside(key)
               for key, held in after.items()):
            # The part is already on that screen, inside the one that owns it.
            self.draw()
            return
        # A part that cannot be inside the playfield is not left there by a swap.
        after = {key: (None if held == INSIDE and not self._may_be_inside(key) else held)
                 for key, held in after.items()}
        moved = changes(self.on, after)
        if moved and await self.move(moved):
            self.on = after
            if self.phase != _IDLE and PLAYFIELD in moved:
                await self._stop(_IDLE)
        self.draw()

    def _may_be_inside(self, key: str) -> bool:
        return any(part.inside for part in self.parts if part.key == key)

    def _asks_rotation(self) -> bool:
        return (self.point is not None and self.test is not None
                and self.screen(self.on.get(PLAYFIELD)) is not None)

    def _rotation(self) -> None:
        with ui.element("div").classes("manager-rotation"):
            with ui.element("div").classes("manager-rotation-head"):
                ui.icon(verbs.SCREEN_ROTATION).classes("manager-rotation-icon")
                ui.label(t("manager.screens.rotation")).classes("manager-rotation-title")
                if self.rotation:
                    chip, hint = _TURNED[self.rotation]
                    ui.label(t(chip)).classes("manager-tier manager-tier--unknown") \
                        .tooltip(t(hint))
            if self.phase == _CHECKING:
                self._checking()
            elif self.phase == _ASKING:
                self._asking()
            elif self.phase == _DONE:
                self._done()
            else:
                self._idle()

    def _idle(self) -> None:
        ui.label(t("manager.screens.rotation_look")).classes("manager-help")
        ui.label(t("manager.screens.rotation_top")).classes("manager-help")
        panel.action(t("manager.screens.test_card_opening" if self.opening
                       else "manager.screens.test_card"),
                     self._open_card, icon=verbs.SHOW_NUMBERS,
                     hint=t("manager.screens.test_card.help"), enabled=not self.opening)()

    def _checking(self) -> None:
        ui.timer(_HOLD_S, self._hold)
        ui.label(t("manager.screens.rotation_press")).classes("manager-help")
        ui.label(t("manager.screens.rotation_stop")).classes("manager-help")
        self._answers()
        with ui.element("div").classes("manager-rotation-wide"):
            panel.action(t("manager.screens.test_card_close"), self._close_card,
                         icon=verbs.CLOSE, hint=t("manager.screens.test_card_close.help"))()

    def _asking(self) -> None:
        ui.label(t("manager.screens.rotation_asked")).classes("manager-help")
        self._answers()
        with ui.element("div").classes("manager-rotation-wide"):
            panel.action(t("manager.screens.test_card_opening" if self.opening
                           else "manager.screens.reopen_card"),
                         self._open_card, icon=verbs.SHOW_NUMBERS,
                         hint=t("manager.screens.reopen_card.help"),
                         enabled=not self.opening)()

    def _answers(self) -> None:
        with ui.element("div").classes("manager-rotation-turns"):
            for degrees, words, icon in _TURNS:
                panel.action(t(words), partial(self._turn_by, degrees), icon=icon,
                             hint=t(_WAYS[degrees]), enabled=not self.opening)()
        with ui.element("div").classes("manager-rotation-wide"):
            panel.action(t("manager.screens.looks_right"), self._looks_right,
                         icon=verbs.DONE, hint=t("manager.screens.looks_right.help"),
                         enabled=not self.opening)()

    def _done(self) -> None:
        with ui.element("div").classes("manager-rotation-done"):
            ui.icon(verbs.DONE).classes("manager-rotation-icon")
            ui.label(t("manager.screens.rotated_correctly"))
        panel.action(t("manager.screens.check_again"), self._check_again,
                     icon=verbs.REVERT, hint=panel.NO_HINT)()

    async def _hold(self) -> None:
        if self.test is None or await self.test.hold() or self.phase != _CHECKING:
            return
        self.phase = _ASKING
        self.draw()

    @on_page
    async def _open_card(self) -> None:
        screen = self.screen(self.on.get(PLAYFIELD))
        if self.test is None or screen is None:
            return
        self.opening = True
        self.draw()
        up = False
        try:
            up = await self.test.open(screen, self.rotation)
            if up and not self._on_playfield(screen):
                await self.test.close()
                up = False
        finally:
            self.opening = False
            if up:
                self.phase = _CHECKING
            self.draw()

    def _on_playfield(self, screen: Screen) -> bool:
        now = self.screen(self.on.get(PLAYFIELD))
        return now is not None and now.number == screen.number

    @on_page
    async def _turn_by(self, degrees: int) -> None:
        screen = self.screen(self.on.get(PLAYFIELD))
        if self.point is None or self.test is None or screen is None:
            return
        reopen = False
        async with self.turning:
            if self.phase not in (_CHECKING, _ASKING) or self.opening:
                return
            total = (self.rotation + degrees) % 360
            if await self.point(total, screen):
                self.rotation = total
                if self.phase == _CHECKING:
                    await self.test.turn(total)
                else:
                    reopen = True
        if reopen and self.phase == _ASKING:
            await self._open_card()
        else:
            self.draw()

    async def _stop(self, phase: str) -> None:
        if self.test is not None:
            await self.test.close()
        self.phase = phase
        self.draw()

    @on_page
    async def _looks_right(self) -> None:
        # Not under `turning`: an answer given while a turn is being kept goes through at once.
        if self.answering or self.phase not in (_CHECKING, _ASKING):
            return
        self.answering = True
        try:
            await self._stop(_DONE)
            if self.test is not None and self.test.apply is not None:
                await self.test.apply()
        finally:
            self.answering = False

    @on_page
    async def _close_card(self) -> None:
        await self._stop(_IDLE)

    @on_page
    async def _check_again(self) -> None:
        self.phase = _IDLE
        self.draw()
