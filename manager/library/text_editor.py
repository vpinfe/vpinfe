"""The text editor in a file's stage: the only place the Manager draws `ui.codemirror`.

The text a person has typed and not saved is a draft, kept per file in the page's state
so it survives selecting something else. Leaving the view or reloading asks about every
draft, and the Assets grid marks the file.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import urlencode

from nicegui import ui
from nicegui.client import Client
from nicegui.elements.codemirror.constants import SUPPORTED_LANGUAGES, SUPPORTED_THEMES

from common.failures import why
from common.i18n import t
from manager.kit import appearance, confirm, offload, panel, verbs
from manager.kit import dialog as frame
from manager.kit.api import ApiError
from manager.kit.on_page import on_page

logger = logging.getLogger("vpinfe.manager.library.text_editor")

# The kinds whose files this edits, and the language each is highlighted as. A rule sheet
# is a media kind and its language follows the file: see `language_of`.
LANGUAGES: dict[str, SUPPORTED_LANGUAGES | None] = {"script": "VBScript", "ini": "Properties files",
                                    "pov": "XML", "scv": None, "nix": None, "patch": "diff",
                                    "readme": "Markdown", "rule_sheet": None}
WRAPPED = frozenset({"readme", "rule_sheet"})
# Media kinds: a save files its copy under Backups > Media, and the file has no window of
# its own, which is addressed by an asset kind.
MEDIA_KINDS = frozenset({"rule_sheet"})
_RULE_SHEET_LANGUAGES: dict[str, SUPPORTED_LANGUAGES] = {".md": "Markdown", ".html": "HTML"}
CHANGED, NO_COPY = "changed", "no_copy"

# The browser's own question, for a reload or a link out while a rule or a file is unsaved.
# Whoever draws an editor runs this once and `flag` keeps `__hubUnsaved` current.
ASK_BEFORE_UNLOAD = """
window.addEventListener('beforeunload', (event) => {
  if (!window.__hubUnsaved) return;
  event.preventDefault();
  event.returnValue = '';
});
"""

# A window of its own for one file. The page behind this address is `edit_page`.
ADDRESS = "/manager/edit"
WINDOW_FEATURES = "popup,width=1000,height=800"

# Raised when a tab or window comes back into view, which is when the other one may have
# saved the file.
RETURN_EVENT = "hub_editor_shown"
LISTEN_FOR_RETURN = f"""
document.addEventListener('visibilitychange', () => {{
  if (!document.hidden) emitEvent({json.dumps(RETURN_EVENT)});
}});
window.addEventListener('focus', () => emitEvent({json.dumps(RETURN_EVENT)}));
"""

DARK_EVENT = "hub_editor_dark"
_WATCH_DARK = ("Vue.watch(() => Quasar.Dark.isActive, dark => emitEvent("
               f"{json.dumps(DARK_EVENT)}, dark), {{immediate: true}});")
_THEMES: dict[bool, SUPPORTED_THEMES] = {True: "basicDark", False: "basicLight"}

# Per page: whether Quasar is drawing dark, which the editor's theme follows.
_DARK: dict[Client, bool] = {}


def draft_key(game_id: str, path: str) -> tuple[str, str]:
    return (game_id, path)


def edits(kind: str) -> bool:
    return kind in LANGUAGES


def language_of(kind: str, path: str) -> SUPPORTED_LANGUAGES | None:
    if kind == "rule_sheet":
        return _RULE_SHEET_LANGUAGES.get(PurePosixPath(path).suffix.lower())
    return LANGUAGES[kind]


def wire() -> Callable[[Any], None]:
    """Follow the page's dark switch, once as the page is built: the handler for
    `DARK_EVENT`, which the page registers. A mode change repaints through Quasar without
    a rebuild, and the editors already open have to follow it."""
    client = ui.context.client
    _DARK[client] = appearance.QUASAR_DARK.get(appearance.DEFAULT_MODE) is not False

    def changed(event: Any) -> None:
        _DARK[client] = bool(event.args)
        for element in list(client.elements.values()):
            if isinstance(element, ui.codemirror):
                element.theme = _THEMES[_DARK[client]]

    ui.run_javascript(_WATCH_DARK)
    client.on_delete(lambda: _DARK.pop(client, None))
    return changed


def window_address(game_id: str, kind: str, path: str) -> str:
    return ADDRESS + "?" + urlencode({"game": game_id, "kind": kind, "path": path})


def window_name(game_id: str, path: str) -> str:
    """The same file opens the same window again."""
    return "vpinfe-edit-" + hashlib.sha1(f"{game_id}\0{path}".encode()).hexdigest()[:16]


def rereads_of(state: dict[str, Any]) -> dict[tuple[str, str], Callable[[], Any]]:
    return state.setdefault("text_rereads", {})


async def shown_again(state: dict[str, Any]) -> None:
    """Read again every file this page holds an editor for, as far as each can be: one
    with changes typed is left to the save's own check."""
    for again in list(rereads_of(state).values()):
        await again()


