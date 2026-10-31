"""What each action is mapped to, one field for every key and button that does it.

The caller passes the rows, what each is mapped to now and the one act it reports to; the
table writes nothing itself. A press another row already holds is a question rather than a
second binding, because dispatch gives a press to the first action that lists it and the
second would silently never fire.
"""

from __future__ import annotations

import ipaddress
import logging
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from nicegui import ui

from common.config import input_registry
from common.config.paths import get_ini_config
from common.devices.install_identity import display_name
from common.failures import why
from common.host import peer_name
from common.i18n import t
from common.security import sign_in
from manager.kit import confirm, offload, panel, verbs
from manager.kit.api import ApiClient
from manager.kit.on_page import on_page
from manager.settings import binding_editor, input_relay

logger = logging.getLogger("vpinfe.manager.settings.binding_table")

Write = Callable[[dict[str, list[str]]], Awaitable[bool]]

# How long the page holds the cabinet's buttons with nothing pressed or touched on it, and how
# long it waits for the frontend windows it asked to open.
IDLE_S = 120.0
OPEN_WAIT_S = 30.0

# How long a binding can be asked to be held, in milliseconds. A length set some other way
# joins the choices where it falls.
HOLD_STOPS = (0, 500, 1000, 1500, 2000, 3000)

# Reports what was pressed to the table, which names it and says what it
# does. Installed once per document: a table drawn later only repoints it. A press while a
# question over the table, a menu, a listening row or a text field has the keyboard is not a
# press to report. A dialog the table is drawn in is not a question over it. It also tells
# the table when the page is shown or hidden and, at most every 5 seconds, that the person
# touched it, and carries a take-back from the cabinet to it. Browsers send no keyup for a key
# pressed under Command, so Command's own keyup and the window's blur clear the keys.
_LIVE_JS = """(() => {
  const live = window.__bindingLive = window.__bindingLive || {};
  live.id = %(id)d;
  if (live.listening) return;
  live.listening = true;
  const busy = () => (window.__bindingListen || {}).row
    || document.querySelector('.q-menu')
    || [...document.querySelectorAll('.q-dialog')].some(
         one => !one.contains(document.getElementById('c' + live.id)))
    || /^(INPUT|TEXTAREA|SELECT)$/.test((document.activeElement || {}).tagName || '');
  const tell = (name, detail) => {
    const target = document.getElementById('c' + live.id);
    if (target) target.dispatchEvent(new CustomEvent(name, {detail}));
  };
  const held = [];
  const report = (token) => {
    if (busy()) return;
    const at = held.indexOf(token);
    if (at >= 0) held.splice(at, 1);
    held.push(token);
    tell('heard', token);
  };
  const release = (token) => {
    const at = held.indexOf(token);
    if (at < 0) return;
    held.splice(at, 1);
    tell('released', held.length ? held[held.length - 1] : '');
  };
  const drop = (gone) => {
    const kept = held.filter(token => !gone(token));
    if (kept.length === held.length) return;
    held.length = 0;
    held.push(...kept);
    tell('released', held.length ? held[held.length - 1] : '');
  };
  const relayed = new Set();
  live.relayed = report;
  live.relay = (token, isDown) => {
    const listen = window.__bindingListen;
    if (listen && listen.relay) listen.relay(token, isDown);
    else if (isDown) report(token);
    if (isDown) relayed.add(token);
    else { relayed.delete(token); release(token); }
  };
  live.dropRelayed = () => {
    const lost = [...relayed];
    relayed.clear();
    drop(token => lost.includes(token));
  };
  const allUp = () => {
    const listen = window.__bindingListen;
    if (listen && listen.relayAllUp) listen.relayAllUp();
    if (held.length) { held.length = 0; tell('released', ''); }
  };
  live.taken = () => { allUp(); tell('taken', ''); };
  const seen = () => { if (document.hidden) allUp(); tell('visible', !document.hidden); };
  document.addEventListener('visibilitychange', seen);
  seen();
  let touched = 0;
  const touch = () => {
    const now = Date.now();
    if (now - touched > 5000) { touched = now; tell('touched', ''); }
  };
  ['pointerdown', 'pointermove', 'keydown', 'wheel', 'touchstart']
    .forEach(name => window.addEventListener(name, touch, true));
  window.addEventListener('keydown', (e) => { if (!e.repeat) report('key:' + e.code); }, true);
  const isKey = (token) => token.startsWith('key:');
  window.addEventListener('keyup', (e) => {
    release('key:' + e.code);
    if (e.code.startsWith('Meta')) drop(isKey);
  }, true);
  window.addEventListener('blur', () => drop(isKey));
  const down = {};
  const named = {};
  window.addEventListener('gamepaddisconnected', (e) => {
    for (const seat of Object.keys(down)) {
      if (!seat.startsWith(e.gamepad.index + ':') || !down[seat]) continue;
      down[seat] = false;
      release(named[seat]);
    }
  });
  setInterval(() => {
    for (const pad of (navigator.getGamepads ? navigator.getGamepads() : [])) {
      if (!pad) continue;
      pad.buttons.forEach((b, i) => {
        const seat = pad.index + ':' + i;
        const fresh = b.pressed && !down[seat];
        const gone = !b.pressed && down[seat];
        down[seat] = b.pressed;
        if (fresh) { named[seat] = %(pad_token)s; report(named[seat]); }
        if (gone) release(named[seat]);
      });
    }
  }, 40);
})()"""


