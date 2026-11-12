"""Settings: the panel, with the install as its subject.

A grouped rail beside one open page, which is the shape the details pane already uses -
the index is the map of the product, and it is the thing VPin Studio gets right about
its preferences even though it throws the rest of the app away to show it.

Everything on a page is drawn through `manager/kit/panel.py`. A config control is the one
place that carries its explanation beneath it rather than in a tooltip: a key's name
says what it is called, not what turning it off costs.

One settings grammar, wherever a page is reached from. `build_system` draws this
install's own and the device panel draws another machine's, from one declaration and
through one renderer - what differs is only which install answers.
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from nicegui import run, ui

from common import extensions, tokens
from common.backups import folder as backups_folder
from common.config import config_schema, input_registry, path_state
from common.config.labels import field_label, humanize
from common.devices import install_identity, serving
from common.extensions import host as extension_host
from common.failures import why
from common.games.asset_registry import ALWAYS_KEPT, ASSET_SPECS
from common.host import (
    addresses,
    display_service,
    frontend_browser,
    key_reader,
    key_simulator,
    tools,
)
from common.i18n import t
from common.media.media_specs import MEDIA_SPECS, NAMED_TOKENS, VIDEO_FAMILY, media_label_map
from common.online import theme_source_checks as online_checks
from common.settings import feature_checks
from common.vpinfe_version import get_version
from manager.backups import backups_move
from manager.devices import browser_state
from manager.extensions import ext_page
from manager.frontend import theme_card, theme_sources
from manager.kit import (
    appearance_picker,
    busy,
    confirm,
    deeplink,
    folder_picker,
    link_tab,
    offload,
    panel,
    undo,
    verbs,
    when,
)
from manager.kit.data import Library
from manager.kit.on_page import on_page
from manager.overview import needs
from manager.security import security
from manager.settings import (
    attract,
    binding_table,
    chrome_options,
    displays,
    dof,
    folders,
    input_page,
    placement_panel,
    recording,
    screens,
    shelves,
    vpx_displays,
)
from manager.settings import commands as commands_help
from manager.setup import setup as setup_page

logger = logging.getLogger("vpinfe.manager.settings.settings")

# Wider than the pane's rail: these are page names rather than section names, and
# "Virtual Pinball Spreadsheet" ellipses at the pane's width.
RAIL_PX = 230

KINDS_NOTE = (t("manager.settings.kinds_note"), t("manager.settings.kinds_names_note"))
ASSETS_NOTE = t("manager.settings.assets_note")

# Every source that ships is listed, switched off included: "why is that catalog not
# coming up" is answered by seeing it sitting there off.
SOURCES_NOTE = t("manager.settings.sources_note")

DISCOVERY_PAGE = "library.discovery"
# The setting that shows updates: the number on the rail, the shelf and the notices.
SHOW_COUNT = "show_updates"
VERSION_WAIT = 2.0

# This install's, unlike the checks: a device row is about the machine it is read on.
OVERVIEW_NOTE = t("manager.settings.overview_note")


class Refusal:
    """The field a refused write says why at, instead of in a toast."""

    def __init__(self) -> None:
        self.control: Any = None

    def attach(self, control: Any) -> None:
        self.control = control

    def show(self, reason: str) -> None:
        control = self.control
        if control is None or control.is_deleted:
            return
        if reason:
            control.props["error"] = True
            control.props["error-message"] = reason
        else:
            control.props(remove="error error-message")
        control.update()

    def reason_for(self, exc: Exception, section: str, key: str) -> str:
        """What the API said about this setting, or empty where it said nothing about it."""
        refused = getattr(exc, "details", {}).get("settings") or {}
        return str(refused.get(f"{section}.{key}") or "")


@on_page
async def _write_many(library: Library, changes: dict[str, dict[str, Any]],
                      refusal: Refusal | None = None) -> bool:
    """Settings, written when they are set. One request, so a change that is several
    settings is kept whole or not at all.

    No save bar: a control that changes a value writes it, which is what every other
    control in the Manager does. The reason a write failed is the API's own message, never
    the status line - `raise_for_status` throws the body away.

    With a `refusal`, a refusal of its one setting is said at that field and not in a toast.
    """
    try:
        await run.io_bound(library.put_config, changes)
    except Exception as exc:  # noqa: BLE001 - the reason belongs on the page
        if refusal is not None:
            (section, entries), = changes.items()
            reason = refusal.reason_for(exc, section, next(iter(entries)))
            if reason:
                refusal.show(reason)
                return False
        ui.notify(t("manager.settings.save_failed"), caption=why(exc), type="negative")
        return False
    if refusal is not None:
        refusal.show("")
    return True


async def _write(library: Library, section: str, key: str, value: Any,
                 refusal: Refusal | None = None) -> bool:
    return await _write_many(library, {section: {key: value}}, refusal)


# Tools a setting can ask for by name, where a control cannot do the job. Routed the way
# `type` is, so declaring one is a line in the schema rather than a branch here. A name
# nothing serves falls back to the control for its type: a surface that has not been
# taught the tool should still be able to edit the value badly rather than not at all.
def _frontend_theme(option: dict, value: Any, save: Callable[[Any], Any], *,
                    suggestions: dict[str, Any] | None = None,
                    **_: Any) -> Callable[[], None]:
    """The active theme as the Themes grid draws it, and the way to where it is chosen."""
    known = (suggestions or {}).get(config_schema.EDITOR_FRONTEND_THEME) or {}

    def draw() -> None:
        theme_card.active(str(value or ""), known.get(str(value or "")))

    return draw


def _thumbnail(option: dict, value: Any, save: Callable[[Any], Any], *,
               writable: bool = True, suggestions: dict[str, Any] | None = None,
               **_: Any) -> Callable[[], None]:
    """The kinds the library keeps, and the one already chosen."""
    hidden = (suggestions or {}).get(config_schema.EDITOR_THUMBNAIL) or set()
    value = value_for(option, value)
    named = option.get("choice_labels") or {}
    offered = {choice: named.get(choice, choice) for choice in option.get("choices") or ()
               if choice not in hidden or choice == str(value)}
    return panel.select(offered, str(value or ""), lambda e: save(e.value),
                        disabled=not writable)


def _open_links(option: dict, value: Any, save: Callable[[Any], Any], *,
                writable: bool = True, **_: Any) -> Callable[[], None]:
    """The two ways a link to another site can open. The page script reads the choice on
    every click, so the pages already open are told when it is saved."""
    named = option.get("choice_labels") or {}
    offered = {choice: named.get(choice, choice) for choice in option.get("choices") or ()}

    @on_page
    async def chosen(event: Any) -> None:
        if await save(event.value):
            ui.run_javascript(link_tab.change_js(link_tab.way(event.value)))

    return panel.select(offered, link_tab.way(value), chosen, disabled=not writable)


EDITORS: dict[str, Callable[..., Callable[[], None]]] = {
    config_schema.EDITOR_MANAGER_APPEARANCE: appearance_picker.tiles,
    config_schema.EDITOR_FRONTEND_THEME: _frontend_theme,
    config_schema.EDITOR_THUMBNAIL: _thumbnail,
    config_schema.EDITOR_CAPTURE_COMMAND: recording.command_editor,
    config_schema.EDITOR_CHROME_LEAVE_OUT: chrome_options.leave_out,
    config_schema.EDITOR_OPEN_LINKS: _open_links,
}


def secret_setting(option: dict, held: Any, save: Callable[[Any], Any], *,
                   writable: bool = True) -> Callable[[], None]:
    """A setting the API answers as set or not, drawn as an account's secret is: an empty
    box that says Set or Not set, and Clear once one is saved.

    `held` is that answer. Drawing it as text would put "True" in the box.
    """
    async def typed(text: str) -> str:
        text = text.strip()
        if not text:
            return ""
        return "" if await save(text) else t("manager.settings.save_failed")

    async def clear() -> None:
        await save("")

    is_set = held if isinstance(held, bool) else bool(str(held or "").strip())
    return ext_page.secret_control(
        str(option.get("label") or humanize(option["key"])), is_set, typed, clear,
        detail=t("manager.settings.secret_clear_detail"), disabled=not writable)


def value_for(option: dict, raw: Any) -> Any:
    """A stored value as its declared type, or the default where it will not fit.

    Values read out of a program's own file are strings, and not every one of them
    matches what that program says the setting is: an integer written once as `0.0`
    stays that way, and a closed set of answers has no option spelled "". A settings
    page that raises on either is a page that will not draw at all, so the declared
    type wins and a value that cannot meet it falls back to the default.
    """
    said = str(raw if raw is not None else "").strip()
    kind = str(option.get("type") or "")
    if not said:
        said = str(option.get("default") or "").strip()
    if not said:
        return False if kind == "bool" else ""

    if kind == "bool":
        return said.strip().lower() not in ("0", "false", "no", "off", "")
    if kind in ("int", "number"):
        try:
            number = float(said)
        except ValueError:
            return _fallback_number(option, kind)
        return int(number) if kind == "int" else number
    if kind == "choice":
        offered = option.get("choices") or {}
        keys = offered.keys() if isinstance(offered, dict) else offered
        if said not in keys:
            return str(option.get("default") or "") if str(
                option.get("default") or "") in keys else next(iter(keys), "")
    return said


def _default_number(option: dict) -> float | None:
    """The declared default as a number, where there is one: what a box showing it selects."""
    default = option.get("default")
    try:
        number = float(str(default if default is not None else "").strip())
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def _fallback_number(option: dict, kind: str) -> Any:
    try:
        number = float(str(option.get("default") or "").strip() or 0)
    except ValueError:
        number = 0
    return int(number) if kind == "int" else number


def _browse_path(browse: Callable[..., dict] | None, kind: str, suffixes: tuple[str, ...],
                 default: str = "") -> Callable[[str], Any] | None:
    """`panel.field`'s own `browse`, opening the picker at the field's current value. A
    field holding its default opens at the nearest folder of it that is there."""
    if browse is None:
        return None

    async def browsed(current: str) -> str | None:
        return await folder_picker.pick_path(browse, current or default, kind=kind,
                                             suffixes=suffixes,
                                             nearest=bool(default) and current in ("", default))

    return browsed


# Where the key under `dof` comes from: the cabinet's page on this site.
DOF_CONFIG_TOOL = "https://configtool.vpuniverse.com/app/home"

# What a suggested setting can say besides one of its suggestions, where the empty value
# is an answer of its own.
_FIRST_CHOICES: dict[str, Callable[[], dict[str, str]]] = {
    config_schema.SUGGEST_COLLECTIONS: panel.open_on_choices,
    config_schema.SUGGEST_WHEEL_SETS: lambda: {"": t("manager.settings.wheel_set_none")},
}


def control_for(option: dict, value: Any, save: Callable[[Any], Any], *,
                writable: bool = True, refill: Callable[[], None] | None = None,
                check: dict | None = None,
                suggestions: dict[str, Any] | None = None,
                section_values: dict[str, Any] | None = None,
                varies: bool = False,
                browse: Callable[..., dict] | None = None,
                refusal: Refusal | None = None,
                saved: Callable[[], Awaitable[None]] | None = None) -> Callable[[], None]:
    """The control a declared value's type asks for.

    Driven by the declaration, never by the key's name: something added to the schema -
    or to an app's launcher fields - renders here without this file being touched.

    `save` takes the new value and answers whether it was written. Passed in rather than
    built from a section and a key, because the same grammar now draws two things that
    are stored quite differently: a setting goes to a config section, and a launcher
    field goes to a launcher.

    `varies` draws it holding no value at all, not even the words for a blank one: it
    stands for several values that differ.

    `browse` is the Manager's own `library.folders`, offered to whichever field declares
    a `path` so its control can draw Browse. A call site with nothing path-shaped to draw
    still passes it - the field, not the caller, decides whether it is used.

    `refusal` is where a number says why its value was refused, beside the number.

    `saved` is awaited after a path or a suggested value is written, so the page can read
    what the write changed and draw again only what that touched. `refill` draws the page
    again unasked, for the controls whose write always changes something on it.
    """
    editor = EDITORS.get(str(option.get("editor") or ""))
    if editor is not None:
        return editor(option, value, save, section=section_values or {},
                      writable=writable, refill=refill, suggestions=suggestions)

    kind = option.get("type")
    if varies:
        value, option = None, {**option, "blank": ""}
    off = not writable
    found = check or {}
    state = panel.value_state(str(found.get("state") or ""),
                              str(found.get("reason") or ""))

    if option.get("suggest") == config_schema.SUGGEST_SCREENS:
        if found_screens := (suggestions or {}).get(config_schema.SUGGEST_SCREENS):
            return screens.picker(found_screens, value, save, disabled=off, refill=refill,
                                  blank=not str(option.get("default") or ""))
    elif option.get("suggest"):
        # Offered and not imposed: what produced the list can be wrong - a network that
        # filters multicast has nothing on it - so anything may still be typed.
        offered = (suggestions or {}).get(option["suggest"]) or {}

        # Only where this setting is carrying a mark. The page is read again to refresh
        # one - `network.library_url` is what a feature check points at, and answering it
        # has to clear the badge that led here. Every other suggested setting changes
        # nothing the page is showing about anything else.
        marks = bool(found)

        async def save_suggested(event: Any) -> None:
            if await save(str(event.value or "").strip()) and marks and saved is not None:
                await saved()

        return panel.combo(str(value or ""), offered, save_suggested, disabled=off,
                           status=state, clearable=option.get("clearable", True),
                           placeholder=str(option.get("blank") or ""),
                           first=_FIRST_CHOICES.get(option["suggest"], lambda: None)())

    if kind == "bool":
        # Through the declared type, not `bool()`. A setting nobody has stored answers
        # with its declared default, and that is the string "false" - which is a
        # perfectly true string, and drew every untouched switch as on.
        return panel.switch(None if varies else bool(value_for(option, value)),
                            lambda e: save(bool(e.value)), disabled=off)
    if kind == "choice" and option.get("choices"):
        # Passed through when it is already a mapping. A theme names its choices
        # `{value: label}`, and flattening that to a list would put the stored value on
        # screen where the label belongs.
        choices = option["choices"]
        named = option.get("choice_labels") or {}
        if named and not isinstance(choices, dict):
            choices = {value: named.get(value, value) for value in choices}
        shown = "" if varies else str(value_for(option, value))
        held = "" if varies else str(value if value is not None else "").strip()
        if held and held not in choices:
            # A value the file holds that the list does not offer is what is in effect, so
            # it is drawn as itself rather than as the default.
            choices = {**(choices if isinstance(choices, dict)
                          else {one: one for one in choices}), held: held}
            shown = held
        if option.get("as_buttons") and isinstance(choices, dict):
            return panel.choice_buttons(choices, shown, lambda e: save(e.value), disabled=off)
        helped = option.get("choice_help") or {}
        labels = choices if isinstance(choices, dict) else {one: one for one in choices}
        return panel.select(choices if isinstance(choices, dict) else list(choices),
                            shown, lambda e: save(e.value), disabled=off,
                            describes=option.get("describes")
                            or {labels.get(one, one): said for one, said in helped.items()}
                            or None,
                            tags=option.get("tags") or None)
    if kind == "color":
        return panel.swatch(str(value or ""), save, disabled=off)
    blank = str(option.get("blank") or "")
    if kind in ("int", "number") and option.get("named"):
        return panel.named_number(value, option["named"], save, disabled=off,
                                  whole=kind == "int", low=option.get("min"),
                                  high=option.get("max"), unit=_unit_word(option))
    if kind == "int":
        return panel.number(
            value, lambda e: save("" if e.value is None else int(e.value)), disabled=off,
            low=option.get("min"), high=option.get("max"), unit=_unit_word(option),
            hint=_range_hint(option),
            placeholder=blank, on_control=refusal.attach if refusal else None,
            on_leave=refusal is not None, select_at=_default_number(option))
    if kind == "number":
        # Not `int`: a theme declares scale factors and opacities, and a control that
        # formats them as whole numbers shows a value that is not the one stored.
        return panel.number(
            value, lambda e: save(None if e.value is None else float(e.value)),
            disabled=off, whole=False, low=option.get("min"), high=option.get("max"),
            hint=_range_hint(option),
            step=option.get("step"), placeholder=blank,
            select_at=_default_number(option))
    if kind == "text" and option.get("lines"):
        return panel.field(str(value or ""), lambda text: save(text),
                           lines=int(option["lines"]), disabled=off,
                           placeholder=blank)
    if kind == "list":
        # One line, comma separated, which is how the file holds it. A chip editor would
        # be nicer and would need to know whether order matters; it does for some.
        return panel.field(
            ", ".join(str(v) for v in (value or [])),
            lambda text: save([p.strip() for p in text.split(",") if p.strip()]),
            disabled=off)
    if option.get("path"):
        # A path is the one value that can be well-formed and still wrong, and it fails
        # much later - at launch, as a file-not-found. Re-checked after a write rather
        # than guessed at here: the answer is about this machine's disk, not the text.
        async def save_path(text: str) -> None:
            if await save(text) and saved is not None:
                await saved()

        stands_for = str(option.get("default_path") or "")
        return panel.field(str(value or ""), save_path, disabled=off, status=state,
                           placeholder=blank, left_empty=str(option.get("left_empty") or ""),
                           default=stands_for, whose=str(option.get("default_whose") or ""),
                           marked=not option.get("unmarked"),
                           readonly=bool(option.get("readonly")),
                           browse_tip=str(option.get("chooser_tip") or ""),
                           browse=option.get("chooser") or _browse_path(
                               browse, str(option["path"]),
                               tuple(option.get("suffixes") or ()), stands_for))
    return panel.field(str(value or ""), lambda text: save(text), disabled=off,
                       placeholder=blank)


def _bound(value: Any) -> str:
    """A bound as a person writes it: 100000000, not 1e+08. Nothing for one that is not a
    finite number, which a theme's JSON can declare."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ""
    if not math.isfinite(number):
        return ""
    return str(int(number)) if number.is_integer() else f"{number:g}"


