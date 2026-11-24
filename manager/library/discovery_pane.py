"""What the Discovery rail's sections hold besides the lists: the Game section, the mark a
section wears in the rail, the foot bar that closes every section, and the pane's menu entry
that downloads what is missing.
"""

from __future__ import annotations

from typing import Any

from nicegui import ui

from common import icons
from common.failures import why
from common.i18n import t
from manager.kit import art, confirm, deeplink, panel, states, thumbnails, verbs, when
from manager.library import (
    discovery,
    discovery_ext,
    discovery_get,
    discovery_list,
    discovery_marks,
    mediaview,
)
from manager.library.discovery_get import BODY, RAIL, standing_of
from manager.library.discovery_kinds import Kind, Scene


def game(context: dict[str, Any], scene: Scene) -> None:
    """The picture, whether the library has the game, and what VPS says it is."""
    row, state = context["discovery"], context["state"]
    with ui.column().classes("gap-0 w-full min-w-0"):
        _picture(str(row[thumbnails.FIELD]), row["name"])
        if scene.counted:
            _library_card(context, state, scene.in_library)
        if scene.failed is not None:
            _media_failed(state, scene.failed)
        elif not scene.listed:
            panel.line(t("manager.discovery.lists_nothing"))
        _details(row)
    tail(context, scene, offer_add=False)


def kind_body(context: dict[str, Any], scene: Scene, key: str) -> None:
    """The section of one kind, with the foot bar under it."""
    if scene.kind(key) is not None:
        scene.tray.paint(BODY, ui.column().classes("gap-0 w-full min-w-0"),
                         lambda: _draw_kind(context, key))
    tail(context, scene)


def _draw_kind(context: dict[str, Any], key: str) -> None:
    """Drawn from the scene the context holds now, so a settled pane repaints the new one."""
    scene: Scene = context["scene"]
    kind = scene.kind(key)
    if kind is not None:
        discovery_list.body(context, scene, kind)


def tail(context: dict[str, Any], scene: Scene, *, offer_add: bool = True) -> None:
    """The foot bar, and the timer that follows the sources. Both are drawn in the open
    section, so a redraw ends them. `offer_add` is False where the section already holds the
    way to add the game."""
    if not scene.counted:
        return
    state = context["state"]
    discovery_get.foot(
        context, scene.tray, lambda: _add_this(state, context),
        end_hosted=discovery_ext.stopper(context, scene.tray, lambda: scene.game_id,
                                         scene.listed),
        offer_add=offer_add)
    if context["library"].online_sources():
        discovery_ext.watch(context, scene.tray, scene.game_id, scene.listed, scene.version)


async def _add_this(state: dict[str, Any], context: dict[str, Any]) -> None:
    await discovery.add(context["library"], [context["discovery"]],
                        lambda: discovery_get.redraw(state))


def _picture(src: str, name: str) -> None:
    with ui.element("div").classes("manager-vps-picture") as plate:
        if src:
            ui.html(f'<img src="{src}" alt="" loading="lazy">')
            ui.icon(verbs.ENLARGE).classes("manager-vps-picture-mark")
        else:
            ui.label(t("manager.discovery.no_picture")).classes("manager-help")
    if src:
        def enlarge() -> None:
            mediaview.open_image(src, name)

        plate.classes("manager-vps-picture--open").tooltip(t("word.enlarge"))
        plate.props("tabindex=0 role=button")
        plate.props["aria-label"] = t("word.enlarge")
        plate.on("click", enlarge).on("keydown.enter.self", enlarge) \
            .on("keydown.space.self.prevent", enlarge)


def _media_failed(state: dict[str, Any], failed: BaseException) -> None:
    with ui.row().classes("items-center gap-3 w-full"):
        panel.line(t("manager.discovery.media_failed"), hint=why(failed))
        panel.action(t("manager.page.try_again"), lambda: discovery_get.redraw(state),
                     icon=verbs.RESET, inline=True,
                     hint=t("manager.discovery.media_failed.help"))()


def _library_card(context: dict[str, Any], state: dict[str, Any], in_library: bool) -> None:
    """Where the entry stands with the library, in one box under the picture: the games of the
    library it is, a row each that opens the game, or that none is with the way to add it."""
    row, library = context["discovery"], context["library"]
    games = [one for one in library.games if str(one.get("vps_id") or "") == row["id"]]
    with ui.column().classes("manager-library-card"):
        if games:
            ui.label(t("manager.discovery.present")).classes("manager-library-said")
            for game_row in games:
                _game_row(context, game_row)
        elif in_library:
            ui.label(t("manager.discovery.present")).classes("manager-library-said")
        elif discovery_marks.ignored_status(context["library"], row, _redraw(context)):
            pass
        else:
            with ui.row().classes("items-center gap-x-3 no-wrap w-full"):
                ui.label(t("manager.discovery.absent")).classes("manager-library-said grow")
                panel.draft_button(t("manager.discovery.add"),
                                   lambda: _add_this(state, context),
                                   icon=verbs.ADD, primary=True)


def _redraw(context: dict[str, Any]) -> Any:
    """What puts the grid and the pane right after a change made from the pane."""
    async def again() -> None:
        redraw = context["state"].get("redraw_discovery")
        if callable(redraw):
            await redraw()

    return again


