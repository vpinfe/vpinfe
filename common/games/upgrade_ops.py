"""What an upgrade of a launcher's program left in its settings, and moving it.

The app says what was left (`left_behind`) and what each setting becomes. This turns that
into the notice's rows, writes the settings that move, and puts a box the settings held into
each backglass file that has none. Everything it writes is journaled so one call undoes it.

A box is carried only where the file places no DMD of its own for that window: what a file
already says is the one in force. A box any of whose files cannot be written stays where it
is, whole, and is named under `held_back`; none of its files is written. So is a box that
would take the move past the files the undo journal keeps, and every box after it, so that
Undo can always put back everything a move wrote. Everything that can refuse does so before
anything is written, the files go first and the settings last, and a settings write that
fails puts the files back.
"""

from __future__ import annotations

import logging
import secrets
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

from common import failures, i18n, service_errors
from common.apps.contract import MOVE, PLACE, SCOPE_LAUNCHER, TURN_ON, Leftover
from common.games import backglass_displays, backglass_ops, backglass_order, launcher_ops
from common.i18n import t

logger = logging.getLogger("vpinfe.common.games.upgrade_ops")

_KEPT = 16
_ON = ("1", "true")
_lock = threading.Lock()


@dataclass
class _Done:
    launcher_id: str
    before: dict[str, str]
    after: dict[str, str]
    placed: list[tuple[str, str, str]] = field(default_factory=list)


# Moves that can still be undone, oldest first. Not kept across a restart.
_done: OrderedDict[str, _Done] = OrderedDict()


def _words(launcher_id: str, item: Leftover) -> dict[str, str]:
    app = launcher_ops.launcher_or_refuse(launcher_id).app
    return {"text": i18n.literal_or("", f"app.{app}.leftover.{item.word}")[0]}


def _after_the_move(held: dict[str, str], items: list[Leftover]) -> dict[str, str]:
    """The settings as they will stand once the moves are written."""
    for item in items:
        if item.action == MOVE:
            held = {**held, item.to: item.new_value, **dict.fromkeys(dict(item.drops), "")}
    return held


def _in_file(item: Leftover, space: tuple[int, int]) -> tuple[int, int, int, int]:
    """The box the settings hold as the file counts it: pixels of `space` from its top
    left, where the settings count y up from the bottom, in `item.frame` where the program
    counts in a frame of its own."""
    assert item.box is not None
    x, y, wide, high = item.box
    across, down = item.frame or space
    return (max(0, round(space[0] * x / across)),
            max(0, round(space[1] * (down - y - high) / down)),
            max(1, round(space[0] * wide / across)),
            max(1, round(space[1] * high / down)))


def _space(file: dict[str, Any], window: str, cut: bool) -> tuple[int, int] | None:
    """The pixels a box in this window is counted in: the art, less the grill where it is
    cut. None where the file has no art for the window."""
    if window == "ScoreView":
        return tuple(file["scoreview_art"]) if file["scoreview_art"] else None  # type: ignore[return-value]
    wide, high = file["size"]
    grill = file["grill"]
    return (wide, high - grill) if cut and 0 < grill < high else (wide, high)


def _targets(files: list[dict[str, Any]], item: Leftover, cut: bool) -> list[dict[str, Any]]:
    """The files that would take this box, each with the box in its own pixels. A file
    that places a DMD in this window already, or has no art for it, takes none."""
    found: list[dict[str, Any]] = []
    for file in files:
        space = _space(file, item.target, cut)
        if space is None or any(one[0] == item.target for one in file["placed"]):
            continue
        found.append({"game": file["game"], "path": file["path"], "file": file,
                      "box": _in_file(item, space)})
    return found


def _cut(launcher_id: str, item: Leftover, before: dict[str, str],
         after: dict[str, str]) -> bool:
    """Whether the box was counted on the art without its grill. A box that names the setting
    it was counted under is cut while that setting was on before the move; any other is cut
    as the program in effect will cut it once the moves are written."""
    if item.cut_if:
        return before.get(item.cut_if, "").lower() in _ON
    return backglass_ops.cuts_grill(after, launcher_ops.program_of(launcher_id))


def listing(launcher_id: str) -> dict[str, Any]:
    """What this launcher's program left behind, as rows the Manager can say. `files` is
    how many backglass files a box would go into, and `ids` the keys that name the rows,
    which a dismissal remembers."""
    items = list(launcher_ops.left_behind(launcher_id))
    boxes = any(one.action == PLACE for one in items)
    held = backglass_ops.files_of(launcher_id) if boxes else []
    before = launcher_ops.effective_values(launcher_id) if boxes else {}
    after = _after_the_move(before, items) if boxes else {}
    rows: list[dict[str, Any]] = []
    for item in items:
        rows.append({"key": item.key, "action": item.action, "to": item.to,
                     "value": item.value,
                     "files": (len(_targets(held, item, _cut(launcher_id, item, before, after)))
                               if item.action == PLACE else 0),
                     **_words(launcher_id, item)})
    return {"launcher": launcher_id, "items": rows}


