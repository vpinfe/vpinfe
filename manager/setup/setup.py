"""Setup: VPinFE's first run, and the same questions asked again from System.

It runs on the wizard's dialog host (`manager/kit/wizard.py`) over whichever Manager page the
person is on, answering the wizard's own protocol with local closures - there is no
boundary here for an HTTP call to cross. `STEPS` is the whole list, in the order they are
asked: a step is one entry there and nothing else names the steps.

Every answer is saved as it is given, and `setup_done` on this install's row in the device
registry records which steps have one, so leaving and coming back costs a person nothing
but their place.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from functools import partial
from typing import Any

from nicegui import run, ui

from common.config.config_access import DisplayConfig
from common.devices import install_setup, serving
from common.games import launchers as launcher_model
from common.host import addresses
from common.i18n import t
from common.vpinfe_version import get_version
from manager.kit import deeplink, offload, wizard
from manager.kit.api import ApiClient
from manager.kit.on_page import on_page
from manager.settings import screens
from manager.setup import setup_appearance, setup_controls, setup_screens, setup_tables, setup_vpx
from manager.system import early_access

logger = logging.getLogger("vpinfe.manager.setup.setup")

HOW = "how"
APPEARANCE = setup_appearance.KEY
SCREENS = "screens"
CONTROLS = "controls"
VPX = "vpx"
GLASS = "glass"
VPX_KEYS = "vpxkeys"
TABLES = "tables"
# Where an import that Setup started is followed once Setup has closed.
JOBS_VIEW = "/manager?" + deeplink.query({"view": "jobs"})

# The launcher setting `presentation.cab_mode` mirrors in VPX's own ini.
BGSET_KEY = "Player.BGSet"

# Where How you play preselects Cabinet. Only the preselection: the person answers.
CABINET_SCREENS = 2


@dataclass
class Session:
    """One opening of Setup."""
    # Asked from System on an install that has finished: the answers it opens on are the
    # current ones, and nothing is written for a step answered the same way.
    again: bool
    # Opened in the window the frontend itself opened, which has no other way to say
    # where Setup can be reached from.
    window: bool = False
    vpx: setup_vpx.Vpx = field(default_factory=setup_vpx.Vpx)
    # Whether How you play was answered differently from what the install holds, so the
    # Visual Pinball half of it is still to write once the program is known.
    bgset_due: bool = True
    screens: setup_screens.Screens = field(default_factory=setup_screens.Screens)
    tables: setup_tables.Tables = field(default_factory=setup_tables.Tables)
    # Set once the install has been finished, which a job that is polled more than once
    # must not do again.
    finished: bool = False
    offer: Callable[[], None] | None = None


@dataclass(frozen=True)
class Step:
    """One question. `ask` draws it as the wizard's protocol has it, without the parts
    that depend on the other steps (`step`, `steps`, `confirm`), which `_ask` adds."""
    key: str
    # The catalog key of the name the step row shows.
    label: str
    ask: Callable[[Session], Awaitable[dict[str, Any]]]
    answer: Callable[[Session, dict[str, Any]], Awaitable[None]]
    applies: Callable[[Session], bool] = lambda session: True


def _config() -> Any:
    from common.config.paths import get_ini_config

    return get_ini_config()


def _address() -> str:
    """Where another device reaches this one, without the trailing slash."""
    return addresses.best(serving.network(_config()).http_port, "")


async def _screens_here() -> int:
    try:
        return len(await offload.io(screens.connected))
    except Exception:
        logger.warning("Could not count the screens", exc_info=True)
        return 0


async def _ask_how(session: Session) -> dict[str, Any]:
    config = _config()
    if session.again or HOW in install_setup.answered():
        cabinet = DisplayConfig.from_config(config).cab_mode
    else:
        cabinet = await _screens_here() >= CABINET_SCREENS
    question = t("manager.setup.how_question")
    step: dict[str, Any] = {
        "title": t("manager.setup.title") if session.again else t("manager.setup.welcome_title"),
        "help": question if session.again else t("manager.setup.welcome_lead"),
        "welcome": not session.again,
        "fields": [{
            "key": "cab_mode", "type": "choice", "label": question,
            "value": "cabinet" if cabinet else "desktop",
            "choices": [
                ("desktop", t("manager.setup.desktop"), t("manager.setup.desktop_help"),
                 "desktop"),
                ("cabinet", t("manager.setup.cabinet"), t("manager.setup.cabinet_help"),
                 "cabinet"),
            ],
        }],
    }
    if session.window and (address := await offload.io(_address)):
        step["notes"] = [t("manager.setup.address_note", address=address,
                           action=t("signin.page.get_code"))]
    return step


async def _answer_how(session: Session, values: dict[str, Any]) -> None:
    cabinet = str(values.get("cab_mode") or "desktop") == "cabinet"
    if session.again and cabinet == DisplayConfig.from_config(_config()).cab_mode:
        session.bgset_due = False
        return
    client = ApiClient()
    await offload.io(client.put_config, {"presentation": {"cab_mode": cabinet}})
    # Not offload.io: no launcher at all is a real answer, not a shutdown.
    launcher = await run.io_bound(launcher_model.default_launcher, "vpx")
    # A launcher with no program has no settings file to write to; the VPX step writes
    # BGSet when the program is chosen. One with a program that has never run has no file
    # yet, so the write makes it.
    if launcher is not None and launcher.has_program():
        try:
            await offload.io(client.write_launcher_config, launcher.launcher_id,
                             {BGSET_KEY: "1" if cabinet else "0"}, create=True)
        except Exception:
            logger.warning("Could not write BGSet for the default launcher", exc_info=True)


def _cabinet() -> bool:
    return DisplayConfig.from_config(_config()).cab_mode


async def _ask_appearance(session: Session) -> dict[str, Any]:
    return await setup_appearance.ask()


async def _answer_appearance(session: Session, values: dict[str, Any]) -> None:
    await setup_appearance.answer(values)


async def _ask_screens(session: Session) -> dict[str, Any]:
    return await setup_screens.ask(session.screens)


async def _answer_screens(session: Session, values: dict[str, Any]) -> None:
    await setup_screens.answer(session.screens, session.vpx)


async def _ask_controls(session: Session) -> dict[str, Any]:
    return await setup_controls.ask()


async def _answer_controls(session: Session, values: dict[str, Any]) -> None:
    await setup_controls.answer(values)


async def _ask_vpx(session: Session) -> dict[str, Any]:
    return await setup_vpx.ask_program(session.vpx)


async def _answer_vpx(session: Session, values: dict[str, Any]) -> None:
    session.tables.program = await setup_vpx.answer_program(
        session.vpx, values, cabinet=_cabinet(), write_bgset=session.bgset_due,
        then=partial(setup_screens.fill, session.screens) if _cabinet() else None)


async def _ask_glass(session: Session) -> dict[str, Any]:
    return await setup_vpx.ask_glass(session.vpx, _config())


async def _answer_glass(session: Session, values: dict[str, Any]) -> None:
    await setup_vpx.answer_glass(session.vpx, values)


async def _ask_keys(session: Session) -> dict[str, Any]:
    return await setup_vpx.ask_keys(session.vpx)


async def _answer_keys(session: Session, values: dict[str, Any]) -> None:
    await setup_vpx.answer_keys(session.vpx, values)


async def _ask_tables(session: Session) -> dict[str, Any]:
    return await setup_tables.ask(session.tables, again=session.again)


async def _answer_tables(session: Session, values: dict[str, Any]) -> None:
    await setup_tables.answer(session.tables, values, again=session.again)


# The questions, in the order they are asked. A step that depends on an earlier answer
# says so with `applies`; it is not asked, and not in the row, where it does not.
STEPS: tuple[Step, ...] = (
    Step(HOW, "manager.setup.step.how", _ask_how, _answer_how),
    Step(APPEARANCE, "manager.setup.step.appearance", _ask_appearance, _answer_appearance),
    Step(SCREENS, "manager.setup.step.screens", _ask_screens, _answer_screens,
         lambda session: _cabinet()),
    Step(CONTROLS, "manager.setup.step.controls", _ask_controls, _answer_controls),
    # Three pages under one name in the row.
    Step(VPX, "manager.setup.step.vpx", _ask_vpx, _answer_vpx),
    Step(GLASS, "manager.setup.step.vpx", _ask_glass, _answer_glass,
         lambda session: _cabinet() and setup_vpx.expects_program(session.vpx)),
    Step(VPX_KEYS, "manager.setup.step.vpx", _ask_keys, _answer_keys,
         lambda session: setup_vpx.expects_program(session.vpx)),
    Step(TABLES, "manager.setup.step.tables", _ask_tables, _answer_tables),
)


def _step(key: str | None) -> Step | None:
    """The step a key belongs to: the importer's steps are asked inside Tables."""
    if setup_tables.inside(key):
        key = TABLES
    return next((one for one in STEPS if one.key == key), None)