def range_words(low: Any, high: Any) -> str:
    """What a number may be, for its tooltip. Nothing when it is unbounded."""
    low, high = _bound(low), _bound(high)
    if low and high:
        return t("manager.settings.range_between", low=low, high=high)
    if low:
        return t("manager.settings.range_at_least", low=low)
    if high:
        return t("manager.settings.range_at_most", high=high)
    return ""


def _range_hint(option: dict) -> str:
    """The tooltip, unless the row already says its range beneath the control."""
    return "" if option.get("range_beneath") else range_words(option.get("min"), option.get("max"))


def _unit_word(option: dict) -> str:
    """What a number counts, said beside it. A port is a number nobody counts with."""
    return t("word.seconds") if option.get("unit") == config_schema.UNIT_SECONDS else ""


def by_group(options: list[dict]) -> list[dict]:
    """The section's settings gathered under their headings, the ungrouped last.

    Gathered rather than assumed contiguous: two settings of one group can be declared
    either side of another, and a renderer emitting a heading on every change prints one
    twice. Declaration order decides everything else, so a page is ordered by moving a
    line in the schema.
    """
    ordered: dict[str, list[dict]] = {}
    for option in options:
        ordered.setdefault(str(option.get("group") or ""), []).append(option)
    loose = ordered.pop("", [])
    return [option for group in ordered.values() for option in group] + loose


def saver(source: Any, section: str, key: str,
           refusal: Refusal | None = None) -> Callable[[Any], Any]:
    """Write one setting to a config section, for the control grammar to call."""
    async def save(value: Any) -> bool:
        return await _write(source, section, key, value, refusal)

    return save


class Waiting:
    """A chip beside each setting that is saved and not yet in effect.

    Drawn from the server's own answer, and asked again after each save of a setting that
    can wait, so the chip appears when a change is made and clears when it is changed back.
    """

    def __init__(self, source: Any, found: dict[str, Any] | None = None) -> None:
        self.source = source
        self.entries: dict[str, str] = {}
        self.chips: dict[str, Any] = {}
        self.take(found or {})

    def take(self, found: dict[str, Any]) -> None:
        self.entries = {f"{one['section']}.{one['key']}": str(one.get("waits_for") or "")
                        for one in found.get("waiting") or []
                        if one.get("kind") == "setting"}

    def beside(self, draw: Callable[[], None], option: dict) -> Callable[[], None]:
        key = f"{option['section']}.{option['key']}"

        return _Chipped(self, draw, key)

    def after(self, save: Callable[[Any], Any]) -> Callable[[Any], Any]:
        async def saved(value: Any) -> bool:
            done = bool(await save(value))
            if done:
                await self.refresh()
            return done

        return saved

    async def refresh(self) -> None:
        try:
            self.take(await offload.io(self.source.config_waiting))
        except Exception as exc:  # noqa: BLE001 - a chip not redrawn is not a failed save
            logger.warning("Could not read what is waiting: %s", why(exc))
            return
        for key in self.chips:
            self.show(key)

    def show(self, key: str) -> None:
        chip = self.chips[key]
        if chip.is_deleted:
            return
        waits = self.entries.get(key)
        chip.set_visibility(bool(waits))
        if waits:
            chip.set_text(t("manager.settings.chip_reopen" if waits == "reopen"
                            else "manager.settings.chip_restart"))


class _Chipped:
    """A control drawn with its waiting chip after it. What the control carries - a picker's
    value, its offered choices - stays reachable through this."""

    def __init__(self, waiting: Waiting, draw: Callable[[], None], key: str) -> None:
        self.waiting = waiting
        self.draw = draw
        self.key = key

    def __getattr__(self, name: str) -> Any:
        return getattr(self.draw, name)

    def __call__(self) -> None:
        with ui.element("div").classes("manager-waiting-row"):
            self.draw()
            self.waiting.chips[self.key] = ui.label().classes(
                "manager-member-chip manager-tier manager-tier--warn")
            self.waiting.show(self.key)


def _asked_first(option: dict, save: Callable[[Any], Any],
                 refill: Callable[[], None]) -> Callable[[Any], Any]:
    """`save`, once the person has been told what it will cost them: this setting decides
    where the Manager can be reached from, and the page asking is on another device."""
    async def asked(value: Any) -> bool:
        if str(value).strip() in ("", str(option.get("default"))):
            return bool(await save(value))
        if not await confirm.ask(
                t("manager.settings.bind_title",
                  setting=option.get("label") or option["key"]),
                detail=t("manager.settings.bind_detail"),
                confirm=t("word.change"), icon=verbs.SAVE, danger=False):
            refill()
            return False
        return bool(await save(value))

    return asked


def _redrawn_after(save: Callable[[Any], Any],
                   refill: Callable[[], None]) -> Callable[[Any], Any]:
    """`save`, then the page drawn again: something below the field reads what was saved."""
    async def saved(value: Any) -> bool:
        done = bool(await save(value))
        if done:
            refill()
        return done

    return saved


def _then(save: Callable[[Any], Any],
          after: Callable[[], Awaitable[None]]) -> Callable[[Any], Any]:
    """`save`, then `after` awaited once the write has gone through."""
    async def saved(value: Any) -> bool:
        done = bool(await save(value))
        if done:
            await after()
        return done

    return saved


def _kind_page(library: Library, refill: Callable[[], None] | None,
               note: str | tuple[str, ...], section: str, key: str,
               items: Callable[[Any], dict[str, str]], columns: bool = False) -> None:
    """A switch per thing, over one list in the config.

    Not a schema page. What it switches is a *list*, and the switches themselves come
    from a registry or from the API - which `common/` may not reach for, because nothing
    in it may import a domain package. The Manager may, so the rendering lives here.
    """
    body = ui.column().classes("w-full gap-0")
    busy.fill(body, lambda: _fill_kinds(library, refill, body, note, section, key, items,
                                        columns))


def _listed(value: Any) -> set[str]:
    """A stored list, however the config layer hands it over - a list from JSON, or the
    comma string the ini holds."""
    if isinstance(value, str):
        value = value.split(",")
    return {str(item).strip() for item in (value or []) if str(item).strip()}


KindSwitch = tuple[str, str, Callable[[], None]]

# What a column page draws beside a switch, given the policy as read and the write that
# stores a change to it. The answer draws the cell for one kind, or is None for a kind that
# has nothing to say.
Aside = Callable[[dict[str, Any], Callable[[dict], Any]],
                 Callable[[str], Callable[[], None] | None]]


async def _fill_kinds(library: Library, refill: Callable[[], None] | None, body: Any,
                      note: str | tuple[str, ...], section: str, key: str,
                      items: Callable[[Any], dict[str, str]],
                      columns: bool = False,
                      arrange: Callable[[list[KindSwitch]], list[tuple[Any, Any]]] | None = None,
                      aside: Aside | None = None,
                      ) -> list:
    """The switches, drawn into `body` - or returned when `body` is None, for a page that
    orders them among its own settings rather than beside them.

    A switch is written and left where it is. `refill` is for a page where something else
    reads the list - the thumbnail choice offers only the kinds that are kept.

    `columns` is for the long lists, the media and asset kinds: two columns, in name order
    down the first and on into the second, so the list is a screen and not a scroll.

    `arrange` is handed each switch as (name, label, draw), in the order `items` gave
    them, and answers the entries to draw in place of the name-ordered rows. An empty
    `note` draws no line over them, and a tuple draws a line for each.

    The `manager` section holds this install's own list; every other is the library's."""
    device = section == "manager"
    try:
        # This install's lists, read through /config like every other setting.
        policy = await offload.io(library.manager_policy if device else library.library_policy)
        known = await offload.io(items, library)
    except Exception as exc:  # noqa: BLE001 - a settings page says why, never 500s
        said = [panel.intro(t("said.settings_unreadable"), hint=why(exc))]
        if body is None:
            return said
        with body:
            panel.facts(ui, said)
        return said

    on = set(known) - _listed(policy.get(key))
    write = library.put_manager_policy if device else library.put_library_policy

    @on_page
    async def flip(name: str, wanted_on: bool) -> None:
        before, was = set(on), policy.get(key)
        # Taken before the write, so a second switch thrown while this one is in flight
        # is built from this one's result rather than over it.
        on.clear()
        on.update((before | {name}) if wanted_on else (before - {name}))
        store = sorted((_listed(was) - set(known)) | (set(known) - on))
        policy[key] = store
        try:
            await run.io_bound(write, {key: store})
        except Exception as exc:  # noqa: BLE001 - the reason belongs on the page
            on.clear()
            on.update(before)
            policy[key] = was
            ui.notify(t("manager.settings.save_failed"), caption=why(exc), type="negative")
            return
        if refill is not None:
            refill()

    # Bound by a call rather than captured off the loop variable, which a lambda
    # would read only when it fires.
    def flipper(name: str) -> Callable[[Any], Any]:
        return lambda event: flip(name, bool(event.value))

    lines = (note,) if isinstance(note, str) else note
    entries: list[tuple[Any, Any]] = [panel.lede(line) for line in lines if line]
    beside = aside(policy, write) if aside is not None else None
    ordered = sorted(known.items(), key=lambda pair: pair[1])
    if arrange is not None:
        entries += arrange([(name, label, panel.switch(name in on, flipper(name)))
                            for name, label in known.items()])
    elif columns:
        entries.append((panel.FULL, lambda: _kind_columns(
            [(label, panel.switch(name in on, flipper(name)), beside(name) if beside else None)
             for name, label in ordered])))
    else:
        for name, label in ordered:
            entries.append((label, panel.switch(name in on, flipper(name))))
    if body is None:
        return entries
    with body:
        panel.facts(ui, entries)
    return entries


