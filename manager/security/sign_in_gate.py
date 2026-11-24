"""Keeps another device out of every page until a browser on it has signed in.

This device is never asked. Another one is sent to `/signin`, a page served here by
hand: it must work with none of the Manager's scripts, because those are behind this gate.
The API passes through untouched and answers by scope.

A phone that scanned the Remote Code arrives at `/remote?pass=...` and is signed in at
Play. That sign-in reaches the Remote and what the Remote loads, and nothing else.
"""

from __future__ import annotations

import base64
import html
import json
import mimetypes
import posixpath
import secrets
from typing import Any
from urllib.parse import quote

from starlette.datastructures import Headers
from starlette.requests import HTTPConnection, Request
from starlette.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

from common.config import paths
from common.devices.install_identity import display_name
from common.i18n import t
from common.players import players
from common.security import accepted, access_keys, authenticator, sign_in
from httpapi import API_PREFIX, auth
from manager.kit import appearance

PAGE = "/signin"
REMOTE_PAGE = "/remote"
REMOTE_MANIFEST = "/remote.webmanifest"
PICTURE_SIZE = 96
# Everything the Remote loads besides its own page: the UI framework's scripts and socket,
# the shared stylesheet, the icon and the manifest a Home Screen app is installed from.
# A Play sign-in is held to these.
REMOTE_REACHES = ("/_nicegui/", "/_nicegui_ws/", appearance.BASE_MOUNT + "/")
REMOTE_FILES = frozenset({REMOTE_PAGE, REMOTE_MANIFEST, "/favicon.ico"})


def next_page(raw: str) -> str:
    """Where to go after signing in. Only a path on this server: anything that names another
    site would make the sign-in page a way to send someone there."""
    if raw.startswith("/") and not raw.startswith("//") and "\\" not in raw \
            and raw.isprintable():
        return raw
    return "/"


def theme_served(path: str) -> bool:
    """Whether a path under /themes/ is a theme that may run: a folder nobody accepted is
    files, and a page served from it would speak with this address's trust."""
    parts = posixpath.normpath(path).split("/")
    if len(parts) < 3 or parts[1] != "themes":
        return True
    return accepted.theme_runs(parts[2])


def admitted(connection: HTTPConnection) -> bool:
    return auth.caller_is_local(connection) or auth.session_for(connection) is not None


def remote_reaches(path: str) -> bool:
    """Whether a Remote pass may fetch this path."""
    return path in REMOTE_FILES or path.startswith(REMOTE_REACHES)


def confined(connection: HTTPConnection, path: str) -> bool:
    """Whether a Remote pass is asking for something past the Remote."""
    if auth.caller_is_local(connection):
        return False
    session = auth.session_for(connection)
    return session is not None and session.kind == sign_in.REMOTE and not remote_reaches(path)


def _redeem(scope: Scope) -> Response | None:
    """A phone arriving with the Remote pass in its address is signed in at Play and sent to
    the same address again, now holding the cookie. None when this is not that arrival: no
    pass, a wrong one, or a browser that already holds a sign-in the pass would not improve."""
    request = Request(scope)
    secret = request.query_params.get(access_keys.PASS_PARAM, "")
    pass_id = access_keys.pass_id_for(secret) if secret else ""
    if not pass_id:
        return None
    held = auth.session_for(request)
    if held is not None and (held.kind == sign_in.OWNER or held.via == pass_id):
        return None
    token, _ = sign_in.instance().open(
        kind=sign_in.REMOTE, address=auth.caller_address(request), via=pass_id,
        agent=sign_in.describe_agent(request.headers.get("user-agent", "")))
    target = scope["path"] + "?" + scope["query_string"].decode("latin-1")
    response = RedirectResponse(target, status_code=303)
    response.set_cookie(auth.COOKIE, token, max_age=sign_in.SESSION_SECONDS, httponly=True,
                        samesite="lax", secure=auth.request_is_https(request), path="/")
    return response


