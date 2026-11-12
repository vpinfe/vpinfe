"""What needs you on Overview, as rows ordered by what each one costs.

Pure: this decides which rows there are, what each counts and where a click on it lands.
`sections.overview` draws them. A row draws only while its count is above zero, and keeps
its own unit - games, tables, recordings - so that nothing is added together and a click
can open the grid on exactly what the row counted.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Iterable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from common.i18n import t
from common.security import approvals
from common.settings import feature_checks
from manager.kit import leftover_ids, verbs
from manager.kit.need_keys import AWAY, AWAY_ALL
from manager.system import jobs_list

# The tone a row is drawn in, which says how much it costs to leave alone.
DANGER, WARM, ACCENT, MUTED = "danger", "warm", "accent", "muted"

# What a click does that is not opening a grid on a filter. `FOLDERS` opens the list of
# folders `Opens.keys["folders"]` names: "unreadable", "unwritten" or "older_format".
REVIEW, RETRY, FOLDERS = "review", "retry", "folders"

# The rows an extension raises are keyed `ext:<extension>:<kind>`.
EXTENSION = "ext:"

# The rows for a theme or an extension waiting to be accepted, keyed `unaccepted:theme:<key>`
# and `unaccepted:ext:<name>`; two or more themes share `unaccepted:themes`. They are about
# this install and cannot be set aside: whatever is stopped stays so until the Owner
# accepts it or removes it.
UNACCEPTED = "unaccepted:"

# A program's ask to make one change, keyed `asked:<id>`. It lapses on its own and is
# answered, never set aside.
ASKED = "asked:"

# A fix VPinFE can do itself, the first entry of the row's menu.
GET_MEDIA, UPDATE, UPGRADE = "get_media", "update", "upgrade"

_UNIT_GAMES = "manager.games.games"
_UNIT_TABLES = "manager.games.tables"
_UNIT_FOLDERS = "manager.needs.folders"
_UNIT_RECORDINGS = "manager.needs.recordings_count"
_UNIT_SETTINGS = "manager.needs.settings_count"
_UNIT_FILES = "manager.needs.files_count"

# The rows for files Convert is for, which are also the keys they are set aside under.
UNSUPPORTED, WONT_PLAY = "unsupported", "wont_play"


@dataclass(frozen=True)
class Opens:
    """Where a click lands: a rail view, the filter its grid arrives on, the state keys
    that view reads on arrival, and what to do instead where it is not a view."""

    view: str = ""
    arriving: dict[str, Any] | None = None
    keys: Mapping[str, Any] = field(default_factory=dict)
    then: str = ""
    # One of an extension's dialogs, opened over nothing.
    extension: str = ""
    action: str = ""


@dataclass(frozen=True)
class Fix:
    key: str
    label: str
    tip: str
    # What the fix is for: a media kind for Get Media.
    kind: str = ""


@dataclass(frozen=True)
class Need:
    key: str
    label: str
    tip: str
    tone: str
    icon: str
    opens: Opens
    # Games, tables or recordings the row counts, by id. Empty for a row with no count.
    ids: tuple[str, ...] = ()
    unit: str = ""
    fix: Fix | None = None
    # What a dismissal of a row with no ids of its own is recorded against: the version
    # that is out, the job that had errors.
    token: tuple[str, ...] = ()
    # The ids that joined since the row was dismissed. Only set by `arrange`.
    new: tuple[str, ...] = ()
    # An extension's row: its name, drawn where a count would be.
    source: str = ""
    # The count of a row with no ids to open a grid on: an extension's, which is in its
    # label, or the games a newer build wrote, which `unit` names.
    total: int = 0
    # An ask's id, and the exact request it would let through, in one line.
    answer: str = ""
    detail: str = ""

    @property
    def count(self) -> int:
        return len(self.ids) or self.total

    @property
    def covered(self) -> tuple[str, ...]:
        """What a dismissal of this row is recorded against."""
        return self.ids or self.token

    @property
    def by_kind(self) -> bool:
        """An extension's row that named no ids and no token, so a dismissal is kept against
        its kind and the count changing does not bring it back."""
        return is_extension(self.key) and self.token == (self.key.rpartition(":")[2],)

    def counted(self) -> str:
        """The count in its own unit, or "" for a row that has none."""
        return t(self.unit, count=self.count) if self.unit and self.count else ""


def _games_without(key: str, found: Mapping[str, list[dict[str, Any]]],
                   *, label: str, tip: str, tone: str, icon: str,
                   opens: Opens | Callable[[tuple[str, ...]], Opens],
                   fix: Fix | None = None) -> Need | None:
    ids = tuple(sorted(str(game["id"]) for game in found.get(key) or ()))
    if not ids:
        return None
    return Need(key, label, tip, tone, icon, opens(ids) if callable(opens) else opens,
                ids, _UNIT_GAMES, fix)


def unfilled_opens(ids: tuple[str, ...]) -> Opens:
    """The Games grid on the games a row counts: those with a blank slot no source can fill,
    the rest being on Discovery's Missing from Your Games."""
    from manager.library import games

    return Opens("games", keys={games.UNFILLED_KEY: ids})