def _kind_columns(rows: list[tuple[str, Callable[[], None], Callable[[], None] | None]]
                  ) -> None:
    """A label, what goes beside the switch, and the switch per cell, filled down the
    first column and on into the second."""
    with ui.element("div").classes("manager-kinds") \
            .style(f"--kind-rows: {(len(rows) + 1) // 2}"):
        for label, draw, beside in rows:
            with ui.element("div").classes("manager-kind"):
                ui.label(label).classes("manager-fact-label")
                if beside is not None:
                    beside()
                draw()


def _media_names(policy: dict[str, Any], write: Callable[[dict], Any],
                 ) -> Callable[[str], Callable[[], None] | None]:
    """Each media kind's file name beside its switch. Flyer and Instruction Card take a
    picker, since a new file of either can be spelled Visual Pinball's way or VPinFE's; the
    rest have one name and show it."""
    specs = {spec.kind: spec for spec in MEDIA_SPECS}

    def shares_a_picture(kind: str) -> bool:
        spec = specs[kind]
        return spec.family == VIDEO_FAMILY and any(
            other.token == spec.token and other.family != VIDEO_FAMILY
            for other in specs.values())

    def named(kind: str) -> Callable[[], None] | None:
        spec = specs.get(kind)
        if spec is None or not spec.token:
            return None
        if kind in NAMED_TOKENS:
            return _name_picker(kind, policy, write)
        token = spec.token
        if kind in NO_VISUAL_PINBALL_NAME:
            tip = t("manager.settings.kind_name_unlisted", token=token)
        elif shares_a_picture(kind):
            tip = t("manager.settings.kind_name_video", token=token)
        else:
            tip = t("manager.settings.kind_name", token=token)

        def draw() -> None:
            ui.label(token).classes("manager-kind-name").tooltip(tip)

        return draw

    return named


# The kinds Visual Pinball's file layout says nothing about, so another frontend may not
# show a file named this way.
NO_VISUAL_PINBALL_NAME = frozenset({"cab", "logo", "playfield_fss", "rule_sheet"})


def _name_picker(kind: str, policy: dict[str, Any], write: Callable[[dict], Any],
                 ) -> Callable[[], None]:
    """The two spellings of a kind's new files, each saying who it suits."""
    key = f"{kind}_name"
    tokens = NAMED_TOKENS[kind]
    first = next(iter(tokens))
    says = {
        tokens["visual_pinball"]: t("manager.settings.name_visual_pinball"),
        tokens["vpinfe"]: t("manager.settings.name_vpinfe"),
    }

    @on_page
    async def choose(event: Any) -> None:
        before = policy.get(key)
        if str(event.value) == str(before or first):
            return
        policy[key] = str(event.value)
        try:
            await run.io_bound(write, {key: str(event.value)})
        except Exception as exc:  # noqa: BLE001 - the reason belongs on the page
            policy[key] = before
            event.sender.set_value(before or first)
            ui.notify(t("manager.settings.save_failed"), caption=why(exc), type="negative")

    current = str(policy.get(key) or first)
    return panel.select(dict(tokens), current if current in tokens else first, choose,
                        describes=says)


async def _kind_rows(library: Library, refill: Callable[[], None] | None,
                     page: str) -> list:
    """One registry's switches as entries, for a page that orders them among its own."""
    found = KIND_PAGES.get(page)
    if found is None:
        return []
    note, section, name, items = found
    return await _fill_kinds(library, refill, None, note, section, name, items,
                             page in TWO_COLUMNS, ARRANGED.get(page), BESIDE.get(page))


async def _spreadsheet_check(library: Library, refill: Callable[[], None],
                             ) -> list[tuple[Any, Any]]:
    """When the catalog was last asked, and the way to ask now.

    A schedule is a setting and the schema renders it; "do it now" is not a setting and
    has nowhere in a schema page to live.

    Rows rather than a drawing, because they join the page's one list. A second grid
    sizes a label column of its own and its values start somewhere else entirely.
    """
    try:
        state = await offload.io(library.vps_sync_state)
    except Exception as exc:  # noqa: BLE001 - a settings page says why, never 500s
        return [panel.intro(t("manager.settings.sync_failed"), hint=why(exc))]

    @on_page
    async def now() -> None:
        # Held: an ongoing notification never times out on its own.
        checking = ui.notification(t("manager.settings.checking_vpsdb"), spinner=True, timeout=None)
        try:
            done = await offload.io(library.sync_vps)
        except Exception as exc:  # noqa: BLE001
            ui.notify(t("manager.settings.check_failed"), caption=why(exc), type="negative")
            return
        finally:
            checking.dismiss()
        # "Already current" is the ordinary outcome and says itself; a positive toast
        # for it would make the rare one look the same as the common one.
        ui.notify(t("manager.settings.vpsdb_updated") if done.get("changed")
                else t("manager.settings.already_date"),
                  type="positive" if done.get("changed") else "info")
        refill()

    return [(panel.SUB, last_checked(str(state.get("checked") or ""), now))]


def last_checked(checked_at: str, now: Callable[[], Any], *,
                 blocked: str = "") -> tuple[Any, Any]:
    """When something kept was last read, and the act that reads it now. `blocked` is why
    the act is dimmed, where it is."""
    def checked() -> None:
        with ui.element("div").classes("manager-fact-edit"):
            shown = ui.label(when.ago(checked_at) or t("word.never")) \
                .classes("manager-fact-value truncate min-w-0")
            if checked_at:
                shown.tooltip(when.local(checked_at))
            panel.action(t("manager.settings.check_now"), now, icon=verbs.REFRESH, inline=True,
                         enabled=not blocked,
                         hint=blocked or t("manager.settings.check_now.help"))()

    return (field_label(t("manager.settings.last_checked")), checked)


async def _themes_foot(library: Library, refill: Callable[[], None],
                       **_: Any) -> list[tuple[Any, Any]]:
    try:
        held = await offload.io(library.themes)
    except Exception as exc:  # noqa: BLE001 - a settings page says why, never 500s
        return [panel.intro(t("manager.settings.themes_failed"), hint=why(exc))]

    @on_page
    async def now() -> None:
        checking = ui.notification(t("manager.settings.checking_themes"), spinner=True,
                                   timeout=None)
        try:
            await offload.io(library.themes, True)
        except Exception as exc:  # noqa: BLE001
            ui.notify(t("manager.settings.check_failed"), caption=why(exc), type="negative")
            return
        finally:
            checking.dismiss()
        refill()

    return [last_checked(str(held.get("checked") or ""), now)]


# section -> what to draw under its settings. Only where a page has an act in it, or a
# reading that answers a question its settings raise.
FOOTERS: dict[str, Callable] = {"themes": _themes_foot, recording.SECTION: recording.foot,
                                dof.SECTION: dof.foot}


# The two video formats whose absence makes the browser unfit (`frontend_browser.state_of`).
_NEEDED = ("h264", "vp9")


def _format_level(found: dict[str, Any], one: frontend_browser.Format) -> str:
    answer = (found.get("formats") or {}).get(one.id)
    if answer is None:
        return "unknown"
    if answer:
        return "on"
    nothing_plays = found.get("state") == frontend_browser.NO_VIDEO
    return "bad" if nothing_plays and one.id in _NEEDED else "warn"


def _browser_rows(found: dict[str, Any]) -> list[tuple[Any, Any]]:
    """What the browser plays, as a row of chips for the video formats and one for the
    audio formats. Before a report is on file the chips are all unknown; a browser with no
    program is one chip saying so."""
    video, audio = t("manager.settings.browser_video"), t("manager.settings.browser_audio")
    if found.get("state") == frontend_browser.NO_BROWSER:
        said = browser_state.STATES["no_browser"]
        return [(video, panel.states([(said["label"], said["tier"], said["why"])]))]
    if not found.get("reported_at"):
        why_grey = browser_state.STATES["unknown"]["why"]
        return [(label, panel.states(
                    [(one.name, "unknown", why_grey)
                     for one in frontend_browser.FORMATS if one.kind == kind],
                    note=t("manager.settings.browser_not_checked") if kind == frontend_browser.VIDEO
                    else ""))
                for kind, label in ((frontend_browser.VIDEO, video),
                                    (frontend_browser.AUDIO, audio))]
    ago = when.ago(str(found["reported_at"]), inline=True)
    hint = (t("manager.settings.browser_hover", browser=found["browser"], when=ago)
            if found.get("browser")
            else t("manager.settings.browser_hover_nameless", when=ago))
    return [(label, panel.states([(one.name, _format_level(found, one), hint)
                                  for one in frontend_browser.FORMATS if one.kind == kind]))
            for kind, label in ((frontend_browser.VIDEO, video),
                                (frontend_browser.AUDIO, audio))]


def _browser_finding(library: Library, refill: Callable[[], None],
                     found: dict[str, Any]) -> None:
    """What is wrong, the fix in words, and the act where VPinFE can do it."""
    fix = found.get("fix") or {}
    chrome = str(fix.get("chrome_path") or "")

    @on_page
    async def use(path: str, said: str) -> None:
        held = await offload.io(library.config_values)
        before = str(((held.get("chromium") or {}).get("path")) or "")
        try:
            await offload.io(library.put_config, {"chromium": {"path": path}})
        except Exception as exc:  # noqa: BLE001 - the reason belongs on screen
            ui.notify(t("manager.settings.switch_failed"), caption=why(exc),
                      type="negative")
            return
        if said:
            undo.offer(said, lambda: use(before, ""))
        refill()

    with ui.element("div").classes("manager-attention w-full mb-2"):
        ui.icon("error_outline").classes("manager-attention-icon")
        with ui.column().classes("gap-0 min-w-0 grow"):
            ui.label(str(found.get("finding") or "")).classes("manager-attention-line")
            if found.get("fix_text"):
                ui.label(str(found["fix_text"])).classes("manager-member-table")
            if found.get("state") == frontend_browser.NO_H264:
                with ui.row().classes("items-center gap-1 no-wrap min-w-0"):
                    ui.label(t("manager.recording.vp9_offer")) \
                        .classes("manager-member-table")
                    panel.link(t("manager.settings.page_recording"),
                               to=address_for(recording.SECTION))()
        if fix.get("action") == "use_chrome" and chrome:
            panel.action(t("manager.settings.use_chrome"),
                         lambda: use(chrome, t("manager.settings.chrome_active")),
                         icon=verbs.CHOOSE,
                         hint=t("manager.settings.use_chrome.help"))()


async def _browser_head(library: Library, refill: Callable[[], None],
                        values: dict, read: dict[str, Any] | None = None,
                        **_: Any) -> list[tuple[Any, Any]]:
    """What is wrong with the browser, before its settings. The report it read goes in
    `read` for the rows drawn under the program."""
    read = {} if read is None else read
    try:
        found = read["found"] = await offload.io(library.frontend_browser)
    except Exception as exc:  # noqa: BLE001 - a settings page says why, never 500s
        read["found"] = {}
        return [panel.intro(t("manager.settings.browser_failed"), hint=why(exc))]
    if not found:
        return []
    if found.get("finding"):
        return [(panel.FULL, lambda: _browser_finding(library, refill, found))]
    return []


TOOLS = "tools"


def _get_tool(library: Library, refill: Callable[[], None], row: dict[str, Any],
              offered: dict[str, Any]) -> None:
    """A missing Tool VPinFE can get itself, and Get, asking first what and from where."""
    @on_page
    async def get() -> None:
        from manager.library import get_tool

        if await get_tool.ask_and_get(library, str(row.get("id")), offered):
            refill()

    with ui.element("div").classes("manager-attention w-full mb-2"):
        ui.icon("error_outline").classes("manager-attention-icon")
        ui.label(t("manager.settings.ffmpeg_missing")) \
            .classes("manager-attention-line min-w-0 grow")
        panel.action(t("manager.settings.get_ffmpeg"), get, icon=verbs.FETCH,
                     hint=t("manager.settings.get_ffmpeg.help"))()


async def _tools_head(library: Library, refill: Callable[[], None],
                      values: dict, **_: Any) -> list[tuple[Any, Any]]:
    """Get, for each missing Tool VPinFE can get itself, before the paths."""
    rows = await _tools_found(library, (TOOLS,))
    return [(panel.FULL, lambda row=row, offered=offered:
             _get_tool(library, refill, row, offered))
            for row in rows
            if (offered := (row.get("remedy") or {}).get("get"))]


# section -> what to draw above its settings, the counterpart of FOOTERS.
HEADS: dict[str, Callable] = {"chromium": _browser_head, TOOLS: _tools_head}


async def _browser_playback(library: Library, read: dict[str, Any]) -> list[tuple[Any, Any]]:
    """What the browser plays, drawn after the program that is the browser."""
    found = read.get("found")
    return _browser_rows(found) if found else []


# section -> what to draw at the end of its first group of settings.
UNDER_FIRST_GROUP: dict[str, Callable] = {"chromium": _browser_playback}

