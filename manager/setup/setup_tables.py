"""Setup's Tables step: where the games are, and the first library folder.

Three answers. **In a folder** is the importer's Folders source with its paths worked out:
a folder of game folders is the library folder itself, and a folder holding loose tables
carries on into the importer's own steps. **In another frontend** goes straight to them.
**None yet** makes the library folder from a folder picked, or made, in the picker.

The importer's steps are asked in Setup's frame as `tables.<its step>`, over the calls
`manager/extensions/ext_action.py` binds; Setup finishes when its job does.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nicegui import background_tasks, ui

from common.config.path_state import OK as PATH_OK
from common.games import library_folders as model
from common.games import tables
from common.i18n import t
from manager.extensions import ext_action
from manager.kit import offload, panel, wizard
from manager.kit.api import ApiClient
from manager.settings.library_folders import IMPORTER

logger = logging.getLogger("vpinfe.manager.setup.setup_tables")

WHERE = "tables_where"
IN_A_FOLDER = "folder"
FROM_A_FRONTEND = "frontend"
NONE_YET = "none"
FOLDER = "tables_folder"
FOLDER_CHECKED = "tables_folder_checked"
SURVEYED = "tables_surveyed"
NEW_FOLDER = "tables_new"
NEW_CHECKED = "tables_new_checked"

PREFIX = "tables."
IMPORT = "import"
IMPORT_KEY = "import"
FOLDERS_SOURCE = "folders"
PATHS_STEP = "sources"
SOURCE_STEP = "source"


@dataclass
class Tables:
    """What Setup knows about the library, read when it opens."""
    # The importer's import action, where that extension is running.
    action: dict | None = None
    games: int = 0
    locations: list[dict[str, Any]] = field(default_factory=list)
    # The Visual Pinball program, which the first folder offered is found beside.
    program: str = ""


def gather(program: str = "") -> Tables:
    """Blocking."""
    client = ApiClient()
    found = Tables(program=program)
    try:
        importer = next((one for one in client.extensions() if one.get("name") == IMPORTER), {})
        found.action = next((one for one in importer.get("actions") or []
                             if one.get("key") == IMPORT_KEY), None)
    except Exception:
        logger.warning("Could not look for the importer", exc_info=True)
    try:
        found.locations = list(client.locations().get("locations") or [])
        found.games = len(client.games())
    except Exception:
        logger.warning("Could not read the library", exc_info=True)
    return found


def shows_library(found: Tables, *, again: bool) -> bool:
    """Where there is a library already, the step shows it instead of asking: run again
    with one, or Setup reopened after an import that was left running."""
    return bool(found.locations) and (again or found.games > 0)


def tables_beside(program: str) -> str:
    """Visual Pinball's own Tables folder: the one next to the program. Blocking."""
    if not program:
        return ""
    try:
        for one in Path(program).parent.iterdir():
            if one.is_dir() and one.name.lower() == "tables":
                return str(one)
    except OSError:
        pass
    return ""


def _in_folder(values: dict[str, Any]) -> dict[str, Any] | None:
    """What was found in the folder typed under In a folder, once it has been."""
    said = str(values.get(FOLDER) or "").strip()
    held = values.get(SURVEYED)
    if held and held[0] == said:
        return dict(held[1])
    return None


def _checked(values: dict[str, Any], path_key: str, state_key: str) -> bool:
    said = str(values.get(path_key) or "").strip()
    return bool(said) and tuple(values.get(state_key) or ()) == (said, PATH_OK)


def _where(values: dict[str, Any]) -> str:
    return str(values.get(WHERE) or IN_A_FOLDER)


def complete(values: dict[str, Any], *, importer: bool = True) -> bool:
    """A folder with loose tables needs the importer to carry on."""
    where = _where(values)
    if where == NONE_YET:
        return _checked(values, NEW_FOLDER, NEW_CHECKED)
    if where == IN_A_FOLDER:
        found = _in_folder(values)
        return (_checked(values, FOLDER, FOLDER_CHECKED) and found is not None
                and bool(found["game_folders"] or (found["loose"] and importer)))
    return importer


