"""The dialog that asks which tables are to have a game's file.

A row per table, ticked where the table uses the game's file. Saving makes the ticks true:
see `move_plan` for what each tick does to the files, and `file_move` for running it.
"""

from __future__ import annotations

from typing import Any

from nicegui import ui

from common.i18n import t
from manager.kit import dialog as frame
from manager.kit import media_ownership, verbs, when
from manager.library import move_plan
from manager.library.discovery_ext import size_text
from manager.library.move_plan import Outcome, Slot

# A line under a table's name: its text, and whether it warns.
Line = tuple[str, bool]

# The order several files' lines are said in.
_CODES = (move_plan.REPLACES, move_plan.GETS_OWN, move_plan.GETS_COPY, move_plan.LOSES,
          move_plan.USES, move_plan.OWN)


def noun(label: str) -> str:
    """A kind's name inside a sentence: lower case, unless it is a name or an acronym."""
    return label if label[1:] != label[1:].lower() else label.lower()


def joined(names: list[str]) -> str:
    if len(names) < 2:
        return "".join(names)
    return t("manager.file_move.and", head=", ".join(names[:-1]), last=names[-1])


def slot_key(slot: Slot) -> str:
    return f"{slot.family}:{slot.kind}"


def initial(slots: list[Slot], row_id: str) -> bool | None:
    """Whether the table uses every slot's file today, none of them, or some."""
    uses = [move_plan.uses_now(one, one.row(row_id)) for one in slots]
    return True if all(uses) else False if not any(uses) else None


def choices_of(slots: list[Slot]) -> dict[str, bool | None]:
    return {one["id"]: initial(slots, one["id"]) for one in slots[0].rows
            if not one["folder_named"]}


def targets(slots: list[Slot], choices: dict[str, bool | None]) -> dict[str, set[str]]:
    return {slot_key(one): move_plan.target(one, choices) for one in slots}


def outcomes(slots: list[Slot], choices: dict[str, bool | None]) -> dict[str, Outcome]:
    return {key: move_plan.outcome(one, chosen)
            for one, (key, chosen) in zip(slots, targets(slots, choices).items(),
                                          strict=True)}


def changed(slots: list[Slot], choices: dict[str, bool | None]) -> list[Slot]:
    """The slots whose set of tables the choices change."""
    wanted = targets(slots, choices)
    return [one for one in slots if wanted[slot_key(one)] != move_plan.readers(one)]


def blocked(slots: list[Slot], found: dict[str, Outcome]) -> list[Slot]:
    """The changed slots left with no table to have the file."""
    return [one for one in slots
            if found[slot_key(one)].changes and found[slot_key(one)].problem
            == move_plan.NO_TABLE]


def lines(slots: list[Slot], row_id: str, found: dict[str, Outcome]) -> list[Line]:
    """What this table ends up with, in words, for every slot."""
    row = slots[0].row(row_id)
    ends = {slot_key(one): found[slot_key(one)].ends.get(row_id, move_plan.NONE)
            for one in slots}
    said: list[Line] = []
    if len(slots) == 1:
        said.extend(_single(slots[0], row, ends[slot_key(slots[0])], found, row_id))
    else:
        said.extend(_several(slots, ends))
    if row["folder_named"]:
        return said
    if any(one.kind == "pov" and one.family == "asset" for one in slots) and row["ini"]:
        chosen = any(ends[slot_key(one)] in (move_plan.GETS_OWN, move_plan.GETS_COPY,
                                             move_plan.REPLACES)
                     for one in slots if one.kind == "pov")
        said.append((t("manager.file_move.row.ini"), chosen))
    return said


def _single(slot: Slot, row: dict[str, Any], end: str, found: dict[str, Outcome],
            row_id: str) -> list[Line]:
    kind = noun(slot.label)
    if end == move_plan.REPLACES:
        own = row["own"] or {}
        return [(t("manager.file_move.row.replaces", size=size_text(int(own.get("size") or 0)),
                   date=when.day(when.from_epoch(float(own.get("modified") or 0)))), True),
                (t("manager.file_move.row.kept"), False)]
    if row["folder_named"]:
        return _folder_named(slot, kind, end, found)
    return {
        move_plan.USES: [(t("manager.file_move.row.uses"), False)],
        move_plan.GETS_OWN: [(t("manager.file_move.row.gets_own"), False)],
        move_plan.GETS_COPY: [(t("manager.file_move.row.gets_copy"), False)],
        move_plan.LOSES: [(t("manager.file_move.row.loses", kind=kind), True)],
        move_plan.OWN: [(t("manager.file_move.row.own"), False)],
        move_plan.NONE: [(t("manager.file_move.row.none", kind=kind), False)],
    }[end]


