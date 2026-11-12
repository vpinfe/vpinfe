"""The collection subject: its details, rules, order and the games it holds."""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
from typing import Any, NamedTuple

from nicegui import run, ui

from common import icons
from common.config import config_schema
from common.failures import why
from common.games.collection_filters import group_kind
from common.games.collection_store import (
    DEFAULT_DIRECTION,
    DEFAULT_ORDER_BY,
    DIRECTION_WORDS,
    MANUAL_ORDER,
    NATURAL_DIRECTION,
    SORT_LABELS,
)
from common.games.game_metadata import star_words
from common.i18n import t
from manager.extensions import ext_lists
from manager.kit import arrange as arranging
from manager.kit import (
    art,
    deeplink,
    dropdown,
    game_tables,
    offload,
    panel,
    tag_chips,
    thumbnails,
    verbs,
)
from manager.kit import dialog as frame
from manager.kit.data import read_state, tag_source
from manager.kit.on_page import on_page
from manager.library import collection_adds, collection_rules, mediasource, row_drag, text_editor
from manager.workbench.core import _ADD_BOX, _KEEP_SCROLL, FULL, HEADING, _built_label, _rows


async def _all_games_details(context: dict[str, Any]) -> None:
    entries: list[tuple[Any, Any]] = [
        (HEADING, t("manager.workbench.frontend")),
        (t("manager.workbench.open_on"), _open_on(context, {"name": ""}, all_games=True)),
        panel.note(t("manager.workbench.open_on.help")),
        panel.note(t("manager.workbench.all_games.paging")),
    ]
    with ui.column().classes("gap-0 manager-form"):
        _rows(ui, entries)


def _note_unsaved(state: dict[str, Any], names: set[str]) -> None:
    """`names` as the page's `unsaved_rules`, the grid's marks and the browser's flag."""
    state["unsaved_rules"] = set(names)
    mark = state.get("mark_unsaved")
    if callable(mark):
        mark(set(names))
    text_editor.flag(state)


# --- collections ------------------------------------------------------------------
# A collection's rail. Nothing here is shared with a game's: the two subjects have no
# section in common, because the rail is a function of (nav node, subject) rather than
# one list everything appears in.
#
def _collection(context: dict[str, Any]) -> dict[str, Any]:
    return context["collection"]


def _is_dynamic(row: dict[str, Any]) -> bool:
    """Whether this collection changes under its owner.

    Derived, never stored: it carries criteria or it does not. Named for what it does
    to the user rather than for how it was built, which is what makes the arrangement
    question answerable - only a static list has a membership stable enough to arrange.
    """
    return (row.get("type") or "") == "filter"


def _rules_drafted(context: dict[str, Any]) -> bool:
    """Whether an unsaved draft asks for anything."""
    return bool(context.get("unsaved") and collection_rules.filters_from(
        context["draft"].get("rules") or [], context["fields"]))


async def _collection_details(context: dict[str, Any]) -> None:
    row = _collection(context)
    entries: list[tuple[Any, Any]] = [
        (HEADING, t("manager.workbench.this_collection")),
        (t("word.name"), _text_control(context, row, "name")),
        (t("manager.workbench.description"), _text_control(context, row, "description",
                lines=3)),
        (HEADING, t("manager.workbench.frontend")),
        (t("manager.workbench.in_menu"), _in_frontend_switch(context, row)),
        (t("manager.workbench.open_on"), _open_on(context, row)),
        panel.note(t("manager.workbench.open_on.help")),
        (t("manager.workbench.page_by"), _page_by(context, row)),
        panel.note(t("manager.workbench.page_by.help")),
        (FULL, partial(_image_slot, context, row)),
    ]
    with ui.column().classes("gap-0 manager-form"):
        _rows(ui, entries)


def _text_control(context: dict[str, Any], row: dict[str, Any], field: str,
                  lines: int = 0) -> Callable[[], None]:
    """One editable field of a collection, and when it is written.

    Two rhythms, because the two fields cost different amounts to change. Free text
    settles as you stop typing. A name is this collection's identity - every route is
    keyed by it - so it is written when you leave the field, and `debounce=0` is what
    makes that safe: nicegui's model is only current if every keystroke reaches it, and
    reading it on blur without that gets whatever the last sync happened to hold.
    """
    @on_page
    async def save(value: str) -> None:
        if value == (row.get(field) or ""):
            return
        if field == "name" and not value.strip():
            ui.notify(t("said.needs_name"), type="warning")
            return
        if field == "name":
            await _rename(context, value)
            return
        await _save(context, {field: value})

    def draw() -> None:
        control: ui.textarea | ui.input
        if lines:
            control = ui.textarea()
            control.value = row.get(field) or ""
            control.props(f"dense outlined rows={lines} debounce=800") \
                .classes("w-full min-w-0")
            control.on_value_change(lambda: save(control.value or ""))
            return
        control = ui.input()
        control.value = row.get(field) or ""
        control.props("dense outlined debounce=0").classes("w-full min-w-0")
        control.on("blur", lambda: save(control.value or ""))

    return draw


@on_page
async def _rename(context: dict[str, Any], wanted: str) -> None:
    # Imported here: library.collections imports library.games, which imports this module.
    from manager.library.collections import renamed_to

    state = context["state"]
    try:
        new = await renamed_to(context["library"], state, _collection(context)["name"], wanted)
    except Exception as exc:
        ui.notify(t("manager.workbench.save_failed"), caption=why(exc), type="negative")
        return
    state["collection"] = new
    reread = state.get("refresh_collections")
    if callable(reread):
        await reread(new)
        return
    deeplink.sync(state)
    await context["rebuild"]()


def _in_frontend_switch(context: dict[str, Any], row: dict[str, Any]) -> Callable[[], None]:
    def shown() -> bool:
        return row.get("in_frontend") is not False

    async def changed(event: Any) -> None:
        if bool(event.value) != shown():
            await _save(context, {"in_frontend": bool(event.value)})

    return panel.switch(shown(), changed)


def _open_now(context: dict[str, Any]) -> str:
    behavior = (context.get("settings") or {}).get("behavior") or {}
    return str(behavior.get("startup_collection") or "").strip()


@on_page
async def _write_open_on(context: dict[str, Any], value: str) -> None:
    try:
        await run.io_bound(context["library"].put_config, {"behavior": {
            "startup_collection": value}})
    except Exception as exc:
        ui.notify(t("manager.workbench.save_failed"), caption=why(exc), type="negative")
        return
    settings = context.get("settings")
    if not isinstance(settings, dict):
        settings = context["settings"] = {}
    settings.setdefault("behavior", {})["startup_collection"] = value
    await _settled(context)
    _refill(context, "open_on")


