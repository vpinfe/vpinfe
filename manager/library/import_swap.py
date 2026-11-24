"""What the import confirmation asks a game that already has a table: replace one of its
tables or keep both, and which of the old table's files go with the new one.

The plan decides what each choice does; this holds what the person has said and draws it.
A change is asked of the plan again, so the cards and the ticks are always what an import
with these answers would do.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from nicegui import ui

from common.i18n import t
from manager.kit import game_tables, offload, panel, undo


@dataclass
class Swapping:
    """The answers so far. `carry` is None until the plan has said what is ticked by default."""

    tables: list[dict[str, Any]]
    replace: bool
    table: str
    carry: list[str] | None = None
    instead: bool = False

    @classmethod
    def of(cls, choice: dict[str, Any] | None, *, instead: bool = False) -> Swapping | None:
        if not choice or not choice.get("tables"):
            return None
        return cls(list(choice["tables"]), bool(choice.get("replace")),
                   str(choice.get("table") or ""), instead=instead)

    def asked(self, table_on: bool = True) -> dict[str, Any]:
        """What the plan and the import are asked with, over the caller's own. With the table
        row unticked nothing is carried."""
        if not self.replace:
            return {"add_table": True, "replace_table": "", "carry": None, "instead": False}
        return {"add_table": False, "replace_table": self.table,
                "carry": self.carry if table_on else [], "instead": self.instead}

    def label_of(self, table_id: str) -> str:
        found = next((one for one in self.tables if one["id"] == table_id), None)
        return str((found or {}).get("label") or "")

    def settle(self, plan: dict[str, Any], table_on: bool = True) -> None:
        """Take the plan's word for what is ticked, until the person ticks otherwise."""
        swap = swap_of(plan)
        if table_on and self.replace and self.carry is None and swap is not None:
            self.carry = [str(one["kind"]) for one in swap["files"] if one["carry"]]


def swap_of(plan: dict[str, Any]) -> dict[str, Any] | None:
    return next((one["swap"] for one in plan.get("items") or () if one.get("swap")), None)


def new_name(plan: dict[str, Any], release_label: str = "") -> str:
    """What the arriving table is called: its release where one is named, else its file."""
    if release_label:
        return release_label
    return next((str(one.get("name") or "") for one in plan.get("items") or ()
                 if one.get("kind") == "table"), "")


def table_on(plan: dict[str, Any], chosen: dict[int, bool]) -> bool:
    """Whether the table row of a replacing plan is ticked, which is true of any other plan."""
    return all(chosen.get(int(one["index"]), True)
               for one in plan.get("items") or () if one.get("swap"))


def title(plan: dict[str, Any], swapping: Swapping | None, release_label: str = "",
          on: bool = True) -> str:
    swap = swap_of(plan)
    if swapping is None or not swapping.replace or swap is None or not on:
        return ""
    return t("manager.import_swap.title", old=swap["label"],
             new=new_name(plan, release_label))


def said(library: Any, swapped: dict[str, Any], *, old: str, new: str,
         after: Callable[[], Any] | None = None, partly: bool = False) -> None:
    """Tell what a swap did, with Undo swapping the table back to the file it replaced.
    `after` redraws what shows the table once the Undo is done. `partly` says another item of
    the same import failed."""
    game_id = str(swapped["game"])
    table_id = str(swapped["table"]["id"])
    copies = [str(one["id"]) for one in swapped.get("kept") or ()]
    tied = swapped.get("source")

    async def back() -> None:
        await offload.io(library.swap_table_back, game_id, table_id, copies, tied)
        if after is not None:
            answer = after()
            if hasattr(answer, "__await__"):
                await answer

    line = t("manager.import_swap.replaced_partly" if partly else "manager.import_swap.replaced",
             old=old, new=new)
    if not copies:
        ui.notify(line, type="warning" if partly else "positive")
        return
    undo.offer(line, back, done=t("manager.import_swap.swapped_back", old=old))