def _applicable(session: Session) -> list[Step]:
    return [one for one in STEPS if one.applies(session)]


def _after(session: Session, step: Step) -> Step | None:
    """The next question, which is not `step`'s successor in `STEPS` when that one has
    stopped applying."""
    return next((one for one in STEPS[STEPS.index(step) + 1:] if one.applies(session)), None)


def _resume(session: Session) -> Step:
    """Where Setup opens: the first question not answered, or the first of them again
    where Setup is being asked again. Every one answered and Setup not finished is the
    last, which is where Finish is."""
    steps = _applicable(session)
    if session.again:
        return steps[0]
    answered = install_setup.answered()
    return next((one for one in steps if one.key not in answered), steps[-1])


def _row(session: Session, steps: list[Step]) -> list[dict[str, Any]]:
    answered = install_setup.answered()
    row: list[dict[str, Any]] = []
    for one in steps:
        done = session.again or one.key in answered
        # Steps that share a name are one segment, which is done when all of them are.
        if row and row[-1]["label"] == t(one.label):
            row[-1]["holds"].append(one.key)
            row[-1]["done"] = row[-1]["done"] and done
        else:
            row.append({"key": one.key, "label": t(one.label), "done": done,
                        "holds": [one.key]})
    return row


def _framed(session: Session, step: Step, found: dict[str, Any]) -> dict[str, Any]:
    """The step row, with the step the page is on named in its segment: a step drawn
    inside another is one name in the row."""
    steps = _applicable(session)
    # One name in a row says nothing the title does not.
    if len(steps) > 1:
        row = _row(session, steps)
        here = str(found.get("step") or "")
        for entry in row:
            if step.key in entry["holds"] and here not in entry["holds"]:
                entry["holds"].append(here)
        found["steps"] = row
    return found


