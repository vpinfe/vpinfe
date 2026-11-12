"""Wanted and Ignored on Discovery: the chip a row wears, the menu entries that set a mark and
the notice that offers Undo.

A thing is unmarked, Wanted or Ignored and never two, so every entry here is a pair: the
state it sets, and the one it takes off. The marks themselves are the install's, read over
the API; `read` is the snapshot a screen asks its questions of.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from nicegui import ui

from common.failures import why
from common.i18n import t
from common.media.media_specs import media_label_map
from common.online import picture_changes, vps_marked, vps_marks, vps_updates
from manager.kit import offload, panel, undo, verbs
from manager.kit.data import Library
from manager.kit.on_page import on_page
from manager.library import discovery_states

logger = logging.getLogger("vpinfe.manager.library.discovery_marks")

IGNORED = vps_marks.IGNORED
WANTED = vps_marks.WANTED

# What a grid row says of its mark; the release rows also keep the release's own, since a
# release of an ignored game wears Ignored without being ignored itself.
FIELD = "mark"
OWN = "own_mark"

# Home's shelves, by the name a tile's menu is asked for.
NEW = "new"
MISSING = "missing"
WANT = "wanted"
DONT_HAVE = "dont_have"
UPDATE = "updates"

After = Callable[[], Awaitable[None]]


def read(library: Library) -> vps_marks.Marks:
    """The marks as the Library last read them. Asks nothing."""
    return vps_marks.Marks(library.marks())


async def refresh(library: Library) -> None:
    """Read the marks again before a screen is drawn from them. A read that fails leaves the
    last one, and a change then says why it could not be made."""
    try:
        await offload.io(library.load_marks)
    except Exception:  # noqa: BLE001 - the screen draws without chips rather than not at all
        logger.warning("manager: could not read what is marked", exc_info=True)


# ---------------------------------------------------------------------------------------
# The chip


def column(neither: str = "") -> dict[str, Any]:
    """The Status column of both grids: Wanted with the bookmark, Ignored as the quiet word,
    and nothing on the rows that are neither. `neither` names those rows in the filter, for a
    grid whose view keeps them without the Ignored ones; empty offers no such choice."""
    return discovery_states.marks(neither).column(FIELD, t("word.status"))


# ---------------------------------------------------------------------------------------
# What a menu entry acts on


@dataclass(frozen=True)
class Subject:
    """One thing a menu entry marks: its scope, id and detail as the store keys them, the
    name a notice calls it, the state it holds now, and whether it is a release the library
    already holds."""
    scope: str
    id: str
    name: str
    state: str = ""
    detail: str = ""
    held: bool = False


def game_subjects(games: Sequence[Mapping[str, Any]]) -> list[Subject]:
    return [Subject(vps_marks.GAME, str(one["id"]), str(one["name"]), str(one.get(FIELD) or ""))
            for one in games]


def release_name(game: str, kind: str, version: str) -> str:
    key = "manager.discovery.mark.release_name" if version \
        else "manager.discovery.mark.release_name_bare"
    return t(key, game=game, kind=kind, version=version)


def release_subjects(releases: Sequence[Mapping[str, Any]],
                     kind_word: Callable[[str], str]) -> list[Subject]:
    """The Releases tab's rows as subjects. A release's own state is its row's, not the
    chip, which an ignored game puts on all of its releases."""
    return [Subject(vps_marks.RELEASE, str(one["release_id"]),
                    release_name(str(one["name"]), kind_word(str(one["kind"])),
                                 str(one["version"])), str(one.get(OWN) or ""),
                    held=bool(one.get("held")))
            for one in releases]


# ---------------------------------------------------------------------------------------
# Setting a mark, and the notice that offers it back


def _set(library: Library, state: str, one: Subject) -> str:
    """Put `state` on the thing, or take its mark off for "". The state it held before."""
    if state:
        return library.mark_discovery(state, one.scope, one.id, one.detail)
    return library.unmark_discovery(one.scope, one.id, one.detail)


def _called(subjects: Sequence[Subject]) -> str:
    if len(subjects) == 1:
        return subjects[0].name
    scopes = {one.scope for one in subjects}
    key = {vps_marks.RELEASE: "manager.discovery.mark.many_releases",
           vps_marks.GAME: "manager.discovery.mark.many_games"}.get(
        next(iter(scopes)) if len(scopes) == 1 else "", "manager.discovery.mark.many_items")
    return t(key, count=len(subjects))


def _said(state: str, before: set[str], name: str, *, skipped: bool = False) -> str:
    """What the notice says: what was set, and what it took the thing off."""
    if state == IGNORED:
        key = "skipped" if skipped else "ignored_unwanted" if WANTED in before else "ignored"
    elif state == WANTED:
        key = "wanted_unignored" if IGNORED in before else "wanted"
    elif IGNORED in before:
        key = "stopped"
    elif WANTED in before:
        key = "unwanted"
    else:
        return ""
    return t(f"manager.discovery.mark.said.{key}", name=name)


@on_page
async def apply(library: Library, state: str, subjects: Sequence[Subject], after: After,
                *, name: str = "", skipped: bool = False) -> None:
    """Put `state` on each subject, or take its mark off for "", and offer Undo.

    Undo puts each back as it was, which is unmarked for one that held nothing. A change
    that fails part-way keeps what it did and offers Undo for that."""
    changed: list[tuple[Subject, str]] = []
    failure: Exception | None = None
    for one in subjects:
        try:
            before = await offload.io(_set, library, state, one)
        except Exception as exc:  # noqa: BLE001 - the reason belongs on screen
            logger.warning("manager: could not mark %s", one.id, exc_info=True)
            failure = exc
            break
        changed.append((one, before))
    if failure is not None:
        ui.notify(t("manager.discovery.mark.failed"), caption=why(failure), type="negative")
    if not changed:
        return

    async def reverse() -> None:
        """Put back what it can. One that cannot is raised once the rest are back and the
        screen is drawn, which is when Undo says it could not."""
        stuck: Exception | None = None
        for one, before in changed:
            try:
                await offload.io(_set, library, before, one)
            except Exception as exc:  # noqa: BLE001 - the others still go back
                logger.warning("manager: could not put %s back", one.id, exc_info=True)
                stuck = stuck or exc
        await after()
        if stuck is not None:
            raise stuck

    said = _said(state, {before for _one, before in changed},
                 name or _called([one for one, _before in changed]), skipped=skipped)
    if said:
        undo.offer(said, reverse)
    await after()


# ---------------------------------------------------------------------------------------
# Menu entries


def _entry(library: Library, subjects: Sequence[Subject], after: After, *, state: str,
           label: str, icon: str, among: str, name: str = "",
           skipped: bool = False) -> panel.Verb | None:
    """The entry that sets `state` ("" to take it off) on the subjects whose state is
    `among` or, for a set, is not it. None where none of them qualifies."""
    if state:
        fitting = [one for one in subjects if one.state != state]
    else:
        fitting = [one for one in subjects if one.state == among]
    if not fitting:
        return None
    return panel.Verb(
        label, lambda: apply(library, state, fitting, after, name=name, skipped=skipped),
        icon=icon)


def _wanting(library: Library, subjects: Sequence[Subject], after: After) -> list[panel.Verb]:
    wantable = [one for one in subjects if not one.held]
    out = [_entry(library, wantable, after, state=WANTED, among="",
                  label=t("manager.discovery.mark.want"), icon=verbs.WANTED),
           _entry(library, subjects, after, state="", among=WANTED,
                  label=t("manager.discovery.mark.unwant"), icon=verbs.REMOVE)]
    return [one for one in out if one is not None]


def distinguish(entries: list[panel.Verb]) -> list[panel.Verb]:
    """Name the two Stop Ignoring entries of a menu that holds both. A menu on a release
    offers its own first and its game's after, so the first is the release's. Where only
    one is offered the plain word says it."""
    plain = t("manager.discovery.mark.stop")
    at = [n for n, one in enumerate(entries) if one.label == plain]
    if len(at) < 2:
        return entries
    out = list(entries)
    out[at[0]] = replace(out[at[0]], label=t("manager.discovery.mark.stop.release"))
    out[at[1]] = replace(out[at[1]], label=t("manager.discovery.mark.stop.game"))
    return out


def _ignoring(library: Library, subjects: Sequence[Subject], after: After,
              word: str) -> list[panel.Verb]:
    out = [_entry(library, subjects, after, state=IGNORED, among="", label=word,
                  icon=verbs.IGNORE),
           _entry(library, subjects, after, state="", among=IGNORED,
                  label=t("manager.discovery.mark.stop"), icon=verbs.UNHIDE)]
    return [one for one in out if one is not None]


def listed_verbs(library: Library, subjects: Sequence[Subject],
                 after: After) -> list[panel.Verb]:
    """What the Marks grid's rows can be given: Stop Ignoring for the ignored, Add to Wanted
    for the games and releases that are not Wanted and Remove from Wanted for those that are.
    A missing kind, a version or a game's updates can only be ignored, so only stop."""
    wantable = [one for one in subjects if one.scope in vps_marks.WANTABLE]
    out = [_entry(library, subjects, after, state="", among=IGNORED,
                  label=t("manager.discovery.mark.stop"), icon=verbs.UNHIDE),
           *_wanting(library, wantable, after)]
    return [one for one in out if one is not None]


