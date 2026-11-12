"""The configured ways this install runs an app, and which table uses which.

A launcher is user data: a name, the app it wraps, whether it is enabled, a value for each
field that app declares, and the tables that deviate from the default. An install ships
one, seeded from the configuration it already had. Duplicating it and pointing the copy
at another ini is the case this exists for.

They are the `launchers` list of `vpinfe.json`. `LauncherStore` is a view over it that
answers in `Launcher`s.

**Per install.** A binary path is a fact about one machine, so a launcher belongs to the
machine that runs it. Sharing across cabinets is a copy that keeps the id, not shared
storage - the same launcher then exists on every machine under one name, and the tables
listed on it mean the same thing everywhere.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from common import apps
from common.config import config_schema
from common.config.config_store import ConfigStore
from common.config.paths import get_ini_config
from common.devices.install_identity import mint_id
from common.i18n import t
from common.settings import config_service

logger = logging.getLogger("vpinfe.common.games.launchers")

LAUNCHERS_KEY = "launchers"


def mint_launcher_id() -> str:
    """An id for a new launcher. The generator every id in this project uses, so an id
    minted here and one that arrives from another machine are indistinguishable - which
    is what lets a launcher be copied to a cabinet without being renumbered."""
    return mint_id()


def same_name(one: str, other: str) -> bool:
    """Whether two launcher names read as one to somebody looking at a picker."""
    return one.strip().casefold() == other.strip().casefold()


def free_name(wanted: str, taken: Iterable[str]) -> str:
    """`wanted`, or the first of `wanted 2`, `wanted 3`... that no name in `taken` is."""
    held = list(taken)
    found, count = wanted.strip(), 1
    while any(same_name(found, one) for one in held):
        count += 1
        found = f"{wanted.strip()} {count}"
    return found


@dataclass(frozen=True)
class Launcher:
    """One configured way of running an app.

    `settings` is keyed by the field names its app declares, so two launchers of the same
    app hold the same names and an editor can be generated rather than written.

    `owns_ini` says whether VPinFE created the file `ini_path` names. It is a field rather
    than something inferred from the path because removing a launcher offers to delete
    that file, and the shipped launcher points at Visual Pinball's own `VPinballX.ini` -
    guessing wrong there deletes a person's real configuration.
    """

    launcher_id: str
    app: str = ""
    display_name: str = ""
    enabled: bool = True
    owns_ini: bool = False
    settings: dict[str, Any] = field(default_factory=dict)

    def value(self, key: str) -> Any:
        """One setting, falling back to what its app declares. A launcher that does not
        carry a field is running on the default, so answering blank would say the
        opposite."""
        if key in self.settings:
            return self.settings[key]
        declared = next((f for f in self.fields() if f.key == key), None)
        return declared.default if declared is not None else ""

    def in_effect(self, key: str) -> Any:
        """One setting as the program will see it: an empty one is what the app says it
        stands for on this machine, such as Visual Pinball's own settings file."""
        found = self.value(key)
        if found not in ("", None):
            return found
        left_empty = getattr(getattr(apps.get(self.app), "config", None), "left_empty", None)
        if not callable(left_empty):
            return found
        standing = left_empty({f.key: self.value(f.key) for f in self.fields()}).get(key)
        return standing.path if standing is not None and standing.exists else found

    def fields(self) -> tuple[apps.Field, ...]:
        """What configuring this launcher takes: what its app declares, and what every
        launcher has whatever it wraps."""
        return apps.launcher_fields(self.app) or apps.OWN_FIELDS

    def has_program(self) -> bool:
        """Whether every path to a program this launcher runs has been filled in."""
        return all(str(self.value(field.key) or "").strip()
                   for field in self.fields() if field.path == "exe")

    def as_dict(self) -> dict[str, Any]:
        return {"launcher_id": self.launcher_id, "app": self.app,
                "display_name": self.display_name, "enabled": self.enabled,
                "owns_ini": self.owns_ini, "settings": dict(self.settings)}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Launcher | None:
        launcher_id = str(raw.get("launcher_id", "") or "").strip()
        if not launcher_id:
            return None
        return cls(
            launcher_id=launcher_id,
            app=str(raw.get("app", "") or ""),
            display_name=str(raw.get("display_name", "") or ""),
            # Absent reads as enabled: a file written before the flag existed described
            # launchers that were all in use.
            enabled=bool(raw.get("enabled", True)),
            owns_ini=bool(raw.get("owns_ini", False)),
            settings=dict(raw.get("settings") or {}),
        )

    def as_item(self, tables: Iterable[str] = ()) -> dict[str, Any]:
        """The row as `launchers` holds it: the tables that run on it are its own."""
        return {"id": self.launcher_id, "app": self.app, "name": self.display_name,
                "enabled": self.enabled, "owns_ini": self.owns_ini,
                "tables": list(tables), "settings": dict(self.settings)}

    @classmethod
    def from_item(cls, raw: dict[str, Any]) -> Launcher | None:
        return cls.from_dict({
            "launcher_id": raw.get("id"), "app": raw.get("app"),
            "display_name": raw.get("name"), "enabled": raw.get("enabled", True),
            "owns_ini": raw.get("owns_ini", False), "settings": raw.get("settings")})


