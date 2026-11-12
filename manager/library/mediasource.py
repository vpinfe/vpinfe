"""Where a file comes from, in one place.

Three ways in, because there are three: the device you are looking at this from, the
machine VPinFE runs on, and the online sources. A collection's picture has a fourth, the art its
games already have, and a table has one with no file at all. Anything already on a disk
is one browser rather than a tab apiece - this game's folder, another game's, and a folder
of downloads are the same act, and splitting them made three answers to one question.

A slot's file lands under the slot's name at the tier the lens is on, and whatever it
displaced was named before it went.
"""

from __future__ import annotations

import asyncio
import base64
import inspect
import logging
import mimetypes
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import partial
from pathlib import PurePath, PurePosixPath, PureWindowsPath
from typing import Any

from nicegui import background_tasks, run, ui

from common import apps, i18n, icons
from common.capture.run import FILL
from common.capture.session import RECORDABLE
from common.failures import why
from common.games.asset_registry import ARCHIVE_EXTENSIONS, ASSET_SPECS, spec_for, specs_named
from common.i18n import t
from common.media.media_specs import (
    DOC_FAMILY,
    IMAGE_FAMILY,
    MEDIA_SPECS,
    canonical_kind,
    media_family,
    media_label_map,
)
from common.online import sources as source_registry
from manager.kit import (
    acting,
    art,
    busy,
    candidates,
    deeplink,
    folder_picker,
    kind_words,
    offload,
    panel,
    pick_pane,
    verbs,
)
from manager.kit import dialog as frame
from manager.kit.on_page import on_page
from manager.library import mediaview, picker_memory, picker_online, record, uploads
from manager.settings import recording

logger = logging.getLogger("vpinfe.manager.library.mediasource")

# Enough of a list to scroll rather than to page. A folder of artwork is tens of files,
# not thousands, and a picker with pages in it is a database browser.
_LIST_MAX = 60
_RECHECK_S = 3.0

_FIND_FROM = 8

# The folder a game keeps its media in, which a file written there is named under.
MEDIA_FOLDER = "medias"

# What the Backglass File tab saves, which is the name its extension is read from.
BACKGLASS_PICTURE = "backglass.png"

SIZE_NAMES = picker_online.SIZE_NAMES

# Only a drag carrying files belongs to the dialog: text dragged within a field is left
# to the field.
_FILES = "Array.from(e.dataTransfer.types || []).includes('Files')"
_TAKE = (f"if (!{_FILES}) return; e.preventDefault(); e.stopPropagation(); ")
_LIGHT = ("(e) => { " + _TAKE + "e.dataTransfer.dropEffect = 'copy'; "
          "e.currentTarget.classList.add('manager-drop-hot'); }")
_DIM = ("(e) => { if (!e.currentTarget.contains(e.relatedTarget)) "
        "e.currentTarget.classList.remove('manager-drop-hot'); }")
# The kinds a person usually has as one archive, so the file button takes one.
_ONE_OR_FOLDER = frozenset({"pup_pack", "alt_sound", "altsound"})
_MANY = ("(e) => { " + _TAKE + "e.currentTarget.classList.remove('manager-drop-hot'); "
         "window.__managerDrop(e.dataTransfer, emit); }")


def _one(uploader_id: int) -> str:
    """A single file goes to the uploader; anything else comes back as a count."""
    return ("(e) => { " + _TAKE + "e.currentTarget.classList.remove('manager-drop-hot'); "
            "const folder = Array.from(e.dataTransfer.items || []).some(item => "
            "((item.webkitGetAsEntry && item.webkitGetAsEntry()) || {}).isDirectory); "
            "const files = Array.from(e.dataTransfer.files || []); "
            "if (folder || files.length !== 1) { emit({count: files.length, folder}); return; } "
            f"getElement({uploader_id}).$refs.qRef.addFiles(files); }}")


def _size(count: int | None) -> str:
    """Nothing for a file whose size is not known."""
    return i18n.size(count) if count else ""


def _suffix(item: dict[str, Any]) -> str:
    return PurePosixPath(_file_name(str(item.get("name") or ""))).suffix.lower()


# A dropped picture bigger than this is shown as a file card: it travels to the browser
# inside the page.
_PREVIEW_MAX = 4_000_000


def _dropped_card(name: str, data: bytes) -> pick_pane.Card:
    """New's card for a file that has arrived: the picture itself where it is small enough
    to show, else the file's name and size."""
    served = mimetypes.guess_type(name)[0] or ""
    if served.startswith("image/") and len(data) <= _PREVIEW_MAX:
        src = f"data:{served};base64,{base64.b64encode(data).decode()}"
        return pick_pane.card_for("image", src, name, _size(len(data)))
    return pick_pane.card_for("", "", name, _size(len(data)))


@dataclass(frozen=True)
class _Target:
    """What the dialog fills: the calls that fill it, and the files that can."""

    placements: Callable[..., dict]
    displaced: Callable[..., list[str]]
    place: Callable[..., dict]
    family: str
    accept: tuple[str, ...] = ()
    file: bool = False


def _media(library: Any, kind: str) -> _Target:
    family = media_family(kind)
    spec = next((item for item in MEDIA_SPECS if item.kind == canonical_kind(kind)), None)
    return _Target(library.placements, library.displaced_by, library.place_media,
                   family, accept=spec.family if spec else ())


def _asset(library: Any, kind: str) -> _Target:
    return _Target(library.asset_placements, library.asset_displaced_by,
                   library.place_asset, "",
                   accept=spec_for(kind).extensions, file=True)


def _choose(many: bool, heard: Callable[[Any], Any]) -> None:
    """Files, or one file, with a folder behind the caret."""
    panel.split_action(
        t("manager.mediasource.choose_files" if many else "manager.mediasource.choose_file"),
        heard, icon=verbs.FROM_FILE,
        hint=t("manager.mediasource.choose_files.help" if many
               else "manager.mediasource.choose_file.help"),
        js=f"() => window.__managerChoose(false, emit, {str(not many).lower()})",
        also=t("manager.mediasource.choose_folder"),
        also_js="() => window.__managerChoose(true, emit)",
        also_hint=t("manager.mediasource.choose_folder.help"))()


def _host_name(library: Any) -> str:
    return str(library.discovery().get("display_name") or "")


class _Entry:
    """Something a list row, a drop or the file button has offered, waiting for the verb.

    A click on a row picks it, which fills New and wakes the verb; the verb, or a
    double-click, runs `commit`. `discard` gives back what the offer took, such as an
    upload nobody went on to use.
    """

    def __init__(self, sources: _Sources, card: pick_pane.Card | None,
                 commit: Callable[[], Any], discard: Callable[[], Any] | None = None,
                 filename: str = "", arrives: bool = False) -> None:
        self.sources = sources
        self.card = card
        self.commit = commit
        self.discard = discard
        self.filename = filename
        # Whether this is a file that came from outside the list (a drop or the file
        # button), which is how the file fetched from a VPS page arrives.
        self.arrives = arrives
        self.row: Any = None

    def bind(self, row: Any) -> Any:
        self.row = row
        return row

    def pick(self) -> None:
        self.sources.choose(self)

    async def use(self) -> None:
        self.sources.choose(self)
        await self.sources.go()


