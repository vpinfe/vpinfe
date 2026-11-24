"""Every setting this install has, what it is set to, and what may be changed.

`common/config/config_schema.py` declares what a setting is called, what it accepts and what it
means. This answers the three questions a settings page asks of that: describe it, read
it, write it - once, so the config file, the Manager and anything reading over HTTP
describe the same install the same way.

Values are typed the way the store types them - a bool arrives as a bool, a list as a
list - because a caller that has to know which strings mean true holds half a schema of
its own.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Sequence
from typing import Any

from common import events
from common.apps.contract import Field
from common.config import config_schema, path_state, paths
from common.config.labels import humanize
from common.config.paths import CONFIG_DIR, get_ini_config
from common.devices import install_presence
from common.failures import why
from common.i18n import t
from common.media.manufacturer_logos import resolve_logos_dir
from common.settings import port_checks

logger = logging.getLogger("vpinfe.common.settings.config_service")

_WRITING = threading.RLock()

# Where the settings sit beside the lists, in the file and on the wire.
SETTINGS = "settings"
THEME_OPTIONS = config_schema.THEME_OPTIONS

# Sections served over the API and refused a write. Empty; the refusal path below is
# what a caller-origin check would reuse.
READ_ONLY_SECTIONS: frozenset[str] = frozenset()


class UnknownSettingsError(ValueError):
    """Keys no schema knows. `keys` names every one of them."""

    def __init__(self, keys: list[str]) -> None:
        self.keys = sorted(keys)
        super().__init__(", ".join(self.keys))


class SettingsWriteError(RuntimeError):
    """The settings file could not be written. The values are already in the store."""


class MalformedPatchError(ValueError):
    """A section of a patch that is not an object."""


class ReadOnlySettingsError(ValueError):
    """Keys this install will not take over the wire. `keys` names every one."""

    def __init__(self, keys: list[str]) -> None:
        self.keys = sorted(keys)
        super().__init__(", ".join(self.keys))


class RefusedSettingsError(ValueError):
    """Values a setting cannot hold on this machine. `reasons` says why, keyed
    `section.key`."""

    def __init__(self, reasons: dict[str, str]) -> None:
        self.reasons = dict(reasons)
        super().__init__("; ".join(reasons.values()))


def _describe(option: config_schema.ConfigOption) -> dict[str, Any]:
    return {
        "section": option.section,
        "key": option.key,
        "type": option.type,
        "default": option.default,
        "label": option.label or option.key,
        "label_key": f"{option.keys}.label",
        "description": option.description,
        "choices": list(option.choices),
        "choice_labels": option.choice_labels,
        "choice_help": option.choice_help,
        "writable": option.section not in READ_ONLY_SECTIONS,
        # So a caller knows which strings name something on disk without matching on the
        # key. Empty for everything that is only text.
        "path": option.path,
        # Which live list is worth offering beside this setting, where one is. Named
        # rather than filled in here: the values change while the install runs, so a
        # caller asks for them when it draws rather than reading a snapshot taken when
        # the schema was described.
        "suggest": option.suggest,
        # What to gather it under on a page. Answered here rather than decided by each
        # surface, so the grouping is the schema's answer and not one each one invents.
        "group": option.group,
        "group_label": option.group_label,
        "editor": option.editor,
        # How many rows a text field gets. A setting declared over three lines that
        # arrives without this renders as a one-line box - which is what a command list
        # did until it was served.
        "lines": option.lines,
        "default_is": option.default_is,
        "unit": option.unit,
        "as_buttons": option.as_buttons,
        "min": option.minimum,
        "max": option.maximum,
        "group_note": option.group_note,
        "cuts_off_manager": option.cuts_off_manager,
        "moves_with": option.moves_with,
        "applies": option.applies,
        "blank": option.blank,
        "home": option.home,
    }


def _said(key: str, fallback: str) -> str:
    said = t(key)
    return fallback if said == key else said


def _describe_field(list_key: str, field: Field) -> dict[str, Any]:
    words = f"config.list.{list_key}.field.{field.key}"
    return {
        "key": field.key,
        "type": field.type,
        "default": field.default,
        "label": field.label or _said(f"{words}.label", humanize(field.key)),
        "description": field.description or _said(f"{words}.description", ""),
        "choices": [{"value": value, "label": label} for value, label in field.choices],
        "lines": field.lines,
        "minimum": field.minimum,
        "maximum": field.maximum,
        "path": field.path,
        "suffixes": list(field.suffixes),
    }


def _describe_list(spec: config_schema.ConfigList) -> dict[str, Any]:
    variant = spec.variant
    return {
        "key": spec.key,
        "label": _said(f"config.list.{spec.key}.label", humanize(spec.key)),
        "home": spec.home,
        "fields": [_describe_field(spec.key, field) for field in spec.fields],
        "variant": None if variant is None else {
            "field": variant.field,
            "kinds": [{"kind": kind,
                       "fields": [_describe_field(spec.key, field)
                                  for field in variant.fields(kind) or ()]}
                      for kind in variant.kinds()],
        },
    }


def settable(windows: Sequence[str]) -> tuple[config_schema.ConfigOption, ...]:
    """The schema's settings, with a screen for each of `windows` it does not declare
    placed after its own."""
    declared = config_schema.settable()
    held = {option.section for option in declared}
    added = tuple(option for option in map(config_schema.window_screen, dict.fromkeys(windows))
                  if option.section not in held)
    after = 1 + max((at for at, option in enumerate(declared)
                     if config_schema.window_name(option.section)), default=-1)
    return (*declared[:after], *added, *declared[after:])


def schema(windows: Sequence[str] = ()) -> dict[str, Any]:
    """What a settings page is built from. `windows` are the active theme's.

    Internal options are left out: they are runtime state that happens to live in the
    config file, and offering a last-played pointer as a setting invites someone to set
    it. `settable()` is the same predicate the config file's own docs use.
    """
    options = [_describe(option) for option in settable(windows)]
    sections = [{"name": name,
                 "writable": name not in READ_ONLY_SECTIONS,
                 "options": [o for o in options if o["section"] == name]}
                for name in dict.fromkeys(option["section"] for option in options)]
    return {"sections": sections, "count": len(options),
            "lists": [_describe_list(spec) for spec in config_schema.lists()]}


def blank_stands_for() -> dict[tuple[str, str], str]:
    """By (section, key), the path a blank path setting falls back to on this machine.

    Only a setting whose blank means a folder VPinFE picks for itself is here. A Tool or
    the browser left blank is discovered instead, and those answer through their own
    reports.
    """
    return {("backups", "folder"): str(paths.DEFAULT_BACKUPS_DIR),
            ("media", "manufacturer_logos_dir"): str(resolve_logos_dir("", CONFIG_DIR))}


def path_states() -> dict[str, Any]:
    """Every path setting and every path in a list's items, checked against this machine's
    disk.

    All of them in one answer rather than one call per field: a settings page wants the
    whole column at once, and the alternative is six requests that each stat one file.
    `default` is the path a blank one stands for, so the page can show where it points.
    """
    store = get_ini_config()
    options = path_state.path_options()
    members = path_state.item_paths({spec.key: store.get_list(spec.key)
                                     for spec in config_schema.lists()})
    found = path_state.look_all(
        [*(path_state.Ask(store.value(option.section, option.key), option.path)
           for option in options),
         *(path_state.Ask(one.value, one.kind) for one in members)])
    stands_for = blank_stands_for()
    checks: list[dict[str, Any]] = [
        {"section": option.section, "key": option.key, "path": option.path,
         "state": one.state, "reason": one.reason,
         "default": stands_for.get((option.section, option.key), "")}
        for option, one in zip(options, found[:len(options)], strict=True)]
    checks += [{"list": member.list_key, "id": member.id, "field": member.field,
                "path": member.kind, "state": one.state, "reason": one.reason}
               for member, one in zip(members, found[len(options):], strict=True)]
    return {"checks": checks}


def values(windows: Sequence[str] = ()) -> dict[str, dict[str, Any]]:
    """Current settings by section and key, typed. A setting the file does not carry
    answers its default, because that is what the install is actually running on."""
    store = get_ini_config()
    found: dict[str, dict[str, Any]] = {}
    for option in settable(windows):
        found.setdefault(option.section, {})[option.key] = \
            store.value(option.section, option.key)
    return found


def whole(windows: Sequence[str] = ()) -> dict[str, Any]:
    """The settings, every declared list, and what was chosen on each theme."""
    store = get_ini_config()
    return {SETTINGS: values(windows),
            **{spec.key: store.get_list(spec.key) for spec in config_schema.lists()},
            THEME_OPTIONS: store.theme_options()}


def set_values(wanted: dict[str, dict[str, Any]] | None,
               windows: Sequence[str] = ()) -> dict[str, dict[str, Any]]:
    """A patch of settings alone. See `apply`."""
    return apply({SETTINGS: wanted or {}}, windows)[SETTINGS]


def apply(patch: dict[str, Any] | None, windows: Sequence[str] = ()) -> dict[str, Any]:
    """A patch shaped like `whole`: `settings` is written key by key, a list replaces the
    list. Answers `whole`.

    Everything is checked first and the whole request is refused if any of it is wrong.
    A list a write brings is checked by the store (`ConfigListError`).
    """
    with _WRITING:
        return _apply(patch or {}, windows)


def require_sections(wanted: dict[str, Any]) -> None:
    """Each section of a settings patch is an object, or nothing."""
    for section, entries in wanted.items():
        if entries is not None and not isinstance(entries, dict):
            raise MalformedPatchError(t("error.config.section_object", section=section))


def _out_of_range(staged: list[tuple[str, str, Any]]) -> dict[str, str]:
    """Why each staged number falls outside the range its option declares, keyed
    `section.key`. A value that is not a number is not judged here."""
    reasons: dict[str, str] = {}
    for section, key, value in staged:
        option = config_schema.option(section, key)
        if option is None or (option.minimum is None and option.maximum is None):
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        field = f"{section}.{key}"
        if option.minimum is not None and number < option.minimum:
            reasons[field] = t("error.config.list.minimum", field=field,
                               minimum=f"{option.minimum:g}")
        elif option.maximum is not None and number > option.maximum:
            reasons[field] = t("error.config.list.maximum", field=field,
                               maximum=f"{option.maximum:g}")
    return reasons


def _apply(patch: dict[str, Any], windows: Sequence[str]) -> dict[str, Any]:
    store = get_ini_config()
    served = set(settable(windows))
    declared = {spec.key for spec in config_schema.lists()}
    wanted = patch.get(SETTINGS) or {}
    require_sections(wanted)
    staged: list[tuple[str, str, Any]] = []
    unknown: list[str] = [key for key in patch
                          if key not in (SETTINGS, THEME_OPTIONS) and key not in declared]
    refused: list[str] = []

    for section, entries in wanted.items():
        for key, value in (entries or {}).items():
            # Any spelling this setting has ever had, including a section it has since
            # moved out of. A caller written against an older name keeps working, which
            # is the contract every other reader of the config already honours.
            here, name = config_schema.locate(section, key)
            option = config_schema.option(here, name)
            if option is None or option not in served:
                unknown.append(f"{section}.{key}")
            elif option.section in READ_ONLY_SECTIONS:
                refused.append(f"{option.section}.{option.key}")
            else:
                staged.append((option.section, option.key, value))

    if unknown:
        raise UnknownSettingsError(unknown)
    if refused:
        raise ReadOnlySettingsError(refused)
    lists = {key: items for key, items in patch.items() if key in declared}
    chosen = _checked_theme_options(patch.get(THEME_OPTIONS))
    if not staged and not lists and not chosen:
        return whole(windows)
    if reasons := port_checks.refused(wanted) | _out_of_range(staged):
        raise RefusedSettingsError(reasons)

    changed: list[str] = []
    if lists:
        before = {key: store.get_list(key) for key in lists}
        store.set_lists(lists)
        changed += [key for key in lists if store.get_list(key) != before[key]]
    if chosen and _stage_theme_options(store, chosen):
        changed.append(THEME_OPTIONS)
    for section, key, value in staged:
        before_value = store.value(section, key)
        store.set_value(section, key, value)
        if store.value(section, key) != before_value:
            changed.append(f"{section}.{key}")
    _save(store)

    if any(section == "device" and key == "name" for section, key, _ in staged):
        # The registry holds a copy of what each install reported. This one just changed
        # what it reports, and every screen listing devices reads the copy.
        install_presence.record_self()
    _announce(store, changed)
    return whole(windows)


def _checked_theme_options(wanted: Any) -> dict[str, dict[str, Any]]:
    """The theme entries of a patch, by trimmed folder name. Only the shape is checked
    here: what a theme accepts is the theme's to say, and a caller that knows the themes
    checks before it gets here."""
    if wanted is None:
        return {}
    if not isinstance(wanted, dict) or not all(isinstance(one, dict) for one in wanted.values()):
        raise MalformedPatchError(t("error.config.theme_options_shape"))
    if not all(folder.strip() for folder in wanted):
        raise MalformedPatchError(t("error.config.theme_options_shape"))
    return {folder.strip(): values for folder, values in wanted.items()}


def _stage_theme_options(store: Any, wanted: dict[str, dict[str, Any]]) -> bool:
    """Each named theme's entry becomes what is given, or goes when it is empty; the
    themes not named are left alone. True when that changed anything."""
    before = store.theme_options()
    store.set_theme_options({**before, **wanted})
    return store.theme_options() != before


def theme_options() -> dict[str, dict[str, Any]]:
    """What the person chose on each theme, by the theme's folder name."""
    return get_ini_config().theme_options()


