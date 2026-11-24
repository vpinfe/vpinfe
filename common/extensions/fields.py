"""The field types an extension can ask for, as one list.

A dialog is drawn by the wizard control, a settings page by the Manager's extension page.
Each draws the types below and refuses to draw any other, so a type an extension invents
is stopped when it is declared and not when somebody opens the page.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from common.online import sites

from .accounts import scrubbed
from .store import RESERVED

# What `manager/kit/wizard.FIELD_TYPES` draws.
DIALOG_TYPES: frozenset[str] = frozenset({
    "string", "secret", "path", "select", "multi", "switch", "choice", "number",
    "checklist", "settle",
})

# What a settings page draws.
SETTINGS_TYPES: frozenset[str] = frozenset({
    "string", "secret", "switch", "select", "multi", "number", "path",
})

# Where a `path` setting's value must end up.
PATH_KINDS: tuple[str, ...] = ("dir", "file", "exe")

ALL_TYPES: frozenset[str] = DIALOG_TYPES | SETTINGS_TYPES

# What a dialog's step may carry. The wizard reads more than this for the flows core draws
# itself; none of the rest is an extension's to send.
STEP_KEYS: frozenset[str] = frozenset({
    "step", "title", "help", "fields", "confirm", "steps", "welcome", "ready", "reason",
    "summary", "notes", "errors", "acts",
})


class FieldError(ValueError):
    """A declared field is not one core can draw."""


def settings_fields(name: str, fields: Iterable[Any]) -> list[dict]:
    """The settings an extension declared, in the form core keeps them: every key
    present once, every type one a settings page draws, a `default` that is text.

    The file holds strings: a `switch` is `"true"`/`"false"` (default `"false"`), a `number`
    its digits, a `multi` a JSON list of the choices ticked (default `"[]"`).
    """
    kept: list[dict] = []
    seen: set[str] = set()
    for one in fields:
        if not isinstance(one, dict):
            raise FieldError(f"{name} declares a setting that is not a dict")
        key = str(one.get("key") or "").strip()
        if not key:
            raise FieldError(f"{name} declares a setting with no key")
        if key in seen:
            raise FieldError(f"{name} declares the setting {key!r} twice")
        if key in RESERVED:
            raise FieldError(f"{name} declares a setting named {key!r}, which core keeps for "
                             f"itself. Reserved: {', '.join(sorted(RESERVED))}")
        seen.add(key)
        kind = str(one.get("type") or "string")
        if kind not in SETTINGS_TYPES:
            raise FieldError(
                f"{name} declares {key!r} as {kind!r}, which a settings page does not draw."
                f" Drawn: {', '.join(sorted(SETTINGS_TYPES))}")
        default = one.get("default", "")
        if kind == "secret" and default:
            raise FieldError(f"{name} gives the secret {key!r} a default")
        if kind == "switch":
            default = "true" if default in (True, "true") else "false"
        choices = [[str(item[0]), str(item[1])] for item in one.get("choices") or []]
        if kind == "select":
            if not choices:
                raise FieldError(f"{name} declares the select {key!r} with no choices")
            offered = [value for value, _label in choices]
            default = str(default) if default not in ("", None) else offered[0]
            if default not in offered:
                raise FieldError(f"{name} defaults the select {key!r} to {default!r}, "
                                 f"which is not one of its choices: {', '.join(offered)}")
        extra: dict[str, Any] = {}
        if kind == "multi":
            if not choices:
                raise FieldError(f"{name} declares the multi {key!r} with no choices")
            offered = [value for value, _label in choices]
            if default not in ("", None) and listed(default) is None:
                raise FieldError(f"{name} defaults the multi {key!r} to {default!r}, "
                                 "which is not a list of its choices")
            chosen = ticked(default)
            unknown = [one for one in chosen if one not in offered]
            if unknown:
                raise FieldError(f"{name} defaults the multi {key!r} to {unknown[0]!r}, "
                                 f"which is not one of its choices: {', '.join(offered)}")
            default = json.dumps(chosen)
        elif kind == "number":
            extra = _bounds(name, key, one)
            if default not in ("", None) and number_text(default, extra) is None:
                raise FieldError(f"{name} defaults the number {key!r} to {default!r}, "
                                 "which is not a number inside its limits")
        elif kind == "path":
            wants = str(one.get("wants") or "dir")
            if wants not in PATH_KINDS:
                raise FieldError(f"{name} declares the path {key!r} as {wants!r}. "
                                 f"Known: {', '.join(PATH_KINDS)}")
            extra = {"wants": wants}
        kept.append({"key": key, "type": kind, "default": "" if default is None
                     else str(default), "choices": choices, **extra})
    return kept


def listed(value: Any) -> list[Any] | None:
    """A multi's value as a list, from a list or from the JSON text it is stored as; None
    where it is neither, so a malformed value is refused and not read as nothing ticked."""
    if isinstance(value, str):
        try:
            value = json.loads(value) if value.strip() else []
        except ValueError:
            return None
    return value if isinstance(value, list) else None


def ticked(value: Any) -> list[str]:
    """What a multi holds: the choices ticked, once each, in the order they were ticked.
    Anything that is not a list holds none."""
    seen: list[str] = []
    for one in listed(value) or []:
        if str(one) not in seen:
            seen.append(str(one))
    return seen


def _bounds(name: str, key: str, one: dict) -> dict[str, Any]:
    """A number's `min`, `max` and `unit`, where it declared them."""
    bounds: dict[str, Any] = {}
    for bound in ("min", "max"):
        if one.get(bound) not in (None, ""):
            try:
                bounds[bound] = float(one[bound])
            except (TypeError, ValueError) as exc:
                raise FieldError(f"{name} gives the number {key!r} a {bound} that is not "
                                 f"a number: {one[bound]!r}") from exc
    if one.get("unit"):
        bounds["unit"] = str(one["unit"])
    return bounds