def drafts_of(state: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    return state.setdefault("text_drafts", {})


def waiting(state: dict[str, Any]) -> list[str]:
    """The files with changes not saved, as the leave question lists them."""
    return sorted(str(one["name"]) for one in drafts_of(state).values())


def note(state: dict[str, Any]) -> None:
    """After a draft starts or ends: the grid's marks and the browser's reload question."""
    for hook in ("mark_unsaved_files", "mark_unsaved_media"):
        mark = state.get(hook)
        if callable(mark):
            mark(set(drafts_of(state)))
    flag(state)


def flag(state: dict[str, Any]) -> None:
    """Whether the browser asks before a reload: any draft of either kind."""
    held = bool(state.get("unsaved_rules") or drafts_of(state))
    ui.run_javascript(f"window.__hubUnsaved = {json.dumps(held)};")


def drop_all(state: dict[str, Any]) -> None:
    drafts_of(state).clear()
    note(state)


def show_whole(label: str, text: str) -> None:
    """A file that is not edited, read whole in the full dialog."""
    with frame.opened("", size="full") as dialog:
        with ui.row().classes("items-center gap-2 w-full no-wrap"):
            ui.label(label).classes("manager-dialog-title")
            ui.space()
            panel.icon_only(verbs.CLOSE, dialog.close, hint=t("word.close"))
        with ui.element("div").classes("manager-editor"):
            with ui.element("div").classes("manager-editor-box"):
                ui.codemirror(text, theme=_THEMES[_DARK.get(ui.context.client, True)]).disable()
    dialog.open()


@dataclass
class Held:
    """What the file has on disk as far as the page knows, and whether a save is going.
    One per file on a page, shared by the stage's editor and the enlarged one over it.
    `views` redraw each editor's strip and `syncs` set each editor's text."""

    base: str
    version: str
    saving: bool = False
    views: list[Callable[[], None]] = field(default_factory=list)
    syncs: list[Callable[[], None]] = field(default_factory=list)

    def touch(self) -> None:
        for view in list(self.views):
            view()

    def reload(self) -> None:
        """The file changed under every editor on it: each takes the text again."""
        for sync in list(self.syncs):
            sync()
        self.touch()


def _closing(context: dict[str, Any], dialog: Any) -> dict[str, Any]:
    """The context for an enlarged editor: where an editor opened in place goes back to the
    page behind it on Cancel or Save, the dialog closes too."""
    if not callable(context.get("after_cancel")):
        return context

    def closes(after: Any) -> Any:
        async def run() -> None:
            dialog.close()
            if callable(after):
                await after()
        return run

    return {**context, "after_cancel": closes(context["after_cancel"]),
            "after_save": closes(context.get("after_save"))}


def editor(context: dict[str, Any], path: str, label: str, found: dict[str, Any],
           locked: str, locked_help: str = "", *,
           held: Held | None = None) -> None:
    """The file's text in an editor with its strip under it. `found` is what the API read,
    and `locked` the reason it cannot be saved, or blank.

    Enlarge draws a second editor over the same draft, passing the stage's `held` (the text
    and version the file has on disk) so a save in either moves both. The stage's editor is
    not touched until the enlarged one closes, and then it is brought up to date.
    """
    state, library = context["state"], context["library"]
    game_id, kind = context["game_id"], context["kind"]
    enlarged = held is not None
    windowed = bool(context.get("windowed"))
    key = draft_key(game_id, path)
    drafts = drafts_of(state)
    draft = drafts.get(key)
    if held is None:
        held = Held(draft["base"] if draft else found["text"],
                    draft["version"] if draft else found["version"])
    name = str((context.get("game") or {}).get("name") or game_id) + " - " + label
    # An editor opened in place is left through Cancel, with or without a change typed.
    leaves = callable(context.get("after_cancel"))
    big: ui.button | None = None
    opener: ui.button | None = None
    tip: ui.tooltip | None = None

    def current() -> str:
        return str(drafts[key]["text"]) if key in drafts else str(held.base)

    def show() -> None:
        if said.is_deleted:
            return
        dirty = key in drafts
        said.set_visibility(dirty)
        cancel.set_visibility(dirty or not locked or leaves)
        cancel.set_enabled((dirty or leaves) and not held.saving)
        save.set_enabled(dirty and not held.saving)
        if big is not None:
            big.set_enabled(not held.saving)
        if opener is not None and tip is not None:
            free = not dirty and not held.saving
            opener.set_enabled(free)
            tip.set_text(t("manager.text_editor.open_window.unsaved") if dirty
                         else t("manager.text_editor.open_window"))

    def sync() -> None:
        if not code.is_deleted and code.value != current():
            code.value = current()

    def catch_up() -> None:
        sync()
        held.touch()

    def enlarge() -> None:
        with frame.opened("", size="full") as dialog:
            with ui.row().classes("items-center gap-2 w-full no-wrap"):
                with ui.column().classes("gap-0 min-w-0"):
                    ui.label(label).classes("manager-dialog-title")
                    ui.label(str((context.get("game") or {}).get("name") or game_id)) \
                        .classes("manager-help truncate")
                ui.space()
                panel.icon_only(verbs.CLOSE, dialog.close, hint=t("word.close"))
            editor(_closing(context, dialog), path, label, found, locked, locked_help,
                   held=held)
        dialog.on("hide", catch_up)
        dialog.open()

    def changed(event: Any) -> None:
        text = str(event.value or "")
        was = key in drafts
        if text == held.base:
            drafts.pop(key, None)
        else:
            drafts[key] = {"text": text, "base": held.base, "version": held.version,
                           "name": name}
        held.touch()
        if was != (key in drafts):
            note(state)

    async def notify_failed(exc: Exception) -> None:
        ui.notify(t("manager.workbench.save_failed"), caption=why(exc), type="negative")

    async def write(replace_changed: bool = False, keep_copy: bool = True) -> None:
        if locked or held.saving or key not in drafts:
            return
        text = drafts[key]["text"]
        held.saving = True
        held.touch()
        try:
            await send(text, replace_changed, keep_copy)
        finally:
            held.saving = False
            held.touch()

    async def send(text: str, replace_changed: bool, keep_copy: bool) -> None:
        try:
            done = await offload.io(library.save_asset_text, game_id, path, text,
                                    held.version, replace_changed, keep_copy,
                                    kind in MEDIA_KINDS)
        except ApiError as exc:
            refused = exc.details.get("refused")
            if refused == CHANGED:
                if await confirm.ask(t("manager.text_editor.changed"),
                                     detail=t("manager.text_editor.changed.help"),
                                     confirm=t("manager.text_editor.save_anyway"),
                                     icon=verbs.SAVE, danger=False):
                    await send(text, True, keep_copy)
            elif refused == NO_COPY:
                if await confirm.ask(t("manager.text_editor.no_copy"), detail=str(exc),
                                     confirm=t("manager.text_editor.save_without"),
                                     icon=verbs.SAVE, danger=False):
                    await send(text, replace_changed, False)
            else:
                await notify_failed(exc)
            return
        except Exception as exc:  # noqa: BLE001 - the draft stays; the save can be tried again
            logger.debug("Could not save %s", path, exc_info=True)
            await notify_failed(exc)
            return
        held.base, held.version = text, done["version"]
        typed = drafts.get(key)
        if typed is not None and typed["text"] != text:
            typed["base"], typed["version"] = held.base, held.version
        else:
            drafts.pop(key, None)
        note(state)
        ui.notify(t("manager.text_editor.saved"), type="positive")
        for after in (context.get("saved"), context.get("after_save")):
            if callable(after):
                await after()

    @on_page
    async def save_it() -> None:
        await write()

    async def reread() -> None:
        if code.is_deleted:
            rereads_of(state).pop(key, None)
            return
        if key in drafts or held.saving:
            return
        was = held.version
        try:
            fresh = await offload.io(library.asset_text, game_id, path)
        except Exception:  # noqa: BLE001 - what is shown stays as it was
            logger.debug("Could not read %s again", path, exc_info=True)
            return
        if fresh["version"] == held.version or held.version != was or key in drafts \
                or held.saving:
            return
        held.base, held.version = fresh["text"], fresh["version"]
        held.reload()
        after = context.get("after_save")
        if callable(after):
            await after()

    @on_page
    async def cancel_it() -> None:
        if held.saving:
            return
        try:
            fresh = await offload.io(library.asset_text, game_id, path)
        except Exception as exc:  # noqa: BLE001 - the text it had is the text it goes back to
            logger.debug("Could not read %s again", path, exc_info=True)
            ui.notify(t("manager.workbench.read_failed"), caption=why(exc), type="negative")
        else:
            held.base, held.version = fresh["text"], fresh["version"]
        drafts.pop(key, None)
        code.value = held.base
        held.touch()
        note(state)
        if callable(after := context.get("after_cancel")):
            await after()

    with ui.element("div").classes("manager-editor"):
        with ui.row().classes("items-center gap-2 w-full no-wrap manager-editor-strip"):
            said = ui.label(t("word.not_saved")).classes("manager-draft-state") \
                .tooltip(t("manager.text_editor.not_saved.help"))
            if locked:
                panel.line(locked, hint=locked_help, classes="manager-help truncate")
            with ui.row().classes("items-center gap-1 no-wrap ml-auto"):
                if not windowed and kind not in MEDIA_KINDS:
                    with ui.element("span") as shelf:
                        opener = panel.icon_only(verbs.OPEN_WINDOW, lambda: None)
                        opener.on("click", js_handler=(
                            f"() => window.open({json.dumps(window_address(game_id, kind, path))}, "
                            f"{json.dumps(window_name(game_id, path))}, "
                            f"{json.dumps(WINDOW_FEATURES)})"))
                    with shelf:
                        tip = ui.tooltip(t("manager.text_editor.open_window"))
                if not enlarged and not windowed:
                    big = panel.icon_only(verbs.ENLARGE, enlarge, hint=t("word.enlarge"))
                cancel = panel.draft_button(t("word.cancel"), cancel_it, icon=verbs.CANCEL,
                                            hint=t("manager.text_editor.cancel.help"))
                save = panel.draft_button(t("word.save"), save_it, icon=verbs.SAVE,
                                          hint=t("manager.text_editor.save.help"),
                                          primary=True)
            if locked:
                save.set_visibility(False)
        with ui.element("div").classes("manager-editor-box"):
            code = ui.codemirror(
                current(), language=language_of(kind, path),
                theme=_THEMES[_DARK.get(ui.context.client, True)],
                line_wrapping=kind in WRAPPED, on_change=changed,
                keymap={"Mod-s": save_it})
            if locked:
                code.disable()
    held.views.append(show)
    held.syncs.append(sync)
    if not enlarged:
        rereads_of(state)[key] = reread
    show()
