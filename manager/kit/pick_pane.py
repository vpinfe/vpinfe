"""The pane beside a picker's list: what is in use now, what is picked, and what saving does.

Every picker draws the same pane, so a person who has seen one knows the others. Current
is the file in use, drawn once and as it really looks. New is whatever the list, a drop or
the file button last offered, and stays empty until something is. A picture is always
shown whole, in its own shape, on the dark ground that hugs it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from nicegui import ui

from common.i18n import t
from manager.kit import panel, verbs

# How many lines of a text a card shows before it stops, so a long notes file is a taste of
# itself and not a column.
TEXT_LINES = 6

PICTURE, VIDEO, AUDIO, TEXT, FILE, FOLDER = "picture", "video", "audio", "text", "file", "folder"

_GLYPHS = {AUDIO: "graphic_eq", FILE: "description", FOLDER: "folder", TEXT: "notes",
           PICTURE: "image", VIDEO: "movie"}


@dataclass(frozen=True)
class Card:
    """One thing the pane can show, in the shape its kind takes."""

    name: str = ""
    shape: str = PICTURE
    src: str = ""
    detail: str = ""
    text: str = ""
    glyph: str = ""


@dataclass(frozen=True)
class Trip:
    """A pick that is a page elsewhere: the file is fetched there and dropped back here."""

    site: str
    to: str
    opened: Callable[[], Any] | None = None


def card_for(family: str, src: str, name: str, detail: str = "") -> Card:
    """The card a file of this media family is drawn as: a picture, a video or a file."""
    shape = {"image": PICTURE, "video": VIDEO, "audio": AUDIO}.get(family, FILE)
    return Card(name=name, shape=shape, src=src if shape != FILE else "", detail=detail)


class Pane:
    """Current and New, side by side for a tall kind and stacked for the rest, with a place
    under them for whatever a picker adds (the For line, the way a table is kept). A
    picker that fills nothing already there (adding a table) is `single`: New alone.
    `extra` is that place, and exists once `draw` has run."""

    extra: ui.column

    def __init__(self, *, now: Card | None, empty: str, tall: bool, drop: str,
                 choose: Callable[[], None] | None = None, single: bool = False,
                 enlarge: Callable[[Card], None] | None = None) -> None:
        self.single = single
        self.now = now
        self.empty = empty
        self.tall = tall
        self.drop = drop
        self.choose = choose
        self.enlarge = enlarge
        self.saves_as = ""
        self.card: Card | None = None
        self.trip: Trip | None = None
        self.backups: ui.label | None = None
        self._now: ui.column | None = None
        self._new: ui.column | None = None
        self._choose: ui.row | None = None
        self._marked: Any = None

    def draw(self) -> ui.column:
        with ui.column().classes("manager-pane gap-2") as root:
            with ui.element("div").classes("manager-pair" + (" manager-pair--side" if self.tall
                                                             else "")):
                if not self.single:
                    with ui.column().classes("manager-pair-cell gap-1"):
                        ui.label(t("manager.pane.current")).classes("manager-pair-label")
                        self._now = ui.column().classes("manager-pair-art gap-1")
                    ui.icon("arrow_forward").classes("manager-pair-arrow")
                with ui.column().classes("manager-pair-cell gap-1"):
                    ui.label(t("manager.pane.new")).classes("manager-pair-label")
                    self._new = ui.column().classes("manager-pair-art gap-1")
                    # Drawn once, outside what a pick repaints: a button that is deleted while
                    # the browser's file chooser is open has nothing to answer.
                    if self.choose is not None:
                        self._choose = ui.row().classes("manager-pair-choose")
                        with self._choose:
                            self.choose()
            self.backups = ui.label(t("manager.pane.backups")).classes("manager-help")
            self.backups.visible = False
            self.extra = ui.column().classes("w-full gap-1 manager-pane-extra")
        self._paint_now()
        self._paint_new()
        return root

    def backs_up(self, yes: bool) -> None:
        """Say the file there now goes to Backups, where it does."""
        if self.backups is not None:
            self.backups.visible = yes

    def show(self, card: Card | None) -> None:
        self.card, self.trip = card, None
        self._paint_new()

    def save_as(self, name: str) -> None:
        """Name New by the file it will become rather than the file picked: Current and New
        then read as the name now and the name after. Empty names it as picked."""
        if name == self.saves_as:
            return
        self.saves_as = name
        if self.card is not None:
            self._paint_new()

    def go_to(self, trip: Trip) -> None:
        self.card, self.trip = None, trip
        self._paint_new()

    def reset(self) -> None:
        self.show(None)

    def mark(self, row: Any) -> None:
        """Light `row` as the picked one and put the light out on the one before it."""
        if self._marked is not None and not self._marked.is_deleted:
            self._marked.classes(remove="manager-source-row--chosen")
        self._marked = row
        if row is not None:
            row.classes("manager-source-row--chosen")

    def _paint_now(self) -> None:
        if self._now is None:
            return
        self._now.clear()
        with self._now:
            if self.now is None:
                ui.label(self.empty).classes("manager-help manager-pair-empty")
            else:
                _card(self.now, self.enlarge)

    def _paint_new(self) -> None:
        if self._new is None:
            return
        self._new.clear()
        with self._new:
            if self.card is not None:
                _card(self.card, self.enlarge, self.saves_as)
            elif self.trip is not None:
                ui.label(t("manager.pane.trip", site=self.trip.site)) \
                    .classes("manager-help manager-pair-trip--wide")
                ui.label(t("manager.pane.trip_back", site=self.trip.site)) \
                    .classes("manager-help manager-pair-trip--narrow")
                panel.link_out(t("manager.pane.open_site", site=self.trip.site),
                               to=self.trip.to, on_click=self.trip.opened)()
            else:
                with ui.column().classes("manager-pair-drop items-center gap-1"):
                    ui.icon(verbs.FROM_FILE).classes("manager-pair-drop-icon")
                    ui.label(self.drop).classes("manager-help manager-drop-only")
        if self._choose is not None:
            late = self.card is not None or self.trip is not None
            self._choose.classes(add="manager-pair-choose--late" if late else "",
                                 remove="" if late else "manager-pair-choose--late")


def _card(card: Card, enlarge: Callable[[Card], None] | None = None, name: str = "") -> None:
    with ui.column().classes("manager-pair-card gap-1"):
        if card.shape in (PICTURE, VIDEO) and card.src:
            with ui.element("div").classes("manager-pick-art"):
                if card.shape == VIDEO:
                    ui.html(f'<video src="{card.src}" autoplay loop muted playsinline></video>')
                else:
                    ui.html(f'<img src="{card.src}">')
                if enlarge is not None:
                    panel.icon_only(verbs.ENLARGE, lambda: enlarge(card),
                                    hint=t("word.enlarge")).classes("manager-slot-zoom")
        elif card.shape == AUDIO and card.src:
            ui.html(f'<audio src="{card.src}" controls></audio>')
        elif card.shape == TEXT and card.text:
            ui.label("\n".join(card.text.splitlines()[:TEXT_LINES])) \
                .classes("manager-pick-text")
        else:
            with ui.element("div").classes("manager-pick-art manager-pick-art--glyph"):
                ui.icon(card.glyph or _GLYPHS.get(card.shape, "description")) \
                    .classes("manager-source-thumb-glyph")
        if name or card.name:
            ui.label(name or card.name).classes("manager-source-name manager-pair-name")
        if card.detail:
            ui.label(card.detail).classes("manager-help")
