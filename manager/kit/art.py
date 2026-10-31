"""The address of a picture the Manager draws: which file, at what size, which version."""

from __future__ import annotations

from urllib.parse import quote, urlencode

from common.media.media_specs import media_family

# The longest edge the API sizes to. CELL is anything drawn at thumbnail scale; PANEL is
# a slot or a tile someone is looking at.
CELL = 256
PANEL = 1024


def media(game_id: str, kind: str, table_id: str = "", *,
          version: str | None = None, size: int | None = None, page: int = 1,
          file: str = "") -> str:
    """A game's shared file for `kind`, or one table's. A size on a kind that is not a
    picture is dropped, because the API refuses it. Page 1 is the address without a
    page, so a kind with none reads the same as before. A later page carries no version:
    the token is the first page's, and a page replaced under it would keep its old
    picture."""
    table = f"/tables/{quote(table_id, safe='')}" if table_id else ""
    base = f"/api/v1/games/{quote(game_id, safe='')}{table}/media/{kind}"
    return _with(base + (f"?file={quote(file, safe='')}" if file else ""),
                 version if page <= 1 else None,
                 size if media_family(kind) == "image" else None,
                 page if page > 1 else None)


def picture(game_id: str, name: str, *, version: str | None = None,
            size: int | None = None) -> str:
    """One of a game's Pictures."""
    return _with(f"/api/v1/games/{quote(game_id, safe='')}/pictures/{quote(name, safe='')}",
                 version, size)


def backglass(game_id: str, path: str, *, version: str | None = None,
              size: int | None = None, grill: bool = True, look: str = "on",
              edition: str = "authentic") -> str:
    """The picture a game's backglass file draws, lit at start. `grill` is whether it is
    cut as the cabinet cuts it, and `edition` is the half of a two-version file."""
    query = urlencode({"path": path, "look": look, "version": edition,
                       "grill": str(grill).lower()})
    return _with(f"/api/v1/games/{quote(game_id, safe='')}/assets/backglass/picture?{query}",
                 version, size)


def scoreview(game_id: str, path: str, *, version: str | None = None,
               size: int | None = None) -> str:
    """The art a game's backglass file draws on the score view window."""
    query = urlencode({"path": path})
    return _with(f"/api/v1/games/{quote(game_id, safe='')}/assets/backglass/scoreview?{query}",
                 version, size)


def collection(name: str, *, version: str | None = None, size: int | None = None) -> str:
    return _with(f"/api/v1/collections/{quote(name, safe='')}/image", version, size)


def player(player_id: str, *, version: str | None = None, size: int | None = None) -> str:
    return _with(f"/api/v1/players/{quote(player_id, safe='')}/picture", version, size)


def theme_preview(key: str, *, version: str | None = None, size: int | None = None,
                  layout: str = "") -> str:
    """A theme's preview, or the one it has for `layout`."""
    return _with(f"/api/v1/themes/{quote(key, safe='')}/preview", version, size,
                 layout=layout)


def upload_picture(upload_id: str, name: str, *, size: int | None = None) -> str:
    """A loose image in an upload that has not been imported yet, by the name it was dropped
    under."""
    query = urlencode({"name": name})
    return _with(f"/api/v1/uploads/{quote(upload_id, safe='')}/picture?{query}", None, size)


def sized(url: str, size: int) -> str:
    """An art address the API handed over, at `size`."""
    return _with(url, None, size)


def _with(url: str, version: str | None, size: int | None,
          page: int | None = None, layout: str = "") -> str:
    query = {key: value for key, value in
             (("size", size), ("v", version), ("page", page), ("layout", layout)) if value}
    if not query:
        return url
    return f"{url}{'&' if '?' in url else '?'}{urlencode(query)}"