class _Sources:
    """One picker dialog: its tabs, the pane beside them, and the host's folders.

    A subclass says what is being filled - the title, where the host tab starts, which
    files there can fill it, and what happens to a file once it is chosen.
    """

    online = False
    games = False
    keyed = False
    records = False
    backglass = False
    # The tab the dialog opens on, where it is not the first.
    first = ""
    # Where this dialog is in a walk over several files ("2 of 5"), which turns Cancel into
    # Skip and Stop. Empty outside one.
    walk_place = ""
    # The game this dialog fills, which the host tab opens on the folder of. Empty where it
    # fills no one game.
    game_id = ""
    drop_key = "manager.mediasource.drop_file"
    # Whether the pane is New alone, for a dialog that fills nothing already there.
    pane_alone = False

    def __init__(self, library: Any, label: str, done: Callable, current: str = "") -> None:
        self.library = library
        self.label = label
        self.done = done
        # The path on the host of what fills this now, which the host tab marks rather
        # than offers.
        self.current = current
        self.dialog: Any = None
        self.tabs: Any = None
        self.pane: pick_pane.Pane = None  # type: ignore[assignment]
        self.verb: Any = None
        self.pending: _Entry | None = None
        self.committing = False
        # Whether the browser is on the device VPinFE runs on, which has one device: the
        # host tab is then also where the file button reads from.
        self.merged = False
        # Filled when the host tab loads: where a file shown from the library is served.
        self.browse_roots: list[dict[str, Any]] = []

    def title(self) -> str:
        raise NotImplementedError

    def verb_label(self) -> str:
        """The one verb the footer ends on, named for the job."""
        raise NotImplementedError

    def now_card(self) -> pick_pane.Card | None:
        """What is in use now, or None where nothing is."""
        return None

    @staticmethod
    def enlarge(card: pick_pane.Card) -> None:
        mediaview.open_viewer(card.src, "", card.name,
                              family="video" if card.shape == pick_pane.VIDEO else "image")

    def empty_words(self) -> str:
        return t("manager.pane.no_picture")

    def tall(self) -> bool:
        """Whether Now and New sit side by side, for a kind that is taller than wide."""
        return False

    def drop_words(self) -> str:
        return t(self.drop_key)

    def shown(self) -> bool:
        """False once a dialog that was opened has closed: a read that comes back after
        that has nowhere to draw."""
        return (self.dialog is None
                or (not self.dialog.is_deleted and bool(self.dialog.value)))

    def starts(self) -> list[dict[str, Any]]:
        """The places the host tab offers. Blocking."""
        raise NotImplementedError

    def suffixes(self) -> tuple[str, ...]:
        """What the host tab lists of the files in a folder."""
        raise NotImplementedError

    def fits(self, item: dict[str, Any]) -> bool:
        return True

    def memory_kind(self) -> str:
        """What the folder a file came from is remembered under."""
        return ""

    def kinds_word(self) -> str:
        """Several of what this takes, for the Places entry that names where the last came from."""
        return self.label

    def remember(self, path: str) -> None:
        picker_memory.record(self.memory_kind(), path)

    async def use_file(self, path: str) -> None:
        """Take the file at `path` on the host."""
        raise NotImplementedError

    def file_row(self, item: dict[str, Any]) -> None:
        raise NotImplementedError

    def folder_entry(self, path: str) -> _Entry | None:
        """What picking a folder row offers, or None where a folder is only a way down."""
        return None

    def before(self) -> None:
        """Drawn ahead of the tabs, so it is there whichever tab is open."""

    def drops(self, card: Any) -> None:
        """What a drop on the dialog does."""

    def take_button(self) -> None:
        """The file button, drawn under New."""
        raise NotImplementedError

    def choices(self) -> None:
        """Drawn in the pane under Now and New."""

    def opened(self, extra: ui.column) -> None:
        """Called once the dialog is up, with the pane's place for what a picker adds."""

    async def online_tab(self, body: ui.column) -> None:
        return None

    async def games_tab(self, body: ui.column) -> None:
        return None

    async def keyed_tab(self, body: ui.column) -> None:
        return None

    async def record_tab(self, body: ui.column) -> None:
        return None

    async def backglass_tab(self, body: ui.column) -> None:
        return None

    def picked(self, tab: str) -> None:
        """`tab` was opened."""

    # --- the pick ---------------------------------------------------------------

    def entry(self, card: pick_pane.Card | None, commit: Callable[[], Any], *,
              discard: Callable[[], Any] | None = None, filename: str = "",
              arrives: bool = False) -> _Entry:
        return _Entry(self, card, commit, discard, filename, arrives)

    def choose(self, entry: _Entry) -> None:
        """Make `entry` the pick: New shows it and the verb is ready. Ignored while the
        verb is running, which holds the pick it is working on."""
        if self.committing:
            if entry.discard is not None:
                entry.discard()
            return
        self._let_go(entry)
        self.pending = entry
        self.pane.show(entry.card)
        self.pane.mark(entry.row)
        self.verb.enable()

    def choose_trip(self, trip: pick_pane.Trip | None, row: Any) -> None:
        """Make a trip the pick: New says where to fetch the file, and the verb waits for it.
        A file with no page to fetch it from leaves New empty and still waiting."""
        if self.committing:
            return
        self._let_go(None)
        if trip is None:
            self.pane.reset()
        else:
            self.pane.go_to(trip)
        self.pane.mark(row)
        self.verb.disable()

    def forget_trip(self) -> None:
        """The page New sent the person to is no longer a pick: back to an empty New."""
        if self.pane.trip is not None:
            self.choose_trip(None, None)

    def _let_go(self, keep: _Entry | None) -> None:
        held, self.pending = self.pending, None
        if held is not None and held is not keep and held.discard is not None:
            held.discard()

    @on_page
    async def go(self) -> None:
        """The verb: do what the pick offers."""
        entry = self.pending
        if entry is None or self.committing:
            return
        self.committing = True
        self.verb.disable()
        kept = False
        try:
            await entry.commit()
        except Exception:
            # A path can be tried again; an upload is spent by the attempt.
            kept = entry.discard is None
            raise
        finally:
            self.committing = False
            if self.pending is entry:
                self.pending = entry if kept else None
            if self.shown():
                if kept:
                    self.verb.enable()
                else:
                    self.pane.reset()
                    self.pane.mark(None)

    def _closed(self) -> None:
        # The verb's own commit closes the dialog and owns the pick it is using.
        if not self.committing:
            self._let_go(None)

    def open(self) -> ui.dialog:
        """Show the dialog and answer it: a walk awaits it, which resolves when it closes."""
        with frame.opened(self.title(), size="large", classes="manager-sources-card") as box:
            self.dialog = box
            card = ui.context.slot.parent
            if self.walk_place:
                ui.label(self.walk_place).classes("manager-help")
            self.before()

            # Ordered by how far the file has to travel: this library, the machine
            # VPinFE runs on, then the internet.
            host = _host_name(self.library)
            merged = self.merged = acting.browser_is_here()
            with frame.tabs().props("inline-label") as tabs:
                if self.backglass:
                    ui.tab("backglass", label=t("manager.mediasource.backglass_file"),
                           icon=verbs.FROM_BACKGLASS_FILE)
                if self.games:
                    ui.tab("games", label=t("manager.mediasource.its_games"),
                           icon=icons.GAMES)
                named = ui.tab(
                    "host", icon=verbs.FROM_FILE if merged else verbs.FROM_HOST,
                    label=t("manager.mediasource.this_device") if merged
                    else t("manager.mediasource.on_device", device=host)) \
                    .classes("manager-source-host")
                named.tooltip(t("manager.mediasource.this_device.help") if merged
                              else t("manager.mediasource.on_device.help"))
                if self.online:
                    ui.tab("online", label=t("manager.mediasource.online"),
                           icon=verbs.FROM_ONLINE)
                if self.keyed:
                    ui.tab("keyed", label=t("manager.mediasource.without_file"),
                           icon=verbs.WITHOUT_FILE)
                if self.records:
                    ui.tab("record", label=t("manager.record.record"), icon=verbs.RECORD)
            self.tabs = tabs
            online_body: ui.column | None = None
            games_body: ui.column | None = None
            keyed_body: ui.column | None = None
            record_body: ui.column | None = None
            backglass_body: ui.column | None = None
            first = self.first or "host"
            with frame.body(split=True):
                with ui.tab_panels(tabs, value=first).classes("manager-sources-panels"):
                    if self.backglass:
                        with ui.tab_panel("backglass"):
                            backglass_body = ui.column() \
                                .classes("w-full gap-2 manager-source-fill")
                    if self.games:
                        with ui.tab_panel("games"):
                            games_body = ui.column().classes("w-full gap-2 manager-source-fill")
                    with ui.tab_panel("host"):
                        host_body = ui.column().classes("w-full gap-2 manager-source-fill")
                    if self.online:
                        with ui.tab_panel("online"):
                            online_body = ui.column().classes("w-full gap-2 manager-source-fill")
                    if self.keyed:
                        with ui.tab_panel("keyed"):
                            keyed_body = ui.column().classes("w-full gap-2 manager-source-fill")
                    if self.records:
                        with ui.tab_panel("record"):
                            record_body = ui.column().classes("w-full gap-2 manager-source-fill")
                self.pane = pick_pane.Pane(now=self.now_card(), empty=self.empty_words(),
                                           tall=self.tall(), drop=self.drop_words(),
                                           choose=self.take_button,
                                           single=self.pane_alone,
                                           enlarge=self.enlarge)
                self.pane.draw()
                with self.pane.extra:
                    self.choices()
            with frame.footer():
                if self.walk_place:
                    frame.leave(box, True)
                else:
                    frame.cancel(box.close)
                self.verb = frame.answer(self.verb_label(), self.go, icon=verbs.ACCEPT)
                self.verb.disable()
            frame.enter_presses(self.verb)
        card.on("dragover", js_handler=_LIGHT)
        card.on("dragleave", js_handler=_DIM)
        self.drops(card)
        box.on("hide", self._closed)

        box.open()
        self.opened(self.pane.extra)

        # Each tab reads when it is opened rather than up front: most make a request, and
        # a dialog that fetches everything before showing anything would be slowest at
        # the thing people do most, which is choose a file on the first tab.
        loaded: set[str] = set()

        async def load(event: Any) -> None:
            self.picked(str(event.value))
            if event.value in loaded:
                return
            loaded.add(event.value)
            reads = {"host": (host_body, self.host_tab),
                     "online": (online_body, self.online_tab),
                     "games": (games_body, self.games_tab),
                     "keyed": (keyed_body, self.keyed_tab),
                     "record": (record_body, self.record_tab),
                     "backglass": (backglass_body, self.backglass_tab)}
            body, read = reads.get(event.value, (None, None))
            if body is None or read is None:
                return
            with busy.held(body):
                await read(body)

        tabs.on_value_change(load)
        if first == "host":
            loaded.add("host")
            with box:
                busy.fill(host_body, lambda: self.host_tab(host_body))
        if first == "backglass" and backglass_body is not None:
            loaded.add("backglass")
            self.picked("backglass")
            with box:
                busy.fill(backglass_body, lambda: self.backglass_tab(backglass_body))
        if first == "record" and record_body is not None:
            loaded.add("record")
            self.picked("record")
            with box:
                busy.fill(record_body, lambda: self.record_tab(record_body))
        return box

    async def finish(self, message: str) -> None:
        self.dialog.close()
        ui.notify(message, type="positive")
        await self.done()

    # --- from anywhere on the machine VPinFE runs on -------------------------

    async def host_tab(self, body: ui.column) -> None:
        body.clear()
        suffixes = self.suffixes()
        try:
            starts = await offload.io(self.starts)
            today = self._opens_at(starts)
            first = await folder_picker.read_start(
                self.library.folders, self._first_folder(today, starts),
                kind="file", suffixes=suffixes, nearest=True, fallback=today)
        except Exception as exc:
            if self.shown():
                with body:
                    panel.line(t("manager.mediasource.host_failed",
                                 host=_host_name(self.library)), hint=why(exc))
            return
        if not self.shown():
            return
        self.browse_roots = starts
        with body:
            if not self.merged:
                ui.label(t("manager.mediasource.on_device.line", device=_host_name(self.library))) \
                    .classes("manager-help")
            folder_picker.draw_browser(
                first, self.library.folders, kind="file", suffixes=suffixes,
                take=self.use_file,
                places=picker_memory.places(self.memory_kind(), self.kinds_word(),
                                            _places(starts)),
                file_row=self.file_row, keep=self.fits, pickable=self.folder_entry,
                empty=self.nothing_here(), limit=_LIST_MAX, embedded=True)

    def nothing_here(self) -> str:
        return t("manager.mediasource.nothing_use", kind=self.label)

    def _first_folder(self, today: str, starts: list[dict[str, Any]]) -> str:
        """Where the host tab opens: the game's own folder where this dialog is for one game."""
        own = self.current or any(one.get("source") == "game" for one in starts)
        if self.game_id and today and own:
            return today
        return picker_memory.last(self.memory_kind()) or today

    def _opens_at(self, starts: list[dict[str, Any]]) -> str:
        """The folder of what fills this now, else the game's own folder or the library's,
        else nothing, which is the person's Home - or the top of the disk where the account
        has no Home folder."""
        if self.current:
            return self.current
        return next((str(one["path"]) for one in starts
                     if one.get("source") in ("game", "library")), "")