def no_tables_opens() -> Opens:
    """Games on the No Tables view."""
    from manager.library import games

    return Opens("games", keys={games.ARRIVE_VIEW: games.NO_TABLES_VIEW})


def duplicates_opens() -> Opens:
    """Games on the Duplicates view."""
    from manager.library import games

    return Opens("games", keys={games.ARRIVE_VIEW: games.DUPLICATES_VIEW})


@dataclass(frozen=True)
class Unplayable:
    """The files Convert is for: what is in a format VPinFE cannot use, and the videos a
    device refused, each by `game:path`, and the devices that refused them by name."""

    unsupported: tuple[str, ...] = ()
    wont_play: tuple[str, ...] = ()
    devices: tuple[str, ...] = ()
    here: bool = False

    def __bool__(self) -> bool:
        return bool(self.unsupported or self.wont_play)


def unplayable(found: Mapping[str, Any], local_id: str,
               record: Mapping[str, list[str]] | None = None,
               stopped: Collection[str] = ()) -> Unplayable:
    """Counted from the same rows and judgement as the Media view, so a row here and the
    view it opens agree. `found` is what `Library._watch_media` read. A kind that is
    stopped, or set aside with nothing new in it, is left out, as `arrange` leaves its row."""
    from manager.devices import plays_on
    from manager.library import file_states, media

    devices = list(found.get("devices") or ())
    unsupported: set[str] = set()
    wont_play: dict[str, str | None] = {}
    for row in found.get("media_files") or ():
        where = f"{row.get('game_id')}:{row.get('path')}"
        reason = media.reason_of(row, devices, local_id)
        if reason == file_states.UNSUPPORTED:
            unsupported.add(where)
        elif reason == file_states.WONT_PLAY:
            wont_play[where] = row.get("video_codec")
    def held(key: str, ids: set[str]) -> bool:
        return key in stopped or (bool(ids) and ids <= set((record or {}).get(key, ())))

    if held(UNSUPPORTED, unsupported):
        unsupported = set()
    if held(WONT_PLAY, set(wont_play)):
        wont_play = {}
    return Unplayable(tuple(sorted(unsupported)), tuple(sorted(wont_play)),
                      tuple(plays_on.refusers(wont_play.values(), devices, local_id)),
                      plays_on.refused_here(wont_play.values(), devices, local_id))


def unsupported_opens() -> Opens:
    """Media on the Unsupported view."""
    from manager.library import games, media

    return Opens("media", keys={games.ARRIVE_VIEW: media.UNSUPPORTED_VIEW})


def wont_play_opens() -> Opens:
    """Media on the Won't Play view."""
    from manager.library import games, media

    return Opens("media", keys={games.ARRIVE_VIEW: media.WONT_PLAY_VIEW})


def blank(name: str) -> dict[str, Any]:
    return {name: {"values": [""]}}


def _unread(failed: Any) -> str:
    """The reads that failed, in the words a person would use for what they were after."""
    said = (("update", t("manager.needs.unread_update")),
            ("recordings", t("manager.needs.unread_recordings")),
            ("fill", t("manager.needs.unread_fill")),
            ("extensions", t("manager.needs.unread_extensions")),
            ("unaccepted", t("manager.needs.unread_untrusted")),
            ("leftovers", t("manager.needs.unread_leftovers")),
            ("media", t("manager.needs.unread_media")))
    return ", ".join(words for key, words in said if key in failed)


