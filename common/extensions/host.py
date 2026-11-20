"""Loading extensions, and keeping a broken one from taking core with it.

Every step is contained: reading a manifest, importing the package and calling
`register(ctx)` are each somebody else's code, and the answer to any of them failing is
one extension that is not running and a line saying which and why.
"""

from __future__ import annotations

import functools
import importlib.util
import logging
import re
import sys
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from platform import freedesktop_os_release
from typing import Any

from common import events as core_events
from common import i18n, tokens
from common.config.paths import CONFIG_DIR, OFFICIAL_EXTENSIONS_DIR, bundled, get_ini_config
from common.devices import install_identity
from common.online import sources
from common.security import accepted
from common.values import newer_version, parse_version

from . import catalogs, contributions, reach
from . import fingerprint as fingerprints
from .context import ExtensionApps, ExtensionContext, check_play, check_references
from .contract import (
    MANIFEST_NAME,
    ContractError,
    Manifest,
    ManifestError,
    check_picture,
    read_manifest,
)
from .pictures import is_openable
from .store import ExtensionStore, get_extension_store

logger = logging.getLogger("vpinfe.common.extensions.host")

# Where people put their own, where VPinFE installs the official ones it takes from the
# list, and where the build keeps the ones it ships. A folder somebody put under the same
# name as an official extension is looked at first, and it is never official.
INSTALLED_DIR = CONFIG_DIR / "extensions"
OFFICIAL_DIR = OFFICIAL_EXTENSIONS_DIR
BUNDLED_DIR = bundled("extensions")
SEARCH_PATH = (INSTALLED_DIR, OFFICIAL_DIR, BUNDLED_DIR)

# Running.
# The names core's rail already uses, by the keys of `manager.section.*`.
CORE_SECTIONS = ("overview", "games", "tables", "media", "assets", "collections", "tags",
                 "discovery", "players", "launchers", "themes", "settings", "devices", "extensions",
                 "metrics", "logs", "jobs", "backups", "about", "library", "frontend",
                 "community", "system")

# Read, and waiting for the second pass of the start to run it.
PENDING = "pending"
LOADED = "loaded"
# Never got as far as running: a manifest that would not parse, a package that would not
# import, or a `register` that raised.
FAILED = "failed"
# Ran, then something it owns threw. For this run only.
DISABLED = "disabled"
# Switched off by the user, or not for this machine.
OFF = "off"
SWITCHED_OFF = "extension.reason.switched_off"
# Switched on since this run started.
STARTS_AT_RESTART = "extension.reason.pending_restart"
WRONG_PLATFORM = "extension.reason.wrong_platform"
LACKS_FEATURES = "extension.reason.lacks_features"
# Not shipped with VPinFE and not accepted by the Owner, or accepted before it declared
# something it declares now. Never imported while this is the reason.
NOT_ACCEPTED = "extension.reason.not_accepted"

# A manifest that says the team made it.
_TEAM_REPOSITORY = re.compile(r"^https://github\.com/vpinfe/", re.IGNORECASE)

_PLATFORMS = {"linux": "linux", "win32": "windows", "darwin": "macos"}
PLATFORM_NAMES = {"linux": "extension.platform.linux",
                  "windows": "extension.platform.windows",
                  "macos": "extension.platform.macos"}


def this_platform() -> str:
    return _PLATFORMS.get(sys.platform, sys.platform)


@functools.cache
def _os_release() -> dict[str, str]:
    if not sys.platform.startswith("linux"):
        return {}
    try:
        return dict(freedesktop_os_release())
    except OSError:
        return {}


def this_distribution() -> tuple[str, str]:
    """This Linux's `ID` and `NAME` from os-release: `("vpinos", "VPinOS")`. Empty where the
    file is missing, and everywhere off Linux."""
    found = _os_release()
    return found.get("ID", "").lower(), found.get("NAME", "")


def _read(value: str | tuple[str, ...]) -> str:
    return ", ".join(i18n.t(key) for key in value) if isinstance(value, tuple) else value