_DROP_RELAYED_JS = "window.__bindingLive && window.__bindingLive.dropRelayed()"


@dataclass(frozen=True)
class Kept:
    """A mapping the table shows and never edits: what it says, what it is, and whether a
    controller makes it."""

    label: str
    tip: str
    pad: bool = True


@dataclass(frozen=True)
class Row:
    """One action: what it is called, where it is listed and what it ships mapped to.

    `holds` is False where a hold means nothing. `kept` are mappings drawn after the
    others with no way to remove or move them. `note` and `findings` are drawn under the
    row.
    """

    name: str
    label: str
    group: str = ""
    defaults: tuple[str, ...] = ()
    holds: bool = True
    kept: tuple[Kept, ...] = ()
    note: str = ""
    findings: tuple[Callable[[], None], ...] = ()


@dataclass(frozen=True)
class Live:
    """The last press: what it was called, whether a controller made it, and what it does."""

    name: str = ""
    pad: bool = False
    does: str = ""
    nothing: str = ""


def selectors(value: Any) -> list[str]:
    """A binding list, whatever shape it arrived in.

    A stored value is a list and a schema default is the comma-joined string the config
    file would hold. Taking `list()` of the second gives one chip per character.
    """
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    return [str(one).strip() for one in (value or []) if str(one).strip()]


def holders_of(bound: Mapping[str, Sequence[str]], name: str, binding: str) -> list[str]:
    """The other rows that hold this press, at whatever length they hold it."""
    wanted = input_registry.contention(binding)
    return [other for other, held in bound.items()
            if other != name and any(input_registry.contention(one) == wanted for one in held)]


def after_adding(bound: Mapping[str, Sequence[str]], name: str, binding: str, *,
                 old: str = "", take: bool = False) -> dict[str, list[str]]:
    """`bound` with `binding` on `name` in place of `old`, and off every other row if `take`."""
    out = {other: list(held) for other, held in bound.items()}
    out[name] = (input_registry.replaced(out[name], old, binding) if old
                 else [*out[name], binding])
    if take:
        for other in holders_of(bound, name, binding):
            out[other] = input_registry.without(out[other], binding)
    return out


def after_reverting(bound: Mapping[str, Sequence[str]], name: str,
                    defaults: Iterable[str]) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """`bound` with `name` back on `defaults`, and what other rows give up for it.

    A default another row has since taken goes back on a yes: the second answer is what
    each of those rows holds that the default wants.
    """
    out = {other: list(held) for other, held in bound.items()}
    have = {input_registry.identity(one) for one in bound[name]}
    back = [one for one in defaults if input_registry.identity(one) not in have]
    given: dict[str, list[str]] = {}
    for other, held in bound.items():
        if other == name:
            continue
        lost = [one for one in held if any(
            input_registry.contention(one) == input_registry.contention(want) for want in back)]
        if lost:
            given[other] = lost
            out[other] = [one for one in held if one not in lost]
    out[name] = list(defaults)
    return out, given


def changed(before: Mapping[str, Sequence[str]],
            after: Mapping[str, Sequence[str]]) -> dict[str, list[str]]:
    """Only the rows whose bindings differ."""
    return {name: list(held) for name, held in after.items()
            if list(before.get(name, ())) != list(held)}


def stops(now: int) -> list[int]:
    """The lengths to offer a hold, with the one it has if that is none of them."""
    return sorted({*HOLD_STOPS, now})


