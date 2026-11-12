"""The curated facade an extension is handed, and nothing else.

`register(ctx)` never receives the application. Everything below is a narrow view over
one core facility, named for the extension that holds it, so that "which extension did
this?" is answerable from a log line, a config file or a scope.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from common import events as core_events
from common.host import os_helper
from common.i18n import t

from . import accounts, reach
from . import cards as cards_module
from .contract import ContractError, Manifest, Unavailable, words
from .contract import why as worded
from .fields import FieldError, settings_fields, ticked
from .games import GAMES_READ, ExtensionGames
from .hardware import ExtensionHardware
from .library_collections import ExtensionCollections
from .pictures import is_openable
from .store import ExtensionStore
from .system import ExtensionSystem
from .uploads import ExtensionUploads

if TYPE_CHECKING:
    from common.runtime import jobs

LOG_ROOT = "vpinfe.ext"


def logger_for(name: str) -> logging.Logger:
    return logging.getLogger(f"{LOG_ROOT}.{name}")


class ExtensionConfig:
    """An extension's settings, under its own name."""

    def __init__(self, name: str, store: ExtensionStore,
                 declared: Callable[[], dict[str, str]] = dict) -> None:
        self._name = name
        self._store = store
        self._declared = declared

    def get(self, key: str, default: str = "") -> str:
        """What is stored under `key`; where that is empty, the default its field
        declares; where there is neither, `default`."""
        wanted = str(key or "").strip()
        stored = self.all()
        if stored.get(wanted):
            return stored[wanted]
        return self._declared().get(wanted) or stored.get(wanted, default)

    def get_list(self, key: str) -> list[str]:
        """The choices ticked in a `multi` setting; the declared default where none has
        been saved."""
        return ticked(self.get(key))

    def all(self) -> dict[str, str]:
        return self._store.settings(self._name)

    def set(self, key: str, value: str) -> None:
        self._store.set_setting(self._name, key, value)


# Events an extension hears only for a scope its manifest declares and its owner sees. Kept
# beside the subscription because the bus itself takes any name from anyone.
SCOPED_EVENTS = {core_events.INPUT_ACTION: "input:read"}


class ExtensionEvents:
    """The bus, seen from one extension: it is told what core did, and publishes under
    its own namespace so nothing it emits can be mistaken for a core event."""

    def __init__(self, name: str, declared: tuple[str, ...],
                 on_failure: Callable[..., None], scopes: Iterable[str] = ()) -> None:
        self._name = name
        self._declared = frozenset(declared)
        self._scopes = frozenset(scopes)
        self._on_failure = on_failure
        self._logger = logger_for(name)
        self.registered: list[tuple[str, Callable]] = []

    def subscribe(self, event: str, handler: Callable) -> None:
        if event in SCOPED_EVENTS and SCOPED_EVENTS[event] not in self._scopes:
            raise ContractError(f"{self._name} subscribes to {event}, which needs "
                                f"{SCOPED_EVENTS[event]}, which its manifest does not declare")

        def contained(**payload: Any) -> None:
            try:
                with reach.unmarked():
                    handler(**payload)
            except Exception:
                self._logger.exception("Handling %s failed", event)
                self._on_failure("extension.reason.failed_handling", event=event)

        core_events.subscribe(event, contained)
        self.registered.append((event, contained))

    def publish(self, event: str, **payload: Any) -> None:
        wanted = str(event or "").strip()
        if wanted not in self._declared:
            raise ContractError(f"{self._name} publishes {wanted!r}, which its manifest "
                                "does not declare")
        core_events.emit(f"{self._name}.{wanted}", **payload)


class ExtensionFiles:
    """The folders an extension works from, so core will accept a path inside one.

    Nothing here stops an extension reading a file - in-process Python cannot be
    prevented from opening one, and pretending otherwise would be theater. What it does
    is let core's own routes take a path the extension is working with: an importer
    converting somebody's old library has to hand core files that are nowhere near ours,
    and without this every one of them is refused.

    Declared rather than assumed, and only by an extension whose manifest asks for it, so
    what an install will read is something a person agreed to and can be shown.
    """

    def __init__(self, name: str, allowed: bool, writes: bool = False) -> None:
        self._name = name
        self._allowed = allowed
        self._writes = writes
        self._roots: tuple[str, ...] = ()

    def roots(self) -> tuple[str, ...]:
        return self._roots

    def may_write(self) -> bool:
        """Whether the manifest declared `fs:write`, which is what lets core take a
        file out of one of these folders instead of copying it."""
        return self._writes

    def set_roots(self, paths: Iterable[str]) -> None:
        """Replace the set. The user moves a share or points somewhere else, and what
        core will accept has to follow rather than accumulate."""
        if not self._allowed:
            raise ContractError(f"{self._name} sets folders to read from, which needs "
                                "the fs:read capability its manifest does not declare")
        wanted = [str(one or "").strip() for one in paths]
        self._roots = tuple(str(Path(one).expanduser().resolve())
                            for one in wanted if one)

    def _require_inside(self, wanted: Path, roots: Sequence[Path] | None = None) -> None:
        """Refuse a path that is not one of these folders or under one.

        Both sides are compared resolved, so a link cannot take a path out of its folder or
        make a declared one look undeclared. `roots`, already resolved, spares a caller
        asking about many paths from resolving the set once for each.
        """
        wanted = wanted.expanduser().resolve()
        if roots is None:
            roots = [Path(one).expanduser().resolve() for one in self.roots()]
        if not any(wanted == root or root in wanted.parents for root in roots):
            raise ContractError(
                f"{self._name} offered {wanted}, which is not inside any folder it says "
                "it works from")


class ExtensionApps:
    """The programs this install can play a table with, as an extension may add to them.

    VPinFE plays Visual Pinball and, through the generic app, anything a person can point
    at a binary. An extension is how a format becomes first-class instead: something that
    claims its own suffixes, so a library of them can be imported and offered rather than
    read and dropped.
    """

    def __init__(self, name: str, scopes: Iterable[str],
                 directory: Path | None = None) -> None:
        self._name = name
        self._scopes = frozenset(scopes)
        self._directory = directory
        self._mine: list[str] = []

    def provide(self, **described: Any) -> str:
        """Add an app, described in plain data. Answers with the id it took.

        `id`, `suffixes`, and optionally `name`, `accepts_keys`, `companions`,
        `vps_formats`, `fields`, `kinds` and `command`. `command(entry, settings)` answers
        with a list of arguments, or with nothing to run the launcher's binary and
        arguments the way the generic app does.

        Its words are the extension's `i18n/<language>.json` under `app.<id>.`, keyed as
        an app's own file is. A `name` or a field's `label` given here is shown as written.
        """
        from . import provided_apps

        if provided_apps.APPS_PROVIDE not in self._scopes:
            raise ContractError(
                f"{self._name} provides an app, which needs "
                f"{provided_apps.APPS_PROVIDE}, and its manifest does not declare it")
        from common import apps, i18n

        built = provided_apps.build(self._name, described)
        apps.contribute(built)
        self._mine.append(built.id)
        if self._directory is not None:
            i18n.own(f"app.{built.id}", self._directory / "i18n", section=f"app.{built.id}")
        logger_for(self._name).info(
            "provides the %s app for %s", built.id,
            ", ".join(built.claim.suffixes) or "keyed entries")
        return built.id

    def suffixes(self) -> tuple[str, ...]:
        """Every file extension this install can play, its own and any provided.

        Ungated, unlike the library: this says what the build can do, not what the user
        has. An extension deciding whether a foreign library is worth importing needs it
        before it has been granted anything.
        """
        from common import apps

        found: list[str] = []
        for app in apps.all_apps():
            found.extend(app.claim.suffixes)
        return tuple(dict.fromkeys(found))

    def names(self) -> tuple[str, ...]:
        """The product names of the apps here. For a source that says which program a
        system used but not which files it holds - a database found in a folder named
        after the program is often all there is."""
        from common import apps

        return tuple(one.name for one in apps.all_apps() if one.name)

    def plays(self, name: str) -> bool:
        """Whether anything here plays a file of this name, or this bare suffix."""
        wanted = str(name or "").strip().lower()
        if not wanted:
            return False
        return any(wanted == one or wanted.endswith(one) for one in self.suffixes())

    def provided(self) -> tuple[str, ...]:
        """What this extension has added, which is what gets taken back with it."""
        return tuple(self._mine)

    def withdraw(self) -> None:
        """Take them all back. Called when the extension is unloaded, so a disabled
        extension does not leave a suffix claimed by something that is no longer here."""
        from common import apps, i18n

        for app_id in self._mine:
            apps.withdraw(app_id)
            i18n.disown(f"app.{app_id}")
        self._mine.clear()