@dataclass
class Record:
    """One extension, and what became of it."""

    name: str
    directory: Path
    manifest: Manifest | None = None
    # What the person set, which is not whether it is running.
    enabled: bool = True
    state: str = FAILED
    why: str = ""
    why_values: dict[str, str | tuple[str, ...]] = field(default_factory=dict)
    # (router, scope), collected at registration and mounted once by the API.
    routers: list[tuple[Any, str]] = field(default_factory=list)
    subscriptions: list[tuple[str, Any]] = field(default_factory=list)
    apps: ExtensionApps | None = None
    files: Any = None
    actions: list[dict] = field(default_factory=list)
    surfaces: dict = field(default_factory=dict)
    declared_lists: list[dict] = field(default_factory=list)
    declared_links: list[dict] = field(default_factory=list)
    declared_groups: list[dict] = field(default_factory=list)
    declared_library_verbs: list[dict] = field(default_factory=list)
    declared_sections: list[dict] = field(default_factory=list)
    declared_panel: dict = field(default_factory=dict)
    declared_needs: list[dict] = field(default_factory=list)
    # The registration core calls the functions of; None while it is not running.
    context: ExtensionContext | None = None
    # Remembered between runs, so a stopped extension's page can say it has settings.
    has_settings: bool = False
    # {base, label, cards, marker} when a player can hold an account with it.
    account: dict = field(default_factory=dict)
    # What `register` raised, for `--check-extension`; the log has the traceback.
    error: str = ""
    # For a built-in that has an installed copy of the same name waiting to be accepted:
    # that copy. It runs only once accepted, at the next start.
    waiting: Record | None = None
    # Hash of every file in its folder, and the files that are not what VPinFE shipped or
    # installed. Set once, by `examine`.
    fingerprint: str = ""
    differs: list[str] = field(default_factory=list)
    # Its files were replaced or removed while it runs. The code in memory is the old
    # code until the next start.
    files_changed: bool = False
    _origin: str = ""

    @property
    def running(self) -> bool:
        return self.state == LOADED

    @property
    def reason(self) -> str:
        if not self.why:
            return ""
        return i18n.t(self.why, **{slot: _read(value)
                                   for slot, value in self.why_values.items()})

    def became(self, state: str, why: str = "", **values: str | tuple[str, ...]) -> None:
        self.state, self.why, self.why_values = state, why, values

    def said(self, literal: str, key: str, fallback: str = "") -> tuple[str, str]:
        """Words it declared: `literal` as written, else `ext.<name>.<key>` from its own
        catalog, else `fallback`. With the key they came from."""
        return i18n.literal_or(literal, f"ext.{self.name}.{key}", fallback=fallback)

    @property
    def display_name(self) -> str:
        return self.said(self.manifest.display_name if self.manifest else "", "name",
                         self.name)[0]

    @property
    def description(self) -> str:
        return self.said(self.manifest.description if self.manifest else "",
                         "description")[0]

    def lists(self) -> list[dict]:
        """Its lists, in the language now set."""
        return [self._list(one) for one in self.declared_lists]

    def groups(self) -> list[dict]:
        """Its panel groups, headed in the language now set."""
        return [{**one, "title": self.said(one["title"], f"group.{one['subject']}.title",
                                           self.display_name)[0]}
                for one in self.declared_groups]

    def library_verbs(self) -> list[dict]:
        """Its entries for the Games and Tables menus, worded in the language now set."""
        found = []
        for one in self.declared_library_verbs:
            at = f"verb.{one['subject']}.{one['key']}"
            found.append({**one,
                          "label": self.said(one["label"], f"{at}.label", one["key"])[0],
                          "tip": self.said("", f"{at}.tip")[0],
                          "confirm_key": i18n.first_key(f"ext.{self.name}.{at}.confirm")})
        return found

    @property
    def bundled(self) -> bool:
        """Whether it ships with VPinFE rather than being installed by the person."""
        return BUNDLED_DIR in self.directory.parents

    @property
    def origin(self) -> str:
        """Where it came from, decided by where its files are and whether they are still
        what VPinFE wrote there, never by what the manifest says: SHIPPED or OFFICIAL when
        they are, CHANGED when they are not, REPLACES for a folder that takes an official
        extension's name, IN_FOLDER for any other. One the list has dropped is IN_FOLDER from
        the check that learned it, not from the next start."""
        if self.left_list:
            return accepted.IN_FOLDER
        return self._origin or self.examine()

    @property
    def comes_with_vpinfe(self) -> bool:
        """Whether the build ships an extension of this name, so it still comes with VPinFE
        after an update from the list put a newer copy beside it."""
        return (BUNDLED_DIR / self.name).is_dir()

    @property
    def from_list(self) -> bool:
        """Whether VPinFE installed it from the Extension List, into its own folder."""
        return OFFICIAL_DIR in self.directory.parents

    @property
    def left_list(self) -> bool:
        """Whether it was installed from the list and the list, as last fetched, no longer
        names it. Offline, or before any fetch, nothing has left."""
        names = fingerprints.held_names()
        return self.from_list and names is not None and self.name not in names

    @property
    def official(self) -> bool:
        return accepted.is_official(self.origin)

    @property
    def switch_key(self) -> str:
        """What its switch is kept under. A copy has one of its own, so switching it off
        lets the official extension of that name run."""
        return f"{self.name}.copy" if self.origin == accepted.REPLACES else self.name

    def examine(self) -> str:
        """Hash its folder and settle its origin. Local only: the build's list, the receipt
        VPinFE kept, or the names it knows."""
        files = fingerprints.hash_files(self.directory)
        self.fingerprint, self.differs = fingerprints.digest(files), []
        if self.bundled:
            built = fingerprints.read_build_list(BUNDLED_DIR)
            self._origin = (
                accepted.SHIPPED
                if built is None
                else self._against(built.get(self.name), files, accepted.SHIPPED)
            )
        elif self.from_list:
            self._origin = (
                accepted.IN_FOLDER
                if self.left_list
                else self._against(fingerprints.receipt(self.name), files, accepted.OFFICIAL)
            )
        elif self.name in official_names():
            self._origin = accepted.REPLACES
        else:
            self._origin = accepted.IN_FOLDER
        return self._origin

    def _against(
        self, expected: fingerprints.Receipt | None, files: dict[str, str], origin: str
    ) -> str:
        if expected is not None:
            self.differs = fingerprints.differences(expected.files, files)
        else:
            self.differs = sorted(files)
        return accepted.CHANGED if self.differs else origin

    @property
    def claims_team(self) -> bool:
        """Whether an extension that is not VPinFE's says it is: the team as its author or
        a repository of the team's as its source. Never true of one VPinFE stands behind."""
        manifest = self.manifest
        return (
            manifest is not None
            and not self.official
            and (
                "vpinfe" in manifest.author.casefold()
                or bool(_TEAM_REPOSITORY.match(manifest.source))
            )
        )

    @property
    def repository_and_release(self) -> tuple[str, str]:
        """Where it came from and which release: the build's or VPinFE's own record for an
        included or listed extension, and what its manifest says for any other."""
        kept = None
        if self.bundled:
            kept = (fingerprints.read_build_list(BUNDLED_DIR) or {}).get(self.name)
        elif self.from_list:
            kept = fingerprints.receipt(self.name)
        if kept is not None:
            return kept.repository, kept.release
        manifest = self.manifest
        return (manifest.source, manifest.version) if manifest else ("", "")

    @property
    def folder(self) -> str:
        """The folder it was found in, which an acceptance is keyed to."""
        return str(self.directory.parent)

    @property
    def path(self) -> str:
        """The extension's own folder."""
        return str(self.directory)

    @property
    def picture_shown(self) -> bool:
        """Whether its page draws the picture its manifest names: shipped or accepted, and
        the file still there."""
        manifest = self.manifest
        if manifest is None or not manifest.picture:
            return False
        return (self.official or self.is_accepted()) and (
            self.directory / manifest.picture
        ).is_file()

    def uses(self) -> list[str]:
        """What it says it uses: the core scopes and capabilities it declares, and
        `play:<surface>` for each surface a Remote can open."""
        if self.manifest is None:
            return []
        return [*self.manifest.scopes, *self.manifest.capabilities,
                *(f"play:{one}" for one in self.manifest.play)]

    def is_accepted(self) -> bool:
        """Whether it may run: shipped, or accepted from here with all it declares."""
        if self.manifest is None:
            return False
        return accepted.extension_accepted(self.name, self.origin, self.manifest.scopes,
                                           self.manifest.capabilities, self.folder,
                                           self.manifest.play)

    def trust(self) -> dict[str, Any]:
        """Where it came from, whether the Owner has accepted it, and what it declares
        that the acceptance did not cover."""
        if self.manifest is None:
            return {
                "origin": self.origin,
                "accepted": False,
                "folder": self.folder,
                "new_access": [],
                "accepted_before": False,
                "fingerprint": self.fingerprint,
                "changed_files": self.differs,
            }
        return {
            "origin": self.origin,
            "accepted": self.is_accepted(),
            "folder": self.folder,
            "uses": self.uses(),
            "fingerprint": self.fingerprint,
            "changed_files": self.differs,
            "claims_team": self.claims_team,
            "new_access": accepted.new_access(
                self.name,
                self.origin,
                self.manifest.scopes,
                self.manifest.capabilities,
                self.folder,
                self.manifest.play,
            ),
            "accepted_before": accepted.accepted_before(self.name, self.origin, self.folder),
            "accepted_version": accepted.accepted_version(self.name, self.origin, self.folder),
        }

    def sections(self) -> list[dict]:
        """Its sections, in the order declared, titled in the language now set."""
        return [{**one, "title": self.said(one["title"], f"section.{one['key']}.title",
                                           one["key"])[0]}
                for one in self.declared_sections]

    def panels(self) -> list[dict]:
        """Its panel as an entry in the navigation, as a list of one, or none where it
        names no area."""
        if not self.declared_panel:
            return []
        one = self.declared_panel
        return [{**one, "title": self.said(one["title"], "panel.title",
                                           self.display_name)[0]}]

    def links(self) -> list[dict]:
        """Its links, with the address each answers now and the words in the language now
        set. One whose address is not a web address, or whose function raises, is left
        out: the page draws the rest."""
        ui = self.context.ui if self.context is not None else None
        found = []
        for declared in self.declared_links:
            key = declared["key"]
            url = self._address(ui.link_addresses.get(key) if ui else None, key)
            if not url:
                continue
            under = f"link.{key}"
            found.append({**declared, "url": url,
                          "label": self.said("", f"{under}.label", key)[0],
                          "tip": self.said("", f"{under}.tip")[0]})
        return found

    def _address(self, given: Any, key: str) -> str:
        try:
            url = given() if callable(given) else given
        except Exception:
            logger.exception("Extension %s link %s did not answer an address", self.name, key)
            return ""
        return url if isinstance(url, str) and is_openable(url) else ""

    def _list(self, declared: dict) -> dict:
        under = f"list.{declared['key']}"
        return {
            **declared,
            "title": self.said(declared["title"], f"{under}.title", declared["key"])[0],
            "columns": [{**one,
                         "header": self.said(one["header"],
                                             f"{under}.column.{one['field']}.header",
                                             one["field"])[0],
                         "help": self.said(one["help"],
                                           f"{under}.column.{one['field']}.help")[0],
                         "states": self._states(under, one)}
                        for one in declared["columns"]],
            "views": [{**one,
                       "name": self.said(one["name"], f"{under}.view.{one['key']}.name",
                                         one["key"])[0],
                       "help": self.said(one["help"], f"{under}.view.{one['key']}.help")[0]}
                      for one in declared["views"]],
            "verbs": [self._verb(under, one) for one in declared["verbs"]],
            "add": [{"action": key, "label": self._action_label(key)}
                    for key in declared["add"]],
        }

    def _states(self, under: str, column: dict) -> dict:
        at = f"{under}.column.{column['field']}.state"
        return {value: {**look,
                        "label": self.said("", f"{at}.{value}", value)[0],
                        "why": self.said("", f"{at}.{value}.why")[0]}
                for value, look in column["states"].items()}

    def _verb(self, under: str, declared: dict) -> dict:
        at = f"{under}.verb.{declared['key']}"
        return {**declared,
                "label": self.said(declared["label"], f"{at}.label", declared["key"])[0],
                "tip": self.said("", f"{at}.tip")[0],
                "needs_reason": self.said("", f"{at}.needs")[0],
                "confirm_key": i18n.first_key(f"ext.{self.name}.{at}.confirm")}

    def _action_label(self, key: str) -> str:
        declared = next((one for one in self.actions if one["key"] == key), {})
        return self.said(declared.get("label") or "", f"action.{key}.label", key)[0]

    def _action(self, declared: dict) -> dict:
        key = declared["key"]
        label, label_key = self.said(declared["label"], f"action.{key}.label", key)
        return {**declared, "label": label, "label_key": label_key,
                "description": self.said(declared["description"],
                                         f"action.{key}.description")[0]}

    def _waiting(self) -> dict[str, Any] | None:
        """The installed copy that would replace this built-in, once accepted."""
        copy = self.waiting
        if copy is None or copy.manifest is None:
            return None
        return {"version": copy.manifest.version, "author": copy.manifest.author,
                "source": copy.manifest.source, "folder": copy.folder, "path": copy.path,
                "uses": copy.uses(), "state": copy.state, "reason_key": copy.why,
                "trust": copy.trust()}

    def account_declared(self) -> dict:
        """Its account, in the language now set. Empty when it offers none."""
        if not self.account:
            return {}
        consent = list(self.account.get("consent") or [])
        if self.account.get("sends_picture"):
            consent.append(i18n.t("extension.account.sends_picture"))
        return {**self.account, "consent": consent,
                "label": self.said(self.account["label"], "account.label",
                                   self.display_name)[0],
                "share_help": self.said("", "account.share.help")[0]}

    def as_dict(self) -> dict[str, Any]:
        found = self.manifest.as_dict() if self.manifest else {"name": self.name}
        return {**found, "display_name": self.display_name,
                "description": self.description, "enabled": self.enabled,
                "state": self.state, "reason": self.reason, "reason_key": self.why,
                "has_settings": self.has_settings,
                "path": self.path, "picture_shown": self.picture_shown,
                "routes": [scope for _router, scope in self.routers],
                # Only while it is running: an action on an extension that is not
                # there would draw a button that refuses.
                "actions": ([self._action(one) for one in self.actions]
                            if self.running else []),
                "surfaces": dict(self.surfaces) if self.running else {},
                "lists": self.lists() if self.running else [],
                "links": self.links() if self.running else [],
                "groups": self.groups() if self.running else [],
                "library_verbs": self.library_verbs() if self.running else [],
                "bundled": self.bundled,
                "comes_with_vpinfe": self.comes_with_vpinfe,
                "from_list": self.from_list,
                "left_list": self.left_list,
                "files_changed": self.files_changed,
                "origin": self.origin,
                "trust": self.trust(),
                "waiting": self._waiting(),
                "sections": self.sections() if self.running else [],
                "panels": self.panels() if self.running else [],
                "needs": [dict(one) for one in self.declared_needs] if self.running else [],
                "account": self.account_declared() if self.running else {}}


