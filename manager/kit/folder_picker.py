"""Browsing this machine to fill a path field with a folder, a program or a file.

`list_entries` is `library.folders`, taken as a parameter rather than imported: this
draws for any field that wants a path, and none of them is the library client.

New folder is offered for a folder, whenever the callable that lists is a method of an
object that can also make one - `library.folders` and the API client's are, a bare
function is not and so draws without it.

`pick_path` is the dialog. `read_start` and `draw_browser` are what it is made of, so a
dialog with a tab for this device's disk draws the same browser in the tab.
"""

from __future__ import annotations

import contextlib
import inspect
import json
import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

from nicegui import ui

from common.config import path_state
from common.failures import why
from common.games import settings_file_name
from common.i18n import t
from manager.kit import dialog as frame
from manager.kit import offload, panel, verbs
from manager.kit.on_page import on_page

_TITLE = {"dir": "manager.folder_picker.title_folder",
         "exe": "manager.folder_picker.title_program",
         "file": "manager.folder_picker.title_file"}
_ROW_ICON = {"exe": "terminal", "file": "description"}
# `/`, a drive and the mount points: where browsing starts over, so they wear the drive's mark.
_DRIVE_PATH = re.compile(r"^(/|/Volumes|/media|/mnt|[A-Za-z]:\\?)$")


def _free_name(folders: list[dict[str, Any]]) -> str:
    """The first of New folder, New folder 2, ... that this folder does not hold."""
    taken = {one["name"].lower() for one in folders}
    name, count = t("manager.folder_picker.new_folder_name"), 1
    while name.lower() in taken:
        count += 1
        name = t("manager.folder_picker.new_folder_numbered", count=count)
    return name


def _icon_for(place: dict[str, Any]) -> str:
    """Home and Downloads by the name the listing gives them, which is the only thing that
    says so: the Manager may be drawing for a machine whose home it cannot see."""
    if place.get("icon"):
        return str(place["icon"])
    if place.get("name") == t("word.home"):
        return verbs.HOME
    if place.get("name") == t("word.downloads"):
        return verbs.DOWNLOADS
    return verbs.DRIVE if _DRIVE_PATH.match(str(place.get("path") or "")) else "folder"


def suffixes_named(suffixes: tuple[str, ...]) -> str:
    """`.a`, `.a or .b`, `.a, .b or .c` - in the language's own list words."""
    if len(suffixes) == 1:
        return suffixes[0]
    return t("manager.collection_rules.or_last",
             rest=t("manager.collection_rules.list_join").join(suffixes[:-1]),
             last=suffixes[-1])


def last_name(path: str) -> str:
    return re.split(r"[\\/]", path.rstrip("\\/"))[-1] or path


def enclosing(path: str) -> str:
    """The folder that holds `path`, as text, or "" at the top of it."""
    trimmed = path.rstrip("\\/")
    cut = max(trimmed.rfind("/"), trimmed.rfind("\\"))
    if cut < 0:
        return ""
    head = trimmed[:cut]
    return trimmed[:cut + 1] if not head or head.endswith(":") else head


def join(folder: str, name: str) -> str:
    sep = "\\" if "\\" in folder and "/" not in folder else "/"
    return folder.rstrip("\\/") + sep + name


def _typed_path(text: str, here: str) -> str:
    """What was typed or pasted as a path: quotes dropped, as Windows' Copy as path adds
    them, and a bare name taken as inside the folder you are in."""
    said = text.strip()
    if len(said) > 1 and said[0] == said[-1] and said[0] in "\"'":
        said = said[1:-1].strip()
    if said and not re.search(r"[\\/]", said) and not said.startswith("~") \
            and not re.match(r"^[A-Za-z]:", said):
        said = join(here, said)
    return said


# Focus selects the whole path. Escape restores it once something else is typed; before
# that the dialog gets the key and closes.
_FOCUS_JS = ("() => { const input = document.getElementById(%s);"
             " setTimeout(() => input && input.select(), 0); }")
_ESCAPE_JS = ("(event) => { if (event.key !== 'Escape') return;"
              " const input = document.getElementById(%s);"
              " if (!input || input.dataset.shown === undefined"
              " || input.value === input.dataset.shown) return;"
              " event.stopPropagation(); input.value = input.dataset.shown;"
              " input.dispatchEvent(new Event('input')); window.managerPathEnd(%s); }")