COLUMN_KINDS = frozenset({"text", "number", "date", "state", "chips", "link", "picture",
                          "game", "table"})
# The kinds that draw something other than the value's own text. `game` and `table` are
# not among them: the identifier column may be one, and it is the name core draws.
CELL_KINDS = frozenset({"state", "chips", "link", "picture"})
LIBRARY_KINDS = frozenset({"game", "table"})
COLUMN_KEYS = frozenset({"field", "header", "kind", "help", "under", "states", "name_from",
                         "keys", "picture", "icon", "identifier"})
# Whose ids a `game` or `table` holds: the catalog's, or VPinFE's own.
ID_KEYS = ("vps", "vpinfe")
# The chip tiers a state can wear; a state with neither a tier nor an icon is a quiet word.
STATE_TIERS = frozenset({"on", "off", "unknown", "warn", "bad"})
STATE_LOOKS = frozenset({"tier", "icon"})
RANKING_KINDS = frozenset({"number", "date"})
# What a list's identifier column relates its rows to, by its kind and `keys`.
RELATIONS = {("game", "vps"): "vps_entry", ("table", "vps"): "vps_release",
             ("game", "vpinfe"): "vpinfe_game", ("table", "vpinfe"): "vpinfe_table"}
VERB_KEYS = frozenset({"key", "run", "link", "action", "each", "danger", "needs", "one",
                       "label"})

# The Material icons an entry in the rail can wear; the extension icon where it names none.
RAIL_ICONS = frozenset({
    "analytics", "bar_chart", "build", "bolt", "calendar_month", "campaign", "chat",
    "cloud", "computer", "dashboard", "download", "emoji_events", "event", "extension",
    "favorite", "folder", "groups", "history", "inventory_2", "leaderboard", "link",
    "list_alt", "lock", "map", "monitoring", "music_note", "notifications", "person",
    "photo_library", "public", "receipt_long", "rocket_launch", "schedule", "sell",
    "settings", "share", "shopping_cart", "speed", "sports_esports", "star", "storage",
    "sync", "timeline", "trending_up", "tune", "upload", "videogame_asset", "visibility",
    "wifi"})

# The groups in the rail a list can be put in, each one core already draws.
AREAS = ("library", "frontend", "community", "system")

# The sections of a game's or a table's panel a group can end, each one core already draws.
PANEL_SECTIONS = ("details", "play", "collections", "guides")


@dataclass
class ListFunctions:
    """What a list's extension answers with."""

    rows: Callable[[], Any]
    status: Callable[[], Any] | None = None
    acts: Any = None
    verbs: dict[str, dict[str, Any]] = field(default_factory=dict)
    detail: Callable[[str], Any] | None = None
    save: Callable[[str, dict], Any] | None = None
    tier: str = reach.LIBRARY


@dataclass
class PageFunctions:
    """What a page's extension answers with."""

    read: Callable[[], Any]
    acts: Any = None
    save: Callable[[dict], Any] | None = None
    tier: str = reach.LIBRARY


@dataclass
class GroupFunctions:
    """What a panel group's extension answers with."""

    read: Callable[[dict], Any]
    save: Callable[[dict, dict], Any] | None = None
    acts: Any = None
    private_ok: bool = False
    tier: str = reach.LIBRARY


# The panels a group can be put on: a game's, and one table's.
GROUP_SUBJECTS = ("game", "table")

# How much a kind of need costs to leave alone, as Overview draws it; warm unless said.
NEED_TONES = ("danger", "warm", "accent", "muted")
DEFAULT_NEED_TONE = "warm"
# What a kind opens: one of the extension's lists, one of its pages, or one of its dialogs.
NEED_OPENS = ("list", "page", "action")


@dataclass
class AccountFunctions:
    """What a player's account is answered with, one function for each thing core asks."""

    read: Callable[[str], dict]
    write: Callable[[str, dict], Any] | None = None
    acts: dict[str, Callable[[str], Any]] = field(default_factory=dict)
    card: Callable[[str], dict | None] | None = None
    read_card: Callable[[Any], dict] | None = None
    available: Callable[[str], Any] | None = None


