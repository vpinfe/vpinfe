"""Matching a file to what VPS publishes for its game: the release a table is, or the
published record a game's file is.

One dialog for both, so the two cannot drift. A click selects and **Match to This**
commits. The list is the one VPS answers in, each mod under the release it is based on,
and **nothing here ranks or scores it**: what the dialog does show is the person's own
file - its authors and its version - beside the list, so the comparison is theirs to
make.
"""

from __future__ import annotations

import html
import re
from collections.abc import Collection
from dataclasses import dataclass
from typing import Any

from nicegui import ui

from common import icons
from common.i18n import t
from common.online import vps_formats
from manager.kit import candidates, game_tables, panel, verbs, when
from manager.kit import dialog as frame
from manager.library import vps_match

# "g5k and 3rdaxis" is two people, and so is "g5k & 3rdaxis".
_PEOPLE = re.compile(r",|&|\s+and\s+", re.IGNORECASE)
CHOSEN = "manager-source-row--chosen"


@dataclass(frozen=True)
class Yours:
    """The file as the person has it, drawn as a row of the list it is compared against.

    `name` is what the title quotes. `file` heads the row, `authors` and `facts` are the
    two lines under it and `version` goes in the column the releases' versions share.
    `picture` is its own picture where it is one.
    """

    name: str
    file: str
    authors: tuple[str, ...] = ()
    facts: str = ""
    version: str = ""
    glyph: str = icons.TABLES
    picture: str = ""


async def ask(yours: Yours, listed: list[dict[str, Any]], *, bound: str,
              formats: Collection[str] = (), empty: str = "", reason: str = "",
              place: str = "", files: bool = False) -> Any:
    """The `vps_file_id` picked, `""` to take the match back, or what leaving answers:
    nothing, or in a walk (`place` is its position) Skip and Stop.

    Only the releases `formats` can play are listed. `empty` is the line shown where VPS
    lists nothing, with `reason` behind it. `files` words the dialog for a game's
    published files rather than a table's releases.
    """
    words = _FILES if files else _RELEASES
    fitting = [one for one in listed
               if one.get("vps_file_id") and vps_formats.fits(one.get("format"), formats)]
    kind = next(iter(formats)) if len(formats) == 1 else ""
    shared = _people(yours.authors)
    chosen = {"id": bound}
    rows: dict[str, tuple[ui.element, ui.element]] = {}

    with frame.opened(t(words["title"], name=yours.name), size="wide",
                      tall=True, ignore_outside_click=True) as box:
        if place:
            ui.label(place).classes("manager-help")
        with frame.band(t("manager.release_match.yours")):
            pass
        with frame.well():
            _yours_row(yours)
        with frame.band(t(words["heading"])):
            count = ui.label("").classes("manager-band-count")
        with frame.well(scrolls=True):
            with ui.row().classes("manager-well-head no-wrap"):
                with ui.element("div").classes("grow min-w-0"):
                    search = frame.field(placeholder=t(words["filter"]))
            listing = ui.column().classes("w-full gap-0 manager-well-rows")
        with frame.footer():
            if bound:
                frame.aside(t("manager.vps_match.clear_match"), lambda: box.submit(""),
                            icon=verbs.UNMATCH)
            frame.leave(box, bool(place))
            commit = frame.answer(t("manager.vps_match.pick"),
                                  lambda: box.submit(chosen["id"]), icon=verbs.MATCH)
        commit.disable()

        def settle() -> None:
            """Match to This is for a different match than the one in place, and only for a
            row the filter still shows."""
            commit.set_enabled(chosen["id"] in rows and chosen["id"] != bound)

        def take(release_id: str) -> None:
            chosen["id"] = release_id
            settle()
            for found, (row, mark) in rows.items():
                row.classes(add=CHOSEN) if found == release_id else row.classes(remove=CHOSEN)
                mark.clear()
                if found == release_id:
                    with mark:
                        ui.icon(verbs.DONE)

        def draw() -> None:
            said = str(search.value or "").strip()
            shown = [one for one in fitting if _matches(one, said)]
            rows.clear()
            listing.clear()
            with listing:
                if not listed:
                    if empty:
                        panel.line(empty, hint=reason)
                elif not fitting:
                    ui.label(
                        t("manager.release_match.none_fit", format=vps_formats.name(kind))
                        if kind else t("manager.release_match.none_fit_any")
                    ).classes("manager-help")
                elif not shown:
                    ui.label(t(words["no_match"], said=said)).classes("manager-help")
                for release, under in in_lineage(shown):
                    _row(release, bound=bound, chosen=chosen["id"], under=under, yours=yours,
                         shared=shared, pick=take, rows=rows)
            count.text = _count(words, len(shown), len(fitting), bool(said), kind)
            settle()

        search.on_value_change(draw)
        draw()
        frame.focus(box, search)
        _scroll_to_chosen(box, rows, chosen["id"])

    return await box


