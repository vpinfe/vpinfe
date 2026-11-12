"""What a frontend window loads, served by the HTTP app at the paths a theme builds.

A window and the Manager share one origin, so a theme page calls the API without
crossing one. Files come from the mounted directories and nowhere else; the game
routes answer by id or folder name and never take a path from the caller.
"""

from __future__ import annotations

import html
import logging
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

import requests
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, Response
from starlette.staticfiles import StaticFiles

from common.media import sized_media
from common.security.http_client import get_text
from frontend.theme_windows import window_title
from httpapi import auth, scopes

logger = logging.getLogger("vpinfe.frontend.theme_routes")

# The catalog namespaces a core page is sent.
CORE_WORDS = ("frontend", "word", "input")

PINBALL_PRIMER_PREFIX = "https://pinballprimer.github.io/"


class _Static(StaticFiles):
    """A mounted folder, validated on every request: a theme edited on disk shows up on
    the next load, and an unchanged file costs a 304."""

    def file_response(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


def register(app: FastAPI, mount_points: dict[str, str]) -> None:
    """Add every route a window loads to `app`. The specific routes come first, so a
    mount over the same prefix cannot shadow them."""
    themes_root = mount_points.get("/themes/", "")

    @app.get("/app/{window}", include_in_schema=False)
    def app_page(window: str) -> Response:
        return _bootstrap_page(window)

    @app.get("/core/i18n.json", include_in_schema=False)
    def core_words(request: Request) -> Response:
        return _core_words(request.query_params.get("theme", ""), themes_root)

    @app.get("/proxy/pinballprimer", include_in_schema=False)
    def pinball_primer(request: Request) -> Response:
        return _pinball_primer_page(request.query_params.get("url", ""))

    @app.get("/media/{table_id}/{kind}", include_in_schema=False)
    def game_media(request: Request, table_id: str, kind: str) -> Response:
        if not _may_read(request):
            return _forbidden()
        from common.games import game_repository, media_lookup
        try:
            path = media_lookup.media_path(game_repository.all_games(), table_id, kind,
                                           _requested_page(request))
        except Exception:
            logger.exception("Media lookup failed for %s/%s", table_id, kind)
            return PlainTextResponse("Media lookup failed", status_code=500)
        if not path:
            return PlainTextResponse("No such media", status_code=404)
        size = _requested_size(request)
        if size is None:
            return PlainTextResponse("Unknown size", status_code=400)
        return _file(request, sized_media.served(path, size).path if size else path)

    @app.get("/manufacturers/{slug}/logo", include_in_schema=False)
    def manufacturer_logo(request: Request, slug: str) -> Response:
        from common.media.manufacturer_logos import manufacturer_logo_file
        path = manufacturer_logo_file(slug)
        return _file(request, path) if path else PlainTextResponse("No such logo",
                                                                  status_code=404)

    @app.get("/tables/{folder}/{rest:path}", include_in_schema=False)
    def game_file(request: Request, folder: str, rest: str) -> Response:
        return _game_file(request, folder, rest)

    for prefix, root in mount_points.items():
        # Symlinks are followed: a table folder or a media file linked in from elsewhere
        # is how a library is often laid out.
        app.mount(prefix.rstrip("/"), _Static(directory=root, follow_symlink=True))


def _forbidden() -> Response:
    return PlainTextResponse("", status_code=403)


def _may_read(request: Request, *, full: bool = False) -> bool:
    """Whether the caller may read the library. This device's own windows always may; a
    browser on another device needs games:read, and the whole folder is games:export_full,
    the same split the archive download makes."""
    if auth.caller_is_local(request):
        return True
    identity = auth.current_policy().identify(request)
    return identity.can(scopes.GAMES_EXPORT_FULL if full else scopes.GAMES_READ)


def _file(request: Request, path: str | os.PathLike[str]) -> Response:
    """Send a file, validated by its ETag. Answers 304 when the caller already holds it."""
    stat = Path(path).stat()
    etag = f'"{int(stat.st_mtime)}-{stat.st_size}"'
    headers = {"ETag": etag, "Cache-Control": "no-cache"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return FileResponse(path, headers=headers)


def _requested_page(request: Request) -> int:
    """The `page` in the query: 1 when it is absent, and 0 - a page nothing has - when it
    is not a positive whole number."""
    asked = request.query_params.get("page")
    if not asked:
        return 1
    return int(asked) if asked.isdecimal() and len(asked) < 9 else 0


def _requested_size(request: Request) -> int | None:
    """The `size` in the query: 0 when it is absent, one of `sized_media.SIZES` when it
    names one, and None for anything else."""
    asked = request.query_params.get("size")
    if not asked:
        return 0
    return int(asked) if asked.isdecimal() and int(asked) in sized_media.SIZES else None


def _game_file(request: Request, folder: str, rest: str) -> Response:
    """/tables/<folder>/<file> - a file in a game's folder, found by the folder's name in
    whichever library folder holds it. It is the URL contract 1 themes build from a media
    path."""
    if not rest.strip("/"):
        return PlainTextResponse("File not found", status_code=404)
    if not _may_read(request):
        return _forbidden()
    from common.games import game_repository
    try:
        game = game_repository.by_folder_name(folder)
    except Exception:
        logger.exception("Game lookup failed for %s", folder)
        return PlainTextResponse("Game lookup failed", status_code=500)
    root = os.path.abspath(str(game.full_path_game or "")) if game else ""
    path = os.path.abspath(os.path.join(root, *rest.split("/"))) if root else ""
    if not path or os.path.commonpath([root, path]) != root or not os.path.isfile(path):
        return PlainTextResponse("File not found", status_code=404)
    if not auth.caller_is_local(request) and not _in_default_bundle(request, root, path):
        return _forbidden()
    return _file(request, path)


def _in_default_bundle(request: Request, root: str, path: str) -> bool:
    """Whether a caller on another device may read this file: anything in the game's
    default bundle with games:read, anything else only with the whole-folder permission."""
    if _may_read(request, full=True):
        return True
    if not _may_read(request):
        return False
    from common.games.export_bundle import bundle_paths
    return any(os.path.abspath(held) == path for held, _ in bundle_paths(Path(root)))


def _bootstrap_page(window: str) -> Response:
    label = window_title(window)
    if label is None:
        return PlainTextResponse("Unknown app window", status_code=404)
    return HTMLResponse(f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <title>VPinFE {label}</title>
    <script src="/core/common/vpinfe-core.js"></script>
    <script>
      const vpin = new VPinFECore();
      vpin.init();

      vpin.ready.then(async () => {{
        const params = window.location.search;
        const splashEnabledRaw = await vpin.call("get_splashscreen_enabled");
        const splashEnabledNormalized = String(splashEnabledRaw).trim().toLowerCase();
        const splashDisabled = ["false", "0", "no", "off"].includes(splashEnabledNormalized);

        let location = await vpin.call("get_theme_index_page");
        if (!splashDisabled) {{
          location = `/core/splash.html?window={window}`;
        }}

        if (params) {{
          const separator = location.includes("?") ? "&" : "?";
          const extraParams = params.substring(1);
          if (extraParams) {{
            location += separator + extraParams;
          }}
        }}

        window.location.replace(location);
      }});
    </script>
  </head>
  <body style="margin: 0; background: black;"></body>
</html>
""")


def _theme_locales(theme: str, themes_root: str) -> str | None:
    """`<themes>/<theme>/locales` when it is a folder inside the themes mount."""
    if not themes_root or not theme or theme.startswith(".") or "/" in theme or "\\" in theme:
        return None
    root = os.path.abspath(themes_root)
    folder = os.path.abspath(os.path.join(root, theme, "locales"))
    if os.path.commonpath([root, folder]) != root or not os.path.isdir(folder):
        return None
    return folder


def _core_words(theme: str, themes_root: str) -> Response:
    """Core's own chrome, for the pages core serves.

    Only `CORE_WORDS` and the two number separators: a theme page has no business with the
    Manager's words, and shipping them would put the whole catalog on the frontend to render
    six menu items.

    `?theme=<folder>` adds that theme's own words from its `locales/` folder, under
    `theme.`. Core's namespaces are never `theme.`, so a theme adds words and cannot change
    one of core's.
    """
    from common import i18n
    words = {f"{space}.{k}": v
             for space in CORE_WORDS for k, v in i18n.under(space).items()}
    words.update({key: i18n.t(key) for key in ("number.group", "number.decimal")})
    directory = _theme_locales(theme, themes_root)
    if directory is not None:
        words.update({k: v for k, v in i18n.theme_words(directory).items()
                      if k.startswith("theme.")})
    return JSONResponse(words)


def _inject_base_tag(html_text: object, base_href: str) -> str:
    if not isinstance(html_text, str):
        html_text = str(html_text or "")
    base_tag = f'<base href="{html.escape(base_href)}">'
    if re.search(r"<base\s", html_text, flags=re.IGNORECASE):
        return html_text
    head_match = re.search(r"<head[^>]*>", html_text, flags=re.IGNORECASE)
    if head_match:
        insert_at = head_match.end()
        return f"{html_text[:insert_at]}{base_tag}{html_text[insert_at:]}"
    return f"<head>{base_tag}</head>{html_text}"


def _is_allowed_pinball_primer_url(url: object) -> bool:
    if not isinstance(url, str):
        return False
    normalized = url.strip()
    if not normalized.startswith(PINBALL_PRIMER_PREFIX):
        return False
    parsed = urlsplit(normalized)
    return parsed.scheme == "https" and parsed.netloc == "pinballprimer.github.io"


def _pinball_primer_error_html(message: str, requested_url: str = "") -> str:
    safe_message = html.escape(message)
    safe_url = html.escape(requested_url)
    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <title>Pinball Primer Tutorial Error</title>
    <style>
      body {{
        margin: 0;
        min-height: 100vh;
        display: flex;
        align-items: center;
        justify-content: center;
        background: #111827;
        color: #e5eefc;
        font-family: sans-serif;
      }}
      .card {{
        max-width: 48rem;
        padding: 2rem;
        border-radius: 1rem;
        background: rgba(15, 23, 42, 0.92);
        border: 1px solid rgba(148, 163, 184, 0.28);
        box-shadow: 0 0 24px rgba(15, 23, 42, 0.35);
      }}
      h1 {{ margin: 0 0 1rem; font-size: 1.5rem; }}
      p {{ margin: 0.5rem 0; line-height: 1.5; }}
      code {{
        display: block;
        margin-top: 1rem;
        padding: 0.75rem 1rem;
        background: rgba(15, 23, 42, 0.9);
        border-radius: 0.75rem;
        overflow-wrap: anywhere;
        color: #93c5fd;
      }}
    </style>
  </head>
  <body>
    <div class="card">
      <h1>Pinball Primer Tutorial Unavailable</h1>
      <p>{safe_message}</p>
      <p>Press <strong>T</strong> or your Back button to close this overlay.</p>
      <code>{safe_url}</code>
    </div>
  </body>
</html>
"""


def _pinball_primer_page(requested_url: str) -> Response:
    """A Pinball Primer tutorial page, fetched here with a base tag so its own links and
    assets resolve against the site it came from."""
    if not _is_allowed_pinball_primer_url(requested_url):
        return HTMLResponse(_pinball_primer_error_html(
            "Only https://pinballprimer.github.io/ URLs are allowed.", requested_url),
            status_code=403)
    try:
        page = get_text(requested_url, headers={"User-Agent": "VPinFE PinballPrimer Proxy"})
    except requests.RequestException as exc:
        logger.warning("Pinball Primer proxy fetch failed for %s: %s", requested_url, exc)
        return HTMLResponse(_pinball_primer_error_html(
            "The Pinball Primer page could not be downloaded right now.", requested_url),
            status_code=502)
    return HTMLResponse(_inject_base_tag(page, requested_url))

