"""Reads this install over its own HTTP API."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
from collections.abc import Iterable, Iterator
from itertools import takewhile
from typing import Any
from urllib.parse import quote, urlencode, urlsplit

import requests
from requests.adapters import HTTPAdapter

from common.config.paths import get_ini_config
from common.devices import device_client, device_registry, launcher_wire, serving
from common.i18n import t
from common.security import http_client, pinned_certificates, sign_in
from httpapi import auth, scopes
from manager.kit import acting

logger = logging.getLogger("vpinfe.manager.kit.api")

_TIMEOUT = 15
# Reads that cover the whole library. The first one scans it, and on a network share that
# has gone idle the scan outlasts _TIMEOUT. Reads of one thing keep _TIMEOUT.
_LIBRARY_TIMEOUT = 120
# An import copies files, which is disk work rather than a question - a pup pack is
# gigabytes and the default would give up on it partway through. Sending a file's
# bytes shares it.
_IMPORT_TIMEOUT = 900
# A step of an extension's action, which reads whatever it is pointed at: a library on a
# network share is minutes before it has answered. The action reports how far it has got
# on its own progress route, so a long wait is watched rather than assumed to have hung.
STEP_TIMEOUT = _IMPORT_TIMEOUT
# Three of the event stream's 15-second keepalives. An install that goes away without
# closing the connection is otherwise waited on forever.
_FOLLOW_TIMEOUT = 45
# The program gets 30 seconds to write its file and exit; the answer comes after that.
_MAKE_FILE_TIMEOUT = 45


def local_base_url() -> str:
    """This install's own API, always.

    Following the library setting would run the whole Manager against another machine for
    an install reading somebody else's catalog - settings included, which is exactly what
    configuration must not do. Nothing is lost by pinning it: the nav is
    derived from features, so an install with no library of its own has no library
    sections for a remote catalog to fill.
    """
    return f"http://127.0.0.1:{serving.own_port(get_ini_config())}"


class ApiError(RuntimeError):
    """What the API said went wrong, in its own words - which is what a surface shows.

    `details` is the error's own payload where it sent one. A refusal names which
    settings it declined and why, and a surface that only had the sentence would have to
    parse it back out of prose.
    """

    def __init__(self, message: str, details: dict | None = None) -> None:
        super().__init__(message)
        self.details = dict(details or {})


def _is_this_install(url: str) -> bool:
    return urlsplit(url).hostname == "127.0.0.1"


def _wants_confirm(response: requests.Response) -> bool:
    if response.status_code != 403:
        return False
    try:
        found = (response.json() or {}).get("error") or {}
    except ValueError:
        return False
    return (found.get("details") or {}).get("reason") == auth.CONFIRM_NEEDED


class _WordedSession(requests.Session):
    """Raises ApiError, never a requests exception, for a request nothing answered.

    A call made for a browser on another device says so, and is held to that browser's
    tier. When that tier asks for a Confirm, the browser is asked and the call is made once
    more.
    """

    def __init__(self) -> None:
        super().__init__()
        # One pooled connection per worker thread that can be calling at once.
        adapter = HTTPAdapter(pool_maxsize=acting.WORKERS)
        self.mount("http://", adapter)
        self.mount("https://", adapter)

    def _send(self, who: acting.Acting | None, method: str, url: str,
              **kwargs: Any) -> requests.Response:
        if who is not None:
            signed = auth.sign_acting(who.session_id, who.encrypted)
            kwargs["headers"] = {**(kwargs.get("headers") or {}), auth.ACTING_HEADER: signed}
        http_client.require_reachable(url)
        return pinned_certificates.sending(super().request, url)(method, url, **kwargs)

    @staticmethod
    def _paired(method: str, url: str, kwargs: dict[str, Any]) -> dict[str, Any]:
        """A call to another install carries the token this install paired with it, which is
        Full Control. Made for a Play sign-in it is held to Play here, and for a browser that
        is no longer signed in it carries nothing."""
        paired = device_registry.paired_header(url)
        if paired is None:
            return kwargs
        who = acting.current()
        if who is not None:
            session = sign_in.instance().session(who.session_id)
            if session is None:
                return kwargs
            if session.kind == sign_in.REMOTE:
                here = urlsplit(url).path.removeprefix("/api/v1")
                if auth.request_scope(method, here) not in scopes.PLAY:
                    raise ApiError(t("error.auth.protected"))
        return {**kwargs, "headers": {**(kwargs.get("headers") or {}), **paired}}

    def request(self, method: str, url: str, **kwargs: Any) -> requests.Response:  # type: ignore[override]
        here = _is_this_install(url)
        who = acting.current() if here else None
        if here:
            # Compressing for a caller inside this process costs the CPU the page is waiting on.
            kwargs["headers"] = {"Accept-Encoding": "identity", **(kwargs.get("headers") or {})}
        else:
            kwargs = self._paired(method, url, kwargs)
        try:
            response = self._send(who, method, url, **kwargs)
            streamed = any(hasattr(kwargs.get(name), "read") for name in ("data", "files"))
            if who is not None and not streamed and _wants_confirm(response) \
                    and acting.confirm():
                response = self._send(who, method, url, **kwargs)
            return response
        except requests.RequestException as exc:
            raise ApiError(t(device_client.why_not(exc))) from exc


def _as_mobile_device(item: dict) -> dict:
    """A mobile device as the Devices page holds one, from the list item the
    configuration answers with."""
    return {"device_id": item.get("id"), "kind": "vpx_mobile",
            "display_name": item.get("name"), "address": item.get("address"),
            "port": item.get("port")}


def updates_shown(values: dict) -> bool:
    """Whether the person has update notices on: the setting is a switch that is on unless
    it says otherwise."""
    kept = str((values.get("updates") or {}).get("show_updates", "true"))
    return kept.strip().lower() not in ("false", "0", "no", "off")


class ApiClient:
    """An ordinary consumer of /api/v1, over HTTP rather than by import.

    Deliberate, and the point of the exercise: anything the Manager cannot do through
    this client, no third-party client can do either.
    """

    def __init__(self, base_url: str | None = None) -> None:
        self._base = f"{base_url or local_base_url()}/api/v1"
        self._session = _WordedSession()
        self._media: dict[str, dict] = {}
        self._discovery: dict | None = None
        self._discovery_lock = threading.Lock()

    def _answered(self, response: Any) -> None:
        """Raise what the API said, not what HTTP said.

        `raise_for_status` throws away the body, so a considered message - "No Visual
        Pinball on this machine..." - reaches the panel as "501 Server Error: Not
        Implemented for url: ...", which names our own route at a user and tells them
        nothing. Every write in this client goes through here for that reason.
        """
        if response.ok:
            return
        said = ""
        # `object`, not `dict`: this is whatever the wire sent, and the isinstance
        # below is what decides it was a payload at all.
        details: object = {}
        try:
            found = (response.json() or {}).get("error") or {}
            said = str(found.get("message") or "")
            details = found.get("details") or {}
        except ValueError:
            said = ""
        raise ApiError(said or t("manager.api.api_answered",
                status_code=(response.status_code)),
                       details if isinstance(details, dict) else {})

    def _get(self, path: str, timeout: int = _TIMEOUT) -> dict:
        _refuse_the_event_loop(path)
        response = self._session.get(f"{self._base}{path}", timeout=timeout)
        self._answered(response)
        return response.json()

    def discovery(self) -> dict:
        with self._discovery_lock:
            if self._discovery is None:
                self._discovery = self._get("")
            return self._discovery

    def capabilities(self) -> list[dict]:
        return self.discovery().get("capabilities") or []

    def ext_get(self, path: str) -> dict:
        """A GET on an extension's own routes. Generic on purpose: the client cannot
        know what an extension serves, and a method per extension is not a client."""
        return self._get(path)

    def ext_post(self, path: str, body: dict, timeout: int = _TIMEOUT) -> dict:
        return self._post(path, body, timeout)

    def ext_put(self, path: str, body: dict) -> dict:
        return self._put(path, body)

    def extensions(self) -> list[dict]:
        """Every extension this install looked at, running or not."""
        return list(self._get("/extensions").get("extensions") or [])

    def extension_needs(self) -> dict:
        """What the running extensions ask for on Overview: `needs`, the extensions `away`
        whose needs could not be given, and the ones that `failed`."""
        return self._get("/extensions/needs")

    def asks(self) -> list[dict]:
        """The changes programs have asked the Owner to approve, still waiting."""
        return list(self._get("/auth/asks").get("asks") or [])

    def answer_ask(self, ask_id: str, approve: bool) -> dict:
        """Approve or decline one ask. Approving is a Protected write; declining is not."""
        return self._post(f"/auth/asks/{quote(ask_id, safe='')}/"
                          f"{'approve' if approve else 'decline'}", {})

    def sessions(self) -> list[dict]:
        """The browsers signed in, newest use first."""
        return list(self._get("/auth/sessions").get("sessions") or [])

    def sign_out_session(self, session_id: str) -> None:
        self._delete(f"/auth/sessions/{quote(session_id, safe='')}")

    def sign_out_everyone(self) -> None:
        self._delete("/auth/sessions")

    def confirm_code(self) -> None:
        """Draw a Confirm Code on this device's screen. Asking again within a few seconds
        is refused, and the code already shown is still good."""
        try:
            self._post("/auth/confirm/code", {})
        except ApiError as exc:
            if exc.details.get("reason") != "too_soon":
                raise

    def confirm(self, code: str) -> dict:
        return self._post("/auth/confirm", {"code": code})

    def authenticator(self) -> dict:
        """`enabled`, and `screen_codes`: whether this device shows a sign-in code."""
        return self._get("/auth/authenticator")

    def authenticator_setup(self) -> dict:
        """A new `secret` and the `uri` a QR carries. `authenticator_finish` makes it count."""
        return self._post("/auth/authenticator/setup", {})

    def authenticator_finish(self, code: str) -> dict:
        return self._put("/auth/authenticator", {"code": code})

    def authenticator_remove(self) -> None:
        self._delete("/auth/authenticator")

    def set_screen_codes(self, on: bool) -> dict:
        return self._put("/auth/authenticator/screen_codes", {"on": bool(on)})

    def confirm_authenticator(self, code: str) -> dict:
        return self._post("/auth/authenticator/confirm", {"code": code})

    def tokens(self) -> list[dict]:
        return list(self._get("/auth/tokens").get("tokens") or [])

    def make_token(self, name: str, access: str, *, can_power: bool = False) -> dict:
        """The token as made, with its `secret`, which cannot be read again."""
        return self._post("/auth/tokens", {"name": name, "access": access,
                                           "can_power": can_power})

    def set_token_power(self, token_id: str, can_power: bool) -> dict:
        return self._put(f"/auth/tokens/{quote(token_id, safe='')}",
                         {"can_power": bool(can_power)})

    def revoke_token(self, token_id: str) -> None:
        self._delete(f"/auth/tokens/{quote(token_id, safe='')}")

    def encrypted(self) -> dict:
        """The Trusted Proxies, the Trusted Networks and the certificate chosen, and
        whether this run answers HTTPS."""
        return self._get("/auth/encrypted")

    def add_trusted_proxy(self, address: str) -> dict:
        return self._post("/auth/encrypted/proxies", {"value": address})

    def remove_trusted_proxy(self, address: str) -> None:
        self._delete(f"/auth/encrypted/proxies?address={quote(address, safe='')}")

    def add_encrypted_network(self, text: str) -> dict:
        return self._post("/auth/encrypted/networks", {"value": text})

    def add_tailscale(self) -> dict:
        return self._post("/auth/encrypted/networks/tailscale", {})

    def remove_encrypted_network(self, text: str) -> None:
        self._delete(f"/auth/encrypted/networks?range={quote(text, safe='')}")

    def choose_certificate(self, certfile: str, keyfile: str) -> dict:
        return self._put("/auth/encrypted/certificate",
                         {"certfile": certfile, "keyfile": keyfile})

    def clear_certificate(self) -> None:
        self._delete("/auth/encrypted/certificate")

    def renew_remote_code(self) -> int:
        """Make a new Remote Code. How many phones that came in with the old one are out."""
        return int(self._post("/auth/remote_code/renew", {}).get("ended") or 0)

    def accept_extension(self, name: str) -> dict:
        """The extension as it stands once accepted."""
        return self._post(f"/extensions/{quote(name, safe='')}/accept", {})

    def set_extension_enabled(self, name: str, on: bool) -> dict:
        """The extension as it stands after the switch."""
        return self._put(f"/extensions/{quote(name, safe='')}/enabled",
                         {"enabled": bool(on)})

    def games(self) -> list[dict]:
        return self._get("/games", _LIBRARY_TIMEOUT).get("games", [])

    def filter_axes(self) -> list[dict]:
        return self._get("/library/filters", _LIBRARY_TIMEOUT).get("axes", [])

    def game(self, game_id: str) -> dict:
        return self._get(f"/games/{game_id}")

    def all_tables(self, media: str = "", game: str = "") -> list[dict]:
        """Every table in the library, or one game's, one row each, with its game's name
        on it; with `media`, which file of that kind each one shows."""
        query = urlencode({key: value for key, value in (("media", media), ("game", game))
                           if value})
        return list(self._get(f"/tables{'?' if query else ''}{query}",
                              _LIBRARY_TIMEOUT).get("tables") or [])

    def all_media(self, kind: str = "", *, shared: bool = False) -> list[dict]:
        """Every media file in the library, one row each, and one per file it lacks; with
        `kind`, only that kind's. With `shared`, only each game's shared files, cut to what
        a count of the coverage reads."""
        if shared:
            return self._get("/media/shared", _LIBRARY_TIMEOUT).get("media", [])
        query = "?" + urlencode({"kind": kind}) if kind else ""
        return self._get(f"/media{query}", _LIBRARY_TIMEOUT).get("media", [])

    def all_assets(self) -> list[dict]:
        """Every asset file in the library, one row each, and one per file it lacks."""
        return self._get("/assets", _LIBRARY_TIMEOUT).get("assets", [])

    def media_of(self, game_id: str) -> list[dict]:
        return self._get("/media?" + urlencode({"game": game_id})).get("media", [])

    def assets_of(self, game_id: str, kind: str = "") -> list[dict]:
        query = urlencode({"game": game_id, **({"kind": kind} if kind else {})})
        return self._get("/assets?" + query).get("assets", [])

    def outside_links(self, game_id: str, table: str = "", path: str = "") -> list[dict]:
        query = urlencode({key: value for key, value in (("table", table), ("path", path))
                           if value})
        return self._get(f"/games/{game_id}/links" + (f"?{query}" if query else "")) \
            .get("links") or []

    def asset_detail(self, game_id: str, path: str, lines: int = 40) -> dict:
        return self._get(f"/games/{game_id}/assets/detail?"
                         + urlencode({"path": path, "lines": lines}))

    def asset_text(self, game_id: str, path: str) -> dict:
        return self._get(f"/games/{game_id}/assets/text?" + urlencode({"path": path}))

    def put_asset_text(self, game_id: str, path: str, text: str, version: str,
                       replace_changed: bool = False, keep_copy: bool = True,
                       media: bool = False) -> dict:
        return self._put(f"/games/{game_id}/assets/text?" + urlencode({"path": path}),
                         {"text": text, "version": version,
                          "replace_changed": replace_changed, "keep_copy": keep_copy,
                          "media": media})

    def put_backglass_displays(self, game_id: str, path: str, target: str,
                               box: tuple[int, int, int, int] | None) -> dict:
        """Place the DMD of one window in the backglass file, or take it out for None."""
        body: dict[str, Any] = {"target": target}
        if box is not None:
            body.update(dict(zip("xywh", box, strict=True)))
        return self._put(f"/games/{game_id}/assets/backglass/displays?"
                         + urlencode({"path": path}), body)

    def undo_backglass_displays(self, game_id: str, token: str) -> dict:
        return self._post(f"/games/{game_id}/assets/backglass/displays/undo",
                          {"undo": token})

    def tables(self, game_id: str) -> list[dict]:
        return self._get(f"/games/{game_id}/tables").get("tables", [])

    def high_scores(self, game_id: str, table_id: str = "") -> dict:
        """A ROM's high score table, read again where its score file is newer. `{}`
        where there is nothing to show."""
        path = (f"/games/{game_id}/tables/{table_id}/high_scores" if table_id
                else f"/games/{game_id}/high_scores")
        return self._get(path) or {}

    def pictures(self, game_id: str, table_id: str = "") -> dict:
        """`pictures`, newest first; only `table_id`'s where one is named."""
        query = "?" + urlencode({"table": table_id}) if table_id else ""
        return self._get(f"/games/{game_id}/pictures{query}")

    def _picture_path(self, game_id: str, name: str) -> str:
        return f"/games/{game_id}/pictures/{quote(name, safe='')}"

    def picture_bytes(self, game_id: str, name: str) -> bytes:
        route = self._picture_path(game_id, name)
        _refuse_the_event_loop(route)
        response = self._session.get(f"{self._base}{route}", timeout=_TIMEOUT)
        self._answered(response)
        return response.content

    def remove_picture(self, game_id: str, name: str) -> dict:
        route = self._picture_path(game_id, name)
        _refuse_the_event_loop(route)
        response = self._session.delete(f"{self._base}{route}", timeout=_TIMEOUT)
        self._answered(response)
        return response.json()

    def put_picture(self, game_id: str, name: str, data: bytes) -> dict:
        route = self._picture_path(game_id, name)
        _refuse_the_event_loop(route)
        response = self._session.put(f"{self._base}{route}",
                                     files={"file": (name, data)}, timeout=_TIMEOUT)
        self._answered(response)
        return response.json()

    def media(self, game_id: str) -> dict:
        # Cached per client: /games carries VPS addon flags, not media coverage, so
        # coverage costs one call per game. 147 games measured at 1.1s, and threading
        # does not help - the server answers these sequentially.
        if game_id not in self._media:
            self._media[game_id] = self._get(f"/games/{game_id}/media").get("media", {})
        return self._media[game_id]

    def forget_media(self, game_id: str) -> None:
        """Drop the cached read after a write, so the next one sees what changed."""
        self._media.pop(game_id, None)

    def table_media(self, game_id: str, table_id: str) -> dict:
        """One build's answer, which differs from the game's only where it owns a file.

        Not cached with the rest: this is asked for one game at a time, by someone
        looking at it, rather than for the whole library on the way to a grid.
        """
        return self._get(
            f"/games/{game_id}/tables/{table_id}/media").get("media", {})

    def media_codecs(self, game_id: str, table_id: str) -> dict[str, dict]:
        """One lens's media with each video's codec read, which the lists above skip."""
        base = f"/games/{game_id}/tables/{table_id}/media" if table_id \
            else f"/games/{game_id}/media"
        return self._get(f"{base}?codecs=true").get("media", {})

    def preferences(self, scope: str) -> dict:
        return self._get(f"/preferences/{scope}").get("value", {})

    def put_preferences(self, scope: str, value: dict) -> None:
        _refuse_the_event_loop(f"/preferences/{scope}")
        response = self._session.put(f"{self._base}/preferences/{scope}",
                                     json=value, timeout=_TIMEOUT)
        self._answered(response)

    def rate(self, game_id: str, rating: int) -> None:
        _refuse_the_event_loop(f"/games/{game_id}/rating")
        response = self._session.put(f"{self._base}/games/{game_id}/rating",
                                     json={"rating": rating}, timeout=_TIMEOUT)
        self._answered(response)

    def set_table_tags(self, game_id: str, table_id: str, tags: list[str]) -> None:
        _refuse_the_event_loop(f"/games/{game_id}/tables/{table_id}/tags")
        response = self._session.put(
            f"{self._base}/games/{game_id}/tables/{table_id}/tags",
            json={"tags": list(tags)}, timeout=_TIMEOUT)
        self._answered(response)

    def rate_table(self, game_id: str, table_id: str, rating: int) -> None:
        """One table's own rating, which refines its game's rather than replacing it."""
        _refuse_the_event_loop(f"/games/{game_id}/tables/{table_id}/rating")
        response = self._session.put(
            f"{self._base}/games/{game_id}/tables/{table_id}/rating",
            json={"rating": rating}, timeout=_TIMEOUT)
        self._answered(response)

    def vps_sync_state(self) -> dict:
        """How fresh the local catalog is, and whether a check is owed."""
        _refuse_the_event_loop("/vps/sync")
        response = self._session.get(f"{self._base}/vps/sync", timeout=_TIMEOUT)
        self._answered(response)
        return dict(response.json() or {})

    def sync_vps(self) -> dict:
        """Check now, whatever the schedule says. The catalog is about 7 MB."""
        _refuse_the_event_loop("/vps/sync")
        response = self._session.post(f"{self._base}/vps/sync", timeout=_TIMEOUT * 6)
        self._answered(response)
        return dict(response.json() or {})

    def dof_event_state(self) -> dict:
        """The event on now and why none could be turned on, in words."""
        return self._get("/dof/event")

    def test_dof_event(self, event: str) -> dict:
        """Turn an event on until it is stopped; a refusal says why in words."""
        return self._post("/dof/event", {"event": event})

    def stop_dof_event(self) -> None:
        self._delete("/dof/event")

    def dof_config_state(self) -> dict:
        """When the DOF config was last downloaded, and whether a key is stored."""
        return self._get("/dof/config")

    def download_dof_config(self) -> dict:
        """Download the DOF config now, whatever version the tool is at; the job to watch."""
        return self._post("/dof/config", {})

    def vps_held(self, game_id: str) -> dict:
        """What this game holds against what the catalog lists for it: per kind, and its
        tables."""
        _refuse_the_event_loop(f"/games/{game_id}/vps_state")
        response = self._session.get(f"{self._base}/games/{game_id}/vps_state",
                                     timeout=_TIMEOUT)
        self._answered(response)
        return dict(response.json() or {})

    def vps_state(self, game_id: str) -> list[dict]:
        """Per kind: what this game holds, and what the catalog lists for it."""
        return list(self.vps_held(game_id).get("kinds") or [])

    def catalog_discovery(self) -> dict:
        """Every catalog entry against what the last count found the library holding."""
        return dict(self._get("/library/discovery"))

    def discovery_home(self, days: int) -> dict:
        """Discovery's front page, every shelf in one read."""
        return dict(self._get(f"/discovery/home?days={int(days)}"))

    def discovery_missing(self) -> dict:
        """Home's missing tabs as counts and ids, without the rows Home draws."""
        return dict(self._get("/discovery/missing"))

    def picture_changes(self, game_id: str = "") -> dict:
        """The pictures a source has replaced since they were placed, of one game or all."""
        return dict(self._get("/discovery/pictures" + (
            "?" + urlencode({"game_id": game_id}) if game_id else "")))


    def discovery_updates(self, game_id: str = "", set_aside: bool = False) -> dict:
        """The games with a later version of something they hold: `count`, `game_ids` and
        `games`, each with the `moves` behind it. With `game_id`, that game alone; with
        `set_aside`, the moves the person skipped or ignored too."""
        query = urlencode({**({"game_id": game_id} if game_id else {}),
                           **({"set_aside": "true"} if set_aside else {})})
        return dict(self._get("/discovery/updates" + (f"?{query}" if query else "")))

    def discovery_marks(self) -> list[dict]:
        """Everything marked Wanted or Ignored on this install, the newest first."""
        return list(self._get("/discovery/marks").get("marks") or [])

    def put_discovery_mark(self, state: str, scope: str, vps_id: str,
                           detail: str = "") -> dict:
        """Put a state on a thing: `marks` as they stand, and `replaced`, the state it held."""
        return dict(self._put("/discovery/marks", {"state": state, "scope": scope,
                                                   "id": vps_id, "detail": detail}))

    def delete_discovery_mark(self, scope: str, vps_id: str, detail: str = "") -> dict:
        """Take the mark off a thing: `marks` as they stand, and `replaced`, the state it held."""
        _refuse_the_event_loop("/discovery/marks")
        response = self._session.delete(
            f"{self._base}/discovery/marks", params={"scope": scope, "id": vps_id,
                                                      "detail": detail}, timeout=_TIMEOUT)
        self._answered(response)
        return dict(response.json())

    def vps_details(self, game_id: str) -> list[dict]:
        """Which of the game's details disagree with its entry - empty for a game
        nobody has re-matched, which is nearly all of them."""
        _refuse_the_event_loop(f"/games/{game_id}/vps_details")
        response = self._session.get(f"{self._base}/games/{game_id}/vps_details",
                                     timeout=_TIMEOUT)
        self._answered(response)
        return list((response.json() or {}).get("differs") or [])

    def adopt_vps_details(self, game_id: str,
                          fields: list[str] | None = None) -> None:
        """Take the named details from the entry. None takes every one that differs."""
        _refuse_the_event_loop(f"/games/{game_id}/vps_details")
        response = self._session.put(f"{self._base}/games/{game_id}/vps_details",
                                     json={"fields": list(fields or [])},
                                     timeout=_TIMEOUT)
        self._answered(response)

    def set_game_guides(self, game_id: str, guides: list[dict]) -> list[dict]:
        """The game's whole list of guides, in order. Answers with what was stored."""
        _refuse_the_event_loop(f"/games/{game_id}/guides")
        response = self._session.put(f"{self._base}/games/{game_id}/guides",
                                     json={"guides": guides}, timeout=_TIMEOUT)
        self._answered(response)
        return list((response.json() or {}).get("guides") or [])

    def set_table_source(self, game_id: str, table_id: str, vps_file_id: str) -> None:
        """Bind one table to the release somebody says it is. Empty unbinds."""
        _refuse_the_event_loop(f"/games/{game_id}/tables/{table_id}/source")
        response = self._session.put(
            f"{self._base}/games/{game_id}/tables/{table_id}/source",
            json={"vps_file_id": vps_file_id}, timeout=_TIMEOUT)
        self._answered(response)

    def set_asset_source(self, game_id: str, path: str, vps_file_id: str) -> None:
        """Bind one file to the VPS record somebody says it is. Empty unbinds. The path
        is the ledger's own key - folder-relative, forward slashes."""
        _refuse_the_event_loop(f"/games/{game_id}/asset_source")
        response = self._session.put(
            f"{self._base}/games/{game_id}/asset_source",
            json={"path": path, "vps_file_id": vps_file_id}, timeout=_TIMEOUT)
        self._answered(response)

    def set_favorite(self, game_id: str, favorite: bool) -> None:
        """A game's favorite flag. Games only - you favorite a machine, not a build."""
        _refuse_the_event_loop(f"/games/{game_id}/favorite")
        response = self._session.put(f"{self._base}/games/{game_id}/favorite",
                                     json={"favorite": favorite}, timeout=_TIMEOUT)
        self._answered(response)

    def set_private(self, game_id: str, private: bool) -> None:
        """Mark one game Private, or not: never sent to a community service."""
        self._put(f"/games/{game_id}/private", {"private": private})

    def set_private_many(self, game_ids: list[str], private: bool) -> dict:
        """Mark a selection Private, or not, in one request. `{private, games, changed}`;
        an id the library does not hold refuses the lot."""
        return self._put("/library/private", {"game_ids": list(game_ids), "private": private})

    def put_marks(self, edits: list[dict]) -> dict:
        """Marks on several tables or games in one request. What comes back is the job to
        watch; its result names each row written and each that could not be."""
        return dict(self._put("/library/marks", {"edits": edits}) or {})

    def set_tags(self, game_id: str, tags: list[str]) -> None:
        """The whole set. What comes back is what was stored, which may differ - the
        the API trims and drops repeats."""
        _refuse_the_event_loop(f"/games/{game_id}/tags")
        response = self._session.put(f"{self._base}/games/{game_id}/tags",
                                     json={"tags": list(tags)}, timeout=_TIMEOUT)
        self._answered(response)

    def vps_search(self, term: str, limit: int = 40) -> list[dict]:
        """Catalog entries matching every word, already in a neutral order. No score
        comes back and none is wanted - the ranker was measured and retired."""
        _refuse_the_event_loop("/vps/search")
        params: dict[str, str | int] = {"q": term, "limit": limit}
        response = self._session.get(f"{self._base}/vps/search", params=params,
                                     timeout=_TIMEOUT)
        self._answered(response)
        return list(response.json().get("results") or [])

    def vps_entry(self, vps_id: str) -> dict:
        """What a game is matched to, so a surface shows the match and not its id."""
        _refuse_the_event_loop("/vps/entry")
        response = self._session.get(
            f"{self._base}/vps/entry/{quote(vps_id, safe='')}", timeout=_TIMEOUT)
        if response.status_code == 404:
            return {}
        self._answered(response)
        return dict(response.json() or {})

    def vps_releases(self, vps_id: str, listed_as: str = "tableFiles") -> list[dict]:
        """One kind of record VPSdb lists for an entry, in the order it holds them.
        `listed_as` is VPS's own key, because one of theirs can be two of ours."""
        _refuse_the_event_loop("/vps/entry/releases")
        response = self._session.get(
            f"{self._base}/vps/entry/{quote(vps_id, safe='')}/releases",
            params={"listed_as": listed_as}, timeout=_TIMEOUT)
        if response.status_code == 404:
            return []
        self._answered(response)
        return list((response.json() or {}).get("releases") or [])

    def owned(self, ids: list[str], keys: str = "vps", kind: str = "") -> dict:
        return self._post("/library/owned", {"ids": list(ids), "keys": keys, "kind": kind})

    def tags(self) -> list[dict]:
        return list(self._get("/library/tags", _LIBRARY_TIMEOUT).get("tags") or [])

    def put_tag(self, tag: str, changes: dict) -> dict:
        return self._put(f"/library/tags/{quote(tag, safe='')}", changes)

    def merge_tags(self, sources: list[str], into: str) -> int:
        """Across the library: a tag is a word the library holds, not a game's."""
        _refuse_the_event_loop("/library/tags/merge")
        response = self._session.post(f"{self._base}/library/tags/merge",
                                      json={"sources": list(sources), "into": into},
                                      timeout=_TIMEOUT)
        self._answered(response)
        return int(response.json().get("changed") or 0)

    def delete_tag(self, tag: str) -> int:
        _refuse_the_event_loop("/library/tags")
        response = self._session.delete(f"{self._base}/library/tags/{quote(tag, safe='')}",
                                        timeout=_TIMEOUT)
        self._answered(response)
        return int(response.json().get("changed") or 0)

    def reset_play_record(self, game_id: str, table_id: str = "") -> None:
        """Clear the counters, keeping what was entered. A table's record is its own."""
        path = (f"/games/{game_id}/tables/{table_id}/play_record" if table_id
                else f"/games/{game_id}/play_record")
        _refuse_the_event_loop(path)
        self._answered(self._session.delete(f"{self._base}{path}", timeout=_TIMEOUT))

    def extract_script(self, game_id: str, table_id: str) -> None:
        """Write the sidecar, which is also what makes the table run it."""
        _refuse_the_event_loop(f"/games/{game_id}/tables/{table_id}/script")
        response = self._session.post(
            f"{self._base}/games/{game_id}/tables/{table_id}/script", timeout=_TIMEOUT)
        self._answered(response)

    def delete_script(self, game_id: str, table_id: str) -> None:
        """Take the sidecar away, putting the table back on its own script."""
        _refuse_the_event_loop(f"/games/{game_id}/tables/{table_id}/script")
        response = self._session.delete(
            f"{self._base}/games/{game_id}/tables/{table_id}/script", timeout=_TIMEOUT)
        self._answered(response)

    def launch(self, game_id: str, file: str = "") -> None:
        """`file` picks one of the game's tables; empty launches its default."""
        _refuse_the_event_loop(f"/games/{game_id}/launch")
        response = self._session.post(f"{self._base}/games/{game_id}/launch",
                                      json={"file": file} if file else {},
                                      timeout=_TIMEOUT)
        self._answered(response)

    def _post(self, path: str, body: dict, timeout: int = _TIMEOUT) -> dict:
        _refuse_the_event_loop(path)
        response = self._session.post(f"{self._base}{path}", json=body, timeout=timeout)
        self._answered(response)
        return response.json()

    def _put(self, path: str, body: dict | list) -> Any:
        _refuse_the_event_loop(path)
        response = self._session.put(f"{self._base}{path}", json=body, timeout=_TIMEOUT)
        self._answered(response)
        return response.json()

    def _patch(self, path: str, body: dict) -> dict:
        _refuse_the_event_loop(path)
        response = self._session.patch(f"{self._base}{path}", json=body,
                                       timeout=_TIMEOUT)
        self._answered(response)
        return response.json()

    def _delete(self, path: str) -> None:
        _refuse_the_event_loop(path)
        response = self._session.delete(f"{self._base}{path}", timeout=_TIMEOUT)
        self._answered(response)

    # --- collections ----------------------------------------------------------
    # Addressed by name, which is what the routes take. A rename is therefore a move,
    # and every caller here has to use the name the server last reported rather than
    # one it remembered.

    def game_collections(self, game_id: str) -> dict:
        """`collections` holding the game, and `taken_out`, the ones keeping it out."""
        said = self._get(f"/games/{game_id}/collections")
        return {"collections": list(said.get("collections") or []),
                "taken_out": list(said.get("taken_out") or [])}

    def collections(self) -> list[dict]:
        return list(self._get("/collections").get("collections") or [])

    def library_game_collections(self) -> dict[str, list[dict]]:
        """Game id to the collections holding it, for every game some collection holds."""
        held = self._get("/library/game_collections", _LIBRARY_TIMEOUT)
        return {str(one.get("game") or ""): list(one.get("collections") or [])
                for one in held.get("games") or []}

    def collection_games(self, name: str) -> list[dict]:
        """The management lens: what is in it now, and why each one is there."""
        return list(self._get(f"/collections/{quote(name, safe='')}/games",
                              _LIBRARY_TIMEOUT).get("games") or [])

    def library_entries(self) -> list[dict]:
        """The play lens over the whole library: what the cabinet offers before a
        collection is chosen."""
        return list(self._get("/library/entries", _LIBRARY_TIMEOUT).get("entries") or [])

    def collection_entries(self, name: str) -> list[dict]:
        """The play lens: one entry per game, in the order the cabinet shows them."""
        return list(self._get(f"/collections/{quote(name, safe='')}/entries",
                              _LIBRARY_TIMEOUT).get("entries") or [])

    def create_collection(self, name: str, filters: dict | None = None,
                          games: list[str] | None = None, copy_of: str | None = None) -> dict:
        """Criteria, hand-picked games, or both - they are combinable, and the kind is
        derived from what ends up stored. `copy_of` starts it as a copy of that one."""
        body: dict[str, Any] = {"name": name, "games": games or []}
        if filters is not None:
            body["filters"] = filters
        if copy_of is not None:
            body["copy_of"] = copy_of
        return self._post("/collections", body)

    def patch_collection(self, name: str, changes: dict) -> dict:
        """Only what is sent is written, so a rename need not restate the criteria."""
        return self._patch(f"/collections/{quote(name, safe='')}", changes)

    def delete_collection(self, name: str) -> None:
        self._delete(f"/collections/{quote(name, safe='')}")

    def add_to_collection(self, name: str, game_id: str, table: str = "",
                          after_table: str | None = None) -> None:
        """`table` holds the collection to exactly that table; without it the member
        names the game and follows whichever table is its default. `after_table` puts
        the new ref beside its sibling rather than at the end of the list."""
        self._put_empty(f"/collections/{quote(name, safe='')}/games/{game_id}",
                        {"table": table, "after_table": after_table})

    def set_member_table(self, name: str, game_id: str, table: str = "",
                         was: str = "") -> None:
        """Point an existing member at a different table, in place. `was` names the ref
        to change where a game appears more than once; empty `table` gives it back its
        default."""
        self._put_empty(f"/collections/{quote(name, safe='')}/games/{game_id}/table",
                        {"table": table, "was": was})

    def remove_from_collection(self, name: str, game_id: str,
                               table: str | None = None) -> None:
        """`None` removes every ref naming this game; a string - including "" for the
        ref that names no table - removes exactly that one."""
        suffix = "" if table is None else f"?table={quote(table, safe='')}"
        self._delete(f"/collections/{quote(name, safe='')}/games/{game_id}{suffix}")

    def arrange_collections(self, names: list[str]) -> list[dict]:
        """Every collection, in the order the cabinet should show them."""
        return list(self._patch("/collections", {"order": names}).get("collections") or [])

    def set_collection_order(self, name: str, games: list[str]) -> None:
        """The whole ordered list at once - atomic, and no index arithmetic here."""
        self._put_empty(f"/collections/{quote(name, safe='')}/order",
                        {"games": games})

    def collection_members(self, name: str) -> dict:
        """Stored membership with the state of each: what is written down, not what
        resolved. The only lens that reports a member naming something gone."""
        return self._get(f"/collections/{quote(name, safe='')}/members")

    def preview_collection(self, name: str, filters: dict) -> dict:
        return self._post(f"/collections/{quote(name, safe='')}/members/preview",
                          {"filters": filters or None})

    def preview_filters(self, filters: dict | None, limit: int | None = None) -> dict:
        """What a rule would match, storing nothing - so a rule can be built while its
        result is on screen instead of every experiment landing on the frontend."""
        body: dict[str, Any] = {"filters": filters or {}}
        if limit:
            body["limit"] = limit
        return self._post("/library/preview", body)

    def exclude_from_collection(self, name: str, game_id: str, table: str = "") -> None:
        self._put_empty(f"/collections/{quote(name, safe='')}/excluded/{game_id}",
                        {"table": table})

    def unexclude_from_collection(self, name: str, game_id: str,
                                  table: str | None = None) -> None:
        """`None` lifts every exclusion naming this game; a string - including "" for
        the one that names no table - lifts exactly that one."""
        suffix = "" if table is None else f"?table={quote(table, safe='')}"
        self._delete(f"/collections/{quote(name, safe='')}/excluded/{game_id}{suffix}")

    def keep_collection_result(self, name: str) -> dict:
        """Replace the criteria with what they currently match. The list stops changing
        under its owner, which is what makes it static."""
        return self._post(
            f"/collections/{quote(name, safe='')}/members/from_filters", {})

    def set_collection_image(self, name: str, filename: str, content: bytes) -> dict:
        route = f"/collections/{quote(name, safe='')}/image"
        _refuse_the_event_loop(route)
        response = self._session.put(f"{self._base}{route}",
                                     files={"file": (filename, content)},
                                     timeout=_TIMEOUT)
        self._answered(response)
        return response.json()

    def clear_collection_image(self, name: str) -> None:
        self._delete(f"/collections/{quote(name, safe='')}/image")

    def _put_empty(self, path: str, body: dict | None = None) -> None:
        """A PUT that answers 204. `_put` would raise trying to read a body."""
        _refuse_the_event_loop(path)
        response = self._session.put(f"{self._base}{path}", json=body or {},
                                     timeout=_TIMEOUT)
        self._answered(response)

    def _media_path(self, game_id: str, table_id: str, kind: str) -> str:
        """Which route places a file, which is the same thing as which tier it lands at."""
        return (f"/games/{game_id}/tables/{table_id}/media/{kind}" if table_id
                else f"/games/{game_id}/media/{kind}")

    def placements(self, game_id: str, kind: str) -> dict:
        """Where a file for this kind could land, and what each choice replaces."""
        return self._get(f"/games/{game_id}/media/{kind}/placements")

    def media_overrides(self, game_id: str) -> dict:
        """Kinds where a table has media of its own, keyed by kind."""
        return self._get(f"/games/{game_id}/media/overrides").get("overrides") or {}

    def media_detail(self, game_id: str, table_id: str, kind: str, page: int = 1) -> dict:
        """One slot in full - its file's size and shape, and every tier holding one."""
        return self._get(f"{self._media_path(game_id, table_id, kind)}/detail"
                         + _page_query(page))

    def browse_roots(self, game_id: str = "") -> list[dict]:
        """Where browsing this machine may start, this game's own folder included."""
        query = "?" + urlencode({"game": game_id}) if game_id else ""
        return list(self._get(f"/filesystem/roots{query}").get("roots") or [])

    def browse(self, path: str, asset_kind: str = "", archives: bool = False) -> dict:
        """One folder on this machine, as folders and media files, and the files of
        `asset_kind` where one is named. `archives` adds the archives."""
        asked = {"path": path, **({"kind": asset_kind} if asset_kind else {}),
                 **({"archives": "true"} if archives else {})}
        return self._get("/filesystem/entries?" + urlencode(asked))

    def folders(self, path: str = "", kind: str = "dir",
               suffixes: Iterable[str] = (), hidden: bool = False) -> dict:
        """One folder on this machine, as its immediate subfolders and the files `kind`
        wants - unbounded, for a Browse button filling in a path field."""
        asked: dict[str, Any] = {"path": path, "kind": kind}
        if suffixes:
            asked["suffix"] = list(suffixes)
        if hidden:
            asked["hidden"] = "true"
        return self._get("/folders?" + urlencode(asked, doseq=True))

    def make_folder(self, parent: str, name: str) -> dict:
        """A folder made inside `parent`, answered with its own listing."""
        return self._post("/folders", {"parent": parent, "name": name})

    def browsed_file_url(self, path: str) -> str:
        """Where the browser can fetch a file it is showing, so it can be looked at."""
        return "/api/v1/filesystem/file?" + urlencode({"path": path})

    def disk_file_url(self, path: str) -> str:
        """Where the browser fetches a file anywhere on this device, to look at it.
        Loopback only: a Manager opened from another device is refused."""
        return "/api/v1/folders/file?" + urlencode({"path": path})

    def media_file(self, game_id: str, table_id: str, kind: str,
                   page: int = 1) -> tuple[bytes, str]:
        """The file a slot resolves to, and the content type it is served as."""
        route = self._media_path(game_id, table_id, kind)
        _refuse_the_event_loop(route)
        response = self._session.get(f"{self._base}{route}", params=_page_params(page),
                                     timeout=_TIMEOUT)
        self._answered(response)
        return response.content, response.headers.get("content-type", "")

    def media_sources(self) -> list[dict]:
        """The online catalogs this install knows, and which are being asked."""
        return list(self._get("/media-sources").get("sources") or [])

    def source_offers(self, extension: str, key: str, kind: str, game_id: str,
                      vps_id: str, name: str) -> dict:
        """What an extension's online source has of `kind` for a game: its rows, and the
        note and link it draws under them."""
        return self.ext_get(
            f"/ext/{quote(extension, safe='')}/sources/{quote(key, safe='')}/offers?"
            + urlencode({"kind": kind, "game_id": game_id, "vps_id": vps_id, "name": name}))

    def source_take(self, extension: str, key: str, kind: str, row: str, game_id: str,
                    vps_id: str, name: str, table_id: str = "") -> dict:
        return self.ext_post(
            f"/ext/{quote(extension, safe='')}/sources/{quote(key, safe='')}/take",
            {"kind": kind, "row": row,
             "who": {"game_id": game_id, "vps_id": vps_id, "name": name,
                     "table_id": table_id}})

    def source_take_many(self, extension: str, key: str, picks: list[dict], game_id: str,
                         vps_id: str, name: str) -> dict:
        """Hand an extension's source every file ticked in Discovery as one batch."""
        return self.ext_post(
            f"/ext/{quote(extension, safe='')}/sources/{quote(key, safe='')}/take-many",
            {"picks": [{"kind": one["kind"], "key": one["key"]} for one in picks],
             "who": {"game_id": game_id, "vps_id": vps_id, "name": name}})

    def source_act(self, extension: str, key: str, kind: str, row: str, act: str,
                   game_id: str, vps_id: str, name: str) -> dict:
        """Press a button a source drew on a row, or on the pane where `kind` is empty."""
        return self.ext_post(
            f"/ext/{quote(extension, safe='')}/sources/{quote(key, safe='')}/act",
            {"act": act, "kind": kind, "row": row,
             "who": {"game_id": game_id, "vps_id": vps_id, "name": name}})

    def source_look(self, extension: str, key: str, kind: str, row: str, game_id: str,
                    vps_id: str, name: str) -> dict:
        """What the page of a source's file says: pictures, description, files."""
        return self.ext_get(
            f"/ext/{quote(extension, safe='')}/sources/{quote(key, safe='')}/look?"
            + urlencode({"kind": kind, "row": row, "game_id": game_id, "vps_id": vps_id,
                         "name": name}))

    def game_offers(self, game_id: str, vps_id: str, name: str, kinds: list[str]) -> dict:
        """What every enabled source has for a game, in one call: for each source asked, its
        answer per kind, with the reason where it could not answer. `vps_id` and `name` ask
        about another entry than the game's own; empty asks about its own. No kinds asks
        nothing: the route would read none as every kind."""
        if not kinds:
            return {"game_id": game_id, "vps_id": vps_id, "sources": []}
        asked: list[tuple[str, str]] = [("kind", one) for one in kinds]
        if vps_id:
            asked += [("vps_id", vps_id), ("name", name)]
        return self._get(f"/games/{quote(game_id, safe='')}/offers"
                         + (f"?{urlencode(asked)}" if asked else ""))

    def source_changes(self, since: int) -> dict:
        """The newest version of the offers extensions' sources report, and the VPS ids that
        moved after `since`."""
        return self._get("/media-sources/changes?" + urlencode({"since": since}))

    def wheel_sets(self) -> list[str]:
        """The wheel sets the library has media in, and the reserved `logo`."""
        return [str(name) for name in self._get("/media/wheel-sets").get("sets") or []]

    def media_offers(self, vps_id: str, kind: str) -> list[dict]:
        """What every enabled catalog has for this game and kind."""
        return list(self._get("/media-sources/offers?"
                              + urlencode({"vps_id": vps_id, "kind": kind}))
                    .get("offers") or [])

    def search_vps(self, query: str, limit: int = 12) -> list[dict]:
        """VPS entries by name, for taking media from a game other than this one."""
        return list(self._get("/vps/search?" + urlencode({"q": query, "limit": limit}))
                    .get("results") or [])

    def fetch_media(self, game_id: str, table_id: str, kind: str, source: str,
                    vps_id: str, size: str = "") -> dict:
        """Pull a catalog's media into the slot. The API resolves the link itself."""
        return self._post(f"/games/{game_id}/media/{kind}/fetch",
                          {"source": source, "vps_id": vps_id, "size": size,
                           "table": table_id or ""})

    def draw_backglass_picture(self, game_id: str, table_id: str, kind: str = "") -> dict:
        """Draw the picture of the backglass file of `kind` into the slot, the backglass
        file where it is empty."""
        return self._post(f"/games/{game_id}/media/backglass/from-file",
                          {"table": table_id or "", "asset_kind": kind or "backglass"})

    def file_move_state(self, game_id: str, family: str, kind: str) -> dict:
        """What a move of the game's file would start from: the game's file, each table's
        own, and whether it can go to the game or a table is playing."""
        return self._get(f"/games/{game_id}/moves/{family}/{kind}")

    def move_file_to_tables(self, game_id: str, family: str, kind: str,
                            table_ids: list[str]) -> dict:
        return self._post(f"/games/{game_id}/moves/{family}/{kind}/to-tables",
                          {"tables": table_ids})

    def move_file_to_game(self, game_id: str, family: str, kind: str,
                          table_id: str) -> dict:
        return self._post(f"/games/{game_id}/moves/{family}/{kind}/to-game",
                          {"table": table_id})

    def undo_file_move(self, game_id: str, token: str) -> dict:
        return self._post(f"/games/{game_id}/moves/undo", {"undo": token})

    def move_media_page(self, game_id: str, table_id: str, kind: str, page: int,
                        to: int) -> dict:
        """Swap page `page` of a slot with page `to`. Only the numbers in the names move."""
        path = f"/games/{game_id}/media/{kind}/move"
        _refuse_the_event_loop(path)
        response = self._session.post(f"{self._base}{path}",
                                      json={"table": table_id or "", "page": page, "to": to},
                                      timeout=_TIMEOUT)
        self._answered(response)
        return response.json()

    def displaced_by(self, game_id: str, table_id: str, kind: str, filename: str,
                     page: int = 1) -> list[str]:
        """What placing `filename` here would replace, asked before the bytes go up."""
        path = f"{self._media_path(game_id, table_id, kind)}/displaced"
        _refuse_the_event_loop(path)
        asked: dict[str, str | int] = {"filename": filename, **_page_params(page)}
        response = self._session.get(f"{self._base}{path}", params=asked,
                                     timeout=_TIMEOUT)
        self._answered(response)
        return list(response.json().get("displaced") or [])

    def place_media(self, game_id: str, table_id: str, kind: str,
                    filename: str, data: bytes, page: int = 1) -> dict:
        path = self._media_path(game_id, table_id, kind)
        _refuse_the_event_loop(path)
        response = self._session.put(f"{self._base}{path}", params=_page_params(page),
                                     files={"file": (filename, data)},
                                     timeout=_IMPORT_TIMEOUT)
        self._answered(response)
        return response.json()

    def _asset_path(self, game_id: str, table_id: str, kind: str) -> str:
        return (f"/games/{game_id}/tables/{table_id}/assets/{kind}" if table_id
                else f"/games/{game_id}/assets/{kind}")

    def asset_placements(self, game_id: str, kind: str) -> dict:
        return self._get(f"/games/{game_id}/assets/{kind}/placements")

    def asset_displaced_by(self, game_id: str, table_id: str, kind: str,
                           filename: str) -> list[str]:
        path = f"/games/{game_id}/assets/{kind}/displaced"
        _refuse_the_event_loop(path)
        response = self._session.get(f"{self._base}{path}",
                                     params={"filename": filename, "table": table_id or ""},
                                     timeout=_TIMEOUT)
        self._answered(response)
        return list(response.json().get("displaced") or [])

    def place_asset(self, game_id: str, table_id: str, kind: str,
                    filename: str, data: bytes) -> dict:
        path = self._asset_path(game_id, table_id, kind)
        _refuse_the_event_loop(path)
        response = self._session.put(f"{self._base}{path}",
                                     files={"file": (filename, data)},
                                     timeout=_IMPORT_TIMEOUT)
        self._answered(response)
        return response.json()

    def remove_asset(self, game_id: str, path: str) -> dict:
        route = f"/games/{game_id}/assets"
        _refuse_the_event_loop(route)
        response = self._session.delete(f"{self._base}{route}", params={"path": path},
                                        timeout=_TIMEOUT)
        self._answered(response)
        return response.json()

    def remove_media(self, game_id: str, table_id: str, kind: str,
                     page: int | None = None) -> dict:
        """Take a file out of the slot's tier. With a `page` only that page goes; without
        one the kind goes with all of its pages."""
        path = self._media_path(game_id, table_id, kind)
        _refuse_the_event_loop(path)
        response = self._session.delete(
            f"{self._base}{path}", params={"page": page} if page else None,
            timeout=_TIMEOUT)
        self._answered(response)
        return response.json()

    def file_impacts(self, game_id: str, path: str) -> list[dict]:
        """What removing this file of the game's folder would do, removing nothing."""
        route = f"/games/{game_id}/files/impacts"
        _refuse_the_event_loop(route)
        response = self._session.get(f"{self._base}{route}",
                                     params={"path": path, "remove": "true"}, timeout=_TIMEOUT)
        self._answered(response)
        return list(response.json().get("impacts") or [])

    def remove_game_file(self, game_id: str, path: str, resolve: list[str]) -> dict:
        """Remove one file of the game's folder by its path there. A copy goes to Backups
        first; `resolve` answers the effects `file_impacts` listed."""
        route = f"/games/{game_id}/files"
        _refuse_the_event_loop(route)
        response = self._session.delete(f"{self._base}{route}",
                                        params={"path": path, "resolve": resolve},
                                        timeout=_TIMEOUT)
        self._answered(response)
        return response.json()

    def set_table_hidden(self, game_id: str, table_id: str, hidden: bool) -> dict:
        """Offer this table in the frontend, or stop. The file is not touched."""
        path = f"/games/{game_id}/tables/{table_id}/hidden"
        _refuse_the_event_loop(path)
        response = self._session.put(f"{self._base}{path}", json={"hidden": hidden},
                                     timeout=_TIMEOUT)
        self._answered(response)
        return response.json()

    def set_default_table(self, game_id: str, table_id: str) -> dict:
        """Which table this game offers first. Empty clears the choice."""
        path = f"/games/{game_id}/default_table"
        _refuse_the_event_loop(path)
        response = self._session.put(f"{self._base}{path}", json={"table": table_id},
                                     timeout=_TIMEOUT)
        self._answered(response)
        return response.json()

    def metrics(self, history_seconds: float = 0) -> dict:
        """What this machine is doing, and optionally what it has been doing.

        Reading is what fills the history, so the page polling this is the thing that
        builds the record - a machine nobody is looking at keeps none.
        """
        tail = f"?history_seconds={history_seconds}" if history_seconds else ""
        return dict(self._get(f"/metrics{tail}") or {})

    def themes(self, refresh: bool = False) -> dict:
        """Every theme this install knows, active first. `refresh` re-reads the sources,
        which reaches the network - so it is asked for rather than done on every draw."""
        return dict(self._get(f"/themes?refresh={'true' if refresh else 'false'}") or {})

    def theme_sources(self) -> dict:
        """The configured theme sources and what each said when last read. Reads nothing."""
        return dict(self._get("/themes/sources") or {})

    def check_theme_source(self, address: str) -> dict:
        """What adding `address` would be, without adding it. Reads the address, and a
        repository may be read twice, so it is given longer than an ordinary call."""
        _refuse_the_event_loop("/themes/sources/check")
        response = self._session.post(f"{self._base}/themes/sources/check",
                                      json={"address": address}, timeout=_TIMEOUT * 2)
        self._answered(response)
        return dict(response.json() or {})

    def name_theme_source(self, address: str, name: str) -> dict:
        """Call the added source at `address` `name`; blank takes the name back."""
        return dict(self._put("/themes/sources/name", {"address": address, "name": name}) or {})

    def install_theme(self, key: str) -> dict:
        """Install or update - installing over an existing copy is what an update is."""
        return dict(self._post(f"/themes/{key}/install", {}) or {})

    def accept_theme(self, key: str) -> dict:
        """Accept a theme from the address this install knows it by."""
        return dict(self._post(f"/themes/{quote(key, safe='')}/accept", {}) or {})

    def unaccepted_themes(self) -> dict:
        """The installed themes stopped until accepted. Reads the folder, not the sources."""
        return dict(self._get("/themes/unaccepted") or {})

    def remove_theme(self, key: str) -> dict:
        self._delete(f"/themes/{key}")
        return {"key": key}

    def activate_theme(self, key: str) -> dict:
        return dict(self._put("/themes/active", {"key": key}) or {})

    def theme_options(self, key: str) -> dict:
        """What a theme declares it can be configured with, and its current values."""
        return dict(self._get(f"/themes/{key}/options") or {})

    def save_theme_options(self, key: str, values: dict) -> dict:
        return dict(self._put(f"/themes/{key}/options", {"values": values}) or {})

    def about(self, refresh: bool = False) -> dict:
        """What this install and this machine are, grouped, with the text to paste.

        The plain text comes back with the groups rather than being assembled here: the
        two would drift apart the day either end gained a field.
        """
        return dict(self._get(f"/about?refresh={'true' if refresh else 'false'}") or {})

    def launchers(self) -> dict:
        """Every launcher, the tables that deviate, and which one is the default.

        The field shape travels with each launcher, so the editor is drawn from what the
        install says a launcher of that app holds rather than from a list kept here. Each
        carries `checks` for the fields that name a path: `{field: {state, reason}}`.
        """
        found = dict(self._get("/launchers") or {})
        checks: dict[str, dict[str, dict]] = {}
        for one in self.config_path_checks():
            if one.get("list") == "launchers":
                checks.setdefault(str(one.get("id")), {})[str(one.get("field"))] = {
                    "state": one.get("state", ""), "reason": one.get("reason", "")}
        found["launchers"] = [{**one, "checks": checks.get(str(one.get("launcher_id")), {})}
                              for one in found.get("launchers") or []]
        return found

    def put_launcher(self, launcher_id: str, body: dict) -> dict:
        """One whole launcher, under an id the caller owns - which is also how one copied
        from another machine lands without being renumbered. The tables already on it
        stay on it."""
        held = [one for one in self._get("/config/launchers") or [] if isinstance(one, dict)]
        item = launcher_wire.to_item(launcher_id, body, held)
        return launcher_wire.from_item(
            dict(self._put(f"/config/launchers/{launcher_id}", item) or {}))

    def delete_launcher(self, launcher_id: str) -> None:
        self._delete(f"/config/launchers/{launcher_id}")

    def launcher_standing(self, app: str, settings: dict) -> dict:
        return dict(self._post("/launchers/standing", {"app": app, "settings": settings})
                    or {})

    def launcher_fallback(self, launcher_id: str) -> dict:
        return dict(self._get(f"/launchers/{launcher_id}/fallback") or {})

    def make_launcher_default(self, launcher_id: str) -> dict:
        return dict(self._post(f"/launchers/{launcher_id}/default", {}) or {})

    def restore_launcher_order(self, order: list[str]) -> dict:
        return dict(self._post("/launchers/order", {"order": list(order)}) or {})

    def assign_launcher(self, table_id: str, launcher_id: str) -> dict:
        """Point a table at one, or clear it back to the default with an empty id."""
        return dict(self._put(f"/launchers/mappings/{table_id}",
                              {"launcher_id": launcher_id}) or {})

    # --- players --------------------------------------------------------------

    def players(self) -> list[dict]:
        """The owner, the kept players, then the guests as they joined."""
        return list(self._get("/players").get("players") or [])

    def add_player(self, name: str, initials: str) -> dict:
        return dict(self._post("/players", {"name": name, "initials": initials}) or {})

    def add_guest(self, initials: str) -> dict:
        return dict(self._post("/players/guests", {"initials": initials}) or {})

    def add_guest_from_card(self, card: str) -> dict:
        """`card` is a card file's text, or the card's own text."""
        return dict(self._post("/players/guests/card", {"card": card}) or {})

    def change_player(self, player_id: str, *, name: str | None = None,
                      initials: str | None = None) -> dict:
        body = {key: value for key, value in (("name", name), ("initials", initials))
                if value is not None}
        return dict(self._patch(f"/players/{quote(player_id, safe='')}", body) or {})

    def remove_player(self, player_id: str) -> None:
        """Removes a kept player or signs a guest out, with their accounts and record."""
        self._delete(f"/players/{quote(player_id, safe='')}")

    def set_player_up(self, player_id: str, up: bool) -> list[dict]:
        """The whole roster afterwards."""
        said = self._put(f"/players/{quote(player_id, safe='')}/up", {"up": bool(up)})
        return list((said or {}).get("players") or [])

    def _player_picture_path(self, player_id: str) -> str:
        return f"/players/{quote(player_id, safe='')}/picture"

    def set_player_picture(self, player_id: str, filename: str, content: bytes) -> dict:
        route = self._player_picture_path(player_id)
        _refuse_the_event_loop(route)
        response = self._session.put(f"{self._base}{route}",
                                     files={"file": (filename, content)}, timeout=_TIMEOUT)
        self._answered(response)
        return response.json()

    def clear_player_picture(self, player_id: str) -> dict:
        route = self._player_picture_path(player_id)
        _refuse_the_event_loop(route)
        response = self._session.delete(f"{self._base}{route}", timeout=_TIMEOUT)
        self._answered(response)
        return response.json()

    def player_picture_bytes(self, player_id: str) -> bytes | None:
        """The stored picture, or None for a player with none."""
        route = self._player_picture_path(player_id)
        _refuse_the_event_loop(route)
        response = self._session.get(f"{self._base}{route}", timeout=_TIMEOUT)
        if response.status_code == 404:
            return None
        self._answered(response)
        return response.content

    def player_record(self, player_id: str) -> list[dict]:
        """What a player other than the owner has played, most recently first."""
        return list(self._get(f"/players/{quote(player_id, safe='')}/record")
                    .get("games") or [])

    def player_accounts(self, player_id: str) -> list[dict]:
        """One per running extension that offers an account."""
        return list(self._get(f"/players/{quote(player_id, safe='')}/accounts")
                    .get("accounts") or [])

    def account_available(self, extension: str, check: str, candidate: str) -> bool:
        """Whether a candidate id is free to choose, asked at `extension`'s own declared
        `check` route."""
        return bool(self.ext_get(
            f"/ext/{quote(extension, safe='')}{check}"
            f"?candidate={quote(candidate)}").get("available"))

    def _account(self, player_id: str, extension: str) -> str:
        return f"/players/{quote(player_id, safe='')}/accounts/{quote(extension, safe='')}"

    def put_account(self, player_id: str, extension: str, values: dict) -> dict:
        return dict(self._put(self._account(player_id, extension), {"values": values}) or {})

    def put_share(self, player_id: str, extension: str, share: bool) -> dict:
        return dict(self._put(f"{self._account(player_id, extension)}/share",
                              {"share": bool(share)}) or {})

    def use_card(self, player_id: str, extension: str, card: str) -> dict:
        """`card` is a card file's text, or the card's own text. Answers the account."""
        return dict(self._post(f"{self._account(player_id, extension)}/card",
                               {"card": card}) or {})

    def use_account_picture(self, player_id: str, extension: str) -> dict:
        """Copy the picture `extension` has for this player. Answers the player."""
        return dict(self._post(f"{self._account(player_id, extension)}/picture", {}) or {})

    def account_act(self, player_id: str, extension: str, act: str) -> dict:
        return dict(self._post(f"{self._account(player_id, extension)}/acts/"
                               f"{quote(act, safe='')}", {}) or {})

    def player_card(self, player_id: str, extension: str) -> bytes:
        """The card as its SVG file."""
        path = f"{self._account(player_id, extension)}/card"
        _refuse_the_event_loop(path)
        response = self._session.get(f"{self._base}{path}", timeout=_TIMEOUT)
        self._answered(response)
        return response.content

    def player_rating(self, player_id: str, game_id: str, rating: int) -> None:
        """A rating for a player other than the owner. `rate()` above is the owner's."""
        self._put(f"/players/{quote(player_id, safe='')}/ratings/{quote(game_id, safe='')}",
                  {"rating": rating})

    # --- imports ------------------------------------------------------------
    # The files themselves are uploaded by the browser straight to the API, so nothing
    # streams through here. What the Manager does is the three steps after that: read
    # what arrived, work out where it would go, and say go.

    def upload_analysis(self, upload_id: str) -> dict:
        """What was dropped, as the install reads it."""
        return dict(self._get(f"/uploads/{upload_id}/analysis") or {})

    def upload_from_path(self, path: str) -> str:
        """A session reading a file, folder or archive already on this machine, where
        it is, anywhere on the disk. Nothing is copied until the import, and the original is
        never moved."""
        return str(self._post("/folders/uploads", {"path": path})["id"])

    def upload_image(self, upload_id: str) -> tuple[str, bytes]:
        """The one picture the session holds, with the name the server gave it. Raises with
        the API's reason where the session holds anything else."""
        route = f"/uploads/{upload_id}/image"
        _refuse_the_event_loop(route)
        response = self._session.get(f"{self._base}{route}", timeout=_TIMEOUT)
        self._answered(response)
        named = re.search(r'filename="([^"]+)"', response.headers.get("Content-Disposition", ""))
        return (named.group(1) if named else "picture"), response.content

    def add_game_from_vps(self, vps_id: str, location_id: str = "") -> dict:
        """A game with no tables, matched to that catalog entry. Raises when its folder is
        already there, which the caller reports on that row."""
        return self._post("/games/from_vps", {"vps_id": vps_id, "location": location_id})

    def create_game(self, name: str, location_id: str = "") -> dict:
        """A folder with a record and no tables, in the place new games go."""
        return self._post("/games", {"name": name, "location": location_id})

    def delete_game_folder(self, game_id: str) -> None:
        """Remove the game's whole folder from the disk. Refused unless this device, or
        an Owner who has confirmed, is asking."""
        self._delete(f"/games/{quote(game_id, safe='')}/folder")

    def new_game_destination(self) -> dict:
        """Where a new game would be created, or why it could not, and what else it
        could go to. Read fresh: a share that has dropped is exactly the case."""
        return dict(self._get("/locations/destination") or {})

    def upload_plan(self, upload_id: str, *, game_dir: str = "", rom_name: str = "",
                    allow_new_game: bool = False, vps_id: str = "",
                    media_kind: str = "", location_id: str = "",
                    asset_kind: str = "", add_table: bool = False,
                    replace_table: str = "", carry: list[str] | None = None,
                    instead: bool = False, table_id: str = "", scope: str = "",
                    page: int = 1, assignments: list[dict] | None = None,
                    picks: dict[str, str] | None = None,
                    new_game_dir_name: str | None = None,
                    convert: dict | None = None,
                    info_modes: dict[str, str] | None = None) -> dict:
        """Where each of those files would go. Nothing is written by asking."""
        path = f"/uploads/{upload_id}/plan"
        _refuse_the_event_loop(path)
        body: dict[str, Any] = {
            "game_dir": game_dir, "rom_name": rom_name,
            "allow_new_game": allow_new_game, "vps_id": vps_id,
            "media_kind": media_kind, "location_id": location_id,
            "asset_kind": asset_kind, "add_table": add_table,
            "replace_table": replace_table, "instead": instead,
            "table_id": table_id, "scope": scope, "page": page,
        }
        if carry is not None:
            body["carry"] = carry
        if assignments:
            body["assignments"] = assignments
        if picks is not None:
            body["picks"] = picks
        if new_game_dir_name is not None:
            body["new_game_dir_name"] = new_game_dir_name
        if convert:
            body["convert"] = convert
        if info_modes:
            body["info_modes"] = info_modes
        response = self._session.post(f"{self._base}{path}", json=body, timeout=_TIMEOUT)
        self._answered(response)
        return response.json()

    @staticmethod
    def _import_body(*, game_dir: str, rom_name: str, allow_new_game: bool, vps_id: str,
                     media_kind: str, location_id: str, asset_kind: str, add_table: bool,
                     replace_table: str, carry: list[str] | None, instead: bool,
                     table_id: str, scope: str, page: int, assignments: list[dict] | None,
                     picks: dict[str, str] | None, new_game_dir_name: str | None,
                     selected: list[int] | None, declared: dict | None,
                     convert: dict | None,
                     info_modes: dict[str, str] | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {
            "game_dir": game_dir, "rom_name": rom_name,
            "allow_new_game": allow_new_game, "vps_id": vps_id,
            "media_kind": media_kind, "location_id": location_id,
            "asset_kind": asset_kind, "add_table": add_table,
            "replace_table": replace_table, "instead": instead,
            "table_id": table_id, "scope": scope, "page": page,
        }
        if carry is not None:
            body["carry"] = carry
        if assignments:
            body["assignments"] = assignments
        if picks is not None:
            body["picks"] = picks
        if new_game_dir_name is not None:
            body["new_game_dir_name"] = new_game_dir_name
        if selected is not None:
            body["selected"] = selected
        if declared:
            body["declared"] = declared
        if convert:
            body["convert"] = convert
        if info_modes:
            body["info_modes"] = info_modes
        return body

    def upload_import(self, upload_id: str, *, game_dir: str = "", rom_name: str = "",
                      allow_new_game: bool = False, vps_id: str = "",
                      media_kind: str = "", location_id: str = "",
                      asset_kind: str = "", add_table: bool = False,
                      replace_table: str = "", carry: list[str] | None = None,
                      instead: bool = False, table_id: str = "", scope: str = "",
                      page: int = 1, assignments: list[dict] | None = None,
                      picks: dict[str, str] | None = None,
                      new_game_dir_name: str | None = None,
                      selected: list[int] | None = None,
                      declared: dict | None = None,
                      convert: dict | None = None,
                      info_modes: dict[str, str] | None = None) -> dict:
        """Do it. `selected` left out means every item the plan offered."""
        path = f"/uploads/{upload_id}/import"
        _refuse_the_event_loop(path)
        body = self._import_body(
            game_dir=game_dir, rom_name=rom_name, allow_new_game=allow_new_game,
            vps_id=vps_id, media_kind=media_kind, location_id=location_id,
            asset_kind=asset_kind, add_table=add_table, replace_table=replace_table,
            carry=carry, instead=instead, table_id=table_id, scope=scope, page=page,
            assignments=assignments, picks=picks, new_game_dir_name=new_game_dir_name,
            selected=selected, declared=declared, convert=convert, info_modes=info_modes)
        response = self._session.post(f"{self._base}{path}", json=body,
                                      timeout=_IMPORT_TIMEOUT)
        self._answered(response)
        return response.json()

    def start_upload_import(self, upload_id: str, **asked: Any) -> dict:
        """`upload_import` as a job: answered as soon as it is accepted, with the job to
        watch through `job`. `asked` is `upload_import`'s own keywords."""
        path = f"/uploads/{upload_id}/import/start"
        _refuse_the_event_loop(path)
        names = ("game_dir", "rom_name", "allow_new_game", "vps_id", "media_kind",
                 "location_id", "asset_kind", "add_table", "replace_table", "carry",
                 "instead", "table_id", "scope", "page", "assignments", "picks",
                 "new_game_dir_name", "selected", "declared", "convert", "info_modes")
        defaults: dict[str, Any] = {
            "game_dir": "", "rom_name": "", "allow_new_game": False, "vps_id": "",
            "media_kind": "", "location_id": "", "asset_kind": "", "add_table": False,
            "replace_table": "", "carry": None, "instead": False, "table_id": "",
            "scope": "", "page": 1, "assignments": None, "picks": None,
            "new_game_dir_name": None, "selected": None, "declared": None, "convert": None,
            "info_modes": None}
        body = self._import_body(**{name: asked.get(name, defaults[name]) for name in names})
        response = self._session.post(f"{self._base}{path}", json=body, timeout=_TIMEOUT)
        self._answered(response)
        return dict(response.json() or {})

    def stop_upload_import(self) -> bool:
        """Stop the import that is running, before it writes anything. False where none is."""
        return bool(self._post("/uploads/import/stop", {}).get("ok"))

    def abort_upload(self, upload_id: str) -> None:
        """Throw the staged files away. Best effort - a session left behind is rubbish
        in a temp directory, not a fault worth reporting to somebody."""
        try:
            self._delete(f"/uploads/{upload_id}")
        except Exception:  # noqa: BLE001
            logger.debug("manager: could not abort upload %s", upload_id, exc_info=True)

    def locations(self) -> dict:
        """Every location, with what the disk says about each one right now.

        Read fresh every time: reachable and writable are answers about this moment, and
        a cache would show that a share was mounted before it dropped.
        """
        return dict(self._get("/locations", _LIBRARY_TIMEOUT) or {})

    def put_location(self, location_id: str, body: dict) -> dict:
        return dict(self._put(f"/config/locations/{location_id}", body) or {})

    def delete_location(self, location_id: str) -> None:
        self._delete(f"/config/locations/{location_id}")

    def set_location_order(self, order: list[str]) -> list[str]:
        """Which location outranks which. The list order is the priority.

        A location the caller does not name keeps its place after the ones it did, so a
        caller working from a stale list rearranges what it knew about and loses nothing
        it did not.
        """
        held = [one for one in self._get("/config/locations") or [] if isinstance(one, dict)]
        by_id = {str(one.get("id")): one for one in held}
        named = [by_id[one] for one in dict.fromkeys(str(x) for x in order) if one in by_id]
        rest = [one for one in held if one not in named]
        return [str(one.get("id")) for one in
                self._put("/config/locations", [*named, *rest]) or []]

    def shadowed_here(self, location_id: str) -> list[dict]:
        """Game folders in this location whose id a higher one already answers for."""
        return list((self._get(f"/locations/{location_id}/shadowed")
                     or {}).get("shadowed") or [])

    def adopt_shadowed(self, location_id: str, path: str) -> dict:
        """Give one shadowed folder an id of its own. The only write in any of this."""
        found = f"/locations/{location_id}/shadowed/adopt"
        _refuse_the_event_loop(found)
        response = self._session.post(f"{self._base}{found}", json={"path": path},
                                      timeout=_TIMEOUT)
        self._answered(response)
        return response.json()

    def set_location_write_to(self, location_id: str) -> dict:
        """Where a new game's folder is created."""
        return self.put_config({"updates": {"new_games_location": location_id}})

    def launcher_config(self, launcher_id: str, table: str = "",
                        scope: str = "launcher") -> dict:
        """The app's declared groups and their values at one scope."""
        return dict(self._get(
            f"/launchers/{launcher_id}/config?table={table}&scope={scope}") or {})

    def make_launcher_settings_file(self, launcher_id: str) -> dict:
        """Runs the launcher's program once so it writes its own settings file."""
        return dict(self._post(f"/launchers/{launcher_id}/config/file", {},
                               timeout=_MAKE_FILE_TIMEOUT) or {})

    def set_launcher_settings_file(self, launcher_id: str, body: dict) -> dict:
        """Give a launcher a settings file. `needs_replace` in the answer means nothing was
        done yet."""
        return dict(self._post(f"/launchers/{launcher_id}/settings-file", body,
                               timeout=_MAKE_FILE_TIMEOUT) or {})

    def make_launcher_own_copy(self, launcher_id: str) -> dict:
        return dict(self._post(f"/launchers/{launcher_id}/settings-file/own-copy", {}) or {})

    def launcher_was_renamed(self, launcher_id: str, was: str) -> dict:
        return dict(self._post(f"/launchers/{launcher_id}/settings-file/renamed",
                               {"was": was}) or {})

    def delete_launcher_own_file(self, launcher_id: str) -> dict:
        return dict(self._post(f"/launchers/{launcher_id}/settings-file/delete", {}) or {})

    def undo_launcher_settings_file(self, token: str) -> dict:
        return dict(self._post("/launchers/settings-file/undo", {"undo": token}) or {})

    def launcher_settings_file_places(self, app: str, settings: dict) -> list[dict]:
        return list(self._post("/launchers/settings-file/places",
                               {"app": app, "settings": settings}).get("places") or [])

    def launcher_leftovers(self, launcher_id: str) -> dict:
        """What an upgrade of the launcher's program left in its settings."""
        return dict(self._get(f"/launchers/{launcher_id}/leftovers") or {})

    def move_launcher_leftovers(self, launcher_id: str) -> dict:
        return dict(self._post(f"/launchers/{launcher_id}/leftovers/move", {}) or {})

    def turn_on_launcher_leftover(self, launcher_id: str, key: str) -> dict:
        return dict(self._post(f"/launchers/{launcher_id}/leftovers/turn-on",
                               {"key": key}) or {})

    def undo_launcher_leftovers(self, launcher_id: str, token: str) -> dict:
        return dict(self._post(f"/launchers/{launcher_id}/leftovers/undo",
                               {"undo": token}) or {})

    def backups(self, subject_kind: str = "", subject_id: str = "") -> dict:
        """Every copy VPinFE has kept, or those of one game, launcher or theme, and which of
        them wait on a close."""
        query = (f"?subject_kind={quote(subject_kind)}&subject_id={quote(subject_id, safe='')}"
                 if subject_kind else "")
        said = self._get(f"/backups{query}") or {}
        return {"copies": list(said.get("copies") or []),
                "pending": list(said.get("pending") or []),
                "folder": dict(said.get("folder") or {})}

    def take_backup(self, scope: str, *, games: list[str] | None = None, launcher: str = "",
                    note: str = "", name: str = "") -> dict:
        return dict(self._post("/backups", {"scope": scope, "games": games or [],
                                            "launcher": launcher, "note": note,
                                            "name": name}) or {})

    def set_backup_note(self, copy_id: str, note: str) -> dict:
        return dict(self._patch(f"/backups/{copy_id}", {"note": note}) or {})

    def restore_backup(self, copy_id: str) -> dict:
        return dict(self._post(f"/backups/{copy_id}/restore", {}) or {})

    def delete_backup(self, copy_id: str) -> None:
        self._delete(f"/backups/{copy_id}")

    def going_back_plan(self) -> dict:
        return dict(self._get("/backups/going-back") or {})

    def go_back_to_2x(self) -> dict:
        """VPinFE quits after this answers."""
        return dict(self._post("/backups/going-back", {}) or {})

    def write_launcher_config(self, launcher_id: str, values: dict, *,
                              table: str = "", scope: str = "launcher",
                              create: bool = False) -> dict:
        """`create` makes the program's settings file where it has none yet."""
        return dict(self._put(f"/launchers/{launcher_id}/config",
                              {"values": values, "table": table, "scope": scope,
                               **({"create": True} if create else {})}) or {})

    def write_launcher_config_tables(self, launcher_id: str, values: dict,
                                     tables: list[str], *, scope: str = "entry") -> dict:
        """The same values into each of `tables`. Several come back as the job to watch."""
        return dict(self._put(f"/launchers/{launcher_id}/config",
                              {"values": values, "tables": tables, "scope": scope}) or {})

    def config_schema(self) -> list[dict]:
        """Every setting this install has, sectioned. Read from the install rather than
        carried here, so a client cannot offer a setting the install does not have."""
        return list(self._get("/config/schema").get("sections") or [])

    def config_values(self) -> dict:
        """What it is set to, typed - a bool arrives as a bool."""
        return dict(self._get("/config/settings") or {})

    def config_waiting(self) -> dict:
        """What is saved and not yet in effect: `waiting`, whether a `reopen` can be done
        here, and the `http_port` the next start listens on."""
        return dict(self._get("/config/waiting") or {})

    def library_policy(self) -> dict:
        """What this install collects: the `library` section of the settings."""
        return dict(self.config_values().get("library") or {})

    def put_library_policy(self, changes: dict) -> dict:
        """A patch. Only the keys sent are written."""
        return dict(self.put_config({"library": changes}).get("library") or {})

    def manager_policy(self) -> dict:
        """What this install's Manager shows: the `manager` section of the settings."""
        return dict(self.config_values().get("manager") or {})

    def put_manager_policy(self, changes: dict) -> dict:
        """A patch. Only the keys sent are written."""
        return dict(self.put_config({"manager": changes}).get("manager") or {})

    def dismissed(self) -> dict[str, list[str]]:
        """Each Overview row set aside on this install, with the ids it counted."""
        found = self._get("/library/dismissed").get("rows") or []
        return {str(one["row"]): [str(item) for item in one.get("ids") or []] for one in found}

    def put_dismissed(self, row: str, ids: list[str]) -> None:
        """Replace what a row was dismissed over; none brings it back."""
        self._put(f"/library/dismissed/{quote(row, safe='')}", {"ids": list(ids)})

    def config_path_checks(self) -> list[dict]:
        """Whether each path setting finds anything on the machine holding it. Stats the
        disk, so it is answered by that install and not worked out from the value."""
        return list(self._get("/config/paths").get("checks") or [])

    def config_tools(self) -> list[dict]:
        """Each Tool as the install finds it on its own disk and PATH."""
        return list(self._get("/config/tools").get("tools") or [])

    def get_tool(self, tool_id: str) -> dict:
        """Starts downloading a Tool the install can get itself; its job."""
        return self._post(f"/config/tools/{tool_id}/get", {})

    def input_play(self) -> dict:
        """What the install can do with keys while a table runs: press and hear them."""
        return dict(self._get("/input/play") or {})

    def put_config(self, changes: dict) -> dict:
        """A patch, section then key. Refused whole if any key is unknown."""
        return dict(self._put("/config", {"settings": changes}).get("settings") or {})

    def set_game_overrides(self, game_id: str, changes: dict) -> dict:
        """What the user says about the machine. Only the keys sent are written, so
        two surfaces editing different fields do not overwrite each other."""
        return self._put(f"/games/{game_id}/overrides", changes)

    def declare_no_match(self, game_id: str) -> None:
        """Say this game is in no catalog."""
        self._delete(f"/games/{game_id}/vps_match")

    def auto_match(self, game_ids: list[str]) -> dict:
        """Match these games again from their folder names. A person's matches stay."""
        return self._post("/library/auto_match", {"game_ids": game_ids})

    def missing_media(self, game_ids: list[str] | None = None) -> dict:
        """Per kept kind, how many of these games have no file and how many can get one."""
        return self._post("/library/media/missing", {"game_ids": game_ids})

    def fill_media(self, game_ids: list[str] | None = None, kinds: list[str] | None = None,
                   slots: tuple[tuple[str, str], ...] = ()) -> dict:
        """Get missing media. Returns the job to watch."""
        return self._post("/library/media/fill", {
            "game_ids": game_ids, "kinds": kinds,
            "slots": [{"game_id": game_id, "kind": kind} for game_id, kind in slots]})

    def rotate_media(self, files: list[tuple[str, str]], turn: str, keep: bool) -> dict:
        """Turn these files in place. Returns the job to watch."""
        return self._post("/library/media/rotate", {
            "files": [{"game_id": game_id, "path": path} for game_id, path in files],
            "turn": turn, "keep": keep})

    def convertible_media(self, files: list[tuple[str, str]], choices: dict[str, Any],
                          devices: list[dict[str, Any]]) -> dict:
        """What converting these files would do, and the choices there are. Nothing changes."""
        return self._post("/library/media/convertible", _convert_body(files, choices, devices))

    def convert_media(self, files: list[tuple[str, str]], choices: dict[str, Any],
                      devices: list[dict[str, Any]]) -> dict:
        """Convert these files. Returns the job to watch."""
        return self._post("/library/media/convert", _convert_body(files, choices, devices))

    def backups_folder_change(self, folder: str) -> dict:
        """What making `folder` the backups folder would do with the backups already taken."""
        return self._get("/backups/folder/change?" + urlencode({"to": folder}))

    def move_backups_folder(self, folder: str) -> dict:
        """Make `folder` the backups folder and move the backups across. Returns the job."""
        return self._post("/backups/folder/move", {"folder": folder})

    def set_table_overrides(self, game_id: str, table_id: str, changes: dict) -> dict:
        """What the user says about one file. Same patch shape as the game's."""
        return self._put(f"/games/{game_id}/tables/{table_id}/overrides", changes)

    def launch_apps(self) -> list[dict]:
        """The programs that play something in this library, read rather than carried:
        a client with its own copy of the list is a second place for it to be wrong."""
        return list(self._get("/tables/apps").get("apps") or [])

    def add_keyed_table(self, game_id: str, app: str, key: str) -> dict:
        """Record something this game holds that has no file, by the name its program
        knows it by. Nothing scans one of these into existence."""
        path = f"/games/{game_id}/tables"
        _refuse_the_event_loop(path)
        response = self._session.post(f"{self._base}{path}",
                                      json={"app": app, "key": key}, timeout=_TIMEOUT)
        self._answered(response)
        return response.json()

    def add_referenced_table(self, game_id: str, file_path: str) -> dict:
        """Point this game at a file that is not in its folder."""
        path = f"/games/{game_id}/tables"
        _refuse_the_event_loop(path)
        response = self._session.post(f"{self._base}{path}",
                                      json={"path": file_path}, timeout=_TIMEOUT)
        self._answered(response)
        return response.json()

    def import_table_file(self, game_id: str, file_path: str) -> dict:
        """Copy a file anywhere on this machine into the game folder, as one more table."""
        path = f"/folders/games/{game_id}/tables"
        _refuse_the_event_loop(path)
        response = self._session.post(f"{self._base}{path}",
                                      json={"path": file_path}, timeout=_IMPORT_TIMEOUT)
        self._answered(response)
        return response.json()

    def contain_table(self, game_id: str, table_id: str) -> dict:
        """Copy a referenced file into the game folder and stop pointing at it."""
        path = f"/games/{game_id}/tables/{table_id}/contain"
        _refuse_the_event_loop(path)
        response = self._session.post(f"{self._base}{path}", timeout=_TIMEOUT)
        self._answered(response)
        return response.json()

    def forget_table(self, game_id: str, table_id: str) -> dict:
        """Drop the record of a table whose file is gone. The API refuses if it is not."""
        path = f"/games/{game_id}/tables/{table_id}"
        _refuse_the_event_loop(path)
        response = self._session.delete(f"{self._base}{path}", timeout=_TIMEOUT)
        self._answered(response)
        return response.json()

    def table_swap_plan(self, game_id: str, table_id: str) -> dict:
        """The table named by version and author, and the files named for it."""
        return self._get(f"/games/{game_id}/tables/{table_id}/swap")

    def delete_table_file(self, game_id: str, table_id: str) -> dict:
        """Delete a table and the files named for it, after copying each to Backups."""
        path = f"/games/{game_id}/tables/{table_id}/file"
        _refuse_the_event_loop(path)
        response = self._session.delete(f"{self._base}{path}", timeout=_TIMEOUT)
        self._answered(response)
        return response.json()

    def swap_table_back(self, game_id: str, table_id: str, copies: list[str],
                        source: dict | None = None) -> dict:
        """Put the table back to the file those copies in Backups hold, with the release
        tie it had."""
        return self._post(f"/games/{game_id}/tables/{table_id}/swap_back",
                          {"copies": copies, "source": source})

    def actions(self) -> list[dict]:
        """What this install can be asked to do to itself, and performs."""
        return list(self._get("/actions").get("actions") or [])

    def perform_action(self, scope: str, action: str, reason: str = "") -> dict:
        """Do one of them. The confirm has already been put to the person who asked -
        this is the call that follows their yes."""
        return self._post("/actions", {"scope": scope, "action": action,
                                       "reason": reason or "asked from the Manager"})

    def logs(self, limit: int = 200, level: str = "", contains: str = "",
             source: str = "") -> dict:
        """Recent records from this install's own log, oldest first, with the list of
        files it could have come from."""
        query = urlencode({"limit": limit, "level": level, "contains": contains,
                           "source": source})
        return self._get(f"/logs?{query}")

    def log_file(self, source: str = "") -> bytes:
        """One log file, whole, as the install would hand it to a person."""
        path = f"/logs/file?{urlencode({'source': source})}"
        _refuse_the_event_loop(path)
        response = self._session.get(f"{self._base}{path}", timeout=_TIMEOUT)
        self._answered(response)
        return response.content

    def update_check(self, refresh: bool = False) -> dict:
        """Whether a newer build is published. `refresh` asks GitHub on the server's side
        rather than reading the last answer."""
        return self._get(f"/update?refresh={'true' if refresh else 'false'}")

    def forget_device(self, device_id: str, kind: str = "") -> None:
        """Drop a device. A VPX Mobile one is a list item in the configuration and an
        install is a registry row, so which it is decides the route. Both answer 204."""
        if kind == "vpx_mobile":
            self._delete(f"/config/mobile_devices/{quote(device_id)}")
            return
        _refuse_the_event_loop(f"/devices/{device_id}")
        response = self._session.delete(f"{self._base}/devices/{quote(device_id)}",
                                        timeout=_TIMEOUT)
        self._answered(response)

    def discovered_installs(self) -> list[dict]:
        """Every install announcing itself on this network, as it stands. Read when a
        page draws rather than cached: a machine is switched on while somebody is
        looking at the list."""
        return list(self._get("/devices/discovered").get("installs") or [])

    def add_device(self, address: str, port: int, name: str = "") -> dict:
        """List a VPX Mobile device by hand. It cannot announce itself, so the install
        mints its id."""
        return _as_mobile_device(self._post(
            "/config/mobile_devices", {"name": name, "address": address, "port": port}))

    def edit_device(self, device_id: str, address: str, port: int, name: str) -> dict:
        """Rewrite a mobile device's name and where it is, keeping the entry."""
        return _as_mobile_device(self._put(
            f"/config/mobile_devices/{quote(device_id)}",
            {"name": name, "address": address, "port": port}))

    def probe_device(self, device_id: str) -> dict:
        """Ask one device whether it is there."""
        return self._post(f"/devices/{quote(device_id)}/probe", {})

    def perform_update(self, *, stop_table: bool = False) -> dict:
        """Take the published build. The install goes down to do it, so this is the last
        call that install answers - a failure afterwards is the update working."""
        return self._post("/update", {"stop_table": stop_table})

    def info_maintenance(self) -> dict:
        """What the library's own metadata files need doing to them."""
        return self._get("/library/info", _LIBRARY_TIMEOUT)

    def upgrade_info(self) -> dict:
        """Upgrade every `.info` to the current format. Returns the job to watch."""
        return self._post("/library/info/upgrade", {})

    def device_games(self, device_id: str) -> list[dict]:
        """What a VPX Mobile device is carrying, asked of the device: each `folder` with
        the `game_id` of the library's game it is, None where it is none."""
        return list(self._get(f"/devices/{quote(device_id)}/games").get("games") or [])

    def send_to_device(self, device_id: str, game_ids: list[str], *,
                       everything: bool = False, settings: bool = False) -> dict:
        """Put games on a device. Returns the job to watch, not the finished work."""
        return self._post(f"/devices/{quote(device_id)}/games",
                          {"games": game_ids, "everything": everything,
                           "settings": settings})

    def plan_send_to_device(self, device_id: str, game_ids: list[str]) -> list[str]:
        """The ids, of these games, that would send a mobile settings file."""
        found = self._post(f"/devices/{quote(device_id)}/games/plan", {"games": game_ids})
        return [str(one) for one in found.get("mobile_settings") or []]

    def remove_from_device(self, device_id: str, name: str) -> None:
        """Take one game off a device. It answers 204, so nothing is read."""
        path = f"/devices/{quote(device_id)}/games/{quote(name, safe='')}"
        _refuse_the_event_loop(path)
        response = self._session.delete(f"{self._base}{path}", timeout=_TIMEOUT)
        self._answered(response)

    def stop_play(self) -> dict:
        """Close whatever is running. `stopped` is false where there was nothing to
        close, which is an answer rather than a failure."""
        return self._post("/play/stop", {})

    def press_input(self, action: str, phase: str = "tap", *,
                    ttl_ms: int = 0, source: str = "remote") -> dict:
        """Press, hold or release one input action on the install this client points at.

        Which install is the whole of the routing: a press reaches the windows of the
        machine that received it, so aiming at another one is a different base URL rather
        than a different call.
        """
        body: dict[str, Any] = {"action": action, "phase": phase, "source": source}
        if ttl_ms:
            body["ttl_ms"] = ttl_ms
        return self._post("/input/actions", body)

    def table_controls(self) -> dict:
        """What can be pressed on the table that is running, in the install's words. Its
        `controls` is empty where nothing plays or the app offers none."""
        return self._get("/play/controls")

    def press_control(self, control_id: str, *, source: str = "remote", phase: str = "tap",
                      ttl_ms: int | None = None) -> dict:
        """Press one of them: a `tap`, or on a holdable one a `press` renewed until a
        `release`. `pressed` is false where the key could not be sent."""
        body: dict = {"source": source, "phase": phase}
        if ttl_ms is not None:
            body["ttl_ms"] = ttl_ms
        return self._post(f"/play/controls/{quote(control_id, safe='')}", body)

    def play_state(self) -> dict:
        """What the play host is doing. `launching` stays true for as long as a table
        is up, not just while it is starting."""
        return self._get("/play/state")

    def frontend_state(self) -> dict | None:
        """What the frontend is showing: `running`, the `collection` ("" is the whole
        library) and the `game` on the wheel. None from an install too old to say, which
        is a different answer from a frontend that is closed."""
        _refuse_the_event_loop("/frontend/state")
        response = self._session.get(f"{self._base}/frontend/state", timeout=_TIMEOUT)
        if response.status_code == 404:
            return None
        self._answered(response)
        return response.json()

    def capture_report(self) -> dict:
        """What this device can record and why not, with VPinFE's own commands. Empty from
        an install too old to say."""
        _refuse_the_event_loop("/capture")
        response = self._session.get(f"{self._base}/capture", timeout=_TIMEOUT)
        if response.status_code == 404:
            return {}
        self._answered(response)
        return response.json()

    def test_capture(self, settings: dict | None = None) -> dict:
        """A few seconds of the playfield through the commands, and what came out."""
        _refuse_the_event_loop("/capture/test")
        response = self._session.post(f"{self._base}/capture/test",
                                      json={"settings": settings or {}},
                                      timeout=_TIMEOUT * 8)
        self._answered(response)
        return response.json()

    def choose_capture_screens(self) -> dict:
        """Asks the device's desktop which screens VPinFE may record; the job to watch."""
        return self._post("/capture/screens/choose", {})

    def plan_capture(self, body: dict) -> dict:
        """What recording games or tables would fill and replace, doing nothing."""
        return self._post("/capture/plan", body)

    def start_capture(self, body: dict) -> dict:
        """Record games or tables, one after another. Returns the job to watch."""
        return self._post("/capture/runs", body)

    def capture_run(self) -> dict:
        """The recording run in hand, running or paused; empty where there is none, or
        from an install too old to say."""
        _refuse_the_event_loop("/capture/runs/current")
        response = self._session.get(f"{self._base}/capture/runs/current", timeout=_TIMEOUT)
        if response.status_code == 404:
            return {}
        self._answered(response)
        return dict((response.json() or {}).get("run") or {})

    def resume_capture(self) -> dict:
        """Carry on with the paused run. Returns the job to watch."""
        return self._post("/capture/runs/current/resume", {})

    def discard_capture(self) -> dict:
        """Forget the run in hand."""
        return self._post("/capture/runs/current/discard", {})

    def pause_capture(self) -> dict:
        """Hold the run at the game in hand, which is recorded again on Resume. The run
        as it stands; empty where there is none."""
        return dict(self._post("/capture/runs/current/pause", {}).get("run") or {})

    def stop_capture(self) -> dict:
        """End the run, keeping what it has done. The run until it has ended; empty
        once it has."""
        return dict(self._post("/capture/runs/current/stop", {}).get("run") or {})

    def show_proposal(self, proposal_id: str, showing: str) -> None:
        """Play a recording waiting for a decision on the frontend, on the screen it
        belongs to: `showing` is "after" for the recording, "before" for the file it
        would take the place of."""
        self._put_empty("/frontend/preview", {"proposal": proposal_id, "showing": showing})

    def end_preview(self) -> None:
        self._delete("/frontend/preview")

    def stop_job(self, link: str) -> dict:
        """Stop a job at the `links.stop` its resource names."""
        _refuse_the_event_loop(link)
        response = self._session.post(f"{self._base.removesuffix('/api/v1')}{link}",
                                      timeout=_TIMEOUT)
        self._answered(response)
        return dict(response.json() or {})

    def capture_proposals(self) -> dict:
        """The recordings waiting for a decision."""
        return self._get("/capture/proposals")

    def use_proposal(self, proposal_id: str, use: bool) -> dict:
        """Place a recording waiting for a decision, or throw it away."""
        return self._post(f"/capture/proposals/{quote(proposal_id)}", {"use": use})

    def discard_proposals(self) -> dict:
        _refuse_the_event_loop("/capture/proposals")
        response = self._session.delete(f"{self._base}/capture/proposals",
                                        timeout=_TIMEOUT)
        self._answered(response)
        return response.json()

    def frontend_browser(self) -> dict:
        """What the frontend's browser can play, with the finding and the fix in words.
        Empty from an install too old to say, never None: `offload.io` reads None as a
        cancelled call."""
        _refuse_the_event_loop("/frontend/browser")
        response = self._session.get(f"{self._base}/frontend/browser", timeout=_TIMEOUT)
        if response.status_code == 404:
            return {}
        self._answered(response)
        return response.json()

    def show_on_frontend(self, collection: str) -> None:
        """Ask the frontend to show a collection, "" being the whole library. The change
        arrives as the next `frontend.state_changed`, not as this call's answer."""
        self._put_empty("/frontend/collection", {"name": collection})

    def move_wheel(self, game_id: str) -> None:
        """Ask the wheel to move to a game in the collection on screen."""
        self._put_empty("/frontend/game", {"id": game_id})

    def follow(self, names: Iterable[str],
               stop: threading.Event) -> Iterator[tuple[str, dict]]:
        """The install's events as they happen, until `stop` is set or the stream ends.

        Blocks between events for as long as it is followed, so it belongs on a thread
        of its own and never in the shared pool. `stop` is seen at the next line the
        install sends, which a keepalive makes at most 15 seconds away.
        """
        _refuse_the_event_loop("/events")
        response = self._session.get(f"{self._base}/events",
                                     params={"events": ",".join(names)}, stream=True,
                                     timeout=(_TIMEOUT, _FOLLOW_TIMEOUT))
        with response:
            self._answered(response)
            # No chunk size: with one, a frame waits until that many bytes have arrived,
            # and a frame is usually smaller.
            lines = _lines(response.iter_content(chunk_size=None))
            yield from read_frames(takewhile(lambda _line: not stop.is_set(), lines))

    def job(self, job_id: str) -> dict:
        """One run of slow work, for watching a particular one to its end."""
        return self._get(f"/jobs/{job_id}")

    def jobs(self, limit: int = 20) -> list[dict]:
        """Runs of slow work, running first. Read on a timer while one is going; the Jobs
        view asks for the whole history."""
        return self._get(f"/jobs?limit={limit}").get("jobs", [])

    def refresh_library(self) -> dict:
        """Ask the install to look at the disk again. Returns the job to watch."""
        _refuse_the_event_loop("/library/refresh")
        response = self._session.post(f"{self._base}/library/refresh", timeout=_TIMEOUT)
        self._answered(response)
        return response.json()

    def devices(self) -> list[dict]:
        """Every device this install knows, as it knows them.

        A plain read. The install you are sitting at records itself at startup and a phone is
        a registry entry with a minted id, so both are ordinary rows and nothing is folded
        in here.
        """
        return self._get("/devices").get("devices", [])


def _lines(chunks: Iterable[bytes]) -> Iterator[str]:
    """Text lines out of bytes that arrive split wherever the network split them."""
    held = b""
    for chunk in chunks:
        *whole, held = (held + chunk).split(b"\n")
        for line in whole:
            yield line.rstrip(b"\r").decode("utf-8", "replace")


def read_frames(lines: Iterable[str]) -> Iterator[tuple[str, dict]]:
    """An event stream's frames as (event name, payload).

    A comment, a frame without a name and a payload that is not a JSON object are passed
    over rather than raised: the stream is the install's, and one frame this end cannot
    read is no reason to stop hearing the next.
    """
    name = ""
    data: list[str] = []
    for line in lines:
        if not line:
            if name and data:
                try:
                    payload = json.loads("\n".join(data))
                except ValueError:
                    payload = None
                if isinstance(payload, dict):
                    yield name, payload
            name, data = "", []
            continue
        field, _, value = line.partition(":")
        if field == "event":
            name = value.removeprefix(" ")
        elif field == "data":
            data.append(value.removeprefix(" "))


def _page_params(page: int) -> dict[str, int]:
    """The query that names a later page; page 1 is the route as it always was."""
    return {"page": page} if page > 1 else {}


def _page_query(page: int) -> str:
    return f"?page={page}" if page > 1 else ""


def _convert_body(files: list[tuple[str, str]], choices: dict[str, Any],
                  devices: list[dict[str, Any]]) -> dict[str, Any]:
    """The request both conversion routes take. A choice left out is the plan's
    recommendation, and no devices is this install's own frontend."""
    return {"files": [{"game_id": game_id, "path": path} for game_id, path in files],
            **choices, "devices": devices or None}


def _refuse_the_event_loop(path: str) -> None:
    """Say so, loudly, when an API call is about to block the server from answering it.

    These calls go to our own process. Made from the event loop they deadlock until the
    read times out, and what the user sees is the browser losing its socket - which
    reads as a crash rather than as a blocking call, so the mistake reports itself
    instead. Every call belongs in run.io_bound.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return  # No loop here: a worker thread, which is where these belong.
    logger.error("manager: %s called on the event loop - wrap it in run.io_bound", path)