class ExtensionUI:
    """What an extension offers a person: its settings, lists, actions and accounts.

    Declared rather than drawn, and served by core rather than by the extension: it hands
    over fields, a `rows()` and a dialog object, and core mounts the routes the Manager
    reads. An extension that painted its own page would tie the Manager's look to whoever
    wrote it, and would stop working the moment it moved out of this process - where a
    task described as data still does.

    A word left out is looked up in the extension's own `i18n/<language>.json`:
    `settings.<key>.label`, `list.<key>.title`, `action.<key>.label` and so on, as
    `docs/extensions/reference/words.md` lists them. One given is shown as written.
    """

    def __init__(self, name: str, allowed: bool, scopes: Iterable[str] = (),
                 tiers: Iterable[str] = (reach.LIBRARY,), play: Iterable[str] = ()) -> None:
        self._name = name
        self._allowed = allowed
        self._reads_games = GAMES_READ in frozenset(scopes)
        self._tiers = frozenset(tiers) - {reach.PLAY}
        self._play = frozenset(play)
        self.played: set[str] = set()
        self.declared_groups: list[dict] = []
        self.group_functions: dict[str, GroupFunctions] = {}
        self.declared_library_verbs: list[dict] = []
        self.library_verb_functions: dict[tuple[str, str], Callable[[list[dict]], Any]] = {}
        self.actions: list[dict] = []
        self.action_objects: dict[str, Any] = {}
        self.declared_lists: list[dict] = []
        self.list_functions: dict[str, ListFunctions] = {}
        self.declared_pages: list[dict] = []
        self.page_functions: dict[str, PageFunctions] = {}
        self.declared_needs: list[dict] = []
        self.needs_function: Callable[[], Any] | None = None
        self.declared_links: list[dict] = []
        self.link_addresses: dict[str, str | Callable[[], Any]] = {}
        self.settings_declared: list[dict] = []
        self.settings_check: Callable[[dict], Any] | None = None
        self.account_offered: dict = {}
        self.account_functions: AccountFunctions | None = None

    def settings(self, fields: Iterable[dict], *,
                 check: Callable[[dict], Any] | None = None) -> None:
        """This extension's settings. A field is `{"key", "type", "default", "choices"}`,
        `type` one of `common.extensions.fields.SETTINGS_TYPES`. A `number` may carry
        `min`, `max` and `unit`; a `path` says what it `wants`: `dir`, `file` or `exe`.

        `check(values)` gets every setting as it would be after the change and raises to
        refuse it: the refusal is shown under the field that was changed.
        """
        self._needs_ui("settings")
        if self.settings_declared:
            raise ContractError(f"{self._name} declares its settings twice")
        if check is not None and not callable(check):
            raise ContractError(f"{self._name} gives its settings a check that is not "
                                "a function")
        try:
            self.settings_declared = settings_fields(self._name, fields)
        except FieldError as exc:
            raise ContractError(str(exc)) from exc
        self.settings_check = check

    def _tier(self, tier: Any, whose: str, surface: str) -> str:
        """`tier` as the surface named `surface` in the manifest's terms declares it, or a
        ContractError for a word that is no tier, for Play where the manifest's `play` does
        not name the surface, and for another tier the manifest's `tiers` leaves out."""
        named = tier.strip() if isinstance(tier, str) else tier
        if named not in reach.TIERS:
            raise ContractError(f"{self._name} gives {whose} the tier {tier!r}; the tiers "
                                f"are {', '.join(reach.TIERS)}")
        if named == reach.PLAY:
            if surface not in self._play:
                raise ContractError(f"{self._name} puts {whose} at play, and its manifest's "
                                    f"play does not list {surface!r}")
            self.played.add(surface)
            return str(named)
        if named not in self._tiers:
            raise ContractError(f"{self._name} puts {whose} at {named}, and its manifest's "
                                "tiers does not list it")
        return str(named)

    def settings_defaults(self) -> dict[str, str]:
        return {one["key"]: one["default"] for one in self.settings_declared}

    def action(self, key: str, dialog: Any, *, label: str = "",
               description: str = "", tier: str = reach.LIBRARY) -> None:
        """A verb somebody can press, in the vocabulary the rest of the app uses.

        `dialog` has `ask(step)`, answering the dialog for that step, and any of
        `check(values)`, `act(key, values)` and `run(values, progress)`. A method that
        names `step` or `progress` among its arguments is handed it.

        `tier` is who may press it: `act` and `run` need it, and `PROTECTED` asks for the
        Confirm first. Looking at the dialog needs Library, or Play where `tier` is.
        """
        self._needs_ui("an action")
        wanted = str(key or "").strip()
        if not wanted:
            raise ContractError(f"{self._name} offers an action with no key")
        if wanted in self.action_objects:
            raise ContractError(f"{self._name} offers the action {wanted!r} twice")
        if not callable(getattr(dialog, "ask", None)):
            raise ContractError(f"{self._name} offers the action {wanted!r} with an "
                                "object that has no ask(step)")
        held = self._tier(tier, f"the action {wanted!r}", f"action:{wanted}")
        self.action_objects[wanted] = dialog
        self.actions.append({
            "key": wanted,
            "label": str(label or "").strip(),
            "description": str(description or "").strip(),
            "base": f"/actions/{wanted}",
            "tier": held,
        })

    def account(self, *, read: Callable[[str], dict],
                write: Callable[[str, dict], Any] | None = None,
                acts: dict[str, Callable[[str], Any]] | None = None,
                card: Callable[[str], dict | None] | None = None,
                read_card: Callable[[Any], dict] | None = None,
                available: Callable[[str], Any] | None = None,
                label: str = "", cards: Iterable[str] = (), marker: str = "",
                consent: Iterable[str] = (), sends_picture: bool = False) -> None:
        """Say that a player can hold an account with this extension.

        `read(player_id)` answers what the player's panel draws: whether it is claimed, a
        page, a status line and the acts. `write(player_id, values)` saves what was
        chosen. `acts` maps an act's key to a function of the player's id, each answering
        the account again or a `message`. `cards` are the `type`s of card it reads,
        `read_card(card)` checks one and answers `{"name", "initials", "values"}`, and
        `card(player_id)` makes the player's own as `{"card", "filename"}`, or None.
        `available(candidate)` answers whether an id is free, while it is being chosen.
        `marker` names where a card it makes hides its text, and is the extension's name
        unless given. `consent` is what Share tells the player becomes public, each line
        already in its own words from `ctx.t`. `sends_picture` declares that this
        extension sends a player's picture to its service: `ctx.players.picture` answers
        only where it is set, and core adds its own line to the consent.
        """
        self._needs_ui("an account")
        if self.account_offered:
            raise ContractError(f"{self._name} offers a second account; a player holds "
                                "one account with an extension")
        hidden = str(marker or self._name).strip()
        reads = tuple(dict.fromkeys(str(one or "").strip() for one in cards))
        if not callable(read) or not all(reads) or not cards_module.MARKER.match(hidden):
            raise ContractError(f"{self._name} offers an account with no read function, a "
                                "card with no type, or a marker that is not a plain name")
        if reads and not callable(read_card):
            raise ContractError(f"{self._name} reads cards without a read_card function")
        offered = dict(acts or {})
        if not all(str(key).strip() and callable(one) for key, one in offered.items()):
            raise ContractError(f"{self._name} gives an account an act that is not a "
                                "function")
        self.account_functions = AccountFunctions(
            read=read, write=write, acts=offered, card=card, read_card=read_card,
            available=available)
        self.account_offered = {"base": "/accounts", "label": str(label or "").strip(),
                                "cards": list(reads), "marker": hidden,
                                "check": "/available" if available else "",
                                "consent": [str(one) for one in consent if str(one).strip()],
                                "sends_picture": bool(sends_picture)}

    def kept(self, key: str) -> dict | None:
        """The last good read core keeps of one of this extension's lists:
        `{"rows", "read_at"}`, or None where there has been none."""
        from common.games import community_lists

        self._needs_ui("a kept list")
        said = community_lists.kept(self._name, str(key or "").strip())
        if said["rows"] is None:
            return None
        return {"rows": said["rows"], "read_at": said["read_at"]}

    def link(self, key: str, url: str | Callable[[], Any], *, area: str = "",
             icon: str = "") -> None:
        """A link to this extension's own page, opened in a new tab.

        `url` is an address or a function answering one, and `{host}` in it is filled with
        the host the Manager was opened at. Only `http://` and `https://` are drawn. It is
        on the extension's page in Settings; with an `area` it is also an entry in that
        group of the rail, as a list's `area` places a list, drawn with `icon`, one of
        `RAIL_ICONS`. Its words are `link.<key>.label` and `link.<key>.tip` in the
        extension's catalog.
        """
        self._needs_ui("a link")
        wanted = str(key or "").strip()
        where = str(area or "").strip()
        if not wanted:
            raise ContractError(f"{self._name} declares a link with no key")
        if wanted in self.link_addresses:
            raise ContractError(f"{self._name} declares the link {wanted!r} twice")
        if where and where not in AREAS:
            raise ContractError(f"{self._name} puts a link in {where!r}, which is not one "
                                f"of {', '.join(AREAS)}")
        if not callable(url) and not (isinstance(url, str) and is_openable(url)):
            raise ContractError(f"{self._name} gives the link {wanted!r} an address that "
                                "is neither a function nor an http:// or https:// address")
        self.link_addresses[wanted] = url
        self.declared_links.append({"key": wanted, "area": where,
                                    "icon": self._rail_icon(f"the link {wanted!r}", icon)})

    def panel_group(self, subject: str, read: Callable[[dict], Any], *, title: str = "",
                    save: Callable[[dict, dict], Any] | None = None,
                    acts: Any = None, private_ok: bool = False,
                    tier: str = reach.LIBRARY, icon: str = "", section: str = "") -> None:
        """A group on a game's panel (`subject` `"game"`) or on one table's (`"table"`). One
        for each subject.

        With no `section` the group is a section of its own, after core's, named `title`
        and drawn with `icon`, one of `RAIL_ICONS`. With a `section`, one of
        `PANEL_SECTIONS`, it is drawn at the end of that section of core's, under `title`.

        `read(who)` answers the group's one section: `{"facts", "fields", "rows"}`, as a
        pane's section is. `who` is `{"game_id", "vps_id", "name"}`, and for a table also
        `{"table_id", "vps_file_id", "version"}`. Answering `None`, or a section with
        nothing in it, is no group. A game marked Private is never asked about, unless the
        group is declared `private_ok=True`: then it is asked, and `who` carries `private`.
        `save(who, values)` writes the keys changed among the fields the last answer offered.

        `acts` is what can be done to the subject, in a strip closing the group: a dict of
        `{key: function or address}` or a function of `who` answering one. A function is
        called with `who` and answers what to say, an address is opened.

        `tier` is who may read and change the group, and `contract.tier` marks one act or
        the save that differs.

        Reading the games needs `games:read` in the manifest. The group is headed with
        `title`, else `group.<subject>.title` in the extension's catalog, else its name.
        """
        self._needs_ui("a panel group")
        if not self._reads_games:
            raise ContractError(f"{self._name} offers a panel group, which needs "
                                f"{GAMES_READ}, and its manifest does not declare it")
        wanted = str(subject or "").strip()
        if wanted not in GROUP_SUBJECTS:
            raise ContractError(f"{self._name} puts a group on {subject!r}, which is not "
                                f"one of {', '.join(GROUP_SUBJECTS)}")
        if wanted in self.group_functions:
            raise ContractError(f"{self._name} declares a group on a {wanted} twice")
        if not callable(read) or (save is not None and not callable(save)):
            raise ContractError(f"{self._name} gives its {wanted} group a read or a save "
                                "that is not a function")
        if acts is not None and not callable(acts) and not isinstance(acts, dict):
            raise ContractError(f"{self._name} gives its {wanted} group acts that are "
                                "neither a dict nor a function")
        held = self._tier(tier, f"its {wanted} group", f"group:{wanted}")
        placed = str(section or "").strip()
        if placed and placed not in PANEL_SECTIONS:
            raise ContractError(f"{self._name} puts its {wanted} group in the section "
                                f"{placed!r}, which is not one of {', '.join(PANEL_SECTIONS)}")
        glyph = self._rail_icon(f"its {wanted} group", icon)
        self.group_functions[wanted] = GroupFunctions(read=read, save=save, acts=acts,
                                                    private_ok=bool(private_ok), tier=held)
        self.declared_groups.append({"subject": wanted, "title": str(title or "").strip(),
                                     "base": f"/groups/{wanted}", "save": save is not None,
                                     "private_ok": bool(private_ok), "tier": held,
                                     "icon": glyph, "section": placed})

    def verb(self, subject: str, key: str, run: Callable[[list[dict]], Any], *,
             label: str = "", one: bool = False, danger: bool = False,
             private_ok: bool = False, tier: str = reach.LIBRARY) -> None:
        """An entry in the menu of the Games grid (`subject` `"game"`) or the Tables grid
        (`"table"`), on a row, on a selection and on the panel's header, in a block of this
        extension's own after core's.

        `run(who)` is called with the games or tables chosen, each `{"game_id", "vps_id",
        "name"}` and for a table also `{"table_id", "vps_file_id", "version"}`, and answers
        a `message` or `{"message", "url"}`. A game marked Private is left out of `who`, and a
        verb with none left refuses, unless the entry says `private_ok`: it works on Private
        games, and each `who` then carries `private`. Say it only for an entry that sends
        nothing out of VPinFE. `one` refuses several at once, and `danger` asks first and
        draws the entry last in the block, under a rule.

        Reading the games needs `games:read` in the manifest. The words are
        `verb.<subject>.<key>.label`, `.tip` and `.confirm` in the extension's catalog.
        """
        self._needs_ui("a verb")
        if not self._reads_games:
            raise ContractError(f"{self._name} offers a verb, which needs "
                                f"{GAMES_READ}, and its manifest does not declare it")
        wanted, named = str(subject or "").strip(), str(key or "").strip()
        if wanted not in GROUP_SUBJECTS:
            raise ContractError(f"{self._name} puts a verb on {subject!r}, which is not "
                                f"one of {', '.join(GROUP_SUBJECTS)}")
        if not re.fullmatch(r"[a-z][a-z0-9_]*", named) or (
                wanted, named) in self.library_verb_functions:
            raise ContractError(f"{self._name} gives a {wanted} verb {named!r} for a key, "
                                "which is not lower-case words joined by underscores, or "
                                "two with the same one")
        if not callable(run):
            raise ContractError(f"{self._name} gives its {wanted} verb {named!r} a run that "
                                "is not a function")
        held = self._tier(tier, f"its {wanted} verb {named!r}", f"verb:{wanted}:{named}")
        self.library_verb_functions[(wanted, named)] = run
        self.declared_library_verbs.append({
            "subject": wanted, "key": named, "label": str(label or "").strip(),
            "one": bool(one), "danger": bool(danger), "private_ok": bool(private_ok),
            "tier": held})

    def needs(self, kinds: dict[str, dict], read: Callable[[], Any]) -> None:
        """The kinds of thing this extension can ask for on Overview, under Needs You.

        `kinds` maps each kind to the one thing a click opens: `{"list": key}`, `{"page":
        key}` or `{"action": key}` for a list, page or dialog it declares, with an optional
        `"tone"` of danger, warm, accent or muted. `read()` answers the kinds that hold
        now, `[{"kind", "ids"}]`: the ids are what the row counts, and what a dismissal is
        kept against. A kind with no ids to name answers `{"kind", "count", "token"}`, and
        the dismissal comes back when the token changes.

        A row reads `need.<kind>.label`, given `count`, and shows `need.<kind>.says` as its
        tip, from the extension's catalog. A stopped extension raises none.
        """
        self._needs_ui("a need")
        if self.needs_function is not None:
            raise ContractError(f"{self._name} declares its needs twice")
        if not isinstance(kinds, dict) or not kinds:
            raise ContractError(f"{self._name} declares needs without a dict of kinds")
        if not callable(read):
            raise ContractError(f"{self._name} gives its needs a read that is not a function")
        declared = []
        for kind, how in kinds.items():
            name = str(kind or "").strip()
            if not name or any(one in name for one in ":/ ") or name != kind:
                raise ContractError(f"{self._name} names a need {kind!r}, which is empty or "
                                    "has a colon, a slash or a space in it")
            how = how if isinstance(how, dict) else {}
            opened = [one for one in NEED_OPENS if one in how]
            if len(opened) != 1 or not isinstance(how[opened[0]], str) \
                    or not how[opened[0]].strip():
                raise ContractError(f"{self._name} gives the need {name!r} "
                                    f"{'no' if not opened else 'more than one'} "
                                    f"{' or '.join(NEED_OPENS)} to open")
            tone = str(how.get("tone") or DEFAULT_NEED_TONE)
            if tone not in NEED_TONES:
                raise ContractError(f"{self._name} gives the need {name!r} the tone {tone!r}, "
                                    f"which is not one of {', '.join(NEED_TONES)}")
            declared.append({"kind": name, "tone": tone, "opens": opened[0],
                             "to": how[opened[0]].strip()})
        self.declared_needs = declared
        self.needs_function = read

    def page(self, key: str, read: Callable[[], Any], *, area: str = "",
             title: str = "", icon: str = "", acts: Any = None,
             save: Callable[[dict], Any] | None = None, tier: str = reach.LIBRARY) -> None:
        """A page of this extension's own, shown in the rail under `area`, which it must
        name: one subject drawn as a panel, with no grid.

        `read()` answers what a list's `detail` does: `{"title", "under", "picture",
        "sections": [{"key", "label", "facts", "fields", "rows"}]}`, drawn the same way.
        `acts` is the title menu, as a list's is. A field is written when it is set:
        `save(values)` is called with the keys changed, answers nothing, and core asks
        `read` again to draw the page.

        The rail entry is named `title`, else `page.<key>.title` in the extension's
        catalog, else the extension's name, and drawn with `icon`, one of `RAIL_ICONS`.
        `tier` is who may read and change the page, and `contract.tier` marks one act or
        the save that differs.
        """
        self._needs_ui("a page")
        wanted = str(key or "").strip()
        where = self._area(f"the page {wanted!r}", area)
        if not wanted:
            raise ContractError(f"{self._name} declares a page with no key")
        if wanted in self.page_functions:
            raise ContractError(f"{self._name} declares the page {wanted!r} twice")
        if not callable(read) or (save is not None and not callable(save)):
            raise ContractError(f"{self._name} gives the page {wanted!r} a read or a save "
                                "that is not a function")
        if acts is not None and not callable(acts) and not isinstance(acts, dict):
            raise ContractError(f"{self._name} gives the page {wanted!r} acts that are "
                                "neither a dict nor a function")
        held = self._tier(tier, f"the page {wanted!r}", f"page:{wanted}")
        self.page_functions[wanted] = PageFunctions(read=read, acts=acts, save=save, tier=held)
        self.declared_pages.append({"key": wanted, "area": where, "tier": held,
                                    "icon": self._rail_icon(f"the page {wanted!r}", icon),
                                    "title": str(title or "").strip(),
                                    "base": f"/pages/{wanted}",
                                    "about": f"/pages/{wanted}/about",
                                    "save": save is not None})

    def _area(self, whose: str, area: Any) -> str:
        """`area` as the rail names it, or a ContractError where it is blank or not one the
        rail has: an entry with no home in the navigation has nowhere to be found."""
        named = area.strip() if isinstance(area, str) else area
        if not named:
            raise ContractError(f"{self._name} gives {whose} no area, which says where in the "
                                f"navigation it goes: one of {', '.join(AREAS)}")
        if named not in AREAS:
            raise ContractError(f"{self._name} puts {whose} in {named!r}, which is not one "
                                f"of {', '.join(AREAS)}")
        return str(named)

    def _rail_icon(self, whose: str, icon: Any) -> str:
        """`icon` as the rail draws it, "" where there is none, or a ContractError for a
        name the rail does not know."""
        named = icon.strip() if isinstance(icon, str) else icon
        if not named:
            return ""
        if named not in RAIL_ICONS:
            raise ContractError(f"{self._name} gives {whose} the icon {named!r}, which is not "
                                f"one of {', '.join(sorted(RAIL_ICONS))}")
        return str(named)

    def _needs_ui(self, what: str) -> None:
        if not self._allowed:
            raise ContractError(f"{self._name} offers {what}, which needs the ui:mount "
                                "capability its manifest does not declare")

    def list(self, key: str, rows: Callable[[], Any], *, columns: Sequence[dict],
             area: str = "", title: str = "", icon: str = "",
             views: Sequence[dict] = (),
             status: Callable[[], Any] | None = None, acts: Any = None,
             tag: str = "", verbs: Sequence[dict] = (), add: str | Sequence[str] = (),
             detail: Callable[[str], Any] | None = None,
             save: Callable[[str, dict], Any] | None = None,
             tier: str = reach.LIBRARY) -> None:
        """A list this extension holds, shown in the rail under `area`, which it must name,
        drawn with `icon`, one of `RAIL_ICONS`.

        `rows()` answers the rows, each a dict. `status()` answers the line the list's
        page says, text or `{"text", "detail", "to"}`. `acts` is what the title menu
        offers, a dict of `{key: function or address}` or a function answering one: a
        function runs when pressed and answers what to say, an address is opened.

        A column is `{"field", "header", "kind"}` with `kind` one of `text`, `number`,
        `date`, `state`, `chips`, `link`, `picture`, `game` or `table`. One column is the
        row's name, the identifier: the one that says `identifier: True`, else the first
        that is not a `state`, `chips`, `link` or `picture`, wherever columns are put. It
        alone may name `under`, row fields drawn on the line beneath its value. A `state`
        column names `states={value: {"tier"} or {"icon"}}`, a `link` column may name the
        row field it reads as in `name_from`, and a `picture` column holds a web address or a
        file in the extension's folder. A `game` or `table` column holds an id, VPS's or
        VPinFE's own as its `keys` says (`vps`, the default, or `vpinfe`), and `name_from` names
        the row field holding the name to draw where neither the library nor VPS knows the
        id. The identifier may name `picture`, a row field holding an image, and `icon`, a
        Material icon or SVG path drawn where there is no picture.

        An identifier column of kind `game` or `table` is what the list relates to in this
        library, so a view that `ranks` and a `tag` need one. A view is `{"key", "name",
        "columns", "sort": [{"field", "desc"}], "help", "ranks"}`, and one that `ranks` is
        offered as an order for a collection, with a sort on number or date columns only.
        `tag` is put on every game or table of this library the list relates to.

        `verbs` are what can be done to the rows, in the row's menu and the selection's.
        A verb is `{"key", "run", "link" or "action", "label", "each", "danger", "needs",
        "one"}`: `run(rows)` is called with the rows chosen and answers a `message` or
        `{"message", "url"}`, `link` names a row field holding a web address to open,
        `action` names an action declared with `action()` and opens its dialog over the
        rows chosen, one dialog for them all or, with `each`, one for each row with Skip
        and Stop. `danger` asks first, `needs` names a boolean row field that must be true
        for the verb to act on a row, and `one` refuses several rows at once. A row needs
        an `id`, a string unique in the list, to be ticked or to have a menu.

        `add` names the action, or the actions, that put something into this list: a
        button in the list's bar that opens that dialog and reads the list again after a
        run.

        `detail(row_id)` answers what the pane beside the list shows for a row, so a click on
        a row opens it: `{"title", "under", "picture", "sections": [{"key", "label",
        "facts": [{"label", "value", "kind", "to", "help", "tier", "says", "keys",
        "picture"}], "fields": [{"key", "type", "label", "value"}], "rows": [{"label",
        "under"}]}]}`. A fact's `kind` is one of `common.extensions.pane.FACT_KINDS` and a
        field's `type` one of `fields.SETTINGS_TYPES`. A field is written when it is set:
        `save(row_id, values)` is called with the keys changed, answers nothing, and core
        asks `detail` again to draw the pane. `save` needs `detail`.

        `tier` is who may read and change the list: `PLAY` also reaches a Remote pass and a
        Play token, `PROTECTED` asks the Owner for a Confirm to change it. `contract.tier`
        marks one act, verb `run` or `save` that differs.
        """
        self._needs_ui("a list")
        wanted = str(key or "").strip()
        where = self._area(f"the list {wanted!r}", area)
        rail_icon = self._rail_icon(f"the list {wanted!r}", icon)
        names = [str((one or {}).get("field") or "").strip() for one in columns]
        if not wanted or not columns or not all(names):
            raise ContractError(f"{self._name} declares a list with no key or "
                                "a column with no field")
        if wanted in self.list_functions:
            raise ContractError(f"{self._name} declares the list {wanted!r} twice")
        if not callable(rows) or (status is not None and not callable(status)):
            raise ContractError(f"{self._name} gives the list {wanted!r} rows or a "
                                "status that is not a function")
        if (detail is not None and not callable(detail)) \
                or (save is not None and not callable(save)):
            raise ContractError(f"{self._name} gives the list {wanted!r} a detail or a save "
                                "that is not a function")
        if save is not None and detail is None:
            raise ContractError(f"{self._name} gives the list {wanted!r} a save and no "
                                "detail, which is where its fields come from")
        if acts is not None and not callable(acts) and not isinstance(acts, dict):
            raise ContractError(f"{self._name} gives the list {wanted!r} acts that are "
                                "neither a dict nor a function")
        kinds = {str((one or {}).get("kind") or "text") for one in columns}
        if not kinds <= COLUMN_KINDS:
            raise ContractError(f"{self._name} declares a column kind core does not draw: "
                                f"{', '.join(sorted(kinds - COLUMN_KINDS))}")
        for one in columns:
            self._column(wanted, one or {})
        named = self._identifier(wanted, columns)
        for view in views:
            used = set(view.get("columns") or []) | {str(one.get("field") or "")
                                                     for one in view.get("sort") or []}
            if not str(view.get("key") or "").strip() or not used <= set(names):
                raise ContractError(f"{self._name} declares a view with no key or on a "
                                    "column it does not have")
        subject = columns[named] or {}
        subject_kind = str(subject.get("kind") or "text")
        relation = ({"field": names[named],
                     "keys": RELATIONS[(subject_kind, str(subject.get("keys") or ID_KEYS[0]))]}
                    if subject_kind in LIBRARY_KINDS else None)
        derived = " ".join(str(tag or "").split())
        if derived and not relation:
            raise ContractError(f"{self._name} derives a tag from a list that relates to "
                                "nothing in the library")
        measured = {name for name, one in zip(names, columns, strict=True)
                    if str((one or {}).get("kind") or "text") in RANKING_KINDS}
        for view in views:
            if not view.get("ranks"):
                continue
            sorted_on = {str(one.get("field") or "") for one in view.get("sort") or []}
            if not sorted_on or not sorted_on <= measured:
                raise ContractError(f"{self._name} ranks by a view that sorts on nothing, "
                                    "or on a column that is not a number or a date")
            if not relation:
                raise ContractError(f"{self._name} ranks by a view of a list that relates "
                                    "to nothing in the library")
        offered = self._verbs(wanted, verbs)
        adding = [add] if isinstance(add, str) else [*add] if isinstance(add, Sequence) else [None]
        adding = [one.strip() if isinstance(one, str) else one for one in adding]
        if not all(isinstance(one, str) and one for one in adding) \
                or len(set(adding)) != len(adding):
            raise ContractError(f"{self._name} gives the list {wanted!r} an add that is "
                                "not an action key, or names one twice")
        held = self._tier(tier, f"the list {wanted!r}", f"list:{wanted}")
        self.list_functions[wanted] = ListFunctions(rows=rows, status=status, acts=acts,
                                                    verbs=offered, detail=detail, save=save,
                                                    tier=held)
        self.declared_lists.append({
            "key": wanted, "area": where, "title": str(title or "").strip(), "tier": held,
            "icon": rail_icon, "base": f"/lists/{wanted}",
            "columns": [{"field": name, "header": str(one.get("header") or ""),
                         "identifier": index == named,
                         "kind": str(one.get("kind") or "text"),
                         "help": str(one.get("help") or ""),
                         "under": [str(under) for under in one.get("under") or []],
                         "name_from": str(one.get("name_from") or ""),
                         "keys": (str(one.get("keys") or ID_KEYS[0])
                                  if str(one.get("kind") or "text") in LIBRARY_KINDS else ""),
                         "picture": str(one.get("picture") or "").strip(),
                         "icon": str(one.get("icon") or "").strip(),
                         "states": {value: dict(look or {})
                                    for value, look in (one.get("states") or {}).items()}}
                        for index, (name, one) in enumerate(zip(names, columns, strict=True))],
            "views": [{"key": str(view["key"]).strip(),
                       "name": str(view.get("name") or "").strip(),
                       "columns": [*(view.get("columns") or names)],
                       "sort": [{"field": str(one["field"]), "desc": bool(one.get("desc"))}
                                for one in view.get("sort") or []],
                       "help": str(view.get("help") or ""),
                       "ranks": bool(view.get("ranks"))} for view in views],
            "relation": dict(relation) if relation else None,
            "tag": derived,
            "about": f"/lists/{wanted}/about",
            "detail": f"/lists/{wanted}/detail" if detail is not None else "",
            "verbs": [{"key": verb, "label": one["label"], "link": one["link"],
                       "action": one["action"], "each": one["each"],
                       "danger": one["danger"], "needs": one["needs"], "one": one["one"]}
                      for verb, one in offered.items()],
            "add": adding,
        })

    def _identifier(self, list_key: str, columns: Sequence[dict]) -> int:
        """The index of the column drawn as the row's name: the one that says
        `identifier`, else the first that draws its own text. The line under it, its
        picture and its icon belong to it alone."""
        where = f"{self._name} gives the list {list_key!r}"
        marked = [index for index, one in enumerate(columns) if (one or {}).get("identifier")]
        if len(marked) > 1:
            raise ContractError(f"{where} more than one identifier column")
        plain = [index for index, one in enumerate(columns)
                 if str((one or {}).get("kind") or "text") not in CELL_KINDS]
        if marked and marked[0] not in plain:
            raise ContractError(f"{where} a {columns[marked[0]]['kind']} column as its "
                                "identifier, which is drawn as the row's name")
        if not plain:
            raise ContractError(f"{where} no column that can be the row's name; a state, "
                                "chips, link or picture column cannot")
        chosen = marked[0] if marked else plain[0]
        for index, one in enumerate(columns):
            for part in ("under", "picture", "icon"):
                if index != chosen and (one or {}).get(part):
                    raise ContractError(f"{where} a {part} on the column "
                                        f"{one.get('field')!r}, which is not the identifier "
                                        f"and the only one drawn with one")
        return chosen

    def _column(self, list_key: str, column: dict) -> None:
        where = f"{self._name} gives the list {list_key!r} the column {column.get('field')!r}"
        unknown = sorted(set(column) - COLUMN_KEYS)
        if unknown:
            raise ContractError(f"{where} keys core does not read: {', '.join(unknown)}. "
                                f"It reads {', '.join(sorted(COLUMN_KEYS))}")
        kind = str(column.get("kind") or "text")
        if column.get("states") is not None and kind != "state":
            raise ContractError(f"{where} states, which only a state column has")
        if column.get("name_from") is not None and kind != "link" \
                and kind not in LIBRARY_KINDS:
            raise ContractError(f"{where} name_from, which only a link, game or table "
                                "column has")
        if (kind == "link" or kind in LIBRARY_KINDS) \
                and not isinstance(column.get("name_from") or "", str):
            raise ContractError(f"{where} a name_from that is not the name of a row field")
        if column.get("keys") is not None and kind not in LIBRARY_KINDS:
            raise ContractError(f"{where} keys, which only a game or table column has")
        if kind in LIBRARY_KINDS and (column.get("keys") or ID_KEYS[0]) not in ID_KEYS:
            raise ContractError(f"{where} keys of {column.get('keys')!r}, which is not one "
                                f"of {', '.join(ID_KEYS)}")
        for named in ("picture", "icon"):
            if column.get(named) is not None \
                    and not (isinstance(column[named], str) and column[named].strip()):
                raise ContractError(f"{where} a {named} that is not text")
        if kind == "picture" and column.get("picture") is not None:
            raise ContractError(f"{where} a picture key, which a picture column does not take")
        if kind != "state":
            return
        states = column.get("states")
        if not isinstance(states, dict) or not states \
                or not all(isinstance(value, str) and value and "." not in value
                           for value in states):
            raise ContractError(f"{where} no states, or states keyed by something other "
                                "than the text a row holds, or by a value with a dot in it")
        for value, look in states.items():
            look = look if look is not None else {}
            if not isinstance(look, dict) or not set(look) <= STATE_LOOKS or len(look) > 1:
                raise ContractError(f"{where} the state {value!r} with more than one of a "
                                    "tier and an icon, or with something else")
            if "tier" in look and not (isinstance(look["tier"], str)
                                       and look["tier"] in STATE_TIERS):
                raise ContractError(f"{where} the state {value!r} a tier that is not one "
                                    f"of {', '.join(sorted(STATE_TIERS))}")
            if "icon" in look and not (isinstance(look["icon"], str) and look["icon"].strip()):
                raise ContractError(f"{where} the state {value!r} an icon that is not a "
                                    "Material icon name")

    def _verbs(self, list_key: str, verbs: Sequence[dict]) -> dict[str, dict[str, Any]]:
        """The verbs of one list, checked, by key. Each holds its `run`, which is not
        declared to the Manager."""
        found: dict[str, dict[str, Any]] = {}
        for raw in verbs:
            where = f"{self._name} gives the list {list_key!r}"
            if not isinstance(raw, dict):
                raise ContractError(f"{where} a verb that is not a dict")
            key = str(raw.get("key") or "").strip()
            if not key or key in found:
                raise ContractError(f"{where} a verb with no key, or two with the same one")
            unknown = sorted(set(raw) - VERB_KEYS)
            if unknown:
                raise ContractError(f"{where} the verb {key!r} with keys core does not "
                                    f"read: {', '.join(unknown)}. It reads "
                                    f"{', '.join(sorted(VERB_KEYS))}")
            run, link = raw.get("run"), str(raw.get("link") or "").strip()
            if not isinstance(raw.get("action") or "", str):
                raise ContractError(f"{where} the verb {key!r} an action that is not the "
                                    "key of a dialog")
            action = str(raw.get("action") or "").strip()
            if sum([run is not None, bool(link), bool(action)]) != 1 \
                    or (run is not None and not callable(run)):
                raise ContractError(f"{where} the verb {key!r} with more than one of a run "
                                    "function, a link and an action, or with none, or a "
                                    "run that is not a function")
            if link and (raw.get("danger") or raw.get("needs")):
                raise ContractError(f"{where} the link verb {key!r} danger or needs, "
                                    "which a link has no use for")
            if action and raw.get("danger"):
                raise ContractError(f"{where} the verb {key!r} danger with an action, "
                                    "whose dialog asks for itself")
            if raw.get("each") and not action:
                raise ContractError(f"{where} the verb {key!r} each without an action "
                                    "to walk the rows with")
            found[key] = {"run": run, "link": link, "action": action,
                          "each": bool(raw.get("each")),
                          "danger": bool(raw.get("danger")),
                          "needs": str(raw.get("needs") or "").strip(),
                          "one": bool(raw.get("one")) or bool(link),
                          "label": str(raw.get("label") or "").strip()}
        return found


