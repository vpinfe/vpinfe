"""Setup's Visual Pinball step: which program, the playfield glass, and the buttons.

Three pages that Setup draws as one name in its row. They write the default Visual Pinball
launcher: its program setting, and keys of its ini - created at the newest version's
preferences folder when the program has never run, so nothing is lost to a missing file.
Each answer is saved as given; a value is written only where it differs from what the ini
already holds.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nicegui import ui

from common.config.config_access import DisplayConfig
from common.config.path_state import OK as PATH_OK
from common.failures import why
from common.games import launchers as launcher_model
from common.games.launchers import Launcher
from common.host import screen_size
from common.i18n import t
from manager.kit import offload, panel, units, verbs, when
from manager.kit.api import ApiClient
from manager.kit.data import config_groups
from manager.kit.on_page import on_page
from manager.settings import vpx_find, vpx_input
from manager.settings.binding_table import Table
from manager.settings.vpx_find import Find

logger = logging.getLogger("vpinfe.manager.setup.setup_vpx")

PROGRAM = "program"
SOMEWHERE_ELSE = "else"
# What Somewhere else's path was last checked as: (the path, its state).
CHECKED = "else_checked"
NOT_INSTALLED = "none"
# Where Visual Pinball's releases are published.
RELEASES = "https://github.com/vpinball/vpinball/releases"
# Where its newest builds are made, the 10.8.1 preview among them. Downloading from here asks
# for a GitHub sign-in.
PREVIEW_BUILDS = "https://github.com/vpinball/vpinball/actions/workflows/vpinball.yml"

BGSET_KEY = "Player.BGSet"
SIZE_KEYS = ("Player.ScreenWidth", "Player.ScreenHeight")
ANGLE_KEY = "Player.ScreenInclination"
# A typical cabinet's playfield glass, offered where nothing says otherwise.
TYPICAL_ANGLE = 6.0
# The ini's own range for the glass: lengths in centimeters, the angle in degrees.
SIZE_RANGE_CM = (5.0, 200.0)
ANGLE_RANGE = (-30.0, 30.0)
# What a stored length is kept to, in centimeters, and the difference that is a change.
_CM_PLACES = 2
_TOLERANCE_CM = 0.05

# Where each kind of find came from: the words and the icon beside them.
_SOURCES = {
    vpx_find.PINBALLX: ("manager.setup.vpx.from_pinballx", "dvr"),
    vpx_find.POPPER: ("manager.setup.vpx.from_popper", "dvr"),
    vpx_find.USUAL: ("manager.setup.vpx.usual_place", "apps"),
    vpx_find.APPLICATIONS: ("manager.setup.vpx.in_applications", "apps"),
    vpx_find.PATH: ("manager.setup.vpx.path", "terminal"),
}

# The glass's own state in the walk's values, and where each of its values came from.
GLASS = "glass"
FROM_SCREEN = "screen"
MEASURE = "measure"
KEPT = "kept"
TYPICAL = "typical"
YOURS = "yours"


@dataclass
class Vpx:
    """What Setup knows of Visual Pinball: the launcher it writes, what is found, and the
    program the answers so far have chosen."""
    launcher: Launcher | None = None
    finds: list[Find] = field(default_factory=list)
    # The launcher's program setting as stored. `chosen` is the program answered, which is
    # "" until it is, and stays "" while "Not installed yet" is the answer.
    stored: str = ""
    chosen: str = ""
    # Whether the program page has been answered. Until it is, the pages after it are
    # taken to apply, so the page before them is not drawn as the last.
    answered: bool = False


def gather() -> Vpx:
    """Blocking."""
    launcher = launcher_model.default_launcher("vpx")
    stored = str(launcher.settings.get("bin_path") or "").strip() if launcher else ""
    return Vpx(launcher=launcher, finds=vpx_find.finds(), stored=stored, chosen=stored)


def expects_program(vpx: Vpx | None) -> bool:
    return bool(vpx and (vpx.chosen or not vpx.answered))


def _date(find: Find) -> str:
    return when.day(dt.datetime.fromtimestamp(find.updated)) if find.updated else ""


# ---- the program ----------------------------------------------------------------------


def _same(find: str, stored: str) -> bool:
    """Whether the stored program is the find, or the executable inside the find's bundle."""
    try:
        found, held = Path(find).resolve(), Path(stored).resolve()
    except OSError:
        return find == stored
    return held == found or (found.suffix.lower() == ".app" and found in held.parents)