def game_verbs(library: Library, games: Sequence[Mapping[str, Any]], after: After,
               *, wanting: bool = True) -> list[panel.Verb]:
    """Add to Wanted and Ignore Game for the All Games rows, each turning to what takes it off
    where the game holds it. `wanting` is False on the Releases tab, where a release is what
    is wanted."""
    subjects = game_subjects(games)
    return [*(_wanting(library, subjects, after) if wanting else []),
            *_ignoring(library, subjects, after, t("manager.discovery.mark.ignore.game"))]


def gap_verb(library: Library, game_id: str, game: str, listing: str, kind_word: str,
             held: bool, after: After) -> panel.Verb:
    """Ignore Missing {Kind} for one game, or Stop Ignoring Missing {Kind} where it is."""
    subject = Subject(vps_marks.GAP, game_id, t("manager.discovery.mark.gap_name",
                                                 game=game, kind=kind_word),
                      IGNORED if held else "", listing)
    if held:
        return panel.Verb(t("manager.discovery.mark.stop.gap", kind=kind_word),
                          lambda: apply(library, "", [subject], after), icon=verbs.UNHIDE)
    return panel.Verb(t("manager.discovery.mark.ignore.gap", kind=kind_word),
                      lambda: apply(library, IGNORED, [subject], after), icon=verbs.IGNORE)