def _open_on(context: dict[str, Any], row: dict[str, Any], *,
             all_games: bool = False) -> Callable[[], None]:
    """What the frontend opens on, and how it moves. A collection offers the one act: to
    be it, or back to all games. The value is the setting Settings shows, written the
    same way. `all_games` is the row for the whole library, which is where the choices
    that are not a collection are made."""
    name = str(row.get("name") or "")
    choices = panel.open_on_choices()

    if all_games:
        now = _open_now(context)
        # What is set stays on the list while it is a collection, so the control never
        # shows a value it cannot name.
        options = choices if not now or now in choices else {**choices, now: now}

        @on_page
        async def chosen(event: Any) -> None:
            if str(event.value or "") != _open_now(context):
                await _write_open_on(context, str(event.value or ""))

        return panel.select(options, now, chosen)

    @on_page
    async def use() -> None:
        await _write_open_on(context, "" if _open_now(context) == name else name)

    def draw() -> None:
        now = _open_now(context)
        here = bool(name) and now == name
        with ui.row().classes("items-center gap-2 no-wrap min-w-0"):
            ui.label(choices.get(now) or now) \
                .classes("manager-fact-value truncate min-w-0")
            panel.action(
                t("manager.workbench.open_on.use_all" if here
                  else "manager.workbench.open_on.use_this"), use, inline=True,
                icon=verbs.OPENS_ON,
                hint=panel.NO_HINT if here else t("manager.collections.opens_on.help"))()

    return _region(context, "open_on", draw)


_PAGE_BY_GROUP = frozenset({"letter", "year", "rating"})


def _page_by(context: dict[str, Any], row: dict[str, Any]) -> Callable[[], None]:
    """Every choice is listed; the group one is greyed, with the order's name, where that
    order has no groups, and a stored group still reads as stored."""
    by, _, orders = _shown_order(context, row)
    choices = config_schema.option("behavior", "paging_group")
    said = ((context.get("settings") or {}).get("behavior") or {}).get("paging_group")
    current = str(said or (choices.default if choices else "") or "count")
    word = {key: t(f"config.behavior.paging_group.choice.{key}") for key in ("sort", "count")}
    options = {"": t("manager.workbench.page_by.same", choice=word.get(current, "")),
               "sort": word["sort"], "count": word["count"]}
    unavailable = {} if group_kind(by) in _PAGE_BY_GROUP else {
        "sort": t("manager.workbench.page_by.no_groups", order=orders[by])}

    def shown() -> str:
        stored = row.get("paging_group") or ""
        return stored if stored in options else ""

    async def changed(event: Any) -> None:
        if event.value != shown():
            await _save(context, {"paging_group": event.value})

    return panel.select(options, shown(), changed, unavailable=unavailable)


def _image_slot(context: dict[str, Any], row: dict[str, Any]) -> None:
    """The collection's wheel, in the shape a media slot takes.

    Same card, same art region, same blank state, same row of actions - a picture in
    this app is presented one way, and a bespoke drop target here was a second.
    """
    name = row.get("name") or ""
    library = context["library"]
    present = bool(row.get("image"))
    stand_ins = [] if present else list(row.get("game_wheels") or [])
    label = t("manager.workbench.wheel")

    async def clear() -> None:
        await run.io_bound(library.clear_collection_image, name)
        await _written(context)

    with ui.column().classes("w-full gap-1 manager-slot p-2 mt-3"):
        ui.label(label).classes("manager-card-title")
        with ui.element("div").classes("manager-slot-art manager-slot-art--wheel"):
            if present:
                ui.image(art.collection(name, version=row.get("image_version"),
                                        size=art.PANEL)) \
                    .classes("manager-slot-image")
            elif stand_ins:
                with ui.element("div").classes("manager-wheel-mosaic"):
                    for src in stand_ins:
                        ui.element("img").props(
                            f'src="{art.sized(src, art.CELL)}" loading="lazy"')
            else:
                with ui.column().classes("manager-slot-blank items-center gap-1"):
                    ui.icon("image").classes("manager-slot-blank-icon")
        if stand_ins:
            ui.label(t("manager.workbench.slot_origin")) \
                .classes("text-xs manager-label self-center")
        with ui.row().classes("items-center gap-2 w-full manager-slot-actions"):
            ui.button(t("word.replace") if present else t("word.add"),
                      icon=verbs.REPLACE if present else verbs.ADD,
                      on_click=lambda: mediasource.open_image_sources(
                          library, name, label, lambda: _written(context))) \
                .props("flat dense no-caps size=sm").classes("manager-action")
            if present:
                ui.button(t("word.remove"), icon=verbs.REMOVE, on_click=clear) \
                    .props("flat dense no-caps size=sm") \
                    .classes("manager-action manager-action--danger")


def _region(context: dict[str, Any], key: str, build: Callable[[], Any]) -> panel.Region:
    """A part of the panel that reads the collection, found again by `key`. Drawn again,
    it takes its key back from the one it replaces."""
    made = panel.region(build)
    context.setdefault("regions", {})[key] = made
    return made


def _refill(context: dict[str, Any], *keys: str) -> None:
    """The parts of the panel named `keys` that are on screen, drawn again where they stand."""
    for key in keys:
        found = context.get("regions", {}).get(key)
        if found is not None:
            found.refill()


async def _settled(context: dict[str, Any]) -> None:
    """After a write that leaves the panel as it is: the panel's copy of the collection
    read again, and the grid's row put right."""
    reload = context.get("reload")
    if callable(reload):
        await reload()
    settle = context["state"].get("settle_collections")
    if callable(settle):
        await settle()


@on_page
async def _save(context: dict[str, Any], changes: dict[str, Any],
                *refills: str) -> bool:
    """Write `changes` to this collection and settle the panel, refilling the parts that
    read them. False where the write failed."""
    try:
        await run.io_bound(context["library"].patch_collection,
                           _collection(context)["name"], changes)
    except Exception as exc:
        ui.notify(t("manager.workbench.save_failed"), caption=why(exc), type="negative")
        return False
    await _settled(context)
    _refill(context, *refills)
    return True


async def _written(context: dict[str, Any], name: str = "") -> None:
    """The grid read again and the panel redrawn. Refocusing the row is what redraws the
    panel, so nothing here rebuilds it as well."""
    reread = context["state"].get("refresh_collections")
    if callable(reread):
        await reread(name or _collection(context)["name"])
        return
    await context["rebuild"]()


async def _membership_changed(context: dict[str, Any], name: str) -> None:
    """The members read again where the panel still shows `name`, and the grid's counts
    either way. Run again by an Undo, when the panel it was started from may have been
    drawn again: it is the panel on screen that is read."""
    state = context["state"]
    if state.get("collection") == name and state.get("view") == "collections":
        shown = state.get("collection_panel") or context
        await _settled(shown)
        _read_drawn(shown)
        return
    reread = state.get("refresh_collections")
    if callable(reread) and state.get("view") == "collections":
        await reread()


def _games_label(context: dict[str, Any]) -> str:
    got = (context.get("membership") or {}).get("playable")
    return t("manager.workbench.games") if got is None \
        else t("manager.workbench.games_counted", count=got)


def _forked(context: dict[str, Any]) -> bool:
    """Whether the section offers the choice between rules and a hand-picked list, which
    is all an empty list with no rules has to show."""
    held = (context.get("membership") or {}).get("members") or []
    return not held and not _is_dynamic(_collection(context)) and not _drafting(context)


async def _collection_games(context: dict[str, Any]) -> None:
    _region(context, "games", partial(_games_section, context))()


def _shape(context: dict[str, Any]) -> tuple[bool, bool, bool]:
    """What the section lays out: the fork or the list, the stored rules or a draft, and
    whether the list is long enough to be searched."""
    return _forked(context), bool(context.get("unsaved")), _searchable(context)