class _OneFile(_Sources):
    """A dialog that takes one file, through an uploader held to `accept`."""

    def __init__(self, library: Any, label: str, done: Callable,
                 accept: tuple[str, ...], current: str = "") -> None:
        super().__init__(library, label, done, current)
        self.accept = accept
        self.uploader: Any = None
        self._reading_note: Any = None
        # The game whose own folder the host tab opens on, where there is one.
        self.game_id = ""

    async def took(self, name: str, data: bytes) -> None:
        raise NotImplementedError

    def starts(self) -> list[dict[str, Any]]:
        return self.library.disk_starts(self.game_id)

    def suffixes(self) -> tuple[str, ...]:
        return tuple(self.accept)

    def preview(self, path: str) -> str:
        """Where the browser fetches a file to show it. A file the library reads from is
        served to any Manager; one elsewhere on the disk only to this device's own."""
        inside = any(root.get("source") and PurePath(path).is_relative_to(root["path"])
                     for root in self.browse_roots)
        return (self.library.browsed_file_url(path) if inside
                else self.library.disk_file_url(path))

    async def contents(self, path: str) -> bytes:
        """A listed file's bytes, read here."""
        return await offload.io(self.library.disk_file, path, self.accept)

    def before(self) -> None:
        self.uploader = ui.upload(on_upload=self._arrived, on_begin_upload=self._reading,
                                  on_rejected=self._refused, auto_upload=True) \
            .classes("hidden")
        if self.accept:
            self.uploader.props(f'accept="{",".join(self.accept)}"')
        self.uploader.on("failed", self._failed, [])
        # Reset by the browser on its own finish. Sent from here, a reset can arrive
        # before the upload's answer and fail an upload that worked.
        self.uploader.on("finish", js_handler=f"() => getElement({self.uploader.id})"
                                              ".$refs.qRef.reset()")

    def drops(self, card: Any) -> None:
        card.on("drop", self._dropped_wrong, js_handler=_one(self.uploader.id))

    def take_button(self) -> None:
        panel.action(t("manager.mediasource.choose_file"), None, icon=verbs.FROM_FILE,
                     hint=t("manager.mediasource.choose_file.help"),
                     js=f"() => getElement({self.uploader.id}).$refs.qRef.pickFiles()")()

    def _takes(self) -> str:
        return t("manager.mediasource.takes", label=self.label,
                 extensions=", ".join(self.accept))

    def _reading(self) -> None:
        self._reading_note = ui.notification(t("manager.uploads.reading"), spinner=True,
                                             timeout=None)

    def _read(self) -> None:
        if self._reading_note is not None:
            self._reading_note.dismiss()
            self._reading_note = None

    def _failed(self) -> None:
        self._read()
        ui.notify(t("manager.uploads.not_work"), type="negative")

    def _refused(self) -> None:
        ui.notify(self._takes(), type="warning")

    def _dropped_wrong(self, event: Any) -> None:
        said = event.args or {}
        if said.get("folder") and self.accept:
            ui.notify(self._takes(), type="warning")
        elif int(said.get("count") or 0) == 0:
            ui.notify(t("manager.uploads.empty"), type="warning")
        else:
            ui.notify(t("manager.mediasource.one_file"), type="warning")

    async def _arrived(self, event: Any) -> None:
        try:
            data = await event.file.read()
        finally:
            self._read()
        name = event.file.name
        self.choose(self.entry(_dropped_card(name, data), partial(self.took, name, data),
                               filename=name, arrives=True))


class _OnlineTab(_Sources):
    """A picker with an Online tab, drawn by `picker_online.Online` for the kind it fills."""

    built_in = False
    glyph = ""
    family = ""
    context: dict[str, Any]
    kind: str
    # What an extension source calls this slot, which differs from `kind` where a media
    # kind and an asset kind share a name.
    source_kind: str
    game_id: str
    listing: picker_online.Listing
    view: picker_online.Online

    def setup_online(self, media: bool, *, first_page: bool = True) -> None:
        """Decide whether there is an Online tab and give it its view. Needs `kind` and
        `source_kind` set."""
        self.listing = picker_online.listing_for(self.kind, media)
        self.built_in = bool(self.listing.manifest)
        self.online = first_page and (self.listing.offered()
                                      or self.library.offers_online(self.source_kind))
        self.view = picker_online.Online(self)
        # The VPS file whose page the person was sent to, which the next file to arrive is
        # matched to. Taken when that file is picked, so a later pick from a list is not.
        self.vps_for: picker_online.VpsPick | None = None

    async def online_tab(self, body: ui.column) -> None:
        await self.view.draw(body)

    def choose(self, entry: _Entry) -> None:
        if not self.committing:
            self.vps_for = self.view.pick if entry.arrives and self.pane.trip is not None \
                else None
        super().choose(entry)

    async def take_offer(self, offer: dict[str, Any], source_name: str, vps_id: str) -> None:
        """Save a file a built-in catalog has. Only a slot has somewhere to put one."""
        raise NotImplementedError

    async def finish_plain(self, message: str) -> None:
        await _Sources.finish(self, message)

    @property
    def destination(self) -> str:
        """The table a picked file is saved for; "" is the game's own, which is also all a
        dialog with no such choice can mean."""
        return ""


