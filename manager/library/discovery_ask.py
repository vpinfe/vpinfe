"""Asking Discovery about releases: the search that finds a name, and the page of one
author's, maker's or theme's work.

The question a page asks is `common.online.vps_question`'s. The grids filter on their
columns, and the saved views are Discovery's.
"""

from __future__ import annotations

import html
from collections.abc import Awaitable, Callable, Collection, Mapping, Sequence
from functools import partial
from typing import Any

from nicegui import ui

from common.games import launchers
from common.i18n import t
from common.online import vps_find, vps_question
from manager.kit import dropdown, panel, thumbnails, verbs

SINCE_DAYS = (("manager.discovery.ask.week", 7), ("manager.discovery.ask.fortnight", 14),
              ("manager.discovery.ask.month", 30))
WALL_AT_FIRST = 60
TABLES = "tableFiles"

SUBJECT_KEY = {"author": "by", "maker": "maker", "theme": "theme"}
SUBJECT_WORD = {"author": "word.author", "maker": "word.manufacturer",
                "theme": "manager.discovery.ask.theme"}

# What a kind's releases are called together.
KIND_MANY = {
    "tableFiles": "manager.discovery.ask.kinds.table",
    "b2sFiles": "manager.discovery.ask.kinds.backglass",
    "romFiles": "manager.discovery.ask.kinds.rom",
    "pupPackFiles": "manager.discovery.ask.kinds.pup_pack",
    "altColorFiles": "manager.discovery.ask.kinds.color",
    "altSoundFiles": "manager.discovery.ask.kinds.altsound",
    "povFiles": "manager.discovery.ask.kinds.pov",
    "ruleFiles": "manager.discovery.ask.kinds.rules",
    "wheelArtFiles": "manager.discovery.ask.kinds.wheel",
    "topperFiles": "manager.discovery.ask.kinds.topper",
}

# One of a kind, in the words a sentence names it by.
KIND_ONE = {
    "tableFiles": "manager.discovery.ask.noun.table",
    "b2sFiles": "manager.discovery.ask.noun.backglass",
    "romFiles": "manager.discovery.ask.noun.rom",
    "pupPackFiles": "manager.discovery.ask.noun.pup_pack",
    "altColorFiles": "manager.discovery.ask.noun.color",
    "altSoundFiles": "manager.discovery.ask.noun.altsound",
    "povFiles": "manager.discovery.ask.noun.pov",
    "ruleFiles": "manager.discovery.ask.noun.rules",
    "wheelArtFiles": "manager.discovery.ask.noun.wheel",
    "topperFiles": "manager.discovery.ask.noun.topper",
}

# A tile's count of releases, with the kind's noun in the one place a translation can order it.
TILE_COUNT = {
    "tableFiles": "manager.discovery.ask.tile.table",
    "b2sFiles": "manager.discovery.ask.tile.backglass",
    "romFiles": "manager.discovery.ask.tile.rom",
    "pupPackFiles": "manager.discovery.ask.tile.pup_pack",
    "altColorFiles": "manager.discovery.ask.tile.color",
    "altSoundFiles": "manager.discovery.ask.tile.altsound",
    "povFiles": "manager.discovery.ask.tile.pov",
    "ruleFiles": "manager.discovery.ask.tile.rules",
    "wheelArtFiles": "manager.discovery.ask.tile.wheel",
    "topperFiles": "manager.discovery.ask.tile.topper",
}

# Show: which of the page's games the wall lists.
EVERYTHING, YOURS, WITHOUT, ELSEWHERE = "", "yours", "without", "elsewhere"
SHOW_WORD = {EVERYTHING: "manager.discovery.ask.show.everything",
             YOURS: "manager.discovery.ask.show.yours",
             WITHOUT: "manager.discovery.ask.show.without",
             ELSEWHERE: "manager.discovery.ask.show.elsewhere"}
# On a maker's or a theme's page nothing is "theirs", so Without asks about a file.
SHOW_WORD_OF_GAMES = {**SHOW_WORD, WITHOUT: "manager.discovery.ask.show.missing"}

# Where a game the library has stands against what the page lists.
HAVE, OTHER, UNKNOWN, MISSING = "have", "other", "unknown", "missing"
STANDING_WORD = {HAVE: "manager.view.have", OTHER: "manager.discovery.ask.have_other",
                 UNKNOWN: "manager.discovery.ask.have_unknown",
                 MISSING: "manager.discovery.ask.missing"}
_STANDING_CLASS = {HAVE: "manager-have", OTHER: "manager-wall-note--part",
                   UNKNOWN: "manager-wall-note--part", MISSING: ""}