def _newest_official_first(group: list[Record]) -> list[Record]:
    """The copies of one name in the order they are tried. The search order stands, except
    that the copy the build ships runs ahead of one installed from the list when it is the
    newer, so a VPinFE update never leaves an older extension running."""
    first = group[0]
    if first.origin != accepted.OFFICIAL or first.manifest is None:
        return group
    shipped = next((one for one in group[1:] if one.bundled and one.official
                    and one.manifest is not None), None)
    if shipped is None or shipped.manifest is None:
        return group
    if not newer_version(parse_version(shipped.manifest.version),
                         parse_version(first.manifest.version)):
        return group
    return [shipped, *(one for one in group if one is not shipped)]


class Registry:
    """Every extension this install has looked at."""

    def __init__(self, store: ExtensionStore | None = None) -> None:
        self._store = store or get_extension_store()
        self._records: dict[str, Record] = {}
        self._lock = threading.RLock()
        # The extensions whose own routers the API has mounted.
        self._mounted: set[str] = set()
        # Unofficial extensions imported in this process. Switching one off withdraws what
        # it declared, and its code stays in memory until the next start.
        self._imported_unofficial: set[str] = set()

    # -- reading -------------------------------------------------------------

    @property
    def store(self) -> ExtensionStore:
        """Where the extensions it loaded keep their settings and accounts."""
        return self._store

    def records(self) -> list[Record]:
        with self._lock:
            return sorted(self._records.values(), key=lambda one: one.name)

    def get(self, name: str) -> Record | None:
        with self._lock:
            return self._records.get(str(name or "").strip())

    def unofficial_running(self) -> list[Record]:
        """The extensions VPinFE's team does not stand behind whose code is in this process:
        running, or switched off since it was imported."""
        return [one for one in self.records()
                if (one.running and not one.official)
                or one.name in self._imported_unofficial]

    def inventory(self) -> list[Record]:
        """Every extension folder this install has, read and never imported. For a process
        that started none, such as the command-line support package."""
        return sorted((self._prepare(directory)
                       for root in SEARCH_PATH for directory in _directories(root)),
                      key=lambda one: one.name)

    def running(self, name: str) -> bool:
        found = self.get(name)
        return found is not None and found.running

    def granted_scopes(self) -> frozenset[str]:
        """The `ext:` scopes a caller may hold right now.

        Only from an extension that is running: disabling one takes its scopes with it,
        which is what makes the kill switch reach a route already mounted.
        """
        return frozenset(f"ext:{record.name}:{tier}" for record in self.records()
                         if record.running for tier in reach.TIERS)

    def read_roots(self) -> tuple[str, ...]:
        """Every folder a running extension says it works from.

        Only from one that is running: an extension that has been taken out stops
        widening what this install will read, the same way it stops holding its scopes.
        """
        return tuple(root for record in self.records() if record.running
                     for root in (record.files.roots() if record.files else ()))

    def mounted(self) -> list[tuple[Record, Any, str]]:
        """Every router an extension added itself, whatever state it is in now.

        Routes are mounted once, at startup. A disabled extension keeps its paths and
        refuses on them, which is a better answer than a 404 that reads as a typo.
        """
        found = [(record, router, scope) for record in self.records()
                 for router, scope in record.routers]
        with self._lock:
            self._mounted.update(record.name for record, _router, _scope in found)
        return found

    def served(self) -> list[Record]:
        """Every extension core answers for, whatever state it is in now: all with a
        manifest. Core's routes for each are mounted once, at startup, and refuse
        while it is not running."""
        return [record for record in self.records() if record.manifest is not None]

    # -- loading -------------------------------------------------------------

    def load_from(self, root: Path | str) -> list[Record]:
        """Load every extension directly under a root."""
        return [self.load(directory) for directory in _directories(root)]

    def load_installed(self) -> list[Record]:
        """Every extension this install has, from every root it keeps them in.

        Two passes. The first reads every folder, settles where each came from and
        whether its files are what VPinFE wrote, and logs the whole inventory. Only then
        does the second import anything, so the record of what was there is made before
        any extension's code can run.

        A name already loaded is not loaded again, which is what makes the search order
        the precedence: a copy somebody put in a folder answers, and the official one is
        left alone rather than replacing it halfway through a run. The official one runs
        in its place while the copy is not accepted or is switched off.
        """
        groups: dict[str, list[Record]] = {}
        for root in SEARCH_PATH:
            for directory in _directories(root):
                if self.get(directory.name) is not None:
                    logger.info("Not loading %s from %s: already loaded",
                                directory.name, root)
                    continue
                groups.setdefault(directory.name, []).append(self._prepare(directory))
        _log_inventory([one for group in groups.values() for one in group])

        found = []
        for group in groups.values():
            group = _newest_official_first(group)
            first = group[0]
            found.append(self._start(first))
            behind = next((one for one in group[1:] if one.official), None)
            if (
                first.origin == accepted.REPLACES
                and behind is not None
                and first.state == OFF
                and first.why in (NOT_ACCEPTED, SWITCHED_OFF)
            ):
                runs = self._start(behind)
                runs.waiting = first
                found.append(runs)
        return found

    def load(
        self, directory: Path | str, *, any_platform: bool = False, unaccepted: bool = False
    ) -> Record:
        return self._start(
            self._prepare(Path(directory)), any_platform=any_platform, unaccepted=unaccepted
        )

    def _prepare(self, directory: Path) -> Record:
        """Read the manifest and settle the origin. Imports nothing."""
        record = Record(name=directory.name, directory=directory)
        record.examine()
        record.enabled = self._store.enabled(record.switch_key)
        try:
            record.manifest = _read_manifest(directory)
            record.name = record.manifest.name
        except ManifestError as exc:
            logger.error("Extension %s: %s", record.name,
                         i18n.t_source(exc.key, **exc.values), exc_info=exc.__cause__)
            record.became(FAILED, exc.key, **exc.values)
            return record
        record.became(PENDING)
        return record

    def _start(
        self, record: Record, *, any_platform: bool = False, unaccepted: bool = False
    ) -> Record:
        """Decide whether a prepared extension runs, and run it."""
        manifest = record.manifest
        if manifest is None:
            return self._remember(record)
        i18n.own(f"ext.{record.name}", record.directory / "i18n")

        record.has_settings = bool(self._store.declared(record.name).get("settings"))
        why, values = (
            (SWITCHED_OFF, {})
            if not record.enabled
            else _cannot_run(manifest, any_platform=any_platform)
        )
        if why:
            record.became(OFF, why, **values)
            if why == WRONG_PLATFORM:
                logger.info("Extension %s: %s", record.name, platform_refusal(manifest))
            return self._remember(record)

        self._register(record, gated=not unaccepted)
        return self._remember(record)

    def _register(self, record: Record, *, gated: bool = True) -> None:
        """Import the package, call `register(ctx)` and take what it declared. Leaves
        the record running, or failed with the reason.

        `gated` is False only for `--check-extension`, which loads what it is asked to
        look at and serves nothing. Everything else stops here, before the import, for
        code the Owner has not accepted."""
        manifest = record.manifest
        assert manifest is not None
        if gated and not record.is_accepted():
            logger.warning("Extension %s from %s is not accepted and was not started",
                           record.name, record.folder)
            record.became(OFF, NOT_ACCEPTED)
            return
        context: ExtensionContext | None = None
        if not record.official:
            self._imported_unofficial.add(record.name)
        try:
            module = _import(manifest.name, record.directory)
            register = getattr(module, "register", None)
            if not callable(register):
                raise TypeError("its package defines no register(ctx)")
            name = record.name
            context = ExtensionContext(
                manifest, self._store,
                on_failure=lambda why, **values: self.disable(name, why, **values),
                directory=record.directory)
            register(context)
            context.open = False
            check_references(context.ui)
            check_play(context.ui, manifest.play)
            record.declared_lists = list(context.ui.declared_lists)
            record.declared_links = list(context.ui.declared_links)
            record.declared_groups = list(context.ui.declared_groups)
            record.declared_library_verbs = list(context.ui.declared_library_verbs)
            record.declared_sections = list(context.ui.declared_sections)
            record.declared_panel = dict(context.ui.declared_panel)
            record.declared_needs = list(context.ui.declared_needs)
            self._check_titles(record)
        except Exception as exc:
            logger.exception("Extension %s did not load: %r", record.name, exc)
            record.error = f"{type(exc).__name__}: {exc}"
            record.declared_lists = []
            record.declared_links = []
            record.declared_groups = []
            record.declared_library_verbs = []
            record.declared_sections = []
            record.declared_panel = {}
            record.declared_needs = []
            if context is not None:
                _withdraw(record.name, context.events.registered, context.apps)
            record.became(FAILED, "extension.reason.start_failed")
            return

        record.context = context
        record.routers = list(context.routers)
        record.subscriptions = list(context.events.registered)
        record.apps = context.apps
        record.files = context.files
        record.actions = list(context.ui.actions)
        record.surfaces = {
            "settings": "/settings" if context.ui.settings_declared else "",
        }
        record.account = dict(context.ui.account_offered)
        record.has_settings = bool(context.ui.settings_declared)
        if record.has_settings != bool(self._store.declared(record.name).get("settings")):
            self._store.set_declared(record.name, {"settings": record.has_settings})
        record.became(LOADED)
        logger.info("Extension %s %s loaded", record.name, manifest.version)

    def _check_titles(self, record: Record) -> None:
        """A list or a panel is not titled as a core entry, nor as another list or panel in
        its area: the rail would show two of the same name, and one of them is not what it
        says."""
        core = {i18n.t(f"manager.section.{one}").casefold() for one in CORE_SECTIONS}
        theirs = {(one["area"], one["title"].casefold())
                  for other in self._records.values() if other is not record and other.running
                  for one in [*other.lists(), *other.panels()]}
        for kind, shown in (("list", record.lists()), ("panel", record.panels())):
            for one in shown:
                title = one["title"].casefold()
                if title in core or (one["area"], title) in theirs:
                    raise ContractError(f"{record.name} titles a {kind} {one['title']!r}, "
                                        "which is the name of a page core has or of another "
                                        "list or panel in its area")
                theirs.add((one["area"], title))

    # -- the switch ----------------------------------------------------------

    def switch(self, name: str, on: bool) -> Record | None:
        """Written to `extensions.switched_off`. None when there is no such extension."""
        with self._lock:
            record = self._switch(str(name or "").strip(), on)
        if record is not None:
            core_events.emit(core_events.EXTENSION_SWITCHED, extension=record.name, enabled=on)
        return record

    def _switch(self, name: str, on: bool) -> Record | None:
        record = self._records.get(name)
        if record is None:
            return None
        self._store.set_enabled(record.switch_key, on)
        record.enabled = on
        logger.info("Extension %s switched %s", record.name, "on" if on else "off")
        # A refused manifest is refused at every start, whatever the switch says.
        if record.manifest is None:
            return record
        if not on:
            if not self._take_out(record.name, OFF, SWITCHED_OFF):
                record.became(OFF, SWITCHED_OFF)
            if record.origin == accepted.REPLACES:
                return self._fall_back(record)
        elif record.state == OFF:
            why, values = _cannot_run(record.manifest)
            if why:
                record.became(OFF, why, **values)
            else:
                self._start_again(record)
        return record

    def switch_copy(self, name: str, on: bool) -> Record | None:
        """Switch the copy waiting behind an official extension of this name. It takes
        the name at the next start, as an accepted copy does. None when there is none."""
        with self._lock:
            record = self._records.get(str(name or "").strip())
            copy = record.waiting if record else None
            if record is None or copy is None:
                return None
            self._store.set_enabled(copy.switch_key, on)
            copy.enabled = on
            logger.info("Extension %s (copy) switched %s", copy.name, "on" if on else "off")
            if not on:
                copy.became(OFF, SWITCHED_OFF)
            else:
                copy.became(OFF, STARTS_AT_RESTART if copy.is_accepted() else NOT_ACCEPTED)
            return record

    def _fall_back(self, copy: Record) -> Record:
        """A copy that took an official extension's name is off: the official one takes the
        name back, with the copy waiting behind it. Returns what now holds the name."""
        for root in SEARCH_PATH:
            directory = root / copy.name
            if directory != copy.directory and (directory / MANIFEST_NAME).is_file():
                official = self._prepare(directory)
                self._start(official)
                official.waiting = copy
                if official.running and (official.routers or official.name in self._mounted):
                    _withdraw(official.name, official.subscriptions, official.apps)
                    official.subscriptions, official.context = [], None
                    official.became(OFF, STARTS_AT_RESTART)
                return self._remember(official)
        return copy

    def _start_again(self, record: Record) -> None:
        """Register one that was off. Everything it declared with `ctx.ui` is served by
        core and is live at once; routers it added itself are mounted once at startup,
        so an extension that has some and had none mounted waits for a restart."""
        self._register(record)
        if record.running and record.routers and record.name not in self._mounted:
            _withdraw(record.name, record.subscriptions, record.apps)
            record.subscriptions, record.context = [], None
            record.became(OFF, STARTS_AT_RESTART)

    def accept(self, name: str) -> Record | None:
        """Accept this extension as it declares itself now, and start it. None when
        there is no such extension.

        The built-in a copy replaces keeps running: its routes are mounted at startup, so
        the copy starts at the next start."""
        with self._lock:
            record = self._records.get(str(name or "").strip())
            if record is None:
                return None
            target = record.waiting or record
            if target.manifest is None:
                return record
            accepted.accept_extension(target.name, target.origin, folder=target.folder,
                                      version=target.manifest.version,
                                      scopes=target.manifest.scopes,
                                      capabilities=target.manifest.capabilities,
                                      play=target.manifest.play)
            if target is not record:
                target.became(OFF, STARTS_AT_RESTART)
            elif record.state == OFF and record.why == NOT_ACCEPTED:
                why, values = ((SWITCHED_OFF, {}) if not record.enabled
                               else _cannot_run(record.manifest or target.manifest))
                if why:
                    record.became(OFF, why, **values)
                else:
                    self._start_again(record)
            logger.info("Extension %s accepted", target.name)
            return record

    def files_replaced(self, name: str) -> Record | None:
        """The folder under `name` was installed, updated or removed. A running extension
        keeps running its old code and says so; one that was not running is read again
        and starts at the next start. None when nothing of that name is left."""
        name = str(name or "").strip()
        with self._lock:
            current = self._records.get(name)
            if current is not None and current.running:
                current.files_changed = True
                return current
            copies = [self._prepare(root / name) for root in SEARCH_PATH
                      if (root / name / MANIFEST_NAME).is_file()]
            if not copies:
                self._records.pop(name, None)
                return None
            fresh = _newest_official_first(copies)[0]
            if fresh.manifest is not None:
                fresh.has_settings = bool(self._store.declared(fresh.name).get("settings"))
                fresh.became(OFF, STARTS_AT_RESTART if fresh.enabled else SWITCHED_OFF)
            fresh.waiting = current.waiting if current is not None else None
            return self._remember(fresh)

    # -- the kill switch -----------------------------------------------------

    def disable(self, name: str, why: str, **values: str) -> None:
        """Stop one without stopping anything else, for `why`, a catalog key. Not written
        down: a fault that happened once must not take it away until somebody notices a
        setting they never set."""
        self._stop(name, DISABLED, why, **values)

    def refuse(self, name: str, why: str, **values: str) -> None:
        """Take one out for something only the seam it registered at could see - an
        extension that never really loaded, rather than one that broke."""
        self._stop(name, FAILED, why, **values)

    def _stop(self, name: str, state: str, why: str, **values: str) -> None:
        if self._take_out(name, state, why, **values):
            logger.error("Extension %s %s: %s", name, state,
                         i18n.t_source(why, **values))

    def _take_out(self, name: str, state: str, why: str, **values: str) -> bool:
        """Withdraw a running one and record why. False when it was not running."""
        with self._lock:
            record = self._records.get(str(name or "").strip())
            if record is None or record.state != LOADED:
                return False
            _withdraw(record.name, record.subscriptions, record.apps)
            record.subscriptions, record.context = [], None
            record.became(state, why, **values)
        return True

    def clear(self) -> None:
        """Forget everything loaded, unsubscribing as it goes. For tests."""
        with self._lock:
            for record in self._records.values():
                for event, handler in record.subscriptions:
                    core_events.unsubscribe(event, handler)
            self._records.clear()
            self._imported_unofficial.clear()

    def _remember(self, record: Record) -> Record:
        with self._lock:
            self._records[record.name] = record
        return record


