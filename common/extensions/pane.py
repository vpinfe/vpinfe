"""What a list's `detail(row_id)` or a panel group may answer, checked before the
Manager draws any of it.

The pane has a closed vocabulary, like a dialog's step: a key core does not read would be
dropped without a word, so it is refused where the extension can see it. A secret goes out
with `set` in place of its value, as it does everywhere else.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .accounts import scrubbed
from .context import ID_KEYS
from .fields import PATH_KINDS, SETTINGS_TYPES, number_text, ticked
from .pictures import is_openable

ANSWER_KEYS = frozenset({"title", "under", "picture", "sections"})
SECTION_KEYS = frozenset({"key", "label", "facts", "fields", "rows"})
FACT_KEYS = frozenset({"label", "value", "kind", "to", "help", "tier", "says", "keys",
                       "picture"})
FIELD_KEYS = frozenset({"key", "type", "label", "value", "help", "choices", "min", "max",
                        "unit", "wants", "placeholder"})
ROW_KEYS = frozenset({"label", "under"})
# A panel group answers the one section itself, with no title or sections around it.
GROUP_KEYS = frozenset({"facts", "fields", "rows"})
GROUP_SECTION = "group"

# The cell drawings of a list that a fact can use. A `game` or `table` fact is the id, drawn
# as one row of the name control.
FACT_KINDS = ("text", "number", "date", "state", "chips", "link", "picture", "game", "table")
LIBRARY_FACTS = ("game", "table")
TIERS = ("on", "off", "unknown", "warn", "bad")


class PaneError(ValueError):
    """The answer is not one the pane draws. `line` is the catalog line that says why."""

    def __init__(self, line: str, **params: Any) -> None:
        super().__init__(line)
        self.line = line
        self.params = params


def _dict(value: Any, what: str) -> dict:
    if not isinstance(value, dict):
        raise PaneError("error.extensions.pane_shape", what=what, need="a dict")
    return value


def _list(value: Any, what: str) -> list:
    if value is None:
        return []
    if not isinstance(value, list):
        raise PaneError("error.extensions.pane_shape", what=what, need="a list")
    return value


def _only(found: dict, allowed: frozenset[str], what: str) -> None:
    unknown = sorted(set(found) - allowed)
    if unknown:
        raise PaneError("error.extensions.pane_key", what=what, key=unknown[0],
                        drawn=", ".join(sorted(allowed)))


def _text(found: dict, key: str, what: str) -> str:
    value = found.get(key)
    if value is None:
        return ""
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        raise PaneError("error.extensions.pane_shape", what=f"{what} {key}", need="text")
    return str(value)


def _limit(value: Any, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise PaneError("error.extensions.pane_shape", what=what, need="a number")
    try:
        number = float(value)
    except ValueError:
        raise PaneError("error.extensions.pane_shape", what=what, need="a number") from None
    if number != number or number in (float("inf"), float("-inf")):
        raise PaneError("error.extensions.pane_shape", what=what, need="a finite number")
    return number


def _fact(one: Any, where: str) -> dict:
    fact = _dict(one, f"{where} fact")
    _only(fact, FACT_KEYS, f"{where} fact")
    kind = str(fact.get("kind") or "text")
    if kind not in FACT_KINDS:
        raise PaneError("error.extensions.pane_kind", what=f"{where} fact", kind=kind,
                        drawn=", ".join(FACT_KINDS))
    tier = str(fact.get("tier") or "")
    if tier and tier not in TIERS:
        raise PaneError("error.extensions.pane_tier", what=f"{where} fact", tier=tier,
                        drawn=", ".join(TIERS))
    if tier and kind != "state":
        raise PaneError("error.extensions.pane_looks", what=f"{where} fact", kind=kind)
    value = fact.get("value")
    if kind == "chips":
        value = [str(each) for each in _list(value, f"{where} fact value")]
    else:
        value = _text(fact, "value", f"{where} fact")
    if kind == "link" and not is_openable(str(fact.get("to") or "")):
        raise PaneError("error.extensions.pane_link", what=f"{where} fact")
    keys = str(fact.get("keys") or ID_KEYS[0])
    if kind in LIBRARY_FACTS:
        if keys not in ID_KEYS:
            raise PaneError("error.extensions.pane_shape", what=f"{where} fact keys",
                            need=f"one of {', '.join(ID_KEYS)}")
    elif any(fact.get(own) is not None for own in ("says", "keys", "picture")):
        raise PaneError("error.extensions.pane_own", what=f"{where} fact")
    return {"label": _text(fact, "label", f"{where} fact"), "value": value, "kind": kind,
            "to": _text(fact, "to", f"{where} fact"),
            "help": _text(fact, "help", f"{where} fact"),
            "tier": tier,
            "says": _text(fact, "says", f"{where} fact"),
            "keys": keys if kind in LIBRARY_FACTS else "",
            "picture": _text(fact, "picture", f"{where} fact")}


def _field(one: Any, where: str) -> dict:
    field = _dict(one, f"{where} field")
    _only(field, FIELD_KEYS, f"{where} field")
    key = str(field.get("key") or "").strip()
    if not key:
        raise PaneError("error.extensions.pane_shape", what=f"{where} field", need="a key")
    kind = str(field.get("type") or "string")
    if kind not in SETTINGS_TYPES:
        raise PaneError("error.extensions.pane_field", what=f"{where} field {key}", kind=kind,
                        drawn=", ".join(sorted(SETTINGS_TYPES)))
    out: dict[str, Any] = {"key": key, "type": kind,
                           "label": _text(field, "label", f"{where} field") or key,
                           "help": _text(field, "help", f"{where} field")}
    choices = []
    for each in _list(field.get("choices"), f"{where} field {key} choices"):
        if not isinstance(each, (list, tuple)) or len(each) != 2:
            raise PaneError("error.extensions.pane_shape", what=f"{where} field {key} choice",
                            need="a [value, label] pair")
        choices.append([str(each[0]), str(each[1])])
    if kind in ("select", "multi"):
        if not choices:
            raise PaneError("error.extensions.pane_choices", what=f"{where} field {key}",
                            kind=kind)
        out["choices"] = choices
    value = field.get("value")
    if kind == "secret":
        out = scrubbed({**out, "value": value})
    elif kind == "switch":
        out["value"] = value in (True, "true")
    elif kind == "multi":
        out["value"] = ticked(value)
    elif kind == "number":
        out["value"] = "" if value in (None, "") else str(value)
        for bound in ("min", "max"):
            if field.get(bound) not in (None, ""):
                out[bound] = _limit(field[bound], f"{where} field {key} {bound}")
        if field.get("unit"):
            out["unit"] = _text(field, "unit", f"{where} field {key}")
    else:
        out["value"] = "" if value is None else str(value)
        if kind == "path":
            out["wants"] = str(field.get("wants") or "dir")
            if out["wants"] not in PATH_KINDS:
                raise PaneError("error.extensions.pane_shape",
                                what=f"{where} field {key} wants", need=" or ".join(PATH_KINDS))
        out["placeholder"] = _text(field, "placeholder", f"{where} field")
    return out


def _row(one: Any, where: str) -> dict:
    row = _dict(one, f"{where} row")
    _only(row, ROW_KEYS, f"{where} row")
    return {"label": _text(row, "label", f"{where} row"),
            "under": _text(row, "under", f"{where} row")}


def _section(section: dict, key: str, label: str, names: set[str]) -> dict:
    """One section's facts, fields and rows, shaped. `names` holds the field keys the pane
    has so far, and takes this section's."""
    fields = [_field(each, f"section {key}") for each in _list(section.get("fields"), "fields")]
    for each in fields:
        if each["key"] in names:
            raise PaneError("error.extensions.pane_twice", what="field", key=each["key"])
        names.add(each["key"])
    return {
        "key": key, "label": label,
        "facts": [_fact(each, f"section {key}")
                  for each in _list(section.get("facts"), "facts")],
        "fields": fields,
        "rows": [_row(each, f"section {key}") for each in _list(section.get("rows"), "rows")],
    }