def _folder_named(slot: Slot, kind: str, end: str, found: dict[str, Outcome]) -> list[Line]:
    if not slot.state.get("fallback"):
        return [(t("manager.file_move.row.loses", kind=kind), True)] \
            if end == move_plan.LOSES else [(t("manager.file_move.row.uses"), False)]
    if end == move_plan.USES:
        return [(t("manager.file_move.row.uses"), False),
                (t("manager.file_move.row.same_file", kind=kind), False)]
    waiting = [one["stem"] for one in slot.rows
               if one["id"] in move_plan.readers(slot) and one["id"] not in
               _chosen(slot, found)]
    return [(t("manager.file_move.row.loses", kind=kind), True),
            (t("manager.file_move.row.keeps_if" if len(waiting) < 2
                else "manager.file_move.row.keeps_if_all",
                kind=kind, tables=joined(waiting)), False)]


def _chosen(slot: Slot, found: dict[str, Outcome]) -> set[str]:
    return {key for key, end in found[slot_key(slot)].ends.items()
            if end in (move_plan.USES, move_plan.GETS_OWN, move_plan.GETS_COPY,
                       move_plan.REPLACES)}


def _several(slots: list[Slot], ends: dict[str, str]) -> list[Line]:
    said: list[Line] = []
    for code in _CODES:
        names = [one.label for one in slots if ends[slot_key(one)] == code]
        if not names:
            continue
        if code == move_plan.LOSES and len(names) == len(slots):
            said.append((t("manager.file_move.row.loses_all", count=len(slots)), True))
            continue
        said.append((t(f"manager.file_move.row.many_{code}", kinds=joined(names)),
                     code in (move_plan.REPLACES, move_plan.LOSES)))
    if any(ends[slot_key(one)] == move_plan.REPLACES for one in slots):
        said.append((t("manager.file_move.row.kept"), False))
    return said


def title(slots: list[Slot]) -> str:
    if len(slots) == 1:
        return t("manager.file_move.title", kind=slots[0].label)
    return t("manager.file_move.title_several", count=len(slots))


def none_hint(slots: list[Slot], found: dict[str, Outcome]) -> str:
    stuck = blocked(slots, found)
    if not stuck:
        return ""
    if len(slots) == 1:
        return t("manager.file_move.none_one")
    return t("manager.file_move.none_some", kinds=joined([one.label for one in stuck]))


async def ask(slots: list[Slot]) -> dict[str, list[str]] | None:
    """Which tables are to have each changed slot's file, by `slot_key`; None where the
    dialog was left."""
    choices = choices_of(slots)
    found = outcomes(slots, choices)
    holders: dict[str, Any] = {}
    boxes: dict[str, Any] = {}

    def settle() -> None:
        nonlocal found
        found = outcomes(slots, choices)
        for row in slots[0].rows:
            row_id = row["id"]
            if row["folder_named"]:
                ends = [found[slot_key(one)].ends.get(row_id) for one in slots]
                boxes[row_id].set_value(all(end == move_plan.USES for end in ends))
            holders[row_id].clear()
            with holders[row_id]:
                for text, warn in lines(slots, row_id, found):
                    ui.label(text).classes(
                        "manager-move-warn manager-help" if warn else "manager-help")
        stuck = none_hint(slots, found)
        hint.text = stuck
        hint.set_visibility(bool(stuck))
        save.set_enabled(bool(changed(slots, choices)) and not blocked(slots, found)
                         and not any(one.problem for one in found.values()
                                     if one.changes))

    def picked(row_id: str, value: bool | None) -> None:
        choices[row_id] = value
        settle()

    with frame.opened(title(slots), tall=True) as box:
        with ui.column().classes("w-full gap-1"):
            for one in slots:
                with ui.row().classes("items-center gap-2 no-wrap"):
                    ui.label(", ".join(one.state["game"]["files"])) \
                        .classes("manager-confirm-line min-w-0")
                    media_ownership.badge_for(media_ownership.GAME)
        ui.label(t("word.used_by")).classes("manager-fact-heading")
        with ui.column().classes("w-full gap-0 manager-moves-list"):
            for row in slots[0].rows:
                row_id = row["id"]
                with ui.row().classes("w-full items-start gap-2 no-wrap manager-moves-row"):
                    mark = ui.checkbox(row["stem"], value=bool(choices.get(row_id))) \
                        .props("dense")
                    if row["folder_named"]:
                        mark.set_enabled(False)
                    else:
                        if choices[row_id] is None:
                            mark.set_value(None)
                        mark.on_value_change(
                            lambda event, row_id=row_id: picked(row_id, event.value))
                    boxes[row_id] = mark
                with ui.column().classes("gap-0 pl-8 manager-moves-lines") as lines_box:
                    pass
                holders[row_id] = lines_box
        with frame.footer():
            hint = ui.label("").classes("manager-move-warn manager-help manager-dialog-aside")
            frame.cancel(lambda: box.submit(None))
            save = frame.answer(t("word.save"), lambda: box.submit(
                {slot_key(one): sorted(targets(slots, choices)[slot_key(one)])
                 for one in changed(slots, choices)}), icon=verbs.SAVE)
        settle()
    return await box
