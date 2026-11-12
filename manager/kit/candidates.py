"""One row for one thing you could pick, wherever the Manager offers a choice of things.

A file to put in a media slot, a catalog entry to match a game against - the question
is the same in both, and it is not one a name answers. So the row leads with the
picture, and keeps its frame when there is no picture to put in it: a list where only
some rows carry art steps in and out as it scrolls.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from nicegui import ui

from manager.kit import panel

# A family with no frame of its own still gets a mark, so the slot is never empty.
GLYPHS = {"audio": "graphic_eq"}
FALLBACK = "description"
SHOWABLE = ("image", "video")


def _peek(src: str, family: str) -> None:
    """A bigger look, without leaving the dialog the row is in.

    Off to the side rather than over the row, so the list it belongs to stays readable
    behind it. In a tooltip because the lists around it scroll, and anything drawn
    inside a scrolling box is clipped by it.
    """
    with ui.tooltip().classes("manager-thumb-peek") \
            .props('anchor="center left" self="center right"'):
        if family == "video":
            ui.html(f'<video src="{src}#t=0.1" preload="metadata" muted '
                    f'playsinline></video>')
        else:
            ui.html(f'<img src="{src}">')


def credits(*pieces: str | tuple[str, ...]) -> Callable[[], None]:
    """A meta line of `pieces` set apart by a dot. A piece given as (words, address) or
    (words, address, hint) is a link out, so a row can name where it came from and let the
    person go and look."""
    def draw() -> None:
        with ui.row().classes("manager-help items-center gap-1 no-wrap"):
            for index, piece in enumerate(pieces):
                if index:
                    ui.label("\u00b7")
                if isinstance(piece, tuple):
                    panel.link_out(piece[0], to=piece[1],
                                   hint=piece[2] if len(piece) > 2 else "")()
                else:
                    ui.label(piece)

    return draw


def _body(src: str, name: str, meta: str | Callable[[], None], tag: str, family: str,
          glyph: str, small: bool = False, line: bool = False, peek: str = "",
          more: tuple[str, ...] = (), missing: str = "", slot: bool = False,
          tag_here: bool = False) -> None:
    """The picture and the words, which both shapes draw the same way."""
    frame = "manager-source-thumb" + (" manager-source-thumb--slot" if slot else
                                      " manager-source-thumb--small" if small else "")
    if line:
        ui.icon(glyph or GLYPHS.get(family, FALLBACK)).classes("shrink-0")
    else:
        _frame(frame, src, family, glyph, peek, missing)
    with ui.column().classes("gap-0 min-w-0 grow"):
        ui.label(name).classes("manager-source-name")
        if callable(meta):
            meta()
        elif meta:
            ui.label(meta).classes("manager-help")
        for said in more:
            ui.label(said).classes("manager-help")
        if tag:
            ui.label(tag).classes("manager-source-tag"
                                  + (" manager-source-tag--here" if tag_here else ""))


def thumb(src: str, glyph: str, missing: str = "", family: str = "image") -> None:
    """The small picture a row leads with, for a row that draws its own words."""
    _frame("manager-source-thumb manager-source-thumb--small", src, family, glyph,
           missing=missing)


def _frame(frame: str, src: str, family: str, glyph: str, peek: str = "",
           missing: str = "") -> None:
    with ui.element("div").classes(frame) as box:
        if src and family == "video":
            ui.html(f'<video src="{src}#t=0.1" preload="metadata" muted '
                    f'playsinline></video>')
        elif src and family == "image":
            ui.html(f'<img src="{src}" loading="lazy">')
        else:
            ui.icon(glyph or GLYPHS.get(family, FALLBACK)) \
                .classes("manager-source-thumb-glyph")
        if src and family in SHOWABLE:
            _peek(peek or src, family)
    if missing and not src:
        box.tooltip(missing)


def row(src: str, name: str, meta: str | Callable[[], None], tag: str, pick: Callable, *,
        family: str = "image", glyph: str = "", line: bool = False, peek: str = "",
        use: Callable | None = None, tag_here: bool = False,
        missing: str = "") -> Any:
    """What it looks like and what it is, with the whole row as the target.

    A click picks it, which is a choice and not yet an act; a double-click is `use`, the act
    itself, for a person who knows what they want. `meta` is a line of words, or a function
    that draws one (see `credits`). `line` draws it the way a folder is drawn, for a list
    where nothing has a picture. `tag` is what the file already does, green where it does
    that for the thing being filled. `peek` is a larger copy for the bigger look, where
    `src` is sized for the row.
    """
    classes = "items-center gap-3 w-full no-wrap manager-source-row manager-source-row--pick"
    element = ui.row().classes(classes + (" manager-source-row--line" if line else ""))
    with element:
        _body(src, name, meta, tag, family, glyph, line=line, peek=peek, slot=True,
              tag_here=tag_here, missing=missing)
    element.on("click", lambda: pick())
    if use is not None:
        element.on("dblclick", lambda: use())
    return element


def choice(src: str, name: str, meta: str, pick: Callable | None = None, *,
           family: str = "image", glyph: str = "", chosen: bool = False,
           trailing: Callable[[], None] | None = None, entry: bool = False,
           more: tuple[str, ...] = (), missing: str = "", slot: bool = False,
           use: Callable | None = None, tag: str = "", tag_here: bool = False) -> Any:
    """A row whose target is the whole row, with no button on it.

    For the lists you scan rather than compare: forty candidates with forty buttons is
    forty times the same word. `row` is the one to use where the act wants naming.

    The picture is small here, because these lists are the long ones and recognizing a
    thing is a smaller question than judging it.

    `pick` absent draws the row without making it a target, for showing one on its own.
    `chosen` lights it. `trailing` puts one control at the end, for an act about that
    row rather than about the list. `entry` takes the grid's two-line type, for a row
    naming the same kind of thing a grid row names. `more` is lines under `meta`, as
    quiet as it is. `missing` is what the frame says on hover when there is no picture.
    `slot` draws the picture in the slot a picker's list shares, whole. `use` is what a
    double-click does. `tag` and `tag_here` are as in `row`.
    """
    classes = "items-center gap-3 w-full no-wrap manager-source-row"
    if pick is not None:
        classes += " manager-source-row--pick"
    if chosen:
        classes += " manager-source-row--chosen"
    if entry:
        classes += " manager-source-row--entry"
    element = ui.row().classes(classes)
    with element:
        _body(src, name, meta, tag, family, glyph, small=True, more=more, missing=missing,
              slot=slot, tag_here=tag_here)
        if trailing is not None:
            trailing()
    if pick is not None:
        element.on("click", lambda: pick())
    if use is not None:
        element.on("dblclick", lambda: use())
    return element