class _Slot(_OnlineTab, _OneFile):
    """A media or asset slot: one file, under the slot's own name."""

    def __init__(self, context: dict[str, Any], kind: str, label: str,
                 done: Callable, target: _Target, current: str = "", *,
                 records: bool = False, first: str = "",
                 backglass_files: Sequence[dict[str, Any]] = (), page: int = 1) -> None:
        super().__init__(context["library"], label, done, target.accept,
                         _on_host(context, current))
        self.target = target
        # A page past 1 follows page 1 to whichever tier holds it, so there is no name
        # to choose and the dialog has no For line.
        self.page = page
        self.kind = kind
        self.source_kind = source_registry.file_kind(kind) if target.file else kind
        self.family = target.family
        self.setup_online(not target.file, first_page=page == 1)
        self.records = records
        self.backglass_files = list(backglass_files)
        self.backglass = bool(self.backglass_files)
        self.first = first
        self.context = context
        self.game_id = context["game_id"]
        self.table_id = context["lens"]
        # Where a file could land, and which of those is chosen.
        self.placements: list[dict[str, Any]] = []
        self.placed_at: dict[str, Any] | None = None
        self.where: Any = None

    def memory_kind(self) -> str:
        return self.source_kind

    def kinds_word(self) -> str:
        return kind_words.plural(self.kind, not self.target.file)

    def title(self) -> str:
        return t("manager.mediasource.choose_for",
                 kind=kind_words.with_article(self.kind, not self.target.file),
                 name=str((self.context.get("game") or {}).get("name") or ""))

    def verb_label(self) -> str:
        return t("manager.mediasource.use_this", kind=self.label)

    def tall(self) -> bool:
        # Imported here: the media map imports the dialogs that open this one.
        from manager.library import mediamap
        return mediamap.TILE_AR.get(self.kind, 1.6) < 1

    def now_card(self) -> pick_pane.Card | None:
        if not self.current:
            return None
        name = _file_name(self.current)
        if self.target.file:
            return pick_pane.card_for("", "", name)
        return pick_pane.card_for(
            self.target.family,
            art.media(self.game_id, self.kind, self.table_id, size=art.PANEL, page=self.page),
            name)

    def opened(self, extra: ui.column) -> None:
        self.pane.backs_up(bool(self.current))

        async def start() -> None:
            if self.page > 1:
                return
            # Read before anything else: the destination is the decision every tab
            # feeds, and a picker that appears after the first file is chosen has come
            # too late.
            await self.load_placements()
            if not self.shown():
                return
            with extra:
                self._destination_row()

        busy.fill(extra, start)

    async def load_placements(self) -> None:
        """Where a file could land here, so it can be chosen rather than inferred."""
        try:
            body = await offload.io(self.target.placements, self.game_id, self.kind)
        except Exception:
            logger.debug("No placements for %s", self.kind, exc_info=True)
            return
        self.placements = list(body.get("placements") or [])
        # The lens preselects rather than decides. Looking at one build is a good guess
        # that art is for that build, and a guess is all it should be.
        wanted = next((item for item in self.placements
                       if item.get("table") == (self.table_id or "")), None)
        self.placed_at = wanted or (self.placements[0] if self.placements else None)

    @property
    def destination(self) -> str:
        """The table id whatever arrives should be named for, "" for the shared name."""
        if self.page > 1:
            return self.table_id or ""
        return str((self.placed_at or {}).get("table") or "")

    def _of_page(self) -> dict[str, int]:
        """The page the calls name, only where it is not the first: the asset calls take
        none."""
        return {"page": self.page} if self.page > 1 else {}

    def _destination_row(self) -> None:
        """Who sees the file: the whole game or this table only. Two placements are a
        choice; one is not, so nothing is drawn."""
        if len(self.placements) < 2:
            self._describe_placement()
            return
        names = {str(item.get("table") or ""): t("manager.picker.this_table"
                                                 if item.get("table")
                                                 else "manager.picker.game")
                 for item in self.placements}
        with ui.column().classes("w-full gap-1 manager-destination"):
            ui.label(t("manager.picker.applies_to")).classes("manager-pair-label")
            self.where = ui.radio(
                names, value=self.destination,
                on_change=lambda event: self._choose_placement(str(event.value or ""))) \
                .props("inline dense")
        self._describe_placement()

    def _choose_placement(self, table: str) -> None:
        self.placed_at = next((item for item in self.placements
                               if item.get("table") == (table or "")), self.placed_at)
        self._describe_placement()

    def choose(self, entry: _Entry) -> None:
        super().choose(entry)
        self._describe_placement()

    def choose_trip(self, trip: pick_pane.Trip | None, row: Any) -> None:
        super().choose_trip(trip, row)
        self._describe_placement()

    def _file_name(self) -> str:
        """The name the pick would be saved under: the placement's base and the picked file's
        extension, which the file decides. Empty until a pick names a file with one."""
        base = str((self.placed_at or {}).get("base") or "")
        picked = self.pending.filename if self.pending is not None else ""
        extension = PurePath(picked).suffix.lower()
        return base + extension if base and re.fullmatch(r"\.[a-z0-9]{1,5}", extension) else ""

    def _describe_placement(self) -> None:
        """Say what name it gets, and whether it takes a place."""
        chosen = self.placed_at or {}
        going = list(chosen.get("displaces") or [])
        self.pane.save_as(self._file_name() if self.pending is not None else "")
        self.pane.backs_up(bool(going) or bool(self.current and not self.placements))

    async def finish(self, message: str) -> None:
        await super().finish(self.said_where(message))

    def said_where(self, message: str) -> str:
        """Say where, including when "where" is not what the panel behind is showing.

        A file saved for one table while the shared media is in view changes nothing
        on screen. The write worked and the panel is right; without a word about it
        the pair reads as a failure.
        """
        if self.page > 1:
            return message
        chosen = self.placed_at or {}
        where = (t("manager.mediasource.for",
                   table=_trimmed_stem(str(chosen.get("label") or "")))
                 if self.destination else t("manager.mediasource.for_all"))
        if not self._in_view():
            where = t("manager.mediasource.off_view", where=where)
        return t("manager.mediasource.saved_where", message=message, where=where)

    def _in_view(self) -> bool:
        """Whether the panel behind shows what was just saved. A table missing from the
        placements has no name apart from the folder's."""
        if self.destination == (self.table_id or ""):
            return True
        own = next((item for item in self.placements
                    if item.get("table") == self.table_id), None)
        return not self.destination and not (own or {}).get("displaces")

    @on_page
    async def take_offer(self, offer: dict[str, Any], source_name: str, vps_id: str) -> None:
        # Held: an ongoing notification never times out, so one nothing dismisses
        # outlives the answer it was waiting for.
        fetching = ui.notification(
            t("manager.mediasource.fetching", source_name=source_name),
            spinner=True, timeout=None)
        try:
            await run.io_bound(self.library.fetch_media, self.game_id, self.destination,
                               self.kind, offer["source"], vps_id,
                               str(offer.get("size") or ""))
        except Exception as exc:
            ui.notify(t("manager.mediasource.fetch_failed"), caption=why(exc),
                      type="negative")
            return
        finally:
            fetching.dismiss()
        await self.finish(t("manager.mediasource.saved_2", label=self.label,
                            source_name=source_name))

    @on_page
    async def took(self, name: str, data: bytes) -> None:
        try:
            placed = await run.io_bound(partial(
                self.target.place, self.game_id, self.destination, self.kind, name, data,
                **self._of_page()))
        except Exception as exc:
            ui.notify(t("manager.mediasource.place_failed"), caption=why(exc),
                      type="negative")
            return
        await self._matched(placed)
        await self.finish(t("manager.mediasource.label_saved", label=self.label))

    @on_page
    async def _matched(self, placed: Any) -> None:
        """Match the saved file to the VPS file whose page the person was sent to, if there
        was one. The file is saved either way, so a match that fails is said and the save
        stands."""
        pick = self.vps_for
        saved = str(placed.get("written") or "") if isinstance(placed, dict) else ""
        if pick is None or not saved:
            return
        key = saved if self.target.file else f"{MEDIA_FOLDER}/{saved}"
        try:
            await run.io_bound(self.library.set_asset_source, self.game_id, key, pick.file_id)
        except Exception as exc:  # noqa: BLE001
            ui.notify(t("manager.picker.match_failed", kind=self.view.vps_kind()),
                      caption=why(exc), type="warning")

    @on_page
    async def use_file(self, path: str) -> None:
        try:
            data = await self.contents(path)
        except Exception as exc:
            ui.notify(t("manager.mediasource.import_failed"), caption=why(exc),
                      type="negative")
            return
        self.remember(path)
        await self.took(_file_name(path), data)

    def file_row(self, item: dict[str, Any]) -> None:
        path, name = item["path"], item["name"]
        src = self.preview(path)
        taken = self.entry(pick_pane.card_for(self.target.family, src, name),
                           partial(self.use_file, path), filename=name)
        used, here = self._in_use(path, name)
        taken.bind(candidates.row(
            src, name, "", used, taken.pick, family=self.target.family,
            line=not self.target.family, use=taken.use, tag_here=here))

    def _in_use(self, path: str, name: str) -> tuple[str, bool]:
        """What this file already is for the game, and whether that is the kind being
        chosen.

        In a game's own folder most files are already somebody's, and the interesting
        ones are the strays. Said per row rather than by hiding the rest, so the folder
        still looks like the folder.
        """
        if path == self.current:
            return t("manager.mediasource.used_as", kind=self.label), True
        for kind, entry in (self.context.get("media") or {}).items():
            if entry.get("file") == name:
                return (t("manager.mediasource.used_as",
                          kind=media_label_map().get(kind, kind)), kind == self.kind)
        return "", False

    # --- the backglass file's own picture -----------------------------------------

    async def backglass_tab(self, body: ui.column) -> None:
        """The picture each of the game's backglass files draws, as a row apiece."""
        with body:
            for offer in self.backglass_files:
                self._backglass_candidate(offer)

    def _backglass_candidate(self, offer: dict[str, Any]) -> None:
        @on_page
        async def draw() -> None:
            try:
                await run.io_bound(self.library.draw_backglass_picture, self.game_id,
                                   self.destination, str(offer.get("asset_kind") or ""))
            except Exception as exc:  # noqa: BLE001 - the notice says why
                ui.notify(t("manager.mediasource.place_failed"), caption=why(exc),
                          type="negative")
                return
            await self.finish(t("manager.mediasource.label_saved", label=self.label))

        def src(size: int) -> str:
            return art.backglass(self.game_id, str(offer.get("path") or ""),
                                 version=offer.get("version"), size=size,
                                 grill=bool(offer.get("grill")))

        name = str(offer.get("file") or "")
        detail = t("manager.mediasource.backglass_grill") if offer.get("grill") else ""
        taken = self.entry(pick_pane.card_for("image", src(art.PANEL), name, detail), draw)
        taken.bind(candidates.row(src(art.CELL), name, detail, "", taken.pick,
                                  use=taken.use, peek=src(art.PANEL)))

    # --- recorded on this device --------------------------------------------------

    async def record_tab(self, body: ui.column) -> None:
        """This kind recorded on its own: Record and what it made, then the settings."""
        library = self.library
        try:
            report, schema, values, playing = await asyncio.gather(
                offload.io(library.capture_report), offload.io(library.config_schema),
                offload.io(library.config_values), offload.io(library.play_state))
        except Exception as exc:  # noqa: BLE001 - the tab says why
            if self.shown():
                with body:
                    panel.line(t("manager.record.could_not_read"), hint=why(exc))
            return
        if not self.shown():
            return
        held = record.Settings(record.options_of(schema),
                               dict((values or {}).get(recording.SECTION) or {}))
        running = bool((playing or {}).get("launching"))
        now: dict[str, Any] = {"asked": 0, "plan": {}, "stop": "", "busy": False,
                              "looking": False}
        with body:
            top = ui.column().classes("w-full gap-2")
            made = ui.column().classes("w-full gap-1")
            tuning = ui.column().classes(
                "w-full gap-0 manager-source-offers manager-record-settings")

        def draw_top() -> None:
            top.clear()
            with top:
                if now["stop"]:
                    _attention(now["stop"])
                with ui.row().classes("items-center w-full no-wrap min-w-0 manager-source-head"):
                    panel.action(t("manager.record.record"), go, icon=verbs.RECORD,
                                 enabled=not now["stop"] and not now["busy"],
                                 hint=t("manager.mediasource.record_this.help"))()
                    if now["plan"].get("recording"):
                        ui.label(record.about(int(now["plan"].get("estimate_seconds") or 0))) \
                            .classes("manager-help")

        async def replan() -> None:
            now["asked"] += 1
            asked = now["asked"]
            try:
                plan = await offload.io(library.plan_capture, {
                    **record.target(self.game_id, self.destination), "kinds": [self.kind],
                    "review": True, "settings": dict(held.changed)})
                stop = record.stopped(report, (plan.get("kinds") or [{}])[0], running)
            except Exception as exc:  # noqa: BLE001 - the refusal is what stops it
                plan, stop = {}, why(exc)
            if asked == now["asked"] and self.shown():
                now["plan"], now["stop"] = plan, stop
                draw_top()

        def draw_tuning() -> None:
            tuning.clear()
            with tuning:
                panel.facts(ui, record.setting_rows(held, report, replan, draw_tuning))

        async def look_again() -> None:
            """Screens are read as the tab opens; one woken since is seen on the next
            read. A failed read leaves the old answer standing."""
            nonlocal report
            if report.get("available") or now["looking"] or now["busy"] or not self.shown():
                return
            now["looking"] = True
            try:
                report = await offload.io(library.capture_report)
            except Exception:  # noqa: BLE001 - tried again on the next tick
                return
            finally:
                now["looking"] = False
            if report.get("available"):
                draw_tuning()
            await replan()

        @on_page
        async def go() -> None:
            wanted = {**record.wanted(self.game_id, self.destination, FILL, [self.kind],
                                      {self.kind: True}, held.changed), "review": True}
            try:
                job = await offload.io(library.start_capture, wanted)
            except Exception as exc:  # noqa: BLE001 - said, and nothing was recorded
                ui.notify(t("manager.record.could_not_start"), caption=why(exc),
                          type="warning")
                return
            watch = (self.context.get("state") or {}).get("watch_jobs")
            if callable(watch):
                watch()
            now["busy"] = True
            if self.shown():
                self.dialog.props("persistent")
                draw_top()
                made.clear()
                with made:
                    panel.line(
                        record.recording_for(int(now["plan"].get("estimate_seconds") or 0)))
                with busy.held(made):
                    ran = await record.ended(library, str(job.get("id") or ""))
            else:
                ran = await record.ended(library, str(job.get("id") or ""))
            now["busy"] = False
            if not self.shown():
                await record.say(library, ran, self.done)
                return
            self.dialog.props(remove="persistent")
            draw_top()
            await review(ran)

        async def review(ran: dict[str, Any]) -> None:
            made.clear()
            kept = {str(one.get("id") or "") for one in ran.get("proposed") or []}
            if kept:
                try:
                    listing = await offload.io(library.capture_proposals)
                except Exception as exc:  # noqa: BLE001 - it still waits on the device
                    if self.shown():
                        with made:
                            panel.line(t("manager.record.waiting_failed"), hint=why(exc))
                    return
                if not self.shown():
                    return
                row = next((one for one in listing.get("proposals") or []
                            if one.get("id") in kept), None)
                if row is not None:
                    self._offer_recording(row)
                    return
            mine = [one for one in ran.get("failed") or [] if one.get("kind") == self.kind]
            if mine:
                said = record.failed_because(mine[0])
            elif "error" in ran:
                said = t("manager.record.said_detail",
                         said=t("manager.record.could_not_record"), detail=str(ran["error"]))
            else:
                said, _level, caption = record.outcome(ran)
                said = caption or said
            with made:
                _attention(said)

        draw_tuning()
        await replan()
        if not report.get("available"):
            with body:
                ui.timer(_RECHECK_S, look_again)

    def _offer_recording(self, row: dict[str, Any]) -> None:
        """A finished recording becomes the pick, for the verb to use. One nobody uses
        stays waiting on the device, as it does for the page that lists them."""
        @on_page
        async def use() -> None:
            try:
                await offload.io(self.library.use_proposal, str(row["id"]), True)
            except Exception as exc:  # noqa: BLE001 - it still waits on the device
                ui.notify(t("manager.record.could_not_use"), caption=why(exc),
                          type="negative")
                return
            await self.finish(t("manager.mediasource.label_saved", label=self.label))

        self.choose(self.entry(
            pick_pane.card_for(self.target.family, str(row.get("url") or ""),
                               str(row.get("file") or ""),
                               record.size(int(row.get("size") or 0))), use))