def extension_rows(raised: Any) -> list[Need]:
    """The rows running extensions raise, after core's own, in the order they answered."""
    from manager.extensions import ext_lists

    out = []
    for one in raised:
        if str(one.get("opens") or "") == "action":
            opens = Opens(extension=str(one["extension"]), action=str(one["to"]))
        else:
            make = ext_lists.page_view_key if one.get("opens") == "page" \
                else ext_lists.view_key
            opens = Opens(make(str(one["extension"]), str(one["to"])))
        out.append(Need(str(one["key"]), str(one["label"]), str(one.get("says") or ""),
                        str(one.get("tone") or WARM), ext_lists.ICON, opens,
                        token=tuple(str(each) for each in one.get("covered") or ()),
                        source=str(one.get("display_name") or ""),
                        total=int(one.get("count") or 0)))
    return out


def unaccepted_rows(waiting: Mapping[str, Any]) -> list[Need]:
    """A row for each theme and extension that is stopped for want of being accepted."""
    from manager.extensions import ext_page

    out = []
    themes = list(waiting.get("themes") or ())
    if len(themes) > 1:
        out.append(Need(UNACCEPTED + "themes", t("manager.needs.untrusted_themes",
                                                 count=len(themes)),
                        t("manager.needs.untrusted_themes.tip"), WARM, verbs.LOCKED,
                        Opens("themes")))
    else:
        for theme in themes:
            out.append(Need(f"{UNACCEPTED}theme:{theme['key']}",
                            t("manager.needs.untrusted",
                              name=theme.get("name") or theme["key"]),
                            t("manager.needs.untrusted.tip"), WARM, verbs.LOCKED,
                            Opens("themes")))
    for extension in waiting.get("extensions") or ():
        name = str(extension["name"])
        out.append(Need(f"{UNACCEPTED}ext:{name}",
                        t("manager.needs.untrusted",
                          name=extension.get("display_name") or name),
                        t("manager.needs.untrusted.tip"), WARM, verbs.LOCKED,
                        Opens("settings", keys={"settings_page": ext_page.key(name)})))
    return out


