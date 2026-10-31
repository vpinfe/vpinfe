"""The remote: VPinFE in one hand.

A fourth posture, and the whole specification. Not the frontend, which is read across a
room; not the Manager, which is a workbench at a desk. This is standing beside the
machine or sitting across from it - one thumb, screen lit for twenty seconds at a time.
Anything that does not fit that is not on this surface.

A second shell rather than a stylesheet, because the Manager *is* a workbench - a
splitter with a list on one side and an inspector on the other - and a phone cannot hold
two panes. Below its own width the organizing idea is simply absent, so what would ship
is a shell missing the thing it is for. The client, the reads, the tokens and the fact
list are all shared; only the shape is new.

Three screens, and they read as a sequence: what is happening, pick something, drive it.
"""

from __future__ import annotations

import asyncio
import html
import logging
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager, nullcontext
from datetime import UTC, datetime
from typing import Any

from nicegui import app, background_tasks, run, ui
from starlette.responses import JSONResponse

from common import events, icons
from common.config.labels import humanize
from common.devices import device_registry, install_identity, serving
from common.failures import why
from common.games import library_policy
from common.host import addresses, key_simulator
from common.host.launch_state import SOURCE_CAPTURE
from common.i18n import t
from common.runtime import lifecycle
from manager.frontend.players import (
    CARD_FILES,
    CLAIM,
    CONSENT,
    PERSON,
    kind_of,
    picture_address,
    save_card,
    shown_name,
)
from manager.kit import (
    acting,
    appearance,
    art,
    busy,
    confirm,
    dropdown,
    game_tables,
    offload,
    panel,
    play_facts,
    qr,
    remembered,
    stars,
    thumbnails,
    verbs,
)
from manager.kit import dialog as frame
from manager.kit.api import ApiClient, ApiError, local_base_url
from manager.kit.data import Library
from manager.kit.on_page import on_page
from manager.remote import remote_record
from manager.security.sign_in_gate import REMOTE_MANIFEST, REMOTE_PAGE

logger = logging.getLogger("vpinfe.manager.remote.remote")

_NAME_POLL_SECONDS = 2.0

WHEEL, GAMES, NOW, JOIN = "wheel", "games", "now", "join"

WHEEL_GLYPH = "view_carousel"

# The order is a sequence: what the cabinet shows, pick another, what is running.
SCREENS = (
    (WHEEL, t("manager.remote.tab_wheel"), WHEEL_GLYPH),
    (GAMES, t("manager.remote.tab_games"), icons.GAMES),
    (NOW, t("word.now"), "play_circle"),
)


def is_here(device: dict[str, Any], local_device_id: str) -> bool:
    """Whether this row is the install serving the page.

    Two answers, and the second is not a fallback for the first. An install records
    itself with nothing to dial - there is no address it would reach itself on - while
    every other row is written from an address it was heard at, and a phone is refused
    without one. So a row with no address *is* this machine, by construction.

    That matters because the id is not always there to compare: discovery reads the
    identity off the config file each time it is asked, and a read that lands while that
    file is being rewritten answers with no id at all. Keying only on the id made the
    whole surface report that there was nothing to drive.
    """
    if local_device_id and device.get("device_id") == local_device_id:
        return True
    return not str(device.get("address") or "").strip()


def base_url_of(device: dict[str, Any], local_device_id: str) -> str:
    """Where to send this target's requests.

    Loopback for the install serving this page, whatever address it recorded for itself:
    a machine's own entry holds the address other machines reach it on, and dialling
    that from here would leave the network to answer a question we can answer without
    it.
    """
    if is_here(device, local_device_id):
        return local_base_url()
    return device_registry.install_url(device)


def targets(devices: list[dict[str, Any]], local_device_id: str) -> list[dict[str, Any]]:
    """The devices worth aiming at, which is the ones that play.

    A device with no frontend is a real device and belongs in the Manager's list; it is
    not something a remote points at, and offering it would make the picker a list of
    machines rather than a list of answers to "where".
    """
    found = [one for one in devices
             if install_identity.FRONTEND in (one.get("features") or ())
             and base_url_of(one, local_device_id)]
    # This install first: it is the one the person is most likely to mean, and it is the
    # only one that is certainly there - it is serving the page.
    return sorted(found, key=lambda one: not is_here(one, local_device_id))


def target_name(device: dict[str, Any]) -> str:
    return str(device.get("display_name") or "").strip() or t("manager.remote.this_device")


def _identity_key(device: dict[str, Any]) -> str:
    """Per browser and per target: a phone aimed at two installs can hold a
    different answer to who is holding it on each."""
    return f"remote.who.{str(device.get('device_id') or '').strip() or 'here'}"


