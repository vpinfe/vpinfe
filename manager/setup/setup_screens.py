"""Setup's Screens step: which screen is which, and the DMD panel if there is one.

The picture and the panel rows are the ones Settings > Hardware > Displays draws, and each
change is written as it is made. The answer is VPinFE's own; Visual Pinball's half of it is
`fill`, which waits for the Visual Pinball step, where the person has confirmed the program.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

from nicegui import background_tasks, run, ui

from common.failures import why
from common.host import display_service
from common.i18n import t
from manager.kit import busy, offload, panel, verbs
from manager.kit import dialog as frame
from manager.kit.api import ApiClient
from manager.kit.data import Library
from manager.kit.on_page import on_page
from manager.settings import displays, dmd_panel, placement_panel, screens, vpx_displays

logger = logging.getLogger("vpinfe.manager.setup.setup_screens")


@dataclass
class Screens:
    """What the Screens step has been told, for the Visual Pinball half of it."""
    answered: bool = False
    # The DMD panel's kind. VPinFE stores it only for PIN2DMD, so the others are kept here.
    kind: str = ""
    # The question about a launcher that holds its own screens is put once.
    asked: bool = False


@dataclass(frozen=True)
class Seen:
    """What the step draws from: VPinFE's settings and this device's screens."""
    values: dict[str, Any]
    schema: list[dict]
    monitors: list[Any]
    names: list[str]
    reading: placement_panel.Reading | None


async def _gather(library: Library) -> Seen:
    return Seen(
        values=await offload.io(library.config_values),
        schema=await offload.io(library.config_schema),
        monitors=await offload.io(screens.connected),
        names=list(await offload.io(display_service.get_display_names)),
        reading=await run.io_bound(placement_panel.read, os.environ))


@on_page
async def write(changes: dict[str, dict[str, Any]]) -> bool:
    try:
        await run.io_bound(ApiClient().put_config, changes)
    except Exception as exc:  # noqa: BLE001 - the reason belongs on the page
        ui.notify(t("manager.settings.save_failed"), caption=why(exc), type="negative")
        return False
    return True


def _entries(state: Screens, library: Library, seen: Seen,
             rebuild: Any) -> list[tuple[Any, Any]]:
    """The picture without its heading, which the step's title already says, then the
    DMD panel where the cabinet has one, then where the windows go."""
    @on_page
    async def follow(before: Any, after: Any, here: Any) -> None:
        await vpx_displays.follow(library, before, after, here)
        try:
            await offload.io(placement_panel.resync)
        except OSError as exc:
            ui.notify(t("manager.placement.update_failed"), caption=why(exc), type="warning")

    async def kept(kind: str) -> None:
        state.kind = kind
        await vpx_displays.follow_dmd(library, kind)

    picture = [one for one in displays.rows(seen.values, seen.schema, seen.monitors, seen.names,
                                            write, rebuild, follow=follow)
               if one[0] is not panel.HEADING]
    cabinet = displays.cabinet(seen.values, seen.schema)
    return [*picture,
            *(dmd_panel.rows(seen.values, seen.schema, write, rebuild, kept, kind=state.kind)
              if cabinet else []),
            *placement_panel.rows(seen.reading, seen.values, seen.schema, write, rebuild)]


async def ask(state: Screens) -> dict[str, Any]:
    library = Library(ApiClient())
    try:
        first = await _gather(library)
    except Exception:
        logger.warning("Could not read the screens", exc_info=True)
        return {"title": t("manager.setup.screens.title"),
                "notes": [t("said.settings_unreadable")]}

    def draw(_: dict[str, Any]) -> None:
        with ui.element("div").classes("w-full") as page:
            shown = ui.element("div").classes("w-full")

        def render(seen: Seen) -> None:
            shown.clear()
            with shown:
                panel.facts(ui, _entries(state, library, seen, again))

        async def redraw() -> None:
            render(await _gather(library))

        def again() -> None:
            with page:
                busy.fill(shown, redraw)

        render(first)

    return {"title": t("manager.setup.screens.title"), "draw": draw}


async def answer(state: Screens) -> None:
    """The step is answered. Nothing of Visual Pinball's is touched or shown until the
    Visual Pinball step has confirmed the program: `fill` writes it from there."""
    state.answered = True


@on_page
async def fill(state: Screens, launcher_id: str) -> None:
    """Visual Pinball's half of the answer, from VPinFE's settings as they stand, then the
    question for a part the file already puts elsewhere."""
    if not state.answered:
        return
    library = Library(ApiClient())
    try:
        seen = await _gather(library)
        here = screens.numbered(seen.monitors, seen.names)
        kind = (dmd_panel.kind_of(seen.values, seen.schema) or state.kind) \
            if dmd_panel.is_on(seen.values, seen.schema) else ""
        standing = displays.standing(seen.values, seen.schema)
        await vpx_displays.fill(ApiClient(), launcher_id, standing, here, kind)
    except Exception as exc:  # noqa: BLE001 - say so, the step itself is answered
        logger.warning("Could not write the screens for Visual Pinball", exc_info=True)
        ui.notify(t("said.save_failed"), caption=why(exc), type="negative")
        return
    try:
        parts = await vpx_displays.disagreeing(ApiClient(), launcher_id, standing, here)
    except Exception:  # noqa: BLE001 - written, so only the question is lost
        logger.warning("Could not compare Visual Pinball's screens with VPinFE's", exc_info=True)
        return
    if parts and not state.asked:
        state.asked = True
        background_tasks.create(_ask_which(launcher_id, standing, here, parts),
                                name="setup-screens-ask-which")


@on_page
async def _ask_which(launcher_id: str, standing: displays.Standing, here: Any,
                     parts: list[str]) -> None:
    """Visual Pinball's file already puts these screens somewhere else. Setup is not held
    up: Escape, or Leave as Is, leaves the file as it is."""
    with frame.opened(t("manager.setup.screens.apart"), size="question") as box:
        ui.label(t("manager.setup.screens.apart_detail", parts=vpx_displays.named(parts))) \
            .classes("manager-help")
        with frame.footer():
            frame.quiet(t("manager.setup.screens.keep_vp"), lambda: box.submit(False),
                        icon=verbs.CANCEL)
            frame.answer(t("manager.setup.screens.use_vpinfe"), lambda: box.submit(True),
                         icon=verbs.REVERT)
        box.escape_answer = False
    if not await box:
        return
    try:
        written = await vpx_displays.match_launcher(ApiClient(), launcher_id, standing, here)
    except Exception as exc:  # noqa: BLE001 - the reason belongs on the page
        ui.notify(t("said.save_failed"), caption=why(exc), type="negative")
        return
    vpx_displays.say_unnamed(written)