PLAYERS_READ = "players:read"


def check_references(ui: ExtensionUI) -> None:
    """Refuse a list that opens an action the extension never declared. Run once
    `register` has returned, since an action may be declared after the list."""
    added = {one["key"]: one["add"] for one in ui.declared_lists}
    held = [("list", key, found.acts, [*(one["action"] for one in found.verbs.values()
                                         if one["action"]), *added.get(key, [])])
            for key, found in ui.list_functions.items()]
    held += [("page", key, found.acts, []) for key, found in ui.page_functions.items()]
    places: dict[str, Mapping[str, Any]] = {
        "list": ui.list_functions, "page": ui.page_functions, "action": ui.action_objects}
    for one in ui.declared_needs:
        if one["to"] not in places[one["opens"]]:
            raise ContractError(f"{ui._name} has a need {one['kind']!r} that opens the "
                                f"{one['opens']} {one['to']!r}, which it never offers")
    for kind, key, acts, named in held:
        if isinstance(acts, dict):
            for one in acts.values():
                if isinstance(one, dict) and "action" in one:
                    if not isinstance(one["action"], str) or not one["action"].strip():
                        raise ContractError(f"{ui._name} has an act on the {kind} {key!r} "
                                            "whose action is not an action key")
                    named.append(one["action"])
        unknown = sorted({one for one in named if one not in ui.action_objects})
        if unknown:
            raise ContractError(f"{ui._name} opens, from the {kind} {key!r}, "
                                f"{'an action' if len(unknown) == 1 else 'actions'} it "
                                f"never offers: {', '.join(unknown)}")