def _item_tables(item: dict[str, Any]) -> list[str]:
    return [str(one) for one in item.get("tables") or []]


class LauncherStore:
    """Every launcher this install has, and which table uses which."""

    def __init__(self, folder: Path | str | None = None) -> None:
        """Without `folder` this is the install's own. With one, the list lives in its
        `vpinfe.json`."""
        self._folder = Path(folder) if folder is not None else None
        self._config: ConfigStore | None = None
        self._lock = threading.RLock()

    def _core(self) -> ConfigStore:
        if self._folder is None:
            return get_ini_config()
        if self._config is None:
            self._folder.mkdir(parents=True, exist_ok=True)
            self._config = ConfigStore(str(self._folder / "vpinfe.json"))
        else:
            self._config.reread()
        return self._config

    @staticmethod
    def _items(config: ConfigStore) -> list[dict[str, Any]]:
        return [one for one in config.get_list(LAUNCHERS_KEY) if isinstance(one, dict)]

    @staticmethod
    def _write(config: ConfigStore, items: list[dict[str, Any]]) -> None:
        config.set_list(LAUNCHERS_KEY, items)
        config_service.written(config, [LAUNCHERS_KEY])

    # -- reading -------------------------------------------------------------

    def launchers(self) -> list[Launcher]:
        """Every launcher, in the order the list holds them."""
        with self._lock:
            return [one for one in (Launcher.from_item(raw)
                                    for raw in self._items(self._core())) if one is not None]

    def get(self, launcher_id: str) -> Launcher | None:
        wanted = (launcher_id or "").strip()
        if not wanted:
            return None
        return next((one for one in self.launchers()
                     if one.launcher_id == wanted), None)

    def mappings(self) -> dict[str, str]:
        """Table id to launcher id, and only for the tables that deviate.

        A table with no entry runs on the default, so changing the default re-points
        everything that never asked for anything else.
        """
        with self._lock:
            found: dict[str, str] = {}
            for item in self._items(self._core()):
                for table in _item_tables(item):
                    found.setdefault(table, str(item.get("id")))
            return found

    def mapped(self, table_id: str) -> str:
        return self.mappings().get(str(table_id or "").strip(), "")

    # -- writing -------------------------------------------------------------

    def save(self, launchers: list[Launcher], mappings: dict[str, str]) -> None:
        """The whole list, in the order given, with each table on the launcher that
        `mappings` names."""
        with config_service.writing(), self._lock:
            config = self._core()
            self._write(config, [
                one.as_item(table for table, to in mappings.items() if to == one.launcher_id)
                for one in launchers])

    def put(self, launcher: Launcher) -> Launcher:
        """Add one, or replace the one with its id, keeping its place and its tables.

        Replace rather than merge, and by id rather than by position: this is also how a
        launcher copied from another machine lands, and that copy is the whole launcher.
        """
        with config_service.writing(), self._lock:
            config = self._core()
            items = self._items(config)
            at = next((i for i, one in enumerate(items)
                       if one.get("id") == launcher.launcher_id), None)
            if at is None:
                items.append(launcher.as_item())
            else:
                items[at] = launcher.as_item(_item_tables(items[at]))
            self._write(config, items)
            return launcher

    def to_front(self, launcher_id: str) -> bool:
        """Move one to the head of the list, which makes it its app's default while it
        is switched on. False where there is no such launcher."""
        wanted = (launcher_id or "").strip()
        with config_service.writing(), self._lock:
            config = self._core()
            items = self._items(config)
            found = [one for one in items if one.get("id") == wanted]
            if not found:
                return False
            self._write(config, found + [one for one in items if one.get("id") != wanted])
            return True

    def remove(self, launcher_id: str) -> bool:
        """Forget a launcher. The tables listed on it go with it, back to the default."""
        wanted = (launcher_id or "").strip()
        with config_service.writing(), self._lock:
            config = self._core()
            items = self._items(config)
            kept = [one for one in items if one.get("id") != wanted]
            if len(kept) == len(items):
                return False
            self._write(config, kept)
            return True

    def assign(self, table_id: str, launcher_id: str) -> None:
        """Point a table at a launcher, or clear it with an empty id.

        Clearing removes the entry rather than storing a blank, because an absent entry
        already means "the default" and two spellings of one state is what makes a reader
        ask which is which.
        """
        table = str(table_id or "").strip()
        if not table:
            return
        wanted = str(launcher_id or "").strip()
        with config_service.writing(), self._lock:
            config = self._core()
            items = self._items(config)
            moved = [{**one, "tables": [
                *(name for name in _item_tables(one) if name != table),
                *([table] if wanted and one.get("id") == wanted else [])]}
                for one in items]
            if moved != items:
                self._write(config, moved)


