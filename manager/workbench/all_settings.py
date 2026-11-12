"""The settings rows: one per setting, with their marks, and the all-settings view."""

from __future__ import annotations

import asyncio
import inspect
import json
from collections import Counter
from collections.abc import (
    Awaitable,
    Callable,
    Collection,
    Coroutine,
    Iterable,
    Mapping,
    Sequence,
)
from functools import partial
from itertools import groupby
from types import SimpleNamespace
from typing import Any

from nicegui import run, ui

from common import apps
from common.apps import OWN_FIELDS
from common.apps.contract import SCOPE_ENTRY
from common.config import path_state
from common.failures import why
from common.i18n import t
from manager.frontend import launchers as launchers_page
from manager.kit import confirm, deeplink, dropdown, offload, panel, verbs
from manager.kit.data import Library
from manager.kit.on_page import on_page
from manager.settings import screens, vpx_displays, vpx_input
from manager.settings import settings as settings_page
from manager.workbench.core import (
    _FOCUSED_SETTING,
    _REFOCUS_IN_PLACE,
    _REFOCUS_SETTING,
    FULL,
    HEADING,
    Section,
    _choose,
    _table_line,
)


async def config_applied(write: Callable[..., Any], values: dict[str, str]) -> Any:
    """`values` through a panel's `config_write`, which is a coroutine function where the
    write waits on a job and a plain one, to run off the loop, where it does not."""
    if inspect.iscoroutinefunction(write):
        return await write(values)
    return await run.io_bound(write, values)


# The app's declared groups, as rail sections. Fixed rather than read at import: a
# section key has to be stable for `section=` in an address to mean one thing, and the
# groups an app declares are its own vocabulary rather than this file's.
CONFIG_GROUP_KEYS = ("displays", "input", "sound", "graphics", "plugins")


def _launcher_config_sections() -> tuple[Section, ...]:
    """One rail section per group the app declares, in the order it declares them.

    A group the launcher's app does not offer draws nothing and the rail leaves it out,
    which is how 10.8.0 - where the plugin architecture does not exist - shows fewer
    sections rather than empty ones.
    """
    return tuple(
        Section(f"launcher_{key}", _config_group_label(key), _config_group_block(key),
                subjects=frozenset({"launcher"}), shown=_config_group_shown(key))
        for key in CONFIG_GROUP_KEYS)


def _config_group_shown(key: str) -> Callable[[dict[str, Any]], bool]:
    def shown(context: dict[str, Any]) -> bool:
        if not _program_is_there(context):
            return False
        found = _group(context, key)
        return found is not None and bool(found.settings)
    return shown


def _program_is_there(context: dict[str, Any]) -> bool:
    """Whether the program this launcher names is on this machine, which is where its
    settings are read from. Details stays without it, to point it somewhere else."""
    return _program_state(context.get("launcher") or {}) == path_state.OK


def _program_state(launcher: dict[str, Any]) -> str:
    checks = launcher.get("checks") or {}
    return str((checks.get("bin_path") or {}).get("state") or "")


def _program_note(launcher: dict[str, Any]) -> str:
    """Why its program's own settings are not shown, or "" where they are or it has
    none to show."""
    state = _program_state(launcher)
    if not launcher.get("has_config"):
        return ""
    if state == path_state.OK:
        return ""
    if state == path_state.UNSET:
        return t("manager.workbench.program_hint", app=launcher["app_name"])
    return t("manager.workbench.app_missing", app=launcher["app_name"])


def awaiting_file(launcher: dict[str, Any]) -> bool:
    """Whether its program is there and has not written its settings file, which is where
    it can be asked to."""
    return bool(launcher.get("has_config")) and _program_state(launcher) == path_state.OK \
        and launcher.get("has_settings_file") is False


def _config_group_label(key: str) -> Callable[[dict[str, Any]], str]:
    def said(context: dict[str, Any]) -> str:
        found = _group(context, key)
        return found.label if found is not None else key.title()
    return said


def _group(context: dict[str, Any], key: str) -> Any:
    return next((g for g in context.get("config_groups") or [] if g.key == key), None)


def _config_group_block(key: str) -> Callable[[dict[str, Any]], Any]:
    async def draw(context: dict[str, Any]) -> None:
        found = _group(context, key)
        if found is None or not found.settings:
            return
        await _config_rows(context, found)
    return draw


# Where a value came from, in the words somebody would use. Never the wire's: a
# setting nobody has touched is not "unset", it is what the program does by default, and
# saying so takes no word at all - an unmarked row is the untouched one, so a mark always
# means somebody did something.
CAME_FROM = {
    "launcher": "manager.workbench.the_launcher",
    "folder": "manager.workbench.this_game",
    "entry": "manager.workbench.table",
}


def _config_mark(held: dict, scope: str, field: Any) -> Callable[[], None] | None:
    """The word beside a value, where it is an exception: a scope other than this one and
    All Tables. Set here is the dot's to say (`_marked`)."""
    came = held.get("scope") or ""
    if not held.get("set_here") and came not in ("", scope, "launcher"):
        return panel.state(t(CAME_FROM.get(came, "manager.workbench.inherited")), "off")
    return None


Part = tuple[Callable[[], None], dict, Any]


def _varies(parts: Sequence[Part], tables: Sequence[dict[str, Any]], app_name: str,
            joiner: str = "", *, by_value: bool = False) -> Callable[[], None] | None:
    """Varies, where the tables a row is written to use different values for it, with
    each table's on hover: a pair's two parts joined by `joiner`. With `by_value` the
    hover counts the tables holding each value, which is what a long selection can be
    read from."""
    if not any(held.get("varies") for _, held, _ in parts):
        return None
    each = [held.get("each") or [] for _, held, _ in parts]
    said = [joiner.join(_said_value(field, used[index][1])
                        or t("manager.workbench.app_default", app=app_name)
                        for used, (_, _, field) in zip(each, parts, strict=True))
            for index in range(len(each[0]))]
    if by_value:
        lines = [t("manager.workbench.value_count", said=value, count=count)
                 for value, count in Counter(said).most_common()]
    else:
        lines = [t("manager.workbench.part_said", label=_table_line(table, list(tables)),
                   said=value) for (table, _), value in zip(each[0], said, strict=True)]
    return panel.state(t("manager.workbench.varies"), "off", hint="\n".join(lines))