def _game_row(context: dict[str, Any], game_row: dict[str, Any]) -> None:
    library = context["library"]
    game_id = str(game_row["id"])
    wheel = (library.media.get(game_id) or {}).get("wheel") or {}
    played = (game_row.get("user") or {}).get("last_played")
    with panel.link_tile(to="/manager?" + deeplink.query({"view": "games", "game": game_id}),
                         classes="manager-library-row"):
        if wheel.get("present"):
            src = art.media(game_id, "wheel", version=str(wheel.get("version") or ""),
                            size=art.CELL)
            ui.html(f'<img src="{src}" alt="" loading="lazy">').classes("manager-library-wheel")
        else:
            ui.icon(icons.GAMES).classes("manager-library-wheel")
        with ui.column().classes("gap-0 grow min-w-0"):
            ui.label(str(game_row.get("name") or "")).classes("truncate")
            ui.label(t("manager.discovery.played", when=when.ago(played, inline=True))
                     if played else t("manager.discovery.never_played")).classes(
                "manager-help truncate")
        ui.icon(verbs.GO).tooltip(t("manager.discovery.to_games"))


def _details(row: dict[str, Any]) -> None:
    def vps() -> None:
        with ui.row().classes("items-center gap-2 no-wrap min-w-0"):
            ui.label(str(row["id"])).classes("manager-fact-value truncate min-w-0")
            if row["url"]:
                panel.out(to=row["url"], hint=t("manager.discovery.picture_open"))()

    panel.facts(ui, [
        (panel.HEADING, t("manager.workbench.details")),
        (t("manager.games.type"), discovery.type_word(row["game_type"]) or "-"),
        (t("manager.games.game_themes"), ", ".join(row["themes"]) or "-"),
        (t("manager.discovery.vps"), vps)])


# ---------------------------------------------------------------------------------------
# The mark a section wears in the rail


def corner(context: dict[str, Any], holder: Any, key: str) -> None:
    """The exception a kind is in, drawn at the right edge of its row and redrawn whenever a
    download on the entry moves."""
    def draw() -> None:
        scene: Scene = context["scene"]
        kind = scene.kind(key)
        if kind is not None:
            _mark(scene, kind)

    context["scene"].tray.paint(RAIL, holder, draw)


def _mark(scene: Scene, kind: Kind) -> None:
    """Failed, then Needs You, then downloading, then where the game stands with the kind:
    Have, Missing, Not Listed or Unknown, as the grid reads them. A game the library says
    nothing about wears nothing."""
    said = [one for key in kind.picks if (one := standing_of(scene.tray, key)) is not None]
    for level, glyph, word, hint in (
            ("bad", verbs.JOB_FAILED, "manager.discovery.state.failed",
             "manager.discovery.corner.failed"),
            ("warn", verbs.HAD_ERRORS, "manager.discovery.state.needs_you",
             "manager.discovery.corner.needs_you")):
        if any(one.level == level for one in said):
            _corner(level, t(word), t(hint), glyph=glyph)
            return
    going = [one for one in said if one.level == "unknown"]
    if going:
        known = [one.percent for one in going if one.percent is not None]
        percent = round(sum(known) / len(known)) if known and len(known) == len(going) else None
        _corner("going", t("manager.discovery.state.downloading"),
                t("manager.discovery.corner.downloading"), percent=percent)
        return
    if kind.standing == discovery.HAVE:
        _corner("have", t("manager.view.have"), t(_have_meaning(kind)), glyph=verbs.DONE)
    elif kind.standing == discovery.MISSING:
        _corner("missing", t("manager.view.missing"),
                t("manager.discovery.corner.missing_media" if kind.media
                  else "manager.discovery.corner.missing"))
    elif kind.standing == discovery.BLANK and kind.unread:
        _corner("unknown", t("manager.discovery.state.unknown"),
                t("manager.discovery.corner.unknown"))
    elif kind.standing == discovery.BLANK:
        _corner("blank", t("manager.discovery.state.not_listed"),
                t("manager.discovery.corner.not_listed", kind=kind.label))


def _have_meaning(kind: Kind) -> str:
    """The key of what Have says: whether the file is matched to a VPS release, which only a
    file kind can tell."""
    if kind.media:
        return "manager.discovery.corner.have_media"
    return ("manager.discovery.corner.have" if any(one.get("held") for one in kind.records)
            else "manager.discovery.corner.have_unmatched")


def _corner(level: str, word: str, meaning: str, *, glyph: str = "",
            percent: int | None = None) -> None:
    hint = states.line(word, meaning)
    with ui.element("span").classes(f"manager-corner manager-corner--{level}") as mark:
        if glyph:
            ui.icon(glyph, size="18px")
        elif percent is not None:
            ui.element("span").classes("manager-corner-ring").style(f"--p: {percent}")
        elif level == "going":
            ui.spinner(size="xs")
        elif level == "blank":
            ui.element("span").classes("manager-corner-bar")
        elif level == "unknown":
            ui.label("?").classes("manager-unknown")
        else:
            ui.element("span").classes("manager-corner-dash")
        ui.label(word).classes("manager-corner-word")
    mark.tooltip(hint)
    mark.props["aria-label"] = hint


# ---------------------------------------------------------------------------------------
# Download what is missing


def download_missing(context: dict[str, Any]) -> panel.Verb:
    """The pane's menu entry: the newest release of each file kind the game lacks that a
    source can download, in one batch after the person has read which."""
    scene: Scene | None = context.get("scene")
    newest = scene.newest if scene is not None else []
    label = t("manager.discovery.download_missing")
    if scene is None or not newest:
        return panel.Verb(label, hint=t("manager.discovery.download_missing.none"),
                          icon=verbs.FROM_ONLINE)

    async def run() -> None:
        if not await confirm.ask(
                t("manager.discovery.download_missing.ask", count=len(newest)),
                lines=[kind.label for kind, _pick in newest],
                confirm=t("manager.discovery.download_missing.confirm"),
                icon=verbs.FROM_ONLINE, danger=False):
            return
        await discovery_get.get(context, scene.tray, scene.game_id,
                                [pick for _kind, pick in newest])

    return panel.Verb(label, run, icon=verbs.FROM_ONLINE)