def confirm_label(swapping: Swapping | None, on: bool = True) -> str:
    if swapping is None or (swapping.replace and not on):
        return t("manager.import_dialog.import")
    return t("word.replace") if swapping.replace else t("manager.import_swap.keep_both")


def draw(box: Any, plan: dict[str, Any], swapping: Swapping, *, game: str,
         release_label: str, changed: Callable[[], Any], on: bool = True) -> None:
    """The section, drawn again into `box`. `changed` asks the plan again once an answer
    moves."""
    box.clear()
    with box:
        ui.label(t("manager.import_swap.has_table", name=game)).classes("manager-member-name")
        panel.segmented(
            {True: t("manager.import_swap.replace_one",
                     old=swapping.label_of(swapping.table)),
             False: t("manager.import_swap.keep_both")},
            swapping.replace, lambda e: _chose(swapping, bool(e.value), changed))
        if not swapping.replace:
            return
        if len(swapping.tables) > 1:
            panel.facts(ui, [(t("manager.import_swap.which"), panel.select(
                {one["id"]: one["label"] for one in swapping.tables}, swapping.table,
                lambda e: _moved(swapping, str(e.value or ""), changed)))])
        swap = swap_of(plan)
        if swap is not None and not on:
            ui.label(t("manager.import_swap.stays", old=swap["label"])).classes("manager-help")
        elif swap is not None:
            _cards(swap, new_name(plan, release_label))
            _carry(swap, swapping, new_name(plan, release_label), changed)


def _chose(swapping: Swapping, replace: bool, changed: Callable[[], Any]) -> Any:
    if replace == swapping.replace:
        return None
    swapping.replace = replace
    return changed()


def _moved(swapping: Swapping, table: str, changed: Callable[[], Any]) -> Any:
    if not table or table == swapping.table:
        return None
    swapping.table = table
    swapping.carry = None
    return changed()


def _cards(swap: dict[str, Any], new: str) -> None:
    def named(name: str, file: str) -> Callable[[], None]:
        def draw_card() -> None:
            with ui.column().classes("gap-0 min-w-0"):
                ui.label(name).classes("manager-member-name truncate")
                if file and file != name:
                    ui.label(file).classes("manager-member-table truncate")

        return draw_card

    panel.facts(ui, [(t("word.replace"), named(str(swap["label"]), str(swap["filename"]))),
                     (t("manager.import_swap.with"), named(new, ""))])


def _carry(swap: dict[str, Any], swapping: Swapping, new: str,
           changed: Callable[[], Any]) -> None:
    """One tick per file the old table has under its own name."""
    held = list(swap["files"])
    if held:
        ui.label(t("manager.import_swap.carry", new=new)).classes("manager-member-name")
        with ui.column().classes("gap-0 w-full"):
            for one in held:
                _tick(one, swapping, new, changed)
    ui.label(t("manager.import_swap.to_backups", old=swap["label"])).classes("manager-help")
    patched = game_tables.made_from_it_line(list(swap.get("patched") or []))
    if patched:
        with ui.row().classes("items-center gap-1 no-wrap min-w-0"):
            ui.icon("error_outline").classes("manager-attention-icon")
            ui.label(patched).classes("manager-member-table truncate")


def _tick(one: dict[str, Any], swapping: Swapping, new: str,
          changed: Callable[[], Any]) -> None:
    kind = str(one["kind"])

    def picked(event: Any) -> Any:
        kept = set(swapping.carry or ())
        if event.value:
            kept.add(kind)
        else:
            kept.discard(kind)
        swapping.carry = sorted(kept)
        return changed()

    with ui.row().classes("items-center gap-2 no-wrap"):
        ui.checkbox(t(f"asset.kind.{kind}.label"), value=bool(one["carry"]),
                    on_change=picked).props("dense").tooltip(str(one["file"]))
        if one.get("brought"):
            ui.label(t("manager.import_swap.brings", new=new)).classes("manager-help")