def _searchable(context: dict[str, Any]) -> bool:
    lens = context.get("membership") or {}
    return not lens.get("error") and len(lens.get("members") or []) > 8


def _games_section(context: dict[str, Any]) -> None:
    context["shape"] = _shape(context)
    row = _collection(context)
    if _forked(context):
        _empty_fork(context)
        return
    held = (context.get("membership") or {}).get("members") or []

    def controls() -> None:
        _order_bar(context, row)
        _add_control(context, held)

    with ui.column().classes("gap-0 w-full min-w-0"):
        _region(context, "rules", partial(_rules_block, context, row))()
        _games_list(context, row, controls)
    _region(context, "draft_bar", partial(_draft_bar, context))()


def _empty_fork(context: dict[str, Any]) -> None:
    with ui.column().classes("gap-0 manager-form"):
        panel.facts(ui, [(HEADING, _games_label(context))])
    with ui.column().classes("gap-2 w-full min-w-0 py-2"):
        ui.label(t("manager.workbench.empty")).classes("manager-empty-title")
        _add_control(context, [])
        with ui.row().classes("items-center gap-2 no-wrap"):
            ui.label(t("word.or")).classes("manager-help")
            _add_rule_button(context)
            ui.label(t("manager.workbench.rule_hint")).classes("manager-help")
        library = context["library"]
        lists = [(tag_source(one), tag) for tag, look in library.tag_looks().items()
                 for one in look.get("sources") or []]
        offered = [*collection_rules.templates(context["fields"]),
                   *collection_rules.tagged(lists, context["fields"])]
        if offered:
            with ui.row().classes("items-baseline gap-2 no-wrap"):
                ui.label(t("manager.workbench.start_from")).classes("manager-help shrink-0")
                with ui.row().classes("items-center gap-2 min-w-0"):
                    for one in offered:
                        panel.action(one.reads(),
                                     lambda _e=None, one=one: _start_from(context, one),
                                     icon=verbs.ADD, inline=True,
                                     hint=t("manager.workbench.start_from.help"))()


async def _start_from(context: dict[str, Any], template: collection_rules.Template) -> None:
    context["draft"]["rules"] = [dict(row) for row in template.rows]
    context["draft"]["order"] = dict(template.order)
    await _drafted(context, structural=True)


# --- the rules ------------------------------------------------------------------


def _drafting(context: dict[str, Any]) -> bool:
    return "rules" in context["draft"]


def _stored_rules(context: dict[str, Any]) -> list[dict[str, Any]]:
    return collection_rules.rows_from(_collection(context).get("filters"),
                                      context["fields"])


def _rules_now(context: dict[str, Any]) -> list[dict[str, Any]]:
    """The rows on screen: the draft's while there is one, the stored rule's otherwise."""
    if _drafting(context):
        return list(context["draft"]["rules"])
    return _stored_rules(context)


def unsaved(drafts: dict[str, Any], collections: list[dict[str, Any]],
            known: list[collection_rules.Field]) -> set[str]:
    """The collections whose draft asks for something other than their stored rule."""
    stored = {str(one.get("name") or ""): one for one in collections}
    found = set()
    for name, draft in drafts.items():
        if "rules" not in draft or name not in stored:
            continue
        have = collection_rules.rows_from(stored[name].get("filters"), known)
        if (collection_rules.filters_from(draft["rules"], known)
                != collection_rules.filters_from(have, known)):
            found.add(name)
    return found


@on_page
async def _edit_rules(context: dict[str, Any], change: Callable[[list[dict[str, Any]]], None],
                      *, structural: bool = False, refresh: bool = True) -> None:
    """Change the drafted rules. `structural` where the rows themselves change - one
    added, removed or asked another way - and the controls are drawn again; otherwise
    they stay and only what reads the rules is. `refresh` false holds the preview back,
    for a list that is still open."""
    rows = [dict(one) for one in _rules_now(context)]
    change(rows)
    context["draft"]["rules"] = rows
    if refresh:
        await _drafted(context, structural=structural)


async def _drafted(context: dict[str, Any], *, structural: bool = False) -> None:
    """The preview and the marks read again after the draft changed, and the parts of the
    section that read them drawn again. The whole section where it changed what it
    offers: the fork, a stored list and a draft each lay it out differently."""
    await context["reload"]()
    _read_drawn(context, structural=structural)


def _read_drawn(context: dict[str, Any], *, structural: bool = False) -> None:
    """The parts of the section that read the collection, drawn again after it was."""
    if _shape(context) != context.get("shape"):
        _refill(context, "games")
        return
    _refill(context, "sentence", "draft_bar", "heading", "list")
    if structural:
        _refill(context, "rules")
    adder = context.get("adder")
    if adder is not None and not adder.is_deleted:
        adder.hold(collection_adds.holds(
            (context.get("membership") or {}).get("members") or [])[0])


def _add_rule_button(context: dict[str, Any]) -> None:
    used = {str(one.get("field") or "") for one in _rules_now(context)}
    offered = [one for one in context["fields"] if one.name not in used and one.askable]
    button = ui.button(t("manager.workbench.add_rule"), icon=verbs.ADD) \
        .props("flat dense no-caps size=sm").classes("manager-action")
    if not offered:
        button.disable()
        return
    with button, ui.menu():
        for one in offered:
            ui.menu_item(one.label,
                         on_click=lambda _e=None, one=one: _edit_rules(
                             context, lambda rows: rows.append(
                                 collection_rules.row_on(one)), structural=True)) \
                .classes("manager-menu-item")


def _rules_block(context: dict[str, Any], row: dict[str, Any]) -> None:
    rows = _rules_now(context)
    smart = _is_dynamic(row)
    with ui.row().classes("items-center gap-1 no-wrap manager-group"):
        ui.label(t("manager.workbench.rules"))
        if smart:
            ui.icon(verbs.SMART).classes("manager-heading-mark")
    if not rows and not smart:
        with ui.row().classes("items-center gap-2 w-full no-wrap py-1"):
            ui.label(t("manager.workbench.hand_picked_rules")) \
                .classes("manager-help min-w-0")
            _add_rule_button(context)
        return
    with ui.column().classes("gap-1 w-full min-w-0"):
        for index, one in enumerate(rows):
            _condition(context, index, one)
    with ui.row().classes("items-center gap-2 manager-slot-actions"):
        _add_rule_button(context)
    _region(context, "sentence", partial(_rule_sentence, context))()


def _rule_sentence(context: dict[str, Any]) -> None:
    added, taken = _by_hand(context)
    ui.label(collection_rules.sentence(_rules_now(context), context["fields"], added, taken)) \
        .classes("manager-help manager-rule-sentence mt-1 mb-2")


def _by_hand(context: dict[str, Any]) -> tuple[int, int]:
    members = (context.get("membership") or {}).get("members") or []
    added = {one.get("game") for one in members if one.get("origin") == "named"}
    taken = {one.get("game") for one in members if one.get("origin") == "excluded"}
    return len(added), len(taken)