def _marked(parts: Sequence[Part], app_name: str,
            redraws: list[Callable[[], None]] | None = None, *, joiner: str = "",
            on_leave: Callable[[], Any] | None = None,
            clear: Callable[[], Awaitable[None]] | None = None,
            playing: bool = False,
            again: Callable[[], Sequence[Part]] | None = None,
            regrow: dict[str, Callable[[], None]] | None = None,
            back: Callable[[Sequence[str]], str] | None = None) -> Callable[[], None]:
    """One value, or a pair with `joiner` between them, as `(control, held, field)`. A
    value at the default is drawn quiet and unmarked. Where this scope sets any of them
    to something else, a dot comes before it, whose each is on hover, and `clear` after
    the control. Each draw adds to `redraws` what brings the dot, the hover and Reset up to
    each `held` without drawing the controls again. `on_leave` runs when focus leaves the
    line. Where `again` hands back the parts as they are now, `regrow` gets, under the
    field's key, what draws this line's controls again.

    Reset only where there is something to reset: on an untouched row it would be a
    control that does nothing, and a column of inert verbs teaches people to stop reading
    them.
    """
    def draw() -> None:
        dot: Any = None
        hover_tip: Any = None
        whose: Any = None

        def controls() -> None:
            nonlocal dot, hover_tip, whose
            dot = ui.element("span").classes(
                "manager-mark manager-mark--full manager-named-mark")
            if len(parts) == 1:
                parts[0][0]()
            else:
                _pair(parts, joiner)
            hover_tip = getattr(parts[0][0], "hover_tip", None) if len(parts) == 1 else None
            whose = None
            if hover_tip is None:
                whose = ui.tooltip("")
                whose.props('anchor="bottom left" self="top left"')

        with ui.row().classes("items-center gap-1 no-wrap manager-field-line") as line:
            line.props["data-setting"] = parts[0][2].key
            with ui.row().classes("items-center gap-1 no-wrap manager-field-row") as row:
                controls()
            end = ui.element("div").classes("manager-row-action")
        if on_leave is not None:
            row.on("focusout", on_leave)

        def show() -> None:
            changed = [(held, field) for _, held, field in parts if _changed(held, field)]
            matching = [(held, field) for _, held, field in parts if _matches(held, field)]
            dot.set_visibility(bool(changed))
            off = any(not held.get("in_effect", True) for held, _ in changed)
            dot.classes(add="manager-named-mark--off" if off else None,
                        remove=None if off else "manager-named-mark--off")
            row.classes(add="manager-field-row--held" if changed else "manager-field-row--default",
                        remove="manager-field-row--default" if changed
                        else "manager-field-row--held")
            said = _each_said(
                [(field, _whose_value(held, field, app_name)) for _, held, field in parts],
                len(parts))
            if hover_tip is not None:
                hover_tip.say(said)
            elif whose is not None:
                whose.text = said
            end.clear()
            if clear is not None and (changed or matching):
                hint = _each_said([(field, _clear_hint(held, field, app_name))
                                   for held, field in changed or matching], len(parts))
                if back is not None and (note := back(
                        [one.key for _, held, one in parts if held.get("set_here")])):
                    hint = "\n".join((hint, note))
                with end:
                    panel.icon_action(t("word.reset"), clear, icon=verbs.RESET,
                                      enabled=not playing,
                                      hint=t(PLAYING_NOTE) if playing else hint)()

        def again_here() -> None:
            nonlocal parts
            if again is None:
                return
            parts = again()
            row.clear()
            with row:
                controls()
            show()

        show()
        if redraws is not None:
            redraws.append(show)
        if again is not None and regrow is not None:
            regrow[parts[0][2].key] = again_here
    return draw


def _pair(parts: Sequence[Part], joiner: str) -> None:
    """Each part's control, named by its label for a screen reader, with `joiner` after
    every part but the last: where the pair wraps, the second control starts the line at
    the first one's edge instead of being pushed in by a joiner."""
    with ui.element("div").classes("manager-field-pair"):
        for index, (control, _, field) in enumerate(parts):
            with ui.element("div").classes("manager-field-part") as named:
                control()
                if joiner and index < len(parts) - 1:
                    ui.label(joiner).classes("manager-cell-join").props("aria-hidden=true")
            named.props["role"] = "group"
            named.props["aria-label"] = field.label
            named.props["data-setting"] = field.key


def _each_said(said: Sequence[tuple[Any, str]], of: int) -> str:
    """What the parts of a row of `of` say: once where each of them says the same, else a
    line per part led by its name."""
    if len(said) == of and len({words for _, words in said}) == 1:
        return said[0][1]
    return "\n".join(t("manager.workbench.part_said", label=field.label, said=words)
                     for field, words in said)


def _whose_value(held: dict, field: Any, app_name: str) -> str:
    whose = t(CAME_FROM.get(held.get("scope") or "", "manager.workbench.inherited"))
    if held.get("set_here") and not held.get("in_effect", True):
        if said := _said_value(field, held.get("value") or ""):
            return t("manager.workbench.override_value", whose=whose, value=said)
        return t("manager.workbench.override", whose=whose)
    if held.get("set_here"):
        value, follows = _follows(held, field, app_name)
        if not _changed(held, field):
            return follows
        if value:
            return t("manager.workbench.changed_from", value=value, whose=follows)
        return t("manager.workbench.changed_link", whose=follows)
    if held.get("scope"):
        return whose
    if not held.get("value") and (said := getattr(field, "left_empty", "")):
        return str(said)
    return t("manager.workbench.app_default", app=app_name)


def _same_value(field: Any, one: str, other: str) -> bool:
    if _said_value(field, one) == _said_value(field, other):
        return True
    try:
        return float(one) == float(other)
    except ValueError:
        return False


def _said_value(field: Any, value: str) -> str:
    """A value as somebody reads it rather than as it is stored. A switch is On or Off,
    never 1 or 0, and an enumerated setting is its own label."""
    if value == "":
        return getattr(field, "blank", "")
    if getattr(field, "type", "") == "bool":
        return t("word.off") if value in ("0", "false",
                t("manager.workbench.false")) else t("word.on")
    for stored, label in getattr(field, "choices", ()) or ():
        if stored == value:
            return label
    return value


def _leaves(held: dict, field: Any) -> str:
    """The value that is in force once this scope stops naming one."""
    if held.get("fallback_scope"):
        return str(held.get("fallback") or "")
    return str(field.default or "")


def _follows(held: dict, field: Any, app_name: str) -> tuple[str, str]:
    """`_leaves` as somebody reads it, and whose it is."""
    where = held.get("fallback_scope") or ""
    whose = (t(CAME_FROM.get(where, "manager.workbench.inherited")) if where
             else t("manager.workbench.app_default", app=app_name))
    return _said_value(field, _leaves(held, field)), whose


def _scope_word(where: str) -> str:
    """Whose value a scope falls back to, as a tag: Default where the program picks it."""
    return {"launcher": t("manager.workbench.launcher"), "folder": t("word.game"),
            "entry": t("word.table")}.get(where, t("word.default"))


def _matches(held: dict, field: Any) -> bool:
    """This scope names a value, and it is the one it would follow anyway."""
    return bool(held.get("set_here")) and not held.get("varies") and not _changed(held, field)


