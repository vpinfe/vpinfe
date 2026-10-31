"""Players: who plays here, who the next game counts for, and the accounts each one holds.

A grid with a workbench, the shape Launchers uses beside it. The account sections of a
player's rail are built as the panel opens, from what is running then.
"""

from __future__ import annotations

import base64
import copy
import html
import inspect
import json
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from functools import partial
from typing import Any

from nicegui import run, ui

from common import icons
from common.failures import why
from common.i18n import t
from manager.extensions import ext_page
from manager.kit import (
    art,
    busy,
    confirm,
    deeplink,
    game_tables,
    grid,
    link_tab,
    offload,
    panel,
    play_facts,
    thumbnails,
    undo,
    verbs,
    views,
)
from manager.kit import dialog as frame
from manager.kit.data import Library
from manager.kit.on_page import on_page
from manager.library import mediasource, mediaview

logger = logging.getLogger("vpinfe.manager.frontend.players")

SCOPE = "manager.players.columns"

OWNER, GUEST = "owner", "guest"

KINDS = {OWNER: ("manager.players.owner", "manager.players.owner.help"),
         GUEST: ("manager.players.guest", "manager.players.guest.help")}

# The account acts core knows by key. Every extension offering an account uses this
# vocabulary; `send_now` is the only one drawn generically, since claiming and
# disconnecting each have their own dialog or confirm.
SEND_NOW, CLAIM, CONSENT, DISCONNECT = "send_now", "claim", "consent", "disconnect"

CARD_FILES = (".svg", "image/svg+xml", ".json", ".txt")

_ESCAPE = ("const esc = s => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;')"
           ".replace(/\"/g, '&quot;');")

_CHIPS = {kind: {"label": t(label), "tip": t(tip)} for kind, (label, tip) in KINDS.items()}

PERSON = "person"

_NAME = (
    "params => {" + _ESCAPE +
    f" const kinds = {json.dumps(_CHIPS)};"
    f" const up = esc({json.dumps(t('manager.players.up'))});"
    f" const upTip = esc({json.dumps(t('manager.players.up.help'))});"
    " const d = params.data || {};"
    " const art = d.picture || '';"
    " const initials = d.initials || '';"
    " let lines = esc(params.value == null ? '' : params.value);"
    " const kind = kinds[d.kind];"
    " if (kind) lines += ' <span class=\"manager-member-chip manager-cell-chip"
    " manager-chip-quiet\" title=\"' + esc(kind.tip) + '\">' + esc(kind.label) + '</span>';"
    " if (d.up) lines += ' <span class=\"manager-member-chip manager-cell-chip"
    " manager-tier manager-tier--on\""
    " title=\"' + upTip + '\">' + up + '</span>';"
    " if (d.session) lines += ' <span class=\"manager-cell-quiet\">' + esc(d.session)"
    " + '</span>';"
    f" return {thumbnails.circle_js(PERSON)}; }}"
)

_INITIALS = (
    "params => {" + _ESCAPE +
    f" const same = esc({json.dumps(t('manager.players.same_initials'))});"
    " const d = params.data || {};"
    " const said = esc(params.value == null ? '' : params.value);"
    " return d.shared ? said + ' <span class=\"manager-member-chip manager-cell-chip manager-tier"
    " manager-tier--warn\" title=\"' + esc(d.shared) + '\">' + same + '</span>' : said; }"
)

# A cell draws more of its row than its own value: without `equals`, a row updated with
# only its Up changed keeps the chips it had.
_REDRAWN: dict[str, Any] = {":equals": "() => false"}

COLUMNS: list[dict[str, Any]] = [
    grid.identifier("name", t("word.name"), 260, pinned="left",
                    help=t("manager.players.name.help"),
                    **{":cellRenderer": _NAME, **_REDRAWN}),
    grid.column("initials", t("manager.players.initials"), 160,
                help=t("manager.players.initials.help"),
                **{":cellRenderer": _INITIALS, **_REDRAWN}),
]

PLAYER_VIEWS: dict[str, list[str] | views.Preset] = {
    "manager.view.everything": ["name", "initials"],
}


# -- the rows ------------------------------------------------------------------

def shown_name(player: dict[str, Any]) -> str:
    """The name, else the initials: a guest who joined with initials has nothing else."""
    return (str(player.get("name") or "").strip() or str(player.get("initials") or "").strip()
            or t("manager.players.no_name"))


def kind_of(player: dict[str, Any]) -> str:
    return OWNER if player.get("owner") else GUEST if player.get("guest") else ""


def kind_word(player: dict[str, Any]) -> str:
    """What the panel's header says the player is."""
    kind = kind_of(player)
    return t(KINDS[kind][0]) if kind else t("manager.players.player")


def picture_address(player: dict[str, Any], size: int = art.CELL) -> str:
    """Where their picture is served, or "" for a player with none."""
    version = player.get("picture_version")
    if not version:
        return ""
    return art.player(str(player.get("id") or ""), version=str(version), size=size)


def same_initials(player: dict[str, Any], roster: list[dict[str, Any]]) -> str:
    """The alert for a player sharing initials with another, or "" for one who is not."""
    named = [shown_name(one) for one in roster
             if one.get("id") in (player.get("shares_initials_with") or [])]
    if not named:
        return ""
    return t("manager.players.initials_clash",
             player=t("manager.collection_rules.list_join").join(named))


def rows(roster: list[dict[str, Any]],
         played: dict[str, int] | None = None) -> list[dict[str, Any]]:
    """One per player, in the roster's order. `played` counts each guest's games this
    session."""
    alone = len(roster) <= 1
    made = []
    for player in roster:
        games = int((played or {}).get(str(player.get("id") or "")) or 0)
        made.append({
            "id": str(player.get("id") or ""),
            "name": shown_name(player),
            "initials": str(player.get("initials") or ""),
            "picture": picture_address(player),
            "kind": kind_of(player),
            "up": bool(player.get("up")) and not alone,
            "shared": same_initials(player, roster),
            "session": (t("manager.players.session_total", count=games)
                        if player.get("guest") and games else ""),
        })
    return made


def games_played(record: list[dict[str, Any]]) -> int:
    return sum(int(one.get("play_count") or 0) for one in record)