def release_verbs(library: Library, releases: Sequence[Mapping[str, Any]], after: After,
                  *, kind_word: Callable[[str], str],
                  lacks: Callable[[Mapping[str, Any]], bool]) -> list[panel.Verb]:
    """What the Releases tab's rows can be marked: Add to Wanted and Ignore Release, and for
    a single release of a kind its game lacks (`lacks` says where that does something),
    Ignore Missing {Kind}."""
    subjects = release_subjects(releases, kind_word)
    out = [*_wanting(library, subjects, after),
           *_ignoring(library, subjects, after, t("manager.discovery.mark.ignore.release"))]
    if len(releases) == 1 and lacks(releases[0]):
        one = releases[0]
        held = read(library).ignored(vps_marks.GAP, str(one["game_id"]), str(one["kind"]))
        out.append(gap_verb(library, str(one["game_id"]), str(one["name"]), str(one["kind"]),
                            kind_word(str(one["kind"])), held, after))
    return out


# ---------------------------------------------------------------------------------------
# The pane


def ignored_status(library: Library, row: Mapping[str, Any], after: After) -> bool:
    """The pane's status line for an ignored entry, in place of Add to Library: Ignored and
    the way to stop. False where the entry is not ignored and nothing was drawn."""
    if row.get(FIELD) != IGNORED:
        return False
    one = game_subjects([row])
    with ui.row().classes("items-center gap-x-3 no-wrap w-full"):
        ui.label(t("manager.discovery.mark.ignored")).classes("manager-library-said grow")
        panel.draft_button(t("manager.discovery.mark.stop"),
                           lambda: apply(library, "", one, after), icon=verbs.UNHIDE)
    return True