def _count(words: dict[str, str], shown: int, total: int, filtering: bool, kind: str) -> str:
    if not total:
        return ""
    if filtering:
        return t("manager.release_match.of_count", shown=shown, total=total)
    return t(words["count_kind"] if kind else words["count"], count=total, format=kind)


def _scroll_to_chosen(box: ui.dialog, rows: dict[str, tuple[ui.element, ui.element]],
                      chosen: str) -> None:
    """The match already in place in view when the dialog opens, in a list longer than it."""
    if chosen not in rows:
        return
    row_id = rows[chosen][0].html_id
    box.on("show", lambda: ui.run_javascript(
        f"document.getElementById('{row_id}')?.scrollIntoView({{block: 'nearest'}})"))


def _yours_row(yours: Yours) -> None:
    with ui.row().classes("items-center gap-3 w-full no-wrap manager-source-row "
                          "manager-source-row--yours"):
        candidates.thumb(yours.picture, yours.glyph)
        with ui.column().classes("gap-0 min-w-0 grow"):
            ui.label(yours.file).classes("manager-slot-file")
            if yours.authors:
                ui.label(", ".join(yours.authors[:4])).classes("manager-help")
            if yours.facts:
                ui.label(yours.facts).classes("manager-help")
        _end(yours.version)


def _end(version: str, *, same: bool = False, url: str = "",
         picked: bool = False) -> ui.element:
    """The right-hand columns: version, the way out to the release's page, the check.
    Each has the width it has on every row, so the file's own lines up with the list."""
    with ui.row().classes("items-center gap-0 no-wrap shrink-0 manager-pick-end"):
        label = ui.label(version).classes(
            "manager-pick-version" + (" manager-have" if same else ""))
        if version:
            label.tooltip(version)
        with ui.element("span").classes("manager-pick-slot"):
            if url:
                # Or opening the page would also pick the row.
                with ui.element("span").on("click.stop", js_handler="() => {}") \
                        .on("keydown.stop", js_handler="() => {}"):
                    panel.out(to=url, hint=t("manager.workbench.open_release"))()
        with ui.element("span").classes("manager-pick-slot manager-pick-mark") as mark:
            if picked:
                ui.icon(verbs.DONE)
    return mark


def _row(release: dict[str, Any], *, bound: str, chosen: str, under: bool, yours: Yours,
         shared: set[str], pick: Any,
         rows: dict[str, tuple[ui.element, ui.element]]) -> None:
    """One published file or release: what VPS calls it, who made it, what it carries."""
    said = str(release.get("vps_file_id") or "")
    authors = [str(name) for name in (release.get("authors") or [])]
    comment = str(release.get("comment") or "").strip()
    version = str(release.get("version") or "")
    made_by = _authors_markup(authors, shared)
    row = ui.row().classes(
        "items-center gap-3 w-full no-wrap manager-source-row manager-source-row--pick "
        "manager-source-row--entry" + (" manager-source-row--under" if under else "")
        + (f" {CHOSEN}" if said == chosen else ""))
    row.props("tabindex=0 role=option")
    with row:
        candidates.thumb(str(release.get("img_url") or ""), yours.glyph, vps_match.NO_PICTURE)
        with ui.column().classes("gap-0 min-w-0 grow"):
            with ui.row().classes("items-center gap-2 no-wrap min-w-0"):
                if comment:
                    ui.label(comment).classes("manager-source-name")
                elif made_by:
                    _markup(made_by).classes("manager-source-name")
                else:
                    ui.label(version or t("manager.workbench.no_version")) \
                        .classes("manager-source-name")
                if said == bound:
                    panel.state(t("word.current"), "on")()
            if comment and made_by:
                _markup(made_by).classes("manager-help")
            facts = _facts(release)
            if facts:
                ui.label(facts).classes("manager-help")
        mark = _end(version, same=bool(version) and version == yours.version,
                    url=str(release.get("url") or ""), picked=said == chosen)
    rows[said] = (row, mark)
    row.on("click", lambda: pick(said))
    row.on("keydown.enter", lambda: pick(said))


