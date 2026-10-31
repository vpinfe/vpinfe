
"""The last look before an import writes anything: what will land, and what will not.

Every row says three things, because those are the three a person is actually deciding
between: what the file is, what it is called, and what it does to whatever is there
already. The third is the one that matters and the one a file listing never tells you.

Nothing here is inferred from a filename. Where the drop landed named the game, and for
a media slot the kind as well, so this dialog confirms a decision rather than asking for
one that was already made.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nicegui import run, ui

from common import i18n
from common.failures import why
from common.i18n import t
from common.media.media_specs import IMAGE_FAMILY, media_label_map
from manager.kit import art, candidates, game_tables, offload, panel, verbs
from manager.kit import dialog as frame
from manager.kit.on_page import on_page
from manager.library import import_swap

logger = logging.getLogger("vpinfe.manager.library.import_dialog")

STOP_ASKING = {"updates": {"ask_where_new_games_go": False}}


@dataclass
class ReleaseMatch:
    """The VPS file an import is offered as, read by whoever matches the file when it is done.

    `kind` is the kind of file in a heading and `vps` the same as a sentence names it ("VPS
    ROM"). `choose` opens the picker and answers the id and the words for it, `("", "")` to
    clear the match, or None where the person left. `picture_of` answers the address of a
    VPS file's picture, and `reason` is why one was matched without being asked.
    """

    choose: Callable[[str], Awaitable[tuple[str, str] | None]]
    kind: str
    vps: str
    vps_file_id: str = ""
    label: str = ""
    picture_of: Callable[[str], str] | None = None
    reason: str = ""


def not_imported(one: dict[str, Any]) -> str:
    return t("manager.import_dialog.not_imported_row",
             kind=t(f"asset.kind.{one.get('kind') or ''}.label"),
             reason=one.get("reason") or "")


def _where(plan: dict[str, Any], item: dict[str, Any]) -> str:
    """Where this lands, relative to the game folder - the folder itself is named by the
    title, and repeating it on every row buries the part that differs."""
    base = Path(str(plan.get("game_dir") or ""))
    found = Path(str(item.get("destination") or ""))
    try:
        rel = found.relative_to(base)
    except ValueError:
        return str(found)
    if item.get("action") == "extract_tree":
        return f"{rel.as_posix()}/"
    if item.get("action") == "replace_media":
        return rel.as_posix()
    if rel.name != str(item.get("name") or ""):
        return rel.as_posix()
    if str(rel.parent) == ".":
        return t("manager.import_dialog.game_folder")
    return f"{rel.parent.as_posix()}/"


@on_page
async def ask_where(library: Any) -> str | None:
    """Which location a new game goes to. "" means the one already marked for it, and
    None means do not import at all.

    Three outcomes, and only one of them is a question. Where the marked location works
    and there is nothing else it could be, nothing is asked - a question with one answer
    is a click charged for nothing. Where it does not work, this refuses and says why,
    offering the places that would work rather than only reporting a failure. And where
    it works but there is a choice, it asks only while somebody wants to be asked.
    """
    try:
        found = await offload.io(library.new_game_destination)
    except Exception as exc:  # noqa: BLE001
        ui.notify(t("manager.import_dialog.place_failed"), caption=why(exc),
                  type="negative")
        return None

    others = list(found.get("alternatives") or [])
    if found.get("reason"):
        return await _no_destination(library, found, others)
    if not found.get("ask"):
        return ""
    return await _which_destination(library, found, others)


async def _no_destination(library: Any, found: dict[str, Any],
                          others: list[dict[str, Any]]) -> str | None:
    """It cannot go where it was told to. Refuse, say why, and offer the rest."""
    if not others:
        ui.notify(str(found.get("reason") or t("manager.import_dialog.no_place")),
                  type="negative")
        return None
    return await _pick(library, str(found.get("reason") or ""), others,
                       offer_remember=False)


async def _which_destination(library: Any, found: dict[str, Any],
                             others: list[dict[str, Any]]) -> str | None:
    """It could go to more than one place, and somebody wants to be asked."""
    marked = {"location_id": found.get("location_id"), "name": found.get("name"),
              "path": found.get("path")}
    return await _pick(library, "", [marked, *others], offer_remember=True)


@on_page
async def _pick(library: Any, reason: str, offered: list[dict[str, Any]],
                *, offer_remember: bool) -> str | None:
    holds = {"id": str(offered[0].get("location_id") or ""), "stop_asking": False}
    with frame.opened(t("manager.import_dialog.place_title")) as picker:
        if reason:
            # The refusal leads, because it is the reason they are being asked at all.
            ui.label(reason).classes("manager-help px-3")
        panel.facts(ui, [(t("word.library_folder"), panel.select(
            {str(one.get("location_id") or ""): str(one.get("name") or one.get("path") or "")
             for one in offered},
            str(holds["id"]), lambda e: holds.__setitem__("id", str(e.value or ""))))])
        if offer_remember:
            # Offered here rather than only in Settings, because this is the moment
            # somebody knows whether they want to be asked again.
            with ui.element("div").classes("px-3"):
                ui.checkbox(t("manager.import_dialog.remember"),
                            on_change=lambda e: holds.__setitem__("stop_asking",
                                                                  bool(e.value))) \
                    .props("dense").classes("manager-help")
        with frame.footer():
            frame.cancel(lambda: picker.submit(None))
            frame.answer(t("manager.import_dialog.use"),
                         lambda: picker.submit(holds["id"]), icon=verbs.ACCEPT)

    said = await picker
    if said is None:
        return None
    if holds["stop_asking"]:
        try:
            await run.io_bound(library.put_config, STOP_ASKING)
            await run.io_bound(library.set_location_write_to, said)
        except Exception as exc:  # noqa: BLE001
            # The import still goes where they said. Only the remembering failed.
            ui.notify(t("manager.import_dialog.remember_failed"), caption=why(exc),
                      type="warning")
    return said


def dropped_title(source: str, file_count: int) -> str:
    if source:
        return t("manager.import_dialog.import_2", name=source)
    if file_count > 1:
        return t("manager.import_dialog.import_files", count=file_count)
    return t("manager.import_dialog.import_drop")


@on_page
async def open_for(library: Any, upload_id: str, plan: dict[str, Any], *,
                   source: str = "", file_count: int = 0, game_dir: str = "",
                   rom_name: str = "",
                   allow_new_game: bool = False, media_kind: str = "",
                   location_id: str = "", asset_kind: str = "", add_table: bool = False,
                   replace_table: str = "", instead: bool = False,
                   declared: dict | None = None, release: ReleaseMatch | None = None,
                   on_done: Callable[[dict], Any] | None = None) -> None:
    """Show the plan and, if it is confirmed, run it.

    The dialog owns the upload from here: whichever way it closes, the staged files are
    either imported or thrown away. A session left behind is invisible rubbish in a temp
    directory that nothing else will ever clean up.

    A game that already has a table is asked whether the drop replaces one of them or
    stays beside them. `add_table` says which the dialog starts on, `replace_table` which
    table, and `instead` that the drop is another release, not a newer file.
    """
    live: dict[str, Any] = {"plan": plan}
    blocked = list(plan.get("blocked") or [])
    new_folder = str(plan.get("new_game_dir_name") or "")
    chosen: dict[int, bool] = {}
    # A single item with nothing blocked is not a choice, so it gets no checkbox - the
    # button already says what will happen.
    named: dict[str, Any] = {"folder": new_folder, "vps_id": ""}
    asked = {"game_dir": game_dir, "rom_name": rom_name, "allow_new_game": allow_new_game,
             "media_kind": media_kind, "location_id": location_id,
             "asset_kind": asset_kind, "add_table": add_table, "replace_table": replace_table,
             "instead": instead}
    swapping = import_swap.Swapping.of(plan.get("choice"), instead=instead)
    game_name = Path(str(plan.get("game_dir") or "")).name
    base_title = (dropped_title(source, file_count) if new_folder
                  else t("manager.import_dialog.import_3", name=game_name))

    def table_on() -> bool:
        return import_swap.table_on(live["plan"], chosen)

    def asking() -> dict[str, Any]:
        return {**asked, **(swapping.asked(table_on()) if swapping else {})}

    def items_of(shown: dict[str, Any]) -> list[dict[str, Any]]:
        return list(shown.get("items") or [])

    def single() -> bool:
        return len(items_of(live["plan"])) == 1 and not blocked

    def kept_choices(shown: dict[str, Any]) -> None:
        """The ticks a person made carried to the items of a fresh plan, matched by what
        the item is rather than its place in the list."""
        def key(one: dict[str, Any]) -> tuple[Any, ...]:
            return one["kind"], one.get("name"), one.get("action")

        was = {key(one): chosen.get(int(one["index"]), True) for one in items_of(live["plan"])}
        chosen.clear()
        chosen.update({int(one["index"]): was.get(key(one),
                                                  bool(one.get("default_enabled", True)))
                       for one in items_of(shown)})

    chosen.update({int(one["index"]): bool(one.get("default_enabled", True))
                   for one in items_of(plan)})
    if swapping is not None:
        swapping.settle(plan)

    with frame.opened("", wide=True, ignore_outside_click=True,
                      classes="manager-import-card") as dialog:
        heading = ui.label("").classes("manager-dialog-title")
        if new_folder:
            ui.label(t("manager.import_dialog.folder_note")) \
                .classes("manager-help px-3")
            _folder_row(library, named, plan)
        elif not single() and source:
            ui.label(t("manager.import_dialog.from", source=(source))) \
                .classes("manager-help px-3")
        choice_box = ui.column().classes("gap-2 w-full px-3") if swapping else None
        rows = ui.column().classes("gap-0 w-full manager-import-rows px-3")

        if blocked:
            with panel.disclosure(t("manager.import_dialog.not_imported", count=len(blocked))) \
                    .classes("manager-import-blocked px-3"):
                for one in blocked:
                    ui.label(not_imported(one)).classes("manager-help")

        count = ui.label("").classes("manager-help px-3")
        if release is not None:
            _release_row(release)

        def recount() -> None:
            wanted = sum(1 for value in chosen.values() if value)
            count.text = ("" if single() else
                          t("manager.import_dialog.imported", wanted=(wanted),
                                  count=len(items_of(live["plan"]))))

        def draw(shown: dict[str, Any]) -> None:
            rows.clear()
            with rows:
                for one in items_of(shown):
                    _draw_row(one, shown, chosen, single(),
                              replan if one.get("swap") else recount,
                              upload_id if _pictured(one, source) else "")
            on = import_swap.table_on(shown, chosen)
            heading.text = (import_swap.title(shown, swapping, release.label if release else "",
                                              on) or base_title)
            if swapping is not None and choice_box is not None:
                import_swap.draw(choice_box, shown, swapping, game=game_name,
                                 release_label=release.label if release else "",
                                 changed=replan, on=on)
                go.text = import_swap.confirm_label(swapping, on)

        @on_page
        async def replan() -> None:
            go.disable()
            try:
                fresh = await offload.io(library.upload_plan, upload_id, **asking(),
                                         vps_id=str(named["vps_id"] or ""))
            except Exception as exc:  # noqa: BLE001
                logger.warning("manager: no plan for %r", asking(), exc_info=True)
                ui.notify(t("manager.uploads.no_destination"), caption=why(exc),
                          type="negative")
                return
            kept_choices(fresh)
            live["plan"] = fresh
            if swapping is not None:
                swapping.settle(fresh, import_swap.table_on(fresh, chosen))
            draw(fresh)
            recount()
            if items_of(fresh):
                go.enable()

        with frame.footer():
            frame.cancel(lambda: dialog.submit(False))
            go = frame.answer(import_swap.confirm_label(swapping),
                              lambda: dialog.submit(True), icon=verbs.IMPORT)
            if not items_of(plan):
                go.disable()

        draw(plan)
        recount()
        follow = _following(library, upload_id, asked, named, draw) if new_folder else None

    said = await dialog
    if follow is not None:
        follow.cancel(with_current_invocation=True)
    if not said:
        await run.io_bound(library.abort_upload, upload_id)
        return

    wanted = None if single() else [i for i, on in sorted(chosen.items()) if on]
    if wanted is not None and not wanted:
        await run.io_bound(library.abort_upload, upload_id)
        ui.notify(t("manager.import_dialog.none_selected"), type="warning")
        return

    note = ui.notification(t("manager.import_dialog.importing"), spinner=True, timeout=None)
    try:
        report = await offload.io(
            library.upload_import, upload_id, **asking(),
            vps_id=str(named["vps_id"] or ""),
            new_game_dir_name=(str(named["folder"]) if new_folder else None),
            selected=wanted, declared=declared)
    except Exception as exc:  # noqa: BLE001
        note.dismiss()
        ui.notify(t("manager.import_dialog.import_failed"), caption=why(exc),
                  type="negative")
        await run.io_bound(library.abort_upload, upload_id)
        kept = list((getattr(exc, "details", None) or {}).get("replaced_tables") or ())
        if kept:
            await _swap_kept(library, kept, swapping, live["plan"], release, on_done)
        return
    note.dismiss()
    swapped = list(report.get("replaced_tables") or ())
    if not report.get("added_tables") and not swapped:
        brought = len(report.get("imported") or ())
        ui.notify(t("manager.import_dialog.imported_item", count=brought), type="positive")
    for one in swapped:
        import_swap.said(library, one,
                         old=swapping.label_of(swapping.table) if swapping else "",
                         new=import_swap.new_name(live["plan"], release.label if release else ""),
                         after=(lambda: on_done(report)) if on_done is not None else None)
    for one in report.get("renamed") or ():
        ui.notify(t("manager.import_dialog.renamed", was=one["was"], now=one["now"]),
                  type="info")
    if report.get("vps_error"):
        # The import worked and the match did not. Two facts, and rolling the second
        # into a failure would say the files did not land when they did.
        ui.notify(t("manager.import_dialog.match_failed"),
                  caption=str(report["vps_error"]), type="warning")
    if on_done is not None:
        answer = on_done(report)
        if hasattr(answer, "__await__"):
            await answer


@on_page
async def _swap_kept(library: Any, kept: list[dict[str, Any]],
                     swapping: import_swap.Swapping | None, plan: dict[str, Any],
                     release: ReleaseMatch | None, on_done: Callable[..., Any] | None) -> None:
    """A later item failed after the table swap ran. The swap stands, so the page is read
    again and the notice still offers to put the old table back."""
    report = {"imported": [], "replaced_tables": kept, "failed": True}
    if on_done is not None:
        answer = on_done(report)
        if hasattr(answer, "__await__"):
            await answer
    for one in kept:
        import_swap.said(library, one,
                         old=swapping.label_of(swapping.table) if swapping else "",
                         new=import_swap.new_name(plan, release.label if release else ""),
                         after=(lambda: on_done(report)) if on_done is not None else None,
                         partly=True)


# Past this many rows a bundle is a list to read, not pictures to look at.
PICTURES_UP_TO = 12


def _pictured(item: dict[str, Any], source: str) -> bool:
    """Whether the row leads with the picture of its file: a loose image, which is the only
    thing the upload serves. A file inside an archive is not extracted until it is imported,
    so where the drop was an archive there is no picture to ask for."""
    name = str(item.get("name") or "")
    return (int(item["index"]) < PICTURES_UP_TO and Path(name).suffix.lower() in IMAGE_FAMILY
            and (not source or source == name))


def _slot(item: dict[str, Any]) -> str:
    """What the file is for: the slot a media file fills, by the name it has everywhere
    else, since "Media" is the same word for every picture and video."""
    kind = str(item.get("media_kind") or "")
    return media_label_map().get(kind) or str(item.get("label") or item.get("kind") or "")


def _draw_row(item: dict[str, Any], plan: dict[str, Any], chosen: dict[int, bool],
              single: bool, changed: Callable[[], Any], upload_id: str = "") -> None:
    """One thing that will land: its picture, what it is called, what it is for and what it
    does to what is there. Where it goes is the tooltip, since the slot says what the path
    would."""
    index = int(item["index"])
    name = str(item.get("name") or "")
    with ui.row().classes("items-center gap-3 w-full no-wrap manager-import-row"):
        if not single:
            box = ui.checkbox(value=chosen[index]).props("dense")

            def picked() -> Any:
                chosen[index] = bool(box.value)
                return changed()

            box.on_value_change(picked)
        if upload_id or item.get("media_kind"):
            candidates.thumb(art.upload_picture(upload_id, name, size=art.CELL)
                             if upload_id else "", "")
        with ui.column().classes("gap-0 grow min-w-0"):
            ui.label(name).classes("manager-member-name truncate")
            line = [_slot(item)]
            if not item.get("media_kind"):
                line.append(_where(plan, item))
            if item.get("replaces"):
                line.append(str(item["replaces"]))
            ui.label(game_tables.JOIN.join(line)).classes("manager-member-table truncate") \
                .tooltip(f"→ {_where(plan, item)}")
            made = game_tables.made_from_it_line(list(item.get("made_from_it") or []))
            if made:
                with ui.row().classes("items-center gap-1 no-wrap min-w-0"):
                    ui.icon("error_outline").classes("manager-attention-icon")
                    ui.label(made).classes("manager-member-table truncate")
        ui.label(i18n.size(int(item.get("size") or 0))) \
            .classes("manager-member-qualifier")


def _folder_row(library: Any, named: dict[str, Any], plan: dict[str, Any]) -> None:
    """The folder a new game is created as, and the upstream record it is named from.

    Searched rather than typed where there is a match: the folder name is a convention
    other tools read, and getting it from the record is how it stays one.
    """
    def draw() -> None:
        with ui.row().classes("items-center gap-2 w-full no-wrap manager-field-row"):
            field = frame.field(str(named["folder"] or ""))
            field.on_value_change(lambda: named.__setitem__("folder", field.value or ""))
            panel.action(t("manager.import_dialog.match"),
                         lambda: _match(library, named, field), icon=verbs.MATCH,
                         inline=True,
                         hint=t("manager.import_dialog.match_hint"))()

    panel.facts(ui, [(t("word.folder"), draw)])
    del plan


def _release_row(release: ReleaseMatch) -> None:
    """The VPS file this file is, under its own heading with the match on a row below it, and
    why it was matched where nobody chose it."""
    @ui.refreshable
    def draw() -> None:
        with ui.row().classes("items-center gap-3 w-full no-wrap manager-source-row "
                              "manager-import-match"):
            if not release.vps_file_id:
                ui.label(t("manager.import_dialog.not_matched", vps=release.vps)) \
                    .classes("manager-help grow")
                panel.action(t("manager.import_dialog.match"), choose, icon=verbs.MATCH,
                             inline=True,
                             hint=t("manager.import_dialog.vps_match_hint", vps=release.vps))()
                return
            candidates.thumb(release.picture_of(release.vps_file_id) if release.picture_of
                             else "", "")
            with ui.column().classes("gap-0 grow min-w-0"):
                ui.label(release.label).classes("manager-member-name truncate")
                if release.reason:
                    ui.label(release.reason).classes("manager-help")
            panel.action(t("word.change"), choose, icon=verbs.MATCH, inline=True,
                         hint=t("manager.import_dialog.vps_match_hint", vps=release.vps))()

    @on_page
    async def choose() -> None:
        picked = await release.choose(release.vps_file_id)
        if picked is None:
            return
        release.vps_file_id, release.label = picked
        release.reason = ""
        draw.refresh()

    panel.facts(ui, [(panel.HEADING, t("manager.import_dialog.vps_match", kind=release.kind)),
                     (panel.FULL, draw)])


def _following(library: Any, upload_id: str, asked: dict[str, Any], named: dict[str, Any],
               draw: Callable[[dict[str, Any]], None]) -> ui.timer:
    """The rows drawn again from the plan under the folder name as typed, asked once the
    typing pauses. A name the install will not take leaves them; the import says why."""
    seen = {"was": str(named["folder"])}

    async def look() -> None:
        folder = str(named["folder"])
        if folder == seen["was"]:
            return
        seen["was"] = folder
        try:
            fresh = await offload.io(library.upload_plan, upload_id, **asked,
                                     vps_id=str(named["vps_id"] or ""),
                                     new_game_dir_name=folder)
        except Exception:  # noqa: BLE001
            logger.debug("manager: no plan under %r", folder, exc_info=True)
            return
        if folder == str(named["folder"]):
            draw(fresh)

    return ui.timer(0.3, look)


@on_page
async def _match(library: Any, named: dict[str, Any], field: Any) -> None:
    """Offer what the catalog has for this name, and take the folder name from it."""
    term = str(field.value or "").split("(")[0].strip()
    try:
        found = await offload.io(library.vps_search, term, 8)
    except Exception as exc:  # noqa: BLE001
        ui.notify(t("manager.import_dialog.search_failed"), caption=why(exc),
                  type="negative")
        return
    if not found:
        ui.notify(t("manager.import_dialog.no_match"), type="warning")
        return
    offered = {str(one.get("vps_id") or ""):
               str(one.get("name") or "") + (f" ({made})" if (made := game_tables.made(one))
                                             else "")
               for one in found}
    holds = {"id": next(iter(offered), "")}
    with frame.opened(t("manager.import_dialog.one")) as picker:
        ui.label(t("manager.import_dialog.pick_note")) \
            .classes("manager-help px-3")
        panel.facts(ui, [(t("word.game"), panel.select(
            offered, holds["id"], lambda e: holds.__setitem__("id", str(e.value or ""))))])
        with frame.footer():
            frame.cancel(lambda: picker.submit(""))
            frame.answer(t("manager.import_dialog.use"),
                         lambda: picker.submit(holds["id"]), icon=verbs.ACCEPT)

    picked = await picker
    if not picked:
        return
    entry = next((one for one in found if str(one.get("vps_id")) == picked), None)
    if entry is None:
        return
    named["vps_id"] = picked
    folder = str(entry.get("folder_name") or "")
    if folder:
        named["folder"] = folder
        field.value = folder
