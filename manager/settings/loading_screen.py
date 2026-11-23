"""The Loading Screen and Launch Audio rows on Frontend Behaviors, under the Launch heading.

Each is a pair of dropdowns: what to play, and what a table gets when that has nothing for
it. The fallback is drawn only where the first can be empty, named for what is missing, and
goes back to its starting choice whenever the first one changes. Under a choice that
plays one file on every table the file shows with a Change button.

Rows that cannot act are disabled where they stand rather than the page being drawn again -
a redraw puts a long page back at its top. That is the case for all of them while the
theme draws its own loading screen, for the video ending while no video can play, and for
Launch Audio while Mute Frontend Audio is on.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from nicegui import ui
from nicegui.elements.mixins.disableable_element import DisableableElement

from common.config import loading_choices as choices
from common.config import setting_groups
from common.i18n import t
from common.media import launch_media
from common.media.launch_lines import Theme
from common.values import is_truthy
from manager.kit import dropdown, panel, verbs
from manager.library import mediasource

SECTION = choices.SECTION
ENDS = choices.ENDS_KEY
MUTE = "mute_audio"

Write = Callable[[dict[str, dict[str, Any]]], Awaitable[bool]]

_FILE_KEYS = {choices.VIDEO: choices.VIDEO_FILE_KEY, choices.IMAGE: choices.IMAGE_FILE_KEY,
              choices.AUDIO: choices.SOUND_FILE_KEY}
_ICONS = {choices.VIDEO: "movie", choices.AUDIO: "music_note"}
_THING = {choices.VIDEO: "video", choices.IMAGE: "image", choices.AUDIO: "audio"}


def _lock(holder: Any, locked: bool) -> None:
    for one in holder.descendants(include_self=True):
        if isinstance(one, DisableableElement):
            (one.disable if locked else one.enable)()


@dataclass
class _Parts:
    """What a pair has drawn, kept so it can be changed in place."""

    main: Any = None
    main_holder: Any = None
    main_below: Any = None
    fall: Any = None
    fall_holder: Any = None
    fall_label: Any = None
    fall_below: Any = None
    quiet: bool = False
    drawn: bool = field(default=False)


class _Pair:
    """One of the two pairs of dropdowns."""

    def __init__(self, owner: LoadingScreen, *, sound: bool, main: str, fallback: str) -> None:
        self.owner = owner
        self.sound = sound
        self.main = main
        self.fallback = fallback
        self.parts = _Parts()

    # What the choices are.

    @property
    def main_key(self) -> str:
        return choices.SOUND_KEY if self.sound else choices.SCREEN_KEY

    @property
    def fallback_key(self) -> str:
        return choices.SOUND_FALLBACK_KEY if self.sound else choices.FALLBACK_KEY

    def label(self, choice: str) -> str:
        return t(f"config.behavior.{self.main_key}.choice.{choice}")

    def family(self, choice: str) -> str:
        return choices.AUDIO if self.sound else choices.BY_ID[choice].family

    def is_theme(self, choice: str) -> bool:
        return choice == choices.THEME_SOUND if self.sound else choices.BY_ID[choice].theme

    def is_mine(self, choice: str) -> bool:
        return choice == choices.MY_SOUND if self.sound else choices.BY_ID[choice].mine

    def can_be_missing(self, choice: str) -> bool:
        return (choices.sound_can_be_missing(choice) if self.sound
                else choices.can_be_missing(choice))

    def main_options(self) -> dict[str, str]:
        return {one: self.label(one)
                for one in (choices.SOUND_CHOICES if self.sound else choices.SCREEN_CHOICES)}

    def fallback_options(self) -> dict[str, str]:
        found = (choices.sound_fallback_choices(self.main) if self.sound
                 else choices.fallback_choices(self.main))
        return {one: self.label(one) for one in found}

    def starting_fallback(self, main: str) -> str:
        return (choices.SOUND_FALLBACK_START.get(main, choices.SILENCE) if self.sound
                else choices.FALLBACK_START.get(main, choices.SPINNER))

    def sections(self) -> dict[str, str]:
        if self.sound:
            return {}
        return {one.id: t(f"manager.loading.group.{one.group}") for one in choices.CHOICES}

    def noun(self, choice: str) -> str:
        if self.sound:
            return t("media.kind.audio_launch.label")
        one = choices.BY_ID[choice]
        if one.kind == "playfield":
            return t("manager.loading.playfield_image")
        return t("media.kind.loading.label" if one.family == choices.VIDEO
                 else "media.kind.loading_image.label")

    def fallback_words(self) -> str:
        whose = "theme" if self.is_theme(self.main) else "table"
        return t(f"manager.loading.fallback.{whose}", noun=self.noun(self.main))

    # The rows.

    def rows(self) -> list[tuple[Any, Any]]:
        label = t("config.behavior.launch_audio.label" if self.sound
                  else "config.behavior.loading_screen.label")
        return [(label, self._draw_main), (panel.ASIDE, self._draw_main_below),
                (self.fallback_words(), self._draw_fallback),
                (panel.ASIDE, self._draw_fallback_below)]

    def _select(self, options: dict[str, str], value: str,
                on_change: Callable[[Any], Any]) -> tuple[Any, dropdown.Select]:
        with ui.element("div").classes("manager-fact-edit") as holder:
            control = dropdown.Select(options, value=value, on_change=on_change,
                                      sections=self.sections())
        return holder, control

    def _draw_main(self) -> None:
        self.parts.main_holder, self.parts.main = self._select(
            self.main_options(), self.main, self._picked_main)
        self.owner.relock()

    def _draw_main_below(self) -> None:
        self.parts.main_below = ui.context.slot.parent
        self._fill(self.parts.main_below, "main")

    def _draw_fallback(self) -> None:
        grid = ui.context.slot.parent
        self.parts.fall_label = grid.default_slot.children[-1]
        self.parts.fall_holder, self.parts.fall = self._select(
            self.fallback_options(), self.fallback, self._picked_fallback)
        self._show_fallback()
        self.owner.relock()

    def _draw_fallback_below(self) -> None:
        self.parts.fall_below = ui.context.slot.parent
        self._fill(self.parts.fall_below, "fall")
        self._show_fallback()

    # What is under a dropdown.

    def _fill(self, container: Any, which: str) -> None:
        container.clear()
        choice = self.main if which == "main" else self.fallback
        with container:
            for line in self._lines(choice, which):
                if isinstance(line, str):
                    panel.line(line)
                else:
                    line()
        _lock(container, self.owner.locked(self.sound))

    def _lines(self, choice: str, which: str) -> list[Any]:
        lines: list[Any] = []
        family = self.family(choice)
        owner = self.owner
        if self.is_theme(choice):
            lines += self._theme_lines(choice)
        elif self.is_mine(choice):
            lines.append(self._file_row(family, which))
            lines.append(t("manager.loading.shows_every" if family == choices.IMAGE
                           else "manager.loading.plays_every"))
        elif not self.sound and choice == choices.SPINNER:
            lines.append(t("manager.loading.spinner_line"))
        elif not self.sound and choice == choices.OFF:
            lines.append(t("manager.loading.off_line"))
        elif self.sound and choice == choices.SOUND_OFF:
            lines.append(t("manager.loading.silent_line"))
        if which == "main":
            if self.sound and owner.muted:
                lines.append(t("config.behavior.launch_audio.muted"))
        elif self.can_be_missing(choice):
            lines.append(self._last_step())
        return lines

    def _theme_lines(self, choice: str) -> list[str]:
        theme = self.owner.theme
        family = self.family(choice)
        if not theme.name:
            return []
        if theme.has(family):
            return [t("manager.loading.theme_has", theme=theme.name),
                    t("manager.loading.theme_changes")]
        thing = t(f"manager.loading.thing.{_THING[family]}")
        return [t("manager.loading.theme_none", theme=theme.name, thing=thing),
                t("manager.loading.shows_later" if family == choices.IMAGE
                  else "manager.loading.plays_later")]

    def _last_step(self) -> str:
        if self.sound:
            return t("manager.loading.last_sound", a=self._whose(self.main),
                     b=self._whose(self.fallback))
        both = self.main != choices.TABLE_PLAYFIELD and self.fallback != choices.TABLE_PLAYFIELD
        rest = t("manager.loading.rest_both" if both else "manager.loading.rest_spinner")
        if not self.is_theme(self.main) and not self.is_theme(self.fallback):
            return t("manager.loading.last_tables", rest=rest)
        return t("manager.loading.last_mixed", a=self._whose(self.main),
                 b=self._whose(self.fallback), rest=rest)

    def _whose(self, choice: str) -> str:
        return t("manager.loading.whose.theme" if self.is_theme(choice)
                 else "manager.loading.whose.table")

    def _file_row(self, family: str, which: str) -> Callable[[], None]:
        def draw() -> None:
            name = self.owner.files.get(family, "")
            with ui.row().classes("items-center gap-2 no-wrap"):
                self._thumb(family, name)
                panel.line(name, classes="manager-help truncate min-w-0")
                panel.action(
                    t("manager.loading.change"),
                    lambda: self.owner.choose_file(
                        family, lambda stored: self._chose(family, stored)),
                    icon=verbs.EDIT, hint=t(f"manager.launchmedia.choose.{family}"))()

        return draw

    def _thumb(self, family: str, name: str) -> None:
        path = launch_media.path_of(name)
        if family == choices.IMAGE and path is not None:
            ui.image(self.owner.library.disk_file_url(str(path))).classes(
                "rounded shrink-0").style("width: 40px; height: 24px; object-fit: cover")
        else:
            ui.icon(_ICONS[family]).classes("shrink-0")

    def _show_fallback(self) -> None:
        show = self.can_be_missing(self.main)
        for one in (self.parts.fall_label, self.parts.fall_holder, self.parts.fall_below):
            if one is not None:
                one.set_visibility(show)

    # What a pick does.

    def _put(self, control: Any, value: str) -> None:
        if control is None or control.value == value:
            return
        self.parts.quiet = True
        try:
            control.set_value(value)
        finally:
            self.parts.quiet = False

    async def _picked_main(self, event: Any) -> None:
        chosen = str(event.value)
        if self.parts.quiet or chosen == self.main:
            return
        if self.is_mine(chosen) and not self.owner.files.get(self.family(chosen)):
            self._put(self.parts.main, self.main)
            await self.owner.choose_file(
                self.family(chosen), lambda name: self._take(chosen, name, main=True))
            return
        await self._take(chosen, None, main=True)

    async def _picked_fallback(self, event: Any) -> None:
        chosen = str(event.value)
        if self.parts.quiet or chosen == self.fallback:
            return
        if self.is_mine(chosen) and not self.owner.files.get(self.family(chosen)):
            self._put(self.parts.fall, self.fallback)
            await self.owner.choose_file(
                self.family(chosen), lambda name: self._take(chosen, name, main=False))
            return
        await self._take(chosen, None, main=False)

    async def _take(self, chosen: str, name: str | None, *, main: bool) -> None:
        changes: dict[str, Any] = {self.main_key if main else self.fallback_key: chosen}
        if name:
            changes[_FILE_KEYS[self.family(chosen)]] = name
        start = self.starting_fallback(chosen)
        if main and self.can_be_missing(chosen):
            changes[self.fallback_key] = start
        if not await self.owner.write({SECTION: changes}):
            self._put(self.parts.main if main else self.parts.fall,
                      self.main if main else self.fallback)
            return
        if name:
            self.owner.files[self.family(chosen)] = name
        if main:
            self.main = chosen
            if self.can_be_missing(chosen):
                self.fallback = start
        else:
            self.fallback = chosen
        self.refresh()
        self.owner.changed()

    async def _chose(self, family: str, name: str) -> None:
        if not await self.owner.write({SECTION: {_FILE_KEYS[family]: name}}):
            return
        self.owner.files[family] = name
        self.owner.redraw_below()

    # Keeping what is drawn in step.

    def refresh(self) -> None:
        """After the first dropdown changed: the fallback's name, choices and visibility, and
        the lines under both."""
        parts = self.parts
        if parts.fall is not None:
            if parts.fall_label is not None:
                parts.fall_label.set_text(self.fallback_words())
            parts.quiet = True
            try:
                parts.fall.set_options(self.fallback_options(), value=self.fallback)
            finally:
                parts.quiet = False
        self._show_fallback()
        self.redraw_below()

    def redraw_below(self) -> None:
        for container, which in ((self.parts.main_below, "main"),
                                 (self.parts.fall_below, "fall")):
            if container is not None:
                self._fill(container, which)

    def relock(self) -> None:
        locked = self.owner.locked(self.sound)
        for holder in (self.parts.main_holder, self.parts.fall_holder,
                       self.parts.main_below, self.parts.fall_below):
            if holder is not None:
                _lock(holder, locked)


class LoadingScreen:
    """What the Launch rows need beyond the schema's own rows."""

    def __init__(self, write: Write, library: Any, values: dict, options: list[dict], *,
                 writable: bool, theme: Theme | None = None) -> None:
        by_key = {one["key"]: one for one in options}
        now = dict(values.get(SECTION) or {})

        def current(key: str) -> Any:
            return now.get(key, (by_key.get(key) or {}).get("default"))

        self.write = write
        self.library = library
        self.writable = writable
        self.theme = theme or Theme()
        self.muted = is_truthy(current(MUTE))
        self.files = {kind: self._kept(str(current(_FILE_KEYS[kind]) or ""))
                      for kind in launch_media.KINDS}
        screen = choices.screen_or_default(str(current(choices.SCREEN_KEY)))
        sound = choices.sound_or_default(str(current(choices.SOUND_KEY)))
        self.screen = _Pair(self, sound=False, main=screen, fallback=choices.fallback_or_default(
            str(current(choices.FALLBACK_KEY)), screen))
        self.sound = _Pair(self, sound=True, main=sound,
                           fallback=choices.sound_fallback_or_default(
                               str(current(choices.SOUND_FALLBACK_KEY)), sound))
        self.held: dict[str, Any] = {}

    @staticmethod
    def _kept(name: str) -> str:
        return name if launch_media.path_of(name) else ""

    # What the page hands to `section_rows`.

    def stands_in(self) -> dict[str, list[tuple[Any, Any]]]:
        """The two pairs where their first dropdowns would be, and nothing where the
        settings that only they use were."""
        return {
            f"{SECTION}.{choices.SCREEN_KEY}": self.screen.rows(),
            f"{SECTION}.{choices.FALLBACK_KEY}": [],
            f"{SECTION}.{choices.VIDEO_FILE_KEY}": [],
            f"{SECTION}.{choices.IMAGE_FILE_KEY}": [],
            f"{SECTION}.{choices.SOUND_KEY}": self.sound.rows(),
            f"{SECTION}.{choices.SOUND_FALLBACK_KEY}": [],
            f"{SECTION}.{choices.SOUND_FILE_KEY}": []}

    def under_heading(self, group: str) -> list[tuple[Any, Any]]:
        """The line saying why these rows are off, where the theme draws its own screen."""
        if group != setting_groups.LAUNCH or not self.theme.own:
            return []

        def draw() -> None:
            with ui.element("div").classes("manager-attention w-full"):
                ui.icon("error_outline").classes("manager-attention-icon")
                ui.label(t("manager.loading.own", theme=self.theme.name)) \
                    .classes("manager-attention-line")

        return [(panel.LEDE, draw)]

    def saves(self, key: str, save: Callable[[Any], Any]) -> Callable[[Any], Any]:
        """The mute row, kept in step with the rows under it."""
        if key != MUTE:
            return save

        async def saved(now: Any) -> bool:
            done = bool(await save(now))
            if not done:
                return done
            self.muted = is_truthy(now)
            self.redraw_below()
            self.relock()
            return done

        return saved

    def draws(self, key: str, control: Callable[[], None]) -> Callable[[], None]:
        """The row for the video ending, drawn so that it can be switched off."""
        if key != ENDS:
            return control

        def draw() -> None:
            with ui.element("div").classes("manager-fact-edit") as holder:
                control()
            self.held[key] = holder
            self.relock()

        return draw

    def under(self, key: str) -> list[tuple[Any, Any]]:
        return []

    # Behavior.

    def locked(self, sound: bool) -> bool:
        return not self.writable or self.theme.own or (sound and self.muted)

    def video_can_play(self) -> bool:
        main, fallback = self.screen.main, self.screen.fallback
        if choices.BY_ID[main].family == choices.VIDEO:
            return True
        return choices.can_be_missing(main) and choices.BY_ID[fallback].family == choices.VIDEO

    def ends_locked(self) -> bool:
        return self.locked(False) or not self.video_can_play()

    def relock(self) -> None:
        holder = self.held.get(ENDS)
        if holder is not None:
            _lock(holder, self.ends_locked())
        self.screen.relock()
        self.sound.relock()

    def changed(self) -> None:
        """A choice changed: what depends on it is switched on or off where it stands."""
        self.relock()

    def redraw_below(self) -> None:
        self.screen.redraw_below()
        self.sound.redraw_below()

    async def choose_file(self, family: str, done: Callable[[str], Awaitable[None]]) -> None:
        """The picker for the files a person adds, over the one in use."""
        mediasource.open_launch_media_sources(
            self.library, family, done, now=self.files.get(family, ""))