def _list_file(sources: _Sources, item: dict[str, Any], commit: Callable[[], Any],
               glyph: str, *, said: str = "", here: bool = False) -> _Entry:
    """A file of a host folder as a list row that picks it, with its name and size on the
    card New shows."""
    name, size = item["name"], _size(item.get("size_bytes"))
    taken = sources.entry(pick_pane.Card(name=name, shape=pick_pane.FILE, detail=size,
                                         glyph=glyph), commit, filename=name)
    taken.bind(candidates.row("", name, size, said, taken.pick, family="", glyph=glyph,
                              line=True, use=taken.use, tag_here=here))
    return taken


def _keep_it(copies: bool, chosen: Callable[[bool], None]) -> None:
    """How a table file is kept, as radios with their descriptions: the descriptions are
    what the choice is made on. `chosen` hears each pick, the first included."""
    marks: dict[bool, Any] = {}

    def pick(copying: bool) -> None:
        chosen(copying)
        for key, mark in marks.items():
            mark.props(f'name={"radio_button_checked" if key == copying else
                                "radio_button_unchecked"}')
            mark.classes(replace="manager-placement-mark"
                         + (" manager-placement-mark--on" if key == copying else ""))

    with ui.column().classes("w-full gap-0 manager-destination"):
        ui.label(t("manager.mediasource.keep_it")).classes("manager-pair-label")
        for option, name, help_ in (
                (True, t("manager.mediasource.copy_in"),
                 t("manager.mediasource.copy_in.help")),
                (False, t("manager.mediasource.use_where"),
                 t("manager.mediasource.use_where.help"))):
            row = ui.row().classes("items-start gap-2 w-full no-wrap manager-placement")
            with row:
                marks[option] = ui.icon("radio_button_unchecked") \
                    .classes("manager-placement-mark")
                with ui.column().classes("gap-0 min-w-0 grow"):
                    ui.label(name).classes("manager-placement-name")
                    ui.label(help_).classes("manager-help")
            row.on("click", lambda c=option: pick(c))
    pick(copies)


class _ManyFiles(_Sources):
    """A dialog whose drop and file button take files or a folder. What arrives is the pick:
    New shows it and the verb imports it, so an upload nobody goes on to use is given back."""

    # Whether the file button takes several files, or one with a folder behind its caret.
    several = True
    _heard: Callable[[Any], None] | None = None

    def _hear(self) -> Callable[[Any], None]:
        """One listener per dialog, made on first use: it holds the client it is made in."""
        if self._heard is None:
            self._heard = uploads.listener(self.arrived)
        return self._heard

    def drops(self, card: Any) -> None:
        card.on("drop", self._hear(), js_handler=_MANY)

    def take_button(self) -> None:
        _choose(self.several, self._hear())

    def drop_card(self, drop: uploads.Drop) -> pick_pane.Card:
        if drop.count > 1:
            return pick_pane.Card(name=drop.name, shape=pick_pane.FOLDER,
                                  detail=t("manager.mediasource.n_files", count=drop.count))
        return pick_pane.Card(name=drop.name, shape=pick_pane.FILE)

    async def arrived(self, drop: uploads.Drop) -> None:
        if not self.shown():
            self._give_back(drop.upload_id)
            return
        self.choose(self.entry(self.drop_card(drop), partial(self.use_drop, drop),
                               discard=partial(self._give_back, drop.upload_id), arrives=True))

    async def use_drop(self, drop: uploads.Drop) -> None:
        raise NotImplementedError

    def _give_back(self, upload_id: str) -> None:
        background_tasks.create(run.io_bound(self.library.abort_upload, upload_id))

    def folder_card(self, path: str) -> pick_pane.Card:
        return pick_pane.Card(name=_file_name(path), shape=pick_pane.FOLDER)