def _markup(line: str) -> ui.html:
    return ui.html(line, sanitize=False, tag="div")


def _authors_markup(authors: list[str], shared: set[str]) -> str:
    """Up to three authors, one marked where the file's authors include them too."""
    parts = [f'<span class="manager-have">{html.escape(name)}</span>'
             if name.strip().lower() in shared else html.escape(name)
             for name in authors[:3]]
    line = ", ".join(parts)
    return t("manager.release_match.and_more", names=line, count=len(authors) - 3) \
        if len(authors) > 3 else line


def _people(authors: Collection[str]) -> set[str]:
    return {name.strip().lower() for one in authors for name in _PEOPLE.split(str(one))
            if name.strip()}


def _facts(release: dict[str, Any]) -> str:
    """What the release carries: that it is a mod and of what, its features, when it
    was last updated."""
    facts = game_tables.release_facts(release)
    if release.get("updated_at"):
        facts.append(t("manager.release_match.updated", when=when.ago(release["updated_at"])))
    return game_tables.JOIN.join(facts)


def _matches(release: dict[str, Any], said: str) -> bool:
    """Every word typed is somewhere in the name, version, format, authors or features."""
    if not said:
        return True
    held = " ".join(str(one) for one in (
        release.get("comment"), release.get("version"), release.get("format"),
        *(release.get("authors") or []), *(release.get("features") or []))
        if one).lower()
    return all(word in held for word in said.lower().split())


def in_lineage(releases: list[dict[str, Any]]) -> list[tuple[dict[str, Any], bool]]:
    """`releases` with each mod straight after the one it is based on, and whether it
    sits under one. One step in however deep the chain; a mod of a release not in the
    list keeps its place, and a loop of links is placed where its first member is."""
    listed = {str(one.get("vps_file_id") or "") for one in releases} - {""}
    under: dict[str, list[dict[str, Any]]] = {}
    for one in releases:
        parent = str((one.get("mod_of") or {}).get("vps_file_id") or "")
        if parent in listed:
            under.setdefault(parent, []).append(one)
    placed: set[int] = set()
    ordered: list[tuple[dict[str, Any], bool]] = []

    def place(one: dict[str, Any], nested: bool) -> None:
        if id(one) in placed:
            return
        placed.add(id(one))
        ordered.append((one, nested))
        for mod in under.get(str(one.get("vps_file_id") or ""), []):
            place(mod, True)

    for one in releases:
        if str((one.get("mod_of") or {}).get("vps_file_id") or "") not in listed:
            place(one, False)
    for one in releases:
        place(one, False)
    return ordered


_RELEASES = {
    "title": "manager.release_match.title", "heading": "manager.release_match.releases",
    "filter": "manager.release_match.filter", "no_match": "manager.release_match.no_match",
    "count": "manager.release_match.count", "count_kind": "manager.release_match.count_kind",
}
_FILES = {
    "title": "manager.release_match.files_title", "heading": "manager.release_match.files",
    "filter": "manager.release_match.files_filter",
    "no_match": "manager.release_match.files_no_match",
    "count": "manager.release_match.files_count",
    "count_kind": "manager.release_match.files_count",
}
