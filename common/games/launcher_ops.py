"""The configured ways this install runs a table.

A launcher is an object somebody creates, removes and duplicates, and what it names is a
program on this machine. Copying one to a cabinet keeps its id, which is why the id is the
caller's rather than something minted on every write.

The app it runs declares its own settings, so a caller can draw an editor without knowing
what a Visual Pinball launcher happens to hold.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

from common import apps, failures, i18n, service_errors
from common.apps.contract import (
    SCOPE_ENTRY,
    SCOPE_FOLDER,
    SCOPE_LAUNCHER,
    Leftover,
    Standing,
    TableOrder,
)
from common.config import config_schema, path_state
from common.games import game_repository, launchers, library_folders, tables
from common.games.table_identity import find_table_by_id
from common.host import bundled_libraries, command_line, launch_state
from common.i18n import t
from common.launcher_path import resolve_launcher_path
from common.runtime import jobs, shutdown

logger = logging.getLogger("vpinfe.common.games.launcher_ops")

_MAKE_FILE_SECONDS = 30
_making_file = threading.Lock()
# launcher id -> the program that ran for its settings file and wrote none. A different
# program under the same launcher is a new question, so it is keyed by what ran.
_wrote_nothing: dict[str, str] = {}


def launcher_or_refuse(launcher_id: str) -> launchers.Launcher:
    found = launchers.get_launcher_store().get(launcher_id)
    if found is None:
        raise service_errors.NotFoundError(
            t("error.launchers.unknown_launcher", launcher_id=(launcher_id)))
    return found


def _described(launcher: launchers.Launcher) -> dict[str, Any]:
    """One launcher, with the shape of its own settings alongside the values.

    The fields travel with it so a client can draw an editor without knowing what a
    Visual Pinball launcher happens to hold - which is the whole point of the app
    declaring them.
    """
    settings = _launcher_settings(launcher)
    left_empty = getattr(_app_settings_surface(launcher), "left_empty", None)
    standing = left_empty(settings) if callable(left_empty) else {}
    return {
        "launcher_id": launcher.launcher_id,
        "app": launcher.app,
        "app_name": apps.app_name(launcher.app),
        "display_name": launcher.display_name,
        "enabled": launcher.enabled,
        "owns_ini": launcher.owns_ini,
        "has_config": _app_settings_surface(launcher) is not None,
        "has_settings_file": _has_settings_file(launcher),
        "settings_file_not_made": _asked_without_result(launcher),
        "can_make_settings_file": _can_run_for_file(launcher)
                                  and not _asked_without_result(launcher),
        "settings": settings,
        # `lines`, `choices` and the bounds travel with the field because the control a
        # surface draws is decided from them - a field declared over three lines that
        # arrives without them renders as a one-line box.
        "fields": [{"key": f.key, **apps.field_words(launcher.app, f), "type": f.type,
                    "default": f.default, "path": f.path, "suffixes": list(f.suffixes),
                    "lines": f.lines, "choices": dict(f.choices),
                    "min": f.minimum, "max": f.maximum,
                    **_standing_words(launcher.app, f, standing.get(f.key))}
                   for f in launcher.fields()],
        "command_line": command_line.shown(launcher),
    }


def _standing_words(app_id: str, f: apps.Field, held: Standing | None) -> dict[str, Any]:
    """What a field left empty reads as. A path names the file it stands for; one that
    names none reads Not found, with the app's own words for why on hover."""
    if held is not None and held.path:
        return {"blank": apps.field_blank(app_id, f), "left_empty": "",
                "default_path": held.path, "default_exists": held.exists}
    nowhere = held is not None
    return {"blank": apps.field_blank(app_id, f) or (t("word.not_found") if nowhere else ""),
            "left_empty": apps.field_nowhere(app_id, f) if nowhere else ""}


