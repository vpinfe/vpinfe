"""A recording run: what it would do, and doing it as a job, one game after another,
written down as it goes so it can pause and resume."""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from common import events, service_errors
from common.apps.contract import VIEW_FSS
from common.atomic_write import write_atomic
from common.config.paths import CAPTURE_DIR, get_ini_config
from common.devices import install_identity
from common.failures import why
from common.games import game_lens, game_repository, tables
from common.host import launch, launch_state, tools
from common.i18n import t
from common.runtime import jobs
from common.timestamps import utc_now_iso

from . import adapters, placing, preflight, session, settings, slots, space, unshown

logger = logging.getLogger("vpinfe.common.capture.run")

FILL = "fill"
REPLACE_DOWNLOADED = "replace_downloaded"
REPLACE_ALL = "replace_all"
CHOOSE = "choose"
EXISTING = (FILL, REPLACE_DOWNLOADED, REPLACE_ALL, CHOOSE)

# What becomes of a slot.
FILLED = "fill"
REPLACED = "replace"
PROPOSED = "propose"
LEFT = "leave"

# Seconds a table takes beyond Wait and Length.
START_SECONDS = 6
CLOSE_SECONDS = 3
ENCODE_SECONDS = 5

WORK = CAPTURE_DIR / "recording"
# The run in hand, written as it goes.
RUN_FILE = CAPTURE_DIR / "run.json"

RUNNING = "running"
PAUSED = "paused"

# Why a run paused. A person's Pause has no reason.
PAUSED_LAUNCHED = "capture.run.launched"
PAUSED_SPACE = "capture.run.space"
INTERRUPTED = "capture.run.interrupted"

NO_FSS = "capture.view.no_fss"


@dataclass(frozen=True)
class Request:
    games: Sequence[str] = ()
    tables: Sequence[tuple[str, str]] = ()
    kinds: Sequence[str] = ()
    existing: str = FILL
    settings: Mapping[str, Any] = field(default_factory=dict)
    sound: bool | None = None
    confirmed: int | None = None
    # Every recording proposed, an empty slot's too.
    review: bool = False


def _named(request: Request) -> list[tuple[str, str]]:
    """Each game or table once, in the order named."""
    named = [(str(game_id), "") for game_id in request.games] + \
        [(str(game_id), str(table_id)) for game_id, table_id in request.tables]
    if not named:
        raise service_errors.RefusedError(t("error.capture.nothing_named"))
    return list(dict.fromkeys(named))


def _launch_key(game: Any, table_id: str) -> str | None:
    """What the launch is asked to play: the table's own key, or None for the game's
    default."""
    if not table_id:
        return None
    entry = tables.table_entries(getattr(game, "meta_config", None) or {}).get(table_id)
    if entry is None:
        raise service_errors.NotFoundError(t("error.games.table_not_found"),
                                           details={"table": table_id})
    return tables.entry_native_key(entry)


def _kinds(request: Request, report: Mapping[str, Any], chosen: settings.Settings) -> list[str]:
    if request.kinds:
        unknown = sorted(set(request.kinds) - session.RECORDABLE)
        if unknown:
            raise service_errors.RefusedError(t("error.capture.not_recordable",
                                                kinds=", ".join(unknown)))
        return list(dict.fromkeys(request.kinds))
    found = []
    for screen in report["screens"]:
        picture, video = session.KINDS[screen["window"]]
        found += [kind for kind, ability in ((picture, "picture"), (video, "video"))
                  if screen[ability]["available"]]
    sound = chosen.sound if request.sound is None else request.sound
    return found + ([session.AUDIO] if sound and report["sound"]["available"] else [])


def _blocked(kind: str, report: Mapping[str, Any]) -> dict[str, Any] | None:
    """Why this device cannot record `kind`, or None where it can. A kind with no screen
    row is one a device that records nothing was asked for."""
    if kind == session.AUDIO:
        return report["sound"]["reason"]
    if kind == session.FSS:
        kind = session.KINDS[adapters.PLAYFIELD][0]
    for screen in report["screens"]:
        picture, video = session.KINDS[screen["window"]]
        if kind in (picture, video):
            return screen["picture" if kind == picture else "video"]["reason"]
    return report["reason"]


def _catalogs() -> set[str]:
    from common.online import sources

    return {source.id for source in sources.catalogs()}


