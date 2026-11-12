"""Launchers > Visual Pinball X > Input: the binding table over that launcher's ini.

The table is the one VPinFE's own Input page draws, with no holds, since the program has
none, and a press another row has offered as both. It writes this launcher's ini only, and
only the keys: a controller button the file holds is shown and never touched.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

from nicegui import run, ui

from apps.vpx import bindings
from common.config import input_registry
from common.failures import why
from common.i18n import t
from manager.kit import panel
from manager.kit.on_page import on_page
from manager.settings.binding_table import Kept, Row, Table, Words

Entries = list[tuple[Any, Any]]

_DEVICE_KEY = "Key"

_WORDS = Words(
    how_body="manager.vpx_input.how_body", how_step_2="manager.vpx_input.how_step_2",
    how_relay="manager.vpx_input.how_relay", send_events="manager.vpx_input.send_events",
    send_events_help="manager.vpx_input.send_events.help",
    where_here="manager.vpx_input.where_here", where_device="manager.vpx_input.where_device")


def applies(group: Any) -> bool:
    """Whether a launcher's group is the Input of a program that keeps its key actions in
    the keys this page draws."""
    return group.key == "input" and any(bindings.is_action(one.key) for one in group.settings)


def _token(chord: bindings.Chord) -> str:
    tokens = [f"{input_registry.KEY_PREFIX}{code}" for code in chord]
    if len(tokens) == 1:
        return tokens[0]
    return f"{input_registry.CHORD_PREFIX}{'+'.join(tokens)})"


def _chord(token: str) -> bindings.Chord:
    """The key codes a binding names, or () for anything that is not made of keys."""
    members = input_registry.chord_members(token) or (token,)
    if not all(one.startswith(input_registry.KEY_PREFIX) for one in members):
        return ()
    return tuple(one[len(input_registry.KEY_PREFIX):] for one in members)


def _name(qualified: str) -> str:
    return qualified.removeprefix(f"{bindings.SECTION}.{bindings.PREFIX}")


def _kept(term: str) -> Kept:
    """How an alternative the page cannot edit reads: a controller's, or a key it has no
    name for."""
    devices = {part.split(";")[0].strip() for part in term.split("&")}
    if devices == {_DEVICE_KEY}:
        return Kept(t("manager.vpx_input.other_key"), t("manager.vpx_input.other_key.help"),
                    pad=False)
    return Kept(t("manager.vpx_input.controller_button"),
                t("manager.vpx_input.controller_button.help"))


def table_rows(group: Any, flat: Mapping[str, str]) -> list[Row]:
    """A row per action, under its heading, in the order the group draws them."""
    by_key = {one.key: one for one in group.settings}
    rows = []
    for heading in group.curated:
        for key in heading.keys:
            one = by_key.get(key)
            if one is None:
                continue
            held = bindings.parse(flat.get(key, ""), one.default)
            rows.append(Row(
                name=_name(key), label=one.label, group=heading.label, holds=False,
                defaults=tuple(_token(one) for one in bindings.parse("", one.default).keys),
                kept=tuple(_kept(term) for term in held.kept)))
    return rows


class Page:
    """One launcher's Input. `flat` is the launcher's ini as keys to text, which `put`
    keeps current as it writes."""

    def __init__(self, library: Any, context: dict[str, Any], group: Any,
                 flat: dict[str, str]) -> None:
        self.library = library
        self.context = context
        self.group = group
        self.flat = flat
        self.by_name = {_name(one.key): one for one in group.settings}

    def bound(self) -> dict[str, list[str]]:
        return {name: [_token(one) for one in
                       bindings.parse(self.flat.get(one.key, ""), one.default).keys]
                for name, one in self.by_name.items()}

    def refuse(self, token: str) -> str:
        """Why a press cannot be mapped here, or "" where it can."""
        if input_registry.is_pad(token):
            return t("manager.vpx_input.controllers_note")
        return "" if bindings.writable(_chord(token)) else t("manager.vpx_input.unknown_key")

    @on_page
    async def put(self, changes: dict[str, list[str]]) -> bool:
        keys: dict[str, str] = {}
        back: list[str] = []
        for name, tokens in changes.items():
            one = self.by_name[name]
            current = self.flat.get(one.key, "")
            value = bindings.value_of([_chord(one) for one in tokens], current, one.default)
            if not tokens and not value and one.default:
                back.append(one.label)
            if value != current.strip() and (value or one.key in self.flat):
                keys[one.key] = value
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


def entries(context: dict[str, Any], group: Any,
            values: Mapping[str, Any]) -> Entries:
    """The page's rows: the line saying what the table is for, then the table."""
    flat = {key: str((held or {}).get("value") or "") for key, held in values.items()}
    page = Page(context["library"], context, group, flat)

    def draw() -> None:
        rows = table_rows(group, flat)
        bound = page.bound()
        mapped = any(not input_registry.same_bindings(bound[row.name], list(row.defaults))
                     for row in rows)
        Table(rows, bound, write=page.put, source=t("manager.vpx_input.defaults"),
              shares=True, refuses=page.refuse, how_open=not mapped, words=_WORDS)

    return [panel.intro(t("manager.vpx_input.intro")), (panel.FULL, draw)]