async def _network_address(values: dict[str, Any]) -> dict[str, list[tuple[Any, Any]]]:
    """Where another device opens this one, under the Port setting. Nothing where this
    device answers only itself, and nothing where it has no address to give."""
    bind = str((values.get("network") or {}).get("http_bind")
               or config_schema.default_of("network", "http_bind"))
    if not serving.reachable_from_elsewhere(bind):
        return {}
    from common.config.paths import get_ini_config

    def find() -> str:
        return addresses.best(serving.network(get_ini_config()).http_port, "")

    address = await offload.io(find)
    return {"http_port": [panel.note(t("manager.settings.network_address",
                                       address=address))]} if address else {}


# section -> the rows to draw under a setting of it, from the values the page was given.
# Only where this install's own client serves the page: the address is this device's.
UNDER_SETTINGS: dict[str, Callable[[dict[str, Any]], Awaitable[dict[str, list]]]] = {
    "network": _network_address}

# section -> what is wrong with a whole page, drawn with its name, from what the page was
# offered, with any act that puts it right; None where nothing is.
FINDINGS: dict[str, Callable[[dict[str, Any], Library, Callable[[], None]],
                             Callable[[], None] | None]] = {
    recording.SECTION: recording.finding}

# section -> the lines a setting at its default resolves to on this device, read from what
# the page was offered: Automatic says what it records here.
RESOLVES: dict[str, Callable[[dict[str, Any]], dict[str, str]]] = {
    recording.SECTION: recording.resolved}

# page -> the line under its heading. Optional: a page whose name says the whole thing
# takes none.
PAGE_NOTES: dict[str, str] = {
    "vpinfe.commands": "manager.settings.commands_warning",
    "frontend.table_commands": "manager.settings.commands_warning",
    "vpinfe.manager": "manager.settings.note_manager",
    "vpinfe.logger": "manager.settings.note_logger",
    "frontend.themes": "manager.settings.note_themes",
    "frontend.chromium": "manager.settings.note_chromium",
    "hardware.output": "manager.settings.note_output",
    "frontend.presentation": "manager.settings.note_presentation",
    "vpinfe.tools": "manager.settings.note_tools",
    "library.updates": "manager.settings.note_updates",
    folders.KEY: "manager.settings.note_folders",
    DISCOVERY_PAGE: "manager.settings.note_discovery",
    "library.checks": "manager.settings.checks_note",
}


def page_head(key: str, findings: Sequence[Callable[[], None]] = ()) -> None:
    """A page's name, in `panel.header`'s treatment: the cyan heading is for a group within
    a page, and using it for the page's own name ranked the two the same."""
    said = PAGE_NOTES.get(key, "")
    panel.header(_page_label(key), t(said) if said else "", findings)


# The two kind pages are not schema pages. What they switch is a *list* in the config,
# and the switches themselves come from the two registries - which `common/` may not
# import, because nothing in it may reach up into a domain package. The Manager may, so the
# rendering lives on this side of that line.
# key -> (note, config section, config key, what to switch). Each list stores what is
# off, so a kind or a source added in a later version arrives switched on.
KIND_PAGES: dict[str, tuple[str | tuple[str, ...], str, str,
                            Callable[[Any], dict[str, str]]]] = {
    "media_kinds": (KINDS_NOTE, "", "hidden_media_kinds",
                    lambda _: dict(media_label_map())),
    "asset_kinds": (ASSETS_NOTE, "", "hidden_asset_kinds",
                    lambda _: {spec.kind: spec.label for spec in ASSET_SPECS
                               if spec.kind not in ALWAYS_KEPT}),
    "media_sources": ("", "", "hidden_sources",
                      lambda library: {s["id"]: s["name"] for s in library.media_sources()
                                       if not s.get("always_on")}),
    "library_checks": ("", "", "hidden_checks",
                       lambda _: _check_labels()),
    "overview_rows": (OVERVIEW_NOTE, "manager", "hidden_overview",
                      lambda _: {key: needs.name_of(key) for key in needs.DEVICE_ROWS}),
}


# The registries long enough to be laid out in two columns. The sources and the checks
# are a handful, and one column reads better.
TWO_COLUMNS = frozenset({"media_kinds", "asset_kinds"})

# The checks under the headings the Checks page draws them in, in the order it draws them:
# (heading, ((check key, label), ...)).
CHECK_GROUPS: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    ("manager.settings.checks_folders", (
        ("unreadable", "manager.settings.check_unreadable"),
        ("no_tables", "manager.settings.check_no_tables"),
        ("older_format", "manager.settings.check_older_format"),
    )),
    ("manager.settings.checks_matching", (
        ("unmatched", "manager.settings.check_unmatched"),
        ("untied", "manager.settings.check_untied"),
        ("no_year", "manager.settings.check_no_year"),
    )),
    ("manager.settings.checks_media", (
        ("no_playfield", "manager.settings.check_no_playfield"),
        ("no_backglass", "manager.settings.check_no_backglass"),
        ("no_media", "manager.settings.check_no_media"),
    )),
    ("manager.settings.checks_tables", (
        ("rom_missing", "manager.settings.check_rom_missing"),
    )),
)


def _check_labels() -> dict[str, str]:
    """Every check's switch label."""
    return {key: t(label) for _heading, checks in CHECK_GROUPS for key, label in checks}


def _checks_arranged(switches: list[KindSwitch]) -> list[tuple[Any, Any]]:
    """The checks' switches under their headings, in the order `CHECK_GROUPS` declares."""
    drawn = {name: (label, draw) for name, label, draw in switches}
    entries: list[tuple[Any, Any]] = []
    for heading, checks in CHECK_GROUPS:
        entries.append((panel.HEADING, t(heading)))
        entries += [drawn[key] for key, _label in checks if key in drawn]
    return entries


def _sources_arranged(switches: list[KindSwitch]) -> list[tuple[Any, Any]]:
    """The sources with a rule between each, the ones VPinFE ships first, each with the line
    that says what it gives."""
    from common.online import vpinmediadb

    shipped = {vpinmediadb.ID: "manager.settings.vpinmediadb.help"}
    order = list(shipped)
    entries: list[tuple[Any, Any]] = []
    for at, (name, label, draw) in enumerate(sorted(
            switches, key=lambda one: order.index(one[0]) if one[0] in order else len(order))):
        if at:
            entries.append((panel.RULE, None))
        entries.append((label, draw))
        if name in shipped:
            entries.append(panel.note(t(shipped[name])))
    return entries


# registry -> how its switches are laid out, where that is not one name-ordered list.
ARRANGED: dict[str, Callable[[list[KindSwitch]], list[tuple[Any, Any]]]] = {
    "library_checks": _checks_arranged, "media_sources": _sources_arranged}

# registry -> what is drawn beside each switch, for the column pages.
BESIDE: dict[str, Aside] = {"media_kinds": _media_names}


async def _folders_page(library: Library, redraw: Callable[[], None],
                        state: dict[str, Any]) -> Callable[[], None]:
    """The library's folders, with the settings that name a folder and have this page as
    their home drawn under them, in the same list so the two share a label column."""
    async def below() -> list[tuple[Any, Any]]:
        try:
            schema = await offload.io(library.config_schema)
            values = await offload.io(library.config_values)
            checks = await offload.io(library.config_path_checks)
        except Exception as exc:  # noqa: BLE001 - the library folders stay on the page
            return [panel.intro(t("manager.folders.read_failed"), hint=why(exc))]
        media = next((one for one in with_default_paths(schema, checks)
                      if one.get("name") == "media"), {})
        options = [one for one in media.get("options") or []
                   if one.get("home") == folders.KEY]
        return section_rows(library, "media", options, values,
                            bool(media.get("writable")), redraw,
                            field_marks([], checks), browse=library.folders,
                            waiting=Waiting(library, await _waiting(library)))

    return await folders.page(library, redraw, state, below)


async def _discovery_page(library: Library, redraw: Callable[[], None],
                          state: dict[str, Any]) -> panel.Region:
    """Where VPinFE looks online: whether the rail counts updates, then the spreadsheet,
    whose schedule is a setting that has this page as its home, then the sources media comes
    from. One list, so the labels share a column.

    A region: Check Now and a schedule write read the page again where it stands, and the
    caller awaits `refilled()` for the first draw.
    """
    async def draw() -> None:
        entries: list[tuple[Any, Any]] = []
        try:
            schema = await offload.io(library.config_schema)
            values = await offload.io(library.config_values)
        except Exception as exc:  # noqa: BLE001 - the sources stay on the page
            entries += [(panel.HEADING, t("manager.settings.heading_sources")),
                        panel.lede(SOURCES_NOTE),
                        panel.intro(t("said.settings_unreadable"), hint=why(exc))]
        else:
            updates = next((one for one in schema if one.get("name") == "updates"), {})
            options = [one for one in updates.get("options") or []
                       if one.get("home") == DISCOVERY_PAGE]
            counted = [one for one in options if one["key"] == SHOW_COUNT]
            rest = [one for one in options if one["key"] != SHOW_COUNT]
            writable = bool(updates.get("writable"))
            async def recount() -> None:
                if (again := state.get("recount_updates")) is not None:
                    await again()
                region.refill()

            entries.append((panel.HEADING, t("manager.settings.heading_updates")))
            entries += section_rows(library, "updates", counted, values, writable,
                                    region.refill, after={SHOW_COUNT: recount})
            entries += shelves.entries(library, state, values)
            entries += [(panel.HEADING, t("manager.settings.heading_sources")),
                        panel.lede(SOURCES_NOTE)]
            entries += section_rows(library, "updates", rest, values, writable,
                                    region.refill)[0 if writable else 1:]
            entries += await _spreadsheet_check(library, region.refill)
        entries.append((panel.RULE, None))
        entries += await _kind_rows(library, None, "media_sources")
        panel.facts(ui, entries)

    region = panel.region(draw)
    return region


SECURITY_PAGE = "vpinfe.security"

# A built page's draw: read what it shows, then hand back what draws it. The read is
# awaited by the caller, so the draw itself never waits. A page that hands back a region
# reads inside it instead: the caller awaits its `refilled()` for the first draw.
PAGES: dict[str, Callable[[Library, Callable[[], None], dict[str, Any]],
                          Awaitable[Callable[[], None]]]] = {
    folders.KEY: _folders_page,
    DISCOVERY_PAGE: _discovery_page,
    SECURITY_PAGE: security.page,
}


def _page_label(key: str) -> str:
    """A page's name, from the one place it is written.

    Identity is searched too: it is pinned by `system_pages` rather than declared in the
    index, so looking only at the index answered a page the rail draws with its own key.
    """
    pages = [page for _group, group_pages in DEVICE_INDEX for page in group_pages]
    pages.append(IDENTITY_PAGE)
    found = next((label for item, label, _kind, _sections, _feature in pages
                  if item == key), "")
    return t(found) if found else key


# A section that heads a page shared with others, where the key makes a poor heading:
# `windows.playfield` humanizes to "Windows Playfield", and the screen is the Playfield.
SECTION_LABELS: dict[str, str] = {
    "windows.playfield": "manager.settings.section_playfield",
    "real_dmd": "manager.settings.section_real_dmd",
    "windows.backglass": "manager.settings.section_backglass",
    "windows.scoreview": "manager.settings.section_scoreview",
    "windows.topper": "manager.settings.section_topper",
}


def _section_label(key: str) -> str:
    """What to call a config section on screen.

    A page's own name where a page is that one section, otherwise a declared heading,
    otherwise the key made readable, or for a window the window's name alone.
    """
    named = next((label for _group, pages in DEVICE_INDEX
                  for _item, label, _kind, sections, _feature in pages
                  if sections == (key,)), "") or SECTION_LABELS.get(key, "")
    if named:
        return t(named)
    return " ".join(humanize(part)
                    for part in (config_schema.window_name(key) or key).split("."))


# `device` appears on no page below, deliberately: it is the device's identity and is
# edited in Details.
# A device's pages, in the shape this page uses for this install's own: grouped, named in a
# person's words, one page per topic rather than one per config section. Several sections
# can back one page - a machine's screens are four of them - because how the config file
# is divided is not how somebody looks for a setting.
#
# group -> ((page key, label, kind, sections it draws, feature), ...)
# Every page names a feature, `core` being the one every install has. `kind` picks the
# renderer.
SCHEMA_PAGE, KIND_PAGE, BUILT_PAGE = "schema", "kind", "built"

# The last is the feature the page answers for, or several where any one of them is enough.
DevicePage = tuple[str, str, str, tuple[str, ...], str | tuple[str, ...]]