def decide(existing: str, held: str | None, review: bool = False) -> str:
    """What becomes of a slot whose file `held` placed, None where it has none."""
    if review:
        return PROPOSED
    if held is None:
        return FILLED
    if existing == CHOOSE:
        return PROPOSED
    if existing == REPLACE_ALL or (existing == REPLACE_DOWNLOADED and held in _catalogs()):
        return REPLACED
    return LEFT


def _estimate(kinds: Sequence[str], at_once: bool, chosen: settings.Settings) -> int:
    """Seconds for the launch of the table's own view and, where `kinds` has the FSS
    picture, the launch of its own after it."""
    own = [kind for kind in kinds if kind != session.FSS]
    seconds = 0
    if own:
        screens = {window for window, pair in session.KINDS.items()
                   if pair[1] in own or pair[0] in own}
        videos = sum(1 for pair in session.KINDS.values() if pair[1] in own)
        recording = chosen.length * (1 if at_once else max(1, videos))
        seconds = (START_SECONDS + chosen.wait + recording + CLOSE_SECONDS
                   + ENCODE_SECONDS * len(screens))
    if session.FSS in kinds:
        seconds += (START_SECONDS + chosen.wait + chosen.picture_at + CLOSE_SECONDS
                    + ENCODE_SECONDS)
    return seconds


_WINDOW_OF = {kind: window for window, pair in session.KINDS.items() for kind in pair}
_WINDOW_OF[session.FSS] = adapters.PLAYFIELD


def _without_fss(game: Any, table: str | None) -> dict[str, Any] | None:
    """Why this table cannot be recorded in Full Single Screen, or None where it can."""
    try:
        here = launch.this_devices_copy(game)
    except launch.LaunchUnavailableError:
        return None
    if VIEW_FSS in placing.views(here, table):
        return None
    return {"key": NO_FSS, "params": {}, "fix": tools.FIX_NONE, "remedy": None}


def _plan_one(game_id: str, table_id: str, request: Request, report: Mapping[str, Any],
              chosen: settings.Settings, kinds: Sequence[str]) -> dict[str, Any]:
    """What recording one game or table would do."""
    game = game_lens.game_or_refuse(game_id)
    key = _launch_key(game, table_id)
    unseen = unshown.of(game_id, game, key)
    no_fss = _without_fss(game, key) if session.FSS in kinds else None

    def blocked_here(kind: str) -> dict[str, Any] | None:
        return (_blocked(kind, report) or unseen.get(_WINDOW_OF.get(kind, ""))
                or (no_fss if kind == session.FSS else None))

    open_kinds = [kind for kind in kinds if not blocked_here(kind)]
    held = slots.serving_each(game_id, table_id, open_kinds) if open_kinds else {}
    rows: list[dict[str, Any]] = []
    doing: list[str] = []
    for kind in kinds:
        blocked = blocked_here(kind)
        serving = held.get(kind)
        source = slots.source(serving)
        what = LEFT if blocked else decide(request.existing, source, request.review)
        rows.append({"kind": kind, "does": what, "source": source, "reason": blocked,
                     "file": serving["path"] if serving else None,
                     "goes": what in (REPLACED, PROPOSED) and slots.goes(serving, table_id)})
        if what != LEFT:
            doing.append(kind)
    replacing: dict[str, int] = {}
    for row in rows:
        if row["does"] == REPLACED and row["goes"]:
            replacing[str(row["source"])] = replacing.get(str(row["source"]), 0) + 1
    return {"game_id": game_id, "table_id": table_id,
            "name": str(game_repository.game_to_row(game).get("name")
                        or getattr(game, "game_dir_name", "") or ""),
            "kinds": rows, "recording": doing,
            "replacing": sum(replacing.values()), "replacing_by_source": replacing,
            "estimate_seconds": _estimate(doing, bool(report["at_once"]), chosen)
            if doing else 0}


def _launches(kinds: Sequence[str]) -> int:
    """How many times the table is launched to record these: once for the kinds of its own
    view, and once more for the Full Single Screen picture."""
    return int(any(kind != session.FSS for kind in kinds)) + int(session.FSS in kinds)


def _counted(kind: str, rows: Sequence[Mapping[str, Any]],
             report: Mapping[str, Any]) -> dict[str, Any]:
    """A kind over every target: the slots it can record that have no file and that have
    one, and why not where no target can record it."""
    able = [row for row in rows if not row["reason"]]
    return {"kind": kind,
            "reason": _blocked(kind, report) or (rows[0]["reason"] if rows and not able
                                                 else None),
            "missing": sum(1 for row in able if row["source"] is None),
            "have": sum(1 for row in able if row["source"])}


