"""The wizard field for a list of rows each answered with a VPS entry.

Like `wizard_lists.checklist` it hands the wizard a control whose `value` is the answer, so
`wizard._sync` reads it the way it reads any other. It registers with the wizard control
when imported.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from nicegui import background_tasks, ui

from common.i18n import t
from manager.kit import game_tables, offload, panel, verbs, wizard
from manager.kit.api import ApiClient
from manager.kit.data import Library
from manager.kit.on_page import on_page
from manager.kit.wizard_lists import Answer, heading
from manager.library import vps_match


def _offered(entry: dict[str, Any]) -> str:
    name, made = str(entry.get("name") or ""), game_tables.made(entry)
    return t("manager.wizard_lists.entry", name=name, made=made) if name and made else name


def settle(field: dict[str, Any], answered: dict[str, str],
           on_change: Callable[[dict[str, str]], None], *, client: ApiClient,
           question: str = "",
           on_control: Callable[[Any], None]) -> Callable[[], None]:
    """`rows` are `{key, name, entry, vps_id, reason, settled}`, `entry` being the
    `{name, manufacturer, year}` the row would offer. A row says it, or Not found, and why
    in a few words; Change... opens the VPS picker. A settled row is behind Show all.

    The answer maps a row's key to the VPS id the person picked. A key absent was left as
    the source had it, and `""` is a row the person said is in no catalog."""
    rows = [one for one in field.get("rows") or [] if one.get("key")]
    unsettled = [one for one in rows if not one.get("settled")]

    def draw() -> None:
        library = Library(client)
        picks = {str(key): str(vps) for key, vps in answered.items()}
        named: dict[str, str] = {}
        lines: dict[str, ui.label] = {}
        holders: dict[str, ui.element] = {}
        every = {"on": False}
        with ui.column().classes("w-full gap-2"):
            if question:
                heading(question)
            box = Answer(dict(picks)).classes("manager-wizard-list")
            with ui.row().classes("items-center gap-2 w-full no-wrap"):
                summary = ui.label("").classes("manager-help grow")
                with ui.row().classes("items-center") as reveal:
                    panel.action(t("manager.wizard_lists.show_all", count=len(rows)),
                                 lambda: toggle(), icon=verbs.UNHIDE,
                                 hint=panel.NO_HINT)()
                    panel.action(t("manager.wizard_lists.hide_matched"),
                                 lambda: toggle(), icon=verbs.HIDE,
                                 hint=panel.NO_HINT)()
                shows, hides = [one for one in reveal.default_slot.children
                                if isinstance(one, ui.button)]
            with box:
                for one in rows:
                    key = str(one["key"])
                    row = ui.element("div").classes("manager-wizard-listrow")
                    with row:
                        with ui.column().classes("gap-0 min-w-0 grow"):
                            ui.label(str(one.get("name") or key)) \
                                .classes("manager-source-name")
                            lines[key] = ui.label("").classes("manager-help")
                        with ui.element("div").classes("shrink-0"):
                            panel.action(t("manager.wizard_lists.change"),
                                         lambda one=one: change(one), icon=verbs.MATCH,
                                         hint=panel.NO_HINT)()
                    holders[key] = row
        on_control(box)

        def saying(one: dict[str, Any]) -> str:
            key = str(one["key"])
            if key in picks:
                if not picks[key]:
                    return t("manager.wizard_lists.not_matched")
                return named.get(key) or t("manager.wizard_lists.picked")
            said = _offered(one.get("entry") or {}) or t("word.not_found")
            if not one.get("reason"):
                return said
            return t("manager.wizard_lists.candidate_why", candidate=said,
                     reason=str(one["reason"]))

        def redraw(one: dict[str, Any]) -> None:
            lines[str(one["key"])].set_text(saying(one))

        def visible() -> None:
            for one in rows:
                holders[str(one["key"])].set_visibility(every["on"] or not one.get("settled"))
            settled = len(rows) - len(unsettled)
            summary.set_text(t("manager.wizard_lists.nothing_left")
                             if rows and not unsettled else "")
            shows.set_visibility(not every["on"])
            hides.set_visibility(every["on"])
            reveal.set_visibility(bool(settled))

        def toggle() -> None:
            every["on"] = not every["on"]
            visible()

        @on_page
        async def change(one: dict[str, Any]) -> None:
            key = str(one["key"])
            game = {"name": str(one.get("name") or key),
                    "vps_id": picks[key] if key in picks else str(one.get("vps_id") or "")}
            found = await vps_match.ask(library, game)
            if found in (vps_match.CANCELLED, vps_match.STOPPED, None) \
                    or lines[key].is_deleted:
                return
            if found == "":
                picks.pop(key, None)
            elif found == vps_match.CLEARED:
                picks[key] = ""
            else:
                picks[key] = str(found)
                named.pop(key, None)
            box.value = dict(picks)
            redraw(one)
            on_change(box.value)
            if found not in ("", vps_match.CLEARED):
                await name_it(one)

        async def name_it(one: dict[str, Any]) -> None:
            key = str(one["key"])
            vps = picks.get(key)
            if not vps or key in named:
                return
            try:
                entry = await offload.io(library.vps_entry, vps)
            except Exception:  # noqa: BLE001
                return
            if picks.get(key) == vps and not lines[key].is_deleted:
                named[key] = str(entry.get("name") or "")
                redraw(one)

        for one in rows:
            redraw(one)
        visible()
        for one in rows:
            if picks.get(str(one["key"])):
                background_tasks.create(name_it(one), name="manager-wizard-settle-named")

    return draw


wizard.FIELD_DRAWERS["settle"] = settle