def check_play(ui: ExtensionUI, play: Iterable[str]) -> None:
    """Refuse a manifest whose `play` lists a surface the extension never put at Play. Run
    once `register` has returned, since a surface may be declared after the list is read."""
    unused = sorted(set(play) - ui.played)
    if unused:
        raise ContractError(f"{ui._name}'s manifest lists under play surfaces it never "
                            f"puts at play: {', '.join(unused)}")


class ExtensionPlayers:
    """Who plays here, and the accounts this extension holds for them.

    The roster is read as plain data, the rows `players.changed` carries. Reading it needs
    `players:read` in the manifest: names and initials are about people. An account's
    values are this extension's own and need nothing declared.
    """

    def __init__(self, name: str, scopes: Iterable[str], store: ExtensionStore,
                 ui: ExtensionUI | None = None) -> None:
        self._name = name
        self._reads = PLAYERS_READ in frozenset(scopes)
        self._store = store
        self._ui = ui

    def roster(self) -> list[dict]:
        """Every player: the owner, the kept players, then the guests as they joined."""
        self._needs_read()
        from common.players import players

        return players.get_roster().state()["players"]

    def get(self, player_id: str) -> dict | None:
        wanted = str(player_id or "").strip()
        return next((one for one in self.roster() if one["id"] == wanted), None)

    def up(self) -> list[dict]:
        """Who the next game counts for."""
        return [one for one in self.roster() if one["up"]]

    def record(self, player_id: str, game_id: str) -> dict | None:
        """A player's record of one game, as `GET /api/v1/players/{id}/record` lists it.
        None for the owner and for a player nobody has."""
        self._needs_read()
        from common.games import player_records
        from common.players import players

        player = players.get_roster().get(player_id)
        if player is None or player.owner:
            return None
        return player_records.get_records().shown(player, str(game_id or "").strip())

    def sharing(self, player_id: str) -> bool:
        """Whether this player's account here is sharing. Off until someone turns it on."""
        from common.players import players

        return players.get_roster().sharing(player_id, self._name)

    def picture(self, player_id: str) -> Path | None:
        """The path of a kept player's picture, for an extension that declared
        `sends_picture` and only while that player's Share here is on. None otherwise,
        including when the player has no picture."""
        from common.players import player_pictures, players

        offered = self._ui.account_offered if self._ui is not None else {}
        roster = players.get_roster()
        if not offered.get("sends_picture") or not roster.sharing(player_id, self._name):
            return None
        kept = roster.get(player_id)
        if kept is None or kept.guest:
            return None
        return player_pictures.find(roster.pictures_dir, kept.player_id)

    def account(self, player_id: str) -> dict[str, str]:
        """This player's account here, secrets included. Empty when they hold none."""
        return accounts.values(self._name, player_id, self._store)

    def set_account(self, player_id: str, values: dict[str, Any]) -> None:
        """Replace this player's account here. Empty values remove it. A guest's is held
        in memory and never written."""
        accounts.keep(self._name, player_id, values, self._store)

    def holders(self) -> list[str]:
        """The ids of every player holding an account here, in roster order."""
        return accounts.holders(self._name, self._store)

    def _needs_read(self) -> None:
        if not self._reads:
            raise ContractError(f"{self._name} reads the players, which needs "
                                f"{PLAYERS_READ}, and its manifest does not declare it")