def standing(app_id: str, settings: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """What each field of an app would read when left empty, for `settings` as they
    stand, before any launcher holds them. Only the fields the app answers for appear."""
    app = apps.get(app_id)
    left_empty = getattr(getattr(app, "config", None), "left_empty", None)
    if app is None or not callable(left_empty):
        return {}
    held = left_empty(dict(settings))
    return {f.key: _standing_words(app_id, f, held[f.key])
            for f in app.fields if f.key in held}


def listing() -> dict[str, Any]:
    """The launchers in order, the tables that deviate, and which one is the default.

    `default` is named rather than left to be worked out: the rule is the first enabled
    launcher for an app, and a client re-deriving that is a second place for it to be
    wrong.
    """
    store = launchers.get_launcher_store()
    held = store.launchers()
    mappings = store.mappings()
    plays = Counter(getattr(launchers.launcher_for_entry(app_id, table_id, held, mappings),
                            "launcher_id", "")
                    for app_id, table_id in _library_tables())
    return {
        "launchers": [_described(one) for one in held],
        "mappings": mappings,
        "defaults": {app.id: getattr(launchers.default_for(app.id, held),
                                     "launcher_id", None)
                     for app in apps.all_apps()},
        "tables": {one.launcher_id: plays[one.launcher_id] for one in held},
        "apps": [{"id": app.id, "name": apps.app_name(app.id),
                  "suffixes": list(app.claim.suffixes),
                  "has_config": app.config is not None,
                  "fields": [{"key": f.key, **apps.field_words(app.id, f), "path": f.path,
                              "suffixes": list(f.suffixes),
                              "blank": apps.field_blank(app.id, f)}
                             for f in app.fields]}
                 for app in apps.all_apps()],
    }


def put(launcher_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """Write one whole, under the id the caller names, and answer as `/launchers` does.

    Keeps the caller's id: a launcher copied from another machine arrives under the id its
    tables name. The list's own rules refuse a bad write.
    """
    wanted = str(launcher_id or "").strip()
    if not wanted:
        raise service_errors.RefusedError(t("error.launchers.needs_id"))
    store = launchers.get_launcher_store()
    try:
        store.put(launchers.Launcher(
            launcher_id=wanted,
            app=str(body.get("app") or "").strip(),
            display_name=str(body.get("display_name") or "").strip(),
            enabled=bool(body.get("enabled", True)),
            owns_ini=bool(body.get("owns_ini", False)),
            settings=dict(body.get("settings") or {}),
        ))
    except config_schema.ConfigListError as exc:
        raise service_errors.RefusedError(failures.why(exc)) from exc
    return _described(launcher_or_refuse(wanted))


def fallback(launcher_id: str) -> dict[str, Any]:
    """What switching one off would do: the tables it plays, where each group of them
    would go, and the refusal `put` would give, or ""."""
    return _fallback(launcher_or_refuse(launcher_id))


def switch_off_refusal(leaving: launchers.Launcher) -> str:
    """Why switching this one off is refused, or "" where it is not."""
    return str(_fallback(leaving)["refused"])


def _fallback(leaving: launchers.Launcher) -> dict[str, Any]:
    store = launchers.get_launcher_store()
    held = store.launchers()
    mappings = store.mappings()
    without = [replace(one, enabled=False) if one.launcher_id == leaving.launcher_id
               else one for one in held]
    moving: dict[str, tuple[launchers.Launcher | None, int]] = {}
    for app_id, table_id in _library_tables():
        now = launchers.launcher_for_entry(app_id, table_id, held, mappings)
        if now is None or now.launcher_id != leaving.launcher_id:
            continue
        after = launchers.launcher_for_entry(app_id, table_id, without, mappings)
        key = after.launcher_id if after is not None else ""
        moving[key] = (after, moving.get(key, (after, 0))[1] + 1)
    return {
        "tables": sum(count for _one, count in moving.values()),
        "fallbacks": [{"launcher_id": key,
                       "display_name": one.display_name if one is not None else "",
                       "tables": count,
                       "has_program": one is not None and _has_program(one)}
                      for key, (one, count) in moving.items()],
        "refused": _refusal([one for one, _count in moving.values()]),
    }


def _refusal(fallbacks: list[launchers.Launcher | None]) -> str:
    for one in fallbacks:
        if one is None:
            return t("error.launchers.no_fallback")
        if not _has_program(one):
            return t("error.launchers.fallback_unrunnable", name=one.display_name)
    return ""


def _has_program(launcher: launchers.Launcher) -> bool:
    return launcher.has_program()


def _library_tables() -> Iterator[tuple[str, str]]:
    """(app, table id) for every table in the library that has an id."""
    for game in game_repository.all_games():
        stored = (getattr(game, "meta_config", None) or {}).get(tables.TABLES_KEY)
        for entry in (stored.values() if isinstance(stored, dict) else ()):
            found = tables.table_id(entry)
            if found:
                yield tables.app_of(entry), found


def _app_settings_surface(launcher: launchers.Launcher) -> Any:
    """The app's own settings surface for this launcher, or None where its app has
    none. `generic` has none: it knows a program and arguments and nothing about what
    that program stores."""
    app = apps.get(launcher.app)
    return getattr(app, "config", None) if app is not None else None


def _launcher_settings(launcher: launchers.Launcher) -> dict[str, Any]:
    return {field.key: launcher.value(field.key) for field in launcher.fields()}


def _game_file(table_id: str) -> str:
    """The file behind a table id, resolved here rather than sent.

    A caller names the table, not the path. Where a game file sits is a fact about this
    machine, and the Manager reads this API over the network - it has no business
    knowing, and on another machine it would be wrong.
    """
    wanted = str(table_id or "").strip()
    if not wanted:
        return ""
    found = find_table_by_id(game_repository.all_games(), wanted)
    if found is None:
        raise service_errors.NotFoundError(t("error.launchers.unknown_table", wanted=(wanted)))
    game, filename = found
    return str(Path(str(game.full_path_game or "")) / filename)


def effective_values(launcher_id: str, table: str = "") -> dict[str, str]:
    """What the program will use for each of its settings at this table, by key. Empty
    where the launcher's program has no settings of its own."""
    found = launcher_or_refuse(launcher_id)
    config = _app_settings_surface(found)
    if config is None:
        return {}
    held = config.read(SCOPE_ENTRY, _game_file(table), _launcher_settings(found))
    return {key: str(one.value or "") for key, one in held.items()}


def _program(launcher: launchers.Launcher) -> dict[str, Any] | None:
    """What the launcher's installed program is, as far as its settings depend on it, or
    None where its app does not say. `plugins` is a list, or None where the program's
    plugins cannot be read."""
    asking = getattr(_app_settings_surface(launcher), "program", None)
    if asking is None:
        return None
    one = asking(_launcher_settings(launcher))
    return {"plugins": None if one.plugins is None else sorted(one.plugins),
            "defaults": dict(one.defaults), "build": one.build,
            "dmd_in_file": one.dmd_in_file}


def program_of(launcher_id: str) -> dict[str, Any] | None:
    """`_program`, for the launcher with this id."""
    return _program(launcher_or_refuse(launcher_id))


def table_program(table_id: str) -> dict[str, Any] | None:
    """`program_of` the launcher that plays this table."""
    return _program(_launcher_of(table_id))


def app_config(launcher_id: str, table: str = "",
               scope: str = "launcher") -> dict[str, Any]:
    """The declared groups, and every value as it stands at one scope.

    The groups come from the app, which reads them out of the program's own files, so a
    setting a later build of that program adds appears without anything here changing.
    """
    found = launcher_or_refuse(launcher_id)
    config = _app_settings_surface(found)
    if config is None:
        return {"groups": [], "values": {}, "scopes": [], "shared_with_game": False,
                "from_game": {}, "follows_game": {}, "app_name": apps.app_name(found.app),
                "files": {}, "program": None}

    settings = _launcher_settings(found)
    if scope not in config.scopes():
        raise service_errors.RefusedError(
            t("error.launchers.unknown_scope_app", scope=(scope),
                    scopes=', '.join(config.scopes())))
    target = _game_file(table)
    values = config.read(scope, target, settings)
    scopes_for = _scopes_for(config)
    shares = getattr(config, "shared_with_game", None)
    reach = getattr(config, "from_game", None)
    follows = getattr(config, "follows_game", None)

    def shown(field: apps.Field) -> bool:
        held = values.get(field.key)
        return scope in scopes_for(field.key) or (held is not None and held.set_here)

    blank = _blank_words(found.app, config)
    named = _named_values(found.app, config)
    reporting = getattr(config, "reported", None)
    reported = dict(reporting()) if reporting is not None else {}
    held = getattr(config, "held_groups", None)
    declared = (*config.groups(settings),
                *(held(target) if target and held is not None else ()))
    groups = [(g, [f for f in g.settings if shown(f)]) for g in declared]
    rows = getattr(config, "summary_rows", None)

    def drawn(group: apps.ConfigGroup, fields: list[apps.Field]) -> list[str]:
        keys = {f.key for f in fields}
        named = rows(group.key, values) if group.summarized and rows is not None else ()
        return [key for key in named if key in keys]

    return {
        "app_name": apps.app_name(found.app),
        "files": config_files(found),
        "program": _program(found),
        "scopes": list(config.scopes()),
        "shared_with_game": bool(target and shares is not None and shares(target)),
        "from_game": (dict(reach(target, settings))
                      if scope == SCOPE_ENTRY and target and reach is not None else {}),
        "follows_game": (dict(follows(target, settings))
                         if scope == SCOPE_ENTRY and target and follows is not None else {}),
        "groups": [{"key": g.key, **apps.group_words(found.app, g),
                    "summarized": g.summarized, "rows": drawn(g, fields),
                    "read_only": g.read_only,
                    "curated": _curated(found.app, g, {f.key for f in fields}),
                    "settings": [{**_described_field(found.app, f), **blank(f.key),
                                  "named": named(f.key),
                                  "reported": list(reported.get(f.key, ())),
                                  "scopes": list(scopes_for(f.key))} for f in fields]}
                   for g, fields in groups if fields],
        "values": {key: {"value": one.value, "scope": one.scope,
                         "set_here": one.set_here, "in_effect": one.in_effect,
                         "fallback": one.fallback, "fallback_scope": one.fallback_scope}
                   for key, one in values.items()},
    }


def _scopes_for(config: Any) -> Callable[[str], tuple[str, ...]]:
    """An app that does not say otherwise offers every setting at every scope."""
    answer = getattr(config, "scopes_for", None)
    return answer if answer is not None else (lambda _key: tuple(config.scopes()))


def _blank_words(app_id: str, config: Any) -> Callable[[str], dict[str, str]]:
    """What a blank value does, in the app's own words, where the app says it is not the
    declared default: `blank` the word and `left_empty` its help. An app that does not
    say leaves every one empty."""
    naming = getattr(config, "blank_words", None)
    words = dict(naming()) if naming is not None else {}

    def said(key: str) -> dict[str, str]:
        word = f"app.{app_id}.{words[key]}" if key in words else ""
        return {"blank": i18n.literal_or("", word)[0] if word else "",
                "left_empty": i18n.literal_or("", f"{word}.help")[0] if word else ""}
    return said


def _named_values(app_id: str, config: Any) -> Callable[[str], list[list[str]]]:
    """The values a setting's app gives a meaning of their own, each `[value, label]` in
    the app's words. An app that does not say names none."""
    naming = getattr(config, "named_values", None)
    named = dict(naming()) if naming is not None else {}
    return lambda key: [[value, i18n.literal_or("", f"app.{app_id}.{word}")[0]]
                        for value, word in named.get(key, ())]


def _described_field(app_id: str, field: apps.Field) -> dict[str, Any]:
    said = apps.choice_words(app_id, field)
    return {"key": field.key, **apps.field_words(app_id, field),
            "help": apps.field_help(app_id, field), "type": field.type,
            "default": field.default,
            "choices": [[value, said.get(value, label)] for value, label in field.choices],
            "choice_help": apps.choice_help(app_id, field),
            "minimum": field.minimum, "maximum": field.maximum,
            "path": field.path, "suffixes": list(field.suffixes),
            "per_table": field.per_table}


def _curated(app_id: str, group: apps.ConfigGroup, shown: set[str]) -> list[dict[str, Any]]:
    """The group's curated headings, holding only rows this scope shows."""
    switches = {one.enabled_by for one in group.curated if one.enabled_by in shown}
    found = []
    for heading in group.curated:
        keys = [key for key in heading.keys if key in shown]
        if keys:
            switched = heading.enabled_by in keys
            found.append({"key": heading.key, **apps.heading_words(app_id, group.key,
                                                                   heading),
                          "keys": keys,
                          "enabled_by": heading.enabled_by if switched else "",
                          "rivals": [key for key in heading.rivals if key in switches]
                          if switched else [],
                          "kinds": list(heading.kinds),
                          "versions": [key for key in heading.versions if key in shown],
                          "pairs": [{"key": pair.key,
                                     **apps.pair_words(app_id, group.key, pair),
                                     "keys": list(pair.keys)}
                                    for pair in heading.pairs if set(pair.keys) <= shown],
                          "switched": [{"enabled_by": one.enabled_by, "keys": rows,
                                        "on": one.on}
                                       for one in heading.switched if one.enabled_by in keys
                                       if (rows := [key for key in one.keys if key in keys])]})
    return found


def write_config(launcher_id: str, body: dict[str, Any], *,
                 settle: bool = True) -> dict[str, Any] | jobs.Job:
    """Values at one scope. The app writes them into its own file in place, and names
    under `cleared` the ones it cleared instead, for holding the launcher's own value.
    Given `tables`, more than one, the same values go into each as a job.

    A value that decides the draw order of a table's backglass and score display brings the
    tables it reaches to what their backglass files place, unless `settle` is off."""
    found = launcher_or_refuse(launcher_id)
    config = _app_settings_surface(found)
    if config is None:
        raise service_errors.RefusedError(
            t("error.launchers.no_own_settings", app_name=(apps.app_name(found.app))))

    scope = str(body.get("scope") or "launcher")
    if scope not in config.scopes():
        raise service_errors.RefusedError(t("error.launchers.unknown_scope", scope=(scope)))
    if scope == SCOPE_FOLDER:
        raise service_errors.RefusedError(t("error.launchers.game_file_locked"))
    values = body.get("values") or {}
    if not isinstance(values, dict) or not values:
        raise service_errors.RefusedError(t("error.launchers.empty_write"))
    settings = _launcher_settings(found)
    named = body.get("tables") or ()
    if not isinstance(named, (list, tuple)):
        raise service_errors.RefusedError(t("error.launchers.tables_list"))
    ids = list(dict.fromkeys(str(one) for one in named if one))
    if ids and scope == SCOPE_LAUNCHER:
        raise service_errors.RefusedError(t("error.launchers.tables_scope"))
    table = _game_file(ids[0] if len(ids) == 1 else str(body.get("table") or ""))
    writing = {str(k): str(v) for k, v in values.items()}
    scopes_for = _scopes_for(config)
    refused = sorted(key for key, value in writing.items()
                     if value != "" and scope not in scopes_for(key))
    if refused:
        raise service_errors.RefusedError(t(
            "error.launchers.one_table_only" if scope == SCOPE_LAUNCHER
            else "error.launchers.all_tables_only", app_name=(apps.app_name(found.app)),
            keys=(", ".join(refused))))

    orders = settle and bool(set(writing) & set(getattr(config, "order_keys", tuple)()))
    if len(ids) > 1:
        return _write_tables(found, config, settings, scope, ids, writing, orders)
    if orders and scope == SCOPE_LAUNCHER:
        with _order().launcher_write():
            answer = _write_one(found, config, settings, scope, table, writing,
                                bool(body.get("create")))
            answer["settling"] = _order().settle_launcher(found.launcher_id).id
        return answer
    answer = _write_one(found, config, settings, scope, table, writing, bool(body.get("create")))
    if orders:
        _order().settle_table(ids[0] if ids else str(body.get("table") or ""))
    return answer


def _write_one(found: launchers.Launcher, config: Any, settings: dict[str, Any], scope: str,
               table: str, writing: dict[str, str], create: bool) -> dict[str, Any]:
    if create and scope == SCOPE_LAUNCHER and not config_files(found):
        # Only when asked: a write on its own refuses where the program has no file yet.
        make = getattr(config, "create", None)
        if make is None:
            raise service_errors.RefusedError(t(
                "error.launchers.cannot_copy_settings", app_name=apps.app_name(found.app)))
        try:
            make(config.newest_version, writing, settings)
        except ValueError as exc:
            raise _cannot_write(found) from exc
        finally:
            game_repository.touch()
        return {"written": sorted(writing), "cleared": []}

    _writable_before_writing(found, scope, table)
    try:
        cleared = config.write(scope, table, writing, settings)
    except ValueError as exc:
        raise _cannot_write(found) from exc
    finally:
        game_repository.touch()
    return {"written": sorted(set(writing) - cleared), "cleared": sorted(cleared)}


def left_behind(launcher_id: str) -> tuple[Leftover, ...]:
    """What an upgrade of this launcher's program left in its settings, as its app says.
    Nothing where its app has no settings of its own or does not look."""
    found = launcher_or_refuse(launcher_id)
    ask = getattr(_app_settings_surface(found), "left_behind", None)
    return ask(_launcher_settings(found)) if ask is not None else ()


def backglass_order(launcher_id: str, target: str, in_file: bool,
                    written: dict[str, str]) -> TableOrder:
    """What the table whose game file is `target` should hold in its own settings for the
    order its backglass and score display draw in, as the launcher's app says, given whether
    its backglass file places a Score View DMD and what was recorded as written there.
    Nothing where the app has no such order."""
    found = launcher_or_refuse(launcher_id)
    ask = getattr(_app_settings_surface(found), "backglass_order", None)
    if ask is None:
        return TableOrder()
    return ask(target, _launcher_settings(found), in_file, written)


def refuse_unwritable(launcher_id: str) -> None:
    """Refuse, in the words a write would, where the launcher's settings file cannot be
    written."""
    _writable_before_writing(launcher_or_refuse(launcher_id), SCOPE_LAUNCHER, "")


def write_table_settings(table_id: str, values: dict[str, Any], *,
                         settle: bool = True) -> dict[str, Any] | jobs.Job:
    """Values for one table alone, in the settings file its program keeps beside it.

    Named for the table rather than a launcher: the caller is holding a table it just
    made, and which launcher plays it is this install's answer. Never the launcher's
    own values, so nothing cabinet-wide changes. A value equal to what the table would
    follow anyway is not written, and a table left with nothing to keep has no file.
    """
    return write_config(_launcher_of(table_id).launcher_id,
                        {"scope": SCOPE_ENTRY, "table": table_id, "values": dict(values)},
                        settle=settle)


def _launcher_of(table_id: str) -> launchers.Launcher:
    """The launcher that plays this table, or the refusal that says it has none."""
    found = find_table_by_id(game_repository.all_games(), str(table_id or "").strip())
    if found is None:
        raise service_errors.NotFoundError(t("error.launchers.unknown_table", wanted=table_id))
    _game, filename = found
    store = launchers.get_launcher_store()
    launcher = launchers.launcher_for_table(filename, table_id, store.launchers(),
                                            store.mappings())
    if launcher is None:
        app = apps.app_for(filename)
        raise service_errors.RefusedError(t(
            "error.launchers.no_own_settings",
            app_name=apps.app_name(app.id) if app is not None else filename))
    return launcher


def _order() -> Any:
    # Imported here: it is built on this module.
    from common.games import backglass_order

    return backglass_order


def _write_tables(launcher: launchers.Launcher, config: Any, settings: dict[str, Any],
                  scope: str, ids: list[str], writing: dict[str, str],
                  orders: bool = False) -> jobs.Job:
    """The same values written into each of several tables' own files, as a job: one table
    that cannot be found or written is named in the result and the rest go on. A table the
    job was stopped before reaching is in neither list."""
    def work(job: jobs.Job) -> dict[str, Any]:
        reporter = job.reporter()
        written: list[str] = []
        failed: list[dict[str, str]] = []
        for index, table_id in enumerate(ids):
            if shutdown.requested():
                break
            reporter.progress(index, len(ids), t("said.table_settings",
                                                 count=len(ids) - index))
            try:
                target = _game_file(table_id)
                _writable_before_writing(launcher, scope, target)
                config.write(scope, target, writing, settings)
            except Exception as exc:  # noqa: BLE001 - one table is named, the rest go on
                if not isinstance(exc, (ValueError, service_errors.ServiceError)):
                    logger.warning("Writing settings for table %s failed", table_id,
                                   exc_info=True)
                failed.append({"table": table_id, "reason": failures.why(exc)})
            else:
                written.append(table_id)
        game_repository.touch()
        if orders:
            failed.extend(_order().settle_tables(written))
        return {"tables": len(ids), "written": written, "failed": failed}

    return jobs.submit(jobs.KIND_TABLE_SETTINGS, work)


def _writable_before_writing(launcher: launchers.Launcher, scope: str, table: str) -> None:
    """Refuse before the write when the disk says the file or the folder it goes in cannot
    be written to. A file that is not there yet is fine while its folder can take it, or is
    itself still to be made."""
    if scope != SCOPE_LAUNCHER:
        if table:
            library_folders.writable_game_folder(Path(table).parent)
        return
    for file in config_files(launcher).values():
        found = Path(file)
        state = path_state.look(str(found), path_state.FILE, writable=True)
        name = found.name
        if state.state == path_state.MISSING:
            state = path_state.look(str(found.parent), path_state.DIR, writable=True)
            name = found.parent.name
            if state.state == path_state.MISSING:
                continue
        said = path_state.refusal(name, state)
        if said:
            raise service_errors.BlockedError(said)


def _cannot_write(launcher: launchers.Launcher) -> service_errors.RefusedError:
    return service_errors.RefusedError(t(
        "error.launchers.cannot_write_settings", app_name=apps.app_name(launcher.app)))


def config_files(launcher: launchers.Launcher) -> dict[str, str]:
    config = _app_settings_surface(launcher)
    naming = getattr(config, "files", None)
    return dict(naming(_launcher_settings(launcher))) if naming else {}


def _has_settings_file(launcher: launchers.Launcher) -> bool:
    return any(Path(file).is_file() for file in config_files(launcher).values())


def _settings_file_command(launcher: launchers.Launcher) -> list[str]:
    asking = getattr(_app_settings_surface(launcher), "settings_file_command", None)
    command = asking(_launcher_settings(launcher)) if callable(asking) else []
    return [str(resolve_launcher_path(command[0])), *command[1:]] if command else []


def _asked_without_result(launcher: launchers.Launcher) -> bool:
    """Whether the program already ran for its settings file and left none. A file that
    has appeared since ends it: the program can write one."""
    held = _wrote_nothing.get(launcher.launcher_id)
    if held and _has_settings_file(launcher):
        del _wrote_nothing[launcher.launcher_id]
        return False
    command = _settings_file_command(launcher)
    return bool(command) and held == command[0]


def _can_run_for_file(launcher: launchers.Launcher) -> bool:
    command = _settings_file_command(launcher)
    return bool(command) and Path(command[0]).is_file() and os.access(command[0], os.X_OK)


def _can_write_preferences() -> bool:
    """Whether a Linux program can keep its preferences: it uses `XDG_DATA_HOME` where set,
    else the home folder, and Visual Pinball crashes without saying why where neither takes
    a write. Every other platform names its own folder and is not asked."""
    if not sys.platform.startswith("linux"):
        return True
    named = os.environ.get("XDG_DATA_HOME") or os.environ.get("HOME") or ""
    if not named:
        return False
    root = Path(named)
    while not root.exists() and root != root.parent:
        root = root.parent
    return os.access(root, os.W_OK | os.X_OK)


def make_settings_file(launcher_id: str) -> dict[str, Any]:
    """Run the program once so it writes its own settings file, then say where it is.

    Refused where the app has no such command, the program is not set, a file is already
    there, or the program ran and left none - which the app does not promise it will.
    """
    found = launcher_or_refuse(launcher_id)
    app_name = apps.app_name(found.app)
    command = _settings_file_command(found)
    if not command:
        raise service_errors.RefusedError(t("error.launchers.cannot_make_file", app_name=app_name))
    if launch_state.current().launching:
        raise service_errors.BlockedError(t("error.launchers.make_file_playing"))
    if not _making_file.acquire(blocking=False):
        raise service_errors.BlockedError(t("error.launchers.make_file_running", app_name=app_name))
    try:
        if _has_settings_file(found):
            raise service_errors.RefusedError(t("error.launchers.file_exists", app_name=app_name))
        if not _can_write_preferences():
            raise service_errors.RefusedError(
                t("error.launchers.no_preferences_folder", app_name=app_name))
        exited = True
        try:
            subprocess.run(command, env=bundled_libraries.for_child(), stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=_MAKE_FILE_SECONDS, check=False)
        except subprocess.TimeoutExpired:
            exited = False
            logger.warning("%s did not exit in %s s making its settings file", app_name,
                           _MAKE_FILE_SECONDS)
        except OSError as exc:
            logger.warning("%s could not be run for its settings file: %s", app_name, exc)
            raise service_errors.RefusedError(
                t("error.launchers.make_file_failed", app_name=app_name)) from exc
    finally:
        _making_file.release()
    if not _has_settings_file(found):
        if exited:
            _wrote_nothing[found.launcher_id] = command[0]
        raise service_errors.RefusedError(t("error.launchers.no_file_made", app_name=app_name))
    return {"files": config_files(found)}


def make_default(launcher_id: str) -> dict[str, Any]:
    """Make it the launcher its app's tables use unless they name another."""
    found = launcher_or_refuse(launcher_id)
    if not _has_program(found):
        raise service_errors.RefusedError(
            t("error.launchers.default_unrunnable", name=found.display_name))
    if not found.enabled:
        raise service_errors.RefusedError(
            t("error.launchers.default_switched_off", name=found.display_name))
    store = launchers.get_launcher_store()
    store.to_front(found.launcher_id)
    return {"launcher_id": found.launcher_id, "app": found.app}


def assign(table_id: str, launcher_id: str) -> dict[str, Any]:
    """An empty `launcher_id` clears it, which is how a table goes back to the default.

    Cleared rather than stored blank: an absent mapping already means the default, and
    two spellings of one state is what makes a reader ask which is which.
    """
    wanted = str(launcher_id or "").strip()
    store = launchers.get_launcher_store()
    if wanted and store.get(wanted) is None:
        raise service_errors.NotFoundError(t("error.launchers.no_launcher", launcher_id=(wanted)))
    store.assign(table_id, wanted)
    return {"table_id": table_id, "launcher_id": store.mapped(table_id)}