def ends_here(values: dict[str, Any]) -> bool:
    """Whether the answer as it stands needs nothing more than Finish: a folder of game
    folders, or a folder to start from."""
    where = _where(values)
    if where == NONE_YET:
        return True
    if where == IN_A_FOLDER:
        found = _in_folder(values)
        return found is not None and not found["loose"]
    return False


def _found_line(found: dict[str, Any]) -> tuple[str, str, str]:
    """The icon, the finding and what happens to it, for a folder's survey."""
    folders, loose = len(found["game_folders"]), len(found["loose"])
    if not folders and not loose:
        return "error", t("manager.setup.tables.found_none"), ""
    if not loose:
        return ("check_circle", t("manager.setup.tables.found_folders", count=folders),
                t("manager.setup.tables.found_folders_help"))
    if not folders:
        return ("info", t("manager.setup.tables.found_loose", count=loose),
                t("manager.setup.tables.found_loose_help"))
    return ("info", t("manager.setup.tables.found_mixed", count=folders, loose=loose),
            t("manager.setup.tables.found_mixed_help", count=loose))


def _folder_detail(client: ApiClient,
                   start: str) -> Callable[[dict[str, Any], Callable[[], None]], None]:
    def draw(values: dict[str, Any], changed: Callable[[], None]) -> None:
        values.setdefault(FOLDER, start)
        says = ui.element("div").classes("w-full")

        def show(found: dict[str, Any]) -> None:
            says.clear()
            icon, line, help_text = _found_line(found)
            with says, ui.element("div").classes("manager-wizard-found"):
                ui.icon(icon)
                with ui.column().classes("gap-0"):
                    ui.label(line).classes("manager-source-name")
                    if help_text:
                        ui.label(help_text).classes("manager-help")

        async def look(said: str) -> None:
            found = await offload.io(tables.survey, said)
            if str(values.get(FOLDER) or "").strip() != said:
                return
            values[SURVEYED] = (said, found)
            show(found)
            changed()

        def checked(state: str, said: str) -> str:
            values[FOLDER_CHECKED] = (said, state)
            if state == PATH_OK:
                background_tasks.create(look(said), name="setup-tables-survey")
            else:
                says.clear()
            changed()
            return ""

        def edited(event: Any) -> None:
            values[FOLDER] = str(event.value or "")
            changed()

        panel.path_field(t("manager.setup.tables.folder_placeholder"), wants="dir",
                         value=str(values[FOLDER] or ""), width="w-full", on_checked=checked,
                         browse=client.folders,
                         opens_at=start).on_value_change(edited)

    return draw


def _new_detail(client: ApiClient) -> Callable[[dict[str, Any], Callable[[], None]], None]:
    def draw(values: dict[str, Any], changed: Callable[[], None]) -> None:
        values.setdefault(NEW_FOLDER, "")

        def checked(state: str, said: str) -> str:
            values[NEW_CHECKED] = (said, state)
            changed()
            return ""

        def edited(event: Any) -> None:
            values[NEW_FOLDER] = str(event.value or "")
            changed()

        panel.path_field(t("manager.setup.tables.new_placeholder"), wants="dir",
                         value=str(values[NEW_FOLDER] or ""), width="w-full", on_checked=checked,
                         browse=client.folders).on_value_change(edited)

    return draw


def _facts(found: Tables) -> Callable[[dict[str, Any]], None]:
    def draw(_values: dict[str, Any]) -> None:
        panel.facts(ui, [(one["name"] or one["path"], one["path"]) for one in found.locations])

    return draw