def shaped(answer: Any, row_id: str) -> dict:
    """`answer` as the pane draws it, or `PaneError` saying what is wrong with it."""
    found = _dict(answer, "the answer")
    _only(found, ANSWER_KEYS, "the answer")
    title = _text(found, "title", "the answer").strip()
    if not title:
        raise PaneError("error.extensions.pane_shape", what="the answer", need="a title")
    sections: list[dict] = []
    keys: set[str] = set()
    names: set[str] = set()
    declared = _list(found.get("sections"), "sections")
    for index, one in enumerate(declared):
        section = _dict(one, "a section")
        _only(section, SECTION_KEYS, "a section")
        key = str(section.get("key") or "").strip() or f"section{index + 1}"
        if key in keys:
            raise PaneError("error.extensions.pane_twice", what="section", key=key)
        keys.add(key)
        label = _text(section, "label", f"section {key}").strip()
        if len(declared) > 1 and not label:
            raise PaneError("error.extensions.pane_shape", what=f"section {key}",
                            need="a label, as it is one of several")
        sections.append(_section(section, key, label, names))
    return {"id": row_id, "title": title, "under": _text(found, "under", "the answer"),
            "picture": _text(found, "picture", "the answer"), "sections": sections}


def grouped(answer: Any) -> dict | None:
    """What a panel group answers, as the one section the panel draws, or None where it
    has nothing to say. `PaneError` says what is wrong with an answer that is not one."""
    if answer is None:
        return None
    found = _dict(answer, "the answer")
    _only(found, GROUP_KEYS, "the answer")
    section = _section(found, GROUP_SECTION, "", set())
    if not (section["facts"] or section["fields"] or section["rows"]):
        return None
    return section


def accepted(fields: list[dict], values: dict, refuse: Callable[..., Exception]
             ) -> dict[str, Any]:
    """`values` checked against the `fields` the pane offered now: each key must be one of
    them, each value of its type. `refuse(key, **params)` builds the error to raise.
    Returns what is to be saved."""
    offered = {one["key"]: one for one in fields}
    out: dict[str, Any] = {}
    for key, value in (values or {}).items():
        one = offered.get(key)
        if one is None:
            raise refuse("error.extensions.no_field", key=key)
        kind = one["type"]
        if kind == "secret":
            if value is None or str(value).strip():
                out[key] = None if value is None else str(value)
            continue
        if kind == "switch":
            out[key] = value in (True, "true")
        elif kind == "select":
            if str(value) not in {choice[0] for choice in one["choices"]}:
                raise refuse("error.extensions.bad_choice", key=key, value=str(value))
            out[key] = str(value)
        elif kind == "multi":
            if not isinstance(value, list):
                raise refuse("error.extensions.bad_choice", key=key, value=str(value))
            offered_values = {choice[0] for choice in one["choices"]}
            for each in ticked(value):
                if each not in offered_values:
                    raise refuse("error.extensions.bad_choice", key=key, value=each)
            out[key] = ticked(value)
        elif kind == "number":
            if value in (None, ""):
                out[key] = None
                continue
            counted = number_text(value, one)
            if counted is None:
                raise refuse("error.extensions.bad_number", key=key, value=str(value))
            out[key] = int(float(counted)) if float(counted).is_integer() else float(counted)
        else:
            out[key] = "" if value is None else str(value)
    return out