def _same_mark(held: dict, field: Any, app_name: str) -> Callable[[], None] | None:
    """The quiet note on a value this scope holds that matches what it would follow, with
    whose that is on hover: it stays until it is reset."""
    if not _matches(held, field):
        return None
    where = held.get("fallback_scope") or ""
    word = t({"launcher": "manager.workbench.matches_launcher",
              "folder": "manager.workbench.matches_game"}.get(
                  where, "manager.workbench.matches_default"))
    return panel.state(word, "off", hint=_follows(held, field, app_name)[1])


def _default_tag(held: dict, field: Any, option: dict[str, Any]) -> dict[str, str]:
    """The choice this scope takes when it names none, with whose it is: Default where
    the program picks it, else the word for the scope that does."""
    if held.get("varies"):
        return {}
    leaves = _leaves(held, field)
    word = _scope_word(held.get("fallback_scope") or "")
    found = next((key for key in option.get("choices") or {}
                  if _same_value(field, key, leaves)), None)
    return {found: word} if found is not None and (leaves or not found) else {}


def _changed(held: dict, field: Any) -> bool:
    """A value this scope sets that is not the one it would follow without it. One that
    something narrower answers over counts: it is the exception most worth seeing."""
    if not held.get("set_here"):
        return False
    if held.get("varies") or not held.get("in_effect", True):
        return True
    return not _same_value(field, str(held.get("value") or ""), _leaves(held, field))


def _clear_hint(held: dict, field: Any, app_name: str) -> str:
    """What resetting it will leave in force, and whose value that is."""
    value, whose = _follows(held, field, app_name)
    if value and not _leaves(held, field) and getattr(field, "blank", ""):
        return t("manager.workbench.back_link_author")
    if value:
        return t("manager.workbench.back_to", value=value, whose=whose)
    return t("manager.workbench.back_link", whose=whose)


def _beside(mark_of: Callable[[], Callable[[], None] | None], held: dict, field: Any,
            redraws: list[Callable[[], None]] | None = None,
            more: Callable[..., Callable[[], None] | None] | None = None,
            beyond: Callable[[], bool | None] | None = None,
            paired: Sequence[tuple[dict, Any]] = (),
            ) -> Callable[[], None]:
    """The mark, whatever `more` offers for somebody's own value, then `beyond` whoever's
    value it is, which says False where it drew nothing. Drawn as a panel ASIDE, whose
    cell it hides while it holds none of them.
    `paired` is the rest of a pair's rows, `(held, field)` each, handed to `more` too.
    """
    def draw() -> None:
        cell = ui.context.slot.parent
        box = ui.row().classes("items-center gap-2 no-wrap")

        def fill() -> None:
            box.clear()
            mark = mark_of()
            own = any(one.get("set_here") for one in (held, *(said for said, _ in paired)))
            verb = more(held, field, *paired) if more is not None and own else None
            beyond_drawn = False
            with box:
                if mark is not None:
                    mark()
                if verb is not None:
                    verb()
                if beyond is not None:
                    beyond_drawn = beyond() is not False
            cell.set_visibility(mark is not None or verb is not None or beyond_drawn)

        fill()
        if redraws is not None:
            redraws.append(fill)
    return draw


# Said once over the group rather than on every row. The program rewrites both layers
# when a table exits and its own in-game menu writes the same keys, so an edit made now
# is one of two writers and the last one wins.
PLAYING_NOTE = "manager.workbench.playing_lock"


PLAYING_WHY = "manager.workbench.playing_lock.help"


# A number writes on every keystroke, so its redraw waits until it loses focus: drawn
# between two keys, the second lands nowhere.
TYPED = ("int", "number")


REPORTED = "reported"


def _playing(library: Library) -> bool:
    try:
        return bool((library.play_state() or {}).get("launching"))
    except Exception:  # noqa: BLE001 - not knowing is not a reason to refuse to draw
        return False


async def _config_rows(context: dict[str, Any], group: Any) -> None:
    """One area of the program's own settings, at the launcher scope: the rows the app
    curates for it under their headings, then the way to the rest."""
    if not group.curated:
        await _every_row(context, group)
        return
    at_table = context.get("config_scope") == SCOPE_ENTRY
    if at_table:
        group = _holdable([group], await _config_values(context), SCOPE_ENTRY, False)[0]
    if vpx_input.applies(group) and not at_table:
        with ui.column().classes("gap-0 manager-form"):
            panel.facts(ui, vpx_input.entries(context, group, await _config_values(context)))
        return
    _area_search(context, group, await _config_values(context))

    async def draw_rows() -> None:
        values = await _config_values(context)
        shown = curated_blocks(group, values)
        if vpx_displays.applies(group) and not at_table:
            entries = await vpx_displays.entries(
                context, group, shown, values,
                partial(_setting_entries, context, curated=True,
                        redraw_on=switches(group.curated) | {vpx_displays.VIEW_MODE},
                        refill=again),
                again)
        else:
            entries = await _setting_entries(
                context, [(heading.label or group.label, heading.note, fields)
                          for heading, fields in shown],
                curated=True, redraw_on=switches(group.curated), refill=again,
                pairs=[pair for heading, _ in shown for pair in heading.pairs])
        if rest := len(group.settings) - len(curated_keys(group)):
            entries.append((FULL, panel.action(
                t("manager.workbench.more_settings", count=rest),
                lambda: _open_all_settings(context, area=group.key), icon=verbs.DRILL,
                hint=panel.NO_HINT)))
        with ui.column().classes("gap-0 manager-form"):
            panel.facts(ui, entries)

    async def again() -> None:
        written = context.get("config_writes", 0)
        await area.refilled()
        if context.get("config_writes", 0) != written:
            await area.refilled()
        if (recount := context.get("recount_changed")) is not None:
            await recount()

    area = panel.region(draw_rows)
    area()
    await area.refilled()


async def _every_row(context: dict[str, Any], group: Any) -> None:
    """An area the app curates nothing for, whole."""
    # A heading per source section. Labels are the program's and are not unique - five
    # plugins each call their switch `Enable`, and two of the five say nothing else
    # about themselves. The section is the only thing that tells them apart, and it is
    # what the program groups them by too.
    blocks = [(_section_label(section, group.label), "", list(fields))
              for section, fields in groupby(group.settings, lambda f: _section_of(f.key))]
    with ui.column().classes("gap-0 manager-form"):
        panel.facts(ui, await _setting_entries(context, blocks))


def curated_blocks(group: Any, values: dict[str, Any]) -> list[tuple[Any, list[Any]]]:
    """Each curated heading with the rows it draws: all of them while its switch is on
    anywhere, the switch alone while it is off, and never a row its own switch hides."""
    by_key = {f.key: f for f in group.settings}

    def set_to(key: str, on: bool) -> bool:
        held = values.get(key) or {}
        return (key in by_key and not held.get("varies")
                and _is_on(by_key[key], held) == on)

    found = []
    for heading in group.curated:
        hidden = {key for one in getattr(heading, "switched", ())
                  if set_to(one.enabled_by, not getattr(one, "on", True)) for key in one.keys}
        fields = [by_key[key] for key in heading.keys if key in by_key and key not in hidden]
        if set_to(heading.enabled_by, False):
            fields = [by_key[heading.enabled_by]]
        if fields:
            found.append((heading, fields))
    return found