class SignInGate:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        path = scope["path"]
        if path == PAGE or path == API_PREFIX or path.startswith(API_PREFIX + "/"):
            await self.app(scope, receive, send)
            return
        connection = HTTPConnection(scope)
        if path == REMOTE_PAGE and scope["type"] == "http" and scope["method"] == "GET" \
                and not auth.caller_is_local(connection):
            arrived = _redeem(scope)
            if arrived is not None:
                await arrived(scope, receive, send)
                return
        if admitted(connection):
            if confined(connection, path):
                await _out_of_reach(scope)(scope, receive, send)
                return
            if scope["type"] == "http" and not theme_served(path):
                await PlainTextResponse("", status_code=404)(scope, receive, send)
                return
            await self.app(scope, receive, send)
            return
        if scope["type"] == "websocket":
            await receive()
            await send({"type": "websocket.close", "code": 1008})
            return
        if path == REMOTE_PAGE and _wants_page(scope):
            await _remote_needs_a_pass()(scope, receive, send)
            return
        await _refusal(scope)(scope, receive, send)


def _wants_page(scope: Scope) -> bool:
    headers = Headers(scope=scope)
    return scope["method"] in ("GET", "HEAD") and "text/html" in headers.get("accept", "")


class _Closed:
    """A websocket refused after it was offered."""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await receive()
        await send({"type": "websocket.close", "code": 1008})


def _out_of_reach(scope: Scope) -> Any:
    """What a Remote pass is told when it asks past the Remote: the code sign-in for a page,
    which is the only way past, and nothing for anything else."""
    if scope["type"] == "websocket":
        return _Closed()
    if _wants_page(scope):
        return _refusal(scope)
    return PlainTextResponse("", status_code=403)


def _refusal(scope: Scope) -> Response:
    if not _wants_page(scope):
        return PlainTextResponse(t("error.signin.required"), status_code=401)
    target = scope["path"] + (f"?{scope['query_string'].decode('latin-1')}"
                              if scope.get("query_string") else "")
    return RedirectResponse(f"{PAGE}?next={quote(target, safe='')}", status_code=303)


def _remote_needs_a_pass() -> Response:
    """The Remote opened with no pass and no sign-in: where to get one, and the code way in."""
    device = display_name(paths.get_ini_config())
    nonce = secrets.token_urlsafe(16)
    body = _REMOTE_PAGE.format(
        lang=html.escape(_language()), nonce=nonce, style=_STYLE,
        heading=html.escape(t("signin.remote.title", device=device)),
        line=html.escape(t("signin.remote.line")),
        use_code=html.escape(t("signin.remote.use_code")),
        href=html.escape(f"{PAGE}?next={quote(REMOTE_PAGE, safe='')}"))
    return HTMLResponse(body, headers=_page_headers(nonce))


def _page_headers(nonce: str) -> dict[str, str]:
    return {
        "Content-Security-Policy": (
            f"default-src 'none'; style-src 'nonce-{nonce}'; script-src 'nonce-{nonce}'; "
            "img-src data:; connect-src 'self'; form-action 'none'; base-uri 'none'; "
            "frame-ancestors 'none'"),
        "X-Frame-Options": "DENY", "Cache-Control": "no-store",
        "Referrer-Policy": "no-referrer"}


def _owner_card() -> tuple[str, str]:
    """The Owner's name and picture as a data address, either of which may be empty."""
    try:
        roster = players.get_roster()
        owner = roster.owner()
        if owner is None:
            return "", ""
        name = owner.name or owner.initials
        try:
            served = roster.picture(owner.player_id, PICTURE_SIZE)
            kind = mimetypes.guess_type(served.path.name)[0] or "image/png"
            data = base64.b64encode(served.path.read_bytes()).decode("ascii")
            return name, f"data:{kind};base64,{data}"
        except Exception:
            return name, ""
    except Exception:
        return "", ""