DEVICE_INDEX: tuple[tuple[str, tuple[DevicePage, ...]], ...] = (
    # The install itself, as against the cabinet it drives, what a player sees, what it
    # collects and what it talks to. Identity leads it and is pinned by `system_pages`.
    ("manager.settings.group_vpinfe", (
        ("vpinfe.network", "manager.settings.page_network", SCHEMA_PAGE, ("network",),
                 install_identity.CORE),
        # How much is written down and where it goes. The records themselves are a
        # place of their own under System, so this page is named for the act rather
        # than for them - two things called Logs is one too many.
        (SECURITY_PAGE, "manager.settings.page_security", BUILT_PAGE, (),
                 install_identity.CORE),
        ("vpinfe.logger", "manager.settings.page_logger", SCHEMA_PAGE, ("logger",),
                 install_identity.CORE),
        # Where VPinFE keeps what it copies. The copies are System's own page, as the
        # logs are.
        ("vpinfe.backups", "manager.settings.page_backups", SCHEMA_PAGE, ("backups",),
                 install_identity.CORE),
        ("vpinfe.manager", "manager.settings.page_manager", SCHEMA_PAGE, ("manager",),
                 install_identity.CORE),
        ("vpinfe.commands", "manager.settings.page_commands", SCHEMA_PAGE, ("commands",),
                 install_identity.CORE),
        # This install's, not the library's: nvtop answers Metrics and ffmpeg would
        # answer media, so an OS dependency belongs to the machine that has to have it.
        ("vpinfe.tools", "manager.settings.page_tools", SCHEMA_PAGE, ("tools",),
                 install_identity.CORE),
    )),
    ("manager.settings.group_hardware", (
        ("hardware.displays", "manager.settings.page_displays", SCHEMA_PAGE,
         ("windows.playfield", "windows.backglass", "windows.scoreview", "windows.topper",
          "real_dmd"),
         "frontend"),
        ("hardware.input", "manager.settings.page_input", SCHEMA_PAGE, ("input",), "frontend"),
        ("hardware.output", "manager.settings.page_output", SCHEMA_PAGE, ("dof",), "frontend"),
        ("hardware.recording", "manager.settings.page_recording", SCHEMA_PAGE, ("capture",),
         "frontend"),
    )),
    ("manager.settings.group_library", (
        # First because everything below it acts on games found in these folders, and a
        # device that only plays them still needs to say where they are.
        (folders.KEY, "manager.settings.page_folders", BUILT_PAGE, (),
         (install_identity.LIBRARY, install_identity.FRONTEND)),
        ("library.media", "manager.settings.page_media", SCHEMA_PAGE, ("media",), "library"),
        ("library.assets", "manager.settings.page_assets", SCHEMA_PAGE, (), "library"),
        ("library.checks", "manager.settings.page_checks", SCHEMA_PAGE, (), "library"),
        ("library.updates", "manager.settings.page_updates", SCHEMA_PAGE, ("updates",),
                "library"),
        (DISCOVERY_PAGE, "manager.settings.page_discovery", BUILT_PAGE, (), "library"),
    )),
    ("manager.settings.group_frontend", (
        ("frontend.presentation", "manager.settings.page_presentation", SCHEMA_PAGE,
                ("presentation",), "frontend"),
        ("frontend.behavior", "manager.settings.page_behavior", SCHEMA_PAGE, ("behavior",),
                "frontend"),
        ("frontend.themes", "manager.settings.page_themes", SCHEMA_PAGE, ("themes",), "frontend"),
        ("frontend.table_commands", "manager.settings.page_table_commands", SCHEMA_PAGE,
                ("table_commands",), "frontend"),
        ("frontend.chromium", "manager.settings.page_chromium", SCHEMA_PAGE, ("chromium",),
                "frontend"),
    )),
)

# A schema page that also draws switches from a registry, ordered among its settings
# rather than beside them.
# page -> ((registry, its heading, the schema heading it sits above), ...). An empty
# third entry puts the block at the foot; an empty heading is for a block that is the
# whole page, which the page's own name already heads.
PAGE_KINDS: dict[str, tuple[tuple[str, str, str], ...]] = {
    "library.checks": (("library_checks", "", ""),),
    "library.media": (("media_kinds", "manager.settings.heading_kinds", "Wheels"),),
    "library.assets": (("asset_kinds", "", ""),),
    "vpinfe.manager": (("overview_rows", "manager.settings.heading_overview", ""),),
}


def pages_for_features(features: Any) -> list[tuple[str, DevicePage]]:
    """(group, page) for every page the features an install has can answer for.

    `core` is held whatever arrives. A list is taken literally, an empty one being an install
    for nothing; only `None` is an install that has not said, which has every feature.
    Identity and features are not filtered here at all - `system_pages` puts them in
    front - because an install with everything switched off has to be able to switch
    something on, and that screen is the only way in.
    """
    said = install_identity.DEFAULT_FEATURES if features is None else features
    held = {str(f).strip().lower() for f in said} | {install_identity.CORE}
    return [(group, page) for group, pages in DEVICE_INDEX for page in pages
            if held & set(_features_of(page))]


def _features_of(page: DevicePage) -> tuple[str, ...]:
    return (page[4],) if isinstance(page[4], str) else page[4]


# What switching one on gets you. The name says which feature; this says what the install
# then does, which is the half a person switching it on is actually choosing between.
FEATURE_NOTES = {
    install_identity.LIBRARY: "manager.settings.feature.library",
    install_identity.FRONTEND: "manager.settings.feature.launch_games",
    install_identity.DEVICES: "manager.settings.feature.devices",
    install_identity.OVERVIEW: "manager.settings.feature.overview"
}


def features_listed(features: Any) -> list[str]:
    """What an install is for, as the names it reported.

    `core` is left out: every install has it, so a list naming it prints the same word
    on every row. Anything else unrecognized stays, because a device reporting a feature
    this build has not heard of is a fact rather than a blank.
    """
    return [str(name) for name in (features or []) if str(name) != install_identity.CORE]


def feature_said(name: str) -> str:
    """A feature in a person's words."""
    return t(install_identity.LABELS.get(name, name))


IDENTITY = "vpinfe.install"

# The one page that is pinned rather than filtered: this is where features are switched
# on, so an install with none still has a way to fix itself from inside. Not a schema
# page - `features` is a list in the file and a closed set on screen, and a
# comma-separated text field is the wrong control for that.
IDENTITY_PAGE: DevicePage = (IDENTITY, "manager.settings.page_install", BUILT_PAGE,
                             ("device",),
                             install_identity.CORE)

# The same group the rest of the install's own pages are in, so it leads them rather than
# sitting in a group of one. `system_pages` puts it first and nothing filters it.
IDENTITY_GROUP = "manager.settings.group_vpinfe"


EXTENSIONS_GROUP = "manager.settings.group_extensions"


def extension_pages(installed: Any) -> list[tuple[str, DevicePage]]:
    """A page for every installed extension that can run here, last in the index and by the
    name it shows. One made for another platform has no page: nothing on it could be
    changed.

    The label is the extension's own words, already in the language set, so it is not a
    catalog key.
    """
    named = sorted(((str(one.get("display_name") or one.get("name") or ""), str(one["name"]))
                    for one in installed or []
                    if one.get("name") and one.get("reason_key") != extension_host.WRONG_PLATFORM),
                   key=lambda one: (one[0].casefold(), one[1]))
    return [(EXTENSIONS_GROUP, (ext_page.key(name), shown, BUILT_PAGE, (),
                                install_identity.CORE))
            for shown, name in named]


def system_pages(features: Any, installed: Any = ()) -> list[tuple[str, DevicePage]]:
    """This install's own index: identity first, then whatever its features can answer
    for, then the extensions it has."""
    return [(IDENTITY_GROUP, IDENTITY_PAGE), *pages_for_features(features),
            *extension_pages(installed)]


def _page_holding(section: str) -> str:
    """Which page draws a config section, or "" where none does."""
    return next((page[0] for _group, pages in DEVICE_INDEX for page in pages
                 if section in page[3]), "")


def place_of(section: str) -> list[str]:
    """Settings, the group and the page that draw a config section, in the rail's words;
    Settings alone where no page does."""
    return [t("manager.section.settings"),
            *next(([t(group), t(page[1])] for group, pages in DEVICE_INDEX
                   for page in pages if section in page[3]), [])]


def address_for(section: str) -> str:
    """Settings, open at the page that draws a config section."""
    return "/manager?" + deeplink.query({"view": "settings",
                                         "settings_page": _page_holding(section)})


def pages_in_trouble(items: Any) -> dict[str, list[Any]]:
    """Unmet requirements, keyed by the page that carries the setting.

    A requirement whose setting is on no page is logged rather than counted: a badge
    that leads nowhere is worse than no badge, and this is a mistake in the index rather
    than in the install.
    """
    found: dict[str, list[Any]] = {}
    for item in items:
        # Only the ones a settings page can fix. A launcher is not a setting, and it
        # carries its own mark on the entry that leads to it.
        if item.where != feature_checks.WHERE_SETTINGS:
            continue
        if item.section == feature_checks.EXTENSIONS:
            found.setdefault(ext_page.key(item.key), []).append(item)
            continue
        page = folders.KEY if item.section == feature_checks.FOLDERS \
            else _page_holding(item.section)
        if not page:
            logger.warning("No settings page draws %s, so nothing can lead to %s.%s",
                           item.section, item.section, item.key)
            continue
        found.setdefault(page, []).append(item)
    return found


def field_marks(items: Any, checks: list[dict]) -> dict[tuple[str, str], dict]:
    """What to draw beside each path field: what the disk said, then what a feature needs.

    The second overrides the first for the case that matters. A blank path draws nothing
    on its own, because an optional one left empty is a choice - and a required one left
    empty is exactly what the badge leading here is pointing at.
    """
    found = {(str(check.get("section") or ""), str(check.get("key") or "")): dict(check)
             for check in checks or []}
    for item in items:
        state = panel.REQUIRED if item.state == path_state.UNSET else item.state
        found[(item.section, item.key)] = {"state": state, "reason": item.reason}
    return found


def settings_here(schema: list[dict]) -> list[dict]:
    """The schema without the settings another page is the home of."""
    return [{**block, "options": [option for option in block.get("options") or []
                                  if not option.get("home")]}
            for block in schema]


def with_discovery(schema: list[dict], found: list[dict]) -> list[dict]:
    """The Tools section with a row only for each Tool this device could run, and each
    field saying what it stands for left empty."""
    rows = {str(row.get("setting") or ""): row for row in found}
    drawn = []
    for block in schema:
        if block.get("name") != TOOLS:
            drawn.append(block)
            continue
        options = []
        for option in block.get("options") or []:
            row = rows.get(f"{TOOLS}.{option.get('key')}")
            if row is None:
                options.append(option)
            elif row.get("state") != tools.State.NOT_HERE:
                options.append({**option, **_left_empty(row)})
        drawn.append({**block, "options": options})
    return drawn


def _left_empty(row: dict) -> dict[str, str]:
    """What an empty Tool field stands for: the program found, quiet, or Not found with
    how to get one on hover."""
    path = str(row.get("default_path") or "")
    version = str(row.get("default_version") or "")
    if path:
        return {"default_path": path,
                "default_whose": t("manager.settings.tool_found_version", version=version)
                if version else t("manager.settings.tool_found_here")}
    return {"blank": t("word.not_found"), "left_empty": tools.words(row.get("remedy") or {})}


def with_default_paths(schema: list[dict], checks: list[dict]) -> list[dict]:
    """Each blank path setting holding, left empty, the folder it stands for as its text,
    and saying on hover that VPinFE chose it."""
    stands_for = {(str(check.get("section") or ""), str(check.get("key") or "")):
                  str(check["default"]) for check in checks or [] if check.get("default")}
    whose = t("manager.workbench.app_default", app="VPinFE")
    return [{**block, "options": [
        {**option, "default_path": found, "default_whose": whose}
        if (found := stands_for.get((str(block.get("name") or ""), str(option.get("key") or ""))))
        else option for option in block.get("options") or []]} for block in schema]


def with_browser_found(schema: list[dict], found: dict[str, Any]) -> list[dict]:
    """The Browser Program field standing, left empty, for the browser the windows will
    open."""
    stands = _browser_left_empty(found)
    return [{**block, "options": [{**option, **stands} if option.get("key") == "path" else option
                                  for option in block.get("options") or []]}
            if block.get("name") == "chromium" else block
            for block in schema]


def _browser_left_empty(found: dict[str, Any]) -> dict[str, str]:
    if (found.get("state") == frontend_browser.NO_BROWSER
            and found.get("path") == found.get("default_path")):
        return {"blank": t("word.not_found"),
                "left_empty": t("manager.settings.browser_slim_build")
                if found.get("variant") == "slim" else str(found.get("fix_text") or "")}
    return {"default_path": str(found.get("default_path") or ""),
            "default_whose": t("manager.settings.browser_bundled")
            if found.get("default_bundled") else t("manager.settings.browser_found_here")}


def tool_marks(found: list[dict], values: dict,
               marks: dict[tuple[str, str], dict]) -> dict[tuple[str, str], dict]:
    """`marks`, with each Tool whose program is there and does not run marked, set or
    left empty."""
    held = dict(values.get(TOOLS) or {})
    marked = dict(marks)
    for row in found:
        section, _, key = str(row.get("setting") or "").partition(".")
        if not str(held.get(key) or "").strip():
            if row.get("state") == tools.State.UNUSABLE:
                marked[(section, key)] = {"state": panel.UNUSABLE,
                                          "reason": tools.words(row.get("reason") or {})}
            continue
        if (marked.get((section, key)) or {}).get("state") != path_state.OK:
            continue
        if row.get("state") == tools.State.UNUSABLE and row.get("set_here"):
            said = tools.words(row.get("reason") or {})
        elif row.get("state") == tools.State.FOUND and not row.get("set_here"):
            said = t("manager.settings.tool_used_instead", path=str(row.get("path") or ""))
        else:
            continue
        marked[(section, key)] = {"state": panel.UNUSABLE, "reason": said}
    return marked


INPUT = "input"
TAKE_PICTURE = "take_picture"


def play_findings(found: dict, values: dict) -> list[tuple[str, str]]:
    """What stops Take Picture on this device, each as a finding and its fix: pausing a
    table at all, and hearing a key bound to it while one runs."""
    out = []
    press = dict(found.get("press") or {})
    if press and not press.get("available"):
        out.append((t("keys.send.not_sent"), key_simulator.words(press.get("reason") or {})))
    hear = dict(found.get("hear") or {})
    held = (values.get(INPUT) or {}).get(TAKE_PICTURE) or ""
    bindings = held.split(",") if isinstance(held, str) else list(held)
    if hear and not hear.get("available") and input_registry.keys_in(bindings):
        out.append((t("keys.hear.not_heard"), key_reader.words(hear.get("reason") or {})))
    return out