def switches(headings: Iterable[Any]) -> set[str]:
    """Every switch the headings draw rows by."""
    return {key for one in headings
            for key in (one.enabled_by, *(s.enabled_by for s in getattr(one, "switched", ())))
            if key}


def conflicts(groups: Sequence[Any], values: dict[str, Any]) -> dict[str, str]:
    """Each switch that is on while a rival of it is too, and that rival's name."""
    headings = [one for group in groups for one in getattr(group, "curated", ())
                if one.enabled_by]
    fields = {f.key: f for group in groups for f in group.settings}
    names = {one.enabled_by: one.label or apps.humanized(one.key) for one in headings}

    def on(key: str) -> bool:
        return key in fields and _is_on(fields[key], values.get(key) or {})

    return {one.enabled_by: names.get(rival, rival) for one in headings
            if on(one.enabled_by) for rival in getattr(one, "rivals", ()) if on(rival)}


def rival_switches(groups: Sequence[Any]) -> set[str]:
    return {key for group in groups for one in getattr(group, "curated", ())
            if getattr(one, "rivals", ()) for key in (one.enabled_by, *one.rivals)}


def curated_keys(group: Any) -> set[str]:
    held = {f.key for f in group.settings}
    return {key for heading in group.curated for key in heading.keys if key in held}


def _is_on(field: Any, held: dict[str, Any]) -> bool:
    value = str(held.get("value") or field.default or "").strip().lower()
    return value not in ("", "0", "false", "off", "no")


def _area_search(context: dict[str, Any], group: Any, values: dict[str, Any]) -> None:
    """Any setting, found from any area: what is typed here carries on in All Settings, and
    so does what differs from the default in this area."""
    app_name = str(context["launcher"].get("app_name") or "")
    with ui.row().classes("items-center gap-x-4 manager-section-bar"):
        search = panel.search(t("manager.app_settings.search_settings")) \
            .props("debounce=250")
        holder = ui.row().classes("items-center")

    def draw_changed(changed: int) -> None:
        holder.clear()
        with holder:
            panel.action(t("manager.workbench.changed_filter_count", count=changed),
                         lambda: _open_all_settings(context, area=group.key, differs=True),
                         icon=verbs.FILTER, enabled=bool(changed),
                         hint=t("manager.workbench.changed_filter.help" if changed
                                else "manager.workbench.changed_filter.none",
                                app=app_name))()

    async def recount() -> None:
        if not holder.is_deleted:
            draw_changed(changed_count([group], await _config_values(context)))

    draw_changed(changed_count([group], values))
    context["recount_changed"] = recount

    def find(typed: Any) -> None:
        if said := str(typed or "").strip():
            _open_all_settings(context, query=said, focus=True)

    search.on_value_change(lambda e: find(e.value))


def _open_all_settings(context: dict[str, Any], **wanted: Any) -> None:
    """All Settings on what was asked for, and nothing an earlier visit narrowed it to. A
    context that is not a page's own, such as a dialog, says in `show_all` how it shows it."""
    context["state"]["all_settings"] = wanted
    if (show := context.get("show_all")) is not None:
        show()
        return
    _choose(context, "launcher_all")


async def _config_values(context: dict[str, Any]) -> dict[str, Any]:
    """Every value as it stands at the context's scope - the launcher's unless it names
    a table, or as its `config_read` reads them - read once per draw."""
    values: dict[str, Any] = context.setdefault("config_values", {})
    if not values and (read := context.get("config_read")) is not None:
        values.update(await run.io_bound(read) or {})
    elif not values:
        values.update(await run.io_bound(
            context["library"].launcher_config_values,
            context["launcher"]["launcher_id"], str(context.get("config_table") or ""),
            str(context.get("config_scope") or "launcher")) or {})
    return values


def _mark_for(held: dict, scope: str, field: Any,
              offered: bool) -> Callable[[], None] | None:
    """`_config_mark`, or why this scope holds a value it does not offer: Per Table at the
    launcher, and Ignored at a table where the program never reads it."""
    if offered:
        return _config_mark(held, scope, field)
    if held.get("set_here") and scope == "launcher":
        return panel.state(t("manager.app_settings.per_table"), "off",
                           hint=t("manager.app_settings.per_table.help"))
    if not held.get("set_here") or held.get("in_effect"):
        return None
    return panel.state(t("manager.app_settings.ignored"), "warn",
                       hint=t("manager.app_settings.launcher_only"))


