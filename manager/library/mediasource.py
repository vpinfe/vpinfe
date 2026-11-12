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
import inspect
import logging
import mimetypes
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import partial
from pathlib import PurePath, PurePosixPath, PureWindowsPath
from typing import Any

from nicegui import run, ui

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
    confirm,
    deeplink,
    folder_picker,
    game_tables,
    kind_words,
    media_ownership,
    offload,
    panel,
    verbs,
)
from manager.kit import dialog as frame
from manager.kit.on_page import on_page
from manager.library import picker_memory, picker_online, record, uploads
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


class _Sources:
    """One Add dialog: its tabs, its drop, and the host's folders.

    A subclass says what is being filled - the title, where the host tab starts, which
    files there can fill it, and what happens to a file once it arrives.
    """

    uploads = True
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

    def __init__(self, library: Any, label: str, done: Callable, current: str = "") -> None:
        self.library = library
        self.label = label
        self.done = done
        # The path on the host of what fills this now, which the host tab marks rather
        # than offers.
        self.current = current
        self.dialog: Any = None
        self.tabs: Any = None
        # Whether the host tab is also where a file is dropped, because the browser is
        # on the device VPinFE runs on.
        self.merged = False
        # Filled when the host tab loads: where a file shown from the library is served.
        self.browse_roots: list[dict[str, Any]] = []

    def title(self) -> str:
        raise NotImplementedError

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

    def folder_use(self, path: str) -> Callable | None:
        """What Use does on a folder row, or None where a folder is only a way down."""
        return None

    def before(self) -> None:
        """Drawn ahead of the tabs, so it is there whichever tab is open."""

    def zone(self, card: Any) -> None:
        """The Upload tab's words and pickers, and what a drop on the card does."""
        raise NotImplementedError

    def choices(self) -> None:
        """Drawn above the host tab's folders, outside the part of it that scrolls."""

    def strip(self) -> None:
        """Drawn between the space over the tabs and the tabs."""

    def opened(self, above: ui.column) -> None:
        """Called once the dialog is up, with the space over the tabs."""

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

    def open(self) -> ui.dialog:
        """Show the dialog and answer it: a walk awaits it, which resolves when it closes."""
        with frame.opened(self.title(), classes="manager-sources-card") as box:
            self.dialog = box
            card = ui.context.slot.parent
            if self.walk_place:
                ui.label(self.walk_place).classes("manager-help px-3")
            self.before()
            above = ui.column().classes("w-full gap-0 px-3")
            self.strip()

            # Ordered by how far the file has to travel: your own device, this library,
            # the machine VPinFE runs on, then the internet.
            host = _host_name(self.library)
            # A browser on the device VPinFE runs on has one device: the tab for its files
            # is the one that takes a drop, and Upload is not a second way in.
            merged = self.merged = self.uploads and acting.browser_is_here()
            with ui.tabs().props("dense no-caps align=left").classes("w-full px-3") as tabs:
                if self.uploads and not merged:
                    ui.tab("upload", label=t("manager.mediasource.upload"),
                           icon=verbs.FROM_FILE)
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
                              else t("manager.mediasource.on_device.help", device=host))
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
            first = self.first or ("upload" if self.uploads and not merged else "host")
            with ui.tab_panels(tabs, value=first).classes("w-full manager-sources-panels"):
                if self.uploads and not merged:
                    with ui.tab_panel("upload"), \
                            ui.column().classes("manager-slot-blank manager-source-zone "
                                                "items-center gap-2"):
                        ui.icon(verbs.FROM_FILE).classes("manager-slot-blank-icon")
                        self.zone(card)
                if self.backglass:
                    with ui.tab_panel("backglass"):
                        backglass_body = ui.column().classes("w-full gap-2 manager-source-fill")
                if self.games:
                    with ui.tab_panel("games"):
                        games_body = ui.column().classes("w-full gap-2 manager-source-fill")
                with ui.tab_panel("host"):
                    self.choices()
                    host_body = ui.column().classes("w-full gap-2 manager-source-fill")
                    if merged:
                        with ui.column().classes("w-full manager-online-base"), \
                                ui.row().classes("w-full items-center gap-x-3 gap-y-1 "
                                                 "manager-dropbox"):
                            self.zone(card)
                if self.online:
                    with ui.tab_panel("online"):
                        online_body = ui.column().classes("w-full gap-2 manager-source-fill")
                if self.keyed:
                    with ui.tab_panel("keyed"):
                        keyed_body = ui.column().classes("w-full gap-2 manager-source-fill")
                if self.records:
                    with ui.tab_panel("record"):
                        record_body = ui.column().classes("w-full gap-2 manager-source-fill")
            with frame.footer().classes("manager-dialog-footer--ruled"):
                if self.walk_place:
                    frame.leave(box, True)
                else:
                    frame.cancel(box.close)
        if self.uploads:
            card.on("dragover", js_handler=_LIGHT)
            card.on("dragleave", js_handler=_DIM)

        box.open()
        self.opened(above)

        # Each tab reads when it is opened rather than up front: two of the three make a
        # request, and a dialog that fetches everything before showing anything would be
        # slowest at the thing people do most, which is drop a file on the first tab.
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
                file_row=self.file_row, keep=self.fits, folder_extra=self._folder_extra,
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

    def _folder_extra(self, path: str) -> Callable[[], Any] | None:
        use = self.folder_use(path)
        if use is None:
            return None
        if path == self.current:
            return panel.state(t("word.current"), "on")
        return panel.action(t("manager.candidates.use"), use, icon=verbs.ACCEPT,
                            hint=t("manager.candidates.use.help"))


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

    def zone(self, card: Any) -> None:
        ui.label(t("manager.mediasource.drop_file")).classes("manager-help manager-drop-only")
        self.pick_button()
        card.on("drop", self._dropped_wrong, js_handler=_one(self.uploader.id))

    def pick_button(self) -> None:
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
        await self.took(event.file.name, data)