def recounting(state: dict[str, Any], redraw: After) -> After:
    """`redraw`, then the count on Discovery's entry read again: a mark, a tie or a take
    that redrew the page can have moved it. Home counts from its own read instead."""
    async def again() -> None:
        await redraw()
        if (update_count := state.get("update_count")) is not None:
            await update_count.recount()

    return again


def redrawing(context: dict[str, Any]) -> After:
    """What draws the pane again once a mark made from it has been set."""
    async def after() -> None:
        again = context["state"].get("redraw_discovery")
        if callable(again):
            await again()

    return after


def update_notice_verbs(context: dict[str, Any], game: str, kind_word: str,
                        move: Mapping[str, Any]) -> list[panel.Verb]:
    """Skip This Version and Ignore Updates to This Table, for the one release `move` is of."""
    return Marking(context["library"], redrawing(context)).update_verbs(
        {"game": game, "moves": [move]}, lambda _listing: kind_word)


def take_back(context: dict[str, Any], game: str, kind_word: str,
              move: Mapping[str, Any], after: After | None = None) -> Callable[[], Awaitable[None]]:
    """Stop Ignoring for the mark that set `move` aside: the version skipped, or the
    release's updates. `after` draws what shows it; the pane's own redraw where none is given."""
    release_id = str(move["release_id"])
    if move["set_aside"] == vps_updates.IGNORED:
        one = Subject(vps_marks.UPDATES, release_id,
                      t("manager.discovery.mark.updates_name", game=game), IGNORED)
    else:
        version = str(move["now"])
        one = Subject(vps_marks.VERSION, release_id, release_name(game, kind_word, version),
                      IGNORED, version)
    return lambda: apply(context["library"], "", [one], after or redrawing(context))


def release_marks(context: dict[str, Any], row: Any, game: str, kind_word: str,
                  record: Mapping[str, Any]) -> None:
    """A release in the pane: its mark beside the name, and the menu a right click or a long
    press opens on its row."""
    file_id = str(record.get("vps_file_id") or "")
    if not file_id:
        return
    library = context["library"]
    state = read(library).state(vps_marks.RELEASE, file_id)
    after = redrawing(context)

    one = [Subject(vps_marks.RELEASE, file_id,
                   release_name(game, kind_word, str(record.get("version") or "")), state)]
    entries = [*_wanting(library, one, after),
               *_ignoring(library, one, after, t("manager.discovery.mark.ignore.release"))]
    with row:
        menu = ui.context_menu()
    panel.verb_menu(menu, one[0].name, entries)


def release_mark_glyph(context: dict[str, Any], file_id: str) -> None:
    """The bookmark on a Wanted release, or the Ignored mark, in the pane's list."""
    state = read(context["library"]).state(vps_marks.RELEASE, file_id)
    if state == WANTED:
        ui.icon(verbs.WANTED, size="18px").classes("manager-cell-mark").tooltip(
            t("manager.discovery.mark.wanted.help"))
    elif state == IGNORED:
        ui.icon(verbs.IGNORE, size="18px").classes("manager-cell-mark").tooltip(
            t("manager.discovery.mark.ignored.help"))


# ---------------------------------------------------------------------------------------
# Home


