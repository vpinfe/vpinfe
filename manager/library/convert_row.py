"""What a picture, sound or video row of the drop sheet offers and says about converting.

The server describes each row (`plan.items[].convert`) and the sheet keeps what the person
chose; a choice goes back to the server, which answers with the row as it now reads.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from nicegui import ui

from common.i18n import t
from manager.kit import panel, verbs
from manager.kit.on_page import on_page
from manager.library import get_tool
from manager.library.drop_sheet import Sheet

AS_IS = "asis"
SETTING_LABELS = {"video": "manager.drop_sheet.setting_video",
                  "quality": "manager.drop_sheet.setting_quality",
                  "sound_in_video": "manager.drop_sheet.setting_sound"}


@dataclass
class Progress:
    """Where a running import is, by the name each converted file has in the drop."""

    running: bool = False
    parts: dict[str, dict[str, Any]] = field(default_factory=dict)


def _lists(options: list[dict[str, Any]]) -> tuple[dict[str, str], dict[str, str],
                                                    dict[str, str]]:
    """The labels, the sentence under each and the options that cannot be picked, from the
    options the server lists. The recommended one says so in its label, and the answer that
    converts nothing is set apart by a rule."""
    labels: dict[str, str] = {}
    says: dict[str, str] = {}
    unavailable: dict[str, str] = {}
    for one in options:
        value, name = str(one["value"]), str(one["label"])
        if value == AS_IS:
            name = t("manager.drop_sheet.dont_convert")
        elif one.get("preferred"):
            name = t("manager.drop_sheet.your_preference", name=name)
        elif one.get("recommended"):
            name = t("manager.drop_sheet.recommended", name=name)
        labels[value] = name
        says[value] = str(one.get("why") or "")
        if not one.get("available", True):
            unavailable[value] = says[value]
    return labels, says, unavailable


def _select(options: list[dict[str, Any]], value: str,
            on_change: Callable[[Any], Any]) -> panel.ExplainedSelect:
    labels, says, unavailable = _lists(options)
    with ui.element("div").classes("manager-fact-edit w-full"):
        return panel.ExplainedSelect(
            labels, value=value if value in labels else None, says=says,
            unavailable=unavailable, apart=AS_IS if AS_IS in labels else None,
            on_change=on_change)


def format_name(view: dict[str, Any]) -> str:
    """What the file is being made as, in the words the choice lists use."""
    found = next((str(one["label"]) for one in view.get("options") or ()
                  if str(one["value"]) == str(view.get("to"))), "")
    return found or str(view.get("to") or "")


def note(view: dict[str, Any] | None) -> str:
    """The words for a row's sub line: what converting does to the file."""
    if not view or view.get("to") == AS_IS:
        return ""
    return str(view.get("note") or "")


def draw(sheet: Sheet, member: str, *, ticked: bool, progress: Progress,
         replan: Callable[[], Any], redraw: Callable[[], Any]) -> None:
    """The right-hand column of a convertible row: the choice of format with its settings
    behind a summary, or where the file is in the import."""
    view = sheet.view(member)
    if view is None:
        return
    with ui.column().classes("gap-1 manager-convert-col"):
        if progress.running:
            _status(view, progress.parts.get(member), ticked)
            return
        _choice(sheet, member, view, replan)
        if view.get("problem") and not _says_it_once(sheet, view):
            with ui.row().classes("items-center gap-1 no-wrap min-w-0"):
                ui.icon("error_outline").classes("manager-attention-icon")
                ui.label(str(view["problem"])).classes("manager-member-table")
        if view.get("line") and view.get("to") != AS_IS:
            _summary(sheet, member, view, redraw)
        if member in sheet.open and view.get("to") != AS_IS:
            _settings(sheet, member, view, replan)


def _says_it_once(sheet: Sheet, view: dict[str, Any]) -> bool:
    """Whether the sheet's notice already says why this row cannot be converted."""
    return sheet.ffmpeg_missing() and view.get("kind") in ("sound", "video")


