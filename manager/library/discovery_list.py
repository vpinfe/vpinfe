"""What a kind's section lists: the files the game holds, then the releases VPS lists and the
pictures a media source offers that it does not.

A held file is a card with the Games pane's own Replace and Remove, and the release it is
matched to. Each release ends in one glyph and acts at once; pressing its row does nothing but
choose the one Full shows beside the cards, which is always drawn and revealed by CSS, so the
toggle never rebuilds.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Any

from nicegui import run, ui

from common import timestamps
from common.failures import why
from common.games import asset_resolver, opened_pages
from common.i18n import t
from common.media.media_specs import IMAGE_FAMILY
from common.online import vps_find, vps_kinds, vps_updates
from manager.kit import (
    art,
    candidates,
    deeplink,
    game_tables,
    kind_words,
    media_ownership,
    panel,
    verbs,
    when,
)
from manager.kit.on_page import on_page
from manager.library import (
    discovery_ext,
    discovery_get,
    discovery_marks,
    discovery_pictures,
    file_acts,
    file_facts,
    getting_file,
    import_dialog,
    mediasource,
    mediaview,
    release_match,
    vps_records,
)
from manager.library.discovery_get import Offered, Pick
from manager.library.discovery_kinds import LONG_KIND, Kind, Scene, short_name

logger = logging.getLogger("vpinfe.manager.library.discovery_list")


@dataclass
class _View:
    """What a kind's list is showing: the filter, the release the Full card holds and the
    pictures the sources' pages gave. Kept in the page's state, so a redraw does not lose it."""

    text: str = ""
    pressed: str = ""
    shells: dict[str, ui.element] = field(default_factory=dict)
    looked: dict[str, str] = field(default_factory=dict)
    reading: bool = False


def _when(record: dict[str, Any]) -> str:
    stamp = when.parsed(record.get("updated_at"))
    return when.day(stamp.astimezone()) if stamp else ""


def _head(record: dict[str, Any]) -> str:
    return vps_records.told(record) or t("manager.discovery.unnamed_file")


def _detail(record: dict[str, Any], *, features: bool) -> str:
    return game_tables.JOIN.join(part for part in (
        _when(record), vps_records.site_of(record)[0],
        vps_records.what_it_is(record, features=features)) if part)


def _record_of(kind: Kind, vps_file_id: str) -> dict[str, Any] | None:
    return next((one for one in kind.records
                 if vps_file_id and str(one.get("vps_file_id")) == vps_file_id), None)


def _last_opened(kind: Kind, scene: Scene) -> tuple[str, str]:
    """The release whose page was opened most recently for this game and is not held yet, with
    its words, or `("", "")`. Read when a file arrives, so a page opened a moment ago counts."""
    tied = _tied(kind)
    for one in opened_pages.recent(scene.vps_id, kind.listed_as):
        record = _record_of(kind, str(one.get("vps_file_id") or ""))
        if record and str(record["vps_file_id"]) not in tied:
            return str(record["vps_file_id"]), _head(record)
    return "", ""


@dataclass
class _Held:
    """A file the game holds: its name on disk, the release it is matched to, and the rows
    the Games pane draws for it."""

    name: str
    vps_file_id: str
    row: dict[str, Any] | None = None
    detail: dict[str, Any] = field(default_factory=dict)


def _named(row: dict[str, Any], name: str) -> bool:
    return name.casefold() in (str(row.get("file") or "").casefold(),
                               Path(str(row.get("path") or "")).name.casefold())


def _helds(kind: Kind) -> list[_Held]:
    """The files the game holds of this kind. A media kind holds one: its slot."""
    if kind.media:
        if not kind.held:
            return []
        slot_path = str(kind.slot.get("path") or "")
        row = next((each for each in kind.rows if slot_path and each.get("path") == slot_path),
                   kind.rows[0] if kind.rows else None)
        name = str(kind.slot.get("file") or kind.detail.get("file") or (row or {}).get("file")
                   or "")
        return [_Held(name, str(kind.slot.get("matched_to") or ""), row, kind.detail)]
    found = []
    for one in kind.mine:
        name = str(one.get("name") or "")
        row = next((each for each in kind.rows if _named(each, name)), None)
        found.append(_Held(name, str(one.get("vps_file_id") or ""), row,
                           kind.details.get(str(row["id"]), {}) if row else {}))
    return found


def _tied(kind: Kind) -> set[str]:
    """The releases the game's files are matched to."""
    return {one.vps_file_id for one in _helds(kind) if one.vps_file_id}


