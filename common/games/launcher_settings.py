"""Which settings file a launcher plays with, and moving it from one file to another.

A launcher either has a file of its own or shares one. Giving it a new one is a copy of
another launcher's file, a fresh file the program writes, or an existing file somebody
points at. What that overwrites or leaves behind is kept in Backups, and what was done can
be undone from a token the answer carries.

Nothing is written until the caller has said it knows what is replaced: a change that
would overwrite settings other launchers play with is answered `needs_replace` and does
nothing.
"""

from __future__ import annotations

import logging
import os
import secrets
import tempfile
import threading
from collections import OrderedDict
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from common import apps, service_errors
from common.atomic_write import staged_for
from common.backups import archive
from common.config import config_schema
from common.config.paths import LAUNCHER_SETTINGS_DIR
from common.failures import why
from common.games import launcher_ops, launchers, settings_file_name
from common.i18n import t
from common.kept_copies import REPLACE, copy_aside

logger = logging.getLogger("vpinfe.common.games.launcher_settings")

KEY = "ini_path"
CHOICES = ("copy", "fresh", "pick")
_UNDO_KEPT = 20
MAX_RECEIVED = 5 * 1024 * 1024
_undo: OrderedDict[str, _Snapshot] = OrderedDict()
_undo_lock = threading.Lock()


@dataclass(frozen=True)
class _Snapshot:
    """What a change touched, as it was before: the launcher's record (None where the
    change made it) and each file's bytes (None where the change made it)."""
    launcher_id: str
    launcher: launchers.Launcher | None
    files: dict[Path, bytes | None]


def effective_file(launcher: launchers.Launcher) -> Path | None:
    found = launcher_ops.config_files(launcher).get("application")
    return Path(found).expanduser() if found else None


def program_own_file(app_id: str, settings: dict[str, Any]) -> Path | None:
    """The file the program uses when nothing is named: what an empty field reads as."""
    held = launcher_ops.standing(app_id, {**settings, KEY: ""}).get(KEY) or {}
    found = str(held.get("default_path") or "")
    return Path(found) if found else None


def same_file(one: Path | None, other: Path | None) -> bool:
    if one is None or other is None:
        return False
    try:
        return os.path.samefile(one, other)
    except OSError:
        return os.path.normcase(os.path.abspath(one)) == os.path.normcase(os.path.abspath(other))


def users_of(path: Path | None, *, besides: str = "") -> list[launchers.Launcher]:
    """The launchers whose settings are this file, in list order."""
    if path is None:
        return []
    return [one for one in launchers.get_launcher_store().launchers()
            if one.launcher_id != besides and same_file(effective_file(one), path)]


def sharing(launcher: launchers.Launcher) -> list[str]:
    """The names of the other launchers that play with this launcher's file."""
    return [one.display_name for one in
            users_of(effective_file(launcher), besides=launcher.launcher_id)]


def resolve(folder: Path, name: str) -> Path:
    """`name` in `folder`, as the drive names it: a file already there under the same name
    in another case is that file where the drive treats the two as one."""
    wanted = folder / name
    try:
        held = os.listdir(folder)
    except OSError:
        return wanted
    if name in held:
        return wanted
    for one in held:
        if one.casefold() == name.casefold() and same_file(wanted, folder / one):
            return folder / one
    return wanted


def _program_file_of(path: Path, launcher: launchers.Launcher) -> bool:
    return same_file(path, program_own_file(launcher.app, dict(launcher.settings)))