async def ask(found: Tables, *, again: bool) -> dict[str, Any]:
    """The step: what is there where there is a library, else the three answers."""
    if shows_library(found, again=again):
        step: dict[str, Any] = {
            "title": t("manager.setup.tables.title_have"),
            "help": t("manager.setup.tables.have", count=found.games),
            "draw": _facts(found)}
        if found.action is not None:
            step["acts"] = [{"key": IMPORT, "label": t("manager.setup.tables.import"),
                             "hint": t("manager.setup.tables.import_hint")}]
        return step
    client = ApiClient()
    start = await offload.io(tables_beside, found.program)
    choices: list[tuple[str, str, str, str]] = [
        (IN_A_FOLDER, t("manager.setup.tables.folder"), t("manager.setup.tables.folder_help"),
         "folder")]
    if found.action is not None:
        choices.append((FROM_A_FRONTEND, t("manager.setup.tables.frontend"),
                        t("manager.setup.tables.frontend_help"), "dvr"))
    choices.append((NONE_YET, t("manager.setup.tables.none"),
                    t("manager.setup.tables.none_help"), "create_new_folder"))
    title = t("manager.setup.tables.title")
    return {
        "title": title,
        "fields": [{
            "key": WHERE, "type": "choice", "label": title, "value": IN_A_FOLDER,
            "choices": choices,
            "details": {IN_A_FOLDER: _folder_detail(client, start),
                        NONE_YET: _new_detail(client)},
        }],
        "gate": lambda values: complete(values, importer=found.action is not None),
        "ends": ends_here,
    }


async def answer(found: Tables, values: dict[str, Any], *, again: bool) -> None:
    """Make the library folder for the answers that end in Finish. The importer makes its
    own, so reaching Finish with any other answer is a defect, not a no-op."""
    if shows_library(found, again=again):
        return
    where = _where(values)
    if where == IN_A_FOLDER and ends_here(values):
        path = str(values[FOLDER]).strip()
    elif where == NONE_YET:
        path = str(values.get(NEW_FOLDER) or "").strip()
    else:
        raise RuntimeError(f"Finish was reached on the Tables answer {where!r}")
    await offload.io(ApiClient().put_location, model.mint_location_id(),
                     {"path": path, "kind": model.KIND_ROOT})


# ---- the importer's steps, in Setup's frame ------------------------------------------


def _calls(found: Tables) -> wizard.Calls:
    assert found.action is not None
    return ext_action.calls(IMPORTER, found.action)


def inside(key: str | None) -> bool:
    return bool(key) and str(key).startswith(PREFIX)


def _named(step: dict[str, Any]) -> dict[str, Any]:
    step["step"] = PREFIX + str(step.get("step") or SOURCE_STEP)
    return step


def _unnamed(key: str) -> str:
    return key.removeprefix(PREFIX)


def _put_in(found: Tables, values: dict[str, Any]) -> dict[str, Any]:
    """What the importer is given: nothing is asked again that nothing here can answer."""
    return {**values, "skip_existing": not found.games}


async def lead_on(found: Tables, values: dict[str, Any]) -> dict[str, Any]:
    """Past the answers, into the importer: a folder of loose tables is read as folders,
    and another frontend asks where it is."""
    if _where(values) == FROM_A_FRONTEND:
        values.pop("path", None)
        values.pop("source_type", None)
        return _named(await _calls(found).first(None))
    values["path"] = str(values[FOLDER]).strip()
    values["source_type"] = FOLDERS_SOURCE
    return _named(await _calls(found).check(_put_in(found, values), PATHS_STEP))


async def open_importer(found: Tables) -> dict[str, Any]:
    return _named(await _calls(found).first(None))


async def check(found: Tables, values: dict[str, Any], key: str) -> dict[str, Any]:
    return _named(await _calls(found).check(_put_in(found, values), _unnamed(key)))


async def act(found: Tables, key: str, values: dict[str, Any], step: str) -> dict[str, Any]:
    return _named(await _calls(found).act(key, values, _unnamed(step)))


async def run(found: Tables, values: dict[str, Any], key: str) -> dict[str, Any]:
    """The import, which answers with a job. A path that turned out longer answers with a
    step instead."""
    started = await _calls(found).run(_put_in(found, values), _unnamed(key))
    return _named(started) if started.get("step") else started


async def job(found: Tables, job_id: str) -> dict[str, Any]:
    return await _calls(found).job(job_id)


async def progress(found: Tables) -> dict[str, Any]:
    ask = _calls(found).progress if found.action is not None else None
    return await ask() if ask is not None else {}