class _Folder(_OnlineTab, _ManyFiles):
    """A kind that arrives as many files - a PUP pack, a color set, a sound bank, music."""

    drop_key = "manager.mediasource.drop_files"

    def __init__(self, context: dict[str, Any], kind: str, label: str,
                 done: Callable, current: str = "") -> None:
        super().__init__(context["library"], label, done, _on_host(context, current))
        self.context = context
        self.kind = kind
        self.source_kind = kind
        self.several = kind not in _ONE_OR_FOLDER
        self.setup_online(False)
        self.game_id = context["game_id"]
        self.game_name = str(context["game"].get("name") or "")
        self.game_dir = str(context["game"].get("folder") or "")
        specs = specs_named(kind)
        self.extensions = {extension for spec in specs for extension in spec.extensions}
        self.glyph = specs[0].icon
        # The kind an import is held to, or "" for whatever the drop brought.
        self.narrows_to = kind
        self.adds_table = False
        self.reads_names = True
        self._busy = False

    def memory_kind(self) -> str:
        return self.kind

    def kinds_word(self) -> str:
        return kind_words.plural(self.kind)

    def title(self) -> str:
        return t("manager.mediasource.choose_for", kind=kind_words.with_article(self.kind),
                 name=self.game_name)

    def verb_label(self) -> str:
        return t("manager.mediasource.use_this", kind=self.label)

    def empty_words(self) -> str:
        return t("manager.pane.nothing_yet")

    def now_card(self) -> pick_pane.Card | None:
        if not self.current:
            return None
        return pick_pane.Card(name=_file_name(self.current), shape=pick_pane.FILE,
                              glyph=self.glyph)

    def starts(self) -> list[dict[str, Any]]:
        return self.library.disk_starts(self.game_id)

    def suffixes(self) -> tuple[str, ...]:
        return (*self.extensions, *ARCHIVE_EXTENSIONS)

    def fits(self, item: dict[str, Any]) -> bool:
        return _suffix(item) in ARCHIVE_EXTENSIONS or _suffix(item) in self.extensions

    async def use_file(self, path: str) -> None:
        await self._take(path)

    def folder_entry(self, path: str) -> _Entry | None:
        return self.entry(self.folder_card(path), partial(self._take, path))

    def _opens_at(self, starts: list[dict[str, Any]]) -> str:
        """Beside what fills this, so its own row is on screen."""
        return folder_picker.enclosing(self.current) if self.current \
            else super()._opens_at(starts)

    def file_row(self, item: dict[str, Any]) -> None:
        archive = _suffix(item) in ARCHIVE_EXTENSIONS
        here = item["path"] == self.current
        _list_file(self, item, partial(self._take, item["path"]),
                   "folder_zip" if archive else self.glyph,
                   said=t("manager.mediasource.used_as", kind=self.label) if here else "",
                   here=here)

    async def use_drop(self, drop: uploads.Drop) -> None:
        await self._import(drop.upload_id, drop.name, drop.count)

    @on_page
    async def _take(self, path: str) -> None:
        try:
            upload_id = await offload.io(self.library.upload_from_path, path)
        except Exception as exc:  # noqa: BLE001
            ui.notify(t("manager.uploads.read_failed"), caption=why(exc), type="negative")
            return
        self.remember(path)
        await self._import(upload_id, _file_name(path))

    @on_page
    async def _import(self, upload_id: str, source: str, file_count: int = 0) -> None:
        if self._busy:
            ui.notify(t("manager.uploads.busy"), type="warning")
            await run.io_bound(self.library.abort_upload, upload_id)
            return
        self._busy = True
        try:
            release = self.view.release(source) \
                if self.online and self.listing.offered() else None
            await uploads.arrived(
                self.library, upload_id, source=source, reads_names=self.reads_names,
                file_count=file_count, on_done=self._imported, game_id=self.game_id,
                game_dir=self.game_dir, asset_kind=self.narrows_to,
                add_table=self.adds_table, release=release,
                on_imported=picker_online.matched_after(
                    self.library, self.game_id, self.game_dir, self.listing, release)
                if release else None)
        finally:
            self._busy = False

    async def _imported(self) -> None:
        if self.dialog is not None:
            self.dialog.close()
        await self.done()


_TEXT_NOTES = (".txt", ".md")
# How much of a notes file is read to show its first lines.
_TEXT_READ = 4000


class _Notes(_Folder):
    """Notes files for a game. A game may have several, so there is nothing to compare
    against and New stands alone; the folder lists everything, with what is not notes dimmed."""

    drop_key = "manager.mediasource.drop_file"
    pane_alone = True

    def __init__(self, context: dict[str, Any], label: str, done: Callable) -> None:
        super().__init__(context, "readme", label, done)
        self.reads_names = False
        self.several = False
        self.accept = tuple(DOC_FAMILY)

    def verb_label(self) -> str:
        return t("manager.mediasource.use_notes")

    def suffixes(self) -> tuple[str, ...]:
        return ()

    def fits(self, item: dict[str, Any]) -> bool:
        return True

    def folder_entry(self, path: str) -> _Entry | None:
        return None

    def take_button(self) -> None:
        panel.action(t("manager.mediasource.choose_file"), self._hear(), icon=verbs.FROM_FILE,
                     hint=t("manager.mediasource.choose_file.help"),
                     js="() => window.__managerChoose(false, emit, true)")()

    def file_row(self, item: dict[str, Any]) -> None:
        if _suffix(item) not in DOC_FAMILY:
            candidates.row("", item["name"], "", "", lambda: None, family="",
                           glyph=self.glyph, line=True) \
                .classes("manager-source-row--dim")
            return
        taken = _list_file(self, item, partial(self._take, item["path"]), self.glyph)
        if _suffix(item) in _TEXT_NOTES:
            taken.row.on("click", lambda: background_tasks.create(self._show_text(taken, item)))

    async def _show_text(self, taken: _Entry, item: dict[str, Any]) -> None:
        """The first lines of the picked notes, in place of a file card, once read."""
        try:
            data = await offload.io(self.library.disk_file, item["path"], self.accept)
        except Exception:  # noqa: BLE001 - the file card stays
            return
        text = data[:_TEXT_READ].decode("utf-8", errors="replace")
        taken.card = pick_pane.Card(name=item["name"], shape=pick_pane.TEXT, text=text,
                                    detail=_size(item.get("size_bytes")))
        if self.pending is taken and self.shown():
            self.pane.show(taken.card)


class _Table(_Folder):
    """A table for a game: taken from the device as a copy or where it is, or an ID its
    launcher starts it from."""

    keyed = True
    drop_key = "manager.mediasource.drop_table"
    pane_alone = True

    def __init__(self, context: dict[str, Any], done: Callable) -> None:
        super().__init__(context, "table", t("asset.kind.table.label"), done)
        self.narrows_to = ""
        self.adds_table = True
        self.copies = True

    def title(self) -> str:
        return t("manager.mediasource.add_table", name=self.game_name)

    def verb_label(self) -> str:
        return t("manager.mediasource.table_verb")

    def now_card(self) -> pick_pane.Card | None:
        return None

    def starts(self) -> list[dict[str, Any]]:
        return [root for root in self.library.disk_starts(self.game_id)
                if root.get("source") != "game"]

    def suffixes(self) -> tuple[str, ...]:
        return tuple(apps.table_suffixes())

    async def use_file(self, path: str) -> None:
        await self._chosen(path)

    def fits(self, item: dict[str, Any]) -> bool:
        inside = _under(self.game_dir, str(item.get("path") or ""))
        return _suffix(item) in self.extensions and not inside

    def folder_entry(self, path: str) -> _Entry | None:
        return None

    def file_row(self, item: dict[str, Any]) -> None:
        _list_file(self, item, partial(self._chosen, item["path"]), self.glyph)

    def choices(self) -> None:
        _keep_it(self.copies, lambda copies: setattr(self, "copies", copies))

    @on_page
    async def _chosen(self, path: str) -> None:
        adds = (self.library.import_table_file if self.copies
                else self.library.add_referenced_table)
        try:
            made = await offload.io(adds, self.game_id, path)
        except Exception as exc:  # noqa: BLE001
            ui.notify(t("said.add_failed"), caption=why(exc), type="negative")
            return
        self.remember(path)
        pick = self.vps_for
        table = str(made.get("id") or "") if isinstance(made, dict) else ""
        if pick is not None and table:
            try:
                await run.io_bound(self.library.set_table_source, self.game_id, table,
                                   pick.file_id)
            except Exception as exc:  # noqa: BLE001
                ui.notify(t("manager.picker.match_failed", kind=self.view.vps_kind()),
                          caption=why(exc), type="warning")
        await self._imported()

    async def keyed_tab(self, body: ui.column) -> None:
        body.clear()
        try:
            found = await offload.io(self.library.launchers)
            apps = await offload.io(self.library.launch_apps)
        except Exception as exc:  # noqa: BLE001
            with body:
                panel.line(t("manager.launchers.launchers_failed"), hint=why(exc))
            return
        takes_ids = {str(app.get("id") or "") for app in apps if app.get("accepts_keys")}
        offered = [one for one in found.get("launchers") or []
                   if str(one.get("app") or "") in takes_ids and one.get("enabled", True)]
        defaults = dict(found.get("defaults") or {})
        with body:
            ui.label(t("manager.mediasource.without_file.help")).classes("manager-help")
            if not offered:
                ui.label(t("manager.mediasource.launcher_none")) \
                    .classes("manager-help")
                with ui.row().classes("items-center gap-2 w-full manager-slot-actions"):
                    panel.action(t("manager.mediasource.add_launcher"),
                                 lambda: self._add_launcher(sorted(takes_ids)),
                                 icon=verbs.ADD,
                                 hint=t("manager.mediasource.add_launcher.help"))()
                return
            first = next((one for one in offered
                          if defaults.get(one.get("app")) == one.get("launcher_id")),
                         offered[0])
            chosen = {"launcher": first}
            by_id = {str(one["launcher_id"]): one for one in offered}
            held: dict[str, Any] = {}

            def draw_id() -> None:
                held["typed"] = frame.field(placeholder=t("manager.mediasource.id_example"))

            rows: list[tuple[Any, Any]] = []
            if len(offered) > 1:
                rows.append((t("manager.workbench.launcher"), panel.select(
                    {key: str(one.get("display_name") or key) for key, one in by_id.items()},
                    str(first["launcher_id"]),
                    lambda event: chosen.update(launcher=by_id[str(event.value)]))))
            rows.append((t("word.id"), draw_id))
            panel.facts(ui, rows)

            @on_page
            async def add() -> None:
                said = str(held["typed"].value or "").strip()
                if not said:
                    held["typed"].props["error"] = True
                    held["typed"].props["error-message"] = \
                        t("manager.mediasource.blank_id")
                    held["typed"].update()
                    return
                launcher = chosen["launcher"]
                app = str(launcher.get("app") or "")
                try:
                    made = await offload.io(self.library.add_keyed_table, self.game_id,
                                            app, said)
                    if defaults.get(app) != launcher.get("launcher_id"):
                        await offload.io(self.library.assign_launcher,
                                         str(made.get("id") or ""),
                                         str(launcher["launcher_id"]))
                except Exception as exc:  # noqa: BLE001
                    ui.notify(t("said.add_failed"), caption=why(exc), type="negative")
                    return
                await self._imported()

            with ui.row().classes("items-center gap-2 w-full manager-slot-actions"):
                panel.action(t("manager.mediasource.table_verb"), add, icon=verbs.ADD,
                             hint=t("manager.mediasource.add_keyed_table.help"))()
            held["typed"].on("keydown.enter", add)

    @on_page
    async def _add_launcher(self, apps: list[str]) -> None:
        """A launcher for the program that takes an ID, opened on the Launchers page to
        be named and pointed at its program."""
        from common.games import launchers as model

        wanted = "/manager?" + deeplink.query({"view": "launchers"})
        if len(apps) == 1:
            made = model.mint_launcher_id()
            name = next((str(one.get("name") or "") for one in
                         await offload.io(self.library.launch_apps)
                         if one.get("id") == apps[0]), apps[0])
            try:
                await offload.io(self.library.put_launcher, made,
                                 {"app": apps[0], "display_name": name, "enabled": True,
                                  "settings": {}})
            except Exception as exc:  # noqa: BLE001
                ui.notify(t("said.add_failed"), caption=why(exc), type="negative")
                return
            wanted = "/manager?" + deeplink.query({"view": "launchers", "launcher": made})
        self.dialog.close()
        ui.navigate.to(wanted)


