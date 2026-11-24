"""The picture ahead of a name in a list: which one, where it is served, and its frame.

Every row of a list draws the same frame, holding the row's glyph where there is no
picture. Its shape, height and ground are the Media settings, carried by an ancestor of
the frame as a `Look`. The address comes from `manager/kit/art.py`.
"""

from __future__ import annotations

import html
import json
import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from typing import Any

from nicegui import ui

from common.config import config_schema
from common.config.config_access import cfg_bool, cfg_get
from common.config.paths import get_ini_config
from manager.kit import art, dropdown

# The row field holding the address, "" where the row has none. A row without the field
# is drawn with no frame at all.
FIELD = "art"
# The two frames: the lists', sized by a `Look`, and the Themes grid's 128 x 72 preview.
LIST = "list"
PREVIEW = "preview"

SQUARE = "square"
WIDE = "wide"
SMALL = "small"
# Each height's row, and the picture in it with 8px above and below.
ROW_PX = {SMALL: 56, "medium": 64, "large": 88}
_ART_PX = {SMALL: 40, "medium": 48, "large": 72}
_GAP_PX = 12
_SQUARE_CLASS = "manager-art-square"
_BARE_CLASS = "manager-art-bare"

_PICTURED = "manager-cell-pictured"
_BOX = "manager-cell-art-box"
_SIZED = {LIST: f"{_BOX} {_BOX}--list", PREVIEW: _BOX}
_GLYPH_BOX = f"{_BOX}--glyph"
_NO_ART = "manager-cell-noart"
_INITIALS = "manager-circle-initials"
_LINES = "manager-cell-lines"
_PATH_DATA = re.compile(r"^[Mm]\s?[-+]?\.?\d")
# A picture that fails to load gives way to the glyph.
_SWAP = f"this.parentNode.classList.add('{_GLYPH_BOX}');this.remove()"
_IMG = f'loading="lazy" alt="" draggable="false" onerror="{_SWAP}"'


def chosen(kept: set[str]) -> str:
    """The kind the lists draw, or "" for none: None, or a kind Media Kinds does not
    keep. A value the setting does not offer reads as its default."""
    option = config_schema.option("manager", "thumbnail")
    assert option is not None
    value = cfg_get(get_ini_config(), "manager", "thumbnail")
    if value not in option.choices:
        value = str(option.default)
    return value if value != "none" and value in kept else ""


@dataclass(frozen=True)
class Look:
    """How the lists draw their art: the frame's shape, the grids' row height, and
    whether the picture sits on the frame's ground or on the row."""

    shape: str = WIDE
    height: str = SMALL
    framed: bool = True

    @property
    def row_px(self) -> int:
        return ROW_PX[self.height]

    @property
    def room_px(self) -> int:
        """How much wider a name column is for the frame and the gap after it."""
        tall = _ART_PX[self.height]
        return (tall if self.shape == SQUARE else 2 * tall) + _GAP_PX

    @property
    def _classes(self) -> str:
        return " ".join(name for name, on in ((_SQUARE_CLASS, self.shape == SQUARE),
                                              (_BARE_CLASS, not self.framed)) if on)

    def apply(self, element: ui.element) -> None:
        """Put the look on `element`, an ancestor of the frames it sizes."""
        element.classes(self._classes).style(f"--art-h: {_ART_PX[self.height]}px")

    def apply_to_options(self, select: dropdown.Select, popup_classes: str) -> None:
        """Put the look on `select`'s options, which open in a popup outside it, beside
        the popup's own `popup_classes`."""
        select.props(f'popup-content-class="{popup_classes} {self._classes}" '
                     f'popup-content-style="--art-h: {_ART_PX[self.height]}px"')

    def in_panel(self) -> Look:
        """The look a panel's list takes: its shape and frame, at the smallest height."""
        return replace(self, height=SMALL)


def look(kind: str) -> Look:
    """How the lists draw `kind`, with Automatic's shape answered for it."""
    config = get_ini_config()

    def chose(key: str) -> str:
        option = config_schema.option("manager", key)
        assert option is not None
        value = cfg_get(config, "manager", key)
        return value if value in option.choices else str(option.default)

    shape = chose("thumbnail_shape")
    if shape not in (SQUARE, WIDE):
        shape = SQUARE if kind == "wheel" else WIDE
    return Look(shape, chose("thumbnail_height"),
                cfg_bool(config, "manager", "thumbnail_frame", True))


def person_look() -> Look:
    """How a list draws a person: a circle, so square and on its own ground, at the height
    the lists are set to whatever their Show, Shape and Frame say."""
    return replace(look(""), shape=SQUARE, framed=True)


def address(row: dict[str, Any]) -> str:
    """Where a tables-listing row's art is served at list size, or "" where it has none.

    A keyed table has no file to name art after, so its art is the game's."""
    kind, version = row.get("media_kind"), row.get("media_version")
    if not kind or not version:
        return ""
    table = "" if row.get("key") else str(row.get("id") or "")
    return art.media(str(row.get("game_id") or ""), str(kind), table,
                     version=str(version), size=art.CELL)