def _preselected(vpx: Vpx) -> tuple[str, str]:
    """The pick, and the path held under Somewhere else. The program the launcher already
    names is the pick when it is found, else Somewhere else holding it. With none named it
    is the newest find, or Somewhere else when there is none."""
    for at, one in enumerate(vpx.finds):
        if vpx.stored and _same(one.path, vpx.stored):
            return str(at), ""
    if vpx.stored:
        return SOMEWHERE_ELSE, vpx.stored
    return ("0", "") if vpx.finds else (SOMEWHERE_ELSE, "")


def _else_detail(client: ApiClient,
                 typed: str) -> Callable[[dict[str, Any], Callable[[], None]], None]:
    def draw(values: dict[str, Any], changed: Callable[[], None]) -> None:
        values.setdefault(SOMEWHERE_ELSE, typed)

        def checked(state: str, said: str) -> str:
            values[CHECKED] = (said, state)
            changed()
            return ""

        def edited(event: Any) -> None:
            # The check runs on a timer; what Next saves is what is in the field now.
            values[SOMEWHERE_ELSE] = str(event.value or "")
            changed()

        panel.path_field(t("manager.setup.vpx.else_placeholder"), wants="exe",
                         value=str(values[SOMEWHERE_ELSE] or ""), width="w-full",
                         on_checked=checked, browse=client.folders).on_value_change(edited)

    return draw


def _none_detail(_values: dict[str, Any], _changed: Callable[[], None]) -> None:
    ui.label(t("manager.setup.vpx.none_note")).classes("manager-help")
    panel.link_out(t("manager.setup.vpx.none_get"), to=RELEASES)()
    with ui.element("div").classes("manager-help"):
        panel.link_out(t("manager.setup.vpx.none_preview"), to=PREVIEW_BUILDS,
                       hint=t("manager.setup.vpx.none_preview_hint"))()


def _missed() -> None:
    with ui.element("div").classes("manager-wizard-found"):
        ui.icon("search_off")
        with ui.column().classes("gap-0"):
            ui.label(t("manager.setup.vpx.not_found")).classes("manager-source-name")
            ui.label(t(vpx_find.looked())).classes("manager-help")


async def ask_program(vpx: Vpx) -> dict[str, Any]:
    several = len(vpx.finds) > 1
    pick, typed = _preselected(vpx)
    choices: list[tuple[str, str, str, str]] = []
    for at, one in enumerate(vpx.finds):
        words, icon = _SOURCES[one.source]
        help_text = t(words)
        if several and (when := _date(one)):
            help_text = t("manager.setup.vpx.updated", source=help_text, date=when)
        choices.append((str(at), one.path, help_text, icon))
    choices.append((SOMEWHERE_ELSE, t("manager.setup.vpx.else"),
                    t("manager.setup.vpx.else_help"), "folder_open"))
    choices.append((NOT_INSTALLED, t("manager.setup.vpx.none"),
                    t("manager.setup.vpx.none_help"), "download"))
    title = t("manager.setup.vpx.title_many" if several else "manager.setup.vpx.title_one")

    def complete(values: dict[str, Any]) -> bool:
        if str(values.get(PROGRAM) or pick) != SOMEWHERE_ELSE:
            return True
        said = str(values.get(SOMEWHERE_ELSE) or "").strip()
        return bool(said) and values.get(CHECKED) == (said, PATH_OK)

    step: dict[str, Any] = {
        "title": title,
        "fields": [{
            "key": PROGRAM, "type": "choice", "label": title, "value": pick,
            "choices": choices,
            "details": {SOMEWHERE_ELSE: _else_detail(ApiClient(), typed),
                        NOT_INSTALLED: _none_detail},
        }],
        "gate": complete,
    }
    if not vpx.finds:
        step["before"] = _missed
    return step


def _program_chosen(vpx: Vpx, values: dict[str, Any]) -> str:
    pick = str(values.get(PROGRAM) or _preselected(vpx)[0])
    if pick == NOT_INSTALLED:
        return ""
    if pick == SOMEWHERE_ELSE:
        return str(values.get(SOMEWHERE_ELSE) or "").strip()
    return vpx.finds[int(pick)].path if pick.isdigit() and int(pick) < len(vpx.finds) else ""