async def _setting_entries(context: dict[str, Any],
                           blocks: Sequence[tuple[str, str, Sequence[Any]]], *,
                           curated: bool = False, sub: bool = False, by_panel: bool = False,
                           redraw_on: Collection[str] = (),
                           pairs: Sequence[Any] = (),
                           ahead: Mapping[str, Sequence[tuple[Any, Any]]] | None = None,
                           after: Mapping[str, Sequence[tuple[Any, Any]]] | None = None,
                           refill: Callable[[], Coroutine[Any, Any, Any]] | None = None,
                           ) -> list[tuple[Any, Any]]:
    """Settings as fact rows, block by block: its heading where it has one - a
    sub-heading with `sub` - the line under that, then its rows. A curated row's line is
    the app's help where it has one; the program's description otherwise. Two rows of a
    block that `pairs` names are one row, under the pair's label and note. `ahead` maps a
    setting's key to entries drawn above its row, and `after` a block's heading to entries
    drawn under its rows. `by_panel` names every row as a table's panel does, away from an
    area's page.

    A write marks every row again in place, so a control keeps its focus. A named
    setting's row, and a rival switch's Conflict note, are drawn again in place too. The
    panel is drawn again instead - by `refill`, else the page's rebuild - where a setting
    in `redraw_on` was written, or where the write changed another setting's value or whose
    it is - drawn or not, since a table's first file of its own changes what it lists. For a
    number, that waits until it loses focus.

    The value shown is always the one the program will use, never what this scope
    happens to hold: you should not be looking at a number that is not in force.
    """
    library = context["library"]
    launcher = context["launcher"]
    values = await _config_values(context)
    scope = str(context.get("config_scope") or "launcher")
    table = str(context.get("config_table") or "")
    their_own = await _set_by_tables(context) if scope == "launcher" and not table else []
    if (more := context.get("config_more")) is not None:
        await more.ready(values)
    groups = context.get("config_groups") or []
    by_view = _row_names(groups, by_panel)
    clashing = conflicts(groups, values)
    rival = rival_switches(groups)
    named = {f.key for _, _, fields in blocks for f in fields if getattr(f, "reported", ())}
    redraw_on = set(redraw_on)

    def rebuild() -> Callable[[], Awaitable[Any]]:
        return refill or context["rebuild"]

    seen = await offload.io(screens.connected) if named else []
    rows: dict[str, dict] = {}
    redraws: list[Callable[[], None]] = []
    regrow: dict[str, Callable[[], None]] = {}
    row_of: dict[str, str] = {}
    shown = dict(values)
    pending = {"written": ""}
    reads = {"begun": 0, "shown": 0}
    in_turn = asyncio.Lock()

    async def settle() -> None:
        if written := pending["written"]:
            pending["written"] = ""
            await _keeping_place(ui.context.client, rebuild(), written)

    def written(values: dict[str, str]) -> Awaitable[Any]:
        if (write := context.get("config_write")) is not None:
            return config_applied(write, values)
        return run.io_bound(library.write_launcher_config, launcher["launcher_id"], values,
                            table=table, scope=scope)

    async def show_fresh(key: str, fresh: dict[str, Any], mine: int,
                         regrown: Callable[[], None] | None = None) -> None:
        """Bring the rows up to `fresh`, the read `mine` began, without drawing the page
        again. `regrown` draws the controls of the row that was written afresh."""
        saved = context.get("saved")
        if callable(saved) and _whose(shown, key) != _whose(fresh, key):
            asyncio.create_task(saved())
        if more is not None:
            await more.ready(fresh)
        if mine < reads["shown"]:
            return
        reads["shown"] = mine
        shown.update(fresh)
        clashing.clear()
        clashing.update(conflicts(groups, fresh))
        for other, held in rows.items():
            held.clear()
            held.update(fresh.get(other) or {})
        if regrown is not None:
            regrown()
        for redraw in redraws:
            redraw()
        for follow in list(context.get("followers", ())):
            follow(fresh)
        if (recount := context.get("recount_changed")) is not None:
            await recount()

    def clear(keys: Sequence[str]) -> Callable[[], Awaitable[None]]:
        @on_page
        async def wipe() -> None:
            try:
                async with in_turn:
                    wrote = await written({key: "" for key in keys
                                           if rows[key].get("set_here")})
            except Exception as exc:  # noqa: BLE001
                ui.notify(t("said.clear_failed"), caption=why(exc), type="negative")
                return
            if (done := context.get("config_done")) is not None:
                done(wrote or {})
            context.pop("config_values", None)
            context["config_writes"] = context.get("config_writes", 0) + 1
            reads["begun"] += 1
            mine = reads["begun"]
            try:
                fresh = await _config_values(context)
            except Exception:  # noqa: BLE001
                fresh = {}

            async def in_place() -> None:
                await show_fresh(keys[0], fresh, mine, regrow.get(keys[0]))

            moved = not fresh or redraw_on.intersection(keys) or _moved(shown, fresh, keys)
            await _keeping_place(ui.context.client, rebuild() if moved else in_place,
                                 keys[0], in_place=not moved)
        return wipe

    def save(key: str, typed: bool) -> Callable[[Any], Awaitable[bool]]:
        @on_page
        async def write(value: Any) -> bool:
            try:
                async with in_turn:
                    wrote = await written({key: _as_text(value)})
            except Exception as exc:  # noqa: BLE001
                ui.notify(t("said.save_failed"), caption=why(exc), type="negative")
                return False
            if cut := (wrote or {}).get("cut"):
                no_longer_reads_game(cut, context.get("tables"))
            if (done := context.get("config_done")) is not None:
                done(wrote or {})
            context.pop("config_values", None)
            context["config_writes"] = context.get("config_writes", 0) + 1
            reads["begun"] += 1
            mine = reads["begun"]
            try:
                fresh = await _config_values(context)
            except Exception:  # noqa: BLE001
                fresh = {}
            cleared = bool(table and fresh and key in ((wrote or {}).get("cleared") or ()))
            stuck = cleared and _whose(fresh, key) not in ("", "launcher")
            if cleared and not stuck:
                ui.notify(t("manager.app_settings.cleared"), type="positive")
            if fresh and typed and (stuck or key in redraw_on or _moved(shown, fresh, key)):
                pending["written"] = key
            elif not fresh or stuck or key in redraw_on or _moved(shown, fresh, key):
                asyncio.create_task(_keeping_place(ui.context.client, rebuild(), key))
                return True
            if key in named:
                # Its mark is drawn with its control, so the row's control is drawn again.
                async def regrown() -> None:
                    await show_fresh(key, fresh, mine, regrow.get(row_of.get(key, key)))
                await _keeping_place(ui.context.client, regrown, key, in_place=True)
                return True
            await show_fresh(key, fresh, mine)
            return True
        return write

    playing = bool(context.get("playing"))
    app_name = str(launcher.get("app_name") or "")

    def offered(field: Any) -> bool:
        return scope in (getattr(field, "scopes", ()) or (scope,))

    def part(field: Any) -> Part:
        held = rows.setdefault(field.key, dict(values.get(field.key) or {}))
        option = _as_option(field)
        if tags := _default_tag(held, field, option):
            option["tags"] = tags
        reported = getattr(field, "reported", ())
        return settings_page.control_for(
            option, settings_page.value_for(option, held.get("value")),
            save(field.key, option["type"] in TYPED), writable=not playing and offered(field),
            check=_unreported(held.get("value"), reported, app_name),
            suggestions={REPORTED: screens.reported(reported, seen)},
            varies=bool(held.get("varies")), browse=library.folders), held, field

    def marks(parts: Sequence[Part], joiner: str = "") -> Callable[[], None] | None:
        return _in_turn(_varies(parts, context.get("tables") or [], app_name, joiner,
                                by_value=bool(context.get("by_value"))),
                        *(None if held.get("varies")
                          else _mark_for(held, scope, field, offered(field))
                          or _same_mark(held, field, app_name)
                          for _, held, field in parts))

    entries: list[tuple[Any, Any]] = []
    if playing:
        entries.append(panel.note(t(PLAYING_NOTE), hint=t(PLAYING_WHY)))
    for title, lede, fields in blocks:
        if title and sub:
            entries.append((FULL, partial(_subheading, title)))
        elif title:
            entries.append((HEADING, title))
        if lede:
            entries.append(panel.lede(lede))
        in_block = {field.key: field for field in fields}
        rows_of = [(pair, [in_block[key] for key in pair.keys]) for pair in pairs
                   if all(key in in_block for key in pair.keys)]
        paired = {pair.keys[0]: (pair, both) for pair, both in rows_of}
        taken = {key for pair, _ in rows_of for key in pair.keys[1:]}
        for field in fields:
            if field.key in taken:
                continue
            entries.extend((ahead or {}).get(field.key, ()))
            pair, members = paired.get(field.key, (None, [field]))
            if (pair is None and table and not offered(field)
                    and not (values.get(field.key) or {}).get("set_here")):
                entries.append((by_view.get(field.key, field.label),
                                _launcher_page_link(launcher["launcher_id"])))
                if field.description:
                    entries.append(panel.note(field.description))
                continue
            parts =[part(one) for one in members]
            keys = [one.key for one in members]
            row_of.update({key: field.key for key in keys})
            owned = _tables_setting(their_own, keys)
            entries.append((pair.label if pair else by_view.get(field.key, field.label), _marked(
                parts, app_name, redraws, joiner=pair.joiner if pair else "",
                on_leave=settle if any(_as_option(one)["type"] in TYPED for one in members)
                else None, clear=clear(keys), playing=playing,
                again=partial(_again, part, members), regrow=regrow,
                back=partial(note, context) if (note := context.get("clear_note")) else None)))
            entries.append((panel.ASIDE, _beside(
                partial(marks, parts, pair.joiner if pair else ""), parts[0][1], field,
                redraws, more,
                _clash_note(clashing, keys, _tables_of_their_own(launcher, keys, owned)
                            if owned else None) if rival.intersection(keys)
                else _tables_of_their_own(launcher, keys, owned) if owned else None,
                paired=[(held, one) for _, held, one in parts[1:]])))
            if pair:
                said = pair.note
            else:
                said = (getattr(field, "help", "") if curated else "") or field.description
            if said:
                entries.append(panel.note(said))
        entries.extend((after or {}).get(title, ()))
    return entries