def listed(names: Iterable[str]) -> str:
    """Names read out in a sentence: "A", "A and B", "A, B and C"."""
    names = list(names)
    if len(names) < 2:
        return names[0] if names else ""
    return t("manager.binding_table.and_last", most=", ".join(names[:-1]), last=names[-1])


def elsewhere(peer: str) -> bool:
    """Whether the socket address a page was asked from is another device.

    The socket's own address, never a header, which the caller writes. No address, or one
    that is not an address, is no other device.
    """
    try:
        address = ipaddress.ip_address(peer)
    except ValueError:
        return False
    return not (getattr(address, "ipv4_mapped", None) or address).is_loopback


def _asker() -> tuple[str, str]:
    """The User-Agent and socket address the page being drawn was asked from."""
    try:
        request = ui.context.client.request
    except RuntimeError:
        return "", ""
    return request.headers.get("user-agent", ""), request.client.host if request.client else ""


def opened_elsewhere() -> bool:
    """Whether the page being drawn was opened from another device than the one serving it."""
    return elsewhere(_asker()[1])


def holder_of(agent: str, host: str) -> tuple[str, str]:
    """The browser a page runs in, named as sign-in names it, and the device it runs on, or
    "" for this one. Looks the device up by address, which can wait: keep it off the event loop."""
    browser = sign_in.describe_agent(agent)
    if not elsewhere(host):
        return browser, ""
    name = peer_name.place_of(host)
    try:
        ipaddress.ip_address(name)
    except ValueError:
        name = name.split(".")[0]
    return browser, name


def _around(key: str, name: str) -> tuple[str, str]:
    """The words before and after `name` in a sentence, whichever order a language puts it."""
    before, _, after = t(key, row="\0").partition("\0")
    return before.strip(), after.strip()


def _lit(element: ui.element, on: bool) -> None:
    if on:
        element.classes(add="manager-binding-lit")
    else:
        element.classes(remove="manager-binding-lit")


def said(bindings: Sequence[str]) -> str:
    """Bindings read out mid-sentence, a held one closing its own aside with a comma."""
    text = listed(input_registry.describe(one) for one in bindings)
    return f"{text}," if bindings and input_registry.hold_ms(bindings[-1]) else text