def remembered_identity(device: dict[str, Any],
                        roster: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Who this phone said it is for the target it is aimed at now, or None - nobody
    said, which behaves as the owner.

    Checked against the roster on every read rather than trusted: a kept player removed,
    or a guest signed out, elsewhere leaves nothing for a stale id to resolve to.
    """
    wanted = str((remembered.get(_identity_key(device)) or {}).get("player_id") or "").strip()
    if not wanted:
        return None
    return next((one for one in roster if one.get("id") == wanted), None)


def remember_identity(device: dict[str, Any], player_id: str) -> None:
    remembered.put(_identity_key(device), {"player_id": player_id} if player_id else None)


def is_someone_else(identity: dict[str, Any] | None) -> bool:
    """Whether the phone is answering for anyone other than the owner."""
    return identity is not None and not identity.get("owner")


def _has_initials(identity: dict[str, Any]) -> bool:
    """Whether `identity` can share: the extension's own `claim` refuses without
    initials, and drawing Share only to have it fail is worse than not drawing it."""
    return bool(str(identity.get("initials") or "").strip())


def last_played(games: list[dict[str, Any]]) -> dict[str, Any]:
    """The game played most recently, or nothing where none has been.

    Read off the library rather than from the frontend's own record of what it last
    launched: that one is per install and per window, and the question here is which
    game *this person* last had a game on.
    """
    played = [one for one in games if (one.get("user") or {}).get("last_played")]
    if not played:
        return {}
    return max(played, key=lambda one: str(one["user"]["last_played"]))


def _read_here() -> dict[str, Any]:
    """What this install knows about the network, made off the event loop.

    The Manager consumes its own process over HTTP, so a page handler that asks the API
    a question while holding the loop deadlocks - uvicorn cannot answer a request it is
    blocked inside.
    """
    client = ApiClient()
    return {
        "devices": client.devices(),
        "local_device_id": str(client.discovery().get("install_id") or ""),
    }


def _read_target(client: ApiClient) -> dict[str, Any]:
    """What the chosen machine is doing and what it can play, and the collection its
    frontend is showing where it is up, which is the one the phone opens on.

    Asked of the target rather than of this install, because that is the machine the
    launch is going to. Two installs can hold different libraries, and a list read from
    the wrong one offers games whose ids the target has never heard of.
    """
    showing = _frontend_of(client)
    kind = _art_kind()
    files, art_of = _files_of(client, kind)
    play = client.play_state()
    read: dict[str, Any] = {
        "play": play,
        "controls": _controls_of(client, play),
        "games": offered_games(client.library_entries()),
        "files": files,
        "art": art_of,
        "look": thumbnails.look(kind) if kind else None,
        "jobs": client.jobs(),
        "collections": client.collections(),
        "frontend": showing,
        "players": client.players(),
        "actions": _actions_of(client),
        **remote_record.read(client),
    }
    if mirroring(showing):
        read.update(_narrowed_to(client, str((showing or {}).get("collection") or "")))
    return read


def _controls_of(client: ApiClient, play: dict[str, Any]) -> dict[str, Any]:
    """What the running table can be asked to do. Empty where no table is up, where a
    recording holds it, and where the target is too old to know the route."""
    if not play.get("launching") or play.get("source") == SOURCE_CAPTURE:
        return {}
    try:
        return client.table_controls() or {}
    except Exception as exc:
        logger.info("remote: the target did not say what its table can be asked to do: %s", exc)
        return {}


def _actions_of(client: ApiClient) -> list[dict[str, Any]]:
    """What the target can be asked to do to itself; none where it cannot say."""
    try:
        return client.actions()
    except ApiError as exc:
        logger.info("remote: the target did not say what it can be asked to do: %s", exc)
        return []


def _files_of(client: ApiClient,
              kind: str) -> tuple[list[dict[str, Any]], dict[str, str] | None]:
    """Every table the target holds, and each game's picture where the Media settings draw
    one (None where they draw none). A target too old to list tables has no Tables lens
    and no pictures."""
    try:
        files = client.all_tables(media=kind)
    except ApiError as exc:
        logger.info("remote: the target did not list its tables: %s", exc)
        return [], None
    return files, thumbnails.by_game(files) if kind else None


def _frontend_of(client: ApiClient) -> dict[str, Any] | None:
    """None where the target cannot say, and the Remote then does without a mirror."""
    try:
        return client.frontend_state()
    except ApiError as exc:
        logger.info("remote: the target did not say what its frontend shows: %s", exc)
        return None


def _narrowed_to(client: ApiClient, collection: str) -> dict[str, Any]:
    """The phone's collection and the ids of the games in it; None for the whole library."""
    ids = ({one["id"] for one in offered_games(client.collection_entries(collection))}
           if collection else None)
    return {"collection": collection, "collection_ids": ids}


# The first three carry the whole state, so a phone that missed one is right after the
# next. `table.play_recorded` does not - it says what one play came to, and a phone that
# missed it finds out on the next one instead, which is a fact about that play, not
# something to resync.
FOLLOWED = (events.FRONTEND_STATE_CHANGED, events.PLAY_STATE_CHANGED,
            events.CAPTURE_RUN_CHANGED, events.PLAYERS_CHANGED, events.TABLE_PLAY_RECORDED)

RECONNECT_SECONDS = 5.0


def _follow(base_url: str, stop: threading.Event,
            heard: Callable[[str, dict], None]) -> None:
    """Hand the target's changes to `heard` until `stop` is set.

    Every connection opens with the target's current state, so reconnecting after a drop
    also repairs whatever was missed while it was down.
    """
    while not stop.is_set():
        try:
            for name, payload in ApiClient(base_url or None).follow(FOLLOWED, stop):
                heard(name, payload)
        except Exception as exc:
            logger.info("remote: stopped hearing from %s: %s", base_url, exc)
        stop.wait(RECONNECT_SECONDS)


def mirroring(showing: dict[str, Any] | None) -> bool:
    return bool((showing or {}).get("running"))


def frontend_closed(showing: dict[str, Any] | None) -> bool:
    """Closed, as against unknown: a target too old to say is not a closed frontend."""
    return showing is not None and not showing.get("running")


def wheel_id(showing: dict[str, Any] | None) -> str:
    return str(((showing or {}).get("game") or {}).get("id") or "")


def on_the_wheel(showing: dict[str, Any] | None, games: list[dict[str, Any]],
                 strays: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """The game on the wheel as the list holds it, or as the frontend named it where the
    list does not; nothing where the wheel is empty. `strays` holds the second kind, and is
    the caller's, so what is written onto one outlives the draw."""
    game_id = wheel_id(showing)
    if not game_id:
        return {}
    for game in games:
        if game.get("id") == game_id:
            return game
    stray = strays.setdefault(game_id, {"id": game_id})
    stray["name"] = str(((showing or {}).get("game") or {}).get("name") or "")
    return stray


def _opening_screen(state: dict[str, Any]) -> str:
    if (state.get("play") or {}).get("launching"):
        return NOW
    kind = str((state.get("target") or {}).get("kind") or "")
    if frontend_closed(state.get("frontend")) or kind == device_registry.KIND_VPX_MOBILE:
        return GAMES
    return WHEEL


_VIEWPORT = ("width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no, "
             "viewport-fit=cover")

_ICON_DIR = appearance.BASE_MOUNT + "/img/"
_HOME_SCREEN_ICONS = (180, 192, 512)
_HOME_SCREEN_BACKGROUND = "#0f0722"


@app.get(REMOTE_MANIFEST, include_in_schema=False)
def remote_manifest() -> JSONResponse:
    """What a phone reads to install the Remote to its Home Screen, where it opens without
    the browser's address and navigation bars."""
    return JSONResponse({
        "name": t("manager.remote.vpinfe_remote"),
        "start_url": REMOTE_PAGE,
        "scope": "/",
        "display": "standalone",
        "background_color": _HOME_SCREEN_BACKGROUND,
        "theme_color": _HOME_SCREEN_BACKGROUND,
        "icons": [{"src": f"{_ICON_DIR}vpinfe-remote-{size}.png",
                   "sizes": f"{size}x{size}", "type": "image/png"}
                  for size in _HOME_SCREEN_ICONS],
    }, media_type="application/manifest+json")


def _home_screen_head() -> str:
    """The tags that make the Remote installable. `use-credentials` sends the sign-in
    cookie with the manifest request, which a browser otherwise leaves out and the
    sign-in gate would answer with the sign-in page."""
    name = html.escape(t("manager.remote.vpinfe_remote"), quote=True)
    return (
        f'<link rel="manifest" href="{REMOTE_MANIFEST}" crossorigin="use-credentials">'
        f'<link rel="apple-touch-icon" href="{_ICON_DIR}vpinfe-remote-180.png">'
        '<meta name="apple-mobile-web-app-capable" content="yes">'
        '<meta name="mobile-web-app-capable" content="yes">'
        f'<meta name="apple-mobile-web-app-title" content="{name}">'
        '<meta name="apple-mobile-web-app-status-bar-style" content="default">'
        f'<meta name="theme-color" content="{_HOME_SCREEN_BACKGROUND}">')


@ui.page("/remote", title=t("manager.remote.vpinfe_remote"), reconnect_timeout=300,
         viewport=_VIEWPORT)
async def remote_page(screen: str = "") -> None:
    """The remote. `screen` names which of the three, so a place can be linked to."""
    # The same setting the desk surface reads. One install, one appearance - a phone
    # showing a different palette from the workbench it is beside reads as a different
    # product.
    chosen = appearance.configured_mode()
    ui.dark_mode(appearance.QUASAR_DARK[chosen])
    appearance.apply_colors(chosen)
    appearance.apply_flair(chosen)
    appearance.apply_surface("remote")
    # The shell takes the viewport once and everything below it is flex, the same way
    # the Manager's does - a pane that subtracts a fixed header height collapses the
    # moment that header changes.
    ui.query(".nicegui-content").classes("p-0 gap-0 remote-viewport")
    # A phone locking its screen is the most common disconnect there is, far more common
    # than anything a desk session sees, so the meta viewport matters as much as the
    # reconnect timeout above.
    # iOS ignores `user-scalable=no` for a pinch in Safari; this is what stops it there.
    ui.add_head_html(
        "<script>document.addEventListener('gesturestart', "
        "function (e) { e.preventDefault(); });</script>")
    ui.add_head_html(_home_screen_head())

    # Before the client connects, which is the only time a page can add to its own body.
    # Adding it from inside the screen that uses it looked right and installed nothing:
    # by then the page has been sent, and the pad had no behavior at all.
    ui.add_body_html(f"<script>{_HOLD_SCRIPT % {'renew': RENEW_MS, 'repeat': REPEAT_MS}}</script>")
    ui.add_body_html(f"<script>{_CLOCK_SCRIPT}</script>")

    # Once per page, not once per draw. Registered inside the screen that uses them, a
    # handler would be added again on every redraw and one thumb would send N presses.
    async def held(event: Any) -> None:
        await _say(client_for_target, str((event.args or {}).get("action") or ""),
                   "press")

    async def let_go(event: Any) -> None:
        await _say(client_for_target, str((event.args or {}).get("action") or ""),
                   "release")

    pressing: set[str] = set()

    async def repeated(event: Any) -> None:
        control_id = str((event.args or {}).get("id") or "")
        if not control_id or control_id in pressing:
            return
        pressing.add(control_id)
        try:
            await _press_control(client_for_target, control_id, quiet=True)
        finally:
            pressing.discard(control_id)

    # A press and its release are answered in the order they were sent: threads would
    # otherwise let a quick tap's release overtake its press and leave the key down.
    in_order: dict[str, asyncio.Lock] = {}

    @on_page
    async def key_phase(event: Any, phase: str) -> None:
        control_id = str((event.args or {}).get("id") or "")
        if not control_id:
            return
        async with in_order.setdefault(control_id, asyncio.Lock()):
            await _press_control(client_for_target, control_id, quiet=True, phase=phase)

    async def key_down(event: Any) -> None:
        await key_phase(event, "press")

    async def key_up(event: Any) -> None:
        await key_phase(event, "release")

    ui.on("remote_press", held)
    ui.on("remote_release", let_go)
    ui.on("remote_control", repeated)
    ui.on("remote_control_down", key_down)
    ui.on("remote_control_up", key_up)

    with ui.column().classes("w-full h-full items-center justify-center gap-3") as loading:
        ui.label(t("manager.remote.loading")).classes("text-sm opacity-60")
    busy.until_gone(loading)

    await ui.context.client.connected()
    loaded = await offload.io(_read_here)
    if ui.context.client.is_deleted:
        # Reading takes long enough that somebody can close the tab inside it, and there
        # is then nothing to draw on. Building anyway raises out of the page function and
        # logs a stack trace for somebody having changed their mind.
        return

    local_device_id = loaded["local_device_id"]
    aimable = targets(loaded["devices"], local_device_id)
    state: dict[str, Any] = {
        "screen": screen if screen in {key for key, *_ in SCREENS} | {JOIN} else "",
        "target": aimable[0] if aimable else {},
        "play": {}, "games": [], "files": [], "art": None, "look": None, "jobs": [],
        "collections": [], "find": "", "collection": "", "collection_ids": None,
        "lens": GAMES_LENS, "tiles": bool(remembered.get(TILES_KEY, False)),
        "frontend": None, "rows": {}, "relist": None, "refill_up": None,
        "capture": None, "run": {}, "waiting": [], "reviewing": None,
        "players": [], "identity": None, "record": {}, "accounts": [], "result": None,
        "actions": [], "strays": {}, "controls": {},
        "local_device_id": local_device_id,
    }

    def client_for_target() -> ApiClient:
        """A client aimed at whichever target is chosen. The picker is a base URL."""
        return ApiClient(base_url_of(state["target"], local_device_id) or None)

    async def reread_identity() -> None:
        """Who this phone is for the target now, and what goes with that: their own
        record, so a rating shown while browsing is theirs, and their accounts, for the
        identity sheet's Share and Save Card. Cleared, not failed, when there is nobody -
        that is the ordinary case, not an error."""
        state["identity"] = remembered_identity(state["target"], state["players"] or [])
        identity = state["identity"]
        if identity is None:
            state["record"], state["accounts"] = {}, []
            return
        try:
            state["record"] = {row["game_id"]: row for row in
                               await offload.io(client_for_target().player_record,
                                                identity["id"])}
            if identity.get("guest"):
                state["accounts"] = await offload.io(
                    client_for_target().player_accounts, identity["id"])
            else:
                state["accounts"] = []
        except Exception as exc:
            logger.info("remote: could not read %s's record: %s",
                        shown_name(identity), exc)
            state["record"], state["accounts"] = {}, []

    async def reread() -> None:
        """Ask the chosen machine again. Failure is a state, not a crash: a target that
        has gone away is the ordinary case for a page held in a hand."""
        if not state["target"]:
            return
        try:
            state.update(await offload.io(_read_target, client_for_target()))
            state["reachable"] = True
        except Exception as exc:
            logger.info("remote: %s did not answer: %s",
                        target_name(state["target"]), exc)
            state.update({"play": {}, "games": [], "files": [], "art": None, "look": None,
                          "jobs": [], "collections": [],
                          "frontend": None, "reachable": False, "capture": None,
                          "run": {}, "waiting": [], "reviewing": None,
                          "players": [], "identity": None, "record": {}, "accounts": [],
                          "actions": [], "controls": {}})
            follow()
            return
        await reread_identity()
        follow()

    def redraw() -> None:
        """All of it, always. The bar says which screen you are on, so a redraw that
        rebuilt only the screen left the mark behind on the one you came from - and the
        header's identity can change from something other than the header itself (This
        Is Me on its own sheet, a roster change heard from the target), so it is rebuilt
        here too rather than trusted to still be right."""
        header.clear()
        with header:
            _header(state, aimable, aim, client_for_target, redraw)
        body.clear()
        tabs.clear()
        state["relist"] = None
        state["refill_up"] = None
        state["refill_wheel"] = None
        with body:
            _screen(state, client_for_target, redraw)
        with tabs:
            _tabs(state, redraw)

    # The target's changes arrive on a thread of the follower's own and are applied here,
    # on the loop, in the order they were sent. Each carries the follower that heard it,
    # so a change still in flight from a target the phone has moved off is dropped.
    loop = asyncio.get_running_loop()
    page = ui.context.client
    arriving: asyncio.Queue[tuple[threading.Event, str, dict]] = asyncio.Queue()
    following: list[threading.Event] = []

    def unfollow() -> None:
        while following:
            following.pop().set()

    def follow() -> None:
        """Hear the chosen target, where it said what its frontend shows. One that could
        not say is too old to stream the event either, and refuses the subscription."""
        unfollow()
        # A read can outlast the page: a thread started for a closed tab is never stopped.
        if state.get("frontend") is None or page.is_deleted:
            return
        stop = threading.Event()
        following.append(stop)

        def heard(name: str, payload: dict) -> None:
            loop.call_soon_threadsafe(arriving.put_nowait, (stop, name, payload))

        threading.Thread(target=_follow, name="remote-follow", daemon=True,
                         args=(base_url_of(state["target"], local_device_id), stop,
                               heard)).start()

    async def adopt(showing: dict[str, Any]) -> bool:
        """Take the frontend's collection as the phone's, where it has moved."""
        name = str(showing.get("collection") or "")
        if not mirroring(showing) or name == state["collection"]:
            return False
        try:
            state.update(await offload.io(_narrowed_to, client_for_target(), name))
        except Exception as exc:
            logger.info("remote: could not read %s from %s: %s", name,
                        target_name(state["target"]), exc)
            return False
        return True

    @on_page
    async def changed(name: str, payload: dict) -> None:
        if name == events.PLAYERS_CHANGED:
            before = _who(state)
            state["players"] = list((payload.get("state") or {}).get("players") or [])
            await reread_identity()
            if before != _who(state):
                redraw()
            elif callable(state.get("refill_up")):
                state["refill_up"]()
            return
        if name == events.TABLE_PLAY_RECORDED:
            await _play_recorded(state, payload, client_for_target, redraw)
            return
        if name == events.CAPTURE_RUN_CHANGED:
            was, now = state.get("run") or {}, dict(payload.get("run") or {})
            state["run"] = now
            state["run_heard"] = int(state.get("run_heard") or 0) + 1
            if was and not now:
                try:
                    state["waiting"] = await offload.io(remote_record.waiting_of,
                                                        client_for_target())
                except Exception as exc:
                    logger.info("remote: could not read what waits: %s", exc)
            if was != now and state["screen"] != GAMES:
                redraw()
            return
        said = payload.get("state")
        if not isinstance(said, dict):
            return
        if name == events.PLAY_STATE_CHANGED:
            was = state.get("play") or {}
            state["play"] = said
            if said.get("launching"):
                state["reviewing"] = None
            table_changed = bool(was.get("launching")) != bool(said.get("launching"))
            if table_changed or (said.get("launching") and not state.get("controls")):
                target = state.get("target")
                read = await offload.io(_controls_of, client_for_target(), said)
                if state.get("target") is target:
                    state["controls"] = read
            if table_changed:
                held = state.pop("controls_sheet", None)
                if held is not None:
                    held.close()
            if table_changed or (bool(was.get("paused")) != bool(said.get("paused"))
                                 and state["screen"] != GAMES):
                redraw()
            return
        was = state.get("frontend")
        state["frontend"] = said
        await remote_record.heard(state, said, client_for_target, redraw)
        switched = await adopt(said)
        if mirroring(was) != mirroring(said):
            redraw()
            return
        if switched and state.get("relist"):
            state["relist"]()
        else:
            _mark_here(state["rows"], wheel_id(was), wheel_id(said))
        if callable(state.get("refill_wheel")):
            state["refill_wheel"]()

    async def listen() -> None:
        with page:
            while True:
                stop, name, payload = await arriving.get()
                if stop not in following:
                    continue
                try:
                    await changed(name, payload)
                except Exception:
                    logger.exception("remote: could not apply %s", name)

    listening = background_tasks.create(listen(), name="remote-listen")

    def gone() -> None:
        unfollow()
        listening.cancel()

    page.on_delete(gone)

    state["reread"] = reread

    async def aim(device: dict[str, Any]) -> None:
        """A different machine is a different library, a different state and a different
        base URL, so everything below the header is read again."""
        state.update({"target": device, "find": "", "collection": "",
                      "collection_ids": None})
        await reread()
        redraw()

    # `listen`, which draws what the target says, cannot run before this function next
    # waits, so nothing reaches `redraw` before the shell below exists.
    await reread()
    if page.is_deleted:
        return
    state["screen"] = state["screen"] or _opening_screen(state)
    # Header, then the screen, then the tabs, in that order and inside the shell: the
    # body has to be built here rather than earlier and reparented, because a NiceGUI
    # element belongs to whatever slot was open when it was made.
    with ui.column().classes("w-full h-full gap-0 remote-shell no-wrap"):
        header = ui.column().classes("w-full gap-0")
        body = ui.column().classes(
            "w-full grow min-h-0 gap-0 overflow-auto remote-body")
        tabs = ui.row().classes("w-full items-stretch gap-0 remote-tabs no-wrap")
    redraw()
    loading.delete()


def on_target(state: dict[str, Any], path: str) -> str:
    """Where the phone loads `path` from, or "" for none or no way there.

    The install serving this page answers on this page's own origin; another device on its
    own address, which the phone may not reach.
    """
    if not path:
        return ""
    device = state.get("target") or {}
    if is_here(device, str(state.get("local_device_id") or "")):
        return path
    base = base_url_of(device, str(state.get("local_device_id") or ""))
    return f"{base}{path}" if base else ""


def picture_of(state: dict[str, Any], player: dict[str, Any]) -> str:
    """Where the phone loads this player's picture from, or "" for none or no way there."""
    return on_target(state, picture_address(player))


def table_picture(state: dict[str, Any], game_id: str, size: int) -> str:
    """The game's table picture, as the phone loads it."""
    return on_target(state, art.media(game_id, "playfield", size=size)) if game_id else ""


def _avatar(state: dict[str, Any], player: dict[str, Any]) -> None:
    """A player's circle: their picture, over their initials, or a person where they have
    none - which is also what shows while the picture loads and if it never does."""
    initials = str(player.get("initials") or "").strip()
    address = picture_of(state, player)
    with ui.element("span").classes("remote-avatar"):
        if initials:
            ui.label(initials).classes("remote-avatar-initials")
        else:
            ui.icon(PERSON).classes("remote-avatar-glyph")
        if address:
            ui.html(f'<img src="{html.escape(address)}" alt="" draggable="false" '
                    'onerror="this.remove()">', sanitize=False, tag="span") \
                .classes("remote-avatar-picture")


def _header(state: dict[str, Any], aimable: list[dict[str, Any]], aim: Any,
            client_for_target: Callable[[], Any], redraw: Callable[[], None]) -> None:
    """The target, on every screen, because every action's meaning depends on it - and
    beside it, who this phone is answering for: the target alone for nobody said
    or a guest, the target and their initials for a kept player.

    Drawn as a picker only when there is a choice to make. With one target it is the
    name alone - a select with one option is furniture, which is the same rule that
    keeps a chip off every row.
    """
    identity = state.get("identity")
    with ui.row().classes("w-full items-center gap-2 remote-header no-wrap"):
        if len(aimable) > 1:
            names = {one["device_id"]: target_name(one) for one in aimable}
            by_id = {one["device_id"]: one for one in aimable}

            async def chosen(event: Any) -> None:
                await aim(by_id.get(event.value, {}))

            dropdown.Select(names, value=state["target"].get("device_id"),
                            on_change=chosen) \
                .classes("remote-target grow min-w-0")
        elif aimable:
            ui.label(target_name(state["target"])).classes("remote-target-name truncate")
        else:
            # Not an error: an install with no frontend feature is a library somebody
            # administers, and there is nothing here for a remote to drive.
            ui.label(t("manager.remote.nothing_drive")) \
                .classes("remote-target-name truncate")
        if state.get("target"):

            def open_sheet() -> None:
                _identity_sheet(state, client_for_target, redraw)

            with ui.row().classes("items-center gap-1 cursor-pointer remote-identity") \
                    .on("click", open_sheet):
                if identity is not None and kind_of(identity) == "":
                    ui.label(str(identity.get("initials") or "")) \
                        .classes("remote-identity-initials")
                if identity is not None and picture_of(state, identity):
                    _avatar(state, identity)
                else:
                    ui.icon(PERSON).classes("remote-identity-icon")

            panel.icon_only(verbs.MORE,
                            lambda: _dots_sheet(state, client_for_target, redraw),
                            hint=t("manager.remote.more")).classes("remote-dots")


def _tabs(state: dict[str, Any], redraw: Callable[[], None]) -> None:
    """The three screens, at the bottom, where a thumb is.

    Not a nav rail and not a drawer: with three destinations and one hand, the whole map
    is worth the space it takes, and hiding it behind a button charges a tap to find out
    what the page can do.
    """
    for key, label, icon in SCREENS:
        def go(_event: Any=None, key: Any=key) -> None:
            state["screen"] = key
            redraw()

        here = state["screen"] == key
        with ui.column().on("click", go) \
                .classes("remote-tab" + (" remote-tab--here" if here else "")):
            with ui.element("span").classes("remote-tab-mark"):
                ui.icon(icon).classes("remote-tab-icon")
                if key == NOW and (state.get("play") or {}).get("launching"):
                    ui.element("span").classes("remote-tab-dot") \
                        .tooltip(t("manager.remote.table_up"))
            ui.label(label).classes("remote-tab-label")


@contextmanager
def _asked(title: str) -> Iterator[Any]:
    """`manager.kit.dialog.opened`'s own shape, without calling it."""
    with frame.made() as box, ui.card().classes("manager-dialog"):
        ui.label(title).classes("manager-dialog-title")
        yield box


def _identity_sheet(state: dict[str, Any], client_for_target: Callable[[], Any],
                    redraw: Callable[[], None]) -> None:
    """Who this phone is answering for: a kept player chosen with This Is Me, a
    guest signed out, or nobody, in which case the sheet's own way into Join is the one
    a phone that never scanned the cabinet's QR still has."""
    identity = state.get("identity")
    players = state.get("players") or []

    @on_page
    async def become(player_id: str) -> None:
        remember_identity(state["target"], player_id)
        sheet.close()
        await state["reread"]()
        redraw()

    @on_page
    async def leave() -> None:
        remember_identity(state["target"], "")
        sheet.close()
        await state["reread"]()
        redraw()

    @on_page
    async def sign_out() -> None:
        if identity is None:
            return
        if not await confirm.ask(
                t("manager.players.sign_out_one", name=shown_name(identity)),
                detail=t("manager.players.sign_out_detail"),
                confirm=t("manager.players.sign_out"), icon=verbs.SIGN_OUT):
            return
        sheet.close()
        try:
            await run.io_bound(client_for_target().remove_player, identity["id"])
        except Exception as exc:
            ui.notify(t("said.action_failed"), caption=why(exc), type="negative")
            return
        remember_identity(state["target"], "")
        await state["reread"]()
        redraw()

    def go_join() -> None:
        sheet.close()
        state["screen"] = JOIN
        redraw()

    with frame.made().props("position=bottom") as sheet, \
            ui.card().classes("w-full remote-sheet"):
        if identity is None:
            ui.label(t("manager.remote.identify")).classes("remote-headline")
            for player in (one for one in players if kind_of(one) == ""):
                with ui.row().classes("w-full items-center gap-2 no-wrap"):
                    _avatar(state, player)
                    panel.remote_action(shown_name(player),
                                        lambda _e=None, pid=player["id"]: become(pid),
                                        icon=verbs.THIS_IS_ME,
                                        hint=t("manager.remote.this_is_me.help")) \
                        .classes("grow min-w-0")
            panel.remote_action(t("manager.remote.join_guest"), go_join, icon=verbs.JOIN,
                                hint=t("manager.remote.join_guest.help"))
        else:
            ui.label(t("manager.remote.youre", name=shown_name(identity))) \
                .classes("remote-headline")
            if identity.get("guest"):
                panel.remote_action(t("manager.players.sign_out"), sign_out,
                                    icon=verbs.SIGN_OUT,
                                    hint=t("manager.players.sign_out.help"))
                if not acting.play_only():
                    _guest_accounts(state, identity, client_for_target, redraw, sheet)
            else:
                panel.remote_action(t("manager.remote.not_me"), leave, icon=verbs.NOT_ME,
                                    hint=t("manager.remote.not_me.help"))
    sheet.open()


def _guest_accounts(state: dict[str, Any], identity: dict[str, Any],
                    client_for_target: Callable[[], Any], redraw: Callable[[], None],
                    sheet: Any) -> None:
    """Where a visitor who joined with Just Initials gets a card: typing a user id makes
    one, and once it is made, Save Card is how it leaves this phone. A guest who joined
    with a card already shares and already has one on their phone, so neither is offered
    - `account.share` alone decides, with no memory of how they joined."""
    @on_page
    async def share(account: dict[str, Any]) -> None:
        if await _share_with(state, identity, account, client_for_target, redraw):
            sheet.close()

    for account in state.get("accounts") or []:
        if account.get("share"):
            if account.get("card"):
                panel.remote_action(
                    t("manager.players.save_card"),
                    lambda _e=None, account=account:
                        save_card(Library(client_for_target()), identity, account),
                    icon=verbs.FETCH,
                    hint=t("manager.players.save_card.help"))
            continue
        service = str(account.get("label") or account["extension"])
        panel.remote_action(t("manager.remote.share_with", service=service),
                            lambda _e=None, account=account: share(account),
                            icon=verbs.SHARE,
                            hint=t("manager.remote.share_with.help"))


async def _share_with(state: dict[str, Any], identity: dict[str, Any],
                      account: dict[str, Any], client_for_target: Callable[[], Any],
                      redraw: Callable[[], None]) -> bool:
    """The panel's own flow, drawn for a phone: an id checked live, the panel's consent,
    then claimed and shared. One dialog, stepped in place from Choose to Consent - never
    close `box` to move between steps (U8). Answers whether Share ended up on, so the
    identity sheet behind it knows to close."""
    extension = str(account["extension"])
    service = str(account.get("label") or extension)
    check = str(account.get("check") or "")
    library = Library(client_for_target())
    held: dict[str, Any] = {"id": str(account.get("user_id") or ""), "seen": object()}
    refs: dict[str, Any] = {}

    def _use_card_action() -> None:
        @on_page
        async def with_card(event: Any) -> None:
            text = (await event.file.read()).decode("utf-8", "replace")
            try:
                await run.io_bound(library.use_card, identity["id"], extension, text)
                await run.io_bound(library.put_share, identity["id"], extension, True)
            except Exception as exc:  # noqa: BLE001
                ui.notify(t("manager.players.card_failed"), caption=why(exc),
                          type="negative")
                return
            box.submit(True)

        uploader = ui.upload(on_upload=with_card, auto_upload=True).classes("hidden")
        uploader.props(f'accept="{",".join(CARD_FILES)}"')
        uploader.on("finish",
                    js_handler=f"() => getElement({uploader.id}).$refs.qRef.reset()")
        button = panel.remote_action(t("manager.remote.use_card"), icon=verbs.FROM_FILE,
                                     hint=t("manager.players.use_card.help"))
        button.on("click",
                 js_handler=f"() => getElement({uploader.id}).$refs.qRef.pickFiles()")

    async def look() -> None:
        said = str(refs["field"].value or "").strip().lower()
        if said == held["seen"]:
            return
        held["seen"] = said
        refs["holder"].clear()
        refs["extra"].clear()
        if not said:
            refs["go"].disable()
            return
        if not check:
            with refs["holder"]:
                panel.state(t("manager.players.id_unreachable", service=service),
                           "unknown", beside=said)()
            refs["go"].disable()
            return
        try:
            free = await offload.io(library.account_available, extension, check, said)
        except Exception:  # noqa: BLE001 - shown as "can't reach", whatever the reason
            with refs["holder"]:
                panel.state(t("manager.players.id_unreachable", service=service),
                           "unknown", beside=said)()
            refs["go"].disable()
            return
        with refs["holder"]:
            if free:
                panel.state(t("manager.players.id_available"), "on", beside=said)()
            else:
                panel.state(t("manager.players.id_taken"), "warn", beside=said)()
        if free:
            refs["go"].enable()
        else:
            refs["go"].disable()
            with refs["extra"]:
                _use_card_action()

    @on_page
    async def to_consent() -> None:
        said = str(refs["field"].value or "").strip().lower()
        if not said:
            return
        try:
            await run.io_bound(library.put_account, identity["id"], extension,
                               {"user_id": said})
        except Exception as exc:  # noqa: BLE001
            ui.notify(t("said.save_failed"), caption=why(exc), type="negative")
            return
        held["id"] = said
        refs["timer"].deactivate()
        draw_consent()

    @on_page
    async def do_share() -> None:
        act = CONSENT if account.get("claimed") else CLAIM
        try:
            await offload.io(library.account_act, identity["id"], extension, act)
            await run.io_bound(library.put_share, identity["id"], extension, True)
        except Exception as exc:  # noqa: BLE001 - "taken", "can't reach": said, Share off
            ui.notify(t("said.on_failed"), caption=why(exc), type="negative")
            return
        box.submit(True)

    def draw_choose() -> None:
        heading.text = t("manager.players.id_title", service=service)
        body.clear()
        buttons.clear()
        with body:
            refs["field"] = frame.field(held["id"])
            refs["holder"] = ui.element("div").classes("manager-fact-edit")
            refs["extra"] = ui.row().classes("w-full")
        with buttons:
            frame.cancel(lambda: box.submit(False))
            refs["go"] = frame.answer(t("manager.players.choose"), to_consent,
                                      icon=verbs.CHOOSE)
            refs["go"].disable()
        refs["timer"] = ui.timer(0.4, look)
        frame.focus(box, refs["field"], select=True)
        frame.enter_presses(refs["go"])

    def draw_consent() -> None:
        heading.text = t("manager.players.share_consent_title", id=held["id"],
                         service=service)
        body.clear()
        buttons.clear()
        can_share = _has_initials(identity)
        with body:
            if not can_share:
                ui.label(t("manager.remote.share_needs_initials")) \
                    .classes("manager-help px-3")
            else:
                ui.label(t("manager.players.share_consent_heading")) \
                    .classes("manager-help px-3")
                with ui.column().classes("gap-1 px-3"):
                    for line in account.get("consent") or []:
                        ui.label(f"• {line}").classes("manager-help")
                ui.label(t("manager.players.consent_note", service=service)) \
                    .classes("manager-help px-3")
        with buttons:
            frame.cancel(lambda: box.submit(False))
            if can_share:
                frame.answer(t("manager.players.share"), do_share, icon=verbs.SHARE)

    with frame.made() as box, ui.card().classes("manager-dialog"):
        heading = ui.label("").classes("manager-dialog-title")
        body = ui.column().classes("w-full gap-1")
        buttons = frame.footer()
        draw_choose()
    went = bool(await box)
    if went:
        await state["reread"]()
        redraw()
    return went


# The lifecycle pairs the dots sheet offers, in the order it draws them. The first two act on
# VPinFE and the last two on the device it runs on.
_VPINFE_PAIRS = ((lifecycle.VPINFE, lifecycle.RESTART), (lifecycle.VPINFE, lifecycle.STOP))
_DEVICE_PAIRS = ((lifecycle.SYSTEM, lifecycle.RESTART), (lifecycle.SYSTEM, lifecycle.STOP))
_PAIR_ICONS = {lifecycle.RESTART: verbs.RESTART, lifecycle.STOP: verbs.POWER}


def _offered_pairs(state: dict[str, Any],
                   pairs: tuple[tuple[str, str], ...]) -> list[tuple[str, str]]:
    """Which of `pairs` the target lists as ready to run. Nothing for a browser that came in
    with a Remote pass: the pass holds Play, and these need more."""
    if acting.play_only():
        return []
    ready = {(str(one.get("scope") or ""), str(one.get("action") or ""))
             for one in state.get("actions") or [] if one.get("available", True)}
    return [pair for pair in pairs if pair in ready]


_OPEN_FRONTEND = (lifecycle.FRONTEND, lifecycle.START)


def _open_frontend_offer(state: dict[str, Any], client_for_target: Callable[[], Any],
                         after: Callable[[], object] | None = None) -> None:
    """The action that opens the frontend's windows, for a place that says they are closed.
    Drawn only where the target lists it as ready and this browser may run it, so a
    Remote pass sees the note alone."""
    if not _offered_pairs(state, (_OPEN_FRONTEND,)):
        return
    label = lifecycle.label(*_OPEN_FRONTEND)

    @on_page
    async def open_it() -> None:
        if after:
            after()
        try:
            done = await run.io_bound(client_for_target().perform_action, lifecycle.FRONTEND,
                                      lifecycle.START, "asked from the Remote")
        except Exception as exc:
            ui.notify(t("said.action_failed"), caption=why(exc), type="negative",
                      position="top")
            return
        if (done or {}).get("performed"):
            ui.notify(t("manager.devices.windows_opened"), type="positive", position="top")
        else:
            ui.notify(t("manager.devices.not_happen", label=label), type="warning",
                      position="top")

    panel.remote_action(label, open_it, icon=verbs.OPEN_WINDOW,
                        hint=t("action.frontend.start.help"))


def _dots_sheet(state: dict[str, Any], client_for_target: Callable[[], Any],
                redraw: Callable[[], None]) -> None:
    """What is rare or costly, behind the header's dots rather than on a screen of its own.
    Tutorial is the frontend's own action, so a closed frontend hears nothing: the sheet
    says so instead of offering a button that reports success. The lifecycle actions are
    the install's, and are offered whatever the frontend is doing."""
    target = state["target"]
    name = target_name(target)

    @on_page
    async def tutorial() -> None:
        sheet.close()
        await _say(client_for_target, "tutorial", "tap")

    def who() -> None:
        sheet.close()
        with ui.context.client.layout:
            _identity_sheet(state, client_for_target, redraw)

    @on_page
    async def lifecycle_action(scope: str, action: str) -> None:
        label = lifecycle.label(scope, action)
        if not await confirm.ask(
                t("manager.remote.do_ask", label=label, target_name=name),
                detail=t(f"action.{scope}.{action}.help"),
                confirm=label, icon=_PAIR_ICONS[action]):
            return
        sheet.close()
        try:
            await run.io_bound(client_for_target().perform_action, scope, action,
                               "asked from the Remote")
        except Exception as exc:
            ui.notify(t("said.action_failed"), caption=why(exc), type="negative",
                      position="top")

    def draw_pairs(pairs: list[tuple[str, str]]) -> None:
        for scope, action in pairs:
            panel.remote_action(
                lifecycle.label(scope, action),
                lambda _e=None, scope=scope, action=action: lifecycle_action(scope, action),
                icon=_PAIR_ICONS[action], danger=action == lifecycle.STOP,
                hint=t(f"action.{scope}.{action}.help"))

    if str(target.get("kind") or "") == device_registry.KIND_VPX_MOBILE:
        said = t("manager.remote.cannot_drive", target_name=name)
    elif state.get("reachable") is False or frontend_closed(state.get("frontend")):
        said = t("manager.remote.frontend_closed")
    elif (state.get("play") or {}).get("launching"):
        said = t("manager.remote.wheel_busy")
    else:
        said = ""
    own = _offered_pairs(state, _VPINFE_PAIRS)
    device = _offered_pairs(state, _DEVICE_PAIRS)

    # Under the page's layout, not the header: a redraw clears the header, and a confirm
    # left open would go with it.
    with ui.context.client.layout, frame.made().props("position=bottom") as sheet, \
            ui.card().classes("w-full remote-sheet"):
        ui.label(name).classes("remote-headline")
        if said:
            ui.label(said).classes("remote-note")
            if frontend_closed(state.get("frontend")):
                _open_frontend_offer(state, client_for_target, sheet.close)
        else:
            panel.remote_action(t("word.tutorial"), tutorial, icon=verbs.GET_HELP,
                                hint=t("manager.remote.tutorial.help"))
        panel.remote_action(t("manager.remote.who_playing"), who, icon=verbs.THIS_IS_ME,
                            hint=t("manager.remote.who_playing.help"))
        draw_pairs(own)
        if device:
            ui.label(t("manager.remote.the_device")).classes("manager-card-title")
            draw_pairs(device)
    sheet.open()


def _screen(state: dict[str, Any],
            client_for_target: Callable[[], Any],
            redraw: Callable[[], None]) -> None:
    if not state["target"]:
        return _nothing(t("manager.remote.nothing_drive"))
    if state.get("reachable") is False:
        # Said before anything is pressed rather than as the answer to a press: a target
        # that is not there is a fact about the screen, not a failed request.
        return _unreachable(state, redraw)
    if state["screen"] == NOW:
        _now(state, client_for_target, redraw)
    elif state["screen"] == GAMES:
        _play(state, client_for_target, redraw)
    elif state["screen"] == JOIN:
        _join(state, client_for_target, redraw)
    else:
        _wheel(state, client_for_target, redraw)


def _nothing(said: str) -> None:
    with ui.column().classes("w-full items-center justify-center grow gap-2 p-6"):
        ui.label(said).classes("remote-empty text-center")


def _unreachable(state: dict[str, Any], redraw: Callable[[], None]) -> None:
    """The target is not there, and the way out is to ask it again.

    Nothing here says *why*: this end cannot tell a machine that is switched off from
    one whose network dropped, and a guess dressed as a diagnosis is worse than the
    plain fact. What it can offer is the thing somebody does next, which is switch the
    machine on and ask again.

    The picker upstream does not mark a dead target, and deliberately. `last_reachable`
    says when a device last answered, and a device recorded a moment ago has a fresh
    timestamp whether or not it is on - so a mark drawn from it would call this one
    alive. Asking is the only thing that knows.
    """
    async def again() -> None:
        await state["reread"]()
        redraw()

    with ui.column().classes("w-full items-center justify-center grow gap-3 p-6"):
        ui.label(t("manager.remote.not_answering", target_name=(target_name(state['target'])))) \
            .classes("remote-empty text-center")
        ui.button(t("manager.remote.try"), icon=verbs.REFRESH, on_click=again) \
            .props("no-caps flat").classes("remote-action")


def _now(state: dict[str, Any],
         client_for_target: Callable[[], Any],
         redraw: Callable[[], None]) -> None:
    """What this machine is doing, and the one thing worth doing about it.

    What is playing, what work is running and anything wanting attention are three
    answers to one question - what is this machine doing - so they are one screen. Split
    apart, this one has nothing to say most of the time.
    """
    play = state.get("play") or {}
    run_now = state.get("run") or {}
    with ui.column().classes("w-full gap-3 p-3"):
        if state.get("reviewing"):
            remote_record.controller(state, client_for_target, redraw)
        elif remote_record.going(run_now):
            remote_record.run_card(state, client_for_target, redraw)
        else:
            if play.get("launching"):
                _playing(play, state, client_for_target, redraw)
            elif frontend_closed(state.get("frontend")):
                with ui.column().classes("w-full gap-1 manager-card"):
                    ui.label(t("manager.remote.frontend_closed")).classes("remote-empty")
                    _open_frontend_offer(state, client_for_target)
            if run_now:
                remote_record.run_card(state, client_for_target, redraw)
            elif state.get("waiting"):
                remote_record.waiting_card(
                    state, client_for_target, redraw,
                    reviewable=mirroring(state.get("frontend")) and not play.get("launching"))
            if not play.get("launching"):
                _up_section(state, client_for_target)
                if state.get("result"):
                    _result_card(state, client_for_target)
                else:
                    _idle(state, client_for_target, redraw)
        _running_jobs(state)


def takes_pictures(play: dict[str, Any], showing: dict[str, Any] | None) -> bool:
    """Whether a table is up that a player can take a picture of from here."""
    return (bool(play.get("launching")) and play.get("source") != SOURCE_CAPTURE
            and not frontend_closed(showing))


def _hero(state: dict[str, Any], game_id: str, name: str, made: str, *, badge: str,
          pulse: bool = False, tall: bool = False,
          open_sheet: Callable[[], None] | None = None,
          heart: Callable[[], None] | None = None) -> None:
    """A game's table picture with its name over it: the Wheel's game and Now's table.

    The picture is a layer over a ground, so a game with none, or one that does not load,
    is a name on the ground. `heart` draws a control at the trailing corner outside the part
    that opens the sheet, so pressing it does not also open that.
    """
    address = table_picture(state, game_id, art.PANEL)
    with ui.element("div").classes("remote-hero" + (" remote-hero--tall" if tall else "")):
        with ui.element("div").classes("remote-hero-face") as face:
            if address:
                ui.html(f'<img src="{html.escape(address)}" alt="" draggable="false" '
                        'onerror="this.remove()">', sanitize=False, tag="span") \
                    .classes("remote-hero-art")
            ui.element("div").classes("remote-hero-scrim")
            with ui.row().classes("remote-hero-badge items-center gap-2 no-wrap"):
                if pulse:
                    ui.element("span").classes("remote-pulse")
                ui.label(badge)
            with ui.column().classes("remote-hero-text gap-0" + (" remote-hero-text--heart"
                                                                if heart else "")):
                ui.label(name).classes("remote-hero-name")
                if made:
                    ui.label(made).classes("remote-hero-made truncate")
        if open_sheet is not None:
            face.on("click", open_sheet).classes("cursor-pointer")
        if heart is not None:
            heart()


def _elapsed_seconds(started_at: str) -> int | None:
    try:
        began = datetime.fromisoformat(started_at)
    except ValueError:
        return None
    if began.tzinfo is None:
        began = began.replace(tzinfo=UTC)
    return max(0, int((datetime.now(UTC) - began).total_seconds()))


def _clock(started_at: str) -> None:
    """How long this table has been up. The page's own script ticks every `data-elapsed`
    label."""
    seconds = _elapsed_seconds(started_at)
    if seconds is None:
        return
    with ui.column().classes("w-full items-center gap-0"):
        ui.label(_hms(seconds)).classes("remote-clock") \
            .props(f'data-elapsed={seconds}')
        ui.label(t("manager.remote.session_clock")).classes("remote-note")


def _hms(seconds: int) -> str:
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def _playing(play: dict[str, Any], state: dict[str, Any], client_for_target: Callable[[], Any],
             redraw: Callable[[], None], note: str = "") -> None:
    if play.get("source") == SOURCE_CAPTURE and remote_record.going(state.get("run") or {}):
        remote_record.run_card(state, client_for_target, redraw)
        return

    @on_page
    async def quit_table() -> None:
        try:
            await run.io_bound(client_for_target().stop_play)
        except Exception as exc:
            ui.notify(t("manager.remote.quit_failed"), caption=why(exc), type="negative")
            return
        state["play"] = await offload.io(client_for_target().play_state)
        redraw()

    paused = bool(play.get("paused"))
    game_id = str(play.get("game_id") or "")
    held: dict[str, Any] = next(
        (one for one in state.get("games") or [] if one.get("id") == game_id), {})
    _hero(state, game_id, str(play.get("game_name") or t("manager.remote.table")),
          game_tables.made(held) if held else "",
          badge=t("manager.remote.paused" if paused else "manager.remote.playing"),
          pulse=not paused, tall=True)
    if note:
        ui.label(note).classes("remote-note text-center")
    _clock(str(play.get("started_at") or ""))
    controls = state.get("controls") or {}
    pause = _control_of(controls, "pause")
    pictures = takes_pictures(play, state.get("frontend"))
    if pause or pictures:
        with ui.row().classes("w-full gap-2 no-wrap"):
            if pause:
                _pause_key(pause, paused, controls, client_for_target)
            if pictures:
                _tap_button("take_picture", client_for_target, icon=verbs.TAKE_PICTURE,
                            hint=t("manager.remote.take_picture.help"), size="action")
                if paused:
                    _tap_button("back", client_for_target, icon=verbs.BACK,
                                hint=t("manager.remote.back.help"), size="action")
    if any(one.get("id") != "pause" for one in controls.get("controls") or []):
        panel.remote_action(
            t("manager.remote.table_controls"),
            lambda _e=None: _controls_sheet(state, client_for_target),
            icon=verbs.TUNE, hint=t("manager.remote.table_controls.help"))
    panel.remote_action(t("manager.remote.quit_table"), quit_table, icon=verbs.STOP,
                        danger=True, hint=t("manager.remote.quit_table.help"))


def _control_of(controls: dict[str, Any], control_id: str) -> dict[str, Any] | None:
    return next((one for one in controls.get("controls") or []
                 if one.get("id") == control_id), None)


def _blocked(controls: dict[str, Any], control: dict[str, Any]) -> str:
    """Why a control cannot be pressed now, or an empty string where it can: the control's
    own reason, or the install's where it cannot send keys at all."""
    press = controls.get("press") or {}
    if not press.get("available", True):
        said = press.get("reason")
        return key_simulator.words(said) if said else str(control.get("reason") or "")
    if not control.get("available", True):
        return str(control.get("reason") or "")
    return ""


def _pause_key(pause: dict[str, Any], paused: bool, controls: dict[str, Any],
               client_for_target: Callable[[], Any]) -> None:
    async def tap() -> None:
        # Pause is a toggle, and the label flips only when the table reports it: a second
        # tap before then would undo the first.
        key.disable()
        try:
            await _press_control(client_for_target, "pause")
            await asyncio.sleep(PAUSE_SETTLE_S)
        finally:
            key.enable()

    key = panel.remote_key(
        t("manager.page.resume" if paused else "manager.remote.pause"), tap,
        icon=verbs.RUN if paused else verbs.PAUSE, size="action",
        hint=_blocked(controls, pause)
        or t("manager.remote.resume_table.help" if paused else "manager.remote.pause_table.help"))
    if _blocked(controls, pause):
        key.disable()


PAUSE_SETTLE_S = 1.0
_CONTROL_GLYPHS = {
    "start": verbs.RUN, "show_rules": verbs.RULES, "reset": verbs.RESET,
    "extra_ball": verbs.ADD, "fire": verbs.FIRE, "volume_down": verbs.VOLUME_DOWN,
    "left_flipper": verbs.LEFT_FLIPPER, "right_flipper": verbs.RIGHT_FLIPPER,
    "launch_ball": verbs.LAUNCH_BALL, "left_magna": verbs.MAGNA_SAVE,
    "right_magna": verbs.MAGNA_SAVE, "nudge_left": verbs.NUDGE_LEFT,
    "nudge_center": verbs.NUDGE_CENTER, "nudge_right": verbs.NUDGE_RIGHT,
    "volume_up": verbs.VOLUME_UP, "coin_door": verbs.COIN_DOOR, "menu_up": verbs.UP,
    "menu_down": verbs.DOWN, "menu_enter": verbs.CHOOSE, "menu_cancel": verbs.CANCEL,
}
_GROUP_GLYPHS = {
    "play": verbs.GAME_CONTROL, "nudge": verbs.NUDGE_CENTER,
    "game": verbs.GAME_CONTROL, "coins": verbs.COIN, "sound": verbs.VOLUME_UP,
    "menu": verbs.COIN_DOOR, "service": verbs.SERVICE, "debug": verbs.DEBUG,
}
# Where the Remote folds what is rare, so the sheet opens on what a player reaches for.
_FOLDED_GROUPS = ("nudge", "menu", "service", "debug")
# Controls that take repeated presses for as long as they are held.
_REPEATING_GROUPS = ("sound",)


@on_page
async def _press_control(client_for_target: Callable[[], Any], control_id: str, *,
                         quiet: bool = False, phase: str = "tap") -> None:
    """One press of a control the target listed, or the press or the release of one held
    down. A failure is said once, except for a control held down, which stops on its own
    when the thumb lifts."""
    try:
        said = await run.io_bound(client_for_target().press_control, control_id,
                                  **({} if phase == "tap" else {"phase": phase}))
    except Exception as exc:
        logger.info("remote: control %s did not reach the target: %s", control_id, exc)
        if not quiet:
            ui.notify(t("manager.remote.control_failed"), caption=why(exc), type="negative",
                      position="top")
        return
    if not (said or {}).get("pressed") and not quiet:
        ui.notify(t("manager.remote.control_not_sent"), type="warning", position="top")


def _controls_sheet(state: dict[str, Any], client_for_target: Callable[[], Any]) -> None:
    """The keys the running table's app declares, drawn as the target listed them.

    The Remote holds no list of its own: a group, a label and whether a control can be
    pressed are the target's, so a different app draws different keys here without this
    changing. Nudge, Service and Debug are folded.
    """
    controls = state.get("controls") or {}
    listed = [one for one in controls.get("controls") or [] if one.get("id") != "pause"]
    press = controls.get("press") or {}
    notes = [] if not press.get("available", True) else list(controls.get("notes") or [])

    def draw_group(group: str, held: list[dict[str, Any]]) -> None:
        ui.label(t(f"controls.group.{group}")).classes("manager-card-title")
        if any(one.get("side") for one in held):
            with ui.element("div").classes("remote-controls"):
                for side in ("left", "", "right"):
                    with ui.element("div").classes("remote-hand"):
                        for one in held:
                            if (one.get("side") or "") == side:
                                draw_key(group, one)
        else:
            with ui.element("div").classes("remote-controls"):
                for one in held:
                    draw_key(group, one)
        ids = {str(one.get("id")) for one in held}
        for note in [one for one in notes if ids & set(one.get("controls") or [])]:
            notes.remove(note)
            ui.label(str(note.get("text") or "")).classes("remote-note")

    def draw_key(group: str, one: dict[str, Any]) -> None:
        control_id = str(one.get("id") or "")
        label = str(one.get("label") or control_id)
        blocked = _blocked(controls, one)
        glyph = _CONTROL_GLYPHS.get(control_id) or _GROUP_GLYPHS.get(group, verbs.TUNE)

        async def tap() -> None:
            await _press_control(client_for_target, control_id)

        follows_thumb = bool(one.get("holdable"))
        key = panel.remote_key(
            label, None if group in _REPEATING_GROUPS or follows_thumb else tap, icon=glyph,
            hint=blocked or t("manager.remote.control.help", label=label), size="tap")
        if blocked:
            key.disable()
        elif group in _REPEATING_GROUPS:
            key.props(f"data-repeat-control={control_id}")
        elif follows_thumb:
            key.props(f"data-hold-control={control_id}")

    by_group: dict[str, list[dict[str, Any]]] = {}
    for one in listed:
        by_group.setdefault(str(one.get("group") or ""), []).append(one)

    with ui.context.client.layout, frame.made().props("position=bottom") as sheet, \
            ui.card().classes("w-full remote-sheet remote-sheet--tall"):
        with ui.row().classes("remote-sheet-head w-full items-center no-wrap"):
            ui.label(str((controls.get("table") or {}).get("game_name")
                         or t("manager.remote.table_controls"))).classes("remote-headline truncate")
            panel.icon_action(t("word.close"), sheet.close, icon=verbs.CLOSE,
                              hint=t("word.close"))()
        if not press.get("available", True) and press.get("reason"):
            ui.label(key_simulator.words(press["reason"])).classes("remote-note")
        for group, held in by_group.items():
            if group not in _FOLDED_GROUPS:
                draw_group(group, held)
        folded = [(group, held) for group, held in by_group.items() if group in _FOLDED_GROUPS]
        if folded:
            with panel.disclosure(t("manager.remote.folded_controls")):
                for group, held in folded:
                    draw_group(group, held)
    state["controls_sheet"] = sheet
    sheet.open()


def _who(state: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """The roster and this phone's player, without who is up: what a header, a sheet and
    the rows read. A change in who is up alone is drawn by the section that shows it."""
    def bare(one: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in one.items() if key != "up"}

    identity = state.get("identity")
    return ([bare(one) for one in state.get("players") or []],
            bare(identity) if identity is not None else None)


def _rating_of(game_id: str, game: dict[str, Any], identity: dict[str, Any] | None,
               record: dict[str, dict[str, Any]]) -> int:
    """The stars to show."""
    if is_someone_else(identity):
        return int((record.get(game_id) or {}).get("rating") or 0)
    return int((game.get("user") or {}).get("rating") or 0)


async def _rate(client: ApiClient, identity: dict[str, Any] | None,
                game_id: str, value: int) -> None:
    """Writes the rating `_rating_of` reads back."""
    if identity is not None and is_someone_else(identity):
        await run.io_bound(client.player_rating, str(identity["id"]), game_id, value)
    else:
        await run.io_bound(client.rate, game_id, value)


def _rating_stars(state: dict[str, Any], client_for_target: Callable[[], Any],
                  game_id: str, shown: int,
                  game: dict[str, Any] | None = None) -> Callable[[], None]:
    """The stars for one game on this phone, wherever they are drawn: lit at the tap, the
    write behind them, and what the screen holds updated once it is saved. `game` is the
    row the caller holds when that is not one of `state["games"]`."""
    identity = state.get("identity")

    async def save(value: int) -> None:
        await _rate(client_for_target(), identity, game_id, value)
        if is_someone_else(identity):
            state.setdefault("record", {}).setdefault(game_id, {})["rating"] = value
            return
        for held in (game, *(state.get("games") or [])):
            if held is not None and str(held.get("id") or "") == game_id:
                held.setdefault("user", {})["rating"] = value

    return stars.rated(shown, save)


def _up_section(state: dict[str, Any], client_for_target: Callable[[], Any]) -> None:
    """Who the next game counts for. Nothing to toggle with one player in the roster.
    Drawn as a region: who is up changes without anyone joining or leaving, and
    `state["refill_up"]` draws it again where it stands."""
    @on_page
    async def toggle(player_id: str, up: bool) -> None:
        try:
            state["players"] = await run.io_bound(
                client_for_target().set_player_up, player_id, up)
        except Exception as exc:
            ui.notify(t("said.save_failed"), caption=why(exc), type="negative")
        drawn.refill()

    def flip(player_id: str) -> Callable[[Any], Any]:
        return lambda e: toggle(player_id, bool(e.value))

    def draw() -> None:
        players = state.get("players") or []
        if len(players) <= 1:
            return
        identity = state.get("identity")
        if identity is not None:
            mine = next((one for one in players if one.get("id") == identity.get("id")),
                        identity)
            if not mine.get("up"):
                with ui.column().classes("w-full gap-2 manager-card"):
                    panel.remote_action(t("manager.remote.im_up"),
                                        lambda: toggle(str(mine["id"]), True),
                                        icon=verbs.ACCEPT, primary=True,
                                        hint=t("manager.remote.im_up.help"))
                return
            if identity.get("guest"):
                with ui.row().classes("w-full items-center justify-between manager-card"):
                    ui.label(shown_name(mine)).classes("remote-headline")
                    panel.switch(True, flip(str(mine["id"])))()
                return

        with ui.column().classes("w-full gap-0 manager-card"):
            ui.label(t("manager.players.up")).classes("manager-card-title")
            for player in players:
                with ui.row().classes("w-full items-center justify-between remote-up-row"):
                    with ui.row().classes("items-center gap-2 no-wrap min-w-0"):
                        _avatar(state, player)
                        ui.label(shown_name(player)).classes("remote-row-name")
                    panel.switch(bool(player.get("up")), flip(str(player["id"])))()

    drawn = panel.region(draw)
    state["refill_up"] = drawn.refill
    drawn()


def _thumb(state: dict[str, Any], game_id: str) -> None:
    address = table_picture(state, game_id, art.CELL)
    with ui.element("span").classes("remote-thumb"):
        if address:
            ui.html(f'<img src="{html.escape(address)}" alt="" draggable="false" '
                    'onerror="this.remove()">', sanitize=False, tag="span") \
                .classes("remote-thumb-art")


def _when(stamp: str) -> str:
    """How long ago, in the one unit that reads at a glance; nothing where it cannot say."""
    seconds = _elapsed_seconds(stamp)
    if seconds is None:
        return ""
    if seconds < 60:
        return t("manager.remote.just_now")
    if seconds < 3600:
        return t("manager.remote.minutes_ago", count=seconds // 60)
    if seconds < 86400:
        return t("manager.remote.hours_ago", count=seconds // 3600)
    return t("manager.remote.days_ago", count=seconds // 86400)


def _idle(state: dict[str, Any], client_for_target: Callable[[], Any],
          redraw: Callable[[], None]) -> None:
    """Nothing is playing, so this offers what is worth doing about that.

    The last game played, with its rating: the moment somebody has an opinion about a table
    and the phone is already in their hand, and the way back into it. Superseded by the
    result card the moment a play is recorded this session - this is what is left to say
    before that has happened even once.
    """
    game = last_played(state.get("games") or [])
    if not game:
        with ui.column().classes("w-full gap-1 manager-card"):
            ui.label(t("manager.remote.nothing_playing")).classes("remote-headline")
        return
    identity = state.get("identity")
    game_id = str(game["id"])

    @on_page
    async def show_on_wheel() -> None:
        try:
            await run.io_bound(client_for_target().move_wheel, game_id)
        except Exception as exc:
            ui.notify(t("manager.remote.wheel_failed"), caption=why(exc), type="negative",
                      position="top")
            return
        state["screen"] = WHEEL
        redraw()

    with ui.column().classes("w-full gap-3 manager-card"):
        ui.label(t("word.last_played")).classes("manager-card-title")
        with ui.row().classes("w-full items-center gap-3 no-wrap"):
            _thumb(state, game_id)
            with ui.column().classes("grow min-w-0 gap-0"):
                ui.label(str(game.get("name") or "")).classes("remote-headline")
                line = " \u00b7 ".join(part for part in (
                    game_tables.made(game),
                    _when(str((game.get("user") or {}).get("last_played") or ""))) if part)
                if line:
                    ui.label(line).classes("remote-note")
        with ui.row().classes("w-full items-center justify-between no-wrap"):
            ui.label(t("manager.remote.your_rating")).classes("remote-note")
            _rating_stars(state, client_for_target, game_id,
                          _rating_of(game_id, game, identity, state.get("record") or {}),
                          game)()
        with ui.row().classes("w-full gap-2 no-wrap"):
            _launch_button(game, state, client_for_target, redraw,
                           label=t("manager.remote.play_again"),
                           cls="remote-action remote-action--half"
                           if mirroring(state.get("frontend")) else "remote-action")
            if mirroring(state.get("frontend")):
                panel.remote_action(t("manager.remote.on_wheel"), show_on_wheel,
                                    icon=WHEEL_GLYPH,
                                    hint=t("manager.remote.on_wheel.help")) \
                    .classes("remote-action--half")


def _visible_up(identity: dict[str, Any] | None,
                up: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Which of who was up belongs on this phone's result card: everyone for
    nobody said or the owner, theirs first for a kept player, theirs alone for a guest -
    and nothing for a kept player who was not part of this play, the same reason a guest
    sees nothing of somebody else's."""
    if identity is None:
        return list(up)
    player_id = str(identity.get("id") or "")
    mine = next((one for one in up if str(one.get("id") or "") == player_id), None)
    if mine is None:
        return []
    if identity.get("guest"):
        return [mine]
    return [mine, *(one for one in up if str(one.get("id") or "") != player_id)]


def _send_status(account: dict[str, Any], *, credited: bool, several_up: bool) -> str:
    """Empty for an account that is not sharing."""
    if not account.get("share"):
        return ""
    service = str(account.get("label") or account.get("extension") or "")
    if several_up and not credited:
        return t("manager.remote.solo_only", service=service)
    if account.get("waiting"):
        return t("manager.remote.send_pending")
    return t("manager.remote.sent_to", service=service)


async def _play_recorded(state: dict[str, Any], payload: dict[str, Any],
                         client_for_target: Callable[[], Any],
                         redraw: Callable[[], None]) -> None:
    """`table.play_recorded`: build the result card for whichever of who was up belongs
    on this phone, and show it the moment Now is open - or the moment it is next opened,
    since it replaces the idle card until superseded by the next one."""
    up = list(payload.get("up") or [])
    visible = _visible_up(state.get("identity"), up)
    if not visible:
        return
    credited = {str((one.get("player") or {}).get("id") or "")
               for one in payload.get("new_entries") or []}
    entries_of = {str((one.get("player") or {}).get("id") or ""): one.get("entries") or []
                 for one in payload.get("new_entries") or []}
    private = bool(payload.get("private"))
    several = len(up) > 1
    rows = []
    for player in visible:
        player_id = str(player.get("id") or "")
        got = player_id in credited
        line = ""
        if got and entries_of.get(player_id):
            line = play_facts.score_lines(entries_of[player_id][0])[0]
        statuses: list[str] = []
        if not private:
            try:
                accounts = await offload.io(client_for_target().player_accounts, player_id)
            except Exception as exc:
                logger.info("remote: could not read %s's accounts: %s", player_id, exc)
                accounts = []
            statuses = [said for said in
                       (_send_status(one, credited=got, several_up=several)
                        for one in accounts) if said]
        rows.append({"player": player, "entry": line, "statuses": statuses})
    game = payload.get("game") or {}
    state["result"] = {"game_id": str(game.get("id") or ""),
                       "game_name": str(game.get("name") or ""), "players": rows}
    if state["screen"] == NOW:
        redraw()


def _result_card(state: dict[str, Any], client_for_target: Callable[[], Any]) -> None:
    """What became of the play just recorded, for each player the result card is
    showing - their new entry where they made one, and what each of their sharing
    accounts did with it. The phone's own row carries the rating control the idle card
    used to; nobody else's does; stars are always about the one screen they are on."""
    result = state.get("result") or {}
    rows = result.get("players") or []
    if not rows:
        return
    identity = state.get("identity")
    game_id = str(result.get("game_id") or "")
    if identity is None:
        mine = next((row for row in rows if row["player"].get("owner")), None)
    else:
        mine = next((row for row in rows
                    if str(row["player"].get("id") or "") == str(identity.get("id") or "")),
                    None)

    with ui.column().classes("w-full gap-2 manager-card"):
        ui.label(str(result.get("game_name") or "")).classes("manager-card-title")
        for row in rows:
            with ui.column().classes("w-full gap-0"):
                ui.label(shown_name(row["player"])).classes("remote-headline")
                if row.get("entry"):
                    ui.label(row["entry"]).classes("remote-note")
                for said in row.get("statuses") or []:
                    ui.label(said).classes("remote-note")
        if mine is not None:
            held: dict[str, Any] = next(
                (one for one in state.get("games") or []
                 if str(one.get("id") or "") == game_id), {})
            _rating_stars(state, client_for_target, game_id,
                          _rating_of(game_id, held, identity, state.get("record") or {}))()


def _running_jobs(state: dict[str, Any]) -> None:
    """Only what is still going. A finished job is not news on a screen this size, and
    a list that keeps yesterday's work is a list nobody reads."""
    for job in state.get("jobs") or []:
        if str(job.get("state") or "") != "running" or job.get("kind") == remote_record.KIND:
            continue
        with ui.column().classes("w-full gap-2 manager-card"):
            ui.label(t("word.running")).classes("manager-card-title")
            # The kind is a wire word - `library.scan` - and nothing on this surface
            # shows one. A percentage says more than the name does anyway, so the name
            # is the label and the bar is the answer.
            ui.label(str(job.get("title") or "")
                     or humanize(str(job.get("kind") or "").replace(".", " "))) \
                .classes("remote-headline")
            ui.linear_progress(value=float(job.get("pct") or 0) / 100,
                               show_value=False).props("rounded")
            if job.get("message"):
                ui.label(str(job["message"])).classes("remote-note")


# What the list will draw before it asks you to narrow it. A phone renders every row it
# is given, and a library is longer than a screen by design - the answer to a long list
# is typing into it, not scrolling it.
SHOWN_AT_ONCE = 40

# The Games tab lists the target's games or its tables, as a list or as tiles. The choice
# of tiles is the phone's, remembered per browser; a lens is for the visit.
GAMES_LENS, TABLES_LENS = "games", "tables"
LIST_VIEW, TILES_VIEW = "list", "tiles"
TILES_KEY = "remote.tiles"


def lens_of(state: dict[str, Any]) -> str:
    """The lens in force. A target with no tables to list reads as games whatever was
    chosen, because the switch back is drawn only where there are tables."""
    return TABLES_LENS if state.get("lens") == TABLES_LENS and state.get("files") \
        else GAMES_LENS


def matching(games: list[dict[str, Any]], said: str) -> list[dict[str, Any]]:
    """The games a typed word finds.

    Name, maker and year, because those are the three things somebody standing at a
    machine knows about it. Every word has to land somewhere, so "bally 1991" narrows
    rather than widening - which is what a person means by typing a second word.
    """
    words = said.lower().split()
    if not words:
        return games
    found = []
    for game in games:
        against = " ".join(str(game.get(key) or "")
                           for key in ("name", "manufacturer", "year")).lower()
        if all(word in against for word in words):
            found.append(game)
    return found


def offered_games(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    found = []
    for entry in entries:
        game = entry.get("game") or {}
        game_id = str(game.get("id") or "")
        if game_id and game_id not in seen:
            seen.add(game_id)
            found.append(game)
    return found


def frontend_collections(collections: list[dict[str, Any]], chosen: str = "") -> list[str]:
    return [str(one.get("name") or "") for one in collections
            if one.get("name") and (one.get("in_frontend", True) or one.get("name") == chosen)]


def in_collection(games: list[dict[str, Any]],
                  ids: set[str] | None) -> list[dict[str, Any]]:
    """Narrowed to one collection, or left alone where none is chosen."""
    return games if ids is None else [one for one in games if one.get("id") in ids]


def manual_collections(collections: list[dict[str, Any]]) -> list[str]:
    """The ones a game can simply be put in.

    A filter collection is a rule, and pinning a game against a rule is a curator's
    decision made with the rule in view. That is desk work, and the posture line falls
    where it falls everywhere else here.
    """
    return [str(one.get("name") or "") for one in collections
            if str(one.get("type") or "") == "manual" and one.get("name")]


def lens_items(state: dict[str, Any]) -> list[dict[str, Any]]:
    """What the Games tab lists before anything narrows it: the target's games, or its
    tables.

    A table is listed only where it can be started from here: its game is one the target
    offers, it is not hidden, and it is not known to be unlaunchable.
    """
    games = state.get("games") or []
    if lens_of(state) != TABLES_LENS:
        return games
    offered = {str(one.get("id") or "") for one in games}
    return [one for one in state.get("files") or []
            if str(one.get("game_id") or "") in offered and not one.get("hidden")
            and one.get("launchable") is not False]


def matching_files(files: list[dict[str, Any]], said: str) -> list[dict[str, Any]]:
    """The tables a typed word finds: the game's name, maker and year, and which table
    it is. Every word has to land somewhere, as `matching` has it."""
    words = said.lower().split()
    if not words:
        return files
    return [one for one in files
            if all(word in " ".join(str(part or "") for part in (
                one.get("game"), one.get("manufacturer"), one.get("year"),
                game_tables.table_name(one))).lower() for word in words)]


def narrowed_items(state: dict[str, Any], items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """`items` narrowed to what was typed and to the chosen collection."""
    said = state.get("find") or ""
    ids = state.get("collection_ids")
    if lens_of(state) != TABLES_LENS:
        return in_collection(matching(items, said), ids)
    found = matching_files(items, said)
    return found if ids is None else [one for one in found if one.get("game_id") in ids]


def _art_kind() -> str:
    return thumbnails.chosen(library_policy.kept_kinds()["media"])


def _art_of(state: dict[str, Any], item: dict[str, Any]) -> str | None:
    """The picture a list row shows, "" where it has none, None where the Media settings
    draw no pictures and a row has no frame at all."""
    art = state.get("art")
    if art is None:
        return None
    if lens_of(state) == TABLES_LENS:
        return on_target(state, thumbnails.address(item))
    return on_target(state, art.get(str(item.get("id") or ""), ""))


def _play(state: dict[str, Any],
          client_for_target: Callable[[], Any],
          redraw: Callable[[], None]) -> None:
    """Find a game and start it.

    The search field is first because the library is longer than a screen, and a list
    longer than a screen is typed into rather than scrolled.

    Where the frontend is up, the collection is the one it is showing, both ways.
    """
    chips: dict[str, Any] = {}

    # Not redraw(): a field rebuilt under the cursor drops the focus mid-word.
    async def typed(event: Any) -> None:
        state["find"] = str(event.value or "")
        listing.refill()

    @on_page
    async def narrow(name: str) -> None:
        # Taken before anything is awaited, so the frontend's own report of this switch
        # finds the phone already on it rather than switching it a second time.
        state["collection"] = name
        for one, chip in chips.items():
            chip.classes(add="remote-chip--on") if one == name \
                else chip.classes(remove="remote-chip--on")
        showing = state.get("frontend")
        if mirroring(showing) and name != str((showing or {}).get("collection") or ""):
            try:
                await run.io_bound(client_for_target().show_on_frontend, name)
            except Exception as exc:
                ui.notify(t("manager.remote.show_failed"), caption=why(exc),
                          type="negative")
        try:
            state.update(await offload.io(_narrowed_to, client_for_target(), name))
        except Exception as exc:
            state["collection_ids"] = None
            ui.notify(t("manager.remote.collection_failed"), caption=why(exc),
                      type="negative")
        listing.refill()

    def draw_picker() -> None:
        chips.clear()
        named = frontend_collections(state.get("collections") or [],
                                    state.get("collection") or "")
        if not named:
            return
        chosen = state.get("collection") or ""
        with ui.row().classes("w-full items-center gap-2 no-wrap remote-chips"):
            for name in ["", *named]:
                chips[name] = panel.chip(
                    name or t("manager.remote.all_games"), name == chosen,
                    lambda _e=None, name=name: narrow(name),
                    t("manager.remote.show_collection", name=name) if name
                    else t("manager.remote.show_all_games"))

    def draw_listing() -> None:
        items = lens_items(state)
        found = narrowed_items(state, items)
        if found and len(found) != len(items):
            ui.label(t("manager.remote.match_tables" if lens_of(state) == TABLES_LENS
                       else "manager.remote.match_games",
                       shown=len(found), count=len(items))).classes("remote-note px-3 pb-1")
        _entry_list(found, state, client_for_target, redraw)

    def draw_search() -> None:
        tables_lens = lens_of(state) == TABLES_LENS
        with ui.row().classes("w-full items-center gap-2 no-wrap"):
            ui.input(placeholder=t("manager.remote.search_tables" if tables_lens
                                   else "manager.remote.search_games",
                                   count=len(lens_items(state))),
                     value=state.get("find") or "", on_change=typed) \
                .props("dense outlined clearable inputmode=search").classes("grow")
            panel.segmented({LIST_VIEW: t("manager.remote.view_list"),
                             TILES_VIEW: t("manager.remote.view_tiles")},
                            TILES_VIEW if state.get("tiles") else LIST_VIEW, switch_view)

    searching = panel.region(draw_search)
    picker = panel.region(draw_picker)
    listing = panel.region(draw_listing)

    def relist() -> None:
        picker.refill()
        listing.refill()

    def switch_lens(event: Any) -> None:
        state["lens"] = str(event.value)
        searching.refill()
        relist()

    def switch_view(event: Any) -> None:
        state["tiles"] = event.value == TILES_VIEW
        remembered.put(TILES_KEY, state["tiles"])
        listing.refill()

    with ui.column().classes("w-full gap-2 p-3"):
        if state.get("files"):
            panel.segmented({GAMES_LENS: t("manager.section.games"),
                             TABLES_LENS: t("manager.section.tables")},
                            lens_of(state), switch_lens,
                            glyphs=(icons.GAMES, icons.TABLES)) \
                .props("spread").classes("w-full")
        searching()
        picker()
    with ui.column().classes("w-full gap-0"):
        listing()
    state["relist"] = relist


def _entry_list(found: list[dict[str, Any]], state: dict[str, Any],
                client_for_target: Callable[[], Any], redraw: Callable[[], None]) -> None:
    state["rows"] = {}
    if not found:
        return _nothing(t("manager.remote.nothing_name") if state.get("find")
                        else t("manager.remote.collection_empty"))
    tiles = bool(state.get("tiles"))
    with ui.column().classes("w-full gap-0") as shown:
        if state.get("look") is not None:
            state["look"].apply(shown)
        with ui.element("div").classes("remote-tiles w-full" if tiles else "w-full"):
            for item in found[:SHOWN_AT_ONCE]:
                _entry(item, state, client_for_target, redraw, tiles)
        left = len(found) - SHOWN_AT_ONCE
        if left > 0:
            # The count, not a "load more": what is wanted is one game, and typing two
            # more letters reaches it faster than paging to it does.
            ui.label(t("manager.remote.search_more", left=left)).classes("remote-note p-3")


def _entry(item: dict[str, Any], state: dict[str, Any], client_for_target: Callable[[], Any],
           redraw: Callable[[], None], tiles: bool) -> None:
    """One game, or one table, as a row or a tile. Where the frontend is up, a tap moves
    the wheel to the game; a tap on the one already there, or one the wheel refuses, opens
    it instead. The chevron always opens it.

    Never tap-to-launch. A mis-tap that opens a sheet costs a tap to undo; a mis-tap
    that starts a table takes the machine away from whoever is on it.
    """
    by_table = lens_of(state) == TABLES_LENS
    game = next((one for one in state.get("games") or []
                 if one.get("id") == item.get("game_id")), item) if by_table else item
    table = item if by_table else None
    game_id = str(game.get("id") or "")

    @on_page
    async def tapped(_event: Any = None) -> None:
        showing = state.get("frontend")
        if mirroring(showing) and game_id != wheel_id(showing):
            try:
                await run.io_bound(client_for_target().move_wheel, game_id)
                return
            except Exception as exc:
                logger.info("remote: the wheel did not move to %s: %s", game_id, exc)
        _game_sheet(game, state, client_for_target, redraw, table)

    def opened(_event: Any = None) -> None:
        _game_sheet(game, state, client_for_target, redraw, table)

    here = game_id == wheel_id(state.get("frontend"))
    picture = _art_of(state, item)
    glyph = icons.TABLES if by_table else icons.GAMES
    identity = state.get("identity")
    if by_table:
        rating = 0 if is_someone_else(identity) else int(item.get("rating") or 0)
        line = game_tables.table_name(item) or game_tables.made(game)
    else:
        rating = _rating_of(game_id, game, identity, state.get("record") or {})
        line = game_tables.made(game)
    name = str(game.get("name") or "")
    classes = ("remote-tile" if tiles else "w-full items-center gap-2 no-wrap remote-row") \
        + (" remote-row--here" if here else "")
    with (ui.column() if tiles else ui.row()).on("click", tapped).classes(classes) as row:
        if picture is not None:
            ui.html(thumbnails.frame_html(picture, glyph), sanitize=False, tag="span")
        with ui.row().classes("w-full items-center gap-1 no-wrap") if tiles \
                else nullcontext():
            with ui.column().classes("grow min-w-0 gap-0"):
                with ui.row().classes("w-full items-center gap-2 no-wrap min-w-0"):
                    ui.label(name).classes("remote-row-name truncate")
                    ui.label(t("manager.remote.row_on_wheel")).classes("remote-here-mark")
                if line:
                    ui.label(line).classes("remote-note truncate w-full")
                if rating:
                    stars.static(rating)
            if (game.get("user") or {}).get("favorite"):
                ui.icon("favorite").classes("remote-row-mark") \
                    .tooltip(t("word.favorite")).props(
                        f'aria-label="{html.escape(t("word.favorite"))}"')
            panel.open_row(t("manager.remote.open_game"), opened)
    state["rows"].setdefault(game_id, []).append(row)


def _mark_here(rows: dict[str, list[Any]], was: str, now: str) -> None:
    """Move the wheel's mark between rows without rebuilding the list under a thumb."""
    if was != now:
        for one in rows.get(was, []):
            one.classes(remove="remote-row--here")
    for one in rows.get(now, []):
        one.classes(add="remote-row--here")


def _game_sheet(game: dict[str, Any], state: dict[str, Any], client_for_target: Callable[[], Any],
                redraw: Callable[[], None], table: dict[str, Any] | None = None) -> None:
    """One game, everything that can be done to it from here, and Launch at the foot.
    From the Tables lens, `table` is the file the sheet was opened for, and the Launch
    starts that one.

    A sheet from the bottom rather than a screen of its own: what is being decided is
    about the row you just touched, and pushing a screen would take the list away to
    answer a question about one line of it.
    """
    with frame.made().props("position=bottom") as sheet, \
            ui.card().classes("w-full remote-sheet"):
        ui.label(str(game.get("name") or "")).classes("remote-headline")
        made = game_tables.made(game)
        if made:
            ui.label(made).classes("remote-note")
        if table is not None and game_tables.table_name(table):
            ui.label(game_tables.table_name(table)).classes("remote-note")
        lacks = ui.label("").classes("remote-note")
        lacks.set_visibility(False)
        identity = state.get("identity")
        someone_else = is_someone_else(identity)

        @on_page
        async def write(call: Any, *args: Any) -> bool:
            try:
                await run.io_bound(call, *args)
            except Exception as exc:
                ui.notify(t("said.save_failed"), caption=why(exc), type="negative")
                return False
            return True

        _rating_stars(state, client_for_target, str(game["id"]),
                      _rating_of(str(game["id"]), game, identity, state.get("record") or {}),
                      game)()

        if not someone_else:
            held = bool((game.get("user") or {}).get("favorite"))

            async def favor() -> None:
                if await write(client_for_target().set_favorite, game["id"], not held):
                    game.setdefault("user", {})["favorite"] = not held
                    sheet.close()
                    redraw()

            ui.button(t("word.favorite") if not held else t("manager.remote.remove_favorite"),
                      icon="favorite" if not held else "favorite_border",
                      on_click=favor) \
                .props("no-caps flat").classes("remote-action")

        if not (identity is not None and identity.get("guest")) and not acting.play_only():
            _add_to_collection(game, state, sheet, write, client_for_target)

        async def started() -> None:
            sheet.close()
            state["screen"] = NOW
            state["run"] = await offload.io(client_for_target().capture_run)
            redraw()

        remote_record.sheet_entry(game, state, client_for_target, lacks, started)
        _launch_button(game, state, client_for_target, redraw,
                       cls="remote-action remote-action--primary", then=sheet.close,
                       file=game_tables.native_key(table) if table else "")
    sheet.open()


def _launch_button(game: dict[str, Any], state: dict[str, Any],
                   client_for_target: Callable[[], Any], redraw: Callable[[], None], *,
                   cls: str, then: Callable[[], Any] = lambda: None, label: str = "",
                   file: str = "") -> None:
    """Launch, at the foot of the sheet and under the game on the wheel alike. Once it
    has started, Now is where the table is quit from. `file` picks one of the game's
    tables, and the default is what is started without it."""
    @on_page
    async def launch() -> None:
        try:
            await run.io_bound(client_for_target().launch, game["id"], file)
        except Exception as exc:
            ui.notify(t("manager.remote.launch_failed"), caption=why(exc), type="negative")
            return
        then()
        state["screen"] = NOW
        state["play"] = await offload.io(client_for_target().play_state)
        redraw()

    ui.button(label or t("manager.remote.launch"), icon="play_arrow", on_click=launch) \
        .props("no-caps unelevated color=primary").classes(cls)


def _add_to_collection(game: dict[str, Any], state: dict[str, Any], sheet: Any,
                       write: Any, client_for_target: Callable[[], Any]) -> None:
    """Put it in a list you keep.

    Shown disabled with the reason rather than hidden when there is nowhere to put it:
    an action that vanishes leaves somebody wondering whether this surface can do it at
    all, and the answer is that it can once there is a list to add to.
    """
    named = manual_collections(state.get("collections") or [])
    if not named:
        ui.button(t("manager.remote.add_collection"), icon=verbs.ADD_TO_LIST) \
            .props("no-caps flat disable").classes("remote-action") \
            .tooltip(t("manager.remote.manual_empty"))
        return

    @on_page
    async def add(name: str) -> None:
        if await write(client_for_target().add_to_collection, name, game["id"]):
            ui.notify(t("manager.remote.added", name=(name)), type="positive")
            sheet.close()

    with ui.button(t("manager.remote.add_collection"), icon=verbs.ADD_TO_LIST) \
            .props("no-caps flat").classes("remote-action"):
        with ui.menu():
            for name in named:
                ui.menu_item(name, on_click=lambda _e=None, name=name: add(name)) \
                    .classes("manager-menu-item")


# What each button asks for, in the words a person would use rather than the vocabulary
# the wire carries. `collection_menu` is the wheel's list of collections; on screen it is
# what that list is called.
BUTTON_WORDS = {
    "select": t("manager.remote.select"),
    "back": t("word.back"),
    "take_picture": t("input.take_picture.label"),
    "menu": t("manager.remote.menu"),
    "collection_menu": t("manager.remote.collections"),
}

# How often a held button says it is still held. A third of the time to live, so a
# renewal has to be lost twice running before the install lets go.
RENEW_MS = 500
# How often a control held down is pressed again, for the ones that take repeated presses.
REPEAT_MS = 200

# Press and release from a thumb, and the renewal in between.
#
# Client-side because a hold is a gesture, not a request: the browser is what knows the
# thumb is still down, and a server that had to infer it from the last message would be
# guessing at exactly the moment a wheel is moving. What it sends is what the seam
# expects - one press, renewed, then one release.
_HOLD_SCRIPT = """
if (!window.__vpinRemoteHold) {
  window.__vpinRemoteHold = true;
  const held = new Map();
  const letGo = (action) => {
    const timer = held.get(action);
    if (timer === undefined) return;
    clearInterval(timer);
    held.delete(action);
    emitEvent('remote_release', {action});
  };
  const takeHold = (action) => {
    if (held.has(action)) return;
    emitEvent('remote_press', {action});
    held.set(action, setInterval(() => emitEvent('remote_press', {action}), %(renew)d));
  };
  const actionAt = (target) => {
    const el = target && target.closest ? target.closest('[data-hold-action]') : null;
    return el ? el.dataset.holdAction : '';
  };
  // A table control held for as long as the thumb is on it: down, renewed, then up.
  const kept = new Map();
  const dropKept = (id) => {
    const one = kept.get(id);
    if (one === undefined) return;
    clearInterval(one.timer);
    kept.delete(id);
    emitEvent('remote_control_up', {id});
  };
  const keep = (id, pointer) => {
    if (kept.has(id)) return;
    emitEvent('remote_control_down', {id});
    // A key whose page has been redrawn under the thumb never hears the thumb lift, so
    // each renewal checks that it is still there.
    const timer = setInterval(() => {
      if (!document.querySelector('[data-hold-control="' + id + '"]')) { dropKept(id); return; }
      emitEvent('remote_control_down', {id});
    }, %(renew)d);
    kept.set(id, {timer, pointer});
  };
  const keptAt = (target) => {
    const el = target && target.closest ? target.closest('[data-hold-control]') : null;
    return el ? el.dataset.holdControl : '';
  };
  const repeating = new Map();
  const stopRepeat = (id) => {
    const timer = repeating.get(id);
    if (timer === undefined) return;
    clearInterval(timer);
    repeating.delete(id);
  };
  const startRepeat = (id) => {
    if (repeating.has(id)) return;
    emitEvent('remote_control', {id});
    repeating.set(id, setInterval(() => emitEvent('remote_control', {id}), %(repeat)d));
  };
  const controlAt = (target) => {
    const el = target && target.closest ? target.closest('[data-repeat-control]') : null;
    return el ? el.dataset.repeatControl : '';
  };
  document.addEventListener('pointerdown', (e) => {
    const action = actionAt(e.target);
    if (action) { e.preventDefault(); takeHold(action); }
    const control = controlAt(e.target);
    if (control && e.button === 0) { e.preventDefault(); startRepeat(control); }
    const key = keptAt(e.target);
    if (key && e.button === 0) { e.preventDefault(); keep(key, e.pointerId); }
  });
  // Every way a thumb can stop being on the button, including sliding off it and the
  // browser taking the pointer away for a scroll. A release that is never sent is the
  // failure the install's own expiry exists to catch, and this is the half that keeps
  // it from happening in the first place.
  for (const name of ['pointerup', 'pointercancel', 'pointerleave']) {
    document.addEventListener(name, (e) => {
      const action = actionAt(e.target);
      if (action) letGo(action);
      const id = keptAt(e.target);
      if (id) dropKept(id);
    });
  }
  // A mouse released off the key, or a touch the browser took away, lets go of the key
  // that pointer was holding and no other: two thumbs hold two flippers.
  for (const name of ['pointerup', 'pointercancel']) {
    document.addEventListener(name, (e) => [...kept.entries()]
      .filter(([, one]) => one.pointer === e.pointerId)
      .forEach(([id]) => dropKept(id)));
  }
  // A repeating key stops on any release, wherever the pointer ended up: a mouse dragged
  // off the key lets go over something else, and `pointerleave` does not bubble.
  for (const name of ['pointerup', 'pointercancel']) {
    document.addEventListener(name, () => [...repeating.keys()].forEach(stopRepeat));
  }
  // The page going away with a thumb down: a phone locking its screen is the common
  // one, and it is why the press expires at the other end as well.
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) {
      [...held.keys()].forEach(letGo);
      [...repeating.keys()].forEach(stopRepeat);
      [...kept.keys()].forEach(dropKept);
    }
  });
  window.addEventListener('blur', () => {
    [...held.keys()].forEach(letGo);
    [...repeating.keys()].forEach(stopRepeat);
    [...kept.keys()].forEach(dropKept);
  });
}
"""


# Counts every `data-elapsed` label up from the seconds it was drawn with.
_CLOCK_SCRIPT = """
if (!window.__vpinRemoteClock) {
  window.__vpinRemoteClock = true;
  const two = (n) => String(n).padStart(2, '0');
  const tick = () => document.querySelectorAll('[data-elapsed]').forEach((el) => {
    if (!el.dataset.seen) el.dataset.seen = String(Date.now());
    const n = Number(el.dataset.elapsed)
      + Math.floor((Date.now() - Number(el.dataset.seen)) / 1000);
    el.textContent = two(Math.floor(n / 3600)) + ':' + two(Math.floor(n % 3600 / 60))
      + ':' + two(n % 60);
  });
  setInterval(tick, 250);
}
"""


def _heart(state: dict[str, Any], game: dict[str, Any], client_for_target: Callable[[], Any],
           refill: Callable[[], None]) -> Callable[[], None] | None:
    """The Favorite control at the hero's corner, or None for a phone answering as someone
    else: a favorite is the owner's."""
    if is_someone_else(state.get("identity")):
        return None
    game_id = str(game.get("id") or "")
    held = bool((game.get("user") or {}).get("favorite"))

    @on_page
    async def favor() -> None:
        try:
            await run.io_bound(client_for_target().set_favorite, game_id, not held)
        except Exception as exc:
            ui.notify(t("said.save_failed"), caption=why(exc), type="negative",
                      position="top")
            return
        game.setdefault("user", {})["favorite"] = not held
        refill()

    def draw() -> None:
        panel.icon_only("favorite" if held else "favorite_border", favor,
                        hint=t("manager.remote.remove_favorite" if held else "word.favorite")) \
            .classes("remote-hero-heart" + (" remote-hero-heart--on" if held else ""))

    return draw


def _wheel(state: dict[str, Any],
           client_for_target: Callable[[], Any],
           redraw: Callable[[], None]) -> None:
    """Drive the frontend from here.

    Not the table. In-game input is VPX's own and reaches it as keystrokes; what these
    keys produce is an action on the install's bus, addressed at the machine the header
    names. Keeping the two apart is what stops a press meaning one thing on the wheel
    and another inside a game.

    The game the frontend is showing is the hero, with Launch under it, and it follows the
    frontend's own changes without the keys below it being drawn again.
    """
    target = state["target"]
    if str(target.get("kind") or "") == device_registry.KIND_VPX_MOBILE:
        # Said rather than shown as dead keys: this is a real target and it really
        # can be played on, which is a different thing from being driveable.
        return _nothing(t("manager.remote.cannot_drive",
                target_name=(target_name(target))))

    play = state.get("play") or {}
    if play.get("launching"):
        # Honest, and not a guess: the frontend stops listening for the length of a
        # launch, so every key here would do nothing and report success.
        return _playing_instead(play, state, client_for_target, redraw)
    if frontend_closed(state.get("frontend")):
        # The same reason: a press goes to the windows alone, so with none up every
        # key here reports success and nothing hears it.
        return _closed_wheel(state, client_for_target, redraw)

    def draw_head() -> None:
        game = on_the_wheel(state.get("frontend"), state.get("games") or [],
                            state["strays"])
        if not game:
            ui.label(t("manager.remote.wheel_empty")).classes("remote-empty text-center")
            return
        game_id = str(game["id"])

        def open_sheet() -> None:
            _game_sheet(game, state, client_for_target, redraw)

        _hero(state, game_id, str(game.get("name") or ""), game_tables.made(game),
              badge=t("manager.remote.wheel_title"), open_sheet=open_sheet,
              heart=_heart(state, game, client_for_target, lambda: head.refill()))
        with ui.row().classes("w-full justify-center"):
            _rating_stars(state, client_for_target, game_id,
                          _rating_of(game_id, game, state.get("identity"),
                                     state.get("record") or {}),
                          game)()
        _launch_button(game, state, client_for_target, redraw,
                       cls="remote-action remote-action--primary")

    head = panel.region(draw_head)
    state["refill_wheel"] = head.refill
    with ui.column().classes("w-full gap-3 p-3"):
        head()
        _keys(client_for_target)


def _closed_wheel(state: dict[str, Any], client_for_target: Callable[[], Any],
                  redraw: Callable[[], None]) -> None:
    """No frontend to drive, and the way to what still works without one."""
    def open_games() -> None:
        state["screen"] = GAMES
        redraw()

    with ui.column().classes("w-full items-center justify-center grow gap-3 p-6"):
        ui.label(t("manager.remote.frontend_closed")).classes("remote-empty text-center")
        _open_frontend_offer(state, client_for_target)
        panel.remote_action(t("manager.remote.open_games"), open_games, icon=icons.GAMES,
                            hint=t("manager.remote.open_games.help"))


def _playing_instead(play: dict[str, Any], state: dict[str, Any],
                     client_for_target: Callable[[], Any],
                     redraw: Callable[[], None]) -> None:
    with ui.column().classes("w-full gap-3 p-3"):
        _playing(play, state, client_for_target, redraw,
                 note=t("manager.remote.wheel_busy"))


def _keys(client_for_target: Callable[[], Any]) -> None:
    """The wheel's moves, laid out as they act: a page either way over a step either way,
    with Select between the steps. The four that move are held keys, so a thumb that stays
    down keeps the wheel going - the curve for that lives in the install, so it feels the
    same from a thumb as it does from a flipper or a key."""
    with ui.column().classes("w-full gap-2 remote-keys"):
        with ui.element("div").classes("remote-keys-pages"):
            panel.remote_key(t("manager.remote.page_back"), icon=verbs.PAGE_EARLIER,
                             hint=t("manager.remote.page_back.help"), size="page",
                             hold="page_previous")
            panel.remote_key(t("manager.remote.page_forward"), icon=verbs.PAGE_LATER,
                             hint=t("manager.remote.page_forward.help"), size="page",
                             hold="page_next", icon_after=True)
        with ui.element("div").classes("remote-keys-steps"):
            panel.remote_key(t("input.previous.label"), icon=verbs.EARLIER,
                             hint=t("manager.remote.previous.help"), hold="previous")
            _tap_button("select", client_for_target, icon=verbs.PICK,
                        hint=t("manager.remote.select.help"), size="select")
            panel.remote_key(t("input.next.label"), icon=verbs.LATER,
                             hint=t("manager.remote.next.help"), hold="next")
        ui.label(t("manager.remote.hold_hint")).classes("remote-hint")
    with ui.element("div").classes("remote-keys-tap"):
        _tap_button("back", client_for_target, icon=verbs.BACK,
                    hint=t("manager.remote.back.help"))
        _tap_button("menu", client_for_target, icon=verbs.OPEN_MENU,
                    hint=t("manager.remote.menu.help"))
        _tap_button("collection_menu", client_for_target, icon=verbs.OPEN_COLLECTIONS,
                    hint=t("manager.remote.collections.help"))


def _tap_button(action: str, client_for_target: Callable[[], Any], *, icon: str, hint: str,
                size: str = "tap") -> None:
    async def tap() -> None:
        await _say(client_for_target, action, "tap")

    panel.remote_key(BUTTON_WORDS.get(action, action), tap, icon=icon, hint=hint, size=size)


async def _say(client_for_target: Callable[[], Any], action: str, phase: str) -> None:
    """One press, one renewal or one release, at whichever machine is aimed at.

    A failure is reported once and the gesture is abandoned rather than retried: a
    renewal that cannot be delivered means the target has gone, and the install lets go
    on its own the moment the renewals stop.
    """
    if not action:
        return
    try:
        await run.io_bound(client_for_target().press_input, action, phase,
                           ttl_ms=RENEW_MS * 3)
    except Exception as exc:
        logger.info("remote: %s %s did not reach the target: %s", action, phase, exc)


def _join(state: dict[str, Any], client_for_target: Callable[[], Any],
         redraw: Callable[[], None]) -> None:
    """The Join screen: Use My Card, read as 2.x reads it, or Just Initials."""

    @on_page
    async def joined(said: dict[str, Any]) -> None:
        player = dict(said or {})
        remember_identity(state["target"], str(player.get("id") or ""))
        ui.notify(t("manager.remote.youre_up", initials=str(player.get("initials") or "")),
                  type="positive")
        state["screen"] = NOW
        await state["reread"]()
        redraw()

    @on_page
    async def with_card(event: Any) -> None:
        text = (await event.file.read()).decode("utf-8", "replace")
        try:
            said = await run.io_bound(client_for_target().add_guest_from_card, text)
        except Exception as exc:
            ui.notify(t("manager.remote.join_failed"), caption=why(exc), type="negative")
            return
        await joined(said or {})

    @on_page
    async def just_initials() -> None:
        with _asked(t("manager.players.just_initials")) as box:
            control = frame.field(placeholder=t("manager.players.initials_example"))
            control.props("maxlength=3 bottom-slots")

            @on_page
            async def go() -> None:
                try:
                    said = await run.io_bound(client_for_target().add_guest,
                                              str(control.value or "").strip())
                except Exception as exc:
                    control.props["error"] = True
                    control.props["error-message"] = why(exc)
                    control.update()
                    return
                box.submit(said)

            with frame.footer():
                frame.cancel(lambda: box.submit(None))
                answered = frame.answer(t("manager.remote.join"), go, icon=verbs.ACCEPT)
        frame.focus(box, control)
        frame.enter_presses(answered)
        said: dict[str, Any] | None = await box
        if said is not None:
            await joined(said)

    with ui.column().classes("w-full gap-4 p-4 items-center text-center"):
        ui.label(t("manager.remote.play_target",
                   target=target_name(state["target"]))).classes("remote-headline")
        uploader = ui.upload(on_upload=with_card, auto_upload=True).classes("hidden")
        uploader.props(f'accept="{",".join(CARD_FILES)}"')
        uploader.on("finish",
                   js_handler=f"() => getElement({uploader.id}).$refs.qRef.reset()")
        card_button = panel.remote_action(t("manager.remote.use_card"), icon=verbs.FROM_FILE,
                                          primary=True,
                                          hint=t("manager.remote.use_card.help"))
        card_button.on("click",
                       js_handler=f"() => getElement({uploader.id}).$refs.qRef.pickFiles()")
        panel.remote_action(t("manager.players.just_initials"), just_initials,
                            icon=verbs.INITIALS_ONLY,
                            hint=t("manager.players.just_initials.help"))
        ui.label(t("manager.remote.guest_until_close")).classes("remote-note")


def where_to_find_it() -> str:
    """What to read out to somebody holding a phone: the device's name once it has
    answered, its IP address until then. Never loopback; empty while this install answers
    only itself."""
    return _with_pass(lambda port: addresses.best(port, "/remote"))


def where_to_scan() -> str:
    """The same address as a QR code carries it: the IP address, which a phone resolves
    without help where it may or may not resolve the name."""
    return _with_pass(lambda port: addresses.to_scan(addresses.urls(port, "/remote")))


def _with_pass(choose: Callable[[int], str]) -> str:
    from common.config.paths import get_ini_config
    from common.security import access_keys

    network = serving.network(get_ini_config())
    if not serving.reachable_from_elsewhere(network.http_bind):
        return ""
    address = choose(network.http_port)
    if not address:
        return ""
    return f"{address}?{access_keys.PASS_PARAM}={access_keys.remote_pass()[1]}"


def invite(labels: list) -> None:
    """The way somebody finds this surface at all.

    In the Manager, because that is where a person configuring an install already is -
    and a phone is exactly the device that will not have found `/remote` by typing. It
    does not reach somebody who never opens the Manager on any device; that half is the
    frontend's, and is not built.

    The label joins `labels` so it collapses with the rail and leaves the icon, which is
    the rule every other entry in this drawer follows.
    """
    said = where_to_find_it()
    if not said:
        return

    @on_page
    async def renew(sheet: Any) -> None:
        if not await confirm.ask(
                t("manager.remote.new_code_question"),
                detail=t("manager.remote.new_code_detail"),
                confirm=t("manager.remote.new_code"), icon=verbs.REFRESH, danger=False):
            return
        ended = await run.io_bound(ApiClient().renew_remote_code)
        sheet.close()
        ui.notify(t("manager.remote.new_code_made", count=ended or 0), type="positive")
        show()

    def show() -> None:
        waiting = addresses.resolving()
        with frame.made() as sheet, ui.card().classes("manager-confirm items-center"):
            ui.label(t("manager.remote.sheet_title")).classes("manager-confirm-title")
            art = qr.svg(where_to_scan())
            if art:
                ui.html(art).classes("manager-qr")
            # Always, not only when the drawing failed: a camera is not the only way
            # somebody gets this, and an address nobody can read out is one they cannot
            # type either.
            readable = ui.label(where_to_find_it()).classes("manager-help text-center")
            if waiting:
                def name_arrived() -> None:
                    readable.set_text(where_to_find_it())
                    if not addresses.resolving():
                        arrival.deactivate()

                arrival = ui.timer(_NAME_POLL_SECONDS, name_arrived)
            with ui.row().classes("justify-center w-full"):
                panel.action(t("manager.remote.new_code"), lambda: renew(sheet),
                             icon=verbs.REFRESH, hint=t("manager.remote.new_code_hint"))()
                ui.button(t("word.close"), icon=verbs.CLOSE, on_click=sheet.close) \
                    .props("flat no-caps").classes("manager-action")
        sheet.open()

    with ui.row().classes("items-center justify-center gap-2 w-full no-wrap "
                          "manager-invite").on("click", show):
        ui.icon("qr_code_2").classes("shrink-0")
        labels.append(ui.label(t("manager.remote.mobile_device")).classes("text-xs"))