async def _ask(session: Session, step: Step) -> dict[str, Any]:
    found = await step.ask(session)
    found["step"] = step.key
    if step.key == TABLES and session.offer is not None:
        found["after"] = lambda values, offer=session.offer: offer()
    _framed(session, step, found)
    if _after(session, step) is None:
        found["confirm"] = t("word.finish")
    return found


async def _answer(session: Session, step: Step, values: dict[str, Any]) -> None:
    await step.answer(session, values)
    if not session.again:
        await run.io_bound(install_setup.answer, step.key)


async def _done(session: Session, answered: str | None = None) -> None:
    """Setup is over, once. `answered` is the step to record when nothing else has."""
    if session.again or session.finished:
        return
    if answered:
        await run.io_bound(install_setup.answer, answered)
    await run.io_bound(install_setup.finish, _config())
    session.finished = True


def calls(session: Session) -> wizard.Calls:
    """Setup's four calls for the wizard, bound to one opening."""
    tables = session.tables

    def inside(found: dict[str, Any]) -> dict[str, Any]:
        owner = _step(TABLES)
        assert owner is not None
        return _framed(session, owner, found)

    async def first(key: str | None) -> dict:
        return await _ask(session, _step(key) or _resume(session))

    async def check(values: dict[str, Any], key: str) -> dict:
        step = _step(key) or _resume(session)
        if step.key == TABLES:
            if setup_tables.inside(key):
                return inside(await setup_tables.check(tables, values, key))
            return inside(await setup_tables.lead_on(tables, values))
        await _answer(session, step, values)
        return await _ask(session, _after(session, step) or step)

    async def act(key: str, values: dict[str, Any], step: str) -> dict:
        if setup_tables.inside(step):
            return inside(await setup_tables.act(tables, key, values, step))
        if key == setup_tables.IMPORT:
            return inside(await setup_tables.open_importer(tables))
        return await _ask(session, _step(step) or _resume(session))

    async def finish(values: dict[str, Any], key: str) -> dict:
        if setup_tables.inside(key):
            started = await setup_tables.run(tables, values, key)
            if started.get("job_id"):
                # Setup closes once the import has started, so it is over then rather
                # than when the job is.
                await _done(session, TABLES)
            return inside(started) if started.get("step") else started
        step = _step(key) or _resume(session)
        await _answer(session, step, values)
        # The answer may have lengthened the path: a step drawn as the last was drawn
        # before the question that decides whether there is another had been answered.
        if (later := _after(session, step)) is not None:
            return await _ask(session, later)
        await _done(session)
        return {"ok": True}

    async def job(job_id: str) -> dict:
        found = await setup_tables.job(tables, job_id)
        if found.get("state") == "done":
            await _done(session, TABLES)
        return found

    async def progress() -> dict:
        return await setup_tables.progress(tables)

    return wizard.Calls(first, check, act, finish, job, progress)


@on_page
async def open_setup(*, window: bool = False) -> None:
    """Setup over the page this is called from, until it is closed or finished."""
    vpx = await offload.io(setup_vpx.gather)
    session = Session(again=not install_setup.pending(_config()), window=window, vpx=vpx,
                      tables=await offload.io(setup_tables.gather, vpx.chosen))
    session.offer = await early_access.offer(get_version(), again=session.again)
    session.vpx.answered = not session.again and VPX in install_setup.answered()
    session.screens.answered = not session.again and SCREENS in install_setup.answered()
    handed: list[str] = []
    ran = await wizard.open_dialog(
        label=t("manager.setup.title"), calls=calls(session), under="setup", mark=True,
        aside=t("word.close") if session.again else t("manager.setup.finish_later"),
        close_when_done=True, hand_off=handed.append)
    if handed:
        ui.navigate.to(JOBS_VIEW)
    # The page was drawn for an unfinished install, line above it and all.
    elif ran and not session.again:
        ui.navigate.reload()
    else:
        setup_appearance.restore()