class _Library(_ManyFiles):
    """The Add dialog of a library page. What it takes goes where a file dropped on the
    page's empty space goes: a table makes a game, anything else asks which game."""

    drop_key = "manager.mediasource.drop_files"
    pane_alone = True

    def __init__(self, library: Any, name: str, took: Callable[[uploads.Drop], Any], *,
                 tables: bool, referenced: Callable[[str], Any] | None = None,
                 find: tuple[str, Callable[[], Any]] | None = None) -> None:
        super().__init__(library, name, took)
        self.took = took
        # Whether a table file from the device may stay where it is.
        self.tables = tables
        self.referenced = referenced
        self.find = find
        self.copies = True
        self.extensions = {*(ext for spec in ASSET_SPECS for ext in spec.extensions),
                           *DOC_FAMILY}

    def title(self) -> str:
        return self.label.removesuffix("…")

    def verb_label(self) -> str:
        return t("manager.mediasource.table_verb" if self.tables
                 else "manager.mediasource.file_verb")

    def memory_kind(self) -> str:
        return "library"

    def kinds_word(self) -> str:
        return t("manager.mediasource.library_files")

    def nothing_here(self) -> str:
        return t("manager.add.nothing_here")

    def starts(self) -> list[dict[str, Any]]:
        return self.library.disk_starts("")

    def suffixes(self) -> tuple[str, ...]:
        return (*sorted(self.extensions), *sorted(ARCHIVE_EXTENSIONS))

    def fits(self, item: dict[str, Any]) -> bool:
        return _suffix(item) in ARCHIVE_EXTENSIONS or _suffix(item) in self.extensions

    def before(self) -> None:
        if self.find is None:
            return
        words, go = self.find
        link = ui.label(words).classes("manager-link cursor-pointer")
        link.on("click", lambda: self._leave(go))

    async def _leave(self, go: Callable[[], Any]) -> None:
        self.dialog.close()
        answer = go()
        if inspect.isawaitable(answer):
            await answer

    def choices(self) -> None:
        if self.tables and self.referenced is not None:
            _keep_it(self.copies, lambda copies: setattr(self, "copies", copies))

    def folder_entry(self, path: str) -> _Entry | None:
        return self.entry(self.folder_card(path), partial(self._take, path))

    async def use_file(self, path: str) -> None:
        await self._chosen(path)

    def file_row(self, item: dict[str, Any]) -> None:
        archive = _suffix(item) in ARCHIVE_EXTENSIONS
        glyph = "folder_zip" if archive else icons.TABLES \
            if _suffix(item) in apps.table_suffixes() else verbs.FROM_FILE
        _list_file(self, item, partial(self._chosen, item["path"]), glyph)

    async def use_drop(self, drop: uploads.Drop) -> None:
        self.dialog.close()
        await self.took(drop)

    @on_page
    async def _chosen(self, path: str) -> None:
        """A table that stays where it is makes its game here; anything else is read as a
        drop is."""
        if self.referenced is not None and not self.copies \
                and _suffix({"name": path}) in apps.table_suffixes():
            self.remember(path)
            self.dialog.close()
            await self.referenced(path)
            return
        await self._take(path)

    @on_page
    async def _take(self, path: str) -> None:
        try:
            upload_id = await offload.io(self.library.upload_from_path, path)
        except Exception as exc:  # noqa: BLE001
            ui.notify(t("manager.uploads.read_failed"), caption=why(exc), type="negative")
            return
        self.remember(path)
        await self.use_drop(uploads.Drop(upload_id=upload_id, name=_file_name(path)))


class _Image(_OneFile):
    """A collection's picture."""

    games = True

    def __init__(self, library: Any, name: str, label: str, done: Callable,
                 now: str = "") -> None:
        super().__init__(library, label, done, IMAGE_FAMILY)
        self.name = name
        # Where the browser fetches the picture in use, or "" where there is none.
        self.now = now

    def memory_kind(self) -> str:
        return "picture"

    def kinds_word(self) -> str:
        return t("word.pictures")

    def title(self) -> str:
        return t("manager.mediasource.choose_for_collection", name=self.name)

    def verb_label(self) -> str:
        return t("manager.mediasource.use_this", kind=self.label)

    def now_card(self) -> pick_pane.Card | None:
        return pick_pane.card_for("image", self.now, "") if self.now else None

    @on_page
    async def use_file(self, path: str) -> None:
        try:
            data = await self.contents(path)
        except Exception as exc:  # noqa: BLE001
            ui.notify(t("manager.mediasource.image_failed"), caption=why(exc),
                      type="negative")
            return
        self.remember(path)
        await self.took(_file_name(path), data)

    def file_row(self, item: dict[str, Any]) -> None:
        path, name = item["path"], item["name"]
        src = self.preview(path)
        taken = self.entry(pick_pane.card_for("image", src, name),
                           partial(self.use_file, path), filename=name)
        taken.bind(candidates.row(src, name, "", "", taken.pick, use=taken.use))

    async def games_tab(self, body: ui.column) -> None:
        body.clear()
        try:
            entries = await offload.io(self.library.collection_entries, self.name)
        except Exception as exc:  # noqa: BLE001
            with body:
                panel.line(t("manager.mediasource.games_failed"), hint=why(exc))
            return
        pictures = [(entry, kind) for entry in entries
                    for kind in (entry.get("media") or []) if media_family(kind) == "image"]
        with body:
            if not pictures:
                ui.label(t("manager.mediasource.no_game_pictures")).classes("manager-help")
                return
            games = {str((entry.get("game") or {}).get("id") or "") for entry, _ in pictures}
            ui.label(t("manager.mediasource.pictures_games", count=len(games))) \
                .classes("manager-pair-label")
            with ui.element("div").classes("w-full"):
                find = frame.field(placeholder=t("manager.folder_picker.filter"))
            listing = ui.column().classes("w-full gap-1 manager-source-list")

        def draw() -> None:
            typed = str(find.value or "").strip().casefold()
            found = [(entry, kind) for entry, kind in pictures
                     if typed in str((entry.get("game") or {}).get("name") or "").casefold()]
            listing.clear()
            with listing:
                if not found:
                    ui.label(t("manager.mediasource.find_empty")).classes("manager-help")
                for entry, kind in found:
                    self._picture_row(entry, kind)

        find.on_value_change(draw)
        draw()

    def _picture_row(self, entry: dict[str, Any], kind: str) -> None:
        game = entry.get("game") or {}
        game_id = str(game.get("id") or "")
        table_id = str((entry.get("table") or {}).get("id") or "")
        name = str(game.get("name") or "")
        label = media_label_map().get(kind, kind)
        whole = art.media(game_id, kind, table_id, size=art.PANEL)

        @on_page
        async def take() -> None:
            try:
                data, served = await offload.io(self.library.media_file, game_id, table_id,
                                                kind)
            except Exception as exc:  # noqa: BLE001
                ui.notify(t("manager.mediasource.image_failed"), caption=why(exc),
                          type="negative")
                return
            await self.took(_named_for(name, served), data)

        taken = self.entry(pick_pane.card_for("image", whole, name, label), take,
                           filename=name)
        taken.bind(candidates.row(art.media(game_id, kind, table_id, size=art.CELL), name,
                                  label, "", taken.pick, use=taken.use, peek=whole))

    @on_page
    async def took(self, name: str, data: bytes) -> None:
        try:
            await offload.io(self.library.set_collection_image, self.name, name, data)
        except Exception as exc:  # noqa: BLE001
            ui.notify(t("manager.mediasource.image_failed"), caption=why(exc),
                      type="negative")
            return
        await self.finish(t("manager.mediasource.label_saved", label=self.label))


