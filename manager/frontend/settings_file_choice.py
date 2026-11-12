"""Where a launcher's settings come from, asked in the dialogs that make or change one.

Three ways to a settings file - copied from a launcher or a file, made new by the program,
or one already on disk - and where a new file is saved. New Launcher, Duplicate and Change
Settings File all ask it with the same two rows, so one object draws them and answers with
the body the service takes.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Awaitable, Callable
from typing import Any

from nicegui import ui

from common.games import settings_file_name
from common.i18n import t
from manager.kit import confirm, folder_picker, offload, panel, undo, verbs
from manager.kit import dialog as frame
from manager.kit.data import Library
from manager.kit.on_page import on_page

logger = logging.getLogger("vpinfe.manager.frontend.settings_file_choice")

COPY, FRESH, PICK = "copy", "fresh", "pick"
NEW, DUPLICATE, CHANGE = "new", "duplicate", "change"

_FILE, _BROWSE = "file", "browse"


def same_path(one: str, other: str) -> bool:
    if not one or not other:
        return False
    return os.path.normcase(os.path.abspath(one)) == os.path.normcase(os.path.abspath(other))


def names_in(names: list[str]) -> str:
    if len(names) < 2:
        return "".join(names)
    joiner = t("manager.launchers.sf.list_join")
    return t("manager.launchers.sf.names", rest=joiner.join(names[:-1]), last=names[-1])


class Choice:
    """The state of the two rows, drawn into a `panel.facts` list by `rows()`.

    `name()` answers what the launcher is called now, so a Save To that has not been
    picked follows it as it is typed. `fallback` is the name used while that is empty.
    """

    def __init__(self, library: Library, *, app: dict, held: dict, places: list[dict],
                 mode: str, name: Callable[[], str], fallback: str,
                 launcher: dict | None = None) -> None:
        self.library = library
        self.app = app
        self.mode = mode
        self.name = name
        self.fallback = fallback
        self.launcher = launcher
        self.launchers: list[dict] = list(held.get("launchers") or [])
        self.places = places
        self.default_dir = next((str(one["path"]) for one in places if one.get("default")), "")
        besides = str(launcher["launcher_id"]) if launcher and mode == CHANGE else ""
        self.others = [one for one in self.launchers
                       if one["app"] == app["id"] and one["launcher_id"] != besides]
        self.copy_from = self._first_source(held, launcher)
        self.copy_file = ""
        self.existing = ""
        self.save_dir = self._first_folder()
        self.file_name = ""
        self.how = COPY if self.others else FRESH
        self.controls: dict[str, Any] = {}
        self.radios: dict[str, Any] = {}
        self._syncing = False

    def _first_source(self, held: dict, launcher: dict | None) -> str:
        if self.mode == DUPLICATE and launcher:
            return str(launcher["launcher_id"])
        known = {str(one["launcher_id"]) for one in self.others}
        wanted = str((held.get("defaults") or {}).get(self.app["id"]) or "")
        if wanted in known:
            return wanted
        return str(self.others[0]["launcher_id"]) if self.others else ""

    def _first_folder(self) -> str:
        """Where a new file goes: beside the one the launcher keeps for itself, else VPinFE's
        own folder."""
        mine = str((self.launcher or {}).get("settings_file") or "")
        if self.mode == CHANGE and mine and (self.launcher or {}).get("owns_ini"):
            return folder_picker.enclosing(mine) or self.default_dir
        return self.default_dir

    # What the rows hold

    def default_name(self) -> str:
        return (settings_file_name.from_display_name(self.name())
                or settings_file_name.from_display_name(self.fallback))

    def save_path(self) -> str:
        return folder_picker.join(self.save_dir, self.file_name or self.default_name()) \
            if self.save_dir else ""

    def users_of(self, path: str) -> list[str]:
        """The launchers that play with `path`, other than the one being changed."""
        mine = str((self.launcher or {}).get("launcher_id") or "") if self.mode == CHANGE else ""
        return [str(one["display_name"]) for one in self.launchers
                if one["launcher_id"] != mine
                and same_path(str(one.get("settings_file") or ""), path)]

    def note(self, path: str) -> str:
        users = self.users_of(path)
        return t("manager.launchers.sf.uses", count=len(users), names=names_in(users)) \
            if users else ""

    def body(self) -> dict[str, Any]:
        found: dict[str, Any] = {"choice": self.how}
        if self.how == COPY:
            found["copy_file" if self.copy_from == _FILE else "copy_from"] = \
                self.copy_file if self.copy_from == _FILE else self.copy_from
        if self.how == PICK:
            found["existing"] = self.existing
        else:
            found["save_dir"] = self.save_dir
            found["file_name"] = self.file_name or self.default_name()
        return found

    # Drawing

    def rows(self) -> list[tuple[Any, ...]]:
        sharing = [str(one) for one in (self.launcher or {}).get("shared_with") or []] \
            if self.mode == CHANGE else []
        return [*([(t("manager.launchers.sf.sharers"), names_in(sharing))] if sharing else []),
                (t("manager.launchers.sf.label"), self._choices),
                (t("manager.launchers.sf.save_to"), self._save)]

    def follow_name(self) -> None:
        """The launcher's name changed: a Save To nobody picked changes with it."""
        save = self.controls.get("save")
        if save is not None and not self.file_name and not save.is_deleted:
            save.value = self.save_path()

    def _choices(self) -> None:
        with ui.column().classes("manager-fact-edit manager-choices gap-2"):
            with ui.column().classes("manager-choice gap-1"):
                self._radio(COPY, t("manager.launchers.sf.copy"), enabled=bool(self.others))
                with ui.element("div").classes("manager-choice-more"):
                    panel.select(self._copy_options(), self.copy_from or _BROWSE,
                                 self._copied, on_control=self._hold("copy"))()
            with ui.column().classes("manager-choice gap-1"):
                self._radio(FRESH, t("manager.launchers.sf.fresh"))
            with ui.column().classes("manager-choice gap-1"):
                self._radio(PICK, t("manager.launchers.sf.existing"))
                with ui.element("div").classes("manager-choice-more"):
                    box = frame.field(
                        self.existing, placeholder=t("manager.launchers.sf.choose"),
                        browse=self._browse_existing, room=True, readonly=True,
                        name=t("manager.launchers.sf.existing"),
                        browse_tip=t("manager.launchers.sf.choose_tip"))
                    self.controls["existing"] = box
        self._enable()

    def _radio(self, key: str, label: str, *, enabled: bool = True) -> None:
        radio = ui.radio({key: label}, value=key if self.how == key else None).props("dense")
        radio.on_value_change(lambda event, key=key: self._chose(key) if event.value else None)
        radio.set_enabled(enabled)
        self.radios[key] = radio

    def _hold(self, key: str) -> Callable[[Any], None]:
        def keep(control: Any) -> None:
            self.controls[key] = control
        return keep

    def _chose(self, key: str) -> None:
        if self._syncing:
            return
        self.how = key
        self._syncing = True
        for other, radio in self.radios.items():
            radio.value = key if other == key else None
        self._syncing = False
        self._say_existing("")
        self._enable()

    def _enable(self) -> None:
        for key, on in ((("copy", self.how == COPY), ("existing", self.how == PICK),
                         ("save", self.how != PICK))):
            if (control := self.controls.get(key)) is not None and not control.is_deleted:
                control.set_enabled(on)
        self._also()

    def _save(self) -> None:
        box = frame.field(self.save_path(), browse=self._browse_save, readonly=True,
                          name=t("manager.launchers.sf.save_to"),
                          browse_tip=t("manager.launchers.sf.save_tip"))
        self.controls["save"] = box
        box.set_enabled(self.how != PICK)

    # The copy list

    def _copy_options(self) -> dict[str, str]:
        options = {str(one["launcher_id"]): str(one["display_name"]) for one in self.others}
        if self.copy_file:
            options[_FILE] = folder_picker.last_name(self.copy_file)
        options[_BROWSE] = t("manager.launchers.sf.browse")
        return options

    @on_page
    async def _copied(self, event: Any) -> None:
        if self._syncing:
            return
        if event.value != _BROWSE:
            self.copy_from = str(event.value)
            return
        picked = await self._pick_file(self.copy_file or self.default_dir)
        control = self.controls["copy"]
        self._syncing = True
        try:
            if picked:
                self.copy_file, self.copy_from = picked, _FILE
            control.set_options(self._copy_options(), value=self.copy_from or _BROWSE)
        finally:
            self._syncing = False

    # The pickers

    async def _pick_file(self, start: str) -> str | None:
        return await folder_picker.pick_path(
            self.library.folders, folder_picker.enclosing(start) or start,
            kind="file", suffixes=(settings_file_name.SUFFIX,), nearest=True,
            title=t("manager.launchers.sf.picker_title"), lead=self.places,
            empty=t("manager.launchers.sf.none"), notes=self.note)

    async def _browse_existing(self, current: str) -> str | None:
        if self.how != PICK:
            return None
        start = current or str((self.launcher or {}).get("settings_file") or "") or self.default_dir
        picked = await self._pick_file(start)
        if picked:
            self.existing = picked
            self._say_existing("")
            self._also(picked)
        return picked

    async def _browse_save(self, _current: str) -> str | None:
        if self.how == PICK:
            return None
        name = self.file_name or self.default_name()
        stem = name[:-len(settings_file_name.SUFFIX)] \
            if name.lower().endswith(settings_file_name.SUFFIX) else name
        picked = await folder_picker.pick_path(
            self.library.folders, self.save_dir, kind="file",
            suffixes=(settings_file_name.SUFFIX,), nearest=True,
            title=t("manager.launchers.sf.save_to"), lead=self.places,
            empty=t("manager.launchers.sf.none"), notes=self.note, saving=stem)
        if picked:
            self.save_dir = folder_picker.enclosing(picked)
            self.file_name = folder_picker.last_name(picked)
        return picked

    # Under the Existing box: what is wrong with it, or who else uses the file

    def _say_existing(self, message: str) -> None:
        box = self.controls.get("existing")
        if box is not None and not box.is_deleted:
            frame.said(box, message)

    def _also(self, path: str = "") -> None:
        box = self.controls.get("existing")
        if box is None or box.is_deleted:
            return
        found = path or self.existing
        users = self.users_of(found) if self.how == PICK and found else []
        text = t("manager.launchers.sf.also", count=len(users), names=names_in(users)) \
            if users else ""
        if text:
            box.props["hint"] = text
        else:
            box.props.pop("hint", None)
        box.update()

    def refuse(self) -> bool:
        """Say what is missing, and whether the dialog can go on."""
        if self.how == PICK and not self.existing:
            self._say_existing(t("error.launchers.pick_missing"))
            return True
        return False

    # Doing it

    async def apply(self, launcher_id: str, *, create: dict | None = None) -> dict | None:
        """Give the launcher this file. None where somebody declined to write over settings
        others use; a refusal raises."""
        body = self.body()
        if create is not None:
            body["create"] = create
        answer = await offload.io(self.library.set_launcher_settings_file, launcher_id, body)
        if (asked := answer.get("needs_replace")) is None:
            return answer
        if not await self._replace(asked):
            return None
        return await offload.io(self.library.set_launcher_settings_file, launcher_id,
                                {**body, "replace": True})

    def _replace_words(self, asked: dict) -> tuple[str, str]:
        users = [str(one) for one in asked.get("users") or []]
        if asked.get("program_file"):
            app = self.app["name"]
            detail = (t("manager.launchers.sf.replace_own_detail", app=app, names=names_in(users))
                      if users else t("manager.launchers.sf.replace_solo", app=app))
            return t("manager.launchers.sf.replace_own", app=app), detail
        if len(users) == 1:
            return (t("manager.launchers.sf.replace_one", name=users[0]),
                    t("manager.launchers.sf.replace_one_detail", name=users[0]))
        if users:
            detail = (t("manager.launchers.sf.replace_two_detail") if len(users) == 2
                      else t("manager.launchers.sf.replace_many_detail", count=len(users)))
            return t("manager.launchers.sf.replace_some", names=names_in(users)), detail
        return (t("manager.launchers.sf.replace_there"),
                t("manager.launchers.sf.replace_there_detail"))

    async def _replace(self, asked: dict) -> bool:
        question, detail = self._replace_words(asked)
        return await confirm.ask(question, detail=detail, confirm=t("word.replace"),
                                 icon=verbs.REPLACE)


def words_for(answer: dict, how: str, mode: str) -> str:
    """What the toast says after a change, or "" where nothing needs saying."""
    replaced, kept = bool(answer.get("replaced")), bool(answer.get("backed_up"))
    if replaced:
        said = t("manager.launchers.sf.done_replaced")
    elif how == FRESH:
        said = t("manager.launchers.sf.done_created")
    elif how == COPY and mode == CHANGE:
        said = t("manager.launchers.sf.done_copied")
    elif mode == CHANGE:
        said = t("manager.launchers.sf.done_changed")
    else:
        said = ""
    if kept:
        return t("manager.launchers.sf.done_and_backups", said=said) if said \
            else t("manager.launchers.sf.done_backups")
    return said


def announce(library: Library, answer: dict, how: str, mode: str,
             after: Callable[[], Awaitable[Any]] | None = None) -> None:
    """Say what was done; with Undo where the service kept what it changed."""
    said = words_for(answer, how, mode)
    token = str(answer.get("undo") or "")
    if not said:
        return
    if not token:
        ui.notify(said, type="positive")
        return

    async def reverse() -> None:
        await offload.io(library.undo_launcher_settings_file, token)
        if after is not None:
            await after()

    undo.offer(said, reverse)