def number_text(value: Any, bounds: dict[str, Any]) -> str | None:
    """A number setting's value as it is stored - a whole number without its `.0` - or
    None where it is not a finite number or is outside its limits."""
    if isinstance(value, str) and "_" in value:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    if number < bounds.get("min", number) or number > bounds.get("max", number):
        return None
    return str(int(number)) if number == int(number) else str(number)


def dialog_fields(name: str, fields: Iterable[Any]) -> list[dict]:
    """The fields a dialog's `ask` answered with, checked to be ones the wizard draws. A
    secret goes out with `set` in place of its `value`: it is drawn empty whatever the
    extension sent."""
    kept = []
    for one in fields:
        kind = str((one or {}).get("type") or "string")
        if kind == "secret":
            one = scrubbed(one)
        if kind not in DIALOG_TYPES:
            raise FieldError(
                f"{name} asks for {(one or {}).get('key')!r} as {kind!r}, which a dialog "
                f"does not draw. Drawn: {', '.join(sorted(DIALOG_TYPES))}")
        kept.append(one)
    return kept


def dialog_step(name: str, step: dict) -> None:
    """Refuse a step that carries a key the wizard does not read, which it would drop
    without a word, a note whose `to` is not a web address with words to link, and an
    error that names a `to`, which a field's error does not draw."""
    unknown = sorted(set(step) - STEP_KEYS)
    if unknown:
        raise FieldError(f"{name} answers a step with {', '.join(repr(one) for one in unknown)},"
                         f" which a dialog does not draw. Drawn: {', '.join(sorted(STEP_KEYS))}")
    notes = step.get("notes") or []
    if not isinstance(notes, list):
        raise FieldError(f"{name} answers a step with notes that are not a list")
    for note in notes:
        if isinstance(note, dict) and "to" in note and not (
                sites.is_web(note["to"]) and isinstance(note.get("text"), str)
                and note["text"].strip()):
            raise FieldError(f"{name} gives a note a 'to' of {note['to']!r}, which needs an "
                             "http or https address and a 'text' to show it as")
    errors = step.get("errors")
    for field, error in (errors.items() if isinstance(errors, dict) else ()):
        if isinstance(error, dict) and "to" in error:
            raise FieldError(f"{name} gives the error for {field!r} a 'to', which an error "
                             "does not draw")
