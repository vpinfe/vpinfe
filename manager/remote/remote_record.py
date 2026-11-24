"""The Remote's part in recording media: a run steered from Now, Record Media on a game's
sheet, and the recordings waiting for a decision reviewed on the frontend's own screens
with the phone as the controller.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from functools import partial
from typing import Any

from nicegui import run, ui

from common.capture import preflight
from common.capture.run import CHOOSE, FILL, RUNNING
from common.failures import why
from common.i18n import t
from common.media.media_specs import media_label_map
from common.runtime.jobs import KIND_MEDIA_CAPTURE
from manager.kit import busy, offload, panel, verbs
from manager.kit.api import ApiError
from manager.kit.on_page import on_page
from manager.library import record

logger = logging.getLogger("vpinfe.manager.remote.remote_record")

BEFORE, AFTER = "before", "after"
KIND = KIND_MEDIA_CAPTURE

Client = Callable[[], Any]


def capability(capabilities: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Discovery's `capture`: whether the target records, and why not; None where it
    does not record at all."""
    return next((one for one in capabilities if one.get("name") == "capture"), None)


def read(client: Any) -> dict[str, Any]:
    """What the target is recording and what waits for a decision there."""
    try:
        able = capability(client.capabilities())
        if able is None:
            return {"capture": None, "run": {}, "waiting": []}
        return {"capture": able, "run": client.capture_run(), "waiting": waiting_of(client)}
    except ApiError as exc:
        logger.info("remote: could not read what the target records: %s", exc)
        return {"capture": None, "run": {}, "waiting": []}


def waiting_of(client: Any) -> list[dict[str, Any]]:
    return list(client.capture_proposals().get("proposals") or [])


def going(run_now: dict[str, Any]) -> bool:
    return run_now.get("state") == RUNNING


def place(run_now: dict[str, Any]) -> str:
    """How far a run of more than one has come, counting the game in hand."""
    of = int(run_now.get("of") or 0)
    if of < 2:
        return ""
    return t("manager.remote.at_of", at=min(int(run_now.get("done") or 0) + 1, of), of=of)


def _open(plan: dict[str, Any], count: str) -> list[str]:
    return [str(row.get("kind") or "") for row in plan.get("kinds") or []
            if int(row.get(count) or 0) and not row.get("reason")]


def either(labels: list[str]) -> str:
    if len(labels) < 2:
        return "".join(labels)
    return t("manager.remote.either", some=", ".join(labels[:-1]), last=labels[-1])


def lacking(plan: dict[str, Any]) -> str:
    """What a game has no file for among the kinds the target records; "" for none."""
    labels = media_label_map()
    kinds = [labels.get(kind, kind) for kind in _open(plan, "missing")]
    return t("manager.remote.no_kinds", kinds=either(kinds)) if kinds else ""


def offered(plan: dict[str, Any]) -> list[str]:
    """Missing Only where a kind has no file, Replace where one has."""
    return ([FILL] if _open(plan, "missing") else []) \
        + ([CHOOSE] if _open(plan, "have") else [])