class _OnlineTab(_Sources):
    """A picker with an Online tab, drawn by `picker_online.Online` for the kind it fills."""

    built_in = False
    glyph = ""
    family = ""
    drop_key = "manager.picker.drop_file"
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

    async def online_tab(self, body: ui.column) -> None:
        await self.view.draw(body)

    def strip(self) -> None:
        if self.online:
            self.view.strip()

    def show_online(self) -> None:
        self.tabs.set_value("online")

    def take_button(self) -> None:
        raise NotImplementedError

    async def take_offer(self, offer: dict[str, Any], source_name: str, vps_id: str) -> None:
        """Save a file a built-in catalog has. Only a slot has somewhere to put one."""
        raise NotImplementedError

    async def finish_plain(self, message: str) -> None:
        await _Sources.finish(self, message)

    async def agreed_to_replace(self) -> bool:
        """Whether a file may go where the pick will put one; asked before an extension's
        row is taken."""
        return True

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
                 backglass_file: dict[str, Any] | None = None, page: int = 1) -> None:
        super().__init__(context["library"], label, done, target.accept,
                         _on_host(context, current))
        self.target = target
        # A page past 1 follows page 1 to whichever tier holds it, so there is no name
        # to choose and the dialog has no destination row.
        self.page = page
        self.kind = kind
        self.source_kind = source_registry.file_kind(kind) if target.file else kind
        self.family = target.family
        self.setup_online(not target.file, first_page=page == 1)
        self.records = records
        self.backglass_file = backglass_file
        self.backglass = bool(backglass_file)
        self.first = first
        self.context = context
        self.game_id = context["game_id"]
        self.table_id = context["lens"]
        # Where a file could land, which of those is chosen, and the extension the
        # chosen file will bring - the three things that decide what it gets called.
        self.placements: list[dict[str, Any]] = []
        self.extensions: list[str] = []
        self.placed_at: dict[str, Any] | None = None
        self.chosen_extension = ""
        self.filename_note: Any = None
        self._marks: dict[str, Any] = {}

    def memory_kind(self) -> str:
        return self.source_kind

    def kinds_word(self) -> str:
        return kind_words.plural(self.kind, not self.target.file)

    def title(self) -> str:
        return t("manager.mediasource.title_table" if self.table_id
                 else "manager.mediasource.title_game", kind=self.label)

    def opened(self, above: ui.column) -> None:
        async def start() -> None:
            if self.page > 1:
                return
            # Read before anything else: the destination is the decision every tab
            # feeds, and a picker that appears after the first file is chosen has come
            # too late.
            await self.load_placements()
            if not self.shown():
                return
            with above:
                self._destination_row()

        busy.fill(above, start)

    async def load_placements(self) -> None:
        """Where a file could land here, so it can be chosen rather than inferred."""
        try:
            body = await offload.io(self.target.placements, self.game_id, self.kind)
        except Exception:
            logger.debug("No placements for %s", self.kind, exc_info=True)
            return
        self.placements = list(body.get("placements") or [])
        self.extensions = list(body.get("extensions") or [])
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
        """The one decision every way in shares, so it is made once and above them.

        Every option on screen rather than behind a select: there are rarely more than
        a few, and what they differ by is the name the file gets - which a closed
        control shows one of at a time.
        """
        if not self.placements:
            return
        with ui.column().classes("w-full gap-0 manager-destination"):
            ui.label(t("manager.mediasource.save")).classes("manager-card-title")
            for item in self.placements:
                self._placement_choice(item)
            self.filename_note = ui.label("").classes("manager-help manager-destination-name")
        self._describe_placement()

    def _placement_choice(self, item: dict[str, Any]) -> None:
        """One name the file could be given, under the scope that name carries.

        The chip is the media map's own, so the choice made here is labelled with the
        words the map will use about the file afterwards.
        """
        table = str(item.get("table") or "")
        row = ui.column().classes("gap-0 w-full manager-placement")
        with row:
            with ui.row().classes("items-start gap-2 w-full no-wrap manager-placement-line"):
                mark = ui.icon("radio_button_unchecked").classes("manager-placement-mark")
                tier = media_ownership.tier_for(
                    media_ownership.TABLE if table else media_ownership.GAME)
                ui.label(t("manager.picker.this_table" if table
                           else "manager.picker.this_game")) \
                    .classes(f"manager-tier {tier.css}")
                ui.label(str(item.get("base") or "")).classes("manager-placement-name")
            going = list(item.get("displaces") or [])
            if going:
                ui.label(t("manager.mediasource.overwrites", count=len(going))) \
                    .classes("manager-destination-conflict manager-placement-note")
        self._marks[table] = mark
        row.on("click", lambda t=table: self._choose_placement(t))

    def _choose_placement(self, table: str) -> None:
        self.placed_at = next((item for item in self.placements
                               if item.get("table") == (table or "")), self.placed_at)
        self._describe_placement()

    def _describe_placement(self) -> None:
        """Which name is taken, and the one thing the name on screen cannot say."""
        for table, mark in self._marks.items():
            picked = table == self.destination
            mark.props(f'name={"radio_button_checked" if picked else
                                "radio_button_unchecked"}')
            mark.classes(replace="manager-placement-mark"
                         + (" manager-placement-mark--on" if picked else ""))
        # The extension comes from the file, which on the upload tab is not chosen yet.
        sure = self.chosen_extension or (self.extensions[0] if len(self.extensions) == 1
                                         else "")
        self.filename_note.text = (
            t("manager.mediasource.saved", chosen_extension=sure) if sure
            else t("manager.mediasource.extension_hint"))

    def note_extension(self, filename: str) -> None:
        """The picked file's extension, so the name shown is the name it will get."""
        suffix = ("." + filename.rsplit(".", 1)[1].lower()) if "." in filename else ""
        if suffix != self.chosen_extension:
            self.chosen_extension = suffix
            if self.filename_note is not None:
                self._describe_placement()

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
    async def confirmed(self, filename: str) -> bool:
        """Ask before a write that deletes something, naming what goes.

        Asked again here rather than trusted from the list: the dropdown's count was
        read when the dialog opened, and the answer that matters is the one at the
        moment of the write.
        """
        self.note_extension(filename)
        return await self._agreed(filename)

    @on_page
    async def agreed_to_replace(self) -> bool:
        """Ask before an extension's row takes the place of files, naming those the
        chosen name would replace. What arrives is not known yet, so its extension is not
        either: the family of the kind answers for any of them."""
        return await self._agreed("file" + (self.target.accept or ("",))[0])

    async def _agreed(self, filename: str) -> bool:
        try:
            going = await offload.io(partial(
                self.target.displaced, self.game_id, self.destination, self.kind,
                filename, **self._of_page()))
        except Exception as exc:
            ui.notify(t("manager.mediasource.slot_failed"), caption=why(exc),
                      type="negative")
            return False
        return not going or await confirm.replace(self.label, going)

    def take_button(self) -> None:
        self.pick_button()

    @on_page
    async def take_offer(self, offer: dict[str, Any], source_name: str, vps_id: str) -> None:
        if not await self.confirmed(str(offer.get("name") or "")):
            return
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

    def candidate(self, src: str, name: str, meta: str | Callable[[], None], tag: str,
                  take: Callable, current: bool = False) -> None:
        """A candidate row carrying what this dialog knows: how to draw a file of the
        kind being replaced."""
        candidates.row(src, name, meta, tag, take, family=self.target.family,
                       line=not self.target.family, current=current)

    @on_page
    async def took(self, name: str, data: bytes) -> None:
        if await self.confirmed(name):
            await self.place(name, data)

    @on_page
    async def place(self, name: str, data: bytes) -> None:
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
        """Match the saved file to the VPS file picked on the Online tab, if one was. The
        file is saved either way, so a match that fails is said and the save stands."""
        pick = self.view.pick
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
        name = _file_name(path)
        # Asked before the file is read: a video is hundreds of megabytes, and a
        # replacement somebody declines should not have cost the read.
        if not await self.confirmed(name):
            return
        try:
            data = await self.contents(path)
        except Exception as exc:
            ui.notify(t("manager.mediasource.import_failed"), caption=why(exc),
                      type="negative")
            return
        self.remember(path)
        await self.place(name, data)

    def file_row(self, item: dict[str, Any]) -> None:
        current = item["path"] == self.current
        self.candidate(self.preview(item["path"]), item["name"], "",
                       "" if current else self._in_use(item["name"]),
                       lambda: self.use_file(item["path"]), current)

    def _in_use(self, name: str) -> str:
        """Whether this file is already serving one of the game's slots.

        In a game's own folder most files are already somebody's, and the interesting
        ones are the strays. Said per row rather than by hiding the rest, so the folder
        still looks like the folder.
        """
        for kind, entry in (self.context.get("media") or {}).items():
            if entry.get("file") == name:
                return t("manager.mediasource.already", kind=media_label_map().get(kind, kind))
        return ""

    # --- recorded on this device --------------------------------------------------

    def picked(self, tab: str) -> None:
        if self.records:
            made = record.MADE_AS.get(media_family(self.kind), "")
            self.note_extension(made if tab == "record" else "")
        if self.backglass:
            self.note_extension(BACKGLASS_PICTURE if tab == "backglass" else "")

    async def backglass_tab(self, body: ui.column) -> None:
        """The picture the game's backglass file draws, as one candidate."""
        offer = self.backglass_file or {}

        @on_page
        async def take() -> None:
            if not await self.confirmed(BACKGLASS_PICTURE):
                return
            try:
                await run.io_bound(self.library.draw_backglass_picture, self.game_id,
                                   self.destination)
            except Exception as exc:  # noqa: BLE001 - the notice says why
                ui.notify(t("manager.mediasource.place_failed"), caption=why(exc),
                          type="negative")
                return
            await self.finish(t("manager.mediasource.label_saved", label=self.label))

        def src(size: int) -> str:
            return art.backglass(self.game_id, str(offer.get("path") or ""),
                                 version=offer.get("version"), size=size,
                                 grill=bool(offer.get("grill")))

        with body:
            candidates.row(src(art.CELL), str(offer.get("file") or ""),
                           t("manager.mediasource.backglass_grill") if offer.get("grill")
                           else "", "", take, peek=src(art.PANEL))

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
            tuning = ui.column().classes("w-full gap-0")

        def draw_top() -> None:
            top.clear()
            with top:
                if now["stop"]:
                    _attention(now["stop"])
                with ui.row().classes("items-center gap-2 no-wrap min-w-0"):
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

        async def decided(use: bool) -> None:
            if use:
                await self.finish(t("manager.mediasource.label_saved", label=self.label))
            else:
                made.clear()

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
                    with made:
                        record.proposal(library, row, decided, titled=False)
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