def sign_in_page(request: Request) -> Response:
    session = auth.session_for(request)
    remote_only = session is not None and session.kind == sign_in.REMOTE
    if auth.caller_is_local(request) or (session is not None and not remote_only):
        return RedirectResponse(next_page(request.query_params.get("next", "/")),
                                status_code=303)
    device = display_name(paths.get_ini_config())
    name, picture = _owner_card()
    nonce = secrets.token_urlsafe(16)
    words = {key: t(f"signin.page.{key}", device=device) for key in (
        "get_code", "look", "code", "submit", "cant_see", "cant_see_body", "failed",
        "use_app", "use_screen", "app_line")}
    held = authenticator.instance()
    has_app, screen = held.enabled(), held.screen_codes()
    heading = t("signin.page.title", device=device)
    owner = ""
    if picture:
        owner += f'<img class="owner" src="{html.escape(picture)}" alt="">'
    if name:
        owner += f'<p class="name">{html.escape(name)}</p>'
    remote = ""
    if remote_only:
        remote = (f'<p id="remote">{html.escape(t("signin.page.remote_only"))} '
                  f'<a href="{REMOTE_PAGE}">{html.escape(t("signin.page.remote_back"))}</a></p>')
    body = _PAGE.format(
        lang=html.escape(_language()), style=_STYLE, heading=html.escape(heading), owner=owner,
        remote=remote, line=html.escape(t("signin.page.line", device=device)),
        get_code=html.escape(words["get_code"]), code=html.escape(words["code"]),
        submit=html.escape(words["submit"]), cant_see=html.escape(words["cant_see"]),
        cant_see_body=html.escape(words["cant_see_body"]), nonce=nonce,
        app_line=html.escape(words["app_line"]),
        has_app="true" if has_app else "false", screen="true" if screen else "false",
        words=_script_value({key: words[key] for key in (
            "look", "failed", "use_app", "use_screen")}),
        next=_script_value(next_page(request.query_params.get("next", "/"))),
        api=_script_value(API_PREFIX))
    return HTMLResponse(body, headers=_page_headers(nonce))


def _script_value(value: object) -> str:
    """JSON that is safe inside a script element: no `</script>` can end it early."""
    return json.dumps(value).replace("<", "\\u003c")


def _language() -> str:
    from common import i18n
    return i18n.language() or "en"


_STYLE = """:root { color-scheme: light dark; --bg:#f4f5f7; --fg:#15171b; --dim:#5b6270;
  --line:#c9ced8; --accent:#0a7c91; --bad:#b3261e; }
@media (prefers-color-scheme: dark) { :root { --bg:#101216; --fg:#e8eaee; --dim:#9aa2b1;
  --line:#2c313b; --accent:#3cc4dd; --bad:#ff8a80; } }
body { margin:0; min-height:100vh; display:grid; place-items:center; background:var(--bg);
  color:var(--fg); font:16px/1.5 system-ui, sans-serif; }
main { width:min(24rem, 100% - 2rem); padding:2rem 0; }
h1 { margin:0 0 .25rem; font-size:1.4rem; color:var(--accent); }
.owner { width:4rem; height:4rem; border-radius:50%; object-fit:cover; margin:.5rem 0 0; }
.name { margin:.25rem 0 0; font-weight:600; }
p { margin:.75rem 0; color:var(--dim); }
button, input { font:inherit; padding:.7rem 1rem; border-radius:.5rem;
  border:1px solid var(--line); background:transparent; color:var(--fg); width:100%;
  box-sizing:border-box; }
button { background:var(--accent); border-color:var(--accent); color:#fff; cursor:pointer; }
button:disabled { opacity:.5; cursor:default; }
input { letter-spacing:.4em; text-align:center; font-size:1.4rem; margin-bottom:.75rem; }
.error { color:var(--bad); min-height:1.5em; }
details { margin-top:1.5rem; color:var(--dim); } summary { cursor:pointer; }
[hidden] { display:none !important; }
"""