def put_theme_options(wanted: dict[str, dict[str, Any]]) -> None:
    """Save the entries given as they are; the caller has checked them. An empty entry
    takes that theme's choices away."""
    with _WRITING:
        store = get_ini_config()
        if _stage_theme_options(store, wanted):
            _save(store)
            _announce(store, [THEME_OPTIONS])


def drop_theme_options(folders: Sequence[str]) -> None:
    """Remove these themes' entries outright, as against an empty one, which says the
    person took their choices away."""
    with _WRITING:
        store = get_ini_config()
        before = store.theme_options()
        kept = {folder: values for folder, values in before.items() if folder not in folders}
        if kept != before:
            store.set_theme_options(kept)
            _save(store)
            _announce(store, [THEME_OPTIONS])


def _save(store: Any) -> None:
    try:
        store.save()
    except Exception as exc:
        logger.exception("Could not write the settings file")
        store.reread()
        raise SettingsWriteError(why(exc)) from exc


def _announce(store: Any, changed: list[str]) -> None:
    if changed:
        events.emit(events.SETTINGS_CHANGED, keys=changed, ini_config=store)


def writing() -> threading.RLock:
    """Held by anything that reads the config file and writes it back."""
    return _WRITING


def written(store: Any, changed: list[str]) -> None:
    """Save a store somebody else changed, and say which keys changed. Under `writing()`."""
    _save(store)
    _announce(store, changed)