def _remember(done: _Done) -> str:
    token = secrets.token_urlsafe(9)
    with _lock:
        _done[token] = done
        while len(_done) > _KEPT:
            _done.popitem(last=False)
    return token


def _write(launcher_id: str, values: dict[str, str]) -> str:
    """The id of the job that brings the library's tables to the new order where the write
    started one, else empty."""
    done = launcher_ops.write_config(launcher_id, {"scope": SCOPE_LAUNCHER, "values": values})
    return str(done.get("settling") or "") if isinstance(done, dict) else ""


def _answer(token: str, moved: list[str], held_back: list[dict[str, Any]],
            order_failed: list[dict[str, str]] | None = None,
            settling: str = "") -> dict[str, Any]:
    return {"moved": moved, "held_back": held_back, "undo": token,
            "order_failed": order_failed or [], **({"settling": settling} if settling else {})}


def _settled(placed: list[tuple[str, str, str]]) -> list[dict[str, str]]:
    """Each game a file was written for, brought to what its files place now. The tables
    that could not be written, each with why."""
    failed: list[dict[str, str]] = []
    for game_id in dict.fromkeys(game for game, _path, _token in placed):
        failed.extend(backglass_order.settle(game_id))
    return failed


class _Box:
    """One box and what carrying it needs: the files that would take it, the files that
    refused, and the bytes its changes take in the undo journal. `limit` is the number of
    files the journal keeps where this box was left out for being one too many."""

    def __init__(self, item: Leftover) -> None:
        self.item = item
        self.targets: list[dict[str, Any]] = []
        self.refused: list[dict[str, str]] = []
        self.size = 0
        self.limit = 0

    def held(self) -> dict[str, Any]:
        """How `held_back` names this box: its key, the files that refused, and `limit` where
        it was one box too many for the undo journal."""
        return {"key": self.item.key, "files": self.refused,
                **({"limit": self.limit} if self.limit else {})}


def _plan(launcher_id: str, items: list[Leftover]) -> list[_Box]:
    """Every box, with each of its files asked whether it can be written now. A box with a
    file that refuses takes none of them, and neither does one that would take the move past
    the files the undo journal keeps: the boxes go in order, and the first that does not fit
    and every box after it stay in the settings, whole."""
    boxes = [one for one in items if one.action == PLACE]
    if not boxes:
        return []
    kept, _biggest = backglass_displays.limits()
    used = 0
    full = False
    held = backglass_ops.files_of(launcher_id)
    before = launcher_ops.effective_values(launcher_id)
    after = _after_the_move(before, items)
    planned: list[_Box] = []
    for item in boxes:
        box = _Box(item)
        for target in _targets(held, item, _cut(launcher_id, item, before, after)):
            try:
                box.size += backglass_displays.check(target["game"], target["path"],
                                                     item.target, target["box"])
            except (service_errors.ServiceError, OSError) as exc:
                box.refused.append({"path": target["path"], "reason": failures.why(exc)})
            else:
                box.targets.append(target)
        if box.refused:
            box.targets, box.size = [], 0
        elif box.targets and (full or used + len(box.targets) > kept):
            box.targets, box.size, box.limit = [], 0, kept
            full = True
        used += len(box.targets)
        planned.append(box)
    return planned


def _refuse_unkeepable(boxes: list[_Box]) -> None:
    """The undo journal keeps so many bytes. A move it could not take whole would lose a
    file from its Undo, so it does not start."""
    _kept, biggest = backglass_displays.limits()
    if sum(one.size for one in boxes) > biggest:
        raise service_errors.BlockedError(t("error.upgrade.too_large"),
                                          details={"limit": biggest})


def _roll_back(placed: list[tuple[str, str, str]]) -> None:
    """Put the files back, newest change first. One that cannot be put back is logged and
    the rest still are."""
    for game_id, path, token in reversed(placed):
        try:
            backglass_displays.undo(game_id, token)
        except Exception:  # noqa: BLE001 - the error being raised is the one to report
            logger.exception("Could not put back %s after a failed move", path)


