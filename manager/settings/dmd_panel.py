"""The DMD panel switch: an LED panel on the cabinet in place of a screen.

The switch is `real_dmd.enabled`. The kind is asked once it is on, because Visual Pinball
shows nothing on a panel until its DMDUtil plugin is told which kind to look for, and
`follow` carries the answer there. Turning the switch off writes VPinFE's setting only.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

from nicegui import ui

from apps.vpx import screens as vpx
from common.host import tools
from common.i18n import t
from manager.kit import panel
from manager.settings.displays import Write, stored

SECTION = "real_dmd"
KIND_WORDS = {vpx.ZEDMD: "manager.dmd_panel.kind.zedmd",
              vpx.PIN2DMD: "manager.dmd_panel.kind.pin2dmd",
              vpx.PIXELCADE: "manager.dmd_panel.kind.pixelcade"}
# Told the kind that was picked, once it is kept.
Follow = Callable[[str], Awaitable[Any]]


def is_on(values: Mapping[str, Any], schema: Sequence[dict]) -> bool:
    said = str(stored(values, schema, SECTION, "enabled") or "").strip().lower()
    return said not in ("", "0", "false", "no", "off")


def kind_of(values: Mapping[str, Any], schema: Sequence[dict]) -> str:
    """The kind VPinFE can tell from its own settings. Only PIN2DMD has one; ZeDMD and
    Pixelcade are found by the hardware, so the caller says where else to look."""
    said = str(stored(values, schema, SECTION, "pin2dmd_enabled") or "").strip().lower()
    return vpx.PIN2DMD if said not in ("", "0", "false", "no", "off") else ""


def writes(on: bool, kind: str) -> dict[str, dict[str, Any]]:
    """VPinFE's settings for the switch and the kind. A kind is only written while the
    switch is on; PIN2DMD's own setting follows whether the kind is PIN2DMD."""
    if not on:
        return {SECTION: {"enabled": False}}
    found: dict[str, Any] = {"enabled": True}
    if kind in vpx.DMD_KINDS:
        found["pin2dmd_enabled"] = kind == vpx.PIN2DMD
    return {SECTION: found}


def rows(values: Mapping[str, Any], schema: Sequence[dict], write: Write,
         refill: Callable[[], None], follow: Follow | None = None, *,
         kind: str = "") -> list[tuple[Any, Any]]:
    """The switch, and under it the question of which kind once it is on. `kind` is what
    the caller already knows when VPinFE's settings do not say. `refill` draws the rows
    again from the stored settings, which is how a refused write puts the switch back."""
    held = {"on": is_on(values, schema), "kind": kind_of(values, schema) or kind}

    async def switched(event: Any) -> None:
        on = bool(event.value)
        if on == held["on"]:
            return
        if await write(writes(on, str(held["kind"]))):
            held["on"] = on
            if on and held["kind"] and follow is not None:
                await follow(str(held["kind"]))
        refill()

    async def picked(event: Any) -> None:
        chosen = str(event.value or "")
        if not chosen or chosen == held["kind"]:
            return
        if await write(writes(True, chosen)):
            held["kind"] = chosen
            if follow is not None:
                await follow(chosen)
        else:
            refill()

    found: list[tuple[Any, Any]] = [
        (t("manager.dmd_panel.switch"),
         panel.switch(bool(held["on"]), switched, hint=t("manager.dmd_panel.switch.help")))]
    if held["on"]:
        kinds = [(one, t(KIND_WORDS[one]), "") for one in vpx.DMD_KINDS]
        found.append((t("manager.dmd_panel.kind"),
                      panel.choice(kinds, str(held["kind"]), picked)))
        if tools.resolve(tools.FFMPEG).state != tools.State.FOUND:
            found.append((panel.FULL, _ffmpeg_missing))
    return found


def _ffmpeg_missing() -> None:
    with ui.element("div").classes("manager-attention w-full"):
        ui.icon("error_outline").classes("manager-attention-icon")
        ui.label(t("manager.dmd_panel.ffmpeg_missing")) \
            .classes("manager-attention-line min-w-0 grow")