# What a game's cell for a kind reads when the library has a file of that kind.
MATCHED, UNMATCHED = "matched", "unmatched"
# What a counted column reads before the library has been compared with VPS.
NOT_COMPARED = "not_compared"
FILES = (MATCHED, UNMATCHED)


class Suggest:
    """The search box, with what its letters could mean listed under it as you type: a
    person under any name they are credited as, a maker, a theme, a game. Arrow keys move
    and Enter chooses."""

    def __init__(self, box: ui.input, find: Callable[[str], dict[str, list[dict[str, Any]]]],
                 choose: Callable[[str, dict[str, Any]], Awaitable[None]]) -> None:
        self.box, self.find, self.choose = box, find, choose
        self.items: list[tuple[str, dict[str, Any]]] = []
        self.at = -1
        self.list = ui.element("div").classes("manager-suggest")
        self.list.set_visibility(False)
        box.props("debounce=120 autocomplete=off")
        box.on_value_change(lambda event: self.typed(event.value or ""))
        box.on("keydown.down.prevent", lambda: self.move(1))
        box.on("keydown.up.prevent", lambda: self.move(-1))
        box.on("keydown.enter", self.enter)
        box.on("keydown.escape", self.hide)
        box.on("blur", self.hide)

    def typed(self, text: str) -> None:
        found = self.find(text)
        self.items = [(group, one) for group in vps_find.GROUPS for one in found.get(group, [])]
        self.at = -1
        self.draw()

    def hide(self) -> None:
        self.items, self.at = [], -1
        self.list.set_visibility(False)

    def move(self, step: int) -> None:
        if self.items:
            self.at = (self.at + step) % len(self.items)
            self.draw()

    async def enter(self) -> None:
        if 0 <= self.at < len(self.items):
            await self.pick(*self.items[self.at])

    async def pick(self, group: str, one: dict[str, Any]) -> None:
        self.hide()
        await self.choose(group, one)

    def draw(self) -> None:
        self.list.clear()
        self.list.set_visibility(bool(self.items))
        if not self.items:
            return
        last = ""
        with self.list:
            for position, (group, one) in enumerate(self.items):
                if group != last:
                    last = group
                    ui.label(t(f"manager.discovery.ask.group.{group}")) \
                        .classes("manager-suggest-head")
                with ui.element("div").classes(
                        "manager-suggest-row" + (" manager-suggest-row--on"
                                                 if position == self.at else "")) as row:
                    ui.label(one["name"]).classes("grow truncate")
                    if one.get("via"):
                        ui.label(t("manager.discovery.ask.as", name=one["via"])) \
                            .classes("manager-help")
                    if group == vps_find.GAME:
                        if one.get("said"):
                            ui.label(one["said"]).classes("manager-help")
                    else:
                        ui.label(str(one["count"])).classes("manager-help")
                # mousedown, not click: the box would lose focus first and hide the list
                # before the press reached the row.
                row.on("mousedown.prevent", lambda _e=None, g=group, o=one: self.pick(g, o))


def _tile(row: Mapping[str, Any], line: str, where: tuple[str, str],
          on_open: Callable[[], Any]) -> None:
    """One game: its picture, its name, `line` under it, and where it stands in the
    library as a third line only for a game the library has."""
    with ui.element("div").classes("manager-wall-tile").props(
            "tabindex=0 role=button") as tile:
        src = str(row.get(thumbnails.FIELD) or "")
        with ui.element("div").classes("manager-wall-picture"):
            if src:
                ui.html(f'<img src="{html.escape(src)}" alt="" loading="lazy">')
        ui.label(str(row["name"])).classes("manager-wall-name truncate")
        if line:
            ui.label(line).classes("manager-wall-note truncate")
        state, tip = where
        if state:
            note = ui.label(t(STANDING_WORD[state])).classes(
                f"manager-wall-note {_STANDING_CLASS[state]} truncate")
            if tip:
                note.classes("manager-wall-note--tip").tooltip(tip)
    tile.on("click", on_open).on("keydown.enter", on_open)


Fitting = Sequence[tuple[str, Mapping[str, Any]]]