class ExtensionEntries:
    """What this extension adds to every entry a theme is handed.

    One key, filled in by core when the player moves to a game. A theme reads
    `entry.ext.<key>` and never learns which extension answered - which is the point: the
    surface a theme sees does not grow a method per connector.
    """

    def __init__(self, name: str) -> None:
        self._name = name

    def contribute(self, key: str, fetch: Callable[[dict], object]) -> None:
        """Answer about one game at a time.

        `fetch` is given a plain description of the game - its ids and what is known
        about the machine - and returns whatever a theme should read, or None where there
        is nothing to say. It is called on core's thread when the wheel stops, so it may
        block; it must not raise for a game it simply has no answer about.
        """
        from . import contributions

        wanted = str(key or "").strip()
        if not wanted:
            raise ContractError(f"{self._name} contributes under no key")
        contributions.register(self._name, wanted, fetch)

    def stale(self, key: str) -> None:
        """What this extension answered under `key` is out of date: core drops what it
        holds, and asks again the next time each game is reached."""
        from . import contributions

        contributions.forget_answers(self._name, str(key or "").strip())

class ExtensionCatalogs:
    """Outside places a game, a table or a file can be reached, which core draws."""

    def __init__(self, name: str) -> None:
        self._name = name

    def contribute(self, key: str, name: str, subject: str,
                   link: Callable[[dict], str]) -> None:
        """`subject` is `game`, `table` or `file`. `link` is given a plain description
        of one and answers its address there, or "" where it has none."""
        from . import catalogs

        wanted = str(key or "").strip()
        if not wanted or not str(name or "").strip():
            raise ContractError(f"{self._name} contributes a link with no key or name")
        try:
            catalogs.register(self._name, wanted, str(name).strip(), str(subject or ""),
                              link)
        except ValueError as exc:
            raise ContractError(str(exc)) from exc


