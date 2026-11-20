"""An extension's whole panel as an entry of its own in the navigation.

The panel is the one the Extensions page draws for the extension, at full width: Details,
Settings, the extension's own sections, then Actions. Where the extension names no area
the Extensions page is its only home.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from manager.extensions import ext_lists


def is_panel(view: str) -> bool:
    return view.startswith(ext_lists.PANEL_PREFIX)


def panels(extensions: Sequence[dict[str, Any]]) -> list[tuple[dict, dict]]:
    """(extension, panel) for every running extension that put its panel in the
    navigation."""
    return [(one, declared) for one in extensions
            if str(one.get("state") or "") == "loaded"
            for declared in one.get("panels") or []]


def find(view: str, extensions: Sequence[dict[str, Any]]) -> tuple[dict, dict] | None:
    return next(((one, declared) for one, declared in panels(extensions)
                 if ext_lists.panel_view_key(str(one.get("name") or "")) == view), None)