class _Folder(_OnlineTab):
    """A kind that arrives as many files - a PUP pack, a color set, a sound bank, music."""

    def __init__(self, context: dict[str, Any], kind: str, label: str,
                 done: Callable, current: str = "") -> None:
        super().__init__(context["library"], label, done, _on_host(context, current))
        self.context = context
        self.kind = kind
        self.source_kind = kind
        self.drop_key = ("manager.picker.drop_pack" if kind in _ONE_OR_FOLDER
                         else "manager.picker.drop_files")
        self.setup_online(False)
        self.game_id = context["game_id"]
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
        return t("manager.mediasource.title_game", kind=self.label)

    def starts(self) -> list[dict[str, Any]]:
        return self.library.disk_starts(self.game_id)

    def suffixes(self) -> tuple[str, ...]:
        return (*self.extensions, *ARCHIVE_EXTENSIONS)

    def fits(self, item: dict[str, Any]) -> bool:
        return _suffix(item) in ARCHIVE_EXTENSIONS or _suffix(item) in self.extensions

    async def use_file(self, path: str) -> None:
        await self._take(path)

    def folder_use(self, path: str) -> Callable | None:
        return lambda: self._take(path)

    def _opens_at(self, starts: list[dict[str, Any]]) -> str:
        """Beside what fills this, so its own row, with Current and Use, is on screen."""
        return folder_picker.enclosing(self.current) if self.current \
            else super()._opens_at(starts)

    def file_row(self, item: dict[str, Any]) -> None:
        archive = _suffix(item) in ARCHIVE_EXTENSIONS
        candidates.row("", item["name"], _size(item.get("size_bytes")), "",
                       lambda: self._take(item["path"]), family="",
                       glyph="folder_zip" if archive else self.glyph, line=True,
                       current=item["path"] == self.current)

    def zone(self, card: Any) -> None:
        heard = uploads.listener(self.arrived)
        ui.label(t("manager.mediasource.drop_files")).classes("manager-help manager-drop-only")
        self.choose(heard)
        card.on("drop", heard, js_handler=_MANY)

    def choose(self, heard: Callable[[Any], Any]) -> None:
        """The one button this kind is taken with: files or one file with a folder behind
        its caret, never two buttons."""
        _choose(self.kind not in _ONE_OR_FOLDER, heard)

    def take_button(self) -> None:
        self.choose(uploads.listener(self.arrived))

    async def arrived(self, drop: uploads.Drop) -> None:
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