def _origin(held: _Held) -> str:
    """The catalog a file was fetched from, where the ledger names one: not that you added it,
    or that nobody recorded where it came from."""
    origin = str((held.row or {}).get("origin") or "")
    named = media_ownership.source_name(origin)
    generic = {media_ownership.YOU, media_ownership.UNKNOWN, media_ownership.RECORDED,
               media_ownership.BACKGLASS_FILE, media_ownership.B2SPRO_FILE}
    return "" if named in generic else named


def _enlarges(box: ui.element, show: Callable[[], None]) -> None:
    box.classes("manager-record-thumb").props("tabindex=0 role=button")
    box.props["aria-label"] = t("word.enlarge")
    box.on("click", show).on("keydown.enter", show).on("keydown.space.prevent", show)


def _art(src: str, family: str, glyph: str) -> None:
    """A picture or the first frame of a video, or the kind's glyph where there is none."""
    if src and family == "video":
        ui.html(f'<video src="{src}#t=0.1" preload="metadata" muted playsinline></video>')
    elif src and family == "image":
        ui.html(f'<img src="{src}" loading="lazy" alt="">')
    else:
        ui.icon(glyph).classes("manager-source-thumb-glyph")


def body(context: dict[str, Any], scene: Scene, kind: Kind) -> None:
    """The section of one kind: why VPS could not be read, what the game has and what else
    is available."""
    holder = ui.column().classes("gap-1 w-full min-w-0 manager-kind-body")
    with holder:
        if kind.unread:
            panel.line(t("manager.discovery.files_failed"), hint=kind.unread)
        helds = _helds(kind)
        rows = [one for one, _nested in release_match.in_lineage(_unheld(kind))]
        view =context["state"].setdefault("discovery_views", {}).setdefault(
            (scene.vps_id, kind.key), _View())
        ids = [str(one.get("vps_file_id") or "") for one in rows]
        if view.pressed not in ids:
            view.pressed = ids[0] if ids else ""
        use_file = _use_file(context, scene, kind, holder)
        with ui.element("div").classes("manager-kind-cols" + (" manager-kind-cols--card"
                                                              if rows else "")):
            with ui.column().classes("gap-2 w-full min-w-0 manager-kind-have"):
                _have(context, scene, kind, helds, use_file)
            card = ui.column().classes("gap-0 w-full min-w-0 manager-card-compare")
            if rows or kind.offers:
                with ui.column().classes("gap-0 w-full min-w-0 manager-kind-available"):
                    _available(context, scene, kind, rows, view, card)


def _ours(kind: Kind) -> tuple[tuple[str, ...], str]:
    """Our kinds a file dropped on this section is for, and which inventory holds them."""
    if kind.media:
        return discovery_get.media_ours(kind.listed_as, kind.media), vps_kinds.MEDIA
    return discovery_get.of_listing(kind.listed_as)


def _match_of(context: dict[str, Any], scene: Scene, kind: Kind, *, vps_file_id: str = "",
              label: str = "") -> import_dialog.ReleaseMatch:
    """What the import dialog is told of a file taken as this kind, by the VPS file it is
    taken as where one is named."""
    ours, held_in = _ours(kind)
    return import_dialog.ReleaseMatch(
        _chooser(context, kind, scene), kind.label,
        kind_words.vps_name(ours[0], held_in == vps_kinds.MEDIA), vps_file_id=vps_file_id,
        label=label, picture_of=_picture_of(context, scene, kind))


def _use_file(context: dict[str, Any], scene: Scene, kind: Kind,
              holder: ui.element) -> Callable[[], None]:
    """Make the section take a dropped file, and return Import a File…."""
    ours, held_in = _ours(kind)
    return discovery_get.use_a_file(
        context, scene.game_id, holder, ours=ours, held_in=held_in,
        add_table=kind.listed_as == "tableFiles",
        match=_match_of(context, scene, kind) if kind.records else None,
        opened=lambda: _last_opened(kind, scene))


def _heading(word: str, count: int = 0) -> None:
    with ui.row().classes("items-baseline gap-2 manager-kind-head"):
        ui.label(word).classes("manager-card-title")
        if count:
            ui.label(str(count)).classes("manager-select-count")


# ---------------------------------------------------------------------------------------
# Have