def _launcher_body(vpx: Vpx, path: str) -> tuple[str, dict[str, Any]]:
    """The whole launcher with its program set: `put` replaces a launcher, so everything
    else it holds goes back as it was. Where the install has no Visual Pinball launcher,
    one is made."""
    if vpx.launcher is not None:
        body = vpx.launcher.as_dict()
        settings = {**body["settings"], "bin_path": path}
        return vpx.launcher.launcher_id, {**body, "settings": settings}
    return launcher_model.mint_launcher_id(), {
        "app": "vpx", "display_name": "Visual Pinball X", "enabled": True,
        "settings": {"bin_path": path}}


def _read(client: ApiClient, launcher_id: str) -> dict[str, Any]:
    return dict(client.launcher_config(launcher_id).get("values") or {})


def _number(values: dict[str, Any], key: str, default: float = 0.0) -> float:
    try:
        return float(str((values.get(key) or {}).get("value")))
    except ValueError:
        return default


def _held(values: dict[str, Any], key: str) -> str:
    return str((values.get(key) or {}).get("value") or "").strip()


def _set_here(values: dict[str, Any], key: str) -> bool:
    return bool((values.get(key) or {}).get("set_here"))


async def _write_ini(launcher_id: str, keys: dict[str, str]) -> None:
    """Keys of the launcher's ini, which is made where Visual Pinball has never run."""
    if keys:
        await offload.io(ApiClient().write_launcher_config, launcher_id, keys, create=True)


async def answer_program(vpx: Vpx, values: dict[str, Any], *, cabinet: bool,
                         write_bgset: bool = True,
                         then: Callable[[str], Awaitable[None]] | None = None) -> str:
    """The program answered, which the Tables step starts its search beside. `then` is
    told the launcher's id when this answer is what made the launcher."""
    path = _program_chosen(vpx, values)
    vpx.chosen = path
    vpx.answered = True
    if not path:
        return path
    client = ApiClient()
    made = vpx.launcher is None
    if not (vpx.stored and _same(path, vpx.stored)):
        launcher_id, body = _launcher_body(vpx, path)
        await offload.io(client.put_launcher, launcher_id, body)
        vpx.launcher = Launcher.from_dict({**body, "launcher_id": launcher_id})
        vpx.stored = path
    if vpx.launcher is None:
        return path
    if write_bgset:
        # The Screens answer's Visual Pinball half: How you play could not write it while
        # the program was unknown, and the file may not exist yet.
        wanted = "1" if cabinet else "0"
        try:
            held = await offload.io(_read, client, vpx.launcher.launcher_id)
            if str((held.get(BGSET_KEY) or {}).get("value") or "0") != wanted:
                await _write_ini(vpx.launcher.launcher_id, {BGSET_KEY: wanted})
        except Exception:
            logger.warning("Could not write BGSet for the default launcher", exc_info=True)
    if made and then is not None:
        await then(vpx.launcher.launcher_id)
    return path


# ---- the playfield glass --------------------------------------------------------------


def _cm(value: Any) -> float:
    return round(float(value), _CM_PLACES)


async def _reported_cm(config: Any) -> tuple[float, float] | None:
    said = await offload.io(screen_size.reported_mm,
                            DisplayConfig.from_config(config).playfield_screen_id)
    return (_cm(said[0] / 10), _cm(said[1] / 10)) if said else None


def _source(icon: str, label: str, detail: str = "") -> None:
    with ui.element("div").classes("manager-wizard-src"):
        ui.icon(icon)
        with ui.column().classes("gap-0 min-w-0"):
            ui.label(label).classes("manager-wizard-src-label")
            if detail:
                ui.label(detail)


def _differs(left: tuple[float, float], right: tuple[float, float]) -> bool:
    return any(abs(one - other) > _TOLERANCE_CM for one, other in zip(left, right, strict=True))


def _unreadable(title_key: str) -> dict[str, Any]:
    return {"title": t(title_key), "notes": [t("said.settings_unreadable")]}