class NoSuchItemError(LookupError):
    """A list holds no item with that id."""

    def __init__(self, item_id: str) -> None:
        super().__init__(item_id)
        self.item_id = item_id


def list_items(key: str) -> list[dict[str, Any]]:
    """The objects of a list. Anything else a hand-edited file holds there is not an item."""
    return [item for item in get_ini_config().get_list(key) if isinstance(item, dict)]


def list_item(key: str, item_id: str) -> dict[str, Any]:
    for item in list_items(key):
        if item.get("id") == item_id:
            return item
    raise NoSuchItemError(item_id)


def replace_list(key: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The whole list, in the order given. Reordering is this."""
    with _WRITING:
        store = get_ini_config()
        before = store.get_list(key)
        store.set_list(key, items)
        after = store.get_list(key)
        _save(store)
        _announce(store, [key] if after != before else [])
        return after


def _kept(key: str, kept: list[dict[str, Any]], item_id: str) -> dict[str, Any]:
    """The item as stored. The list's normalizer has the last word, so it may be gone."""
    for one in kept:
        if isinstance(one, dict) and one.get("id") == item_id:
            return one
    raise config_schema.ConfigListError(t("error.config.list.not_kept", list=key))


def add_item(key: str, item: dict[str, Any]) -> dict[str, Any]:
    """Append one item. The store mints its id; `item` brings none."""
    with _WRITING:
        items = list_items(key)
        known = {one.get("id") for one in items}
        kept = replace_list(key, [*items, item])
        fresh = [one for one in kept if isinstance(one, dict) and one.get("id") not in known]
        if not fresh:
            raise config_schema.ConfigListError(t("error.config.list.not_kept", list=key))
        return fresh[0]


def put_item(key: str, item_id: str, item: dict[str, Any]) -> dict[str, Any]:
    """Replace the item with that id where it stands, or append it under that id."""
    with _WRITING:
        items = list_items(key)
        given = {**item, "id": item_id}
        if any(one.get("id") == item_id for one in items):
            items = [given if one.get("id") == item_id else one for one in items]
        else:
            items.append(given)
        return _kept(key, replace_list(key, items), item_id)


def delete_item(key: str, item_id: str) -> None:
    with _WRITING:
        items = list_items(key)
        kept = [one for one in items if one.get("id") != item_id]
        if len(kept) == len(items):
            raise NoSuchItemError(item_id)
        replace_list(key, kept)