def _have(context: dict[str, Any], scene: Scene, kind: Kind, helds: list[_Held],
          use_file: Callable[[], None]) -> None:
    """The files the game holds, a card each; or the box a file is dropped in. Where the
    cards have no Replace to offer, Import a File… stands under them."""
    _heading(t("manager.discovery.have"), len(helds))
    if not helds:
        with ui.column().classes("items-center gap-1 w-full manager-dropbox"):
            ui.label(t("manager.discovery.none")).classes("manager-empty-title")
            ui.label(t("manager.discovery.drop_here")).classes("manager-help")
            use_file()
        return
    replaceable = False
    for held in helds:
        acts = _acts(context, scene, kind, held)
        replaceable = replaceable or file_acts.REPLACE in acts
        _held_card(context, scene, kind, held, acts)
    if not replaceable:
        use_file()


def _acts(context: dict[str, Any], scene: Scene, kind: Kind,
          held: _Held) -> dict[str, panel.Verb]:
    """Replace and Remove, the verbs the Games pane offers this file. A table is not given
    them here."""
    if held.row is None or kind.listed_as == "tableFiles":
        return {}
    library, state = context["library"], context["state"]

    async def after(_games: list[str]) -> None:
        await discovery_get.settle(state, scene.vps_id)

    acts = file_acts.held_acts(
        library, file_acts.files_of(library, [held.row])[0],
        vps_kinds.MEDIA if kind.media else vps_kinds.ASSET, state, after)
    # A card the game's lens has nothing to take from (a stand-in, a file a set hides) is
    # left without Remove, which would only say so.
    if kind.media and file_acts.removal([], {}, "", held.detail.get("tiers"), held.row) is None:
        acts.pop(file_acts.REMOVE, None)
    return acts


def _picture(context: dict[str, Any], scene: Scene, kind: Kind, held: _Held,
             record: dict[str, Any] | None) -> tuple[str, str, Callable[[], None] | None]:
    """What a held file draws as: its address, the family it draws as and what enlarges it.
    A picture is drawn from the file itself, in place of the release's."""
    if kind.media:
        version = str(kind.slot.get("version") or "")
        return (art.media(scene.game_id, kind.media, version=version, size=art.CELL),
                kind.held_family,
                partial(mediaview.open_viewer,
                        art.media(scene.game_id, kind.media, version=version), kind.media,
                        kind.label))
    own = _own_picture(context, scene, kind, held.name)
    if own:
        return (art.media(scene.game_id, own[0], version=own[1], size=art.CELL), "image",
                partial(mediaview.open_pages, [
                    art.media(scene.game_id, own[0], version=own[1], size=art.PANEL)],
                    kind.label))
    return str(record.get("img_url") or "") if record else "", "image", None


def _held_card(context: dict[str, Any], scene: Scene, kind: Kind, held: _Held,
               acts: dict[str, panel.Verb]) -> None:
    record = _record_of(kind, held.vps_file_id)
    src, family, show = _picture(context, scene, kind, held, record)
    spec = (file_facts.media_spec if kind.media else file_facts.asset_spec)(held.detail)
    with ui.column().classes("gap-1 manager-kind-card manager-have-card"):
        with ui.row().classes("items-start gap-3 no-wrap w-full"):
            with ui.element("div").classes("manager-rel-pic") as pic:
                candidates.thumb(src, kind.glyph, t("manager.discovery.no_picture"), family)
            if show is not None:
                _enlarges(pic, show)
            with ui.column().classes("gap-0 grow min-w-0"):
                ui.label(short_name(held.name, scene.title)).classes("truncate") \
                    .tooltip(held.name)
                if spec:
                    ui.label(spec).classes("manager-help truncate")
                if kind.records:
                    _match_line(context, scene, kind, held, record)
                _status_line(context, scene, kind, held)
        if kind.records and (move := _move(context, scene, held.vps_file_id)):
            _update_notice(context, scene, kind, held, record, move)
        with ui.element("div").classes("manager-slot-actions"):
            _verb(acts.get(file_acts.REPLACE), verbs.REPLACE)
            if kind.records:
                label, icon, hint = (
                    (t("manager.workbench.change_match_2"), verbs.MATCH,
                     t("manager.discovery.change_match.help")) if held.vps_file_id
                    else (t("manager.workbench.match"), verbs.NO_MATCH,
                          t("manager.discovery.match.help")))
                panel.action(label, partial(_match_held, context, scene, kind, held),
                             icon=icon, hint=hint)()
            _verb(acts.get(file_acts.REMOVE), verbs.REMOVE, danger=True)