def _launcher_page_link(launcher_id: str) -> Callable[[], None]:
    """Where a setting a table cannot hold is set: the launcher's All Settings."""
    return panel.link(t("manager.app_settings.launcher_page_link"),
                      to="/manager?" + deeplink.query({
                          "view": "launchers", "launcher": launcher_id,
                          "section": "launcher_all"}))


def _again(part: Callable[[Any], Part], members: Sequence[Any]) -> list[Part]:
    return [part(one) for one in members]


def _whose(values: dict[str, Any], key: str) -> str:
    return str((values.get(key) or {}).get("scope") or "")


async def _keeping_place(client: Any, rebuild: Callable[[], Awaitable[Any]],
                         written: str, *, in_place: bool = False) -> None:
    """`rebuild`, then focus the control that held focus, else `written`'s first, else the
    row now where the one that held it was. Nothing is focused where focus was outside the
    panel. With `in_place` `rebuild` draws no new line, so focus is only put back on the
    setting that held it where the draw dropped it."""
    try:
        where = await client.run_javascript(_FOCUSED_SETTING)
    except Exception:  # noqa: BLE001 - a panel that cannot say where focus is still draws
        where = None
    await rebuild()
    if in_place:
        if where:
            client.run_javascript(_REFOCUS_IN_PLACE % json.dumps(where[0]))
    elif where is not None:
        places, row = ([where[0]], where[1]) if where else ([], -1)
        client.run_javascript(_REFOCUS_SETTING % json.dumps([[*places, [written, 0]], row]))


def no_longer_reads_game(cut: Sequence[dict[str, Any]],
                         tables: list[dict[str, Any]] | None) -> None:
    ui.notify(t("manager.app_settings.detached", count=len(cut),
                tables="\n".join(_table_line(one, tables) for one in cut)), type="warning")


async def _set_by_tables(context: dict[str, Any]) -> list[frozenset[str]]:
    """What each of the launcher's tables sets for itself, one set per table that sets
    anything."""
    launcher_id = context["launcher"]["launcher_id"]
    try:
        rows = await offload.io(context["library"].load_tables)
    except Exception:  # noqa: BLE001 - a row that cannot say how many still draws
        return []
    return [frozenset(keys) for row in rows if row.get("launcher") == launcher_id
            if (keys := row.get("launcher_settings_keys"))]


def _tables_setting(their_own: Sequence[frozenset[str]], keys: Collection[str]) -> int:
    """How many tables set any of `keys` for themselves."""
    return sum(1 for held in their_own if not held.isdisjoint(keys))


def _unreported(value: Any, reported: Sequence[str], app_name: str) -> dict[str, str] | None:
    """The mark on a name the program has not reported using. None where it has reported
    nothing, which says nothing either way."""
    said = str(value or "")
    if not reported or not said or said in reported:
        return None
    return {"state": "missing",
            "reason": t("manager.workbench.display_not_used", app=app_name)}


def _conflict(rival: str) -> Callable[[], None]:
    return panel.state(t("manager.workbench.conflict"), "warn",
                       hint=t("manager.workbench.conflict_hint", rival=rival))


def _clash_note(clashing: Mapping[str, str], keys: Sequence[str],
                after: Callable[[], None] | None) -> Callable[[], bool]:
    """The Conflict note of whichever of `keys` clashes as `clashing` stands when this is
    drawn, then `after`. False where it drew nothing."""
    def draw() -> bool:
        noted = [clashing[key] for key in keys if key in clashing]
        for rival in noted:
            _conflict(rival)()
        if after is not None:
            after()
        return bool(noted) or after is not None
    return draw


def _in_turn(*draws: Callable[[], None] | None) -> Callable[[], None] | None:
    kept = [one for one in draws if one is not None]
    if not kept:
        return None

    def draw() -> None:
        for one in kept:
            one()
    return draw


def _tables_of_their_own(launcher: dict[str, Any], keys: Sequence[str],
                         count: int) -> Callable[[], None]:
    """The link to the Tables grid on the tables that answer over any of these
    settings."""
    return panel.link(t("manager.workbench.override_count", count=count),
                      to="/manager?" + deeplink.query({
                          "view": "tables", "launcher": launcher["launcher_id"],
                          "sets": ",".join(keys)}))


def _moved(before: dict[str, Any], after: dict[str, Any], written: str | Collection[str]) -> bool:
    """Whether a setting other than those written has a different value now, or has it
    from another scope."""
    own = {written} if isinstance(written, str) else set(written)

    def said(held: Any) -> tuple[Any, Any]:
        return (held or {}).get("value"), (held or {}).get("scope")
    return any(said(before.get(key)) != said(after.get(key))
               for key in before.keys() | after.keys() if key not in own)


def _subheading(title: str) -> None:
    ui.label(title).classes("manager-group")


def _section_of(qualified: str) -> str:
    """The part before the last dot. A section name has dots of its own -
    `Plugin.B2S.Enable` is `Enable` in `Plugin.B2S`."""
    return qualified.rsplit(".", 1)[0] if "." in qualified else ""


def _section_label(section: str, group_label: str) -> str:
    """What to call a source section on screen. Never its raw name: `[Plugin.B2S]` is
    the file's spelling, and what a reader wants is which of the two backglass plugins
    this is."""
    said = section.split(".", 1)[-1] if section.startswith("Plugin.") else section
    # Left as the program spells it. A section here is somebody's product name, and the
    # humanizer this file uses is built for snake_case fields - it lowercases the
    # capitals inside a name and gives back something worse than it was handed.
    said = said.replace("\\", " - ")
    return group_label if not said or said.lower() == group_label.lower() else said


