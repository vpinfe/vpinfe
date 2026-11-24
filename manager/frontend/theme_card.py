"""A theme as the Themes grid's name cell draws it: the preview frame, the name, and
under it author and version.

Shared by the grid's rows and the Active Theme row on Settings > Themes, which is
the same cell drawn in Python.
"""

from __future__ import annotations

from typing import Any

from nicegui import ui

from common.i18n import t
from manager.kit import art, panel, thumbnails

# A theme's own glyph, where it has no preview.
GLYPH = "palette"
THEMES_PAGE = "/manager?view=themes"


def version_said(theme: dict[str, Any]) -> str:
    """The version, and the one on offer where it is newer."""
    if theme.get("update_available"):
        return t("manager.themes.version_to", now=theme.get("installed_version") or "",
                 then=theme.get("version") or "")
    return str(theme.get("installed_version") or theme.get("version") or "")


def preview(theme: dict[str, Any], size: int | None, layout: str = "") -> str:
    """The address of a theme's preview at `size` (as it is kept when None), or nothing for
    a theme without one. `layout` asks for the picture of that layout, where it has one."""
    if not theme.get("preview"):
        return ""
    own = (theme.get("previews") or {}).get(layout) or {}
    version = own.get("version") if own else theme.get("preview_version")
    return art.theme_preview(str(theme["key"]), version=str(version or ""), size=size,
                             layout=layout if own else "")


def pictures(theme: dict[str, Any]) -> list[str]:
    """The layouts that have a picture of their own, in the order a theme lists them."""
    return list(theme.get("previews") or {})


def said(theme: dict[str, Any]) -> str:
    """The line under the name: who made it, and which version."""
    return " · ".join(part for part in (str(theme.get("author") or ""),
                                              version_said(theme)) if part)


def active(key: str, theme: dict[str, Any] | None) -> None:
    """The theme the frontend runs, then where it is changed.

    `theme` is its entry in the themes listing. A theme the listing does not know - its
    package gone, or no source offers it - draws its key beside the glyph, with no line
    of author and version to say.
    """
    entry = theme or {}
    with thumbnails.beside(preview(entry, art.CELL), GLYPH, size=thumbnails.PREVIEW), \
            ui.column().classes("gap-0 grow min-w-0"):
        ui.label(str(entry.get("name") or key)) \
            .classes("manager-fact-value truncate min-w-0")
        if line := said(entry):
            ui.label(line).classes("manager-cell-said")
        panel.link(t("manager.settings.themes_link"), to=THEMES_PAGE)()
