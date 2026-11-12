"""The registry: which apps this install has.

The built-in source is code that ships with the program, so it cannot fail to be there -
`generic` has to exist for an install whose everything else is broken to still be
configurable. A loaded source arrives later and only ever adds, which is why everything
reads through `all_apps()` rather than a constant frozen at import time.

Two `apps` packages: this one is core's registry, the top-level `apps/` holds the
implementations.
"""

from __future__ import annotations

import re
from pathlib import Path

from common import i18n
from common.config.labels import ACRONYMS, field_label

from .contract import (
    App,
    Availability,
    Buttons,
    Claim,
    ConfigGroup,
    Entry,
    Field,
    Heading,
    Kinds,
    Pair,
    Parsed,
    Pause,
    PlayRecord,
    Plays,
    Session,
)

__all__ = [
    "App", "Availability", "Buttons", "Claim", "ConfigGroup", "Entry", "Field", "Heading",
    "Kinds", "OWN_FIELDS", "Pair", "Parsed", "Pause", "PlayRecord", "Plays", "Session",
    "all_apps", "app_for", "app_name", "default_app", "field_blank", "field_help",
    "field_words", "get", "group_words", "heading_words", "launcher_fields", "pair_words",
    "strip_suffix", "table_suffixes", "vps_formats",
]

# What every launcher has, whatever app it wraps. Commands to run around a table are
# not an app's business - Visual Pinball has no opinion about mounting a share - but they
# are this launcher's, because they run only when this launcher is what plays the table.
# The install-wide pair is in Settings, and runs outside these.
OWN_FIELDS: tuple[Field, ...] = (
    Field("on_table_start", type="text", lines=3),
    Field("on_table_exit", type="text", lines=3),
    Field("on_start_required", type="bool", default="false"),
)

_built_in_apps: tuple[App, ...] = ()


def _built_in() -> tuple[App, ...]:
    """Imported here rather than at the top of the file: importing `common.apps.contract`
    initializes this package, so an app imported first would find it half-built."""
    global _built_in_apps
    if not _built_in_apps:
        from apps import generic, vpx

        shipped = ((vpx, vpx.VPX), (generic, generic.GENERIC))
        for module, app in shipped:
            i18n.own(f"app.{app.id}", Path(str(module.__file__)).parent / "i18n")
        _built_in_apps = tuple(app for _, app in shipped)
    return _built_in_apps


_contributed: dict[str, App] = {}


def contribute(app: App) -> None:
    """Add an app an extension provides.

    Kept apart from the built-ins and always offered after them, so a contributed app
    cannot take a suffix out from under Visual Pinball by loading first. An id already in
    use is refused rather than allowed to win: ids are stored in a table's record, and
    two apps answering to one id makes what is stored ambiguous.
    """
    if any(one.id == app.id for one in _built_in()):
        raise ValueError(f"{app.id!r} is an app this build ships")
    if app.id in _contributed:
        raise ValueError(f"{app.id!r} is already provided by something else")
    _contributed[app.id] = app


def withdraw(app_id: str) -> None:
    """Take one back, when its extension is disabled or reloaded."""
    _contributed.pop(str(app_id or ""), None)


def withdraw_all() -> None:
    _contributed.clear()


def contributed() -> tuple[App, ...]:
    return tuple(_contributed.values())


def all_apps() -> tuple[App, ...]:
    """Every app this install has, in the order they are offered.

    Built-ins first. `app_for` takes the first that claims a file, so the order is what
    decides a contest, and this build's own answer wins one.
    """
    return (*_built_in(), *_contributed.values())


def default_app() -> App:
    """What a file nothing else claims is assumed to be."""
    return _built_in()[0]


def get(app_id: str | None) -> App | None:
    """The app with this id, or None. Ids are stored, so an unknown one is a real state
    rather than a programming error."""
    wanted = str(app_id or "").strip()
    return next((app for app in all_apps() if app.id == wanted), None)


def launcher_fields(app_id: str | None) -> tuple[Field, ...] | None:
    """What a launcher of this app holds: the app's fields, then `OWN_FIELDS`. None for
    an app this install lacks."""
    found = get(app_id)
    return (*found.fields, *OWN_FIELDS) if found is not None else None


def app_for(filename: str) -> App | None:
    """Which app claims this file, or None for something no app plays."""
    return next((app for app in all_apps() if app.claim.claims(filename)), None)


def vps_formats(app_id: str | None) -> tuple[str, ...]:
    """The VPS formats an app plays. Empty for an app that declares none and for an id
    this install lacks, and empty means every release is a candidate."""
    found = get(app_id)
    return found.vps_formats if found is not None else ()


def app_name(app_id: str | None) -> str:
    """What to call an app on screen. Ids are for the wire. An id nothing claims comes
    back as it came, because inventing a name for it would be worse."""
    wanted = str(app_id or "").strip()
    if not wanted:
        return "-"
    found = get(wanted)
    if found is None:
        return wanted
    return i18n.literal_or(found.name, f"app.{found.id}.name", fallback=found.id)[0]


_ASIDE = re.compile(r"(.*\S)\s*\(([^()]+)\)")