async def ask_glass(vpx: Vpx, config: Any) -> dict[str, Any]:
    if vpx.launcher is None:
        return _unreadable("manager.setup.glass.title")
    client = ApiClient()
    try:
        held = await offload.io(_read, client, vpx.launcher.launcher_id)
        stored_config = await offload.io(client.config_values)
    except Exception:
        logger.warning("Could not read the glass settings", exc_info=True)
        return _unreadable("manager.setup.glass.title")
    reported = await _reported_cm(config)
    size = (_cm(_number(held, SIZE_KEYS[0])), _cm(_number(held, SIZE_KEYS[1])))
    if all(_set_here(held, key) and _number(held, key) > 0 for key in SIZE_KEYS):
        size_from = KEPT
    elif reported:
        size, size_from = reported, FROM_SCREEN
    else:
        size_from = MEASURE
    angle_kept = _set_here(held, ANGLE_KEY) and _held(held, ANGLE_KEY) != ""
    glass: dict[str, Any] = {
        "size": list(size), "size_from": size_from,
        "angle": _number(held, ANGLE_KEY) if angle_kept else TYPICAL_ANGLE,
        "angle_from": KEPT if angle_kept else TYPICAL,
        "units": units.resolved((stored_config.get("manager") or {}).get("units")),
    }

    def draw(values: dict[str, Any]) -> None:
        now = values.setdefault(GLASS, glass)
        drawn: dict[str, Any] = {}

        def says_size() -> None:
            drawn["size_says"].clear()
            with drawn["size_says"]:
                words = {FROM_SCREEN: ("tv", "manager.setup.glass.from_screen", ""),
                         MEASURE: ("straighten", "manager.setup.glass.measure",
                                   "manager.setup.glass.measure_size_help"),
                         KEPT: ("history", "manager.setup.glass.kept", "")}.get(
                    now["size_from"])
                if words:
                    _source(words[0], t(words[1]), t(words[2]) if words[2] else "")
                if reported and now["size_from"] != FROM_SCREEN and _differs(
                        reported, (now["size"][0], now["size"][1])):
                    offer(reported)

        def offer(from_screen: tuple[float, float]) -> None:
            shown = " × ".join(f"{units.shown(one, now['units']):.1f}" for one in from_screen)
            with ui.element("div").classes("manager-wizard-src manager-wizard-src--offer"):
                ui.icon("tv")
                ui.label(t("manager.setup.glass.offer", size=shown,
                           unit=t(f"manager.units.{now['units']}"))
                         ).classes("manager-wizard-src-label")
                panel.action(t("manager.setup.glass.use_it"), lambda: use_it(from_screen),
                             icon=verbs.ACCEPT, hint=t("manager.setup.glass.use_it_hint"))()

        def use_it(from_screen: tuple[float, float]) -> None:
            now["size"], now["size_from"] = list(from_screen), FROM_SCREEN
            drawn["fill"]()

        def says_angle() -> None:
            drawn["angle_says"].clear()
            with drawn["angle_says"]:
                if now["angle_from"] == TYPICAL:
                    _source("straighten", t("manager.setup.glass.typical"),
                            t("manager.setup.glass.angle_help"))
                elif now["angle_from"] == KEPT:
                    _source("history", t("manager.setup.glass.kept"))

        def typed_size(at: int, event: Any) -> None:
            if event.value is None or drawn.get("filling"):
                return
            now["size"][at] = _cm(units.to_cm(float(event.value), now["units"]))
            now["size_from"] = YOURS
            says_size()

        def typed_angle(event: Any) -> None:
            if event.value is None:
                return
            now["angle"], now["angle_from"] = float(event.value), YOURS
            says_angle()

        @on_page
        async def chose_units(event: Any) -> None:
            chosen = str(event.value)
            try:
                await offload.io(ApiClient().put_config, {"manager": {"units": chosen}})
            except Exception:
                logger.warning("Could not save the units", exc_info=True)
                ui.notify(t("manager.settings.save_failed"), type="negative")
                return
            now["units"] = chosen
            drawn["fill"]()

        def edit_size() -> None:
            box = ui.element("div").classes("manager-fact-edit")

            def fill() -> None:
                # Making the controls is not typing in them.
                drawn["filling"] = True
                box.clear()
                with box:
                    with ui.element("div").classes("manager-field-pair"):
                        for at, tip in enumerate(("long_side", "short_side")):
                            with ui.element("div").classes("manager-field-part"):
                                ui.number(
                                    value=units.shown(now["size"][at], now["units"]),
                                    format="%.1f", step=0.1,
                                    min=units.shown(SIZE_RANGE_CM[0], now["units"]),
                                    max=units.shown(SIZE_RANGE_CM[1], now["units"]),
                                    suffix=t(f"manager.units.{now['units']}"),
                                    on_change=lambda event, at=at: typed_size(at, event)) \
                                    .props("dense borderless") \
                                    .classes("manager-edit-field manager-edit-narrow") \
                                    .tooltip(t(f"manager.vpx_displays.{tip}"))
                                if not at:
                                    ui.label("×").classes("manager-cell-join") \
                                        .props("aria-hidden=true")
                    panel.segmented({units.CM: t("manager.units.cm"),
                                     units.INCH: t("manager.units.in")},
                                    now["units"], chose_units).classes("manager-units")
                drawn["filling"] = False
                if "size_says" in drawn:
                    says_size()

            drawn["fill"] = fill
            fill()

        def edit_angle() -> None:
            panel.number(now["angle"], typed_angle, whole=False, step=0.5, unit="°",
                     low=ANGLE_RANGE[0], high=ANGLE_RANGE[1])()

        def holder(key: str) -> Callable[[], None]:
            def make() -> None:
                drawn[key] = ui.element("div")

            return make

        panel.facts(ui, [
            (t("manager.setup.glass.size"), edit_size),
            (panel.ASIDE, holder("size_says")),
            (t("manager.setup.glass.angle"), edit_angle),
            (panel.ASIDE, holder("angle_says")),
        ])
        says_size()
        says_angle()

    return {"title": t("manager.setup.glass.title"), "help": t("manager.setup.glass.lead"),
            "draw": draw}