def official_names() -> frozenset[str]:
    """The names VPinFE's team stands behind, which no folder somebody put an extension
    in takes over unmarked: the ones the build ships, the ones installed from the list,
    and the ones in the list as it was last fetched. Needs no connection."""
    return fingerprints.listed_names(
        one.name for root in (BUNDLED_DIR, OFFICIAL_DIR) for one in _directories(root)
    )


def _log_inventory(records: list[Record]) -> None:
    logger.info("Extensions found: %d", len(records))
    for one in records:
        version = one.manifest.version if one.manifest else "unreadable"
        logger.info(
            "Extension inventory: %s %s origin=%s folder=%s fingerprint=%s %s",
            one.name,
            version,
            one.origin,
            one.folder,
            one.fingerprint[:16] or "none",
            "on" if one.enabled else "off",
        )
        if one.differs and one.origin == accepted.CHANGED:
            logger.warning(
                "Extension %s is not as VPinFE put it there: %s",
                one.name,
                ", ".join(one.differs[:10]),
            )


def _withdraw(name: str, subscriptions: list[tuple[str, Any]], apps: ExtensionApps | None) -> None:
    for event, handler in subscriptions:
        core_events.unsubscribe(event, handler)
    contributions.forget(name)
    catalogs.forget(name)
    sources.forget(name)
    tokens.forget(name)
    if apps is not None:
        apps.withdraw()


