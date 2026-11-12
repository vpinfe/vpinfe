"""The choice an `.info` file of a drop offers: how it is merged into the details the game
already has, or, for a game with none, what a first import takes.

The server marks the row (`plan.items[].info`); the sheet keeps the mode chosen and a
file not wanted is an unticked row.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from nicegui import ui

from common.i18n import t
from manager.kit import panel
from manager.library.drop_sheet import Sheet

SKIP = "skip"
FILL = "fill"
FACTS = "facts"
ALL = "all"


def draw(sheet: Sheet, item: dict[str, Any], *, is_ticked: Callable[[], bool], single: bool,
         set_ticked: Callable[[bool], Any]) -> Callable[[], None] | None:
    """The choice of what the file does, under the label that names the case: how it is
    merged into details the game has, or what a first import takes. Not drawn for a file
    the server did not mark, and a lone file has no way to be left out here.

    Answers what brings the choice in step with the row's box after the box is changed, or
    None where nothing was drawn.
    """
    view = item.get("info")
    if not view:
        return None
    member = str(item.get("member") or item.get("name") or "")
    modes = [str(one) for one in view.get("modes") or ()]
    first = not view.get("existing")
    recommended = FACTS if first else FILL
    if first:
        asks = t("manager.info_import.first.asks")
        labels = {FACTS: t("manager.info_import.first.facts.choice"),
                  ALL: t("manager.info_import.first.all.choice")}
        says = {FACTS: t("manager.info_import.first.facts.says"),
                ALL: t("manager.info_import.first.all.says")}
        skips = t("manager.info_import.first.skip.says")
    else:
        asks = t("manager.info_import.asks")
        labels = {mode: t(f"manager.info_import.{mode}.choice") for mode in modes}
        says = {mode: t(f"manager.info_import.{mode}.says") for mode in modes}
        skips = t("manager.info_import.skip.says")
    labels[recommended] = t("manager.drop_sheet.recommended", name=labels[recommended])
    if not single:
        labels[SKIP] = t("manager.info_import.skip.choice")
        says[SKIP] = skips

    def shown() -> str:
        if not is_ticked():
            return SKIP
        return sheet.info_modes.get(member) or str(view.get("mode") or recommended)

    def chose(e: Any) -> Any:
        value = str(e.value or "")
        if value == SKIP:
            return set_ticked(False) if is_ticked() else None
        sheet.info_modes[member] = value
        return None if is_ticked() else set_ticked(True)

    with ui.column().classes("gap-1 manager-convert-col manager-info-col"):
        ui.label(asks).classes("manager-help")
        with ui.element("div").classes("manager-fact-edit w-full"):
            select = panel.ExplainedSelect(
                labels, value=shown(), says=says,
                apart=SKIP if SKIP in labels else None, on_change=chose)

    def follow_the_box() -> None:
        select.value = shown()

    return follow_the_box