class Table:
    """The rows, each with one field for everything mapped to it.

    `bound` is every row's bindings now and `write` is told the rows that change, answering
    whether they were kept; the table follows it only if they were. `source` names where
    the defaults come from, for the revert mark's tooltip. A table that is not `writable`
    only shows. Where the program does everything a press is mapped to, `shares`, a press
    another row holds may be used for both and nothing is slowed or left without.
    `refuses` says why a press cannot be mapped, or "" where it can. With `pads_when_heard`
    the controller buttons are left undrawn until a controller is pressed.

    Clicking a row's label or field has that row listen for the next press. Above the rows
    a line names whatever is pressed and what it does, and says what is
    being listened for while a row is.
    """

    def __init__(self, rows: Sequence[Row], bound: Mapping[str, Any], *, write: Write,
                 source: str, writable: bool = True, shares: bool = False,
                 refuses: Callable[[str], str] | None = None,
                 pads_when_heard: bool = False) -> None:
        self.rows = tuple(rows)
        self.by_name = {row.name: row for row in self.rows}
        self.bound = {row.name: selectors(bound.get(row.name)) for row in self.rows}
        self.write = write
        self.source = source
        self.writable = writable
        self.shares = shares
        self.refuses = refuses
        self.pads_hidden = pads_when_heard
        self.lit = ""
        self.live = Live()
        self.listening = ""
        self._refocus = ""
        self.elsewhere = opened_elsewhere()
        self.sending = True
        self.off_reason = ""
        self.opening = False
        self._opening_since = 0.0
        self._visible = True
        self._active_at = time.monotonic()
        self._holder: tuple[str, str] = ("", "")
        self._playing = input_relay.table_playing()
        self._frontend_up = input_relay.frontend_up()
        self._cab = display_name(get_ini_config())
        self._rows: dict[str, ui.element] = {}
        self._labels: dict[str, ui.element] = {}
        self._chips: dict[tuple[str, str], ui.element] = {}
        with ui.element("div").classes("w-full") as self.container:
            ui.label(t("manager.binding_table.tester")) \
                .classes("manager-fact-heading manager-binding-tester")
            self.line = ui.element("div")
            self.send = ui.element("div").classes("manager-binding-send")
            self.grid = ui.element("div").classes("manager-facts manager-bindings")
        self._client = ui.context.client
        self._asker = _asker()
        self.line.on("heard", self._heard, js_handler="(e) => emit(e.detail)")
        self.line.on("released", self._let_go, js_handler="(e) => emit(e.detail)")
        self.line.on("taken", self._taken, js_handler="(e) => emit()")
        self.line.on("visible", self._shown, js_handler="(e) => emit(e.detail)")
        self.line.on("touched", self._touched, js_handler="(e) => emit()")
        ui.run_javascript(_LIVE_JS % {"id": self.line.id,
                                      "pad_token": binding_editor.PAD_TOKEN_JS})
        if writable:
            self.line.on("listen", self._listening, js_handler="(e) => emit(e.detail)")
            self.line.on("captured", self._captured, js_handler="(e) => emit(e.detail)")
            ui.run_javascript(binding_editor.listen_js(self.line.id))
        input_relay.present(self._client)
        with self.container:
            ui.timer(0.2, self._begin, once=True)
            ui.timer(input_relay.REFRESH_S, self._keep)
        self._draw_live()
        self._draw_send()
        self.draw()

    def label_of(self, name: str) -> str:
        row = self.by_name.get(name)
        return row.label if row is not None else name

    def draw(self) -> None:
        if self.container.is_deleted:
            return
        self.grid.clear()
        self._rows.clear()
        self._labels.clear()
        self._chips.clear()
        clashes = input_registry.holders(self.bound)
        heading = ""
        with self.grid:
            for row in self.rows:
                if row.group and row.group != heading:
                    ui.label(row.group).classes("manager-fact-heading")
                heading = row.group
                self._row(row, clashes)
        self._light()
        if self._refocus:
            binding_editor.focus_label(self._refocus)

    def _row(self, row: Row, clashes: Mapping[str, list[str]]) -> None:
        with ui.element("div").classes("manager-binding-row") as cells:
            self._rows[row.name] = cells
            if self.writable:
                cells.props(f'data-row="{row.name}" data-holds="{int(row.holds)}"')
            self._labels[row.name] = ui.label(row.label).classes(
                "manager-fact-label manager-binding-label")
            if self.writable:
                self._labels[row.name].props("tabindex=0 role=button")
            with ui.element("div").classes("manager-fact-edit"):
                with ui.element("div").classes("manager-binding-field"):
                    with ui.element("div").classes("manager-chips"):
                        self._chips_of(row, clashes)
                with ui.element("div").classes("manager-binding-revert"):
                    if self.writable and not input_registry.same_bindings(
                            self.bound[row.name], row.defaults):
                        self._revert_mark(row)
        self._mark()
        if row.note:
            with ui.element("div").classes("manager-binding-aside"):
                ui.label(row.note).classes("manager-help")
        for finding in row.findings:
            with ui.element("div").classes("manager-binding-aside"):
                finding()

    def _chips_of(self, row: Row, clashes: Mapping[str, list[str]]) -> None:
        held = [one for one in self.bound[row.name]
                if not (self.pads_hidden and input_registry.is_pad(one))]
        # Keys before controller buttons, whichever order they were mapped in.
        for binding in sorted(held, key=input_registry.is_pad):
            self._chip(row, binding, clashes.get(input_registry.contention(binding)) or [])
        for one in row.kept:
            with ui.element("div").classes("manager-chip-holder"):
                with ui.element("div").classes("manager-member-chip manager-chip-quiet") \
                        .tooltip(one.tip):
                    if one.pad:
                        ui.icon(verbs.CONTROLLER).classes("manager-chip-glyph")
                    ui.label(one.label)
        if not held and not row.kept:
            ui.label(t("manager.binding_table.nothing_mapped")) \
                .classes("manager-member-chip manager-tier manager-tier--warn") \
                .tooltip(t("manager.binding_table.nothing_mapped.help"))

    def _chip(self, row: Row, binding: str, claimed_by: list[str]) -> None:
        shown = input_registry.describe(binding)
        crowded = len(claimed_by) > 1 and not self.shares
        with ui.element("div").classes("manager-chip-holder"):
            with ui.element("div").classes(
                    "manager-member-chip "
                    + ("manager-chip-warn" if crowded else "manager-chip-quiet")) as chip:
                if input_registry.is_pad(binding):
                    ui.icon(verbs.CONTROLLER).classes("manager-chip-glyph")
                ui.label(shown)
            self._chips[(row.name, binding)] = chip
            if crowded:
                chip.tooltip(t("manager.binding_editor.shared", others=listed(
                    self.label_of(name) for name in claimed_by if name != row.name)))
            elif len(claimed_by) > 1:
                chip.tooltip(t("manager.binding_table.also_does", others=listed(
                    self.label_of(name) for name in claimed_by if name != row.name)))
            if not self.writable:
                return
            if row.holds:
                chip.classes("cursor-pointer")
                with chip:
                    self._hold_menu(row, binding)
            ui.label("✕").classes("manager-chip-remove") \
                .on("click", lambda: self._remove(row, binding)) \
                .tooltip(t("manager.binding_table.remove_binding", binding=shown))

    def _hold_menu(self, row: Row, binding: str) -> None:
        """How long the binding has to be down, which a gesture cannot say exactly."""
        now = input_registry.hold_ms(binding)
        with ui.menu() as menu:
            panel.segmented({ms: t("manager.binding_table.seconds", seconds=f"{ms / 1000:g}")
                             if ms else t("manager.binding_table.no_hold") for ms in stops(now)},
                            now,
                            lambda event: self._held(row, binding, int(event.value), menu)) \
                .classes("manager-binding-hold")

    def _revert_mark(self, row: Row) -> None:
        back = listed(t("manager.binding_table.quoted", binding=input_registry.describe(one))
                      for one in row.defaults) or t("manager.binding_table.nothing")
        ui.icon(verbs.REVERT).classes("manager-revert") \
            .on("click", lambda: self._revert(row)) \
            .tooltip(t("manager.binding_table.revert_to", bindings=back, source=self.source))

    def _draw_live(self) -> None:
        self.line.clear()
        live = self.live
        if self.listening:
            self._draw_listening(self.by_name[self.listening])
            return
        with self.line, ui.element("div").classes(
                "manager-binding-live" + ("" if live.name else " manager-binding-live--idle")):
            if not live.name:
                ui.icon(verbs.PRESS)
                ui.label(t("manager.binding_table.press_anything"))
                return
            ui.icon(verbs.CONTROLLER if live.pad else verbs.KEYBOARD)
            ui.label(live.name).classes("manager-binding-live-name")
            ui.label("·").classes("manager-binding-live-sep")
            if live.does:
                ui.label(live.does).classes("manager-binding-live-does")
            else:
                ui.label(live.nothing or t("manager.binding_table.no_action")) \
                    .classes("manager-binding-live-key")

    def _draw_listening(self, row: Row) -> None:
        """What the row is listening for."""
        before, after = _around("manager.binding_table.press_for", row.label)
        with self.line, ui.element("div").classes("manager-binding-live manager-binding-live--on"):
            ui.icon(verbs.LISTEN)
            if before:
                ui.label(before)
            ui.label(row.label).classes("manager-binding-live-name")
            if after:
                ui.label(after)

    def _draw_send(self) -> None:
        """The switch that sends the cabinet's buttons here, or what stands in its place:
        the offer to open the frontend windows, or that they are opening."""
        if self.send.is_deleted:
            return
        self.send.clear()
        words = ""
        with self.send:
            if self.opening:
                ui.spinner(size="16px").classes("shrink-0")
                words = t("manager.binding_table.opening", device=self._cab)
            elif self.elsewhere and not self._frontend_up:
                panel.action(t("action.frontend.start"), self._open_windows, icon=verbs.OPEN_WINDOW,
                             hint=t("action.frontend.start.help"))()
                words = t("manager.binding_table.frontend_closed", device=self._cab)
            else:
                ui.label(t("manager.binding_table.send_events")).classes("manager-fact-label")
                panel.switch(self.sending, self._switched,
                             hint=t("manager.binding_table.send_events.help",
                                    device=self._cab))()
                words = self._words()
            if words:
                ui.label(words).classes("manager-help manager-binding-send-words")

    def _words(self) -> str:
        """What is worth saying beside the switch. Nothing while all is normal."""
        if self.sending and self._playing:
            return t("manager.binding_table.paused", device=self._cab)
        if self.listening:
            return t("manager.binding_table.click_away")
        if not self.sending and self.off_reason == "idle":
            return t("manager.binding_table.off_idle")
        if not self.sending and self.off_reason == "taken":
            return t("manager.binding_table.off_taken", device=self._cab)
        return ""

    def _mark(self) -> None:
        """Draw the row that is listening as the one."""
        for name, cells in self._rows.items():
            if name == self.listening:
                cells.classes(add="manager-binding-row--listening")
            else:
                cells.classes(remove="manager-binding-row--listening")

    def _light(self) -> None:
        """Mark the rows and chips the last press belongs to."""
        for name, label in self._labels.items():
            _lit(label, bool(self.lit) and any(
                input_registry.press(one) == self.lit for one in self.bound[name]))
        for (_, binding), chip in self._chips.items():
            _lit(chip, bool(self.lit) and input_registry.press(binding) == self.lit)

    def _does(self, token: str) -> list[str]:
        """Every row that holds this press, a held one said as such."""
        wanted = input_registry.press(token)
        return [t("manager.binding_table.when_held", row=row.label)
                if input_registry.hold_ms(one) else row.label
                for row in self.rows for one in self.bound[row.name]
                if input_registry.press(one) == wanted]

    def _show(self, token: str, *, does: str = "", nothing: str = "") -> None:
        if self.pads_hidden and input_registry.is_pad(token):
            self.pads_hidden = False
            self.draw()
        self.lit = input_registry.press(token)
        self.live = Live(input_registry.describe(token),
                         input_registry.is_pad(token), does, nothing)
        self._draw_live()
        self._light()

    def _idle(self) -> None:
        self.lit = ""
        self.live = Live()
        self._draw_live()
        self._light()

    def _heard(self, event: Any) -> None:
        token = str(event.args or "").strip()
        if token:
            self._active_at = time.monotonic()
            self._show(token, does=listed(self._does(token)))

    def _let_go(self, event: Any) -> None:
        """A press let go: the line shows the one still down, or goes back to waiting."""
        if self.listening:
            return
        token = str(event.args or "").strip()
        if token:
            self._show(token, does=listed(self._does(token)))
        else:
            self._idle()

    def _listening(self, event: Any) -> None:
        """The page started listening for a row, moved to another, or stopped."""
        name = str(event.args or "")
        self.listening = name if name in self.by_name else ""
        self._active_at = time.monotonic()
        if self.listening:
            self.lit = ""
            self._light()
        else:
            self.live = Live()
        self._mark()
        self._draw_live()
        self._draw_send()

    def _touched(self, _event: Any) -> None:
        self._active_at = time.monotonic()

    def _shown(self, event: Any) -> None:
        """The page came into view or went out of it. Out of view it lets the buttons go."""
        self._visible = bool(event.args)
        if self._visible:
            self._active_at = time.monotonic()
        self._sync_lease()

    def _taken(self, _event: Any) -> None:
        """A button was held at the cabinet, which took the buttons from every page."""
        self._turn_off("taken")

    def _turn_off(self, reason: str) -> None:
        self.sending = False
        self.off_reason = reason
        input_relay.disarm(self._client)
        self._draw_send()

    def _switched(self, event: Any) -> None:
        self.sending = bool(event.value)
        self.off_reason = ""
        self._active_at = time.monotonic()
        self._sync_lease(resume=True)
        self._draw_send()

    def _sync_lease(self, *, resume: bool = False) -> None:
        """The page holds the cabinet's buttons while its switch is on, it is in view and no
        table is playing, and asks again every tick to keep them."""
        if resume and self.sending:
            input_relay.resume(self._client)
        if self._client.is_deleted:
            input_relay.disarm(self._client)
        elif self.sending and self._visible and not self._playing:
            browser, device = self._holder
            input_relay.arm(self._client, browser=browser, device=device)
        elif input_relay.disarm(self._client):
            self._client.run_javascript(_DROP_RELAYED_JS)

    async def _begin(self) -> None:
        """Names who is asking, which needs a lookup, and then asks."""
        agent, host = self._asker
        try:
            self._holder = await offload.io(holder_of, agent, host)
        except Exception:  # noqa: BLE001 - an unnamed holder is still a holder
            logger.debug("Could not name the browser this page runs in", exc_info=True)
        self._sync_lease(resume=True)

    async def _keep(self) -> None:
        """Keeps the ask alive, and redraws when something under the switch changed: the
        frontend opened or closed, a table started or ended, or the page went idle."""
        if self.container.is_deleted:
            input_relay.disarm(self._client)
            return
        now = time.monotonic()
        playing = input_relay.table_playing()
        up = input_relay.frontend_up()
        changed = (playing, up) != (self._playing, self._frontend_up)
        self._playing, self._frontend_up = playing, up
        if self.opening and up:
            self.opening = False
            self.sending = True
            self.off_reason = ""
            self._active_at = now
            changed = True
            self._sync_lease(resume=True)
        elif self.opening and now - self._opening_since >= OPEN_WAIT_S:
            self.opening = False
            ui.notify(t("manager.binding_table.open_failed", device=self._cab), type="warning")
            changed = True
        if self.sending and now - self._active_at >= IDLE_S:
            self._turn_off("idle")
            return
        self._sync_lease()
        if changed:
            self._draw_send()

    @on_page
    async def _open_windows(self) -> None:
        """Asks to open the frontend windows, so the cabinet's controller can be heard. The
        two ways to say yes differ in what leaving this page does to the windows."""
        answer = await confirm.ask(
            t("manager.binding_table.open_question", device=self._cab),
            detail=[t("manager.binding_table.open_heard"),
                    t("manager.binding_table.open_leave_detail")],
            confirm=t("manager.binding_table.open_page"), icon=verbs.OPEN_WINDOW,
            danger=False,
            second=(t("manager.binding_table.open_stay"), verbs.OPEN_WINDOW))
        if answer == confirm.Answer.CANCEL:
            return
        self.opening = True
        self._opening_since = time.monotonic()
        self._draw_send()
        try:
            done = await offload.io(ApiClient().perform_action, "frontend", "start",
                                    "asked from the Input page")
        except Exception as exc:  # noqa: BLE001 - the reason belongs on the page
            self.opening = False
            self._draw_send()
            ui.notify(t("said.action_failed"), caption=why(exc), type="negative")
            return
        if not done.get("performed"):
            self.opening = False
            self._draw_send()
            ui.notify(t("manager.binding_table.open_failed", device=self._cab), type="warning")
            return
        if answer == confirm.Answer.CONFIRM:
            input_relay.close_with_the_last_page()
        else:
            input_relay.leave_open()

    @on_page
    async def _captured(self, event: Any) -> None:
        """The row that was listening heard a press and everything was let go."""
        heard = event.args if isinstance(event.args, dict) else {}
        row = self.by_name.get(str(heard.get("row") or ""))
        token = str(heard.get("token") or "").strip()
        self.listening = ""
        self._active_at = time.monotonic()
        self._mark()
        self._draw_send()
        if row is not None and token:
            # A press that began at the keyboard ends there: the redraw replaces the label
            # that had focus.
            self._refocus = row.name if heard.get("key") else ""
            try:
                await self._mapped(row, token)
            finally:
                self._refocus = ""
        else:
            self._draw_live()

    @on_page
    async def _mapped(self, row: Row, token: str) -> None:
        """A press captured for `row`."""
        token = input_registry.identity(token)
        if self.refuses is not None and (why := self.refuses(token)):
            ui.notify(why, type="warning")
            self._draw_live()
            return
        if any(input_registry.contention(one) == input_registry.contention(token)
               for one in self.bound[row.name]):
            self._show(token, nothing=t("manager.binding_table.already_does", row=row.label))
            return
        self._show(token, does=listed(self._does(token)))
        await self._offer(row, token)

    @on_page
    async def _held(self, row: Row, binding: str, ms: int, menu: ui.menu) -> None:
        menu.close()
        wanted = input_registry.with_hold(binding, ms)
        if wanted != binding:
            await self._offer(row, wanted, old=binding)

    async def _offer(self, row: Row, binding: str, *, old: str = "") -> None:
        holders = holders_of(self.bound, row.name, binding)
        if holders:
            await self._move(row, binding, old, holders)
        else:
            await self._add(row, binding, old)

    async def _move(self, row: Row, binding: str, old: str, holders: list[str]) -> None:
        after = after_adding(self.bound, row.name, binding, old=old, take=True)
        theirs = next(one for one in self.bound[holders[0]]
                      if input_registry.contention(one) == input_registry.contention(binding))
        mapped = t("manager.binding_table.mapped_to", binding=said([theirs]),
                   rows=listed(self.label_of(name) for name in holders))
        if input_registry.hold_ms(theirs) != input_registry.hold_ms(binding):
            mapped += " " + t("manager.binding_table.one_hold", binding=input_registry.describe(
                input_registry.with_hold(binding, 0)))
        if self.shares:
            if not await confirm.ask(
                    t("manager.binding_table.share_question",
                      binding=input_registry.describe(binding), row=row.label),
                    detail=[mapped],
                    confirm=t("manager.binding_table.share", count=len(holders)),
                    icon=verbs.ADD, danger=False):
                self._idle()
                return
            await self._apply(after_adding(self.bound, row.name, binding, old=old), binding)
            return
        question = t("manager.binding_table.move_question",
                     binding=input_registry.describe(binding), row=row.label)
        detail = [mapped, *self._emptied(after, holders), *self._slowing(after)]
        if not await confirm.ask(question, detail=detail, confirm=t("word.move"),
                                 icon=verbs.MOVE, danger=False):
            self._idle()
            return
        moved = t("manager.binding_table.moved",
                  source=listed(self.label_of(name) for name in holders), target=row.label)
        await self._apply(after, binding, moved)

    async def _add(self, row: Row, binding: str, old: str) -> None:
        after = after_adding(self.bound, row.name, binding, old=old)
        if notes := self._slowing(after):
            held = bool(input_registry.hold_ms(binding))
            bare = input_registry.describe(input_registry.with_hold(binding, 0))
            answer = await confirm.ask(
                t("manager.binding_table.hold_question" if held
                  else "manager.binding_table.add_question",
                  binding=bare if held else input_registry.describe(binding), row=row.label),
                detail=notes,
                confirm=t("manager.binding_table.add_hold") if held else t("word.add"),
                icon=verbs.ADD, danger=False)
            if not answer:
                self._idle()
                return
        await self._apply(after, binding)

    @on_page
    async def _remove(self, row: Row, binding: str) -> None:
        # Asked only for a binding no press can make again, a modifier or an axis, because
        # removing it is permanent.
        if not input_registry.capturable(binding) and not await confirm.ask(
                t("manager.binding_editor.remove_2", shown=input_registry.describe(binding)),
                detail=t("manager.binding_editor.remove_detail"),
                confirm=t("word.remove"), icon=verbs.REMOVE):
            return
        after = {name: list(held) for name, held in self.bound.items()}
        after[row.name] = [one for one in after[row.name] if one != binding]
        await self._apply(after)

    @on_page
    async def _revert(self, row: Row) -> None:
        if self.shares:
            await self._apply({**self.bound, row.name: list(row.defaults)})
            return
        after, given = after_reverting(self.bound, row.name, row.defaults)
        emptied = self._emptied(after, list(given))
        notes = self._slowing(after)
        if given or notes:
            moving = [one for lost in given.values() for one in lost]
            lines = [t("manager.binding_table.moves_back", count=len(moving),
                       binding=said(moving),
                       rows=listed(self.label_of(name) for name in given)) if given else "",
                     *emptied, *notes]
            if not await confirm.ask(
                    t("manager.binding_table.revert_question", row=row.label),
                    detail=[line for line in lines if line], confirm=t("word.revert"),
                    icon=verbs.REVERT, danger=False):
                return
        await self._apply(after)

    def _emptied(self, after: Mapping[str, Sequence[str]], rows: Sequence[str]) -> list[str]:
        gone = [self.label_of(name) for name in rows if not after[name]]
        return [t("manager.binding_table.unmapped", rows=listed(gone))] if gone else []

    def _slowing(self, after: Mapping[str, Sequence[str]]) -> list[str]:
        """What a change would stop scrolling, said before it happens."""
        if self.shares:
            return []
        out = []
        for one in input_registry.newly_stalled(self.bound, after):
            row = self.label_of(one.action)
            binding = input_registry.describe(one.binding)
            if one.hold in self.bound.get(one.holder, ()):
                out.append(t("manager.binding_table.slowed_by", hold=said([one.hold]),
                             holder=self.label_of(one.holder), row=row, binding=binding))
            else:
                out.append(t("manager.binding_table.slowed", row=row, binding=binding))
        return list(dict.fromkeys(out))

    async def _apply(self, after: Mapping[str, Sequence[str]], binding: str = "",
                     moved: str = "") -> None:
        """Keep the rows that changed, and follow the write only if it was kept."""
        changes = changed(self.bound, after)
        if changes and not await self.write(changes):
            return
        self.bound = {name: list(held) for name, held in after.items()}
        if binding:
            self._show(binding, does=moved or listed(self._does(binding)))
        else:
            self._idle()
        self.draw()