class _PlayerPicture(_Image):
    """A player's picture: a file from your device or from the host. A player has no
    games to borrow a picture from."""

    games = False

    def __init__(self, library: Any, label: str, save: Callable,
                 services: Sequence[dict[str, Any]] = (),
                 use_service: Callable | None = None, now: str = "") -> None:
        super().__init__(library, "", label, save, now)
        self.services = list(services)
        self.use_service = use_service
        self.online = bool(self.services and use_service)

    def title(self) -> str:
        return t("manager.mediasource.choose_picture")

    async def online_tab(self, body: ui.column) -> None:
        """One button per account whose service has a picture for this player."""
        body.clear()
        with body:
            for service in self.services:
                name = str(service.get("label") or service["extension"])
                panel.action(
                    t("manager.players.picture_from_service", service=name),
                    partial(self._copied, str(service["extension"])),
                    icon=verbs.FROM_ONLINE,
                    hint=t("manager.players.picture_from_service.help", service=name))()

    @on_page
    async def _copied(self, extension: str) -> None:
        assert self.use_service is not None
        try:
            redraw = await self.use_service(extension)
        except Exception as exc:  # noqa: BLE001
            ui.notify(t("manager.mediasource.image_failed"), caption=why(exc),
                      type="negative")
            return
        self.dialog.close()
        await redraw()

    @on_page
    async def took(self, name: str, data: bytes) -> None:
        try:
            redraw = await self.done(name, data)
        except Exception as exc:  # noqa: BLE001
            ui.notify(t("manager.mediasource.image_failed"), caption=why(exc),
                      type="negative")
            return
        self.dialog.close()
        await redraw()


def _attention(said: str) -> None:
    with ui.element("div").classes("manager-attention w-full"):
        ui.icon("error_outline").classes("manager-attention-icon")
        ui.label(said).classes("manager-attention-line")


def _flavor(path: str) -> type[PurePath]:
    """The rules a host's path follows. A path is kept as the host wrote it, so how it
    looks decides, not the platform this runs on."""
    windows = re.match(r"[A-Za-z]:|\\\\", path) or ("\\" in path and "/" not in path)
    return PureWindowsPath if windows else PurePosixPath


def _under(folder: str, path: str) -> bool:
    """Whether `path` is somewhere below `folder`, by the folder's own separators."""
    if not folder:
        return False
    kind = _flavor(folder)
    return kind(path) != kind(folder) and kind(path).is_relative_to(kind(folder))


def _on_host(context: dict[str, Any], path: str) -> str:
    """A path in the game's folder, as the host tab lists it."""
    folder = str(context["game"].get("folder") or "")
    return str(_flavor(folder)(folder) / path) if folder and path else ""


def _named_for(name: str, served: str) -> str:
    return name + (mimetypes.guess_extension(served.split(";")[0].strip()) or "")


def _trimmed_stem(label: str) -> str:
    """The file's name without its extension, short enough to read."""
    stem = label[:-4] if label.lower().endswith(".vpx") else label
    return stem if len(stem) <= 40 else "\u2026" + stem[-39:]


def _file_name(path: str) -> str:
    return re.split(r"[\\/]", path.rstrip("\\/"))[-1]


def _places(starts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The host tab's Places, each named by `_start_name`; two that share a name are told
    apart by their path."""
    named = [{**one, "name": _start_name(one)} for one in starts]
    count: dict[str, int] = {}
    for one in named:
        count[one["name"]] = count.get(one["name"], 0) + 1
    return [{**one, "name": str(one["path"])} if count[one["name"]] > 1 else one
            for one in named]


def _start_name(root: dict[str, Any]) -> str:
    """What to call a starting point. The game's own folder is not named after the
    folder, because the folder's name is the one thing already on screen above it."""
    return (t("manager.mediasource.game_folder") if root.get("source") == "game"
            else str(root.get("name") or root.get("path") or ""))


def records(library: Any, kind: str) -> dict[str, Any] | None:
    """The `capture` capability where this install records and `kind` is a recording's;
    None where it does not."""
    if canonical_kind(kind) not in RECORDABLE:
        return None
    return next((one for one in library.discovery().get("capabilities") or []
                 if one.get("name") == "capture"), None)


def backglass_files(detail: dict[str, Any]) -> list[dict[str, Any]]:
    """The backglass files of a slot's media detail whose picture could fill it, as
    `open_sources` takes them: each with the kind of file it is."""
    return [{**offer, "asset_kind": kind}
            for kind, offer in (("backglass", detail.get("backglass_file")),
                                ("b2spro", detail.get("b2spro_file"))) if offer]


def open_sources(context: dict[str, Any], kind: str, label: str,
                 done: Callable, current: str = "", *, recording_it: bool = False,
                 backglass_files: Sequence[dict[str, Any]] = (),
                 place: str = "", page: int = 1) -> ui.dialog:
    """Open the ways to fill this slot. Returns as soon as the dialog is up.

    `current` is what fills it now, as a path in the game's folder. `recording_it` opens
    it on its Record tab. `backglass_files` are the pictures the game's backglass files
    draw, from `backglass_files`, which add a tab and, on an empty slot, are where the
    dialog opens. `place` is the position in a walk, which makes the way out Skip and Stop.
    `page` is the page of a paged kind the file becomes; `label` then names the page, not
    the kind.
    """
    library = context["library"]
    first = "record" if recording_it else \
        "backglass" if backglass_files and not current and page == 1 else ""
    sources = _Slot(context, kind, label, done, _media(library, kind), current,
                    records=records(library, kind) is not None and page == 1, first=first,
                    backglass_files=backglass_files if page == 1 else (), page=page)
    sources.walk_place = place
    return sources.open()


def open_asset_sources(context: dict[str, Any], kind: str, label: str,
                       done: Callable, current: str = "", place: str = "") -> ui.dialog:
    sources = _Slot(context, kind, label, done, _asset(context["library"], kind), current)
    sources.walk_place = place
    return sources.open()


def open_folder_sources(context: dict[str, Any], kind: str, label: str,
                        done: Callable, current: str = "", place: str = "") -> ui.dialog:
    sources = _Folder(context, kind, label, done, current)
    sources.walk_place = place
    return sources.open()


def open_notes_sources(context: dict[str, Any], label: str, done: Callable) -> None:
    _Notes(context, label, done).open()


def open_table_sources(context: dict[str, Any], done: Callable) -> None:
    """The ways a table joins this game. `done` runs after any of them adds one."""
    _Table(context, done).open()


def open_library_sources(library: Any, name: str, took: Callable[[uploads.Drop], Any], *,
                         tables: bool, referenced: Callable[[str], Any] | None = None,
                         find: tuple[str, Callable[[], Any]] | None = None) -> None:
    """The Add dialog of a library page. `took` hears what arrives as it would hear a drop
    on the page's empty space. `referenced` takes a table the person asked to leave where
    it is; `find` is the words and the way to Discovery."""
    _Library(library, name, took, tables=tables, referenced=referenced, find=find).open()


def take_drops(element: Any, arrived: Callable[[uploads.Drop], Any]) -> None:
    """Make `element` a place files are dropped on, hearing each finished drop in `arrived`.

    Marked as taking its own drops, so the page-wide handler leaves it alone.
    """
    element._props[uploads.DROP] = uploads.OWN_ZONE
    element.on("dragover", js_handler=_LIGHT)
    element.on("dragleave", js_handler=_DIM)
    element.on("drop", uploads.listener(arrived), js_handler=_MANY)


def open_image_sources(library: Any, name: str, label: str, done: Callable,
                       now: str = "") -> None:
    """`now` is where the browser fetches the picture the collection has, if it has one."""
    _Image(library, name, label, done, now).open()


def open_player_picture_sources(library: Any, label: str, save: Callable, *,
                                services: Sequence[dict[str, Any]] = (),
                                use_service: Callable | None = None,
                                now: str = "") -> None:
    """`save(name, data)` keeps the file and raises with its reason if it cannot.

    `services` are the accounts whose service has a picture; `use_service(extension)` copies
    that one and returns the redraw, raising with its reason where it cannot. `now` is where
    the browser fetches the picture the player has, if there is one.
    """
    _PlayerPicture(library, label, save, services, use_service, now).open()
