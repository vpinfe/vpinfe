"""Setup's Controls step: the buttons that move around VPinFE, on the table Settings > Input draws.

Each press is saved as it is made, so the step has nothing of its own to write. What belongs
to playing a table is left to Settings > Input: Take Picture is heard only during play, and a
game's own buttons are Visual Pinball's.
"""

from __future__ import annotations

import logging
from typing import Any

from nicegui import ui

from common.config import input_registry, setting_groups
from common.i18n import t
from manager.kit import offload, panel
from manager.kit.api import ApiClient
from manager.kit.data import Library
from manager.settings import input_page
from manager.setup.setup_screens import write

logger = logging.getLogger("vpinfe.manager.setup.setup_controls")


def _asked_here(schema: list[dict]) -> list[dict]:
    """The schema with the input actions that are not about moving around VPinFE left out."""
    return [{**block, "options": [one for one in block.get("options") or []
                                  if one.get("group") != setting_groups.DURING_PLAY]}
            if block.get("name") == input_registry.SECTION else block for block in schema]


async def ask() -> dict[str, Any]:
    title = t("manager.setup.controls.title")
    library = Library(ApiClient())
    try:
        schema = _asked_here(await offload.io(library.config_schema))
        values = await offload.io(library.config_values)
    except Exception:
        logger.warning("Could not read the buttons", exc_info=True)
        return {"title": title, "notes": [t("said.settings_unreadable")]}
    entries = input_page.rows(schema, values, write, pads_when_heard=True, keep_intro_open=True)
    if not entries:
        return {"title": title, "notes": [t("said.settings_unreadable")]}

    def draw(_values: dict[str, Any]) -> None:
        panel.facts(ui, entries)

    return {"title": title, "draw": draw}


async def answer(_values: dict[str, Any]) -> None:
    """Each press is saved as it is made."""