class _Notes(_Folder):
    def __init__(self, context: dict[str, Any], label: str, done: Callable) -> None:
        super().__init__(context, "readme", label, done)
        self.reads_names = False

    def suffixes(self) -> tuple[str, ...]:
        return tuple(DOC_FAMILY)

    def folder_use(self, path: str) -> Callable | None:
        return None

    def zone(self, card: Any) -> None:
        heard = uploads.listener(self.arrived)
        ui.label(t("manager.mediasource.drop_file")).classes("manager-help manager-drop-only")
        panel.action(t("manager.mediasource.choose_file"), heard, icon=verbs.FROM_FILE,
                     hint=t("manager.mediasource.choose_file.help"),
                     js="() => window.__managerChoose(false, emit, true)")()
        card.on("drop", heard, js_handler=_MANY)


def _copy_or_use(copies: bool, chosen: Callable[[bool], None]) -> None:
    """Copy or reference, as radios with their descriptions: the descriptions are what
    the choice is made on. `chosen` hears each pick, the first included."""
    marks: dict[bool, Any] = {}

    def pick(copying: bool) -> None:
        chosen(copying)
        for key, mark in marks.items():
            mark.props(f'name={"radio_button_checked" if key == copying else
                                "radio_button_unchecked"}')
            mark.classes(replace="manager-placement-mark"
                         + (" manager-placement-mark--on" if key == copying else ""))

    with ui.column().classes("w-full gap-0 manager-destination"):
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