def _verb(verb: panel.Verb | None, icon: str, *, danger: bool = False) -> None:
    if verb is not None:
        panel.action(verb.label, verb.run, icon=icon, danger=danger, hint=verb.hint,
                     enabled=verb.run is not None)()


def _match_line(context: dict[str, Any], scene: Scene, kind: Kind, held: _Held,
                record: dict[str, Any] | None) -> None:
    """The release a file is matched to, with the way to its page, or that it is matched to
    none."""
    with ui.row().classes("items-center gap-1 no-wrap w-full"):
        if not held.vps_file_id:
            ui.icon(verbs.NO_MATCH).classes("manager-help")
            ui.label(t("manager.discovery.not_matched")).classes("manager-help truncate")
            return
        ui.icon(verbs.MATCH).classes("manager-have")
        ui.label(t("manager.discovery.matched_to", release=_head(record))
                 if record else t("manager.workbench.delisted_file")).classes("truncate")
        if record:
            site, page = vps_records.site_of(record)
            if page:
                _way_out(scene, kind, held.vps_file_id, site, page,
                         vps_records.page_is_broken(record), lambda: None)


# ---------------------------------------------------------------------------------------
# Available


def _unheld(kind: Kind) -> list[dict[str, Any]]:
    """The releases the game is not matched to: those whose page was opened first, then the
    rest, each newest first."""
    tied = _tied(kind)
    rows = [one for one in vps_records.newest_first(kind.records)
            if str(one.get("vps_file_id") or "") not in tied]
    return sorted(rows, key=lambda one: str(one.get("vps_file_id") or "") not in kind.opened)


def _available(context: dict[str, Any], scene: Scene, kind: Kind, rows: list[dict[str, Any]],
               view: _View, card: ui.element) -> None:
    features = kind.listed_as == "tableFiles"
    _heading(t("manager.discovery.online"), len(rows) + len(kind.offers))
    if len(rows) > LONG_KIND:
        box = panel.search(t("manager.folder_picker.filter")).classes("w-full")
        box.value = view.text
        box.on_value_change(lambda event: filter_by(str(event.value or "")))

    def shown() -> list[dict[str, Any]]:
        text = view.text.strip().lower()
        if not text:
            return rows
        return [one for one in rows if text in " ".join((
            _head(one), _detail(one, features=features),
            str(one.get("comment") or ""))).lower()]

    def hosted_of(record: dict[str, Any]) -> Offered | None:
        return context.get("hosted", {}).get(str(record.get("vps_file_id") or ""))

    def picture_of(record: dict[str, Any]) -> str:
        return str(record.get("img_url") or view.looked.get(str(record.get("vps_file_id")), ""))

    @on_page
    async def show_pictures() -> None:
        if view.reading:
            return
        view.reading = True
        draw()
        offered = [one for one in (hosted_of(record) for record in shown()) if one]
        try:
            said = await asyncio.gather(*(discovery_ext.read_look(context, one, scene.game_id)
                                          for one in offered))
        finally:
            view.reading = False
        if card.is_deleted:
            return
        for one, found in zip(offered, said, strict=True):
            if found.get("pictures"):
                view.looked[str(one.row["vps_file_id"])] = str(found["pictures"][0])
        draw()

    def press(file_id: str) -> None:
        view.pressed = file_id
        shade(view)
        draw_card()

    def draw_card() -> None:
        card.clear()
        if not rows:
            return
        with card:
            _card(context, scene, kind, rows, view, features, press)

    def build() -> None:
        view.shells.clear()
        with ui.row().classes("items-center justify-end w-full"):
            if view.reading:
                ui.spinner(size="xs")
            elif any(hosted_of(one) for one in shown()) and not any(picture_of(one)
                                                                    for one in rows):
                panel.action(t("manager.discovery.show_pictures"), show_pictures,
                             icon=verbs.SHOW_PICTURES, inline=True,
                             hint=panel.NO_HINT)()
        with ui.column().classes("gap-0 w-full min-w-0 manager-rels"):
            for record, nested in release_match.in_lineage(shown()):
                _release(context, scene, kind, record, view, features, picture_of(record),
                         press, nested)
            _offers(context, scene, kind)
        shade(view)

    part = panel.region(build)

    def draw() -> None:
        part.refill()

    def filter_by(value: str) -> None:
        view.text = value
        part.refill()

    part()
    draw_card()