def _condition(context: dict[str, Any], index: int, row: dict[str, Any]) -> None:
    known = context["fields"]
    field = collection_rules.by_name(known).get(str(row.get("field") or ""))
    if field is None:
        return
    used = {str(one.get("field") or "") for at, one in enumerate(_rules_now(context))
            if at != index}

    def put(**changes: Any) -> Callable[[list[dict[str, Any]]], None]:
        """The row with `changes` on it. A change given as a function is handed the value
        the row holds now: the controls stay drawn across edits, so what they were drawn
        with is not."""
        def change(rows: list[dict[str, Any]]) -> None:
            rows[index] = {**rows[index], **{
                key: new(rows[index].get(key)) if callable(new) else new
                for key, new in changes.items()}}
        return change

    def moved_to(rows: list[dict[str, Any]]) -> None:
        rows[index] = collection_rules.row_on(collection_rules.by_name(known)[which.value])

    def asked_another_way(rows: list[dict[str, Any]]) -> None:
        rows[index] = {**rows[index], "op": how.value,
                       "value": _kept_value(rows[index], how.value)}

    def gone(rows: list[dict[str, Any]]) -> None:
        del rows[index]

    with ui.element("div").classes("manager-condition"):
        which = dropdown.Select({one.name: one.label for one in known
                                 if (one.name not in used and one.askable)
                                 or one.name == field.name},
                                value=field.name)
        which.on_value_change(lambda: _edit_rules(context, moved_to, structural=True))
        if field.summary:
            which.tooltip(field.summary)
        asks = field.operators
        if len(asks) > 1:
            how = dropdown.Select({op: collection_rules.operator_word(op)
                                   for op in asks}, value=row.get("op"))
            how.on_value_change(lambda: _edit_rules(context, asked_another_way,
                                                    structural=True))
        else:
            ui.label(collection_rules.operator_word(asks[0])) \
                .classes("manager-condition-op")
        with ui.element("div").classes("manager-condition-value"):
            _condition_value(context, field, row, put)
        ui.button(icon=verbs.CANCEL,
                  on_click=lambda: _edit_rules(context, gone, structural=True)) \
            .props("flat dense round size=sm").classes("manager-condition-remove") \
            .tooltip(t("manager.workbench.remove_rule"))


def _kept_value(row: dict[str, Any], op: str) -> Any:
    """What a row keeps asking for when only how it asks changes."""
    before = row.get("op")
    kinds = ({collection_rules.AT_LEAST, collection_rules.EXACTLY},
             {collection_rules.BEFORE, collection_rules.AFTER},
             {collection_rules.ANY_OF, collection_rules.NONE_OF})
    if any(before in pair and op in pair for pair in kinds):
        return row.get("value")
    return None


def _condition_value(context: dict[str, Any], field: collection_rules.Field,
                     row: dict[str, Any],
                     put: Callable[..., Callable[[list[dict[str, Any]]], None]]) -> None:
    op, value = row.get("op"), row.get("value")
    if op in (collection_rules.YES, collection_rules.NO):
        return
    if op in (collection_rules.ANY_OF, collection_rules.NONE_OF, collection_rules.STARTS_WITH):
        _many_values(context, field, value, put)
        return
    if op in (collection_rules.AT_LEAST, collection_rules.EXACTLY):
        stars = dropdown.Select(
            {one: t("manager.stars.5", n=star_words(one)) for one in field.values},
            value=(value if value in field.values else None))
        stars.on_value_change(lambda: _edit_rules(context, put(value=stars.value)))
        return
    if op == collection_rules.BETWEEN:
        ends = value if isinstance(value, dict) else {}

        def end(which: str) -> Callable[[int | None], Callable[[list[dict[str, Any]]], None]]:
            return lambda year: put(value=lambda held: {
                **(held if isinstance(held, dict) else {}), which: year})

        with ui.row().classes("items-center gap-2 no-wrap"):
            _year_box(context, ends.get("from"), end("from"))
            ui.label(t("manager.workbench.and")).classes("manager-condition-op")
            _year_box(context, ends.get("to"), end("to"))
        return
    _year_box(context, value, lambda year: put(value=year))


def _year_box(context: dict[str, Any], value: Any,
              change: Callable[[int | None], Callable[[list[dict[str, Any]]], None]],
              ) -> None:
    """A year, written when the box is left or Enter is pressed."""
    box = ui.number(value=value, format="%d", placeholder=t("word.year")) \
        .props("dense borderless debounce=0") \
        .classes("manager-edit-field manager-edit-narrow")
    held = {"year": value}

    async def done() -> None:
        typed = box.value
        year = int(typed) if isinstance(typed, (int, float)) else None
        if year != held["year"]:
            held["year"] = year
            await _edit_rules(context, change(year))

    box.on("blur", done)
    box.on("keydown.enter", lambda: box.run_method("blur"))


def _many_values(context: dict[str, Any], field: collection_rules.Field, value: Any,
                 put: Callable[..., Callable[[list[dict[str, Any]]], None]]) -> None:
    """Written to the draft on every pick and read once the list closes: what reads the
    draft is drawn again behind the open list otherwise, once per pick."""
    chosen = [str(one) for one in value or []]
    offered = [*field.values, *(one for one in chosen if one not in field.values)]
    control: dropdown.Select
    if field.name == "tags":
        control = tag_chips.Picker(offered, value=chosen,
                                   looks=context["library"].tag_looks(),
                                   counts=field.counts)
    else:
        control = panel.CountedSelect(offered, field.counts, value=chosen,
                                      typed=len(offered) > 8)
    control.props('popup-content-class="manager-picker-popup"') \
        .classes("w-full min-w-0")
    held = {"open": False, "waiting": False}

    async def picked() -> None:
        held["waiting"] = held["open"]
        await _edit_rules(context, put(value=list(control.value or [])),
                          refresh=not held["open"])

    def shown() -> None:
        held["open"] = True

    async def hidden() -> None:
        held["open"] = False
        if held["waiting"]:
            held["waiting"] = False
            await _drafted(context)

    control.on_value_change(picked)
    control.on("popup-show", shown)
    control.on("popup-hide", hidden)


@on_page
async def _save_rules(context: dict[str, Any]) -> None:
    """Write the rule and nothing else, then stop drafting."""
    row, library = _collection(context), context["library"]
    wanted = collection_rules.filters_from(context["draft"].get("rules") or [],
                                           context["fields"])
    if not wanted:
        if _is_dynamic(row):
            await _take_rules_away(context)
        else:
            await _cancel_rules(context)
        return
    order = context["draft"].get("order") or {}
    try:
        await run.io_bound(library.patch_collection, row["name"],
                           {"filters": wanted, **order})
    except Exception as exc:  # noqa: BLE001
        ui.notify(t("manager.workbench.save_failed"), caption=why(exc), type="negative")
        return
    context["draft"].pop("rules", None)
    context["draft"].pop("order", None)
    await _written(context)


async def _cancel_rules(context: dict[str, Any]) -> None:
    context["draft"].pop("rules", None)
    await context["rebuild"]()


async def _keep_what_it_found(context: dict[str, Any]) -> None:
    # Imported here: library.collections imports library.games, which imports this module.
    from manager.library.collections import keep_result

    await keep_result(context["library"], context["state"], [_collection(context)["name"]],
                      lambda name: _written(context, name or ""))