def _plan(request: Request, report: Mapping[str, Any],
          chosen: settings.Settings) -> dict[str, Any]:
    if request.existing not in EXISTING:
        raise service_errors.RefusedError(t("error.capture.existing_unknown",
                                            existing=request.existing))
    kinds = _kinds(request, report, chosen)
    targets = [_plan_one(game_id, table_id, request, report, chosen, kinds)
               for game_id, table_id in _named(request)]
    rows = [row for target in targets for row in target["kinds"]]
    replacing: dict[str, int] = {}
    for target in targets:
        for source, count in target["replacing_by_source"].items():
            replacing[source] = replacing.get(source, 0) + count
    each = [_counted(kind, [row for row in rows if row["kind"] == kind], report)
            for kind in kinds]
    return {"games": len(targets), "targets": targets, "kinds": each,
            "recording": [kind for kind in kinds
                          if any(kind in target["recording"] for target in targets)],
            "fills": sum(1 for row in rows if row["does"] == FILLED
                         or (row["does"] == REPLACED and not row["goes"])),
            "asks": sum(1 for row in rows if row["does"] == PROPOSED),
            "replacing": sum(replacing.values()), "replacing_by_source": replacing,
            "launches": sum(_launches(target["recording"]) for target in targets),
            "at_once": bool(report["at_once"]),
            "estimate_seconds": sum(target["estimate_seconds"] for target in targets)}


@dataclass(frozen=True)
class Device:
    """This device's screens as a recording reaches them."""

    adapter: adapters.Adapter
    found: Mapping[str, tools.Found]
    config: Any
    screens: Mapping[str, adapters.Output]
    placed: placing.Placing


def reach(report: Mapping[str, Any], game: Any = None, table: str | None = None) -> Device:
    """Where each window of this table would be recorded from, or of the default
    launcher's with no game. Raises UnavailableError where this session has no way to
    record."""
    adapter = adapters.resolve()
    if isinstance(adapter, adapters.Unsupported):
        raise service_errors.UnavailableError(preflight.words(report["reason"]))
    from common.host import display_service

    config = get_ini_config()
    placed = placing.Placing(adapter.outputs(), config, display_service.get_display_monitors(),
                             placing.shown(game, table))
    return Device(adapter, {tool.id: tools.resolve(tool) for tool in adapter.requirements()},
                  config, {window: screen.output for window, screen in placed.screens().items()
                           if screen.output}, placed)