# The field is drawn again on arriving, so Enter would leave the keyboard on nothing.
_REFOCUS_JS = ("setTimeout(() => { const input = document.getElementById(%s);"
               " if (input) input.focus(); }, 80);")
_SHOWN_JS = ("setTimeout(() => { const input = document.getElementById(%s);"
             " if (input) input.dataset.shown = %s; }, 60);")


@dataclass(frozen=True)
class Start:
    """Where a browser opens: the folder listed, the start asked for, and why that start
    could not be listed, where it could not."""

    here: dict[str, Any]
    start: str
    refused: Exception | None


@dataclass
class Browser:
    """What the caller of a drawn browser can do with it."""

    choose: Callable[[], Awaitable[None]]
    start_naming: Callable[[], None]
    selected: Callable[[], str]
    can_name: bool
    can_choose: Callable[[], bool]


_DISK_TOPS = ("", "/", "/Volumes", "/media", "/mnt")


def _is_disk_top(path: str) -> bool:
    """The top of a disk or the folder that holds the mounted ones: a place that is there
    whether or not the drive a path was on still is."""
    return path.rstrip("\\/") in _DISK_TOPS or bool(re.fullmatch(r"[A-Za-z]:[\\/]*", path))


async def read_start(list_entries: Callable[..., dict], start: str = "", *,
                     kind: str = "dir", suffixes: tuple[str, ...] = (),
                     nearest: bool = False, fallback: str = "") -> Start:
    """The folder to open at.

    A `start` that cannot be listed opens at the kind's own start, saying why, instead
    of failing to open at all - the field it came from may hold a path from a mount that
    has since gone away, or one inside a private folder.

    With `nearest`, a `start` that is not there opens at the closest folder above it that
    is, saying nothing. Where a `fallback` is given, a closest folder that is the top of a
    disk or the one that holds the mounted ones, which a gone drive leaves behind, is not
    taken: the picker opens at `fallback`, saying why.
    """
    refused: Exception | None = None
    here: Any = None
    try:
        here = await offload.io(list_entries, start, kind, suffixes)
    except Exception as exc:  # noqa: BLE001 - a stale saved path is not a reason to refuse
        refused = exc
        above = enclosing(start) if nearest else ""
        while above and not (fallback and _is_disk_top(above)) and here is None:
            try:
                here = await offload.io(list_entries, above, kind, suffixes)
                refused = None
            except Exception:  # noqa: BLE001 - keep climbing
                above = enclosing(above)
    if here is None and fallback:
        with contextlib.suppress(Exception):
            here = await offload.io(list_entries, fallback, kind, suffixes)
    if here is None:
        here = await offload.io(list_entries, "", kind, suffixes)
    return Start(here, start, refused)


@on_page
async def pick_path(list_entries: Callable[..., dict], start: str = "", *,
                    kind: str = "dir", suffixes: tuple[str, ...] = (),
                    nearest: bool = False, title: str = "",
                    lead: Sequence[dict[str, Any]] | None = None, empty: str = "",
                    notes: Callable[[str], str] | None = None,
                    saving: str | None = None) -> str | None:
    """The chosen path, or None where the dialog was cancelled.

    With `saving`, the dialog names a file to be written in the folder it is in, starting
    from that name: the path given back need not exist.
    """
    first = await read_start(list_entries, start, kind=kind, suffixes=suffixes,
                             nearest=nearest)
    choose: dict[str, Any] = {}

    def ready(enabled: bool) -> None:
        if "button" in choose:
            choose["button"].set_enabled(enabled)

    with frame.opened(title or t(_TITLE[kind]), wide=True,
                      ignore_outside_click=True) as box:
        browser = draw_browser(first, list_entries, kind=kind, suffixes=suffixes,
                               take=box.submit, on_ready=ready, lead=lead, empty=empty,
                               notes=notes, saving=saving)
        with frame.footer().classes("manager-dialog-footer--ruled"):
            if browser.can_name:
                frame.aside(t("manager.folder_picker.new_folder"), browser.start_naming,
                            icon=verbs.NEW_FOLDER)
            frame.cancel(lambda: box.submit(None))
            if kind == "dir":
                frame.answer(t("manager.folder_picker.accept"), browser.choose,
                             icon=verbs.CHOOSE)
            else:
                choose["button"] = frame.answer(
                    t("manager.folder_picker.choose"), browser.choose, icon=verbs.CHOOSE)
                choose["button"].set_enabled(browser.can_choose())

    return await box