def _wrong_platform(manifest: Manifest) -> dict[str, str | tuple[str, ...]] | None:
    """The slots of `WRONG_PLATFORM` when this machine is none of the platforms the manifest
    names, a family or a distribution's ID; else None."""
    platform = this_platform()
    if not manifest.platforms:
        return None
    distribution, name = this_distribution() if platform == "linux" else ("", "")
    if {platform, distribution} & set(manifest.platforms):
        return None
    return {"platform": name or (PLATFORM_NAMES.get(platform, platform),)}


def platform_refusal(manifest: Manifest) -> str:
    """The sentence that says this machine is not one it names, or `""`. In English whatever
    language is set: it goes in a report that is quoted."""
    slots = _wrong_platform(manifest)
    if slots is None:
        return ""
    return i18n.t_source(WRONG_PLATFORM, **{
        slot: ", ".join(i18n.t_source(key) for key in value) if isinstance(value, tuple)
        else value for slot, value in slots.items()})


def _cannot_run(manifest: Manifest, *, any_platform: bool = False
                ) -> tuple[str, dict[str, str | tuple[str, ...]]]:
    """Why this device cannot run it whatever the switch says, or `""`. `any_platform`
    leaves the platforms out, for `--check-extension` to load on a machine it is not for."""
    slots = None if any_platform else _wrong_platform(manifest)
    if slots is not None:
        return WRONG_PLATFORM, slots
    missing = [name for name in install_identity.FEATURES
               if name in manifest.requires_features and name not in _features()]
    if missing:
        return LACKS_FEATURES, {
            "count": str(len(missing)),
            "features": tuple(install_identity.LABELS[name] for name in missing)}
    return "", {}