def _choice(sheet: Sheet, member: str, view: dict[str, Any],
            replan: Callable[[], Any]) -> None:
    def chose(e: Any) -> Any:
        if str(e.value or "") == str(view.get("to")):
            return None
        sheet.decide(member, "to", str(e.value or ""))
        return replan()

    ui.label(t("manager.drop_sheet.convert_to")).classes("manager-help")
    _select(list(view.get("options") or ()), str(view.get("to") or ""), chose)


def _summary(sheet: Sheet, member: str, view: dict[str, Any],
             redraw: Callable[[], Any]) -> None:
    def toggle() -> Any:
        sheet.open.symmetric_difference_update({member})
        return redraw()

    with ui.row().classes("items-center gap-1 no-wrap min-w-0 w-full"):
        ui.label(str(view["line"])).classes("manager-member-table truncate grow")
        if view.get("settings"):
            shown = member in sheet.open
            panel.icon_only("expand_less" if shown else "expand_more", toggle,
                            hint=t("manager.drop_sheet.settings"))


def _settings(sheet: Sheet, member: str, view: dict[str, Any],
              replan: Callable[[], Any]) -> None:
    for one in view.get("settings") or ():
        key = str(one["key"])

        def chose(e: Any, key: str = key, was: str = str(one["value"])) -> Any:
            if str(e.value or "") == was:
                return None
            sheet.decide(member, key, str(e.value or ""))
            return replan()

        with ui.column().classes("gap-0 w-full"):
            ui.label(t(SETTING_LABELS[key])).classes("manager-help")
            _select(list(one.get("options") or ()), str(one["value"]), chose)


def _status(view: dict[str, Any], part: dict[str, Any] | None, ticked: bool) -> None:
    """Where one file is while the import runs. A file that is not converted says nothing."""
    if view.get("to") == AS_IS or not ticked:
        return
    state = str((part or {}).get("state") or "waiting")
    name = format_name(view)
    if state == "done":
        with ui.row().classes("items-center gap-1 no-wrap"):
            ui.icon("check").classes("manager-tick")
            ui.label(t("manager.drop_sheet.converted_to", format=name)) \
                .classes("manager-member-table")
        return
    if state != "working":
        ui.label(t("manager.drop_sheet.waiting")).classes("manager-member-table")
        return
    ui.label(t("manager.drop_sheet.converting_to", format=name)) \
        .classes("manager-member-table")
    pct = int((part or {}).get("pct") or 0)
    with ui.row().classes("items-center gap-2 no-wrap w-full"):
        ui.linear_progress(value=pct / 100, show_value=False).props("rounded") \
            .classes("grow")
        if pct:
            ui.label(f"{pct}%").classes("manager-member-table")


def draw_wait(sheet: Sheet) -> None:
    """Whether converting pauses while a table plays, asked in the footer where a video is
    to be re-encoded."""
    def chose(e: Any) -> None:
        sheet.wait = e.value == "wait"

    ui.label(t("manager.drop_sheet.while_playing")).classes("manager-help")
    with ui.element("div").classes("manager-fact-edit"):
        panel.ExplainedSelect(
            {"wait": t("manager.drop_sheet.wait"), "keep": t("manager.drop_sheet.keep_going")},
            value="wait" if sheet.wait else "keep",
            says={"wait": t("media_convert.why.wait_yes"),
                  "keep": t("media_convert.why.wait_no")},
            on_change=chose)


def draw_notice(library: Any, replan: Callable[[], Any]) -> None:
    """The one place the sheet says sounds and videos need FFmpeg, with the way to get it."""
    @on_page
    async def get() -> None:
        if await get_tool.ask_and_get_ffmpeg(library):
            await replan()

    with ui.element("div").classes("manager-attention w-full"):
        ui.icon("error_outline").classes("manager-attention-icon")
        ui.label(t("manager.drop_sheet.need_ffmpeg")) \
            .classes("manager-attention-line min-w-0 grow")
        panel.action(t("manager.drop_sheet.get_ffmpeg"), get, icon=verbs.FETCH, inline=True,
                     hint=t("manager.drop_sheet.get_ffmpeg_help"))()