def plan(request: Request, report: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """What `start` would do with the same request, doing nothing."""
    report = preflight.report() if report is None else report
    return _plan(request, report, settings.read(None, request.settings))


def _refuse_now(report: Mapping[str, Any]) -> None:
    if not report["available"]:
        raise service_errors.UnavailableError(preflight.words(report["reason"]))
    if launch_state.current().launching:
        raise service_errors.BlockedError(t("error.launch.already_launching"))


# --- the run in hand ------------------------------------------------------------------

_lock = threading.RLock()
_job: jobs.Job | None = None
# Set to end the game in hand: the session closes its table and keeps nothing.
_halt = threading.Event()
# What a person asked for with the halt: "stop" or "pause".
_asked: dict[str, str] = {}
# A table was launched by anyone but the run while it ran.
_elsewhere = threading.Event()


def _read() -> dict[str, Any] | None:
    try:
        found = json.loads(RUN_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        logger.exception("Could not read the recording run; leaving it")
        return None
    return found if isinstance(found, dict) and found.get("targets") else None


def _running(run: Mapping[str, Any] | None = None) -> bool:
    return (_job is not None and _job.state == jobs.RUNNING
            and (run is None or run.get("job_id") == _job.id))


def _left(run: Mapping[str, Any]) -> int:
    """Seconds the games still to come take, scaled by how the games so far compared with
    their estimates."""
    ahead = sum(int(target.get("estimate") or 0) for target in run["targets"][run["at"]:])
    timed = [one for one in run["outcomes"] if one.get("seconds") and one.get("estimate")]
    if not timed:
        return ahead
    return round(ahead * sum(one["seconds"] for one in timed)
                 / sum(one["estimate"] for one in timed))


def _view(run: Mapping[str, Any]) -> dict[str, Any]:
    running = run.get("state") == RUNNING and _running(run)
    reason = run.get("reason")
    if run.get("state") == RUNNING and not running:
        reason = session.said(INTERRUPTED)
    targets, at = run["targets"], int(run["at"])
    hand = targets[min(at, len(targets) - 1)]
    outcomes = list(run["outcomes"])

    def counted(state: str) -> int:
        return sum(1 for one in outcomes if one.get("state") == state)

    return {"id": run["id"], "state": RUNNING if running else PAUSED, "reason": reason,
            "job_id": run.get("job_id") if running else None,
            "done": at, "of": len(targets),
            "game": {"id": hand["game_id"], "table_id": hand["table_id"],
                     "name": hand["name"]},
            "existing": run["request"]["existing"],
            "recorded": counted(session.RECORDED), "failed": counted(session.FAILED),
            "closed": counted(session.CLOSED), "skipped": counted(session.SKIPPED),
            "proposed": sum(len(one.get("proposed") or []) for one in outcomes),
            "estimate_seconds": _left(run)}


def current() -> dict[str, Any] | None:
    """The run in hand, running or paused; None where there is none."""
    with _lock:
        run = _read()
        return _view(run) if run is not None else None


def _save(run: dict[str, Any]) -> None:
    with _lock:
        RUN_FILE.parent.mkdir(parents=True, exist_ok=True)
        write_atomic(RUN_FILE, lambda handle: json.dump(run, handle, indent=2))
        view = _view(run)
    events.emit(events.CAPTURE_RUN_CHANGED, run=view)


def _clear() -> None:
    with _lock:
        RUN_FILE.unlink(missing_ok=True)
    events.emit(events.CAPTURE_RUN_CHANGED, run=None)


def _request(run: Mapping[str, Any]) -> Request:
    asked = run["request"]
    return Request(tables=[(one["game_id"], one["table_id"]) for one in run["targets"]],
                   kinds=list(asked.get("kinds") or ()), existing=asked["existing"],
                   settings=dict(asked.get("settings") or {}), sound=asked.get("sound"),
                   review=bool(asked.get("review")))


def _device_name() -> str:
    return install_identity.display_name(get_ini_config())


def _folder(game_id: str) -> Path:
    return Path(str(game_lens.game_or_refuse(game_id).full_path_game))


def _needs(planned: Sequence[Mapping[str, Any]], length: int) -> list[space.Need]:
    found = []
    for target in planned:
        rows = list(target["kinds"])
        placed = [row["kind"] for row in rows if row["does"] in (FILLED, REPLACED)]
        kept = [row["kind"] for row in rows if row["does"] == PROPOSED]
        found.append(space.Need(_folder(str(target["game_id"])),
                                space.stored(placed, length), space.stored(kept, length)))
    return found


def _short(planned: Sequence[Mapping[str, Any]], kinds: Sequence[str],
           chosen: settings.Settings) -> bool:
    return space.short(_needs(planned, chosen.length), WORK,
                       space.raw(kinds, chosen.length))


def start(request: Request, kit: session.Kit | None = None) -> jobs.Job:
    """Record these games or tables, one after another, as a job."""
    report = preflight.report()
    _refuse_now(report)
    with _lock:
        if _read() is not None:
            raise service_errors.BlockedError(t("error.capture.run_waiting"))
    chosen = settings.read(None, request.settings)
    planned = _plan(request, report, chosen)
    if planned["replacing"] and request.confirmed != planned["replacing"]:
        raise service_errors.RefusedError(
            t("error.capture.confirm_count", count=planned["replacing"]),
            details={"replacing": planned["replacing"],
                     "by_source": planned["replacing_by_source"]})
    if _short(planned["targets"], planned["recording"], chosen):
        raise service_errors.BlockedError(t("error.capture.no_room",
                                            device=_device_name()))
    run = {"id": uuid.uuid4().hex[:12], "started_at": utc_now_iso(),
           "request": {"existing": request.existing, "kinds": list(request.kinds),
                       "settings": dict(request.settings), "sound": request.sound,
                       "review": request.review},
           "targets": [{"game_id": one["game_id"], "table_id": one["table_id"],
                        "name": one["name"], "estimate": one["estimate_seconds"]}
                       for one in planned["targets"]],
           "at": 0, "outcomes": [], "state": RUNNING, "reason": None}
    return _submit(run, kit)


def _submit(run: dict[str, Any], kit: session.Kit | None) -> jobs.Job:
    global _job
    with _lock:
        _halt.clear()
        _asked.clear()
        _elsewhere.clear()
        job = jobs.submit(jobs.KIND_MEDIA_CAPTURE, lambda job: _work(job, run, kit),
                          stoppable=True)
        _job = job
        run.update(job_id=job.id, state=RUNNING, reason=None)
        _save(run)
    return job


def resume(kit: session.Kit | None = None) -> jobs.Job:
    """Carry on from the game in hand, which is recorded again from its start."""
    with _lock:
        run = _read()
        if run is None:
            raise service_errors.NotFoundError(t("error.capture.no_run"))
        if _running(run):
            raise service_errors.BlockedError(t("error.capture.busy"))
        _refuse_now(preflight.report())
        return _submit(run, kit)


def _close_ours() -> None:
    if launch_state.current().source == launch_state.SOURCE_CAPTURE:
        launch_state.stop()


def pause() -> dict[str, Any] | None:
    """End the game in hand, keeping nothing of it, and hold the run there."""
    with _lock:
        run = _read()
        if run is None:
            raise service_errors.NotFoundError(t("error.capture.no_run"))
        if not _running(run):
            return _view(run)
        _asked["what"] = "pause"
        _halt.set()
    _close_ours()
    return current()


def stop() -> dict[str, Any] | None:
    """End the game in hand, keeping nothing of it, and the run with it. What earlier
    games placed or kept stays."""
    with _lock:
        run = _read()
        if run is None:
            raise service_errors.NotFoundError(t("error.capture.no_run"))
        if not _running(run):
            _clear()
            return None
        _asked["what"] = "stop"
        _halt.set()
    _close_ours()
    return current()


discard = stop


def _heard_launch(**payload: Any) -> None:
    if payload.get("source") != launch_state.SOURCE_CAPTURE:
        _elsewhere.set()


def _outcome(target: Mapping[str, Any], result: session.Result, seconds: float = 0.0,
             estimate: int = 0) -> dict[str, Any]:
    return {"game_id": target["game_id"], "table_id": target["table_id"],
            "name": target["name"], **result.as_dict(),
            "seconds": round(seconds), "estimate": estimate}


def _result(run: Mapping[str, Any], state: str, at_once: bool) -> dict[str, Any]:
    return {"tables": [{key: value for key, value in one.items()
                        if key not in ("seconds", "estimate")} for one in run["outcomes"]],
            "at_once": at_once,
            "run": {"state": state, "reason": run.get("reason"), "done": run["at"],
                    "of": len(run["targets"])}}


def _work(job: jobs.Job, run: dict[str, Any], kit: session.Kit | None) -> dict[str, Any]:
    events.subscribe(events.TABLE_LAUNCHING, _heard_launch)
    try:
        with _lock:
            pass    # until `_submit` has recorded this job as the run's
        result = _go(job, run, kit)
        job.stopped = result["run"]["state"] in ("stopped", PAUSED)
        return result
    finally:
        events.unsubscribe(events.TABLE_LAUNCHING, _heard_launch)


def _held_up(many: bool) -> dict[str, Any] | None:
    """What holds the run before its next game: a person's Pause or Stop, or a table
    someone else launched. {} for a pause with no reason."""
    if _asked.get("what"):
        return {}
    now = launch_state.current()
    if many and (_elsewhere.is_set()
                 or (now.launching and now.source != launch_state.SOURCE_CAPTURE)):
        return session.said(PAUSED_LAUNCHED)
    return None


def _go(job: jobs.Job, run: dict[str, Any], kit: session.Kit | None) -> dict[str, Any]:
    report = preflight.report()
    request = _request(run)
    chosen = settings.read(None, request.settings)
    codec = settings.video_codec(chosen.video_codec)
    kinds = _kinds(request, report, chosen)
    targets = run["targets"]
    many = len(targets) > 1
    at_once = bool(report["at_once"])
    while run["at"] < len(targets):
        target = targets[run["at"]]
        held = _held_up(many)
        if held is not None:
            return _stop_or_pause(run, held or None, at_once)
        job.progress(run["at"], len(targets),
                     t("capture.progress.recording_of", game=target["name"],
                       at=run["at"] + 1, of=len(targets)) if many
                     else t("capture.progress.recording", game=target["name"]))
        try:
            planned = _plan_one(target["game_id"], target["table_id"], request, report,
                                chosen, kinds)
        except service_errors.ServiceError as exc:
            _advance(run, _outcome(target, session.Result(
                session.FAILED, at_once=at_once,
                reason={"key": session.WOULD_NOT_START, "params": {}, "detail": why(exc)})))
            continue
        if not planned["recording"]:
            _advance(run, _outcome(target, session.Result(
                session.SKIPPED, reason=_why_nothing(planned), at_once=at_once)))
            continue
        if many and _short([planned], planned["recording"], chosen):
            return _pause(run, session.said(PAUSED_SPACE, device=_device_name()), at_once)
        began = time.monotonic()
        try:
            result = _record(planned, request, report, chosen, codec, at_once, kit)
        except service_errors.ServiceError as exc:
            result = session.Result(session.FAILED, at_once=at_once, reason={
                "key": session.WOULD_NOT_START, "params": {}, "detail": why(exc)})
        at_once = result.at_once
        if result.state == session.STOPPED:
            return _stop_or_pause(run, None, at_once)
        if result.state == session.CLOSED and many:
            return _pause(run, result.reason, at_once)
        _advance(run, _outcome(target, result, time.monotonic() - began,
                               int(planned["estimate_seconds"])))
    return _end(run, "done", at_once)


def _why_nothing(planned: Mapping[str, Any]) -> dict[str, Any]:
    left = next((row["reason"] for row in planned["kinds"] if row["reason"]), None)
    return dict(left) if left else session.said(session.NOTHING_TO_RECORD)


def _advance(run: dict[str, Any], outcome: dict[str, Any]) -> None:
    run["outcomes"].append(outcome)
    run["at"] += 1
    if run["at"] < len(run["targets"]):
        _save(run)


def _stop_or_pause(run: dict[str, Any], reason: dict[str, Any] | None,
                   at_once: bool) -> dict[str, Any]:
    if _asked.get("what") == "stop":
        return _end(run, "stopped", at_once)
    return _pause(run, reason, at_once)


def _pause(run: dict[str, Any], reason: dict[str, Any] | None,
           at_once: bool) -> dict[str, Any]:
    run.update(state=PAUSED, reason=reason)
    _save(run)
    return _result(run, "paused", at_once)


def _end(run: dict[str, Any], state: str, at_once: bool) -> dict[str, Any]:
    _clear()
    return _result(run, state, at_once)


def _record(planned: Mapping[str, Any], request: Request, report: Mapping[str, Any],
            chosen: settings.Settings, codec: str, at_once: bool,
            kit: session.Kit | None) -> session.Result:
    game = game_lens.game_or_refuse(planned["game_id"])
    try:
        game = launch.this_devices_copy(game)
    except launch.LaunchUnavailableError as exc:
        raise service_errors.UnavailableError(why(exc)) from exc
    key = _launch_key(game, planned["table_id"])
    device = reach(report, game, key)
    propose = frozenset(row["kind"] for row in planned["kinds"] if row["does"] == PROPOSED)
    replace = frozenset(row["kind"] for row in planned["kinds"] if row["does"] == REPLACED)

    def launched(kinds: Sequence[str], view: str, at_once: bool) -> session.Result:
        target = session.Target(planned["game_id"], game, planned["table_id"], key,
                                tuple(kinds), propose=propose, replace=replace, view=view)
        return session.Session(
            target, chosen, adapter=device.adapter, screens=device.screens,
            found=device.found, at_once=at_once, codec=codec, work=WORK,
            config=device.config, kit=kit, placed=device.placed, halt=_halt).run()

    own = [kind for kind in planned["recording"] if kind != session.FSS]
    if session.FSS not in planned["recording"]:
        return launched(own, "", at_once)
    first = launched(own, "", at_once) if own else None
    if first is None:
        return launched((session.FSS,), VIEW_FSS, at_once)
    if first.state in (session.STOPPED, session.CLOSED):
        return first
    if _halt.is_set():
        return _joined(first, session.Result(session.STOPPED, at_once=first.at_once))
    return _joined(first, launched((session.FSS,), VIEW_FSS, first.at_once))


def _joined(first: session.Result, second: session.Result) -> session.Result:
    """What two launches of one table came to, the second after the first."""
    placed = first.placed + second.placed
    proposed = first.proposed + second.proposed
    held_up = second.state in (session.STOPPED, session.CLOSED)
    if held_up:
        state = second.state
    elif placed or proposed:
        state = session.RECORDED
    elif first.state == second.state == session.SKIPPED:
        state = session.SKIPPED
    else:
        state = session.FAILED
    return session.Result(
        state, placed, first.failed + second.failed,
        None if state == session.RECORDED
        else (second.reason if held_up else first.reason or second.reason),
        second.at_once, proposed)


def reset_for_tests() -> None:
    global _job
    with _lock:
        _job = None
        _halt.clear()
        _asked.clear()
        _elsewhere.clear()