def draw_browser(first: Start, list_entries: Callable[..., dict], *, kind: str = "dir",
                 suffixes: tuple[str, ...] = (), take: Callable[[str], Any],
                 on_ready: Callable[[bool], None] | None = None,
                 places: Sequence[dict[str, Any]] | None = None,
                 file_row: Callable[[dict[str, Any]], None] | None = None,
                 keep: Callable[[dict[str, Any]], bool] | None = None,
                 folder_extra: Callable[[str], Callable[[], Any] | None] | None = None,
                 empty: str = "", limit: int = 0, embedded: bool = False,
                 notes: Callable[[str], str] | None = None,
                 saving: str | None = None,
                 lead: Sequence[dict[str, Any]] | None = None) -> Browser:
    """Draw the browser where the caller is standing and answer how to drive it.

    `take(path)` is called with the path chosen. `on_ready(enabled)` says whether Choose
    has something to take. `places` replaces the places the listing names, so a dialog can
    put its own first. `file_row(item)` draws a file's row where it is more than a name,
    `keep(item)` drops the files it refuses and `folder_extra(path)` answers what to draw on
    the end of a folder's row, or None. `limit` caps the rows drawn and `empty` words an
    empty folder. `embedded` is a browser in a tab: it fills the tab, opens with nothing
    chosen, and takes a typed file at once as there is no Choose to press.

    `lead` is places to put before the ones the listing names, Home and Downloads among them.
    `notes(path)` says a word to show at the end of a file's row, or "". `saving` turns the
    browser into one that names a file: a Name box under the list, starting with that name
    and ending in .ini whatever is typed, whose refusals switch Choose off. Clicking a file
    puts its name in the box, and what is taken is the folder you are in and that name.
    """
    here = first.here
    start = first.start
    refused = first.refused
    ready = on_ready or (lambda enabled: None)
    picked = start if kind != "dir" and not embedded and any(
        f["path"] == start for f in here.get("files") or []) else ""
    state: dict[str, Any] = {"here": here, "selected": picked, "naming": None, "shown": "",
                             "touched": False}
    field: dict[str, Any] = {}
    make_folder: Callable[[str, str], dict] | None = getattr(
        getattr(list_entries, "__self__", None), "make_folder", None) \
        if kind == "dir" and not embedded else None

    if refused is not None:
        with ui.element("div").classes("manager-attention w-full"):
            ui.icon("error_outline").classes("manager-attention-icon")
            with ui.column().classes("gap-0 min-w-0"):
                ui.label(why(refused)).classes("manager-attention-line")
                ui.label(start).classes("manager-help truncate")
    with ui.column().classes("manager-picker-head gap-2"):
        path_row = ui.row().classes("items-center gap-2 w-full no-wrap")
        with ui.row().classes("items-center gap-x-4 w-full no-wrap"):
            term = ui.input(placeholder=t("manager.folder_picker.filter_folders"
                                          if kind == "dir"
                                          else "manager.folder_picker.filter")) \
                .props("dense outlined clearable clear-icon=close").classes("grow")
            shown = ui.checkbox(t("manager.folder_picker.show_hidden"), value=False) \
                .props("dense").classes("whitespace-nowrap")
    with ui.column().classes("manager-picker-well gap-0"
                             + (" manager-source-fill" if embedded else "")):
        listing_col = ui.column().classes(
            "w-full gap-1 " + ("manager-source-list" if embedded
                               else "manager-folder-list"
                               + (" manager-folder-list--saving" if saving is not None
                                  else "")))
        ui.label(t("manager.folder_picker.private_note")).classes(
            "manager-help manager-picker-note")

    def _final_name() -> str:
        text = (field["name"].value or "").strip()
        return text if text.lower().endswith(settings_file_name.SUFFIX) \
            else text + settings_file_name.SUFFIX

    def _name_problem() -> str:
        text = field["name"].value or ""
        return settings_file_name.problem(text) if text.strip() or state["touched"] else ""

    def _name_suffix() -> None:
        ends = (field["name"].value or "").lower().endswith(settings_file_name.SUFFIX)
        field["name"].props["suffix"] = "" if ends else settings_file_name.SUFFIX

    def _name_changed() -> None:
        state["touched"] = True
        _name_suffix()
        why_not = _name_problem()
        field["name"].props["error"] = bool(why_not)
        field["name"].props["error-message"] = why_not
        field["name"].update()
        ready(_ok(False))

    @on_page
    async def _name_enter() -> None:
        await _take()

    def _ok(typing_now: bool) -> bool:
        """Whether Choose has something to take."""
        if saving is not None:
            return typing_now or not settings_file_name.problem(field["name"].value or "")
        return typing_now or bool(state["selected"])

    def _choice() -> str:
        """What Choose would give: the folder you are in, or the file chosen in it."""
        return state["selected"] if kind != "dir" and state["selected"] \
            else state["here"]["path"]

    def _sync_field() -> None:
        """Show `_choice()` in the field, as what is shown and not as something typed."""
        control = field.get("control")
        if control is None or control.is_deleted:
            return
        state["shown"] = _choice()
        control.value = state["shown"]
        ui.run_javascript(_SHOWN_JS % (json.dumps(panel.native_id(control)),
                                       json.dumps(state["shown"])))
        panel.path_ends(control)

    def draw_path() -> None:
        path_row.clear()
        here_now = state["here"]
        named: dict[str, dict[str, Any]] = {}
        for one in [*(lead or []), *(places if places is not None
                                     else here_now.get("roots") or [])]:
            named.setdefault(one["path"], one)
        state["shown"] = _choice()
        seen: dict[str, Any] = {"was": object()}
        with path_row:
            up = panel.icon_only(verbs.UP, lambda: _go(here_now["parent"]),
                                 hint=t("manager.folder_picker.up"))
            if not here_now["parent"]:
                up.disable()
            control = panel.path_box(state["shown"], name=t("word.path"))
            field["control"] = control
            with control.add_slot("prepend"):
                ui.icon(_ROW_ICON[kind] if kind != "dir" and state["selected"]
                        else _icon_for(named.get(here_now["path"], {"path": here_now["path"]}))
                        ).classes("manager-path-mark")
            with control.add_slot("append"):
                mark = ui.element("div").classes("manager-value-state")
                panel.copy_path(control)
                caret = panel.icon_only(verbs.PLACES, lambda: None,
                                        hint=t("manager.folder_picker.places"))
                caret.props["aria-label"] = t("manager.folder_picker.places")
                with caret, ui.menu().props('anchor="bottom right" self="top right"'):
                    _places_menu(named)
            panel.path_input(control)
            native = json.dumps(panel.native_id(control))
            control.on("focus", js_handler=_FOCUS_JS % native)
            control.on("keyup", js_handler=_ESCAPE_JS % (native, native))
            control.on("keydown.enter", _enter)
            ui.run_javascript(_SHOWN_JS % (native, json.dumps(state["shown"])))

            def typing() -> bool:
                return (control.value or "") != state["shown"]

            def typed() -> None:
                ready(_ok(typing()))

            async def look() -> None:
                said = control.value or ""
                if said == seen["was"]:
                    return
                seen["was"] = said
                if not typing() or not said.strip():
                    mark.clear()
                    return
                _, _, reason = await _resolve(said)
                if control.is_deleted or (control.value or "") != said:
                    return
                mark.clear()
                with mark:
                    panel.value_state(path_state.MISSING if reason else path_state.OK,
                                      reason)(control)

            control.on_value_change(typed)
            # One timer comparing against what it last asked about, as `path_field`
            # does: a restarted one-shot per keystroke stops arriving.
            ui.timer(0.3, look)

    def _places_menu(places: dict[str, dict[str, Any]]) -> None:
        """One entry per place, the current one ticked."""
        def entry(place: dict[str, Any]) -> None:
            current = place["path"] == state["here"]["path"]
            icon = _icon_for(place)
            panel.menu_entry(
                place["name"], None if current else _opens(place["path"]),
                mark=lambda: ui.icon(icon).classes("manager-menu-mark"),
                trail=(lambda: ui.icon("check").classes("manager-menu-check"))
                if current else None,
                classes="manager-menu-on" if current else "")

        for place in places.values():
            entry(place)

    async def _resolve(text: str) -> tuple[dict[str, Any] | None, str, str]:
        """The listing that holds what was typed, the file in it that it names (empty
        for a folder), and why Enter cannot go there (empty when it can).

        The listing decides, as it does for a click: a file it does not offer, by
        kind, case or Show hidden, or a private folder, is refused here rather than
        accepted and then dropped.
        """
        where = _typed_path(text, state["here"]["path"])
        folder = await offload.io(path_state.look, where, path_state.DIR)
        is_folder = folder.state == path_state.OK
        probe = where
        if not is_folder:
            if kind == "dir":
                return None, "", folder.reason
            found = await offload.io(path_state.look, where, kind)
            if found.state != path_state.OK:
                return None, "", found.reason
            if kind == "file" and suffixes and not where.lower().endswith(
                    tuple(one.lower() for one in suffixes)):
                return None, "", t("error.path.wrong_suffix", suffix=suffixes_named(suffixes))
            probe = enclosing(where)
        try:
            fresh = await offload.io(list_entries, probe, kind, suffixes, shown.value)
        except Exception as exc:  # noqa: BLE001
            return None, "", why(exc)
        name = last_name(where).lower()
        files = fresh.get("files") or []
        entry = next((one for one in files if one["name"].lower() == name), None)
        if is_folder:
            if entry is not None and name.endswith(".app"):
                return fresh, entry["path"], ""
            if re.search(r"\.app([\\/]|$)", where, re.IGNORECASE):
                return None, "", t("error.path.not_listed")
            return fresh, "", ""
        if entry is None or (keep is not None and not keep(entry)):
            return None, "", t("error.path.not_listed")
        return fresh, entry["path"], ""

    @on_page
    async def _typed(text: str) -> tuple[dict[str, Any], str] | None:
        """The listing of what was typed and the file in it that it names, or None
        where it cannot be gone to, saying why."""
        fresh, chosen, reason = await _resolve(text)
        if fresh is None:
            ui.notify(t("manager.mediasource.folder_failed"), caption=reason,
                      type="negative")
            return None
        return fresh, chosen

    async def _give(path: str) -> None:
        """Hand `path` to the caller, who may answer at once or after some work."""
        done = take(path)
        if inspect.isawaitable(done):
            await done

    @on_page
    async def _enter() -> None:
        text = field["control"].value or ""
        if text == state["shown"] or not text.strip():
            await _take()
            return
        got = await _typed(text)
        if got is not None:
            if embedded and got[1]:
                await _give(got[1])
                return
            state["selected"] = got[1]
            _arrive(got[0])
            ui.run_javascript(_REFOCUS_JS % json.dumps(panel.native_id(field["control"])))

    async def _take() -> None:
        if saving is not None:
            if _ok(False):
                await _give(join(state["here"]["path"], _final_name()))
        elif kind == "dir":
            await _give(state["here"]["path"])
        elif state["selected"]:
            await _give(state["selected"])

    @on_page
    async def _choose() -> None:
        text = field["control"].value or ""
        if text == state["shown"] or not text.strip():
            await _take()
            return
        got = await _typed(text)
        if got is None:
            return
        fresh, chosen = got
        if kind == "dir":
            await _give(fresh["path"])
        elif chosen:
            await _give(chosen)
        else:
            _arrive(fresh)

    def select(path: str) -> None:
        state["selected"] = path
        if saving is not None:
            name = last_name(path)
            field["name"].value = name[:-len(settings_file_name.SUFFIX)] \
                if name.lower().endswith(settings_file_name.SUFFIX) else name
        draw_listing()
        _sync_field()
        ready(_ok(False))

    def draw_listing() -> None:
        listing_col.clear()
        wanted = (term.value or "").strip().lower()
        folders = [f for f in state["here"]["folders"] if wanted in f["name"].lower()]
        files = [f for f in state["here"].get("files") or []
                 if wanted in f["name"].lower() and (keep is None or keep(f))]
        rows = [("folder", one) for one in folders] + [("file", one) for one in files]
        with listing_col:
            if state["naming"] is not None:
                _naming_row()
            if not rows and state["naming"] is None:
                ui.label(empty if empty and not wanted
                         else t("manager.folder_picker.no_folders" if kind == "dir"
                                else "manager.folder_picker.nothing_here")) \
                    .classes("manager-help")
            for what, one in rows[:limit or None]:
                if what == "folder":
                    _folder_row(one)
                elif file_row is not None:
                    file_row(one)
                else:
                    _file_row(one)
            if limit and len(rows) > limit:
                ui.label(t("manager.mediasource.overflow", count=len(rows) - limit)) \
                    .classes("manager-help")

    def _folder_row(folder: dict[str, Any]) -> None:
        row = ui.row().classes("items-center gap-2 w-full no-wrap manager-source-row "
                               "manager-source-row--pick manager-source-row--line")
        with row:
            ui.icon("folder").classes("shrink-0")
            ui.label(folder["name"]).classes("manager-source-name grow")
            extra = folder_extra(folder["path"]) if folder_extra is not None else None
            if extra is not None:
                # The row opens the folder; what is drawn here acts on it instead.
                with ui.element("div").classes("shrink-0") \
                        .on("click", js_handler="(e) => e.stopPropagation()"):
                    extra()
        row.on("click", lambda p=folder["path"]: _go(p))

    def _file_row(one: dict[str, Any]) -> None:
        classes = ("items-center gap-2 w-full no-wrap manager-source-row "
                   "manager-source-row--pick manager-source-row--line")
        if one["path"] == state["selected"]:
            classes += " manager-source-row--chosen"
        row = ui.row().classes(classes)
        with row:
            ui.icon(_ROW_ICON[kind]).classes("shrink-0")
            ui.label(one["name"]).classes("manager-source-name grow")
            if note := notes(one["path"]) if notes is not None else "":
                ui.label(note).classes("manager-help shrink-0")
        row.on("click", lambda p=one["path"]: select(p))
        row.on("dblclick", lambda p=one["path"]: _give(p))

    def _naming_row() -> None:
        """The row a folder is named in, where it will sit, selected for typing as
        Finder and Explorer leave it."""
        with ui.row().classes("items-center gap-2 w-full no-wrap manager-source-row "
                              "manager-source-row--line manager-source-row--chosen"):
            ui.icon("folder").classes("shrink-0")
            typed = ui.input(value=state["naming"]) \
                .props("dense borderless debounce=0").classes("manager-edit-field grow")
        typed.on("keydown.enter", lambda: _make(typed.value))
        # Stopped before the window sees it: Quasar closes the whole dialog on Escape.
        typed.on("keyup.escape.stop", _stop_naming)
        ui.run_javascript(
            "requestAnimationFrame(() => { const field = "
            f"document.getElementById('c{typed.id}'); const input = field && "
            "(field.matches('input') ? field : field.querySelector('input')); "
            "if (input) { input.focus(); input.select(); } })")

    def _start_naming() -> None:
        term.value = ""
        state["naming"] = _free_name(state["here"]["folders"])
        draw_listing()

    def _stop_naming() -> None:
        state["naming"] = None
        draw_listing()

    @on_page
    async def _make(name: str) -> None:
        if make_folder is None:
            return
        try:
            fresh = await offload.io(make_folder, state["here"]["path"], name)
        except Exception as exc:  # noqa: BLE001
            ui.notify(t("manager.folder_picker.make_failed"), caption=why(exc),
                      type="negative")
            return
        _arrive(fresh)

    def _arrive(fresh: dict[str, Any], *, keep_filter: bool = False) -> None:
        state["here"] = fresh
        if not any(f["path"] == state["selected"] for f in fresh.get("files") or []):
            state["selected"] = ""
        state["naming"] = None
        # Clearing a filter redraws the listing from its change handler.
        cleared = not keep_filter and bool(term.value)
        if cleared:
            term.value = ""
        draw_path()
        if not cleared:
            draw_listing()
        ready(_ok(False))

    @on_page
    async def _go(path: str, *, keep_filter: bool = False) -> None:
        try:
            fresh = await offload.io(list_entries, path, kind, suffixes, shown.value)
        except Exception as exc:  # noqa: BLE001
            ui.notify(t("manager.mediasource.folder_failed"), caption=why(exc),
                      type="negative")
            return
        _arrive(fresh, keep_filter=keep_filter)

    def _opens(path: str) -> Callable[[], Any]:
        return lambda: _go(path)

    if saving is not None:
        field["name"] = ui.input(label=t("word.name"), value=saving) \
            .props("dense outlined").classes("manager-picker-name")
        _name_suffix()
        field["name"].on_value_change(_name_changed)
        field["name"].on("keydown.enter", _name_enter)
    draw_path()
    draw_listing()
    term.on_value_change(draw_listing)
    shown.on_value_change(lambda: _go(state["here"]["path"], keep_filter=True))

    return Browser(_choose, _start_naming, lambda: state["selected"],
                   can_name=make_folder is not None, can_choose=lambda: _ok(False))