def _one_table_each(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A table is listed on one launcher: the first that names it keeps it."""
    seen: set[str] = set()
    out = []
    for item in items:
        tables = [name for name in _item_tables(item) if name not in seen]
        seen.update(tables)
        out.append({**item, "tables": tables} if tables != _item_tables(item) else item)
    return out


def _apart_from_tables(item: dict[str, Any] | None) -> dict[str, Any] | None:
    return None if item is None else {k: v for k, v in item.items() if k != "tables"}


def _refuse_bad_launchers(items: list[dict[str, Any]], held: list[dict[str, Any]]) -> None:
    """A write that names an app this install lacks, a name another launcher has, Arguments
    that cannot be read, or switches off a launcher whose tables nothing could play is
    refused by name.

    Only items that are new or changed apart from their tables are asked: a launcher whose
    extension was switched off since it was written would otherwise block every other write,
    including moving a table off it.
    """
    kept = {one.get("id"): one for one in held}
    names = [(one.get("id"), str(one.get("name") or "").strip()
              or apps.app_name(str(one.get("app")))) for one in items]
    for item in items:
        if _apart_from_tables(item) == _apart_from_tables(kept.get(item.get("id"))):
            continue
        app_id = str(item.get("app") or "")
        if apps.get(app_id) is None:
            raise config_schema.ConfigListError(t(
                "error.launchers.unknown_app", app_id=app_id,
                apps=", ".join(app.id for app in apps.all_apps())))
        name = str(item.get("name") or "").strip() or apps.app_name(app_id)
        if any(same_name(name, other) for launcher_id, other in names
               if launcher_id != item.get("id")):
            raise config_schema.ConfigListError(t("error.launchers.name_taken", name=name))
        before = kept.get(item.get("id"))
        typed = str((item.get("settings") or {}).get("args") or "")
        if typed != str(((before or {}).get("settings") or {}).get("args") or ""):
            problem = apps.arguments_problem(app_id, typed)
            if problem:
                raise config_schema.ConfigListError(problem)
        if before is not None and before.get("enabled") and not item.get("enabled"):
            from common.games import launcher_ops

            launcher = Launcher.from_item(before)
            refused = launcher_ops.switch_off_refusal(launcher) if launcher else ""
            if refused:
                raise config_schema.ConfigListError(refused)


def _name_the_blank(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [item if str(item.get("name") or "").strip()
            else {**item, "name": apps.app_name(str(item.get("app")))} for item in items]


config_schema.register_validator(LAUNCHERS_KEY, _refuse_bad_launchers)
config_schema.register_normalizer(
    LAUNCHERS_KEY, lambda items: _one_table_each(_name_the_blank(items)))


def default_for(app_id: str, launchers: Iterable[Launcher]) -> Launcher | None:
    """The launcher a table of this app runs on when nothing says otherwise.

    The first enabled one, so the file's order answers "which is the default" rather than
    a flag that two launchers could carry at once.
    """
    return next((one for one in launchers
                 if one.app == app_id and one.enabled), None)


def playable_formats(launchers: Iterable[Launcher]) -> tuple[str, ...]:
    """The VPS formats the enabled launchers play, each once, in the order they are found.

    Empty where no launcher is enabled and where an enabled one's app declares none, which
    plays every release: either way there is no format to narrow to.
    """
    found: list[str] = []
    for one in launchers:
        if not one.enabled:
            continue
        declared = apps.vps_formats(one.app)
        if not declared:
            return ()
        found.extend(token for token in declared if token not in found)
    return tuple(found)


def launcher_for_table(filename: str, table_id: str, launchers: Iterable[Launcher],
                       mappings: dict[str, str]) -> Launcher | None:
    """Which launcher plays this file. For callers that hold a name off a directory
    listing; anything holding an entry knows its app and asks the next one down."""
    app = apps.app_for(filename)
    return launcher_for_entry(app.id if app is not None else "", table_id,
                              launchers, mappings)


def launcher_for_entry(app_id: str, table_id: str, launchers: Iterable[Launcher],
                       mappings: dict[str, str]) -> Launcher | None:
    """Which launcher plays this entry: what it names, then the default for its app.

    By app rather than by filename, because an entry with no file has no suffix to
    resolve through - a key says nothing about whose it is, so the entry declares it.

    One function, so the grid's effective-launcher column and the launch path can never
    disagree.

    A table naming a launcher that is switched off falls back rather than refusing, and
    the caller is expected to say so: the fallback is honest, and silence about it is
    what turns a configuration choice into a mystery.
    """
    app = apps.get(str(app_id or "").strip())
    if app is None:
        return None
    wanted = str(mappings.get(str(table_id or "").strip(), "") or "")
    if wanted:
        named = next((one for one in launchers if one.launcher_id == wanted), None)
        if named is not None and named.enabled:
            return named
    return default_for(app.id, launchers)


def default_launcher(app_id: str = "vpx") -> Launcher | None:
    """This install's launcher for an app, for anything that needs Visual Pinball without
    having a table in hand - a capability probe, or finding the ROM library beside it."""
    return default_for(app_id, get_launcher_store().launchers())


def default_value(key: str, app_id: str = "vpx") -> str:
    """One field of that launcher, or "" where the install has none. Answering blank for
    a missing launcher is right: nothing is configured, which is what the caller is
    usually about to report."""
    found = default_launcher(app_id)
    return str(found.in_effect(key) or "") if found is not None else ""


def replace_settings(launcher: Launcher, **values: Any) -> Launcher:
    """A launcher with some of its settings changed, leaving the rest alone."""
    return replace(launcher, settings={**launcher.settings, **values})


_store: LauncherStore | None = None


def get_launcher_store() -> LauncherStore:
    """This install's launchers. One per process, the way the device registry is."""
    global _store
    if _store is None:
        _store = LauncherStore()
    return _store