async def _read(library: Library) -> tuple[list[dict[str, Any]], dict[str, int],
                                           dict[str, list[dict[str, Any]]]]:
    """The roster, how many games each guest has played this session, and each player's
    accounts."""
    roster = await offload.io(library.players)
    played: dict[str, int] = {}
    accounts: dict[str, list[dict[str, Any]]] = {}
    for player in roster:
        try:
            accounts[player["id"]] = await offload.io(library.player_accounts, player["id"])
        except Exception:  # noqa: BLE001 - a menu entry is not worth failing the page for
            logger.debug("Could not read a player's accounts", exc_info=True)
        if player.get("guest"):
            try:
                played[player["id"]] = games_played(
                    await offload.io(library.player_record, player["id"]))
            except Exception:  # noqa: BLE001 - a count is not worth failing the page for
                logger.debug("Could not read a guest's record", exc_info=True)
    return roster, played, accounts


# -- the grid ------------------------------------------------------------------

def build(library: Library, state: dict[str, Any],
          on_select: Callable[[dict | None], Any], redraw: Callable[[], None]) -> None:
    """The grid. Read on every draw: a phone or the frontend can change who is up."""
    body = ui.column().classes("w-full grow min-h-0 gap-0")
    busy.fill(body, lambda: _fill(library, state, on_select, redraw, body))


async def _fill(library: Library, state: dict[str, Any],
                on_select: Callable[[dict | None], Any], redraw: Callable[[], None],
                body: Any) -> None:
    state["show_player"] = on_select
    try:
        roster, played, held = await _read(library)
    except Exception as exc:  # noqa: BLE001 - this page says why, never 500s
        with body:
            panel.facts(ui, [panel.intro(t("manager.players.players_failed"),
                                         hint=why(exc))])
        return

    # Imported here: `workbench.rail` imports this module, and `games` imports
    # `workbench.all_settings`.
    from manager.library.games import view_control

    built = rows(roster, played)
    guests = [one for one in roster if one.get("guest")]
    columns = grid.with_art(COLUMNS, thumbnails.person_look())
    fields = [definition["field"] for definition in columns]

    with body:
        with ui.row().classes("w-full items-center gap-2 px-3 py-2 mb-2 shrink-0 "
                              "manager-panel manager-grid-bar"):
            bar = panel.grid_bar()
            wire_views, _picker, showing, describe = view_control(
                library, SCOPE, PLAYER_VIEWS, fields, columns, bar=bar,
                art_in_lists=True)
            describe()
            with bar.top, panel.bar_end():
                search = panel.search(t("manager.players.search_players"))
            with bar.bottom, panel.bar_end():
                count = ui.label(t("manager.players.players_count", count=len(built))) \
                    .classes("text-xs manager-label")
                show_selection = panel.selection_menu(
                    t("manager.players.players_menu"), lambda: grid.selection(table),
                    lambda chosen: acts_on(chosen),
                    lambda: table.run_grid_method("deselectAll"))
                panel.add_action(
                    [(t("manager.players.add_player"),
                      lambda: add_player(library, state, redraw)),
                     (t("manager.players.add_guest"),
                      lambda: add_guest(library, state, redraw))],
                    empty=False)
                if guests:
                    panel.icon_action(
                        t("manager.players.guests_end"),
                        lambda: sign_out_guests(library, state, redraw),
                        icon=verbs.REMOVE, hint=t("manager.players.guests_end"))()

        by_id = {row["id"]: row for row in built}
        by_player = {str(one["id"]): one for one in roster}
        grid.on_row_focus(SCOPE,
                          lambda event: on_select(by_id.get(grid.focused_row(event))))

        def changed_for(player_id: str) -> Callable[[], Awaitable[Any]]:
            async def changed_here() -> None:
                await _refresh(state)
                if state.get("player") != player_id:
                    return
                answer = on_select(by_id.get(player_id))
                if inspect.isawaitable(answer):
                    await answer
            return changed_here

        def acts_on(rows_on: list[dict[str, Any]]) -> list[panel.Verb]:
            chosen = [by_player[str(one["id"])] for one in rows_on
                      if str(one["id"]) in by_player]
            if not chosen:
                return []
            after = (changed_for(str(chosen[0]["id"])) if len(chosen) == 1
                     else partial(_refresh, state))
            return acts(library, state, chosen, redraw, accounts=held, after=after)

        picked: list[dict[str, Any]] = []
        shown = {"rows": len(built)}

        def said() -> str:
            if picked:
                return grid.selection_said(table, len(picked), t(
                    "manager.players.selected", picked=len(picked), shown=shown["rows"]))
            return t("manager.players.players_count", count=len(built))

        def on_selected(rows_selected: list[dict[str, Any]]) -> None:
            picked[:] = rows_selected
            show_selection(bool(rows_selected))
            count.text = said()

        async def counted() -> None:
            seen = await table.run_grid_method("getDisplayedRowCount")
            shown["rows"] = int(seen if isinstance(seen, int) else len(built))
            count.text = said()

        def fill(row: dict | None) -> None:
            if row is None or str(row.get("id") or "") not in by_player:
                menu.clear()
                return
            on = grid.menu_rows(table, row)
            panel.verb_menu(
                menu, shown_name(by_player[str(row["id"])]) if len(on) == 1
                else t("manager.players.players_count", count=len(on)),
                panel.for_selection(acts_on(on), len(on)))

        async def on_header_context(col_id: str | None) -> None:
            await grid.header_menu(menu, table, columns, col_id)

        with ui.element("div").classes("w-full grow min-h-0 flex flex-col"):
            table = grid.build(columns, built, SCOPE, on_selected, fill, on_header_context,
                               view_of=showing)
            menu = ui.context_menu()
        table.on("modelUpdated", counted)
        search.on_value_change(
            lambda: table.run_grid_method("setGridOption", "quickFilterText",
                                          search.value or ""))
        wire_views(table)

        async def refresh_players() -> None:
            fresh, played, again = await _read(library)
            held.clear()
            held.update(again)
            by_player.clear()
            by_player.update({str(one["id"]): one for one in fresh})
            grid.replace_rows(table, built, by_id, rows(fresh, played), lambda _row: True)
            count.text = said()

        state["refresh_players"] = refresh_players


async def _refresh(state: dict[str, Any]) -> None:
    again = state.get("refresh_players")
    if callable(again):
        await again()


async def _open(state: dict[str, Any], redraw: Callable[[], None], player_id: str) -> None:
    """The grid drawn again with them, and them in the workbench."""
    state["player"] = player_id
    grid.land_on(SCOPE, {"id": player_id})
    redraw()
    show = state.get("show_player")
    if callable(show):
        await show({"id": player_id})
    if callable(opened := state.get("open_pane")):
        opened()