@on_page
async def answer_glass(vpx: Vpx, values: dict[str, Any]) -> None:
    glass = values.get(GLASS)
    if not glass or vpx.launcher is None:
        return
    held = await offload.io(_read, ApiClient(), vpx.launcher.launcher_id)
    keys: dict[str, str] = {}
    for key, wanted in zip(SIZE_KEYS, glass["size"], strict=True):
        if abs(_number(held, key) - wanted) > _TOLERANCE_CM / 10:
            keys[key] = f"{_cm(wanted):g}"
    if abs(_number(held, ANGLE_KEY) - float(glass["angle"])) > _TOLERANCE_CM / 10:
        keys[ANGLE_KEY] = f"{round(float(glass['angle']), 1):g}"
    try:
        await _write_ini(vpx.launcher.launcher_id, keys)
    except Exception as exc:
        logger.warning("Could not write the playfield glass", exc_info=True)
        ui.notify(t("manager.settings.save_failed"), caption=why(exc), type="negative")


# ---- the buttons ----------------------------------------------------------------------


class _Creating:
    """The client as the buttons table writes through it, making the program's settings
    file where it has none yet."""

    def __init__(self, client: ApiClient) -> None:
        self.client = client

    def write_launcher_config(self, launcher_id: str, values: dict) -> dict:
        return self.client.write_launcher_config(launcher_id, values, create=True)


def _flat(found: dict[str, Any]) -> dict[str, str]:
    return {key: str((held or {}).get("value") or "")
            for key, held in (found.get("values") or {}).items()}


async def ask_keys(vpx: Vpx) -> dict[str, Any]:
    if vpx.launcher is None:
        return _unreadable("manager.setup.vpxkeys.title")
    launcher_id = vpx.launcher.launcher_id
    client = ApiClient()
    try:
        found = await offload.io(client.launcher_config, launcher_id)
    except Exception:
        logger.warning("Could not read the buttons", exc_info=True)
        return _unreadable("manager.setup.vpxkeys.title")
    group = next((one for one in config_groups(found) if vpx_input.applies(one)), None)
    if group is None:
        return _unreadable("manager.setup.vpxkeys.title")
    step: dict[str, Any] = {"title": t("manager.setup.vpxkeys.title"),
                            "help": t("manager.setup.vpxkeys.lead")}
    flat = _flat(found)

    def draw(_values: dict[str, Any]) -> None:
        holder = ui.element("div").classes("w-full")
        context: dict[str, Any] = {"library": _Creating(client),
                                   "launcher": {"launcher_id": launcher_id}}

        def fill() -> None:
            holder.clear()
            page = vpx_input.Page(context["library"], context, group, flat)
            with holder:
                Table(vpx_input.table_rows(group, flat), page.bound(), write=page.put,
                      source=t("manager.vpx_input.defaults"), shares=True,
                      refuses=page.refuse)

        async def rebuild() -> None:
            flat.clear()
            flat.update(_flat(await offload.io(client.launcher_config, launcher_id)))
            fill()

        context["rebuild"] = rebuild
        fill()

    step["draw"] = draw
    return step


async def answer_keys(_vpx: Vpx, _values: dict[str, Any]) -> None:
    """Each press is saved as it is made."""