def move(launcher_id: str) -> dict[str, Any]:
    """Move every setting that has a new name and carry every box into its files. Whatever
    can refuse is asked first. Then the files are written and the settings once, last; if
    any write fails the files already written are put back and the error is raised. Answers
    the keys moved, the boxes held back with the files that refused, and the token that
    undoes it all."""
    items = list(launcher_ops.left_behind(launcher_id))
    launcher_ops.refuse_unwritable(launcher_id)
    backglass_order.refuse_while_running()
    planned = _plan(launcher_id, items)
    _refuse_unkeepable(planned)
    values: dict[str, str] = {}
    before: dict[str, str] = {}
    moved: list[str] = []
    for item in items:
        if item.action == MOVE:
            values.update({item.to: item.new_value, **dict.fromkeys(dict(item.drops), "")})
            before.update({item.to: "", **dict(item.drops)})
            moved.append(item.key)
    held_back = [one.held() for one in planned if one.refused or one.limit]
    placed: list[tuple[str, str, str]] = []
    settling = ""
    try:
        for box in planned:
            carried = False
            for target in box.targets:
                if any(one[0] == box.item.target for one in target["file"]["placed"]):
                    continue
                answer = backglass_displays.place(target["game"], target["path"],
                                                  box.item.target, target["box"])
                if answer.get("undo"):
                    placed.append((target["game"], target["path"], answer["undo"]))
                elif answer.get("undo_refused"):
                    raise service_errors.BlockedError(t("error.upgrade.too_large"))
                target["file"]["placed"].append((box.item.target, *target["box"]))
                carried = True
            if carried:
                values.update(dict.fromkeys(dict(box.item.drops), ""))
                before.update(dict(box.item.drops))
                moved.append(box.item.key)
        if values:
            settling = _write(launcher_id, values)
    except BaseException:
        _roll_back(placed)
        raise
    failed = [] if settling else _settled(placed)
    if not values and not placed:
        return _answer("", moved, held_back, failed)
    token = _remember(_Done(launcher_id, before, values, placed))
    return _answer(token, moved, held_back, failed, settling)


def turn_on(launcher_id: str, key: str) -> dict[str, Any]:
    """Write the value that turns on the setting `key` names, which the notice only
    names. Answers the token that undoes it."""
    item = next((one for one in launcher_ops.left_behind(launcher_id)
                 if one.action == TURN_ON and one.key == key), None)
    if item is None:
        raise service_errors.NotFoundError(t("error.upgrade.unnamed", key=key))
    launcher_ops.refuse_unwritable(launcher_id)
    backglass_order.refuse_while_running()
    settling = _write(launcher_id, {item.to: item.new_value})
    token = _remember(_Done(launcher_id, {item.to: item.value}, {item.to: item.new_value}))
    return _answer(token, [key], [], settling=settling)


def _refuse_files(placed: list[tuple[str, str, str]]) -> None:
    """Refuse, touching nothing, unless every file can be put back as a whole."""
    games: dict[str, list[str]] = {}
    for game_id, _path, token in placed:
        games.setdefault(game_id, []).append(token)
    for game_id, tokens in games.items():
        try:
            backglass_displays.check_undo(game_id, tokens)
        except service_errors.BlockedError as exc:
            raise service_errors.BlockedError(
                t("error.upgrade.files"),
                details={"files": [{"path": (exc.details or {}).get("path", ""),
                                    "reason": failures.why(exc)}]}) from None


def undo(launcher_id: str, token: str) -> dict[str, Any]:
    """Put back what the move with this token wrote: the backglass files first, byte for
    byte and newest change first, then the settings. Refused whole, with nothing touched and
    the token still good, when a setting or any file has been changed since. The settings
    come back as blanks, so the file need not be as it was byte for byte."""
    with _lock:
        done = _done.pop(token, None)
        if done is not None and done.launcher_id != launcher_id:
            _done[token] = done
            done = None
    if done is None:
        raise service_errors.NotFoundError(t("error.upgrade.expired"))
    try:
        failed, settling = _put_back(launcher_id, done)
    except BaseException:
        with _lock:
            _done[token] = done
        raise
    return {"restored": sorted(done.before), "order_failed": failed,
            **({"settling": settling} if settling else {})}


def _put_back(launcher_id: str, done: _Done) -> tuple[list[dict[str, str]], str]:
    if done.before:
        launcher_ops.refuse_unwritable(launcher_id)
    backglass_order.refuse_while_running()
    held = launcher_ops.effective_values(launcher_id)
    if any(held.get(key, "") != value for key, value in done.after.items() if value):
        raise service_errors.BlockedError(t("error.upgrade.changed"))
    _refuse_files(done.placed)
    stuck: list[dict[str, str]] = []
    left: list[tuple[str, str, str]] = []
    for game_id, path, one in reversed(done.placed):
        try:
            backglass_displays.undo(game_id, one)
        except (service_errors.ServiceError, OSError) as exc:
            stuck.append({"path": path, "reason": failures.why(exc)})
            left.append((game_id, path, one))
    if stuck:
        _settled(done.placed)
        done.placed = list(reversed(left))
        raise service_errors.BlockedError(t("error.upgrade.files"), details={"files": stuck})
    try:
        settling = _write(launcher_id, done.before) if done.before else ""
    except BaseException:
        _settled(done.placed)
        raise
    return ([] if settling else _settled(done.placed)), settling