# -- adding --------------------------------------------------------------------

def _initials_field(value: str = "") -> Any:
    control = frame.field(value, placeholder=t("manager.players.initials_example"))
    control.props("maxlength=3 bottom-slots")
    return control


def _card_picker(label: str, on_upload: Callable[[Any], Any], *,
                 hint: str = "") -> None:
    """A hidden uploader for one card file, and the action that opens it."""
    uploader = ui.upload(on_upload=on_upload, auto_upload=True).classes("hidden")
    uploader.props(f'accept="{",".join(CARD_FILES)}"')
    uploader.on("finish", js_handler=f"() => getElement({uploader.id}).$refs.qRef.reset()")
    panel.action(label, None, icon=verbs.FROM_FILE,
                 hint=hint or t("manager.mediasource.choose_file.help"),
                 js=f"() => getElement({uploader.id}).$refs.qRef.pickFiles()")()


def _refused(control: Any, said: str) -> None:
    control.props["error"] = bool(said)
    control.props["error-message"] = said
    control.update()


@on_page
async def add_player(library: Library, state: dict[str, Any],
                     redraw: Callable[[], None]) -> None:
    """A kept player: a name, and the initials a score finds them by."""
    fields: dict[str, Any] = {}

    async def keep() -> None:
        try:
            made = await offload.io(library.add_player,
                                    str(fields["name"].value or "").strip(),
                                    str(fields["initials"].value or "").strip())
        except Exception as exc:  # noqa: BLE001 - the refusal is about the initials
            _refused(fields["initials"], why(exc))
            return
        box.submit(str(made.get("id") or ""))

    def draw(key: str) -> Callable[[], None]:
        def drawn() -> None:
            fields[key] = _initials_field() if key == "initials" else frame.field()
        return drawn

    with frame.opened(t("manager.players.add_player"), ignore_outside_click=True) as box:
        panel.facts(ui, [(t("word.name"), draw("name")),
                         (t("manager.players.initials"), draw("initials"))])
        with frame.footer():
            frame.cancel(lambda: box.submit(None))
            go = frame.answer(t("word.add"), keep, icon=verbs.ADD)
    fields["initials"].on_value_change(lambda: _refused(fields["initials"], ""))
    frame.focus(box, fields["name"])
    frame.enter_presses(go)
    made = await box
    if made:
        await _open(state, redraw, str(made))


@on_page
async def add_guest(library: Library, state: dict[str, Any],
                    redraw: Callable[[], None]) -> None:
    """A guest, who is up as they join."""
    held: dict[str, Any] = {}

    async def with_initials() -> None:
        try:
            made = await offload.io(library.add_guest,
                                    str(held["initials"].value or "").strip())
        except Exception as exc:  # noqa: BLE001 - the refusal is about the initials
            _refused(held["initials"], why(exc))
            return
        box.submit(str(made.get("id") or ""))

    @on_page
    async def with_card(event: Any) -> None:
        try:
            text = (await event.file.read()).decode("utf-8", "replace")
            made = await offload.io(library.add_guest_from_card, text)
        except Exception as exc:  # noqa: BLE001 - said, and the dialog stays for a retry
            ui.notify(t("manager.players.guest_failed"), caption=why(exc),
                      type="negative")
            return
        box.submit(str(made.get("id") or ""))

    def card_row() -> None:
        with ui.element("div").classes("manager-fact-edit"):
            _card_picker(t("manager.players.choose_card"), with_card)

    def initials_row() -> None:
        held["initials"] = _initials_field()

    with frame.opened(t("manager.players.add_guest"), ignore_outside_click=True) as box:
        ui.label(t("manager.players.guest_forgotten")).classes("manager-help px-3")
        panel.facts(ui, [(t("manager.players.use_card"), card_row),
                         (t("manager.players.just_initials"), initials_row)])
        with frame.footer():
            frame.cancel(lambda: box.submit(None))
            go = frame.answer(t("word.add"), with_initials, icon=verbs.ADD)
    held["initials"].on_value_change(lambda: _refused(held["initials"], ""))
    frame.focus(box, held["initials"])
    frame.enter_presses(go)
    made = await box
    if made:
        await _open(state, redraw, str(made))


# -- removing ------------------------------------------------------------------

def account_verbs(library: Library, player: dict[str, Any],
                  accounts: Sequence[dict[str, Any]],
                  after: Callable[[], Awaitable[Any]], *, one: bool = False
                  ) -> list[panel.Verb]:
    """Disconnect where an account is claimed, and Remove user ID where an id is held
    but not claimed, one each per service. An account with neither is not offered."""
    context = {"library": library, "player": player, "rebuild": after}
    offered: list[panel.Verb] = []
    for account in accounts:
        if account.get("error") or not account.get("user_id"):
            continue
        service = str(account.get("label") or account["extension"])
        if account.get("claimed"):
            offered.append(panel.Verb(
                t("manager.players.menu_disconnect", service=service),
                partial(_disconnect, context, account, service), danger=True, one=one))
        else:
            offered.append(panel.Verb(
                t("manager.players.menu_remove_id", service=service),
                partial(_remove_user_id, context, account, service), danger=True, one=one))
    return offered


def acts(library: Library, state: dict[str, Any], players: Sequence[dict[str, Any]],
         redraw: Callable[[], None], *,
         accounts: Mapping[str, Sequence[dict[str, Any]]] | None = None,
         after: Callable[[], Awaitable[Any]] | None = None) -> list[panel.Verb]:
    """What can be done to the players `players`. A service's account acts belong to one
    player and are for one at a time; Sign Out and Remove act on the guests and the kept
    players among them. The owner stays, and says so."""
    held = accounts or {}
    several = len(players) > 1
    offered: list[panel.Verb] = []
    if after:
        for player in players:
            for verb in account_verbs(library, player, held.get(str(player["id"])) or [],
                                      after, one=several):
                if all(verb.label != seen.label for seen in offered):
                    offered.append(verb)
    guests = [one for one in players if one.get("guest")]
    kept = [one for one in players if not one.get("guest")]
    removable = [one for one in kept if not one.get("owner")]
    if guests:
        offered.append(panel.Verb(t("manager.players.sign_out"),
                                  lambda: sign_out(library, state, redraw, guests),
                                  danger=True))
    if kept:
        offered.append(panel.Verb(
            t("word.remove"),
            (lambda: remove(library, state, redraw, removable)) if removable else None,
            danger=True, hint=t("error.players.owner_not_removable")))
    return offered


