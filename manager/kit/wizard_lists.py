"""The wizard field for a long list to tick, and a picture drawn from the person's own files.

`checklist` is for hundreds of rows, where `multi` is a closed dropdown; it hands the wizard
a control whose `value` is the answer, so `wizard._sync` reads it the way it reads any other.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from nicegui import ui

from common.i18n import t
from manager.kit import panel, verbs


class Answer(ui.element):
    """The box a list is drawn in. `value` is what the person has answered so far."""

    def __init__(self, value: Any) -> None:
        super().__init__("div")
        self.value = value


def heading(question: str) -> None:
    ui.label(question).classes("manager-card-title manager-wizard-question")


def checklist(field: dict[str, Any], ticked: Sequence[str],
              on_change: Callable[[list[str]], None], *, question: str = "",
              on_control: Callable[[Any], None]) -> Callable[[], None]:
    """`rows` are `{key, name, note, groups}` and `chips` are `[group, label]`. A chip is
    drawn only when a row is in its group, and picks the rows to show. The answer is the
    keys ticked, in the rows' order."""
    rows = [one for one in field.get("rows") or [] if one.get("key")]
    present = {str(group) for one in rows for group in one.get("groups") or ()}
    chips = [(str(one[0]), str(one[1])) for one in field.get("chips") or []
             if str(one[0]) in present]

    def draw() -> None:
        ticks = {str(key) for key in ticked}
        shown: dict[str, ui.element] = {}
        boxes: dict[str, ui.checkbox] = {}
        looking = {"text": "", "group": ""}
        bulk = {"on": False}
        with ui.column().classes("w-full gap-2"):
            if question:
                heading(question)
            with ui.row().classes("items-center gap-3 w-full no-wrap"):
                find = panel.search(t("manager.wizard_lists.filter")).props("debounce=150")
                find.classes(remove="w-64", add="grow")
                find.on("keyup.enter.stop", lambda: None)
                if chips:
                    chosen = panel.segmented(
                        {"": t("word.all"), **dict(chips)}, "",
                        lambda event: look(group=str(event.value or "")))
                    chosen.props["aria-label"] = t("manager.wizard_lists.show")
            with ui.row().classes("items-center gap-2 w-full no-wrap"):
                count = ui.label("").classes("manager-help grow")
                panel.action(t("manager.wizard_lists.select_all"),
                             lambda: setall(True), icon=verbs.ACCEPT,
                             hint=panel.NO_HINT)()
                panel.action(t("manager.wizard_lists.select_none"),
                             lambda: setall(False), icon=verbs.CLEAR,
                             hint=panel.NO_HINT)()
            box = Answer([one["key"] for one in rows if str(one["key"]) in ticks]) \
                .classes("manager-wizard-list")
            empty = ui.label(t("manager.wizard_lists.no_match")).classes("manager-help px-3")
            empty.set_visibility(False)
            with box:
                for one in rows:
                    key = str(one["key"])
                    row = ui.element("div").classes("manager-wizard-listrow")
                    with row:
                        tick = ui.checkbox(value=key in ticks).props("dense")
                        with ui.column().classes("gap-0 min-w-0 grow") as words:
                            ui.label(str(one.get("name") or key)) \
                                .classes("manager-source-name")
                            if one.get("note"):
                                ui.label(str(one["note"])).classes("manager-help")
                    words.on("click", lambda tick=tick: tick.set_value(not tick.value))
                    tick.on_value_change(lambda event, key=key: wrote(key, bool(event.value)))
                    shown[key] = row
                    boxes[key] = tick
        on_control(box)

        def wrote(key: str, on: bool) -> None:
            (ticks.add if on else ticks.discard)(key)
            if not bulk["on"]:
                commit()

        def commit() -> None:
            box.value = [str(one["key"]) for one in rows if str(one["key"]) in ticks]
            count.set_text(t("manager.wizard_lists.ticked", ticked=len(box.value),
                             total=len(rows)))
            on_change(box.value)

        def wanted(one: dict[str, Any]) -> bool:
            text = looking["text"].strip().casefold()
            if text and text not in str(one.get("name") or "").casefold():
                return False
            return not looking["group"] or looking["group"] in (one.get("groups") or ())

        def look(text: str | None = None, group: str | None = None) -> None:
            if text is not None:
                looking["text"] = text
            if group is not None:
                looking["group"] = group
            seen = 0
            for one in rows:
                here = wanted(one)
                shown[str(one["key"])].set_visibility(here)
                seen += here
            empty.set_visibility(not seen)

        def setall(on: bool) -> None:
            bulk["on"] = True
            try:
                for one in rows:
                    if wanted(one):
                        boxes[str(one["key"])].set_value(on)
            finally:
                bulk["on"] = False
            commit()

        find.on_value_change(lambda event: look(text=str(event.value or "")))
        count.set_text(t("manager.wizard_lists.ticked", ticked=len(box.value),
                         total=len(rows)) if rows else "")
        empty.set_visibility(not rows)

    return draw


def shots(paths: Sequence[str], url_for: Callable[[str], str]) -> None:
    """A few of the person's own images, small, in a row. The paths are the flow's, inside
    the folders it declared; a file that will not load leaves its frame empty."""
    with ui.element("div").classes("manager-wizard-shots"):
        for path in paths:
            with ui.element("span").classes("manager-wizard-shot"):
                image = ui.element("img")
                image.props["src"] = url_for(str(path))
                image.props["loading"] = "lazy"
                image.props["alt"] = ""