def with_play_input(schema: list[dict], found: dict, values: dict) -> list[dict]:
    """The Take Picture row carrying what stops it here, where something does."""
    findings = play_findings(found, values)
    if not findings:
        return schema
    return [{**block, "options": [{**option, "findings": findings}
                                  if option.get("key") == TAKE_PICTURE else option
                                  for option in block.get("options") or []]}
            if block.get("name") == INPUT else block for block in schema]


def _input_findings(schema: list[dict]) -> dict[str, tuple[Callable[[], None], ...]]:
    """What stops each input on this device, drawn under its row."""
    return {str(option["key"]): tuple(_finding(text, fix)[1]
                                      for text, fix in option.get("findings") or ())
            for block in schema if block.get("name") == INPUT
            for option in block.get("options") or []}


def _finding(text: str, fix: str) -> tuple[Any, Callable[[], None]]:
    def draw() -> None:
        with ui.element("div").classes("manager-attention w-full"):
            ui.icon("error_outline").classes("manager-attention-icon")
            with ui.column().classes("gap-0 min-w-0 grow"):
                ui.label(text).classes("manager-attention-line")
                if fix:
                    ui.label(fix).classes("manager-member-table")

    return (panel.ASIDE, draw)


async def _play_input(library: Library, sections: tuple[str, ...]) -> dict:
    if INPUT not in sections:
        return {}
    try:
        return await offload.io(library.input_play)
    except Exception as exc:  # noqa: BLE001 - the bindings are drawn without it
        logger.warning("Could not read what keys can do during play: %s", why(exc))
        return {}


async def _tools_found(library: Library, sections: tuple[str, ...]) -> list[dict]:
    if TOOLS not in sections:
        return []
    try:
        return await offload.io(library.config_tools)
    except Exception as exc:  # noqa: BLE001 - the fields are drawn without it
        logger.warning("Could not read what the Tools found: %s", why(exc))
        return []


async def _browser_found(library: Library) -> dict[str, Any]:
    try:
        return await offload.io(library.frontend_browser)
    except Exception as exc:  # noqa: BLE001 - the field is drawn without it
        logger.warning("Could not read the browser the frontend found: %s", why(exc))
        return {}


def local_trouble() -> list[Any]:
    """What this install's enabled features are missing, and which of its extensions broke.

    Asked of this machine's own configuration rather than over the API: a path is only
    answerable by the machine holding it, and this is that machine. Its launchers go with
    it, because whether a table can be played is now a question about one of them.
    """
    from common.config.paths import get_ini_config
    from common.games import launchers, library_folders

    store = launchers.get_launcher_store()
    held = library_folders.configured()
    states = library_folders.states_of(held)
    return [*feature_checks.unmet(
        get_ini_config(),
        launcher=launchers.default_for("vpx", store.launchers()),
        locations=[(one, states[one.location_id]) for one in held]),
            *_extension_trouble(), *_backups_folder_trouble()]


def _backups_folder_trouble() -> list[Any]:
    """The folder backups are written to, when it cannot be written to. The default is fine
    until it exists."""
    said, state = backups_folder.refused(backups_folder.current())
    if not said:
        return []
    return [feature_checks.Unmet(feature=install_identity.CORE,
                                 section=backups_folder.SECTION, key=backups_folder.FOLDER,
                                 state=state, reason=said)]


def _extension_trouble() -> list[Any]:
    """An extension that broke. One that cannot run on this device is not here: nothing is
    wrong with it, and which features a device has is the person's choice."""
    return [feature_checks.Unmet(feature=install_identity.CORE,
                                 section=feature_checks.EXTENSIONS, key=record.name,
                                 state=record.state, reason=record.reason)
            for record in extensions.records()
            if record.state in (extension_host.FAILED, extension_host.DISABLED)]


def build_library_page(library: Library, refill: Callable[[], None] | None, key: str,
                       kind: str) -> None:
    """A library page, drawn where a device's rail asks for it.

    These are this install's own: what the library collects, and what it is checked for.
    They reach for registries and for its client rather than for a config schema, which
    is why they are not schema pages and are only offered on an install that holds a
    library.
    """
    found = KIND_PAGES.get(key) if kind != BUILT_PAGE else None
    if found is None:
        panel.facts(ui, [panel.intro(t("manager.settings.unbuilt"))])
        return
    note, _section, name, items = found
    _kind_page(library, refill, note, "", name, items, key in TWO_COLUMNS)


def value_words(option: dict, value: Any) -> str:
    """A value as the control shows it: a choice by its label, a switch On or Off, a
    number with what it counts."""
    kind = str(option.get("type") or "")
    if kind == "bool":
        return t("word.on") if value else t("word.off")
    if kind == "choice":
        return str((option.get("choice_labels") or {}).get(str(value), value))
    if option.get("unit") == config_schema.UNIT_SECONDS:
        return t("manager.settings.seconds", value=str(value))
    return str(value)


def default_said(option: dict, value: Any, resolved: str = "") -> str:
    """The reminder of a setting's default, read from the schema's own value: whose it is,
    and the value where this one is not it. `resolved` is what the default comes to on
    this device, said instead while the setting is left there."""
    whose = str(option.get("default_is") or "")
    if whose not in config_schema.DEFAULTS_ARE:
        return ""
    default = value_for(option, option.get("default"))
    community = whose == config_schema.DEFAULT_COMMUNITY
    if value_for(option, value) == default:
        return resolved or (t("manager.settings.community_standard") if community
                            else t("manager.settings.vpinfe_default"))
    shown = value_words(option, default)
    return (t("manager.settings.community_standard_is", value=shown) if community
            else t("manager.settings.vpinfe_default_is", value=shown))


def _noted(option: dict, value: Any, save: Callable[[Any], Any],
           resolved: str,
           ) -> tuple[Callable[[Any], Any], tuple[Any, Any] | None]:
    """The line under a setting - what it does and its default - and a `save` that keeps
    the default's half true as the value changes."""
    described = str(option.get("description") or "")

    def said(now: Any) -> str:
        reminder = default_said(option, now, resolved)
        if described and reminder:
            return t("manager.settings.described_default", description=described,
                     default=reminder)
        return described or reminder

    first = said(value)
    if not first:
        return save, None
    if not option.get("default_is"):
        return save, panel.note(first)
    shown: dict[str, Any] = {}

    def draw() -> None:
        shown["line"] = panel.line(first)

    async def saved(now: Any) -> bool:
        done = bool(await save(now))
        if done and shown.get("line") is not None:
            shown["line"].set_text(said(now))
        return done

    return saved, (panel.ASIDE, draw)


def section_rows(source: Any, section: str, options: list[dict], values: dict,
                 writable: bool, refill: Callable[[], None],
                 checks: dict[tuple[str, str], dict] | None = None,
                 suggestions: dict[str, Any] | None = None,
                 resolved: dict[str, str] | None = None,
                 browse: Callable[..., dict] | None = None,
                 waiting: Waiting | None = None,
                 from_elsewhere: bool = False,
                 previewed: chrome_options.Preview | None = None,
                 stands_in: dict[str, list[tuple[Any, Any]]] | None = None,
                 linked: attract.Attract | None = None,
                 under: dict[str, list[tuple[Any, Any]]] | None = None,
                 saved: Callable[[], Awaitable[None]] | None = None,
                 after: dict[str, Callable[[], Awaitable[None]]] | None = None,
                ) -> list[tuple[Any, Any]]:
    """One section's settings as fact rows, from whatever is serving them.

    `source` is anything with `put_config` - this install's own client for itself, or
    the client that reaches another machine. Which is the whole reason a device's
    settings page and this one are one page: the schema decides the controls and the
    source decides where the write lands.

    `stands_in` draws other rows where a setting would be, named `section.key`, for a
    setting whose value is better handled one entry at a time than as one field. `linked`
    is the Attract Mode group's own behavior, for the page that has one. `after` maps a
    setting's key to what is awaited once its write has gone through.
    """
    current = dict(values.get(section) or {})
    # What every setting in this section is *effectively* set to, defaults included. An
    # editor that answers for a whole section - which binding two actions both claim -
    # cannot see an action still on its default from `current` alone, because a value
    # equal to its default is not stored.
    effective = {one["key"]: current.get(one["key"], one.get("default"))
                 for one in options}
    entries: list[tuple[Any, Any]] = []
    if not writable:
        entries.append(panel.intro(t("manager.settings.not_writable")))

    names_groups = any(option.get("group") for option in options)
    heading = ""
    for option in by_group(options):
        group = str(option.get("group_label") or "")
        if not group and names_groups:
            group = t("manager.settings.group_other")
        if group and group != heading:
            entries.append((panel.HEADING, group))
            if linked is not None:
                entries += linked.under_heading(str(option.get("group") or ""))
        heading = group
        if (replaced := (stands_in or {}).get(f"{section}.{option['key']}")) is not None:
            entries += replaced
            continue
        value = current.get(option["key"], option.get("default"))
        refusal = Refusal() if option.get("unit") == config_schema.UNIT_PORT else None
        save, note = _noted(option, value, saver(source, section, option["key"], refusal),
                            (resolved or {}).get(option["key"], ""))
        if linked is not None:
            save = linked.saves(option["key"], save)
        if option.get("cuts_off_manager") and from_elsewhere:
            save = _asked_first(option, save, refill)
        if option.get("moves_with") == config_schema.MOVES_BACKUPS and isinstance(
                source, Library):
            save = backups_move.asked_first(source, save, refill)
        if section == dof.SECTION and option["key"] in dof.READ_BELOW:
            save = _redrawn_after(save, refill)
        if option["key"] in (after or {}):
            save = _then(save, (after or {})[option["key"]])
        chipped = waiting if option.get("applies") in (
            config_schema.APPLIES_RESTART, config_schema.APPLIES_WINDOWS) else None
        if chipped is not None:
            save = chipped.after(save)
        if previewed is not None and option["key"] in chrome_options.KEYS:
            save = previewed.after(option["key"], save)
        if previewed is not None and option.get("editor") == config_schema.EDITOR_CHROME_LEAVE_OUT:
            control = previewed.leave_out(option, value, save, section=effective,
                                          writable=writable)
        elif (section, option["key"]) in config_schema.SECRET_SETTINGS:
            control = secret_setting(option, value, save, writable=writable)
        else:
            control = control_for(
                option, value, save,
                writable=writable, refill=refill,
                check=(checks or {}).get((section, option["key"])),
                suggestions=suggestions,
                section_values=effective, browse=browse, refusal=refusal, saved=saved)
        if linked is not None:
            control = linked.draws(option["key"], control)
        entries.append((option.get("label") or humanize(option["key"]),
                        chipped.beside(control, option) if chipped is not None
                        else control))
        if note is not None:
            entries.append(note)
        entries += (under or {}).get(option["key"], [])
        if linked is not None:
            entries += linked.under(option["key"])
        if previewed is not None and option["key"] == chrome_options.ADD:
            entries.append(previewed.warning_row())
        if section == "dof" and option["key"] == "config_tool_api_key":
            entries.append((panel.ASIDE, panel.link_out(t("manager.settings.dof_key_site"),
                                                        to=DOF_CONFIG_TOOL)))
        for text, fix in option.get("findings") or ():
            entries.append(_finding(text, fix))
        if option.get("editor") == config_schema.EDITOR_CAPTURE_COMMAND:
            recording.add_under(entries, option)
        # Under the last field of a pair, where somebody has just read what it does and
        # is about to type into it.
        if section == "commands" and option["key"] == "on_vpinfe_exit":
            commands_help.add_to(entries, tokens.VPINFE)
        elif section == "table_commands" and option["key"] == "on_exit":
            commands_help.add_to(entries, tokens.TABLE)
    return entries


