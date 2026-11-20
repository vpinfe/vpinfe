"""Launchers > Visual Pinball X > Input: the binding table over that launcher's ini.

The table is the one VPinFE's own Input page draws, with no holds, since the program has
none, and a press another row has offered as both. It writes this launcher's ini only.

Where the program's own SDL library loads, its joysticks are heard through that library and
mapped like keys, and the program's record of each joystick is written with them. Where it
does not, the page is keys only and a controller button the file holds is shown and never
touched.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from nicegui import background_tasks, run, ui

from apps.vpx import bindings, controllers
from common.config import input_registry
from common.failures import why
from common.i18n import t
from manager.kit import panel
from manager.kit.on_page import on_page
from manager.settings.binding_table import Kept, Row, Table, Words

logger = logging.getLogger(__name__)

Entries = list[tuple[Any, Any]]

_DEVICE_KEY = "Key"
_DEVICES = f"{bindings.SECTION}.Devices"

_WORDS = Words(
    how_body="manager.vpx_input.how_body", how_step_2="manager.vpx_input.how_step_2",
    how_relay="manager.vpx_input.how_relay", send_events="manager.vpx_input.send_events",
    send_events_help="manager.vpx_input.send_events.help",
    where_here="manager.vpx_input.where_here", where_device="manager.vpx_input.where_device")
_PAD_WORDS = replace(_WORDS, how_body="", how_step_2="manager.vpx_input.how_step_buttons")


def applies(group: Any) -> bool:
    """Whether a launcher's group is the Input of a program that keeps its key actions in
    the keys this page draws."""
    return group.key == "input" and any(bindings.is_action(one.key) for one in group.settings)


def _token(chord: bindings.Chord) -> str:
    tokens = [one if bindings.is_controller(one) else f"{input_registry.KEY_PREFIX}{one}"
              for one in chord]
    if len(tokens) == 1:
        return tokens[0]
    return f"{input_registry.CHORD_PREFIX}{'+'.join(tokens)})"


def _chord(token: str, with_pads: bool = False) -> bindings.Chord:
    """The members a binding names, or () for anything that is not made of keys - and, where
    `with_pads` is on, the program's joysticks."""
    members = input_registry.chord_members(token) or (token,)
    out = []
    for one in members:
        if one.startswith(input_registry.KEY_PREFIX):
            out.append(one[len(input_registry.KEY_PREFIX):])
        elif with_pads and bindings.is_controller(one):
            out.append(one)
        else:
            return ()
    return tuple(out)


def _name(qualified: str) -> str:
    return qualified.removeprefix(f"{bindings.SECTION}.{bindings.PREFIX}")


def _kept(term: str) -> Kept:
    """How an alternative the page cannot edit reads: a controller's, or a key it has no
    name for."""
    devices = {part.split(";")[0].strip() for part in term.split("&")}
    if devices == {_DEVICE_KEY}:
        return Kept(t("manager.vpx_input.other_key"), t("manager.vpx_input.other_key.help"),
                    pad=False)
    return Kept(t("manager.vpx_input.controller_button"))


def table_rows(group: Any, flat: Mapping[str, str], with_pads: bool = False) -> list[Row]:
    """A row per action, under its heading, in the order the group draws them."""
    by_key = {one.key: one for one in group.settings}
    rows = []
    for heading in group.curated:
        for key in heading.keys:
            one = by_key.get(key)
            if one is None:
                continue
            held = bindings.parse(flat.get(key, ""), one.default, controllers=with_pads)
            rows.append(Row(
                name=_name(key), label=one.label, group=heading.label, holds=False,
                defaults=tuple(_token(one) for one in bindings.parse("", one.default).keys),
                kept=tuple(_kept(term) for term in held.kept)))
    return rows


class Pads:
    """The program's joysticks, heard through its own SDL library while a table wants them.

    Each `open` that answers True is one use, ended by one `close`; the library stays loaded
    while any use is open. What was learned of the joysticks stays after the last close.
    """

    def __init__(self, program: str) -> None:
        self.program = program
        self.known: dict[str, bindings.DeviceInfo] = {}
        self._tap: controllers.Tap | None = None
        self._users = 0
        self._loading = asyncio.Lock()

    def is_open(self) -> bool:
        return self._tap is not None

    async def open(self) -> bool:
        async with self._loading:
            if self._tap is None:
                found = await run.io_bound(controllers.listen, self.program)
                tap, reason = found or (None, "the listener did not answer")
                if tap is None:
                    logger.info("Controller buttons are keys only here: %s", reason)
                    return False
                self._tap = tap
            self._users += 1
        return True

    def close(self) -> None:
        self._users = max(0, self._users - 1)
        if not self._users and self._tap is not None:
            tap, self._tap = self._tap, None
            tap.close()

    def owns(self, member: str) -> bool:
        return bindings.is_controller(member)

    def label(self, member: str) -> tuple[str, str]:
        return controllers.label_of(member, self.known)

    def poll(self) -> tuple[list[tuple[str, bool]], bool]:
        tap = self._tap
        if tap is None:
            return [], False
        presses = [(one.member, one.down) for one in tap.drain()]
        seen = tap.devices()
        changed = any(self.known.get(ident) != info for ident, info in seen.items())
        self.known.update(seen)
        return presses, changed