@on_page
async def _take_rules_away(context: dict[str, Any]) -> None:
    row = _collection(context)
    name = row["name"]
    added = _by_hand(context)[0]
    choices = {"found": t("manager.workbench.keep_found", count=int(row.get("count") or 0)),
               "added": t("manager.workbench.keep_added", count=added) if added
               else t("manager.workbench.keep_none")}
    with frame.opened(t("manager.workbench.take_rules_away", name=name)) as box:
        choice = ui.radio(choices, value="found").props("dense")
        with frame.footer():
            frame.cancel(lambda: box.submit(None))
            frame.answer(t("manager.workbench.remove_rules"),
                         lambda: box.submit(choice.value), icon=verbs.REMOVE)
    answer = await box
    if answer == "found":
        await _keep_what_it_found(context)
        return
    if answer != "added":
        return
    try:
        await run.io_bound(context["library"].patch_collection, name,
                           {"clear_filters": True})
    except Exception as exc:  # noqa: BLE001
        ui.notify(t("manager.workbench.save_failed"), caption=why(exc), type="negative")
        return
    context["draft"].pop("rules", None)
    ui.notify(t("manager.workbench.converted", name=name), type="positive")
    await _written(context)


def _shown_games(lens: dict[str, Any] | None) -> set[str]:
    return {str(one.get("game") or "") for one in (lens or {}).get("members") or []
            if one.get("included") and not one.get("past_limit")}


def _draft_bar(context: dict[str, Any]) -> None:
    if not context.get("unsaved"):
        return
    preview = context.get("preview") or {}
    now, then = _shown_games(context.get("membership")), _shown_games(preview)
    asks = collection_rules.filters_from(context["draft"].get("rules") or [],
                                         context["fields"])
    matched = int(preview.get("matched") or 0)
    said = [(t("manager.workbench.games_match", count=matched) if matched
             else t("manager.workbench.search_empty")) if asks
            else t("manager.workbench.no_rules")]
    if then - now:
        said.append(t("manager.workbench.join", count=len(then - now)))
    if now - then:
        said.append(t("manager.workbench.leave", count=len(now - then)))
    with ui.row().classes("items-center gap-x-3 gap-y-1 w-full manager-draft-bar"):
        with ui.row().classes("items-center gap-2 no-wrap min-w-0"):
            ui.label(game_tables.JOIN.join(said)).classes("manager-draft-said")
            ui.label(game_tables.JOIN.strip()).classes("manager-draft-said")
            ui.label(t("word.not_saved")).classes("manager-draft-state") \
                .tooltip(t("manager.workbench.not_saved_rules"))
        with ui.row().classes("items-center gap-2 no-wrap ml-auto"):
            panel.action(t("word.cancel"), lambda: _cancel_rules(context),
                         icon=verbs.CANCEL, hint=t("manager.workbench.cancel_rules.help"))()
            ui.button(t("manager.workbench.save_rules"), icon=verbs.SAVE,
                      on_click=lambda: _save_rules(context)) \
                .props("dense no-caps no-wrap unelevated size=sm")


# --- the order ------------------------------------------------------------------


def _shown_order(context: dict[str, Any], row: dict[str, Any]) -> tuple[str, Any, dict[str, str]]:
    """The order and direction the order bar shows, with the menu it picks from. A stored
    order the menu has dropped reads as the default."""
    row = _ordered(context, row)
    by, direction = row.get("order_by") or DEFAULT_ORDER_BY, row.get("direction")
    orders = _orders(context, row, by)
    if by not in orders:
        by, direction = DEFAULT_ORDER_BY, DEFAULT_DIRECTION
    return by, direction, orders


def _order_bar(context: dict[str, Any], row: dict[str, Any]) -> None:
    by, _, orders = _shown_order(context, row)

    async def ordered(event: Any) -> None:
        if event.value == _shown_order(context, row)[0]:
            return
        changes: dict[str, Any] = {"order_by": event.value}
        if event.value in NATURAL_DIRECTION:
            changes["direction"] = NATURAL_DIRECTION[event.value]
        await _set_order(context, changes)

    async def turned(event: Any) -> None:
        now, direction, _ = _shown_order(context, row)
        if event.value != direction:
            await _set_order(context, {"order_by": now, "direction": event.value})

    def tail() -> None:
        """What follows the order: its direction, and the ranked list's state."""
        now, direction, _ = _shown_order(context, row)
        if now in DIRECTION_WORDS:
            words = DIRECTION_WORDS[now]
            dropdown.Select({way: t(words[way]) for way in ("asc", "desc")},
                            value=direction or NATURAL_DIRECTION[now], on_change=turned)
        ranking = _ranking(context, now)
        if ranking:
            ui.label(read_state(ranking) if ranking.get("offered")
                     else t("manager.workbench.ranked_off")) \
                .classes("manager-order-label")

    with ui.row().classes("items-center gap-x-4 gap-y-1 w-full manager-order-bar"):
        with ui.row().classes("items-center gap-2 no-wrap grow"):
            ui.label(t("manager.workbench.order")).classes("manager-order-label")
            dropdown.Select(orders, value=by, on_change=ordered)
            _region(context, "order_tail", tail)()
        with ui.row().classes("items-center gap-2 no-wrap ml-auto"):
            ui.label(t("manager.workbench.show_limit")).classes("manager-order-label")
            _limit_box(context, row)


def _orders(context: dict[str, Any], row: dict[str, Any], by: str) -> dict[str, str]:
    """The order menu. A stored ranked order is in it whether or not it is offered."""
    orders = {token: t(key) for token, key in SORT_LABELS.items()}
    if not _is_dynamic(row) and not _rules_drafted(context):
        orders = {MANUAL_ORDER: t("order.by.manual"), **orders}
    orders |= context["state"].get("ranked_orders") or {}
    ranking = _ranking(context, by)
    if ranking and by not in orders:
        orders[by] = ext_lists.ranked_label(ranking)
    return orders


def _ranking(context: dict[str, Any], by: str) -> dict[str, Any] | None:
    """The stored collection's ranked view, when `by` is the order it is stored in."""
    stored = _collection(context)
    return stored.get("ranking") if stored.get("order_by") == by else None


def _limit_box(context: dict[str, Any], row: dict[str, Any]) -> None:
    box = ui.number(value=_ordered(context, row).get("limit"), min=1, format="%d",
                    placeholder=t("word.any")) \
        .props("dense borderless debounce=0") \
        .classes("manager-edit-field manager-edit-narrow")

    async def done() -> None:
        typed = box.value
        wanted = max(1, int(typed)) if isinstance(typed, (int, float)) else None
        if wanted == _ordered(context, row).get("limit"):
            return
        await _set_order(context, {"limit": wanted} if wanted else {"clear_limit": True})

    box.on("blur", done)
    box.on("keydown.enter", lambda: box.run_method("blur"))