def queue(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The recordings waiting, a game at a time and each game window by window."""
    return [row for page in record.grouped(rows) for row in page]


def run_card(state: dict[str, Any], client_for_target: Client,
             redraw: Callable[[], None]) -> None:
    """A run going or paused, and what can be done to it."""
    run_now = dict(state.get("run") or {})
    recording = going(run_now)
    reason = run_now.get("reason")
    of = int(run_now.get("of") or 0)

    @on_page
    async def act(call: str, fails: str) -> None:
        heard = state.get("run_heard")
        try:
            answer = await offload.io(getattr(client_for_target(), call))
            if call == "resume_capture":
                answer = await offload.io(client_for_target().capture_run)
        except Exception as exc:
            ui.notify(t(fails), caption=why(exc), type="negative")
            return
        if state.get("run_heard") == heard:
            state["run"] = answer
        redraw()

    with ui.column().classes("w-full gap-1 manager-card"):
        ui.label(t("manager.remote.recording" if recording
                   else "manager.remote.recording_paused")).classes("manager-card-title")
        ui.label(str((run_now.get("game") or {}).get("name") or "")) \
            .classes("remote-headline")
        if place(run_now):
            ui.label(place(run_now)).classes("remote-note")
        if not recording and reason:
            ui.label(preflight.words(reason)).classes("remote-note")
        if of > 1:
            ui.linear_progress(value=int(run_now.get("done") or 0) / of,
                               show_value=False).props("rounded")
    if recording:
        panel.remote_action(t("manager.remote.pause"),
                            partial(act, "pause_capture", "manager.remote.pause_failed"),
                            icon=verbs.PAUSE, hint=panel.NO_HINT)
    else:
        panel.remote_action(t("manager.page.resume"),
                            partial(act, "resume_capture", "manager.remote.resume_failed"),
                            icon=verbs.RUN, primary=True,
                            hint=t("manager.remote.resume_capture.help"))
    panel.remote_action(t("manager.record.stop"),
                        partial(act, "stop_capture", "manager.remote.stop_failed"),
                        icon=verbs.STOP, danger=True,
                        hint=t("manager.remote.stop_capture.help"))


def waiting_card(state: dict[str, Any], client_for_target: Client,
                 redraw: Callable[[], None], *, reviewable: bool) -> None:
    """How many recordings wait for a decision, with Review where the frontend is up
    to play them."""
    count = len(state.get("waiting") or [])
    with ui.column().classes("w-full gap-1 manager-card"):
        ui.label(t("manager.remote.waiting")).classes("manager-card-title")
        ui.label(t("manager.remote.recordings", count=count)).classes("remote-headline")
    panel.remote_action(t("manager.record.review"),
                        partial(begin, state, client_for_target, redraw),
                        icon=verbs.REVIEW,
                        hint=t("manager.record.review.help")).set_enabled(reviewable)


def _current(review: dict[str, Any]) -> dict[str, Any]:
    return dict(review["queue"][review["at"]])


@on_page
async def begin(state: dict[str, Any], client_for_target: Client,
                redraw: Callable[[], None]) -> None:
    """The first recording waiting, on the frontend."""
    try:
        rows = await offload.io(waiting_of, client_for_target())
    except Exception as exc:
        ui.notify(t("manager.record.waiting_failed"), caption=why(exc),
                  type="negative")
        return
    state["waiting"] = rows
    if not rows:
        redraw()
        return
    state["reviewing"] = {"queue": queue(rows), "at": 0, "showing": AFTER, "acting": False}
    await _show(state, client_for_target, redraw)


@on_page
async def _tell(state: dict[str, Any], client_for_target: Client) -> bool:
    """The frontend shows the current recording as the review says. False where it did not."""
    review = state["reviewing"]
    try:
        await run.io_bound(client_for_target().show_proposal,
                           str(_current(review).get("id") or ""), review["showing"])
    except Exception as exc:
        ui.notify(t("manager.remote.show_failed"), caption=why(exc), type="negative")
        return False
    return True


@on_page
async def _show(state: dict[str, Any], client_for_target: Client,
                redraw: Callable[[], None]) -> None:
    await _tell(state, client_for_target)
    redraw()


@on_page
async def _onward(state: dict[str, Any], client_for_target: Client,
                  redraw: Callable[[], None]) -> None:
    review = state["reviewing"]
    review["at"] += 1
    review["showing"] = AFTER
    if review["at"] >= len(review["queue"]):
        await end(state, client_for_target, redraw)
        return
    await _show(state, client_for_target, redraw)


@on_page
async def end(state: dict[str, Any], client_for_target: Client,
              redraw: Callable[[], None]) -> None:
    """The review over, from Stop or from its last recording."""
    state["reviewing"] = None
    try:
        await run.io_bound(client_for_target().end_preview)
        state["waiting"] = await offload.io(waiting_of, client_for_target())
    except Exception as exc:
        logger.info("remote: could not end the review cleanly: %s", exc)
    redraw()


def controller(state: dict[str, Any], client_for_target: Client,
               redraw: Callable[[], None]) -> None:
    """The recording on the frontend: its game and kind, Before and After, and the
    decision."""
    review = state["reviewing"]
    row = _current(review)
    of = len(review["queue"])
    kind = str(row.get("kind") or "")

    @on_page
    async def decide(use: bool) -> None:
        review["acting"] = True
        try:
            await offload.io(client_for_target().use_proposal, str(row.get("id") or ""), use)
        except Exception as exc:
            review["acting"] = False
            ui.notify(t("manager.record.could_not_use" if use
                        else "manager.record.could_not_discard"),
                      caption=why(exc), type="negative")
            return
        state["waiting"] = [one for one in state.get("waiting") or []
                            if one.get("id") != row.get("id")]
        await _onward(state, client_for_target, redraw)
        review["acting"] = False

    @on_page
    async def skip() -> None:
        await _onward(state, client_for_target, redraw)

    @on_page
    async def turn(event: Any) -> None:
        if event.value not in (BEFORE, AFTER) or event.value == review["showing"]:
            return
        was = review["showing"]
        review["showing"] = str(event.value)
        if not await _tell(state, client_for_target):
            review["showing"] = was
            event.sender.set_value(was)

    with ui.column().classes("w-full gap-1 manager-card"):
        ui.label(t("manager.record.review")).classes("manager-card-title")
        ui.label(str(row.get("name") or "")).classes("remote-headline")
        ui.label(media_label_map().get(kind, kind)).classes("remote-note")
        ui.label(t("manager.remote.at_of", at=review["at"] + 1, of=of)).classes("remote-note")
        ui.linear_progress(value=(review["at"] + 1) / of, show_value=False).props("rounded")
    ui.toggle({BEFORE: t("manager.remote.before"), AFTER: t("manager.remote.after")},
              value=review["showing"], on_change=turn) \
        .props("spread no-caps unelevated toggle-color=primary") \
        .classes("w-full remote-toggle").set_enabled(bool(row.get("replaces")))
    panel.remote_action(t("manager.record.use_this"), partial(decide, True),
                        icon=verbs.ACCEPT, primary=True,
                        hint=t("manager.record.use_this.help"))
    panel.remote_action(t("manager.record.discard"), partial(decide, False),
                        icon=verbs.DISCARD, hint=panel.NO_HINT)
    panel.remote_action(t("manager.record.skip"), skip, icon=verbs.SKIP,
                        hint=t("manager.record.skip.help"))
    panel.remote_action(t("manager.record.stop"), partial(end, state, client_for_target, redraw),
                        icon=verbs.STOP, hint=t("manager.record.stop.help"))


@on_page
async def heard(state: dict[str, Any], showing: dict[str, Any],
                client_for_target: Client, redraw: Callable[[], None]) -> None:
    """The frontend's state, for a review going on it: Before and After as the frontend
    turned them, and the next recording where the one it showed was decided elsewhere."""
    review: dict[str, Any] | None = state.get("reviewing")
    if not review:
        return
    if not showing.get("running"):
        state["reviewing"] = None
        redraw()
        return
    row = _current(review)
    preview = showing.get("preview") or {}
    if preview.get("proposal") == row.get("id"):
        if preview.get("showing") in (BEFORE, AFTER) and preview["showing"] != review["showing"]:
            review["showing"] = str(preview["showing"])
            redraw()
        return
    if review.get("acting"):
        return
    try:
        rows = await offload.io(waiting_of, client_for_target())
    except Exception as exc:
        logger.info("remote: could not read what waits: %s", exc)
        return
    state["waiting"] = rows
    if review.get("acting") or review is not state.get("reviewing"):
        return
    if row.get("id") not in {one.get("id") for one in rows}:
        await _onward(state, client_for_target, redraw)


def sheet_entry(game: dict[str, Any], state: dict[str, Any], client_for_target: Client,
                lacks: Any, started: Callable[[], Awaitable[None]]) -> None:
    """Record Media on a game's sheet, and what the game lacks said in `lacks`. Not
    drawn where the target does not record at all."""
    able = state.get("capture")
    if able is None:
        return
    words = {FILL: t("manager.remote.missing_only"), CHOOSE: t("manager.remote.replace")}
    with ui.column().classes("w-full gap-1") as holder:
        button = panel.remote_action(t("manager.remote.record_media"), icon=verbs.RECORD,
                                     hint=t("manager.remote.record_media.help"))
        button.set_enabled(False)
        note = ui.label(str(able.get("reason") or "")).classes("remote-note")
    note.set_visibility(not able.get("available"))
    if not able.get("available"):
        return

    @on_page
    async def record_it(existing: str) -> None:
        try:
            await offload.io(client_for_target().start_capture,
                             {"games": [str(game.get("id") or "")], "existing": existing})
        except Exception as exc:
            ui.notify(t("manager.remote.record_failed"), caption=why(exc),
                      type="negative")
            return
        await started()

    async def planned() -> None:
        try:
            plan = await offload.io(client_for_target().plan_capture,
                                    {"games": [str(game.get("id") or "")]})
        except Exception as exc:
            note.set_text(why(exc))
            note.set_visibility(True)
            return
        said = lacking(plan)
        lacks.set_text(said)
        lacks.set_visibility(bool(said))
        choices = offered(plan)
        if not choices:
            return
        with button, ui.menu():
            for existing in choices:
                ui.menu_item(words[existing], on_click=partial(record_it, existing)) \
                    .classes("manager-menu-item")
        button.set_enabled(True)

    busy.fill(holder, planned)