_REMOTE_PAGE = """<!doctype html>
<html lang="{lang}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{heading}</title>
<style nonce="{nonce}">
{style}
a.button {{ display:block; text-align:center; text-decoration:none; padding:.7rem 1rem;
  border-radius:.5rem; background:var(--accent); color:#fff; }}
</style></head><body><main>
<h1>{heading}</h1>
<p>{line}</p>
<a class="button" href="{href}">{use_code}</a>
</main></body></html>
"""

_PAGE = """<!doctype html>
<html lang="{lang}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{heading}</title>
<style nonce="{nonce}">
{style}
</style></head><body><main>
<h1>{heading}</h1>
{owner}
{remote}
<p id="line">{line}</p>
<button id="ask" type="button">{get_code}</button>
<form id="enter" hidden>
<p id="look"></p>
<input id="code" inputmode="numeric" autocomplete="one-time-code" maxlength="6"
 pattern="[0-9]*" aria-label="{code}" placeholder="{code}">
<button id="send" type="submit">{submit}</button>
</form>
<form id="app" hidden>
<p>{app_line}</p>
<input id="appcode" inputmode="numeric" autocomplete="one-time-code" maxlength="7"
 pattern="[0-9 ]*" aria-label="{code}" placeholder="{code}">
<button type="submit">{submit}</button>
</form>
<p class="error" id="error" role="alert"></p>
<p><a href="#" id="switch" hidden></a></p>
<details id="cant"><summary>{cant_see}</summary><p>{cant_see_body}</p></details>
</main>
<script nonce="{nonce}">
const words = {words}, after = {next}, api = {api};
const hasApp = {has_app}, screen = {screen};
const $ = id => document.getElementById(id);
let request = "";
async function call(path, body) {{
  const answer = await fetch(api + path, {{method: "POST", credentials: "same-origin",
    headers: {{"Content-Type": "application/json"}}, body: body ? JSON.stringify(body) : null}});
  const data = await answer.json().catch(() => ({{}}));
  if (!answer.ok) {{
    const error = new Error((data.error && data.error.message) || words.failed);
    error.reason = data.error && data.error.details && data.error.details.reason;
    throw error;
  }}
  return data;
}}
$("ask").addEventListener("click", async () => {{
  $("error").textContent = "";
  try {{
    request = (await call("/auth/code")).request_id;
    $("ask").hidden = true; $("enter").hidden = false; $("look").textContent = words.look;
    $("code").focus();
  }} catch (e) {{ $("error").textContent = e.message; }}
}});
function show(app) {{
  $("error").textContent = "";
  $("app").hidden = !app;
  $("line").hidden = app;
  $("cant").hidden = app || !screen;
  $("ask").hidden = app || !!request;
  $("enter").hidden = app || !request;
  if (hasApp && screen) {{
    $("switch").hidden = false;
    $("switch").textContent = app ? words.use_screen : words.use_app;
  }}
  (app ? $("appcode") : request ? $("code") : $("ask")).focus();
}}
$("switch").addEventListener("click", event => {{
  event.preventDefault();
  show($("app").hidden);
}});
$("app").addEventListener("submit", async event => {{
  event.preventDefault();
  $("error").textContent = "";
  try {{
    await call("/auth/authenticator/session", {{code: $("appcode").value}});
    location.replace(after);
  }} catch (e) {{
    $("error").textContent = e.message; $("appcode").value = ""; $("appcode").focus();
  }}
}});
if (hasApp && !screen) show(true);
else if (hasApp) {{ $("switch").hidden = false; $("switch").textContent = words.use_app; }}
$("enter").addEventListener("submit", async event => {{
  event.preventDefault();
  $("error").textContent = "";
  try {{
    await call("/auth/session", {{request_id: request, code: $("code").value}});
    location.replace(after);
  }} catch (e) {{
    $("error").textContent = e.message; $("code").value = ""; $("code").focus();
    if (e.reason === "no_code" || e.reason === "used_up") {{
      request = ""; $("enter").hidden = true; $("ask").hidden = false;
    }}
  }}
}});
</script></body></html>
"""
