"""The Edit row under a file's viewer: what can change the file, one icon button per tool.

A press changes the view only. The row then holds the unsaved change, with Cancel and Save
at its end, and Save writes the file once, so two presses are one write and a video is
encoded once.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any

from nicegui import ui

from common.games import media_rotate
from common.host import tools as host_tools
from common.i18n import t
from manager.kit import panel, verbs
from manager.kit.api import ApiClient
from manager.library import media_rotate as rotate_job

_ANIMATABLE = (".gif", ".png", ".apng", ".webp")

# Clockwise degrees held by the view, and the way the job knows each by.
_WAYS = {90: media_rotate.RIGHT, 180: media_rotate.AROUND, 270: media_rotate.LEFT}

# Turns the picture in its box, shrunk so a quarter turn still fits the room the box has. In
# a stage the picture sits at the box's top left, so a turned one is moved back there and
# may grow to the file's own size.
_PREVIEW = """
(() => {
  const el = document.getElementById(ELEMENT);
  const box = el && el.closest('.manager-slot-art');
  if (!el || !box) return;
  const angle = ANGLE;
  const stage = !!box.closest('.manager-stage');
  el.style.transformOrigin = 'center';
  if (angle % 180 === 0) {
    el.style.transform = angle ? 'rotate(' + angle + 'deg)' : '';
    return;
  }
  el.style.transform = '';
  const w = el.offsetWidth, h = el.offsetHeight;
  const room = Math.max(box.clientWidth, 1);
  const height = Math.max(box.clientHeight - (stage ? 0 : 12), 1);
  const natural = el.naturalWidth || el.videoWidth || 0;
  const cap = stage && natural ? Math.max(1, natural / Math.max(w, 1)) : 1;
  const k = Math.min(cap, room / h, height / w);
  const move = stage ? 'translate(' + (h * k - w) / 2 + 'px,' + (w * k - h) / 2 + 'px) ' : '';
  el.style.transform = move + 'rotate(' + angle + 'deg) scale(' + k + ')';
})()
"""


@dataclass(frozen=True)
class Tool:
    """One button in the Edit row. `reason` dims it and is what hovering says."""

    key: str
    icon: str
    label: str
    step: int
    reason: str = ""


def is_animated(path: str) -> bool:
    """Whether the picture at this local path has more than one frame. A path that is not
    here or not a picture answers no: the job says so itself when it skips the file."""
    if not path or Path(path).suffix.lower() not in _ANIMATABLE:
        return False
    try:
        from PIL import Image
        with Image.open(path) as opened:
            return bool(getattr(opened, "is_animated", False))
    except Exception:  # noqa: BLE001 - unreadable here is the job's to report
        return False


def tools_for(family: str, *, animated: bool = False) -> list[Tool]:
    """The tools a file of this family has. A family with none has no Edit row."""
    if family == "image":
        reason = t("error.media_rotate.animated") if animated else ""
    elif family == "video":
        found = host_tools.resolve(host_tools.FFMPEG).state == host_tools.State.FOUND
        reason = "" if found else t("error.media_rotate.no_ffmpeg")
    else:
        return []
    return [Tool("rotate_left", verbs.ROTATE_LEFT, t("manager.media_rotate.left"), -90, reason),
            Tool("rotate_right", verbs.ROTATE_RIGHT, t("manager.media_rotate.right"), 90,
                 reason)]


def edit_row(chosen: list[Tool], *, element_id: str, game_id: str, path: str,
             state: dict[str, Any], then: Callable[[], Any],
             own_file: bool = False) -> None:
    """The row of tools, which gains Cancel and Save once one has been pressed.

    `element_id` is the picture or video the tools turn. Save rotates the file at `path`,
    keeping the original in Backups, and runs `then` once it is written. `own_file` adds
    the line saying that Save stops a catalog checking the file for updates.
    """
    held = {"angle": 0}
    unsaved: list[Any] = []
    saving: list[bool] = []

    def show() -> None:
        ui.run_javascript(_PREVIEW.replace("ELEMENT", json.dumps(element_id))
                          .replace("ANGLE", str(held["angle"])))
        for one in unsaved:
            one.set_visibility(bool(held["angle"]))

    def pressed(step: int) -> None:
        if saving:
            return
        held["angle"] = (held["angle"] + step) % 360
        show()

    def cancel() -> None:
        held["angle"] = 0
        show()

    async def save() -> None:
        if saving:
            return
        way = _WAYS[held["angle"]]
        saving.append(True)
        try:
            await rotate_job.run(
                lambda: ApiClient().rotate_media([(game_id, path)], way, True), state, then)
        finally:
            saving.clear()

    with ui.column().classes("w-full gap-0 manager-edit"):
        with ui.row().classes("items-center gap-1 w-full manager-editor-strip"):
            for tool in chosen:
                panel.icon_action(tool.label, partial(pressed, tool.step),
                                  icon=tool.icon, hint=tool.reason or tool.label,
                                  enabled=not tool.reason)()
            ui.space()
            unsaved.append(panel.draft_button(
                t("word.cancel"), cancel, icon=verbs.CANCEL,
                hint=t("manager.media_rotate.cancel.help")))
            unsaved.append(panel.draft_button(
                t("word.save"), save, icon=verbs.SAVE,
                hint=t("manager.text_editor.save.help"), primary=True))
        if own_file:
            unsaved.append(ui.label(t("manager.media_rotate.own_file"))
                           .classes("manager-help"))
    for one in unsaved:
        one.set_visibility(False)