def standing(row: Mapping[str, Any], fitting: Fitting,
             mine: Mapping[str, Sequence[Mapping[str, Any]]], fields: Mapping[str, str],
             people: Sequence[Mapping[str, Any]], page_name: str, *,
             counted: bool) -> tuple[str, list[str]]:
    """Where a game stands against what the page lists, and whose it is where that is
    another author's. Empty for a game the library does not have, and before the library
    has been compared.

    `fitting` is the releases the page lists for the game, the same set Show reads, and
    `mine` all of its releases by listing. A held release the page leaves out is not the
    page's: the game reads Missing unless someone else's release is the one held. A kind the
    library holds that is bound to no credited release is held by someone VPinFE cannot
    name."""
    if not (counted and row.get("is_in_library")) or not fitting:
        return "", []
    ours = [one for _listing, one in fitting if one.get("held")]
    if ours:
        return (HAVE, []) if any(one.get("by") for one in ours) else (UNKNOWN, [])
    holding = {listing for listing, _one in fitting if row.get(fields.get(listing, "")) in FILES}
    if not holding:
        return MISSING, []
    others = [one for listing in holding for one in mine.get(listing, ()) if one.get("held")]
    if not others:
        return UNKNOWN, []
    names = sorted({str(people[at]["name"]) for one in others for at in one.get("by") or ()
                    if 0 <= at < len(people)} - {page_name})
    if names:
        return OTHER, names
    return (UNKNOWN, []) if any(not one.get("by") for one in others) else (MISSING, [])


def unmatched_kind(row: Mapping[str, Any], fitting: Fitting, fields: Mapping[str, str]) -> str:
    """The listing a game in the library holds without it being bound to any release: the
    first one the page lists for it that the library holds."""
    return next((listing for listing, _one in fitting
                 if row.get(fields.get(listing, "")) in FILES), "")


def in_show(show: str, row: Mapping[str, Any], fitting: Fitting, fields: Mapping[str, str],
            *, of_author: bool) -> bool:
    """Whether a game the page finds is listed under this Show choice. On an author's page
    Without Theirs is a game of the library none of whose listed releases it holds. On a
    maker's or a theme's it is a game of the library missing a file of a kind the page lists."""
    if show == ELSEWHERE:
        return not row.get("is_in_library")
    if show == EVERYTHING:
        return True
    if not row.get("is_in_library"):
        return False
    if show == YOURS:
        return True
    if of_author:
        return not any(one.get("held") for _listing, one in fitting)
    return any(row.get(fields.get(listing, "")) == MISSING for listing, _one in fitting)


def narrows(row: Mapping[str, Any], text: str) -> bool:
    """Whether every word typed is in the game's name, maker or year."""
    words = text.casefold().split()
    seen = " ".join(str(row.get(key) or "") for key in ("name", "manufacturer", "year")).casefold()
    return all(word in seen for word in words)


def tile_line(row: Mapping[str, Any], fitting: Fitting) -> str:
    """Maker and year, then how many releases the page lists for the game, in the kind's
    own noun where they are all of one kind."""
    said = str(row.get("said") or "")
    listings = {listing for listing, _one in fitting}
    count = len(fitting)
    if not count:
        return said
    if len(listings) == 1:
        many = t(TILE_COUNT[next(iter(listings))], count=count)
    else:
        many = t("manager.discovery.ask.tile.releases", count=count)
    return t("manager.discovery.ask.tile_line", said=said, count=many) if said else many


def opening_on(state: Mapping[str, Any], tab: str) -> dict[str, Any] | None:
    """The view a page sent this tab to, if it did. It stays through a redraw of the tab,
    and the tab calls `drop_opening` once the user picks another view."""
    one = state.get("opening")
    return one if one and one.get("tab") == tab else None


def keep_search(opening: dict[str, Any], text: str | None) -> None:
    """The words in the box as the opening holds them, so a redraw of the tab opens it on
    what the box says now."""
    if (text or "").strip():
        opening["search"] = text
    else:
        opening.pop("search", None)


def drop_opening(state: dict[str, Any], tab: str) -> None:
    if opening_on(state, tab):
        state.pop("opening", None)


def subject_question(subject: Mapping[str, str]) -> dict[str, Any]:
    """What a subject page is about, as a question: the author, maker or theme alone."""
    kind, name = subject["type"], subject["name"]
    return {"by": [name]} if kind == "author" else {SUBJECT_KEY[kind]: name}




def _back(address: str, leave: Callable[[], Awaitable[None]]) -> None:
    """The way back to where the page was opened from: an anchor, so the address means
    something to the browser."""
    with panel.link_tile(
            to=address,
            classes="manager-link manager-link--inplace manager-subject-back") as link:
        ui.icon(verbs.BACK)
    link.props["aria-label"] = t("word.back")
    link.tooltip(t("word.back"))
    link.on("click", leave)


