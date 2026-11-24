"""Picking the Manager's appearance by looking at it.

A select would put four words on screen, and the words are the weakest part of this
decision - "Synthwave" means nothing until you have seen it, and Dark and Light are
two words for something the eye settles in about a tenth of a second. So each mode
draws itself: a miniature of this surface in that mode's own colors.

The values are expanded rather than referenced. Every swatch is painted on a page that
is already in one of these modes, so a `var()` left in place would resolve against the
palette in use and all four would come out the same.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from nicegui import ui

from common.i18n import t
from manager.kit import appearance
from manager.kit.on_page import on_page

# The name each mode goes by on screen, in the order a person is offered them: the one a
# new install starts on, then the classic look, then the two plain ones.
NAMES = {
    appearance.SYSTEM: "config.manager.appearance.choice.system",
    "synthwave": "config.manager.appearance.choice.synthwave",
    "dark": "config.manager.appearance.choice.dark",
    "light": "config.manager.appearance.choice.light",
}
# The few words under a name. Dark and Light say what they are; these two do not.
HELPS = {
    appearance.SYSTEM: "config.manager.appearance.choice_help.system",
    "synthwave": "config.manager.appearance.choice_help.synthwave",
}
# Setup draws a mode by this name in its picture slot, so the miniature is the same one.
PICTURE_PREFIX = "appearance:"


def choices() -> list[tuple[str, str, str, str]]:
    """Every mode as `(key, name, caption, picture)`, in the order they are offered. Setup
    and Settings both draw from this, so the two cannot word or order them differently."""
    return [(mode, t(NAMES[mode]), t(HELPS[mode]) if mode in HELPS else "",
             PICTURE_PREFIX + mode) for mode in appearance.MODES]


def _miniature(mode: str, half: str = "") -> None:
    """One palette, drawn as the surface it paints: a rail, and the work beside it.

    Which two regions is not arbitrary - the rail against the work area is the contrast
    the last round of palette work was all about, so it is the thing worth previewing.
    """
    ground = appearance.token("--surface-0", mode)
    with ui.element("div").classes(f"manager-swatch-part {half}") \
            .style(f"background:{ground}"):
        ui.element("div").classes("manager-swatch-rail") \
            .style(f"background:{appearance.token('--nav-bg', mode)}")
        with ui.element("div").classes("manager-swatch-work") \
                .style(f"background:{appearance.token('--surface-work', mode)};"
                       f"border-color:{appearance.token('--line', mode)}"):
            ui.element("div").classes("manager-swatch-flair") \
                .style(f"background:{appearance.token('--flair', mode)}")
            for width in ("70%", "45%"):
                ui.element("div").classes("manager-swatch-line") \
                    .style(f"background:{appearance.token('--ink', mode)};width:{width}")


def swatch(mode: str) -> None:
    """The preview for one choice. `system` is the only one that is two."""
    with ui.element("div").classes("manager-swatch"):
        if mode == appearance.SYSTEM:
            # Both of them, because that is what it is: the picture cannot show one
            # answer when the answer is the device's to give.
            for side, palette in zip(("manager-swatch-part--left",
                                      "manager-swatch-part--right"),
                                     appearance.SYSTEM_PALETTES.values(), strict=True):
                _miniature(palette, side)
        else:
            _miniature(mode)


def tiles(option: dict[str, Any], value: Any, save: Callable[[Any], Any], *,
          section: dict[str, Any] | None = None,
          writable: bool = True,
          refill: Callable[[], None] | None = None,
          **_: Any) -> Callable[[], None]:
    """The four modes, side by side, the one in use marked on its edge.

    The palette is rewritten in place rather than the page reloading. Every surface
    reads the mode from custom properties, so nothing below has to be redrawn - and a
    reload took the settings page back to the top, which is a long way from the control
    that was just used.
    """
    held = {"mode": appearance.mode_or_default(str(value or ""))}

    def draw() -> None:
        made: dict[str, Any] = {}

        def mark() -> None:
            """The edge on the one in use, moved rather than redrawn."""
            for key, tile in made.items():
                on = key == held["mode"]
                tile.classes(add="manager-swatch-tile--active") if on \
                    else tile.classes(remove="manager-swatch-tile--active")
                tile.props(f'aria-pressed="{str(on).lower()}"')

        with ui.element("div").classes("manager-swatches"):
            for mode in appearance.MODES:
                @on_page
                async def pick(mode: str = mode) -> None:
                    if mode == held["mode"] or not await save(mode):
                        return
                    held["mode"] = mode
                    appearance.repaint(mode)
                    mark()

                tile = ui.element("button").classes("manager-swatch-tile") \
                    .props('type="button"')
                made[mode] = tile
                if writable:
                    tile.on("click", pick)
                else:
                    tile.props("disabled")
                with tile:
                    swatch(mode)
                    ui.label(t(NAMES[mode])).classes("manager-swatch-name")
                    if mode in HELPS:
                        ui.label(t(HELPS[mode])).classes("manager-swatch-help")
        mark()

    return draw
