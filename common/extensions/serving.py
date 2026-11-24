"""What core answers when the Manager asks for something an extension declared.

An extension hands core fields, rows and functions; the routes are core's. Each handler
here reads the declaration of one running extension, calls the function it named, and
answers in the shape the Manager already reads. Nothing in this module imports a web
framework: the router that carries these over HTTP is `httpapi/extensions_served.py`.

A function the extension supplied that raises is the extension's refusal or its failure,
never core's fault: it becomes an `ExtensionError` carrying the line `ctx.why` words, and
the API answers with that line without disabling the extension. A request core can tell
is wrong before it calls anything raises `NotFoundError` or `RefusedError` instead.
"""

from __future__ import annotations

import inspect
import json
import logging
import threading
from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any

from common import failures, i18n
from common.media import sized_media
from common.online import sources
from common.service_errors import NotFoundError, RefusedError, ServiceError

from . import contract, pane, pictures, reach
from .fields import FieldError, dialog_fields, dialog_step, listed, number_text, ticked

if TYPE_CHECKING:
    from .host import Record


class ExtensionError(ServiceError):
    """A function an extension supplied raised. `unreachable` says the service it was
    reaching did not answer, and `field` names the setting a refusal belongs under."""

    def __init__(self, message: str, *, unreachable: bool = False, field: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.unreachable = unreachable
        self.field = field


def said(record: Record, suffix: str, fallback: str = "", **params: Any) -> str:
    """What the extension says for `suffix` in its own catalog, else `fallback`."""
    found = i18n.first_key(f"ext.{record.name}.{suffix}")
    return i18n.t(found, **params) if found else fallback


def called(record: Record, at: str, function: Callable[..., Any], *args: Any,
           **kwargs: Any) -> Any:
    """Call something the extension supplied, turning whatever it raises into the line a
    person reads. An error raised `from` a failure to reach a service counts as one."""
    try:
        return function(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001 - an extension's raise is its answer
        line = contract.why(exc, at)
        logging.getLogger(f"vpinfe.ext.{record.name}").warning(
            "%s failed: %s", at, line, exc_info=True)
        raise ExtensionError(line, unreachable=_unreachable(exc)) from exc


def _unreachable(exc: BaseException | None) -> bool:
    seen: set[int] = set()
    while exc is not None and id(exc) not in seen:
        if isinstance(exc, contract.Unavailable) or failures.upstream(exc):
            return True
        seen.add(id(exc))
        exc = exc.__cause__
    return False


def plain(record: Record, at: str, value: Any) -> Any:
    """`value` as JSON would carry it, so a row core cannot encode is the extension's
    failure to answer and not a server error that gets the extension disabled."""
    try:
        return json.loads(json.dumps(value))
    except (TypeError, ValueError, RecursionError) as exc:
        raise ExtensionError(i18n.t("error.extensions.not_data", display=record.display_name,
                                     at=at)) from exc


def _extras(function: Callable[..., Any], **offered: Any) -> dict[str, Any]:
    """The keyword arguments `function` names among those on offer."""
    try:
        named = inspect.signature(function).parameters
    except (TypeError, ValueError):
        return {}
    return {key: value for key, value in offered.items() if key in named}


def _context(record: Record) -> Any:
    if record.context is None:
        raise NotFoundError(i18n.t("error.extensions.not_running",
                                   display=record.display_name, value=record.reason))
    return record.context


# -- tiers -----------------------------------------------------------------------------

# Called with the tier a change is about to be made at, before the extension's function is
# called, and raises where the caller may not reach it.
Need = Callable[[str], None]


def _need(record: Record, need: Need | None, function: Any, base: str, surface: str) -> None:
    """Hold the caller to the tier `function` runs at: the one it was marked with, else
    `base`, its surface's. A tier the manifest does not list is refused, whoever asks.
    `surface` is the name the manifest's `play` uses for it, which an unlisted act is
    refused by."""
    tier = contract.tier_of(function, base)
    if tier not in _context(record).reaches:
        raise RefusedError(i18n.t("error.extensions.tier_not_listed",
                                  display=record.display_name, tier=tier))
    if tier == contract.PLAY and base != contract.PLAY:
        raise RefusedError(i18n.t("error.extensions.play_act_unlisted",
                                  display=record.display_name, surface=surface))
    if need is not None:
        need(tier)
    reach.answer_at(tier)


def surface_tier(record: Record, kind: str, key: str) -> str:
    """The tier a list, section, group (by subject), dialog or library entry sits at,
    which is what reading it needs."""
    if kind == "list":
        return str(_list(record, key).tier)
    if kind == "section":
        return str(_section(record, key).tier)
    if kind == "group":
        return str(_group(record, key).tier)
    if kind == "action":
        _dialog(record, key)
        return str(next(one["tier"] for one in _context(record).ui.actions
                        if one["key"] == key))
    raise NotFoundError(i18n.t("error.extensions.no_action", display=record.display_name,
                               key=key))


def reaches_play(record: Record) -> bool:
    """Whether any of the extension's routes may be reached with a Play token or a Remote
    pass."""
    return contract.PLAY in _context(record).reaches


# -- settings --------------------------------------------------------------------------


def _held(record: Record) -> dict[str, str]:
    return _context(record).config.all()


def _setting(record: Record, one: dict, held: dict[str, str]) -> dict:
    key, kind, default = one["key"], one["type"], one["default"]
    stored = held.get(key) or ""
    out: dict[str, Any] = {
        "key": key, "type": kind, "label": said(record, f"settings.{key}.label", key),
        "help": said(record, f"settings.{key}.help", default=default)}
    if kind == "secret":
        out["set"] = bool(stored)
    elif kind == "switch":
        out["value"] = (stored or default) == "true"
    elif kind in ("select", "multi"):
        out["value"] = (stored or default) if kind == "select" else ticked(stored or default)
        out["choices"] = [[value, said(record, f"settings.{key}.choice.{value}", label)]
                          for value, label in one["choices"]]
    elif kind == "number":
        out["value"] = stored or default
        out.update({bound: one[bound] for bound in ("min", "max", "unit") if bound in one})
    else:
        out["value"] = stored
        out["placeholder"] = default
        if kind == "path":
            out["wants"] = one["wants"]
    return out


def settings_read(record: Record) -> dict:
    held = _held(record)
    return {"fields": [_setting(record, one, held)
                       for one in _context(record).ui.settings_declared]}


def settings_write(record: Record, values: dict) -> dict:
    context = _context(record)
    declared = {one["key"]: one for one in context.ui.settings_declared}
    changes: dict[str, str] = {}
    for key, value in (values or {}).items():
        one = declared.get(key)
        if one is None:
            raise RefusedError(i18n.t("error.extensions.no_setting", key=key))
        kind = one["type"]
        text = "" if value is None else str(value)
        if kind == "secret" and value is not None and not text.strip():
            continue
        if kind == "switch":
            text = "true" if value in (True, "true") else "false"
        offered = {choice[0] for choice in one["choices"]}
        if kind == "select" and text not in offered:
            raise RefusedError(i18n.t("error.extensions.bad_choice", key=key, value=text))
        if kind == "multi":
            chosen = ticked(value)
            if value not in ("", None) and listed(value) is None:
                raise RefusedError(i18n.t("error.extensions.bad_choice", key=key,
                                          value=text))
            for each in chosen:
                if each not in offered:
                    raise RefusedError(i18n.t("error.extensions.bad_choice", key=key,
                                              value=each))
            text = json.dumps(chosen)
        if kind == "number" and text.strip():
            counted = number_text(text, one)
            if counted is None:
                raise RefusedError(i18n.t("error.extensions.bad_number", key=key, value=text))
            text = counted
        changes[key] = text
    check = context.ui.settings_check
    if check is not None and changes:
        held = _held(record)
        after = {key: changes.get(key, held.get(key, "")) or one["default"]
                 for key, one in declared.items()}
        try:
            called(record, "settings", check, after)
        except ExtensionError as exc:
            exc.field = next(iter(changes))
            raise
    for key, text in changes.items():
        context.config.set(key, text)
    return settings_read(record)


# -- lists -----------------------------------------------------------------------------


def _list(record: Record, key: str) -> Any:
    found = _context(record).ui.list_functions.get(key)
    if found is None:
        raise NotFoundError(i18n.t("error.extensions.no_list", display=record.display_name,
                                   key=key))
    return found


def list_rows(record: Record, key: str, *, addressed: bool = True) -> dict:
    functions = _list(record, key)
    rows = plain(record, f"list {key}", called(record, f"list {key}", functions.rows))
    if not isinstance(rows, list) or not all(isinstance(one, dict) for one in rows):
        raise ExtensionError(i18n.t("error.extensions.not_rows",
                                     display=record.display_name, key=key))
    seen: set[str] = set()
    for row in rows:
        if row.get("id") in (None, ""):
            row.pop("id", None)
            continue
        row["id"] = str(row["id"])
        if row["id"] in seen:
            raise ExtensionError(i18n.t("error.extensions.duplicate_row_id",
                                         display=record.display_name, key=key, id=row["id"]))
        seen.add(row["id"])
    if addressed:
        _addressed(record, key, rows)
    return {"rows": rows}


def _addressed_facts(record: Record, facts: list[dict]) -> None:
    """Turn each picture a fact names into the address core serves it from. Raises
    `OutboundNotDeclaredError` for a web address the extension did not declare."""
    for fact in facts:
        if fact["kind"] == "picture":
            fact["value"] = pictures.address(record, fact["value"])
        elif fact["picture"]:
            fact["picture"] = pictures.address(record, fact["picture"])


def _addressed(record: Record, key: str, rows: list[dict]) -> None:
    declared = next((one for one in record.declared_lists if one["key"] == key), {})
    fields = list(dict.fromkeys(
        column["field"] if column["kind"] == "picture" else column.get("picture")
        for column in declared.get("columns", [])
        if column["kind"] == "picture" or column.get("picture")))
    for field in fields:
        for row in rows:
            try:
                row[field] = pictures.address(record, row.get(field))
            except pictures.OutboundNotDeclaredError as exc:
                raise ExtensionError(i18n.t(
                    "error.extensions.picture_outbound",
                    display=record.display_name, key=f"list {key}")) from exc


def picture(record: Record, picture_id: str) -> sized_media.Served:
    return pictures.served(record, picture_id)


def _as_acts(record: Record, at: str, acts: Any) -> dict[str, Any]:
    """The acts an extension answered, or its failure to answer a dict."""
    if acts is not None and not isinstance(acts, dict):
        raise ExtensionError(i18n.t("error.extensions.not_data", display=record.display_name,
                                    at=at))
    return dict(acts or {})


def _acts(record: Record, kind: str, key: str, functions: Any) -> dict[str, Any]:
    acts = functions.acts
    if callable(acts):
        acts = called(record, f"{kind} {key} acts", acts)
    return _as_acts(record, f"{kind} {key} acts", acts)


def _menu(record: Record, kind: str, key: str, functions: Any) -> list[dict]:
    """The title menu of a list or a page: each act with its label and, where it opens
    something, what."""
    offered = []
    for act, what in _acts(record, kind, key, functions).items():
        label = said(record, f"{kind}.{key}.act.{act}.label", act)
        opens = what.get("action") if isinstance(what, dict) else None
        if opens is not None and opens not in _context(record).ui.action_objects:
            raise ExtensionError(i18n.t("error.extensions.no_action",
                                         display=record.display_name, key=opens))
        offered.append({"key": act, "label": label,
                        **({"url": str(what)} if isinstance(what, str) else {}),
                        **({"action": str(opens)} if opens is not None else {})})
    return offered


def _act(record: Record, kind: str, key: str, functions: Any, act: str,
         need: Need | None = None) -> dict:
    what = _acts(record, kind, key, functions).get(act)
    if what is None or isinstance(what, (str, dict)):
        raise NotFoundError(i18n.t("error.extensions.no_act", display=record.display_name,
                                   key=act))
    _need(record, need, what, functions.tier, f"{kind}:{key}")
    return _message(record, f"{kind} {key} act {act}",
                    called(record, f"{kind} {key} act {act}", what))


def list_about(record: Record, key: str) -> dict:
    functions = _list(record, key)
    status = ""
    if functions.status is not None:
        status = plain(record, f"list {key} status",
                       called(record, f"list {key} status", functions.status)) or ""
    return {"status": status, "acts": _menu(record, "list", key, functions)}


def list_act(record: Record, key: str, act: str, need: Need | None = None) -> dict:
    return _act(record, "list", key, _list(record, key), act, need)


def list_verb(record: Record, key: str, verb: str, ids: list[str],
              need: Need | None = None) -> dict:
    """Run one of a list's verbs on the rows with these ids, as the list holds them now.
    The rows a verb's `needs` field is false on are left out, and a verb with none left
    refuses."""
    functions = _list(record, key)
    chosen = functions.verbs.get(verb)
    if chosen is None or chosen["run"] is None:
        raise NotFoundError(i18n.t("error.extensions.no_verb", display=record.display_name,
                                   key=verb))
    _need(record, need, chosen["run"], functions.tier, f"list:{key}")
    wanted = list(dict.fromkeys(ids))
    if not wanted or (chosen["one"] and len(wanted) != 1):
        raise RefusedError(i18n.t("error.extensions.verb_rows", display=record.display_name,
                                  key=verb))
    held = {row["id"]: row for row in list_rows(record, key, addressed=False)["rows"]
            if "id" in row}
    gone = [one for one in wanted if one not in held]
    if gone:
        raise NotFoundError(i18n.t("error.extensions.no_row", display=record.display_name,
                                   id=gone[0]))
    rows = [held[one] for one in wanted if not chosen["needs"] or held[one].get(chosen["needs"])]
    if not rows:
        raise RefusedError(said(record, f"list.{key}.verb.{verb}.needs",
                                i18n.t("error.extensions.verb_needs",
                                       display=record.display_name, key=verb)))
    at = f"list {key} verb {verb}"
    return _message(record, at, called(record, at, chosen["run"], rows))


def _detail(record: Record, key: str) -> Any:
    found = _list(record, key).detail
    if found is None:
        raise NotFoundError(i18n.t("error.extensions.no_detail", display=record.display_name,
                                   key=key))
    return found


def list_detail(record: Record, key: str, row_id: str) -> dict:
    """What the pane shows for a row: the extension's `detail(row_id)`, checked, with each
    picture turned into core's own address and each secret held back."""
    at = f"list {key} detail"
    answer = plain(record, at, called(record, at, _detail(record, key), row_id))
    if answer is None:
        raise NotFoundError(i18n.t("error.extensions.no_row", display=record.display_name,
                                   id=row_id))
    try:
        shown = pane.shaped(answer, row_id)
    except pane.PaneError as exc:
        raise ExtensionError(i18n.t(exc.line, display=record.display_name, id=row_id,
                                    **exc.params)) from exc
    try:
        if shown["picture"]:
            shown["picture"] = pictures.address(record, shown["picture"])
        for section in shown["sections"]:
            _addressed_facts(record, section["facts"])
    except pictures.OutboundNotDeclaredError as exc:
        raise ExtensionError(i18n.t("error.extensions.picture_outbound",
                                    display=record.display_name, key=f"list {key}")) from exc
    return shown


def list_save(record: Record, key: str, row_id: str, values: dict,
              need: Need | None = None) -> dict:
    """Write the fields a pane offers for a row, then answer the pane as it stands. The
    keys must be fields the extension's own answer offers now."""
    functions = _list(record, key)
    if functions.save is None:
        raise NotFoundError(i18n.t("error.extensions.no_save", display=record.display_name,
                                   key=key))
    _need(record, need, functions.save, functions.tier, f"list:{key}")
    offered = [one for section in list_detail(record, key, row_id)["sections"]
               for one in section["fields"]]
    changes = pane.accepted(
        offered, values or {},
        lambda line, **params: RefusedError(i18n.t(line, display=record.display_name,
                                                   **params)))
    if changes:
        try:
            called(record, f"list {key} save", functions.save, row_id, changes)
        except ExtensionError as exc:
            exc.field = next(iter(changes))
            raise
    return list_detail(record, key, row_id)


# -- sections --------------------------------------------------------------------------


def _section(record: Record, key: str) -> Any:
    found = _context(record).ui.section_functions.get(key)
    if found is None:
        raise NotFoundError(i18n.t("error.extensions.no_section", display=record.display_name,
                                   key=key))
    return found


def section_read(record: Record, key: str) -> dict:
    """A section of the extension's panel as the Manager draws it: the extension's
    `read()`, checked, with each picture turned into core's own address and each secret
    held back. `section` is None where it has nothing to say."""
    at = f"section {key}"
    answer = plain(record, at, called(record, at, _section(record, key).read))
    try:
        shown = pane.grouped(answer)
    except pane.PaneError as exc:
        raise ExtensionError(i18n.t(exc.line, display=record.display_name,
                                    id=f"its section {key}", **exc.params)) from exc
    try:
        _addressed_facts(record, (shown or {}).get("facts", []))
    except pictures.OutboundNotDeclaredError as exc:
        raise ExtensionError(i18n.t("error.extensions.picture_outbound",
                                    display=record.display_name, key=at)) from exc
    return {"key": key, "section": shown}


def section_save(record: Record, key: str, values: dict, need: Need | None = None) -> dict:
    """Write the fields a section offers, then answer the section as it stands. The keys
    must be fields the extension's own answer offers now."""
    functions = _section(record, key)
    if functions.save is None:
        raise NotFoundError(i18n.t("error.extensions.no_section_save",
                                   display=record.display_name, key=key))
    _need(record, need, functions.save, functions.tier, f"section:{key}")
    offered = (section_read(record, key)["section"] or {}).get("fields", [])
    changes = pane.accepted(
        offered, values or {},
        lambda line, **params: RefusedError(i18n.t(line, display=record.display_name,
                                                   **params)))
    if changes:
        try:
            called(record, f"section {key} save", functions.save, changes)
        except ExtensionError as exc:
            exc.field = next(iter(changes))
            raise
    return section_read(record, key)


# -- panel groups ----------------------------------------------------------------------

def _group(record: Record, subject: str) -> Any:
    found = _context(record).ui.group_functions.get(subject)
    if found is None:
        raise NotFoundError(i18n.t("error.extensions.no_group", display=record.display_name,
                                   subject=subject))
    return found


def group_keeps_private(record: Record, subject: str) -> bool:
    """Whether the group on `subject` was declared to work on Private games."""
    return bool(_group(record, subject).private_ok)


def declared_library_verb(record: Record, subject: str, key: str) -> dict:
    """What the extension declared for its entry `key` in the `subject` menu."""
    ui = _context(record).ui
    declared = next((one for one in ui.declared_library_verbs
                     if (one["subject"], one["key"]) == (subject, key)), None)
    if declared is None or (subject, key) not in ui.library_verb_functions:
        raise NotFoundError(i18n.t("error.extensions.no_verb", display=record.display_name,
                                   key=key))
    return declared


def library_verb(record: Record, subject: str, key: str, who: list[dict],
                 need: Need | None = None) -> dict:
    """Run one of the extension's entries in the Games or Tables menu on `who`, the games or
    tables core chose to tell it about. Refuses where there are none, which is every one a
    Private game unless the entry is `private_ok`, or several for a verb that takes one."""
    declared = declared_library_verb(record, subject, key)
    run = _context(record).ui.library_verb_functions[(subject, key)]
    _need(record, need, run, declared["tier"], f"verb:{subject}:{key}")
    if not who:
        raise RefusedError(i18n.t("error.extensions.verb_none", display=record.display_name,
                                  key=key))
    if declared["one"] and len(who) != 1:
        raise RefusedError(i18n.t("error.extensions.verb_rows", display=record.display_name,
                                  key=key))
    at = f"{subject} verb {key}"
    return _message(record, at, called(record, at, run, [dict(one) for one in who]))


def group_read(record: Record, subject: str, who: dict) -> dict:
    """The group on a game's or table's panel: its heading, and the one section the
    extension answered with, or None where it has nothing to say. `who` is the subject core
    chose to ask about."""
    functions = _group(record, subject)
    at = f"{subject} group"
    answer = plain(record, at, called(record, at, functions.read, dict(who)))
    try:
        section = pane.grouped(answer)
    except pane.PaneError as exc:
        raise ExtensionError(i18n.t(exc.line, display=record.display_name,
                                    id=f"its {subject} group", **exc.params)) from exc
    try:
        _addressed_facts(record, (section or {}).get("facts", []))
    except pictures.OutboundNotDeclaredError as exc:
        raise ExtensionError(i18n.t("error.extensions.picture_outbound",
                                    display=record.display_name, key=at)) from exc
    title = next((one["title"] for one in record.groups() if one["subject"] == subject), "")
    return {"title": title, "section": section,
            "acts": _group_menu(record, subject, functions, who) if section else []}


def _group_acts(record: Record, subject: str, functions: Any, who: dict) -> dict[str, Any]:
    acts = functions.acts
    if callable(acts):
        acts = called(record, f"{subject} group acts", acts, dict(who))
    return _as_acts(record, f"{subject} group acts", acts)


def _group_menu(record: Record, subject: str, functions: Any, who: dict) -> list[dict]:
    """The strip closing a group: each act with its label, and its address where it opens
    one."""
    return [{"key": act, "label": said(record, f"group.{subject}.act.{act}.label", act),
             **({"url": str(what)} if isinstance(what, str) else {})}
            for act, what in _group_acts(record, subject, functions, who).items()]


def group_act(record: Record, subject: str, who: dict, act: str,
              need: Need | None = None) -> dict:
    """Do one of a group's acts for `who`, and answer what to say."""
    functions = _group(record, subject)
    what = _group_acts(record, subject, functions, who).get(act)
    if what is None or isinstance(what, (str, dict)):
        raise NotFoundError(i18n.t("error.extensions.no_act", display=record.display_name,
                                   key=act))
    _need(record, need, what, functions.tier, f"group:{subject}")
    return _message(record, f"{subject} group act {act}",
                    called(record, f"{subject} group act {act}", what, dict(who)))


def group_save(record: Record, subject: str, who: dict, values: dict,
               need: Need | None = None) -> dict:
    """Write the fields a group offers for a subject, then answer the group as it stands.
    The keys must be fields the extension's own answer offers now."""
    functions = _group(record, subject)
    if functions.save is None:
        raise NotFoundError(i18n.t("error.extensions.no_group_save",
                                   display=record.display_name, subject=subject))
    _need(record, need, functions.save, functions.tier, f"group:{subject}")
    offered = (group_read(record, subject, who)["section"] or {}).get("fields", [])
    changes = pane.accepted(
        offered, values or {},
        lambda line, **params: RefusedError(i18n.t(line, display=record.display_name,
                                                   **params)))
    if changes:
        try:
            called(record, f"{subject} group save", functions.save, dict(who), changes)
        except ExtensionError as exc:
            exc.field = next(iter(changes))
            raise
    return group_read(record, subject, who)


def needs_read(record: Record) -> list[dict]:
    """What this extension asks for on Overview now, one row for each declared kind that
    holds, worded in the language set. A kind holds when it names ids or a count above zero.
    `covered` is what a dismissal of the row is kept against: the ids, else the token the
    extension gave, else the kind."""
    functions = _context(record).ui
    if functions.needs_function is None:
        return []
    answer = plain(record, "needs", called(record, "needs", functions.needs_function))
    if not isinstance(answer, list):
        raise ExtensionError(i18n.t("error.extensions.needs_shape", display=record.display_name))
    declared = {one["kind"]: one for one in record.declared_needs}
    out: list[dict] = []
    for one in answer:
        kind = one.get("kind") if isinstance(one, dict) else None
        if not isinstance(kind, str) or kind not in declared:
            raise ExtensionError(i18n.t("error.extensions.need_unknown",
                                        display=record.display_name, kind=str(kind)))
        try:
            ids = [str(each) for each in one.get("ids") or [] if str(each)]
            count = len(ids) or int(one.get("count") or 0)
            given = one.get("token")
            token = [str(each) for each in (given if isinstance(given, list) else [given])
                     if each is not None and str(each)]
        except (TypeError, ValueError, OverflowError) as exc:
            raise ExtensionError(i18n.t("error.extensions.needs_shape",
                                        display=record.display_name)) from exc
        if any(row["kind"] == kind for row in out):
            raise ExtensionError(i18n.t("error.extensions.needs_shape",
                                        display=record.display_name))
        if count <= 0:
            continue
        found = declared[kind]
        out.append({
            "extension": record.name, "display_name": record.display_name, "kind": kind,
            "key": f"ext:{record.name}:{kind}",
            "label": said(record, f"need.{kind}.label", record.display_name, count=count),
            "says": said(record, f"need.{kind}.says", "", count=count),
            "tone": found["tone"], "opens": found["opens"], "to": found["to"],
            "count": count, "covered": ids or token or [str(kind)]})
    return out


def needs_all(records: Sequence[Record]) -> dict:
    """The needs of every running extension that declared some. `away` names the extensions
    that gave none this time - not running, or failing to answer - so the dismissals kept
    against them are left alone; `failed` is the ones that raised."""
    rows: list[dict] = []
    away: list[str] = []
    failed: list[str] = []
    for record in records:
        if not record.running:
            away.append(record.name)
            continue
        if not record.declared_needs:
            continue
        try:
            rows += needs_read(record)
        except ExtensionError:
            away.append(record.name)
            failed.append(record.name)
    return {"needs": rows, "away": away, "failed": failed}


def _message(record: Record, at: str, result: Any) -> dict:
    if result is None:
        return {}
    if isinstance(result, str):
        return {"message": result}
    return plain(record, at, result)


# -- dialogs ---------------------------------------------------------------------------


class Progress:
    """How far a dialog's `run` has got, for the page that waits on it."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stage = ""
        self._message = ""
        self._done = 0
        self._total = 0

    def stage(self, name: str, message: str = "", total: int = 0) -> None:
        with self._lock:
            self._stage, self._message, self._done, self._total = name, message, 0, total

    def advance(self, done: int | None = None, message: str | None = None) -> None:
        with self._lock:
            self._done = self._done + 1 if done is None else done
            if message is not None:
                self._message = message

    def clear(self) -> None:
        self.stage("")

    def snapshot(self) -> dict:
        with self._lock:
            return {"stage": self._stage, "message": self._message, "done": self._done,
                    "total": self._total}


_progress: dict[tuple[str, str], Progress] = {}
_progress_lock = threading.Lock()


def _tracker(record: Record, key: str) -> Progress:
    with _progress_lock:
        return _progress.setdefault((record.name, key), Progress())


def _dialog(record: Record, key: str) -> Any:
    found = _context(record).ui.action_objects.get(key)
    if found is None:
        raise NotFoundError(i18n.t("error.extensions.no_action", display=record.display_name,
                                   key=key))
    return found


GAME_KEYS = ("game_id", "vps_id", "name")
WHO_KEYS = (*GAME_KEYS, "table_id")


def _titled(record: Record, key: str, answer: Any) -> dict:
    """A step answer with its fields checked, and the action's own label as the title it
    did not give."""
    if not isinstance(answer, dict):
        raise ExtensionError(i18n.t("error.extensions.bad_answer",
                                     display=record.display_name, key=key))
    try:
        dialog_step(record.name, answer)
        dialog_fields(record.name, answer.get("fields") or [])
    except FieldError as exc:
        raise ExtensionError(str(exc)) from exc
    if any(isinstance(one, dict) and one.get("key") == "rows"
           for one in answer.get("fields") or []):
        raise ExtensionError(i18n.t("error.extensions.rows_field", display=record.display_name))
    if not answer.get("title"):
        declared: dict = next((one for one in _context(record).ui.actions
                               if one["key"] == key), {})
        answer = {**answer, "title": said(record, f"action.{key}.label",
                                          declared.get("label") or key)}
    return answer


def _chosen(values: dict | None) -> list[str]:
    """The ids of the rows a dialog was opened over, which travel in its values."""
    rows = (values or {}).get("rows")
    return [str(one) for one in rows] if isinstance(rows, list) else []


def _subject(sent: Any) -> dict[str, str]:
    """Who a dialog was opened for, with only the ids an extension can use; `{}` where
    it was opened for no game."""
    if not isinstance(sent, dict):
        return {}
    who = {key: found if isinstance(found := sent.get(key), str) else "" for key in WHO_KEYS}
    return who if any(who[key] for key in GAME_KEYS) else {}


def dialog_ask(record: Record, key: str, step: str = "", rows: Sequence[str] = (),
               who: Mapping[str, Any] | None = None) -> dict:
    """The step to draw. A dialog with no `check` has no step after this one, so the step is
    the last and its button is the title. `rows` are the ids of the rows it was opened
    over and `who` the game it was opened for, each handed to an `ask` that names it. A
    later call is handed `who` the same way, beside its values."""
    dialog = _dialog(record, key)
    answer = _titled(record, key, plain(record, f"action {key}", called(
        record, f"action {key}", dialog.ask, step,
        **_extras(dialog.ask, rows=list(rows), who=_subject(who)))))
    if not hasattr(dialog, "check") and not (answer.get("summary") or answer.get("confirm")):
        answer = {**answer, "confirm": answer["title"]}
    return answer


def dialog_check(record: Record, key: str, values: dict, step: str = "",
                 need: Need | None = None, who: Mapping[str, Any] | None = None) -> dict:
    check = getattr(_dialog(record, key), "check", None)
    if check is None:
        return dialog_ask(record, key, step, _chosen(values), who)
    _need(record, need, check, surface_tier(record, "action", key), f"action:{key}")
    answer = called(record, f"action {key} check", check, values or {},
                    **_extras(check, step=step, who=_subject(who)))
    if answer is None:
        return dialog_ask(record, key, step, _chosen(values), who)
    return _titled(record, key, plain(record, f"action {key} check", answer))


def dialog_act(record: Record, key: str, act: str, values: dict, step: str = "",
               need: Need | None = None, who: Mapping[str, Any] | None = None) -> dict:
    do = getattr(_dialog(record, key), "act", None)
    if do is None:
        raise NotFoundError(i18n.t("error.extensions.no_act", display=record.display_name,
                                   key=act))
    _need(record, need, do, surface_tier(record, "action", key), f"action:{key}")
    answer = called(record, f"action {key} act {act}", do, act, values or {},
                    **_extras(do, step=step, who=_subject(who)))
    if answer is None:
        return dialog_ask(record, key, step, _chosen(values), who)
    return _titled(record, key, plain(record, f"action {key} act {act}", answer))


def dialog_run(record: Record, key: str, values: dict, step: str = "",
               need: Need | None = None, who: Mapping[str, Any] | None = None) -> dict:
    run = getattr(_dialog(record, key), "run", None)
    if run is None:
        raise NotFoundError(i18n.t("error.extensions.no_run", display=record.display_name,
                                   key=key))
    _need(record, need, run, surface_tier(record, "action", key), f"action:{key}")
    tracker = _tracker(record, key)
    try:
        answer = called(record, f"action {key} run", run, values or {},
                        **_extras(run, step=step, progress=tracker, who=_subject(who)))
    finally:
        tracker.clear()
    return _message(record, f"action {key} run", answer)


def dialog_progress(record: Record, key: str) -> dict:
    own = getattr(_dialog(record, key), "progress", None)
    if own is None:
        return _tracker(record, key).snapshot()
    return plain(record, f"action {key} progress",
                 called(record, f"action {key} progress", own) or {})


def job_stop(record: Record, kind: str) -> dict:
    """Ask the extension's running job of that kind to end after the unit in hand."""
    return {"stopped": _context(record).jobs.stop(kind)}


# -- online sources --------------------------------------------------------------------

SOURCE_ANSWER_KEYS = frozenset({"rows", "note", "link"})
SOURCE_ROW_KEYS = frozenset({"key", "name", "site", "page", "note", "picture", "tag", "current",
                             "vps_file_id", "state", "acts"})
SOURCE_LINK_KEYS = frozenset({"text", "to", "section", "settings"})
SOURCE_TAKE_KEYS = frozenset({"job", "action", "message"})
SOURCE_STATE_KEYS = frozenset({"text", "level", "hint"})
SOURCE_STATE_LEVELS = ("on", "off", "unknown", "warn", "bad")
SOURCE_ACT_KEYS = frozenset({"key", "label"})
SOURCE_LOOK_KEYS = frozenset({"pictures", "description", "files", "note"})
SOURCE_FILE_KEYS = frozenset({"name", "size"})
# What a row's page may show in an opened row, so a page with hundreds of entries stays a glance.
LOOK_PICTURES = 8
LOOK_FILES = 100
LOOK_DESCRIPTION = 4000
LOOK_SIZE = 1 << 63


def _source(record: Record, key: str, kind: str) -> sources.Source:
    found = sources.named(record.name, key)
    if found is None:
        raise NotFoundError(i18n.t("error.extensions.no_source", display=record.display_name,
                                   key=key))
    if kind not in found.kinds:
        raise NotFoundError(i18n.t("error.extensions.source_kind", display=record.display_name,
                                   key=key, kind=kind))
    if not sources.enabled(found):
        raise RefusedError(i18n.t("error.extensions.source_off", display=record.display_name,
                                  key=key))
    return found


def _shape(record: Record, at: str, what: str, need: str) -> ExtensionError:
    return ExtensionError(i18n.t("error.extensions.source_shape", display=record.display_name,
                                 at=at, what=what, need=need))


def _source_text(record: Record, at: str, one: dict, field: str, *,
                 required: bool = False) -> str:
    value = one.get(field)
    if value is not None and not isinstance(value, str):
        raise _shape(record, at, f"a row's {field}", "text")
    shown = (value or "").strip()
    if required and not shown:
        raise _shape(record, at, f"a row with no {field}", f"one with a {field}")
    return shown


def _source_row(record: Record, at: str, one: Any, seen: set[str]) -> dict:
    if not isinstance(one, dict):
        raise _shape(record, at, "a row", "an object")
    unknown = sorted(set(one) - SOURCE_ROW_KEYS)
    if unknown:
        raise _shape(record, at, f"a row with {', '.join(repr(each) for each in unknown)}",
                     f"only {', '.join(sorted(SOURCE_ROW_KEYS))}")
    row: dict[str, Any] = {
        field: _source_text(record, at, one, field, required=field in ("key", "name"))
        for field in ("key", "name", "site", "note", "tag")}
    if row["key"] in seen:
        raise _shape(record, at, f"two rows with the key {row['key']}", "each once")
    seen.add(row["key"])
    row["page"] = _source_text(record, at, one, "page")
    if row["page"] and not pictures.is_openable(row["page"]):
        raise _shape(record, at, "a row's page", "an http or https address")
    try:
        row["picture"] = pictures.address(record, _source_text(record, at, one, "picture"))
    except pictures.OutboundNotDeclaredError as exc:
        raise ExtensionError(i18n.t("error.extensions.picture_outbound",
                                    display=record.display_name, key=at)) from exc
    current = one.get("current")
    if current is not None and not isinstance(current, bool):
        raise _shape(record, at, "a row's current", "true or false")
    row["current"] = bool(current)
    row["vps_file_id"] = _source_text(record, at, one, "vps_file_id")
    row["state"] = _source_state(record, at, one.get("state"))
    row["acts"] = _source_acts(record, at, one.get("acts"))
    return row


def _source_state(record: Record, at: str, state: Any) -> dict | None:
    if state in (None, "", {}):
        return None
    if not isinstance(state, dict) or set(state) - SOURCE_STATE_KEYS:
        raise _shape(record, at, "a row's state",
                     f"an object with {', '.join(sorted(SOURCE_STATE_KEYS))}")
    level = _source_text(record, at, state, "level") or "unknown"
    if level not in SOURCE_STATE_LEVELS:
        raise _shape(record, at, f"a state level of {level}",
                     f"one of {', '.join(SOURCE_STATE_LEVELS)}")
    return {"text": _source_text(record, at, state, "text", required=True), "level": level,
            "hint": _source_text(record, at, state, "hint")}


def _source_acts(record: Record, at: str, acts: Any) -> list[dict]:
    if acts in (None, ""):
        return []
    if not isinstance(acts, list):
        raise _shape(record, at, "a row's acts", "a list")
    seen: set[str] = set()
    made = []
    for one in acts:
        if not isinstance(one, dict) or set(one) - SOURCE_ACT_KEYS:
            raise _shape(record, at, "an act",
                         f"an object with {', '.join(sorted(SOURCE_ACT_KEYS))}")
        key = _source_text(record, at, one, "key", required=True)
        if key in seen:
            raise _shape(record, at, f"two acts with the key {key}", "each once")
        seen.add(key)
        made.append({"key": key, "label": _source_text(record, at, one, "label", required=True)})
    return made


def _source_link(record: Record, at: str, link: Any) -> dict | None:
    if link in (None, "", {}):
        return None
    if not isinstance(link, dict) or set(link) - SOURCE_LINK_KEYS:
        raise _shape(record, at, "a link",
                     f"an object with {', '.join(sorted(SOURCE_LINK_KEYS))}")
    text = _source_text(record, at, link, "text", required=True)
    chosen = [key for key in ("to", "section", "settings") if link.get(key)]
    if len(chosen) != 1:
        raise _shape(record, at, "a link", "one of to, section or settings")
    if "to" in chosen:
        if not isinstance(link["to"], str) or not pictures.is_openable(link["to"]):
            raise _shape(record, at, "a link's to", "an http or https address")
        return {"text": text, "to": link["to"]}
    if "section" in chosen:
        if not isinstance(link["section"], str) or link["section"] not in {
                one["key"] for one in record.declared_sections}:
            raise _shape(record, at, "a link's section", "a section the extension declared")
        return {"text": text, "section": link["section"]}
    return {"text": text, "settings": True}


def _asked_who(who: Mapping[str, Any] | None) -> dict[str, str]:
    sent = who or {}
    return {key: found if isinstance(found := sent.get(key), str) else "" for key in WHO_KEYS}


def source_offers(record: Record, key: str, kind: str, who: Mapping[str, Any] | None) -> dict:
    """What the extension's source `key` has of `kind` for the entry `who` names: the rows
    checked and the pictures addressed, with the note and link it draws under them."""
    found = _source(record, key, kind)
    if found.offers is None:
        raise _no_verb(record, key, "offers")
    at = f"source {key} offers"
    answer = plain(record, at, called(record, at, found.offers, _asked_who(who), kind))
    if answer is None:
        answer = {}
    if not isinstance(answer, dict) or set(answer) - SOURCE_ANSWER_KEYS:
        raise _shape(record, at, "its answer",
                     f"an object with {', '.join(sorted(SOURCE_ANSWER_KEYS))}")
    rows = answer.get("rows") or []
    if not isinstance(rows, list):
        raise _shape(record, at, "its rows", "a list")
    seen: set[str] = set()
    return {"rows": [_source_row(record, at, one, seen) for one in rows],
            "note": _source_text(record, at, answer, "note"),
            "link": _source_link(record, at, answer.get("link"))}


def _no_verb(record: Record, key: str, verb: str) -> NotFoundError:
    return NotFoundError(i18n.t("error.extensions.source_verb", display=record.display_name,
                                key=key, verb=verb))


def _taken(record: Record, at: str, done: Any, *, empty: bool = False) -> dict:
    """An answer to a take or an act: a job it started, a dialog to open next, or a message,
    and nothing else."""
    worded = isinstance(done, dict) and isinstance(done.get("message"), str)
    if worded and not done["message"].strip():
        done = {field: value for field, value in done.items() if field != "message"}
    if empty and done == {}:
        return {}
    if (not isinstance(done, dict) or set(done) - SOURCE_TAKE_KEYS
            or not any(done.get(key) for key in SOURCE_TAKE_KEYS)
            or ("job" in done and "action" in done)
            or ("action" in done and done.get("message"))):
        raise _shape(record, at, "its answer", "a job, an action or a message")
    if "job" in done and not (isinstance(done["job"], str) and done["job"].strip()):
        raise _shape(record, at, "its job", "an id")
    if "action" in done and not (isinstance(done["action"], str)
                                 and done["action"] in _context(record).ui.action_objects):
        raise _shape(record, at, "its action", "a dialog the extension declared")
    if "message" in done and not isinstance(done["message"], str):
        raise _shape(record, at, "its message", "text")
    return done


def _named(record: Record, key: str) -> sources.Source:
    found = sources.named(record.name, key)
    if found is None:
        raise NotFoundError(i18n.t("error.extensions.no_source", display=record.display_name,
                                   key=key))
    if not sources.enabled(found):
        raise RefusedError(i18n.t("error.extensions.source_off", display=record.display_name,
                                  key=key))
    return found


def source_take(record: Record, key: str, kind: str, who: Mapping[str, Any] | None,
                row: str, need: Need | None = None) -> dict:
    """Take the row `row` of the extension's source `key`: a job it started, a dialog to
    open next, or a message, and nothing else."""
    found = _source(record, key, kind)
    if found.take is None:
        raise _no_verb(record, key, "take")
    at = f"source {key} take"
    _need(record, need, found.take, reach.LIBRARY, f"source:{key}")
    return _taken(record, at, _message(record, at, called(record, at, found.take,
                                                          _asked_who(who), kind, row)))


def source_take_many(record: Record, key: str, who: Mapping[str, Any] | None,
                     picks: Sequence[Mapping[str, Any]], need: Need | None = None) -> dict:
    """Take every file ticked in Discovery at once, as `[{"kind", "key"}]`; the answer is a
    message or nothing."""
    found = _named(record, key)
    if found.take_many is None:
        raise _no_verb(record, key, "take_many")
    at = f"source {key} take_many"
    asked = []
    for one in picks:
        kind, row = str(one.get("kind") or "").strip(), str(one.get("key") or "").strip()
        if kind not in found.kinds:
            raise NotFoundError(i18n.t("error.extensions.source_kind",
                                       display=record.display_name, key=key, kind=kind))
        if not row:
            raise _shape(record, at, "a pick with no key", "one with a key")
        asked.append({"kind": kind, "key": row})
    if not asked:
        return {}
    _need(record, need, found.take_many, reach.LIBRARY, f"source:{key}")
    done = _message(record, at, called(record, at, found.take_many, _asked_who(who), asked))
    said_so = done.get("message", "") if isinstance(done, dict) else None
    if not isinstance(done, dict) or set(done) - {"message"} or not isinstance(said_so, str):
        raise _shape(record, at, "its answer", "a message")
    return {"message": said_so.strip()} if said_so.strip() else {}


def source_act(record: Record, key: str, kind: str, who: Mapping[str, Any] | None,
               row: str, act: str, need: Need | None = None) -> dict:
    """Press a button the source drew on a row, or on the pane (no `kind` and no `row`): what
    a take answers, or nothing."""
    found = _source(record, key, kind) if kind else _named(record, key)
    if found.act is None:
        raise _no_verb(record, key, "act")
    at = f"source {key} act"
    _need(record, need, found.act, reach.LIBRARY, f"source:{key}")
    return _taken(record, at, _message(record, at, called(record, at, found.act,
                                                          _asked_who(who), kind, row, act)),
                  empty=True)


def source_look(record: Record, key: str, kind: str, who: Mapping[str, Any] | None,
                row: str) -> dict:
    """What the file's page says, for an opened row: its pictures, its description and the
    files in it with their sizes."""
    found = _source(record, key, kind)
    if found.look is None:
        raise _no_verb(record, key, "look")
    at = f"source {key} look"
    answer = plain(record, at, called(record, at, found.look, _asked_who(who), kind, row))
    if answer is None:
        answer = {}
    if not isinstance(answer, dict) or set(answer) - SOURCE_LOOK_KEYS:
        raise _shape(record, at, "its answer",
                     f"an object with {', '.join(sorted(SOURCE_LOOK_KEYS))}")
    shots = answer.get("pictures") or []
    listed = answer.get("files") or []
    if not isinstance(shots, list) or not isinstance(listed, list):
        raise _shape(record, at, "its pictures or files", "lists")
    try:
        addressed = [pictures.address(record, one.strip()) for one in shots[:LOOK_PICTURES]
                     if isinstance(one, str) and one.strip()]
    except pictures.OutboundNotDeclaredError as exc:
        raise ExtensionError(i18n.t("error.extensions.picture_outbound",
                                    display=record.display_name, key=at)) from exc
    files = []
    for one in listed[:LOOK_FILES]:
        if not isinstance(one, dict) or set(one) - SOURCE_FILE_KEYS:
            raise _shape(record, at, "a file", "an object with name and size")
        size = one.get("size")
        if size is not None and (isinstance(size, bool) or not isinstance(size, int)
                                 or not 0 <= size < LOOK_SIZE):
            raise _shape(record, at, "a file's size", "a number of bytes")
        files.append({"name": _source_text(record, at, one, "name", required=True),
                      "size": size})
    description = _source_text(record, at, answer, "description")
    return {"pictures": [one for one in addressed if one],
            "description": description[:LOOK_DESCRIPTION], "files": files,
            "note": _source_text(record, at, answer, "note")}


# -- accounts --------------------------------------------------------------------------


def _account(record: Record) -> Any:
    found = _context(record).ui.account_functions
    if found is None:
        raise NotFoundError(i18n.t("error.extensions.no_account", display=record.display_name))
    return found


def account_read(record: Record, player_id: str) -> dict:
    return plain(record, "account", called(record, "account", _account(record).read,
                                           player_id))


def account_write(record: Record, player_id: str, values: dict) -> dict:
    write = _account(record).write
    if write is None:
        raise RefusedError(i18n.t("error.extensions.account_read_only",
                                  display=record.display_name))
    answer = called(record, "account write", write, player_id, values or {})
    if isinstance(answer, dict):
        return plain(record, "account write", answer)
    return account_read(record, player_id)


def account_act(record: Record, player_id: str, act: str) -> dict:
    do = _account(record).acts.get(act)
    if do is None:
        raise NotFoundError(i18n.t("error.extensions.no_act", display=record.display_name,
                                   key=act))
    answer = called(record, f"account act {act}", do, player_id)
    if answer is None:
        return account_read(record, player_id)
    return _message(record, f"account act {act}", answer)


def account_card(record: Record, player_id: str) -> dict:
    make = _account(record).card
    answer = called(record, "account card", make, player_id) if make else None
    if answer is None:
        raise NotFoundError(i18n.t("error.extensions.no_card", display=record.display_name))
    return plain(record, "account card", answer)


def account_read_card(record: Record, card: Any) -> dict:
    read = _account(record).read_card
    if read is None:
        raise NotFoundError(i18n.t("error.extensions.no_read_card", display=record.display_name))
    return plain(record, "account card", called(record, "account card", read, card))


def account_available(record: Record, candidate: str) -> dict:
    check = _account(record).available
    if check is None:
        raise NotFoundError(i18n.t("error.extensions.no_available", display=record.display_name))
    return {"available": bool(called(record, "account available", check, candidate))}


__all__ = [
    "ExtensionError", "Progress", "account_act", "account_available",
    "account_card", "account_read", "account_read_card", "account_write", "called",
    "dialog_act", "dialog_ask", "dialog_check", "dialog_progress", "dialog_run",
    "list_about", "list_act", "list_rows", "plain", "said", "settings_read",
    "settings_write", "source_act", "source_look", "source_offers", "source_take",
    "source_take_many",
]