class _Table(_Folder):
    """A table for a game: taken from the device as a copy or where it is, or an ID its
    launcher starts it from."""

    keyed = True

    def __init__(self, context: dict[str, Any], done: Callable) -> None:
        super().__init__(context, "table", t("asset.kind.table.label"), done)
        self.drop_key = "manager.picker.drop_table"
        self.game_name = str(context["game"].get("name") or "")
        self.narrows_to = ""
        self.adds_table = True
        self.copies = True

    def title(self) -> str:
        return t("manager.mediasource.add_table", name=self.game_name)

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

    def folder_use(self, path: str) -> Callable | None:
        return None

    def file_row(self, item: dict[str, Any]) -> None:
        candidates.row("", item["name"], _size(item.get("size_bytes")), "",
                       lambda: self._chosen(item["path"]), family="", glyph=self.glyph,
                       line=True)

    def zone(self, card: Any) -> None:
        heard = uploads.listener(self.arrived)
        ui.label(t("manager.mediasource.drop_table")).classes("manager-help manager-drop-only")
        ui.label(t("manager.mediasource.drop_table.help")).classes("manager-help manager-drop-only")
        self.choose(heard)
        card.on("drop", heard, js_handler=_MANY)

    def choices(self) -> None:
        _copy_or_use(self.copies, lambda copies: setattr(self, "copies", copies))

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
        pick = self.view.pick
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
                panel.action(t("word.add"), add, icon=verbs.ADD,
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


class _Library(_Sources):
    """The Add dialog of a library page. What it takes goes where a file dropped on the
    page's empty space goes: a table makes a game, anything else asks which game."""

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
        link = ui.label(words).classes("manager-link cursor-pointer px-3")
        link.on("click", lambda: self._leave(go))

    async def _leave(self, go: Callable[[], Any]) -> None:
        self.dialog.close()
        answer = go()
        if inspect.isawaitable(answer):
            await answer

    def zone(self, card: Any) -> None:
        heard = uploads.listener(self.arrived)
        ui.label(t("manager.mediasource.drop_files")).classes("manager-help manager-drop-only")
        _choose(True, heard)
        card.on("drop", heard, js_handler=_MANY)

    def choices(self) -> None:
        if self.tables and self.referenced is not None:
            _copy_or_use(self.copies, lambda copies: setattr(self, "copies", copies))

    def folder_use(self, path: str) -> Callable | None:
        return lambda: self._take(path)

    async def use_file(self, path: str) -> None:
        await self._chosen(path)

    def file_row(self, item: dict[str, Any]) -> None:
        archive = _suffix(item) in ARCHIVE_EXTENSIONS
        candidates.row("", item["name"], _size(item.get("size_bytes")), "",
                       lambda: self._chosen(item["path"]), family="",
                       glyph="folder_zip" if archive else icons.TABLES
                       if _suffix(item) in apps.table_suffixes() else verbs.FROM_FILE,
                       line=True)

    async def arrived(self, drop: uploads.Drop) -> None:
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
        await self.arrived(uploads.Drop(upload_id=upload_id, name=_file_name(path)))


class _Image(_OneFile):
    """A collection's picture."""

    games = True

    def __init__(self, library: Any, name: str, label: str, done: Callable) -> None:
        super().__init__(library, label, done, IMAGE_FAMILY)
        self.name = name

    def memory_kind(self) -> str:
        return "picture"

    def kinds_word(self) -> str:
        return t("word.pictures")

    def title(self) -> str:
        return t("manager.mediasource.title_collection", kind=self.label)

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
        candidates.row(self.preview(item["path"]), item["name"], "", "",
                       lambda: self.use_file(item["path"]))

    async def games_tab(self, body: ui.column) -> None:
        body.clear()
        try:
            entries = await offload.io(self.library.collection_entries, self.name)
        except Exception as exc:  # noqa: BLE001
            with body:
                panel.line(t("manager.mediasource.games_failed"), hint=why(exc))
            return
        wheeled = [entry for entry in entries if "wheel" in (entry.get("media") or [])]
        with body:
            if not wheeled:
                ui.label(t("manager.mediasource.no_game_wheels")).classes("manager-help")
                return
            find = None
            if len(wheeled) > _FIND_FROM:
                with ui.element("div").classes("w-full"):
                    find = frame.field(placeholder=t("manager.mediasource.find_game"))
            listing = ui.column().classes("w-full gap-1 manager-source-list")

        def draw() -> None:
            typed = str(find.value or "").strip().casefold() if find is not None else ""
            found = [entry for entry in wheeled
                     if typed in str((entry.get("game") or {}).get("name") or "").casefold()]
            listing.clear()
            with listing:
                if not found:
                    ui.label(t("manager.mediasource.find_empty")).classes("manager-help")
                for entry in found:
                    self._wheel_row(entry)

        if find is not None:
            find.on_value_change(draw)
        draw()

    def _wheel_row(self, entry: dict[str, Any]) -> None:
        game = entry.get("game") or {}
        game_id = str(game.get("id") or "")
        table_id = str((entry.get("table") or {}).get("id") or "")
        name = str(game.get("name") or "")

        @on_page
        async def take() -> None:
            try:
                data, served = await offload.io(self.library.media_file, game_id, table_id,
                                                "wheel")
            except Exception as exc:  # noqa: BLE001
                ui.notify(t("manager.mediasource.image_failed"), caption=why(exc),
                          type="negative")
                return
            await self.took(_named_for(name, served), data)

        candidates.row(art.media(game_id, "wheel", table_id, size=art.CELL), name,
                       game_tables.made(game), "", take,
                       peek=art.media(game_id, "wheel", table_id, size=art.PANEL))

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
    games to borrow a wheel from."""

    games = False

    def __init__(self, library: Any, label: str, save: Callable,
                 services: Sequence[dict[str, Any]] = (),
                 use_service: Callable | None = None) -> None:
        super().__init__(library, "", label, save)
        self.services = list(services)
        self.use_service = use_service
        self.online = bool(self.services and use_service)

    def title(self) -> str:
        return t("manager.mediasource.title_player", kind=self.label)

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


def open_sources(context: dict[str, Any], kind: str, label: str,
                 done: Callable, current: str = "", *, recording_it: bool = False,
                 backglass_file: dict[str, Any] | None = None,
                 place: str = "", page: int = 1) -> ui.dialog:
    """Open the ways to fill this slot. Returns as soon as the dialog is up.

    `current` is what fills it now, as a path in the game's folder. `recording_it` opens
    it on its Record tab. `backglass_file` is the picture the game's backglass file draws,
    which adds a tab and, on an empty slot, is where the dialog opens. `place` is the
    position in a walk, which makes the way out Skip and Stop. `page` is the page of a
    paged kind the file becomes; `label` then names the page, not the kind.
    """
    library = context["library"]
    first = "record" if recording_it else \
        "backglass" if backglass_file and not current and page == 1 else ""
    sources = _Slot(context, kind, label, done, _media(library, kind), current,
                    records=records(library, kind) is not None and page == 1, first=first,
                    backglass_file=backglass_file if page == 1 else None, page=page)
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


def open_image_sources(library: Any, name: str, label: str, done: Callable) -> None:
    _Image(library, name, label, done).open()


def open_player_picture_sources(library: Any, label: str, save: Callable, *,
                                services: Sequence[dict[str, Any]] = (),
                                use_service: Callable | None = None) -> None:
    """`save(name, data)` keeps the file and raises with its reason if it cannot.

    `services` are the accounts whose service has a picture; `use_service(extension)` copies
    that one and returns the redraw, raising with its reason where it cannot.
    """
    _PlayerPicture(library, label, save, services, use_service).open()
