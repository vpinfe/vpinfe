"""Settings > Hardware > Recording: what a settings page cannot say from the schema alone.

What this device can record comes from the device (`GET /capture`), read once per draw
and handed round as `offered[EDITOR_CAPTURE_COMMAND]`; a page drawing another device's
settings has none of it, and draws the settings alone.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from nicegui import run, ui

from common import tokens
from common.capture import adapters, commands, preflight, trial
from common.config import config_schema
from common.failures import why
from common.i18n import size, t
from common.media.media_specs import media_label_map
from manager.kit import confirm, offload, panel, verbs
from manager.kit.on_page import on_page

logger = logging.getLogger("vpinfe.manager.settings.recording")

SECTION = "capture"
REPORT = config_schema.EDITOR_CAPTURE_COMMAND
H264 = "h264"

# How many waiting recordings Settings names before it counts the rest.
SHOWN = 5
# The names whose meaning differs between the two commands.
PER_COMMAND = frozenset({"input", "fps"})


def report_of(offered: dict[str, Any] | None) -> dict[str, Any]:
    return dict((offered or {}).get(REPORT) or {})


def command_of(option: dict) -> str:
    key = str(option.get("key") or "")
    return next((one for one, setting in commands.SETTINGS.items() if setting == key), "")


def resolved(offered: dict[str, Any]) -> dict[str, str]:
    """What Automatic records on this device, as the line under Video Format."""
    codec = report_of(offered).get("video_codec")
    if not codec:
        return {}
    return {"video_codec": t("manager.recording.automatic_h264") if codec == H264
            else t("manager.recording.automatic_vp9")}


def command_editor(option: dict, value: Any, save: Callable[[Any], Any], *,
                   writable: bool = True, suggestions: dict[str, Any] | None = None,
                   **_: Any) -> Callable[[], None]:
    """A command line, refused at the field where it cannot run. It always holds what runs:
    VPinFE's own for this device until it is edited, and saved empty while it is that, so an
    untouched field keeps up with VPinFE."""
    command = command_of(option)
    own = str((report_of(suggestions).get("commands") or {}).get(command) or "")
    stored = str(value or "").strip()
    now: dict[str, Any] = {"kept": "" if stored == own else stored}
    state: dict[str, Any] = {}
    held: dict[str, str] = {}

    async def keep(text: str) -> str:
        text = text.strip() or own
        wrong = commands.problems(text, command)
        if wrong:
            return commands.words(wrong[0])
        kept = "" if text == own else text
        if kept != now["kept"]:
            if not await save(kept):
                return t("word.not_saved")
            now["kept"] = kept
        control = state.get("control")
        if control is not None and not control.is_deleted and (control.value or "") != text:
            control.value = text
        held["was"] = text
        status()
        return ""

    @on_page
    async def reset() -> None:
        await keep(own)

    def status() -> None:
        row = state.get("row")
        if row is None or row.is_deleted:
            return
        row.clear()
        if not now["kept"] or not writable:
            return
        with row:
            panel.action(t("manager.recording.command_reset"), reset, icon=verbs.RESET,
                         inline=True, hint=t("manager.recording.command_reset.help"))()

    field = panel.field(now["kept"] or own, keep, placeholder=own, disabled=not writable,
                        refuses=True, held=held, wrap=True,
                        on_control=lambda control: state.__setitem__("control", control))

    def draw() -> None:
        if not own:
            field()
            return
        with ui.column().classes("gap-1 min-w-0 w-full items-stretch"):
            field()
            state["row"] = ui.row().classes("items-center gap-2 no-wrap")
            status()

    return draw


def add_under(entries: list[Any], option: dict) -> None:
    """Under a command's field: the names it can use."""
    command = command_of(option)
    names = commands.OFFERED[command]

    def draw() -> None:
        with ui.column().classes("gap-1 min-w-0"):
            with panel.disclosure(t("manager.commands.names_title", count=len(names))) \
                    .classes("manager-tokens"):
                with ui.column().classes("gap-1 pt-1"):
                    ui.label(t("manager.commands.names_note")) \
                        .classes("manager-help")
                    with ui.element("div").classes("manager-token-list"):
                        for name in names:
                            ui.label(tokens.written(name)).classes("manager-token")
                            ui.label(says(name, command)).classes("manager-help")

    entries.append((panel.ASIDE, draw))