def field_words(app_id: str, field: Field) -> dict[str, str]:
    """`label`, `label_key` and `description` for a field on a launcher of this app.

    The app's catalog answers first, then the word its config hands back for a label the
    setting shares, then core's `launcher.field.*`, which holds the fields every launcher
    has. A setting the app names a set for carries the set's name, and no `label_key`. A
    label the program wrote is shown in title case, as every label is.
    """
    def leaf(name: str, literal: str, fallback: str, *shared: str) -> tuple[str, str]:
        return i18n.literal_or(literal, f"app.{app_id}.field.{field.key}.{name}", *shared,
                               f"launcher.field.{field.key}.{name}", fallback=fallback)

    config = getattr(get(app_id), "config", None)
    naming = getattr(config, "label_word", None)
    word = naming(field.key) if naming is not None else ""
    label, label_key = leaf("label", field.label, humanized(field.key),
                            *([f"app.{app_id}.{word}"] if word else []))
    if not label_key:
        label = field_label(label)
    grouping = getattr(config, "set_word", None)
    among = grouping(field.key) if grouping is not None else ""
    named_set = i18n.literal_or("", f"app.{app_id}.{among}")[0] if among else ""
    if named_set and (aside := _ASIDE.fullmatch(label)):
        label, label_key = i18n.t("launcher.label_aside", label=aside[1],
                                  aside=aside[2], set_name=named_set), ""
    elif named_set:
        label, label_key = i18n.t("launcher.label_in_set", label=label,
                                  set_name=named_set), ""
    return {"label": label, "label_key": label_key,
            "description": leaf("description", field.description, "")[0]}


def field_help(app_id: str, field: Field) -> str:
    """What a setting is for, in the app's catalog's words, or "" where it says nothing.
    Beside `description`, which is the program's own."""
    return i18n.literal_or("", f"app.{app_id}.field.{field.key}.help")[0]


def field_blank(app_id: str, field: Field) -> str:
    """What a launcher's field does left empty, in the app's catalog's words, or "" where
    it says nothing."""
    return i18n.literal_or("", f"app.{app_id}.field.{field.key}.blank")[0]


def field_nowhere(app_id: str, field: Field) -> str:
    """Why a launcher's field left empty has no file to stand for, in the app's catalog's
    words, or "" where it says nothing."""
    return i18n.literal_or("", f"app.{app_id}.field.{field.key}.nowhere")[0]


def choice_words(app_id: str, field: Field) -> dict[str, str]:
    """By stored value, the word the app's catalog has for each of a setting's choices,
    in place of the program's own where it has one."""
    found = {value: i18n.literal_or(
        "", f"app.{app_id}.field.{field.key}.choice.{value}.label")[0]
        for value, _label in field.choices}
    return {value: said for value, said in found.items() if said}


def choice_help(app_id: str, field: Field) -> dict[str, str]:
    """By stored value, what each of a setting's choices does, where the app's catalog
    says."""
    found = {value: i18n.literal_or(
        "", f"app.{app_id}.field.{field.key}.choice.{value}.help")[0]
        for value, _label in field.choices}
    return {value: said for value, said in found.items() if said}


# A capital after a small letter, or before a capital and a small letter, starts a word:
# `B2SHideGrill` reads `B2S Hide Grill`, and `PIN2DMD` is left whole.
_WORD_START = re.compile(r"(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


_CAPITALS = re.compile(r"\b[A-Z0-9]{4,}\b")


def humanized(key: str) -> str:
    """A key nobody gave words to, as near to words as it goes: its last part, split
    where its capitals start words and where a run of them is acronyms end to end."""
    said = _WORD_START.sub(" ", key.rsplit(".", 1)[-1])
    return _CAPITALS.sub(lambda run: " ".join(_acronyms(run.group(0)) or [run.group(0)]),
                         said)


def _acronyms(run: str) -> list[str] | None:
    """`B2SDMD` as `["B2S", "DMD"]`; None unless every part is one of `ACRONYMS`."""
    if run.lower() in ACRONYMS:
        return [run]
    for cut in range(len(run) - 1, 1, -1):
        if run[:cut].lower() in ACRONYMS and (rest := _acronyms(run[cut:])):
            return [run[:cut], *rest]
    return None


def group_words(app_id: str, group: ConfigGroup) -> dict[str, str]:
    label, label_key = i18n.literal_or(group.label,
                                       f"app.{app_id}.group.{group.key}.label",
                                       fallback=group.key)
    return {"label": label, "label_key": label_key}


def heading_words(app_id: str, group: str, heading: Heading) -> dict[str, str]:
    """`label`, `note` and `description` for one heading of a group's curated rows. The
    note is the app's catalog's, beside the program's own description."""
    base = f"app.{app_id}.group.{group}.heading.{heading.key}"
    note = i18n.literal_or("", f"{base}.note.{heading.variant}")[0] if heading.variant else ""
    return {"label": i18n.literal_or(heading.label, f"{base}.label",
                                     fallback=humanized(heading.key))[0],
            "note": note or i18n.literal_or("", f"{base}.note")[0],
            "description": heading.description}


def pair_words(app_id: str, group: str, pair: Pair) -> dict[str, str]:
    """`label`, `note` and `joiner` - what stands between its two numbers - for one pair
    of a group's curated rows."""
    base = f"app.{app_id}.group.{group}.pair.{pair.key}"
    return {"label": i18n.literal_or("", f"{base}.label", fallback=humanized(pair.key))[0],
            "note": i18n.literal_or("", f"{base}.note")[0],
            "joiner": i18n.literal_or("", f"{base}.joiner")[0]}


def table_suffixes() -> tuple[str, ...]:
    """Every extension that makes a file a table, for a folder listing to filter on."""
    return tuple(suffix for app in all_apps() for suffix in app.claim.suffixes)


def strip_suffix(filename: str) -> str:
    """The name without the extension its app claims it by."""
    app = app_for(filename)
    return app.claim.strip_suffix(filename) if app is not None else str(filename or "")