def shade(view: _View) -> None:
    for file_id, shell in view.shells.items():
        if file_id == view.pressed:
            shell.classes(add="manager-rel--pressed")
        else:
            shell.classes(remove="manager-rel--pressed")


def _own_picture(context: dict[str, Any], scene: Scene, kind: Kind,
                 name: str) -> tuple[str, str] | None:
    """The media slot and version to draw the held file from, where it is a picture the game's
    media route serves: a rule sheet is a document by kind and may still be a picture by
    extension."""
    ours, held_in = discovery_get.of_listing(kind.listed_as)
    if not scene.game_id or held_in != vps_kinds.MEDIA:
        return None
    if Path(name).suffix.lower() not in IMAGE_FAMILY:
        return None
    slot = discovery_get.slot_of((kind.media,) if kind.media else ours, held_in, name)
    held = ((context["library"].media.get(scene.game_id) or {}).get(slot) or {})
    return (slot, str(held.get("version") or "")) if held.get("present") else None


def _move(context: dict[str, Any], scene: Scene, vps_file_id: str, *,
          set_aside: bool = False) -> dict[str, Any] | None:
    """The later version of the release a held file is tied to, where VPS lists one and the
    update has not been skipped or ignored; with `set_aside`, the one that has been."""
    if not vps_file_id or not scene.game_id:
        return None
    library = context["library"]
    moves = (library.set_aside_moves if set_aside else library.update_moves)(scene.game_id)
    return next((move for move in moves if move["release_id"] == vps_file_id), None)


def _update_notice(context: dict[str, Any], scene: Scene, kind: Kind, held: _Held,
                   record: dict[str, Any] | None, move: dict[str, Any]) -> None:
    """The amber strip on a card whose release has a later version."""
    versions = t("manager.discovery.update.versions", then=move.get("then") or "",
                 now=move["now"]) if move.get("then") else str(move["now"])
    entries = discovery_marks.update_notice_verbs(
        context, str(context["discovery"]["name"]), kind.label, move)
    with ui.element("div").classes("manager-attention w-full"):
        ui.icon("error_outline").classes("manager-attention-icon")
        with ui.column().classes("gap-0 min-w-0 grow"):
            ui.label(t("manager.discovery.update.available")).classes("manager-attention-line")
            ui.label(versions).classes("manager-member-table")
        if record and kind.listed_as == "tableFiles":
            panel.action(
                t("manager.workbench.update"),
                partial(_get_table, context, scene, kind, record, held.name, False),
                icon=verbs.REPLACE,
                hint=t("manager.workbench.update.help", version=move["now"]))()
        if entries:
            _button, menu = panel.menu_button(verbs.MORE, t("manager.discovery.update.menu"))
            with menu:
                panel.verb_entries(entries, menu.close)


def _status_line(context: dict[str, Any], scene: Scene, kind: Kind, held: _Held) -> None:
    """One muted line under a held file: its source, and where its update is skipped or
    ignored, that and the link that takes the mark back."""
    source = _origin(held)
    aside = _move(context, scene, held.vps_file_id, set_aside=True) if kind.records else None
    if not source and not aside:
        return
    with ui.row().classes("items-center gap-x-1 no-wrap w-full min-w-0 manager-help"):
        if source:
            ui.label(t("manager.discovery.source_line", source=source)).classes("truncate min-w-0")
        if aside:
            if source:
                ui.label("·").classes("flex-none")
            ui.label(t("manager.discovery.update.ignored")
                     if aside["set_aside"] == vps_updates.IGNORED
                     else t("manager.discovery.update.skipped", version=aside["now"])
                     ).classes("flex-none")
            ui.label("·").classes("flex-none")
            stop = discovery_marks.take_back(
                context, str(context["discovery"]["name"]), kind.label, aside)
            ui.label(t("manager.discovery.mark.stop")).classes("manager-link flex-none") \
                .props("role=button tabindex=0").on("click", stop).on("keydown.enter", stop)


def _looked(context: dict[str, Any], scene: Scene, kind: Kind) -> dict[str, str]:
    """The pictures the pane has read from the source, by release."""
    view = context["state"].get("discovery_views", {}).get((scene.vps_id, kind.key))
    return view.looked if view else {}