def says(name: str, command: str) -> str:
    """What a name stands for in this command."""
    return t(f"capture.token.{name}.{command}") if name in PER_COMMAND \
        else t(f"capture.token.{name}")


def finding(offered: dict[str, Any], library: Any = None,
            refill: Callable[[], None] | None = None) -> Callable[[], None] | None:
    """Why this device records nothing, where it does not; or that it could not say.
    With this install's library, Choose Screens beside a desktop that has not been told
    which screens VPinFE may record."""
    found = report_of(offered)
    if found.get("error"):
        failed = str(found["error"])

        def unread() -> None:
            panel.line(t("manager.recording.could_not_read"), hint=failed)

        return unread
    if not found or found.get("available") or not found.get("reason"):
        return None
    blocked = found["reason"]
    gettable = bool((blocked.get("remedy") or {}).get("get"))
    choosable = blocked.get("key") == adapters.NOT_CHOSEN and library is not None
    said = preflight.words({**blocked, "remedy": None} if choosable else blocked)

    def draw() -> None:
        from manager.settings.settings import TOOLS, address_for

        with ui.element("div").classes("manager-attention w-full mt-2"):
            ui.icon("error_outline").classes("manager-attention-icon")
            with ui.column().classes("gap-0 min-w-0 grow"):
                ui.label(said).classes("manager-attention-line")
                if gettable:
                    panel.link(t("manager.settings.page_tools"), to=address_for(TOOLS))()
            if choosable:
                panel.action(t("manager.recording.choose_screens"),
                             _chooser(library, refill or (lambda: None),
                                      str((blocked.get("params") or {}).get("desktop"))),
                             icon=verbs.CHOOSE,
                             hint=t("manager.recording.choose_screens.help"))()

    return draw


def _chooser(library: Any, refill: Callable[[], None],
             desktop: str) -> Callable[[], Any]:
    """Choose Screens: asked first, since the desktop asks on the device's own screen."""
    @on_page
    async def choose() -> None:
        if not await confirm.ask(t("manager.recording.choose_screens_ask"),
                                 detail=t("manager.recording.choose_screens_detail",
                                          desktop=desktop),
                                 confirm=t("manager.recording.choose_screens"),
                                 icon=verbs.CHOOSE, danger=False):
            return
        try:
            job = await offload.io(library.choose_capture_screens)
        except Exception as exc:  # noqa: BLE001 - the reason belongs on screen
            ui.notify(t("manager.recording.could_not_choose"), caption=why(exc),
                      type="negative")
            return
        from manager.library import record  # record imports this module

        ended = await record.ended_job(library, str(job.get("id") or ""))
        if not ended or ended.get("error"):
            ui.notify(t("manager.recording.could_not_choose"),
                      caption=str(ended.get("error") or ""), type="negative")
        elif ended.get("reason"):
            ui.notify(t("manager.recording.could_not_choose"),
                      caption=preflight.words(ended["reason"]), type="warning")
        else:
            every = ended.get("shared") == ended.get("screens")
            ui.notify(t("manager.recording.chose_screens", desktop=desktop,
                        shared=str(ended.get("shared")), screens=str(ended.get("screens"))),
                      type="positive" if every else "warning")
        refill()

    return choose


async def foot(library: Any, refill: Callable[[], None], *,
               offered: dict[str, Any] | None = None) -> list[tuple[Any, Any]]:
    """Test, under the commands; then the recordings waiting for a decision."""
    found = report_of(offered)
    rows: list[tuple[Any, Any]] = []
    if found:
        rows.append((panel.FULL, _tester(library, found)))
    try:
        waiting = await offload.io(library.capture_proposals)
    except Exception as exc:  # noqa: BLE001 - a settings page says why, never 500s
        logger.warning("Could not read the recordings waiting: %s", why(exc))
        return rows
    if int(waiting.get("count") or 0):
        rows.append(_waiting(library, refill, waiting))
    return rows