def _ordered(context: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    """The collection as its order reads with a template's order drafted over it."""
    order = context["draft"].get("order")
    if order is None:
        return row
    merged = row | order
    if order.get("clear_limit"):
        merged.pop("limit", None)
    return merged


async def _set_order(context: dict[str, Any], changes: dict[str, Any]) -> None:
    """A stored order is written and the list drawn from it again; a template's drafted
    order is held with the rules until they are saved."""
    row = _collection(context)
    before = _shown_order(context, row)[0]
    tail = ("order_tail",) if changes.get("order_by", before) != before else ()
    order = context["draft"].get("order")
    if order is None:
        await _save(context, changes, *tail, "heading", "list")
        return
    if "limit" in changes:
        order.pop("clear_limit", None)
    if "clear_limit" in changes:
        order.pop("limit", None)
    order.update(changes)
    _refill(context, *tail)


# --- what the collection holds ----------------------------------------------------


class _Listing(NamedTuple):
    """The members the list shows: the stored collection's, or a draft's preview."""
    live: bool
    lens: dict[str, Any]
    find: str
    kept: list[dict[str, Any]]
    excluded: list[dict[str, Any]]


def _listing(context: dict[str, Any]) -> _Listing:
    live = not context.get("unsaved")
    lens = (context.get("membership") if live else context.get("preview")) or {}
    members = lens.get("members") or []
    name = _collection(context)["name"]
    find = (context["state"].setdefault("member_find", {}).get(name, "")
            .strip().lower()) if live else ""
    if find:
        members = [one for one in members if find in str(one.get("name") or "").lower()]
    return _Listing(live, lens, find,
                    [one for one in members if (one.get("origin") or "") != "excluded"],
                    [one for one in members if (one.get("origin") or "") == "excluded"])


def _games_list(context: dict[str, Any], row: dict[str, Any],
                controls: Callable[[], None]) -> None:
    _region(context, "heading", partial(_list_heading, context, row))()
    controls()
    _find_box(context)
    _region(context, "list", partial(_member_list, context, row))()


def _list_heading(context: dict[str, Any], row: dict[str, Any]) -> None:
    found = _listing(context)
    playable = sum(1 for one in found.kept
                   if one.get("included") and not one.get("past_limit"))
    with ui.row().classes("items-center gap-2 w-full mt-2"):
        ui.label(t("manager.workbench.games") if found.lens.get("error")
                 else _games_heading(playable,
                                     sum(1 for one in found.kept if _is_hidden(one)),
                                     len(found.excluded))) \
            .classes("manager-card-title whitespace-nowrap")
        if found.live and int(row.get("count") or 0):
            with ui.element("span").classes("whitespace-nowrap"):
                panel.link(t("manager.workbench.games_visible"), to="/manager?"
                           + deeplink.query({"view": "games",
                                             "collection": _collection(context)["name"]}))()


def _find_box(context: dict[str, Any]) -> None:
    """Outside the regions it filters: drawing it again would take the typing out from
    under the person typing."""
    found = _listing(context)
    name = _collection(context)["name"]
    if not found.live or not _searchable(context):
        return
    box = ui.input(placeholder=t("manager.workbench.find_collection")) \
        .props("dense outlined clearable debounce=250") \
        .classes("w-full mb-2 mt-1")
    box.value = context["state"]["member_find"].get(name, "")

    def look() -> None:
        context["state"]["member_find"][name] = box.value or ""
        _refill(context, "heading", "list")

    box.on_value_change(look)


def _member_list(context: dict[str, Any], row: dict[str, Any]) -> None:
    live, lens, find, kept, excluded = _listing(context)
    name = _collection(context)["name"]
    if lens.get("error"):
        panel.line(t("manager.workbench.lookup_failed"), hint=str(lens["error"]),
                   classes="manager-help text-warning")
        return
    # Only a list with no rules is arranged by hand, and only whole: a find draws a part
    # of it, and an order has to be the whole membership.
    arrange = live and not _is_dynamic(row) and not find and len(kept) > 1
    smart = _is_dynamic(row) if live else _rules_drafted(context)
    art_look = context["library"].thumbnail_look()
    with ui.column().classes("gap-0 w-full manager-member-list") \
            .props('data-arrange="hub_member_moved"') as listed:
        listed._props[row_drag.LIST] = True
        if arrange:
            listed._props[row_drag.PLACED] = True
        if live and not _is_dynamic(row) and not find \
                and (row.get("order_by") or "") == MANUAL_ORDER:
            listed._props[row_drag.END] = True
        cut = False
        for member in kept:
            if member.get("past_limit") and not cut:
                cut = True
                ui.label(t("manager.workbench.past_limit", limit=row.get("limit") or 0)) \
                    .classes("manager-limit-line")
            _member_line(context, member, arrange=arrange, live=live, smart=smart,
                         look=art_look)
    # Grouped, not inline: a handful of rows somebody took out do not belong scattered
    # through forty they left in, and they are the ones most likely to be wanted back.
    if excluded:
        ui.label(t("manager.workbench.removed_count", count=(len(excluded)))) \
            .classes("manager-group mt-3")
        with ui.column().classes("gap-0 w-full"):
            for member in excluded:
                _member_line(context, member, live=live, smart=smart, look=art_look)
    if not live:
        return
    # `kept`, not `members`: the excluded rows are drawn in their own group below, so
    # the indices the browser reports are into this list. Sending `members` sends the
    # excluded ones too - a game both named and excluded appears twice, and the route
    # refuses the whole move.
    #
    # Read at the drop by the page's listener, `member_moved`.
    context["state"]["member_move"] = (context, kept) if arrange else None
    if arrange:
        ui.run_javascript(arranging.SCRIPT)
    # A write rebuilds the panel, so it is a new element starting at the top - and
    # changing a member's table sent a forty-row list back to the beginning. The
    # position is remembered per collection, because arriving at a different one
    # should start at the top rather than wherever the last one was left.
    ui.run_javascript(_KEEP_SCROLL % name.replace("'", "\\'"))


async def member_moved(state: dict[str, Any], moved: Any) -> None:
    """A drop in a collection's list, applied to the list drawn last."""
    held = state.get("member_move")
    if held:
        await _reorder(held[0], held[1], moved)


@on_page
async def _reorder(context: dict[str, Any], members: list[dict], moved: Any) -> None:
    """Put one game where it was dropped.

    Sent as the whole ordered list, which is what the route takes - atomic, and no
    index arithmetic on either side of the wire.
    """
    try:
        source, target = int(moved["from"]), int(moved["to"])
    except (KeyError, TypeError, ValueError):
        return
    order = [m["game"] for m in members]
    if not (0 <= source < len(order) and 0 <= target < len(order)) or source == target:
        return
    order.insert(target, order.pop(source))
    library = context["library"]
    try:
        await run.io_bound(library.set_collection_order,
                           _collection(context)["name"], order)
    except Exception as exc:
        ui.notify(t("manager.workbench.move_failed"), caption=why(exc), type="negative")
        return
    await _written(context)


def _games_heading(playable: int, hidden: int, taken: int) -> str:
    if hidden and taken:
        return t("manager.workbench.count_both", count=playable,
                 hidden=hidden, taken=taken)
    if hidden:
        return t("manager.workbench.count_hidden", count=playable, hidden=hidden)
    if taken:
        return t("manager.workbench.count_removed", count=playable, taken=taken)
    return t("manager.workbench.count_games", count=playable)


def _is_hidden(member: dict[str, Any]) -> bool:
    return ((member.get("tables") or [{}])[0].get("origin") == "hidden"
            and member.get("origin") != "excluded")


def _locked_to(member: dict[str, Any]) -> str:
    return str(member.get("ref_table") or "") if member.get("origin") == "named" else ""


def _member_line(context: dict[str, Any], member: dict[str, Any], *,
                 arrange: bool = False, live: bool = True, smart: bool = False,
                 look: thumbnails.Look | None = None) -> None:
    origin = member.get("origin") or ""
    tables = member.get("tables") or []
    table = tables[0] if tables else {}
    gone = not tables or table.get("origin") == "missing"
    game = context.get("games_by_id", {}).get(str(member.get("game") or "")) or {}
    made = game_tables.made(game)
    line = ui.row().classes("items-center gap-2 w-full no-wrap manager-member-row") \
        .props(f'data-origin="{origin}"')
    if member.get("past_limit"):
        line.classes(add="manager-past-limit")
    # The handle and the action sit outside the two text lines so they center against
    # the row rather than against its first line, which read as pinned to the name.
    with line:
        if arrange:
            # Focusable, because the keyboard path grabs from here: without a tab stop
            # the arrangement is mouse-only, which is the same trap the hover-revealed
            # row action had.
            ui.icon("drag_indicator").classes("manager-drag-handle") \
                .props('tabindex=0 role=button') \
                .tooltip(t("manager.workbench.drag_hint"))
        with thumbnails.beside(member.get(thumbnails.FIELD), icons.GAMES, look=look), \
                ui.column().classes("gap-0 grow min-w-0"):
            with ui.row().classes("items-center gap-2 w-full no-wrap"):
                ui.label(member.get("name") or member.get("game") or "") \
                    .classes("manager-member-name manager-cell-identifier grow min-w-0 "
                             "truncate")
                if smart and origin == "named":
                    ui.label(t("manager.workbench.held_manually_added")) \
                        .classes("manager-member-chip manager-tier manager-tier--off") \
                        .tooltip(t("manager.workbench.added_by_hand"))
                if gone and origin != "excluded":
                    word, why = game_tables.GONE_WORDS
                    ui.label(word).tooltip(why) \
                        .classes("manager-member-chip manager-chip-warn")
                elif _is_hidden(member):
                    ui.label(t("word.hidden")) \
                        .tooltip(t("manager.workbench.hidden_row.help")) \
                        .classes("manager-member-chip manager-chip-warn")
            said = game_tables.table_name(table) if table.get("id") and not gone else ""
            missing = table.get("origin") == "missing"
            if made or said or missing:
                with ui.row().classes("items-center gap-1 w-full no-wrap min-w-0 "
                                      "manager-member-table"):
                    if made:
                        ui.label(made).classes("manager-cell-made whitespace-nowrap")
                    if made and (said or missing):
                        ui.label(game_tables.JOIN.strip()).classes("manager-cell-join")
                    if said:
                        # The line already answers "which table does this use?", so it
                        # is also where that is changed. Removed rows are not in the
                        # collection and have nothing to point anywhere, and a preview
                        # is not stored yet.
                        _table_choice(context, member, table, said,
                                      editable=live and origin != "excluded")
                    elif missing:
                        ui.label(t("manager.workbench.table_gone")) \
                            .classes("text-warning truncate min-w-0")
        if live:
            with ui.element("div").classes("manager-row-action"):
                _member_action(context, member, origin)


def _table_choice(context: dict[str, Any], member: dict[str, Any],
                  table: dict[str, Any], said: str, *, editable: bool) -> None:
    """The table half of the row's second line, and the menu that changes which table
    this row plays."""
    with ui.row().classes("items-center gap-1 no-wrap grow min-w-0 "
                          "manager-member-table-line") as line:
        if _locked_to(member):
            word, why = game_tables.LOCKED_WORDS
            ui.icon(verbs.LOCKED).classes("manager-member-lock")
            ui.label(word).tooltip(why)
            ui.label(game_tables.JOIN.strip()).classes("manager-cell-join")
        # The same line, and the same tooltip, as a game's Tables section: version and
        # author on screen, the filename a hover away. One formatter, so the two
        # surfaces cannot drift apart.
        _built_label(table, said).classes("grow min-w-0")
        if not editable:
            return
        ui.icon("expand_more").classes("manager-member-table-caret")
        line.classes(add="cursor-pointer")
        # Anchored inside the element it belongs to. Built as a sibling it lands
        # wherever the parent row happens to start, which put an earlier menu 726px
        # from the control that opened it.
        with line, ui.menu().props('anchor="bottom left" self="top left"'):
            holder = ui.column().classes("gap-0 w-full items-stretch")
        # Filling only. The menu is a child of this line, so Quasar already opens and
        # closes it on a click here - calling `open()` as well reopened it in the same
        # gesture that closed it, which read as the menu refusing to shut.
        line.on("click", lambda: _fill_table_menu(context, member, table, holder))


@on_page
async def _fill_table_menu(context: dict[str, Any], member: dict[str, Any],
                           table: dict[str, Any], holder: Any) -> None:
    """This game's tables, read when asked for rather than with every row.

    One read per game and it is cached, but doing it while drawing forty rows would be
    forty requests on the loop - which `api.py` refuses outright.
    """
    game = str(member.get("game") or "")
    locked = _locked_to(member)
    following = table.get("origin") == "default"
    try:
        choices = await offload.io(context["library"].tables_for, game)
    except Exception as exc:
        ui.notify(t("manager.workbench.tables_failed"), caption=why(exc),
                  type="negative")
        return
    others = [other for other in (context.get("membership") or {}).get("members") or []
              if other is not member and str(other.get("game") or "") == game]
    # Every table this collection already holds for this game - what its refs *resolve
    # to*, not just what they name. Asking only about named tables offers the file a
    # following ref already resolves to, so a one-table game is told it could add the
    # table it has, and "Also Add" and "Lock to" read as the same entry.
    # An excluded ref counts too: offering a table that is being kept out would add a
    # member the collection immediately drops again.
    spoken = {str(held.get("id") or "") for one in [member, *others]
              for held in one.get("tables") or []}
    # Tables another of this game's rows already plays. A pairing appears once (2.10),
    # so pointing this row at one of them cannot be stored - it is shown and refused
    # rather than hidden, because an entry that vanishes without a reason is the same
    # puzzle as a row that vanishes without one.
    taken: set[str] = set()
    default_taken = False
    for other in others:
        tables = other.get("tables") or []
        if tables and tables[0].get("origin") == "default":
            default_taken = True
        else:
            taken.update(str(held.get("id") or "") for held in tables)
    offers = next((one for one in choices if one.get("default")), None)
    blocked = t("manager.workbench.already_collection")
    holder.clear()
    with holder:
        ui.item_label(t("manager.workbench.plays_heading")).props("header") \
            .classes("manager-menu-header")
        _table_menu_item(context, member, "", t("manager.workbench.game_default"),
                         chosen=following, blocked=blocked if default_taken else "",
                         under=game_tables.table_name(offers) if offers else "")
        for one in choices:
            table_id = str(one.get("id") or "")
            _table_menu_item(context, member, table_id,
                             t("manager.workbench.lock_to",
                               table=game_tables.table_name(one)),
                             chosen=table_id == locked,
                             blocked=blocked if table_id in taken else "")
        # The tournament case: a collection holding two versions of one game. Pointing
        # this row somewhere else cannot express it - that is one row, not two - so
        # adding is its own verb.
        spare = [one for one in choices if str(one.get("id") or "") not in spoken]
        if spare:
            ui.separator()
            ui.item_label(t("manager.workbench.also_add")).props("header") \
                .classes("manager-menu-header")
            for one in spare:
                _add_table_item(context, member, one)


async def _hold(context: dict[str, Any], member: dict[str, Any], table_id: str) -> None:
    """This row playing `table_id`, or its game's default where that is ""."""
    library, name = context["library"], _collection(context)["name"]
    game = str(member.get("game") or "")
    if member.get("origin") == "filter":
        await run.io_bound(library.add_to_collection, name, game, table_id)
        return
    await run.io_bound(library.set_member_table, name, game, table_id,
                       str(member.get("ref_table") or ""))


def _table_menu_item(context: dict[str, Any], member: dict[str, Any], table_id: str,
                     label: str, *, chosen: bool, blocked: str = "",
                     under: str = "") -> None:
    """One choice. The current one is ticked and inert - a menu that lets you pick what
    is already true reports a change that did not happen - and so is one the collection
    cannot hold, which says why instead of disappearing.

    `under` names what the game's default is today, so choosing it is not a blind pick.
    """
    @on_page
    async def pick() -> None:
        if chosen or blocked:
            return
        try:
            await _hold(context, member, table_id)
        except Exception as exc:
            ui.notify(t("manager.workbench.change_failed"), caption=why(exc), type="negative")
            return
        await _written(context)

    marked = "manager-menu-item"
    if chosen:
        marked += " manager-menu-on"
    elif blocked:
        marked += " manager-menu-blocked"
    item = ui.menu_item(on_click=pick).classes(marked)
    if blocked:
        # It stays open on a click it will not act on: closing would look like the
        # choice was taken.
        item.props("auto-close=false aria-disabled=true")
        with item:
            ui.tooltip(blocked).classes("manager-menu-tip")
    with item, ui.row().classes("items-center gap-2 no-wrap w-full"):
        if table_id:
            ui.icon(verbs.LOCKED).classes("manager-menu-mark")
        else:
            ui.element("span").classes("manager-menu-mark")
        with ui.column().classes("gap-0 grow min-w-0"):
            ui.label(label).classes("manager-menu-table-name")
            if under:
                ui.label(under).classes("manager-menu-sub")
        if chosen:
            ui.icon("check").classes("manager-menu-check")
        elif blocked:
            ui.icon("block").classes("manager-menu-check manager-menu-blocked-mark")


def _add_table_item(context: dict[str, Any], member: dict[str, Any],
                    table: dict[str, Any]) -> None:
    """A second row for this game, playing another of its tables.

    Landed next to the row it was asked from, not at the end: in a forty-row collection
    the end is off screen, and a new row you cannot see is indistinguishable from a
    click that did nothing.
    """
    library, name = context["library"], _collection(context)["name"]
    game = str(member.get("game") or "")
    playing = member.get("tables") or [{}]

    @on_page
    async def add() -> None:
        try:
            if member.get("origin") == "filter":
                # Found by the rule and not stored: stored as it plays now first, or
                # the game held by hand on the new table alone would replace this row.
                here = ("" if playing[0].get("origin") == "default"
                        else str(playing[0].get("id") or ""))
                await run.io_bound(library.add_to_collection, name, game, here)
                after = here
            else:
                after = str(member.get("ref_table") or "")
            await run.io_bound(library.add_to_collection, name, game,
                               str(table.get("id") or ""), after)
        except Exception as exc:
            ui.notify(t("said.add_failed"), caption=why(exc), type="negative")
            return
        await _written(context)

    with ui.menu_item(on_click=add).classes("manager-menu-item"), \
            ui.row().classes("items-center gap-2 no-wrap w-full"):
        # A plus, not the lock the entries above wear: both groups name the same tables,
        # and the mark is what says this one adds rather than chooses.
        ui.icon("add").classes("manager-menu-add")
        ui.label(game_tables.table_name(table)).classes("manager-menu-table-name grow min-w-0")


def _member_action(context: dict[str, Any], member: dict[str, Any],
                   origin: str) -> None:
    """The one thing this row's state makes sense to do.

    Removing says the same word whichever way the row got here, because that is the
    one intent a reader has. Underneath it differs - a matched row has to be recorded
    as an exclusion or the rule finds it again - but the list says so by moving it
    into "Manually removed", which is better than a tooltip explaining it in advance.
    """
    library = context["library"]
    name = _collection(context)["name"]

    @on_page
    async def put_back() -> None:
        row = collection_adds.row_of(member)
        try:
            await run.io_bound(library.unexclude_from_collection, name, row.game, row.table)
        except Exception as exc:
            ui.notify(t("said.action_failed"), caption=why(exc), type="negative")
            return
        ui.notify(t("manager.workbench.restore_into", name=name), type="positive")
        await _written(context)

    if origin == "excluded":
        ui.button(icon=verbs.REVERT, on_click=put_back) \
            .props("flat dense round size=sm").tooltip(t("manager.workbench.put_back_2"))
    else:
        ui.button(icon=verbs.REMOVE, on_click=lambda: collection_adds.remove_row(
            library, name, member, then=partial(_membership_changed, context, name))) \
            .props("flat dense round size=sm").tooltip(t("manager.workbench.remove_collection"))


_OPEN_ADD_BOX = """
(() => {
  const box = getElement(%d)?.$refs.qRef;
  if (box) { box.focus(); box.showPopup(); }
})()
"""


def _add_control(context: dict[str, Any], members: list[dict]) -> None:
    """Add a game the rule did not find, or that there is no rule to find.

    A game, not a table: a member with no table named follows whichever table is the
    game's default, which is what somebody adding a game to a list almost always
    means. Holding it to one table is the unusual intent and is a second act.
    """
    state = context["state"]
    again = state.pop("add_again", None) == _collection(context)["name"]
    games = context["library"].games
    if not games:
        return
    # Typed into, not scrolled: this is a picker over the whole library, and a list
    # that long is searched. `use-input` with no debounce filters from the first
    # character; `new-value-mode` is left off so only a real game can be chosen.
    picker = panel.GamePicker(games, collection_adds.holds(members)[0],
                              label=t("manager.workbench.add_games"),
                              art=context["library"].game_art()) \
        .props('input-debounce=0 hide-selected fill-input clearable') \
        .classes("w-full mt-2")
    (context["library"].thumbnail_look() or thumbnails.Look()).in_panel() \
        .apply_to_options(picker, "manager-picker-popup")

    @on_page
    async def add() -> None:
        if not picker.value:
            return
        name = _collection(context)["name"]
        state["add_again"] = name
        done = await collection_adds.add(context["library"], name,
                                         [collection_adds.Row(str(picker.value))],
                                         then=partial(_membership_changed, context, name))
        stays = state.pop("add_again", None) == name and done is not None
        if stays and not picker.is_deleted:
            picker.value = None
            ui.run_javascript(_OPEN_ADD_BOX % picker.id)

    context["adder"] = picker
    picker.on_value_change(add)
    ui.run_javascript(_ADD_BOX % (picker.id, "true" if again else "false"))
