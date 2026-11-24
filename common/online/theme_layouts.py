"""The layouts a theme is made for, and the picture it shows for each.

A 3.0 manifest says `"layouts": {"desktop": "desktop.png", "cab": "cabinet.png"}`: the
layouts it supports, each with its own picture. A 2.x manifest says `type` (desktop, cab
or both) and a single `preview_image`, which is still read.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

# The order a theme's layouts are listed in.
LAYOUTS = ("cab", "desktop")

_LEGACY_TYPES = {"desktop": ("desktop",), "cab": ("cab",), "both": LAYOUTS}


def declared(manifest: Mapping[str, Any]) -> dict[str, str]:
    """The `layouts` map as written, in the author's order. Empty when the manifest has none
    or it is not a map of layout to file name."""
    found = manifest.get("layouts")
    if not isinstance(found, Mapping):
        return {}
    return {str(layout): str(name).strip() for layout, name in found.items()
            if isinstance(name, str)}


def supported(manifest: Mapping[str, Any]) -> tuple[str, ...]:
    """The layouts a theme is made for, in `LAYOUTS` order."""
    found = declared(manifest)
    if found:
        return tuple(layout for layout in LAYOUTS if layout in found)
    return _LEGACY_TYPES.get(str(manifest.get("type") or ""), ())


def pictures(manifest: Mapping[str, Any]) -> dict[str, str]:
    """Each layout that has a picture of its own, as layout to file name. A 2.x theme made
    for both layouts has one picture between them, so none of its own."""
    found = {layout: name for layout, name in declared(manifest).items()
             if layout in LAYOUTS and name}
    if found:
        return {layout: found[layout] for layout in LAYOUTS if layout in found}
    only = _LEGACY_TYPES.get(str(manifest.get("type") or ""), ())
    name = str(manifest.get("preview_image") or "").strip()
    return {only[0]: name} if len(only) == 1 and name else {}


def lead(manifest: Mapping[str, Any]) -> str:
    """The picture that stands for the theme where there is room for one: the first
    layout's, else a 2.x theme's `preview_image`."""
    found = declared(manifest)
    if found:
        return next((name for layout, name in found.items() if layout in LAYOUTS and name), "")
    return str(manifest.get("preview_image") or "").strip()


def picture(manifest: Mapping[str, Any], layout: str = "") -> str:
    """The file name for `layout`, falling back to the lead when it has none of its own."""
    return pictures(manifest).get(layout) or lead(manifest)


def problem(manifest: Mapping[str, Any]) -> str:
    """Why `layouts` cannot be used, or empty when it is absent or fine."""
    if "layouts" not in manifest:
        return ""
    found = manifest["layouts"]
    if not isinstance(found, Mapping) or not found:
        return "layouts must name at least one layout"
    for layout, name in found.items():
        if layout not in LAYOUTS:
            return f"unknown layout '{layout}'"
        if not isinstance(name, str) or not name.strip():
            return f"layout '{layout}' has no picture"
    return ""
