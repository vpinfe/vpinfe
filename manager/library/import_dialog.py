
"""The last look before an import writes anything: what will land, and what will not.

Every row says three things, because those are the three a person is actually deciding
between: what the file is, what it is called, and what it does to whatever is there
already. The third is the one that matters and the one a file listing never tells you.

Nothing here is inferred from a filename. Where the drop landed named the game, and for
a media slot the kind as well, so this dialog confirms a decision rather than asking for
one that was already made.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nicegui import run, ui

from common import i18n
from common.failures import why
from common.i18n import t
from common.media.media_specs import CONVERTIBLE_IMAGE, IMAGE_FAMILY, media_label_map
from manager.kit import art, candidates, game_tables, offload, panel, verbs
from manager.kit import dialog as frame
from manager.kit.on_page import on_page
from manager.library import convert_row, drop_sheet, import_swap, info_row

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


def initial_ticks(plan: dict[str, Any]) -> dict[int, bool]:
    return {int(one["index"]): bool(one.get("default_enabled", True))
            for one in plan.get("items") or ()}


def kept_ticks(ticks: dict[int, bool], was: dict[str, Any],
               fresh: dict[str, Any]) -> dict[int, bool]:
    """The ticks a person made carried to the items of a fresh plan, matched by what the item
    is rather than its place in the list."""
    def key(one: dict[str, Any]) -> tuple[Any, ...]:
        return one["kind"], one.get("name"), one.get("action"), one.get("member")

    before = {key(one): ticks.get(int(one["index"]), True) for one in was.get("items") or ()}
    return {int(one["index"]): before.get(key(one), bool(one.get("default_enabled", True)))
            for one in fresh.get("items") or ()}


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


@on_page
async def open_for(library: Any, upload_id: str, plan: dict[str, Any], *,
                   source: str = "", file_count: int = 0, game_dir: str = "",
                   rom_name: str = "",
                   allow_new_game: bool = False, media_kind: str = "",
                   location_id: str = "", asset_kind: str = "", add_table: bool = False,
                   replace_table: str = "", instead: bool = False,
                   table_id: str = "", scope: str = "", page: int = 1,
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
    sheet = drop_sheet.Sheet(plan)
    new_folder = str(plan.get("new_game_dir_name") or "")
    chosen = initial_ticks(plan)
    named: dict[str, Any] = {"folder": new_folder, "vps_id": ""}
    asked = {"game_dir": game_dir, "rom_name": rom_name, "allow_new_game": allow_new_game,
             "media_kind": media_kind, "location_id": location_id,
             "asset_kind": asset_kind, "add_table": add_table, "replace_table": replace_table,
             "instead": instead, "table_id": table_id, "scope": scope, "page": page}
    swapping = import_swap.Swapping.of(plan.get("choice"), instead=instead)
    game_name = Path(str(plan.get("game_dir") or "")).name
    base_title = drop_sheet.title(game=game_name, asked=asked, new_game=bool(new_folder),
                                  source=source)
    waiting = {"on": sheet.seeded}
    progress = convert_row.Progress()
    stopping = {"asked": False}

    def table_on() -> bool:
        return import_swap.table_on(live["plan"], chosen)

    def asking() -> dict[str, Any]:
        return {**asked, **(swapping.asked(table_on()) if swapping else {}), **sheet.asked()}

    def items_of(shown: dict[str, Any]) -> list[dict[str, Any]]:
        return list(shown.get("items") or [])

    if swapping is not None:
        swapping.settle(plan)

    with frame.opened("", wide=True, ignore_outside_click=True,
                      classes="manager-import-card") as dialog:
        heading = ui.label("").classes("manager-dialog-title")
        if new_folder:
            ui.label(t("manager.import_dialog.folder_note")) \
                .classes("manager-help px-3")
            _folder_row(library, named, plan)
        elif not sheet.single() and source:
            ui.label(t("manager.drop_sheet.from_pack", source=source, count=file_count)
                     if file_count > 1 else t("manager.import_dialog.from", source=source)) \
                .classes("manager-help px-3")
        choice_box = ui.column().classes("gap-2 w-full px-3") if swapping else None
        table_box = ui.column().classes("gap-2 w-full px-3")
        rows = ui.column().classes("gap-0 w-full manager-import-rows px-3")
        freeze, thaw = drop_sheet.hold_scroll(rows, ui.run_javascript)
        notice_box = ui.column().classes("gap-0 w-full px-3")
        if release is not None:
            _release_row(release)

        def recount() -> None:
            """The button says what it will do, or what is still to choose."""
            wanted = sum(1 for value in chosen.values() if value)
            base = import_swap.confirm_label(swapping, import_swap.table_on(live["plan"], chosen))
            go.text = (t("manager.import_dialog.importing") if progress.running
                       else drop_sheet.go_label(sheet, base, wanted))
            if (items_of(live["plan"]) and not sheet.reason() and not waiting["on"]
                    and not progress.running):
                go.enable()
            else:
                go.disable()

        def draw(shown: dict[str, Any]) -> None:
            if shown is not sheet.plan:
                sheet.take(shown)
            rows.clear()
            with rows:
                _draw_sheet(sheet, shown, chosen, upload_id, replan, recount, progress,
                            repaint)
            table_box.clear()
            with table_box:
                drop_sheet.draw_table_line(sheet, asked, replan)
            notice_box.clear()
            if sheet.ffmpeg_missing():
                with notice_box:
                    convert_row.draw_notice(library, replan)
            footer_aside()
            on = import_swap.table_on(shown, chosen)
            heading.text = (import_swap.title(shown, swapping, release.label if release else "",
                                              on) or base_title)
            if swapping is not None and choice_box is not None:
                import_swap.draw(choice_box, shown, swapping, game=game_name,
                                 release_label=release.label if release else "",
                                 changed=replan, on=on)

        def repaint() -> None:
            """The rows drawn again from the plan already held, for what the server need not
            be asked about."""
            freeze()
            draw(live["plan"])
            recount()
            thaw()

        def footer_aside() -> None:
            aside.clear()
            with aside:
                if progress.running:
                    ui.label(t("manager.drop_sheet.not_added_until")).classes("manager-help")
                elif sheet.reencodes():
                    convert_row.draw_wait(sheet)

        @on_page
        async def replan(*, quiet: bool = False) -> None:
            go.disable()
            freeze()
            try:
                fresh = await offload.io(
                    library.upload_plan, upload_id, **asking(),
                    vps_id=str(named["vps_id"] or ""),
                    new_game_dir_name=(str(named["folder"]) if new_folder else None))
            except Exception as exc:  # noqa: BLE001
                logger.warning("manager: no plan for %r", asking(), exc_info=True)
                if not quiet:
                    ui.notify(t("manager.uploads.no_destination"), caption=why(exc),
                              type="negative")
                waiting["on"] = False
                recount()
                thaw()
                return
            ticks = kept_ticks(chosen, live["plan"], fresh)
            chosen.clear()
            chosen.update(ticks)
            live["plan"] = fresh
            sheet.take(fresh)
            if swapping is not None:
                swapping.settle(fresh, import_swap.table_on(fresh, chosen))
            seeded = sheet.seeded
            waiting["on"] = seeded
            draw(fresh)
            recount()
            if seeded:
                await replan()
            else:
                thaw()

        @on_page
        async def leave() -> None:
            """Cancel: before the import it closes the sheet, during it it stops the import,
            which ends with nothing added."""
            if progress.running:
                stopping["asked"] = True
                await offload.io(library.stop_upload_import)
                return
            dialog.submit(False)

        @on_page
        async def run_import() -> None:
            if sheet.single():
                wanted = [int(one["index"]) for one in sheet.items()]
            else:
                wanted = [i for i, on in sorted(chosen.items()) if on]
            if not wanted:
                dialog.submit({"none": True})
                return
            progress.running = True
            progress.parts = {}
            # Escape cannot leave a run: the answer it ends on is drawn from this dialog.
            dialog.props("persistent")
            repaint()
            try:
                job = await offload.io(
                    library.start_upload_import, upload_id, **asking(),
                    vps_id=str(named["vps_id"] or ""),
                    new_game_dir_name=(str(named["folder"]) if new_folder else None),
                    selected=wanted, declared=declared)
            except Exception as exc:  # noqa: BLE001
                reason = why(exc)
                dialog.submit({"failed": reason, "kept": []})
                return
            if stopping["asked"]:
                # Cancel was pressed before the server had a job to stop.
                await offload.io(library.stop_upload_import)
            dialog.submit(await _follow_job(library, str(job.get("id") or ""), progress,
                                            repaint))

        with frame.footer():
            aside = ui.row().classes("items-center gap-2 manager-dialog-aside")
            frame.cancel(leave)
            go = frame.answer(import_swap.confirm_label(swapping), run_import,
                              icon=verbs.IMPORT)

        draw(plan)
        recount()
        if waiting["on"]:
            ui.timer(0.05, replan, once=True)
        follow = _following(named, replan) if new_folder else None

    said = await dialog
    if follow is not None:
        follow.cancel(with_current_invocation=True)
    if not said:
        if progress.running:
            await offload.io(library.stop_upload_import)
        await run.io_bound(library.abort_upload, upload_id)
        return
    if said.get("none"):
        await run.io_bound(library.abort_upload, upload_id)
        ui.notify(t("manager.import_dialog.none_selected"), type="warning")
        return
    if said.get("stopped"):
        await run.io_bound(library.abort_upload, upload_id)
        ui.notify(t("manager.drop_sheet.stopped"), type="info")
        return
    if "failed" in said:
        ui.notify(t("manager.import_dialog.import_failed"), caption=str(said["failed"]),
                  type="negative")
        await run.io_bound(library.abort_upload, upload_id)
        kept = list(said.get("kept") or ())
        if kept:
            await _swap_kept(library, kept, swapping, live["plan"], release, on_done)
        return
    report = said["report"]
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

POLL_S = 0.4
# Reads of the job that may fail in a row before the import is reported lost. The job runs on
# whether or not it is watched, so one dropped read is not an answer.
LOST_AFTER = 5


async def _follow_job(library: Any, job_id: str, progress: convert_row.Progress,
                      repaint: Callable[[], Any]) -> dict[str, Any]:
    """An import job followed to its end, redrawing the rows as its files move. Answers one
    of `{"report": ...}`, `{"stopped": True}` and `{"failed": why, "kept": [swapped tables]}`."""
    missed = 0
    while True:
        await asyncio.sleep(POLL_S)
        try:
            found = await offload.io(library.job, job_id)
        except Exception as exc:  # noqa: BLE001
            missed += 1
            if missed < LOST_AFTER:
                continue
            reason = why(exc)
            return {"failed": reason, "kept": []}
        missed = 0
        parts = {str(one["member"]): one for one in found.get("parts") or ()}
        if parts != progress.parts:
            progress.parts = parts
            repaint()
        if found.get("state") == "running":
            continue
        result = dict(found.get("result") or {})
        if found.get("state") == "failed":
            return {"failed": str(found.get("error") or ""), "kept": []}
        if found.get("stopped") or result.get("stopped"):
            return {"stopped": True}
        if result.get("failed"):
            return {"failed": str(result.get("error") or ""),
                    "kept": list(result.get("replaced_tables") or ())}
        return {"report": result}


def _pictured(item: dict[str, Any]) -> bool:
    """Whether the row leads with the picture of its file: an image, loose or inside the
    archive, which the upload serves by the path the file has in the drop."""
    name = str(item.get("name") or "")
    return (int(item["index"]) < PICTURES_UP_TO
            and Path(name).suffix.lower() in IMAGE_FAMILY + CONVERTIBLE_IMAGE)


def _draw_sheet(sheet: drop_sheet.Sheet, shown: dict[str, Any], chosen: dict[int, bool],
                upload_id: str, replan: Callable[[], Any], recount: Callable[[], Any],
                progress: convert_row.Progress, repaint: Callable[[], Any]) -> None:
    """The rows: a lone file as it is, anything more under a heading for each kind of thing
    it holds, with the variants to choose between above the plain rows of their kind."""
    def changed(one: dict[str, Any]) -> Callable[[], Any]:
        return replan if one.get("swap") else recount

    def row(one: dict[str, Any], single: bool,
            goes_to: Callable[[], None] | None = None) -> None:
        _draw_row(sheet, one, shown, chosen, single, changed(one),
                  upload_id if _pictured(one) else "", goes_to,
                  progress=progress, replan=replan, redraw=repaint)

    if sheet.single():
        for one in shown.get("items") or ():
            row(one, True)
        return
    sections = sheet.sections()
    for section in sections:
        if len(sections) > 1:
            ui.label(drop_sheet.heading(section.key)).classes("manager-fact-heading")
        for group in section.groups:
            drop_sheet.draw_group(sheet, group, upload_id=upload_id, changed=replan)
        for one in section.items:
            row(one, False, drop_sheet.place_select(sheet, str(one["member"]), replan)
                if sheet.has_place(one) else None)
        for one in section.unclaimed:
            drop_sheet.draw_unclaimed(sheet, one, upload_id=upload_id, changed=replan)
        for one in section.refused:
            drop_sheet.draw_refused(one)


def _slot(item: dict[str, Any]) -> str:
    """What the file is for: the slot a media file fills, by the name it has everywhere
    else, since "Media" is the same word for every picture and video."""
    kind = str(item.get("media_kind") or "")
    return media_label_map().get(kind) or str(item.get("label") or item.get("kind") or "")


GLYPHS = {"sound": "graphic_eq", "video": "movie"}


def _sub_line(plan: dict[str, Any], item: dict[str, Any],
              view: dict[str, Any] | None) -> str:
    """What the file is for, where it lands, what it replaces, how big it is and what
    converting does to it. A fact appears once: a file kept in the game folder says so in
    place of the folder."""
    line = [_slot(item)]
    if item.get("basis") == "other":
        line.append(t("manager.drop_sheet.other_saved"))
    elif not item.get("media_kind"):
        line.append(_where(plan, item))
    if item.get("replaces"):
        line.append(str(item["replaces"]))
    line.append(i18n.size(int(item.get("size") or 0)))
    said = convert_row.note(view)
    if said:
        line.append(said)
    return game_tables.JOIN.join(line)


def _ticked_as(index: int, chosen: dict[int, bool],
               redraw: Callable[[], Any]) -> Callable[[bool], Any]:
    def set_ticked(value: bool) -> Any:
        chosen[index] = value
        return redraw()

    return set_ticked


def _draw_row(sheet: drop_sheet.Sheet, item: dict[str, Any], plan: dict[str, Any],
              chosen: dict[int, bool], single: bool, changed: Callable[[], Any],
              upload_id: str = "", goes_to: Callable[[], None] | None = None, *,
              progress: convert_row.Progress, replan: Callable[[], Any],
              redraw: Callable[[], Any]) -> None:
    """One thing that will land: its picture, what it is called, what it is for and what it
    does to what is there. Where it goes is the tooltip, since the slot says what the path
    would. `goes_to` draws the choice of place for a file somebody placed; a picture, sound
    or video draws the choice of what it is converted to."""
    index = int(item["index"])
    name = str(item.get("name") or "")
    member = str(item.get("member") or name)
    view = sheet.view(member)
    with ui.row().classes("items-center gap-3 w-full manager-import-row"
                          + (" manager-convert-row" if view else " no-wrap")):
        follows: list[Callable[[], None]] = []
        if not single:
            box = ui.checkbox(value=chosen[index]).props("dense")
            if progress.running:
                box.disable()

            def picked() -> Any:
                chosen[index] = bool(box.value)
                for sync in follows:
                    sync()
                return changed()

            box.on_value_change(picked)
        if upload_id or item.get("media_kind") or view:
            glyph = GLYPHS.get(str((view or {}).get("kind") or ""), "")
            candidates.thumb(art.upload_picture(upload_id, member, size=art.CELL)
                             if upload_id and not glyph else "", glyph)
        with ui.column().classes("gap-0 grow min-w-0 manager-convert-text"):
            ui.label(name).classes("manager-member-name truncate")
            ui.label(_sub_line(plan, item, view)).classes("manager-member-table truncate") \
                .tooltip(f"→ {_where(plan, item)}")
            made = game_tables.made_from_it_line(list(item.get("made_from_it") or []))
            if made:
                with ui.row().classes("items-center gap-1 no-wrap min-w-0"):
                    ui.icon("error_outline").classes("manager-attention-icon")
                    ui.label(made).classes("manager-member-table truncate")
        if goes_to is not None:
            goes_to()
        convert_row.draw(sheet, member, ticked=single or chosen.get(index, True),
                         progress=progress, replan=replan, redraw=redraw)
        if not progress.running:
            info_sync = info_row.draw(
                sheet, item, is_ticked=lambda: single or chosen.get(index, True),
                single=single, set_ticked=_ticked_as(index, chosen, redraw))
            if info_sync:
                follows.append(info_sync)


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


def _following(named: dict[str, Any], replan: Callable[..., Any]) -> ui.timer:
    """The sheet planned again under the folder name as typed, asked once the typing pauses.
    A name the install will not take leaves the rows without a notice; the import says why."""
    seen = {"was": str(named["folder"])}

    async def look() -> None:
        folder = str(named["folder"])
        if folder == seen["was"]:
            return
        seen["was"] = folder
        await replan(quiet=True)

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