class ExtensionSources:
    """Online places a game's files can be had from, which core offers in Add and Replace."""

    def __init__(self, name: str, scopes: Iterable[str], reaches: Iterable[str]) -> None:
        self._name = name
        self._reads_games = GAMES_READ in frozenset(scopes)
        self._reaches = frozenset(reaches)
        self._keys: set[str] = set()

    def contribute(self, key: str, name: str, kinds: Iterable[str],
                   offers: Callable[[dict, str], Any],
                   take: Callable[[dict, str, str], Any],
                   take_many: Callable[[dict, list[dict]], Any] | None = None,
                   act: Callable[[dict, str, str, str], Any] | None = None,
                   look: Callable[[dict, str, str], Any] | None = None) -> None:
        """An Online source for the kinds of file in `kinds`, as core names them: a media
        kind, an asset kind, a whole-folder kind or `table`.

        `offers(who, kind)` answers `{"rows": [{key, name, site, page, note, picture, tag,
        current, vps_file_id, state, acts}], "note", "link"}`: `site` names the place and
        `page` is the file's page there, `current` says the slot already holds it, and
        `link` is `{"text", "to"}`, `{"text", "page"}` for an extension page or
        `{"text", "settings": True}` for its Settings page, drawn under the rows beside
        `note`.  `vps_file_id` ties a row to the file Discovery lists under the same id, so
        the row is drawn there instead of among the other sources.  `state` is `{"text",
        "level", "hint"}`, drawn on the row, `level` one of `on`, `off`, `unknown`, `warn`,
        `bad`, and `acts` is `[{"key", "label"}]`, one button each.
        `who` is `{"game_id", "vps_id", "name", "table_id"}`: the game the slot belongs to,
        and the entry whose files are asked for, which is another one's after Search Another
        Game.  `take` gets `table_id` as the table the person chose in Save It As, empty
        for every table in the game; `offers` always gets it empty.  In Discovery `game_id`
        is empty for a game that is not in the library yet.

        `take(who, kind, key)` is called with the key of the row picked and answers
        `{"job": id, "message"}`, `{"action": dialog_key}` to open that dialog over Add or
        Replace with the row picked as its `rows`, or `{"message"}`. A game marked Private is
        never asked about in Add and Replace; Discovery asks by VPS id alone.

        `take_many(who, picks)` is called with `[{"kind", "key"}]` for every file ticked in
        Discovery at once, and answers `{"message"}`.  `act(who, kind, key, act)` is called
        when a row's button is pressed and answers what `take` does, or `{"message"}`; an
        empty `kind` and `key` is the pane's own button, with `act` `stop`.
        `look(who, kind, key)` answers `{"pictures": [address], "description", "files":
        [{"name", "size"}], "note"}`, what the file's page says, drawn when its row is opened.

        `changed(vps_id)` says what a source offers for that entry has moved, so an open
        Discovery pane asks again.

        Needs `games:read` in the manifest, and Library in its `tiers`.
        """
        from common.online import sources

        if not self._reads_games:
            raise ContractError(f"{self._name} offers an Online source, which needs "
                                f"{GAMES_READ}, and its manifest does not declare it")
        if reach.LIBRARY not in self._reaches:
            raise ContractError(f"{self._name} offers an Online source, which is at "
                                f"{reach.LIBRARY}, and its manifest's tiers does not list it")
        wanted = str(key or "").strip()
        shown = str(name or "").strip()
        if not wanted or not shown:
            raise ContractError(f"{self._name} contributes an Online source with no key "
                                "or name")
        if wanted in self._keys:
            raise ContractError(f"{self._name} contributes an Online source {wanted!r} twice")
        if isinstance(kinds, str) or not isinstance(kinds, Iterable):
            raise ContractError(f"{self._name} gives its Online source {wanted!r} kinds "
                                "that are not a list of names")
        if not callable(offers) or not callable(take):
            raise ContractError(f"{self._name} gives its Online source {wanted!r} an offers "
                                "or a take that is not a function")
        for given in (take_many, act, look):
            if given is not None and not callable(given):
                raise ContractError(f"{self._name} gives its Online source {wanted!r} a "
                                    "take_many, act or look that is not a function")
        try:
            sources.contribute(self._name, wanted, shown,
                             frozenset(str(one).strip() for one in kinds), offers, take,
                             take_many, act, look)
        except ValueError as exc:
            raise ContractError(f"{self._name}: {exc}") from exc
        self._keys.add(wanted)

    def changed(self, vps_id: str) -> None:
        """What this extension's sources offer for the VPS entry has moved: a download
        began, finished or needs the person."""
        from common.online import sources

        sources.changed(vps_id)


