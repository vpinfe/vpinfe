"""The guides section."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from nicegui import run, ui

from common.config.labels import humanize
from common.failures import why
from common.i18n import t
from common.media.media_specs import media_label_map
from manager.extensions import ext_groups
from manager.kit import arrange as arranging
from manager.kit import art, panel, verbs
from manager.kit import dialog as frame
from manager.kit.on_page import on_page
from manager.workbench.game_table import _rule_sheet


def _guides_label(context: dict[str, Any]) -> str:
    held = len(_guides(context)[0]) + bool(_rule_sheet(context))
    return t("manager.workbench.guides_counted", count=held) if held \
        else t("manager.workbench.guides")


def _guide_kind(kind: str) -> str:
    said = t(f"guide.kind.{kind}.label")
    return humanize(kind) if said == f"guide.kind.{kind}.label" else said


def _guides(context: dict[str, Any]) -> tuple[list[dict], list[dict]]:
    """(shown, hidden), each in the game's order."""
    held = [one for one in context["game"].get("guides") or [] if one.get("url")]
    return ([one for one in held if not one.get("hidden")],
            [one for one in held if one.get("hidden")])


async def _guides_block(context: dict[str, Any]) -> None:
    sheet = _rule_sheet(context)
    shown, hidden = _guides(context)
    with ui.column().classes("gap-0 manager-form w-full"):
        if not sheet and not shown:
            ui.label(t("manager.workbench.no_guides")).classes("manager-help px-3")
        if sheet:
            _guide_row(media_label_map().get("rule_sheet", "rule_sheet"),
                       art.media(context["game_id"], "rule_sheet",
                                 version=sheet.get("version")),
                       t("manager.workbench.in_folder"))
        with ui.column().classes("gap-0 w-full").props('data-arrange="hub_guide_moved"'):
            for one in shown:
                _guide_row(*guide_words(one), arrange=len(shown) > 1,
                           act=_guide_act(context, one))
        if hidden:
            with panel.disclosure(t("manager.workbench.hidden_guides", count=len(hidden))) \
                    .classes("manager-disclosure-rows pt-2"):
                for one in hidden:
                    _guide_row(*guide_words(one), act=_guide_act(context, one))
        with ui.element("div").classes("manager-slot-actions px-3"):
            panel.action(t("manager.workbench.add_guide"),
                         lambda: _add_guide(context), icon=verbs.ADD,
                         hint=panel.NO_HINT)()
        ext_groups.fill(context, "guides")
    # Read at the drop by the page's listener, `guide_moved`.
    context["state"]["guide_move"] = context
    if len(shown) > 1:
        ui.run_javascript(arranging.SCRIPT)


def _as_sent(guides: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"url": one["url"], "hidden": bool(one.get("hidden"))} for one in guides]


@on_page
async def _save_guides(context: dict[str, Any], guides: list[dict[str, Any]]) -> None:
    try:
        await run.io_bound(context["library"].set_game_guides, context["game_id"], guides)
    except Exception as exc:  # noqa: BLE001
        ui.notify(t("manager.workbench.save_failed"), caption=why(exc), type="negative")
        return
    await context["rebuild"]()


def _guide_act(context: dict[str, Any], guide: dict[str, Any]) -> Callable[[], None]:
    """Hide or unhide one VPS lists, or remove one of the person's own."""
    held = [one for one in context["game"].get("guides") or [] if one.get("url")]

    async def act() -> None:
        if guide.get("hidden") or guide.get("origin") == "vps":
            wanted = [{**one, "hidden": not one.get("hidden")} if one is guide else one
                      for one in held]
        else:
            wanted = [one for one in held if one is not guide]
        await _save_guides(context, _as_sent(wanted))

    def draw() -> None:
        if guide.get("hidden"):
            panel.action(t("manager.workbench.unhide"), act, icon=verbs.UNHIDE, inline=True,
                         hint=panel.NO_HINT)()
            return
        mine = guide.get("origin") != "vps"
        with ui.element("div").classes("manager-row-action"):
            ui.button(icon=verbs.REMOVE if mine else verbs.HIDE, on_click=act) \
                .props("flat dense round size=sm") \
                .tooltip(t("word.remove") if mine else t("manager.workbench.hide"))

    return draw


async def guide_moved(state: dict[str, Any], moved: Any) -> None:
    """Put one shown guide where it was dropped, in the Guides drawn last. The hidden ones
    keep their order after the shown."""
    context = state.get("guide_move")
    if not context:
        return
    shown, hidden = _guides(context)
    try:
        source, target = int(moved["from"]), int(moved["to"])
    except (KeyError, TypeError, ValueError):
        return
    if not (0 <= source < len(shown) and 0 <= target < len(shown)) or source == target:
        return
    shown.insert(target, shown.pop(source))
    await _save_guides(context, _as_sent(shown + hidden))


async def _add_guide(context: dict[str, Any]) -> None:
    from common.games.info_file import GUIDE_KINDS, GUIDE_TUTORIAL

    held: dict[str, Any] = {"kind": GUIDE_TUTORIAL}

    def draw_address() -> None:
        held["address"] = frame.field(placeholder="https://")

    def draw_title() -> None:
        held["title"] = frame.field()

    async def add() -> None:
        said = str(held["address"].value or "").strip()
        if not said.lower().startswith(("http://", "https://")):
            held["address"].props["error"] = True
            held["address"].props["error-message"] = t("manager.workbench.guide_needs_address")
            return
        box.close()
        kept = [one for one in context["game"].get("guides") or [] if one.get("url")]
        await _save_guides(context, [*_as_sent(kept),
                                     {"url": said, "kind": held["kind"],
                                      "title": str(held["title"].value or "").strip()}])

    with frame.opened(t("manager.workbench.guide_dialog",
                        name=str(context["game"].get("name") or "")),
                       ignore_outside_click=True) as box:
        panel.facts(ui, [
            (t("manager.workbench.guide_address"), draw_address),
            (t("manager.workbench.guide_title"), draw_title),
            (t("word.kind"), panel.select({one: _guide_kind(one) for one in GUIDE_KINDS},
                                          GUIDE_TUTORIAL,
                                          lambda event: held.update(kind=event.value)))])
        with frame.footer():
            frame.cancel(box.close)
            go = frame.answer(t("word.add"), add, icon=verbs.ADD)
    frame.focus(box, held["address"])
    frame.enter_presses(go)
    box.open()


def guide_words(guide: dict[str, Any]) -> tuple[str, str, str]:
    """(name, address, the line under the name)."""
    source = str(guide.get("source") or "")
    makers = ", ".join(name for name in guide.get("authors") or [] if name != source)
    return (str(guide.get("title") or "") or _guide_kind(str(guide.get("kind") or "")),
            str(guide.get("url") or ""),
            " \u00b7 ".join(part for part in (source, makers) if part))


def _guide_row(name: str, address: str, said: str, *, arrange: bool = False,
               act: Callable[[], None] | None = None) -> None:
    with ui.row().classes("items-center gap-2 w-full no-wrap manager-member-row"):
        if arrange:
            ui.icon("drag_indicator").classes("manager-drag-handle") \
                .props("tabindex=0 role=button") \
                .tooltip(t("manager.workbench.drag_hint"))
        with ui.column().classes("gap-0 grow min-w-0"):
            panel.link_out(name, to=address)()
            if said:
                ui.label(said).classes("manager-member-table")
        if act is not None:
            act()