def collection(row: dict[str, Any]) -> str:
    """Where a collection's own picture is served at list size, or "" where it has none."""
    if not row.get("image"):
        return ""
    return art.collection(str(row.get("name") or ""), version=row.get("image_version"),
                          size=art.CELL)


def by_game(rows: list[dict[str, Any]]) -> dict[str, str]:
    """Each game's art from the tables listing: its default table's, else its first's."""
    shown: dict[str, str] = {}
    for row in rows:
        game_id = str(row.get("game_id") or "")
        if game_id not in shown or row.get("default"):
            shown[game_id] = address(row)
    return shown


def by_table(rows: list[dict[str, Any]]) -> dict[tuple[str, str], str]:
    """Each table's art from the tables listing, by its game's id and its own."""
    return {(str(row.get("game_id") or ""), str(row.get("id") or "")): address(row)
            for row in rows}


def glyph_html(glyph: str) -> str:
    """A glyph as the frame holds it: SVG path data, or a Material icon's name."""
    if _PATH_DATA.match(glyph):
        return (f'<svg class="{_NO_ART}" viewBox="0 0 24 24" aria-hidden="true">'
                f'<path d="{html.escape(glyph)}"/></svg>')
    return f'<i class="material-icons {_NO_ART}" aria-hidden="true">{html.escape(glyph)}</i>'


def frame_js(glyph: str, size: str = LIST) -> str:
    """A grid cell renderer's expression for the frame and the name's lines beside it.

    Reads `art`, `lines` and `esc` from the renderer: `art` is the address, or "" for
    the glyph.
    """
    opened = f'<span class="{_PICTURED}"><span class="{_SIZED[size]}'
    with_picture = json.dumps(f'{opened}"><img {_IMG} src="')
    without = json.dumps(f'{opened} {_GLYPH_BOX}">')
    beside = json.dumps(f'{glyph_html(glyph)}</span><span class="{_LINES}">')
    return (f"(art ? {with_picture} + esc(art) + '\">' : {without})"
            f" + {beside} + lines + '</span></span>'")


def circle_js(glyph: str) -> str:
    """`frame_js` for a person: always a circle, sized by the grid's `person_look`, holding
    the row's `initials` where there is no picture, else `glyph`."""
    opened = f'<span class="{_PICTURED}"><span class="{_BOX} {_BOX}--list {_BOX}--circle'
    with_picture = json.dumps(f'{opened}"><img {_IMG} src="')
    without = json.dumps(f'{opened} {_GLYPH_BOX}">')
    initials = json.dumps(f'<span class="{_NO_ART} {_INITIALS}">')
    person = json.dumps(glyph_html(glyph))
    beside = json.dumps(f'</span><span class="{_LINES}">')
    return (f"(art ? {with_picture} + esc(art) + '\">' : {without})"
            f" + (initials ? {initials} + esc(initials) + '</span>' : {person})"
            f" + {beside} + '<span>' + lines + '</span></span></span>'")


def frame_html(address: str, glyph: str, *, to: str = "", size: str = LIST) -> str:
    """A frame as HTML: the picture at `address`, or the glyph where it is "".

    `to` makes it a link there, for a row whose name links there too. `size` is the
    lists' frame, or the Themes grid's 128 x 72 preview."""
    sized = _SIZED[size] + ("" if address else f" {_GLYPH_BOX}")
    picture = f'<img {_IMG} src="{html.escape(address)}">' if address else ""
    opened = (f'<a href="{html.escape(to)}" tabindex="-1" aria-hidden="true" '
              f'class="{sized}">' if to else f'<span class="{sized}">')
    return f'{opened}{picture}{glyph_html(glyph)}{"</a>" if to else "</span>"}'


@contextmanager
def beside(address: str | None, glyph: str, *, to: str = "",
           look: Look | None = None, size: str = LIST) -> Iterator[None]:
    """A row drawn in Python: a frame, with what the block draws as the name's lines
    beside it. `address` as `frame_html` takes it, or None for no frame at all.
    `look` is the lists' look, which a panel's row takes at its own height; the preview
    frame has a size of its own and takes none."""
    if address is None:
        yield
        return
    with ui.element("span").classes(f"{_PICTURED} grow") as pictured:
        if size == LIST:
            (look or Look()).in_panel().apply(pictured)
        ui.html(frame_html(address, glyph, to=to, size=size), sanitize=False, tag="span")
        with ui.element("span").classes(f"{_LINES} grow"):
            yield


def option_html(lines: str, glyph: str) -> str:
    """A `ui.select` option template's frame with `lines` beside it, read from the
    option's `art`. An option without the field shows `lines` alone."""
    art_of = f"props.opt.{FIELD}"
    sized = _SIZED[LIST]
    frame = (f'<span :key="{art_of}" :class="{art_of} ? \'{sized}\' '
             f': \'{sized} {_GLYPH_BOX}\'"><img v-if="{art_of}" {_IMG} :src="{art_of}">'
             f'{glyph_html(glyph)}</span>')
    return (f'<span v-if="{art_of} !== undefined" class="{_PICTURED}">{frame}'
            f'<span class="{_LINES}">{lines}</span></span><template v-else>{lines}</template>')