def setting_names(groups: Sequence[Any]) -> dict[str, str]:
    """What to call each of one program's settings away from its area, by key: its
    label, led by its plugin's or its window's name, or by the heading or section it
    sits under where another setting shares the label. A setting in a pair whose name
    another shares is led by the pair's name instead."""
    from manager.workbench import app_settings

    fields = [(group, field) for group in groups for field in group.settings]
    shared = Counter(field.label for _group, field in fields)
    plugins = _plugin_names(groups)
    pairs = {key: str(pair.label) for pair in app_settings.named_pairs(groups)
             for key in pair.keys}
    labels = {field.key: field.label for _group, field in fields}

    def named(group: Any, field: Any) -> str:
        section = _section_of(field.key)
        if section.startswith(PLUGIN_SECTION):
            return plugin_row(section, field.label, plugins)
        if window := app_settings._window_of(field.key, group):
            return app_settings.window_row(window, field.label)
        if shared[field.label] < 2:
            return field.label
        heading = next((one.label for one in getattr(group, "curated", ())
                        if field.key in one.keys and one.label), "")
        return t("manager.app_settings.heading_row",
                 heading=heading or _section_label(section, group.label), label=field.label)

    names = {field.key: named(group, field) for group, field in fields}
    read = Counter(name.casefold() for name in names.values())
    return {key: t("manager.app_settings.heading_row", heading=pairs[key], label=labels[key])
            if key in pairs and read[name.casefold()] > 1 else name
            for key, name in names.items()}


def _row_names(groups: Sequence[Any], by_panel: bool) -> dict[str, str]:
    """What each row is called, by key, where its heading is not enough to tell it apart:
    away from its area page every row is named as a table's panel names it; on one only
    the summarized group's rows are, since its three view modes share one label."""
    from manager.workbench import app_settings

    if by_panel:
        return setting_names(groups)
    return {field.key: str(app_settings._named(field, group, {}).label)
            for group in groups if getattr(group, "summarized", False)
            for field in group.settings}


def plugin_row(section: str, label: str, plugin_names: dict[str, str]) -> str:
    """A plugin's setting away from its heading, led by the plugin's name: it is all
    that tells one plugin's Enable from the next."""
    return t("manager.app_settings.plugin_row",
             plugin=plugin_name(section[len(PLUGIN_SECTION):], plugin_names), label=label)


# --- All Settings -------------------------------------------------------------------

PLUGIN_SECTION = "Plugin."


def _all_settings_shown(context: dict[str, Any]) -> bool:
    return _program_is_there(context) and any(g.settings for g in _listed_groups(context))


def _listed_groups(context: dict[str, Any]) -> list[Any]:
    """The groups whose settings are rows. At the launcher a summarized or read-only group
    is a table's alone, and the input is its own page. At a table the input is rows, and a
    summarized group lists only the rows it draws: the camera and Table Options are lines
    of their own."""
    at_table = context.get("config_scope") == SCOPE_ENTRY
    found = []
    for group in context.get("config_groups") or []:
        if getattr(group, "read_only", False):
            continue
        if getattr(group, "summarized", False):
            if not at_table:
                continue
            drawn = set(getattr(group, "rows", ()))
            group = SimpleNamespace(**{
                **vars(group), "summarized": False,
                "settings": [f for f in group.settings if f.key in drawn]})
            if not group.settings:
                continue
        elif not at_table and vpx_input.applies(group):
            continue
        found.append(group)
    return found


def _holdable(groups: Sequence[Any], values: dict[str, Any], scope: str,
              searching: bool) -> list[Any]:
    """At a table, the settings it can hold and those its file does: one it cannot hold
    is listed only to a search, so it can be pointed to where it is set."""
    if scope != SCOPE_ENTRY or searching:
        return list(groups)

    def held_here(field: Any) -> bool:
        return (scope in (getattr(field, "scopes", ()) or (scope,))
                or bool((values.get(field.key) or {}).get("set_here")))

    return [SimpleNamespace(**{**vars(group), "settings": [f for f in group.settings
                                                          if held_here(f)]})
            for group in groups]


async def _all_settings(context: dict[str, Any]) -> None:
    """Every setting the program keeps, in its own words, found by typing or narrowed
    to what differs from its default, or one area."""
    wanted: dict[str, Any] = context["state"].setdefault("all_settings", {})
    groups = _listed_groups(context)
    scope = str(context.get("config_scope") or "launcher")
    areas = {"": t("manager.workbench.every_area"), **{g.key: g.label for g in groups}}
    if wanted.get("area") not in areas:
        wanted["area"] = ""

    app_name = str(context["launcher"].get("app_name") or "")
    first = await _config_values(context)
    changed = changed_count(_holdable(groups, first, scope, False), first)
    if above_for := context.get("above_for"):
        if above := await above_for(context):
            with ui.column().classes("gap-0 manager-form"):
                panel.facts(ui, above)
    with ui.row().classes("items-center gap-x-4 gap-y-2 manager-section-bar"):
        search = panel.search(t("manager.app_settings.search_settings")) \
            .props("debounce=250")
        search.value = wanted.get("query") or ""
        if wanted.pop("focus", False):
            search.props("autofocus")
        area = dropdown.Select(areas, value=wanted["area"], look=dropdown.OUTLINED) \
            .classes("w-40")
        differs = ui.checkbox(t("manager.workbench.changed_filter_count", count=changed),
                              value=bool(wanted.get("differs"))) \
            .props("dense").classes("whitespace-nowrap")
        differs.tooltip(t("manager.workbench.changed_filter.help", app=app_name))
    results = ui.column().classes(
        "gap-0 w-full" + (" grow min-h-0 no-wrap overflow-auto" if context.get("scrolls") else ""))

    async def recount() -> None:
        if not differs.is_deleted:
            values = await _config_values(context)
            differs.set_text(t("manager.workbench.changed_filter_count",
                               count=changed_count(_holdable(groups, values, scope, False),
                                                   values)))

    context["recount_changed"] = recount

    async def draw() -> None:
        from manager.workbench import app_settings

        values = await _config_values(context)
        found = found_settings(_holdable(groups, values, scope, bool(wanted.get("query"))),
                               values, wanted)
        lines: dict[str, list[tuple[Any, Any]]] = {}
        if (held := context.get("lines_for")) is not None and not wanted.get("query"):
            lines = await held(values, wanted.get("area") or "")
        entries = await _setting_entries(
            context, [(title, "", fields) for title, fields in found],
            pairs=app_settings.named_pairs(groups), after=lines, by_panel=True)
        drawn = {title for title, _fields in found}
        extra = [one for title, said in lines.items() if title not in drawn
                 for one in ((HEADING, title), *said)]
        results.clear()
        with results, ui.column().classes("gap-0 manager-form"):
            panel.facts(ui, [*entries, *extra] if found or extra else [panel.intro(
                t("manager.workbench.settings_empty"))])

    async def narrow(key: str, value: Any) -> None:
        wanted[key] = value or ("" if key in ("query", "area") else False)
        await draw()

    search.on_value_change(lambda e: narrow("query", e.value))
    area.on_value_change(lambda e: narrow("area", e.value))
    differs.on_value_change(lambda e: narrow("differs", e.value))
    await draw()