def _picture_of(context: dict[str, Any], scene: Scene, kind: Kind) -> Callable[[str], str]:
    """The picture of a release: the one VPS lists, else the one read from the source."""
    def of(vps_file_id: str) -> str:
        record = _record_of(kind, vps_file_id) or {}
        return str(record.get("img_url") or _looked(context, scene, kind).get(vps_file_id, ""))

    return of


def _chooser(context: dict[str, Any], kind: Kind, scene: Scene,
             name: str = "") -> Callable[[str], Awaitable[tuple[str, str] | None]]:
    """The picker of a release, with the pictures the pane has read from the source and, where
    the file is the game's own, its name and picture."""
    async def choose(current: str) -> tuple[str, str] | None:
        looked = _looked(context, scene, kind)
        own = _own_picture(context, scene, kind, name) if name else None
        mine = art.media(scene.game_id, own[0], version=own[1], size=art.CELL) if own else ""
        # The release the file is tied to is that file, as it is on the held row.
        listed = [{**one, "img_url": (mine if current and one.get("vps_file_id") == current
                                      else "") or one.get("img_url")
                   or looked.get(str(one.get("vps_file_id")), "")} for one in kind.records]
        picked = await release_match.ask(
            release_match.Yours(scene.title, name, glyph=kind.glyph, picture=mine),
            listed, bound=current, files=True)
        if not isinstance(picked, str):
            return None
        record = _record_of(kind, picked)
        return (picked, _head(record)) if record else ("", "")

    return choose


def _source_key(kind: Kind, held: _Held) -> str:
    """Where a held file's match is recorded: readers look a ROM zip up under its path in
    the ROMs folder, not its bare name."""
    if kind.media:
        return str((held.row or {}).get("path") or held.name)
    if kind.listed_as == "romFiles":
        return f"{asset_resolver.ROMS_FOLDER}/{held.name}"
    return held.name


@on_page
async def _match_held(context: dict[str, Any], scene: Scene, kind: Kind, held: _Held) -> None:
    """Tie a file the game holds to the release the person says it is."""
    library, state = context["library"], context["state"]
    name = held.name
    chosen = await _chooser(context, kind, scene, name)(held.vps_file_id)
    if chosen is None or not scene.game_id:
        return
    try:
        if kind.listed_as == "tableFiles":
            for one in await run.io_bound(library.tables_for, scene.game_id) or ():
                if one.get("filename") == name:
                    await run.io_bound(library.set_table_source, scene.game_id, str(one["id"]),
                                       chosen[0])
        else:
            await run.io_bound(library.set_asset_source, scene.game_id, _source_key(kind, held),
                               chosen[0])
    except Exception as exc:  # noqa: BLE001 - the file stays; only the tie failed
        logger.warning("manager: could not tie %s to %s", name, chosen[0], exc_info=True)
        ui.notify(t("manager.discovery.tie_failed"), caption=why(exc), type="warning")
        return
    await discovery_get.settle(state, scene.vps_id)


def _release(context: dict[str, Any], scene: Scene, kind: Kind, record: dict[str, Any],
             view: _View, features: bool, picture: str, press: Callable[[str], None],
             nested: bool = False) -> None:
    file_id = str(record.get("vps_file_id") or "")
    wired = False
    with ui.column().classes("gap-0 min-w-0" if nested else "gap-0 w-full min-w-0") as shell:
        if nested:
            shell.classes("manager-source-row--under")
        view.shells[file_id] = shell
        with ui.element("div").classes("manager-rel manager-rel--press") as row:
            with ui.element("div").classes("manager-rel-pic") as pic:
                candidates.thumb(picture, kind.glyph, t("manager.discovery.no_picture"))
            if picture:
                _enlarges(pic, partial(mediaview.open_pages, [picture], kind.label))
            with ui.column().classes("gap-0 grow min-w-0"):
                ui.label(_head(record)).classes("truncate")
                ui.label(_detail(record, features=features)).classes("manager-help truncate")
            discovery_marks.release_mark_glyph(context, file_id)
            _end(context, scene, kind, record, lambda: opened())
        discovery_marks.release_marks(context, row, str(context["discovery"]["name"]),
                                      kind.label, record)
        row.on("click", lambda _event=None: press(file_id))
        noted = ui.column().classes("gap-0 w-full")

        def opened() -> None:
            """Say under the release when its page was opened, and make the row take a file
            dropped on it as that release."""
            nonlocal wired
            at = kind.opened.get(file_id)
            noted.clear()
            if at is None:
                return
            with noted:
                ui.label(t("manager.discovery.opened", when=when.ago(at.get("at"), inline=True))) \
                    .classes("manager-help manager-rel-opened")
            if not wired:
                wired = True
                ours, held_in = _ours(kind)
                discovery_get.use_a_file(
                    context, scene.game_id, shell, ours=ours, held_in=held_in,
                    add_table=kind.listed_as == "tableFiles",
                    match=_match_of(context, scene, kind, vps_file_id=file_id,
                                    label=_head(record)))

        opened()