def _features() -> tuple[str, ...]:
    try:
        return tuple(install_identity.features(get_ini_config()))
    except Exception:
        # An install whose config cannot be read is not one to start refusing
        # extensions at: the honest fallback is what every install does.
        return tuple(install_identity.DEFAULT_FEATURES)


def _directories(root: Path | str) -> Iterator[Path]:
    root = Path(root)
    if not root.is_dir():
        return
    for found in sorted(root.iterdir()):
        if found.is_dir() and (found / MANIFEST_NAME).is_file():
            yield found


def _read_manifest(directory: Path) -> Manifest:
    manifest = read_manifest(directory)
    if manifest.name != directory.name:
        raise ManifestError("extension.reason.manifest_names_another", name=manifest.name)
    check_picture(directory, manifest)
    return manifest


def _import(name: str, directory: Path) -> Any:
    """Import the extension's package from its own directory.

    Loaded by path under a reserved module name rather than by putting the directory on
    `sys.path`, so an extension cannot shadow one of ours by naming a file after it.
    """
    module_name = f"vpinfe_ext_{name}"
    init = directory / "__init__.py"
    if not init.is_file():
        raise ModuleNotFoundError(f"no __init__.py in {directory.name}")
    spec = importlib.util.spec_from_file_location(
        module_name, init, submodule_search_locations=[str(directory)])
    if spec is None or spec.loader is None:
        raise ImportError(f"{directory.name} could not be loaded")
    module = importlib.util.module_from_spec(spec)
    # Its modules under the package are imported afresh too, so a switch off and on runs
    # the code now on disk and not the first import's.
    for loaded in [one for one in sys.modules if one.startswith(f"{module_name}.")]:
        del sys.modules[loaded]
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    return module