def found_settings(groups: Sequence[Any], values: dict[str, Any],
                   wanted: dict[str, Any]) -> list[tuple[str, list[Any]]]:
    """What All Settings lists, each setting under its area, the areas in the order they
    first reach them: the headings and row names of a table's panel, at the launcher
    and at a table alike."""
    terms = str(wanted.get("query") or "").lower().split()
    called = setting_names(groups)

    def wanted_here(field: Any) -> bool:
        held = values.get(field.key) or {}
        return not ((wanted.get("differs") and not _changed(held, field))
                    or (terms and not _says_all(field, terms, called)))

    listed = [(group, field) for group in groups
              if not wanted.get("area") or group.key == wanted["area"]
              for field in group.settings]
    kept = {field.key for _group, field in listed if wanted_here(field)}
    kept.update(key for pair in curated_pairs(groups) if not kept.isdisjoint(pair.keys)
                for key in pair.keys)
    found: dict[str, list[Any]] = {}
    for group, field in listed:
        if field.key in kept:
            found.setdefault(str(group.label), []).append(field)
    return list(found.items())


def curated_pairs(groups: Sequence[Any]) -> list[Any]:
    return [pair for group in groups for heading in getattr(group, "curated", ())
            for pair in getattr(heading, "pairs", ())]


def _says_all(field: Any, terms: Sequence[str], called: Mapping[str, str]) -> bool:
    said = " ".join((called.get(field.key, ""), field.label, field.key, field.description,
                     getattr(field, "help", ""))).lower()
    return all(term in said for term in terms)


def changed_count(groups: Sequence[Any], values: dict[str, Any], area: str = "") -> int:
    """How many rows the Changed filter would list: a pair is one row."""
    keys = {field.key for group in groups if not area or group.key == area
            for field in group.settings
            if _changed(values.get(field.key) or {}, field)}
    rows = len(keys)
    for pair in curated_pairs(groups):
        if taken := keys.intersection(pair.keys):
            rows -= len(taken) - 1
    return rows


def _plugin_names(groups: Sequence[Any]) -> dict[str, str]:
    """A plugin's name where an area heads its rows with one, by plugin id."""
    return {h.key: h.label for g in groups for h in getattr(g, "curated", ())
            if h.keys and h.keys[0].startswith(f"{PLUGIN_SECTION}{h.key}.")}


def plugin_name(plugin: str, plugin_names: dict[str, str]) -> str:
    """A plugin's name: the one its area heads its rows with, else its id in words."""
    return plugin_names.get(plugin) or apps.humanized(plugin)


def section_title(section: str, plugin_names: dict[str, str]) -> str:
    """A source section as All Settings heads it: the program's name for it, in words."""
    if section.startswith(PLUGIN_SECTION):
        return t("manager.workbench.plugin_section",
                 name=plugin_name(section[len(PLUGIN_SECTION):], plugin_names))
    return apps.humanized(section).replace("\\", " - ")


def _as_text(value: Any) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    return "" if value is None else str(value)


def _as_option(field: Any) -> dict[str, Any]:
    """A declared field as the control grammar reads it. `choices` is a mapping so the
    label goes on screen where the stored value would otherwise be."""
    option: dict[str, Any] = {"key": field.key, "type": field.type,
                              "label": field.label, "default": field.default,
                              "blank": getattr(field, "blank", "")}
    low, high = getattr(field, "minimum", None), getattr(field, "maximum", None)
    if low is not None:
        option["min"] = low
    if high is not None:
        option["max"] = high
    if named := getattr(field, "named", ()):
        option["named"] = {value: label for value, label in named}
    if path := getattr(field, "path", ""):
        option.update(path=path, suffixes=tuple(getattr(field, "suffixes", ())))
    if getattr(field, "reported", ()):
        option.update(suggest=REPORTED, clearable=False)
    if field.choices:
        blank = option["blank"]
        option["choices"] = {**({"": blank} if blank else {}),
                             **{value: label for value, label in field.choices}}
        option["type"] = "choice"
        said = getattr(field, "choice_help", None) or {}
        option["describes"] = {label: said[value] for value, label in field.choices
                               if said.get(value)}
    return option


@on_page
async def _agreed_to_switch_off(library: Any, launcher: dict[str, Any]) -> bool:
    try:
        found = await offload.io(library.launcher_fallback, launcher["launcher_id"])
    except Exception as exc:  # noqa: BLE001
        ui.notify(t("manager.workbench.lookup_failed"), caption=why(exc), type="negative")
        return False
    if found.get("refused"):
        ui.notify(found["refused"], type="warning")
        return False
    count = int(found.get("tables") or 0)
    if not count:
        return True
    goes = list(found.get("fallbacks") or [])
    lines: list[str] = []
    if len(goes) == 1:
        detail = t("manager.workbench.off_detail", count=count,
                   fallback=goes[0]["display_name"])
    else:
        detail = t("manager.workbench.switched_off_split", count=count)
        lines = [t("manager.workbench.launch_count", count=int(one["tables"]),
                   name=one["display_name"]) for one in goes]
    return await confirm.ask(
        t("manager.workbench.off_question",
          name=launcher.get("display_name") or launcher["app_name"]),
        detail=detail, lines=lines, confirm=t("manager.workbench.switch_off"),
        icon=verbs.SWITCH_OFF, danger=False)


# Every launcher's own fields follow its app's, and they are the commands.
COMMANDS_FROM = OWN_FIELDS[0].key


def _switched_off_goes_to(launcher: dict[str, Any], held: list[dict[str, Any]]) -> str:
    """The launcher its tables would use with this one off, or empty where none would."""
    found = next((one for one in held
                  if one.get("app") == launcher.get("app") and one.get("enabled")
                  and one.get("launcher_id") != launcher.get("launcher_id")), None)
    return str(found.get("display_name") or found.get("app_name") or "") if found else ""


def _why_not_the_default(launcher: dict[str, Any]) -> str:
    """Why the Default switch cannot make it the default, or "" where it can."""
    state = launchers_page.state_of(launcher)
    if state == launchers_page.STATE_NO_PROGRAM:
        return t("manager.workbench.default_needs_program", name=launcher["display_name"])
    return "" if launcher.get("enabled") else t("manager.workbench.default_needs_on")