class Page:
    """One launcher's Input. `flat` is the launcher's ini as keys to text, which `put`
    keeps current as it writes. `program` is where the program is installed, which says
    whether its joysticks can be heard."""

    def __init__(self, library: Any, context: dict[str, Any], group: Any,
                 flat: dict[str, str], program: str = "") -> None:
        self.library = library
        self.context = context
        self.group = group
        self.flat = flat
        self.by_name = {_name(one.key): one for one in group.settings}
        self.pads = Pads(program)
        self.v2 = False

    def rows(self) -> list[Row]:
        return table_rows(self.group, self.flat, self.v2)

    def bound(self) -> dict[str, list[str]]:
        return {name: [_token(one) for one in bindings.parse(
                    self.flat.get(one.key, ""), one.default, controllers=self.v2).keys]
                for name, one in self.by_name.items()}

    def refuse(self, token: str) -> str:
        """Why a press cannot be mapped here, or "" where it can."""
        if input_registry.is_pad(token):
            return t("manager.vpx_input.controllers_note")
        return "" if bindings.writable(_chord(token, self.v2)) \
            else t("manager.vpx_input.unknown_key")

    @on_page
    async def put(self, changes: dict[str, list[str]]) -> bool:
        keys: dict[str, str] = {}
        written: list[bindings.Chord] = []
        back: list[str] = []
        for name, tokens in changes.items():
            one = self.by_name[name]
            current = self.flat.get(one.key, "")
            chords = [_chord(token, self.v2) for token in tokens]
            value = bindings.value_of(chords, current, one.default, controllers=self.v2)
            if not tokens and not value and one.default:
                back.append(one.label)
            if value != current.strip() and (value or one.key in self.flat):
                keys[one.key] = value
                written.extend(chords)
        if keys and self.v2:
            for key, value in bindings.device_writes(
                    written, self.flat.get(_DEVICES, ""), self.pads.known).items():
                if self.flat.get(key) != value:
                    keys[key] = value
        if keys:
            try:
                await run.io_bound(self.library.write_launcher_config,
                                   self.context["launcher"]["launcher_id"], keys)
            except Exception as exc:  # noqa: BLE001 - the reason belongs on the page
                ui.notify(t("said.save_failed"), caption=why(exc), type="negative")
                return False
            self.flat.update(keys)
        if back:
            ui.notify(t("manager.vpx_input.default_back", rows=", ".join(back)),
                      type="warning")
            self.context.pop("config_values", None)
            asyncio.create_task(self.context["rebuild"]())
        return True

    def show(self, holder: ui.element, *, how: bool = True) -> None:
        """Draws the table into `holder` once it is known whether joysticks can be heard,
        which takes the library loading. `how` puts How Mapping Works above it."""
        background_tasks.create(self._show(holder, how))

    async def _show(self, holder: ui.element, how: bool) -> None:
        if self.pads.program:
            self.v2 = await self.pads.open()
        if holder.is_deleted:
            if self.v2:
                self.pads.close()
            return
        try:
            with holder:
                self._table(how)
        except Exception:  # noqa: BLE001 - keys only is still a page
            logger.warning("Could not draw the buttons with controllers", exc_info=True)
            if self.v2:
                self.pads.close()
                self.v2 = False
            holder.clear()
            with holder:
                self._table(how)

    def _table(self, how: bool) -> None:
        rows = self.rows()
        bound = self.bound()
        mapped = any(not input_registry.same_bindings(bound[row.name], list(row.defaults))
                     for row in rows)
        Table(rows, bound, write=self.put, source=t("manager.vpx_input.defaults"),
              shares=True, refuses=self.refuse, how_open=(not mapped) if how else None,
              words=_PAD_WORDS if self.v2 else _WORDS if how else Words(),
              host=self.pads if self.v2 else None)


def entries(context: dict[str, Any], group: Any,
            values: Mapping[str, Any]) -> Entries:
    """The page's rows: the line saying what the table is for, then the table."""
    flat = {key: str((held or {}).get("value") or "") for key, held in values.items()}
    program = str(((context.get("launcher") or {}).get("settings") or {}).get("bin_path") or "")
    page = Page(context["library"], context, group, flat, program)

    def draw() -> None:
        page.show(ui.element("div").classes("w-full"))

    return [panel.intro(t("manager.vpx_input.intro")), (panel.FULL, draw)]