class ExtensionTokens:
    """Names a user may write into a command, brought by this extension.

    Stored as `<extension>.<name>`.
    """

    # The two moments a command runs, so an extension names one without importing core.
    VPINFE = "vpinfe"
    TABLE = "table"

    def __init__(self, name: str) -> None:
        self._name = name

    def offer(self, name: str, contexts: Iterable[str], value: Callable[[dict], str], *,
              says: str = "", after_only: bool = False) -> str:
        """Answer with the full name, which is what a user types. What it stands for is
        `says`, or `token.<name>.says` in the extension's own catalog."""
        from common import tokens

        try:
            return tokens.register(self._name, name, frozenset(contexts), value,
                                   says=says, after_only=after_only)
        except ValueError as exc:
            raise ContractError(str(exc)) from exc


class ExtensionOS:
    """What an operating system's helper does for an extension, one fixed method at a time.

    The helper runs with more rights than VPinFE and offers a short list of methods, each
    a closed set of arguments. This is the one way an extension reaches it. Nothing here
    contains an extension that opens the helper's socket itself: that code runs as the same
    user, and what bounds it is the helper's short list.

    A method is offered to an extension that declares the capability for its area. A
    method that changes the machine is also refused unless the request it is called from
    reached Protected, so a forgotten declaration fails closed.
    """

    def __init__(self, name: str, capabilities: Iterable[str]) -> None:
        self._name = name
        self._areas = frozenset(one for one in capabilities if one in os_helper.AREAS)

    @property
    def available(self) -> bool:
        """Whether a helper stands on this device."""
        return os_helper.available()

    @property
    def methods(self) -> tuple[dict[str, Any], ...]:
        """The methods this extension may call: `name`, whether it `changes` the machine,
        and `says`, core's sentence for it, which is what a change asks before it runs."""
        return tuple({"name": one.name, "changes": one.changes, "says": one.sentence}
                     for one in os_helper.METHODS.values() if one.area in self._areas)

    def call(self, method: str, **args: Any) -> Any:
        """Ask the helper for `method`. A call from outside a Protected request is
        refused when the method changes the machine, and an area the manifest does not
        declare is refused outright."""
        one = os_helper.METHODS.get(str(method or ""))
        if one is None:
            raise ContractError(f"{self._name} called {method!r}, which no helper offers")
        if one.area not in self._areas:
            raise ContractError(f"{self._name} called {one.name}, which needs the "
                                f"{one.area} capability its manifest does not declare")
        mark = reach.current()
        if one.changes:
            if mark is None:
                raise PermissionError(t("error.os.no_request"))
            if mark.tier != reach.PROTECTED:
                raise PermissionError(t("error.os.not_from_here"))
        origin = mark.origin if mark is not None else ""
        logger_for(self._name).info("asked the system for %s from %s", one.name,
                                    origin or "no request")
        try:
            return os_helper.call(one, args, by=self._name, origin=origin)
        except (os_helper.HelperAbsentError, os_helper.HelperBrokenError) as exc:
            raise Unavailable(worded(exc)) from exc


class ExtensionJobs:
    """Slow work, run the way core runs it.

    The kind carries the extension's name, so a job somebody is watching says which
    extension is doing it - the same reason the log namespace does. One at a time per
    kind, which is core's rule and is right here too: two imports of one library at once
    would race each other into the same folders.
    """

    def __init__(self, name: str) -> None:
        self._name = name

    def submit(self, kind: str, work: Callable[[jobs.Job], object], *,
               stoppable: bool = False, keep_result: bool = False) -> jobs.Job:
        """Run `work` on its own thread. With `stoppable`, the job offers to be stopped
        (core answers that on the extension's write scope): the work looks at
        `job.halt` between its units and ends early when it is set. With `keep_result`
        what `work` returns is written to disk, so the Jobs view can open it after the page
        that started the job has gone; `job.problems` says how many parts did not come
        across."""
        from common.runtime import jobs

        mark = reach.current()
        return jobs.submit(
            f"{self._name}.{str(kind or '').strip()}", reach.carried(work),
            stop_link=f"/api/v1/ext/{self._name}/jobs/{str(kind or '').strip()}/stop"
            if stoppable else "",
            keep_result=keep_result, extension=self._name,
            tier=mark.tier if mark is not None else reach.LIBRARY)

    def stop(self, kind: str) -> bool:
        """Ask this extension's running job of that kind to end early. False where there
        is none, or where it was not started `stoppable`."""
        from common.runtime import jobs

        running = [one for one in jobs.active(f"{self._name}.{str(kind or '').strip()}")
                   if one.stoppable]
        for job in running:
            job.halt.set()
        return bool(running)

    def active(self) -> tuple[str, ...]:
        from common.runtime import jobs

        return tuple(job.id for job in jobs.active()
                     if job.kind.startswith(f"{self._name}."))


class ExtensionContext:
    """What `register(ctx)` is given."""

    def __init__(self, manifest: Manifest, store: ExtensionStore,
                 on_failure: Callable[..., None], directory: Path | None = None) -> None:
        self.name = manifest.name
        self.manifest = manifest
        self.reaches = manifest.reaches
        self.logger = logger_for(manifest.name)
        self.config = ExtensionConfig(manifest.name, store,
                                      lambda: self.ui.settings_defaults())
        self.events = ExtensionEvents(manifest.name, manifest.events, on_failure,
                                      manifest.scopes)
        self.files = ExtensionFiles(manifest.name, "fs:read" in manifest.capabilities,
                                    "fs:write" in manifest.capabilities)
        self.jobs = ExtensionJobs(manifest.name)
        self.os = ExtensionOS(manifest.name, manifest.capabilities)
        self.ui = ExtensionUI(manifest.name, "ui:mount" in manifest.capabilities,
                              manifest.scopes, manifest.reaches, manifest.play)
        self.players = ExtensionPlayers(manifest.name, manifest.scopes, store, self.ui)
        self.entries = ExtensionEntries(manifest.name)
        self.catalogs = ExtensionCatalogs(manifest.name)
        self.sources = ExtensionSources(manifest.name, manifest.scopes,
                                        manifest.reaches)
        self.tokens = ExtensionTokens(manifest.name)
        self.games = ExtensionGames(manifest.name, manifest.scopes, self.files)
        self.uploads = ExtensionUploads(manifest.name, manifest.scopes, self.files, self.games)
        self.collections = ExtensionCollections(manifest.name, manifest.scopes)
        self.system = ExtensionSystem(manifest.name, manifest.scopes)
        self.hardware = ExtensionHardware(manifest.name, manifest.scopes)
        self.apps = ExtensionApps(manifest.name, manifest.scopes, directory)
        # Which program this is, for an extension that has to say so to somebody else.
        # Through the context rather than an import: the one module an extension may
        # import is the contract, and that is what makes the boundary checkable.
        from common.vpinfe_version import get_version

        self.host_version = get_version()
        self.routers: list[tuple[Any, str]] = []
        # Registration is a moment, not a phase: routers are mounted once, so one added
        # after `register` returned would never be reachable and silently answer nothing.
        self.open = True

    def t(self, key: str, /, **params: Any) -> str:
        """What this extension's `i18n/<language>.json` says for `key`, in the language
        now set: a wizard's title, a field's label, a sentence a route answers with."""
        return words(self.name)(key, **params)

    def why(self, exc: BaseException, /, at: str | os.PathLike[str] = "") -> str:
        """What core says under a failure of its own: `contract.why`."""
        return worded(exc, at)

    def add_router(self, router: Any, *, tier: str = reach.LIBRARY) -> None:
        """Offer routes under `/api/v1/ext/<name>/`, for callers who reach `tier`: `PLAY`,
        `LIBRARY` or `PROTECTED`, the one the manifest's `tiers` lists. Core attaches the
        gate; the extension cannot choose to have none."""
        if not self.open:
            raise ContractError(f"{self.name} added a router after registering")
        named = tier.strip() if isinstance(tier, str) else tier
        if named not in reach.TIERS or named not in self.reaches:
            raise ContractError(f"{self.name} puts a router at {tier!r}; its tiers are "
                                f"{', '.join(self.reaches)}")
        if named == reach.PLAY and "router" not in self.manifest.play:
            raise ContractError(f"{self.name} puts a router at play, and its manifest's "
                                "play does not list 'router'")
        if named == reach.PLAY:
            self.ui.played.add("router")
        self.routers.append((router, f"ext:{self.name}:{named}"))