def asked_rows(waiting: Any) -> list[Need]:
    """A row for each change a program has asked to make, waiting on the Owner."""
    out = []
    for one in waiting:
        body = one.get("body")
        change = " ".join(part for part in (one["method"], one["path"],
                                            "" if body is None else approvals.canonical(body))
                          if part)
        minutes = max(1, -(-int(one.get("expires_in") or 0) // 60))
        out.append(Need(f"{ASKED}{one['id']}", t("manager.needs.asked", program=one["token"]),
                        t("manager.needs.asked.tip", change=change, count=minutes),
                        WARM, verbs.LOCKED, Opens(), answer=str(one["id"]), detail=change))
    return out


def _folders_of(metadata: Mapping[str, Any], which: str) -> tuple[str, ...]:
    """The folder of each row the library's metadata state lists under `which`, sorted."""
    return tuple(sorted(str(row.get("folder") or "") for row in metadata.get(which) or ()))


def leftovers_row(held: Any) -> Need | None:
    """The settings an upgrade left behind that a move or Turn On would deal with, across
    the launchers, one id each. A click lands on the first launcher that has some."""
    ids: list[str] = []
    first = ""
    for one in held:
        found = leftover_ids.of(str(one["launcher_id"]), one.get("items") or ())
        ids += found
        first = first or (str(one["launcher_id"]) if found else "")
    if not ids:
        return None
    return Need("leftovers", t("manager.needs.leftovers"), t("manager.needs.leftovers.tip"),
                WARM, verbs.MOVE, Opens("launchers", keys={"launcher": first}),
                tuple(sorted(ids)), _UNIT_SETTINGS)


def collect(library: Any, state: Mapping[str, Any]) -> list[Need]:
    """Every row that has something to say, in cost order: cannot be played, then
    missing, then ready for you, then tidy-up. A check the library has turned off does
    not draw."""
    # Not at import: `sections` draws these rows, and the grid module draws the metadata
    # line from `sections`.
    from manager.library import games
    from manager.settings import sections

    hidden = library.stopped()
    found = sections.findings(library)
    names = {key: (name, why) for key, name, why, _ in sections.CHECKS}
    watched = library.watched()
    collected = library.kept_kinds()["media"]
    rows: list[Need | None] = []

    broken = tuple(sorted(str(row.get("id") or "") for row in library.table_rows()
                          if row.get("rom") and row.get("rom_installed") is False))
    if broken:
        rows.append(Need("rom_missing", *names["rom_missing"], DANGER, verbs.CANNOT_PLAY,
                         Opens("tables", games.ROM_MISSING),
                         broken, _UNIT_TABLES))

    media_found = Unplayable()
    if "media_files" in watched:
        media_found = unplayable(watched, str(library.discovery().get("install_id") or ""))
    if media_found.wont_play:
        rows.append(Need(WONT_PLAY, t("manager.needs.wont_play"),
                         t("manager.needs.wont_play.tip"), DANGER, verbs.CANNOT_PLAY,
                         wont_play_opens(), media_found.wont_play, _UNIT_FILES))

    if any(one.where == feature_checks.WHERE_LAUNCHERS for one in state.get("trouble") or ()):
        rows.append(Need("launchers", t("manager.needs.launchers"),
                         t("manager.needs.launchers.tip"), DANGER, verbs.CANNOT_PLAY,
                         Opens("launchers"), token=("launchers",)))

    if left := leftovers_row(watched.get("leftovers") or ()):
        rows.append(left)

    metadata = library.metadata_state()
    if unreadable := _folders_of(metadata, "unreadable"):
        rows.append(Need("unreadable", t("manager.needs.unreadable"),
                         t("manager.needs.unreadable.tip"), DANGER, verbs.CANNOT_PLAY,
                         Opens(then=FOLDERS, keys={"folders": "unreadable"}),
                         unreadable, _UNIT_FOLDERS))

    if unwritten := _folders_of(metadata, "unwritten"):
        rows.append(Need("unwritten", t("manager.needs.unwritten"),
                         t("manager.needs.unwritten.tip"), WARM, verbs.NOT_SAVED,
                         Opens(then=FOLDERS, keys={"folders": "unwritten"}),
                         unwritten, _UNIT_GAMES))

    rows.append(_games_without("unmatched", found, label=names["unmatched"][0],
                               tip=names["unmatched"][1], tone=WARM, icon=verbs.NO_MATCH,
                               opens=Opens("games", games.UNMATCHED)))
    rows.append(_games_without("no_tables", found, label=names["no_tables"][0],
                               tip=names["no_tables"][1], tone=WARM, icon=verbs.NO_TABLE,
                               opens=no_tables_opens()))
    if media_found.unsupported:
        rows.append(Need(UNSUPPORTED, t("manager.needs.unsupported"),
                         t("manager.needs.unsupported.tip"), WARM, verbs.NO_MEDIA,
                         unsupported_opens(), media_found.unsupported, _UNIT_FILES))
    for key, kind in sections.FILLS.items():
        if kind in collected:
            rows.append(_games_without(
                key, found, label=names[key][0], tip=names[key][1], tone=WARM,
                icon=verbs.NO_ART,
                opens=unfilled_opens,
                fix=Fix(GET_MEDIA, t("manager.media_fill.get_media"),
                        t("manager.media_fill.get_media.help"), kind)))
    if collected:
        rows.append(_games_without("no_media", found, label=names["no_media"][0],
                                   tip=names["no_media"][1], tone=WARM,
                                   icon=verbs.NO_MEDIA, opens=unfilled_opens))

    fill = watched.get("fill")
    if fill and jobs_list.outcome_of(fill) in jobs_list.TO_LOOK_AT:
        rows.append(Need("fill_errors", t("manager.needs.fill_errors"),
                         t("manager.needs.fill_errors.tip"), WARM, verbs.HAD_ERRORS,
                         Opens("jobs", keys={"jobs_outcome": jobs_list.outcome_of(fill),
                                             "jobs_find": jobs_list.named(fill)}),
                         token=(str(fill.get("id") or "fill"),)))

    update = watched.get("update") or {}
    if update.get("update_available") and not update.get("error"):
        latest = str(update.get("latest_version") or "")
        fix = Fix(UPDATE, t("manager.devices.update_3",
                            latest=latest or t("manager.devices.newer_build")),
                  t("manager.devices.update_3.help")) \
            if update.get("update_supported") else None
        rows.append(Need(UPDATE, t("manager.needs.update", version=latest) if latest
                         else t("manager.needs.update_unnamed"),
                         t("manager.needs.update.tip"), ACCENT, verbs.UPDATE,
                         Opens("about"), fix=fix, token=(latest or UPDATE,)))


    if older := tuple(sorted(str(one) for one in metadata.get("pending_games") or ())):
        rows.append(Need("older_format", t("manager.needs.older_format"),
                         t("manager.needs.older_format.tip"), MUTED, verbs.OLDER_FORMAT,
                         Opens(then=FOLDERS, keys={"folders": "older_format"}),
                         older, _UNIT_GAMES,
                         Fix(UPGRADE, t("word.upgrade"), t("manager.sections.backup_note"))))

    if written_later := int(metadata.get("newer_than_us") or 0):
        rows.append(Need("newer_build", t("manager.needs.newer_build"),
                         t("manager.needs.newer_build.tip"), MUTED, verbs.NEWER_BUILD,
                         Opens("about"), unit=_UNIT_GAMES, total=written_later))

    waiting = tuple(sorted(one for one in watched.get("recordings") or () if one))
    if waiting:
        rows.append(Need("recordings", t("manager.needs.recordings"),
                         t("manager.needs.recordings.tip"), ACCENT, verbs.REVIEW,
                         Opens(then=REVIEW), waiting, _UNIT_RECORDINGS))

    if untied := library.untied_tables():
        rows.append(Need("untied", t("manager.needs.untied"), t("manager.needs.untied.tip"),
                         MUTED, verbs.NO_MATCH, Opens("tables", games.UNTIED),
                         untied, _UNIT_TABLES))

    rows.append(_games_without("no_year", found, label=names["no_year"][0],
                               tip=names["no_year"][1], tone=MUTED, icon=verbs.NO_YEAR,
                               opens=Opens("games", {"year": {"filterType": "number",
                                                             "type": "blank"}})))
    rows += asked_rows(watched.get("asked") or ())
    rows += unaccepted_rows(watched.get("unaccepted") or {})
    rows += extension_rows(watched.get("extension_needs") or ())
    if unread := _unread(watched.get("unread") or ()):
        rows.append(Need("unread", t("manager.needs.unread"),
                         t("manager.needs.unread.tip", what=unread), MUTED, verbs.HAD_ERRORS,
                         Opens(then=RETRY)))
    return [one for one in rows
            if one is not None and (one.key not in hidden or is_extension(one.key))]


# Every row that can be set aside, in cost order. `unread` is not one: it is the page saying
# it could not look, and setting that aside would hide the one thing it must say.
ORDER = ("rom_missing", "wont_play", "launchers", "unreadable", "unmatched", "no_tables",
         "unsupported", "no_playfield",
         "no_backglass", "no_media", "fill_errors", "leftovers", "update", "recordings",
         "untied", "older_format", "no_year")

# The rows about this install rather than the library. Their stop is this install's alone;
# a library row's is the library's, as the Checks page already keeps it.
DEVICE_ROWS = ("launchers", "fill_errors", "leftovers", "update", "recordings")

# What a row's read cannot say while that read failed, by row.
_READ_BY = {"update": "update", "recordings": "recordings", "fill_errors": "fill",
            "leftovers": "leftovers", WONT_PLAY: "media", UNSUPPORTED: "media"}

_ICONS = {"rom_missing": verbs.CANNOT_PLAY, "launchers": verbs.CANNOT_PLAY,
          "unreadable": verbs.CANNOT_PLAY, "unmatched": verbs.NO_MATCH,
          "no_tables": verbs.NO_TABLE,
          "no_playfield": verbs.NO_ART, "no_backglass": verbs.NO_ART,
          "no_media": verbs.NO_MEDIA, "fill_errors": verbs.HAD_ERRORS,
          "leftovers": verbs.MOVE, "update": verbs.UPDATE, "recordings": verbs.REVIEW,
          "older_format": verbs.OLDER_FORMAT, "untied": verbs.NO_MATCH,
          "no_year": verbs.NO_YEAR, WONT_PLAY: verbs.CANNOT_PLAY, UNSUPPORTED: verbs.NO_MEDIA}


def is_device(key: str) -> bool:
    """An extension is installed on this install, so a stop of its row is this install's."""
    return key in DEVICE_ROWS or is_extension(key)


def is_extension(key: str) -> bool:
    return key.startswith(EXTENSION)


def can_set_aside(key: str) -> bool:
    return key in ORDER or is_extension(key)


def blind(library: Any) -> set[str]:
    """The rows that are absent because nothing could be counted, not because nothing is
    wrong: no VPX to audit roms against, no catalog to compare versions with, a kind of
    media the library does not collect."""
    from manager.settings import sections

    rows = library.table_rows()
    out: set[str] = set()
    if all(row.get("rom_installed") is None for row in rows):
        out.add("rom_missing")
    if not library.metadata_state():
        out |= {"unreadable", "older_format"}
    collected = library.kept_kinds()["media"]
    out |= {key for key, kind in sections.FILLS.items() if kind not in collected}
    if not collected:
        out.add("no_media")
    if "media" in (library.watched().get("unread") or ()):
        out |= {WONT_PLAY, UNSUPPORTED}
    return out


def name_of(key: str) -> str:
    """What a row is called while it is not drawn as one: in Set Aside, on the Checks page
    and beside its switch. The row itself may carry more, like the version."""
    from manager.settings import sections

    checks = {one[0]: one[1] for one in sections.CHECKS}
    if key in checks:
        return checks[key]
    return {"launchers": t("manager.needs.launchers"),
            "unreadable": t("manager.needs.unreadable"),
            "fill_errors": t("manager.needs.fill_errors"),
            "leftovers": t("manager.needs.leftovers"),
            "update": t("manager.needs.update_unnamed"),
            "older_format": t("manager.needs.older_format"),
            "untied": t("manager.needs.untied"),
            "recordings": t("manager.needs.recordings"),
            WONT_PLAY: t("manager.needs.wont_play"),
            UNSUPPORTED: t("manager.needs.unsupported")}[key]


def comes_back(key: str, need: Need | None = None) -> str:
    """When a dismissed row is drawn again, in the words the menu and the chip both use.
    An extension's row that gave no ids or token is only drawn again once the extension has
    stopped asking, so it is the `need` that says which wording is true."""
    if is_extension(key):
        if need is not None and need.by_kind:
            return t("manager.needs.back.extension_kind")
        return t("manager.needs.back.extension")
    return {"rom_missing": t("manager.needs.back.rom_missing"),
            "launchers": t("manager.needs.back.launchers"),
            "unreadable": t("manager.needs.back.unreadable"),
            "unmatched": t("manager.needs.back.unmatched"),
            "no_tables": t("manager.needs.back.no_tables"),
            "no_playfield": t("manager.needs.back.no_playfield"),
            "no_backglass": t("manager.needs.back.no_backglass"),
            "no_media": t("manager.needs.back.no_media"),
            "fill_errors": t("manager.needs.back.fill_errors"),
            "leftovers": t("manager.needs.back.leftovers"),
            "update": t("manager.needs.back.update"),
            "recordings": t("manager.needs.back.recordings"),
            "older_format": t("manager.needs.back.older_format"),
            "untied": t("manager.needs.back.untied"),
            "no_year": t("manager.needs.back.no_year"),
            WONT_PLAY: t("manager.needs.back.wont_play"),
            UNSUPPORTED: t("manager.needs.back.unsupported")}[key]


@dataclass(frozen=True)
class Aside:
    """A row that is not drawn among the rest: dismissed over what it counted, or not
    being checked at all."""

    key: str
    label: str
    icon: str
    chip: str
    tip: str
    # False where it was dismissed, which Show Again undoes; True where its check is off,
    # which Check Again turns back on.
    stopped: bool = False


@dataclass(frozen=True)
class Arranged:
    rows: list[Need]
    aside: list[Aside]
    # The dismissals as they stand once what no row counts any more has left them.
    record: dict[str, list[str]]


def _dismissed_aside(need: Need) -> Aside:
    skipped = need.key == UPDATE
    return Aside(need.key, need.label, need.icon,
                 t("manager.needs.chip_skipped") if skipped else t("manager.needs.chip_dismissed"),
                 comes_back(need.key, need))


def _stopped_aside(key: str) -> Aside:
    return Aside(key, name_of(key), _ICONS[key], t("manager.needs.chip_stopped"),
                 t("manager.needs.chip_stopped.tip"), stopped=True)


def _extension_unread(key: str, unread: Any) -> bool:
    """Whether the extension a row belongs to could not say what it needs."""
    if not is_extension(key):
        return False
    name = key[len(EXTENSION):].partition(":")[0]
    return AWAY + name in unread or AWAY_ALL in unread


def _held_for_games(key: str, ids: Iterable[str], filled: Mapping[str, Collection[str]] | None,
                    *, exclude: Collection[str] = ()) -> list[str]:
    """The recorded ids of a row that stay while a source fills their game: those in
    `filled` for the row, apart from `exclude`."""
    if not filled:
        return []
    now = filled.get(key, ())
    return [one for one in ids if one in now and one not in exclude]


def arrange(rows: list[Need], record: Mapping[str, list[str]], stopped: set[str],
            unread: Any = (), blind: Collection[str] = (),
            filled: Mapping[str, Collection[str]] | None = None) -> Arranged:
    """Which rows draw, which are set aside, and what is left of the dismissals.

    A row stays away while every id it counts is in its record. When one joins, the row
    returns with its true total and the new ones marked - unless none of the old ones is
    left, in which case it is a different finding and carries no mark. An id the row no
    longer counts leaves the record, except where the row is absent because its check is
    off, its read failed or it was `blind`: there nothing says the id is gone.

    A row a source can fill stops counting a game while a source has a file for it, and
    counts it again when that source is away. `filled` is those games by row, and an id stays
    recorded while its game is in it, so the dismissal is there when the source goes. A game
    that has the file itself is not in it: its id leaves, and the row counts the game as new
    if the file goes.
    """
    shown: list[Need] = []
    aside: list[Aside] = []
    kept: dict[str, list[str]] = {}
    for need in rows:
        covered = need.covered
        held = set(record.get(need.key, ()))
        if is_extension(need.key) and need.key in stopped:
            # Its dismissal is kept: Check Again brings the row back as it was left.
            if held:
                kept[need.key] = list(record[need.key])
            aside.append(Aside(need.key, need.label, need.icon, t("manager.needs.chip_stopped"),
                               t("manager.needs.chip_stopped.tip"), stopped=True))
            continue
        still = [one for one in covered if one in held]
        away = _held_for_games(need.key, record.get(need.key, ()), filled, exclude=covered)
        if still or away:
            kept[need.key] = [*still, *away]
        new = tuple(one for one in covered if one not in held)
        if not held or not covered:
            shown.append(need)
        elif not new:
            aside.append(_dismissed_aside(need))
        else:
            shown.append(replace(need, new=new) if still else need)
    present = {one.key for one in rows}
    for key, ids in record.items():
        if key == leftover_ids.NOTICE or key not in present and (key in stopped or key in blind
                                     or _READ_BY.get(key) in unread
                                     or _extension_unread(key, unread)):
            kept[key] = list(ids)
        elif key not in present and (away := _held_for_games(key, ids, filled)):
            kept[key] = away
    aside += [_stopped_aside(key) for key in ORDER if key in stopped]
    return Arranged(shown, aside, kept)


LIBRARY, DEVICE = "library", "device"

_COST = {DANGER: 0, WARM: 1, ACCENT: 2, MUTED: 3}


@dataclass(frozen=True)
class Group:
    """The rows one owner raised. `owner` is "" for the row that says the page could not
    look, which belongs to none of them and has no heading."""

    owner: str
    label: str
    rows: list[Need]


def owner_of(need: Need) -> str:
    """Who raised a row: the library, this device, or one extension."""
    if is_extension(need.key):
        return EXTENSION + need.key[len(EXTENSION):].partition(":")[0]
    if need.key == "unread":
        return ""
    return DEVICE if need.key in DEVICE_ROWS or need.key.startswith((UNACCEPTED, ASKED)) \
        else LIBRARY


def _group_label(owner: str, rows: list[Need]) -> str:
    if owner == LIBRARY:
        return t("manager.needs.group.library")
    if owner == DEVICE:
        return t("manager.needs.group.device")
    if not owner:
        return ""
    return next((one.source for one in rows if one.source), owner[len(EXTENSION):])


def grouped(rows: list[Need]) -> list[Group]:
    """Rows by owner, costliest first inside a group and, on a tie, in the order given. A
    group goes by its costliest row, so one holding a row that cannot play comes before
    one holding only tidy-up; groups that tie keep the library, then this device, then the
    extensions by name. The row that says the page could not look is always last."""
    by_owner: dict[str, list[Need]] = {}
    for need in rows:
        by_owner.setdefault(owner_of(need), []).append(need)
    groups = [Group(owner, _group_label(owner, members),
                    sorted(members, key=lambda one: _COST[one.tone]))
              for owner, members in by_owner.items()]

    def order(group: Group) -> tuple[bool, int, int, str]:
        place = 0 if group.owner == LIBRARY else 1 if group.owner == DEVICE else 2
        return (not group.owner, min(_COST[one.tone] for one in group.rows), place,
                group.label.casefold())

    return sorted(groups, key=order)