class Marking:
    """What Home asks of the marks: the menu on a tile, and whether a release is Wanted."""

    def __init__(self, library: Library, after: After) -> None:
        self.library = library
        self.after = after

    def wanted_release(self, release_id: str) -> bool:
        return read(self.library).wanted(vps_marks.RELEASE, release_id)

    def ignored_count(self) -> int:
        """How many marks the Marks grid lists as Ignored, which the catalog in memory
        decides: a mark for an entry VPS has dropped is not among them."""
        return len(vps_marked.listed(self.library.catalog(), read(self.library), IGNORED))

    def wanted_game(self, vps_id: str) -> bool:
        return read(self.library).wanted(vps_marks.GAME, vps_id)

    def update_verbs(self, item: Mapping[str, Any],
                     kind_word: Callable[[str], str]) -> list[panel.Verb]:
        """Skip This Version and Ignore Updates to This Table, for every release of the game's
        files that has moved and every picture a source has replaced, the pictures under the
        id the Discovery pane writes them with. A move another source reports names no
        release and no picture of its own has nothing to skip: the entries are left off a tile
        that has only those."""
        found = read(self.library)
        game = str(item["game"])
        moved = [one for one in item["moves"] if one["release_id"]]
        pictures = [one for one in item["moves"] if one.get("md5")]
        words = media_label_map()
        versions = [Subject(vps_marks.VERSION, str(one["release_id"]),
                            release_name(game, kind_word(str(one["kind"])), str(one["now"])),
                            found.state(vps_marks.VERSION, str(one["release_id"]),
                                        str(one["now"])), str(one["now"]))
                    for one in moved]
        releases = [Subject(vps_marks.UPDATES, str(one["release_id"]), game,
                            found.state(vps_marks.UPDATES, str(one["release_id"])))
                    for one in moved]
        for one in pictures:
            at = picture_changes.key(str(one["vps_id"]), str(one["kind"]))
            word = words.get(str(one["kind"]), str(one["kind"]))
            versions.append(Subject(
                vps_marks.VERSION, at, t("manager.discovery.mark.picture_new", kind=word,
                                         game=game),
                found.state(vps_marks.VERSION, at, str(one["md5"])), str(one["md5"])))
            releases.append(Subject(
                vps_marks.UPDATES, at, t("manager.discovery.mark.picture_updates", kind=word,
                                         game=game),
                found.state(vps_marks.UPDATES, at)))
        if not versions:
            return []
        out = [_entry(self.library, versions, self.after, state=IGNORED, among="",
                      label=t("manager.discovery.mark.skip_version"), icon=verbs.IGNORE,
                      name=versions[0].name if len(versions) == 1
                      else t("manager.discovery.mark.versions_name", game=game), skipped=True),
               _entry(self.library, releases, self.after, state=IGNORED, among="",
                      label=t("manager.discovery.mark.ignore.updates"), icon=verbs.IGNORE,
                      name=t("manager.discovery.mark.updates_name", game=game))]
        return [one for one in out if one is not None]

    def title(self, shelf: str, item: Mapping[str, Any]) -> str:
        return str(item["name"] if shelf == MISSING else item["game"])

    def verbs(self, shelf: str, item: Mapping[str, Any], listing: str,
              kind_word: str) -> list[panel.Verb]:
        """The entries of a tile. A New or Wanted tile is a release (a Wanted one may be a
        game); a Missing tile is a game that lacks `listing`; a Games You Don't Have tile is a
        game."""
        found = read(self.library)
        game_id, game = str(item["vps_id"]), self.title(shelf, item)
        whole = [Subject(vps_marks.GAME, game_id, game, found.state(vps_marks.GAME, game_id))]
        if shelf == MISSING:
            return [gap_verb(self.library, game_id, game, listing, kind_word,
                             found.ignored(vps_marks.GAP, game_id, listing), self.after),
                    *_ignoring(self.library, whole, self.after,
                               t("manager.discovery.mark.ignore.game"))]
        if shelf == DONT_HAVE or (shelf == WANT and item["scope"] == vps_marks.GAME):
            return [*_wanting(self.library, whole, self.after),
                    *_ignoring(self.library, whole, self.after,
                               t("manager.discovery.mark.ignore.game"))]
        one = [Subject(vps_marks.RELEASE, str(item["id"]),
                       release_name(game, kind_word, str(item["version"])),
                       found.state(vps_marks.RELEASE, str(item["id"])),
                       held=bool(item.get("held")))]
        return distinguish([
            *_wanting(self.library, one, self.after),
            *_ignoring(self.library, one, self.after, t("manager.discovery.mark.ignore.release")),
            *_ignoring(self.library, whole, self.after, t("manager.discovery.mark.ignore.game"))])