async def build_device_page(source: Any, context: dict[str, Any], schema: list[dict],
                            values: dict, sections: tuple[str, ...],
                            checks: dict[tuple[str, str], dict] | None = None,
                            suggestions: dict[str, Any] | None = None,
                            blocks: list[tuple[str, list]] | None = None,
                            browse: Callable[..., dict] | None = None,
                            drawn_elsewhere: bool = False,
                            waiting: Waiting | None = None,
                            stands_in: dict[str, list[tuple[Any, Any]]] | None = None,
                                       linked: attract.Attract | None = None,
                            below: dict[str, dict[str, list[tuple[Any, Any]]]] | None = None,
                            ) -> None:
    """One page of a device's settings, drawn exactly as this install's are.

    Several config sections can make one page - a machine's screens are four of them -
    with a heading each where there is more than one. How the config file is divided is
    not how somebody looks for a setting, which is why the pages are declared rather
    than taken from the schema's own shape.

    `drawn_elsewhere` leaves out the settings the screens picture draws. Only a page that
    draws the picture asks for it: without one, those settings are the only way to set
    them.

    `checks` is what to draw beside each path field, and only the install being drawn can
    supply it. The machine holding a path answers for it, so asking this one about
    another machine's disk would put a red cross on a file that is perfectly fine over
    there - which is why a device's pages pass none. `browse` is the same story: browsing
    is always this machine's own disk, so a device's pages pass none of that either.
    """
    def refill() -> None:
        again = context.get("refill")
        if again is not None:
            again()
            return
        rebuild = context.get("rebuild")
        if rebuild is not None:
            ui.timer(0.01, rebuild, once=True)

    @on_page
    async def saved() -> None:
        settle = context.get("saved")
        if settle is not None:
            await settle()
        else:
            refill()

    drawn = [block for block in schema
             if str(block.get("name")) in sections and block.get("options")]
    if drawn_elsewhere:
        drawn = displays.without_drawn(drawn)
    if not drawn and not blocks:
        panel.facts(ui, [panel.intro(t("manager.settings.page_empty"))])
        return

    # Read before the first wait: the page being drawn is only known to the client until then.
    from_elsewhere = source is context.get("library") and binding_table.opened_elsewhere()
    entries: list[tuple[Any, Any]] = []
    for block in drawn:
        name = str(block.get("name"))
        if len(drawn) > 1:
            entries.append((panel.HEADING, _section_label(name)))
        # A page may carry a head or a foot for what on it is a reading or an act rather
        # than a value. Only where this install's own client is what serves the page: these
        # reach for the library, which another machine's client cannot answer for.
        offered = suggestions or {}
        head = HEADS.get(name)
        lead: list[tuple[Any, Any]] = []
        read: dict[str, Any] = {}
        if head is not None and source is context.get("library"):
            lead = await head(source, refill, values, offered=offered, read=read)
        resolves = RESOLVES.get(name)
        # Where this install's own client serves the page, for the same reason as the head.
        preview = chrome_options.Preview(values) \
            if name == chrome_options.SECTION and source is context.get("library") else None
        beneath = UNDER_SETTINGS.get(name)
        placed = {**(await beneath(values) if beneath is not None
                     and source is context.get("library") else {}),
                  **(below or {}).get(name, {})}
        rows = section_rows(source, name, block["options"], values,
                            bool(block.get("writable")), refill, checks,
                            suggestions,
                            under=placed or None,
                            resolved=resolves(offered) if resolves else None,
                            browse=browse, waiting=waiting,
                            from_elsewhere=from_elsewhere, previewed=preview,
                            stands_in=stands_in,
                            linked=linked if name == attract.SECTION else None,
                            saved=saved)
        # A section that opens with a heading keeps its head under it: nothing sits
        # above the first heading.
        under = 1 if rows and rows[0][0] is panel.HEADING else 0
        own = [*rows[:under], *lead, *rows[under:]]
        follow = UNDER_FIRST_GROUP.get(name)
        if follow is not None and source is context.get("library"):
            own = _after_first_group(own, await follow(source, read))
        entries += own
        if preview is not None:
            entries.append(preview.row())
        foot = FOOTERS.get(name)
        if foot is not None and source is context.get("library"):
            entries += await foot(source, refill, offered=offered)
    panel.facts(ui, _spliced(entries, blocks or []))


def _after_first_group(own: list, rows: list) -> list:
    """`rows` put at the end of a section's first group, before its second heading."""
    headings = [i for i, one in enumerate(own) if one[0] is panel.HEADING]
    at = headings[1] if len(headings) > 1 else len(own)
    return own[:at] + rows + own[at:]


def _spliced(entries: list, blocks: list[tuple[str, list]]) -> list:
    """Registry blocks put above the heading each one names, or at the foot.

    By heading rather than by index: a block says which part of the page it belongs
    above, and moving a setting in the schema moves the heading with it.
    """
    for above, rows in blocks:
        at = next((i for i, one in enumerate(entries)
                   if one[0] is panel.HEADING and str(one[1]) == above), len(entries))
        entries = entries[:at] + rows + entries[at:]
    return entries



def build_system(library: Library, state: dict[str, Any], redraw: Callable[[], None],
                 discovery: dict[str, Any]) -> None:
    """This install's own configuration: the index, and the page it opens.

    Its own entry point rather than a section of the device panel, because it is about
    the install you are reading the Manager from and needs nothing selected to be about
    one.
    """
    pages = system_pages(discovery.get("features"), state.get("extensions") or [])
    known = {page[0]: page for _group, page in pages}
    chosen = str(state.get("settings_page") or "")
    # A page this install has no answer for is not a place to land: an address written
    # while `library` was on still names Media Kinds after it has been switched off.
    if chosen not in known:
        chosen = pages[0][1][0]
    state["settings_page"] = chosen

    work, draw_index = panel.sections(_index_entries(state, pages), chosen,
                                      lambda key: open_page(state, redraw, key),
                                      rail_px=RAIL_PX, keep="settings")

    def reindex() -> None:
        draw_index(_index_entries(state, system_pages(discovery.get("features"),
                                                      state.get("extensions") or [])),
                   str(state.get("settings_page") or ""))

    state["reindex_settings"] = reindex
    with work:
        body = ui.column().classes("min-w-0 overflow-auto gap-0 manager-workbench-body "
                                   "manager-settings-body")
    # Later, because a page reads the schema and the values over HTTP and the draw it is
    # part of runs on the event loop, where the client refuses a call.
    busy.fill(body, lambda: _draw_system_page(library, redraw, body, known[chosen],
                                              discovery, state.get("trouble") or [], state))


def _index_entries(state: dict[str, Any],
                   pages: list[tuple[str, DevicePage]]) -> list[tuple[Any, ...]]:
    """The index's rows, with what is misconfigured marked at every level on the way to the
    setting that fixes it: the group, then the page, then the field's own cross."""
    hurt = pages_in_trouble(state.get("trouble") or [])
    groups_hurt = {group for group, page in pages if page[0] in hurt}
    off = {ext_page.key(str(one.get("name"))) for one in state.get("extensions") or []
           if one.get("state") == extension_host.OFF}

    entries: list[tuple[Any, ...]] = []
    heading = ""
    for group, page in pages:
        if group != heading:
            entries.append((panel.GROUP, group, "",
                            panel.trouble_mark() if group in groups_hurt else None))
            heading = group
        found = hurt.get(page[0]) or []
        entries.append((page[0], page[1], "",
                        panel.trouble_mark(_said(found)) if found
                        else panel.quiet_mark(t("word.off")) if page[0] in off else None))
    return entries


def open_page(state: dict[str, Any], redraw: Callable[[], None], key: str) -> None:
    """Show another page of the index. The open one is already shown: its row is not a
    toggle, and a redraw would drop what is half typed on it."""
    if state.get("settings_page") == key:
        return
    state["settings_page"] = key
    redraw()
    # The address was read on arrival and never written again, so a page opened by
    # hand was lost on the next reload - and a reload is not always something you
    # chose.
    deeplink.sync(state, push=True)


def _said(items: Any) -> str:
    """The reasons behind one mark, in the words the check already wrote for the person
    who has to fix them. De-duplicated: two features needing one setting is two entries
    saying the same sentence."""
    return " ".join(dict.fromkeys(item.reason for item in items if item.reason))


async def _draw_system_page(library: Library, redraw: Callable[[], None], body: Any,
                            page: DevicePage, discovery: dict[str, Any],
                            trouble: Any, state: dict[str, Any] | None = None) -> None:
    key, _label, kind, sections, _feature = page
    if key == IDENTITY:
        with body:
            page_head(key)
            await _identity_page(library, str(discovery.get("display_name") or ""),
                                 redraw, state or {}, str(discovery.get("install_id") or ""))
        return
    if key.startswith(ext_page.PREFIX):
        drawn = _extension_region(redraw, key, state or {})
        with body:
            drawn()
        await drawn.refilled()
        return
    if kind == BUILT_PAGE and key in PAGES:
        draw = await PAGES[key](library, redraw, state if state is not None else {})
        with body:
            page_head(key)
            draw()
        if isinstance(draw, panel.Region):
            await draw.refilled()
        return
    if kind != SCHEMA_PAGE:
        with body:
            page_head(key)
            build_library_page(library, redraw, key, kind)
        return
    page_drawn = SchemaPage(library, key, sections, state, trouble)
    with body:
        page_drawn.region()
    await page_drawn.region.refilled()


class SchemaPage:
    """A schema page, drawn into a region that stays where it is when a value changes.

    A write that something else on the page reads draws the region again beside the old
    one and swaps it in, so the page is not dimmed and the rest of the view is not touched.
    """

    def __init__(self, library: Library, key: str, sections: tuple[str, ...],
                 state: dict[str, Any] | None, trouble: Any) -> None:
        self.library, self.key, self.sections = library, key, sections
        self.state, self.trouble = state, trouble
        self.read: tuple[Any, ...] | None = None
        self.region = panel.region(self.draw, self._shown)

    def _shown(self) -> None:
        block = (self.state or {}).get("theme_sources")
        if block is not None:
            block.stale = False

    async def draw(self) -> None:
        self.read = await _draw_schema_page(self.library, self.key, self.sections,
                                            self._trouble(), self.state, self.region.refill,
                                            self.settled)

    def _trouble(self) -> Any:
        if self.state is not None and "trouble" in self.state:
            return self.state["trouble"] or []
        return self.trouble

    async def settled(self) -> None:
        """After a write that can mend or break something the page marks: the rail's marks
        are read again, and the page is drawn again only if what its marks come from has
        changed. A write that leaves them as they were has nothing to show."""
        recheck = (self.state or {}).get("recheck_trouble")
        if recheck is not None:
            await recheck()
        read = await _marked(self.library, self.sections, self._trouble())
        if read != self.read:
            self.region.refill()


async def _marked(library: Library, sections: tuple[str, ...], trouble: Any) -> tuple[Any, ...]:
    """What the marks on a settings page are read from: the path checks, what the enabled
    features are missing, what the Tools found and the browser the frontend found."""
    return (await offload.io(library.config_path_checks), trouble,
            await _tools_found(library, sections),
            await _browser_found(library) if "chromium" in sections else None)


async def _draw_schema_page(library: Library, key: str, sections: tuple[str, ...],
                            trouble: Any, state: dict[str, Any] | None,
                            refill: Callable[[], None],
                            saved: Callable[[], Awaitable[None]]) -> tuple[Any, ...] | None:
    """The head and the settings of one schema page, drawn where the caller stands.

    Answers what its marks were read from, so a later write can tell whether they moved.
    """
    try:
        schema = settings_here(await offload.io(library.config_schema))
        sections = _with_windows(sections, schema)
        values = await offload.io(library.config_values)
        checks = await offload.io(library.config_path_checks)
        offered = await _suggestions(library, schema, sections)
    except Exception as exc:  # noqa: BLE001 - a settings page says why, never 500s
        panel.facts(ui, [panel.intro(t("said.settings_unreadable"), hint=why(exc))])
        return None
    marks = field_marks(trouble, checks)
    found = await _tools_found(library, sections)
    if found:
        schema = with_discovery(schema, found)
        marks = tool_marks(found, values, marks)
    schema = with_default_paths(schema, checks)
    browser = await _browser_found(library) if "chromium" in sections else None
    if browser:
        schema = with_browser_found(schema, browser)
    if play := await _play_input(library, sections):
        schema = with_play_input(schema, play, values)
    blocks = []
    if key == input_page.KEY:
        sections = tuple(name for name in sections if name != INPUT)
        blocks.append(("", input_page.rows(
            schema, values, lambda changes: _write_many(library, changes),
            findings=_input_findings(schema))))
    reads_kinds = config_schema.EDITOR_THUMBNAIL in offered
    for registry, heading, above in PAGE_KINDS.get(key, ()):
        rows = await _kind_rows(
            library, refill if reads_kinds and registry == "media_kinds" else None, registry)
        head = [(panel.HEADING, t(heading))] if heading else []
        blocks.append((above, [*head, *rows]))
    findings = [drawn for name in sections if name in FINDINGS
                if (drawn := FINDINGS[name](offered, library, refill)) is not None]
    if key == displays.KEY:
        @on_page
        async def follow(before: Any, after: Any, here: Any) -> None:
            await vpx_displays.follow(library, before, after, here)
            try:
                await offload.io(placement_panel.resync)
            except OSError as exc:
                ui.notify(t("manager.placement.update_failed"), caption=why(exc),
                          type="warning")

        def write(changes: dict[str, dict[str, Any]]) -> Any:
            return _write_many(library, changes)

        monitors = offered.get(config_schema.SUGGEST_SCREENS, [])
        names = await offload.io(display_service.get_display_names)
        blocks.append((_section_label("windows.backglass"), [
            *displays.rows(values, schema, monitors, names, write, refill, follow=follow),
            *await vpx_displays.apart_rows(library, displays.standing(values, schema),
                                           screens.numbered(monitors, names), refill),
            *placement_panel.rows(await run.io_bound(placement_panel.read, os.environ),
                                  values, schema, write, refill)]))
    stands_in = await _theme_sources(library, state if state is not None else {}, refill) \
        if theme_sources.SECTION in sections else None
    linked = None
    if attract.SECTION in sections:
        theme, left = attract.left_to_theme(await _themes_listing(library))
        linked = attract.Attract(lambda changes: _write_many(library, changes), values,
                                 _options_of(schema, attract.SECTION),
                                 writable=_writable(schema, attract.SECTION),
                                 theme=theme, left_to_theme=left)
        stands_in = {**(stands_in or {}), **linked.stands_in()}
    page_head(key, findings)
    await build_device_page(library, {"library": library, "refill": refill, "saved": saved},
                            schema, values, sections, checks=marks,
                            suggestions=offered, blocks=blocks, browse=library.folders,
                            drawn_elsewhere=key == displays.KEY,
                            waiting=Waiting(library, await _waiting(library)),
                            stands_in=stands_in, linked=linked,
                            below=_preferred_formats(library, key, schema, values, refill,
                                                     saved))
    return (checks, trouble, found, browser)