def _end(context: dict[str, Any], scene: Scene, kind: Kind, record: dict[str, Any],
         opened: Callable[[], None]) -> None:
    """A release's end: what it can be used for, its download glyph or what its download is
    doing, and last the way to its page."""
    file_id = str(record.get("vps_file_id") or "")
    offered: Offered | None = context.get("hosted", {}).get(file_id)
    site, page = vps_records.site_of(record)
    with ui.row().classes("items-center gap-1 no-wrap shrink-0"):
        if kind.listed_as == "tableFiles" and kind.mine and scene.game_id:
            panel.icon_action(
                t("manager.discovery.use_instead"),
                partial(_get_table, context, scene, kind, record, "", True),
                icon=verbs.REPLACE, hint=t("manager.discovery.use_instead.help"))()
        if offered is not None:
            _download(context, scene, offered)
        if page:
            _way_out(scene, kind, file_id, site, page, vps_records.page_is_broken(record), opened)


@on_page
async def _get_table(context: dict[str, Any], scene: Scene, kind: Kind, record: dict[str, Any],
                     held_name: str, instead: bool) -> None:
    """Take the file of `record` to replace a table the game holds: the one named `held_name`,
    else the default. Update is the newer version of the release the table is tied to; Use
    Instead is another release."""
    library = context["library"]
    rows = await run.io_bound(library.tables_for, scene.game_id)
    swappable = [one for one in rows or () if game_tables.can_swap(one)]
    if held_name:
        table = next((one for one in swappable if one.get("filename") == held_name), None)
    else:
        table = (next((one for one in swappable if one.get("default")), None)
                 or (swappable[0] if swappable else None))
    if table is None:
        ui.notify(t("manager.table_swap.not_here"), type="warning")
        return
    game: dict[str, Any] = next(
        (one for one in library.games if str(one.get("id")) == scene.game_id), {})
    site, page = vps_records.site_of(record)
    file_id = str(record.get("vps_file_id") or "")
    getting_file.open_for(
        library, game_id=scene.game_id, game_dir=str(game.get("folder") or ""),
        table_id=str(table["id"]), page=page, site=site,
        title=t("manager.getting_file.use_instead" if instead
                else "manager.getting_file.update_to", version=_head(record)),
        release=getting_file.release_of(file_id, _head(record)), instead=instead,
        on_done=partial(discovery_get.settle, context["state"], scene.vps_id))


def _way_out(scene: Scene, kind: Kind, file_id: str, site: str, page: str, broken: bool,
             opened: Callable[[], None]) -> None:
    async def note() -> None:
        await run.io_bound(opened_pages.note, scene.vps_id, kind.listed_as, file_id, page)
        kind.opened[file_id] = {"vps_file_id": file_id, "at": timestamps.utc_now_iso()}
        opened()

    panel.out(to=page, on_click=note,
              hint=t("manager.discovery.link_broken") if broken
              else t("manager.discovery.open_on", site=site))()


def _download(context: dict[str, Any], scene: Scene, offered: Offered) -> None:
    """The download glyph of a file a source gets, or the file's state while it is on its way."""
    tray, pick = scene.tray, offered.pick
    said = t("manager.discovery.download_from", site=offered.name)

    def rest() -> None:
        panel.icon_action(
            said, partial(discovery_get.get, context, tray, scene.game_id, [pick]),
            icon=verbs.FROM_ONLINE, hint=said)()

    discovery_get.end(
        tray, pick, rest, discovery_get.try_again(context, tray, scene.game_id, pick),
        act=discovery_ext.acts_for(context, tray, lambda: scene.game_id, scene.listed, pick.key))