def _tester(library: Any, found: dict[str, Any]) -> Callable[[], None]:
    blocked = "" if found.get("available") else preflight.words(found.get("reason") or {})

    def draw() -> None:
        with ui.column().classes("gap-1 w-full min-w-0"):
            with ui.row().classes("items-center gap-2 no-wrap min-w-0"):
                panel.action(t("manager.recording.test"), lambda: tried(), icon=verbs.RUN,
                             enabled=not blocked, hint=blocked)()
                ui.label(t("manager.recording.test_what", seconds=f"{trial.SECONDS:g}")) \
                    .classes("manager-help")
            outcome = ui.column().classes("gap-1 min-w-0")

        @on_page
        async def tried() -> None:
            running = ui.notification(t("manager.recording.testing"), spinner=True,
                                      timeout=None)
            try:
                said = await offload.io(library.test_capture)
            except Exception as exc:  # noqa: BLE001 - said, and nothing was kept
                ui.notify(t("manager.recording.could_not_test"), caption=why(exc),
                          type="negative")
                return
            finally:
                running.dismiss()
            outcome.clear()
            with outcome:
                came_out(said)

    return draw


def came_out(said: dict[str, Any]) -> None:
    """What a test made: a picture and its size, rate and frames, or what failed."""
    if not said.get("ok"):
        with ui.element("div").classes("manager-attention w-full"):
            ui.icon("error_outline").classes("manager-attention-icon")
            panel.line(t(str((said.get("reason") or {}).get("key") or "")),
                       hint=str(said.get("detail") or ""), classes="manager-attention-line")
        return
    width, height = (list(said.get("size") or []) + [0, 0])[:2]
    with ui.row().classes("items-center gap-3 no-wrap min-w-0"):
        if said.get("picture"):
            with ui.element("div").classes("manager-source-thumb"):
                ui.image(str(said["picture"]))
        ui.label(t("manager.recording.test_came_out", width=str(width), height=str(height),
                   fps=f"{float(said.get('fps') or 0):g}",
                   frames=str(int(said.get("frames") or 0)))).classes("manager-help")


def _waiting(library: Any, refill: Callable[[], None],
             waiting: dict[str, Any]) -> tuple[Any, Any]:
    count = int(waiting.get("count") or 0)

    @on_page
    async def discard() -> None:
        if not await confirm.ask(t("manager.recording.discard_ask", count=count),
                                 detail=t("manager.recording.discard_detail"),
                                 confirm=t("manager.recording.discard_all"),
                                 icon=verbs.DISCARD):
            return
        try:
            await offload.io(library.discard_proposals)
        except Exception as exc:  # noqa: BLE001 - the reason belongs on screen
            ui.notify(t("manager.recording.could_not_discard"), caption=why(exc),
                      type="negative")
            return
        refill()

    games = {str(row.get("game_id") or "") for row in waiting.get("proposals") or []}

    async def used() -> None:
        await run.io_bound(library.reread_media, games)

    @on_page
    async def review() -> None:
        from manager.library import record  # record imports this module

        await record.review(library, used)
        refill()

    rows = list(waiting.get("proposals") or [])

    def draw() -> None:
        with ui.column().classes("gap-1 min-w-0"):
            with ui.element("div").classes("manager-fact-edit"):
                ui.label(t("manager.recording.waiting_are", count=count,
                           size=size(int(waiting.get("bytes") or 0)))) \
                    .classes("manager-fact-value truncate min-w-0")
                panel.action(t("manager.record.review"), review, icon=verbs.REVIEW,
                             inline=True, hint=t("manager.record.review.help"))()
                panel.action(t("manager.recording.discard_all"), discard, icon=verbs.DISCARD,
                             inline=True, hint=t("manager.recording.discard_all.help"))()
            kinds = media_label_map()
            for row in rows[:SHOWN]:
                kind = str(row.get("kind") or "")
                name = str(row.get("name") or "")
                ui.label(t("manager.recording.waiting_for", name=name, kind=kinds.get(kind, kind))
                         if name else kinds.get(kind, kind)).classes("manager-help truncate")
            if len(rows) > SHOWN:
                ui.label(t("manager.recording.waiting_more", count=len(rows) - SHOWN)) \
                    .classes("manager-help")

    return (t("manager.recording.waiting"), draw)