def _preferred_formats(library: Library, key: str, schema: list[dict], values: dict,
                       refill: Callable[[], None],
                       saved: Callable[[], Awaitable[None]]) -> dict[str, dict[str, list]]:
    """Recording's Video Format and Quality, drawn on the Media page too, between Sounds and
    Keep Originals: Convert reads the same two settings, so there is one value to set and
    either page changes it."""
    if key != "library.media":
        return {}
    block = next((one for one in schema if one.get("name") == recording.SECTION), {})
    shared = {one["key"]: one for one in block.get("options") or []}
    rows: list[tuple[Any, Any]] = []
    for name in ("video_codec", "quality"):
        option = shared.get(name)
        if option is None:
            continue
        drawn = {**option, "group": "", "group_label": ""}
        if name == "video_codec":
            drawn["label"] = t("manager.settings.preferred_videos.label")
            drawn["description"] = t("manager.settings.preferred_videos.description")
        rows += section_rows(library, recording.SECTION, [drawn], values,
                             bool(block.get("writable")), refill, saved=saved)
    return {"media": {"preferred_sound": rows}}


def _options_of(schema: list[dict], section: str) -> list[dict]:
    return next((list(block.get("options") or []) for block in schema
                 if block.get("name") == section), [])


def _writable(schema: list[dict], section: str) -> bool:
    return any(block.get("writable") for block in schema if block.get("name") == section)


async def _themes_listing(library: Library) -> dict[str, Any]:
    try:
        return await offload.io(library.themes)
    except Exception as exc:  # noqa: BLE001 - the page is drawn without the theme line
        logger.warning("Could not read the themes: %s", why(exc))
        return {}


def _extension_region(redraw: Callable[[], None], key: str,
                      state: dict[str, Any]) -> panel.Region:
    """One extension's page: what its settings say is read, then drawn. A change on it
    reads the extensions again, so the rail follows what is running, and draws this part
    again in place."""
    first = {"read": True}

    async def draw() -> None:
        if not first.pop("read", False) and (again := state.get("read_extensions")) is not None:
            try:
                await again()
            except Exception:  # noqa: BLE001 - the page is drawn from what it already holds
                logger.exception("Could not read the installed extensions")
        found: dict[str, Any] = next((one for one in state.get("extensions") or []
                                      if ext_page.key(str(one.get("name"))) == key), {})
        settings = await ext_page.read(found)
        ext_page.build(found, settings, region.refill,
                       lambda: open_page(state, redraw, IDENTITY))

    region = panel.region(draw)
    return region


async def _theme_sources(library: Library, state: dict[str, Any],
                         redraw: Callable[[], None]) -> dict[str, list[tuple[Any, Any]]] | None:
    """The two address fields, stood in for by the sources and what each said. Kept on the
    page's state for the drag listeners, which are registered once and read it at the drop."""
    try:
        found = await offload.io(library.theme_sources)
    except Exception as exc:  # noqa: BLE001 - the page keeps its fields rather than lose the setting
        logger.warning("Could not read the theme sources: %s", why(exc))
        return None
    # Each address is read once per page, so a read that gives no result is not asked again
    # by the redraw it causes.
    asked: set[str] = state.setdefault("theme_sources_asked", set())
    unread = {str(one["address"]) for group in (theme_sources.LISTS, theme_sources.THEMES)
              for one in found.get(group) or []
              if one["state"] == online_checks.NOT_CHECKED} - asked
    block = theme_sources.Block(library, found, redraw, frozenset(unread), asked)
    # The rows of the old block are still on screen until the page swaps, and a drop on
    # them means nothing against these addresses. `SchemaPage` clears this at the swap.
    block.stale = state.get("theme_sources") is not None
    state["theme_sources"] = block
    return {f"{theme_sources.SECTION}.{theme_sources.LISTS}": theme_sources.rows(block, found),
            f"{theme_sources.SECTION}.{theme_sources.THEMES}": []}


async def _waiting(library: Library) -> dict[str, Any]:
    try:
        return await offload.io(library.config_waiting)
    except Exception as exc:  # noqa: BLE001 - the page is drawn without its chips
        logger.warning("Could not read what is waiting: %s", why(exc))
        return {}


def _with_windows(sections: tuple[str, ...], schema: list[dict]) -> tuple[str, ...]:
    """A page drawing windows draws every window the schema serves."""
    if not any(config_schema.window_name(name) for name in sections):
        return sections
    served = [str(block.get("name")) for block in schema]
    return (*sections, *(name for name in served
                         if config_schema.window_name(name) and name not in sections))


async def _suggestions(library: Library, schema: list[dict],
                       sections: tuple[str, ...]) -> dict[str, Any]:
    """The live lists this page's settings and editors say are worth offering.

    Asked for only where a setting on this page declares one, so opening Displays does
    not go and look at the network.
    """
    here = [option for block in schema if str(block.get("name")) in sections
            for option in block.get("options") or []]
    wanted = {str(option.get("suggest") or "") for option in here}
    editors = {str(option.get("editor") or "") for option in here}
    offered: dict[str, Any] = {}

    if config_schema.EDITOR_CAPTURE_COMMAND in editors:
        try:
            offered[config_schema.EDITOR_CAPTURE_COMMAND] = await offload.io(
                library.capture_report)
        except Exception as exc:  # noqa: BLE001 - the page says it could not read it
            offered[config_schema.EDITOR_CAPTURE_COMMAND] = {"error": why(exc)}

    if config_schema.EDITOR_THUMBNAIL in editors:
        try:
            policy = await offload.io(library.library_policy)
        except Exception as exc:  # noqa: BLE001 - every kind is offered instead
            logger.warning("Could not read the media kinds the library keeps: %s", why(exc))
        else:
            offered[config_schema.EDITOR_THUMBNAIL] = _listed(
                policy.get("hidden_media_kinds"))

    known: dict[str, Any] = {}
    if config_schema.EDITOR_FRONTEND_THEME in editors or config_schema.SUGGEST_THEMES in wanted:
        known = await offload.io(library.themes)
    themes = [theme for theme in known.get("themes") or [] if theme.get("key")]

    if config_schema.EDITOR_FRONTEND_THEME in editors:
        offered[config_schema.EDITOR_FRONTEND_THEME] = {
            str(theme["key"]): theme for theme in themes}

    if config_schema.SUGGEST_LIBRARIES in wanted:
        found = await offload.io(library.discovered_installs)
        # Only the ones that have a library to read. An install that just launches games
        # has nothing to offer another that does the same.
        offered[config_schema.SUGGEST_LIBRARIES] = {
            str(install.get("url") or ""): _install_label(install)
            for install in found
            if install_identity.LIBRARY in (install.get("features") or [])}

    if config_schema.SUGGEST_THEMES in wanted:
        # Keyed by what is stored, labelled by what the theme calls itself - they are
        # usually the same word and a theme is free to make them differ.
        offered[config_schema.SUGGEST_THEMES] = {
            str(theme["key"]): str(theme.get("name") or theme["key"]) for theme in themes}

    if config_schema.SUGGEST_COLLECTIONS in wanted:
        # `collections()` answers from what the Collections page last read, which on
        # this page is nothing. This is already off the event loop, so read.
        held = await offload.io(library.load_collections)
        offered[config_schema.SUGGEST_COLLECTIONS] = {
            str(row.get("name") or ""): str(row.get("name") or "")
            for row in held if row.get("name")}

    if config_schema.SUGGEST_SCREENS in wanted:
        offered[config_schema.SUGGEST_SCREENS] = await offload.io(screens.connected)

    if config_schema.SUGGEST_WHEEL_SETS in wanted:
        try:
            sets = await offload.io(library.wheel_sets)
        except Exception as exc:  # noqa: BLE001 - a set can still be typed
            logger.warning("Could not read the wheel sets: %s", why(exc))
            sets = []
        # `logo` is the one set that is not a folder, so it gets words of its own.
        offered[config_schema.SUGGEST_WHEEL_SETS] = {
            name: t("manager.settings.wheel_set_logo") if name == "logo" else name
            for name in sets}

    return offered


def _install_label(install: dict) -> str:
    """What a discovered install is called in a list, with its address.

    Both, because a name is what somebody recognizes and the address is what they are
    actually choosing - and two machines on one network can share a name.
    """
    name = str(install.get("display_name") or "").strip()
    url = str(install.get("url") or "")
    return t("manager.settings.install_at", name=name, url=url) if name else url


async def _identity_page(library: Library, reported: str,
                         redraw: Callable[[], None],
                         state: dict[str, Any] | None = None,
                         install_id: str = "") -> None:
    """What this install is called, and what it is for."""
    try:
        values = await offload.io(library.config_values)
    except Exception as exc:  # noqa: BLE001 - a settings page says why, never 500s
        panel.facts(ui, [panel.intro(t("said.settings_unreadable"), hint=why(exc))])
        return
    held = dict(values.get("device") or {})
    on = _listed(held.get("features"))

    async def rename(text: str) -> None:
        if await _write(library, "device", "name", text.strip()):
            held["name"] = text.strip()

    @on_page
    async def flip(name: str, wanted_on: bool) -> None:
        # Switching the last one off is allowed. An install for nothing is a real state
        # and this page is still here in it: everything you need to make it for
        # something belongs to `core`.
        after = (on | {name}) if wanted_on else (on - {name})
        # In the order the install declares them, so the file reads the same however the
        # switches were thrown.
        if await _write(library, "device", "features",
                        [name for name in install_identity.FEATURES if name in after]):
            on.clear()
            on.update(after)
            await _shell_follows(state)

    # Bound by a call rather than captured off the loop variable, which a lambda
    # would read only when it fires.
    def flipper(name: str) -> Callable[[Any], Any]:
        return lambda event: flip(name, bool(event.value))

    async def draw() -> None:
        language = config_schema.option("device", "language")
        waiting = Waiting(library, await _waiting(library))

        @on_page
        async def relanguage(event: Any) -> None:
            # The language is read when the install starts, so a saved change waits for a
            # restart, and the chip beside the select says so.
            if await _write(library, "device", "language", event.value):
                held["language"] = event.value
                await waiting.refresh()

        spoken = str(held.get("language") or (language.default if language else ""))
        # A language set by hand to one the list lacks is still the one in use.
        offered = dict.fromkeys((*(language.choices if language else ()), spoken))
        entries: list[tuple[Any, Any]] = [
            (panel.HEADING, t("word.identity")),
            # The name it reports with nothing set is its hostname, so the placeholder is
            # that answer rather than the word for it.
            (t("word.name"), panel.field(str(held.get("name") or ""),
                                                     rename,
                                 placeholder=reported)),
            panel.note(t("manager.settings.name_note")),
        ]
        if language is not None:
            entries.append((language.label or humanize(language.key),
                            waiting.beside(
                                panel.select({value: language.choice_labels.get(value, value)
                                              for value in offered},
                                             spoken, relanguage),
                                {"section": language.section, "key": language.key})))
            if language.description:
                entries.append(panel.note(language.description))
        entries += await _version_entries(library, held, reported, install_id,
                                          part.refill)
        entries += [
            (panel.HEADING, t("manager.settings.features")),
            panel.intro(t("manager.settings.features_note")),
        ]
        for name in install_identity.FEATURES:
            entries.append((t(install_identity.LABELS[name]),
                            panel.switch(name in on, flipper(name))))
            entries.append(panel.note(t(FEATURE_NOTES[name])))
        entries += _setup_entries()
        panel.facts(ui, entries)

    # The version can be asked of GitHub, and a change here draws this part in place.
    part = panel.region(draw)
    part()
    await part.refilled()


async def _version_entries(library: Library, held: dict[str, Any], reported: str,
                           install_id: str, refill: Callable[[], None]) -> list[tuple[Any, Any]]:
    """The version this install runs and where its updates come from, drawn by the same
    rows the Devices page gives it."""
    from manager.devices import devices as devices_page

    async def rebuild() -> None:
        refill()

    context = {"device": {"device_id": install_id,
                          "display_name": str(held.get("name") or reported)},
               "local_device_id": install_id, "library": library, "rebuild": rebuild}
    asking = asyncio.ensure_future(devices_page.version_rows(context))
    await asyncio.wait({asking}, timeout=VERSION_WAIT)
    if asking.done():
        rows = asking.result()
    else:
        # GitHub is slow to answer. The page draws with what is running and fills in the
        # rest when the answer is kept, so the fields below it do not wait.
        rows = [(t("word.version"), get_version())]
        asking.add_done_callback(lambda _: refill())
    entries: list[tuple[Any, Any]] = [(panel.HEADING, t("word.version")), *rows]
    channel = config_schema.option("device", "update_channel")
    if channel is None:
        return entries

    @on_page
    async def rechannel(event: Any) -> None:
        if await _write(library, "device", "update_channel", event.value):
            held["update_channel"] = event.value
        refill()

    entries.append((channel.label or humanize(channel.key),
                    panel.select({value: channel.choice_labels.get(value, value)
                                  for value in channel.choices},
                                 str(held.get("update_channel") or channel.default),
                                 rechannel)))
    if channel.description:
        entries.append(panel.note(channel.description))
    return entries


def _setup_entries() -> list[tuple[Any, Any]]:
    """The way back into the first-run questions."""
    return [
        (panel.HEADING, t("manager.setup.title")),
        panel.intro(t("manager.setup.again_note")),
        (panel.FULL, panel.action(t("manager.setup.open"),
                                  lambda: setup_page.open_setup(), icon=verbs.GO,
                                  hint=t("manager.setup.open.help"))),
    ]


async def _shell_follows(state: dict[str, Any] | None) -> None:
    """After a write that decides what the Manager shows: the shell reads the install
    again and draws its rail from it, and this index follows. The open page stays."""
    held = state or {}
    if (refresh := held.get("refresh_shell")) is not None:
        await refresh()
    if (reindex := held.get("reindex_settings")) is not None:
        reindex()