# ---------------------------------------------------------------------------------------
# Full: yours beside the release pressed


def _card(context: dict[str, Any], scene: Scene, kind: Kind, rows: list[dict[str, Any]],
          view: _View, features: bool, press: Callable[[str], None]) -> None:
    """The release pressed, with the way through the rest. Beside the cards in Full."""
    ids = [str(one.get("vps_file_id") or "") for one in rows]
    index = ids.index(view.pressed) if view.pressed in ids else 0
    record = rows[index]
    with ui.column().classes("gap-1 manager-kind-card"):
        with ui.row().classes("items-center gap-1 no-wrap w-full"):
            ui.label(t("manager.discovery.card_of", n=index + 1, count=len(rows))) \
                .classes("manager-help grow")
            panel.icon_action(
                t("word.previous"), lambda: press(ids[(index - 1) % len(ids)]),
                icon=verbs.EARLIER, hint=t("word.previous"), enabled=len(ids) > 1)()
            panel.icon_action(
                t("word.next"), lambda: press(ids[(index + 1) % len(ids)]),
                icon=verbs.LATER, hint=t("word.next"), enabled=len(ids) > 1)()
            offered = context.get("hosted", {}).get(ids[index])
            if offered is not None:
                _download(context, scene, offered)
        src = str(record.get("img_url") or view.looked.get(ids[index]) or "")
        if src:
            with ui.element("div").classes("manager-tile-art"):
                _art(src, "image", kind.glyph)
        ui.label(_head(record)).classes("truncate")
        ui.label(_detail(record, features=features)).classes("manager-help")
        _authors(context, record)


def _authors(context: dict[str, Any], record: dict[str, Any]) -> None:
    """Every author of the release, each a link to that author's page where the page can open
    one, and the names alone where a release has more than the two its head says."""
    names = [str(name) for name in (record.get("authors") or [])]
    state = context["state"]
    open_author = state.get("open_author")
    if open_author is not None and names:
        async def go(name: str) -> None:
            # The author's page is the list, which Full puts behind the pane.
            state.get("leave_full", lambda: None)()
            await open_author(name)

        with ui.row().classes("items-center gap-x-3 gap-y-0"):
            for name in names:
                panel.link(name, to="/manager?" + deeplink.query(
                    {"view": "discovery", "subject": {"type": vps_find.AUTHOR, "name": name}}),
                    on_click=partial(go, name))()
    elif len(names) > 2:
        ui.label(", ".join(names)).classes("manager-help")


# ---------------------------------------------------------------------------------------
# Pictures


def _size_name(pick: Pick) -> str:
    if pick.size in mediasource.SIZE_NAMES:
        return t(mediasource.SIZE_NAMES[pick.size])
    return pick.size


def _caption(scene: Scene, pick: Pick) -> str:
    source = str((scene.sources.get(pick.source) or {}).get("name") or pick.source)
    size = _size_name(pick)
    return t("manager.discovery.tile_caption", size=size, source=source) if size else source


def _offers(context: dict[str, Any], scene: Scene, kind: Kind) -> None:
    """The pictures the media sources offer, a row each."""
    tray = scene.tray
    for offer in kind.offers:
        pick = discovery_get.pick_of(offer)
        src = str(offer.get("url") or "")
        with ui.column().classes("gap-0 w-full min-w-0"):
            with ui.element("div").classes("manager-rel"):
                with ui.element("div").classes("manager-rel-pic") as pic:
                    candidates.thumb(src, kind.glyph, t("manager.discovery.no_picture"),
                                     kind.family)
                if src:
                    _enlarges(pic, partial(mediaview.open_viewer, src, pick.kind, kind.label))
                ui.label(_caption(scene, pick)).classes("truncate grow min-w-0")
                discovery_get.end(
                    tray, pick, partial(_get_picture, context, scene, kind, pick),
                    discovery_get.try_again(context, tray, scene.game_id, pick))
            if discovery_pictures.newer(kind, offer):
                discovery_pictures.set_aside(context, scene, kind)


def _get_picture(context: dict[str, Any], scene: Scene, kind: Kind, pick: Pick) -> None:
    said = t("manager.discovery.download_pic", kind=kind.label,
             source=media_ownership.source_name(pick.source))
    panel.icon_action(
        said, partial(discovery_get.get, context, scene.tray, scene.game_id, [pick]),
        icon=verbs.FROM_ONLINE, hint=said)()