def _read(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise service_errors.RefusedError(
            t("error.launchers.file_unreadable", path=str(path))) from exc


def _remember(snapshot: _Snapshot) -> str:
    token = secrets.token_urlsafe(16)
    with _undo_lock:
        _undo[token] = snapshot
        while len(_undo) > _UNDO_KEPT:
            _undo.popitem(last=False)
    return token


def _backed_up(launcher: launchers.Launcher, note: str) -> None:
    """The launcher's file in Backups, before it is overwritten or left behind."""
    archive.take(launcher_ops.SCOPE_LAUNCHER, launcher_id=launcher.launcher_id, note=note)


def _put(launcher: launchers.Launcher) -> None:
    try:
        launchers.get_launcher_store().put(launcher)
    except config_schema.ConfigListError as exc:
        raise service_errors.RefusedError(why(exc)) from exc


def _from_body(launcher_id: str, create: Any) -> launchers.Launcher:
    if not isinstance(create, dict):
        raise service_errors.NotFoundError(
            t("error.launchers.unknown_launcher", launcher_id=launcher_id))
    return launchers.Launcher(
        launcher_id=launcher_id, app=str(create.get("app") or "").strip(),
        display_name=str(create.get("display_name") or "").strip(),
        enabled=bool(create.get("enabled", True)), owns_ini=False,
        settings=dict(create.get("settings") or {}))


def _folder_of(body: dict[str, Any]) -> Path:
    named = str(body.get("save_dir") or "").strip()
    return Path(named).expanduser() if named else LAUNCHER_SETTINGS_DIR


def _source_of(body: dict[str, Any]) -> Path:
    """The file a copy is made from: another launcher's, or one picked."""
    if named := str(body.get("copy_file") or "").strip():
        found: Path | None = Path(named).expanduser()
    else:
        found = effective_file(launcher_ops.launcher_or_refuse(str(body.get("copy_from") or "")))
    if found is None or not found.is_file():
        raise service_errors.RefusedError(t("error.launchers.copy_source_missing"))
    return found


def _fresh_file(base: launchers.Launcher, target: Path) -> None:
    """The program writes a standard file at `target`. An old one is set aside while it
    runs and put back if the program leaves none."""
    app_name = apps.app_name(base.app)
    settings = {**launcher_ops.settings_of(base), KEY: str(target)}
    asking = getattr(launcher_ops.settings_surface(base), "settings_file_command", None)
    command = asking(settings) if callable(asking) else []
    if not command:
        raise service_errors.RefusedError(t("error.launchers.cannot_make_file", app_name=app_name))
    command = [str(launcher_ops.resolve_launcher_path(command[0])), *command[1:]]
    aside = target.with_name(f".{target.name}.replaced") if target.exists() else None
    if aside is not None:
        os.replace(target, aside)
    try:
        launcher_ops.run_for_settings_file(command, app_name)
        if not target.is_file():
            raise service_errors.RefusedError(t("error.launchers.no_file_made", app_name=app_name))
    except BaseException:
        if aside is not None:
            os.replace(aside, target)
        raise
    if aside is not None:
        aside.unlink(missing_ok=True)


def apply(launcher_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """Give a launcher a settings file: copied from another, made fresh by the program, or
    one that is already there. The launcher may be one that is about to be added, named
    by `create`; it is stored here, last, so a failure leaves nothing half done.

    Answers `{"needs_replace": {"users", "program_file", "path"}}` and changes nothing
    where the file written over is settings others play with, until the caller says
    `replace`."""
    store = launchers.get_launcher_store()
    stored = store.get(launcher_id)
    base = stored or _from_body(launcher_id, body.get("create"))
    choice = str(body.get("choice") or "")
    if choice not in CHOICES:
        raise service_errors.RefusedError(t("error.launchers.bad_choice"))
    old = effective_file(base) if stored else None
    leaving_own = (stored is not None and stored.owns_ini and old is not None and old.is_file()
                   and not users_of(old, besides=launcher_id))
    if choice == "pick":
        return _pick(base, stored, old, leaving_own, body)

    name = (settings_file_name.with_suffix(body.get("file_name") or "")
            if str(body.get("file_name") or "").strip()
            else settings_file_name.from_display_name(base.display_name))
    if problem := settings_file_name.problem(name):
        raise service_errors.RefusedError(problem)
    target = resolve(_folder_of(body), name)
    if choice == "copy":
        source = _source_of(body)
        if same_file(source, target):
            raise service_errors.RefusedError(t("error.launchers.copy_onto_itself"))
    others = users_of(target, besides=launcher_id)
    program_file = _program_file_of(target, base)
    exists = target.exists()
    own_target = (leaving_own and same_file(old, target)) and not others and not program_file
    if exists and not own_target and not body.get("replace"):
        return {"needs_replace": {"users": [one.display_name for one in others],
                                  "program_file": program_file, "path": str(target)}}

    before = {target: target.read_bytes() if exists else None}
    snapshot = _Snapshot(launcher_id, stored, before)
    backed_up = False
    if leaving_own and stored is not None:
        _backed_up(stored, t("said.settings_replaced") if own_target
                   else t("said.settings_left"))
        backed_up = True
    if exists and not own_target:
        copy_aside(target, REPLACE)
        backed_up = True
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if choice == "copy":
            data = _read(source)
            with staged_for(target) as staged:
                staged.write_bytes(data)
        else:
            _fresh_file(base, target)
        value = "" if program_file else str(target)
        _put(replace(base, owns_ini=not program_file, settings={**base.settings, KEY: value}))
    except BaseException:
        _put_back(snapshot)
        raise
    token = _remember(snapshot) if stored is not None or exists else ""
    return _answer(launcher_id, target, backed_up=backed_up, replaced=exists, undo=token)


def _pick(base: launchers.Launcher, stored: launchers.Launcher | None, old: Path | None,
          leaving_own: bool, body: dict[str, Any]) -> dict[str, Any]:
    named = str(body.get("existing") or "").strip()
    chosen = Path(named).expanduser() if named else None
    if chosen is None or not chosen.is_file():
        raise service_errors.RefusedError(t("error.launchers.pick_missing"))
    if stored is not None and same_file(chosen, old):
        return {"unchanged": True, "launcher": launcher_ops.described(stored)}
    program_file = _program_file_of(chosen, base)
    snapshot = _Snapshot(base.launcher_id, stored, {})
    backed_up = False
    if leaving_own and stored is not None:
        _backed_up(stored, t("said.settings_left"))
        backed_up = True
    _put(replace(base, owns_ini=False,
                 settings={**base.settings, KEY: "" if program_file else str(chosen)}))
    token = _remember(snapshot) if stored is not None else ""
    return _answer(base.launcher_id, chosen, backed_up=backed_up, replaced=False, undo=token)


def _answer(launcher_id: str, target: Path, *, backed_up: bool, replaced: bool,
            undo: str) -> dict[str, Any]:
    return {"launcher": launcher_ops.described(launcher_ops.launcher_or_refuse(launcher_id)),
            "file": str(target), "backed_up": backed_up, "replaced": replaced, "undo": undo}


def _put_back(snapshot: _Snapshot, *, later: bool = False) -> None:
    """The files and the launcher as the snapshot has them. `later` is an Undo, run after
    the launcher may have been edited again: only which file it plays with comes back, and
    a launcher removed since stays removed."""
    for path, data in snapshot.files.items():
        try:
            if data is None:
                path.unlink(missing_ok=True)
            else:
                with staged_for(path) as staged:
                    staged.write_bytes(data)
        except OSError:
            logger.warning("could not put %s back as it was", path, exc_info=True)
    store = launchers.get_launcher_store()
    current = store.get(snapshot.launcher_id)
    if snapshot.launcher is None:
        store.remove(snapshot.launcher_id)
    elif not later:
        store.put(snapshot.launcher)
    elif current is not None:
        was = dict(snapshot.launcher.settings)
        settings = {key: value for key, value in current.settings.items() if key != KEY}
        if KEY in was:
            settings[KEY] = was[KEY]
        store.put(replace(current, owns_ini=snapshot.launcher.owns_ini, settings=settings))


def undo(token: str) -> dict[str, Any]:
    """Put the launcher and its files back as they were before the change that gave
    `token`. A token works once."""
    with _undo_lock:
        snapshot = _undo.pop(str(token or ""), None)
    if snapshot is None:
        raise service_errors.NotFoundError(t("error.launchers.nothing_to_undo"))
    _put_back(snapshot, later=True)
    now = launchers.get_launcher_store().get(snapshot.launcher_id)
    return {"launcher_id": snapshot.launcher_id,
            "launcher": launcher_ops.described(now) if now else None}


def receive_file(launcher_id: str, data: bytes) -> dict[str, Any]:
    """A settings file another install sent along with this launcher. It goes where a copy
    made here would: VPinFE's settings folder, under the launcher's name, whatever it
    replaces kept in Backups first, except a file another launcher plays with: the name
    moves on to a free one instead, so that launcher keeps its settings. The launcher is
    already here; sending it comes first."""
    if len(data) > MAX_RECEIVED:
        raise service_errors.RefusedError(t("error.launchers.bad_file"))
    found = launcher_ops.launcher_or_refuse(launcher_id)
    name = (settings_file_name.from_display_name(found.display_name)
            or f"settings{settings_file_name.SUFFIX}")
    target = resolve(LAUNCHER_SETTINGS_DIR, name)
    if users_of(target, besides=launcher_id) or _program_file_of(target, found):
        name = _free_in(LAUNCHER_SETTINGS_DIR, name).name
    with tempfile.TemporaryDirectory() as folder:
        source = Path(folder) / "received.ini"
        source.write_bytes(data)
        return apply(launcher_id, {"choice": "copy", "copy_file": str(source),
                                   "file_name": name, "replace": True})


def make_own_copy(launcher_id: str) -> dict[str, Any]:
    """A launcher that shares its file gets a copy of its own in VPinFE's settings folder,
    named for the launcher. Answers `{"unchanged": true}` where nothing else plays with it,
    so a second press does not make a second copy."""
    found = launcher_ops.launcher_or_refuse(launcher_id)
    source = effective_file(found)
    if source is None or not source.is_file():
        raise service_errors.RefusedError(t("error.launchers.copy_source_missing"))
    if not sharing(found):
        return {"unchanged": True, "launcher": launcher_ops.described(found)}
    name = (settings_file_name.from_display_name(found.display_name)
            or f"settings{settings_file_name.SUFFIX}")
    target = _free_in(LAUNCHER_SETTINGS_DIR, name)
    data = _read(source)
    snapshot = _Snapshot(launcher_id, found, {target: None})
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with staged_for(target) as staged:
            staged.write_bytes(data)
        _put(replace(found, owns_ini=True, settings={**found.settings, KEY: str(target)}))
    except BaseException:
        _put_back(snapshot)
        raise
    return _answer(launcher_id, target, backed_up=False, replaced=False,
                   undo=_remember(snapshot))


def _free_in(folder: Path, name: str) -> Path:
    found = resolve(folder, name)
    stem, count = name[:-len(settings_file_name.SUFFIX)], 1
    while found.exists():
        count += 1
        found = resolve(folder, f"{stem} {count}{settings_file_name.SUFFIX}")
    return found


def follow_rename(launcher_id: str, was: str) -> dict[str, Any]:
    """After a launcher is renamed from `was`: a file VPinFE keeps for it, in VPinFE's own
    folder under the old name, takes the new name. One somebody put elsewhere or named
    themselves keeps its name, and so does one where the new name cannot be a file's."""
    found = launcher_ops.launcher_or_refuse(launcher_id)
    current = effective_file(found)
    wanted = settings_file_name.from_display_name(found.display_name)
    if (not found.owns_ini or current is None or not wanted or not current.is_file()
            or not same_file(current.parent, LAUNCHER_SETTINGS_DIR)
            or current.name != settings_file_name.from_display_name(was)
            or users_of(current, besides=launcher_id)):
        return {"renamed": False, "file": str(current or "")}
    target = current.with_name(wanted)
    if target == current or (target.exists() and not same_file(target, current)):
        return {"renamed": False, "file": str(current)}
    try:
        os.replace(current, target)
    except OSError:
        logger.warning("could not rename %s for %s", current, found.display_name,
                       exc_info=True)
        return {"renamed": False, "file": str(current)}
    try:
        _put(replace(found, settings={**found.settings, KEY: str(target)}))
    except BaseException:
        os.replace(target, current)
        raise
    return {"renamed": True, "file": str(target)}


def delete_own_file(launcher_id: str) -> dict[str, Any]:
    """Delete the file VPinFE made for a launcher, after keeping it in Backups. Done only
    for one nobody else plays with, which is VPinFE's own to delete."""
    found = launcher_ops.launcher_or_refuse(launcher_id)
    path = effective_file(found)
    if (not found.owns_ini or path is None or not path.is_file()
            or users_of(path, besides=launcher_id)):
        return {"deleted": False}
    _backed_up(found, t("said.settings_removed"))
    path.unlink()
    return {"deleted": True, "file": str(path)}


def kept_file_of(launcher: launchers.Launcher) -> dict[str, Any]:
    """Whether Remove can offer to delete this launcher's file: VPinFE made it and nobody
    else plays with it."""
    path = effective_file(launcher)
    offered = bool(launcher.owns_ini and path is not None and path.is_file()
                   and not users_of(path, besides=launcher.launcher_id))
    return {"offer_delete": offered, "file": str(path) if offered and path else ""}


def places(app_id: str, settings: dict[str, Any]) -> list[dict[str, Any]]:
    """The folders a settings file is looked for first: VPinFE's own, then the program's,
    which is the one the version it runs keeps its preferences in."""
    found = [{"name": t("word.vpinfe_settings"), "path": str(LAUNCHER_SETTINGS_DIR),
              "default": True}]
    own = program_own_file(app_id, settings)
    if own is not None:
        version = own.parent.name
        name = apps.app_name(app_id)
        found.append({"name": f"{name} {version}" if version[:1].isdigit() else name,
                      "path": str(own.parent), "default": False})
    return found