def removal_detail(carded: dict[str, Any] | None) -> list[str]:
    """What a kept player's removal takes with it, and the account only their card can
    bring back where they hold one."""
    said = [t("manager.players.remove_detail")]
    if carded is not None:
        said.append(t("manager.players.remove_card_detail",
                      service=str(carded.get("label") or carded.get("extension") or "")))
    return said


async def _held_cards(library: Library, players: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The accounts among `players` that only a card can bring back."""
    cards: list[dict[str, Any]] = []
    for player in players:
        try:
            held = await offload.io(library.player_accounts, player["id"])
        except Exception:  # noqa: BLE001 - the confirm still asks, without the offer
            logger.debug("Could not read a player's accounts before removing", exc_info=True)
            continue
        cards += [one for one in held if one.get("card")]
    return cards


@on_page
async def remove(library: Library, state: dict[str, Any], redraw: Callable[[], None],
                 players: list[dict[str, Any]]) -> None:
    """Asked first, with Save Card beside the answer where the one player holds a card, and
    a line saying how many do where several go."""
    cards = await _held_cards(library, players)
    if len(players) > 1:
        detail = [t("manager.players.remove_detail")]
        if cards:
            detail.append(t("manager.players.remove_cards_detail", count=len(cards)))
        if not await confirm.ask(t("manager.players.remove_several", count=len(players)),
                                 detail=detail, lines=[shown_name(one) for one in players],
                                 confirm=t("word.remove"), icon=verbs.REMOVE):
            return
        await _gone(library, state, redraw, players)
        return
    player = players[0]
    carded = cards[0] if cards else None
    also = None
    if carded is not None:
        account: dict[str, Any] = carded
        also = (t("manager.players.save_card"),
                lambda: save_card(library, player, account), verbs.FETCH)
    if not await confirm.ask(t("manager.players.remove", name=shown_name(player)),
                             detail=removal_detail(carded), confirm=t("word.remove"),
                             icon=verbs.REMOVE, also=also):
        return
    await _gone(library, state, redraw, [player])


@on_page
async def sign_out(library: Library, state: dict[str, Any], redraw: Callable[[], None],
                   players: list[dict[str, Any]]) -> None:
    several = len(players) > 1
    if not await confirm.ask(
            t("manager.players.sign_out_several", count=len(players)) if several
            else t("manager.players.sign_out_one", name=shown_name(players[0])),
            detail=t("manager.players.sign_out_detail"),
            lines=[shown_name(one) for one in players] if several else (),
            confirm=t("manager.players.sign_out"), icon=verbs.REMOVE):
        return
    await _gone(library, state, redraw, players)


@on_page
async def sign_out_guests(library: Library, state: dict[str, Any],
                          redraw: Callable[[], None]) -> None:
    """Every guest at once: the ones here now, including any who joined from a phone
    after the page was drawn."""
    try:
        guests = [one for one in await offload.io(library.players) if one.get("guest")]
    except Exception as exc:  # noqa: BLE001
        ui.notify(t("manager.players.players_failed"), caption=why(exc),
                  type="negative")
        return
    if not guests:
        redraw()
        return
    if not await confirm.ask(t("manager.players.sign_out_everyone"),
                             detail=t("manager.players.sign_out_detail"),
                             lines=[shown_name(one) for one in guests],
                             confirm=t("manager.players.sign_out"), icon=verbs.REMOVE):
        return
    await _gone(library, state, redraw, guests)


@on_page
async def _gone(library: Library, state: dict[str, Any], redraw: Callable[[], None],
                players: list[dict[str, Any]]) -> None:
    for player in players:
        try:
            await run.io_bound(library.remove_player, player["id"])
        except Exception as exc:  # noqa: BLE001
            ui.notify(t("said.remove_failed"), caption=why(exc), type="negative")
            break
    if state.get("player") in {one["id"] for one in players}:
        state["player"] = ""
    redraw()


# -- the panel -----------------------------------------------------------------

def _of(context: dict[str, Any]) -> dict[str, Any]:
    return context["player"]


async def changed(context: dict[str, Any]) -> None:
    """The grid's rows, after a write."""
    await _refresh(context["state"])


def _alert(line: str) -> None:
    with ui.element("div").classes("manager-attention"):
        ui.icon("error_outline").classes("manager-attention-icon")
        ui.label(line).classes("manager-attention-line")


async def details(context: dict[str, Any]) -> None:
    """Who they are, and whether the next game counts for them."""
    library, player = context["library"], _of(context)
    roster = context["roster"]
    player_id = player["id"]

    async def rename(text: str) -> str:
        wanted = text.strip()
        if wanted == str(player.get("name") or ""):
            return ""
        try:
            await run.io_bound(library.change_player, player_id, name=wanted)
        except Exception as exc:  # noqa: BLE001 - said on the field
            return why(exc)
        player["name"] = wanted
        context["retitle"](shown_name(player))
        await changed(context)
        return ""

    async def initials(text: str) -> str:
        wanted = text.strip()
        if wanted.upper() == str(player.get("initials") or "").upper():
            return ""
        try:
            now = await offload.io(library.change_player, player_id, initials=wanted)
        except Exception as exc:  # noqa: BLE001 - said on the field
            return why(exc)
        player.update(now)
        drawn = context.get("picture_initials")
        if drawn is not None and not drawn.is_deleted:
            drawn.set_text(str(player.get("initials") or "").strip())
        alert.refill()
        await changed(context)
        return ""

    def draw_up() -> None:
        # Nobody up means the owner is: the owner alone up has nowhere to go.
        stays = bool(player.get("owner") and player.get("up")
                     and sum(1 for one in roster if one.get("up")) == 1)
        panel.switch(
            bool(player.get("up")), lambda event: flip(bool(event.value)), disabled=stays,
            hint=t("manager.players.owner_up_alone") if stays else "")()

    up = panel.region(draw_up)

    @on_page
    async def flip(on: bool) -> None:
        try:
            now = await offload.io(library.set_player_up, player_id, on)
        except Exception as exc:  # noqa: BLE001
            ui.notify(t("said.save_failed"), caption=why(exc), type="negative")
        else:
            roster[:] = now
            player.update(next((one for one in now if one.get("id") == player_id), {}))
            await changed(context)
        up.refill()

    def draw_alert() -> None:
        if line := same_initials(player, roster):
            _alert(line)

    # The finding comes or goes with the initials; an empty region hides its line.
    alert = panel.region(draw_alert)
    entries: list[tuple[Any, Any]] = [
        (panel.FULL, alert),
        (t("word.name"), panel.field(str(player.get("name") or ""), rename, refuses=True)),
        (t("manager.players.initials"),
         panel.field(str(player.get("initials") or ""), initials, refuses=True,
                     placeholder=t("manager.players.initials_example"))),
    ]
    if len(roster) > 1:
        entries.append((t("manager.players.up"), up))
    entries.append((t("manager.players.picture"), partial(_picture_slot, context)))
    with ui.column().classes("gap-0 manager-form"):
        panel.facts(ui, entries)


# -- the picture ---------------------------------------------------------------

def _picture_slot(context: dict[str, Any]) -> None:
    """Their picture at panel size, with what can be done to it. A guest has none to
    set, so theirs is the circle alone."""
    player = _of(context)
    present = bool(player.get("picture_version"))
    initials = str(player.get("initials") or "").strip()
    label = t("manager.players.picture")

    with ui.column().classes("w-full gap-1 min-w-0"):
        with ui.element("div").classes("manager-slot-art manager-slot-art--person") \
                .tooltip(t("manager.players.picture.help")):
            with ui.column().classes("manager-slot-blank items-center gap-1"):
                if initials:
                    context["picture_initials"] = ui.label(initials) \
                        .classes("manager-person-initials")
                else:
                    ui.icon(PERSON).classes("manager-slot-blank-icon")
            if present:
                address = html.escape(picture_address(player, art.PANEL))
                ui.html(f'<img src="{address}" alt="" draggable="false" '
                        'onerror="this.remove()">', sanitize=False, tag="span") \
                    .classes("manager-person-picture")
        if player.get("guest"):
            return
        with ui.row().classes("items-center gap-2 w-full manager-slot-actions"):
            panel.action(
                t("word.replace") if present else t("word.add"),
                lambda: mediasource.open_player_picture_sources(
                    context["library"], label, partial(set_picture, context),
                    services=[one for one in context.get("accounts") or []
                              if one.get("picture") and not one.get("error")],
                    use_service=partial(use_account_picture, context)),
                icon=verbs.REPLACE if present else verbs.ADD,
                hint=t("manager.players.picture_replace.help") if present
                else t("manager.players.picture_add.help"))()
            if present:
                panel.action(t("word.remove"), partial(remove_picture, context),
                             icon=verbs.REMOVE, danger=True,
                             hint=t("manager.players.picture_remove.help"))()


async def _picture_written(context: dict[str, Any], player_id: str) -> None:
    """The grid read again, and the panel redrawn if it is still on this player: an Undo
    can be pressed after the panel has moved on."""
    state = context["state"]
    if state.get("player") == player_id:
        await context["rebuild"]()
    else:
        await _refresh(state)


def _picture_undo(context: dict[str, Any], said: str, before: bytes | None) -> None:
    library, player_id = context["library"], _of(context)["id"]

    async def put_back() -> None:
        if before is None:
            await offload.io(library.clear_player_picture, player_id)
        else:
            await offload.io(library.set_player_picture, player_id, "picture.webp", before)
        await _picture_written(context, player_id)

    undo.offer(said, put_back)


@on_page
async def set_picture(context: dict[str, Any], name: str, data: bytes) -> Callable:
    """Saved at once, with Undo holding what it replaced. Raises with the API's reason
    where the file is not a picture, so the dialog that offered it can say so. Returns
    the redraw, for the dialog to run once it has closed."""
    library, player_id = context["library"], _of(context)["id"]
    before = await run.io_bound(library.player_picture_bytes, player_id)
    await offload.io(library.set_player_picture, player_id, name, data)
    _picture_undo(context, t("manager.players.picture_replaced" if before is not None
                             else "manager.players.picture_added"), before)
    return partial(_picture_written, context, player_id)


async def dropped_picture(library: Any, state: dict[str, Any], player_id: str,
                          name: str, data: bytes) -> Callable[[], Awaitable[None]]:
    """A picture dropped on a player, set the way the picker sets one: saved at once, with
    Undo. Raises with the API's reason where it cannot be. What it returns redraws the
    page, and runs after the save is known to have held."""
    async def rebuild() -> None:
        await state["show_player"]({"id": player_id})

    context = {"library": library, "player": {"id": player_id}, "state": state,
               "rebuild": rebuild}
    return await set_picture(context, name, data)


@on_page
async def use_account_picture(context: dict[str, Any], extension: str) -> Callable:
    """The picture a service has for this player, copied once and saved as an upload
    would be, with Undo holding what it replaced."""
    library, player_id = context["library"], _of(context)["id"]
    before = await run.io_bound(library.player_picture_bytes, player_id)
    await offload.io(library.use_account_picture, player_id, extension)
    _picture_undo(context, t("manager.players.picture_replaced" if before is not None
                             else "manager.players.picture_added"), before)
    return partial(_picture_written, context, player_id)


@on_page
async def remove_picture(context: dict[str, Any]) -> None:
    library, player_id = context["library"], _of(context)["id"]
    try:
        before = await run.io_bound(library.player_picture_bytes, player_id)
        await offload.io(library.clear_player_picture, player_id)
    except Exception as exc:  # noqa: BLE001 - the reason belongs on screen
        ui.notify(t("said.save_failed"), caption=why(exc), type="negative")
        return
    _picture_undo(context, t("manager.players.picture_removed"), before)
    await _picture_written(context, player_id)


# -- accounts ------------------------------------------------------------------

def account_key(extension: str) -> str:
    return f"player_account_{extension}"


def account_section(account: dict[str, Any]) -> tuple[str, Callable[[dict], str],
                                                      Callable[[dict], Any]]:
    """The rail row for one account: its key, its name, and how it draws."""
    label = str(account.get("label") or account["extension"])

    async def build(context: dict[str, Any]) -> None:
        await _account(context, account)

    return account_key(str(account["extension"])), (lambda _context: label), build


def share_help(account: dict[str, Any]) -> str:
    return (str(account.get("share_help") or "").strip()
            or t("manager.players.share.help",
                 service=str(account.get("label") or account.get("extension") or "")))


async def _account(context: dict[str, Any], account: dict[str, Any]) -> None:
    """The three states: no account, chosen but not shared, and claimed.
    `docs/extensions/reference/accounts.md` is the contract this draws -
    every extension offering one gets the same panel, in its own words."""
    library, player = context["library"], _of(context)
    if account.get("error"):
        panel.facts(ui, panel.headed(
            [panel.intro(t("manager.players.account_failed"), hint=str(account["error"]))],
            t("manager.players.account_heading")))
        return
    service = str(account.get("label") or account["extension"])

    def draw() -> None:
        user_id = str(account.get("user_id") or "")
        fields = [one for one in account.get("fields") or [] if one.get("key")]
        entries: list[tuple[Any, Any]] = []
        if fields and not user_id:
            entries += _field_rows(context, account, fields, form)
            entries.append((t("manager.players.share"), panel.switch(
                bool(account.get("share")), lambda e: share(bool(e.value)))))
            entries.append(panel.note(share_help(account)))
        elif not user_id:
            entries.append(panel.intro(t("manager.players.no_account", service=service)))
            entries.append((panel.FULL, lambda: _choose_or_card(context, account)))
        elif not account.get("claimed"):
            entries += [(t("manager.players.user_id"), user_id),
                        panel.note(t("manager.players.not_shared_note", service=service)),
                        (t("manager.players.share"), panel.switch(False, lambda e: share(
                            bool(e.value))))]
            entries.append((panel.FULL, lambda: _unclaimed_actions(context, account)))
        else:
            entries.append((panel.HEADING, t("manager.players.account_heading")))
            entries += [(t("manager.players.user_id"),
                         _user_id_value(user_id, str(account.get("page") or ""), service)),
                        (t("manager.players.share"), panel.switch(
                            bool(account.get("share")), lambda e: share(bool(e.value)))),
                        panel.note(share_help(account))]
            waiting = int(account.get("waiting_count") or 0)
            if waiting:
                entries.append((t("manager.players.waiting"),
                                t("manager.players.waiting_count", count=waiting)))
                entries.append((panel.FULL, lambda: _send_now(context, account)))
            entries.append((panel.HEADING, t("manager.players.card_heading")))
            entries.append((panel.FULL, lambda: _card_section(library, player, account,
                                                              service)))
            entries.append((panel.FULL, lambda: _card_actions(context, account)))
        panel.facts(ui, panel.headed(entries, t("manager.players.account_heading")))

    form = panel.region(draw)

    @on_page
    async def share(on: bool) -> None:
        user_id = str(account.get("user_id") or "")
        fields = [one for one in account.get("fields") or [] if one.get("key")]
        if on and account.get("needs_consent"):
            # Nothing to claim where the account has no id: only the consent is settled.
            await _share_on(context, account, service,
                            bool(account.get("claimed")) or bool(fields and not user_id))
            await changed(context)
        else:
            try:
                await run.io_bound(library.put_share, player["id"], account["extension"], on)
            except Exception as exc:  # noqa: BLE001
                ui.notify(t("said.save_failed"), caption=why(exc), type="negative")
            else:
                await changed(context)
        await _read_account(context, account)
        form.refill()

    with ui.column().classes("gap-0 manager-form"):
        form()


async def _read_account(context: dict[str, Any], account: dict[str, Any]) -> bool:
    """`account` as the player holds it now, in place, so the panel draws what was written.
    One that cannot be read stays as it was, and this says False."""
    try:
        held = await offload.io(context["library"].player_accounts, _of(context)["id"])
    except Exception:  # noqa: BLE001 - what is on screen stays
        logger.warning("Could not read a player's accounts", exc_info=True)
        return False
    fresh = next((one for one in held if one.get("extension") == account["extension"]), None)
    if fresh is None:
        return False
    account.clear()
    account.update(fresh)
    return True


async def _field_stands(context: dict[str, Any], account: dict[str, Any], key: str,
                        new_value: Any) -> bool:
    """Whether the account reads as the panel holds it once `key` was set to `new_value`:
    it does where nothing but that field's own value differs. The controls then stay as
    they are, and anything else that changed is drawn again. A secret box is emptied by a
    save, so a secret always draws again. An account that cannot be read stands, since
    there is nothing newer to draw."""
    expected = copy.deepcopy(account)
    secret = False
    for one in expected.get("fields") or []:
        if one.get("key") == key:
            secret = one.get("type") == "secret"
            if "value" in one:
                one["value"] = new_value
    if not await _read_account(context, account):
        return not secret
    return not secret and account == expected


def _field_rows(context: dict[str, Any], account: dict[str, Any],
                fields: list[dict[str, Any]], form: panel.Region) -> list[Any]:
    """An account that is only fields: drawn as a settings page draws them, each saved as
    it is left, with the player's id on the write."""
    library, player = context["library"], _of(context)
    extension = str(account["extension"])
    rows: list[Any] = []
    for field in fields:
        key = str(field["key"])

        @on_page
        async def save(new_value: Any, *, under: bool = False, key: str = key) -> str:
            try:
                await offload.io(library.put_account, player["id"], extension,
                                 {key: new_value})
            except Exception as exc:  # noqa: BLE001
                if under:
                    return why(exc)
                ui.notify(t("said.save_failed"), caption=why(exc), type="negative")
            else:
                await changed(context)
                if await _field_stands(context, account, key, new_value):
                    return ""
            form.refill()
            return ""

        rows.append((str(field.get("label") or key), ext_page.control(library, field, save)))
        if field.get("help"):
            rows.append(panel.note(str(field["help"])))
    return rows


def _user_id_value(user_id: str, page: str, service: str) -> Callable[[], None]:
    def draw() -> None:
        with ui.row().classes("items-center gap-3 no-wrap min-w-0"):
            ui.label(user_id).classes("manager-fact-value truncate min-w-0").tooltip(user_id)
            if page:
                panel.link_out(t("manager.players.your_page"), to=page,
                               hint=t("manager.players.your_page.help",
                                      service=service))()
    return draw


def _choose_or_card(context: dict[str, Any], account: dict[str, Any]) -> None:
    with ui.element("div").classes("manager-slot-actions"):
        panel.action(t("manager.players.choose_id"),
                     lambda: choose_user_id(context, account), icon=verbs.CHOOSE,
                     hint=t("manager.players.choose_id.help",
                            service=str(account.get("label") or account["extension"])))()
        _card_picker(t("manager.players.use_card"),
                    lambda event: use_card(context, account, event),
                    hint=t("manager.players.use_card.help"))


def _unclaimed_actions(context: dict[str, Any], account: dict[str, Any]) -> None:
    with ui.element("div").classes("manager-slot-actions"):
        panel.action(t("manager.players.change_id"),
                     lambda: choose_user_id(context, account), icon=verbs.EDIT,
                     hint=t("manager.players.change_id.help"))()
        _card_picker(t("manager.players.use_card"),
                    lambda event: use_card(context, account, event),
                    hint=t("manager.players.use_card.help"))


def _send_now(context: dict[str, Any], account: dict[str, Any]) -> None:
    with ui.element("div").classes("manager-slot-actions"):
        panel.action(t("manager.players.send_now"),
                     lambda: run_act(context, account, SEND_NOW), icon=verbs.SEND,
                     hint=t("manager.players.send_now.help"))()


def _card_section(library: Library, player: dict[str, Any], account: dict[str, Any],
                  service: str) -> None:
    with ui.row().classes("items-start gap-3 w-full no-wrap"):
        _card_tile(lambda: _open_card(library, player, account),
                  t("manager.players.show.help"))
        ui.label(t("manager.players.card_help", service=service)).classes(
            "manager-help grow")


def _card_actions(context: dict[str, Any], account: dict[str, Any]) -> None:
    library, player = context["library"], _of(context)
    with ui.element("div").classes("manager-slot-actions"):
        panel.action(t("manager.players.save_card"),
                     lambda: save_card(library, player, account), icon=verbs.FETCH,
                     hint=t("manager.players.save_card.help"))()
        _card_picker(t("manager.players.use_card"),
                    lambda event: use_card(context, account, event),
                    hint=t("manager.players.card_replace.help"))


def _card_tile(on_click: Callable[[], Any], hint: str) -> None:
    tile = ui.element("div").classes(
        "manager-source-thumb manager-source-thumb--small cursor-pointer") \
        .style("flex-direction: column; gap: 2px;")
    with tile:
        ui.icon("visibility").classes("manager-source-thumb-glyph")
        ui.label(t("manager.players.show")).classes("manager-help")
    tile.on("click", on_click)
    tile.tooltip(hint)


@on_page
async def _open_card(library: Library, player: dict[str, Any],
                     account: dict[str, Any]) -> None:
    try:
        drawn = await offload.io(library.player_card, player["id"], account["extension"])
    except Exception as exc:  # noqa: BLE001
        ui.notify(t("said.action_failed"), caption=why(exc), type="negative")
        return
    src = f"data:image/svg+xml;base64,{base64.b64encode(drawn).decode('ascii')}"
    mediaview.open_viewer(src, "", t("manager.players.card_of", name=shown_name(player)),
                          family="image")


@on_page
async def choose_user_id(context: dict[str, Any], account: dict[str, Any]) -> None:
    """Checked live as it is typed; choosing only saves the id, never a key."""
    library, player = context["library"], _of(context)
    extension = str(account["extension"])
    check = str(account.get("check") or "")
    service = str(account.get("label") or extension)
    seen: dict[str, Any] = {"was": object()}

    async def look() -> None:
        said = str(field.value or "").strip().lower()
        if said == seen["was"]:
            return
        seen["was"] = said
        holder.clear()
        if not said:
            go.disable()
            return
        if not check:
            with holder:
                panel.state(t("manager.players.id_unreachable", service=service),
                           "unknown", beside=said)()
            go.disable()
            return
        try:
            free = await offload.io(library.account_available, extension, check, said)
        except Exception:  # noqa: BLE001 - shown as "can't reach", whatever the reason
            with holder:
                panel.state(t("manager.players.id_unreachable", service=service),
                           "unknown", beside=said)()
            go.disable()
            return
        with holder:
            if free:
                panel.state(t("manager.players.id_available"), "on", beside=said)()
            else:
                panel.state(t("manager.players.id_taken"), "warn", beside=said)()
        (go.enable() if free else go.disable())

    with frame.opened(t("manager.players.id_title", service=service),
                      ignore_outside_click=True) as box:
        field = frame.field(user_id if (user_id := str(account.get("user_id") or "")) else "") \
            .props("spellcheck=false autocorrect=off autocapitalize=off autocomplete=off")
        holder = ui.element("div").classes("manager-fact-edit")
        with frame.footer():
            frame.cancel(lambda: box.submit(None))
            go = frame.answer(t("manager.players.choose"), lambda: box.submit(field.value),
                              icon=verbs.CHOOSE)
            go.disable()
    ui.timer(0.4, look)
    frame.focus(box, field, select=True)
    frame.enter_presses(go)
    chosen = await box
    if not chosen:
        return
    try:
        await run.io_bound(library.put_account, player["id"], extension,
                           {"user_id": str(chosen).strip().lower()})
    except Exception as exc:  # noqa: BLE001
        ui.notify(t("said.save_failed"), caption=why(exc), type="negative")
        return
    await context["rebuild"]()


@on_page
async def _share_on(context: dict[str, Any], account: dict[str, Any], service: str,
                    claimed: bool) -> None:
    """Share, turned on for the first time this account has seen what it makes public:
    unclaimed, that means claim with an empty send; already claimed, only the consent
    mark. Declining leaves Share off; so does a claim that fails."""
    library, player = context["library"], _of(context)
    extension = str(account["extension"])
    user_id = str(account.get("user_id") or "")
    if not str(player.get("initials") or "").strip():
        ui.notify(t("manager.players.share_needs_initials"), type="warning")
        return

    title = (t("manager.players.share_consent_title", id=user_id, service=service) if user_id
             else t("manager.players.share_consent_plain", service=service))
    with frame.opened(title, ignore_outside_click=True) as box:
        ui.label(t("manager.players.share_consent_heading")).classes("manager-help px-3")
        with ui.column().classes("gap-1 px-3"):
            for line in account.get("consent") or []:
                ui.label(f"• {line}").classes("manager-help")
        ui.label(t("manager.players.consent_note", service=service)) \
            .classes("manager-help px-3")
        with frame.footer():
            frame.cancel(lambda: box.submit(False))
            frame.answer(t("manager.players.share"), lambda: box.submit(True),
                        icon=verbs.SHARE)
    agreed = await box
    if not agreed:
        return
    try:
        await offload.io(library.account_act, player["id"], extension,
                         CONSENT if claimed else CLAIM)
        await run.io_bound(library.put_share, player["id"], extension, True)
    except Exception as exc:  # noqa: BLE001 - "taken", "can't reach": said, Share stays off
        ui.notify(t("said.on_failed"), caption=why(exc), type="negative")


@on_page
async def _remove_user_id(context: dict[str, Any], account: dict[str, Any],
                          service: str) -> None:
    library, player = context["library"], _of(context)
    extension = str(account["extension"])
    if not await confirm.ask(t("manager.players.forget_confirm"),
                             detail=t("manager.players.forget_detail",
                                     service=service),
                             confirm=t("word.remove"), icon=verbs.REMOVE):
        return
    try:
        await offload.io(library.account_act, player["id"], extension, DISCONNECT)
    except Exception as exc:  # noqa: BLE001
        ui.notify(t("said.save_failed"), caption=why(exc), type="negative")
        return
    await context["rebuild"]()


@on_page
async def _disconnect(context: dict[str, Any], account: dict[str, Any],
                      service: str) -> None:
    """VPinFE forgets the id and key; the account stays on the service with what it was
    sent. Save Card is offered every time, since nothing here tracks whether a copy of
    it is already on this device."""
    library, player = context["library"], _of(context)
    extension = str(account["extension"])
    also = (t("manager.players.save_card"),
           lambda: save_card(library, player, account), verbs.FETCH)
    if not await confirm.ask(t("manager.players.disconnect_confirm", service=service),
                             detail=t("manager.players.disconnect_detail", service=service),
                             confirm=t("manager.players.disconnect"), icon=verbs.FORGET,
                             also=also):
        return
    try:
        await offload.io(library.account_act, player["id"], extension, DISCONNECT)
    except Exception as exc:  # noqa: BLE001
        ui.notify(t("said.save_failed"), caption=why(exc), type="negative")
        return
    await context["rebuild"]()


@on_page
async def use_card(context: dict[str, Any], account: dict[str, Any], event: Any) -> None:
    """A card made on another device, taken into this account. Asked first where the
    account is already claimed, since the card's replaces it."""
    library, player = context["library"], _of(context)
    extension = str(account["extension"])
    service = str(account.get("label") or extension)
    text = (await event.file.read()).decode("utf-8", "replace")
    if account.get("claimed"):
        carded = bool(account.get("card"))
        detail = [t("manager.players.use_card_replaces", service=service)] + (
            [t("manager.players.remove_card_detail", service=service)] if carded else [])
        also = ((t("manager.players.save_card"),
                 lambda: save_card(library, player, account), verbs.FETCH) if carded else None)
        if not await confirm.ask(t("manager.players.card_confirm", name=shown_name(player)),
                                 detail=detail, confirm=t("manager.players.use_card"),
                                 icon=verbs.REPLACE, also=also):
            return
    try:
        await run.io_bound(library.use_card, player["id"], extension, text)
    except Exception as exc:  # noqa: BLE001
        ui.notify(t("manager.players.card_failed"), caption=why(exc), type="negative")
        return
    ui.notify(t("manager.players.card_used", service=service), type="positive")
    await context["rebuild"]()


@on_page
async def run_act(context: dict[str, Any], account: dict[str, Any], key: str) -> None:
    """Do it, and follow what it answers - a `message` said, a `url` opened."""
    library, player = context["library"], _of(context)
    try:
        said = await offload.io(library.account_act, player["id"], account["extension"], key)
    except Exception as exc:  # noqa: BLE001
        ui.notify(t("said.action_failed"), caption=why(exc), type="negative")
        return
    if said.get("url"):
        link_tab.open_link(str(said["url"]))
    if said.get("message"):
        ui.notify(str(said["message"]))
    await context["rebuild"]()


def card_filename(account: dict[str, Any]) -> str:
    """A name for the card's SVG file: nothing here calls an act to ask for one."""
    user_id = str(account.get("user_id") or "").strip()
    stem = f"{account['extension']}-{user_id}" if user_id else f"{account['extension']}-card"
    return f"{stem}.svg"


@on_page
async def save_card(library: Library, player: dict[str, Any],
                    account: dict[str, Any]) -> None:
    """Downloads core's drawing of the card."""
    extension = str(account["extension"])
    try:
        drawn = await offload.io(library.player_card, player["id"], extension)
    except Exception as exc:  # noqa: BLE001
        ui.notify(t("said.action_failed"), caption=why(exc), type="negative")
        return
    ui.download.content(drawn, card_filename(account), "image/svg+xml")
    ui.notify(t("manager.players.card_saved"), type="positive")


# -- plays ---------------------------------------------------------------------

def has_plays(player: dict[str, Any]) -> bool:
    """Not the owner, whose plays are the library's."""
    return not player.get("owner")


def played_line(game: dict[str, Any]) -> str:
    """Count, time and when, where they played it; their best score, where they have
    one. Either can stand alone: a score reaches its player whether they were up or not."""
    parts = []
    count = int(game.get("play_count") or 0)
    if count:
        parts.append(t("manager.players.played", count=count,
                       time=play_facts.played_for(int(game.get("play_time_seconds") or 0)),
                       when=play_facts.played_when(game.get("last_played"))))
    best = game.get("best_score") or {}
    if best:
        parts.append(t("manager.players.best", score=play_facts.score_lines(best)[0]))
    return game_tables.JOIN.join(parts)


async def plays(context: dict[str, Any]) -> None:
    """A row per game they played or scored on, in the record's order."""
    library, player = context["library"], _of(context)
    try:
        record = await offload.io(library.player_record, player["id"])
    except Exception as exc:  # noqa: BLE001
        panel.facts(ui, [panel.intro(t("manager.players.plays_failed"),
                                     hint=why(exc))])
        return
    shown = [one for one in record if played_line(one)]
    if library.thumbnail():
        await offload.io(library.load_tables)
    art = library.game_art()
    look = library.thumbnail_look()
    games = {str(one.get("id") or ""): one for one in library.games}
    with ui.column().classes("gap-0 manager-form w-full min-w-0"):
        if not shown:
            ui.label(t("manager.players.no_plays")).classes("manager-help px-3")
        for one in shown:
            game_id = str(one.get("game_id") or "")
            game = games.get(game_id)
            to = "/manager?" + deeplink.query({"view": "games", "game": game_id})
            with ui.row().classes("items-center gap-2 w-full no-wrap manager-member-row"), \
                    thumbnails.beside(None if art is None else art.get(game_id, ""),
                                      icons.GAMES, to=to, look=look), \
                    ui.column().classes("gap-0 grow min-w-0"):
                if game is None:
                    ui.label(t("manager.players.game_gone")).classes("manager-help")
                else:
                    panel.link(str(game.get("name") or ""), to=to)()
                    if made := game_tables.made(game):
                        ui.label(made).classes("manager-cell-made")
                ui.label(played_line(one)).classes("manager-help")