def _switch(label: str, value: bool, on_change: Callable[[Any], Any], hint: str = "") -> ui.row:
    row = ui.row().classes("items-center gap-1 no-wrap")
    with row:
        ui.label(label).classes("manager-label")
        panel.switch(value, on_change)()
    if hint:
        row.tooltip(hint)
    return row


def page(*, subject: Mapping[str, str], state: dict[str, Any],
         people: Sequence[Mapping[str, Any]], rows: Sequence[Mapping[str, Any]],
         releases: Mapping[str, Any], kinds: Collection[str], fields: Mapping[str, str],
         address: str, open_game: Callable[[Mapping[str, Any]], Awaitable[None]],
         leave: Callable[[], Awaitable[None]],
         see_all: Callable[[dict[str, Any], str], Awaitable[None]]) -> Callable[[str], None]:
    """The page of one author's, maker's or theme's work, drawn into the current container.

    What it shows is a question like any other, built from the subject and the page's own
    choices, so Open in Grid hands the grid the same one and the words typed in the box.
    `kinds` is the VPS kinds the library collects and `fields` the row field each one's
    state is in. It answers the function that narrows the wall to the games whose name,
    maker or year hold the words typed, for the search box on the tab row.

    A choice changes the counts, the wall and the page's question in place, so the control
    under the pointer is not redrawn."""
    kind, name = subject["type"], subject["name"]
    base = subject_question(subject)
    own = state.setdefault("page", {})
    if own.get("for") != (kind, name):
        own.clear()
        own["for"] = (kind, name)
    by_id = {str(row["id"]): row for row in rows}
    counted = any(row.get("in_library") not in (None, "", NOT_COMPARED) for row in rows)
    playable = launchers.playable_formats(launchers.get_launcher_store().launchers())
    found_for: dict[tuple[str, bool, bool], dict[str, Fitting]] = {}
    typed = {"text": ""}

    def hiding() -> bool:
        return kind == "author" and bool(own.get("hide_mods"))

    def playing() -> bool:
        return bool(own.get("playable", True))

    def question(listing: str, *, narrowed: bool | None = None) -> dict[str, Any]:
        formats = list(playable) if listing == TABLES and (
            playing() if narrowed is None else narrowed) else []
        return vps_question.clean({**base, "kind": listing,
                                   "mod": "originals" if hiding() else "", "format": formats})

    def found(listing: str, *, narrowed: bool | None = None) -> dict[str, Fitting]:
        key = (listing, hiding(), playing() if narrowed is None else narrowed)
        if key not in found_for:
            found_for[key] = dict(vps_question.games_found(
                question(listing, narrowed=narrowed), people, rows, releases))
        return found_for[key]

    def listed(listing: str, show: str) -> dict[str, Fitting]:
        return {game: one for game, one in found(listing).items()
                if in_show(show, by_id[game], one, fields, of_author=kind == "author")}

    listed_kinds = [listing for listing in kinds if found(listing, narrowed=False)]
    if own.get("kind") not in listed_kinds:
        own["kind"] = ""
    if not counted or own.get("show") not in SHOW_WORD:
        own["show"] = EVERYTHING

    def kind_options() -> dict[str, str]:
        show = own["show"]
        return {"": t("manager.discovery.counted", name=t("word.all"),
                      count=len(listed("", show))),
                **{listing: t("manager.discovery.counted", name=t(KIND_MANY[listing]),
                              count=len(listed(listing, show))) for listing in listed_kinds}}

    def show_options() -> dict[str, str]:
        every = found(own["kind"])
        return {show: t("manager.discovery.counted", name=t(word),
                        count=sum(1 for game, one in every.items()
                                  if in_show(show, by_id[game], one, fields,
                                             of_author=kind == "author")))
                for show, word in (SHOW_WORD if kind == "author" else SHOW_WORD_OF_GAMES).items()}

    def grid_question() -> dict[str, Any]:
        show = own["show"]
        owned = {YOURS: "yes", WITHOUT: "yes", ELSEWHERE: "no"}.get(show, "")
        return vps_question.clean({**question(own["kind"]), "owned": owned,
                                   "held": "missing" if show == WITHOUT else ""})

    def settle() -> None:
        kind_pick.set_options(kind_options(), value=own["kind"])
        if counted:
            show_pick.set_options(show_options(), value=own["show"])
        playable_row.set_visibility(own["kind"] == TABLES and bool(playable))
        wall()

    def pick_kind(event: Any) -> None:
        if event.value == own["kind"]:
            return
        own.update(kind=event.value, limit=WALL_AT_FIRST)
        settle()

    def pick_show(event: Any) -> None:
        if event.value != own["show"]:
            own.update(show=event.value, limit=WALL_AT_FIRST)
            settle()

    def pick_hiding(event: Any) -> None:
        own.update(hide_mods=bool(event.value), limit=WALL_AT_FIRST)
        settle()

    def pick_playable(event: Any) -> None:
        if bool(event.value) != playing():
            own.update(playable=bool(event.value), limit=WALL_AT_FIRST)
            settle()

    def type_in(text: str) -> None:
        typed["text"] = text
        own["limit"] = WALL_AT_FIRST
        wall()

    root = ui.column().classes("w-full grow min-h-0 gap-0 manager-subject-root")
    with root:
        with ui.row().classes(
                "w-full items-center gap-x-4 gap-y-2 px-3 py-2 mb-2 shrink-0 "
                "manager-panel manager-subject-bar"):
            with ui.row().classes("items-center gap-3 no-wrap manager-subject-who"):
                _back(address, leave)
                with ui.column().classes("gap-0 min-w-0"):
                    person = vps_question.person_named(name, people) if kind == "author" else None
                    title = ui.label(name).classes("manager-subject-name truncate")
                    if person and person["aliases"]:
                        title.tooltip(t("manager.discovery.ask.also",
                                        names=", ".join(person["aliases"])))
                    ui.label(t(SUBJECT_WORD[kind])).classes("text-xs manager-cell-made")
            kind_pick = dropdown.Select(
                kind_options(), value=own["kind"], label=t("word.kind"), look=dropdown.OUTLINED,
                on_change=pick_kind).classes("manager-subject-kind")
            with ui.row().classes("items-center gap-4 manager-subject-filters"):
                if counted:
                    show_pick = dropdown.Select(
                        show_options(), value=own["show"], label=t("manager.discovery.ask.show"),
                        look=dropdown.OUTLINED, on_change=pick_show).classes(
                            "manager-subject-show")
                if kind == "author":
                    _switch(t("manager.discovery.ask.hide_mods"), hiding(), pick_hiding)
                playable_row = _switch(
                    t("manager.discovery.ask.playable"), playing(),
                    pick_playable,
                    t("manager.discovery.ask.playable.help",
                      formats=t("manager.discovery.ask.formats", count=len(playable),
                                first=", ".join(playable[:-1]), last=playable[-1])
                      if playable else ""))
                playable_row.set_visibility(own["kind"] == TABLES and bool(playable))
            with ui.row().classes("items-center gap-2 no-wrap manager-subject-end"):
                count = ui.label("").classes("text-xs manager-label")
                panel.icon_action(
                    t("manager.discovery.ask.see_all"),
                    lambda: see_all(grid_question(), typed["text"]), icon=verbs.OPEN_OUT,
                    hint=t("manager.discovery.ask.see_all.help"))()
        wall_box = ui.column().classes("w-full grow min-h-0 gap-0")

    def wall() -> None:
        shown = {game: one for game, one in listed(own["kind"], own["show"]).items()
                 if narrows(by_id[game], typed["text"])}
        count.text = t("manager.discovery.ask.games", count=len(shown))
        ordered = sorted(shown, key=lambda game: max(
            (str(one.get("added") or "") for _listing, one in shown[game]), default=""),
            reverse=True)
        limit = int(own.get("limit", WALL_AT_FIRST))
        wall_box.clear()
        with wall_box, ui.scroll_area().classes("w-full grow min-h-0 px-3"):
            if not ordered:
                ui.label(t("manager.discovery.ask.nothing")).classes("manager-help")
            with ui.element("div").classes("manager-wall"):
                for game in ordered[:limit]:
                    row, fitting = by_id[game], shown[game]
                    state_of, names = standing(row, fitting, releases.get(game) or {}, fields,
                                               people, name if kind == "author" else "",
                                               counted=counted)
                    tip = (t("manager.discovery.ask.have_other.help", names=", ".join(names))
                           if state_of == OTHER else
                           t("manager.discovery.ask.have_unknown.help", game=row["name"],
                             kind=t(KIND_ONE[unmatched_kind(row, fitting, fields)]))
                           if state_of == UNKNOWN else "")
                    _tile(row, tile_line(row, fitting), (state_of, tip), partial(open_game, row))
            if len(ordered) > limit:
                panel.action(t("manager.discovery.ask.more", count=len(ordered) - limit),
                             lambda: more(limit),
                             icon=verbs.ADD, hint=panel.NO_HINT)()

    def more(limit: int) -> None:
        own["limit"] = limit + WALL_AT_FIRST * 2
        wall()

    settle()
    return type_in
