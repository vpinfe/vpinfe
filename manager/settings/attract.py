"""The Attract Mode group on Frontend Behaviors.

Start After is one picker over two settings: Never is `attract_enabled` off, and any time is
it on with the seconds. The four rows under it cannot act while it reads Never, and Play
Game Sound cannot act while Mute Frontend Audio is on, so those rows are disabled where
they stand rather than the page being drawn again - a redraw puts a long page back at its
top.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from nicegui import ui
from nicegui.elements.mixins.disableable_element import DisableableElement

from common.config import setting_groups
from common.i18n import t
from common.online import theme_service
from common.values import is_truthy
from manager.kit import panel

SECTION = "behavior"
ENABLED = "attract_enabled"
IDLE = "attract_idle"
MUTE = "mute_audio"
SOUND = "attract_audio"
UNDER_START_AFTER = ("attract_step_time", "attract_order", "attract_on_return", SOUND)

NEVER = "0"
NAMED_SECONDS = (30, 60, 120, 300, 600)

Write = Callable[[dict[str, dict[str, Any]]], Awaitable[bool]]


class Attract:
    """What the Attract Mode group needs beyond the schema's own rows."""

    def __init__(self, write: Write, values: dict, options: list[dict], *,
                 writable: bool, theme: str = "", left_to_theme: str = "") -> None:
        by_key = {one["key"]: one for one in options}
        now = dict(values.get(SECTION) or {})
        self.write = write
        self.writable = writable
        self.theme = theme
        self.left_to_theme = left_to_theme
        self.idle_option = by_key.get(IDLE) or {}
        self.on = is_truthy(now.get(ENABLED, (by_key.get(ENABLED) or {}).get("default", True)))
        self.idle = now.get(IDLE, self.idle_option.get("default", 60))
        self.muted = is_truthy(now.get(MUTE, (by_key.get(MUTE) or {}).get("default", False)))
        self.held: dict[str, Any] = {}
        self.sound_line: Any = None
        self.default: Any = None

    # What the page hands to `section_rows`.

    def stands_in(self) -> dict[str, list[tuple[Any, Any]]]:
        """Start After where the switch would be, and nothing where the time was."""
        return {f"{SECTION}.{ENABLED}": [
            (self.idle_option.get("label") or "", self.start_after()), self.default_line()],
            f"{SECTION}.{IDLE}": []}

    def under_heading(self, group: str) -> list[tuple[Any, Any]]:
        """The group's note and, where core's attract does not run under the current theme,
        the line saying so."""
        if group != setting_groups.ATTRACT:
            return []
        rows: list[tuple[Any, Any]] = []
        if said := self.idle_option.get("group_note"):
            rows.append(panel.lede(said))
        if line := self.theme_line():
            rows.append(line)
        return rows

    def saves(self, key: str, save: Callable[[Any], Any]) -> Callable[[Any], Any]:
        """Mute Frontend Audio, kept in step with the row that depends on it."""
        if key != MUTE:
            return save

        async def saved(now: Any) -> bool:
            done = bool(await save(now))
            if done:
                self.muted = is_truthy(now)
                self.relock(SOUND)
            return done

        return saved

    def draws(self, key: str, control: Callable[[], None]) -> Callable[[], None]:
        """A row that can be switched off by another one, drawn so that it can be."""
        if key not in UNDER_START_AFTER:
            return control

        def draw() -> None:
            with ui.element("div").classes("manager-fact-edit") as holder:
                control()
            self.held[key] = holder
            self.relock(key)

        return draw

    def under(self, key: str) -> list[tuple[Any, Any]]:
        """The line under Play Game Sound that says why it is off, drawn hidden."""
        if key != SOUND:
            return []

        def draw() -> None:
            self.sound_line = panel.line(t("config.behavior.attract_audio.muted"))
            self.sound_line.set_visibility(self.muted)

        return [(panel.ASIDE, draw)]

    # Behavior.

    def locked(self, key: str) -> bool:
        return not self.writable or not self.on or (key == SOUND and self.muted)

    def relock(self, *keys: str) -> None:
        for key in keys or tuple(self.held):
            holder = self.held.get(key)
            if holder is None:
                continue
            for one in holder.descendants(include_self=True):
                if isinstance(one, DisableableElement):
                    (one.disable if self.locked(key) else one.enable)()
        if self.sound_line is not None:
            self.sound_line.set_visibility(self.muted)

    def start_after(self) -> Callable[[], None]:
        named = self.named()

        async def picked(chosen: Any) -> bool:
            changes: dict[str, Any]
            if str(chosen) == NEVER:
                changes = {ENABLED: False}
            else:
                changes = {ENABLED: True, IDLE: int(chosen)}
            done = await self.write({SECTION: changes})
            if done:
                self.on = bool(changes[ENABLED])
                if IDLE in changes:
                    self.idle = changes[IDLE]
                self.relock()
                self.refresh_default()
            return done

        return panel.named_number(
            self.idle if self.on else NEVER, named, picked, disabled=not self.writable,
            low=self.idle_option.get("min"), high=self.idle_option.get("max"),
            unit=t("word.seconds"))

    def named(self) -> dict[str, str]:
        """The times that have a name, by their seconds, with Never first."""
        return {NEVER: t("word.never"),
                **{str(one): t(f"config.behavior.{IDLE}.named.{one}") for one in NAMED_SECONDS}}

    def default_said(self) -> str:
        """The default in the picker's own words: Default, or Default: 1 Minute."""
        standard = self.idle_option.get("default", 60)
        if self.on and str(self.idle) == str(standard):
            return t("manager.settings.vpinfe_default")
        words = self.named().get(str(standard)) \
            or t("manager.settings.seconds", value=str(standard))
        return t("manager.settings.vpinfe_default_is", value=words)

    def default_line(self) -> tuple[Any, Callable[[], None]]:
        def draw() -> None:
            self.default = panel.line(self.default_said())

        return (panel.ASIDE, draw)

    def refresh_default(self) -> None:
        if self.default is not None:
            self.default.set_text(self.default_said())

    def theme_line(self) -> tuple[Any, Callable[[], None]] | None:
        """The warn-tone line, by why the current theme does not take core's attract."""
        why = {theme_service.ATTRACT_TWO_X: "config.group.attract.theme_2x",
               theme_service.ATTRACT_OWN: "config.group.attract.theme_own"}.get(
                   self.left_to_theme)
        if why is None:
            return None
        text = t(why, theme=self.theme)

        def draw() -> None:
            with ui.element("div").classes("manager-attention w-full"):
                ui.icon("error_outline").classes("manager-attention-icon")
                ui.label(text).classes("manager-attention-line")

        return (panel.LEDE, draw)


def left_to_theme(listing: dict) -> tuple[str, str]:
    """The current theme's name and why core's attract does not run under it, from the
    themes listing; both blank when nothing is active or it does take core's attract."""
    active: dict = next((one for one in listing.get("themes") or () if one.get("active")), {})
    return str(active.get("name") or ""), str(active.get("attract_left_to_theme") or "")

